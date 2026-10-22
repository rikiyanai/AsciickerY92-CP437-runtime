"""CLI entry point — Click-based commands + REPL mode."""

import json
import os
import shlex
import sys

import click

from cli_anything.blender.core import (
    addons,
    animation,
    bridge,
    io,
    materials,
    modifiers,
    objects,
    osm,
    project,
    render,
    scene,
    state,
)
from cli_anything.blender.utils.formatting import output


# ── Helpers ──────────────────────────────────────────────────────────

def _blend_file(ctx):
    """Resolve blend file from --file flag or session state."""
    f = ctx.obj.get("blend_file") or state.get_blend_file()
    if not f:
        click.echo("No blend file set. Use --file or `open <path>`.", err=True)
        ctx.exit(1)
    return f


def _json(ctx):
    return ctx.obj.get("json", False)


def _blender(ctx):
    return ctx.obj.get("blender_path")


# ── Root group ───────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("--file", "-f", "blend_file", help="Path to .blend file")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON for agent consumption")
@click.option("--blender-path", envvar="BLENDER_PATH", help="Path to Blender executable")
@click.option("--repl", is_flag=True, help="Enter interactive REPL mode")
@click.pass_context
def cli(ctx, blend_file, json_mode, blender_path, repl):
    """CLI-Anything Blender — drive Blender headlessly from the command line."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
    ctx.obj["blender_path"] = blender_path
    if blend_file:
        ctx.obj["blend_file"] = os.path.abspath(blend_file)
        state.set_blend_file(blend_file)

    if ctx.invoked_subcommand is None:
        if repl:
            _repl(ctx)
        else:
            click.echo(ctx.get_help())


# ── Project commands ─────────────────────────────────────────────────

@cli.command()
@click.argument("path")
@click.pass_context
def open(ctx, path):
    """Open a .blend file and show summary."""
    path = os.path.abspath(path)
    state.set_blend_file(path)
    ctx.obj["blend_file"] = path
    result = project.open_file(path, blender_path=_blender(ctx))
    output(result, _json(ctx))


@cli.command()
@click.argument("path")
@click.pass_context
def info(ctx, path):
    """Show detailed info about a blend file."""
    result = project.info(path, blender_path=_blender(ctx))
    output(result, _json(ctx))


@cli.command("new")
@click.option("--output", "-o", "output_path", help="Save new file to path")
@click.pass_context
def new_file(ctx, output_path=None):
    """Create a new empty blend file."""
    result = project.new(output_path=output_path, blender_path=_blender(ctx))
    if output_path:
        state.set_blend_file(output_path)
    output(result, _json(ctx))


@cli.command("save-as")
@click.argument("output_path")
@click.pass_context
def save_as(ctx, output_path):
    """Save current blend file to a new path."""
    result = project.save_as(_blend_file(ctx), output_path, blender_path=_blender(ctx))
    output(result, _json(ctx))


@cli.command()
@click.pass_context
def version(ctx):
    """Show Blender version."""
    b = bridge.BlenderBridge(blender_path=_blender(ctx))
    v = b.version()
    if _json(ctx):
        output({"ok": True, "data": {"version": v}}, True)
    else:
        click.echo(v)


# ── Object commands ──────────────────────────────────────────────────

@cli.group("obj")
@click.pass_context
def obj_group(ctx):
    """Object operations (list, add, delete, transform, duplicate)."""
    pass


@obj_group.command("list")
@click.option("--type", "type_filter", help="Filter by type (MESH, CAMERA, LIGHT, etc.)")
@click.pass_context
def obj_list(ctx, type_filter):
    """List all objects."""
    result = objects.list_objects(_blend_file(ctx), blender_path=_blender(ctx),
                                  type_filter=type_filter)
    output(result, _json(ctx))


@obj_group.command("add")
@click.argument("obj_type")
@click.option("--name", "-n", help="Name for the new object")
@click.option("--location", "-l", nargs=3, type=float, help="X Y Z location")
@click.pass_context
def obj_add(ctx, obj_type, name, location):
    """Add a primitive (cube, sphere, cylinder, plane, cone, monkey, camera, light_*)."""
    loc = list(location) if location else None
    result = objects.add_object(_blend_file(ctx), obj_type, name=name, location=loc,
                                blender_path=_blender(ctx))
    output(result, _json(ctx))


@obj_group.command("delete")
@click.argument("name")
@click.pass_context
def obj_delete(ctx, name):
    """Delete an object by name."""
    result = objects.delete_object(_blend_file(ctx), name, blender_path=_blender(ctx))
    output(result, _json(ctx))


@obj_group.command("transform")
@click.argument("name")
@click.option("--location", "-l", nargs=3, type=float, help="X Y Z")
@click.option("--rotation", "-r", nargs=3, type=float, help="X Y Z (radians)")
@click.option("--scale", "-s", nargs=3, type=float, help="X Y Z")
@click.pass_context
def obj_transform(ctx, name, location, rotation, scale):
    """Set transform (location/rotation/scale) on an object."""
    result = objects.transform(
        _blend_file(ctx), name,
        location=list(location) if location else None,
        rotation=list(rotation) if rotation else None,
        scale=list(scale) if scale else None,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@obj_group.command("duplicate")
@click.argument("name")
@click.option("--new-name", help="Name for duplicate")
@click.option("--offset", nargs=3, type=float, help="X Y Z offset")
@click.pass_context
def obj_duplicate(ctx, name, new_name, offset):
    """Duplicate an object."""
    result = objects.duplicate(
        _blend_file(ctx), name,
        new_name=new_name,
        offset=list(offset) if offset else None,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── Render commands ──────────────────────────────────────────────────

@cli.group("render")
@click.pass_context
def render_cmd(ctx):
    """Rendering operations."""
    pass


@render_cmd.command("image")
@click.argument("output_file")
@click.option("--engine", "-e", help="Render engine (CYCLES, BLENDER_EEVEE_NEXT)")
@click.option("--resolution", "-r", nargs=2, type=int, help="Width Height")
@click.option("--samples", "-s", type=int, help="Render samples")
@click.option("--format", "-F", "fmt", help="Image format (PNG, JPEG, TIFF, etc.)")
@click.option("--camera", help="Camera name to render from")
@click.option("--transparent", is_flag=True, help="Transparent background")
@click.pass_context
def render_image(ctx, output_file, engine, resolution, samples, fmt, camera, transparent):
    """Render a single frame to an image file."""
    result = render.render_image(
        _blend_file(ctx), output_file,
        engine=engine,
        resolution=tuple(resolution) if resolution else None,
        samples=samples,
        format=fmt,
        camera=camera,
        transparent=transparent,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@render_cmd.command("animation")
@click.argument("output_dir")
@click.option("--start", type=int, help="Start frame")
@click.option("--end", type=int, help="End frame")
@click.option("--engine", "-e", help="Render engine")
@click.option("--resolution", "-r", nargs=2, type=int, help="Width Height")
@click.option("--format", "-F", "fmt", help="Image format")
@click.pass_context
def render_anim(ctx, output_dir, start, end, engine, resolution, fmt):
    """Render animation frames."""
    result = render.render_animation(
        _blend_file(ctx), output_dir,
        frame_start=start, frame_end=end,
        engine=engine,
        resolution=tuple(resolution) if resolution else None,
        format=fmt,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@render_cmd.command("engines")
@click.pass_context
def render_engines(ctx):
    """List available render engines."""
    result = render.list_engines(blender_path=_blender(ctx))
    output(result, _json(ctx))


# ── Material commands ────────────────────────────────────────────────

@cli.group("mat")
@click.pass_context
def mat_group(ctx):
    """Material operations."""
    pass


@mat_group.command("list")
@click.pass_context
def mat_list(ctx):
    """List all materials."""
    result = materials.list_materials(_blend_file(ctx), blender_path=_blender(ctx))
    output(result, _json(ctx))


@mat_group.command("create")
@click.argument("name")
@click.option("--color", nargs=3, type=float, help="R G B (0.0-1.0)")
@click.option("--metallic", type=float, help="Metallic value (0.0-1.0)")
@click.option("--roughness", type=float, help="Roughness value (0.0-1.0)")
@click.pass_context
def mat_create(ctx, name, color, metallic, roughness):
    """Create a new Principled BSDF material."""
    result = materials.create_material(
        _blend_file(ctx), name,
        color=list(color) if color else None,
        metallic=metallic,
        roughness=roughness,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@mat_group.command("assign")
@click.argument("object_name")
@click.argument("material_name")
@click.pass_context
def mat_assign(ctx, object_name, material_name):
    """Assign a material to an object."""
    result = materials.assign_material(
        _blend_file(ctx), object_name, material_name,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── Import/Export commands ───────────────────────────────────────────

@cli.group("io")
@click.pass_context
def io_group(ctx):
    """Import/Export operations."""
    pass


@io_group.command("import")
@click.argument("path")
@click.option("--save", "-o", "output_blend", help="Save result to .blend file")
@click.pass_context
def io_import(ctx, path, output_blend):
    """Import a 3D file (fbx, gltf, glb, obj, stl, ply, svg, bvh)."""
    bf = None
    try:
        bf = _blend_file(ctx)
    except SystemExit:
        bf = None
    result = io.import_file(path, blend_file=bf, output_blend=output_blend,
                            blender_path=_blender(ctx))
    output(result, _json(ctx))


@io_group.command("export")
@click.argument("output_path")
@click.option("--format", "-F", "fmt", help="Override export format")
@click.pass_context
def io_export(ctx, output_path, fmt):
    """Export blend file to another format (fbx, gltf, glb, obj, stl, ply)."""
    result = io.export_file(_blend_file(ctx), output_path, format=fmt,
                            blender_path=_blender(ctx))
    output(result, _json(ctx))


@io_group.command("convert")
@click.argument("input_path")
@click.argument("output_path")
@click.pass_context
def io_convert(ctx, input_path, output_path):
    """Convert between 3D formats (e.g. fbx -> glb)."""
    result = io.convert(input_path, output_path, blender_path=_blender(ctx))
    output(result, _json(ctx))


@io_group.command("formats")
@click.pass_context
def io_formats(ctx):
    """List supported import/export formats."""
    result = io.list_formats()
    output(result, _json(ctx))


# ── Addon management commands ────────────────────────────────────────

@cli.group("addon")
@click.pass_context
def addon_group(ctx):
    """Blender addon management."""
    pass


@addon_group.command("enable")
@click.argument("module_name")
@click.pass_context
def addon_enable(ctx, module_name):
    """Enable a Blender addon by module name."""
    result = addons.enable_addon(
        module_name, blend_file=ctx.obj.get("blend_file"),
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@addon_group.command("disable")
@click.argument("module_name")
@click.pass_context
def addon_disable(ctx, module_name):
    """Disable a Blender addon by module name."""
    result = addons.disable_addon(
        module_name, blend_file=ctx.obj.get("blend_file"),
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@addon_group.command("list")
@click.option("--state", "-s", "addon_state", type=click.Choice(["ENABLED", "DISABLED"]),
              help="Filter by addon state.")
@click.pass_context
def addon_list(ctx, addon_state):
    """List Blender addons."""
    result = addons.list_addons(
        addon_state=addon_state, blend_file=ctx.obj.get("blend_file"),
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@addon_group.command("status")
@click.argument("module_name")
@click.pass_context
def addon_status_cmd(ctx, module_name):
    """Query the status of a specific addon."""
    result = addons.addon_status(
        module_name, blend_file=ctx.obj.get("blend_file"),
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── OSM terrain painter commands ─────────────────────────────────────

@cli.group("osm")
@click.pass_context
def osm_group(ctx):
    """OSM terrain painter operations (requires io_asciicker addon)."""
    pass


@osm_group.command("import-blosm")
@click.option("--min-lat", type=float, required=True, help="Minimum latitude.")
@click.option("--max-lat", type=float, required=True, help="Maximum latitude.")
@click.option("--min-lon", type=float, required=True, help="Minimum longitude.")
@click.option("--max-lon", type=float, required=True, help="Maximum longitude.")
@click.option("--mode", type=click.Choice(["2D", "3Dsimple", "3Drealistic"]), default="2D",
              help="blosm import mode (default: 2D flat footprints).")
@click.option("--no-buildings", is_flag=True, help="Skip building import.")
@click.option("--no-save", is_flag=True, help="Don't save .blend after import.")
@click.pass_context
def osm_import_blosm(ctx, min_lat, max_lat, min_lon, max_lon, mode, no_buildings, no_save):
    """Import OpenStreetMap data via blosm addon (downloads from server)."""
    result = osm.import_blosm(
        _blend_file(ctx), min_lat=min_lat, max_lat=max_lat,
        min_lon=min_lon, max_lon=max_lon, mode=mode,
        buildings=not no_buildings, save=not no_save,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("create-terrain")
@click.option("--size", type=float, default=64.0, help="Terrain size (snaps to 8-unit grid, min 8).")
@click.option("--subdivisions", type=int, default=1, help="Subdivisions per unit (1-4).")
@click.option("--no-save", is_flag=True, help="Don't save .blend after creation.")
@click.pass_context
def osm_create_terrain(ctx, size, subdivisions, no_save):
    """Create terrain plane with vertex colors for material painting."""
    result = osm.create_terrain(
        _blend_file(ctx), size=size, subdivisions=subdivisions,
        save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("paint-terrain-direct")
@click.option("--no-save", is_flag=True, help="Don't save .blend after painting.")
@click.pass_context
def osm_paint_terrain_direct(ctx, no_save):
    """Paint terrain vertex colors directly from OSM data (no asciiid needed)."""
    result = osm.paint_terrain_direct(
        _blend_file(ctx), save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("auto-terrain")
@click.option("--no-save", is_flag=True, help="Don't save .blend after creation.")
@click.pass_context
def osm_auto_terrain(ctx, no_save):
    """Auto-create terrain sized to cover all blosm objects."""
    result = osm.auto_terrain(
        _blend_file(ctx), save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("scan-scene")
@click.pass_context
def osm_scan_scene(ctx):
    """Scan blend file for blosm OSM objects and categorize them."""
    result = osm.scan_osm_scene(
        _blend_file(ctx), blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("paint-terrain")
@click.option("--scale", type=float, default=1.0, help="blosm-to-engine scale factor.")
@click.option("--offset-x", type=float, default=0.0, help="Calibration offset X.")
@click.option("--offset-y", type=float, default=0.0, help="Calibration offset Y.")
@click.option("--grass-mat", type=int, default=1, help="Material ID for grass (0-255).")
@click.option("--road-mat", type=int, default=4, help="Material ID for roads (0-255).")
@click.option("--concrete-mat", type=int, default=5, help="Material ID for concrete (0-255).")
@click.option("--residential-mat", type=int, default=3, help="Material ID for residential (0-255).")
@click.option("--default-mat", type=int, default=2, help="Material ID for default (0-255).")
@click.option("--simplify-tolerance", type=float, default=1.0, help="Douglas-Peucker epsilon.")
@click.option("--cmd-file", default="/tmp/asciiid_cmd", help="Path to MCP relay command file.")
@click.pass_context
def osm_paint_terrain(ctx, scale, offset_x, offset_y, grass_mat, road_mat,
                      concrete_mat, residential_mat, default_mat,
                      simplify_tolerance, cmd_file):
    """Paint terrain materials from OSM data."""
    result = osm.paint_terrain_from_osm(
        _blend_file(ctx),
        scale=scale, offset_x=offset_x, offset_y=offset_y,
        grass_mat=grass_mat, road_mat=road_mat,
        concrete_mat=concrete_mat, residential_mat=residential_mat,
        default_mat=default_mat, simplify_tolerance=simplify_tolerance,
        cmd_file=cmd_file, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("calibrate")
@click.pass_context
def osm_calibrate(ctx):
    """Auto-calibrate OSM-to-engine coordinate mapping."""
    result = osm.auto_calibrate_osm(
        _blend_file(ctx), blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("extrude-buildings")
@click.option("--no-save", is_flag=True, help="Don't save .blend after extrusion.")
@click.pass_context
def osm_extrude_buildings(ctx, no_save):
    """Extrude flat blosm building footprints to 3D."""
    result = osm.extrude_buildings(
        _blend_file(ctx), save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("paint-buildings")
@click.option("--subdivision", type=int, default=3, help="Subdivision level (1-4).")
@click.option("--no-save", is_flag=True, help="Don't save .blend after painting.")
@click.pass_context
def osm_paint_buildings(ctx, subdivision, no_save):
    """Subdivide and paint windows on all blosm buildings."""
    result = osm.paint_buildings(
        _blend_file(ctx), subdivision_level=subdivision,
        save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("separate-buildings")
@click.option("--no-save", is_flag=True, help="Don't save .blend after separation.")
@click.pass_context
def osm_separate_buildings(ctx, no_save):
    """Separate merged blosm buildings into individual objects by loose parts."""
    result = osm.separate_buildings(
        _blend_file(ctx), save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("prepare-meshes")
@click.option("--meshes-dir", required=True, help="Path to engine meshes/ directory.")
@click.option("--target-faces", type=int, default=500, help="Max faces per mesh (default: 500, hard cap: 4950).")
@click.option("--no-save", is_flag=True, help="Don't save .blend after preparation.")
@click.pass_context
def osm_prepare_meshes(ctx, meshes_dir, target_faces, no_save):
    """Inventory, reduce, and export new meshes as AKM files."""
    result = osm.prepare_meshes(
        _blend_file(ctx), meshes_dir=meshes_dir, target_faces=target_faces,
        save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("clean-scene")
@click.option("--meshes-dir", default=None, help="Path to engine meshes/ dir (preserves objects with existing AKMs).")
@click.option("--no-save", is_flag=True, help="Don't save .blend after cleaning.")
@click.pass_context
def osm_clean_scene(ctx, meshes_dir, no_save):
    """Delete non-building blosm objects (roads, vegetation, empties)."""
    result = osm.clean_scene(
        _blend_file(ctx), meshes_dir=meshes_dir, save=not no_save,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("export-a3d")
@click.argument("output_path")
@click.pass_context
def osm_export_a3d(ctx, output_path):
    """Export scene as A3D map file."""
    result = osm.export_a3d(
        _blend_file(ctx), output_path=output_path, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@osm_group.command("full-pipeline")
@click.option("--meshes-dir", required=True, help="Path to engine meshes/ directory.")
@click.option("--a3d-output", required=True, help="Output path for .a3d map file.")
@click.option("--target-faces", type=int, default=500, help="Max faces per mesh (default: 500, hard cap: 4950).")
@click.option("--subdivision", type=int, default=3, help="Building subdivision level (1-4).")
@click.option("--no-save", is_flag=True, help="Don't save .blend after pipeline.")
@click.pass_context
def osm_full_pipeline(ctx, meshes_dir, a3d_output, target_faces, subdivision,
                      no_save):
    """Run complete OSM-to-engine pipeline: extrude, paint, prepare, clean, export."""
    result = osm.full_pipeline(
        _blend_file(ctx), meshes_dir=meshes_dir, a3d_output=a3d_output,
        target_faces=target_faces, subdivision_level=subdivision,
        save=not no_save, blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── Animation commands ───────────────────────────────────────────────

@cli.group("anim")
@click.pass_context
def anim_group(ctx):
    """Animation operations."""
    pass


@anim_group.command("timeline")
@click.pass_context
def anim_timeline(ctx):
    """Show timeline information."""
    result = animation.get_timeline(_blend_file(ctx), blender_path=_blender(ctx))
    output(result, _json(ctx))


@anim_group.command("keyframes")
@click.argument("object_name")
@click.pass_context
def anim_keyframes(ctx, object_name):
    """List keyframes for an object."""
    result = animation.list_keyframes(_blend_file(ctx), object_name,
                                      blender_path=_blender(ctx))
    output(result, _json(ctx))


@anim_group.command("set-range")
@click.argument("start", type=int)
@click.argument("end", type=int)
@click.pass_context
def anim_set_range(ctx, start, end):
    """Set animation frame range."""
    result = animation.set_frame_range(_blend_file(ctx), start, end,
                                       blender_path=_blender(ctx))
    output(result, _json(ctx))


@anim_group.command("insert-keyframe")
@click.argument("object_name")
@click.argument("frame", type=int)
@click.option("--property", "-p", "data_path", default="location", help="Property path")
@click.pass_context
def anim_insert_kf(ctx, object_name, frame, data_path):
    """Insert a keyframe on an object."""
    result = animation.insert_keyframe(
        _blend_file(ctx), object_name, frame,
        data_path=data_path,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── Modifier commands ────────────────────────────────────────────────

@cli.group("mod")
@click.pass_context
def mod_group(ctx):
    """Modifier operations."""
    pass


@mod_group.command("list")
@click.argument("object_name")
@click.pass_context
def mod_list(ctx, object_name):
    """List modifiers on an object."""
    result = modifiers.list_modifiers(_blend_file(ctx), object_name,
                                      blender_path=_blender(ctx))
    output(result, _json(ctx))


@mod_group.command("add")
@click.argument("object_name")
@click.argument("mod_type")
@click.option("--name", "-n", help="Modifier name")
@click.pass_context
def mod_add(ctx, object_name, mod_type, name):
    """Add a modifier (SUBSURF, MIRROR, ARRAY, SOLIDIFY, BEVEL, etc.)."""
    result = modifiers.add_modifier(
        _blend_file(ctx), object_name, mod_type, name=name,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@mod_group.command("remove")
@click.argument("object_name")
@click.argument("modifier_name")
@click.pass_context
def mod_remove(ctx, object_name, modifier_name):
    """Remove a modifier."""
    result = modifiers.remove_modifier(
        _blend_file(ctx), object_name, modifier_name,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


@mod_group.command("apply")
@click.argument("object_name")
@click.argument("modifier_name")
@click.pass_context
def mod_apply(ctx, object_name, modifier_name):
    """Apply (bake) a modifier."""
    result = modifiers.apply_modifier(
        _blend_file(ctx), object_name, modifier_name,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── Scene commands ───────────────────────────────────────────────────

@cli.group("scene")
@click.pass_context
def scene_cmd(ctx):
    """Scene operations."""
    pass


@scene_cmd.command("list")
@click.pass_context
def scene_list(ctx):
    """List all scenes."""
    result = scene.list_scenes(_blend_file(ctx), blender_path=_blender(ctx))
    output(result, _json(ctx))


@scene_cmd.command("info")
@click.option("--name", "-n", help="Scene name (default: active)")
@click.pass_context
def scene_info(ctx, name):
    """Show scene details."""
    result = scene.get_scene(_blend_file(ctx), scene_name=name,
                             blender_path=_blender(ctx))
    output(result, _json(ctx))


@scene_cmd.command("settings")
@click.option("--engine", "-e", help="Render engine")
@click.option("--resolution", "-r", nargs=2, type=int, help="Width Height")
@click.option("--fps", type=int, help="Frames per second")
@click.option("--format", "-F", "fmt", help="Output format")
@click.option("--samples", "-s", type=int, help="Render samples")
@click.pass_context
def scene_settings(ctx, engine, resolution, fps, fmt, samples):
    """Update render settings."""
    result = scene.set_render_settings(
        _blend_file(ctx),
        engine=engine,
        resolution=tuple(resolution) if resolution else None,
        fps=fps,
        format=fmt,
        samples=samples,
        blender_path=_blender(ctx),
    )
    output(result, _json(ctx))


# ── Execute raw Python ──────────────────────────────────────────────

@cli.command("exec")
@click.argument("code")
@click.pass_context
def exec_python(ctx, code):
    """Execute raw Python code in Blender. Code must set _data."""
    bf = None
    try:
        bf = _blend_file(ctx)
    except SystemExit:
        pass
    b = bridge.BlenderBridge(blend_file=bf, blender_path=_blender(ctx))
    result = b.execute(code)
    output(result, _json(ctx))


# ── REPL ─────────────────────────────────────────────────────────────

def _repl(ctx):
    """Interactive REPL mode."""
    click.echo("CLI-Anything Blender REPL (type 'help' or 'quit')")
    bf = ctx.obj.get("blend_file") or state.get_blend_file()
    if bf:
        click.echo(f"  blend file: {bf}")

    while True:
        try:
            line = input("blender> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nBye!")
            break

        if not line:
            continue
        if line in ("quit", "exit", "q"):
            break
        if line == "help":
            click.echo(ctx.get_help())
            continue

        try:
            args = shlex.split(line)
        except ValueError as e:
            click.echo(f"Parse error: {e}")
            continue

        try:
            with cli.make_context("blender", args, parent=ctx) as sub_ctx:
                cli.invoke(sub_ctx)
        except click.exceptions.UsageError as e:
            click.echo(f"Usage error: {e}")
        except SystemExit:
            pass


# ── Entry point ──────────────────────────────────────────────────────

def main():
    cli(obj={})


if __name__ == "__main__":
    main()
