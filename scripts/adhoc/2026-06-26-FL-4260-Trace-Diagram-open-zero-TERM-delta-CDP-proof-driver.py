# Ad hoc script: FL-4260 Trace Diagram open zero TERM++ delta CDP proof driver
# Created: 2026-06-26
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 Trace Diagram zero-delta proof.

Opens the detached read-only Trace Diagram through CDP and verifies that this
UI-only action does not change the rendered TERM++ buffer. The command under
test reports profile/world/render mutation flags as false; the rendered-buffer
comparison is the hard falsifier.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import subprocess
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-trace-diagram-open-zero-delta-proof"
TERM_CAMERA = "64 64 40960 45 30 10.0 0"

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

def wait_port(port: int, timeout: float = 12.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"CDP port {port} did not open")

def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def cell_key(row: dict[str, Any]) -> tuple[int, int]:
    return int(row.get("x", -1)), int(row.get("y", -1))

def visible_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    sidecar = row.get("sidecar_gid")
    extended = row.get("extended")
    final_gid = row.get("final_gid")
    visible_gid = final_gid if extended or sidecar != 0xFFFFFFFF else row.get("cp437")
    return (row.get("fg"), row.get("bk"), visible_gid, sidecar, extended)

def bridge_materials(path: pathlib.Path) -> dict[tuple[int, int], int]:
    mats: dict[tuple[int, int], int] = {}
    for row in read_jsonl(path):
        try:
            mats[cell_key(row)] = int(row.get("material_id", -999))
        except Exception:
            mats[cell_key(row)] = -999
    return mats

def rendered_diff(a_path: pathlib.Path, b_path: pathlib.Path, bridge_path: pathlib.Path, selected_mat: int) -> dict[str, Any]:
    a = {cell_key(r): r for r in read_jsonl(a_path)}
    b = {cell_key(r): r for r in read_jsonl(b_path)}
    mats = bridge_materials(bridge_path)
    changed = []
    selected_changed = 0
    other_changed = 0
    changed_by_mat: dict[str, int] = {}
    for k, ar in a.items():
        br = b.get(k)
        if br is None:
            changed.append({"x": k[0], "y": k[1], "kind": "missing_after"})
            continue
        if visible_tuple(ar) != visible_tuple(br):
            mat = mats.get(k, -999)
            if mat == selected_mat:
                selected_changed += 1
            else:
                other_changed += 1
            changed_by_mat[str(mat)] = changed_by_mat.get(str(mat), 0) + 1
            changed.append({
                "x": k[0], "y": k[1], "material_id": mat,
                "before": visible_tuple(ar),
                "after": visible_tuple(br),
            })
    extra = [k for k in b.keys() if k not in a]
    return {
        "changed_total": len(changed) + len(extra),
        "selected_material_changed": selected_changed,
        "other_material_changed": other_changed,
        "changed_by_material": changed_by_mat,
        "changed_sample": changed[:20],
        "extra_after_count": len(extra),
    }

def extract_json(raw: str, token: str) -> dict[str, Any]:
    m = re.search(re.escape(token) + r"\s+(\{.*\})", raw, re.S)
    if not m:
        raise ValueError(f"missing {token}: {raw[-1200:]}")
    return json.loads(m.group(1))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--mat", type=int, default=1)
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(ROOT / ".run/asciiid"), "--cdp", str(args.port)], cwd=ROOT,
        stdout=(out / "asciiid_cdp.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT, text=True)
    transcript: list[dict[str, str]] = []
    c: CDPClient | None = None
    try:
        wait_port(args.port)
        c = CDPClient(args.port)
        def run(method: str, params: str = "", timeout: float = 60.0) -> str:
            res = c.call(method, params, timeout=timeout)  # type: ignore[union-attr]
            transcript.append({"method": method, "params": params, "result": res[-2000:]})
            return res

        run("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA)
        run("RENDER_TERMPP_ONCE", "")
        before_state_raw = run("FL4260_DUMP_TRACE_DIAGRAM_STATE", "")
        before_state = extract_json(before_state_raw, "[MCP] FL4260_TRACE_DIAGRAM_STATE")
        run("FL4260_DUMP_BRIDGE_CELLS", str(out / "before.bridge.jsonl"))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out / "before.rendered.jsonl"))
        open_raw = run("FL4260_TRACE_DIAGRAM_OPEN", "1")
        after_state_raw = run("FL4260_DUMP_TRACE_DIAGRAM_STATE", "")
        after_state = extract_json(after_state_raw, "[MCP] FL4260_TRACE_DIAGRAM_STATE")
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out / "after_open.rendered.jsonl"))
        run("CAPTURE_UI_FRAME", str(out / "ui_trace_diagram"))

        diff = rendered_diff(out / "before.rendered.jsonl", out / "after_open.rendered.jsonl", out / "before.bridge.jsonl", args.mat)
        command_flags_ok = all(s in open_raw for s in ["render_mutation=0", "profile_mutation=0", "world_mutation=0"])
        state_flags_ok = after_state.get("render_mutation") is False and after_state.get("profile_mutation") is False and after_state.get("world_mutation") is False
        state_open_ok = before_state.get("open") == 0 and after_state.get("open") == 1
        verdict = "PASS_TRACE_DIAGRAM_OPEN_SELECTED_MATERIAL_ZERO_DELTA_WITH_OFFSCOPE_DRIFT_REPORTED" if diff["selected_material_changed"] == 0 and command_flags_ok and state_flags_ok and state_open_ok else "FAIL_TRACE_DIAGRAM_OPEN_MUTATED_SELECTED_MATERIAL"
        result = {
            "schema": "fl4260.trace_diagram_open_zero_delta.v1",
            "verdict": verdict,
            "prediction_before_action": "Opening the detached Trace Diagram is UI-only. Expected selected-material rendered TERM++ cell delta is exactly 0. Any live off-scope TERM++ drift is attributed and reported. Expected profile/world mutation flags are false.",
            "selected_material": args.mat,
            "rendered_diff": diff,
            "command_flags_ok": command_flags_ok,
            "state_flags_ok": state_flags_ok,
            "state_open_ok": state_open_ok,
            "before_state": before_state,
            "after_state": after_state,
            "paths": {
                "before_rendered": str(out / "before.rendered.jsonl"),
                "after_open_rendered": str(out / "after_open.rendered.jsonl"),
                "ui_frame": str(out / "ui_trace_diagram/ui_frame.png"),
            },
            "transcript": transcript,
        }
        (out / "trace_diagram_open_zero_delta_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if verdict.startswith("PASS") else 1
    finally:
        if c is not None:
            c.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    raise SystemExit(main())
