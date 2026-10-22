#!/usr/bin/env python3
# =============================================================================
# fl4359_geometry_parity_gate.py
# =============================================================================
# WHAT THIS IS
# ------------
# A BACKEND (no-pixel) measurement gate for the FL-4359 perspective geometry
# complaints. The operator's standing rule: every "fixed / not fixed" claim
# must come from a measured receipt, not from looking at screenshots.
#
# THE IDEA
# --------
# The renderer's perspective lane answers one question per screen subcell:
# "what surface does this camera ray see?" (terrain / water / mesh / sky).
# That answer is stored, per subcell, in the moving-cell diagnostic dump
# (--fl4359-moving-cell-dump), independent of any color mode — so this gate
# is chroma-independent BY CONSTRUCTION (it reads the owner buffer, never
# the painted pixels).
#
# This script recomputes the SAME answer on the CPU from first principles:
#   * it parses the authored map (game_map_y8.a3d) exactly the way the
#     Godot loader does (A3DMapLoader.gd: 8x8-cell patches, 5x5 height
#     lattice per patch, bilinear interpolation, /16 height scale,
#     center-to-origin offset), and
#   * it replays the exact per-subcell camera rays of the pinned pose and
#     marches them against the bilinear heightfield with the same rules the
#     resolve shader uses (descending rays bounded by the water-plane
#     crossing; land-classified crossings continue to the bed; water only
#     over water-classified crossings; everything else sky).
# The CPU answer is a pure function of (authored map, camera pose) — it
# cannot be wrong in the ways the GPU pipeline can (splat coverage, winner
# re-picks, backstop fabrication). Comparing the two per subcell yields
# HARD COUNTS for each operator symptom:
#
#   * "elongated islands / tongues"  -> gpu_terrain_over_cpu_water count
#     (GPU claims terrain where the exact ray provably reaches water)
#   * "water bleeding into terrain (blue-in-green)" -> gpu_water_over_cpu_terrain
#   * "mountain streaks / edge leakage" -> interior (non-boundary) owner
#     mismatches, reported with world coordinates so the leak is locatable
#   * "same artifact on every F4 chroma" -> the gate never reads colors, so
#     its numbers are the chroma-independent geometry truth.
#
# Static meshes are NOT in the CPU oracle (it has no mesh BVH), so subcells
# the GPU resolves as mesh owners are counted separately and excluded from
# the mismatch rate — mesh visibility has its own backend gate
# (fl4359_presence_proof_gate.py).
#
# BOUNDARY TOLERANCE
# ------------------
# A subcell whose ray passes within a small height clearance of the terrain
# (or whose hit sits within one cell of a land/water class change) is a
# legitimate coin-flip between two real surfaces — GPU and CPU may honestly
# disagree there by sub-texel sampling differences. Those are counted as
# "boundary_adjacent" and reported separately from "interior" mismatches,
# which are the real defect signal.
#
# HOW TO RUN
# ----------
#   python3 scripts/fl4359_geometry_parity_gate.py \
#       --dump-dir "<dir with frame_NNN.json + frame_NNN_diag.bin>" \
#       --camera-pos 66.477,43.306,-2.219 --camera-yaw 184.0 \
#       --camera-pitch -31.613558 [--fov 58] [--viewport 1280x800] \
#       [--a3d godot_project/assets/maps/game_map_y8.a3d] \
#       [--frame -1] [--out receipt.json]
#
# The camera pose is the F3-overlay pose of the pinned capture (the pinned
# harness prints it; repro_fl4359_leak_poses.py pins it by construction).
# The gate prints a verdict block and writes a JSON receipt.
# =============================================================================

import argparse   # command-line flags
import glob       # find dump frame manifests
import json       # manifests in, receipt out
import math       # trig for the camera basis
import os         # path handling
import struct     # decode the binary A3D patches and the diag records
import sys        # exit codes

# ---------------------------------------------------------------------------
# Constants mirrored from the engine. If any of these drift from the shader
# or loader, the gate is measuring a different world than the renderer —
# keep them in sync with the cited sources.
# ---------------------------------------------------------------------------
VIS = 8            # visual cells per A3D patch side (A3DMapLoader.gd VISUAL_CELLS)
HC = 4             # height cells per patch side (A3DMapLoader.gd HEIGHT_CELLS)
HEIGHT_SCALE = 16.0  # raw u16 height -> world units (A3DMapLoader.gd HEIGHT_SCALE)
WATER_Y = 3.0625   # global water plane (ascii_probe_resolve.glsl WATER_SURFACE_Y)
VISIBLE_WATER_MAT = 140  # authored water material id (capture classification rule)
# Diagnostic owner-kind enum (ascii_probe_resolve.glsl:236-240)
OWNER_NONE, OWNER_TERRAIN, OWNER_STATIC_MESH = 0, 1, 2
OWNER_DYNAMIC_MESH, OWNER_ACTOR, OWNER_WATER, OWNER_SKY = 3, 4, 5, 6
OWNER_NAMES = {0: "none", 1: "TERRAIN", 2: "STATIC_MESH", 3: "DYNAMIC_MESH",
               4: "ACTOR", 5: "WATER", 6: "SKY", 0xFF: "invalid"}
# Bed-resolution extension mirrored from the shader (SHORE_BED_MARCH_EXTRA).
BED_MARCH_EXTRA = 16.0
# Height clearance (world units) under which a ray is "grazing" the terrain
# and an owner flip is a legitimate boundary case, not a defect.
BOUNDARY_CLEARANCE = 0.75


class Heightfield:
    """The authored terrain as the resolve shader sees it: a 1-unit corner
    lattice (bilinear between corners) plus a per-cell material id. Parsed
    byte-for-byte the way A3DMapLoader.gd parses the .a3d file."""

    def __init__(self, a3d_path):
        buf = open(a3d_path, "rb").read()
        patch_count = struct.unpack_from("<I", buf, 8)[0]
        off = 16
        self.patches = {}
        for _ in range(patch_count):
            px, py = struct.unpack_from("<ii", buf, off)
            visual = struct.unpack_from("<64H", buf, off + 8)   # 8x8 cell words
            heights = struct.unpack_from("<25H", buf, off + 136)  # 5x5 lattice
            self.patches[(px, py)] = (visual, heights)
            off += 188  # PATCH_BYTES
        xs = [p[0] for p in self.patches]
        ys = [p[1] for p in self.patches]
        self.bx0, self.by0 = min(xs), min(ys)
        bw, bh = max(xs) - self.bx0 + 1, max(ys) - self.by0 + 1
        # center_to_origin: Godot world (0,0) is the map center (loader default).
        self.cx_off = (self.bx0 + bw / 2.0) * VIS
        self.cy_off = (self.by0 + bh / 2.0) * VIS
        # Godot-world coordinate of lattice corner (0,0) — mirrors the
        # terrain_origin_dims.xy uniform the shader subtracts before texel math.
        self.origin_x = self.bx0 * VIS - self.cx_off
        self.origin_z = self.by0 * VIS - self.cy_off
        self.dims_x, self.dims_z = bw * VIS, bh * VIS  # cells

    def _patch_height(self, heights, vx, vy):
        """Bilinear height (world units) inside one patch at vertex-space
        (vx, vy) in [0, 8]. The 5x5 lattice spans the patch at 2-cell pitch,
        exactly like A3DMapLoader._height_at."""
        hx, hy = vx / 2.0, vy / 2.0
        x0, y0 = int(math.floor(hx)), int(math.floor(hy))
        x1, y1 = min(x0 + 1, HC), min(y0 + 1, HC)
        tx, ty = hx - x0, hy - y0
        h00 = heights[y0 * (HC + 1) + x0]
        h10 = heights[y0 * (HC + 1) + x1]
        h01 = heights[y1 * (HC + 1) + x0]
        h11 = heights[y1 * (HC + 1) + x1]
        return ((h00 * (1 - tx) + h10 * tx) * (1 - ty)
                + (h01 * (1 - tx) + h11 * tx) * ty) / HEIGHT_SCALE

    def height(self, wx, wz):
        """Bilinear terrain height at a Godot-world XZ point, or None when the
        point lies outside the authored map (open sea by definition)."""
        sx, sz = wx + self.cx_off, wz + self.cy_off
        pxc, pyc = int(math.floor(sx / VIS)), int(math.floor(sz / VIS))
        p = self.patches.get((pxc, pyc))
        if p is None:
            return None
        return self._patch_height(p[1], sx - pxc * VIS, sz - pyc * VIS)

    def cell_mat(self, cell_x, cell_z):
        """Material id of an integer terrain cell (lattice coordinates), or
        None when out of bounds."""
        sx, sz = cell_x + self.origin_x + self.cx_off, cell_z + self.origin_z + self.cy_off
        pxc, pyc = int(math.floor(sx / VIS)), int(math.floor(sz / VIS))
        p = self.patches.get((pxc, pyc))
        if p is None:
            return None
        lx, lz = int(math.floor(sx)) - pxc * VIS, int(math.floor(sz)) - pyc * VIS
        return p[0][lz * VIS + lx] & 0xFF

    def cell_from_world(self, wx, wz):
        """Integer lattice cell containing a Godot-world XZ point (the same
        floor(world - origin) the shader uses)."""
        return (int(math.floor(wx - self.origin_x)),
                int(math.floor(wz - self.origin_z)))

    def cell_is_water(self, cell):
        """Capture's land/water classification, mirrored from
        resolve_cell_is_water: out-of-map = water; else water material OR all
        four corner heights at/below the plane."""
        cx, cz = cell
        if cx < 0 or cz < 0 or cx >= self.dims_x or cz >= self.dims_z:
            return True
        wx = cx + self.origin_x
        wz = cz + self.origin_z
        hs = [self.height(wx + dx, wz + dz) for dx in (0, 1) for dz in (0, 1)]
        hs = [h for h in hs if h is not None]
        maxh = max(hs) if hs else 0.0
        mat = self.cell_mat(cx, cz)
        return (mat == VISIBLE_WATER_MAT) or (maxh <= WATER_Y)


def cpu_expected_owner(hf, origin, direction, step=0.10):
    """The CPU oracle: what surface does this exact ray see?

    Returns (owner_kind, clearance) where clearance is the smallest height
    gap between the ray and the terrain along the inspected span (used to
    classify grazing/boundary cases). Rules mirror the resolve backstop:
      * first terrain hit before the water-plane crossing -> TERRAIN
      * no hit, descending, water-classified crossing      -> WATER
      * no hit, descending, land-classified crossing       -> bed march
        (continue past the plane; bed hit -> TERRAIN)      -> TERRAIN
      * ray at/above horizon that hits nothing             -> SKY
    The march is fine-step sampling rather than the shader's exact DDA; the
    BOUNDARY_CLEARANCE class absorbs the sub-sample differences.
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    descending = dy < -1e-4
    # March bound: descending rays stop at the water crossing (plus the bed
    # extension for land crossings); other rays get a flat far cap.
    if descending:
        t_water = (WATER_Y - oy) / dy
        t_max = t_water + BED_MARCH_EXTRA
    else:
        t_water = None
        t_max = 1024.0
    min_clearance = 1e9
    t = 0.5
    while t < t_max:
        px, py, pz = ox + dx * t, oy + dy * t, oz + dz * t
        h = hf.height(px, pz)
        if h is not None:
            c = py - h
            if c <= 0.0:
                # Terrain hit. Hits past the water crossing only count when
                # the crossing cell was land-classified (the bed rule) —
                # over real water the plane occludes them.
                if t_water is not None and t > t_water:
                    cross = (ox + dx * t_water, oz + dz * t_water)
                    if hf.cell_is_water(hf.cell_from_world(*cross)):
                        return OWNER_WATER, min_clearance
                return OWNER_TERRAIN, min_clearance
            if t_water is None or t <= t_water:
                min_clearance = min(min_clearance, c)
        t += step
    if descending:
        cross = (ox + dx * t_water, oz + dz * t_water)
        if hf.cell_is_water(hf.cell_from_world(*cross)):
            return OWNER_WATER, min_clearance
        # Land crossing whose bed lies beyond the extension: inconclusive,
        # mirror the shader's fail-open by accepting either land answer —
        # report as TERRAIN with zero clearance so it lands in the boundary
        # class, never in the interior-defect class.
        return OWNER_TERRAIN, 0.0
    return OWNER_SKY, min_clearance


def camera_rays(cam_pos, yaw_deg, pitch_deg, fov_deg, vw, vh, ansi_w, ansi_h):
    """Per-SUBCELL ray directions for the pinned pose.

    The resolve lane runs one ray per subcell on a (2*ansi_w) x (2*ansi_h)
    grid (probe_subcell_product_index: each ANSI cell has a 2x2 slot block).
    Godot camera: yaw rotates -Z forward about +Y, fov is VERTICAL.
    Returns a function slot_xy -> unit direction, mirroring safe_ray_dir's
    'NDC at slot center' construction.
    """
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cp = math.cos(pitch)
    fwd = (-math.sin(yaw) * cp, math.sin(pitch), -math.cos(yaw) * cp)
    # right = normalize(cross(fwd, up)), up' = cross(right, fwd).
    # cross((fx,fy,fz),(0,1,0)) = (-fz, 0, fx) — verified against the GPU
    # owner map (the first cut used cross(up, fwd) and produced a mirrored
    # screen: water/terrain swapped left-right vs the dump).
    rx, ry, rz = (-fwd[2], 0.0, fwd[0])
    rl = math.sqrt(rx * rx + ry * ry + rz * rz)
    rx, ry, rz = rx / rl, ry / rl, rz / rl
    ux = ry * fwd[2] - rz * fwd[1]
    uy = rz * fwd[0] - rx * fwd[2]
    uz = rx * fwd[1] - ry * fwd[0]
    tanv = math.tan(math.radians(fov_deg) / 2.0)
    aspect = vw / vh
    fb_w, fb_h = ansi_w * 2, ansi_h * 2  # subcell grid = the shader's fb dims

    def ray(sx, sy):
        ndc_x = ((sx + 0.5) / fb_w) * 2.0 - 1.0
        ndc_y = 1.0 - ((sy + 0.5) / fb_h) * 2.0
        dx = fwd[0] + (rx * ndc_x * tanv * aspect) + (ux * ndc_y * tanv)
        dy = fwd[1] + (ry * ndc_x * tanv * aspect) + (uy * ndc_y * tanv)
        dz = fwd[2] + (rz * ndc_x * tanv * aspect) + (uz * ndc_y * tanv)
        n = math.sqrt(dx * dx + dy * dy + dz * dz)
        return (dx / n, dy / n, dz / n)

    return ray


def main():
    ap = argparse.ArgumentParser(description="FL-4359 CPU-vs-GPU geometry parity gate")
    ap.add_argument("--dump-dir", required=True,
                    help="moving-cell dump dir (frame_NNN.json + _diag.bin)")
    ap.add_argument("--camera-pos", required=True,
                    help="pinned camera position x,y,z (F3 overlay)")
    ap.add_argument("--camera-yaw", type=float, required=True)
    ap.add_argument("--camera-pitch", type=float, required=True)
    ap.add_argument("--fov", type=float, default=58.0, help="vertical FOV degrees")
    ap.add_argument("--viewport", default="1280x800")
    ap.add_argument("--a3d", default="godot_project/assets/maps/game_map_y8.a3d")
    ap.add_argument("--frame", type=int, default=-1,
                    help="dump frame index to grade (-1 = last)")
    ap.add_argument("--out", default="", help="JSON receipt path")
    args = ap.parse_args()

    manifests = sorted(glob.glob(os.path.join(args.dump_dir, "frame_*.json")))
    if not manifests:
        print("VERDICT: ERROR no dump manifests in", args.dump_dir)
        return 2
    mpath = manifests[args.frame]
    m = json.load(open(mpath))
    diag = open(mpath.replace(".json", "_diag.bin"), "rb").read()
    aw, ah, stride = m["ansi_w"], m["ansi_h"], m["diag_stride"]

    cam = tuple(float(v) for v in args.camera_pos.split(","))
    # Sanity pin: the dump's own camera origin must match the pose we replay,
    # or the receipt would grade rays the GPU never cast.
    dc = m.get("camera_origin", [0, 0, 0])
    if max(abs(dc[i] - cam[i]) for i in range(3)) > 0.05:
        print(f"VERDICT: ERROR dump camera {dc} != replay camera {cam}")
        return 2
    vw, vh = (int(v) for v in args.viewport.split("x"))
    # EXACT replay (2026-07-12): newer dumps publish the camera basis rows in
    # the manifest, so the per-subcell rays can be rebuilt from the REAL
    # orientation instead of a chase-camera yaw/pitch assumption. Godot basis
    # columns are camera right (x), up (y), back (z) in world space; the view
    # direction is -z. When present this overrides the CLI yaw/pitch args.
    if "camera_basis_x" in m:
        bx, by, bz = m["camera_basis_x"], m["camera_basis_y"], m["camera_basis_z"]
        fwd = (-bz[0], -bz[1], -bz[2])
        # yaw/pitch back-derived only for the receipt header; the ray builder
        # below re-derives right/up from fwd exactly as the shader does.
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd[1]))))
        yaw = math.degrees(math.atan2(-fwd[0], -fwd[2]))
        print(f"  manifest camera basis: yaw={yaw:.3f} pitch={pitch:.3f} "
              f"(cli said yaw={args.camera_yaw} pitch={args.camera_pitch})")
        ray_fn = camera_rays(cam, yaw, pitch, args.fov, vw, vh, aw, ah)
    else:
        ray_fn = camera_rays(cam, args.camera_yaw, args.camera_pitch, args.fov, vw, vh, aw, ah)

    hf = Heightfield(args.a3d)

    # Per-subcell comparison. GPU final owner = diag r0 word 2. When the dump
    # carries the widened 48-byte GEOMETRY TRACE record (diag_stride >= 48),
    # also decode the trace words: captured owner, decision bits, march
    # distances, derived cell, and the receiver world position — these settle
    # each island/height-leak claim without any pixel reading.
    has_trace = stride >= 48
    counts = {"total": 0, "mesh_excluded": 0, "agree": 0,
              "boundary_adjacent": 0, "interior_mismatch": 0}
    pair_hist = {}       # (gpu_owner, cpu_owner) -> count, mismatches only
    worst = []           # sample interior mismatches with world coords
    decision_hist = {}   # DIAG_DECIDE_* bit name -> subcell count
    DECIDE_BITS = [(1, "arb_ran"), (2, "arb_water"), (4, "arb_terrain"),
                   (8, "arb_bed"), (16, "march_truncated"),
                   (32, "rekey_march_ran"), (64, "rekey_march_hit"),
                   (128, "height_envelope_ok"), (256, "rekey_adopted"),
                   (512, "backstop_bed")]
    captured_vs_final = {}  # pre-decision winner kind -> final kind flow
    height_errs = []        # |receiver.y - CPU terrain height| on agreeing terrain
    for sy in range(ah * 2):
        for sx in range(aw * 2):
            idx = ((sy // 2) * aw + (sx // 2)) * 4 + ((sx & 1) | ((sy & 1) << 1))
            base = idx * stride
            gpu_kind = struct.unpack_from("<I", diag, base + 8)[0]
            if has_trace:
                cap_kind, dec_bits, tw_u, th_u = struct.unpack_from("<4I", diag, base + 16)
                rcell, rxu, ryu, rzu = struct.unpack_from("<4I", diag, base + 32)
                recv = struct.unpack("<3f", struct.pack("<3I", rxu, ryu, rzu))
                for bit, name in DECIDE_BITS:
                    if dec_bits & bit:
                        decision_hist[name] = decision_hist.get(name, 0) + 1
                flow = (f"{OWNER_NAMES.get(cap_kind, cap_kind)}->"
                        f"{OWNER_NAMES.get(gpu_kind, gpu_kind)}")
                if cap_kind != gpu_kind:
                    captured_vs_final[flow] = captured_vs_final.get(flow, 0) + 1
            if gpu_kind in (OWNER_STATIC_MESH, OWNER_DYNAMIC_MESH, OWNER_ACTOR):
                counts["mesh_excluded"] += 1
                continue
            if gpu_kind not in (OWNER_TERRAIN, OWNER_WATER, OWNER_SKY):
                continue  # invalid/none: absence is a different gate's business
            counts["total"] += 1
            d = ray_fn(sx, sy)
            cpu_kind, clearance = cpu_expected_owner(hf, cam, d)
            if cpu_kind == gpu_kind:
                counts["agree"] += 1
                # HEIGHT PARITY (the operator's "height mis-resolution"
                # metric): on subcells where both sides agree the surface is
                # terrain, compare the GPU receiver's claimed height against
                # the CPU heightfield at the receiver's own XZ. Zero means
                # the GPU's height answer is the authored terrain.
                if has_trace and gpu_kind == OWNER_TERRAIN:
                    h_cpu = hf.height(recv[0], recv[2])
                    if h_cpu is not None:
                        height_errs.append(abs(recv[1] - h_cpu))
                continue
            key = f"gpu_{OWNER_NAMES[gpu_kind]}_over_cpu_{OWNER_NAMES[cpu_kind]}"
            pair_hist[key] = pair_hist.get(key, 0) + 1
            if clearance < BOUNDARY_CLEARANCE:
                counts["boundary_adjacent"] += 1
            else:
                counts["interior_mismatch"] += 1
                if len(worst) < 40:
                    t_w = (WATER_Y - cam[1]) / d[1] if d[1] < -1e-4 else 0.0
                    wx, wz = cam[0] + d[0] * t_w, cam[2] + d[2] * t_w
                    entry = {"slot": [sx, sy], "ansi": [sx // 2, sy // 2],
                             "gpu": OWNER_NAMES[gpu_kind],
                             "cpu": OWNER_NAMES[cpu_kind],
                             "clearance": round(clearance, 3),
                             "crossing_world_xz": [round(wx, 1), round(wz, 1)]}
                    if has_trace:
                        entry["captured"] = OWNER_NAMES.get(cap_kind, cap_kind)
                        entry["decision_bits"] = dec_bits
                        entry["receiver"] = [round(v, 2) for v in recv]
                    worst.append(entry)

    graded = counts["total"]
    mismatch = counts["boundary_adjacent"] + counts["interior_mismatch"]
    receipt = {
        "fl": "FL-4359",
        "kind": "geometry_parity_receipt",
        "dump_frame": os.path.basename(mpath),
        "camera": {"pos": cam, "yaw": args.camera_yaw, "pitch": args.camera_pitch,
                   "fov": args.fov},
        "counts": counts,
        "mismatch_pct": round(100.0 * mismatch / max(graded, 1), 3),
        "interior_mismatch_pct": round(100.0 * counts["interior_mismatch"] / max(graded, 1), 3),
        "pair_histogram": dict(sorted(pair_hist.items(), key=lambda kv: -kv[1])),
        "worst_interior_samples": worst,
        # Symptom-mapped counters (the operator's names for the artifacts):
        "island_tongue_metric_gpu_terrain_over_cpu_water":
            pair_hist.get("gpu_TERRAIN_over_cpu_WATER", 0),
        "blue_in_green_metric_gpu_water_over_cpu_terrain":
            pair_hist.get("gpu_WATER_over_cpu_TERRAIN", 0),
    }
    if has_trace:
        receipt["trace"] = {
            "decision_histogram": decision_hist,
            "captured_to_final_flows": dict(
                sorted(captured_vs_final.items(), key=lambda kv: -kv[1])),
        }
        if height_errs:
            height_errs.sort()
            n = len(height_errs)
            receipt["height_parity"] = {
                "samples": n,
                "p50": round(height_errs[n // 2], 3),
                "p90": round(height_errs[int(n * 0.9)], 3),
                "max": round(height_errs[-1], 3),
            }
    if args.out:
        json.dump(receipt, open(args.out, "w"), indent=2)
    print(f"GEOMETRY PARITY  frame={receipt['dump_frame']}  graded={graded} "
          f"mesh_excluded={counts['mesh_excluded']}")
    print(f"  agree={counts['agree']}  boundary_adjacent={counts['boundary_adjacent']}  "
          f"interior_mismatch={counts['interior_mismatch']}  "
          f"({receipt['interior_mismatch_pct']}% interior)")
    for k, v in receipt["pair_histogram"].items():
        print(f"    {k}: {v}")
    print(f"  island-tongue metric (gpu TERRAIN over cpu WATER): "
          f"{receipt['island_tongue_metric_gpu_terrain_over_cpu_water']}")
    print(f"  blue-in-green metric (gpu WATER over cpu TERRAIN): "
          f"{receipt['blue_in_green_metric_gpu_water_over_cpu_terrain']}")
    if has_trace:
        print("  decisions:", " ".join(f"{k}={v}" for k, v in
                                       sorted(decision_hist.items())))
        if "height_parity" in receipt:
            hp = receipt["height_parity"]
            print(f"  height parity |receiver.y - cpu height| on agreeing "
                  f"terrain: p50={hp['p50']} p90={hp['p90']} max={hp['max']} "
                  f"(n={hp['samples']})")
        flows = receipt["trace"]["captured_to_final_flows"]
        if flows:
            print("  captured->final owner flows (changed only):",
                  " ".join(f"{k}:{v}" for k, v in list(flows.items())[:6]))
    if args.out:
        print(f"  receipt: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
