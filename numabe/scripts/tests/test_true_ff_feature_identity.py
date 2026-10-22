"""Collision-free True FF contour identity laws.

Offline only: these tests exercise the retained source namespace and exact
chain/address lanes. Semantic object ids remain paint facts, never contour
equality keys.
"""
from pathlib import Path
import json
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_feature_passes_ref as passes  # noqa: E402
import true_ff_feature_layout_ref as layout  # noqa: E402


def _seg(namespace, address=0, cls=passes.CREASE, object_id=77):
    return passes.Segment(
        np.array([-1.0, 0.0, 2.0]), np.array([1.0, 0.0, 2.0]), 0.0, 2.0,
        namespace, cls, object_id, 0, chain_address=0,
        source_address=address, level_mask=1 << 8,
    )


def _site(namespace, address, cls=layout.CLASS_TERRAIN_RELIEF, object_id=77):
    return layout.Site(40.0, 8.0, 4.0, 0, 0, 0,
                       layout.pack_role(layout.class_role(cls), 1, cls, 0),
                       object_id, 1.0, 0.0, namespace, address)


def test_same_object_chain_zero_stays_separate_across_retained_parts():
    a, b = _seg(11, 3), _seg(12, 3)
    assert (a.key, a.chain_address) != (b.key, b.chain_address)
    assert a.object_id == b.object_id  # semantic paint id is intentionally shared
    generated = passes.sites(passes.make_camera((0, 0, 5), (0, 0, 0), ortho=True), [a, b])
    assert {s.identity[:2] for s in generated} == {(11, 0), (12, 0)}


def test_forced_old_hash_collision_cannot_merge_exact_identity():
    # Concrete collision under the deleted object/chain/class hash.
    def old(ident):
        return passes.mix32((7 * 0x9E3779B1) ^ passes.mix32(ident ^ passes.SMOOTH)) | 1
    assert old(18915) == old(120802) == 3244972129
    a, b = _site(21, 18915, passes.SMOOTH), _site(21, 120802, passes.SMOOTH)
    assert a.contour_identity != b.contour_identity
    # The deleted OR-1 consumer also aliased adjacent even/odd chain numbers.
    assert _site(21, 2).contour_identity != _site(21, 3).contour_identity


def test_reorder_and_reveal_keep_source_identity():
    cam = passes.make_camera((0, 0, 5), (0, 0, 0), ortho=True)
    all_sites = passes.sites(cam, [_seg(31, 9), _seg(32, 4)])
    reversed_sites = passes.sites(cam, [_seg(32, 4), _seg(31, 9)])
    assert {s.identity for s in all_sites} == {s.identity for s in reversed_sites}
    revealed = passes.sites(cam, [_seg(31, 9)])
    assert {s.identity for s in revealed} <= {s.identity for s in all_sites}


def test_classes_and_implicit_terrain_keep_class_and_address_facts():
    fixed = _seg(41, 17, passes.CREASE)
    terrain = _seg(41, 17, passes.RELIEF)
    assert fixed.cls != terrain.cls
    assert fixed.source_address == terrain.source_address == 17
    # Terrain's implicit source still has a retained part namespace and a
    # chain/address; no feature/frame slot is needed to name it.
    terrain_site = _site(41, 17)
    assert terrain_site.contour_identity == (41, 17, layout.CLASS_TERRAIN_RELIEF)
    assert _site(41, 17, passes.CREASE).contour_identity != terrain_site.contour_identity
    extract = (ROOT / "godot_project/render/ascii/features/true_ff_feature_extract_common.glslinc").read_text()
    assert "uint chain_address = tff_is_terrain(inst) ? a.y : f.ident.y;" in extract


def test_row_connectivity_uses_exact_two_word_identity():
    table = layout.Table()
    page = layout.Page(200, 17)
    same = [_site(51, 0), _site(51, 0)]
    other_part = [_site(51, 0), _site(52, 0)]
    same_nodes = layout.build_nodes(table, same, 0, page)
    other_nodes = layout.build_nodes(table, other_part, 0, page)
    assert same_nodes and other_nodes
    assert same_nodes[0].edge == same_nodes[1].edge
    assert other_nodes[0].edge != other_nodes[1].edge


def test_production_has_no_hashed_run_identity_or_low_bit_alias():
    features = ROOT / "godot_project/render/ascii/features"
    text = "\n".join((features / name).read_text() for name in (
        "true_ff_feature_extract.glsl",
        "true_ff_feature_extract_common.glslinc",
        "true_ff_feature_passes.glslinc",
        "true_ff_feature_sites.glsl",
        "true_ff_feature_layout.glsl",
    ))
    assert "run_key" not in text
    assert "return uvec4(s.identity.xy,s.source.z,s.key.w);" in text
    assert "certificate_group_key(FeatureSite s)" in text


def test_source_namespace_is_an_abi_fact_and_not_an_instance_slot():
    abi = (ROOT / "godot_project/render/ascii/features/TrueFFFeatureABI.gd").read_text()
    glsl = (ROOT / "godot_project/render/ascii/features/true_ff_feature_abi.glslinc").read_text()
    bank = (ROOT / "godot_project/render/ascii/features/TrueFFFeatureBank.gd").read_text()
    assert "const INSTANCE_OFF_IDENTITY := 112" in abi
    assert "const INSTANCE_BYTES := 144" in abi
    assert "uvec4 identity;" in glsl
    assert "uint tff_instance_source_namespace(TffFeatureInstance inst)" in glsl
    assert "source_namespace" in bank


def namespace_owner():
    """Execute the production allocator's scalar subset, not a second model.

    Syntax-only GDScript -> Python translation; this does not exercise Godot.
    """
    # FL-4907 RETAINED_INSTANCE_TABLE_AND_BANK_KEY_20261002: the exact source
    # tuple is _identity_key (also the retained slot identity); the namespace
    # allocator maps resident keys to ids and recycles released ids (N3).
    path = ROOT / "godot_project/render/ascii/features/TrueFFFeatureBank.gd"
    source = path.read_text()
    match = re.search(r"^static func _identity_key\(.*?\n(.*?)(?=^##|^(?:static )?func )",
                      source, re.M | re.S)
    assert match
    body = re.sub(r"var (\w+) :=", r"\1 =", match[1])
    body = body.replace("JSON.stringify", "json.dumps")
    body = body.replace('(rec.get("topology_identity") is String)',
                        'isinstance(rec.get("topology_identity"), str)')
    body = body.replace('rec.has("topology_identity")', '"topology_identity" in rec')
    body = body.replace("String(", "str(")
    body = body.replace("geometry_identity.is_empty()", "not geometry_identity")
    state = {"json": json, "_source_namespaces": {}, "_namespace_keys": {}}
    exec(compile("def identity_key(rec):\n" + body, str(path) + "[offline subset]", "exec"), state)
    allocator = re.search(r"^func _namespace_for_key\(.*?\n(.*?)(?=^##|^(?:static )?func )",
                          source, re.M | re.S)[1]
    assert "if _source_namespaces.has(key):" in allocator
    assert "if _namespace_keys.size() >= 0x0FFFFFFF:\n\t\treturn 0" in allocator

    def allocate(rec):
        key = state["identity_key"](rec)
        if not key:
            return 0
        if key in state["_source_namespaces"]:
            return state["_source_namespaces"][key]
        if len(state["_namespace_keys"]) >= 0x0FFFFFFF:
            return 0
        namespace = len(state["_namespace_keys"]) + 1
        state["_source_namespaces"][key] = namespace
        state["_namespace_keys"][namespace] = key
        return namespace

    state["allocate"] = allocate
    return state


def source(kind="rigid", object_id=77, part=0, digest="a" * 64):
    rec = dict(source_kind=kind, semantic_object_id=object_id, source_part=part,
               content_hash=digest)
    if kind == "actor":
        rec["topology_identity"] = "certified-actor-topology"
    return rec


def test_actor_namespace_uses_certified_topology_not_pose_digest():
    allocate = namespace_owner()["allocate"]
    first = source(kind="actor")
    next_pose = source(kind="actor", digest="b" * 64)
    assert allocate(first) != 0
    assert allocate(first) == allocate(next_pose)
    changed_topology = dict(next_pose, topology_identity="different-topology")
    assert allocate(changed_topology) != allocate(first)


def test_actor_namespace_rejects_missing_empty_or_nonstring_topology():
    allocate = namespace_owner()["allocate"]
    rec = source(kind="actor")
    del rec["topology_identity"]
    assert allocate(rec) == 0
    for invalid in ("", None, 7):
        assert allocate(dict(rec, topology_identity=invalid)) == 0


def test_production_namespace_allocator_separates_every_source_coordinate():
    state = namespace_owner()
    allocate = state["allocate"]
    records = [source(), source(object_id=78), source(part=1), source(kind="actor"),
               source(kind="static"), source(kind="terrain", object_id=0),
               source(digest="b" * 64)]
    ids = [allocate(rec) for rec in records]
    assert len(set(ids)) == len(records)
    # Asset repack / active array reorder / hide-reveal cannot recycle a key.
    assert [allocate(rec) for rec in reversed(records)] == list(reversed(ids))
    assert allocate(records[0]) == ids[0]
    # Carry these actual producer keys through the row consumer, same chain 0.
    nodes = layout.build_nodes(layout.Table(), [_site(i, 0) for i in ids], 0,
                               layout.Page(200, 17))
    assert len({node.edge for node in nodes}) == len(records)


def test_namespace_exhaustion_fails_without_alias_and_existing_key_survives():
    state = namespace_owner()
    first = state["allocate"](source())
    class Full(dict):
        def __len__(self):
            return 0x0FFFFFFF
    state["_namespace_keys"] = Full(state["_namespace_keys"])
    assert state["allocate"](source()) == first
    assert state["allocate"](source(part=1)) == 0


def test_smooth_contour_uses_exact_cut_address_not_common_chain_number():
    # Multiple disconnected smooth loops may have the same baked chain id.
    def crossing(address):
        return passes.Crossing(np.array([float(address), 0., 0.]),
                               np.array([float(address), 0., 0.]), 0, address,
                               18915, 77, source_namespace=1, chain_id=0)
    crossings = [crossing(i) for i in range(4)]
    lp = {"cut_of": [0, 0, 2, 2], "open": [False] * 4,
          "arc": [0., 1., 0., 1.]}
    segments = passes.loop_segments(crossings, [1, 0, 3, 2], lp)
    assert [segment.chain_address for segment in segments] == [0, 0, 2, 2]
    assert [segment.source_address for segment in segments] == [0, 1, 2, 3]
    # Production emit's lane must use that cut's retained asset-local address.
    shader = (ROOT / "godot_project/render/ascii/features/true_ff_feature_extract_common.glslinc").read_text()
    assert "s.identity = uvec4(c.link.w, component_reference(j)," in shader
    assert "crossings[j.k.z].phase.reference.y : crossings[j.p.y].ids.y" in shader


def test_namespace_packing_has_room_for_all_authored_feature_classes():
    abi = (ROOT / "godot_project/render/ascii/features/true_ff_feature_abi.glslinc").read_text()
    classes = [int(v) for v in re.findall(r"const uint TFF_CLASS_\w+ = (\d+)u;", abi)]
    assert classes and all(0 < c < 16 for c in classes)
    assert len({(0x0FFFFFFF << 4) | c for c in classes}) == len(classes)
    assert all(((0x0FFFFFFF << 4) | c) <= 0xFFFFFFFF for c in classes)
