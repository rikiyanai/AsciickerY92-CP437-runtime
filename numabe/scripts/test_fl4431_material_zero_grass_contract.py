#!/usr/bin/env python3
"""Guard Product's single material-family authority for terrain foliage.

The typed terrain classifier names materials 0, 1, and 3 as grass. Water is
excluded independently by the selected surface height. A second raw-material
allowlist would silently remove material-0 grass and recreate two competing
owners for the same foliage admission decision.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def function_body(source: str, name: str) -> str:
    """Extract one GLSL function body so unrelated text cannot satisfy a pin."""

    signature = re.search(r"\b" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    if signature is None:
        return ""
    open_brace = source.find("{", signature.start())
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index]
    return ""


def contract(source: str) -> tuple[str, ...]:
    """Return every violation in the one-owner foliage admission law."""

    body = function_body(source, "foliage_surface_facts")
    failures: list[str] = []
    if not body:
        return ("foliage_surface_facts is missing",)
    family_assignment = re.search(
        r"f\.proven_grass\s*=\s*"
        r"\(\s*f\.material_family\s*==\s*TERRAIN_FAMILY_GRASS\s*\)"
        r"\s*&&\s*!\s*water_by_height\s*;",
        body,
    )
    if family_assignment is None:
        failures.append("typed grass-family plus height admission is missing")
    if len(re.findall(r"f\.proven_grass\s*=\s*false\s*;", body)) != 1:
        failures.append("a second proven-grass rejection owner exists")
    if re.search(r"\bmat\s*!=\s*1\s*&&\s*mat\s*!=\s*3\b", body):
        failures.append("material-0 grass suppression was resurrected")
    return tuple(failures)


def test_shipping_source_uses_typed_material_family_once() -> None:
    """Require material 0 to reach foliage through the shared classifier."""

    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert contract(source) == ()


def test_material_zero_resuppression_mutant_turns_red() -> None:
    """Prove the deleted material-1/3 allowlist cannot silently return."""

    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    admission = (
        "\tf.proven_grass = (f.material_family == TERRAIN_FAMILY_GRASS) "
        "&& !water_by_height;\n"
    )
    resurrection = (
        admission
        + "\tif (mat != 1 && mat != 3) f.proven_grass = false;\n"
    )
    assert source.count(admission) == 1
    mutant = source.replace(admission, resurrection, 1)
    failures = contract(mutant)
    assert "material-0 grass suppression was resurrected" in failures
    assert "a second proven-grass rejection owner exists" in failures


def main() -> int:
    """Run the contract as a repository front door."""

    test_shipping_source_uses_typed_material_family_once()
    test_material_zero_resuppression_mutant_turns_red()
    print("FL4431_MATERIAL_ZERO_GRASS_CONTRACT_PASS mutants=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
