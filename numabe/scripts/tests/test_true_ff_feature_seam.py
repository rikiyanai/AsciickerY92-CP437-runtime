"""FL-4907 INTEGRATE_LANE_FEATURE_RENDERER_WIRED_20260930: the seam between
TrueFFFrameRenderer._render_features and its callees.

The frame renderer builds three dictionaries for TrueFFFeatureRenderer.render
(PASSES camera facts, RASTER page, P0 prepass) and reads the frame's
page_facts and retained scene facts. This static test parses the GDScript and
requires every built key set to equal the set the callee actually reads
(`.get("key"` and `["key"]` in the callee source), so the seam cannot drift
when either side changes. No Godot launch.
"""
import re
import itertools
import math
import pytest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
ASCII = ROOT / "godot_project" / "render" / "ascii"
FRAME = ASCII / "composition" / "TrueFFFrameRenderer.gd"
FEATURES = ASCII / "features" / "TrueFFFeatureRenderer.gd"
PASSES = ASCII / "features" / "TrueFFFeaturePasses.gd"
BANK = ASCII / "features" / "TrueFFFeatureBank.gd"
COMPOSITOR = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"


def _function(source: str, name: str) -> str:
    start = re.search(rf"^(?:static )?func {name}\(", source, re.M)
    assert start, name
    end = re.search(r"^(?:static )?func |\Z", source[start.end():], re.M)
    return source[start.start():start.end() + end.start()]


def _dict_keys(body: str, var: str) -> set:
    match = re.search(rf"var {var}\s*(?::\s*Dictionary\s*)?:?=\s*\{{(.*?)\n\t\}}", body, re.S)
    if match is None:
        match = re.search(rf"var {var}\s*(?::\s*Dictionary\s*)?:?=\s*\{{(.*?)\}}", body, re.S)
    assert match, var
    return set(re.findall(r'"(\w+)"\s*:', match.group(1)))


def _reads(source: str, name: str) -> set:
    """Keys a function body reads from the dictionary parameter `name`."""
    return set(re.findall(rf'\b{name}\.get\("(\w+)"', source)) \
        | set(re.findall(rf'\b{name}\["(\w+)"\]', source))


FRAME_SRC = FRAME.read_text(encoding="utf-8")
FEATURES_SRC = FEATURES.read_text(encoding="utf-8")
PASSES_SRC = PASSES.read_text(encoding="utf-8")
SEAM = _function(FRAME_SRC, "_render_features")


def test_p5_preparation_and_submission_have_separate_ownership():
    """Source contract, not a claim that the incomplete frame integration runs."""
    prepare = _function(FEATURES_SRC, "prepare")
    render = _function(FEATURES_SRC, "render")
    assert "prepass: Dictionary, target: RID, passes: Dictionary" in render
    assert "prepare_language(rd)" in prepare and "_page(extent, site_capacity)" in prepare
    assert "_setup()" in _function(FEATURES_SRC, "prepare_language")
    for forbidden in ("_bank", "_passes", "Passes.new", "Bank.new"):
        assert forbidden not in FEATURES_SRC
    for forbidden in ("_setup", "_page", "compile_compute", "configure"):
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(forbidden)}\(", render) is None
    assert "true_ff_feature_prepare_required" in render
    assert "true_ff_feature_prepare_capacity_mismatch" in render
    consumed = _reads(render, "passes") | _reads(FEATURES_SRC, "pipeline_facts")
    assert consumed == {
        "sites", "args", "sites_args_offset", "site_capacity", "site_bytes",
        "tangent_in_sites", "pipeline_overflow_word",
    }


def test_p5_does_not_retry_upstream_or_claim_incomplete_layout_as_complete():
    assert "recut_native_rows" not in FEATURES_SRC
    assert "p5_layout_incomplete" in _function(FEATURES_SRC, "_on_text_part")
    render = _function(FEATURES_SRC, "render")
    assert render.count("if not _layout(") == 1
    assert "for round" not in render
    assert 'var top0: float = NATIVE_PAGE_ORIGIN.y' in render
    assert 'var x0: float = NATIVE_PAGE_ORIGIN.x' in render


def test_call_order_and_signatures():
    execute = SEAM.index("_passes.execute(_bank.bank_facts(), passes_camera, prepass,")
    render = SEAM.index("_features.render(")
    assert execute < render
    assert "prepass: Dictionary, target: RID, passes: Dictionary" in _function(FEATURES_SRC, "render")
    assert 'if not bool(passes.get("valid", false)):' in SEAM
    assert "_features.sync_bank(" not in FRAME_SRC
    assert "view_index: int = 0) -> Dictionary:" in _function(PASSES_SRC, "execute")
    assert 'int(page["view_index"])' in SEAM
    preparation = _function(FRAME_SRC, "prepare_residency")
    assert preparation.index("_bank.prepare_residency(") < preparation.index("_gbuffer.prepare_scene(")
    assert preparation.index("_gbuffer.prepare_scene(") < preparation.index("_passes.prepare_views(")
    assert preparation.index("_passes.prepare_views(") < preparation.index("_features.prepare(")
    assert "_passes.site_capacity()" in preparation
    assert "_passes.prepare(" not in SEAM


def test_pass_preparation_requires_page_metrics_not_a_dummy_camera():
    preparation = _function(PASSES_SRC, "prepare") + _function(PASSES_SRC, "reconcile_publication")
    assert "_valid_page_facts(page_facts)" in preparation
    assert "_valid_frame_camera(" not in preparation
    assert "_camera_bytes(" not in preparation
    assert "_valid_frame_camera(camera_facts)" in _function(PASSES_SRC, "execute")
    borrow = _function(FRAME_SRC, "_prepass_facts")
    assert '"language": _features.language_facts()' in borrow
    assert '"static_hierarchy": _gbuffer.static_hierarchy()' in borrow


def test_shadow_submission_uses_retained_bounds_and_real_motion_witness():
    frame = _function(FRAME_SRC, "render_frame")
    assert frame.index("_gbuffer.publish(scene)") < frame.index("_gbuffer.shadow_scene_facts()")
    assert frame.index("_gbuffer.shadow_scene_facts()") < frame.index("GBuffer.light_view(")
    assert frame.index("GBuffer.light_view(") < frame.index("_gbuffer.draw_light(")
    for name in ("receiver_slab", "terrain_domain", "caster_bounds"):
        assert f'shadow_facts["{name}"]' in frame
    world = COMPOSITOR.read_text()
    resident = _function(world, "true_ff_resident_sources")
    assert '"rigid": [_rigid_buffers.get_instance_id(),' in resident
    assert "_rigid_buffers.motion_generation()" in resident
    assert 'source_omissions["shadow"]' in frame


def test_world_family_sources_reach_the_same_bank():
    prepare = _function(FRAME_SRC, "prepare_residency")
    assert prepare.index("_figures.prepare(figure_scene)") < prepare.index("_bank.prepare_residency(device, scene_db")
    assert '_bank.apply_changes("figures"' in prepare
    assert '_bank.apply_changes("actors"' in prepare
    assert '_bank.apply_changes("rigid"' in prepare
    assert "_world_sources.prepare(device, scene)" in prepare
    assert "_world_sources.apply_changes(residency_events)" in prepare
    assert '"omitted_producers": _preparation_omissions.duplicate()' in prepare


def _legacy_world_family_sources_reach_the_same_bank():
    # Source preparation lives in _prepare_feature_sources; _render_features
    # consumes its publication and syncs the one bank.
    prepare = _function(FRAME_SRC, "_prepare_feature_sources")
    assert '_actor_features.feature_sources_with_receipt(scene.get("actor_pose_db"))' in prepare
    assert '_rigid_features.sources(scene.get("scene_db"))' in prepare
    # FL-4907 RETAINED_INSTANCE_TABLE_AND_BANK_KEY_20261002: one retained
    # array per producer (feature_producers); no flat per-frame source list.
    assert 'source_facts["feature_producers"] = producers' in prepare
    assert 'source_facts["feature_sources"]' not in prepare
    for producer in ("actor", "rigid", "figure", "world"):
        assert f'producers["{producer}"] = _retained_sources("{producer}",' in prepare, producer
    assert 'var rigid: Dictionary = _rigid_features.receipt()' in prepare
    assert 'var resident_hashes: Array = actors["resident_hashes"].duplicate()' in prepare
    assert 'resident_hashes.append_array(rigid["resident_hashes"])' in prepare
    assert ('source_facts["feature_resident_hashes"] = _retained("feature_resident_hashes", '
            'resident_hashes)') in prepare
    # N11: the omitted set is serialized only when it changed.
    assert prepare.index("omitted != _last_producer_omissions") < prepare.index("JSON.stringify(omitted)")
    assert "_features.sync_bank(" in SEAM
    # Operator decision 2026-10-02: a producer that is not ready is omitted by
    # name and logged; it never rejects (blanks) the whole frame.
    for producer in ("actor", "rigid", "figure"):
        assert f'omitted["{producer}"]' in prepare, producer
    assert '"omitted_producers": omitted' in prepare
    assert "push_error(\"TrueFFFrameRenderer: producers omitted" in prepare
    assert "moving_feature_publication_rejected" not in prepare
    # Per-asset figure rejections are still named in the log when the
    # producer itself is ready.
    assert 'omitted["figure_assets"] = figure["rejection_reasons"]' in prepare
    # World families are omitted by name in the same log/receipt; the logging
    # runs after the world publication so family omissions are included.
    assert 'omitted["world." + family] = omitted_families[family]' in prepare
    assert '"omitted_families": omitted_families' in prepare
    assert prepare.index("_world_features.publication(") < prepare.index("push_error(")
    bounds = _function(FRAME_SRC, "_deform_bounds")
    assert 'actors["deform_bounds"].duplicate()' in bounds
    assert 'rigid["deform_bounds"]' in bounds
    assert "maxf(value.x, existing.x)" in bounds
    assert "maxf(value.y, existing.y)" in bounds


FIGURE = ASCII / "features" / "TrueFFFigureFeatures.gd"
WORLD = ASCII / "features" / "TrueFFWorldFeatureSources.gd"


def test_figure_readiness_is_per_asset():
    # Operator decision 2026-10-02 (FL-4907): one invalid figure asset never
    # drops the others. Only "*" (every-asset failure) makes it not ready.
    source = FIGURE.read_text(encoding="utf-8")
    receipt = _function(source, "receipt")
    assert '"ready": _prepared and not _rejections.has("*")' in receipt
    assert "_rejections.is_empty()" not in receipt
    prepare = _function(source, "prepare")
    assert '_rejections[asset_key] = asset.get("reason", "figure_sidecar_invalid")' in prepare
    assert "for source: Dictionary in _instance_sources(" in prepare
    assert "next[identity] = source" in prepare
    withdraw = _function(source, "withdraw")
    assert '_rejections["*"] = reason' in withdraw


def test_world_publication_omits_families_not_the_frame():
    publication = _function(WORLD.read_text(encoding="utf-8"), "_receipt")
    assert "var omitted: Dictionary = _family_rejections.duplicate(false)" in publication
    assert 'omitted[family + ".sources"] = errors' in publication
    assert '"publication_ready": _prepared' in publication
    assert '"omitted_families": omitted' in publication
    assert '"water": water, "effects": effects, "sky": sky' in publication


def _legacy_world_publication_omits_families_not_the_frame():
    publication = _function(WORLD.read_text(encoding="utf-8"), "_receipt")
    # A not-ready family is named and contributes no sources or banks.
    assert 'omitted[family] = {"reason": String(lane.get("reason", "world_source_rejected")),' in publication
    loop = publication[publication.index("for family: String in families:"):
                       publication.index("var sea: float")]
    assert loop.index("continue") < loop.index("sources.append_array(")
    assert '"water": water, "effects": effects, "sky": sky, "shadows": shadows' in publication
    assert '"omitted_families": omitted' in publication
    # Site buffer failure omits water, never returns a frame rejection.
    assert 'omitted["water"] = {"reason": "world_waterfall_site_buffer_missing"' in publication
    assert publication.count('return {"ready": false') == 1
    assert '"world_feature_device_or_scene_missing"' in publication
    # Omitted water/sky bind the inert {} set-1 facts; every output key stays.
    assert 'var water_bindings: Dictionary = {} if omitted.has("water")' in publication
    assert 'var sky_bindings: Dictionary = {} if omitted.has("sky")' in publication
    for key in ("live_bindings", "feature_sources", "feature_banks",
                "waterfall_site_field", "water_flow_runtime"):
        assert f'"{key}":' in publication, key
    # Readiness is publication-level only (bindings), not any family's.
    assert '"ready": reasons.is_empty()' in publication
    assert "reasons[String(lane.get(" not in publication


def test_active_instance_counts_do_not_multiply_terrain_by_other_objects():
    facts = _function(BANK.read_text(), "bank_facts")
    assert '"instance_node_total": _instance_node_total' in facts
    assert '"instance_feature_total": _instance_feature_total' in facts
    assert "bank.receipt()" not in facts
    assert "count * max_" not in facts
    # Sums of resident slots, maintained per committed/withdrawn slot.
    count = _function(BANK.read_text(), "_count_slot")
    assert "_instance_node_total += direction * _rows.decode_u32(base + 8)" in count
    assert "_instance_feature_total += direction * _rows.decode_u32(base + 12)" in count


def test_passes_camera_keys_equal_what_the_passes_read():
    built = _dict_keys(SEAM, "passes_camera")
    camera_read = _reads(_function(PASSES_SRC, "_camera_bytes"), "facts")
    capacity_read = _reads(_function(PASSES_SRC, "_ensure_buffers"), "camera_facts")
    # Native capacity is prepared from the language owner's exact minimum
    # advance, not added to the per-frame camera ABI as a second constant.
    reconcile = _function(PASSES_SRC, "reconcile_publication")
    assert 'native_facts["minimum_advance"] = SHADOW_ACCOUNT_ADVANCE' in reconcile
    assert '_ensure_buffers(bank, native_facts, pixels)' in reconcile
    read = camera_read | (capacity_read - {"minimum_advance"}) | _reads(_function(PASSES_SRC, "execute"), "camera_facts")
    assert built == read
    assert built == {"world_to_clip", "eye", "forward", "orthographic", "page_size", "advance",
                     "row_pitch", "arc_quantum", "viewport"}
    # Numbers come from their owners, never restated here.
    assert '"arc_quantum": FeaturePasses.ARC_QUANTUM' in SEAM
    for key in ("advance", "row_pitch"):
        assert f'"{key}": float(_font_metrics["{key}"])' in SEAM
    assert "const ARC_QUANTUM := 1.0 / 1024.0" in PASSES_SRC


def test_raster_page_keys_equal_what_the_feature_renderer_reads():
    built = _dict_keys(SEAM, "raster_page")
    read = _reads(FEATURES_SRC, "page")
    assert built == read, (built ^ read)


def test_export_snapshots_actual_page_transform_and_p6_pixel_rounding():
    queue = _function(FEATURES_SRC, "_export_text_frame")
    emit = _function(FEATURES_SRC, "_on_text_part")
    for key in ("translation", "viewport", "scale"):
        assert f'page["{key}"]' in queue
        assert f'state["{key}"]' in emit
    assert '"page_shift_px": [translation.x, translation.y]' in emit
    assert '"size_px": [viewport.x * scale, viewport.y * scale]' in emit
    assert "floori(items.decode_float(at) + 0.5)" in emit
    assert "floori(items.decode_float(at + 4) + 0.5)" in emit
    glyphs = (ASCII / "features" / "true_ff_feature_glyphs.glsl").read_text()
    assert "ivec2(floor(instance.placement.xy+vec2(0.5)))" in glyphs


def _production_text_rows():
    """Execute the production scalar serialization, without launching Godot.

    Only GDScript container/string syntax is adapted. This does not assert
    Godot semantic compilation, GPU placement or rendered paint parity.
    """
    source = (ASCII / "features" / "TrueFFTextRows.gd").read_text()
    scope = {"ROW_PITCH": 17, "FULL_ADVANCE": 11, "HALF_ADVANCE": 5,
             "floori": math.floor, "posmod": lambda x, y: x % y,
             "maxi": max, "mini": min, "String": str,
             "EMPTY_EXTENT": SimpleNamespace(x=0, y=0)}
    for name, signature in (("_before", "a, b"), ("spacing", "distance, leading"),
                            ("_spaces", "out, spelling, pen, top, flags, fg, bg"),
                            ("serialize", "chars, extent=EMPTY_EXTENT, paper=b''")):
        body = re.split(r"\) -> \w+:\n", _function(source, name), maxsplit=1)[1]
        body = re.sub(r"\bvar (\w+): [^=\n]+ =", r"\1 =", body)
        body = body.replace("for entry: Array in", "for entry in")
        body = body.replace("PackedStringArray()", "[]")
        body = body.replace("chars.duplicate()", "list(chars)")
        body = re.sub(r"\b(\w+)\.(?:size|length)\(\)", r"len(\1)", body)
        body = re.sub(r"\b(\w+)\.is_empty\(\)", r"(not \1)", body)
        body = body.replace("ordered.back()", "ordered[-1]")
        body = body.replace("spelling.unicode_at(i)", "ord(spelling[i])")
        body = body.replace("paper.decode_u32((row_index * columns + column) * 4)",
                            "int.from_bytes(paper[(row_index * columns + column) * 4:"
                            "(row_index * columns + column + 1) * 4], 'little')")
        body = body.replace("ordered.sort_custom(_before)",
                            "ordered.sort(key=lambda item: (int(item[2]), int(item[1])))")
        body = body.replace("String.chr", "chr").replace("spaces.length()", "len(spaces)")
        body = body.replace('" ".repeat(prefix)', '(" " * prefix)')
        body = body.replace('" ".repeat(here)', '(" " * here)')
        body = body.replace('"\\n".repeat(next_row - row)', '("\\n" * (next_row - row))')
        body = body.replace(" / HALF_ADVANCE", " // HALF_ADVANCE")
        body = body.replace(" / ROW_PITCH", " // ROW_PITCH")
        body = body.replace(" / FULL_ADVANCE", " // FULL_ADVANCE")
        body = re.sub(r"\btrue\b", "True", body)
        body = re.sub(r"\bfalse\b", "False", body)
        exec(f"def {name}({signature}):\n" + body, scope)
    return scope


def test_text_export_spells_the_production_spacing_law_exhaustively():
    rows = _production_text_rows()
    for leading in (False, True):
        for gap in range(600):
            result = rows["spacing"](gap, leading)
            solutions = [(a, b) for a in range(gap // 11 + 1)
                         for b in [(gap - 11 * a) // 5]
                         if 11 * a + 5 * b == gap and b <= 2 * a + (0 if leading else 2)]
            assert result["valid"] == bool(solutions), (gap, leading)
            if result["valid"]:
                spelling = result["text"]
                assert sum(11 if c == "\u3000" else 5 for c in spelling) == gap
                assert "   " not in spelling
                assert not leading or not spelling.startswith(" ")


def test_text_export_reproduces_pens_with_real_spaces_and_newlines():
    rows = _production_text_rows()
    # Shuffle the readback arena, including missing rows and a mixed-width row.
    chars = [[ord("/"), 22, 0, 7, 0, 1, 2], [ord("|"), 45, 0, 3, 0, 1, 2],
             [ord("≈"), 0, 34, 11, 4, 3, 4]]
    for shuffled in itertools.permutations(chars):
        result = rows["serialize"](list(shuffled))
        assert result["valid"]
        assert result["text"] == "\u3000\u3000/\u3000 |\n\n≈"
        x, y, visited = 0, 0, []
        by_offset = dict(zip(result["glyph_offsets"], result["chars"]))
        for offset, char in enumerate(result["text"]):
            if char == "\n":
                x, y = 0, y + 17
            elif char in (" ", "\u3000"):
                x += 5 if char == " " else 11
            else:
                entry = by_offset[offset]
                assert (ord(char), x, y) == tuple(entry[:3])
                x += entry[3]
                visited.append(entry)
        assert visited == [entry for entry in result["chars"] if entry[0] not in (32, 0x3000)]
        # Added spaces carry the same row paper as the next glyph, not an
        # invented default background; serialization preserves native pens.
        assert all(entry[5:7] == [1, 2] for entry in result["chars"] if entry[2] == 0)
    emit = _function(FEATURES_SRC, "_on_text_part")
    assert 'TextRows.serialize(chars,' in emit
    assert '"text": text_page.get("text", "")' in emit
    assert '"text_layout_valid": text_page["valid"]' in emit


@pytest.mark.parametrize("chars,reason", [
    ([[47, 5, 0, 7]], "unspellable_gap_or_overlap"),  # forbidden leading half
    ([[47, 0, 0, 7], [124, 5, 0, 3]], "unspellable_gap_or_overlap"),
    ([[47, 0, 1, 7]], "row_outside_page"),
    ([[47, 0, -17, 7]], "row_outside_page"),
])
def test_text_export_does_not_repair_or_hide_unreproducible_layout(chars, reason):
    chars = [entry + [0, 1, 2] for entry in chars]
    result = _production_text_rows()["serialize"](chars)
    assert not result["valid"] and result["reason"] == reason
    assert "text" not in result
    assert len(result["chars"]) == len(chars)


def test_world_and_hud_share_fixed_native_rows_before_page_translation():
    render = _function(FEATURES_SRC, "render")
    assert 'const NATIVE_PAGE_ORIGIN: Vector2 = Vector2.ZERO' in FEATURES_SRC
    assert 'var top0: float = NATIVE_PAGE_ORIGIN.y' in render
    assert 'var x0: float = NATIVE_PAGE_ORIGIN.x' in render
    assert '"native_origin": NATIVE_PAGE_ORIGIN' in _function(FEATURES_SRC, "language_facts")
    assert '_prepared_language["native_origin"]' in _function(PASSES_SRC, "_dispatch_native_certificate")
    assert 'page_residual_px' not in render
    assert 'page_residual_px' not in SEAM


def test_p5_preserves_p1_certificate_without_late_feature_admission():
    layout = (ASCII / "features" / "true_ff_feature_layout.glsl").read_text()
    coverage = (ASCII / "features" / "true_ff_feature_coverage.glslinc").read_text()
    prepare = layout.split("void prepare_node(", 1)[1].split("#include", 1)[0]
    assert 'idiom=int(s.key.y)' in prepare
    assert 'int origin=int(s.tangent.z)' in prepare
    assert 'node.local0.x=0' in prepare
    assert 'if(!native_certified(source) || native_member(source)) return false;' in coverage
    assert 'tff_feature_select_directed' not in prepare
    assert 'representative_idiom(' not in prepare
    assert 'coverage_solve' not in coverage
    assert 'coverage_existing_group' not in coverage
    assert '_coverage_rows' not in FEATURES_SRC
    assert 'owners!=1u || native_member(s)' in layout
    assert '!certificate_member_valid(sites.items[node.meta.x],sites.items[owner])' in layout
    assert 'native_member(sites.items[node.meta.x])!=(node.meta.z!=0u)' in layout
    stage = coverage.split('void stage_coverage(', 1)[1]
    # An uncovered owed cell is reported for its row; the row still paints.
    assert 'coverage_cell_owes_mark' in stage and 'report_row(' in stage and 'reject_row(' not in stage
    for forbidden in ('coverage_nominate', 'coverage_dot_glyph', 'coverage_insertion',
                      'sites.items[address]=', 'instances.items[first+', 'coverage_refit('):
        assert forbidden not in stage
    assert 'coverage_nominate' not in coverage
    page = _function(FEATURES_SRC, '_page')
    assert '_storage(site_capacity * SITE_BYTES' in page
    assert 'coverage_cells' not in page and 'paint_site_capacity' not in page
    execute = _function(PASSES_SRC, 'execute')
    cut = _function(PASSES_SRC, '_record_cut_round')
    assert execute.index('SHADOW_PATH, [2, 0, 0, revision]') < execute.index(
        '_record_cut_round(')
    assert cut.index('DISCOVER_PATH, [0, 0, 0, 0]') < cut.index('TRAVERSE_KERNELS[3], [3, 0, 0, revision]')
    assert cut.index('TRAVERSE_KERNELS[3], [3, 0, 0, revision]') < cut.index(
        'SITES_PATH, [0, 0, 0, revision]') < cut.index('_dispatch_native_certificate(list,')
    assert execute.index('_record_cut_round(') < execute.index(
        'SHADOW_PATH, [3, 0, 0, revision]') < execute.index('SITES_PATH, [1, 0, 0, revision]')
    assert execute.index('SITES_PATH, [1, 0, 0, revision]') < execute.index('VISIBILITY_PATH')
    assert execute.index('VISIBILITY_PATH') < execute.index('TRAVERSE_KERNELS[14],')
    assert execute.index('VISIBILITY_PATH') < execute.index('TRAVERSE_KERNELS[7],')


def test_owed_admission_is_page_owned_and_precedes_output_publication():
    """Host/GLSL ABI and order only; not a GPU coverage result."""
    execute = _function(PASSES_SRC, 'execute')
    cut = _function(PASSES_SRC, '_record_cut_round')
    owed = _function(PASSES_SRC, '_dispatch_owed')
    assert '"owed": 64 + 2 * site_slots * 160' in PASSES_SRC
    assert '59: "owed"' in PASSES_SRC
    assert '62: "native_placements", 63: "native_coverage"' in PASSES_SRC
    assert 'HudText.RESERVED_ROWS.x, HudText.RESERVED_ROWS.y' in owed
    assert 'compute_list_dispatch_indirect(list, _buffers["owed"], 32)' in owed
    assert '1 if stage == 4 else GROUP' in owed
    assert execute.index('for stage in [0, 1, 24, 25, 26, 11, 20, 21, 22,') < execute.index('_record_cut_round(')
    assert execute.index('12, 13, 14, 15, 2, 8') < execute.index('_record_cut_round(')
    assert cut.index('_dispatch_owed(list, 7,') < cut.index('DISCOVER_PATH, [0,')
    assert cut.index('_dispatch_owed(list, 3,') < cut.index('_dispatch_owed(list, 5,')
    assert cut.index('_dispatch_owed(list, 5,') < cut.index(
        '_dispatch_native_certificate(list, cut_round, final_round, [0, 1, 2])')
    assert cut.index('_dispatch_owed(list, 4,') < cut.index('_dispatch_owed(list, 6,')
    assert cut.index('_dispatch_owed(list, 6,') < cut.index(
        '_dispatch_native_certificate(list, cut_round, final_round, [3])')
    shadow_sequence = '[0, 7, 4, 5, 6, 0, 1, 2]'
    assert shadow_sequence in execute
    shadow_solve = execute.index(shadow_sequence)
    assert execute.index('SHADOW_PATH, [2, 0, 0, revision]') < shadow_solve
    assert shadow_solve < execute.index('_dispatch_owed(list, 9,')
    native = _function(PASSES_SRC, '_dispatch_native_certificate')
    assert '1, 6, 7:' in native
    assert '2, 4:' in native
    assert '3, 5:' in native
    # A certificate tag must never become an indirect dispatch dimension.
    native = (ASCII / 'features' / 'true_ff_native_row_admission.glslinc').read_text()
    work = (ASCII / 'features' / 'true_ff_feature_work.glslinc').read_text()
    assert 'TFF_NATIVE_CERTIFICATE_WORD = 59u * 4u' in native
    assert 'TFF_SHADOW_PLAN_WORD = 59u * 4u + 1u' in native
    assert 'TFF_SLOT_COUNT = 60u' in work
    assert 'const SLOT_COUNT := 60' in PASSES_SRC
    assert 'for cut_round in range(cut_rounds)' in execute
    assert 'gpu_admission_result_not_read_back' in _function(PASSES_SRC, '_submission_receipt')
    for name in ('true_ff_feature_native.glsl', 'true_ff_feature_discover.glsl',
                 'true_ff_feature_passes.glslinc', 'true_ff_shadow_admission.glslinc',
                 'true_ff_shadow_boundary.glsl'):
        source = (ASCII / 'features' / name).read_text()
        assert 'TFF_SHADOW_PLAN_WORD' in source
        assert not re.search(r'TFF_SLOT_CANDIDATES\s*\*\s*4u\s*\+\s*2u', source)


def test_native_page_edge_rows_require_complete_ink_not_empty_cell_padding():
    native = (ASCII / "features" / "true_ff_feature_native.glsl").read_text()
    layout = (ASCII / "features" / "true_ff_feature_layout.glsl").read_text()
    prepare = native.split("bool prepare_spelling(", 1)[1].split("\nbool ", 1)[0]
    node = layout.split("void prepare_node(", 1)[1].split("\n}\n", 1)[0]
    assert 'top+float(TFF_FEATURE_ROW_PITCH)>pc.page.y' not in prepare
    assert 'top+float(TFF_FEATURE_ROW_PITCH)<=float(pc.sizes.w)' not in node
    assert 'top+float(upper)<0.0 || top+float(bottom)>pc.page.y' in prepare
    assert 'top+float(metric.ink.y)>=0.0' in node
    assert 'top+float(metric.ink.y+metric.ink.w)<=float(pc.sizes.w)' in node


def test_native_page_edges_keep_whole_glyphs_and_straddled_owed_identity():
    native = (ASCII / "features" / "true_ff_feature_native.glsl").read_text()
    prepare = native.split("bool prepare_spelling(", 1)[1].split("\nbool ", 1)[0]
    solve = native.split("bool prepare(uint site", 1)[1].split("\nbool ", 1)[0]
    grouped = native.split("bool grouped_site(", 1)[1].split("\nbool less_group", 1)[0]
    owed = native.split("bool same_owed_semantics(", 1)[1].split("\n}", 1)[0]
    assert 'first_page_pen-(TFF_FEATURE_ORIGINS-1)' in prepare
    assert 'last_page_pen=int(floor(pc.page.x-pc.page.z-float(shape.y)))' in prepare
    assert 'page_fit_representative(language,height' in solve
    assert 'top+float(ink.y+ink.w)>pc.page.y' in native
    assert 'owed_representative_site(site)' in grouped
    assert 'all(equal(a.position,b.position))' in owed
    assert 'all(equal(a.identity,b.identity))' in owed


def test_text_renderer_and_serializer_declare_explicit_variable_types():
    serializer = (ASCII / "features" / "TrueFFTextRows.gd").read_text()
    for source in (FEATURES_SRC, serializer):
        assert not re.search(r"\bvar\s+\w+\s*:=", source)


def test_unreproducible_text_reaches_recording_receipt():
    driver = (ASCII / "composition" / "TrueFFCompositorEffect.gd").read_text()
    writer = _function(driver, "_write_text_frame")
    assert 'if not bool(record.get("text_layout_valid", false)):' in writer
    assert "_text_layout_errors += 1" in writer
    assert '"layout_errors": _text_layout_errors' in _function(driver, "end_text_export")
    recorder = (ROOT / "godot_project" / "scripts" / "recording" /
                "ScreenRecordingController.gd").read_text()
    assert '"layout_errors": int(result.get("layout_errors", 0))' in recorder


def _production_export_drain():
    # Narrow syntax adaptation of the actual dictionary/ordering body. No
    # Godot or GPU: this checks the exported sequence, not callback scheduling.
    body = _function(FEATURES_SRC, "_drain_text_frames").split("\n", 1)[1]
    body = re.sub(r"\bvar (\w+): Dictionary =", r"\1 =", body)
    body = body.replace('state.has("record")', '("record" in state)')
    body = body.replace("_text_pending.erase(_text_next)", "_text_pending.pop(_text_next, None)")
    body = body.replace("_text_sink.call(record)", "_text_sink(record)")
    emitted = []
    namespace = {"_text_next": 0, "_text_frame": 3,
                 "_text_pending": {n: {} for n in range(3)}, "_text_sink": emitted.append}
    exec("def drain(abandon=False):\n\tglobal _text_next\n" + body, namespace)
    return namespace, emitted


def test_export_drains_every_callback_permutation_in_frame_order():
    for order in itertools.permutations(range(3)):
        state, emitted = _production_export_drain()
        for frame in order:
            state["_text_pending"][frame]["record"] = {"frame": frame}
            state["drain"]()
            assert [r["frame"] for r in emitted] == list(range(len(emitted)))
        assert emitted == [{"frame": n} for n in range(3)]
        assert not state["_text_pending"]


def test_export_stop_accounts_for_missing_tail_and_rejects_stale_generation():
    state, emitted = _production_export_drain()
    state["_text_pending"][1]["record"] = {"frame": 1}
    state["drain"](True)
    assert [r["frame"] for r in emitted] == [0, 1, 2]
    assert [r.get("error") for r in emitted] == [
        "true_ff_text_export_stopped_with_pending_readback", None,
        "true_ff_text_export_stopped_with_pending_readback"]
    assert not state["_text_pending"]
    callback = _function(FEATURES_SRC, "_on_text_part")
    assert callback.index("generation != _text_generation") < callback.index('_text_pending[frame]')
    setter = _function(FEATURES_SRC, "set_text_sink")
    assert setter.index("_drain_text_frames(true)") < setter.index("_text_pending.clear()")
    assert "_text_generation += 1" in setter


def test_export_normal_finish_waits_and_force_accounts_for_unfinished_frames():
    from types import SimpleNamespace

    state, emitted = _production_export_drain()
    state.update({"_text_accepting": True,
                  "_text_mutex": SimpleNamespace(lock=lambda: None, unlock=lambda: None)})
    body = _function(FEATURES_SRC, "finish_text_export").split("\n", 1)[1]
    body = re.sub(r"\bvar (\w+): Dictionary =", r"\1 =", body)
    body = body.replace("_text_accepting = false", "_text_accepting = False")
    body = body.replace("_text_sink.is_valid()", "True")  # valid sink fixture
    body = body.replace("_text_pending.size()", "len(_text_pending)")
    state["_drain_text_frames"] = state["drain"]
    exec("def finish(force=False):\n\tglobal _text_accepting\n" + body, state)
    assert state["finish"]() == {"pending": 3}
    assert not state["_text_accepting"] and not emitted
    state["_text_pending"][0]["record"] = {"frame": 0}
    state["_text_pending"][1]["record"] = {"frame": 1}
    assert state["finish"]() == {"pending": 1}
    assert emitted == [{"frame": 0}, {"frame": 1}]
    assert state["finish"](True) == {"pending": 0}
    assert emitted[2]["error"] == "true_ff_text_export_stopped_with_pending_readback"
    driver = (ASCII / "composition" / "TrueFFCompositorEffect.gd").read_text()
    end = _function(driver, "end_text_export")
    assert end.index('drain["pending"]') < end.index("set_text_sink(Callable())") < end.index("_text_file.close()")
    queue = _function(FEATURES_SRC, "_export_text_frame")
    assert "if not _text_accepting or not _text_sink.is_valid():" in queue


def test_prepass_keys_equal_what_both_callees_read():
    facts = _function(FRAME_SRC, "_prepass_facts")
    built = _dict_keys(facts, "prepass")
    built.update(re.findall(r'prepass\["(\w+)"\]\s*=', facts))
    assert built == {"depth", "ids", "primitive", "size", "water_surface", "light",
                     "live_source_bindings", "language", "static_hierarchy",
                     "vegetation_feature_uniforms"}
    assert _reads(PASSES_SRC, "prepass") <= built
    assert "var prepass: Dictionary = _prepass_facts(scene)" in SEAM
    # P0 remains an input to P1-P4 visibility acquisition only.  P5/P6 do not
    # read its ids/depth for language or paint ownership.
    assert not _reads(_function(FEATURES_SRC, "_layout"), "prepass")
    assert not _reads(_function(FEATURES_SRC, "_paint"), "prepass")


def test_page_facts_supply_every_key_the_seam_reads():
    page_facts = _dict_keys(_function(FRAME_SRC, "render_frame"), "page_facts")
    read = _reads(SEAM, "page")
    assert read <= page_facts, read - page_facts


def test_scene_keys_read_by_the_seam_are_published_by_the_compositor():
    # FL-4907: the compositor publishes world sources; the True FF driver adds
    # the packed atmosphere facts.
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    scene = _function(compositor, "true_ff_resident_sources")
    assert "true_ff_resident_sources()" in _function(compositor, "true_ff_scene_sources")
    published = set(re.findall(r'"(\w+)"\s*:', scene))
    published.add("atmosphere")
    read = _reads(SEAM, "scene")
    assert read and read <= published, read - published


def test_page_resources_are_prepared_outside_the_render_callback():
    frame = _function(FRAME_SRC, "render_frame")
    assert "_ensure(" not in frame
    assert "_prepare_page_resources(" not in frame
    assert '"%s_page_preparation_required"' in frame
    prepare = _function(FRAME_SRC, "prepare_page")
    assert "PagePhase.page_extent(viewport)" in prepare
    assert "_prepare_page_resources(device, extent)" in prepare
    driver = (ASCII / "composition" / "TrueFFCompositorEffect.gd").read_text(encoding="utf-8")
    request = _function(driver, "prepare_output")
    assert "call_on_render_thread" not in request
    assert "_page_preparation_pending = true" in request
    schedule = _function(driver, "_schedule_page_preparation")
    assert "call_on_render_thread(_prepare_output.bind(output_size, generation))" in schedule
    assert "_renderer.prepare_page(rd, output_size)" in _function(driver, "_prepare_output")
    callback = _function(driver, "_render_callback")
    assert "prepare_page(" not in callback and "FrameRenderer.new()" not in callback
    assert callback.index("_paint_startup(") < callback.index("_queue_page_preparation()")
    paper = (ASCII / "composition" / "TrueFFPageBackground.gd").read_text(encoding="utf-8")
    assert "storage_buffer_create(" in _function(paper, "setup")
    assert "storage_buffer_create(" not in _function(paper, "dispatch")
    assert "true_ff_page_paper_preparation_required" in _function(paper, "dispatch")


def test_residency_publication_is_not_a_begin_frame_sweep():
    """Source lifecycle gate, not an executed threading or render test."""
    world = COMPOSITOR.read_text(encoding="utf-8")
    assert "_apply_pending_scene_geometry_residency" not in _function(world, "begin_gpu_frame")
    queue = _function(world, "queue_scene_geometry_residency")
    assert "call_on_render_thread(_apply_pending_scene_geometry_residency)" in queue
    publication = _function(world, "_apply_pending_scene_geometry_residency")
    assert publication.index("_scene_geometry_receipt = residency_receipt") < publication.index(
        "scene_residency_published.emit()")
    resident = _function(world, "true_ff_resident_sources")
    for live_work in ("_update_vegetation_frame", "_dispatch_vegetation_clumps",
                      "water_flow.prepare", "wake_trail.sync_for_bind"):
        assert live_work not in resident
    assert "true_ff_resident_sources()" in _function(world, "true_ff_scene_sources")
    driver = (ASCII / "composition" / "TrueFFCompositorEffect.gd").read_text(encoding="utf-8")
    assert "world.scene_residency_published.connect(_prepare_scene_residency)" in driver
    assert "_renderer.prepare_residency" not in _function(driver, "_render_callback")


def test_residency_preparation_does_not_require_a_live_server_clock():
    """Preparation order/contract guard, not executed source correctness."""
    root = Path(__file__).resolve().parents[2]
    features = root / "godot_project/render/ascii/features"
    world = (features / "TrueFFWorldFeatureSources.gd").read_text()
    sky = (features / "TrueFFSkyFeatures.gd").read_text()
    frame = (root / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    for source in (world, sky):
        prepare = source.split("func prepare(", 1)[1].split("\nfunc ", 1)[0]
        assert "update_live(" not in prepare and 'get("server_time_seconds"' not in prepare
    prepare_frame = frame.split("func prepare_residency(", 1)[1].split("\nfunc ", 1)[0]
    assert prepare_frame.index("_bank.prepare_residency(") < prepare_frame.index("_world_sources.prepare(")
    # A contained bank failure may prepare a bankless P0 before world sources;
    # the normal publication path prepares world sources before the final P0.
    assert prepare_frame.index("_world_sources.prepare(") < prepare_frame.rindex("_gbuffer.prepare_scene(")
    assert 'scene["true_ff_terrain_topology"] = _bank.terrain_topology()' in prepare_frame
    assert '"sky": {"terrain_topology": _bank.terrain_topology()}' in prepare_frame


def test_ff_vegetation_prepares_before_the_single_world_tick():
    """Source schedule guard; no simulation/GPU execution claim."""
    root = Path(__file__).resolve().parents[2]
    world = (root / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    compositor = (root / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd").read_text()
    vegetation = (root / "godot_project/render/effects/vegetation/VegetationRuntime.gd").read_text()
    tick = world.split("func true_ff_scene_sources(", 1)[1].split("\nfunc ", 1)[0]
    assert "_sync_vegetation_clumps()" not in tick
    assert "dispatch_prepared_motion(rd)" in tick
    callback = compositor.split("func _render_callback(", 1)[1].split("\nfunc ", 1)[0]
    assert callback.index('world.call("true_ff_scene_sources"') < callback.index("for view in range(view_count):")
    strict = vegetation.split("func dispatch_prepared_motion(", 1)[1].split("\nfunc ", 1)[0]
    for forbidden in ("compile_compute", "get_cache", "buffer_create", "prepare_motion("):
        assert forbidden not in strict
    prepare = vegetation.split("func prepare_motion(", 1)[1].split("\nfunc ", 1)[0]
    assert "advance_field(" not in prepare and "compute_list_dispatch" not in prepare


def test_live_source_ingress_runs_once_before_views():
    """Source-order guard only; no Godot process or GPU execution."""
    compositor = (ASCII / "composition" / "TrueFFCompositorEffect.gd").read_text()
    callback = _function(compositor, "_render_callback")
    assert callback.count("_renderer.begin_source_frame(") == 1
    assert callback.index("_renderer.begin_source_frame(") < callback.index("for view in range(view_count):")
    ingress = _function(FRAME_SRC, "begin_source_frame")
    assert ingress.count("_world_sources.update_live(") == 1
    assert ingress.count("_world_sources.record_source_tick(") == 1
    assert ingress.index("_rd.compute_list_end()") < ingress.index("_borrow_source_scene(scene)")
    assert 'true_ff_feature_source_changes' in ingress
    assert '_actors.withdraw("actor_owner_residency_changed")' in ingress
    assert '_rigid_owner.call("source_changes")' in ingress
    assert '_rigid.withdraw("rigid_owner_residency_changed")' in ingress
    assert '_world_sources.apply_changes(' in ingress
    assert '{"effects": accepted_effect_event})' in ingress
    assert callback.index("world.accepted_effect_source_changes()") < callback.index("_renderer.begin_source_frame(")
    assert ingress.index("_residency_stamp != _source_residency_stamp(scene)") < ingress.index(
        '"residency_required": true')
    assert "prepare_residency(_rd, scene)" not in ingress
    for forbidden in ("true_ff_feature_source_facts", "_actors.prepare("):
        assert forbidden not in ingress
    assert "begin_source_frame(" not in _function(FRAME_SRC, "render_frame")


def test_live_binding_cardinality_and_journal_lifecycle_have_one_owner():
    ensure_sets = _function(PASSES_SRC, "_ensure_sets")
    assert '[["water", 1], ["effects", 2], ["sky", 4]]' in ensure_sets
    assert '(family_keys as Array).size() == (family_signature as Array).size()' in ensure_sets
    assert 'live.call("binding_keys_for_mask", mask)' in PASSES_SRC
    assert 'live_sources.call("binding_keys_for_mask", family_bit)' in ensure_sets
    assert "live_signature.size() != 9" not in PASSES_SRC
    world = COMPOSITOR.read_text()
    for name in ("set_scene_geometry_db", "reset_authority_session_epoch"):
        body = _function(world, name)
        assert "clear_accepted_effect_sources()" in body
        assert "rigid_meshes.clear_source_publication()" in body


def test_empty_source_journals_do_not_republish_or_lose_omissions():
    """Offline ingress guard; this does not establish rendered coverage."""
    bank = (ASCII / "features" / "TrueFFFeatureBank.gd").read_text()
    changes = _function(bank, "apply_changes")
    assert changes.index('"source_change_dictionary_required:"') < changes.index("var has_events")
    assert changes.index("if not has_events:") < changes.index("_commit_publication(scene_db)")
    assert "if not has_events:\n\t\treturn ready()" in changes
    ingress = _function(FRAME_SRC, "begin_source_frame")
    assert "if not effect_upserts.is_empty() or not effect_removals.is_empty():" in ingress
    publication = _function(FRAME_SRC, "_apply_world_publication")
    assert 'publication.get("omitted_families", {})' in publication
    assert "_preparation_omissions[key] = omitted[key]" in publication
    assert "_preparation_omissions.erase(key)" in publication


def test_source_history_completes_once_after_all_view_consumers():
    driver = (ASCII / "composition" / "TrueFFCompositorEffect.gd").read_text()
    callback = _function(driver, "_render_callback")
    assert callback.count("_renderer.begin_source_frame(") == 1
    assert "effect_events, view_count)" in callback
    assert callback.count("_renderer.complete_source_frame(") == 1
    assert "_renderer.complete_source_frame(views.size() == view_count)" in callback
    assert callback.index("_renderer.render_frame(") < callback.index("_renderer.complete_source_frame(")
    frame = _function(FRAME_SRC, "render_frame")
    assert frame.index("_gbuffer.publish(scene)") < frame.index("prepare_source_view(scene, view_index)")
    assert frame.index("prepare_source_view(scene, view_index)") < frame.index("_render_features(")
    prepare = _function(FRAME_SRC, "prepare_source_view")
    assert "_passes.reconcile_publication(" in prepare and "view_index, 0)" in prepare
    complete = _function(FRAME_SRC, "complete_source_frame")
    assert "_passes.complete_source_frame(_bank, views_complete)" in complete
    p0 = (ASCII / "structure" / "TrueFFSceneGBuffer.gd").read_text()
    stamp = _function(p0, "_preparation_stamp")
    assert 'live.call("signature_for_mask", 1 if water else 0, true)' in stamp
    assert "if water and live_signature.size() != 6" in stamp


def test_world_water_withdrawal_reaches_p0_and_recovery_invalidates_preparation():
    """Production-source connectivity only, not an executed Godot result."""
    publication = _function(FRAME_SRC, "_apply_world_publication")
    assert 'for key: String in [family, family + ".sources"]:' in publication
    assert 'publication.get("publication_ready", false)' in publication
    assert 'publication.get("ready", false)' not in publication
    borrow = _function(FRAME_SRC, "_borrow_source_scene")
    assert 'scene["true_ff_water_available"] = bool(water.get("ready", false))' in borrow
    for key in ("waterfall_site_field", "water_flow_runtime"):
        assert f'scene["{key}"] = _world_source_receipt.get("{key}")' in borrow
    p0 = (ASCII / "structure" / "TrueFFSceneGBuffer.gd").read_text()
    stamp = _function(p0, "_preparation_stamp")
    assert 'stamp.append(water)' in stamp
    water = _function(p0, "_publish_water")
    assert water.index('_withdraw_water(') < water.index('scene_db.call("water_revision")')
    retire = _function(p0, "_withdraw_water")
    for field in ("_water_spans", "_water_surfaces", "_water_authored_surfaces"):
        assert f"{field} = 0" in retire
    assert "_water_revision = -1" in retire
    assert "_water_backstop_key.clear()" in retire
    for field in ("_authored_water_slab", "_outside_water_slab", "_fall_receiver_slab"):
        assert f"{field} = Vector2(INF, -INF)" in retire
    assert 'omitted["p0.water"] = _water_omission' in _function(p0, "publish")
    assert 'live.call("signature_for_mask", 1, true)' in _function(p0, "_live_uniform_set")


def test_p0_borrows_the_feature_bank_scene_hierarchy():
    """One retained GPU TLAS owner; source guard, not GPU execution."""
    root = Path(__file__).resolve().parents[2]
    visibility = (root / "godot_project/render/ascii/structure/TrueFFStaticVisibility.gd").read_text()
    p0 = (root / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    assert '_nodes = hierarchy["static_nodes"]' in visibility
    assert '_ids = hierarchy["static_leaf_ids"]' in visibility
    assert '_storage(publication["tlas_node_bytes"])' not in visibility
    assert '_storage(publication["tlas_id_bytes"])' not in visibility
    assert 'hierarchy.get("scene_owner", -1)' in visibility
    # FL-4907 G6: one residency identity; the camera-roster revision is not compared.
    assert 'hierarchy.get("residency_generation", -1)' in visibility
    assert '"scene_revision"' not in visibility
    assert 'scene.get("true_ff_static_hierarchy", {})' in p0


def test_target_is_the_output_the_page_paper_painted():
    frame = _function(FRAME_SRC, "render_frame")
    assert "_background.dispatch(atmosphere_facts, _params, output, page_extent," in frame
    assert '"output": output,' in frame
    assert frame.index("_background.dispatch(") < frame.index("_render_features(camera_facts, page_facts)")
    glyphs = (ASCII / "features" / "true_ff_feature_glyphs.glsl").read_text(encoding="utf-8")
    paper = (ASCII / "composition" / "true_ff_page_background.glsl").read_text(encoding="utf-8")
    fmt = re.search(r"layout\(set=0,binding=0,(\w+)\) uniform restrict writeonly image2D output_image", paper)
    assert fmt and f"layout(set=0,binding=2,{fmt.group(1)}) uniform restrict writeonly image2D frame_target" in glyphs


def test_gpu_rejection_receipt_reads_complete_fixed_admission_headers():
    passes = PASSES.read_text(encoding="utf-8")
    renderer = FEATURES.read_text(encoding="utf-8")
    for field in ("native_control", "native_plan_control", "owed_control", "retry_control"):
        assert f'"{field}"' in passes
    # All fixed admission headers are packed into the args owner. One async
    # read keeps diagnostics always-on without four extra driver callbacks.
    report = _function(renderer, "_report_gpu_status")
    assert '"args": [passes.get("args", RID()), 60 * 4 * 4]' in report
    for label in ("meta_count=", "obligations=", "cert=[native=", "shadow=", "bindings="):
        assert label in renderer


def test_shadow_overlap_has_one_native_and_p5_whole_mask_occlusion_contract():
    native = (ASCII / "features" / "true_ff_feature_native.glsl").read_text(encoding="utf-8")
    admission = (ASCII / "features" / "true_ff_shadow_admission.glslinc").read_text(encoding="utf-8")
    layout = (ASCII / "features" / "true_ff_feature_layout.glsl").read_text(encoding="utf-8")
    row = (ASCII / "features" / "true_ff_native_row_admission.glslinc").read_text(encoding="utf-8")
    assert "bool whole_mask_site(NativeSite site)" in native
    assert "|| shadow_site(site);" in native
    assert "shadow_site(a)!=shadow_site(b)" in native
    assert "TFF_NATIVE_OCCLUSION_OWNER = 64u" in row
    for flag in ("TFF_NATIVE_OCCLUDED_MEMBER", "TFF_NATIVE_OCCLUSION_OWNER"):
        assert flag in admission
        assert flag in layout
    assert layout.index("if((s.key.z&((TFF_NATIVE_OCCLUDED_MEMBER") < layout.index("if(shadow_site(s)) return s.identity")


def test_owed_capture_errors_have_distinct_fixed_header_origins():
    common = (ASCII / "features" / "true_ff_feature_owed_common.glslinc").read_text(encoding="utf-8")
    completion = (ASCII / "features" / "true_ff_feature_owed_stage_08.glsl").read_text(encoding="utf-8")
    assert "TFF_OWED_CAPTURE_ERROR_INIT" in common
    assert "TFF_OWED_CAPTURE_ERROR_COMPLETION" in completion
    assert '"native_feasibility_uncertified"' in FEATURES_SRC


def test_owed_probe_is_deterministic_cell_owned_and_has_no_replay():
    common = (ASCII / "features" / "true_ff_feature_owed_common.glslinc").read_text(encoding="utf-8")
    probe = re.search(r"void capture_cell\(uint cell_index\).*?\n\}", common, re.S)
    assert probe
    body = probe.group(0)
    assert body.count("support_known(") == 1
    assert "for(int y=lo.y;y<hi.y;++y) for(int x=lo.x;x<hi.x;++x)" in body
    assert "if(!present || pixel<retained.state.z)" in body
    assert "candidate.state.z=pixel;owed.cells[slot]=candidate" in body
    assert "atomicMin" not in body and "publish" not in body
    passes = PASSES.read_text(encoding="utf-8")
    assert not any(re.search(rf"\b{stage}:\s*OWED_PROBE", passes) for stage in range(16, 20))
    for name in ("true_ff_feature_owed_probe_water.glsl",
                 "true_ff_feature_owed_probe_effect.glsl",
                 "true_ff_feature_owed_probe_sky.glsl"):
        wrapper = (ASCII / "features" / name).read_text(encoding="utf-8")
        assert "capture_cell(tff_dispatch_linear_invocation_id(64u));" in wrapper
    capture = (ASCII / "features" / "true_ff_feature_owed_stage_12.glsl").read_text(encoding="utf-8")
    assert "capture_cell(tff_dispatch_linear_invocation_id(64u));" in capture


def test_water_owed_probe_prunes_unrelated_surface_and_site_call_graphs():
    surface = (ASCII / "features" / "true_ff_surface_representatives.glslinc").read_text(encoding="utf-8")
    passes = (ASCII / "features" / "true_ff_feature_passes.glslinc").read_text(encoding="utf-8")
    sources = (ASCII / "features" / "true_ff_live_feature_source.glslinc").read_text(encoding="utf-8")
    assert surface.count("#ifdef TFF_OWED_WATER_ONLY") >= 4
    assert "r.source = tff_live_water_source_facts(inst, material_edge);" in surface
    witness = re.search(r"TffSite tff_witness_site\(.*?\n\}", passes, re.S)
    assert witness and "#ifndef TFF_OWED_WATER_ONLY" in witness.group(0)
    assert "uvec4 tff_live_water_source_facts" in sources


# FL-4907: the frame drivers must COMPILE the way World.gd loads them.
# Source-text and C++ harness tests above never load a GDScript, so on
# 2026-10-02 five separate edits (unregistered TrueFF types, a native-class
# const name, Variant-inferred locals in a preloaded web backend, a stale
# shader caller, the reserved GLSL word `sample`) each blanked the operator's
# launch while every test here stayed green. These two tests close that gap.
# Headless and windowless: no scene, no presentation mode, no capture.
FF_LOAD_CHAIN = (
    "res://render/ascii/features/TrueFFFeatureBank.gd",
    "res://render/ascii/features/TrueFFFeaturePasses.gd",
    "res://render/ascii/features/TrueFFFeatureRenderer.gd",
    "res://render/ascii/composition/TrueFFFrameRenderer.gd",
    "res://render/ascii/composition/TrueFFCompositorEffect.gd",
    "res://render/api/ASCIICompositorEffect.gd",
    "res://scripts/World.gd",
)


def _godot_binary():
    import os
    import shutil
    return os.environ.get("GODOT_BIN") or shutil.which("godot") \
        or "/Applications/Godot.app/Contents/MacOS/Godot"


def test_ff_frame_drivers_load_without_parse_or_compile_errors(tmp_path):
    import subprocess
    probe = tmp_path / "ff_load_probe.gd"
    chain = ", ".join(f'"{p}"' for p in FF_LOAD_CHAIN)
    probe.write_text(
        "extends SceneTree\n"
        f"const CHAIN := [{chain}]\n"
        "func _init() -> void:\n"
        "\tfor path in CHAIN:\n"
        "\t\tvar script: Script = load(path) as Script\n"
        "\t\tif script == null or not script.can_instantiate():\n"
        "\t\t\tprint(\"FF_LOAD_FAIL \", path)\n"
        "\tprint(\"FF_LOAD_DONE\")\n"
        "\tquit(0)\n", encoding="utf-8")
    result = subprocess.run([_godot_binary(), "--headless", "--path", str(ROOT / "godot_project"),
                             "--script", str(probe)], capture_output=True, text=True, timeout=240)
    out = result.stdout + result.stderr
    assert "FF_LOAD_DONE" in out, out[-2000:]
    # load() returns a script even after printing compile errors: the
    # error lines and their source location are the verdict.
    lines = out.splitlines()
    errors = [lines[n].strip() + " " + (lines[n + 1].strip() if n + 1 < len(lines) else "")
              for n, line in enumerate(lines) if re.search(r"(Parse|Compile) Error", line)]
    assert not errors, "\n".join(errors[:8])
    assert "FF_LOAD_FAIL" not in out, out[-2000:]


def test_all_shader_stages_compile_for_metal():
    import json
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "scripts/fl4895_glsl_check.py", "--metal", "--all"],
                            cwd=ROOT, capture_output=True, text=True, timeout=600)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    summaries = [line for line in lines if line.startswith("METAL_GATE_RESULT ")]
    bad = [line for line in lines
           if not line.startswith("OK") and not line.startswith("METAL_GATE_RESULT ")]
    summary = json.loads(summaries[0].split(" ", 1)[1]) if len(summaries) == 1 else {}
    assert result.returncode == 0 and not bad and summary.get("failed") == 0, \
        "\n".join(bad[:8] + summaries[:2]) + result.stderr[-1500:]
