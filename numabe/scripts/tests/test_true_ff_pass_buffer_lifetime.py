"""Offline lifetime unit; fake RD, no Godot/GPU execution or FPS evidence.

Execute the production resize branch after syntax-only translation of its
small GDScript subset. This does not validate Godot's deferred RID destruction.
"""
from pathlib import Path
import re

import pytest

from scripts.tests.test_true_ff_end_to_end_repairs import gd_body


SOURCE = Path(__file__).resolve().parents[2] / "godot_project/render/ascii/features/TrueFFFeaturePasses.gd"


class RID:
    def __init__(self, index=0):
        self.index = index

    def is_valid(self):
        return self.index != 0

    def get_id(self):
        return self.index


class Bytes:
    def __init__(self):
        self.length = 0

    def resize(self, size):
        self.length = size

    def size(self):
        return self.length

    def fill(self, value):
        assert value in (0, 0xFF)


class Device:
    STORAGE_BUFFER_USAGE_DISPATCH_INDIRECT = 1
    LIMIT_MAX_COMPUTE_WORKGROUP_COUNT_X = 28

    def __init__(self):
        self.created = []
        self.freed = []
        self.fail_at = None
        self.initialized = []
        self.max_dispatch_groups = 65535
        self.copied = []
        self.copy_result = 0

    def limit_get(self, limit):
        assert limit == self.LIMIT_MAX_COMPUTE_WORKGROUP_COUNT_X
        return self.max_dispatch_groups

    def storage_buffer_create(self, size, data, usage=0):
        assert size > 0, "RenderingDevice allocation must request physical bytes"
        assert data.size() in (0, size)
        if self.fail_at == len(self.created):
            return RID()
        rid = RID(len(self.created) + 1)
        self.created.append((rid, size, usage))
        self.initialized.append(data.size())
        return rid

    def uniform_buffer_create(self, size, data):
        return self.storage_buffer_create(size, data)

    def free_rid(self, rid):
        assert rid not in self.freed
        self.freed.append(rid)

    def buffer_copy(self, source, target, source_offset, target_offset, size):
        assert source not in self.freed and target not in self.freed
        self.copied.append((source, target, source_offset, target_offset, size))
        return self.copy_result


def body(name):
    source = SOURCE.read_text()
    match = re.search(rf"^func {name}\(.*?\n(.*?)(?=^func |^static func |\Z)",
                      source, re.M | re.S)
    assert match, name
    return match[1]


def host():
    # Only spelling changes; allocation ordering/branches are production code.
    code = body("_resize_buffers")
    # Keep the production branch executable when Godot adds a typed generic
    # local (for example Array[PackedInt64Array]).  The annotation is not
    # part of the lifetime behavior under test.
    code = re.sub(r"var (\w+)(?::[^=\n]+)?\s*(?::=|=)", r"\1 =", code)
    code = re.sub(r"var (\w+): RID\s*\n", r"\1 = RID()\n", code)
    code = re.sub(r"\s+as\s+[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?", "", code)
    code = code.replace("replacements.is_empty()", "not replacements")
    code = code.replace('replacements.has("admission_nodes")', '"admission_nodes" in replacements')
    code = re.sub(r"([A-Za-z_]\w*)\.has\(([^()]*)\)", r"\2 in \1", code)
    code = re.sub(r"\btrue\b", "True", code)
    code = re.sub(r"\bfalse\b", "False", code)
    code = "def resize(sizes, phase_capacities=None):\n\tglobal _adjacency_key, _set_key, _adjacency_stale, _instance_nodes_fresh\n\tif phase_capacities is None:\n\t\tphase_capacities = Vector2i(0, 0)\n" + code
    rd = Device()
    template = Bytes()
    template.resize(896)
    frees = []
    class Vector2i:
        def __init__(self, x, y):
            self.x, self.y = x, y

        def __eq__(self, other):
            return isinstance(other, Vector2i) and (self.x, self.y) == (other.x, other.y)

        def __getitem__(self, index):
            return (self.x, self.y)[index]

    state = dict(RID=RID, PackedByteArray=Bytes, PackedInt64Array=list,
                 RenderingDevice=Device, Vector2i=Vector2i,
                 _rd=rd, _buffers={}, _buffer_sizes={}, _args_template=template,
                 _adjacency_key="", _set_key="", _free_sets=lambda: frees.append(True),
                 MAX_BUFFER_BYTES=0xFFFFFFFF, mini=min, OK=0,
                 ADMISSION_NODE_BYTES=48, PHASE_HISTORY_BYTES=48,
                 _phase_capacities=Vector2i(0, 0), _epoch=0,
                 _view_scene_base=-1, _scene_specs={"admission_base": 0, "metadata": 0},
                 _adjacency_stale=False, _instance_nodes_fresh=False,
                 _view_scene_key=[], _scene_topology_key=[], _view_scene_count=0)
    exec(compile(code, str(SOURCE) + "::_resize_buffers[offline subset]", "exec"), state)
    return state, rd, frees


def sizes():
    return dict(edge_faces=160, candidates=960, sites=960, crossings=640,
                slot_map=80, args=896, camera=240)


@pytest.mark.parametrize("changes", [dict(candidates=1920, sites=1920),
                                    dict(crossings=1280, slot_map=160),
                                    dict(crossings=320, slot_map=40)])
def test_view_scratch_changes_keep_retained_adjacency(changes):
    state, rd, frees = host()
    initial = sizes()
    assert state["resize"](initial)
    old = state["_buffers"].copy()
    state["_adjacency_key"] = "bank-sha"
    state["_set_key"] = "bound"
    assert state["resize"](initial | changes)
    assert state["_adjacency_key"] == "bank-sha"
    assert state["_set_key"] == ""
    assert len(frees) == 2
    assert set(rd.freed) == {old[key] for key in changes}
    for key in initial.keys() - changes.keys():
        assert state["_buffers"][key] is old[key]
    assert len(rd.created) == len(initial) + len(changes)


def test_unchanged_sizes_allocate_and_invalidate_nothing():
    state, rd, frees = host()
    assert state["resize"](sizes())
    state["_adjacency_key"] = "bank-sha"
    state["_set_key"] = "bound"
    assert state["resize"](sizes())
    assert len(rd.created) == len(sizes()) and not rd.freed and len(frees) == 1
    assert state["_adjacency_key"] == "bank-sha" and state["_set_key"] == "bound"
    assert [usage for _, _, usage in rd.created].count(1) == 1


def test_admission_history_growth_copies_before_freeing_old_buffer():
    state, rd, frees = host()
    assert state["resize"]({"admission_nodes": 48 * 7, "sites": 112 * 10})
    old = state["_buffers"]["admission_nodes"]
    assert state["resize"]({"admission_nodes": 48 * 11, "sites": 112 * 20})
    replacement = state["_buffers"]["admission_nodes"]
    assert rd.copied == [(old, replacement, 0, 0, 48 * 7)]
    assert old in rd.freed and replacement not in rd.freed
    assert len(frees) == 2


def test_admission_history_copy_failure_preserves_every_old_binding():
    state, rd, frees = host()
    assert state["resize"]({"admission_nodes": 48 * 7, "sites": 112 * 10})
    old = state["_buffers"].copy()
    sizes_before = state["_buffer_sizes"].copy()
    rd.copy_result = 1
    assert not state["resize"]({"admission_nodes": 48 * 11, "sites": 112 * 20})
    assert state["_buffers"] == old and state["_buffer_sizes"] == sizes_before
    assert all(rid not in rd.freed for rid in old.values())
    assert len(frees) == 1


def test_unequal_phase_halves_copy_previous_descriptor_and_revision_ranges():
    state, rd, frees = host()
    old_capacities = state["Vector2i"](4, 8)
    new_capacities = state["Vector2i"](6, 10)
    old_size = (old_capacities.x + old_capacities.y) * 48 + 8
    new_size = (new_capacities.x + new_capacities.y) * 48 + 8
    assert state["resize"]({"phase_history": old_size}, old_capacities)
    old = state["_buffers"]["phase_history"]
    state["_phase_capacities"] = old_capacities
    state["_epoch"] = 1  # the previous half is the unequal y=8 half
    assert state["resize"]({"phase_history": new_size}, new_capacities)
    replacement = state["_buffers"]["phase_history"]
    assert rd.copied == [
        (old, replacement, 4 * 48, 6 * 48, 8 * 48),
        (old, replacement, (4 + 8) * 48, (6 + 10) * 48, 8),
    ]
    assert old in rd.freed and replacement not in rd.freed
    assert len(frees) == 2


def test_scene_history_cut_is_relocated_before_old_admission_rid_retirement():
    state, rd, _ = host()
    assert state["resize"]({"admission_nodes": 48 * 20})
    old = state["_buffers"]["admission_nodes"]
    state.update(_view_scene_base=4, _view_scene_count=3,
                 _view_scene_key=["scene"], _scene_topology_key=["scene"],
                 _scene_specs={"admission_base": 9, "metadata": 3})
    assert state["resize"]({"admission_nodes": 48 * 30})
    replacement = state["_buffers"]["admission_nodes"]
    assert rd.copied == [
        (old, replacement, 0, 0, 4 * 48),
        (old, replacement, 4 * 48, 9 * 48, 3 * 48),
    ]
    assert old in rd.freed and replacement not in rd.freed


def test_source_history_frame_requires_all_views_before_owner_tick_advance():
    begin = body("begin_source_frame")
    complete = body("complete_source_frame")
    assert "_source_frame_open" in begin and "_source_frame_submitted_views.clear()" in begin
    assert "_source_frame_history_key" in begin and "_source_frame_view_count" in begin
    assert complete.index("source_history_views_incomplete") < complete.index("advance_source_tick")
    assert "source_history_completed_owner_changed" in complete
    assert "source_history_completed_use_rejected" in complete
    assert "_source_frame_submitted_views.size() != _source_frame_view_count" in complete


def test_view_history_retirement_is_explicit_and_keeps_temporal_state_view_local():
    prepare_views = body("prepare_views")
    save_history = body("_save_view_history")
    activate = body("_activate_view")
    assert "_source_frame_open" in prepare_views
    assert "for view_index in range(view_count)" in prepare_views
    assert prepare_views.index("_activate_view(0)") < prepare_views.index("_free_view_history")
    assert "retired < view_count" in prepare_views
    assert "_view_histories.erase(retired)" in prepare_views
    for name in ("admission_nodes", "phase_history", "owed", "retry_dispatch"):
        assert '"%s"' % name in save_history
        assert '"%s"' % name in activate
    assert "_view_histories[_view_index]" in save_history
    assert "_view_histories.get(view_index, {})" in activate


def candidate_host():
    state, rd, frees = host()
    state.update(_caps={}, _failure="", SITE_BYTES=112, MAX_ROUNDS=32, GROUP=64,
                 MAX_BUFFER_BYTES=0xFFFFFFFF, maxi=max, _dispatch_groups_x=0,
                 _dispatch_groups_y=0, MAX_LEVELS=8, SLOT_BYTES=16,
                 WITNESS_LANE_BYTES=336)
    class Vector2i:
        def __init__(self, x, y):
            self.x, self.y = x, y
    state["Vector2i"] = Vector2i
    state["dispatch_groups_x"] = lambda: (state["_dispatch_groups_x"]
        if state["_dispatch_groups_x"] > 0 else max(state["_rd"].max_dispatch_groups, 1))
    # Keep the offline harness red-first against the production arithmetic;
    # these are the exact extracted count/tiling bodies, not fake allocation
    # bookkeeping.
    for function, args in (("_dispatch_group_count", "count, local_size"),
                           ("_dispatch_dimensions", "count, local_size, tile_x=-1"),
                           ("supports_tiled_dispatch", "count, local_size, tile_x"),
                           ("supports_dispatch_count", "count, local_size=GROUP")):
        code = body(function)
        code = re.sub(r"var (\w+)(?:: \w+)?\s*(?::=|=)", r"\1 =", code)
        code = re.sub(r"\btrue\b", "True", code)
        code = re.sub(r"\bfalse\b", "False", code)
        code = code.replace("maxi(", "max(").replace("mini(", "min(")
        exec("def " + function + "(" + args + "):\n\tglobal _failure\n" + code, state)
    for function, args in (("_round_bound", "capacity"), ("_page_candidate_capacity", "required")):
        code = body(function)
        code = re.sub(r"var (\w+)(?:: \w+)?\s*(?::=|=)", r"\1 =", code)
        code = re.sub(r"\btrue\b", "True", code)
        code = re.sub(r"\bfalse\b", "False", code)
        code = code.replace("MAX_BUFFER_BYTES / SITE_BYTES", "MAX_BUFFER_BYTES // SITE_BYTES")
        code = code.replace("MAX_BUFFER_BYTES / WITNESS_LANE_BYTES",
                            "MAX_BUFFER_BYTES // WITNESS_LANE_BYTES")
        exec("def " + function + "(" + args + "):\n\tglobal _failure\n" + code, state)
    state["_resize_buffers"] = state["resize"]
    return state, rd, frees


def test_p5_rejects_node_byte_overflow_before_releasing_page():
    source = SOURCE.with_name("TrueFFFeatureRenderer.gd").read_text()
    page = source.split("func _page(", 1)[1].split("\nfunc ", 1)[0]
    assert page.index("site_capacity > 0xFFFFFFFF / NODE_BYTES") < page.index("_release_page()")
    storage = gd_body(source, "_storage")
    assert "size > 0xFFFFFFFF" in storage
    assert "_rd.storage_buffer_create(maxi(size, 16), initial)" in storage
    assert ".resize(" not in storage


@pytest.mark.parametrize("budget", [1, 73, 120, 25000, 25001])
def test_fixed_page_candidate_capacity_is_exact_not_a_high_water_bucket(budget):
    state, rd, _ = candidate_host()
    assert state["_page_candidate_capacity"](budget) == budget
    assert not rd.created  # validation never stalls or allocates mid-frame
    assert state["resize"]({"candidates": budget * 112, "sites": budget * 112})
    physical = {rid: size for rid, size, _ in rd.created}
    for name in ("candidates", "sites"):
        assert physical[state["_buffers"][name]] == budget * 112
    assert not any(rd.initialized)


def test_fixed_page_capacity_rejects_byte_and_dispatch_abi_without_trimming():
    state, rd, _ = candidate_host()
    validate = state["_page_candidate_capacity"]
    for invalid in (-1, 0, 0x100000000):
        assert validate(invalid) == 0
        assert state["_failure"] == "page_budget_exceeds_u32_abi"
    assert validate(0xffffffff // 112 + 1) == 0
    assert state["_failure"] == "page_budget_exceeds_buffer_abi"
    rd.max_dispatch_groups = 2
    assert validate(128) == 128
    assert validate(129) == 0
    assert state["_failure"] == "page_budget_exceeds_dispatch_abi"
    assert not rd.created


def test_execute_is_submission_only_and_never_reads_current_frame_demand():
    execute = body("execute")
    epoch = execute.index("_epoch += 1")
    begin = execute.index("_rd.compute_list_begin()")
    cut = execute.index("_record_cut_round(")
    native = execute.index("_dispatch_native_certificate(")
    end = execute.index("_rd.compute_list_end()", begin)
    assert epoch < begin < native < cut < end
    assert "buffer_get_data(" not in execute
    assert "buffer_get_data_async" not in execute
    assert "_reserve_candidates(" not in execute
    assert '"candidate_demand_read_failed"' not in execute
    traversal = SOURCE.with_name("true_ff_feature_traverse.glslinc").read_text()
    assert "args[TFF_SLOT_META * 4u + 2u] = required;" in traversal
    assert "required > cam.caps1.y" in traversal  # invariant failure, never a detail selector
    assert "page_sites" not in body("_ensure_buffers")


def test_replacing_retained_adjacency_preserves_content_key_and_invalidates_sets():
    state, rd, _ = host()
    assert state["resize"](sizes())
    old = state["_buffers"]["edge_faces"]
    state["_adjacency_key"] = "bank-sha"
    state["_set_key"] = "bound"
    assert state["resize"](sizes() | dict(edge_faces=320))
    # The old edge-face prefix is copied before the RID is retired, so the
    # adjacency content key remains valid; only borrowed uniform sets need a
    # rebuild after the buffer identity changes.
    assert state["_adjacency_key"] == "bank-sha"
    assert state["_set_key"] == ""
    assert rd.freed == [old]


def test_failed_replacement_keeps_all_live_bindings_and_allocation_sizes():
    state, rd, frees = host()
    assert state["resize"](sizes())
    old = state["_buffers"].copy()
    old_sizes = state["_buffer_sizes"].copy()
    state["_adjacency_key"] = "bank-sha"
    state["_set_key"] = "bound"
    rd.fail_at = len(rd.created) + 1  # second requested replacement fails
    changed = sizes() | dict(candidates=1920, sites=1920)
    assert not state["resize"](changed)
    assert state["_buffers"] == old and state["_buffer_sizes"] == old_sizes
    assert state["_adjacency_key"] == "bank-sha" and state["_set_key"] == "bound"
    assert not set(old.values()).intersection(rd.freed)
    assert rd.freed == [rd.created[-1][0]] and len(frees) == 1
    rd.fail_at = None
    assert state["resize"](changed)


def test_live_caller_commits_capacities_only_after_resize_and_keeps_revision_check():
    ensure = body("_ensure_buffers")
    assert "_free_buffers()" not in ensure
    assert ensure.index("if not _resize_buffers(sizes, phase_capacities):") < ensure.index("_caps = caps")
    assert "phase_capacities[" in ensure
    reconcile = body("reconcile_publication")
    assert reconcile.index("_build_adjacency(bank)") < reconcile.index("_adjacency_stale = false")
    assert "_prepare_scene_metadata(bank, scene_key)" in reconcile
    assert "_buffer_sizes.clear()" in body("_free_buffers")


def test_live_source_set_cache_is_keyed_by_complete_publication_signature():
    ensure_sets = body("_ensure_sets")
    assert "live_source_bindings" in ensure_sets
    assert "family_mask" in ensure_sets
    assert "signature_for_mask" in ensure_sets
    assert "binding_keys_for_mask" in ensure_sets
    cache_hit = ensure_sets.index("if key == _set_key")
    cache_rebuild = ensure_sets.index("_free_pass_sets()")
    assert cache_hit < cache_rebuild
    assert "uniform_set_is_valid" in ensure_sets
