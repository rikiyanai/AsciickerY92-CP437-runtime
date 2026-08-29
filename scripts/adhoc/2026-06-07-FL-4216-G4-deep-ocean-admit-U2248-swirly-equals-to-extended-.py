# Ad hoc script: FL-4216 G4 deep ocean: admit U+2248 swirly equals to extended atlas at glyph_id 670 for deep-ocean 3-glyph rotation (~/ܚ/≈)
# Created: 2026-06-07
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4216 G4 deep-ocean fallback: admit U+2248 (≈ ALMOST EQUAL TO) to
extended_glyph_material_additive_v1.json at glyph_id 670.

Mirrors the prior G2a admission script structure. Adds one entry, extends
admission_set to include 670, registers in the shoreline-wave-symbols pack as
a deep-ocean fallback glyph (separate from the 32-lane shoreline table).
Idempotent on glyph_id collision.
"""
from __future__ import annotations
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"

NEW = [
    (670, 0x2248, "WATER_DEEP_OCEAN_SWIRLY_EQUALS_ALMOST_EQUAL_TO"),
]

PACK_ID = "deep-ocean-fallback-symbols"


def main() -> int:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    existing_ids = {int(e["glyph_id"]) for e in data["entries"]}
    added = []
    for gid, codepoint, label in NEW:
        if gid in existing_ids:
            continue
        data["entries"].append({
            "glyph_id": gid,
            "label": label,
            "unicode_scalar": codepoint,
            "coverage_quadrants": 0x4444,
            "coverage_hint": "partial",
            "cell_width_em": 1.0,
        })
        added.append(gid)
    admission = set(int(g) for g in data["admission_set"])
    admission.update(gid for gid, _, _ in NEW)
    data["admission_set"] = sorted(admission)

    packs = data["script_packs"]
    pack_ids = sorted({gid for gid, _, _ in NEW})
    existing_pack = next((p for p in packs if p["pack_id"] == PACK_ID), None)
    if existing_pack is None:
        packs.append({
            "pack_id": PACK_ID,
            "status": "partial",
            "purpose": (
                "FL-4216 G4 deep-ocean fallback symbols. Cells beyond Chebyshev "
                "radius 6 from any shore rotate among glyph 670 (almost equal "
                "to), 659 (Syriac heth), and base ASCII 0x7E (~) using x13 "
                "sweep phase so the open ocean animates instead of staying "
                "as the hard-fail red '!' marker."
            ),
            "script": "Symbol",
            "glyph_ids": pack_ids,
            "notes": "coverage_quadrants placeholder. Real bitmap hash recorded in fl4216_shoreline_wave_receipts.json after page12 rebake.",
        })
    else:
        existing_pack["glyph_ids"] = pack_ids

    PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"added {len(added)} entries: {added}")
    print(f"admission_set spans {data['admission_set'][0]}..{max(data['admission_set'])} ({len(data['admission_set'])} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
