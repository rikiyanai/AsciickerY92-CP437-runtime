"""Proof tool-path ownership: proof/harness drivers live under tools/proofs."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot_project"
NEW_DIR = GODOT / "tools" / "proofs"
OLD_DIR = GODOT / "scripts"

EXPECTED_UIDS = {
    "run_fl4265_airplane_dogfight_proof.gd": "uid://rcqyehjmwrw1",
    "run_fl4270_view_mode_capture.gd": "uid://c4nqsyfi1elqs",
    "run_fl4359_walk_capture.gd": "uid://cvqcf6p11hke5",
    "run_fl4359_static_capture.gd": "uid://3377q35qaeru",
    "run_headed_gameplay_harness.gd": "uid://g338uqmd2a54",
}

OLD_RES_ADDRESSES = [
    "res://scripts/run_fl4265_airplane_dogfight_proof",
    "res://scripts/run_fl4270_view_mode_capture",
    "res://scripts/run_fl4359_walk_capture",
    "res://scripts/run_fl4359_static_capture",
    "res://scripts/run_headed_gameplay_harness",
]

OLD_FS_ADDRESSES = [
    "godot_project/scripts/run_fl4265_airplane_dogfight_proof.gd",
    "godot_project/scripts/run_fl4270_view_mode_capture.gd",
    "godot_project/scripts/run_fl4359_walk_capture.gd",
    "godot_project/scripts/run_fl4359_static_capture.gd",
    "godot_project/scripts/run_headed_gameplay_harness.gd",
]

# Allowlisted live callers: path -> new-address tokens that must be present.
# Old res:// and filesystem addresses must be absent. Basename-only
# proof-identity strings (png_capture_owner, NIGHT_CAPTURE_OWNER) are
# intentionally not asserted here.
CALLERS: dict[Path, list[str]] = {
    ROOT / "config" / "godot_proof_pose_registry.json": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
        "tools/proofs/run_fl4270_view_mode_capture",
        "tools/proofs/run_fl4359_static_capture",
        "tools/proofs/run_fl4359_walk_capture",
    ],
    ROOT / "scripts" / "validate_godot_proof_pose_registry.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
        "tools/proofs/run_fl4270_view_mode_capture",
        "tools/proofs/run_fl4359_static_capture",
        "tools/proofs/run_fl4359_walk_capture",
    ],
    ROOT / "scripts" / "run_fl4265_airplane_proof.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "run_fl4359_phase2a_capture.py": [
        "tools/proofs/run_fl4359_walk_capture",
    ],
    ROOT / "scripts" / "build_fl4431_baseline_receipt_v2.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "run_fl4359_acceptance_package.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "test_fl4359_acceptance_package.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "test_fl4408_pixel_camera_context_contract.py": [
        "tools/proofs/run_headed_gameplay_harness",
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "test_fl4431_airborne_latch_deleted_contract.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "test_fl4431_flight_input_deadzone_contract.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "test_fl4359_moving_camera_analyzer.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "scripts" / "tests" / "test_fl4359_screen_recording_lifecycle_contract.py": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "godot_project" / "scripts" / "tests" / "test_fl4265_flight_command_observation.gd": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
    ROOT / "godot_project" / "scripts" / "tests" / "test_fl4431_night_null_product_tuple.gd": [
        "tools/proofs/run_fl4265_airplane_dogfight_proof",
    ],
}

_PRELOAD_RE = re.compile(r'preload\("res://([^"]+)"\)')
_LOAD_RE = re.compile(r'load\("res://([^"]+)"\)')


def test_new_canonical_paths_exist_with_uid_siblings() -> None:
    assert NEW_DIR.is_dir(), f"missing {NEW_DIR}"
    for name, expected_uid in EXPECTED_UIDS.items():
        script = NEW_DIR / name
        uid = NEW_DIR / f"{name}.uid"
        assert script.is_file(), f"missing {script}"
        assert uid.is_file(), f"missing {uid}"
        text = uid.read_text(encoding="utf-8").strip()
        assert text == expected_uid, f"UID drift in {uid}: {text!r}"


def test_old_runtime_addresses_absent() -> None:
    leftovers = []
    for name in EXPECTED_UIDS:
        if (OLD_DIR / name).exists():
            leftovers.append(str(OLD_DIR / name))
        if (OLD_DIR / f"{name}.uid").exists():
            leftovers.append(str(OLD_DIR / f"{name}.uid"))
    assert leftovers == [], f"old proof addresses remain: {leftovers}"


def test_moved_bodies_carry_no_old_self_addresses() -> None:
    for name in EXPECTED_UIDS:
        source = (NEW_DIR / name).read_text(encoding="utf-8")
        for old in OLD_RES_ADDRESSES:
            assert old not in source, f"{name} still references old address {old}"


def test_moved_driver_preloads_and_loads_resolve() -> None:
    missing: list[str] = []
    for name in EXPECTED_UIDS:
        source = (NEW_DIR / name).read_text(encoding="utf-8")
        for match in list(_PRELOAD_RE.finditer(source)) + list(_LOAD_RE.finditer(source)):
            target = GODOT / match.group(1)
            if not target.is_file():
                missing.append(f"{name} -> res://{match.group(1)}")
    assert missing == [], f"unresolved proof driver references: {missing}"


def test_known_callers_and_registry_resolve_to_new_address() -> None:
    for caller, tokens in CALLERS.items():
        assert caller.is_file(), f"missing caller {caller}"
        text = caller.read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, f"{caller} missing new address {token}"
        for old in OLD_RES_ADDRESSES:
            assert old not in text, f"{caller} still references old address {old}"
        for old_fs in OLD_FS_ADDRESSES:
            assert old_fs not in text, f"{caller} still references old filesystem path {old_fs}"


def test_additional_path_expression_consumers_resolve() -> None:
    # Path expressions split across lines evade literal old-address searches.
    # Resolve only ROOT / string literals, without importing executable drivers.
    consumers = {
        "fl4408_actual_godot_capture_adapter.py": "DOGFIGHT_DRIVER",
        "test_fl4265_airplane_dogfight_contract.py": "PROOF_HARNESS",
        "test_fl4431_night_capture_frontdoor_contract.py": "STATIC_CAPTURE_PATH",
        "test_fl4431_night_temporal_capture_contract.py": "DRIVER_PATH",
        "test_fl4431_surface_falsifier_contract.py": "HARNESS",
    }

    def resolve(node: ast.expr) -> Path | str:
        if isinstance(node, ast.Name) and node.id == "ROOT":
            return ROOT
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return Path(resolve(node.left)) / resolve(node.right)
        raise AssertionError(f"unsupported driver path expression: {ast.dump(node)}")

    for filename, symbol in consumers.items():
        source = ROOT / "scripts" / filename
        assignments = [
            node.value for node in ast.parse(source.read_text()).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == symbol
                    for target in node.targets)
        ]
        assert len(assignments) == 1, f"{source}: expected one {symbol} owner"
        target = Path(resolve(assignments[0]))
        assert target.parent == NEW_DIR and target.is_file(), (source, target)

    verifier = GODOT / "import_upstream" / "verify_godot_slice.py"
    source = verifier.read_text()
    assert '_read("tools/proofs/run_headed_gameplay_harness.gd")' in source
    assert '_read("scripts/run_headed_gameplay_harness.gd")' not in source
