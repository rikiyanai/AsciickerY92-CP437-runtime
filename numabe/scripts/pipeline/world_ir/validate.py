"""Y92 World IR validator - stdlib only, no ML inference, no optional deps.

FL-4811.  The rules enforced here are the ones that make the IR a *canonical
owner* rather than a bag of dictionaries.  Every rule below has a negative
test in ``scripts/tests/test_fl4811_world_ir_schema.py`` that proves it fires.

Deliberately stdlib-only: ``import jsonschema`` happens to succeed in this
repo, but validation of world truth must never depend on an optional package
or on a model being loadable.  ``schema.json_schema()` publishes the
structural half for outside consumers; the relational half - provenance
needing evidence, authority not exceeding evidence, conflicts needing a
recorded resolution - is cross-record and lives only here.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import schema as S

# --------------------------------------------------------------------------
# error codes
# --------------------------------------------------------------------------

E_SCHEMA_VERSION = "E_SCHEMA_VERSION"
E_MISSING_FIELD = "E_MISSING_FIELD"
E_UNKNOWN_FIELD = "E_UNKNOWN_FIELD"
E_BAD_TYPE = "E_BAD_TYPE"
E_WRONG_RECORD_KIND = "E_WRONG_RECORD_KIND"
E_REPRESENTATION_AS_CANONICAL = "E_REPRESENTATION_AS_CANONICAL"
E_UNKNOWN_SEMANTIC_CLASS = "E_UNKNOWN_SEMANTIC_CLASS"
E_DUPLICATE_ID = "E_DUPLICATE_ID"
E_UNKNOWN_PROVENANCE = "E_UNKNOWN_PROVENANCE"
E_MEASURED_WITHOUT_OBSERVATION = "E_MEASURED_WITHOUT_OBSERVATION"
E_UNKNOWN_OBSERVATION_REF = "E_UNKNOWN_OBSERVATION_REF"
E_UNSUPPORTED_OBSERVATION_ID = "E_UNSUPPORTED_OBSERVATION_ID"
E_MISSING_UNCERTAINTY = "E_MISSING_UNCERTAINTY"
E_BAD_UNCERTAINTY = "E_BAD_UNCERTAINTY"
E_UNKNOWN_AUTHORITY = "E_UNKNOWN_AUTHORITY"
E_SYNTHESIZED_AUTHORITY = "E_SYNTHESIZED_AUTHORITY"
E_UNKNOWN_CONFLICT_STATE = "E_UNKNOWN_CONFLICT_STATE"
E_RESOLVED_WITHOUT_RESOLUTION = "E_RESOLVED_WITHOUT_RESOLUTION"
E_CONFLICT_EVIDENCE_LOST = "E_CONFLICT_EVIDENCE_LOST"
E_UNEXPECTED_RESOLUTION = "E_UNEXPECTED_RESOLUTION"
E_BAD_GEOMETRY = "E_BAD_GEOMETRY"
E_UNKNOWN_FRAME = "E_UNKNOWN_FRAME"
E_BAD_CONFIDENCE = "E_BAD_CONFIDENCE"
E_UNKNOWN_SOURCE_KIND = "E_UNKNOWN_SOURCE_KIND"
E_DANGLING_FEATURE_REF = "E_DANGLING_FEATURE_REF"
E_AUTHORITY_ESCALATION = "E_AUTHORITY_ESCALATION"
E_BAD_PRESENTATION = "E_BAD_PRESENTATION"
E_MISSING_GEOMETRY_CLAIM = "E_MISSING_GEOMETRY_CLAIM"
E_PRESENTATION_IMPLIES_COLLISION = "E_PRESENTATION_IMPLIES_COLLISION"

GEOMETRY_CLAIM = "geometry"


class Finding:
    """One validation failure: a code, the path that failed, and why."""

    __slots__ = ("code", "path", "message")

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Finding({self.code}, {self.path!r}, {self.message!r})"

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"

    def as_dict(self) -> dict:
        return {"code": self.code, "path": self.path, "message": self.message}


class WorldIRValidationError(ValueError):
    def __init__(self, findings: Sequence[Finding]) -> None:
        self.findings = list(findings)
        joined = "\n  ".join(str(f) for f in self.findings)
        super().__init__(f"{len(self.findings)} World IR validation finding(s):\n  {joined}")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_fields(
    rec: Mapping[str, Any],
    required: Iterable[str],
    allowed: frozenset[str],
    path: str,
    out: list[Finding],
) -> bool:
    ok = True
    for field in required:
        if field not in rec:
            out.append(Finding(E_MISSING_FIELD, path, f"required field {field!r} is absent"))
            ok = False
    for field in rec:
        if field not in allowed:
            out.append(Finding(E_UNKNOWN_FIELD, f"{path}.{field}", "field is not in the schema"))
            ok = False
    return ok


def _walk_keys(value: Any, path: str):
    """Yield (key, path) for every mapping key anywhere under ``value``."""
    if isinstance(value, Mapping):
        for key, sub in value.items():
            here = f"{path}.{key}"
            yield str(key), here
            yield from _walk_keys(sub, here)
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            yield from _walk_keys(sub, f"{path}[{i}]")


# --------------------------------------------------------------------------
# component validators
# --------------------------------------------------------------------------


def _validate_frame(frame: Any, frame_id: str, path: str, out: list[Finding]) -> None:
    if not isinstance(frame, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "coordinate frame must be an object"))
        return
    _require_fields(frame, S.COORDINATE_FRAME_REQUIRED, S.COORDINATE_FRAME_FIELDS, path, out)
    for field in ("frame_id", "crs", "units"):
        value = frame.get(field)
        if field in frame and (not isinstance(value, str) or not value.strip()):
            out.append(Finding(E_BAD_TYPE, f"{path}.{field}", "must be a non-empty string"))
    if frame.get("frame_id") not in (None, frame_id):
        out.append(
            Finding(
                E_BAD_TYPE,
                f"{path}.frame_id",
                f"frame_id {frame.get('frame_id')!r} does not match its key {frame_id!r}",
            )
        )


def _validate_ring(ring: Any, min_points: int, path: str, out: list[Finding]) -> None:
    if not isinstance(ring, list) or len(ring) < min_points:
        out.append(
            Finding(E_BAD_GEOMETRY, path, f"expected a list of at least {min_points} points")
        )
        return
    for i, pt in enumerate(ring):
        if not isinstance(pt, list) or not (2 <= len(pt) <= 3) or not all(_is_num(c) for c in pt):
            out.append(
                Finding(E_BAD_GEOMETRY, f"{path}[{i}]", "point must be 2 or 3 finite numbers")
            )
            return


def _validate_geometry(
    geom: Any, frames: Mapping[str, Any], path: str, out: list[Finding]
) -> str | None:
    """Return the geometry kind, or None when the geometry is unusable."""
    if not isinstance(geom, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "geometry must be an object"))
        return None
    kind = geom.get("kind")
    if kind not in S.GEOMETRY_KINDS:
        out.append(Finding(E_BAD_GEOMETRY, f"{path}.kind", f"unknown geometry kind {kind!r}"))
        return None
    if kind == S.GEOMETRY_NONE:
        return kind

    frame_id = geom.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id:
        out.append(
            Finding(E_UNKNOWN_FRAME, f"{path}.frame_id", "geometric record needs a frame_id")
        )
    elif frame_id not in frames:
        out.append(
            Finding(
                E_UNKNOWN_FRAME,
                f"{path}.frame_id",
                f"frame {frame_id!r} is not declared in coordinate_frames",
            )
        )

    if kind == "point":
        coords = geom.get("coordinates")
        if (
            not isinstance(coords, list)
            or not (2 <= len(coords) <= 3)
            or not all(_is_num(c) for c in coords)
        ):
            out.append(
                Finding(E_BAD_GEOMETRY, f"{path}.coordinates", "point needs 2 or 3 numbers")
            )
    elif kind == "polyline":
        _validate_ring(geom.get("coordinates"), 2, f"{path}.coordinates", out)
    elif kind == "polygon":
        _validate_ring(geom.get("coordinates"), 3, f"{path}.coordinates", out)
    elif kind == "polygon_with_holes":
        _validate_ring(geom.get("outer"), 3, f"{path}.outer", out)
        holes = geom.get("holes", [])
        if not isinstance(holes, list):
            out.append(Finding(E_BAD_GEOMETRY, f"{path}.holes", "holes must be a list of rings"))
        else:
            for i, ring in enumerate(holes):
                _validate_ring(ring, 3, f"{path}.holes[{i}]", out)
    elif kind == "prism":
        _validate_ring(geom.get("base"), 3, f"{path}.base", out)
        for field in ("base_z", "top_z"):
            if not _is_num(geom.get(field)):
                out.append(Finding(E_BAD_GEOMETRY, f"{path}.{field}", "must be a number"))
    elif kind == "plane":
        for field in ("origin", "normal"):
            value = geom.get(field)
            if not isinstance(value, list) or len(value) != 3 or not all(_is_num(c) for c in value):
                out.append(Finding(E_BAD_GEOMETRY, f"{path}.{field}", "must be 3 numbers"))
    elif kind == "field_samples":
        samples = geom.get("samples")
        if not isinstance(samples, list) or not samples:
            out.append(Finding(E_BAD_GEOMETRY, f"{path}.samples", "needs at least one sample"))
        elif not all(isinstance(s, Mapping) for s in samples):
            out.append(Finding(E_BAD_GEOMETRY, f"{path}.samples", "samples must be objects"))
        if not isinstance(geom.get("lookup"), str):
            out.append(
                Finding(E_BAD_GEOMETRY, f"{path}.lookup", "field_samples needs a lookup rule")
            )
    return kind


def _validate_uncertainty(unc: Any, path: str, out: list[Finding]) -> None:
    if not isinstance(unc, Mapping):
        out.append(Finding(E_BAD_UNCERTAINTY, path, "uncertainty must be an object"))
        return
    model = unc.get("model")
    if model not in S.UNCERTAINTY_MODELS:
        out.append(Finding(E_BAD_UNCERTAINTY, f"{path}.model", f"unknown model {model!r}"))
        return
    for field in S.UNCERTAINTY_MODELS[model]:
        if field not in unc:
            out.append(
                Finding(E_BAD_UNCERTAINTY, path, f"model {model!r} requires field {field!r}")
            )
            return
    if model == "isotropic_sigma":
        sigma = unc["sigma"]
        if not _is_num(sigma) or sigma < 0:
            out.append(
                Finding(E_BAD_UNCERTAINTY, f"{path}.sigma", "sigma must be a non-negative number")
            )
    elif model == "covariance":
        matrix = unc["matrix"]
        if not isinstance(matrix, list) or not matrix:
            out.append(Finding(E_BAD_UNCERTAINTY, f"{path}.matrix", "matrix must be non-empty"))
            return
        n = len(matrix)
        for i, row in enumerate(matrix):
            if not isinstance(row, list) or len(row) != n or not all(_is_num(v) for v in row):
                out.append(
                    Finding(E_BAD_UNCERTAINTY, f"{path}.matrix[{i}]", "matrix must be square")
                )
                return
        for i in range(n):
            if matrix[i][i] < 0:
                out.append(
                    Finding(
                        E_BAD_UNCERTAINTY,
                        f"{path}.matrix[{i}][{i}]",
                        "covariance diagonal must be non-negative",
                    )
                )
            for j in range(i + 1, n):
                if abs(matrix[i][j] - matrix[j][i]) > 1e-9:
                    out.append(
                        Finding(
                            E_BAD_UNCERTAINTY,
                            f"{path}.matrix[{i}][{j}]",
                            "covariance matrix must be symmetric",
                        )
                    )
    elif model == "bounds":
        low, high = unc["low"], unc["high"]
        if not _is_num(low) or not _is_num(high) or low > high:
            out.append(Finding(E_BAD_UNCERTAINTY, path, "bounds need numbers with low <= high"))
    elif model == "ordinal":
        levels, level = unc["levels"], unc["level"]
        if not isinstance(levels, list) or not levels or level not in levels:
            out.append(Finding(E_BAD_UNCERTAINTY, path, "level must be one of the declared levels"))
    elif model == "categorical_probability":
        probs = unc["probabilities"]
        if not isinstance(probs, Mapping) or not probs:
            out.append(Finding(E_BAD_UNCERTAINTY, f"{path}.probabilities", "must be non-empty"))
            return
        if not all(_is_num(v) and 0.0 <= v <= 1.0 for v in probs.values()):
            out.append(
                Finding(E_BAD_UNCERTAINTY, f"{path}.probabilities", "values must be in [0,1]")
            )
        elif abs(sum(probs.values()) - 1.0) > 1e-6:
            out.append(
                Finding(E_BAD_UNCERTAINTY, f"{path}.probabilities", "values must sum to 1.0")
            )


def _validate_claim(
    name: str,
    claim: Any,
    observation_ids: frozenset[str],
    path: str,
    out: list[Finding],
) -> str | None:
    """Validate one property claim; return its provenance class when usable."""
    if not isinstance(claim, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "property claim must be an object"))
        return None
    for field in ("value", "provenance", "method", "observation_ids"):
        if field not in claim:
            out.append(Finding(E_MISSING_FIELD, path, f"claim requires field {field!r}"))
    prov = claim.get("provenance")
    if prov not in S.PROVENANCE_CLASSES:
        out.append(
            Finding(
                E_UNKNOWN_PROVENANCE,
                f"{path}.provenance",
                f"provenance must be one of {S.PROVENANCE_CLASSES}, got {prov!r}",
            )
        )
        prov = None
    obs = claim.get("observation_ids")
    if not isinstance(obs, list) or not all(isinstance(o, str) for o in obs):
        out.append(Finding(E_BAD_TYPE, f"{path}.observation_ids", "must be a list of strings"))
        obs = []
    for oid in obs:
        if oid not in observation_ids:
            out.append(
                Finding(
                    E_UNKNOWN_OBSERVATION_REF,
                    f"{path}.observation_ids",
                    f"observation {oid!r} is not in source_observations",
                )
            )
    # Property-level provenance rule: a claim that asserts a source fact must
    # name the evidence it came from.  ``synthesized`` may name none.
    if prov in S.PROVENANCE_REQUIRING_OBSERVATION and not obs:
        out.append(
            Finding(
                E_MEASURED_WITHOUT_OBSERVATION,
                path,
                f"property {name!r} claims {prov!r} but names no supporting observation",
            )
        )
    if not isinstance(claim.get("method"), str) or not str(claim.get("method")).strip():
        out.append(Finding(E_BAD_TYPE, f"{path}.method", "method must be a non-empty string"))
    if "confidence" in claim:
        conf = claim["confidence"]
        if not _is_num(conf) or not (0.0 <= conf <= 1.0):
            out.append(Finding(E_BAD_CONFIDENCE, f"{path}.confidence", "must be in [0,1]"))
    if "uncertainty" in claim:
        _validate_uncertainty(claim["uncertainty"], f"{path}.uncertainty", out)
    if "alternatives" in claim:
        alts = claim["alternatives"]
        if not isinstance(alts, list):
            out.append(Finding(E_BAD_TYPE, f"{path}.alternatives", "must be a list"))
        else:
            for i, alt in enumerate(alts):
                if not isinstance(alt, Mapping) or "value" not in alt:
                    out.append(
                        Finding(
                            E_BAD_TYPE,
                            f"{path}.alternatives[{i}]",
                            "an alternative needs at least a value",
                        )
                    )
    return prov


def _validate_conflict(conflict: Any, path: str, out: list[Finding]) -> None:
    if not isinstance(conflict, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "conflict must be an object"))
        return
    state = conflict.get("state")
    if state not in S.CONFLICT_STATES:
        out.append(
            Finding(E_UNKNOWN_CONFLICT_STATE, f"{path}.state", f"unknown conflict state {state!r}")
        )
        return
    contradictions = conflict.get("contradictions")
    if not isinstance(contradictions, list):
        out.append(Finding(E_BAD_TYPE, f"{path}.contradictions", "must be a list"))
        return
    resolution = conflict.get("resolution", None)

    for i, item in enumerate(contradictions):
        here = f"{path}.contradictions[{i}]"
        if not isinstance(item, Mapping):
            out.append(Finding(E_BAD_TYPE, here, "contradiction must be an object"))
            continue
        ids = item.get("observation_ids")
        values = item.get("values")
        if not isinstance(item.get("property"), str):
            out.append(Finding(E_BAD_TYPE, f"{here}.property", "must be a string"))
        if not isinstance(ids, list) or len(ids) < 2:
            out.append(
                Finding(
                    E_BAD_TYPE,
                    f"{here}.observation_ids",
                    "a contradiction needs at least two disagreeing observations",
                )
            )
        if not isinstance(values, list) or len(values) < 2:
            out.append(
                Finding(
                    E_BAD_TYPE,
                    f"{here}.values",
                    "a contradiction must record the disagreeing values",
                )
            )

    if state == S.CONFLICT_NONE:
        if contradictions:
            out.append(
                Finding(
                    E_UNKNOWN_CONFLICT_STATE,
                    path,
                    "state 'none' cannot carry recorded contradictions",
                )
            )
        if resolution is not None:
            out.append(
                Finding(E_UNEXPECTED_RESOLUTION, path, "state 'none' cannot carry a resolution")
            )
        return

    # Conflicts are preserved: resolving a conflict ADDS a resolution record,
    # it never deletes the contradictory observations that caused it.
    if not contradictions:
        out.append(
            Finding(
                E_CONFLICT_EVIDENCE_LOST,
                path,
                f"state {state!r} must keep the contradictory observations on record",
            )
        )

    if state == S.CONFLICT_UNRESOLVED and resolution is not None:
        out.append(
            Finding(E_UNEXPECTED_RESOLUTION, path, "state 'unresolved' cannot carry a resolution")
        )

    if state == S.CONFLICT_RESOLVED_BY_REVIEW:
        if not isinstance(resolution, Mapping):
            out.append(
                Finding(
                    E_RESOLVED_WITHOUT_RESOLUTION,
                    path,
                    "state 'resolved_by_review' requires a resolution record",
                )
            )
            return
        for field in ("reviewer", "decided_at", "rationale"):
            value = resolution.get(field)
            if not isinstance(value, str) or not value.strip():
                out.append(
                    Finding(
                        E_RESOLVED_WITHOUT_RESOLUTION,
                        f"{path}.resolution.{field}",
                        f"resolution requires a non-empty {field}",
                    )
                )
        if not isinstance(resolution.get("chosen"), Mapping) or not resolution.get("chosen"):
            out.append(
                Finding(
                    E_RESOLVED_WITHOUT_RESOLUTION,
                    f"{path}.resolution.chosen",
                    "resolution must record what was chosen",
                )
            )


def _validate_presentation(req: Any, path: str, out: list[Finding]) -> None:
    if not isinstance(req, Mapping):
        out.append(Finding(E_BAD_PRESENTATION, path, "presentation_requirements must be an object"))
        return
    for key, value in req.items():
        expected = S.PRESENTATION_REQUIREMENT_FIELDS.get(key)
        if expected is None:
            out.append(
                Finding(
                    E_BAD_PRESENTATION,
                    f"{path}.{key}",
                    "presentation requirements state what the world needs, not how to draw it",
                )
            )
            continue
        if expected is bool and not isinstance(value, bool):
            out.append(Finding(E_BAD_PRESENTATION, f"{path}.{key}", "must be a boolean"))
        elif expected is str and not isinstance(value, str):
            out.append(Finding(E_BAD_PRESENTATION, f"{path}.{key}", "must be a string"))
    legibility = req.get("legibility_class")
    if legibility is not None and legibility not in S.LEGIBILITY_CLASSES:
        out.append(
            Finding(
                E_BAD_PRESENTATION,
                f"{path}.legibility_class",
                f"unknown legibility class {legibility!r}",
            )
        )


# --------------------------------------------------------------------------
# record validators
# --------------------------------------------------------------------------


def _validate_observation(
    rec: Any, frames: Mapping[str, Any], path: str, out: list[Finding]
) -> None:
    if not isinstance(rec, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "source observation must be an object"))
        return
    if rec.get("record_kind") != S.RECORD_SOURCE_OBSERVATION:
        out.append(
            Finding(
                E_WRONG_RECORD_KIND,
                f"{path}.record_kind",
                f"expected {S.RECORD_SOURCE_OBSERVATION!r}, got {rec.get('record_kind')!r}",
            )
        )
        return
    _require_fields(
        rec, S.SOURCE_OBSERVATION_REQUIRED, S.SOURCE_OBSERVATION_FIELDS, path, out
    )
    if rec.get("source_kind") not in S.SOURCE_KINDS:
        out.append(
            Finding(
                E_UNKNOWN_SOURCE_KIND,
                f"{path}.source_kind",
                f"unknown source kind {rec.get('source_kind')!r}",
            )
        )
    if not isinstance(rec.get("attributes"), Mapping):
        out.append(Finding(E_BAD_TYPE, f"{path}.attributes", "attributes must be an object"))
    if "geometry" in rec:
        _validate_geometry(rec["geometry"], frames, f"{path}.geometry", out)


def _validate_feature(
    rec: Any,
    frames: Mapping[str, Any],
    observation_ids: frozenset[str],
    path: str,
    out: list[Finding],
) -> None:
    if not isinstance(rec, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "canonical feature must be an object"))
        return

    # Rule 3: a lowered product can never be stored as canonical truth.
    kind = rec.get("record_kind")
    if kind == S.RECORD_GENERATED_REPRESENTATION:
        out.append(
            Finding(
                E_REPRESENTATION_AS_CANONICAL,
                f"{path}.record_kind",
                "a generated_representation cannot be stored in canonical_features",
            )
        )
        return
    if kind != S.RECORD_CANONICAL_FEATURE:
        out.append(
            Finding(
                E_WRONG_RECORD_KIND,
                f"{path}.record_kind",
                f"expected {S.RECORD_CANONICAL_FEATURE!r}, got {kind!r}",
            )
        )
        return

    for key, key_path in _walk_keys(rec, path):
        if key in S.REPRESENTATION_ONLY_KEYS:
            out.append(
                Finding(
                    E_REPRESENTATION_AS_CANONICAL,
                    key_path,
                    f"{key!r} names a lowered product; it belongs on a "
                    "generated_representation, not on world truth",
                )
            )

    _require_fields(rec, S.CANONICAL_FEATURE_REQUIRED, S.CANONICAL_FEATURE_FIELDS, path, out)

    if rec.get("semantic_class") not in S.SEMANTIC_CLASSES:
        out.append(
            Finding(
                E_UNKNOWN_SEMANTIC_CLASS,
                f"{path}.semantic_class",
                f"unknown semantic class {rec.get('semantic_class')!r}; "
                "the registry in schema.SEMANTIC_CLASSES is closed",
            )
        )

    geom_kind = _validate_geometry(rec.get("geometry"), frames, f"{path}.geometry", out)

    conf = rec.get("confidence")
    if not _is_num(conf) or not (0.0 <= conf <= 1.0):
        out.append(Finding(E_BAD_CONFIDENCE, f"{path}.confidence", "confidence must be in [0,1]"))

    authority = rec.get("gameplay_authority")
    if authority not in S.GAMEPLAY_AUTHORITIES:
        out.append(
            Finding(
                E_UNKNOWN_AUTHORITY,
                f"{path}.gameplay_authority",
                f"unknown gameplay authority {authority!r}",
            )
        )

    _validate_conflict(rec.get("conflict"), f"{path}.conflict", out)
    _validate_presentation(
        rec.get("presentation_requirements"), f"{path}.presentation_requirements", out
    )

    props = rec.get("properties")
    provenance_by_property: dict[str, str] = {}
    if not isinstance(props, Mapping):
        out.append(Finding(E_BAD_TYPE, f"{path}.properties", "properties must be an object"))
        props = {}
    else:
        for name, claim in props.items():
            prov = _validate_claim(
                str(name), claim, observation_ids, f"{path}.properties.{name}", out
            )
            if prov is not None:
                provenance_by_property[str(name)] = prov

    # Every geometric feature must carry a geometry claim, and that claim must
    # state its uncertainty.  A position with no uncertainty is not world truth.
    if geom_kind in S.GEOMETRIC_KINDS:
        claim = props.get(GEOMETRY_CLAIM) if isinstance(props, Mapping) else None
        if not isinstance(claim, Mapping):
            out.append(
                Finding(
                    E_MISSING_GEOMETRY_CLAIM,
                    f"{path}.properties.{GEOMETRY_CLAIM}",
                    "a geometric feature needs a 'geometry' property claim carrying its "
                    "provenance and uncertainty",
                )
            )
        elif "uncertainty" not in claim:
            out.append(
                Finding(
                    E_MISSING_UNCERTAINTY,
                    f"{path}.properties.{GEOMETRY_CLAIM}",
                    "a geometric feature must record positional uncertainty",
                )
            )

    # Rule 6: synthesized appearance must never silently become physical truth.
    if authority in S.PHYSICAL_AUTHORITIES:
        supporting = rec.get("authority_supporting_properties") or [GEOMETRY_CLAIM]
        if not isinstance(supporting, list):
            out.append(
                Finding(
                    E_BAD_TYPE,
                    f"{path}.authority_supporting_properties",
                    "must be a list of property names",
                )
            )
            supporting = [GEOMETRY_CLAIM]
        classes = {
            provenance_by_property[name] for name in supporting if name in provenance_by_property
        }
        if not classes or classes == {S.PROVENANCE_SYNTHESIZED}:
            out.append(
                Finding(
                    E_SYNTHESIZED_AUTHORITY,
                    path,
                    f"gameplay_authority {authority!r} is supported only by synthesized "
                    f"provenance ({sorted(classes) or 'no claim'}); physical authority "
                    "requires measured or derived evidence",
                )
            )

    supporting_ids = rec.get("supporting_observation_ids")
    if not isinstance(supporting_ids, list) or not all(
        isinstance(o, str) for o in supporting_ids
    ):
        out.append(
            Finding(E_BAD_TYPE, f"{path}.supporting_observation_ids", "must be a list of strings")
        )
    else:
        declared = set(supporting_ids)
        for oid in declared:
            if oid not in observation_ids:
                out.append(
                    Finding(
                        E_UNKNOWN_OBSERVATION_REF,
                        f"{path}.supporting_observation_ids",
                        f"observation {oid!r} is not in source_observations",
                    )
                )
        used: set[str] = set()
        if isinstance(props, Mapping):
            for claim in props.values():
                if isinstance(claim, Mapping):
                    ids = claim.get("observation_ids")
                    if isinstance(ids, list):
                        used.update(o for o in ids if isinstance(o, str))
        missing = sorted(used - declared)
        if missing:
            out.append(
                Finding(
                    E_UNSUPPORTED_OBSERVATION_ID,
                    f"{path}.supporting_observation_ids",
                    f"claims cite observations not listed on the feature: {missing}",
                )
            )


def _validate_representation(
    rec: Any,
    feature_authority: Mapping[str, str],
    path: str,
    out: list[Finding],
) -> None:
    if not isinstance(rec, Mapping):
        out.append(Finding(E_BAD_TYPE, path, "generated representation must be an object"))
        return
    if rec.get("record_kind") != S.RECORD_GENERATED_REPRESENTATION:
        out.append(
            Finding(
                E_WRONG_RECORD_KIND,
                f"{path}.record_kind",
                f"expected {S.RECORD_GENERATED_REPRESENTATION!r}, got {rec.get('record_kind')!r}",
            )
        )
        return
    _require_fields(
        rec,
        S.GENERATED_REPRESENTATION_REQUIRED,
        S.GENERATED_REPRESENTATION_FIELDS,
        path,
        out,
    )
    feature_id = rec.get("feature_id")
    if feature_id not in feature_authority:
        out.append(
            Finding(
                E_DANGLING_FEATURE_REF,
                f"{path}.feature_id",
                f"representation targets unknown feature {feature_id!r}",
            )
        )
    claim = rec.get("authority_claim")
    if claim not in S.GAMEPLAY_AUTHORITIES:
        out.append(
            Finding(E_UNKNOWN_AUTHORITY, f"{path}.authority_claim", f"unknown authority {claim!r}")
        )
    elif feature_id in feature_authority:
        owned = feature_authority[feature_id]
        if S.AUTHORITY_RANK.get(claim, 99) > S.AUTHORITY_RANK.get(owned, -1):
            out.append(
                Finding(
                    E_AUTHORITY_ESCALATION,
                    f"{path}.authority_claim",
                    f"representation claims {claim!r} but feature {feature_id!r} only "
                    f"owns {owned!r}; lowering cannot promote authority",
                )
            )
    for field in ("product", "lowering"):
        if field in rec and not isinstance(rec[field], Mapping):
            out.append(Finding(E_BAD_TYPE, f"{path}.{field}", "must be an object"))
    if not isinstance(rec.get("target"), str) or not str(rec.get("target", "")).strip():
        out.append(Finding(E_BAD_TYPE, f"{path}.target", "target must be a non-empty string"))


# --------------------------------------------------------------------------
# document validator
# --------------------------------------------------------------------------


def validate_document(doc: Any) -> list[Finding]:
    """Validate a whole World IR document.  Returns every finding it can see."""
    out: list[Finding] = []
    if not isinstance(doc, Mapping):
        return [Finding(E_BAD_TYPE, "$", "document must be an object")]

    version = doc.get("schema_version")
    if version != S.SCHEMA_VERSION:
        out.append(
            Finding(
                E_SCHEMA_VERSION,
                "$.schema_version",
                f"expected {S.SCHEMA_VERSION!r}, got {version!r}",
            )
        )
        # A document of another version cannot be interpreted against this
        # vocabulary; stop rather than emit meaningless field errors.
        return out

    _require_fields(doc, S.DOCUMENT_REQUIRED, S.DOCUMENT_FIELDS, "$", out)

    frames = doc.get("coordinate_frames")
    if not isinstance(frames, Mapping):
        out.append(Finding(E_BAD_TYPE, "$.coordinate_frames", "must be an object keyed by frame_id"))
        frames = {}
    else:
        for frame_id, frame in frames.items():
            _validate_frame(frame, str(frame_id), f"$.coordinate_frames.{frame_id}", out)

    observations = doc.get("source_observations")
    if not isinstance(observations, list):
        out.append(Finding(E_BAD_TYPE, "$.source_observations", "must be a list"))
        observations = []
    features = doc.get("canonical_features")
    if not isinstance(features, list):
        out.append(Finding(E_BAD_TYPE, "$.canonical_features", "must be a list"))
        features = []
    representations = doc.get("generated_representations")
    if not isinstance(representations, list):
        out.append(Finding(E_BAD_TYPE, "$.generated_representations", "must be a list"))
        representations = []

    seen_obs: set[str] = set()
    for i, rec in enumerate(observations):
        path = f"$.source_observations[{i}]"
        if isinstance(rec, Mapping):
            oid = rec.get("observation_id")
            if isinstance(oid, str):
                if oid in seen_obs:
                    out.append(
                        Finding(E_DUPLICATE_ID, path, f"duplicate observation_id {oid!r}")
                    )
                seen_obs.add(oid)
        _validate_observation(rec, frames, path, out)
    observation_ids = frozenset(seen_obs)

    feature_authority: dict[str, str] = {}
    seen_features: set[str] = set()
    for i, rec in enumerate(features):
        path = f"$.canonical_features[{i}]"
        if isinstance(rec, Mapping):
            fid = rec.get("feature_id")
            if isinstance(fid, str):
                if fid in seen_features:
                    out.append(Finding(E_DUPLICATE_ID, path, f"duplicate feature_id {fid!r}"))
                seen_features.add(fid)
                authority = rec.get("gameplay_authority")
                feature_authority[fid] = (
                    authority if authority in S.GAMEPLAY_AUTHORITIES else S.AUTHORITY_NONE
                )
        _validate_feature(rec, frames, observation_ids, path, out)

    for i, rec in enumerate(features):
        if isinstance(rec, Mapping):
            parent = rec.get("parent_feature_id")
            if isinstance(parent, str) and parent not in seen_features:
                out.append(
                    Finding(
                        E_DANGLING_FEATURE_REF,
                        f"$.canonical_features[{i}].parent_feature_id",
                        f"parent feature {parent!r} is not in this document",
                    )
                )

    seen_reps: set[str] = set()
    for i, rec in enumerate(representations):
        path = f"$.generated_representations[{i}]"
        if isinstance(rec, Mapping):
            rid = rec.get("representation_id")
            if isinstance(rid, str):
                if rid in seen_reps:
                    out.append(Finding(E_DUPLICATE_ID, path, f"duplicate representation_id {rid!r}"))
                seen_reps.add(rid)
        _validate_representation(rec, feature_authority, path, out)

    return out


def is_valid(doc: Any) -> bool:
    return not validate_document(doc)


def assert_valid(doc: Any) -> None:
    findings = validate_document(doc)
    if findings:
        raise WorldIRValidationError(findings)
