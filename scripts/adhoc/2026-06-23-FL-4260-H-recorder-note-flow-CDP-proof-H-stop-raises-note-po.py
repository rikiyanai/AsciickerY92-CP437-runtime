# Ad hoc script: FL-4260 H-recorder note-flow CDP proof
# Created: 2026-06-23
# Proves the full UX: H starts, an action is recorded, H stops -> note window
# stays open (file not finalized), clicking the tagged "Save note" button
# finalizes with session_stop.

import sys, time, json, glob
sys.path.insert(0, 'scripts')
from fl4260_cdp_audit import send_cdp

def cdp(cmd, params=None, wait=0.5):
    r = send_cdp(cmd, params); time.sleep(wait); return str(r.get('result', ''))

def parse_rects(text):
    out = []
    for ln in text.splitlines():
        if 'CTRL_RECT' not in ln: continue
        d = {}
        for p in ln.split():
            for k in ('label', 'x', 'y', 'w', 'h'):
                if p.startswith(k + '='): d[k] = p.split('=', 1)[1]
        if 'label' in d and 'x' in d:
            d['x'] = float(d['x']); d['y'] = float(d['y']); d['w'] = float(d['w']); d['h'] = float(d['h'])
            out.append(d)
    return out

def kinds():
    fs = sorted(glob.glob('.run/asciiid_recordings/session_*.jsonl'))
    if not fs: return []
    return [json.loads(l)['kind'] for l in open(fs[-1]) if l.strip()]

for m, p in [('NEW_MAP', None), ('FL4260_SET_SIDEBAR_WIDTH', '1120'),
             ('FL4260_RENDERING_PROOF', '1 -1 0'), ('FL4260_LOCK_SIDEBAR_TAB', '9'), ('OPEN_TERMPP', None)]:
    cdp(m, p, 0.5)

cdp('RUN_SDL_KEY', '11', 2.0)  # H ON
cdp('FL4260_CTRL_RECTS_RECORD', '1', 0.25); rt = cdp('FL4260_CTRL_RECTS_RECORD', '0', 0.25)
for r in parse_rects(rt):
    if r['label'] == 'starters.add_all' and 0 <= r['x'] < 800 and 0 <= r['y'] < 560:
        cdp('RUN_MOUSE_CLICK_PROBE', f"{int(r['x']+r['w']/2)} {int(r['y']+r['h']/2)}", 0.7); break

cdp('RUN_SDL_KEY', '11', 2.5)  # H STOP -> note window
mid = kinds()
print('MID (after stop):', {x: mid.count(x) for x in set(mid)}, '| session_stop?', 'session_stop' in mid)

cdp('FL4260_CTRL_RECTS_RECORD', '1', 0.25); rt2 = cdp('FL4260_CTRL_RECTS_RECORD', '0', 0.25)
saved = False
for r in parse_rects(rt2):
    if r['label'] == 'hrec.save_note':
        x = int(r['x'] + r['w'] / 2); y = int(r['y'] + r['h'] / 2)
        print('  found hrec.save_note @', x, y, '->', cdp('RUN_MOUSE_CLICK_PROBE', f'{x} {y}', 1.5)[:40])
        saved = True
        break
if not saved:
    print('  hrec.save_note rect NOT found')

fin = kinds()
print('FINAL:', {x: fin.count(x) for x in set(fin)}, '| session_stop?', 'session_stop' in fin)
