#!/usr/bin/env python3
"""FL-4831: authored AAHub-informed car targets, NOT game-render proof.

Original car volumes and selected construction are authored here. Opaque solids
answer visibility, never supply a triangle-edge inventory. The existing chapel
study's complete native-page fitter articulates that picture using Saitamaar;
its motion adapter follows all supported contributions to a glyph, after the
wheel-local rigid transforms are applied to the prior's world associations.
No archived AA pixels/text are inserted into a frame. Native text is retained.
"""
from __future__ import annotations

import argparse
import ctypes
import copy
import hashlib
import importlib.util
import json
import math
import random
import shutil
from pathlib import Path
import subprocess
import sys
import time
import types

import numpy as np
from PIL import Image, ImageDraw
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import distance_transform_edt

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = Path('/Users/r/Projects/ascii-art-archive/collections/aahub/jidousha')
CHAPEL = Path('/Users/r/Projects/trueff-shallot/evidence/ascii-authoring/church-graveyard-remake-20261015/checkpoint-remake')
BUILD = REPO / '.run/fl4831_car_target_authoring'
OUTPUT = REPO / 'articles/fl4831-aahub-car-turntables-20261019/revision-04'
W, H, PITCH, FOCAL, PAD = 640, 476, 17, 720., 0
WHEEL_TURNS=-1


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def smooth(t):
    return np.clip(t, 0, 1) ** 2 * (3 - 2 * np.clip(t, 0, 1))


def mix(a, b, t):
    return np.asarray(a) * (1-t) + np.asarray(b) * t


class Camera:
    def __init__(self, eye, target, focal=FOCAL):
        self.eye = np.asarray(eye, float)
        self.forward = unit(np.asarray(target)-self.eye)
        self.right = unit(np.cross(self.forward, [0, 1, 0]))
        self.up = np.cross(self.right, self.forward)
        self.axes = np.array([self.right, self.up, self.forward])
        self.focal = focal

    def project(self, points):
        q = (np.asarray(points)-self.eye) @ self.axes.T
        d = q[..., 2]
        return np.stack([W/2+self.focal*q[..., 0]/d,
                         H/2-self.focal*q[..., 1]/d, d], axis=-1)


RASTER = None


class Solids:
    def __init__(self, v=None, f=None):
        self.v = [] if v is None else list(v)
        self.f = [] if f is None else list(f)

    def quad(self, a, b, c, d):
        n = len(self.v)
        self.v.extend([a, b, c, d])
        self.f.extend([(n, n+1, n+2), (n, n+2, n+3)])

    def box(self, lo, hi):
        x, y, z = lo
        X, Y, Z = hi
        for q in [((x,y,z),(X,y,z),(X,Y,z),(x,Y,z)),
                  ((x,y,Z),(x,Y,Z),(X,Y,Z),(X,y,Z)),
                  ((x,y,z),(x,Y,z),(x,Y,Z),(x,y,Z)),
                  ((X,y,z),(X,y,Z),(X,Y,Z),(X,Y,z)),
                  ((x,Y,z),(X,Y,z),(X,Y,Z),(x,Y,Z)),
                  ((x,y,z),(x,y,Z),(X,y,Z),(X,y,z))]:
            self.quad(*q)

    def depth(self, cam):
        if not self.v:
            return np.full((H, W), -np.inf)
        q = (np.asarray(self.v)-cam.eye) @ cam.axes.T
        faces = np.asarray(self.f, np.int32)
        assert np.all(q[:, 2] > .13), 'Orbit must keep all car geometry beyond near plane'
        d = q[:, 2]
        v = np.ascontiguousarray(np.stack([W/2+cam.focal*q[:,0]/d,
                                           H/2-cam.focal*q[:,1]/d, 1/d], axis=1))
        out = np.empty((H, W))
        owner = np.empty((H, W), np.int32)
        RASTER(v.ctypes.data, faces.ctypes.data, len(v), len(faces), W, H,
               out.ctypes.data, owner.ctypes.data)
        return out


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def dependencies():
    """Parameter adapter only; original chapel sources remain unchanged."""
    global RASTER
    BUILD.mkdir(parents=True, exist_ok=True)
    for source, library in [('raster.cpp', 'raster.so'), ('joint_fit.cpp', 'libjointfit.so')]:
        output = BUILD / library
        if not output.exists() or output.stat().st_mtime < (CHAPEL/source).stat().st_mtime:
            subprocess.run(['c++', '-O3', '-std=c++17', '-shared', '-fPIC',
                            str(CHAPEL/source), '-o', str(output)], check=True)
    lib = ctypes.CDLL(str(BUILD/'raster.so'))
    RASTER = lib.raster
    RASTER.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    # The picture/motion adapters consume these declared projection constants;
    # no old chapel geometry or frame producer is executed.
    geometry = types.ModuleType('geometry')
    geometry.__dict__.update(dict(W=W, H=H, PITCH=PITCH, FOCAL=FOCAL, PAD=PAD,
        ROOT=CHAPEL, Camera=Camera, Solids=Solids, unit=unit, smooth=smooth,
        mix=mix, np=np, math=math))
    sys.modules['geometry'] = geometry
    picture = load_module('car_target_picture_adapter', CHAPEL/'picture.py')
    fitting = load_module('car_target_page_fitter', CHAPEL/'joint_fit.py')
    fitting.ROOT = BUILD  # native library location; CAT/FONT retain original pinned inputs
    motion = load_module('car_target_motion_adapter', CHAPEL/'motion.py')
    return picture, fitting, motion


SPECS = {
    'bluebird510': dict(title='Nissan Bluebird 510 — boxy compact', refs=[215,216,217],
        length=4.10, width=1.60, belt=.90, roof=1.46, wheel=.335,
        axles=[-1.29,1.20], cabin=[-.70,-.20,.62,1.16], style='boxy',
        transfer='Four round front lamps, upright greenhouse, square deck, framed rear lamps, thin separate bumpers;215front/216front-quarter/217rear-quarter.',
        inference='Opposite side mirrors the source construction; exact dimensions, unseen side trim, underbody and suspension are authored, not measured reference facts.'),
    'cresta_gx71': dict(title='Toyota Cresta GX71 — long three-box sedan', refs=[94,95],
        length=4.65, width=1.73, belt=.91, roof=1.43, wheel=.34,
        axles=[-1.42,1.33], cabin=[-.78,-.20,.74,1.32], style='sedan',
        transfer='Long flat bonnet, low square three-box body, thin rectangular headlamp/grille band, long side crease, four-door glass rhythm and wide rear lamp/plate frame;94front-quarter/95rear-quarter.',
        inference='Source front/rear pairs anchor the construction; pure side view, opposite flank, precise physical dimensions and unseen running gear are authored inference.'),
    'porsche911': dict(title='Porsche 911 Turbo — rounded coupe', refs=[327,328,329],
        length=4.25, width=1.78, belt=.82, roof=1.34, wheel=.355,
        axles=[-1.29,1.19], cabin=[-.63,-.12,.47,1.24], style='porsche',
        transfer='Raised round headlamps, sloping bonnet, curved short roof, rear-quarter glass, broad haunches and raised rear wing;327/328front-quarter,329overhead. Do not merge996/331.',
        inference='Porsche is the explicitly named German exception, not a Japanese make.329is overhead, not rear. Rear fascia, unseen opposite side and dimensions are authored inference;327general911 and328/329Turbo are family evidence, not verified identical trim.'),
    'ae86': dict(title='Toyota Corolla Levin AE86 — Japanese coupe', refs=[86,87,88,89],
        length=4.22, width=1.64, belt=.88, roof=1.36, wheel=.33,
        axles=[-1.30,1.19], cabin=[-.67,-.15,.53,1.23], style='coupe',
        transfer='Angular low bonnet, rectangular fixed headlamps, straight beltline, compact two-door glass and squared rear;86front/87-88alternate views/89rear.',
        inference='Exact trim, opposite-flank hardware, dimensions and underside are authored inference; Levin is not silently replaced by a pop-up-headlamp Trueno.'),
    'kei_sambar': dict(title='Sambar-informed kei flatbed', refs=[442,443,450],
        length=3.30,width=1.40,belt=.95,roof=1.74,wheel=.255,
        axles=[-1.10,1.12],style='kei',orbit=5.5,
        transfer='442/443 opposite side constructions and450front-quarter: tall cab-over screen, compact round lamps in rectangular surrounds, low open bed rails, pressed ribs, hinges and small steel wheels. Retain white bed/pane voids and bounded chassis hatching.',
        inference='Original Sambar-informed study, not an exact generation/trim reconstruction. Open bed interior, rear gate, opposite unseen fittings and dimensions are authored; no invented badges.'),
    'teardrop1930': dict(title='1930s-inspired Wraith / teardrop coachwork concept', refs=[276,299],
        length=5.24,width=1.92,belt=.94,roof=1.65,wheel=.405,
        axles=[-1.52,1.40],style='teardrop',orbit=7.65,
        transfer='2761939Cadillac: long bonnet, upright radiator, separate round lamps, swept separate fenders and running boards.299Horch: bounded radiator/bonnet slots and darker repetitive tire/chassis texture. Transfer AA mark hierarchy, not copied pixels or exact coachwork.',
        inference='Original 1930s-inspired teardrop concept requested by operator; NOT a documented Rolls-Royce Wraith replica. Fastback roof, boat-tail taper, split screen, wire wheels and exact geometry are authored interpretation.333Rolls-Royce is not used as a1930s reference.'),
}


def rotate_axle(points, center, angle):
    """Rigid wheel-local rotation, from immutable construction (no drift)."""
    q=np.asarray(points,float)-center
    result=q.copy();c,s=math.cos(angle),math.sin(angle)
    result[...,1]=q[...,1]*c-q[...,2]*s
    result[...,2]=q[...,1]*s+q[...,2]*c
    return result+center


def wheels(p, spec, pattern='steel'):
    r=spec['wheel'];half=spec['width']/2;refs=spec['refs'];p.wheel_parts=[]
    for side in (-1,1):
        for j,z in enumerate(spec['axles']):
            prefix=f'wheel.{side}.{j}.';outer=side*(half+.07);inner=side*(half-.15)
            center=np.array([outer,r,z]);first=len(p.solids.v)
            ring=np.linspace(0,2*math.pi,65)
            def arc(fid,rr,angles,role):
                p.line(prefix+fid,[[outer+side*.016,r+rr*math.cos(a),z+rr*math.sin(a)] for a in angles],refs,role)
            for aa,bb in zip(ring,ring[1:]):
                for x in (outer,inner):
                    p.solids.quad([x,r,z],[x,r+r*math.cos(aa),z+r*math.sin(aa)],
                        [x,r+r*math.cos(bb),z+r*math.sin(bb)],[x,r,z])
                p.solids.quad([inner,r+r*math.cos(aa),z+r*math.sin(aa)],
                    [outer,r+r*math.cos(aa),z+r*math.sin(aa)],
                    [outer,r+r*math.cos(bb),z+r*math.sin(bb)],
                    [inner,r+r*math.cos(bb),z+r*math.sin(bb)])
            for rr,name in [(r,'tire'),(r*.70,'rim'),(r*.23,'hub')]:
                arc(name,rr,ring,'Continuous tire / bright rim / small hub: hierarchy, not eight generic spokes')
            count={'petal':5,'wire':12,'steel':4,'hubcap':6}[pattern]
            for k in range(count):
                a=2*math.pi*k/count+.13
                if pattern=='wire':
                    for twist in (-.14,.14):
                        p.line(prefix+f'wire{k}.{twist}',[[outer+side*.018,r+r*.23*math.cos(a),z+r*.23*math.sin(a)],
                            [outer+side*.018,r+r*.66*math.cos(a+twist),z+r*.66*math.sin(a+twist)]],refs,'Cross-laced wire wheel; physically rotating attached stroke')
                elif pattern=='petal':
                    pts=[]
                    for rr,da in [(.25,-.13),(.61,-.13),(.66,0),(.61,.13),(.25,.13)]:
                        pts.append([outer+side*.018,r+r*rr*math.cos(a+da),z+r*rr*math.sin(a+da)])
                    p.line(prefix+f'petal{k}',pts,refs,'Five shaped wheel petals instead of interchangeable radial spokes')
                else:
                    arc(f'vent{k}',r*(.51 if pattern=='steel' else .58),np.linspace(a-.12,a+.12,7),
                        'Short rotating steel vent / hubcap slot; large central blank remains')
            # Off-axis valve makes rotation observable even on symmetric rims.
            p.line(prefix+'valve',[[outer+side*.021,r+r*.65,z],[outer+side*.021,r+r*.77,z+.015]],refs,'Asymmetric rotating valve marker')
            for band,scale in enumerate((.81,.91)):
                for k in range(35):
                    a=k*2*math.pi/35+.07*band
                    p.tone(prefix+f'sidewall{band}.{k}',[outer+side*.022,r+r*scale*math.cos(a),z+r*scale*math.sin(a)],
                           2+(k%3==0),refs)
            for k in range(24):
                a=2*math.pi*k/24
                p.line(prefix+f'tread{k}',[[inner,r+r*math.cos(a),z+r*math.sin(a)],
                    [outer,r+r*math.cos(a+.035),z+r*math.sin(a+.035)]],refs,'Tread notches turn with the cylinder, never screen-fixed noise')
            p.wheel_parts.append(dict(prefix=prefix,center=center,vertices=(first,len(p.solids.v))))
            p.line(f'contact.{side}.{j}',[[outer-side*.08,.006,z-.15],[outer+side*.02,.006,z+.15]],refs,'Fixed short ground contact; not rotated with wheel')


def prepare_motion(p):
    p.base_vertices=np.asarray(p.solids.v,float).copy()
    p.base_paths={q['id']:q['p'].copy() for q in p.paths if q['id'].startswith('wheel.')}
    p.base_tones={q['id']:q['p'].copy() for q in p.tones if q['id'].startswith('wheel.')}


def set_wheels(p,t):
    angle=2*math.pi*WHEEL_TURNS*t
    vertices=p.base_vertices.copy()
    for part in p.wheel_parts:
        prefix,c=part['prefix'],part['center'];lo,hi=part['vertices']
        vertices[lo:hi]=rotate_axle(p.base_vertices[lo:hi],c,angle)
        for path in p.paths:
            if path['id'].startswith(prefix):path['p']=rotate_axle(p.base_paths[path['id']],c,angle)
        for tone in p.tones:
            if tone['id'].startswith(prefix):tone['p']=rotate_axle(p.base_tones[tone['id']],c,angle)
    p.solids.v=vertices.tolist()


def move_wheel_prior(old,p,delta):
    result=copy.deepcopy(old)
    for rec in result:
        for contribution in rec['parts']:
            for part in p.wheel_parts:
                if contribution['feature'].startswith(part['prefix']):
                    contribution['world']=rotate_axle(contribution['world'],part['center'],delta).tolist()
                    break
    return result


def compose_car(p,t):
    """Admit wheel detail by available projected span, before glyph selection.

    A narrow wheel needs its boundary and a rotating valve, not all the wires
    of a broadside wheel compressed into one dense kanji-shaped ink mass.
    """
    camera=p.camera(t);widths={}
    for part in p.wheel_parts:
        ring=next(q for q in p.paths if q['id']==part['prefix']+'tire')
        widths[part['prefix']]=np.ptp(camera.project(ring['p'])[:,0])
    original=p.paths
    dynamic=roof_silhouettes(p,camera) if hasattr(p,'roof_surface') else []
    selected=[]
    for q in original+dynamic:
        fid=q['id'];admit=True
        for prefix,width in widths.items():
            if not fid.startswith(prefix):continue
            suffix=fid[len(prefix):]
            if suffix.startswith(('wire','petal','vent')) and width<18:admit=False
            if suffix.startswith('wire'):
                number=int(suffix.split('.')[0][4:])
                if number%2:admit=False  # six legible crossed pairs at this shot size
            if suffix.startswith('tread') and width<12:admit=False
            break
        if admit:selected.append(q)
    try:
        p.paths=selected;result=p.compose(t)
    finally:p.paths=original
    result['admission']=dict(selected_paths=len(selected),constructed_paths=len(original)+len(dynamic),
                             projected_wheel_widths=widths)
    return result


def finish_plan(p,spec):
    p.spec=spec;orbit=spec.get('orbit',6.4)
    add_plates(p,spec)
    p.camera=lambda t: Camera([orbit*math.sin(2*math.pi*t-.62),2.10,
                               -orbit*math.cos(2*math.pi*t-.62)],[0,.77,0])
    prepare_motion(p)
    return p


def add_plates(p,spec):
    rng=random.Random(int(hashlib.sha256((p.name+'fictional-plate-v1').encode()).hexdigest(),16))
    serial=f'{rng.randrange(10,100):02}-{rng.randrange(10,100):02}'
    supported={g['char'] for g in json.loads((CHAPEL/'catalog.json').read_text())['glyphs']}
    kana=rng.choice([ch for ch in ['ｱ','ｻ','ﾅ','ﾈ','ﾎ','ﾏ'] if ch in supported])
    p.plates=[]
    for name,sign in [('front',-1),('rear',1)]:
        fid=name+'.plate';p.paths=[q for q in p.paths if q['id']!=fid]
        z=sign*(spec['length']/2+(.115 if sign<0 else .065))
        corners=[[-.29,.365,z],[.29,.365,z],[.29,.645,z],[-.29,.645,z]]
        p.solids.quad(*corners);p.line(fid,corners+[corners[0]],spec['refs'],'Mounted white fictional Japanese plate with native kana and serial')
        p.plates.append(dict(id=fid,center=[0,.505,z],normal=[0,0,sign],corners=corners,
                             registration=f'品川 330 {kana} {serial}',visible_text=kana+serial))


def add_911_greenhouse(p,spec,top):
    refs=spec['refs']
    zz=[-.67,-.43,-.12,.18,.43,.70,1.00,1.27]
    heights=[.84,1.09,1.29,1.365,1.34,1.21,.99,.83]
    crown=PchipInterpolator(zz,heights)
    widths=PchipInterpolator(zz,[.65,.67,.67,.66,.65,.65,.69,.73])
    def surface(z,theta,lift=0):
        base=top(z)-.012
        return np.array([(float(widths(z))+lift)*math.sin(theta),
            base+(float(crown(z))-base+lift)*math.cos(theta),z])
    zs=np.linspace(zz[0],zz[-1],85);angles=np.linspace(0,2*math.pi,97)
    for z0,z1 in zip(zs,zs[1:]):
        for a0,a1 in zip(angles,angles[1:]):
            p.solids.quad(surface(z0,a0),surface(z0,a1),surface(z1,a1),surface(z1,a0))
    for z in (zs[0],zs[-1]):
        for a,b in zip(angles,angles[1:]):
            p.solids.quad([0,top(z)-.012,z],surface(z,a),surface(z,b),[0,top(z)-.012,z])
    def glass(fid,zlo,zhi,alo,ahi):
        # Borders AND protected paper follow this actual curved shell.
        contour=[surface(zlo,a,.012) for a in np.linspace(alo,ahi,21)]
        contour += [surface(z,ahi,.012) for z in np.linspace(zlo,zhi,21)[1:]]
        contour += [surface(zhi,a,.012) for a in np.linspace(ahi,alo,21)[1:]]
        contour += [surface(z,alo,.012) for z in np.linspace(zhi,zlo,21)[1:]]
        p.line(fid,contour+[contour[0]],refs,'Curved911glazing frame attached to the continuous arched greenhouse')
        for j,(z0,z1) in enumerate(zip(np.linspace(zlo,zhi,7),np.linspace(zlo,zhi,7)[1:])):
            for k,(a0,a1) in enumerate(zip(np.linspace(alo,ahi,9),np.linspace(alo,ahi,9)[1:])):
                p.opening(fid+f'.void{j}.{k}',[surface(z0,a0,.008),surface(z0,a1,.008),surface(z1,a1,.008),surface(z1,a0,.008)],refs)
    glass('911.windscreen',-.53,-.10,-1.08,1.08)
    glass('911.backlight',.68,1.13,-.91,.91)
    for side in (-1,1):
        glass(f'911.glass.{side}.door',-.25,.31,side*.91,side*1.39)
        glass(f'911.glass.{side}.quarter',.40,.91,side*.96,side*1.39)
        p.line(f'911.{side}.gutter',[surface(z,side*.83,.015) for z in np.linspace(-.15,.73,55)],refs,
               'Continuous curved rain gutter above side glass, subordinate to migrating roof silhouette')
        p.line(f'door.{side}.seam',[[side*.85,.80,.36],[side*.85,.43,.36],
            [side*.85,.43,-.46]],refs,'Long911door perimeter below side glass')
        p.line(f'door.{side}.handle',[[side*.86,.775,.12],[side*.86,.775,.28]],refs,'Small911door handle')
        p.line(f'911.{side}.A-pillar',[surface(-.13,a,.015) for a in np.linspace(side*.88,side*1.42,17)],refs,'Attached A pillar joins roof/gutter to belt')
        for k,z in enumerate(np.arange(-.06,.62,.085)):
            p.tone(f'911.{side}.roof-turn{k}',surface(z,side*.64,.018),1,refs)
        mx=side*.91
        p.solids.box([min(mx,mx+side*.13),.91,-.57],[max(mx,mx+side*.13),1.03,-.44])
        p.line(f'911.{side}.mirror',[[mx,.91,-.57],[mx+side*.13,.91,-.57],
            [mx+side*.13,1.03,-.44],[mx,1.03,-.44],[mx,.91,-.57]],refs,'Attached911mirror housing')
    p.roof_surface=surface;p.roof_zs=zs


def roof_silhouettes(p,cam):
    curves=[[],[]];aa=np.linspace(0,2*math.pi,97)
    for z in p.roof_zs:
        points=np.array([p.roof_surface(z,a,.012) for a in aa])
        da=np.array([p.roof_surface(z,a+.0001)-p.roof_surface(z,a-.0001) for a in aa])
        dz=np.array([p.roof_surface(z+.0001,a)-p.roof_surface(z-.0001,a) for a in aa])
        normals=np.cross(da,dz);facing=np.sum(normals*(cam.eye-points),axis=1)
        crossings=np.flatnonzero(facing[:-1]*facing[1:]<0)
        if len(crossings)!=2:continue
        pp=[]
        for ix in crossings:
            a=aa[ix]+(aa[ix+1]-aa[ix])*facing[ix]/(facing[ix]-facing[ix+1])
            pp.append(p.roof_surface(z,a,.018))
        pp.sort(key=lambda point:cam.project(point)[0])
        for i in (0,1):curves[i].append(pp[i])
    return [dict(id=f'911.roof.silhouette{i}',p=np.asarray(points),refs=p.spec['refs'],
        role='True migrating silhouette of continuous rounded911roof, not a sparse polygon edge')
        for i,points in enumerate(curves) if len(points)>1]


def build_car(picture, key):
    spec = SPECS[key]
    if spec['style']=='kei':return build_kei(picture,spec,key)
    if spec['style']=='teardrop':return build_teardrop(picture,spec,key)
    p = picture.Plan(key)
    refs, L, half = spec['refs'], spec['length'], spec['width']/2
    belt, roof, radius = spec['belt'], spec['roof'], spec['wheel']
    front, rear = -L/2, L/2
    axles = spec['axles']
    style = spec['style']
    a, b, c, d = spec['cabin']
    coupe = style in ('porsche', 'coupe')

    def line(fid, points, role):
        p.line(fid, points, refs, role)

    def polygon(fid, points, role, opening=False):
        line(fid, list(points)+[points[0]], role)
        if opening:
            p.opening(fid+'.void', points, refs)

    def circle(fid, center, r, axis, role, count=49):
        points=[]
        for angle in np.linspace(0,2*math.pi,count):
            u,v = r*math.cos(angle),r*math.sin(angle)
            x,y,z=center
            points.append([x,y+u,z+v] if axis=='x' else [x+u,y+v,z])
        line(fid, points, role)

    def width(z):
        if style=='porsche':
            return half*(.88+.12*max(math.exp(-((z-axle)/.62)**2) for axle in axles))
        return half*(.96+.04*math.sin(math.pi*(z-front)/L))

    def top(z):
        if style=='porsche':
            return float(np.interp(z,[front,front+.35,a,d,rear], [.59,.78,belt,belt,.68]))
        return float(np.interp(z,[front,front+.25,a,d,rear],[belt-.13,belt-.02,belt,belt,belt-.07]))

    def bottom(z):
        low=.31
        for axle in axles:
            distance=abs(z-axle)
            if distance < radius+.075:
                low=max(low,radius+math.sqrt((radius+.075)**2-distance**2))
        return min(low,top(z)-.04)

    def surface_y(z, u):
        # The911 has raised front wings around a lower bonnet. A flat slab
        # with round lamps pasted on it would remain a generic coupe.
        ridge = (.16*math.exp(-((z-axles[0])/.56)**2)
                 + .055*math.exp(-((z-axles[1])/.60)**2)) if style=='porsche' else 0
        if style=='porsche':
            return top(z)+ridge*math.exp(-((abs(u)-.78)/.24)**2)-.04*abs(u)**8
        return top(z)

    def body_point(z,u,low=False):
        rounding=(.21*abs(u)**3*(math.exp(-((z-front)/.30)**2)
                     -math.exp(-((z-rear)/.28)**2))) if style=='porsche' else 0
        return [u*width(z),bottom(z) if low else surface_y(z,u),z+rounding]

    zs=sorted(set(np.linspace(front,rear,115).tolist()+[a,d]+[
        axle+delta for axle in axles for delta in [-radius-.076,0,radius+.076]
        if front<axle+delta<rear]))
    for z0,z1 in zip(zs,zs[1:]):
        w0,w1=width(z0),width(z1)
        y0,y1=surface_y(z0,1),surface_y(z1,1)
        cross=np.linspace(-1,1,17) if style=='porsche' else [-1,-.48,.48,1]
        for u0,u1 in zip(cross,cross[1:]):
            p.solids.quad(body_point(z0,u0),body_point(z0,u1),body_point(z1,u1),body_point(z1,u0))
        for side in (-1,1):
            p.solids.quad(body_point(z0,side,True),body_point(z1,side,True),
                          body_point(z1,side),body_point(z0,side))
    for z in (front,rear):
        w=width(z)
        for u0,u1 in zip(cross,cross[1:]):
            q0,q1=body_point(z,u0),body_point(z,u1)
            p.solids.quad([q0[0],.31,q0[2]],[q1[0],.31,q1[2]],q1,q0)
    for side in (-1,1):
        line(f'body.{side}.shoulder',[np.asarray(body_point(z,side))+[0,.008,0] for z in zs],
             'Selected hood/shoulder/deck turn; not triangle edges')
        line(f'body.{side}.arch-and-sill',[np.asarray(body_point(z,side,True))+[side*.006,0,0] for z in zs],
             'Wheel-arch clearances joined to sill')
        if style!='porsche':
            line(f'body.{side}.belt-crease',[[side*(width(z)+.009),min(top(z)-.025,.77),z]
                 for z in np.linspace(front+.15,rear-.12,40)],'Long identity-bearing side crease')
    for z,name in [(front,'front'),(rear,'rear')]:
        line('body.'+name+'.rim',[body_point(z,u) for u in cross],'Curved nose/deck turn, not square corners')

    # Closed opaque greenhouse: glass is protected empty paper, not see-through
    # far-side linework. Side pane borders articulate pillars and their gaps.
    greenhouse_start=(len(p.solids.v),len(p.solids.f),len(p.paths),len(p.voids))
    cabin_width=half*(.76 if coupe else .78)
    stations=[(a,width(a)*.92,top(a)),(b,cabin_width,roof),
              (c,cabin_width,roof),(d,width(d)*.92,top(d))]
    if style=='porsche':
        stations=[(a,width(a)*.88,top(a)),(b,cabin_width*.98,roof-.07),
                  ((b+c)/2,cabin_width,roof),(c,cabin_width*.98,roof-.025),
                  (c+(d-c)*.45,cabin_width*.94,roof-.16),
                  (d,width(d)*.88,top(d))]
    for (z0,w0,y0),(z1,w1,y1) in zip(stations,stations[1:]):
        p.solids.quad([-w0,y0,z0],[w0,y0,z0],[w1,y1,z1],[-w1,y1,z1])
        for side in (-1,1):
            p.solids.quad([side*width(z0)*.92,top(z0),z0],
                          [side*width(z1)*.92,top(z1),z1],
                          [side*w1,y1,z1],[side*w0,y0,z0])
    for side in (-1,1):
        line(f'cabin.{side}.roofline',[[side*w,y+.008,z] for z,w,y in stations],
             'Model-specific roof/A-pillar/C-pillar silhouette')
    for z,w,y in (stations[0],stations[1],stations[-2],stations[-1]):
        line(f'cabin.cross.{z:.3f}',[[-w,y+.008,z],[w,y+.008,z]],'Windscreen or roof transverse boundary')
    p.opening('windscreen',[[-stations[0][1],stations[0][2]+.006,a],
        [stations[0][1],stations[0][2]+.006,a],
        [stations[1][1],stations[1][2]+.006,b],[-stations[1][1],stations[1][2]+.006,b]],refs)
    p.opening('backlight',[[-stations[-2][1],stations[-2][2]+.006,stations[-2][0]],
        [stations[-2][1],stations[-2][2]+.006,stations[-2][0]],
        [stations[-1][1],stations[-1][2]+.006,d],[-stations[-1][1],stations[-1][2]+.006,d]],refs)
    divide=b+(c-b)*(.72 if coupe else .50)
    for side in (-1,1):
        # Pane plane interpolates the actual opaque greenhouse side.
        def pane(z,y):
            upper=float(np.interp(z,[s[0] for s in stations],[s[2] for s in stations]))
            uw=float(np.interp(z,[s[0] for s in stations],[s[1] for s in stations]))
            t=np.clip((y-top(z))/max(.001,upper-top(z)),0,1)
            x=(width(z)*.92)*(1-t)+uw*t
            return [side*(x+.009),y,z]
        lower=top(a)+.075
        pane1=[pane(a+.12,lower),pane(b+.07,roof-.075),
               pane(divide-.035,roof-.075),pane(divide-.035,lower)]
        pane2=[pane(divide+.055,lower),pane(divide+.055,roof-.075),
               pane(c-.05,roof-.075),pane(d-.16,lower)]
        for name,points in [('front',pane1),('rear-quarter' if coupe else 'rear',pane2)]:
            polygon(f'glass.{side}.{name}',points,'Glazing boundary around protected empty pane',True)
        seam_zs=[a+.035,divide,d-.06] if not coupe else [a+.035,divide+.06]
        for j,z in enumerate(seam_zs):
            line(f'door.{side}.seam{j}',[[side*(width(z)+.012),top(z)-.015,z],
                 [side*(width(z)+.012),.43,z]],'Door-to-panel seam attached below glazing')
        for j,z in enumerate([divide-.20] if coupe else [divide-.19,d-.27]):
            line(f'door.{side}.handle{j}',[[side*(width(z)+.02),.79,z-.09],
                 [side*(width(z)+.02),.79,z+.055]],'Small subordinate door handle')
        # Mirror is a separate attached volume; unseen opposite one is declared
        # symmetric authored inference rather than archive-observed hardware.
        mx=side*(half+.065); mz=a+.10
        p.solids.box([min(mx,mx+side*.15),belt+.035,mz-.085],
                     [max(mx,mx+side*.15),belt+.17,mz+.075])
        polygon(f'mirror.{side}',[[mx,belt+.04,mz-.085],
            [mx+side*.15,belt+.04,mz-.085],[mx+side*.15,belt+.17,mz+.075],
            [mx,belt+.17,mz+.075]],'Attached mirror outline')

    if style=='porsche':
        # Replace the ENTIRE generic greenhouse, including its old occluders
        # and generic protected panes; retaining either would cut roof pieces.
        nv,nf,nl,no=greenhouse_start
        p.solids.v=p.solids.v[:nv];p.solids.f=p.solids.f[:nf]
        p.paths=p.paths[:nl];p.voids=p.voids[:no]
        add_911_greenhouse(p,spec,top)

    wheels(p,spec,'petal' if style=='porsche' else 'hubcap' if style=='sedan' else 'steel')

    # Authored form-tone, not a body-wide luminance ramp. Density is nested
    # towards the underside; the broad door, bonnet and pane centers stay white.
    for side in (-1,1):
        for band,offset in enumerate((.085,.20,.31)):
            for k,z in enumerate(np.arange(front+.16,rear-.12,.065)):
                y=surface_y(z,1)-offset
                if y<bottom(z)+.05 or (band==2 and k%4):continue
                if band==1 and k%3==1:continue
                p.tone(f'form.{side}.shoulder{band}.{k}',[side*(width(z)+.017),y,z],
                       2 if band<2 else 1,refs)
        for k,z in enumerate(np.arange(front+.18,rear-.15,.075)):
            if bottom(z)>.4:continue
            p.tone(f'form.{side}.sill{k}',[side*(width(z)+.018),.365,z],3,refs)
        # Two discontinuous rolls of bonnet tone, explicitly bounded to the
        # outer wings. No cross-hatching through the empty bonnet center.
        for k,z in enumerate(np.arange(front+.25,a-.08,.07)):
            for band,u in enumerate((.64,.82)):
                if band==0 and k%3:continue
                p.tone(f'form.{side}.bonnet{band}.{k}',
                    [side*width(z)*u,surface_y(z,u)+.018,z],1+band,refs)
        if style!='porsche':
            for k,z in enumerate(np.arange(b+.08,c-.06,.09)):
                p.tone(f'form.{side}.roofroll{k}',[side*cabin_width*.88,roof+.016,z],1,refs)

    # Model-specific fascia grammar is selected construction, never a glyph
    # alphabet: round/rectangular features will use the whole native font bank.
    fz=front-.012; rz=rear+.012
    def rect(fid,x0,y0,x1,y1,z,role):
        polygon(fid,[[x0,y0,z],[x1,y0,z],[x1,y1,z],[x0,y1,z]],role)
    if style=='boxy':
        for i,x in enumerate([-.60,-.34,.34,.60]):
            circle(f'front.lamp{i}',[x,.715,fz],.115,'z','Four separate round510headlamps')
        for j,y in enumerate([.65,.72,.79]):
            line(f'front.grille{j}',[[-.21,y,fz],[.21,y,fz]],'Bounded horizontal grille slats')
        rect('rear.lamp-left',-.70,.61,-.27,.80,rz,'Framed rear lamp left')
        rect('rear.lamp-right',.27,.61,.70,.80,rz,'Framed rear lamp right')
    elif style=='porsche':
        for side in (-1,1):
            points=[]
            back=[]
            for angle in np.linspace(0,2*math.pi,49):
                points.append([side*.64+.145*math.cos(angle),.90+.14*math.sin(angle),front+.10+.10*math.sin(angle)])
                x=side*.64+.145*math.cos(angle)
                z=front+.58
                back.append([x,max(.87+.125*math.sin(angle),
                    surface_y(z,x/width(z))-.008),z])
            # A lens is a mounted surface, not a freestanding circle. The
            # opaque tapered cowl meets the raised wing and hides the lens
            # from rear views; source-derived lamp rings never face camera.
            for j in range(len(points)-1):
                p.solids.quad(points[j],points[j+1],back[j+1],back[j])
                p.solids.quad([side*.64,.90,front+.10],points[j],
                              points[j+1],[side*.64,.90,front+.10])
                p.solids.quad([side*.64,.89,front+.58],back[j],
                              back[j+1],[side*.64,.89,front+.58])
            line(f'front.{side}.raised-round-lamp',points,'Raised round headlamp on shaped front wing')
            line(f'front.{side}.lamp-cowl-crown',
                [points[12],back[12],[side*.64,surface_y(front+.75,side*.64/width(front+.75))+.009,front+.75]],
                'Lamp cowl joins the raised front wing; no detached rear-view ellipse')
            line(f'bonnet.{side}.seam',[[side*.46,.77,front+.38],
                [side*.39,.81,front+.9],[side*.49,belt+.012,a]],'Sloping bonnet seam between raised wings')
        # Whaletail: rear wing spans above the deck, not a new roof.
        p.solids.box([-half*.96,.88,rear-.54],[half*.96,.95,rear-.08])
        for y in (.89,.955):
            polygon(f'wing.{y}',[[-half*.96,y,rear-.54],[half*.96,y,rear-.54],
                [half*.96,y,rear-.08],[-half*.96,y,rear-.08]],'Attached raised rear spoiler lip')
        for x in (-.43,.43):
            line(f'wing.support{x}',[[x,.75,rear-.30],[x,.89,rear-.30]],'Rear wing support')
        for j,z in enumerate(np.linspace(d+.05,rear-.58,7)):
            line(f'engine-lid.vent{j}',[[-.48,top(z)+.015,z],[.48,top(z)+.015,z]],'Bounded rear engine-lid vent strip')
        rect('rear.inferred-lamp-band',-.74,.59,.74,.68,rz,'Explicitly inferred911rear lamp band')
    else:
        for side in (-1,1):
            x0,x1=sorted([side*.30,side*.76])
            rect(f'front.lamp{side}',x0,.59,x1,.77,fz,'Thin rectangular fixed headlamp group')
        rect('front.grille',-.27,.59,.27,.77,fz,'Separate central rectangular grille')
        for j,y in enumerate([.64,.70]):
            line(f'front.grille.slat{j}',[[-.25,y,fz],[.25,y,fz]],'Bounded grille line')
        for side in (-1,1):
            x0,x1=sorted([side*.28,side*.77])
            rect(f'rear.lamp{side}',x0,.61,x1,.77,rz,'Long rear lamp frame')
    for z,name in [(fz-.01,'front'),(rz+.01,'rear')]:
        if style=='porsche':
            sweep=.20 if name=='front' else -.13
            us=np.linspace(-1,1,41)
            points=[[u*half*.98,.40,z+sweep*abs(u)**4] for u in us]
            points += [[u*half*.98,.48,z+sweep*abs(u)**4] for u in us[::-1]]
            polygon(name+'.bumper',points,'Thin911bumper wraps around rounded nose/tail corners')
        else:
            polygon(name+'.bumper',[[-half*.98,.40,z],[half*.98,.40,z],
                [half*.98,.48,z],[-half*.98,.48,z]],'Separate thin bumper band')
        rect(name+'.plate',-.19,.42,.19,.59,z+(-.006 if name=='front' else .006),
             'Blank plate recess, no invented lettering')
    return finish_plan(p,spec)


class Construction:
    """Small selected-feature authoring vocabulary, not an all-edges renderer."""
    def __init__(self,p,refs):self.p=p;self.refs=refs
    def line(self,fid,points,role):self.p.line(fid,points,self.refs,role)
    def poly(self,fid,points,role,void=False):
        self.line(fid,list(points)+[points[0]],role)
        if void:self.p.opening(fid+'.void',points,self.refs)
    def panel(self,fid,points,role,void=False):
        self.p.solids.quad(*points);self.poly(fid,points,role,void)
    def rect(self,fid,x0,y0,x1,y1,z,role,void=False):
        self.poly(fid,[[x0,y0,z],[x1,y0,z],[x1,y1,z],[x0,y1,z]],role,void)
    def tone(self,fid,point,level):self.p.tone(fid,point,level,self.refs)
    def lamp(self,fid,x,y,z,r,back):
        aa=np.linspace(0,2*math.pi,49)
        ring=[[x+r*math.cos(a),y+r*math.sin(a),z] for a in aa]
        rear=[[x+r*.85*math.cos(a),y+r*.85*math.sin(a),back] for a in aa]
        for a,b,c,d in zip(ring,ring[1:],rear,rear[1:]):
            self.p.solids.quad(a,b,d,c)
            self.p.solids.quad([x,y,z],a,b,[x,y,z])
            self.p.solids.quad([x,y,back],c,d,[x,y,back])
        self.line(fid+'.lens',ring,'Round lens attached to an opaque lamp housing')
        self.line(fid+'.housing',[ring[12],rear[12]],'Crown joins lens to mounting volume')


def build_kei(picture,spec,key):
    p=picture.Plan(key);a=Construction(p,spec['refs']);half=.70;front=-1.65;rear=1.65
    # Cabin face slopes into a shallow rounded roof; no sedan bonnet.
    sections=[(front,.67,.87),(front+.13,.65,1.10),(-1.30,.62,1.69),
              (-1.20,.62,1.74),(-.43,.64,1.74),(-.38,.68,.94)]
    p.solids.box([-.67,.29,front],[.67,.86,-.38])
    for (z0,w0,y0),(z1,w1,y1) in zip(sections,sections[1:]):
        p.solids.quad([-w0,y0,z0],[w0,y0,z0],[w1,y1,z1],[-w1,y1,z1])
        for side in (-1,1):
            p.solids.quad([side*.68,.73,z0],[side*.68,.73,z1],[side*w1,y1,z1],[side*w0,y0,z0])
    p.solids.quad([-.68,.73,-.38],[.68,.73,-.38],[.64,1.74,-.43],[-.64,1.74,-.43])
    for side in (-1,1):
        a.line(f'cab.{side}.roof',[[side*w,y+.008,z] for z,w,y in sections],
               'Tall compact cab-over roof and raked A pillar')
        a.line(f'cab.{side}.lower',[[side*.685,.87,front],[side*.685,.87,-.38]],'Cab pressed belt below glass')
        # Side glass on the actual cabin plane, no body fill inside.
        pane=[[side*.657,1.09,-1.45],[side*.635,1.64,-1.24],
              [side*.655,1.64,-.51],[side*.68,1.09,-.51]]
        a.poly(f'cab.{side}.sideglass',pane,'Large single cab side pane',True)
        a.line(f'cab.{side}.door',[[side*.69,1.05,-.47],[side*.69,.34,-.47],
            [side*.69,.34,-.75]],'Rear door seam meets sill, cab-over wheel cutaway')
        a.line(f'cab.{side}.handle',[[side*.70,1.01,-.63],[side*.70,1.01,-.78]],'Subordinate horizontal handle')
        for band,y in enumerate((.78,.64)):
            for k,z in enumerate(np.arange(-1.47,-.49,.055)):
                if band and k%3:continue
                a.tone(f'cab.{side}.pressed-return{band}.{k}',[side*.691,y,z],2 if not band else 1)
        # Front arch is genuinely cut out of the lower cab shell.
        zax=spec['axles'][0];r=.315
        arc=[[side*.69,.255+r*math.cos(t),zax+r*math.sin(t)] for t in np.linspace(-1.8,1.8,43)]
        a.line(f'cab.{side}.arch',arc,'Small front wheel arch under cab door')
        mx=side*.83
        a.line(f'cab.{side}.mirror-stalk',[[side*.67,1.18,-1.40],[mx,1.20,-1.43]],'Attached thin mirror stalk')
        p.solids.box([min(mx,mx+side*.09),1.20,-1.48],[max(mx,mx+side*.09),1.42,-1.39])
        a.poly(f'cab.{side}.mirror',[[mx,1.20,-1.48],[mx+side*.09,1.20,-1.48],
               [mx+side*.09,1.42,-1.39],[mx,1.42,-1.39]],'Narrow tall mirror head')
    # Front glass occupies the same inclined plane as the cab face.
    a.poly('cab.windscreen',[[-.593,1.15,-1.499],[.593,1.15,-1.499],
        [.58,1.63,-1.331],[-.58,1.63,-1.331]],'Broad cab-over windscreen with white center',True)
    for side in (-1,1):
        a.line(f'cab.wiper{side}',[[side*.05,1.115,-1.527],[side*.50,1.115,-1.527]],'Low parked wiper, not windscreen hatching')
    a.rect('cab.backlight',-.46,1.16,.46,1.56,-.375,'Rear cab window above low bed',True)
    for z,w,y in (sections[0],sections[2],sections[3],sections[4]):
        a.line(f'cab.cross{z}',[[-w,y+.01,z],[w,y+.01,z]],'Selected front/roof transverse turn')
    for side in (-1,1):
        a.rect(f'front.lamp-frame{side}',side*.38-.13,.61,side*.38+.13,.83,front-.013,'Square light surround')
        a.lamp(f'front.lamp{side}',side*.38,.72,front-.021,.087,front+.08)
    for j,y in enumerate((.42,.48,.54)):
        a.line(f'front.grille{j}',[[-.62,y,front-.018],[.62,y,front-.018]],'Three bounded slat strokes, no black face fill')
    a.rect('front.plate',-.17,.40,.17,.56,front-.026,'Blank central plate')
    a.rect('front.bumper',-.70,.30,.70,.38,front-.03,'Thin cab-over bumper')
    # Real open flatbed: floor + narrow walls, NOT a box with a top polygon.
    p.solids.box([-.66,.47,-.31],[.66,.52,rear])
    for side in (-1,1):
        x=side*.69
        p.solids.box([min(x,x-side*.035),.51,-.31],[max(x,x-side*.035),.85,rear])
        a.poly(f'bed.{side}.wall',[[x,.51,-.31],[x,.51,rear],[x,.85,rear],[x,.85,-.31]],'Low drop-side flatbed rail')
        for j,y in enumerate((.62,.74)):
            a.line(f'bed.{side}.press{j}',[[x+side*.008,y,-.23],[x+side*.008,y,rear-.08]],'Two pressed sheet-metal ribs')
        for j,z in enumerate((-.20,.42,1.05,1.52)):
            a.line(f'bed.{side}.latch{j}',[[x+side*.018,.84,z],[x+side*.018,.77,z],[x+side*.018,.77,z+.035]],'Hanging latch rhythm below top rail')
        for k,z in enumerate(np.arange(-.20,rear-.08,.07)):
            a.tone(f'bed.{side}.lip-tone{k}',[x+side*.015,.80,z],2)
        a.line(f'chassis.{side}.rail',[[side*.53,.33,-.33],[side*.53,.33,rear-.04]],'Narrow exposed chassis below empty bed')
        for k,z in enumerate(np.arange(-.27,rear,.07)):
            if abs(z-1.12)<.32:continue
            a.line(f'chassis.{side}.hatch{k}',[[side*.535,.34,z],[side*.535,.43,z+.025]],'Bounded chassis hatch; no door fill')
    a.panel('bed.tailgate',[[-.69,.51,rear],[.69,.51,rear],[.69,.85,rear],[-.69,.85,rear]],'Low rear drop gate, bed remains open above')
    for j,y in enumerate((.62,.74)):
        a.line(f'bed.tailpress{j}',[[-.61,y,rear+.01],[.61,y,rear+.01]],'Rear pressed rib')
    a.poly('bed.floor-rim',[[-.64,.528,-.29],[.64,.528,-.29],[.64,.528,rear-.03],[-.64,.528,rear-.03]],'Visible empty bed floor perimeter')
    for side in (-1,1):
        a.rect(f'rear.lamp{side}',side*.49-.11,.29,side*.49+.11,.40,rear+.015,'Small rectangular rear lamp below bed')
    a.rect('rear.plate',-.16,.28,.16,.40,rear+.02,'Small rear plate')
    wheels(p,spec,'steel')
    return finish_plan(p,spec)


def build_teardrop(picture,spec,key):
    p=picture.Plan(key);a=Construction(p,spec['refs']);front=-2.62;rear=2.62
    # Narrow long bonnet, separate fenders, tapering boat-tail body.
    stations=[(front,.41,.92),(-2.40,.48,1.03),(-1.65,.53,1.06),
        (-.66,.66,1.02),(.35,.76,.95),(1.18,.73,.89),(1.97,.56,.76),(rear,.14,.56)]
    zz=np.linspace(front,rear,100)
    width_curve=PchipInterpolator([q[0] for q in stations],[q[1] for q in stations])
    height_curve=PchipInterpolator([q[0] for q in stations],[q[2] for q in stations])
    def w(z):return float(width_curve(z))
    def y(z):return float(height_curve(z))
    def crown(z,u):return y(z)-.10*abs(u)**2
    for z0,z1 in zip(zz,zz[1:]):
        for u0,u1 in zip(np.linspace(-1,1,13),np.linspace(-1,1,13)[1:]):
            p.solids.quad([u0*w(z0),crown(z0,u0),z0],[u1*w(z0),crown(z0,u1),z0],
                [u1*w(z1),crown(z1,u1),z1],[u0*w(z1),crown(z1,u0),z1])
        for side in (-1,1):
            p.solids.quad([side*w(z0),.38,z0],[side*w(z1),.38,z1],
                [side*w(z1),crown(z1,1),z1],[side*w(z0),crown(z0,1),z0])
    for z in (front,rear):
        p.solids.quad([-w(z),.38,z],[w(z),.38,z],[w(z),y(z)-.1,z],[-w(z),y(z)-.1,z])
    for side in (-1,1):
        a.line(f'body.{side}.turn',[[side*w(z),crown(z,1)+.008,z] for z in zz],'Long bonnet shoulder flowing into narrow boat tail')
        a.line(f'body.{side}.sill',[[side*w(z),.39,z] for z in zz],'Body lower boundary above running board')
        # Fenders are curved opaque shells above a real wheel opening.
        for j,axle in enumerate(spec['axles']):
            angles=np.linspace(-1.80,1.80,65);cross=np.linspace(0,1,9)
            def fp(t,u):
                rr=.515+.025*math.sin(math.pi*u)
                sweep=.90 if j==0 else .67
                return [side*(.55+.53*u),.405+rr*math.cos(t)+.055*math.sin(math.pi*u),axle+sweep*math.sin(t)]
            for t0,t1 in zip(angles,angles[1:]):
                for u0,u1 in zip(cross,cross[1:]):p.solids.quad(fp(t0,u0),fp(t0,u1),fp(t1,u1),fp(t1,u0))
            for u,name in ((0,'inner'),(1,'outer'),(.53,'crown')):
                a.line(f'fender.{side}.{j}.{name}',[fp(t,u) for t in angles],
                    'Separate swept fender: bright crown, continuous dark outer lip and white roll')
            for end in (angles[0],angles[-1]):
                a.line(f'fender.{side}.{j}.return{end}',[fp(end,u) for u in cross],'Fender return joins running board')
            for band,u in enumerate((.77,.92)):
                for k,t in enumerate(np.arange(-1.55,1.56,.07)):
                    if band==0 and k%3:continue
                    point=np.array(fp(t,u));point[1]+=.016
                    a.tone(f'fender.{side}.{j}.roll{band}.{k}',point,1+band)
        p.solids.box([min(side*.60,side*1.04),.29,-1.35],[max(side*.60,side*1.04),.35,1.23])
        a.poly(f'board.{side}',[[side*.62,.36,-1.30],[side*1.04,.36,-1.30],
            [side*1.04,.36,1.21],[side*.62,.36,1.21]],'Long separate running board under doors')
        for k,z in enumerate(np.arange(-1.23,1.20,.07)):
            a.line(f'board.{side}.groove{k}',[[side*.76,.366,z],[side*1.015,.366,z]],'Bounded running-board tread rhythm')
        for k,z in enumerate(np.arange(front+.16,-.76,.09)):
            a.line(f'bonnet.{side}.vent{k}',[[side*(w(z)+.008),.65,z],
                [side*(w(z)+.008),.82,z-.025]],'Short vertical bonnet cooling slots; not all body edges')
        for band,u in enumerate((.70,.86)):
            for k,z in enumerate(np.arange(front+.18,-.70,.075)):
                if band==0 and k%3:continue
                a.tone(f'bonnet.{side}.roll{band}.{k}',[side*w(z)*u,crown(z,u)+.015,z],1+band)
        for band,offset in enumerate((.19,.32)):
            for k,z in enumerate(np.arange(-.60,2.48,.065)):
                if band and k%3:continue
                a.tone(f'body.{side}.lower-turn{band}.{k}',[side*(w(z)+.016),y(z)-offset,z],2 if not band else 1)
    # Smooth arched fastback. Its rear slopes into the boat tail, not a box.
    roof=[(-.76,.61,.99),(-.28,.56,1.53),(.16,.58,1.65),(.66,.59,1.61),
          (1.12,.57,1.43),(1.60,.48,1.05),(2.11,.36,.75)]
    roof_headers=roof[1:3]
    rw=PchipInterpolator([s[0] for s in roof],[s[1] for s in roof])
    rh=PchipInterpolator([s[0] for s in roof],[s[2] for s in roof])
    roof=[(float(z),float(rw(z)),float(rh(z))) for z in np.linspace(roof[0][0],roof[-1][0],65)]
    for (z0,w0,y0),(z1,w1,y1) in zip(roof,roof[1:]):
        for u0,u1 in zip(np.linspace(-1,1,13),np.linspace(-1,1,13)[1:]):
            def rp(z,ww,yy,u):return [u*ww,yy-.08*u*u,z]
            p.solids.quad(rp(z0,w0,y0,u0),rp(z0,w0,y0,u1),rp(z1,w1,y1,u1),rp(z1,w1,y1,u0))
        for side in (-1,1):p.solids.quad([side*w(z0),crown(z0,1),z0],
            [side*w(z1),crown(z1,1),z1],[side*w1,y1-.08,z1],[side*w0,y0-.08,z0])
    for side in (-1,1):
        a.line(f'roof.{side}.sweep',[[side*ww,yy-.07,z] for z,ww,yy in roof],'Arched roof flows into teardrop tail')
        for name,points in [('front',[[-.62,1.05,-.64],[-.57,1.45,-.26],[-.60,1.49,.43],[-.70,1.02,.43]]),
                            ('quarter',[[-.70,1.01,.54],[-.60,1.48,.54],[-.57,1.33,1.10],[-.64,.96,1.35]])]:
            pts=[[-side*x,yy,z] for x,yy,z in points]
            a.poly(f'glass.{side}.{name}',pts,'Slim framed side pane with broad empty interior',True)
        a.line(f'door.{side}.rear',[[side*.72,.94,.48],[side*.72,.45,.48]],'Single long coach door under pillar')
        a.line(f'door.{side}.handle',[[side*.75,.88,.17],[side*.75,.88,.37]],'Small separate coach door handle')
    # Split V screen: each white half has a true opaque cabin surface behind it.
    for side in (-1,1):
        a.poly(f'glass.front{side}',[[side*.035,1.05,-.71],[side*.55,1.05,-.71],
            [side*.50,1.46,-.28],[side*.025,1.53,-.28]],'Split V windscreen half',True)
    a.poly('glass.back',[[-.43,1.37,1.17],[.43,1.37,1.17],[.35,1.03,1.61],[-.35,1.03,1.61]],'Narrow fastback rear glass',True)
    for z,ww,yy in roof_headers:a.line(f'roof.cross{z}',[[-ww,yy-.07,z],[0,yy+.009,z],[ww,yy-.07,z]],'Curved screen header / roof crown')
    # Upright radiator, mounted lamp pods and thin bumpers make the period read.
    a.panel('radiator.frame',[[-.34,.43,front-.02],[.34,.43,front-.02],
        [.31,.94,front-.02],[-.31,.94,front-.02]],'Tall narrow upright radiator')
    for k,x in enumerate(np.arange(-.27,.28,.055)):
        a.line(f'radiator.slat{k}',[[x,.47,front-.03],[x,.88,front-.03]],'Fine bounded vertical radiator slats')
    for side in (-1,1):
        a.lamp(f'front.lamp{side}',side*.65,.92,front+.03,.135,front+.30)
        a.line(f'front.lamp-mount{side}',[[side*.65,.785,front+.17],[side*.72,.64,front+.27]],'Lamp stalk attached to extended swept fender apron')
        a.lamp(f'rear.lamp{side}',side*.25,.58,rear+.01,.055,rear-.07)
    a.line('bonnet.center',[ [0,1.045,front+.22],[0,1.075,-1.65],[0,1.03,-.77] ],'Fine central bonnet spine')
    for z,name in ((front-.09,'front'),(rear+.04,'rear')):
        a.poly(f'{name}.bumper',[[-.98,.31,z],[.98,.31,z],[.98,.36,z],[-.98,.36,z]],'Thin separate period bumper')
    wheels(p,spec,'wire')
    return finish_plan(p,spec)


def write_construction(plan, key, root, text_cpp=None):
    root.mkdir(parents=True, exist_ok=True)
    snapshot=OUTPUT/'authoring-source'/(sha(__file__)+'.py')
    snapshot.parent.mkdir(parents=True,exist_ok=True)
    if not snapshot.exists():snapshot.write_bytes(Path(__file__).read_bytes())
    text_dependencies={}
    for file in ([REPO/'scripts/native_plate_layout.py',text_cpp] if text_cpp else []):
        preserved=OUTPUT/'authoring-source'/(sha(file)+file.suffix)
        if not preserved.exists():preserved.write_bytes(file.read_bytes())
        text_dependencies[str(file)]=dict(sha256=sha(file),preserved=str(preserved.relative_to(REPO)))
    source_refs=[]
    for n in plan.spec['refs']:
        source_refs.append(dict(id=f'resK-{n}', text_sha256=sha(ARCHIVE/f'resK-{n}.txt'),
                                png_sha256=sha(ARCHIVE/f'resK-{n}.png')))
    data=dict(schema='asciicker.authored_car_target.v1', status='AUTHORED_TARGET_NOT_GAME_RENDER',
        title=plan.spec['title'], method='Original3Dconstruction; opaque-depth visibility; whole native Saitamaar page fit, no archived art stamped into frames',
        reference_page='08b069287f65400cc3673cc8757ffc63', archive=str(ARCHIVE),
        references=source_refs, transfer=plan.spec['transfer'], inference=plan.spec['inference'],
        native_matrix=[W,H], font_px=16, row_pitch=PITCH, full_native_bank=532,
        black_ink_white_paper=True, captions=False, generator=str(Path(__file__).resolve()),
        revision=4, orbit_radius=plan.spec.get('orbit',6.4),
        articulation='Complete native-row joint fit: full532glyph bank; tangent/continuation costs favor oriented joined contours, no material-specific alphabet or distorted glyphs. Selected surface rolls use sparse nested world-space tone sites, with white panel and pane centers.',
        wheel_motion=dict(turns_per_orbit=WHEEL_TURNS,axis='local x',phase_degrees_per_frame=-3.75,
            physical_direction='Forward for nose-negativeZ: top of tire moves toward negativeZ, bottom toward positiveZ. One revolution avoids aliasing the35-site sidewall and24tread repetitions at96samples.',
            method='Immutable wheel mesh, paths and tone sites rotated rigidly about fixed axle centers; prior world associations transformed by the same delta; ground contact remains fixed.',
            parts=[dict(prefix=q['prefix'],center=q['center'].tolist(),vertices=q['vertices']) for q in plan.wheel_parts]),
        fit_parameters=dict(keep=24.,precision=6.,direction=24.,closure=30.,mass=.5,spare=2.),
        scale_admission='Six selected crossed wire pairs at this shot size; vents/petals/wires omitted below18px projected wheel width, tread omitted below12px. Tire/rim/hub and rotating valve remain. Full glyph bank unchanged.',
        ink_support_admission='Every intact native glyph must keep all ink within8.5px (half row pitch) of intended selected construction/tone, as well as outside protected panes. Hard solve constraint, never post-raster clipping or glyph deletion.',
        fictional_plates=plan.plates,native_text_dependencies=text_dependencies,
        plate_contract='Stable randomly generated fictional Japanese registration per car; native kana+serial tokens are hard full-row solve constraints when all ink fits the current visible physical plate. Full region/class retained as metadata; never warp glyphs or display a number outside an occluded/edge-on plate.',
        generator_sha256=sha(__file__), source_dependencies={str(CHAPEL/name):sha(CHAPEL/name)
            for name in ['picture.py','joint_fit.py','joint_fit.cpp','motion.py','raster.cpp','catalog.json','Saitamaar.ttf']},
        paths=[dict(id=q['id'],role=q['role'],references=q['refs'],points=q['p'].tolist()) for q in plan.paths],
        bounded_tone_sites=[dict(id=q['id'],point=q['p'].tolist(),level=q['level'],
                                  references=q['refs']) for q in plan.tones],
        protected_openings=[dict(id=q['id'],points=q['p'].tolist()) for q in plan.voids],
        occlusion_vertices=np.asarray(plan.solids.v).tolist(),occlusion_triangles=plan.solids.f)
    (root/'construction.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def finalize(root, key, count,deliver=False):
    frames=[];stats=[];durations=[]
    for index in range(count):
        image=Image.open(root/'frames'/f'{index:03}.png').convert('L')
        assert image.size==(W,H) and set(np.unique(image)).issubset({0,255})
        record=json.loads((root/'frames'/f'{index:03}.json').read_text())
        stats.append(record['summary'])
        frames.append(image.resize((W*2,H*2),Image.Resampling.NEAREST).convert('P'))
        durations.append(80)
    output=OUTPUT/'gifs'/(key+'-360.gif');output.parent.mkdir(parents=True,exist_ok=True)
    frames[0].save(output,save_all=True,append_images=frames[1:],duration=durations,
                   loop=0,optimize=True,disposal=1)
    gif=Image.open(output); total=0;changes=0;previous=None
    for index in range(gif.n_frames):
        gif.seek(index); pixels=np.asarray(gif.convert('L'))
        assert set(np.unique(pixels)).issubset({0,255})
        total+=gif.info['duration']
        if previous is not None: changes+=int(np.any(pixels!=previous))
        previous=pixels.copy()
    assert gif.n_frames>=count*.9 and changes>=count*.85, 'Not a real rotating sequence'
    report=dict(schema='asciicker.authored_car_gif.v1',status='AUTHORED_TARGET_NOT_GAME_RENDER',
        file=str(output),sha256=sha(output),bytes=output.stat().st_size,
        native_matrix=[W,H],presentation=[W*2,H*2],presentation_method='2x nearest-neighbor',
        samples=count,encoded_frames=gif.n_frames,duration_ms=total,full_rotation_degrees=360,
        black_and_white_only=True,captions=False,frame_summaries=stats,
        visual_acceptance='Candidate for operator review; mechanical checks do not certify reference-level art quality')
    if deliver:
        dest=Path('/Users/r/Desktop/AAHub-car-turntables-revision-04')
        dest.mkdir(exist_ok=True);file=dest/output.name
        if file.exists() and sha(file)!=sha(output):raise RuntimeError(f'Refusing overwrite of different Desktop file: {file}')
        shutil.copy2(output,file);assert sha(file)==sha(output);report['desktop']=str(file)
    (root/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print('GIF_DONE',key,str(output),gif.n_frames,total,flush=True)


def register_targets():
    """Publish finished artifacts to the existing canonical car source owner."""
    owner=REPO/'assets/meshes/incoming/tutorial_cars.source.json'
    data=json.loads(owner.read_text());previous=data['authored_turntable_targets']
    if previous.get('revision') is None:
        data['authored_turntable_targets_revision_01']=previous
    elif previous.get('revision')==2:
        data['authored_turntable_targets_revision_02']=previous
    elif previous.get('revision')==3:
        data['authored_turntable_targets_revision_03']=previous
    targets=[]
    for key in SPECS:
        root=OUTPUT/key;c=json.loads((root/'construction.json').read_text())
        v=json.loads((root/'validation.json').read_text());gif=Path(v['file'])
        assert sha(gif)==v['sha256'] and v['samples']==v['encoded_frames']==96
        assert c['generator_sha256']==sha(__file__)
        targets.append(dict(id=key,title=c['title'],gif=str(gif.relative_to(REPO)),
            gif_sha256=v['sha256'],bytes=v['bytes'],native_matrix=v['native_matrix'],
            presentation=v['presentation'],encoded_frames=v['encoded_frames'],duration_ms=v['duration_ms'],
            reference_page=c['reference_page'],references=c['references'],
            retained_construction=c['transfer'],authored_inference=c['inference'],
            construction=str((root/'construction.json').relative_to(REPO)),
            validation=str((root/'validation.json').relative_to(REPO)),
            generator_sha256=c['generator_sha256'],
            preserved_generator=str((OUTPUT/'authoring-source'/(c['generator_sha256']+'.py')).relative_to(REPO))))
    data['authored_turntable_targets']=dict(schema='asciicker.authored_car_visual_targets.v4',revision=4,
        status='SIX_REVISED_TARGET_GIFS_EXPORTED; operator visual review pending',
        scope='AUTHORED_TARGET_NOT_GAME_RENDER; no production-engine repair or runtime acceptance',
        request='Repair missing/boxy911roof and body, add fictional Japanese plate numbers, correct backward/aliased wheels; preserve tone and six-car scope.',
        selection='Original Japanese Bluebird510, CrestaGX71, LevinAE86 and Sambar-informed open flatbed; explicitly German Porsche911; original1930s-inspired coachwork concept, not an exact historical Wraith claim.',
        authoring='Selected original3Dconstruction and protected glazing, nested sparse surface-roll tone, bounded recess/tire/chassis hatching. Full532glyph native-page joint fit with orientation and cross-row continuation objectives. Not individually hand-typed frames, archived-AA billboards or mesh-triangle wireframes.',
        format='640x476native, Saitamaar16px,17px rows;1280x952exact2xnearest-neighbor GIFs;96frames at80ms,7.68seconds per360degree orbit; pure black/white, no captions.',
        wheel_motion='One forward rigid local-axle turn per orbit (-3.75degrees/sample, nose-negativeZ); below half-period of35sidewall sites and24tread notches. Model-specific vents/petals/wires plus valve rotate; ground contacts fixed and temporal priors follow part delta. Playback judgment remains required.',
        validation='All encoded frames must match native exports. Complete native-row replay, disjoint glyph ink/advances, protected glazing, half-row intended-ink support and loop/kinematic tests supplement but never establish artistic acceptance. Revision2 is retained as a failed unsupported-ink checkpoint.',
        visual_review='Sampled native-angle review and playback observations are recorded in GODOT_GOAL.MD; operator acceptance remains pending. Prior four preserved for A/B in authored_turntable_targets_revision_01.',
        dependencies='GIFs self-contained. Reproduction requires pinned local chapel fitter/font dependencies in construction.json; archive art look-only, not vendored.',targets=targets)
    owner.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print('REGISTERED',len(targets),owner,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--car',choices=SPECS)
    parser.add_argument('--register',action='store_true')
    parser.add_argument('--frames',type=int,default=96)
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--count',type=int,default=1)
    parser.add_argument('--indices',type=int,nargs='+')
    parser.add_argument('--finalize',action='store_true')
    parser.add_argument('--deliver-desktop',action='store_true')
    args=parser.parse_args()
    if args.register:register_targets();return
    if args.car is None:parser.error('--car is required unless --register is selected')
    assert args.frames>=24
    root=OUTPUT/args.car; (root/'frames').mkdir(parents=True,exist_ok=True)
    if args.finalize:
        finalize(root,args.car,args.frames,args.deliver_desktop); return
    picture,fitting,motion=dependencies()
    plan=build_car(picture,args.car)
    fitter=fitting.JointFitter()
    plate_adapter=load_module('car_native_plate_layout',REPO/'scripts/native_plate_layout.py')
    text_cpp=plate_adapter.enable(sys.modules[__name__],fitter)
    write_construction(plan,args.car,root,text_cpp)
    tracker=motion.Contributions(fitter)
    indices=args.indices if args.indices is not None else range(args.start,min(args.frames,args.start+args.count))
    old=[];old_index=None;started=time.monotonic()
    # Resume the actual contribution map, not a screen-fixed old glyph layout.
    if args.indices is None and args.start>0:
        prior_file=root/'frames'/f'{args.start-1:03}.json'
        if prior_file.exists():
            old=json.loads(prior_file.read_text())['contributions'];old_index=args.start-1
    for index in indices:
        assert 0<=index<args.frames
        set_wheels(plan,index/args.frames)
        drawing=compose_car(plan,index/args.frames)
        labels=plate_adapter.add_registration(sys.modules[__name__],plan,drawing,fitter)
        support_distance=distance_transform_edt(drawing['target']<.3)
        forbidden=np.maximum(drawing['void'],support_distance>PITCH/2).astype('float32')
        moved=move_wheel_prior(old,plan,2*math.pi*WHEEL_TURNS*(index-old_index)/args.frames) if old else []
        prior=tracker.prior(moved,drawing) if moved else None
        image,records,rows,layout=fitter.fit(drawing['target'],forbidden=forbidden,
            previous=prior,keep=24.,precision=6.,direction=24.,closure=30.,mass=.5,spare=2.)
        # Independent complete-string typesetting, no per-character offsets.
        check=Image.new('L',(W,H),255); draw=ImageDraw.Draw(check)
        for row,text in enumerate(rows):
            draw.text((0,row*PITCH),text,font=fitting.FONT,fill=0,anchor='la')
        assert np.array_equal(np.asarray(check)>127,np.asarray(image)>127)
        ink=np.zeros((H,W),np.uint16); spans=np.zeros((H,W),np.uint16)
        for text,x,y,gi in records:
            glyph=fitter.bank[gi]; points=glyph['points']
            ink[y+points[:,1],x+points[:,0]]+=1
            spans[y:y+PITCH,x:x+glyph['token_advance']]+=1
        assert ink.max()<=1 and spans.max()<=1
        assert not np.any((np.asarray(image)==0)&(drawing['void']>.5))
        assert not np.any((np.asarray(image)==0)&(support_distance>PITCH/2))
        contributions=tracker.assign(records,drawing);old=contributions;old_index=index
        summary=dict(sample=index,angle_degrees=360*index/args.frames,
            eye=drawing['camera'].eye.tolist(),target=[0,.77,0],glyphs=len(records),
            wheel_phase_degrees=360*WHEEL_TURNS*index/args.frames,
            plate_labels=labels,
            admission=drawing['admission'],
            moving_wheel_contributions=sum(any(q['feature'].startswith('wheel.') for q in r['parts']) for r in contributions),
            ink_overlap=0,advance_overlap=0,protected_void_ink=0,
            unsupported_ink=0,max_ink_support_distance=float(support_distance[np.asarray(image)==0].max()),
            temporal_prior=None if prior is None else tracker.last,
            ink_bbox=image.point(lambda p:255-p).getbbox())
        payload=dict(rows=rows,glyphs=records,contributions=contributions,summary=summary)
        (root/'frames'/f'{index:03}.json').write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n')
        image.save(root/'frames'/f'{index:03}.png')
        # Intended construction is retained for direct before/after critique.
        Image.fromarray(np.uint8(255-drawing['target']*255)).save(root/'frames'/f'{index:03}-intended.png')
        print('FRAME_DONE',args.car,index,'glyphs',len(records),'elapsed',round(time.monotonic()-started,2),flush=True)


if __name__=='__main__':
    main()
