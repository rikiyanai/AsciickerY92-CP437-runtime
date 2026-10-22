#!/usr/bin/env python3
"""Lower World IR building footprints into the existing building-bake contract.

This is an adapter between the canonical World IR owner and the existing
``bake_osm_building_footprints.py`` gameplay-mass owner.  It does not create a
second building compiler.  Horizontal geometry stays measured OSM geometry;
the height claim records which source rung supplied it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.world_ir import WorldIRStore, assert_valid


# FL-4809 NUMABE-POPPLAN-03 / NUMABE-BUILD-11: height priors are rules facts
# (road_class_defaults storey_height_m / default_building_height_m). The
# mirrors below exist only for a rules-less call; the lane passes
# rules_path so both values come from rules_resolved and the receipt's
# height_contract names the source.
_RULES_MIRROR_STOREY_HEIGHT_M = 3.0
_RULES_MIRROR_DEFAULT_BUILDING_HEIGHT_M = 9.0
_RULES_STOREY_KEY = "storey_height_m"
_RULES_DEFAULT_KEY = "default_building_height_m"
"""Mirror the road_class_defaults DEFAULT facts (no ordinance basis).

Used only when no rules document is passed: the lane owner wires
--rules-path so both values come from rules_resolved instead. Values
must equal the rules rows; the specs run fails closed on any drift
when rules are provided.
"""
# PLATEAU missing-value sentinel for bldg:storeysAboveGround; never trust
# it as a measurement even if a stale entities document still carries one.
STOREYS_SENTINEL_MISSING = 9999
# FL-4932: the canonical TMG height claim and the key it lowers under.
TMG_SOURCE = "tmg_pointcloud"
TMG_HEIGHT_SOURCE_KEY = "tmg:roof_p90"


def _source_ref(feature: dict, observations: dict[str, dict]) -> tuple[str, dict]:
    for obs_id in feature.get("supporting_observation_ids", []):
        obs = observations.get(obs_id) or {}
        ref = (obs.get("attributes") or {}).get("source_ref")
        if ref:
            return str(ref), obs
    feature_id = str(feature.get("feature_id") or "")
    marker = "feature:building_footprint:"
    if feature_id.startswith(marker):
        return feature_id[len(marker):], {}
    raise ValueError(f"building {feature_id!r} has no source reference")


def _parse_positive_number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower().removesuffix("m").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _height_claim(tags: dict, fallback_height_m: float, storey_height_m: float) -> dict:
    explicit = _parse_positive_number(tags.get("height"))
    if explicit is not None:
        return {
            "roof_height_m": explicit,
            "height_provenance_class": "measured",
            "height_method": "OSM height tag interpreted as metres",
            "height_source_key": "height",
            "height_source_value": tags["height"],
        }
    levels = _parse_positive_number(tags.get("building:levels"))
    if levels is not None:
        return {
            "roof_height_m": levels * storey_height_m,
            "height_provenance_class": "derived",
            "height_method": f"OSM building:levels × {storey_height_m:g} m/storey",
            "height_source_key": "building:levels",
            "height_source_value": tags["building:levels"],
        }
    return {
        "roof_height_m": fallback_height_m,
        "height_provenance_class": "synthesized",
        "height_method": "deterministic uncovered-building fallback",
        "height_source_key": None,
        "height_source_value": None,
    }


def _load_canonical_entities(path: Path | None) -> dict[str, dict]:
    """Index canonical entities by source_ref (way/... or plateau/bldg_...).

    OSM source_object_ids already carry the way/... form that _source_ref
    returns; PLATEAU gml ids need the plateau/ prefix to match the
    feature:building_footprint:plateau/bldg_... suffix. A matched pair
    resolves through either key to the same conflated entity.
    """
    if path is None:
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for entity in doc.get("entities", []):
        for source in entity.get("source_bridge", []):
            if source.get("source") == "osm":
                index[str(source["source_object_id"])] = entity
            elif source.get("source") == "plateau":
                _gid = str(source["source_object_id"])
                index["plateau/" + _gid] = entity
                index[_gid] = entity
    return index


def _canonical_height_claim(entity: dict) -> dict | None:
    """Height/storeys override from a canonical entity, or None to fall back.

    Returns None when the entity has no PLATEAU-observed height, so the
    caller runs the unchanged _height_claim ladder.  A conflicting claim
    keeps the authoritative PLATEAU value and reports both.
    """
    claim = (entity.get("claims") or {}).get("height_m") or {}
    state = claim.get("evidence_state")
    if (state == "directly_observed" and claim.get("source") == TMG_SOURCE
            and claim.get("value") is not None):
        # FL-4932 TMG-HEIGHT-01: compile_canonical_entities ranked a measured
        # TMG roof above OSM building:levels; honour it here instead of
        # re-running the OSM ladder, which would hand back the storey count.
        return {
            "roof_height_m": float(claim["value"]),
            "height_provenance_class": "measured",
            "height_method": (
                "TMG 2023 airborne LiDAR p90 roof via canonical_entities "
                + str(entity.get("canonical_id"))
            ),
            "height_source_key": TMG_HEIGHT_SOURCE_KEY,
            "height_source_value": claim["value"],
        }
    if state in ("authoritative_observed", "conflicting"):
        value = claim.get("value")
        if value is None:
            return None
        result = {
            "roof_height_m": float(value),
            "height_provenance_class": state,
            "height_method": (
                "PLATEAU bldg:measuredHeight via canonical_entities "
                + str(entity.get("canonical_id"))
            ),
            "height_source_key": "plateau:measuredHeight",
            "height_source_value": value,
        }
        if state == "conflicting":
            result["conflicting_height_values"] = claim.get("values")
        storeys = ((entity.get("claims") or {}).get("storeys") or {})
        if (storeys.get("evidence_state") == "authoritative_observed"
                and storeys.get("value") is not None
                and storeys.get("value") != STOREYS_SENTINEL_MISSING):
            result["storeys"] = int(storeys["value"])
            result["storeys_source"] = "plateau:storeysAboveGround"
        bridge = {s.get("source"): s.get("observation_id")
                  for s in entity.get("source_bridge", [])}
        if bridge.get("plateau"):
            result["plateau_observation_id"] = bridge["plateau"]
        return result
    return None


def compile_specs(
    osm_world_ir: Path,
    terrain_metadata: Path,
    output_path: Path,
    receipt_path: Path,
    fallback_height_m: float | None = None,
    *, locale_context: dict | None = None,
    canonical_entities: Path | None = None,
    rules_path: Path | None = None,
) -> dict:
    # FL-4809 NUMABE-POPPLAN-03 / NUMABE-BUILD-11: resolve the height
    # priors from rules_resolved when given; fail closed when the rules
    # document lacks the two DEFAULT rows.
    _rules_storey = _RULES_MIRROR_STOREY_HEIGHT_M
    _rules_fallback = _RULES_MIRROR_DEFAULT_BUILDING_HEIGHT_M
    _rules_source = "mirror"
    if rules_path is not None:
        _rdoc = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        try:
            _rcd = _rdoc["road_class_defaults"]
            _rules_storey = float(_rcd[_RULES_STOREY_KEY]["value"])
            _rules_fallback = float(_rcd[_RULES_DEFAULT_KEY]["value"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"rules lack road_class_defaults.{_RULES_STOREY_KEY}/"
                f"{_RULES_DEFAULT_KEY}: no height facts without the "
                "DEFAULT rows")
        _rules_source = "rules_resolved"
    if fallback_height_m is None:
        fallback_height_m = _rules_fallback
    if fallback_height_m <= 0.5:
        raise ValueError("fallback building height must exceed 0.5 m")
    store = WorldIRStore.load(osm_world_ir)
    assert_valid(store.to_dict())
    metadata = json.loads(terrain_metadata.read_text(encoding="utf-8"))
    projection = metadata.get("projection", metadata)
    origin_e = float(projection["origin_easting"])
    origin_n = float(projection["origin_northing"])
    scale = float(projection["content_scale"])
    offset_x = float(projection["offset_x"])
    offset_y = float(projection["offset_y"])
    observations = {
        str(obs["observation_id"]): obs for obs in store.observations
    }
    from scripts.pipeline.template_resolver import discover_pack_chain, resolve_building_fallback
    packs = discover_pack_chain(REPO_ROOT / 'assets/world_templates', locale_context) if locale_context else []

    specs = []
    height_models: dict[str, int] = {}
    height_sources: dict[str, int] = {}
    synthesized_ids: list[dict] = []
    canonical_index = _load_canonical_entities(canonical_entities)
    conflicting_entities: list[dict] = []
    for feature in store.features:
        if feature.get("semantic_class") != "building_footprint":
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("kind") != "polygon" or geometry.get("frame_id") != "epsg32654":
            raise ValueError(
                f"{feature.get('feature_id')} must be an EPSG:32654 polygon"
            )
        ring = geometry.get("coordinates") or []
        footprint = [
            [
                (float(point[0]) - origin_e) * scale + offset_x,
                (float(point[1]) - origin_n) * scale + offset_y,
            ]
            for point in ring
        ]
        if len(footprint) > 1 and footprint[0] == footprint[-1]:
            footprint.pop()
        if len(footprint) < 3:
            raise ValueError(f"{feature.get('feature_id')} has a degenerate footprint")
        source, observation = _source_ref(feature, observations)
        tags = dict((observation.get("attributes") or {}).get("tags") or {})
        # FL-4814: replace the blanket 9 m prior only where source height is absent.
        area = abs(sum(footprint[i][0]*footprint[(i+1)%len(footprint)][1]
                       - footprint[(i+1)%len(footprint)][0]*footprint[i][1]
                       for i in range(len(footprint)))) / (2*scale*scale)
        fallback = resolve_building_fallback(tags=tags, area_m2=area, packs=packs)
        height = _height_claim(tags, fallback['height_m'] if fallback else fallback_height_m, _rules_storey)
        if fallback and height['height_provenance_class'] == 'synthesized':
            height['height_method'] = 'locale size/use prior: ' + fallback['family']
        # CONFLATE-01: the canonical height claim replaces the ladder for
        # matched entities.  _height_claim above stays the fallback for
        # entities without a PLATEAU claim (and for the no-flag path).
        entity = None
        if canonical_index:
            entity = canonical_index.get(source)
            if entity is not None:
                override = _canonical_height_claim(entity)
                if override is not None:
                    plateau_obs = override.pop("plateau_observation_id", None)
                    height = override
                    obs_ids = list(feature.get("supporting_observation_ids", []))
                    if plateau_obs and plateau_obs not in obs_ids:
                        obs_ids.append(plateau_obs)
                    height_source_obs_ids = obs_ids
                else:
                    height_source_obs_ids = list(
                        feature.get("supporting_observation_ids", []))
            else:
                height_source_obs_ids = list(
                    feature.get("supporting_observation_ids", []))
        else:
            height_source_obs_ids = list(
                feature.get("supporting_observation_ids", []))
        _skey = height.get("height_source_key")
        if _skey == "plateau:measuredHeight":
            height_source = "plateau_measured"
        elif _skey == TMG_HEIGHT_SOURCE_KEY:
            height_source = "tmg_lidar_measured"
        elif _skey == "height":
            height_source = "osm_height"
        elif _skey == "building:levels":
            height_source = "osm_levels_derived"
        else:
            height_source = "default_synthesized"
            synthesized_ids.append({
                "feature_id": feature["feature_id"],
                "reason": ("entity_without_height_claim" if entity is not None
                           else "no_entity_match")
                          + ";no OSM height/levels tags",
            })
        height_models[height["height_provenance_class"]] = (
            height_models.get(height["height_provenance_class"], 0) + 1
        )
        height_sources[height_source] = height_sources.get(height_source, 0) + 1
        specs.append({
            "feature_id": feature["feature_id"],
            "source": source,
            "building_fallback": fallback,
            "footprint_area_m2": round(area, 3),
            "inst_name": "building_mass_" + source.replace("/", "_"),
            "footprint": footprint,
            "bake_footprint": footprint,
            # Kept for the old payload reader.  The bake owner uses the
            # terrain-relative roof_height_m claim below for this path.
            "bake_height": 1,
            # FL-4876 SCALE-01: the bake converts roof metres to raw z with
            # spec["content_scale"] and falls back to 2.25 (the old scale225
            # constant) when it is absent. This run's terrain is 3.4213
            # world units per metre, so every mass baked 0.674 of its
            # measured height (scale_receipt baked_mass_scale 36.9 vs 54.7
            # raw/m). The spec carries the run's own scale.
            "content_scale": scale,
            "height_source_observation_ids": height_source_obs_ids,
            "height_source": height_source,
            **height,
        })
        if height.get("height_provenance_class") == "conflicting":
            conflicting_entities.append({
                "feature_id": feature["feature_id"],
                "canonical_id": (canonical_index.get(source, {}).get("canonical_id")
                                 if canonical_index else None),
                "roof_height_m": height.get("roof_height_m"),
                "conflicting_height_values": height.get("conflicting_height_values"),
            })

    specs.sort(key=lambda row: row["feature_id"])
    if len({row["feature_id"] for row in specs}) != len(specs):
        raise ValueError("duplicate World IR building feature IDs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(specs, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema": "y92.world_ir_building_specs.v1",
        "input_world_ir": str(osm_world_ir.resolve()),
        "terrain_metadata": str(terrain_metadata.resolve()),
        "output": str(output_path.resolve()),
        "counts": {
            "world_ir_buildings": len(specs),
            "specs_emitted": len(specs),
            "height_provenance": dict(sorted(height_models.items())),
        },
        "height_contract": {
            "explicit_height": "OSM height metres",
            "levels": f"building:levels × {_rules_storey:g} m",
            "storey_height_m": _rules_storey,
            "storey_source": _rules_source,
            "fallback_height_m": fallback_height_m,
            "fallback_source": _rules_source,
            "fallback_provenance": "synthesized",
            "ground_owner": "DEM terrain sampled by bake_osm_building_footprints.py",
        },
    }
    # CONFLATE-01: the no-flag receipt keeps its exact historical shape so
    # the BUILD-04 path is byte-identical; the new keys exist only with
    # --canonical-entities.
    if canonical_entities is not None:
        receipt["canonical_entities"] = str(Path(canonical_entities).resolve())
        receipt["counts"]["conflicting_heights"] = len(conflicting_entities)
        receipt["conflicting_heights"] = sorted(
            conflicting_entities, key=lambda row: row["feature_id"])
        receipt["counts"]["height_source"] = dict(sorted(height_sources.items()))
        receipt["synthesized_buildings"] = sorted(
            synthesized_ids, key=lambda row: row["feature_id"])
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv=None) -> int:
    # FL-4809 NUMABE-BUILD-11: the CLI header (this def, the parser and the
    # four required path flags) was dropped by commit 4233371bd, so the
    # module raised NameError at __main__ while the library path kept
    # working. CLI and library share compile_specs(); the CLI adds no
    # behaviour of its own, so CLI output is byte-identical to the lane's.
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--osm-world-ir", required=True, type=Path)
    ap.add_argument("--terrain-metadata", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--receipt", required=True, type=Path)
    ap.add_argument("--fallback-height-m", type=float, default=None,
                    help="override; default resolves from rules_resolved")
    ap.add_argument("--rules-path", type=Path, default=None,
                    help="rules_resolved.json carrying road_class_defaults "
                         "storey_height_m / default_building_height_m")
    ap.add_argument("--canonical-entities", type=Path, default=None,
                    help="canonical_entities.json; height claims replace the "
                         "ladder for matched entities")
    ap.add_argument("--locale-context", type=json.loads, default=None,
                    help="locale context JSON for the fallback chain")
    args = ap.parse_args(argv)
    print(json.dumps(compile_specs(
        args.osm_world_ir,
        args.terrain_metadata,
        args.output,
        args.receipt,
        args.fallback_height_m,
        locale_context=args.locale_context,
        canonical_entities=args.canonical_entities,
        rules_path=args.rules_path,
    ), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
