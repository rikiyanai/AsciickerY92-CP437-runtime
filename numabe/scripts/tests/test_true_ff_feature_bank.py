"""FL-4907 BANK: True FF world feature bank ABI, builder and loader invariants.

Offline only (no Godot): the GDScript ABI owner, the GLSL mirror (compiled and
reflected by glslangValidator) and the Python builder must agree byte for
byte; the builder's trees must satisfy the topology rules of
DETAILED_SPEC_FIVE_AREAS_20260930 section 1 and section 4.
"""
import math
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fl4895_glsl_check as glsl  # noqa: E402
import true_ff_feature_bank as bank  # noqa: E402

ABI = bank.ABI
FEATURES = ROOT / "godot_project/render/ascii/features"
GLSLINC = FEATURES / "true_ff_feature_abi.glslinc"
LOADER = FEATURES / "TrueFFFeatureBank.gd"
WORLD_MESH = ROOT / "godot_project/assets/world_mesh"


def test_static_source_records_follow_owner_revisions_not_frames():
    """Execute the production scalar cache method; no Godot runtime claim."""
    source = LOADER.read_text()
    body = re.search(r"^func _sync_static_records\(.*?\n(.*?)(?=^func )",
                     source, re.M | re.S).group(1)
    body = re.sub(r"\bvar (\w+)(?:: [A-Za-z\[\]]+)?\s*=", r"\1 =", body)
    body = body.replace("String(", "str(").replace(".duplicate()", ".copy()")
    body = body.replace("content_hash.is_empty()", "not content_hash")
    body = body.replace(" = true", " = True")
    state = {"_static_record_key": [], "_static_records": []}
    code = ('def sync(scene_db):\n'
            '\tglobal _static_record_key, _static_records\n' + body)
    exec(compile(code, 'TrueFFFeatureBank.gd[static cache subset]', 'exec'), state)

    class Scene:
        owner = 10
        scene_revision = 1
        assets_revision = 1
        reads = 0
        hashes = 0
        rows = [{"asset_key": "mesh", "semantic_object_id": 21}]

        def get_instance_id(self): return self.owner
        # FL-4907 G6: the single residency generation advances with registry or assets.
        def true_ff_static_residency_generation(self):
            return self.scene_revision * 1000 + self.assets_revision
        def instance_count(self): return len(self.rows)
        def get_static_instance(self, i):
            self.reads += 1
            return self.rows[i]
        def static_asset_content_hash(self, key):
            self.hashes += 1
            return key + str(self.assets_revision)

    scene = Scene()
    sync = state['sync']
    sync(scene)
    retained = state['_static_records'][0]
    for _ in range(20):
        sync(scene)
        assert state['_static_records'][0] is retained
    assert (scene.reads, scene.hashes) == (1, 1)
    assert 'source_kind' not in scene.rows[0]  # cache never annotates scene rows
    for field in ('scene_revision', 'assets_revision', 'owner'):
        setattr(scene, field, getattr(scene, field) + 1)
        sync(scene)
        assert state['_static_records'][0] is not retained
        retained = state['_static_records'][0]
    assert (scene.reads, scene.hashes) == (4, 4)
    assert retained['content_hash'] == 'mesh2'
    scene.rows = []
    scene.scene_revision += 1
    sync(scene)
    assert state['_static_records'] == []
    # Residency preparation borrows cached rows; frame execution has no record loop.
    prepare_body = source.split('func prepare_residency(', 1)[1].split('\nfunc ', 1)[0]
    assert '_sync_static_records(scene_db)' in prepare_body
    assert 'get_active_instance(' not in prepare_body
    release = source.split('func release()', 1)[1]
    assert '_static_record_key = []' in release
    assert '_static_records = []' in release
NAUSICAA = WORLD_MESH / "nausicaa_airframe_faces.json"
PIETA = WORLD_MESH / "pieta_statue_mossy_faces.json"
F32_TOL = 1e-5

PROBE = """#[compute]
#version 450
layout(local_size_x = 64) in;
#include "res://render/ascii/features/true_ff_feature_abi.glslinc"
layout(set = 0, binding = 0, std430) readonly buffer Nodes { TffFeatureNode nodes[]; } bn;
layout(set = 0, binding = 1, std430) readonly buffer Features { TffFeatureRecord records[]; } bf;
layout(set = 0, binding = 2, std430) readonly buffer Faces { TffFaceRecord faces[]; } bc;
layout(set = 0, binding = 3, std430) readonly buffer Instances { TffFeatureInstance rows[]; } bi;
layout(set = 0, binding = 4, std430) writeonly buffer Out { vec4 values[]; } probe;
layout(push_constant, std430) uniform Params { uvec4 counts; } pc;
void main() {
    uint i = gl_GlobalInvocationID.x;
    if (i >= pc.counts.x) return;
    TffFeatureInstance inst = bi.rows[0];
    TffFeatureRecord r = bf.records[inst.refs.y + i];
    TffFeatureNode n = bn.nodes[inst.refs.x];
    uvec4 f = bc.faces[inst.refs.z].edges;
    probe.values[i] = vec4(tff_oct_decode(r.normals.x) * n.sphere.w + n.cone.xyz,
        float(tff_instance_node_count(inst) + tff_instance_feature_count(inst) + f.w + n.links.x + n.meta.x)
        + (tff_is_bypass_class(r.ident.x) ? 1.0 : 0.0));
}
"""


# ------------------------------------------------------------------ helpers
def glsl_constants():
    text = GLSLINC.read_text(encoding="utf-8")
    return {m.group(1): int(m.group(2), 0) for m in re.finditer(r"const uint (\w+) = (0x[0-9A-Fa-f]+|\d+)u;", text)}


def decode(data):
    d = bank.decode_file(data)
    nodes = [struct.unpack_from("<4f4f4I4I", d["nodes"], i) for i in range(0, len(d["nodes"]), 64)]
    feats = [struct.unpack_from("<4f4f4I4I", d["features"], i) for i in range(0, len(d["features"]), 64)]
    faces = [struct.unpack_from("<4I", d["faces"], i) for i in range(0, len(d["faces"]), 16)]
    return d["header"], nodes, feats, faces


def faces_doc(path):
    return next(bank.faces_sources([path]))


def mesh_file(src):
    b = bank.build_source(src)
    return bank.encode_file(b, [src["source"]]), b


def quad_rows(corners, mat=0):
    """One face row from 3 (triangle) or 4 corners."""
    if len(corners) == 3:
        corners = list(corners) + [corners[2]]
    out = []
    for k, c in enumerate(corners):
        out += [c[0], c[1], c[2], float(mat) if k == 0 else 0.0]
    return out


def hinge(angle_deg, mat_b=0):
    """Two triangles sharing edge x-axis; dihedral between normals = angle."""
    a = math.radians(angle_deg)
    rows = quad_rows([(0, 0, 0), (1, 0, 0), (0.5, 0, -1)])
    rows += quad_rows([(1, 0, 0), (0, 0, 0), (0.5, math.sin(a), math.cos(a))], mat_b)
    return rows, 2


@pytest.fixture(scope="module")
def pieta():
    data, b = mesh_file(faces_doc(PIETA))
    return decode(data)


@pytest.fixture(scope="module")
def nausicaa():
    data, b = mesh_file(faces_doc(NAUSICAA))
    return decode(data)


# ------------------------------------------------------------------ ABI
def test_assembly_preserves_source_owned_identity_through_spatial_ordering():
    # FL-4907: assembly owns spatial ordering, not source correspondence.
    # Its two production callers provide different identity policies.
    rows = []
    # Alternate opposite regions so leaf ordering cannot preserve source
    # order by accident; exceed the production leaf threshold deliberately.
    count = ABI["LEAF_MAX_FEATURES"] + 2
    for index in range(count):
        x = (-1 if index % 2 else 1) * (10 + index)
        rows += quad_rows([(x, 0, 0), (x + .5, 0, 0), (x, .5, 0)])
    triangles = bank.triangles_from_rows(rows, count)
    features, edges, _ = bank.mesh_features(triangles)
    bank.build_chains(features)
    expected = {}
    for feature in features:
        feature.sid = 100 + feature.local
        expected[feature.sid] = feature.chain
    result = bank.assemble(features, edges, [t[1] for t in triangles])
    emitted_ids = [struct.unpack_from("<I", result["features"], i + 56)[0]
                   for i in range(0, len(result["features"]), 64)]
    assert result["node_count"] > 1
    assert emitted_ids != list(expected), "fixture did not reorder features"
    actual = {struct.unpack_from("<I", result["features"], i + 56)[0]:
              struct.unpack_from("<I", result["features"], i + 52)[0]
              for i in range(0, len(result["features"]), 64)}
    assert actual == expected


def test_actor_address_certificate_survives_shared_file_serializer():
    compiled = bank.build_mesh_bank(quad_rows([(0, 0, 0), (1, 0, 0), (0, 1, 0)]), 1)
    compiled["actor_feature_identity"] = "actor_test_identity"
    compiled["canonical_address_signature"] = "ab" * 32
    header = bank.decode_file(bank.encode_file(compiled, []))["header"]
    assert header["actor_feature_identity"] == compiled["actor_feature_identity"]
    assert header["canonical_address_signature"] == compiled["canonical_address_signature"]


def test_actor_namespace_uses_certified_topology_not_selected_pose():
    # Offline integration guard, not GDScript execution. The production
    # compiler's behavioral tests establish address equality across poses.
    source = LOADER.read_text()
    # The slot identity and the namespace share one exact source tuple.
    owner = source.split("func _identity_key(", 1)[1].split("\n\nfunc ", 1)[0]
    assert 'if String(rec.get("source_kind", "")) == "actor" or rec.has("topology_identity"):' in owner
    assert 'geometry_identity = String(rec["topology_identity"])' in owner
    assert 'rec.get("source_part"), geometry_identity])' in owner
    assert '"content_hash"]])' not in owner
    assert "return _namespace_for_key(_identity_key(rec))" in source.split("func _source_namespace(", 1)[1]
    upload = source.split("func _pack_dirty_sources(", 1)[1].split("\n\nfunc ", 1)[0]
    assert 'slice["actor_certificate"]' in upload
    assert 'for field in ActorFeatures.BANK_CERTIFICATE_FIELDS:' in upload
    assert 'rec[field] != certificate[field]' in upload
    assert upload.index('"actor_address_layout"') < upload.index("_namespace_for_key(ident)")


def test_abi_constants_match_between_gdscript_glsl_and_python():
    g = glsl_constants()
    pairs = {
        "NODE_BYTES": "TFF_FEATURE_NODE_BYTES", "FEATURE_BYTES": "TFF_FEATURE_RECORD_BYTES",
        "FACE_BYTES": "TFF_FEATURE_FACE_BYTES", "INSTANCE_BYTES": "TFF_FEATURE_INSTANCE_BYTES",
        "NODE_FLAG_BYPASS": "TFF_NODE_FLAG_BYPASS", "NODE_FLAG_LEAF": "TFF_NODE_FLAG_LEAF",
        "FEATURE_FLAG_MATERIAL_MASK": "TFF_FEATURE_FLAG_MATERIAL_MASK",
        "FEATURE_FLAG_OPEN": "TFF_FEATURE_FLAG_OPEN", "LEAF_MAX_FEATURES": "TFF_LEAF_MAX_FEATURES",
    }
    for name in ("SMOOTH_CANDIDATE", "CREASE", "MATERIAL_BOUNDARY", "OPEN_BOUNDARY", "TWO_SIDED",
                 "AUTHORED_STROKE", "TERRAIN_RELIEF", "SHORELINE"):
        pairs[f"CLASS_{name}"] = f"TFF_CLASS_{name}"
    for gd, gl in pairs.items():
        assert ABI[gd] == g[gl], (gd, ABI[gd], g[gl])
    assert [ABI[f"CLASS_{n}"] for n in ("SMOOTH_CANDIDATE", "CREASE", "MATERIAL_BOUNDARY", "OPEN_BOUNDARY",
                                        "TWO_SIDED", "AUTHORED_STROKE", "TERRAIN_RELIEF", "SHORELINE")] == list(range(1, 9))
    # Python record packing uses the same sizes.
    assert struct.calcsize("<4f4f4I4I") == ABI["NODE_BYTES"] == ABI["FEATURE_BYTES"] == 64
    assert struct.calcsize("<4I") == ABI["FACE_BYTES"] == 16
    assert ABI["INSTANCE_BYTES"] == 144
    assert (ABI["NODE_OFF_SPHERE"], ABI["NODE_OFF_CONE"], ABI["NODE_OFF_LINKS"], ABI["NODE_OFF_META"]) == (0, 16, 32, 48)
    assert (ABI["FEATURE_OFF_P0"], ABI["FEATURE_OFF_P1"], ABI["FEATURE_OFF_NORMALS"], ABI["FEATURE_OFF_IDENT"]) == (0, 16, 32, 48)
    assert (ABI["INSTANCE_OFF_LOCAL_TO_WORLD"], ABI["INSTANCE_OFF_REFS"],
            ABI["INSTANCE_OFF_DEFORM"], ABI["INSTANCE_OFF_MATERIALS"],
            ABI["INSTANCE_OFF_IDENTITY"], ABI["INSTANCE_OFF_PAINT"]) == (0, 64, 80, 96, 112, 128)


def test_bypass_class_set_is_identical_in_all_three_owners():
    # Every fixed class bypasses the cone reject, terrain relief and shoreline
    # included (FL-4907 INTEGRATE_LANE_FEATURE_RENDERER_WIRED_20260930).
    names = {"CREASE", "MATERIAL_BOUNDARY", "OPEN_BOUNDARY", "TWO_SIDED", "AUTHORED_STROKE",
             "TERRAIN_RELIEF", "SHORELINE"}
    assert bank.ABI["BYPASS_CLASSES"] == {ABI[f"CLASS_{n}"] for n in names}
    gd = (FEATURES / "TrueFFFeatureABI.gd").read_text(encoding="utf-8")
    gd_mask = gd[gd.index("const BYPASS_CLASS_MASK"):gd.index("# Crease owner")]
    assert set(re.findall(r"\bCLASS_(\w+)", gd_mask)) == names
    inc = GLSLINC.read_text(encoding="utf-8")
    gl_mask = inc[inc.index("TFF_BYPASS_CLASS_MASK"):inc.index("// 64 B. sphere")]
    assert set(re.findall(r"TFF_CLASS_(\w+)", gl_mask)) == names


@pytest.mark.skipif(shutil.which("glslangValidator") is None, reason="glslangValidator missing")
def test_glsl_mirror_compiles_and_reflects_the_abi_offsets(tmp_path):
    probe = tmp_path / "abi_probe.glsl"
    probe.write_text(PROBE, encoding="utf-8")
    assert glsl.main([str(probe)]) == 0
    comp = tmp_path / "abi_probe.comp"
    comp.write_text("#version 450\n" + glsl.expand(probe), encoding="utf-8")
    out = subprocess.run(["glslangValidator", "-V", "-S", "comp", "-q", "-o", str(tmp_path / "x.spv"), str(comp)],
                         capture_output=True, text=True, check=True).stdout
    refl = {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"^(\w+\.\w+\.\w+): offset (\d+),.*topLevelArrayStride (\d+)", out, re.M)}
    expect = {
        "Nodes.nodes.sphere": (ABI["NODE_OFF_SPHERE"], ABI["NODE_BYTES"]),
        "Nodes.nodes.cone": (ABI["NODE_OFF_CONE"], ABI["NODE_BYTES"]),
        "Nodes.nodes.links": (ABI["NODE_OFF_LINKS"], ABI["NODE_BYTES"]),
        "Nodes.nodes.meta": (ABI["NODE_OFF_META"], ABI["NODE_BYTES"]),
        "Features.records.p0": (ABI["FEATURE_OFF_P0"], ABI["FEATURE_BYTES"]),
        "Features.records.p1": (ABI["FEATURE_OFF_P1"], ABI["FEATURE_BYTES"]),
        "Features.records.normals": (ABI["FEATURE_OFF_NORMALS"], ABI["FEATURE_BYTES"]),
        "Features.records.ident": (ABI["FEATURE_OFF_IDENT"], ABI["FEATURE_BYTES"]),
        "Faces.faces.edges": (ABI["FACE_OFF_EDGES"], ABI["FACE_BYTES"]),
        "Instances.rows.local_to_world": (ABI["INSTANCE_OFF_LOCAL_TO_WORLD"], ABI["INSTANCE_BYTES"]),
        "Instances.rows.refs": (ABI["INSTANCE_OFF_REFS"], ABI["INSTANCE_BYTES"]),
        "Instances.rows.deform": (ABI["INSTANCE_OFF_DEFORM"], ABI["INSTANCE_BYTES"]),
        "Instances.rows.materials": (ABI["INSTANCE_OFF_MATERIALS"], ABI["INSTANCE_BYTES"]),
        "Instances.rows.identity": (ABI["INSTANCE_OFF_IDENTITY"], ABI["INSTANCE_BYTES"]),
    }
    for name, value in expect.items():
        assert refl.get(name) == value, (name, refl.get(name), value)


def test_metal_rules_set_zero_and_no_runtime_length():
    for path in (GLSLINC,):
        text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
        assert not re.search(r"\.\s*length\s*\(", text)
        assert not re.search(r"\bset\s*=\s*[1-9]", text)
    loader = LOADER.read_text(encoding="utf-8")
    assert "uniform_set_create" not in loader  # passes own set 0


def test_oct_encoding_round_trip_is_tight():
    worst = 0.0
    for i in range(2000):
        z = 1.0 - 2.0 * (i + 0.5) / 2000
        r = math.sqrt(max(0.0, 1.0 - z * z))
        phi = i * 2.399963
        n = (r * math.cos(phi), r * math.sin(phi), z)
        d = bank.oct_decode(bank.oct_encode(n))
        worst = max(worst, math.acos(max(-1.0, min(1.0, bank.dot(n, d)))))
    assert worst < 1e-3


# ------------------------------------------------------------------ crease owner
def test_crease_threshold_is_read_from_the_single_owner():
    structure = bank._gd_constants(bank.STRUCTURE_ABI_GD)["DEFAULT_CREASE_COS"]
    assert ABI["CREASE_COS"] == structure
    assert abs(structure - math.cos(math.radians(32.0))) < 1e-12
    gd = (FEATURES / "TrueFFFeatureABI.gd").read_text(encoding="utf-8")
    assert "TrueFFStructureABIScript.DEFAULT_CREASE_COS" in gd
    for text in (gd, (ROOT / "scripts/true_ff_feature_bank.py").read_text(encoding="utf-8")):
        assert not re.search(r"0\.848|radians\(\s*32", text)


@pytest.mark.parametrize("angle,expected", [(31.0, "SMOOTH_CANDIDATE"), (33.0, "CREASE"), (90.0, "CREASE")])
def test_crease_classification_uses_32_degree_owner(angle, expected):
    rows, fc = hinge(angle)
    feats, _, _ = bank.mesh_features(bank.triangles_from_rows(rows, fc))
    shared = [f for f in feats if not f.open]
    assert len(shared) == 1 and shared[0].cls == ABI[f"CLASS_{expected}"]
    assert all(f.cls == ABI["CLASS_OPEN_BOUNDARY"] for f in feats if f.open)


def test_material_boundary_and_two_sided_classes():
    rows, fc = hinge(5.0, mat_b=7)
    feats, _, _ = bank.mesh_features(bank.triangles_from_rows(rows, fc))
    assert [f.cls for f in feats if not f.open] == [ABI["CLASS_MATERIAL_BOUNDARY"]]
    rows = quad_rows([(0, 0, 0), (1, 0, 0), (0, 0, 1)]) + quad_rows([(1, 0, 0), (0, 0, 0), (0, 0, 1)])
    feats, _, _ = bank.mesh_features(bank.triangles_from_rows(rows, 2))
    assert {f.cls for f in feats} == {ABI["CLASS_TWO_SIDED"]}


def test_edges_follow_the_baked_edge_table_order():
    import json
    doc = json.loads(NAUSICAA.read_text(encoding="utf-8"))
    tris = bank.triangles_from_rows(doc["rows"], doc["face_count"])
    feats, _, _ = bank.mesh_features(tris)
    floats = doc["edge_rows"]["floats"]
    assert len(feats) == doc["edge_table"]["count"]
    for f in feats:
        row = floats[16 * f.local:16 * f.local + 16]
        assert {bank._key(row[0:3]), bank._key(row[4:7])} == {f.k0, f.k1}


# ------------------------------------------------------------------ octree
def _check_tree(header, nodes, feats, faces, octree=True):
    n_feat = len(feats)
    leaves = [n for n in nodes if n[12] & ABI["NODE_FLAG_LEAF"]]
    seen = [0] * n_feat
    for n in leaves:
        for i in range(n[10], n[10] + n[11]):
            seen[i] += 1
        if octree:
            assert n[11] <= ABI["LEAF_MAX_FEATURES"] or n[13] >= bank.MAX_DEPTH
    assert all(s == 1 for s in seen), "every feature in exactly one leaf"
    for idx, n in enumerate(nodes):
        centre, r = n[0:3], n[3]
        first_child, child_count, ff, fc = n[8:12]
        flags, level = n[12], n[13]
        assert bool(flags & ABI["NODE_FLAG_LEAF"]) == (child_count == 0)
        if child_count:
            kids = nodes[first_child:first_child + child_count]
            assert kids[0][10] == ff and kids[-1][10] + kids[-1][11] == ff + fc
            for k in kids:
                assert k[13] == level + 1
                assert bank.norm(bank.sub(k[0:3], centre)) + k[3] <= r * (1 + F32_TOL) + F32_TOL
        sub = feats[ff:ff + fc]
        for f in sub:
            for p in (f[0:3], f[4:7]):
                assert bank.norm(bank.sub(p, centre)) <= r * (1 + F32_TOL) + F32_TOL
        classes = {f[12] for f in sub}
        assert bool(flags & ABI["NODE_FLAG_BYPASS"]) == bool(classes & ABI["BYPASS_CLASSES"])
        axis, half = n[4:7], n[7]
        if half >= 0.0 and octree:
            assert half < math.pi / 2
            a = bank.unit(axis)
            for f in sub:
                for packed in f[8:12]:
                    v = bank.oct_decode(packed)
                    assert math.acos(max(-1.0, min(1.0, bank.dot(a, v)))) <= half + 1e-6
    return leaves


def test_octree_invariants_on_the_pieta(pieta):
    header, nodes, feats, faces = pieta
    leaves = _check_tree(header, nodes, feats, faces)
    assert header["mesh_edge_count"] == 16375
    assert len(leaves) > 1 and header["max_leaf_features"] <= ABI["LEAF_MAX_FEATURES"]
    # Faces: grouped per leaf, edges valid, each face edge0 inside its leaf.
    covered = 0
    for leaf in leaves:
        for fi in range(leaf[14], leaf[14] + leaf[15]):
            e = faces[fi]
            assert all(0 <= x < len(feats) for x in e[:3])
            assert leaf[10] <= e[0] < leaf[10] + leaf[11]
        covered += leaf[15]
    assert covered == len(faces) == header["triangle_count"]
    # Authored figure/fold strokes are producer-owned retained banks now; a
    # static mesh build contains only classes derived from its own geometry.
    assert ABI["CLASS_AUTHORED_STROKE"] not in {f[12] for f in feats}


def test_cone_undefined_only_when_not_provable(nausicaa):
    header, nodes, feats, faces = nausicaa
    _check_tree(header, nodes, feats, faces)
    assert any(n[7] >= 0.0 for n in nodes), "some cone must be defined"


# ------------------------------------------------------------------ chains
def test_chain_arc_is_monotone_and_continuous(pieta):
    _, _, feats, _ = pieta
    chains = {}
    for f in feats:
        assert f[7] >= f[3]
        assert abs((f[7] - f[3]) - bank.norm(bank.sub(f[4:7], f[0:3]))) <= 1e-3 * (1 + f[7])
        chains.setdefault(f[13], []).append(f)
    multi = 0
    for run in chains.values():
        classes = {f[12] for f in run}
        assert len(classes) == 1, "a chain holds one class"
        run.sort(key=lambda f: f[3])
        assert run[0][3] == 0.0
        for a, b in zip(run, run[1:]):
            assert a[7] == b[3], "arc continuous"
            assert bank._key(a[4:7]) == bank._key(b[0:3]) or bank.norm(bank.sub(a[4:7], b[0:3])) < 1e-4
        multi += len(run) > 1
    assert multi > 0


# ------------------------------------------------------------------ identity
def test_stable_ids_are_fnv1a_of_digest_and_local_index():
    def fnv(data):
        h = 0x811C9DC5
        for b in data:
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
        return h
    digest = "ab" * 32
    assert bank.stable_id(digest, 7) == fnv(bytes.fromhex(digest) + struct.pack("<I", 7))
    assert fnv(b"") == 0x811C9DC5 and fnv(b"a") == 0xE40C292C  # FNV-1a 32 reference vectors


def test_build_is_deterministic_and_content_keyed():
    src = faces_doc(NAUSICAA)
    a, ba = mesh_file(src)
    b, bb = mesh_file(faces_doc(NAUSICAA))
    assert a == b
    assert ba["content_hash"] == bank.content_hash_of(src["rows32"], src["face_count"])
    rows = list(src["rows"])
    rows[0] += 0.25
    moved = bank.build_mesh_bank(rows, src["face_count"])
    assert moved["content_hash"] != ba["content_hash"]
    ids_a = {struct.unpack_from("<I", ba["features"], i + 56)[0] for i in range(0, len(ba["features"]), 64)}
    ids_b = {struct.unpack_from("<I", moved["features"], i + 56)[0] for i in range(0, len(moved["features"]), 64)}
    assert not ids_a & ids_b
    header = decode(a)[0]
    assert header["content_hash"] == ba["content_hash"]


def test_committed_banks_match_a_fresh_build():
    out = bank.DEFAULT_OUT
    if not (out / "index.v1.json").exists():
        pytest.skip("no committed bank directory")
    for path in (NAUSICAA, PIETA):
        src = faces_doc(path)
        data, b = mesh_file(src)
        committed = (out / f"{b['content_hash']}.v1.bin").read_bytes()
        assert decode(committed)[1:] == decode(data)[1:]


# ------------------------------------------------------------------ terrain
def _terrain_nodes(data):
    decoded = bank.decode_file(data)
    nodes = [struct.unpack_from("<4f4f4I4I", decoded["nodes"], i)
             for i in range(0, len(decoded["nodes"]), 64)]
    classes = list(struct.unpack("<%dH" % (len(decoded["terrain_classes"]) // 2), decoded["terrain_classes"]))
    return decoded["header"], nodes, classes


def test_terrain_compact_grid_bakes_topology_fixed_classes_and_shoreline():
    W, H = 4, 2
    heights = [0.0] * ((W + 1) * (H + 1))
    for z in range(H + 1):
        for x in range(W + 1):
            heights[z * (W + 1) + x] = 4.0 if x == 2 else 0.0
    mats = [0, 0, 9, 9, 0, 0, 9, 9]
    banked, _ = bank.build_terrain_bank(heights, (W, H), (0, 0), 1, 1, mats)
    data = bank.encode_file(banked, ["synthetic"])
    header, nodes, classes = _terrain_nodes(data)
    assert header["encoding"] == "implicit_grid_v1"
    assert header["leaf_cells"] == 16 and header["terrain_class_count"] == (W + 1) * (H + 1)
    assert header["feature_count"] == header["face_count"] == 0
    assert len(banked["features"]) == len(banked["faces"]) == 0
    assert all(section["offset"] % 16 == 0 for section in header["sections"].values())
    assert header["sections"]["terrain_classes"]["stride"] == 2
    assert any(((value >> 0) & 0xF) == ABI["CLASS_OPEN_BOUNDARY"] for value in classes)
    assert any(((value >> 4) & 0xF) == ABI["CLASS_OPEN_BOUNDARY"] for value in classes)
    assert any(any(((value >> shift) & 0xF) == ABI["CLASS_TERRAIN_RELIEF"] for shift in (0, 4, 8))
               for value in classes)
    assert any(any(((value >> shift) & 0xF) == ABI["CLASS_MATERIAL_BOUNDARY"] for shift in (0, 4, 8))
               for value in classes)
    assert any(value & (bank.TERRAIN_SHORELINE_BIT_TRI0 | bank.TERRAIN_SHORELINE_BIT_TRI1) for value in classes)
    assert nodes[0][12] & bank.TERRAIN_NODE_FLAG_IMPLICIT
    assert nodes[0][12] & ABI["NODE_FLAG_BYPASS"]


def test_terrain_edge_topology_diagonal_adjacency_and_stable_readmit_ids():
    dims = (2, 2)
    heights = [0.0] * 9
    records = bank.terrain_reference_edges(heights, dims, (0, 0), 1, -1)
    edges = {r["address"]: r for r in records if "orientation" in r}
    assert len(edges) == 2 * 3 + 3 * 2 + 2 * 2  # valid H/V/D slots
    assert bank.terrain_edge_address(0, 0, bank.TERRAIN_EDGE_H, dims) == 0
    assert bank.terrain_edge_address(0, 0, bank.TERRAIN_EDGE_V, dims) == 1
    assert bank.terrain_edge_address(0, 0, bank.TERRAIN_EDGE_D, dims) == 2
    assert bank.terrain_shoreline_address(0, 0, 0, dims) == 3 * 9
    digest = bank.terrain_content_hash(heights, dims, (0, 0), 1, -1, [0] * 4)
    assert bank.terrain_stable_id(digest, 2) == bank.terrain_stable_id(digest, 2)
    changed = bank.terrain_reference_edges(heights, dims, (0, 0), 1, -1)
    assert [r["stable_id"] for r in records] == [r["stable_id"] for r in changed]


def test_terrain_hierarchy_bounds_and_vertex_normal_cones_are_conservative():
    W, H = 40, 20
    heights = [float((x - 20) * 2 if x >= 20 else 0) for z in range(H + 1) for x in range(W + 1)]
    terrain, root = bank.build_terrain_bank(heights, (W, H), (0, 0), 1, 1)
    data = bank.encode_file(terrain, ["synthetic"])
    header, nodes, classes = _terrain_nodes(data)
    assert len(nodes) == 9  # clipped 4x2 leaf grid under padded side 4
    assert nodes[0][12] & bank.TERRAIN_NODE_FLAG_IMPLICIT
    for obj, raw in zip(bank._bfs(root), nodes):
        x0, z0, x1, z1 = obj.region
        assert ((raw[10] & 0xFFFF), (raw[10] >> 16)) == (x0, z0)
        assert ((raw[11] & 0xFFFF), (raw[11] >> 16)) == (x1 - x0, z1 - z0)
        assert struct.unpack("<f", struct.pack("<I", raw[14]))[0] == obj.height_range[0]
        assert struct.unpack("<f", struct.pack("<I", raw[15]))[0] == obj.height_range[1]
        for child in nodes[raw[8]:raw[8] + raw[9]]:
            assert bank.norm(bank.sub(child[0:3], raw[0:3])) + child[3] <= raw[3] * (1 + F32_TOL) + F32_TOL
        if raw[7] >= 0:
            axis = bank.unit(raw[4:7])
            for normal in obj.region_normals:
                assert math.acos(max(-1, min(1, bank.dot(axis, normal)))) <= raw[7] + 1e-5


def test_terrain_compact_size_is_not_explicit_grid_feature_storage():
    W, H = 128, 96
    heights = [0.0] * ((W + 1) * (H + 1))
    terrain, _ = bank.build_terrain_bank(heights, (W, H), (0, 0), 1, -1)
    encoded = bank.encode_file(terrain, ["synthetic"])
    assert len(terrain["features"]) == len(terrain["faces"]) == 0
    assert len(encoded) < 200_000


def test_terrain_bank_header_preserves_authoritative_origin_topology_and_water():
    heights = [0.0, 1.0, 2.0, 3.0]
    dims = (1, 1)
    origin = (12.5, -7.25)
    cell = 1.0
    water = bank.terrain_water_surface_y()
    terrain, _ = bank.build_terrain_bank(heights, dims, origin, cell, water)
    header, *_ = decode(bank.encode_file(terrain, ["synthetic-terrain"]))
    assert header["kind"] == "terrain"
    assert header["content_hash"] == bank.terrain_content_hash(heights, dims, origin, cell, water)
    assert header["terrain_dims"] == list(dims)
    assert header["terrain_origin"] == list(origin)
    assert header["terrain_cell"] == cell
    assert header["water_level"] == water


def test_terrain_generation_requires_explicit_water_source_fact(tmp_path):
    source = tmp_path / "terrain.json"
    source.write_text(json.dumps({
        "heights": [0.0, 1.0, 2.0, 3.0],
        "dims": [1, 1], "origin": [0.0, 0.0], "cell": 1.0,
        "water_level": bank.terrain_water_surface_y(),
    }), encoding="utf-8")
    output = tmp_path / "bank.v1.bin"
    assert bank.main(["terrain", "--heights", str(source), "--out", str(output)]) == 0
    assert output.exists()
    doc = json.loads(source.read_text(encoding="utf-8"))
    del doc["water_level"]
    source.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(KeyError, match="water_level"):
        bank.main(["terrain", "--heights", str(source), "--out", str(tmp_path / "bad.bin")])


def test_build_route_publishes_terrain_bank_and_index_entry(tmp_path):
    source = tmp_path / "terrain.json"
    source.write_text(json.dumps({
        "heights": [0.0, 1.0, 2.0, 3.0],
        "dims": [1, 1], "origin": [2.0, 4.0], "cell": 1.0,
        "water_level": bank.terrain_water_surface_y(),
    }), encoding="utf-8")
    output = tmp_path / "bank"
    assert bank.main(["build", "--terrain-heights", str(source), "--out", str(output)]) == 0
    index = json.loads((output / "index.v1.json").read_text(encoding="utf-8"))
    assert len(index["banks"]) == 3  # terrain plus retained water/cloud planes
    entries = list(index["banks"].values())
    entry = next(value for value in entries if value["kind"] == "terrain")
    assert entry["kind"] == "terrain"
    assert entry["terrain_dims"] == [1, 1]
    assert entry["terrain_origin"] == [2.0, 4.0]
    assert {value.get("plane") for value in entries if value["kind"] == "world_plane"} \
        == {"water", "cloud"}


def test_runtime_bank_declares_revision_aware_terrain_connection():
    loader = LOADER.read_text(encoding="utf-8")
    assert "heightfield_revision" in loader
    assert "water_revision" in loader
    assert 'source.has("height_revision")' in loader
    assert "terrain_feature_bank_facts" in loader
    assert "get_water_descriptor" in loader
    assert "terrain_world_baked" in loader


def test_terrain_bank_key_is_owned_by_geometry_publication_not_frame_preparation():
    """Source ownership guard only; it does not execute GDScript or prove fps."""
    root = LOADER.parents[4]
    store = (root / "godot_project/render/scene/geometry/terrain/TerrainWaterGeometryStore.gd").read_text()
    facade = (root / "godot_project/render/scene/SceneGeometryDB.gd").read_text()
    loader = LOADER.read_text()
    setter = _gd_function(store, "set_heightfield_descriptor")
    getter = _gd_function(store, "terrain_feature_bank_facts")
    key = _gd_function(store, "_feature_bank_content_hash")
    prepare = _gd_function(loader, "_sync_terrain")
    source = _gd_function(loader, "_terrain_source_facts")
    assert setter.index("if signature == _heightfield_signature:") < setter.index(
        "_feature_bank_content_hash(origin_world_xz, cell_dims, signature)"
    )
    assert 'signature["corner_height_image"]["data"]' in key
    assert 'signature["material_id_image"]["data"]' in key
    assert "header.resize(24)" in key
    assert "header.encode_float(20, WATER_SURFACE_Y)" in key
    assert "hasher" not in prepare + source + getter
    assert "get_data()" not in prepare + source + getter + key
    assert "get_heightfield_descriptor" not in source
    assert "_terrain_water_store.terrain_feature_bank_facts()" in facade
    assert 'var digest: String = source["content_hash"]' in prepare


def test_water_publication_cannot_withhold_an_already_owned_terrain_identity():
    """Source guard for independent families, not a water-coverage success test."""
    loader = LOADER.read_text()
    prepare = _gd_function(loader, "_sync_terrain")
    water = _gd_function(loader, "_terrain_water_facts")
    assert 'var water_level: float = float(source["water_level"])' in prepare
    assert "if water.is_empty()" not in prepare
    assert "water_surface_y_missing_for_terrain_key" not in loader
    assert '"omitted": "water_surface_y_missing"' in water
    assert '"omitted": "water_surface_y_disagrees_with_terrain"' in water
    assert '{"terrain:water": _terrain_water_omission}' in prepare


def test_hierarchy_live_membership_updates_do_not_rescan_static_sources():
    """Source ownership guard; GPU address correctness still needs execution."""
    loader = LOADER.read_text()
    refresh = _gd_function(loader, "_refresh_hierarchy_bindings")
    commit = _gd_function(loader, "_commit_slot")
    withdraw = _gd_function(loader, "_withdraw_slot")
    assert "_slot_keys" not in refresh and "_desired" not in refresh
    assert "for leaf_value in _static_leaf_writes:" in refresh
    assert "for root_value in _hierarchy_root_writes:" in refresh
    assert "_index_hierarchy_slot(ident, slot)" in commit
    assert "_remove_hierarchy_slot(ident)" in withdraw
    assert "_index_hierarchy_slot(moved, slot)" in withdraw
    remove = _gd_function(loader, "_remove_hierarchy_slot")
    assert "_hierarchy_root_index[moved] = index" in remove
    assert "_hierarchy_root_writes.erase(last)" in remove
    reset = _gd_function(loader, "_reset_source_table")
    for retained in ("_static_slot_keys", "_static_registry_slots",
                     "_static_leaf_of_registry", "_hierarchy_root_index",
                     "_hierarchy_root_keys", "_hierarchy_root_writes"):
        assert retained + ".clear()" in reset


def test_surface_membership_uses_exact_retained_radix_updates():
    """Offline ABI/source guard; does not execute the GDScript mutator."""
    loader = LOADER.read_text()
    flush = _gd_function(loader, "_flush_instance_table")
    upload = _gd_function(loader, "_upload_surface_instances")
    assert "_slot_lookup" not in flush
    assert "lookup.sort()" not in loader
    assert "for slot_value in _surface_metadata_writes:" in upload
    assert "for block_value in _surface_radix_writes:" in upload
    assert "_surface_radix_offset != radix_at" in upload
    remove = _gd_function(loader, "_surface_index_remove")
    assert "_surface_radix_free.append(dead)" in remove
    assert "range(7, 0, -1)" in remove
    source = (LOADER.parent / "true_ff_surface_representatives.glslinc").read_text()
    assert "depth < 8u" in source and "while (low < high)" not in source
    assert "header.y + word / 4u" in source
    # Execute the production byte-selection expression for full-width keys.
    # The mapping must retain every bit, including high-bit object IDs and
    # part IDs that share their lower bytes; no lossy integer hash is allowed.
    expression = _gd_function(loader, "_surface_key_byte").split("return ", 1)[1].strip()
    keys = [(0, 0), (1, 0), (1, 1), (0x80000001, 0),
            (0xFFFFFFFF, 0xFFFFFFFF), (1, 0x01000000), (1, 0x100)]
    for object_id, part in keys:
        actual = bytes(eval(expression, {"__builtins__": {}},
                            {"word": object_id if depth < 4 else part, "depth": depth})
                       for depth in range(8))
        assert actual == struct.pack(">II", object_id, part)


def _gd_function(source, name):
    match = re.search(rf"^(?:static )?func {re.escape(name)}\(.*?"
                      r"(?=^(?:static )?func |\Z)", source, re.M | re.S)
    assert match, name
    return match.group(0)


def test_bank_sync_omits_invalid_sources_by_name_not_the_whole_frame():
    """FL-4907 operator decision 2026-10-02: source-text guard, not a Godot run.

    One malformed producer fact or retained stroke bank is omitted by identity
    and exact reason; it never rejects sync. Only the no-world conditions
    (device, scene DB) stay frame-fatal. Terrain faults degrade to the
    absent-terrain path with a named producer omission (row 8, 2026-10-09).
    """
    loader = LOADER.read_text(encoding="utf-8")
    prepare = _gd_function(loader, "prepare_residency")
    apply = _gd_function(loader, "apply_changes")
    # The old all-or-nothing rejections are gone.
    assert "feature_source_facts_invalid" not in loader
    assert "source_facts_valid" not in apply
    assert 'return _reject("retained_stroke_bank_' not in apply
    # Frame-fatal conditions that mean there is no world to draw remain.
    for fatal in ('return _reject("residency_authority_missing")',):
        assert fatal in prepare
    # Terrain faults must not reject the whole bank; they degrade.
    assert "_producer_omissions[\"terrain\"]" in prepare
    # Per-source validation and named omission (the producer slot-write
    # interface validates only new or changed sources).
    admit = _gd_function(loader, "_admit_fact")
    assert "_source_fact_rejection(source)" in admit
    assert '"retained_stroke_bank_invalid:%s" % content_hash' in admit
    resolve = _gd_function(loader, "_resolve_bank_hash")
    assert '"retained_stroke_bank_identity_conflict:%s" % "|".join(PackedStringArray(labels))' in resolve
    # A poisoned hash never falls through to a disk asset and its sources are
    # omitted by name at pack time; no rows or residency keys dangle.
    assets = _gd_function(loader, "_sync_assets")
    assert "not _bank_providers.has(content_hash) and not _invalid_shared.has(content_hash)" in assets
    pack = _gd_function(loader, "_pack_dirty_sources")
    assert pack.index("_poisoned.get(content_hash") < pack.index("_commit_slot(")
    # N2 (RETAINED_INSTANCE_TABLE_AND_BANK_KEY_20261002): omissions never
    # enter the asset key; there is no per-frame key string at all.
    assert 'key_parts.append("omitted:' not in loader
    assert "_loaded_key" not in loader and "_failed_key" not in loader
    report = _gd_function(loader, "_report_omissions")
    assert "_reported_omissions.has(report)" in report
    assert "push_error(" in report
    assert '"omitted_sources": _omitted_sources.duplicate()' in _gd_function(loader, "receipt")
    release = _gd_function(loader, "release")
    assert "_reset_source_table()" in release
    assert "_key_omissions.clear()" in _gd_function(loader, "_reset_source_table")


def test_bank_identity_conflict_names_every_provider_and_only_that_hash():
    """Source-text guard: every conflicting provider is named and sorted."""
    resolve = _gd_function(LOADER.read_text(encoding="utf-8"), "_resolve_bank_hash")
    assert "labels.sort()" in resolve
    assert resolve.index("labels.sort()") < resolve.index('"|".join(PackedStringArray(labels))')
    assert "for label in labels:" in resolve
    assert "omissions[label] = reason" in resolve
    assert "_dirty_hash(content_hash)" in resolve
    assert "_inline_banks.erase(content_hash)" in resolve


def test_retained_instance_table_writes_only_dirty_slots():
    """FL-4907 RETAINED_INSTANCE_TABLE_AND_BANK_KEY_20261002 (N1/N2/N3):
    source-text guard only; the runtime evidence is the headless probe."""
    loader = LOADER.read_text(encoding="utf-8")
    prepare = _gd_function(loader, "prepare_residency")
    apply = _gd_function(loader, "apply_changes")
    commit = _gd_function(loader, "_commit_publication")
    assert "_commit_publication(scene_db)" in prepare
    assert "_pack_dirty_sources(_prev_deform)" in commit
    assert "_flush_instance_table()" in commit
    assert "_static_records.duplicate()" not in commit
    assert "for key_value in upserts.keys() + removals:" in apply
    assert "if not has_events:" in apply and "_set_desired(key" in apply
    flush = _gd_function(loader, "_flush_instance_table")
    assert "_gpu_write(_instances, at + span.x" in flush
    assert "if _surface_dirty:" in flush
    assert "_upload_instances" not in loader and "_static_row_cache" not in loader
    # N3: namespaces and admission ranges are recycled by residency lifetime.
    release = _gd_function(loader, "_release_recycled")
    assert "_namespace_free.append(source_namespace)" in release
    assert "_block_free(_admission_blocks, node_range.x, node_range.y)" in release
    # N2: residency-keyed static region; producer banks in a separate arena.
    assert "_place_arena_bank(content_hash)" in _gd_function(loader, "_sync_assets")
    assert "_gpu_copy(_asset_buffer(buffer_name), grown, 0, 0, old_size)" in \
        _gd_function(loader, "_ensure_arena_capacity")


def test_every_authored_landmark_has_a_committed_feature_bank():
    """FL-4907 LANDMARK_FEATURE_BANKS_MISSING_20261002: every manifest
    placement World registers as a SceneGeometryDB asset (mesh or box list)
    must have a committed bank keyed by its local-row digest. Residue keys
    (terrain-placed rows) are unknowable offline and must not be the route.
    Offline only; the runtime digest equality was probed headlessly."""
    manifest = json.loads((WORLD_MESH / "authored_landmarks.json").read_text(encoding="utf-8"))
    index = json.loads((bank.DEFAULT_OUT / "index.v1.json").read_text(encoding="utf-8"))["banks"]
    box_ids = {s["source"].rsplit("#", 1)[1]: s for s in bank.landmark_box_sources()}
    for pid, placement in manifest["placements"].items():
        if "boxes" in placement:
            src = box_ids[pid]
            assert src["face_count"] == 6 * len(placement["boxes"])
        else:
            src = next(bank.faces_sources([ROOT / "godot_project" / placement["mesh"]]))
        digest = bank.content_hash_of(src["rows32"], src["face_count"])
        assert digest in index, (pid, digest)
        assert (bank.DEFAULT_OUT / index[digest]["file"]).exists(), (pid, digest)
    world = (ROOT / "godot_project/scripts/World.gd").read_text(encoding="utf-8")
    worker = world.split("func _append_static_world_cyan_pedestal(", 1)[1].split("\nfunc ", 1)[0]
    # Boxes are built in local space and translated by the instance origin.
    assert "_append_static_world_box(local_rows," in worker
    assert "deferred_placements.append(" in worker


# FL-4907 RUNTIME_SOURCE_BANK_COVERAGE_20261002 (N16). Every runtime rigid
# mesh is a compiled route; a mesh whose digest cannot be produced offline
# would be a named gap here (TrueFFRigidFeatures omits it as
# rigid_part_feature_bank_missing). The spider rig and drop-ship cube are
# baked assets since the operator orders of 2026-10-02, so none remain.
UNCOMPILED_RIGID_MESHES: dict = {}
# Baked true-ff-rigid-mesh.v1 assets: file -> (mesh key, faces, part count).
BAKED_RIGID_MESHES = {
    "bird_flock_rigid_mesh.json": ("bird_flock_v1", 4, 2),
    "falling_leaf_rigid_mesh.json": ("falling_leaf_v1", 2, 1),
    "pumpkin_rigid_mesh.json": ("pumpkin_firefly_v1", 39, 1),
    "spider_parts_rigid_mesh.json": ("spider_parts_v1", 89, 18),
    "drop_ship_cube_rigid_mesh.json": ("drop_ship_cube_v1", 6, 1),
}
# Kinds True FF draws as ink from retained runtime stroke templates
# (TrueFFEffectFeatures INK_KINDS / TrueFFStrokeBank.compile), not offline banks.
RIGID_KINDS = {"airplane", "spider", "ambient_mesh", "bird_face"}


def test_every_runtime_rigid_mesh_and_mover_kind_has_a_committed_bank():
    """Every rigid mesh producer route compiles through `build --rigid` and its
    part digests are committed; every mover kind is rigid (compiled banks) or
    ink (runtime stroke templates); any uncompiled rigid mesh is a named gap.
    Offline only: runtime digest equality needs the headless coverage probe."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import true_ff_rigid_features as rigid

    proj = ROOT / "godot_project"
    index = json.loads((bank.DEFAULT_OUT / "index.v1.json").read_text(encoding="utf-8"))["banks"]
    routes = {}
    for src in rigid.rigid_sources(proj):
        digest = bank.content_hash_of(src["rows32"], src["face_count"])
        assert digest in index, (src["source"], digest)
        assert (bank.DEFAULT_OUT / index[digest]["file"]).exists(), (src["source"], digest)
        routes.setdefault(src["source"].split("#", 1)[0].rsplit("/", 1)[-1], []).append(src)
    # Baked rigid assets: every producer constant names a compiled asset.
    for script, pattern in (("scripts/visual/BirdFlock3D.gd", r'RIGID_MESH_ASSET := "res://(.+?)"'),
                            ("scripts/visual/FallingLeafField.gd", r'RIGID_MESH_ASSET := "res://(.+?)"'),
                            ("scripts/visual/PumpkinFireflyField.gd", r'RIGID_MESH_ASSET := "res://(.+?)"'),
                            ("scripts/creatures/SpiderPresentationBuilder.gd", r'RIGID_MESH_ASSET := "res://(.+?)"'),
                            ("scripts/world/DropShipPresentation.gd", r'RESOURCE := "res://(.+?)"')):
        text = (proj / script).read_text(encoding="utf-8")
        asset = re.search(pattern, text).group(1)
        key, faces, part_count = BAKED_RIGID_MESHES[Path(asset).name]
        doc = json.loads((proj / asset).read_text(encoding="utf-8"))
        assert doc["schema"] == "true-ff-rigid-mesh.v1" and doc["mesh_key"] == key, asset
        assert doc["face_count"] == faces and len(set(doc["part_indices"])) == part_count, asset
        assert len(routes[Path(asset).name]) == part_count, asset
    drop = (proj / "scripts/world/DropShipPresentation.gd").read_text(encoding="utf-8")
    assert 'const MESH_KEY := "drop_ship_cube_v1"' in drop
    for deleted in ("get_pixel", "albedo", "triangle_material_slots", "_palette"):
        assert deleted not in drop, deleted  # load-time texture face sampling deleted
    spider = (proj / "scripts/creatures/SpiderPresentationBuilder.gd").read_text(encoding="utf-8")
    for deleted in ("_append_ellipsoid_rows", "_append_body_rows", "_body_point",
                    "BODY_RADIAL_SEGMENTS", "BODY_LATITUDE_BANDS", "_append_quad"):
        assert deleted not in spider, deleted
    for name in ("airplane_fracture.json", "macchi_fracture.json", "nausicaa_fracture.json"):
        shards = json.loads((proj / "assets/world_mesh" / name).read_text(encoding="utf-8"))["shards"]
        assert len(routes[name]) == len(shards), name
    assert len(routes["World.gd"]) == 1  # boat_v1
    world = (proj / "scripts/World.gd").read_text(encoding="utf-8")
    assert 'register_mesh("boat_v1", _boat_airframe_rows, parts)' in world
    # Every GDScript rigid registration site is a compiled route or a named gap.
    compositor = (proj / "render/api/ASCIICompositorEffect.gd").read_text(encoding="utf-8")
    assert 'geometry.call("register_mesh", key, rows, parts)' in compositor
    fracture = (proj / "scripts/visual/AirplaneFracture.gd").read_text(encoding="utf-8")
    assert '"mesh_key": "%s#shard=%d" % [asset_path, shard_index]' in fracture
    assert "String(template.mesh_key), template.rows, template.parts)" in world  # spider rig
    sites = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*.gd")
                   if "/tests/" not in p.as_posix() and "register_mesh(" in p.read_text(encoding="utf-8"))
    assert sites == ["render/scene/geometry/dynamic/rigid/RigidMoverGeometry.gd",
                     "scripts/World.gd", "scripts/visual/ActorWeaponPresentation.gd",
                     "scripts/world/DropShipPresentation.gd"], sites
    assert UNCOMPILED_RIGID_MESHES == {}
    # Every VSB mover kind is either a rigid (compiled bank) kind or an ink kind.
    db = (proj / "render/scene/SceneGeometryDB.gd").read_text(encoding="utf-8")
    table = db.split("const VSB_DYNAMIC_KIND_IDS := {", 1)[1].split("}", 1)[0]
    kinds = set(re.findall(r'&"(\w+)"', table))
    effects = (proj / "render/ascii/features/TrueFFEffectFeatures.gd").read_text(encoding="utf-8")
    ink_list = effects.split("const INK_KINDS", 1)[1].split("= [", 1)[1].split("]", 1)[0]
    ink = set(re.findall(r'"(\w+)"', ink_list))
    assert kinds == RIGID_KINDS | ink and not (RIGID_KINDS & ink), (kinds, ink)
    assert 'if kind in ["airplane", "spider", "ambient_mesh", "bird_face"]:' in compositor
    # No parallel CPU face owner for rigid kinds (deleted, FL-4907 N16 rework).
    for deleted in ("_append_dynamic_bird_face", "_append_dynamic_ambient_mesh"):
        assert deleted not in compositor, deleted
