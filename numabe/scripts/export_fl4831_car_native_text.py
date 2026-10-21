#!/usr/bin/env python3
"""Export exact revision-04 car rows beside hash-matched approved GIFs.

No redraw, glyph substitution or GIF mutation. Checks the entire text animation
against the original native rasters and the approved GIF's decoded timeline
before publishing any exports. UTF-8 LF rows / U+000C whole-frame separator.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO=Path(__file__).resolve().parents[1]
SOURCE=REPO/'articles/fl4831-aahub-car-turntables-20261019/revision-04'
FONT=Path('/Users/r/Projects/trueff-shallot/evidence/ascii-authoring/church-graveyard-remake-20261015/checkpoint-remake/Saitamaar.ttf')
KEYS=['bluebird510','cresta_gx71','porsche911','ae86','kei_sambar','teardrop1930']


def sha(data):return hashlib.sha256(data).hexdigest()


def safe_write(path,data):
    if path.exists() and path.read_bytes()!=data:
        raise RuntimeError(f'Refusing to replace a different existing file: {path}')
    if not path.exists():path.write_bytes(data)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-root',type=Path,required=True)
    args=parser.parse_args()
    destination=args.approved_root/'AAHub-car-turntables-revision-04'
    if not destination.is_dir():raise RuntimeError(f'Missing approved car folder: {destination}')
    font_bytes=FONT.read_bytes();font=ImageFont.truetype(str(FONT),16)
    staged=[];metadata=[]
    for key in KEYS:
        filename=f'{key}-360.gif';approved=destination/filename;original=SOURCE/'gifs'/filename
        gif_bytes=approved.read_bytes()
        if sha(gif_bytes)!=sha(original.read_bytes()):
            raise RuntimeError(f'Approved GIF differs from revision-04 source: {approved}')
        with Image.open(approved) as gif:
            decoded=[];duration=0
            for i in range(gif.n_frames):
                gif.seek(i);dt=gif.info['duration'];duration+=dt
                decoded.append((duration,np.asarray(gif.convert('L')).copy()))
            assert duration==96*80,(key,duration)
        frames=[];decoded_index=0
        for i in range(96):
            record=json.loads((SOURCE/key/'frames'/f'{i:03}.json').read_text())
            rows=record['rows'];assert len(rows)==28 and all(isinstance(r,str) for r in rows)
            replay=Image.new('L',(640,476),255);draw=ImageDraw.Draw(replay)
            for row,text in enumerate(rows):draw.text((0,row*17),text,font=font,fill=0,anchor='la')
            binary=replay.point(lambda p:255 if p>127 else 0)
            with Image.open(SOURCE/key/'frames'/f'{i:03}.png') as native:
                assert np.array_equal(np.asarray(binary),np.asarray(native.convert('L'))),(key,i,'native replay')
            while decoded[decoded_index][0]<=i*80:decoded_index+=1
            assert np.array_equal(np.asarray(binary.resize((1280,952),Image.Resampling.NEAREST)),
                                  decoded[decoded_index][1]),(key,i,'approved GIF replay')
            frames.append(rows)
        txt='\f'.join('\n'.join(rows) for rows in frames).encode('utf-8')
        assert txt.decode('utf-8').split('\f')==['\n'.join(r) for r in frames]
        native=(json.dumps(dict(schema='shallot.authored-text-animation.v1',width=640,height=476,
            font='Saitamaar.ttf',fontSize=16,lineHeight=17,frameDurationMs=80,frames=frames),
            ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
        staged.extend([(f'{key}-360.txt',txt),(f'{key}-360.text.json',native)])
        metadata.append(dict(subject=key,gif=filename,gif_sha256=sha(gif_bytes),
            txt=f'{key}-360.txt',txt_sha256=sha(txt),native_json=f'{key}-360.text.json',
            frames=96,encoded_gif_frames=len(decoded),duration_ms=duration,
            verified='All 96 text frames match original native PNG and hash-matched approved GIF timeline exactly'))
        print('VERIFIED',key,'96 native text frames / approved GIF',flush=True)
    manifest=(json.dumps(dict(schema='asciicker.native-animation-text-package.v1',revision=4,
        text_encoding='UTF-8',row_separator='LF U+000A',frame_separator='FORM FEED U+000C',
        whitespace='Preserve every character and space; do not trim rows or normalize fullwidth/halfwidth spaces',
        font='Saitamaar.ttf',font_sha256=sha(font_bytes),font_px=16,row_pitch_px=17,
        native_size=[640,476],gif_size=[1280,952],frame_duration_ms=80,
        subjects=metadata),ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    staged.extend([('Saitamaar.ttf',font_bytes),('native-text-format.json',manifest)])
    # Preflight all destinations before changing either package.
    for parent in [destination,SOURCE/'gifs']:
        for filename,data in staged:
            path=parent/filename
            if path.exists() and path.read_bytes()!=data:raise RuntimeError(f'Existing different output: {path}')
    for parent in [destination,SOURCE/'gifs']:
        for filename,data in staged:safe_write(parent/filename,data)
    for entry in metadata:
        assert sha((destination/entry['gif']).read_bytes())==entry['gif_sha256'],'Approved GIF was altered'
        assert (destination/entry['txt']).read_bytes()==(SOURCE/'gifs'/entry['txt']).read_bytes()
    print('EXPORTED',destination,'six complete TXT animations, native JSON, font; GIFs unchanged',flush=True)


if __name__=='__main__':main()
