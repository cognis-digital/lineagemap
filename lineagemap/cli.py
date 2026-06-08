"""Command-line interface for LINEAGEMAP."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import extract_lineage, analyze_files, build_dbt_graph, Lineage


def _render_table(lineages: List[Lineage]) -> str:
    lines: List[str] = []
    for lin in lineages:
        lines.append(f"# {lin.model}")
        ups = ", ".join(lin.upstream_tables) or "(none)"
        lines.append(f"  upstream: {ups}")
        if not lin.columns:
            lines.append("  (no output columns parsed)")
        for col in lin.columns:
            srcs = ", ".join(col.sources) or "(literal/unknown)"
            lines.append(f"  {col.output:<24} <- {srcs}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _emit(lineages: List[Lineage], fmt: str, graph: bool) -> None:
    if fmt == "json":
        payload = {
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "models": [lin.to_dict() for lin in lineages],
        }
        if graph:
            payload["graph"] = build_dbt_graph(lineages)
        print(json.dumps(payload, indent=2))
    else:
        print(_render_table(lineages))
        if graph:
            print("\n# dbt graph (model <- upstream models)")
            for model, ups in sorted(build_dbt_graph(lineages).items()):
                print(f"  {model} <- {', '.join(ups) or '(roots)'}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Column-level lineage from SQL and dbt models.",
    )
    parser.add_argument("--version", action="version",
                        version=f"{TOOL_NAME} {TOOL_VERSION}")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    sub = parser.add_subparsers(dest="command")

    p_trace = sub.add_parser("trace", help="Trace lineage of SQL files or stdin.")
    p_trace.add_argument("files", nargs="*", help=".sql files (omit to read stdin)")
    p_trace.add_argument("--graph", action="store_true",
                         help="also emit model-to-model dbt dependency graph")

    args = parser.parse_args(argv)

    if args.command != "trace":
        parser.print_help()
        return 2

    try:
        if args.files:
            lineages = analyze_files(args.files)
        else:
            data = sys.stdin.read()
            if not data.strip():
                print("error: no SQL provided on stdin or via files",
                      file=sys.stderr)
                return 1
            lineages = [extract_lineage(data, model="stdin")]
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _emit(lineages, args.format, getattr(args, "graph", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
