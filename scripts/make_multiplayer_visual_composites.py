#!/usr/bin/env python3

import argparse
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw


def load_img(p: Path):
    if not p.exists():
        return None
    return Image.open(p).convert('RGB')


def hstack(imgs, pad=8, bg=(8, 8, 8)):
    imgs = [im for im in imgs if im is not None]
    if not imgs:
        return None
    h = max(im.height for im in imgs)
    w = sum(im.width for im in imgs) + pad * (len(imgs) - 1)
    out = Image.new('RGB', (w, h), bg)
    x = 0
    for im in imgs:
        y = (h - im.height) // 2
        out.paste(im, (x, y))
        x += im.width + pad
    return out


def placeholder(w=960, h=720, text='missing source'):
    im = Image.new('RGB', (w, h), (30, 30, 30))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, w - 1, h - 1), outline=(200, 60, 60), width=3)
    draw.text((24, 24), text, fill=(255, 255, 255))
    return im


def annotate(im, text):
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, im.width, 22), fill=(0, 0, 0))
    draw.text((6, 5), text, fill=(255, 255, 255))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out-dir', default='output/visual')
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    t1_before = load_img(out_dir / 'tab1_before.png')
    t2_before = load_img(out_dir / 'tab2_before.png')
    t1_after = load_img(out_dir / 'tab1_after_move.png')
    t2_after = load_img(out_dir / 'tab2_after_move.png')

    before = hstack([
        annotate(t1_before.copy(), 'tab1 before') if t1_before else None,
        annotate(t2_before.copy(), 'tab2 before') if t2_before else None,
    ])
    after = hstack([
        annotate(t1_after.copy(), 'tab1 after move') if t1_after else None,
        annotate(t2_after.copy(), 'tab2 after move') if t2_after else None,
    ])

    if before is None:
        before = placeholder(text='composite_before: missing tab screenshots')
    if after is None:
        after = placeholder(text='composite_after: missing tab screenshots')
    before.save(out_dir / 'composite_before.png')
    after.save(out_dir / 'composite_after.png')

    strip_frames = []
    for name in ['tab1_before.png', 'tab1_after_move.png', 'tab1_after_pickup.png', 'tab1_after_combat.png']:
        im = load_img(out_dir / name)
        if im is not None:
            strip_frames.append(annotate(im.copy(), name.replace('.png', '')))

    strip = hstack(strip_frames, pad=6)
    if strip is None:
        strip = placeholder(text='combat_sequence_strip: missing stage frames')
    strip.save(out_dir / 'combat_sequence_strip.png')

    print(f'output_dir={out_dir}')
    print(f'composite_before={out_dir / "composite_before.png"}')
    print(f'composite_after={out_dir / "composite_after.png"}')
    print(f'combat_sequence_strip={out_dir / "combat_sequence_strip.png"}')


if __name__ == '__main__':
    main()
