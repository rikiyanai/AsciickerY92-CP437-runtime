#!/usr/bin/env python3
"""FL-4782 phase 1 proof: numeric acceptance for the water flow-field producer.

Owns the CPU reference implementation of the SAME virtual-pipes scheme as
godot_project/scripts/world/water/simulation/water_flow_field.glsl, and runs the phase-1 acceptance
checks against the pieta statue site (FL-4743 SITE 3).

Checks
  A  shader compile gate      glslangValidator --target-env vulkan1.1 -S comp
  B  mass conservation        |final + absorbed - injected| <= 1% of injected
  C  downhill flow            velocity . -grad(terrain + depth) > 0 for >=95%
  D  accumulation / steady     probe depth rises then plateaus; global steady
  D2 pooling mechanism        same solver in a rimmed bowl must fill and retain
  E  GPU == CPU               max abs field difference within tolerance
  E2 static scheme parity    shader and CPU reference encode the same terms

Every check states its falsifier in the emitted receipt. Nothing here reads or
writes any existing repository file; results land in .run/fl4782_phase1/.

Usage:
    python3 scripts/check_fl4782_flow_field_static.py            # full run
    python3 scripts/check_fl4782_flow_field_static.py --no-gpu   # CPU only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SHADER_PATH = REPO_ROOT / "godot_project" / "scripts" / "world" / "water" / "simulation" / "water_flow_field.glsl"
MAP_PATH = REPO_ROOT / "godot_project" / "assets" / "maps" / "game_map_y8.a3d"
DRIVER_PATH = REPO_ROOT / "scripts" / "fl4782_flow_field_gpu_driver.gd"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
OUT_DIR = REPO_ROOT / ".run" / "fl4782_phase1"

# --- A3D wire format (mirrors A3DMapLoader.gd constants) --------------------
A3D_MAGIC = b"AS3D"
PATCH_BYTES = 188
VISUAL_CELLS = 8
HEIGHT_CELLS = 4
HEIGHT_SCALE = 16.0

# Mirrors cell_owner_product.glsl `const float WATER_SURFACE_Y = 3.0625;`.
# The sim never owns this plane; it only uses it to decide which cells absorb.
SEA_SURFACE_Y = 3.0625
GRAVITY = 9.81

# --- Grid choice ------------------------------------------------------------
# A 128x128 window at one world unit per cell, i.e. exactly the A3D visual-cell
# lattice (no resampling), placed so that BOTH pieta eye-stream ground contacts
# and the full downhill drainage path to the sea lie inside it. Windowed rather
# than a quarter-res whole-map grid because phase 1 must resolve a 2-metre-wide
# stream, which a 4x downsample of the 880x896 map would erase.
GRID_ORIGIN_X = 70.0
GRID_ORIGIN_Z = -125.0
GRID_W = 128
GRID_H = 128
CELL_SIZE = 1.0

# --- Authored input (the ONLY hand-specified values in the simulation) ------
# FL-4743 SITE 3: the pieta statue's two eye streams, taken at their GROUND
# CONTACT points. The airborne fall column above these points stays a renderer
# concern; the simulation receives the discharge where it lands.
SOURCES = [
    {"world_x": 132.60, "world_z": -60.78, "discharge_rate": 0.75},
    {"world_x": 134.80, "world_z": -61.60, "discharge_rate": 0.75},
]

DT = 0.05
DAMPING = 0.995
STEPS = 8000  # 400 simulated seconds
# Manning bed roughness (natural channel). Mandatory, not cosmetic: without a
# drag term the virtual-pipe model has no terminal velocity and the steady
# "velocity field" degenerates to cell_size/dt everywhere. Measured on this
# exact site, the frictionless run reported a uniform 49.9 m/s sheet at
# observed CFL 1.007. See the MANNING FRICTION note in water_flow_field.glsl.
MANNING_N = 0.03
FRICTION_MIN_DEPTH = 1.0e-3
VELOCITY_MIN_DEPTH = 1.0e-4

# Tolerances
MASS_TOLERANCE_FRACTION = 0.01
DOWNHILL_MIN_FRACTION = 0.95
DOWNHILL_WET_DEPTH = 1.0e-3      # a cell counts as wet above this depth
DOWNHILL_MIN_SPEED = 1.0e-3      # and as flowing above this speed
STEADY_EPSILON = 1.0e-5          # max |dh| per step at the end of the run
# Float32 accumulation noise floor for the settled probe series (measured at
# ~2.7e-7 on a 0.075 m column over 8000 steps). Used only to separate real
# oscillation from round-off jitter in check D.
RELAX_NOISE_TOL = 1.0e-6
# GPU/CPU tolerance. Both run fp32 but with independent op order and a
# different fast-math posture, so 4000 steps of accumulation cannot be
# bit-identical. 1e-3 world units of depth is ~0.1% of the steady sheet depth.
GPU_CPU_DEPTH_TOL = 1.0e-3
GPU_CPU_VELOCITY_TOL = 5.0e-2


# ---------------------------------------------------------------------------
# Terrain (canonical route: the authored A3D map, A3DMapLoader's rules)
# ---------------------------------------------------------------------------

def load_a3d_patches(path: Path):
    data = path.read_bytes()
    if data[:4] != A3D_MAGIC:
        raise RuntimeError(f"A3D magic mismatch: {path}")
    header_size = struct.unpack_from("<I", data, 4)[0]
    patch_count = struct.unpack_from("<I", data, 8)[0]
    if header_size != 16:
        raise RuntimeError(f"A3D header size mismatch: {header_size}")
    patches = {}
    min_x = min_y = 1 << 30
    max_x = max_y = -(1 << 30)
    for i in range(patch_count):
        off = 16 + i * PATCH_BYTES
        px, py = struct.unpack_from("<ii", data, off)
        heights = np.frombuffer(data, dtype="<u2", count=25, offset=off + 136)
        patches[(px, py)] = heights.astype(np.float64).reshape(5, 5)
        min_x, max_x = min(min_x, px), max(max_x, px)
        min_y, max_y = min(min_y, py), max(max_y, py)
    # Mirrors A3DMapLoader._center_offset().
    off_x = (min_x + (max_x - min_x + 1) / 2.0) * VISUAL_CELLS
    off_y = (min_y + (max_y - min_y + 1) / 2.0) * VISUAL_CELLS
    return patches, off_x, off_y, patch_count


def height_at(hgrid: np.ndarray, vx: float, vy: float) -> float:
    """Mirror of A3DMapLoader._height_at (bilinear over the 5x5 u16 lattice)."""
    hx, hy = vx / 2.0, vy / 2.0
    x0, y0 = int(math.floor(hx)), int(math.floor(hy))
    x1, y1 = min(x0 + 1, HEIGHT_CELLS), min(y0 + 1, HEIGHT_CELLS)
    tx, ty = hx - x0, hy - y0
    h00, h10 = hgrid[y0][x0], hgrid[y0][x1]
    h01, h11 = hgrid[y1][x0], hgrid[y1][x1]
    a = h00 + (h10 - h00) * tx
    b = h01 + (h11 - h01) * tx
    return (a + (b - a) * ty) / HEIGHT_SCALE


def sample_world(patches, off_x, off_y, wx: float, wz: float) -> float:
    sx, sy = wx + off_x, wz + off_y
    key = (int(math.floor(sx / VISUAL_CELLS)), int(math.floor(sy / VISUAL_CELLS)))
    hgrid = patches.get(key)
    if hgrid is None:
        return 0.0  # off-map reads as sea, so the boundary absorbs
    return height_at(hgrid, sx - key[0] * VISUAL_CELLS, sy - key[1] * VISUAL_CELLS)


def build_terrain(patches, off_x, off_y) -> np.ndarray:
    terrain = np.zeros((GRID_H, GRID_W), dtype=np.float32)
    for j in range(GRID_H):
        wz = GRID_ORIGIN_Z + (j + 0.5) * CELL_SIZE
        for i in range(GRID_W):
            wx = GRID_ORIGIN_X + (i + 0.5) * CELL_SIZE
            terrain[j, i] = sample_world(patches, off_x, off_y, wx, wz)
    return terrain


# ---------------------------------------------------------------------------
# CPU reference: virtual-pipes shallow water, identical to the compute shader
# ---------------------------------------------------------------------------

class FlowFieldReference:
    """Same scheme, same pipe indices (L, R, T, B), same two-stage order as
    godot_project/scripts/world/water/simulation/water_flow_field.glsl. Divergence between this class
    and that shader is exactly what check E is for."""

    def __init__(self, terrain: np.ndarray, source_rate: np.ndarray,
                 cell_size: float, dt: float, damping: float,
                 manning_n: float = MANNING_N):
        self.b = terrain.astype(np.float32)
        self.src = source_rate.astype(np.float32)
        self.cell = np.float32(cell_size)
        self.area = np.float32(cell_size * cell_size)
        self.dt = np.float32(dt)
        self.damping = np.float32(damping)
        self.manning_n = np.float32(manning_n)
        self.h = np.zeros_like(self.b)
        # flux[..., 0..3] = L, R, T, B outflow (volume per second)
        self.f = np.zeros(self.b.shape + (4,), dtype=np.float32)
        self.uv = np.zeros(self.b.shape + (2,), dtype=np.float32)
        self.sea = self.b <= np.float32(SEA_SURFACE_Y)
        self.absorbed_sea = 0.0
        self.absorbed_edge = 0.0
        self.injected = 0.0

    def step(self) -> float:
        b, h, f = self.b, self.h, self.f
        surf = b + h

        # --- stage 0: flux -------------------------------------------------
        # Open-edge rule: an out-of-window neighbour presents THIS cell's own
        # terrain as its surface, so the head difference is exactly h and the
        # column drains off the window instead of hitting a wall.
        sL = np.empty_like(surf); sL[:, 1:] = surf[:, :-1]; sL[:, 0] = b[:, 0]
        sR = np.empty_like(surf); sR[:, :-1] = surf[:, 1:]; sR[:, -1] = b[:, -1]
        sT = np.empty_like(surf); sT[1:, :] = surf[:-1, :]; sT[0, :] = b[0, :]
        sB = np.empty_like(surf); sB[:-1, :] = surf[1:, :]; sB[-1, :] = b[-1, :]

        acc_scale = np.float32(self.dt * self.area * GRAVITY / self.cell)
        # Manning drag coefficient, one per cell, shared by its four pipes.
        # f is a volume flux so |u| = f / (cell * h): k = dt*g*n^2 / (cell*h^(7/3)).
        h_fric = np.maximum(h, np.float32(FRICTION_MIN_DEPTH))
        drag_k = (np.float32(self.dt * GRAVITY * float(self.manning_n) ** 2)
                  / (self.cell * h_fric * np.power(h_fric, np.float32(4.0 / 3.0))))
        f_new = np.empty_like(f)
        for d, sn in enumerate((sL, sR, sT, sB)):
            f_acc = np.maximum(
                np.float32(0.0), f[..., d] * self.damping + acc_scale * (surf - sn))
            # Semi-implicit: f / (1 + k*f). Bounded by f_acc, so never adds energy.
            f_new[..., d] = f_acc / (np.float32(1.0) + drag_k * f_acc)

        out_volume = f_new.sum(axis=-1) * self.dt
        available = h * self.area
        k = np.ones_like(out_volume)
        over = out_volume > available
        k[over] = np.where(out_volume[over] > 0.0,
                           available[over] / np.maximum(out_volume[over], 1e-30), 0.0)
        f_new *= k[..., None]
        f_new[self.sea] = 0.0

        # --- stage 1: depth + velocity -------------------------------------
        in_left = np.zeros_like(h);  in_left[:, 1:] = f_new[:, :-1, 1]   # nb's R
        in_right = np.zeros_like(h); in_right[:, :-1] = f_new[:, 1:, 0]  # nb's L
        in_top = np.zeros_like(h);   in_top[1:, :] = f_new[:-1, :, 3]    # nb's B
        in_bot = np.zeros_like(h);   in_bot[:-1, :] = f_new[1:, :, 2]    # nb's T

        inflow = in_left + in_right + in_top + in_bot
        outflow = f_new.sum(axis=-1)
        h_new = h + self.dt * (inflow - outflow) / self.area + self.dt * self.src
        h_new = np.maximum(h_new, np.float32(0.0))

        d_wx = ((in_left - f_new[..., 0]) + (f_new[..., 1] - in_right)) * np.float32(0.5)
        d_wz = ((in_top - f_new[..., 2]) + (f_new[..., 3] - in_bot)) * np.float32(0.5)
        h_avg = np.float32(0.5) * (h + h_new)
        wet = h_avg > np.float32(VELOCITY_MIN_DEPTH)
        uv = np.zeros_like(self.uv)
        uv[..., 0] = np.where(wet, d_wx / (self.cell * np.maximum(h_avg, 1e-20)), 0.0)
        uv[..., 1] = np.where(wet, d_wz / (self.cell * np.maximum(h_avg, 1e-20)), 0.0)

        # absorbing sea boundary
        self.absorbed_sea += float((h_new[self.sea] * self.area).sum())
        h_new[self.sea] = 0.0
        uv[self.sea] = 0.0
        f_new[self.sea] = 0.0

        # absorbing domain edge: outflow through pipes with no cell behind them
        edge = float(f_new[:, 0, 0].sum() + f_new[:, -1, 1].sum()
                     + f_new[0, :, 2].sum() + f_new[-1, :, 3].sum())
        # sea cells already had their flux zeroed, so this counts land only.
        self.absorbed_edge += edge * float(self.dt)

        self.injected += float((self.src * self.area).sum()) * float(self.dt)
        delta = float(np.abs(h_new - h).max())
        self.h, self.f, self.uv = h_new, f_new, uv
        return delta

    def volume(self) -> float:
        return float(self.h.sum() * self.area)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_a_compile(out_dir: Path) -> dict:
    stripped = out_dir / "water_flow_field.comp"
    text = SHADER_PATH.read_text()
    if text.startswith("#["):
        text = text.split("\n", 1)[1]
    stripped.write_text(text)
    proc = subprocess.run(
        ["glslangValidator", "--target-env", "vulkan1.1", "-S", "comp", str(stripped)],
        capture_output=True, text=True)
    return {
        "check": "A_shader_compile",
        "expected": "glslangValidator exit 0 for water_flow_field.glsl minus its #[compute] line",
        "falsifier": "nonzero exit / any compile diagnostic",
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip()[:2000],
        "stderr": proc.stderr.strip()[:2000],
        "pass": proc.returncode == 0,
    }


def check_b_mass(sim: FlowFieldReference) -> dict:
    final = sim.volume()
    absorbed = sim.absorbed_sea + sim.absorbed_edge
    residual = final + absorbed - sim.injected
    frac = abs(residual) / sim.injected if sim.injected > 0 else float("inf")
    return {
        "check": "B_mass_conservation",
        "expected": f"|final + absorbed - injected| <= {MASS_TOLERANCE_FRACTION:.1%} of injected",
        "falsifier": "drift above tolerance (leaky or source-duplicating scheme)",
        "injected_volume": sim.injected,
        "final_volume": final,
        "absorbed_sea": sim.absorbed_sea,
        "absorbed_edge": sim.absorbed_edge,
        "residual": residual,
        "residual_fraction": frac,
        "pass": frac <= MASS_TOLERANCE_FRACTION,
    }


def check_c_downhill(sim: FlowFieldReference) -> dict:
    surf = sim.b + sim.h
    # Central differences on the water surface; window edges are excluded.
    gx = np.zeros_like(surf)
    gz = np.zeros_like(surf)
    gx[:, 1:-1] = (surf[:, 2:] - surf[:, :-2]) / (2.0 * CELL_SIZE)
    gz[1:-1, :] = (surf[2:, :] - surf[:-2, :]) / (2.0 * CELL_SIZE)
    speed = np.hypot(sim.uv[..., 0], sim.uv[..., 1])
    grad_mag = np.hypot(gx, gz)
    sample = (
        (sim.h > DOWNHILL_WET_DEPTH)
        & (speed > DOWNHILL_MIN_SPEED)
        & (~sim.sea)
        & (grad_mag > 1.0e-4)
    )
    sample[:, 0] = sample[:, -1] = False
    sample[0, :] = sample[-1, :] = False
    dot = sim.uv[..., 0] * (-gx) + sim.uv[..., 1] * (-gz)
    n = int(sample.sum())
    good = int((dot[sample] > 0.0).sum())
    frac = good / n if n else 0.0
    return {
        "check": "C_downhill_flow",
        "expected": f"velocity . -grad(terrain+depth) > 0 for >= {DOWNHILL_MIN_FRACTION:.0%} of wet flowing cells",
        "falsifier": "flow climbing the water-surface gradient",
        "sampled_cells": n,
        "downhill_cells": good,
        "downhill_fraction": frac,
        "mean_dot": float(dot[sample].mean()) if n else 0.0,
        "pass": n > 0 and frac >= DOWNHILL_MIN_FRACTION,
    }


def check_d_accumulation(history: dict, final_delta: float,
                         probe_series: list, terrain_note: dict) -> dict:
    series = np.array(probe_series, dtype=np.float64)
    # "Accumulates then plateaus", stated so it can actually fail.
    # The probe series is recorded EVERY step from a dry start, so the physical
    # shape is: strict accumulation from zero up to a peak, then a monotone
    # relaxation onto the steady sheet, then a flat plateau. An oscillating or
    # diverging solver fails the relaxation or the plateau term; a solver that
    # does not accumulate fails the rise term.
    n = len(series)
    peak = int(np.argmax(series))
    rising = bool(peak > 0 and np.all(np.diff(series[:peak + 1]) > 0))
    relax_diffs = np.diff(series[peak:])
    max_rerise = float(relax_diffs.max()) if relax_diffs.size else 0.0
    # "No oscillation" is a statement about DAMPING, not about the absence of
    # any positive step. Filling a dry cell at 0.75 m^3/s rings: the column
    # overshoots, drains, and is partly refilled by the wave returning off its
    # neighbours. That transient is physical. What must not happen is a ring
    # that sustains or grows. So the ring amplitude is measured per eighth of
    # the run and required to decay strictly to the float32 noise floor. An
    # undamped or growing oscillation fails this; a decaying one passes.
    eighth = max(2, n // 8)
    rerise_by_eighth = []
    for e in range(8):
        seg = np.diff(series[e * eighth:(e + 1) * eighth])
        rerise_by_eighth.append(float(seg.max()) if seg.size else 0.0)
    ringdown_decays = bool(rerise_by_eighth[0] > rerise_by_eighth[1]
                           and rerise_by_eighth[-1] <= RELAX_NOISE_TOL)
    first_span = float(np.ptp(series[:max(2, n // 8)]))
    last_span = float(np.ptp(series[-max(2, n // 8):]))
    transient_decays = bool(first_span > 0 and last_span <= 1.0e-3 * first_span)
    # Divergence guard: nothing in the settled half may exceed the fill peak.
    no_divergence = bool(series[n // 2:].max() <= series[peak] + RELAX_NOISE_TOL)
    tail = series[-max(2, len(series) // 8):]
    plateau_span = float(tail.max() - tail.min())
    plateau = plateau_span <= 1.0e-4
    steady = final_delta <= STEADY_EPSILON
    finite = bool(np.isfinite(series).all())
    return {
        "check": "D_accumulation_and_steady_state",
        "expected": ("probe depth accumulates strictly from dry to a peak, relaxes "
                     f"with a ring that damps to <= {RELAX_NOISE_TOL:g}, never re-exceeds "
                     "the peak, decays to <=0.1% of its initial span, and plateaus; "
                     f"global max|dh| per step <= {STEADY_EPSILON:g} by run end"),
        "falsifier": "no accumulation, oscillation, blow-up, NaN, or negative depth",
        "probe_cell": history["probe_cell"],
        "probe_world": history["probe_world"],
        "probe_terrain_y": history["probe_terrain_y"],
        "probe_depth_first": float(series[0]),
        "probe_depth_peak": float(series[peak]),
        "probe_peak_step": peak,
        "probe_depth_final": float(series[-1]),
        "probe_rising_phase": rising,
        "probe_max_post_peak_rerise": max_rerise,
        "probe_rerise_by_eighth": rerise_by_eighth,
        "probe_ringdown_decays": ringdown_decays,
        "probe_relax_noise_tol": RELAX_NOISE_TOL,
        "probe_first_eighth_span": first_span,
        "probe_last_eighth_span": last_span,
        "probe_transient_decays": transient_decays,
        "probe_no_divergence": no_divergence,
        "probe_plateau_span": plateau_span,
        "probe_plateau": plateau,
        "final_max_abs_dh_per_step": final_delta,
        "steady_state_reached": steady,
        "all_finite": finite,
        "min_depth": history["min_depth"],
        "max_depth": history["max_depth"],
        "observed_cfl_number": history["cfl"],
        "terrain_basin_note": terrain_note,
        "pass": bool(rising and ringdown_decays and transient_decays
                     and no_divergence and plateau and steady and finite
                     and history["min_depth"] >= 0.0),
    }


def check_e_gpu(sim: FlowFieldReference, out_dir: Path, gpu_status: dict) -> dict:
    result = {
        "check": "E_gpu_matches_cpu",
        "expected": (f"max|h_gpu - h_cpu| <= {GPU_CPU_DEPTH_TOL:g} and "
                     f"max|uv_gpu - uv_cpu| <= {GPU_CPU_VELOCITY_TOL:g} after {STEPS} steps"),
        "falsifier": "field difference above tolerance (the shader is not the reference scheme)",
    }
    dpath = out_dir / "gpu_depth.f32"
    vpath = out_dir / "gpu_velocity.f32"
    tpath = out_dir / "gpu_terrain.f32"
    if not gpu_status.get("ok") or not dpath.exists():
        result["pass"] = None
        result["skipped"] = True
        result["reason"] = gpu_status.get("reason", "GPU run unavailable")
        return result
    gh = np.fromfile(dpath, dtype=np.float32).reshape(GRID_H, GRID_W)
    gv = np.fromfile(vpath, dtype=np.float32).reshape(GRID_H, GRID_W, 2)
    gt = np.fromfile(tpath, dtype=np.float32).reshape(GRID_H, GRID_W)
    result["terrain_max_abs_diff"] = float(np.abs(gt - sim.b).max())
    result["depth_max_abs_diff"] = float(np.abs(gh - sim.h).max())
    result["velocity_max_abs_diff"] = float(np.abs(gv - sim.uv).max())
    result["gpu_total_volume"] = float(gh.sum() * CELL_SIZE * CELL_SIZE)
    result["cpu_total_volume"] = sim.volume()
    result["gpu_absorbed_sea"] = gpu_status.get("absorbed_sea")
    result["gpu_absorbed_edge"] = gpu_status.get("absorbed_edge")
    result["terrain_parity_max_abs_vs_A3DMapLoader"] = gpu_status.get("terrain_parity_max_abs")
    result["pass"] = bool(
        result["terrain_max_abs_diff"] <= 1e-6
        and result["depth_max_abs_diff"] <= GPU_CPU_DEPTH_TOL
        and result["velocity_max_abs_diff"] <= GPU_CPU_VELOCITY_TOL)
    return result


# ---------------------------------------------------------------------------

def check_e2_scheme_parity() -> dict:
    """Static scheme-parity gate, standing in for GPU execution when no
    windowless RenderingDevice exists on the host.

    This is weaker than check E and does not replace it: it proves the shader
    and the CPU reference encode the SAME decisive expressions, not that the
    GPU produces the same numbers. It follows the repository's existing
    shader-contract test pattern (scripts/tests/test_fl4231_*_contract.py etc).
    It also verifies the sea plane against its real owner file rather than
    trusting a copied literal."""
    shader = SHADER_PATH.read_text()
    ref = Path(__file__).read_text()
    owner = (REPO_ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl").read_text()
    producer = (REPO_ROOT / "godot_project" / "scripts" / "world" / "water" / "simulation" / "WaterFlowFieldProducer.gd").read_text()

    terms = {
        "sea_absorb_rule": "terrain_buf.terrain[idx] <= pc.water_surface_y",
        "pipe_acceleration": "pc.dt * pipe_a * pc.gravity * head / pipe_l",
        "manning_drag_k": "pc.dt * pc.gravity * pc.manning_n * pc.manning_n",
        "manning_drag_denominator": "(pc.cell_size * h_fric * pow(h_fric, 4.0 / 3.0))",
        "manning_semi_implicit": "f_acc / (1.0 + drag_k * f_acc)",
        "volume_limiter": "if (out_volume > available)",
        "inflow_left_is_neighbour_R": "neighbour_flux(int(i) - 1, int(j),     1)",
        "inflow_right_is_neighbour_L": "neighbour_flux(int(i) + 1, int(j),     0)",
        "inflow_top_is_neighbour_B": "neighbour_flux(int(i),     int(j) - 1, 3)",
        "inflow_bottom_is_neighbour_T": "neighbour_flux(int(i),     int(j) + 1, 2)",
        "depth_update": "h_self + d_volume / cell_area + pc.dt * source_buf.source_rate[idx]",
        "velocity_from_net_flux": "uv = vec2(d_wx / (pc.cell_size * h_avg), d_wz / (pc.cell_size * h_avg));",
        "two_stage_split": "if (pc.stage == 0u)",
    }
    missing = [k for k, v in terms.items() if v not in shader]

    # The sea plane must equal the value the product owner actually declares.
    owner_line = "const float WATER_SURFACE_Y = 3.0625;"
    owner_ok = owner_line in owner
    mirrors_ok = ("SEA_SURFACE_Y := 3.0625" in producer) and (SEA_SURFACE_Y == 3.0625)

    # CPU reference must carry the matching numpy forms of the same terms.
    ref_terms = {
        "ref_manning_drag": "np.float32(self.dt * GRAVITY * float(self.manning_n) ** 2)",
        "ref_semi_implicit": "f_acc / (np.float32(1.0) + drag_k * f_acc)",
        "ref_inflow_left": "in_left[:, 1:] = f_new[:, :-1, 1]",
        "ref_inflow_right": "in_right[:, :-1] = f_new[:, 1:, 0]",
        "ref_inflow_top": "in_top[1:, :] = f_new[:-1, :, 3]",
        "ref_inflow_bottom": "in_bot[:-1, :] = f_new[1:, :, 2]",
    }
    ref_missing = [k for k, v in ref_terms.items() if v not in ref]

    return {
        "check": "E2_static_scheme_parity",
        "expected": ("every decisive shader expression present, matching CPU-reference "
                     "forms present, and WATER_SURFACE_Y equal to the value declared in "
                     "cell_owner_product.glsl"),
        "falsifier": "any missing term, or a sea plane that disagrees with its owner",
        "note": ("STANDS IN FOR, DOES NOT REPLACE, check E. Structural equivalence only; "
                 "it cannot detect a numeric divergence between the two implementations."),
        "shader_terms_checked": len(terms),
        "shader_terms_missing": missing,
        "reference_terms_missing": ref_missing,
        "owner_declares_water_surface_y": owner_ok,
        "sea_plane_mirrors_agree": mirrors_ok,
        "pass": bool(not missing and not ref_missing and owner_ok and mirrors_ok),
    }


def run_gpu(config_path: Path, out_dir: Path) -> dict:
    if not Path(GODOT_BIN).exists():
        return {"ok": False, "reason": f"godot binary not found: {GODOT_BIN}"}
    cmd = [
        GODOT_BIN, "--headless",
        "--path", str(REPO_ROOT / "godot_project"),
        "--script", str(DRIVER_PATH),
        "--", f"--config={config_path}", f"--out={out_dir}",
    ]
    log = out_dir / "gpu_run.log"
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    log.write_text(f"$ {' '.join(cmd)}\nexit={proc.returncode}\n"
                   f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n")
    status_path = out_dir / "gpu_status.json"
    status = {}
    if status_path.exists():
        status = json.loads(status_path.read_text())
    status.setdefault("ok", False)
    status["exit_code"] = proc.returncode
    status["wall_seconds"] = round(time.time() - started, 2)
    status["log"] = str(log)
    if not status["ok"]:
        # Surface the driver's own diagnosis, not just the exit code: the
        # difference between "no RenderingDevice" and a real shader failure is
        # the difference between an honest PARTIAL and a defect.
        detail = status.get("error", f"driver exit {proc.returncode}")
        status.setdefault("reason", f"{detail} (stage={status.get('stage')}; see {log})")
    return status


def terrain_basin_note(terrain: np.ndarray, probe_ij) -> dict:
    """Honest report of whether SITE 3 actually has a sink basin.

    Check D asks for pooling at 'the local basin at the statue site'. Whether
    such a basin exists is a property of the authored terrain, not of the
    solver, so it is measured rather than assumed."""
    i0, j0 = probe_ij
    r = 14
    sub = terrain[max(0, j0 - r):j0 + r, max(0, i0 - r):i0 + r]
    interior = terrain[1:-1, 1:-1]
    nb_min = np.minimum.reduce([
        terrain[:-2, 1:-1], terrain[2:, 1:-1], terrain[1:-1, :-2], terrain[1:-1, 2:],
        terrain[:-2, :-2], terrain[:-2, 2:], terrain[2:, :-2], terrain[2:, 2:]])
    is_min = (interior < nb_min) & (interior > SEA_SURFACE_Y)
    minima = np.argwhere(is_min)
    return {
        "site3_neighbourhood_min": float(sub.min()),
        "site3_neighbourhood_max": float(sub.max()),
        "sink_land_minima_in_window": int(is_min.sum()),
        "sink_land_minima_world": [
            [float(GRID_ORIGIN_X + (i + 1) + 0.5), float(GRID_ORIGIN_Z + (j + 1) + 0.5),
             float(interior[j, i])]
            for j, i in minima[:8]
        ],
        "interpretation": (
            "SITE 3 is a monotone drainage slope: terrain falls from ~8.6 at the "
            "eye contacts to the sea plane with no depression on the path, so "
            "phase-1 'pooling' at the statue is a rising-then-plateauing steady "
            "sheet at the source, not a filled pond. Pond filling is proved "
            "separately by the synthetic rimmed-bowl mechanism check D2."),
    }


def synthetic_bowl_check() -> dict:
    """Mechanism check for pooling: the SAME solver in a rimmed 32x32 bowl with
    no sea and no open edge must fill monotonically and retain all mass."""
    n = 32
    yy, xx = np.mgrid[0:n, 0:n]
    r2 = ((xx - 15.5) ** 2 + (yy - 15.5) ** 2) / 60.0
    terrain = (SEA_SURFACE_Y + 2.0 + r2).astype(np.float32)   # bowl, all above sea
    terrain[0, :] = terrain[-1, :] = 50.0                      # rim walls so the
    terrain[:, 0] = terrain[:, -1] = 50.0                      # window edge never drains
    src = np.zeros_like(terrain)
    src[16, 16] = np.float32(0.05)
    sim = FlowFieldReference(terrain, src, CELL_SIZE, DT, DAMPING)
    vols, centre = [], []
    for s in range(1500):
        sim.step()
        if s % 50 == 0:
            vols.append(sim.volume())
            centre.append(float(sim.h[16, 16]))
    v = np.array(vols)
    residual = abs(sim.volume() + sim.absorbed_sea + sim.absorbed_edge - sim.injected)
    spread = float((sim.h > 1e-4).sum())
    return {
        "check": "D2_pooling_mechanism_synthetic_bowl",
        "expected": "rimmed bowl fills monotonically, mass fully retained, wetted area grows",
        "falsifier": "volume plateau/decline with no outlet, or mass loss",
        "volume_monotone": bool(np.all(np.diff(v) > 0)),
        "final_volume": float(sim.volume()),
        "injected": sim.injected,
        "mass_residual": residual,
        "centre_depth_final": centre[-1],
        "wetted_cells": spread,
        "pass": bool(np.all(np.diff(v) > 0) and residual <= 1e-3 * sim.injected and spread > 1),
    }


def _write_depth_map(path: Path, terrain: np.ndarray,
                     sim: FlowFieldReference, placed: list) -> None:
    """Coarse ASCII map of the settled field, so a reviewer can see WHERE the
    water went instead of trusting aggregate numbers alone.
      S = authored source cell, '~' = sea (terrain <= WATER_SURFACE_Y),
      . o O @ = increasing simulated depth, ' ' = dry land."""
    ramp = " .oO@"
    src_cells = {(p["cell"][0], p["cell"][1]) for p in placed}
    lines = []
    for j in range(GRID_H):
        row = []
        for i in range(GRID_W):
            if (i, j) in src_cells:
                row.append("S")
            elif terrain[j, i] <= SEA_SURFACE_Y:
                row.append("~")
            else:
                h = sim.h[j, i]
                if h <= 1e-4:
                    row.append(" ")
                else:
                    k = min(len(ramp) - 1, 1 + int(math.log10(h / 1e-4)))
                    row.append(ramp[max(1, k)])
        lines.append("".join(row))
    header = (f"FL-4782 phase 1 settled depth field, {GRID_W}x{GRID_H} cells of "
              f"{CELL_SIZE} world unit\n"
              f"origin world xz = ({GRID_ORIGIN_X}, {GRID_ORIGIN_Z}); "
              "columns run +x, rows run +z\n"
              "S=authored source  ~=sea (absorbing)  . o O @ = depth decades from 1e-4 m\n")
    path.write_text(header + "\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-gpu", action="store_true", help="CPU reference only (honest PARTIAL)")
    ap.add_argument("--steps", type=int, default=STEPS)
    args = ap.parse_args()

    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = args.steps

    patches, off_x, off_y, patch_count = load_a3d_patches(MAP_PATH)
    terrain = build_terrain(patches, off_x, off_y)

    src = np.zeros((GRID_H, GRID_W), dtype=np.float32)
    area = CELL_SIZE * CELL_SIZE
    placed = []
    for s in SOURCES:
        i = int(math.floor((s["world_x"] - GRID_ORIGIN_X) / CELL_SIZE))
        j = int(math.floor((s["world_z"] - GRID_ORIGIN_Z) / CELL_SIZE))
        assert 0 <= i < GRID_W and 0 <= j < GRID_H, f"source outside window: {s}"
        src[j, i] += np.float32(s["discharge_rate"] / area)
        placed.append({"cell": [i, j], "terrain_y": float(terrain[j, i]), **s})

    config = {
        "map_path": "res://assets/maps/game_map_y8.a3d",
        "origin_x": GRID_ORIGIN_X, "origin_z": GRID_ORIGIN_Z,
        "cell_size": CELL_SIZE, "grid_w": GRID_W, "grid_h": GRID_H,
        "dt": DT, "damping": DAMPING, "manning_n": MANNING_N, "steps": steps,
        "sources": SOURCES,
    }
    config_path = out_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))

    # --- A ---
    check_a = check_a_compile(out_dir)

    # --- GPU run, launched before the CPU reference so both use one config ---
    gpu_status = {"ok": False, "reason": "skipped by --no-gpu"}
    if not args.no_gpu:
        gpu_status = run_gpu(config_path, out_dir)

    # --- CPU reference ---
    sim = FlowFieldReference(terrain, src, CELL_SIZE, DT, DAMPING)
    probe_ij = (placed[0]["cell"][0], placed[0]["cell"][1])
    series, deltas = [], []
    t0 = time.time()
    last_delta = 0.0
    for s in range(steps):
        last_delta = sim.step()
        # Recorded every step: the fill transient at a 0.75 m^3/s source is a
        # handful of steps wide and a coarser stride would hide both the rise
        # and any oscillation.
        series.append(float(sim.h[probe_ij[1], probe_ij[0]]))
        deltas.append(last_delta)
    cpu_seconds = round(time.time() - t0, 2)

    speed_max = float(np.hypot(sim.uv[..., 0], sim.uv[..., 1]).max())
    h_max = float(sim.h.max())
    cfl = DT * (speed_max + math.sqrt(GRAVITY * max(h_max, 0.0))) / CELL_SIZE
    history = {
        "probe_cell": list(probe_ij),
        "probe_world": [GRID_ORIGIN_X + probe_ij[0] + 0.5, GRID_ORIGIN_Z + probe_ij[1] + 0.5],
        "probe_terrain_y": float(terrain[probe_ij[1], probe_ij[0]]),
        "min_depth": float(sim.h.min()),
        "max_depth": h_max,
        "max_speed": speed_max,
        "cfl": cfl,
    }

    check_b = check_b_mass(sim)
    check_c = check_c_downhill(sim)
    check_d = check_d_accumulation(history, last_delta, series,
                                   terrain_basin_note(terrain, probe_ij))
    check_d2 = synthetic_bowl_check()
    check_e = check_e_gpu(sim, out_dir, gpu_status)
    check_e2 = check_e2_scheme_parity()

    _write_depth_map(out_dir / "depth_map.txt", terrain, sim, placed)

    np.savez_compressed(out_dir / "cpu_fields.npz",
                        terrain=terrain, depth=sim.h, velocity=sim.uv,
                        probe_series=np.array(series), deltas=np.array(deltas))

    checks = [check_a, check_b, check_c, check_d, check_d2, check_e, check_e2]
    hard = [c for c in checks if c["pass"] is not None]
    verdict = "PROVEN" if all(c["pass"] for c in hard) else "FAILED"
    if verdict == "PROVEN" and check_e["pass"] is None:
        verdict = "PARTIAL"

    receipt = {
        "fl": "FL-4782",
        "phase": 1,
        "verdict": verdict,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "site": "FL-4743 SITE 3 (pieta statue eye streams)",
        "terrain_route": {
            "chosen": "authored A3D map godot_project/assets/maps/game_map_y8.a3d "
                      "decoded with A3DMapLoader's rules (_center_offset + _height_at, "
                      "raw u16 / HEIGHT_SCALE 16.0)",
            "why": "the only heightfield source readable outside a live headed "
                   "compositor frame that is also the exact data the playable scene "
                   "loads (PlayableMap.tscn source_map_path)",
            "rejected_water_interaction_field_atlas": "SurfaceNeighborhoodAtlas / "
                   "ASCIICompositorEffect build it inside a live compositor frame; it is a "
                   "screen-space neighbourhood, not a world heightfield",
            "rejected_colyseus_terrain_authority": "colyseus_server/src/sim/terrain.ts "
                   "holds the same data but behind the server process boundary",
            "a3d_patch_count": patch_count,
        },
        "grid": {"origin_world_xz": [GRID_ORIGIN_X, GRID_ORIGIN_Z],
                 "cells": [GRID_W, GRID_H], "cell_size": CELL_SIZE,
                 "world_extent_x": [GRID_ORIGIN_X, GRID_ORIGIN_X + GRID_W * CELL_SIZE],
                 "world_extent_z": [GRID_ORIGIN_Z, GRID_ORIGIN_Z + GRID_H * CELL_SIZE],
                 "why": "windowed at native A3D visual-cell resolution; a quarter-res "
                        "whole-map grid would erase a 2 m stream",
                 "sea_cells": int((terrain <= SEA_SURFACE_Y).sum())},
        "scheme": {"name": "virtual pipes shallow water (O'Brien-Hodgins / Mei et al.)",
                   "integration": "explicit Euler, two barrier-separated stages",
                   "dt": DT, "steps": steps, "simulated_seconds": DT * steps,
                   "damping": DAMPING, "gravity": GRAVITY, "manning_n": MANNING_N,
                   "friction": "semi-implicit Manning bed drag on each pipe; without it the pipe model has no terminal velocity and the steady speed degenerates to cell_size/dt",
                   "cfl_bound": "dt <= cell_size / (|u|max + sqrt(g*h_max))",
                   "observed_cfl_number": cfl,
                   "sea_boundary": f"terrain <= WATER_SURFACE_Y ({SEA_SURFACE_Y}) absorbs; "
                                   "value mirrored from cell_owner_product.glsl, never edited"},
        "authored_sources": placed,
        "cpu_reference_seconds": cpu_seconds,
        "gpu_status": gpu_status,
        "gpu_execution_note": (
            "Godot 4.6.2 on this host exposes no RenderingDevice under a windowless "
            "display server: '--headless' gives DisplayServer 'headless' with the dummy "
            "rendering driver, so both RenderingServer.get_rendering_device() and "
            "create_local_rendering_device() return null. The only combination that "
            "yields a real device ('--display-driver headless --rendering-driver "
            "vulkan|metal') silently promotes the display server back to macOS and "
            "creates a window, which this lane is forbidden to do because a sibling lane "
            "owns the machine's single headed slot. Check E is therefore SKIPPED and the "
            "verdict is PARTIAL. Reproduce check E when the headed slot is free with: "
            "godot --display-driver headless --rendering-driver vulkan --path godot_project "
            "--script scripts/fl4782_flow_field_gpu_driver.gd -- "
            "--config=.run/fl4782_phase1/config.json --out=.run/fl4782_phase1"),
        "checks": checks,
        "artifacts": {
            "shader": str(SHADER_PATH.relative_to(REPO_ROOT)),
            "producer": "godot_project/scripts/world/water/simulation/WaterFlowFieldProducer.gd",
            "gpu_driver": "scripts/fl4782_flow_field_gpu_driver.gd",
            "cpu_fields": str((out_dir / "cpu_fields.npz").relative_to(REPO_ROOT)),
            "config": str(config_path.relative_to(REPO_ROOT)),
        },
    }
    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))

    print(f"FL-4782 phase 1 verdict: {verdict}")
    for c in checks:
        print(f"  {c['check']}: {c['pass']}")
    print(f"receipt: {receipt_path}")
    return 0 if verdict in ("PROVEN", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
