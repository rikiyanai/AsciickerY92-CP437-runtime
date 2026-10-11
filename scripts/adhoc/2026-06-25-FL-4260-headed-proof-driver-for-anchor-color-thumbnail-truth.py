# Ad hoc script: FL-4260 headed proof driver for anchor color, thumbnail truth, brush selection, and keyboard controls
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
PORT = int(os.environ.get("FL4260_PROOF_PORT", "8766"))
REPO = Path(__file__).resolve().parents[2]
ASCIIID = REPO / ".run" / "asciiid"
OUT = REPO / "docs/research/ascii/verification/fl4260/2026-06-24-anchor-thumbnail-brush-keyboard-proof"
MAP = REPO / "assets/a3d/game_map_y8_original_game_map.a3d"
SC = {"COMMA":54,"PERIOD":55,"SEMI":51,"QUOTE":52,"TAB":43,"ENTER":40,"W":26,"A":4,"S":22,"D":7}

OUT.mkdir(parents=True, exist_ok=True)

class Cdp:
    def __init__(self, port:int, deadline:float=40.0):
        self.port = port
        self.next_id = 1
        end = time.time() + deadline
        while time.time() < end:
            try:
                s = socket.create_connection((HOST, port), timeout=1.0)
                s.close()
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError(f"CDP not ready on {port}")
    def call(self, method:str, params:str="", idle:float=1.5, hard:float=25.0) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4.0)
        try:
            sock.connect((HOST, self.port))
            req = {"id": self.next_id, "method": method, "params": params}
            self.next_id += 1
            sock.sendall((json.dumps(req)+"\n").encode())
            sock.settimeout(idle)
            chunks=[]; start=time.time()
            while time.time() - start < hard:
                try:
                    c = sock.recv(65536)
                except socket.timeout:
                    break
                if not c: break
                chunks.append(c)
            raw = b"".join(chunks).decode("utf-8", "replace")
            # CDP wraps ProcessMCPCommand stdout in {"id":N,"result":"..."}.
            # Some old one-off scripts parsed stdout directly because they used
            # MCP stdin; this driver uses CDP, so unwrap before source-specific
            # regexes read command payloads.
            for line in raw.splitlines():
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if "result" in msg:
                    return str(msg.get("result", ""))
            return raw
        finally:
            sock.close()

def write_json(path:Path, obj:Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True)+"\n", encoding="utf-8")

def parse_state(raw:str) -> dict[str, Any]:
    m = re.search(r"FL4260_MATERIAL_LOOK_STATE (\{.*\})", raw, re.S)
    if not m:
        return {"parse_error": raw[-800:]}
    return json.loads(m.group(1))

def material_counts(state:dict[str,Any]) -> dict[int,int]:
    return {int(x["material_id"]): int(x["terrain_cells"]) for x in state.get("materials", [])}

def capture_ui(c:Cdp, tag:str) -> Path:
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    c.call("CAPTURE_UI_FRAME", str(d), idle=2.5, hard=14.0)
    p = d / "ui_frame.png"
    end = time.time()+8
    while time.time() < end and not p.exists(): time.sleep(0.1)
    if not p.exists(): raise RuntimeError(f"missing UI capture {tag}")
    return p

def capture_term(c:Cdp, tag:str) -> tuple[Path,Path,Path]:
    png = OUT / f"{tag}.termpp.png"
    cells = OUT / f"{tag}.rendered.jsonl"
    bridge = OUT / f"{tag}.bridge.jsonl"
    c.call("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.5, hard=35.0)
    end = time.time()+10
    while time.time() < end and (not cells.exists() or not bridge.exists()): time.sleep(0.1)
    if not cells.exists() or not bridge.exists():
        raise RuntimeError(f"missing TERM capture {tag}")
    return png, cells, bridge

def load_jsonl(path:Path) -> dict[tuple[int,int],dict[str,Any]]:
    rows={}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line: continue
            o=json.loads(line)
            if o.get("kind") == "cell":
                rows[(int(o["x"]), int(o["y"]))]=o
    return rows

def vis(row:dict[str,Any]) -> tuple[Any,...]:
    return (row.get("final_gid"), row.get("fg"), row.get("bk"))

def bridge_world(row:dict[str,Any]) -> tuple[Any,...]:
    return (row.get("material_id"), row.get("dispatch_surface"), row.get("resolve_elev"), row.get("resolve_shade"), row.get("ramp"), row.get("density"), tuple(row.get("sample_diffuses", [])))

def diff_cells(before_cells:Path, after_cells:Path, before_bridge:Path, after_bridge:Path, target:int) -> dict[str,Any]:
    bc=load_jsonl(before_cells); ac=load_jsonl(after_cells); bb=load_jsonl(before_bridge); ab=load_jsonl(after_bridge)
    keys=sorted(set(bc)&set(ac))
    changed=[k for k in keys if vis(bc[k]) != vis(ac[k])]
    hist=Counter(bb.get(k,{}).get("material_id") for k in changed)
    target_changed=[k for k in changed if bb.get(k,{}).get("material_id") == target]
    other_terrain=[k for k in changed if isinstance(bb.get(k,{}).get("material_id"), int) and bb[k].get("material_id") != target and bb[k].get("material_id") >= 0]
    world_changed=[k for k in sorted(set(bb)&set(ab)) if bridge_world(bb[k]) != bridge_world(ab[k])]
    lanes=Counter()
    for row in bb.values():
        if row.get("material_id") == target:
            lanes[row.get("ramp")] += 1
    return {
        "target_material": target,
        "visible_identity_changed_total": len(changed),
        "visible_identity_changed_target": len(target_changed),
        "visible_identity_changed_other_terrain": len(other_terrain),
        "visible_material_hist": dict(hist.most_common()),
        "bridge_world_fact_changed_total": len(world_changed),
        "selected_material_lane_counts": dict(lanes),
        "examples": [{"x":k[0],"y":k[1],"mat":bb.get(k,{}).get("material_id"),"before":vis(bc[k]),"after":vis(ac[k])} for k in changed[:16]],
    }

def parse_kb_list(raw:str) -> dict[str,Any]:
    count = None; focus = None; labels=[]; last={}
    m = re.search(r"FL4260_KB_LIST count=(\d+) focus=([^\n]*)", raw)
    if m:
        count=int(m.group(1)); focus=m.group(2).strip()
    for ln in raw.splitlines():
        m = re.search(r"kb\[(\d+)\] (.*)$", ln)
        if m: labels.append(m.group(2).strip())
    m = re.search(r"last_focus=([^ ]+) last_adjust=([^ ]+) adjust_count=(\d+) value_i=(-?\d+) value_f=([-0-9.]+) is_float=(\d+)", raw)
    if m:
        last={"last_focus":m.group(1),"last_adjust":m.group(2),"adjust_count":int(m.group(3)),"value_i":int(m.group(4)),"value_f":float(m.group(5)),"is_float":int(m.group(6))}
    return {"count":count,"focus":focus,"labels":labels,"last":last,"raw_tail":raw[-1200:]}

def parse_kb_status(raw:str) -> dict[str,Any]:
    m = re.search(r"FL4260_KB_STATUS focus=([^ ]*) last=([^ ]*) count=(\d+) value_i=(-?\d+) value_f=([-0-9.]+) is_float=(\d+)", raw)
    if not m:
        return {"parse_error": raw[-800:]}
    return {
        "focus": m.group(1),
        "last": m.group(2),
        "count": int(m.group(3)),
        "value_i": int(m.group(4)),
        "value_f": float(m.group(5)),
        "is_float": int(m.group(6)),
        "raw_tail": raw[-800:],
    }

def key(c:Cdp, name:str, presses:int=1):
    r = c.call("RUN_SDL_KEY", f"{SC[name]} {presses}", idle=1.0, hard=10.0)
    time.sleep(max(0.35, 0.12*presses+0.2))
    return r

def main() -> int:
    proc = subprocess.Popen([str(ASCIIID), "--cdp", str(PORT)], cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    summary: dict[str,Any] = {"out_dir": str(OUT), "port": PORT, "steps": {}}
    try:
        c=Cdp(PORT)
        summary["setup"] = {
            "load_map": c.call("LOAD_MAP", str(MAP), idle=3.0, hard=60.0)[-1000:],
            "harri": c.call("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1", idle=1.5, hard=20.0)[-500:],
            "mode": c.call("FL4260_SET_RENDER_MODE", "1", idle=1.5, hard=20.0)[-500:],
            "open_termpp": c.call("OPEN_TERMPP_CURRENT_VIEW", "", idle=4.0, hard=50.0)[-500:],
        }
        time.sleep(2.0)
        # Pick visible material 1 when present; zero-count material for brush paint target.
        state0=parse_state(c.call("FL4260_DUMP_MATERIAL_LOOK_STATE", "", idle=1.0, hard=10.0))
        counts0=material_counts(state0)
        visible_mat = 1 if counts0.get(1,0)>0 else max((m for m,n in counts0.items() if n>0), key=lambda m: counts0[m])
        zero_mat = next((m for m in range(2,256) if counts0.get(m,0)==0), 2)
        summary["initial_state"] = state0
        summary["visible_mat"] = visible_mat
        summary["zero_mat_for_brush"] = zero_mat

        # 3. Anchor-color behavior: use palette square path equivalence command plus profile status and TERM++ diff.
        c.call("FL4260_RENDERING_PROOF", f"{visible_mat} -1 0", idle=1.0, hard=15.0); time.sleep(1.0)
        capture_ui(c, "01_material_look_before_anchor")
        _pn0, cellsn0, bridgen0 = capture_term(c, "00_noedit_a")
        _pn1, cellsn1, bridgen1 = capture_term(c, "00_noedit_b")
        noedit_diff = diff_cells(cellsn0, cellsn1, bridgen0, bridgen1, visible_mat)
        _p0, cells0, bridge0 = capture_term(c, "01_before_anchor")
        colors_before=[]
        for row in range(4):
            colors_before.append(c.call("FL4260_DUMP_PROFILE_EDIT_COLORS", f"{visible_mat}", idle=0.8, hard=8.0).strip())
        action = c.call("FL4260_SET_FLAT_ANCHOR_COLOR", f"{visible_mat} 200 60 30", idle=1.0, hard=12.0)
        time.sleep(1.2)
        colors_after=[]
        for row in range(4):
            colors_after.append(c.call("FL4260_DUMP_PROFILE_EDIT_COLORS", f"{visible_mat}", idle=0.8, hard=8.0).strip())
        capture_ui(c, "02_material_look_after_anchor_family")
        _p1, cells1, bridge1 = capture_term(c, "02_after_anchor")
        anchor_diff=diff_cells(cells0,cells1,bridge0,bridge1,visible_mat)
        allowed_other = max(10, noedit_diff["visible_identity_changed_other_terrain"] + 10)
        allowed_world = max(10, noedit_diff["bridge_world_fact_changed_total"] + 10)
        summary["steps"]["3_anchor_color"]={
            "method":"FL4260_SET_FLAT_ANCHOR_COLOR calls the same profile helper as the UI flat-fill picker; headed UI before/after captured",
            "action_tail":action[-500:],
            "noedit_diff": noedit_diff,
            "profile_status_before":colors_before,
            "profile_status_after":colors_after,
            "termpp_diff":anchor_diff,
            "expected":"selected material color cells change; bridge world facts do not move; sibling rows form darker/lighter family",
            "verdict":"PASS_LOCAL" if anchor_diff["visible_identity_changed_target"]>0 and anchor_diff["visible_identity_changed_other_terrain"]<=allowed_other and anchor_diff["bridge_world_fact_changed_total"]<=allowed_world else "FAIL_OR_INCONCLUSIVE"
        }

        # 4. Thumbnail truth: capture selected visible material, then zero material; compare live state and screenshots.
        c.call("FL4260_RENDERING_PROOF", f"{visible_mat} -1 0", idle=1.0, hard=15.0); time.sleep(0.8)
        ui_vis = capture_ui(c, "03_thumbnail_visible_selected_material")
        st_vis = parse_state(c.call("FL4260_DUMP_MATERIAL_LOOK_STATE", "", idle=1.0, hard=10.0))
        c.call("FL4260_RENDERING_PROOF", f"{zero_mat} -1 0", idle=1.0, hard=15.0); time.sleep(0.8)
        ui_zero = capture_ui(c, "04_thumbnail_zero_cell_material")
        st_zero = parse_state(c.call("FL4260_DUMP_MATERIAL_LOOK_STATE", "", idle=1.0, hard=10.0))
        summary["steps"]["4_thumbnail_truth"]={
            "visible_material_capture":str(ui_vis.relative_to(REPO)),
            "zero_cell_material_capture":str(ui_zero.relative_to(REPO)),
            "visible_state":st_vis,
            "zero_state":st_zero,
            "expected":"selected visible material preview shows live cells; zero-cell material reports no map cells instead of fabricating samples",
            "verdict":"PASS_LOCAL_SCREENSHOT_REQUIRED" if material_counts(st_zero).get(zero_mat,0)==0 and st_zero.get("selected_material")==zero_mat else "FAIL_OR_INCONCLUSIVE"
        }

        # 5. Brush/material selection: select zero material, focus MAT-id, click/drag main viewport, compare terrain counts.
        c.call("FL4260_RENDERING_PROOF", f"{zero_mat} -1 0", idle=1.0, hard=15.0); time.sleep(0.6)
        st_sel_before=parse_state(c.call("FL4260_DUMP_MATERIAL_LOOK_STATE", "", idle=1.0, hard=10.0))
        c.call("FL4260_FOCUS_BRUSH_TAB", "1", idle=1.0, hard=8.0)
        c.call("FL4260_FOCUS_SIDEBAR_TAB", "1", idle=1.0, hard=8.0)
        c.call("FL4260_EDIT_SCROLL_Y", "1250", idle=1.0, hard=8.0)
        time.sleep(0.8)
        edit_ui_before = capture_ui(c, "05_edit_matid_before_paint")
        # Main viewport probe: draw a short stroke over the visible map area, away from the side panel.
        # CAPTURE_UI_FRAME is retina-sized (1600x1200 here), but SDL probes use
        # window coordinates (~1200x900). The live map region at screenshot
        # x=1300..1590 maps to SDL x~=975..1192.
        drag_resp = c.call("RUN_SDL_MOUSE_DRAG_PROBE", "1040 430 1140 560 18", idle=1.0, hard=15.0)
        time.sleep(1.5)
        edit_ui_after = capture_ui(c, "06_edit_matid_after_paint")
        st_sel_after=parse_state(c.call("FL4260_DUMP_MATERIAL_LOOK_STATE", "", idle=1.0, hard=10.0))
        before_counts=material_counts(st_sel_before); after_counts=material_counts(st_sel_after)
        before_zero=before_counts.get(zero_mat,0); after_zero=after_counts.get(zero_mat,0)
        summary["steps"]["5_brush_material_selection"]={
            "selected_material_before":st_sel_before.get("selected_material"),
            "active_material_before":st_sel_before.get("active_material"),
            "selected_material_after":st_sel_after.get("selected_material"),
            "active_material_after":st_sel_after.get("active_material"),
            "zero_mat":zero_mat,
            "terrain_cells_before":before_zero,
            "terrain_cells_after":after_zero,
            "drag_response_tail":drag_resp[-800:],
            "edit_before_capture":str(edit_ui_before.relative_to(REPO)),
            "edit_after_capture":str(edit_ui_after.relative_to(REPO)),
            "expected":"Material Look selection and EDIT active_material match; MAT-id paint increases selected material terrain-cell count",
            "verdict":"PASS_LOCAL" if st_sel_before.get("selected_material")==zero_mat and st_sel_before.get("active_material")==zero_mat and after_zero>before_zero else "FAIL_OR_INCONCLUSIVE"
        }

        # 6. Keyboard audit: verify visible keys panel and real injected keys adjust a focused slider.
        c.call("FL4260_RENDERING_PROOF", f"{visible_mat} -1 0", idle=1.0, hard=15.0); time.sleep(0.8)
        c.call("FL4260_RESET_SCORING", f"{visible_mat}", idle=1.0, hard=12.0); time.sleep(0.4)
        c.call("FL4260_SCROLL_Y", "0", idle=0.8, hard=8.0); time.sleep(0.4)
        kb_before = capture_ui(c, "07_keyboard_panel_before")
        c.call("FL4260_KB_FOCUS", "scoring.curve", idle=0.8, hard=8.0)
        c.call("FL4260_SCROLL_Y", "1900", idle=0.8, hard=8.0); time.sleep(0.8)
        kb_list_before=parse_kb_list(c.call("FL4260_KB_LIST", "", idle=1.0, hard=10.0))
        kb_status_before=parse_kb_status(c.call("FL4260_KB_STATUS", "", idle=1.0, hard=10.0))
        key(c,"ENTER",1)
        key(c,"PERIOD",6)
        kb_list_after=parse_kb_list(c.call("FL4260_KB_LIST", "", idle=1.0, hard=10.0))
        kb_status_after=parse_kb_status(c.call("FL4260_KB_STATUS", "", idle=1.0, hard=10.0))
        kb_after = capture_ui(c, "08_keyboard_panel_after_period_adjust")
        key(c,"QUOTE",2)
        kb_list_focus=parse_kb_list(c.call("FL4260_KB_LIST", "", idle=1.0, hard=10.0))
        kb_status_focus=parse_kb_status(c.call("FL4260_KB_STATUS", "", idle=1.0, hard=10.0))
        summary["steps"]["6_keyboard_drive"]={
            "before_capture":str(kb_before.relative_to(REPO)),
            "after_capture":str(kb_after.relative_to(REPO)),
            "kb_list_before":kb_list_before,
            "kb_status_before":kb_status_before,
            "kb_list_after_period":kb_list_after,
            "kb_status_after_period":kb_status_after,
            "kb_list_after_quote":kb_list_focus,
            "kb_status_after_quote":kb_status_focus,
            "expected":"keyboard panel visible; FL4260_KB_FOCUS selects a control; injected period changes the focused slider; quote moves focus",
            "verdict":"PASS_LOCAL" if kb_status_after.get("count",0) > kb_status_before.get("count",0) else "FAIL_OR_INCONCLUSIVE"
        }

    finally:
        try:
            c.call("QUIT", "", idle=0.5, hard=2.0)
        except Exception:
            pass
        try:
            proc.terminate(); proc.communicate(timeout=5)
        except Exception:
            try: proc.kill(); proc.communicate(timeout=5)
            except Exception: pass
    summary_path = OUT / "proof-summary.json"
    write_json(summary_path, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(str(v.get("verdict","")).startswith("PASS") for v in summary["steps"].values()) else 1

if __name__ == "__main__":
    raise SystemExit(main())
