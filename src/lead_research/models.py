from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    id: str
    url: str
    method: str  # telem_search | telem_fetch | fetch_html | browser_dom | declared_config
    locator: str
    excerpt: str
    truncated: bool = False


class FormField(BaseModel):
    key: str
    label: str
    type: str
    options: list[str] | None = None
    required: bool | None = None  # None = unknown, never guessed
    step: int | None = None


class FormSchema(BaseModel):
    url: str
    source: Literal["declared_config", "dom"]
    coverage: Literal["declared", "observed_partial"]
    declared_steps: int
    observed_steps: int
    fields: list[FormField]
    evidence_id: str
    notes: list[str] = Field(default_factory=list)


class Document(BaseModel):
    id: str
    url: str
    title: str = ""
    provider: str = ""
    text: str = ""
    html: str | None = None
    evidence_id: str


class Finding(BaseModel):
    statement: str
    kind: Literal["fact", "inference"] = "fact"
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchReport(BaseModel):
    """Final merchant research report. Every finding must cite evidence IDs returned by tools."""

    target: str
    status: Literal["completed", "partial"] = "completed"
    company_summary: str = ""
    business_findings: list[Finding] = Field(default_factory=list)
    lead_intake_findings: list[Finding] = Field(default_factory=list)
    added_findings: list[Finding] = Field(
        default_factory=list,
        description="Evidence found only via form/config/browser inspection, not in page body text.",
    )
    unknowns: list[str] = Field(default_factory=list)
    stop_reason: str | None = None


class ToolResult(BaseModel):
    documents: list[dict] = Field(default_factory=list)
    forms: list[FormSchema] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceStore:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def add(self, url: str, method: str, locator: str, excerpt: str, truncated: bool = False) -> Evidence:
        ev = Evidence(id=f"E{len(self._items) + 1}", url=url, method=method,
                      locator=locator, excerpt=excerpt[:500], truncated=truncated or len(excerpt) > 500)
        self._items[ev.id] = ev
        return ev

    def get(self, id: str) -> Evidence:
        return self._items[id]

    def has(self, id: str) -> bool:
        return id in self._items

    def all(self) -> list[Evidence]:
        return list(self._items.values())


DEFAULT_LIMITS = {"search": 4, "fetch_url": 5, "browser": 2}


@dataclass
class RunState:
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    documents: dict[str, Document] = field(default_factory=dict)
    forms: list[FormSchema] = field(default_factory=list)
    search_session: str | None = None
    telem_session: dict | None = None
    monitor: dict | None = None
    trace: list[dict] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve(self, kind: str, count: int = 1) -> bool:
        """Atomically reserve attempt budget. Failed calls still consume it."""
        async with self.lock:
            used = self.attempts.get(kind, 0)
            if used + count > self.limits.get(kind, 0):
                return False
            self.attempts[kind] = used + count
            return True

    def log(self, event: str, **data) -> None:
        self.trace.append({"ts": round(time.time(), 3), "event": event, **data})

    def add_document(self, url: str, title: str, provider: str, text: str, html: str | None, method: str) -> Document:
        ev = self.evidence.add(url, method, "page_text", text[:500], truncated=len(text) > 500)
        doc = Document(id=f"D{len(self.documents) + 1}", url=url, title=title, provider=provider,
                       text=text, html=html, evidence_id=ev.id)
        self.documents[doc.id] = doc
        return doc

    def trace_jsonl(self) -> str:
        return "\n".join(json.dumps(t, ensure_ascii=False, default=str) for t in self.trace) + "\n"
