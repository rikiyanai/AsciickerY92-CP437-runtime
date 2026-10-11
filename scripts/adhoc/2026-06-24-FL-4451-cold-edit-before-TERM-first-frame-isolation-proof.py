# Ad hoc script: FL-4451 cold edit before TERM++ first frame isolation proof
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import json, os, socket, subprocess, sys, time
from collections import Counter
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
APP = os.path.join(ROOT, '.run', 'asciiid')
MAP = 'assets/a3d/game_map_y8.a3d'
PORT = 8765
OUT = os.path.join(ROOT, 'docs/research/ascii/verification/fl4260/2026-06-24-FL4451-cold-edit-isolation')
os.makedirs(OUT, exist_ok=True)

def send(method, params='', idle=2.0, hard=18.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(4)
    try:
        s.connect(('127.0.0.1', PORT))
        s.sendall((json.dumps({'id': 1, 'method': method, 'params': params}) + '\n').encode())
        s.settimeout(idle); out = b''; t0 = time.time()
        while time.time() - t0 < hard:
            try:
                chunk = s.recv(65536)
                if not chunk: break
                out += chunk
            except socket.timeout:
                break
        return out.decode(errors='replace')
    finally:
        s.close()

def wait_port(proc, seconds=12.0):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if proc.poll() is not None:
            raise RuntimeError('asciiid exited early')
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(0.2)
            s.connect(('127.0.0.1', PORT)); s.close(); return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('cdp port did not open')

def start(tag):
    log = open(os.path.join(OUT, tag + '.log'), 'w')
    proc = subprocess.Popen([APP, MAP, '--cdp', str(PORT)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    wait_port(proc)
    time.sleep(1.5)
    return proc, log

def stop(proc, log):
    try:
        send('QUIT', '', idle=0.5, hard=2.0)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=5)
    log.close()
    time.sleep(0.8)

def cap(tag):
    d = os.path.join(OUT, tag); os.makedirs(d, exist_ok=True)
    png = os.path.join(d, 'termpp.png')
    cells = os.path.join(d, 'cells.jsonl')
    bridge = os.path.join(d, 'bridge.jsonl')
    send('OPEN_TERMPP', 'harri=1', idle=3.0, hard=18.0)
    time.sleep(2.0)
    send('CAPTURE_TERMPP_FRAME_WITH_BUFFER', f'{png} {cells} {bridge}', idle=3.0, hard=20.0)
    time.sleep(1.5)
    return cells, bridge

def load_cells(path):
    rows = {}
    with open(path) as fh:
        for line in fh:
            obj = json.loads(line)
            if obj.get('kind') == 'cell':
                rows[(obj['x'], obj['y'])] = (obj['fg'], obj['bk'], obj.get('final_gid'))
    return rows

def load_mat(path):
    rows = {}
    with open(path) as fh:
        for line in fh:
            obj = json.loads(line)
            if obj.get('kind') == 'cell':
                rows[(obj['x'], obj['y'])] = obj.get('material_id')
    return rows

def diff(a, b):
    return [k for k in a if a.get(k) != b.get(k)]

# Baseline: open TERM++ first, no edit.
p, log = start('baseline')
try:
    base_cells, base_bridge = cap('baseline')
finally:
    stop(p, log)

# Cold edit: edit material 7 before TERM++ ever opens, then capture first TERM++ frame.
p, log = start('cold_edit_unused7')
try:
    edit_reply = send('FL4260_RENDERING_PROOF', '7 3 0', idle=2.0, hard=18.0)
    time.sleep(1.0)
    edit_cells, edit_bridge = cap('cold_edit_unused7')
finally:
    stop(p, log)

base = load_cells(base_cells)
edit = load_cells(edit_cells)
mat = load_mat(edit_bridge)
changed = diff(base, edit)
hist = Counter(mat.get(k) for k in changed)
summary = {
    'changed_final_cells': len(changed),
    'changed_material_histogram': dict(hist),
    'edit_reply_tail': edit_reply[-400:],
    'verdict': 'PASS' if len(changed) <= 5 else 'FAIL',
}
with open(os.path.join(OUT, 'summary.json'), 'w') as fh:
    json.dump(summary, fh, indent=2, sort_keys=True)
print(json.dumps(summary, indent=2, sort_keys=True))
