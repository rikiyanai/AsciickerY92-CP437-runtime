#!/usr/bin/env python3
"""FL-4162 step 3 — compiler consumes reviewed layer_roles for migrated keys only."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# The compiler imports sibling modules (compile_glyph_manifest); ensure scripts/ is
# importable regardless of how pytest is invoked.
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))


def load_compiler():
    path = REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py"
    spec = importlib.util.spec_from_file_location("compile_actor_visual_profiles", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._LAYER_ROLES_CACHE = None  # reset cross-test
    return module


def _key_for(c, family, ahsw):
    for k in c._enumerate_server_reachable_keys():
        if c._resolve_source_xp(k)[:2] == (family, ahsw):
            return k
    raise AssertionError(f"no reachable key for {family}-{ahsw}")


def test_migrated_key_uses_reviewed_per_role_layers():
    c = load_compiler()
    profile, _ = c._synthetic_profile_for_key(_key_for(c, "player", "1000"))
    shape = [(l["role"], l["layer_index"], l["merge_extra_layers"]) for l in profile["layers"]]
    assert shape == [("player_body", 2, False), ("player_armor_regular", 3, False)]


def test_unmigrated_key_keeps_legacy_single_body_owner():
    c = load_compiler()
    profile, _ = c._synthetic_profile_for_key(_key_for(c, "player", "0100"))
    shape = [(l["role"], l["layer_index"], l["merge_extra_layers"]) for l in profile["layers"]]
    assert shape == [("body", 2, True)]


def test_one_owner_per_key_no_shadow():
    """A migrated source_id never carries the legacy body/merge block."""
    c = load_compiler()
    profile, _ = c._synthetic_profile_for_key(_key_for(c, "player", "1000"))
    assert not any(l["merge_extra_layers"] for l in profile["layers"])
    assert all(l["role"] != "body" for l in profile["layers"])


def test_migrated_mismatch_fails_closed():
    c = load_compiler()
    c._LAYER_ROLES_CACHE = None
    # migrated_source_ids vs profiles disagree -> _migrated_layer_roles must fail.
    import json
    orig = c.LAYER_ROLES_SOURCE
    bad = REPO_ROOT / "docs/research/ascii/semantic_maps" / "_test_bad_layer_roles.json"
    bad.write_text(json.dumps({"migrated_source_ids": ["a", "b"], "profiles": {"a": {}}}))
    try:
        c.LAYER_ROLES_SOURCE = bad
        c._LAYER_ROLES_CACHE = None
        with pytest.raises(SystemExit, match="migrated_source_ids"):
            c._migrated_layer_roles()
    finally:
        c.LAYER_ROLES_SOURCE = orig
        c._LAYER_ROLES_CACHE = None
        bad.unlink(missing_ok=True)


def test_reviewed_layer_index_out_of_range_fails_closed():
    c = load_compiler()
    c._LAYER_ROLES_CACHE = {"player-1000": {"layers": [
        {"layer_index": 99, "role": "player_body", "topology_class": "owned"}]}}
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/player-1000.xp")
    with pytest.raises(SystemExit, match="absent from"):
        c._profile_layers("player-1000", "assets/sprites/player-1000.xp", sprite,
                          list(range(sprite.atlas_frames)))
    c._LAYER_ROLES_CACHE = None


def test_non_owned_topology_class_fails_closed():
    """Step 4: a composite/unresolved layer must never compile as owned source."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/player-1000.xp")
    c._LAYER_ROLES_CACHE = {"player-1000": {
        "layers": [{"layer_index": 2, "role": "player_body", "topology_class": "composite"}],
        "expected_visible_layer_indices": [2]}}
    with pytest.raises(SystemExit, match="is not 'owned'"):
        c._profile_layers("player-1000", "assets/sprites/player-1000.xp", sprite,
                          list(range(sprite.atlas_frames)))
    c._LAYER_ROLES_CACHE = None


def test_expected_visible_layer_mismatch_fails_closed():
    """Step 4: owned layers must equal the baked visible-layer set (no unowned layer)."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/player-1000.xp")
    c._LAYER_ROLES_CACHE = {"player-1000": {
        "layers": [{"layer_index": 2, "role": "player_body", "topology_class": "owned"}],
        "expected_visible_layer_indices": [2, 3]}}  # claims L3 visible but doesn't own it
    with pytest.raises(SystemExit, match="unowned visible layer"):
        c._profile_layers("player-1000", "assets/sprites/player-1000.xp", sprite,
                          list(range(sprite.atlas_frames)))
    c._LAYER_ROLES_CACHE = None


def test_legacy_stub_asset_fails_closed():
    """Step 4 universal gate: a non-migrated XP without layer 2 is a stub/split asset."""
    import types
    c = load_compiler()
    c._LAYER_ROLES_CACHE = {}  # nothing migrated -> legacy path
    stub = types.SimpleNamespace(layers=[0, 1], anim_lens=[1])  # only 2 layers
    with pytest.raises(SystemExit, match="stub/split asset"):
        c._profile_layers("player-9999", "assets/sprites/player-9999.xp", stub, [0])
    c._LAYER_ROLES_CACHE = None


# --- FL-4162 Branch A: explicit swoosh composition (upstream composition semantics) ---
# attack-0001 is the first composite-owned variant whose reviewed stack pairs a
# body/weapon composite at L2 with a weapon_swoosh overlay at the final layer. The
# legacy owner folded that cyan-fg swoosh INTO the body; the explicit per-role path
# must reproduce that fold (NOT via merge_extra_layers) so output stays byte-identical.
ATTACK_0001_ROLES = {
    "attack-0001": {
        "layers": [
            {"layer_index": 2, "role": "attack_body;attack_weapon_sword", "topology_class": "owned"},
            {"layer_index": 3, "role": "weapon_swoosh", "topology_class": "owned"},
        ],
        "expected_visible_layer_indices": [2, 3],
    }
}


def test_attack_swoosh_composite_byte_identical_to_legacy():
    """Branch A core: the reviewed per-role owner (explicit swoosh composite) compiles
    byte-identical to the legacy merge owner -- ownership change only, no visual change."""
    c = load_compiler()
    key = _key_for(c, "attack", "0001")
    c._LAYER_ROLES_CACHE = {}  # legacy single-body merge owner
    prof_legacy, _ = c._synthetic_profile_for_key(key)
    _, legacy_cells, legacy_meta = c._compile_profile_cells(prof_legacy, {})
    c._LAYER_ROLES_CACHE = dict(ATTACK_0001_ROLES)  # reviewed per-role owner
    prof_new, _ = c._synthetic_profile_for_key(key)
    _, new_cells, new_meta = c._compile_profile_cells(prof_new, {})
    c._LAYER_ROLES_CACHE = None
    # ownership shape changed, legacy merge flag stays dead for the migrated key
    assert [(l["role"], l["layer_index"], l["merge_extra_layers"]) for l in prof_new["layers"]] == [
        ("attack_body;attack_weapon_sword", 2, False),
        ("weapon_swoosh", 3, False),
    ]
    assert not any(l["merge_extra_layers"] for l in prof_new["layers"])
    body = next(l for l in prof_new["layers"] if l["layer_index"] == 2)
    swoosh = next(l for l in prof_new["layers"] if l["role"] == "weapon_swoosh")
    # attack-0001 is the degenerate single-overlay case of the multifold composite:
    # only L3 sits above the base, so the overlay run is [3].
    assert body.get("multifold_composite_overlay_indices") == [3]
    assert swoosh.get("composited_into_body_layer_index") == 2
    # byte identity (this is the seam-equivalence gate the promotion rests on)
    assert new_cells == legacy_cells
    assert new_meta == legacy_meta


def test_non_swoosh_migrated_profile_not_in_swoosh_path():
    """A migrated profile with no weapon_swoosh role must NOT be annotated for the
    swoosh composite path (player-1000: body + armor)."""
    c = load_compiler()
    profile, _ = c._synthetic_profile_for_key(_key_for(c, "player", "1000"))
    for l in profile["layers"]:
        assert "swoosh_composite_swoosh_layer_index" not in l
        assert "multifold_composite_overlay_indices" not in l
        assert "composited_into_body_layer_index" not in l
    c._LAYER_ROLES_CACHE = None


def test_swoosh_not_final_source_layer_fails_closed():
    """weapon_swoosh that is not the final source layer breaks is_final_layer parity
    with the legacy merge -> fail closed (byte identity cannot be guaranteed)."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/attack-0001.xp")  # 4 layers, final=L3
    c._LAYER_ROLES_CACHE = {"attack-0001": {
        "layers": [
            {"layer_index": 2, "role": "attack_body;attack_weapon_sword", "topology_class": "owned"},
            {"layer_index": 1, "role": "weapon_swoosh", "topology_class": "owned"},  # not final
        ],
        "expected_visible_layer_indices": [1, 2]}}
    with pytest.raises(SystemExit, match="final source layer"):
        c._profile_layers("attack-0001", "assets/sprites/attack-0001.xp", sprite,
                          list(range(sprite.atlas_frames)))
    c._LAYER_ROLES_CACHE = None


def test_swoosh_without_body_at_layer2_fails_closed():
    """A reviewed weapon_swoosh with no body layer at index 2 to composite into is a
    missing-reviewed-context regression -> fail closed visibly."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/attack-0001.xp")
    c._LAYER_ROLES_CACHE = {"attack-0001": {
        "layers": [
            {"layer_index": 3, "role": "weapon_swoosh", "topology_class": "owned"},
        ],
        "expected_visible_layer_indices": [3]}}
    with pytest.raises(SystemExit, match="body layer at index 2"):
        c._profile_layers("attack-0001", "assets/sprites/attack-0001.xp", sprite,
                          list(range(sprite.atlas_frames)))
    c._LAYER_ROLES_CACHE = None


# --- FL-4162 wolack mounted lane: the accumulating multifold composite ---
# wolack is the first family whose composite needs MORE than a single final swoosh
# fold: a dense rider-on-mount composite at L3 plus equipment overlays plus a final
# cyan swoosh. The seam-equivalence gate is that the explicit accumulating multifold
# reproduces the legacy `merge_extra_layers` owner cell-for-cell. attack-0001's swoosh
# composite is just the degenerate single-overlay case of the same fold.
WOLACK_VARIANTS = ["0001", "0011", "0101", "0111", "1001", "1011", "1101", "1111"]


def test_multifold_composite_matches_legacy_visual_cells_all_wolack():
    """SEAM-EQUIVALENCE CORE: the accumulating multifold over every raw layer above the
    base is byte-identical to the legacy merge owner for every wolack variant."""
    c = load_compiler()
    for ahsw in WOLACK_VARIANTS:
        sprite = c._load_xp(REPO_ROOT / f"assets/sprites/wolack-{ahsw}.xp")
        legacy = sprite._visual_cells(2, merge_extra_layers=True)
        overlays = list(range(3, len(sprite.layers)))
        multifold = sprite._visual_cells_multifold_composite(2, overlays)
        assert multifold == legacy, f"wolack-{ahsw} multifold != legacy merge"


def test_swoosh_composite_is_degenerate_multifold():
    """attack-0001's swoosh composite (1 overlay) routes through the same fold and stays
    byte-identical to the legacy merge."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/attack-0001.xp")
    legacy = sprite._visual_cells(2, merge_extra_layers=True)
    swoosh = sprite._visual_cells_swoosh_composite(2, len(sprite.layers) - 1)
    multifold = sprite._visual_cells_multifold_composite(2, list(range(3, len(sprite.layers))))
    assert swoosh == legacy
    assert multifold == legacy


def _wolack_roles(n_layers):
    """Synthetic reviewed roles for a wolack variant: base mount at L2, dense rider
    composite + equipment at the intermediate layers, weapon_swoosh at the final layer.
    The role strings are immaterial to the fold (it reads raw layers); only ownership,
    owned topology, and the final weapon_swoosh matter."""
    overlays = list(range(3, n_layers))
    layers = [{"layer_index": 2, "role": "mount_body_wolf", "topology_class": "owned"}]
    for i in overlays:
        role = "weapon_swoosh" if i == overlays[-1] else (
            "mount_body_wolf;rider_torso;wolack_weapon_sword" if i == 3 else f"equipment_{i}")
        layers.append({"layer_index": i, "role": role, "topology_class": "owned"})
    return {"layers": layers, "expected_visible_layer_indices": [2] + overlays}


def test_wolack_weapon_swoosh_binds_full_multifold():
    """The reviewed wolack stack binds the ENTIRE overlay run [3..N] into the base (not
    just the final swoosh), and marks every overlay composited."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/wolack-1111.xp")  # 8 layers, L2..L7
    n = len(sprite.layers)
    c._LAYER_ROLES_CACHE = {"wolack-1111": _wolack_roles(n)}
    layers = c._profile_layers("wolack-1111", "assets/sprites/wolack-1111.xp", sprite,
                               list(range(sprite.atlas_frames)))
    body = next(l for l in layers if l["layer_index"] == 2)
    assert body.get("multifold_composite_overlay_indices") == list(range(3, n))
    assert all(l.get("composited_into_body_layer_index") == 2
               for l in layers if l["layer_index"] != 2)
    assert not any(l["merge_extra_layers"] for l in layers)
    c._LAYER_ROLES_CACHE = None


def test_wolack_multifold_compiles_byte_identical_to_legacy():
    """End-to-end through _compile_profile_cells: the explicit per-role wolack profile
    compiles byte-identical to the legacy merge owner (the promotion gate)."""
    c = load_compiler()
    key = _key_for(c, "wolack", "1111")
    c._LAYER_ROLES_CACHE = {}  # legacy single-body merge owner
    prof_legacy, _ = c._synthetic_profile_for_key(key)
    _, legacy_cells, legacy_meta = c._compile_profile_cells(prof_legacy, {})
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/wolack-1111.xp")
    c._LAYER_ROLES_CACHE = {"wolack-1111": _wolack_roles(len(sprite.layers))}
    prof_new, _ = c._synthetic_profile_for_key(key)
    _, new_cells, new_meta = c._compile_profile_cells(prof_new, {})
    c._LAYER_ROLES_CACHE = None
    assert new_cells == legacy_cells
    assert new_meta == legacy_meta


def test_wolack_multifold_unowned_intermediate_fails_closed():
    """A wolack stack that owns the base + swoosh but NOT the intermediate folded layers
    cannot be byte-identical to legacy (legacy folds them too) -> fail closed."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/wolack-1111.xp")
    n = len(sprite.layers)
    c._LAYER_ROLES_CACHE = {"wolack-1111": {
        "layers": [
            {"layer_index": 2, "role": "mount_body_wolf", "topology_class": "owned"},
            {"layer_index": n - 1, "role": "weapon_swoosh", "topology_class": "owned"},
        ],
        "expected_visible_layer_indices": [2, n - 1]}}
    with pytest.raises(SystemExit, match="byte-identity cannot hold"):
        c._profile_layers("wolack-1111", "assets/sprites/wolack-1111.xp", sprite,
                          list(range(sprite.atlas_frames)))
    c._LAYER_ROLES_CACHE = None


def test_multifold_non_contiguous_overlay_fails_closed():
    """The fold method itself rejects an overlay run that is not every raw layer above
    the base (byte-identity with legacy requires the full contiguous run)."""
    c = load_compiler()
    sprite = c._load_xp(REPO_ROOT / "assets/sprites/wolack-1111.xp")
    n = len(sprite.layers)
    with pytest.raises(SystemExit, match="full contiguous run"):
        sprite._visual_cells_multifold_composite(2, [3, n - 1])  # skips intermediates
    c._LAYER_ROLES_CACHE = None
