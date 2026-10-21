# FL-4359: source matching must distinguish recapture from content changes and
# must never turn missing comparisons or failed GPU copies into a clean result.
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_actual_field_stability import compare_samples, samples, runtime_readback_health


def value(fg=0.5, bg=0.2, coverage=0.5):
    return np.full(3, fg), np.full(3, bg), coverage


class V2Field(SimpleNamespace):
    """Small offline stand-in for ActualFieldFrame.pixel_provenance()."""

    def __init__(self, *, fg=0.8, bg=0.4, fg_texel=(1, 2), bg_texel=(1, 2),
                 fg_generation=1, bg_generation=1, schema="fl4359.final_paint_field.v2",
                 origin=(10.0, 20.0, 30.0), publication=None):
        fg_rgb = np.zeros((32, 32, 3))
        bg_rgb = np.zeros((32, 32, 3))
        fg_rgb[8, 24] = [fg, fg, fg]
        bg_rgb[8, 24] = [bg, bg, bg]
        super().__init__(
            metadata={"schema": schema, "semantic_ansi_size": [1, 1],
                      "logical_target_size": [32, 32],
                      "source_capture_state": {
                          "probe_size": 8,
                          "layer_content_epoch_generations": [0, fg_generation, 0, bg_generation],
                          "captured_origin_valid_mask": 1,
                          "captured_origins_after_capture": [list(origin)],
                          "capture_bands": [0],
                      }},
            width=32, height=32, projection="perspective",
            fields={"fg_rgb": fg_rgb, "bg_rgb": bg_rgb,
                    "effective_coverage": np.ones((32, 32))},
        )
        fg_reference = 1 + 1 * 64 + fg_texel[1] * 8 + fg_texel[0]
        bg_reference = 1 + 3 * 64 + bg_texel[1] * 8 + bg_texel[0]
        self.source_generation_maps = {
            "front": np.zeros((18, 8, 8), dtype="<u4"),
            "underlay": np.zeros((18, 8, 8), dtype="<u4"),
        }
        self.source_generation_maps["front"][1, fg_texel[1], fg_texel[0]] = fg_generation
        self.source_generation_maps["front"][3, bg_texel[1], bg_texel[0]] = bg_generation
        self._sources = {
            "foreground": {"reference": fg_reference, "present": True, "layer": 1,
                           "texel": list(fg_texel), "logical_probe": 0,
                           "capture_generation": fg_generation, "bank": "front",
                           "capture_generation_kind": "captured_geometry", "geometry_capture": True,
                           "publication": publication or {"terrain": "same"}},
            "background": {"reference": bg_reference, "present": True, "layer": 3,
                           "texel": list(bg_texel), "logical_probe": 0,
                           "capture_generation": bg_generation, "bank": "front",
                           "capture_generation_kind": "captured_geometry", "geometry_capture": True,
                           "publication": publication or {"terrain": "same"}},
        }

    def pixel_provenance(self, _x, _y):
        return {"front": self._sources, "underlay": {}, "underlay_used": False}


def source_row(field, *, prepack="background"):
    """Emit exact presented/prepack provenance for the synthetic field."""
    def key(source):
        extent = 8
        reference = source["reference"] & 0x0fffffff
        layer, local = divmod(reference - 1, extent * extent)
        return [source["logical_probe"], (layer % 6) * extent * extent + local,
                source["capture_generation"], layer]

    fg = field._sources["foreground"]
    witness = field._sources[prepack]
    return {
        "probe_prepack_valid": True,
        "probe_prepack_identity": {"owner_kind": 3, "object_id": 0,
                                    "primitive_id": 2, "material": 140},
        "probe_prepack_world_position": [1, 2, 3, 1],
        "probe_prepack_provenance": [witness["capture_generation"] << 2 | witness["logical_probe"],
                                      key(witness)[1], witness["layer"], 0],
        "probe_prepack_revisions": [1, 1, 1, witness["capture_generation"]],
        "presented_surface": {
            "source": key(fg), "state": [1, 1, 0, 0],
            "identity": [3, 0, 2, 140], "world": [1, 2, 3, 1],
            "revisions": [1, 1, 1, fg["capture_generation"]],
        },
        "screen_cell": {"x": 0, "y": 0}, "subcell_slot": 1,
    }


def test_camera_scroll_without_shared_world_samples_is_not_zero_shimmer():
    result = compare_samples({"departed": [value()]}, {"arrived": [value()]})
    assert result["channels"]["fg"]["changed_pct"] is None
    assert result["before_unmatched_sources"] == 1
    assert result["after_unmatched_sources"] == 1


def test_actual_background_change_is_independent_of_foreground():
    result = compare_samples({"same_world": [value()]},
                             {"same_world": [value(bg=0.4)]})
    assert result["channels"]["fg"]["changed_samples"] == 0
    assert result["channels"]["bg"]["changed_samples"] == 1
    assert result["channels"]["bg"]["changed_pct"] == 100


def test_duplicates_are_excluded_even_when_their_colours_agree():
    before = {"ambiguous": [value(), value()], "unique": [value()]}
    after = {"ambiguous": [value(bg=0.4)], "unique": [value()]}
    result = compare_samples(before, after)
    assert result["ambiguous_sources_excluded"] == 1
    assert result["channels"]["bg"]["comparable_samples"] == 1


def test_hidden_ink_is_not_reported_as_visible_ink_instability():
    result = compare_samples({"world": [value(coverage=0)]},
                             {"world": [value(fg=0.9, coverage=0)]})
    assert result["channels"]["fg"]["changed_samples"] == 1
    assert result["visible_channels"]["fg"]["comparable_samples"] == 0


def quarter_fixture():
    fg = np.zeros((32, 32, 3))
    fg[8, 24] = [0.8, 0.6, 0.4]
    field = V2Field()
    field.fields["fg_rgb"] = fg
    field.fields["bg_rgb"] = fg * 0.5
    row = source_row(field)
    return row, field


def test_quarter_source_joins_actual_pixel_without_cell_pair_flattening():
    row, field = quarter_fixture()
    bank, population = samples([row], field)
    np.testing.assert_array_equal(next(iter(bank.values()))[0][0], [0.8, 0.6, 0.4])
    assert population["distinct_sources"] == 1


def test_recapture_generation_does_not_reidentify_the_same_world_point():
    row, field = quarter_fixture()
    before, _ = samples([row], field)
    after_field = V2Field(fg_generation=2, bg_generation=2)
    after, _ = samples([source_row(after_field)], after_field)
    assert compare_samples(before, after)["channels"]["fg"]["comparable_samples"] == 1


def test_elected_sources_replace_visible_prepack_identity_in_the_join():
    row, before_field = quarter_fixture()
    before_field._sources["background"] = dict(before_field._sources["foreground"])
    row = source_row(before_field)
    before, _ = samples([row], before_field)
    after_field = V2Field(fg=0.8, bg=0.9)
    after_field._sources["background"] = dict(after_field._sources["foreground"])
    after_row = source_row(after_field)
    after_row["probe_prepack_identity"] = {"owner_kind": 99}
    after, _ = samples([after_row], after_field)
    result = compare_samples(before, after)
    assert result["channels"]["bg"]["comparable_samples"] == 1
    assert result["channels"]["bg"]["changed_samples"] == 1


def test_elected_owner_switch_is_unmatched_even_when_prepack_identity_is_stable():
    row, before_field = quarter_fixture()
    before, _ = samples([row], before_field)
    after_field = V2Field(fg_texel=(1, 3), bg_texel=(1, 2))
    after_row = source_row(after_field)
    after_row["presented_surface"]["identity"] = [3, 0, 99, 140]
    after, _ = samples([after_row], after_field)
    result = compare_samples(before, after)
    assert result["channels"]["fg"]["comparable_samples"] == 0
    assert result["owner_mismatch"]["fg"] == 1
    assert result["channels"]["bg"]["comparable_samples"] == 1


def test_unresolved_background_keeps_an_exact_foreground_comparison():
    row, field = quarter_fixture()
    row["probe_prepack_provenance"] = [1 << 2, 36, 1, 0]
    before, population = samples([row], field)
    assert population["missing_background_owner_rows"] == 1
    assert population["missing_foreground_owner_rows"] == 0
    after, _ = samples([row], field)
    result = compare_samples(before, after)
    assert result["channels"]["fg"]["comparable_samples"] == 1
    assert result["channels"]["bg"]["comparable_samples"] == 0
    assert result["unresolved_channel_samples"]["bg"] == 1


def test_prepack_identity_cannot_fallback_after_exact_source_mismatch():
    row, field = quarter_fixture()
    row["probe_prepack_provenance"][1] += 1
    bank, population = samples([row], field)
    assert bank
    assert population["missing_background_owner_rows"] == 1


@pytest.mark.parametrize("world_x", [1.000001, 1.125, 2.0])
def test_changed_joined_world_fact_is_unmatched(world_x):
    row, before_field = quarter_fixture()
    before, _ = samples([row], before_field)
    after_field = V2Field()
    after_row = source_row(after_field)
    after_row["presented_surface"]["world"] = [world_x, 2, 3, 1]
    after, _ = samples([after_row], after_field)
    result = compare_samples(before, after)
    assert result["channels"]["fg"]["comparable_samples"] == 0
    assert result["owner_mismatch"]["fg"] == 1


def test_recapture_address_changes_preserve_exact_recorded_content():
    row, before_field = quarter_fixture()
    before, _ = samples([row], before_field)
    after_field = V2Field(fg_texel=(2, 3), bg_texel=(3, 2),
                          fg_generation=2, bg_generation=2)
    after, _ = samples([source_row(after_field)], after_field)
    result = compare_samples(before, after)
    assert result["channels"]["fg"]["comparable_samples"] == 1
    assert result["channels"]["bg"]["comparable_samples"] == 1


def test_presented_content_generation_must_match_exact_source_join():
    row, field = quarter_fixture()
    row["presented_surface"]["revisions"][3] += 1
    bank, population = samples([row], field)
    assert population["missing_foreground_owner_rows"] == 1
    assert population["missing_background_owner_rows"] == 0
    assert compare_samples(bank, bank)["channels"]["fg"]["comparable_samples"] == 0


def test_capture_generation_is_validated_but_not_content_identity():
    row, before_field = quarter_fixture()
    before, _ = samples([row], before_field)
    after_field = V2Field(fg_generation=2, bg_generation=2)
    after, _ = samples([source_row(after_field)], after_field)
    assert compare_samples(before, after)["channels"]["fg"]["comparable_samples"] == 1


def test_missing_v2_owner_facts_fail_closed_without_prepack_fallback():
    row, _ = quarter_fixture()
    field = V2Field(schema="fl4359.final_paint_field.v1")
    bank, population = samples([row], field)
    assert bank == {}
    assert population["missing_actual_owner_rows"] == 1


def test_changed_source_family_epoch_prevents_a_false_comparison():
    row, field = quarter_fixture()
    before, _ = samples([row], field)
    row["probe_prepack_revisions"][2] += 1
    after, _ = samples([row], field)
    assert compare_samples(before, after)["channels"]["fg"]["changed_pct"] is None


def test_one_byte_round_trip_is_not_counted_as_colour_instability():
    result = compare_samples({"world": [value()]},
                             {"world": [value(bg=0.2 + 1 / 255)]})
    assert result["channels"]["bg"]["changed_samples"] == 0


def test_gpu_timeout_invalidates_numeric_payload_evidence(tmp_path):
    log = tmp_path / "godot.log"
    log.write_text("ERROR: timeout waiting for fence\nFL4359_EXTERNAL_DRIVER_RECEIPT receipt.json\n")
    result = runtime_readback_health(log)
    assert result["status"] == "failed"
    assert result["gpu_fence_timeouts"] == 1


def test_missing_runtime_log_cannot_claim_clean_transfer(tmp_path):
    assert runtime_readback_health(tmp_path / "missing.log")["status"] == "unavailable"


from fl4359_actual_field_stability import quarter_pixel, quad_residual_px, mask_error


def test_background_requires_exact_eye_ray_address_and_far_depth():
    from fl4359_actual_field_stability import matches_eye_background
    matrix = np.eye(4)
    matrix[2, 2], matrix[2, 3], matrix[3, 2], matrix[3, 3] = .1, 1, -1, 0
    row = {"probe_source_capture_join_flags": 127, "probe_prepack_hardware_depth": 1.0,
           "probe_raster_candidate_source_contender_count": 0,
           "probe_prepack_provenance": [4, 85, 5, 0],
           "frame_trace": {"world_to_clip_matrix": matrix.flatten(order="F").tolist(),
                           "perspective_probe_state": {"probe_size": 4}}}
    point, extent = np.array([32.5, 32.5]), np.array([64, 64])
    assert matches_eye_background(row, point, extent)
    for key, value in [("probe_prepack_provenance", [4, 84, 5, 0]),
                       ("probe_prepack_provenance", [5, 85, 5, 0]),
                       ("probe_prepack_hardware_depth", .999999),
                       ("probe_raster_candidate_source_contender_count", 1),
                       ("probe_source_capture_join_flags", 126), ("frame_trace", {})]:
        assert not matches_eye_background({**row, key: value}, point, extent)


def test_geometry_uses_native_pixel_center_not_ideal_quarter_coordinate():
    row, field = quarter_fixture()
    np.testing.assert_array_equal(quarter_pixel(row, field.metadata), [24.5, 8.5])


def test_projected_fragment_detects_one_pixel_translation_outside_footprint():
    corners = [[-1, -1, 0, 1], [0, -1, 0, 1], [-1, 0, 0, 1], [0, 0, 0, 1]]
    assert quad_residual_px(np.array([15.5, 8.5]), corners, np.array([32, 32])) == 0
    assert quad_residual_px(np.array([16.5, 8.5]), corners, np.array([32, 32])) == 0.5


def test_missing_or_behind_camera_geometry_is_not_a_zero_jitter_result():
    assert quad_residual_px(np.array([1, 1]), [], np.array([32, 32])) is None
    assert quad_residual_px(np.array([1, 1]), [[0, 0, 0, -1]] * 4, np.array([32, 32])) is None


def test_native_mask_detects_one_pixel_jitter_and_rotation():
    mask = np.zeros((8, 8), dtype=bool)
    mask[1:7, 2] = True
    mask[6, 2:5] = True
    assert mask_error(mask.astype(float), mask)["different_pixels"] == 0
    assert mask_error(np.roll(mask, 1, axis=1).astype(float), mask)["different_pixels"] > 0
    assert mask_error(np.rot90(mask).astype(float), mask)["different_pixels"] > 0


def test_native_wide_glyph_oracle_checks_both_columns_and_detects_half_shift():
    import json
    from fl4359_cell_dump_raster import AtlasPage
    from fl4359_actual_field_stability import observe_presentation
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "godot_project/assets/glyphs/terminal/manifest.json").read_text())
    pages = {}
    for spec in manifest["pages"]:
        spec = dict(spec, path="godot_project/" + spec["path"].removeprefix("res://"),
                    native_terminal=True, native_widths=[float(w) for w in manifest["widths"]])
        pages[spec["page"]] = AtlasPage(spec["page"], spec)
    mask = pages[2].mask(764, 32, 32)
    rows = [{"screen_cell": {"x": x, "y": 0},
             "presented_surface": {"head": [1, 0, 0, glyph]},
             "probe_prepack_valid": False} for x, glyph in enumerate([764, 65])]
    field = SimpleNamespace(metadata={"logical_target_size": [32, 32],
                "cell_size": [16, 32], "semantic_ansi_size": [2, 1]},
                width=32, height=32, fields={"native_intensity": mask.astype(float)})
    correct = observe_presentation(rows, field, pages)
    assert correct["counts"]["mask_mismatch_pixels"] == 0
    field.fields["native_intensity"][:, 16:] = np.roll(mask[:, 16:], 1, axis=0)
    shifted = observe_presentation(rows, field, pages)
    assert shifted["counts"]["mask_mismatch_pixels"] > 0


def test_corrupt_publication_glyph_is_rejected_before_native_layout():
    import pytest
    from fl4359_actual_field import ActualFieldError
    from fl4359_actual_field_stability import observe_presentation
    field = SimpleNamespace(metadata={"logical_target_size": [16, 32],
                                      "cell_size": [16, 32], "semantic_ansi_size": [1, 1]})
    pages = {0: SimpleNamespace(spec={"native_terminal": True, "native_widths": [1] * 1881})}
    rows = [{"screen_cell": {"x": 0, "y": 0},
             "presented_surface": {"head": [1, 1, 1, 1065353216]}}]
    with pytest.raises(ActualFieldError, match="outside width table"):
        observe_presentation(rows, field, pages)


def test_retained_role_promotion_preserves_capture_content_identity():
    row, before_field = quarter_fixture()
    before, _ = samples([row], before_field)
    after_field = V2Field()
    after_field.metadata["source_capture_state"].update(
        captured_origin_valid_mask=3,
        captured_origins_after_capture=[[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]],
        capture_bands=[1, 0],
    )
    for source in after_field._sources.values():
        source["logical_probe"] = 1
        source["reference"] |= 1 << 28
    after, _ = samples([source_row(after_field)], after_field)
    assert compare_samples(before, after)["channels"]["fg"]["comparable_samples"] == 1


def test_actual_retained_generation_can_differ_from_host_layer_epoch():
    row, field = quarter_fixture()
    field.metadata["source_capture_state"]["layer_content_epoch_generations"] = [17] * 18
    bank, population = samples([row], field)
    assert population["missing_actual_owner_rows"] == 0
    assert compare_samples(bank, bank)["channels"]["fg"]["comparable_samples"] == 1


@pytest.mark.parametrize("defect", ["missing_map", "changed_token", "wrong_extent", "wrong_bank"])
def test_exact_source_bank_is_required_before_temporal_comparison(defect):
    row, field = quarter_fixture()
    if defect == "missing_map":
        field.source_generation_maps = {}
    elif defect == "changed_token":
        field.source_generation_maps["front"][1, 2, 1] = 2
    elif defect == "wrong_extent":
        field.source_generation_maps["front"] = np.zeros((18, 7, 7), dtype="<u4")
    else:
        field._sources["foreground"]["bank"] = "underlay"
    bank, population = samples([row], field)
    assert bank == {}
    assert population["missing_actual_owner_rows"] == 1


def test_typed_sky_does_not_borrow_empty_geometry_for_temporal_equality():
    row, field = quarter_fixture()
    source = field._sources["background"]
    source["capture_generation_kind"] = "generated_sky"
    source["geometry_capture"] = False
    field.source_generation_maps["front"][3, 2, 1] = 0x80000001
    bank, population = samples([row], field)
    assert population["missing_foreground_owner_rows"] == 0
    assert population["missing_background_owner_rows"] == 1
    comparison = compare_samples(bank, bank)
    assert comparison["channels"]["fg"]["comparable_samples"] == 1
    assert comparison["channels"]["bg"]["comparable_samples"] == 0


def sky_content_fixture(generation=1):
    import struct
    from fl4359_actual_field_stability import _sky_content_token
    raw = bytearray(400)
    struct.pack_into("<3I", raw, 288, 8, 18, generation)
    struct.pack_into("<4f", raw, 3 * 16, 10, 20, 30, 5)
    struct.pack_into("<18I", raw, 320, *([generation] * 18))
    token = 0x80000000 | generation
    source = {"bank": "front", "reference": 3 * 64 + 18}
    key = ("front", 0, 3 * 64 + 17, token, 3)
    content = {"sky_context_layer": {
        "bank": "front", "physical_layer": 3,
        "selected_sky_generation_token": token,
        "selected_product_texel_shade_params": {"params_hex": raw.hex()},
        "frozen_product_inputs": {
            "product_frame_time_seconds": 5.0, "sun_direction": [0, 1, 0],
            "moon_direction": [0, -1, 0], "cloud_time_seconds": 5.0,
            "night_factor": 0.0, "cloud_opacity": 0.25,
            "source_render_frame_index": 17,
        },
    }}
    return _sky_content_token, content, source, key


def test_sky_temporal_owner_uses_retained_inputs_and_not_capture_epoch():
    resolve, content, source, key = sky_content_fixture()
    before = resolve(content, source, key, "sky")
    resolve, after_content, source, after_key = sky_content_fixture(19)
    after_content["sky_context_layer"]["frozen_product_inputs"]["source_render_frame_index"] = 45
    assert before == resolve(after_content, source, after_key, "sky")
    after_content["sky_context_layer"]["frozen_product_inputs"]["cloud_time_seconds"] = 6.0
    assert before != resolve(after_content, source, after_key, "sky")


@pytest.mark.parametrize("defect", ["bank", "layer", "token", "packed_generation", "packed_extent"])
def test_sky_temporal_owner_rejects_wrong_retained_producer(defect):
    import struct
    from fl4359_actual_field import ActualFieldError
    resolve, content, source, key = sky_content_fixture()
    record = content["sky_context_layer"]
    if defect == "bank":
        record["bank"] = "underlay"
    elif defect == "layer":
        record["physical_layer"] = 4
    elif defect == "token":
        record["selected_sky_generation_token"] += 1
    else:
        raw = bytearray.fromhex(record["selected_product_texel_shade_params"]["params_hex"])
        struct.pack_into("<I", raw, 296 if defect == "packed_generation" else 288, 2)
        record["selected_product_texel_shade_params"]["params_hex"] = raw.hex()
    with pytest.raises(ActualFieldError):
        resolve(content, source, key, "sky")


def test_sky_temporal_owner_requires_source_inputs_and_exact_origin():
    import struct
    resolve, content, source, key = sky_content_fixture()
    before = resolve(content, source, key, "sky")
    record = content["sky_context_layer"]
    raw = bytearray.fromhex(record["selected_product_texel_shade_params"]["params_hex"])
    struct.pack_into("<f", raw, 3 * 16, 11.0)
    record["selected_product_texel_shade_params"]["params_hex"] = raw.hex()
    assert before != resolve(content, source, key, "sky")
    record["frozen_product_inputs"].pop("sun_direction")
    assert resolve(content, source, key, "sky") is None


def test_underlay_cannot_borrow_front_content_at_identical_physical_address():
    row, field = quarter_fixture()
    underlay = {name: dict(source, bank="underlay") for name, source in field._sources.items()}
    field.source_generation_maps["underlay"] = field.source_generation_maps["front"].copy()
    field.pixel_provenance = lambda _x, _y: {
        "front": field._sources, "underlay": underlay, "underlay_used": True}
    bank, population = samples([row], field)
    assert population["missing_foreground_owner_rows"] == 0
    assert population["missing_background_owner_rows"] == 1
    assert compare_samples(bank, bank)["channels"]["bg"]["comparable_samples"] == 0


def complete_content(field):
    def read(reference, bank):
        address = reference & 0x0fffffff
        layer, local = divmod(address-1, 64)
        token = int(field.source_generation_maps['underlay' if bank else 'front'][layer,local//8,local%8])
        return {'status':'ok', 'key':[address,bank,token,1],
                'identity':[3,0,2,140], 'world':[1,2,3,1], 'revisions':[1,1,1,token],
                'normal_depth':[0,1,0,5], 'anchor':[1,2,3,0],
                'colour':[0,0,0,0], 'surface_meta':[0,0,0,1]}
    field.source_content = read


def test_complete_native_content_supplies_paper_without_sparse_elected_witness():
    field=V2Field(); complete_content(field); row=source_row(field)
    row['probe_prepack_provenance']=[4,0,0,0]
    result=samples([row],field)
    assert result[1]['missing_background_owner_rows']==0


def test_complete_native_sky_context_reaches_independent_background_comparator():
    row, field = quarter_fixture()
    complete_content(field)
    geometry_reader = field.source_content
    source = field._sources["background"]
    source["capture_generation_kind"] = "generated_sky"
    source["geometry_capture"] = False
    field.source_generation_maps["front"][3, 2, 1] = 0x80000001
    _, sky, _, _ = sky_content_fixture()

    def read(reference, bank):
        result = geometry_reader(reference, bank)
        if reference == source["reference"]:
            result["key"][3] |= 4
            result.update(sky)
        return result

    field.source_content = read
    observed, population = samples([row], field)
    assert population["missing_background_owner_rows"] == 0
    assert compare_samples(observed, observed)["channels"]["bg"]["comparable_samples"] == 1


def test_complete_content_missing_does_not_fall_back_to_sparse_fact():
    field=V2Field();field.source_content=lambda _reference,_bank: {'status':'missing'}
    result=samples([source_row(field)],field)
    assert result[1]['missing_foreground_owner_rows']==1


@pytest.mark.parametrize("change,comparable", [
    ("normal", 0), ("pose_colour", 0), ("material_meta", 0),
    ("capture_depth", 1), ("payload_output", 1),
])
def test_temporal_capture_inputs_distinguish_changes_from_output_and_origin_depth(change, comparable):
    field = V2Field()
    complete_content(field)
    row = source_row(field)
    before, _ = samples([row], field)
    read = field.source_content

    def changed(reference, bank):
        content = read(reference, bank)
        if change == "normal":
            content["normal_depth"][0] = 0.5
        elif change == "pose_colour":
            content["colour"][0] = 0.5
        elif change == "material_meta":
            content["surface_meta"][1] = 123
        elif change == "capture_depth":
            content["normal_depth"][3] = 123
        else:
            content["payload"] = [123, 456, 789, 0]
        return content

    field.source_content = changed
    after, _ = samples([row], field)
    assert compare_samples(before, after)["channels"]["fg"]["comparable_samples"] == comparable


def test_complete_source_missing_actor_or_meadow_facts_stays_unresolved():
    field = V2Field()
    complete_content(field)
    read = field.source_content

    def missing(reference, bank):
        content = read(reference, bank)
        content.pop("colour")
        return content

    field.source_content = missing
    _, population = samples([source_row(field)], field)
    assert population["missing_foreground_owner_rows"] == 1


def test_complete_content_wrong_bank_is_rejected():
    field=V2Field();complete_content(field);read=field.source_content
    def wrong(reference,bank):
        value=read(reference,bank);value['key'][1]=1-bank;return value
    field.source_content=wrong
    result=samples([source_row(field)],field)
    assert result[0] == {}
    assert result[1]['missing_actual_owner_rows']==1


def test_complete_content_stale_generation_is_rejected():
    field=V2Field();complete_content(field);read=field.source_content
    def stale(reference,bank):
        value=read(reference,bank);value['key'][2]=17;return value
    field.source_content=stale
    result=samples([source_row(field)],field)
    assert result[0] == {}
    assert result[1]['missing_actual_owner_rows']==1


def test_complete_content_contradiction_with_sparse_is_rejected():
    field=V2Field();complete_content(field);read=field.source_content
    def wrong(reference,bank):
        value=read(reference,bank);value['world'][0]+=1;return value
    field.source_content=wrong
    result=samples([source_row(field)],field)
    assert result[0] == {}
    assert result[1]['missing_actual_owner_rows']==1


def test_underlay_mix_ignores_replaced_paper_but_requires_exact_blend():
    from fl4359_actual_field_stability import _actual_field_owner_key
    field=V2Field();complete_content(field)
    under={name:dict(source,bank='underlay') for name,source in field._sources.items()}
    field.source_generation_maps['underlay']=field.source_generation_maps['front'].copy()
    provenance={'front':field._sources,'underlay':under,'underlay_used':True,'underlay_intensity':.25}
    field.pixel_provenance=lambda _x,_y: provenance
    first=_actual_field_owner_key(field,24,8,{},set(),{})
    assert first[1][0]=='underlay_mix'
    # Changed front paper does not contribute to the used underlay mix.
    field._sources['background']={'present':False, 'reference':0}
    second=_actual_field_owner_key(field,24,8,{},set(),{})
    assert first[1]==second[1]
    provenance['underlay_intensity']=.75
    third=_actual_field_owner_key(field,24,8,{},set(),{})
    assert first[1]!=third[1]


def test_sky_temporal_key_excludes_dispatch_admission_but_keeps_ray_range():
    import struct
    resolve, content, source, key = sky_content_fixture()
    before = resolve(content, source, key, "sky")
    record = content["sky_context_layer"]
    raw = bytearray.fromhex(record["selected_product_texel_shade_params"]["params_hex"])
    struct.pack_into("<f", raw, 3 * 16 + 12, 7)
    struct.pack_into("<I", raw, 300, 2)
    struct.pack_into("<f", raw, 316, 1)
    struct.pack_into("<18I", raw, 320, *range(50, 68))
    record["selected_product_texel_shade_params"]["params_hex"] = raw.hex()
    assert before == resolve(content, source, key, "sky")
    struct.pack_into("<f", raw, 308, 512)
    record["selected_product_texel_shade_params"]["params_hex"] = raw.hex()
    assert before != resolve(content, source, key, "sky")
