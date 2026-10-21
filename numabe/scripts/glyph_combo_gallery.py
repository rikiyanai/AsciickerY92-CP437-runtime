#!/usr/bin/env python3
"""Build the MEASURED glyph-combination review data: a curses-viewer JSON and an HTML sheet.

Offline FL-4512 authoring aid. Reads nothing but the font chain and writes
.run/glyph_audit/glyph_combo_measured.json (read by
``glyph_families_viewer.py --mode seam``) and glyph_combo_gallery.html, both
gitignored. No runtime surface.

What the page shows, all on the real 8x16 unifont raster the numbers were
measured on (never a browser font):

    1. the seam study: the anti-aliasing family stacked over ``|`` with the
       seam gap and the xu-2017 DSM against the ideal continuous bar;
    2. the nine authored combinations with per-seam readouts;
    3. the top measured candidates per lattice shape, ranked by continuity,
       with the four connected seams that exist in the whole alphabet.

Usage:
    python3 scripts/glyph_combo_gallery.py            # writes the html
    python3 scripts/glyph_combo_gallery.py --per-shape 80
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_cell_features as gcf  # noqa: E402
import glyph_cell_pairs as gcp  # noqa: E402
import glyph_combo_candidates as gcc  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
OUT = REPO_ROOT / ".run" / "glyph_audit" / "glyph_combo_gallery.html"
MEASURED_JSON = REPO_ROOT / ".run" / "glyph_audit" / "glyph_combo_measured.json"
AA_FAMILY = "|!¡'.:·,`´"


def lattice_text(cells: list[dict], cols: int, rows: int) -> list[str]:
    """The glyphs laid out on their lattice, one text row per lattice row (reading order)."""
    grid = [[" "] * cols for _ in range(rows)]
    for c in cells:
        grid[int(c["row"])][int(c["col"])] = c["char"]
    return ["".join(r) for r in grid]


def rows_of(grid) -> list[str]:
    return ["".join("#" if v else "." for v in row) for row in grid]


def build_data(per_shape: int) -> dict:
    scorer = gcf.GlyphScorer()
    m = gcc.PairMeasurer(scorer)

    def raster(cp: int):
        return m.raster(cp)

    # 1. seam study over |
    bar = raster(ord("|")).grid
    box = raster(ord("│")).grid
    ref = gcp.composite(box, box, "stacked")
    study = []
    for ch in AA_FAMILY:
        ga = raster(ord(ch)).grid
        comp = gcp.composite(ga, bar, "stacked")
        p = gcp.pair_features(ga, bar, "stacked")
        study.append({
            "char": ch, "cp": ord(ch),
            "rows": rows_of(comp),
            "gap_min": p["seam"]["min"],
            "dsm_stroke": round(p["continuity"]["dsm"], 2) if p["continuity"]["dsm"] is not None else None,
            "dsm_box": round(gcp.dsm(comp, ref)["dsm"], 2),
            "cells": [{"col": 0, "row": 0, "cp": ord(ch), "char": ch},
                      {"col": 0, "row": 1, "cp": ord("|"), "char": "|"}],
        })
    study.sort(key=lambda r: r["dsm_box"])
    box_box = gcp.pair_features(box, box, "stacked")
    study_ref = {"rows": rows_of(ref), "gap_min": box_box["seam"]["min"], "dsm_box": 0.0}

    # 2. authored combos
    authored = []
    for combo in gcp.load_combos():
        f = gcp.combo_features(scorer, combo)
        cells = [(c["col"], c["row"], raster(c["cp"]).grid) for c in combo["cells"]]
        comp = gcp.grid_composite(cells, combo["cols"], combo["rows"])
        authored.append({
            "id": combo["id"], "label": combo["label"], "chars": f["chars"],
            "cols": combo["cols"], "rows_n": combo["rows"], "rows": rows_of(comp),
            "lattice": lattice_text(combo["cells"], combo["cols"], combo["rows"]),
            "beta": f["composite_beta"],
            "orientation": None if f["composite_slope_deg"] is None else round(f["composite_slope_deg"], 1),
            "dsm": None if f["composite_continuity"]["dsm"] is None else round(f["composite_continuity"]["dsm"], 2),
            "pairs": [{"cells": p["cells"], "relation": p["relation"], "gap_min": p["seam"]["min"],
                       "connected": p["seam"]["connected"], "port": round(p["port_overlap"], 2),
                       "dsm": None if p["continuity"]["dsm"] is None else round(p["continuity"]["dsm"], 2)}
                      for p in f["pairs"]],
            "source": combo.get("source", ""),
        })

    # 3. measured candidates
    rows = gcc.enumerate_candidates(c=None, max_size=3)
    gcc.measure_candidates(rows, m)
    rows.sort(key=gcc.RANK_KEYS["continuity"])
    by_shape: dict[str, list[dict]] = {}
    connected = []
    for r in rows:
        if r["seam_connected_all"]:
            connected.append(r)
        lst = by_shape.setdefault(r["shape"], [])
        if len(lst) < per_shape:
            lst.append(r)
    keep = {id(r) for lst in by_shape.values() for r in lst} | {id(r) for r in connected}

    def pack(r: dict) -> dict:
        cells = [(c["col"], c["row"], raster(c["cp"]).grid) for c in r["cells"]]
        comp = gcp.grid_composite(cells, r["cols"], r["rows"])
        return {
            "id": r["id"], "shape": r["shape"], "chars": r["chars"], "mirror": r["mirror_chars"],
            "cols": r["cols"], "rows_n": r["rows"], "rows": rows_of(comp),
            "dsm": r["continuity_mean"], "connected": r["seam_connected_all"],
            "port": r["port_overlap_mean"], "distract": r["distract_max"],
            "gaps": [p.get("seam_min") for p in r["pairs"]],
            "names": r["names"],
            "cells": r["cells"],
            "mirror_cells": r["mirror"]["cells"],
            "mirror_rows": rows_of(gcp.grid_composite(
                [(c["col"], c["row"], raster(c["cp"]).grid) for c in r["mirror"]["cells"]], r["cols"], r["rows"])),
            "lattice": lattice_text(r["cells"], r["cols"], r["rows"]),
            "mirror_lattice": lattice_text(r["mirror"]["cells"], r["cols"], r["rows"]),
        }

    packed_shapes = {k: [pack(r) for r in v] for k, v in by_shape.items()}
    counts = {}
    for r in rows:
        counts[r["shape"]] = counts.get(r["shape"], 0) + 1
    # Discovered pairs from the whole-repertoire seam index (glyph_seam_index.py),
    # one shape per (relation, width) file. Their mirror is not derived.
    for f in sorted(OUT.parent.glob("seam_pairs_*.json")) + sorted(OUT.parent.glob("runs_*.json")) + [OUT.parent / "mined_combos.json"]:
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        drows = [r for r in (d.get("rows") or d.get("families") or []) if "rows" in r]
        if not drows:
            continue
        by: dict[str, list[dict]] = {}
        for r in drows:
            r.setdefault("mirror_rows", r["rows"])
            r.setdefault("mirror_cells", r["cells"])
            r.setdefault("port", None)
            by.setdefault(r["shape"], []).append(r)
        for shape, lst in by.items():
            packed_shapes[shape] = lst[:per_shape]
            counts[shape] = int(d.get("selection", {}).get("joined", len(lst)))
    return {
        "font": scorer.font_names[0],
        "cell": [8, 16],
        "r": gcp.DSM_RADIUS,
        "alphabet": "".join(gcc.USEFUL_LINE_GLYPHS),
        "total": len(rows),
        "counts": counts,
        "per_shape": per_shape,
        "study": study, "study_ref": study_ref,
        "authored": authored,
        "shapes": packed_shapes,
        "connected": [pack(r) for r in connected],
    }


HTML = r"""<title>Seam Ledger</title>
<meta name="description" content="Measured glyph combinations on the unifont 8x16 cell">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{
  --ground:#0d1117; --panel:#131a24; --panel-2:#182130; --line:#243043;
  --ink:#ece9e0; --text:#c9cdd6; --mute:#7f8797; --accent:#d9a441; --accent-2:#5fb3a1;
  --warn:#c9573f; --cellgrid:#1f2a3b;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--text);font-family:var(--sans);font-size:15px;line-height:1.5}
a{color:var(--accent-2)}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 80px}
header{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:36px}
h1{font-family:var(--mono);font-weight:600;font-size:34px;letter-spacing:-0.01em;margin:0 0 6px;color:var(--ink);text-wrap:balance}
.sub{max-width:62ch;color:var(--mute);margin:0}
.domain{font-family:var(--mono);font-size:12.5px;color:var(--mute);display:grid;grid-template-columns:auto auto;gap:2px 18px;text-align:right}
.domain b{color:var(--text);font-weight:600}
h2{font-family:var(--mono);font-weight:600;font-size:17px;margin:44px 0 6px;color:var(--ink)}
h2 small{font-family:var(--sans);font-weight:400;color:var(--mute);font-size:13px;margin-left:10px}
.lede{max-width:66ch;color:var(--mute);margin:0 0 18px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:0 0 4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.tile{background:var(--panel);border:1px solid var(--line);padding:12px 12px 10px;display:flex;flex-direction:column;gap:8px}
.tile.ref{border-color:var(--accent-2)}
.tile.rej{opacity:.55}
.tile canvas{display:block;image-rendering:pixelated;margin:0 auto}
.chars{font-family:var(--mono);font-size:20px;color:var(--ink);text-align:center;line-height:1.1;white-space:pre}
.kv sub{font-size:.75em}
.kv{font-family:var(--mono);font-size:11.5px;display:grid;grid-template-columns:auto 1fr;gap:1px 8px;color:var(--mute);font-variant-numeric:tabular-nums}
.kv b{color:var(--text);font-weight:400;text-align:right}
.kv .hot{color:var(--accent)}
.kv .ok{color:var(--accent-2)}
.pill{display:inline-block;font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;padding:1px 6px;border:1px solid var(--line);color:var(--mute)}
.pill.on{border-color:var(--accent-2);color:var(--accent-2)}
.study{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:12px}
.bar{height:4px;background:var(--panel-2);position:relative}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--accent)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px;font-family:var(--mono);font-size:12.5px}
.controls button{background:var(--panel);border:1px solid var(--line);color:var(--text);font:inherit;padding:5px 10px;cursor:pointer}
.controls button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
.controls button:focus-visible,.controls input:focus-visible{outline:2px solid var(--accent-2);outline-offset:1px}
.controls label{color:var(--mute);display:flex;gap:6px;align-items:center}
.controls input[type=text]{background:var(--panel);border:1px solid var(--line);color:var(--ink);font:inherit;padding:5px 8px;width:9ch}
.note{border-left:2px solid var(--accent);padding:6px 12px;color:var(--text);max-width:70ch;margin:14px 0 0;font-size:14px}
.count{color:var(--mute);font-family:var(--mono);font-size:12px;margin:0 0 10px}
@media (prefers-reduced-motion:no-preference){.tile{transition:border-color .15s}.tile:hover{border-color:var(--accent)}}
</style>
<div class="wrap">
<header>
  <div>
    <p class="eyebrow">FL-4512 · measured on the raster, not typed</p>
    <h1>Seam Ledger</h1>
    <p class="sub">Every glyph pair here was placed at its real offset on the unifont cell and measured: seam gap in pixels, port contact, and D<sub>SM</sub>. Rasters are the measured pixels, drawn 1:2 as the cell is.</p>
    <p class="sub" style="margin-top:8px"><b style="color:var(--text)">D<sub>SM</sub></b> is Xu 2017’s structure-map distance (Eq. 19) between two rasters: every ink pixel looks for a partner within 5 px and pays for the difference in edge strength and stroke angle, in both directions. 0 means the composite matches the reference stroke pixel for pixel; lower reads more like one continuous stroke. It is an offline authoring number, not a runtime term.</p>
  </div>
  <div class="domain" id="domain"></div>
</header>

<section>
  <h2>Stacked over <span style="color:var(--ink)">|</span><small>the anti-aliasing family, ranked by distance from a continuous bar</small></h2>
  <p class="lede">Reference is the box-drawing bar <code>│</code> over itself: a 0 px seam, outside the plate alphabet. Inside the plate alphabet only four pairs close their seam (<code>,‾ |‾ {‾ }‾</code>, all with the overline below). The amber line is the cell seam.</p>
  <div class="study" id="study"></div>
</section>

<section>
  <h2>The nine authored combinations<small>glyph_combinations.v1.json</small></h2>
  <div class="tiles" id="authored"></div>
  <p class="note">Unifont’s <code>|</code> spans rows 2–15, so it is terminal (bottom contact only), not through-vertical; <code>_</code> sits on row 14, one row above the floor. Both are measured facts of this font, not authoring errors.</p>
</section>

<section>
  <h2>Measured candidates<small id="cand-sub"></small></h2>
  <div class="controls" id="controls">
    <span id="shape-buttons"></span>
    <label><input type="checkbox" id="conn"> connected seams only</label>
    <label>contains <input type="text" id="q" placeholder="glyphs" aria-label="filter by glyph"></label>
  </div>
  <p class="count" id="count"></p>
  <div class="tiles" id="cands"></div>
</section>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const PX = 4;
const css = getComputedStyle(document.documentElement);
const C = {ink: css.getPropertyValue('--ink').trim(), grid: css.getPropertyValue('--cellgrid').trim(),
           seam: css.getPropertyValue('--accent').trim(), ground: css.getPropertyValue('--panel-2').trim()};
function drawRaster(rows, cols, rowsN) {
  const h = rows.length, w = rows[0].length;
  const cv = document.createElement('canvas');
  cv.width = w * PX; cv.height = h * PX;
  const g = cv.getContext('2d');
  g.fillStyle = C.ground; g.fillRect(0, 0, cv.width, cv.height);
  g.strokeStyle = C.grid; g.lineWidth = 1;
  for (let x = PX; x < cv.width; x += PX) { g.beginPath(); g.moveTo(x + .5, 0); g.lineTo(x + .5, cv.height); g.stroke(); }
  for (let y = PX; y < cv.height; y += PX) { g.beginPath(); g.moveTo(0, y + .5); g.lineTo(cv.width, y + .5); g.stroke(); }
  g.fillStyle = C.ink;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (rows[y][x] === '#') g.fillRect(x * PX, y * PX, PX, PX);
  g.strokeStyle = C.seam; g.lineWidth = 1;
  const cw = w / cols, ch = h / rowsN;
  for (let c = 1; c < cols; c++) { const x = c * cw * PX; g.beginPath(); g.moveTo(x + .5, 0); g.lineTo(x + .5, cv.height); g.stroke(); }
  for (let r = 1; r < rowsN; r++) { const y = r * ch * PX; g.beginPath(); g.moveTo(0, y + .5); g.lineTo(cv.width, y + .5); g.stroke(); }
  return cv;
}
const fmt = v => v === null || v === undefined ? '—' : (typeof v === 'number' ? v.toFixed(2).replace(/\.00$/, '') : v);
document.getElementById('domain').innerHTML =
  `<span>font</span><b>${D.font}</b><span>cell</span><b>${D.cell[0]}×${D.cell[1]} px, α = 2</b><span>DSM radius</span><b>r = ${D.r}</b><span>alphabet</span><b>${D.alphabet.length} line glyphs</b><span>candidates</span><b>${D.total.toLocaleString()}</b>`;

// seam study
const study = document.getElementById('study');
const maxBox = Math.max(...D.study.map(s => s.dsm_box));
function studyTile(s, isRef) {
  const t = document.createElement('div'); t.className = 'tile' + (isRef ? ' ref' : '');
  t.appendChild(drawRaster(s.rows, 1, 2));
  const ch = document.createElement('div'); ch.className = 'chars'; ch.textContent = isRef ? '│\n│' : `${s.char}\n|`; t.appendChild(ch);
  const kv = document.createElement('div'); kv.className = 'kv';
  kv.innerHTML = `<span>gap</span><b class="${s.gap_min === 0 ? 'ok' : ''}">${fmt(s.gap_min)} px</b><span>D<sub>SM</sub> vs ││</span><b class="${isRef ? 'ok' : ''}">${fmt(s.dsm_box)}</b>` + (isRef ? '' : `<span>vs stroke</span><b>${fmt(s.dsm_stroke)}</b>`);
  t.appendChild(kv);
  const bar = document.createElement('div'); bar.className = 'bar'; bar.innerHTML = `<i style="width:${(s.dsm_box / maxBox) * 100}%"></i>`; t.appendChild(bar);
  return t;
}
study.appendChild(studyTile(D.study_ref, true));
D.study.forEach(s => study.appendChild(studyTile(s, false)));

// authored
const auth = document.getElementById('authored');
D.authored.forEach(a => {
  const t = document.createElement('div'); t.className = 'tile';
  const eb = document.createElement('p'); eb.className = 'eyebrow'; eb.textContent = a.id.replace(/_/g, ' '); t.appendChild(eb);
  t.appendChild(drawRaster(a.rows, a.cols, a.rows_n));
  const ch = document.createElement('div'); ch.className = 'chars'; ch.textContent = a.lattice.join('\n'); t.appendChild(ch);
  const kv = document.createElement('div'); kv.className = 'kv';
  const gaps = a.pairs.map(p => p.gap_min === null ? '—' : p.gap_min).join(' · ');
  kv.innerHTML = `<span>D<sub>SM</sub></span><b>${fmt(a.dsm)}</b><span>orientation</span><b>${fmt(a.orientation)}°</b><span>β₀ β₁</span><b>${a.beta[0]} ${a.beta[1]}</b><span>seam gaps</span><b>${gaps || '—'}</b>`;
  t.appendChild(kv);
  auth.appendChild(t);
});

// candidates
const shapes = Object.keys(D.shapes);
let shape = shapes[0], connOnly = false, q = '';
const sb = document.getElementById('shape-buttons');
shapes.forEach(s => {
  const b = document.createElement('button'); b.type = 'button'; b.textContent = s; b.setAttribute('aria-pressed', s === shape);
  b.addEventListener('click', () => { shape = s; render(); }); sb.appendChild(b);
});
document.getElementById('conn').addEventListener('change', e => { connOnly = e.target.checked; render(); });
document.getElementById('q').addEventListener('input', e => { q = e.target.value; render(); });
document.getElementById('cand-sub').textContent = `top ${D.per_shape} per shape by continuity, of ${D.total.toLocaleString()}`;
function render() {
  sb.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b.textContent === shape));
  let list = connOnly ? D.connected : D.shapes[shape];
  if (q) list = list.filter(r => [...q].every(ch => r.chars.includes(ch)));
  const host = document.getElementById('cands'); host.textContent = '';
  document.getElementById('count').textContent = connOnly
    ? `${list.length} candidates in the whole alphabet have a 0 px gap on every seam (all ${D.connected.length} put ‾ in the lower cell)`
    : `${list.length} shown · ${D.counts[shape].toLocaleString()} enumerated for ${shape}`;
  list.forEach(r => {
    const t = document.createElement('div'); t.className = 'tile';
    t.appendChild(drawRaster(r.rows, r.cols, r.rows_n));
    const ch = document.createElement('div'); ch.className = 'chars';
    ch.textContent = r.lattice.join('\n'); t.appendChild(ch);
    const kv = document.createElement('div'); kv.className = 'kv';
    kv.innerHTML = `<span>D<sub>SM</sub></span><b class="hot">${fmt(r.dsm)}</b><span>gaps</span><b class="${r.connected ? 'ok' : ''}">${r.gaps.map(g => g === null ? '—' : g).join(' · ')}</b><span>port</span><b>${fmt(r.port)}</b><span>mirror</span><b>${r.mirror_lattice.join(' / ')}</b>`;
    t.appendChild(kv);
    t.title = r.names.join(' / ');
    host.appendChild(t);
  });
}
render();
</script>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--per-shape", type=int, default=60)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--json-out", type=Path, default=MEASURED_JSON,
                    help="measured data for glyph_families_viewer.py --mode seam")
    a = ap.parse_args(argv)
    data = build_data(a.per_shape)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.json_out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    a.out.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    print(f"wrote {a.out} ({a.out.stat().st_size / 1024:.0f} KB) and {a.json_out}; "
          f"{data['total']} candidates measured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
