from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPLAT = (ROOT / "godot_project/render/backends/perspective/splat/perspective_probe_splat.glsl").read_text()
VSB_SHADER = (ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl").read_text()
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"

from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    return _expand_product_includes(_expand_includes(PRODUCT_PATH.read_text(encoding="utf-8")))


PRODUCT = _product_source()
VSB_HOST = (ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd").read_text()
PIPELINE = (ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd").read_text()
COMPOSITOR_HOST = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()


def test_capture_payload_availability_has_no_live_eye_cone_gate() -> None:
    # bb39a9de5 zeroed captured payload according to display-camera direction.
    # This guards that source regression only, not temporal visual acceptance.
    params = PRODUCT.split("uniform Fl4359TexelShadeParams {", 1)[1].split(
        "} fl4359_texel_shade;", 1
    )[0]
    assert "view_cone" not in params
    assert "TEXEL_SHADE_CONE_MARGIN_DEG" not in PIPELINE
    assert "TEXEL_SHADE_PARAM_BYTES := 18 * 16 + 32" in PIPELINE
    shade = PRODUCT.split("void fl4359_texel_shade_main() {", 1)[1]
    assert "fl4359_texel_shade.view_cone" not in shade
    cull = (ROOT / "godot_project/render/backends/perspective/cull/perspective_probe_cull.glsl").read_text()
    assert "texel_payload" not in cull
    assert "quad_may_touch_screen" not in cull


def _probe_candidate_decoder(source: str) -> str:
    # The current-camera traversal selects the nearest hit. It writes only
    # caller-owned out-params; publication globals stay untouched.
    start = source.index("bool query_current_camera_surface(")
    end = source.index("bool fl4359_oracle_query_surface(", start)
    return source[start:end]


def test_current_owns_retained_overlap_and_previous_only_fills_holes() -> None:
    vertex, fragment = SPLAT.split("#[fragment]", 1)
    # Roles come from the capture bands, not named probe constants: 0 carries
    # the EYE origin, 1 CURRENT, 2 PREVIOUS. The retired per-probe depth
    # constants are gone; reversed-Z plus the EYE push-back and a hash
    # micro-bias disambiguate depth deterministically.
    assert "uint role = band == splat.bands.x ? 0u : (band == splat.bands.y ? 1u : 2u);" in vertex
    assert "const uint PROBE_EYE" not in SPLAT
    assert "const uint PROBE_PREVIOUS" not in SPLAT
    assert "clip.z = clip.w - clip.z;" in vertex
    # FL-4880: EYE tie bias skips water and EYE-only movers/actors; capped at 2% of view distance.
    assert "const float EYE_RETAINED_TIE_RELATIVE = 0.02;" in vertex
    assert ("if (role == 0u && v_identity.x != CAPTURE_OWNER_WATER\n"
        "            && v_identity.x != CAPTURE_OWNER_DYNAMIC && v_identity.x != CAPTURE_OWNER_ACTOR)\n"
        "        clip.z += min(0.001 * clip.w, (clip.w - clip.z) * EYE_RETAINED_TIE_RELATIVE);") in vertex
    assert "clip.z += h * 0.000000001 * clip.w;" in vertex
    assert "fl4359_stable_identity_transition_key" not in fragment
    assert "fl4359_retained_transition_key" not in fragment
    assert "current_identity_threshold" not in fragment
    assert "previous_identity_cutoff" not in fragment
    # Reference transition law: Bayer over the cell address selects whole
    # PREVIOUS/CURRENT payloads in the fragment; lifecycle.y carries fade_t,
    # lifecycle.z the crossfade.
    assert "float threshold = bayer4(cell);" in fragment
    assert "if (splat.lifecycle.z != 0u) {" in fragment
    assert "if (v_probe == 1u && threshold >= fade) discard;" in fragment
    assert "if (v_probe == 2u && threshold < fade) discard;" in fragment
    assert "splat.lifecycle.w" not in fragment
    assert "current_identity_threshold" not in PIPELINE
    assert "previous_identity_threshold" not in PIPELINE
    assert '_encode_uvec4(bytes, 128, int(_state.get("generation", 0)),\n\t\t_float_bits(fade_t), 1 if crossfading else 0, 1)' in PIPELINE


def test_product_observes_the_same_water_cell_compatibility_key() -> None:
    helper_start = PRODUCT.index("bool fl4359_pack_signed_water_cell")
    helper_end = PRODUCT.index("// Interpret one VSB record", helper_start)
    helper = PRODUCT[helper_start:helper_end]
    assert "left.owner_kind != VSB_OWNER_WATER" in helper
    assert "return left.primitive_id == right.primitive_id;" in helper
    assert "left.world_position.xyz" in helper
    assert "right.world_position.xyz" in helper
    assert "return left_cell == right_cell;" in helper
    assert "FL4359_SIGNED_CELL_MIN = -32768" in PRODUCT
    assert "FL4359_SIGNED_CELL_MAX = 32767" in PRODUCT


def test_probe_generation_provenance_cannot_author_direct_vsb_epoch() -> None:
    # VSB publishes DIRECT current-ray geometry only (source epoch 0 in
    # hit_record). Probe EYE/CURRENT/PREVIOUS epochs arrive exclusively from
    # the splat side (band roles + source-generation bank), which the decode
    # admits only for perspective records with capture provenance.
    vsb = VSB_SHADER
    assert "VSB publishes DIRECT current-ray geometry only (source epoch 0)" in vsb
    hit = vsb.split("VisibleSurfaceProduct hit_record(SurfaceHit hit)", 1)[1].split(
        "return record;", 1)[0]
    assert "EPOCH" not in hit
    assert "fl4359_pending_source_epoch" not in vsb
    decoder = _probe_candidate_decoder(VSB_SHADER)
    for stage in ("scene_query_dynamic_tlas", "scene_query_static_tlas",
                  "scene_query_heightfield", "scene_query_water"):
        assert stage in decoder, stage
    assert "fl4359_pending_source_epoch" not in decoder
    assert "fl4737_pending_submerged_source_bits" not in decoder
    assert "SOURCE_EPOCH_SHIFT" in vsb and "SOURCE_EPOCH_MASK" in vsb

    for source in (VSB_SHADER, PRODUCT, VSB_HOST, COMPOSITOR_HOST):
        assert "SOURCE_EPOCH_SHIFT" in source
        assert "SOURCE_EPOCH_MASK" in source
    assert "const uint STATUS_SOURCE_EPOCH_MASK = 0xc0000000u;" in VSB_SHADER
    assert "const uint VSB_STATUS_SOURCE_EPOCH_MASK = 0xc0000000u;" in PRODUCT
    assert "const STATUS_SOURCE_EPOCH_MASK := 0xC0000000" in VSB_HOST
    assert "const VSB_STATUS_SOURCE_EPOCH_MASK := 0xC0000000" in COMPOSITOR_HOST


def test_probe_candidate_decoder_has_no_b64_publication_side_effects() -> None:
    decoder = _probe_candidate_decoder(VSB_SHADER)
    for publication_global in (
        "fl4359_pending_source_epoch",
        "fl4737_pending_submerged_source_bits",
    ):
        assert publication_global not in decoder


def test_vsb_abi_v4_preserves_water_bits_and_rejects_unknown_hit_bits() -> None:
    assert "const uint VISIBLE_SURFACE_ABI_VERSION = 4u;" in VSB_SHADER
    assert "const uint VSB_VISIBLE_SURFACE_ABI_VERSION = 4u;" in PRODUCT
    assert "const VISIBLE_SURFACE_ABI_VERSION := 4" in VSB_HOST
    assert "const VSB_VISIBLE_SURFACE_ABI_VERSION := 4" in COMPOSITOR_HOST
    assert "const uint STATUS_SUBMERGED_SOURCE_MASK = 0x1fffff80u;" in VSB_SHADER
    assert "const uint STATUS_DIRECT_WATER_SOLID_CONTACT = 1u << 29u;" in VSB_SHADER
    assert "const uint STATUS_SOURCE_EPOCH_MASK = 0xc0000000u;" in VSB_SHADER
    assert "const uint VSB_STATUS_HIT_KNOWN_MASK" in PRODUCT
    assert "(record.status_flags & ~VSB_STATUS_HIT_KNOWN_MASK) != 0u" in PRODUCT
    assert "(record.status_flags & VSB_STATUS_VALID) == 0u" in PRODUCT
    assert "STATUS_VALID | STATUS_WATER_PAYLOAD_MASK | STATUS_SOURCE_EPOCH_MASK" in VSB_HOST
    assert "(vsb_status & ~VSB_STATUS_KNOWN_MASK) != 0" in COMPOSITOR_HOST
    assert "(vsb_status & VSB_STATUS_VALID) == 0" in COMPOSITOR_HOST


def test_visible_product_accepts_splat_epochs_and_direct_hole_fallback() -> None:
    assert "STATUS_SOURCE_EPOCH_CURRENT" in VSB_HOST
    assert "STATUS_SOURCE_EPOCH_PREVIOUS" in VSB_HOST
    assert "source_epoch != STATUS_SOURCE_EPOCH_DIRECT and not retained_epoch_admitted" in VSB_HOST
    decode_start = PRODUCT.index("uint vsb_product_decode_record")
    decode_end = PRODUCT.index(
        "bool vsb_product_decoded_finite_water_slot", decode_start
    )
    decode = PRODUCT[decode_start:decode_end]
    assert "source_epoch != VSB_STATUS_SOURCE_EPOCH_DIRECT" in decode
    assert "record.projection_kind == VSB_PROJECTION_PERSPECTIVE" in decode
    assert "source_epoch == VSB_STATUS_SOURCE_EPOCH_EYE" in decode
    assert "source_epoch == VSB_STATUS_SOURCE_EPOCH_CURRENT" in decode
    assert "source_epoch == VSB_STATUS_SOURCE_EPOCH_PREVIOUS" in decode
    assert "diagnostic = 0x0115u;" in decode
    assert "0x0116u" not in decode
    assert "PRODUCT_STABLE_IDENTITY_TRANSITION_RECEIPTS_ENABLED" not in PRODUCT
    assert "fl4359_same_identity_mixed_epochs" not in PRODUCT
