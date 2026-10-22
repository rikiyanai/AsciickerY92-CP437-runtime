# =============================================================
# test_fl4588_cpp_candidate_material_parity.py  (FL-4588 task 76 / U9)
# =============================================================
# WHAT THIS FILE PROVES
# ---------------------
# The hidden C++ counterfactual evaluator (editor/asciiid_counterfactual.cpp)
# and the Python twin evaluator (scripts/fl4588/asciiid_counterfactual.py) must
# agree BYTE-FOR-BYTE when they evaluate a *live candidate* Material Paint
# action — the U9 vertical where the editor renders a private,
# non-mutating candidate document and hands the counterfactual genuine
# production "bridge" facts plus a candidate glyph descriptor INSTEAD of the
# fixture-only `material_scene_signatures` shortcut.
#
# WHY A SEPARATE TEST
# -------------------
# The existing golden parity test (test_fl4588_counterfactual_golden.py) only
# exercises the FIXTURE path (the frozen goldens still carry
# material_scene_signatures). This test exercises the LIVE-CANDIDATE path — the
# branch selected when the snapshot carries a `candidate_material_paint` object
# and `renderer.production_bridge_cells`. It feeds ONE synthetic live snapshot
# to BOTH evaluators and asserts they emit identical canonical bytes, that the
# result is armed (commit_armed == True), and that the visible delta is exactly
# the single painted cell {x:0, y:0}.
#
# HOW THE C++ SIDE IS RUN
# -----------------------
# There is no long-lived C++ service here: the evaluator is compiled into a tiny
# standalone CLI (scripts/fl4588/asciiid_counterfactual_cli.cpp) that reads a
# snapshot JSON file and an action JSON file and writes the canonical
# action-impact JSON to stdout. We build it exactly the way the golden parity
# harness does, then compare its stdout against the Python twin's canonical
# bytes.
# =============================================================
import copy
import json
import subprocess
import sys
from pathlib import Path

# ROOT is the repository root (this file lives two directories below it:
# <root>/scripts/tests/<thisfile>). parents[2] walks up tests -> scripts -> root.
ROOT = Path(__file__).resolve().parents[2]
# The Python twin evaluator lives in scripts/fl4588; put it on the import path.
sys.path.insert(0, str(ROOT / "scripts/fl4588"))

# `evaluate` runs one action against one snapshot and returns the action-impact
# dict. `canonical_bytes` serializes any JSON value to the ONE canonical byte
# form both evaluators agree on (stable key order, no insignificant whitespace).
# `value_hash` is the exact document-model hash the counterfactual validates the
# snapshot's document.sha256 against, so we must recompute it after we mutate the
# synthetic document model below.
from asciiid_counterfactual import (  # noqa: E402
    evaluate,
    canonical_bytes,
    value_hash,
)

# The frozen golden we derive the synthetic live snapshot FROM. It already has a
# whole canonical A3D document body, a production compiled pool, and four
# renderer cells — everything the armed Material Paint path needs — but in its
# FIXTURE shape (material_scene_signatures present).
TERRAIN_BRUSH_FIXTURE = (
    ROOT / "assets/asciiid/counterfactual/terrain_material_brush_v1.golden.json"
)


def _build_cpp_cli(tmp_path: Path) -> Path:
    """Compile the standalone C++ counterfactual CLI and return its path.

    This mirrors build_cpp() in test_fl4588_counterfactual_golden.py exactly so
    the two tests build the SAME translation units: first the vendored cJSON
    parser to an object file, then the evaluator + the CLI shim linked against
    it. Any compile error surfaces here as a failed subprocess (check=True).
    """
    cjson_object = tmp_path / "cJSON.o"
    binary = tmp_path / "asciiid_counterfactual_cpp"
    # Step 1: compile the C JSON library. -I points the compiler at the header.
    subprocess.run(
        [
            "cc", "-Iengine/third_party/cjson", "-c",
            "engine/third_party/cjson/cJSON.c", "-o", str(cjson_object),
        ],
        cwd=ROOT, check=True,
    )
    # Step 2: compile+link the evaluator and its file-driven CLI front end.
    subprocess.run(
        [
            "c++", "-std=c++17", "-I.",
            "apps/asciicker-presenter/asciiid_counterfactual.cpp",
            "scripts/fl4588/asciiid_counterfactual_cli.cpp",
            str(cjson_object), "-o", str(binary),
        ],
        cwd=ROOT, check=True,
    )
    return binary


def _synthetic_live_candidate_case():
    """Return (snapshot, action) for ONE synthetic live-candidate Material Paint.

    We start from the frozen terrain-brush golden and transform it into the
    U9 live shape, EXACTLY as the Python-side reference does in
    test_fl4588_candidate_material_render.py:
      * remove material_scene_signatures (the fixture-only scene-transfer proof),
      * attach renderer.production_bridge_cells (genuine production facts: one
        bridge cell per renderer cell, all currently owning terrain material 4),
      * attach candidate_material_paint (the private candidate render descriptor:
        cell (0,0) becomes material 5, the rest stay material 4),
      * recompute document.sha256 for the edited model.
    The candidate document SHA + changed-cell count are taken from the golden's
    own armed artifact mutation so the live prediction check passes.
    """
    case = json.loads(TERRAIN_BRUSH_FIXTURE.read_text(encoding="utf-8"))
    snapshot = copy.deepcopy(case["snapshot"])
    action = copy.deepcopy(case["action"])

    model = snapshot["document"]["model"]
    # Drop the fixture shortcut so the live-candidate branch is taken.
    model.pop("material_scene_signatures")

    production_cells = []
    candidate_cells = []
    # Walk the four frozen renderer cells in lockstep with the document terrain
    # cells, building one production bridge cell and one candidate cell each.
    for index, fixture_cell in enumerate(snapshot["renderer"]["model"]["cells"]):
        terrain_cell = model["terrain_cells"][index]
        # A stable per-cell id keyed by world coordinate; the bridge cell and the
        # candidate cell both reference it so the counterfactual can bind the
        # renderer cell to its painted terrain source.
        source_id = f"cell:{terrain_cell['world_x']}:{terrain_cell['world_y']}"
        terrain_cell["terrain_cell_id"] = source_id
        coord = copy.deepcopy(fixture_cell["cell"])
        production_cells.append(
            {
                "cell": coord,
                # profile_namespace 1 + dispatch_surface 1 + route_key 6 are the
                # genuine terrain-tonal production identity the projector checks.
                "profile_namespace": 1,
                "material_id": 4,
                "dispatch_surface": 1,
                "resolve_elev": 0,
                "resolve_shade": 2,
                "eligible": 1,
                "selected_role": 6,
                # winner_gid / presented_gid / screen must match the golden's
                # before cell (final glyph 627, fg 102, bg 16).
                "winner_gid": 627,
                "presented_gid": 627,
                "pool_address": {
                    "profile_namespace": 1,
                    "material_key": 4,
                    "route_key": 6,
                    "lane_id": 0,
                    "secondary_lane_id": 1,
                },
                "result_kind": 1,
                "absence_reason": 0,
                "descriptor_id": "fl4260.global6.v1",
                "pool_revision": 18,
                "temporal": {"decision": "keep_current"},
                "ramp": 0,
                "density": 1,
                "fact_mask": 0,
                "vertical_relation": -1,
                "direction": -1,
                "flow": -1,
                "surface_id": {
                    "world_generation": 9,
                    "owner_kind": 0,
                    "owner_x": terrain_cell["world_x"],
                    "owner_y": terrain_cell["world_y"],
                    "anchor_u_q": 0,
                    "anchor_v_q": 0,
                },
                "shape6_global": [0, 0, 0, 0, 0.5, 0.5],
                "terrain_source_cell_ids": [source_id],
                "screen": {"fg": 102, "bg": 16, "final_gid": 627},
            }
        )
        candidate_cells.append(
            {
                "cell": coord,
                # Only cell 0 flips to material 5; that is the single painted cell.
                "material_id": 5 if index == 0 else 4,
                "resolve_elev": 0,
                "resolve_shade": 2,
                # Candidate snapshots publish the renderer's explicit dispatch.
                # A missing field is malformed; a non-terrain value is the
                # paper-unspecified mixed-dispatch boundary. Keep this parity
                # fixture on the genuine terrain path, matching the Python-side
                # reference. The in-editor producer that once emitted this shape
                # live (BuildAsciiidMaterialPaintCandidateSnapshot) was retired in
                # commit 8e7c9e6b4; this fixture and the CLI are now the only
                # sources of a candidate snapshot, and the evaluator twins remain
                # the owners of its classification.
                "dispatch_surface": 1,
                "terrain_source_cell_id": source_id,
                "shape6_global": [0, 0, 0, 0, 0.5, 0.5],
            }
        )

    # The edited model has a new canonical hash; the counterfactual validates
    # document.sha256 against exactly this value.
    snapshot["document"]["sha256"] = value_hash(model)
    snapshot["renderer"]["production_bridge_cells"] = production_cells
    # The candidate render predicts the SAME after-document the golden's armed
    # artifact mutation seals (material 4 -> 5 at world cell (0,0)).
    after_hash = case["expected_action_impact"]["expected_artifact_mutations"][0][
        "after_sha256"
    ]
    snapshot["candidate_material_paint"] = {
        "schema": "asciiid.material_paint_candidate.v1",
        "base_snapshot_sha256": snapshot["snapshot_sha256"],
        "candidate_document_sha256": after_hash,
        "changed_terrain_cell_count": 1,
        "cells": candidate_cells,
    }
    return snapshot, action


def test_cpp_and_python_agree_on_live_candidate_material_paint(tmp_path):
    """The C++ twin reproduces the Python twin byte-for-byte on the live path.

    This is the U9 parity gate: one synthetic live-candidate snapshot, two
    independent evaluators, identical canonical bytes, an armed impact, and a
    visible delta of exactly the one painted cell.
    """
    snapshot, action = _synthetic_live_candidate_case()

    # Python twin: canonical bytes of the evaluated action-impact.
    python_impact = evaluate(snapshot, action)
    python_bytes = canonical_bytes(python_impact)

    # C++ twin: write the SAME canonical snapshot + action to disk, run the CLI,
    # and capture its stdout (already canonical bytes by construction).
    binary = _build_cpp_cli(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    action_path = tmp_path / "action.json"
    snapshot_path.write_bytes(canonical_bytes(snapshot))
    action_path.write_bytes(canonical_bytes(action))
    cpp_bytes = subprocess.run(
        [str(binary), str(snapshot_path), str(action_path)],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout

    # The core parity assertion: the two independent implementations converge on
    # the exact same canonical action-impact bytes.
    assert cpp_bytes == python_bytes, (
        "C++ and Python live-candidate evaluators diverged"
    )

    # Both must arm the sealed candidate with the single-cell visible delta.
    assert python_impact["theory_status"] == "defined"
    assert python_impact["commit_armed"] is True
    assert python_impact["exact_visible_delta"] == [{"x": 0, "y": 0}]

    # And the C++ stdout, decoded, agrees on those same armed facts.
    cpp_impact = json.loads(cpp_bytes)
    assert cpp_impact["commit_armed"] is True
    assert cpp_impact["exact_visible_delta"] == [{"x": 0, "y": 0}]
