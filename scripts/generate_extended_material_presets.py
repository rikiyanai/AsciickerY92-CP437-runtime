#!/usr/bin/env python3
"""Generate FL-4131 ASCIIID extended material preset tables from shape rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
DEFAULT_RULES = REPO_ROOT / "assets/glyphs/presets/material_shape_preset_rules.json"
DEFAULT_JSON_OUT = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.json"
DEFAULT_HEADER_OUT = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.generated.h"
DEFAULT_MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def ident(s: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", s)
    return "".join(w[:1].upper() + w[1:] for w in words) or "Preset"


def c_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def match_query(entry: dict[str, Any], query: dict[str, Any]) -> bool:
    if not query:
        return True
    if "repertoire" in query and entry.get("repertoire") not in set(query["repertoire"]):
        return False
    if "roles" in query:
        roles = set(entry.get("roles", []))
        if not roles.intersection(set(query["roles"])):
            return False
    if "stroke_class" in query and entry.get("stroke_class") not in set(query["stroke_class"]):
        return False
    for key, field in (
        ("density_range", "density"),
        ("top_weight_range", "top_weight"),
        ("curve_score_range", "curve_score"),
        ("corner_score_range", "corner_score"),
    ):
        if key in query:
            lo, hi = query[key]
            value = float(entry.get(field, 0.0))
            if value < float(lo) or value > float(hi):
                return False
    for key, field in (
        ("density_min", "density"),
        ("top_weight_min", "top_weight"),
        ("curve_score_min", "curve_score"),
        ("corner_score_min", "corner_score"),
    ):
        if key in query and float(entry.get(field, 0.0)) < float(query[key]):
            return False
    return True


def score_entry(entry: dict[str, Any], query: dict[str, Any], col: int, cols: int) -> float:
    target_density = 0.08 + (0.58 * (col / float(max(1, cols - 1))))
    score = 1.0 - abs(float(entry.get("density", 0.0)) - target_density)
    for role in query.get("roles", []):
        if role in entry.get("roles", []):
            score += 0.35
    if entry.get("repertoire") in query.get("repertoire", []):
        score += 0.18
    if entry.get("stroke_class") in query.get("stroke_class", []):
        score += 0.18
    score += float(entry.get("curve_score", 0.0)) * 0.08
    score -= abs(float(entry.get("corner_score", 0.0)) - float(query.get("corner_score_target", entry.get("corner_score", 0.0)))) * 0.04
    return score


def choose_row(row_rule: dict[str, Any], entries_by_gid: dict[int, dict[str, Any]], entries: list[dict[str, Any]], cols: int) -> list[int]:
    query = row_rule.get("query", {})
    preferred = [int(g) for g in row_rule.get("preferred_glyph_ids", []) if int(g) in entries_by_gid]
    chosen: list[int] = []
    for gid in preferred:
        if gid not in chosen:
            chosen.append(gid)
        if len(chosen) == cols:
            return chosen

    candidates = [e for e in entries if match_query(e, query)]
    for col in range(cols):
        ranked = sorted(candidates, key=lambda e: (score_entry(e, query, col, cols), -abs(int(e["glyph_id"]) - 512)), reverse=True)
        for e in ranked:
            gid = int(e["glyph_id"])
            if gid not in chosen:
                chosen.append(gid)
                break
        if len(chosen) < col + 1 and chosen:
            chosen.append(chosen[-1])
    while len(chosen) < cols:
        chosen.append(preferred[len(chosen) % len(preferred)] if preferred else int(entries[0]["glyph_id"]))
    return chosen[:cols]


def build_presets(catalog_path: Path, rules_path: Path) -> dict[str, Any]:
    catalog = load_json(catalog_path)
    rules = load_json(rules_path)
    entries = sorted(catalog["entries"], key=lambda e: int(e["glyph_id"]))
    entries_by_gid = {int(e["glyph_id"]): e for e in entries}
    presets = []
    for preset_rule in rules["presets"]:
        rows = preset_rule["rows"]
        row_count = len(rows)
        cols = int(preset_rule.get("cols", rules.get("default_cols", 4)))
        glyphs: list[int] = []
        row_outputs = []
        for row in rows:
            row_glyphs = choose_row(row, entries_by_gid, entries, cols)
            glyphs.extend(row_glyphs)
            row_outputs.append({
                "role": row["role"],
                "glyphs": row_glyphs,
                "criteria": row.get("query", {}),
                "preferred_glyph_ids": row.get("preferred_glyph_ids", []),
            })
        presets.append({
            "purpose": preset_rule["purpose"],
            "material": preset_rule["material"],
            "name": preset_rule["name"],
            "rows": row_count,
            "cols": cols,
            "glyphs": glyphs,
            "row_roles": row_outputs,
            "note": "generated from shape_catalog; rows=terrain role, columns=density ramp",
            "criteria": "; ".join(f"{r['role']}={json.dumps(r.get('query', {}), sort_keys=True)}" for r in rows),
        })
    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "generator": "scripts/generate_extended_material_presets.py",
        "source_catalog": str(catalog_path.relative_to(REPO_ROOT)),
        "source_rules": str(rules_path.relative_to(REPO_ROOT)),
        "content_pack_id": catalog["content_pack_id"],
        "manifest_hash": catalog["manifest_hash"],
        "presets": presets,
    }


def write_header(path: Path, preset_doc: dict[str, Any]) -> None:
    lines = [
        "// Generated by scripts/generate_extended_material_presets.py. Do not edit by hand.",
        "#ifndef ASCIICKER_MATERIAL_SHAPE_PRESETS_GENERATED_H",
        "#define ASCIICKER_MATERIAL_SHAPE_PRESETS_GENERATED_H",
        "",
    ]
    array_names: list[str] = []
    for i, preset in enumerate(preset_doc["presets"]):
        name = f"kAsciiidGeneratedShapePreset{ident(preset['purpose'])}{ident(preset['name'])}{i}"
        array_names.append(name)
        glyphs = ", ".join(str(int(g)) for g in preset["glyphs"])
        lines.append(f"static const GlyphId {name}[] = {{{glyphs}}};")
    lines.append("")
    lines.append("static const AsciiidExtendedGlyphPreset kAsciiidExtendedGlyphPresets[] =")
    lines.append("{")
    for preset, array_name in zip(preset_doc["presets"], array_names):
        lines.append(
            "\t{"
            f"{c_string(preset['purpose'])}, "
            f"{c_string(preset['material'])}, "
            f"{c_string(preset['name'])}, "
            f"{array_name}, "
            f"(int)(sizeof({array_name}) / sizeof({array_name}[0])), "
            f"{int(preset['rows'])}, "
            f"{int(preset['cols'])}, "
            f"{c_string(preset['note'])}, "
            f"{c_string(preset['criteria'])}"
            "},"
        )
    lines.append("};")
    lines.append("")
    lines.append("static const int kAsciiidExtendedGlyphPresetCount = (int)(sizeof(kAsciiidExtendedGlyphPresets) / sizeof(kAsciiidExtendedGlyphPresets[0]));")
    lines.append("")
    lines.append("#endif")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify(preset_path: Path, manifest_path: Path, catalog_path: Path) -> int:
    doc = load_json(preset_path)
    manifest = load_json(manifest_path)
    catalog = load_json(catalog_path)
    admitted = set(int(g) for g in manifest.get("admission_set", []))
    catalog_ids = {int(e["glyph_id"]) for e in catalog.get("entries", [])}
    errors: list[str] = []
    for preset in doc.get("presets", []):
        glyphs = [int(g) for g in preset.get("glyphs", [])]
        if int(preset.get("rows", 0)) * int(preset.get("cols", 0)) != len(glyphs):
            errors.append(f"{preset.get('purpose')}/{preset.get('name')}: rows*cols != glyph count")
        for gid in glyphs:
            if gid not in admitted:
                errors.append(f"{preset.get('purpose')}/{preset.get('name')}: GlyphId {gid} not admitted")
            if gid not in catalog_ids:
                errors.append(f"{preset.get('purpose')}/{preset.get('name')}: GlyphId {gid} not in shape catalog")
    if errors:
        for err in errors:
            print(f"[FAIL] {err}", file=sys.stderr)
        return 1
    print(f"[OK] extended material presets verified: {len(doc.get('presets', []))} presets")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--rules", default=str(DEFAULT_RULES))
    parser.add_argument("--out-json", default=str(DEFAULT_JSON_OUT))
    parser.add_argument("--out-header", default=str(DEFAULT_HEADER_OUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        return verify(Path(args.out_json), Path(args.manifest), Path(args.catalog))
    doc = build_presets(Path(args.catalog), Path(args.rules))
    dump_json(Path(args.out_json), doc)
    write_header(Path(args.out_header), doc)
    print(f"[OK] wrote {len(doc['presets'])} generated shape presets")
    print(f"     json   = {args.out_json}")
    print(f"     header = {args.out_header}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
