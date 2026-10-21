"""D3 offline corpus and confidence-calibration contracts."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/fl4260_admission_confidence.py"
CORPUS_PATH = ROOT / "assets/glyphs/fixtures/fl4260_admission_confidence_corpus.v1.json"
RECEIPT_PATH = ROOT / "assets/glyphs/fixtures/fl4260_admission_confidence_receipt.v1.json"

SPEC = importlib.util.spec_from_file_location("fl4260_admission_confidence", SCRIPT)
assert SPEC and SPEC.loader
D3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D3)


def corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text())


def resign(value: dict) -> dict:
    unsigned = dict(value)
    unsigned.pop("corpus_sha256", None)
    value["corpus_sha256"] = D3.sha256_json(unsigned)
    return value


def test_fixture_has_pinned_profile_qualified_corpus_hash() -> None:
    value = corpus()
    assert value["schema"] == D3.CORPUS_SCHEMA
    assert value["scorer_id"] == "u7.mask.v1"
    assert value["threshold"] == {"numerator": 15, "denominator": 100}
    assert D3.validate_corpus(value) is value
    assert value["materials"][0]["profile_namespace"] == "mesh"
    assert value["materials"][0]["material_key"] == 200


def test_exact_symmetric_confidence_and_quantile_are_rederivable() -> None:
    receipt = D3.compile_receipt(corpus())
    assert receipt["schema"] == "fl4260.admission_confidence.v1"
    assert receipt["runtime_admission_activated"] is False
    assert receipt["calibration_id"].endswith(receipt["corpus_sha256"])
    assert receipt["threshold"]["decimal"] == "0.150000"
    assert receipt["threshold_empirical_quantile"] == {
        "at_or_below": 2,
        "sample_count": 4,
        "numerator": 2,
        "denominator": 4,
        "decimal": "0.500000",
    }
    accepted = next(row for row in receipt["records"] if row["sample_id"].endswith("accepted_vertical"))
    rejected = next(row for row in receipt["records"] if row["sample_id"].endswith("rejected_cross"))
    assert accepted["d_accept"] == 0
    assert accepted["confidence"] == {"numerator": 1, "denominator": 1, "decimal": "1.000000"}
    assert accepted["admitted"] is True
    assert accepted["fail_closed_cause"] is None
    assert rejected["d_reject"] == 0
    assert rejected["confidence"]["numerator"] < 0
    assert rejected["admitted"] is False
    assert rejected["fail_closed_cause"] == "inversion"
    assert D3.validate_receipt(receipt) is receipt
    assert json.loads(RECEIPT_PATH.read_text()) == receipt


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda value: value["materials"][0].update({"accepted_exemplars": []}), "accept_set_empty"),
        (lambda value: value["materials"][0].update({"rejected_exemplars": []}), "reject_set_empty"),
    ],
)
def test_empty_exemplar_sets_fail_closed(mutation, reason: str) -> None:
    value = resign(copy.deepcopy(corpus()))
    mutation(value)
    resign(value)
    receipt = D3.compile_receipt(value)
    assert {row["fail_closed_cause"] for row in receipt["records"]} == {reason}
    assert all(row["admitted"] is False for row in receipt["records"])


def test_zero_denominator_and_inversion_are_distinguishable() -> None:
    zero = resign(copy.deepcopy(corpus()))
    target = zero["materials"][0]["accepted_exemplars"][0]
    zero["materials"][0]["rejected_exemplars"][0]["mask8_u64_le_hex"] = target["mask8_u64_le_hex"]
    zero["materials"][0]["rejected_exemplars"][0]["glyph_id"] = 603
    resign(zero)
    receipt = D3.compile_receipt(zero)
    row = next(item for item in receipt["records"] if item["sample_id"].endswith("accepted_vertical"))
    assert row["fail_closed_cause"] == "corpus_defect"
    assert row["confidence"] is None

    inversion = D3.compile_receipt(corpus())
    assert inversion["failure_state_counts"]["inversion"] == 2
    assert inversion["failure_state_counts"]["corpus_defect"] == 0


def test_positive_margin_below_threshold_remains_fail_closed() -> None:
    value = resign(copy.deepcopy(corpus()))
    # A partial cross lies exactly between the horizontal accepted exemplar and
    # the full-cross rejected exemplar under the same U7 lattice.
    horizontal = int.from_bytes(
        bytes.fromhex(value["materials"][0]["accepted_exemplars"][1]["mask8_u64_le_hex"]),
        "little",
    )
    row = None
    # Fixed candidate order makes this a deterministic calibration fixture,
    # while avoiding a hand-waved floating threshold example.
    for subset in range(1, 1 << 6):
        partial_cross = horizontal | sum(
            1 << (8 * y + 3) for y in range(1, 7) if subset & (1 << (y - 1))
        )
        sample = copy.deepcopy(value["calibration_samples"][0])
        sample["mask8_u64_le_hex"] = partial_cross.to_bytes(8, "little").hex()
        candidate = D3._record_for_sample(sample, value["materials"][0], value["threshold"])
        confidence = candidate["confidence"]
        if (
            confidence is not None
            and confidence["numerator"] >= 0
            and confidence["numerator"] * value["threshold"]["denominator"]
            < value["threshold"]["numerator"] * confidence["denominator"]
        ):
            row = candidate
            break
    assert row is not None
    assert row["confidence"]["numerator"] >= 0
    assert row["admitted"] is False
    assert row["fail_closed_cause"] == "confidence_below_threshold"


def test_hash_mismatch_and_runtime_activation_fail_closed() -> None:
    value = copy.deepcopy(corpus())
    value["corpus_sha256"] = "0" * 64
    with pytest.raises(D3.AdmissionConfidenceError, match="corpus_hash_mismatch"):
        D3.validate_corpus(value)

    receipt = D3.compile_receipt(corpus())
    receipt["runtime_admission_activated"] = True
    with pytest.raises(D3.AdmissionConfidenceError, match="runtime_activation_forbidden"):
        D3.validate_receipt(receipt)


def test_source_stays_offline_and_does_not_modify_u4_compiler_or_runtime() -> None:
    source = SCRIPT.read_text()
    compiler = (ROOT / "scripts/compile_fl4512_pools.py").read_text()
    resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
    assert "runtime_admission_activated\": False" in source
    assert "admission_confidence" not in compiler
    assert "admission_confidence" not in resolver
