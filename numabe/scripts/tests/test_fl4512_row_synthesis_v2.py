import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "assets/glyphs/generated"
ARTIFACT = GENERATED / "fl4512.row_synthesis.v2.json"


def test_v2_artifact_is_hash_closed_and_monotonic() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    rank_by_gid = {
        int(record["glyph_id"]): int(record["rank"])
        for record in (
            json.loads(line)
            for line in (GENERATED / "fl4512.reduction_order.v1.jsonl").read_text().splitlines()
        )
    }
    assert artifact["schema"] == "fl4512.row_synthesis.v2"
    assert artifact["row_count"] == 64
    assert artifact["all_rows_monotonic"] is True
    assert artifact["inputs"]["rejected_v1_sha256"] == hashlib.sha256(
        (GENERATED / "fl4512.row_synthesis.v1.json").read_bytes()
    ).hexdigest()

    budgets = [str(value) for value in artifact["budgets"]]
    for row in artifact["rows"]:
        previous_mean = None
        previous_max = None
        for budget in budgets:
            result = row["budgets"][budget]
            glyphs = result["synth_glyph_ids"]
            assert 0 < len(glyphs) <= artifact["row_capacity"]
            assert len(glyphs) == len(set(glyphs))
            assert all(rank_by_gid[glyph] < int(budget) for glyph in glyphs)
            current_mean = result["member_nearest_dist_mean"]
            current_max = result["member_nearest_dist_max"]
            if previous_mean is not None:
                assert current_mean <= previous_mean
                assert current_max <= previous_max
            previous_mean = current_mean
            previous_max = current_max


def test_v2_rule_keeps_authored_descriptor_targets_explicit() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    assert artifact["distinct_target_descriptor_count"] > 0
    for row in artifact["rows"]:
        assert 0 < row["distinct_authored_descriptor_count"] <= row["authored_count"]
        assert row["authored_dropped_no_descriptor"] == 0
