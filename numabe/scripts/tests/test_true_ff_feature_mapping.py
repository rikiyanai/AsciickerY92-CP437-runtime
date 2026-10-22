"""FL-4917 offline requirement checks, not depiction or runtime acceptance."""
from pathlib import Path
import json
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import godot_feature_regression_gate as gate
import build_godot_feature_registry as builder
import compile_true_ff_outline_language as outline_compiler


def fixture_card():
    """Synthetic schema fixtures, not proposed player-facing artwork."""
    candidates = []
    for candidate_id, demo in (("A", "　≈≈"), ("B", "　⌒≈≈"), ("C", "　≈≈ヽ")):
        candidates.append({
            "id": candidate_id,
            "whole_candidate": "Synthetic whole candidate for schema testing only.",
            "directed_mirrors": "Synthetic directed mirror contract.",
            "negative_space_occlusion": "Synthetic negative-space contract.",
            "anchored_animation_part_timing": "Synthetic timing contract.",
            "near_far_behavior": "Synthetic nested-detail contract.",
            "facts": ["source crest identity"],
            "falsifiers": ["fixture source identity lost"],
            "source_archive_refs": ["archive.txt:1"],
            "original_text_demo": demo,
            "sequence_bindings": [{"sequence": "≈≈", "mode": "empty"},
                                  {"sequence": "⌒", "mode": "empty"},
                                  {"sequence": "ヽ", "mode": "empty"}],
            "directed_forms": {direction: demo for direction in gate.TRUE_FF_WATER_DIRECTIONS},
            "glyph_run_citations": {"≈≈": "ascii-art-authoring section 4.5; D1-D11",
                                    "⌒/ヽ": "Synthetic fixture decisions under D4/D5/D6, not player art."},
        })
    return {"selected": "B", "selection_rule_refs": ["D4/D5/D8 synthetic selection fixture"], "candidates": candidates,
            "active_candidates": ["B"],
            "rejected_candidates": [{"id": "A", "reason": "Synthetic rejection fixture, not art."},
                                    {"id": "C", "reason": "Synthetic rejection fixture, not art."}],
            "native_metrics": {"font": "Saitamaar", "font_px": 16, "row_pitch_px": 17,
                               "crest_glyph_advance_px": 7, "crest_pair_advance_px": 14,
                               "native_baseline_offset_px": 14,
                               "source_font_sha256": "592bf6be803ed76311841b95a10b20db3df0ec693a15f858757d47420b9b3a28",
                               "glyph_advances_px": {"≈": 7, "⌒": 16, "ヽ": 12, "　": 11}}}


def row(feature_id="GODOT-W06"):
    return {
        "id": feature_id,
        "name": "directional_waves",
        "true_ff": {
            "meaning": "A travelling crest shows wave direction.",
            "meaning_refs": ["water.glsl:1"],
            "mapping": "ADAPT",
            "depiction": "Required connected wave runs following a source crest.",
            "world_facts": ["crest identity, source curve and propagation phase"],
            "state": "REQUIRED",
            "fold_into": [],
            "rationale": "Carry wave movement, not the non-FF glyph selection.",
            "authoring_refs": ["ascii-art-authoring section 4.5"],
            "archive_refs": ["archive.txt:1"],
            "authoring_card": fixture_card(),
            "wave_evidence": {"operator_wave_form_decision": {
                "form": "≈≈", "codepoints": ["U+2248", "U+2248"],
                "date": "2026-09-30", "ref": "synthetic test fixture",
                "quote": "Synthetic schema fixture; not operator acceptance of art.",
            }},
            "open_work": ["Connect the required form to the source producer; runtime unverified."],
            "required": {
                "classes": ["authored_stroke"],
                "runs": [{"role": "crest", "language": "water", "tracks": ["crest.arm.level.pair"]}],
                "aa": "D6: preserve empty troughs around intact crest pairs.",
                "tempo": {"behavior": "Source world clock; no renderer holds.", "numeric_source": None},
                "paint": "Flat per-glyph FG/BG; no shading.",
                "cost": "Count-sized crest arcs in shared admission/layout; no screen pass.",
                "rule_refs": ["D4 combinations; D5 mirrors; D6 edge AA; D7 no scenery letters; D8 direction; D9 tempos"],
            },
        },
    }


@pytest.fixture
def source(tmp_path):
    (tmp_path / "water.glsl").write_text("// Meaning source, not implementation.\n")
    (tmp_path / "archive.txt").write_text("Synthetic archive fixture, not real authoring evidence.\n")
    return tmp_path


def test_required_is_valid_without_claiming_implementation(source):
    assert gate.validate_true_ff_mappings([row()], base_dir=source) == []


@pytest.mark.parametrize("role,track", [
    ("star", "authored.sky.star_classes"),
    ("cloud_lobe", "authored.sky.cloud_perimeter"),
    ("rain", "authored.sky.rain.snow"),
    ("smoke_puff", "authored.subject.smoke_puff"),
    ("projectile_burst", "authored.subject.projectile_burst"),
    ("explosion", "authored.subject.explosion"),
    ("vapor_trail", "authored.subject.vapor_trail"),
])
def test_existing_whole_authored_parts_are_not_fictitious_generic_runs(role, track, source):
    feature = row("GODOT-E01")
    feature["true_ff"]["required"]["runs"] = [
        {"role": role, "language": "soft", "tracks": [track]}
    ]
    if role == "star":
        for candidate, demo in zip(feature["true_ff"]["authoring_card"]["candidates"],
                                   ("　☆", "　☆☆", "　☆　☆")):
            candidate["original_text_demo"] = demo
            candidate["directed_forms"] = {direction: demo for direction in gate.TRUE_FF_WATER_DIRECTIONS}
            candidate["glyph_run_citations"] = {"☆": "D4 whole star mark; D6 empty surroundings; synthetic fixture only."}
            candidate["sequence_bindings"] = [{"sequence": "☆", "mode": "empty",
                                                "table": "sky_authoring.star_classes", "class": 1}]
        feature["true_ff"]["authoring_card"]["native_metrics"]["glyph_advances_px"]["☆"] = 16
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []


def test_authored_table_name_does_not_admit_a_missing_part(source):
    feature = row("GODOT-E01")
    feature["true_ff"]["required"]["runs"] = [
        {"role": "rain", "language": "soft", "tracks": ["authored.sky.rain.fragments"]}
    ]
    assert any("no compiled role/language/track" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("subject", ["projectile_burst", "explosion", "vapor_trail"])
def test_existing_subject_table_cannot_claim_a_generic_role(subject, source):
    # FL-4917: table existence does not establish the declared role binding.
    feature = row("GODOT-E01")
    feature["true_ff"]["required"]["runs"] = [
        {"role": "generic", "language": "soft",
         "tracks": [f"authored.subject.{subject}"]}
    ]
    assert any("no compiled role/language/track" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("feature_id,subject", [
    ("GODOT-A22", "projectile_burst"),
    ("GODOT-V05", "explosion"),
    ("GODOT-V11", "vapor_trail"),
])
def test_effect_recipes_keep_subject_and_generic_contour_owners_distinct(feature_id, subject):
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(row["true_ff"] for row in document["features"] if row["id"] == feature_id)
    assert mapping["state"] == "REQUIRED"
    runs = mapping["required"]["runs"]
    authored = [run for run in runs if f"authored.subject.{subject}" in run["tracks"]]
    assert authored == [{"role": subject, "language": "soft",
                         "tracks": [f"authored.subject.{subject}"]}]
    assert any(run["role"] == "generic" for run in runs)
    assert gate._true_ff_required_errors(mapping, feature_id) == []


def test_rain_fragment_comparison_retains_native_underscore_metric():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(row for row in document["features"] if row["id"] == "GODOT-E11")
    # This isolated card fixture omits the registry dependency graph; its audit
    # closure is checked by the full-registry and manual-audit tests.
    feature["true_ff"].pop("manual_audit", None)
    card = feature["true_ff"]["authoring_card"]
    rejected = next(candidate for candidate in card["candidates"] if candidate["id"] == "B")
    assert "_" in rejected["original_text_demo"]
    assert gate.validate_true_ff_mappings([feature]) == []
    del card["native_metrics"]["glyph_advances_px"]["_"]
    assert any("every used glyph/space advance" in error
               for error in gate.validate_true_ff_mappings([feature]))


def test_water_direction_cannot_use_letters_as_scenery(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0]["directed_forms"]["N"] = "　V≈≈"
    assert any("D7 forbids" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_required_rules_cannot_be_arbitrary_nonempty_text(source):
    feature = row()
    feature["true_ff"]["required"]["rule_refs"] = ["No real glyph rules cited"]
    assert any("cite D7 explicitly" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_every_adapt_needs_its_own_whole_candidate_card(source):
    feature = row("GODOT-A02")
    del feature["true_ff"]["authoring_card"]
    assert any("three-candidate authoring card" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("reference", ["../water.glsl:1", "/tmp/water.glsl:1"])
def test_meaning_citation_cannot_escape_the_owned_repository(reference, source):
    feature = row()
    feature["true_ff"]["meaning_refs"] = [reference]
    assert any("inside the repository" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_blank_archive_range_is_not_authoring_evidence(source):
    (source / "archive.txt").write_text("\n\n")
    feature = row()
    assert any("no art/source content" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("reference", [".../clouds.txt:1", "missing.txt:1", "archive.txt:2"])
def test_each_candidate_archive_reference_must_resolve(reference, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0]["source_archive_refs"] = [reference]
    assert any("candidates[0].source_archive_refs" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_every_character_in_directed_forms_needs_a_named_decision(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0]["directed_forms"]["NE"] = "≈≈○"
    assert any("uncited characters ['○']" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_sceneries_outside_water_also_reject_alphanumeric_shape_shortcuts(source):
    feature = row("GODOT-A02")
    feature["true_ff"]["authoring_card"]["candidates"][0]["original_text_demo"] = "V"
    assert any("D7 forbids scenery" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_semantic_ui_letters_require_the_actual_native_text_role(source):
    feature = row("GODOT-U05")
    feature["true_ff"]["required"]["runs"] = [
        {"role": "text", "language": "native_text", "tracks": ["native_character_sequence"]}]
    candidate = feature["true_ff"]["authoring_card"]["candidates"][0]
    candidate["original_text_demo"] = "MAP"
    candidate["glyph_run_citations"]["M/A/P"] = "D7 semantic UI exception; native text, not scenery."
    feature["true_ff"]["authoring_card"]["native_metrics"]["glyph_advances_px"].update({"M": 12, "A": 10, "P": 10})
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []


def test_adding_a_text_role_cannot_exempt_geometric_scenery_from_d7(source):
    feature = row("GODOT-E07")
    feature["true_ff"]["required"]["runs"].append(
        {"role": "text", "language": "native_text", "tracks": ["native_character_sequence"]})
    candidate = feature["true_ff"]["authoring_card"]["candidates"][0]
    candidate["original_text_demo"] = "MAP"
    candidate["glyph_run_citations"]["M/A/P"] = "D7 falsely claimed UI exception"
    assert any("D7 forbids scenery" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_glyph_decision_cannot_replace_rule_citation_with_generic_reassurance(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0]["glyph_run_citations"]["≈≈"] = "It looks appropriate"
    assert any("each decision must cite" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_all_families_need_explicit_native_text_metrics_not_global_receipt_metadata(source):
    feature = row("GODOT-A02")
    del feature["true_ff"]["authoring_card"]["native_metrics"]
    assert any("16px/17px text layout" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_unselected_loser_cannot_remain_in_active_art_pool(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["active_candidates"] = ["A", "B"]
    assert any("only the selected whole candidate" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_rejected_record_is_not_an_unexplained_unselected_option(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["rejected_candidates"][0]["reason"] = ""
    assert any("both inactive losers" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("location", ["required", "tempo", "run"])
def test_stale_nested_proposal_fields_are_not_silently_accepted(location, source):
    feature = row()
    required = feature["true_ff"]["required"]
    target = required if location == "required" else required["tempo"] if location == "tempo" else required["runs"][0]
    target["proposed"] = "unowned option"
    assert any("unknown" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("field", ["meaning", "depiction", "rationale", "world_facts", "open_work"])
def test_missing_required_mapping_fact_rejected(field, source):
    value = row()
    del value["true_ff"][field]
    assert gate.validate_true_ff_mappings([value], base_dir=source)


def test_deleting_mapping_and_unmapped_state_are_defects(source):
    assert gate.validate_true_ff_mappings([{"id": "GODOT-W06"}], base_dir=source)
    value = row()
    value["true_ff"]["state"] = "UNMAPPED"
    assert gate.validate_true_ff_mappings([value], base_dir=source)


@pytest.mark.parametrize("state", ["MAPPED_PROPOSED", "OPERATOR_CHOSEN", "WIRED"])
def test_obligation_state_is_not_approval_or_implementation(state, source):
    value = row()
    value["true_ff"]["state"] = state
    errors = gate.validate_true_ff_mappings([value], base_dir=source)
    assert any("REQUIRED is the obligation" in error for error in errors)


def test_no_depiction_needs_a_forcing_rule(source):
    value = row()
    value["true_ff"]["mapping"] = "NO_TRUE_FF_DEPICTION"
    assert any("forcing rule" in e for e in gate.validate_true_ff_mappings([value], base_dir=source))
    required = value["true_ff"]["required"]
    required.update(classes=[], runs=[], omission_rule="The goal limits depiction to visible world/UI meanings; this fixture is server bookkeeping.")
    assert not gate.validate_true_ff_mappings([value], base_dir=source)


@pytest.mark.parametrize("field", ["classes", "runs", "aa", "tempo", "paint", "cost", "rule_refs"])
def test_each_required_depiction_mechanism_is_mandatory(field, source):
    feature = row()
    del feature["true_ff"]["required"][field]
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("paint", ["A coloured edge", "Flat FG/BG with smooth per-pixel shading"])
def test_paint_cannot_hide_a_shading_owner(paint, source):
    feature = row()
    feature["true_ff"]["required"]["paint"] = paint
    assert any("flat per-glyph FG/BG" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("cost", ["A full screen effect pass", "Count-sized pass then a screen-sized refinement"])
def test_cost_cannot_hide_an_independent_screen_pass(cost, source):
    feature = row()
    feature["true_ff"]["required"]["cost"] = cost
    assert any("count-sized work" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_no_holds_is_a_tempo_constraint_not_a_status_label(source):
    feature = row()
    feature["true_ff"]["required"]["tempo"]["behavior"] = "Keep previous glyph until stable"
    assert any("no-holds" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_binding_must_exist_in_the_compiled_language(source):
    feature = row()
    feature["true_ff"]["required"]["runs"][0]["tracks"] = ["invented_water_wave"]
    assert any("no compiled role/language/track" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("language", [[], {}, None])
def test_malformed_required_language_returns_gate_errors_not_a_type_crash(language, source):
    feature = row()
    feature["true_ff"]["required"]["runs"][0]["language"] = language
    assert any("unknown outline language" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_native_text_is_not_a_geometric_role_alias(source):
    feature = row()
    feature["true_ff"]["required"]["runs"] = [{"role": "text", "language": "native_text", "tracks": ["native_character_sequence"]}]
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []
    feature["true_ff"]["required"]["runs"][0]["role"] = "crest"
    assert any("native text must bind" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_later_operator_spacing_law_allows_two_midrow_half_spaces():
    assert gate._true_ff_art_text_errors("≈≈  ≈≈", "fixture") == []
    assert gate._true_ff_art_text_errors("≈≈   ≈≈", "fixture")


@pytest.mark.parametrize("hidden", ["\x1b", "\u2060", "\u202e", "\u2028", "\x0b"])
def test_hidden_controls_cannot_override_ordinary_character_sequence_layout(hidden):
    assert gate._true_ff_art_text_errors("≈≈" + hidden + "≈≈", "fixture")


@pytest.mark.parametrize("targets", [[], ["GODOT-MISSING"], ["GODOT-W06"]])
def test_fold_requires_known_other_row(targets, source):
    value = row()
    value["true_ff"].update(mapping="FOLD", fold_into=targets)
    assert gate.validate_true_ff_mappings([value], base_dir=source)


def test_mutual_folds_cannot_hide_absent_depiction(source):
    first, second = row(), row("GODOT-W07")
    first["true_ff"].update(mapping="FOLD", fold_into=["GODOT-W07"])
    second["true_ff"].update(mapping="FOLD", fold_into=["GODOT-W06"])
    assert any("cyclic" in e for e in gate.validate_true_ff_mappings([first, second], base_dir=source))


@pytest.mark.parametrize("reference", ["missing.glsl:1", "water.glsl:function", "water.glsl:2"])
def test_meaning_citation_must_resolve(reference, source):
    value = row()
    value["true_ff"]["meaning_refs"] = [reference]
    assert gate.validate_true_ff_mappings([value], base_dir=source)


def test_historical_meaning_pins_blob_not_mutable_branch(source, monkeypatch):
    feature = row()
    revision = "a" * 40
    feature["true_ff"]["meaning_refs"] = [f"old.glsl:1 @ commit {revision}"]
    calls = []

    def read_blob(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="// Historical source fixture.\n", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", read_blob)
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []
    assert calls[0][0] == ["git", "show", "--no-ext-diff", f"{revision}:old.glsl"]
    assert calls[0][1]["cwd"] == source
    feature["true_ff"]["meaning_refs"] = ["old.glsl:1 @ commit main"]
    assert gate.validate_true_ff_mappings([feature], base_dir=source)
    assert len(calls) == 1


def test_absent_historical_blob_is_not_evidence(source, monkeypatch):
    feature = row()
    feature["true_ff"]["meaning_refs"] = [f"old.glsl:1 @ commit {'a' * 40}"]

    def missing_blob(argv, **kwargs):
        raise subprocess.CalledProcessError(128, argv)

    monkeypatch.setattr(gate.subprocess, "run", missing_blob)
    assert any("unreadable source" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_source_gap_cannot_be_hidden_by_required_status(source):
    feature = row()
    feature["true_ff"]["source_gap"] = "Citation does not establish the intended meaning."
    assert any("still an open defect" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def manual_audit_fixture():
    return {
        "status": "SOURCE_REVIEWED_VISUAL_UNVERIFIED",
        "priority": "INDISPENSABLE_READABILITY", "disposition": "ADAPT",
        "reason": "This fixture tests a decision schema, not a finished artwork.",
        "preserve": "Source crest direction and contact.",
        "structure": "Whole crest pairs with open troughs.",
        "dependencies": [], "inspected": ["water.glsl:1"],
        "falsifier": "Crest direction disagrees with the source.",
        "unverified": "No rendered or operator acceptance evidence.",
    }


def test_unresolved_manual_decision_cannot_pass_without_legacy_source_gap(source):
    feature = row()
    feature["true_ff"].pop("source_gap", None)
    feature["true_ff"]["manual_audit"] = {
        **manual_audit_fixture(), "status": "SOURCE_UNRESOLVED",
    }
    assert any("manual_audit.status: unresolved source decision" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_manual_audit_does_not_replace_source_gap(source):
    feature = row()
    feature["true_ff"]["manual_audit"] = manual_audit_fixture()
    feature["true_ff"]["source_gap"] = "Source ownership remains unresolved."
    errors = gate.validate_true_ff_mappings([feature], base_dir=source)
    assert any("still an open defect" in e for e in errors)
    assert not any("manual_audit" in e for e in errors)


@pytest.mark.parametrize("field", ["reason", "preserve", "structure", "falsifier", "unverified", "inspected"])
def test_manual_audit_requires_individual_decision_fields(source, field):
    feature = row()
    feature["true_ff"]["manual_audit"] = manual_audit_fixture()
    del feature["true_ff"]["manual_audit"][field]
    assert any(f"manual_audit.{field}" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("changes", [
    {"status": "ACCEPTED"}, {"priority": "ALL_EQUAL"},
    {"disposition": "DROP_GAMEPLAY"}, {"dependencies": ["GODOT-MISSING"]},
    {"status": "SOURCE_REVIEWED_NONVISUAL"},
])
def test_manual_audit_cannot_promote_acceptance_or_drop_gameplay(source, changes):
    feature = row()
    feature["true_ff"]["manual_audit"] = {**manual_audit_fixture(), **changes}
    assert any("manual_audit" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_compiler_preserves_audit_and_unresolved_obligations_without_inventing_reviews():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    language = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json")
    catalog = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json")
    # Explicit controls distinguish reviewed and unreviewed inputs even after
    # the live registry's audit is eventually complete.
    document["features"][0]["true_ff"]["manual_audit"] = manual_audit_fixture()
    document["features"][1]["true_ff"].pop("manual_audit", None)
    document["features"][0]["true_ff"]["source_gap"] = "Synthetic unresolved source control"
    compiled = outline_compiler.compile_feature_depictions(json.dumps(document).encode(), language, catalog)
    rows = {**compiled["direct"], **compiled["folds"], **compiled["no_depiction"]}
    assert len(rows) == 237
    first, second = (row["id"] for row in document["features"][:2])
    assert rows[first]["manual_audit"] == manual_audit_fixture()
    assert rows[first]["retained_obligations"]["source_gap"] == "Synthetic unresolved source control"
    assert rows[second]["manual_audit"] is None
    assert "not runtime or visual acceptance" in compiled["contract"]


def test_compiler_rejects_duplicate_feature_ids_even_at_237_rows():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    document["features"][1]["id"] = document["features"][0]["id"]
    with pytest.raises(ValueError, match="237 distinct"):
        outline_compiler.compile_feature_depictions(json.dumps(document).encode(), {}, {})


@pytest.mark.parametrize("changes, message", [
    ({"role": "tuft", "language": "plant", "track": "tuft_arch"}, "Named run"),
    ({"role": "generic", "language": "hard", "track": "missing"}, "Named run"),
    ({"active": False}, "inactive/former"),
    ({"former_table": "sky_authoring.star_classes"}, "inactive/former"),
    ({"table": "invented.owner"}, "Unknown authored"),
])
def test_standalone_compiler_rejects_false_selected_sequence_ownership(changes, message):
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    language, catalog = gate._true_ff_card_sources()
    card = next(row["true_ff"]["authoring_card"] for row in document["features"]
                if row["id"] == "GODOT-V08")
    selected = next(candidate for candidate in card["candidates"] if candidate["id"] == card["selected"])
    cross = next(binding for binding in selected["sequence_bindings"] if binding["sequence"] == "┼")
    # The globally admitted cross is valid in its real owner before mutation.
    outline_compiler.compile_feature_depictions(json.dumps(document).encode(), language, catalog)
    cross.update(changes)
    with pytest.raises(ValueError, match=message):
        outline_compiler.compile_feature_depictions(json.dumps(document).encode(), language, catalog)


@pytest.mark.parametrize("changes, message", [
    ({"table": "invented.owner"}, "required authored owner"),
    ({"class": -1}, "named class/mode"),
    ({"class": True}, "named class/mode"),
])
def test_standalone_compiler_retains_exact_star_class_owner(changes, message):
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    language, catalog = gate._true_ff_card_sources()
    card = next(row["true_ff"]["authoring_card"] for row in document["features"]
                if row["id"] == "GODOT-E01")
    selected = next(candidate for candidate in card["candidates"] if candidate["id"] == card["selected"])
    selected["sequence_bindings"][0].update(changes)
    with pytest.raises(ValueError, match=message):
        outline_compiler.compile_feature_depictions(json.dumps(document).encode(), language, catalog)


def test_compiler_keeps_dropped_visual_census_but_not_production_eligibility():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    language, catalog = gate._true_ff_card_sources()
    compiled = outline_compiler.compile_feature_depictions(json.dumps(document).encode(), language, catalog)
    row = compiled["direct"]["GODOT-E11"]
    assert row["manual_audit"]["status"] == "SOURCE_UNRESOLVED"
    assert row["recipe"]  # preserve prior authorship and accountable open work
    assert row["visual_eligible"] is False
    assert "GODOT-E11" not in compiled["runtime"]["visual_feature_ids"]
    for group in compiled["runtime"]["role_groups"]:
        assert "GODOT-E11" not in group["feature_ids"]
    for table in compiled["runtime"]["authored_tables"].values():
        assert "GODOT-E11" not in table["feature_ids"]
    assert compiled["runtime"]["required_feature_count"] == 237


def test_reticle_drafts_use_admitted_cross_and_native_advance_alignment():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    catalog = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json")
    advances = {glyph["char"]: glyph["advance_px"] for glyph in catalog["glyphs"]}
    card = next(row for row in document["features"] if row["id"] == "GODOT-V08")["true_ff"]["authoring_card"]
    for candidate in card["candidates"][:2]:
        lines = candidate["original_text_demo"].splitlines()
        centres = []
        for line in lines:
            mark = "┼" if "┼" in line else "|"
            pen = sum(advances[char] for char in line[:line.index(mark)])
            centres.append(pen + advances[mark] / 2)
        assert centres == [centres[0]] * 3
        assert "+" not in candidate["original_text_demo"]
        assert any(binding.get("sequence") == "┼" and binding.get("track") == "cross"
                   for binding in candidate["sequence_bindings"])


@pytest.mark.parametrize("content", ["\n", "   \n\t\n", "}\n{\n", "// ---\n"])
def test_meaning_citation_cannot_point_at_empty_source_content(source, content):
    (source / "water.glsl").write_text(content)
    feature = row()
    feature["true_ff"]["meaning_refs"] = ["water.glsl:1"]
    assert any("no substantive source content" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_historical_citation_checks_content_not_only_range(source, monkeypatch):
    feature = row()
    feature["true_ff"]["meaning_refs"] = [f"old.glsl:1 @ commit {'a' * 40}"]
    monkeypatch.setattr(gate.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, stdout="\n", stderr=""))
    assert any("no substantive source content" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_builder_preserves_individually_authored_data():
    old = {"features": [row()]}
    new = {"features": [{"id": "GODOT-W06", "name": "directional_waves"}]}
    builder.preserve_true_ff_mappings(new, old)
    assert new["features"][0]["true_ff"] == old["features"][0]["true_ff"]
    new["features"][0]["true_ff"]["meaning"] = "new"
    assert old["features"][0]["true_ff"]["meaning"] != "new"


def test_missing_owner_is_explicit_not_silent(source):
    value = row()
    value["true_ff"]["meaning_refs"] = []
    assert any("source_gap" in e for e in gate.validate_true_ff_mappings([value], base_dir=source))
    value["true_ff"]["source_gap"] = "Declared owner is missing; intended meaning only."
    assert any("source_gap" in e for e in gate.validate_true_ff_mappings([value], base_dir=source))


def test_duplicate_mapping_json_keys_are_rejected(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"true_ff": {}, "true_ff": {}}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        gate.load_registry(path)


def test_shore_break_art_does_not_redefine_directional_behavior():
    # FL-4512: offline proposal-contract check, not renderer parity evidence.
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    water = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-W07")
    behavior = water["behavior_contract"]
    assert behavior["period_seconds"] == 3.2
    assert behavior["along_shore_cycles_per_world_unit"] == 0.045
    assert behavior["forward_cycle_fraction"] == 0.72
    assert behavior["rebound_fraction_of_start_distance"] == 0.3
    assert behavior["maximum_shore_distance"] == 6.0
    assert behavior["distance_stage_thresholds"] == [0.75, 2.26, 4.14]
    assert "toward the solid" in behavior["direction"]
    assert "N, NE, E, SE, S, SW, W, NW" in behavior["direction"]
    assert "SHORE_BREAK" in behavior["current_implementation_gap"]
    mismatch = behavior["implementation_mismatch"]
    assert mismatch["status"].startswith("OPEN:")
    assert "5354a757f" in mismatch["historical_mismatch"]
    assert any("features/true_ff_feature_layout.glslinc:" in ref for ref in mismatch["refs"])
    assert water["authoring_card"]["selected"] == "B_turning_paired_front"
    for candidate in water["authoring_card"]["candidates"]:
        assert "behavior_contract" in candidate["anchored_animation_part_timing"]


def test_flag_completion_folds_visible_meaning_not_the_forbidden_alpha_mask():
    # Offline source/spec regression, not lifecycle execution or art acceptance.
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    rows = {feature["id"]: feature["true_ff"] for feature in document["features"]}
    completion = rows["GODOT-U51"]
    assert completion["mapping"] == "FOLD"
    assert completion["fold_into"] == ["GODOT-U50"]
    assert rows["GODOT-U50"]["mapping"] == "ADAPT"
    assert "omission_rule" not in completion["required"]
    assert "2.0-second wait" in completion["required"]["tempo"]["behavior"]
    assert "2.0-second completion interval" in completion["required"]["tempo"]["behavior"]
    assert "quit" in completion["required"]["tempo"]["behavior"]
    assert "no renderer holds" in completion["required"]["tempo"]["behavior"]
    assert "invented caption" in completion["required"]["aa"]
    assert "victory copy" not in str(completion["open_work"])
    source = (gate.REPO_ROOT / "godot_project/level/interactable/flag/flag_reached_screen.gd").read_text()
    scene = (gate.REPO_ROOT / "godot_project/level/interactable/flag/flag_reached_screen.tscn").read_text()
    assert "create_timer(2.0)" in source and "animation_finished" in source
    assert "get_tree().quit()" in source and "length = 2.0" in scene
    assert 'type="Label"' not in scene


def test_live_mapping_population_and_water_cards_are_required():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    features = document["features"]
    assert len(features) == len({f["id"] for f in features}) == 237
    assert gate.validate_true_ff_mappings(features) == []
    assert {f["true_ff"]["state"] for f in features} == {"REQUIRED"}
    water = {f["id"]: f["true_ff"] for f in features if f["id"] in {"GODOT-W06", "GODOT-W07"}}
    for mapping in water.values():
        card = mapping["authoring_card"]
        assert card["selected"] in {c["id"] for c in card["candidates"]}
        assert card["selection_rule_refs"]
        assert len(card["candidates"]) == 3
        assert len({c["original_text_demo"] for c in card["candidates"]}) == 3
        for candidate in card["candidates"]:
            assert candidate["glyph_run_citations"]
            assert candidate["facts"] and candidate["falsifiers"]
            for line in candidate["original_text_demo"].splitlines():
                assert not line.startswith(" ") and not line.endswith((" ", "\u3000"))
                assert "  " not in line and "[empty" not in line
            assert "D15" not in json.dumps(candidate)
        assert all("true_ff" not in ref.lower() for ref in mapping["meaning_refs"])


def test_builder_refuses_silent_orphan_or_name_change():
    old = {"features": [row()]}
    for features in ([], [{"id": "GODOT-W06", "name": "another_meaning"}]):
        with pytest.raises(builder.RegistryBuildError):
            builder.preserve_true_ff_mappings({"features": features}, old)


def test_new_census_row_cannot_erase_mapping_coverage():
    old = {"features": [row()]}
    new = {"features": [
        {"id": "GODOT-W06", "name": "directional_waves"},
        {"id": "GODOT-W07", "name": "shoreline_breaks"},
    ]}
    with pytest.raises(builder.RegistryBuildError, match="explicit True FF meanings"):
        builder.preserve_true_ff_mappings(new, old)


def test_required_adaptation_needs_rule_selected_candidate(source):
    value = row()
    value["true_ff"]["authoring_card"]["selected"] = None
    assert any("rule-selected authored candidate" in e for e in gate.validate_true_ff_mappings([value], base_dir=source))


@pytest.mark.parametrize("field,value", [("state", []), ("mapping", {}), ("meaning_refs", [12])])
def test_malformed_data_returns_errors_not_exceptions(field, value, source):
    feature = row()
    feature["true_ff"][field] = value
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("reference", ["missing.txt:1", "archive.txt:2", "archive.txt:0", "archive.txt:2-1"])
def test_archive_citation_must_resolve(reference, source):
    feature = row()
    feature["true_ff"]["archive_refs"] = [reference]
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


def test_adaptation_requires_archive_evidence(source):
    feature = row()
    feature["true_ff"]["archive_refs"] = []
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


def test_true_ff_implementation_is_not_non_ff_meaning_evidence(source):
    (source / "TrueFFWater.gd").write_text("# FF implementation fixture\n")
    feature = row()
    feature["true_ff"]["meaning_refs"] = ["TrueFFWater.gd:1"]
    assert any("not the non-FF meaning owner" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("method", ["_ff_page_lines", "_publish_ff_menu", "build_true_ff_rows"])
@pytest.mark.parametrize("suffix", [".gd", ".GD", ".gD"])
def test_ff_method_in_mixed_owner_file_is_not_non_ff_meaning_evidence(method, suffix, source):
    filename = "InventoryPanel" + suffix
    (source / filename).write_text(
        "func apply_server_inventory():\n\tpass\n\nfunc " + method + "():\n\treturn []\n")
    feature = row()
    feature["true_ff"]["meaning_refs"] = [filename + ":4-5"]
    assert any("explicitly FF-owned method" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_shared_publication_method_in_mixed_file_still_requires_semantic_review(source):
    (source / "InventoryPanel.gd").write_text(
        "func apply_server_inventory():\n\tpublished_rows = rows\n\nfunc _ff_page_lines():\n\treturn []\n")
    feature = row()
    feature["true_ff"]["meaning_refs"] = ["InventoryPanel.gd:1-2"]
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []


@pytest.mark.parametrize("suffix", [".gd", ".GD", ".gD"])
@pytest.mark.parametrize("reference", ["3-4", "6", "8"])
def test_generic_method_cannot_supply_ff_branch_or_sink_as_meaning(suffix, reference, source):
    filename = "ChatLog" + suffix
    (source / filename).write_text(
        "func _refresh():\n"
        "\tvar received_count = messages.size()\n"
        "\tif _ff_text:\n"
        "\t\tvar rows = messages.duplicate()\n"
        "\t\tFFHudText.publish(FFHudText.SOURCE_CHAT, rows)\n"
        "\telif _ff_published:\n"
        "\t\tFFHudText.publish(FFHudText.SOURCE_CHAT, [])\n"
        "\tvar selected = FFHudText.presentation_is_ff(self)\n"
        "\tordinary_count = received_count\n")
    feature = row()
    feature["true_ff"]["meaning_refs"] = [filename + ":" + reference]
    assert any("presentation branch or call" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("statement", [
    "_ff_buttons.append(button)",
    "_ff_selection = resume_index",
    "self._ff_menu_dirty = true",
    "_ff_selection += 1",
    "_ff_pages[section] = rows",
    "_sync_ff_menu()",
])
def test_generic_method_ff_field_mutations_and_calls_are_not_meaning(statement, source):
    # FL-4917: these are FF presentation writes even outside an FF branch.
    (source / "PauseMenu.gd").write_text(
        "func build_actions():\n\t" + statement + "\n\tshared_actions = actions\n")
    feature = row()
    feature["true_ff"]["meaning_refs"] = ["PauseMenu.gd:2"]
    assert any("presentation branch or call" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))
    feature["true_ff"]["meaning_refs"] = ["PauseMenu.gd:3"]
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []


def test_generic_fact_lines_and_literal_ff_names_are_not_rejected_as_branches(source):
    (source / "ChatLog.gd").write_text(
        "func _refresh():\n"
        "\tvar notice = \"FFHudText.publish and _ff_text are literal text\"\n"
        "\t# FFHudText.publish is not called in this comment\n"
        "\tif connected:\n"
        "\t\treceived_count = messages.size()\n"
        "\tif _ff_text:\n"
        "\t\tFFHudText.publish(FFHudText.SOURCE_CHAT, [])\n"
        "\tordinary_count = received_count\n")
    feature = row()
    feature["true_ff"]["meaning_refs"] = ["ChatLog.gd:1-5", "ChatLog.gd:8"]
    assert gate.validate_true_ff_mappings([feature], base_dir=source) == []


def test_chat_and_pause_meaning_ranges_exclude_existing_ff_presentation():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    rows = {feature["id"]: feature["true_ff"] for feature in document["features"]}
    chat, pause = rows["GODOT-U65"], rows["GODOT-U08"]
    assert "godot_project/scripts/ui/ChatLog.gd:199-218" not in chat["meaning_refs"]
    assert "godot_project/scripts/ui/ChatLog.gd:178-183" in chat["meaning_refs"]
    assert "godot_project/scripts/ui/ChatLog.gd:192-198" in chat["meaning_refs"]
    assert "literal bracket" in chat["world_facts"][2]
    assert "ignores sequence/tick" in chat["world_facts"][1]
    assert "godot_project/scripts/PauseMenu.gd:204-231" not in pause["meaning_refs"]
    assert "godot_project/scripts/PauseMenu.gd:422-450" in pause["meaning_refs"]
    assert "2026-10-18 explicit operator reply" in pause["rationale"]
    assert "does not stop authoritative" in pause["world_facts"][1]


def test_fold_cannot_end_in_proposed_omission(source):
    first, second, third = row("GODOT-F01"), row("GODOT-F02"), row("GODOT-F03")
    first["true_ff"].update(mapping="FOLD", fold_into=[second["id"]])
    second["true_ff"].update(mapping="FOLD", fold_into=[third["id"]])
    third["true_ff"]["mapping"] = "NO_TRUE_FF_DEPICTION"
    assert any("no direct ADAPT depiction" in e for e in gate.validate_true_ff_mappings([first, second, third], base_dir=source))


def test_fold_must_name_the_depiction_not_an_intermediate_fold(source):
    first, second, third = row("GODOT-F01"), row("GODOT-F02"), row("GODOT-F03")
    first["true_ff"].update(mapping="FOLD", fold_into=[second["id"]])
    second["true_ff"].update(mapping="FOLD", fold_into=[third["id"]])
    assert any("no direct ADAPT depiction" in e for e in gate.validate_true_ff_mappings([first, second, third], base_dir=source))


def test_carriage_return_is_not_hidden_by_splitlines():
    assert gate._true_ff_art_text_errors("≈≈\r\n≈≈", "fixture")


@pytest.mark.parametrize("gap", ["\u00a0", "\u2009", "\u200b", "\u202f"])
def test_invisible_unicode_spacing_cannot_fake_native_alignment(gap):
    assert gate._true_ff_art_text_errors("≈≈" + gap + "≈≈", "fixture")


def test_water_mirror_prose_does_not_replace_eight_whole_forms(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0].pop("directed_forms")
    assert any("enumerate complete" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_water_requires_explicit_native_metrics_not_a_monospace_assertion(source):
    feature = row()
    feature["true_ff"]["authoring_card"]["native_metrics"]["crest_pair_advance_px"] = 32
    assert any("native_metrics" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("field", ["original_text_demo", "directed_forms"])
def test_water_rejects_odd_crest_runs_even_if_a_pair_is_present(field, source):
    feature = row()
    candidate = feature["true_ff"]["authoring_card"]["candidates"][0]
    if field == "directed_forms":
        candidate[field]["N"] = "≈≈≈"
    else:
        candidate[field] = "≈≈　≈≈≈"
    assert any("complete ≈≈ pair" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("bad_id", [None, [], ""])
def test_bad_feature_id_does_not_crash(bad_id, source):
    feature = row()
    feature["id"] = bad_id
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


def test_duplicate_feature_ids_are_not_coverage(source):
    assert any("distinct" in e for e in gate.validate_true_ff_mappings([row(), row()], base_dir=source))


@pytest.mark.parametrize("demo", [" ～～", "～～ ", "～～　", "～～  ～～", "～～\t～～", "[empty pocket]", "TODO"])
def test_water_candidate_rejects_placeholder_or_invalid_spacing(demo, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0]["original_text_demo"] = demo
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("mutation", ["two_candidates", "duplicate_demo", "duplicate_id", "missing_timing", "invalid_d_rule", "no_citation"])
def test_incomplete_water_card_is_not_complete(mutation, source):
    feature = row()
    candidates = feature["true_ff"]["authoring_card"]["candidates"]
    if mutation == "two_candidates":
        candidates.pop()
    elif mutation == "duplicate_demo":
        candidates[1]["original_text_demo"] = candidates[0]["original_text_demo"]
    elif mutation == "duplicate_id":
        candidates[1]["id"] = candidates[0]["id"]
    elif mutation == "missing_timing":
        candidates[0].pop("anchored_animation_part_timing")
    elif mutation == "invalid_d_rule":
        candidates[0]["glyph_run_citations"] = {"～～": "D15"}
    else:
        candidates[0]["glyph_run_citations"] = {}
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("selected", [None, [], {}, "MISSING"])
def test_required_card_cannot_leave_selection_open(selected, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["selected"] = selected
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("selected", [[], {}, "MISSING"])
def test_bad_operator_candidate_pointer_returns_error(selected, source):
    feature = row()
    feature["true_ff"].update(state="OPERATOR_CHOSEN", operator_decision={"ref": "fixture", "quote": "fixture"})
    feature["true_ff"]["authoring_card"]["selected"] = selected
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("demo", ["～～", "≈", "≈≈　≈", "≈≈～～"])
def test_superseded_or_partial_crest_form_is_rejected(demo, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["candidates"][0]["original_text_demo"] = demo
    assert any("complete ≈≈ pairs" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_current_operator_wave_decision_not_archive_frequency():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    water = {f["id"]: f["true_ff"] for f in document["features"] if f["id"] in {"GODOT-W06", "GODOT-W07"}}
    evidence = water["GODOT-W06"]["wave_evidence"]
    assert evidence["operator_wave_form_decision"]["form"] == "≈≈"
    assert evidence["operator_wave_form_decision"]["quote"] == "`≈≈` is RIGHT i meant the otehr way around"
    assert evidence["candidate_counts"]["U+2248_≈"]["occurrences"] == 0
    for mapping in water.values():
        assert mapping["state"] == "REQUIRED"
        assert mapping["authoring_card"]["selected"] in {c["id"] for c in mapping["authoring_card"]["candidates"]}
        for candidate in mapping["authoring_card"]["candidates"]:
            assert "≈≈" in candidate["original_text_demo"]
            assert "～" not in candidate["original_text_demo"]


def test_mapping_cli_checks_census_without_running_feature_commands(source, monkeypatch, capsys):
    feature = row()
    feature["check"] = {"kind": "command", "argv": ["must-never-run"]}
    registry = {"features": [feature], "census": {"expected_census_path": "census.json"}}
    (source / "registry.json").write_text(json.dumps(registry))
    (source / "census.json").write_text(json.dumps({"feature_ids": [feature["id"]]}))
    monkeypatch.chdir(source)
    monkeypatch.setattr(gate, "run_registry", lambda *a, **kw: pytest.fail("runtime check invoked"))
    assert gate.main(["--registry", "registry.json", "--validate-true-ff"]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "offline mapping data only"
    (source / "census.json").write_text(json.dumps({"feature_ids": [feature["id"], "GODOT-W07"]}))
    assert gate.main(["--registry", "registry.json", "--validate-true-ff"]) == 1


@pytest.mark.parametrize("different_clock", [True, False])
def test_three_single_time_candidates_are_not_three_event_ages(different_clock, source):
    feature = row("GODOT-E07")
    for index, candidate in enumerate(feature["true_ff"]["authoring_card"]["candidates"]):
        candidate["single_time_world_clock_stage"] = {
            "event_identity": "synthetic schema event, not player art",
            "stage": "splash_ring",
            "age_seconds": 0.603 + (index * 0.06 if different_clock else 0.0),
            "ripple_progress": 0.5,
            "radius_world_units": 0.41,
        }
    errors = gate.validate_true_ff_mappings([feature], base_dir=source)
    assert any("not different animation keyframes" in e for e in errors) == different_clock


@pytest.mark.parametrize("field", ["projection", "topology", "global_time_seconds"])
def test_single_time_comparison_cannot_change_other_declared_world_facts(field, source):
    feature = row("GODOT-E07")
    for candidate in feature["true_ff"]["authoring_card"]["candidates"]:
        candidate["single_time_world_clock_stage"] = {
            "event_identity": "synthetic schema event, not player art",
            "stage": "splash_ring", "age_seconds": 0.603,
            "ripple_progress": 0.5, "radius_world_units": 0.41,
            "projection": "one fixture camera", "topology": "one source ring",
            "global_time_seconds": 2.0,
        }
    feature["true_ff"]["authoring_card"]["candidates"][2]["single_time_world_clock_stage"][field] = "different"
    assert any("not different animation keyframes" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("mutation", [None, "sequence", "role", "language", "track", "incomplete"])
def test_named_run_binding_checks_exact_ink_not_only_train_admission(mutation, source):
    feature = row()
    binding = feature["true_ff"]["authoring_card"]["candidates"][0]["sequence_bindings"][0]
    binding.update({"role": "crest", "language": "water", "track": "crest.arm.level.pair"})
    if mutation == "sequence":
        binding["sequence"] = "⌒"  # Admitted ink, but not this crest run.
    elif mutation == "incomplete":
        del binding["track"]
    elif mutation is not None:
        binding[mutation] = "wrong_owner"
    errors = gate.validate_true_ff_mappings([feature], base_dir=source)
    assert any("does not bind this exact sequence/mode" in error for error in errors) == (mutation is not None)


@pytest.mark.parametrize("mark", ["Ａ", "ｚ", "０", "𝙣", "①"])
def test_compatibility_letters_and_digits_cannot_evade_scenery_d7(mark, source):
    feature = row("GODOT-E07")
    candidate = feature["true_ff"]["authoring_card"]["candidates"][0]
    candidate["original_text_demo"] = "　" + mark
    candidate["glyph_run_citations"][mark] = "D7 claimed styling does not authorize an alphanumeric"
    assert any("D7 forbids scenery alphanumerics" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_archive_presence_and_a_rule_citation_do_not_admit_a_circle_glyph(source):
    feature = row("GODOT-E07")
    candidate = feature["true_ff"]["authoring_card"]["candidates"][0]
    candidate["original_text_demo"] = "　○"
    candidate["glyph_run_citations"]["○"] = "D4 whole mark, but not an approved mode-qualified sequence"
    assert any("outside bounded scenery vocabulary/native catalog" in e
               for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_explicitly_admitted_cjk_person_is_not_a_latin_d7_letter():
    assert not gate._true_ff_scenery_alphanumeric("人")
    assert "人" in gate._true_ff_scenery_character_inventory()


@pytest.fixture
def compiled_scenery_row(monkeypatch):
    # FL-4917: production removed the bad tracks. Reintroduce bad input through
    # the real loader, not by replacing the admission predicate under test.
    original = gate.load_registry
    path = gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"
    document = original(path)

    def install(role, language, track, sequence):
        rows = document["run_table"]["rows"]
        rows[:] = [row for row in rows
                   if (row["role"], row["language"], row["track"]) != (role, language, track)]
        rows.append(dict(role=role, language=language, track=track,
                         sequence=sequence, substitute=sequence))
        gate._true_ff_run_bindings.cache_clear()
        gate._true_ff_forbidden_run_bindings.cache_clear()

    monkeypatch.setattr(gate, "load_registry", lambda candidate:
                        document if Path(candidate) == path else original(candidate))
    yield install
    gate._true_ff_run_bindings.cache_clear()
    gate._true_ff_forbidden_run_bindings.cache_clear()


@pytest.mark.parametrize("track,sequence,forbidden", [("fork_up", "Y", True),
                                                      ("fork_down", "人", False)])
def test_named_compiled_track_does_not_bless_latin_y_but_preserves_person_contour(
        track, sequence, forbidden, source, compiled_scenery_row):
    compiled_scenery_row("generic", "hard", track, sequence)
    feature = row("GODOT-T09")
    feature["true_ff"]["required"]["runs"] = [
        {"role": "generic", "language": "hard", "tracks": [track]}]
    errors = gate.validate_true_ff_mappings([feature], base_dir=source)
    assert any("D7 rejects active compiled" in error for error in errors) == forbidden


@pytest.mark.parametrize("sequence,forbidden", [("O", True), ("。", False)])
def test_waterfall_drop_track_cannot_reintroduce_latin_o_as_scenery(
        sequence, forbidden, source, compiled_scenery_row):
    compiled_scenery_row("waterfall_impact", "water", "waterfall_impact.drop", sequence)
    feature = row("GODOT-W10")
    feature["true_ff"]["required"]["runs"] = [
        {"role": "waterfall_impact", "language": "water", "tracks": ["waterfall_impact.drop"]}]
    assert any("D7 rejects active compiled" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source)) == forbidden


@pytest.mark.parametrize("binding", [
    {"sequence": "≈≈", "mode": "sparse_scatter"},
    {"sequence": "≈", "mode": "empty"},
    {"sequence": "≈≈", "mode": "invented"},
])
def test_character_inventory_cannot_replace_whole_sequence_mode_admission(binding, source):
    feature = row("GODOT-E07")
    feature["true_ff"]["authoring_card"]["candidates"][0]["sequence_bindings"] = [binding]
    assert any("sequence/mode is not explicitly admitted" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_approved_double_crest_does_not_admit_its_single_character(source):
    feature = row("GODOT-E07")
    candidate = feature["true_ff"]["authoring_card"]["candidates"][0]
    candidate["original_text_demo"] = "　≈"
    assert any("no explicit admitted sequence segmentation" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("key,value", [("native_baseline_offset_px", 0),
                                      ("source_font_sha256", "unverified")])
def test_native_baseline_and_font_provenance_are_not_card_self_assertions(key, value, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["native_metrics"][key] = value
    assert any("independent catalog" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("char,value", [("≈", 16), ("　", 16), ("⌒", True)])
def test_native_advance_lies_fail_even_when_the_text_cache_is_self_consistent(char, value, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["native_metrics"]["glyph_advances_px"][char] = value
    assert any("advance must match independent catalog" in error
               for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("field", ["illustrative_row_origins_px", "illustrative_row_origins_y_px",
                                   "illustrative_row_baselines_px"])
def test_native_position_caches_cannot_override_character_sequence(field):
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-E01")
    feature["true_ff"]["authoring_card"]["native_metrics"][field] = [[999]]
    assert any("cached positions must derive" in error
               for error in gate.validate_true_ff_mappings([feature]))


@pytest.mark.parametrize("mutation", ["wrong_class", "activate_former", "unpin_former"])
def test_star_class_bindings_and_deleted_losers_are_checked_by_the_gate(mutation):
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-E01")
    card = feature["true_ff"]["authoring_card"]
    if mutation == "wrong_class":
        card["candidates"][0]["sequence_bindings"][0]["class"] = 2
        expected = "named class/mode"
    else:
        binding = card["candidates"][1]["sequence_bindings"][2]
        if mutation == "activate_former":
            binding["active"] = True
            expected = "inactive rejected record"
        else:
            binding["source_revision"] = "HEAD"
            expected = "full commit"
    assert any(expected in error for error in gate.validate_true_ff_mappings([feature]))


def test_former_binding_cannot_misstate_its_card_comparison_commit():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-E01")
    feature["true_ff"]["authoring_card"]["comparison_basis_commit"] = "58656844a6ffd0603c127152407f3c091247fbe8"
    assert any("pinned comparison basis" in error for error in gate.validate_true_ff_mappings([feature]))


@pytest.mark.parametrize("former", [False, True])
def test_star_owner_cannot_be_erased_to_claim_generic_vocabulary_admission(former):
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-E01")
    card = feature["true_ff"]["authoring_card"]
    binding = card["candidates"][1 if former else 0]["sequence_bindings"][2 if former else 0]
    binding.pop("former_table" if former else "table")
    binding.pop("source_revision", None)
    assert any("required authored owner" in error for error in gate.validate_true_ff_mappings([feature]))


def test_authored_compound_cannot_be_replaced_by_admitted_component_bindings():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-W06")
    candidate = feature["true_ff"]["authoring_card"]["candidates"][1]
    candidate["sequence_bindings"] = [b for b in candidate["sequence_bindings"] if b["sequence"] != "(⌒"]
    candidate["sequence_bindings"].append({"sequence": "⌒", "mode": "empty"})
    assert any("authored compound '(⌒'" in error for error in gate.validate_true_ff_mappings([feature]))


def test_native_text_card_does_not_require_redundant_coordinate_caches():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-E01")
    # Validate card geometry in isolation, not an incomplete audit graph.
    feature["true_ff"].pop("manual_audit", None)
    metrics = feature["true_ff"]["authoring_card"]["native_metrics"]
    for field in ("illustrative_row_origins_px", "illustrative_row_origins_y_px", "illustrative_row_baselines_px"):
        metrics.pop(field)
    assert gate.validate_true_ff_mappings([feature]) == []


@pytest.mark.parametrize("field,value", [("font_px", 17), ("row_pitch_px", 16)])
def test_card_font_geometry_must_match_independent_catalog(field, value, source):
    feature = row()
    feature["true_ff"]["authoring_card"]["native_metrics"][field] = value
    assert any("match independent catalog" in error for error in gate.validate_true_ff_mappings([feature], base_dir=source))


@pytest.mark.parametrize("runs", [None, {}, [{"role": "star", "tracks": None}]])
def test_malformed_authored_owner_runs_are_red_not_exceptions(runs, source):
    feature = row()
    feature["true_ff"]["required"]["runs"] = runs
    assert gate.validate_true_ff_mappings([feature], base_dir=source)


@pytest.mark.parametrize("stage", [{}, {"event_identity": "fixture", "stage": "ring", "age_seconds": True},
                                   {"event_identity": "fixture", "stage": "ring", "age_seconds": float("nan")}])
def test_empty_or_nonfinite_same_clock_signature_is_not_a_comparison(stage, source):
    feature = row("GODOT-E07")
    for candidate in feature["true_ff"]["authoring_card"]["candidates"]:
        candidate["single_time_world_clock_stage"] = stage
    assert any("same event facts" in e for e in gate.validate_true_ff_mappings([feature], base_dir=source))


def test_terrain_meaning_does_not_claim_the_ff_empty_style_already_exists_in_non_ff():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    rows = {f["id"]: f["true_ff"] for f in document["features"]}
    assert "elevation/shade bands" in rows["GODOT-T01"]["meaning"]
    assert ":198-226" in rows["GODOT-T01"]["meaning_refs"][1]
    assert "quiet interiors" not in rows["GODOT-T09"]["meaning"].lower()
    assert len(rows["GODOT-T09"]["meaning_refs"]) == 3
    for feature_id in ("GODOT-T01", "GODOT-T09"):
        assert "mno/mountain/res01.txt:1-13" in rows[feature_id]["archive_refs"][0]
        assert not any("haikei/resK-01" in ref for ref in rows[feature_id]["archive_refs"])


def test_historical_dash_dust_is_not_relabelled_as_generic_footfalls():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-A16")
    assert "source_gap" not in mapping
    assert "historical" in mapping["meaning"]
    assert "authoritative dash" in mapping["meaning"]
    assert all(ref.endswith("@ commit 0de6f35798f1d78f5d1883b04e24ba2c615831ba")
               for ref in mapping["meaning_refs"])
    assert "dash_ticks_remaining > 0" in mapping["required"]["tempo"]["behavior"]
    assert "six" in mapping["required"]["cost"]
    assert "subsequently deleted" in mapping["implementation_gap"]
    assert all("bakuhatsu-kemuri/resK-" in ref for ref in mapping["archive_refs"])


def test_actor_vapor_family_name_cannot_invent_a_humanoid_trigger():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-A18")
    assert "does not independently require a humanoid" in mapping["meaning"]
    assert "source_gap" in mapping
    assert all("bakuhatsu-kemuri/resK-" in ref for ref in mapping["archive_refs"])
    assert "figure" not in str(mapping["archive_refs"])


def test_projectile_smoke_shape_references_do_not_fabricate_the_missing_emitter():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-A21")
    assert mapping["meaning_refs"] == [] and "source_gap" in mapping
    assert all("bakuhatsu-kemuri/resK-" in ref for ref in mapping["archive_refs"])
    assert "figure" not in str(mapping["archive_refs"]) and "starlit" not in str(mapping["archive_refs"])


def test_star_cards_are_whole_native_fields_not_arbitrary_sprite_positions():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-E01")
    # Full-registry tests retain and validate these cross-feature dependencies.
    mapping.pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([{"id": "GODOT-E01", "true_ff": mapping}]) == []
    card = mapping["authoring_card"]
    assert card["selected"] == "A" and card["active_candidates"] == ["A"]
    advances = card["native_metrics"]["glyph_advances_px"]
    assert card["native_metrics"]["illustrative_row_origins_y_px"] == [0, 17, 34]
    assert card["native_metrics"]["illustrative_row_baselines_px"] == [14, 31, 48]
    assert card["native_metrics"]["native_baseline_offset_px"] == 14
    for candidate in card["candidates"]:
        observed = []
        for line in candidate["original_text_demo"].splitlines():
            x = 0
            origins = []
            for char in line:
                if char != "　":
                    origins.append(x)
                x += advances[char]
            observed.append(origins)
        assert observed == card["native_metrics"]["illustrative_row_origins_px"]
        assert len([c for c in candidate["original_text_demo"] if not c.isspace()]) == 6
    policy = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/outline_authoring.v1.json")
    language = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json")
    assert policy["sky_authoring"]["star_classes"][2]["idioms"] == [["."]]
    assert language["sky_authoring"]["star_classes"][2]["idioms"] == [["."]]
    selected = next(candidate for candidate in card["candidates"] if candidate["id"] == card["selected"])
    classes = {entry["class"]: entry for entry in language["sky_authoring"]["star_classes"]}
    for binding in selected["sequence_bindings"]:
        assert binding["table"] == "sky_authoring.star_classes"
        assert classes[binding["class"]]["mode"] == binding["mode"]
        assert [binding["sequence"]] in classes[binding["class"]]["idioms"]
    for candidate in card["candidates"]:
        for binding in candidate["sequence_bindings"]:
            if binding["sequence"] == "'":
                assert binding["active"] is False
                assert binding["source_revision"] == card["comparison_basis_commit"]
                assert "former_table" in binding and "table" not in binding


def test_regeneration_retains_cards_without_reauthoring_them(source, monkeypatch):
    original = row()
    original["true_ff"]["authoring_card"] = {"selected": None, "candidates": ["a", "b", "c"]}
    registry_path = source / "registry.json"
    registry_path.write_text(json.dumps({"features": [original]}))
    monkeypatch.setattr(builder, "build_documents", lambda **kw: (
        {"features": [{"id": original["id"], "name": original["name"]}]}, {"feature_ids": [original["id"]]}
    ))
    builder.write_outputs(registry_path=registry_path, census_path=source / "census.json")
    rebuilt = json.loads(registry_path.read_text())
    assert rebuilt["features"][0]["true_ff"] == original["true_ff"]


def test_inventory_cards_compare_one_published_state_not_three_interactions():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-U61")
    # Card-only fixture; full-registry validation checks the audit graph.
    feature["true_ff"].pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([feature]) == []
    card = feature["true_ff"]["authoring_card"]
    facts = card["comparison_facts"]
    assert facts["published_inventory"] == "potion_red*1@0"
    assert facts["equipment_loadout"] == []
    assert facts["selected_item_id"] == "potion_red" and facts["selected_cell"] == 0
    assert facts["open"] and not facts["move_mode"] and facts["pending_intent"] is None
    for candidate in card["candidates"]:
        rows = candidate["original_text_demo"].splitlines()
        assert len(rows) == 6 and rows[-2:] == ["--", "--"]
        assert rows[0] == "Inventory　1/1"
        assert "Healing Potion" in rows[3] and "x1" in rows[3] and "USE" in rows[3]
        for action in ("Up/Down", "Enter", "Del", "M", "Tab", "Esc"):
            assert action in "\n".join(rows[1:3])
        assert "pickup" in candidate["anchored_animation_part_timing"].lower()
        assert "publication" in candidate["anchored_animation_part_timing"]


def test_inventory_selection_gutter_is_character_derived_not_an_independent_pointer():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-U61")
    card = feature["true_ff"]["authoring_card"]
    assert card["selected"] == "U61-A_leading_identity_gutter"
    advances = card["native_metrics"]["glyph_advances_px"]
    assert advances[">"] == advances["-"] == 8
    assert advances["　"] == 11
    selected = next(c for c in card["candidates"] if c["id"] == card["selected"])
    assert selected["original_text_demo"].splitlines()[3].startswith(">　Healing Potion")
    assert sum(advances[char] for char in ">　") == sum(advances[char] for char in "-　") == 19


def test_inventory_meaning_citations_do_not_use_the_existing_ff_page_builder():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U61")
    assert "godot_project/scripts/ui/InventoryPanel.gd:186-218" not in mapping["meaning_refs"]
    # FL-4917: 108-109 mutate the FF selection/dirty state, not source meaning.
    assert "godot_project/scripts/ui/InventoryPanel.gd:107-127" not in mapping["meaning_refs"]
    assert "godot_project/scripts/ui/InventoryPanel.gd:107" in mapping["meaning_refs"]
    assert "godot_project/scripts/ui/InventoryPanel.gd:110-125" in mapping["meaning_refs"]
    assert "godot_project/scripts/ui/InventoryHud.gd:159-202" in mapping["meaning_refs"]
    assert "presentation selector" in mapping["rationale"]
    assert "colyseus_server/src/sim/world.ts:3464-3509" in mapping["meaning_refs"]
    assert "colyseus_server/src/rooms/AuthoritativeRoom.ts:704-722" in mapping["meaning_refs"]
    assert "unrelated world changes" in mapping["world_facts"][1]
    assert "newly authored" in mapping["authoring_card"]["origin_contract"]
    assert "bare potion_red" in mapping["authoring_card"]["comparison_facts"]["serialization_boundary"]
    assert "no-new-publication" not in mapping["depiction"]
    assert "new world snapshot" in mapping["open_work"][1]


def test_chat_candidates_compare_one_current_draft_not_a_coverage_ledger():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-U62")
    # Card-only fixture; full-registry validation checks the audit graph.
    feature["true_ff"].pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([feature]) == []
    card = feature["true_ff"]["authoring_card"]
    facts = card["comparison_facts"]
    assert facts["open"] and facts["focused"] and facts["draft"] == "hello"
    assert facts["caret_index"] == 5 and facts["history_index"] == -1
    assert facts["history"] == facts["retained_local_feedback"] == facts["received_server_lines"] == []
    assert facts["submit_result"] is None
    for candidate in card["candidates"]:
        demo = candidate["original_text_demo"]
        assert demo.count("hello|") == 1 and demo.count("Chat") == 1
        for control in ("Enter submit", "Esc cancel", "Up/Down history"):
            assert control in demo
        for false_fixture in ("Draftempty", "Clienthandoff", "Receiptnotsent", "Refusalserveronly", "sent:"):
            assert false_fixture not in demo


def test_chat_literal_caret_is_native_and_not_the_line_edit_cursor():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U62")
    card = mapping["authoring_card"]
    selected = next(c for c in card["candidates"] if c["id"] == card["selected"])
    assert selected["original_text_demo"].splitlines() == [
        "Chat", "hello|", "Enter submit　Esc cancel　Up/Down history"]
    advances = card["native_metrics"]["glyph_advances_px"]
    assert sum(advances[c] for c in "hello") == 30 and advances["|"] == 4
    assert "newly authored" in card["origin_contract"] and "No blink" in card["origin_contract"]


def test_chat_source_events_keep_local_handoff_separate_from_received_chat():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U62")
    program = mapping["authoring_card"]["event_program"]
    assert "clears draft" in program["open"]
    assert "without dispatch or history insertion" in program["cancel_or_empty_submit"]
    assert "composer closes immediately" in program["submit"]
    assert "Closing suppresses" in program["local_feedback"]
    assert "U65 separately" in program["local_feedback"]
    assert "next-open" in program["local_feedback"]
    assert "no same-submit receipt" in program["local_feedback"]
    assert "labelled handoff, never sent or delivered" in program["local_feedback"]
    assert "source input owner trims" in program["submit"]
    assert "raw-draft publication" in program["submit"]
    assert "Preserve that raw string" in program["edit_or_caret"]
    assert "connection remains unestablished" in program["local_feedback"]
    assert "open integration work" in program["representation_boundary"]
    assert "LineEdit borders" in mapping["rationale"]
    assert "godot_project/scripts/CommandLineInput.gd:121-177" in mapping["meaning_refs"]
    assert "colyseus_server/src/rooms/AuthoritativeRoom.ts:648-701" in mapping["meaning_refs"]


def test_received_chat_candidates_preserve_literal_identity_and_same_event_window():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-U65")
    # Card-only fixture; the live registry retains its complete audit graph.
    feature["true_ff"].pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([feature]) == []
    card = feature["true_ff"]["authoring_card"]
    facts = card["comparison_facts"]
    assert facts["composer_open"] is False and facts["max_retained_entries"] == 8
    assert facts["display_identity"] == facts["ordered_events"][0]["actor_id"] == "mika"
    assert facts["visible_metadata"] == []
    for candidate in card["candidates"]:
        text = candidate["original_text_demo"]
        assert "mika　hello [all]" in text and "Mika" not in text
        assert "handoff" in text and "text_too_long" in text
        assert "[lb]" not in text and "<mika>" not in text
        assert "seq" not in text and "tick" not in text
        assert text.index("hello [all]") < text.index("handoff") < text.index("text_too_long")


def test_received_chat_native_widths_derive_from_literal_catalog_advances():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    card = next(f["true_ff"]["authoring_card"] for f in document["features"] if f["id"] == "GODOT-U65")
    catalog = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json")
    actual = {glyph["char"]: glyph["advance_px"] for glyph in catalog["glyphs"]}
    used = set("".join(c["original_text_demo"] for c in card["candidates"])) - {"\n"}
    assert set(card["native_metrics"]["glyph_advances_px"]) == used
    assert len(used) == 35
    expected = ([144, 115, 128], [100, 115, 191], [40, 100, 111, 153])
    for candidate, widths in zip(card["candidates"], expected):
        assert [sum(actual[char] for char in row) for row in candidate["original_text_demo"].splitlines()] == widths


def test_received_chat_requirement_does_not_invent_notice_producer_or_delivery():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U65")
    card = mapping["authoring_card"]
    assert card["selected"] == "U65-A_role_labeled" and card["active_candidates"] == [card["selected"]]
    assert "unestablished" in card["comparison_facts"]["ordered_events"][1]["producer"]
    assert "not established" in card["comparison_facts"]["deduplication"]
    assert "FF adapter remains unconnected" in card["event_program"]["message"]
    assert "not a current producer claim" in card["event_program"]["local_notice"]
    assert "not visible text" in card["event_program"]["composer"]
    assert "late arrival-order discard" in card["event_program"]["capacity"]


def test_received_chat_fixture_order_and_normalization_are_not_runtime_claims():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U65")
    card = mapping["authoring_card"]
    assert "not an observed cross-event chronology" in card["comparison_facts"]["ordering_evidence"]
    assert "future typed adapter" in card["event_program"]["layout"]
    assert "current source does not store" in card["event_program"]["layout"]
    assert "received validated text" in card["event_program"]["capacity"]
    assert "strips controls and trims" in card["event_program"]["capacity"]
    assert "colyseus_server/src/rooms/AuthoritativeRoom.ts:257-274" in mapping["meaning_refs"]
    assert "godot_project/scripts/net/ColyseusGameClient.gd:213-217" in mapping["meaning_refs"]
    for candidate in card["candidates"]:
        assert "chronology" in candidate["whole_candidate"]


def test_pause_candidates_share_one_legal_collapsed_state_without_world_pause():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-U08")
    # Isolated card geometry; full-registry tests retain dependency closure.
    feature["true_ff"].pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([feature]) == []
    card = feature["true_ff"]["authoring_card"]
    facts = card["comparison_facts"]
    assert facts["menu_open"] is True and facts["world_paused"] is False
    assert facts["controls_expanded"] is False and facts["live_state_expanded"] is False
    assert facts["ff_focus_action"] == "resume"
    assert "not a false startup default" in facts["section_contract"]
    stages = [candidate["single_time_world_clock_stage"] for candidate in card["candidates"]]
    assert stages[0] == stages[1] == stages[2]
    assert "adapter remains unconnected" in card["event_program"]["focus"]
    for candidate in card["candidates"]:
        text = candidate["original_text_demo"]
        assert "Resume" in text and "Controls　closed" in text
        assert "Live State　closed" in text and "Fullscreen　F11" in text
        assert "Paused" not in text and "Tab next" in text


def test_pause_native_demand_and_gutter_derive_from_complete_character_rows():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    card = next(f["true_ff"]["authoring_card"] for f in document["features"] if f["id"] == "GODOT-U08")
    catalog = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json")
    actual = {glyph["char"]: glyph["advance_px"] for glyph in catalog["glyphs"]}
    used = set("".join(c["original_text_demo"] for c in card["candidates"])) - {"\n"}
    assert set(card["native_metrics"]["glyph_advances_px"]) == used and len(used) == 35
    expected = ([36, 107, 129, 143, 110, 123, 47, 304],
                [36, 107, 110, 124, 91, 104, 28, 304],
                [36, 88, 110, 124, 91, 104, 28, 104, 304])
    for candidate, widths, demand in zip(card["candidates"], expected, [129, 119, 129]):
        rows = candidate["original_text_demo"].splitlines()
        assert [sum(actual[char] for char in row) for row in rows] == widths
        assert sum(map(len, rows)) == demand
    assert actual[">"] + actual["　"] == actual["-"] + actual["　"] == 19
    assert card["selected"] == "U08-A_action_gutter"


def test_pause_program_keeps_action_ownership_and_unconnected_window_boundary():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U08")
    program = mapping["authoring_card"]["event_program"]
    assert "shared menu owner handles Escape" in program["open_close"]
    assert "No automatic invocation" in program["actions"]
    assert "No parsing copied panel_text" in program["sections"]
    assert "no renderer holds" in mapping["required"]["tempo"]["behavior"]
    assert "quarter-second refresh" in program["tempo"]
    assert "late arrival-order cap" in program["capacity"]
    assert "remain unconnected" in program["capacity"]
    # Operator-authorized Xanh panel retains the same input/state owners;
    # the native-text event programme above is historical candidate evidence.
    assert "godot_project/scripts/PauseMenu.gd:422-450" in mapping["meaning_refs"]
    assert "godot_project/scripts/PauseMenu.gd:527-542" in mapping["meaning_refs"]
    assert "historical candidate evidence" in mapping["rationale"]


def test_fullscreen_candidates_compare_one_actual_mode_not_a_toggle_receipt():
    # FL-4917: authored Windowed fixtures cannot assert a live mode transition.
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    feature = next(f for f in document["features"] if f["id"] == "GODOT-U06")
    # Isolated card geometry; full-registry tests retain dependency closure.
    feature["true_ff"].pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([feature]) == []
    card = feature["true_ff"]["authoring_card"]
    assert card["comparison_facts"]["actual_mode_label"] == "Windowed"
    assert card["comparison_facts"]["pending_input"] is False
    stages = [candidate["single_time_world_clock_stage"] for candidate in card["candidates"]]
    assert stages[0] == stages[1] == stages[2]
    assert "no pending input" in stages[0]["stage"]
    for candidate in card["candidates"]:
        assert candidate["original_text_demo"].count("Windowed") == 1
        assert candidate["original_text_demo"].count("F11") == 1
        assert "Fullscreen" in candidate["original_text_demo"]


def test_fullscreen_native_rows_and_demand_come_from_literal_advances():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    card = next(f["true_ff"]["authoring_card"] for f in document["features"] if f["id"] == "GODOT-U06")
    catalog = gate.load_registry(gate.REPO_ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json")
    actual = {glyph["char"]: glyph["advance_px"] for glyph in catalog["glyphs"]}
    used = set("".join(c["original_text_demo"] for c in card["candidates"])) - {"\n"}
    assert set(card["native_metrics"]["glyph_advances_px"]) == used and len(used) == 18
    for candidate, widths, demand in zip(card["candidates"], ([68, 112, 75], [144, 75], [65, 68, 75]), [33, 29, 28]):
        rows = candidate["original_text_demo"].splitlines()
        assert [sum(actual[char] for char in row) for row in rows] == widths
        assert sum(map(len, rows)) == demand
    assert sum(actual[char] for char in "Mode　") == 47
    assert sum(actual[char] for char in "F11　") == 36
    assert card["selected"] == "U06-A_named_mode" and card["active_candidates"] == [card["selected"]]


def test_fullscreen_requirement_keeps_actual_owner_and_composition_gaps():
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U06")
    program = mapping["authoring_card"]["event_program"]
    assert "godot_project/scripts/PauseMenu.gd:618-637" in mapping["meaning_refs"]
    assert "historical candidate evidence" in mapping["rationale"]
    assert "godot_project/scripts/PauseMenu.gd:318-359" not in mapping["meaning_refs"]
    assert "Exclusive Fullscreen" in program["input"]
    assert "never infer success" in program["state"] and "not connected" in program["state"]
    assert "Preserve every actual enum distinctly" in program["state"]
    assert "additional modes" in program["state"]
    assert "one fullscreen action identity" in program["integration"]
    assert "remain unconnected" in program["integration"]
    assert "late arrival cap" in program["capacity"]
    assert "No fabricated world-clock cycle" in program["timing"]


def test_pause_control_fold_preserves_all_actions_and_continuing_world():
    # FL-4917: this fold carries actual meanings, not only compatible run names.
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    source = next(f for f in document["features"] if f["id"] == "GODOT-U05")
    target = next(f for f in document["features"] if f["id"] == "GODOT-U08")
    assert source["true_ff"]["mapping"] == "FOLD"
    assert source["true_ff"]["fold_into"] == [target["id"]]
    assert target["true_ff"]["mapping"] == "ADAPT"
    # This two-row card/fold fixture deliberately excludes the wider gameplay
    # dependency graph; the full-registry test validates that graph separately.
    source["true_ff"].pop("manual_audit", None)
    target["true_ff"].pop("manual_audit", None)
    assert gate.validate_true_ff_mappings([source, target]) == []
    card = target["true_ff"]["authoring_card"]
    assert card["comparison_facts"]["world_paused"] is False
    for candidate in card["candidates"]:
        for action in ("Resume", "Map", "Fullscreen", "Quit"):
            assert action in candidate["original_text_demo"]
    program = card["event_program"]
    for behavior in ("Resume closes", "Map opens", "Fullscreen requests", "Quit invokes"):
        assert behavior in program["actions"]
    assert "Server simulation continues" in program["open_close"]
    assert "adapter remains unconnected" in program["focus"]


def test_pause_cards_name_existing_owner_and_direct_control_definitions():
    # FL-4917: semantic cards cannot retain a nonexistent metadata owner.
    document = gate.load_registry(gate.REPO_ROOT / "config/godot_feature_regressions.json")
    for feature_id in ("GODOT-U06", "GODOT-U08"):
        feature = next(f for f in document["features"] if f["id"] == feature_id)
        # The source/card fixture does not include the other audited rows.
        feature["true_ff"].pop("manual_audit", None)
        assert feature["source_owners"] == ["godot_project/scripts/PauseMenu.gd"]
        assert all((gate.REPO_ROOT / owner).is_file() for owner in feature["source_owners"])
        assert gate.validate_true_ff_mappings([feature]) == []
    mapping = next(f["true_ff"] for f in document["features"] if f["id"] == "GODOT-U08")
    assert "godot_project/scripts/ControlsRegistry.gd:93-125" in mapping["meaning_refs"]
    assert "godot_project/scripts/ControlsRegistry.gd:132-178" in mapping["meaning_refs"]
    lines = (gate.REPO_ROOT / "godot_project/scripts/ControlsRegistry.gd").read_text().splitlines()
    assert "const TOGGLES" in "\n".join(lines[92:125])
    assert all(token in "\n".join(lines[92:125]) for token in ('"id"', '"key"', '"label"'))
    assert "const INFO_ONLY" in "\n".join(lines[131:178])
    assert all(token in "\n".join(lines[131:178]) for token in ('KEY_F11', 'KEY_ESCAPE', '"Fullscreen"'))
