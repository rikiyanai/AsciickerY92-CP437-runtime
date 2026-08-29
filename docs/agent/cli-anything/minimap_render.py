#!/usr/bin/env python3
"""Render terrain minimap from .a3d file. No asciiid needed.

Reads the .a3d binary directly — fast, no editor process required.

Usage:
  minimap_render.py [options]                 render map (default)
  minimap_render.py list-markers              list embedded map markers
  minimap_render.py --help                    show this help
"""
import argparse
import importlib.util
import sys
from pathlib import Path

CLI_ANYTHING_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = CLI_ANYTHING_ROOT.parents[2]
_CLI_ANYTHING_ROOT = str(CLI_ANYTHING_ROOT)
_MINIMAP_PY = CLI_ANYTHING_ROOT / "cli_anything/asciiid/core/minimap.py"
_DEFAULT_MAP = str(PROJECT_ROOT / "assets/a3d/game_map_y8.a3d")
_GAME_MINIMAP_SCALE = 16.0
_GAME_MINIMAP_WIDTH = 32
_GAME_MINIMAP_HEIGHT = 16


def _load_minimap():
    """Load minimap module from the relocated cli-anything root."""
    if _CLI_ANYTHING_ROOT not in sys.path:
        sys.path.insert(0, _CLI_ANYTHING_ROOT)
    spec = importlib.util.spec_from_file_location("minimap_core", _MINIMAP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Subcommand handlers ──────────────────────────────────────────────

def _resolve_map(path: str) -> str:
    """Resolve map path: try as-is, then relative to PROJECT_ROOT."""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    if p.exists():
        return str(p.resolve())
    fallback = PROJECT_ROOT / path
    if fallback.exists():
        return str(fallback)
    return str(p)  # let open() produce the natural error


def cmd_view(args):
    mm = _load_minimap()
    map_path = _resolve_map(args.map)
    output = mm.render_minimap_from_a3d(
        map_path=map_path,
        cx=args.cx,
        cy=args.cy,
        scale=args.scale,
        width=args.width,
        height=args.height,
        markers=None,
        player_dir=args.dir,
        show_meshes=not args.no_meshes,
        min_footprint_cells=args.min_footprint_cells,
    )
    print(output)


def cmd_list_markers(args):
    mm = _load_minimap()
    markers = mm.list_markers(map_path=_resolve_map(args.map))
    if not markers:
        print("No embedded markers found.")
        return
    col_w = max(len(m["name"]) for m in markers) + 2
    print(f"{'Name':<{col_w}} {'X':>9} {'Y':>9}  {'Type':<10} Glyph  Label")
    print("-" * (col_w + 43))
    for m in markers:
        print(
            f"{m['name']:<{col_w}} {m['x']:>9.1f} {m['y']:>9.1f}"
            f"  {m.get('type', 'building'):<10} {m.get('glyph', '?')}      {m.get('label', '')}"
        )


# ── Argument parser ──────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minimap_render.py",
        description=(
            "Render terrain minimap from .a3d file.\n"
            "No asciiid process needed — reads binary directly."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s                                  render with in-game minimap framing
  %(prog)s --cx 100 --cy -50               center on (100,-50)
  %(prog)s --scale 30 --width 80 --height 40  use the older wide helper view
  %(prog)s --map other.a3d                 use a different map file
  %(prog)s list-markers                   show embedded map markers

material legend:
  ~  Water    .  Grass    :  Dirt     #  Stone
  .  Sand     *  Blood    ~  Mud      o  Cobblestone  ;  Gravel
        """,
    )

    # ── Global options
    parser.add_argument(
        "--map", dest="map", metavar="FILE", default=_DEFAULT_MAP,
        help="A3D map file to render (default: assets/a3d/game_map_y8.a3d)",
    )
    parser.add_argument(
        "--cx", type=float, default=0.0,
        help="Center world X (default: 0)",
    )
    parser.add_argument(
        "--cy", type=float, default=0.0,
        help="Center world Y (default: 0)",
    )
    parser.add_argument(
        "--scale", type=float, default=8.0,
        help="World units per terminal cell — defaults to in-game minimap scale (16)",
    )
    parser.add_argument(
        "--width", type=int, default=_GAME_MINIMAP_WIDTH,
        help="Output grid width in cells (default: in-game width 32)",
    )
    parser.add_argument(
        "--height", type=int, default=_GAME_MINIMAP_HEIGHT,
        help="Output grid height in cells (default: in-game height 16)",
    )
    parser.add_argument(
        "--dir", type=float, default=0.0,
        help="Player direction in degrees for the center arrow (default: 0)",
    )
    parser.add_argument(
        "--no-meshes", dest="no_meshes", action="store_true", default=False,
        help="Hide mesh instance overlay (default: shown)",
    )
    parser.add_argument(
        "--min-footprint-cells", dest="min_footprint_cells", type=float, default=4.0,
        metavar="N",
        help="Min footprint size in grid cells to show (default: 4 — hides small trees/bushes)",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="SUBCOMMAND")

    # ── list-markers
    subparsers.add_parser(
        "list-markers",
        help="List embedded markers with coordinates and type",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.scale == 8.0:
        args.scale = _GAME_MINIMAP_SCALE

    if args.command == "list-markers":
        cmd_list_markers(args)
    else:
        cmd_view(args)


if __name__ == "__main__":
    main()
