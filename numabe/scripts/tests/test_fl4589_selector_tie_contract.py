import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "fl4588"))

from asciiid_counterfactual import tonal_selection  # noqa: E402


def load_leaf_predictor():
    path = ROOT / "scripts" / "fl4548" / "fl4512_leaf_prediction.py"
    spec = importlib.util.spec_from_file_location("fl4512_leaf_prediction", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profile_gpu_tie_rule_matches_scorer_family():
    source = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
    assert "if(sc<rbest||(sc==rbest&&cg<rg))" in source
    assert "else if((sc<rsecond||(sc==rsecond&&cg<rg2))&&cg!=rg)" in source
    assert "id_tie" not in source


def test_cpu_fixed_point_tonal_scan_uses_lower_glyph_id_tie():
    source = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")
    start = source.index("static uint32_t Fl4260MultiBucketPickBestL2")
    end = source.index("// FL-4512 U7", start)
    function = source[start:end]
    assert "score == best_score && gid < best_gid" in function
    assert "score == runner_score && gid < runner_gid" in function


def test_retired_runtime_scoring_and_replay_owners_are_deleted():
    resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(
        encoding="utf-8"
    )
    presenter = (ROOT / "platform/terminal_gl_present.cpp").read_text(
        encoding="utf-8"
    )
    combined = resolver + presenter
    assert "FL4512_TONAL_FIXED_POINT" not in combined
    assert "Fl4512TonalFixedPointEnabled" not in combined
    assert "tonal_fixed_point" not in combined
    assert "FL4512_TEMPORAL_SEQUENCE_REPLAY" not in presenter
    assert "Fl4512TemporalSequenceReplayEnabled" not in presenter


def test_counterfactual_observers_use_fixed_point_tie_domain():
    leaf = load_leaf_predictor()
    source = [0.5] * 6
    weights = [1.0] * 6
    shapes = {
        900: (0.5001,) * 6,
        800: (0.5002,) * 6,
    }
    assert leaf.weighted_l2_winner([900, 800], source, weights, shapes) == 800

    compiled = {
        "role_weights": weights,
        "rows": [
            {
                "role": "ramp",
                "lane_id": 0,
                "row_sha256": "ramp-row",
                "candidate_descriptors": [
                    {"glyph_id": 900, "global6": [0.5001] * 6},
                    {"glyph_id": 800, "global6": [0.5002] * 6},
                ],
            },
            {
                "role": "density",
                "lane_id": 0,
                "row_sha256": "density-row",
                "candidate_descriptors": [],
            },
        ],
    }
    cell = {
        "pool_address": {"route_key": "density:0/ramp:0"},
        "descriptor": {"global6": source},
    }
    winner, _ = tonal_selection(cell, compiled)
    assert winner == 800


def test_cpp_counterfactual_uses_same_f8_w4_formula():
    source = (ROOT / "apps" / "asciicker-presenter" / "asciiid_counterfactual.cpp").read_text(
        encoding="utf-8"
    )
    assert "CounterfactualQuantF8" in source
    assert "CounterfactualQuantW4" in source
    assert "score == best_score && glyph_id < best_gid" in source
