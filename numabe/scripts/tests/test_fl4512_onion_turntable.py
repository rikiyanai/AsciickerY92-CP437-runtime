"""Native onion artifact and actual-object-motion checks, not art approval."""
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image,ImageDraw,ImageFont
import pytest
from scipy.ndimage import distance_transform_edt

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import author_fl4512_onion_turntable as onion


def test_original_shape_rotates_with_fixed_camera_and_no_random_dither():
    if not onion.core.CHAPEL.is_dir():pytest.skip('Declared local fitter unavailable')
    picture=onion.core.dependencies()[0];p=onion.build(picture)
    a=onion.compose(p,0);base={q['id']:q['p'].copy() for q in p.paths}
    vertices=np.asarray(p.solids.v).copy()
    b=onion.compose(p,.25);paths={q['id']:q['p'] for q in p.paths}
    assert np.array_equal(a['camera'].eye,b['camera'].eye)
    assert not np.array_equal(a['target'],b['target'])
    assert np.allclose(p.solids.v,onion.spin(vertices,np.pi/2))
    for fid in base:
        if not fid.startswith('contour.'):
            assert np.allclose(paths[fid],onion.spin(base[fid],np.pi/2))
    assert np.array_equal(b['target'],onion.compose(p,.25)['target'])
    assert np.array_equal(a['target'],onion.compose(p,0)['target'])
    assert np.array_equal(a['target'],onion.compose(p,1)['target'])
    assert np.isclose(np.dot(onion.AXIS,[0,1,0]),np.cos(np.deg2rad(35)))


def test_reference_binding_and_preserved_sources():
    c=json.loads((onion.OUT/'construction.json').read_text())
    assert c['reference_page']==onion.PAGE
    assert c['projection_ratio']==.4 and c['tilt_degrees']==35
    assert c['revision']==4 and 'pointed taper' in c['reauthoring']
    assert {r['id'] for r in c['user_references']}=={'image-1','image-2'}
    for r in c['user_references']:assert onion.core.sha(ROOT/r['preserved'])==r['sha256']
    assert {r['id'] for r in c['references']}=={'res213','res215','res217','res219'}
    for r in c['references']:
        assert onion.core.sha(r['text'])==r['text_sha256']
        assert onion.core.sha(r['png'])==r['png_sha256']
    assert onion.core.sha(ROOT/c['preserved_generator'])==c['generator_sha256']
    assert onion.core.sha(ROOT/c['preserved_adapter'])==c['shared_adapter_sha256']
    assert any('skin.' in p['id'] for p in c['paths'])
    assert 'not perceptual acceptance' in json.loads((onion.OUT/'validation.json').read_text())['validation']


def test_pointed_silhouette_and_skin_arcs_converge_as_supplied_references():
    picture=onion.core.dependencies()[0];p=onion.build(picture)
    peak=int(np.argmax(onion.RADII))
    assert onion.HEIGHTS[peak]<(onion.HEIGHTS[0]+onion.HEIGHTS[-1])/2
    assert onion.PROFILE(2.45)<onion.RADII.max()*.10
    assert min(f[2]-f[1] for f in onion.FOLDS)>2
    gaps=np.diff(sorted(f[0] for f in onion.FOLDS))
    assert not np.allclose(gaps,gaps[0])
    ids={q['id'] for q in p.base_paths}
    assert 'tip.fiber1' in ids and 'root.basal-rim' in ids
    assert not any(x.startswith('neck.') for x in ids)
    assert not any('.fray' in x or '.crease' in x for x in ids)
    flows=[q for q in p.base_paths if q['id'].startswith('skin.flow')]
    assert len(flows)==5
    for f in onion.FOLDS:
        for y in (f[1],f[2]):
            point=onion.surface(y,onion.fold_angle(f,y))
            assert np.linalg.norm(point[[0,2]])<.20
    for phase in (0,.25,.5,.75):
        d=onion.compose(p,phase)
        ix=[i for i,f in enumerate(d['features']) if f['id'].startswith('contour.')]
        assert ix and any(np.any(d['samples'][:,8]==i) for i in ix)


def test_complete_gif_native_rows_margins_and_motion():
    v=json.loads((onion.OUT/'validation.json').read_text())
    gif=Image.open(v['file']);assert gif.size==(1280,952) and gif.n_frames==96
    assert onion.core.sha(v['file'])==v['sha256']
    font=ImageFont.truetype(str(onion.core.CHAPEL/'Saitamaar.ttf'),16)
    eyes=[];hashes=set();total=0
    for i in range(96):
        gif.seek(i);pixels=np.asarray(gif.convert('L'));total+=gif.info['duration']
        native=Image.open(onion.OUT/'frames'/f'{i:03}.png').convert('L')
        assert np.array_equal(pixels,np.asarray(native.resize(gif.size,Image.Resampling.NEAREST)))
        assert set(np.unique(pixels)).issubset({0,255})
        record=json.loads((onion.OUT/'frames'/f'{i:03}.json').read_text());s=record['summary']
        assert s['phase_degrees']==i*3.75
        assert s['ink_overlap']==s['advance_overlap']==s['unsupported_ink']==0
        x0,y0,x1,y1=s['ink_bbox'];assert 0<x0<x1<640 and 0<y0<y1<476
        intended=np.asarray(Image.open(onion.OUT/'frames'/f'{i:03}-intended.png'))<127
        assert np.all(distance_transform_edt(~intended)[np.asarray(native)<127]<=8.5)
        check=Image.new('L',(640,476),255);draw=ImageDraw.Draw(check)
        for row,text in enumerate(record['rows']):draw.text((0,row*17),text,font=font,fill=0,anchor='la')
        assert np.array_equal(np.asarray(check)>127,np.asarray(native)>127)
        eyes.append(s['camera_eye']);hashes.add(native.tobytes())
    assert total==7680 and len(hashes)==96
    assert np.allclose(eyes,eyes[0])
    baseline=onion.OUT.parent/'frames'
    original=json.loads((baseline/'000.json').read_text())['summary']['ink_bbox']
    small=json.loads((onion.OUT/'frames/000.json').read_text())['summary']['ink_bbox']
    # Allow glyph-width quantization and changed oblique envelope; reject an
    # unchanged-size image or a bitmap-only downsample of the original glyphs.
    assert .30<(small[2]-small[0])/(original[2]-original[0])<.55
    assert .30<(small[3]-small[1])/(original[3]-original[1])<.55
    accepted_size=json.loads((onion.OUT.parent/'revision-02/frames/000.json').read_text())['summary']['ink_bbox']
    assert .80<(small[2]-small[0])/(accepted_size[2]-accepted_size[0])<1.20
    assert .80<(small[3]-small[1])/(accepted_size[3]-accepted_size[1])<1.20
    if 'desktop' in v:assert onion.core.sha(v['desktop'])==v['sha256']
