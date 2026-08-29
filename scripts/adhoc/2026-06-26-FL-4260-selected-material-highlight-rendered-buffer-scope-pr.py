# Ad hoc script: FL-4260 selected-material highlight rendered-buffer scope proof
# Created: 2026-06-26
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, pathlib, re, socket, subprocess, time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-selected-material-highlight-rendered-buffer-proof"
TERM_CAMERA = "64 64 40960 45 30 10.0 0"

class CDP:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1
    def call(self, method: str, params: str = "", timeout: float = 60.0) -> str:
        payload: dict[str, Any] = {"id": self.next_id, "method": method}
        if params:
            payload["params"] = params
        self.next_id += 1
        self.file.write((json.dumps(payload) + "\n").encode())
        self.sock.settimeout(timeout)
        line = self.file.readline()
        if not line:
            raise RuntimeError(f"CDP EOF waiting for {method}")
        obj = json.loads(line.decode(errors="replace"))
        return str(obj.get("result", obj))
    def close(self) -> None:
        self.sock.close()

def wait_port(port: int, timeout: float = 20.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"CDP port {port} did not open")

def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows=[]
    with path.open(encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def key(row: dict[str, Any]) -> tuple[int,int]:
    return int(row.get("x", -1)), int(row.get("y", -1))

def visual_tuple(row: dict[str, Any]) -> tuple[Any,...]:
    return (row.get("cp437"), row.get("fg"), row.get("bk"), row.get("sidecar_gid"), row.get("final_gid"), row.get("extended"))

def bridge_mats(path: pathlib.Path) -> dict[tuple[int,int], int]:
    out={}
    for r in read_jsonl(path):
        out[key(r)] = int(r.get("material_id", -999))
    return out

def diff_rendered(before: pathlib.Path, after: pathlib.Path, bridge: pathlib.Path, selected_mat: int) -> dict[str, Any]:
    a={key(r):r for r in read_jsonl(before)}
    b={key(r):r for r in read_jsonl(after)}
    mats=bridge_mats(bridge)
    selected=0; nonselected_material=0; offsurface=0
    selected_glyph_identity=0; nonselected_material_glyph_identity=0
    glyph_identity=0; color_only=0
    bymat={}
    samples=[]
    for k, ar in a.items():
        br=b.get(k)
        if br is None:
            continue
        if visual_tuple(ar) != visual_tuple(br):
            mat=mats.get(k, -999)
            bymat[str(mat)] = bymat.get(str(mat), 0)+1
            if mat == selected_mat:
                selected += 1
            elif mat >= 0:
                nonselected_material += 1
            else:
                offsurface += 1
            same_glyph = (ar.get("cp437"), ar.get("sidecar_gid"), ar.get("final_gid"), ar.get("extended")) == (br.get("cp437"), br.get("sidecar_gid"), br.get("final_gid"), br.get("extended"))
            if same_glyph:
                color_only += 1
            else:
                glyph_identity += 1
                if mat == selected_mat:
                    selected_glyph_identity += 1
                elif mat >= 0:
                    nonselected_material_glyph_identity += 1
            if len(samples) < 20:
                samples.append({"x":k[0],"y":k[1],"material_id":mat,"before":visual_tuple(ar),"after":visual_tuple(br)})
    return {
        "changed_total": selected+nonselected_material+offsurface,
        "selected_material_changed": selected,
        "nonselected_material_changed": nonselected_material,
        "offsurface_changed": offsurface,
        "selected_glyph_identity_changes": selected_glyph_identity,
        "nonselected_material_glyph_identity_changes": nonselected_material_glyph_identity,
        "color_only_changes": color_only,
        "glyph_identity_changes": glyph_identity,
        "changed_by_material": bymat,
        "sample": samples,
    }

def parse_state(raw: str) -> dict[str, Any]:
    m=re.search(r"FL4260_MATERIAL_LOOK_STATE (\{.*\})", raw)
    if not m:
        return {}
    return json.loads(m.group(1))

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--mat", type=int, default=1)
    args=ap.parse_args()
    out=pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    proc=subprocess.Popen([str(ROOT/".run/asciiid"), "--cdp", str(args.port)], cwd=ROOT, stdout=(out/"asciiid_cdp.log").open("w", encoding="utf-8"), stderr=subprocess.STDOUT, text=True)
    c=None; transcript=[]
    try:
        wait_port(args.port)
        c=CDP(args.port)
        def run(method: str, params: str = "", timeout: float = 60.0) -> str:
            res=c.call(method, params, timeout=timeout)
            transcript.append({"method":method,"params":params,"result":res[-2400:]})
            return res
        run("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA)
        run("FL4260_RENDERING_PROOF", f"{args.mat} -1 0")
        state=parse_state(run("FL4260_DUMP_MATERIAL_LOOK_STATE"))
        run("FL4260_TRACE_HIGHLIGHT", "0")
        run("CAPTURE_UI_FRAME", str(out/"highlight_off_publish_ui"))
        run("RENDER_TERMPP_ONCE")
        run("FL4131_HARRI_DUMP_GPU_BRIDGE", "0 0")
        run("FL4260_DUMP_BRIDGE_CELLS", str(out/"highlight_off.bridge.jsonl"))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out/"highlight_off.rendered.jsonl"))
        run("CAPTURE_UI_FRAME", str(out/"highlight_off_ui"))
        run("FL4260_TRACE_HIGHLIGHT", "1")
        run("CAPTURE_UI_FRAME", str(out/"highlight_on_publish_ui"))
        run("RENDER_TERMPP_ONCE")
        run("FL4131_HARRI_DUMP_GPU_BRIDGE", "0 0")
        run("FL4260_DUMP_BRIDGE_CELLS", str(out/"highlight_on.bridge.jsonl"))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out/"highlight_on.rendered.jsonl"))
        run("CAPTURE_UI_FRAME", str(out/"highlight_on_ui"))
        d=diff_rendered(out/"highlight_off.rendered.jsonl", out/"highlight_on.rendered.jsonl", out/"highlight_off.bridge.jsonl", args.mat)
        pass_ok = (
            d["selected_material_changed"] > 0 and
            d["nonselected_material_changed"] == 0 and
            d["selected_glyph_identity_changes"] == 0 and
            d["nonselected_material_glyph_identity_changes"] == 0
        )
        result={
            "schema":"fl4260.selected_material_highlight_rendered_buffer_scope.v1",
            "verdict":"PASS_SELECTED_MATERIAL_HIGHLIGHT_COLOR_ONLY_SELECTED_SCOPE" if pass_ok else "FAIL_SELECTED_MATERIAL_HIGHLIGHT_SCOPE",
            "prediction_before_action":"Turning on selected-material highlight should recolor rendered cells whose bridge material_id equals the selected material. It must not change glyph identity, profile JSON, map data, or rendered cells attributed to other material ids. Cells without material attribution are reported separately as off-surface drift.",
            "selected_material":args.mat,
            "state":state,
            "rendered_diff":d,
            "paths":{
                "highlight_off_ui":str(out/"highlight_off_ui/ui_frame.png"),
                "highlight_on_ui":str(out/"highlight_on_ui/ui_frame.png"),
                "highlight_off_rendered":str(out/"highlight_off.rendered.jsonl"),
                "highlight_on_rendered":str(out/"highlight_on.rendered.jsonl"),
                "highlight_off_bridge":str(out/"highlight_off.bridge.jsonl"),
                "highlight_on_bridge":str(out/"highlight_on.bridge.jsonl"),
            },
            "transcript":transcript,
        }
        (out/"selected_material_highlight_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if pass_ok else 1
    finally:
        if c:
            c.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    raise SystemExit(main())
