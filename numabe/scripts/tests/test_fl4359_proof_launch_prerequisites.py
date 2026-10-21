"""Refuse unusable full-grid captures before starting the game and server."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_fl4359_headed_movement as runner

METHODS = ("canvas_provenance_arm", "canvas_provenance_readback",
           "canvas_provenance_capture_bounds", "get_render_frame_number")


@pytest.mark.parametrize("mode", ["FF", "FC", "CF", "CC", "FF_SHARED"])
def test_presentation_path_is_selected_at_world_startup_and_proof_start(mode):
    original = ["--some-independent-option"]
    values = runner._presentation_user_args(mode, original)
    assert original == ["--some-independent-option"]
    assert f"--presentation-mode={mode}" in values
    assert f"--fl4359-presentation-mode={mode}" in values
    assert runner._presentation_user_args(mode, values) == values


@pytest.mark.parametrize("prefix", ["--presentation-mode=", "--fl4359-presentation-mode="])
def test_conflicting_extra_presentation_arg_cannot_bypass_admitted_path(prefix):
    with pytest.raises(ValueError, match="conflicts"):
        runner._presentation_user_args("FF_SHARED", [prefix + "FF"])


def test_headed_admission_receives_actual_presentation_selection():
    import ast

    source = ast.parse(Path(runner.__file__).read_text())
    calls = [node for node in ast.walk(source) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "admit"]
    assert len(calls) == 1
    mode = next(key.value for key in calls[0].keywords if key.arg == "presentation_mode")
    assert ast.unparse(mode) == "args.presentation_mode"
CANVAS_BOUNDS = {
    "schema": "fl4691.canvas_provenance_bounds.v1",
    "max_target_width": 3600,
    "max_target_height": 2252,
}
PIXEL_BOUNDED_CANVAS_BOUNDS = {
    **CANVAS_BOUNDS,
    "max_target_width": 3840,
    "max_target_pixels": 3840 * 2160,
}


def configuration(**changes):
    values = dict(analyzer_proof_profile="full_grid_temporal_oracle_v1",
                  run_classification="movement-proof", presentation_field_capture=True,
                  canvas_provenance_capture=True,
                  presentation_field_capture_budget_bytes=runner.PRESENTATION_FIELD_REQUIRED_PEAK_BYTES)
    values.update(changes)
    return argparse.Namespace(**values)


@pytest.mark.parametrize("field,value", [
    ("presentation_field_capture", False), ("canvas_provenance_capture", False),
    ("presentation_field_capture_budget_bytes", 0),
])
def test_full_grid_requires_each_observer_and_budget(field, value):
    result = runner._full_grid_capture_prerequisites(configuration(**{field: value}))
    assert result["required"] and not result["ok"]
    assert len(result["failed_checks"]) == 1


def test_configuration_pass_does_not_claim_engine_compatibility():
    result = runner._full_grid_capture_prerequisites(configuration())
    assert result["ok"]
    assert result["engine_capabilities"] == "not_checked_configuration_only"
    assert runner._full_grid_capture_prerequisites(configuration(
        run_classification="diagnostic-only", canvas_provenance_capture=False))["ok"]


def probe(monkeypatch, tmp_path, *, missing=(), suffix="", returncode=0,
          mutate=False, bounds=CANVAS_BOUNDS, omit_bounds=False,
          requested_resolution=None):
    binary = tmp_path / "godot"
    binary.write_bytes(b"fixed engine identity")
    witness = {"schema": "fl4359.engine_capabilities.v1", "game_started": False,
               "engine": {"hash": "test-engine"},
               "methods": {method: method not in missing for method in METHODS}}
    if not omit_bounds:
        witness["canvas_provenance_bounds"] = bounds
    output = "FL4359_ENGINE_CAPABILITIES " + json.dumps(witness) + "\n" + suffix
    def execute(command, **kwargs):
        assert "--headless" in command and "--fl4359-engine-capabilities-only" in command
        assert kwargs["timeout"] == 30
        if mutate:
            binary.write_bytes(b"replaced engine")
        return SimpleNamespace(returncode=returncode, stdout=output)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    return runner._canvas_engine_capabilities(
        str(binary), tmp_path, tmp_path / "driver.gd", requested_resolution
    )


@pytest.mark.parametrize("missing", [(method,) for method in METHODS] + [METHODS])
def test_engine_missing_any_required_api_is_rejected(monkeypatch, tmp_path, missing):
    result = probe(monkeypatch, tmp_path, missing=missing)
    assert not result["ok"] and result["reason"] == "canvas_engine_api_missing"
    assert result["missing_methods"] == list(missing)


def test_capable_engine_retains_binary_identity_without_starting_game(monkeypatch, tmp_path):
    result = probe(monkeypatch, tmp_path)
    assert result["ok"] and not result["game_started"]
    assert len(result["executable"]["sha256"]) == 64
    assert result["canvas_provenance_bounds"] == CANVAS_BOUNDS


@pytest.mark.parametrize("kwargs", [
    {"omit_bounds": True},
    {"bounds": None},
    {"bounds": {"schema": "fl4691.canvas_provenance_bounds.v1"}},
    {"bounds": {**CANVAS_BOUNDS, "max_target_width": 0}},
    {"bounds": {**CANVAS_BOUNDS, "max_target_height": "2252"}},
    {"bounds": {**CANVAS_BOUNDS, "max_target_pixels": 0}},
    {"bounds": {**CANVAS_BOUNDS, "max_target_pixels": None}},
    {"bounds": {**CANVAS_BOUNDS, "max_target_pixels": True}},
    {"bounds": {**CANVAS_BOUNDS, "max_target_pixels": "8294400"}},
    {"bounds": {**CANVAS_BOUNDS, "schema": "wrong.schema"}},
])
def test_missing_or_malformed_canvas_bounds_refuse_before_launch(monkeypatch, tmp_path, kwargs):
    result = probe(monkeypatch, tmp_path, **kwargs)
    assert not result["ok"]
    assert result["reason"] == "canvas_engine_bounds_invalid"


@pytest.mark.parametrize("requested_resolution", [
    (3600, 2252),
    [3600, 2252],
])
def test_exact_canvas_bounds_are_supported(monkeypatch, tmp_path, requested_resolution):
    result = probe(monkeypatch, tmp_path, requested_resolution=requested_resolution)
    assert result["ok"]
    assert result["resolution_check"] == {
        "requested": [3600, 2252],
        "maximum": [3600, 2252],
        "exceeded": [],
        "pass": True,
    }


@pytest.mark.parametrize("requested_resolution,exceeded", [
    ((3601, 2252), ["width"]),
    ((3600, 2253), ["height"]),
    ((3601, 2253), ["width", "height"]),
])
def test_resolution_over_canvas_bounds_refuses_before_launch(
    monkeypatch, tmp_path, requested_resolution, exceeded
):
    result = probe(monkeypatch, tmp_path, requested_resolution=requested_resolution)
    assert not result["ok"]
    assert result["reason"] == "canvas_engine_requested_resolution_exceeds_bounds"
    assert result["resolution_check"]["exceeded"] == exceeded


@pytest.mark.parametrize("requested_resolution", [
    (3840, 2160),
    (3600, 2252),
])
def test_pixel_bounded_canvas_envelope_admits_both_canonical_shapes(
    monkeypatch, tmp_path, requested_resolution
):
    result = probe(
        monkeypatch, tmp_path, bounds=PIXEL_BOUNDED_CANVAS_BOUNDS,
        requested_resolution=requested_resolution,
    )
    assert result["ok"]
    assert result["resolution_check"]["maximum_pixels"] == 8_294_400
    assert result["resolution_check"]["requested_pixels"] <= 8_294_400


def test_pixel_bounded_canvas_envelope_rejects_rectangular_corner(monkeypatch, tmp_path):
    result = probe(
        monkeypatch, tmp_path, bounds=PIXEL_BOUNDED_CANVAS_BOUNDS,
        requested_resolution=(3840, 2252),
    )
    assert not result["ok"]
    assert result["reason"] == "canvas_engine_requested_resolution_exceeds_bounds"
    assert result["resolution_check"]["exceeded"] == ["pixels"]
    assert result["resolution_check"]["requested_pixels"] == 8_647_680



@pytest.mark.parametrize("requested_resolution", [
    (0, 2252),
    (3600,),
    ("3600", 2252),
])
def test_malformed_requested_resolution_refuses_without_probe(monkeypatch, tmp_path, requested_resolution):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid requested resolution must not launch the capability probe")
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    result = runner._canvas_engine_capabilities(
        str(tmp_path / "missing"), tmp_path, tmp_path / "driver.gd", requested_resolution
    )
    assert not result["ok"]
    assert result["reason"] == "canvas_requested_resolution_invalid"


@pytest.mark.parametrize("changes", [{"suffix": "SCRIPT ERROR: Parse Error\n"}, {"returncode": 1}])
def test_zero_exit_or_valid_json_cannot_hide_compile_failure(monkeypatch, tmp_path, changes):
    assert not probe(monkeypatch, tmp_path, **changes)["ok"]


def test_engine_replacement_is_rejected(monkeypatch, tmp_path):
    result = probe(monkeypatch, tmp_path, mutate=True)
    assert result["reason"] == "engine_binary_changed_during_capability_probe"


def test_missing_binary_refuses_without_launch(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("must not launch a missing executable")
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    assert not runner._canvas_engine_capabilities(str(tmp_path / "absent"), tmp_path, tmp_path)["ok"]


def test_timeout_refuses(monkeypatch, tmp_path):
    binary = tmp_path / "godot"
    binary.write_bytes(b"binary")
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(runner.subprocess, "run", timeout)
    result = runner._canvas_engine_capabilities(str(binary), tmp_path, tmp_path)
    assert result["reason"] == "engine_capability_probe_failed"


def test_incompatible_engine_stops_before_game_or_server_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["runner", "--out", str(tmp_path), "--camera-mode", "perspective",
        "--canvas-provenance-capture", "--presentation-field-capture",
        "--presentation-field-capture-budget-bytes", str(runner.PRESENTATION_FIELD_REQUIRED_PEAK_BYTES)])
    monkeypatch.setattr(runner, "_repo_provenance", lambda **kwargs: {"git_head": "1" * 40})
    monkeypatch.setattr(runner, "_runtime_source_identity", lambda *args: {"valid": True})
    monkeypatch.setattr(runner, "check_run_budget", lambda *args, **kwargs:
        SimpleNamespace(allowed=True, as_dict=lambda: {}))
    monkeypatch.setattr(runner, "_canvas_engine_capabilities", lambda *args:
        {"ok": False, "reason": "canvas_engine_api_missing", "game_started": False})
    def forbidden(*args, **kwargs):
        pytest.fail("engine failure must precede context/server/game setup")
    monkeypatch.setattr(runner, "_context_preflight", forbidden)
    assert runner.main() == 2
    assert json.loads((tmp_path / "engine_capabilities.json").read_text())["ok"] is False


def test_missing_source_identity_stops_before_engine_or_game(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["runner", "--out", str(tmp_path), "--camera-mode", "perspective",
        "--canvas-provenance-capture", "--presentation-field-capture",
        "--presentation-field-capture-budget-bytes", str(runner.PRESENTATION_FIELD_REQUIRED_PEAK_BYTES)])
    monkeypatch.setattr(runner, "_repo_provenance", lambda **kwargs: {"git_head": "1" * 40})
    monkeypatch.setattr(runner, "_runtime_source_identity", lambda *args: {"valid": False})
    monkeypatch.setattr(runner, "check_run_budget", lambda *args, **kwargs:
        SimpleNamespace(allowed=True, as_dict=lambda: {}))
    def forbidden(*args, **kwargs):
        pytest.fail("missing executed source identity must prevent engine and game launch")
    monkeypatch.setattr(runner, "_canvas_engine_capabilities", forbidden)
    monkeypatch.setattr(runner, "_context_preflight", forbidden)
    assert runner.main() == 2
    assert not (tmp_path / "engine_capabilities.json").exists()
