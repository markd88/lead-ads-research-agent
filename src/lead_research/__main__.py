from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .agent import ResearchDeps, run_research
from .browser_tools import BrowserTools
from .models import RunState
from .report import write_report
from .telem_tools import ConfigError, merchant_domain, TelemTools, make_client, resolve_base_url


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lead_research", description="Lead Ads merchant research agent")
    p.add_argument("target", nargs="?", help="company name or URL")
    p.add_argument("--out", type=Path, help="output directory (must not exist)")
    p.add_argument("--watch", action="store_true",
                   help="after the run, create a Telem monitor (daily, 30-day TTL) for lead-form changes")
    p.add_argument("--changes", metavar="MONITOR_ID", help="print what a Lead_Lens monitor has found, then exit")
    p.add_argument("--model", default=None, help="PydanticAI model id (default: $RESEARCH_MODEL)")
    args = p.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if not args.changes and (not args.target or not args.out):
        p.error("target and --out are required (or use --changes MONITOR_ID)")
    model = args.model or os.environ.get("RESEARCH_MODEL", "anthropic:claude-fable-5-2")
    if args.changes:
        try:
            changes = asyncio.run(TelemTools(make_client(), RunState()).monitor_changes(args.changes))
        except ConfigError as e:
            print(f"config error: {e}", file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"monitor read failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        for ch in changes:
            print(json.dumps(ch, ensure_ascii=False, default=str))
        print(f"{len(changes)} change(s)", file=sys.stderr)
        return 0
    if args.out.exists():
        print(f"error: output directory {args.out} already exists; refusing to overwrite", file=sys.stderr)
        return 1
    try:
        resolve_base_url()
        client = make_client()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    if model.startswith("anthropic:") and not os.environ.get("ANTHROPIC_API_KEY"):
        print("config error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    state = RunState()
    deps = ResearchDeps(state, TelemTools(client, state, target=args.target), BrowserTools(state, args.out))

    async def go():
        report = await run_research(args.target, model, deps)
        if args.watch:
            domain = merchant_domain(args.target, state)
            if domain:
                try:
                    await deps.telem.create_monitor(domain)
                except Exception as e:  # noqa: BLE001
                    report.unknowns.append(f"Monitor not created: {type(e).__name__}: {e}")
            else:
                report.unknowns.append("Monitor not created: merchant domain unknown")
        return report

    try:
        report = asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        print(f"run failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    write_report(report, state, args.out)
    if state.monitor:
        print(f"monitor {state.monitor['monitor_id']} watching {state.monitor['domain']}; "
              f"check with: python -m lead_research --changes {state.monitor['monitor_id']}")
    print(f"{report.status}: wrote {args.out}/report.md, report.json, trace.jsonl")
    return 0 if report.status == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
