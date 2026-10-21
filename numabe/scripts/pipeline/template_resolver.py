#!/usr/bin/env python3
"""Resolve semantic observations against an explicit locale pack chain."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PACK_SCHEMA = "y92.world_template_pack.v1"


def load_pack_chain(root: Path, pack_ids: list[str]) -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack_id in pack_ids:
        if pack_id in seen:
            raise ValueError(f"duplicate template pack: {pack_id}")
        path = root / pack_id / "pack.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != PACK_SCHEMA or document.get("pack_id") != pack_id:
            raise ValueError(f"invalid template pack: {path}")
        missing_parents = set(document.get("parents", [])) - seen
        if missing_parents:
            raise ValueError(
                f"template pack {pack_id} appears before parents {sorted(missing_parents)}"
            )
        packs.append(document)
        seen.add(pack_id)
    return packs


def _context_matches(pack: dict[str, Any], context: dict[str, Any]) -> bool:
    jurisdiction = pack.get("jurisdiction", {})
    for key in ("country_code", "region", "municipality"):
        required = jurisdiction.get(key)
        if required is not None and required != context.get(key):
            return False
    required_operators = set(pack.get("operator_ids", []))
    if required_operators and not required_operators.intersection(context.get("operator_ids", [])):
        return False
    required_sites = set(pack.get("site_contexts", []))
    if required_sites and not required_sites.intersection(context.get("site_contexts", [])):
        return False
    return True


def discover_pack_chain(root: Path, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive the compatible parent-first pack chain from geographic context."""
    available: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*/pack.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != PACK_SCHEMA:
            raise ValueError(f"invalid template pack: {path}")
        pack_id = str(document.get("pack_id") or "")
        if not pack_id or pack_id in available:
            raise ValueError(f"duplicate or absent pack_id: {path}")
        available[pack_id] = document

    compatible = {
        pack_id for pack_id, pack in available.items()
        if _context_matches(pack, context)
    }
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    emitted: set[str] = set()

    def emit(pack_id: str) -> None:
        if pack_id in emitted or pack_id not in compatible:
            return
        if pack_id in visiting:
            raise ValueError(f"template pack parent cycle at {pack_id}")
        visiting.add(pack_id)
        for parent in available[pack_id].get("parents", []):
            if parent not in available:
                raise ValueError(f"template pack {pack_id} has absent parent {parent}")
            if parent not in compatible:
                raise ValueError(
                    f"compatible template pack {pack_id} has incompatible parent {parent}"
                )
            emit(parent)
        visiting.remove(pack_id)
        emitted.add(pack_id)
        ordered.append(available[pack_id])

    for pack_id in sorted(compatible):
        emit(pack_id)
    return ordered


def resolve_building_fallback(*, tags: dict, area_m2: float, packs: list[dict]) -> dict | None:
    """FL-4814: size/use classify an unidentified building for presentation.

    The rules are locale data. Their height is an explicit synthesized prior,
    and cannot replace a source height or change the canonical footprint.
    """
    for pack in reversed(packs):
        for rule in pack.get('building_fallback_rules', []):
            types = rule.get('building_types')
            if types and tags.get('building') not in types:
                continue
            if area_m2 > rule.get('max_area_m2', float('inf')):
                continue
            return {**rule, 'pack_id': pack['pack_id'], 'area_m2': round(area_m2, 3),
                    'provenance_class': 'synthesized',
                    'method': 'locale use/footprint-area prior; not observed building identity'}
    return None


def resolve_data_profile(
    *,
    collection: str,
    attributes: dict[str, Any],
    packs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve declarative locale/operator policy without identity branches.

    Profile match keys are ordinary source attributes.  ``*_in`` accepts a
    list of exact values and ``*_contains`` accepts case-folded substrings.
    A profile without a match block is a fallback.  More specific packs and
    profiles win; executors only consume the resolved parameters.
    """
    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    for specificity, pack in enumerate(packs):
        for profile in pack.get(collection, []):
            match = profile.get("match", {})
            matched = True
            for raw_key, expected in match.items():
                if raw_key.endswith("_contains"):
                    key = raw_key[:-9]
                    actual = str(attributes.get(key, "")).casefold()
                    values = expected if isinstance(expected, list) else [expected]
                    matched = any(str(value).casefold() in actual for value in values)
                elif raw_key.endswith("_in"):
                    key = raw_key[:-3]
                    values = expected if isinstance(expected, list) else [expected]
                    matched = attributes.get(key) in values
                else:
                    matched = attributes.get(raw_key) == expected
                if not matched:
                    break
            if matched:
                resolved = {**profile, "pack_id": pack["pack_id"]}
                candidates.append((specificity, len(match), str(profile.get("id", "")), resolved))
    if not candidates:
        return None
    # A matched semantic/brand rule must beat a child pack's catch-all.  Pack
    # specificity breaks ties between rules with equal evidence specificity.
    return max(candidates, key=lambda row: (row[1], row[0], row[2]))[3]


def resolve_template_candidates(
    *,
    semantic_candidates: list[str],
    context: dict[str, Any],
    packs: list[dict[str, Any]],
    ocr_text: str = "",
) -> list[dict[str, Any]]:
    semantics = set(semantic_candidates)
    results: list[dict[str, Any]] = []
    for specificity, pack in enumerate(packs):
        if not _context_matches(pack, context):
            continue
        jurisdiction = pack.get("jurisdiction", {})
        required_operators = set(pack.get("operator_ids", []))
        if required_operators and not required_operators.intersection(
            context.get("operator_ids", [])
        ):
            continue
        pack_sites = set(pack.get("site_contexts", []))
        if pack_sites and not pack_sites.intersection(context.get("site_contexts", [])):
            continue
        for template in pack.get("templates", []):
            if template["semantic_class"] not in semantics:
                continue
            score = 100 + specificity
            reasons = ["semantic_class"]
            if jurisdiction.get("country_code"):
                score += 20
                reasons.append("country")
            if jurisdiction.get("region"):
                score += 10
                reasons.append("region")
            if jurisdiction.get("municipality"):
                score += 5
                reasons.append("municipality")
            if required_operators:
                score += 30
                reasons.append("operator")
            if pack_sites:
                score += 10
                reasons.append("site_context")
            expected_text = template.get("expected_text", [])
            matched_text = [value for value in expected_text if value and value in ocr_text]
            if matched_text:
                score += 20
                reasons.append("ocr")
            results.append({
                "template_id": template["template_id"],
                "pack_id": pack["pack_id"],
                "semantic_class": template["semantic_class"],
                "identity_bucket": template["identity_bucket"],
                "geometry_family": template["geometry_family"],
                "face_mode": template["face_mode"],
                "score": score,
                "score_reasons": reasons,
                "ocr_matches": matched_text,
                "parameters": dict(template.get("parameters", {})),
            })
    return sorted(results, key=lambda row: (-row["score"], row["template_id"]))


def unresolved_source_fallback(semantic_candidates: list[str]) -> dict[str, Any]:
    return {
        "template_id": None,
        "semantic_candidates": sorted(set(semantic_candidates)),
        "identity_bucket": "unresolved",
        "geometry_family": None,
        "face_mode": "source_derived",
        "resolution_state": "no_compatible_locale_template",
    }
