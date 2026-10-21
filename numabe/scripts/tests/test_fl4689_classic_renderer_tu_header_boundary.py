from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_server_links_classic_port_owner_without_fl4512_product():
    makefile = (ROOT / "makefile_server").read_text()
    assert makefile.count("apps/classic-runtime/classic_product_adapter.cpp") == 1
    assert "-DASCIICKER_RENDERER_CLASSIC_CP437=1" in makefile
    assert "apps/fl4512-runtime/fl4512_product_sources.mk" not in makefile
    scene = (ROOT / "engine/render/render_scene.cpp").read_text()
    assert 'printf("[FL-4131] preharri_extended_writes_blocked=' not in scene


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_renderer_interface_does_not_import_authoring_vocabulary():
    """Compile the production Interface, not a symbol-name proxy (FL-4512)."""
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    # Re-definitions are intentional: they fail if the renderer header starts
    # importing complete product authoring types again, even via another header.
    source = '''
#include "engine/render/renderer_ports_v1.h"
struct Fl4131HarriMatProfile {};
struct Fl4131RuntimeHarriCounterSnapshot {};
struct Fl4512ProfileWorkspaceV1 {};
struct ProfileKeyV1 {};
void AsciickerProductInstallV1() {
    AsciickerProductPortsV1 ports = {};
    ports.version = 1;
    AsciickerProductSetPortsV1(&ports);
}
int main() { return AsciickerAuthoring() != nullptr; }
'''
    result = subprocess.run(
        [compiler, "-std=c++11", "-I", str(ROOT), "-x", "c++", "-fsyntax-only", "-"],
        input=source, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_classic_adapter_has_no_authoring_stub_owner():
    source = read("apps/classic-runtime/classic_product_adapter.cpp")
    assert "ClassicAuthoring" not in source
    assert "Fl4131HarriMatProfile" not in source
    assert "Fl4512ProfileWorkspaceV1" not in source
    assert "NULL, // FL-4512: Classic has no authoring capability" in source


def test_authoring_consumers_import_their_interface_explicitly():
    assert '#include "authoring_ports_v1.h"' in read("apps/asciicker-presenter/asciiid.cpp")
    assert '#include "../../../engine/render/authoring_ports_v1.h"' in read(
        "apps/fl4512-runtime/engine/fl4512_neutral_adapter.cpp"
    )


def test_render_resolve_owner_headers_are_nonclassic_only():
    source = read("engine/render/render_resolve.cpp")

    # Single neutral seam: the shared resolver includes the neutral runtime
    # interface (unguarded; Classic-safe) and no Current or deleted-owner
    # include. (Provenance comments may name the Current owners; only
    # #include lines are load-bearing for the boundary.)
    assert '#include "renderer_ports_v1.h"' in source
    includes = [
        line for line in source.splitlines()
        if line.strip().startswith("#include")
    ]
    for forbidden in (
        "authoring_ports_v1.h",
        "apps/fl4512-runtime/",
        "fl4512_spacetime_track_owner.h",
        "fl4512_structural_sequence",
        "fl4512_scene_fact_builder.h",
        "fl4131_harri_gpu_bridge.h",
        "fl4512_u7_topology.h",
        "fl4693_product_glyph_program.h",
    ):
        assert all(forbidden not in line for line in includes)

    # The four scene-fact writers have one owner each (the Current builder);
    # the resolver reaches them only through hook slots, and tuple staging
    # for the resolve path runs module-side (the shared staging adapter left
    # with the dispatch it served).
    for writer in (
        "WriteFl4131BridgeCellAttribution",
        "WriteFl4512MeshToneBridgeCell",
        "WriteFl4260BridgeCellFacts",
        "Fl4683WriteSceneFactFromResolverInputV1",
        "Fl4693FinalTupleStageInputV1",
    ):
        assert writer not in source
    assert "resolve_automap_cell" in source
    assert "resolve_terrain_cell" in source

def test_render_sprite_blit_final_tuple_header_is_nonclassic_only():
    source = read("engine/render/render_sprite_blit.cpp")

    # The deleted-owner transport include is gone; the shared blit crosses
    # only the neutral runtime interface and includes no product path.
    assert '#include "fl4693_final_tuple_transport.h"' not in source
    assert '#include "renderer_ports_v1.h"' in source
    includes = [
        line for line in source.splitlines()
        if line.strip().startswith("#include")
    ]
    assert all("apps/fl4512-runtime/" not in line for line in includes)


def test_classic_presenter_has_no_fake_unicode_bridge_owner():
    presenter = read("platform/terminal_gl_present.cpp")
    resolve = read("engine/render/render_resolve.cpp")
    sprites = read("engine/render/render_sprite_blit.cpp")
    adapter = read("apps/classic-runtime/classic_product_adapter.cpp")

    for source in (presenter, resolve, sprites):
        assert "g_classic_cp437_presenter_bridge_state" not in source

    # The Classic final-cell copy left the shared terminal TU for the
    # Classic adapter: shared keeps upload only, the adapter owns the
    # direct CP437 copy exactly once per frame.
    assert "final_glyphs[index] = (GlyphId)cells[index].gl;" in adapter
    assert "void ClassicCommitPresentationCells(" in adapter
    assert "term->final_glyph_buf[index] = (GlyphId)term->buf[index].gl;" not in presenter
    assert "uniform sampler2D font;" in presenter
    assert "glBindTexture(GL_TEXTURE_2D, fnt_tex);" in presenter

def test_classic_terminal_depfile_has_no_unicode_renderer_headers():
    depfile = ROOT / ".d_game_classic/platform/terminal_gl_present.d"
    if not depfile.exists():
        return

    text = depfile.read_text(errors="ignore")
    # The terminal reaches the product only through neutral ports, so the
    # Classic dep sees no product path. The neutral ports header itself is
    # product-neutral and Classic-safe, so its presence is allowed.
    assert "engine/render/fl4512_runtime_interface.h" not in text
    for forbidden in (
        "apps/fl4512-runtime/",
        "fl4512_spacetime_track_owner.h",
        "fl4512_structural_sequence",
        "fl4512_scene_fact_builder.h",
        "fl4131_pre_glyph_scene.h",
        "fl4693_native_final_cell_adapter.h",
        "fl4131_harri_gpu_bridge.h",
        "fl4512_u7_topology.h",
        "fl4693_product_glyph_program.h",
        "glyph_graph_runtime_adapter.h",
    ):
        assert forbidden not in text


def test_render_internal_spacetime_owner_header_is_nonclassic_only():
    source = read("engine/render/render_internal.h")

    # The temporal owner left the shared header: no deleted-owner include,
    assert "AsciickerProductPortsCached()" in source
    # interface-owned state pointer with neutral lifecycle.
    assert "fl4512_spacetime_track_owner.h" not in source
    assert "Fl4512SpacetimeTrackStoreV1 fl4512_spacetime_track_store" not in source
    assert "void* product_runtime_state;" in source

def test_application_unicode_headers_are_nonclassic_only():
    game_app = read("engine/game_app.cpp")
    game_bridge = read("engine/game_render_bridge.cpp")
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"

    # game_app.cpp reaches product startup through the neutral slot (no
    # product include anywhere in shared TUs); the old inline
    # profile-workspace block is gone.
    assert '#include "../apps/fl4512-runtime/engine/fl4512_profile_workspace.h"' not in game_app
    assert "apps/fl4512-runtime/" not in game_app
    assert '#include "render/renderer_ports_v1.h"' in game_app
    assert "startup_nrt->startup_configure_from_argv" in game_app
    # The bridge crosses only the neutral seam now: interface included,
    assert '#include "render/renderer_ports_v1.h"' in game_bridge
    assert "apps/fl4512-runtime/" not in game_bridge
    assert "close_world_frame" in game_bridge


def test_classic_renderer_has_no_harri_runtime_session_owner():
    internal = read("engine/render/render_internal.h")
    mainmenu = read("engine/mainmenu.cpp")
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"

    # The session config left the shared header for the product mirror: no
    # neutral-named include, no full current-only resolver, no session state
    # on the Renderer.
    assert '#include "fl4131_render_session_config.h"' not in internal
    assert '#include "fl4131_runtime_harri_resolver.h"' not in internal
    # The session state itself left the Renderer for the Current module state.
    assert "Fl4131RenderSessionConfig fl4131_session" not in internal
    assert "fl4131_render_session_config.h" not in mainmenu
    assert "fl4131_runtime_harri_resolver.h" not in mainmenu

    resolve = read("engine/render/render_resolve.cpp")
    # Dead Harri helpers deleted per handoff section 1 (no resurrection):
    # no activity probe, no bundle builder, no reason enum in shared code.
    # The bundle build moved to the Current module; the resolver routes
    # through the neutral automap slot.
    assert "Fl4131HarriIsActive" not in resolve
    assert "static bool Fl4512BuildProjectedFeatureBundle" not in resolve
    assert "resolve_automap_cell" in resolve
    assert "enum Fl4512BundleBuildReason" not in resolve


def test_render_session_config_neutral_single_owner():
    import re

    definition = re.compile(r"struct\s+Fl4131RenderSessionConfig\s*\{")
    mirror = read("apps/fl4512-runtime/engine/fl4131_render_session_config.h")
    resolver = read("apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h")
    internal = read("engine/render/render_internal.h")

    # Exactly one live definition, in the product mirror. The shared header
    # neither defines it nor includes the mirror.
    assert len(definition.findall(mirror)) == 1
    assert definition.search(resolver) is None
    assert definition.search(internal) is None
    assert "fl4131_render_session_config.h" not in internal
    # The resolver consumes the product mirror instead of redefining it.
    assert '#include "fl4131_render_session_config.h"' in resolver
    # 16-byte ABI lock travels with the single definition.
    assert "Fl4131RenderSessionConfigSizeCheck" in mirror
    assert "sizeof(struct Fl4131RenderSessionConfig) == 16" in mirror


def test_classic_depfiles_lost_direct_guarded_renderer_headers():
    depdir = ROOT / ".d_game_classic"
    if not depdir.exists():
        return

    render_resolve = depdir / "engine/render/render_resolve.d"
    render_sprite_blit = depdir / "engine/render/render_sprite_blit.d"
    if not render_resolve.exists() or not render_sprite_blit.exists():
        return

    resolve_text = render_resolve.read_text(errors="ignore")
    sprite_text = render_sprite_blit.read_text(errors="ignore")
    assert "apps/fl4512-runtime/engine/fl4131_harri_resolver.h" not in resolve_text
    assert "apps/fl4512-runtime/engine/fl4512_u7_topology.h" not in resolve_text
    assert "apps/fl4512-runtime/engine/fl4512_u7_score.h" not in resolve_text
    assert "platform/fl4684_scene_fact_emission_census.h" not in resolve_text
    assert "engine/render/fl4693_final_tuple_transport.h" not in sprite_text


def test_classic_application_depfiles_exclude_unicode_owner_headers():
    depdir = ROOT / ".d_game_classic/engine"
    game_app = depdir / "game_app.d"
    game_bridge = depdir / "game_render_bridge.d"
    if not game_app.exists() or not game_bridge.exists():
        return

    app_text = game_app.read_text(errors="ignore")
    bridge_text = game_bridge.read_text(errors="ignore")
    assert "engine/fl4512_profile_workspace.h" not in app_text
    assert "engine/render/fl4693_final_tuple_transport.h" not in bridge_text
    assert "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h" not in bridge_text


def test_classic_depfiles_exclude_harri_runtime_owner():
    depdir = ROOT / ".d_game_classic"
    if not depdir.exists():
        return

    dep_text = "\n".join(path.read_text(errors="ignore") for path in depdir.rglob("*.d"))
    assert "fl4131_runtime_harri_resolver.h" not in dep_text


def test_classic_frame_input_excludes_unicode_bridge_record_headers():
    frame_input = read("engine/render/render_frame_input.h")
    internal = read("engine/render/render_internal.h")

    # The product record includes left the neutral header outright (not
    # merely guarded): shared TUs reach those records only product-side.
    for token in (
        '#include "../../platform/fl4683_scene_fact_record.h"',
        '#include "../../platform/fl4684_active_stroke_reachability_totals.h"',
        "fl4683_scene_fact_record.h",
        "fl4684_active_stroke_reachability_totals.h",
    ):
        assert token not in frame_input

    # The reachability record itself left the Renderer for the Current module
    # state; the neutral header keeps no Current-typed member.
    assert "Fl4684ActiveStrokeReachabilityTotalsV1" not in internal


def test_classic_depfiles_exclude_unicode_bridge_record_headers():
    depdir = ROOT / ".d_game_classic"
    if not depdir.exists():
        return

    dep_text = "\n".join(path.read_text(errors="ignore") for path in depdir.rglob("*.d"))
    assert "fl4683_scene_fact_record.h" not in dep_text
    assert "fl4684_active_stroke_reachability_totals.h" not in dep_text


def test_classic_headers_exclude_projected_feature_and_sequence_island():
    frame_input = read("engine/render/render_frame_input.h")
    internal = read("engine/render/render_internal.h")
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"

    # Deleted-owner includes are gone from both shared headers.
    for source, token in (
        (frame_input, "fl4512_structural_sequence_input.h"),
        (internal, "fl4512_structural_sequence_input.h"),
        (internal, "fl4512_spacetime_track_owner.h"),
    ):
        assert token not in source

    # Aspect fact ownership lives product-side (Fl4512RuntimeStateV1): no
    # aspect field, reset, or product type remains on the shared Renderer.
    for token in (
        "fl4512_aspect_qv3.h",
        "Fl4512AspectPolarFactV3",
        "mesh_aspect_authority_valid",
        "mesh_aspect_cell_index",
        "mesh_aspect_region_id",
        "mesh_aspect_q15",
        "mesh_aspect_polar",
        "mesh_aspect_asset_id",
    ):
        assert token not in internal

    # Projected-feature and sequence state left the Renderer for the module.
    for token in (
        "struct Fl4512ProjectedSurfaceFeatureV1",
        "Fl4512SequenceCellClaimV2* fl4512_sequence_cell_claims",
        "void FinalizeProjectedSurfaceFeatures()",
        '#include "projected_surface_feature.h"',
    ):
        assert token not in internal


def test_classic_world_pass_excludes_projected_feature_producers():
    world_pass = read("engine/render/render_world_pass.cpp")
    scene = read("engine/render/render_scene.cpp")

    # Producers moved to the Current module; the shared pass keeps no
    # definitions and includes no Current owner.
    for token in (
        "Fl4512ProjectScaleAtLocalAnchorV1",
        "Fl4512RegisterProjectedTriangle(r,",
        "Renderer::FinalizeProjectedSurfaceFeatures",
        "Fl4512ProduceVisibleBoundarySilhouetteLatticeV1(Renderer* r)",
        "Fl4512FinalizeActiveStrokeFrameV2(Renderer* r)",
        "apps/fl4512-runtime/",
        "fl4512_spacetime_track_owner.h",
        "fl4512_structural_sequence",
        "fl4512_mesh_differential.h",
    ):
        assert token not in world_pass
    # The shared pass routes through neutral slots instead.
    for token in (
        "register_projected_triangle",
        "mesh_submit_begin",
        "mesh_submit_end",
        "capture_stroke_reprojection",
        "mesh_tone_begin_face",
        "mesh_tone_write_sample",
        "mesh_tone_compute_vertex",
    ):
        assert token in world_pass

    # The scene frame routes resets + finalization + pre-glyph build through
    # hooks; concrete Current state is gone from the frame function.
    for token in (
        "begin_frame",
        "finalize_projected_features",
        "build_pre_glyph",
    ):
        assert token in scene
    for token in (
        "r->projected_surface_feature_count = 0",
        "r->fl4512_active_stroke_reprojection_count",
        "r->FinalizeProjectedSurfaceFeatures()",
    ):
        assert token not in scene

    for retired_shared_name in (
        "Fl4512Fnv1a64",
        "Fl4512ClampInt",
        "Fl4512MeshInstanceUid",
        "Fl4512StablePrimitiveId",
    ):
        assert retired_shared_name not in world_pass
    for shared_name in (
        "RenderFnv1a64",
        "RenderClampInt",
        "RenderMeshInstanceUid",
        "RenderStablePrimitiveId",
    ):
        assert shared_name in world_pass


def test_classic_depfiles_exclude_projected_feature_and_sequence_headers():
    depdir = ROOT / ".d_game_classic"
    if not depdir.exists():
        return

    dep_text = "\n".join(path.read_text(errors="ignore") for path in depdir.rglob("*.d"))
    for forbidden in (
        "engine/render/fl4512_aspect_qv3.h",
        "engine/render/fl4512_projected_scale.h",
        "engine/render/fl4512_structural_sequence_input.h",
        "apps/fl4512-runtime/engine/fl4512_projected_feature_extent.h",
        "apps/fl4512-runtime/engine/fl4693_runtime_epoch.h",
    ):
        assert forbidden not in dep_text
    assert "apps/fl4512-runtime/" not in dep_text


def test_classic_preprocessed_world_pass_excludes_projected_feature_types():
    preprocessed = ROOT / ".o_game_classic/engine/render/render_world_pass.ii"
    if not preprocessed.exists():
        return

    text = preprocessed.read_text(errors="ignore")
    for forbidden in (
        "Fl4512ProjectedScaleFactV1",
        "Fl4512AspectPolarFactV3",
        "Fl4512ProjectedSurfaceFeatureV1",
    ):
        assert forbidden not in text


def test_classic_excludes_fl4512_continuous_tone_sidecar_owner():
    internal = read("engine/render/render_internal.h")
    scene = read("engine/render/render_scene.cpp")
    resolve = read("engine/render/render_resolve.cpp")
    world_pass = read("engine/render/render_world_pass.cpp")
    guard = "#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)"

    # Live FL tone state left shared code for the product (handoff ss5/2):
    # the sidecar struct, the normal-matrix scratch, the writer spelling,
    # and the shared-side normal-matrix write are all gone from shared TUs.
    for source, token in (
        (internal, "struct Fl4512MeshToneSample"),
        (internal, "double mesh_normal_tm[16]"),
        (internal, "mesh_normal_tm_valid"),
        (world_pass, "void WriteMeshTone(Sample* s, int16_t mu_q15)"),
        (world_pass, "Fl4512MeshToneSample* mesh_tone_samples"),
        (world_pass, "Fl4512MeshToneSample"),
        (world_pass, "mesh_normal_tm"),
        (world_pass, "r->mesh_normal_tm_valid = Invert"),
    ):
        assert token not in source
    # The sidecar STORAGE left the Renderer for the Current module state;
    # shared sides route through neutral tone slots.
    for source, token in (
        (internal, "Fl4512MeshToneSample* mesh_tone_samples;"),
        (scene, "r->mesh_tone_samples = (Fl4512MeshToneSample*)malloc"),
        (resolve, "static inline void WriteFl4512MeshToneBridgeCell"),
    ):
        assert token not in source
    assert "mesh_tone_begin_face" in world_pass
    assert "mesh_tone_write_sample" in world_pass
    assert "mesh_tone_compute_vertex" in world_pass
    assert "resolve_terrain_cell" in resolve

    # The shared FL-4652 upstream tonal byte still owns Sample::diffuse in both
    # products; this cut removes only FL-4512's additional continuous-tone data.
    # No product-selection guard remains in the shared world pass: both
    # products execute the same upstream-tonal lines through product ports.
    tonal_start = world_pass.index("Fl4652UpstreamTonalBaseV1 upstream_tonal_base")
    tonal_end = world_pass.index("float inst_n[4];", tonal_start)
    tonal_block = world_pass[tonal_start:tonal_end]
    assert "Fl4652ComputeUpstreamTonalBaseV1" in tonal_block
    assert "shader.upstream_tonal_base_diffuse" in tonal_block
    assert "ASCIICKER_RENDERER_CLASSIC_CP437" not in world_pass


def test_classic_preprocessed_objects_exclude_fl4512_continuous_tone_sidecar():
    object_dir = ROOT / ".o_game_classic"
    if not object_dir.exists():
        return

    preprocessed = "\n".join(
        path.read_text(errors="ignore") for path in object_dir.rglob("*.ii")
    )
    for forbidden in (
        "Fl4512MeshToneSample",
        "WriteFl4512MeshToneBridgeCell",
        "mesh_tone_samples",
        "mesh_normal_tm",
    ):
        assert forbidden not in preprocessed

def test_current_only_harri_gpu_bridge_outside_neutral_frame_input():
    frame_input = read("engine/render/render_frame_input.h")
    # Current-only mutable owner must not live in the neutral shared header,
    # guarded or otherwise: Classic parses this header.
    assert "struct Fl4131HarriGpuBridge" not in frame_input
    assert "g_fl4131_harri_gpu_bridge" not in frame_input

    # Positive ownership: exactly one current-runtime header DEFINES the bridge
    # (forward declarations, e.g. the native adapter's, do not count).
    import re as _re
    candidates = [
        path
        for path in (ROOT / "apps/fl4512-runtime").rglob("*.h")
        if _re.search(r"struct Fl4131HarriGpuBridge\s*\n\{",
                      path.read_text(errors="ignore"))
    ]
    assert len(candidates) == 1
    owner_text = candidates[0].read_text(errors="ignore")
    assert any(
        "extern" in line and "g_fl4131_harri_gpu_bridge" in line
        for line in owner_text.splitlines()
    )

    # The neutral header must not reach into the current-runtime owner either.
    assert candidates[0].name not in frame_input

    # Seam rule: no shared/platform TU includes the Current bridge owner at
    # all (guarded or otherwise); presenter paths route through hook slots.
    presenter = read("platform/terminal_gl_present.cpp")
    assert candidates[0].name not in presenter

    # Classic depfile exclusion: the Classic build must not pull the owner.
    depdir = ROOT / ".d_game_classic"
    if depdir.exists():
        dep_text = "\n".join(
            path.read_text(errors="ignore") for path in depdir.rglob("*.d")
        )
        assert candidates[0].name not in dep_text
