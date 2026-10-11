"""cli-anything-asciiid: CLI harness for the Asciicker asciiid map editor.

Provides both one-shot subcommands and an interactive REPL for controlling
the asciiid editor via its MCP stdin protocol.
"""

import json
import os
import shlex
import sys

import click

from cli_anything.asciiid.core import (
    assets as assets_mod,
    camera as cam_mod,
    editor as editor_mod,
    instances as inst_mod,
    minimap as minimap_mod,
    placement as place_mod,
    terrain as terrain_mod,
    weather as weather_mod,
    world as world_mod,
)
from cli_anything.asciiid.utils.repl_skin import ReplSkin


def _output(data, as_json: bool):
    """Print data as JSON or human-readable."""
    if as_json:
        # Strip internal 'response' lists from JSON output
        if isinstance(data, dict):
            clean = {k: v for k, v in data.items() if k != "response"}
        else:
            clean = data
        click.echo(json.dumps(clean, indent=2, default=str))
    elif isinstance(data, dict):
        for k, v in data.items():
            if k == "response":
                continue
            click.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                click.echo("  " + "  ".join(f"{k}={v}" for k, v in item.items() if k != "response"))
            else:
                click.echo(f"  {item}")
    else:
        click.echo(str(data))


def handle_error(e: Exception, as_json: bool):
    """Unified error handler."""
    if as_json:
        click.echo(json.dumps({"error": str(e)}), err=True)
    else:
        click.echo(f"Error: {e}", err=True)


# ── Main CLI group ───────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--project-root", envvar="ASCIIID_PROJECT_ROOT",
              default=None, help="Asciicker project root directory")
@click.pass_context
def cli(ctx, as_json, project_root):
    """CLI harness for the Asciicker asciiid map editor."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json
    ctx.obj["project_root"] = project_root or _find_project_root()

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


def _find_project_root() -> str:
    """Auto-detect project root by looking for .run/asciiid."""
    # Check common locations
    candidates = [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
    ]

    # Walk up from candidates
    for start in candidates:
        path = start
        for _ in range(10):
            if os.path.isfile(os.path.join(path, ".run", "asciiid")):
                return path
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent

    return os.getcwd()


# ── Editor commands ──────────────────────────────────────────────────

@cli.group()
def editor():
    """Editor process lifecycle (start/stop/status)."""
    pass


@editor.command("start")
@click.option("--binary", default=None, help="Path to asciiid binary")
@click.option("--timeout", default=15.0, help="Startup timeout in seconds")
@click.pass_context
def editor_start(ctx, binary, timeout):
    """Launch asciiid in MCP mode."""
    try:
        result = editor_mod.start(
            project_root=ctx.obj["project_root"],
            binary_path=binary,
            timeout=timeout,
        )
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@editor.command("stop")
@click.pass_context
def editor_stop(ctx):
    """Stop the running asciiid process."""
    try:
        result = editor_mod.stop()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@editor.command("status")
@click.pass_context
def editor_status(ctx):
    """Check editor process status."""
    try:
        result = editor_mod.status()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Project commands ─────────────────────────────────────────────────

@cli.group()
def project():
    """World file operations (load/save)."""
    pass


@project.command("load")
@click.argument("path", default="")
@click.pass_context
def project_load(ctx, path):
    """Load an .a3d world file (default: game_map_y8.a3d)."""
    try:
        result = world_mod.load_map(path)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@project.command("save")
@click.argument("path")
@click.pass_context
def project_save(ctx, path):
    """Save the current world to an .a3d file."""
    try:
        result = world_mod.save_map(path)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@project.command("info")
@click.pass_context
def project_info(ctx):
    """Show world info (instance count, camera, weather)."""
    try:
        instances = world_mod.list_instances()
        camera = cam_mod.get()
        weather = weather_mod.get()
        info = {
            "instance_count": len(instances),
            "camera": {k: v for k, v in camera.items() if k != "response"},
            "weather": weather.get("name", "unknown"),
        }
        _output(info, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Terrain commands ─────────────────────────────────────────────────

@cli.group()
def terrain():
    """Terrain editing (height, probe, grid)."""
    pass


@terrain.command("set-height")
@click.argument("height", type=int)
@click.pass_context
def terrain_set_height(ctx, height):
    """Set terrain height for all patches (0-65535)."""
    try:
        result = terrain_mod.set_height(height)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@terrain.command("probe")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.pass_context
def terrain_probe(ctx, x, y):
    """Query terrain height at position (X, Y)."""
    try:
        result = terrain_mod.probe(x, y)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@terrain.command("grid")
@click.argument("alpha", type=float)
@click.pass_context
def terrain_grid(ctx, alpha):
    """Set grid visibility (0.0-1.0)."""
    try:
        result = terrain_mod.set_grid(alpha)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@terrain.command("paint-poly")
@click.option("--mat-id", required=True, type=int, help="Material ID (0-255).")
@click.argument("vertices", nargs=-1, type=float, required=True)
@click.pass_context
def terrain_paint_poly(ctx, mat_id, vertices):
    """Paint terrain material in a polygon.

    VERTICES are flat pairs of floats: x1 y1 x2 y2 x3 y3 ...
    Requires 3-32 vertex pairs (6-64 float values).
    """
    try:
        if len(vertices) % 2 != 0:
            raise ValueError(f"Vertices must be pairs of floats (got {len(vertices)} values, which is odd)")
        pairs = [(vertices[i], vertices[i + 1]) for i in range(0, len(vertices), 2)]
        result = terrain_mod.paint_terrain_poly(mat_id, pairs)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Placement commands ───────────────────────────────────────────────

@cli.group()
def place():
    """Place meshes and sprites in the world."""
    pass


@place.command("mesh")
@click.argument("name")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.argument("z", type=float)
@click.option("--scale", type=float, default=1.0, help="Uniform scale")
@click.pass_context
def place_mesh(ctx, name, x, y, z, scale):
    """Place a mesh instance (e.g., Tree.akm)."""
    try:
        result = place_mod.place_mesh(name, x, y, z, scale)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@place.command("sprite")
@click.argument("name")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.argument("z", type=float)
@click.option("--yaw", type=float, default=0.0, help="Rotation degrees")
@click.option("--anim", type=int, default=0, help="Animation index")
@click.option("--frame", type=int, default=0, help="Frame index")
@click.pass_context
def place_sprite(ctx, name, x, y, z, yaw, anim, frame):
    """Place a sprite instance (e.g., player-0100.xp)."""
    try:
        result = place_mod.place_sprite(name, x, y, z, yaw, anim, frame)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@place.command("sprite-active")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.argument("z", type=float)
@click.option("--yaw", type=float, default=0.0)
@click.option("--anim", type=int, default=0)
@click.option("--frame", type=int, default=0)
@click.pass_context
def place_sprite_active(ctx, x, y, z, yaw, anim, frame):
    """Place the active sprite at absolute coordinates."""
    try:
        result = place_mod.place_sprite_active(x, y, z, yaw, anim, frame)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@place.command("sprite-active-rel")
@click.argument("dx", type=float)
@click.argument("dy", type=float)
@click.argument("dz", type=float)
@click.option("--yaw", type=float, default=0.0)
@click.option("--anim", type=int, default=0)
@click.option("--frame", type=int, default=0)
@click.pass_context
def place_sprite_active_rel(ctx, dx, dy, dz, yaw, anim, frame):
    """Place the active sprite relative to camera position."""
    try:
        result = place_mod.place_sprite_active_rel(dx, dy, dz, yaw, anim, frame)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@place.command("set-active")
@click.argument("name")
@click.pass_context
def place_set_active(ctx, name):
    """Set the active sprite for subsequent placements."""
    try:
        result = place_mod.set_active_sprite(name)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@place.command("load-sprite")
@click.argument("path")
@click.pass_context
def place_load_sprite(ctx, path):
    """Load a sprite from a file path."""
    try:
        result = place_mod.load_sprite(path)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Camera commands ──────────────────────────────────────────────────

@cli.group()
def camera():
    """Camera position and orientation."""
    pass


@camera.command("get")
@click.pass_context
def camera_get(ctx):
    """Get current camera state."""
    try:
        result = cam_mod.get()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@camera.command("set")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.argument("z", type=float)
@click.argument("yaw", type=float)
@click.option("--pitch", type=float, default=60.0, help="Vertical angle (30-90)")
@click.pass_context
def camera_set(ctx, x, y, z, yaw, pitch):
    """Set camera position and orientation."""
    try:
        result = cam_mod.set(x, y, z, yaw, pitch)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@camera.command("focus-origin")
@click.pass_context
def camera_focus_origin(ctx):
    """Jump camera to origin (0, 0, 0)."""
    try:
        result = cam_mod.focus_origin()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Weather commands ─────────────────────────────────────────────────

@cli.group()
def weather():
    """Weather system control."""
    pass


@weather.command("get")
@click.pass_context
def weather_get(ctx):
    """Get current weather state."""
    try:
        result = weather_mod.get()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@weather.command("set")
@click.argument("state")
@click.pass_context
def weather_set(ctx, state):
    """Set weather (0-3 or clear/light_snow/heavy_snow/blizzard)."""
    try:
        # Try as integer first
        try:
            state_val = int(state)
        except ValueError:
            state_val = state
        result = weather_mod.set(state_val)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── World inspection commands ────────────────────────────────────────

@cli.group("world")
def world_group():
    """World inspection and rendering."""
    pass


@world_group.command("list-instances")
@click.pass_context
def world_list(ctx):
    """List all mesh/sprite instances."""
    try:
        instances = world_mod.list_instances()
        if ctx.obj["json"]:
            click.echo(json.dumps(instances, indent=2, default=str))
        else:
            click.echo(f"  Instances: {len(instances)}")
            for inst in instances[:50]:  # Cap display
                click.echo(f"    {inst.get('raw', inst)}")
            if len(instances) > 50:
                click.echo(f"    ... and {len(instances) - 50} more")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@world_group.command("render")
@click.option("--output", "-o", "output_file", default=None,
              help="Save base64 data to file")
@click.pass_context
def world_render(ctx, output_file):
    """Render current view to ASCII cells."""
    try:
        result = world_mod.render()
        if output_file and result.get("data"):
            with open(output_file, "w") as f:
                f.write(result["data"])
            result["output_file"] = output_file
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@world_group.command("dump-matrix")
@click.pass_context
def world_dump_matrix(ctx):
    """Dump material shade tables."""
    try:
        lines = world_mod.dump_matrix()
        if ctx.obj["json"]:
            click.echo(json.dumps({"matrix": lines}, indent=2))
        else:
            for line in lines:
                click.echo(f"  {line}")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@world_group.command("debug-axis")
@click.pass_context
def world_debug_axis(ctx):
    """Place debug axis cubes at origin."""
    try:
        result = world_mod.debug_axis()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@world_group.command("select-instance")
@click.argument("idx", type=int)
@click.option("--add", is_flag=True, help="Add to selection instead of replacing")
@click.pass_context
def world_select(ctx, idx, add):
    """Select instance by index."""
    try:
        result = inst_mod.select_instance(idx, add=add)
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@world_group.command("clear-selection")
@click.pass_context
def world_clear_sel(ctx):
    """Clear all selection."""
    try:
        result = inst_mod.clear_selection()
        _output(result, ctx.obj["json"])
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@world_group.command("get-selected")
@click.pass_context
def world_get_selected(ctx):
    """List selected instances."""
    try:
        selected = inst_mod.get_selected()
        if ctx.obj["json"]:
            click.echo(json.dumps(selected, indent=2, default=str))
        else:
            click.echo(f"  Selected: {len(selected)}")
            for s in selected:
                click.echo(f"    [{s.get('idx','')}] {s.get('name','')} at {s.get('x','')},{s.get('y','')},{s.get('z','')}")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Asset commands ───────────────────────────────────────────────────

@cli.group()
def assets():
    """Asset discovery (meshes, sprites)."""
    pass


@assets.command("list-meshes")
@click.pass_context
def assets_list_meshes(ctx):
    """List all loaded mesh definitions."""
    try:
        meshes = assets_mod.list_meshes()
        if ctx.obj["json"]:
            click.echo(json.dumps(meshes, indent=2))
        else:
            click.echo(f"  Meshes: {len(meshes)}")
            for m in meshes:
                click.echo(f"    {m['idx']:3d}  {m['name']}  faces={m['faces']}")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@assets.command("list-sprites")
@click.pass_context
def assets_list_sprites(ctx):
    """List all loaded sprites."""
    try:
        sprites = assets_mod.list_sprites()
        if ctx.obj["json"]:
            click.echo(json.dumps(sprites, indent=2))
        else:
            click.echo(f"  Sprites: {len(sprites)}")
            for s in sprites:
                click.echo(f"    {s['idx']:3d}  {s['name']}  angles={s['angles']} anims={s['anims']} frames={s['frames']} projs={s['projs']}")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Minimap commands ─────────────────────────────────────────────────

@cli.group("minimap")
def minimap_group():
    """Minimap viewing for agent workflows."""
    pass


@minimap_group.command("view")
@click.option("--map", "map_path", default=None, metavar="FILE",
              help="Read directly from .a3d file and use embedded markers")
@click.option("--cx", type=float, default=None, help="Center X (default: camera X or 0)")
@click.option("--cy", type=float, default=None, help="Center Y (default: camera Y or 0)")
@click.option("--width", "-w", type=int, default=48, help="Grid width in cells")
@click.option("--height", "-h", type=int, default=24, help="Grid height in cells")
@click.option("--scale", "-s", type=float, default=16.0, help="World units per cell")
@click.option("--no-markers", is_flag=True, help="Hide markers")
@click.option("--no-meshes", is_flag=True, help="Hide mesh footprint outlines")
@click.option("--min-mesh-size", type=float, default=16.0,
              help="Minimum mesh bbox span to show (world units)")
@click.pass_context
def minimap_view(ctx, map_path, cx, cy, width, height, scale, no_markers, no_meshes,
                 min_mesh_size):
    """Render a colored minimap of the terrain to the terminal.

    Shows material types as colored ASCII art with markers overlaid.
    Use --map FILE to read directly from an .a3d file without starting asciiid.
    Embedded map markers are only available on the --map path.
    """
    try:
        # A3D-direct path: no asciiid needed
        if map_path is not None:
            cx = cx if cx is not None else 0.0
            cy = cy if cy is not None else 0.0
            markers = [] if no_markers else None
            ansi_output = minimap_mod.render_minimap_from_a3d(
                map_path=map_path,
                cx=cx, cy=cy,
                scale=scale, width=width, height=height,
                markers=markers,
            )
            if ctx.obj["json"]:
                click.echo(json.dumps({"output": ansi_output}, default=str))
            else:
                click.echo(ansi_output)
            return

        # Live editor path
        if cx is None or cy is None:
            cam = cam_mod.get()
            if cx is None:
                cx = cam.get("x", 0.0)
            if cy is None:
                cy = cam.get("y", 0.0)

        grid_data = minimap_mod.query_terrain_grid(cx, cy, width, height, scale)

        markers = []

        footprints = None
        if not no_meshes:
            try:
                fp_data = minimap_mod.query_mesh_footprints(
                    cx, cy, width, height, scale, min_mesh_size,
                )
                footprints = fp_data.get("footprints", []) or None
            except Exception:
                pass

        ansi_output = minimap_mod.render_ansi(
            grid_data, markers=markers, player_pos=(cx, cy),
            footprints=footprints,
        )

        if ctx.obj["json"]:
            click.echo(json.dumps({
                "width": grid_data.get("width"),
                "height": grid_data.get("height"),
                "cx": cx, "cy": cy, "scale": scale,
                "grid": grid_data.get("grid", []),
                "markers": markers,
            }, indent=2, default=str))
        else:
            click.echo(ansi_output)
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@minimap_group.command("add-marker", hidden=True)
@click.argument("name")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.option("--type", "marker_type", type=click.Choice(["building", "region"]),
              default="building", help="Marker type (affects color)")
@click.pass_context
def minimap_add_marker(ctx, name, x, y, marker_type):
    """Add a named marker at world coordinates.

    Buildings show in yellow, regions in cyan.
    """
    try:
        raise click.ClickException(
            "Minimap markers are embedded in the .a3d map file. "
            "Sidecar mutation is disabled until map-file marker editing exists."
        )
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@minimap_group.command("remove-marker", hidden=True)
@click.argument("name")
@click.pass_context
def minimap_remove_marker(ctx, name):
    """Remove a marker by name."""
    try:
        raise click.ClickException(
            "Minimap markers are embedded in the .a3d map file. "
            "Sidecar mutation is disabled until map-file marker editing exists."
        )
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


@minimap_group.command("list-markers")
@click.option("--map", "map_path", default=None, metavar="FILE",
              help="Read embedded markers from a specific .a3d file")
@click.pass_context
def minimap_list_markers(ctx, map_path):
    """List embedded minimap markers from an .a3d file."""
    try:
        markers = minimap_mod.list_markers(map_path=map_path)
        if ctx.obj["json"]:
            click.echo(json.dumps(markers, indent=2, default=str))
        else:
            if not markers:
                click.echo("  No embedded markers found.")
            else:
                for m in markers:
                    mtype = m.get("type", "building")
                    suffix = f" ({m['label']})" if m.get("label") else ""
                    click.echo(f"  [{mtype}] {m['name']}{suffix} at ({m['x']}, {m['y']})")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Echo command ─────────────────────────────────────────────────────

@cli.command()
@click.argument("text")
@click.pass_context
def echo(ctx, text):
    """Send an echo command (connectivity test)."""
    try:
        result = world_mod.echo(text)
        if ctx.obj["json"]:
            click.echo(json.dumps({"echo": result}))
        else:
            click.echo(f"  {result}")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── Raw MCP command ──────────────────────────────────────────────────

@cli.command("raw")
@click.argument("command")
@click.pass_context
def raw_command(ctx, command):
    """Send a raw MCP command to the editor."""
    try:
        lines = editor_mod.send(command)
        if ctx.obj["json"]:
            click.echo(json.dumps({"command": command, "response": lines}))
        else:
            for line in lines:
                click.echo(f"  {line}")
    except Exception as e:
        handle_error(e, ctx.obj["json"])
        sys.exit(1)


# ── REPL ─────────────────────────────────────────────────────────────

REPL_COMMANDS = {
    "editor start": "Launch asciiid in MCP mode",
    "editor stop": "Stop the asciiid process",
    "editor status": "Check process status",
    "project load [PATH]": "Load .a3d world file",
    "project save PATH": "Save world to .a3d",
    "project info": "Show world info",
    "terrain set-height H": "Set terrain height",
    "terrain probe X Y": "Query height at position",
    "terrain grid ALPHA": "Set grid visibility",
    "place mesh NAME X Y Z": "Place a mesh instance",
    "place sprite NAME X Y Z": "Place a sprite instance",
    "place set-active NAME": "Set active sprite",
    "camera get": "Get camera state",
    "camera set X Y Z YAW": "Set camera position",
    "camera focus-origin": "Jump to origin",
    "weather get": "Get weather state",
    "weather set STATE": "Set weather (0-3 or name)",
    "world list-instances": "List all instances",
    "world select-instance IDX [--add]": "Select instance by index",
    "world clear-selection": "Clear all selection",
    "world get-selected": "List selected instances",
    "world render": "Render current view",
    "minimap view [--map FILE] [--cx X --cy Y]": "Render colored minimap (--map skips asciiid)",
    "minimap list-markers [--map FILE]": "List embedded map markers",
    "assets list-meshes": "List loaded mesh definitions",
    "assets list-sprites": "List loaded sprites",
    "raw COMMAND": "Send raw MCP command",
    "help": "Show this help",
    "quit / exit": "Exit the REPL",
}


@cli.command(hidden=True)
@click.pass_context
def repl(ctx):
    """Interactive REPL mode."""
    skin = ReplSkin("asciiid", version="1.0.0")
    skin.print_banner()

    pt_session = skin.create_prompt_session()

    while True:
        try:
            # Get session info for prompt
            st = editor_mod.status()
            project_name = st.get("loaded_map", "")
            modified = st.get("modified", False)
            context = project_name if st.get("status") == "running" else ""

            line = skin.get_input(
                pt_session,
                project_name=context,
                modified=modified,
            )

            if not line:
                continue

            if line.lower() in ("quit", "exit", "q"):
                skin.print_goodbye()
                break

            if line.lower() == "help":
                skin.help(REPL_COMMANDS)
                continue

            # Parse and dispatch through Click
            try:
                args = shlex.split(line)
            except ValueError as e:
                skin.error(f"Parse error: {e}")
                continue

            try:
                cli.main(args=args, standalone_mode=False, obj=ctx.obj)
            except SystemExit:
                pass
            except click.exceptions.UsageError as e:
                skin.error(str(e))
            except Exception as e:
                skin.error(str(e))

        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break


# ── Entry point ──────────────────────────────────────────────────────

def main():
    cli(obj={})


if __name__ == "__main__":
    main()
