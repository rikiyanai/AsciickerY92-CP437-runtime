#!/usr/bin/env python3
# WARNING: agents keep calling this with wrong flags (FL-2566).
# terrain-grid uses --cx/--cy, NOT --x/--y/--radius.
# probe-point uses --x/--y (no --radius).
# READ THE SUBCOMMAND HELP BELOW before invoking.
"""Terrain inspection CLI — wraps asciiid --batch for A3D terrain queries.

Subcommands:

  terrain-grid <a3d> --cx N --cy N [--w N] [--h N] [--scale N]
      Query a W×H terrain grid centered at (cx,cy) with SCALE units/cell.
      Prints mat_id and height per cell, plus summary of unique materials.

  probe-building-paint <a3d> --buildings <building_instances.json>
      Check whether mat 2 (building material) was painted at each building
      center in the terrain.  Useful for diagnosing paint_buildings failures
      (FL-1174 class: all buildings show mat 1 only).

  probe-fixtures <a3d> --fixtures <fixture_instances.json> [--write-back]
      Filter sentinel fixtures (XY < -5), run PROBE_TERRAIN for survivors,
      report Z distribution.  With --write-back patches transform[14] and
      rewrites the JSON (same as _filter_and_probe_fixtures in sbu_e2e_run).

  probe-point <a3d> --x N --y N
      Single PROBE_TERRAIN call — returns height at (x, y).

  building-bases --buildings <building_instances.json> [--mesh-root DIR] [--expected-base H]
      Compute each building instance's implied world-space base/top using its
      run-local AKM bounds. Useful for proving whether a buildings-only handoff
      still lands on the terrain baseline without opening asciiid. This is a
      base-Z check only; it is not a visible-size / camera-framing oracle.

Usage examples:
    python3 scripts/inspect_terrain.py terrain-grid assets/a3d/sbu_work.a3d \\
        --cx 848 --cy 723 --w 9 --h 9 --scale 16

    python3 scripts/inspect_terrain.py probe-building-paint \\
        assets/meshes/osm_runs/sbu_e2e_baked_20260418_full12/output_terrain_only.a3d \\
        --buildings assets/meshes/osm_runs/sbu_e2e_baked_20260418_full12/building_instances.json

    python3 scripts/inspect_terrain.py probe-fixtures \\
        assets/meshes/osm_runs/sbu_e2e_baked_20260418_full12/output.a3d \\
        --fixtures assets/meshes/osm_runs/sbu_e2e_baked_20260418_full12/fixture_instances.json

    python3 scripts/inspect_terrain.py building-bases \\
        --buildings assets/meshes/osm_runs/launcher_osm_20260430_070001/building_instances.json
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASCIIID_BIN = str(PROJECT_ROOT / ".run" / "asciiid")

# ---------------------------------------------------------------------------
# asciiid batch helper
# ---------------------------------------------------------------------------

def _run_batch(map_path, commands, timeout=120):
    """Run asciiid --batch with the given commands; return stdout."""
    inp = "".join(cmd if cmd.endswith("\n") else cmd + "\n" for cmd in commands)
    proc = subprocess.run(
        [ASCIIID_BIN, "--batch", "--map", map_path],
        input=inp,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        print(f"asciiid error (exit {proc.returncode}):", file=sys.stderr)
        print(proc.stderr[-400:], file=sys.stderr)
        sys.exit(1)
    return proc.stdout


def _parse_probe_heights(stdout, expected):
    heights = []
    for line in stdout.splitlines():
        m = re.search(r"height=(\d+)", line)
        if m:
            heights.append(float(m.group(1)))
    while len(heights) < expected:
        heights.append(None)
    return heights[:expected]


def _read_akm_bounds(akm_path):
    """Read min/max XYZ from an ASCII PLY-backed .akm file."""
    with open(akm_path, encoding="utf-8", errors="replace") as fh:
        vertex_count = None
        in_vertex = False
        vertex_props = []
        for raw in fh:
            line = raw.strip()
            if line == "end_header":
                break
            if line.startswith("element vertex "):
                vertex_count = int(line.split()[-1])
                in_vertex = True
                continue
            if line.startswith("element "):
                in_vertex = False
                continue
            if line.startswith("property ") and in_vertex:
                vertex_props.append(line.split()[-1])

        if vertex_count is None or vertex_count <= 0:
            raise ValueError(f"missing vertex header in {akm_path}")
        if not {"x", "y", "z"}.issubset(vertex_props):
            raise ValueError(f"missing xyz vertex props in {akm_path}")

        x_idx = vertex_props.index("x")
        y_idx = vertex_props.index("y")
        z_idx = vertex_props.index("z")
        xs, ys, zs = [], [], []
        for _ in range(vertex_count):
            parts = fh.readline().split()
            xs.append(float(parts[x_idx]))
            ys.append(float(parts[y_idx]))
            zs.append(float(parts[z_idx]))

    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "min_z": min(zs),
        "max_z": max(zs),
    }


# ---------------------------------------------------------------------------
# terrain-grid
# ---------------------------------------------------------------------------

def cmd_terrain_grid(a3d, cx, cy, w, h, scale):
    stdout = _run_batch(a3d, [f"QUERY_TERRAIN_GRID {cx} {cy} {w} {h} {scale}"])
    start = stdout.find("[TERRAIN_GRID_START]")
    end = stdout.find("[TERRAIN_GRID_END]")
    if start < 0 or end < 0:
        print("ERROR: no grid output from asciiid", file=sys.stderr)
        sys.exit(1)

    raw = stdout[start:end + len("[TERRAIN_GRID_END]")]
    lines = raw.strip().splitlines()
    header = lines[0]
    data_lines = [l for l in lines[1:] if "," in l]

    print(header)
    all_mats = []
    for row in data_lines:
        cells = row.strip().split()
        all_mats.extend(c.split(",")[0] for c in cells)
        # format: mat:height per cell
        formatted = "  ".join(f"{c.split(',')[0]}:{c.split(',')[1]}" for c in cells)
        print(formatted)

    mat_counts = Counter(all_mats)
    print(f"\nMat summary: {dict(sorted(mat_counts.items()))}")
    mat2 = mat_counts.get("2", 0)
    total = sum(mat_counts.values())
    print(f"Mat 2 coverage: {mat2}/{total} cells ({100*mat2/total:.1f}%)" if total else "")


# ---------------------------------------------------------------------------
# probe-building-paint
# ---------------------------------------------------------------------------

def cmd_probe_building_paint(a3d, buildings_json, grid_size=3, grid_scale=32):
    with open(buildings_json) as f:
        buildings = json.load(f)

    commands = []
    for b in buildings:
        t = b["transform"]
        commands.append(f"QUERY_TERRAIN_GRID {t[12]:.0f} {t[13]:.0f} {grid_size} {grid_size} {grid_scale}")

    stdout = _run_batch(a3d, commands, timeout=180)

    blocks = re.findall(
        r"\[TERRAIN_GRID_START\][^\n]*\n(.*?)\[TERRAIN_GRID_END\]",
        stdout, re.DOTALL
    )

    missing = len(buildings) - len(blocks)
    if missing:
        print(f"WARNING: received {len(blocks)}/{len(buildings)} grids", file=sys.stderr)

    print(f"{'Building':<52} {'mat2?':<6} {'mat IDs'}")
    print("-" * 80)
    no_mat2 = []
    for b, block in zip(buildings, blocks):
        cells = re.findall(r"(\d+),\d+", block)
        has_mat2 = "2" in cells
        mats = " ".join(sorted(set(cells)))
        flag = "YES" if has_mat2 else "no"
        print(f"{b['inst_name']:<52} {flag:<6} {mats}")
        if not has_mat2:
            no_mat2.append(b["inst_name"])

    print(f"\nMissing mat 2: {len(no_mat2)}/{len(buildings)}")
    if len(no_mat2) == len(buildings):
        print("  → ALL buildings missing mat 2 — paint_buildings step likely failed silently")
    elif no_mat2:
        for name in no_mat2:
            print(f"  - {name}")


# ---------------------------------------------------------------------------
# probe-fixtures
# ---------------------------------------------------------------------------

def cmd_probe_fixtures(a3d, fixtures_json, write_back=False):
    with open(fixtures_json) as f:
        fixtures = json.load(f)

    _SENTINEL_THRESHOLD = -5.0
    valid = []
    sentinels = []
    for fx in fixtures:
        t = fx.get("transform", [])
        bx = t[12] if len(t) > 12 else 0.0
        by = t[13] if len(t) > 13 else 0.0
        if bx < _SENTINEL_THRESHOLD or by < _SENTINEL_THRESHOLD:
            sentinels.append(fx)
        else:
            valid.append(fx)

    sentinel_names = Counter(fx["mesh_name"] for fx in sentinels)
    print(f"Input: {len(fixtures)} fixtures")
    print(f"Sentinels filtered: {len(sentinels)} (threshold={_SENTINEL_THRESHOLD})")
    if sentinel_names:
        for name, count in sorted(sentinel_names.items()):
            print(f"  {name}: {count}")
    print(f"Valid: {len(valid)}")

    if not valid:
        print("Nothing to probe.")
        return

    cmds = [f"PROBE_TERRAIN {fx['transform'][12]:.3f} {fx['transform'][13]:.3f}" for fx in valid]
    stdout = _run_batch(a3d, cmds)
    heights = _parse_probe_heights(stdout, len(valid))

    none_count = sum(1 for h in heights if h is None)
    patched = 0
    height_dist = Counter()
    for fx, h in zip(valid, heights):
        z = h if h is not None else 120.0
        height_dist[int(z)] += 1
        if write_back:
            fx["transform"][14] = z
            if h != 120.0:
                patched += 1

    print(f"Probed: {len(valid) - none_count}/{len(valid)} ({none_count} failed)")
    print(f"Z distribution: {dict(sorted(height_dist.items()))}")
    if none_count:
        print(f"  {none_count} fixtures fell back to Z=120.0")

    if write_back:
        with open(fixtures_json, "w") as f:
            json.dump(valid, f, indent=2, sort_keys=True)
        print(f"Wrote back {len(valid)} fixtures ({patched} non-baseline Z) to {fixtures_json}")


# ---------------------------------------------------------------------------
# probe-point
# ---------------------------------------------------------------------------

def cmd_probe_point(a3d, x, y):
    stdout = _run_batch(a3d, [f"PROBE_TERRAIN {x:.3f} {y:.3f}"])
    for line in stdout.splitlines():
        if "height=" in line:
            print(line.strip())
            return
    print("No height returned")


def cmd_building_bases(buildings_json, mesh_root=None, expected_base=120.0, tolerance=0.25):
    buildings_path = Path(buildings_json).resolve()
    with buildings_path.open(encoding="utf-8") as fh:
        buildings = json.load(fh)

    if not isinstance(buildings, list):
        print(f"ERROR: expected a list in {buildings_path}", file=sys.stderr)
        sys.exit(1)

    mesh_root_path = Path(mesh_root).resolve() if mesh_root else (buildings_path.parent / "meshes").resolve()
    if not mesh_root_path.is_dir():
        print(f"ERROR: mesh root not found: {mesh_root_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Buildings: {buildings_path}")
    print(f"Mesh root: {mesh_root_path}")
    print(f"Expected base: {expected_base:.3f} +/- {tolerance:.3f}")
    print(f"{'Building':<40} {'base_z':>9} {'top_z':>9} {'dx':>9} {'dy':>9} {'status'}")
    print("-" * 92)

    drifted = []
    bases = []
    for raw in buildings:
        mesh_name = raw.get("mesh_name", "")
        transform = raw.get("transform") or []
        if len(transform) != 16:
            drifted.append((mesh_name or "<missing>", "bad-transform"))
            continue
        akm_path = mesh_root_path / mesh_name
        if not akm_path.is_file():
            drifted.append((mesh_name or "<missing>", "missing-mesh"))
            continue
        bounds = _read_akm_bounds(akm_path)
        scale_z = float(transform[10])
        # FL-2553: this derived base_z proves vertical grounding only.
        # It can disprove the old ~40960 baseline failure, but it cannot prove
        # that asciiid presents the building at the correct apparent size once
        # the map is opened and framed at runtime.
        base_z = float(transform[14] + bounds["min_z"] * scale_z)
        top_z = float(transform[14] + bounds["max_z"] * scale_z)
        dx = float(bounds["max_x"] - bounds["min_x"])
        dy = float(bounds["max_y"] - bounds["min_y"])
        bases.append(base_z)
        delta = abs(base_z - expected_base)
        status = "OK" if delta <= tolerance else "DRIFT"
        print(f"{mesh_name:<40} {base_z:>9.3f} {top_z:>9.3f} {dx:>9.3f} {dy:>9.3f} {status}")
        if status != "OK":
            drifted.append((mesh_name, "base-drift"))

    if bases:
        print("-" * 92)
        print(f"Base range: {min(bases):.3f} .. {max(bases):.3f}")
    if drifted:
        print(f"FAIL: {len(drifted)} building(s) drifted or were unreadable", file=sys.stderr)
        sys.exit(1)
    print("PASS: all building bases match the expected terrain baseline")
    print("NOTE: building-bases proves base Z only; it does not close visible building size/origin/runtime scale lanes (FL-2553).")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Terrain inspection CLI — wraps asciiid --batch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # terrain-grid
    p_grid = sub.add_parser("terrain-grid", help="Query W×H terrain grid centered at (cx,cy)")
    p_grid.add_argument("a3d", help="Path to .a3d file")
    p_grid.add_argument("--cx", type=float, required=True)
    p_grid.add_argument("--cy", type=float, required=True)
    p_grid.add_argument("--w", type=int, default=9, help="Grid width (default 9)")
    p_grid.add_argument("--h", type=int, default=9, help="Grid height (default 9)")
    p_grid.add_argument("--scale", type=float, default=32.0, help="World units per cell (default 32)")

    # probe-building-paint
    p_paint = sub.add_parser("probe-building-paint", help="Check mat 2 at each building center")
    p_paint.add_argument("a3d", help="Path to .a3d file")
    p_paint.add_argument("--buildings", required=True, help="building_instances.json path")
    p_paint.add_argument("--grid-size", type=int, default=3, help="Grid size for each building (default 3)")
    p_paint.add_argument("--grid-scale", type=int, default=32, help="Units per cell (default 32)")

    # probe-fixtures
    p_fix = sub.add_parser("probe-fixtures", help="Filter sentinels and probe terrain Z for fixtures")
    p_fix.add_argument("a3d", help="Path to .a3d file")
    p_fix.add_argument("--fixtures", required=True, help="fixture_instances.json path")
    p_fix.add_argument("--write-back", action="store_true",
                       help="Patch transform[14] and rewrite JSON (FL-1144 pass)")

    # probe-point
    p_pt = sub.add_parser("probe-point", help="Single PROBE_TERRAIN at (x, y)")
    p_pt.add_argument("a3d", help="Path to .a3d file")
    p_pt.add_argument("--x", type=float, required=True)
    p_pt.add_argument("--y", type=float, required=True)

    # building-bases
    p_bases = sub.add_parser("building-bases", help="Check implied world-space building base Z from instances + AKMs (not a visible-size oracle)")
    p_bases.add_argument("--buildings", required=True, help="building_instances.json path")
    p_bases.add_argument("--mesh-root", default=None, help="Directory containing referenced AKMs (default: sibling meshes/)")
    p_bases.add_argument("--expected-base", type=float, default=120.0, help="Expected terrain baseline (default 120)")
    p_bases.add_argument("--tolerance", type=float, default=0.25, help="Allowed absolute base drift (default 0.25)")

    args = parser.parse_args()

    if args.cmd == "terrain-grid":
        cmd_terrain_grid(args.a3d, args.cx, args.cy, args.w, args.h, args.scale)
    elif args.cmd == "probe-building-paint":
        cmd_probe_building_paint(args.a3d, args.buildings, args.grid_size, args.grid_scale)
    elif args.cmd == "probe-fixtures":
        cmd_probe_fixtures(args.a3d, args.fixtures, args.write_back)
    elif args.cmd == "probe-point":
        cmd_probe_point(args.a3d, args.x, args.y)
    elif args.cmd == "building-bases":
        cmd_building_bases(args.buildings, args.mesh_root, args.expected_base, args.tolerance)


if __name__ == "__main__":
    main()
