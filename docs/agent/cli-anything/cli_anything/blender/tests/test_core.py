"""Unit tests for core modules — uses real Blender subprocess."""

import json
import os
import tempfile
import shutil
from unittest import mock

import pytest

from cli_anything.blender.core.bridge import BlenderBridge, _find_blender
from cli_anything.blender.core import state


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def blender_path():
    """Find Blender once per session."""
    try:
        return _find_blender()
    except FileNotFoundError:
        pytest.skip("Blender not found")


@pytest.fixture
def blend_file(blender_path, tmp_path):
    """Create a fresh blend file with default objects for each test."""
    path = str(tmp_path / "test.blend")
    bridge = BlenderBridge(blender_path=blender_path)
    result = bridge.execute(f"""
import bpy
bpy.ops.wm.save_as_mainfile(filepath={path!r})
_data = {{"path": {path!r}}}
""")
    assert result["ok"], result
    return path


@pytest.fixture(autouse=True)
def clean_state():
    """Clean session state before each test."""
    state.clear()
    yield
    state.clear()


# ── Bridge tests ─────────────────────────────────────────────────────

class TestBridge:
    def test_version(self, blender_path):
        bridge = BlenderBridge(blender_path=blender_path)
        v = bridge.version()
        assert "Blender" in v

    def test_execute_simple(self, blender_path):
        bridge = BlenderBridge(blender_path=blender_path)
        result = bridge.execute("_data = {'hello': 'world'}")
        assert result["ok"] is True
        assert result["data"]["hello"] == "world"

    def test_execute_with_bpy(self, blender_path):
        bridge = BlenderBridge(blender_path=blender_path)
        result = bridge.execute("import bpy; _data = {'scene': bpy.context.scene.name}")
        assert result["ok"] is True
        assert isinstance(result["data"]["scene"], str)

    def test_execute_error(self, blender_path):
        bridge = BlenderBridge(blender_path=blender_path)
        result = bridge.execute("raise ValueError('test error')")
        assert result["ok"] is False
        assert "test error" in result["error"]

    def test_execute_with_blend_file(self, blend_file, blender_path):
        bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
        result = bridge.execute("""
import bpy
_data = {"filepath": bpy.data.filepath, "objects": len(bpy.data.objects)}
""")
        assert result["ok"] is True
        assert result["data"]["filepath"] == blend_file


# ── State tests ──────────────────────────────────────────────────────

class TestState:
    def test_set_get_blend_file(self):
        state.set_blend_file("/tmp/test.blend")
        assert state.get_blend_file() == "/tmp/test.blend"

    def test_clear(self):
        state.set_blend_file("/tmp/test.blend")
        state.clear()
        assert state.get_blend_file() is None

    def test_set_none(self):
        state.set_blend_file("/tmp/test.blend")
        state.set_blend_file(None)
        assert state.get_blend_file() is None


# ── Project tests ────────────────────────────────────────────────────

class TestProject:
    def test_open(self, blend_file, blender_path):
        from cli_anything.blender.core import project
        result = project.open_file(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert result["data"]["file"] == blend_file
        assert isinstance(result["data"]["objects"], int)

    def test_info(self, blend_file, blender_path):
        from cli_anything.blender.core import project
        result = project.info(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert "objects" in result["data"]
        assert "materials" in result["data"]
        assert "resolution" in result["data"]

    def test_new(self, blender_path, tmp_path):
        from cli_anything.blender.core import project
        path = str(tmp_path / "new.blend")
        result = project.new(output_path=path, blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(path)

    def test_save_as(self, blend_file, blender_path, tmp_path):
        from cli_anything.blender.core import project
        new_path = str(tmp_path / "copy.blend")
        result = project.save_as(blend_file, new_path, blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(new_path)


# ── Object tests ─────────────────────────────────────────────────────

class TestObjects:
    def test_list(self, blend_file, blender_path):
        from cli_anything.blender.core import objects
        result = objects.list_objects(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    def test_add_cube(self, blend_file, blender_path):
        from cli_anything.blender.core import objects
        result = objects.add_object(blend_file, "cube", name="MyCube",
                                    location=[1, 2, 3], blender_path=blender_path)
        assert result["ok"] is True
        assert result["data"]["name"] == "MyCube"
        assert result["data"]["type"] == "MESH"

    def test_add_invalid_type(self, blend_file, blender_path):
        from cli_anything.blender.core import objects
        result = objects.add_object(blend_file, "nonexistent", blender_path=blender_path)
        assert result["ok"] is False

    def test_delete(self, blend_file, blender_path):
        from cli_anything.blender.core import objects
        # Default scene has objects - get one to delete
        list_result = objects.list_objects(blend_file, blender_path=blender_path)
        if list_result["data"]:
            name = list_result["data"][0]["name"]
            result = objects.delete_object(blend_file, name, blender_path=blender_path)
            assert result["ok"] is True

    def test_transform(self, blend_file, blender_path):
        from cli_anything.blender.core import objects
        list_result = objects.list_objects(blend_file, blender_path=blender_path)
        if list_result["data"]:
            name = list_result["data"][0]["name"]
            result = objects.transform(blend_file, name, location=[5, 5, 5],
                                       blender_path=blender_path)
            assert result["ok"] is True
            assert result["data"]["location"] == [5.0, 5.0, 5.0]

    def test_duplicate(self, blend_file, blender_path):
        from cli_anything.blender.core import objects
        list_result = objects.list_objects(blend_file, blender_path=blender_path)
        mesh_objs = [o for o in list_result["data"] if o["type"] == "MESH"]
        if mesh_objs:
            name = mesh_objs[0]["name"]
            result = objects.duplicate(blend_file, name, new_name="Clone",
                                       blender_path=blender_path)
            assert result["ok"] is True
            assert result["data"]["duplicate"] == "Clone"


# ── Material tests ───────────────────────────────────────────────────

class TestMaterials:
    def test_list(self, blend_file, blender_path):
        from cli_anything.blender.core import materials
        result = materials.list_materials(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert isinstance(result["data"], list)

    def test_create(self, blend_file, blender_path):
        from cli_anything.blender.core import materials
        result = materials.create_material(blend_file, "TestMat",
                                           color=[1, 0, 0],
                                           metallic=0.8,
                                           roughness=0.3,
                                           blender_path=blender_path)
        assert result["ok"] is True
        assert result["data"]["name"] == "TestMat"

    def test_create_and_list(self, blend_file, blender_path):
        from cli_anything.blender.core import materials
        materials.create_material(blend_file, "PersistMat", color=[0, 1, 0],
                                  blender_path=blender_path)
        result = materials.list_materials(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        names = [m["name"] for m in result["data"]]
        assert "PersistMat" in names


# ── IO tests ─────────────────────────────────────────────────────────

class TestIO:
    def test_list_formats(self):
        from cli_anything.blender.core import io
        result = io.list_formats()
        assert result["ok"] is True
        assert "fbx" in result["data"]["import"]
        assert "glb" in result["data"]["export"]

    def test_export_glb(self, blend_file, blender_path, tmp_path):
        from cli_anything.blender.core import io
        out = str(tmp_path / "export.glb")
        result = io.export_file(blend_file, out, blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(out)

    def test_export_fbx(self, blend_file, blender_path, tmp_path):
        from cli_anything.blender.core import io
        out = str(tmp_path / "export.fbx")
        result = io.export_file(blend_file, out, blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(out)

    def test_convert_glb_to_fbx(self, blend_file, blender_path, tmp_path):
        from cli_anything.blender.core import io
        # Export first as GLB
        glb = str(tmp_path / "source.glb")
        io.export_file(blend_file, glb, blender_path=blender_path)
        # Convert GLB -> FBX
        fbx = str(tmp_path / "converted.fbx")
        result = io.convert(glb, fbx, blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(fbx)


# ── Modifier tests ───────────────────────────────────────────────────

class TestModifiers:
    def _ensure_cube(self, blend_file, blender_path):
        """Add a cube if no mesh objects exist."""
        from cli_anything.blender.core import objects
        r = objects.list_objects(blend_file, blender_path=blender_path)
        meshes = [o for o in r["data"] if o["type"] == "MESH"]
        if meshes:
            return meshes[0]["name"]
        objects.add_object(blend_file, "cube", name="TestCube", blender_path=blender_path)
        return "TestCube"

    def test_add_and_list(self, blend_file, blender_path):
        from cli_anything.blender.core import modifiers
        name = self._ensure_cube(blend_file, blender_path)
        result = modifiers.add_modifier(blend_file, name, "SUBSURF", blender_path=blender_path)
        assert result["ok"] is True

        result = modifiers.list_modifiers(blend_file, name, blender_path=blender_path)
        assert result["ok"] is True
        assert len(result["data"]) >= 1

    def test_apply(self, blend_file, blender_path):
        from cli_anything.blender.core import modifiers, objects
        name = self._ensure_cube(blend_file, blender_path)
        modifiers.add_modifier(blend_file, name, "SUBSURF", name="TestSub",
                               blender_path=blender_path)
        result = modifiers.apply_modifier(blend_file, name, "TestSub",
                                          blender_path=blender_path)
        assert result["ok"] is True
        assert result["data"]["vertices_after"] > result["data"]["vertices_before"]


# ── Scene tests ──────────────────────────────────────────────────────

class TestScene:
    def test_list(self, blend_file, blender_path):
        from cli_anything.blender.core import scene
        result = scene.list_scenes(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert len(result["data"]) >= 1

    def test_get(self, blend_file, blender_path):
        from cli_anything.blender.core import scene
        result = scene.get_scene(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert "render" in result["data"]

    def test_set_settings(self, blend_file, blender_path):
        from cli_anything.blender.core import scene
        result = scene.set_render_settings(
            blend_file, resolution=(640, 480), fps=30,
            blender_path=blender_path,
        )
        assert result["ok"] is True
        assert result["data"]["resolution"] == [640, 480]
        assert result["data"]["fps"] == 30


# ── Render tests ─────────────────────────────────────────────────────

class TestRender:
    def test_render_image(self, blend_file, blender_path, tmp_path):
        from cli_anything.blender.core import render
        out = str(tmp_path / "render.png")
        result = render.render_image(blend_file, out, resolution=(160, 120),
                                     blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(out)

    def test_list_engines(self, blender_path):
        from cli_anything.blender.core import render
        result = render.list_engines(blender_path=blender_path)
        assert result["ok"] is True
        ids = [e["id"] for e in result["data"]]
        assert "CYCLES" in ids

    def test_render_transparent(self, blend_file, blender_path, tmp_path):
        from cli_anything.blender.core import render
        out = str(tmp_path / "transparent.png")
        result = render.render_image(blend_file, out, resolution=(160, 120),
                                     transparent=True, blender_path=blender_path)
        assert result["ok"] is True
        assert os.path.isfile(out)


# ── Animation tests ──────────────────────────────────────────────────

class TestAnimation:
    def test_timeline(self, blend_file, blender_path):
        from cli_anything.blender.core import animation
        result = animation.get_timeline(blend_file, blender_path=blender_path)
        assert result["ok"] is True
        assert "frame_start" in result["data"]
        assert "fps" in result["data"]

    def test_set_frame_range(self, blend_file, blender_path):
        from cli_anything.blender.core import animation
        result = animation.set_frame_range(blend_file, 10, 100,
                                           blender_path=blender_path)
        assert result["ok"] is True
        assert result["data"]["frame_start"] == 10
        assert result["data"]["frame_end"] == 100


# ── Addon management tests (mock-based) ─────────────────────────────

from unittest import mock

from cli_anything.blender.core import addons


def _mock_bridge_execute(return_data):
    """Create a mock BlenderBridge whose execute() returns ok + data."""
    return mock.patch(
        "cli_anything.blender.core.addons.BlenderBridge",
        return_value=mock.Mock(
            execute=mock.Mock(return_value={"ok": True, "data": return_data})
        ),
    )


def _mock_bridge_error(error_msg):
    """Create a mock BlenderBridge whose execute() returns ok=False."""
    return mock.patch(
        "cli_anything.blender.core.addons.BlenderBridge",
        return_value=mock.Mock(
            execute=mock.Mock(return_value={"ok": False, "error": error_msg})
        ),
    )


class TestAddonEnable:
    def test_enable_returns_enabled_status(self):
        with _mock_bridge_execute({
            "module": "io_asciicker", "status": "enabled",
            "loaded": True, "enabled": True, "bl_info": {"name": "Asciicker"},
        }):
            result = addons.enable_addon("io_asciicker")
            assert result["ok"] is True
            assert result["data"]["module"] == "io_asciicker"
            assert result["data"]["status"] == "enabled"

    def test_enable_already_enabled_is_idempotent(self):
        with _mock_bridge_execute({
            "module": "io_asciicker", "status": "enabled",
            "loaded": True, "enabled": True, "bl_info": {},
        }):
            result = addons.enable_addon("io_asciicker")
            assert result["ok"] is True
            assert result["data"]["status"] == "enabled"

    def test_enable_nonexistent_addon_returns_error(self):
        with _mock_bridge_error("No module named 'nonexistent_addon'"):
            result = addons.enable_addon("nonexistent_addon")
            assert result["ok"] is False
            assert "nonexistent_addon" in result["error"]


class TestAddonDisable:
    def test_disable_returns_disabled_status(self):
        with _mock_bridge_execute({
            "module": "io_asciicker", "status": "disabled",
            "loaded": False, "enabled": False,
        }):
            result = addons.disable_addon("io_asciicker")
            assert result["ok"] is True
            assert result["data"]["status"] == "disabled"


class TestAddonList:
    def test_list_returns_addon_dicts(self):
        with _mock_bridge_execute({
            "addons": [
                {"module": "io_asciicker", "enabled": True, "loaded": True,
                 "name": "Asciicker", "version": [0, 1], "category": "Import-Export"},
            ],
            "count": 1,
        }):
            result = addons.list_addons()
            assert result["ok"] is True
            assert len(result["data"]["addons"]) == 1
            assert result["data"]["addons"][0]["module"] == "io_asciicker"


class TestAddonStatus:
    def test_status_returns_addon_info(self):
        with _mock_bridge_execute({
            "module": "io_asciicker", "found": True,
            "enabled": True, "loaded": True,
            "bl_info": {"name": "Asciicker", "version": [0, 1]},
        }):
            result = addons.addon_status("io_asciicker")
            assert result["ok"] is True
            assert result["data"]["found"] is True
            assert result["data"]["enabled"] is True


# ── A3D/AKM format support tests (mock-based) ───────────────────────

from cli_anything.blender.core import io as io_mod


def _capture_bridge_script():
    """Mock BlenderBridge to capture the script passed to execute()."""
    mock_bridge = mock.Mock()
    mock_bridge.execute.return_value = {"ok": True, "data": {"status": "done"}}
    return mock.patch(
        "cli_anything.blender.core.io.BlenderBridge",
        return_value=mock_bridge,
    ), mock_bridge


def _capture_osm_bridge_script():
    """Mock BlenderBridge for cli_anything.blender.core.osm script capture."""
    mock_bridge = mock.Mock()
    mock_bridge.execute.return_value = {"ok": True, "data": {"status": "done"}}
    return mock.patch(
        "cli_anything.blender.core.osm.BlenderBridge",
        return_value=mock_bridge,
    ), mock_bridge


class TestA3DImportExport:
    def test_a3d_in_import_formats(self):
        assert "a3d" in io_mod.IMPORT_FORMATS

    def test_a3d_in_export_formats(self):
        assert "a3d" in io_mod.EXPORT_FORMATS

    def test_akm_in_import_formats(self):
        assert "akm" in io_mod.IMPORT_FORMATS

    def test_akm_in_export_formats(self):
        assert "akm" in io_mod.EXPORT_FORMATS

    def test_import_a3d_injects_addon_enable(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.import_file("/tmp/map.a3d", blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "addon_utils.enable" in script
            assert "'io_asciicker'" in script
            assert "import_scene.a3d" in script

    def test_export_a3d_injects_addon_enable(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.export_file("/tmp/test.blend", "/tmp/out.a3d",
                               blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "addon_utils.enable" in script
            assert "'io_asciicker'" in script
            assert "export_scene.a3d" in script

    def test_import_fbx_no_addon_enable(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.import_file("/tmp/model.fbx", blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "addon_utils" not in script

    def test_convert_a3d_to_glb_injects_addon_enable(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.convert("/tmp/map.a3d", "/tmp/out.glb",
                           blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "addon_utils.enable" in script
            assert "'io_asciicker'" in script

    def test_convert_fbx_to_a3d_injects_addon_enable(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.convert("/tmp/model.fbx", "/tmp/out.a3d",
                           blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "addon_utils.enable" in script
            assert "'io_asciicker'" in script


class TestOsmFullPipelineScript:
    def test_full_pipeline_normalizes_all_building_origins(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "_set_object_origins_to_bounds" in script
            assert "bldg_final = [obj for obj in building_list if obj.type == 'MESH']" in script

    def test_full_pipeline_marks_all_post_rename_buildings(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "_set_pipeline_building_marker(bldg_final)" in script
            assert "if o.type == 'MESH' and o.get('asciicker_pipeline_building')" in script

    def test_full_pipeline_keeps_local_mesh_geometry_owner(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "_ground_world_z" in script
            assert "_localize_world_footprint" in script
            assert "osm_footprint_xy_json" in script
            assert "_ear_clip_polygon([(p[0], p[1]) for p in local_footprint])" in script
            assert "obj.data.from_pydata(verts, [], faces)" in script
            assert "obj.matrix_world = obj.matrix_world.__class__.Identity(4)" not in script

    def test_full_pipeline_simplifies_noisy_building_footprints_before_bake(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "_simplify_osm_building_footprint" in script
            assert "_douglas_peucker" in script
            assert "simplified = _simplify_osm_building_footprint(footprint)" in script

    def test_full_pipeline_skips_facade_painting_for_deferred_building_bake(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                building_specs_output="/tmp/buildings.json",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "skip_paint_buildings_for_bake" in script
            assert "if building_specs_output:" in script
            assert "osm_bake_footprint_xy_json" in script
            assert "bake_footprint" in script
            assert "bake_height" in script
            assert '"bake_material_id": 5' in script

    def test_full_pipeline_clean_step_preserves_marker_owned_buildings(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "from io_asciicker.tools.osm_pipeline import _is_pipeline_building" in script
            assert "if _is_pipeline_building(obj):" in script
            assert "building_prefixes = ('none_buildings', 'building_')" not in script

    def test_full_pipeline_can_defer_buildings_with_marker_carriers(self):
        from cli_anything.blender.core import osm as osm_mod

        patcher, mock_bridge = _capture_osm_bridge_script()
        with patcher:
            osm_mod.full_pipeline(
                "/tmp/test.blend",
                "/tmp/meshes",
                "/tmp/out.a3d",
                building_specs_output="/tmp/buildings.json",
                terrain_metadata_output="/tmp/terrain.json",
                blender_path="/usr/bin/blender",
            )
            script = mock_bridge.execute.call_args[0][0]
            assert "building_specs_output = '/tmp/buildings.json'" in script
            assert "terrain_metadata_output = '/tmp/terrain.json'" in script
            assert "marker_obj['a3d_marker_only'] = True" in script
            assert "steps_done.append(f\"buildings_deferred({len(payload)})\")" in script

    def test_convert_a3d_to_a3d_deduplicates_addon_enable(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.convert("/tmp/in.a3d", "/tmp/out.a3d",
                           blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            # Should only have one enable call, not two
            assert script.count("addon_utils.enable") == 1

    def test_akm_export_includes_axis_orientation(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.export_file("/tmp/test.blend", "/tmp/out.akm",
                               blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "axis_forward='Y'" in script
            assert "axis_up='Z'" in script

    def test_akm_import_uses_mesh_category(self):
        patcher, mock_bridge = _capture_bridge_script()
        with patcher:
            io_mod.import_file("/tmp/model.akm", blender_path="/usr/bin/blender")
            script = mock_bridge.execute.call_args[0][0]
            assert "import_mesh.akm" in script
