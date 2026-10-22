from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from fl4588_ui_registry_identity import (
    RegistryIdentityError,
    canonical_registry_sha256,
    validate_registry_identity,
)


REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_authoritative_registry_body_matches_every_identity_stamp() -> None:
    registry = _registry()
    assert validate_registry_identity(registry) == registry["registry_sha256"]


def test_declared_hash_cannot_hide_changed_action_graph() -> None:
    registry = _registry()
    stale_declared = registry["registry_sha256"]
    registry["active_tool_graph"]["default_tool_id"] = "material_paint"
    assert canonical_registry_sha256(registry) != stale_declared
    with pytest.raises(RegistryIdentityError, match="registry_self_hash_mismatch"):
        validate_registry_identity(registry)


def test_child_stamp_and_counts_join_the_same_body() -> None:
    registry = _registry()
    bad_child = copy.deepcopy(registry)
    bad_child["interactions"][0]["registry_sha256"] = "0" * 64
    with pytest.raises(RegistryIdentityError, match="registry_interaction_hash_mismatch"):
        validate_registry_identity(bad_child)

    bad_count = copy.deepcopy(registry)
    bad_count["interaction_count"] += 1
    with pytest.raises(RegistryIdentityError, match="registry_interaction_count_mismatch"):
        validate_registry_identity(bad_count)
