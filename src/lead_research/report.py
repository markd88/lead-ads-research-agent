from __future__ import annotations

import json
import re
from pathlib import Path

from .models import ResearchReport, RunState

_MD = re.compile(r"([\\`*_{}\[\]()#+!|<>])")


def esc(s: str) -> str:
    return _MD.sub(r"\\\1", s or "").replace("\n", " ")


def validate_report(report: ResearchReport, state: RunState) -> None:
    for group in (report.business_findings, report.lead_intake_findings, report.added_findings):
        for f in group:
            bad = [i for i in f.evidence_ids if not state.evidence.has(i)]
            if bad:
                raise ValueError(f"invalid evidence ids {bad} in finding: {f.statement[:80]}")


def drop_invalid_citations(report: ResearchReport, state: RunState) -> ResearchReport:
    """Remove findings citing unknown evidence; record them as unknowns instead of trusting them."""
    for name in ("business_findings", "lead_intake_findings", "added_findings"):
        kept = []
        for f in getattr(report, name):
            if f.evidence_ids and all(state.evidence.has(i) for i in f.evidence_ids):
                kept.append(f)
            else:
                report.unknowns.append(f"Unsupported claim removed (no valid evidence): {f.statement}")
        setattr(report, name, kept)
    return report


def partial_report(target: str, state: RunState, reason: str) -> ResearchReport:
    return ResearchReport(target=target, status="partial", stop_reason=reason,
                          unknowns=["Run stopped before synthesis; see forms and evidence collected so far."])


def _section(title: str, findings) -> list[str]:
    lines = [f"## {title}", ""]
    if not findings:
        return lines + ["_None._", ""]
    for f in findings:
        tag = "" if f.kind == "fact" else " _(inference)_"
        lines.append(f"- {esc(f.statement)}{tag} [{', '.join(f.evidence_ids)}]")
    return lines + [""]


def render_markdown(report: ResearchReport, state: RunState) -> str:
    L = [f"# Merchant research: {esc(report.target)}", "",
         f"**Status:** {report.status}" + (f" — {esc(report.stop_reason)}" if report.stop_reason else ""), ""]
    if report.company_summary:
        L += [esc(report.company_summary), ""]
    L += _section("Business findings", report.business_findings)
    L += _section("Lead intake findings", report.lead_intake_findings)
    L += _section("Added by form discovery (missed by body text)", report.added_findings)
    L += ["## Lead forms discovered", ""]
    if not state.forms:
        L += ["_No form structure extracted._", ""]
    for fm in state.forms:
        L += [f"### {esc(fm.url)} — {fm.source} ({fm.coverage}) [{fm.evidence_id}]", "",
              f"Declared steps: {fm.declared_steps} · Observed steps: {fm.observed_steps} · Fields: {len(fm.fields)}", "",
              "| Step | Key | Label | Type | Options | Required |", "| --- | --- | --- | --- | --- | --- |"]
        for f in fm.fields:
            opts = ", ".join(f.options) if f.options else ""
            req = "unknown" if f.required is None else str(f.required).lower()
            L.append(f"| {f.step or ''} | {esc(f.key)} | {esc(f.label)} | {esc(f.type)} | {esc(opts)} | {req} |")
        L += [""] + [f"> {esc(n)}" for n in fm.notes] + [""]
    L += ["## Unknowns", ""] + ([f"- {esc(u)}" for u in report.unknowns] or ["_None._"]) + [""]
    L += ["## Telem session", ""]
    ts = state.telem_session
    if ts:
        L.append(f"- Session `{esc(ts['session_id'])}` — one Telem session for every search and fetch in this run")
        if "interaction_count" in ts:
            L.append(f"- Server-side history: {ts['interaction_count']} interactions (see https://hack.telem.ai/)")
        if ts.get("error"):
            L.append(f"- History unavailable: {esc(ts['error'])}")
    else:
        L.append("_No Telem session was created._")
    if state.monitor:
        m = state.monitor
        L.append(f"- Monitor `{esc(m['monitor_id'])}` watching {esc(m['domain'])} every {esc(m['frequency'])} "
                 f"(status {esc(m['status'])}, expires {esc(str(m['expires_at']))})")
    L += ["", "## Evidence", ""]
    for ev in state.evidence.all():
        L.append(f"- **{ev.id}** `{ev.method}` {esc(ev.url)} — {esc(ev.locator)}: {esc(ev.excerpt[:200])}")
    return "\n".join(L) + "\n"


def write_report(report: ResearchReport, state: RunState, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = {"report": report.model_dump(), "forms": [f.model_dump() for f in state.forms],
            "evidence": [e.model_dump() for e in state.evidence.all()],
            "budget_used": state.attempts, "telem_session": state.telem_session,
            "monitor": state.monitor}
    (output_dir / "report.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    (output_dir / "report.md").write_text(render_markdown(report, state))
    (output_dir / "trace.jsonl").write_text(state.trace_jsonl())
