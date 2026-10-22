"""Construction/motion contracts, not a perceptual-quality certificate."""
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('car_author',ROOT/'scripts/author_fl4831_car_turntables.py')
author=importlib.util.module_from_spec(spec)
spec.loader.exec_module(author)


@pytest.fixture(scope='module')
def picture():
    if not author.CHAPEL.is_dir():pytest.skip('Declared external authoring dependencies unavailable')
    return author.dependencies()[0]


@pytest.mark.parametrize('key',author.SPECS)
def test_rotating_parts_not_camera_trick_and_exact_loop(picture,key):
    p=author.build_car(picture,key)
    paths={q['id']:q['p'].copy() for q in p.paths}
    tones={q['id']:q['p'].copy() for q in p.tones}
    base=np.asarray(p.solids.v).copy()
    author.set_wheels(p,1/96)
    current={q['id']:q['p'] for q in p.paths}
    for part in p.wheel_parts:
        fid=part['prefix']+'valve';c=part['center']
        assert not np.allclose(paths[fid],current[fid])
        assert np.allclose(np.linalg.norm(paths[fid]-c,axis=1),np.linalg.norm(current[fid]-c,axis=1))
        assert np.allclose(current[fid],author.rotate_axle(paths[fid],c,-np.pi/48))
        tone=next(q for q in p.tones if q['id'].startswith(part['prefix']))
        assert np.allclose(tone['p'],author.rotate_axle(tones[tone['id']],c,-np.pi/48))
        lo,hi=part['vertices']
        assert np.allclose(np.asarray(p.solids.v)[lo:hi],author.rotate_axle(base[lo:hi],c,-np.pi/48))
        # The prior follows the same physical motion, without mutating its input.
        old=[dict(parts=[dict(feature=fid,world=paths[fid].tolist())])]
        untouched=copy.deepcopy(old)
        moved=author.move_wheel_prior(old,p,-np.pi/48)
        assert old==untouched and np.allclose(moved[0]['parts'][0]['world'],current[fid])
    for fid in paths:
        if not fid.startswith('wheel.'):assert np.array_equal(paths[fid],current[fid])
    author.set_wheels(p,1)
    assert np.allclose(base,p.solids.v)
    for q in p.paths:assert np.allclose(q['p'],paths[q['id']])
    author.set_wheels(p,0)
    assert np.array_equal(base,p.solids.v)


def test_forward_direction_and_repeated_pattern_sampling():
    # Independent of implementation sign: top must advance noseward (-Z),
    # contact-side surface rearward (+Z), for forward translation along -Z.
    center=np.array([0.,.35,0.]);angle=-2*np.pi/96
    top=author.rotate_axle([[0,.70,0]],center,angle)[0]
    bottom=author.rotate_axle([[0,0,0]],center,angle)[0]
    assert top[2]<0<bottom[2]
    assert 360/96<min(360/35,360/24)/2


def test_911_continuous_crown_and_visible_roof_all_quadrants(picture):
    p=author.build_car(picture,'porsche911')
    assert p.roof_surface(.18,0)[1]>p.roof_surface(.18,.8)[1]
    assert p.roof_surface(.18,0)[1]>p.roof_surface(-.43,0)[1]
    assert p.roof_surface(.18,0)[1]>p.roof_surface(1.,0)[1]
    for i in (0,8,16,24,40,48,64,72,88):
        d=author.compose_car(p,i/96)
        ix=[j for j,f in enumerate(d['features']) if f['id'].startswith('911.roof.silhouette')]
        assert ix and any(np.any(d['samples'][:,8]==j) for j in ix)


def test_fictional_plate_content_covered_stable_and_distinct(picture):
    chars={g['char'] for g in json.loads((author.CHAPEL/'catalog.json').read_text())['glyphs']}
    registrations=[]
    for key in author.SPECS:
        p=author.build_car(picture,key)
        assert len(p.plates)==2
        assert p.plates[0]['visible_text']==p.plates[1]['visible_text']
        assert set(p.plates[0]['visible_text'])<=chars
        assert p.plates[0]['visible_text']==author.build_car(picture,key).plates[0]['visible_text']
        registrations.append(p.plates[0]['registration'])
    assert len(set(registrations))==len(author.SPECS)


def test_new_subjects_are_distinct_constructed_parts(picture):
    kei=author.build_car(picture,'kei_sambar');coach=author.build_car(picture,'teardrop1930')
    kid={q['id'] for q in kei.paths};cid={q['id'] for q in coach.paths}
    assert 'bed.floor-rim' in kid and 'bed.tailgate' in kid
    assert sum('.latch' in x for x in kid)==8
    assert 'radiator.frame' in cid and 'bonnet.center' in cid
    assert all(f'fender.{s}.{j}.outer' in cid for s in (-1,1) for j in (0,1))
    assert any('.wire' in x for x in cid) and not any('.wire' in x for x in kid)
    assert len(kei.voids)>=4 and len(coach.voids)>=7
    assert any(x['id'].startswith('body.') for x in coach.tones)


@pytest.mark.parametrize('key',author.SPECS)
def test_wheel_drawing_changes_with_camera_held_fixed(picture,key):
    p=author.build_car(picture,key)
    author.set_wheels(p,0)
    a=author.compose_car(p,0)
    author.set_wheels(p,1/96)
    b=author.compose_car(p,0)
    assert np.array_equal(a['camera'].eye,b['camera'].eye)
    assert np.any(a['target']!=b['target'])
    changed=np.argwhere(a['target']!=b['target'])[:,::-1]
    # No screen-wide dither phase. Differences belong to projected wheel
    # volumes, with a native-row margin for deliberate tone reduction.
    allowed=np.zeros((author.H,author.W),bool)
    for part in p.wheel_parts:
        lo,hi=part['vertices'];q=a['camera'].project(np.asarray(p.solids.v)[lo:hi])
        x0,y0=np.maximum([0,0],np.floor(q[:,:2].min(0)-18).astype(int))
        x1,y1=np.minimum([author.W,author.H],np.ceil(q[:,:2].max(0)+18).astype(int))
        allowed[y0:y1,x0:x1]=True
    assert np.all(allowed[changed[:,1],changed[:,0]])
    author.set_wheels(p,1)
    c=author.compose_car(p,0)
    assert np.array_equal(a['target'],c['target'])
