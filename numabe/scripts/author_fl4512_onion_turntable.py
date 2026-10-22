#!/usr/bin/env python3
"""Original AAHub-informed spinning onion target; not engine-render proof."""
from __future__ import annotations
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import time

import numpy as np
from PIL import Image,ImageDraw
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import distance_transform_edt
import author_fl4831_car_turntables as core

OUT=core.REPO/'articles/fl4512-aahub-onion-turntable-20261020/revision-04'
ARCHIVE=Path('/Users/r/Projects/ascii-art-archive/collections/aahub/yasai-kudamono')
PAGE='https://aahub.org/mlt/af2d0567b491b61bd90183e58b2bb1bf'
NOTES={
 213:'Single pointed bulb; large white upper region and dense dot-to-colon lower tone. Retain graduated punctuation, reject uniform all-over flat density.',
 215:'Sparse skin arcs gather into a dry stem and opposite fine root tuft. Retain long tapering skin gestures and subordinate frayed ends; not the diagonal pose.',
 217:'Retain gathered pointed onion neck, selected curving skin arcs and fine colon tone. Long arcs are not inherently pumpkin ribs; operator supplied new silhouette references to correct that earlier misdiagnosis. No lobed geometry.',
 219:'AA articulation/tone anchor: flowing skin direction, fine dot/colon gradients, broad white highlights and compact basal roots. New operator images govern pointed silhouette and root-to-tip convergence. Reject copied two-object composition, regular lobed ribs and ground-shadow wash.'}
USER_REFERENCES=[
 ('image-1','/var/folders/r8/n6419zqn3lx20lt6f2dph24w0000gn/T/TemporaryItems/NSIRD_screencaptureui_3X8C9k/Screenshot 2026-10-20 at 03.09.43.png',
  'Continuous pointed taper into a rounded lower bulb; selected sweeping arcs converge at both top and basal root. Transfer shape/flow, not bold filled icon stroke weights or watermark.'),
 ('image-2','/var/folders/r8/n6419zqn3lx20lt6f2dph24w0000gn/T/TemporaryItems/NSIRD_screencaptureui_e2tcjP/Screenshot 2026-10-20 at 03.10.39.png',
  'Asymmetric taper, curved longitudinal skin grain, narrow dry pointed tip and compact root tuft/rim. Translate engraving tone selectively into native AA, not dense copied crosshatching.')]


CENTER=np.array([0.,1.25,0.])
TILT=math.radians(35)
AXIS=np.array([math.sin(TILT)*math.cos(.35),math.cos(TILT),-math.sin(TILT)*math.sin(.35)])
ORIENT_AXIS=core.unit(np.cross([0,1,0],AXIS))


def rotate_vectors(points,axis,angle):
    p=np.asarray(points,float);c,s=math.cos(angle),math.sin(angle)
    return p*c+np.cross(axis,p)*s+np.sum(p*axis,axis=-1)[...,None]*axis*(1-c)


def orient(points):
    return rotate_vectors(np.asarray(points)-CENTER,ORIENT_AXIS,TILT)+CENTER


def spin(points,angle):
    return rotate_vectors(np.asarray(points)-CENTER,AXIS,angle%(2*math.pi))+CENTER


HEIGHTS=np.array([.15,.22,.35,.55,.80,1.02,1.24,1.46,1.67,1.88,2.08,2.27,2.45,2.59,2.68])
RADII=np.array([.045,.37,.70,.91,1.00,1.02,.97,.82,.61,.41,.25,.135,.075,.035,.015])
PROFILE=PchipInterpolator(HEIGHTS,RADII)
DERIVATIVE=PROFILE.derivative()


def surface(y,theta,lift=0):
    # One smooth asymmetric skin, no periodic lobes or radial groove field.
    r=float(PROFILE(y))*(1+.018*math.cos(theta+.6)*(1-.3*y))+lift
    return np.array([.028*math.sin(2*y)+r*math.cos(theta),y,
                     .023*math.cos(1.7*y)+r*.97*math.sin(theta)])


FOLDS=[(-2.91,.165,2.63,.28),(-1.58,.165,2.63,-.22),
       (-.10,.165,2.63,.15),(1.21,.165,2.63,-.33),(2.55,.165,2.63,.18)]


def fold_angle(fold,y):
    theta,lo,hi,bend=fold;t=(y-lo)/(hi-lo)
    return theta+bend*math.sin(math.pi*t)+.045*t


def build(picture):
    p=picture.Plan('onion');refs=[217,219];ys=np.linspace(HEIGHTS[0],HEIGHTS[-1],100);angles=np.linspace(0,2*math.pi,113)
    for y0,y1 in zip(ys,ys[1:]):
        for a0,a1 in zip(angles,angles[1:]):
            p.solids.quad(surface(y0,a0),surface(y0,a1),surface(y1,a1),surface(y1,a0))
    # New operator references require FLOWING convergent arcs, not isolated
    # scratches. These lie on one smooth pointed skin, not grooved squash lobes.
    for k,fold in enumerate(FOLDS):
        theta,lo,hi,bend=fold
        p.line(f'skin.flow{k}',[surface(y,fold_angle(fold,y),.012) for y in np.linspace(lo,hi,145)],
               refs,'Continuous curved onion skin arc gathers at pointed tip and compact basal root; shallow surface mark, not a raised rib')
    # Two unequal dry tip ends continue the SAME taper. No separately mounted
    # neck cylinder, shoulder stump or radial crown.
    for k,(aa,length) in enumerate(((.3,.075),(3.25,.045))):
        start=surface(2.65,aa,.005)
        p.line(f'tip.fiber{k}',[start,start+[.014, length*.55,.006],start+[.025,length,.012]],
               [215,219],'Small unequal dry tip fibers continue the pointed onion skin')
    # A compact basal rim and short root tuft, rather than detached stars.
    basal=[[.12*math.cos(a),.16,.11*math.sin(a)] for a in np.linspace(0,2*math.pi,41)]
    p.line('root.basal-rim',basal,[219],'Compact basal root plate where skin arcs converge')
    for k,theta in enumerate((.2,.9,1.8,2.7,3.8,4.7,5.4)):
        pts=[]
        for t in np.linspace(0,1,15):
            rr=.045+.10*t;aa=theta+.19*math.sin(4*t+k)
            pts.append([rr*math.cos(aa),.17-.11*t+.01*math.sin(5*t+k),rr*math.sin(aa)])
        p.line(f'root.fiber{k}',pts,[215,219],'Fine downward basal root tuft, not a second crown or ground shadow')
    # The lattice is on the ORIGINAL skin, with a nested ordered pattern.
    # Rotation transports its sites; fixed lighting changes form tone, not
    # an independent screen-space noise phase.
    p.tone_meta=[]
    order=np.array([[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]])/16
    for j,y in enumerate(np.arange(.25,2.27,.030)):
        for k,theta in enumerate(np.arange(0,2*math.pi,.055)):
            # One light band; structural arcs provide flow without carving
            # repeated white rib lanes into the whole bulb.
            gap_theta=1.88+.12*math.sin(3*y)
            gap=abs(math.atan2(math.sin(theta-gap_theta),math.cos(theta-gap_theta)))
            normal=core.unit([math.cos(theta),-float(DERIVATIVE(y)),math.sin(theta)/.97])
            fid=f'tone.skin.{j}.{k}'
            p.tone(fid,surface(y,theta,.018),1,refs)
            p.tone_meta.append(dict(normal=normal,threshold=float(order[j%4,k%4]),
                                    seam_gap=.50<y<1.62 and gap<.09,latitude=float(y)))
    p.camera=lambda t:core.Camera([0,2.55,-6.0],[0,1.25,0],focal=360.)
    p.base_vertices=orient(p.solids.v)
    for q in p.paths:q['p']=orient(q['p'])
    for q in p.tones:q['p']=orient(q['p'])
    for q in p.tone_meta:q['normal']=rotate_vectors(q['normal'],ORIENT_AXIS,TILT)
    p.base_paths=copy.deepcopy(p.paths);p.base_tones=copy.deepcopy(p.tones)
    p.ys=ys;p.ring_angles=np.linspace(0,2*math.pi,513)
    return p


def tangent_rims(function,ys,angle,cam):
    rims=[[],[]]
    for y in ys:
        aa=np.linspace(0,2*math.pi,129)
        local=np.array([function(y,a,.009) for a in aa])
        dt=np.array([function(y,a+.0001)-function(y,a-.0001) for a in aa])
        dy=np.array([function(y+.0001,a)-function(y-.0001,a) for a in aa])
        normal=rotate_vectors(rotate_vectors(np.cross(dy,dt),ORIENT_AXIS,TILT),AXIS,angle)
        points=spin(orient(local),angle)
        facing=np.sum(normal*(cam.eye-points),axis=1)
        at=np.flatnonzero(facing[:-1]*facing[1:]<0)
        if len(at)!=2:continue
        tangent=[]
        for ix in at:
            theta=aa[ix]+(aa[ix+1]-aa[ix])*facing[ix]/(facing[ix]-facing[ix+1])
            tangent.append(spin(orient(function(y,theta,.009)),angle))
        tangent.sort(key=lambda q:cam.project(q)[0])
        for j in (0,1):rims[j].append(tangent[j])
    return rims


def compose(p,t):
    angle=2*math.pi*t
    p.solids.v=spin(p.base_vertices,angle).tolist()
    p.paths=copy.deepcopy(p.base_paths)
    for q in p.paths:q['p']=spin(q['p'],angle)
    cam=p.camera(0)
    # True migrating bulb/neck silhouettes, not a camera-facing outline stamp.
    rims=tangent_rims(surface,p.ys,angle,cam)
    for j in (0,1):p.line(f'contour.{j}',rims[j],[213,217,219],'Migrating silhouette, never all mesh edges or a camera-facing AA billboard')
    light=core.unit([-.65,1.10,-1.0]);p.tones=[]
    for base,meta in zip(p.base_tones,p.tone_meta):
        normal=rotate_vectors(meta['normal'],AXIS,angle);lambert=float(np.dot(normal,light))
        darkness=float(np.clip((.87-lambert)/1.32,0,1))
        coverage=.13+.87*darkness
        if meta['seam_gap'] or meta['threshold']>=coverage:continue
        site=copy.deepcopy(base);site['p']=spin(site['p'],angle)
        site['level']=1 if darkness<.40 else 2
        p.tones.append(site)
    drawing=p.compose(0)
    drawing['phase_degrees']=360*t
    return drawing


def construction(p):
    OUT.mkdir(parents=True,exist_ok=True);snapshot=OUT/'authoring-source'/f'{core.sha(__file__)}.py'
    snapshot.parent.mkdir(exist_ok=True)
    if not snapshot.exists():snapshot.write_bytes(Path(__file__).read_bytes())
    # Preserve exact shared adapter too: reproduction is not bound to a moving
    # working-tree version of the rejected car-target generator.
    adapter=OUT/'authoring-source'/f'{core.sha(core.__file__)}.car-adapter.py'
    if not adapter.exists():adapter.write_bytes(Path(core.__file__).read_bytes())
    dependencies={str(core.CHAPEL/n):core.sha(core.CHAPEL/n) for n in
        ['picture.py','joint_fit.py','joint_fit.cpp','motion.py','raster.cpp','catalog.json','Saitamaar.ttf']}
    supplied=[];(OUT/'references').mkdir(exist_ok=True)
    for rid,path,note in USER_REFERENCES:
        target=OUT/'references'/(rid+'.png')
        if not target.exists():shutil.copy2(path,target)
        supplied.append(dict(id=rid,original=path,preserved=str(target.relative_to(core.REPO)),
                             sha256=core.sha(target),qualitative_transfer=note))
    data=dict(schema='asciicker.authored_onion_target.v1',status='AUTHORED_TARGET_NOT_GAME_RENDER; operator review pending',
        request='Find AAHub onion references and create a spinning onion animation',reference_page=PAGE,
        references=[dict(id=f'res{i}',text=str(ARCHIVE/f'resK-{i}.txt'),text_sha256=core.sha(ARCHIVE/f'resK-{i}.txt'),
            png=str(ARCHIVE/f'resK-{i}.png'),png_sha256=core.sha(ARCHIVE/f'resK-{i}.png'),qualitative_transfer=notes) for i,notes in NOTES.items()],
        inference='One original asymmetric onion with integrated pointed taper; unseen sides, convergent skin-arc layout, dry tip and basal-root geometry authored from the supplied silhouette/engraving references. Static references do not prove temporal quality. No archived AA or supplied-reference pixels/text stamped into frames.',
        contract='Fixed camera; original3Dbulb rotates360degrees around35degree tilted axis through bulb center. Projection is40percent of original focal length, redrawn with unchanged16pxfont (not downsampled glyphs). Surface seams, tone sites, neck and roots follow rotation. True surface-normal tangency silhouettes and fixed-light tone follow the current geometry. Full532native Saitamaar glyphs, complete proportional rows,17px pitch,640x476native; exact2xGIF,96frames at80ms; pure black/white, no captions/background coloring.',
        revision=4,projection_ratio=.4,spin_axis=AXIS.tolist(),spin_center=CENTER.tolist(),tilt_degrees=35,
        user_references=supplied,
        reauthoring='Revision03rejected despite improvement. Operator images1/2supersede stubby/round interpretation: continuous pointed taper flows into a rounded lower bulb; five selected long curved skin arcs converge at tip and basal root on a smooth non-lobed mass. Two unequal dry tip fibers and compact root rim/tuft replace separately mounted neck/crown and scattered scratches. Retain accepted small oblique framing and native AA tone; no copied icon weights, engraving pixels or watermark.',
        tone='Nested object-space ordered stipple; dot/colon levels from fixed-light form turns, broad highlight and skin-seam lanes left white. No independent per-frame random dither or density-only silhouette.',
        generator_sha256=core.sha(__file__),preserved_generator=str(snapshot.relative_to(core.REPO)),
        shared_adapter_sha256=core.sha(core.__file__),preserved_adapter=str(adapter.relative_to(core.REPO)),source_dependencies=dependencies,
        profile=dict(y=HEIGHTS.tolist(),radius=RADII.tolist()),
        paths=[dict(id=q['id'],points=q['p'].tolist(),role=q['role'],references=q['refs']) for q in p.base_paths],
        tone_site_count=len(p.base_tones),occlusion_vertices=p.base_vertices.tolist(),occlusion_triangles=p.solids.f)
    (OUT/'construction.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def finalize(deliver=False):
    images=[];summaries=[]
    for i in range(96):
        im=Image.open(OUT/'frames'/f'{i:03}.png').convert('L')
        assert im.size==(640,476) and set(np.unique(im)).issubset({0,255})
        images.append(im.resize((1280,952),Image.Resampling.NEAREST))
        summaries.append(json.loads((OUT/'frames'/f'{i:03}.json').read_text())['summary'])
    gif=OUT/'onion-360.gif'
    images[0].save(gif,save_all=True,append_images=images[1:],duration=80,loop=0,disposal=1)
    decoded=Image.open(gif);assert decoded.n_frames==96
    for i in range(96):
        decoded.seek(i);assert np.array_equal(np.asarray(decoded.convert('L')),np.asarray(images[i]))
    report=dict(schema='asciicker.authored_onion_gif.v1',status='AUTHORED_TARGET_NOT_GAME_RENDER; operator review pending',
        file=str(gif),sha256=core.sha(gif),bytes=gif.stat().st_size,encoded_frames=96,duration_ms=7680,
        frame_summaries=summaries,validation='Exact decoded/native pixel parity, native text replay, disjoint glyph ink/advance, no unsupported ink; not perceptual acceptance.')
    if deliver:
        dest=Path('/Users/r/Desktop/AAHub-onion-pointed-reference-360.gif')
        if dest.exists() and core.sha(dest)!=core.sha(gif):raise RuntimeError('Desktop onion exists with different content; refusing overwrite')
        shutil.copy2(gif,dest);assert core.sha(dest)==core.sha(gif);report['desktop']=str(dest)
    (OUT/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print('ONION_GIF',gif,report['sha256'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',type=int,default=0);parser.add_argument('--count',type=int,default=1)
    parser.add_argument('--indices',type=int,nargs='+');parser.add_argument('--finalize',action='store_true')
    parser.add_argument('--deliver-desktop',action='store_true');args=parser.parse_args()
    if args.finalize:finalize(args.deliver_desktop);return
    picture,fitting,motion=core.dependencies();p=build(picture);construction(p)
    (OUT/'frames').mkdir(exist_ok=True);fitter=fitting.JointFitter();tracker=motion.Contributions(fitter)
    old=[];old_index=None;started=time.monotonic()
    if args.indices is None and args.start>0:
        previous=OUT/'frames'/f'{args.start-1:03}.json'
        if previous.exists():old=json.loads(previous.read_text())['contributions'];old_index=args.start-1
    indices=args.indices if args.indices is not None else range(args.start,min(96,args.start+args.count))
    for i in indices:
        drawing=compose(p,i/96)
        moved=copy.deepcopy(old)
        for rec in moved:
            for part in rec['parts']:
                if part['feature'].startswith('contour.'):
                    # View-dependent silhouette has no fixed material point.
                    # Release it, not a frozen screen glyph or a false rigid prior.
                    rec['supported_ink']=0
                else:part['world']=spin(part['world'],2*math.pi*(i-old_index)/96).tolist()
        prior=tracker.prior(moved,drawing) if moved else None
        distance=distance_transform_edt(drawing['target']<.3)
        forbidden=(distance>8.5).astype('float32')
        image,records,rows,layout=fitter.fit(drawing['target'],forbidden=forbidden,previous=prior,
            keep=24.,precision=6.,direction=24.,closure=30.,mass=.5,spare=2.)
        check=Image.new('L',(640,476),255);draw=ImageDraw.Draw(check)
        for row,text in enumerate(rows):draw.text((0,row*17),text,font=fitting.FONT,fill=0,anchor='la')
        assert np.array_equal(np.asarray(check)>127,np.asarray(image)>127)
        ink=np.zeros((476,640),np.uint16);span=np.zeros_like(ink)
        for text,x,y,gi in records:
            glyph=fitter.bank[gi];pts=glyph['points'];ink[y+pts[:,1],x+pts[:,0]]+=1
            span[y:y+17,x:x+glyph['token_advance']]+=1
        assert ink.max()<=1 and span.max()<=1
        assert not np.any((np.asarray(image)==0)&(distance>8.5))
        old=tracker.assign(records,drawing);old_index=i
        summary=dict(sample=i,phase_degrees=360*i/96,camera_eye=drawing['camera'].eye.tolist(),
            spin_axis=AXIS.tolist(),projection_ratio=.4,
            glyphs=len(records),ink_overlap=0,advance_overlap=0,unsupported_ink=0,
            max_ink_support_distance=float(distance[np.asarray(image)==0].max()),
            ink_bbox=image.point(lambda v:255-v).getbbox(),temporal_prior=tracker.last if prior is not None else None)
        (OUT/'frames'/f'{i:03}.json').write_text(json.dumps(dict(rows=rows,glyphs=records,contributions=old,summary=summary),ensure_ascii=False,separators=(',',':'))+'\n')
        image.save(OUT/'frames'/f'{i:03}.png')
        Image.fromarray(np.uint8(255-drawing['target']*255)).save(OUT/'frames'/f'{i:03}-intended.png')
        print('ONION_FRAME',i,len(records),round(time.monotonic()-started,2),flush=True)


if __name__=='__main__':main()
