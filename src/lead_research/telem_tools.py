"""Telem search/fetch wrappers. Records real provider/session/interaction IDs; no silent retries."""
from __future__ import annotations

import os
from typing import Any, Literal

from . import HACKATHON_BASE_URL
from .forms import extract_forms
from .models import RunState, ToolResult
from .url_policy import UrlPolicyError, validate_public_url

MAX_DOC_CHARS = 8000


class ConfigError(RuntimeError):
    pass


def resolve_base_url(value: str | None = None) -> str:
    url = (value if value is not None else os.environ.get("TELEM_BASE_URL", HACKATHON_BASE_URL)).rstrip("/")
    if url != HACKATHON_BASE_URL:
        raise ConfigError(f"TELEM_BASE_URL must be {HACKATHON_BASE_URL} (hackathon deployment), got {url!r}")
    return url


def make_client(base_url: str | None = None):
    from telem import AsyncTelem
    return AsyncTelem(base_url=resolve_base_url(base_url), timeout=20.0)


def _html_of(r: Any) -> str | None:
    raw = getattr(r, "raw", None) or {}
    for key in ("raw_html", "html"):
        if isinstance(raw.get(key), str) and raw[key]:
            return raw[key]
    pm = getattr(r, "provider_metadata", None) or {}
    for key in ("raw_html", "html"):
        if isinstance(pm.get(key), str) and pm[key]:
            return pm[key]
    content = getattr(r, "content", None) or ""
    if (getattr(r, "content_format", "") or "").lower() == "html" or content.lstrip().startswith("<"):
        return content or None
    return None


def merchant_domain(target: str, state: RunState) -> str | None:
    """Domain to watch: the target URL's host, else the first fetched document's host."""
    from urllib.parse import urlsplit
    candidates = [target] if "." in target and " " not in target.strip() else []
    candidates += [d.url for d in state.documents.values()]
    for c in candidates:
        host = urlsplit(c if "://" in c else "https://" + c).hostname
        if host:
            return host.removeprefix("www.")
    return None


MONITOR_QUERY = ("Changes to lead capture forms, application questions, eligibility requirements "
                 "or quote/apply flows on {domain} ({target})")


class TelemTools:
    def __init__(self, client: Any, state: RunState, allowed_test_origins: frozenset[str] = frozenset(),
                 target: str = ""):
        self.client = client
        self.state = state
        self.allowed = allowed_test_origins
        self.target = target

    # -- shared request context -------------------------------------------------
    @property
    def goal(self) -> str:
        return (f"Research lead-gen advertiser '{self.target}': business facts, lead capture entry points "
                "and the questions its lead forms ask, with sources.")

    def _metadata(self, tool: str) -> dict[str, Any]:
        return {"app": "lead_lens", "tool": tool, "target": self.target}

    def _adopt_session(self, session_id: str | None) -> None:
        """One research run = one Telem session, whichever call creates it first."""
        if self.state.search_session is None and session_id:
            self.state.search_session = session_id

    async def search_web(self, query: str, domain: str | None = None) -> ToolResult:
        if not await self.state.reserve("search"):
            return ToolResult(errors=["search budget exhausted"])
        kwargs: dict[str, Any] = {}
        if domain:
            kwargs["include_domains"] = [domain.lower().removeprefix("www.")]
        try:
            resp = await self.client.search(
                query, tier="default", num_results=3, auto_routing="accuracy",
                session=self.state.search_session, goal=self.goal,
                context="site-scoped lead entry discovery" if domain else "merchant discovery",
                metadata=self._metadata("search_site" if domain else "search_web"), **kwargs)
        except Exception as e:  # noqa: BLE001 - surface every SDK failure
            self.state.log("telem_search_error", query=query, error=f"{type(e).__name__}: {e}")
            return ToolResult(errors=[f"telem search failed: {type(e).__name__}: {e}"])
        self._adopt_session(resp.session_id)
        out = ToolResult()
        seen: set[str] = set()
        for r in resp.results:
            if not r.url or r.url in seen or len(seen) >= 8:
                continue
            seen.add(r.url)
            snippet = r.summary or " ".join(r.excerpt or []) or ""
            ev = self.state.evidence.add(r.url, "telem_search", f"search:{query}", f"{r.title} — {snippet}")
            out.documents.append({"url": r.url, "title": r.title, "snippet": snippet[:400],
                                  "provider": r.provider, "evidence_id": ev.id})
        for run in resp.by_provider:
            if run.status != "ok" and run.status != "success":
                out.warnings.append(f"provider {run.provider}: {run.status} {run.error or ''}".strip())
        self.state.log("telem_search", query=query, include_domains=kwargs.get("include_domains"),
                       session_id=resp.session_id,
                       interaction_id=resp.interaction_id, status=resp.status,
                       providers=[(p.provider, p.status) for p in resp.by_provider], results=len(out.documents))
        return out

    async def fetch_pages(self, urls: list[str], mode: Literal["text", "rich"] = "rich") -> ToolResult:
        out = ToolResult()
        clean: list[str] = []
        for u in urls:
            try:
                clean.append(validate_public_url(u, self.allowed))
            except UrlPolicyError as e:
                out.errors.append(f"{u}: {e}")
        if not clean:
            return out
        if not await self.state.reserve("fetch_url", len(clean)):
            out.errors.append("fetch budget exhausted (max 5 URLs per run)")
            return out
        kwargs: dict[str, Any] = {"tier": "extended" if mode == "rich" else "default",
                                  "session": self.state.search_session,
                                  "metadata": {**self._metadata("fetch_pages"), "goal": self.goal}}
        if mode == "text":
            kwargs["providers"] = ["tavily"]
        try:
            resp = await self.client.fetch(clean, **kwargs)
        except Exception as e:  # noqa: BLE001
            self.state.log("telem_fetch_error", urls=clean, error=f"{type(e).__name__}: {e}")
            out.errors.append(f"telem fetch failed: {type(e).__name__}: {e}")
            return out
        self._adopt_session(resp.session_id)
        for r in resp.results:
            if r.error or (r.status and r.status not in ("ok", "success")) and not r.content:
                out.errors.append(f"{r.url}: {r.status} {r.error or ''} (provider={r.provider})")
                continue
            html = _html_of(r)
            text = r.content or ""
            doc = self.state.add_document(r.url, r.title, r.provider, text, html, "telem_fetch")
            info = {"document_id": doc.id, "url": r.url, "title": r.title, "provider": r.provider,
                    "evidence_id": doc.evidence_id, "has_html": html is not None,
                    "text": text[:MAX_DOC_CHARS] + (" …[truncated]" if len(text) > MAX_DOC_CHARS else "")}
            if html is None:
                out.warnings.append(f"{r.url}: no HTML returned (capability gap, NOT proof of no form)")
            out.documents.append(info)
        self.state.log("telem_fetch", urls=clean, mode=mode, session_id=resp.session_id,
                       interaction_id=resp.interaction_id, status=resp.status,
                       results=[(r.url, r.provider, r.status) for r in resp.results])
        return out

    def inspect_form(self, document_id: str) -> ToolResult:
        doc = self.state.documents.get(document_id)
        if doc is None:
            return ToolResult(errors=[f"unknown document_id {document_id}"])
        if not doc.html:
            return ToolResult(warnings=["document has no HTML; use browser_inspect to render it"])
        forms = extract_forms(doc.html, doc.url, self.state.evidence, method="fetch_html")
        self.state.forms.extend(forms)
        self.state.log("inspect_form", document_id=document_id, forms=len(forms),
                       fields=sum(len(f.fields) for f in forms))
        return ToolResult(forms=forms, warnings=[] if forms else ["no controls or supported config found in HTML"])

    # -- server-side session record --------------------------------------------
    async def session_record(self) -> dict[str, Any] | None:
        """Pull Telem's own record of this run's session so the report links to server-side truth."""
        sid = self.state.search_session
        if not sid:
            return None
        record: dict[str, Any] = {"session_id": sid}
        try:
            hist = await self.client.sessions.history(sid)
            record["interaction_count"] = len(hist.interactions)
            record["interactions"] = [
                {k: i.get(k) for k in ("id", "type", "kind", "status", "created_at", "query") if k in i}
                for i in hist.interactions][:20]
        except Exception as e:  # noqa: BLE001
            record["error"] = f"{type(e).__name__}: {e}"
        self.state.telem_session = record
        self.state.log("telem_session_history", **{k: v for k, v in record.items() if k != "interactions"})
        return record

    # -- monitoring --------------------------------------------------------------
    async def create_monitor(self, domain: str, frequency: str = "1d", ttl: str = "30d") -> dict[str, Any]:
        """Create a Telem monitor that watches the merchant's site for lead-form changes."""
        domain = domain.lower().removeprefix("www.")
        mon = await self.client.monitors.create(
            MONITOR_QUERY.format(domain=domain, target=self.target or domain), frequency=frequency, ttl=ttl,
            include_domains=[domain], search={"num_results": 5},
            metadata={**self._metadata("monitor"), "goal": self.goal})
        info = {"monitor_id": mon.id, "status": mon.status, "frequency": mon.frequency,
                "expires_at": mon.expires_at, "session_id": mon.session_id, "domain": domain}
        self.state.monitor = info
        self.state.log("telem_monitor_created", **info)
        return info

    async def monitor_changes(self, monitor_id: str, limit: int = 20) -> list[dict[str, Any]]:
        page = await self.client.monitors.changes(monitor_id, limit=limit)
        return [c.model_dump() for c in page.changes]
