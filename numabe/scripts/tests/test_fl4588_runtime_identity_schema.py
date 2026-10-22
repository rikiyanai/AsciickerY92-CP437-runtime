# =============================================================
# test_fl4588_runtime_identity_schema.py  (FL-4588)
# =============================================================
# Focused schema fixtures for FL-4588 task 28's /api/v1/runtime_identity shape:
# the minimal authenticated read-only identity of the live packaged backend.
# backend_pid is the running process; source_commit + ui_registry_sha256 are
# provenance the endpoint reads from the packaged bin/asset_manifest.json and
# must never invent. These are lightweight schema fixtures (no native build).
# =============================================================
import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/asciiid.runtime_identity.v1.json"

H = "a" * 64


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid() -> dict:
    return {
        "schema": "asciiid.runtime_identity.v1",
        "backend_pid": 4321,
        "source_commit": "abcdef0",
        "source_dirty": False,
        "proof_eligible": True,
        "ui_registry_sha256": H,
    }


def test_runtime_identity_requires_pid_commit_and_registry_hash():
    schema = _schema()
    jsonschema.validate(_valid(), schema)
    for field in (
        "backend_pid", "source_commit", "source_dirty", "proof_eligible",
        "ui_registry_sha256",
    ):
        broken = copy.deepcopy(_valid())
        del broken[field]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(broken, schema)


def test_runtime_identity_rejects_bad_types_and_extra_fields():
    schema = _schema()
    # pid must be a positive integer.
    bad_pid = copy.deepcopy(_valid())
    bad_pid["backend_pid"] = 0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_pid, schema)
    # registry hash must be 64 lowercase hex.
    bad_hash = copy.deepcopy(_valid())
    bad_hash["ui_registry_sha256"] = "NOTAHASH"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_hash, schema)
    bad_dirty = copy.deepcopy(_valid())
    bad_dirty["source_dirty"] = "false"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_dirty, schema)
    bad_eligibility = copy.deepcopy(_valid())
    bad_eligibility["proof_eligible"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_eligibility, schema)
    inconsistent = copy.deepcopy(_valid())
    inconsistent["source_dirty"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(inconsistent, schema)
    dirty = copy.deepcopy(_valid())
    dirty["source_dirty"] = True
    dirty["proof_eligible"] = False
    jsonschema.validate(dirty, schema)
    # No invented provenance fields are allowed.
    extra = copy.deepcopy(_valid())
    extra["fixture_source"] = "invented"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra, schema)
