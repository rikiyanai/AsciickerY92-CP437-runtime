#!/usr/bin/env python3
"""FL-4431 loader-owned elevation-row publication contract.

The A3D map loader evaluates the immutable six-neighbour elevation-flag law
once and publishes the final 0..3 row as one R8 image.  World installs that
image, ASCIICompositorEffect uploads it as R8UI and binds Product slot 15, and
the Q3 Product compiler performs one guarded integer texel fetch per terrain
subcell.

The contract compiles every shader mutant before rejecting it.  Loader mutants
are parsed by Godot before rejection, so syntax damage cannot masquerade as
coverage.  This is structural and engine-fixture evidence; it does not claim a
rendered-frame result or the FL-4431 60 FPS target.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "godot_project"
LOADER = PROJECT / "scripts/map/A3DMapLoader.gd"
WORLD = PROJECT / "scripts/World.gd"
HOST = PROJECT / "render/api/ASCIICompositorEffect.gd"
PRODUCT = PROJECT / "render/product/cell_owner_product.glsl"


@dataclass(frozen=True)
class Sources:
    loader: str
    world: str
    host: str
    product: str


@dataclass(frozen=True)
class Mutant:
    name: str
    sources: Sources
    language: str


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def ordered(body: str, tokens: tuple[str, ...]) -> bool:
    cursor = -1
    for token in tokens:
        cursor = body.find(token, cursor + 1)
        if cursor < 0:
            return False
    return True


def gdscript_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?m)^func\s+{re.escape(name)}\s*\([^)]*\)(?:\s*->\s*[^:]+)?:\s*$",
        source,
    )
    if match is None:
        return ""
    next_function = re.search(r"(?m)^func\s+", source[match.end() :])
    end = (
        match.end() + next_function.start()
        if next_function is not None
        else len(source)
    )
    return source[match.start() : end]


def glsl_function(source: str, name: str) -> str:
    match = re.search(
        (
            rf"(?m)^[A-Za-z_][A-Za-z0-9_<>, \t]*\b{re.escape(name)}"
            r"\s*\([^;{}]*\)\s*\{"
        ),
        source,
    )
    if match is None:
        return ""
    opening = source.find("{", match.start())
    depth = 0
    for cursor in range(opening, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : cursor + 1]
    return ""


def replace_once(source: str, old: str, new: str, name: str) -> str:
    count = source.count(old)
    if count != 1:
        raise AssertionError(
            f"{name}: mutation target count={count}, expected 1 for {old!r}"
        )
    return source.replace(old, new, 1)


def contract_errors(sources: Sources) -> tuple[str, ...]:
    errors: list[str] = []
    loader = compact(
        gdscript_function(sources.loader, "build_authoritative_elev_image")
    )
    world_install = compact(
        gdscript_function(sources.world, "_install_authoritative_elevation_rows")
    )
    world_setup = compact(
        gdscript_function(sources.world, "_install_ascii_compositor")
    )
    host_set = compact(
        gdscript_function(sources.host, "set_authoritative_elev")
    )
    host_upload = compact(
        gdscript_function(sources.host, "_recreate_terrain_elev")
    )
    host_dispatch = compact(
        gdscript_function(sources.host, "_prepare_cell_owner_product_dispatch")
    )
    shader = compact(sources.product)
    q3_elevation_row = compact(
        glsl_function(sources.product, "q3_probe_payload_elevation_row")
    )
    row_owner = compact(
        glsl_function(sources.product, "product_elevation_row_at")
    )
    shade = compact(
        glsl_function(sources.product, "q3_probe_payload_compile_terrain_shade")
    )
    stable_shade = compact(
        glsl_function(
            sources.product, "q3_probe_payload_compile_stable_terrain_shade"
        )
    )
    material = compact(
        glsl_function(sources.product, "q3_probe_payload_apply_material")
    )
    morphology = compact(
        glsl_function(sources.product, "product_compile_terrain_morphology")
    )
    compiler = compact(
        glsl_function(sources.product, "compile_probe_subcell_payload")
    )
    mountain_blank = compact(
        glsl_function(sources.product, "product_mountain_blank_glyph")
    )
    mountain_payload = compact(
        glsl_function(sources.product, "product_mountain_payload_glyph")
    )

    loader_law = (
        "varraw_flags:=PackedByteArray()",
        "raw_flags.resize(w*h)",
        "varelev:=(cell_visual>>15)&0x1",
        "raw_flags[idx]=elev",
        "varelevation_rows:=PackedByteArray()",
        "elevation_rows.resize(w*h)",
        "vary_lo:=maxi(y-1,0)",
        "vary_hi:=mini(y+1,h-1)",
        "varx_hi:=mini(x+1,w-1)",
        "vare_mi:=("
        "int(raw_flags[y*w+x])+int(raw_flags[y*w+x_hi]))",
        "vare_lo:=("
        "int(raw_flags[y_lo*w+x])+int(raw_flags[y_lo*w+x_hi]))",
        "vare_hi:=("
        "int(raw_flags[y_hi*w+x])+int(raw_flags[y_hi*w+x_hi]))",
        "ife_mi==0:elevation_row=3",
        "elife_mi>=2:elevation_row=1",
        "elife_lo<=0:elevation_row=3ife_hi<=0else2",
        "else:elevation_row=0ife_hi<=0else1",
        "elevation_rows[y*w+x]=elevation_row",
        "returnImage.create_from_data("
        "w,h,false,Image.FORMAT_R8,elevation_rows)",
    )
    if not loader or not ordered(loader, loader_law):
        errors.append("loader six-neighbour final-row law drifted")
    if loader.count("int(raw_flags[") != 6:
        errors.append("loader must consume exactly six raw flag neighbours")
    if "elevation_rows[y*w+x]=raw_flags" in loader:
        errors.append("loader republished raw A3D flags instead of final rows")

    world_law = (
        'loader.has_method("build_authoritative_elev_image")',
        'effect.has_method("set_authoritative_elev")',
        "varelev_img:Image=loader.build_authoritative_elev_image()",
        "ifelev_img!=null:",
        "effect.set_authoritative_elev(elev_img)",
    )
    if not world_install or not ordered(world_install, world_law):
        errors.append("World elevation publication handoff drifted")
    setup_law = (
        "_install_authoritative_terrain_height(ascii_effect)",
        "_install_authoritative_elevation_rows(ascii_effect)",
        "_install_static_world_mesh(ascii_effect)",
        "_install_firefly_grass_anchors(ascii_effect)",
    )
    if not world_setup or not ordered(world_setup, setup_law):
        errors.append("World elevation installation acquired an unrelated gate")

    host_validation = (
        "_pending_terrain_elev_img=imgif(",
        "img!=null",
        "img.get_format()==Image.FORMAT_R8",
        "img.get_size()==terrain_mat_dims",
        ")elsenull",
        "ifrd!=null:",
        "RenderingServer.call_on_render_thread(_recreate_terrain_elev)",
    )
    if not host_set or not ordered(host_set, host_validation):
        errors.append("host elevation image freeze seam drifted")
    upload_law = (
        "ifterrain_elev_rd.is_valid():",
        "rd.free_rid(terrain_elev_rd)",
        "terrain_elev_rd=RID()",
        "if_pending_terrain_elev_img==null:return",
        "terrain_elev_rd=_upload_r8_uint_image(_pending_terrain_elev_img)",
    )
    if not host_upload or not ordered(host_upload, upload_law):
        errors.append("host elevation R8UI upload drifted")
    host_binding_law = (
        "ifnotterrain_elev_rd.is_valid():",
        "return{}",
        "varelev_bind:RID=terrain_elev_rd",
        "varu_elev:=RDUniform.new()",
        "u_elev.uniform_type="
        "RenderingDevice.UNIFORM_TYPE_SAMPLER_WITH_TEXTURE",
        "u_elev.binding=15",
        "u_elev.add_id(sampler_rd)",
        "u_elev.add_id(elev_bind)",
    )
    if not host_dispatch or not ordered(host_dispatch, host_binding_law):
        errors.append("host Product binding-15 publication drifted")

    declaration = (
        "layout(set=0,binding=15)uniformusampler2Dterrain_elev_tex;"
    )
    if declaration not in shader:
        errors.append("Product elevation binding/class drifted")
    for retired in (
        "q3_probe_payload_elev_at(",
        "q3_probe_payload_elv_for_cell(",
        "product_elev_at(",
    ):
        if retired in shader:
            errors.append(f"retired raw-elevation shader helper survived: {retired}")
    if shader.count("texelFetch(terrain_elev_tex") != 1:
        errors.append("Product must have one binding-15 texel fetch owner")
    if not row_owner:
        errors.append("shared Product elevation-row owner is missing")
    else:
        row_owner_law = (
            "ivec2dims=ivec2(max(params.misc.y,1),max(params.misc.z,1));",
            "cell=clamp(cell,ivec2(0),dims-ivec2(1));",
            "returnint(texelFetch(terrain_elev_tex,cell,0).r);",
        )
        if not ordered(row_owner, row_owner_law):
            errors.append("shared Product elevation coordinate/fetch law drifted")
        if "params.terrain_params.zw" in row_owner:
            errors.append("elevation owner uses the host-zero terrain dims lane")
    if not q3_elevation_row:
        errors.append("typed Q3 elevation-row accessor is missing")
    else:
        accessor_law = (
            "if(!q3_probe_payload_elevation_valid(flags))return0;",
            "returnproduct_elevation_row_at(cell);",
        )
        if not ordered(q3_elevation_row, accessor_law):
            errors.append("guarded Q3 elevation-row accessor drifted")
        if "texelFetch(" in q3_elevation_row:
            errors.append("Q3 accessor bypasses the single binding-15 owner")
        if any(
            token in q3_elevation_row for token in ("e_mi", "e_lo", "e_hi")
        ):
            errors.append("Product resurrected the loader-owned six-neighbour law")
    if (
        "shade.elv=q3_probe_payload_elevation_row(cell,flags);"
        not in stable_shade
        or stable_shade.count("q3_probe_payload_elevation_row(") != 1
        or "q3_probe_payload_elevation_row(" in shade
        or "q3_probe_payload_compile_stable_terrain_shade(" not in shade
    ):
        errors.append("terrain shade compiler elevation-row call count drifted")
    material_law = (
        "outintterrain_elevation_row",
        "terrain_elevation_row=0;",
        "Q3ProbeTerrainShadeshade="
        "q3_probe_payload_compile_terrain_shade(",
        "TerrainMorphologymorphology=product_compile_terrain_morphology(",
        "terrain_elevation_row=morphology.elevation_row;",
    )
    if not material or not ordered(material, material_law):
        errors.append("terrain elevation row was not returned by material phase")
    compiler_law = (
        "intterrain_elevation_row=0;",
        "q3_probe_payload_apply_material(",
        "terrain_face_role,terrain_elevation_row,foliage_root_ramp_band,"
        "terrain_cell_glyph_class,terrain_mountain_glyph_remapped,"
        "receiver_cast_shadow_attenuation);",
    )
    if not compiler or not ordered(compiler, compiler_law):
        errors.append("terrain elevation row was not threaded to mountain paint")
    morphology_law = (
        "intelevation_row,intshade_row",
        "morphology.elevation_row=elevation_row;",
        "terrain.glyph=product_mountain_payload_glyph("
        "terrain.glyph,uint(max(family,0)),context.material_cell,"
        "elevation_row,dirt_mountain);",
    )
    if not morphology or not ordered(morphology, morphology_law):
        errors.append("shared terrain morphology lost the carried elevation row")
    for name, mountain in (
        ("blank", mountain_blank),
        ("payload", mountain_payload),
    ):
        if "product_elevation_row_at(" in mountain or "texelFetch(" in mountain:
            errors.append(f"mountain {name} helper reopened binding 15")
        if "intelevation_row" not in mountain:
            errors.append(f"mountain {name} helper lost its typed row input")

    return tuple(errors)


def _standalone_shader(source: str) -> str:
    return source.replace("#[compute]\n", "", 1)


def shader_compiles(source: str, label: str) -> tuple[bool, str]:
    compiler = shutil.which("glslangValidator")
    if compiler is None:
        return False, "glslangValidator is required"
    with tempfile.TemporaryDirectory(
        prefix=f"fl4431-elevation-{label}-"
    ) as directory:
        shader_path = Path(directory) / "product.comp"
        spirv_path = Path(directory) / "product.spv"
        shader_path.write_text(_standalone_shader(source), encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    compiler,
                    "--target-env",
                    "vulkan1.1",
                    "-S",
                    "comp",
                    "-o",
                    str(spirv_path),
                    str(shader_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"{label}: glslangValidator timed out"
        diagnostic = (result.stdout + result.stderr).strip()
        return result.returncode == 0, diagnostic


def gdscript_compiles(
    source: str,
    label: str,
    original_class_name: str,
) -> tuple[bool, str]:
    engine = shutil.which("godot")
    if engine is None:
        return False, "godot is required"
    class_name = "Fl4431ElevationMutant" + re.sub(
        r"[^A-Za-z0-9]", "", label.title()
    )
    parse_source = source.replace(
        f"class_name {original_class_name}",
        f"class_name {class_name}",
        1,
    )
    if parse_source == source:
        return False, (
            f"{label}: class_name {original_class_name} was not found"
        )
    with tempfile.TemporaryDirectory(
        prefix=f"fl4431-elevation-{label}-"
    ) as directory:
        script_path = Path(directory) / f"{label}.gd"
        log_path = Path(directory) / f"{label}.log"
        script_path.write_text(parse_source, encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    engine,
                    "--headless",
                    "--path",
                    str(PROJECT),
                    "--log-file",
                    str(log_path),
                    "--check-only",
                    "--script",
                    str(script_path),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"{label}: Godot parser timed out"
        diagnostic = (result.stdout + result.stderr).strip()
        return result.returncode == 0, diagnostic


def damaging_mutants(sources: Sources) -> tuple[Mutant, ...]:
    mutants: list[Mutant] = []

    def loader_mutant(name: str, old: str, new: str) -> None:
        mutants.append(
            Mutant(
                name,
                Sources(
                    replace_once(sources.loader, old, new, name),
                    sources.world,
                    sources.host,
                    sources.product,
                ),
                "loader",
            )
        )

    def host_mutant(name: str, old: str, new: str) -> None:
        mutants.append(
            Mutant(
                name,
                Sources(
                    sources.loader,
                    sources.world,
                    replace_once(sources.host, old, new, name),
                    sources.product,
                ),
                "host",
            )
        )

    def shader_mutant(name: str, old: str, new: str) -> None:
        mutants.append(
            Mutant(
                name,
                Sources(
                    sources.loader,
                    sources.world,
                    sources.host,
                    replace_once(sources.product, old, new, name),
                ),
                "shader",
            )
        )

    loader_mutant(
        "raw_flag_publisher_restored",
        "elevation_rows[y * w + x] = elevation_row",
        "elevation_rows[y * w + x] = raw_flags[y * w + x]",
    )
    loader_mutant(
        "border_clamp_drift",
        "var y_lo := maxi(y - 1, 0)",
        "var y_lo := maxi(y - 1, 1)",
    )
    loader_mutant(
        "east_border_clamp_drift",
        "var x_hi := mini(x + 1, w - 1)",
        "var x_hi := mini(x + 1, w - 2)",
    )
    loader_mutant(
        "e_lo_e_hi_swap",
        """var e_lo := (
				int(raw_flags[y_lo * w + x])
				+ int(raw_flags[y_lo * w + x_hi])
			)
			var e_hi := (
				int(raw_flags[y_hi * w + x])
				+ int(raw_flags[y_hi * w + x_hi])
			)""",
        """var e_lo := (
				int(raw_flags[y_hi * w + x])
				+ int(raw_flags[y_hi * w + x_hi])
			)
			var e_hi := (
				int(raw_flags[y_lo * w + x])
				+ int(raw_flags[y_lo * w + x_hi])
			)""",
    )
    loader_mutant(
        "output_mapping_drift",
        "elevation_row = 0 if e_hi <= 0 else 1",
        "elevation_row = 1 if e_hi <= 0 else 0",
    )

    accessor = glsl_function(sources.product, "q3_probe_payload_elevation_row")
    if not accessor:
        raise AssertionError("shipping Q3 elevation-row accessor is missing")
    row_owner = glsl_function(sources.product, "product_elevation_row_at")
    if not row_owner:
        raise AssertionError("shipping shared elevation-row owner is missing")
    six_fetch_accessor = """int q3_probe_payload_elevation_row(
	ivec2 cell,
	ProbePayloadFlags flags
) {
	if (!q3_probe_payload_elevation_valid(flags)) return 0;
	ivec2 dims = max(ivec2(params.terrain_params.zw), ivec2(1));
	cell = clamp(cell, ivec2(0), dims - ivec2(1));
	int e_mi =
		int(texelFetch(terrain_elev_tex, cell, 0).r)
		+ int(texelFetch(terrain_elev_tex,
			clamp(cell + ivec2(1, 0), ivec2(0), dims - ivec2(1)), 0).r);
	int e_lo =
		int(texelFetch(terrain_elev_tex,
			clamp(cell + ivec2(0, -1), ivec2(0), dims - ivec2(1)), 0).r)
		+ int(texelFetch(terrain_elev_tex,
			clamp(cell + ivec2(1, -1), ivec2(0), dims - ivec2(1)), 0).r);
	int e_hi =
		int(texelFetch(terrain_elev_tex,
			clamp(cell + ivec2(0, 1), ivec2(0), dims - ivec2(1)), 0).r)
		+ int(texelFetch(terrain_elev_tex,
			clamp(cell + ivec2(1, 1), ivec2(0), dims - ivec2(1)), 0).r);
	if (e_mi == 0) return 3;
	if (e_mi >= 2) return 1;
	if (e_lo <= 0) return e_hi <= 0 ? 3 : 2;
	return e_hi <= 0 ? 0 : 1;
}"""
    shader_mutant(
        "six_shader_fetches_restored",
        accessor,
        six_fetch_accessor,
    )
    shader_mutant(
        "second_texel_fetch_restored",
        "return int(texelFetch(terrain_elev_tex, cell, 0).r);",
        """int selected_row = int(texelFetch(terrain_elev_tex, cell, 0).r);
	return selected_row + int(texelFetch(
		terrain_elev_tex, cell, 0).r) * 0;""",
    )
    shader_mutant(
        "elevation_valid_gate_removed",
        "if (!q3_probe_payload_elevation_valid(flags)) return 0;",
        "if (false && !q3_probe_payload_elevation_valid(flags)) return 0;",
    )
    shader_mutant(
        "binding_changed",
        "layout(set = 0, binding = 15) uniform usampler2D terrain_elev_tex;",
        "layout(set = 0, binding = 23) uniform usampler2D terrain_elev_tex;",
    )
    shader_mutant(
        "host_zero_terrain_dims_restored",
        """ivec2 dims = ivec2(
		max(params.misc.y, 1),
		max(params.misc.z, 1));""",
        """ivec2 dims =
		max(ivec2(params.terrain_params.zw), ivec2(1));""",
    )
    inclusive_clamp_owner = replace_once(
        row_owner,
        "cell = clamp(cell, ivec2(0), dims - ivec2(1));",
        "cell = clamp(cell, ivec2(0), dims);",
        "inclusive_upper_clamp_restored",
    )
    shader_mutant(
        "inclusive_upper_clamp_restored",
        row_owner,
        inclusive_clamp_owner,
    )

    mountain = glsl_function(
        sources.product, "product_mountain_blank_glyph"
    )
    if not mountain:
        raise AssertionError("shipping mountain blank-glyph helper is missing")
    mutated_mountain = replace_once(
        mountain,
        "int family = int(material_family);",
        """elevation_row = product_elevation_row_at(terr_cell);
	int family = int(material_family);""",
        "mountain_second_fetch_restored",
    )
    shader_mutant(
        "mountain_second_fetch_restored",
        mountain,
        mutated_mountain,
    )

    host_mutant(
        "b15_material_fallback_restored",
        "var elev_bind: RID = terrain_elev_rd",
        """var elev_bind: RID = (
		terrain_elev_rd if terrain_elev_rd.is_valid() else mat_bind
	)""",
    )
    host_mutant(
        "invalid_b15_dispatch_admitted",
        "if not terrain_elev_rd.is_valid():",
        "if false and not terrain_elev_rd.is_valid():",
    )

    moved_world = replace_once(
        sources.world,
        "\t_install_authoritative_elevation_rows(ascii_effect)\n",
        "",
        "world_elevation_install_moved_under_firefly",
    )
    moved_world = replace_once(
        moved_world,
        '\tprint("FL-4204 firefly_grass_anchors=", anchors.size())',
        """	print("FL-4204 firefly_grass_anchors=", anchors.size())
	_install_authoritative_elevation_rows(effect)""",
        "world_elevation_install_moved_under_firefly",
    )
    mutants.append(
        Mutant(
            "world_elevation_install_moved_under_firefly",
            Sources(
                sources.loader,
                moved_world,
                sources.host,
                sources.product,
            ),
            "world",
        )
    )
    return tuple(mutants)


def read_sources() -> Sources:
    return Sources(
        LOADER.read_text(encoding="utf-8"),
        WORLD.read_text(encoding="utf-8"),
        HOST.read_text(encoding="utf-8"),
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
    )


def run_contract() -> tuple[int, int, int, int, int]:
    sources = read_sources()
    shipping_errors = contract_errors(sources)
    assert shipping_errors == (), "\n".join(shipping_errors)
    compiled, diagnostic = shader_compiles(sources.product, "shipping")
    assert compiled, f"shipping Product shader rejected:\n{diagnostic}"

    mutants = damaging_mutants(sources)
    assert len(mutants) == 15
    escaped: list[str] = []
    loader_mutants = 0
    host_mutants = 0
    world_mutants = 0
    shader_mutants = 0
    for mutant in mutants:
        if mutant.language == "loader":
            loader_mutants += 1
            compiled, diagnostic = gdscript_compiles(
                mutant.sources.loader, mutant.name, "A3DMapLoader"
            )
        elif mutant.language == "host":
            host_mutants += 1
            compiled, diagnostic = gdscript_compiles(
                mutant.sources.host, mutant.name, "ASCIICompositorEffect"
            )
        elif mutant.language == "world":
            world_mutants += 1
            compiled, diagnostic = gdscript_compiles(
                mutant.sources.world, mutant.name, "World"
            )
        else:
            shader_mutants += 1
            compiled, diagnostic = shader_compiles(
                mutant.sources.product, mutant.name
            )
        assert compiled, (
            f"{mutant.name}: destructive mutant was not compile-valid\n"
            f"{diagnostic}"
        )
        if contract_errors(mutant.sources) == ():
            escaped.append(mutant.name)
    assert escaped == [], f"compile-valid destructive mutants escaped: {escaped}"
    return (
        len(mutants),
        loader_mutants,
        host_mutants,
        world_mutants,
        shader_mutants,
    )


def test_elevation_row_single_fetch_contract() -> None:
    run_contract()


def main() -> int:
    try:
        (
            mutant_count,
            loader_mutants,
            host_mutants,
            world_mutants,
            shader_mutants,
        ) = run_contract()
    except (
        AssertionError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"FL4431_ELEVATION_ROW_SINGLE_FETCH_CONTRACT_FAIL {error}")
        return 1
    print(
        "FL4431_ELEVATION_ROW_SINGLE_FETCH_CONTRACT_PASS "
        f"mutants={mutant_count} loader_mutants={loader_mutants} "
        f"host_mutants={host_mutants} world_mutants={world_mutants} "
        f"shader_mutants={shader_mutants} product_fetches=1 binding=15"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
