"""Source-lifetime P1 history ownership; no engine launch or temporal proof.

FL-4907 RETAINED_INSTANCE_TABLE_AND_BANK_KEY_20261002 (N3): admission ranges
and namespaces belong to RESIDENT sources. They are recycled after a delay by
a first-fit block allocator, every (re)allocated range is zeroed by the passes
through the bank change log, and node ranges are uploaded as dirty spans.
The runtime evidence is the headless retained-table probe, not this file.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"


def _gd_function(source, name):
    match = re.search(rf"^(?:static )?func {name}\(", source, re.M)
    assert match, name
    return source[match.start():].split("\n\n\n", 1)[0]


def _block_model():
    """Python port of _block_alloc/_block_free (same algorithm, checked
    against the GDScript text below so the two cannot drift silently)."""
    def alloc(space, count, limit):
        blocks = space["free"]
        for index, (base, size) in enumerate(blocks):
            if size < count:
                continue
            if size == count:
                blocks.pop(index)
            else:
                blocks[index] = (base + count, size - count)
            return base
        base = space["top"]
        if base > limit - count:
            return -1
        space["top"] = base + count
        return base

    def free(space, base, count):
        if count <= 0:
            return
        blocks = space["free"]
        index = 0
        while index < len(blocks) and blocks[index][0] < base:
            index += 1
        blocks.insert(index, (base, count))
        if index + 1 < len(blocks) and base + count == blocks[index + 1][0]:
            blocks[index] = (base, count + blocks[index + 1][1])
            blocks.pop(index + 1)
        if index > 0 and blocks[index - 1][0] + blocks[index - 1][1] == blocks[index][0]:
            blocks[index - 1] = (blocks[index - 1][0], blocks[index - 1][1] + blocks[index][1])
            blocks.pop(index)
        last = blocks[-1]
        if last[0] + last[1] == space["top"]:
            space["top"] = last[0]
            blocks.pop()

    return alloc, free


def test_block_allocator_text_matches_the_model():
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    alloc = _gd_function(bank, "_block_alloc")
    free = _gd_function(bank, "_block_free")
    assert "if block.y < count:" in alloc and "blocks.remove_at(index)" in alloc
    assert "blocks[index] = Vector2i(block.x + count, block.y - count)" in alloc
    assert "if base > limit - count:" in alloc and 'space["top"] = base + count' in alloc
    assert "blocks.insert(index, Vector2i(base, count))" in free
    assert 'if last.x + last.y == int(space["top"]):' in free


def test_recycled_ranges_are_bounded_by_resident_sources():
    alloc, free = _block_model()
    space = {"free": [], "top": 0}
    limit = 0xFFFFFFFF // 192
    a = alloc(space, 4, limit)
    b = alloc(space, 7, limit)
    c = alloc(space, 5, limit)
    assert (a, b, c) == (0, 4, 11) and space["top"] == 16
    # b leaves residency: its exact range is reused, the space does not grow.
    free(space, b, 7)
    assert alloc(space, 7, limit) == 4 and space["top"] == 16
    # A long session of churn stays bounded by the resident total.
    for _ in range(1000):
        d = alloc(space, 3, limit)
        free(space, d, 3)
    assert space["top"] == 16
    # The top block retiring lowers the top; adjacent frees merge.
    free(space, c, 5)
    assert space["top"] == 11
    free(space, a, 4)
    free(space, 4, 7)
    assert space["top"] == 0 and space["free"] == []
    assert alloc(space, limit + 1, limit) == -1


def test_namespaces_and_ranges_recycle_after_the_delay_and_are_zeroed():
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    namespace = _gd_function(bank, "_namespace_for_key")
    assert "_namespace_free" in namespace and "_recycle.append(" in namespace
    release = _gd_function(bank, "_release_recycled")
    assert 'int(_namespace_refs.get(source_namespace, 1)) > 0' in release
    assert "_namespace_free.append(source_namespace)" in release
    assert "_block_free(_admission_blocks, node_range.x, node_range.y)" in release
    base = _gd_function(bank, "_admission_node_base")
    assert "_log_change(CHANGE_ADMISSION_CLEAR, base, node_count)" in base
    assert "_range_recycle.append(" in base
    reconcile = _gd_function(host, "reconcile_publication")
    assert '_rd.buffer_clear(_buffers["admission_nodes"], offset, size)' in reconcile
    assert "history_key != _history_key" in reconcile


def test_host_uploads_owned_ranges_and_never_rebases_history_on_active_offsets():
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    shader = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    assert "uint base = instance_nodes[root.x].x;" in shader
    assert "uint nodes_owned = tff_instance_node_count(instances[root.x]);" in shader
    assert "instance_nodes[k] =" not in shader
    commit = _gd_function(bank, "_commit_slot")
    assert "_instance_node_ranges.encode_u32(slot * 8, node_base)" in commit
    assert "_log_change(CHANGE_NODE_RANGE, slot)" in commit
    assert 'return "%d:%d" % [get_instance_id(), _source_generation]' in bank
    reconcile = _gd_function(host, "reconcile_publication")
    assert 'buffer_update(_buffers["instance_nodes"]' in reconcile
    assert reconcile.index('buffer_update(_buffers["instance_nodes"]') < reconcile.index("_prepare_scene_metadata(bank, scene_key)")
    assert "node_ranges.slice(low * 8, high * 8)" in reconcile
    assert '"admission_nodes": history_total * ADMISSION_NODE_BYTES' in host
    assert "buffer_get_data(" not in host
