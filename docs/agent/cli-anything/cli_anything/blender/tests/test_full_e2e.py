"""End-to-end tests — full CLI workflows through subprocess."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest


def _resolve_cli(name="cli-anything-blender"):
    """Resolve CLI executable path.

    When CLI_ANYTHING_FORCE_INSTALLED=1, assume the command is in PATH.
    Otherwise, try PATH first, fall back to venv bin.
    """
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1":
        return name

    found = shutil.which(name)
    if found:
        return found

    # Check common venv locations
    venv = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), ".venv", "bin", name)
    if os.path.isfile(venv):
        return venv

    pytest.skip(f"{name} not found in PATH")


class TestCLISubprocess:
    """Test the installed CLI command via subprocess."""

    @pytest.fixture(scope="class")
    def cli_path(self):
        return _resolve_cli()

    @pytest.fixture(scope="class")
    def blend_file(self, cli_path, tmp_path_factory):
        """Create a blend file for the test class."""
        tmp = tmp_path_factory.mktemp("e2e")
        path = str(tmp / "e2e_test.blend")
        subprocess.run([cli_path, "new", "-o", path], check=True, capture_output=True)
        return path

    def _run(self, cli_path, *args, json_mode=False):
        """Run CLI command and return (returncode, output)."""
        cmd = [cli_path]
        if json_mode:
            cmd.append("--json")
        cmd.extend(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode, result.stdout, result.stderr

    def _run_json(self, cli_path, *args):
        """Run CLI with --json and return parsed result."""
        rc, stdout, stderr = self._run(cli_path, *args, json_mode=True)
        assert rc == 0, f"CLI failed: {stderr}"
        return json.loads(stdout)

    # ── Version ──────────────────────────────────────────────────

    def test_version(self, cli_path):
        rc, stdout, _ = self._run(cli_path, "version")
        assert rc == 0
        assert "Blender" in stdout

    # ── Help ─────────────────────────────────────────────────────

    def test_help(self, cli_path):
        rc, stdout, _ = self._run(cli_path, "--help")
        assert rc == 0
        assert "CLI-Anything Blender" in stdout

    # ── Project workflow ─────────────────────────────────────────

    def test_new_and_info(self, cli_path, tmp_path):
        path = str(tmp_path / "new_test.blend")
        data = self._run_json(cli_path, "new", "-o", path)
        assert data["ok"]
        assert os.path.isfile(path)

        data = self._run_json(cli_path, "info", path)
        assert data["ok"]
        assert "objects" in data["data"]

    # ── Object CRUD workflow ─────────────────────────────────────

    def test_object_lifecycle(self, cli_path, blend_file):
        # Add
        data = self._run_json(cli_path, "-f", blend_file, "obj", "add", "cube",
                              "--name", "E2ECube", "-l", "1", "2", "3")
        assert data["ok"]
        assert data["data"]["name"] == "E2ECube"

        # List
        data = self._run_json(cli_path, "-f", blend_file, "obj", "list")
        assert data["ok"]
        names = [o["name"] for o in data["data"]]
        assert "E2ECube" in names

        # Transform
        data = self._run_json(cli_path, "-f", blend_file, "obj", "transform",
                              "E2ECube", "-s", "2", "2", "2")
        assert data["ok"]
        assert data["data"]["scale"] == [2.0, 2.0, 2.0]

        # Duplicate
        data = self._run_json(cli_path, "-f", blend_file, "obj", "duplicate",
                              "E2ECube", "--new-name", "E2ECopy")
        assert data["ok"]

        # Delete
        data = self._run_json(cli_path, "-f", blend_file, "obj", "delete", "E2ECopy")
        assert data["ok"]
        assert data["data"]["status"] == "deleted"

    # ── Material workflow ────────────────────────────────────────

    def test_material_workflow(self, cli_path, blend_file):
        # Create
        data = self._run_json(cli_path, "-f", blend_file, "mat", "create",
                              "E2EMat", "--color", "0", "0", "1", "--metallic", "0.5")
        assert data["ok"]

        # List (verify persistence)
        data = self._run_json(cli_path, "-f", blend_file, "mat", "list")
        assert data["ok"]
        names = [m["name"] for m in data["data"]]
        assert "E2EMat" in names

        # Assign (need an object first)
        self._run_json(cli_path, "-f", blend_file, "obj", "add", "sphere",
                       "--name", "MatTarget")
        data = self._run_json(cli_path, "-f", blend_file, "mat", "assign",
                              "MatTarget", "E2EMat")
        assert data["ok"]

    # ── Modifier workflow ────────────────────────────────────────

    def test_modifier_workflow(self, cli_path, blend_file):
        # Ensure object exists
        self._run_json(cli_path, "-f", blend_file, "obj", "add", "cube",
                       "--name", "ModTarget")

        # Add modifier
        data = self._run_json(cli_path, "-f", blend_file, "mod", "add",
                              "ModTarget", "SUBSURF", "-n", "MySub")
        assert data["ok"]

        # List modifiers
        data = self._run_json(cli_path, "-f", blend_file, "mod", "list", "ModTarget")
        assert data["ok"]
        assert any(m["name"] == "MySub" for m in data["data"])

        # Apply
        data = self._run_json(cli_path, "-f", blend_file, "mod", "apply",
                              "ModTarget", "MySub")
        assert data["ok"]
        assert data["data"]["vertices_after"] > data["data"]["vertices_before"]

    # ── Render workflow ──────────────────────────────────────────

    def test_render_image(self, cli_path, blend_file, tmp_path):
        out = str(tmp_path / "e2e_render.png")
        data = self._run_json(cli_path, "-f", blend_file, "render", "image",
                              out, "-r", "160", "120")
        assert data["ok"]
        assert os.path.isfile(out)
        assert os.path.getsize(out) > 0

    def test_render_engines(self, cli_path):
        data = self._run_json(cli_path, "render", "engines")
        assert data["ok"]
        ids = [e["id"] for e in data["data"]]
        assert "CYCLES" in ids

    # ── Export workflow ───────────────────────────────────────────

    def test_export_glb(self, cli_path, blend_file, tmp_path):
        out = str(tmp_path / "e2e_export.glb")
        data = self._run_json(cli_path, "-f", blend_file, "io", "export", out)
        assert data["ok"]
        assert os.path.isfile(out)
        assert os.path.getsize(out) > 100

    def test_formats(self, cli_path):
        data = self._run_json(cli_path, "io", "formats")
        assert data["ok"]
        assert "fbx" in data["data"]["import"]

    # ── Scene workflow ───────────────────────────────────────────

    def test_scene_info(self, cli_path, blend_file):
        data = self._run_json(cli_path, "-f", blend_file, "scene", "info")
        assert data["ok"]
        assert "render" in data["data"]
        assert data["data"]["render"]["engine"] in ("BLENDER_EEVEE_NEXT", "CYCLES")

    def test_scene_settings(self, cli_path, blend_file):
        data = self._run_json(cli_path, "-f", blend_file, "scene", "settings",
                              "-r", "800", "600", "--fps", "30")
        assert data["ok"]
        assert data["data"]["resolution"] == [800, 600]
        assert data["data"]["fps"] == 30

    # ── Animation workflow ───────────────────────────────────────

    def test_animation_timeline(self, cli_path, blend_file):
        data = self._run_json(cli_path, "-f", blend_file, "anim", "timeline")
        assert data["ok"]
        assert "frame_start" in data["data"]
        assert "fps" in data["data"]

    def test_animation_set_range(self, cli_path, blend_file):
        data = self._run_json(cli_path, "-f", blend_file, "anim", "set-range", "5", "50")
        assert data["ok"]
        assert data["data"]["frame_start"] == 5
        assert data["data"]["frame_end"] == 50

    # ── Exec raw Python ──────────────────────────────────────────

    def test_exec(self, cli_path, blend_file):
        data = self._run_json(cli_path, "-f", blend_file, "exec",
                              "import bpy; _data = {'count': len(bpy.data.objects)}")
        assert data["ok"]
        assert isinstance(data["data"]["count"], int)

    # ── Full realistic workflow ──────────────────────────────────

    def test_full_workflow(self, cli_path, tmp_path):
        """Simulate a real agent workflow: create, populate, render, export."""
        blend = str(tmp_path / "workflow.blend")

        # 1. Create new file
        data = self._run_json(cli_path, "new", "-o", blend)
        assert data["ok"]

        # 2. Add objects
        self._run_json(cli_path, "-f", blend, "obj", "add", "monkey",
                       "--name", "Suzanne", "-l", "0", "0", "0")
        self._run_json(cli_path, "-f", blend, "obj", "add", "plane",
                       "--name", "Floor", "-l", "0", "0", "-1")

        # 3. Create and assign material
        self._run_json(cli_path, "-f", blend, "mat", "create", "Gold",
                       "--color", "1", "0.8", "0", "--metallic", "1.0",
                       "--roughness", "0.2")
        self._run_json(cli_path, "-f", blend, "mat", "assign", "Suzanne", "Gold")

        # 4. Add modifier
        self._run_json(cli_path, "-f", blend, "mod", "add", "Suzanne", "SUBSURF")

        # 5. Set render settings
        self._run_json(cli_path, "-f", blend, "scene", "settings",
                       "-r", "320", "240")

        # 6. Render
        render_out = str(tmp_path / "workflow_render.png")
        data = self._run_json(cli_path, "-f", blend, "render", "image", render_out)
        assert data["ok"]
        assert os.path.isfile(render_out)

        # 7. Export
        glb_out = str(tmp_path / "workflow.glb")
        data = self._run_json(cli_path, "-f", blend, "io", "export", glb_out)
        assert data["ok"]
        assert os.path.isfile(glb_out)

        # 8. Verify final state
        data = self._run_json(cli_path, "-f", blend, "obj", "list")
        assert data["ok"]
        names = [o["name"] for o in data["data"]]
        assert "Suzanne" in names
        assert "Floor" in names
