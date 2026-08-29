#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_fl4131_material_morphology as validator  # noqa: E402


def test_fl4131_material_morphology_generated_artifacts_validate() -> None:
    errors, counts = validator.validate()

    assert errors == []
    assert counts["candidate_inventory_rows"] >= 1000
    # After v2 atlas bake the receipts file carries 159 v1 + 1021 v2 = 1180 rows.
    assert counts["shape_receipt_rows"] >= 159
    assert counts["profile_cells"] == 544
    assert counts["ui_required_fields"] >= 33


def test_fl4131_material_morphology_scripts_are_executable() -> None:
    for script_name in [
        "generate_fl4131_material_morphology.py",
        "validate_fl4131_material_morphology.py",
    ]:
        path = SCRIPTS / script_name
        assert path.exists()
        assert os.access(path, os.X_OK)
