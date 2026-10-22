#!/usr/bin/env python3
"""FL-4131 W5 — VAL-03 fixture harness prep.

VAL-03 (engine/sprite.cpp Phase 0B) is the sidecar branch that decides
how a loaded .xp interacts with the extended-glyph contract:

   1. .glyph_profile.json exists alongside .xp?
      a. parse_sidecar fails        -> hard load failure
      b. parse_sidecar succeeds
         - any cell glyph > 255     -> Phase-2-not-implemented hard failure
         - all cells glyph in 0..255 -> legacy CP437 behavior preserved
   2. .glyph_profile.json absent     -> legacy VAL-03 rejection of any glyph > 255

This harness covers the FOUR scenarios above. The first three (sidecar
present, sidecar invalid, sidecar valid + CP437-only) are tested entirely
in Python through scripts/glyph_sidecar.py. The fourth (sidecar valid +
extended glyph) requires the C++ engine to report the
"Phase-2-not-implemented" fail-closed message; Python can only verify
that the sidecar parses and that the extended glyph IS in the XP. A
C++ runner is marked as TODO and skipped with fail-closed exit code
to prevent false-green claims (no skip ever passes the gate).

Front door:
  python3 scripts/fl4131_val03_fixture_harness.py
  python3 scripts/fl4131_val03_fixture_harness.py --json

Gates it informs:
  - val03_legacy_gate_preserved
  - val03_sidecar_branch_wired
  - extended_xp_profile_discriminated

Hard rules honored:
  - Does NOT load engine/sprite.cpp (Python-only) and does NOT admit
    GlyphId > 255 into any runtime path.
  - Phase 0 corrections may rename fields/filename in glyph_sidecar.py;
    any rename will surface as a Python ImportError or schema mismatch
    when this harness re-runs — that is the intended TODO drift surface.
  - No false-green: when a scenario requires C++ runtime evidence and
    no runner is available, the scenario is reported as SKIPPED with
    overall exit code 2 (gate NOT passed).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Pull in the Python sidecar parser. Any rename / contract change in
# Phase 0 corrections will fail this import and surface as a TODO.
try:
    from glyph_sidecar import (  # type: ignore
        CURRENT_PROFILE_KIND,
        CURRENT_SIDECAR_VERSION,
        GlyphSidecar,
        GlyphSidecarError,
        SIDECAR_SUFFIX,
        parse_sidecar,
        sidecar_path_for,
    )
except Exception as exc:
    print(
        f"[FL-4131 W5] CONTRACT DRIFT: cannot import scripts/glyph_sidecar.py "
        f"({exc}). The Phase 0 sidecar API may have moved; harness CANNOT make "
        f"a gate-status claim until imports resolve.",
        file=sys.stderr,
    )
    sys.exit(2)


# Sample valid sidecar payload for fixture synthesis. Hash must be a 64-char
# lowercase hex SHA-256 to satisfy the parser; the exact value is irrelevant
# to behavioral tests as long as it parses.
_VALID_HASH = "0" * 63 + "a"


def _write_valid_sidecar(path: Path) -> None:
    payload = {
        "sidecar_version": CURRENT_SIDECAR_VERSION,
        "profile_kind": CURRENT_PROFILE_KIND,
        "content_pack_id": "fl4131_w5_harness",
        "glyph_manifest_hash": _VALID_HASH,
        "glyph_manifest_path": None,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_invalid_sidecar(path: Path, mutation: str) -> None:
    payload: dict[str, Any] = {
        "sidecar_version": CURRENT_SIDECAR_VERSION,
        "profile_kind": CURRENT_PROFILE_KIND,
        "content_pack_id": "fl4131_w5_harness",
        "glyph_manifest_hash": _VALID_HASH,
        "glyph_manifest_path": None,
    }
    if mutation == "wrong_profile_kind":
        payload["profile_kind"] = "not_extended"
    elif mutation == "wrong_version":
        payload["sidecar_version"] = 0
    elif mutation == "bad_hash":
        payload["glyph_manifest_hash"] = "not-a-hex-digest"
    elif mutation == "missing_content_pack_id":
        del payload["content_pack_id"]
    elif mutation == "bad_json":
        path.write_text("{not json", encoding="utf-8")
        return
    else:
        raise ValueError(f"unknown mutation: {mutation}")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scenario results
# ---------------------------------------------------------------------------

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_SKIP_REQUIRES_CPP = "skip_requires_cpp"  # fail-closed: does NOT pass gate


@dataclass
class ScenarioResult:
    name: str
    status: str
    detail: str
    gate_pass_contribution: bool  # True if this scenario can satisfy its gate


def scenario_legacy_no_sidecar_extended_glyph_fails() -> ScenarioResult:
    """Scenario 1: no sidecar present + XP contains glyph > 255 -> legacy VAL-03 rejection.

    Legacy VAL-03 rejection lives in engine/sprite.cpp. Python cannot prove
    the C++ rejection without invoking the build, so this scenario is
    marked SKIPPED with no false-green contribution.
    """
    return ScenarioResult(
        name="legacy_no_sidecar_extended_glyph_fails",
        status=STATUS_SKIP_REQUIRES_CPP,
        detail=(
            "Requires C++ engine to load an extended-glyph XP with no sidecar "
            "and produce the legacy VAL-03 rejection. TODO: add a C++ runner "
            "behind makefile_game_term that builds a tiny ValidationRunner "
            "linked against engine/sprite.cpp."
        ),
        gate_pass_contribution=False,
    )


def scenario_invalid_sidecar_fails_closed() -> ScenarioResult:
    """Scenario 2: invalid sidecar variants all raise GlyphSidecarError."""
    expected_mutations = [
        "wrong_profile_kind",
        "wrong_version",
        "bad_hash",
        "missing_content_pack_id",
        "bad_json",
    ]
    fails: list[str] = []
    passes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fl4131_w5_") as td:
        for mut in expected_mutations:
            sidecar = Path(td) / f"sample_{mut}.glyph_profile.json"
            _write_invalid_sidecar(sidecar, mut)
            try:
                parse_sidecar(str(sidecar))
            except GlyphSidecarError:
                passes.append(mut)
                continue
            except Exception as exc:  # any other exception is also a hard fail
                if mut == "bad_json":
                    # Parser may raise JSONDecodeError before wrapping, or wrap it
                    passes.append(mut)
                    continue
                fails.append(f"{mut}:unexpected_exception:{type(exc).__name__}")
            else:
                fails.append(f"{mut}:did_not_raise")
    if fails:
        return ScenarioResult(
            name="invalid_sidecar_fails_closed",
            status=STATUS_FAIL,
            detail="; ".join(fails),
            gate_pass_contribution=False,
        )
    return ScenarioResult(
        name="invalid_sidecar_fails_closed",
        status=STATUS_PASS,
        detail=f"all {len(passes)} mutations rejected: {','.join(passes)}",
        gate_pass_contribution=True,
    )


def scenario_valid_sidecar_extended_glyph_fails() -> ScenarioResult:
    """Scenario 3: valid sidecar + XP with glyph > 255 -> "Phase 2 not implemented"."""
    # Python-side we can verify the sidecar parses cleanly; the actual
    # "Phase 2 not implemented" rejection lives in engine/sprite.cpp.
    parses_ok = False
    detail_parts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fl4131_w5_") as td:
        sidecar = Path(td) / "fake.xp.glyph_profile.json"
        _write_valid_sidecar(sidecar)
        try:
            sc = parse_sidecar(str(sidecar))
        except Exception as exc:
            detail_parts.append(f"sidecar_parse_failed:{exc}")
        else:
            parses_ok = isinstance(sc, GlyphSidecar)
            detail_parts.append(
                f"sidecar_parses_ok content_pack_id={sc.content_pack_id} "
                f"hash_len={len(sc.glyph_manifest_hash)}"
            )
    if not parses_ok:
        return ScenarioResult(
            name="valid_sidecar_extended_glyph_fails",
            status=STATUS_FAIL,
            detail="; ".join(detail_parts),
            gate_pass_contribution=False,
        )
    detail_parts.append(
        "TODO: C++ runner must load an extended-glyph XP alongside this "
        "sidecar and confirm the 'Phase 2 (FL-4131) extended loader not "
        "implemented' rejection. Until then this scenario is SKIPPED and "
        "does NOT satisfy the gate."
    )
    return ScenarioResult(
        name="valid_sidecar_extended_glyph_fails",
        status=STATUS_SKIP_REQUIRES_CPP,
        detail="; ".join(detail_parts),
        gate_pass_contribution=False,
    )


def scenario_valid_sidecar_cp437_only_legacy_preserved() -> ScenarioResult:
    """Scenario 4: valid sidecar + CP437-only XP -> legacy behavior preserved."""
    # Python-side: we can prove the sidecar is valid and well-formed.
    # The "legacy behavior preserved" claim is byte-equivalent CP437 cell
    # decoding which is exercised by W2 already. Combining the two requires
    # C++ runtime (or a Python loader that mirrors sprite.cpp's branch
    # selection). Mark TODO + SKIP for the runtime equivalence proof.
    parses_ok = False
    detail_parts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fl4131_w5_") as td:
        sidecar = Path(td) / "fake.xp.glyph_profile.json"
        _write_valid_sidecar(sidecar)
        # Round-trip existence checks via the parser-side helpers.
        side_path_string = sidecar_path_for(str(td) + "/fake.xp")
        detail_parts.append(f"computed_sidecar_path={side_path_string}")
        if not side_path_string.endswith(SIDECAR_SUFFIX):
            return ScenarioResult(
                name="valid_sidecar_cp437_only_legacy_preserved",
                status=STATUS_FAIL,
                detail=(
                    f"sidecar_path_for() returned unexpected suffix; "
                    f"expected {SIDECAR_SUFFIX!r}"
                ),
                gate_pass_contribution=False,
            )
        try:
            parse_sidecar(str(sidecar))
            parses_ok = True
            detail_parts.append("sidecar_parses_ok")
        except Exception as exc:
            detail_parts.append(f"sidecar_parse_failed:{exc}")
    if not parses_ok:
        return ScenarioResult(
            name="valid_sidecar_cp437_only_legacy_preserved",
            status=STATUS_FAIL,
            detail="; ".join(detail_parts),
            gate_pass_contribution=False,
        )
    detail_parts.append(
        "TODO: C++ runner must load a CP437-only XP alongside this sidecar "
        "and confirm the loader takes the legacy CP437 path. Until then "
        "this scenario is SKIPPED and does NOT satisfy the gate."
    )
    return ScenarioResult(
        name="valid_sidecar_cp437_only_legacy_preserved",
        status=STATUS_SKIP_REQUIRES_CPP,
        detail="; ".join(detail_parts),
        gate_pass_contribution=False,
    )


SCENARIOS: list[Callable[[], ScenarioResult]] = [
    scenario_legacy_no_sidecar_extended_glyph_fails,
    scenario_invalid_sidecar_fails_closed,
    scenario_valid_sidecar_extended_glyph_fails,
    scenario_valid_sidecar_cp437_only_legacy_preserved,
]


def run_all() -> list[ScenarioResult]:
    return [fn() for fn in SCENARIOS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    results = run_all()
    payload = {
        "fl": "FL-4131",
        "stage": "W5_VAL03_FIXTURE_HARNESS_PREP",
        "scenarios": [
            {
                "name": r.name,
                "status": r.status,
                "gate_pass_contribution": r.gate_pass_contribution,
                "detail": r.detail,
            }
            for r in results
        ],
        "summary": {
            "pass": sum(1 for r in results if r.status == STATUS_PASS),
            "fail": sum(1 for r in results if r.status == STATUS_FAIL),
            "skip_requires_cpp": sum(1 for r in results if r.status == STATUS_SKIP_REQUIRES_CPP),
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("FL-4131 W5 VAL-03 fixture harness:")
        for r in results:
            mark = "PASS" if r.status == STATUS_PASS else ("FAIL" if r.status == STATUS_FAIL else "SKIP-CPP")
            print(f"  [{mark}] {r.name}")
            print(f"         {r.detail}")
        print()
        print("Summary:", payload["summary"])
        if payload["summary"]["fail"]:
            print("VERDICT: FAIL (one or more scenarios failed)")
        elif payload["summary"]["skip_requires_cpp"]:
            print(
                "VERDICT: PARTIAL — Python scenarios PASS; C++ runner scenarios "
                "SKIPPED (no false-green). Gates remain implemented_unproven "
                "until the C++ runner lands."
            )
        else:
            print("VERDICT: PASS")
    # Exit code policy:
    #   0 only if every scenario is PASS.
    #   1 if any FAIL.
    #   2 if no FAIL but at least one SKIP_REQUIRES_CPP (no false-green).
    if payload["summary"]["fail"]:
        return 1
    if payload["summary"]["skip_requires_cpp"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
