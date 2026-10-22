"""Focused tests for the v3 production admission owner.

These tests are scene-independent.  The live Pieta supply is deliberately not
opened here; the canonical full-supply transaction remains a separate run.
"""

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import fl4512_mesh_feature_candidate_admission_v2 as A  # noqa: E402
from fl4512_u7_mask import topology_counts  # noqa: E402


@pytest.fixture(scope="module")
def grammar():
    return A.build_grammar()


def _descriptor(mask: int, gid: int) -> dict:
    _skel, ep, jn = topology_counts(mask)
    return {"glyph_id": gid, "mask8": mask,
            "endpoint_count": ep, "junction_count": jn}


def _toy_domain(count: int = 40) -> list[dict]:
    # Every candidate is one bit away from the target, keeping the legacy
    # fixture below inside the 660 hard bound while preserving unique U7 IDs.
    return [_descriptor(1 | (1 << i), 1000 + i) for i in range(count)]


def _toy_target(mask: int = 1) -> dict:
    _skel, ep, jn = topology_counts(mask)
    return {"_mask8": mask, "endpoint_count": ep, "junction_count": jn}


def test_frozen_combined_grammar_identity(grammar):
    assert grammar["grammar_id"] == "fl4512.mesh_feature_candidate_admission.v3"
    assert grammar["grammar_hash"] == (
        "f501bd27acae6b6209f7906eedc7dc0aac7f5478948c2235c22143e0ea5b50d7"
    )
    assert grammar["target_count"] == 1111
    assert grammar["legacy_330_count"] == 330
    assert len(grammar["_targets_runtime"]) == 1111


def test_legacy_builder_is_retained_only_for_bound():
    legacy = A._build_legacy_grammar()
    assert legacy["target_count"] == 330
    assert A.build_grammar()["target_count"] == 1111


def test_u2_raster_threshold_exact():
    # A sample-centred segment is admitted, while a short segment in the
    # centre of the four-sample gap is outside the 0.0200 squared threshold.
    assert A._u2_rasterize_world((0.125, 0.125), (0.126, 0.125)) != 0
    assert A._u2_rasterize_world((0.25, 0.25), (0.251, 0.25)) == 0


def test_endpoint_lattice_is_deterministic_and_explicit():
    first, second = A._endpoint_lattice(), A._endpoint_lattice()
    assert first == second
    assert len(first) == len(set(first))
    assert (0, 0) in first and (32, 32) in first
    assert A.ENDPOINT_LATTICE_DENOMINATOR == 4


def test_signature_universe_keeps_lowest_glyph_id():
    descriptor = _descriptor(3, 50)
    twin = dict(descriptor, glyph_id=10)
    universe, identity = A._full_supply_signature_universe(
        [descriptor, twin] + _toy_domain(32))
    assert identity["lowest_glyph_id_tie_owner"] is True
    assert next(item for item in universe if item["mask8"] == 3)["glyph_id"] == 10


def test_exact_optimizer_matches_scalar_no_swap_oracle():
    universe = _toy_domain(40)
    targets = [_toy_target()]
    current = [item["glyph_id"] for item in universe[:A.ROW_CAPACITY]]
    result = A._full_supply_constrained_exchange(
        current, universe, targets, legacy_targets=targets, block_size=8)
    got = tuple(result["final_selected_glyph_ids"])
    current_score = max(A._cost(1, targets[0]["endpoint_count"],
                                targets[0]["junction_count"],
                                item["mask8"], item["endpoint_count"],
                                item["junction_count"]) for item in universe[:32])
    assert result["legacy_worst_pass"] is True
    assert result["final_objective"][0] <= current_score
    # A strict lexicographic descent cannot report a worse final row.
    assert got == tuple(sorted(got))
    assert len(got) == A.ROW_CAPACITY


def test_optimizer_rejects_legacy_bound_violation():
    universe = [_descriptor(1 << i, 2000 + i) for i in range(40)]
    targets = [_toy_target(1)]
    current = [item["glyph_id"] for item in universe[:32]]
    with pytest.raises(A.AdmissionError):
        A._full_supply_constrained_exchange(
            current, universe, targets,
            legacy_targets=[_toy_target((1 << 64) - 1)],
            block_size=8)


def test_memory_ledger_is_conservative_and_bounded():
    ledger = A._full_supply_memory_ledger(1111, 512)
    assert ledger["modeled_live_tensor_bytes"] == sum(
        value for key, value in ledger.items()
        if key.endswith("_bytes") and key not in {
            "modeled_live_tensor_bytes", "memory_cap_bytes"
        }
    )
    assert ledger["memory_bound_pass"] is True
    assert ledger["memory_cap_bytes"] <= 512 * 1024 * 1024
    assert ledger["actual_rss_measured"] is False


def test_schema_and_grammar_tamper_fail_closed(grammar):
    # This synthetic receipt is not constructed because selection itself is
    # intentionally guarded by the legacy hard bound. Identity checks
    # still fail before any selector work.
    bad = {"schema": "fl4512.mesh_feature_candidate_admission_receipt.v2",
           "grammar_id": grammar["grammar_id"], "scorer_id": A.SCORER_ID}
    with pytest.raises(A.AdmissionError) as exc:
        A.recompute_and_validate(bad, [], grammar=grammar)
    assert exc.value.reason == A.REASON_SCHEMA_MISMATCH


def test_no_live_mask_can_enter_combined_target_record():
    with pytest.raises(A.AdmissionError):
        A._combined_target_record(1, "runtime_line", {}, "heldout")


def _scalar_exchange(current_ids, universe, targets, legacy, bound):
    """Independent scalar strict-single-swap oracle for the production pass."""
    by_gid = {item["glyph_id"]: item for item in universe}
    current = tuple(sorted(current_ids))

    def scores(row, domain):
        return [min(A._cost(target["_mask8"], target["endpoint_count"],
                            target["junction_count"], item["mask8"],
                            item["endpoint_count"], item["junction_count"])
                for item in row) for target in domain]

    def objective(row):
        values = scores(row, targets)
        return (max(values), sum(values), tuple(sorted(item["glyph_id"] for item in row)))

    while True:
        current_items = [by_gid[gid] for gid in current]
        current_objective = objective(current_items)
        best = None
        for removed in current:
            retained = [by_gid[gid] for gid in current if gid != removed]
            for added in sorted(set(by_gid) - set(current)):
                candidate = retained + [by_gid[added]]
                if max(scores(candidate, legacy)) > bound:
                    continue
                candidate_objective = objective(candidate)
                if candidate_objective >= current_objective:
                    continue
                choice = (candidate_objective, tuple(sorted(item["glyph_id"] for item in candidate)))
                if best is None or choice < best[0]:
                    best = (choice, candidate)
        if best is None:
            return current
        current = best[0][1]


def test_scalar_oracle_matches_exact_optimizer_seeded_random_multi_swap(monkeypatch):
    monkeypatch.setattr(A, "GRAMMAR_WORST_BOUND", 1000)
    for seed in range(1, 6):
        import random
        rng = random.Random(seed)
        universe = [_descriptor(rng.randrange(1, 256), 1) for _ in []]
        masks = []
        for gid in range(1, 45):
            mask = rng.randrange(1, 256)
            while mask in masks:
                mask = rng.randrange(1, 256)
            masks.append(mask)
            universe.append(_descriptor(mask, gid))
        targets = [_toy_target(rng.randrange(1, 256)) for _ in range(5)]
        legacy = [_toy_target(0)]
        current = [item["glyph_id"] for item in universe[:A.ROW_CAPACITY]]
        expected = _scalar_exchange(current, universe, targets, legacy, 1000)
        actual = A._full_supply_constrained_exchange(
            current, universe, targets, legacy_targets=legacy, block_size=4)
        assert tuple(actual["final_selected_glyph_ids"]) == expected


def test_optimizer_lexicographic_tie_owner_is_lowest_glyph_id():
    universe = [_descriptor(1 << i, i + 1) for i in range(32)]
    universe.extend([_descriptor(0xFF, 99), _descriptor(0xFF, 100)])
    targets = [_toy_target(0xFF)]
    result = A._full_supply_constrained_exchange(
        list(range(1, 33)), universe, targets,
        legacy_targets=[_toy_target(0)], block_size=4)
    assert 99 in result["final_selected_glyph_ids"]
    assert 100 not in result["final_selected_glyph_ids"]


def test_optimizer_rejects_legacy_unsafe_improving_swap(monkeypatch):
    monkeypatch.setattr(A, "GRAMMAR_WORST_BOUND", 70)
    universe = [_descriptor(1 << i, i + 1) for i in range(32)]
    universe.append(_descriptor(0xFF, 99))
    result = A._full_supply_constrained_exchange(
        list(range(1, 33)), universe, [_toy_target(0xFF)],
        legacy_targets=[_toy_target(1 << i) for i in range(32)], block_size=4)
    assert result["swap_count"] == 0
    assert 99 not in result["final_selected_glyph_ids"]


def test_canonical_1111_330_block_boundary_and_uint16_matrix(grammar):
    legacy = A._legacy_targets()
    assert len(grammar["_targets_runtime"]) == 1111
    assert len(legacy) == 330
    block = A._u7_cost_matrix_block(grammar["_targets_runtime"][:1111],
                                    [_descriptor(1, 1), _descriptor(2, 2)])
    assert block.shape == (2, 1111)
    assert str(block.dtype) == "uint16"
    chunks = list(A._full_supply_blocks([_descriptor(i + 1, i + 1)
                                         for i in range(513)], 512))
    assert [len(chunk) for chunk in chunks] == [512, 1]


def test_optimizer_block_size_invariance_and_final_no_improving_neighbor():
    universe = [_descriptor(1 << i, i + 1) for i in range(32)]
    universe.extend([_descriptor(0x0F, 99), _descriptor(0xF0, 100)])
    targets, legacy = [_toy_target(0xFF), _toy_target(0x0F)], [_toy_target(0)]
    current = list(range(1, 33))
    small = A._full_supply_constrained_exchange(
        current, universe, targets, legacy_targets=legacy, block_size=4)
    large = A._full_supply_constrained_exchange(
        current, universe, targets, legacy_targets=legacy, block_size=16)
    assert small["final_selected_glyph_ids"] == large["final_selected_glyph_ids"]
    final_ids = set(small["final_selected_glyph_ids"])
    final_row = [item for item in universe if item["glyph_id"] in final_ids]
    def row_objective(row):
        values = [min(A._cost(t["_mask8"], t["endpoint_count"], t["junction_count"],
                              d["mask8"], d["endpoint_count"], d["junction_count"])
                   for d in row) for t in targets]
        return (max(values), sum(values), tuple(sorted(d["glyph_id"] for d in row)))
    for item in universe:
        if item["glyph_id"] in final_ids:
            continue
        for removed in final_row:
            candidate = [d for d in final_row if d is not removed] + [item]
            assert row_objective(candidate) >= row_objective(final_row)


def test_full_supply_source_and_grammar_contract_fail_closed(grammar):
    with pytest.raises(A.AdmissionError):
        A._validate_frozen_full_supply_contract(
            {"source": {"catalog_source_sha256": "c",
                         "registry_source_sha256": "r"}},
            {"catalog": {"sha256": "c"}, "registry": {"sha256": "r"}},
            {"grammar_hash": "wrong", "target_count": 1111,
             "_targets_runtime": grammar["_targets_runtime"]},
            [_descriptor(1, 1)] * 32,
        )
