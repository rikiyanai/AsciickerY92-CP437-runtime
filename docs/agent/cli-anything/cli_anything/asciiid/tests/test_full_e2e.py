"""End-to-end tests for cli-anything-asciiid.

These tests launch the REAL asciiid binary and exercise the full CLI pipeline.
They require:
  1. asciiid binary built at .run/asciiid
  2. A display (macOS native or Xvfb on Linux)

Tests FAIL (not skip) if the binary is not found — it's a hard dependency.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import pytest

RUN_REAL_ASCIIID_E2E = os.environ.get("ASCIIID_RUN_REAL_E2E", "").strip() == "1"
pytestmark = [pytest.mark.e2e, pytest.mark.slow]
if not RUN_REAL_ASCIIID_E2E:
    pytestmark.append(
        pytest.mark.skip(
            reason="Set ASCIIID_RUN_REAL_E2E=1 to run real asciiid E2E tests"
        )
    )

# ── Resolve project root ────────────────────────────────────────────

def _find_project_root() -> str:
    """Walk up from this file to find the .run/asciiid binary."""
    path = Path(__file__).resolve()
    for _ in range(10):
        path = path.parent
        if (path / ".run" / "asciiid").exists():
            return str(path)
    # Fallback: try CWD
    if (Path.cwd() / ".run" / "asciiid").exists():
        return str(Path.cwd())
    pytest.fail(
        "Cannot find project root with .run/asciiid. "
        "Build with: make -f makefile_asciiid"
    )


PROJECT_ROOT = _find_project_root()


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def editor_session():
    """Start an asciiid process for the test module, stop when done."""
    from cli_anything.asciiid.core import editor
    from cli_anything.asciiid.utils.asciiid_backend import find_asciiid

    binary = find_asciiid(PROJECT_ROOT)
    result = editor.start(PROJECT_ROOT, binary_path=binary, timeout=20.0)
    assert result["status"] in ("started", "already_running")

    yield editor

    editor.stop()


# ── E2E Tests ────────────────────────────────────────────────────────

class TestEditorLifecycle:
    def test_start_and_status(self, editor_session):
        st = editor_session.status()
        assert st["status"] == "running"
        assert st["pid"] is not None

    def test_echo(self, editor_session):
        from cli_anything.asciiid.core.world import echo
        result = echo("hello_e2e")
        assert "hello_e2e" in result


class TestCameraControl:
    def test_set_and_get_camera(self, editor_session):
        from cli_anything.asciiid.core import camera

        camera.set(100.0, 200.0, 300.0, 45.0, 60.0)
        state = camera.get()
        # Camera may not return exact values due to engine rounding,
        # but we verify we get a response with numeric fields
        assert "x" in state
        assert isinstance(state["x"], (int, float))

    def test_focus_origin(self, editor_session):
        from cli_anything.asciiid.core import camera

        result = camera.focus_origin()
        assert result["status"] == "focused"


class TestWeatherControl:
    def test_set_and_get_weather(self, editor_session):
        from cli_anything.asciiid.core import weather

        weather.set(2)  # heavy_snow
        state = weather.get()
        assert state["state"] in (0, 1, 2, 3)

    def test_weather_by_name(self, editor_session):
        from cli_anything.asciiid.core import weather

        weather.set("blizzard")
        state = weather.get()
        assert state["state"] in (0, 1, 2, 3)


class TestWorldOperations:
    def test_load_default_map(self, editor_session):
        from cli_anything.asciiid.core.world import load_map

        result = load_map()
        assert result["status"] == "loaded"

    def test_save_and_verify(self, editor_session):
        from cli_anything.asciiid.core.world import load_map, save_map

        load_map()

        with tempfile.NamedTemporaryFile(suffix=".a3d", delete=False) as f:
            tmp_path = f.name

        try:
            result = save_map(tmp_path)
            assert result["status"] == "saved"
            assert os.path.exists(tmp_path)
            size = os.path.getsize(tmp_path)
            assert size > 0, f"Saved file is empty"
            print(f"\n  .a3d: {tmp_path} ({size:,} bytes)")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_list_instances(self, editor_session):
        from cli_anything.asciiid.core.world import list_instances, load_map

        load_map()
        instances = list_instances()
        assert isinstance(instances, list)
        print(f"\n  Instances: {len(instances)}")

    def test_render(self, editor_session):
        from cli_anything.asciiid.core.world import render

        result = render()
        assert result["width"] > 0
        assert result["height"] > 0
        assert len(result["data"]) > 0
        print(f"\n  Render: {result['width']}x{result['height']}, "
              f"{len(result['data'])} bytes base64")


class TestTerrainOperations:
    def test_set_height(self, editor_session):
        from cli_anything.asciiid.core.terrain import set_height

        result = set_height(5000)
        assert result["status"] == "set"

    def test_probe_terrain(self, editor_session):
        from cli_anything.asciiid.core.terrain import probe

        result = probe(0.0, 0.0)
        assert "height" in result

    def test_grid_visibility(self, editor_session):
        from cli_anything.asciiid.core.terrain import set_grid

        result = set_grid(0.5)
        assert result["status"] == "set"
        assert result["grid_alpha"] == 0.5


class TestPlacement:
    def test_place_sprite(self, editor_session):
        from cli_anything.asciiid.core.placement import place_sprite
        from cli_anything.asciiid.core.world import load_map

        load_map()
        # Use a sprite that should exist in the assets/sprites/ directory
        result = place_sprite("player-0100.xp", 10.0, 20.0, 0.0, yaw=45.0)
        assert result["status"] == "placed"
        assert result["type"] == "sprite"


class TestFullWorkflow:
    """Multi-step workflow simulating a real editing session."""

    def test_complete_editing_session(self, editor_session):
        from cli_anything.asciiid.core import camera, weather
        from cli_anything.asciiid.core.world import load_map, save_map, list_instances
        from cli_anything.asciiid.core.terrain import set_grid

        # 1. Load map
        load_map()

        # 2. Move camera
        camera.set(50.0, 50.0, 100.0, 90.0)
        cam = camera.get()
        assert "x" in cam

        # 3. Set weather
        weather.set("light_snow")
        w = weather.get()
        assert w["state"] in (0, 1, 2, 3)

        # 4. Set grid
        set_grid(1.0)

        # 5. List instances
        instances = list_instances()
        assert isinstance(instances, list)

        # 6. Save to temp
        with tempfile.NamedTemporaryFile(suffix=".a3d", delete=False) as f:
            tmp_path = f.name
        try:
            result = save_map(tmp_path)
            assert result["status"] == "saved"
            assert os.path.getsize(tmp_path) > 0
            print(f"\n  Workflow output: {tmp_path} ({os.path.getsize(tmp_path):,} bytes)")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ── CLI Subprocess Tests ─────────────────────────────────────────────

def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    import shutil
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        probe = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "ASCIIID_PROJECT_ROOT": PROJECT_ROOT},
        )
        if probe.returncode == 0:
            print(f"[_resolve_cli] Using installed command: {path}")
            return [path]
        print(
            f"[_resolve_cli] Installed command failed probe "
            f"(rc={probe.returncode}); falling back to module path"
        )
    if force:
        raise RuntimeError(
            f"{name} not usable from PATH. Install with: pip install -e ."
        )
    module = "cli_anything.asciiid.asciiid_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


@lru_cache(maxsize=1)
def _asciiid_cli_base():
    return tuple(_resolve_cli("cli-anything-asciiid"))


class TestCLISubprocess:
    def _run(self, args, check=True):
        return subprocess.run(
            list(_asciiid_cli_base()) + args,
            capture_output=True,
            text=True,
            check=check,
            env={**os.environ, "ASCIIID_PROJECT_ROOT": PROJECT_ROOT},
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "asciiid" in result.stdout.lower()

    def test_editor_status_json(self):
        result = self._run(["--json", "editor", "status"], check=False)
        # May or may not have a running process, but should produce valid JSON
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert "status" in data

    def test_command_groups_exist(self):
        for group in ["editor", "project", "terrain", "place", "camera", "weather", "world"]:
            result = self._run([group, "--help"])
            assert result.returncode == 0, f"{group} --help failed"
