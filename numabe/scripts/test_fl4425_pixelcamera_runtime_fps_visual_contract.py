import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
COMPOSITOR = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def glsl_function(source: str, name: str) -> str:
    """Return one complete GLSL function so calls elsewhere cannot satisfy a pin."""
    signature = re.compile(
        rf"(?m)^[ \t]*(?:[A-Za-z_]\w*[ \t]+)+{re.escape(name)}[ \t]*\("
    )
    match = signature.search(source)
    assert match is not None, f"missing GLSL function {name}"
    body_start = source.find("{", match.end())
    assert body_start >= 0
    assert ";" not in source[match.end() : body_start]
    depth = 0
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"unterminated GLSL function {name}")


def compositor_variant(source: str, capture_enabled: bool) -> str:
    """Resolve the one compile-time capture guard used by the compositor."""
    output: list[str] = []
    include = True
    guard_stack: list[bool] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped == "#ifdef FL4431_FINAL_CELL_CAPTURE":
            guard_stack.append(include)
            include = include and capture_enabled
            continue
        if stripped == "#endif" and guard_stack:
            include = guard_stack.pop()
            continue
        if include:
            output.append(line)
    assert not guard_stack
    return "\n".join(output)


def test_final_paint_receipt_is_a_same_source_compile_time_variant() -> None:
    """Ordinary paint must not pay for the explicit final-cell evidence lane."""
    effect = read(EFFECT)
    compositor = read(COMPOSITOR)
    ordinary = compositor_variant(compositor, capture_enabled=False)
    capture = compositor_variant(compositor, capture_enabled=True)

    assert "binding = 43" not in ordinary
    assert "final_cell_paint" not in ordinary
    assert "atomicAdd" not in ordinary
    assert "record_final_cell_paint(" not in ordinary
    assert "FINAL_CELL_PAINT_PROOF_FLAG" not in ordinary
    assert "FINAL_CELL_PAINT_PROOF_FLAG" not in capture

    assert "binding = 43" in capture
    assert "final_cell_paint" in capture
    assert "atomicAdd" in capture
    assert "record_final_cell_paint(" in capture

    # Both pipelines compile the same painter. The define changes only the
    # evidence variant, and binding 43 is appended only when that variant runs.
    assert effect.count('"res://render/ascii/raster/ascii_compositor.glsl"') >= 2
    assert 'PackedStringArray(["FL4431_FINAL_CELL_CAPTURE"])' in effect
    uniform_start = effect.index(
        "var compositor_uniforms: Array[RDUniform] = ["
    )
    uniform_end = effect.index("\n\t\t]", uniform_start)
    ordinary_uniforms = effect[uniform_start:uniform_end]
    assert "u_final_cell_paint" not in ordinary_uniforms
    capture_start = effect.index("if final_paint_capture_active:", uniform_end)
    capture_end = effect.index(
        "var uniform_set: RID = UniformSetCacheRD.get_cache(", capture_start
    )
    capture_setup = effect[capture_start:capture_end]
    assert "compositor_uniforms.append(u_final_cell_paint)" in capture_setup
    assert "compositor_shader_for_frame = final_cell_capture_shader" in capture_setup
    assert (
        "compositor_pipeline_for_frame = final_cell_capture_pipeline"
        in capture_setup
    )


def test_cloud_shadow_uses_live_product_time_without_a_second_visible_sky_body() -> None:
    """FL-4425 cloud drift stays live while Product remains the sole sky painter."""
    product = read(PRODUCT)
    clock = glsl_function(product, "product_cloud_shadow_time")
    density = glsl_function(product, "product_cloud_density_at_sample_xz")

    assert "return params.feature_params.x;" in clock
    assert "product_quantized_time(" not in clock
    assert "float time = product_cloud_shadow_time();" in density
    assert "float time = 0.0;" not in density
    assert "product_quantized_time(PRODUCT_CLOUD_SHADOW_FPS)" not in density

    # The retired visible-sky helpers must stay deleted. The dormant Q3
    # payload compiler has its own frozen parity helpers and is not this owner.
    assert "float sky_cloud_density_at(" not in product
    assert "float sky_cloud_visible_density_at(" not in product


def test_static_shadow_caster_is_typed_and_retained_product_owned() -> None:
    """Static shadow selection must end in the retained TLAS-to-BLAS authority."""
    product = read(PRODUCT)
    selector = glsl_function(product, "vsb_retained_select_static_shadow_caster")
    q3_selector = glsl_function(
        product, "vsb_retained_select_q3_static_shadow_caster"
    )
    density = glsl_function(product, "q3_probe_payload_static_shadow_density")

    assert "struct VsbRetainedStaticCasterSelection {" in product
    assert "bool valid;" in product[
        product.index("struct VsbRetainedStaticCasterSelection {") :
        product.index("};", product.index("struct VsbRetainedStaticCasterSelection {"))
    ]
    assert "vsb_semantic_static_tlas_ids.ids[" in selector
    assert "vsb_retained_static_blas_select(" in selector
    assert "return vsb_retained_select_static_shadow_caster(" in q3_selector
    assert "VsbRetainedStaticCasterSelection selected =" in density
    assert "vsb_retained_select_q3_static_shadow_caster(" in density
    assert "return selected.valid ? 1.0 : 0.0;" in density

    # These names belonged to the deleted flat-bank tree-shadow owner.
    assert "STATIC_TREE_SHADOW_SOURCE_BASE" not in product
    assert "static_tree_shadow_fact_at_world" not in product


def test_tree_and_falling_leaf_runtime_owner_stays_deleted() -> None:
    product = read(PRODUCT)
    for retired in (
        "vsb_product_consider_leaf_lane",
        "vsb_product_consider_leaf_record",
        "vsb_product_apply_leaf",
        "STATIC_WORLD_TREE_LEAF_MAT",
        "LeafParticleBank",
        "leaf_product_attached",
    ):
        assert retired not in product


def test_extended_glyph_cells_fail_closed_when_their_atlas_cell_is_absent() -> None:
    """An invalid extended-atlas cell must not masquerade as a CP437 glyph."""
    compositor = read(COMPOSITOR)
    sampler = glsl_function(compositor, "sample_compositor_glyph_intensity")
    extended_start = sampler.index(
        "if (glyph >= ADDITIVE_GLYPH_MIN && glyph <= ADDITIVE_GLYPH_MAX)"
    )
    morphology_start = sampler.index(
        "if (glyph >= MORPHOLOGY_GLYPH_MIN && glyph <= MORPHOLOGY_GLYPH_MAX)"
    )
    extended_branch = sampler[extended_start:morphology_start]

    assert "ivec2 ext_size = textureSize(extended_glyph_tex, 0);" in extended_branch
    assert "int ext_cell_px = max(" in extended_branch
    assert (
        "ext_base.x + ext_cell_px > ext_size.x"
        in extended_branch
    )
    assert "ext_base.y + ext_cell_px > ext_size.y" in extended_branch
    assert "return 0.0;" in extended_branch
    assert "sample_cp437_glyph_intensity" not in extended_branch
    assert "extended_glyph_visible_fallback" not in compositor
    assert sampler.rfind("return sample_cp437_glyph_intensity(") > morphology_start
