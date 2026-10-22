#!/usr/bin/env python3
"""Report whether the FL-4230 scoped exception has already been consumed.

WHY THIS EXISTS
---------------
`.water_lane_active` is the human-readable contract that stops
runtime-propagating generators from pushing post-Baseline-A drift back into the
restored Godot runtime. FL-4230 owns it.

On 2026-08-18 the operator authorised ONE narrow exception: republish six
FL-4512 derived artifacts that had drifted from their authored profile source.
That exception said it "expires when the transaction lands" -- but a sentence
is not enforcement. Nothing stopped a later agent from reading the clause,
seeing an exception for exactly the paths it wanted to write, and treating it
as live authorisation for a SECOND regeneration.

This script closes that gap. The marker now pins the exact sha256 each path
carried when the transaction landed, so "already consumed" becomes a fact a
machine can check instead of a claim a reader has to interpret.

WHAT IT DOES NOT DO
-------------------
It writes nothing, and it grants nothing. It cannot authorise a write; only
FL-4230 can. A `SPENT` result means "this exception is used up, go get new
authorisation" -- it is not permission to proceed when the result is anything
else.

EXIT CODES
    0  exception NOT yet consumed in this tree (pinned bytes are not all present)
    2  exception SPENT: every pinned path already carries its recorded sha256
    1  the marker, the clause, or a pinned path could not be read
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / ".water_lane_active"
GENERATED = Path("assets/glyphs/generated")

# One "<filename> <64-hex>" pair per pinned artifact, inside the CONSUMED block.
PIN_LINE = re.compile(r"^\s{4}(\S+\.(?:json|bin))\s+([0-9a-f]{64})\s*$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not MARKER.exists():
        print("marker .water_lane_active is absent; the guard is not active", file=sys.stderr)
        return 1
    text = MARKER.read_text(encoding="utf-8")

    if "### CONSUMED" not in text:
        print("no CONSUMED clause in the marker: the exception has not been recorded as spent")
        return 0

    # Read pins only from the CONSUMED block, so an unrelated table cannot leak in.
    block = text.split("### CONSUMED", 1)[1]
    pins = {m.group(1): m.group(2) for m in (PIN_LINE.match(l) for l in block.split("\n")) if m}
    if not pins:
        print("CONSUMED clause present but it pins no artifacts", file=sys.stderr)
        return 1

    matched, drifted, missing = [], [], []
    for name, expected in sorted(pins.items()):
        path = ROOT / GENERATED / name
        if not path.exists():
            missing.append(name)
            continue
        actual = sha256_file(path)
        (matched if actual == expected else drifted).append((name, expected, actual))

    for name, _expected, _actual in matched:
        print(f"  spent   {name}")
    for name, expected, actual in drifted:
        print(f"  DRIFTED {name}: pinned {expected[:16]}..., on disk {actual[:16]}...")
    for name in missing:
        print(f"  MISSING {name}")

    if missing:
        print("\nA pinned artifact is missing; cannot judge the exception.", file=sys.stderr)
        return 1
    if not drifted:
        print(
            f"\nSPENT: all {len(matched)} pinned artifacts already carry the bytes this "
            "exception produced.\nIt authorises nothing further. A new write to these paths "
            "needs fresh authorisation from FL-4230."
        )
        return 2
    print(
        f"\nNOT CONSUMED as recorded: {len(drifted)} of {len(pins)} pinned artifacts differ "
        "from the bytes the exception produced.\nThis is NOT permission to write. Investigate "
        "the drift and consult FL-4230."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
