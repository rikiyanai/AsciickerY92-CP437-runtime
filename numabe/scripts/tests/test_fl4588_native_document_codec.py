# =============================================================
# test_fl4588_native_document_codec.py
# =============================================================
# FL-4588 native parity INTEGRATION gate (read-only).
#
# This file is an isolated OBSERVATION surface. It does not own the
# C++ product codec, the independent Python twin, the editor UI, or
# the transport layer. Those live elsewhere and are owned by other
# agents. This gate only:
#
#   1. Builds the current ASCIIID product binary from the live tree.
#   2. Invokes the product's env-gated read-only report
#      ASCIID_FL4588_CODEC_ROUNDTRIP on the two real maps that must
#      parse and canonicalize.
#   3. Parses the emitted [FL-4588][CODEC-ROUNDTRIP] fact lines.
#   4. Compares each map's canonical byte count and SHA-256 against
#      the independent Python evaluator (scripts/fl4588/
#      a3d_document_codec.py) for the SAME input bytes.
#   5. Confirms sandbox_20x20.a3d fails closed (hostile revive range).
#
# It never calls user-unreachable authoring setters, never opens a
# windowed editor session for mutation, and never writes .a3d bytes.
# =============================================================

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "fl4588"))

from a3d_document_codec import A3dCodecError, roundtrip_verdict  # noqa: E402

# Fact line printed by editor/asciiid.cpp when ASCIID_FL4588_CODEC_ROUNDTRIP
# is set. The field names are the cross-evaluator contract: canonical1 is
# the byte length of the first canonical serialization, and
# canonical_sha256 is its lowercase hex digest.
CODEC_LINE = re.compile(
    r"^\[FL-4588\]\[CODEC-ROUNDTRIP\] "
    r"path=(?P<path>\S+) "
    r"bytes=(?P<bytes>\d+) "
    r"ok=(?P<ok>\d+) "
    r"roundtrip_stable=(?P<roundtrip_stable>\d+) "
    r"input_canonical=(?P<input_canonical>\d+) "
    r"patches=(?P<patches>\d+) "
    r"world_version=(?P<world_version>-?\d+) "
    r"instances=(?P<instances>\d+) "
    r"enemies=(?P<enemies>\d+) "
    r"markers=(?P<markers>\d+) "
    r"enemy_section=(?P<enemy_section>\d+) "
    r"marker_section=(?P<marker_section>\d+) "
    r"canonical1=(?P<canonical1>\d+) "
    r"canonical2=(?P<canonical2>\d+) "
    r"first_diff=(?P<first_diff>-?\d+) "
    r"canonical_sha256=(?P<canonical_sha256>\S+) "
    r"reason=(?P<reason>.*)$"
)

# Real maps that must succeed under the native product codec.
PASS_MAPS = (
    "assets/a3d/game_map_y8_original_game_map.a3d",
    "assets/a3d/game_map_y8.a3d",
)

# Legacy sandbox with revive exponents outside the frozen 0..30 range.
# The product must reject it; HTML Open/Save stay fail-closed on this.
FAIL_CLOSED_MAP = "assets/a3d/sandbox_20x20.a3d"

BINARY = ROOT / ".run" / "asciiid"


def _asciiid_makefile() -> str:
    """Pick the platform makefile the product actually builds with."""
    if platform.system() == "Darwin":
        return "apps/asciicker-presenter/makefile_asciiid_mac"
    return "apps/asciicker-presenter/makefile_asciiid"


def _build_current_asciiid() -> Path:
    """
    Rebuild .run/asciicker-presenter from the current worktree.

    Why rebuild instead of trusting a pre-existing binary: the FL-4588
    codec and its sprite-catalog binding live in the product link. A
    stale binary would report facts for an older owner and silently
    pass or fail the wrong contract.
    """
    makefile = _asciiid_makefile()
    completed = subprocess.run(
        ["make", "-f", makefile, str(BINARY.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"ASCIIID product build failed under {makefile}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert BINARY.is_file(), f"missing product binary after build: {BINARY}"
    return BINARY


def _run_codec_roundtrip(map_paths: list[str]) -> tuple[int, list[dict]]:
    """
    Invoke the product's read-only env gate on one or more .a3d paths.

    The env value is a semicolon-separated list (product contract). The
    process exits with the number of files that failed to parse or
    verify, and prints one CODEC-ROUNDTRIP fact line per path. No
    window is created and no live editor document is mutated.
    """
    env = os.environ.copy()
    env["ASCIID_FL4588_CODEC_ROUNDTRIP"] = ";".join(map_paths)
    completed = subprocess.run(
        [str(BINARY)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    facts: list[dict] = []
    for line in completed.stdout.splitlines():
        match = CODEC_LINE.match(line.strip())
        if not match:
            continue
        row = match.groupdict()
        # Convert numeric fact fields so callers compare ints, not strings.
        for key in (
            "bytes",
            "ok",
            "roundtrip_stable",
            "input_canonical",
            "patches",
            "world_version",
            "instances",
            "enemies",
            "markers",
            "enemy_section",
            "marker_section",
            "canonical1",
            "canonical2",
            "first_diff",
        ):
            row[key] = int(row[key])
        facts.append(row)
    return completed.returncode, facts


def _python_verdict(rel_path: str) -> dict:
    """Independent evaluator facts for the same file bytes the native gate saw."""
    data = (ROOT / rel_path).read_bytes()
    return roundtrip_verdict(data)


def test_native_document_codec_parity_against_python_evaluator():
    """
    Build ASCIIID, run the native round-trip report on the two real
    maps, and require byte-count + SHA-256 identity with the Python twin.
    """
    _build_current_asciiid()

    exit_code, facts = _run_codec_roundtrip(list(PASS_MAPS))
    assert exit_code == 0, (
        f"native codec round-trip exited {exit_code}; facts={facts}"
    )
    assert len(facts) == len(PASS_MAPS), (
        f"expected {len(PASS_MAPS)} CODEC-ROUNDTRIP lines, got {len(facts)}: {facts}"
    )

    by_path = {fact["path"]: fact for fact in facts}
    for rel_path in PASS_MAPS:
        native = by_path[rel_path]
        assert native["ok"] == 1, f"{rel_path} native ok=0 reason={native['reason']}"
        assert native["roundtrip_stable"] == 1, (
            f"{rel_path} native roundtrip_stable=0 first_diff={native['first_diff']}"
        )
        assert native["canonical_sha256"] != "-", (
            f"{rel_path} missing native canonical_sha256"
        )

        # Cross-evaluator parity: same input bytes → same canonical length
        # and same SHA-256. This is the FL-4588 document-codec identity.
        python = _python_verdict(rel_path)
        assert python["roundtrip_stable"] is True, (
            f"{rel_path} python twin was not roundtrip-stable"
        )
        assert native["canonical1"] == python["canonical_sha_input_len"], (
            f"{rel_path} canonical byte count mismatch: "
            f"native={native['canonical1']} python={python['canonical_sha_input_len']}"
        )
        assert native["canonical_sha256"] == python["canonical_sha256"], (
            f"{rel_path} canonical SHA-256 mismatch: "
            f"native={native['canonical_sha256']} python={python['canonical_sha256']}"
        )


def test_sandbox_20x20_fails_closed_under_native_codec():
    """
    sandbox_20x20.a3d authors revive exponents outside the frozen 0..30
    consumption range. The product codec must reject it; a silent accept
    would arm HTML Open/Save on an undefined document.
    """
    _build_current_asciiid()

    exit_code, facts = _run_codec_roundtrip([FAIL_CLOSED_MAP])
    assert len(facts) == 1, f"expected one sandbox fact line, got {facts}"
    native = facts[0]
    assert native["path"] == FAIL_CLOSED_MAP
    assert native["ok"] == 0, (
        f"sandbox_20x20.a3d must fail closed, but native ok=1 facts={native}"
    )
    assert exit_code != 0, (
        "sandbox_20x20.a3d must produce a non-zero process exit "
        f"(got {exit_code})"
    )

    # Independent twin must also reject the same hostile bytes. Matching
    # the exact reason string is not required — only fail-closed behavior.
    with pytest.raises(A3dCodecError):
        roundtrip_verdict((ROOT / FAIL_CLOSED_MAP).read_bytes())
