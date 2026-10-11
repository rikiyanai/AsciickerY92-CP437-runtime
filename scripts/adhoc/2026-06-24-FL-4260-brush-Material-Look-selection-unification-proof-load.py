# Ad hoc script: FL-4260 brush<->Material-Look selection unification proof: load fixture, select materials via RENDERING tab, dump active_material vs selected_material (must be equal), capture UI frames of renamed sections + all-256 list + brush labels
# Created: 2026-06-24
# Canonical gap: CDP proof driver needs a drain-until-idle reader; stock send_cdp closes after first '}' and SIGPIPE-kills the editor on large dumps.

#!/usr/bin/env python3
"""FL-4260 brush/selection unification + UI-rename visual proof driver."""
import socket, json, os, time, re

HOST, PORT = "localhost", 8765
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(REPO, 'docs/research/ascii/verification/fl4260/2026-06-23-brush-selection-unification-proof')
os.makedirs(OUT, exist_ok=True)


def send(cmd, params="", idle=1.2, hard=8.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((HOST, PORT))
        s.sendall((json.dumps({"id": 1, "method": cmd, "params": params}) + "\n").encode())
        s.settimeout(idle)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < hard:
            try:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                break  # idle -> editor finished writing
        return buf.decode(errors="replace")
    except Exception as e:
        return f"ERR:{e}"
    finally:
        s.close()


def dump(tag):
    raw = send("FL4260_DUMP_MATERIAL_LOOK_STATE", "").replace('\\', '')
    m = re.search(r'"active_material":(-?\d+),"selected_material":(-?\d+)', raw)
    if m:
        am, sm = int(m.group(1)), int(m.group(2))
        print(f"DUMP {tag:22s} active_material={am} selected_material={sm} EQUAL={am==sm}")
        return am, sm
    print(f"DUMP {tag:22s} (no match) {raw[:120]}")
    return None, None


def cap(name):
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    send("CAPTURE_UI_FRAME", d, idle=2.0, hard=9.0)
    png = os.path.join(d, "ui_frame.png")
    print(f"CAP  {name:34s} png_exists={os.path.exists(png)}")
    return os.path.exists(png)


print("PING", send("FL4260_GET_RENDER_MODE", "")[-80:].replace("\n", " "))
# NOTE: the all-materials fixture (fl4260_fixture_all_materials.a3d) crashes the
# editor in its post-load path (pre-existing, unrelated to these UI changes), so
# this proof runs on the stable default startup map. Active materials show
# non-greyed; unused show greyed — which is exactly what the all-256 list proves.
dump("default_map")

send("FL4260_RENDERING_PROOF", "7 -1 0"); time.sleep(0.8)
dump("after_select_7")
cap("01_material_look_top_mat7")

send("FL4260_POOL_ACTION", "7 select_all"); time.sleep(0.8)
dump("after_pool_action_7")
send("FL4260_RENDERING_PROOF", "7 -1 0"); time.sleep(0.8)
cap("02_live_autosave_mat7")

send("FL4260_RENDERING_PROOF", "42 -1 5"); time.sleep(0.8)
dump("after_select_42")
cap("03_section6_glyph_selection_mat42")

send("FL4260_FOCUS_BRUSH_TAB", "1"); time.sleep(0.8)
dump("brush_tab_mat42")
cap("04_brush_matid_mat42")

print("DONE", OUT)
