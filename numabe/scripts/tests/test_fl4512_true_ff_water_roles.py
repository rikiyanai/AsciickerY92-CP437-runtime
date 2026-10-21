"""Offline checks for the typed True FF water depiction grammar.

These checks read the pinned TRAIN aggregate and native Saitamaar catalog only.
They do not launch Godot or claim runtime selector wiring or visible acceptance.
"""
import copy
import json
import math
import os
import unittest
from pathlib import Path

from scripts.compile_true_ff_outline_language import compile_language, native_shapes, validate_water_role_table

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "godot_project/assets/glyphs/true_ff/outline_authoring.v1.json"
LANGUAGE = ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"
CORPUS = Path(os.environ.get("TRUE_FF_TRAIN_CORPUS", str(
    ROOT.parent / "unicode-glyph-morphology-explorer/assets/glyphs/corpus/"
    "aahub_aa003_train.sjis_combos.v1.json")))
CATALOG = ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json"


def load_inputs():
    policy = json.loads(POLICY.read_text())
    language = json.loads(LANGUAGE.read_text())
    source = json.loads(CORPUS.read_text())
    catalog = json.loads(CATALOG.read_text())
    glyphs = {row["char"]: row for row in catalog["glyphs"]}
    return policy, language, source, glyphs


class TrueFFWaterRoles(unittest.TestCase):
    def test_unconsumed_labels_are_not_reported_as_runtime_inputs(self):
        policy, language, _, _ = load_inputs()
        roles = policy["depiction_grammar"]["subjects"]["water_surface"]["role_table"]["roles"]
        authored = {candidate["selector_key"]: candidate["geometry"]
                    for role in roles if role["mode"] != "sparse_scatter"
                    for candidate in role["candidates"]}
        for row in language["run_table"]["rows"]:
            if row["track"] in authored:
                self.assertEqual(row["unselected_inputs"],
                                 {key: value for key, value in authored[row["track"]].items()
                                  if key not in {"angle", "extent_px", "bow_ratio", "bank_side", "height_px"}})

    def test_production_language_regenerates_from_authored_inputs(self):
        # Exercise the actual compiler, not a generated table compiled back
        # into itself. This caught committed run rows whose authoring fields
        # existed only in a concurrent worktree (FL-4512 / FL-4902).
        compiled = compile_language(CORPUS.read_bytes(), POLICY.read_bytes(),
                                    CATALOG.read_bytes(),
                                    CATALOG.with_name("atlas.png").read_bytes())
        self.assertEqual(json.dumps(compiled, ensure_ascii=False, indent=2) + "\n",
                         LANGUAGE.read_text())

    def test_roles_are_typed_sparse_and_admitted_into_existing_modes(self):
        policy, language, source, glyphs = load_inputs()
        admissions = []
        compiled_table = validate_water_role_table(
            policy, source, glyphs, lambda *entry: admissions.append(entry))
        self.assertEqual(compiled_table, language["water_role_admissions"])
        # FL-4512: one whole approved pair replaces the two superseded level
        # spellings. Joints stay distinct; no runtime reachability is implied.
        self.assertEqual(len(admissions), 34)
        table = policy["depiction_grammar"]["subjects"]["water_surface"]["role_table"]
        self.assertEqual([row["role"] for row in table["roles"]],
                         ["shoreline", "crest", "ripple", "spray", "shore_break",
                          "waterfall_ledge", "waterfall_strand", "waterfall_impact"])
        self.assertEqual(table["anti_blanket"]["distribution"], "feature_relative_sparse")
        self.assertIn("parallel_line_blanket", table["anti_blanket"]["forbid"])
        compiled = {row["role"]: row for row in language["water_role_admissions"]}
        groups = {(row["mode"], text)
                  for row in language["groups"] for text in row["sequences"]}
        for row in table["roles"]:
            self.assertIn(row["role"], compiled)
            self.assertEqual(compiled[row["role"]]["selection"], row["selection"])
            self.assertEqual(compiled[row["role"]]["orientation_tracks"],
                             row.get("orientation_tracks", []))
            self.assertIn("foreground_depth", row["required_feature_facts"])
            self.assertEqual(len({candidate["selector_key"] for candidate in row["candidates"]}),
                             len(row["candidates"]))
            for candidate in row["candidates"]:
                self.assertIn((row["mode"], candidate["sequence"]), groups)
                self.assertIn("direction", candidate["geometry"])
                self.assertIn("extent_px", candidate["geometry"])
                self.assertEqual(candidate["native_advance_px"], next(
                    item["native_advance_px"]
                    for item in compiled[row["role"]]["candidates"]
                    if item["sequence"] == candidate["sequence"]))

    def test_disjoint_repeated_glyphs_are_allowed_but_geometry_ties_are_not(self):
        policy, _, source, glyphs = load_inputs()

        # The same native glyph may be used by disjoint authored intervals.
        repeated = copy.deepcopy(policy)
        repeated_strand = next(row for row in repeated["depiction_grammar"][
            "subjects"]["water_surface"]["role_table"]["roles"]
            if row["role"] == "waterfall_strand")
        repeated_strand["candidates"][1]["sequence"] = repeated_strand["candidates"][0]["sequence"]
        admitted = validate_water_role_table(repeated, source, glyphs, lambda *entry: None)
        admitted_strand = next(row for row in admitted if row["role"] == "waterfall_strand")
        self.assertEqual([candidate["sequence"] for candidate in admitted_strand["candidates"][:2]],
                         ["|", "|"])

        # Reusing the glyph does not relax the geometry/key partition rules.
        tied = copy.deepcopy(repeated)
        tied_strand = next(row for row in tied["depiction_grammar"][
            "subjects"]["water_surface"]["role_table"]["roles"]
            if row["role"] == "waterfall_strand")
        tied_strand["candidates"][1]["geometry"] = copy.deepcopy(
            tied_strand["candidates"][0]["geometry"])
        with self.assertRaises(ValueError):
            validate_water_role_table(tied, source, glyphs, lambda *entry: None)

    def test_operator_crest_pair_is_compiled_without_fabricating_train_evidence(self):
        policy, language, source, glyphs = load_inputs()
        rows = [r for r in language["run_table"]["rows"]
                if r["track"].startswith("crest.arm.level.")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sequence"], "≈≈")
        self.assertEqual(rows[0]["phase_advance_px"], 14)
        crest = next(r for r in language["water_role_admissions"] if r["role"] == "crest")
        pair = next(c for c in crest["candidates"] if c["sequence"] == "≈≈")
        self.assertEqual(pair["train_sections"], [])
        self.assertEqual(pair["operator_decision"]["form"], "≈≈")
        self.assertIn("REVERSED_TO_APPROX_PAIR", pair["operator_decision"]["ref"])
        self.assertEqual(sum(glyphs[c]["advance_px"] for c in "≈≈"), 14)
        for mutation in ("no_decision", "wrong_form", "false_train"):
            broken = copy.deepcopy(policy)
            role = next(r for r in broken["depiction_grammar"]["subjects"][
                "water_surface"]["role_table"]["roles"] if r["role"] == "crest")
            candidate = next(c for c in role["candidates"] if c["sequence"] == "≈≈")
            if mutation == "no_decision":
                candidate.pop("operator_decision")
            elif mutation == "wrong_form":
                candidate["operator_decision"]["form"] = "～～"
            else:
                candidate["source_sections"] = ["bigrams_outline"]
            with self.assertRaises(ValueError, msg=mutation):
                validate_water_role_table(broken, source, glyphs, lambda *entry: None)

    def test_compiler_rejects_malformed_orientation_tracks(self):
        policy, _, source, glyphs = load_inputs()
        role_path = ("depiction_grammar", "subjects", "water_surface", "role_table", "roles")

        def role_for(candidate, name):
            roles = candidate
            for key in role_path:
                roles = roles[key]
            return next(row for row in roles if row["role"] == name)

        malformed = {}
        candidate = copy.deepcopy(policy)
        role_for(candidate, "waterfall_ledge")["orientation_tracks"] = ["does_not_exist"]
        malformed["unknown"] = candidate

        candidate = copy.deepcopy(policy)
        role_for(candidate, "waterfall_ledge")["orientation_tracks"] = ["level", "level"]
        malformed["duplicate"] = candidate

        candidate = copy.deepcopy(policy)
        role_for(candidate, "waterfall_ledge")["orientation_tracks"] = ["hard_upper_left_corner"]
        malformed["wrong_language"] = candidate

        candidate = copy.deepcopy(policy)
        role_for(candidate, "waterfall_ledge")["orientation_tracks"] = ["soft_arch"]
        malformed["curved"] = candidate

        candidate = copy.deepcopy(policy)
        role_for(candidate, "spray")["orientation_tracks"] = ["level"]
        malformed["sparse_role"] = candidate

        for value in ([{}], [[]], "level", [3]):
            candidate = copy.deepcopy(policy)
            role_for(candidate, "waterfall_ledge")["orientation_tracks"] = value
            malformed[repr(value)] = candidate

        for label, broken in malformed.items():
            with self.assertRaises(ValueError, msg=label):
                validate_water_role_table(broken, source, glyphs, lambda *entry: None)

    def test_authored_material_height_bands_fit_native_ink_near_a_line(self):
        # Necessary geometry check, not full fit/occlusion/endpoint coverage.
        # Seven centred x origins match fit_origins; the long straight line
        # isolates the height-shadowing bug from visibility and finite ends.
        policy, _, _, glyphs = load_inputs()
        shapes = native_shapes(glyphs, CATALOG.with_name("atlas.png").read_bytes())
        roles = policy["depiction_grammar"]["subjects"]["water_surface"]["role_table"]["roles"]

        def distance(points, advance, angle, height, residual):
            a = math.radians(angle)
            return min(max(abs((x - advance // 2 + shift - residual) * math.sin(a)
                               - (y - height) * math.cos(a)) for x, y in points)
                       for shift in range(-3, 4))

        for role in roles:
            if not role.get("orientation_tracks"):
                continue
            for candidate in role["candidates"]:
                points, advance = [], 0
                for char in candidate["sequence"]:
                    points.extend((x + advance + .5, y + .5) for x, y in shapes[char][0])
                    advance += glyphs[char]["advance_px"]
                geometry = candidate["geometry"]
                alo, ahi = geometry["angle"]
                hlo, hhi = geometry["height_px"]
                for angle in (alo + (ahi - alo - .001) * k / 20 for k in range(21)):
                    for height in (hlo + (hhi - hlo - .001) * k / 20 for k in range(21)):
                        for residual in (-.499, 0, .499):
                            self.assertLessEqual(distance(points, advance, angle, height, residual), 8.5,
                                (candidate["selector_key"], angle, height, residual))
                if candidate["sequence"] == "_,":
                    # The prior full-height row selected low ink for a top-row
                    # source. No horizontal shift could make it fit.
                    self.assertGreater(distance(points, advance, 0, 0, 0), 8.5)

    def test_water_height_intervals_cannot_leave_the_native_row(self):
        policy, _, source, glyphs = load_inputs()
        for interval in ([-1, 8], [8, 18], [8, 8], [float("nan"), 8]):
            broken = copy.deepcopy(policy)
            role = next(r for r in broken["depiction_grammar"]["subjects"]["water_surface"][
                "role_table"]["roles"] if r["role"] == "waterfall_ledge")
            role["candidates"][0]["geometry"]["height_px"] = interval
            with self.assertRaises(ValueError):
                validate_water_role_table(broken, source, glyphs, lambda *entry: None)

    def test_compiler_rejects_non_train_or_wrong_native_candidate(self):
        policy, _, source, glyphs = load_inputs()

        def admit(text, mode, section, evidence):
            admitted[(mode, text)] = {"section": section, "evidence": evidence}

        for mutation in ("source", "advance", "fact", "input", "shape", "tie", "overlap",
                         "nearest", "unknown_geometry", "missing_geometry", "nan", "empty_interval"):
            candidate = copy.deepcopy(policy)
            first = candidate["depiction_grammar"]["subjects"]["water_surface"][
                "role_table"]["roles"][0]
            if mutation == "source":
                first["candidates"][0]["sequence"] = "not_train"
            elif mutation == "advance":
                first["candidates"][0]["native_advance_px"] = 999
            elif mutation == "fact":
                first["required_feature_facts"].remove("shore_intersection")
            elif mutation == "input":
                first["selection"]["required_inputs"].remove("bank_side")
            elif mutation == "shape":
                first["candidates"][0]["geometry"]["bank_side"] = "screen_row"
            elif mutation == "tie":
                first["candidates"][1]["selector_key"] = first["candidates"][0]["selector_key"]
            elif mutation == "overlap":
                first["candidates"][1]["geometry"]["bank_side"] = "lower"
            elif mutation == "nearest":
                first["selection"]["policy"] = "nearest_geometry_candidate"
            elif mutation == "unknown_geometry":
                first["candidates"][0]["geometry"]["screen_row"] = 4
            elif mutation == "missing_geometry":
                del first["candidates"][0]["geometry"]["angle"]
            elif mutation == "nan":
                first["candidates"][0]["geometry"]["angle"][0] = float("nan")
            else:
                first["candidates"][0]["geometry"]["angle"] = [10, 10]
            admitted = {}
            with self.assertRaises(ValueError, msg=mutation):
                validate_water_role_table(candidate, source, glyphs, admit)


if __name__ == "__main__":
    unittest.main()
