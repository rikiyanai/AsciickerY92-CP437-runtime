#!/usr/bin/env python3
"""Highlight material_id=4 / DIRT-family terrain glyph ownership in a dump.

Canonical gap: final_render_cell_dump can show per-cell glyphs, but it does
not provide an owner-family overlay for terrain glyph triage. This FL-4231
helper preserves selectable ASCII while coloring likely owner families.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from pathlib import Path


YY_DIRT_TALL = {0x3A, 0x3B, 0x2D, 0x2E, 0x2C, 0x60, 0x27}
YY_DIRT_SQUASHED = {0x2E, 0x2C, 0x3A, 0x3B, 0x2D, 0x60}
YY_DIRT = YY_DIRT_TALL | YY_DIRT_SQUASHED
SLOPE_DECAL = {0x2F, 0x5C, 0x2D, 0x5F}
SCREEN_EDGE_BOX = {0xB3, 0xC4, 0xC5}
FL4102_POSTPASS_OUTPUT = {0x2C, 0x2E, 0x27, 0x23, 0x3B, 0x3A}
LEGACY_HASH_ONLY = {0x2A, 0x22, 0x5E, 0x7E, 0xF8, 0xFA}
DIRT_EXTENDED_CURRENT = {645, 521, 543, 640}
DIRT_EXTENDED_RETIRED_KATAKANA = {644, 517, 637, 639}
FL4216_WATER_TABLE_OVERLAP = {
    664,
    546,
    661,
    668,
    649,
    662,
    517,
    648,
    652,
    657,
    654,
    556,
    647,
    659,
    665,
    656,
    660,
    669,
    653,
    651,
    666,
    663,
    650,
    667,
    655,
    658,
}
WATER_ACTIVITY_GLYPH = {670}


def classify_owner(glyph_id: int) -> tuple[str, str]:
    """Return CSS class plus owner label for a final glyph id.

    This is owner-candidate classification. Some CP437 punctuation values are
    intentionally ambiguous because multiple shader paths collapse to the same
    final glyph id after FL-4102 post-pass enforcement.
    """

    if glyph_id in DIRT_EXTENDED_RETIRED_KATAKANA:
        return "bad retired", "retired DIRT extended / katakana-risk"
    if glyph_id in DIRT_EXTENDED_CURRENT:
        return "bad dirt_ext", "current DIRT extended_glyph_for_family"
    if glyph_id in FL4216_WATER_TABLE_OVERLAP:
        return "bad table_overlap", "shared extended glyph id; overlaps FL-4216 water table"
    if glyph_id in WATER_ACTIVITY_GLYPH:
        return "bad water_activity", "670 water activity glyph id on DIRT row"
    if glyph_id in SCREEN_EDGE_BOX:
        return "edge_box", "apply_screen_space_edge box-draw"
    if glyph_id in SLOPE_DECAL:
        return "slope", "slope decal / silhouette candidate"
    if glyph_id in YY_DIRT:
        if glyph_id in FL4102_POSTPASS_OUTPUT:
            return "ok ambiguous", "YY_DIRT or FL-4102 post-pass punctuation"
        return "ok yy", "YY_DIRT family punctuation"
    if glyph_id in LEGACY_HASH_ONLY:
        return "hash", "legacy hash fallback / non-family palette"
    if glyph_id >= 512:
        return "bad extended_other", "other extended glyph on DIRT row"
    return "other", "other CP437"


def row_glyph(row: dict) -> str:
    char = row.get("glyph_char")
    if isinstance(char, str) and char:
        return char
    glyph_id = row.get("glyph_id")
    if isinstance(glyph_id, int) and 32 <= glyph_id <= 126:
        return chr(glyph_id)
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump_dir", type=Path)
    parser.add_argument("--material-id", type=int, default=4)
    parser.add_argument("--material-family", type=int, default=1)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output HTML path. Defaults inside dump dir.",
    )
    args = parser.parse_args()

    cells_path = args.dump_dir / "cells.jsonl"
    if not cells_path.exists():
        raise SystemExit(f"missing cells.jsonl: {cells_path}")

    rows = []
    target_rows = []
    owner_counts = Counter()
    glyph_counts = Counter()
    target_glyph_counts = Counter()
    family_counts = Counter()
    material_counts = Counter()
    owner_by_glyph: dict[int, str] = {}

    with cells_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            rows.append(row)
            mat = row.get("material_id")
            fam = row.get("material_family")
            glyph_id = int(row.get("glyph_id", -1))
            family_counts[fam] += 1
            material_counts[(mat, fam)] += 1
            glyph_counts[glyph_id] += 1
            if mat == args.material_id and fam == args.material_family:
                target_rows.append(row)
                cls, owner = classify_owner(glyph_id)
                owner_counts[owner] += 1
                target_glyph_counts[(glyph_id, row_glyph(row), owner)] += 1
                owner_by_glyph[glyph_id] = owner

    if not rows:
        raise SystemExit("no rows")

    max_x = max(int(r["screen_cell"]["x"]) for r in rows)
    max_y = max(int(r["screen_cell"]["y"]) for r in rows)
    by_xy = {
        (int(r["screen_cell"]["x"]), int(r["screen_cell"]["y"])): r
        for r in rows
    }
    out_path = args.out or (
        args.dump_dir / "fl4231_material4_family1_glyph_owner_highlight.html"
    )
    summary_path = out_path.with_suffix(".summary.json")

    summary = {
        "dump_dir": str(args.dump_dir),
        "total_cells": len(rows),
        "target_filter": {
            "material_id": args.material_id,
            "material_family": args.material_family,
        },
        "target_cells": len(target_rows),
        "family_counts": {str(k): v for k, v in family_counts.items()},
        "material_family_counts": {
            f"{mat}/{fam}": count
            for (mat, fam), count in sorted(material_counts.items())
        },
        "owner_counts": dict(owner_counts.most_common()),
        "target_glyph_counts": [
            {
                "glyph_id": gid,
                "glyph_char": ch,
                "count": count,
                "owner_candidate": owner,
            }
            for (gid, ch, owner), count in target_glyph_counts.most_common()
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    css = """
body { margin: 18px; background: #151515; color: #e8e8e8; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
a { color: #9bd3ff; }
.meta { max-width: 1180px; line-height: 1.35; margin-bottom: 14px; }
.legend { display: grid; grid-template-columns: repeat(2, minmax(260px, 1fr)); gap: 6px 14px; margin: 14px 0; }
.legend span { display: inline-block; min-width: 2.2em; text-align: center; margin-right: 8px; }
.buffer { white-space: pre; font-size: 12px; line-height: 12px; letter-spacing: 0; }
.cell { display: inline-block; width: 12px; height: 12px; line-height: 12px; text-align: center; color: #555; background: #1f1f1f; }
.target { color: #e8e8e8; }
.ok { background: #24452b; color: #dff7d8; }
.ambiguous { background: #36501f; color: #fff8bd; }
.slope { background: #52501d; color: #fff08a; }
.edge_box { background: #5a351c; color: #ffd29a; }
.hash { background: #2a4160; color: #c7e6ff; }
.bad { outline: 1px solid #ff3b30; }
.table_overlap { background: #3b2d00; color: #ffe691; }
.water_activity { background: #00576b; color: #b6ffff; }
.retired { background: #6d1435; color: #ffd2e2; }
.dirt_ext { background: #5e1d6d; color: #f6d0ff; }
.extended_other { background: #711d1d; color: #ffd4d4; }
.other { background: #343434; color: #ddd; }
table { border-collapse: collapse; margin: 12px 0 20px; }
td, th { border: 1px solid #555; padding: 4px 8px; text-align: left; }
th { background: #222; }
"""

    def table_rows() -> str:
        lines = []
        for item in summary["target_glyph_counts"][:40]:
            lines.append(
                "<tr>"
                f"<td>{item['glyph_id']}</td>"
                f"<td>{html.escape(str(item['glyph_char']))}</td>"
                f"<td>{item['count']}</td>"
                f"<td>{html.escape(item['owner_candidate'])}</td>"
                "</tr>"
            )
        return "\n".join(lines)

    legend = """
<div class="legend">
  <div><span class="cell target ok">.</span>YY_DIRT family punctuation</div>
  <div><span class="cell target ambiguous">,</span>YY_DIRT / FL-4102 post-pass ambiguity</div>
  <div><span class="cell target slope">/</span>slope decal / silhouette candidate</div>
  <div><span class="cell target edge_box">│</span>screen-space edge box-draw</div>
  <div><span class="cell target hash">*</span>legacy hash fallback / non-family palette</div>
  <div><span class="cell target bad table_overlap">=</span>shared extended glyph id; overlaps FL-4216 water table</div>
  <div><span class="cell target bad water_activity">≈</span>670 water activity glyph id on DIRT row</div>
  <div><span class="cell target bad retired">i</span>retired DIRT extended / katakana-risk</div>
</div>
"""

    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("<!doctype html><meta charset='utf-8'>\n")
        fh.write(f"<style>{css}</style>\n")
        fh.write("<div class='meta'>\n")
        fh.write("<h1>FL-4231 material_id=4 / family=DIRT glyph owner highlight</h1>\n")
        fh.write(
            f"<p>Source dump: <code>{html.escape(str(args.dump_dir))}</code>. "
            f"Target rows: <strong>{len(target_rows)}</strong> / {len(rows)}. "
            "All glyphs remain selectable text; non-target cells are muted.</p>\n"
        )
        fh.write(legend)
        fh.write("<h2>Top target glyph counts</h2>\n")
        fh.write("<table><tr><th>glyph_id</th><th>char</th><th>count</th><th>owner candidate</th></tr>\n")
        fh.write(table_rows())
        fh.write("</table>\n")
        fh.write(f"<p>Machine summary: <code>{html.escape(str(summary_path))}</code></p>\n")
        fh.write("</div>\n<div class='buffer'>")
        for y in range(max_y + 1):
            for x in range(max_x + 1):
                row = by_xy.get((x, y))
                if row is None:
                    fh.write("<span class='cell'> </span>")
                    continue
                glyph = html.escape(row_glyph(row))
                mat = row.get("material_id")
                fam = row.get("material_family")
                glyph_id = int(row.get("glyph_id", -1))
                classes = ["cell"]
                owner = "non-target"
                if mat == args.material_id and fam == args.material_family:
                    cls, owner = classify_owner(glyph_id)
                    classes.extend(["target"] + cls.split())
                title = (
                    f"x={x} y={y} glyph_id={glyph_id} glyph={row_glyph(row)} "
                    f"mat={mat} family={fam} owner={owner} "
                    f"terrain={row.get('terrain_cell')}"
                )
                fh.write(
                    f"<span class='{html.escape(' '.join(classes))}' "
                    f"data-glyph-id='{glyph_id}' data-owner='{html.escape(owner)}' "
                    f"title='{html.escape(title)}'>{glyph}</span>"
                )
            fh.write("\n")
        fh.write("</div>\n")

    print(json.dumps({"html": str(out_path), "summary": str(summary_path)}, indent=2))
    print("Top target glyph counts:")
    for (gid, ch, owner), count in target_glyph_counts.most_common(30):
        print(f"{count:6d} glyph_id={gid:<4} char={ch!r:<4} owner={owner}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
