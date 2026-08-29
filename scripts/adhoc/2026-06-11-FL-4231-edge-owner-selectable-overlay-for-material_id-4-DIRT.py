# Ad hoc script: FL-4231 edge-owner selectable overlay for material_id 4 DIRT-family mountain dump
# Created: 2026-06-11
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

WATER_IDS = {517, 546, 649, 664, 667, 668, 670}
DIRT_SCREEN_EDGE_PUNCT = {39: "apostrophe", 46: "dot", 44: "comma"}
DIRT_REMAINING_EDGE_NOISE = {61: "equals", 126: "tilde"}
BOUNDARY_REMAP_IDS = {222, 223, 95, 92, 179, 217}


def read_rows(dump: Path):
    rows = {}
    rows_list = []
    with (dump / "cells.jsonl").open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("row_type") != "final_render_cell":
                continue
            xy = (int(row["screen_cell"]["x"]), int(row["screen_cell"]["y"]))
            rows[xy] = row
            rows_list.append(row)
    return rows, rows_list


def glyph_text(row):
    if not row:
        return " "
    c = row.get("glyph_char")
    if isinstance(c, str) and c:
        return c
    gid = row.get("glyph_id")
    if isinstance(gid, int) and 32 <= gid <= 126:
        return chr(gid)
    return "?"


def is_target(row):
    return bool(row and row.get("material_id") == 4 and row.get("material_family") == 1)


def neighbor_rows(rows, x, y, radius=1):
    for ny in range(y - radius, y + radius + 1):
        for nx in range(x - radius, x + radius + 1):
            if nx == x and ny == y:
                continue
            row = rows.get((nx, ny))
            if row:
                yield row


def class_for(rows, row):
    if not is_target(row):
        return "outside"
    x = int(row["screen_cell"]["x"])
    y = int(row["screen_cell"]["y"])
    gid = row.get("glyph_id")
    water_state = row.get("water_state")
    has_edge_facts = "edge.screen_pass" in row
    ns = list(neighbor_rows(rows, x, y, 1))
    near_water = any((n.get("material_family") == 3 or n.get("glyph_id") in WATER_IDS or n.get("water_state") is not None) for n in ns)
    near_cliff = any(n.get("material_family") == 2 for n in ns)
    material_boundary = any(n.get("material_id") != row.get("material_id") for n in ns)
    if water_state is not None or gid in WATER_IDS:
        return "water_splice"
    if bool(row.get("edge.water_neighbor", False)) or near_water:
        return "near_water_boundary"
    if bool(row.get("edge.cliff_neighbor", False)) or near_cliff:
        return "near_cliff_boundary"
    if gid in BOUNDARY_REMAP_IDS:
        return "boundary_remap"
    if bool(row.get("edge.material_neighbor", False)) or material_boundary:
        return "material_boundary"
    if gid in DIRT_REMAINING_EDGE_NOISE:
        return "remaining_noise"
    if bool(row.get("edge.concave_reject", False)):
        return "concave_rejected_neighbor"
    if bool(row.get("edge.sidewall_pass", False)):
        return "sidewall_edge_pass"
    if bool(row.get("edge.screen_pass", False)) and bool(row.get("edge.dirt_punct", False)):
        return "screen_edge_dirt_punct"
    if has_edge_facts and bool(row.get("edge.early_bail", False)) and gid in DIRT_SCREEN_EDGE_PUNCT:
        return "punct_after_edge_bail"
    if gid in DIRT_SCREEN_EDGE_PUNCT:
        return "screen_punct_candidate"
    if float(row.get("mesh_contact_shadow") or 0.0) > 0.01:
        return "mesh_shadow_darkening"
    return "dirt_other"


def summarize(rows):
    counts = Counter()
    glyphs_by_class = defaultdict(Counter)
    for row in rows.values():
        c = class_for(rows, row)
        counts[c] += 1
        if is_target(row):
            glyphs_by_class[c][row.get("glyph_id")] += 1
    return counts, glyphs_by_class


def top_glyphs(counter, limit=12):
    out = []
    for gid, n in counter.most_common(limit):
        glyph = chr(gid) if isinstance(gid, int) and 32 <= gid <= 126 else str(gid)
        out.append({"glyph_id": gid, "glyph": glyph, "count": n})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows, _ = read_rows(args.dump)
    max_x = max(x for x, _ in rows)
    max_y = max(y for _, y in rows)
    counts, glyphs_by_class = summarize(rows)
    first_row = next(iter(rows.values()))
    has_edge_facts = "edge.screen_pass" in first_row
    summary = {
        "dump": str(args.dump),
        "target": "material_id=4 material_family=1 DIRT",
        "edge_fact_schema": "present" if has_edge_facts else "missing: no branch fact for convex accepted, concave rejected, screen_edge_score, sidewall, projected terrain coverage owner",
        "counts": dict(sorted(counts.items())),
        "glyphs_by_class": {k: top_glyphs(v) for k, v in sorted(glyphs_by_class.items())},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary_path = args.out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    css = """
body { margin:16px; background:#101010; color:#eee; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.buffer { white-space:pre; font-size:10px; line-height:10px; letter-spacing:0; }
.cell { display:inline-block; width:10px; height:10px; line-height:10px; text-align:center; color:#555; background:#171717; }
.outside { color:#4a4a4a; background:#151515; }
.water_splice { color:#e6fbff; background:#005c7a; outline:1px solid #6eeaff; }
.near_water_boundary { color:#b8f4ff; background:#123f4c; }
.near_cliff_boundary { color:#ffd8a8; background:#593200; }
.boundary_remap { color:#fff0b8; background:#6a5200; outline:1px solid #f0cf43; }
.material_boundary { color:#f8f8f8; background:#49335f; }
.remaining_noise { color:#fff; background:#8a1f1f; outline:1px solid #ff5c5c; }
.concave_rejected_neighbor { color:#f6d6ff; background:#5f214f; outline:1px solid #dc80ff; }
.sidewall_edge_pass { color:#ffe0b3; background:#5c3000; outline:1px solid #ffad42; }
.screen_edge_dirt_punct { color:#dbffd6; background:#145a20; outline:1px solid #60d66e; }
.punct_after_edge_bail { color:#d6e7ff; background:#1e3660; outline:1px solid #6da8ff; }
.screen_punct_candidate { color:#ddffd2; background:#244b25; }
.mesh_shadow_darkening { color:#d6c4ff; background:#37255d; }
.dirt_other { color:#c8c8c8; background:#242424; }
.legend span { display:inline-block; padding:2px 6px; margin:2px; }
table { border-collapse:collapse; margin:10px 0 16px; }
td, th { border:1px solid #555; padding:4px 8px; vertical-align:top; }
th { background:#222; }
code { color:#bde3ff; }
"""
    def render_table():
        rows_html = ["<tr><th>class</th><th>count</th><th>top glyph IDs</th><th>meaning</th></tr>"]
        meanings = {
            "water_splice": "DIRT row whose glyph/facts show water-owner splice; exclude from DIRT morphology patch.",
            "near_water_boundary": "DIRT row adjacent to water evidence; edge fix must treat as shoreline danger.",
            "near_cliff_boundary": "DIRT row adjacent to CLIFF family evidence; safest cliff-edge stress set.",
            "boundary_remap": "DIRT row with halfblock/material-boundary remap glyph candidate.",
            "material_boundary": "DIRT row adjacent to a different material id in screen grid.",
            "remaining_noise": "Remaining equals/tilde noise after slash/backslash patch.",
            "concave_rejected_neighbor": "At least one neighbor was rejected by the convex test.",
            "sidewall_edge_pass": "Sidewall score passed and can darken edge ink.",
            "screen_edge_dirt_punct": "Screen-space edge passed and final DIRT punctuation was emitted.",
            "punct_after_edge_bail": "Final punctuation remained even though screen edge bailed; earlier owner produced it.",
            "screen_punct_candidate": "Punctuation that could come from screen-space edge family substitution, YY_DIRT, or post-pass; current dump cannot split those owners.",
            "mesh_shadow_darkening": "DIRT row with mesh_contact_shadow nonzero after other classes.",
            "dirt_other": "Target DIRT row not assigned to a narrower evidence class.",
            "outside": "Non-target rows.",
        }
        for k, n in counts.most_common():
            glyphs = ", ".join(f"{g['glyph_id']}:{html.escape(str(g['glyph']))}x{g['count']}" for g in top_glyphs(glyphs_by_class.get(k, Counter()), 10))
            rows_html.append(f"<tr><td>{html.escape(k)}</td><td>{n}</td><td>{glyphs}</td><td>{html.escape(meanings.get(k, ''))}</td></tr>")
        return "\n".join(rows_html)

    def render_buffer():
        out = []
        for y in range(max_y + 1):
            for x in range(max_x + 1):
                row = rows.get((x, y))
                cls = class_for(rows, row)
                gid = row.get("glyph_id") if row else None
                title = json.dumps({
                    "screen": [x, y],
                    "class": cls,
                    "glyph_id": gid,
                    "glyph": glyph_text(row),
                    "material_id": row.get("material_id") if row else None,
                    "material_family": row.get("material_family") if row else None,
                    "terrain_cell": row.get("terrain_cell") if row else None,
                    "water_state": row.get("water_state") if row else None,
                    "mesh_contact_shadow": row.get("mesh_contact_shadow") if row else None,
                }, separators=(",", ":"))
                out.append(f"<span class='cell {cls}' title='{html.escape(title)}'>{html.escape(glyph_text(row))}</span>")
            out.append("\n")
        return "".join(out)

    with args.out.open("w", encoding="utf-8") as fh:
        fh.write("<!doctype html><meta charset='utf-8'>")
        fh.write(f"<style>{css}</style>")
        fh.write("<h1>FL-4231 DIRT edge-owner overlay</h1>")
        fh.write(f"<p>Dump: <code>{html.escape(str(args.dump))}</code><br>Summary: <code>{html.escape(str(summary_path))}</code></p>")
        if has_edge_facts:
            fh.write("<p><strong>Edge facts present:</strong> screen pass, sidewall pass, early bail, concave reject, material/water/cliff neighbor, and DIRT punctuation flags are decoded from cells.jsonl.</p>")
        else:
            fh.write("<p><strong>Important schema gap:</strong> this dump does not contain convex accepted, concave rejected, screen_edge_score, sidewall, or projected terrain coverage branch facts. This overlay proves only downstream rows and proxy neighborhoods.</p>")
        fh.write("<div class='legend'>")
        for cls in ["water_splice", "near_water_boundary", "near_cliff_boundary", "boundary_remap", "material_boundary", "remaining_noise", "concave_rejected_neighbor", "sidewall_edge_pass", "screen_edge_dirt_punct", "punct_after_edge_bail", "screen_punct_candidate", "mesh_shadow_darkening", "dirt_other"]:
            fh.write(f"<span class='{cls}'>{html.escape(cls)}</span>")
        fh.write("</div>")
        fh.write("<table>" + render_table() + "</table>")
        fh.write("<div class='buffer'>" + render_buffer() + "</div>")
    print(json.dumps({"html": str(args.out), "summary": str(summary_path)}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
