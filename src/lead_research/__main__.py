from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .agent import ResearchDeps, run_research
from .browser_tools import BrowserTools
from .models import RunState
from .report import write_report
from .telem_tools import ConfigError, TelemTools, make_client, resolve_base_url


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lead_research", description="Lead Ads merchant research agent")
    p.add_argument("target", help="company name or URL")
    p.add_argument("--out", required=True, type=Path, help="output directory (must not exist)")
    p.add_argument("--model", default=None, help="PydanticAI model id (default: $RESEARCH_MODEL)")
    args = p.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    model = args.model or os.environ.get("RESEARCH_MODEL", "anthropic:claude-fable-5-2")
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
    deps = ResearchDeps(state, TelemTools(client, state), BrowserTools(state, args.out))
    try:
        report = asyncio.run(run_research(args.target, model, deps))
    except Exception as e:  # noqa: BLE001
        print(f"run failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    write_report(report, state, args.out)
    print(f"{report.status}: wrote {args.out}/report.md, report.json, trace.jsonl")
    return 0 if report.status == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
