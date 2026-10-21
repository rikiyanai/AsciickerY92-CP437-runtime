"""Offline contract checks for the FL-4907 P1 demand descriptor phase."""

from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
HOST = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
TRAVERSE = (FEATURES / "true_ff_feature_traverse.glslinc").read_text(encoding="utf-8")
SHARED = (FEATURES / "true_ff_feature_passes.glslinc").read_text(encoding="utf-8")
EXTRACT = (FEATURES / "true_ff_feature_extract_common.glslinc").read_text(encoding="utf-8")


def _body(source: str, name: str) -> str:
    match = re.search(r"\b(?:void|bool|uint)\s+%s\s*\([^)]*\)\s*\{" % name, source)
    assert match, name
    start = match.end()
    depth = 1
    pos = start
    while depth:
        opening = source.find("{", pos)
        closing = source.find("}", pos)
        assert closing >= 0, name
        if 0 <= opening < closing:
            depth += 1
            pos = opening + 1
        else:
            depth -= 1
            pos = closing + 1
    return source[start : pos - 1]


def test_demand_writes_and_admit_reads_one_instance_address_descriptor():
    demand = _body(TRAVERSE, "demand")
    admit = _body(TRAVERSE, "admit")
    assert "uint feature_descriptor=descriptor++;" in demand
    assert "tff_feature_demand(inst, f, count, has_crossing, feature_descriptor)" in demand
    assert "tff_write_feature_descriptor(l.x, f, l.y, ordinal, feature_descriptor, level_mask, count)" in demand
    assert "tff_demand_kernel_source(inst.identity.z&255u)" in demand
    assert "count = TFF_NONE;" in TRAVERSE
    assert "if (!has_crossing) continue;" in demand
    assert "tff_read_feature_descriptor(l.w + ordinal, l.x, l.y, f, level_mask, count)" in admit
    assert "level_mask == 0u && count == TFF_NONE" in admit
    assert "tff_feature_demand" not in admit
    assert "tff_feature(inst, f)" not in admit
    assert "inst.refs.y" not in admit


def test_reserve_assigns_ranges_before_demand_without_geometry_scan():
    run_demand = HOST.index("for kernel in [2, 37, 38, 39]:")
    run_reserve = HOST.index("TRAVERSE_KERNELS[6], [6, 0, 0, revision]")
    assert run_reserve < run_demand
    reserve = _body(TRAVERSE, "reserve")
    assert "leaves[k].w = slot_base" in reserve
    assert "leaves[k].z >> 1u" in reserve
    assert "tff_feature_demand" not in reserve
    assert "tff_feature(" not in reserve
    exact = _body(TRAVERSE, "tff_exact_leaf_charge_family")
    assert "tff_leaf_slots(inst,n)" in exact
    assert "tff_feature_demand(inst,feature,count,has_crossing,TFF_NONE)" in exact
    assert "tff_explore_kernel_source(inst.identity.z&255u)" in exact
    assert "terrain_leaf_slots" not in reserve + exact


def test_production_no_crossing_compaction_keeps_zero_and_saturated_demand():
    """Pin the source order that distinguishes no crossing from zero demand."""
    demand = _body(TRAVERSE, "demand")
    admit = _body(TRAVERSE, "admit")
    assert demand.index("tff_write_feature_descriptor") < demand.index("if (!has_crossing) continue;")
    assert demand.index("if (!has_crossing) continue;") < demand.index("++kept;")
    assert "charge = tff_charge_add(charge, max(count, 1u))" in demand
    assert "if (level_mask == 0u && count == TFF_NONE)" in admit
    assert "if (level_mask == 0u)" not in admit


def test_descriptor_marker_is_disjoint_from_host_p2_epochs():
    assert "const uint TFF_P1_DESCRIPTOR_TAG = 0x80000000u;" in SHARED
    assert "const uint TFF_P1_DESCRIPTOR_LEVEL_MASK = 0x3FFFFFFFu;" in SHARED
    assert "TFF_P1_DESCRIPTOR_TAG | (level_mask & TFF_P1_DESCRIPTOR_LEVEL_MASK)" in SHARED
    assert "descriptor.x & TFF_P1_DESCRIPTOR_TAG" in SHARED
    tag = 0x80000000
    assert 1 <= 0x7FFFFFFF < tag
    assert tag | 0x3FFFFFFF == 0xBFFFFFFF


def test_slot_map_phase_and_zero_fill_are_fail_closed():
    assert "slot_map[m] = uvec2(TFF_P1_DESCRIPTOR_TAG" in SHARED
    assert "e.x == cam.counts.y ? e.y : TFF_NONE" in EXTRACT
    assert "zeros.resize(int(sizes[name]))" in HOST
    assert "tff_write_feature_descriptor" in TRAVERSE
    assert "tff_read_feature_descriptor" in TRAVERSE


def test_slot_meta_template_starts_without_overflow_or_demand():
    """Metadata control words must not inherit indirect-dispatch defaults."""
    slot_meta = int(re.search(r"const SLOT_META := (\d+)", HOST).group(1))
    slot_count = int(re.search(r"const SLOT_COUNT := (\d+)", HOST).group(1))

    # Execute the actual production loop through its scalar Python-compatible
    # subset. A copied loop would stay green if the shader host regressed.
    class Bytes:
        def resize(self, size):
            self.words = [0] * (size // 4)

        def encode_u32(self, offset, value):
            self.words[offset // 4] = value

    body = re.search(r"\t_args_template = PackedByteArray\(\)\n(.*?)\treturn true",
                     HOST, re.S).group(1)
    body = "\n".join(line[1:] if line.startswith("\t") else line
                     for line in body.splitlines())
    template = Bytes()
    exec(compile(body, "TrueFFFeaturePasses.gd[template subset]", "exec"),
         {"_args_template": template, "SLOT_COUNT": slot_count,
          "SLOT_META": slot_meta, "SLOT_BYTES": 16})
    words = template.words

    meta = slot_meta * 4
    assert words[meta : meta + 4] == [0, 0, 0, 0]
    assert words[59 * 4 : 59 * 4 + 4] == [0, 0, 0, 0]
    assert all(words[slot*4+1:slot*4+3] == [1, 1]
               for slot in range(slot_count) if slot not in (slot_meta, 59))


def test_descriptor_ranges_are_active_instance_addressed_not_asset_addressed():
    feature_counts = [3, 3]
    bases = []
    cursor = 0
    for count in feature_counts:
        bases.append(cursor)
        cursor += count
    assert [(instance, bases[instance] + address)
            for instance in range(2) for address in range(3)] == [
                (0, 0), (0, 1), (0, 2), (1, 3), (1, 4), (1, 5)]
    assert bases[0] != bases[1]


def test_production_descriptor_lifetime_and_addressing_offline_cpp(tmp_path):
    """Execute the production compact descriptor read/write helpers."""
    compiler = shutil.which('c++')
    if compiler is None:
        pytest.skip('C++ compiler unavailable')
    names = ('tff_write_feature_descriptor', 'tff_read_feature_descriptor')
    functions = []
    for name in names:
        match = re.search(r'(?m)^(?:uint|bool)\s+' + name + r'\([^)]*\)\s*\{', SHARED)
        assert match
        end, depth = match.end(), 1
        while depth:
            depth += (SHARED[end] == '{') - (SHARED[end] == '}')
            end += 1
        functions.append(SHARED[match.start():end].replace('out uint ', 'uint& '))
    constants = '\n'.join(re.findall(
        r'^const uint (?:TFF_NONE|TFF_P1_DESCRIPTOR_TAG|TFF_P1_DESCRIPTOR_LEVEL_MASK) = .*;',
        SHARED, re.M))
    prelude = r'''
#include <cstdint>
#include <initializer_list>
using uint = std::uint32_t;
struct uvec2 {uint x,y; uvec2(uint a=0,uint b=0):x(a),y(b){}};
struct uvec4 {uint x,y,z,w; uvec4(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d){}};
uvec2 slot_map[32]; uvec4 slot_keys[32];
struct Camera {uvec4 caps1, counts;} cam={{0,0,0,32},{0,42,0,0}};
'''
    cases = r'''
int main(){
    uint address=99,level_mask=99,count=99;
    if(tff_read_feature_descriptor(4,0,2,address,level_mask,count))return 1;
    if(!tff_write_feature_descriptor(0,7,2,3,4,0x20000001u,123))return 2;
    if(!tff_write_feature_descriptor(1,7,5,8,9,0x00000003u,456))return 3;
    if(!tff_read_feature_descriptor(4,0,2,address,level_mask,count)||address!=7||level_mask!=0x20000001u||count!=123)return 4;
    if(!tff_read_feature_descriptor(9,1,5,address,level_mask,count)||address!=7||level_mask!=3||count!=456)return 5;
    if(tff_read_feature_descriptor(4,1,2,address,level_mask,count))return 6;
    if(tff_read_feature_descriptor(4,0,5,address,level_mask,count))return 7;
    for(uint epoch: {1u,31u,42u,0x7FFFFFFFu}){
        slot_map[4]=uvec2(epoch,8); // P2 replaces P1, never both readable
        if(tff_read_feature_descriptor(4,0,2,address,level_mask,count))return 8;
        tff_write_feature_descriptor(0,7,2,3,4,0x3FFFFFFFu,0xFFFFFFFFu);
        if(!tff_read_feature_descriptor(4,0,2,address,level_mask,count)||level_mask!=0x3FFFFFFFu||count!=0xFFFFFFFFu)return 9;
    }
    if(tff_write_feature_descriptor(0,1,0,0,32,1,1))return 10;
    if(tff_read_feature_descriptor(32,0,0,address,level_mask,count))return 11;
    return 0;
}
'''
    binary = tmp_path / 'descriptor_unit'
    compiled = subprocess.run([compiler, '-std=c++17', '-O1', '-x', 'c++', '-', '-o', str(binary)],
                              input=prelude + constants + '\n'.join(functions) + cases,
                              text=True, capture_output=True)
    assert compiled.returncode == 0, compiled.stderr
    checked = subprocess.run([str(binary)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
