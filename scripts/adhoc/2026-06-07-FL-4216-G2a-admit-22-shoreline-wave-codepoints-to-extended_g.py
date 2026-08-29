# Ad hoc script: FL-4216 G2a admit 22 shoreline-wave codepoints to extended_glyph_material_additive_v1.json
# Created: 2026-06-07
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4216 Commit G2a: admit the 22 missing shoreline-wave codepoints.

Adds entries 648..669 to extended_glyph_material_additive_v1.json with
labels keyed to the locked 8-direction x 4-stage table, extends
admission_set, and adds a new shoreline-wave-symbols script_pack
grouping all 26 table glyph_ids (4 existing + 22 new). Idempotent: if
an entry/glyph_id is already present, skip without modification.
"""
from __future__ import annotations
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"

# 22 new (glyph_id, codepoint, label) per FL-4216 G2 admission.
# Ordered by codepoint for stable diffs.
NEW = [
    (648, 0x0029, "WATER_WAVE_E_S2_RIGHT_PAREN"),
    (649, 0x03E1, "WATER_WAVE_NE_S2_GREEK_SAMPI"),
    (650, 0x0404, "WATER_WAVE_W_S4_CYRILLIC_UKRAINIAN_YE"),
    (651, 0x060C, "WATER_WAVE_SW_S3_ARABIC_COMMA"),
    (652, 0x061B, "WATER_WAVE_E_S3_ARABIC_SEMICOLON"),
    (653, 0x0639, "WATER_WAVE_SW_S2_ARABIC_AIN"),
    (654, 0x0641, "WATER_WAVE_SE_S1_ARABIC_FEH"),
    (655, 0x0645, "WATER_WAVE_NW_S2_ARABIC_MEEM"),
    (656, 0x0646, "WATER_WAVE_S_S2_ARABIC_NUN"),
    (657, 0x06D4, "WATER_WAVE_E_S4_ARABIC_FULL_STOP"),
    (658, 0x0718, "WATER_WAVE_NW_S34_SYRIAC_WAW"),
    (659, 0x071A, "WATER_WAVE_SE_S4_SYRIAC_HETH"),
    (660, 0x072B, "WATER_WAVE_S_S34_SYRIAC_SHIN"),
    (661, 0x0CE7, "WATER_WAVE_N_S34_KANNADA_ONE"),
    (662, 0x0D9E, "WATER_WAVE_NE_S34_SINHALA_AYANNA"),
    (663, 0x15F4, "WATER_WAVE_W_S2_CANADIAN_SYLLABICS_SE"),
    (664, 0x2229, "WATER_WAVE_N_S1_INTERSECT"),
    (665, 0x222A, "WATER_WAVE_S_S1_UNION"),
    (666, 0x2282, "WATER_WAVE_W_S1_SUBSET"),
    (667, 0x25DC, "WATER_WAVE_NW_S1_ARC_QUADRANT_UPPER_LEFT"),
    (668, 0x25DD, "WATER_WAVE_NE_S1_ARC_QUADRANT_UPPER_RIGHT"),
    (669, 0x25DF, "WATER_WAVE_SW_S1_ARC_QUADRANT_LOWER_LEFT"),
]

# Pre-existing entries that also belong to the shoreline-wave table.
EXISTING_PACK_MEMBERS = [517, 546, 556, 647]

# Coverage placeholder. 0x4444 = balanced partial each quadrant. Real values
# come from generate_glyph_shape_catalog.py after the next atlas bake.
PLACEHOLDER_COVERAGE = 0x4444

PACK_ID = "shoreline-wave-symbols"


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
            "coverage_quadrants": PLACEHOLDER_COVERAGE,
            "coverage_hint": "partial",
            "cell_width_em": 1.0,
        })
        added.append(gid)

    admission = set(int(g) for g in data["admission_set"])
    admission.update(gid for gid, _, _ in NEW)
    data["admission_set"] = sorted(admission)

    packs = data["script_packs"]
    pack_ids = [gid for gid, _, _ in NEW] + EXISTING_PACK_MEMBERS
    pack_ids = sorted(set(pack_ids))
    existing_pack = next((p for p in packs if p["pack_id"] == PACK_ID), None)
    if existing_pack is None:
        packs.append({
            "pack_id": PACK_ID,
            "status": "partial",
            "purpose": "FL-4216 shoreline-wave 8-direction x 4-stage glyph table. Direction = where shore is relative to the water cell; stage = distance-to-shore band.",
            "script": "Symbol",
            "glyph_ids": pack_ids,
            "notes": "Glyph picks span Arabic, Syriac, Sinhala, Kannada, Cyrillic, Canadian Syllabics, Japanese kana, Greek archaic, math symbols, and geometric arcs. coverage_quadrants for 648..669 are placeholders to be regenerated from baked atlas pixels.",
        })
    else:
        existing_pack["glyph_ids"] = pack_ids

    PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"added {len(added)} entries: {added}")
    print(f"admission_set now spans 512..{max(data['admission_set'])} ({len(data['admission_set'])} entries)")
    print(f"pack {PACK_ID} has {len(pack_ids)} members")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
