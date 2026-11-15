import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from lead_research.forms import extract_forms
from lead_research.models import EvidenceStore, ResearchReport, Finding, RunState
from lead_research.report import drop_invalid_citations, render_markdown, validate_report, write_report
from lead_research.telem_tools import ConfigError, TelemTools, resolve_base_url

FIX = Path(__file__).parent / "fixtures"
EDU = (FIX / "education.html").read_text()


def test_bootstrap_six_steps_seven_fields():
    forms = extract_forms(EDU, "https://yourdreamdegree.com/education-29", EvidenceStore())
    declared = [f for f in forms if f.source == "declared_config"]
    assert len(declared) == 1
    fm = declared[0]
    assert fm.declared_steps == 6 and len(fm.fields) == 7
    assert fm.coverage == "declared" and fm.observed_steps == 0
    edu = fm.fields[0]
    assert edu.key == "education" and len(edu.options) == 8
    assert fm.fields[1].key == "Citizen" and fm.fields[1].options == ["Yes", "No"]
    assert all(f.required is None for f in fm.fields)


def test_no_form_tag_native_controls_and_private_values():
    html = '<div><label for="e">Email</label><input id="e" name="email" type="email" value="me@x.com" required>' \
           '<input type="hidden" name="token" value="T"><select name="state"><option value="">Pick</option>' \
           '<option>CA</option></select><div data-react-props="{broken"></div></div>'
    store = EvidenceStore()
    forms = extract_forms(html, "https://x.test/", store)
    assert len(forms) == 1 and forms[0].source == "dom"
    keys = [f.key for f in forms[0].fields]
    assert keys == ["email", "state"]
    assert forms[0].fields[0].required is True and forms[0].fields[1].required is None
    dump = json.dumps([f.model_dump() for f in forms]) + json.dumps([e.model_dump() for e in store.all()])
    assert "me@x.com" not in dump and "T\"" not in dump and "token" not in dump


def test_atomic_attempt_budget():
    async def go():
        st = RunState(limits={"search": 1})
        return await asyncio.gather(st.reserve("search"), st.reserve("search"))
    assert sorted(asyncio.run(go())) == [False, True]


def test_hackathon_base_url_only():
    assert resolve_base_url("https://hack.telem.ai/api") == "https://hack.telem.ai/api"
    for bad in ("", "https://hack.telem.ai/api/v1", "https://api.telem.ai"):
        with pytest.raises(ConfigError):
            resolve_base_url(bad)


class FakeTelem:
    def __init__(self):
        self.calls = []

    async def search(self, query, **kw):
        self.calls.append(("search", query, kw))
        r = NS(url="https://yourdreamdegree.com/education-29", title="YDD", summary="Degree matching",
               excerpt=None, provider="tavily")
        return NS(results=[r, r], by_provider=[NS(provider="tavily", status="ok", error=None)],
                  session_id="sess-1", interaction_id="int-1", status="ok")

    async def fetch(self, urls, **kw):
        self.calls.append(("fetch", urls, kw))
        ok = NS(url=urls[0], title="YDD", status="ok", content="Find the right degree", content_format="markdown",
                provider="firecrawl", error=None, raw={"raw_html": EDU}, provider_metadata=None)
        bad = NS(url="https://example.com/", title="", status="failed", content=None, content_format=None,
                 provider="tavily", error={"code": "blocked"}, raw={}, provider_metadata=None)
        return NS(results=[ok, bad][: len(urls)], session_id="sess-1", interaction_id="int-2", status="partial")


def tools():
    st = RunState()
    return st, TelemTools(FakeTelem(), st, allowed_test_origins=frozenset({"https://yourdreamdegree.com", "https://example.com"}))


def test_routing_session_and_dedupe():
    st, t = tools()
    r1 = asyncio.run(t.search_web("YourDreamDegree"))
    asyncio.run(t.search_web("YourDreamDegree form"))
    assert len(r1.documents) == 1
    kw1, kw2 = t.client.calls[0][2], t.client.calls[1][2]
    assert kw1["auto_routing"] == "accuracy" and kw1["num_results"] == 3 and kw1["session"] is None
    assert kw2["session"] == "sess-1"


def test_mixed_fetch_and_inspect_form():
    st, t = tools()
    res = asyncio.run(t.fetch_pages(["https://yourdreamdegree.com/education-29", "https://example.com/"]))
    assert len(res.documents) == 1 and len(res.errors) == 1 and "blocked" in res.errors[0]
    assert t.client.calls[0][2]["tier"] == "extended"
    forms = t.inspect_form(res.documents[0]["document_id"]).forms
    assert forms[0].declared_steps == 6
    asyncio.run(t.fetch_pages(["https://example.com/"], mode="text"))
    assert t.client.calls[1][2]["providers"] == ["tavily"]


def test_private_url_rejected():
    st = RunState()
    t = TelemTools(FakeTelem(), st)
    res = asyncio.run(t.fetch_pages(["http://127.0.0.1/admin", "file:///etc/passwd"]))
    assert len(res.errors) == 2 and not t.client.calls


def test_invalid_evidence_dropped_and_markdown_escaped(tmp_path):
    st = RunState()
    ev = st.evidence.add("https://x.test", "telem_fetch", "page_text", "ok")
    rep = ResearchReport(target="X", business_findings=[
        Finding(statement="[click](http://evil)", evidence_ids=[ev.id]),
        Finding(statement="made up", evidence_ids=["E99"])])
    with pytest.raises(ValueError):
        validate_report(rep, st)
    rep = drop_invalid_citations(rep, st)
    assert len(rep.business_findings) == 1 and any("made up" in u for u in rep.unknowns)
    md = render_markdown(rep, st)
    assert "](http://evil)" not in md
    write_report(rep, st, tmp_path / "out")
    assert {p.name for p in (tmp_path / "out").iterdir()} == {"report.md", "report.json", "trace.jsonl"}


def test_agent_end_to_end_with_function_model(tmp_path):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from lead_research.agent import ResearchDeps, run_research
    from lead_research.browser_tools import BrowserTools

    st, t = tools()
    step = {"n": 0}

    def model(messages, info):
        step["n"] += 1
        if step["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("fetch_pages", {"urls": ["https://yourdreamdegree.com/education-29"]})])
        if step["n"] == 2:
            return ModelResponse(parts=[ToolCallPart("inspect_form", {"document_id": "D1"})])
        decl = st.forms[0].evidence_id
        out = {"target": "x", "status": "completed", "company_summary": "Education lead gen.",
               "business_findings": [{"statement": "Degree matching service", "evidence_ids": ["E1"]}],
               "added_findings": [{"statement": "6-step quiz asks US residency", "evidence_ids": [decl]},
                                  {"statement": "Rejects non-residents", "kind": "inference", "evidence_ids": [decl]}],
               "unknowns": ["Steps 2-6 not interactively verified"]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    deps = ResearchDeps(st, t, BrowserTools(st, tmp_path))
    rep = asyncio.run(run_research("https://yourdreamdegree.com/education-29", FunctionModel(model), deps))
    assert rep.status == "completed"
    assert rep.added_findings[1].kind == "inference"
    assert [e["event"] for e in st.trace][:3] == ["run_start", "telem_fetch", "inspect_form"]


def test_cli_refuses_existing_dir_and_bad_base_url(tmp_path, monkeypatch, capsys):
    from lead_research.__main__ import main
    assert main(["x", "--out", str(tmp_path)]) == 1
    monkeypatch.setenv("TELEM_BASE_URL", "https://api.telem.ai")
    assert main(["x", "--out", str(tmp_path / "new")]) == 1
    assert "hack.telem.ai" in capsys.readouterr().err
