#!/usr/bin/env python3
# Ad hoc script: FL-4260 RQ-153d Edge lane strict CDP proof driver
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

"""FL-4260 RQ-153d strict Edge lane CDP proof.

Captures observe-render TERM++ buffers before and after authoring one material Edge
lane. The accepted invariant is exact: only selected material terrain cells whose
vertical_relation equals the edited lane and whose fact mask carries vertical
relation may change. Same-material nonmatching cells, other materials, and non-
material cells must remain stable. A no-action capture pair must also stay stable.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-25-rq153d-edge-runtime-proof"
PORT = 8765
MAT = 1
VR = 0
GID = 559
REJECTED_VR = 1
REJECTED_GID = 576
TERM_CAMERA = "64 64 40960 45 30 10.0 0"
WEATHER = "0"
MAP_PATH = ""
FIXTURE_STAMP_ALPHA = 0.5
FIXTURE_STAMP_RADIUS = 18.0
FIXTURE_KIND = "stamp"

class CDP:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.seq = 1
    def call(self, method: str, params: str = "", timeout: float = 60.0) -> str:
        payload = {"id": self.seq, "method": method, "params": params}
        self.seq += 1
        self.file.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.sock.settimeout(timeout)
        line = self.file.readline()
        if not line:
            raise RuntimeError(f"CDP EOF waiting for {method}")
        obj = json.loads(line.decode("utf-8", errors="replace"))
        return str(obj.get("result", obj))
    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

def port_open() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False

def start_asciiid() -> subprocess.Popen[str] | None:
    if port_open():
        return None
    log = OUT / "strict_observe_asciiid.log"
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([str(ROOT / ".run/asciiid"), "--cdp", str(PORT)], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if port_open():
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError(f"ASCIIID CDP did not open on {PORT}; see {log}")

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def cells(rows: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "cell":
            out[(int(r["x"]), int(r["y"]))] = r
    return out

def rendered_sig(r: dict[str, Any] | None) -> tuple[Any, ...]:
    if not r:
        return (None,)
    glyph = r.get("final_gid", None)
    if glyph in (None, 0):
        glyph = r.get("gid", r.get("glyph", r.get("cp437")))
    return (
        glyph, r.get("fg"), r.get("bk"),
        r.get("r"), r.get("g"), r.get("b"), r.get("a"),
    )

def bridge_sig(r: dict[str, Any] | None) -> tuple[Any, ...]:
    if not r:
        return (None,)
    return (
        r.get("material_id"), r.get("dispatch_surface"), r.get("winner_gid"),
        r.get("ramp"), r.get("density"), r.get("vertical_relation"),
        r.get("direction"), r.get("flow"), r.get("fact_mask"),
        r.get("axis_routing"), r.get("candidate_lane"),
        r.get("route_candidate_count"), r.get("route_reason"),
    )

def diff_keys(a: dict[tuple[int, int], dict[str, Any]], b: dict[tuple[int, int], dict[str, Any]], sig) -> list[tuple[int, int]]:
    return [k for k in sorted(set(a) | set(b), key=lambda p: (p[1], p[0])) if sig(a.get(k)) != sig(b.get(k))]

def classify(keys: list[tuple[int, int]], before_bridge: dict[tuple[int, int], dict[str, Any]]) -> Counter[str]:
    c: Counter[str] = Counter()
    for k in keys:
        b = before_bridge.get(k, {})
        mat = int(b.get("material_id", -999))
        surf = int(b.get("dispatch_surface", -1))
        vr = int(b.get("vertical_relation", -999))
        fm = int(b.get("fact_mask", 0))
        if mat == MAT and surf == 1 and vr == VR and (fm & 1):
            c["selected_fact"] += 1
        elif mat == MAT and surf == 1:
            c["selected_nonmatching"] += 1
        elif mat >= 0:
            c["other_material"] += 1
        else:
            c["nonmaterial"] += 1
    return c

def counter_pairs(counter: Counter[Any]) -> list[list[Any]]:
    return [[k, v] for k, v in sorted(counter.items(), key=lambda item: str(item[0]))]

def load_font(size: int) -> ImageFont.ImageFont:
    for path in [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()

def fit_image(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    scale = min(box_w / max(1, img.width), box_h / max(1, img.height))
    out_w = max(1, int(img.width * scale))
    out_h = max(1, int(img.height * scale))
    return img.resize((out_w, out_h), Image.Resampling.LANCZOS)

def draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                 font: ImageFont.ImageFont, fill: tuple[int, int, int],
                 width_px: int, line_gap: int = 4) -> int:
    x, y = xy
    words = text.split()
    line = ""
    for word in words:
        trial = word if not line else f"{line} {word}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= width_px:
            line = trial
            continue
        if line:
            draw.text((x, y), line, font=font, fill=fill)
            y += font.size + line_gap
        line = word
    if line:
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + line_gap
    return y

def create_operator_sheet(summary: dict[str, Any], out_path: Path) -> None:
    paths = summary.get("paths", {})
    before_png = paths.get("before_termpp_png")
    after_png = paths.get("after_termpp_png")
    no_action_png = paths.get("no_action_termpp_png")
    after_ui = paths.get("after_ui_frame")
    if not before_png or not after_png:
        return

    images: list[tuple[str, Image.Image]] = []
    for label, rel in [
        ("Before Edge lane edit", before_png),
        ("After Edge lane edit", after_png),
        ("No-action stability frame", no_action_png),
        ("ASCIIID Material Look UI", after_ui),
    ]:
        if not rel:
            continue
        p = ROOT / rel
        if not p.exists():
            continue
        images.append((label, Image.open(p).convert("RGB")))

    title_font = load_font(28)
    body_font = load_font(18)
    small_font = load_font(15)
    w = 2200
    margin = 34
    header_h = 270
    thumb_w = (w - margin * 3) // 2
    thumb_h = 520
    rows = (len(images) + 1) // 2
    h = header_h + rows * (thumb_h + 70) + margin
    sheet = Image.new("RGB", (w, h), (18, 20, 22))
    draw = ImageDraw.Draw(sheet)
    draw.rectangle((0, 0, w, header_h), fill=(30, 34, 38))
    draw.text((margin, 24), "FL-4260 RQ-153d Edge Headed UI Proof", font=title_font, fill=(245, 245, 245))
    lane = summary.get("vertical_relation_lane")
    mat = summary.get("material")
    proof_lines = [
        f"Selected material: terrain:{mat}",
        f"Visible selected-material terrain cells: {summary.get('visible_selected_material_cells')}",
        f"Predicted vertical_relation lane {lane} cells: {summary.get('predicted_vertical_relation_lane_cells')}",
        f"Changed selected lane cells: {summary.get('selected_fact_rendered_changes')}",
        f"Same-material nonmatching changed: {summary.get('selected_nonmatching_rendered_changes')}",
        f"Other-material changed: {summary.get('other_material_rendered_changes')}",
        f"Nonmaterial changed: {summary.get('nonmaterial_rendered_changes')}",
        f"No-action drift: {summary.get('no_action_rendered_changes')}",
        f"Verdict: {summary.get('verdict')}",
    ]
    y = 70
    for line in proof_lines:
        draw.text((margin, y), line, font=body_font, fill=(220, 228, 235))
        y += 25
    contract = ("Contract: author Edge bucket for the selected material, then only terrain cells "
                "with that material and the predicted vertical_relation lane may change.")
    draw_wrapped(draw, (980, 74), contract, body_font, (220, 228, 235), 1120)

    for idx, (label, img) in enumerate(images):
        col = idx % 2
        row = idx // 2
        x = margin + col * (thumb_w + margin)
        y = header_h + row * (thumb_h + 70)
        draw.text((x, y + 12), label, font=body_font, fill=(245, 245, 245))
        fitted = fit_image(img, thumb_w, thumb_h)
        px = x + (thumb_w - fitted.width) // 2
        py = y + 46 + (thumb_h - fitted.height) // 2
        draw.rectangle((x, y + 44, x + thumb_w, y + 44 + thumb_h), outline=(72, 82, 92), width=2)
        sheet.paste(fitted, (px, py))
        rel = paths.get({
            "Before Edge lane edit": "before_termpp_png",
            "After Edge lane edit": "after_termpp_png",
            "No-action stability frame": "no_action_termpp_png",
            "ASCIIID Material Look UI": "after_ui_frame",
        }[label])
        draw_wrapped(draw, (x, y + 52 + thumb_h), rel or "", small_font, (170, 178, 185), thumb_w)
    sheet.save(out_path)

def write_observe_tuple() -> Path:
    p = OUT / "observe_view_tuple.json"
    p.write_text(json.dumps({
        "camera": {
            "pos": [64, 64, 40960],
            "yaw": 45,
            "zoom": 1.0,
            "perspective": True,
            "scene_shift": 0,
        },
        "light": {
            "dir": [0.0, -1.0, -1.0],
            "ambience": 0.35,
        },
        "water": 55,
    }, indent=2), encoding="utf-8")
    return p

def wait_path(path: Path, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.25)
    raise TimeoutError(f"missing output {path}")

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the FL-4260 RQ-153d strict Edge-lane CDP proof."
    )
    parser.add_argument("--out", type=Path, default=OUT, help="proof output directory")
    parser.add_argument("--port", type=int, default=PORT, help="ASCIIID CDP port")
    parser.add_argument("--material", type=int, default=MAT, help="selected terrain material id")
    parser.add_argument("--vertical-relation", type=int, default=VR, help="Edge vertical-relation lane")
    parser.add_argument("--glyph-id", type=int, default=GID, help="accepted Edge candidate GlyphId")
    parser.add_argument("--rejected-vertical-relation", type=int, default=REJECTED_VR,
                        help="negative-check vertical-relation lane")
    parser.add_argument("--rejected-glyph-id", type=int, default=REJECTED_GID,
                        help="rejected FL-4208 candidate GlyphId expected to admit zero candidates")
    parser.add_argument("--term-camera", default=TERM_CAMERA, help="SET_TERMPP_CAMERA_VIEW argument string")
    parser.add_argument("--weather", default=WEATHER, help="SET_WEATHER argument")
    parser.add_argument("--map", dest="map_path", default=MAP_PATH,
                        help="optional LOAD_MAP path before TERM++ setup")
    parser.add_argument("--build-edge-fixture", action="store_true",
                        help="use existing EDIT terrain commands to create a visible selected-material height step before capture")
    parser.add_argument("--fixture-stamp-alpha", type=float, default=FIXTURE_STAMP_ALPHA,
                        help="STAMP alpha for --build-edge-fixture")
    parser.add_argument("--fixture-stamp-radius", type=float, default=FIXTURE_STAMP_RADIUS,
                        help="STAMP radius for --build-edge-fixture")
    parser.add_argument("--fixture-kind",
                        choices=[
                            "stamp", "crater", "grid-rise", "grid-fall",
                            "grid-ridge", "grid-valley", "grid-wall",
                            "grid-ridge-heavy", "grid-valley-heavy",
                        ],
                        default=FIXTURE_KIND,
                        help="fixture shape for --build-edge-fixture")
    parser.add_argument("--headed", action=argparse.BooleanOptionalAction, default=True,
                        help="capture same-frame TERM++ PNGs and ASCIIID UI frames")
    parser.add_argument("--scan-lanes", action="store_true",
                        help="only scan visible vertical_relation counts for the current camera")
    return parser.parse_args(argv)

def rect_cells(cx: int, cy: int, radius: int) -> list[tuple[int, int]]:
    return [(x, y) for y in range(cy - radius, cy + radius + 1)
            for x in range(cx - radius, cx + radius + 1)]

def batch_cells_command(method: str, prefix: str, points: list[tuple[int, int]]) -> tuple[str, str]:
    coords = " ".join(f"{x} {y}" for x, y in points)
    return method, f"{prefix} {len(points)} {coords}"

def build_edge_fixture(cdp: CDP, transcript: list[dict[str, str]]) -> None:
    # Uses existing EDIT-owned terrain/material commands only. This fixture is
    # proof setup for source facts; it does not write Material Look profile state.
    center_x = 64
    center_y = 64
    setup: list[tuple[str, str]] = [("SET_TERRAIN_OVERVIEW", "0")]
    if FIXTURE_KIND in ("grid-ridge-heavy", "grid-valley-heavy"):
        center_px = center_x // 8
        center_py = center_y // 8
        patch_radius = 1
        material_cells: list[tuple[int, int]] = []
        patch_coords: list[tuple[int, int]] = []
        for py in range(center_py - patch_radius, center_py + patch_radius + 1):
            for px in range(center_px - patch_radius, center_px + patch_radius + 1):
                patch_coords.append((px, py))
                material_cells.extend(
                    (x, y)
                    for y in range(py * 8, py * 8 + 8)
                    for x in range(px * 8, px * 8 + 8)
                )
        setup.append(batch_cells_command("BATCH_SET_CELLS", "1", material_cells))
        for px, py in patch_coords:
            for hy in range(5):
                for hx in range(5):
                    center_plateau = (1 <= hx <= 3 and 1 <= hy <= 3)
                    if FIXTURE_KIND == "grid-ridge-heavy":
                        h = 50000 if center_plateau else 30000
                    else:
                        h = 30000 if center_plateau else 50000
                    setup.append(("SET_HEIGHT_CELL", f"{px} {py} {hx} {hy} {h}"))
    elif FIXTURE_KIND.startswith("grid-"):
        px = center_x // 8
        py = center_y // 8
        patch_cells = [(x, y) for y in range(py * 8, py * 8 + 8)
                       for x in range(px * 8, px * 8 + 8)]
        setup.append(batch_cells_command("BATCH_SET_CELLS", "1", patch_cells))
        row_patterns = {
            "grid-rise":   [128, 176, 224, 272, 320],
            "grid-fall":   [320, 272, 224, 176, 128],
            "grid-ridge":  [128, 176, 320, 176, 128],
            "grid-valley": [320, 176, 128, 176, 320],
            "grid-wall":   [128, 128, 320, 320, 320],
        }
        rows = row_patterns[FIXTURE_KIND]
        for hy, h in enumerate(rows):
            for hx in range(5):
                setup.append(("SET_HEIGHT_CELL", f"{px} {py} {hx} {hy} {h}"))
    elif FIXTURE_KIND == "crater":
        outer = max(FIXTURE_STAMP_RADIUS + 10.0, 28.0)
        setup.extend([
            ("STAMP", f"{center_x} {center_y} {outer:.3f} 0.500 1"),
            ("STAMP", f"{center_x} {center_y} {FIXTURE_STAMP_RADIUS:.3f} -0.500 1"),
        ])
    else:
        setup.append(("STAMP", f"{center_x} {center_y} {FIXTURE_STAMP_RADIUS:.3f} {FIXTURE_STAMP_ALPHA:.3f} 1"))
    for method, params in setup:
        res = cdp.call(method, params, timeout=90.0)
        transcript.append({"method": method, "params": params[:2000], "result": res[-2000:]})
        if method in ("STAMP", "BATCH_SET_CELLS", "BATCH_ELEV_DELTA"):
            time.sleep(0.5)
    time.sleep(2.0)

def setup_scene(cdp: CDP, transcript: list[dict[str, str]], build_fixture: bool = False) -> Path:
    view_tuple = write_observe_tuple()
    if MAP_PATH:
        res = cdp.call("LOAD_MAP", MAP_PATH, timeout=90.0)
        transcript.append({"method": "LOAD_MAP", "params": MAP_PATH, "result": res[-2000:]})
        time.sleep(1.0)
    if build_fixture:
        build_edge_fixture(cdp, transcript)
    setup = [
        ("FL4260_SET_OBSERVE_RENDER", f"{OUT} {view_tuple} fl4260-rq153d-edge-strict"),
        ("SET_WEATHER", WEATHER),
        ("OPEN_TERMPP_CURRENT_VIEW", ""),
        ("FL4260_SET_RENDER_MODE", "1"),
        ("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA),
    ]
    for method, params in setup:
        res = cdp.call(method, params, timeout=90.0)
        transcript.append({"method": method, "params": params, "result": res[-2000:]})
        time.sleep(1.0)
    time.sleep(2.0)
    return view_tuple

def dump(cdp: CDP, label: str, transcript: list[dict[str, str]]) -> tuple[Path, Path]:
    bridge = OUT / f"strict_{label}.bridge.jsonl"
    rendered = OUT / f"strict_{label}.rendered.jsonl"
    for p in [bridge, rendered]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    res = cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(bridge), timeout=60.0)
    transcript.append({"method": "FL4260_DUMP_BRIDGE_CELLS", "params": str(bridge), "result": res[-1200:]})
    wait_path(bridge)
    res = cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(rendered), timeout=60.0)
    transcript.append({"method": "FL4207_DUMP_TERMPP_RENDERED_BUFFER", "params": str(rendered), "result": res[-1200:]})
    wait_path(rendered)
    return bridge, rendered

def capture_termpp_bundle(cdp: CDP, label: str, transcript: list[dict[str, str]]) -> tuple[Path, Path, Path]:
    png = OUT / f"strict_{label}.termpp.png"
    bridge = OUT / f"strict_{label}.bridge.jsonl"
    rendered = OUT / f"strict_{label}.rendered.jsonl"
    for p in [png, bridge, rendered]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    res = cdp.call("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {rendered} {bridge}", timeout=90.0)
    transcript.append({"method": "CAPTURE_TERMPP_FRAME_WITH_BUFFER", "params": f"{png} {rendered} {bridge}", "result": res[-1200:]})
    wait_path(png)
    wait_path(rendered)
    wait_path(bridge)
    return bridge, rendered, png

def capture_ui(cdp: CDP, label: str, transcript: list[dict[str, str]]) -> Path:
    ui_dir = OUT / f"strict_{label}_ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    ui_path = ui_dir / "ui_frame.png"
    try:
        ui_path.unlink()
    except FileNotFoundError:
        pass
    res = cdp.call("FL4260_RENDERING_PROOF", f"{MAT} -1 1", timeout=60.0)
    transcript.append({"method": "FL4260_RENDERING_PROOF", "params": f"{MAT} -1 1", "result": res[-1200:]})
    time.sleep(0.5)
    res = cdp.call("CAPTURE_UI_FRAME", str(ui_dir), timeout=60.0)
    transcript.append({"method": "CAPTURE_UI_FRAME", "params": str(ui_dir), "result": res[-1200:]})
    wait_path(ui_path)
    return ui_path

def summarize_vertical_relations(bridge_path: Path, material: int) -> dict[str, Any]:
    rows = cells(read_jsonl(bridge_path))
    selected: Counter[int] = Counter()
    all_terrain: Counter[int] = Counter()
    material_counts: Counter[int] = Counter()
    for r in rows.values():
        mat = int(r.get("material_id", -999))
        surf = int(r.get("dispatch_surface", -1))
        fact_mask = int(r.get("fact_mask", 0))
        if surf != 1 or not (fact_mask & 1):
            continue
        vr = int(r.get("vertical_relation", -999))
        all_terrain[vr] += 1
        if mat >= 0:
            material_counts[mat] += 1
        if mat == material:
            selected[vr] += 1
    return {
        "material": material,
        "term_camera": TERM_CAMERA,
        "map": MAP_PATH,
        "selected_material_vertical_relation_counts": counter_pairs(selected),
        "all_terrain_vertical_relation_counts": counter_pairs(all_terrain),
        "terrain_material_counts": counter_pairs(material_counts),
        "bridge_path": str(bridge_path.relative_to(ROOT)),
    }

def pool_edge_len(text: str) -> int:
    needle = f"bucket[{8 + VR}] edge[{VR}]"
    for line in text.splitlines():
        if needle in line and " len=" in line:
            return int(line.rsplit(" len=", 1)[1].split()[0])
    return -1

def main(argv: list[str] | None = None) -> int:
    global OUT, PORT, MAT, VR, GID, REJECTED_VR, REJECTED_GID, TERM_CAMERA, WEATHER, MAP_PATH
    global FIXTURE_STAMP_ALPHA, FIXTURE_STAMP_RADIUS, FIXTURE_KIND
    args = parse_args(sys.argv[1:] if argv is None else argv)
    OUT = args.out if args.out.is_absolute() else (ROOT / args.out)
    PORT = args.port
    MAT = args.material
    VR = args.vertical_relation
    GID = args.glyph_id
    REJECTED_VR = args.rejected_vertical_relation
    REJECTED_GID = args.rejected_glyph_id
    TERM_CAMERA = args.term_camera
    WEATHER = args.weather
    MAP_PATH = args.map_path
    FIXTURE_STAMP_ALPHA = args.fixture_stamp_alpha
    FIXTURE_STAMP_RADIUS = args.fixture_stamp_radius
    FIXTURE_KIND = args.fixture_kind
    OUT.mkdir(parents=True, exist_ok=True)
    stale_non_strict = [
        "edge_lane_result.json",
        "edge_lane_scoped_summary.json",
        "edge_lane_transcript.json",
        "edge_before.bridge.jsonl",
        "edge_before.rendered.jsonl",
        "edge_after.bridge.jsonl",
        "edge_after.rendered.jsonl",
        "before.bridge.jsonl",
        "before.rendered.jsonl",
        "no_action_a.bridge.jsonl",
        "no_action_a.rendered.jsonl",
        "no_action_b.bridge.jsonl",
        "no_action_b.rendered.jsonl",
        "no_action_drift_result.json",
    ]
    for name in stale_non_strict:
        try:
            (OUT / name).unlink()
        except FileNotFoundError:
            pass
    strict_outputs = [
        "strict_before.bridge.jsonl", "strict_before.rendered.jsonl",
        "strict_after.bridge.jsonl", "strict_after.rendered.jsonl",
        "strict_no_action_after.bridge.jsonl", "strict_no_action_after.rendered.jsonl",
        "strict_before.termpp.png", "strict_after.termpp.png", "strict_no_action_after.termpp.png",
        "edge_lane_strict_observe_transcript.json", "edge_lane_strict_summary.json",
        "edge_lane_strict_observe_result.json",
    ]
    scan_outputs = [
        "strict_lane_scan.bridge.jsonl", "strict_lane_scan.rendered.jsonl",
        "strict_lane_scan.termpp.png", "edge_lane_vertical_relation_scan.json",
        "edge_lane_vertical_relation_scan_transcript.json",
    ]
    for name in (scan_outputs if args.scan_lanes else strict_outputs + scan_outputs):
        try:
            (OUT / name).unlink()
        except FileNotFoundError:
            pass
    if not args.scan_lanes:
        for name in ["strict_before_ui", "strict_after_ui", "strict_no_action_after_ui"]:
            try:
                (OUT / name / "ui_frame.png").unlink()
            except FileNotFoundError:
                pass
    proc = start_asciiid()
    transcript: list[dict[str, str]] = []
    cdp = CDP(PORT)
    try:
        setup_scene(cdp, transcript, args.build_edge_fixture)
        if args.scan_lanes:
            scan_bridge_path, scan_rendered_path, scan_png_path = capture_termpp_bundle(cdp, "lane_scan", transcript)
            scan = summarize_vertical_relations(scan_bridge_path, MAT)
            scan["termpp_png"] = str(scan_png_path.relative_to(ROOT))
            scan["rendered_path"] = str(scan_rendered_path.relative_to(ROOT))
            scan["verdict"] = "SCAN_ONLY"
            (OUT / "edge_lane_vertical_relation_scan.json").write_text(json.dumps(scan, indent=2), encoding="utf-8")
            (OUT / "edge_lane_vertical_relation_scan_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
            print(json.dumps(scan, indent=2))
            return 0
        pools_before = cdp.call("FL4260_DUMP_BUCKET_POOLS", str(MAT), timeout=60.0)
        transcript.append({"method": "FL4260_DUMP_BUCKET_POOLS", "params": str(MAT), "result": pools_before[-3000:]})
        rejected_res = cdp.call("FL4260_SET_EDGE_BUCKET", f"{MAT} {REJECTED_VR} {REJECTED_GID}", timeout=60.0)
        transcript.append({"method": "FL4260_SET_EDGE_BUCKET", "params": f"{MAT} {REJECTED_VR} {REJECTED_GID}", "result": rejected_res[-2000:]})
        before_ui_path = None
        after_ui_path = None
        no_action_ui_path = None
        before_png_path = None
        after_png_path = None
        no_action_png_path = None
        if args.headed:
            before_bridge_path, before_rendered_path, before_png_path = capture_termpp_bundle(cdp, "before", transcript)
            before_ui_path = capture_ui(cdp, "before", transcript)
        else:
            before_bridge_path, before_rendered_path = dump(cdp, "before", transcript)
        set_res = cdp.call("FL4260_SET_EDGE_BUCKET", f"{MAT} {VR} {GID}", timeout=60.0)
        transcript.append({"method": "FL4260_SET_EDGE_BUCKET", "params": f"{MAT} {VR} {GID}", "result": set_res[-2000:]})
        time.sleep(2.0)
        pools_after = cdp.call("FL4260_DUMP_BUCKET_POOLS", str(MAT), timeout=60.0)
        transcript.append({"method": "FL4260_DUMP_BUCKET_POOLS", "params": str(MAT), "result": pools_after[-3000:]})
        if args.headed:
            after_bridge_path, after_rendered_path, after_png_path = capture_termpp_bundle(cdp, "after", transcript)
            after_ui_path = capture_ui(cdp, "after", transcript)
        else:
            after_bridge_path, after_rendered_path = dump(cdp, "after", transcript)
        time.sleep(1.0)
        if args.headed:
            no_action_bridge_path, no_action_rendered_path, no_action_png_path = capture_termpp_bundle(cdp, "no_action_after", transcript)
            no_action_ui_path = capture_ui(cdp, "no_action_after", transcript)
        else:
            no_action_bridge_path, no_action_rendered_path = dump(cdp, "no_action_after", transcript)
    finally:
        cdp.close()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
    (OUT / "edge_lane_strict_observe_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    bb = cells(read_jsonl(before_bridge_path))
    br = cells(read_jsonl(before_rendered_path))
    ab = cells(read_jsonl(after_bridge_path))
    ar = cells(read_jsonl(after_rendered_path))
    nb = cells(read_jsonl(no_action_bridge_path))
    nr = cells(read_jsonl(no_action_rendered_path))
    render_changed = diff_keys(br, ar, rendered_sig)
    bridge_changed = diff_keys(bb, ab, bridge_sig)
    no_action_render_changed = diff_keys(ar, nr, rendered_sig)
    no_action_bridge_changed = diff_keys(ab, nb, bridge_sig)
    render_class = classify(render_changed, bb)
    bridge_class = classify(bridge_changed, bb)
    selected_fact_keys = [k for k, b in bb.items()
                          if int(b.get("material_id", -999)) == MAT
                          and int(b.get("dispatch_surface", -1)) == 1
                          and int(b.get("vertical_relation", -999)) == VR
                          and (int(b.get("fact_mask", 0)) & 1)]
    changed_after_rows = [ab[k] for k in render_changed if k in ab]
    route_explanation = {
        "axis_routing": Counter(str(r.get("axis_routing")) for r in changed_after_rows),
        "candidate_lane": Counter(str(r.get("candidate_lane")) for r in changed_after_rows),
        "route_candidate_count": Counter(int(r.get("route_candidate_count", -1)) for r in changed_after_rows),
        "route_reason": Counter(str(r.get("route_reason")) for r in changed_after_rows),
        "after_selected_fact_final_gid": Counter(int(ar[k].get("final_gid", -1)) for k in render_changed if k in ar),
    }
    changed_scope = {
        "material_id": Counter(int(r.get("material_id", -999)) for r in changed_after_rows),
        "dispatch_surface": Counter(int(r.get("dispatch_surface", -1)) for r in changed_after_rows),
        "vertical_relation": Counter(int(r.get("vertical_relation", -999)) for r in changed_after_rows),
        "fact_mask": Counter(int(r.get("fact_mask", 0)) for r in changed_after_rows),
    }
    selected_terrain_keys = [k for k, b in bb.items()
                             if int(b.get("material_id", -999)) == MAT
                             and int(b.get("dispatch_surface", -1)) == 1]
    material_keys = [k for k, b in bb.items()
                     if int(b.get("material_id", -999)) >= 0]
    unchanged_reason_counts = {
        "selected_matching_edge_cells_changed": len(selected_fact_keys),
        "selected_nonmatching_cells_keep_previous_route": max(0, len(selected_terrain_keys) - len(selected_fact_keys)),
        "other_material_cells_unchanged": max(0, len(material_keys) - len(selected_terrain_keys)),
        "nonmaterial_cells_unchanged": max(0, len(bb) - len(material_keys)),
    }
    serial_route = {k: counter_pairs(v) for k, v in route_explanation.items()}
    serial_scope = {k: counter_pairs(v) for k, v in changed_scope.items()}
    before_len = pool_edge_len(pools_before)
    after_len = pool_edge_len(pools_after)
    verdict = (
        render_class["selected_fact"] == len(selected_fact_keys) and
        render_class["selected_nonmatching"] == 0 and
        render_class["other_material"] == 0 and
        render_class["nonmaterial"] == 0 and
        bridge_class["other_material"] == 0 and
        len(no_action_render_changed) == 0 and
        len(no_action_bridge_changed) == 0 and
        "accepted=0" in rejected_res and
        before_len == 0 and after_len == 1 and "accepted=1" in set_res
    )
    summary = {
        "material": MAT,
        "vertical_relation_lane": VR,
        "edge_gids": [GID],
        "proof_contract": (
            "Only selected-material terrain cells whose vertical_relation equals the edited Edge lane "
            "and whose fact_mask carries vertical_relation may change."
        ),
        "observe_render": True,
        "headed_capture": bool(args.headed),
        "weather": int(WEATHER),
        "map": MAP_PATH,
        "visible_selected_material_cells": len(selected_terrain_keys),
        "predicted_vertical_relation_lane_cells": len(selected_fact_keys),
        "selected_fact_cells": len(selected_fact_keys),
        "selected_fact_rendered_changes": render_class["selected_fact"],
        "selected_nonmatching_rendered_changes": render_class["selected_nonmatching"],
        "other_material_rendered_changes": render_class["other_material"],
        "nonmaterial_rendered_changes": render_class["nonmaterial"],
        "bridge_changed_selected_fact": bridge_class["selected_fact"],
        "bridge_changed_other_material": bridge_class["other_material"],
        "no_action_rendered_changes": len(no_action_render_changed),
        "no_action_bridge_changes": len(no_action_bridge_changed),
        "route_explanation": serial_route,
        "changed_cell_scope": serial_scope,
        "unchanged_reason_counts": unchanged_reason_counts,
        "bucket_before_after": f"edge[{VR}] len {before_len} -> {after_len}, accepted={'1' if 'accepted=1' in set_res else '0'}",
        "rejected_candidate_check": {
            "vertical_relation_lane": REJECTED_VR,
            "glyph_id": REJECTED_GID,
            "accepted": 0 if "accepted=0" in rejected_res else -1,
            "result": rejected_res,
        },
        "set_edge_result": set_res,
        "paths": {
            "before_bridge": str(before_bridge_path.relative_to(ROOT)),
            "before_rendered": str(before_rendered_path.relative_to(ROOT)),
            "after_bridge": str(after_bridge_path.relative_to(ROOT)),
            "after_rendered": str(after_rendered_path.relative_to(ROOT)),
            "no_action_bridge": str(no_action_bridge_path.relative_to(ROOT)),
            "no_action_rendered": str(no_action_rendered_path.relative_to(ROOT)),
            "strict_result": str((OUT / "edge_lane_strict_observe_result.json").relative_to(ROOT)),
            "strict_transcript": str((OUT / "edge_lane_strict_observe_transcript.json").relative_to(ROOT)),
        },
        "supersedes": "edge_lane_scoped_summary.json drift-classified non-observe proof",
        "verdict": "PASS_STRICT_SELECTED_EDGE_AND_OTHER_MATERIAL_STABLE" if verdict else "FAIL_STRICT_EDGE_CONTRACT",
    }
    if args.headed:
        summary["paths"].update({
            "before_termpp_png": str(before_png_path.relative_to(ROOT)) if before_png_path else None,
            "after_termpp_png": str(after_png_path.relative_to(ROOT)) if after_png_path else None,
            "no_action_termpp_png": str(no_action_png_path.relative_to(ROOT)) if no_action_png_path else None,
            "before_ui_frame": str(before_ui_path.relative_to(ROOT)) if before_ui_path else None,
            "after_ui_frame": str(after_ui_path.relative_to(ROOT)) if after_ui_path else None,
            "no_action_ui_frame": str(no_action_ui_path.relative_to(ROOT)) if no_action_ui_path else None,
        })
        operator_json = OUT / "operator_facing_ui_acceptance.json"
        operator_sheet = OUT / "operator_facing_ui_proof_sheet.png"
        summary["paths"]["operator_acceptance_json"] = str(operator_json.relative_to(ROOT))
        summary["paths"]["operator_proof_sheet_png"] = str(operator_sheet.relative_to(ROOT))
        operator_acceptance = {
            "requirement": "Headed ASCIIID UI proof for FL-4260 Edge Material Look lane exposure",
            "selected_material": f"terrain:{MAT}",
            "visible_selected_material_cells": len(selected_terrain_keys),
            "predicted_vertical_relation_lane": VR,
            "predicted_vertical_relation_lane_cells": len(selected_fact_keys),
            "action_attempted": f"FL4260_SET_EDGE_BUCKET terrain:{MAT} edge[{VR}] GlyphId {GID}",
            "expected_result_before_action": (
                "Edge bucket is empty for the lane; selected material cells in the predicted lane have no Edge candidate."
            ),
            "actual_result_after_action": (
                f"{render_class['selected_fact']} selected-material lane cells changed; "
                f"{render_class['selected_nonmatching']} same-material nonmatching cells changed; "
                f"{render_class['other_material']} other-material cells changed; "
                f"{render_class['nonmaterial']} nonmaterial cells changed."
            ),
            "no_action_stability": {
                "rendered_changes": len(no_action_render_changed),
                "bridge_changes": len(no_action_bridge_changed),
            },
            "rejected_candidate_check": summary["rejected_candidate_check"],
            "verdict": summary["verdict"],
            "evidence": {
                "before_termpp_png": summary["paths"].get("before_termpp_png"),
                "after_termpp_png": summary["paths"].get("after_termpp_png"),
                "no_action_termpp_png": summary["paths"].get("no_action_termpp_png"),
                "after_ui_frame": summary["paths"].get("after_ui_frame"),
                "strict_summary": str((OUT / "edge_lane_strict_summary.json").relative_to(ROOT)),
                "strict_transcript": summary["paths"].get("strict_transcript"),
            },
        }
        operator_json.write_text(json.dumps(operator_acceptance, indent=2), encoding="utf-8")
        create_operator_sheet(summary, operator_sheet)
    (OUT / "edge_lane_strict_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "edge_lane_strict_observe_result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if verdict else 1

if __name__ == "__main__":
    sys.exit(main())
