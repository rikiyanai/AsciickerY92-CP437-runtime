"""Y92 World IR - declared vocabulary, record builders, canonical serialization.

FL-4811.  This module is the single owner of the World IR vocabulary.  It sits
between *source evidence* and *generated runtime representations*:

    SourceObservation   ->  CanonicalFeature  ->  GeneratedRepresentation
    (what a source said)    (fused world truth)   (a lowered product reference)

Three hard rules are encoded here rather than in prose:

1.  The vocabulary is **source-independent and locale-independent**.  There is
    no Tokyo class, no Numabe class, no SBU class, no OSM class.  A street lamp
    is ``fixture`` whether lowering later picks an AKM mesh, a card, an XP file
    or an engine primitive.
2.  Provenance is **property-level**.  There is deliberately no object-level
    ``provenance`` field on a CanonicalFeature, because a lamp can have a
    triangulated (measured) position, a fused (derived) class and a
    synthesized appearance at the same time.  ``CanonicalFeature.properties``
    maps a property name to a claim that carries its own provenance.
3.  Representation choice is **not** storable as canonical truth.  Every field
    that names a lowered product lives on GeneratedRepresentation only, and
    ``validate.py`` rejects any CanonicalFeature carrying one.

Serialization is canonical JSON: sorted keys, no insignificant whitespace,
no NaN/Inf, ``-0.0`` normalized to ``0.0``.  load->save->load->save is
byte-identical.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# version
# --------------------------------------------------------------------------

SCHEMA_ID = "y92.world_ir"
SCHEMA_VERSION = "y92.world_ir/1.0.0"

# --------------------------------------------------------------------------
# record kinds - the three structurally separate shapes
# --------------------------------------------------------------------------

RECORD_SOURCE_OBSERVATION = "source_observation"
RECORD_CANONICAL_FEATURE = "canonical_feature"
RECORD_GENERATED_REPRESENTATION = "generated_representation"

RECORD_KINDS = frozenset(
    {
        RECORD_SOURCE_OBSERVATION,
        RECORD_CANONICAL_FEATURE,
        RECORD_GENERATED_REPRESENTATION,
    }
)

# Document collection name -> the record_kind it is allowed to hold.  A record
# in the wrong collection is a hard validation error: this is what stops a
# GeneratedRepresentation from being stored as canonical truth.
COLLECTION_RECORD_KIND = {
    "source_observations": RECORD_SOURCE_OBSERVATION,
    "canonical_features": RECORD_CANONICAL_FEATURE,
    "generated_representations": RECORD_GENERATED_REPRESENTATION,
}

# --------------------------------------------------------------------------
# semantic class registry - closed set; unknown class is a validation error
# --------------------------------------------------------------------------

SEMANTIC_CLASSES = frozenset(
    {
        "terrain",
        "road",
        "sidewalk",
        "curb",
        "tactile_paving",
        "road_marking",
        "building_footprint",
        "roof",
        "facade_plane",
        "facade_region",
        "door",
        "window",
        "sign",
        "fixture",
        "tree",
        "hedge",
        "shrub",
        "planter",
        "wall",
        "fence",
        "utility_pole",
        "utility_wire",
        "railway_boundary",
        "crossing",
        "station_entrance",
        # FL-4876 BRIDGE-DECK-01: a closed man_made=bridge outline (bridge
        # deck footprint). Routes with the ground-surface classes; layer=1
        # omits it from at-grade stamping while corridor rails reconstruct
        # above. Deck sides/parapets remain unmodeled (recorded residual).
        "bridge_deck",
        # A named or categorized site such as a shop, clinic, station service,
        # or place of worship.  The place is semantic world truth; signs,
        # storefront cards, gates, and other visible products remain separate
        # observations/representations so a POI cannot synthesize them merely
        # by existing in a vector source.
        "place",
        "spawn",
        "world_bounds",
        # A solved capture position and orientation. Added by FL-4812, which
        # first emitted camera poses as ``spawn`` to avoid widening a closed
        # registry. That was wrong: ``spawn`` carries gameplay meaning, so a
        # consumer asking for spawn points would have received 22 camera
        # stations. A camera station is world truth about where an observation
        # was made -- it is not a player spawn and it is not a fixture -- so it
        # gets its own class rather than borrowing a gameplay one.
        "camera_station",
    }
)

# Classes whose truth is inherently geometric.  A feature in one of these must
# carry a geometry claim with an uncertainty model.
NON_GEOMETRIC_CLASSES = frozenset()

# --------------------------------------------------------------------------
# provenance - exactly three values, applied per property
# --------------------------------------------------------------------------

PROVENANCE_MEASURED = "measured"
PROVENANCE_DERIVED = "derived"
PROVENANCE_SYNTHESIZED = "synthesized"

PROVENANCE_CLASSES = (PROVENANCE_MEASURED, PROVENANCE_DERIVED, PROVENANCE_SYNTHESIZED)

# A claim in one of these classes asserts a source fact and must name at least
# one supporting SourceObservation.  ``synthesized`` may name zero.
PROVENANCE_REQUIRING_OBSERVATION = frozenset({PROVENANCE_MEASURED, PROVENANCE_DERIVED})

# --------------------------------------------------------------------------
# gameplay authority - explicit, ranked, never silently upgraded
# --------------------------------------------------------------------------

AUTHORITY_NONE = "none"
AUTHORITY_PRESENTATION_ONLY = "presentation_only"
AUTHORITY_WALKABILITY = "walkability"
AUTHORITY_COLLISION = "collision"

GAMEPLAY_AUTHORITIES = (
    AUTHORITY_NONE,
    AUTHORITY_PRESENTATION_ONLY,
    AUTHORITY_WALKABILITY,
    AUTHORITY_COLLISION,
)

AUTHORITY_RANK = {
    AUTHORITY_NONE: 0,
    AUTHORITY_PRESENTATION_ONLY: 1,
    AUTHORITY_WALKABILITY: 2,
    AUTHORITY_COLLISION: 3,
}

# Authority classes that change where the player can stand or what blocks them.
# Synthesized appearance must never become one of these on its own.
PHYSICAL_AUTHORITIES = frozenset({AUTHORITY_WALKABILITY, AUTHORITY_COLLISION})

# --------------------------------------------------------------------------
# conflict state - conflicts are preserved, never overwritten
# --------------------------------------------------------------------------

CONFLICT_NONE = "none"
CONFLICT_UNRESOLVED = "unresolved"
CONFLICT_RESOLVED_BY_REVIEW = "resolved_by_review"

CONFLICT_STATES = (CONFLICT_NONE, CONFLICT_UNRESOLVED, CONFLICT_RESOLVED_BY_REVIEW)

# --------------------------------------------------------------------------
# geometry + coordinate frame
# --------------------------------------------------------------------------

GEOMETRY_NONE = "none"

GEOMETRY_KINDS = frozenset(
    {
        GEOMETRY_NONE,
        "point",
        "polyline",
        "polygon",
        "polygon_with_holes",
        "prism",
        "plane",
        "field_samples",
    }
)

GEOMETRIC_KINDS = frozenset(GEOMETRY_KINDS - {GEOMETRY_NONE})

COORDINATE_FRAME_REQUIRED = ("frame_id", "crs", "units")
COORDINATE_FRAME_FIELDS = frozenset(
    {"frame_id", "crs", "units", "axis_order", "vertical_datum", "note"}
)

# --------------------------------------------------------------------------
# uncertainty models
# --------------------------------------------------------------------------

UNCERTAINTY_MODELS = {
    "isotropic_sigma": ("sigma", "units"),
    "covariance": ("matrix", "units"),
    "bounds": ("low", "high", "units"),
    "ordinal": ("levels", "level"),
    "categorical_probability": ("probabilities",),
}

# --------------------------------------------------------------------------
# source kinds
# --------------------------------------------------------------------------

SOURCE_KINDS = frozenset(
    {
        "vector_map",
        "point_cloud",
        "imagery",
        "survey",
        "operator_review",
        "procedural",
        "runtime_probe",
        "document",
    }
)

# --------------------------------------------------------------------------
# presentation requirements - what the world demands of ANY representation.
# Never which representation.  Keys are a closed registry.
# --------------------------------------------------------------------------

LEGIBILITY_CLASSES = frozenset(
    {"background", "secondary", "landmark", "navigational_cue"}
)

PRESENTATION_REQUIREMENT_FIELDS = {
    "visible": bool,
    "silhouette_required": bool,
    "must_not_imply_collision": bool,
    "legibility_class": str,
    "notes": str,
}

# --------------------------------------------------------------------------
# representation leakage guard
# --------------------------------------------------------------------------

# Fields that may appear on a GeneratedRepresentation only.  Finding any of
# these anywhere inside a CanonicalFeature means a representation choice is
# being stored as world truth.
REPRESENTATION_ONLY_KEYS = frozenset(
    {
        "representation_id",
        "target",
        "product",
        "lowering",
        "authority_claim",
        "mesh",
        "mesh_name",
        "material_id",
        "texture",
        "sprite",
        "glyph",
        "glyphs",
        "shader",
        "lod",
        "prefab",
        "atlas",
        "asset_ref",
        "akm",
        "card_id",
        "xp_file",
        "transform",
        "bake_material_id",
    }
)

CANONICAL_FEATURE_REQUIRED = (
    "record_kind",
    "feature_id",
    "semantic_class",
    "geometry",
    "properties",
    "supporting_observation_ids",
    "confidence",
    "conflict",
    "gameplay_authority",
    "presentation_requirements",
)

CANONICAL_FEATURE_FIELDS = frozenset(
    CANONICAL_FEATURE_REQUIRED
    + ("parent_feature_id", "labels", "authority_supporting_properties")
)

SOURCE_OBSERVATION_REQUIRED = (
    "record_kind",
    "observation_id",
    "source_id",
    "source_kind",
    "observed_at",
    "attributes",
)

SOURCE_OBSERVATION_FIELDS = frozenset(
    SOURCE_OBSERVATION_REQUIRED + ("geometry", "quality", "digest")
)

GENERATED_REPRESENTATION_REQUIRED = (
    "record_kind",
    "representation_id",
    "feature_id",
    "target",
    "product",
    "lowering",
    "authority_claim",
)

GENERATED_REPRESENTATION_FIELDS = frozenset(
    GENERATED_REPRESENTATION_REQUIRED + ("generated_at", "notes")
)

DOCUMENT_REQUIRED = (
    "schema_version",
    "ir_id",
    "coordinate_frames",
    "source_observations",
    "canonical_features",
    "generated_representations",
)

DOCUMENT_FIELDS = frozenset(DOCUMENT_REQUIRED + ("generated_at", "notes"))


# ==========================================================================
# builders
# ==========================================================================


def coordinate_frame(
    frame_id: str,
    crs: str,
    units: str,
    axis_order: Sequence[str] | None = None,
    vertical_datum: str | None = None,
    note: str | None = None,
) -> dict:
    """Declare a coordinate frame.

    ``crs`` is an opaque identifier: an authority code (``EPSG:32618``) or a
    project-local frame (``local:a3d_world``).  The IR does not privilege
    either, which is what keeps it locale-independent.
    """
    frame: dict[str, Any] = {"frame_id": frame_id, "crs": crs, "units": units}
    if axis_order is not None:
        frame["axis_order"] = list(axis_order)
    if vertical_datum is not None:
        frame["vertical_datum"] = vertical_datum
    if note is not None:
        frame["note"] = note
    return frame


def geometry_none() -> dict:
    return {"kind": GEOMETRY_NONE}


def geometry_point(frame_id: str, coordinates: Sequence[float]) -> dict:
    return {"kind": "point", "frame_id": frame_id, "coordinates": [float(c) for c in coordinates]}


def geometry_polyline(frame_id: str, coordinates: Sequence[Sequence[float]]) -> dict:
    return {
        "kind": "polyline",
        "frame_id": frame_id,
        "coordinates": [[float(c) for c in pt] for pt in coordinates],
    }


def geometry_polygon(frame_id: str, ring: Sequence[Sequence[float]]) -> dict:
    return {
        "kind": "polygon",
        "frame_id": frame_id,
        "coordinates": [[float(c) for c in pt] for pt in ring],
    }


def geometry_polygon_with_holes(
    frame_id: str,
    outer: Sequence[Sequence[float]],
    holes: Sequence[Sequence[Sequence[float]]] = (),
) -> dict:
    return {
        "kind": "polygon_with_holes",
        "frame_id": frame_id,
        "outer": [[float(c) for c in pt] for pt in outer],
        "holes": [[[float(c) for c in pt] for pt in ring] for ring in holes],
    }


def geometry_prism(
    frame_id: str,
    base: Sequence[Sequence[float]],
    base_z: float,
    top_z: float,
) -> dict:
    return {
        "kind": "prism",
        "frame_id": frame_id,
        "base": [[float(c) for c in pt] for pt in base],
        "base_z": float(base_z),
        "top_z": float(top_z),
    }


def geometry_field_samples(frame_id: str, samples: Sequence[Mapping[str, Any]], lookup: str) -> dict:
    """A sampled scalar field: the shape a LiDAR-derived roof lattice takes."""
    return {
        "kind": "field_samples",
        "frame_id": frame_id,
        "samples": [dict(s) for s in samples],
        "lookup": lookup,
    }


def uncertainty_isotropic(sigma: float, units: str) -> dict:
    return {"model": "isotropic_sigma", "sigma": float(sigma), "units": units}


def uncertainty_covariance(matrix: Sequence[Sequence[float]], units: str) -> dict:
    return {
        "model": "covariance",
        "matrix": [[float(v) for v in row] for row in matrix],
        "units": units,
    }


def uncertainty_bounds(low: float, high: float, units: str) -> dict:
    return {"model": "bounds", "low": float(low), "high": float(high), "units": units}


def uncertainty_ordinal(levels: Sequence[str], level: str) -> dict:
    return {"model": "ordinal", "levels": list(levels), "level": level}


def uncertainty_categorical(probabilities: Mapping[str, float]) -> dict:
    return {
        "model": "categorical_probability",
        "probabilities": {str(k): float(v) for k, v in probabilities.items()},
    }


def property_claim(
    value: Any,
    provenance: str,
    method: str,
    observation_ids: Iterable[str] = (),
    confidence: float | None = None,
    uncertainty: Mapping[str, Any] | None = None,
    alternatives: Iterable[Mapping[str, Any]] = (),
) -> dict:
    """One property of one feature, with its own provenance.

    This is the unit that makes provenance property-level: a feature whose
    position is ``measured``, whose class is ``derived`` and whose appearance
    requirement is ``synthesized`` is three claims, not one object label.
    """
    claim: dict[str, Any] = {
        "value": value,
        "provenance": provenance,
        "method": method,
        "observation_ids": list(observation_ids),
    }
    if confidence is not None:
        claim["confidence"] = float(confidence)
    if uncertainty is not None:
        claim["uncertainty"] = dict(uncertainty)
    alts = [dict(a) for a in alternatives]
    if alts:
        claim["alternatives"] = alts
    return claim


def contradiction(property_name: str, observation_ids: Sequence[str], values: Sequence[Any]) -> dict:
    """A preserved disagreement between sources about one property."""
    return {
        "property": property_name,
        "observation_ids": list(observation_ids),
        "values": list(values),
    }


def conflict_none() -> dict:
    return {"state": CONFLICT_NONE, "contradictions": [], "resolution": None}


def conflict_unresolved(contradictions: Sequence[Mapping[str, Any]]) -> dict:
    return {
        "state": CONFLICT_UNRESOLVED,
        "contradictions": [dict(c) for c in contradictions],
        "resolution": None,
    }


def conflict_resolved(
    contradictions: Sequence[Mapping[str, Any]],
    reviewer: str,
    decided_at: str,
    rationale: str,
    chosen: Mapping[str, Any],
) -> dict:
    """Resolution ADDS a record; the contradictions stay in the document."""
    return {
        "state": CONFLICT_RESOLVED_BY_REVIEW,
        "contradictions": [dict(c) for c in contradictions],
        "resolution": {
            "reviewer": reviewer,
            "decided_at": decided_at,
            "rationale": rationale,
            "chosen": dict(chosen),
        },
    }


def presentation_requirements(
    visible: bool = True,
    silhouette_required: bool = False,
    must_not_imply_collision: bool = False,
    legibility_class: str = "secondary",
    notes: str | None = None,
) -> dict:
    req: dict[str, Any] = {
        "visible": bool(visible),
        "silhouette_required": bool(silhouette_required),
        "must_not_imply_collision": bool(must_not_imply_collision),
        "legibility_class": legibility_class,
    }
    if notes is not None:
        req["notes"] = notes
    return req


def source_observation(
    observation_id: str,
    source_id: str,
    source_kind: str,
    observed_at: str,
    attributes: Mapping[str, Any],
    geometry: Mapping[str, Any] | None = None,
    quality: Mapping[str, Any] | None = None,
    digest: str | None = None,
) -> dict:
    """Evidence: what one source said, unfused and unedited."""
    rec: dict[str, Any] = {
        "record_kind": RECORD_SOURCE_OBSERVATION,
        "observation_id": observation_id,
        "source_id": source_id,
        "source_kind": source_kind,
        "observed_at": observed_at,
        "attributes": dict(attributes),
    }
    if geometry is not None:
        rec["geometry"] = dict(geometry)
    if quality is not None:
        rec["quality"] = dict(quality)
    if digest is not None:
        rec["digest"] = digest
    return rec


def canonical_feature(
    feature_id: str,
    semantic_class: str,
    geometry: Mapping[str, Any],
    properties: Mapping[str, Mapping[str, Any]],
    confidence: float,
    gameplay_authority: str,
    presentation: Mapping[str, Any] | None = None,
    conflict: Mapping[str, Any] | None = None,
    supporting_observation_ids: Iterable[str] | None = None,
    parent_feature_id: str | None = None,
    labels: Mapping[str, str] | None = None,
    authority_supporting_properties: Sequence[str] | None = None,
) -> dict:
    """Fused world truth.  Carries no representation choice, by construction.

    ``supporting_observation_ids`` defaults to the sorted union of every
    claim's observation ids, so the feature can always be traced back to
    evidence without the caller restating it.
    """
    props = {str(k): dict(v) for k, v in properties.items()}
    if supporting_observation_ids is None:
        union: set[str] = set()
        for claim in props.values():
            union.update(claim.get("observation_ids") or ())
        supporting = sorted(union)
    else:
        supporting = sorted(set(supporting_observation_ids))
    rec: dict[str, Any] = {
        "record_kind": RECORD_CANONICAL_FEATURE,
        "feature_id": feature_id,
        "semantic_class": semantic_class,
        "geometry": dict(geometry),
        "properties": props,
        "supporting_observation_ids": supporting,
        "confidence": float(confidence),
        "conflict": dict(conflict) if conflict is not None else conflict_none(),
        "gameplay_authority": gameplay_authority,
        "presentation_requirements": dict(presentation)
        if presentation is not None
        else presentation_requirements(),
    }
    if parent_feature_id is not None:
        rec["parent_feature_id"] = parent_feature_id
    if labels:
        rec["labels"] = {str(k): str(v) for k, v in labels.items()}
    if authority_supporting_properties is not None:
        rec["authority_supporting_properties"] = list(authority_supporting_properties)
    return rec


def generated_representation(
    representation_id: str,
    feature_id: str,
    target: str,
    product: Mapping[str, Any],
    lowering: Mapping[str, Any],
    authority_claim: str,
    generated_at: str | None = None,
    notes: str | None = None,
) -> dict:
    """A reference to one lowered product for one canonical feature.

    ``target`` is an opaque lowering destination token (``a3d``, ``godot``,
    ``card``, ``primitive``).  The IR does not enumerate engines, so adding a
    target never edits this file.
    """
    rec: dict[str, Any] = {
        "record_kind": RECORD_GENERATED_REPRESENTATION,
        "representation_id": representation_id,
        "feature_id": feature_id,
        "target": target,
        "product": dict(product),
        "lowering": dict(lowering),
        "authority_claim": authority_claim,
    }
    if generated_at is not None:
        rec["generated_at"] = generated_at
    if notes is not None:
        rec["notes"] = notes
    return rec


def document(
    ir_id: str,
    coordinate_frames: Iterable[Mapping[str, Any]] = (),
    source_observations: Iterable[Mapping[str, Any]] = (),
    canonical_features: Iterable[Mapping[str, Any]] = (),
    generated_representations: Iterable[Mapping[str, Any]] = (),
    generated_at: str | None = None,
    notes: str | None = None,
) -> dict:
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ir_id": ir_id,
        "coordinate_frames": {f["frame_id"]: dict(f) for f in coordinate_frames},
        "source_observations": [dict(r) for r in source_observations],
        "canonical_features": [dict(r) for r in canonical_features],
        "generated_representations": [dict(r) for r in generated_representations],
    }
    if generated_at is not None:
        doc["generated_at"] = generated_at
    if notes is not None:
        doc["notes"] = notes
    return doc


# ==========================================================================
# canonical serialization
# ==========================================================================


class CanonicalizationError(ValueError):
    """A value cannot be serialized deterministically."""


def _normalize(value: Any, path: str = "$") -> Any:
    """Make a value canonical: no NaN/Inf, no -0.0, no tuples, no int keys."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalizationError(f"{path}: non-finite float {value!r}")
        # -0.0 and 0.0 must not produce different bytes.
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, sub in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path}: non-string key {key!r}")
            out[key] = _normalize(sub, f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize(v, f"{path}[{i}]") for i, v in enumerate(value)]
    raise CanonicalizationError(f"{path}: unserializable type {type(value).__name__}")


def canonical_dumps(obj: Any) -> str:
    """Deterministic JSON text: sorted keys, tight separators, trailing newline."""
    return (
        json.dumps(
            _normalize(obj),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def canonical_bytes(obj: Any) -> bytes:
    return canonical_dumps(obj).encode("utf-8")


def loads(text: str) -> Any:
    return json.loads(text)


def json_schema() -> dict:
    """A JSON Schema covering the *structural* half of the contract.

    The relational rules - provenance needing an observation, authority not
    exceeding evidence, conflicts needing a resolution - are cross-record and
    are not expressible here; ``validate.py`` owns all of them and never needs
    this document or the optional ``jsonschema`` package to run.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://asciicker.invalid/{SCHEMA_VERSION}",
        "title": "Y92 World IR",
        "type": "object",
        "required": list(DOCUMENT_REQUIRED),
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "ir_id": {"type": "string", "minLength": 1},
            "generated_at": {"type": "string"},
            "notes": {"type": "string"},
            "coordinate_frames": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": list(COORDINATE_FRAME_REQUIRED),
                    "additionalProperties": False,
                    "properties": {
                        "frame_id": {"type": "string", "minLength": 1},
                        "crs": {"type": "string", "minLength": 1},
                        "units": {"type": "string", "minLength": 1},
                        "axis_order": {"type": "array", "items": {"type": "string"}},
                        "vertical_datum": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            },
            "source_observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": list(SOURCE_OBSERVATION_REQUIRED),
                    "properties": {
                        "record_kind": {"const": RECORD_SOURCE_OBSERVATION},
                        "observation_id": {"type": "string", "minLength": 1},
                        "source_id": {"type": "string", "minLength": 1},
                        "source_kind": {"enum": sorted(SOURCE_KINDS)},
                        "observed_at": {"type": "string"},
                        "attributes": {"type": "object"},
                    },
                },
            },
            "canonical_features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": list(CANONICAL_FEATURE_REQUIRED),
                    "properties": {
                        "record_kind": {"const": RECORD_CANONICAL_FEATURE},
                        "feature_id": {"type": "string", "minLength": 1},
                        "semantic_class": {"enum": sorted(SEMANTIC_CLASSES)},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "gameplay_authority": {"enum": list(GAMEPLAY_AUTHORITIES)},
                        "supporting_observation_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "properties": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "required": ["value", "provenance", "method", "observation_ids"],
                                "properties": {
                                    "provenance": {"enum": list(PROVENANCE_CLASSES)},
                                    "method": {"type": "string"},
                                    "observation_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "conflict": {
                            "type": "object",
                            "required": ["state", "contradictions", "resolution"],
                            "properties": {"state": {"enum": list(CONFLICT_STATES)}},
                        },
                        "geometry": {
                            "type": "object",
                            "required": ["kind"],
                            "properties": {"kind": {"enum": sorted(GEOMETRY_KINDS)}},
                        },
                    },
                },
            },
            "generated_representations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": list(GENERATED_REPRESENTATION_REQUIRED),
                    "properties": {
                        "record_kind": {"const": RECORD_GENERATED_REPRESENTATION},
                        "representation_id": {"type": "string", "minLength": 1},
                        "feature_id": {"type": "string", "minLength": 1},
                        "target": {"type": "string", "minLength": 1},
                        "authority_claim": {"enum": list(GAMEPLAY_AUTHORITIES)},
                    },
                },
            },
        },
    }
