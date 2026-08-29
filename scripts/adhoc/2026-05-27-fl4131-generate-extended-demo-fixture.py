#!/usr/bin/env python3
"""FL-4131 Phase 1.1 user-facing fail-closed inspection fixture generator.

Emits two paired files under assets/glyphs/fixtures/:

  fl4131_extended_demo.xp
    Minimal 1x1, 3-layer REXPaint sprite with layer 2 glyph = 0x4131
    (> 255). Loadable through the normal engine sprite path.

  fl4131_extended_demo.xp.glyph_profile.json
    Valid Phase 0 sidecar (profile_kind=extended_glyph_v1, valid
    glyph_manifest_hash). The hash value is a placeholder (no admitted
    manifest is referenced); Phase 2 will tighten manifest-hash admission.

Engine contract (engine/sprite.cpp:867-942, gate val03_sidecar_branch_wired):
  - sidecar exists + valid + any glyph > 255  -> LoadSprite returns NULL
    with the fail-closed stderr message
    "extended glyph loader not implemented until Phase 2 (FL-4131)".
  - sidecar exists + invalid                   -> LoadSprite returns NULL
    with sidecar-error stderr message.
  - no sidecar + glyph > 255                   -> VAL-03 legacy rejection
    (gate val03_legacy_gate_preserved).
  - sidecar exists + valid + CP437 only        -> inert GlyphPlane handoff,
    legacy rendering unchanged.

This fixture is the manual operator-visible probe for the
no_silent_glyph_truncation gate. It MUST NOT silently render as CP437.

Phase 1.1 acceptance comes only from a recorded operator inspection in
docs/research/ascii/verification/fl4131_manual_inspection_receipt.md.
Scripts and builds are diagnostics; they do not promote gates.
"""

from __future__ import annotations

import argparse
import gzip
import json
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "assets" / "glyphs" / "fixtures"
XP_PATH = FIXTURE_DIR / "fl4131_extended_demo.xp"
SIDECAR_PATH = FIXTURE_DIR / "fl4131_extended_demo.xp.glyph_profile.json"

# Placeholder 64-char lowercase hex SHA-256. The parser only validates shape;
# Phase 2+ will tighten admission against assets/glyphs/admission_allowlist.json.
PLACEHOLDER_HASH = "0" * 63 + "a"

# Extended glyph (> 255). Chosen to encode the FL number for grep-ability.
EXTENDED_GLYPH = 0x4131  # 16689 decimal


def _cell(glyph: int, fg: tuple, bk: tuple) -> bytes:
    return struct.pack("<I", glyph) + bytes(fg) + bytes(bk)


def build_xp_payload() -> bytes:
    """Decompressed REXPaint .xp payload for a 1x1, 3-layer sprite."""
    # Header: version, layers, width, height (all int32 little-endian).
    header = struct.pack("<iiii", -1, 3, 1, 1)

    # Engine loader contract: layer 0 cell data starts immediately after the
    # global header. Layers 1+ are preceded by their own (width, height) int32
    # pair. See engine/sprite.cpp layer pointer arithmetic.
    l0 = _cell(0x20, (0, 0, 0), (255, 0, 255))
    l1 = struct.pack("<ii", 1, 1) + _cell(ord("0"), (0, 0, 0), (0, 0, 0))
    l2 = struct.pack("<ii", 1, 1) + _cell(EXTENDED_GLYPH, (255, 255, 255), (0, 0, 0))

    return header + l0 + l1 + l2


def build_sidecar() -> dict:
    return {
        "sidecar_version": 1,
        "profile_kind": "extended_glyph_v1",
        "content_pack_id": "fl4131_extended_demo",
        "glyph_manifest_hash": PLACEHOLDER_HASH,
        "glyph_manifest_path": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit nonzero if outputs would change")
    args = parser.parse_args()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    xp_bytes = gzip.compress(build_xp_payload(), mtime=0)
    sidecar_text = json.dumps(build_sidecar(), indent=2) + "\n"

    if args.check:
        ok = True
        if not XP_PATH.is_file() or XP_PATH.read_bytes() != xp_bytes:
            print(f"DRIFT: {XP_PATH}", file=sys.stderr)
            ok = False
        if not SIDECAR_PATH.is_file() or SIDECAR_PATH.read_text() != sidecar_text:
            print(f"DRIFT: {SIDECAR_PATH}", file=sys.stderr)
            ok = False
        return 0 if ok else 1

    XP_PATH.write_bytes(xp_bytes)
    SIDECAR_PATH.write_text(sidecar_text)
    print(f"WROTE {XP_PATH.relative_to(REPO_ROOT)}")
    print(f"WROTE {SIDECAR_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
