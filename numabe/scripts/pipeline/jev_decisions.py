#!/usr/bin/env python3
"""Typed decision boundary between perception and reconstruction (FL-4809 JEV-01).

One normalised observation candidate in, small atomic typed questions out,
each its own backend call. Composition and thresholds live in the rules
jev_thresholds DEFAULT fact. Below threshold routes to more_evidence /
opus_review / human_review / observation_debt, never to world truth.

Jev never owns coordinates, transforms, scale, collision, terrain,
authority, or dimensions: normalise() refuses those keys on input and
_assert_no_geometry() refuses them on output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

SCHEMA = "y92.jev_decisions.v1"

# The only keys a backend call may ever see. Coordinates, yaw, transforms,
# scale, collision, terrain, authority and dimension FIELDS are absent here;
# estimated_height_m may enter as a candidate attribute but must never leave.
INPUT_KEYS = (
    "candidate_id", "semantic_class", "object_value", "frames_seen_count",
    "ocr", "vlm_description", "estimated_height_m", "host_kind",
    "existing_asset_candidates",
)

QUESTION_IDS = (
    "semantic_family", "permanence", "asset_choice", "representation",
    "vegetation_detail", "manual_review",
)

SEMANTIC_FAMILIES = (
    "utility_pole", "street_lamp", "sign_pole", "bench", "bollard",
    "vending_machine", "vegetation", "other", "unresolved",
)

PERMANENCE = ("permanent", "transient", "unresolved")

REPRESENTATIONS = (
    "existing_asset", "procedural_template", "new_asset_required",
    "observation_debt",
)

ROUTINGS = (
    "decided", "more_evidence", "opus_review", "human_review",
    "observation_debt",
)

REFUSED_GEOMETRY = frozenset({
    "coordinates", "geometry", "yaw_deg", "transform", "scale",
    "collision", "terrain", "authority", "dimensions", "position",
    "E", "N", "lon", "lat", "width_m", "height_m", "estimated_height_m",
    "depth_m", "chainage_m", "lateral_offset_m",
})

TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
TYPESAFE_ENV_VAR = "TYPESAFE_API_KEY"
MAPILLARY_OBSERVATION_PREFIX = "obs:mapillary:"


class JevError(Exception):
    """Named failure: missing fixture key, absent API key, refused geometry."""


def _claim_value(properties: dict, name: str):
    node = properties.get(name)
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def normalise(feature: dict) -> dict:
    """Project one IR feature onto exactly INPUT_KEYS. Refuses geometry."""
    props = feature.get("properties", {})
    for banned in ("geometry", "yaw_deg"):
        if banned in props:
            pass  # present in IR but never copied below
    frames = _claim_value(props, "frames_seen")
    out = {
        "candidate_id": feature.get("feature_id"),
        "semantic_class": feature.get("semantic_class"),
        "object_value": _claim_value(props, "object_value"),
        "frames_seen_count": len(frames) if isinstance(frames, list)
        else (0 if frames is None else 1),
        "ocr": _claim_value(props, "ocr"),
        "vlm_description": _claim_value(props, "vlm_description"),
        "estimated_height_m": _claim_value(props, "estimated_height_m"),
        "host_kind": _claim_value(props, "host_kind"),
        "existing_asset_candidates": _claim_value(
            props, "existing_asset_candidates"),
    }
    assert set(out) == set(INPUT_KEYS), set(out) ^ set(INPUT_KEYS)
    return out


def select_current_candidates(canonical_world: dict) -> list[dict]:
    """Select this generation's Mapillary-backed canonical street entities.

    Candidate identity belongs to canonical fusion, not to an older observation
    file.  The supporting-observation edge is the durable membership law: it
    includes both separate Mapillary entities and sightings merged into OSM
    points, once each under the current canonical feature id.
    """
    rows = []
    for feature in canonical_world.get("canonical_features", []):
        observation_ids = feature.get("supporting_observation_ids") or []
        if not any(str(value).startswith(MAPILLARY_OBSERVATION_PREFIX)
                   for value in observation_ids):
            continue
        if feature.get("semantic_class") not in {
                "fixture", "road_marking", "sign", "utility_pole"}:
            raise JevError(
                "Mapillary-backed candidate has unsupported semantic class: "
                f"{feature.get('feature_id')}={feature.get('semantic_class')}")
        if _claim_value(feature.get("properties", {}), "object_value") in (
                None, "", "None"):
            raise JevError(
                "Mapillary-backed candidate lost its object token: "
                f"{feature.get('feature_id')}")
        rows.append(feature)
    rows.sort(key=lambda row: str(row["feature_id"]))
    if len({str(row["feature_id"]) for row in rows}) != len(rows):
        raise JevError("current canonical Jev denominator has duplicate ids")
    return rows


def _assert_no_geometry(record: dict) -> None:
    """Fail closed: no numeric geometry leaves the module."""
    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in REFUSED_GEOMETRY:
                    raise JevError(
                        "geometry leaves jev: key %r at %s" % (key, path))
                walk(value, path + "." + str(key))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, "%s[%d]" % (path, i))
    walk(record)


def _key(normalised: dict) -> str:
    return hashlib.sha256(json.dumps(
        normalised, sort_keys=True).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canned(normalised: dict) -> dict:
    """Deterministic fixture answers: pure function of the normalised input."""
    seed = int(_key(normalised), 16)
    base = 0.35 + (seed % 65) / 100.0
    spread = lambda n: min(0.99, max(0.05, base + (
        ((seed >> (7 * n)) % 21 - 10) / 100.0)))
    cls, oval = normalised["semantic_class"], normalised["object_value"] or ""
    family, default_conf = "other", spread(0)
    if (seed >> 13) % 29 == 0:
        family, default_conf = "unresolved", spread(0)
    elif cls == "utility_pole":
        family = "utility_pole"
    elif cls == "sign":
        family = "sign_pole"
    elif cls == "road_marking":
        family = "other"
    elif cls == "fixture":
        for name in ("lamp", "bench", "bollard", "vending", "vegetation",
                     "vegetation_tree", "tree"):
            if name in oval.replace("--", "_"):
                family = {"lamp": "street_lamp", "bench": "bench",
                          "bollard": "bollard", "vending": "vending_machine",
                          "vegetation": "vegetation",
                          "vegetation_tree": "vegetation",
                          "tree": "vegetation"}[name]
                break
    permanence = "permanent" if (seed >> 3) % 4 else "transient"
    candidates = normalised["existing_asset_candidates"] or []
    asset = candidates[(seed >> 5) % len(candidates)] if candidates else "none"
    if family == "other" and cls == "road_marking":
        representation = "procedural_template"
    elif asset != "none" and (seed >> 6) % 3:
        representation = "existing_asset"
    elif (seed >> 7) % 8 == 0:
        representation = "observation_debt"
    else:
        representation = "new_asset_required"
    answers = {
        "semantic_family": {"answer": family, "confidence": default_conf},
        "permanence": {"answer": permanence, "confidence": spread(1)},
        "asset_choice": {"answer": asset, "confidence": spread(2)},
        "representation": {"answer": representation,
                           "confidence": spread(3)},
        "vegetation_detail": {
            "answer": {"template_family": "street_tree",
                       "size_class": ["small", "medium", "large"][
                           (seed >> 9) % 3]} if family == "vegetation"
            else None,
            "confidence": spread(4)},
        "manual_review": {"answer": bool((seed >> 11) % 5 == 0),
                          "confidence": spread(5)},
    }
    return answers


class FixtureBackend:
    """Deterministic canned answers keyed by input hash. Used for all tests."""

    def __init__(self, fixtures_path: Path):
        self.answers = json.loads(fixtures_path.read_text(encoding="utf-8"))

    def ask(self, question_id: str, normalised: dict) -> dict:
        key = _key(normalised)
        try:
            entry = self.answers[key]
        except KeyError:
            raise JevError(
                "fixture key absent for %s; regenerate fixtures with "
                "--record-fixtures" % normalised.get("candidate_id")) \
                from None
        return entry[question_id]


# Live TypeSafe contract (FL-4809 JEV-LIVE-02): one POST carries
# {"model", "questions", "state"} and answers come back under the same ids.
# Every spec below is a complete judgment: the question id is never sent to
# the model, so instructions and criteria restate the full meaning. The
# normalised candidate travels as state; it is geometry-free by
# construction (normalise() copies exactly INPUT_KEYS).
_FAMILY_RUBRICS = {
    "utility_pole": "poles carrying power or telecom wires, transformers, or pole-mounted equipment",
    "street_lamp": "lamp posts and luminaires lighting the street or sidewalk",
    "sign_pole": "signs, signboards, and the poles that carry them",
    "bench": "benches and other fixed seating",
    "bollard": "bollards and short protective posts",
    "vending_machine": "vending machines and similar unattended sales boxes",
    "vegetation": "trees, shrubs, hedges, and other plants",
    "other": "a permanent street object fitting none of the named families",
    "unresolved": "the evidence cannot support any family assignment",
}


def _typed_questions(normalised: dict, detail: bool = False) -> dict:
    """Build the typed question map for one candidate's state."""
    questions = {
        "semantic_family": {
            "type": "choice",
            "instructions": "Classify the observed street object into exactly "
                            "one semantic family. Use its semantic class, "
                            "object value, frame count, OCR text, VLM "
                            "description, estimated height, host kind, and "
                            "listed existing-asset candidates.",
            "criteria": dict(_FAMILY_RUBRICS),
        },
        "permanence": {
            "type": "choice",
            "instructions": "Judge whether the observed object is permanent "
                            "street infrastructure, transient or movable, or "
                            "unresolvable from the evidence.",
            "criteria": {
                "permanent": "fixed infrastructure expected to persist",
                "transient": "movable, temporary, or passing objects",
                "unresolved": "the evidence cannot support a permanence call",
            },
        },
        "representation": {
            "type": "choice",
            "instructions": "Choose how reconstruction should represent this "
                            "object: reuse the matched existing asset, build "
                            "it from a procedural template, author a new "
                            "asset, or park it as observation debt for more "
                            "evidence.",
            "criteria": {
                "existing_asset": "the matched existing asset covers it",
                "procedural_template": "a procedural template covers it",
                "new_asset_required": "it needs a newly authored asset",
                "observation_debt": "evidence is insufficient; park it",
            },
        },
        "manual_review": {
            "type": "noul",
            "instructions": "Does this candidate need human review before "
                            "reconstruction proceeds?",
            "criteria": {
                "true": "uncertain, ambiguous, or risky: a person should check it",
                "false": "routine: reconstruction can proceed without a person",
            },
        },
    }
    candidates = normalised.get("existing_asset_candidates") or []
    if candidates:
        questions["asset_choice"] = {
            "type": "choice",
            "instructions": "Select the existing-asset candidate that "
                            "matches the observed object, or none when "
                            "nothing listed matches. Candidate identities "
                            "are in `existing_asset_candidates`.",
            "criteria": {str(a): "catalogued existing-asset candidate"
                         for a in candidates} | {
                "none": "no listed candidate matches the observed object",
            },
        }
    if detail:
        questions["vegetation_detail"] = {
            "type": "choice",
            "instructions": "What size class is this vegetation?",
            "criteria": {
                "small": "low shrubs, planters, or young or small trees",
                "medium": "typical street trees",
                "large": "large mature trees",
            },
        }
    return questions


def _map_answer(question_id: str, spec: dict, answer: dict) -> dict:
    """Project one live answer onto the {answer, confidence} row shape."""
    if spec["type"] == "choice":
        choice = answer["choice"]
        if question_id == "vegetation_detail":
            choice = {"template_family": "street_tree",
                      "size_class": choice}
        return {"answer": choice,
                "confidence": float(answer["confidence"]),
                "probabilities": {k: float(v)
                                  for k, v in
                                  answer["probabilities"].items()}}
    p = float(answer["noul"])
    return {"answer": bool(p >= 0.5),
            "confidence": abs(p - 0.5) * 2.0,
            "noul": p}


class TypesafeBackend:
    """Live TypeSafe Jev client. Resolve the configured key without logging it."""

    def __init__(self, max_retries: int = 3):
        key = os.environ.get(TYPESAFE_ENV_VAR)
        if not key:
            key_path = Path.home() / ".config" / "typesafe" / "key"
            if key_path.is_file():
                key = key_path.read_text(encoding="utf-8").strip()
        if not key:
            raise JevError(
                "TYPESAFE_API_KEY_ABSENT: live client needs "
                "TYPESAFE_API_KEY or ~/.config/typesafe/key; use --backend "
                "fixture when neither is configured. No request was sent.")
        self._api_key = key
        self.max_retries = max_retries
        self.stats = {"requests": 0, "retries": 0, "failures": 0,
                      "input_tokens": 0, "output_tokens": 0}

    def _post(self, questions: dict, state: dict) -> dict:
        # The key travels only in the Authorization header. It is never
        # printed, logged, or written anywhere on this path.
        body = json.dumps({"model": TYPESAFE_MODEL,
                           "questions": questions,
                           "state": state}).encode()
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                TYPESAFE_ENDPOINT, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self._api_key},
                method="POST")
            try:
                with urllib.request.urlopen(request,
                                           timeout=60) as response:
                    doc = json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                last = exc
                if 500 <= exc.code < 600 and attempt < self.max_retries:
                    self.stats["retries"] += 1
                    time.sleep(2.0 * (attempt + 1))
                    continue
                self.stats["failures"] += 1
                raise JevError("typesafe service HTTP %s on attempt %d" % (
                    exc.code, attempt + 1)) from exc
            except (urllib.error.URLError, TimeoutError,
                    ConnectionError, OSError) as exc:
                last = exc
                if attempt < self.max_retries:
                    self.stats["retries"] += 1
                    time.sleep(2.0 * (attempt + 1))
                    continue
                self.stats["failures"] += 1
                raise JevError("typesafe transport failed after %d attempts: "
                               "%s" % (attempt + 1, type(exc).__name__)) \
                    from exc
            self.stats["requests"] += 1
            usage = doc.get("usage") or {}
            self.stats["input_tokens"] += int(usage.get("input_tokens") or 0)
            self.stats["output_tokens"] += int(usage.get("output_tokens") or 0)
            if not isinstance(doc.get("answers"), dict):
                self.stats["failures"] += 1
                raise JevError("typesafe response without answers map")
            return doc
        self.stats["failures"] += 1
        raise JevError("typesafe transport failed after %d attempts" % (
            self.max_retries + 1)) from last

    def ask_batch(self, question_ids: list, normalised: dict) -> dict:
        """Ask independent questions over one state in a single request."""
        specs = _typed_questions(
            normalised, detail=("vegetation_detail" in question_ids))
        doc = self._post(specs, normalised)
        got = doc["answers"]
        out = {}
        for qid, spec in specs.items():
            if qid not in question_ids:
                continue
            if qid not in got:
                self.stats["failures"] += 1
                raise JevError("typesafe answer absent for %s" % qid)
            try:
                out[qid] = _map_answer(qid, spec, got[qid])
            except (KeyError, TypeError, ValueError) as exc:
                self.stats["failures"] += 1
                raise JevError("typesafe answer malformed for %s" % qid) \
                    from exc
        return out

    def ask(self, question_id: str, normalised: dict) -> dict:
        return self.ask_batch([question_id], normalised)[question_id]


def _thresholds(rules_path: Path) -> dict:
    try:
        block = json.loads(rules_path.read_text(
            encoding="utf-8"))["jev_thresholds"]
        return {"accept": float(block["accept_confidence"]["value"]),
                "floor": float(block["review_floor"]["value"])}
    except KeyError as exc:
        raise JevError(
            "jev_thresholds DEFAULT fact missing from rules (absent key "
            "%s); regenerate rules_resolved.json with "
            "compile_rules_resolved.py" % (exc,)) from exc


def _routine_policy(rules_path: Path) -> dict:
    """Load the code-owned routine-object law and catalog mappings."""
    try:
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        block = rules["jev_routine_resolution"]
        families = rules["street_template_families"]["families"]
        return {
            "catalog_match": dict(block["catalog_match"]),
            "observed_road_marking": dict(block["observed_road_marking"]),
            "observed_flat_surface": dict(block["observed_flat_surface"]),
            "unmatched": dict(block["unmatched"]),
            "catalog_rows": [
                (str(row["object_token"]), str(row["template_id"]))
                for row in families
            ],
        }
    except KeyError as exc:
        raise JevError(
            "JEV-03 routine-resolution fact missing from rules (absent key "
            "%s); regenerate rules_resolved.json" % (exc,)) from exc


def _enrich_from_policy(normalised: dict, policy: dict) -> dict:
    """Add only deterministic catalog candidates; preserve source facts."""
    enriched = dict(normalised)
    if enriched.get("existing_asset_candidates"):
        return enriched
    object_value = str(enriched.get("object_value") or "")
    matches = [(token, template_id)
               for token, template_id in policy["catalog_rows"]
               if token in object_value]
    if matches:
        longest = max(len(token) for token, _template_id in matches)
        candidates = sorted({template_id for token, template_id in matches
                             if len(token) == longest})
        enriched["existing_asset_candidates"] = candidates
    return enriched


def _routine_resolution(normalised: dict, policy: dict) -> dict:
    """Return one explicit law verdict for every candidate."""
    marking = policy["observed_road_marking"]
    object_value = str(normalised.get("object_value") or "")
    if normalised.get("semantic_class") == marking["semantic_class"] and \
            object_value.startswith(marking["object_value_prefix"]):
        return {
            "rule": "observed_road_marking",
            "law_makes_sense": True,
            "routing": marking["routing"],
            "representation": marking["representation"],
            "template_id": None,
            "reason": "measured marking token has a surface-decal consumer",
            "source": marking["source"],
        }
    surface = policy["observed_flat_surface"]
    if normalised.get("semantic_class") == surface["semantic_class"] and \
            object_value.startswith(surface["object_value_prefix"]):
        return {
            "rule": "observed_flat_surface",
            "law_makes_sense": True,
            "routing": surface["routing"],
            "representation": surface["representation"],
            "template_id": None,
            "reason": (
                "measured flat-construction token has a point surface-stamp "
                "consumer; it is not a freestanding asset"),
            "source": surface["source"],
        }
    candidates = normalised.get("existing_asset_candidates") or []
    if len(candidates) == 1:
        catalog = policy["catalog_match"]
        return {
            "rule": "catalog_match",
            "law_makes_sense": True,
            "routing": catalog["routing"],
            "representation": catalog["representation"],
            "template_id": str(candidates[0]),
            "reason": (
                "unique longest observed object token maps to one authored "
                "generic template"),
            "source": catalog["source"],
        }
    unmatched = policy["unmatched"]
    return {
        "rule": "unmatched",
        "law_makes_sense": True,
        "routing": unmatched["routing"],
        "representation": unmatched["representation"],
        "template_id": None,
        "reason": "no verified catalog or surface representation; keep model review",
        "source": unmatched["source"],
    }


def _routine_record(normalised: dict, policy: dict) -> dict | None:
    """Resolve code-owned routine cases without asking a model."""
    enriched = _enrich_from_policy(normalised, policy)
    resolution = _routine_resolution(enriched, policy)
    if resolution["routing"] == "model_decision":
        return None
    record = {
        "candidate_id": normalised["candidate_id"],
        "semantic_class": normalised["semantic_class"],
        "answers": {
            "asset_choice": resolution.get("template_id") or "none",
            "representation": resolution["representation"],
        },
        "confidences": {},
        "model_routing": "not_called",
        "routing": resolution["routing"],
        "routine_resolution": resolution,
        "policy": "jev_routine_resolution",
        "decision_authority": "deterministic_reconstruction_policy",
        "backend_called": False,
    }
    _assert_no_geometry(record)
    return record


def decide(normalised: dict, backend, thresholds: dict,
           routine_policy: dict | None = None) -> dict:
    """Ask the atomic questions, compose with thresholds, route or decide."""
    policy_normalised = (
        _enrich_from_policy(normalised, routine_policy)
        if routine_policy is not None else normalised)
    # Preserve the historical fixture key.  Live Jev receives the catalog
    # candidates because they are useful semantic context; fixture playback
    # must still address the immutable recorded question state.
    backend_normalised = (
        policy_normalised if hasattr(backend, "ask_batch") else normalised)
    answers = {}
    batch_ids = [q for q in QUESTION_IDS if q != "vegetation_detail"]
    if hasattr(backend, "ask_batch"):
        # One request carries every independent question over the same
        # state; the backend answers them in parallel.
        answers.update(backend.ask_batch(batch_ids, backend_normalised))
    else:
        for question_id in batch_ids:
            answers[question_id] = backend.ask(
                question_id, backend_normalised)
    if "asset_choice" not in answers:
        # No listed candidates: nothing to match, decided in code.
        answers["asset_choice"] = {"answer": "none", "confidence": 1.0,
                                   "probabilities": {"none": 1.0}}
    if answers["semantic_family"]["answer"] == "vegetation":
        answers["vegetation_detail"] = backend.ask("vegetation_detail",
                                                   backend_normalised)
    else:
        answers["vegetation_detail"] = {"answer": None, "confidence": 1.0}
    confs = [answers[q]["confidence"] for q in QUESTION_IDS
             if not (q == "vegetation_detail" and answers[q]["answer"]
                     is None)]
    weakest = min(confs)
    accept, floor = thresholds["accept"], thresholds["floor"]
    if answers["semantic_family"]["answer"] == "unresolved":
        routing = "more_evidence"
    elif answers["manual_review"]["answer"] is True:
        routing = "human_review"
    elif answers["representation"]["answer"] == "observation_debt":
        routing = "observation_debt"
    elif weakest < floor:
        routing = "observation_debt"
    elif weakest < accept:
        routing = "opus_review"
    else:
        routing = "decided"
    record = {
        "candidate_id": normalised["candidate_id"],
        "semantic_class": normalised["semantic_class"],
        "answers": {q: answers[q]["answer"] for q in QUESTION_IDS},
        "confidences": {q: answers[q]["confidence"] for q in QUESTION_IDS},
        "routing": routing,
        "thresholds": thresholds,
        "policy": "jev_thresholds",
    }
    probs = {q: answers[q]["probabilities"] for q in QUESTION_IDS
             if answers[q].get("probabilities") is not None}
    if probs:
        record["probabilities"] = probs
    if routine_policy is not None:
        resolution = _routine_resolution(policy_normalised, routine_policy)
        if resolution["routing"] != "model_decision":
            raise JevError(
                "routine candidate reached model decision path: %s" %
                normalised["candidate_id"])
        record["model_routing"] = record["routing"]
        record["routine_resolution"] = resolution
        record["policy"] = "jev_thresholds+jev_routine_resolution"
    _assert_no_geometry(record)
    return record


def record_fixtures(candidates_path: Path, out_path: Path) -> int:
    """Generate the committed fixture table (deterministic, no backend)."""
    doc = json.loads(candidates_path.read_text(encoding="utf-8"))
    table = {}
    for feature in doc["canonical_features"]:
        normed = normalise(feature)
        table[_key(normed)] = _canned(normed)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    return len(table)


def merge_reviews(decisions_path: Path, reviews_path: Path,
                  rules_path: Path, out_path: Path) -> dict:
    """Fold vlm_review observations into routing (deterministic).

    A reviewed candidate with every review confidence at or above the
    rules accept threshold becomes decided; anything weaker becomes
    human_review. Unreviewed rows keep their fixture routing untouched.
    """
    thresholds = _thresholds(rules_path)
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    changed = {"decided": 0, "human_review": 0}
    for row in decisions.get("rows", []):
        if row.get("backend_called") is False:
            continue
        review = (reviews.get("observations", {}) if isinstance(
            reviews, dict) else {}).get(row["candidate_id"])
        if review is None:
            continue
        confs = [float(v) for v in
                 (review.get("confidences") or {}).values()]
        if confs and min(confs) >= thresholds["accept"]:
            row["routing"] = "decided"
            changed["decided"] += 1
        else:
            row["routing"] = "human_review"
            changed["human_review"] += 1
        row["review_source"] = "vlm_review"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(decisions, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    return {"changed": changed, "rows": len(decisions.get("rows", []))}

def run(candidates_path: Path, backend, rules_path: Path,
        out_path: Path, *, backend_kind: str) -> dict:
    """Decide every candidate; every row carries a named routing state."""
    thresholds = _thresholds(rules_path)
    routine_policy = _routine_policy(rules_path)
    doc = json.loads(candidates_path.read_text(encoding="utf-8"))
    rows = []
    routine_rows = 0
    model_rows = 0
    for feature in doc["canonical_features"]:
        normalised = normalise(feature)
        routine = _routine_record(normalised, routine_policy)
        if routine is not None:
            rows.append(routine)
            routine_rows += 1
            continue
        row = decide(normalised, backend, thresholds,
                     routine_policy=routine_policy)
        row["decision_authority"] = "jev_model"
        row["backend_called"] = True
        rows.append(row)
        model_rows += 1
    rows.sort(key=lambda r: r["candidate_id"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"schema": SCHEMA, "rows": rows,
         "inputs": {
             "candidates": str(candidates_path.resolve()),
             "candidates_sha256": _sha256(candidates_path),
             "rules": str(rules_path.resolve()),
             "rules_sha256": _sha256(rules_path),
         },
         "execution": {
             "backend": backend_kind,
             "routine_rows_without_backend": routine_rows,
             "model_rows": model_rows,
         },
         "counts": {"rows": len(rows), "routine_rows": routine_rows,
                    "model_rows": model_rows}}, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    return {"rows": len(rows), "routine_rows": routine_rows,
            "model_rows": model_rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path)
    ap.add_argument("--backend", default="fixture",
                    choices=("fixture", "typesafe"))
    ap.add_argument("--fixtures", type=Path)
    ap.add_argument("--rules", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--record-fixtures", action="store_true")
    ap.add_argument("--merge-reviews", action="store_true")
    ap.add_argument("--reviews", type=Path)
    ap.add_argument("--merged-out", type=Path)
    args = ap.parse_args(argv)
    if args.record_fixtures:
        if not args.candidates or not args.fixtures:
            ap.error("--record-fixtures needs --candidates and --fixtures")
        count = record_fixtures(args.candidates, args.fixtures)
        print("[jev] recorded %d fixture keys" % (count,))
        return 0
    if args.merge_reviews:
        for name in ("candidates", "reviews", "merged_out", "rules"):
            if getattr(args, name) is None:
                ap.error("--merge-reviews needs --candidates (decisions "
                         "file), --reviews, --merged-out and --rules")
        result = merge_reviews(args.candidates, args.reviews, args.rules,
                               args.merged_out)
        print("[jev] merged reviews -> %s %s" % (
            args.merged_out, result["changed"]))
        return 0
    required = ["candidates", "rules", "out"]
    if args.backend == "fixture":
        required.append("fixtures")
    for name in required:
        if getattr(args, name) is None:
            ap.error("decide run needs --%s" % name.replace("_", "-"))
    if args.backend == "fixture":
        backend = FixtureBackend(args.fixtures)
    else:
        backend = TypesafeBackend()
    counts = run(args.candidates, backend, args.rules, args.out,
                 backend_kind=args.backend)
    print("[jev] decided %d (routine=%d model=%d) -> %s" % (
        counts["rows"], counts["routine_rows"], counts["model_rows"],
        args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
