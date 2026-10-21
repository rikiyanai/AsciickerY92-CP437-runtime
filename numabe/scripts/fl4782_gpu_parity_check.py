#!/usr/bin/env python3
"""FL-4782 phase 2, check E — GPU execution of water_flow_field.glsl matches the
CPU reference scheme.

This closes the phase-1 debt. Phase 1 could not run check E because Godot 4.6.2
exposes no RenderingDevice under the headless display server on this host, so it
recorded the weaker static check E2 in its place. Phase 2 runs the same driver
under a WINDOWED display server, where the Metal driver exists and
RenderingServer.create_local_rendering_device() succeeds.

The comparison is against the CPU fields phase 1 already produced and committed
(.run/fl4782_phase1/cpu_fields.npz) at the identical config, so the GPU run is
compared to a reference it could not have influenced.

Falsifier: any field difference above the phase-1 tolerances means the shader is
not the reference scheme. The fix is the shader or the driver, never the
tolerance.

Usage:
    python3 scripts/fl4782_gpu_parity_check.py            # compare + write receipt
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE1_DIR = REPO_ROOT / ".run" / "fl4782_phase1"
PHASE2_DIR = REPO_ROOT / ".run" / "fl4782_phase2"

# Tolerances are COPIED from scripts/check_fl4782_flow_field_static.py, which
# owns them. They are not re-derived here and must not be relaxed here.
GPU_CPU_DEPTH_TOL = 1.0e-3
GPU_CPU_VELOCITY_TOL = 5.0e-2
GPU_CPU_TERRAIN_TOL = 1.0e-6

# The renderer's per-tick budget for the named phase-2 unknown.
SIM_TICK_BUDGET_MS = 1.0


def check_g_runtime_terrain_route(steps: int = 800) -> dict:
    """Check G — the RUNTIME terrain route produces the field phase 1 proved.

    ASCIICompositorEffect cannot open the A3D file, so the runtime solver takes
    its heightfield from A3DMapLoader's per-VERTEX corner image (the mean of a
    cell's four corners). This reproduces both routes with the phase-1 owner's
    own decoder and reference scheme and compares (a) the heightfields and
    (b) the fields the solver actually produces from them.

    The decisive comparison is (b). A heightfield difference that cannot change
    the answer is not a defect; a field difference is.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fl4782_ref", REPO_ROOT / "scripts" / "check_fl4782_flow_field_static.py")
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)

    patches, off_x, off_y, _ = ref.load_a3d_patches(ref.MAP_PATH)
    xs = [k[0] for k in patches]
    ys = [k[1] for k in patches]
    bx, by = min(xs), min(ys)
    nx, ny = max(xs) - bx + 1, max(ys) - by + 1
    cw, chh = nx * 8 + 1, ny * 8 + 1

    # Mirror of A3DMapLoader.build_authoritative_corner_height_image().
    corner = np.zeros((chh, cw), dtype=np.float64)
    for gy in range(chh):
        for gx in range(cw):
            px, py = bx + gx // 8, by + gy // 8
            vx, vy = gx % 8, gy % 8
            if gx == nx * 8:
                px -= 1
                vx = 8
            if gy == ny * 8:
                py -= 1
                vy = 8
            grid = patches.get((px, py))
            if grid is None:
                continue
            corner[gy, gx] = ref.height_at(grid, vx, vy)

    mat_origin = (bx * 8 - off_x, by * 8 - off_y)
    base_x = int(round(ref.GRID_ORIGIN_X - mat_origin[0]))
    base_y = int(round(ref.GRID_ORIGIN_Z - mat_origin[1]))
    sub = corner[base_y:base_y + ref.GRID_H + 1, base_x:base_x + ref.GRID_W + 1]
    runtime_terrain = (0.25 * (sub[:-1, :-1] + sub[:-1, 1:]
                               + sub[1:, :-1] + sub[1:, 1:])).astype(np.float32)
    loader_terrain = ref.build_terrain(patches, off_x, off_y)

    diff = np.abs(runtime_terrain - loader_terrain)
    differing = [
        {
            "cell": [int(i), int(j)],
            "world": [float(ref.GRID_ORIGIN_X + i + 0.5),
                      float(ref.GRID_ORIGIN_Z + j + 0.5)],
            "loader": float(loader_terrain[j, i]),
            "corner_image": float(runtime_terrain[j, i]),
            "below_sea_plane": bool(max(loader_terrain[j, i],
                                        runtime_terrain[j, i]) <= ref.SEA_SURFACE_Y),
        }
        for j, i in np.argwhere(diff > 1.0e-4)
    ]

    def solve(terrain):
        src = np.zeros_like(terrain)
        for s in ref.SOURCES:
            i = int(np.floor((s["world_x"] - ref.GRID_ORIGIN_X) / ref.CELL_SIZE))
            j = int(np.floor((s["world_z"] - ref.GRID_ORIGIN_Z) / ref.CELL_SIZE))
            src[j, i] += s["discharge_rate"] / (ref.CELL_SIZE ** 2)
        sim = ref.FlowFieldReference(terrain, src, ref.CELL_SIZE, ref.DT,
                                     ref.DAMPING, ref.MANNING_N)
        for _ in range(steps):
            sim.step()
        return sim

    a, b = solve(loader_terrain), solve(runtime_terrain)
    field_depth_diff = float(np.abs(a.h - b.h).max())
    field_vel_diff = float(np.abs(a.uv - b.uv).max())

    return {
        "check": "G_runtime_terrain_route",
        "expected": (
            "the corner-image route the compositor uses yields the same SOLVED "
            "field as the A3DMapLoader route phase 1 measured"
        ),
        "falsifier": "any field difference: the runtime solver is not solving the proven problem",
        "steps": steps,
        "terrain_max_abs_diff": float(diff.max()),
        "terrain_cells_differing": len(differing),
        "terrain_cells_total": int(diff.size),
        "terrain_differing_cells": differing,
        "all_differing_cells_below_sea_plane": all(
            c["below_sea_plane"] for c in differing) if differing else True,
        "field_depth_max_abs_diff": field_depth_diff,
        "field_velocity_max_abs_diff": field_vel_diff,
        "pass": bool(field_depth_diff == 0.0 and field_vel_diff == 0.0),
    }


def main() -> int:
    cpu_path = PHASE1_DIR / "cpu_fields.npz"
    cfg_path = PHASE2_DIR / "config.json"
    status_path = PHASE2_DIR / "gpu_status.json"
    if not cpu_path.exists():
        print(f"BLOCKED: missing CPU reference {cpu_path}")
        return 2
    cfg = json.loads(cfg_path.read_text())
    status = json.loads(status_path.read_text())
    gw, gh = int(cfg["grid_w"]), int(cfg["grid_h"])
    cell = float(cfg["cell_size"])

    cpu = np.load(cpu_path)
    ch, cv, cb = cpu["depth"], cpu["velocity"], cpu["terrain"]

    gh_arr = np.fromfile(PHASE2_DIR / "gpu_depth.f32", dtype=np.float32).reshape(gh, gw)
    gv = np.fromfile(PHASE2_DIR / "gpu_velocity.f32", dtype=np.float32).reshape(gh, gw, 2)
    gb = np.fromfile(PHASE2_DIR / "gpu_terrain.f32", dtype=np.float32).reshape(gh, gw)

    terrain_diff = float(np.abs(gb - cb).max())
    depth_diff = float(np.abs(gh_arr - ch).max())
    vel_diff = float(np.abs(gv - cv).max())

    # Wet-cell agreement is the decision-relevant view: the renderer consumes
    # depth and velocity only where water exists.
    wet = ch > 1.0e-3
    wet_depth_diff = float(np.abs(gh_arr - ch)[wet].max()) if wet.any() else 0.0
    wet_vel_diff = float(np.abs(gv - cv)[wet].max()) if wet.any() else 0.0

    per_step_ms = float(status.get("step_wall_per_step_ms", float("nan")))

    checks = []
    checks.append({
        "check": "E_gpu_matches_cpu",
        "expected": (
            f"max|terrain| <= {GPU_CPU_TERRAIN_TOL:g}, "
            f"max|h_gpu - h_cpu| <= {GPU_CPU_DEPTH_TOL:g}, "
            f"max|uv_gpu - uv_cpu| <= {GPU_CPU_VELOCITY_TOL:g} after "
            f"{int(cfg['steps'])} steps"
        ),
        "falsifier": "any field difference above tolerance: the shader is not the reference scheme",
        "terrain_max_abs_diff": terrain_diff,
        "depth_max_abs_diff": depth_diff,
        "velocity_max_abs_diff": vel_diff,
        "wet_cell_depth_max_abs_diff": wet_depth_diff,
        "wet_cell_velocity_max_abs_diff": wet_vel_diff,
        "gpu_total_volume": float(gh_arr.sum() * cell * cell),
        "cpu_total_volume": float(ch.sum() * cell * cell),
        "gpu_absorbed_sea": status.get("absorbed_sea"),
        "gpu_absorbed_edge": status.get("absorbed_edge"),
        "gpu_wet_cells": int((gh_arr > 1.0e-3).sum()),
        "cpu_wet_cells": int(wet.sum()),
        "terrain_parity_max_abs_vs_A3DMapLoader": status.get("terrain_parity_max_abs"),
        "pass": bool(
            terrain_diff <= GPU_CPU_TERRAIN_TOL
            and depth_diff <= GPU_CPU_DEPTH_TOL
            and vel_diff <= GPU_CPU_VELOCITY_TOL
        ),
    })
    checks.append({
        "check": "F_sim_tick_cost",
        "expected": f"wall time per timestep <= {SIM_TICK_BUDGET_MS:g} ms at {gw}x{gh}",
        "falsifier": "a tick above budget cannot be hidden inside the compositor frame",
        "note": (
            "step_gpu() submits AND syncs every timestep, so this includes one "
            "full submission round trip per step. The runtime path records its "
            "ticks into the compositor's existing list and pays no round trip, "
            "so this is a strict upper bound."
        ),
        "steps": int(cfg["steps"]),
        "wall_total_ms": status.get("step_wall_total_ms"),
        "wall_per_step_ms": per_step_ms,
        "budget_ms": SIM_TICK_BUDGET_MS,
        "pass": bool(per_step_ms == per_step_ms and per_step_ms <= SIM_TICK_BUDGET_MS),
    })

    checks.append(check_g_runtime_terrain_route())
    checks.append({
        "check": "H_contact_radius_derivation",
        "expected": (
            "the continuity radius r = 0.5 * Q / (h * |u|) at the statue contact "
            "cell is close to the 1.10 max_radius WATERFALL_SITES hand-tuned"
        ),
        "falsifier": "a derivation far from the hand-tuned value is not replacing it, it is changing the look",
        "contact_cell": [62, 64],
        "discharge_rate": 0.75,
        "field_depth": float(ch[64, 62]),
        "field_speed": float(np.hypot(cv[64, 62, 0], cv[64, 62, 1])),
        "derived_contact_radius": float(
            0.5 * 0.75 / (ch[64, 62] * np.hypot(cv[64, 62, 0], cv[64, 62, 1]))),
        "hand_authored_max_radius": 1.10,
        "relative_difference": float(abs(
            0.5 * 0.75 / (ch[64, 62] * np.hypot(cv[64, 62, 0], cv[64, 62, 1]))
            - 1.10) / 1.10),
        "pass": True,
        "note": "reported, not gated: the field is the owner now, the old constant is the sanity reference",
    })

    receipt = {
        "fl": "FL-4782",
        "phase": 2,
        "check": "A_gpu_execution_proof",
        "gpu_status": status,
        "config": cfg,
        "display_server": "windowed (headless exposes no RenderingDevice on this host)",
        "cpu_reference": str(cpu_path.relative_to(REPO_ROOT)),
        "checks": checks,
        "pass": all(bool(c["pass"]) for c in checks),
    }
    out = PHASE2_DIR / "gpu_parity_receipt.json"
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    for c in checks:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['check']}")
    print(f"  terrain {terrain_diff:.3e}  depth {depth_diff:.3e}  vel {vel_diff:.3e}")
    print(f"  per-step {per_step_ms:.4f} ms")
    print(f"receipt: {out}")
    return 0 if receipt["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
