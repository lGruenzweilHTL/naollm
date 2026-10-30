"""Command line entry point."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="naollm", description="NAO 6 x LLM bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("run", "bridge + conversation loop + operator console"),
        ("admin", "robot connection + operator console, no LLM"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", default="config.yaml", help="path to the YAML config")
        p.add_argument("--dry-run", action="store_true", help="use the fake robot")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"naollm {args.command}: not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
