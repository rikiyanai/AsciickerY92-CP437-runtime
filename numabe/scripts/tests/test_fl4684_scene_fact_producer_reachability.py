from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAME_INPUT = ROOT / "engine/render/render_frame_input.h"
RENDER_RESOLVE = ROOT / "engine/render/render_resolve.cpp"
HANDSHAKE = ROOT / "apps/fl4512-runtime/platform/fl4683_scene_fact_handshake.h"
RECORD = ROOT / "apps/fl4512-runtime/platform/fl4683_scene_fact_record.h"
SCENE_FACT_BUILDER = ROOT / "apps/fl4512-runtime/engine/fl4512_scene_fact_builder.cpp"
SEAM_MODULE = ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
BUILD_WEB = ROOT / "build-web.sh"
MAKE_GAME = ROOT / "apps/game-fl4512/makefile_game"
MAKE_GAME_MAC = ROOT / "apps/game-fl4512/makefile_game_mac"
WEB_CPP = ROOT / "web/game_web.cpp"
WEB_HTML = ROOT / "web/game_web.html"
WEB_DIAGNOSTICS = ROOT / "web/web_diagnostics.cpp"
GAME_RENDER_BRIDGE = ROOT / "engine/game_render_bridge.cpp"
MAINMENU = ROOT / "engine/mainmenu.cpp"
MAINMENU_H = ROOT / "engine/mainmenu.h"
PRESENTER = ROOT / "platform/terminal_gl_present.cpp"
TERM = ROOT / "platform/term.h"
ASCIIID = ROOT / "apps/asciicker-presenter/asciiid.cpp"
CELL_SCHEMA = ROOT / "schemas/asciiid.cell_inspection.v1.json"
WEB_PROOF = ROOT / "scripts/proofs/proof_fl4684_scene_fact_web_reachability.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    start = source.index(name)
    next_function = source.index("\nstatic inline", start + len(name))
    return source[start:next_function]


def test_scene_fact_record_is_shared_without_importing_selector_contract() -> None:
    # Seam ownership: the record is product-owned and selector-independent;
    # the shared frame stages no scene-fact array (carriers publish
    # product-side through the neutral publish slot).
    record = _read(RECORD)
    handshake = _read(HANDSHAKE)
    frame = _read(FRAME_INPUT)
    assert "struct SceneFactV1" in record
    assert "uint8_t payload_sha256[32]" in record
    assert "independent of the selector" in record
    assert "selector.h" not in record
    assert '#include "fl4683_scene_fact_record.h"' in handshake
    assert '#include "fl4512_u6_presentation_tuple.h"' in handshake
    assert "SceneFactV1" not in frame
    assert "fl4683_scene_fact" not in frame
    assert "publish_frame_carriers" in _read(RENDER_RESOLVE)


def test_material_glyph_plane_free_rejects_unowned_web_reload_pointer(tmp_path: Path) -> None:
    source = tmp_path / "material_glyph_plane_ownership.cpp"
    binary = tmp_path / "material_glyph_plane_ownership"
    source.write_text(
        r"""
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include "engine/material_glyph_plane.h"

static void require(bool condition, const char* message)
{
    if (!condition)
    {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

int main()
{
    material_glyph_plane_free(reinterpret_cast<MaterialGlyphPlane*>(static_cast<uintptr_t>(1)));
    MaterialGlyphPlane* plane = material_glyph_plane_alloc();
    require(plane != nullptr, "alloc returned null");
    require(plane->cells != nullptr, "alloc left cells null");
    material_glyph_plane_init(plane);
    material_glyph_plane_free(plane);
    material_glyph_plane_free(plane);
    std::puts("FL4684_MATERIAL_GLYPH_PLANE_OWNERSHIP_PASS");
    return 0;
}
""",
        encoding="utf-8",
    )
    compile_result = subprocess.run(
        [
            "clang++",
            "-std=c++20",
            "-I",
            str(ROOT),
            str(source),
            str(ROOT / "engine" / "material_glyph_plane.cpp"),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    runtime_result = subprocess.run(
        [str(binary)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert runtime_result.returncode == 0, runtime_result.stderr
    assert "FL4684_MATERIAL_GLYPH_PLANE_OWNERSHIP_PASS" in runtime_result.stdout


def test_shared_resolve_path_materializes_scene_fact_rows_from_live_producers() -> None:
    # Seam ownership: the resolve slots drive the product builder writer;
    # shared resolve holds no writer body.
    source = _read(SCENE_FACT_BUILDER)
    module = _read(SEAM_MODULE)
    resolve = _read(RENDER_RESOLVE)
    writer = source.split("void Fl4683WriteSceneFactFromResolverInputV1", 1)[1]
    assert "Fl4683WriteSceneFactFromResolverInputV1" in source
    assert "Fl4683HashSceneFactPayloadV1" in source
    assert "Fl4208Sha256Final(&ctx, fact->payload_sha256)" in source
    assert "const int gw = g_fl4131_harri_gpu_bridge.fl4683_scene_fact_width" in writer
    assert "const int gh = g_fl4131_harri_gpu_bridge.fl4683_scene_fact_height" in writer
    assert "g_fl4131_harri_gpu_bridge.fl4683_scene_fact_plane_valid" in writer
    assert "g_fl4131_harri_gpu_bridge.actual_w" not in writer
    assert "g_fl4131_harri_gpu_bridge.actual_h" not in writer
    assert "out.frame_id = g_fl4131_harri_gpu_bridge.fl4208_bridge_frame" in source
    assert "out.surface_id = in.surface_id_valid" in source
    assert "out.material_namespace =" in source
    assert "out.material_key = (int16_t)in.mat_id" in source
    assert "out.ramp_bin = Fl4683ClampBinV1(in.elev_quantized, 3)" in source
    assert "out.density_bin = Fl4683ClampBinV1(in.shade_quantized / 4, 3)" in source
    assert "out.direction_bin = Fl4683ClampBinV1(in.direction_idx, 7)" in source
    assert "out.flow_bin = Fl4683ClampBinV1(in.flow_idx, 7)" in source
    assert "out.edge_bin = Fl4683ClampBinV1(in.vertical_relation_idx, 6)" in source
    assert "out.depth_q0_16 = Fl4683DepthQ0_16FromSceneV1(in)" in source
    assert "out.projected_area_q0_16 = in.filled_primitive_valid" in source
    assert "Fl4683WriteSceneFactFromResolverInputV1(in)" in module
    assert "resolve_pre_pass" in resolve


def test_scene_fact_plane_resets_each_normal_resolve_frame() -> None:
    # Seam ownership: plane reset/arm runs in the product pre-pass behind
    # the resolve_pre_pass slot.
    source = _read(SEAM_MODULE)
    assert "g_fl4131_harri_gpu_bridge.fl4683_scene_fact_plane_valid = 0" in source
    assert "g_fl4131_harri_gpu_bridge.fl4683_scene_fact_valid_count = 0" in source
    assert "sizeof(g_fl4131_harri_gpu_bridge.fl4683_scene_fact)" in source
    assert "g_fl4131_harri_gpu_bridge.fl4683_scene_fact_plane_valid = 1" in source
    assert "g_fl4131_harri_gpu_bridge.fl4683_scene_fact_width = (uint16_t)w" in source
    assert "g_fl4131_harri_gpu_bridge.fl4683_scene_fact_height = (uint16_t)h" in source


def test_desktop_and_web_builds_share_the_producer_path() -> None:
    build_web = _read(BUILD_WEB)
    make_game = _read(MAKE_GAME)
    make_game_mac = _read(MAKE_GAME_MAC)
    assert "engine/render/render_resolve.cpp" in build_web
    # Web selects the manifest partitions; the manifest names the owners.
    assert "$FL4512_PARTITION_SRCS" in build_web
    manifest = (
        ROOT / "apps/fl4512-runtime/fl4512_product_sources.mk"
    ).read_text(encoding="utf-8")
    assert "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp" in manifest
    assert "apps/fl4512-runtime/engine/fl4512_mesh_differential.cpp" in manifest
    assert "engine/render/render_resolve.cpp" in make_game
    assert "engine/render/render_resolve.cpp" in make_game_mac
    assert "platform/terminal_gl_present.cpp" not in build_web


def test_fl4684_does_not_activate_loader_selector_presenter_authority() -> None:
    runtime = "\n".join(
        _read(path)
        for path in (
            RENDER_RESOLVE, WEB_CPP, WEB_HTML, WEB_DIAGNOSTICS,
            PRESENTER, TERM, ASCIIID,
        )
    )
    assert "fl4683_scene_fact_handshake.h" not in runtime
    assert "GlyphProgramV1" not in runtime
    assert "Fl4683SelectorInputV1" not in runtime
    assert "Fl4683SelectorInputPreflightV1" not in runtime
    assert "FL4683_RUNTIME_RIGHT_SELECT_FACT_FROM_SCENE_FACT" not in runtime
    assert "fl4676.glyph_fact.v1.json" not in runtime


def test_web_snapshot_exposes_bounded_scene_fact_producer_evidence() -> None:
    source = _read(WEB_DIAGNOSTICS)
    assert "WebDiagnosticsAppendFl4684SceneFactJson" in source
    assert '\\"fl4684_scene_fact\\":{' in source
    assert "diag_nrt->read_scene_fact_rows" in source
    assert "plane_valid" in source
    assert "valid_count" in source
    assert "plane_w == g_web_render_buf_sample_width" in source
    assert "plane_h == g_web_render_buf_sample_height" in source
    assert "first_valid_index" in source
    assert "sample_payload_sha256" in source
    assert "WebDiagnosticsBuildAnsiFrameSnapshotJson" in source
    assert "WebDiagnosticsAppendFl4684SceneFactJson(g_web_cpp_ansi_frame_snapshot_json" in source


def test_web_reachability_proof_uses_normal_play_driver_and_read_only_snapshot() -> None:
    source = _read(WEB_PROOF)
    assert "proof_driver_playwright.js" in source
    assert "process.env.PROOF_MAP = 'assets/a3d/game_map_y8.a3d'" in source
    assert "openProofPage({ waitForRecorderState: true })" in source
    assert "GetCppAnsiFrameSnapshotJson" in source
    assert "fl4684_scene_fact" in source
    assert "valid_count" in source
    assert "sample_payload_sha256" in source
    assert "GlyphProgramV1" not in source
    assert "fl4676.glyph_fact.v1.json" not in source


def test_web_mainmenu_keeps_the_active_frame_owner_alive() -> None:
    header = _read(MAINMENU_H)
    mainmenu = _read(MAINMENU)
    bridge = _read(GAME_RENDER_BRIDGE)
    reset_start = mainmenu.index("static void ResetGame()")
    reset_end = mainmenu.index("// WHY: Main rendering entry point", reset_start)
    reset_body = mainmenu[reset_start:reset_end]
    render_start = mainmenu.index("void MainMenu_Render(Game* active_game")
    render_end = mainmenu.index("mainmenu_stamp = _stamp", render_start)
    render_preamble = mainmenu[render_start:render_end]

    assert "void MainMenu_Render(Game* active_game, uint64_t _stamp, AnsiCell* ptr, int width, int height)" in header
    assert "MainMenu_Render(this, _stamp, ptr, width, height)" in bridge
    assert "game = active_game" in render_preamble
    assert "render_buf = ptr" in render_preamble
    assert "#if !defined(__EMSCRIPTEN__)" in reset_body
    assert "FreeGame(game)" in reset_body
    assert "browser-lifetime Game singleton" in reset_body


def test_scene_fact_rows_are_visible_through_read_only_cell_inspection() -> None:
    term = _read(TERM)
    presenter = _read(PRESENTER)
    asciiid = _read(ASCIIID)
    schema = _read(CELL_SCHEMA)

    # Seam ownership: shared inspection carries opaque scene-fact bytes and
    # plane validity; the product inspect_cell slot fills the typed record.
    assert "scene_fact_payload[136]" in term
    assert "scene_fact_plane_valid" in term
    assert "inspect_nrt->inspect_cell" in presenter
    assert 'cJSON_AddObjectToObject(out, "scene_fact")' in asciiid
    assert 'cJSON_AddBoolToObject(scene_fact, "row_valid"' in asciiid
    assert 'cJSON_AddStringToObject(scene_fact, "payload_sha256"' in asciiid
    assert '"scene_fact": {' in schema
    assert '"payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}' in schema
