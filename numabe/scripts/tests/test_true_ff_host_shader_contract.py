from pathlib import Path

from scripts import check_true_ff_host_shader_contract as contract


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
HOST_PATH = PROJECT / "render/ascii/features/TrueFFFeaturePasses.gd"


def test_set0_bindings_are_read_from_the_actual_host_source():
    host = HOST_PATH.read_text(encoding="utf-8")
    path = "render/ascii/features/true_ff_feature_traverse_hierarchy_dispatch.glsl"
    supplied = contract._normal_set0_types(PROJECT, host, path, True)
    assert 66 not in supplied
    assert supplied[23] == "sampler_with_texture"
    assert supplied[44] == "uniform_buffer"
    assert supplied[114] == "uniform_buffer"

    without_witness = host.replace(
        "_uniform(66, RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER",
        "_uniform(67, RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER",
        1,
    )
    changed = contract._normal_set0_types(PROJECT, without_witness, path, True)
    assert 66 not in changed
    errors = contract._contract_errors(
        path, {(0, 66): "storage_buffer"},
        {(0, binding): kind for binding, kind in changed.items() if binding == 66}, 16, 16,
    )
    assert errors == [f"{path}: shader requires set=0 binding=66 that the host does not supply"]


def test_witness_binding_is_supplied_only_to_wrappers_that_declare_it():
    host = HOST_PATH.read_text(encoding="utf-8")
    witness_paths = contract._resolved_path_array(host, "WITNESS_KERNEL_PATHS")
    assert witness_paths
    for path in contract._program_paths(host):
        wrapper = (PROJECT / path).read_text(encoding="utf-8")
        declares_witness = "#define TFF_WITNESS_KERNEL" in wrapper
        if declares_witness:
            supplied = contract._normal_set0_types(PROJECT, host, path, True)
            assert supplied[66] == "storage_buffer"
            assert 60 not in supplied
        elif Path(path).name not in {"true_ff_feature_discover.glsl", "true_ff_feature_native.glsl"}:
            assert 66 not in contract._normal_set0_types(PROJECT, host, path, True)


def test_program_inventory_is_the_exact_resolved_compile_list():
    host = HOST_PATH.read_text(encoding="utf-8")
    paths = contract._program_paths(host)
    # Large extraction, owed, surface, and witness graphs have one compile
    # owner per stage so Metal never receives the former combined call graph.
    assert len(paths) == 64
    assert len(paths) == len(set(paths))
    assert any(path.endswith("witness_geometry.glsl") for path in paths)
    assert any(path.endswith("witness_advance.glsl") for path in paths)


def test_extract_and_base_owed_operations_have_one_compile_owner_per_stage():
    host = HOST_PATH.read_text(encoding="utf-8")
    paths = contract._program_paths(host)
    assert paths.count("render/ascii/features/true_ff_feature_extract.glsl") == 1
    assert paths.count("render/ascii/features/true_ff_feature_owed.glsl") == 1
    assert all(paths.count(
        f"render/ascii/features/true_ff_feature_extract_stage_{stage:02d}.glsl") == 1
        for stage in range(1, 10))
    assert all(paths.count(
        f"render/ascii/features/true_ff_feature_owed_stage_{stage:02d}.glsl") == 1
        for stage in range(1, 13))


def test_witness_command_graph_has_a_fixed_fail_closed_recording_bound():
    host = HOST_PATH.read_text(encoding="utf-8")
    body = contract._function_body(host, "_dispatch_witness_lanes")
    assert body.count("_dispatch_indirect") == 5
    assert "for batch in range(64):" in body
    assert 'range(int(_caps["candidates"]))' not in body
    operations = {
        "true_ff_feature_traverse_scene_edges.glsl": "tff_witness_prepare(i);",
        "true_ff_feature_traverse_witness_geometry.glsl": "tff_geometry_probe(i);",
        "true_ff_feature_traverse_witness_visibility.glsl": "tff_visibility_probe(i);",
        "true_ff_feature_traverse_witness_advance.glsl": "tff_witness_advance(i);",
    }
    for name, operation in operations.items():
        shader = (PROJECT / "render/ascii/features" / name).read_text()
        assert operation in shader
        assert "for(uint attempt" not in shader


def test_cut_retry_bound_matches_actual_pressure_propagation():
    host = HOST_PATH.read_text(encoding="utf-8")
    capacity = contract._function_body(host, "coarsening_round_capacity")
    assert "return 3" in capacity
    assert "feature_levels" not in capacity
    traverse = (PROJECT / "render/ascii/features/true_ff_feature_traverse.glslinc").read_text()
    pressure = traverse.split("void pressure_up(", 1)[1].split("\n}", 1)[0]
    assert "admission_nodes[at].demand.z=TFF_NATIVE_PRESSURE_TAG|cam.counts.y" in pressure
    scene_pressure = traverse.split("void pressure_scene(", 1)[1].split("\n}", 1)[0]
    assert "for(uint depth=0u;depth<TFF_SCENE_DEPTH && parent!=TFF_NONE;++depth)" in scene_pressure
    assert "admission_nodes[scene.owner.w+parent].demand.z=" in scene_pressure
    record = contract._function_body(host, "_record_cut_round")
    assert "for level in range(levels - 1, -1, -1):" in record
    assert record.index("TRAVERSE_KERNELS[16]") < record.index("TRAVERSE_KERNELS[17]")


def test_scene_query_stage_is_the_lightweight_preparation_barrier():
    wrapper = (PROJECT / "render/ascii/features/true_ff_feature_traverse_surface_stage_24.glsl").read_text(
        encoding="utf-8"
    )
    assert "#define TFF_TRAVERSE_STAGE 24" in wrapper
    assert "scene_witness_queries(" in wrapper


def test_owed_transport_is_split_by_disjoint_source_family():
    host = HOST_PATH.read_text(encoding="utf-8")
    expected = {
        "true_ff_feature_owed_stage_01.glsl": "transport(tff_dispatch_linear_invocation_id(64u));",
        "true_ff_feature_owed_transport_water.glsl": "TFF_OWED_WATER_ONLY",
        "true_ff_feature_owed_transport_effect.glsl": "TFF_OWED_EFFECT_ONLY",
        "true_ff_feature_owed_transport_sky.glsl": "TFF_OWED_SKY_ONLY",
    }
    for name, marker in expected.items():
        assert marker in (PROJECT / "render/ascii/features" / name).read_text(encoding="utf-8")
    execute = contract._function_body(host, "execute")
    assert "for stage in [0, 1, 24, 25, 26, 11" in execute
    assert "12, 13, 14, 15, 2, 8" in execute
    dispatch = contract._function_body(host, "_dispatch_owed")
    assert "if stage in [1, 24, 25, 26]:" in dispatch
    assert "12, 13, 14, 15, 20, 21, 22" in dispatch


def test_closed_surface_preflight_precedes_both_surface_lookup_stages():
    host = HOST_PATH.read_text(encoding="utf-8")
    cut = contract._function_body(host, "_record_cut_round")
    preflight = "TRAVERSE_KERNELS[35], [35, level, 0, revision]"
    cached = "TRAVERSE_KERNELS[23], [23, level, 0, revision]"
    elected = "TRAVERSE_KERNELS[28], [28, level, 0, revision]"
    assert cut.index(preflight) < cut.index(cached) < cut.index(elected)
    wrapper = (PROJECT / "render/ascii/features/true_ff_feature_traverse_surface_stage_35.glsl").read_text(
        encoding="utf-8"
    )
    assert "#define TFF_TRAVERSE_STAGE 35" in wrapper
    assert "closed_surface_preflight(" in wrapper


def test_scene_surface_preflight_precedes_elected_lookup():
    host = HOST_PATH.read_text(encoding="utf-8")
    cut = contract._function_body(host, "_record_cut_round")
    queries = "TRAVERSE_KERNELS[24], [24, level, 0, revision]"
    preflight = "TRAVERSE_KERNELS[36], [36, level, 0, revision]"
    elected = "TRAVERSE_KERNELS[29], [29, level, 0, revision]"
    assert cut.index(queries) < cut.index(preflight) < cut.index(elected)
    wrapper = (PROJECT / "render/ascii/features/true_ff_feature_traverse_surface_stage_36.glsl").read_text(
        encoding="utf-8"
    )
    assert "#define TFF_TRAVERSE_STAGE 36" in wrapper
    assert "scene_surface_preflight(" in wrapper


def test_demand_is_split_by_disjoint_source_family_before_seal():
    host = HOST_PATH.read_text(encoding="utf-8")
    expected = {
        "true_ff_feature_traverse_demand.glsl": "TFF_WITHDRAWN_WATER",
        "true_ff_feature_traverse_demand_water.glsl": "TFF_DEMAND_WATER_ONLY",
        "true_ff_feature_traverse_demand_effect.glsl": "TFF_DEMAND_EFFECT_ONLY",
        "true_ff_feature_traverse_demand_sky.glsl": "TFF_DEMAND_SKY_ONLY",
    }
    for name, marker in expected.items():
        assert marker in (PROJECT / "render/ascii/features" / name).read_text(encoding="utf-8")
    cut = contract._function_body(host, "_record_cut_round")
    split = "for kernel in [2, 37, 38, 39]:"
    assert split in cut
    assert cut.index(split) < cut.index("TRAVERSE_KERNELS[3], [3, 0, 0, revision]")


def test_exact_leaf_charge_is_split_by_family_then_finalized_before_opening():
    host = HOST_PATH.read_text(encoding="utf-8")
    expected = {
        "true_ff_feature_traverse_explore.glsl": "TFF_WITHDRAWN_WATER",
        "true_ff_feature_traverse_explore_water.glsl": "TFF_EXPLORE_WATER_ONLY",
        "true_ff_feature_traverse_explore_effect.glsl": "TFF_EXPLORE_EFFECT_ONLY",
        "true_ff_feature_traverse_explore_sky.glsl": "TFF_EXPLORE_SKY_ONLY",
    }
    for name, marker in expected.items():
        assert marker in (PROJECT / "render/ascii/features" / name).read_text(encoding="utf-8")
    cut = contract._function_body(host, "_record_cut_round")
    split = "for kernel in [10, 40, 41, 42, 43]:"
    assert split in cut
    assert cut.index(split) < cut.index("TRAVERSE_KERNELS[30], [10, level, 0, revision]")
    hierarchy = (
        PROJECT / "render/ascii/features/true_ff_feature_traverse_hierarchy_dispatch.glsl"
    ).read_text(encoding="utf-8")
    assert "case 43u:" in hierarchy
    assert "explore_leaf_finalize(" in hierarchy


def test_generic_traversal_stages_have_only_three_compiled_owners():
    host = HOST_PATH.read_text(encoding="utf-8")
    paths = contract._program_paths(host)
    dispatchers = {
        "render/ascii/features/true_ff_feature_traverse_dispatch.glsl",
        "render/ascii/features/true_ff_feature_traverse_hierarchy_dispatch.glsl",
        "render/ascii/features/true_ff_feature_traverse_surface_dispatch.glsl",
    }
    assert dispatchers <= set(paths)
    superseded = {
        "true_ff_feature_traverse.glsl", "true_ff_feature_traverse_seal.glsl",
        "true_ff_feature_traverse_admit.glsl", "true_ff_feature_traverse_adjacency.glsl",
        "true_ff_feature_traverse_reserve.glsl", "true_ff_feature_traverse_revision.glsl",
        "true_ff_feature_traverse_bottom_up.glsl", "true_ff_feature_traverse_surface.glsl",
        "true_ff_feature_traverse_roots.glsl", "true_ff_feature_traverse_budget.glsl",
        "true_ff_feature_traverse_commit.glsl", "true_ff_feature_traverse_pressure.glsl",
        "true_ff_feature_traverse_scene_pressure.glsl", "true_ff_feature_traverse_reset.glsl",
        "true_ff_feature_traverse_schedule.glsl", "true_ff_feature_traverse_finish.glsl",
        "true_ff_feature_traverse_scene_witness.glsl",
        "true_ff_feature_traverse_scene_queries.glsl",
        "true_ff_feature_traverse_scene_summaries.glsl",
        "true_ff_feature_traverse_scene_elected.glsl",
        "true_ff_feature_traverse_surface_preflight.glsl",
        "true_ff_feature_traverse_scene_surface_preflight.glsl",
        "true_ff_feature_traverse_explore_finalize.glsl",
    }
    assert not superseded.intersection(Path(path).name for path in paths)
    feature_dir = PROJECT / "render/ascii/features"
    assert all(not (feature_dir / name).exists() for name in superseded)


def test_every_compiled_program_has_a_production_dispatch_route():
    host = HOST_PATH.read_text(encoding="utf-8")
    assert contract._dispatch_coverage_errors(host) == []


def test_contract_rejects_host_supplied_undeclared_binding():
    errors = contract._contract_errors(
        "program[variant]", {(0, 5): "uniform_buffer"},
        {(0, 5): "uniform_buffer", (0, 66): "storage_buffer"}, 16, 16,
    )
    assert errors == [
        "program[variant]: host supplies undeclared set=0 binding=66 type=storage_buffer"
    ]


def test_live_bindings_and_types_come_from_the_publication_owner():
    live = contract._live_types(PROJECT, {"WATER", "EFFECTS", "SKY"})
    assert live == {
        0: "uniform_buffer", 1: "uniform_buffer", 2: "storage_buffer",
        3: "storage_buffer", 4: "storage_buffer", 5: "uniform_buffer",
        6: "uniform_buffer", 7: "uniform_buffer", 8: "storage_buffer",
        9: "uniform_buffer", 10: "uniform_buffer",
    }


def test_push_sizes_are_derived_from_host_payload_construction():
    host = HOST_PATH.read_text(encoding="utf-8")
    assert contract._expected_push_bytes(
        host, "true_ff_feature_traverse_hierarchy_dispatch.glsl"
    ) == 16
    assert contract._expected_push_bytes(host, "true_ff_feature_owed.glsl") == 64
    assert contract._expected_push_bytes(host, "true_ff_feature_native.glsl") == 80


def test_contract_reports_type_and_push_size_drift_together():
    errors = contract._contract_errors(
        "program[variant]", {(0, 5): "uniform_buffer"},
        {(0, 5): "storage_buffer"}, 12, 16,
    )
    assert errors == [
        "program[variant]: set=0 binding=5 type mismatch "
        "host=storage_buffer shader=uniform_buffer",
        "program[variant]: push constant size mismatch host=16 shader=12",
    ]
