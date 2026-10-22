#!/usr/bin/env python3
"""Classic traversal capture driver for the connected Numabe AOI.

Computes camera poses (E, N, heading) along the Valhalla pedestrian spine and
along distinct road carriers inside the AOI ring, converts them to Classic map
units, then captures one Classic frame per pose.

Pose mechanism (named unknown NUMABE-CLASSIC-DRIVER-01): engine/game_app.cpp
exposes no CLI spawn/pose flag (--auto-start-map, --observe-render,
--view-tuple, --schema-version only). engine/mainmenu.cpp LoadGame prefers the
A3D embedded player-start record (WorldGetPlayerStart) over the
ASCIICKER_SPAWN_* env defaults. Poses are therefore driven by rewriting the
player start per pose on a scratch copy of the map, mirroring the
_embed_player_start owner in scripts/sbu_e2e_run.py (terrain bilinear sample +
16.0 clearance, yaw/dir set to the pose heading) without editing that owner.
Heading uses the engine body-facing convention (render_s3 fwd_e=-sin(yaw),
fwd_n=cos(yaw); local_player_authority movement math): yaw = -(bearing).

Frame rendering (named unknown NUMABE-CLASSIC-DRIVER-02): contact-sheet tiles
are rasterized with the existing scripts/xp_to_png.py owner (read_xp, load_bdf,
render_layer); this driver only downscales, labels and assembles with PIL.

Planning rules: spine poses every --spine-step-m along the Valhalla leg;
street poses by walking each distinct road carrier inside the AOI ring at
--street-step-m, keeping a minimum 30 m separation from any already-planned
pose (spine included) and skipping carriers with under 20 m inside the ring.
--max-poses caps the plan with deterministic thinning that keeps the spine
intact. The plan is written to poses.json BEFORE any launch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from scripts.pipeline import geo_frames as GF  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "docs" / "agent" / "cli-anything"))
from a3d_edit import read_a3d_sections, write_a3d_sections, _load_a3d_format  # noqa: E402


def _load_xp_renderer():
    """Load the existing scripts/xp_to_png.py owner without executing it."""
    mod_path = REPO_ROOT / "scripts" / "xp_to_png.py"
    spec = importlib.util.spec_from_file_location("xp_to_png_owner", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find xp renderer at {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xp_to_png_owner"] = mod
    spec.loader.exec_module(mod)
    return mod


SPAWN_CLEARANCE = 16.0
MIN_STREET_SEPARATION_M = 30.0
MIN_CARRIER_INSIDE_M = 20.0
CAPTURE_FILES = ("source-shot.xp", "source-shot.json", "source-resolved-cells.jsonl")
XP_FONT = REPO_ROOT / "assets" / "fonts" / "cp437_12x12.png.bdf"


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Decode a Valhalla/Google encoded polyline at 1e-6 degree precision."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        values = []
        for _axis in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("truncated encoded polyline")
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        coords.append((lat / 1_000_000.0, lon / 1_000_000.0))
    if len(coords) < 2:
        raise ValueError("pedestrian route must contain at least two coordinates")
    return coords


def load_spine_en(route_path: Path) -> list[tuple[float, float]]:
    """Spine polyline as EPSG:32654 (E, N) metres."""
    route_doc = json.loads(route_path.read_text(encoding="utf-8"))
    legs = (route_doc.get("trip") or {}).get("legs") or []
    if len(legs) != 1 or not isinstance(legs[0].get("shape"), str):
        raise ValueError("route source must contain exactly one Valhalla encoded leg")
    spine = []
    for lat, lon in decode_polyline6(legs[0]["shape"]):
        easting, northing = GF.utm_forward(lon, lat)
        spine.append((easting, northing))
    return spine


def load_aoi_ring_en(route_path: Path) -> list[tuple[float, float]]:
    """AOI ring as EPSG:32654 (E, N) metres (bundle sibling of the route)."""
    aoi_path = route_path.parent / "connected_scope.geojson"
    doc = json.loads(aoi_path.read_text(encoding="utf-8"))
    for feature in doc.get("features", []):
        if feature.get("properties", {}).get("role") != "aoi":
            continue
        geom = feature.get("geometry", {})
        if geom.get("type") != "Polygon":
            continue
        ring = geom["coordinates"][0]
        return [GF.utm_forward(lon, lat) for lon, lat in ring]
    raise ValueError(f"no aoi-role Polygon in {aoi_path}")


def point_in_ring(px: float, py: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < xin:
                inside = not inside
    return inside


def compass_bearing(ax: float, ay: float, bx: float, by: float) -> float:
    """Compass bearing degrees: 0 = north (+N), 90 = east (+E), clockwise."""
    return (math.degrees(math.atan2(bx - ax, by - ay)) + 360.0) % 360.0


def sample_polyline(
    pts: list[tuple[float, float]], step_m: float
) -> list[tuple[float, float, float]]:
    """Samples (E, N, bearing) every step_m along the polyline, plus endpoint."""
    if len(pts) < 2:
        return []
    seg_lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    total = sum(seg_lens)
    if total <= 0:
        return []
    out: list[tuple[float, float, float]] = []
    target = 0.0
    acc = 0.0
    for (a, b, seg) in zip(pts, pts[1:], seg_lens):
        bearing = compass_bearing(a[0], a[1], b[0], b[1])
        while target <= acc + seg:
            t = 0.0 if seg <= 0 else (target - acc) / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, bearing))
            target += step_m
        acc += seg
    last_bearing = compass_bearing(pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1])
    if not out or math.hypot(out[-1][0] - pts[-1][0], out[-1][1] - pts[-1][1]) > 1e-6:
        out.append((pts[-1][0], pts[-1][1], last_bearing))
    return out


def clip_runs_inside_ring(
    pts: list[tuple[float, float]], ring: list[tuple[float, float]],
    densify_m: float = 5.0,
) -> list[list[tuple[float, float]]]:
    """Consecutive runs of a carrier polyline lying inside the AOI ring."""
    if len(pts) < 2:
        return []
    dense: list[tuple[float, float]] = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, int(math.ceil(seg / densify_m)))
        for s in range(1, steps + 1):
            t = s / steps
            dense.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    runs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for p in dense:
        if point_in_ring(p[0], p[1], ring):
            cur.append(p)
        elif cur:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def run_length(run: list[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(run, run[1:]))


def plan_poses(
    spine: list[tuple[float, float]],
    carriers: list[dict],
    ring: list[tuple[float, float]],
    spine_step_m: float,
    street_step_m: float,
    max_poses: int,
) -> list[dict]:
    """Plan spine poses then separated street poses; deterministic cap."""
    poses: list[dict] = []
    for e, n, bearing in sample_polyline(spine, spine_step_m):
        poses.append({"source": "spine", "feature_id": None,
                      "e": e, "n": n, "heading": bearing})
    street: list[dict] = []
    for feat in carriers:
        runs = clip_runs_inside_ring(feat["geometry"]["coordinates"], ring)
        inside_len = sum(run_length(r) for r in runs)
        if inside_len < MIN_CARRIER_INSIDE_M:
            continue
        for run in runs:
            if run_length(run) < MIN_CARRIER_INSIDE_M:
                continue
            for e, n, bearing in sample_polyline(run, street_step_m):
                if all(math.hypot(e - q["e"], n - q["n"]) >= MIN_STREET_SEPARATION_M
                       for q in poses + street):
                    street.append({"source": "road", "feature_id": feat["feature_id"],
                                   "e": e, "n": n, "heading": bearing})
    spine_count = len(poses)
    if spine_count + len(street) > max_poses:
        keep = max(0, max_poses - spine_count)
        if keep <= 0:
            street = []
        else:
            stride = math.ceil(len(street) / keep)
            street = street[::stride][:keep]
    return poses + street


def bearing_to_yaw(bearing: float) -> float:
    """Engine body-facing convention: game yaw = -(compass bearing)."""
    yaw = -bearing
    return ((yaw + 180.0) % 360.0) - 180.0


def embed_player_start(map_path: Path, spawn_x: float, spawn_y: float, yaw: float) -> float:
    """Mirror scripts/sbu_e2e_run.py::_embed_player_start (read-only reuse)."""
    fmt = _load_a3d_format()
    with open(map_path, "rb") as f:
        sig = f.read(4)
        if sig != b"AS3D":
            raise ValueError(f"not an A3D file: {map_path}")
        header_size = struct.unpack("<I", f.read(4))[0]
        num_patches = struct.unpack("<I", f.read(4))[0]
        f.seek(header_size)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(num_patches)]
    if not patches:
        raise ValueError(f"no terrain patches in {map_path}")
    patch_world = float(fmt.VISUAL_CELLS)
    target = None
    for patch in patches:
        px0 = float(patch.x) * patch_world
        py0 = float(patch.y) * patch_world
        if px0 <= spawn_x <= px0 + patch_world and py0 <= spawn_y <= py0 + patch_world:
            target = patch
            break
    if target is None:
        target = min(
            patches,
            key=lambda p: (
                (float(p.x) * patch_world + patch_world * 0.5 - spawn_x) ** 2
                + (float(p.y) * patch_world + patch_world * 0.5 - spawn_y) ** 2
            ),
        )
    local_x = max(0.0, min(patch_world, spawn_x - float(target.x) * patch_world))
    local_y = max(0.0, min(patch_world, spawn_y - float(target.y) * patch_world))
    vertex_step = patch_world / float(fmt.HEIGHT_CELLS)
    fx = local_x / vertex_step
    fy = local_y / vertex_step
    x0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(fx)))
    y0 = min(fmt.HEIGHT_CELLS - 1, max(0, int(fy)))
    x1 = min(fmt.HEIGHT_CELLS, x0 + 1)
    y1 = min(fmt.HEIGHT_CELLS, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    h00 = float(target.height[y0][x0])
    h10 = float(target.height[y0][x1])
    h01 = float(target.height[y1][x0])
    h11 = float(target.height[y1][x1])
    hx0 = h00 + (h10 - h00) * tx
    hx1 = h01 + (h11 - h01) * tx
    terrain_h = hx0 + (hx1 - hx0) * ty
    ps = fmt.A3DPlayerStart(
        pos=[float(spawn_x), float(spawn_y), terrain_h + float(SPAWN_CLEARANCE)],
        yaw=float(yaw),
        dir=float(yaw),
    )
    pre, fv, instances, _old_ps, enemy_gens, markers = read_a3d_sections(str(map_path))
    out_fv = fv if fv <= -4 else -4
    write_a3d_sections(str(map_path), pre, out_fv, instances, ps, enemy_gens, markers)
    return float(ps.pos[2])


def read_xp_cells(xp_path: Path) -> tuple[int, int, list[tuple[int, bytes, bytes]]]:
    """Raw cells from a native .xp capture: (width, height, [(glyph, fg, bg)])."""
    with open(xp_path, "rb") as f:
        data = f.read()
    if len(data) < 16:
        raise ValueError("XP file too small for header")
    _version, _layers, width, height = struct.unpack_from("<4i", data, 0)
    off = 16
    n = width * height
    if len(data) < off + n * 10:
        raise ValueError("XP layer data out of range")
    cells = []
    for _ in range(n):
        glyph = struct.unpack_from("<I", data, off)[0]
        off += 4
        fg = bytes(data[off:off + 3])
        off += 3
        bg = bytes(data[off:off + 3])
        off += 3
        cells.append((glyph, fg, bg))
    return width, height, cells


def frame_stats(xp_path: Path) -> dict:
    """Numeric frame stats: blank means all-space or single-glyph."""
    width, height, cells = read_xp_cells(xp_path)
    glyphs = {g for g, _, _ in cells}
    non_space = sum(1 for g, _, _ in cells if g not in (0, 32))
    return {
        "width": width,
        "height": height,
        "cells": len(cells),
        "non_space_frac": non_space / len(cells) if cells else 0.0,
        "distinct_glyphs": len(glyphs),
        "blank": glyphs <= {0, 32} or len(glyphs) <= 1,
    }


def render_frame_image(xp_path: Path, font_path: Path = XP_FONT):
    """Rasterize a .xp frame with the existing xp_to_png owner; returns a PIL image."""
    from PIL import Image

    owner = _load_xp_renderer()
    version, layers = owner.read_xp(str(xp_path))
    if not layers:
        raise ValueError("XP file has no layers")
    glyphs, cell_w, cell_h, ascent, descent = owner.load_bdf(str(font_path))
    width, height, rgb = owner.render_layer(
        layers[-1], glyphs, cell_w, cell_h, ascent, descent, 1)
    return Image.frombytes("RGB", (width, height), bytes(rgb))


def write_contact_sheet(
    tiles: list[tuple[str, object]], sheet_path: Path, max_tiles: int = 40
) -> int:
    """Assemble labelled tiles (label, PIL image) into one sheet; returns count."""
    from PIL import Image, ImageDraw

    tiles = tiles[:max_tiles]
    if not tiles:
        raise ValueError("no tiles for contact sheet")
    thumb_w, thumb_h = 384, 216
    label_h = 22
    cols = 8
    rows = max(1, math.ceil(len(tiles) / cols))
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(tiles):
        thumb = img.resize((thumb_w, thumb_h), Image.LANCZOS)
        x, y = (i % cols) * thumb_w, (i // cols) * (thumb_h + label_h)
        sheet.paste(thumb, (x, y))
        draw.text((x + 3, y + thumb_h + 3), label, fill=(255, 255, 255))
    sheet.save(sheet_path)
    return len(tiles)


def launch_capture(
    game_bin: Path, scratch_map: Path, pose_dir: Path, timeout_s: int
) -> dict:
    """Launch the Classic runtime for one pose; record stdout/stderr/duration."""
    import re

    pose_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(CAPTURE_FILES) + ["launch_stdout.log", "launch_stderr.log"]:
        try:
            (pose_dir / stale).unlink()
        except FileNotFoundError:
            pass
    cmd = [
        str(game_bin.resolve()),
        "--auto-start-map",
        str(scratch_map.resolve()),
        "--observe-render",
        str(pose_dir.resolve()),
    ]
    env = os.environ.copy()
    started = time.time()
    stdout_path = pose_dir / "launch_stdout.log"
    stderr_path = pose_dir / "launch_stderr.log"
    timed_out = False
    returncode: int | None = None
    try:
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            proc = subprocess.run(
                cmd, cwd=str(REPO_ROOT), env=env,
                stdout=out, stderr=err, timeout=timeout_s, check=False,
            )
            returncode = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
    duration = time.time() - started
    deadline = time.time() + 30
    while time.time() < deadline:
        if all((pose_dir / name).exists() for name in CAPTURE_FILES):
            break
        time.sleep(0.25)
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    for pid in re.findall(
        r"LOCAL AUTH: connected to owned local server on port \d+ \(pid=(\d+)\)",
        stdout_text,
    ):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return {
        "command": cmd,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_sec": round(duration, 3),
        "observe_render_ok": "OBSERVE_RENDER_OK" in stdout_text,
        "artifacts": {
            name: (pose_dir / name).exists() for name in CAPTURE_FILES
        },
    }


def pose_fingerprint(pose: dict, mx: float, my: float, yaw: float) -> dict:
    return {
        "source": pose["source"], "feature_id": pose["feature_id"],
        "e": pose["e"], "n": pose["n"], "heading": pose["heading"],
        "map_x": mx, "map_y": my, "yaw": yaw,
    }


def fingerprint_matches(pose_dir: Path, fingerprint: dict) -> bool:
    try:
        saved = json.loads((pose_dir / "pose.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return False
    return saved.get("fingerprint") == fingerprint and all(
        (pose_dir / name).exists() for name in CAPTURE_FILES)


def build_sheets_from_existing(out_dir: Path) -> tuple[list[str], list[dict]]:
    """Contact sheets plus per-frame records from existing batch dirs.

    Pose identity is recovered from each scratch map's embedded player-start
    (map units, inverted to E/N with the run's terrain metadata when present);
    kind follows the DRIVER-01 plan order (spine poses first).
    """
    batch_dirs = sorted(out_dir.glob("batch_*"))
    sheet_names: list[str] = []
    frames: list[dict] = []
    meta: dict | None = None
    meta_path = out_dir / "terrain_metadata_used.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = None
    for batch_dir in batch_dirs:
        if not batch_dir.is_dir():
            continue
        pose_dirs = sorted(batch_dir.glob("pose_*"))
        tiles: list[tuple[str, object]] = []
        for pose_dir in pose_dirs:
            xp = pose_dir / "source-shot.xp"
            if not xp.exists():
                continue
            try:
                stats = frame_stats(xp)
            except Exception:  # noqa: BLE001 - blank-equivalent, counted below
                stats = {"blank": True, "non_space_frac": 0.0, "distinct_glyphs": 0,
                         "width": 0, "height": 0, "cells": 0}
            label_e, label_n, label_hdg, kind = "?", "?", "?", "street"
            try:
                idx = int(pose_dir.name.split("_")[1])
            except (IndexError, ValueError):
                idx = -1
            try:
                _pre, _fv, _inst, ps, _gen, _mark = read_a3d_sections(
                    str(pose_dir / "map_pose.a3d"))
                if ps is not None and meta is not None:
                    e = (ps.pos[0] - meta["offset_x"]) / meta["scale"] + meta["origin_e"]
                    n = (ps.pos[1] - meta["offset_y"]) / meta["scale"] + meta["origin_n"]
                    label_e, label_n = f"{e:.0f}", f"{n:.0f}"
                    label_hdg = f"{(-ps.yaw) % 360:.0f}"
            except Exception:  # noqa: BLE001 - label stays unknown
                pass
            if 0 <= idx < 12:
                kind = "spine"
            label = f"{pose_dir.name} {kind} E{label_e} N{label_n} h{label_hdg}"
            try:
                tiles.append((label, render_frame_image(xp)))
            except Exception:  # noqa: BLE001 - frame counted, tile skipped
                pass
            frames.append({"pose_dir": str(pose_dir.relative_to(out_dir)),
                           "batch": batch_dir.name, "kind": kind,
                           "blank": bool(stats["blank"]), "stats": stats})
        if tiles:
            sheet_name = f"contact_sheet_{batch_dir.name.split('_')[1]}.png"
            write_contact_sheet(tiles, out_dir / sheet_name)
            sheet_names.append(sheet_name)
    return sheet_names, frames


def summarize_launch_logs(out_dir: Path) -> dict:
    """Runtime load evidence from existing launch_stdout.log files."""
    logs = sorted(out_dir.glob("batch_*/pose_*/launch_stdout.log"))
    with_map = with_patches = with_ok = 0
    error_hits: list[str] = []
    for log in logs:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "MAP PATH:" in text:
            with_map += 1
        if "patches done, progress -> 3" in text:
            with_patches += 1
        if "OBSERVE_RENDER_OK" in text:
            with_ok += 1
        for marker in ("REJECTED", "Traceback", " RuntimeError", "FAILED"):
            if marker in text:
                error_hits.append(f"{log.parent.parent.name}/{log.parent.name}:{marker}")
    return {"logs": len(logs), "with_map": with_map, "with_patches": with_patches,
            "with_ok": with_ok, "error_hits": error_hits}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--terrain-metadata", type=Path, required=True)
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--spine-step-m", type=float, default=25.0)
    ap.add_argument("--street-step-m", type=float, default=40.0)
    ap.add_argument("--batch-size", type=int, default=40)
    ap.add_argument("--max-poses", type=int, default=100)
    ap.add_argument("--plan-only", action="store_true",
                    help="Write poses.json and exit without launching")
    ap.add_argument("--resume", action="store_true",
                    help="Skip poses whose directory holds the full triple")
    ap.add_argument("--sheets-only", action="store_true",
                    help="Build contact sheets and driver note from existing frames only")
    ap.add_argument("--game", type=Path, default=Path(".run/game_classic"))
    ap.add_argument("--launch-timeout-s", type=int, default=180)
    args = ap.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.sheets_only:
        sheet_names, frames = build_sheets_from_existing(out_dir)
        present = len(frames)
        blank = sum(1 for f in frames if f["blank"])
        ev = summarize_launch_logs(out_dir)
        lines = [
            f"frames present: {present} pose dirs with source-shot.xp",
            f"frames blank (all-space or single-glyph): {blank}",
            f"runtime load: MAP PATH in {ev['with_map']}/{ev['logs']} logs, "
            f"patches-done in {ev['with_patches']}, OBSERVE_RENDER_OK in {ev['with_ok']}",
            f"launch errors: {len(ev['error_hits'])} "
            f"({'; '.join(ev['error_hits'][:3]) or 'none'})",
            "defect: DRIVER-01 planned 123 poses for a ~35-pose smoke proof and "
            "launched the runtime 118 times; operator stopped it at pose_117.",
            f"sheets: {', '.join(sheet_names) or 'none'} (<=40 tiles each)",
        ]
        (out_dir / "driver_note.txt").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8")
        print("\n".join(lines), flush=True)
        return 0

    metadata = json.loads(args.terrain_metadata.read_text(encoding="utf-8"))
    if metadata.get("projection_kind") != "epsg32654_affine":
        raise SystemExit("terrain metadata must use the direct EPSG:32654 frame")
    origin_e = float(metadata["origin_easting"])
    origin_n = float(metadata["origin_northing"])
    scale = float(metadata["content_scale"])
    if abs(scale - GF.WORLD_UNITS_PER_METRE) > 1e-9:
        raise SystemExit(f"metadata scale {scale} conflicts with {GF.WORLD_UNITS_PER_METRE}")
    off_x = float(metadata.get("offset_x", 0.0))
    off_y = float(metadata.get("offset_y", 0.0))

    def to_map(e: float, n: float) -> tuple[float, float]:
        return ((e - origin_e) * scale + off_x, (n - origin_n) * scale + off_y)

    spine = load_spine_en(args.route)
    ring = load_aoi_ring_en(args.route)
    world_ir = json.loads(args.world_ir.read_text(encoding="utf-8"))
    carriers = [
        f for f in world_ir.get("canonical_features", [])
        if f.get("semantic_class") == "road"
        and f.get("geometry", {}).get("kind") == "polyline"
        and f.get("geometry", {}).get("frame_id") == "epsg32654"
    ]
    planned = plan_poses(spine, carriers, ring, args.spine_step_m,
                         args.street_step_m, args.max_poses)
    if len(planned) > args.max_poses:
        raise SystemExit(f"plan has {len(planned)} poses over --max-poses {args.max_poses}")
    (out_dir / "terrain_metadata_used.json").write_text(
        json.dumps({"origin_e": origin_e, "origin_n": origin_n, "scale": scale,
                    "offset_x": off_x, "offset_y": off_y}, indent=1) + "\n",
        encoding="utf-8",
    )
    (out_dir / "poses.json").write_text(
        json.dumps({"poses": [
            {"index": i, "source": p["source"], "feature_id": p["feature_id"],
             "e": p["e"], "n": p["n"], "heading": round(p["heading"], 3)}
            for i, p in enumerate(planned)],
            "pose_count": len(planned),
            "spine_count": sum(1 for p in planned if p["source"] == "spine"),
            "max_poses": args.max_poses}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"plan: {len(planned)} poses "
          f"({sum(1 for p in planned if p['source'] == 'spine')} spine), "
          f"cap {args.max_poses}", flush=True)
    if args.plan_only:
        return 0

    game_bin = args.game if args.game.is_absolute() else REPO_ROOT / args.game
    if not game_bin.exists():
        raise SystemExit(f"game binary missing: {game_bin}")

    t0 = time.time()
    records: list[dict] = []
    for i, pose in enumerate(planned):
        batch = i // args.batch_size
        batch_name = f"batch_{batch:02d}"
        pose_name = f"pose_{i:03d}"
        pose_dir = out_dir / batch_name / pose_name
        pose_dir.mkdir(parents=True, exist_ok=True)
        mx, my = to_map(pose["e"], pose["n"])
        yaw = bearing_to_yaw(pose["heading"])
        fingerprint = pose_fingerprint(pose, mx, my, yaw)
        if args.resume and fingerprint_matches(pose_dir, fingerprint):
            try:
                stats = frame_stats(pose_dir / "source-shot.xp")
                frame_ok, fail = not stats["blank"], None
                frame = stats
            except Exception as exc:  # noqa: BLE001
                frame_ok, fail, frame = False, f"resumed xp unreadable: {exc}", None
            records.append({"index": i, "resumed": True, "frame_ok": frame_ok,
                            "fail": fail, "frame": frame})
            print(f"[{i + 1}/{len(planned)}] {pose_name} resumed ok={frame_ok}",
                  flush=True)
            continue
        scratch = pose_dir / "map_pose.a3d"
        shutil.copy2(args.map, scratch)
        try:
            mz = embed_player_start(scratch, mx, my, yaw)
            embed_error = None
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            mz = None
            embed_error = f"{type(exc).__name__}: {exc}"
        (pose_dir / "pose.json").write_text(
            json.dumps({"index": i, "fingerprint": fingerprint, "map_z": mz,
                        "yaw": round(yaw, 3)}, indent=1) + "\n", encoding="utf-8")
        launch: dict | None = None
        frame = None
        frame_ok = False
        fail: str | None = embed_error
        if fail is None:
            launch = launch_capture(game_bin, scratch, pose_dir, args.launch_timeout_s)
            xp = pose_dir / "source-shot.xp"
            artifacts_ok = all(launch["artifacts"].values()) and launch["observe_render_ok"]
            if artifacts_ok and xp.exists():
                try:
                    frame = frame_stats(xp)
                    frame_ok = not frame["blank"]
                    if not frame_ok:
                        fail = "blank frame (all-space or single-glyph)"
                except Exception as exc:  # noqa: BLE001
                    fail = f"xp parse failed: {type(exc).__name__}: {exc}"
            else:
                missing = [k for k, v in launch["artifacts"].items() if not v]
                fail = (
                    f"runtime failed: timeout={launch['timed_out']} "
                    f"rc={launch['returncode']} ok={launch['observe_render_ok']} "
                    f"missing={missing}"
                )
        else:
            fail = f"embed failed: {fail}"
        records.append({"index": i, "resumed": False, "frame_ok": frame_ok,
                        "fail": fail, "frame": frame, "launch": launch})
        print(f"[{i + 1}/{len(planned)}] {pose_name} ok={frame_ok} "
              f"{('fail=' + str(fail)) if fail else ''}", flush=True)

    for batch_name in sorted({f"batch_{i // args.batch_size:02d}"
                              for i in range(len(planned))}):
        batch_dir = out_dir / batch_name
        tiles = []
        for rec in [r for r in records
                    if f"batch_{rec['index'] // args.batch_size:02d}" == batch_name]:
            pose_dir = batch_dir / f"pose_{rec['index']:03d}"
            xp = pose_dir / "source-shot.xp"
            if not rec["frame_ok"] or not xp.exists():
                continue
            p = planned[rec["index"]]
            try:
                tiles.append((f"pose_{rec['index']:03d} {p['source']} "
                              f"E{p['e']:.0f} N{p['n']:.0f} h{p['heading']:.0f}",
                              render_frame_image(xp)))
            except Exception:  # noqa: BLE001 - frame counted, tile skipped
                pass
        if tiles:
            write_contact_sheet(tiles, out_dir / f"contact_sheet_{batch_name.split('_')[1]}.png")

    ok = [r for r in records if r["frame_ok"]]
    failed = [r for r in records if not r["frame_ok"]]
    durations = [r["launch"]["duration_sec"] for r in ok
                 if not r.get("resumed") and r.get("launch")]
    mean_load = sum(durations) / len(durations) if durations else 0.0
    fail_list = ",".join(str(r["index"]) for r in failed) or "none"
    lines = [
        f"poses: {len(records)} planned (cap {args.max_poses}), "
        f"batches: {len({r['index'] // args.batch_size for r in records})}",
        f"frames ok: {len(ok)}/{len(records)}, failed: {len(failed)}",
        f"mean runtime load: {mean_load:.1f}s over {len(durations)} fresh captures",
        f"total driver time: {(time.time() - t0) / 60.0:.1f}min",
        f"failed pose indices: {fail_list}",
    ]
    for r in failed[:7]:
        lines.append(f"pose {r['index']}: {r['fail']}")
    while len(lines) > 12:
        lines.pop()
    (out_dir / "driver_note.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
