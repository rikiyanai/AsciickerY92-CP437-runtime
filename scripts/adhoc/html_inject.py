#!/usr/bin/env python3
"""Inject content before </body> in an HTML file.

Accepts an HTML file path, optional needle string (default "</body></html>"),
and optional inject string (default: inject a <script> tag before </body>).

Origin: proposal BF-7d771d6842a5 (FL-049) from codex session
  rollout-2026-02-26T05-28-39-019c997e-56f3-7502-9562-e348d253e9c4
Generalized: replaced hardcoded needle/inject with CLI args, added idempotency guard.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Path to the HTML file to modify")
    parser.add_argument("--needle", default="</body></html>",
                        help="String to replace (default: </body></html>)")
    parser.add_argument("--inject", default=None,
                        help="Replacement string (default: injects a <script> tag before </body>)")
    parser.add_argument("--guard", default=None,
                        help="Idempotency guard string (default: extract from --inject)")
    parser.add_argument("--inplace", action="store_true", default=True,
                        help="Modify file in-place (default: true)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"❌ File not found: {args.file}", file=sys.stderr)
        return 1

    inject = args.inject or '<script src="flat_map_bootstrap.js"></script></body></html>'
    guard = args.guard or inject.split('"')[1] if '"' in inject else "bootstrap"

    s = args.file.read_text(encoding="utf-8", errors="ignore")

    if guard in s:
        print(f"⏭️  Guard '{guard}' already present — skipping")
        return 0

    if args.needle in s:
        s = s.replace(args.needle, inject, 1)
    else:
        s += inject

    if args.inplace:
        args.file.write_text(s, encoding="utf-8")
        print(f"✅ Modified: {args.file}")
    else:
        sys.stdout.write(s)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
