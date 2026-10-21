"""Reject terrain support crossing a visible building footprint (FL-4814)."""
import math
from scripts.pipeline.bake_osm_building_footprints import (
    _bake_one, fmt, HEIGHT_STEP_WORLD, terrain_support_inside_footprint,
)


def test_vertex_inside_wall_is_not_sufficient():
    square = [(0,0),(16,0),(16,16),(0,16)]
    assert not terrain_support_inside_footprint(1,8,square)
    assert terrain_support_inside_footprint(8,8,square)
    concave = [(0,0),(16,0),(16,16),(9,16),(9,6),(7,6),(7,16),(0,16)]
    assert not terrain_support_inside_footprint(6,8,concave)
    assert terrain_support_inside_footprint(4,4,concave)


def test_actual_bake_never_raises_an_exterior_interpolation_cell():
    patches = {}
    for y in range(3):
        for x in range(3):
            p = fmt.A3DPatch(x=x,y=y)
            for row in p.height:
                for i in range(len(row)): row[i]=128
            patches[x,y]=p
    poly=[(1,1),(21,1),(21,21),(1,21)]
    result=_bake_one(patches, {'source':'test','footprint':poly,'roof_height_m':6},
                     6,0,0,contain_terrain_support=True)
    assert result['ok']
    changed=set()
    for (px,py),p in patches.items():
        for y,row in enumerate(p.height):
            for x,h in enumerate(row):
                if h>128:
                    wx,wy=px*8+x*2,py*8+y*2
                    changed.add((wx,wy))
                    # Every incident square's vertices remain within the wall.
                    for dx in (-2,2):
                        for dy in (-2,2):
                            assert 1<=wx+dx<=21 and 1<=wy+dy<=21
    assert changed


def test_tiny_building_does_not_relax_containment_or_disappear_as_success():
    p=fmt.A3DPatch(x=0,y=0)
    before=[row[:] for row in p.height]
    result=_bake_one({(0,0):p},
        {'footprint':[(1,1),(3,1),(3,7),(1,7)],'roof_height_m':3},
        6,0,0,contain_terrain_support=True)
    assert not result['ok']
    assert result['reason']=='no_contained_height_support'
    assert p.height==before


def test_small_collision_mesh_uses_source_footprint_and_collision_faces(tmp_path):
    from scripts.pipeline.bake_osm_building_footprints import _compile_small_collision_mass, write_a3d
    patch=fmt.A3DPatch(x=0,y=0)
    for row in patch.height:
        for i in range(len(row)):row[i]=128
    ground=tmp_path/'ground.a3d'
    write_a3d(ground,fmt.A3DHeader(1),[patch],b'')
    poly=[(1,1),(7,1),(7,4),(4,4),(4,7),(1,7)]
    palette={'materials':{'6':{'bg':[100,100,100]},'7':{'bg':[60,60,60]}}}
    for ring in (poly,list(reversed(poly))):
        spec={'source':'way/test','inst_name':'test','footprint':ring,'roof_height_m':3,'content_scale':3}
        result=_compile_small_collision_mass(spec,ground,tmp_path,{'wall':6,'roof':7},palette)
        assert result['collision_alpha']==0
        assert abs(result['roof_area_world2']-27)<1e-8
        text=(tmp_path/result['mesh_name']).read_text()
        rows=text.split('end_header\n')[1].splitlines()
        count=int(text.split('element vertex ')[1].splitlines()[0])
        assert all(row.split()[-1]=='0' for row in rows[:count])


def test_fixture_dimensions_and_noncollision_contract():
    from scripts.pipeline.classic_procedural_mesh import _build_archetype
    for subtype,height in [('bench',.95),('vending_machine',1.85),('utility_pole',7)]:
        vertices,faces=_build_archetype(subtype,3)
        assert abs(max(v[2] for v in vertices)-height*3)<1e-8
        assert all(v[-1]==255 for v in vertices)
        assert faces


def test_actual_support_audit_rejects_corrupted_height(tmp_path):
    import json
    from scripts.pipeline.bake_osm_building_footprints import audit_terrain_support, write_a3d
    p=fmt.A3DPatch(x=0,y=0)
    for row in p.height:
        for i in range(len(row)):row[i]=128
    ground=tmp_path/'ground.a3d';built=tmp_path/'built.a3d';specs=tmp_path/'specs.json'
    write_a3d(ground,fmt.A3DHeader(1),[p],b'')
    specs.write_text(json.dumps([{'footprint':[(1,1),(7,1),(7,7),(1,7)]}]))
    p.height[2][2]=220
    write_a3d(built,fmt.A3DHeader(1),[p],b'')
    assert audit_terrain_support(ground,built,specs)==[]
    p.height[1][1]=220
    write_a3d(built,fmt.A3DHeader(1),[p],b'')
    assert audit_terrain_support(ground,built,specs)==[(2.,2.)]
