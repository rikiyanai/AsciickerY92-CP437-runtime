#!/usr/bin/env python3
"""Fail-closed registry gate for the complete Godot feature census.

The registry is intentionally data-driven.  It does not contain a built-in
feature list: the history-census owner supplies ``features`` and an
independent expected-census JSON list.  The registry records the list's path
in ``census.expected_census_path``.  Every expected evidence id must be
referenced by a feature row, so deleting a row without changing the external
census is an observable failure.  Keeping the expectation outside the
registry is deliberate: editing one registry object cannot silently delete
the evidence that it is meant to account for.

Registry shape (version 1)::

    {
      "version": 1,
      "census": {
        "provenance": {
          "source": "docs/...",
          "revision": "<commit or immutable source id>",
          "generated_at": "2026-08-28T00:00:00Z"
        },
        "expected_census_path": "docs/.../expected_evidence_ids.json"
      },
      "features": [{
        "id": "feature.example",
        "name": "descriptive name",
        "category": "rendering",
        "state": "active",
        "evidence_refs": ["evidence.example"],
        "source_owners": ["path/to/owner"],
        "observable": "one machine-observable behavior claim",
        "parent_family_id": "family.example",
        "behavior_class": "runtime",
        "check": {
          "kind": "command",
          "argv": ["python3", "scripts/check.py"],
          "cwd": ".",
          "timeout_seconds": 30,
          "max_output_bytes": 4096,
          "receipt_path": "artifacts/feature.example.json",
          "receipt_schema": "feature.runtime.v1",
          "route_id": "route.example",
          "target_identity": {
            "target": "target.example",
            "build": "build.example",
            "map": "map.example",
            "scene": "scene.example",
            "pose": "pose.example",
            "camera": "camera.example",
            "mode": "mode.example",
            "baseline": "baseline.example",
            "timing_window": "window.example"
          },
          "required_observation_keys": ["observation"]
        }
      }]
    }

The external expected-census file is a versioned object below.  The old plain
array form is intentionally rejected because it cannot prove history coverage
or own retirement approvals::

    {
      "version": 1,
      "feature_ids": ["feature.example"],
      "evidence_ids": ["evidence.example"],
      "operator_reference_commit": "<immutable operator reference git object id>",
      "graph_baseline_commit": "<reachable rewritten graph baseline git object id>",
      "coverage_through_commit": "<full git object id>",
      "history_paths": [],
      "classified_commits": [{
        "commit": "<full git object id>",
        "disposition": "feature-introducing",
        "feature_ids": ["feature.example"],
        "reason": "the commit changed the fixture feature",
        "evidence_refs": ["evidence.example"],
        "process_failure_category": null,
        "review_state": "resolved"
      }],
      "retirement_approvals": [{
        "ref": "approval.example",
        "feature_id": "feature.example",
        "operator_approved": true,
        "fl_ref": "FL-1234",
        "record_marker": "OPERATOR_APPROVED_FEATURE_RETIREMENT feature=feature.example ref=approval.example"
      }]
    }

The external census must classify the graph baseline commit and every commit in
the full reachable graph returned by
``git rev-list graph_baseline_commit..coverage_through_commit``.  The
``operator_reference_commit`` is an immutable external identity that need not
be reachable after a history rewrite.  It must resolve to a commit whose tree,
subject, author identity and timestamp, and committer identity and timestamp
exactly match ``graph_baseline_commit``.  The parent relationship is
intentionally excluded because a rewritten graph baseline can have different
ancestry.  The complete raw commit-message body is compared by its SHA-256
digest, so matching subjects cannot hide a changed body.
``history_paths`` is retained as an explicit provenance field and must be an
empty list; a nonempty path filter is rejected because it can hide unrelated
or pathless commits.  Normal validation also inspects every descendant
commit from ``coverage_through_commit`` through the current HEAD.  Each
descendant must carry exactly one machine-readable impact, retirement, or
``Godot-Feature-Impact: none`` plus non-empty ``Godot-Feature-Reason:``
trailer.  ``--historical-audit`` or ``historical_audit=True`` validates only
the anchored historical range.  The object also gives retired
rows an independent approval owner.  A retired
row is invalid unless its ``operator_approval_ref`` matches an approval record
for that row's feature id whose ``operator_approved`` value is true.  The path
is resolved relative to the repository root (or the ``base_dir`` supplied to
the Python API).

``behavior_class`` is one of ``static``, ``runtime``, ``temporal``, or
``interactive``.  Runtime, temporal, and interactive active rows require a
``command`` check.  ``static-contract`` is reserved for static rows and is a
small built-in path-plus-text assertion.  Retired rows require both a
non-empty ``operator_approval_ref`` and ``retirement_record``.  All objects
reject unknown keys.

Front door::

    python3 scripts/godot_feature_regression_gate.py --registry PATH --validate --json
    python3 scripts/godot_feature_regression_gate.py --registry PATH --run --json
    python3 scripts/godot_feature_regression_gate.py --registry PATH --fingerprint REV
    python3 scripts/godot_feature_regression_gate.py --registry PATH --fingerprint-diff BASE HEAD
    python3 scripts/godot_feature_regression_gate.py --registry PATH --staged-impact MSG_FILE

``--fingerprint*`` modes hash each feature's resolved ``source_owners`` (see
the "Per-feature source fingerprints" section).  ``--staged-impact`` is the
commit-msg enforcement: a staged change to a feature fingerprint must be named
in the ``Godot-Feature-Impact:`` trailer.

Exit status is zero only when the registry validates and, for ``--run``, every
active check passes.  Commands are passed as argv arrays with ``shell=False``;
no command string is interpolated by this tool.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from functools import lru_cache
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 4096
MAX_TIMEOUT_SECONDS = 900.0
MAX_OUTPUT_BYTES = 1_048_576

TOP_LEVEL_KEYS = frozenset({"version", "census", "features"})
CENSUS_KEYS = frozenset({"provenance", "expected_census_path"})
EXPECTED_V4_CORRECTION_COUNT = 2_445
V5_METADATA_KEYS = frozenset(
    {
        "v5_correction_count",
        "v5_resolved_count",
        "v5_unresolved_count",
        "remaining_semantic_reviews",
        "v5_outcome_reviewed_count",
        "v5_outcome_unlocated_count",
        "remaining_outcome_reviews",
    }
)
EXTERNAL_CENSUS_KEYS = frozenset(
    {
        "version",
        "feature_ids",
        "evidence_ids",
        "operator_reference_commit",
        "graph_baseline_commit",
        "coverage_through_commit",
        "history_paths",
        "classified_commits",
        "retirement_approvals",
        *V5_METADATA_KEYS,
    }
)
CLASSIFIED_COMMIT_KEYS = frozenset(
    {
        "commit",
        "disposition",
        "feature_ids",
        "reason",
        "evidence_refs",
        "process_failure_category",
        "review_state",
    }
)
RETIREMENT_APPROVAL_KEYS = frozenset(
    {"ref", "feature_id", "operator_approved", "fl_ref", "record_marker"}
)
PROVENANCE_KEYS = frozenset({"source", "revision", "generated_at"})
FEATURE_KEYS = frozenset(
    {
        "id",
        "name",
        "category",
        "state",
        "evidence_refs",
        "source_owners",
        "observable",
        "parent_family_id",
        "behavior_class",
        "check",
        "operator_approval_ref",
        "retirement_record",
        "true_ff",
    }
)
TRUE_FF_MAPPINGS = frozenset({"ADAPT", "FOLD", "NO_TRUE_FF_DEPICTION"})
TRUE_FF_STATES = frozenset({"REQUIRED"})
TRUE_FF_AUDIT_STATUSES = frozenset({
    "SOURCE_REVIEWED_VISUAL_UNVERIFIED", "SOURCE_REVIEWED_NONVISUAL",
    "SOURCE_UNRESOLVED",
})
TRUE_FF_AUDIT_PRIORITIES = frozenset({
    "INDISPENSABLE_READABILITY", "INDISPENSABLE_PLAYABILITY",
    "IMPORTANT_IDENTITY", "IMPORTANT_DEPTH_FEEDBACK", "OPTIONAL_ATMOSPHERE",
    "RENDERER_DEPENDENCY", "SUPPORT_TOOLING",
})
TRUE_FF_AUDIT_DISPOSITIONS = frozenset({
    "ADAPT", "PRESERVE_MINIMAL_VISUAL_CHANGE", "DROP_VISUAL_TREATMENT",
    "RETAIN_NONVISUAL_BEHAVIOR",
})
TRUE_FF_FEATURE_CLASSES = frozenset({
    "smooth_silhouette", "crease", "material_boundary", "relief", "shoreline",
    "authored_stroke", "authored_mark",
})
# FL-4917 operator correction 2026-09-30. This is a glyph-form decision,
# not selection of a whole candidate or runtime acceptance.
TRUE_FF_CREST_FORM = "≈≈"
TRUE_FF_WATER_DIRECTIONS = frozenset({"N", "NE", "E", "SE", "S", "SW", "W", "NW"})


@lru_cache(maxsize=1)
def _true_ff_run_bindings() -> frozenset[tuple[str, str, str]]:
    path = REPO_ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"
    document = load_registry(path)
    bindings = {(r["role"], r["language"], r["track"])
                for r in document["run_table"]["rows"]}
    # Authored marks/whole subjects are already compiler-validated against the
    # same mode-qualified vocabulary. They are not generic contour tracks or
    # proof that the feature renderer consumes these parts. Name their actual
    # owned tables rather than falsely binding a star to a straight-line run.
    sky = document.get("sky_authoring", {})
    if sky.get("star_classes"):
        bindings.add(("star", "soft", "authored.sky.star_classes"))
    cloud = sky.get("cloud_perimeter", {})
    if cloud.get("classes"):
        bindings.add((cloud["role"], "soft", "authored.sky.cloud_perimeter"))
    sun = sky.get("sun", {})
    if sun.get("parts"):
        bindings.add((sun["role"], sun["language"], "authored.sky.sun"))
    rain = sky.get("rain", {})
    for event, table in rain.get("events", {}).items():
        if table.get("parts"):
            bindings.add((rain["role"], rain["language"], f"authored.sky.rain.{event}"))
    for subject in document.get("subject_keyframes", []):
        if subject.get("parts"):
            bindings.add((subject["subject"], subject["language"],
                          f"authored.subject.{subject['subject']}"))
    return frozenset(bindings)


def _true_ff_complete_crest_pairs(value: str) -> bool:
    """Keep whole operator-chosen pairs; an odd run leaves a half crest."""
    return (TRUE_FF_CREST_FORM in value and "～" not in value
            and all(len(run) % 2 == 0 for run in re.findall(r"≈+", value)))


def _true_ff_named_gd_scopes(lines: list[str]) -> frozenset[int]:
    """Reject explicit FF methods, branches, calls and field mutations.

    This lexical boundary is not semantic entailment. Shared fact producers
    and non-FF branches still need review; a generic method name proves neither.
    """
    owned, current, branches = set(), None, []
    for index, line in enumerate(lines, 1):
        declaration = re.match(r"^(?:static\s+)?func\s+(\w+)\s*\(", line)
        if declaration:
            current = declaration.group(1)
            branches = []
        elif line.strip() and not line.startswith((" ", "\t", "#")):
            current = None
            branches = []
        if current and re.search(r"(?:^_?ff_|_ff_|true_?ff)", current, re.I):
            owned.add(index)
        # Names in ordinary literals/comments are not presentation ownership.
        # This is a bounded lexical check, not whole-program flow analysis.
        code = re.sub(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', "", line,
                      flags=re.X).split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip())
        branches = [depth for depth in branches if indent > depth]
        if re.match(r"\s*(?:if|elif|while)\b", code) and re.search(
                r"\b(?:_ff_\w*|true_ff\w*|TrueFF\w*|FFHudText)\b", code):
            branches.append(indent)
            owned.add(index)
        ff_mutation = re.search(
            r"\b_ff_\w+\s*(?:\(|\.|\[|(?:[+\-*/%&|^]?=(?!=)))", code)
        ff_call = re.search(r"\b\w*_ff_\w+\s*\(", code, re.I)
        if branches or ff_mutation or ff_call or re.search(r"\b(?:FFHudText|TrueFF\w*)\s*\.", code):
            owned.add(index)
    return frozenset(owned)


def _true_ff_scenery_alphanumeric(value: str) -> bool:
    # Compatibility spellings cannot evade D7: fullwidth Latin, circled digits
    # and mathematical Latin letters still read as alphanumerics. CJK contour
    # marks such as the explicitly admitted 人 are not an ASCII alphabet.
    return re.search(r"[A-Za-z0-9]", unicodedata.normalize("NFKC", value)) is not None


@lru_cache(maxsize=1)
def _true_ff_forbidden_run_bindings() -> frozenset[tuple[str, str, str]]:
    language = load_registry(REPO_ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json")
    # Names alone cannot bless a track whose active depiction violates D7.
    # The TRAIN groups are an inventory, not blanket scenery approval.
    return frozenset((row["role"], row["language"], row["track"])
                     for row in language["run_table"]["rows"]
                     if _true_ff_scenery_alphanumeric(row["sequence"] + row["substitute"]))


@lru_cache(maxsize=1)
def _true_ff_scenery_character_inventory() -> frozenset[str]:
    directory = REPO_ROOT / "godot_project/assets/glyphs/true_ff"
    language = load_registry(directory / "outline_language.v1.json")
    catalog = load_registry(directory / "saitamaar/catalog.json")
    vocabulary = {char for group in language["groups"]
                  for sequence in group["sequences"] for char in sequence if not char.isspace()}
    native = {glyph["char"] for glyph in catalog["glyphs"]}
    # Necessary inventory check only: a character occurring in a combination
    # does NOT admit it alone, in another mode, or in an unreviewed joint.
    # Mode-qualified runs and actual native connectivity still need review.
    return frozenset(vocabulary & native)


@lru_cache(maxsize=1)
def _true_ff_card_sources() -> tuple[dict, dict]:
    directory = REPO_ROOT / "godot_project/assets/glyphs/true_ff"
    return (load_registry(directory / "outline_language.v1.json"),
            load_registry(directory / "saitamaar/catalog.json"))


@lru_cache(maxsize=8)
def _true_ff_former_language(revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("former binding must pin a full commit")
    result = subprocess.run(
        ["git", "show", "--no-ext-diff",
         f"{revision}:godot_project/assets/glyphs/true_ff/outline_language.v1.json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def _true_ff_candidate_binding_errors(candidate: dict, context: str, selected: object,
                                      texts: list, comparison_basis: object = None,
                                      sequence_owner: str | None = None) -> list[str]:
    """Check explicit whole sequences, never admit components of a compound.

    Exact token segmentation is necessary, not a native-ink junction verdict.
    Rejected historical bindings remain forensic only; they cannot be active.
    """
    errors, sequences = [], set()
    language, _ = _true_ff_card_sources()
    admitted = {(group["mode"], sequence) for group in language["groups"]
                for sequence in group["sequences"]}
    bindings = candidate.get("sequence_bindings")
    if not isinstance(bindings, list) or not bindings:
        return [f"{context}.sequence_bindings: explicit mode-qualified whole sequences required"]
    for index, binding in enumerate(bindings):
        prefix = f"{context}.sequence_bindings[{index}]"
        if not isinstance(binding, dict):
            errors.append(f"{prefix}: whole-sequence binding must be an object")
            continue
        sequence, mode = binding.get("sequence"), binding.get("mode")
        if (not _nonempty_string(sequence) or any(c.isspace() for c in sequence)
                or not isinstance(mode, str) or (mode, sequence) not in admitted):
            errors.append(f"{prefix}: sequence/mode is not explicitly admitted in the compiled language")
            continue
        table = binding.get("table", binding.get("former_table"))
        if sequence_owner is not None and table != sequence_owner:
            errors.append(f"{prefix}: binding must retain its required authored owner {sequence_owner}")
            continue
        source = language
        if "former_table" in binding:
            if ("table" in binding or binding.get("active") is not False
                    or candidate.get("id") == selected):
                errors.append(f"{prefix}: former binding is an inactive rejected record, never active art")
                continue
            try:
                source = _true_ff_former_language(str(binding.get("source_revision", "")))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{prefix}: unreadable pinned former binding: {exc}")
                continue
            if binding["source_revision"] != comparison_basis:
                errors.append(f"{prefix}: former binding must match the card's pinned comparison basis")
                continue
        elif binding.get("active") is False and candidate.get("id") == selected:
            errors.append(f"{prefix}: selected candidate cannot use an inactive sequence")
            continue
        # FL-4917: an optional named run is an ownership claim, not decoration.
        # TRAIN admission of a sequence alone cannot prove that a named role's
        # track selects it. Planned replacements need authored/compiler work,
        # not a false binding to an existing run with different ink.
        run_keys = ("role", "language", "track")
        if any(key in binding for key in run_keys):
            if (not all(_nonempty_string(binding.get(key)) for key in run_keys)
                    or not any(all(row.get(key) == binding[key] for key in run_keys)
                               and row.get("mode") == mode and row.get("sequence") == sequence
                               for row in source["run_table"]["rows"])):
                errors.append(f"{prefix}: named role/language/track does not bind this exact sequence/mode")
                continue
        if table is not None:
            # Extend with another explicit owner when authored cards need it;
            # arbitrary non-empty table names must not bypass the binding check.
            if table != "sky_authoring.star_classes":
                errors.append(f"{prefix}: unknown authored sequence table")
                continue
            star_class = binding.get("class")
            matching = [entry for entry in source["sky_authoring"]["star_classes"]
                        if entry["class"] == star_class and not isinstance(star_class, bool)]
            if (len(matching) != 1 or matching[0]["mode"] != mode
                    or [sequence] not in matching[0]["idioms"]):
                errors.append(f"{prefix}: sequence does not belong to the named class/mode")
                continue
        sequences.add(sequence)
    for text_index, text in enumerate(texts):
        if not isinstance(text, str):
            continue
        for token in re.split(r"[ \u3000\n]+", text):
            reachable = {0}
            for offset in range(len(token)):
                if offset in reachable:
                    reachable.update(offset + len(sequence) for sequence in sequences
                                     if token.startswith(sequence, offset))
            if len(token) not in reachable:
                errors.append(f"{context}.text[{text_index}]: whole token {token!r} has no explicit admitted sequence segmentation")
            # Retain explicitly authored contour idioms as units. This is not
            # a demand to turn every TRAIN substring into an active art rule.
            compounds = {row["sequence"] for row in language["run_table"]["rows"]
                         if len(row["sequence"]) > 1}
            compounds.update(sequence for sequence in language["sky_authoring"]["cloud_perimeter"]["token"]
                             if len(sequence) > 1)
            for sequence in sorted(compounds):
                if sequence in token and sequence not in sequences:
                    errors.append(f"{context}.text[{text_index}]: authored compound {sequence!r} must retain its whole-sequence binding")
    return errors


def _true_ff_native_card_errors(metrics: object, texts: list, context: str,
                                demo: object) -> list[str]:
    """Compare card caches to independent catalog facts, not their own claims."""
    if not isinstance(metrics, dict):
        return []  # Header check owns this error.
    _, catalog = _true_ff_card_sources()
    advances = {glyph["char"]: glyph["advance_px"] for glyph in catalog["glyphs"]}
    errors = []
    if (metrics.get("font_px") != catalog["font_px"]
            or metrics.get("row_pitch_px") != catalog["recommended_row_step_px"]):
        errors.append(f"{context}.native_metrics: font size and row pitch must match independent catalog")
    if (metrics.get("native_baseline_offset_px") != catalog["baseline_px"]
            or metrics.get("source_font_sha256") != catalog["source_font_sha256"]):
        errors.append(f"{context}.native_metrics: native baseline and source-font provenance must match independent catalog")
    cache = metrics.get("glyph_advances_px")
    used = {char for text in texts if isinstance(text, str) for char in text if char != "\n"}
    if (not isinstance(cache, dict) or any(char not in advances or char not in cache
            or isinstance(cache[char], bool) or cache[char] != advances[char] for char in used)):
        errors.append(f"{context}.native_metrics: every used glyph/space advance must match independent catalog")
    if isinstance(demo, str) and all(char in advances for char in demo if char != "\n"):
        origins = []
        for line in demo.splitlines():
            x, row = 0, []
            for char in line:
                if not char.isspace():
                    row.append(x)
                x += advances[char]
            origins.append(row)
        y = [index * catalog["recommended_row_step_px"] for index in range(len(origins))]
        expected = {"illustrative_row_origins_px": origins,
                    "illustrative_row_origins_y_px": y,
                    "illustrative_row_baselines_px": [origin + catalog["baseline_px"] for origin in y]}
        for key, value in expected.items():
            if key in metrics and metrics[key] != value:
                errors.append(f"{context}.native_metrics.{key}: cached positions must derive from literal text and native catalog")
    return errors


def _true_ff_art_text_errors(value: object, context: str) -> list[str]:
    """Check text hygiene only; this is not a target-font or art-quality proof."""
    if not _nonempty_string(value):
        return [f"{context}: required whole selectable-text candidate"]
    errors = []
    if any(char != "\n" and unicodedata.category(char) in {"Cc", "Cf", "Cs"}
           for char in value):
        errors.append(f"{context}: control/format/surrogate characters cannot masquerade as native text spacing")
    if any(char.isspace() and char not in {" ", "\u3000", "\n"} for char in value):
        errors.append(f"{context}: use actual U+0020/U+3000 spacing and line breaks only")
    if "\r" in value:
        errors.append(f"{context}: carriage returns are not native candidate text")
    if re.search(r"[\u00a0\u2000-\u200b\u202f\u205f\ufeff]", value):
        errors.append(f"{context}: unsupported invisible spacing is not native U+0020/U+3000 spacing")
    for index, line in enumerate(value.splitlines(), 1):
        if line.startswith(" ") or line.endswith((" ", "\u3000")):
            errors.append(f"{context}:{index}: illegal leading half-space or trailing whitespace")
        # The later SPACE_RULE_DOUBLE_HALF_MIDROW_20260928 decision supersedes
        # the skill's older single-half limit. Three halves still cannot spell
        # an admitted native gap; neither tabs nor leading halves are allowed.
        if "   " in line or "\t" in line or "\r" in line:
            errors.append(f"{context}:{index}: excessive half-spaces/tabs are not native spacing")
    if re.search(r"\[(?:empty|gap|bank|rock|pocket|placeholder)\b|\bTODO\b", value, re.I):
        errors.append(f"{context}: placeholder is not a whole art candidate")
    return errors


def _true_ff_water_card_errors(card: object, context: str) -> list[str]:
    """Check that native metrics and each directed whole form are explicit.

    These fields expose reviewable decisions, not a raster/connection proof.
    Saitamaar's source hmtx gives two 7px advances for the operator's pair.
    """
    if not isinstance(card, dict):
        return []  # The general card validator owns this error.
    errors = []
    metrics = card.get("native_metrics")
    expected = {"font": "Saitamaar", "font_px": 16, "row_pitch_px": 17,
                "crest_glyph_advance_px": 7, "crest_pair_advance_px": 14}
    if not isinstance(metrics, dict) or any(metrics.get(key) != value for key, value in expected.items()):
        errors.append(f"{context}.native_metrics: explicit Saitamaar 16px/17px rows and 7px + 7px crest advances required")
    candidates = card.get("candidates")
    if not isinstance(candidates, list):
        return errors
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        prefix = f"{context}.candidates[{index}]"
        forms = candidate.get("directed_forms")
        if not isinstance(forms, dict) or set(forms) != TRUE_FF_WATER_DIRECTIONS:
            errors.append(f"{prefix}.directed_forms: enumerate complete N/NE/E/SE/S/SW/W/NW whole forms")
            continue
        for direction, demo in forms.items():
            errors.extend(_true_ff_art_text_errors(demo, f"{prefix}.directed_forms.{direction}"))
            if isinstance(demo, str) and not _true_ff_complete_crest_pairs(demo):
                errors.append(f"{prefix}.directed_forms.{direction}: crest must remain a complete ≈≈ pair")
            if isinstance(demo, str) and _true_ff_scenery_alphanumeric(demo):
                errors.append(f"{prefix}.directed_forms.{direction}: D7 forbids scenery alphanumerics")
    return errors


def _true_ff_card_errors(card: object, context: str, state: object, *, scenery_only: bool = True,
                         sequence_owner: str | None = None) -> list[str]:
    if not isinstance(card, dict):
        return [f"{context}: required three-candidate authoring card"]
    errors = []
    metrics = card.get("native_metrics")
    if not isinstance(metrics, dict) or any(metrics.get(key) != value for key, value in
            {"font": "Saitamaar", "font_px": 16, "row_pitch_px": 17}.items()):
        errors.append(f"{context}.native_metrics: explicit Saitamaar 16px/17px text layout required")
    candidates = card.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 3:
        return [f"{context}.candidates: exactly three whole candidates required"]
    candidate_ids, demos = set(), set()
    # A structured single-time comparison cannot choose between different
    # event ages as if they were three depiction alternatives. Keyframes and
    # in-betweens belong inside each whole candidate's animation contract.
    comparison_stages = [candidate.get("single_time_world_clock_stage")
                         for candidate in candidates if isinstance(candidate, dict)]
    if any(stage is not None for stage in comparison_stages):
        if (len(comparison_stages) != 3
                or not all(isinstance(stage, dict) for stage in comparison_stages)
                or not all(_nonempty_string(stage.get("event_identity"))
                           and _nonempty_string(stage.get("stage"))
                           and isinstance(stage.get("age_seconds"), (int, float))
                           and not isinstance(stage.get("age_seconds"), bool)
                           and 0.0 <= stage["age_seconds"] < float("inf")
                           for stage in comparison_stages)
                or any(stage != comparison_stages[0]
                       for stage in comparison_stages[1:])):
            errors.append(f"{context}: single-time candidates must compare the same event facts, not different animation keyframes")
    for index, candidate in enumerate(candidates):
        prefix = f"{context}.candidates[{index}]"
        if not isinstance(candidate, dict):
            errors.append(f"{prefix}: candidate must be an object")
            continue
        candidate_id = candidate.get("id")
        if not _nonempty_string(candidate_id) or candidate_id in candidate_ids:
            errors.append(f"{prefix}.id: distinct non-empty candidate id required")
        elif isinstance(candidate_id, str):
            candidate_ids.add(candidate_id)
        for field in ("whole_candidate", "directed_mirrors", "negative_space_occlusion",
                      "anchored_animation_part_timing", "near_far_behavior"):
            if not _nonempty_string(candidate.get(field)):
                errors.append(f"{prefix}.{field}: required full card contract")
        for field in ("facts", "falsifiers", "source_archive_refs"):
            errors.extend(_string_list(candidate.get(field), f"{prefix}.{field}"))
        demo = candidate.get("original_text_demo")
        errors.extend(_true_ff_art_text_errors(demo, f"{prefix}.original_text_demo"))
        forms = candidate.get("directed_forms", {})
        texts = [demo] + (list(forms.values()) if isinstance(forms, dict) else [])
        try:
            errors.extend(_true_ff_native_card_errors(metrics, texts, prefix, demo))
            if scenery_only:
                errors.extend(_true_ff_candidate_binding_errors(candidate, prefix, card.get("selected"), texts,
                                                                card.get("comparison_basis_commit"), sequence_owner))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{prefix}: unreadable compiled sequences/native catalog: {exc}")
        if scenery_only and any(isinstance(text, str) and _true_ff_scenery_alphanumeric(text)
                                for text in texts):
            errors.append(f"{prefix}: D7 forbids scenery alphanumerics; semantic UI needs its owned native_text role")
        used = {char for text in texts if isinstance(text, str)
                for char in text if not char.isspace()}
        if scenery_only:
            try:
                missing_inventory = sorted(used - _true_ff_scenery_character_inventory())
                if missing_inventory:
                    errors.append(f"{prefix}: characters outside bounded scenery vocabulary/native catalog {missing_inventory!r}")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{prefix}: unreadable bounded scenery vocabulary/native catalog: {exc}")
        if isinstance(demo, str):
            if demo in demos:
                errors.append(f"{prefix}: duplicate whole candidate")
            demos.add(demo)
        citations = candidate.get("glyph_run_citations")
        if not isinstance(citations, dict) or not citations or not all(
            _nonempty_string(key) and _nonempty_string(ref) for key, ref in citations.items()
        ):
            errors.append(f"{prefix}.glyph_run_citations: every glyph decision needs citations")
        else:
            for ref in citations.values():
                if any(not 1 <= int(rule) <= 11 for rule in re.findall(r"\bD(\d+)\b", ref)):
                    errors.append(f"{prefix}.glyph_run_citations: FL-4359 has D1-D11, not a higher D rule")
                if not re.search(r"\bD\d+\b|§\s*\d|\bsection\s+\d", ref, re.I):
                    errors.append(f"{prefix}.glyph_run_citations: each decision must cite a D-rule or skill section")
            # A generic citation entry is not a decision for every character.
            # This checks coverage, not whether the cited rule supports the
            # choice; semantic and native-font connectivity review stay open.
            named = "".join(citations)
            missing = sorted(char for char in used if char not in named)
            if missing:
                errors.append(f"{prefix}.glyph_run_citations: uncited characters {missing!r}")
    selected = card.get("selected")
    if (
        not isinstance(selected, str) or selected not in candidate_ids
    ):
        errors.append(f"{context}.selected: must name the rule-selected authored candidate")
    errors.extend(_string_list(card.get("selection_rule_refs"), f"{context}.selection_rule_refs"))
    if card.get("active_candidates") != [selected]:
        errors.append(f"{context}.active_candidates: only the selected whole candidate may be active")
    rejected = card.get("rejected_candidates")
    inactive_ids = candidate_ids - {selected} if isinstance(selected, str) else candidate_ids
    if (not isinstance(rejected, list) or len(rejected) != 2
            or not all(isinstance(item, dict) and _nonempty_string(item.get("id"))
                       and _nonempty_string(item.get("reason")) for item in rejected)
            or {item.get("id") for item in rejected if isinstance(item, dict)} != inactive_ids):
        errors.append(f"{context}.rejected_candidates: both inactive losers need explicit rejection reasons")
    return errors


def _true_ff_required_errors(mapping: dict, context: str) -> list[str]:
    """Check the mandatory depiction specification, not implementation status.

    Nonblank citations and typed bindings are necessary, not a semantic/art
    verdict. A reviewer must still check that cited code establishes the meaning
    and that a fold's target actually carries it. No schema pass proves that.
    """
    required = mapping.get("required")
    if not isinstance(required, dict):
        return [f"{context}.required: derived depiction contract required"]
    errors = []
    allowed = {"classes", "runs", "aa", "tempo", "paint", "cost", "rule_refs",
               "omission_rule", "source_gap"}
    for key in sorted(set(required) - allowed):
        errors.append(f"{context}.required.{key}: unknown contract field")
    for field in ("aa", "paint", "cost"):
        if not _nonempty_string(required.get(field)):
            errors.append(f"{context}.required.{field}: explicit mechanism required")
    paint = str(required.get("paint", ""))
    if not (re.search(r"flat.*(?:glyph|FG/BG)", paint, re.I)
            and re.search(r"no shading", paint, re.I)):
        errors.append(f"{context}.required.paint: flat per-glyph FG/BG with no shading is mandatory")
    cost = str(required.get("cost", ""))
    if not (re.search(r"(?:count-sized|0 FF glyph)", cost, re.I)
            and re.search(r"no (?:\w+ )?screen(?:-sized)?(?: \w+)? pass", cost, re.I)):
        errors.append(f"{context}.required.cost: count-sized work with no independent screen pass is mandatory")
    errors.extend(_string_list(required.get("rule_refs"), f"{context}.required.rule_refs"))
    cited_rules = str(required.get("rule_refs", []))
    for rule in (4, 5, 7, 8, 9):
        if not re.search(rf"\bD{rule}\b", cited_rules):
            errors.append(f"{context}.required.rule_refs: cite D{rule} explicitly")
    if not re.search(r"\bD6\b", str(required.get("aa", ""))):
        errors.append(f"{context}.required.aa: cite D6 for the specific empty-space edge mechanism")
    tempo = required.get("tempo")
    if not isinstance(tempo, dict) or not _nonempty_string(tempo.get("behavior")):
        errors.append(f"{context}.required.tempo: world-clock/event law required")
    elif not re.search(r"no (?:renderer )?holds?", tempo["behavior"], re.I):
        errors.append(f"{context}.required.tempo: no-holds contract required")
    if isinstance(tempo, dict):
        for key in sorted(set(tempo) - {"behavior", "numeric_source"}):
            errors.append(f"{context}.required.tempo.{key}: unknown clock field")
    classes, runs = required.get("classes"), required.get("runs")
    if not isinstance(classes, list) or any(not isinstance(c, str) or c not in TRUE_FF_FEATURE_CLASSES for c in classes):
        errors.append(f"{context}.required.classes: use the owned feature-bank classes")
    if not isinstance(runs, list):
        errors.append(f"{context}.required.runs: explicit role/run bindings required")
        runs = []
    omission = mapping.get("mapping") == "NO_TRUE_FF_DEPICTION"
    if omission:
        if not _nonempty_string(required.get("omission_rule")):
            errors.append(f"{context}.required.omission_rule: a forcing rule, not a proposed omission, is required")
        if classes or runs:
            errors.append(f"{context}.required: no-depiction rows cannot secretly bind a drawing class/run")
    else:
        if not classes or not runs:
            errors.append(f"{context}.required: depicted meaning needs both feature class and role/run")
        if _nonempty_string(required.get("omission_rule")):
            errors.append(f"{context}.required.omission_rule: only a rule-forced no-depiction row may omit")
    for index, run in enumerate(runs):
        prefix = f"{context}.required.runs[{index}]"
        if not isinstance(run, dict) or not _nonempty_string(run.get("role")):
            errors.append(f"{prefix}: named owned role required")
            continue
        for key in sorted(set(run) - {"role", "language", "tracks"}):
            errors.append(f"{prefix}.{key}: unknown role binding field")
        language = run.get("language")
        if language == "native_text":
            if run.get("role") != "text" or run.get("tracks") != ["native_character_sequence"]:
                errors.append(f"{prefix}: native text must bind text/native_character_sequence, not geometric tracks")
        elif not isinstance(language, str) or language not in {"hard", "plant", "soft", "water"}:
            errors.append(f"{prefix}.language: unknown outline language")
        else:
            errors.extend(_string_list(run.get("tracks"), f"{prefix}.tracks"))
            if isinstance(run.get("tracks"), list):
                try:
                    bindings = _true_ff_run_bindings()
                    forbidden_bindings = _true_ff_forbidden_run_bindings()
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f"{prefix}: unreadable owned outline language: {exc}")
                    continue
                for track in run["tracks"]:
                    if not isinstance(track, str) or (run["role"], language, track) not in bindings:
                        errors.append(f"{prefix}: no compiled role/language/track binding for {track!r}")
                    elif (run["role"], language, track) in forbidden_bindings:
                        errors.append(f"{prefix}: D7 rejects active compiled alphanumeric sequence for {track!r}; author a complete replacement, do not silently omit the structure")
    if _nonempty_string(required.get("source_gap")):
        errors.append(f"{context}.required.source_gap: unresolved source meaning cannot pass by becoming REQUIRED")
    return errors


def _true_ff_manual_audit_errors(mapping: dict, context: str, ids: set[str]) -> list[str]:
    """Validate authored decisions when present; never fabricate unaudited rows.

    The legacy mapping labels are not manual-review completion. Missing audits
    remain missing, and this schema check does not approve art or runtime work.
    """
    if "manual_audit" not in mapping:
        return []
    audit = mapping["manual_audit"]
    context = f"{context}.manual_audit"
    if not isinstance(audit, dict):
        return [f"{context}: expected an individual decision object"]
    errors: list[str] = []
    for field, choices in (("status", TRUE_FF_AUDIT_STATUSES),
                           ("priority", TRUE_FF_AUDIT_PRIORITIES),
                           ("disposition", TRUE_FF_AUDIT_DISPOSITIONS)):
        value = audit.get(field)
        if not isinstance(value, str) or value not in choices:
            errors.append(f"{context}.{field}: unsupported manual-review value")
    for field in ("reason", "preserve", "structure", "falsifier", "unverified"):
        if not _nonempty_string(audit.get(field)):
            errors.append(f"{context}.{field}: required individual evidence/decision")
    errors.extend(_string_list(audit.get("inspected"), f"{context}.inspected"))
    dependencies = audit.get("dependencies")
    errors.extend(_string_list(dependencies, f"{context}.dependencies", nonempty=False))
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if isinstance(dependency, str) and dependency not in ids:
                errors.append(f"{context}.dependencies: unknown feature {dependency}")
    if audit.get("status") == "SOURCE_REVIEWED_NONVISUAL" and audit.get("disposition") not in {
        "RETAIN_NONVISUAL_BEHAVIOR", "DROP_VISUAL_TREATMENT",
    }:
        errors.append(f"{context}: a visual disposition cannot be marked nonvisual")
    if audit.get("status") == "SOURCE_UNRESOLVED":
        errors.append(f"{context}.status: unresolved source decision is still an open defect")
    return errors


def validate_true_ff_mappings(
    features: object, *, base_dir: Path = REPO_ROOT
) -> list[str]:
    """FL-4917: validate required meaning mappings, never implement a port.

    This is a data check. It does not judge art, approve omissions,
    execute feature checks, or promote an implementation to visual acceptance.
    The existing census validator still owns the independent population check.
    """
    if not isinstance(features, list) or not features:
        return ["true_ff: features must be a non-empty list"]
    errors: list[str] = []
    ids = {row["id"] for row in features if isinstance(row, dict) and _nonempty_string(row.get("id"))}
    if len(ids) != len(features):
        errors.append("true_ff: feature IDs must be distinct non-empty strings")
    mappings_by_id = {row["id"]: row.get("true_ff") for row in features
                      if isinstance(row, dict) and _nonempty_string(row.get("id"))}
    folds: dict[str, list[str]] = {}
    line_counts: dict[Path | tuple[Path, str], int] = {}
    source_lines: dict[Path | tuple[Path, str], list[str]] = {}
    source_ff_scopes: dict[Path | tuple[Path, str], frozenset[int]] = {}
    for row in features:
        if not isinstance(row, dict):
            errors.append("true_ff: feature must be an object")
            continue
        feature_id = row.get("id")
        context = f"{feature_id}.true_ff"
        mapping = row.get("true_ff")
        if not isinstance(mapping, dict):
            errors.append(f"{context}: every feature needs an explicit mapping")
            continue
        errors.extend(_true_ff_manual_audit_errors(mapping, context, ids))
        for field in ("meaning", "depiction", "rationale"):
            if not _nonempty_string(mapping.get(field)):
                errors.append(f"{context}.{field}: required non-empty string")
        for field in ("world_facts",):
            errors.extend(_string_list(mapping.get(field), f"{context}.{field}"))
        errors.extend(_string_list(mapping.get("open_work"), f"{context}.open_work", nonempty=False))
        for field in ("meaning_refs", "archive_refs", "authoring_refs", "fold_into"):
            errors.extend(_string_list(mapping.get(field), f"{context}.{field}", nonempty=False))
        kind = mapping.get("mapping")
        if not isinstance(kind, str) or kind not in TRUE_FF_MAPPINGS:
            errors.append(f"{context}.mapping: use ADAPT, FOLD, or NO_TRUE_FF_DEPICTION")
        state = mapping.get("state")
        if not isinstance(state, str) or state not in TRUE_FF_STATES:
            errors.append(f"{context}.state: REQUIRED is the obligation; implementation/acceptance is separate")
        if _nonempty_string(mapping.get("source_gap")):
            errors.append(f"{context}.source_gap: unresolved meaning ownership is still an open defect")
        errors.extend(_true_ff_required_errors(mapping, context))
        if kind == "ADAPT":
            errors.extend(_string_list(mapping.get("authoring_refs"), f"{context}.authoring_refs"))
            errors.extend(_string_list(mapping.get("archive_refs"), f"{context}.archive_refs"))
            required = mapping.get("required")
            runs = required.get("runs", []) if isinstance(required, dict) else []
            # A text role does not exempt a mixed geometry/text card from D7
            # or whole-sequence admission. Semantic text must have its own typed
            # carrier; one role cannot exempt an undifferentiated scenery demo.
            native_ui = isinstance(runs, list) and bool(runs) and all(isinstance(run, dict)
                         and run.get("role") == "text" and run.get("language") == "native_text"
                         for run in runs)
            errors.extend(_true_ff_card_errors(mapping.get("authoring_card"),
                                              f"{context}.authoring_card", state,
                                              scenery_only=not native_ui,
                                              sequence_owner="sky_authoring.star_classes" if any(
                                                  isinstance(run, dict) and run.get("role") == "star"
                                                  and isinstance(run.get("tracks"), list)
                                                  and "authored.sky.star_classes" in run["tracks"]
                                                  for run in (runs if isinstance(runs, list) else [])) else None))
            if feature_id == "GODOT-W06":
                evidence = mapping.get("wave_evidence")
                decision = evidence.get("operator_wave_form_decision") if isinstance(evidence, dict) else None
                if not isinstance(decision, dict) or decision.get("form") != TRUE_FF_CREST_FORM or not all(
                    _nonempty_string(decision.get(key)) for key in ("quote", "ref", "date")
                ) or decision.get("codepoints") != ["U+2248", "U+2248"]:
                    errors.append(f"{context}.wave_evidence: preserve the cited operator correction selecting ≈≈")
            if isinstance(feature_id, str) and feature_id in {"GODOT-W06", "GODOT-W07"}:
                card = mapping.get("authoring_card")
                errors.extend(_true_ff_water_card_errors(card, f"{context}.authoring_card"))
                candidates = card.get("candidates") if isinstance(card, dict) else None
                if isinstance(candidates, list):
                    for index, candidate in enumerate(candidates):
                        demo = candidate.get("original_text_demo") if isinstance(candidate, dict) else None
                        if isinstance(demo, str) and not _true_ff_complete_crest_pairs(demo):
                            errors.append(f"{context}.authoring_card.candidates[{index}]: crest demos must use complete ≈≈ pairs, not ～ or a lone ≈")
        targets = mapping.get("fold_into")
        if isinstance(targets, list):
            if kind == "FOLD" and not targets:
                errors.append(f"{context}.fold_into: FOLD needs a named target row")
            if kind != "FOLD" and targets:
                errors.append(f"{context}.fold_into: only FOLD may name target rows")
            valid_targets = []
            for target in targets:
                if not isinstance(target, str) or target not in ids:
                    errors.append(f"{context}.fold_into: unknown target {target!r}")
                elif target == feature_id:
                    errors.append(f"{context}.fold_into: cannot fold into itself")
                else:
                    valid_targets.append(target)
                    target_mapping = mappings_by_id.get(target)
                    if not isinstance(target_mapping, dict) or target_mapping.get("mapping") != "ADAPT":
                        errors.append(f"{context}.fold_into: {target} has no direct ADAPT depiction to carry the meaning")
                    elif (not _nonempty_string(target_mapping.get("depiction"))
                          or target_mapping.get("state") != "REQUIRED"
                          or _nonempty_string(target_mapping.get("source_gap"))
                          or _true_ff_required_errors(target_mapping, f"{target}.true_ff")):
                        errors.append(f"{context}.fold_into: {target} has no evidenced mapped depiction to carry the meaning")
            if isinstance(feature_id, str):
                folds[feature_id] = valid_targets
        refs = mapping.get("meaning_refs")
        if refs == []:
            errors.append(f"{context}: absent non-FF meaning citations remain a source_gap, not a completed mapping")
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, str):
                    continue
                match = re.fullmatch(r"([^:]+):(\d+)(?:-(\d+))?(?: @ commit ([0-9a-f]{40}))?", ref)
                if not match:
                    errors.append(f"{context}.meaning_refs: expected source path:numeric-line, got {ref!r}")
                    continue
                relative_path = Path(match.group(1))
                path = base_dir / relative_path
                if (relative_path.is_absolute() or ".." in relative_path.parts
                        or not path.resolve().is_relative_to(base_dir.resolve())):
                    errors.append(f"{context}.meaning_refs: source must stay inside the repository: {ref}")
                    continue
                if re.search(r"(?:true_ff|TrueFF)", match.group(1), re.I):
                    errors.append(f"{context}.meaning_refs: True FF is not the non-FF meaning owner: {ref}")
                try:
                    commit = match.group(4)
                    cache_key = (path, commit) if commit else path
                    if cache_key not in source_lines:
                        if commit:
                            # Historical meaning evidence must pin immutable bytes.
                            # Reading a blob is not executing its feature command
                            # or claiming the deleted owner still runs today.
                            archived = subprocess.run(
                                ["git", "show", "--no-ext-diff", f"{commit}:{match.group(1)}"],
                                cwd=base_dir, capture_output=True, text=True,
                                encoding="utf-8", timeout=10, check=True,
                            )
                            source_lines[cache_key] = archived.stdout.splitlines()
                        else:
                            source_lines[cache_key] = path.read_text(encoding="utf-8").splitlines()
                    first = int(match.group(2))
                    last = int(match.group(3) or first)
                    if not 1 <= first <= last <= len(source_lines[cache_key]):
                        errors.append(f"{context}.meaning_refs: line outside source: {ref}")
                    elif not any(re.search(r"\w", line) for line in source_lines[cache_key][first - 1:last]):
                        # FL-4512: a valid path/range that cites only blank lines,
                        # braces or separators carries no meaning evidence. This
                        # catches that defect, not semantic entailment: a colour
                        # constant still cannot establish a transmission law by
                        # passing this check. The cited implementation needs review.
                        errors.append(f"{context}.meaning_refs: cited range has no substantive source content: {ref}")
                    elif relative_path.suffix.lower() == ".gd":
                        if cache_key not in source_ff_scopes:
                            source_ff_scopes[cache_key] = _true_ff_named_gd_scopes(source_lines[cache_key])
                        if not source_ff_scopes[cache_key].isdisjoint(range(first, last + 1)):
                            errors.append(f"{context}.meaning_refs: explicitly FF-owned method/presentation branch or call is not a non-FF meaning owner: {ref}")
                except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
                    errors.append(f"{context}.meaning_refs: unreadable source {ref}: {exc}")
        archive_groups = [(f"{context}.archive_refs", mapping.get("archive_refs"))]
        card = mapping.get("authoring_card")
        if isinstance(card, dict) and isinstance(card.get("candidates"), list):
            for index, candidate in enumerate(card["candidates"]):
                if isinstance(candidate, dict):
                    archive_groups.append((f"{context}.authoring_card.candidates[{index}].source_archive_refs",
                                           candidate.get("source_archive_refs")))
        for archive_context, archive_refs in archive_groups:
            if not isinstance(archive_refs, list):
                continue
            for ref in archive_refs:
                if not isinstance(ref, str):
                    continue
                location = re.sub(r"\s+\(.*\)$", "", ref)
                match = re.fullmatch(r"(.+?)(?::(\d+)(?:-(\d+))?)?", location)
                path = base_dir / match.group(1) if match else None
                if path is None or not path.is_file():
                    errors.append(f"{archive_context}: unreadable archive source {ref}")
                    continue
                archive_root = Path("/Users/r/Projects/ascii-art-archive").resolve()
                if not (path.resolve().is_relative_to(base_dir.resolve())
                        or path.resolve().is_relative_to(archive_root)):
                    errors.append(f"{archive_context}: source is outside the owned archive/repository: {ref}")
                    continue
                if match.group(2):
                    try:
                        if path not in source_lines:
                            source_lines[path] = path.read_text(encoding="utf-8").splitlines()
                        line_counts[path] = len(source_lines[path])
                        first = int(match.group(2))
                        last = int(match.group(3) or first)
                        if not 1 <= first <= last <= line_counts[path]:
                            errors.append(f"{archive_context}: line outside source: {ref}")
                        elif not any(line.strip() for line in source_lines[path][first - 1:last]):
                            errors.append(f"{archive_context}: cited range has no art/source content: {ref}")
                    except (OSError, UnicodeError) as exc:
                        errors.append(f"{archive_context}: unreadable text source {ref}: {exc}")
    # A fold must not hide a missing meaning in a circular dependency.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(feature_id: str) -> None:
        if feature_id in visiting:
            errors.append(f"{feature_id}.true_ff.fold_into: cyclic meaning mapping")
            return
        if feature_id in visited:
            return
        visiting.add(feature_id)
        for target in folds.get(feature_id, []):
            visit(target)
        visiting.remove(feature_id)
        visited.add(feature_id)

    for feature_id in folds:
        visit(feature_id)
    return errors
COMMAND_CHECK_KEYS = frozenset(
    {
        "kind",
        "argv",
        "cwd",
        "timeout_seconds",
        "max_output_bytes",
        "receipt_path",
        "receipt_schema",
        "route_id",
        "target_identity",
        "required_observation_keys",
    }
)
TARGET_IDENTITY_KEYS = frozenset(
    {
        "target",
        "build",
        "map",
        "scene",
        "pose",
        "camera",
        "mode",
        "baseline",
        "timing_window",
        "feature_state",
    }
)
STATIC_CHECK_KEYS = frozenset({"kind", "path", "contains"})
VALID_STATES = frozenset({"active", "retired"})
VALID_BEHAVIOR_CLASSES = frozenset(
    {"static", "runtime", "temporal", "interactive"}
)
VALID_CHECK_KINDS = frozenset({"command", "static-contract"})
EXECUTABLE_BEHAVIOR_CLASSES = frozenset({"runtime", "temporal", "interactive"})
TEMPORAL_REQUIRED_OBSERVATION_KEYS = frozenset({"motion", "coherence"})
# A receipt may carry one of these optional status fields in addition to the
# required ``behavior_observed`` boolean.  Unknown statuses are fail-closed:
# an executable route must not turn an unmeasured or skipped observation into
# a passing feature result by setting the boolean alone.
RECEIPT_STATUS_KEYS = frozenset(
    {
        "status",
        "result",
        "verdict",
        "measurement_status",
        "measurement_state",
        "observation_status",
        "verification_status",
        "result_status",
    }
)
RECEIPT_PASS_STATUSES = frozenset(
    {"pass", "passed", "ok", "success", "successful", "measured", "observed", "verified"}
)
RECEIPT_REJECT_STATUSES = frozenset(
    {
        "skip",
        "skipped",
        "unknown",
        "unmeasured",
        "not_measured",
        "not_observed",
        "inconclusive",
        "pending",
        "not_run",
        "not_available",
        "unavailable",
    }
)
# The presence analyzer is deliberately a narrow intake surface.  A registry
# command may not nominate it as the executable owner for a feature, even when
# a wrapper would otherwise turn its output into a receipt.
INTAKE_ONLY_ANALYZER_BASENAMES = frozenset({"analyze_feature_presence.py"})
VALID_COMMIT_DISPOSITIONS = frozenset(
    {
        "feature-introducing",
        "feature-modifying",
        "feature-restoring",
        "feature-removing",
        "retirement",
        "no-feature",
        "process-only",
        "process-failure",
    }
)
EMPTY_FEATURE_DISPOSITIONS = frozenset(
    {"no-feature", "process-only", "process-failure"}
)
# Process-only and process-failure records describe repository operation, not
# shipped behavior ownership. Keeping feature IDs on those records recreates
# the path-family leakage that this census is meant to reject.
REQUIRES_EMPTY_FEATURE_DISPOSITIONS = EMPTY_FEATURE_DISPOSITIONS
# Ordinary process-only commits still need an explicit reason, but they are not
# failures. Only an actual process-failure record requires a failure category.
PROCESS_FAILURE_DISPOSITIONS = frozenset({"process-failure"})
VALID_PROCESS_FAILURE_CATEGORIES = frozenset(
    {
        "process",
        "failure",
        "governance",
        "documentation",
        "infrastructure",
        "tooling",
    }
)
VALID_SEMANTIC_REVIEW_STATES = frozenset({"resolved", "unresolved"})


def _is_bool(value: object) -> bool:
    """Return true for bool values, which must not masquerade as integers."""

    return isinstance(value, bool)


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _unknown_keys(
    value: Mapping[str, Any], allowed: frozenset[str], context: str
) -> list[str]:
    unknown = sorted(set(value) - allowed)
    return [f"{context}: unknown key {key!r}" for key in unknown]


def _string_list(value: object, context: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list):
        return [f"{context}: expected a list of strings"]
    errors: list[str] = []
    if nonempty and not value:
        errors.append(f"{context}: must not be empty")
    for index, item in enumerate(value):
        if not _nonempty_string(item):
            errors.append(f"{context}[{index}]: must be a non-empty string")
    string_items = [item for item in value if isinstance(item, str)]
    if len(string_items) == len(value) and len(set(string_items)) != len(value):
        errors.append(f"{context}: duplicate values are not allowed")
    return errors


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _executable_path(token: str, base_dir: Path) -> Path | None:
    """Resolve an argv[0] executable without invoking a shell."""

    if "/" in token or os.sep in token:
        candidate = _resolve_path(token, base_dir)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        return None
    found = shutil.which(token)
    return Path(found).resolve() if found else None


def _validate_command(
    check: Mapping[str, Any],
    context: str,
    base_dir: Path,
    behavior_class: str,
    state: str,
) -> list[str]:
    errors = _unknown_keys(check, COMMAND_CHECK_KEYS, context)
    if check.get("kind") != "command":
        errors.append(f"{context}.kind: expected 'command'")

    argv = check.get("argv")
    if not isinstance(argv, list) or not argv:
        errors.append(f"{context}.argv: must be a non-empty argv list")
    elif any(not isinstance(item, str) or "\x00" in item for item in argv):
        errors.append(f"{context}.argv: every argument must be a NUL-free string")
    else:
        executable = _executable_path(argv[0], base_dir)
        if executable is None:
            errors.append(f"{context}.argv[0]: executable not found or not executable: {argv[0]!r}")
        intake_only_tokens = [
            token
            for token in argv[1:]
            if Path(token).name in INTAKE_ONLY_ANALYZER_BASENAMES
        ]
        if intake_only_tokens:
            errors.append(
                f"{context}.argv: intake-only analyzer cannot own executable behavior: "
                f"{intake_only_tokens[0]!r}"
            )

    cwd = check.get("cwd", ".")
    if not _nonempty_string(cwd):
        errors.append(f"{context}.cwd: must be a non-empty string when provided")
    else:
        cwd_path = _resolve_path(cwd, base_dir)
        if not cwd_path.is_dir():
            errors.append(f"{context}.cwd: directory does not exist: {cwd!r}")

    timeout = check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if (
        _is_bool(timeout)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or float(timeout) <= 0
        or float(timeout) > MAX_TIMEOUT_SECONDS
    ):
        errors.append(
            f"{context}.timeout_seconds: must be a finite number in (0, {MAX_TIMEOUT_SECONDS:g}]"
        )

    max_output = check.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)
    if (
        _is_bool(max_output)
        or not isinstance(max_output, int)
        or max_output <= 0
        or max_output > MAX_OUTPUT_BYTES
    ):
        errors.append(
            f"{context}.max_output_bytes: must be an integer in (0, {MAX_OUTPUT_BYTES}]"
        )
    if state == "active" and behavior_class in EXECUTABLE_BEHAVIOR_CLASSES:
        receipt_path = check.get("receipt_path")
        if not _nonempty_string(receipt_path):
            errors.append(f"{context}.receipt_path: required for executable behavior")
        else:
            receipt_parent = _resolve_path(receipt_path, base_dir).parent
            if not receipt_parent.is_dir():
                errors.append(f"{context}.receipt_path: parent directory does not exist: {receipt_path!r}")
        for field_name in ("receipt_schema", "route_id"):
            if not _nonempty_string(check.get(field_name)):
                errors.append(f"{context}.{field_name}: required for executable behavior")
        target_identity = check.get("target_identity")
        if not isinstance(target_identity, dict):
            errors.append(
                f"{context}.target_identity: required object for executable behavior"
            )
        else:
            errors.extend(_validate_target_identity(target_identity, context))
        observation_keys = check.get("required_observation_keys")
        errors.extend(
            _string_list(
                observation_keys,
                f"{context}.required_observation_keys",
                nonempty=True,
            )
        )
        if behavior_class == "temporal" and isinstance(observation_keys, list):
            missing_temporal_keys = sorted(
                TEMPORAL_REQUIRED_OBSERVATION_KEYS - set(observation_keys)
            )
            if missing_temporal_keys:
                errors.append(
                    f"{context}.required_observation_keys: temporal behavior requires "
                    f"motion and coherence observations; missing {missing_temporal_keys}"
                )
    return errors


def _validate_static_contract(
    check: Mapping[str, Any], context: str, base_dir: Path
) -> list[str]:
    errors = _unknown_keys(check, STATIC_CHECK_KEYS, context)
    if check.get("kind") != "static-contract":
        errors.append(f"{context}.kind: expected 'static-contract'")
    path = check.get("path")
    if not _nonempty_string(path):
        errors.append(f"{context}.path: must be a non-empty string")
    elif not _resolve_path(path, base_dir).is_file():
        errors.append(f"{context}.path: file does not exist: {path!r}")
    contains = check.get("contains")
    if not _nonempty_string(contains):
        errors.append(f"{context}.contains: must be a non-empty NUL-free string")
    return errors


def _validate_provenance(provenance: object, context: str) -> list[str]:
    if not isinstance(provenance, dict):
        return [f"{context}: must be an object"]
    errors = _unknown_keys(provenance, PROVENANCE_KEYS, context)
    for key in sorted(PROVENANCE_KEYS):
        if not _nonempty_string(provenance.get(key)):
            errors.append(f"{context}.{key}: must be a non-empty string")
    return errors


def _validate_target_identity(
    target_identity: Mapping[str, Any], context: str
) -> list[str]:
    errors = _unknown_keys(target_identity, TARGET_IDENTITY_KEYS, f"{context}.target_identity")
    required = (
        "target",
        "build",
        "map",
        "scene",
        "pose",
        "camera",
        "mode",
        "baseline",
        "timing_window",
    )
    for field_name in required:
        if not _nonempty_string(target_identity.get(field_name)):
            errors.append(
                f"{context}.target_identity.{field_name}: must be a non-empty string"
            )
    if "feature_state" in target_identity and not _nonempty_string(
        target_identity.get("feature_state")
    ):
        errors.append(
            f"{context}.target_identity.feature_state: must be a non-empty string when provided"
        )
    return errors


@dataclass
class ExternalCensus:
    feature_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    approval_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    operator_reference_commit: str | None = None
    graph_baseline_commit: str | None = None
    coverage_through_commit: str | None = None
    history_paths: list[str] = field(default_factory=list)
    history_paths_declared: bool = False
    classified_commits: list[dict[str, Any]] = field(default_factory=list)
    v5_correction_count: int | None = None
    v5_resolved_count: int | None = None
    v5_unresolved_count: int | None = None
    remaining_semantic_reviews: int | None = None
    v5_outcome_reviewed_count: int | None = None
    v5_outcome_unlocated_count: int | None = None
    remaining_outcome_reviews: int | None = None
    reachable_commits: set[str] = field(default_factory=set)
    descendant_commits: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


COMMIT_ID_RE = re.compile(r"^[0-9a-f]{40,64}$")
FL_REF_RE = re.compile(r"^FL-[0-9]+$")


def _validate_commit_id(value: object, context: str) -> list[str]:
    if not isinstance(value, str) or not COMMIT_ID_RE.fullmatch(value):
        return [f"{context}: must be a lowercase full git object id"]
    return []


GIT_COMMIT_IDENTITY_FIELDS = (
    ("tree", "%T"),
    ("author_name", "%an"),
    ("author_email", "%ae"),
    ("author_timestamp", "%aI"),
    ("committer_name", "%cn"),
    ("committer_email", "%ce"),
    ("committer_timestamp", "%cI"),
    ("subject", "%s"),
)


def _git_commit_message_sha256(
    commit: str, base_dir: Path
) -> tuple[str | None, str | None]:
    """Hash the exact raw commit message bytes, including intentional endings."""

    try:
        completed = subprocess.run(
            ["git", "cat-file", "commit", commit],
            cwd=base_dir,
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        return None, f"git commit body lookup failed for {commit}: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or b"").decode(
            "utf-8", "replace"
        ).strip()
        return None, f"git commit body lookup failed for {commit}: {detail}"
    header_end = completed.stdout.find(b"\n\n")
    if header_end < 0:
        return None, f"git commit body lookup returned malformed data for {commit}"
    message = completed.stdout[header_end + 2 :]
    return hashlib.sha256(message).hexdigest(), None


def _git_commit_identity(
    commit: str, base_dir: Path
) -> tuple[dict[str, str] | None, str | None]:
    """Read the identity facts that survive a rewritten commit's ancestry."""

    format_spec = "%x00".join(field_format for _, field_format in GIT_COMMIT_IDENTITY_FIELDS)
    try:
        completed = subprocess.run(
            ["git", "show", "-s", f"--format={format_spec}", commit],
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return None, f"git commit identity lookup failed for {commit}: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return None, f"git commit identity lookup failed for {commit}: {detail}"
    values = completed.stdout.rstrip("\n").split("\x00")
    if len(values) != len(GIT_COMMIT_IDENTITY_FIELDS):
        return None, f"git commit identity lookup returned malformed data for {commit}"
    identity = {
        field_name: value
        for (field_name, _), value in zip(GIT_COMMIT_IDENTITY_FIELDS, values)
    }
    message_sha256, message_error = _git_commit_message_sha256(commit, base_dir)
    if message_error:
        return None, message_error
    assert message_sha256 is not None
    identity["message_sha256"] = message_sha256
    return identity, None


def _validate_commit_identity_equivalence(
    operator_reference: str,
    graph_baseline: str,
    context: str,
    base_dir: Path,
) -> list[str]:
    """Require exact Git identity facts for the pre- and post-rewrite anchors."""

    errors: list[str] = []
    operator_identity, operator_error = _git_commit_identity(operator_reference, base_dir)
    if operator_error:
        errors.append(
            f"{context}.operator_reference_commit: {operator_error}"
        )
    graph_identity, graph_error = _git_commit_identity(graph_baseline, base_dir)
    if graph_error:
        errors.append(f"{context}.graph_baseline_commit: {graph_error}")
    if operator_identity is None or graph_identity is None:
        return errors
    for field_name in (*[name for name, _ in GIT_COMMIT_IDENTITY_FIELDS], "message_sha256"):
        if operator_identity[field_name] != graph_identity[field_name]:
            errors.append(
                f"{context}: operator_reference_commit and graph_baseline_commit "
                f"differ in {field_name}"
            )
    return errors


def _validate_retirement_marker(
    approval_ref: str,
    feature_id: str,
    approval: Mapping[str, Any],
    base_dir: Path,
) -> list[str]:
    """Require an operator-owned FL record for every current retirement.

    The gate only consumes this marker.  It deliberately never creates or
    edits the failure log because an agent-authored approval would defeat the
    operator-law boundary this check is intended to enforce.
    """

    context = f"retirement approval {approval_ref!r}"
    errors: list[str] = []
    fl_ref = approval.get("fl_ref")
    if not isinstance(fl_ref, str) or not FL_REF_RE.fullmatch(fl_ref):
        errors.append(f"{context}.fl_ref: must match FL-[0-9]+")
        return errors
    expected_marker = (
        "OPERATOR_APPROVED_FEATURE_RETIREMENT "
        f"feature={feature_id} ref={approval_ref}"
    )
    record_marker = approval.get("record_marker")
    if record_marker != expected_marker:
        errors.append(
            f"{context}.record_marker: must equal {expected_marker!r}"
        )
        return errors
    failure_log = base_dir / "docs" / "FAILURE_LOG.md"
    try:
        failure_log_text = failure_log.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{context}: cannot read operator failure log: {exc}")
        return errors
    heading_re = re.compile(r"(?m)^\s*(?P<level>#{1,6})\s*(?P<ref>FL-[0-9]+)\b[^\n]*$")
    headings = list(heading_re.finditer(failure_log_text))
    fl_heading_index = next(
        (index for index, heading in enumerate(headings) if heading.group("ref") == fl_ref),
        None,
    )
    if fl_heading_index is None:
        errors.append(f"{context}: failure log has no entry for {fl_ref}")
    else:
        section_start = headings[fl_heading_index].end()
        section_end = (
            headings[fl_heading_index + 1].start()
            if fl_heading_index + 1 < len(headings)
            else len(failure_log_text)
        )
        if expected_marker not in failure_log_text[section_start:section_end]:
            errors.append(
                f"{context}: failure log is missing exact operator marker in {fl_ref} section"
            )
    return errors


def _git_current_head(base_dir: Path) -> tuple[str | None, str | None]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return None, f"git HEAD lookup failed: {exc}"
    if completed.returncode != 0:
        return None, f"git HEAD lookup failed: {(completed.stderr or '').strip()}"
    return completed.stdout.strip(), None


def _git_is_ancestor(
    ancestor: str, descendant: str, base_dir: Path
) -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return False, f"git ancestry check failed: {exc}"
    if completed.returncode == 0:
        return True, None
    if completed.returncode == 1:
        return False, None
    detail = (completed.stderr or completed.stdout or "").strip()
    return False, f"git ancestry check failed: {detail}"


def _git_history_commits(
    graph_baseline: str,
    coverage_through: str,
    history_paths: Sequence[str],
    base_dir: Path,
) -> tuple[set[str], str | None]:
    command = ["git", "rev-list", f"{graph_baseline}..{coverage_through}"]
    try:
        completed = subprocess.run(
            command,
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return set(), f"git history census failed: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return set(), f"git history census failed: {detail}"
    commits = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    # ``rev-list graph_baseline..coverage`` excludes the baseline itself.  The
    # complete census includes that anchor so baseline-owned atomic rows cannot
    # disappear without a classified semantic record.
    commits.add(graph_baseline)
    return commits, None


def _git_descendant_commits(anchor: str, base_dir: Path) -> tuple[set[str], str | None]:
    try:
        completed = subprocess.run(
            ["git", "rev-list", f"{anchor}..HEAD"],
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return set(), f"git descendant census failed: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return set(), f"git descendant census failed: {detail}"
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}, None


def _git_commit_message(commit: str, base_dir: Path) -> tuple[str, str | None]:
    try:
        completed = subprocess.run(
            ["git", "show", "-s", "--format=%B", commit],
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return "", f"git commit message lookup failed for {commit}: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return "", f"git commit message lookup failed for {commit}: {detail}"
    return completed.stdout, None


def _validate_descendant_trailer(
    commit: str,
    message: str,
    feature_rows: Mapping[str, Mapping[str, Any]],
    approval_records: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    prefix = f"history descendant {commit}"
    impact_lines = [
        line.split(":", 1)[1].strip()
        for line in message.splitlines()
        if line.startswith("Godot-Feature-Impact:")
    ]
    retirement_lines = [
        line.split(":", 1)[1].strip()
        for line in message.splitlines()
        if line.startswith("Godot-Feature-Retirement:")
    ]
    reason_lines = [
        line.split(":", 1)[1].strip()
        for line in message.splitlines()
        if line.startswith("Godot-Feature-Reason:")
    ]
    errors: list[str] = []
    if len(impact_lines) + len(retirement_lines) != 1:
        errors.append(
            f"{prefix}: requires exactly one Godot-Feature-Impact or Godot-Feature-Retirement trailer"
        )
        return errors
    if retirement_lines:
        parts = retirement_lines[0].split()
        if len(parts) != 2 or not all(_nonempty_string(part) for part in parts):
            errors.append(
                f"{prefix}: retirement trailer must be '<feature-id> <operator-approval-ref>'"
            )
            return errors
        feature_id, approval_ref = parts
        row = feature_rows.get(feature_id)
        if row is None:
            errors.append(f"{prefix}: retirement trailer references unknown feature {feature_id!r}")
        elif row.get("state") != "retired":
            errors.append(f"{prefix}: retirement trailer feature is not retired: {feature_id!r}")
        approval = approval_records.get(approval_ref)
        if approval is None:
            errors.append(f"{prefix}: retirement trailer references unknown approval {approval_ref!r}")
        elif approval.get("feature_id") != feature_id or approval.get("operator_approved") is not True:
            errors.append(f"{prefix}: retirement trailer approval is not current and approved")
        if reason_lines:
            errors.append(f"{prefix}: retirement trailer cannot include Godot-Feature-Reason")
        return errors

    impact = impact_lines[0]
    if impact == "none":
        if len(reason_lines) != 1 or not _nonempty_string(reason_lines[0]):
            errors.append(f"{prefix}: none impact requires one non-empty Godot-Feature-Reason trailer")
        return errors
    if not impact:
        errors.append(f"{prefix}: impact trailer must name a feature or use none")
        return errors
    if reason_lines:
        errors.append(
            f"{prefix}: Godot-Feature-Reason is reserved for none impact"
        )
    feature_ids = [item.strip() for item in impact.split(",")]
    if any(not _nonempty_string(feature_id) for feature_id in feature_ids):
        errors.append(f"{prefix}: impact trailer contains an empty feature id")
    if len(set(feature_ids)) != len(feature_ids):
        errors.append(f"{prefix}: impact trailer contains duplicate feature ids")
    for feature_id in feature_ids:
        if feature_id not in feature_rows:
            errors.append(f"{prefix}: impact trailer references unknown feature {feature_id!r}")
    return errors


def _validate_descendant_trailers(
    anchor: str,
    feature_rows: Mapping[str, Mapping[str, Any]],
    approval_records: Mapping[str, Mapping[str, Any]],
    base_dir: Path,
) -> tuple[set[str], list[str]]:
    commits, history_error = _git_descendant_commits(anchor, base_dir)
    if history_error:
        return set(), [history_error]
    errors: list[str] = []
    for commit in sorted(commits):
        message, message_error = _git_commit_message(commit, base_dir)
        if message_error:
            errors.append(message_error)
        else:
            errors.extend(
                _validate_descendant_trailer(
                    commit,
                    message,
                    feature_rows,
                    approval_records,
                )
            )
    return commits, errors


def _load_external_census(
    document: object, context: str, base_dir: Path, *, historical_audit: bool
) -> ExternalCensus:
    """Validate and independently recompute one complete census document."""

    result = ExternalCensus()
    if not isinstance(document, dict):
        result.errors.append(f"{context}: must be a versioned object")
        return result

    result.errors.extend(_unknown_keys(document, EXTERNAL_CENSUS_KEYS, context))
    version = document.get("version")
    if _is_bool(version) or not isinstance(version, int) or version != SCHEMA_VERSION:
        result.errors.append(f"{context}.version: expected integer {SCHEMA_VERSION}")

    feature_value = document.get("feature_ids")
    feature_errors = _string_list(
        feature_value, f"{context}.feature_ids", nonempty=True
    )
    result.errors.extend(feature_errors)
    if not feature_errors:
        result.feature_ids = list(feature_value)

    evidence_value = document.get("evidence_ids")
    evidence_errors = _string_list(evidence_value, f"{context}.evidence_ids", nonempty=True)
    result.errors.extend(evidence_errors)
    if not evidence_errors:
        result.evidence_ids = list(evidence_value)

    for field_name in (
        "operator_reference_commit",
        "graph_baseline_commit",
        "coverage_through_commit",
    ):
        value = document.get(field_name)
        if not _nonempty_string(value):
            result.errors.append(f"{context}.{field_name}: required non-empty string")
        else:
            setattr(result, field_name, value)
            result.errors.extend(_validate_commit_id(value, f"{context}.{field_name}"))

    history_paths = document.get("history_paths")
    history_path_errors = _string_list(history_paths, f"{context}.history_paths", nonempty=False)
    result.errors.extend(history_path_errors)
    if not history_path_errors:
        result.history_paths = list(history_paths)
        result.history_paths_declared = True
        if result.history_paths:
            result.errors.append(
                f"{context}.history_paths: must be [] so the complete git graph is covered"
            )

    classified = document.get("classified_commits")
    if not isinstance(classified, list):
        result.errors.append(f"{context}.classified_commits: must be a list of semantic records")
        classified = []
    for index, record in enumerate(classified):
        record_context = f"{context}.classified_commits[{index}]"
        if not isinstance(record, dict):
            result.errors.append(f"{record_context}: must be an object, not a bare commit id")
            continue
        result.errors.extend(_unknown_keys(record, CLASSIFIED_COMMIT_KEYS, record_context))
        required_fields = (
            "commit",
            "disposition",
            "feature_ids",
            "reason",
            "evidence_refs",
            "process_failure_category",
            "review_state",
        )
        for field_name in required_fields:
            if field_name not in record:
                result.errors.append(f"{record_context}.{field_name}: required")
        commit = record.get("commit")
        result.errors.extend(_validate_commit_id(commit, f"{record_context}.commit"))
        disposition = record.get("disposition")
        if disposition not in VALID_COMMIT_DISPOSITIONS:
            result.errors.append(
                f"{record_context}.disposition: unknown value {disposition!r}; "
                f"expected one of {sorted(VALID_COMMIT_DISPOSITIONS)}"
            )
        feature_id_errors = _string_list(
            record.get("feature_ids"), f"{record_context}.feature_ids", nonempty=False
        )
        result.errors.extend(feature_id_errors)
        if (
            isinstance(disposition, str)
            and disposition not in EMPTY_FEATURE_DISPOSITIONS
            and isinstance(record.get("feature_ids"), list)
            and not record.get("feature_ids")
        ):
            result.errors.append(
                f"{record_context}.feature_ids: cannot be empty for {disposition!r} disposition"
            )
        if (
            disposition in REQUIRES_EMPTY_FEATURE_DISPOSITIONS
            and isinstance(record.get("feature_ids"), list)
            and record.get("feature_ids")
        ):
            result.errors.append(
                f"{record_context}.feature_ids: must be empty for {disposition!r} disposition"
            )
        if not _nonempty_string(record.get("reason")):
            result.errors.append(f"{record_context}.reason: must be a non-empty string")
        result.errors.extend(
            _string_list(
                record.get("evidence_refs"),
                f"{record_context}.evidence_refs",
                nonempty=False,
            )
        )
        category = record.get("process_failure_category")
        if category is not None and not _nonempty_string(category):
            result.errors.append(
                f"{record_context}.process_failure_category: must be null or a non-empty string"
            )
        elif (
            isinstance(category, str)
            and category not in VALID_PROCESS_FAILURE_CATEGORIES
        ):
            result.errors.append(
                f"{record_context}.process_failure_category: unknown category {category!r}"
            )
        if disposition in PROCESS_FAILURE_DISPOSITIONS and category not in VALID_PROCESS_FAILURE_CATEGORIES:
            result.errors.append(
                f"{record_context}.process_failure_category: required for process-failure disposition"
            )
        review_state = record.get("review_state")
        if review_state not in VALID_SEMANTIC_REVIEW_STATES:
            result.errors.append(
                f"{record_context}.review_state: expected one of "
                f"{sorted(VALID_SEMANTIC_REVIEW_STATES)}"
            )
        elif review_state != "resolved":
            result.errors.append(
                f"{record_context}.review_state: unresolved semantic record blocks the census"
            )
        if isinstance(commit, str) and COMMIT_ID_RE.fullmatch(commit):
            if any(existing.get("commit") == commit for existing in result.classified_commits):
                result.errors.append(f"{context}: duplicate classified commit {commit}")
            else:
                result.classified_commits.append(record)

    metadata_present = [key in document for key in V5_METADATA_KEYS]
    if any(metadata_present):
        missing_metadata = sorted(key for key in V5_METADATA_KEYS if key not in document)
        for key in missing_metadata:
            result.errors.append(f"{context}.{key}: required when v5 metadata is present")
        metadata_values: dict[str, int] = {}
        for key in sorted(V5_METADATA_KEYS - set(missing_metadata)):
            value = document.get(key)
            if _is_bool(value) or not isinstance(value, int) or value < 0:
                result.errors.append(f"{context}.{key}: must be a non-negative integer")
            else:
                metadata_values[key] = value
        if len(metadata_values) == len(V5_METADATA_KEYS):
            result.v5_correction_count = metadata_values["v5_correction_count"]
            result.v5_resolved_count = metadata_values["v5_resolved_count"]
            result.v5_unresolved_count = metadata_values["v5_unresolved_count"]
            result.remaining_semantic_reviews = metadata_values["remaining_semantic_reviews"]
            result.v5_outcome_reviewed_count = metadata_values["v5_outcome_reviewed_count"]
            result.v5_outcome_unlocated_count = metadata_values["v5_outcome_unlocated_count"]
            result.remaining_outcome_reviews = metadata_values["remaining_outcome_reviews"]
            if (
                result.v5_resolved_count + result.v5_unresolved_count
                != result.v5_correction_count
            ):
                result.errors.append(
                    f"{context}: v5 resolved/unresolved counts do not equal v5 correction count"
                )
            if (
                result.v5_correction_count + result.remaining_semantic_reviews
                != EXPECTED_V4_CORRECTION_COUNT
            ):
                result.errors.append(
                    f"{context}: v5 correction count plus remaining reviews must equal {EXPECTED_V4_CORRECTION_COUNT}"
                )
            unresolved_classified = sum(
                record.get("review_state") == "unresolved"
                for record in result.classified_commits
            )
            expected_unresolved = (
                result.remaining_semantic_reviews + result.v5_unresolved_count
            )
            if unresolved_classified != expected_unresolved:
                result.errors.append(
                    f"{context}: classified unresolved count {unresolved_classified} "
                    f"does not equal remaining plus v5 unresolved {expected_unresolved}"
                )
            if (
                result.v5_outcome_reviewed_count + result.v5_outcome_unlocated_count
                != result.v5_correction_count
            ):
                result.errors.append(
                    f"{context}: v5 outcome reviewed/unlocated counts do not equal v5 correction count"
                )
            if result.remaining_outcome_reviews != result.v5_outcome_unlocated_count:
                result.errors.append(
                    f"{context}: remaining outcome reviews do not equal v5 outcome-unlocated count"
                )
            if result.remaining_outcome_reviews:
                result.errors.append(
                    f"{context}: unresolved outcome coverage blocks the census "
                    f"({result.remaining_outcome_reviews} outcome reviews remain)"
                )

    approvals = document.get("retirement_approvals")
    if not isinstance(approvals, list):
        result.errors.append(f"{context}.retirement_approvals: must be a list")
        approvals = []
    for index, approval in enumerate(approvals):
        approval_context = f"{context}.retirement_approvals[{index}]"
        if not isinstance(approval, dict):
            result.errors.append(f"{approval_context}: must be an object")
            continue
        result.errors.extend(_unknown_keys(approval, RETIREMENT_APPROVAL_KEYS, approval_context))
        ref = approval.get("ref")
        feature_id = approval.get("feature_id")
        approved = approval.get("operator_approved")
        fl_ref = approval.get("fl_ref")
        record_marker = approval.get("record_marker")
        if not _nonempty_string(ref):
            result.errors.append(f"{approval_context}.ref: must be a non-empty string")
        if not _nonempty_string(feature_id):
            result.errors.append(f"{approval_context}.feature_id: must be a non-empty string")
        if not isinstance(approved, bool) or not approved:
            result.errors.append(f"{approval_context}.operator_approved: must be true")
        if not isinstance(fl_ref, str) or not FL_REF_RE.fullmatch(fl_ref):
            result.errors.append(f"{approval_context}.fl_ref: must match FL-[0-9]+")
        if not _nonempty_string(record_marker):
            result.errors.append(
                f"{approval_context}.record_marker: must be a non-empty string"
            )
        elif _nonempty_string(ref) and _nonempty_string(feature_id):
            expected_marker = (
                "OPERATOR_APPROVED_FEATURE_RETIREMENT "
                f"feature={feature_id} ref={ref}"
            )
            if record_marker != expected_marker:
                result.errors.append(
                    f"{approval_context}.record_marker: must equal {expected_marker!r}"
                )
        if _nonempty_string(ref):
            if ref in result.approval_records:
                result.errors.append(f"{context}: duplicate retirement approval ref {ref!r}")
            else:
                result.approval_records[ref] = approval

    required_history_fields_ok = (
        result.operator_reference_commit is not None
        and result.graph_baseline_commit is not None
        and result.coverage_through_commit is not None
        and result.history_paths_declared
        and all(
            isinstance(record.get("commit"), str)
            and COMMIT_ID_RE.fullmatch(record["commit"]) is not None
            for record in result.classified_commits
        )
    )
    if required_history_fields_ok:
        graph_baseline_ancestor, graph_baseline_error = _git_is_ancestor(
            result.graph_baseline_commit,
            result.coverage_through_commit,
            base_dir,
        )
        if graph_baseline_error:
            result.errors.append(graph_baseline_error)
        elif not graph_baseline_ancestor:
            result.errors.append(
                f"{context}: graph_baseline_commit is not an ancestor of coverage_through_commit"
            )
        result.errors.extend(
            _validate_commit_identity_equivalence(
                result.operator_reference_commit,
                result.graph_baseline_commit,
                context,
                base_dir,
            )
        )
        actual_commits, history_error = _git_history_commits(
            result.graph_baseline_commit,
            result.coverage_through_commit,
            result.history_paths,
            base_dir,
        )
        if history_error:
            result.errors.append(history_error)
        else:
            result.reachable_commits = actual_commits
            classified_set = {
                record["commit"]
                for record in result.classified_commits
                if isinstance(record.get("commit"), str)
            }
            for missing in sorted(actual_commits - classified_set):
                result.errors.append(f"{context}: unclassified reachable commit: {missing}")
            for extra in sorted(classified_set - actual_commits):
                result.errors.append(f"{context}: classified commit not in reachable range: {extra}")
        if not historical_audit:
            current_head, current_head_error = _git_current_head(base_dir)
            if current_head_error:
                result.errors.append(current_head_error)
            elif current_head is not None:
                anchor_ancestor, anchor_error = _git_is_ancestor(
                    result.coverage_through_commit,
                    current_head,
                    base_dir,
                )
                if anchor_error:
                    result.errors.append(anchor_error)
                elif not anchor_ancestor:
                    result.errors.append(
                        f"{context}.coverage_through_commit: anchor is not an ancestor of current HEAD {current_head!r}"
                    )
    return result


def _validate_continuity(
    prior_registry: object,
    prior_census_document: object,
    current_rows: Sequence[Mapping[str, Any]],
    current_evidence_ids: set[str],
    current_approval_records: Mapping[str, Mapping[str, Any]],
    current_external_census: ExternalCensus | None,
    base_dir: Path,
) -> list[str]:
    """Enforce append-only feature/evidence continuity from a prior snapshot."""

    errors: list[str] = []
    if not isinstance(prior_registry, dict):
        return ["registry baseline: prior registry must be an object"]
    prior_features = prior_registry.get("features")
    if not isinstance(prior_features, list):
        return ["registry baseline: prior registry features must be a list"]

    prior_by_id: dict[str, Mapping[str, Any]] = {}
    prior_feature_evidence: set[str] = set()
    for index, row in enumerate(prior_features):
        context = f"registry baseline.features[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{context}: must be an object")
            continue
        feature_id = row.get("id")
        if not _nonempty_string(feature_id):
            errors.append(f"{context}.id: must be a non-empty string")
            continue
        if feature_id in prior_by_id:
            errors.append(f"registry baseline: duplicate prior feature id {feature_id!r}")
        else:
            prior_by_id[feature_id] = row
        state = row.get("state")
        if not isinstance(state, str) or state not in VALID_STATES:
            errors.append(f"{context}.state: unknown status {state!r}")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list):
            errors.append(f"{context}.evidence_refs: must be a list")
        else:
            prior_feature_evidence.update(
                item for item in refs if _nonempty_string(item)
            )

    prior_external = _load_external_census(
        prior_census_document,
        "registry baseline external census",
        base_dir,
        historical_audit=True,
    )
    errors.extend(prior_external.errors)
    if current_external_census is not None:
        prior_records = {
            record.get("commit"): record
            for record in prior_external.classified_commits
            if isinstance(record.get("commit"), str)
        }
        current_records = {
            record.get("commit"): record
            for record in current_external_census.classified_commits
            if isinstance(record.get("commit"), str)
        }
        for commit, prior_record in sorted(prior_records.items()):
            current_record = current_records.get(commit)
            if current_record is None:
                errors.append(
                    f"registry baseline: prior classified commit removed: {commit!r}"
                )
            elif current_record != prior_record:
                errors.append(
                    f"registry baseline: prior classified commit record changed: {commit!r}"
                )
    prior_evidence = set(prior_external.evidence_ids) | prior_feature_evidence

    current_by_id = {
        row.get("id"): row
        for row in current_rows
        if _nonempty_string(row.get("id"))
    }
    prior_feature_ids = set(prior_external.feature_ids)
    for feature_id in sorted(prior_feature_ids - set(current_by_id)):
        errors.append(
            f"registry baseline: prior census feature id removed: {feature_id!r}"
        )
    for feature_id in sorted(set(prior_by_id) - set(current_by_id)):
        errors.append(f"registry baseline: prior feature id removed: {feature_id!r}")
    for evidence_id in sorted(prior_evidence - current_evidence_ids):
        errors.append(f"registry baseline: prior evidence id removed: {evidence_id!r}")

    for feature_id, prior_row in prior_by_id.items():
        current_row = current_by_id.get(feature_id)
        if current_row is None:
            continue

        # An atomic row's identity is its observable claim and semantic
        # classification.  Preserve those fields across revisions so a row
        # cannot be silently renamed, generalized, or downgraded while
        # retaining the same evidence identity.  Route ownership and command
        # details intentionally remain mutable as implementations move.
        for field_name in (
            "id",
            "observable",
            "behavior_class",
            "parent_family_id",
            "name",
            "category",
        ):
            if current_row.get(field_name) != prior_row.get(field_name):
                errors.append(
                    f"registry baseline: prior feature semantic field changed "
                    f"for {feature_id!r}: {field_name!r}"
                )

        prior_refs = prior_row.get("evidence_refs")
        current_refs = current_row.get("evidence_refs")
        if isinstance(prior_refs, list) and isinstance(current_refs, list):
            removed_refs = {
                ref for ref in prior_refs if _nonempty_string(ref) and ref not in current_refs
            }
            for evidence_id in sorted(removed_refs):
                errors.append(
                    f"registry baseline: prior feature evidence ref removed "
                    f"for {feature_id!r}: {evidence_id!r}"
                )
        prior_state = prior_row.get("state")
        current_state = current_row.get("state")
        if prior_state == "retired" and current_state != "retired":
            errors.append(
                f"registry baseline: retired feature cannot be reactivated: {feature_id!r}"
            )
        if prior_state == "active" and current_state == "retired":
            # Current-row validation already checked the record's shape and
            # independent approval.  Keep this explicit continuity branch so
            # the only legal active-state transition is auditable here too.
            approval_ref = current_row.get("operator_approval_ref")
            approval = current_approval_records.get(approval_ref)
            if (
                not _nonempty_string(approval_ref)
                or approval is None
                or approval.get("feature_id") != feature_id
                or approval.get("operator_approved") is not True
            ):
                errors.append(
                    f"registry baseline: active feature retirement lacks current independent approval: {feature_id!r}"
                )
        elif prior_state == "active" and current_state != "active":
            errors.append(
                f"registry baseline: invalid state transition for active feature {feature_id!r}"
            )
    return errors


def _validate_check(
    check: object,
    context: str,
    behavior_class: str,
    state: str,
    base_dir: Path,
) -> list[str]:
    if not isinstance(check, dict):
        return [f"{context}: must be an object"]
    kind = check.get("kind")
    errors: list[str] = []
    if not isinstance(kind, str) or kind not in VALID_CHECK_KINDS:
        errors.append(
            f"{context}.kind: unknown check kind {kind!r}; expected one of {sorted(VALID_CHECK_KINDS)}"
        )
        return errors
    if kind == "command":
        errors.extend(
            _validate_command(check, context, base_dir, behavior_class, state)
        )
    else:
        errors.extend(_validate_static_contract(check, context, base_dir))
        if state == "active":
            errors.append(
                f"{context}.kind: static-contract is not valid for active behavior; "
                "active features require an executable command"
            )
    if state == "active" and kind != "command":
        errors.append(
            f"{context}.kind: active features require an executable command"
        )
    return errors


@dataclass(frozen=True)
class ValidationResult:
    """Deterministic schema/census validation result."""

    errors: tuple[str, ...] = ()
    feature_count: int = 0
    expected_evidence_count: int = 0
    registered_evidence_count: int = 0
    history_commit_count: int = 0
    classified_commit_count: int = 0
    descendant_commit_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "feature_count": self.feature_count,
            "expected_evidence_count": self.expected_evidence_count,
            "registered_evidence_count": self.registered_evidence_count,
            "history_commit_count": self.history_commit_count,
            "classified_commit_count": self.classified_commit_count,
            "descendant_commit_count": self.descendant_commit_count,
        }


def validate_registry(
    registry: object,
    *,
    base_dir: Path = REPO_ROOT,
    historical_audit: bool = False,
    prior_registry: object | None = None,
    prior_census: object | None = None,
) -> ValidationResult:
    """Validate a registry object without executing any feature checks."""

    base_dir = Path(base_dir)
    errors: list[str] = []
    if not isinstance(registry, dict):
        return ValidationResult(("registry: top-level value must be an object",))

    errors.extend(_unknown_keys(registry, TOP_LEVEL_KEYS, "registry"))
    version = registry.get("version")
    if _is_bool(version) or not isinstance(version, int) or version != SCHEMA_VERSION:
        errors.append(f"registry.version: expected integer {SCHEMA_VERSION}")

    census = registry.get("census")
    expected_ids: list[str] = []
    approval_records: dict[str, dict[str, Any]] = {}
    external_census: ExternalCensus | None = None
    history_commit_count = 0
    classified_commit_count = 0
    if not isinstance(census, dict):
        errors.append("registry.census: must be an object")
    else:
        errors.extend(_unknown_keys(census, CENSUS_KEYS, "registry.census"))
        errors.extend(_validate_provenance(census.get("provenance"), "registry.census.provenance"))
        expected_census_path_value = census.get("expected_census_path")
        if not _nonempty_string(expected_census_path_value):
            errors.append("registry.census.expected_census_path: required non-empty string")
        else:
            expected_path = _resolve_path(expected_census_path_value, base_dir)
            try:
                expected_document = json.loads(expected_path.read_text(encoding="utf-8"))
            except OSError as exc:
                errors.append(f"registry.census.expected_census_path: could not read {expected_census_path_value!r}: {exc}")
                expected_document = None
            except json.JSONDecodeError as exc:
                errors.append(f"registry.census.expected_census_path: invalid JSON: {exc}")
                expected_document = None
            external_census = _load_external_census(
                expected_document,
                "external expected census",
                base_dir,
                historical_audit=historical_audit,
            )
            expected_ids = external_census.evidence_ids
            approval_records = external_census.approval_records
            history_commit_count = len(external_census.reachable_commits)
            classified_commit_count = len(external_census.classified_commits)
            errors.extend(external_census.errors)

    features = registry.get("features")
    feature_rows: list[Mapping[str, Any]] = []
    if not isinstance(features, list):
        errors.append("registry.features: must be a list")
    else:
        if not features:
            errors.append("registry.features: must not be empty")
        for index, feature in enumerate(features):
            context = f"registry.features[{index}]"
            if not isinstance(feature, dict):
                errors.append(f"{context}: must be an object")
                continue
            feature_rows.append(feature)
            errors.extend(_unknown_keys(feature, FEATURE_KEYS, context))
            feature_id = feature.get("id")
            for field_name in ("id", "name", "category"):
                if not _nonempty_string(feature.get(field_name)):
                    errors.append(f"{context}.{field_name}: must be a non-empty string")
            if not _nonempty_string(feature.get("observable")):
                errors.append(
                    f"{context}.observable: must be a non-empty machine-observable claim"
                )
            if "parent_family_id" in feature and not _nonempty_string(
                feature.get("parent_family_id")
            ):
                errors.append(
                    f"{context}.parent_family_id: must be a non-empty string when provided"
                )
            state = feature.get("state")
            if not isinstance(state, str) or state not in VALID_STATES:
                errors.append(
                    f"{context}.state: unknown status {state!r}; expected one of {sorted(VALID_STATES)}"
                )
                state = "active"
            behavior_class = feature.get("behavior_class")
            if not isinstance(behavior_class, str) or behavior_class not in VALID_BEHAVIOR_CLASSES:
                errors.append(
                    f"{context}.behavior_class: unknown class {behavior_class!r}; expected one of {sorted(VALID_BEHAVIOR_CLASSES)}"
                )
                behavior_class = "static"
            for field_name in ("evidence_refs", "source_owners"):
                errors.extend(_string_list(feature.get(field_name), f"{context}.{field_name}"))
            errors.extend(
                _validate_check(
                    feature.get("check"),
                    f"{context}.check",
                    behavior_class,
                    state,
                    base_dir,
                )
            )
            if state == "retired":
                for field_name in ("operator_approval_ref", "retirement_record"):
                    if not _nonempty_string(feature.get(field_name)):
                        errors.append(
                            f"{context}.{field_name}: required non-empty retirement authorization"
                        )
                approval_ref = feature.get("operator_approval_ref")
                if _nonempty_string(approval_ref):
                    approval = approval_records.get(approval_ref)
                    if approval is None:
                        errors.append(
                            f"{context}.operator_approval_ref: no independent approval record for {approval_ref!r}"
                        )
                    else:
                        if approval.get("feature_id") != feature.get("id"):
                            errors.append(
                                f"{context}.operator_approval_ref: approval feature id does not match row id"
                            )
                        if approval.get("operator_approved") is not True:
                            errors.append(
                                f"{context}.operator_approval_ref: independent approval is not operator-approved"
                            )
                        elif not historical_audit and _nonempty_string(feature.get("id")):
                            errors.extend(
                                _validate_retirement_marker(
                                    approval_ref,
                                    str(feature.get("id")),
                                    approval,
                                    base_dir,
                                )
                            )

    # FL-4512: the canonical census cannot evade coverage by deleting every
    # true_ff field. Historical foreign registries remain readable unchanged.
    provenance = census.get("provenance", {}) if isinstance(census, dict) else {}
    canonical_mapping = isinstance(provenance, dict) and provenance.get("source") == "docs/FAILURE_LOG.md#FL-4781"
    if canonical_mapping or any("true_ff" in row for row in feature_rows):
        errors.extend(validate_true_ff_mappings(features, base_dir=base_dir))

    retired_refs = {
        row.get("operator_approval_ref")
        for row in feature_rows
        if row.get("state") == "retired" and _nonempty_string(row.get("operator_approval_ref"))
    }
    for ref, approval in approval_records.items():
        if ref not in retired_refs:
            errors.append(
                f"registry.census: orphan retirement approval {ref!r} is not referenced by a retired feature"
            )

    ids = [row.get("id") for row in feature_rows if _nonempty_string(row.get("id"))]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    for duplicate in duplicate_ids:
        errors.append(f"registry.features: duplicate feature id {duplicate!r}")
    registered_feature_set = set(ids)

    registered_ids: list[str] = []
    for index, feature in enumerate(feature_rows):
        refs = feature.get("evidence_refs")
        if isinstance(refs, list):
            registered_ids.extend(item for item in refs if _nonempty_string(item))
    registered_set = set(registered_ids)
    expected_set = set(expected_ids)
    if external_census is not None:
        expected_feature_set = set(external_census.feature_ids)
        for missing in sorted(expected_feature_set - registered_feature_set):
            errors.append(f"registry.census: expected feature id omitted: {missing!r}")
        for unexpected in sorted(registered_feature_set - expected_feature_set):
            errors.append(f"registry.census: unlisted registered feature id: {unexpected!r}")
    for missing in sorted(expected_set - registered_set):
        errors.append(f"registry.census: expected evidence id omitted: {missing!r}")
    for unexpected in sorted(registered_set - expected_set):
        errors.append(f"registry.census: unlisted registered evidence id: {unexpected!r}")

    current_feature_by_id = {
        row.get("id"): row
        for row in feature_rows
        if _nonempty_string(row.get("id"))
    }
    known_evidence_ids = expected_set | registered_set
    if external_census is not None:
        for index, record in enumerate(external_census.classified_commits):
            record_context = f"external expected census.classified_commits[{index}]"
            disposition = record.get("disposition")
            feature_ids = record.get("feature_ids")
            if not isinstance(feature_ids, list):
                continue
            for feature_id in feature_ids:
                if feature_id not in current_feature_by_id:
                    errors.append(
                        f"{record_context}: unknown feature id {feature_id!r}"
                    )
            if disposition == "retirement":
                for feature_id in feature_ids:
                    row = current_feature_by_id.get(feature_id)
                    if row is not None and row.get("state") != "retired":
                        errors.append(
                            f"{record_context}: retirement feature is not retired: {feature_id!r}"
                        )
            evidence_refs = record.get("evidence_refs")
            if isinstance(evidence_refs, list):
                for evidence_id in evidence_refs:
                    if evidence_id not in known_evidence_ids:
                        errors.append(
                            f"{record_context}: unknown evidence id {evidence_id!r}"
                        )

    if (
        external_census is not None
        and not historical_audit
        and external_census.coverage_through_commit is not None
        and external_census.history_paths_declared
        and COMMIT_ID_RE.fullmatch(external_census.coverage_through_commit)
    ):
        current_head, current_head_error = _git_current_head(base_dir)
        if current_head_error:
            errors.append(current_head_error)
        elif current_head is not None:
            anchor_ancestor, anchor_error = _git_is_ancestor(
                external_census.coverage_through_commit,
                current_head,
                base_dir,
            )
            if anchor_error:
                errors.append(anchor_error)
            elif anchor_ancestor:
                current_feature_rows = {
                    row.get("id"): row
                    for row in feature_rows
                    if _nonempty_string(row.get("id"))
                }
                descendants, descendant_errors = _validate_descendant_trailers(
                    external_census.coverage_through_commit,
                    current_feature_rows,
                    approval_records,
                    base_dir,
                )
                external_census.descendant_commits = descendants
                errors.extend(descendant_errors)

    if (prior_registry is None) != (prior_census is None):
        errors.append("registry baseline comparison requires both prior_registry and prior_census")
    elif prior_registry is not None and prior_census is not None:
        errors.extend(
            _validate_continuity(
                prior_registry,
                prior_census,
                feature_rows,
                registered_set,
                approval_records,
                external_census,
                base_dir,
            )
        )
    errors = sorted(set(errors))
    return ValidationResult(
        tuple(errors),
        feature_count=len(feature_rows),
        expected_evidence_count=len(expected_set),
        registered_evidence_count=len(registered_set),
        history_commit_count=history_commit_count,
        classified_commit_count=classified_commit_count,
        descendant_commit_count=(
            len(external_census.descendant_commits)
            if external_census is not None
            else 0
        ),
    )


@dataclass(frozen=True)
class CheckResult:
    feature_id: str
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    deduplicated: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "feature_id": self.feature_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "deduplicated": self.deduplicated,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class RunResult:
    validation: ValidationResult
    checks: tuple[CheckResult, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.validation.ok and not self.errors and all(
            check.status in {"passed", "retired"} for check in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "validation": self.validation.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
            "errors": list(self.errors),
        }


def _bounded_output(value: str, limit: int) -> tuple[str, bool]:
    if len(value.encode("utf-8", errors="replace")) <= limit:
        return value, False
    # Keep the returned payload within the configured byte bound even when a
    # multibyte character is cut at the boundary.
    encoded = value.encode("utf-8", errors="replace")[:limit]
    return encoded.decode("utf-8", errors="ignore"), True


def _output_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _receipt_snapshot(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.is_file():
        return False, None, None, None
    try:
        payload = path.read_bytes()
        stat = path.stat()
    except OSError:
        return False, None, None, None
    return True, stat.st_mtime_ns, stat.st_size, hashlib.sha256(payload).hexdigest()


def _value_present(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _validate_receipt_statuses(receipt: Mapping[str, Any]) -> str | None:
    """Reject explicit non-measured or unknown result states.

    The executable checker owns the receipt payload, while this gate owns the
    minimum evidence meaning.  A checker that exits zero but emits
    ``status=skipped`` or ``result=unknown`` has not measured the behavior.
    Receipts without an optional status field remain valid because the required
    observation keys and ``behavior_observed`` flag provide the established
    fixture contract.
    """

    for key in sorted(RECEIPT_STATUS_KEYS):
        if key not in receipt or receipt[key] is None:
            continue
        value = receipt[key]
        if not isinstance(value, str) or not value.strip():
            return f"receipt {key} must be a non-empty status string"
        normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
        if normalized in RECEIPT_REJECT_STATUSES:
            return f"receipt {key} reports non-measured result {value!r}"
        if normalized not in RECEIPT_PASS_STATUSES:
            return f"receipt {key} reports unknown result {value!r}"
    return None


def _validate_temporal_receipt(receipt: Mapping[str, Any]) -> str | None:
    continuous_run_id = receipt.get("continuous_run_id")
    if not _nonempty_string(continuous_run_id):
        return "temporal receipt requires a non-empty continuous_run_id"
    samples = receipt.get("frames", receipt.get("samples"))
    if not isinstance(samples, list) or len(samples) < 2:
        return "temporal receipt requires at least two frames or samples"
    identities: list[str] = []
    times: list[object] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            return f"temporal receipt sample {index} is not an object"
        identity = next(
            (sample.get(key) for key in ("frame_id", "sample_id", "id") if key in sample),
            None,
        )
        timestamp = next(
            (
                sample.get(key)
                for key in ("time_ns", "timestamp_ns", "time", "timestamp")
                if key in sample
            ),
            None,
        )
        if not _value_present(identity) or not _value_present(timestamp):
            return f"temporal receipt sample {index} lacks identity or timestamp"
        identities.append(str(identity))
        times.append(timestamp)
        sample_run = sample.get("continuous_run_id")
        if sample_run != continuous_run_id:
            return "temporal receipt samples must share the root continuous_run_id"
    if len(set(identities)) < 2:
        return "temporal receipt requires two distinct frame/sample identities"
    if len({str(timestamp) for timestamp in times}) < 2:
        return "temporal receipt requires time-separated frames/samples"
    return None


def _validate_receipt(
    check: Mapping[str, Any],
    feature: Mapping[str, Any],
    base_dir: Path,
    before_snapshot: tuple[bool, int | None, int | None, str | None] | None,
    *,
    require_fresh: bool,
) -> str | None:
    receipt_path = _resolve_path(str(check["receipt_path"]), base_dir)
    after_snapshot = _receipt_snapshot(receipt_path)
    if not after_snapshot[0]:
        return f"receipt path does not contain a JSON receipt: {check['receipt_path']!r}"
    if require_fresh and before_snapshot == after_snapshot:
        return "receipt was not newly written by this check"
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"receipt is not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return "receipt must be a JSON object"
    if payload.get("schema") != check.get("receipt_schema"):
        return "receipt schema does not match check receipt_schema"

    candidate: dict[str, Any] = dict(payload)
    feature_results = payload.get("features")
    if isinstance(feature_results, dict):
        entry = feature_results.get(feature.get("id"))
        if not isinstance(entry, dict):
            return f"receipt has no per-feature result for {feature.get('id')!r}"
        if entry.get("behavior_observed") is not True:
            return "per-feature receipt behavior_observed must be true"
        candidate.update(entry)
    elif "features" in payload:
        return "receipt features must be a per-feature object"
    status_error = _validate_receipt_statuses(candidate)
    if status_error:
        return status_error
    if candidate.get("feature_id") != feature.get("id"):
        return "receipt feature_id does not match registry feature"
    if candidate.get("route_id") != check.get("route_id"):
        return "receipt route_id does not match check route_id"
    expected_target_identity = check.get("target_identity")
    if not isinstance(expected_target_identity, dict):
        return "check target_identity is missing or malformed"
    receipt_target_identity = candidate.get("target_identity")
    if receipt_target_identity != expected_target_identity:
        return "receipt target_identity does not match check target_identity"
    current_head, head_error = _git_current_head(base_dir)
    if head_error:
        return head_error
    if candidate.get("source_head") != current_head:
        return "receipt source_head does not match current HEAD"
    if candidate.get("behavior_observed") is not True:
        return "receipt behavior_observed must be true"
    for key in check.get("required_observation_keys", []):
        if key not in candidate or not _value_present(candidate[key]):
            return f"receipt missing non-empty required observation {key!r}"

    behavior_class = feature.get("behavior_class")
    if behavior_class == "temporal":
        temporal_error = _validate_temporal_receipt(candidate)
        if temporal_error:
            return temporal_error
    elif behavior_class == "interactive":
        if not _value_present(candidate.get("event_sequence")):
            return "interactive receipt requires a user-reachable event_sequence"
        if not _value_present(candidate.get("runtime_truth")):
            return "interactive receipt requires matching runtime_truth"
    return None


def _run_command(
    check: Mapping[str, Any],
    feature: Mapping[str, Any],
    feature_id: str,
    base_dir: Path,
) -> CheckResult:
    argv = list(check["argv"])
    timeout = float(check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    max_output = int(check.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
    cwd = _resolve_path(str(check.get("cwd", ".")), base_dir)
    before_snapshot = None
    if feature.get("behavior_class") in EXECUTABLE_BEHAVIOR_CLASSES:
        before_snapshot = _receipt_snapshot(
            _resolve_path(str(check["receipt_path"]), base_dir)
        )
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_output(_output_text(exc.stdout), max_output)
        stderr, stderr_truncated = _bounded_output(_output_text(exc.stderr), max_output)
        return CheckResult(
            feature_id,
            "failed",
            None,
            stdout,
            stderr,
            stdout_truncated,
            stderr_truncated,
            f"command timed out after {timeout:g} seconds",
        )
    except OSError as exc:
        return CheckResult(feature_id, "failed", None, error=f"command could not execute: {exc}")
    stdout, stdout_truncated = _bounded_output(_output_text(completed.stdout), max_output)
    stderr, stderr_truncated = _bounded_output(_output_text(completed.stderr), max_output)
    status = "passed" if completed.returncode == 0 else "failed"
    error = None if status == "passed" else f"command exited with status {completed.returncode}"
    if status == "passed" and feature.get("behavior_class") in EXECUTABLE_BEHAVIOR_CLASSES:
        receipt_error = _validate_receipt(
            check,
            feature,
            base_dir,
            before_snapshot,
            require_fresh=True,
        )
        if receipt_error:
            status = "failed"
            error = receipt_error
    return CheckResult(
        feature_id,
        status,
        completed.returncode,
        stdout,
        stderr,
        stdout_truncated,
        stderr_truncated,
        error,
    )


def _run_static_contract(
    check: Mapping[str, Any], feature_id: str, base_dir: Path
) -> CheckResult:
    path = _resolve_path(str(check["path"]), base_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(feature_id, "failed", error=f"static contract could not read: {exc}")
    needle = str(check["contains"])
    if needle not in text:
        return CheckResult(
            feature_id,
            "failed",
            error=f"static contract text not found in {check['path']!r}",
        )
    return CheckResult(feature_id, "passed", stdout=f"matched {check['path']!r}")


def _command_key(check: Mapping[str, Any], base_dir: Path) -> tuple[Any, ...]:
    """Return the identity of one validated command route for run deduplication."""

    # Receipt and target contracts describe each feature's observation, not
    # process execution.  Deduplicate one canonical process route even when
    # its receipt publishes different per-feature identities.
    return (
        tuple(check["argv"]),
        str(_resolve_path(str(check.get("cwd", ".")), base_dir)),
        float(check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        int(check.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)),
    )


def _feature_execution_validation_errors(
    feature: object,
    index: int,
    base_dir: Path,
    duplicate_ids: set[str],
) -> list[str]:
    """Validate one feature enough to decide whether its check may execute.

    Registry-level errors such as an unresolved census must not suppress an
    otherwise valid diagnostic check.  This per-row validation is deliberately
    stricter than a kind/executable probe, so malformed rows never reach a
    subprocess while aggregate validation remains RED.
    """

    context = f"registry.features[{index}]"
    if not isinstance(feature, dict):
        return [f"{context}: must be an object"]
    errors = _unknown_keys(feature, FEATURE_KEYS, context)
    feature_id = feature.get("id")
    if not _nonempty_string(feature_id):
        errors.append(f"{context}.id: must be a non-empty string")
    elif feature_id in duplicate_ids:
        errors.append(f"{context}.id: duplicate feature id {feature_id!r}")
    for field_name in ("name", "category", "observable"):
        if not _nonempty_string(feature.get(field_name)):
            errors.append(f"{context}.{field_name}: must be a non-empty string")
    if "parent_family_id" in feature and not _nonempty_string(
        feature.get("parent_family_id")
    ):
        errors.append(f"{context}.parent_family_id: must be a non-empty string when provided")
    state = feature.get("state")
    if not isinstance(state, str) or state not in VALID_STATES:
        errors.append(
            f"{context}.state: unknown status {state!r}; expected one of {sorted(VALID_STATES)}"
        )
    for field_name in ("evidence_refs", "source_owners"):
        errors.extend(_string_list(feature.get(field_name), f"{context}.{field_name}"))
    if state == "active":
        behavior_class = feature.get("behavior_class")
        if not isinstance(behavior_class, str) or behavior_class not in VALID_BEHAVIOR_CLASSES:
            errors.append(
                f"{context}.behavior_class: unknown class {behavior_class!r}; expected one of {sorted(VALID_BEHAVIOR_CLASSES)}"
            )
        else:
            errors.extend(
                _validate_check(
                    feature.get("check"),
                    f"{context}.check",
                    behavior_class,
                    state,
                    base_dir,
                )
            )
    return errors


def _with_feature_id(result: CheckResult, feature_id: str, *, deduplicated: bool) -> CheckResult:
    return CheckResult(
        feature_id=feature_id,
        status=result.status,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        stdout_truncated=result.stdout_truncated,
        stderr_truncated=result.stderr_truncated,
        error=result.error,
        deduplicated=deduplicated,
    )


def run_registry(
    registry: object,
    *,
    base_dir: Path = REPO_ROOT,
    historical_audit: bool = False,
    prior_registry: object | None = None,
    prior_census: object | None = None,
) -> RunResult:
    """Validate and run all active checks in registry order."""

    base_dir = Path(base_dir)
    validation = validate_registry(
        registry,
        base_dir=base_dir,
        historical_audit=historical_audit,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    if not isinstance(registry, dict) or not isinstance(registry.get("features"), list):
        return RunResult(
            validation,
            errors=("registry validation failed; no feature checks available to execute",),
        )

    features = registry["features"]
    assert isinstance(features, list)
    ids = [
        feature.get("id")
        for feature in features
        if isinstance(feature, dict) and _nonempty_string(feature.get("id"))
    ]
    duplicate_ids = {feature_id for feature_id in ids if ids.count(feature_id) > 1}
    invalid_features: dict[int, list[str]] = {}
    runnable_features: set[int] = set()
    # Keep row-level execution eligibility separate from aggregate validation.
    # A broken census or another unrelated row remains a global failure, while
    # every independently schema-valid command can still provide diagnostics.
    for index, feature in enumerate(features):
        row_errors = _feature_execution_validation_errors(
            feature, index, base_dir, duplicate_ids
        )
        if row_errors:
            invalid_features[index] = row_errors
        elif isinstance(feature, dict) and feature.get("state") == "active":
            runnable_features.add(index)

    results: list[CheckResult] = []
    errors: list[str] = []
    command_results: dict[tuple[Any, ...], CheckResult] = {}
    # Capture every route's receipt state before any command runs.  Shared
    # routes execute once, but each feature still owns an independent receipt
    # obligation.  Without this map a second feature could pass from a stale
    # pre-existing artifact merely because the first feature's route passed.
    route_before_snapshots: dict[
        tuple[Any, ...], dict[str, tuple[bool, int | None, int | None, str | None]]
    ] = {}
    for index, feature in enumerate(features):
        if index not in runnable_features or not isinstance(feature, dict):
            continue
        if feature["check"]["kind"] != "command":
            continue
        check = feature["check"]
        key = _command_key(check, base_dir)
        snapshots = route_before_snapshots.setdefault(key, {})
        if feature.get("behavior_class") in EXECUTABLE_BEHAVIOR_CLASSES:
            snapshots[str(feature["id"])] = _receipt_snapshot(
                _resolve_path(str(check["receipt_path"]), base_dir)
            )
        else:
            snapshots[str(feature["id"])] = (False, None, None, None)
    for index, feature in enumerate(features):
        feature_id = str(feature.get("id", f"feature[{index}]")) if isinstance(feature, dict) else f"feature[{index}]"
        if index in invalid_features:
            detail = "; ".join(invalid_features[index])
            result = CheckResult(
                feature_id,
                "skipped",
                error=f"check not executed: {detail}",
            )
            results.append(result)
            errors.append(f"feature {feature_id!r}: {result.error}")
            continue
        if not isinstance(feature, dict):
            continue
        if feature["state"] == "retired":
            results.append(CheckResult(feature_id, "retired"))
            continue
        check = feature["check"]
        if check["kind"] == "command":
            key = _command_key(check, base_dir)
            prior = command_results.get(key)
            if prior is None:
                prior = _run_command(check, feature, feature_id, base_dir)
                command_results[key] = prior
                result = prior
            else:
                result = _with_feature_id(prior, feature_id, deduplicated=True)
                if (
                    prior.status == "passed"
                    and feature.get("behavior_class") in EXECUTABLE_BEHAVIOR_CLASSES
                ):
                    receipt_error = _validate_receipt(
                        check,
                        feature,
                        base_dir,
                        route_before_snapshots[key].get(feature_id),
                        require_fresh=True,
                    )
                    if receipt_error:
                        result = CheckResult(
                            feature_id=feature_id,
                            status="failed",
                            exit_code=prior.exit_code,
                            stdout=prior.stdout,
                            stderr=prior.stderr,
                            stdout_truncated=prior.stdout_truncated,
                            stderr_truncated=prior.stderr_truncated,
                            error=receipt_error,
                            deduplicated=True,
                        )
        else:
            result = _run_static_contract(check, feature_id, base_dir)
        results.append(result)
        if result.status != "passed":
            detail = result.error or f"status {result.status}"
            errors.append(f"feature {feature_id!r}: {detail}")
    return RunResult(validation, tuple(results), tuple(errors))


def load_registry(path: Path) -> object:
    """Load JSON registry with a deterministic, caller-visible parse error."""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"registry has duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except OSError as exc:
        raise ValueError(f"registry could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"registry is not valid JSON: {exc}") from exc


def _git_show_json(base_dir: Path, ref: str, relative_path: str, label: str) -> object:
    try:
        completed = subprocess.run(
            ["git", "show", f"{ref}:{relative_path}"],
            cwd=base_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        raise ValueError(f"{label} could not execute git show: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"{label} is unavailable at {ref}:{relative_path}: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON at {ref}:{relative_path}: {exc}") from exc


def _repo_relative(path: Path, base_dir: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the supplied git repository") from exc


def load_prior_snapshot(
    base_dir: Path, base_ref: str, registry_path: Path
) -> tuple[object, object]:
    """Load the prior registry and its prior external census at ``base_ref``."""

    base_dir = Path(base_dir)
    registry_relative = _repo_relative(registry_path, base_dir, "registry path")
    prior_registry = _git_show_json(
        base_dir, base_ref, registry_relative, "prior registry"
    )
    if not isinstance(prior_registry, dict):
        raise ValueError("prior registry must be an object")
    prior_census_meta = prior_registry.get("census")
    if not isinstance(prior_census_meta, dict):
        raise ValueError("prior registry census must be an object")
    census_path = prior_census_meta.get("expected_census_path")
    if not _nonempty_string(census_path):
        raise ValueError("prior registry census expected_census_path is missing")
    prior_census_path = _resolve_path(census_path, base_dir)
    census_relative = _repo_relative(
        prior_census_path, base_dir, "prior census path"
    )
    prior_census = _git_show_json(
        base_dir, base_ref, census_relative, "prior external census"
    )
    return prior_registry, prior_census


# ---------------------------------------------------------------------------
# Per-feature source fingerprints (FL-4781).
#
# Operator law: "there is supposed to be a hash of every feature".  Every
# registry row names its code through ``source_owners`` ("path",
# "path:symbol" or "path:FIRST-LAST").  This section resolves each owner to
# its source text at one revision, normalises comments and whitespace, and
# hashes the result.  A code change to a feature then becomes a machine-visible
# event instead of an unchecked commit.
#
# The fingerprint is derived, not stored in the registry.  A stored hash would
# be a second truth that goes stale with every commit.  Fingerprints at named
# revisions are preserved in content-addressed receipts instead.
#
# An owner that cannot be resolved is reported as UNRESOLVED with a reason.  It
# is never skipped: an unresolved owner is the census NO_CURRENT_OWNER truth.
# ---------------------------------------------------------------------------

FINGERPRINT_SCHEMA = "asciicker.godot-feature-fingerprint.v1"
FINGERPRINT_DIFF_SCHEMA = "asciicker.godot-feature-fingerprint-diff.v1"
FINGERPRINT_FL_ID = "FL-4781"
DEFAULT_REGISTRY_RELATIVE = "config/godot_feature_regressions.json"
UNRESOLVED_STATUS = "UNRESOLVED"
RESOLVED_STATUS = "resolved"
NO_CURRENT_OWNER_PLACEHOLDER = "UNRESOLVED_NO_CURRENT_SOURCE_OWNER"
INDEX_REVISION = ":"
WORKTREE_REVISION = "WORKTREE"
# RenderShaderSource.gd resolves ``res://`` against the Godot project root.
GODOT_RES_ROOT = "godot_project"
GLSL_SUFFIXES = frozenset({".glsl", ".glslinc"})
C_LIKE_SUFFIXES = frozenset(
    {".glsl", ".glslinc", ".gdshader", ".gdshaderinc", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs"}
)
TS_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".mjs", ".cjs"})
INI_SUFFIXES = frozenset({".tscn", ".tres", ".godot", ".import", ".cfg"})
LINE_RANGE_RE = re.compile(r"^([0-9]+)-([0-9]+)$")
SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CODE_TOKEN_RE = re.compile(r"[A-Za-z0-9_$]+|\S")
_C_TOKEN_RE = re.compile(r"\n|[A-Za-z0-9_$]+|[^\s]")
_INDENT_TOKEN_RE = re.compile(r"\n|[^\S\n]+|[A-Za-z0-9_$]+|\S")
_C_SEGMENT_RE = re.compile(r"//|/\*|\"|'")
_TS_SEGMENT_RE = re.compile(r"//|/\*|\"|'|`")
_HASH_SEGMENT_RE = re.compile(r"#|\"\"\"|'''|\"|'")
_INI_SEGMENT_RE = re.compile(r"(?m)^[ \t]*;|\"")
_BRACKET_RE = re.compile(r"[()\[\]{}]")
_C_FUNC_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_C_TAIL_STOP_RE = re.compile(r"[{};=()]")
_C_STATEMENT_RE = re.compile(r"[;(\[{)\]}]")
_C_DECL_RE = re.compile(
    r"\b(?:const|constexpr|let|var)[ \t]+(?:[A-Za-z_][A-Za-z0-9_<>,\*&:]*[ \t]+)*?"
    r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?=[=;\[:])"
)
_C_DEFINE_RE = re.compile(r"(?m)^[ \t]*#[ \t]*define[ \t]+([A-Za-z_][A-Za-z0-9_]*)")
_C_TYPE_RE = re.compile(
    r"\b(?:struct|class|enum(?:[ \t]+class)?|interface|namespace|union)[ \t]+"
    r"([A-Za-z_][A-Za-z0-9_]*)[^;{}()=]*\{"
)
_TS_TYPE_ALIAS_RE = re.compile(r"\btype[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?:<[^=;]*>)?[ \t]*=")
C_NON_DEF_KEYWORDS = frozenset(
    {
        "if", "for", "while", "switch", "catch", "return", "sizeof", "else", "do",
        "with", "new", "typeof", "function", "await", "delete", "void", "throw",
        "case", "defined", "elif", "ifdef", "ifndef", "layout", "super", "import",
    }
)
_GD_DEF_RE = re.compile(
    r"^(?:@[A-Za-z_][A-Za-z0-9_]*(?:\([^)\n]*\))?\s+)*(?:static\s+)?"
    r"(func|const|var|enum|signal|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_PY_DEF_RE = re.compile(
    r"^(?:async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^()([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]*)?=(?!=)"
)
FEATURE_IMPACT_TRAILER = "Godot-Feature-Impact:"
FEATURE_RETIREMENT_TRAILER = "Godot-Feature-Retirement:"
# Lane rule for commit attribution.  A commit is on the True FF lane when its
# subject names FF as a word ("True FF", "FF terrain", ...).  The conventional
# subject type is the lane and its scope is the label (usually FL ids).
FF_LANE_RE = re.compile(r"(?<![Nn]on-)\b(?:True[ _-]?)?FF\b")
CONVENTIONAL_SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?!?:")
FF_LANE_RULE = (
    "commit subject matches \\b(True[ _-]?)?FF\\b and not non-FF (True FF lane); lane = conventional type, "
    "label = conventional scope; feature is FF when its id/name/observable matches the same pattern"
)


class OwnerUnresolved(Exception):
    """One source owner cannot be resolved at one revision."""


def _source_language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in C_LIKE_SUFFIXES:
        return "c"
    if suffix in TS_SUFFIXES:
        return "ts"
    if suffix == ".gd":
        return "gd"
    if suffix == ".py":
        return "py"
    if suffix in INI_SUFFIXES:
        return "ini"
    return "text"


def _string_end(text: str, start: int, quote: str) -> int:
    index = start + len(quote)
    length = len(text)
    single_line = len(quote) == 1 and quote != "`"
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if text.startswith(quote, index):
            return index + len(quote)
        if single_line and char == "\n":
            return index
        index += 1
    return length


def _source_segments(text: str, language: str) -> list[tuple[str, int, int]]:
    """Split source into offset-preserving code, comment and string segments."""

    if language == "c":
        pattern = _C_SEGMENT_RE
    elif language == "ts":
        pattern = _TS_SEGMENT_RE
    elif language in {"gd", "py"}:
        pattern = _HASH_SEGMENT_RE
    elif language == "ini":
        pattern = _INI_SEGMENT_RE
    else:
        return [("code", 0, len(text))]
    segments: list[tuple[str, int, int]] = []
    length = len(text)
    code_start = 0
    index = 0
    while index < length:
        match = pattern.search(text, index)
        if match is None:
            break
        token = match.group()
        start = match.start()
        if token in {"//", "#"} or token.endswith(";"):
            if token.endswith(";"):
                start = match.end() - 1
            end = text.find("\n", start)
            end = length if end < 0 else end
            kind = "comment"
        elif token == "/*":
            end = text.find("*/", start + 2)
            end = length if end < 0 else end + 2
            kind = "comment"
        else:
            end = _string_end(text, start, token)
            kind = "string"
        if start > code_start:
            segments.append(("code", code_start, start))
        segments.append((kind, start, end))
        code_start = index = max(end, start + 1)
    if code_start < length:
        segments.append(("code", code_start, length))
    return segments


def _masked_source(text: str, segments: Sequence[tuple[str, int, int]]) -> str:
    """Blank comments and string contents while preserving offsets and newlines."""

    parts: list[str] = []
    for kind, start, end in segments:
        chunk = text[start:end]
        if kind == "code":
            parts.append(chunk)
        elif kind == "comment":
            parts.append(re.sub(r"[^\n]", " ", chunk))
        elif len(chunk) >= 2:
            parts.append(chunk[0] + re.sub(r"[^\n]", "_", chunk[1:-1]) + chunk[-1])
        else:
            parts.append(chunk)
    return "".join(parts)


def _normalize_c_like(text: str, segments: Sequence[tuple[str, int, int]]) -> str:
    tokens: list[str] = []
    in_preprocessor = False
    at_line_start = True
    for kind, start, end in segments:
        if kind == "comment":
            continue
        if kind == "string":
            tokens.append(text[start:end])
            at_line_start = False
            continue
        for match in _C_TOKEN_RE.finditer(text, start, end):
            token = match.group()
            if token == "\n":
                if in_preprocessor and not (tokens and tokens[-1] == "\\"):
                    tokens.append("⏎")
                    in_preprocessor = False
                at_line_start = True
                continue
            if at_line_start and token == "#":
                in_preprocessor = True
            at_line_start = False
            tokens.append(token)
    return " ".join(tokens)


def _normalize_indented(text: str, segments: Sequence[tuple[str, int, int]]) -> str:
    """Normalise GDScript/Python while keeping block structure significant."""

    logical: list[tuple[int, list[str]]] = []
    tokens: list[str] | None = None
    depth = 0
    line_width = 0
    physical_start = True

    def open_line() -> list[str]:
        nonlocal tokens
        if tokens is None:
            tokens = []
            logical.append((line_width, tokens))
        return tokens

    for kind, start, end in segments:
        if kind == "comment":
            continue
        if kind == "string":
            open_line().append(text[start:end])
            physical_start = False
            continue
        for match in _INDENT_TOKEN_RE.finditer(text, start, end):
            token = match.group()
            if token == "\n":
                if depth == 0 and not (tokens and tokens[-1] == "\\"):
                    tokens = None
                physical_start = True
                line_width = 0
                continue
            if token[0] in " \t\r\f\v":
                if physical_start:
                    line_width += token.count(" ") + 4 * token.count("\t")
                continue
            physical_start = False
            current = open_line()
            if token in "([{":
                depth += 1
            elif token in ")]}":
                depth = max(0, depth - 1)
            current.append(token)
    stack = [logical[0][0]] if logical else [0]
    lines: list[str] = []
    for width, line_tokens in logical:
        if not line_tokens:
            continue
        if width > stack[-1]:
            stack.append(width)
        else:
            while len(stack) > 1 and width < stack[-1]:
                stack.pop()
        lines.append(f"{len(stack) - 1}|" + " ".join(line_tokens))
    return "\n".join(lines)


def normalize_source(text: str, language: str) -> str:
    """Return comment- and whitespace-insensitive source text for hashing."""

    segments = _source_segments(text, language)
    if language in {"c", "ts"}:
        return _normalize_c_like(text, segments)
    if language in {"gd", "py"}:
        return _normalize_indented(text, segments)
    stripped = "".join(text[start:end] for kind, start, end in segments if kind != "comment")
    return "\n".join(
        " ".join(CODE_TOKEN_RE.findall(line)) for line in stripped.splitlines() if line.strip()
    )


def _bracket_pairs(mask: str) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[tuple[str, int]] = []
    openers = {")": "(", "]": "[", "}": "{"}
    for match in _BRACKET_RE.finditer(mask):
        char = match.group()
        position = match.start()
        if char in "([{":
            stack.append((char, position))
            continue
        opener = openers[char]
        # Tolerate unbalanced preprocessor branches: drop unmatched openers.
        while stack and stack[-1][0] != opener:
            stack.pop()
        if stack:
            _, open_position = stack.pop()
            pairs[open_position] = position
            pairs[position] = open_position
    return pairs


def _statement_end(mask: str, pairs: Mapping[int, int], position: int) -> int:
    index = position
    while True:
        match = _C_STATEMENT_RE.search(mask, index)
        if match is None:
            return len(mask)
        char = match.group()
        if char == ";":
            return match.end()
        if char in "([{":
            close = pairs.get(match.start())
            if close is None:
                return len(mask)
            index = close + 1
            continue
        return match.start()


def _outermost_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(set(spans), key=lambda item: (item[0], -item[1])):
        if result and start >= result[-1][0] and end <= result[-1][1]:
            continue
        result.append((start, end))
    return result


class _SourceUnit:
    """One resolved source unit (a file, or a GLSL file after include expansion)."""

    def __init__(self, text: str, language: str) -> None:
        self.text = text
        self.language = language
        self.segments = _source_segments(text, language)
        self.mask = _masked_source(text, self.segments)
        self._definitions: tuple[dict[str, list[tuple[int, int]]], dict[str, list[tuple[int, int]]]] | None = None

    def normalized(self, start: int = 0, end: int | None = None) -> str:
        return normalize_source(self.text[start:end], self.language)

    def definitions(self) -> tuple[dict[str, list[tuple[int, int]]], dict[str, list[tuple[int, int]]]]:
        """Return (closure index of unit-level definitions, local declarations).

        Local declarations map a name to its enclosing definition span.
        """

        if self._definitions is None:
            if self.language in {"gd", "py"}:
                self._definitions = self._indented_definitions()
            else:
                self._definitions = self._c_like_definitions()
        return self._definitions

    def _c_like_definitions(self) -> tuple[dict[str, list[tuple[int, int]]], dict[str, list[tuple[int, int]]]]:
        mask = self.mask
        pairs = _bracket_pairs(mask)
        index: dict[str, list[tuple[int, int]]] = defaultdict(list)
        local: dict[str, list[tuple[int, int]]] = defaultdict(list)
        functions: list[tuple[int, int, int, str]] = []
        for match in _C_FUNC_RE.finditer(mask):
            name = match.group(1)
            if name in C_NON_DEF_KEYWORDS:
                continue
            if match.start() > 0 and mask[match.start() - 1] in ".>":
                continue
            close = pairs.get(match.end() - 1)
            if close is None:
                continue
            stop = _C_TAIL_STOP_RE.search(mask, close + 1)
            if stop is None or stop.group() != "{" or stop.start() - close > 400:
                continue
            body_close = pairs.get(stop.start())
            if body_close is None:
                continue
            start = mask.rfind("\n", 0, match.start()) + 1
            functions.append((start, body_close + 1, stop.start(), name))
        for start, end, _, name in functions:
            index[name].append((start, end))
        top_level = _outermost_spans([(start, end) for start, end, _, _ in functions])
        top_starts = [start for start, _ in top_level]
        by_start = sorted(functions)

        def enclosing_function(position: int) -> tuple[int, int] | None:
            slot = bisect.bisect_right(top_starts, position) - 1
            if slot < 0 or not (top_level[slot][0] <= position < top_level[slot][1]):
                return None
            outer_start, outer_end = top_level[slot]
            best: tuple[int, int] | None = None
            first = bisect.bisect_left(by_start, (outer_start, -1, -1, ""))
            for start, end, body_open, _ in by_start[first:]:
                if start > position:
                    break
                if body_open <= position < end:
                    best = (start, end)
            return best

        for match in _C_DECL_RE.finditer(mask):
            name = match.group(1)
            enclosing = enclosing_function(match.start())
            if enclosing is not None:
                local[name].append(enclosing)
                continue
            start = mask.rfind("\n", 0, match.start()) + 1
            index[name].append((start, _statement_end(mask, pairs, match.end())))
        for match in _C_DEFINE_RE.finditer(mask):
            end = mask.find("\n", match.end())
            while end >= 0 and mask[end - 1] == "\\":
                end = mask.find("\n", end + 1)
            end = len(mask) if end < 0 else end
            index[match.group(1)].append((match.start(), end))
        for match in _C_TYPE_RE.finditer(mask):
            if enclosing_function(match.start()) is not None:
                continue
            close = pairs.get(match.end() - 1)
            if close is None:
                continue
            start = mask.rfind("\n", 0, match.start()) + 1
            index[match.group(1)].append((start, close + 1))
        if self.language == "ts":
            for match in _TS_TYPE_ALIAS_RE.finditer(mask):
                if enclosing_function(match.start()) is not None:
                    continue
                start = mask.rfind("\n", 0, match.start()) + 1
                index[match.group(1)].append((start, _statement_end(mask, pairs, match.end())))
        return dict(index), dict(local)

    def _indented_definitions(self) -> tuple[dict[str, list[tuple[int, int]]], dict[str, list[tuple[int, int]]]]:
        mask = self.mask
        starts = [0] + [match.end() for match in re.finditer("\n", mask)]
        line_count = len(starts)
        ends = [starts[i + 1] - 1 if i + 1 < line_count else len(mask) for i in range(line_count)]
        continuation: set[int] = set()
        for kind, start, end in self.segments:
            if kind == "string" and "\n" in self.text[start:end]:
                line = bisect.bisect_right(starts, start)
                while line < line_count and starts[line] < end:
                    continuation.add(line)
                    line += 1
        depth_at = [0] * line_count
        depth = 0
        line = 0
        for match in re.finditer(r"[()\[\]{}\n]", mask):
            char = match.group()
            if char == "\n":
                line += 1
                if line < line_count:
                    depth_at[line] = depth
            elif char in "([{":
                depth += 1
            else:
                depth = max(0, depth - 1)
        indent: list[int | None] = []
        for line in range(line_count):
            content = mask[starts[line]:ends[line]]
            if line in continuation or depth_at[line] > 0 or not content.strip():
                indent.append(None)
                continue
            leading = content[: len(content) - len(content.lstrip())]
            indent.append(leading.count(" ") + 4 * leading.count("\t"))

        def block_span(line: int) -> tuple[int, int]:
            width = indent[line]
            assert width is not None
            last = line
            probe = line + 1
            while probe < line_count:
                probe_width = indent[probe]
                if probe_width is not None and probe_width <= width:
                    break
                if mask[starts[probe]:ends[probe]].strip():
                    last = probe
                probe += 1
            return starts[line], ends[last]

        pattern = _GD_DEF_RE if self.language == "gd" else _PY_DEF_RE
        block_kinds = {"func", "class", "def", "enum", "signal"}
        parsed: list[tuple[int, str, str]] = []
        for line in range(line_count):
            if indent[line] is None:
                continue
            match = pattern.match(mask[starts[line]:ends[line]].lstrip())
            if match is None:
                continue
            if self.language == "gd":
                kind, name = match.group(1), match.group(2)
            else:
                kind = match.group(1) or "assign"
                name = match.group(2) or match.group(4)
            parsed.append((line, kind, name))
        kind_by_line = {line: kind for line, kind, _ in parsed}

        def enclosing_block(line: int) -> tuple[int, int] | None:
            width = indent[line]
            probe = line - 1
            while probe >= 0 and width:
                probe_width = indent[probe]
                if probe_width is not None and probe_width < width:
                    if kind_by_line.get(probe) in {"func", "def"}:
                        return block_span(probe)
                    if kind_by_line.get(probe) == "class":
                        return None
                    width = probe_width
                probe -= 1
            return None

        index: dict[str, list[tuple[int, int]]] = defaultdict(list)
        local: dict[str, list[tuple[int, int]]] = defaultdict(list)
        nested: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for line, kind, name in parsed:
            if indent[line] == 0:
                index[name].append(block_span(line))
                continue
            if kind in block_kinds:
                nested[name].append(block_span(line))
                continue
            enclosing = enclosing_block(line)
            if enclosing is None:
                nested[name].append(block_span(line))
            else:
                local[name].append(enclosing)
        self._nested = dict(nested)
        return dict(index), dict(local)

    def extract(self, symbol: str) -> tuple[str, list[str], str]:
        """Return (normalised text, closure names, resolution) for one symbol."""

        index, local = self.definitions()
        nested = getattr(self, "_nested", {})
        spans = list(index.get(symbol, [])) + list(nested.get(symbol, []))
        resolution = "definition"
        if not spans:
            spans = list(local.get(symbol, []))
            resolution = "enclosing-definition"
        if not spans:
            raise OwnerUnresolved(f"symbol {symbol!r} has no definition in the resolved unit")
        seen = {symbol}
        selected = list(spans)
        queue = list(spans)
        while queue:
            start, end = queue.pop()
            for identifier in set(IDENT_RE.findall(self.mask, start, end)):
                if identifier in seen or identifier not in index:
                    continue
                seen.add(identifier)
                for span in index[identifier]:
                    selected.append(span)
                    queue.append(span)
        text = "\n".join(self.normalized(start, end) for start, end in _outermost_spans(selected))
        return text, sorted(seen - {symbol}), resolution


class _GitObjectStore:
    """Read blobs through persistent ``git cat-file`` processes; no checkout."""

    BLOB_CACHE_BYTES = 256 * 1024 * 1024

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self._check: subprocess.Popen[bytes] | None = None
        self._batch: subprocess.Popen[bytes] | None = None
        self._blobs: OrderedDict[str, bytes] = OrderedDict()
        self._blob_bytes = 0
        self._oids: dict[str, str | None] = {}

    def _process(self, flag: str) -> subprocess.Popen[bytes]:
        attribute = "_check" if flag == "--batch-check" else "_batch"
        process = getattr(self, attribute)
        if process is None:
            process = subprocess.Popen(
                ["git", "cat-file", flag],
                cwd=self.base_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            setattr(self, attribute, process)
        return process

    def oid(self, object_name: str) -> str | None:
        if object_name in self._oids:
            return self._oids[object_name]
        process = self._process("--batch-check")
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(object_name.encode("utf-8") + b"\n")
        process.stdin.flush()
        parts = process.stdout.readline().decode("utf-8", "replace").split()
        oid = parts[0] if len(parts) == 3 and parts[1] == "blob" else None
        # Index lookups must not be cached across stores; commit lookups are
        # immutable.  One store serves one call, so caching both is safe.
        self._oids[object_name] = oid
        return oid

    def blob(self, oid: str) -> bytes:
        cached = self._blobs.get(oid)
        if cached is not None:
            self._blobs.move_to_end(oid)
            return cached
        process = self._process("--batch")
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(oid.encode("ascii") + b"\n")
        process.stdin.flush()
        header = process.stdout.readline().decode("utf-8", "replace").split()
        if len(header) != 3:
            raise OwnerUnresolved(f"git object {oid} is unreadable")
        size = int(header[2])
        data = process.stdout.read(size)
        process.stdout.read(1)
        self._blobs[oid] = data
        self._blob_bytes += len(data)
        while self._blob_bytes > self.BLOB_CACHE_BYTES and len(self._blobs) > 1:
            _, evicted = self._blobs.popitem(last=False)
            self._blob_bytes -= len(evicted)
        return data

    def close(self) -> None:
        for process in (self._check, self._batch):
            if process is not None:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=10)
        self._check = self._batch = None


class _RevisionReader:
    """Read repository paths at a commit, the index (``:``), or the worktree."""

    def __init__(self, store: _GitObjectStore, revision: str | None) -> None:
        self.store = store
        self.revision = revision
        self._worktree: dict[str, bytes] = {}
        self._closures: dict[str, list[tuple[str, str]]] = {}

    def oid(self, path: str) -> str | None:
        if self.revision is None:
            candidate = self.store.base_dir / path
            if not candidate.is_file():
                return None
            data = candidate.read_bytes()
            oid = "worktree:" + hashlib.sha256(data).hexdigest()
            self._worktree[oid] = data
            return oid
        prefix = INDEX_REVISION if self.revision == INDEX_REVISION else f"{self.revision}:"
        return self.store.oid(f"{prefix}{path}")

    def read(self, oid: str) -> str:
        data = self._worktree[oid] if oid.startswith("worktree:") else self.store.blob(oid)
        return data.decode("utf-8", "replace")


def _glsl_include_target(line: str, path: str) -> str | None:
    """Mirror RenderShaderSource.gd include parsing for one source line."""

    stripped = line.strip()
    if not stripped.startswith("#include"):
        return None
    target = stripped[len("#include"):].strip()
    if not target.startswith('"res://') or not target.endswith('"'):
        raise OwnerUnresolved(f"malformed shader include in {path}: {stripped}")
    relative = target[len('"res://'):-1]
    return f"{GODOT_RES_ROOT}/{relative}"


class FeatureFingerprinter:
    """Resolve and hash registry source owners at one or more revisions."""

    UNIT_CACHE_SIZE = 12

    def __init__(self, base_dir: Path = REPO_ROOT) -> None:
        self.base_dir = Path(base_dir)
        self.store = _GitObjectStore(self.base_dir)
        self._readers: dict[str | None, _RevisionReader] = {}
        self._owner_memo: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._units: OrderedDict[tuple[str, str], _SourceUnit] = OrderedDict()
        self._includes: dict[str, list[str]] = {}

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "FeatureFingerprinter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def reader(self, revision: str | None) -> _RevisionReader:
        if revision == WORKTREE_REVISION:
            revision = None
        reader = self._readers.get(revision)
        if reader is None:
            reader = _RevisionReader(self.store, revision)
            self._readers[revision] = reader
        return reader

    def _include_targets(self, reader: _RevisionReader, oid: str, path: str) -> list[str]:
        cached = self._includes.get(oid)
        if cached is None:
            cached = [
                target
                for target in (
                    _glsl_include_target(line, path) for line in reader.read(oid).split("\n")
                )
                if target is not None
            ]
            self._includes[oid] = cached
        return cached

    def source_files(
        self, reader: _RevisionReader, path: str, chain: tuple[str, ...] = ()
    ) -> list[tuple[str, str]]:
        """Return (path, blob id) for a file and, for GLSL, its include closure."""

        if not chain and path in reader._closures:
            return reader._closures[path]
        oid = reader.oid(path)
        if oid is None:
            if chain:
                raise OwnerUnresolved(f"shader include is missing: {path} (from {chain[-1]})")
            raise OwnerUnresolved(f"source file is missing: {path}")
        files = [(path, oid)]
        if Path(path).suffix.lower() in GLSL_SUFFIXES:
            for target in self._include_targets(reader, oid, path):
                if target in chain or target == path:
                    raise OwnerUnresolved(f"shader include is cyclic: {target}")
                files.extend(self.source_files(reader, target, chain + (path,)))
        if not chain:
            reader._closures[path] = files
        return files

    def owner_paths(self, reader: _RevisionReader, owner: str) -> set[str]:
        """Every repository path whose content can change this owner."""

        path = owner.partition(":")[0]
        if Path(path).suffix.lower() not in GLSL_SUFFIXES:
            return {path}
        try:
            return {item for item, _ in self.source_files(reader, path)}
        except OwnerUnresolved:
            return {path}

    def _expanded_text(self, reader: _RevisionReader, path: str) -> str:
        oid = reader.oid(path)
        assert oid is not None
        text = reader.read(oid)
        if Path(path).suffix.lower() not in GLSL_SUFFIXES:
            return text
        lines = text.split("\n")
        for index, line in enumerate(lines):
            target = _glsl_include_target(line, path)
            if target is not None:
                lines[index] = self._expanded_text(reader, target)
        return "\n".join(lines)

    def _unit(self, reader: _RevisionReader, path: str, files: Sequence[tuple[str, str]]) -> _SourceUnit:
        key = (path, hashlib.sha256(json.dumps(list(files)).encode("utf-8")).hexdigest())
        unit = self._units.get(key)
        if unit is None:
            unit = _SourceUnit(self._expanded_text(reader, path), _source_language(path))
            self._units[key] = unit
            while len(self._units) > self.UNIT_CACHE_SIZE:
                self._units.popitem(last=False)
        else:
            self._units.move_to_end(key)
        return unit

    def owner(self, reader: _RevisionReader, owner: str) -> dict[str, Any]:
        """Return one owner's fingerprint record; UNRESOLVED owners carry a reason."""

        path, _, selector = owner.partition(":")
        record: dict[str, Any] = {
            "owner": owner,
            "path": path,
            "selector": selector or None,
        }

        def unresolved(reason: str) -> dict[str, Any]:
            return {**record, "status": UNRESOLVED_STATUS, "reason": reason, "fingerprint": None}

        if owner == NO_CURRENT_OWNER_PLACEHOLDER:
            return unresolved("NO_CURRENT_OWNER: registry row names no current source owner")
        try:
            files = self.source_files(reader, path)
        except OwnerUnresolved as exc:
            return unresolved(str(exc))
        memo_key = (owner, tuple(files))
        cached = self._owner_memo.get(memo_key)
        if cached is not None:
            return cached
        language = _source_language(path)
        closure: list[str] = []
        try:
            unit = self._unit(reader, path, files)
            if not selector:
                normalized = unit.normalized()
                resolution = "file"
            elif LINE_RANGE_RE.fullmatch(selector):
                first, last = (int(value) for value in LINE_RANGE_RE.fullmatch(selector).groups())
                lines = unit.text.split("\n")
                if first < 1 or last < first or last > len(lines):
                    raise OwnerUnresolved(
                        f"line range {selector} is outside the {len(lines)}-line file"
                    )
                normalized = normalize_source("\n".join(lines[first - 1:last]), language)
                resolution = "line-range"
            elif SYMBOL_RE.fullmatch(selector) and language in {"c", "ts", "gd", "py"}:
                normalized, closure, resolution = unit.extract(selector)
            else:
                raise OwnerUnresolved(
                    f"selector {selector!r} is not a code symbol or line range for a {language} file"
                )
        except OwnerUnresolved as exc:
            result = unresolved(str(exc))
            result["files"] = [item for item, _ in files]
            self._owner_memo[memo_key] = result
            return result
        digest = hashlib.sha256(
            f"{FINGERPRINT_SCHEMA}\0{language}\0{normalized}".encode("utf-8")
        ).hexdigest()
        result = {
            **record,
            "status": RESOLVED_STATUS,
            "reason": None,
            "fingerprint": digest,
            "resolution": resolution,
            "language": language,
            "files": [item for item, _ in files],
            "closure": closure,
        }
        self._owner_memo[memo_key] = result
        return result

    def feature(self, reader: _RevisionReader, row: Mapping[str, Any]) -> dict[str, Any]:
        owners = [self.owner(reader, owner) for owner in row.get("source_owners") or []]
        material = sorted(
            [
                owner["owner"],
                owner["fingerprint"]
                if owner["status"] == RESOLVED_STATUS
                else f"{UNRESOLVED_STATUS}:{owner['reason']}",
            ]
            for owner in owners
        )
        resolved = sum(owner["status"] == RESOLVED_STATUS for owner in owners)
        status = (
            RESOLVED_STATUS
            if owners and resolved == len(owners)
            else (UNRESOLVED_STATUS if resolved == 0 else "partial")
        )
        return {
            "id": row.get("id"),
            "fingerprint": hashlib.sha256(json.dumps(material).encode("utf-8")).hexdigest(),
            "status": status,
            "owners": owners,
        }

    def feature_paths(self, reader: _RevisionReader, row: Mapping[str, Any]) -> set[str]:
        paths: set[str] = set()
        for owner in row.get("source_owners") or []:
            if owner != NO_CURRENT_OWNER_PLACEHOLDER:
                paths |= self.owner_paths(reader, owner)
        return paths


def _fingerprint_rows(registry: object) -> list[Mapping[str, Any]]:
    if not isinstance(registry, dict) or not isinstance(registry.get("features"), list):
        raise ValueError("registry features must be a list")
    return [
        row
        for row in registry["features"]
        if isinstance(row, dict)
        and _nonempty_string(row.get("id"))
        and isinstance(row.get("source_owners"), list)
    ]


def _is_ff_feature(row: Mapping[str, Any]) -> bool:
    return any(
        FF_LANE_RE.search(str(row.get(key) or "")) for key in ("id", "name", "observable")
    )


def _unresolved_reason_class(reason: str) -> str:
    for prefix, label in (
        ("NO_CURRENT_OWNER", "no-current-owner placeholder"),
        ("source file is missing", "owner file missing"),
        ("shader include", "shader include missing or cyclic"),
        ("malformed shader include", "shader include malformed"),
        ("symbol ", "symbol not defined in file"),
        ("selector ", "selector is not a code symbol"),
        ("line range ", "line range outside file"),
    ):
        if reason.startswith(prefix):
            return label
    return "other"


def _unresolved_counts(snapshot: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    distinct: dict[str, str] = {}
    references = 0
    partial = 0
    fully = 0
    for feature in snapshot.values():
        unresolved = [owner for owner in feature["owners"] if owner["status"] != RESOLVED_STATUS]
        references += len(unresolved)
        for owner in unresolved:
            distinct[owner["owner"]] = owner["reason"]
        if feature["status"] == UNRESOLVED_STATUS:
            fully += 1
        elif unresolved:
            partial += 1
    by_reason: dict[str, int] = defaultdict(int)
    for reason in distinct.values():
        by_reason[_unresolved_reason_class(reason)] += 1
    return {
        "distinct_owners": len(distinct),
        "owner_references": references,
        "features_fully_unresolved": fully,
        "features_partially_unresolved": partial,
        "by_reason": dict(sorted(by_reason.items())),
        "owners": dict(sorted(distinct.items())),
    }


def _git_text(base_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=base_dir, check=False, capture_output=True, text=True, errors="replace"
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def _resolve_commit(base_dir: Path, revision: str) -> str:
    return _git_text(base_dir, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()


def fingerprint_snapshot(
    registry: object, revision: str, *, base_dir: Path = REPO_ROOT
) -> dict[str, Any]:
    """Fingerprint every registry feature at one revision (commit, ``:`` or WORKTREE)."""

    base_dir = Path(base_dir)
    rows = _fingerprint_rows(registry)
    resolved = revision
    if revision not in {INDEX_REVISION, WORKTREE_REVISION}:
        resolved = _resolve_commit(base_dir, revision)
    with FeatureFingerprinter(base_dir) as fingerprinter:
        reader = fingerprinter.reader(resolved)
        features = {str(row["id"]): fingerprinter.feature(reader, row) for row in rows}
    return {
        "schema": FINGERPRINT_SCHEMA,
        "revision": revision,
        "commit": resolved,
        "features": features,
        "unresolved": _unresolved_counts(features),
    }


def _commit_metadata(base_dir: Path, base: str, head: str) -> dict[str, dict[str, Any]]:
    output = _git_text(
        base_dir, "log", "--format=%H%x1f%P%x1f%aI%x1f%s%x1f%B%x1e", f"{base}..{head}"
    )
    metadata: dict[str, dict[str, Any]] = {}
    for record in output.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit, parents, date, subject, body = record.split("\x1f", 4)
        subject_match = CONVENTIONAL_SUBJECT_RE.match(subject)
        metadata[commit] = {
            "commit": commit,
            "parents": parents.split(),
            "date": date,
            "subject": subject,
            "lane": subject_match.group("type").lower() if subject_match else None,
            "label": (subject_match.group("scope") or None) if subject_match else None,
            "ff_lane": bool(FF_LANE_RE.search(subject)),
            "impact_trailer": [
                line.split(":", 1)[1].strip()
                for line in body.splitlines()
                if line.startswith(FEATURE_IMPACT_TRAILER)
            ],
        }
    return metadata


def fingerprint_diff(
    registry: object, base_revision: str, head_revision: str, *, base_dir: Path = REPO_ROOT
) -> dict[str, Any]:
    """List every feature whose fingerprint differs between two revisions.

    Each changed feature carries the commits in ``BASE..HEAD`` whose own
    fingerprint transition changed it (per-commit recompute against every
    parent), so a merge is attributed only when it differs from all parents.
    """

    base_dir = Path(base_dir)
    rows = _fingerprint_rows(registry)
    base = _resolve_commit(base_dir, base_revision)
    head = _resolve_commit(base_dir, head_revision)
    metadata = _commit_metadata(base_dir, base, head)
    order = [
        line.split()[0]
        for line in _git_text(base_dir, "rev-list", "--topo-order", "--reverse", f"{base}..{head}").splitlines()
        if line.strip()
    ]
    owner_paths = {
        owner.partition(":")[0]
        for row in rows
        for owner in row["source_owners"]
        if owner != NO_CURRENT_OWNER_PLACEHOLDER
    }
    touches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with FeatureFingerprinter(base_dir) as fingerprinter:
        base_reader = fingerprinter.reader(base)
        head_reader = fingerprinter.reader(head)
        base_snapshot = {str(row["id"]): fingerprinter.feature(base_reader, row) for row in rows}
        head_snapshot = {str(row["id"]): fingerprinter.feature(head_reader, row) for row in rows}
        for commit in order:
            parents = metadata.get(commit, {}).get("parents") or []
            if not parents:
                continue
            changed = set(
                _git_text(
                    base_dir, "diff-tree", "-r", "--no-renames", "--name-only", "-z", parents[0], commit
                ).split("\0")
            ) - {""}
            if not changed & owner_paths and not any(
                Path(path).suffix.lower() in GLSL_SUFFIXES for path in changed
            ):
                continue
            commit_reader = fingerprinter.reader(commit)
            parent_readers = [fingerprinter.reader(parent) for parent in parents]
            for row in rows:
                paths = fingerprinter.feature_paths(parent_readers[0], row) | fingerprinter.feature_paths(
                    commit_reader, row
                )
                if not paths & changed:
                    continue
                after = fingerprinter.feature(commit_reader, row)
                befores = [fingerprinter.feature(reader, row) for reader in parent_readers]
                if any(before["fingerprint"] == after["fingerprint"] for before in befores):
                    continue
                first_parent = {owner["owner"]: owner for owner in befores[0]["owners"]}
                owners_changed = sorted(
                    owner["owner"]
                    for owner in after["owners"]
                    if (owner["fingerprint"], owner["reason"])
                    != (
                        first_parent.get(owner["owner"], {}).get("fingerprint"),
                        first_parent.get(owner["owner"], {}).get("reason"),
                    )
                )
                info = metadata[commit]
                touches[str(row["id"])].append(
                    {
                        "commit": commit,
                        "subject": info["subject"],
                        "date": info["date"],
                        "lane": info["lane"],
                        "label": info["label"],
                        "ff_lane": info["ff_lane"],
                        "impact_trailer": info["impact_trailer"],
                        "merge": len(parents) > 1,
                        "owners_changed": owners_changed,
                    }
                )
    features: list[dict[str, Any]] = []
    for row in rows:
        feature_id = str(row["id"])
        before = base_snapshot[feature_id]
        after = head_snapshot[feature_id]
        before_owners = {owner["owner"]: owner for owner in before["owners"]}
        owner_rows = []
        for owner in after["owners"]:
            prior = before_owners.get(owner["owner"], {})
            owner_rows.append(
                {
                    "owner": owner["owner"],
                    "base_status": prior.get("status"),
                    "head_status": owner["status"],
                    "base_fingerprint": prior.get("fingerprint"),
                    "head_fingerprint": owner["fingerprint"],
                    "base_reason": prior.get("reason"),
                    "head_reason": owner["reason"],
                    "changed": (prior.get("fingerprint"), prior.get("reason"))
                    != (owner["fingerprint"], owner["reason"]),
                }
            )
        features.append(
            {
                "id": feature_id,
                "name": row.get("name"),
                "category": row.get("category"),
                "parent_family_id": row.get("parent_family_id"),
                "ff_feature": _is_ff_feature(row),
                "changed": before["fingerprint"] != after["fingerprint"],
                "base_fingerprint": before["fingerprint"],
                "head_fingerprint": after["fingerprint"],
                "base_status": before["status"],
                "head_status": after["status"],
                "owners": owner_rows,
                "commits": touches.get(feature_id, []),
            }
        )
    changed = [feature for feature in features if feature["changed"]]
    restored = [feature["id"] for feature in features if not feature["changed"] and feature["commits"]]
    commit_features: dict[str, list[str]] = defaultdict(list)
    for feature in features:
        for touch in feature["commits"]:
            commit_features[touch["commit"]].append(feature["id"])
    return {
        "base": {"revision": base_revision, "commit": base},
        "head": {"revision": head_revision, "commit": head},
        "commit_count": len(order),
        "summary": {
            "feature_count": len(features),
            "changed_feature_count": len(changed),
            "changed_non_ff_feature_count": sum(not item["ff_feature"] for item in changed),
            "changed_ff_feature_count": sum(item["ff_feature"] for item in changed),
            "registered_ff_feature_count": sum(item["ff_feature"] for item in features),
            "touched_then_restored": restored,
            "commits_touching_features": len(commit_features),
            "ff_lane_commits_touching_features": sum(
                metadata[commit]["ff_lane"] for commit in commit_features
            ),
        },
        "unresolved": {
            "base": _unresolved_counts(base_snapshot),
            "head": _unresolved_counts(head_snapshot),
        },
        "commits": [
            {**{key: metadata[commit][key] for key in ("commit", "subject", "lane", "label", "ff_lane", "impact_trailer")}, "features": sorted(commit_features[commit])}
            for commit in order
            if commit in commit_features
        ],
        "features": sorted(features, key=lambda item: (not item["changed"], item["ff_feature"], item["id"])),
        "base_snapshot": base_snapshot,
        "head_snapshot": head_snapshot,
    }


def fingerprint_diff_receipt(
    registry: object,
    windows: Sequence[tuple[str, str]],
    *,
    base_dir: Path = REPO_ROOT,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    registry_bytes = json.dumps(registry, sort_keys=True).encode("utf-8")
    return {
        "schema": FINGERPRINT_DIFF_SCHEMA,
        "fl_id": FINGERPRINT_FL_ID,
        "producer": "scripts/godot_feature_regression_gate.py --fingerprint-diff",
        "registry": {
            "path": _repo_relative(registry_path, base_dir, "registry path") if registry_path else None,
            "canonical_json_sha256": hashlib.sha256(registry_bytes).hexdigest(),
            "note": "current registry rows and owners are applied at both revisions",
        },
        "lane_rule": FF_LANE_RULE,
        "normalisation": (
            "comments removed; whitespace-insensitive token stream; GDScript/Python keep "
            "relative block depth; GLSL expanded with RenderShaderSource.gd include rules; "
            "symbol owners include transitive same-unit callees, constants, defines and types"
        ),
        "windows": [
            fingerprint_diff(registry, base, head, base_dir=base_dir) for base, head in windows
        ],
    }


def _short(commit: str) -> str:
    return commit[:9]


def render_fingerprint_diff_markdown(receipt: Mapping[str, Any], preserved: str | None) -> str:
    lines = [
        "# Feature fingerprint diff (FL-4781)",
        "",
        "Generated by `python3 scripts/godot_feature_regression_gate.py --registry "
        f"{receipt['registry']['path']} --fingerprint-diff BASE HEAD`.",
        f"Durable receipt: `{preserved}`." if preserved else "Durable receipt: not preserved.",
        "Evidence stage: Executed (static source fingerprints). A changed fingerprint means the",
        "feature's code changed. It does not prove a visual or runtime regression.",
        "",
        f"Lane rule: {receipt['lane_rule']}.",
        f"Normalisation: {receipt['normalisation']}.",
        "",
    ]
    for window in receipt["windows"]:
        summary = window["summary"]
        base = window["base"]
        head = window["head"]
        lines += [
            f"## Window {base['revision']} ({_short(base['commit'])}) .. {head['revision']} ({_short(head['commit'])})",
            "",
            f"- Commits in range: {window['commit_count']}",
            f"- Features: {summary['feature_count']}; changed: {summary['changed_feature_count']} "
            f"(non-FF {summary['changed_non_ff_feature_count']}, FF {summary['changed_ff_feature_count']})",
            f"- Registered FF features: {summary['registered_ff_feature_count']}",
            f"- Commits that changed a feature fingerprint: {summary['commits_touching_features']} "
            f"(FF lane: {summary['ff_lane_commits_touching_features']})",
            f"- Touched then restored (net unchanged): {', '.join(summary['touched_then_restored']) or 'none'}",
        ]
        for side in ("base", "head"):
            counts = window["unresolved"][side]
            lines.append(
                f"- UNRESOLVED at {side}: {counts['distinct_owners']} distinct owners, "
                f"{counts['owner_references']} feature-owner references, "
                f"{counts['features_fully_unresolved']} features fully unresolved, "
                f"{counts['features_partially_unresolved']} partially"
            )
        lines.append("")
        changed = [feature for feature in window["features"] if feature["changed"]]
        for heading, subset in (
            ("Changed non-FF features (operator law: expected zero)", [f for f in changed if not f["ff_feature"]]),
            ("Changed FF features", [f for f in changed if f["ff_feature"]]),
        ):
            lines += [f"### {heading}", ""]
            if not subset:
                lines += ["None.", ""]
                continue
            lines += ["| Feature | Name | Category | Changed owners | Commits (sha lane(label) [FF]) |", "|---|---|---|---|---|"]
            for feature in subset:
                owners = "<br>".join(
                    f"`{owner['owner']}`"
                    + ("" if owner["head_status"] == RESOLVED_STATUS else f" ({owner['head_status']})")
                    for owner in feature["owners"]
                    if owner["changed"]
                )
                commits = "<br>".join(
                    f"{_short(touch['commit'])} {touch['lane'] or '?'}({touch['label'] or ''})"
                    + (" FF" if touch["ff_lane"] else "")
                    for touch in feature["commits"]
                ) or "(net change without a single attributable commit)"
                lines.append(
                    f"| {feature['id']} | {feature['name']} | {feature['category']} | {owners} | {commits} |"
                )
            lines.append("")
        lines += ["### Commits that changed feature fingerprints", "", "| Commit | Lane(label) | FF | Trailer | Features | Subject |", "|---|---|---|---|---|---|"]
        for commit in window["commits"]:
            subject = commit["subject"].replace("|", "\\|")
            lines.append(
                f"| {_short(commit['commit'])} | {commit['lane'] or '?'}({commit['label'] or ''}) | "
                f"{'yes' if commit['ff_lane'] else 'no'} | {', '.join(commit['impact_trailer']) or 'none'} | "
                f"{len(commit['features'])}: {', '.join(commit['features'])} | {subject} |"
            )
        lines += ["", "### UNRESOLVED owners at head", ""]
        for owner, reason in window["unresolved"]["head"]["owners"].items():
            lines.append(f"- `{owner}`: {reason}")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_fingerprint_diff_receipt(
    receipt: Mapping[str, Any],
    *,
    base_dir: Path,
    label: str,
    working_root: Path | None = None,
    destination_root: Path | None = None,
    summary_path: Path | None = None,
) -> dict[str, str]:
    """Write the working receipt, then preserve it content-addressed (never overwrite)."""

    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_receipt_preservation import preserve_run_receipt

    base_dir = Path(base_dir)
    run_dir = (working_root or base_dir / ".run" / "godot_feature_regressions" / "fingerprints") / label
    run_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    root = destination_root or base_dir / "artifacts" / "run_receipts"
    preserved_dir = root / FINGERPRINT_FL_ID.lower() / f"{label}__{receipt_sha[:16]}"
    try:
        preserved_display = preserved_dir.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        preserved_display = str(preserved_dir)
    markdown = render_fingerprint_diff_markdown(receipt, preserved_display + "/receipt.json")
    (run_dir / "summary.md").write_text(markdown, encoding="utf-8")
    manifest = preserve_run_receipt(
        repo_root=base_dir,
        receipt_path=receipt_path,
        fl_id=FINGERPRINT_FL_ID,
        runner="scripts/godot_feature_regression_gate.py --fingerprint-diff",
        destination_root=destination_root,
        companion_names=("summary.md",),
    )
    result = {"working_receipt": str(receipt_path), "preserved_manifest": str(manifest)}
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(markdown, encoding="utf-8")
        result["summary_markdown"] = str(summary_path)
    return result


def _trailer_feature_ids(message_text: str) -> set[str]:
    named: set[str] = set()
    for line in message_text.splitlines():
        if line.startswith(FEATURE_IMPACT_TRAILER):
            named.update(
                item.strip()
                for item in line.split(":", 1)[1].split(",")
                if item.strip() and item.strip() != "none"
            )
        elif line.startswith(FEATURE_RETIREMENT_TRAILER):
            parts = line.split(":", 1)[1].split()
            if parts:
                named.add(parts[0])
    return named


def staged_feature_impact_errors(
    message_text: str,
    *,
    base_dir: Path = REPO_ROOT,
    registry_relative: str = DEFAULT_REGISTRY_RELATIVE,
) -> list[str]:
    """Refuse a commit that changes a feature fingerprint without naming it.

    Only features whose owner files (GLSL: include closure) intersect the
    staged paths are fingerprinted, at HEAD and at the index.  Merge commits
    are skipped: their authored commits were checked when they were made.
    """

    base_dir = Path(base_dir)
    git_dir = Path(_git_text(base_dir, "rev-parse", "--absolute-git-dir").strip())
    if (git_dir / "MERGE_HEAD").exists():
        return []
    try:
        head = _resolve_commit(base_dir, "HEAD")
    except ValueError:
        return []
    staged = set(
        _git_text(base_dir, "diff", "--cached", "--no-renames", "--name-only", "-z").split("\0")
    ) - {""}
    if not staged:
        return []
    try:
        registry_text = _git_text(base_dir, "show", f":{registry_relative}")
    except ValueError:
        registry_text = (base_dir / registry_relative).read_text(encoding="utf-8")
    rows = _fingerprint_rows(json.loads(registry_text))
    owner_paths = {
        owner.partition(":")[0] for row in rows for owner in row["source_owners"]
    }
    if not staged & owner_paths and not any(
        Path(path).suffix.lower() in GLSL_SUFFIXES for path in staged
    ):
        return []
    changed: list[tuple[str, list[str]]] = []
    with FeatureFingerprinter(base_dir) as fingerprinter:
        head_reader = fingerprinter.reader(head)
        index_reader = fingerprinter.reader(INDEX_REVISION)
        for row in rows:
            paths = fingerprinter.feature_paths(head_reader, row) | fingerprinter.feature_paths(
                index_reader, row
            )
            if not paths & staged:
                continue
            before = fingerprinter.feature(head_reader, row)
            after = fingerprinter.feature(index_reader, row)
            if before["fingerprint"] == after["fingerprint"]:
                continue
            prior = {owner["owner"]: owner for owner in before["owners"]}
            owners = [
                owner["owner"]
                for owner in after["owners"]
                if (owner["fingerprint"], owner["reason"])
                != (prior[owner["owner"]]["fingerprint"], prior[owner["owner"]]["reason"])
            ]
            changed.append((str(row["id"]), owners))
    named = _trailer_feature_ids(message_text)
    missing = [(feature_id, owners) for feature_id, owners in changed if feature_id not in named]
    if not missing:
        return []
    listing = "\n".join(f"  {feature_id}: {', '.join(owners)}" for feature_id, owners in missing)
    return [
        "GIT GUARD BLOCKED (FL-4781): this commit changes the source fingerprint of "
        f"{len(missing)} registered Godot feature(s) that the message does not name:\n"
        f"{listing}\n"
        "Name every changed feature in one trailer, for example:\n"
        f"  Godot-Feature-Impact: {', '.join(feature_id for feature_id, _ in missing)}"
    ]


def _fingerprint_label(windows: Sequence[tuple[str, str]], base_dir: Path) -> str:
    parts = [
        f"{_short(_resolve_commit(base_dir, base))}-{_short(_resolve_commit(base_dir, head))}"
        for base, head in windows
    ]
    return "fingerprint-diff_" + "_".join(parts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("registry_path", nargs="?", help="registry JSON path")
    parser.add_argument("--registry", dest="registry_option", help="registry JSON path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true", help="validate schema and census only")
    mode.add_argument("--validate-true-ff", action="store_true", help="offline True FF mapping and census-ID check only; no feature execution")
    mode.add_argument("--run", action="store_true", help="validate then execute active checks")
    mode.add_argument(
        "--fingerprint",
        metavar="REV",
        help="print per-feature and per-owner source fingerprints at REV (commit, ':' index, or WORKTREE)",
    )
    mode.add_argument(
        "--fingerprint-diff",
        nargs=2,
        action="append",
        metavar=("BASE", "HEAD"),
        help="list features whose source fingerprint changed in BASE..HEAD; repeatable; "
        "writes a content-addressed receipt under artifacts/run_receipts/fl-4781/",
    )
    mode.add_argument(
        "--staged-impact",
        metavar="MESSAGE_FILE",
        help="commit-msg mode: refuse when a staged change alters a feature fingerprint "
        "that the Godot-Feature-Impact trailer does not name",
    )
    parser.add_argument("--summary-markdown", help="with --fingerprint-diff: also write the readable summary here")
    parser.add_argument("--receipt-working-root", help=argparse.SUPPRESS)
    parser.add_argument("--receipt-destination-root", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON (default)")
    parser.add_argument(
        "--historical-audit",
        action="store_true",
        help="allow an external census head older than current HEAD",
    )
    parser.add_argument(
        "--base-ref",
        help="compare against the registry and external census at this git ref",
    )
    parser.add_argument("--prior-registry", help="prior registry JSON for continuity comparison")
    parser.add_argument("--prior-census", help="prior external census JSON for continuity comparison")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.registry_path and args.registry_option and Path(args.registry_path) != Path(args.registry_option):
        payload = {"ok": False, "errors": ["registry path supplied twice with different values"]}
        print(json.dumps(payload, sort_keys=True))
        return 2
    registry_text = args.registry_option or args.registry_path
    if not registry_text:
        payload = {"ok": False, "errors": ["registry path is required"]}
        print(json.dumps(payload, sort_keys=True))
        return 2
    base_dir = Path.cwd().resolve()
    registry_path = Path(registry_text).expanduser()
    if not registry_path.is_absolute():
        registry_path = base_dir / registry_path
    if args.staged_impact:
        try:
            message_text = Path(args.staged_impact).read_text(encoding="utf-8")
            errors = staged_feature_impact_errors(
                message_text,
                base_dir=base_dir,
                registry_relative=_repo_relative(registry_path, base_dir, "registry path"),
            )
        except (OSError, ValueError, OwnerUnresolved) as exc:
            errors = [f"GIT GUARD BLOCKED (FL-4781): feature fingerprint check failed: {exc}"]
        for error in errors:
            print(error, file=sys.stderr)
        return 1 if errors else 0
    try:
        registry = load_registry(registry_path)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
        return 2

    if args.validate_true_ff:
        features = registry.get("features") if isinstance(registry, dict) else None
        errors = validate_true_ff_mappings(features, base_dir=base_dir)
        try:
            census_path = _resolve_path(registry["census"]["expected_census_path"], base_dir)
            census = load_registry(census_path)
            expected = census["feature_ids"]
            actual = [row["id"] for row in features]
            if len(actual) != len(set(actual)) or set(actual) != set(expected):
                errors.append("true_ff: mapped rows must exactly match the independent census IDs")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"true_ff: cannot check independent census: {exc}")
        print(json.dumps({"ok": not errors, "scope": "offline mapping data only", "errors": errors}, indent=2))
        return 1 if errors else 0

    if args.fingerprint:
        try:
            payload = fingerprint_snapshot(registry, args.fingerprint, base_dir=base_dir)
        except ValueError as exc:
            print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
            return 2
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.fingerprint_diff:
        windows = [(base, head) for base, head in args.fingerprint_diff]
        try:
            receipt = fingerprint_diff_receipt(
                registry, windows, base_dir=base_dir, registry_path=registry_path
            )
            written = write_fingerprint_diff_receipt(
                receipt,
                base_dir=base_dir,
                label=_fingerprint_label(windows, base_dir),
                working_root=Path(args.receipt_working_root) if args.receipt_working_root else None,
                destination_root=Path(args.receipt_destination_root) if args.receipt_destination_root else None,
                summary_path=Path(args.summary_markdown) if args.summary_markdown else None,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            # A produced receipt that cannot be preserved is a failed run.
            print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    **written,
                    "windows": [
                        {
                            "base": window["base"],
                            "head": window["head"],
                            "summary": window["summary"],
                            "unresolved": {
                                side: {key: value for key, value in counts.items() if key != "owners"}
                                for side, counts in window["unresolved"].items()
                            },
                        }
                        for window in receipt["windows"]
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.base_ref and (args.prior_registry or args.prior_census):
        payload = {
            "ok": False,
            "errors": ["--base-ref cannot be combined with --prior-registry/--prior-census"],
        }
        print(json.dumps(payload, sort_keys=True))
        return 2
    if bool(args.prior_registry) != bool(args.prior_census):
        payload = {
            "ok": False,
            "errors": ["--prior-registry and --prior-census must be supplied together"],
        }
        print(json.dumps(payload, sort_keys=True))
        return 2
    prior_registry: object | None = None
    prior_census: object | None = None
    try:
        if args.base_ref:
            prior_registry, prior_census = load_prior_snapshot(
                base_dir, args.base_ref, registry_path
            )
        elif args.prior_registry:
            prior_registry = load_registry(Path(args.prior_registry).expanduser())
            prior_census = load_registry(Path(args.prior_census).expanduser())
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
        return 2

    if args.run:
        result: ValidationResult | RunResult = run_registry(
            registry,
            base_dir=base_dir,
            historical_audit=args.historical_audit,
            prior_registry=prior_registry,
            prior_census=prior_census,
        )
        payload = result.to_dict()
    else:
        result = validate_registry(
            registry,
            base_dir=base_dir,
            historical_audit=args.historical_audit,
            prior_registry=prior_registry,
            prior_census=prior_census,
        )
        payload = {"ok": result.ok, "validation": result.to_dict(), "errors": list(result.errors)}
    payload["registry"] = str(registry_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if bool(payload.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
