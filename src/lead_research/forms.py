"""Deterministic form discovery: native controls + embedded config. Never executes page scripts."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from .models import EvidenceStore, FormField, FormSchema

SKIP_INPUT_TYPES = {"hidden", "submit", "button", "image", "reset"}


def _label_for(soup: BeautifulSoup, el) -> str:
    if el.get("id"):
        lab = soup.find("label", attrs={"for": el["id"]})
        if lab and lab.get_text(strip=True):
            return lab.get_text(" ", strip=True)
    for attr in ("aria-label", "placeholder", "name", "id"):
        if el.get(attr):
            return str(el[attr])
    return el.name


def _native_fields(soup: BeautifulSoup) -> list[FormField]:
    fields: list[FormField] = []
    for el in soup.find_all(["input", "select", "textarea"]):
        typ = (el.get("type") or el.name).lower() if el.name == "input" else el.name
        if el.name == "input" and typ in SKIP_INPUT_TYPES:
            continue
        options = None
        if el.name == "select":
            options = [o.get_text(" ", strip=True) for o in el.find_all("option")
                       if o.get_text(strip=True) and o.get("value", "x") != ""]
        # values the user may have typed are never read
        fields.append(FormField(
            key=str(el.get("name") or el.get("id") or f"field_{len(fields) + 1}"),
            label=_label_for(soup, el), type=typ, options=options,
            required=True if el.has_attr("required") or el.get("aria-required") == "true" else None,
        ))
    return fields


def _declared_steps(soup: BeautifulSoup) -> list[dict] | None:
    for node in soup.find_all(attrs={"data-react-props": True}):
        try:
            props = json.loads(node["data-react-props"])
        except (ValueError, TypeError):
            continue
        steps = (props.get("landingPage") or {}).get("steps") if isinstance(props, dict) else None
        if isinstance(steps, list) and steps:
            return steps
    return None


def extract_forms(html: str, url: str, store: EvidenceStore, method: str = "fetch_html") -> list[FormSchema]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    forms: list[FormSchema] = []

    steps = _declared_steps(soup)
    if steps:
        fields: list[FormField] = []
        for i, step in enumerate(steps, start=1):
            for q in step.get("questions") or []:
                opts = q.get("options")
                if opts and isinstance(opts[0], dict):
                    opts = [str(o.get("label") or o.get("text") or o.get("value")) for o in opts]
                fields.append(FormField(
                    key=str(q.get("key") or q.get("id")), label=str(q.get("text") or step.get("title") or ""),
                    type=str(q.get("type") or "unknown"), options=opts or None, required=None, step=i))
        summary = "; ".join(f"step {f.step}: {f.key}" for f in fields)
        ev = store.add(url, "declared_config", "[data-react-props] -> landingPage.steps",
                       f"{len(steps)} declared steps, {len(fields)} fields: {summary}")
        forms.append(FormSchema(
            url=url, source="declared_config", coverage="declared", declared_steps=len(steps),
            observed_steps=0, fields=fields, evidence_id=ev.id,
            notes=["Declared by public page config; runtime branching, validation and requiredness not verified."]))

    native = _native_fields(soup)
    if native:
        ev = store.add(url, method, "input/select/textarea",
                       "; ".join(f"{f.key} ({f.type})" for f in native))
        forms.append(FormSchema(
            url=url, source="dom", coverage="observed_partial", declared_steps=0,
            observed_steps=1 if method == "browser_dom" else 0, fields=native, evidence_id=ev.id,
            notes=["Only controls present in this snapshot; later steps/branches not observed."]))
    return forms
