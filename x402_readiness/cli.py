"""Command line entry point: `x402-readiness <url> [<url> ...]`."""

from __future__ import annotations

import argparse
import json
import sys

from .core import audit

LABEL = {"ready": "READY", "workable": "WORKABLE", "impaired": "IMPAIRED",
         "broken": "BROKEN", "unreachable": "UNREACHABLE"}


def _fmt_human(r: dict) -> str:
    lines = []
    head = "%-11s %3d/100  HTTP %-5s %s" % (
        LABEL.get(r["verdict"], r["verdict"].upper()), r["score"], r["status"], r["url"])
    lines.append(head)
    if r["verdict"] == "ready" and not r["findings"]:
        lines.append("             nothing to fix; buyers can read the payment requirement.")
    for i, detail in enumerate(r["fix_order"], 1):
        lines.append("   %d. %s" % (i, detail))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="x402-readiness",
        description="Audit whether an x402 endpoint can actually be reached and paid.",
        epilog="Exit code is 0 when every endpoint scores >= 90, otherwise 1. "
               "Useful as a CI gate: `x402-readiness $PAID_URL || exit 1`.",
    )
    ap.add_argument("urls", nargs="+", help="HTTP(S) URL of an x402 resource")
    ap.add_argument("--method", default="GET", help="HTTP method to probe with (default GET)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--fail-under", type=int, default=90,
                    help="exit non-zero if any endpoint scores below this (default 90)")
    args = ap.parse_args(argv)

    results = [audit(u, method=args.method) for u in args.urls]

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
    else:
        for i, r in enumerate(results):
            if i:
                print()
            print(_fmt_human(r))

    worst = min(r["score"] for r in results)
    return 0 if worst >= args.fail_under else 1


if __name__ == "__main__":
    sys.exit(main())
