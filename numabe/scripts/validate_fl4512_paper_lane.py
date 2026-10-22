#!/usr/bin/env python3
"""Validate the normative FL-4512 GlyphTransformRelation v2 paper amendment.

This is a documentation gate, not an implementation of the renderer.  It keeps
the manuscript's successor schema, arithmetic, cardinalities, source anchors,
and explicit open status together so later edits cannot silently turn the
specification into a runtime claim.  The validator also reads the current V1
source constants and admitted compiler row.  Those reads establish that the
paper accurately describes the cut boundary; they do not bless V1 behavior.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEX_PATH = (
    ROOT
    / "docs/agent/glyph-rendering-paper/"
    "2026-07-07-fl4512-first-rough-draft.tex"
)
NOTE_PATH = (
    ROOT
    / "docs/agent/glyph-rendering-paper/"
    "2026-07-17-fl4512-transform-relation-affine-generalization.md"
)
RUNTIME_HEADER_PATH = ROOT / "engine/render/fl4512_spacetime_track_owner.h"
MASK_COMPILER_PATH = ROOT / "scripts/fl4512_u7_mask.py"
ADMISSION_PATH = (
    ROOT
    / "assets/glyphs/generated/"
    "fl4512.mesh_feature_candidate_admission.v2.json"
)


# Each value is the maximum integer contribution published by the manuscript.
# Their sum is checked because the paper promises a bounded 448-unit component
# subtotal before confidence scaling and the explicit unexplained term.
COMPONENT_WEIGHTS = {
    "affine-mask XOR": 80,
    "density delta": 12,
    "endpoint delta": 18,
    "junction delta": 18,
    "beta0 delta": 16,
    "beta1 delta": 16,
    "Euler delta": 8,
    "orientation-pair": 28,
    "path delta": 18,
    "cycle delta": 18,
    "curvature delta": 20,
    "convexity delta": 16,
    "declared-reflection XOR": 24,
    "nearest-C4 XOR": 24,
    "determinant": 20,
    "fit residual": 32,
    "involution residual": 32,
    "observed-offset residual": 48,
}


# These are existing source values, not newly selected documentation constants.
# The paper must preserve them unless it explicitly names a later U6 ABI cut.
ENERGY_CONSTANTS = {
    "kFl4512LambdaSpatialV2": 70,
    "kFl4512LambdaIdentityV2": 40,
    "kFl4512LambdaTransformV2": 1,
    "kFl4512LambdaTopologyV2": 25,
    "kFl4512UnexplainedSwitchPenaltyV2": 200,
}


def _line_number(text: str, needle: str) -> int:
    """Return the one-based line containing one unique manuscript anchor."""

    matches = [
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if needle in line
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one line for {needle!r}, found {len(matches)}"
        )
    return matches[0]


def _require(text: str, needle: str, failures: list[str]) -> None:
    """Append a stable diagnostic when a normative phrase is absent."""

    normalized_text = " ".join(text.split())
    normalized_needle = " ".join(needle.split())
    if needle not in text and normalized_needle not in normalized_text:
        failures.append(f"missing:{needle}")


def validate_contract() -> tuple[list[str], dict[str, int]]:
    """Validate the paper, derivation note, current constants, and row shape.

    Returns a list of human-readable failures plus useful cardinality metadata.
    Keeping the pure result separate from ``main`` lets focused tests mutate one
    fact without shelling out to the command-line program.
    """

    failures: list[str] = []
    tex = TEX_PATH.read_text(encoding="utf-8")
    note = NOTE_PATH.read_text(encoding="utf-8")
    header = RUNTIME_HEADER_PATH.read_text(encoding="utf-8")
    mask_compiler = MASK_COMPILER_PATH.read_text(encoding="utf-8")
    admission = json.loads(ADMISSION_PATH.read_text(encoding="utf-8"))

    # The exact source span is part of the handoff contract.  It prevents a
    # review note from pointing at a stale manuscript location after edits.
    try:
        start_line = _line_number(
            tex,
            r"\subsubsection{Transitional view conditioned "
            r"\texttt{GlyphTransformRelation} v2}",
        )
        end_line = _line_number(
            tex, "Those terms are this renderer's authored adaptation"
        ) + 1
    except ValueError as error:
        failures.append(str(error))
        start_line = 0
        end_line = 0
    if start_line and f"TeX lines {start_line}--{end_line}" not in note:
        failures.append("affine-note TeX line citation is stale")

    # These anchors cover the successor identity, all provenance hashes, exact
    # shape, scalar formula, contribution boundary, ownership split, and deletion boundary.
    required_tex = (
        "It is not a reinterpretation of",
        r"H_{\mathrm{row}},H_{\mathrm{font}},H_{\mathrm{raster}},H_{\mathrm{atlas}}",
        r"H_{\mathrm{descriptor}},H_{\mathrm{aspect}},H_{\mathrm{profile}}",
        r"H_{\mathrm{schema}}",
        "$32^2=1024$ relation rows per aspect",
        "$64\\cdot1024=65536$ relation rows",
        r"\operatorname{cycles}_g=E_g-V_g+k_g",
        r"\operatorname{paths}_g",
        r"\operatorname{curv}_g",
        r"\operatorname{conv}_g",
        "four reflection masks and four $C_4$ masks",
        r"S_a=A_aR_2A_a^{-1}",
        r"q_{\mathrm{eff}}=\min",
        r"C_{\mathrm{transform}}(a,g,h)=",
        r"=(70,40,1,25,200)",
        "lower transform index breaking equal minima",
        "lower \\GlyphId{}",
        "Raster similarity never creates track identity",
        "empty V1 constructor",
        "flat unexplained switch penalty",
        "Those terms are this renderer's authored adaptation",
        "discrete glyph transition cost",
        "identity pair zero cost",
        "one oblique affine relation",
        "one missing observed offset",
        "one matrix multiply overflow",
    )
    for needle in required_tex:
        _require(tex, needle, failures)

    required_note = (
        "# GlyphTransformRelationV2",
        "SPECIFIED / NOT_IMPLEMENTED / PROOF_OPEN",
        "successor schema",
        "delete the V1 row, table, constructor, lookup, FNV seal",
    )
    for needle in required_note:
        _require(note, needle, failures)

    # Check the exact current energy constants directly rather than trusting a
    # prose transcription.  A source change now requires an explicit paper ABI
    # reconciliation instead of silently drifting.
    for symbol, expected in ENERGY_CONSTANTS.items():
        pattern = rf"{re.escape(symbol)}\s*=\s*{expected}u"
        if not re.search(pattern, header):
            failures.append(f"source constant mismatch:{symbol}={expected}")

    # The V2 descriptor derives from the frozen U7 mask semantics.  These are
    # source-presence checks only; no new descriptor code is installed here.
    for needle in (
        'SCORER_ID = "u7.mask.v1"',
        'REDUCTION_VERSION = "coverage_ge_2.v1"',
        'SKELETON_VERSION = "zhang_suen.v1"',
        "def topology_counts(",
        "def betti_numbers(",
    ):
        _require(mask_compiler, needle, failures)

    selected = admission.get("receipt", {}).get("selected_glyph_ids", [])
    descriptor_hashes = admission.get("receipt", {}).get(
        "selected_descriptor_hashes", []
    )
    selected_row = admission.get("selected_row", [])
    if len(selected) != 32 or len(set(selected)) != 32:
        failures.append("admitted row is not 32 unique GlyphIds")
    if selected != sorted(selected):
        failures.append("admitted row is not lower-GlyphId ordered")
    if len(descriptor_hashes) != 32 or len(selected_row) != 32:
        failures.append("admitted row identity projection is incomplete")

    if sum(COMPONENT_WEIGHTS.values()) != 448:
        failures.append("component maximum does not sum to 448")
    if "448$." not in tex:
        failures.append("manuscript omits the 448 component bound")

    metadata = {
        "tex_start_line": start_line,
        "tex_end_line": end_line,
        "glyph_count": len(selected),
        "aspect_count": 64,
        "relations_per_aspect": len(selected) ** 2,
        "relation_count": 64 * len(selected) ** 2,
        "component_max": sum(COMPONENT_WEIGHTS.values()),
    }
    return failures, metadata


def main(argv: list[str] | None = None) -> int:
    """Delegate manuscript validation to the canonical paper-lane gate.

    The transform-relation amendment checker remains importable as
    ``validate_contract`` for focused unit tests. Command-line manuscript
    validation has one owner:
    ``docs/agent/glyph-rendering-paper/scripts/validate_fl4512_paper_lane.py``.
    """

    if argv is None:
        argv = sys.argv[1:]
    if "--transform-relation-only" not in argv:
        canonical = (
            ROOT
            / "docs/agent/glyph-rendering-paper/scripts/"
            "validate_fl4512_paper_lane.py"
        )
        return subprocess.run([sys.executable, str(canonical), *argv]).returncode

    failures, metadata = validate_contract()
    if failures:
        for failure in failures:
            print(f"FL4512_PAPER_V2_FAIL {failure}")
        return 1
    print(
        "FL4512_PAPER_V2_PASS "
        f"tex_lines={metadata['tex_start_line']}--{metadata['tex_end_line']} "
        f"glyphs={metadata['glyph_count']} "
        f"aspects={metadata['aspect_count']} "
        f"relations={metadata['relation_count']} "
        f"component_max={metadata['component_max']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
