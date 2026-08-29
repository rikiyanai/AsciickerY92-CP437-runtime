# Ad hoc script: FL-4216 G5: admit U+1616 (ᘖ Canadian Syllabics Carrier JO) to extended atlas at glyph_id 671 for water body Layer 1 rotation
# Created: 2026-06-07
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4216 G5: admit U+1616 ᘖ at glyph_id 671 for the Layer 1 water
body glyph rotation (~ / ≈ / ᘖ)."""
from __future__ import annotations
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"

NEW = [
    (671, 0x1616, "WATER_BODY_CARRIER_JO"),
]

PACK_ID = "water-body-layer1-symbols"


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
                "FL-4216 G5 Layer 1 water body glyphs. All water cells up "
                "to shore (per the C++ runtime model at "
                "engine/game_app.cpp:826) rotate among ~ (ASCII 0x7E), ≈ "
                "(glyph_id 670), and ᘖ (glyph_id 671) selected by per-cell "
                "hash. Wave break (Layer 3) overlays a separate staged "
                "glyph; this pack owns only the non-breaking water body."
            ),
            "script": "Symbol",
            "glyph_ids": pack_ids,
            "notes": "Bitmap receipts in fl4216_shoreline_wave_receipts.json deep_ocean_fallback_entries section after page12/page16 rebake.",
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
