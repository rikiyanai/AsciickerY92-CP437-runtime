#!/usr/bin/env python3
"""
sprite_validator.py -- Comprehensive .xp sprite validator

ARCHITECTURE:
    Production-ready sprite validation replacing the limited diagnose_xp.py.
    Validates .xp files against all engine invariants and family presets,
    producing specific diagnostic reports instead of just checking 2 hypotheses.

    This validator checks:
    - All 7 engine-level invariants (ENG-01 through ENG-07)
    - Family geometry matching (frame size, angles, projs)
    - Layer count and key_color consistency
    - Glyph range and metadata extraction

PURPOSE:
    Replace diagnose_xp.py's 2 checks with comprehensive validation covering
    all sprite.cpp loading requirements. Every .xp file can be validated before
    use, preventing silent failures in the engine or editor.

    diagnose_xp.py checked:
    1. projs mismatch (metadata vs geometry)
    2. transparency artifact (fg==bg==MAGENTA fallback)

    sprite_validator.py checks:
    1-7. All ENGINE_INVARIANTS
    8. Family geometry match
    9. Layer count match
    10. Key color match

USAGE:
    # Validate single sprite
    python3 -m scripts.pipeline.sprite_validator assets/sprites/player-nude.xp

    # Validate with family hint
    python3 -m scripts.pipeline.sprite_validator assets/sprites/player-nude.xp --family 7x9_a8_p2

    # Batch validate directory
    python3 -m scripts.pipeline.sprite_validator assets/sprites/*.xp

    # Python API
    from scripts.pipeline.sprite_validator import validate_xp
    report = validate_xp("assets/sprites/player-nude.xp")
    if not report["valid"]:
        print(report["summary"])

[FLOW:VALIDATION] [DATA-CONTRACT:XP]
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from .sprite_invariants import (
    check_raw_file_invariants,
    check_engine_invariants,
    check_family_match,
    FAMILY_PRESETS,
)
from .xp_core import XPFile


def validate_xp(xp_path: str | Path, family_hint: str = None) -> dict[str, Any]:
    """Validate a .xp file against all invariants.

    Args:
        xp_path: Path to .xp file
        family_hint: Optional family ID to check against (e.g., "7x9_a8_p2")

    Returns:
        Validation report dict with:
        - valid: bool (True if engine_violations is empty and file loaded)
        - path: str (input path)
        - family_id: str or None (detected or specified family)
        - engine_violations: list of fatal violations (blocks engine loading)
        - compatibility_warnings: list of non-fatal issues (family mismatch, unusual geometry)
        - quality_findings: list of quality issues (separate from validity)
        - summary: str (human-readable summary)
        - error: str or None (if file couldn't be loaded)

    Severity split (Phase 2):
    - valid depends ONLY on engine_violations + load errors
    - family mismatches are warnings, not errors
    - quality issues reported separately

    [FLOW:VALIDATION]
    """
    xp_path = Path(xp_path)

    # Check raw file invariants first (ENG-01, ENG-07) — before load
    raw_violations = check_raw_file_invariants(xp_path)

    # Try to load file
    try:
        xp = XPFile()
        xp.load(str(xp_path))
    except Exception as e:
        return {
            "valid": False,
            "path": str(xp_path),
            "family_id": None,
            "engine_violations": raw_violations + [{"id": "LOAD-ERROR", "description": str(e)}],
            "compatibility_warnings": [],
            "quality_findings": [],
            "summary": f"Failed to load: {e}",
            "error": str(e)
        }

    # Check engine invariants (fatal) — ENG-02 through ENG-06
    engine_violations = raw_violations + check_engine_invariants(xp)

    # Check family match (warnings only)
    family_result = check_family_match(xp, family_hint)
    compatibility_warnings = []
    if not family_result["matched"]:
        for m in family_result["mismatches"]:
            warning = {
                "field": m["field"],
                "expected": m.get("expected"),
                "actual": m.get("actual"),
                "reason": m.get("reason"),
                "severity": "warning"
            }
            compatibility_warnings.append(warning)

    # Quality findings (separate from validity)
    # TODO: Add color drift, outline artifacts, roundtrip checks
    quality_findings = []

    # Valid depends ONLY on engine violations
    valid = len(engine_violations) == 0

    # Build summary
    summary_parts = []
    if valid:
        summary_parts.append(f"✓ ENGINE-VALID: {xp_path.name}")
        summary_parts.append(f"  Family: {family_result['family_id']}")
        if compatibility_warnings:
            summary_parts.append(f"  ⚠ Compatibility warnings: {len(compatibility_warnings)}")
    else:
        summary_parts.append(f"✗ ENGINE-INVALID: {xp_path.name}")
        if engine_violations:
            summary_parts.append(f"  Engine violations (fatal): {len(engine_violations)}")
            for v in engine_violations[:3]:  # Show first 3
                summary_parts.append(f"    - {v['id']}: {v['description']}")

    if compatibility_warnings:
        summary_parts.append(f"  Compatibility warnings: {len(compatibility_warnings)}")
        for w in compatibility_warnings[:3]:  # Show first 3
            summary_parts.append(f"    - {w['field']}: {w.get('reason', 'mismatch')}")

    return {
        "valid": valid,
        "path": str(xp_path),
        "family_id": family_result["family_id"],
        "engine_violations": engine_violations,
        "compatibility_warnings": compatibility_warnings,
        "quality_findings": quality_findings,
        "summary": "\n".join(summary_parts),
        "error": None
    }


def validate_import(png_path: str | Path, xp_path: str | Path, family_hint: str = None) -> dict[str, Any]:
    """Validate a PNG-to-XP import result.

    Compares the imported .xp file against invariants and optionally the
    source PNG metadata.

    Args:
        png_path: Path to source PNG
        xp_path: Path to imported .xp file
        family_hint: Optional family ID to check against

    Returns:
        Validation report dict (same structure as validate_xp) plus:
        - png_path: str (source PNG path)

    [FLOW:VALIDATION]
    """
    # For now, just validate the .xp file
    # Future enhancement: compare PNG dimensions to XP dimensions
    report = validate_xp(xp_path, family_hint)
    report["png_path"] = str(png_path)
    return report


def format_report(report: dict[str, Any], verbose: bool = False) -> str:
    """Format validation report as human-readable text.

    Args:
        report: Report dict from validate_xp()
        verbose: If True, show all violations/mismatches

    Returns:
        Formatted report string

    [FLOW:VALIDATION]
    """
    lines = []

    # Header
    lines.append("=" * 60)
    lines.append(f"SPRITE VALIDATION REPORT: {Path(report['path']).name}")
    lines.append("=" * 60)

    # Valid/Invalid status
    if report["valid"]:
        lines.append("Status: ✓ ENGINE-VALID")
    else:
        lines.append("Status: ✗ ENGINE-INVALID")

    # Family info
    if report["family_id"]:
        lines.append(f"Family: {report['family_id']}")
        if report["family_id"] in FAMILY_PRESETS:
            preset = FAMILY_PRESETS[report["family_id"]]
            lines.append(f"  Label: {preset['label']}")
            lines.append(f"  Geometry: {preset['frame_w']}x{preset['frame_h']}, {preset['angles']} angles, {preset['projs']} projs")

    # Engine violations (fatal)
    if report["engine_violations"]:
        lines.append("")
        lines.append(f"Engine Violations (FATAL) ({len(report['engine_violations'])}):")
        violations = report["engine_violations"] if verbose else report["engine_violations"][:5]
        for v in violations:
            if "name" in v:
                lines.append(f"  [{v['id']}] {v['name']}")
            else:
                lines.append(f"  [{v['id']}]")
            lines.append(f"    {v['description']}")
            if "actual" in v and "expected" in v:
                lines.append(f"    Actual: {v['actual']}, Expected: {v['expected']}")

    # Compatibility warnings (non-fatal)
    compat_warnings = report.get("compatibility_warnings", report.get("family_mismatches", []))
    if compat_warnings:
        lines.append("")
        lines.append(f"Compatibility Warnings (non-fatal) ({len(compat_warnings)}):")
        warnings = compat_warnings if verbose else compat_warnings[:5]
        for w in warnings:
            if "reason" in w:
                lines.append(f"  {w['field']}: {w['reason']}")
            else:
                lines.append(f"  {w['field']}: expected {w.get('expected')}, got {w.get('actual')}")

    # Quality findings (separate from validity)
    quality_findings = report.get("quality_findings", [])
    if quality_findings:
        lines.append("")
        lines.append(f"Quality Findings ({len(quality_findings)}):")
        findings = quality_findings if verbose else quality_findings[:5]
        for f in findings:
            lines.append(f"  {f.get('type', 'unknown')}: {f.get('description', '')}")

    # Error
    if report.get("error"):
        lines.append("")
        lines.append(f"Error: {report['error']}")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    """CLI entry point for sprite validation."""
    parser = argparse.ArgumentParser(
        description="Validate .xp sprite files against engine invariants"
    )
    parser.add_argument(
        "sprites",
        type=Path,
        nargs="+",
        help="Sprite file(s) to validate"
    )
    parser.add_argument(
        "--family",
        help="Expected family ID (e.g., 7x9_a8_p2)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all violations and mismatches"
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Show only summary line per sprite"
    )

    args = parser.parse_args()

    # Validate each sprite
    all_valid = True
    for sprite_path in args.sprites:
        report = validate_xp(sprite_path, args.family)

        if args.summary_only:
            # Just print first line of summary
            print(report["summary"].split("\n")[0])
        else:
            print(format_report(report, args.verbose))
            print()

        if not report["valid"]:
            all_valid = False

    # Exit code
    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()
