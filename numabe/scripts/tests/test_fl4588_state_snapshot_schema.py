# =============================================================
# test_fl4588_state_snapshot_schema.py  (FL-4588 task 44)
# =============================================================
# The asciiid.state_snapshot.v1 contract is the COMPLETE counterfactual input
# both evaluators consume. Task 44 restored it after a narrowed health-only owner
# had dropped evaluator inputs. These lightweight fixtures confirm the restored
# schema still admits the full evaluator shape and still requires every deep
# component (paper identity, document, profiles, compiled pool, glyph catalog,
# renderer, temporal history, artifact hashes, editor session).
# =============================================================
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/asciiid.state_snapshot.v1.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_restored_contract_requires_every_evaluator_component():
    required = set(_schema()["required"])
    # The full evaluator input set — the narrowed owner had removed these.
    for field in (
        "paper_model_sha256", "document", "editor_session", "profiles",
        "compiled_pool", "glyph_catalog", "renderer", "temporal_history",
        "artifact_hashes", "state_revision", "source_commit",
        "ui_registry_sha256", "snapshot_sha256",
    ):
        assert field in required, f"restored contract must require {field}"


def test_editor_session_owns_active_material_not_document():
    schema = _schema()
    session_req = set(schema["$defs"]["editor_session"]["required"])
    assert "active_material" in session_req
    # active_material is session state, never an authored-document field.
    assert "active_material" not in schema["$defs"]["document"]["properties"]


def test_a_golden_snapshot_validates_against_the_restored_schema():
    # The generated goldens carry a complete state_snapshot.v1; validating one
    # confirms the restored schema matches the shape both evaluators consume.
    schema = _schema()
    goldens = sorted((ROOT / "assets/asciiid/counterfactual").glob("*.golden.json"))
    if not goldens:
        pytest.skip("no counterfactual goldens present")
    validated = 0
    for path in goldens:
        snap = json.loads(path.read_text(encoding="utf-8")).get("snapshot")
        if not snap:
            continue
        jsonschema.validate(snap, schema)
        validated += 1
    assert validated >= 1, "no golden carried a state_snapshot to validate"
