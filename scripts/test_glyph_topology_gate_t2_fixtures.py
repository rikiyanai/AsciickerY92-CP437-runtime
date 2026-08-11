"""FL-4208 Gate T2 — FIXTURE INTEGRITY ONLY (round-6 step 3).

This freezes and self-validates the oracle data the FUTURE skeleton-graph
constructor will be checked against. It does **NOT** implement or test the graph
constructor (junction-cluster collapse, degree-2 suppression, edge tracing,
mask-vs-graph equality) — that is Gate T2 proper and stays deferred.

What this test asserts:
  * the oracle JSON is well-formed;
  * each synthetic mask's `beta_0` / `beta_1_raw` match the frozen oracle, grounded
    via the committed mask-side primitives (components / _enclosed_holes);
  * each reviewed Unifont glyph trace's `beta_0` / `beta_1_raw` match.

What this test deliberately does NOT touch:
  * `degree_multiset`, `nodes`, `edges` — those require the unbuilt constructor;
  * any mask<->graph equality assertion.

Run: `python3 -m pytest scripts/test_glyph_topology_gate_t2_fixtures.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from glyph_skeleton import components, _enclosed_holes  # noqa: E402

ORACLES = REPO_ROOT / "tests" / "fixtures" / "fl4208" / "gate_t2_graph_oracles.json"
UNIFONT = REPO_ROOT / "assets" / "fonts" / "unifont-17.0.04.otf"
INK_THRESHOLD = 96


def _load():
    return json.loads(ORACLES.read_text())


def _mask_to_grid(rows: list[str]) -> np.ndarray:
    width = len(rows[0])
    assert all(len(r) == width for r in rows), "ragged mask"
    return np.array([[1 if c == "#" else 0 for c in r] for r in rows], np.uint8)


def _unifont_grid(cp: int, size: int = 96, font_px: int = 192) -> np.ndarray:
    font = ImageFont.truetype(str(UNIFONT), font_px)
    ch = chr(cp)
    bbox = font.getbbox(ch)
    pad = 8
    canvas = Image.new("L", (bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2), 0)
    ImageDraw.Draw(canvas).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=font)
    ink = canvas.getbbox()
    crop = canvas.crop(ink)
    cw, chh = crop.size
    scale = min(size / cw, size / chh)
    nw, nh = max(1, round(cw * scale)), max(1, round(chh * scale))
    rs = crop.resize((nw, nh), Image.LANCZOS)
    out = Image.new("L", (size, size), 0)
    out.paste(rs, ((size - nw) // 2, (size - nh) // 2))
    return (np.asarray(out) > INK_THRESHOLD).astype(np.uint8)


def test_oracle_json_well_formed():
    d = _load()
    assert d["synthetic_graph_oracles"], "no synthetic oracles"
    assert d["reviewed_glyph_traces"]["traces"], "no glyph traces"
    for o in d["synthetic_graph_oracles"]:
        for key in ("name", "mask", "beta_0", "beta_1_raw", "nodes", "edges", "degree_multiset"):
            assert key in o, f"{o.get('name')}: missing {key}"
        # Euler-characteristic self-consistency of the FROZEN graph (not computed
        # from a constructor): cycle_rank = E - V + beta_0.
        cycle_rank = o["edges"] - o["nodes"] + o["beta_0"]
        assert cycle_rank == o["beta_1_raw"], (
            f"{o['name']}: frozen graph E-V+C={cycle_rank} != beta_1_raw={o['beta_1_raw']}")


def test_synthetic_masks_ground_beta0_beta1():
    for o in _load()["synthetic_graph_oracles"]:
        g = _mask_to_grid(o["mask"])
        assert components(g) == o["beta_0"], (
            f"{o['name']}: components={components(g)} != beta_0={o['beta_0']}")
        assert _enclosed_holes(g) == o["beta_1_raw"], (
            f"{o['name']}: _enclosed_holes={_enclosed_holes(g)} != beta_1_raw={o['beta_1_raw']}")


def test_glyph_traces_ground_beta0_beta1():
    assert UNIFONT.exists(), f"frozen Unifont fixture missing: {UNIFONT}"
    for t in _load()["reviewed_glyph_traces"]["traces"]:
        cp = int(t["codepoint"][2:], 16)
        g = _unifont_grid(cp)
        assert components(g) == t["beta_0"], (
            f"{t['codepoint']} {t['char']}: components={components(g)} != beta_0={t['beta_0']}")
        assert _enclosed_holes(g) == t["beta_1_raw"], (
            f"{t['codepoint']} {t['char']}: holes={_enclosed_holes(g)} != beta_1_raw={t['beta_1_raw']}")


if __name__ == "__main__":
    d = _load()
    print(f"synthetic oracles: {len(d['synthetic_graph_oracles'])}")
    for o in d["synthetic_graph_oracles"]:
        g = _mask_to_grid(o["mask"])
        print(f"  {o['name']:14} beta_0={components(g)} (want {o['beta_0']})  "
              f"beta_1_raw={_enclosed_holes(g)} (want {o['beta_1_raw']})")
    print("Gate-T2 constructor: NOT implemented (degree_multiset/edges frozen, unasserted).")
