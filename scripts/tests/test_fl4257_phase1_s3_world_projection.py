#!/usr/bin/env python3
"""FL-4257 Phase 1 S3 — world-projection ownership tests.

S3 deleted direct projection-matrix reads from the world-geometry hot paths
(terrain patch, sprite billboard, mesh face) and routed every projection
through render_projection.cpp's dispatchers. These tests assert:

- The world-vertex helpers are declared and call sites consume them
- render_world_pass.cpp + render_sprite_blit.cpp no longer dereference
  r->mul / r->add / r->view_pos / r->view_dir / r->view_ofs / r->inv_tm
- The broadened audit-render-curvature-ownership reports CLEAN
- The broadened audit FAILs on a synthetic probe that puts a direct read
  back into a render-side file (regression for the matrix violation class)
- render_resolve.cpp remains on the allow-list as the documented Phase 2
  deferral
"""
from __future__ import annotations

import json as _json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_world_projection_dispatchers_declared() -> None:
    text = (REPO_ROOT / "engine" / "render" / "render_world_projection.h").read_text()
    for sym in (
        "ProjectWorldVertexPatchPersp",
        "ProjectWorldVertexSpritePersp",
        "ProjectWorldVertexOrthoIntFlag",
        "ProjectWorldVertexOrthoIadd",
        "ProjectFaceVertexPersp",
        "BuildMeshViewTransformEuclidean",
    ):
        assert sym in text, f"render_world_projection.h missing {sym}"


def test_world_pass_no_direct_matrix_reads() -> None:
    text = (REPO_ROOT / "engine" / "render" / "render_world_pass.cpp").read_text()
    # Strip comments so the doc references in the file header don't count.
    text_no_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text_no_comments = re.sub(r"//[^\n]*", "", text_no_comments)
    for pattern in (
        r"r->mul\[",
        r"r->add\[",
        r"r->view_pos\[",
        r"r->view_dir\[",
        r"r->view_ofs\[",
        r"r->inv_tm\[",
    ):
        m = re.search(pattern, text_no_comments)
        assert not m, (
            f"render_world_pass.cpp still has direct projection-matrix read "
            f"{pattern}: routed sites must call ProjectWorldVertex* helpers."
        )
    # And it must actually consume the helpers.
    assert "ProjectWorldVertexPatchPersp" in text
    assert "ProjectFaceVertexPersp" in text
    assert "BuildMeshViewTransformEuclidean" in text


def test_sprite_blit_no_direct_matrix_reads() -> None:
    text = (REPO_ROOT / "engine" / "render" / "render_sprite_blit.cpp").read_text()
    text_no_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text_no_comments = re.sub(r"//[^\n]*", "", text_no_comments)
    for pattern in (
        r"r->mul\[",
        r"r->add\[",
        r"r->view_pos\[",
        r"r->view_dir\[",
        r"r->view_ofs\[",
        r"r->inv_tm\[",
    ):
        m = re.search(pattern, text_no_comments)
        assert not m, (
            f"render_sprite_blit.cpp still has direct projection-matrix read "
            f"{pattern}: routed sites must call ProjectWorldVertexSpritePersp / "
            f"ProjectWorldVertexOrthoIntFlag."
        )
    assert "ProjectWorldVertexSpritePersp" in text
    assert "ProjectWorldVertexOrthoIntFlag" in text


def test_render_curvature_ownership_audit_clean() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
         "audit-render-curvature-ownership", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"audit returned {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = _json.loads(proc.stdout)
    assert payload["status"] == "CLEAN", payload
    assert payload["view_violations"] == [], payload
    assert payload["matrix_violations"] == [], payload


def test_audit_catches_resurrected_matrix_read() -> None:
    """Regression: the broadened audit must flag a render-side file that
    re-introduces a direct r->mul[ or r->view_pos[ read. Without this, a
    well-meaning future edit could silently regress S3.
    """
    fake = REPO_ROOT / "engine" / "render" / "_fl4257_phase1_s3_matrix_probe.cpp"
    fake.write_text(
        "// FL-4257 Phase 1 S3 matrix-ownership probe — must trip the audit.\n"
        "struct Renderer { double mul[6]; double add[3]; float view_pos[3]; "
        "float view_dir[3]; float view_ofs[2]; };\n"
        "static float probe_use_matrix(Renderer* r, float x, float y) {\n"
        "    float fx = (float)(r->mul[0] * x + r->mul[2] * y + r->add[0]);\n"
        "    float fy = (float)(r->mul[1] * x + r->mul[3] * y + r->add[1]);\n"
        "    float dx = x - r->view_pos[0];\n"
        "    float dy = y - r->view_pos[1];\n"
        "    return fx + fy + dx + dy + r->view_dir[0] + r->view_ofs[0];\n"
        "}\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
             "audit-render-curvature-ownership", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 2, (
            f"audit should FAIL closed on direct matrix reads (rc={proc.returncode})\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        payload = _json.loads(proc.stdout)
        assert payload["status"] == "FAIL", payload
        rel = "engine/render/_fl4257_phase1_s3_matrix_probe.cpp"
        matrix_paths = [r["path"] for r in payload.get("matrix_violations", [])]
        assert rel in matrix_paths, (
            f"probe {rel} not in matrix_violations: {payload}"
        )
        row = next(r for r in payload["matrix_violations"] if r["path"] == rel)
        kinds = {v["kind"] for v in row["violations"]}
        # Must catch at least the patterns the probe exercises.
        assert "direct r->mul[ read" in kinds, kinds
        assert "direct r->add[ read" in kinds, kinds
        assert "direct r->view_pos[ read" in kinds, kinds
        assert "direct r->view_dir[ read" in kinds, kinds
        assert "direct r->view_ofs[ read" in kinds, kinds
    finally:
        fake.unlink(missing_ok=True)
    proc2 = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
         "audit-render-curvature-ownership"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc2.returncode == 0, (
        f"audit not CLEAN after probe removed:\nstdout: {proc2.stdout}\nstderr: {proc2.stderr}"
    )


def test_render_resolve_remains_on_allow_list_phase2_deferral() -> None:
    """render_resolve.cpp owns the pixel-level INVERSE projection used by the
    resolve pass. Spherical Unproject is Phase 2 work; until then this file
    stays on the audit's allow-list. The test ensures the allow-list entry
    is not silently removed before the spherical inverse exists — losing it
    would mean every resolve-pass run fails the audit even though the
    deferral is documented.
    """
    analyze_runs_text = (REPO_ROOT / "scripts" / "analyze_runs.py").read_text()
    assert "engine/render/render_resolve.cpp" in analyze_runs_text, (
        "render_resolve.cpp must remain on the allow-list comment in "
        "scripts/analyze_runs.py until the spherical inverse exists."
    )
    # Source-side sanity: the file still reads matrix fields, but is allow-listed.
    resolve = (REPO_ROOT / "engine" / "render" / "render_resolve.cpp").read_text()
    assert "r->mul[" in resolve or "r->view_dir[" in resolve, (
        "render_resolve.cpp is expected to still read projection matrices "
        "(phase 2 deferral); if not, drop it from the allow-list."
    )
