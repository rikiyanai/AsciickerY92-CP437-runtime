#!/usr/bin/env python3
"""FL-4162 step 3 — promotion to compiler-owned layer_roles is fail-closed."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import json

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_promoter():
    path = REPO_ROOT / "scripts" / "promote_entries_to_layer_roles.py"
    spec = importlib.util.spec_from_file_location("promote_entries_to_layer_roles", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_player_1000_promotes_two_per_role_layers():
    p = load_promoter()
    doc = p.build_layer_roles(["player-1000"])
    assert doc["migrated_source_ids"] == ["player-1000"]
    profile = doc["profiles"]["player-1000"]
    layers = profile["layers"]
    assert [l["layer_index"] for l in layers] == [2, 3]          # sorted
    assert [l["role"] for l in layers] == ["player_body", "player_armor_regular"]
    assert all(l["source_card_fingerprint"] for l in layers)     # provenance per layer
    # Step 4: topology truth baked for the compiler to enforce against.
    assert all(l["topology_class"] == "owned" for l in layers)
    assert profile["expected_visible_layer_indices"] == [2, 3]


def test_promoted_file_is_not_runtime_authoritative():
    p = load_promoter()
    doc = p.build_layer_roles(["player-1000"])
    assert doc["authority"] is False
    assert doc["runtime_authoritative"] is False
    assert doc["status"] == "promoted_layout_not_runtime_authoritative"
    prov = doc["provenance"]
    assert prov["state_final_sha256"] == "ecc9a16112ce48beaeb0e24beba2ccc7399c4efc50d32505f3fd54f8e8d76020"
    assert prov["entries_sha256"] and prov["authorability_report_sha256"]


def test_refuses_variant_with_blocked_sibling():
    p = load_promoter()
    # player-0100's L3 carries a role_name_conflict -> variant not profile_complete.
    with pytest.raises(p.PromotionError, match="not profile_complete"):
        p.build_layer_roles(["player-0100"])


def test_refuses_unknown_variant():
    p = load_promoter()
    with pytest.raises(p.PromotionError, match="not profile_complete"):
        p.build_layer_roles(["does-not-exist"])


def test_missing_input_fails_closed(tmp_path):
    p = load_promoter()
    with pytest.raises(p.PromotionError, match="required entries missing"):
        p.build_layer_roles(["player-1000"], entries_path=tmp_path / "nope.json")


def test_every_promoted_layer_is_content_clean_in_report():
    """The source file can only contain layers the authorability report calls clean."""
    p = load_promoter()
    import json
    report = json.loads((REPO_ROOT / "docs/research/ascii/semantic_maps"
                         / "compiler_authorability_report.json").read_text())
    clean = {l["card_id"] for l in report["layers"] if l["content_status"] == "content_clean"}
    doc = p.build_layer_roles(["player-1000"])
    for layer in doc["profiles"]["player-1000"]["layers"]:
        assert layer["source_key"] in clean


def test_player_1001_armor_role_normalized_at_promotion_boundary():
    """FL-4162 Tier D: the bare reviewed role `armor` promotes as the normalized
    contract role, with the original hand label preserved as provenance. The
    evidence is never edited — normalization happens only at the promotion edge."""
    p = load_promoter()
    doc = p.build_layer_roles(["player-1001"])
    layers = doc["profiles"]["player-1001"]["layers"]
    l3 = next(l for l in layers if l["layer_index"] == 3)
    assert l3["role"] == "player_armor_regular"
    assert l3["original_reviewed_role"] == "armor"
    assert l3["role_normalized_at_promotion"] is True
    # The non-normalized sibling carries no provenance keys.
    l2 = next(l for l in layers if l["layer_index"] == 2)
    assert "original_reviewed_role" not in l2
    assert "role_normalized_at_promotion" not in l2


def test_normalization_fails_closed_when_original_role_changes(monkeypatch):
    """If the hand label no longer matches the rule's `from`, promotion must
    refuse rather than silently renormalize a different role (Law 6)."""
    p = load_promoter()
    monkeypatch.setitem(
        p.ROLE_NORMALIZATION, ("player-1001", 3),
        {"from": "not_the_real_label", "to": "player_armor_regular"},
    )
    with pytest.raises(p.PromotionError, match="normalization rule expected"):
        p.build_layer_roles(["player-1001"])


def test_false_clean_l2_player_weapon_promotes_as_composite_contract_role():
    p = load_promoter()
    doc = p.build_layer_roles(["player-0001", "player-1001"])
    for variant in ("player-0001", "player-1001"):
        l2 = next(l for l in doc["profiles"][variant]["layers"] if l["layer_index"] == 2)
        assert l2["role"] == "player_body;player_weapon_sword"
        assert l2["topology_class"] == "owned"
        assert l2["composite_owned_at_contract"] is True
        assert l2["original_composite_roles"] == ["player_body", "player_weapon_sword"]


def test_false_clean_l2_player_shield_promotes_as_composite_contract_role():
    p = load_promoter()
    doc = p.build_layer_roles(["player-0010"])
    l2 = doc["profiles"]["player-0010"]["layers"][0]
    assert l2["role"] == "player_body;player_shield_regular"
    assert l2["topology_class"] == "owned"
    assert l2["composite_owned_at_contract"] is True
    assert l2["original_composite_roles"] == ["player_body", "player_shield_regular"]


def test_false_clean_l2_wolfie_weapon_promotes_as_mount_rider_composite():
    p = load_promoter()
    doc = p.build_layer_roles(["wolfie-0001"])
    l2 = doc["profiles"]["wolfie-0001"]["layers"][0]
    assert l2["role"] == "mount_body_wolf;rider_torso;sword"
    assert l2["topology_class"] == "owned"
    assert l2["composite_owned_at_contract"] is True
    assert l2["original_composite_roles"] == ["mount_body_wolf", "rider_torso", "sword"]


def test_bare_l2_equipment_role_fails_closed(tmp_path):
    p = load_promoter()
    entries = json.loads((REPO_ROOT / "docs/research/ascii/semantic_maps"
                          / "actor_visual_profile_entries.json").read_text())
    for entry in entries["authored_entries"]:
        if entry["source_key"] == "player-0001-L2":
            entry["layer"]["role"] = "player_weapon_sword"
            entry["layer"].pop("composite_owned_at_contract", None)
            entry["layer"].pop("original_composite_roles", None)
    entries_path = tmp_path / "entries.json"
    entries_path.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(p.PromotionError, match="L2 base accumulator cannot promote"):
        p.build_layer_roles(["player-0001"], entries_path=entries_path)
