"""FL-4512 watchdog routing: Classic default, explicit Current, no ambiguity.

The local two-tab path builds server output plus the web WASM bundle
(build-web.sh compiles current-runtime glyph sources) and drives them on the
sandbox proof map. That stack is the FL4512/Current runtime, never the
Classic CP437 native binary — so the default product must be Classic, the
local launch must fail closed without the explicit Current flag, and every
receipt must stamp the product/map/choreography identity it ran under.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watchdog_runner
from watchdog.analyze_receipt import build_summary
from watchdog.phases.collect_runtime import collect_local_runtime
from watchdog_operator_help import build_operator_help


def _local_args(**overrides: object):
    argv = ["--mode", "local"]
    for key, value in overrides.items():
        argv.append(f"--{key.replace('_', '-')}")
        argv.append(str(value))
    return watchdog_runner.build_parser().parse_args(argv)


def test_runtime_product_defaults_to_classic() -> None:
    args = watchdog_runner.build_parser().parse_args([])
    assert args.runtime_product == watchdog_runner.RUNTIME_PRODUCT_CLASSIC


def test_runtime_product_accepts_explicit_current() -> None:
    args = _local_args(runtime_product="fl4512_unicode")
    assert args.runtime_product == watchdog_runner.RUNTIME_PRODUCT_CURRENT


def test_runtime_product_rejects_unknown_product() -> None:
    with pytest.raises(SystemExit):
        _local_args(runtime_product="game-classic")


def test_dry_run_local_default_names_classic_refusal(capsys) -> None:
    assert watchdog_runner.main(["--mode", "local", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "runtime-product:   classic_cp437" in out
    assert "REFUSE under the Classic default" in out
    assert "--runtime-product fl4512_unicode" in out


def test_dry_run_local_explicit_current_proceeds(capsys) -> None:
    argv = ["--mode", "local", "--dry-run",
            "--runtime-product", "fl4512_unicode",
            "--controller-mode", "recipe",
            "--controller-recipe", "smoke"]
    assert watchdog_runner.main(argv) == 0
    out = capsys.readouterr().out
    assert "PROCEED as explicit Current" in out
    assert "not the Classic binary" in out
    assert watchdog_runner.LOCAL_PROOF_MAP in out
    assert watchdog_runner.LOCAL_COLLECTOR in out
    assert "controller-recipe: smoke" in out


def test_run_local_refuses_classic_before_side_effects(tmp_path) -> None:
    args = _local_args(artifact_root=str(tmp_path / "runs"))
    with pytest.raises(RuntimeError, match="fl4512_unicode"):
        watchdog_runner._run_local(args)
    assert not (tmp_path / "runs").exists()


def test_collector_refuses_non_current_product(tmp_path) -> None:
    source = types.SimpleNamespace(repo_root=tmp_path)
    with pytest.raises(RuntimeError, match="fl4512_unicode only"):
        collect_local_runtime(
            source=source,
            invocation_repo=REPO_ROOT,
            artifact_dir=tmp_path / "artifacts",
            run_id="refused",
            drive_ms=12000,
            controller_timeout_seconds=300,
            movement_pulse_ms=35,
            runtime_product="classic_cp437",
        )
    assert not (tmp_path / "artifacts").exists()


def test_summary_stamps_product_map_and_choreography(tmp_path) -> None:
    summary = build_summary(
        run_id="probe",
        run_label="passive",
        artifact_dir=tmp_path,
        source_ref="abc123def456",
        started_at="2026-09-20T00:00:00+00:00",
        recorder_health={"status": "pass"},
        actor_visual_parity={"gates": {}},
        runtime_product="fl4512_unicode",
        runtime_map=watchdog_runner.LOCAL_PROOF_MAP,
        controller_mode="recipe",
        controller_recipe="smoke",
    )
    assert summary["runtime_product"] == "fl4512_unicode"
    assert summary["runtime_renderer"] == "fl4512_unicode"
    assert summary["runtime_map"] == watchdog_runner.LOCAL_PROOF_MAP
    assert summary["controller_mode"] == "recipe"
    assert summary["controller_recipe"] == "smoke"
    assert summary["artifact_path"] == str(tmp_path)
    assert summary["closure_eligible"] is False
    assert summary["git_head"] == "abc123def456"[:12]


def test_summary_rejects_unknown_product(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="unknown runtime_product"):
        build_summary(
            run_id="probe",
            run_label="passive",
            artifact_dir=tmp_path,
            source_ref="abc123def456",
            started_at="2026-09-20T00:00:00+00:00",
            recorder_health={"status": "pass"},
            actor_visual_parity={"gates": {}},
            runtime_product="game-classic",
            runtime_map=watchdog_runner.LOCAL_PROOF_MAP,
        )


def test_operator_help_surfaces_runtime_product() -> None:
    help_data = build_operator_help(
        {
            "run_id": "probe",
            "runtime_product": "fl4512_unicode",
            "runtime_map": watchdog_runner.LOCAL_PROOF_MAP,
            "false_gates": [],
            "null_gates": [],
        }
    )
    assert help_data["runtime_product"] == "fl4512_unicode"
    assert help_data["runtime_map"] == watchdog_runner.LOCAL_PROOF_MAP
    assert "Classic" in help_data["interpretation"]["runtime_product"]
    assert "failure_class" in help_data
    assert "controller_mode" in help_data


def test_operator_help_preserves_legacy_fields() -> None:
    help_data = build_operator_help(
        {
            "run_id": "probe",
            "failure_class": "online_unavailable",
            "controller_mode": "recipe",
            "false_gates": [],
            "null_gates": [],
        }
    )
    assert help_data["failure_class"] == "online_unavailable"
    assert help_data["controller_mode"] == "recipe"


def test_failure_receipt_keeps_closure_and_identity(tmp_path, monkeypatch) -> None:
    args = _local_args(
        artifact_root=str(tmp_path / "runs"),
        runtime_product="fl4512_unicode",
    )
    def _boom(*args, **kwargs):
        raise RuntimeError("boom-before-build")
    monkeypatch.setattr(watchdog_runner, "prepare_source_snapshot", _boom)
    with pytest.raises(RuntimeError, match="boom-before-build"):
        watchdog_runner._run_local(args)
    runs = list((tmp_path / "runs").glob("*"))
    assert runs, "expected _run_local to create an artifact dir before failing"
    failure = next(runs[0].glob("launch_failure.json"), None)
    assert failure is not None
    import json
    payload = json.loads(failure.read_text(encoding="utf-8"))
    assert payload["closure_eligible"] is False
    assert payload["runtime_product"] == "fl4512_unicode"
    assert payload["runtime_map"] == watchdog_runner.LOCAL_PROOF_MAP
