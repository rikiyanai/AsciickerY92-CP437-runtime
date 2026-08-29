#!/usr/bin/env python3
"""FL-4257 Phase 0 implementation tests.

Verifies the executable + on-disk scaffolding of Phase 0:
- engine/matrix_euclidean.h exists and is the substantive Euclidean kernel
- engine/matrix.h is a dispatcher that re-exports matrix_euclidean.h
- engine/matrix_curved.h exists with the spherical-first primitives
- tests/engine_tests/test_matrix_curved.cpp compiles and passes
- server/server_state.h declares curvature_kappa/_kind/_present
- server/server_tick.cpp publishes curvature_kappa in authoritative_state.json
- engine/terrain.h declares ReadA3DCurvatureKind
- scripts/analyze_runs.py exposes audit-no-dual-curvature-include and passes
  on the current tree.

These are local-evidence checks. Headed VPS proof of the four analyzer gates
remains RAW-OPEN per FL-4257 until a two-tab run exercises them.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_matrix_split_dispatcher() -> None:
    eucl = (REPO_ROOT / "engine" / "matrix_euclidean.h").read_text()
    disp = (REPO_ROOT / "engine" / "matrix.h").read_text()
    assert "template <typename M>" in eucl, "Euclidean primitives must live in matrix_euclidean.h"
    assert "bool Invert(" in eucl
    assert "void MatProduct(" in eucl
    assert '#include "matrix_euclidean.h"' in disp, "matrix.h must re-export matrix_euclidean.h"
    # matrix.h itself must NOT re-declare primitives now.
    assert "template <typename M>" not in disp, "dispatcher must not re-declare Euclidean templates"
    assert "bool Invert(" not in disp


def test_curved_header_present_and_spherical_primitives() -> None:
    curved = REPO_ROOT / "engine" / "matrix_curved.h"
    assert curved.exists(), "engine/matrix_curved.h missing"
    text = curved.read_text()
    for sym in (
        "CurvedKappaIsEuclidean",
        "CurvedKappaIsSpherical",
        "CurvedKappaIsHyperbolic",
        "SphericalGreatCircleAngle",
        "SphericalArcLength",
        "SphericalRotateAroundAxis",
        "SphericalProjectToUnit",
        "SphericalCurvatureRadius",
    ):
        assert sym in text, f"matrix_curved.h missing {sym}"


def test_curved_tests_compile_and_pass(tmp_path: Path) -> None:
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        # No host C++ compiler — record skip so we don't false-pass.
        import pytest

        pytest.skip("no host c++ compiler available")
    out_bin = tmp_path / "test_matrix_curved"
    src = REPO_ROOT / "tests" / "engine_tests" / "test_matrix_curved.cpp"
    inc = REPO_ROOT / "engine"
    compile_proc = subprocess.run(
        [cxx, "-std=c++17", "-O0", f"-I{inc}", str(src), "-o", str(out_bin)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compile_proc.returncode == 0, (
        f"compile failed:\nstdout: {compile_proc.stdout}\nstderr: {compile_proc.stderr}"
    )
    run_proc = subprocess.run(
        [str(out_bin)], capture_output=True, text=True, check=False
    )
    assert run_proc.returncode == 0, (
        f"spherical tests failed:\nstdout: {run_proc.stdout}\nstderr: {run_proc.stderr}"
    )
    assert "0 failures" in run_proc.stdout, run_proc.stdout


def test_server_state_declares_curvature_fields() -> None:
    text = (REPO_ROOT / "server" / "server_state.h").read_text()
    assert "float curvature_kappa;" in text
    assert "uint8_t curvature_kind;" in text
    assert "uint8_t curvature_present;" in text
    assert "void SvrSetCurvatureFromIntake" in text
    assert "SVR_CURVATURE_KIND_SPHERICAL" in text


def test_server_tick_publishes_curvature_in_authoritative_state() -> None:
    # The C source uses backslash-escaped quotes inside format strings, so we
    # search for the C-source representation, not the runtime JSON output.
    text = (REPO_ROOT / "server" / "server_tick.cpp").read_text()
    assert r'\"curvature_kappa\":null' in text, "missing null branch (Law 6)"
    assert r'\"curvature_kappa\":%.6f' in text, "missing present branch"
    assert r'\"curvature_kind\":null' in text
    assert r'\"curvature_present\":0' in text
    assert r'\"curvature_present\":1' in text


def test_terrain_h_declares_a3d_curvature_intake() -> None:
    text = (REPO_ROOT / "engine" / "terrain.h").read_text()
    assert "ReadA3DCurvatureKind" in text


def test_curvature_client_header_present_and_failsclosed() -> None:
    text = (REPO_ROOT / "engine" / "curvature.h").read_text()
    assert "AsciCurvatureViewAbsent" in text
    assert "AsciCurvatureViewFromIntake" in text
    # FL-4257 Law 6: the adapter MUST take BOTH the wire-level presence bit
    # and the kind. Taking kind alone is unsafe because a JSON decoder that
    # maps null kind -> 0 would manufacture Euclidean authority out of an
    # absent server truth. The signature is the contract; re-check it here so
    # a future refactor cannot silently regress.
    assert "AsciCurvatureViewFromIntake(uint8_t present_wire," in text, (
        "FL-4257 curvature.h adapter must take (present_wire, kind)"
    )
    # FL-4257 Law 6: the fail-closed contract MUST appear in commentary so
    # future readers cannot quietly add a local Euclidean default. Accept
    # either hyphenated or space spelling (line wraps are fine).
    lowered = text.lower()
    assert (
        "fail-closed" in lowered
        or "fail closed" in lowered
        or "fail\nclosed" in lowered
    ), "FL-4257 curvature.h must mention the fail-closed contract"


def test_curvature_client_view_present_zero_overrides_kind() -> None:
    """The adapter must return absent when present_wire=0 even if kind is a
    valid 0/1/2. A JSON decoder that maps null kind -> 0 must not be able to
    manufacture Euclidean authority out of an absent server truth.
    """
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        import pytest

        pytest.skip("no host c++ compiler available")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        src = td_path / "test_curvature_view.cpp"
        src.write_text(
            '#include "curvature.h"\n'
            '#include <cstdio>\n'
            'int main(void) {\n'
            '    int failures = 0;\n'
            '    // present_wire=0, kind=Euclidean -> absent\n'
            '    AsciCurvatureView v0 = AsciCurvatureViewFromIntake(0, ASCK_CURVATURE_KIND_EUCLIDEAN);\n'
            '    if (v0.present != 0) { std::fprintf(stderr, "FAIL: present_wire=0,kind=0 -> present=%u\\n", v0.present); ++failures; }\n'
            '    // present_wire=0, kind=Spherical -> absent\n'
            '    AsciCurvatureView v1 = AsciCurvatureViewFromIntake(0, ASCK_CURVATURE_KIND_SPHERICAL);\n'
            '    if (v1.present != 0) { std::fprintf(stderr, "FAIL: present_wire=0,kind=1 -> present=%u\\n", v1.present); ++failures; }\n'
            '    // present_wire=1, kind=Spherical -> kappa=+1.0\n'
            '    AsciCurvatureView v2 = AsciCurvatureViewFromIntake(1, ASCK_CURVATURE_KIND_SPHERICAL);\n'
            '    if (v2.present != 1 || v2.kappa != 1.0f) { std::fprintf(stderr, "FAIL: present_wire=1,kind=1 -> present=%u kappa=%g\\n", v2.present, (double)v2.kappa); ++failures; }\n'
            '    // present_wire=1, kind=Euclidean -> kappa=0.0 present=1\n'
            '    AsciCurvatureView v3 = AsciCurvatureViewFromIntake(1, ASCK_CURVATURE_KIND_EUCLIDEAN);\n'
            '    if (v3.present != 1 || v3.kappa != 0.0f) { std::fprintf(stderr, "FAIL: present_wire=1,kind=0 -> present=%u kappa=%g\\n", v3.present, (double)v3.kappa); ++failures; }\n'
            '    // present_wire=1, kind unrecognised -> absent\n'
            '    AsciCurvatureView v4 = AsciCurvatureViewFromIntake(1, 9);\n'
            '    if (v4.present != 0) { std::fprintf(stderr, "FAIL: present_wire=1,kind=9 -> present=%u\\n", v4.present); ++failures; }\n'
            '    // FL-4257 review follow-up: presence is strictly {0,1}. A byte\n'
            '    // outside that set violates the wire contract and must fail closed\n'
            '    // even if kind is valid.\n'
            '    AsciCurvatureView v5 = AsciCurvatureViewFromIntake(2, ASCK_CURVATURE_KIND_SPHERICAL);\n'
            '    if (v5.present != 0) { std::fprintf(stderr, "FAIL: present_wire=2,kind=1 -> present=%u\\n", v5.present); ++failures; }\n'
            '    AsciCurvatureView v6 = AsciCurvatureViewFromIntake(0xFF, ASCK_CURVATURE_KIND_EUCLIDEAN);\n'
            '    if (v6.present != 0) { std::fprintf(stderr, "FAIL: present_wire=0xFF,kind=0 -> present=%u\\n", v6.present); ++failures; }\n'
            '    std::printf("ok failures=%d\\n", failures);\n'
            '    return failures == 0 ? 0 : 1;\n'
            '}\n'
        )
        out_bin = td_path / "test_curvature_view"
        inc = REPO_ROOT / "engine"
        compile_proc = subprocess.run(
            [cxx, "-std=c++17", "-O0", f"-I{inc}", str(src), "-o", str(out_bin)],
            capture_output=True, text=True, check=False,
        )
        assert compile_proc.returncode == 0, (
            f"compile failed:\nstdout: {compile_proc.stdout}\nstderr: {compile_proc.stderr}"
        )
        run_proc = subprocess.run(
            [str(out_bin)], capture_output=True, text=True, check=False
        )
        assert run_proc.returncode == 0, (
            f"client view tests failed:\nstdout: {run_proc.stdout}\nstderr: {run_proc.stderr}"
        )


def test_analyze_runs_dual_curvature_include_audit_clean() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
         "audit-no-dual-curvature-include"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"audit-no-dual-curvature-include returned {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "CLEAN" in proc.stdout or "ok" in proc.stdout.lower(), proc.stdout


def test_audit_flags_dispatcher_plus_curved_as_dual() -> None:
    """Regression for the HIGH finding: a runtime file that includes the
    legacy `matrix.h` dispatcher (which re-exports matrix_euclidean.h) AND
    `matrix_curved.h` must be reported as a dual include. The audit's old
    basename-only check would have false-greened this case.
    """
    import json as _json
    fake = REPO_ROOT / "engine" / "_fl4257_phase0_audit_probe.cpp"
    fake.write_text(
        "// FL-4257 Phase 0 audit regression probe — must be reported as dual.\n"
        "#include \"matrix.h\"\n"
        "#include \"matrix_curved.h\"\n"
        "void fl4257_phase0_audit_probe_dummy(void) {}\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
             "audit-no-dual-curvature-include", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 2, (
            f"audit should fail closed on dispatcher+curved dual include "
            f"(got rc={proc.returncode})\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        payload = _json.loads(proc.stdout)
        assert payload["status"] == "FAIL", payload
        rel = "engine/_fl4257_phase0_audit_probe.cpp"
        dual_paths = [r["path"] for r in payload.get("dual_includes", [])]
        assert rel in dual_paths, (
            f"probe {rel} not in dual_includes: {dual_paths}\nfull payload: {payload}"
        )
        # Confirm the row carries the Euclidean-via-dispatcher attribution so
        # operators can see WHY the file was flagged.
        row = next(r for r in payload["dual_includes"] if r["path"] == rel)
        assert "matrix.h" in row.get("euclidean_includes", []), row
        assert "matrix_curved.h" in row.get("curved_includes", []), row
    finally:
        fake.unlink(missing_ok=True)
    # Sanity: audit returns to CLEAN once the probe is removed.
    proc2 = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
         "audit-no-dual-curvature-include"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc2.returncode == 0, (
        f"audit not CLEAN after probe removed:\nstdout: {proc2.stdout}\nstderr: {proc2.stderr}"
    )
