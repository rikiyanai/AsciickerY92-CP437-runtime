"""Unit tests for cli-anything-asciiid core modules.

These tests use synthetic data and mock objects — no asciiid process needed.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# ── Backend tests ────────────────────────────────────────────────────

from cli_anything.asciiid.utils.asciiid_backend import (
    AsciiidNotFound,
    AsciiidProcess,
    run_batch,
    find_asciiid,
)


class TestFindAsciiid:
    def test_finds_binary_in_project_root(self, tmp_path):
        run_dir = tmp_path / ".run"
        run_dir.mkdir()
        binary = run_dir / "asciiid"
        binary.write_text("#!/bin/sh\necho test")
        binary.chmod(0o755)

        result = find_asciiid(str(tmp_path))
        assert result == str(binary.resolve())

    def test_raises_when_not_found(self, tmp_path):
        with pytest.raises(AsciiidNotFound):
            find_asciiid(str(tmp_path))

    def test_raises_when_no_project_root(self):
        with mock.patch("shutil.which", return_value=None):
            with pytest.raises(AsciiidNotFound):
                find_asciiid(None)

    def test_finds_via_path(self, tmp_path):
        with mock.patch("shutil.which", return_value="/usr/bin/asciiid"):
            result = find_asciiid(str(tmp_path))
            assert result == "/usr/bin/asciiid"

    def test_prefers_project_root_over_path(self, tmp_path):
        run_dir = tmp_path / ".run"
        run_dir.mkdir()
        binary = run_dir / "asciiid"
        binary.write_text("#!/bin/sh")
        binary.chmod(0o755)

        with mock.patch("shutil.which", return_value="/usr/bin/asciiid"):
            result = find_asciiid(str(tmp_path))
            assert result == str(binary.resolve())


class TestAsciiidProcess:
    def test_initial_state(self):
        with mock.patch(
            "cli_anything.asciiid.utils.asciiid_backend.daemon_alive",
            return_value=False,
        ), mock.patch(
            "cli_anything.asciiid.utils.asciiid_backend.PID_PATH",
            mock.Mock(exists=mock.Mock(return_value=False)),
        ):
            proc = AsciiidProcess("/fake/asciiid", "/fake/root")
            assert proc.running is False
            assert proc.pid is None

    def test_binary_path_stored(self):
        proc = AsciiidProcess("/path/to/asciiid", "/root")
        assert proc.binary_path == "/path/to/asciiid"
        assert proc.project_root == "/root"

    def test_run_batch_uses_headless_batch_flag(self):
        completed = mock.Mock(returncode=0, stdout="[MCP] ok\n", stderr="")
        with mock.patch(
            "cli_anything.asciiid.utils.asciiid_backend.subprocess.run",
            return_value=completed,
        ) as run_mock:
            result = run_batch(
                ["ECHO hi"],
                binary_path="/tmp/asciiid",
                project_root="/tmp/project",
            )

        assert result["mcp"] == ["ok"]
        assert run_mock.call_args.args[0] == ["/tmp/asciiid", "--headless-batch"]


# ── Session tests ────────────────────────────────────────────────────

from cli_anything.asciiid.core.session import (
    SESSION_FILE,
    clear_session,
    load_session,
    save_session,
    update_session,
)


class TestSession:
    @pytest.fixture(autouse=True)
    def _use_temp_session(self, tmp_path, monkeypatch):
        """Redirect session file to temp directory."""
        test_file = tmp_path / "session.json"
        monkeypatch.setattr(
            "cli_anything.asciiid.core.session.SESSION_FILE", test_file
        )
        monkeypatch.setattr(
            "cli_anything.asciiid.core.session.SESSION_DIR", tmp_path
        )
        self.session_file = test_file

    def test_save_and_load(self):
        # Use current PID so the alive-check passes
        save_session(pid=os.getpid(), binary_path="/bin/asciiid",
                     project_root="/project")
        data = load_session()
        assert data is not None
        assert data["pid"] == os.getpid()
        assert data["binary_path"] == "/bin/asciiid"
        assert data["project_root"] == "/project"

    def test_load_returns_none_when_no_file(self):
        assert load_session() is None

    def test_load_returns_none_for_dead_pid(self):
        save_session(pid=999999999, binary_path="/bin/x",
                     project_root="/p")
        # PID 999999999 should not be alive
        result = load_session()
        assert result is None

    def test_update_session(self):
        # Use current PID so it's "alive"
        save_session(pid=os.getpid(), binary_path="/bin/x",
                     project_root="/p")
        update_session(loaded_map="test.a3d", modified=True)
        data = load_session()
        assert data["loaded_map"] == "test.a3d"
        assert data["modified"] is True

    def test_clear_session(self):
        save_session(pid=os.getpid(), binary_path="/bin/x",
                     project_root="/p")
        clear_session()
        assert load_session() is None

    def test_corrupt_session_file(self):
        self.session_file.write_text("not json{{{")
        assert load_session() is None

    def test_save_includes_timestamps(self):
        save_session(pid=os.getpid(), binary_path="/b",
                     project_root="/p")
        data = load_session()
        assert "started_at" in data
        assert "updated_at" in data
        assert isinstance(data["started_at"], float)


# ── Weather tests ────────────────────────────────────────────────────

from cli_anything.asciiid.core.weather import (
    WEATHER_NAMES,
    WEATHER_STATES,
)
from cli_anything.asciiid.core import camera as camera_mod


class TestWeatherConstants:
    def test_all_states_defined(self):
        assert WEATHER_STATES["clear"] == 0
        assert WEATHER_STATES["light_snow"] == 1
        assert WEATHER_STATES["heavy_snow"] == 2
        assert WEATHER_STATES["blizzard"] == 3

    def test_reverse_mapping(self):
        assert WEATHER_NAMES[0] == "clear"
        assert WEATHER_NAMES[3] == "blizzard"

    def test_round_trip(self):
        for name, val in WEATHER_STATES.items():
            assert WEATHER_NAMES[val] == name

    def test_weather_set_validates_string(self):
        from cli_anything.asciiid.core.weather import set as weather_set

        with mock.patch("cli_anything.asciiid.core.editor.send"):
            with pytest.raises(ValueError, match="Unknown weather"):
                weather_set("tornado")

    def test_weather_set_validates_range(self):
        from cli_anything.asciiid.core.weather import set as weather_set

        with mock.patch("cli_anything.asciiid.core.editor.send"):
            with pytest.raises(ValueError, match="must be 0-3"):
                weather_set(5)


class TestCameraParsing:
    def test_get_parses_mcp_pos_tuple(self):
        with mock.patch(
            "cli_anything.asciiid.core.editor.send",
            return_value=[
                "Received command: GET_CAMERA",
                "Camera: pos=100.00,200.00,300.00 yaw=45.00 pitch=30.00 zoom=0.00",
            ],
        ):
            result = camera_mod.get()
        assert result["x"] == 100.0
        assert result["y"] == 200.0
        assert result["z"] == 300.0
        assert result["yaw"] == 45.0
        assert result["pitch"] == 30.0
        assert result["zoom"] == 0.0


# ── CLI output tests ─────────────────────────────────────────────────

from cli_anything.asciiid.asciiid_cli import _output, _find_project_root


class TestOutputFormatting:
    def test_json_dict(self, capsys):
        _output({"status": "ok", "count": 3}, as_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "ok"
        assert data["count"] == 3

    def test_json_strips_response_key(self, capsys):
        _output({"status": "ok", "response": ["line1"]}, as_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "response" not in data

    def test_human_dict(self, capsys):
        _output({"status": "ok"}, as_json=False)
        out = capsys.readouterr().out
        assert "status: ok" in out

    def test_human_list(self, capsys):
        _output(["item1", "item2"], as_json=False)
        out = capsys.readouterr().out
        assert "item1" in out
        assert "item2" in out


class TestFindProjectRoot:
    def test_finds_root_with_run_dir(self, tmp_path, monkeypatch):
        run_dir = tmp_path / ".run"
        run_dir.mkdir()
        (run_dir / "asciiid").write_text("binary")
        monkeypatch.chdir(tmp_path)

        result = _find_project_root()
        assert result == str(tmp_path)

    def test_returns_cwd_when_not_found(self, tmp_path, monkeypatch):
        # Use a subdirectory that definitely has no .run/asciiid
        empty = tmp_path / "empty_subdir"
        empty.mkdir()
        monkeypatch.chdir(empty)
        # Also patch the module __file__ so it doesn't walk up to real project
        monkeypatch.setattr(
            "cli_anything.asciiid.asciiid_cli.os.path.abspath",
            lambda p: str(empty / "fake_cli.py"),
        )
        result = _find_project_root()
        assert result == str(empty)


# ── Terrain module tests ─────────────────────────────────────────────

from cli_anything.asciiid.core.terrain import set_grid, paint_terrain_poly


class TestTerrainValidation:
    def test_grid_clamps_alpha(self):
        with mock.patch("cli_anything.asciiid.core.editor.send", return_value=[]):
            result = set_grid(5.0)
            assert result["grid_alpha"] == 1.0

            result = set_grid(-1.0)
            assert result["grid_alpha"] == 0.0


class TestPaintTerrainPoly:
    """Tests for paint_terrain_poly core function."""

    def test_happy_path_triangle(self):
        with mock.patch(
            "cli_anything.asciiid.core.editor.send", return_value=["OK"]
        ) as mock_send:
            result = paint_terrain_poly(1, [(0, 0), (10, 0), (5, 10)])
            mock_send.assert_called_once_with(
                "PAINT_TERRAIN_POLY 1 3 0 0 10 0 5 10", timeout=10.0
            )
            assert result["status"] == "painted"
            assert result["mat_id"] == 1
            assert result["vertex_count"] == 3

    def test_edge_min_vertices_3(self):
        with mock.patch(
            "cli_anything.asciiid.core.editor.send", return_value=["OK"]
        ):
            result = paint_terrain_poly(0, [(0, 0), (1, 0), (0, 1)])
            assert result["vertex_count"] == 3

    def test_edge_max_vertices_32(self):
        verts = [(float(i), float(i)) for i in range(32)]
        with mock.patch(
            "cli_anything.asciiid.core.editor.send", return_value=["OK"]
        ):
            result = paint_terrain_poly(255, verts)
            assert result["vertex_count"] == 32

    def test_error_mat_id_negative(self):
        with pytest.raises(ValueError, match="mat_id must be 0-255"):
            paint_terrain_poly(-1, [(0, 0), (1, 0), (0, 1)])

    def test_error_mat_id_over_255(self):
        with pytest.raises(ValueError, match="mat_id must be 0-255"):
            paint_terrain_poly(256, [(0, 0), (1, 0), (0, 1)])

    def test_error_too_few_vertices(self):
        with pytest.raises(ValueError, match="at least 3 vertices"):
            paint_terrain_poly(1, [(0, 0), (1, 0)])

    def test_error_too_many_vertices(self):
        verts = [(float(i), float(i)) for i in range(33)]
        with pytest.raises(ValueError, match="Maximum 32 vertices"):
            paint_terrain_poly(1, verts)

    def test_command_format_with_floats(self):
        with mock.patch(
            "cli_anything.asciiid.core.editor.send", return_value=["OK"]
        ) as mock_send:
            paint_terrain_poly(4, [(1.5, 2.5), (3.5, 4.5), (5.5, 6.5)])
            mock_send.assert_called_once_with(
                "PAINT_TERRAIN_POLY 4 3 1.5 2.5 3.5 4.5 5.5 6.5", timeout=10.0
            )


# ── Placement argument construction ─────────────────────────────────

class TestPlacementArgs:
    def test_place_mesh_constructs_command(self):
        with mock.patch("cli_anything.asciiid.core.editor.send",
                        return_value=["OK"]) as mock_send:
            from cli_anything.asciiid.core.placement import place_mesh
            place_mesh("Tree.akm", 10.0, 20.0, 30.0, 2.0)
            mock_send.assert_called_once_with(
                "PLACE_MESH Tree.akm 10.0 20.0 30.0 2.0", timeout=10.0
            )

    def test_place_sprite_constructs_command(self):
        with mock.patch("cli_anything.asciiid.core.editor.send",
                        return_value=["OK"]) as mock_send:
            from cli_anything.asciiid.core.placement import place_sprite
            place_sprite("npc.xp", 5.0, 10.0, 0.0, 90.0, 1, 2)
            mock_send.assert_called_once_with(
                "PLACE_SPRITE npc.xp 5.0 10.0 0.0 90.0 1 2", timeout=10.0
            )

    def test_set_active_sprite_constructs_command(self):
        with mock.patch("cli_anything.asciiid.core.editor.send",
                        return_value=["OK"]) as mock_send:
            from cli_anything.asciiid.core.placement import set_active_sprite
            set_active_sprite("hero.xp")
            mock_send.assert_called_once_with(
                "SET_ACTIVE_SPRITE hero.xp", timeout=10.0
            )


# ── World response parsing ──────────────────────────────────────────

class TestWorldParsing:
    def test_list_instances_parses_lines(self):
        with mock.patch("cli_anything.asciiid.core.editor.send",
                        return_value=["Tree 1 100.0 200.0 0.0",
                                      "Rock 2 50.0 60.0 10.0"]):
            from cli_anything.asciiid.core.world import list_instances
            instances = list_instances()
            assert len(instances) == 2
            assert instances[0]["name"] == "Tree"
            assert instances[1]["x"] == 50.0

    def test_echo_returns_text(self):
        with mock.patch("cli_anything.asciiid.core.editor.send",
                        return_value=["hello world"]):
            from cli_anything.asciiid.core.world import echo
            result = echo("hello world")
            assert result == "hello world"

    def test_echo_empty_response(self):
        with mock.patch("cli_anything.asciiid.core.editor.send",
                        return_value=[]):
            from cli_anything.asciiid.core.world import echo
            result = echo("test")
            assert result == ""
