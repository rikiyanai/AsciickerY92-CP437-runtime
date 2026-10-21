"""FL-4708 rounded retained-tree publication and Product-paint contract.

This is destructive structural evidence for the F7 source slice. It proves the
published geometry/paint/shadow route, not viewport density, temporal stability,
reflection quality, or operator acceptance.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
COMPILER = ROOT / "scripts" / "compile_fl4789_chunk_catalog.py"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
VSB = ROOT / "godot_project" / "render" / "backends" / "ortho" / "visibility" / "visible_surface_builder.glsl"


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"\bfunc\s+{re.escape(name)}\s*\(", source)
    if match is not None:
        # GDScript is indentation-delimited. Do not mistake a Dictionary literal
        # inside the function for a C-style function brace.
        start_line = source.rfind("\n", 0, match.start()) + 1
        next_func = source.find("\nfunc ", match.end())
        return source[start_line:] if next_func < 0 else source[start_line:next_func]
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S
    )
    assert match is not None, f"missing function {name}"
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"unterminated function {name}")


def _compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    source = re.sub(r"#[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def _assert_contract(
    world: str, product: str, vsb: str, compiler_source: str | None = None
) -> None:
    compiler = compiler_source if compiler_source is not None else COMPILER.read_text()
    install = _compact(_function_body(world, "_install_static_world_mesh"))
    material = _compact(
        _function_body(product, "q3_probe_payload_apply_material")
    )
    signed_normal = _compact(
        _function_body(product, "product_signed_probe_normal")
    )
    stable_normal = _compact(
        _function_body(product, "product_stable_probe_normal")
    )
    tree_material = _compact(
        _function_body(product, "product_retained_tree_material")
    )
    tree_palette = _compact(
        _function_body(product, "product_retained_tree_paint_pair")
    )
    static_anchor_match = _compact(
        _function_body(product, "vsb_retained_static_anchor_matches")
    )
    static_local_facts = _compact(
        _function_body(product, "vsb_retained_static_face_local_facts")
    )
    dynamic_local_facts = _compact(
        _function_body(product, "vsb_retained_dynamic_face_local_facts")
    )
    transported_paint = _compact(
        _function_body(product, "product_water_transported_source_paint")
    )
    grass_cycles = _compact(
        _function_body(product, "foliage_saved_static_cycle_glyph")
    )
    reflection_family = _compact(
        _function_body(vsb, "fl4216_reflection_material_family")
    )
    caster = _compact(
        _function_body(product, "vsb_retained_consider_static_caster")
    )
    terrain_shadow = _compact(
        _function_body(product, "q3_probe_payload_static_shadow_density")
    )

    assert "constFL4708_AUTHORED_TREE_COUNT:=179" in _compact(world)
    assert "constFL4708_TREE_LAB_COUNT:=3" in _compact(world)
    assert (
        "constFL4708_TREE_COUNT:=FL4708_AUTHORED_TREE_COUNT"
        "+FL4708_TREE_LAB_COUNT" in _compact(world)
    )
    assert "fl4708_y8_tree_instances.v1.json" not in world
    assert "_append_fl4708_retained_tree" not in world
    assert "_preload_fl4789_authored_tree_assets" not in world
    assert "_install_fl4789_compiled_tree_registry" in world
    assert "admit_compiled_asset" in world
    assert "register_resident_instance" in world
    assert "seal_instance_registry" in world
    assert "replace_resident_instances" in world
    assert "scene_db.build(face_rows)" in _compact(world)
    assert 'effect.call("set_scene_geometry_db",scene_db,scene_receipt)' in _compact(world)

    compiler_compact = _compact(compiler)
    assert 'PAYLOAD_MAGIC=b"FL4789P1"' in compiler_compact
    assert "defbuild_tree_local_rows(" in compiler_compact
    assert "source_id=1201+grove_index" in compiler_compact
    assert "_build_blas(rows)" in compiler_compact
    assert "_rows_aabb(rows)" in compiler_compact
    assert '"runtime_tree_generation_count":0' not in compiler_compact

    # Retained tree geometry is READ from the authored .akm assets. The compiler
    # opens each canonical mesh through the shared ASCII-PLY reader and emits its
    # own triangles; it does not build a trunk, a branch, or a canopy lobe.
    assert "defload_tree_mesh(" in compiler_compact
    assert "def_classify_tree_mesh(" in compiler_compact
    assert "def_authored_local_columns(" in compiler_compact
    assert "parse_akm_ply(str(path))" in compiler_compact
    assert 'AKM_READER_PATH=Path(__file__).resolve().parent/"bake_akm_to_static_world_faces.py"' in compiler_compact
    for mesh_name in (
        "tree-1.akm",
        "tree-2.akm",
        "tree-3.akm",
        "old-tree-1.akm",
        "old-tree-2.akm",
        "osm-tree-pole.akm",
    ):
        assert f'"{mesh_name}",' in compiler_compact
    assert "ifmesh_namenotinCANONICAL_TREE_MESHES:" in compiler_compact

    # Every generator that produced the lobed stand-in canopy is deleted, along
    # with the two root-decoration materials only that generator could emit.
    for deleted in (
        "_append_trunk",
        "_append_root_zone",
        "_append_branch",
        "_append_canopy_lobe",
        "lobe_count",
        "lobe_seed",
        "tree_identity",
        "TREE_ROOT_MOSS_MAT",
        "TREE_ROOT_FLOWER_MAT",
        "TREE_TRUNK_FACE_COUNT",
        "TREE_ROOT_ZONE_FACE_COUNT",
    ):
        assert deleted not in compiler

    # The four retained material ids and the authored rules that select them.
    assert "TREE_TRUNK_MAT=240" in compiler_compact
    assert "TREE_CANOPY_CONTOUR_MAT=241" in compiler_compact
    assert "TREE_CANOPY_INTERIOR_MAT=242" in compiler_compact
    assert "TREE_CANOPY_UNDERSIDE_MAT=243" in compiler_compact
    assert "AKM_TREE_BARK_LINE=0.32" in compiler_compact
    assert "AKM_CANOPY_CONTOUR_ALIGNMENT=0.36" in compiler_compact
    assert "leaf_dominant=colour[1]>max(colour[0],colour[2])" in compiler_compact
    assert (
        "is_canopy.append(leaf_dominantandheight_fraction>=AKM_TREE_BARK_LINE)"
        in compiler_compact
    )
    assert "material=TREE_CANOPY_CONTOUR_MAT" in compiler_compact
    assert "material=TREE_CANOPY_UNDERSIDE_MAT" in compiler_compact

    # The authored A3D basis reaches the published rows intact: A3D X/Y are
    # horizontal cells and A3D Z is 1/16-cell height steps, and the runtime's own
    # yaw and uniform scale are divided back out of the immutable local rows.
    assert "A3D_HEIGHT_SCALE=16.0" in compiler_compact
    assert "godot_y=a3d_z/A3D_HEIGHT_SCALE" in compiler_compact
    assert "inverse_scale=1.0/scale" in compiler_compact
    assert (
        "((cosine*godot_x+sine*godot_z)*inverse_scale,godot_y*inverse_scale,"
        "(-sine*godot_x+cosine*godot_z)*inverse_scale,)" in compiler_compact
    )

    for forbidden in ("randf(", "randi(", "Time.", "camera", "projection"):
        assert forbidden not in compiler
    assert "constuintSTATIC_WORLD_TREE_TRUNK_MAT=240u;" in _compact(product)
    assert "constuintSTATIC_WORLD_TREE_CANOPY_MAT_FIRST=241u;" in _compact(product)
    assert "constuintSTATIC_WORLD_TREE_CANOPY_MAT_LAST=243u;" in _compact(product)
    assert "constuintSTATIC_WORLD_TREE_ROOT_MOSS_MAT=244u;" in _compact(product)
    assert "constuintSTATIC_WORLD_TREE_ROOT_FLOWER_MAT=245u;" in _compact(product)
    assert (
        "if(!vsb_retained_static_tree_material(material)"
        "||product_vsb_frame.view_identity.z!=VSB_PROJECTION_PERSPECTIVE){"
        "returnvsb_retained_anchor_matches_exact(retained_anchor,selected_anchor);}"
        in static_anchor_match
    )
    assert (
        "floatBitsToUint(retained_anchor.w)!=floatBitsToUint(1.0)"
        "||floatBitsToUint(selected_anchor.w)!=floatBitsToUint(1.0)"
        in static_anchor_match
    )
    assert (
        "vec3delta=abs(retained_anchor.xyz-selected_anchor.xyz);"
        "floatepsilon=max(product_vsb_frame.ray_parameters.w*64.0,1.0e-3);"
        "returnall(lessThanEqual(delta,vec3(epsilon)));"
        in static_anchor_match
    )
    assert (
        "||!vsb_retained_static_anchor_matches("
        "identity.material,retained_anchor,identity.stable_world_anchor))returnfalse;"
        in static_local_facts
    )
    assert "vsb_retained_static_anchor_matches(" not in dynamic_local_facts
    assert "vsb_retained_anchor_matches(retained_anchor,identity.stable_world_anchor)" in dynamic_local_facts
    assert (
        "boolretained_tree_surface=context.surface_kind==OWNER_STATIC_MESH"
        "&&product_retained_tree_material(context.material);" in material
    )
    # 3c3b97acb: only retained tree mats 240..243 preserve authored normal sign.
    # Every other static/dynamic mesh retains the established positive-Y fold.
    assert signed_normal == (
        "floatl=length(nrm);if(l<1e-4)returnvec3(0.0,1.0,0.0);"
        "returnnrm/l;"
    )
    assert stable_normal == (
        "nrm=product_signed_probe_normal(nrm);"
        "returnnrm.y<0.0?-nrm:nrm;"
    )
    assert (
        "precisevec3material_normal=retained_tree_surface?"
        "product_signed_probe_normal(context.payload_normal):"
        "product_stable_probe_normal(context.payload_normal);" in material
    )
    assert (
        "q3_probe_payload_cast_shadow_attenuation("
        "context.anchor_world,material_normal,context.surface_kind," in material
    )
    assert "precisevec3nrm=material_normal;" in material
    assert (
        "floatlambert_raw=max(dot(nrm,"
        "vsb_product_selected_effective_sun_dir),0.0);" in material
    )
    assert "product_quantise_shd(lambert,stable_cell_noise(" in material
    assert "if(retained_tree_surface){" in material
    assert (
        "product_retained_tree_paint_pair("
        "context.material,sub_i,nrm,anchor_cell,glyph,fg,bg);" in material
    )
    assert "if(context.surface_kind==OWNER_DYNAMIC_MESH)" in material
    assert "q3_probe_payload_cutover_cloud_shadow_for_cell(" in material
    assert "fg*=mesh_hf_shadow*mesh_cloud_atten;" in material
    assert (
        "product_apply_ambient_base(fg,bg,nrm,lambert_raw,0.0,"
        "TERRAIN_FAMILY_UNKNOWN);" in material
    )

    # Direct and reflected source paint still share one palette/morphology owner.
    assert tree_material == (
        "returnmaterial>=STATIC_WORLD_TREE_TRUNK_MAT"
        "&&material<=STATIC_WORLD_TREE_ROOT_FLOWER_MAT;"
    )
    assert (
        "booltrunk=material==STATIC_WORLD_TREE_TRUNK_MAT;"
        "boolroot_moss=material==STATIC_WORLD_TREE_ROOT_MOSS_MAT;"
        "boolroot_flower=material==STATIC_WORLD_TREE_ROOT_FLOWER_MAT;"
        # Trunk bark is the authored .akm colour, not an invented cream. The
        # faces the compiler assigns to material 240 average (0.035,0.110,0.063)
        # in tree-1, (0.224,0.196,0.129) in tree-2 and (0.055,0.027,0.016) in
        # tree-3 -- all dark, two plainly brown. The previous 0.96/0.84/0.68 was
        # about five times too bright and the wrong hue.
        "glyph=0xB3u;vec3tree_base=vec3(0.14,0.112,0.073);"
        in tree_palette
    )
    assert "boolroot_moss=material==STATIC_WORLD_TREE_ROOT_MOSS_MAT;" in tree_palette
    assert "boolroot_flower=material==STATIC_WORLD_TREE_ROOT_FLOWER_MAT;" in tree_palette
    assert "if(root_moss){glyph=0xB0u;" in tree_palette
    assert "if(root_flower){glyph=42u;" in tree_palette
    # Face-authored canopy roles own the palette: sparse outer shell, dense
    # interior mass, then the occluded underside. These are no longer per-tree
    # hue alternatives selected by a normal-only classifier.
    for role_colour in (
        "vec3(0.18,0.30,0.14)",
        "vec3(0.12,0.23,0.11)",
        "vec3(0.09,0.16,0.08)",
    ):
        assert role_colour in tree_palette
    assert (
        "if(trunk){"
        "ink=clamp(tree_base*mix(0.84,1.18,tree_band),"
        "vec3(0.0),vec3(1.0));"
        "paper=clamp(tree_base*mix(0.58,0.86,tree_band),"
        "vec3(0.0),vec3(1.0));return;}"
        in tree_palette
    )
    assert (
        "floatbase_luma=max(rec709_luminance(tree_base),0.0001);"
        "ink=clamp(tree_base*(mix(0.32,0.54,tree_band)/base_luma),"
        "vec3(0.0),vec3(1.0));"
        "paper=clamp(tree_base*(mix(0.16,0.30,tree_band)/base_luma),"
        "vec3(0.0),vec3(1.0));"
        in tree_palette
    )
    assert (
        "vec3nrm=retained_normal;floatnrm_len=length(nrm);"
        "if(!(nrm_len>1.0e-4)){nrm=vec3(0.0,1.0,0.0);}" in tree_palette
    )
    assert "if(nrm.y<0.0)nrm=-nrm;" not in tree_palette
    assert (
        "uintnormal_key=(nrm.x>=0.0?1u:0u)"
        "|(nrm.y>=0.0?2u:0u)|(nrm.z>=0.0?4u:0u);" in tree_palette
    )
    assert "uintworld_key=uint(anchor_cell.x)*73856093u" in tree_palette
    # The selected leaf stroke is stable from the retained tree facts.  Product
    # must not phase it from a feature clock or camera-dependent value.
    assert "uintglyph_slot=world_key%6u;" in tree_palette
    assert (
        "uintouter_sweep[6]=uint[6](764u,765u,766u,767u,768u,769u);"
        in tree_palette
    )
    assert (
        "uintouter_angle[6]=uint[6](742u,745u,740u,751u,756u,761u);"
        in tree_palette
    )
    assert (
        "uintdense_stroke[6]=uint[6](774u,775u,776u,777u,778u,779u);"
        in tree_palette
    )
    assert "boolassembled_crown_contour=material==STATIC_WORLD_TREE_CANOPY_MAT_FIRST;" in tree_palette
    assert "boolunderside=material==STATIC_WORLD_TREE_CANOPY_MAT_LAST;" in tree_palette
    assert "if(underside){" in tree_palette
    assert "elseif(assembled_crown_contour){" in tree_palette
    assert "glyph=(world_key&1u)==0u?outer_sweep[glyph_slot]:outer_angle[glyph_slot];" in tree_palette
    assert "mix(0.72,1.20,tree_band)" in tree_palette
    assert "mix(0.68,1.10,tree_band)" in tree_palette
    for forbidden in (
        "view_identity",
        "VSB_PROJECTION_",
        "camera",
        "screen",
        "clock",
        "frame_time",
        "feature_params.x",
        "176u",
        "177u",
        "178u",
    ):
        assert forbidden not in tree_palette
    assert _compact(product).count("product_retained_tree_paint_pair(") == 3
    assert "product_retained_tree_paint_pair(" in transported_paint

    # The established grass cycles stay live. FL-4708's three new stroke pools
    # are tree-only and must not replace them.
    for grass_cycle_id in (
        "1842u", "1845u", "1734u", "1848u"
    ):
        assert grass_cycle_id in grass_cycles
    for tree_only_cycle_id in ("764u", "742u", "774u"):
        assert tree_only_cycle_id not in grass_cycles

    # VSB classifies canopy 241..243 and compiler-owned root flora 244..245 as
    # foliage family 4. Trunk 240, every other static mesh, and dynamic movers
    # remain solid family 5; the existing packed word transports identity.
    vsb_whole = _compact(vsb)
    assert "constuintFL4216_REFLECTION_FAMILY_MESH_FOLIAGE=4u;" in vsb_whole
    assert "constuintFL4216_REFLECTION_FAMILY_MESH_SOLID=5u;" in vsb_whole
    assert "constuintFL4708_TREE_CANOPY_MAT_FIRST=241u;" in vsb_whole
    assert "constuintFL4708_TREE_CANOPY_MAT_LAST=243u;" in vsb_whole
    assert "constuintFL4708_TREE_ROOT_FLOWER_MAT=245u;" in vsb_whole
    assert (
        "if(owner_kind==OWNER_STATIC_MESH){"
        "if(material>=FL4708_TREE_CANOPY_MAT_FIRST"
        "&&material<=FL4708_TREE_ROOT_FLOWER_MAT){"
        "returnFL4216_REFLECTION_FAMILY_MESH_FOLIAGE;}"
        "returnFL4216_REFLECTION_FAMILY_MESH_SOLID;}"
        "if(owner_kind==OWNER_DYNAMIC_MESH){"
        "returnFL4216_REFLECTION_FAMILY_MESH_SOLID;}" in reflection_family
    )

    # Tree faces share the retained static caster bank. Material 250 is the sole
    # excluded fixture; the admitted 240..245 tree materials are not filtered.
    assert "if(facts.material_id==250u)returnfalse;" in caster
    for tree_mat in ("240u", "241u", "242u", "243u", "244u", "245u"):
        assert tree_mat not in caster
    assert "vsb_retained_select_q3_static_shadow_caster(" in terrain_shadow
    assert "returnselected.valid?1.0:0.0;" in terrain_shadow


def test_fl4708_rounded_retained_tree_contract() -> None:
    _assert_contract(
        WORLD.read_text(encoding="utf-8"),
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
        VSB.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    ("path", "old", "new"),
    (
        (
            WORLD,
            "const FL4708_AUTHORED_TREE_COUNT := 179",
            "const FL4708_AUTHORED_TREE_COUNT := 0",
        ),
        # Silently stop reading the authored mesh library.
        (
            COMPILER,
            "def _classify_tree_mesh(",
            "def _append_canopy_lobe(",
        ),
        # Paint every triangle as canopy, which is what a stand-in generator
        # with no authored colour signal would produce.
        (
            COMPILER,
            "leaf_dominant = colour[1] > max(colour[0], colour[2])",
            "leaf_dominant = True",
        ),
        # Move the trunk/canopy split to a place no authored asset agrees with.
        (
            COMPILER,
            "AKM_TREE_BARK_LINE = 0.32",
            "AKM_TREE_BARK_LINE = 0.90",
        ),
        # Drop the A3D height-step conversion, which would stretch every tree
        # sixteen times taller than its authored placement.
        (
            COMPILER,
            "godot_y = a3d_z / A3D_HEIGHT_SCALE",
            "godot_y = a3d_z",
        ),
        (
            PRODUCT,
            "? product_signed_probe_normal(context.payload_normal)\n"
            "\t\t: product_stable_probe_normal(context.payload_normal);",
            "? product_stable_probe_normal(context.payload_normal)\n"
            "\t\t: product_stable_probe_normal(context.payload_normal);",
        ),
        (
            PRODUCT,
            "\t\t: product_stable_probe_normal(context.payload_normal);",
            "\t\t: product_signed_probe_normal(context.payload_normal);",
        ),
        (
            PRODUCT,
            "context.anchor_world,\n\t\t\t\tmaterial_normal,",
            "context.anchor_world,\n\t\t\t\t"
            "product_stable_probe_normal(context.payload_normal),",
        ),
        (
            PRODUCT,
            "precise vec3 nrm = material_normal;",
            "precise vec3 nrm = product_stable_probe_normal(material_normal);",
        ),
        (
            PRODUCT,
            "tree_base = vec3(0.14, 0.112, 0.073)",
            "tree_base = vec3(0.02, 0.02, 0.02)",
        ),
        (
            PRODUCT,
            "product_retained_tree_paint_pair(\n"
            "\t\t\tsource.material, sub_i, source.normal, water_cell,\n"
            "\t\t\tpaint.glyph, paint.fg, paint.bg);",
            "paint.fg = vec3(0.5);\n\t\tpaint.bg = vec3(0.5);",
        ),
        (
            PRODUCT,
            "mix(0.32, 0.54, tree_band)",
            "mix(0.20, 0.32, tree_band)",
        ),
        (
            PRODUCT,
            "mix(0.16, 0.30, tree_band)",
            "mix(0.08, 0.18, tree_band)",
        ),
        (
            PRODUCT,
            "uint normal_key = (nrm.x >= 0.0 ? 1u : 0u)",
            "if (nrm.y < 0.0) nrm = -nrm;\n"
            "\tuint normal_key = (nrm.x >= 0.0 ? 1u : 0u)",
        ),
        (
            PRODUCT,
            "uint glyph_slot = world_key % 6u;",
            "uint glyph_slot = material % 6u;",
        ),
        (
            VSB,
            "material >= FL4708_TREE_CANOPY_MAT_FIRST\n"
            "\t\t\t\t&& material <= FL4708_TREE_ROOT_FLOWER_MAT",
            "material >= 240u\n"
            "\t\t\t\t&& material <= FL4708_TREE_ROOT_FLOWER_MAT",
        ),
        (
            VSB,
            "return FL4216_REFLECTION_FAMILY_MESH_FOLIAGE;",
            "return FL4216_REFLECTION_FAMILY_MESH_SOLID;",
        ),
        (
            PRODUCT,
            "if (facts.material_id == 250u) return false;",
            "if (facts.material_id >= 240u) return false;",
        ),
        (
            PRODUCT,
            "float epsilon = max(product_vsb_frame.ray_parameters.w * 64.0, 1.0e-3);",
            "float epsilon = max(product_vsb_frame.ray_parameters.w * 64.0, 0.0);",
        ),
        (
            PRODUCT,
            "|| !vsb_retained_static_anchor_matches(\n"
            "\t\t\t\tidentity.material, retained_anchor,\n"
            "\t\t\t\tidentity.stable_world_anchor)) return false;",
            "|| !vsb_retained_anchor_matches(\n"
            "\t\t\t\tretained_anchor,\n"
            "\t\t\t\tidentity.stable_world_anchor)) return false;",
        ),
    ),
)
def test_fl4708_damaging_mutations_turn_red(
    path: Path, old: str, new: str
) -> None:
    world = WORLD.read_text(encoding="utf-8")
    compiler = COMPILER.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    vsb = VSB.read_text(encoding="utf-8")
    target = (
        world
        if path == WORLD
        else compiler
        if path == COMPILER
        else product
        if path == PRODUCT
        else vsb
    )
    assert target.count(old) == 1, old
    target = target.replace(old, new, 1)
    if path == WORLD:
        world = target
    elif path == COMPILER:
        compiler = target
    elif path == PRODUCT:
        product = target
    else:
        vsb = target
    with pytest.raises(AssertionError):
        _assert_contract(world, product, vsb, compiler)
