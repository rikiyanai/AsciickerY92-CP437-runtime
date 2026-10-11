#!/usr/bin/env python3
"""
picocad_cli.py — Five-subcommand CLI for the picoCAD2 → Asciicker pipeline.

Subcommands
-----------
  convert   GLTF→AKM conversion (wraps picocad_to_akm.py)
  place     Place a mesh in a map via asciiid --batch
  render    Render a map to ANSI base64 via asciiid --batch
  audit     Check an AKM for SAFE_LEVELS compliance
  pipeline  One-shot: convert + place + (render) + (save)

Environment variables
---------------------
  ASCIIID_BIN  Path to asciiid binary (default: .run/asciiid)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so 'from scripts.picocad_mcp' works
_script_dir = Path(__file__).resolve().parent
if str(_script_dir.parent) not in sys.path:
    sys.path.insert(0, str(_script_dir.parent))

from scripts.picocad_mcp import (
    AsciiidBatchSession,
    AsciiidTimeoutError,
    audit_picocad,
    convert_picocad,
    ensure_assets_dir,
    find_repo_root,
)


def _find_asciiid_bin() -> str | None:
    """Return .run/asciiid path, or None if not found (no exception)."""
    try:
        repo = find_repo_root()
        return str(repo / ".run" / "asciiid")
    except FileNotFoundError:
        return None


def _resolve_asciiid_or_die(asciiid_arg: str | None = None) -> str:
    """Resolve asciiid binary path, exiting with error if not found."""
    bin_path = (
        asciiid_arg
        or os.environ.get("ASCIIID_BIN")
        or _find_asciiid_bin()
    )
    if bin_path is None or not os.path.isfile(bin_path):
        found = bin_path or "<auto-detect>"
        print(f"Error: asciiid binary not found at '{found}'", file=sys.stderr)
        sys.exit(1)
    return bin_path


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_convert(args: argparse.Namespace) -> None:
    """GLTF → AKM conversion."""
    result = convert_picocad(
        args.gltf_path,
        output=args.output,
        merge=args.merge,
        passthrough=args.passthrough,
    )
    # Print a summary line with stats
    from scripts.picocad_to_akm import audit_akm
    rc = audit_akm(str(result))
    print(f"Wrote: {result} (exit={rc})")


def cmd_place(args: argparse.Namespace) -> None:
    """Place a mesh in a map via asciiid --batch."""
    asciiid_bin = _resolve_asciiid_or_die(args.asciiid)
    x, y = args.at[0], args.at[1]
    z = args.at[2] if len(args.at) >= 3 else None
    with AsciiidBatchSession(asciiid_bin=asciiid_bin) as sess:
        resp = sess.load_map(args.map)
        if "[MCP] Error:" in resp:
            print(f"Error: LOAD_MAP failed: {resp}", file=sys.stderr)
            sys.exit(1)
        if z is None:
            z = sess.query_terrain_height(x, y)
            print(f"Auto-snapped z={z:.0f} from terrain at {x},{y}")
        resp = sess.place_mesh(args.mesh_name, x, y, z, args.scale)
        print(resp)


def cmd_render(args: argparse.Namespace) -> None:
    """Render a map to ANSI base64."""
    asciiid_bin = _resolve_asciiid_or_die(args.asciiid)
    with AsciiidBatchSession(asciiid_bin=asciiid_bin, map_path=args.map) as sess:
        b64 = sess.render()

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(b64)
        print(f"Render data written to {out_path}")
    else:
        # Print base64 to stdout (caller can pipe to a decoder)
        print(b64, end="")
        sys.stdout.flush()


def cmd_audit(args: argparse.Namespace) -> None:
    """Check AKM for SAFE_LEVELS compliance."""
    rc = audit_picocad(args.akm_path)
    sys.exit(rc)


def cmd_pipeline(args: argparse.Namespace) -> None:
    """One-shot: convert + place + (render) + (save)."""
    from scripts.picocad_mcp import pipeline_picocad

    x, y = args.at[0], args.at[1]
    z = args.at[2] if len(args.at) >= 3 else None

    try:
        result = pipeline_picocad(
            args.gltf_path,
            args.map,
            x,
            y,
            z,
            scale=args.scale,
            render=args.render,
            save=args.save,
            asciiid_bin=_resolve_asciiid_or_die(args.asciiid),
        )
    except (FileNotFoundError, RuntimeError, AsciiidTimeoutError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"AKM: {result['akm_path']}")
    print(f"z used: {result['z_used']:.0f}")
    print(f"Place response: {result['response']}")

    if result["render_b64"] and args.out:
        out_path = Path(args.out)
        out_path.write_text(result["render_b64"])
        print(f"Render data written to {out_path}")
    elif result["render_b64"]:
        print(f"Render data: {len(result['render_b64'])} bytes base64")
        print(result["render_b64"], end="")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="picocad_cli.py",
        description="picoCAD2 → Asciicker pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  picocad_cli.py convert model.gltf\n"
            "  picocad_cli.py place mymesh --map game.a3d --at 50 50 0\n"
            "  picocad_cli.py render --map game.a3d --out render.txt\n"
            "  picocad_cli.py audit assets/meshes/model.akm\n"
            "  picocad_cli.py pipeline model.gltf --map game.a3d --at 50 50 0 --render --save\n"
        ),
    )
    parser.add_argument(
        "--asciiid",
        help="Path to asciiid binary (overrides ASCIIID_BIN env var)",
        default=None,
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # --- convert ---
    p_convert = subparsers.add_parser("convert", help="GLTF → AKM conversion")
    p_convert.add_argument("gltf_path", help="Source GLTF file")
    p_convert.add_argument("--output", help="Output AKM path (default: assets/meshes/<stem>.akm)")
    p_convert.add_argument("--merge", action="store_true", help="Merge all meshes into one")
    p_convert.add_argument("--passthrough", action="store_true", help="Passthrough mode (experimental)")
    p_convert.set_defaults(func=cmd_convert)

    # --- place ---
    p_place = subparsers.add_parser("place", help="Place a mesh in a map")
    p_place.add_argument("mesh_name", help="Mesh name (with or without .akm)")
    p_place.add_argument("--map", required=True, help="Map file (.a3d)")
    p_place.add_argument("--at", nargs="+", type=float, required=True,
                         metavar="N", help="x y [z]  — z auto-queried from terrain if omitted")
    p_place.add_argument("--scale", type=float, default=1.0, help="Scale factor")
    p_place.set_defaults(func=cmd_place)

    # --- render ---
    p_render = subparsers.add_parser("render", help="Render a map to ANSI base64")
    p_render.add_argument("--map", required=True, help="Map file (.a3d)")
    p_render.add_argument("--out", help="Output file for base64 data (default: stdout)")
    p_render.set_defaults(func=cmd_render)

    # --- audit ---
    p_audit = subparsers.add_parser("audit", help="Check AKM for SAFE_LEVELS compliance")
    p_audit.add_argument("akm_path", help="Path to AKM file")
    p_audit.set_defaults(func=cmd_audit)

    # --- pipeline ---
    p_pipe = subparsers.add_parser("pipeline", help="One-shot: convert + place + (render) + (save)")
    p_pipe.add_argument("gltf_path", help="Source GLTF file")
    p_pipe.add_argument("--map", required=True, help="Map file (.a3d)")
    p_pipe.add_argument("--at", nargs="+", type=float, required=True,
                        metavar="N", help="x y [z]  — z auto-queried from terrain if omitted")
    p_pipe.add_argument("--scale", type=float, default=1.0, help="Scale factor")
    p_pipe.add_argument("--render", action="store_true", help="Also render after placing")
    p_pipe.add_argument("--save", action="store_true", help="Save the map after placing")
    p_pipe.add_argument("--out", help="Output file for render data")
    p_pipe.set_defaults(func=cmd_pipeline)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
