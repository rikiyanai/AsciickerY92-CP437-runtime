# Ad hoc script: FL-4260 H-recorder universal-action CDP proof
# Created: 2026-06-23
# Proves the H-key recorder captures EVERY clicked control. Re-records rects
# immediately before each click (so coords are never stale after a layout
# change) and clicks only real buttons, expecting src=semantic action records.

import sys, time
sys.path.insert(0, 'scripts')
from fl4260_cdp_audit import send_cdp

def cdp(cmd, params=None, wait=0.4):
    r = send_cdp(cmd, params)
    time.sleep(wait)
    return str(r.get('result', ''))

def parse_rects(text):
    rects = []
    for line in text.splitlines():
        if 'CTRL_RECT' not in line:
            continue
        d = {}
        for p in line.split():
            for k in ('label', 'x', 'y', 'w', 'h'):
                if p.startswith(k + '='):
                    d[k] = p.split('=', 1)[1]
        if 'label' in d and 'x' in d:
            d['x'] = float(d['x']); d['y'] = float(d['y'])
            d['w'] = float(d['w']); d['h'] = float(d['h'])
            rects.append(d)
    return rects

def fresh_rects():
    cdp('FL4260_CTRL_RECTS_RECORD', '1', 0.25)
    txt = cdp('FL4260_CTRL_RECTS_RECORD', '0', 0.25)
    return parse_rects(txt)

def click_label(label):
    # Re-record rects right now so the coordinate reflects the CURRENT layout.
    rects = fresh_rects()
    for r in rects:
        if r.get('label') == label and 0 <= r['x'] < 800 and 0 <= r['y'] < 560 and r['w'] > 0:
            x = int(r['x'] + r['w'] * 0.5); y = int(r['y'] + r['h'] * 0.5)
            cdp('RUN_MOUSE_CLICK_PROBE', f'{x} {y}', 0.7)
            return f'clicked {label} @ {x},{y}'
    return f'SKIP {label} (not visible this frame)'

# Stage Material Look so tagged starter buttons are on screen
for m, p in [('NEW_MAP', None), ('FL4260_SET_SIDEBAR_WIDTH', '1120'),
             ('FL4260_RENDERING_PROOF', '1 -1 0'), ('FL4260_LOCK_SIDEBAR_TAB', '9'),
             ('OPEN_TERMPP', None)]:
    print('setup', m, '->', cdp(m, p, 0.5)[:60])

print('H ON ->', cdp('RUN_SDL_KEY', '11', 2.0)[:60])

# Click real buttons only (NO collapsing headers). Re-record before each so the
# layout shifts caused by a previous click never produce stale coordinates.
for label in ['starters.add_all', 'starters.preset_0_GRASS',
              'starters.glyph_style_presets', 'starters.color_presets']:
    print(' ', click_label(label))
    # restore the top starter region in case a toggle scrolled/opened a section
    cdp('FL4260_RENDERING_PROOF', '1 -1 0', 0.4)

print('H OFF ->', cdp('RUN_SDL_KEY', '11', 2.0)[:60])
print('driver done')
