"""FL-4737 finite-water transport of the already-selected retained source.

This is destructive source proof for the ABI-v2 transaction. It establishes
ownership, packing, validation, and paint order; it does not establish headed
visibility or operator acceptance.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VSB_SHADER = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
PRODUCT_SHADER = ROOT / "godot_project/render/product/cell_owner_product.glsl"
VSB_HOST = ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"
PRODUCT_HOST = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def _compact(source: str) -> str:
    source = re.sub(r"//.*", "", source)
    return re.sub(r"\s+", "", source)


def _gdscript_function(source: str, signature: str) -> str:
    start = source.index(signature)
    candidates = [
        index
        for marker in ("\nstatic func ", "\nfunc ")
        if (index := source.find(marker, start + len(signature))) >= 0
    ]
    end = min(candidates) if candidates else len(source)
    return source[start:end]


def _sources() -> tuple[str, str, str, str]:
    return tuple(
        path.read_text(encoding="utf-8")
        for path in (VSB_SHADER, PRODUCT_SHADER, VSB_HOST, PRODUCT_HOST)
    )  # type: ignore[return-value]


def _assert_contract(vsb: str, product: str, vsb_host: str, product_host: str) -> None:
    vsb_code = _compact(vsb)
    product_code = _compact(product)
    host_code = _compact(vsb_host)
    compositor_code = _compact(product_host)

    # ABI v4 gives status bit 29 its direct contact meaning and bits 30..31
    # retained-probe source epoch without changing the 96-byte record or
    # borrowing byte 92 from the reflection receipt.
    assert "constuintVISIBLE_SURFACE_ABI_VERSION=4u;" in vsb_code
    assert "constuintVSB_VISIBLE_SURFACE_ABI_VERSION=4u;" in product_code
    assert "constVISIBLE_SURFACE_ABI_VERSION:=4" in host_code
    assert "constVSB_VISIBLE_SURFACE_ABI_VERSION:=4" in compositor_code
    assert "constuintSTATUS_SOURCE_EPOCH_MASK=0xc0000000u;" in vsb_code
    assert "constuintVSB_STATUS_SOURCE_EPOCH_MASK=0xc0000000u;" in product_code
    assert "constRECORD_STRIDE_BYTES:=96" in host_code
    assert "constVSB_VISIBLE_SURFACE_RECORD_BYTES:=96" in compositor_code
    assert "uintreflection_word;" in vsb_code

    glsl_layout = (
        "constuintSTATUS_SUBMERGED_OWNER_SHIFT=7u;"
        "constuintSTATUS_SUBMERGED_OWNER_MASK=0x00000180u;"
        "constuintSTATUS_SUBMERGED_MATERIAL_SHIFT=9u;"
        "constuintSTATUS_SUBMERGED_MATERIAL_MASK=0x0001fe00u;"
        "constuintSTATUS_SUBMERGED_SHADE_SHIFT=17u;"
        "constuintSTATUS_SUBMERGED_SHADE_MASK=0x000e0000u;"
        "constuintSTATUS_SUBMERGED_NORMAL_X_SHIFT=20u;"
        "constuintSTATUS_SUBMERGED_NORMAL_Z_SHIFT=24u;"
        "constuintSTATUS_SUBMERGED_NORMAL_COMPONENT_MASK=0x0000000fu;"
        "constuintSTATUS_SUBMERGED_NORMAL_Y_NEGATIVE=1u<<28u;"
        "constuintSTATUS_SUBMERGED_SOURCE_MASK=0x1fffff80u;"
    )
    assert glsl_layout in vsb_code
    assert glsl_layout.replace("STATUS_", "VSB_STATUS_") in product_code
    assert "constuintSTATUS_DIRECT_WATER_SOLID_CONTACT=1u<<29u;" in vsb_code
    assert "constuintVSB_STATUS_DIRECT_WATER_SOLID_CONTACT=1u<<29u;" in product_code
    for host_name, value in (
        ("OWNER_SHIFT", "7"),
        ("OWNER_MASK", "0x00000180"),
        ("MATERIAL_SHIFT", "9"),
        ("MATERIAL_MASK", "0x0001FE00"),
        ("SHADE_SHIFT", "17"),
        ("SHADE_MASK", "0x000E0000"),
        ("NORMAL_X_SHIFT", "20"),
        ("NORMAL_Z_SHIFT", "24"),
        ("NORMAL_COMPONENT_MASK", "0xF"),
        ("NORMAL_Y_NEGATIVE", "1<<28"),
        ("SOURCE_MASK", "0x1FFFFF80"),
    ):
        assert f"constSTATUS_SUBMERGED_{host_name}:={value}" in host_code

    # VSB saves only the finite, already-selected non-water result. The water
    # query remains singular, and the compact helper cannot issue another one.
    pack = _compact(_function(vsb, "uint fl4737_submerged_source_bits("))
    assert "if(!hit.present||hit.material>0xffu)return0u;" in pack
    assert "hit.owner_kind==OWNER_TERRAIN?1u" in pack
    assert "hit.owner_kind==OWNER_STATIC_MESH?2u" in pack
    assert "hit.owner_kind==OWNER_DYNAMIC_MESH?3u:0u" in pack
    assert "clamp(hit.authored_shade,0.0,1.0)*7.0" in pack
    assert "finite_vec3(hit.normal)" in pack
    assert "scene_query" not in pack
    assert "reflection_word" not in pack

    main = _compact(_function(vsb, "void main()"))
    current_camera = _compact(
        _function(vsb, "bool query_current_camera_surface(")
    )
    before_water = (
        "pre_water_source_bits=fl4737_submerged_source_bits(nearest);"
        "pre_water_direct_contact="
        "fl4216_direct_water_solid_contact(nearest);"
        "pre_water_depth=nearest.depth;"
    )
    water_query = "query_valid=scene_query_water(ray,nearest);"
    assert before_water in current_camera
    assert water_query in current_camera
    assert current_camera.index(before_water) < current_camera.index(water_query)

    # The same current-camera query owns orthographic and perspective geometry.
    # Probe candidates remain proof payload and cannot become a second primary
    # hit or a post-selection water annotation path.
    direct_call = (
        "query_current_camera_surface(ray,nearest,pre_water_source_bits,"
        "pre_water_direct_contact,pre_water_depth);"
    )
    assert main.count(direct_call) == 1
    assert "probe_candidate_to_hit(record_index,ray,nearest" not in main
    assert "query_perspective_water_source(" not in vsb_code

    finite_gate = (
        "boolfinite_water_winner=nearest.owner_kind==OWNER_WATER"
        "&&nearest.primitive_id>=finite_water_base"
        "&&nearest.primitive_id-finite_water_base<finite_water_count;"
    )
    assert finite_gate in main
    assert main.index(direct_call) < main.index(finite_gate)
    assert (
        "if(finite_water_winner&&pre_water_source_bits!=0u"
        "&&pre_water_depth>nearest.depth+max(push_data.ray_parameters.w,1.0e-6))"
        "{fl4737_pending_submerged_source_bits=pre_water_source_bits;"
        "if(pre_water_direct_contact){fl4737_pending_submerged_source_bits|="
        "STATUS_DIRECT_WATER_SOLID_CONTACT;}}" in main
    )
    assert current_camera.count("scene_query_water(ray,nearest)") == 1
    hit_record = _compact(_function(vsb, "VisibleSurfaceProduct hit_record("))
    assert (
        "record.status_flags=STATUS_VALID|fl4737_pending_submerged_source_bits;"
        in hit_record
    )
    assert "record.reflection_word=fl4216_pending_reflection_word;" in hit_record

    # Product and both CPU validation paths fail closed on unknown status bits,
    # non-water payloads, absent owner code, and codes above dynamic.
    well_formed = _compact(
        _function(product, "bool vsb_product_submerged_source_is_well_formed(")
    )
    assert "uintpayload=status_flags&VSB_STATUS_SUBMERGED_SOURCE_MASK;" in well_formed
    assert "if(payload==0u)return!direct_contact;" in well_formed
    assert "primary_owner_kind==VSB_OWNER_WATER" in well_formed
    assert "primary_primitive_id-finite_base<finite_count" in well_formed
    assert "boolsource_owner_valid=owner_code>=1u&&owner_code<=3u;" in well_formed
    assert "returnfinite_water&&source_owner_valid;" in well_formed
    assert "owner_code>=2u" not in well_formed
    decode_record = _compact(
        _function(product, "uint vsb_product_decode_record(")
    )
    assert (
        "(record.status_flags&~VSB_STATUS_HIT_KNOWN_MASK)"
        "!=0u" in decode_record
    )
    assert "(record.status_flags&VSB_STATUS_VALID)==0u" in decode_record
    assert "vsb_product_submerged_source_is_well_formed(" in decode_record
    host_hit = _compact(
        _gdscript_function(vsb_host, "static func _validate_hit_record(")
    )
    assert "STATUS_HIT_KNOWN_MASK:=" in _compact(vsb_host)
    assert "STATUS_VALID|STATUS_WATER_PAYLOAD_MASK|STATUS_SOURCE_EPOCH_MASK" in _compact(vsb_host)
    assert "status_flags&~STATUS_HIT_KNOWN_MASK)!=0" in host_hit
    assert "submerged_bits!=0andowner_kind!=OWNER_WATER" in host_hit
    assert "submerged_owner_code<1orsubmerged_owner_code>3" in host_hit
    assert "direct_water_solid_contactandsubmerged_owner_code<1" in host_hit
    assert "submerged_source_present" in vsb_host
    assert "submerged_source_material" in vsb_host
    assert "submerged_source_authored_shade" in vsb_host
    assert "submerged_source_normal" in vsb_host
    assert "vsb_status&~VSB_STATUS_KNOWN_MASK)!=0" in compositor_code
    assert "(vsb_status&VSB_STATUS_VALID)==0" in compositor_code
    assert "vsb_owner_kind!=VisibleSurfaceBuilderScript.OWNER_WATER" in compositor_code
    assert compositor_code.count("vsb_submerged_owner_code<1") == 2
    assert "vsb_submerged_owner_code>3" in compositor_code
    assert "vsb_direct_water_solid_contactandvsb_submerged_owner_code<1" in compositor_code

    # Terrain keeps the exact existing bed. Static and dynamic material facts
    # replay glyph, foreground, and paper through one shared material owner.
    submerged = _compact(
        _function(product, "ProductWaterSourcePaint product_fl4737_submerged_source_paint(")
    )
    transported = _compact(
        _function(product, "ProductWaterSourcePaint product_water_transported_source_paint(")
    )
    assert "if(!source.present||source.owner_kind==OWNER_TERRAIN)" in submerged
    assert "returnProductWaterSourcePaint(false,0u,vec3(0.0),vec3(0.0));" in submerged
    assert "returnproduct_water_transported_source_paint(source,water_cell);" in submerged
    for field in ("paint.glyph", "paint.fg", "paint.bg"):
        assert field in transported
    assert "product_retained_tree_paint_pair(" in transported
    assert "product_static_rock_moss_proxy_material(" in transported

    facts = _compact(_function(product, "ProductFiniteWaterCellFacts product_finite_water_cell_facts("))
    assert facts.index("facts.bed=water_submerged_bed_color(") < facts.index(
        "product_fl4737_submerged_source_paint("
    )
    analytic = _compact(_function(product, "ProductFiniteWaterCellFacts product_analytic_water_cell_facts("))
    assert "facts.submerged_source_present=false;" in analytic

    sample = _compact(_function(product, "ProductWaterSample product_water_sample("))
    # FL-4737 F8 revision 2 (2026-08-13, adversarial review AD1/AD2): the
    # retained source stays IN THE COLUMN, as the bed, where it has always been.
    # Revision 1 removed it from here and composited it last; that capped every
    # SURFACE stage by column thickness (the F11 contact band fell to ~5 percent
    # inside its own admission zone, the D3 cloud lift to 5-50 percent) and,
    # being ungated, moved FULL NIGHT bytes in every submerged-source cell. What
    # had actually erased the wrist was one surface owner -- the FL-4216 mirror
    # mix -- and that is capped at the reflection call site instead.
    assert (
        "vec3bed=cell_facts.submerged_source_present?"
        "cell_facts.submerged_source_bg:cell_facts.bed;" in sample
    )
    assert "s.submerged_source_bg=" not in sample
    assert sample.count("floatbed_visibility=") == 1
    assert (
        "s.submerged_visibility=cell_facts.submerged_source_present?"
        "bed_visibility:0.0;" in sample
    )
    assert "s.bg=mix(water_body,bed,bed_visibility);" in sample

    # Both colour channels share that one visibility. Shore/wake keep their
    # stronger glyph class, reflection remains later surface paint, and the
    # existing night pair remains a later finalizer without a new source owner.
    day = _compact(_function(product, "void product_apply_day_water_pair("))
    # FL-4737 F8 revision 2: ONE composition site, at the TOP of the pair -- the
    # transmitted-fact layer. Everything below it is either the column above the
    # source or light on the surface, and all of it belongs on top.
    assert "foreground=mix(foreground,s.submerged_source_fg,s.submerged_visibility);" in day
    assert day.index("s.submerged_visibility") < day.index("s.break_strength>0.0")
    assert day.index("s.break_strength>0.0") < day.index("product_fl4216_apply_reflection_pair(")
    assert "direct_visibility" not in _compact(product)
    # Exactly one read: the single transmitted-source composition site above.
    # Reflection now yields by shoreline distance and does not own a second
    # submerged-visibility read, so a second site would resurrect the rejected
    # column-thickness cap on later surface stages.
    assert day.count("s.submerged_visibility") == 1
    assert day.count("s.submerged_source_fg") == 1
    # The ONE surface owner this slice caps is the FL-4216 mirror mix, and it is
    # capped by the existing shoreline admission argument rather than by a
    # second submerged-source composition or a direct-contact writer.
    assert (
        "1.0-product_day_night_factor(),shoreline_mirror_admission,"
        in day
    )
    assert "floatshoreline_mirror_admission=smoothstep(0.75,2.26,s.shore_dist_cells);" in day
    reflection = _compact(
        _function(product, "void product_fl4216_apply_reflection_pair(")
    )
    assert (
        "floatamount=FL4216_REFLECTION_SOURCE_INK_MAX"
        "*clamp(daylight,0.0,1.0)*mass_gain"
        "*clamp(mirror_admission,0.0,1.0);" in reflection
    )
    assert (
        "reflected.paper_mix*clamp(daylight,0.0,1.0)*mass_gain"
        "*clamp(mirror_admission,0.0,1.0)" in reflection
    )
    # Two capped mixes remain inside F10 reflection. Universal F11 contact is
    # break state and cannot be scaled or painted by this helper.
    assert reflection.count("clamp(mirror_admission,0.0,1.0)") == 2
    assert "direct_water_solid_contact" not in reflection
    assert "contact_amount" not in reflection
    water_owner = _compact(_function(product, "void q3_probe_payload_apply_water("))
    assert (
        "uintwater_slot_class=water.break_active||water.break_strength>0.0?"
        "0u:(water.wake_source_id!=0u&&water.wake>0.18?1u:2u);" in water_owner
    )
    assert (
        "if(water_slot_class==2u&&water.submerged_source_present"
        "&&water.submerged_source_glyph!=0u){glyph=water.submerged_source_glyph;}"
        in water_owner
    )
    night = _compact(_function(product, "void product_apply_night_water_pair("))
    assert "submerged_source" not in night
    finalize = _compact(
        _function(product, "ProbeSubcellProductRecord vsb_product_finalize_record(")
    )
    assert finalize.index("product_water_sample(") < finalize.index(
        "product_apply_night_water_pair("
    )


def test_fl4737_submerged_retained_material_contract() -> None:
    _assert_contract(*_sources())


@pytest.mark.parametrize(
    ("path", "old", "new"),
    (
        (
            VSB_SHADER,
            "const uint VISIBLE_SURFACE_ABI_VERSION = 4u;",
            "const uint VISIBLE_SURFACE_ABI_VERSION = 2u;",
        ),
        (
            VSB_SHADER,
            "    bool query_valid = query_current_camera_surface(\n"
            "        ray,\n"
            "        nearest,\n"
            "        pre_water_source_bits,\n"
            "        pre_water_direct_contact,\n"
            "        pre_water_depth\n"
            "    );",
            "    bool query_valid = false;",
        ),
        (
            VSB_SHADER,
            "bool finite_water_winner = nearest.owner_kind == OWNER_WATER",
            "bool finite_water_winner = nearest.present",
        ),
        (
            VSB_SHADER,
            "record.status_flags = STATUS_VALID\n"
            "        | fl4737_pending_submerged_source_bits;",
            "record.status_flags = STATUS_VALID;",
        ),
        (
            PRODUCT_SHADER,
            "if (!source.present || source.owner_kind == OWNER_TERRAIN) {",
            "if (!source.present) {",
        ),
        (
            PRODUCT_SHADER,
            "s.submerged_visibility = cell_facts.submerged_source_present\n"
            "\t\t? bed_visibility : 0.0;",
            "s.submerged_visibility = cell_facts.submerged_source_present\n"
            "\t\t? 1.0 : 0.0;",
        ),
        (
            PRODUCT_SHADER,
            "if (water_slot_class == 2u\n"
            "\t\t\t&& water.submerged_source_present",
            "if (water.submerged_source_present",
        ),
        (
            PRODUCT_SHADER,
            "(record.status_flags & ~VSB_STATUS_HIT_KNOWN_MASK) != 0u",
            "(record.status_flags & ~VSB_STATUS_HIT_KNOWN_MASK) != VSB_STATUS_VALID",
        ),
        (
            PRODUCT_SHADER,
            "(record.status_flags & VSB_STATUS_VALID) == 0u",
            "(record.status_flags & VSB_STATUS_VALID) != 0u",
        ),
        # FL-4737 F8 revision 2 (AD2 night guard): deleting the bed substitution
        # ungated is what revision 1 did. At FULL NIGHT the lake floor then
        # appears at 0.1476 in every submerged-source cell where canon is 0.0000.
        (
            PRODUCT_SHADER,
            "\tvec3 bed = cell_facts.submerged_source_present\n"
            "\t\t? cell_facts.submerged_source_bg : cell_facts.bed;",
            "\tvec3 bed = cell_facts.bed;",
        ),
        # AD1: dropping the mirror cap restores the state where reflected ink
        # 0.78 painted over a source arriving at 0.097 -- the missing wrist.
        (
            PRODUCT_SHADER,
            "\t\t\t* mass_gain\n"
            "\t\t\t* clamp(mirror_admission, 0.0, 1.0);",
            "\t\t\t* mass_gain;",
        ),
        # AD1: a second composition site below the D3 cloud lift caps that lift
        # by column thickness. Revision 1 delivered 5 percent of it at the
        # waterline and 50 percent deep.
        (
            PRODUCT_SHADER,
            "\t\tproduct_fl4216_apply_reflection_pair(\n"
            "\t\t\tvsb_product_selected_reflected_paint,",
            "\t\tbackground = mix(\n"
            "\t\t\tbackground, s.submerged_source_fg, s.submerged_visibility);\n"
            "\t\tproduct_fl4216_apply_reflection_pair(\n"
            "\t\t\tvsb_product_selected_reflected_paint,",
        ),
        (
            VSB_HOST,
            "if submerged_bits != 0 and owner_kind != OWNER_WATER:",
            "if false:",
        ),
        (
            PRODUCT_HOST,
            "or vsb_submerged_owner_code < 1\n"
            "\t\t\tor vsb_submerged_owner_code > 3",
            "or vsb_submerged_owner_code > 3",
        ),
        (
            PRODUCT_HOST,
            "(vsb_status & ~VSB_STATUS_KNOWN_MASK) != 0",
            "(vsb_status & ~VSB_STATUS_KNOWN_MASK) != VSB_STATUS_VALID",
        ),
        (
            PRODUCT_HOST,
            "(vsb_status & VSB_STATUS_VALID) == 0",
            "(vsb_status & VSB_STATUS_VALID) != 0",
        ),
    ),
)
def test_fl4737_damaging_mutations_turn_red(
    path: Path, old: str, new: str
) -> None:
    vsb, product, vsb_host, product_host = _sources()
    current = {
        VSB_SHADER: vsb,
        PRODUCT_SHADER: product,
        VSB_HOST: vsb_host,
        PRODUCT_HOST: product_host,
    }
    assert current[path].count(old) == 1, old
    current[path] = current[path].replace(old, new, 1)
    with pytest.raises(AssertionError):
        _assert_contract(
            current[VSB_SHADER],
            current[PRODUCT_SHADER],
            current[VSB_HOST],
            current[PRODUCT_HOST],
        )

