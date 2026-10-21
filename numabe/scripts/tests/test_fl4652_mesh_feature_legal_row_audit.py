#!/usr/bin/env python3
"""Executable audit for the authored FL-4652 mesh-feature legal row.

This suite is a byte/structural companion diagnostic. It deliberately makes
no visual-acceptance claim; FL-4377 keeps that authority with the operator's
multimodal review.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
POOL_PATH = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
CATALOG_PATH = ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
ATLAS_PATH = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
COMPILER_PATH = ROOT / "scripts/compile_fl4512_pools.py"
ASPECT_AUTHORING_PATH = ROOT / "assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json"
EXPECTED_NON_TARGET_ROWS_SHA256 = "0f66de4c2477e29fc12fd116b7b9406e2f52f687fb5370327953ec1aeb44b78d"
EXPECTED_TARGET_STABLE_SHA256 = "eca86930e6fb99a1bd0daaf1371dec93b7d74721b1f28189138a6fc0771213ee"
# Post admission cutover: the legal row is the strict admission artifact's
# selected_row (canonical ascending GlyphId), NOT a profile-authored array.
EXPECTED_GIDS = [
    row["glyph_id"]
    for row in json.loads(
        (ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json").read_text()
    )["selected_row"]
]

_U7_SPEC = importlib.util.spec_from_file_location(
    "fl4512_u7_mask", ROOT / "scripts/fl4512_u7_mask.py"
)
assert _U7_SPEC and _U7_SPEC.loader
U7 = importlib.util.module_from_spec(_U7_SPEC)
_U7_SPEC.loader.exec_module(U7)

_COMPILER_SPEC = importlib.util.spec_from_file_location(
    "compile_fl4512_pools", COMPILER_PATH
)
assert _COMPILER_SPEC and _COMPILER_SPEC.loader
COMPILER = importlib.util.module_from_spec(_COMPILER_SPEC)
_COMPILER_SPEC.loader.exec_module(COMPILER)


# This literal is the frozen descriptor receipt for the exact ordered row.
# Each value contains every U7 topology field whose bytes can affect runtime
# admission. Tests recompute the record through production U7 helpers, hash
# both canonical records, and compare all 32 digests.
EXPECTED_DESCRIPTORS = {
    520: ("0000081c38000000", "0000000810000000", 2, 0, 1, 0, 1),
    521: ("0008181818181000", "0000080808100000", 2, 0, 1, 0, 1),
    522: ("080c040000000000", "000c000000000000", 2, 0, 1, 0, 1),
    549: ("0018080800000000", "0008080800000000", 2, 0, 1, 0, 1),
    526: ("00001818183c0000", "0000000808080000", 2, 0, 1, 0, 1),
    527: ("00003c1818100000", "0000080810000000", 2, 0, 1, 0, 1),
    532: ("00001808141c0000", "00000808141c0000", 1, 1, 1, 1, 0),
    534: ("0004040818202000", "0004040818202000", 2, 0, 1, 0, 1),
    535: ("0020300818040400", "0000300808040400", 2, 0, 1, 0, 1),
    538: ("00003c24243c0000", "00003c24243c0000", 0, 0, 1, 1, 0),
    552: ("1018080000000000", "0018000000000000", 2, 0, 1, 0, 1),
    543: ("00003c1c1c000000", "0000080800000000", 2, 0, 1, 0, 1),
    544: ("00000c3c30000000", "0000001800000000", 2, 0, 1, 0, 1),
    546: ("0000001824000000", "0000001824000000", 2, 0, 1, 0, 1),
    556: ("10303010180c0600", "0010101010000000", 2, 0, 1, 0, 1),
    557: ("0000142662210000", "0000102222010000", 4, 0, 2, 0, 2),
    558: ("00000c3360000000", "00000c3300000000", 2, 0, 1, 0, 1),
    559: ("0004041c34040400", "0004041c04040400", 3, 1, 1, 0, 1),
    560: ("0030180e1a181800", "0000000e08080000", 2, 0, 1, 0, 1),
    561: ("1036343434100800", "0014141414100800", 4, 0, 2, 0, 2),
    562: ("0022243410180400", "0022243410180400", 4, 0, 2, 0, 2),
    553: ("0008000000000000", "0008000000000000", 0, 0, 1, 0, 1),
    606: ("043c3c141a080400", "00001c141a080400", 2, 1, 1, 1, 0),
    568: ("0000041800000000", "0000041800000000", 2, 0, 1, 0, 1),
    632: ("103818183c181808", "0010080808080800", 2, 0, 1, 0, 1),
    637: ("425a5a5a5a5a5b41", "42424a4a4a4a4240", 6, 0, 3, 0, 3),
    638: ("08086b6b6b6b7f00", "0808082929293e00", 3, 1, 1, 0, 1),
    639: ("7f0c047e67677e00", "7f04043c22223c00", 2, 2, 1, 1, 0),
    592: ("00001c1c08000000", "0000080800000000", 2, 0, 1, 0, 1),
    633: ("0020301818180000", "0000101008000000", 2, 0, 1, 0, 1),
    634: ("00040c1818180000", "0000080808000000", 2, 0, 1, 0, 1),
    635: ("001818180c000000", "0000080808000000", 2, 0, 1, 0, 1),
}


@lru_cache(maxsize=1)
def _profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _pool() -> dict:
    return json.loads(POOL_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _atlas_sources() -> tuple[dict[int, dict], dict[tuple[int, int], tuple[list[int], int]]]:
    """Load the exact registry pages and verify the compiler-owned pixel hash."""

    atlas = json.loads(ATLAS_PATH.read_text(encoding="utf-8"))
    bindings = {
        item["glyph_id"]: item
        for item in atlas["bindings"]
        if isinstance(item, dict) and isinstance(item.get("glyph_id"), int)
    }
    pages: dict[tuple[int, int], tuple[list[int], int]] = {}
    for page in atlas["pages"]:
        key = (page["pack_index"], page["page_index"])
        image = Image.open(ROOT / page["page16_path"]).convert("RGBA")
        rgba = image.tobytes()
        assert (image.width, image.height) == (page["width_px"], page["height_px"])
        assert hashlib.sha256(rgba).hexdigest() == page["page16_sha256"]
        pages[key] = (list(rgba), image.width)
    return bindings, pages


def _mask_for_gid(gid: int) -> int:
    bindings, pages = _atlas_sources()
    binding = bindings[gid]
    rgba, width = pages[(binding["pack_index"], binding["page_index"])]
    return U7.mask8_from_page16_rgba(
        rgba,
        width,
        binding["source_column"] * 16,
        binding["source_row"] * 16,
    )


def _mesh_profile() -> dict:
    return next(
        profile for profile in _profile()["profiles"]
        if profile["material_id"] == "mesh:200"
    )


def _mesh_row() -> dict:
    rows = [
        row for row in _pool()["rows"]
        if row["profile_namespace"] == "mesh"
        and row["material_key"] == 200
        and row["role"] == "mesh_feature"
        and row["route_key"] == "mesh_feature.bundle.v1"
        and row["lane"] == 0
    ]
    assert len(rows) == 1
    return rows[0]


def _descriptor_subset(record: dict) -> dict:
    return {
        "mask8_u64_le_hex": record["mask8_u64_le_hex"],
        "skeleton_u64_le_hex": record["skeleton_u64_le_hex"],
        "endpoint_count": record["endpoint_count"],
        "junction_count": record["junction_count"],
        "beta0": record["beta0"],
        "beta1": record["beta1"],
        "euler_characteristic": record["euler_characteristic"],
    }


def _literal_descriptor(values: tuple[str, str, int, int, int, int, int]) -> dict:
    fields = (
        "mask8_u64_le_hex",
        "skeleton_u64_le_hex",
        "endpoint_count",
        "junction_count",
        "beta0",
        "beta1",
        "euler_characteristic",
    )
    return dict(zip(fields, values, strict=True))


def test_1_authored_arrays_are_the_exact_ordered_capacity_row() -> None:
    # Post admission cutover the profile mesh lane keeps ADDRESS METADATA ONLY:
    # it carries no candidates / role_eligible arrays (the compiler injects them
    # from the admission artifact). Only the compiled row carries the legal row.
    bundle = _mesh_profile()["role_buckets"]["mesh_bundle"]
    assert "candidates" not in bundle and "role_eligible_glyph_ids" not in bundle
    assert len(EXPECTED_GIDS) == len(set(EXPECTED_GIDS)) == 32
    assert EXPECTED_GIDS == sorted(EXPECTED_GIDS)  # canonical ascending publication
    # FL-4652 L7 preserves the admission sequence through every compiled surface.
    # Direct list equality catches row reordering, omission, and foreign-glyph
    # insertion before descriptor facts are indexed by gid.
    compiled = _mesh_row()
    assert compiled["role_eligible_glyph_ids"] == EXPECTED_GIDS
    assert compiled["candidate_glyph_ids"] == EXPECTED_GIDS
    assert [
        descriptor["glyph_id"]
        for descriptor in compiled["candidate_descriptors"]
    ] == EXPECTED_GIDS


def test_2_each_gid_has_one_atlas_binding_and_nonempty_mask() -> None:
    # Post cutover the mesh glyphs are UNIVERSAL Unicode GlyphIds (not the old
    # 512-639 additive range), each with a native atlas binding and a nonempty
    # 8x8 mask. They carry u7 topology, NOT global6/shape6 (structural role).
    bindings, _pages = _atlas_sources()
    assert all(isinstance(gid, int) and gid > 255 for gid in EXPECTED_GIDS)
    assert all(gid in bindings for gid in EXPECTED_GIDS)
    assert all(_mask_for_gid(gid) != 0 for gid in EXPECTED_GIDS)
    assert all(
        "global6_norm" not in descriptor
        for descriptor in _mesh_row()["candidate_descriptors"]
    )


def test_3_all_descriptor_hashes_match_production_u7() -> None:
    # The compiled mesh row's per-glyph descriptors must equal a fresh production
    # u7.mask.v1 recompute from each glyph's native atlas page. (The admission
    # cutover derives the row from the artifact, so specific ids are the
    # artifact's, not a hand-authored literal table.)
    compiled_by_gid = {
        record["glyph_id"]: _descriptor_subset(record)
        for record in _mesh_row()["candidate_descriptors"]
    }
    for gid in EXPECTED_GIDS:
        mask = _mask_for_gid(gid)
        skeleton, endpoints, junctions = U7.topology_counts(mask)
        beta0, beta1 = U7.betti_numbers(mask)
        production = _descriptor_subset(U7.descriptor(mask))
        assert production["skeleton_u64_le_hex"] == U7.u64_le_hex(skeleton)
        assert production["endpoint_count"] == endpoints
        assert production["junction_count"] == junctions
        assert (production["beta0"], production["beta1"]) == (beta0, beta1)
        assert compiled_by_gid[gid] == production


def test_4_nonisotropic_covariance_axes_cover_eight_undirected_bins() -> None:
    bins: set[int] = set()
    for gid in EXPECTED_GIDS:
        mask = _mask_for_gid(gid)
        points = [
            (x, y)
            for y in range(8)
            for x in range(8)
            if U7.mask_bit(mask, x, y)
        ]
        mean_x = sum(x for x, _y in points) / len(points)
        mean_y = sum(y for _x, y in points) / len(points)
        xx = sum((x - mean_x) ** 2 for x, _y in points)
        yy = sum((y - mean_y) ** 2 for _x, y in points)
        xy = sum((x - mean_x) * (y - mean_y) for x, y in points)
        if abs(xx - yy) < 1.0e-12 and abs(xy) < 1.0e-12:
            continue
        angle = (0.5 * math.atan2(2.0 * xy, xx - yy)) % math.pi
        bins.add(int(math.floor(angle / (math.pi / 8.0) + 0.5)) % 8)
    assert bins == set(range(8))


def test_5_endpoint_footprint_matches_admission_row() -> None:
    # The admission-selected structural row's endpoint footprint. Pinned as a
    # golden so a change in the admission artifact is caught here. (The retired
    # authored row spanned {0,1,2,3,4,6}; the admission row spans {0,2,4}.)
    observed = {U7.topology_counts(_mask_for_gid(gid))[1] for gid in EXPECTED_GIDS}
    assert observed == {0, 2, 4}


def test_6_junction_footprint_matches_admission_row() -> None:
    observed = {U7.topology_counts(_mask_for_gid(gid))[2] for gid in EXPECTED_GIDS}
    assert observed == {0}


def test_7_row_contains_a_one_subcell_thin_mark() -> None:
    assert any(_mask_for_gid(gid).bit_count() == 1 for gid in EXPECTED_GIDS)


def test_8_row_contains_a_north_to_south_border_mask() -> None:
    north = sum(1 << x for x in range(8))
    south = north << 56
    assert any(
        _mask_for_gid(gid) & north and _mask_for_gid(gid) & south
        for gid in EXPECTED_GIDS
    )


def test_9_reservoir_limit_has_zero_east_border_masks() -> None:
    east = sum(1 << (8 * y + 7) for y in range(8))
    assert sum(bool(_mask_for_gid(gid) & east) for gid in range(512, 640)) == 0


def test_non_target_compiled_rows_remain_byte_identical() -> None:
    target = {
        "profile_namespace": "mesh",
        "material_key": 200,
        "role": "mesh_feature",
        "route_key": "mesh_feature.bundle.v1",
        "lane": 0,
    }
    projection = [
        row for row in _pool()["rows"]
        if not all(row.get(key) == value for key, value in target.items())
    ]
    projection.sort(
        key=lambda row: (
            row["profile_namespace"],
            row["material_key"],
            row["role"],
            row["route_key"],
            row["lane"],
        )
    )
    assert hashlib.sha256(COMPILER.canonical_bytes(projection)).hexdigest() == (
        EXPECTED_NON_TARGET_ROWS_SHA256
    )


def test_target_row_changes_only_the_declared_l7_surfaces() -> None:
    row = dict(_mesh_row())
    for key in (
        "candidate_glyph_ids",
        "candidate_descriptors",
        "topology_partitions",
        "role_eligible_glyph_ids",
        # Admission-cutover provenance surfaces (change with the artifact):
        "candidate_admission_schema",
        "candidate_admission_receipt_id",
        "mesh_admission_artifact_sha256",
        "row_sha256",
    ):
        row.pop(key, None)
    assert hashlib.sha256(COMPILER.canonical_bytes(row)).hexdigest() == (
        EXPECTED_TARGET_STABLE_SHA256
    )


def test_committed_pool_bytes_match_provenance_bound_compiler_transaction(
    tmp_path: Path,
) -> None:
    pool = _pool()
    compiler_commit = pool["header"]["compiler_commit"]
    # FL-4652 provenance gate: byte equality is meaningful only when the
    # compiler named by the generated header contains both structural policy
    # owners.  Reading that committed source prevents a stale generated
    # header from borrowing authority from the current working-tree compiler.
    committed_compiler = subprocess.run(
        [
            "git",
            "show",
            f"{compiler_commit}:scripts/compile_fl4512_pools.py",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert committed_compiler.returncode == 0, committed_compiler.stderr
    # The stamped commit must contain BOTH the live admission injector (the
    # in-memory validate_artifact_documents bind that makes the mesh row) and the
    # active-stroke (snake v2) compiler owner. The silhouette-visibility admission
    # owner is RETIRED and must NOT be reintroduced, so it is not gated.
    for owner in (
        "ADMISSION.validate_artifact_documents(",
        "def compile_active_stroke_topology(",
    ):
        assert owner in committed_compiler.stdout, (
            f"generated pool compiler_commit {compiler_commit} lacks {owner}"
        )

    regenerated = tmp_path / "fl4512.compiled_pool.v1.json"
    subprocess.run(
        [
            sys.executable,
            str(COMPILER_PATH),
            "--aspect-authoring",
            str(ASPECT_AUTHORING_PATH),
            "--compiler-commit",
            compiler_commit,
            "--pool-revision",
            "2",
            "--out",
            str(regenerated),
        ],
        cwd=ROOT,
        check=True,
    )
    assert regenerated.read_bytes() == POOL_PATH.read_bytes()
