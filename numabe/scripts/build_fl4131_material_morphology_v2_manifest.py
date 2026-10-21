#!/usr/bin/env python3
"""Build FL-4131 material morphology v2 atlas manifest from candidate inventory.

Reads:  assets/glyphs/generated/material.morphology.v2.candidate_inventory.jsonl
Writes: assets/glyphs/fixtures/extended_glyph_material_morphology_v2.json

The output manifest is consumed by compile_glyph_manifest.py --check and --compile.
It locks the GlyphId allocation 672+ to the planned v2 pack and binds it to the
same Unifont 17.0.04 build used by material.additive.v1 so the atlas pipeline
can bake the same per-cell-size ladder.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.candidate_inventory.jsonl"
OUT = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_morphology_v2.json"
FONT = REPO_ROOT / "assets/fonts/unifont-17.0.04.otf"

CONTENT_PACK_ID = "material.morphology.v2"
FONT_ID = "unifont-17.0.04"
STYLE_ID = "regular"
SUPPORTED_CELL_SIZES = [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40]
COVERAGE_QUADRANTS_DEFAULT = 65535
COVERAGE_HINT_DEFAULT = "partial"
CELL_WIDTH_EM = 1.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_inventory() -> list[dict]:
    rows: list[dict] = []
    with INVENTORY.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    rows.sort(key=lambda r: int(r["glyph_id"]))
    return rows


def codepoint_label(row: dict) -> str:
    cps = row.get("codepoints") or []
    if not cps:
        cps = [f"U+{ord(ch):04X}" for ch in row.get("unicode_sequence", "")]
    return cps[0] if cps else "U+0000"


def build_entries(rows: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for row in rows:
        unicode_sequence = row.get("unicode_sequence", "")
        unicode_scalar = ord(unicode_sequence[0]) if unicode_sequence else 0xFFFD
        label_id = codepoint_label(row)
        source_block = (row.get("source_block") or "unknown").upper()
        label = f"MORPHV2_{source_block}_{label_id}"
        entries.append(
            OrderedDict(
                [
                    ("glyph_id", int(row["glyph_id"])),
                    ("label", label),
                    ("unicode_scalar", unicode_scalar),
                    ("coverage_quadrants", COVERAGE_QUADRANTS_DEFAULT),
                    ("coverage_hint", COVERAGE_HINT_DEFAULT),
                    ("cell_width_em", CELL_WIDTH_EM),
                ]
            )
        )
    return entries


def build_script_packs(rows: list[dict]) -> list[dict]:
    packs_by_id: dict[str, list[int]] = {}
    purposes: dict[str, str] = {}
    for row in rows:
        pack_id = (row.get("source_block") or "unknown").lower()
        gid = int(row["glyph_id"])
        packs_by_id.setdefault(pack_id, []).append(gid)
        purposes.setdefault(pack_id, f"FL-4131 v2 candidate block: {pack_id}")
    return [
        OrderedDict(
            [
                ("pack_id", pid),
                ("status", "planned"),
                ("purpose", purposes[pid]),
                ("glyph_ids", sorted(packs_by_id[pid])),
            ]
        )
        for pid in sorted(packs_by_id.keys())
    ]


def build_manifest() -> dict:
    if not FONT.exists():
        raise SystemExit(f"missing font: {FONT.relative_to(REPO_ROOT)}")
    rows = load_inventory()
    if not rows:
        raise SystemExit("inventory is empty; nothing to manifest")
    entries = build_entries(rows)
    admission_set = sorted(int(e["glyph_id"]) for e in entries)
    fallback_glyph_id = admission_set[0]
    font_sha256 = sha256_file(FONT)
    manifest = OrderedDict(
        [
            ("_comment", "FL-4131 material.morphology.v2 candidate manifest (planned + unrendered until --compile)"),
            ("manifest_version", 1),
            ("profile_kind", "extended_glyph_v1"),
            ("content_pack_id", CONTENT_PACK_ID),
            ("font_id", FONT_ID),
            ("font_sha256", font_sha256),
            ("style_id", STYLE_ID),
            ("supported_cell_sizes", SUPPORTED_CELL_SIZES),
            ("fallback_glyph_id", fallback_glyph_id),
            ("admission_set", admission_set),
            ("script_packs", build_script_packs(rows)),
            ("entries", entries),
        ]
    )
    return manifest


def main() -> int:
    manifest = build_manifest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(
        json.dumps(
            {
                "manifest_path": str(OUT.relative_to(REPO_ROOT)),
                "entry_count": len(manifest["entries"]),
                "admission_count": len(manifest["admission_set"]),
                "script_packs": len(manifest["script_packs"]),
                "font_sha256": manifest["font_sha256"],
                "fallback_glyph_id": manifest["fallback_glyph_id"],
                "first_glyph_id": manifest["entries"][0]["glyph_id"],
                "last_glyph_id": manifest["entries"][-1]["glyph_id"],
                "supported_cell_sizes": manifest["supported_cell_sizes"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
