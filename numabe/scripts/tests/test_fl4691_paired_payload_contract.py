#!/usr/bin/env python3
"""FL-4691 paired texel-payload transport contract.

The Product texel-shade main is an existing paint law. Each arm owns its
payload; front may read completed paired paint without cross-writing history.
The GPU ownership test exercises late survivor admission. These checks are source and
preprocessor only: they do not launch Godot, a renderer, a game, or a capture.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
PRODUCT = PROJECT / "render" / "product" / "cell_owner_product.glsl"
EFFECT = PROJECT / "render" / "api" / "ASCIICompositorEffect.gd"
PIPELINE = PROJECT / "render" / "backends" / "perspective" / "PerspectiveProbeBackend.gd"
def _function_source(source: str, name: str) -> str:
    """Return one brace-balanced source function without parsing GLSL."""
    marker = re.search(
        rf"(?m)^\s*(?:void|bool|uint|uvec[234]|vec[234]|int|float|SourceDisplayInput|ProbeSubcellProductRecord)"
        rf"\s+{re.escape(name)}\s*\([^{{]*\)",
        source,
    )
    assert marker, f"missing function {name}"
    open_brace = source.find("{", marker.end())
    assert open_brace >= 0, f"missing body for {name}"
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[marker.start() : index + 1]
    raise AssertionError(f"unbalanced body for {name}")


def _gd_function_source(source: str, name: str) -> str:
    marker = re.search(rf"(?m)^\s*func\s+{re.escape(name)}\s*\([^\n]*", source)
    assert marker, f"missing GDScript function {name}"
    open_brace = source.find(":", marker.start())
    assert open_brace >= 0, f"missing declaration for {name}"
    body_start = open_brace + 1
    next_func = re.search(r"(?m)^\s*func\s+", source[body_start:])
    end = body_start + next_func.start() if next_func else len(source)
    return source[marker.start() : end]


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def test_tree_contours_are_part_of_captured_paint_not_native_election():
    adapter = (PROJECT / "render/product/adapters/capture/product_capture_adapter.glslinc").read_text()
    contour = _function_source(adapter, "fl4359_captured_tree_glyph")
    assert "probe_capture_identity(fl4359_texel_identity, ivec3(xy, address.z))" in contour
    assert "adjacent.y == identity.y" in contour
    assert "tree_contour_glyph(neighbors, trunk, interior)" in contour
    assert "camera" not in contour and "sample_reference_at" not in contour
    main = _function_source(adapter, "fl4359_texel_shade_main")
    assert main.index("rec.r0.z = fl4359_captured_tree_glyph") < main.index(
        "fl4359_texel_pack_payload(rec, epoch)")
    publish = (PROJECT / "render/backends/perspective/publish/perspective_probe_payload_publish.glsl").read_text()
    assert "c.glyph = payload.x & 0xfffu;" in publish
    assert "native_tree_neighbors" not in publish
    assert "tree_contour_glyph(" not in publish
    assert "publish_tree_neighbors" not in publish


def test_mesh_material_default_cannot_masquerade_as_valid_0xdb_paint() -> None:
    material = (PROJECT / "render/product/material/product_material.glslinc").read_text()
    mesh = (PROJECT / "render/product/material/product_mesh_material.glslinc").read_text()
    compiler = (PROJECT / "render/product/payload/product_payload_compiler.glslinc").read_text()
    assert "bool default_survived;" in material
    assert "paint.default_survived = false;" in mesh
    assert "Q3_PREPARATION_DIAG_MATERIAL_DEFAULT_REFUSED" in compiler
    refusal = compiler.index("&& material_paint.default_survived")
    glyph_copy = compiler.index("uint glyph = material_paint.glyph;", refusal)
    assert refusal < glyph_copy
    assert "result.record = q3_probe_payload_empty_record();" in compiler[
        refusal:glyph_copy
    ]


def test_full_product_variants_compile_with_capture_owned_tree_contours(tmp_path):
    from scripts.tests.test_fl4057_perspective_probe_pipeline_contract import (
        compile_stage, compiler, split_stages,
    )
    source = split_stages(_expand_includes(PRODUCT.read_text()))["compute"]
    for arm in ("FL4359_TEXEL_SHADE", "FL4359_SOURCE_EFFECTS", ""):
        variant = source.replace("#version 450", "#version 450\n#define " + arm + " 1") if arm else source
        compile_stage(compiler(), "compute", variant, tmp_path / (arm or "screen"))


def _expand_includes(source: str, stack: tuple[Path, ...] = ()) -> str:
    def replace(match: re.Match[str]) -> str:
        included = PROJECT / match.group(1)
        assert included.exists(), f"missing shader include: {included}"
        assert included not in stack, f"cyclic shader include: {included}"
        return _expand_includes(included.read_text(encoding="utf-8"), (*stack, included))

    return re.sub(
        r'^\s*#include\s+"res://([^\"]+)"\s*$',
        replace,
        source,
        flags=re.MULTILINE,
    )


def _preprocess_texel_shader() -> str:
    """Run the same offline GLSL preprocessor used by the shader toolchain."""
    tool = shutil.which("glslangValidator") or shutil.which("glslang")
    if tool is None:
        pytest.skip("glslangValidator/glslang unavailable")
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    source = re.sub(r"^\s*#\[compute\]\s*$", "", source, flags=re.MULTILINE)
    with tempfile.TemporaryDirectory(prefix="fl4691_paired_payload_") as directory:
        path = Path(directory) / "cell_owner_product.comp"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [tool, "-E", "-S", "comp", "-DFL4359_TEXEL_SHADE", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert result.returncode == 0, result.stdout
        return result.stdout


def test_sky_classification_precedes_paired_geometry_reuse() -> None:
    main = _function_source(
        _expand_includes(PRODUCT.read_text(encoding="utf-8")), "fl4359_texel_shade_main"
    )
    normal_depth = main.index("\tvec4 normal_depth = ")
    radial = main.index("\tfloat radial = (normal_depth.w - fl4359_texel_shade.capture_range.x)")
    sky = main.index("\tif (sky) {")
    reuse = main.index("\tif (fl4359_texel_shade.dims.w == 2u && identity.x != 0u")
    assert normal_depth < radial < sky < reuse

    # Both arms use this shared source classification. The front fast path is
    # therefore unreachable for empty and far-depth texels in either dispatch
    # mode; those texels enter the sky owner branch and write b98 themselves.
    compact = _compact(main)
    assert compact.index("vec4normal_depth=") < compact.index(
        "if(sky){"
    ) < compact.index("if(fl4359_texel_shade.dims.w==2u&&identity.x!=0u")
    assert "fl4359_texel_shade.dims.w==1u){fl4359_texel_store(address,uvec4(0u));return;}" not in compact
    assert "fl4359_texel_store(address,fl4359_texel_pack_payload(sky_payload,epoch));" in compact


def test_paired_store_and_refusal_are_one_exact_output_law() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert (
        'layout(set = 0, binding = 98, rgba32ui) uniform writeonly '
        "uimage2DArray fl4359_texel_payload;"
    ) in product
    assert (
        'layout(set = 0, binding = 118, rgba32ui) uniform readonly '
        "uimage2DArray fl4359_texel_paired_payload;"
    ) in product
    store = _function_source(product, "fl4359_texel_store")
    refuse = _function_source(product, "fl4359_texel_refuse")
    compact_store = _compact(store)
    assert compact_store.count("imageStore(") == 1
    assert "imageStore(fl4359_texel_payload,address,payload);" in compact_store
    assert "fl4359_texel_paired_payload" not in compact_store
    reuse = _compact(_function_source(product, "fl4359_texel_reuse_paired"))
    assert "uvec4payload=imageLoad(fl4359_texel_paired_payload,address);" in reuse
    assert "imageStore(fl4359_texel_payload,address,payload);" in reuse
    assert "fl4359_source_generation.values[index]=fl4359_paired_source_generation.values[index];" in reuse
    assert "fl4691_reflection_lineage_alias_paired(address);" in reuse

    compact_refuse = _compact(refuse)
    assert "uvec4payload=uvec4(0u,0u,0u,code);" in compact_refuse
    assert "fl4359_texel_store(address,payload);" in compact_refuse
    assert "imageStore(" not in compact_refuse


def test_visible_underlay_terminal_paths_store_or_refuse() -> None:
    main = _function_source(
        _expand_includes(PRODUCT.read_text(encoding="utf-8")), "fl4359_texel_shade_main"
    )
    compact_main = _compact(main)
    assert "if(layer_generation==0u" in compact_main
    assert "fl4359_shaded_generation.values[survivor_id]==layer_generation)return;" in compact_main
    assert "if(fl4359_texel_shade.dims.w==2u&&identity.x!=0u" in compact_main
    # FL-4691 deferred shade: mode 2 trusts the paired (mode 1) store only when
    # mode 1 painted this texel for the layer's current content generation.
    assert "!deferred||fl4359_paired_shaded_generation" not in compact_main
    assert ("if(fl4359_paired_shaded_generation.values[captured_index]==layer_generation"
        "&&captured_generation!=0u"
        "&&(captured_generation&~FL4359_SOURCE_GENERATION_VALUE_MASK)==0u"
        "&&fl4359_paired_source_generation.values[captured_index]==captured_generation)"
        "{fl4359_texel_reuse_paired(address);return;}") in compact_main

    # Once the source facts needed by the finite geometry path are loaded, every
    # explicit terminal is adjacent to the shared store/refusal helper. Sky and
    # paired reuse have their own terminal stores above this boundary.
    paint_body = main[main.index("vec4 anchor_source") :]
    returns = list(re.finditer(r"\breturn\s*;", paint_body))
    assert returns, "visible shade has no terminal paths"
    for match in returns:
        preceding = paint_body[:match.start()]
        assert re.search(r"fl4359_texel_(?:store|refuse)\s*\([^;]*\);\s*$", preceding), preceding[-200:]
    assert compact_main.endswith("fl4359_texel_store(address,packed);}")

    # FL-4359: underlay sky is real deeper paper beneath vegetation. Both
    # surfaces must store its payload, while front non-meadow adopts paired paint.
    assert (
        "if(fl4359_texel_shade.dims.w==1u){"
        "fl4359_texel_store(address,uvec4(0u));return;}"
    ) not in compact_main
    assert "fl4359_texel_store(address,fl4359_texel_pack_payload(sky_payload,epoch));" in compact_main
    # FL-4691: the underlay arm witnesses only its own store; it never
    # pre-marks the front witness (that skipped the front meadow shade).
    assert compact_main.count("fl4359_texel_shade.dims.w==1u") == 1
    assert "fl4359_paired_shaded_generation.values[survivor_id]=layer_generation" not in compact_main


def test_texel_shader_preprocess_retains_paired_image_contract() -> None:
    preprocessed = _preprocess_texel_shader()
    assert "fl4359_texel_payload" in preprocessed
    assert "fl4359_texel_paired_payload" in preprocessed
    assert "fl4359_texel_shade.dims.w" in preprocessed


def test_host_peer_wiring_validates_counterpart_and_binds_118() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    dispatch = _gd_function_source(effect, "perspective_dispatch_base_paint")
    compact_dispatch = _compact(dispatch)
    assert '"probe_underlay_texel_shade" if underlay else "probe_texel_shade"' in dispatch
    assert '"probe_texel_shade" if underlay else "probe_underlay_texel_shade"' in dispatch
    assert "u_payload.binding = 98" in dispatch
    assert "u_paired.binding = 118" in dispatch
    assert dispatch.index("u_payload.binding = 98") < dispatch.index("u_paired.binding = 118")
    for marker in (
        'not bool(paired_shade.get("valid", false))',
        "not paired_payload_rid.is_valid()",
        "paired_payload_rid == payload_rid",
        'int(paired_shade.get("layer_mask", -1)) != int(shade.get("layer_mask", -2))',
        'int(paired_shade.get("probe_size", -1)) != int(shade.get("probe_size", -2))',
        'int(paired_shade.get("layer_count", -1)) != int(shade.get("layer_count", -2))',
        'int(paired_shade.get("generation", -1)) != int(shade.get("generation", -2))',
    ):
        assert marker in dispatch, marker
    assert "binding = 118" in dispatch
    assert "seed_front_texel_payload" not in effect
    assert "seed_front_texel_payload" not in PIPELINE.read_text(encoding="utf-8")
    assert "paired_payload_rid" in compact_dispatch


def test_pipeline_owns_underlay_front_effects_then_publication() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    callback = _gd_function_source(effect, "_render_callback")
    assert callback.count('"execute_frame_transaction"') == 1
    assert (
        '"execute_frame_transaction",probe_view_receipt,perspective_frame_inputs'
        in _compact(callback)
    )
    for displaced_owner in (
        '"prepare_splat_survivors"', '"resolve_visible_sources"',
        '"perspective_dispatch_base_paint"',
        '"perspective_dispatch_display_paint"', '"draw_and_publish"',
    ):
        assert displaced_owner not in callback

    pipeline = PIPELINE.read_text(encoding="utf-8")
    transaction = _compact(_gd_function_source(pipeline, "execute_frame_transaction"))
    # One production transaction: admission, visibility, paired base paint,
    # display paint, and publication. The adapter supplies execution only.
    ordered = (
        '"perspective_prepare_product_dispatch",frame_inputs',
        "prepare_splat_survivors(",
        "resolve_visible_sources()",
        '"perspective_dispatch_base_paint",prepared_product_dispatch,probe_receipt,true',
        '"perspective_dispatch_base_paint",prepared_product_dispatch,probe_receipt,false',
        '"perspective_dispatch_display_paint",prepared_product_dispatch,probe_receipt',
        "draw_and_publish()",
    )
    cursor = 0
    for marker in ordered:
        marker = _compact(marker)
        position = transaction.find(marker, cursor)
        assert position >= 0, f"missing or out-of-order transaction stage: {marker}"
        cursor = position + len(marker)
    adapter_prepare = _gd_function_source(effect, "perspective_prepare_product_dispatch")
    assert "_prepare_cell_owner_product_dispatch(" in adapter_prepare
    assert "execute_frame_transaction" not in adapter_prepare
    for receipt_owner in (
        '"host_submission_cpu_ms"', '"gpu_timing_owner"',
        '"frame_identity"', '"work_domain"',
    ):
        assert receipt_owner in transaction
    shade_params = _gd_function_source(pipeline, "_write_texel_shade_params")
    assert "1 if underlay else 2" in shade_params


def test_same_key_same_glyph_producers_are_capture_anchored() -> None:
    """Pin glyph writers and their structural, world-anchored decisions.

    This is a source/ABI guard, not temporal or visual proof. Capture and
    display meadow writers may author different stages, but the display writer
    may not use the live screen observer to choose a printed member.
    """
    compiler = (PROJECT / "render/product/payload/product_payload_compiler.glslinc").read_text()
    for writer in (
        "PRODUCT_GLYPH_WRITER_BONFIRE_FACE = 1u",
        "PRODUCT_GLYPH_WRITER_LIGHTHOUSE_BEAM = 2u",
        "PRODUCT_GLYPH_WRITER_BONFIRE_SMOKE = 3u",
        "PRODUCT_GLYPH_WRITER_WEATHER = 4u",
        "PRODUCT_GLYPH_WRITER_LOCAL_LIGHT = 5u",
        "PRODUCT_GLYPH_WRITER_AIRPLANE_VAPOR = 6u",
        "PRODUCT_GLYPH_WRITER_WATERFALL = 7u",
        "PRODUCT_GLYPH_WRITER_MEADOW = 8u",
    ):
        assert writer in compiler, writer
    adapter = (PROJECT / "render/product/adapters/capture/product_capture_adapter.glslinc").read_text()
    assert "Screen-cell adjacency is not a paint input" in adapter
    contour = _function_source(adapter, "fl4359_captured_tree_glyph")
    assert "camera" not in contour.lower()
    assert "adjacent.y == identity.y" in contour
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "float glyph_wind = clamp(front * 0.72" in product
    assert "no camera term enters" in product.lower()
    assert "Flower glyph identity is untouched" in product
    surface = (PROJECT / "render/product/presentation/product_surface_presentation.glslinc").read_text()
    assert "PRODUCT_GLYPH_WRITER_MEADOW, record.r0.z" not in surface
    mesh = (PROJECT / "render/product/material/product_mesh_material.glslinc").read_text()
    assert "paint.glyph_identity = uvec4(PRODUCT_GLYPH_WRITER_MEADOW, stamp_decision)" in mesh
    assert "out uvec3 decision" in product
    finalizer = _function_source(product, "vsb_product_finalize_record")
    assert "inout uvec4 secondary_glyph_identity" in finalizer
    assert "secondary_glyph_identity = uvec4(0u)" not in finalizer
    assert "secondary_glyph_identity = compiled.glyph_identity" in adapter
    # Display meadow paint remains an authored stage, but its member selection
    # consumes captured root/receiver/wind facts rather than screen projection.
    display = (PROJECT / "render/product/adapters/perspective/probe_source_display.glslinc").read_text()
    display_apply = _function_source(display, "fl4359_source_effects_apply")
    assert "glyph = product_meadow_member(root, world," in display_apply
    meadow_arm = display_apply.split("if (meadow)", 1)[1].split(
        "if (needs_atmospheric_transport)", 1)[0]
    assert "observer" not in meadow_arm
    assert "world_to_pixel" not in meadow_arm
    assert "payload.x = (payload.x & ~0xfffu) | glyph;" in display
    meadow = _function_source(product, "product_meadow_member")
    assert "observer" not in meadow
    assert "product_world_to_pixel" not in meadow


def test_visibility_product_vs_presentation_costs_need_paired_measurement() -> None:
    """FL-4691: blur is not the established bottleneck; no stage is removed.

    Narrow scope: this test checks that tilt-shift blur stays
    presentation-only in its dispatch body, that stage fences stay wired, and
    that capture/survivor-shading/generation-lineage symbols stay present. It
    is NOT a performance verdict. The inspected local receipt
    (.run/fl4691_shadow_query_gpu_ab_20260914/ab_summary.json) reports
    candidate means ~37.23/~37.32 ms, p95 ~58.33/~58.86 ms, 0/480 frames
    meeting 16.667 ms, performance_pass=false: diagnostic results, not
    current-build acceptance and not a demonstrated speedup. Fresh rigid GRID
    paint is admitted independently of view visibility and GRID paint
    invalidation includes live phase, so retained geometry does not imply
    retained shading cost -- cost cannot be inferred from caching. A
    diagnostics-free frame-budget measurement under the declared workload is
    still owed. Architecture decisions require _oracle_first_divergence
    evidence, ordered capture/reconstruction/raster/pack/VSB before
    Product/translation and compositor mask -- not another renderer-wide
    mechanism.
    """
    effect = EFFECT.read_text(encoding="utf-8")
    assert "_fl4691_stage_fence(" in effect
    assert 'var _fl4691_stage_fences := OS.get_cmdline_user_args().has("--fl4691-stage-fences")' in effect
    blur = _gd_function_source(effect, "_dispatch_tilt_shift_blur")
    # Blur is a presentation pass: no glyph, owner, Product-record or receipt
    # writes may appear in its dispatch body.
    for banned in ("r0.z", "fl4359_texel", "visible_payload", "fl4359_shaded_generation",
                   "layer_generation", "product_record", "capture_receipt"):
        assert banned not in blur, banned
    compositor = (PROJECT / "render/ascii/raster/ascii_compositor.glsl").read_text()
    assert "presentation-only" in compositor.lower() or "Presentation-only" in compositor
    assert "never changes" in compositor.lower() or "never changes\n" in compositor.lower() \
        or "glyph election, ownership, Product records, or receipts" in compositor
    adapter = (PROJECT / "render/product/adapters/capture/product_capture_adapter.glslinc").read_text()
    assert "cull-survivor list" in adapter or "cull's compacted survivor list" in adapter
    assert "layer_generation" in adapter
    assert "fl4691_reflection_lineage" in adapter
    assert "fl4359_shaded_generation" in adapter
    assert "fl4359_paired_shaded_generation" in adapter
    pipeline = PIPELINE.read_text(encoding="utf-8")
    assert "prepare_splat_survivors" in pipeline
    assert "resolve_visible_sources" in pipeline
    assert "_reflection_lineage_table" in pipeline
    analyzer = (ROOT / "scripts" / "analyze_fl4359_moving_camera.py").read_text(encoding="utf-8")
    divergence = analyzer.split("def _oracle_first_divergence", 1)[1].split("def _raster_winner_relation", 1)[0]
    for stage in (
        '"candidate_pack"',
        '"vsb_selection"',
        '"product"',
        '"product_translation"',
        '"compositor_glyph_mask"',
    ):
        assert stage in divergence, stage
    assert divergence.index('"candidate_pack"') < divergence.index('"vsb_selection"') \
        < divergence.index('"product_translation"') < divergence.index('"compositor_glyph_mask"')


def test_ortho_perspective_share_cell_decision_law() -> None:
    """FL-4359: cell-decision implementations pinned (existence, not equivalence).

    Bounded source audit (no renderer edits; headed equivalence still open).
    This test confirms the two election implementations EXIST with the stated
    shapes; it does NOT prove they obey equivalent semantics. Ortho elects by
    semantic-priority/key over Product/VSB facts with no glyph value, receiver
    depth, projection, pixel coordinate, or slot index participating.
    Perspective publication starts from the center payload, separately elects
    paper, and lets a finite-effect winner replace the glyph source -- a
    second presentation policy that requires an explicit common contract and
    demonstrated equivalence where inputs coincide. Contours belong to the
    texel-shading stage (capture lattice, cube-face boundary unknown not
    silhouette); publication transports the captured glyph without a
    screen-neighbor rewrite. The compositor ortho/perspective split is
    confined to coordinate-transform responsibility (scene-color target vs
    compensation payload), not glyph policy. OPEN AND PINNED BELOW (not
    resolved by this test): native wide-glyph allocation (a width change can
    alter downstream row allocation; publisher-only measurements cannot
    explain every displayed glyph change) and the perspective-BG contract
    footprint behavior (GUT-3 removes cell-flat paper expansion; visual
    conformance to the 2026-09-09 projected-texel amendment remains unverified).
    """
    screen = (PROJECT / "render/product/adapters/ortho/product_screen_adapter.glslinc").read_text()
    assert "No glyph value," in screen
    assert "receiver depth, projection, pixel coordinate, or slot index participates" in screen
    assert "bool key_equal = all(equal(key_a, fl4740_winner_key_a))" in screen
    publish = (PROJECT / "render/backends/perspective/publish/perspective_probe_payload_publish.glsl").read_text()
    assert "uvec4 payload = publish_center_payload;" in publish
    assert "paper_background = publish_paper_background[0];" in publish
    assert "if (publish_effect_index[0] != PUBLISH_NONE) {" in publish
    assert "payload = publish_effect_payload[0];" in publish
    assert "publication transports that glyph without a screen-neighbor rewrite" in publish
    assert "native_tree_neighbors" not in publish
    assert "publish_tree_neighbors" not in publish
    adapter = (PROJECT / "render/product/adapters/capture/product_capture_adapter.glslinc").read_text()
    assert "capture lattice" in adapter
    assert "not a silhouette" in adapter
    host = EFFECT.read_text(encoding="utf-8")
    assert "its compositor writes directly to the scene color image" in host
    assert "retains the one presentation-compensation owner" in host
    assert "not the other surface's" in host
    region = host.split(
        "var presentation_compensation_active := pixel_ctx.is_orthogonal", 1)[1].split(
        "var final_pixel_image_size", 1)[0]
    assert "glyph" not in region.lower()
    assert "tree_contour" not in region.lower()
    # Native wide-glyph allocation is a load-bearing glyph decision outside
    # publication: a two-column glyph is assigned to both cells of the row,
    # and the compositor consumes the allocated glyph/origin.
    layout = (PROJECT / "shaders/native_glyph_layout.glsl").read_text()
    assert "if (span == 2u && x + 1u < params.grid.x)" in layout
    assert "output_layout.rows[index + 1u] = entry;" in layout
    # GUT-3 removes cell-flat paper expansion; native pixels consume projected sources.
    compositor = (PROJECT / "render/ascii/raster/ascii_compositor.glsl").read_text()
    assert "resolved.bg = fl4359_resolve_admitted ? fl4359_elected_paper : vec3(0.0);" not in compositor
    assert "resolved.bg = fl4359_bg_admitted" in compositor
    assert '"bg_transport": "product" if _presentation_is_orthogonal else "depth_winning_splat"' in host


def test_capture_tuple_chooses_physical_surface_once(tmp_path):
    """Exercise the shipping reader with stale foreground fields and equal IDs."""
    source = (PRODUCT.parent / "probe_capture_read.glslinc").read_text()
    functions = source[source.index("struct ProbeCaptureSource"):source.rindex("#endif")]
    harness = r'''
#include <cassert>
#include <cstdint>
using uint=uint32_t;
struct uvec4 { uint x,y,z,w; };
struct vec4 { float x,y,z,w; };
using ivec3=int;
struct usampler2DArray { int id; };
struct sampler2DArray { int id; };
uvec4 identities[2]; vec4 fields[2]; int reads[4]={};
usampler2DArray probe_underlay_identity{1};
uvec4 texelFetch(usampler2DArray s,int address,int lod) {
    assert(address==7 && lod==0);++reads[s.id];return identities[s.id];
}
vec4 texelFetch(sampler2DArray s,int address,int lod) {
    assert(address==7 && lod==0);++reads[2+s.id];return fields[s.id];
}
FUNCTIONS
int main() {
    for(uint front=0;front<2;++front) {
        identities[0]={front,22,33,44};identities[1]={1,22,33,44};
        fields[0]={10,20,30,40};fields[1]={50,60,70,80};
        for(int &count:reads)count=0;
        auto source=probe_capture_source(usampler2DArray{0},7);
        assert(source.identity.x==1 && source.identity.y==22);
        for(int field=0;field<4;++field) {
            auto value=probe_capture_fetch(source.foreground,sampler2DArray{0},sampler2DArray{1},7);
            assert(value.x==(front?10:50) && value.w==(front?40:80));
        }
        assert(reads[0]==1 && reads[1]==int(!front));
        assert(reads[2]==(front?4:0) && reads[3]==(front?0:4));
    }
}
'''.replace("FUNCTIONS", functions)
    path = tmp_path / "capture.cpp"
    path.write_text(harness)
    compiler = shutil.which("c++")
    assert compiler
    result = subprocess.run([compiler, "-std=c++20", str(path), "-o", str(tmp_path / "capture")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    subprocess.run([str(tmp_path / "capture")], check=True)


def test_deleted_full_grid_and_fixture_shims_stay_deleted() -> None:
    pipeline = PIPELINE.read_text()
    effect = EFFECT.read_text()
    for retired in ("deferred_shade", "splat_candidates", "capture_and_splat",
                    "FL4691_LEGACY_SHADE", "--fl4691-legacy-shade"):
        assert retired not in pipeline + effect
    writer = _gd_function_source(pipeline, "_write_texel_shade_params")
    assert "has_method(" not in writer
    assert 'get("_' not in writer
    assert "shade_receipt[\"survivors\"] = _survivor_buffers()" in writer
    publish = _gd_function_source(pipeline, "draw_and_publish")
    assert "_rasterize_source_references()" not in publish
    for name in ("perspective_dispatch_base_paint", "_dispatch_fl4359_source_effects_uniforms"):
        dispatch = _gd_function_source(effect, name)
        assert "compute_list_dispatch_indirect(" in dispatch
        assert "(probe_size+7)/8,(probe_size+7)/8,layer_count" not in _compact(dispatch)
