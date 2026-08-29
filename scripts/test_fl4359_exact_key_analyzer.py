#!/usr/bin/env python3
"""Pin the FL-4359 exact-key analyzer logic: fail-closed + classification + dup preservation."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "analyze_fl4359_exact_key.py"


def _row(frame, sk, stable, screen, glyph, edge=0.0, shade=5, step=0.0, role=0, gr=0,
         mat="grass", detail=0):
    return {
        "row_type": "fl4359_jitter_cell", "frame_id": frame, "selected_owner": "terrain",
        "source_key": sk, "terr_stable_cell_gpu": {"x": stable[0], "y": stable[1]},
        "screen_cell": {"x": screen[0], "y": screen[1]}, "glyph_id": glyph,
        "material_role": mat, "terrain_product_detail": detail,
        "diag_candidate_glyph": glyph, "diag_glyph_role": gr, "diag_role": role,
        "diag_ramp_band": 0, "diag_boundary": 0, "diag_edge_score": edge,
        "diag_sidewall_score": 0.0, "diag_shade_band": shade, "diag_step_amount": step,
        "diag_diff_h": 0.0, "diag_diff_v": 0.0,
    }


def _run(rows, *extra):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        path = f.name
    p = subprocess.run([sys.executable, str(TOOL), path, "--json", *extra],
                       capture_output=True, text=True)
    out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
    return p.returncode, out


def test_fail_closed_missing_field():
    rows = [{"row_type": "fl4359_jitter_cell", "frame_id": 0, "selected_owner": "terrain",
             "glyph_id": 100, "screen_cell": {"x": 1, "y": 1}}]
    rc, out = _run(rows)
    assert rc == 2, rc
    assert out["ok"] is False and "predates" in out["fail_closed"], out


def test_threshold_crossing_authorizes_deadband():
    # one stable key, screen-stable, edge_score straddles 1.15 -> glyph flips with the crossing
    rows = [
        _row(0, 7, (10, 20), (5, 5), glyph=59, edge=1.30, gr=1),
        _row(1, 7, (10, 20), (5, 5), glyph=121, edge=0.90, gr=0),
        _row(2, 7, (10, 20), (5, 5), glyph=59, edge=1.30, gr=1),
    ]
    rc, out = _run(rows)
    assert rc == 0, out
    assert out["cross_frame_output_variant_keys"] == 1, out
    assert out["cross_frame_variant_classification"]["threshold_crossing"] == 1, out
    assert out["deadband_authorized"] is True, out


def test_source_swap_not_deadband():
    # same screen cell, DIFFERENT source_key per frame = winner swap; NOT a same-product threshold
    rows = [
        _row(0, 7, (10, 20), (5, 5), glyph=59, edge=2.0),
        _row(0, 8, (11, 20), (5, 5), glyph=121, edge=2.0),  # frame 0 same screen, 2 sources
        _row(1, 7, (10, 20), (5, 5), glyph=59, edge=2.0),
        _row(1, 8, (11, 20), (5, 5), glyph=121, edge=2.0),
    ]
    rc, out = _run(rows)
    assert rc == 0, out
    # neither key crosses a threshold (edge constant) -> not threshold_crossing
    assert out["cross_frame_variant_classification"]["threshold_crossing"] == 0, out


def test_unexplained_blocks_deadband():
    # output flips with NO scalar crossing, NO source swap -> unexplained, deadband NOT authorized
    rows = [
        _row(0, 7, (10, 20), (5, 5), glyph=100, edge=2.0, shade=5),
        _row(1, 7, (10, 20), (5, 5), glyph=101, edge=2.0, shade=5),
    ]
    rc, out = _run(rows)
    assert rc == 0, out
    assert out["cross_frame_variant_classification"]["unexplained"] == 1, out
    assert out["deadband_authorized"] is False, out


def test_duplicate_ansi_preserved():
    # same exact key appears twice in one frame with DIFFERENT output -> intra-frame variant
    rows = [
        _row(0, 7, (10, 20), (5, 5), glyph=100),
        _row(0, 7, (10, 20), (6, 5), glyph=200),  # same key, same frame, different screen+glyph
        _row(1, 7, (10, 20), (5, 5), glyph=100),
    ]
    rc, out = _run(rows)
    assert rc == 0, out
    assert out["duplicate_ansi_keys"] == 1, out
    assert out["intra_frame_output_variant_keys"] == 1, out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} exact-key analyzer tests passed.")
