from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = REPO_ROOT / "scripts" / "check_actor_visual_payload_parity.py"
    spec = importlib.util.spec_from_file_location("payload_parity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_engine_coverage_table_is_complete() -> None:
    module = _load_module()
    assert len(module._engine_coverage_table()) == 256


def test_all_server_reachable_payloads_match_reviewed_full_xp_sources() -> None:
    module = _load_module()
    result = module.check_payload_parity()
    assert result["row_count"] == 192
    assert result["counts"] == {"pass": 192, "fail": 0}
    assert result["all_pass"] is True


def test_cross_source_overlay_owner_is_deleted_from_bindings() -> None:
    bindings = json.loads(
        (
            REPO_ROOT
            / "assets/actor_visual_profiles/source/profile_bindings.json"
        ).read_text(encoding="utf-8")
    )
    assert bindings["schema"] == "actor_visual_profile_source_bindings/v2"
    assert all("extension_overlay_xp" not in row for row in bindings["rows"])
    assert all(
        "extension_overlay_source_xp_id" not in row for row in bindings["rows"]
    )
    bee_attack_rows = [
        row
        for row in bindings["rows"]
        if row["composition_kind"]
        == "y9_extension_bee_attack_reuses_idle_xp"
    ]
    assert len(bee_attack_rows) == 8
    assert all(row["source_xp_id"].startswith("bigbee-") for row in bee_attack_rows)
