# Lead Ads Research Agent

**Don't just read how a merchant describes itself — discover what its lead forms actually ask.**

A research agent for ad platforms and lead-gen agencies studying lead-driven advertisers (education, lending, auto, health). Input a company name or URL; output a sourced report of business facts, lead-capture entry points, and the questionnaire structure that ordinary page-text reading misses.

## Why

Fetching a page's body text is not the same as getting enough evidence. Multi-step forms, dynamic widgets and front-end config hold information about what a merchant asks its leads.

Verified case: [YourDreamDegree](https://yourdreamdegree.com/education-29). The first screen shows **one** question; the page's public config (`data-react-props → landingPage.steps`) declares **6 steps, 7 fields** — education level (8 options), US permanent residency (Yes/No), first/last name, email, ZIP, phone.

Reports separate **facts**, **inferences** and **unknowns**. A merchant asking a question does not prove it rejects anyone on that basis.

## Architecture

**Python 3.11+ · PydanticAI · Telem SDK · Playwright Chromium** — one agent, four tools, no custom agent loop.

```text
company name / URL
    ↓
PydanticAI Research Agent  (request_limit=8, tool_calls_limit=12, 180s wall timeout)
    ├─ search_web      Telem Search, auto_routing="accuracy", shared session
    ├─ fetch_pages     Telem Fetch (rich = extended tier incl. HTML; text = Tavily via Telem)
    ├─ inspect_form    deterministic parser: native controls + embedded JSON config
    └─ browser_inspect read-only Playwright render, all frames, screenshot
    ↓
report.md + report.json + trace.jsonl (+ screenshots/)
```

| Module | Responsibility |
| --- | --- |
| `src/lead_research/agent.py` | Agent, tools, budgets, partial-on-timeout |
| `src/lead_research/telem_tools.py` | Telem search/fetch, base-URL guard, evidence + trace |
| `src/lead_research/forms.py` | Form extraction (never executes page scripts) |
| `src/lead_research/browser_tools.py` | Fresh context, GET-only, public-URL request filter |
| `src/lead_research/url_policy.py` | Rejects private IPs, userinfo, non-HTTP schemes |
| `src/lead_research/report.py` | Citation validation, escaped Markdown, JSON, trace |

## Hackathon Telem deployment

| Purpose | URL |
| --- | --- |
| Platform / keys / traces | https://hack.telem.ai/ |
| Docs | https://hack.telem.ai/doc/ |
| SDK base URL | `https://hack.telem.ai/api` |

The app **refuses any `TELEM_BASE_URL` other than `https://hack.telem.ai/api`** (no `/v1` suffix; never the production router).

How Telem is used:
- `auto_routing="accuracy"` is set explicitly; searches reuse the first `session_id`.
- Fetch `rich` mode requests the `extended` tier for HTML; `text` mode pins the Tavily provider.
- Real provider, status, session and interaction IDs are written to `trace.jsonl`.
- Per-URL failures inside an HTTP 200 are surfaced, not hidden. Missing HTML is reported as a capability gap — not as "no form" — and the agent falls back to the browser.
- No automatic retries.

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
cp .env.example .env   # fill TELEM_API_KEY and ANTHROPIC_API_KEY
```

## Run

```bash
python -m lead_research "https://yourdreamdegree.com/education-29" --out runs/education-demo
python -m lead_research "YourDreamDegree" --out runs/company-demo
```

Exit codes: `0` completed, `2` partial (budget/timeout), `1` config or run error. Existing output directories are never overwritten. Model defaults to `anthropic:claude-fable-5-2` (`RESEARCH_MODEL` / `--model`).

Live runs spend Telem and model credits. Per-run caps: 3 searches, 5 fetched URLs, 2 browser inspections, 8 model requests.

## Output

```text
runs/education-demo/
  report.md     business findings, lead-intake findings, added-by-form-discovery, forms table, unknowns, evidence
  report.json   structured report + forms + evidence + budget used
  trace.jsonl   every tool action with provider / session / interaction IDs
  screenshots/  browser evidence (when used)
```

Every finding cites evidence IDs recorded during the run; findings with invalid citations are removed and listed as unknowns. Forms distinguish `declared_steps` (from config) from `observed_steps` (actually rendered) — six declared steps are never reported as interactively verified.

## Safety & scope

- Local CLI, not a public scraping service.
- Fresh browser context, no personal cookies. No clicks, typing, logins or submissions; non-GET requests are aborted.
- Public HTTP(S) only; private/loopback addresses blocked at URL and request level.
- Page content is treated as data; the model has no arbitrary-JS or shell tool.
- Only public question definitions are extracted — no hidden tokens, input values or lead data.

## Tests

```bash
python -m pytest -q tests/test_core.py
```

Offline: sanitized fixture of the real education page config, a fake Telem client and PydanticAI `FunctionModel`. No paid API calls.

## Status

- ✅ Implemented and tested offline: form parser (6 steps / 7 fields case), Telem wrappers (routing, session reuse, mixed failures, Tavily text mode, base-URL guard), budgets, URL policy, citation validation, report writer, CLI, end-to-end agent loop with a test model.
- ✅ Playwright tool smoke-tested against a local fixture server (6 steps / 7 fields extracted, screenshot saved, unallowed loopback URL rejected).
- ⚠️ Not yet verified live: real Telem calls against merchant pages, a real model run, and the browser on a live merchant site.

