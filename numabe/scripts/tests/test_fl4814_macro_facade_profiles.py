"""Product geometry/provenance checks for coarse PS1 authoring (FL-4814)."""
import json
from pathlib import Path

from scripts.pipeline.compile_facade_shell import build_shell
from scripts.pipeline.compile_source_palette import (MANUAL_WALL_RGB_GRID, _quantize_manual_rgb,
                                                     compile_palette)
from scripts.pipeline.compile_world_ir_building_specs import _height_claim
from scripts.pipeline.template_resolver import discover_pack_chain, resolve_building_fallback

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = {'country_code': 'JP', 'region': 'Tokyo', 'site_contexts': ['urban_residential']}


def test_locale_size_fallback_does_not_override_source_height():
    packs = discover_pack_chain(ROOT/'assets/world_templates', CONTEXT)
    small = resolve_building_fallback(tags={}, area_m2=60, packs=packs)
    assert small['family'] == 'small_lowrise_residential'
    assert small['height_m'] == 6
    assert small['provenance_class'] == 'synthesized'
    assert _height_claim({'height': '12'}, small['height_m'])['roof_height_m'] == 12
    assert _height_claim({'building:levels': '5'}, small['height_m'])['roof_height_m'] == 15
    assert resolve_building_fallback(tags={'building':'retail'}, area_m2=60, packs=packs)['family'] == 'small_commercial'
    us = discover_pack_chain(ROOT/'assets/world_templates', {'country_code':'US'})
    assert resolve_building_fallback(tags={}, area_m2=60, packs=us) is None


def test_source_neighbourhood_palette_transfer_stays_synthesized(tmp_path):
    specs=tmp_path/'specs.json'; specs.write_text(json.dumps([{'source':'way/1'}]))
    evidence=tmp_path/'evidence.json'
    evidence.write_text(json.dumps({'source_observations': [{
        'observation_id':'wall:1','digest':'sha256:abc',
        'attributes': {'segmentation': {'semantic_candidates':['facade_wall'],
            'mask_area_px':4000,'segmentation_score':.95,
            'source_palette':{'median_rgb':[187,180,166]}}}}]}))
    result=compile_palette(str(specs),None,roles=[],locale_context=CONTEXT,image_evidence_paths=[str(evidence)])
    p=result['building_material']['way/1']['facade_profile']
    assert p['wall_rgb']==[184,176,168]
    assert p['source_observations']==['wall:1']
    assert p['provenance_class']=='synthesized'
    assert 'unresolved' in p['colour_method']
    assert result == compile_palette(str(specs),None,roles=[],locale_context=CONTEXT,image_evidence_paths=[str(evidence)])


def test_manual_building_appearance_is_the_palette_input_not_dead_data(tmp_path):
    feature = 'feature:building_footprint:way/1'
    specs = tmp_path/'specs.json'
    specs.write_text(json.dumps([{'source':'way/1', 'feature_id': feature}]))
    authored = tmp_path/'authored.json'
    authored.write_text(json.dumps({
        'schema': 'y92.fl4876.authored_building_appearance.v1',
        'buildings': {feature: {
            'what': 'hand reviewed building',
            'wall_rgb': [11, 22, 33], 'trim_rgb': [44, 55, 66],
            'roof_rgb': [77, 88, 99], 'ground_floor': 'shop',
            'features': ['awning'],
            'evidence': [{'capture_id': 'mapillary_1'}],
        }},
    }))
    result = compile_palette(
        str(specs), None, roles=[], locale_context=CONTEXT,
        authored_appearance_path=str(authored))
    entry = result['building_material_by_feature'][feature]
    profile = entry['facade_profile']
    assert profile['wall_rgb'] == [11, 22, 33]
    assert profile['trim_rgb'] == [44, 55, 66]
    assert profile['roof_rgb'] == [77, 88, 99]
    assert profile['colour_method'] == 'manual authored panorama assessment'
    assert profile['source_observations'] == ['mapillary_1']
    assert entry['authored_appearance']['features'] == ['awning']
    # The facade profile keeps the exact evidence RGB; the 256-entry Classic
    # material shares a recipe, so it carries the CIELAB-nearest grid snap
    # (walls grid 16, roofs grid 32) of that evidence.
    assert result['materials'][str(entry['wall'])]['bg'] == list(
        _quantize_manual_rgb((11, 22, 33), MANUAL_WALL_RGB_GRID))
    assert result['materials'][str(entry['roof'])]['bg'] == list(_quantize_manual_rgb((77, 88, 99)))


def test_discrete_facade_partition_preserves_scale_area_and_collision():
    packs=discover_pack_chain(ROOT/'assets/world_templates',CONTEXT)
    profile=next(p for pack in packs for p in pack.get('facade_profiles',[]))
    ring=[(0,0),(18,0),(18,9),(0,9)] # 8 x 4 real metres
    for poly in (ring,list(reversed(ring))):
        vertices, faces, _=build_shell(poly,6,(1,2,3),(4,5,6),(7,8,9),2.25,profile=profile)
        assert all(v[-1]==255 for v in vertices)
        assert min(v[2] for v in vertices)==0
        assert max(v[2] for v in vertices) < 6.3*2.25
        area=0
        for face in faces:
            a,b,c,d=[vertices[i] for i in face]
            width=((a[0]-b[0])**2+(a[1]-b[1])**2)**.5
            height=abs(c[2]-b[2])
            assert width>0 and height>0
            area += width*height
        expected=54*(6+min(.25,6*.025))*2.25
        assert abs(area-expected)<1e-7 # complete walls, no overlapping panels
        glass_faces=[f for f in faces if vertices[f[0]][6:9]==tuple(profile['glass_rgb'])]
        assert len(glass_faces)>4
        assert all(abs(vertices[f[1]][0]-vertices[f[0]][0])<18 for f in glass_faces)
