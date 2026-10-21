"""Offline units for the FL-4512 G2/G3 effects and sky subject tables.

Reads the compiled outline language only. No Godot, GPU, capture or replay.
These checks establish authored-data validity, not renderer output.
Budget-class checks preserve the authored allocation requirements; they do not
test runtime enforcement or require the deleted frame-wide class cutoff.
"""
import copy
import json
import unittest
from pathlib import Path

from scripts.compile_true_ff_outline_language import (
    validate_sky_authoring, validate_subject_keyframes)

ROOT = Path(__file__).resolve().parents[2]
LANGUAGE = ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"


def load():
    policy = json.loads(LANGUAGE.read_text())
    admitted = {(g["mode"], text) for g in policy["groups"] for text in g["sequences"]}
    return policy, admitted


def subject(policy, name):
    return next(s for s in policy["subject_keyframes"] if s["subject"] == name)


def static_probe(policy):
    """A valid static (cloud_field) subject built from the moon's parts, so the
    compiler's static-part rule stays exercised without a cloud part table."""
    probe = copy.deepcopy(subject(policy, "moon"))
    probe.update(subject="static_probe", source="cloud_field", clock="static")
    for part in probe["parts"]:
        part["alive"] = [[0.0, 1.0]]
        part.pop("keys", None)
        part.pop("keys_by_tier", None)
    policy["subject_keyframes"].append(probe)
    return probe


def spelling_at(part, tier, value):
    """The renderer's selection rule: alive interval, then the last key <= value."""
    if part["birth_tier"] > tier or not any(lo <= value < hi for lo, hi in part["alive"]):
        return None
    chosen = part["idiom_by_tier"][tier]
    keys = part["keys_by_tier"][tier] if "keys_by_tier" in part else part.get("keys", [])
    for at, spelling in keys:
        if at <= value:
            chosen = spelling
    return "".join(chosen)


class EffectsSkySubjects(unittest.TestCase):
    def test_every_proposal_subject_has_one_source_owner(self):
        policy, admitted = load()
        validate_subject_keyframes(policy, admitted)
        validate_sky_authoring(policy, admitted)
        names = {s["subject"]: s for s in policy["subject_keyframes"]}
        # FL-4900 lane E: clouds left the part tables; their one owner is the
        # perimeter law (sky_authoring.cloud_perimeter).
        self.assertEqual(set(names), {"smoke_puff", "bonfire_flame", "explosion",
                                      "vapor_trail", "impact", "projectile_burst", "moon"})
        kinds = [k for s in names.values() for k in s.get("dynamic_kinds", [])]
        self.assertEqual(sorted(kinds), sorted({"smoke_sample", "bonfire_flame",
                         "explosion_sample", "vapor_sample", "impact_sample"}))
        self.assertEqual({s["budget_class"] for s in names.values()}, {"effects", "sky"})
        # Rain keeps its weather-site path: no subject claims the rain kind.
        self.assertNotIn("rain", kinds)

    def test_growing_tiers_only_add_outlines_and_contour_marks(self):
        policy, _ = load()
        for item in policy["subject_keyframes"]:
            for part in item["parts"]:
                self.assertIn(part["role"], ("contour", "sparse"), item["subject"])
                for tier in range(4):
                    present = part["idiom_by_tier"][tier] is not None
                    self.assertEqual(present, tier >= part["birth_tier"],
                                     f"{item['subject']}/{part['id']}")
                if part["role"] == "sparse":
                    attach = part.get("attach")
                    if attach is not None:
                        self.assertIn(attach, {p["id"] for p in item["parts"]})

    def test_moon_draws_real_phases_from_the_phase_fact(self):
        policy, _ = load()
        moon = {p["id"]: p for p in subject(policy, "moon")["parts"]}

        def drawn(phase, tier=2):
            return {pid: spelling_at(p, tier, phase) for pid, p in moon.items()
                    if spelling_at(p, tier, phase) is not None}

        self.assertEqual(drawn(0.0), {})
        self.assertEqual(drawn(0.99), {})
        waxing = drawn(0.10)
        self.assertEqual(set(waxing), {"RU", "RL", "TOP", "BOT", "TERM"})
        self.assertEqual(waxing["TERM"], ")")
        self.assertEqual(drawn(0.25)["TERM"], "|")
        self.assertEqual(drawn(0.40)["TERM"], "(")
        full = drawn(0.50)
        self.assertEqual(set(full), {"LU", "RU", "LL", "RL", "TOP", "BOT"})
        self.assertEqual(full["TOP"], "-―-")
        waning = drawn(0.90)
        self.assertEqual(set(waning), {"LU", "LL", "TOP", "BOT", "TERM"})
        self.assertEqual(drawn(0.75)["TERM"], "|")
        self.assertEqual(drawn(0.10, tier=0), {"RU": "）"})
        self.assertEqual(drawn(0.90, tier=0), {"LU": "（"})

    def test_stars_are_nested_without_twinkle(self):
        policy, _ = load()
        sky = policy["sky_authoring"]
        self.assertFalse(sky["star_twinkle"])
        self.assertLessEqual(len(sky["stars"]), sky["star_cap"])
        by_class = {}
        for _, _, elevation, klass, night_min in sky["stars"]:
            self.assertGreater(elevation, 0.0)
            by_class.setdefault(klass, []).append(night_min)
        self.assertLessEqual(len(by_class[1]), 6)
        self.assertLess(max(by_class[1]), min(by_class[2]))
        self.assertLess(max(by_class[2]), min(by_class[3]))

    def test_invalid_authoring_fails_closed(self):
        policy, admitted = load()
        mutations = {
            "sky_claims_fact": lambda p: subject(p, "moon").update(dynamic_kinds=["rain"]),
            "double_claim": lambda p: subject(p, "vapor_trail").update(
                dynamic_kinds=["smoke_sample"]),
            "static_key": lambda p: static_probe(p)["parts"][0].update(keys=[[0.5, ["⌒"]]]),
            "moon_clock": lambda p: subject(p, "moon").update(clock="age_over_fade"),
            "shards_on_smoke": lambda p: subject(p, "smoke_puff").update(
                shards=copy.deepcopy(subject(p, "explosion")["shards"])),
            "shard_fill": lambda p: subject(p, "explosion")["shards"].update(role="bounded_fill"),
            "bad_ink": lambda p: subject(p, "impact").update(ink_rgb=[1.2, 0.0, 0.0]),
            "no_share": lambda p: subject(p, "moon").update(budget_class="terrain"),
            "three_lifetimes": lambda p: next(
                part for part in subject(p, "moon")["parts"] if part["id"] == "TERM").update(
                alive=[[0.03, 0.2], [0.3, 0.47], [0.53, 0.97]]),
            "too_many_parts": lambda p: subject(p, "explosion")["parts"].extend(
                copy.deepcopy(subject(p, "explosion")["parts"][:1]) * 1),
        }
        valid_probe = copy.deepcopy(policy)
        static_probe(valid_probe)
        validate_subject_keyframes(valid_probe, admitted)
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(policy)
            mutate(candidate)
            with self.assertRaises(ValueError, msg=name):
                validate_subject_keyframes(candidate, admitted)
        sky_mutations = {
            "twinkle": lambda s: s.update(star_twinkle=True),
            "rain_owner": lambda s: s.update(precipitation_owner="subject_table"),
            "unnested": lambda s: s["stars"][0].__setitem__(4, 0.99),
            "star_glyph": lambda s: s["star_classes"][0].update(idioms=[["NOT"]]),
            "phase_owner": lambda s: s["moon_phase"].update(fact="derived_from_direction"),
            "cloud_lattice": lambda s: s["cloud_field"].update(sites_per_side=500),
        }
        for name, mutate in sky_mutations.items():
            candidate = copy.deepcopy(policy)
            mutate(candidate["sky_authoring"])
            with self.assertRaises(ValueError, msg=name):
                validate_sky_authoring(candidate, admitted)


if __name__ == "__main__":
    unittest.main()
