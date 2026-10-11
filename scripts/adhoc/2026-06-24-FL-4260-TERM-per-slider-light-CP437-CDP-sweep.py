# Ad hoc script: FL-4260 TERM++ per-slider light CP437 CDP sweep
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOISE_CELL_LIMIT = 10
MIN_LIGHT_RESPONSE_CELLS = 100


def pulse_window(pid: int) -> None:
    if pid <= 0:
        return
    subprocess.run([
        "osascript",
        "-e", f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def recv_line(sock: socket.socket, timeout_s: float, pid: int) -> str:
    deadline = time.time() + timeout_s
    buf = b""
    while time.time() < deadline:
        pulse_window(pid)
        sock.settimeout(min(1.0, max(0.05, deadline - time.time())))
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            raise RuntimeError("CDP socket closed")
        buf += chunk
        if b"\n" in buf:
            line, _rest = buf.split(b"\n", 1)
            return line.decode("utf-8", errors="replace").strip()
    raise TimeoutError("timed out waiting for CDP response")


def send_command(sock: socket.socket, seq: int, method: str, params: str, pid: int, timeout_s: float) -> dict[str, Any]:
    payload = json.dumps({"id": seq, "method": method, "params": params}) + "\n"
    sock.sendall(payload.encode("utf-8"))
    line = recv_line(sock, timeout_s, pid)
    msg = json.loads(line)
    if msg.get("id") != seq:
        raise RuntimeError(f"unexpected CDP id: expected {seq}, got {msg.get('id')}: {msg}")
    return msg


def wait_files(paths: list[Path], pid: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pulse_window(pid)
        if all(p.exists() and p.stat().st_size > 0 for p in paths):
            return
        time.sleep(0.25)
    missing = [str(p) for p in paths if not (p.exists() and p.stat().st_size > 0)]
    raise TimeoutError(f"timed out waiting for files: {missing}")


def read_cells(path: Path) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    header: dict[str, Any] = {}
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            obj = json.loads(text)
            if obj.get("kind") == "header":
                header = obj
            elif obj.get("kind") == "cell":
                cells[(int(obj["x"]), int(obj["y"]))] = obj
    return header, cells


def diff_case(base_bridge: Path, base_render: Path, case_bridge: Path, case_render: Path) -> dict[str, Any]:
    _bh, bc = read_cells(base_bridge)
    _ch, cc = read_cells(case_bridge)
    _brh, br = read_cells(base_render)
    _crh, cr = read_cells(case_render)
    shared = sorted(set(bc) & set(cc))
    out: dict[str, Any] = {
        "shared_bridge_cells": len(shared),
        "material_id_changed": 0,
        "dispatch_surface_changed": 0,
        "resolve_elev_changed": 0,
        "resolve_shade_changed": 0,
        "sample_diffuses_changed": 0,
        "terrain_cells": 0,
        "terrain_resolve_shade_changed": 0,
        "shade_hist_before": Counter(),
        "shade_hist_after": Counter(),
    }
    for key in shared:
        a = bc[key]
        b = cc[key]
        if a.get("material_id") != b.get("material_id"):
            out["material_id_changed"] += 1
        if a.get("dispatch_surface") != b.get("dispatch_surface"):
            out["dispatch_surface_changed"] += 1
        if a.get("resolve_elev") != b.get("resolve_elev"):
            out["resolve_elev_changed"] += 1
        if a.get("resolve_shade") != b.get("resolve_shade"):
            out["resolve_shade_changed"] += 1
        if a.get("sample_diffuses") != b.get("sample_diffuses"):
            out["sample_diffuses_changed"] += 1
        if a.get("dispatch_surface") == 1 and b.get("dispatch_surface") == 1:
            out["terrain_cells"] += 1
            out["shade_hist_before"][a.get("resolve_shade")] += 1
            out["shade_hist_after"][b.get("resolve_shade")] += 1
            if a.get("resolve_shade") != b.get("resolve_shade"):
                out["terrain_resolve_shade_changed"] += 1
    shared_render = sorted(set(br) & set(cr))
    out.update({
        "shared_rendered_cells": len(shared_render),
        "fg_changed": 0,
        "bg_changed": 0,
        "cp437_changed": 0,
        "final_gid_changed": 0,
    })
    for key in shared_render:
        a = br[key]
        b = cr[key]
        if a.get("fg") != b.get("fg"):
            out["fg_changed"] += 1
        if a.get("bk") != b.get("bk"):
            out["bg_changed"] += 1
        if a.get("cp437") != b.get("cp437"):
            out["cp437_changed"] += 1
        if a.get("final_gid") != b.get("final_gid"):
            out["final_gid_changed"] += 1
    out["shade_hist_before"] = dict(out["shade_hist_before"].most_common())
    out["shade_hist_after"] = dict(out["shade_hist_after"].most_common())
    out["scope_noise_limit"] = NOISE_CELL_LIMIT
    out["min_light_response_cells"] = MIN_LIGHT_RESPONSE_CELLS
    out["final_changed_total"] = (
        out["fg_changed"] + out["bg_changed"] + out["cp437_changed"] + out["final_gid_changed"]
    )
    out["scope_noise_ok"] = (
        out["material_id_changed"] <= NOISE_CELL_LIMIT and
        out["dispatch_surface_changed"] <= NOISE_CELL_LIMIT and
        out["resolve_elev_changed"] == 0
    )
    out["pass"] = (
        out["terrain_cells"] > 0 and
        out["sample_diffuses_changed"] >= MIN_LIGHT_RESPONSE_CELLS and
        out["terrain_resolve_shade_changed"] >= MIN_LIGHT_RESPONSE_CELLS and
        out["final_changed_total"] >= MIN_LIGHT_RESPONSE_CELLS and
        out["scope_noise_ok"]
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-dir", required=True)
    ap.add_argument("--map", default="assets/a3d/game_map_y8_original_game_map.a3d")
    ap.add_argument("--camera", default="64 64 57 45 0")
    ap.add_argument("--term-camera", default="64 64 57 45 0 10 0")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--settle-s", type=float, default=1.0)
    args = ap.parse_args()

    started_at_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    art = Path(args.artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    cases = [
        ("base", "30 45 12 0.50"),
        ("pitch85", "85 45 12 0.50"),
        ("yaw_minus135", "30 -135 12 0.50"),
        ("time06", "30 45 6 0.50"),
        ("ambient100", "30 45 12 1.00"),
    ]
    transcript: list[dict[str, Any]] = []
    seq = 1
    sock = socket.create_connection((args.host, args.port), timeout=30.0)
    try:
        def run(method: str, params: str = "", timeout_s: float = 120.0) -> dict[str, Any]:
            nonlocal seq
            t0 = time.time()
            msg = send_command(sock, seq, method, params, args.pid, timeout_s)
            transcript.append({
                "id": seq,
                "method": method,
                "params": params,
                "ms": int((time.time() - t0) * 1000),
                "result": str(msg.get("result", msg))[:2000],
            })
            seq += 1
            return msg

        def capture(label: str) -> tuple[Path, Path, Path]:
            png = art / f"{label}.termpp.png"
            buf = art / f"{label}.termpp_buffer.jsonl"
            bridge = art / f"{label}.bridge_cells.jsonl"
            run("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {buf} {bridge}", 120.0)
            wait_files([png, buf, bridge], args.pid, 120.0)
            return png, buf, bridge

        run("LOAD_MAP", args.map, 120.0)
        run("SET_CAMERA", args.camera, 60.0)
        run("SET_LIGHT_CONTROL", cases[0][1], 60.0)
        run("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "0", 60.0)
        run("FL4260_SET_RENDER_MODE", "0", 60.0)
        run("OPEN_TERMPP_CURRENT_VIEW", "", 120.0)
        time.sleep(2.0)
        run("SET_TERMPP_CAMERA_VIEW", args.term_camera, 60.0)
        time.sleep(args.settle_s)
        captures: dict[str, dict[str, str]] = {}
        for label, light in cases:
            run("SET_LIGHT_CONTROL", light, 60.0)
            time.sleep(args.settle_s)
            png, buf, bridge = capture(label)
            captures[label] = {"light": light, "png": str(png), "buffer": str(buf), "bridge": str(bridge)}
        try:
            run("QUIT", "", 30.0)
        except Exception as exc:
            transcript.append({"method": "QUIT", "error": str(exc)})
    finally:
        sock.close()
        (art / "cdp_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    base = captures["base"]
    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    run_id = art.name
    summary: dict[str, Any] = {
        "run_id": run_id,
        "run_label": "fl4260-termpp-light-cp437",
        "git_head": git_head[:12],
        "source_ref": git_head,
        "artifact_path": str(art),
        "artifact_dir": str(art),
        "map": args.map,
        "camera": args.camera,
        "term_camera": args.term_camera,
        "true_gates": [],
        "false_gates": ["evidence_fl4260_termpp_light_updates_cp437_diffuse_shade"],
        "null_gates": [],
        "runtime_required_total": 1,
        "runtime_core_total": 1,
        "captures": captures,
        "diffs": {},
    }
    for label, cap in captures.items():
        if label == "base":
            continue
        summary["diffs"][label] = diff_case(Path(base["bridge"]), Path(base["buffer"]), Path(cap["bridge"]), Path(cap["buffer"]))
    summary["started_at_utc"] = started_at_utc
    summary["completed_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary["pass"] = all(v.get("pass") for v in summary["diffs"].values())
    if summary["pass"]:
        summary["true_gates"] = ["evidence_fl4260_termpp_light_updates_cp437_diffuse_shade"]
        summary["false_gates"] = []
    summary["verdict"] = "pass" if summary["pass"] else "fail"
    summary["runtime_required_passed"] = 1 if summary["pass"] else 0
    summary["runtime_core_passed"] = 1 if summary["pass"] else 0
    summary["law16_disclaimer"] = (
        "PASS here means one local TERM++ CP437 light sweep observed diffuse/shade/final-cell response. "
        "It is not closure; Law 15 and Law 16 still require closure-grade proof and operator signoff."
    )
    (art / "per_slider_light_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (art / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
