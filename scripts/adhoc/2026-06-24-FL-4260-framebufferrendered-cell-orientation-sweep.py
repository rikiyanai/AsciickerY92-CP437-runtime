# Ad hoc script: FL-4260 framebuffer/rendered-cell orientation sweep
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from PIL import Image


def read_jsonl(path):
    header = None
    cells = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('kind') == 'header':
            header = row
        elif row.get('kind') == 'cell':
            cells[(int(row['x']), int(row['y']))] = row
    return header, cells


def rt(c):
    return (c.get('glyph_id'), c.get('sidecar_gid'), c.get('material_id'), c.get('fg'), c.get('bg'), c.get('spare'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--artifact-dir', required=True)
    args = ap.parse_args()
    art = Path(args.artifact_dir)
    h, before = read_jsonl(art / 'before.termpp_buffer.jsonl')
    _h2, after = read_jsonl(art / 'after_scoring.termpp_buffer.jsonl')
    bimg = Image.open(art / 'before.termpp.png').convert('RGBA')
    aimg = Image.open(art / 'after_scoring.termpp.png').convert('RGBA')
    gw, gh = int(h['w']), int(h['h'])
    cw, ch = bimg.width // gw, bimg.height // gh
    rendered_changed = {k for k in before.keys() & after.keys() if rt(before[k]) != rt(after[k])}
    pixel_changed = set()
    bp, apx = bimg.load(), aimg.load()
    for y in range(gh):
        for x in range(gw):
            hit = False
            for py in range(y*ch, (y+1)*ch):
                for px in range(x*cw, (x+1)*cw):
                    if bp[px, py] != apx[px, py]:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                pixel_changed.add((x,y))
    transforms = {
        'identity': lambda x,y:(x,y),
        'yflip': lambda x,y:(x,gh-1-y),
        'xflip': lambda x,y:(gw-1-x,y),
        'xyflip': lambda x,y:(gw-1-x,gh-1-y),
    }
    out = {'grid':[gw,gh], 'cell':[cw,ch], 'rendered_changed':len(rendered_changed), 'pixel_changed':len(pixel_changed), 'transforms':{}}
    for name, fn in transforms.items():
        mapped = {fn(x,y) for x,y in rendered_changed}
        out['transforms'][name] = {
            'overlap': len(mapped & pixel_changed),
            'rendered_without_pixels': len(mapped - pixel_changed),
            'pixels_without_rendered': len(pixel_changed - mapped),
        }
    print(json.dumps(out, indent=2))
    (art / 'orientation_sweep.json').write_text(json.dumps(out, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
