import hashlib
import json
from pathlib import Path

import pytest

from scripts.pipeline.compile_capture_module_mapping import (
    compile_mapping,
    partition_shards,
    skeleton_mapping_row,
)

BATCH_SCHEMA = "y92.capture_module_review_batch_receipt.v1"
MAPPING_DIR = Path(
    "artifacts/experiments/fl4813_capture_review_20260915/preserved"
    "/numabe_full_727_mapping_20260915"
)
M01_BATCH_RECEIPT = json.loads(
    (MAPPING_DIR / "review_receipts" / "old_001_049.batch_receipt.json").read_text(
        encoding="utf-8"
    )
)

OLD = Path("artifacts/experiments/fl4813_capture_review_20260915/annotations.json")
NEW = Path("artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/annotations.json")
TRIAGE = Path(
    "artifacts/experiments/fl4813_capture_review_20260915/preserved"
    "/manual_labels_population_triage_20260915.json"
)
ROLE_SETS = Path(
    "artifacts/experiments/fl4813_capture_review_20260915/preserved"
    "/numabe_full_727_mapping_20260915/site_input_role_sets.json"
)

OLD_HASH = "3687284f92edc25e63e42582ce1ae62abc2b51cefd95a769ba16c5d80ea92dd7"
NEW_HASH = "399348608f7878bf156d95ce57a7a0db9c3559118c4697705f64db049181ad47"


def _run(tmp_path: Path, out_dir: Path) -> dict:
    return compile_mapping(
        old_annotations_path=OLD,
        new_annotations_path=NEW,
        old_corpus_id="old",
        new_corpus_id="new",
        expected_hashes={"old": OLD_HASH, "new": NEW_HASH},
        triage_path=TRIAGE,
        role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
        max_images_per_thread=49,
        out_dir=out_dir,
    )


def test_ledger_conserves_727_observations_with_required_fields(tmp_path):
    receipt = _run(tmp_path, tmp_path)
    assert receipt["counts"] == {
        "observations": 727, "old": 499, "new": 228, "shards": 16,
    }
    rows = [
        json.loads(line)
        for line in (tmp_path / "observation_module_mapping.jsonl").read_text(
            encoding="utf-8").splitlines()
    ]
    assert len(rows) == 727
    required = {
        "corpus_id", "source_annotation_index", "annotation_id", "capture_id",
        "crop_path", "source_hash", "inherited_label", "proposed_population_role",
        "review_state", "provenance",
    }
    assert all(required.issubset(row) for row in rows)
    assert len({row["observation_uid"] for row in rows}) == 727


def test_shard_plan_matches_bounded_thread_layout(tmp_path):
    _run(tmp_path, tmp_path)
    shards = sorted((tmp_path / "shards").glob("*.jsonl"))
    assert len(shards) == 16
    total = sum(1 for shard in shards for _ in shard.read_text(
        encoding="utf-8").splitlines() if _.strip())
    assert total == 727
    old_counts = [1 for shard in shards if shard.name.startswith("old_")]
    new_counts = [1 for shard in shards if shard.name.startswith("new_")]
    assert len(old_counts) == 11
    assert len(new_counts) == 5
    for shard in shards:
        rows = shard.read_text(encoding="utf-8").splitlines()
        assert len(rows) <= 49


def test_scaffolds_stay_unresolved_with_no_module_assignment(tmp_path):
    _run(tmp_path, tmp_path)
    rows = [
        json.loads(line)
        for line in (tmp_path / "observation_module_mapping.jsonl").read_text(
            encoding="utf-8").splitlines()
    ]
    assert all(
        row["mapping"]["status"] == "pending_visual_review"
        and row["mapping"]["module_id"] is None
        and row["mapping"]["variant_id"] is None
        and row["mapping"]["unresolved_reason"]
        for row in rows
    )
    catalog = json.loads((tmp_path / "generic_module_catalog.json").read_text(
        encoding="utf-8"))
    assert catalog["status"] == "blocked_pending_visual_review"
    assert catalog["modules"] == []
    clusters = json.loads((tmp_path / "physical_instance_clusters.json").read_text(
        encoding="utf-8"))
    assert clusters["clusters"] == []
    queue = json.loads((tmp_path / "unresolved_mapping_queue.json").read_text(
        encoding="utf-8"))
    assert queue["unresolved_count"] == 727


def test_mapping_stays_closed_until_visual_review(tmp_path):
    receipt = _run(tmp_path, tmp_path)
    assert receipt["mapping_passed"] is False
    assert receipt["gates"]["no_world_ir_promotion"] is True
    assert receipt["gates"]["no_asset_generation"] is True
    assert receipt["gates"]["no_map_population"] is True
    assert receipt["gates"]["module_mapping_complete"] is False


def test_hash_mismatch_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="hash mismatch"):
        compile_mapping(
            old_annotations_path=OLD,
            new_annotations_path=NEW,
            old_corpus_id="old",
            new_corpus_id="new",
            expected_hashes={"old": "0" * 64, "new": NEW_HASH},
            triage_path=TRIAGE,
            role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
            max_images_per_thread=49,
            out_dir=tmp_path,
        )


def test_compilation_is_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    compile_mapping(
        old_annotations_path=OLD,
        new_annotations_path=NEW,
        old_corpus_id="old",
        new_corpus_id="new",
        expected_hashes={"old": OLD_HASH, "new": NEW_HASH},
        triage_path=TRIAGE,
        role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
        max_images_per_thread=49,
        out_dir=first,
    )
    compile_mapping(
        old_annotations_path=OLD,
        new_annotations_path=NEW,
        old_corpus_id="old",
        new_corpus_id="new",
        expected_hashes={"old": OLD_HASH, "new": NEW_HASH},
        triage_path=TRIAGE,
        role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
        max_images_per_thread=49,
        out_dir=second,
    )
    first_files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    second_files = sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file())
    assert first_files == second_files
    for relative in first_files:
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_partition_shards_never_exceeds_image_limit():
    rows = [
        {"corpus_id": "c", "source_annotation_index": index}
        for index in range(1, 101)
    ]
    shards = partition_shards(rows, max_images_per_thread=49)
    assert [shard["row_count"] for shard in shards] == [49, 49, 2]


def test_partition_shards_rejects_limit_above_muse_thread_cap():
    with pytest.raises(ValueError, match="between 1 and 49"):
        partition_shards(
            [{"corpus_id": "c", "source_annotation_index": 1}],
            max_images_per_thread=50,
        )


def _write_json(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _synth_annotation(prefix: str, index: int) -> dict:
    return {
        "annotation_id": f"{prefix}-{index:03d}",
        "capture_id": f"cap-{prefix}",
        "crop": {"path": f"crops/{prefix}-{index:03d}.png", "sha256": "ab" * 32},
        "label": "synth",
        "note": None,
        "review_state": "operator_authored",
    }


def _synth_inputs(tmp_path: Path, old_n: int = 49, new_n: int = 10) -> dict:
    old_path = _write_json(
        tmp_path / "old_annotations.json",
        {"annotations": [_synth_annotation("syn-old", i) for i in range(1, old_n + 1)]},
    )
    new_path = _write_json(
        tmp_path / "new_annotations.json",
        {"annotations": [_synth_annotation("syn-new", i) for i in range(1, new_n + 1)]},
    )
    return {
        "old": old_path,
        "new": new_path,
        "role_sets": {
            "old": {"default_role": "generic_fixture"},
            "new": {"default_role": "generic_fixture"},
        },
        "hashes": {
            "old": hashlib.sha256(old_path.read_bytes()).hexdigest(),
            "new": hashlib.sha256(new_path.read_bytes()).hexdigest(),
        },
    }


def _synth_compile(
    inputs: dict, out_dir: Path, receipts: list[dict] | None = None
) -> dict:
    return compile_mapping(
        old_annotations_path=inputs["old"],
        new_annotations_path=inputs["new"],
        old_corpus_id="old",
        new_corpus_id="new",
        expected_hashes=inputs["hashes"],
        triage_path=None,
        role_sets=inputs["role_sets"],
        max_images_per_thread=49,
        out_dir=out_dir,
        batch_receipts=receipts,
    )


def _mark_reviewed(shard_path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in shard_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        row["mapping"].update({
            "status": "reviewed_crop_inspected",
            "semantic_family": "street_fixture",
            "module_id": "generic.test.fixture",
            "variant_id": "base",
            "carrier_type": "test_fixture",
            "placement_context": "test_context",
            "evidence_summary": "Synthetic reviewed fixture used to verify the mapping interface.",
            "confidence": "high",
            "unresolved_reason": None,
        })
    shard_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    return rows


def _valid_receipt(
    *,
    shard_id: str,
    corpus: str,
    first: int,
    last: int,
    rows: list[dict],
    source_path: Path,
    source_hash: str,
    output_hash: str,
    **overrides,
) -> dict:
    input_payload = "".join(
        json.dumps(skeleton_mapping_row(row), ensure_ascii=False, sort_keys=True)
        + "\n"
        for row in rows
    ).encode("utf-8")
    receipt = {
        "schema": BATCH_SCHEMA,
        "thread_id": "test-thread-m01",
        "thread_label": "M01",
        "shard_id": shard_id,
        "assigned_corpus": corpus,
        "assigned_first_index": first,
        "assigned_last_index": last,
        "assigned_observation_range": f"{corpus} {first:03d}-{last:03d}",
        "expected_observation_uids": [row["observation_uid"] for row in rows],
        "expected_row_count": len(rows),
        "attached_image_count": len(rows),
        "maximum_image_limit": 49,
        "annotation_source_path": str(source_path),
        "annotation_source_sha256": source_hash,
        "input_shard_sha256": hashlib.sha256(input_payload).hexdigest(),
        "reviewed_output_shard_sha256": output_hash,
        "reviewer_identity": "test-reviewer",
        "model_identity": "unknown",
        "review_timestamp": "unknown",
        "starting_commit": "0" * 40,
        "producing_commit": "1" * 40,
        "review_method": "direct_crop_visual_inspection",
        "unresolved_provenance_fields": ["model_revision", "review_timestamp"],
        "provenance_status": "provenance_incomplete",
        "no_world_ir_promotion": True,
        "no_physical_registration": True,
        "no_generation": True,
    }
    receipt.update(overrides)
    return receipt


def _reviewed_synth(tmp_path: Path) -> tuple[dict, Path, list[dict], str]:
    inputs = _synth_inputs(tmp_path)
    out_dir = tmp_path / "out"
    _synth_compile(inputs, out_dir)
    shard_path = out_dir / "shards" / "old_001_049.jsonl"
    rows = _mark_reviewed(shard_path)
    output_hash = hashlib.sha256(shard_path.read_bytes()).hexdigest()
    return inputs, out_dir, rows, output_hash


def _base_receipt(rows: list[dict], output_hash: str, inputs: dict, **overrides) -> dict:
    return _valid_receipt(
        shard_id="old_001_049",
        corpus="old",
        first=1,
        last=49,
        rows=rows,
        source_path=inputs["old"],
        source_hash=inputs["hashes"]["old"],
        output_hash=output_hash,
        **overrides,
    )


def test_batch_receipt_valid_49_image_batch_accepted(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt = _synth_compile(
        inputs, out_dir, [_base_receipt(rows, output_hash, inputs)]
    )
    manifest = json.loads((out_dir / "mapping_manifest.json").read_text(
        encoding="utf-8"))
    thread = next(
        item for item in manifest["threads"] if item["shard_id"] == "old_001_049"
    )
    assert thread["status"] == "reviewed_with_provenance_incomplete_batch_receipt"
    assert manifest["counts"] == {
        "observations": 59, "old": 49, "new": 10, "shards": 2,
    }
    assert receipt["counts"] == manifest["counts"]
    assert receipt["reviewed_batches"] == ["old_001_049"]
    assert sum(receipt["reviewed_partition"].values()) == 49
    assert sum(receipt["current_full_partition"].values()) == 59
    assert receipt["mapping_passed"] is False


def test_batch_receipt_rejects_50_attached_images(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs, attached_image_count=50)
    with pytest.raises(ValueError, match="exceeds"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_missing_uid(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs)
    receipt_doc["expected_observation_uids"] = receipt_doc[
        "expected_observation_uids"
    ][1:]
    with pytest.raises(ValueError, match="row count"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_duplicate_uid(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs)
    uids = receipt_doc["expected_observation_uids"]
    receipt_doc["expected_observation_uids"] = [uids[0]] + uids
    with pytest.raises(ValueError, match="duplicate"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_uid_set_mismatch(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs)
    receipt_doc["expected_observation_uids"] = (
        receipt_doc["expected_observation_uids"][:-1] + ["old:001:ann-unknown"]
    )
    with pytest.raises(ValueError, match="differs from its assigned shard"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_wrong_range(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs, assigned_first_index=50)
    with pytest.raises(ValueError, match="wrong observation range"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_shard_hash_mismatch(tmp_path):
    inputs, out_dir, rows, _output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, "f" * 64, inputs)
    with pytest.raises(ValueError, match="reviewed output hash"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_input_shard_hash_mismatch(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(
        rows, output_hash, inputs, input_shard_sha256="f" * 64
    )
    with pytest.raises(ValueError, match="input shard hash"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_annotation_hash_mismatch(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs)
    receipt_doc["annotation_source_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="annotation source hash"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_receipt_rejects_annotation_path_mismatch(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(rows, output_hash, inputs)
    receipt_doc["annotation_source_path"] = str(tmp_path / "other.json")
    with pytest.raises(ValueError, match="annotation source path"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_reviewed_shard_without_receipt_rejected(tmp_path):
    inputs, out_dir, _rows, _output_hash = _reviewed_synth(tmp_path)
    with pytest.raises(ValueError, match="has no batch receipt"):
        _synth_compile(inputs, out_dir, [])


def test_batch_receipt_rejects_world_ir_promotion_claim(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(
        rows, output_hash, inputs, no_world_ir_promotion=False
    )
    with pytest.raises(ValueError, match="World IR promotion"):
        _synth_compile(inputs, out_dir, [receipt_doc])
    receipt_doc = _base_receipt(rows, output_hash, inputs, no_generation=False)
    with pytest.raises(ValueError, match="generation"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_reviewed_mapping_cannot_claim_operator_authority(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    rows[0]["mapping"]["population_role_authoritative"] = True
    shard_path = out_dir / "shards" / "old_001_049.jsonl"
    shard_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    receipt_doc = _base_receipt(
        rows,
        hashlib.sha256(shard_path.read_bytes()).hexdigest(),
        inputs,
    )
    with pytest.raises(ValueError, match="non-authoritative"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_incomplete_m01_provenance_retained_without_fabrication(tmp_path):
    assert M01_BATCH_RECEIPT["provenance_status"] == "provenance_incomplete"
    assert M01_BATCH_RECEIPT["model_identity"] == "unknown"
    assert M01_BATCH_RECEIPT["review_timestamp"] == "unknown"
    assert M01_BATCH_RECEIPT["unresolved_provenance_fields"]
    out_dir = tmp_path / "mapping"
    _run(tmp_path, out_dir)
    production_m01 = (
        MAPPING_DIR / "shards" / "old_001_049.jsonl"
    ).read_bytes()
    (out_dir / "shards" / "old_001_049.jsonl").write_bytes(production_m01)
    receipt = compile_mapping(
        old_annotations_path=OLD,
        new_annotations_path=NEW,
        old_corpus_id="old",
        new_corpus_id="new",
        expected_hashes={"old": OLD_HASH, "new": NEW_HASH},
        triage_path=TRIAGE,
        role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
        max_images_per_thread=49,
        out_dir=out_dir,
        batch_receipts=[M01_BATCH_RECEIPT],
    )
    assert receipt["reviewed_batches"] == ["old_001_049"]
    fabricated = dict(M01_BATCH_RECEIPT, provenance_status="provenance_complete")
    with pytest.raises(ValueError, match="complete provenance"):
        compile_mapping(
            old_annotations_path=OLD,
            new_annotations_path=NEW,
            old_corpus_id="old",
            new_corpus_id="new",
            expected_hashes={"old": OLD_HASH, "new": NEW_HASH},
            triage_path=TRIAGE,
            role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
            max_images_per_thread=49,
            out_dir=out_dir,
            batch_receipts=[fabricated],
        )


def test_complete_provenance_requires_producing_commit(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt_doc = _base_receipt(
        rows,
        output_hash,
        inputs,
        provenance_status="provenance_complete",
        unresolved_provenance_fields=[],
        model_identity="fixture-model",
        review_timestamp="2026-09-16T00:00:00Z",
        producing_commit=None,
    )
    with pytest.raises(ValueError, match="producing_commit is unknown"):
        _synth_compile(inputs, out_dir, [receipt_doc])


def test_batch_flow_conserves_727_499_228(tmp_path):
    out_dir = tmp_path / "mapping"
    _run(tmp_path, out_dir)
    production_m01 = (
        MAPPING_DIR / "shards" / "old_001_049.jsonl"
    ).read_bytes()
    (out_dir / "shards" / "old_001_049.jsonl").write_bytes(production_m01)
    receipt = compile_mapping(
        old_annotations_path=OLD,
        new_annotations_path=NEW,
        old_corpus_id="old",
        new_corpus_id="new",
        expected_hashes={"old": OLD_HASH, "new": NEW_HASH},
        triage_path=TRIAGE,
        role_sets=json.loads(ROLE_SETS.read_text(encoding="utf-8")),
        max_images_per_thread=49,
        out_dir=out_dir,
        batch_receipts=[M01_BATCH_RECEIPT],
    )
    assert receipt["counts"] == {
        "observations": 727, "old": 499, "new": 228, "shards": 16,
    }
    manifest = json.loads((out_dir / "mapping_manifest.json").read_text(
        encoding="utf-8"))
    assert manifest["counts"] == receipt["counts"]
    assert len(receipt["open_batches"]) == 15


def test_mapping_receipt_does_not_impersonate_step3_or_authorize_generation(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    receipt = _synth_compile(
        inputs, out_dir, [_base_receipt(rows, output_hash, inputs)]
    )
    assert receipt["mapping_passed"] is False
    assert "phase1_passed" not in receipt
    assert "step2_passed" not in receipt
    assert "step3_passed" not in receipt
    assert "generation_authorized" not in receipt
    assert receipt["gates"]["no_world_ir_promotion"] is True
    assert receipt["gates"]["no_asset_generation"] is True
    assert receipt["gates"]["no_map_population"] is True
    assert receipt["gates"]["mapping_passed"] is False


def test_all_reviewed_batches_compile_catalog_and_pass_mapping(tmp_path):
    inputs = _synth_inputs(tmp_path)
    out_dir = tmp_path / "out"
    _synth_compile(inputs, out_dir)
    receipts = []
    for shard_path in sorted((out_dir / "shards").glob("*.jsonl")):
        rows = _mark_reviewed(shard_path)
        corpus, first, last = shard_path.stem.split("_")
        receipts.append(_valid_receipt(
            shard_id=shard_path.stem,
            corpus=corpus,
            first=int(first),
            last=int(last),
            rows=rows,
            source_path=inputs[corpus],
            source_hash=inputs["hashes"][corpus],
            output_hash=hashlib.sha256(shard_path.read_bytes()).hexdigest(),
            thread_id=f"test-{shard_path.stem}",
        ))
    receipt = _synth_compile(inputs, out_dir, receipts)
    assert receipt["mapping_passed"] is True
    assert receipt["open_batches"] == []
    assert receipt["unresolved_mapping_count"] == 0
    catalog = json.loads((out_dir / "generic_module_catalog.json").read_text())
    assert catalog["complete"] is True
    assert catalog["modules"][0]["supporting_observation_ids"]


def test_batch_compilation_is_deterministic(tmp_path):
    inputs, out_dir, rows, output_hash = _reviewed_synth(tmp_path)
    _synth_compile(inputs, out_dir, [_base_receipt(rows, output_hash, inputs)])
    first_manifest = (out_dir / "mapping_manifest.json").read_bytes()
    first_receipt = (out_dir / "mapping_receipt.json").read_bytes()
    _synth_compile(inputs, out_dir, [_base_receipt(rows, output_hash, inputs)])
    assert (out_dir / "mapping_manifest.json").read_bytes() == first_manifest
    assert (out_dir / "mapping_receipt.json").read_bytes() == first_receipt
