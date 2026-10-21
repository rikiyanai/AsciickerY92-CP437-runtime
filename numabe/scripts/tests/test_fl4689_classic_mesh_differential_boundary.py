from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()

def test_old_root_owner_is_gone_single_current_runtime_owner():
    assert not (ROOT / "engine/fl4512_mesh_differential.cpp").exists()
    assert not (ROOT / "engine/fl4512_mesh_differential.h").exists()
    assert (ROOT / "apps/fl4512-runtime/engine/fl4512_mesh_differential.cpp").exists()
    assert (ROOT / "apps/fl4512-runtime/engine/fl4512_mesh_differential.h").exists()


def test_core_world_mesh_do_not_own_fl4512_mesh_differential():
    world_internal = read("engine/world_internal.h")
    world_mesh = read("engine/world_mesh.cpp")

    forbidden = (
        "fl4512_mesh_differential.h",
        "Fl4512MeshDifferentialV1",
        "fl4512_differential",
        "Fl4512BuildMeshDifferentialV1",
        "Fl4512FreeMeshDifferentialV1",
        "FL4512_MESH_DIFFERENTIAL_TRACE",
    )
    for token in forbidden:
        assert token not in world_internal
        assert token not in world_mesh


def test_mesh_lifecycle_hook_is_renderer_neutral():
    world_internal = read("engine/world_internal.h")
    world_mesh = read("engine/world_mesh.cpp")

    assert "MeshLifecycleObserverV1" in world_internal
    assert "RegisterMeshLifecycleObserverV1" in world_internal
    assert "NotifyMeshLifecycleObserversV1" in world_internal
    assert "MESH_LIFECYCLE_AFTER_UPDATE" in world_internal
    assert "MESH_LIFECYCLE_BEFORE_DESTROY" in world_internal
    assert "Fl4512" not in world_internal
    assert "fl4512" not in world_internal

    assert "RegisterMeshLifecycleObserverV1" in world_mesh
    assert "NotifyMeshLifecycleObserversV1(this, MESH_LIFECYCLE_AFTER_UPDATE)" in world_mesh


def test_fl4512_renderer_tu_owns_cache_and_lifecycle_registration():
    owner = read("apps/fl4512-runtime/engine/fl4512_mesh_differential.cpp")

    assert "Fl4512MeshDifferentialCacheOwnerV1" in owner
    assert "Fl4512GetMeshDifferentialForRendererV1" in owner
    assert "Fl4512MeshDifferentialLifecycleObserverV1" in owner
    assert "RegisterMeshLifecycleObserverV1(" in owner
    assert "MESH_LIFECYCLE_AFTER_UPDATE" in owner
    assert "MESH_LIFECYCLE_BEFORE_DESTROY" in owner
    assert "Fl4512FreeMeshDifferentialV1(entry.diff)" in owner


def test_renderer_consumer_uses_fl4512_owner_accessor_only():
    render_world_pass = read("engine/render/render_world_pass.cpp")

    assert '#include "../../apps/fl4512-runtime/engine/fl4512_mesh_differential.h"' in render_world_pass
    assert "Fl4512QueryMeshDifferentialFacesV1" in render_world_pass
    assert "fl4512_differential" not in render_world_pass


def test_existing_classic_depfiles_do_not_include_fl4512_mesh_differential():
    depdir = ROOT / ".d_game_classic"
    if not depdir.exists():
        return

    depfiles = list(depdir.rglob("*.d"))
    assert depfiles, "Classic dependency directory exists but contains no .d files"
    assert not (depdir / "apps/fl4512-runtime/engine/fl4512_mesh_differential.d").exists()

    forbidden = "apps/fl4512-runtime/engine/fl4512_mesh_differential.h"
    offenders = [
        str(path.relative_to(ROOT))
        for path in depfiles
        if forbidden in path.read_text(errors="ignore")
    ]
    assert offenders == []
