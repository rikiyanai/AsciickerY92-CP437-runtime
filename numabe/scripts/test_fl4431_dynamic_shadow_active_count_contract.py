#!/usr/bin/env python3
# =============================================================================
# test_fl4431_dynamic_shadow_active_count_contract.py
# =============================================================================
# Binding 81 stores one retained dynamic-mover record. Its aabb_min.w lane is
# now an exact count of kind-1 shadow faces in that mover. Product may use that
# count only to avoid loading binding-74 rows that cannot affect dynamic shadow
# density. Airplane face election belongs to VSB and never reads this count.
#
# This contract checks all three owners of that fact:
#   SceneGeometryDB derives the count from validated face rows and serializes it;
#   ASCIICompositorEffect validates the serialized lane against the CPU mirror;
#   Product rejects malformed counts and exhausts its shadow-only scan.
#
# Run directly:
#   python3 scripts/test_fl4431_dynamic_shadow_active_count_contract.py
# Run through pytest:
#   python3 -m pytest scripts/test_fl4431_dynamic_shadow_active_count_contract.py -q
# =============================================================================

"""Guard the exact b81 dynamic-shadow active-face count contract."""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import math
import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PRODUCT_PATH = REPO / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
HOST_PATH = (
    REPO
    / "godot_project"
    / "render" / "api" / "ASCIICompositorEffect.gd"
)
SCENE_DB_PATH = (
    REPO / "godot_project" / "render" / "scene" / "SceneGeometryDB.gd"
)


def _strip_glsl_comments(source: str) -> str:
    """Remove prose so an explanatory sentence cannot satisfy a code law."""

    without_blocks = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", without_blocks)


def _strip_gd_comments(source: str) -> str:
    """Remove GDScript comments while preserving executable indentation."""

    return re.sub(r"#[^\n]*", " ", source)


def _compact(source: str) -> str:
    """Collapse formatting while retaining every executable token."""

    return re.sub(r"\s+", "", source)


def _extract_glsl_function(source: str, function_name: str) -> str | None:
    """Return one complete GLSL function using balanced braces."""

    signature = re.search(
        r"\b\w+(?:\s+\w+)*\s+"
        + re.escape(function_name)
        + r"\s*\([^;]*?\)\s*\{",
        source,
        re.DOTALL,
    )
    if signature is None:
        return None
    opening = source.find("{", signature.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[signature.start() : index + 1]
    return None


def _extract_gd_function(source: str, function_name: str) -> str | None:
    """Return one top-level GDScript function through the next declaration."""

    start = re.search(
        r"^(?:static\s+)?func\s+" + re.escape(function_name) + r"\b",
        source,
        re.MULTILINE,
    )
    if start is None:
        return None
    end = start.start()
    declaration_depth = 0
    declaration_finished = False
    for line in source[start.start() :].splitlines(keepends=True):
        declaration_depth += line.count("(") - line.count(")")
        end += len(line)
        if declaration_depth == 0 and line.rstrip().endswith(":"):
            declaration_finished = True
            break
    if not declaration_finished:
        return None
    for line in source[end:].splitlines(keepends=True):
        if line.strip() and not line[0].isspace():
            break
        end += len(line)
    return source[start.start() : end]


def _extract_braced_block(source: str, start_token: str) -> str | None:
    """Return the first balanced block whose header contains start_token."""

    start = source.find(start_token)
    if start < 0:
        return None
    opening = source.find("{", start + len(start_token))
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return None


def contract_violations(
    product_source: str,
    host_source: str,
    scene_db_source: str,
) -> tuple[str, ...]:
    """Return every broken producer, validator, and Product-consumer law."""

    violations: list[str] = []
    product_code = _strip_glsl_comments(product_source)
    function = _extract_glsl_function(
        product_code, "vsb_retained_consider_dynamic_shadow_mover"
    )
    if function is None:
        return ("Product dynamic-mover retained selector is missing",)
    compact = _compact(function)

    lane_decode = "floatencoded_shadow_face_count=mover.aabb_min.w;"
    if lane_decode not in compact:
        violations.append(
            "Product must decode the b81 shadow count from aabb_min.w"
        )

    malformed_terms = (
        "isnan(encoded_shadow_face_count)",
        "isinf(encoded_shadow_face_count)",
        "encoded_shadow_face_count<0.0",
        "floor(encoded_shadow_face_count)!=encoded_shadow_face_count",
        "encoded_shadow_face_count>float(face_count)",
    )
    if not all(term in compact for term in malformed_terms):
        violations.append("Product must reject every malformed b81 shadow count")
    malformed_start = compact.find("if(isnan(encoded_shadow_face_count)")
    conversion = compact.find(
        "shadow_faces_remaining=uint(encoded_shadow_face_count);"
    )
    if (
        malformed_start < 0
        or conversion < 0
        or "return;" not in compact[malformed_start:conversion]
    ):
        violations.append(
            "Product malformed-count branch must return rather than clamp"
        )
    if any(
        token in compact
        for token in (
            "clamp(encoded_shadow_face_count",
            "min(encoded_shadow_face_count",
            "max(encoded_shadow_face_count",
        )
    ):
        violations.append("Product must not clamp a malformed shadow count")
    if "if(shadow_faces_remaining==0u)return;" not in compact:
        violations.append("Product zero-count return must precede shadow traversal")

    for retired in (
        "selection_mode",
        "vsb_retained_select_airplane_hit(",
        "vsb_retained_select_dynamic_candidate(",
    ):
        if retired in product_code:
            violations.append(f"retired Product airplane selector survived: {retired}")

    positions = {
        "decode": compact.find(lane_decode),
        "convert": compact.find(
            "shadow_faces_remaining=uint(encoded_shadow_face_count);"
        ),
        "zero": compact.find("if(shadow_faces_remaining==0u)return;"),
        "aabb": compact.find(
            "vsb_retained_dynamic_shadow_node_can_contribute("
        ),
        "loop": compact.find("for(uintlocal_face=0u;local_face<face_count;"),
        "exhaust": compact.find("if(shadow_faces_remaining==0u)break;"),
        "load": compact.find("vsb_retained_dynamic_face_by_index("),
    }
    if not (
        0 <= positions["decode"]
        < positions["convert"]
        < positions["zero"]
        < positions["aabb"]
        < positions["loop"]
        < positions["exhaust"]
        < positions["load"]
    ):
        violations.append(
            "Product zero count and exhaustion must precede the first b74 row load"
        )

    if "for(uintlocal_face=0u;local_face<face_count;++local_face)" not in compact:
        violations.append(
            "Product mover traversal must retain face_count as its range bound"
        )

    load_position = positions["load"]
    kind_position = compact.find(
        "if(facts.actor_effect_kind!=1u)continue;", load_position
    )
    decrement_position = compact.find(
        "shadow_faces_remaining-=1u;", load_position
    )
    if not (
        load_position >= 0
        and kind_position > load_position
        and decrement_position > kind_position
    ):
        violations.append(
            "Product must filter each loaded row to kind 1 before decrementing"
        )

    db_code = _strip_gd_comments(scene_db_source)
    setter = _extract_gd_function(db_code, "set_dynamic_mover_descriptors")
    serializer = _extract_gd_function(
        db_code, "build_vsb_semantic_dynamic_fact_bindings"
    )
    if setter is None:
        violations.append("SceneGeometryDB dynamic setter is missing")
    else:
        setter_compact = _compact(setter)
        if (
			'ifnot(source.get("shadow_face_count")isint):' not in setter_compact
			or 'varshadow_face_count:=int(source["shadow_face_count"])'
			not in setter_compact
			or "shadow_face_count<0orshadow_face_count>mover_face_count"
			not in setter_compact
        ):
            violations.append(
				"SceneGeometryDB must type-check and bound the producer-owned count"
            )
    if serializer is None:
        violations.append("SceneGeometryDB dynamic serializer is missing")
    else:
        serializer_compact = _compact(serializer)
        if (
            "dynamic_instances.encode_float(record_base+28,float(shadow_face_count))"
            not in serializer_compact
            or "dynamic_shadow_face_counts[pinned_index]=shadow_face_count"
            not in serializer_compact
        ):
            violations.append(
                "SceneGeometryDB must serialize byte 28 and its exact CPU mirror"
            )

    host_code = _strip_gd_comments(host_source)
    validator = _extract_gd_function(
        host_code, "_validate_vsb_semantic_dynamic_publication"
    )
    if validator is None:
        violations.append("host dynamic semantic publication validator is missing")
    else:
        validator_compact = _compact(validator)
        host_terms = (
            "instances.decode_float(instance_base+28)",
            "notis_finite(packed_shadow_count)",
            "packed_shadow_count<0.0",
            "packed_shadow_count!=floorf(packed_shadow_count)",
            "packed_shadow_count>float(range_face_count)",
            "int(packed_shadow_count)!=shadow_face_counts[instance_index]",
        )
        if not all(term in validator_compact for term in host_terms):
            violations.append(
                "host must reject malformed byte-28 counts and compare the exact mirror"
            )

    return tuple(dict.fromkeys(violations))


def model_loaded_rows(
    encoded_shadow_count: float,
    row_kinds: tuple[int, ...],
) -> tuple[int, ...]:
    """Model which b74 rows the reviewed count law permits Product to load."""

    if (
        not math.isfinite(encoded_shadow_count)
        or encoded_shadow_count < 0.0
        or encoded_shadow_count != math.floor(encoded_shadow_count)
        or encoded_shadow_count > len(row_kinds)
    ):
        return ()
    remaining = int(encoded_shadow_count)
    if remaining == 0:
        return ()
    loaded: list[int] = []
    for index, kind in enumerate(row_kinds):
        if remaining == 0:
            break
        loaded.append(index)
        if kind == 1:
            remaining -= 1
    return tuple(loaded)


def destructive_mutants(product_source: str) -> dict[str, str]:
    """Return eight executable source damages required by the review."""

    function = _extract_glsl_function(
        product_source, "vsb_retained_consider_dynamic_shadow_mover"
    )
    if function is None:
        raise AssertionError("Product dynamic mover function missing")

    def replace_function(mutated_function: str) -> str:
        if product_source.count(function) != 1:
            raise AssertionError("dynamic mover function must be unique")
        return product_source.replace(function, mutated_function, 1)

    zero_line = "\tif (shadow_faces_remaining == 0u) return;\n"
    b74_line = (
        "\t\tif (!vsb_retained_dynamic_face_by_index("
        "legacy_face, facts, retained_anchor)) continue;\n"
    )
    malformed_block = (
        "\tif (isnan(encoded_shadow_face_count)\n"
        "\t\t\t|| isinf(encoded_shadow_face_count)\n"
        "\t\t\t|| encoded_shadow_face_count < 0.0\n"
        "\t\t\t|| floor(encoded_shadow_face_count)\n"
        "\t\t\t\t!= encoded_shadow_face_count\n"
        "\t\t\t|| encoded_shadow_face_count > float(face_count)) {\n"
        "\t\treturn;\n"
        "\t}\n"
    )
    for anchor in (zero_line, b74_line, malformed_block):
        if function.count(anchor) != 1:
            raise AssertionError(f"dynamic count mutation anchor drifted: {anchor!r}")

    without_zero = function.replace(zero_line, "", 1)
    zero_after_load = without_zero.replace(
        b74_line, b74_line + zero_line, 1
    )
    clamped = function.replace(
        malformed_block,
        "\tencoded_shadow_face_count = clamp(\n"
        "\t\tencoded_shadow_face_count, 0.0, float(face_count));\n",
        1,
    )
    row_kind_line = "\t\tif (facts.actor_effect_kind != 1u) continue;\n"
    early_exhaustion = "\t\tif (shadow_faces_remaining == 0u) break;\n"
    if function.count(row_kind_line) != 1:
        raise AssertionError("dynamic kind-1 row filters drifted")
    if function.count(early_exhaustion) != 1:
        raise AssertionError("dynamic early-exhaustion anchor drifted")

    return {
        "zero_return_removed": replace_function(
            function.replace(zero_line, "", 1)
        ),
        "zero_return_after_first_b74_load": replace_function(zero_after_load),
        "malformed_count_clamped": replace_function(clamped),
        "wrong_aabb_lane": replace_function(
            function.replace(
                "mover.aabb_min.w", "mover.aabb_max.w", 1
            )
        ),
        "row_kind1_filter_removed": replace_function(
            function.replace(row_kind_line, "", 1)
        ),
        "airplane_selector_reintroduced": replace_function(
            function.replace(
                "uint first_face = mover.identity_range.y;",
                "vsb_retained_select_airplane_hit(ViewRay());\n"
                "\tuint first_face = mover.identity_range.y;",
                1,
            )
        ),
        "face_count_substituted": replace_function(
            function.replace(
                "local_face < face_count; ++local_face",
                "local_face < shadow_faces_remaining; ++local_face",
                1,
            )
        ),
        "early_exhaustion_removed": replace_function(
            function.replace(early_exhaustion, "", 1)
        ),
    }


def _read_sources() -> tuple[str, str, str]:
    """Read the three live inputs once for deterministic focused checks."""

    return (
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8", errors="replace")),
        HOST_PATH.read_text(encoding="utf-8", errors="replace"),
        SCENE_DB_PATH.read_text(encoding="utf-8", errors="replace"),
    )


def test_dynamic_shadow_active_count_contract() -> None:
    """The current producer, upload validator, and consumer must agree."""

    product, host, scene_db = _read_sources()
    assert contract_violations(product, host, scene_db) == ()


def test_dynamic_shadow_active_count_model() -> None:
    """Zero/malformed counts avoid loads; sparse kind-1 rows exhaust exactly."""

    rows = (4, 1, 4, 4, 1, 4)
    assert model_loaded_rows(0.0, rows) == ()
    assert model_loaded_rows(-1.0, rows) == ()
    assert model_loaded_rows(1.5, rows) == ()
    assert model_loaded_rows(2.0, rows) == (0, 1, 2, 3, 4)


def test_dynamic_shadow_active_count_destructive_mutants() -> None:
    """Every required executable damage must independently turn the law red."""

    product, host, scene_db = _read_sources()
    mutants = destructive_mutants(product)
    assert set(mutants) == {
        "zero_return_removed",
        "zero_return_after_first_b74_load",
        "malformed_count_clamped",
        "wrong_aabb_lane",
        "row_kind1_filter_removed",
        "airplane_selector_reintroduced",
        "face_count_substituted",
        "early_exhaustion_removed",
    }
    for name, mutant in mutants.items():
        assert contract_violations(mutant, host, scene_db), (
            f"destructive mutant stayed green: {name}"
        )


def main() -> int:
    """Run the focused contract without requiring pytest."""

    product, host, scene_db = _read_sources()
    violations = contract_violations(product, host, scene_db)
    mutants = destructive_mutants(product)
    survived = tuple(
        name
        for name, mutant in mutants.items()
        if not contract_violations(mutant, host, scene_db)
    )
    if violations or survived:
        print("FL-4431 dynamic-shadow active-count contract FAILED")
        for violation in violations:
            print(f"  - {violation}")
        for name in survived:
            print(f"  - destructive mutant survived: {name}")
        return 1
    print(
        "FL4431_DYNAMIC_SHADOW_ACTIVE_COUNT_CONTRACT_PASS "
        f"mutants={len(mutants)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
