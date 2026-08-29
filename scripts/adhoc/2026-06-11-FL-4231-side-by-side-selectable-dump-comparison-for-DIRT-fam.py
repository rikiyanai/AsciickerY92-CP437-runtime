# Ad hoc script: FL-4231 side-by-side selectable dump comparison for DIRT-family mountain glyphs
# Created: 2026-06-11
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path

TARGET_BAD = {47: "slash", 92: "backslash", 61: "equals", 126: "tilde"}
WATER_IDS = {664, 668, 649, 546, 667, 670, 517}


def read_rows(dump: Path):
    rows = {}
    with (dump / "cells.jsonl").open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("row_type") != "final_render_cell":
                continue
            xy = (int(row["screen_cell"]["x"]), int(row["screen_cell"]["y"]))
            rows[xy] = row
    return rows


def ch(row):
    if row is None:
        return " "
    c = row.get("glyph_char")
    if isinstance(c, str) and c:
        return c
    gid = row.get("glyph_id")
    if isinstance(gid, int) and 32 <= gid <= 126:
        return chr(gid)
    return "?"


def is_target(row):
    return row and row.get("material_id") == 4 and row.get("material_family") == 1


def cls(row):
    if not is_target(row):
        return "nontarget"
    gid = row.get("glyph_id")
    if gid in TARGET_BAD:
        return "bad"
    if gid in WATER_IDS:
        return "water"
    return "target"


def counts(rows):
    c = Counter()
    for row in rows.values():
        if is_target(row):
            c[row.get("glyph_id")] += 1
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    before = read_rows(args.before)
    after = read_rows(args.after)
    max_x = max([x for x, _ in set(before) | set(after)])
    max_y = max([y for _, y in set(before) | set(after)])
    cb = counts(before)
    ca = counts(after)
    summary = {
        "before": str(args.before),
        "after": str(args.after),
        "target_bad_counts_before": {str(k): cb.get(k, 0) for k in sorted(TARGET_BAD)},
        "target_bad_counts_after": {str(k): ca.get(k, 0) for k in sorted(TARGET_BAD)},
        "water_id_counts_before": {str(k): cb.get(k, 0) for k in sorted(WATER_IDS)},
        "water_id_counts_after": {str(k): ca.get(k, 0) for k in sorted(WATER_IDS)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary_path = args.out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    css = """
body { margin: 16px; background:#111; color:#eee; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.wrap { display:grid; grid-template-columns: max-content max-content; gap:18px; align-items:start; }
.panel { max-width:none; }
.buffer { white-space:pre; font-size:10px; line-height:10px; letter-spacing:0; }
.cell { display:inline-block; width:10px; height:10px; line-height:10px; text-align:center; color:#444; background:#1c1c1c; }
.target { color:#d8f5d1; background:#21351f; }
.bad { color:#fff; background:#8b1e1e; outline:1px solid #ff4b4b; }
.water { color:#c4f7ff; background:#063f4c; }
.nontarget { color:#555; background:#191919; }
table { border-collapse:collapse; margin:10px 0 16px; }
td, th { border:1px solid #555; padding:4px 8px; }
th { background:#222; }
code { color:#bde3ff; }
"""
    def table():
        rows = []
        rows.append("<tr><th>glyph_id</th><th>glyph</th><th>before</th><th>after</th><th>delta</th></tr>")
        for gid in [47, 92, 61, 126, 664, 668, 670, 649, 546, 667, 517]:
            b = cb.get(gid, 0)
            a = ca.get(gid, 0)
            rows.append(f"<tr><td>{gid}</td><td>{html.escape(chr(gid) if 32 <= gid <= 126 else str(gid))}</td><td>{b}</td><td>{a}</td><td>{a-b:+d}</td></tr>")
        return "\n".join(rows)
    def buffer(rows):
        out = []
        for y in range(max_y + 1):
            for x in range(max_x + 1):
                row = rows.get((x, y))
                gid = row.get("glyph_id") if row else None
                title = f"x={x} y={y} gid={gid} mat={row.get('material_id') if row else None} fam={row.get('material_family') if row else None} terrain={row.get('terrain_cell') if row else None}"
                out.append(f"<span class='cell {cls(row)}' title='{html.escape(title)}'>{html.escape(ch(row))}</span>")
            out.append("\n")
        return "".join(out)
    with args.out.open("w", encoding="utf-8") as fh:
        fh.write("<!doctype html><meta charset='utf-8'>")
        fh.write(f"<style>{css}</style>")
        fh.write("<h1>FL-4231 DIRT-family mountain glyph side-by-side</h1>")
        fh.write(f"<p>Before: <code>{html.escape(str(args.before))}</code><br>After: <code>{html.escape(str(args.after))}</code><br>Summary: <code>{html.escape(str(summary_path))}</code></p>")
        fh.write("<p>Red = DIRT-family target bad glyphs / \\ = ~. Blue = water-owner IDs recorded on DIRT rows, excluded from this DIRT morphology patch.</p>")
        fh.write("<table>" + table() + "</table>")
        fh.write("<div class='wrap'><div class='panel'><h2>Before</h2><div class='buffer'>")
        fh.write(buffer(before))
        fh.write("</div></div><div class='panel'><h2>After</h2><div class='buffer'>")
        fh.write(buffer(after))
        fh.write("</div></div></div>")
    print(json.dumps({"html": str(args.out), "summary": str(summary_path)}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
