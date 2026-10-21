"""Native diagnostic colors must match Classic and reject mixed frames."""
import json
import pytest
from scripts.xp_to_png import classic_resolved_layer


def test_native_bottom_origin_and_palette_clamp(tmp_path):
    p=tmp_path/'cells.jsonl'
    rows=[{'x':0,'y':0,'glyph_codepoint':66,'fg_palette_idx':0,'bg_palette_idx':255},
          {'x':0,'y':1,'glyph_codepoint':65,'fg_palette_idx':196,'bg_palette_idx':16}]
    p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    _,_,cells=classic_resolved_layer((1,2,[(65,b'bad',b'bad'),(66,b'bad',b'bad')]),p)
    assert cells==[(65,bytes((255,0,0)),bytes((0,0,0))),
                   (66,bytes((0,0,0)),bytes((255,255,255)))]


@pytest.mark.parametrize('kind',['glyph','incomplete','duplicate'])
def test_native_preview_rejects_mismatched_frame(tmp_path,kind):
    p=tmp_path/'cells.jsonl'
    row={'x':0,'y':0,'glyph_codepoint':65,'fg_palette_idx':16,'bg_palette_idx':16}
    if kind=='glyph':row['glyph_codepoint']=66
    rows=[] if kind=='incomplete' else [row,row] if kind=='duplicate' else [row]
    p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError):classic_resolved_layer((1,1,[(65,b'bad',b'bad')]),p)
