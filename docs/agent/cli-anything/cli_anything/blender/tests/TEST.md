# Test Plan — CLI-Anything Blender

## Test Strategy

Two test suites verify the harness at different levels:

1. **Unit tests** (`test_core.py`) — test each core module (bridge, project, objects, materials, io, modifiers, scene, render, animation, state) directly through Python API calls. Each test creates a fresh temp blend file and exercises a single function.

2. **E2E subprocess tests** (`test_full_e2e.py`) — test the installed `cli-anything-blender` command via `subprocess.run()`. Uses `_resolve_cli()` to find the executable. Includes a full realistic workflow test (create → populate → material → modifier → render → export).

Both suites require Blender to be installed (auto-detected or via `BLENDER_PATH`).

## Test Matrix

| Area | Unit Tests | E2E Tests |
|------|-----------|-----------|
| Bridge (subprocess exec) | 5 | - |
| Session state | 3 | - |
| Project (open/info/new/save-as) | 4 | 1 |
| Objects (CRUD, transform, duplicate) | 6 | 1 |
| Materials (create, list, assign) | 3 | 1 |
| Import/Export | 4 | 2 |
| Modifiers (add, list, apply) | 2 | 1 |
| Scene (list, get, settings) | 3 | 2 |
| Render (image, engines, transparent) | 3 | 2 |
| Animation (timeline, range) | 2 | 2 |
| CLI help/version | - | 2 |
| Raw exec | - | 1 |
| Full workflow (realistic) | - | 1 |
| **Total** | **35** | **16** |

## Running Tests

```bash
# Activate venv
source .venv/bin/activate

# All tests
pytest cli_anything/blender/tests/ -v

# Unit only
pytest cli_anything/blender/tests/test_core.py -v

# E2E only
pytest cli_anything/blender/tests/test_full_e2e.py -v

# With installed CLI
CLI_ANYTHING_FORCE_INSTALLED=1 pytest cli_anything/blender/tests/test_full_e2e.py -v
```

## Test Results

```
============================= test session starts ==============================
platform darwin -- Python 3.14.3, pytest-9.0.2

cli_anything/blender/tests/test_core.py::TestBridge::test_version PASSED
cli_anything/blender/tests/test_core.py::TestBridge::test_execute_simple PASSED
cli_anything/blender/tests/test_core.py::TestBridge::test_execute_with_bpy PASSED
cli_anything/blender/tests/test_core.py::TestBridge::test_execute_error PASSED
cli_anything/blender/tests/test_core.py::TestBridge::test_execute_with_blend_file PASSED
cli_anything/blender/tests/test_core.py::TestState::test_set_get_blend_file PASSED
cli_anything/blender/tests/test_core.py::TestState::test_clear PASSED
cli_anything/blender/tests/test_core.py::TestState::test_set_none PASSED
cli_anything/blender/tests/test_core.py::TestProject::test_open PASSED
cli_anything/blender/tests/test_core.py::TestProject::test_info PASSED
cli_anything/blender/tests/test_core.py::TestProject::test_new PASSED
cli_anything/blender/tests/test_core.py::TestProject::test_save_as PASSED
cli_anything/blender/tests/test_core.py::TestObjects::test_list PASSED
cli_anything/blender/tests/test_core.py::TestObjects::test_add_cube PASSED
cli_anything/blender/tests/test_core.py::TestObjects::test_add_invalid_type PASSED
cli_anything/blender/tests/test_core.py::TestObjects::test_delete PASSED
cli_anything/blender/tests/test_core.py::TestObjects::test_transform PASSED
cli_anything/blender/tests/test_core.py::TestObjects::test_duplicate PASSED
cli_anything/blender/tests/test_core.py::TestMaterials::test_list PASSED
cli_anything/blender/tests/test_core.py::TestMaterials::test_create PASSED
cli_anything/blender/tests/test_core.py::TestMaterials::test_create_and_list PASSED
cli_anything/blender/tests/test_core.py::TestIO::test_list_formats PASSED
cli_anything/blender/tests/test_core.py::TestIO::test_export_glb PASSED
cli_anything/blender/tests/test_core.py::TestIO::test_export_fbx PASSED
cli_anything/blender/tests/test_core.py::TestIO::test_convert_glb_to_fbx PASSED
cli_anything/blender/tests/test_core.py::TestModifiers::test_add_and_list PASSED
cli_anything/blender/tests/test_core.py::TestModifiers::test_apply PASSED
cli_anything/blender/tests/test_core.py::TestScene::test_list PASSED
cli_anything/blender/tests/test_core.py::TestScene::test_get PASSED
cli_anything/blender/tests/test_core.py::TestScene::test_set_settings PASSED
cli_anything/blender/tests/test_core.py::TestRender::test_render_image PASSED
cli_anything/blender/tests/test_core.py::TestRender::test_list_engines PASSED
cli_anything/blender/tests/test_core.py::TestRender::test_render_transparent PASSED
cli_anything/blender/tests/test_core.py::TestAnimation::test_timeline PASSED
cli_anything/blender/tests/test_core.py::TestAnimation::test_set_frame_range PASSED

cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_version PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_help PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_new_and_info PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_object_lifecycle PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_material_workflow PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_modifier_workflow PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_render_image PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_render_engines PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_export_glb PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_formats PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_scene_info PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_scene_settings PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_animation_timeline PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_animation_set_range PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_exec PASSED
cli_anything/blender/tests/test_full_e2e.py::TestCLISubprocess::test_full_workflow PASSED

============================= 51 passed ==============================
```

**51/51 tests passing (100%)** — Blender 4.5.0, Python 3.14.3, macOS Darwin 24.5.0
