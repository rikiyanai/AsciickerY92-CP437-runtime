"""Numabe survey storage metadata must not disable strict provenance checks."""
import dataclasses

import pytest

from scripts.pipeline.source_manifest import SourceRecord


def record(**extra):
    """A retained source still needs its original path, licence and SHA."""
    return SourceRecord.from_dict({
        "record_id": "survey", "provider": "test", "kind": "osm",
        "source_uri": "https://example.com/source", "licence": "ODbL-1.0",
        "retention": "retained", "artifact_path": "source.osm", "sha256": "a" * 64,
        **extra,
    })


def test_descriptive_storage_metadata_round_trips_without_operating_git():
    row = record(git_transport={"encoding": "none", "note": "Local raw source; no transport attempted"})
    assert not row.validate()
    assert SourceRecord.from_dict(row.to_dict()) == row
    assert dataclasses.asdict(row)["git_transport"]["encoding"] == "none"


def test_unknown_fields_and_unsupported_storage_instruction_still_fail():
    with pytest.raises(ValueError, match="unknown SourceRecord"):
        record(unrecognised="no")
    assert record(git_transport={"encoding": "other", "note": "unsupported"}).validate()
    assert record(git_transport={"encoding": "none", "note": "x", "command": "no"}).validate()
    assert record(git_transport={"encoding": "none", "note": "x"}, sha256=None).validate()


def test_legacy_imagery_bytes_are_retained_and_size_checked(tmp_path):
    from scripts.pipeline.source_manifest import SourceManifest, verify
    payload = tmp_path / "frame.jpg"
    payload.write_bytes(b"frame")
    import hashlib
    data = record(kind="street_imagery_frame", artifact_path="frame.jpg",
                  sha256=hashlib.sha256(b"frame").hexdigest(), bytes=5,
                  feature_ids=["feature:place:1"], page_uri="https://example.com/frame").to_dict()
    data.pop("retention")
    data.pop("source_uri")
    row = SourceRecord.from_dict(data)
    assert row.retention == "retained" and row.size_bytes == 5
    assert row.source_uri == row.page_uri
    assert "not retained" in row.parameters["source_uri_basis"]
    assert verify(SourceManifest("test", [row]), tmp_path)[0]["status"] == "OK"
    payload.write_bytes(b"wrong")
    assert verify(SourceManifest("test", [row]), tmp_path)[0]["status"] == "MISMATCH"
    with pytest.raises(ValueError, match="conflicting"):
        record(bytes=5, size_bytes=6)
    data["sha256"] = None
    with pytest.raises(ValueError, match="retained-byte evidence"):
        SourceRecord.from_dict(data)
