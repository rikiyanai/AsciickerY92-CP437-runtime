"""FL-4162/RQ-200 compiler ownership tests for the frozen full-cell contract."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("compile_actor_visual_profiles", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_coverage_checker():
    path = REPO_ROOT / "scripts" / "check_actor_visual_table_coverage.py"
    spec = importlib.util.spec_from_file_location(
        "check_actor_visual_table_coverage", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_contract_covers_every_raw_layer() -> None:
    compiler = _load_module()
    freeze, by_source_key = compiler._require_frozen_upstream_contract()
    assert freeze["coverage"]["raw_layers"] == 573
    assert freeze["coverage"]["review_units"] == 203
    assert freeze["coverage"]["pending_review_units"] == 0
    assert freeze["cutover_boundary"]["compiler_cutover_complete"] is True
    assert len(by_source_key) == 573


def test_false_clean_player_base_stays_composite() -> None:
    compiler = _load_module()
    source_id = "player-0001"
    source_xp = "assets/sprites/player-0001.xp"
    sprite = compiler._load_xp(REPO_ROOT / source_xp)
    layers = compiler._profile_layers(
        source_id, source_xp, sprite, list(range(sprite.atlas_frames))
    )
    assert len(layers) == 1
    assert layers[0]["layer_index"] == 2
    assert layers[0]["semantic_contributions"] == [
        "player_body",
        "player_weapon_sword",
    ]
    assert "seed_l2_base_accumulator" in layers[0]["render_operations"]
    assert "merge_extra_layers" not in layers[0]


def test_final_cyan_swoosh_uses_reviewed_multifold() -> None:
    compiler = _load_module()
    source_id = "attack-0001"
    source_xp = "assets/sprites/attack-0001.xp"
    sprite = compiler._load_xp(REPO_ROOT / source_xp)
    layers = compiler._profile_layers(
        source_id, source_xp, sprite, list(range(sprite.atlas_frames))
    )
    assert [layer["layer_index"] for layer in layers] == [2, 3]
    assert layers[0]["multifold_composite_overlay_indices"] == [3]
    assert layers[1]["composited_into_body_layer_index"] == 2
    assert "final_cyan_swoosh_context_composite" in layers[1]["render_operations"]


def test_stale_freeze_binding_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compiler = _load_module()
    stale_queue = tmp_path / "review_queue.json"
    stale_queue.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(compiler, "UPSTREAM_CONTRACT_QUEUE", stale_queue)
    with pytest.raises(SystemExit, match="freeze source is stale"):
        compiler._require_frozen_upstream_contract()


def test_old_layer_role_owner_is_deleted() -> None:
    assert not (
        REPO_ROOT / "assets/actor_visual_profiles/source/layer_roles.json"
    ).exists()
    assert not (REPO_ROOT / "scripts/promote_entries_to_layer_roles.py").exists()
    runtime = (REPO_ROOT / "engine/actor_visual_profile_runtime.h").read_text(
        encoding="utf-8"
    )
    assert "const bool merge_extra" not in runtime
    assert "ACTOR_VISUAL_LAYER_ROLE_" not in runtime


def test_server_dump_and_authored_bindings_cover_exact_same_keys() -> None:
    compiler = _load_module()
    reachability, keys = compiler._server_reachable_keys()
    bindings = compiler._profile_bindings(reachability, keys)
    assert len(keys) == 192
    assert len(bindings) == 192
    assert set(bindings) == {compiler._key_sort_tuple(key) for key in keys}


def test_local_ahsw_and_reachability_owners_are_deleted() -> None:
    source = (REPO_ROOT / "scripts/compile_actor_visual_profiles.py").read_text(
        encoding="utf-8"
    )
    assert "def _ahs_digits" not in source
    assert "def _resolve_source_xp" not in source
    assert "def _enumerate_server_reachable_keys" not in source
    assert "merge_extra_layers\": True" not in source

    checker = (
        REPO_ROOT / "scripts/check_actor_visual_table_coverage.py"
    ).read_text(encoding="utf-8")
    assert "def _expected_keys(" not in checker
    assert "DEFAULT_REACHABILITY" in checker


@pytest.mark.parametrize(
    "stale_name",
    ["reachability", "bindings", "contract_freeze", "cell_decisions"],
)
def test_generated_table_fails_closed_on_stale_inputs(
    tmp_path: Path, stale_name: str
) -> None:
    checker = _load_coverage_checker()
    paths = {
        "table": REPO_ROOT / "engine/actor_visual_profile_table.generated.h",
        "reachability": checker.DEFAULT_REACHABILITY,
        "bindings": checker.DEFAULT_BINDINGS,
        "contract_freeze": checker.DEFAULT_CONTRACT_FREEZE,
        "cell_decisions": checker.DEFAULT_CELL_DECISIONS,
        "cutover_receipt": checker.DEFAULT_CUTOVER_RECEIPT,
    }
    copied: dict[str, Path] = {}
    for name, source in paths.items():
        destination = tmp_path / source.name
        shutil.copyfile(source, destination)
        copied[name] = destination
    copied[stale_name].write_bytes(copied[stale_name].read_bytes() + b"\n")

    with pytest.raises(SystemExit, match="generated table is stale"):
        checker._check_freshness(**copied)


def test_cutover_receipt_is_hash_bound_to_runtime_table(
    tmp_path: Path,
) -> None:
    checker = _load_coverage_checker()
    paths = {
        "table": REPO_ROOT / "engine/actor_visual_profile_table.generated.h",
        "reachability": checker.DEFAULT_REACHABILITY,
        "bindings": checker.DEFAULT_BINDINGS,
        "contract_freeze": checker.DEFAULT_CONTRACT_FREEZE,
        "cell_decisions": checker.DEFAULT_CELL_DECISIONS,
        "cutover_receipt": checker.DEFAULT_CUTOVER_RECEIPT,
    }
    checker._check_freshness(**paths)

    stale_receipt = tmp_path / "compiler_cutover.json"
    shutil.copyfile(checker.DEFAULT_CUTOVER_RECEIPT, stale_receipt)
    receipt_doc = json.loads(stale_receipt.read_text(encoding="utf-8"))
    receipt_doc["source_hashes"]["generated_table_sha256"] = "0" * 64
    stale_receipt.write_text(json.dumps(receipt_doc) + "\n", encoding="utf-8")
    paths["cutover_receipt"] = stale_receipt
    with pytest.raises(SystemExit, match="compiler cutover receipt is stale"):
        checker._check_freshness(**paths)
