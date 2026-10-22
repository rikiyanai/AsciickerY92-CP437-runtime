#!/usr/bin/env python3
"""Guard the FL-4431 capture child-environment authority boundary.

Every current-source airplane arm must launch the same server-owned route with
no inherited renderer ablation. This contract executes the wrapper's
environment builder with a deliberately polluted parent shell, then pins both
subprocess call sites so the sanitized dictionary is what the Colyseus server
and Godot client actually receive.

This is a source and pure-Python contract. It does not launch the game, render
pixels, establish visual parity, establish performance parity, and accept the
cutover.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = ROOT / "scripts" / "run_fl4265_airplane_proof.py"


def _load_wrapper() -> ModuleType:
    """Load the capture wrapper without entering its command-line main."""

    spec = importlib.util.spec_from_file_location(
        "fl4431_capture_environment_wrapper",
        WRAPPER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function_source(source: str, function_name: str) -> str:
    """Return one complete top-level Python function from the wrapper."""

    marker = f"def {function_name}("
    start = source.index(marker)
    end = source.find("\ndef ", start + len(marker))
    return source[start:] if end < 0 else source[start:end]


def _assert_source_contract(source: str) -> None:
    """Pin deletion, hard assignment, both subprocess seams, and receipt facts."""

    helper = _function_source(source, "_capture_child_environment")
    main = _function_source(source, "main")

    # The broad FL4431 prefix intentionally includes performance ablations,
    # phase counters, ABAB switches, and future diagnostics added under the same
    # namespace. Exact legacy trace names are deleted beside it.
    assert 'CAPTURE_CHILD_DENIED_ENV_PREFIXES = ("FL4431_", "FL4265_PROOF_")' in source
    for trace_key in (
        "PERF_FRAME_TRACE",
        "PERF_GPU_TRACE",
        "PERF_READBACK_TRACE",
        "PERF_TRACE",
    ):
        assert f'"{trace_key}"' in source

    # update() overwrites contaminated spawn values. setdefault() would retain
    # a caller's route and make the two arms fly different trajectories.
    assert "child.update(CAPTURE_CHILD_CANONICAL_SPAWN_ENV)" in helper
    assert "setdefault(" not in helper
    assert "effective_forbidden_keys_absent" in helper
    assert "effective_canonical_route_environment" in helper
    assert "denied_environment_policy_sha256" in helper
    assert "canonical_route_environment_match" in helper

    # The same sanitized dictionary must cross both process boundaries. Merely
    # constructing it while either subprocess inherits os.environ is no guard.
    server_launch = main[main.index("server = subprocess.Popen(") :]
    server_launch = server_launch[: server_launch.index("\n    def _listening")]
    assert "env=env" in server_launch
    godot_launch = main[main.index("godot_rc = subprocess.run(") :]
    godot_launch = godot_launch[: godot_launch.index(").returncode") + 1]
    assert "env=env" in godot_launch

    # Pair-facing receipt mirrors keep the policy and effective route directly
    # comparable without promoting the parent shell's contaminated values.
    for receipt_field in (
        "capture_child_denied_environment_policy_sha256",
        "capture_child_forbidden_environment_absent",
        "capture_child_canonical_route_environment",
        "capture_child_canonical_route_environment_sha256",
        "capture_child_canonical_route_environment_match",
    ):
        assert f'receipt["{receipt_field}"]' in main

    damage_contract = _function_source(
        source,
        "_fl4265_damage_front_door_contract",
    )
    assert "reasons.extend(_damage_png_identity_reasons(record, index))" in (
        damage_contract
    )


def _assert_executable_contract(wrapper: ModuleType) -> None:
    """Prove a contaminated parent produces one canonical child dictionary."""

    polluted_parent = {
        "PATH": "/usr/bin:/bin",
        "SAFE_CAPTURE_LABEL": "preserved",
        "FL4431_PERF_SKIP_PRODUCT": "1",
        "FL4265_PROOF_CRASH_SEQUENCE": "1",
        "PERF_READBACK_TRACE": "1",
        "AIRPLANE_SPAWN_AIRBORNE": "0",
        "AIRPLANE_SPAWN_ALTITUDE": "5",
        "AIRPLANE_SPAWN_THROTTLE": "0.99",
        "AIRPLANE_SPAWN_X": "999",
        "AIRPLANE_SPAWN_Z": "-999",
    }
    child, receipt = wrapper._capture_child_environment(polluted_parent)

    assert child["SAFE_CAPTURE_LABEL"] == "preserved"
    assert "FL4431_PERF_SKIP_PRODUCT" not in child
    assert "FL4265_PROOF_CRASH_SEQUENCE" not in child
    assert "PERF_READBACK_TRACE" not in child
    assert (
        {
            key: child[key]
            for key in wrapper.CAPTURE_CHILD_CANONICAL_SPAWN_ENV
        }
        == wrapper.CAPTURE_CHILD_CANONICAL_SPAWN_ENV
    )
    assert child["AIRPLANE_SPAWN_ALTITUDE"] == "80"
    assert receipt["effective_forbidden_keys"] == []
    assert receipt["effective_forbidden_keys_absent"] is True
    assert receipt["canonical_route_environment_match"] is True
    assert receipt["ok"] is True

    # Policy identity must remain stable across a clean and a polluted shell.
    # A hash of found keys would differ and could not prove both arms used the
    # same deletion contract.
    _, clean_receipt = wrapper._capture_child_environment(
        {"PATH": "/usr/bin:/bin"}
    )
    assert (
        receipt["denied_environment_policy_sha256"]
        == clean_receipt["denied_environment_policy_sha256"]
    )
    expected_route_payload = json.dumps(
        wrapper.CAPTURE_CHILD_CANONICAL_SPAWN_ENV,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert (
        receipt["effective_canonical_route_environment_sha256"]
        == hashlib.sha256(
            expected_route_payload.encode("utf-8")
        ).hexdigest()
    )

    nausicaa_child, nausicaa_receipt = wrapper._capture_child_environment(
        polluted_parent,
        wrapper.CAPTURE_CHILD_NAUSICAA_SPAWN_ENV,
    )
    assert nausicaa_child["NAUSICAA_SPAWN_IN_PLANE"] == "1"
    for key in wrapper.CAPTURE_CHILD_CANONICAL_SPAWN_ENV:
        assert key not in nausicaa_child
    assert (
        nausicaa_receipt["effective_canonical_route_environment"]
        == wrapper.CAPTURE_CHILD_NAUSICAA_SPAWN_ENV
    )
    assert nausicaa_receipt["canonical_route_environment_match"] is True


def _assert_damage_png_identity_contract(wrapper: ModuleType) -> None:
    """Prove post-exit byte substitution invalidates the damage frame row."""

    with tempfile.TemporaryDirectory(
        prefix="fl4431-damage-png-identity-"
    ) as temporary_directory:
        png_path = Path(temporary_directory) / "damage.png"
        original_bytes = b"\x89PNG\r\n\x1a\nFL4431-original-damage-frame"
        png_path.write_bytes(original_bytes)
        record = {
            "capture_path": str(png_path),
            "png_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "png_byte_size": len(original_bytes),
        }
        assert wrapper._damage_png_identity_reasons(record, 0) == []

        # Same-size replacement is the dangerous case: a size-only check would
        # still pass while the receipt points at different pixels.
        overwritten_bytes = b"\x89PNG\r\n\x1a\nFL4431-replaced-damage-frame"
        assert len(overwritten_bytes) == len(original_bytes)
        png_path.write_bytes(overwritten_bytes)
        assert wrapper._damage_png_identity_reasons(record, 0) == [
            "damage_frame_0_png_hash_mismatch"
        ]


def _assert_mutations_go_red(source: str) -> list[str]:
    """Apply real contamination regressions and require every one to fail."""

    mutations = {
        "performance_prefix_not_deleted": source.replace(
            'CAPTURE_CHILD_DENIED_ENV_PREFIXES = ("FL4431_", "FL4265_PROOF_")',
            "CAPTURE_CHILD_DENIED_ENV_PREFIXES = ()",
            1,
        ),
        "spawn_parent_value_preserved": source.replace(
            "child.update(CAPTURE_CHILD_CANONICAL_SPAWN_ENV)",
            (
                "for key, value in CAPTURE_CHILD_CANONICAL_SPAWN_ENV.items():\n"
                "        child.setdefault(key, value)"
            ),
            1,
        ),
        "server_inherits_parent_environment": source.replace(
            "cwd=str(colyseus_dir), env=env, stdout=slog",
            "cwd=str(colyseus_dir), stdout=slog",
            1,
        ),
        "godot_inherits_parent_environment": source.replace(
            "cmd, cwd=str(ROOT), stdout=glog, stderr=subprocess.STDOUT,\n"
            "                    env=env,",
            "cmd, cwd=str(ROOT), stdout=glog, stderr=subprocess.STDOUT,",
            1,
        ),
        "pair_policy_identity_removed": source.replace(
            'receipt["capture_child_denied_environment_policy_sha256"]',
            'receipt["capture_child_denied_environment_policy_removed"]',
            1,
        ),
        "damage_png_disk_identity_not_checked": source.replace(
            "reasons.extend(_damage_png_identity_reasons(record, index))",
            "reasons.extend([])",
            1,
        ),
    }
    caught: list[str] = []
    for name, mutant in mutations.items():
        assert mutant != source, f"mutation did not alter source: {name}"
        try:
            _assert_source_contract(mutant)
        except AssertionError:
            caught.append(name)
            continue
        raise AssertionError(f"capture child-environment mutant survived: {name}")
    return caught


def main() -> int:
    """Run source pins, executable contamination checks, and mutations."""

    source = WRAPPER_PATH.read_text(encoding="utf-8")
    wrapper = _load_wrapper()
    _assert_source_contract(source)
    _assert_executable_contract(wrapper)
    _assert_damage_png_identity_contract(wrapper)
    caught = _assert_mutations_go_red(source)
    print(
        "FL4431_CAPTURE_CHILD_ENVIRONMENT_CONTRACT_PASS "
        f"mutants={len(caught)} inherited_perf_removed=1 "
        "inherited_altitude_overwritten=1 subprocesses=2 "
        "damage_png_post_exit_overwrite_rejected=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
