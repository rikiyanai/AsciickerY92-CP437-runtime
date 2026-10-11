#!/usr/bin/env python3
# Ad hoc script: FL-4260 Material Look undo selected-material restore CDP proof driver
# Created: 2026-06-26
# Canonical gap: FL-4260 Material Look selected-material undo CDP proof front door
"""FL-4260 Material Look undo proof.

Proves the visible Undo owner through CDP:
  baseline profile colors -> flat-anchor edit -> undo -> baseline restored.
The proof reads Material Look profile state and rendered TERM++ cells for the
selected material. It does not mutate world facts and does not write durable JSON
except through the same live edit path already used by the UI action under test.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-material-look-undo-proof"
MAT = 1
ROW = 3

class CDPClient:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1

    def call(self, method: str, params: str = "", timeout: float = 60.0) -> str:
        payload: dict[str, Any] = {"id": self.next_id, "method": method}
        if params:
            payload["params"] = params
        self.next_id += 1
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

def wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"CDP port {port} did not open")

def parse_colors(raw: str) -> dict[str, Any]:
    m = re.search(r"FL4260_PROFILE_EDIT_COLORS (\{.*\})", raw, re.S)
    if not m:
        raise ValueError(f"no color JSON in response: {raw[-1000:]}")
    return json.loads(m.group(1))

def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    out=[]
    with path.open() as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out

def cell_key(row: dict[str, Any]) -> tuple[int, int]:
    return (int(row.get("x", -1)), int(row.get("y", -1)))

def bridge_materials(bridge_path: pathlib.Path) -> dict[tuple[int, int], int]:
    mats: dict[tuple[int, int], int] = {}
    for row in read_jsonl(bridge_path):
        try:
            mats[cell_key(row)] = int(row.get("material_id", -999))
        except Exception:
            mats[cell_key(row)] = -999
    return mats

def rendered_diff(a_path: pathlib.Path, b_path: pathlib.Path, bridge_path: pathlib.Path, mat: int) -> dict[str, Any]:
    a={cell_key(r): r for r in read_jsonl(a_path)}
    b={cell_key(r): r for r in read_jsonl(b_path)}
    mats=bridge_materials(bridge_path)
    changed=[]
    selected=0
    other=0
    for k, ar in a.items():
        br=b.get(k)
        if br is None:
            continue
        fields=("fg","bk","final_gid","sidecar_gid","extended")
        def visible_tuple(r: dict[str, Any]) -> tuple[Any, ...]:
            final_gid = r.get("final_gid")
            sidecar_gid = r.get("sidecar_gid")
            extended = r.get("extended")
            visible_gid = final_gid if extended or sidecar_gid != 0xFFFFFFFF else r.get("cp437")
            return (r.get("fg"), r.get("bk"), visible_gid, sidecar_gid, extended)
        if visible_tuple(ar) != visible_tuple(br):
            row_mat=mats.get(k, -999)
            rec={"x":k[0],"y":k[1],
                 "before":{**{f:ar.get(f) for f in fields}, "cp437": ar.get("cp437"), "visible_tuple": visible_tuple(ar)},
                 "after":{**{f:br.get(f) for f in fields}, "cp437": br.get("cp437"), "visible_tuple": visible_tuple(br)},
                 "mat":row_mat}
            changed.append(rec)
            if row_mat == mat:
                selected += 1
            else:
                other += 1
    return {"changed_total": len(changed), "selected_changed": selected, "other_changed": other, "sample": changed[:20]}

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--mat", type=int, default=MAT)
    args=ap.parse_args()

    out=pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    proc=subprocess.Popen([str(ROOT/".run/asciiid"), "--cdp", str(args.port)], cwd=ROOT,
        stdout=(out/"asciiid_cdp.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True)
    transcript=[]
    try:
        wait_port(args.port)
        c=CDPClient(args.port)
        def run(method: str, params: str = "", timeout: float = 60.0) -> str:
            res=c.call(method, params, timeout=timeout)
            transcript.append({"method":method,"params":params,"result":res[-2000:]})
            return res

        run("SET_TERMPP_CAMERA_VIEW", "64 64 40960 45 30 10.0 0")
        run("FL4260_APPLY_PALETTE_STARTER", str(args.mat))
        baseline_colors=parse_colors(run("FL4260_DUMP_PROFILE_EDIT_COLORS", str(args.mat)))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out/"before.rendered.jsonl"))
        run("FL4260_DUMP_BRIDGE_CELLS", str(out/"before.bridge.jsonl"))

        # Choose a deliberately different flat anchor, then undo through the single UI owner.
        run("FL4260_SET_FLAT_ANCHOR_COLOR", f"{args.mat} 20 40 220")
        changed_colors=parse_colors(run("FL4260_DUMP_PROFILE_EDIT_COLORS", str(args.mat)))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out/"after_edit.rendered.jsonl"))
        run("FL4260_DUMP_BRIDGE_CELLS", str(out/"after_edit.bridge.jsonl"))

        undo_res=run("FL4260_UNDO_PROFILE_EDIT", str(args.mat))
        undo_colors=parse_colors(run("FL4260_DUMP_PROFILE_EDIT_COLORS", str(args.mat)))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out/"after_undo.rendered.jsonl"))
        run("FL4260_DUMP_BRIDGE_CELLS", str(out/"after_undo.bridge.jsonl"))
        run("CAPTURE_UI_FRAME", str(out/"ui_after_undo"))

        edit_diff=rendered_diff(out/"before.rendered.jsonl", out/"after_edit.rendered.jsonl", out/"before.bridge.jsonl", args.mat)
        undo_diff=rendered_diff(out/"before.rendered.jsonl", out/"after_undo.rendered.jsonl", out/"before.bridge.jsonl", args.mat)
        colors_restored = baseline_colors == undo_colors
        colors_changed = baseline_colors != changed_colors
        undo_ok = "ok=1" in undo_res
        off_scope_drift_reported = (
            edit_diff["other_changed"] > 0 or undo_diff["other_changed"] > 0
        )
        verdict = "PASS_UNDO_RESTORES_SELECTED_MATERIAL_LOOK_WITH_OFFSCOPE_DRIFT_REPORTED" if (
            undo_ok and colors_changed and colors_restored
            and edit_diff["selected_changed"] > 0
            and undo_diff["selected_changed"] == 0
        ) else "FAIL_UNDO_RESTORE"
        result={
            "schema":"fl4260.material_look_undo_proof.v1",
            "verdict":verdict,
            "material":args.mat,
            "prediction_before_action":"Flat-anchor edit must change selected-material Material Look cells; Undo must restore selected-material profile colors and selected-material TERM++ cells to baseline. Off-scope TERM++ drift is measured and reported separately.",
            "colors_changed_by_edit":colors_changed,
            "colors_restored_by_undo":colors_restored,
            "undo_command_ok":undo_ok,
            "off_scope_render_drift_reported":off_scope_drift_reported,
            "edit_render_diff":edit_diff,
            "undo_vs_baseline_render_diff":undo_diff,
            "paths":{
                "before_rendered":str(out/"before.rendered.jsonl"),
                "after_edit_rendered":str(out/"after_edit.rendered.jsonl"),
                "after_undo_rendered":str(out/"after_undo.rendered.jsonl"),
                "ui_after_undo":str(out/"ui_after_undo/ui_frame.png"),
            },
            "transcript":transcript,
        }
        (out/"material_look_undo_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if verdict.startswith("PASS") else 1
    finally:
        try:
            c.close()  # type: ignore[name-defined]
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    raise SystemExit(main())
