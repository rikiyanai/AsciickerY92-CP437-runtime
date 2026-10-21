#!/usr/bin/env python3
"""Compile the offline D3 admission-confidence calibration receipt.

This is deliberately an offline authority.  It receives an authored,
content-hashed accepted/rejected exemplar corpus, evaluates the paper's
``u7.mask.v1`` distance exactly, and emits the byte-stable
``fl4260.admission_confidence.v1`` receipt.  It does *not* alter U4 rows,
candidate legality, the runtime selector, or presentation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import tempfile
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CORPUS_SCHEMA = "fl4260.admission_confidence_corpus.v1"
RECEIPT_SCHEMA = "fl4260.admission_confidence.v1"
SCORER_ID = "u7.mask.v1"

_U7_SPEC = importlib.util.spec_from_file_location(
    "fl4512_u7_mask", Path(__file__).with_name("fl4512_u7_mask.py")
)
if not _U7_SPEC or not _U7_SPEC.loader:
    raise RuntimeError("cannot load fl4512_u7_mask.py")
U7 = importlib.util.module_from_spec(_U7_SPEC)
_U7_SPEC.loader.exec_module(U7)


class AdmissionConfidenceError(ValueError):
    """Fail-closed D3 corpus or receipt construction error."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _expect_exact_keys(value: dict[str, Any], expected: set[str], address: str) -> None:
    if set(value) != expected:
        raise AdmissionConfidenceError("schema_invalid", f"{address}: fields")


def _parse_mask(value: Any, address: str) -> int:
    if not isinstance(value, str) or len(value) != 16:
        raise AdmissionConfidenceError("mask8_invalid", address)
    try:
        encoded = bytes.fromhex(value)
    except ValueError as exc:
        raise AdmissionConfidenceError("mask8_invalid", address) from exc
    if len(encoded) != 8 or value != encoded.hex():
        raise AdmissionConfidenceError("mask8_invalid", address)
    mask = int.from_bytes(encoded, "little")
    try:
        U7.descriptor(mask)
    except U7.U7MaskError as exc:
        raise AdmissionConfidenceError(exc.reason, address) from exc
    return mask


def _profile_key(value: dict[str, Any], address: str) -> tuple[str, int]:
    namespace = value.get("profile_namespace")
    material_key = value.get("material_key")
    if namespace not in {"terrain", "mesh"}:
        raise AdmissionConfidenceError("profile_key_invalid", address)
    if (
        not isinstance(material_key, int)
        or isinstance(material_key, bool)
        or not 0 <= material_key < 256
    ):
        raise AdmissionConfidenceError("profile_key_invalid", address)
    return namespace, material_key


def _parse_exemplars(value: Any, address: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AdmissionConfidenceError("exemplar_set_invalid", address)
    parsed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_glyph_ids: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise AdmissionConfidenceError("exemplar_set_invalid", f"{address}[{index}]")
        _expect_exact_keys(
            item,
            {"exemplar_id", "glyph_id", "mask8_u64_le_hex", "raster_provenance"},
            f"{address}[{index}]",
        )
        exemplar_id = item["exemplar_id"]
        glyph_id = item["glyph_id"]
        provenance = item["raster_provenance"]
        if (
            not isinstance(exemplar_id, str)
            or not exemplar_id
            or not isinstance(glyph_id, int)
            or isinstance(glyph_id, bool)
            or glyph_id <= 255
            or not isinstance(provenance, dict)
            or set(provenance) != {"page16_tile_sha256", "reduction_version", "source"}
            or not isinstance(provenance["page16_tile_sha256"], str)
            or len(provenance["page16_tile_sha256"]) != 64
            or provenance["reduction_version"] != U7.REDUCTION_VERSION
            or not isinstance(provenance["source"], str)
            or not provenance["source"]
            or exemplar_id in seen_ids
            or glyph_id in seen_glyph_ids
        ):
            raise AdmissionConfidenceError("exemplar_set_invalid", f"{address}[{index}]")
        _parse_mask(item["mask8_u64_le_hex"], f"{address}[{index}]")
        parsed.append(item)
        seen_ids.add(exemplar_id)
        seen_glyph_ids.add(glyph_id)
    if parsed != sorted(parsed, key=lambda row: (row["glyph_id"], row["exemplar_id"])):
        raise AdmissionConfidenceError("exemplar_order_invalid", address)
    return parsed


def validate_corpus(corpus: Any) -> dict[str, Any]:
    """Validate the authored corpus and return its canonical material table."""
    if not isinstance(corpus, dict):
        raise AdmissionConfidenceError("schema_invalid", "root")
    _expect_exact_keys(
        corpus,
        {"schema", "scorer_id", "threshold", "materials", "calibration_samples", "corpus_sha256"},
        "root",
    )
    if corpus["schema"] != CORPUS_SCHEMA or corpus["scorer_id"] != SCORER_ID:
        raise AdmissionConfidenceError("schema_invalid", "schema/scorer")
    threshold = corpus["threshold"]
    if (
        not isinstance(threshold, dict)
        or set(threshold) != {"numerator", "denominator"}
        or threshold["numerator"] != 15
        or threshold["denominator"] != 100
    ):
        raise AdmissionConfidenceError("threshold_invalid", repr(threshold))
    supplied_hash = corpus["corpus_sha256"]
    unsigned = dict(corpus)
    unsigned.pop("corpus_sha256")
    expected_hash = sha256_json(unsigned)
    if not isinstance(supplied_hash, str) or supplied_hash != expected_hash:
        raise AdmissionConfidenceError("corpus_hash_mismatch", expected_hash)

    materials = corpus["materials"]
    if not isinstance(materials, list) or not materials:
        raise AdmissionConfidenceError("material_set_invalid", "materials")
    parsed_materials: list[dict[str, Any]] = []
    seen_profiles: set[tuple[str, int]] = set()
    for index, material in enumerate(materials):
        if not isinstance(material, dict):
            raise AdmissionConfidenceError("material_set_invalid", str(index))
        _expect_exact_keys(
            material,
            {"profile_namespace", "material_key", "accepted_exemplars", "rejected_exemplars"},
            f"materials[{index}]",
        )
        key = _profile_key(material, f"materials[{index}]")
        if key in seen_profiles:
            raise AdmissionConfidenceError("duplicate_profile_key", repr(key))
        accepted = _parse_exemplars(material["accepted_exemplars"], f"materials[{index}].accepted")
        rejected = _parse_exemplars(material["rejected_exemplars"], f"materials[{index}].rejected")
        overlap = {row["glyph_id"] for row in accepted} & {row["glyph_id"] for row in rejected}
        if overlap:
            raise AdmissionConfidenceError("corpus_defect", f"overlapping_glyph_ids={sorted(overlap)}")
        parsed_materials.append(material)
        seen_profiles.add(key)
    if parsed_materials != sorted(
        parsed_materials, key=lambda row: (row["profile_namespace"], row["material_key"])
    ):
        raise AdmissionConfidenceError("material_order_invalid", "materials")

    samples = corpus["calibration_samples"]
    if not isinstance(samples, list) or not samples:
        raise AdmissionConfidenceError("calibration_samples_invalid", "calibration_samples")
    seen_sample_ids: set[str] = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise AdmissionConfidenceError("calibration_samples_invalid", str(index))
        _expect_exact_keys(
            sample,
            {"sample_id", "profile_namespace", "material_key", "mask8_u64_le_hex", "raster_provenance"},
            f"calibration_samples[{index}]",
        )
        sample_id = sample["sample_id"]
        if not isinstance(sample_id, str) or not sample_id or sample_id in seen_sample_ids:
            raise AdmissionConfidenceError("calibration_samples_invalid", f"id[{index}]")
        if _profile_key(sample, f"calibration_samples[{index}]") not in seen_profiles:
            raise AdmissionConfidenceError("calibration_profile_unknown", sample_id)
        _parse_mask(sample["mask8_u64_le_hex"], f"calibration_samples[{index}]")
        provenance = sample["raster_provenance"]
        if (
            not isinstance(provenance, dict)
            or set(provenance) != {"page16_tile_sha256", "reduction_version", "source"}
            or not isinstance(provenance["page16_tile_sha256"], str)
            or len(provenance["page16_tile_sha256"]) != 64
            or provenance["reduction_version"] != U7.REDUCTION_VERSION
            or not isinstance(provenance["source"], str)
            or not provenance["source"]
        ):
            raise AdmissionConfidenceError("calibration_samples_invalid", f"provenance[{index}]")
        seen_sample_ids.add(sample_id)
    if samples != sorted(samples, key=lambda row: row["sample_id"]):
        raise AdmissionConfidenceError("sample_order_invalid", "calibration_samples")
    return corpus


def _distance(target_mask: int, exemplars: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    if not exemplars:
        return None
    scored = []
    for exemplar in exemplars:
        mask = _parse_mask(exemplar["mask8_u64_le_hex"], exemplar["exemplar_id"])
        scored.append((U7.score_num(target_mask, mask)[0], exemplar["glyph_id"], exemplar["exemplar_id"], exemplar))
    score, _glyph_id, _exemplar_id, winner = min(scored)
    return score, winner


def _decimal_fraction(numerator: int, denominator: int) -> str:
    with localcontext() as context:
        context.prec = 28
        value = Decimal(numerator) / Decimal(denominator)
        return format(value.quantize(Decimal("0.000000"), rounding=ROUND_HALF_EVEN), "f")


def _record_for_sample(sample: dict[str, Any], material: dict[str, Any], threshold: dict[str, int]) -> dict[str, Any]:
    target_mask = _parse_mask(sample["mask8_u64_le_hex"], sample["sample_id"])
    accepted = _distance(target_mask, material["accepted_exemplars"])
    rejected = _distance(target_mask, material["rejected_exemplars"])
    cause: str | None = None
    d_accept: int | None = None
    d_reject: int | None = None
    accepted_id: str | None = None
    rejected_id: str | None = None
    numerator: int | None = None
    denominator: int | None = None
    admitted = False
    if accepted is None:
        cause = "accept_set_empty"
    elif rejected is None:
        cause = "reject_set_empty"
    else:
        d_accept, accepted_row = accepted
        d_reject, rejected_row = rejected
        accepted_id = accepted_row["exemplar_id"]
        rejected_id = rejected_row["exemplar_id"]
        numerator = d_reject - d_accept
        denominator = d_reject + d_accept
        if denominator == 0:
            cause = "corpus_defect"
        elif numerator < 0:
            cause = "inversion"
        else:
            admitted = numerator * threshold["denominator"] >= threshold["numerator"] * denominator
            if not admitted:
                cause = "confidence_below_threshold"
    confidence: dict[str, Any] | None = None
    if numerator is not None and denominator is not None and denominator > 0:
        divisor = math.gcd(abs(numerator), denominator)
        confidence = {
            "numerator": numerator // divisor,
            "denominator": denominator // divisor,
            "decimal": _decimal_fraction(numerator, denominator),
        }
    return {
        "sample_id": sample["sample_id"],
        "profile_namespace": sample["profile_namespace"],
        "material_key": sample["material_key"],
        "target_mask8_u64_le_hex": sample["mask8_u64_le_hex"],
        "raster_provenance": sample["raster_provenance"],
        "accepted_exemplar_id": accepted_id,
        "rejected_exemplar_id": rejected_id,
        "d_accept": d_accept,
        "d_reject": d_reject,
        "confidence": confidence,
        "admitted": admitted,
        "fail_closed_cause": cause,
    }


def compile_receipt(corpus: Any) -> dict[str, Any]:
    """Compile an exact D3 calibration receipt from a validated corpus."""
    corpus = validate_corpus(corpus)
    material_by_key = {
        (row["profile_namespace"], row["material_key"]): row
        for row in corpus["materials"]
    }
    records = [
        _record_for_sample(
            sample,
            material_by_key[(sample["profile_namespace"], sample["material_key"])],
            corpus["threshold"],
        )
        for sample in corpus["calibration_samples"]
    ]
    valid_confidences = [
        row["confidence"] for row in records if row["confidence"] is not None
    ]
    threshold = corpus["threshold"]
    at_or_below = sum(
        confidence["numerator"] * threshold["denominator"]
        <= threshold["numerator"] * confidence["denominator"]
        for confidence in valid_confidences
    )
    sample_count = len(valid_confidences)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "scorer_id": SCORER_ID,
        "corpus_sha256": corpus["corpus_sha256"],
        "calibration_id": f"{RECEIPT_SCHEMA}:{corpus['corpus_sha256']}",
        "threshold": {
            "numerator": threshold["numerator"],
            "denominator": threshold["denominator"],
            "decimal": _decimal_fraction(threshold["numerator"], threshold["denominator"]),
        },
        "threshold_empirical_quantile": {
            "at_or_below": at_or_below,
            "sample_count": sample_count,
            "numerator": at_or_below,
            "denominator": sample_count,
            "decimal": _decimal_fraction(at_or_below, sample_count) if sample_count else None,
        },
        "records": records,
        "failure_state_counts": {
            cause: sum(row["fail_closed_cause"] == cause for row in records)
            for cause in (
                "accept_set_empty",
                "reject_set_empty",
                "corpus_defect",
                "inversion",
                "confidence_below_threshold",
            )
        },
        "runtime_admission_activated": False,
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    return receipt


def validate_receipt(receipt: Any) -> dict[str, Any]:
    """Validate the receipt's self hash and all required D3 state fields."""
    if not isinstance(receipt, dict):
        raise AdmissionConfidenceError("receipt_schema_invalid", "root")
    required = {
        "schema", "scorer_id", "corpus_sha256", "calibration_id", "threshold",
        "threshold_empirical_quantile", "records", "failure_state_counts",
        "runtime_admission_activated", "receipt_sha256",
    }
    _expect_exact_keys(receipt, required, "receipt")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["scorer_id"] != SCORER_ID:
        raise AdmissionConfidenceError("receipt_schema_invalid", "schema/scorer")
    if receipt["runtime_admission_activated"] is not False:
        raise AdmissionConfidenceError("runtime_activation_forbidden")
    supplied = receipt["receipt_sha256"]
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256")
    expected = sha256_json(unsigned)
    if supplied != expected:
        raise AdmissionConfidenceError("receipt_hash_mismatch", expected)
    return receipt


def _read_json(path_text: str) -> Any:
    return json.loads(sys_stdin_bytes() if path_text == "-" else Path(path_text).read_bytes())


def sys_stdin_bytes() -> bytes:
    import sys
    return sys.stdin.buffer.read()


def _write_json(path_text: str, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if path_text == "-":
        print(encoded, end="")
        return
    output = Path(path_text)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out")
    parser.add_argument("--verify-receipt")
    args = parser.parse_args()
    try:
        if args.verify_receipt:
            validate_receipt(_read_json(args.verify_receipt))
            print(f"FL4260_D3_RECEIPT_PASS receipt={args.verify_receipt}")
            return 0
        if not args.out:
            parser.error("--out is required unless --verify-receipt is used")
        receipt = compile_receipt(_read_json(args.corpus))
        _write_json(args.out, receipt)
        print(
            f"FL4260_D3_COMPILE_PASS records={len(receipt['records'])} "
            f"calibration_id={receipt['calibration_id']} out={args.out}"
        )
        return 0
    except (AdmissionConfidenceError, OSError, json.JSONDecodeError) as exc:
        print(f"FL4260_D3_COMPILE_REJECT {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
