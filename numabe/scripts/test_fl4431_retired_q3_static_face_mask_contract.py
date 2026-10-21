#!/usr/bin/env python3
"""FL-4431 retired Q3 static-face mask deletion contract.

Product binding 61 is a 128-word storage buffer whose host owner fills every
byte with 0xFF exactly once and never updates it. Its only shader consumer is
the retained static-shadow admission helper. For legacy face ids 0 through
2,047, that all-ones bit test is therefore the identity operation; the point
light and sun selectors already bypass it.

This suite is deliberately red-first. The exhaustive semantic witness and both
resurrection mutants pass now. One aggregate source-shape finding remains red
until Product's b61 declaration/read plus the compositor's allocation/binding/
cleanup owner are physically deleted. Static evidence does not establish
runtime rendering, timing, visual parity, operator acceptance, and closure.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
HOST_PATH = (
    ROOT
    / "godot_project"
    / "render" / "api" / "ASCIICompositorEffect.gd"
)

LEGACY_FACE_LIMIT = 2048
MASK_WORD_BASE = 64
MASK_WORD_COUNT = 128
PATHS = {
    "q3": True,
    "point_light": False,
    "sun": False,
}


def compact(value: str) -> str:
    """Remove spacing so formatting changes cannot weaken structural pins."""
    return re.sub(r"\s+", "", value)


def extract_braced_function(source: str, name: str) -> str:
    """Return one complete GLSL/GDScript function using balanced braces."""
    glsl = re.search(
        r"\b[\w\s]+\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
        source,
        re.S,
    )
    if glsl is not None:
        brace = source.find("{", glsl.start())
        depth = 0
        for index in range(brace, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[glsl.start() : index + 1]
    return ""


def old_admitted(
    legacy_face_index: int,
    receiver_face_index: int,
    scan_cap: int,
    require_q3_admission: bool,
    mask_words: tuple[int, ...],
) -> bool:
    """Mirror the shipping helper, including its retired all-ones mask read."""
    if scan_cap <= 0:
        return False
    if legacy_face_index >= scan_cap:
        return False
    if legacy_face_index == receiver_face_index:
        return False
    if require_q3_admission and legacy_face_index < LEGACY_FACE_LIMIT:
        word = mask_words[
            MASK_WORD_BASE + (legacy_face_index >> 5)
        ]
        bit = 1 << (legacy_face_index & 31)
        if (word & bit) == 0:
            return False
    return True


def replacement_admitted(
    legacy_face_index: int,
    receiver_face_index: int,
    scan_cap: int,
) -> bool:
    """Model the retained helper after deleting the identity mask operation."""
    if scan_cap <= 0:
        return False
    if legacy_face_index >= scan_cap:
        return False
    if legacy_face_index == receiver_face_index:
        return False
    return True


def semantic_witness() -> int:
    """Prove all admitted legacy ids are identical on Q3, point, and sun."""
    full_mask = tuple(0xFFFFFFFF for _ in range(MASK_WORD_COUNT))
    comparisons = 0
    for legacy_face_index in range(LEGACY_FACE_LIMIT):
        for require_q3_admission in PATHS.values():
            old = old_admitted(
                legacy_face_index,
                -1,
                LEGACY_FACE_LIMIT,
                require_q3_admission,
                full_mask,
            )
            new = replacement_admitted(
                legacy_face_index,
                -1,
                LEGACY_FACE_LIMIT,
            )
            assert old
            assert new
            assert old == new
            comparisons += 1

    # Keep the non-mask admission laws in the witness. Deleting b61 must not
    # admit the receiver itself, widen the 2,048-face cap, and revive cap zero.
    for require_q3_admission in PATHS.values():
        assert not old_admitted(
            77, 77, LEGACY_FACE_LIMIT, require_q3_admission, full_mask
        )
        assert not replacement_admitted(77, 77, LEGACY_FACE_LIMIT)
        assert not old_admitted(
            LEGACY_FACE_LIMIT,
            -1,
            LEGACY_FACE_LIMIT,
            require_q3_admission,
            full_mask,
        )
        assert not replacement_admitted(
            LEGACY_FACE_LIMIT,
            -1,
            LEGACY_FACE_LIMIT,
        )
        assert not old_admitted(
            0, -1, 0, require_q3_admission, full_mask
        )
        assert not replacement_admitted(0, -1, 0)
    return comparisons


def source_absence_findings(product: str, host: str) -> list[str]:
    """Return every surviving member of the retired b61 ownership family."""
    findings: list[str] = []
    product_patterns = {
        "Product binding 61 declaration": r"binding\s*=\s*61\b",
        "Product b61 block type": r"\bQ3StaticFaceCullMasks\b",
        "Product b61 resource name": r"\bq3_static_face_cull_masks\b",
        "Product mask-only control": r"\brequire_q3_admission\b",
    }
    host_patterns = {
        "host b61 RID": r"\b_vsb_product_q3_static_face_masks_rd\b",
        "host b61 byte constant": r"\bVSB_PRODUCT_Q3_STATIC_FACE_MASK_BYTES\b",
        "host b61 local uniform": r"\bu_q3_static_face_masks\b",
        "host all-ones mask allocation": (
            r"admitted_masks\s*\.\s*fill\s*\(\s*0xFF\s*\)"
        ),
        "host binding 61 construction": (
            r"_probe_u_storage\s*\(\s*61\s*,"
        ),
    }
    for label, pattern in product_patterns.items():
        if re.search(pattern, product):
            findings.append(label)
    for label, pattern in host_patterns.items():
        if re.search(pattern, host):
            findings.append(label)

    helper = compact(
        extract_braced_function(
            product,
            "vsb_retained_static_legacy_candidate_admitted",
        )
    )
    if not helper:
        findings.append("retained static admission helper missing")
    else:
        required = (
            "scan_cap<=0",
            "primitive_id>=instance.refs.w",
            "legacy_face_index=instance.refs.z+primitive_id",
            "legacy_face_index>=uint(scan_cap)",
            "int(legacy_face_index)==receiver_face_idx",
            "returntrue;",
        )
        for token in required:
            if token not in helper:
                findings.append(
                    f"retained non-mask admission law missing: {token}"
                )
        if (
            "legacy_face_index>>5u" in helper
            and "legacy_face_index&31u" in helper
        ):
            findings.append("legacy-face mask bit branch survived")

    wrappers = (
        "vsb_retained_select_point_light_caster",
        "vsb_retained_select_sun_shadow_caster",
        "vsb_retained_select_q3_static_shadow_caster",
    )
    for wrapper_name in wrappers:
        wrapper = compact(extract_braced_function(product, wrapper_name))
        if not wrapper:
            findings.append(f"{wrapper_name} missing")
            continue
        if wrapper.count("vsb_retained_select_static_shadow_caster(") != 1:
            findings.append(
                f"{wrapper_name} must call the retained selector exactly once"
            )
    return findings


POST_DELETE_PRODUCT_FIXTURE = """
bool vsb_retained_static_legacy_candidate_admitted(
    VsbSemanticStaticInstance instance, uint primitive_id,
    int receiver_face_idx, int scan_cap
) {
    if (scan_cap <= 0 || primitive_id >= instance.refs.w) return false;
    uint legacy_face_index = instance.refs.z + primitive_id;
    if (legacy_face_index >= uint(scan_cap)
            || int(legacy_face_index) == receiver_face_idx) return false;
    return true;
}
VsbRetainedStaticCasterSelection vsb_retained_select_point_light_caster(
    ViewRay ray, int receiver
) {
    return vsb_retained_select_static_shadow_caster(
        ray, receiver, FL4270_STATIC_FACE_SCAN_MAX);
}
VsbRetainedStaticCasterSelection vsb_retained_select_sun_shadow_caster(
    ViewRay ray, int receiver, int cap
) {
    return vsb_retained_select_static_shadow_caster(ray, receiver, cap);
}
VsbRetainedStaticCasterSelection vsb_retained_select_q3_static_shadow_caster(
    ViewRay ray, int receiver
) {
    return vsb_retained_select_static_shadow_caster(
        ray, receiver, FL4270_STATIC_FACE_SCAN_MAX);
}
"""

POST_DELETE_HOST_FIXTURE = """
var _vsb_product_q3_diagnostics_rd: RID
var _vsb_product_q3_origins_rd: RID
func _bind_product_read_facts() -> void:
    var u_q3_diagnostics := _probe_u_storage(62, _vsb_product_q3_diagnostics_rd)
    var u_q3_origins := _probe_u_ubo(66, _vsb_product_q3_origins_rd)
"""


def mutation_witness() -> int:
    """Prove resource and branch resurrection independently turn the gate red."""
    assert not source_absence_findings(
        POST_DELETE_PRODUCT_FIXTURE,
        POST_DELETE_HOST_FIXTURE,
    )

    resource_product = (
        "layout(set = 0, binding = 61, std430) readonly buffer "
        "Q3StaticFaceCullMasks { uint words[]; } "
        "q3_static_face_cull_masks;\n"
        + POST_DELETE_PRODUCT_FIXTURE
    )
    resource_host = POST_DELETE_HOST_FIXTURE + """
var _vsb_product_q3_static_face_masks_rd: RID
const VSB_PRODUCT_Q3_STATIC_FACE_MASK_BYTES := 128 * 4
func _restore_b61() -> void:
    var admitted_masks := PackedByteArray()
    admitted_masks.resize(VSB_PRODUCT_Q3_STATIC_FACE_MASK_BYTES)
    admitted_masks.fill(0xFF)
    var u_q3_static_face_masks := _probe_u_storage(
        61, _vsb_product_q3_static_face_masks_rd)
"""
    assert source_absence_findings(resource_product, resource_host)

    branch_product = POST_DELETE_PRODUCT_FIXTURE.replace(
        "    return true;\n}",
        "    if (legacy_face_index < 2048u\n"
        "            && (retired_static_face_mask_word(\n"
        "                64u + (legacy_face_index >> 5u))\n"
        "                & (1u << (legacy_face_index & 31u))) == 0u) {\n"
        "        return false;\n"
        "    }\n"
        "    return true;\n}",
        1,
    )
    assert branch_product != POST_DELETE_PRODUCT_FIXTURE
    assert source_absence_findings(
        branch_product,
        POST_DELETE_HOST_FIXTURE,
    )
    return 2


def main() -> int:
    comparisons = semantic_witness()
    mutations = mutation_witness()
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    host = HOST_PATH.read_text(encoding="utf-8")
    source_findings = source_absence_findings(product, host)
    print(
        "FL4431_RETIRED_Q3_STATIC_FACE_MASK_SEMANTIC_PASS "
        f"faces={LEGACY_FACE_LIMIT} paths={len(PATHS)} "
        f"comparisons={comparisons}"
    )
    print(
        "FL4431_RETIRED_Q3_STATIC_FACE_MASK_MUTANTS_PASS "
        f"caught={mutations}"
    )
    if source_findings:
        print(
            "FL4431_RETIRED_Q3_STATIC_FACE_MASK_CONTRACT_FAIL "
            "source_shape_failures=1"
        )
        print(
            "  - retired Product b61 owner remains: "
            + ", ".join(source_findings)
        )
        return 1
    print(
        "FL4431_RETIRED_Q3_STATIC_FACE_MASK_CONTRACT_PASS "
        "source_shape_failures=0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
