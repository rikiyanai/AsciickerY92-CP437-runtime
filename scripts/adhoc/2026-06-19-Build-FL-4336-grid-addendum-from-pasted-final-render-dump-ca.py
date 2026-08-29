# Ad hoc script: Build FL-4336 grid addendum from pasted final-render dump camera poses
# Created: 2026-06-19
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
BASE_GRID = ROOT / 'docs/research/ascii/verification/fl4336/2026-06-19-attempt-177-grid/FL4336_ATTEMPT177_CLONE_GRID.png'
DEFAULT_DUMPS = [
    ROOT / '.run/final_render_cell_dump/1781866280_107025',
    ROOT / '.run/final_render_cell_dump/1781866841_667835',
]
OUT_DIR = ROOT / 'docs/research/ascii/verification/fl4336/2026-06-19-attempt-178-dump-pose-addendum'
OFFICIAL_OUT_DIR = ROOT / 'docs/research/ascii/verification/fl4336/2026-06-19-attempt-179-official-dump-pose-grid'
OFFICIAL_CAPTURE_ROOT = OFFICIAL_OUT_DIR / 'panels'
OFFICIAL_POSES = [
    {
        'id': 'dump1781866280',
        'dump': ROOT / '.run/final_render_cell_dump/1781866280_107025',
        'captured_projection': 'PERSPECTIVE',
        'player': [56.3897247314453, 9.77867698669434, 58.5685157775879],
        'yaw_deg': 39.03468579975313,
    },
    {
        'id': 'dump1781866841',
        'dump': ROOT / '.run/final_render_cell_dump/1781866841_667835',
        'captured_projection': 'ORTHOGONAL',
        'player': [-2.03723335266113, 18.1532421112061, 60.8741264343262],
        'yaw_deg': 30.854272614319108,
    },
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def font(size: int) -> ImageFont.ImageFont:
    for candidate in [
        '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/Library/Fonts/Arial.ttf',
    ]:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = ''
        for word in raw.split(' '):
            candidate = word if not line else f'{line} {word}'
            if draw.textbbox((0, 0), candidate, font=fnt)[2] <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = word
        lines.append(line)
    return lines


def draw_text_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=fill)
    y = y0 + 8
    line_h = max(16, int(getattr(fnt, 'size', 13) * 1.25))
    for line in wrap(draw, text, fnt, x1 - x0 - 16):
        if y + line_h > y1:
            break
        draw.text((x0 + 8, y), line, fill=(240, 242, 244), font=fnt)
        y += line_h


def read_projection(meta: dict[str, Any]) -> str:
    fl4270 = meta.get('fl4270_capture') or {}
    for sample in fl4270.get('capture_states') or []:
        proj = sample.get('camera_projection')
        if proj:
            return str(proj)
    scope = meta.get('perspective_capture_scope') or meta.get('camera_sync_scope') or {}
    samples = scope.get('samples') or []
    for sample in samples:
        proj = sample.get('camera_projection')
        if proj:
            return str(proj)
    cam = meta.get('camera') or {}
    if cam.get('projection'):
        return str(cam['projection'])
    if cam.get('perspective') is True:
        return 'PERSPECTIVE'
    if cam.get('perspective') is False:
        return 'ORTHOGONAL'
    return 'UNKNOWN'


def dump_panel(dump_dir: Path, panel_w: int, img_h: int, label_h: int) -> tuple[Image.Image, dict[str, Any]]:
    meta_path = dump_dir / 'metadata.json'
    png_path = dump_dir / 'final.png'
    if not meta_path.exists():
        raise SystemExit(f'missing metadata: {meta_path}')
    if not png_path.exists():
        raise SystemExit(f'missing final png: {png_path}')
    meta = json.loads(meta_path.read_text())
    img = Image.open(png_path).convert('RGB')
    scale = min(panel_w / img.width, img_h / img.height)
    resized = img.resize((int(round(img.width * scale)), int(round(img.height * scale))), Image.Resampling.LANCZOS)
    panel = Image.new('RGB', (panel_w, label_h + img_h), (10, 11, 14))
    panel.paste(resized, ((panel_w - resized.width) // 2, label_h + (img_h - resized.height) // 2))
    projection = read_projection(meta)
    feature_state = meta.get('feature_state') or {}
    camera_world = meta.get('camera_world') or {}
    label = '\n'.join([
        f'DUMP {dump_dir.name}  {projection}',
        f'yaw={meta.get("camera_yaw")} pitch={meta.get("camera_pitch")}',
        f'camera_world=({camera_world.get("x")}, {camera_world.get("y")}, {camera_world.get("z")})',
        'features: clouds={clouds} cloud_shadow_deep={cloud_shadow_deep} ramp={material_ramp_steps} dither={surface_stable_dither} glyph_distinctness={glyph_distinctness} weather={weather_kind_name}'.format(**feature_state),
        f'source={rel(png_path)}',
    ])
    d = ImageDraw.Draw(panel)
    draw_text_box(d, (0, 0, panel_w, label_h), label, font(14), (24, 30, 38))
    info = {
        'dump_dir': rel(dump_dir),
        'final_png': rel(png_path),
        'metadata_json': rel(meta_path),
        'sha256_final_png': sha256(png_path),
        'camera_world': camera_world,
        'camera_yaw': meta.get('camera_yaw'),
        'camera_pitch': meta.get('camera_pitch'),
        'projection': projection,
        'feature_state_subset': {k: feature_state.get(k) for k in [
            'clouds', 'cloud_density', 'cloud_shadow_deep', 'material_ramp_steps',
            'surface_stable_dither', 'glyph_distinctness', 'weather_kind_name'
        ]},
    }
    return panel, info


def load_labeled_panel(path: Path, label: str, panel_w: int, img_h: int, label_h: int) -> tuple[Image.Image, dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f'missing official panel: {path}')
    img = Image.open(path).convert('RGB')
    scale = min(panel_w / img.width, img_h / img.height)
    resized = img.resize((int(round(img.width * scale)), int(round(img.height * scale))), Image.Resampling.LANCZOS)
    panel = Image.new('RGB', (panel_w, label_h + img_h), (9, 10, 13))
    panel.paste(resized, ((panel_w - resized.width) // 2, label_h + (img_h - resized.height) // 2))
    draw_text_box(ImageDraw.Draw(panel), (0, 0, panel_w, label_h), label, font(13), (22, 28, 36))
    frame_prefix = path.with_suffix('').name + '_frame_'
    frame_sequence = sorted(p for p in path.parent.glob(f'{frame_prefix}*.png'))
    return panel, {
        'path': rel(path),
        'sha256': sha256(path),
        'source_size': [img.width, img.height],
        'displayed_frame': rel(path),
        'frame_sequence': [rel(p) for p in frame_sequence],
        'frame_sequence_sha256': {rel(p): sha256(p) for p in frame_sequence},
    }


def build_official_pose_grid(capture_root: Path, out_dir: Path, attempt: int, attempt_note: str) -> int:
    panel_w = 560
    img_h = 350
    label_h = 128
    row_label_w = 330
    gap = 14
    margin = 18
    header_h = 250
    row_h = label_h + img_h + 18
    cols = [
        ('clone_ortho', 'CLONE ORTHO'),
        ('current_ortho', 'CURRENT ORTHO'),
        ('current_perspective', 'CURRENT PERSPECTIVE'),
    ]
    width = margin * 2 + row_label_w + gap + len(cols) * panel_w + (len(cols) - 1) * gap
    height = header_h + row_h * len(OFFICIAL_POSES) + margin
    canvas = Image.new('RGB', (width, height), (13, 14, 17))
    draw = ImageDraw.Draw(canvas)
    header = '\n'.join([
        f'FL-4336 / FL-4381 ATTEMPT {attempt} OFFICIAL DUMP-POSE GRID',
        f'generated={datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}',
        'rows=operator-pasted final_render_cell_dump poses; columns=clone ortho, current ortho, current perspective',
        attempt_note,
        'Forward capture contract: fullscreen source frames, frame-sequence PNGs per projection, first frame may be displayed in this grid only with every source frame listed in the manifest.',
        'status=review artifact only; visual acceptance still requires operator signoff',
    ])
    draw_text_box(draw, (0, 0, width, header_h), header, font(17), (32, 36, 44))

    y = header_h
    panels: list[dict[str, Any]] = []
    for pose in OFFICIAL_POSES:
        dump_dir = Path(pose['dump'])
        meta = json.loads((dump_dir / 'metadata.json').read_text())
        feature_state = meta.get('feature_state') or {}
        row_label = '\n'.join([
            f"{pose['id']}  dumped_projection={pose['captured_projection']}",
            f"player=({pose['player'][0]:.4f}, {pose['player'][1]:.4f}, {pose['player'][2]:.4f})",
            f"camera_yaw={meta.get('camera_yaw')} rad / {pose['yaw_deg']:.4f} deg",
            f"camera_pitch={meta.get('camera_pitch')} rad",
            f"camera_world={meta.get('camera_world')}",
            'features: clouds={clouds} shadow={cloud_shadow_deep} ramp={material_ramp_steps} dither={surface_stable_dither} glyph_distinctness={glyph_distinctness} weather={weather_kind_name}'.format(**feature_state),
            f"source_dump={rel(dump_dir)}",
        ])
        draw_text_box(draw, (margin, y, margin + row_label_w, y + label_h + img_h), row_label, font(13), (28, 25, 31))
        x = margin + row_label_w + gap
        row_entries: dict[str, Any] = {
            'pose_id': pose['id'],
            'dump_dir': rel(dump_dir),
            'player': pose['player'],
            'yaw_deg': pose['yaw_deg'],
            'camera_yaw': meta.get('camera_yaw'),
            'camera_pitch': meta.get('camera_pitch'),
            'camera_world': meta.get('camera_world'),
            'dumped_projection': pose['captured_projection'],
            'feature_state_subset': {k: feature_state.get(k) for k in [
                'clouds', 'cloud_density', 'cloud_shadow_deep', 'material_ramp_steps',
                'surface_stable_dither', 'glyph_distinctness', 'weather_kind_name'
            ]},
            'panels': {},
        }
        panel_paths = {
            'clone_ortho': capture_root / str(pose['id']) / 'clone_ortho' / 'fl4270_ortho.png',
            'current_ortho': capture_root / str(pose['id']) / 'current' / 'fl4270_ortho.png',
            'current_perspective': capture_root / str(pose['id']) / 'current' / 'fl4270_perspective.png',
        }
        for key, title in cols:
            label = '\n'.join([
                title,
                f"pose={pose['id']}",
                f"yaw_deg={pose['yaw_deg']:.4f}",
                f"source={rel(panel_paths[key])}",
            ])
            panel, info = load_labeled_panel(panel_paths[key], label, panel_w, img_h, label_h)
            canvas.paste(panel, (x, y))
            row_entries['panels'][key] = info
            x += panel_w + gap
        panels.append(row_entries)
        y += row_h

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'FL4336_ATTEMPT{attempt}_OFFICIAL_DUMP_POSE_GRID.png'
    canvas.save(out_path)
    this_artifact_has_frame_sequences = all(
        bool(panel_info.get('frame_sequence'))
        for row in panels
        for panel_info in row.get('panels', {}).values()
    )
    manifest = {
        'artifact': rel(out_path),
        'sha256': sha256(out_path),
        'lane': ['FL-4336', 'FL-4381', 'FL-4231', 'FL-4377'],
        'attempt': attempt,
        'closure_claim': False,
        'proof_state': 'official-dump-pose-grid-generated; operator visual signoff required',
        'columns': [title for _, title in cols],
        'capture_contract': {
            'fullscreen_required': True,
            'frame_sequence_required': True,
            'required_flags': [
                '--fl4270-fullscreen',
                '--fl4270-capture-frames=N',
            ],
            'grid_display_policy': 'display frame_00 alias in the panel; manifest must list every captured frame',
        },
        'this_artifact_capture_conforms': this_artifact_has_frame_sequences,
        'this_artifact_capture_conformance_note': (
            'false means the source panels predate the fullscreen frame-sequence patch; '
            'rerun capture with --fl4270-fullscreen --fl4270-capture-frames=N before treating a new grid as conforming'
        ),
        'attempt_note': attempt_note,
        'poses': panels,
    }
    manifest_path = out_path.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'WROTE {out_path}')
    print(f'WROTE {manifest_path}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-grid', type=Path, default=BASE_GRID)
    parser.add_argument('--out-dir', type=Path, default=OUT_DIR)
    parser.add_argument('--official-pose-grid', action='store_true')
    parser.add_argument('--capture-root', type=Path, default=OFFICIAL_CAPTURE_ROOT)
    parser.add_argument('--attempt', type=int, default=179)
    parser.add_argument('--attempt-note', default='Attempt 179 official dump-pose rows generated from pasted final_render_cell_dump poses.')
    parser.add_argument('dumps', nargs='*', type=Path)
    args = parser.parse_args()
    if args.official_pose_grid:
        out_dir = args.out_dir if args.out_dir != OUT_DIR else OFFICIAL_OUT_DIR
        return build_official_pose_grid(args.capture_root, out_dir, args.attempt, args.attempt_note)

    dumps = args.dumps or DEFAULT_DUMPS
    base = Image.open(args.base_grid).convert('RGB')
    panel_w = 720
    img_h = 450
    label_h = 150
    gap = 16
    margin = 18
    header_h = 170
    f_header = font(18)

    panels = []
    infos = []
    for dump in dumps:
        panel, info = dump_panel(dump, panel_w, img_h, label_h)
        panels.append(panel)
        infos.append(info)

    width = max(base.width, margin * 2 + len(panels) * panel_w + (len(panels) - 1) * gap)
    addendum_h = header_h + label_h + img_h + margin
    out = Image.new('RGB', (width, base.height + addendum_h), (12, 13, 16))
    out.paste(base, ((width - base.width) // 2, 0))
    draw = ImageDraw.Draw(out)
    y0 = base.height
    header = '\n'.join([
        'FL-4336 / FL-4381 DUMP-POSE ADDENDUM: pasted final_render_cell_dump poses added below the Attempt 177 grid',
        f'generated={datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}',
        'purpose=show the exact manual dump poses that read as homogeneous, with camera and feature gates printed on-panel',
        'finding=both pasted dumps have clouds+cloud_shadow_deep+ramp on, but surface_stable_dither=false and glyph_distinctness=false',
        'status=review artifact only; this is not visual acceptance',
    ])
    draw_text_box(draw, (0, y0, width, y0 + header_h), header, f_header, (38, 34, 26))
    x = margin
    for panel in panels:
        out.paste(panel, (x, y0 + header_h))
        x += panel_w + gap

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / 'FL4336_ATTEMPT178_DUMP_POSE_ADDENDUM_GRID.png'
    out.save(out_path)
    manifest = {
        'artifact': rel(out_path),
        'sha256': sha256(out_path),
        'base_grid': rel(args.base_grid),
        'base_grid_sha256': sha256(args.base_grid),
        'lane': ['FL-4336', 'FL-4381', 'FL-4231', 'FL-4377'],
        'closure_claim': False,
        'proof_state': 'review-addendum-generated; operator visual signoff required',
        'dump_panels': infos,
    }
    manifest_path = out_path.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'WROTE {out_path}')
    print(f'WROTE {manifest_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
