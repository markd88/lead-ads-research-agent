# Lead Ads Research Agent

**Don't just read how a merchant describes itself — discover what its lead forms actually ask.**

🎬 **Demo video (1 min):** https://vimeo.com/1246929198

A research agent for ad platforms and lead-gen agencies studying lead-driven advertisers (education, lending, auto, health). Input a company name or URL; output a sourced report of business facts, lead-capture entry points, and the questionnaire structure that ordinary page-text reading misses.

## Why

Fetching a page's body text is not the same as getting enough evidence. Multi-step forms, dynamic widgets and front-end config hold information about what a merchant asks its leads.

Verified case: [YourDreamDegree](https://yourdreamdegree.com/education-29). The first screen shows **one** question; the page's public config (`data-react-props → landingPage.steps`) declares **6 steps, 7 fields** — education level (8 options), US permanent residency (Yes/No), first/last name, email, ZIP, phone.

Reports separate **facts**, **inferences** and **unknowns**, so every conclusion is traceable to its evidence.

## Market

- **$294.6B** — US internet ad revenue in 2025, up 13.9% year over year. Social ($117.7B) and search ($114.2B), the channels where lead ads run, make up most of it. ([IAB/PwC, 2026](https://www.iab.com/insights/internet-advertising-revenue-report-full-year-2025/))
- **$5.59B → $32.1B** — the global lead generation market, 2025 to 2035, a 17.2% CAGR. ([Research and Markets](https://www.researchandmarkets.com/report/global-lead-generation-market))
- Every lead-gen advertiser that an ad platform or agency onboards needs this research: what it sells, where its lead entry points are, and what its forms ask. Today that research is done by hand.

## Architecture

**Python 3.11+ · PydanticAI · Telem SDK · Playwright Chromium** — one agent, five tools, no custom agent loop.

```text
company name / URL
    ↓
PydanticAI Research Agent  (request_limit=8, tool_calls_limit=12, 180s wall timeout)
    ├─ search_web      Telem Search, auto_routing="accuracy", goal/context metadata
    ├─ search_site     Telem Search scoped to the merchant domain (include_domains)
    ├─ fetch_pages     Telem Fetch (rich = extended tier incl. HTML; text = Tavily via Telem)
    ├─ inspect_form    deterministic parser: native controls + embedded JSON config
    └─ browser_inspect read-only Playwright render, all frames, screenshot
    ↓
report.md + report.json + trace.jsonl (+ screenshots/)
    ↓ (--watch)
Telem Monitor: daily check of the merchant domain for lead-form changes
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
- **One run = one Telem session.** Every search and fetch shares the session created by the first call; at the end the agent pulls Telem's server-side session history into the report, so the run can be audited on hack.telem.ai.
- **Agent intent travels with each request.** Searches send `goal`, `context` and `metadata` (app, tool, target), and fetches send `metadata`, so Telem routes and records with the agent's purpose.
- **Site-scoped discovery.** Once the official domain is known, `search_site` uses `include_domains` to find apply/quote/eligibility pages on the merchant's own site.
- **Continuous monitoring.** `--watch` creates a Telem Monitor (daily, 30-day TTL, scoped to the merchant domain) whose agent tracks changes to lead forms and eligibility questions; `--changes` reads what it found.
- `auto_routing="accuracy"` is set explicitly.
- Fetch `rich` mode requests the `extended` tier for HTML; `text` mode pins the Tavily provider.
- Real provider, status, session and interaction IDs are written to `trace.jsonl`.
- Per-URL failures inside an HTTP 200 are surfaced, not hidden. Missing HTML is reported as a capability gap — not as "no form" — and the agent falls back to the browser.

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

Watch a merchant's lead forms over time (creates a Telem Monitor, then read its changes later):

```bash
python -m lead_research "https://yourdreamdegree.com/education-29" --out runs/watch-demo --watch
python -m lead_research --changes <MONITOR_ID>
```

Exit codes: `0` completed, `2` partial (budget/timeout), `1` config or run error. Existing output directories are never overwritten. Model defaults to `anthropic:claude-fable-5-2` (`RESEARCH_MODEL` / `--model`).

Built-in budgets keep every run fast and predictable: 4 searches, 5 fetched URLs, 2 browser inspections, 8 model requests, 180 s wall clock.

## Output

```text
runs/education-demo/
  report.md     business findings, lead-intake findings, added-by-form-discovery, forms table, unknowns, evidence
  report.json   structured report + forms + evidence + budget used
  trace.jsonl   every tool action with provider / session / interaction IDs, session history, monitor
  screenshots/  browser evidence (when used)
```

Every finding cites evidence IDs recorded during the run; findings with invalid citations are removed and listed as unknowns. Forms distinguish `declared_steps` (from config) from `observed_steps` (actually rendered) — six declared steps are never reported as interactively verified.

## Responsible by design

- Fresh browser context with no personal cookies; read-only browsing — non-GET requests are aborted.
- Public HTTP(S) only; private/loopback addresses blocked at URL and request level.
- Page content is treated as data; the model has no arbitrary-JS or shell tool.
- Only public question definitions are extracted — no hidden tokens, input values or lead data.

## Tests

```bash
python -m pytest -q tests/test_core.py
```

16 fast, deterministic tests: a sanitized fixture of the real education page config, a Telem client stub and PydanticAI `FunctionModel` cover the full agent loop.

## Highlights

- **6 steps / 7 fields** recovered from a page that shows one question on screen.
- **Deep Telem integration:** accuracy routing, one session per run, goal-aware requests, site-scoped search, session history and continuous monitors.
- **Evidence-first reports:** every finding cites an evidence ID; facts, inferences and unknowns are kept apart.
- **Browser fallback** that renders all frames read-only and saves screenshots.

## Roadmap

- More verticals: lending, auto, insurance, health.
- Monitor webhooks that alert account teams when a merchant's lead form changes.
- Branch exploration of conditional form paths.
- Generate native lead-form drafts for ad platforms from the extracted schema.
