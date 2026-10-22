"""Offline car-target artifact checks; NOT engine/artistic acceptance."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest
from scipy.ndimage import distance_transform_edt

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / 'assets/meshes/incoming/tutorial_cars.source.json'
TARGETS = json.loads(OWNER.read_text())['authored_turntable_targets']
CARS = {target['id']: target for target in TARGETS['targets']}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_visual_target_scope_and_six_requested_subjects():
    assert set(CARS) == {'bluebird510', 'cresta_gx71', 'ae86', 'porsche911','kei_sambar','teardrop1930'}
    assert TARGETS['revision']==4
    assert TARGETS['scope'].startswith('AUTHORED_TARGET_NOT_GAME_RENDER')
    assert TARGETS['status'].startswith('OPERATOR_APPROVED_VISUAL_TARGETS_20261021')
    assert 'runtime foreground acceptance OPEN' in TARGETS['status']
    assert [r['id'] for r in CARS['bluebird510']['references']] == ['resK-215', 'resK-216', 'resK-217']
    assert [r['id'] for r in CARS['cresta_gx71']['references']] == ['resK-94', 'resK-95']
    assert '329overhead' in CARS['porsche911']['retained_construction']
    assert 'NOT a documented' in CARS['teardrop1930']['authored_inference']
    baseline=json.loads(OWNER.read_text())['authored_turntable_targets_revision_01']
    assert len(baseline['targets'])==4
    for target in baseline['targets']:
        assert digest(ROOT/target['gif'])==target['gif_sha256']


@pytest.mark.parametrize('car', CARS)
def test_all_encoded_frames_match_native_pixels_and_complete_orbit(car):
    target = CARS[car]
    path = ROOT / target['gif']
    assert digest(path) == target['gif_sha256']
    gif = Image.open(path)
    assert gif.size == (1280, 952) and gif.n_frames == 96
    data_root = (ROOT / target['construction']).parent
    construction=json.loads((data_root/'construction.json').read_text())
    eyes = []
    visible_plates=set()
    elapsed = 0
    for index in range(gif.n_frames):
        gif.seek(index)
        actual = np.asarray(gif.convert('L'))
        native = Image.open(data_root / 'frames' / f'{index:03}.png').convert('L')
        expected = np.asarray(native.resize(gif.size, Image.Resampling.NEAREST))
        assert np.array_equal(actual, expected), (car, index)
        assert set(np.unique(actual)).issubset({0, 255})
        elapsed += gif.info['duration']
        record = json.loads((data_root / 'frames' / f'{index:03}.json').read_text())
        summary = record['summary']
        assert summary['ink_overlap'] == summary['advance_overlap'] == summary['protected_void_ink'] == 0
        assert summary['wheel_phase_degrees']==-index*3.75
        glyph_tokens={(g[1],g[2],g[3]) for g in record['glyphs']}
        for plate in summary['plate_labels']:
            visible_plates.add(plate['id'])
            assert any('\uff61'<=ch<='\uff9f' for ch in plate['visible_text'])
            assert ''.join(t['text'] for t in plate['tokens'])==plate['visible_text']
            assert all((t['x'],t['y'],t['gi']) in glyph_tokens for t in plate['tokens'])
        assert summary['unsupported_ink']==0 and summary['max_ink_support_distance']<=8.5
        intended=np.asarray(Image.open(data_root/'frames'/f'{index:03}-intended.png'))<127
        distance=distance_transform_edt(~intended)
        assert np.all(distance[np.asarray(native)<127]<=8.5)
        assert summary['moving_wheel_contributions']>0
        assert summary['admission']['selected_paths']<=summary['admission']['constructed_paths']
        x0, y0, x1, y1 = summary['ink_bbox']
        assert 0 < x0 < x1 < 640 and 0 < y0 < y1 < 476, (car, index, 'clipped car')
        eyes.append(summary['eye'])
    assert elapsed == target['duration_ms'] == 7680
    assert visible_plates=={'front.plate','rear.plate'}
    eyes = np.asarray(eyes)
    angles = np.unwrap(np.arctan2(eyes[:, 0], -eyes[:, 2]))
    assert np.allclose(np.diff(angles), 2 * math.pi / 96)
    assert np.allclose(np.linalg.norm(eyes[:, [0, 2]], axis=1), construction['orbit_radius'])
    assert np.allclose(eyes[:, 1], 2.1)
    assert math.isclose(angles[-1] - angles[0] + 2 * math.pi / 96, 2 * math.pi)


@pytest.mark.parametrize('car', CARS)
def test_preserved_authoring_source_and_independent_complete_text_replay(car):
    target = CARS[car]
    assert digest(ROOT / target['preserved_generator']) == target['generator_sha256']
    construction = json.loads((ROOT / target['construction']).read_text())
    assert construction['status'] == 'AUTHORED_TARGET_NOT_GAME_RENDER'
    assert construction['generator_sha256'] == target['generator_sha256']
    assert len(construction['protected_openings']) >= (4 if car=='kei_sambar' else 6)
    assert construction['wheel_motion']['turns_per_orbit']==-1
    assert len(construction['fictional_plates'])==2
    assert all(p['registration'].startswith('品川 330') for p in construction['fictional_plates'])
    for info in construction['native_text_dependencies'].values():
        assert digest(ROOT/info['preserved'])==info['sha256']
    assert len(construction['wheel_motion']['parts'])==4
    assert construction['fit_parameters']['direction']==24
    assert any(not q['id'].startswith('wheel.') for q in construction['bounded_tone_sites'])
    assert len({p['id'] for p in construction['paths']}) == len(construction['paths'])
    fonts = [Path(p) for p in construction['source_dependencies'] if p.endswith('Saitamaar.ttf')]
    assert len(fonts) == 1
    if not fonts[0].is_file():
        pytest.skip('Native replay requires the explicitly declared external Saitamaar font')
    assert digest(fonts[0]) == construction['source_dependencies'][str(fonts[0])]
    font = ImageFont.truetype(str(fonts[0]), 16)
    frame_root = (ROOT / target['construction']).parent / 'frames'
    for index in (0, 16, 40, 64, 88, 95):
        rows = json.loads((frame_root / f'{index:03}.json').read_text())['rows']
        image = Image.new('L', (640, 476), 255)
        draw = ImageDraw.Draw(image)
        for row, text in enumerate(rows):
            draw.text((0, row * 17), text, font=font, fill=0, anchor='la')
        native = Image.open(frame_root / f'{index:03}.png').convert('L')
        assert np.array_equal(np.asarray(image) > 127, np.asarray(native) > 127), (car, index)
