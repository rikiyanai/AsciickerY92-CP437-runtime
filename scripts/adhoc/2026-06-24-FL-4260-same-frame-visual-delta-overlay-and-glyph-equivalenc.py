# Ad hoc script: FL-4260 same-frame visual delta overlay and glyph equivalence summary
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw


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
    return (c.get('final_gid'), c.get('sidecar_gid'), c.get('extended'), c.get('fg'), c.get('bk'), c.get('spare'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--artifact-dir', required=True)
    args = ap.parse_args()
    art = Path(args.artifact_dir)
    h, before = read_jsonl(art / 'before.termpp_buffer.jsonl')
    _h2, after = read_jsonl(art / 'after_scoring.termpp_buffer.jsonl')
    before_img = Image.open(art / 'before.termpp.png').convert('RGBA')
    after_img = Image.open(art / 'after_scoring.termpp.png').convert('RGBA')
    gw, gh = int(h['w']), int(h['h'])
    cw, ch = before_img.width // gw, before_img.height // gh
    alignment = json.loads((art / 'pipeline_alignment.json').read_text(encoding='utf-8'))
    transform_detail = alignment['gates']['gameplay_fl4260_framebuffer_delta_matches_final_buffer_cells']['detail']
    transform_stats = json.loads(transform_detail)
    transform = transform_stats.get('framebuffer_transform', 'identity')
    def map_cell(x, y):
        if transform == 'yflip':
            return x, gh - 1 - y
        if transform == 'xflip':
            return gw - 1 - x, y
        if transform == 'xyflip':
            return gw - 1 - x, gh - 1 - y
        return x, y
    rendered_changed = {k for k in before.keys() & after.keys() if rt(before[k]) != rt(after[k])}
    rendered_changed_pixels = {map_cell(x, y) for x, y in rendered_changed}
    pixel_changed = set()
    bp, apx = before_img.load(), after_img.load()
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
    overlay = after_img.copy()
    draw = ImageDraw.Draw(overlay, 'RGBA')
    for x,y in rendered_changed_pixels:
        color = (255, 220, 0, 70)
        if (x,y) in pixel_changed:
            color = (0, 220, 80, 80)
        draw.rectangle([x*cw, y*ch, (x+1)*cw-1, (y+1)*ch-1], fill=color)
    for x,y in pixel_changed - rendered_changed_pixels:
        draw.rectangle([x*cw, y*ch, (x+1)*cw-1, (y+1)*ch-1], outline=(255,0,0,200), width=2)
    overlay.save(art / 'visual_delta_overlay.png')
    binary = Image.new('RGBA', after_img.size, (0,0,0,255))
    bdraw = ImageDraw.Draw(binary, 'RGBA')
    for x,y in rendered_changed_pixels:
        bdraw.rectangle([x*cw, y*ch, (x+1)*cw-1, (y+1)*ch-1], fill=(255,220,0,255))
    for x,y in pixel_changed:
        bdraw.rectangle([x*cw+4, y*ch+4, (x+1)*cw-5, (y+1)*ch-5], fill=(0,180,255,255))
    binary.save(art / 'visual_delta_binary.png')
    sheet = Image.new('RGBA', (before_img.width, before_img.height*2), (0,0,0,255))
    sheet.paste(before_img, (0,0))
    sheet.paste(after_img, (0,before_img.height))
    sdraw = ImageDraw.Draw(sheet, 'RGBA')
    sdraw.rectangle([0,0,900,32], fill=(0,0,0,180))
    sdraw.text((8,8), 'before: same-frame TERM++ PNG', fill=(255,255,255,255))
    sdraw.rectangle([0,before_img.height,1200,before_img.height+32], fill=(0,0,0,180))
    sdraw.text((8,before_img.height+8), 'after: material 1 scoring edit, same-frame TERM++ PNG', fill=(255,255,255,255))
    sheet.save(art / 'visual_before_after_sheet.png')
    changed_pairs = Counter((before[k]['final_gid'], after[k]['final_gid']) for k in rendered_changed)
    stable_pixel_materials = Counter((before[k].get('final_gid'), before[k].get('fg'), before[k].get('bk')) for k in (pixel_changed - rendered_changed_pixels) if k in before)
    summary = {
        'grid': [gw, gh],
        'cell_px': [cw, ch],
        'framebuffer_transform': transform,
        'rendered_changed_cells': len(rendered_changed),
        'pixel_changed_cells': len(pixel_changed),
        'rendered_changed_with_pixel_change': len(rendered_changed_pixels & pixel_changed),
        'rendered_changed_without_pixel_change': len(rendered_changed_pixels - pixel_changed),
        'pixel_changed_without_rendered_change': len(pixel_changed - rendered_changed_pixels),
        'top_gid_change_pairs': [{'before': a, 'after': b, 'count': n} for (a,b), n in changed_pairs.most_common(20)],
        'top_stable_rendered_pixel_changes': [{'final_gid': a, 'fg': b, 'bk': c, 'count': n} for (a,b,c), n in stable_pixel_materials.most_common(20)],
        'artifacts': ['visual_before_after_sheet.png', 'visual_delta_overlay.png', 'visual_delta_binary.png'],
    }
    (art / 'visual_pixel_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()
