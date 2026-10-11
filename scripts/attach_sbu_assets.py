#!/usr/bin/env python3
"""Populate assets/a3d/sbu_work.a3d with markers and fixtures.

Approach F workaround (FL-1146 / FL-1143) — manual coordinate re-projection
from full12 artifacts, bypassing the OSM pipeline (disk-bomb risk).

Steps:
  1. Read 23 markers from full12/output_terrain_only.a3d, remap, embed
  2. PROBE_TERRAIN for each fixture to resolve Z (fixes FL-1144)
  3. Append 55 remapped fixture instances (stone.akm excluded — FL-1143)
  4. Terrain integrity probe using marker positions (FL-1169 guard)
  5. Count report

The topology bake path (old approach F steps 2-4) is removed.
sbu_work.a3d already contains correctly baked terrain from a prior pipeline
run. Running BAKE_MESH_TO_TERRAIN on it with overwrite_height=1 is
destructive (FL-1169). Use --force-bake only if you have a specific reason
to re-bake and fully understand the consequences.

Known limitations (pipeline issues, not fixed here):
  - Coordinate transform is ASSUMED (analytically derived; workspace.blend lost)
    Worst-case placement error ~79 units (FL-1146).
  - FL-1143 root cause remains open in osm_pipeline.py (stone.akm off-map).

Usage:
    cd <project-root>
    python3 scripts/attach_sbu_assets.py
    python3 scripts/attach_sbu_assets.py --dry-run
    python3 scripts/attach_sbu_assets.py --force-bake  # DANGEROUS: re-bakes terrain
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Import a3d_format and a3d_edit without touching bpy or running asciiid
# ---------------------------------------------------------------------------

def _load_module(name, path):
    path = Path(path)
    if not path.exists():
        raise ImportError(f"Cannot find {name} at {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_a3d_format = _load_module(
    "a3d_format",
    PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py",
)
_a3d_edit = _load_module(
    "a3d_edit",
    PROJECT_ROOT / "docs/agent/cli-anything" / "a3d_edit.py",
)
_osm_bake_contract = _load_module(
    "osm_bake_contract",
    PROJECT_ROOT / "scripts" / "osm_bake_contract.py",
)

read_a3d_sections = _a3d_edit.read_a3d_sections
write_a3d_sections = _a3d_edit.write_a3d_sections
A3DMinimapMarker = _a3d_format.A3DMinimapMarker

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FULL12 = PROJECT_ROOT / "assets" / "meshes" / "osm_runs" / "sbu_e2e_baked_20260418_full12"
SRC_A3D = FULL12 / "output_terrain_only.a3d"
TOPOLOGY_JSON = FULL12 / "topology_instance.json"
FIXTURE_JSON = FULL12 / "fixture_instances.json"

SBU_WORK = PROJECT_ROOT / "assets" / "a3d" / "sbu_work.a3d"
A3D_EDIT = PROJECT_ROOT / "docs/agent/cli-anything" / "a3d_edit.py"
ASCIIID = PROJECT_ROOT / ".run" / "asciiid"

TMP_TOPOLOGY = Path("/tmp/sbu_topology_remapped.json")
TMP_FIXTURES = Path("/tmp/sbu_fixtures_remapped.json")

# ---------------------------------------------------------------------------
# Coordinate transform: full12 → test_map
#
# Derived analytically from known full12 blosm extents (ASSUMED, not proven
# by a saved workspace.blend).  Worst-case error ~79 units.
#
#   x_nat  = (x_full12 − 2600) / 12.0
#   y_nat  = (y_full12 − 3032) / 12.0
#   test_x = x_nat × 4.073 + 888
#   test_y = y_nat × 4.073 + 1040
# ---------------------------------------------------------------------------

_SCALE = 4.073 / 12.0  # ≈ 0.33942


def remap_xy(x, y):
    test_x = (x - 2600.0) / 12.0 * 4.073 + 888.0
    test_y = (y - 3032.0) / 12.0 * 4.073 + 1040.0
    return test_x, test_y


def remap_instance(raw, z_probed=None):
    """Return new instance dict with rotation/scale and translation remapped.

    - Columns 0-2 (rotation/scale): multiply 3x3 block by _SCALE
    - Translation column 3 (indices 12,13): apply remap_xy()
    - tz (index 14): use z_probed if provided, else 120.0 (FL-1144 fallback)
    """
    t = list(raw["transform"])
    for col in range(3):
        for row in range(3):
            t[col * 4 + row] *= _SCALE
    t[12], t[13] = remap_xy(t[12], t[13])
    t[14] = float(z_probed) if z_probed is not None else 120.0
    return {**raw, "transform": t}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_a3d_edit(*args):
    cmd = [sys.executable, str(A3D_EDIT)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"ERROR: a3d_edit.py failed (exit {result.returncode}):")
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


def run_asciiid_batch(cmds, label="asciiid batch", timeout=60):
    """Run a list of commands via asciiid --batch. Returns stdout string or None on error."""
    if not ASCIIID.exists():
        print(f"  WARNING: asciiid not found at {ASCIIID} — {label} skipped")
        return None
    stdin_text = "\n".join(cmds) + "\n"
    try:
        result = subprocess.run(
            [str(ASCIIID), "--batch"],
            input=stdin_text, capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  ERROR: {label} timed out after {timeout}s")
        return None
    if result.returncode != 0:
        print(f"  ERROR: {label} returned exit {result.returncode}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:500]}")
        return None
    return result.stdout


def _parse_probe_heights(stdout, expected_count):
    """Parse PROBE_TERRAIN stdout lines; return list of float|None per expected probe."""
    heights = []
    for line in stdout.splitlines():
        m = re.search(r"height=(\d+)", line)
        if m:
            heights.append(float(m.group(1)))
    while len(heights) < expected_count:
        heights.append(None)
    return heights[:expected_count]


def _check_bake_per_instance_coverage(stdout, label, max_stuck_pct):
    """Fail closed if asciiid reports too much baseline-stuck terrain in a bake."""
    coverage = _osm_bake_contract.parse_bake_coverage(stdout)
    verdict = _osm_bake_contract.evaluate_bake_coverage(coverage, max_stuck_pct)
    tag = f" [{label}]" if label else ""
    if not verdict["has_data"]:
        print(f"  WARNING: COVERAGE{tag} no per-instance data — asciiid predates BakeCoverage surface")
        return
    if verdict["stuck_instances"]:
        for stuck in verdict["stuck_instances"]:
            print(
                "  ERROR: "
                f"COVERAGE{tag} {stuck['name']}: "
                f"{stuck['at_baseline']}/{stuck['footprint_cells']} cells stuck at baseline "
                f"({stuck['stuck_pct']}% > limit {max_stuck_pct:.0f}%)"
            )
        raise RuntimeError(
            f"Bake coverage FAIL [{label}]: "
            f"{len(verdict['stuck_instances'])}/{verdict['total_instances']} instance(s) exceed "
            f"{max_stuck_pct:.0f}% stuck-at-baseline coverage."
        )
    print(f"  COVERAGE{tag} OK — all {verdict['total_instances']} instance(s) passed")


# ---------------------------------------------------------------------------
# Step 2 helper: probe terrain Z for each fixture
# ---------------------------------------------------------------------------

def probe_fixture_heights(raw_fixtures, dry=False):
    """PROBE_TERRAIN at each fixture's remapped (x, y) in sbu_work.a3d.

    Returns a list of float heights (same length as raw_fixtures).
    None entries mean the probe failed; caller should fall back to 120.0.
    """
    if dry:
        print("  [dry-run] probe skipped — returning None for all fixtures")
        return [None] * len(raw_fixtures)

    cmds = ["LOAD_MAP assets/a3d/sbu_work.a3d"]
    for fx in raw_fixtures:
        t = fx["transform"]
        rx, ry = remap_xy(t[12], t[13])
        cmds.append(f"PROBE_TERRAIN {rx:.3f} {ry:.3f}")

    stdout = run_asciiid_batch(cmds, label="fixture Z probe", timeout=60)
    if stdout is None:
        print("  WARNING: fixture probe failed — using Z=120.0 fallback (FL-1144 unresolved)")
        return [None] * len(raw_fixtures)

    heights = _parse_probe_heights(stdout, len(raw_fixtures))
    none_count = sum(1 for h in heights if h is None)
    if none_count:
        print(f"  WARNING: {none_count}/{len(raw_fixtures)} probes returned no height — using 120.0 fallback")
    return heights


# ---------------------------------------------------------------------------
# Step 4 helper: terrain integrity probe (FL-1169 guard)
# ---------------------------------------------------------------------------

def probe_terrain_integrity(marker_xy_list, dry=False):
    """Probe terrain at building marker positions; warn if all return height=120 (flat).

    Returns True if terrain looks intact, False if flat-terrain pattern detected.
    """
    if dry:
        print("  [dry-run] integrity probe skipped")
        return True

    # Use first 5 markers (building centers — should be elevated if terrain is baked)
    anchors = marker_xy_list[:5]
    if not anchors:
        print("  WARNING: no anchor positions — integrity check skipped")
        return True

    cmds = ["LOAD_MAP assets/a3d/sbu_work.a3d"]
    for ax, ay in anchors:
        cmds.append(f"PROBE_TERRAIN {ax:.3f} {ay:.3f}")

    stdout = run_asciiid_batch(cmds, label="terrain integrity probe", timeout=30)
    if stdout is None:
        print("  WARNING: integrity probe failed — visual verification required")
        return True  # non-fatal: can't confirm but can't condemn

    heights = _parse_probe_heights(stdout, len(anchors))
    valid = [h for h in heights if h is not None]
    print(f"  Anchor probe heights: {[int(h) if h is not None else 'N/A' for h in heights]}")

    if valid and all(h == 120.0 for h in valid):
        print("  WARNING: ALL anchors at height=120 — terrain is flat (FL-1169 pattern detected)")
        print("  The topology bake may have destroyed the terrain. Do NOT commit this file.")
        return False

    elevated = [h for h in valid if h > 120.0]
    if elevated:
        print(f"  Terrain integrity: OK ({len(elevated)}/{len(anchors)} anchors above baseline 120)")
    else:
        print("  WARNING: no anchors above baseline — visual verification strongly recommended")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print actions without modifying any file")
    ap.add_argument("--force-bake", action="store_true",
                    help=("DANGEROUS: append topology and run BAKE_MESH_TO_TERRAIN. "
                          "Destroys existing terrain (FL-1169). Only use if you know "
                          "sbu_work.a3d needs fresh topology baked in."))
    args = ap.parse_args()
    dry = args.dry_run
    force_bake = args.force_bake

    if dry:
        print("[DRY RUN] No files will be modified.\n")
    if force_bake:
        print("=" * 60)
        print("WARNING: --force-bake will run BAKE_MESH_TO_TERRAIN.")
        print("This OVERWRITES existing terrain in sbu_work.a3d.")
        print("FL-1169: prior run with this flag destroyed correct terrain.")
        print("Ensure sbu_work.a3d has a .bak backup before proceeding.")
        print("=" * 60)

    # Guard: required inputs
    required = [
        (SRC_A3D,      "full12/output_terrain_only.a3d"),
        (FIXTURE_JSON, "full12/fixture_instances.json"),
        (SBU_WORK,     "assets/a3d/sbu_work.a3d"),
        (A3D_EDIT,     "docs/agent/cli-anything/a3d_edit.py"),
    ]
    if force_bake:
        required.append((TOPOLOGY_JSON, "full12/topology_instance.json"))
        if not ASCIIID.exists():
            print(f"ERROR: asciiid binary not found at {ASCIIID}")
            sys.exit(1)

    for p, label in required:
        if not p.exists():
            print(f"ERROR: required file missing: {label}\n  ({p})")
            sys.exit(1)

    # ------------------------------------------------------------------
    # STEP 1: Read markers from full12 source, remap XY, embed into sbu_work
    # ------------------------------------------------------------------
    print("=== Step 1: Remap and embed markers ===")
    _, _, _, _, src_markers = read_a3d_sections(str(SRC_A3D))
    print(f"  Source markers: {len(src_markers)}")

    remapped_markers = []
    for m in src_markers:
        nx, ny = remap_xy(m.x, m.y)
        nm = A3DMinimapMarker(
            name=m.name,
            label=m.label,
            x=nx,
            y=ny,
            fg=m.fg,
            glyph=m.glyph,
            marker_type=m.marker_type,
        )
        remapped_markers.append(nm)
        print(f"  {m.name!r:30} ({m.x:.1f},{m.y:.1f}) -> ({nx:.1f},{ny:.1f})")

    if not dry:
        pre, fv, insts, gens, _ = read_a3d_sections(str(SBU_WORK))
        write_a3d_sections(str(SBU_WORK), pre, fv, [], gens, remapped_markers)
        print(f"  -> Wrote {len(remapped_markers)} markers; cleared {len(insts)} existing instances (step 3 re-appends)")
    else:
        print("  [dry-run] markers write skipped")

    # ------------------------------------------------------------------
    # STEPS 2b-4 (--force-bake only): Topology append + bake + marker restore
    # DEFAULT: SKIPPED — sbu_work.a3d already has correctly baked terrain.
    # Running BAKE_MESH_TO_TERRAIN destroys it (FL-1169).
    # ------------------------------------------------------------------
    if force_bake:
        print("\n=== Step 2b: Append topology instance (--force-bake) ===")
        with open(TOPOLOGY_JSON) as f:
            topology_raw = json.load(f)
        if len(topology_raw) != 1:
            print(f"ERROR: expected 1 topology entry, got {len(topology_raw)}")
            sys.exit(1)

        topology_remapped = [remap_instance(topology_raw[0])]
        t = topology_remapped[0]["transform"]
        print(f"  Remapped: tx={t[12]:.3f}  ty={t[13]:.3f}  tz={t[14]:.3f}")
        print(f"  Scale:    sx={t[0]:.3f}  sy={t[5]:.3f}  sz={t[10]:.3f}")
        assert abs(t[12] - 944.8) < 0.5, f"tx mismatch: {t[12]:.3f} (expect ~944.8)"
        assert abs(t[13] - 857.5) < 0.5, f"ty mismatch: {t[13]:.3f} (expect ~857.5)"

        if not dry:
            TMP_TOPOLOGY.write_text(json.dumps(topology_remapped, indent=2))
            out = run_a3d_edit("append", str(SBU_WORK), "--json", str(TMP_TOPOLOGY))
            print(f"  {out}")
        else:
            print("  [dry-run] topology append skipped")

        print("\n=== Step 3b: Bake topology (--force-bake) ===")
        batch_cmds = [
            "LOAD_MAP assets/a3d/sbu_work.a3d",
            "BAKE_MESH_TO_TERRAIN",
            "DELETE_INSTANCE 0",
            "SAVE assets/a3d/sbu_work.a3d",
        ]
        for cmd in batch_cmds:
            print(f"    {cmd}")

        if not dry:
            stdout = run_asciiid_batch(batch_cmds, label="topology bake", timeout=120)
            if stdout is None:
                print("ERROR: topology bake failed")
                sys.exit(1)
            try:
                _check_bake_per_instance_coverage(
                    stdout,
                    "attach-topology-bake",
                    _osm_bake_contract.TOPOLOGY_BAKE_MAX_STUCK_PCT,
                )
            except RuntimeError as exc:
                print(f"ERROR: {exc}")
                sys.exit(1)
            print(f"  stdout:\n{stdout[:3000]}")
        else:
            print("  [dry-run] bake skipped")

        print("\n=== Step 4b: Restore markers after bake (FL-1145 protection) ===")
        if not dry:
            post_pre, post_fv, post_insts, post_gens, dropped = read_a3d_sections(str(SBU_WORK))
            print(f"  Post-bake instances: {len(post_insts)}")
            print(f"  Post-bake markers:   {len(dropped)}")
            write_a3d_sections(str(SBU_WORK), post_pre, post_fv, [], post_gens, remapped_markers)
            print(f"  -> Restored {len(remapped_markers)} markers")
        else:
            print("  [dry-run] marker restore skipped")

    # ------------------------------------------------------------------
    # STEP 2: Probe terrain Z for each fixture (fixes FL-1144)
    # ------------------------------------------------------------------
    print("\n=== Step 2: PROBE_TERRAIN for fixture Z (FL-1144) ===")
    with open(FIXTURE_JSON) as f:
        all_fixtures = json.load(f)

    def _is_sentinel(fx):
        t = fx.get("transform", [])
        return len(t) >= 14 and t[12] == -32.0 and t[13] == -32.0

    stone_skipped = [fx for fx in all_fixtures if fx.get("mesh_name", "").lower() == "stone.akm"]
    sentinel_skipped = [fx for fx in all_fixtures
                        if fx.get("mesh_name", "").lower() != "stone.akm" and _is_sentinel(fx)]
    valid_fixtures = [fx for fx in all_fixtures
                      if fx.get("mesh_name", "").lower() != "stone.akm" and not _is_sentinel(fx)]

    print(f"  Total in JSON: {len(all_fixtures)}")
    print(f"  stone.akm skipped: {len(stone_skipped)} (FL-1143 — mesh excluded)")
    if sentinel_skipped:
        from collections import Counter as _C
        sc = _C(fx["mesh_name"] for fx in sentinel_skipped)
        print(f"  sentinel (-32,-32) skipped: {len(sentinel_skipped)} (FL-1143 — off-map coords): {dict(sc)}")
    print(f"  Valid fixtures: {len(valid_fixtures)}")

    probed_heights = probe_fixture_heights(valid_fixtures, dry=dry)
    probed_count = sum(1 for h in probed_heights if h is not None)
    fallback_count = len(probed_heights) - probed_count
    if probed_count:
        print(f"  Probed: {probed_count} fixtures got terrain Z; {fallback_count} use 120.0 fallback")
    else:
        print(f"  All {len(valid_fixtures)} fixtures using Z=120.0 fallback (FL-1144 unresolved)")

    # ------------------------------------------------------------------
    # STEP 3: Append fixtures with probed Z
    # ------------------------------------------------------------------
    print("\n=== Step 3: Append fixture instances ===")
    from collections import Counter
    for mesh, cnt in sorted(Counter(fx["mesh_name"] for fx in valid_fixtures).items()):
        print(f"    {mesh}: {cnt}")

    remapped_fixtures = [
        remap_instance(fx, z_probed=h)
        for fx, h in zip(valid_fixtures, probed_heights)
    ]

    if not dry:
        TMP_FIXTURES.write_text(json.dumps(remapped_fixtures, indent=2))
        out = run_a3d_edit("append", str(SBU_WORK), "--json", str(TMP_FIXTURES))
        print(f"  {out}")
    else:
        print("  [dry-run] fixture append skipped")

    # ------------------------------------------------------------------
    # STEP 4: Terrain integrity probe (FL-1169 guard)
    # ------------------------------------------------------------------
    print("\n=== Step 4: Terrain integrity probe ===")
    marker_xy = [(m.x, m.y) for m in remapped_markers]
    terrain_ok = probe_terrain_integrity(marker_xy, dry=dry)

    # ------------------------------------------------------------------
    # STEP 5: Count report
    # ------------------------------------------------------------------
    print("\n=== Step 5: Count report ===")
    if not dry:
        _, _, final_insts, _, final_markers = read_a3d_sections(str(SBU_WORK))
        print(f"  Instances: {len(final_insts)}")
        print(f"  Markers:   {len(final_markers)}")
        if not terrain_ok:
            print("\nSTRUCTURAL WRITE ENDED WITH TERRAIN WARNING — visual verification required before commit.")
            sys.exit(1)
        print("\nStructural write ended. Visual verification required.")
    else:
        print("  [dry-run] count skipped")
        print("\nDry run ended.")


if __name__ == "__main__":
    main()
