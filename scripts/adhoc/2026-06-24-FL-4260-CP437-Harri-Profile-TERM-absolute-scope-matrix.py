# Ad hoc script: FL-4260 CP437 Harri Profile TERM++ absolute scope matrix
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Capture and analyze FL-4260 TERM++ CP437/Profile absolute scope.

The matrix keeps map/camera/runtime constant and captures final TERM++ surfaces:
  cp437: Harri off, CP437 AnsiCell presentation
  profile_initial: Harri on, before selected-material edit
  profile_target_edit: Harri on, after selected-material edit

The analyzer compares final rendered cells against the CP437 baseline and reports
which material ids, dispatch surfaces, and screen rows changed. This catches the
failure class where Material Look changes screen bands, HUD/menu rows, mesh/auto
RGB cells, or blackens cells while a selected-material edit is supposed to be
material-scoped. It does not assume a deleted global CP437/Profile mode switch:
durable authored looks may already affect their own terrain materials when
Harri is enabled.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

GLYPH_ID_NONE = 0xFFFFFFFF
DEFAULT_TERM_GRID = "128x72"


def write_observe_tuple(artifact_dir: Path, term_camera: str, observe_light: str) -> Path:
    parts = [float(p) for p in term_camera.split()]
    if len(parts) < 4:
        raise ValueError("--term-camera must start with x y z yaw")
    light = [float(p) for p in observe_light.split()]
    if len(light) != 4:
        raise ValueError("--observe-light must be four floats: x y z ambience")
    tuple_path = artifact_dir / "observe_render_view_tuple.json"
    tuple_path.write_text(json.dumps({
        "camera": {
            "pos": [parts[0], parts[1], parts[2]],
            "yaw": parts[3],
            "zoom": 1.0,
            "perspective": True,
            "scene_shift": 0,
        },
        "light": {
            "dir": [light[0], light[1], light[2]],
            "ambience": light[3],
        },
        "water": 55,
    }, indent=2), encoding="utf-8")
    return tuple_path


def term_camera_command_args(term_camera: str) -> str:
    parts = term_camera.split()
    if len(parts) == 4:
        return f"{term_camera} 0 10"
    return term_camera


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


def read_jsonl(path: Path) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    header: dict[str, Any] = {}
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            obj = json.loads(text)
            kind = obj.get("kind")
            if kind == "header":
                header = obj
            elif kind == "cell":
                try:
                    key = (int(obj["x"]), int(obj["y"]))
                except KeyError as exc:
                    raise ValueError(f"{path}:{line_no}: missing {exc}") from exc
                cells[key] = obj
    return header, cells


def read_upstream_cells(path: Path) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
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
                continue
            if obj.get("kind") != "cell":
                continue
            try:
                x = int(obj["x"])
                y = int(obj["y"])
            except KeyError as exc:
                raise ValueError(f"{path}:{line_no}: missing {exc}") from exc
            cells[(x, y)] = {
                "cp437": obj.get("cp437"),
                "fg": obj.get("fg"),
                "bk": obj.get("bk"),
                "final_gid": GLYPH_ID_NONE,
                "sidecar_gid": GLYPH_ID_NONE,
                "extended": False,
            }
    return header, cells


def render_tuple(cell: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any, Any]:
    return (
        cell.get("final_gid"),
        cell.get("sidecar_gid"),
        cell.get("extended"),
        cell.get("cp437"),
        cell.get("fg"),
        cell.get("bk"),
    )


def cp437_tuple(cell: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (cell.get("cp437"), cell.get("fg"), cell.get("bk"))


def bridge_tuple(cell: dict[str, Any] | None) -> tuple[Any, Any, Any, Any, Any]:
    if not cell:
        return (None, None, None, None, None)
    return (
        cell.get("material_id"),
        cell.get("dispatch_surface"),
        cell.get("ramp"),
        cell.get("density"),
        cell.get("eligible"),
    )


def is_black_regression(base: dict[str, Any], new: dict[str, Any]) -> bool:
    base_fg = int(base.get("fg", -1))
    base_bk = int(base.get("bk", -1))
    new_fg = int(new.get("fg", -1))
    new_bk = int(new.get("bk", -1))
    if new_fg == new_bk and base_fg != base_bk:
        return True
    if new_fg == 16 and new_bk == 16 and (base_fg != 16 or base_bk != 16):
        return True
    return False


def is_dynamic_status_overlay_cell(
    key: tuple[int, int],
    bridge_cell: dict[str, Any] | None,
    grid_h: int,
) -> bool:
    if not bridge_cell:
        return False
    material = bridge_cell.get("material_id")
    dispatch = bridge_cell.get("dispatch_surface")
    if material != -1 or dispatch != 2:
        return False
    # Runtime camera/status text is drawn over the HP/MP bar at the top of the
    # TERM++ cell grid. It is not a Material Look cell and can change between
    # captures as FPS/camera text refreshes.
    return key[1] in (grid_h - 3, grid_h - 2)


def analyze_pair(
    base_name: str,
    test_name: str,
    base_rendered: dict[tuple[int, int], dict[str, Any]],
    test_rendered: dict[tuple[int, int], dict[str, Any]],
    test_bridge: dict[tuple[int, int], dict[str, Any]],
    target_material: int,
    grid_h: int,
) -> dict[str, Any]:
    shared = sorted(set(base_rendered) & set(test_rendered))
    changed = []
    scoped_changed = []
    by_material: Counter[str] = Counter()
    by_dispatch: Counter[str] = Counter()
    by_row: Counter[int] = Counter()
    scoped_by_row: Counter[int] = Counter()
    black_by_row: Counter[int] = Counter()
    status_by_row: Counter[int] = Counter()
    non_target_examples = []
    non_terrain_examples = []
    black_examples = []
    missing_bridge_examples = []
    status_examples = []
    for key in shared:
        b = base_rendered[key]
        t = test_rendered[key]
        if render_tuple(b) == render_tuple(t):
            continue
        changed.append(key)
        br = test_bridge.get(key)
        material = br.get("material_id") if br else None
        dispatch = br.get("dispatch_surface") if br else None
        by_row[key[1]] += 1
        if is_dynamic_status_overlay_cell(key, br, grid_h):
            status_by_row[key[1]] += 1
            if len(status_examples) < 12:
                status_examples.append({
                    "x": key[0], "y": key[1], "material_id": material,
                    "dispatch_surface": dispatch, "bridge": bridge_tuple(br),
                    "before": render_tuple(b), "after": render_tuple(t),
                })
            continue
        scoped_changed.append(key)
        by_material[str(material)] += 1
        by_dispatch[str(dispatch)] += 1
        scoped_by_row[key[1]] += 1
        if br is None and len(missing_bridge_examples) < 12:
            missing_bridge_examples.append({"x": key[0], "y": key[1], "before": render_tuple(b), "after": render_tuple(t)})
        if material != target_material and len(non_target_examples) < 12:
            non_target_examples.append({
                "x": key[0], "y": key[1], "material_id": material,
                "dispatch_surface": dispatch, "bridge": bridge_tuple(br),
                "before": render_tuple(b), "after": render_tuple(t),
            })
        if dispatch not in (1, "1") and len(non_terrain_examples) < 12:
            non_terrain_examples.append({
                "x": key[0], "y": key[1], "material_id": material,
                "dispatch_surface": dispatch, "bridge": bridge_tuple(br),
                "before": render_tuple(b), "after": render_tuple(t),
            })
        if is_black_regression(b, t):
            black_by_row[key[1]] += 1
            if len(black_examples) < 12:
                black_examples.append({
                    "x": key[0], "y": key[1], "material_id": material,
                    "dispatch_surface": dispatch, "before": render_tuple(b), "after": render_tuple(t),
                })
    top_rows = by_row.most_common(12)
    top_scoped_rows = scoped_by_row.most_common(12)
    top_black_rows = black_by_row.most_common(12)
    changed_non_target = sum(count for mat, count in by_material.items() if mat != str(target_material))
    changed_non_terrain = sum(count for surface, count in by_dispatch.items() if surface != "1")
    result = {
        "base": base_name,
        "test": test_name,
        "shared_cells": len(shared),
        "changed_total": len(changed),
        "changed_material_scope_total": len(scoped_changed),
        "changed_dynamic_status_overlay": sum(status_by_row.values()),
        "changed_target_material": by_material.get(str(target_material), 0),
        "changed_non_target_material": changed_non_target,
        "changed_non_terrain_dispatch": changed_non_terrain,
        "black_regression_cells": sum(black_by_row.values()),
        "changed_by_material": dict(by_material.most_common()),
        "changed_by_dispatch_surface": dict(by_dispatch.most_common()),
        "top_changed_rows": top_rows,
        "top_material_scope_changed_rows": top_scoped_rows,
        "top_black_rows": top_black_rows,
        "dynamic_status_overlay_rows": status_by_row.most_common(12),
        "non_target_examples": non_target_examples,
        "non_terrain_examples": non_terrain_examples,
        "black_examples": black_examples,
        "missing_bridge_examples": missing_bridge_examples,
        "dynamic_status_overlay_examples": status_examples,
    }
    result["pass_selected_material_scope"] = (
        len(scoped_changed) > 0
        and changed_non_target == 0
        and changed_non_terrain == 0
        and sum(black_by_row.values()) == 0
    )
    result["pass_no_material_scope_change"] = (
        len(scoped_changed) == 0
        and sum(black_by_row.values()) == 0
    )
    result["pass_no_black_regression"] = sum(black_by_row.values()) == 0
    return result


def capture_matrix(args: argparse.Namespace) -> list[dict[str, Any]]:
    art = Path(args.artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    transcript: list[dict[str, Any]] = []
    seq = 1
    sock = socket.create_connection((args.host, args.port), timeout=30.0)
    try:
        def run(method: str, params: str = "", timeout_s: float = 90.0) -> dict[str, Any]:
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

        def capture(label: str) -> None:
            png = art / f"{label}.termpp.png"
            buf = art / f"{label}.termpp_buffer.jsonl"
            bridge = art / f"{label}.bridge_cells.jsonl"
            if args.term_camera and not getattr(args, "_observe_render_active", False):
                run("SET_TERMPP_CAMERA_VIEW", term_camera_command_args(args.term_camera), 60.0)
                time.sleep(0.25)
            run("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {buf} {bridge}", 120.0)
            wait_files([png, buf, bridge], args.pid, 120.0)

        run("LOAD_MAP", args.map, 120.0)
        if args.camera:
            run("SET_CAMERA", args.camera, 60.0)
        run("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "0")
        run("OPEN_TERMPP_CURRENT_VIEW", "", 120.0)
        if args.term_camera:
            run("SET_TERMPP_CAMERA_VIEW", term_camera_command_args(args.term_camera), 60.0)
            observe_tuple = write_observe_tuple(art, args.term_camera, args.observe_light)
            run("FL4260_SET_OBSERVE_RENDER",
                f"{art} {observe_tuple} fl4260-termpp-absolute-scope-v1",
                60.0)
            args._observe_render_active = True
        time.sleep(args.settle_s)
        capture("cp437")
        time.sleep(args.settle_s)
        capture("cp437_repeat")

        run("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1")
        time.sleep(args.settle_s)
        capture("profile_initial")

        run("FL4260_APPLY_PALETTE_STARTER", str(args.target_material))
        run("FL4260_POOL_ACTION", f"{args.target_material} select_all")
        run("FL4260_ROLE_BUCKET_AUTOFILL", str(args.target_material))
        run("FL4260_RESET_SCORING", str(args.target_material))
        time.sleep(args.settle_s)
        capture("profile_target_edit")
    finally:
        sock.close()
    (Path(args.artifact_dir) / "cdp_matrix_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    return transcript


def analyze_artifacts(artifact_dir: Path, target_material: int) -> dict[str, Any]:
    data: dict[str, dict[str, Any]] = {}
    for label in ["cp437", "cp437_repeat", "profile_initial", "profile_target_edit"]:
        rh, rc = read_jsonl(artifact_dir / f"{label}.termpp_buffer.jsonl")
        bh, bc = read_jsonl(artifact_dir / f"{label}.bridge_cells.jsonl")
        data[label] = {"render_header": rh, "render_cells": rc, "bridge_header": bh, "bridge_cells": bc}
    def grid_h(label: str) -> int:
        return int(data[label]["render_header"].get("h", 0))

    pairs = [
        analyze_pair("cp437", "cp437_repeat", data["cp437"]["render_cells"], data["cp437_repeat"]["render_cells"], data["cp437_repeat"]["bridge_cells"], target_material, grid_h("cp437_repeat")),
        analyze_pair("cp437", "profile_initial", data["cp437"]["render_cells"], data["profile_initial"]["render_cells"], data["profile_initial"]["bridge_cells"], target_material, grid_h("profile_initial")),
        analyze_pair("profile_initial", "profile_target_edit", data["profile_initial"]["render_cells"], data["profile_target_edit"]["render_cells"], data["profile_target_edit"]["bridge_cells"], target_material, grid_h("profile_target_edit")),
    ]
    upstream_result = None
    upstream_path = artifact_dir / "upstream_cp437.cells.jsonl"
    if upstream_path.exists():
        uh, uc = read_upstream_cells(upstream_path)
        current = data["cp437"]["render_cells"]
        shared = sorted(set(uc) & set(current))
        changed = [key for key in shared if cp437_tuple(uc[key]) != cp437_tuple(current[key])]
        by_row = Counter(key[1] for key in changed)
        black_rows_current = Counter()
        black_rows_upstream = Counter()
        for key in shared:
            cur = current[key]
            up = uc[key]
            if int(cur.get("fg", -1)) == int(cur.get("bk", -2)):
                black_rows_current[key[1]] += 1
            if int(up.get("fg", -1)) == int(up.get("bk", -2)):
                black_rows_upstream[key[1]] += 1
        upstream_result = {
            "schema": "fl4260.upstream_vs_current_cp437.v1",
            "upstream_header": uh,
            "shared_cells": len(shared),
            "changed_cp437_fg_bg": len(changed),
            "top_changed_rows": by_row.most_common(16),
            "current_full_black_like_rows": [
                [row, count] for row, count in black_rows_current.items() if count >= 120
            ],
            "upstream_full_black_like_rows": [
                [row, count] for row, count in black_rows_upstream.items() if count >= 120
            ],
            "examples": [
                {
                    "x": key[0],
                    "y": key[1],
                    "upstream": cp437_tuple(uc[key]),
                    "current": cp437_tuple(current[key]),
                }
                for key in changed[:24]
            ],
        }
    cp437_stable = pairs[0]["pass_no_material_scope_change"]
    profile_initial_policy_scope = (
        pairs[1]["changed_non_terrain_dispatch"] == 0 and
        pairs[1]["pass_no_black_regression"]
    )
    profile_edit_selected_scope = pairs[2]["pass_selected_material_scope"]
    upstream_cp437_parity = (
        upstream_result is None or
        upstream_result["changed_cp437_fg_bg"] == 0
    )
    source_shot = None
    source_shot_path = artifact_dir / "source-shot.json"
    if source_shot_path.exists():
        source_shot = json.loads(source_shot_path.read_text(encoding="utf-8"))
    observe_render_euclidean = (
        source_shot is None or
        int(source_shot.get("render_report", {}).get("s3_active", 0)) == 0
    )
    return {
        "schema": "fl4260.termpp.absolute_scope_matrix.v1",
        "target_material": target_material,
        "artifact_dir": str(artifact_dir),
        "headers": {label: {"render": data[label]["render_header"], "bridge": data[label]["bridge_header"]} for label in data},
        "pairs": pairs,
        "upstream_cp437_compare": upstream_result,
        "source_shot": source_shot,
        "acceptance": {
            "upstream_cp437_parity": upstream_cp437_parity,
            "cp437_repeat_stable": cp437_stable,
            "profile_initial_policy_scope": profile_initial_policy_scope,
            "profile_edit_selected_material_scope": profile_edit_selected_scope,
            "observe_render_euclidean": observe_render_euclidean,
        },
        "verdict": "PASS" if (
            upstream_cp437_parity and
            cp437_stable and
            profile_initial_policy_scope and
            profile_edit_selected_scope and
            observe_render_euclidean
        ) else "FAIL",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-dir", required=True)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--map", default="assets/a3d/fl4260_fixture_all_materials.a3d")
    ap.add_argument("--camera", default="")
    ap.add_argument("--term-camera", default="")
    ap.add_argument("--observe-light", default="1 1 1 1")
    ap.add_argument("--target-material", type=int, default=1)
    ap.add_argument("--settle-s", type=float, default=1.25)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    args = ap.parse_args()
    artifact_dir = Path(args.artifact_dir)
    if not args.capture and not args.analyze:
        args.capture = True
        args.analyze = True
    if args.capture:
        capture_matrix(args)
    result = {"artifact_dir": str(artifact_dir)}
    if args.analyze:
        result = analyze_artifacts(artifact_dir, args.target_material)
        (artifact_dir / "absolute_scope_matrix.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("verdict") in (None, "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
