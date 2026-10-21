import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER_MAKEFILE = ROOT / "makefile_server"
SERVER_BINARY = ROOT / ".run/server"
SERVER_LINK_MAP = ROOT / ".run/server.map"
SERVER_DEP_DIR = ROOT / ".d_server"

EXCLUDED_RENDERER_OWNERS = (
    "platform/fl4683_glyph_program_loader.cpp",
    "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp",
    "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp",
    "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp",
    "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp",
    "apps/fl4512-runtime/engine/fl4131_harri_gpu_bridge.cpp",
    "apps/fl4512-runtime/engine/fl4131_harri_resolver.cpp",
    "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp",
    "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp",
    "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp",
    "apps/fl4512-runtime/engine/fl4512_mesh_differential.cpp",
)
EXCLUDED_RENDERER_HEADERS = (
    "apps/fl4512-runtime/engine/fl4512_projected_feature_extent.h",
    "apps/fl4512-runtime/engine/fl4693_runtime_epoch.h",
)

MIXED_RENDERER_TUS = (
    "engine/render/render_core.cpp",
    "engine/render/render_scene.cpp",
    "engine/render/render_resolve.cpp",
    "engine/render/render_sprite_blit.cpp",
    "engine/render/render_world_pass.cpp",
)

FORBIDDEN_BINARY_OWNER = re.compile(
    r"fl4512|fl4693|fl4683|harri|glyph[._ -]?program|"
    r"final[._ -]?tuple|structural[._ -]?sequence|active[._ -]?stroke",
    re.IGNORECASE,
)


def test_server_source_set_excludes_all_unicode_renderer_owner_units():
    makefile = SERVER_MAKEFILE.read_text()

    for owner in EXCLUDED_RENDERER_OWNERS:
        assert owner not in makefile
    for header in EXCLUDED_RENDERER_HEADERS:
        assert header not in makefile


def test_server_compiles_remaining_mixed_renderer_units_through_classic_guard():
    makefile = SERVER_MAKEFILE.read_text()

    assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" in makefile
    for source in MIXED_RENDERER_TUS:
        assert source in makefile


def test_server_default_build_owns_link_and_binary_audit():
    makefile = SERVER_MAKEFILE.read_text()

    assert "LINK_MAP := .run/server.map" in makefile
    assert "all: actor_visual_reachability_dump_check actor_visual_table_coverage $(BIN) audit_server_renderer_boundary" in makefile
    assert "audit_server_renderer_boundary: $(BIN)" in makefile
    for owner in (Path(path).stem for path in EXCLUDED_RENDERER_OWNERS):
        assert owner in makefile
    for header in (Path(path).stem for path in EXCLUDED_RENDERER_HEADERS):
        assert header in makefile
    assert "nm $(BIN)" in makefile
    assert "strings $(BIN)" in makefile


def test_built_server_has_no_unicode_renderer_owner_symbol_or_text():
    if not SERVER_BINARY.exists() or not SERVER_LINK_MAP.exists():
        return

    assert SERVER_LINK_MAP.stat().st_size > 0
    map_text = SERVER_LINK_MAP.read_text(errors="replace")
    for owner in (Path(path).stem for path in EXCLUDED_RENDERER_OWNERS):
        assert owner.lower() not in map_text.lower()
    for header in (Path(path).stem for path in EXCLUDED_RENDERER_HEADERS):
        assert header.lower() not in map_text.lower()

    nm_text = subprocess.run(
        ["nm", str(SERVER_BINARY)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    strings_text = subprocess.run(
        ["strings", str(SERVER_BINARY)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert not FORBIDDEN_BINARY_OWNER.search(nm_text)
    assert not FORBIDDEN_BINARY_OWNER.search(strings_text)


def test_built_server_dependency_closure_excludes_unicode_renderer_headers():
    if not SERVER_DEP_DIR.exists():
        return

    dependency_text = "\n".join(
        path.read_text(errors="replace") for path in SERVER_DEP_DIR.rglob("*.d")
    )
    assert not FORBIDDEN_BINARY_OWNER.search(dependency_text)
    for header in EXCLUDED_RENDERER_HEADERS:
        assert header not in dependency_text
    assert "apps/fl4512-runtime/" not in dependency_text
