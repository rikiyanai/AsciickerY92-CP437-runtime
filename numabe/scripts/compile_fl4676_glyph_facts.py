#!/usr/bin/env python3
"""Compile the fail-closed FL-4676 GlyphRecord and GlyphFact artifact.

The native atlas registry owns the admitted glyph universe.  This compiler
joins every admitted binding to one intrinsic GlyphRecord, then emits
GlyphFacts whose members contain only a GlyphRecord reference and one
fact-local normalized weight.  No runtime loader consumes this artifact;
FL-4683 still owns the future scene-fact handshake.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from compile_fl4512_glyph_transform_relation_v2 import _orientation
from fl4512_descriptor_census import tile_grid
from fl4512_u7_mask import (
    U7MaskError,
    betti_numbers,
    mask8_from_page16_rgba,
    stroke_port_graph,
    topology_counts,
)
from generate_glyph_shape_catalog import shape6_metrics


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
SCHEMA_ID = "fl4676.glyph_fact.v1"
GLYPH_RECORD_SCHEMA_ID = "fl4676.glyph_record.v1"
GLYPH_FACT_SCHEMA_ID = "fl4676.glyph_fact_record.v1"
NORMALIZED_WEIGHT_SUM = 65535
RELATION_SCOPE = ["unary", "relation", "structural", "temporal"]
LEGACY_HARRI_SCORER_ID = "fl4260.shape6_weighted_l2.v1"
EXACT_HARRI_SCORER_ID = "fl4512.shape6_f8_w4.v2"
# FL-4763 explicit structural decline (see DECLINE_SCORER_ID in
# scripts/compile_fl4512_pools.py). Scorers listed here run no distance
# comparison, so a temporal margin policy would be a policy over a score that is
# never computed. Requiring one would force this compiler to INVENT a temporal
# policy for a row whose whole point is that it declines -- so they are exempt,
# exactly as the authored S_pf field already is.
SCORERS_WITHOUT_TEMPORAL_POLICY = frozenset({
    "S_pf.v1",
    "fl4763.structural_decline.v1",
})

SOURCES = {
    "artifact_schema": Path("assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json"),
    "compiler_script": Path("scripts/compile_fl4676_glyph_facts.py"),
    "compiled_pool_v1": Path("assets/glyphs/generated/fl4512.compiled_pool.v1.json"),
    "cp437_native_source": Path("assets/glyphs/generated/cp437.native_atlas_source.v1.json"),
    "descriptor_census": Path("assets/glyphs/generated/fl4512.descriptor_census.v1.jsonl"),
    "descriptor_census_manifest": Path("assets/glyphs/generated/fl4512.descriptor_census.v1.manifest.json"),
    "descriptor_census_primitives": Path("scripts/fl4512_descriptor_census.py"),
    "gtr_v2": Path("assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"),
    "gtr_v2_schema": Path("assets/glyphs/schema/fl4512.glyph_transform_relation.v2.schema.json"),
    "material_additive_catalog": Path("assets/glyphs/generated/material.additive.v1.shape_catalog.json"),
    "material_morphology_catalog": Path("assets/glyphs/generated/material.morphology.v2.shape_catalog.json"),
    "material_profiles": Path("assets/glyphs/profiles/material_rendering_profiles.v1.json"),
    "mesh_admission": Path("assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"),
    "morphology_primitives": Path("scripts/generate_glyph_shape_catalog.py"),
    "native_atlas_registry": Path("assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"),
    "runtime_bindings": Path("assets/glyphs/generated/unicode16.onecell.v1.runtime_bindings.json"),
    "scalar_registry": Path("assets/glyphs/generated/unicode16.onecell.v1.scalar_registry.json"),
    "temporal_hysteresis": Path("assets/glyphs/profiles/temporal_hysteresis.v1.json"),
    "topology_primitives": Path("scripts/fl4512_u7_mask.py"),
    "unicode_shape_catalog": Path("assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"),
}

REJECTION_CLASSES = [
    "artifact_sha_mismatch",
    "schema_mismatch",
    "source_pin_mismatch",
    "admitted_universe_mismatch",
    "required_attribute_missing",
    "source_join_mismatch",
    "unknown_fact_id",
    "unknown_glyph_record_id",
    "duplicate_fact_id",
    "duplicate_glyph_record_id",
    "relation_scope_violation",
    "membership_overlap_without_semantics",
    "capacity_exceeded",
    "malformed_record",
    "empty_subset",
    "unnormalized_fact_weights",
]

FORBIDDEN_RUNTIME_AUTHORITIES = [
    "derive_glyph_metadata_at_runtime",
    "derive_relation_edges_at_runtime",
    "invent_membership_at_runtime",
    "synthesize_replacement_rows",
    "substitute_missing_fact",
    "mutate_compiled_artifact",
    "treat_scene_fact_as_glyph_metadata",
]


class CompileError(ValueError):
    """Stable fail-closed compiler diagnostic."""


def _root_path(path: Path) -> Path:
    """Resolve a repository-relative path without changing default pins."""
    return path if path.is_absolute() else ROOT / path


def preview_path(value: str | Path, option: str) -> Path:
    from fl4512_compiler_paths import transaction_path
    return transaction_path(value, option, ROOT, CompileError)


def path_identity(path: Path) -> str:
    """Return the stable repository-relative identity stored in source pins."""
    resolved = _root_path(path).resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise CompileError(f"source_path_outside_repository:{path}") from exc


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_value(value: Any) -> str:
    return sha_bytes(canonical(value))


def sha_file(relative: Path) -> str:
    try:
        return sha_bytes(_root_path(relative).read_bytes())
    except OSError as exc:
        raise CompileError(f"source_pin_read_failed:{relative}") from exc


def _duplicate_key_reader(relative: Path):
    def reject_duplicate_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise CompileError(f"duplicate_key:{relative}:{key}")
            out[key] = value
        return out

    return reject_duplicate_keys


def load_json(relative: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _root_path(relative).read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_key_reader(relative),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompileError(f"json_read_failed:{relative}") from exc
    if not isinstance(value, dict):
        raise CompileError(f"root_not_object:{relative}")
    return value


def load_jsonl(relative: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with _root_path(relative).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                value = json.loads(
                    line,
                    object_pairs_hook=_duplicate_key_reader(relative),
                )
                if not isinstance(value, dict):
                    raise CompileError(f"jsonl_row_not_object:{relative}:{line_number}")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompileError(f"jsonl_read_failed:{relative}") from exc
    return rows


def exact_index(rows: Iterable[dict[str, Any]], key: str, owner: str) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}
    for row in rows:
        if key not in row:
            raise CompileError(f"required_attribute_missing:{owner}:{key}")
        value = int(row[key])
        if value in index:
            raise CompileError(f"source_join_mismatch:{owner}:duplicate:{value}")
        index[value] = row
    return index


def require_exact_glyph_ids(owner: str, expected: set[int], actual: set[int]) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        missing_text = ",".join(str(value) for value in missing[:8])
        extra_text = ",".join(str(value) for value in extra[:8])
        raise CompileError(
            f"admitted_universe_mismatch:{owner}:"
            f"missing={len(missing)}[{missing_text}]:extra={len(extra)}[{extra_text}]"
        )


def q0_16(value: float) -> int:
    decimal = Decimal(str(value))
    if not Decimal("0") <= decimal <= Decimal("1"):
        raise CompileError(f"required_attribute_missing:q0_16_range:{value}")
    return int((decimal * NORMALIZED_WEIGHT_SUM).to_integral_value(rounding=ROUND_HALF_UP))


def normalized_member_weights(count: int) -> list[int]:
    """Return descending integer weights whose exact sum is 65,535."""
    if count <= 0:
        raise CompileError("empty_subset")
    numerators = list(range(count, 0, -1))
    denominator = sum(numerators)
    floors = [(NORMALIZED_WEIGHT_SUM * value) // denominator for value in numerators]
    remainders = [(NORMALIZED_WEIGHT_SUM * value) % denominator for value in numerators]
    remaining = NORMALIZED_WEIGHT_SUM - sum(floors)
    order = sorted(range(count), key=lambda index: (-remainders[index], index))
    for index in order[:remaining]:
        floors[index] += 1
    if any(value <= 0 for value in floors) or sum(floors) != NORMALIZED_WEIGHT_SUM:
        raise CompileError("unnormalized_fact_weights")
    return floors


def glyph_record_id(glyph_id: int) -> int:
    return glyph_id


def _atlas_page_pin_name(page: dict[str, Any]) -> str:
    return f"atlas_page_{int(page['pack_index']):02d}_{int(page['page_index']):03d}"


def source_pins(
    registry: dict[str, Any], sources: dict[str, Path] | None = None
) -> list[dict[str, str]]:
    paths = dict(SOURCES if sources is None else sources)
    for page in registry.get("pages", []):
        name = _atlas_page_pin_name(page)
        if name in paths:
            raise CompileError(f"source_join_mismatch:duplicate_source_pin:{name}")
        paths[name] = Path(str(page["page16_path"]))
    return [
        {"name": name, "path": path_identity(relative), "sha256": sha_file(relative)}
        for name, relative in sorted(paths.items())
    ]


def load_and_verify_atlas_pages(
    registry: dict[str, Any],
) -> dict[tuple[int, int], tuple[bytes, int, int]]:
    """Verify every page pin and retain the three small legacy/material pages."""
    retained: dict[tuple[int, int], tuple[bytes, int, int]] = {}
    for page in registry.get("pages", []):
        path = ROOT / str(page.get("page16_path"))
        try:
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                raw = rgba.tobytes()
                width, height = rgba.size
        except (OSError, ValueError) as exc:
            raise CompileError(f"required_attribute_missing:atlas_page:{path}") from exc
        if sha_bytes(raw) != page.get("page16_sha256"):
            raise CompileError(f"source_pin_mismatch:decoded_atlas_page:{path}")
        pack_index = int(page["pack_index"])
        page_index = int(page["page_index"])
        if pack_index < 3:
            retained[(pack_index, page_index)] = (raw, width, height)
    return retained


def extract_tile(
    page: tuple[bytes, int, int], source_column: int, source_row: int
) -> bytes:
    raw, width, height = page
    x0 = source_column * 16
    y0 = source_row * 16
    if x0 < 0 or y0 < 0 or x0 + 16 > width or y0 + 16 > height:
        raise CompileError(f"source_join_mismatch:atlas_cell:{source_column}:{source_row}")
    tile = bytearray()
    for y in range(16):
        start = ((y0 + y) * width + x0) * 4
        tile.extend(raw[start : start + 64])
    return bytes(tile)


def mask_from_page(
    page: tuple[bytes, int, int], source_column: int, source_row: int
) -> int:
    raw, width, _height = page
    try:
        return mask8_from_page16_rgba(raw, width, source_column * 16, source_row * 16)
    except U7MaskError as exc:
        if exc.reason == "mask8_empty":
            return 0
        raise CompileError(f"source_join_mismatch:mask8:{exc.reason}") from exc


def mask_class(mask: int, beta0: int, beta1: int, endpoints: int, junctions: int) -> str:
    if mask == 0:
        return "blank"
    if mask.bit_count() >= 56:
        return "block"
    if beta0 > 1:
        return "multi_component"
    if junctions > 0:
        return "branching"
    if beta1 > 0:
        return "loop"
    if endpoints == 0:
        return "closed"
    orientation = _orientation(mask)
    if abs(orientation[0]) >= 2 * abs(orientation[1]):
        return "horizontal" if orientation[0] >= 0 else "vertical"
    return "diagonal"


def compile_profile_records(
    profile_source: dict[str, Any], pool: dict[str, Any],
    *, profile_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[int, list[str]], set[str]]:
    profile_path = SOURCES["material_profiles"] if profile_path is None else profile_path
    if pool.get("header", {}).get("profile_hash") != sha_file(profile_path):
        raise CompileError("source_pin_mismatch:compiled_pool:material_profiles")
    source_by_id: dict[str, dict[str, Any]] = {}
    memberships: dict[int, set[str]] = defaultdict(set)
    for profile in profile_source.get("profiles", []):
        profile_id = str(profile.get("material_id", ""))
        if not profile_id or profile_id in source_by_id:
            raise CompileError(f"source_join_mismatch:material_profile:{profile_id}")
        source_by_id[profile_id] = profile
        for glyph_id in profile.get("glyph_pools", {}).get("glyph_ids", []):
            memberships[int(glyph_id)].add(f"{profile_id}:glyph_pool")
        for role, buckets in profile.get("role_buckets", {}).items():
            if not isinstance(buckets, list):
                continue
            for index, bucket in enumerate(buckets):
                if not isinstance(bucket, dict):
                    raise CompileError(f"malformed_record:profile_bucket:{profile_id}:{role}")
                discriminator = next(
                    (
                        str(bucket[key])
                        for key in ("bucket", "elevation_row", "fact", "seed", "elv")
                        if key in bucket
                    ),
                    str(index),
                )
                membership = f"{profile_id}:{role}:{discriminator}"
                for glyph_id in bucket.get("candidates", []):
                    memberships[int(glyph_id)].add(membership)

    compiled_by_id: dict[str, dict[str, Any]] = {}
    for material in pool.get("materials", []):
        profile_id = f"{material['profile_namespace']}:{int(material['material_key'])}"
        source = source_by_id.get(profile_id)
        if source is None:
            raise CompileError(f"source_join_mismatch:compiled_profile:{profile_id}")
        compiled_by_id[profile_id] = material
    if set(compiled_by_id) != set(source_by_id):
        raise CompileError("source_join_mismatch:compiled_profile_set")

    records: list[dict[str, Any]] = []
    for profile_id in sorted(source_by_id):
        source = source_by_id[profile_id]
        compiled = compiled_by_id[profile_id]
        record = {
            "profile_id": profile_id,
            "source_profile_sha256": sha_value(source),
            "compiled_profile_sha256": sha_value(compiled),
            "role_weights": [float(value) for value in compiled.get("role_weights", [])],
            "runtime_profile_live": bool(source.get("runtime_profile_live")),
            "profile_sha256": "0" * 64,
        }
        body = dict(record)
        body.pop("profile_sha256")
        record["profile_sha256"] = sha_value(body)
        records.append(record)
    normalized_memberships = {
        glyph_id: sorted(values) for glyph_id, values in memberships.items()
    }
    return records, normalized_memberships, set(source_by_id)


def compile_temporal_policies(
    temporal: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    policies: list[dict[str, Any]] = []
    by_scorer: dict[str, str] = {}
    for row in temporal.get("rows", []):
        scorer_id = str(row.get("scorer_id", ""))
        if not scorer_id or scorer_id in by_scorer:
            raise CompileError(f"source_join_mismatch:temporal_policy:{scorer_id}")
        policy_id = f"temporalpolicy://fl4676/v1/{scorer_id}"
        policy = {
            "temporal_policy_id": policy_id,
            "source_table_sha256": str(temporal.get("table_sha256", "")),
            "source_row": copy.deepcopy(row),
            "policy_sha256": sha_value(row),
        }
        policies.append(policy)
        by_scorer[scorer_id] = policy_id
    if not policies:
        raise CompileError("required_attribute_missing:temporal_policies")
    return policies, by_scorer


def product_scorer_id(source_scorer_id: str) -> str:
    """Retire the pool-era Harri label at the compiled GlyphFact boundary."""
    if source_scorer_id == LEGACY_HARRI_SCORER_ID:
        return EXACT_HARRI_SCORER_ID
    return source_scorer_id


def structural_graph_glyph_ids(pool: dict[str, Any]) -> set[int]:
    """Return the glyph records whose port graphs are runtime-owned data.

    FL-4783 needs compiler-owned stroke-port graphs for U7 structural members.
    Emitting the same verbose graph object for every admitted tonal glyph turns
    GlyphFact into an unpushable transport artifact without adding runtime
    authority. Non-structural records still carry mask/skeleton summaries.
    """
    glyph_ids: set[int] = set()
    for row in pool.get("rows", []):
        if int(row.get("candidate_count", 0)) <= 0 or not row.get("candidate_glyph_ids"):
            continue
        if product_scorer_id(str(row.get("scorer_id"))) != "u7.mask.v1":
            continue
        glyph_ids.update(int(value) for value in row.get("candidate_glyph_ids", []))
    if not glyph_ids:
        raise CompileError("required_attribute_missing:structural_graph_glyph_ids")
    return glyph_ids


def build_relation_index(gtr: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for edge in gtr.get("relations", []):
        from_gid = int(edge["from_glyph_id"])
        out.setdefault(from_gid, []).append(edge)
    for edges in out.values():
        edges.sort(
            key=lambda item: (
                int(item["compiled_transform_cost"]),
                int(item["aspect"]),
                int(item["to_glyph_id"]),
            )
        )
    return out


def build_glyph_records(
    registry: dict[str, Any],
    cp437_source: dict[str, Any],
    additive_catalog: dict[str, Any],
    morphology_catalog: dict[str, Any],
    unicode_catalog: dict[str, Any],
    runtime_bindings: dict[str, Any],
    scalar_registry: dict[str, Any],
    descriptor_rows: list[dict[str, Any]],
    descriptor_manifest: dict[str, Any],
    retained_pages: dict[tuple[int, int], tuple[bytes, int, int]],
    profile_memberships: dict[int, list[str]],
    mesh_glyph_ids: set[int],
    structural_graph_glyph_ids: set[int],
    gtr: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    bindings = registry.get("bindings", [])
    atlas_by_gid = exact_index(bindings, "glyph_id", "native_atlas_registry")
    if len(atlas_by_gid) != int(registry.get("binding_count", -1)):
        raise CompileError("admitted_universe_mismatch:native_atlas_registry:binding_count")
    pack_by_index = {
        int(pack["pack_index"]): pack for pack in registry.get("packs", [])
    }
    if set(pack_by_index) != {0, 1, 2, 3}:
        raise CompileError("source_join_mismatch:native_atlas_registry:packs")

    additive_by_gid = exact_index(additive_catalog.get("entries", []), "glyph_id", "material_additive_catalog")
    morphology_by_gid = exact_index(morphology_catalog.get("entries", []), "glyph_id", "material_morphology_catalog")
    unicode_by_gid = exact_index(unicode_catalog.get("entries", []), "glyph_id", "unicode_shape_catalog")
    runtime_by_gid = exact_index(runtime_bindings.get("bindings", []), "glyph_id", "runtime_bindings")
    scalar_by_gid = exact_index(scalar_registry.get("allocations", []), "glyph_id", "scalar_registry")
    census_by_gid = exact_index(descriptor_rows, "glyph_id", "descriptor_census")
    gtr_by_gid = exact_index(gtr.get("descriptors", []), "glyph_id", "gtr_v2.descriptors")

    if sha_file(SOURCES["descriptor_census"]) != descriptor_manifest.get("census_sha256"):
        raise CompileError("source_pin_mismatch:descriptor_census_manifest")
    if len(descriptor_rows) != int(descriptor_manifest.get("record_count", -1)):
        raise CompileError("source_join_mismatch:descriptor_census:record_count")
    if runtime_bindings.get("unicode_scalar_registry_sha256") != scalar_registry.get("registry_sha256"):
        raise CompileError("source_join_mismatch:runtime_bindings:scalar_registry")

    expected_pack_gids = {
        pack_index: {
            int(binding["glyph_id"])
            for binding in bindings
            if int(binding["pack_index"]) == pack_index
        }
        for pack_index in pack_by_index
    }
    cp437_ids = {int(value) for value in cp437_source.get("glyph_index", {}).keys()}
    require_exact_glyph_ids("cp437_native_source", expected_pack_gids[0], cp437_ids)
    require_exact_glyph_ids("material_additive_catalog", expected_pack_gids[1], set(additive_by_gid))
    require_exact_glyph_ids("material_morphology_catalog", expected_pack_gids[2], set(morphology_by_gid))
    require_exact_glyph_ids("unicode_shape_catalog", expected_pack_gids[3], set(unicode_by_gid))
    require_exact_glyph_ids("scalar_registry", expected_pack_gids[3], set(scalar_by_gid))

    records: list[dict[str, Any]] = []
    record_id_by_gid: dict[int, int] = {}
    for glyph_id in sorted(atlas_by_gid):
        admission = atlas_by_gid[glyph_id]
        pack_index = int(admission["pack_index"])
        pack_id = str(pack_by_index[pack_index]["content_pack_id"])
        catalog: dict[str, Any] | None
        if pack_index == 0:
            catalog = None
            text_identity = {
                "kind": "cp437_code_unit",
                "cp437_code_unit": glyph_id,
            }
        elif pack_index == 1:
            catalog = additive_by_gid[glyph_id]
            text_identity = {
                "kind": "unicode_scalar",
                "unicode_scalar": int(catalog["unicode_scalar"]),
            }
        elif pack_index == 2:
            catalog = morphology_by_gid[glyph_id]
            text_identity = {
                "kind": "unicode_scalar",
                "unicode_scalar": int(catalog["unicode_scalar"]),
            }
        else:
            catalog = unicode_by_gid[glyph_id]
            scalar = int(catalog["unicode_scalar"])
            text_identity = {"kind": "unicode_scalar", "unicode_scalar": scalar}
            scalar_row = scalar_by_gid[glyph_id]
            runtime_row = runtime_by_gid.get(glyph_id)
            if runtime_row is None:
                raise CompileError(f"required_attribute_missing:runtime_binding:{glyph_id}")
            if int(scalar_row["unicode_scalar"]) != scalar or int(runtime_row["unicode_scalar"]) != scalar:
                raise CompileError(f"source_join_mismatch:unicode_identity:{glyph_id}")

        runtime_row = runtime_by_gid.get(glyph_id)
        if runtime_row is not None:
            if runtime_row.get("pack") != pack_id:
                raise CompileError(f"source_join_mismatch:runtime_pack:{glyph_id}")
            if text_identity["kind"] == "unicode_scalar" and int(runtime_row["unicode_scalar"]) != int(text_identity["unicode_scalar"]):
                raise CompileError(f"source_join_mismatch:runtime_scalar:{glyph_id}")
            runtime_binding_declared = True
        else:
            runtime_binding_declared = False

        census = census_by_gid.get(glyph_id)
        if census is not None:
            if census.get("pack") != pack_id:
                raise CompileError(f"source_join_mismatch:descriptor_pack:{glyph_id}")
            mask = int(str(census["mask8_u64"]), 16)
            shape6 = [float(value) for value in census["shape6"]]
            shape6_norm = [float(value) for value in census["shape6_norm"]]
            if catalog is not None and "shape6" in catalog:
                if shape6 != [float(value) for value in catalog["shape6"]]:
                    raise CompileError(f"source_join_mismatch:catalog_shape6:{glyph_id}")
            descriptor_source = 0
        else:
            page_key = (pack_index, int(admission["page_index"]))
            page = retained_pages.get(page_key)
            if page is None:
                raise CompileError(f"required_attribute_missing:retained_atlas_page:{glyph_id}")
            mask = mask_from_page(page, int(admission["source_column"]), int(admission["source_row"]))
            tile = extract_tile(page, int(admission["source_column"]), int(admission["source_row"]))
            measured = shape6_metrics(tile_grid(tile))
            if catalog is None:
                shape6 = [float(value) for value in measured["shape6"]]
                shape6_norm = [float(value) for value in measured["shape6_norm"]]
                descriptor_source = 1
            else:
                shape6 = [float(value) for value in catalog["shape6"]]
                shape6_norm = [float(value) for value in catalog["shape6_norm"]]
                descriptor_source = 2

        skeleton, endpoints, junctions = topology_counts(mask)
        beta0, beta1 = betti_numbers(mask)
        if census is not None:
            if endpoints != int(census["endpoint_count"]) or junctions != int(census["junction_cluster_count"]):
                raise CompileError(f"source_join_mismatch:descriptor_topology:{glyph_id}")
        if pack_index == 3:
            if mask != int(str(catalog["mask8_u64"]), 16):
                raise CompileError(f"source_join_mismatch:unicode_mask:{glyph_id}")
            if endpoints != int(catalog["endpoint_count"]) or junctions != int(catalog["junction_cluster_count"]):
                raise CompileError(f"source_join_mismatch:unicode_topology:{glyph_id}")

        orientation = _orientation(mask)
        gtr_descriptor = gtr_by_gid.get(glyph_id)
        relation_descriptor_ref = None
        if gtr_descriptor is not None:
            if int(str(gtr_descriptor["mask8_u64_hex"]), 16) != mask:
                raise CompileError(f"source_join_mismatch:gtr_descriptor:{glyph_id}")
            relation_descriptor_ref = {
                "source": "fl4512.glyph_transform_relation.v2",
                "descriptor_sha256": sha_value(gtr_descriptor),
            }

        record_id = glyph_record_id(glyph_id)
        connectivity = {
            "mask8_u64_le_hex": mask.to_bytes(8, "little").hex(),
            "skeleton_u64_le_hex": skeleton.to_bytes(8, "little").hex(),
        }
        if glyph_id in structural_graph_glyph_ids:
            connectivity["stroke_port_graph"] = stroke_port_graph(mask)

        record = {
            "glyph_record_id": record_id,
            "admission": {
                "content_pack_id": pack_id,
                "pack_index": pack_index,
                "page_index": int(admission["page_index"]),
                "layer_index": int(admission["layer_index"]),
                "source_column": int(admission["source_column"]),
                "source_row": int(admission["source_row"]),
                "runtime_binding_declared": runtime_binding_declared,
            },
            "text_identity": text_identity,
            "morphology": {
                "descriptor_source": descriptor_source,
                "shape6_q0_16": [q0_16(value) for value in shape6],
                "shape6_norm_q0_16": [q0_16(value) for value in shape6_norm],
                "density_q0_16": (mask.bit_count() * NORMALIZED_WEIGHT_SUM + 32) // 64,
            },
            "structure": {
                "beta0": beta0,
                "beta1": beta1,
                "euler_characteristic": beta0 - beta1,
                "endpoint_count": endpoints,
                "junction_count": junctions,
            },
            "connectivity": connectivity,
            "stroke_identity": {
                "mask_class": mask_class(mask, beta0, beta1, endpoints, junctions),
                "orientation_q1_15": orientation,
                "catalog_stroke_class": None if catalog is None else catalog.get("stroke_class"),
            },
            "mesh_admission_member": glyph_id in mesh_glyph_ids,
            "relation_descriptor_ref": relation_descriptor_ref,
            "record_sha256": "0" * 64,
        }
        catalog_roles = sorted(str(value) for value in (catalog or {}).get("roles", []))
        if catalog_roles:
            record["morphology"]["catalog_roles"] = catalog_roles
        if profile_memberships.get(glyph_id):
            record["profile_memberships"] = profile_memberships[glyph_id]
        body = copy.deepcopy(record)
        body.pop("record_sha256")
        record["record_sha256"] = sha_value(body)
        records.append(record)
        record_id_by_gid[glyph_id] = record_id

    require_exact_glyph_ids("glyph_records", set(atlas_by_gid), set(record_id_by_gid))
    return records, record_id_by_gid


def relation_projection(
    fact_id_suffix: str,
    glyph_ids: list[int],
    weights_by_gid: dict[int, int],
    record_id_by_gid: dict[int, int],
    relation_index: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    subset = set(glyph_ids)
    edges: list[dict[str, Any]] = []
    for from_gid in glyph_ids:
        for edge in relation_index.get(from_gid, []):
            to_gid = int(edge["to_glyph_id"])
            if to_gid not in subset:
                continue
            edges.append(
                {
                    "from_glyph_record_id": record_id_by_gid[from_gid],
                    "to_glyph_record_id": record_id_by_gid[to_gid],
                    "aspect": int(edge["aspect"]),
                    "membership_weight_q0_16": weights_by_gid[from_gid],
                    "relation_confidence_q0_16": int(edge["relation_confidence_q0_16"]),
                    "compiled_transform_cost": int(edge["compiled_transform_cost"]),
                }
            )
            if len(edges) >= 32:
                break
        if len(edges) >= 32:
            break
    projection = {
        "projection_id": f"fl4676.{fact_id_suffix}.relation_projection.v1",
        "source_table": "fl4512.glyph_transform_relation.v2",
        "projection_state": "compiled" if edges else "no_admitted_relation_edges",
        "scope": RELATION_SCOPE,
        "max_outgoing_edges": 32,
        "edges": edges,
        "projection_sha256": "0" * 64,
    }
    body = copy.deepcopy(projection)
    body.pop("projection_sha256")
    projection["projection_sha256"] = sha_value(body)
    return projection


def parse_route_memberships(row: dict[str, Any]) -> list[dict[str, Any]]:
    memberships = [
        {"axis": "role", "key": str(row["role"]), "source": "compiled_pool.v1.row.role"},
        {"axis": "route_key", "key": str(row["route_key"]), "source": "compiled_pool.v1.row.route_key"},
    ]
    lane_name = str(row.get("lane_name", ""))
    for axis in ("ramp", "density", "direction", "flow", "edge"):
        marker = f"{axis}["
        start = lane_name.find(marker)
        value_start = start + len(marker) if start >= 0 else -1
        value_end = lane_name.find("]", value_start) if value_start >= 0 else -1
        if value_start >= 0 and value_end > value_start:
            memberships.append(
                {
                    "axis": axis,
                    "key": lane_name[value_start:value_end],
                    "source": "compiled_pool.v1.row.lane_name",
                }
            )
    return memberships


def build_fact(
    row: dict[str, Any],
    record_id_by_gid: dict[int, int],
    relation_index: dict[int, list[dict[str, Any]]],
    profile_ids: set[str],
    profile_memberships: dict[int, list[str]],
    mesh_glyph_ids: set[int],
    temporal_by_scorer: dict[str, str],
) -> dict[str, Any]:
    glyph_ids = [int(value) for value in row.get("candidate_glyph_ids", [])]
    if not glyph_ids:
        raise CompileError(f"empty_subset:{row.get('row_sha256')}")
    if len(glyph_ids) != len(set(glyph_ids)):
        raise CompileError(f"malformed_record:duplicate_fact_member:{row.get('row_sha256')}")
    unknown = [glyph_id for glyph_id in glyph_ids if glyph_id not in record_id_by_gid]
    if unknown:
        raise CompileError(f"unknown_glyph_record_id:{unknown[0]}")
    profile_id = f"{row['profile_namespace']}:{int(row['material_key'])}"
    if profile_id not in profile_ids:
        raise CompileError(f"source_join_mismatch:fact_profile:{profile_id}")
    if row.get("role") == "mesh_feature":
        if glyph_ids != sorted(mesh_glyph_ids):
            raise CompileError("source_join_mismatch:mesh_admission:compiled_pool")
    else:
        for glyph_id in glyph_ids:
            if not any(value.startswith(f"{profile_id}:") for value in profile_memberships.get(glyph_id, [])):
                raise CompileError(f"source_join_mismatch:profile_membership:{profile_id}:{glyph_id}")

    weights = normalized_member_weights(len(glyph_ids))
    weights_by_gid = dict(zip(glyph_ids, weights, strict=True))
    members = [
        {
            "glyph_record_id": record_id_by_gid[glyph_id],
            "membership_weight_q0_16": weights_by_gid[glyph_id],
        }
        for glyph_id in glyph_ids
    ]
    if sum(member["membership_weight_q0_16"] for member in members) != NORMALIZED_WEIGHT_SUM:
        raise CompileError("unnormalized_fact_weights")

    scorer_id = product_scorer_id(str(row["scorer_id"]))
    temporal_policy_id = temporal_by_scorer.get(scorer_id)
    if temporal_policy_id is None and scorer_id not in SCORERS_WITHOUT_TEMPORAL_POLICY:
        raise CompileError(f"required_attribute_missing:temporal_policy:{scorer_id}")
    temporal_state = "compiled_margin" if temporal_policy_id else "not_applicable_to_structural_field"
    fact_suffix = str(row["row_sha256"])[:16]
    fact = {
        "glyph_fact_schema": GLYPH_FACT_SCHEMA_ID,
        "fact_id": f"glyphfact://fl4676/v1/{fact_suffix}",
        "source_profile_id": profile_id,
        "scene_membership_key": {
            "profile_namespace": str(row["profile_namespace"]),
            "material_key": int(row["material_key"]),
            "role": str(row["role"]),
            "lane_name": str(row["lane_name"]),
            "route_key": str(row["route_key"]),
            "row_sha256": str(row["row_sha256"]),
            "scorer_id": scorer_id,
            "capacity": int(row["capacity"]),
        },
        "subset_weight_sum_q0_16": NORMALIZED_WEIGHT_SUM,
        "glyph_members": members,
        "route_memberships": parse_route_memberships(row),
        "overlap_semantics": {
            "overlap_group_id": f"{profile_id}:{row['role']}:{row['lane_name']}",
            "weights_are_normalized": True,
            "normalization_sum_q0_16": NORMALIZED_WEIGHT_SUM,
            "resolution_rule": "compiled_rank_then_scene_cost",
        },
        "temporal_policy_state": temporal_state,
        "temporal_policy_id": temporal_policy_id,
        "relation_projection": relation_projection(
            fact_suffix,
            glyph_ids,
            weights_by_gid,
            record_id_by_gid,
            relation_index,
        ),
        "absence_policy": {
            "missing_fact_rejection_reason": "unknown_fact_id",
            "unknown_record_rejection_reason": "unknown_glyph_record_id",
            "empty_subset_rejection_reason": "empty_subset",
            "weight_rejection_reason": "unnormalized_fact_weights",
        },
        "fact_sha256": "0" * 64,
    }
    body = copy.deepcopy(fact)
    body.pop("fact_sha256")
    fact["fact_sha256"] = sha_value(body)
    return fact


def compile_artifact(
    *, pool_path: Path | None = None, mesh_admission_path: Path | None = None,
    profile_path: Path | None = None, authoring: bool = False,
) -> dict[str, Any]:
    from fl4512_compiler_paths import transaction_path
    def validate_path(value: Path, option: str) -> Path:
        return transaction_path(value, option, ROOT, CompileError, authoring=authoring)
    sources = dict(SOURCES)
    if profile_path is not None:
        if pool_path is None or mesh_admission_path is None:
            raise CompileError("profile override requires pool and mesh-admission overrides")
        sources["material_profiles"] = validate_path(profile_path, "--profiles")
    if pool_path is not None:
        sources["compiled_pool_v1"] = validate_path(pool_path, "--pool")
    if mesh_admission_path is not None:
        sources["mesh_admission"] = validate_path(mesh_admission_path, "--mesh-admission")
    registry = load_json(sources["native_atlas_registry"])
    pins = source_pins(registry, sources)
    pins_by_name = {pin["name"]: pin for pin in pins}
    pool = load_json(sources["compiled_pool_v1"])
    cp437_source = load_json(SOURCES["cp437_native_source"])
    additive_catalog = load_json(SOURCES["material_additive_catalog"])
    morphology_catalog = load_json(SOURCES["material_morphology_catalog"])
    unicode_catalog = load_json(SOURCES["unicode_shape_catalog"])
    runtime_bindings = load_json(SOURCES["runtime_bindings"])
    scalar_registry = load_json(SOURCES["scalar_registry"])
    descriptor_rows = load_jsonl(SOURCES["descriptor_census"])
    descriptor_manifest = load_json(SOURCES["descriptor_census_manifest"])
    gtr = load_json(SOURCES["gtr_v2"])
    profile_source = load_json(sources["material_profiles"])
    mesh_admission = load_json(sources["mesh_admission"])
    temporal = load_json(SOURCES["temporal_hysteresis"])

    if pool.get("header", {}).get("mesh_admission_artifact_sha256") != sha_file(sources["mesh_admission"]):
        raise CompileError("source_pin_mismatch:compiled_pool:mesh_admission")
    mesh_glyph_ids = {int(value) for value in mesh_admission.get("receipt", {}).get("selected_glyph_ids", [])}
    selected_rows = {int(row["glyph_id"]) for row in mesh_admission.get("selected_row", [])}
    if not mesh_glyph_ids or mesh_glyph_ids != selected_rows:
        raise CompileError("source_join_mismatch:mesh_admission:selected_row")
    structural_glyph_ids = structural_graph_glyph_ids(pool)

    profile_records, profile_memberships, profile_ids = compile_profile_records(
        profile_source, pool, profile_path=sources["material_profiles"]
    )
    temporal_policies, temporal_by_scorer = compile_temporal_policies(temporal)
    retained_pages = load_and_verify_atlas_pages(registry)
    glyph_records, record_id_by_gid = build_glyph_records(
        registry,
        cp437_source,
        additive_catalog,
        morphology_catalog,
        unicode_catalog,
        runtime_bindings,
        scalar_registry,
        descriptor_rows,
        descriptor_manifest,
        retained_pages,
        profile_memberships,
        mesh_glyph_ids,
        structural_glyph_ids,
        gtr,
    )
    relation_index = build_relation_index(gtr)
    facts = [
        build_fact(
            row,
            record_id_by_gid,
            relation_index,
            profile_ids,
            profile_memberships,
            mesh_glyph_ids,
            temporal_by_scorer,
        )
        for row in pool.get("rows", [])
        if int(row.get("candidate_count", 0)) > 0 and row.get("candidate_glyph_ids")
    ]
    fact_ids = [fact["fact_id"] for fact in facts]
    if len(fact_ids) != len(set(fact_ids)):
        raise CompileError("duplicate_fact_id")

    input_set_sha256 = sha_value(pins)
    artifact = {
        "schema": SCHEMA_ID,
        "version": 1,
        "artifact_kind": "offline_glyph_record_and_fact_compiler_output",
        "artifact_identity": {
            "artifact_id": f"glyphfact.v1.{input_set_sha256[:16]}",
            "compiler_id": "scripts/compile_fl4676_glyph_facts.py",
            "compiler_script_sha256": pins_by_name["compiler_script"]["sha256"],
            "input_set_sha256": input_set_sha256,
            "glyph_universe_source_file_sha256": pins_by_name["native_atlas_registry"]["sha256"],
            "glyph_universe_declared_sha256": str(registry["registry_sha256"]),
            "relation_source_content_sha256": str(gtr["content_sha256"]),
            "scene_fact_schema_state": "blocked_by_fl4683",
        },
        "source_pins": pins,
        "loader_contract": {
            "fail_closed": True,
            "rejection_reason_field": "glyph_fact_rejection_reason",
            "rejection_classes": REJECTION_CLASSES,
        },
        "runtime_rights": {
            "runtime_consumption_allowed": False,
            "blocked_by": "FL-4683",
            "future_rights_after_handshake": [
                "select_fact_id_from_scene_fact",
                "score_compiler_emitted_costs",
            ],
            "forbidden_authorities": FORBIDDEN_RUNTIME_AUTHORITIES,
        },
        "normalization_contract": {
            "weight_encoding": "q0_16",
            "required_sum_q0_16": NORMALIZED_WEIGHT_SUM,
            "algorithm": "descending_rank_largest_remainder.v1",
        },
        "relation_representation": {
            "edge_owner": "offline_compiler",
            "storage": "bounded_projection_per_fact",
            "dense_table_required": False,
            "projection_scope": RELATION_SCOPE,
            "max_edges_per_fact": 32,
        },
        "glyph_record_contract": {
            "schema": GLYPH_RECORD_SCHEMA_ID,
            "id_encoding": "admitted_glyph_id_u32",
            "descriptor_source_codes": {
                "0": "fl4512.descriptor_census.v1",
                "1": "legacy_cp437_atlas_measurement",
                "2": "shape_catalog_plus_atlas_measurement"
            },
            "profile_memberships_default": [],
            "catalog_roles_default": [],
            "stroke_port_graph_default": "absent",
            "stroke_port_graph_required_for_scorer": "u7.mask.v1",
            "stroke_port_graph_record_count": len(structural_glyph_ids),
        },
        "glyph_record_count": len(glyph_records),
        "glyph_records": glyph_records,
        "profile_count": len(profile_records),
        "profiles": profile_records,
        "temporal_policy_count": len(temporal_policies),
        "temporal_policies": temporal_policies,
        "fact_count": len(facts),
        "facts": facts,
        "canonical_content_sha256": "0" * 64,
    }
    body = copy.deepcopy(artifact)
    body.pop("canonical_content_sha256")
    artifact["canonical_content_sha256"] = sha_value(body)
    return artifact


def serialized(value: Any) -> bytes:
    return canonical(value) + b"\n"


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialized(value)
    path.write_bytes(payload)
    return sha_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--pool")
    parser.add_argument("--mesh-admission")
    parser.add_argument("--out")
    parser.add_argument("--profiles", help="staged authored profile matching the overridden pool")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write and --check")
    override_values = (args.pool, args.mesh_admission, args.out)
    if any(value is not None for value in override_values) and not all(
        value is not None for value in override_values
    ):
        parser.error("preview overrides require --pool, --mesh-admission, and --out together")
    if args.profiles is not None and not all(value is not None for value in override_values):
        parser.error("--profiles requires --pool, --mesh-admission, and --out together")
    try:
        override_paths = (
            None
            if all(value is None for value in override_values)
            else tuple(
                preview_path(value, option)
                for value, option in zip(
                    override_values, ("--pool", "--mesh-admission", "--out"), strict=True
                )
            )
        )
        pool_path, mesh_admission_path, output_override = override_paths or (None, None, None)
        if output_override is not None:
            output_resolved = _root_path(output_override).resolve(strict=False)
            input_resolved = {
                _root_path(pool_path).resolve(strict=False),
                _root_path(mesh_admission_path).resolve(strict=False),
            }
            if args.profiles is not None:
                input_resolved.add(_root_path(preview_path(args.profiles, "--profiles")).resolve())
            if output_resolved in input_resolved:
                raise CompileError("preview_output_input_collision")
        output_path = OUTPUT if output_override is None else _root_path(output_override)
        artifact = compile_artifact(
            pool_path=pool_path, mesh_admission_path=mesh_admission_path,
            profile_path=Path(args.profiles) if args.profiles is not None else None,
        )
        if args.write:
            file_sha256 = write_json(output_path, artifact)
            print(
                f"wrote {path_identity(output_path)} "
                f"glyph_records={artifact['glyph_record_count']} facts={artifact['fact_count']} "
                f"canonical_content_sha256={artifact['canonical_content_sha256']} "
                f"serialized_file_sha256={file_sha256}"
            )
            return 0
        current = load_json(output_path)
        if current != artifact:
            print(f"{path_identity(output_path)} is stale", file=sys.stderr)
            print(
                f"expected_canonical_content_sha256={artifact['canonical_content_sha256']}",
                file=sys.stderr,
            )
            print(
                f"actual_canonical_content_sha256={current.get('canonical_content_sha256')}",
                file=sys.stderr,
            )
            return 1
        print(
            f"ok {path_identity(output_path)} "
            f"glyph_records={artifact['glyph_record_count']} facts={artifact['fact_count']} "
            f"canonical_content_sha256={artifact['canonical_content_sha256']} "
            f"serialized_file_sha256={sha_file(output_path)}"
        )
        return 0
    except CompileError as exc:
        print(f"FL-4676 compile failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
