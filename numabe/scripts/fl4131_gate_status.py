#!/usr/bin/env python3
"""FL-4131 gate status checker (Phase 0/1 executable mapping).

For each of the FL-4131 gates, classify status as one of:
  proof_passed                  - an executable verification ran and returned green
  manual_inspection_required    - implementation exists but the only valid
                                  acceptance surface is an operator-visible
                                  manual receipt (FL-4131 phase claims)
  implemented_unproven          - code/artifact exists but no executable proof
                                  was run, or proof failed
  artifact_exists               - file-existence-only gate, file is on disk
  open                          - nothing in place

CARDINAL RULES (FL-4131):
  - Do not claim proof_passed unless a real check actually ran in this invocation.
  - Do not mark any extended-glyph runtime gate above 'open' until later phases land.
  - artifact_exists is a passing terminal status only for gates whose acceptance
    criterion is literally artifact presence; otherwise it is implemented_unproven.
  - For no_silent_glyph_truncation, scripts/builds may support diagnostics but
    do not prove acceptance. Only a recorded operator-visible manual inspection
    receipt may promote the gate.

Front door:
  python3 scripts/fl4131_gate_status.py            # human table
  python3 scripts/fl4131_gate_status.py --json     # machine-readable
  python3 scripts/fl4131_gate_status.py --gate G   # single gate

Does NOT:
  - Modify any file.
  - Admit GlyphId > 255 anywhere.
  - Touch glyph_sidecar.cpp/.h/.py (Phase 0 corrections owner reserves).
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_PROOFS = False

STATUS_PROOF_PASSED = "proof_passed"
STATUS_MANUAL_INSPECTION_REQUIRED = "manual_inspection_required"
STATUS_ARTIFACT_EXISTS = "artifact_exists"
STATUS_IMPLEMENTED_UNPROVEN = "implemented_unproven"
STATUS_OPEN = "open"
STATUS_ERROR = "check_error"

VALID_STATUSES = {
    STATUS_PROOF_PASSED,
    STATUS_MANUAL_INSPECTION_REQUIRED,
    STATUS_ARTIFACT_EXISTS,
    STATUS_IMPLEMENTED_UNPROVEN,
    STATUS_OPEN,
    STATUS_ERROR,
}

MANUAL_INSPECTION_RECEIPT_REL = (
    "docs/research/ascii/verification/fl4131_manual_inspection_receipt.md"
)


def _markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    rest = text[start + len(marker):]
    next_heading = rest.find("\n## ")
    if next_heading >= 0:
        rest = rest[:next_heading]
    return rest


def _receipt_field(section: str, field: str) -> str:
    prefix = f"{field}:"
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return ""


def _current_git_head() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def _source_head_for_receipt_commit(commit: str) -> str:
    receipt_only_prefixes = (
        "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/",
        "docs/research/ascii/verification/fl4131/phase_d/2026-05-30/",
        "docs/research/ascii/verification/fl4131/shape_lab/",
        "scripts/fl4131_gate_status.py",
        "scripts/evaluate_fl4131_shape_lab_profiles.py",
        "scripts/test_fl4131_asciiid_workflow_receipt_current.py",
        "scripts/test_fl4131_multiplayer_receipts_current.py",
    )
    for _ in range(16):
        changed = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
        subject = subprocess.check_output(
            ["git", "show", "-s", "--format=%s", commit],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
        receipt_only = all(
            path.startswith(receipt_only_prefixes)
            or (path == "docs/FAILURE_LOG.md" and "FL-4131" in subject)
            for path in changed
        )
        if not changed or not receipt_only:
            return commit
        commit = subprocess.check_output(["git", "rev-parse", f"{commit}^"], cwd=REPO_ROOT, text=True).strip()
    return commit


def _receipt_commit_current(data: dict) -> tuple[bool, str]:
    commit = data.get("commit_under_test")
    if not commit and isinstance(data.get("proof"), dict):
        commit = data["proof"].get("commit_under_test")
    if not isinstance(commit, str) or not commit:
        return False, "receipt has no commit_under_test"
    head = _current_git_head()
    if not head:
        return False, "could not resolve current HEAD"
    source_head = _source_head_for_receipt_commit(head)
    receipt_head = _source_head_for_receipt_commit(commit)
    if source_head == receipt_head or source_head.startswith(receipt_head) or receipt_head.startswith(source_head[:len(receipt_head)]):
        return True, ""
    return False, f"receipt commit {commit} is stale for source HEAD {source_head[:10]}"


def _manual_receipt_ready_for_no_truncation(text: str) -> tuple[bool, str]:
    cp437 = _markdown_section(text, "Phase 1.1 Native CP437 Inspection")
    extended = _markdown_section(text, "Phase 1.1 Extended-Glyph Fail-Closed Inspection")
    gameplay = _markdown_section(text, "Phase 1.1 Gameplay-Entered Proof")
    decision = _markdown_section(text, "Operator Decision")

    cp437_result = _receipt_field(cp437, "Result")
    extended_status = _receipt_field(extended, "Status")
    extended_result = _receipt_field(extended, "Result")
    gameplay_status = _receipt_field(gameplay, "Status")
    gameplay_result = _receipt_field(gameplay, "Result")
    accepted = _receipt_field(decision, "Accepted")

    missing: list[str] = []
    if cp437_result != "PASS":
        missing.append(f"native CP437 Result is {cp437_result or 'missing'}, expected PASS")
    if extended_status.startswith("BLOCKED") or extended_result != "PASS":
        missing.append(
            f"extended fail-closed Status/Result is "
            f"{extended_status or 'missing'}/{extended_result or 'missing'}, expected non-blocked/PASS"
        )
    if gameplay_status.startswith("BLOCKED") or gameplay_result != "PASS":
        missing.append(
            f"gameplay-entered Status/Result is "
            f"{gameplay_status or 'missing'}/{gameplay_result or 'missing'}, expected non-blocked/PASS"
        )
    if accepted != "YES":
        missing.append(f"operator Accepted is {accepted or 'missing'}, expected YES")
    if missing:
        return False, "; ".join(missing)
    return True, "native CP437, gameplay-entered, and extended fail-closed inspections passed with operator acceptance"


def _extract_marker_json(text: str, start_marker: str, end_marker: str) -> dict:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise ValueError(f"markers not found: {start_marker} / {end_marker}")
    json_start = text.find("{", start)
    json_end = text.rfind("}", start, end)
    if json_start < 0 or json_end < json_start:
        raise ValueError("JSON payload not found between proof markers")
    return json.loads(text[json_start:json_end + 1])



def _native_material_render_receipt_ok() -> tuple[bool, str]:
    receipt = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-30/phase_d_native_material_render_resolve.json"
    if not receipt.exists():
        return False, f"missing native render receipt: {receipt.relative_to(REPO_ROOT)}"
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"native render receipt parse failed: {exc!r}"
    proof = data.get("proof", {}) if isinstance(data, dict) else {}
    assertions = data.get("assertions", {}) if isinstance(data, dict) else {}
    checks = [
        data.get("schema") == "fl4131_native_material_render_resolve_receipt.v1",
        data.get("verdict") == "PASS",
        proof.get("schema") == "fl4131_native_material_sidecar_load.v1",
        proof.get("verdict") == "PASS",
        proof.get("loaded") is True,
        proof.get("applied_cells") == 104,
        proof.get("expected_cells") == 104,
        proof.get("first_glyph_id") == 512,
        proof.get("last_glyph_id") == 631,
        proof.get("cells_ok") is True,
        proof.get("coverage_cells") == 104,
        proof.get("coverage_ok") is True,
        proof.get("display_cells") == 104,
        proof.get("display_not_fallback_bytes") is True,
        assertions.get("admitted_extended_cells_loaded") is True,
        assertions.get("coverage_backed_display_cells") is True,
        assertions.get("display_glyphs_not_fallback_bytes") is True,
    ]
    if not all(checks):
        return False, "native render receipt exists but required PASS/load/coverage/display assertions are incomplete"
    return True, (
        "native .run/game receipt loads the 104-cell material sidecar fixture "
        "and resolves each cell to coverage-backed display glyphs distinct from fallback bytes"
    )


def _web_admitted_extended_receipt_ok() -> tuple[bool, str]:
    receipt = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/phase_d_web_admitted_extended_offline.json"
    if not receipt.exists():
        return False, f"missing web admitted receipt: {receipt.relative_to(REPO_ROOT)}"
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"web admitted receipt parse failed: {exc!r}"
    pixels = data.get("pixels", {}) if isinstance(data, dict) else {}
    uniforms = pixels.get("shader_uniforms", {}) if isinstance(pixels, dict) else {}
    manifest = pixels.get("manifest_bound", {}) if isinstance(pixels, dict) else {}
    sidecar_gpu = pixels.get("sidecar_gpu_sample", {}) if isinstance(pixels, dict) else {}

    def _sample_has_alpha(sample: object) -> bool:
        return isinstance(sample, list) and len(sample) >= 4 and sample[0] > 0 and sample[3] == 255

    checks = [
        data.get("schema") == "fl4131_web_admitted_extended_proof_v1",
        data.get("verdict") == "PASS",
        data.get("mode") == "admitted",
        pixels.get("inject_active") is True,
        pixels.get("offline_started") is True,
        not pixels.get("offline_error"),
        _sample_has_alpha(pixels.get("sidecar_sample")),
        _sample_has_alpha(pixels.get("sidecar_upload_sample")),
        _sample_has_alpha(sidecar_gpu.get("x40_y40")),
        uniforms.get("link_status") is True,
        uniforms.get("sidecar_tex") == 2,
        uniforms.get("lut_tex") == 3,
        uniforms.get("page_atlas") == 4,
        uniforms.get("lut_width", 0) > 0,
        uniforms.get("fallback_glyph_id") == 539,
        manifest.get("content_pack_id") == "material.additive.v1",
        manifest.get("glyph_min") == 512,
        manifest.get("glyph_max", 0) >= 631,
        manifest.get("fallback_glyph_id") == 539,
        pixels.get("red_pixels", 0) < 50000,
        pixels.get("nonblack_pixels", 0) > 1000,
    ]
    if not all(checks):
        return False, "web admitted receipt exists but required manifest/sidecar/shader/pixel assertions are incomplete"
    return True, (
        "browser admitted-mode receipt binds material.additive.v1, uploads sidecar GlyphIds, "
        "uses LUT/page-atlas uniforms, and renders nonfallback pixels without diagnostic-red flood"
    )


@dataclass
class GateResult:
    name: str
    status: str
    evidence: str
    note: str = ""


def _exists(*relpaths: str) -> tuple[bool, list[str]]:
    found: list[str] = []
    missing: list[str] = []
    for rel in relpaths:
        p = REPO_ROOT / rel
        if p.exists():
            found.append(rel)
        else:
            missing.append(rel)
    return (not missing, missing if missing else found)


def _grep_count(path: str, needles: list[str]) -> int:
    """Substring-presence helper used by the legacy marker checks.

    Returns the count of distinct needles that appear at least once in the
    file (0 if none match, -1 if the file is missing or unreadable). This is
    intentionally a needle-presence count, NOT a per-needle occurrence count
    — the legacy callers pass a single needle and use the boolean ">0" sense.

    NEW CODE should prefer _grep_marker_pin (review M2) which:
      - matches the dedicated "// MODEL_PIN: <token>" / "MODEL_PIN: <token>"
        line shape (not bare substring), so future comment rewraps that split
        a token across lines do NOT silently flip a gate from
        implemented_unproven to open;
      - returns the true occurrence count.
    """
    p = REPO_ROOT / path
    if not p.exists():
        return -1
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return -1
    return sum(1 for n in needles if n in text)


# FL-4131 review M2 + L2: dedicated MODEL_PIN line matcher.
# A pinned marker lives on its own line in the form
#     // MODEL_PIN: <token>         (C/C++/JS/glsl block-comment style)
#  or # MODEL_PIN: <token>          (python/shell)
#  or <!-- MODEL_PIN: <token>       (html)
# This shape survives comment rewrap, keeps the gate grep deterministic, and
# returns a true per-needle occurrence count (L2). The compiled regex is
# anchored so an embedded mention in prose does NOT trip the gate.
import re as _re  # noqa: E402 (intentional late import; keep gate file standalone)

_MODEL_PIN_RE_CACHE: dict[str, _re.Pattern[str]] = {}


def _model_pin_re(token: str) -> _re.Pattern[str]:
    pat = _MODEL_PIN_RE_CACHE.get(token)
    if pat is None:
        # Match the marker token preceded by "MODEL_PIN:" and arbitrary
        # comment-leader whitespace; require word boundary at end so
        # "<token>_extra" does NOT match.
        pat = _re.compile(r"MODEL_PIN:\s*" + _re.escape(token) + r"\b")
        _MODEL_PIN_RE_CACHE[token] = pat
    return pat


def _grep_marker_pin(path: str, token: str) -> int:
    """Return the per-file occurrence count of "MODEL_PIN: <token>".

    -1 if the file is missing or unreadable. 0 if absent. Counts occurrences,
    not needles (L2 fix).
    """
    p = REPO_ROOT / path
    if not p.exists():
        return -1
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return -1
    return len(_model_pin_re(token).findall(text))


# FL-4131 review M4: marker-gate registry. Replaces 4 near-identical
# check_* functions with one declarative table + one runner.
#
# Schema:
#   gate_name -> {
#       "token":       MODEL_PIN token string,
#       "required":    list of paths that MUST each carry the token (>=1 hit
#                      per file) for the gate to promote (review M3 fix:
#                      explicit per-file check, not >=N count),
#       "open_msg":    evidence shown when the gate is OPEN,
#   }
MARKER_GATES: dict[str, dict] = {
    "asciiid_input_model_pinned": {
        "token": "asciiid_input_model_pinned",
        "required": ["apps/asciicker-presenter/asciiid.cpp"],
        "open_msg": "Phase 4 ASCIIID authoring UI; gated on Phase 2 backend + Phase 3 renderer landing first (backend-before-UI ADR)",
    },
    "shader_lookup_lut_model_pinned": {
        "token": "shader_lookup_lut_model_pinned",
        "required": [
            "engine/render/render.h",
        ],
        "open_msg": "Phase 3 renderer/shader LUT model pin; gated on Phase 2 backend admission landing first",
    },
    "web_extended_glyph_buffer": {
        "token": "web_extended_glyph_buffer",
        "required": ["web/game_web.cpp", "web/game_web.html"],
        "open_msg": "Phase 5 web extended glyph buffer; gated on Phase 2 backend + Phase 3 renderer landing first",
    },
    "multiplayer_manifest_hash_match": {
        "token": "multiplayer_manifest_hash_match",
        "required": ["server/protocol/protocol_join.h"],
        "open_msg": "Phase 7 multiplayer manifest hash match on join/reset; gated on Phase 2 backend + Phase 5 web buffer landing first",
    },
    "asciiid_shader_manifest_lookup": {
        "token": "asciiid_shader_manifest_lookup",
        "required": ["apps/asciicker-presenter/asciiid.cpp"],
        "open_msg": "Phase 3 renderer/shader lookup; gated on Phase 2 backend admission landing first",
    },
}


def _check_marker_gate(gate_name: str) -> "GateResult":
    """Run one marker-gate from MARKER_GATES.

    Review M3: enforces per-file presence (every required path must contain
    the token at least once). Adding the marker to an unrelated file does
    NOT spuriously promote the gate.
    """
    spec = MARKER_GATES[gate_name]
    token = spec["token"]
    required = spec["required"]
    open_msg = spec["open_msg"]

    per_file: list[tuple[str, int]] = []
    missing: list[str] = []
    for path in required:
        cnt = _grep_marker_pin(path, token)
        if cnt <= 0:
            missing.append(path if cnt == 0 else f"{path} (file missing)")
        per_file.append((path, max(cnt, 0)))

    if missing:
        return GateResult(
            gate_name,
            STATUS_OPEN,
            open_msg + " — missing MODEL_PIN in: " + ", ".join(missing),
        )
    hit_summary = ", ".join(f"{p} ({c})" for p, c in per_file)
    return GateResult(
        gate_name,
        STATUS_IMPLEMENTED_UNPROVEN,
        f"MODEL_PIN: {token} present in: {hit_summary}",
    )


def _check_model_pin_artifact(gate_name: str) -> "GateResult":
    """Return artifact_exists for gates whose criterion is a model pin.

    MODEL_PIN comments are durable architecture anchors. They are not runtime
    implementation and must not promote implementation-shaped gates.
    """
    result = _check_marker_gate(gate_name)
    if result.status != STATUS_IMPLEMENTED_UNPROVEN:
        return result
    return GateResult(
        gate_name,
        STATUS_ARTIFACT_EXISTS,
        result.evidence.replace("MODEL_PIN:", "MODEL_PIN artifact:"),
    )


def _run_parity_test(timeout: int = 60, with_c: bool = False) -> tuple[bool, str]:
    """Execute the sidecar parity test if present. Return (ok, evidence).

    with_c=True adds --with-c to the runner; the gate status checker uses this
    because glyph_sidecar_parsers_contract_parity means both parsers agree —
    Python-only is implemented_unproven, not proof_passed.
    With with_c=True, compilation of engine/glyph_sidecar_test.c is attempted
    automatically, so the timeout is longer.
    """
    script = REPO_ROOT / "scripts" / "test_glyph_sidecar_parity.py"
    if not script.exists():
        return False, "scripts/test_glyph_sidecar_parity.py missing"
    cmd = [sys.executable, str(script)]
    if with_c:
        cmd.append("--with-c")
        timeout = max(timeout, 120)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"exec_error: {exc}"
    if proc.returncode == 0:
        return True, f"exit=0 stdout_tail={proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''}"
    return False, f"exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"


def _run_w2_verify(timeout: int = 60) -> tuple[bool, str]:
    """Run the W2 legacy CP437 no-diff harness --verify. Return (ok, evidence)."""
    script = REPO_ROOT / "scripts" / "fl4131_legacy_cp437_no_diff.py"
    if not script.exists():
        return False, "scripts/fl4131_legacy_cp437_no_diff.py missing"
    baseline = REPO_ROOT / "assets" / "glyphs" / "fixtures" / "legacy_cp437_baseline.json"
    if not baseline.exists():
        return False, "W2 baseline not written (run --baseline first)"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--verify"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"exec_error: {exc}"
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    verdict = tail[-1] if tail else ""
    if proc.returncode == 0:
        return True, f"W2 --verify exit=0; {verdict}"
    return False, f"W2 --verify exit={proc.returncode}; {verdict}"


def _run_w3_verify(timeout: int = 30) -> tuple[bool, str]:
    """Run the W3 terrain shade perf baseline --verify. Return (ok, evidence)."""
    script = REPO_ROOT / "scripts" / "fl4131_terrain_shade_perf_baseline.py"
    if not script.exists():
        return False, "scripts/fl4131_terrain_shade_perf_baseline.py missing"
    baseline = REPO_ROOT / "assets" / "glyphs" / "fixtures" / "terrain_shade_perf_baseline.json"
    if not baseline.exists():
        return False, "W3 baseline not written (run --baseline first)"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--verify"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"exec_error: {exc}"
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    verdict = tail[-1] if tail else ""
    if proc.returncode == 0:
        return True, f"W3 --verify exit=0; {verdict}"
    return False, f"W3 --verify exit={proc.returncode}; {verdict}"


@functools.lru_cache(maxsize=1)
def _run_manifest_check(timeout: int = 30) -> tuple[bool, str]:
    """Cached: a single status run invokes compile_glyph_manifest.py --check at most once
    even when multiple gate functions call this helper."""
    script = REPO_ROOT / "scripts" / "compile_glyph_manifest.py"
    if not script.exists():
        return False, "scripts/compile_glyph_manifest.py missing"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"exec_error: {exc}"
    if proc.returncode == 0:
        return True, "exit=0"
    return False, f"exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"


@functools.lru_cache(maxsize=1)
def _run_admission_allowlist_check(timeout: int = 30) -> tuple[bool, str]:
    script = REPO_ROOT / "scripts" / "fl4131_admission_check.py"
    if not script.exists():
        return False, "scripts/fl4131_admission_check.py missing"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"exec_error: {exc}"
    if proc.returncode == 0:
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "exit=0"
        return True, tail
    return False, f"exit={proc.returncode} stderr={proc.stderr.strip()[:300]}"


@functools.lru_cache(maxsize=1)
def _run_actor_visual_compile_admission_test(timeout: int = 30) -> tuple[bool, str]:
    script = REPO_ROOT / "scripts" / "test_fl4131_actor_visual_compile_admission.py"
    if not script.exists():
        return False, "scripts/test_fl4131_actor_visual_compile_admission.py missing"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"exec_error: {exc}"
    if proc.returncode == 0:
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "exit=0"
        return True, tail
    return False, f"exit={proc.returncode} stderr={proc.stderr.strip()[:300]}"


# ---------------------------------------------------------------------------
# Per-gate check functions. Each returns a GateResult.
# Categories mirror the FL-4131 ADR Phase classification.
# ---------------------------------------------------------------------------

def check_glyph_manifest_directory_tree_exists() -> GateResult:
    dirs = [
        "assets/glyphs",
        "assets/glyphs/schema",
        "assets/glyphs/fixtures",
        "assets/glyphs/generated",
        "assets/glyphs/atlases",
        "assets/glyphs/manifests",
    ]
    missing = [d for d in dirs if not (REPO_ROOT / d).is_dir()]
    if missing:
        return GateResult(
            "glyph_manifest_directory_tree_exists",
            STATUS_OPEN,
            "missing: " + ",".join(missing),
        )
    return GateResult(
        "glyph_manifest_directory_tree_exists",
        STATUS_ARTIFACT_EXISTS,
        f"all {len(dirs)} subdirs present under assets/glyphs/",
    )


def check_glyph_manifest_schema_file_exists() -> GateResult:
    schema = REPO_ROOT / "assets/glyphs/schema/glyph_manifest.schema.json"
    if not schema.exists():
        return GateResult(
            "glyph_manifest_schema_file_exists",
            STATUS_OPEN,
            "missing: assets/glyphs/schema/glyph_manifest.schema.json",
        )
    try:
        json.loads(schema.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            "glyph_manifest_schema_file_exists",
            STATUS_ERROR,
            f"schema unparseable: {exc}",
        )
    return GateResult(
        "glyph_manifest_schema_file_exists",
        STATUS_ARTIFACT_EXISTS,
        "assets/glyphs/schema/glyph_manifest.schema.json (valid JSON)",
    )


def check_compile_glyph_manifest_check_wired() -> GateResult:
    ok, evidence = _run_manifest_check()
    if ok:
        return GateResult(
            "compile_glyph_manifest_check_wired",
            STATUS_PROOF_PASSED,
            f"scripts/compile_glyph_manifest.py --check returned 0; {evidence}",
        )
    return GateResult(
        "compile_glyph_manifest_check_wired",
        STATUS_IMPLEMENTED_UNPROVEN,
        f"compile_glyph_manifest.py exists but --check did not pass: {evidence}",
    )


def check_extended_glyph_fixture_exists() -> GateResult:
    fx = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_terrain_v1.json"
    if not fx.exists():
        return GateResult(
            "extended_glyph_fixture_exists",
            STATUS_OPEN,
            "missing: assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
        )
    try:
        data = json.loads(fx.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            "extended_glyph_fixture_exists",
            STATUS_ERROR,
            f"fixture unparseable: {exc}",
        )
    return GateResult(
        "extended_glyph_fixture_exists",
        STATUS_ARTIFACT_EXISTS,
        f"assets/glyphs/fixtures/extended_glyph_terrain_v1.json (keys: {sorted(data.keys()) if isinstance(data, dict) else 'list'})",
    )


def check_rfc8785_sha256_helper_exists() -> GateResult:
    # RFC 8785 = JSON Canonicalization Scheme (JCS). Look for any explicit
    # RFC 8785 marker plus an sha256 helper used by glyph manifests.
    candidates = [
        "scripts/compile_glyph_manifest.py",
        "scripts/glyph_sidecar.py",
    ]
    hits: list[str] = []
    for c in candidates:
        p = REPO_ROOT / c
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if ("RFC 8785" in text or "rfc8785" in text or "jcs" in text.lower()) and "sha256" in text:
            hits.append(c)
    if hits:
        return GateResult(
            "rfc8785_sha256_helper_exists",
            STATUS_ARTIFACT_EXISTS,
            "rfc8785+sha256 markers in: " + ",".join(hits),
        )
    return GateResult(
        "rfc8785_sha256_helper_exists",
        STATUS_OPEN,
        "no rfc8785/jcs marker paired with sha256 in compile/sidecar scripts",
    )


def check_web_atlas_of_atlases_binding() -> GateResult:
    schema = REPO_ROOT / "assets/glyphs/schema/atlas_of_atlases.schema.json"
    if not schema.exists():
        return GateResult(
            "web_atlas_of_atlases_binding",
            STATUS_OPEN,
            "missing: assets/glyphs/schema/atlas_of_atlases.schema.json",
        )
    # Binding gate requires the schema decision + a documented binding model.
    # Without a runtime binding being implemented, this stays artifact-only.
    return GateResult(
        "web_atlas_of_atlases_binding",
        STATUS_ARTIFACT_EXISTS,
        "schema pinned at assets/glyphs/schema/atlas_of_atlases.schema.json; binding model not yet implemented (Phase 2+)",
    )


def check_legacy_cp437_unchanged() -> GateResult:
    ok, evidence = _run_w2_verify()
    if ok:
        return GateResult("legacy_cp437_unchanged", STATUS_PROOF_PASSED, evidence)
    # W2 harness exists but verify did not pass — stay implemented_unproven
    w2_script = REPO_ROOT / "scripts" / "fl4131_legacy_cp437_no_diff.py"
    if w2_script.exists():
        return GateResult("legacy_cp437_unchanged", STATUS_IMPLEMENTED_UNPROVEN,
                          f"W2 harness on disk but verify did not pass: {evidence}")
    return GateResult("legacy_cp437_unchanged", STATUS_OPEN,
                      "W2 harness missing; VAL-03 legacy path preserved but unproven")


def check_glyph_id_type_pinned() -> GateResult:
    # Look for an explicit GlyphId typedef/using anywhere in engine/
    needles = ["typedef uint32_t GlyphId", "using GlyphId =", "typedef GlyphId"]
    hits: list[str] = []
    for hdr in ["engine/glyph_id.h", "engine/glyph_sidecar.h", "engine/render/render.h", "engine/actor_visual_profile.h"]:
        p = REPO_ROOT / hdr
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle in text:
                hits.append(f"{hdr}:{needle}")
                break
    if hits:
        return GateResult(
            "glyph_id_type_pinned",
            STATUS_ARTIFACT_EXISTS,
            "GlyphId type declared: " + ",".join(hits),
        )
    return GateResult(
        "glyph_id_type_pinned",
        STATUS_OPEN,
        "no GlyphId typedef/using located in engine/glyph_id.h / engine/glyph_sidecar.h / engine/render/render.h / engine/actor_visual_profile.h",
    )


def check_extended_xp_profile_discriminated() -> GateResult:
    # Phase 0B sprite.cpp checks profile_kind="extended_glyph_v1" before scan.
    p = REPO_ROOT / "engine/sprite.cpp"
    if not p.exists():
        return GateResult("extended_xp_profile_discriminated", STATUS_OPEN, "engine/sprite.cpp missing")
    text = p.read_text(encoding="utf-8", errors="replace")
    if "extended_glyph_v1" in text or "glyph_sidecar_parse_for_xp" in text:
        return GateResult(
            "extended_xp_profile_discriminated",
            STATUS_IMPLEMENTED_UNPROVEN,
            "sidecar profile_kind discriminated in engine/sprite.cpp",
        )
    return GateResult(
        "extended_xp_profile_discriminated",
        STATUS_OPEN,
        "no profile_kind discrimination found",
    )


def check_val03_legacy_gate_preserved() -> GateResult:
    p = REPO_ROOT / "engine/sprite.cpp"
    if not p.exists():
        return GateResult("val03_legacy_gate_preserved", STATUS_OPEN, "engine/sprite.cpp missing")
    text = p.read_text(encoding="utf-8", errors="replace")
    if "val03_legacy_gate_preserved" not in text:
        return GateResult(
            "val03_legacy_gate_preserved",
            STATUS_OPEN,
            "VAL-03 legacy gate marker absent",
        )
    # Marker present — W2 passing means legacy CP437 path was preserved through changes.
    ok, evidence = _run_w2_verify()
    if ok:
        return GateResult(
            "val03_legacy_gate_preserved",
            STATUS_PROOF_PASSED,
            f"VAL-03 marker in engine/sprite.cpp; W2 confirms legacy assets unchanged: {evidence}",
        )
    return GateResult(
        "val03_legacy_gate_preserved",
        STATUS_IMPLEMENTED_UNPROVEN,
        f"VAL-03 legacy else-branch documented at engine/sprite.cpp; W2 did not pass: {evidence}",
    )


def check_extended_glyph_manifest_schema_validate() -> GateResult:
    # Same script as compile_glyph_manifest_check_wired; if --check passed,
    # schema validation is exercised.
    ok, evidence = _run_manifest_check()
    if ok:
        return GateResult(
            "extended_glyph_manifest_schema_validate",
            STATUS_PROOF_PASSED,
            f"compile_glyph_manifest.py --check exercised schema validation; {evidence}",
        )
    return GateResult(
        "extended_glyph_manifest_schema_validate",
        STATUS_IMPLEMENTED_UNPROVEN,
        f"validate path exists but --check did not pass: {evidence}",
    )


def check_extended_glyph_manifest_build_check() -> GateResult:
    # Gate requires the build (make / build-web.sh) to invoke the compile check.
    candidates = ["apps/game-fl4512/makefile_game_mac", "build-web.sh"]
    wired: list[str] = []
    for c in candidates:
        p = REPO_ROOT / c
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "compile_glyph_manifest" in text:
            wired.append(c)
    if wired:
        return GateResult(
            "extended_glyph_manifest_build_check",
            STATUS_IMPLEMENTED_UNPROVEN,
            "compile_glyph_manifest invoked in: " + ",".join(wired),
        )
    return GateResult(
        "extended_glyph_manifest_build_check",
        STATUS_OPEN,
        "neither apps/game-fl4512/makefile_game_mac nor build-web.sh invoke compile_glyph_manifest",
    )


def check_asciiid_input_model_pinned() -> GateResult:
    # Phase 4 = ASCIIID authoring UI/input. See MARKER_GATES for the model-pin
    # required-paths contract. Review M2/M3/M4: dispatched through the unified
    # registry so adding a new marker gate is a single-table entry, not a
    # fourth grep stanza to keep in sync. The required file is
    # editor/asciiid.cpp; canon spec mirrors are documentation, not enforcement.
    return _check_model_pin_artifact("asciiid_input_model_pinned")


def check_asciiid_shader_manifest_lookup() -> GateResult:
    # Phase 3 ASCIIID editor shader lookup. Dispatches through MARKER_GATES
    # (review M2/M3/M4). Required path: editor/asciiid.cpp near term_fs_src.
    return _check_model_pin_artifact("asciiid_shader_manifest_lookup")


def check_coverage_font_independent_contract() -> GateResult:
    # Phase 2/3 supporting contract gate: font-independence is a property of
    # the manifest-driven coverage lookup that Phase 2 introduces. Authoring
    # the contract doc is allowed in parallel with Phase 2 approval; pinning
    # it as proof requires Phase 2 backend admission to observe.
    docs = list((REPO_ROOT / "docs").rglob("*coverage*font*independent*"))
    docs += list((REPO_ROOT / "docs").rglob("*coverage_contract*"))
    if docs:
        return GateResult(
            "coverage_font_independent_contract",
            STATUS_ARTIFACT_EXISTS,
            "contract doc(s): " + ",".join(str(d.relative_to(REPO_ROOT)) for d in docs[:3]),
        )
    return GateResult(
        "coverage_font_independent_contract",
        STATUS_OPEN,
        "Phase 2/3 supporting contract; no font-independent coverage contract doc found under docs/",
    )


def check_coverage_generator_golden_rows() -> GateResult:
    # Phase 2 supporting tooling gate: the extended-coverage generator and
    # golden rows feed engine/glyph_manifest at Phase 2 admission time. The
    # generator may be authored in parallel with the Phase 2 design pick;
    # promotion to proof requires Phase 2 backend admission to consume it.
    gen_a = REPO_ROOT / "scripts/generate_coverage_table.py"
    gen_b = REPO_ROOT / "scripts/generate_extended_coverage.py"
    golden_root = REPO_ROOT / "assets/glyphs/generated"
    has_generator = gen_a.exists() or gen_b.exists()
    has_output = golden_root.is_dir() and any(golden_root.iterdir())
    if has_generator and has_output:
        return GateResult(
            "coverage_generator_golden_rows",
            STATUS_ARTIFACT_EXISTS,
            "generator + non-empty assets/glyphs/generated/",
        )
    if has_generator:
        return GateResult(
            "coverage_generator_golden_rows",
            STATUS_IMPLEMENTED_UNPROVEN,
            "generator exists; generated/ dir empty or missing",
        )
    return GateResult(
        "coverage_generator_golden_rows",
        STATUS_OPEN,
        "Phase 2 supporting tooling; no generator script + empty generated/ dir",
    )


def check_atlas_page_binding_model_pinned() -> GateResult:
    schema = REPO_ROOT / "assets/glyphs/schema/atlas_of_atlases.schema.json"
    if schema.exists():
        return GateResult(
            "atlas_page_binding_model_pinned",
            STATUS_ARTIFACT_EXISTS,
            "atlas_of_atlases.schema.json pins atlas-page binding model",
        )
    return GateResult(
        "atlas_page_binding_model_pinned",
        STATUS_OPEN,
        "atlas_of_atlases.schema.json missing",
    )


def check_cell_texture_schema_versioned() -> GateResult:
    # Look for an explicit schema version field in glyph manifest schema.
    schema = REPO_ROOT / "assets/glyphs/schema/glyph_manifest.schema.json"
    if not schema.exists():
        return GateResult("cell_texture_schema_versioned", STATUS_OPEN, "manifest schema missing")
    try:
        data = json.loads(schema.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult("cell_texture_schema_versioned", STATUS_ERROR, f"schema unparseable: {exc}")
    found = False
    text = json.dumps(data)
    for needle in ("\"version\"", "\"schema_version\"", "manifest_version", "sidecar_version"):
        if needle in text:
            found = True
            break
    if found:
        return GateResult(
            "cell_texture_schema_versioned",
            STATUS_ARTIFACT_EXISTS,
            "manifest schema declares a version field",
        )
    return GateResult(
        "cell_texture_schema_versioned",
        STATUS_OPEN,
        "no version field in glyph_manifest.schema.json",
    )


def check_shader_lookup_lut_model_pinned() -> GateResult:
    # Phase 3 atlas/page/UV LUT pin. Dispatches through MARKER_GATES
    # (review M2/M3/M4). FL-4131's ASCIIID glyph lane requires the native
    # renderer model anchor; Godot terrain-splat is a separate lane and must
    # not be a dependency for this gate.
    return _check_model_pin_artifact("shader_lookup_lut_model_pinned")


def check_recorder_extended_glyph_schema() -> GateResult:
    # W7 designs this. Until W7 lands, stay open.
    candidates = [
        "docs/research/ascii/verification/recorder_extended_glyph_schema.md",
        "assets/glyphs/schema/recorder_extended_glyph.schema.json",
        "scripts/fl4131_recorder_schema_check.py",
    ]
    for c in candidates:
        if (REPO_ROOT / c).exists():
            return GateResult(
                "recorder_extended_glyph_schema",
                STATUS_ARTIFACT_EXISTS,
                f"schema artifact: {c}",
            )
    return GateResult(
        "recorder_extended_glyph_schema",
        STATUS_OPEN,
        "no recorder extended-glyph schema artifact (awaits W7)",
    )


def check_native_gl_extended_glyph_rendered() -> GateResult:
    # Phase 3 implementation gate (distinct from the model-pin artifact gate).
    # The extended glyph path lives in two layers:
    #   1. platform/terminal_gl_present.cpp — GL fragment shader + sidecar upload
    #      (the atlas-of-atlases page_atlas sampler + usampler2D sidecar conditional)
    #   2. engine/render/render_sprite_blit.cpp — sidecar write at blit time
    #      (future: write GlyphId to output sidecar when sprite has glyph_plane)
    # MODEL_PIN in render.h is necessary but not sufficient — this gate requires
    # the implementation to exist in the GL display path.
    render_files = [
        REPO_ROOT / "platform" / "terminal_gl_present.cpp",
        REPO_ROOT / "engine" / "render" / "render.cpp",
        REPO_ROOT / "engine" / "render" / "render_resolve.cpp",
        REPO_ROOT / "engine" / "render" / "render_sprite_blit.cpp",
    ]
    found = [f for f in render_files if f.exists()]
    if not found:
        return GateResult(
            "native_gl_extended_glyph_rendered",
            STATUS_OPEN,
            "render.cpp / render_resolve.cpp not found",
        )
    for f in found:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # terminal_gl_present.cpp: usampler2D sidecar + page_atlas conditional
        # render_sprite_blit.cpp: GlyphPlane + glyph_plane write path (future)
        has_sidecar = "usampler2D" in text or "GlyphPlane" in text or "glyph_plane" in text
        has_atlas = "page_atlas" in text or "atlas_of_atlases" in text or "glyph_id > 255" in text
        if has_sidecar and has_atlas:
            receipt_ok, receipt_evidence = _native_material_render_receipt_ok()
            if receipt_ok:
                return GateResult(
                    "native_gl_extended_glyph_rendered",
                    STATUS_PROOF_PASSED,
                    receipt_evidence,
                )
            return GateResult(
                "native_gl_extended_glyph_rendered",
                STATUS_IMPLEMENTED_UNPROVEN,
                f"extended glyph branch found in {f.name}; {receipt_evidence}",
            )
    return GateResult(
        "native_gl_extended_glyph_rendered",
        STATUS_OPEN,
        "render.cpp / render_resolve.cpp exist but contain no extended glyph branch (Phase 3 implementation pending)",
    )


def check_asciiid_shader_extended_glyph_rendered() -> GateResult:
    # Phase 3 ASCIIID implementation gate (distinct from the asciiid_shader_manifest_lookup
    # model-pin artifact gate). Checks that the term_fs_src GLSL string in asciiid.cpp
    # branches on GlyphId > 255 and samples a page atlas.
    p = REPO_ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp"
    if not p.exists():
        return GateResult(
            "asciiid_shader_extended_glyph_rendered",
            STATUS_OPEN,
            "apps/asciicker-presenter/asciiid.cpp not found",
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    # The term_fs_src block contains the live GLSL. Check for the extended branch inside it.
    fs_start = text.find("term_fs_src")
    if fs_start < 0:
        return GateResult(
            "asciiid_shader_extended_glyph_rendered",
            STATUS_OPEN,
            "term_fs_src not found in asciiid.cpp",
        )
    fs_block = text[fs_start:fs_start + 8000]  # generous window covering the shader string
    if ("page_atlas" in fs_block or "atlas_of_atlases" in fs_block) and (
        "glyph_id" in fs_block or "GlyphId" in fs_block or "sidecar" in fs_block
    ):
        return GateResult(
            "asciiid_shader_extended_glyph_rendered",
            STATUS_IMPLEMENTED_UNPROVEN,
            "term_fs_src GLSL contains the extended glyph branch; historical native ImGui/CDP receipts are inadmissible after the HTML-only FL-4588 cutover; awaits a closed-graph HTML receipt",
        )
    return GateResult(
        "asciiid_shader_extended_glyph_rendered",
        STATUS_OPEN,
        "term_fs_src GLSL exists but contains no extended glyph branch (Phase 3 ASCIIID implementation pending)",
    )


def check_asciiid_picker_extended_glyph_authored() -> GateResult:
    # Phase 4 ASCIIID implementation gate (distinct from the asciiid_input_model_pinned
    # model-pin artifact gate). Checks that the picker UI browses manifest.entries
    # rather than (or in addition to) the flat CP437 16x16 grid.
    p = REPO_ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp"
    if not p.exists():
        return GateResult(
            "asciiid_picker_extended_glyph_authored",
            STATUS_OPEN,
            "apps/asciicker-presenter/asciiid.cpp not found",
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    if ("manifest.entries" in text or "glyph_manifest" in text) and (
        "entry_count" in text or "admission_set" in text
    ):
        return GateResult(
            "asciiid_picker_extended_glyph_authored",
            STATUS_IMPLEMENTED_UNPROVEN,
            "native source retains manifest catalog mechanics, but its retired ImGui picker is not a product surface; awaits the user-reachable HTML picker leaf plus a closed-graph receipt",
        )
    return GateResult(
        "asciiid_picker_extended_glyph_authored",
        STATUS_OPEN,
        "picker UI contains no manifest.entries browse (Phase 4 ASCIIID picker implementation pending)",
    )


def check_asciiid_extended_preset_catalog() -> GateResult:
    p = REPO_ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp"
    if not p.exists():
        return GateResult(
            "asciiid_extended_preset_catalog",
            STATUS_OPEN,
            "apps/asciicker-presenter/asciiid.cpp not found",
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    generated_json = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.json"
    generated_header = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.generated.h"
    shape_catalog = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
    rules = REPO_ROOT / "assets/glyphs/presets/material_shape_preset_rules.json"
    required_source_tokens = [
        "material_shape_presets.generated.h",
        "Extended Shape Presets",
        "AsciiidExtendedPresetGlyphForCell",
        "DrawAsciiidExtendedGlyphPresetStripButton",
        "ImGuiItemFlags_Disabled",
    ]
    missing = [item for item in required_source_tokens if item not in text]
    if missing:
        return GateResult(
            "asciiid_extended_preset_catalog",
            STATUS_OPEN,
            "missing generated preset catalog source tokens: " + ", ".join(missing),
        )
    if not all(path.exists() for path in (generated_json, generated_header, shape_catalog, rules)):
        missing_artifacts = [str(path.relative_to(REPO_ROOT)) for path in (generated_json, generated_header, shape_catalog, rules) if not path.exists()]
        return GateResult(
            "asciiid_extended_preset_catalog",
            STATUS_OPEN,
            "missing generated preset artifact(s): " + ", ".join(missing_artifacts),
        )
    try:
        preset_doc = json.loads(generated_json.read_text(encoding="utf-8"))
        purpose_names = {p.get("purpose") for p in preset_doc.get("presets", [])}
    except Exception as exc:
        return GateResult(
            "asciiid_extended_preset_catalog",
            STATUS_OPEN,
            f"generated preset JSON unreadable: {exc!r}",
        )
    required_purposes = {"Grass Tops", "Wave Flow", "Flower Tops", "Rock Faces", "Strata", "Corners / Lips", "Fracture"}
    if purpose_names != required_purposes:
        return GateResult(
            "asciiid_extended_preset_catalog",
            STATUS_OPEN,
            f"generated preset purposes mismatch: {sorted(purpose_names)!r}",
        )
    return GateResult(
        "asciiid_extended_preset_catalog",
        STATUS_IMPLEMENTED_UNPROVEN,
        "ASCIIID consumes generated shape preset artifacts; historical native ImGui/CDP receipts are evidence-only; awaits a user-reachable HTML catalog receipt",
    )


def check_asciiid_extended_glyph_browser_visible() -> GateResult:
    """Require the current HTML glyph browser, never the retired ImGui panel."""
    registry_path = REPO_ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    panel_path = REPO_ROOT / "web/asciiid-authoring-shell/src/GlyphRepertoirePanel.tsx"
    app_path = REPO_ROOT / "web/asciiid-authoring-shell/src/App.tsx"
    control_path = REPO_ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    store_path = REPO_ROOT / "web/asciiid-authoring-shell/src/store.ts"
    required_paths = (registry_path, panel_path, app_path, control_path, store_path)
    missing_paths = [
        str(path.relative_to(REPO_ROOT)) for path in required_paths if not path.is_file()
    ]
    if missing_paths:
        return GateResult(
            "asciiid_extended_glyph_browser_visible",
            STATUS_OPEN,
            "missing HTML glyph catalog source: " + ", ".join(missing_paths),
        )
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            "asciiid_extended_glyph_browser_visible",
            STATUS_OPEN,
            f"HTML glyph registry is unreadable: {exc!r}",
        )

    interactions = {
        item.get("leaf_id"): item for item in registry.get("interactions", [])
    }
    selection = interactions.get("glyph.catalog.select") or {}
    repertoire = interactions.get("glyph.filter.repertoire") or {}
    add = interactions.get("glyph.catalog.add_to_active_pool") or {}
    remove = interactions.get("glyph.catalog.remove_from_active_pool") or {}
    panel = panel_path.read_text(encoding="utf-8")
    app = app_path.read_text(encoding="utf-8")
    control = control_path.read_text(encoding="utf-8")
    store = store_path.read_text(encoding="utf-8")

    gaps: list[str] = []
    if selection.get("route") != "/glyphs/catalog":
        gaps.append("registry-backed glyph.catalog.select route")
    if selection.get("action_scope") != "browser_draft":
        gaps.append("browser-draft glyph selection scope")
    if selection.get("action_schema") != "asciiid.browser.select_glyph_catalog_entry.v1":
        gaps.append("explicit glyph selection schema")
    if selection.get("write_set_template") != [
        "/browser/glyphs/selected_glyph_id"
    ]:
        gaps.append("single selected GlyphId write set")
    if repertoire.get("route") != "/glyphs/catalog" or "/glyphs/search" in app:
        gaps.append("single /glyphs/catalog route")
    for item, name in ((add, "add"), (remove, "remove")):
        if "browser.glyphs.selected_glyph_id" not in item.get("read_model_fields", []):
            gaps.append(f"{name} selected GlyphId read")
    for token, name in (
        ('type="button"', "clickable glyph cells"),
        ("aria-pressed", "selected glyph accessibility"),
        ("data-leaf-id={selectionLeaf.leaf_id}", "registry leaf identity"),
        ("unicodeLabel(entry.unicode_scalar)", "Unicode identity"),
    ):
        if token not in panel:
            gaps.append(name)
    if "selectedGlyphId: number | null" not in store:
        gaps.append("sole selected-glyph store owner")
    if "commitGlyphCatalogPoolAction" not in control:
        gaps.append("selected GlyphId transaction consumption")
    if "glyph_id: typeof raw" in control or 'lane_id: "ramp.0"' in control:
        gaps.append("deleted numeric GlyphId plus fixed-lane owner")

    if gaps:
        return GateResult(
            "asciiid_extended_glyph_browser_visible",
            STATUS_OPEN,
            "HTML glyph catalog closed graph is incomplete: " + "; ".join(gaps),
        )
    return GateResult(
        "asciiid_extended_glyph_browser_visible",
        STATUS_IMPLEMENTED_UNPROVEN,
        "one registry-backed /glyphs/catalog grid owns browser-selected GlyphId and feeds the typed add/remove candidate transactions; no headed closed-graph receipt yet",
    )


def check_unknown_glyph_fallback_all_targets() -> GateResult:
    # Aggregate fallback gate: all four targets must have the extended glyph
    # fallback path wired AND at minimum implemented_unproven before this gate
    # advances beyond open. Phase 3 native GL and ASCIIID shader implementation
    # gates (new) must clear before this umbrella can advance.
    sub_gates = {
        "extended_sprite_roundtrip": check_extended_sprite_roundtrip,
        "web_extended_glyph_buffer": check_web_extended_glyph_buffer,
        "multiplayer_manifest_hash_match": check_multiplayer_manifest_hash_match,
        "native_gl_extended_glyph_rendered": check_native_gl_extended_glyph_rendered,
        "asciiid_shader_extended_glyph_rendered": check_asciiid_shader_extended_glyph_rendered,
    }
    still_open = []
    for name, fn in sub_gates.items():
        try:
            r = fn()
        except Exception as exc:
            r = GateResult(name, STATUS_OPEN, f"check raised: {exc!r}")
        if r.status == STATUS_OPEN:
            still_open.append(name)
    if still_open:
        return GateResult(
            "unknown_glyph_fallback_all_targets",
            STATUS_OPEN,
            "sub-gates still open: " + ", ".join(still_open),
        )
    local_fallback = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/phase_d_multiplayer_fallback_agreement_local_two_tab.json"
    if local_fallback.exists():
        try:
            receipt = json.loads(local_fallback.read_text(encoding="utf-8"))
        except Exception:
            receipt = {}
        proof_points = receipt.get("proof_points", {}) if isinstance(receipt, dict) else {}
        if (
            receipt.get("verdict") == "PASS"
            and proof_points.get("evidence_recorder_captured_extended_fallback_render_event") is True
            and proof_points.get("gameplay_no_silent_glyph_truncation_in_either_tab") is True
            and proof_points.get("gameplay_fallback_render_agreement_between_tabs") is True
        ):
            return GateResult(
                "unknown_glyph_fallback_all_targets",
                STATUS_IMPLEMENTED_UNPROVEN,
                "all required sub-gates are at least implemented_unproven; local two-tab fallback agreement plus recorder fallback event PASS; VPS proof still required",
            )
    return GateResult(
        "unknown_glyph_fallback_all_targets",
        STATUS_IMPLEMENTED_UNPROVEN,
        "all required sub-gates are at least implemented_unproven; awaits full proof across all targets",
    )


def check_extended_material_roundtrip() -> GateResult:
    # Phase 2 = backend material GlyphId carrier only. Engine manifest parser/hash
    # verification + .a3d material sidecar + payload support checks into
    # MaterialGlyphPlane + glyph_coverage_lookup extended coverage status.
    # Preserves MatCell.gl/AnsiCell.gl as CP437 fallback bytes only. No
    # ASCIIID picker exposure (Phase 4), no web buffer change (Phase 5), no
    # multiplayer hash carriage (Phase 7), no visual-map extended paint, no
    # proof_passed state without operator receipt.
    # IMPLEMENTED 2026-05-28: MaterialGlyphPlane carrier, material sidecar parser,
    # Material.glyph_plane wired into all load paths. Manifest hash verification
    # and support checks follow the sprite pattern. Gate stays implemented_unproven.
    material_glyph_plane_h = REPO_ROOT / "engine" / "material_glyph_plane.h"
    material_glyph_plane_cpp = REPO_ROOT / "engine" / "material_glyph_plane.cpp"
    material_sidecar_h = REPO_ROOT / "engine" / "material_sidecar.h"
    material_sidecar_cpp = REPO_ROOT / "engine" / "material_sidecar.cpp"
    if not all(p.exists() for p in [material_glyph_plane_h, material_glyph_plane_cpp, material_sidecar_h, material_sidecar_cpp]):
        return GateResult(
            "extended_material_roundtrip",
            STATUS_OPEN,
            "Phase 2 backend material GlyphId carrier: MaterialGlyphPlane or material sidecar parser missing",
        )
    if RUN_PROOFS:
        return GateResult(
            "extended_material_roundtrip",
            STATUS_IMPLEMENTED_UNPROVEN,
            "Retired material-grid GlyphId proof runner deleted. FL-4260 direct Material Rendering Profile edit is now the active owner; use current Rendering/TERM++ proof instead.",
        )
    receipt = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/phase_d_asciiid_extended_material_roundtrip.json"
    if receipt.exists():
        return GateResult(
            "extended_material_roundtrip",
            STATUS_IMPLEMENTED_UNPROVEN,
            "Phase D ASCIIID receipt exists for the retired material-grid proof. Current direct profile-edit proof lives under FL-4260 Rendering/TERM++ evidence; --run-proofs no longer runs the retired owner.",
        )
    return GateResult(
        "extended_material_roundtrip",
        STATUS_IMPLEMENTED_UNPROVEN,
        "Phase 2 backend material GlyphId carrier: MaterialGlyphPlane + material sidecar parser implemented; manifest hash verification + support checks follow sprite pattern; Material.glyph_plane wired into mainmenu.cpp, game_render_bridge.cpp, asciiid.cpp; build wired in apps/game-fl4512/makefile_game_mac; current proof owner is FL-4260 Rendering/TERM++.",
    )


def check_extended_sprite_roundtrip() -> GateResult:
    # Phase 2 = backend admission ONLY. Engine manifest parser/hash
    # verification + .xp sprite sidecar admission into GlyphPlane +
    # glyph_coverage_lookup extended-admitted status. Replaces the VAL-03
    # "extended glyph loader not implemented until Phase 2" fail-close with
    # manifest-verified admission. No ASCIIID picker (Phase 4), no shader
    # lookup (Phase 3), no web buffer (Phase 5), no multiplayer (Phase 7),
    # no proof_passed without operator receipt.
    manifest_cpp = REPO_ROOT / "engine" / "glyph_manifest.cpp"
    manifest_hdr = REPO_ROOT / "engine" / "glyph_manifest.h"
    if not manifest_cpp.exists() or not manifest_hdr.exists():
        return GateResult(
            "extended_sprite_roundtrip",
            STATUS_OPEN,
            "engine/glyph_manifest.{h,cpp} missing",
        )
    parity_script = REPO_ROOT / "scripts" / "test_glyph_manifest_parity.py"
    if not parity_script.exists():
        return GateResult(
            "extended_sprite_roundtrip",
            STATUS_OPEN,
            "scripts/test_glyph_manifest_parity.py missing",
        )
    # Run parity test
    proc = subprocess.run(
        [sys.executable, str(parity_script), "--with-c"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        return GateResult(
            "extended_sprite_roundtrip",
            STATUS_OPEN,
            f"test_glyph_manifest_parity.py --with-c failed (exit={proc.returncode})",
        )
    # Check fixture presence
    for fname in ("fl4131_phase2_valid.xp", "fl4131_phase2_valid.xp.glyph_profile.json"):
        if not (REPO_ROOT / "assets" / "glyphs" / "fixtures" / fname).exists():
            return GateResult(
                "extended_sprite_roundtrip",
                STATUS_OPEN,
                f"fixture {fname} missing",
            )
    # Build artifact check
    if not (REPO_ROOT / ".run" / "game").exists():
        return GateResult(
            "extended_sprite_roundtrip",
            STATUS_OPEN,
            "build artifact .run/game missing",
        )
    return GateResult(
        "extended_sprite_roundtrip",
        STATUS_IMPLEMENTED_UNPROVEN,
        "engine/glyph_manifest.{h,cpp} exists; test_glyph_manifest_parity.py --with-c PASS; fixtures present; build OK; awaits gameplay-entered proof",
    )


def check_web_extended_glyph_buffer() -> GateResult:
    # Phase 5 web cell-buffer / fragment shader gate. MODEL_PIN comments are
    # USEFUL ANCHORS BUT DO NOT COUNT AS IMPLEMENTATION. This gate means the
    # web runtime has actual extended GlyphId carriage: a real C-side sidecar
    # buffer allocation + JS-visible exports + shader-side LUT/page-atlas
    # lookup + a diagnostic-fallback path (operator contract: black-on-red '!'
    # for unknown ids, NOT a silent CP437 substitute). The string markers below
    # are deliberately chosen to live ONLY in real code paths, not in MODEL_PIN
    # comment blocks.
    pin = _check_marker_gate("web_extended_glyph_buffer")
    if pin.status != STATUS_IMPLEMENTED_UNPROVEN:
        return pin

    cpp = REPO_ROOT / "web" / "game_web.cpp"
    html = REPO_ROOT / "web" / "game_web.html"
    try:
        cpp_text = cpp.read_text(encoding="utf-8")
        html_text = html.read_text(encoding="utf-8")
    except Exception as exc:
        return GateResult(
            "web_extended_glyph_buffer",
            STATUS_OPEN,
            f"unable to inspect web sources: {exc}",
        )

    # Real C-side storage: stable token + actual allocation + JS export
    missing_cpp: list[str] = []
    if "FL-4131 PHASE_5_SIDECAR_ALLOC" not in cpp_text:
        missing_cpp.append("PHASE_5_SIDECAR_ALLOC token")
    if "uint32_t* glyph_sidecar" not in cpp_text:
        missing_cpp.append("uint32_t* glyph_sidecar declaration")
    if "calloc(GLYPH_SIDECAR_W" not in cpp_text and "calloc(160 * 160" not in cpp_text:
        missing_cpp.append("calloc(GLYPH_SIDECAR_W*GLYPH_SIDECAR_H,sizeof(uint32_t)) allocation")
    if "GetGlyphSidecar()" not in cpp_text:
        missing_cpp.append("GetGlyphSidecar() JS export")

    # Real shader path: diagnostic-fallback uniforms + second-pass LUT
    missing_shader: list[str] = []
    if "uniform sampler2D" not in html_text:
        missing_shader.append("sidecar/lut/page_atlas samplers")
    if "fallback_glyph_id" not in html_text:
        missing_shader.append("fallback_glyph_id uniform")
    if "fallback_diag_fg" not in html_text or "fallback_diag_bg" not in html_text:
        missing_shader.append("fallback_diag_fg/fallback_diag_bg diagnostic-color uniforms")
    if "page_atlas" not in html_text and "atlas_of_atlases" not in html_text:
        missing_shader.append("page_atlas / atlas_of_atlases binding")
    if "cell.z * 255.00" in html_text:
        missing_shader.append("legacy 'cell.z * 255.00' exact string still present")
    if "_GetGlyphSidecar" not in html_text:
        missing_shader.append("JS upload path not wired (_GetGlyphSidecar not called)")
    if "ak_sidecar_tex" not in html_text:
        missing_shader.append("ak_sidecar_tex per-frame upload binding")

    if not missing_cpp and not missing_shader:
        receipt_ok, receipt_evidence = _web_admitted_extended_receipt_ok()
        if receipt_ok:
            return GateResult(
                "web_extended_glyph_buffer",
                STATUS_PROOF_PASSED,
                receipt_evidence,
            )
        return GateResult(
            "web_extended_glyph_buffer",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"C-side sidecar (alloc + JS export) and shader (samplers + diagnostic uniforms + per-frame upload) wired; {receipt_evidence}",
        )
    parts = []
    if missing_cpp:
        parts.append("game_web.cpp missing: " + "; ".join(missing_cpp))
    if missing_shader:
        parts.append("game_web.html missing: " + "; ".join(missing_shader))
    return GateResult(
        "web_extended_glyph_buffer",
        STATUS_OPEN,
        "real implementation gaps — " + " | ".join(parts),
    )


def check_multiplayer_manifest_hash_match() -> GateResult:
    # Phase 7 join/reset handshake hash gate. MODEL_PIN comments are anchors
    # only; this gate requires the protocol shape itself to carry and reject
    # on glyph manifest identity.
    pin = _check_marker_gate("multiplayer_manifest_hash_match")
    if pin.status != STATUS_IMPLEMENTED_UNPROVEN:
        return pin

    proto = REPO_ROOT / "server" / "protocol" / "protocol_join.h"
    try:
        text = proto.read_text(encoding="utf-8")
    except Exception as exc:
        return GateResult(
            "multiplayer_manifest_hash_match",
            STATUS_OPEN,
            f"unable to inspect protocol_join.h: {exc}",
        )

    def _struct_body(name: str) -> str:
        marker = f"struct {name}"
        start = text.find(marker)
        if start < 0:
            return ""
        brace = text.find("{", start)
        end = text.find("};", brace)
        if brace < 0 or end < 0:
            return ""
        return text[brace:end]

    req = _struct_body("STRUCT_REQ_JOIN_V2")
    rsp = _struct_body("STRUCT_RSP_JOIN")
    enum_prefix = text.split("#pragma pack(push,1)", 1)[0]
    has_wire_fields = (
        "glyph_manifest_hash" in req
        and "content_pack_id" in req
        and "glyph_manifest_hash" in rsp
        and "content_pack_id" in rsp
    )
    has_reject_reason = "GLYPH_MANIFEST_MISMATCH" in enum_prefix
    if has_wire_fields and has_reject_reason:
        mismatch_receipt = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/phase_d_multiplayer_manifest_mismatch_local_two_tab.json"
        fallback_receipt = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/phase_d_multiplayer_fallback_agreement_local_two_tab.json"
        admitted_receipt = REPO_ROOT / "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/phase_d_multiplayer_admitted_extended_local_two_tab.json"
        mismatch_ok = False
        fallback_ok = False
        admitted_ok = False
        mismatch_gap = f"missing {mismatch_receipt.relative_to(REPO_ROOT)}"
        fallback_gap = f"missing {fallback_receipt.relative_to(REPO_ROOT)}"
        admitted_gap = f"missing {admitted_receipt.relative_to(REPO_ROOT)}"
        if mismatch_receipt.exists():
            try:
                receipt_data = json.loads(mismatch_receipt.read_text(encoding="utf-8"))
            except Exception as exc:
                receipt_data = {}
                mismatch_gap = f"mismatch receipt parse failed: {exc!r}"
            proof_points = receipt_data.get("proof_points", {}) if isinstance(receipt_data, dict) else {}
            mismatch_ok = (
                receipt_data.get("verdict") == "PASS"
                and proof_points.get("scenario_two_tab_join_handshake_reject_observed") is True
                and proof_points.get("evidence_recorder_captured_reject_reason_code") is True
                and proof_points.get("accepted_tab_joined_without_reject") is True
                and proof_points.get("accepted_tab_recorder_hash_match") is True
            )
            if not mismatch_ok and "parse failed" not in mismatch_gap:
                mismatch_gap = "mismatch receipt does not prove reject + accepted-tab hash-match"
        if fallback_receipt.exists():
            try:
                fallback_data = json.loads(fallback_receipt.read_text(encoding="utf-8"))
            except Exception as exc:
                fallback_data = {}
                fallback_gap = f"fallback receipt parse failed: {exc!r}"
            proof_points = fallback_data.get("proof_points", {}) if isinstance(fallback_data, dict) else {}
            fallback_ok = (
                fallback_data.get("verdict") == "PASS"
                and proof_points.get("evidence_recorder_captured_extended_fallback_render_event") is True
                and proof_points.get("evidence_browser_captured_extended_fallback_render_event") is True
                and proof_points.get("gameplay_no_silent_glyph_truncation_in_either_tab") is True
                and proof_points.get("gameplay_fallback_render_agreement_between_tabs") is True
                and proof_points.get("accepted_tabs_joined_without_reject") is True
                and proof_points.get("accepted_tabs_recorder_hash_match") is True
            )
            if not fallback_ok and "parse failed" not in fallback_gap:
                fallback_gap = "fallback receipt does not prove accepted two-tab fallback agreement"
        if admitted_receipt.exists():
            try:
                admitted_data = json.loads(admitted_receipt.read_text(encoding="utf-8"))
            except Exception as exc:
                admitted_data = {}
                admitted_gap = f"admitted receipt parse failed: {exc!r}"
            proof_points = admitted_data.get("proof_points", {}) if isinstance(admitted_data, dict) else {}
            admitted_ok = (
                admitted_data.get("verdict") == "PASS"
                and proof_points.get("evidence_browser_captured_admitted_extended_render_event") is True
                and proof_points.get("evidence_manifest_bound_for_admitted_content") is True
                and proof_points.get("gameplay_no_silent_glyph_truncation_in_either_tab") is True
                and proof_points.get("gameplay_admitted_extended_render_agreement_between_tabs") is True
                and proof_points.get("accepted_tabs_joined_without_reject") is True
                and proof_points.get("accepted_tabs_recorder_hash_match") is True
            )
            if not admitted_ok and "parse failed" not in admitted_gap:
                admitted_gap = "admitted receipt does not prove accepted two-tab admitted extended render agreement"
        if mismatch_ok and fallback_ok and admitted_ok:
            return GateResult(
                "multiplayer_manifest_hash_match",
                STATUS_PROOF_PASSED,
                "local two-tab receipts PASS: bad glyph manifest rejects with recorder reason; accepted tabs join/hash-match, agree on admitted extended GlyphIds, and agree on shader-owned black-on-red fallback without silent truncation",
            )
        gaps = []
        if not mismatch_ok:
            gaps.append(mismatch_gap)
        if not fallback_ok:
            gaps.append(fallback_gap)
        if not admitted_ok:
            gaps.append(admitted_gap)
        return GateResult(
            "multiplayer_manifest_hash_match",
            STATUS_IMPLEMENTED_UNPROVEN,
            "protocol carries glyph_manifest_hash/content_pack_id in join response/request and reserves GLYPH_MANIFEST_MISMATCH; awaits complete local two-tab proof: " + "; ".join(gaps),
        )
    missing = []
    if not has_wire_fields:
        missing.append("STRUCT_REQ_JOIN_V2/STRUCT_RSP_JOIN glyph_manifest_hash+content_pack_id fields")
    if not has_reject_reason:
        missing.append("APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH enum slot")
    return GateResult(
        "multiplayer_manifest_hash_match",
        STATUS_OPEN,
        "MODEL_PIN artifact present, but protocol wire migration missing: " + "; ".join(missing),
    )


def check_multiplayer_unknown_glyph_fallback_fixture() -> GateResult:
    # Phase 7 fixture artifact. Input for the two-tab VPS proof. Authoring
    # the fixture is gated on Phase 2 backend admission + Phase 3 native GL
    # fallback render + Phase 5 web buffer so both tabs have a real
    # renderable fallback path to observe. Phase 2 alone does NOT close
    # this gate.
    fx = REPO_ROOT / "assets/glyphs/fixtures/multiplayer_unknown_glyph_fallback.json"
    if fx.exists():
        return GateResult(
            "multiplayer_unknown_glyph_fallback_fixture",
            STATUS_ARTIFACT_EXISTS,
            str(fx.relative_to(REPO_ROOT)),
        )
    return GateResult(
        "multiplayer_unknown_glyph_fallback_fixture",
        STATUS_OPEN,
        "Phase 7 multiplayer fallback fixture; gated on Phase 2 admission + Phase 3 native fallback render landing first",
    )


def check_no_silent_glyph_truncation() -> GateResult:
    # Acceptance for this gate is gameplay-visible. Scripts and build
    # diagnostics may demonstrate that the implementation routes extended
    # GlyphIds to the fail-closed branch in isolation, but menu-only or
    # first-frame probes do not show that the real game path is visually safe.
    # A valid receipt must include gameplay-entered evidence (for example via
    # CDP/Playwright/MCP/headed input) plus Accepted: YES.
    sprite = REPO_ROOT / "engine/sprite.cpp"
    compositor = REPO_ROOT / "engine/glyph_compositor.cpp"
    lookup = REPO_ROOT / "engine/glyph_coverage_lookup.cpp"
    lookup_hdr = REPO_ROOT / "engine/glyph_coverage_lookup.h"
    compositor_hdr = REPO_ROOT / "engine/glyph_compositor.h"

    if not sprite.exists():
        return GateResult("no_silent_glyph_truncation", STATUS_OPEN, "engine/sprite.cpp missing")

    impl_files = [compositor, compositor_hdr, lookup, lookup_hdr]
    missing_impl = [f for f in impl_files if not f.exists()]
    if missing_impl:
        return GateResult(
            "no_silent_glyph_truncation",
            STATUS_OPEN,
            "implementation incomplete; missing: "
            + ",".join(str(p.relative_to(REPO_ROOT)) for p in missing_impl),
        )

    # Implementation present. Read the manual inspection receipt and
    # classify by its recorded Accepted: line.
    receipt = REPO_ROOT / MANUAL_INSPECTION_RECEIPT_REL
    if not receipt.exists():
        return GateResult(
            "no_silent_glyph_truncation",
            STATUS_MANUAL_INSPECTION_REQUIRED,
            f"engine renderer coverage lookup path moved through "
            f"glyph_coverage_lookup + glyph_compositor; manual inspection "
            f"receipt missing at {MANUAL_INSPECTION_RECEIPT_REL}",
        )
    try:
        txt = receipt.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return GateResult(
            "no_silent_glyph_truncation",
            STATUS_MANUAL_INSPECTION_REQUIRED,
            f"manual inspection receipt unreadable: {exc}",
        )
    receipt_ready, receipt_reason = _manual_receipt_ready_for_no_truncation(txt)
    if receipt_ready:
        return GateResult(
            "no_silent_glyph_truncation",
            STATUS_PROOF_PASSED,
            f"manual inspection receipt accepted: {receipt_reason} ({MANUAL_INSPECTION_RECEIPT_REL})",
        )
    # Note Phase F gameplay-entered proof driver if it exists.
    phase_f_driver = REPO_ROOT / "scripts/proofs/proof_fl4131_extended_glyph.js"
    phase_f_receipts = sorted(
        (REPO_ROOT / "docs/research/ascii/verification/fl4131/manual").glob(
            "*-gameplay-entered-web-proof.json"
        )
    )
    phase_f_note = ""
    if phase_f_driver.exists():
        if phase_f_receipts:
            last = phase_f_receipts[-1].name
            phase_f_note = f"; Phase-F driver + receipt ({last}) present, awaiting operator signoff"
        else:
            phase_f_note = "; Phase-F driver present (scripts/proofs/proof_fl4131_extended_glyph.js), no receipt yet"
    return GateResult(
        "no_silent_glyph_truncation",
        STATUS_MANUAL_INSPECTION_REQUIRED,
        f"implementation present (glyph_coverage_lookup + glyph_compositor); "
        f"manual inspection receipt incomplete: {receipt_reason}"
        f"{phase_f_note} "
        f"({MANUAL_INSPECTION_RECEIPT_REL})",
    )


def check_legacy_assets_no_diff_after_font_capacity() -> GateResult:
    ok, evidence = _run_w2_verify()
    if ok:
        return GateResult("legacy_assets_no_diff_after_font_capacity", STATUS_PROOF_PASSED, evidence)
    harness = REPO_ROOT / "scripts/fl4131_legacy_cp437_no_diff.py"
    if harness.exists():
        return GateResult(
            "legacy_assets_no_diff_after_font_capacity",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"W2 harness on disk but verify did not pass: {evidence}",
        )
    return GateResult(
        "legacy_assets_no_diff_after_font_capacity",
        STATUS_OPEN,
        "W2 no-diff harness not yet present",
    )


def check_glyph_storage_layout_decision_pinned() -> GateResult:
    # Phase -1 storage layout decision (commit 13c85bf5a). Authoritative artifact
    # is engine/glyph_id.h which carries the glyph_storage_layout_decision_pinned
    # marker in its PURPOSE/CONTRACT block by design.
    glyph_id_hdr = REPO_ROOT / "engine/glyph_id.h"
    if glyph_id_hdr.exists():
        text = glyph_id_hdr.read_text(encoding="utf-8", errors="replace")
        if "glyph_storage_layout_decision_pinned" in text:
            return GateResult(
                "glyph_storage_layout_decision_pinned",
                STATUS_ARTIFACT_EXISTS,
                "engine/glyph_id.h carries glyph_storage_layout_decision_pinned marker (Phase -1 contract)",
            )
    # Fallback: named doc under docs/
    cand_docs = list((REPO_ROOT / "docs").rglob("*storage_layout*")) + \
                list((REPO_ROOT / "docs").rglob("*phase_minus_1*")) + \
                list((REPO_ROOT / "docs").rglob("*glyph_layout*"))
    if cand_docs:
        return GateResult(
            "glyph_storage_layout_decision_pinned",
            STATUS_ARTIFACT_EXISTS,
            "decision doc: " + str(cand_docs[0].relative_to(REPO_ROOT)),
        )
    # Fallback: canon spec literal
    spec = REPO_ROOT / "docs/plans/2026-03-22-multiplayer-canonical-spec.md"
    if spec.exists():
        text = spec.read_text(encoding="utf-8", errors="replace")
        if "glyph_storage_layout_decision_pinned" in text or "Phase -1" in text:
            return GateResult(
                "glyph_storage_layout_decision_pinned",
                STATUS_IMPLEMENTED_UNPROVEN,
                "marker/Phase -1 reference in canon spec",
            )
    return GateResult(
        "glyph_storage_layout_decision_pinned",
        STATUS_OPEN,
        "no storage-layout decision doc found",
    )


def check_glyph_sidecar_parser_python_exists() -> GateResult:
    p = REPO_ROOT / "scripts/glyph_sidecar.py"
    if p.exists():
        return GateResult(
            "glyph_sidecar_parser_python_exists",
            STATUS_ARTIFACT_EXISTS,
            "scripts/glyph_sidecar.py",
        )
    return GateResult(
        "glyph_sidecar_parser_python_exists",
        STATUS_OPEN,
        "scripts/glyph_sidecar.py missing",
    )


def check_glyph_sidecar_parser_engine_exists() -> GateResult:
    ok, info = _exists("engine/glyph_sidecar.h", "engine/glyph_sidecar.cpp")
    if ok:
        return GateResult(
            "glyph_sidecar_parser_engine_exists",
            STATUS_ARTIFACT_EXISTS,
            "engine/glyph_sidecar.{h,cpp}",
        )
    return GateResult(
        "glyph_sidecar_parser_engine_exists",
        STATUS_OPEN,
        "missing: " + ",".join(info),
    )


def check_glyph_sidecar_parsers_contract_parity() -> GateResult:
    # Run with --with-c: proof_passed requires both parsers to agree on all corpus cases.
    # Python-only is implemented_unproven because the gate name says "parsers_contract_parity".
    ok, evidence = _run_parity_test(with_c=True)
    if ok:
        return GateResult(
            "glyph_sidecar_parsers_contract_parity",
            STATUS_PROOF_PASSED,
            f"scripts/test_glyph_sidecar_parity.py --with-c returned 0; {evidence}",
        )
    # C parity did not pass; check if Python at least passes for the evidence label.
    py_ok, _py_ev = _run_parity_test(with_c=False)
    test_script = REPO_ROOT / "scripts/test_glyph_sidecar_parity.py"
    if test_script.exists():
        note = "Python OK, C not proven (build engine/glyph_sidecar_test.c)" if py_ok else f"parity test on disk but failed: {evidence}"
        return GateResult(
            "glyph_sidecar_parsers_contract_parity",
            STATUS_IMPLEMENTED_UNPROVEN,
            note,
        )
    return GateResult(
        "glyph_sidecar_parsers_contract_parity",
        STATUS_OPEN,
        f"no parity test: {evidence}",
    )


def check_engine_json_library_vendored() -> GateResult:
    ok, info = _exists("engine/third_party/cjson/cJSON.h", "engine/third_party/cjson/cJSON.c")
    if ok:
        return GateResult(
            "engine_json_library_vendored",
            STATUS_ARTIFACT_EXISTS,
            "engine/third_party/cjson/cJSON.{h,c}",
        )
    return GateResult(
        "engine_json_library_vendored",
        STATUS_OPEN,
        "missing: " + ",".join(info),
    )


def check_val03_sidecar_branch_wired() -> GateResult:
    # Build inputs wired AND sprite.cpp has the sidecar branch.
    sprite = REPO_ROOT / "engine/sprite.cpp"
    if not sprite.exists():
        return GateResult("val03_sidecar_branch_wired", STATUS_OPEN, "engine/sprite.cpp missing")
    text = sprite.read_text(encoding="utf-8", errors="replace")
    has_branch = "glyph_sidecar_exists" in text and "glyph_sidecar_parse_for_xp" in text
    if not has_branch:
        return GateResult(
            "val03_sidecar_branch_wired",
            STATUS_OPEN,
            "sprite.cpp does not call glyph_sidecar_exists+parse",
        )
    # Check build inputs include the sidecar TU.
    mk = (REPO_ROOT / "apps/game-fl4512/makefile_game_mac").read_text(encoding="utf-8", errors="replace") \
        if (REPO_ROOT / "apps/game-fl4512/makefile_game_mac").exists() else ""
    bw = (REPO_ROOT / "build-web.sh").read_text(encoding="utf-8", errors="replace") \
        if (REPO_ROOT / "build-web.sh").exists() else ""
    in_mk = "engine/glyph_sidecar.cpp" in mk and "engine/glyph_plane.cpp" in mk
    in_bw = "engine/glyph_sidecar.cpp" in bw and "engine/glyph_plane.cpp" in bw
    # Phase 1: GlyphPlane carrier is now wired; has_sidecar_for_frames handoff present.
    has_phase1_handoff = "has_sidecar_for_frames" in text and "glyph_plane_alloc" in text
    if in_mk and in_bw and has_phase1_handoff:
        return GateResult(
            "val03_sidecar_branch_wired",
            STATUS_IMPLEMENTED_UNPROVEN,
            "Phase 1: branch + GlyphPlane handoff + both build inputs wired; awaits headed proof",
        )
    if in_mk and in_bw:
        return GateResult(
            "val03_sidecar_branch_wired",
            STATUS_IMPLEMENTED_UNPROVEN,
            "branch + both build inputs wired (glyph_plane.cpp); awaits headed proof",
        )
    return GateResult(
        "val03_sidecar_branch_wired",
        STATUS_OPEN,
        f"branch present but build wiring incomplete: makefile={in_mk} build-web={in_bw}",
    )


def check_bundle_compile_identity_includes_glyph_manifest_hash() -> GateResult:
    p = REPO_ROOT / "scripts/compile_actor_visual_profiles.py"
    if not p.exists():
        return GateResult(
            "bundle_compile_identity_includes_glyph_manifest_hash",
            STATUS_OPEN,
            "scripts/compile_actor_visual_profiles.py missing",
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    if "ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256" in text:
        return GateResult(
            "bundle_compile_identity_includes_glyph_manifest_hash",
            STATUS_IMPLEMENTED_UNPROVEN,
            "ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 emitted; runtime not yet reading it",
        )
    return GateResult(
        "bundle_compile_identity_includes_glyph_manifest_hash",
        STATUS_OPEN,
        "no ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 emission (awaits W4 Stage A)",
    )


def check_bundle_compiler_rejects_extended_glyph_without_manifest_hash() -> GateResult:
    p = REPO_ROOT / "scripts/compile_actor_visual_profiles.py"
    if not p.exists():
        return GateResult(
            "bundle_compiler_rejects_extended_glyph_without_manifest_hash",
            STATUS_OPEN,
            "scripts/compile_actor_visual_profiles.py missing",
        )
    text = p.read_text(encoding="utf-8", errors="replace")
    if (
        "_assert_glyphs_admitted_for_source" in text
        or "_scan_inputs_for_extended_glyphs" in text
        or "extended_glyph_without_manifest_hash" in text
    ):
        if RUN_PROOFS:
            ok, evidence = _run_actor_visual_compile_admission_test()
            if ok:
                return GateResult(
                    "bundle_compiler_rejects_extended_glyph_without_manifest_hash",
                    STATUS_PROOF_PASSED,
                    "actor visual compile admission regression passed: "
                    "valid sidecar fixture admitted; unadmitted extended fixture and combat sprite denied; "
                    + evidence,
                )
            return GateResult(
                "bundle_compiler_rejects_extended_glyph_without_manifest_hash",
                STATUS_IMPLEMENTED_UNPROVEN,
                f"fail-closed scan present but admission regression did not pass: {evidence}",
            )
        return GateResult(
            "bundle_compiler_rejects_extended_glyph_without_manifest_hash",
            STATUS_IMPLEMENTED_UNPROVEN,
            "fail-closed scan present in compile_actor_visual_profiles.py; rerun with --run-proofs to execute admission regression",
        )
    return GateResult(
        "bundle_compiler_rejects_extended_glyph_without_manifest_hash",
        STATUS_OPEN,
        "no fail-closed extended-glyph scan in compile_actor_visual_profiles.py (awaits W4 Stage B)",
    )


def check_bundle_extended_admission_set_enforced() -> GateResult:
    allowlist = REPO_ROOT / "assets/glyphs/admission_allowlist.json"
    enforcer = REPO_ROOT / "scripts/fl4131_admission_check.py"
    if allowlist.exists() and enforcer.exists():
        if RUN_PROOFS:
            allow_ok, allow_evidence = _run_admission_allowlist_check()
            compile_ok, compile_evidence = _run_actor_visual_compile_admission_test()
            if allow_ok and compile_ok:
                return GateResult(
                    "bundle_extended_admission_set_enforced",
                    STATUS_PROOF_PASSED,
                    "allowlist schema check and compile-admission regression passed: "
                    f"{allow_evidence}; {compile_evidence}",
                )
            return GateResult(
                "bundle_extended_admission_set_enforced",
                STATUS_IMPLEMENTED_UNPROVEN,
                "allowlist/enforcer exist but proof did not pass: "
                f"allowlist={allow_ok} ({allow_evidence}); compile={compile_ok} ({compile_evidence})",
            )
        return GateResult(
            "bundle_extended_admission_set_enforced",
            STATUS_IMPLEMENTED_UNPROVEN,
            "allowlist + enforcer on disk; rerun with --run-proofs to execute schema and compile-admission checks",
        )
    if allowlist.exists():
        return GateResult(
            "bundle_extended_admission_set_enforced",
            STATUS_ARTIFACT_EXISTS,
            "allowlist on disk; no enforcer yet",
        )
    return GateResult(
        "bundle_extended_admission_set_enforced",
        STATUS_OPEN,
        "no allowlist on disk (awaits W6)",
    )


# ---------------------------------------------------------------------------
# FL-4131 Shape Lab gates (Task 6).
#
# `shape_catalog_metric_model_truthful`, `shape6_catalog_fields_present`, and
# `shape_lab_fixture_isolated` are structural-and-content gates. The two UX
# gates stay OPEN until the Shape Lab is a user-reachable HTML closed-graph
# surface. Historical native ImGui/CDP receipts are evidence-only and cannot
# promote either gate. None of the five gates may claim renderer closure.
# ---------------------------------------------------------------------------

_SHAPE_LAB_PROFILE_EVAL_REL = "docs/research/ascii/verification/fl4131/shape_lab/latest_profile_eval.json"
_SHAPE_LAB_FIXTURE_SIDECAR_REL = "assets/a3d/fl4131_shape_lab_20x20.a3d.glyph_profile.json"
_SHAPE_CATALOG_REL = "assets/glyphs/generated/material.additive.v1.shape_catalog.json"


def _run_test_script(script_rel: str, timeout: int = 60) -> tuple[bool, str]:
    script = REPO_ROOT / script_rel
    if not script.exists():
        return False, f"missing {script_rel}"
    try:
        proc = subprocess.run(
            ["python3", str(script)],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout running {script_rel}"
    except Exception as exc:
        return False, f"error running {script_rel}: {exc!r}"
    output = (proc.stdout + proc.stderr).strip().splitlines()
    tail = " | ".join(line.strip() for line in output[-3:]) if output else ""
    return proc.returncode == 0, tail or f"returncode={proc.returncode}"


def check_shape_catalog_metric_model_truthful() -> GateResult:
    catalog_path = REPO_ROOT / _SHAPE_CATALOG_REL
    if not catalog_path.exists():
        return GateResult(
            "shape_catalog_metric_model_truthful",
            STATUS_OPEN,
            f"missing {_SHAPE_CATALOG_REL}",
        )
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            "shape_catalog_metric_model_truthful",
            STATUS_ERROR,
            f"could not parse shape catalog: {exc!r}",
        )
    model = catalog.get("metric_model")
    if model == "harri_style_regions_v1":
        return GateResult(
            "shape_catalog_metric_model_truthful",
            STATUS_IMPLEMENTED_UNPROVEN,
            "stale harri_style_regions_v1 label remains in shape catalog",
        )
    if model != "atlas_summary_metrics_v1":
        return GateResult(
            "shape_catalog_metric_model_truthful",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"metric_model is {model!r}, expected atlas_summary_metrics_v1",
        )
    if RUN_PROOFS:
        ok, evidence = _run_test_script("scripts/test_fl4131_shape_catalog_schema.py")
        if ok:
            return GateResult(
                "shape_catalog_metric_model_truthful",
                STATUS_PROOF_PASSED,
                f"metric_model=atlas_summary_metrics_v1; schema test green: {evidence}",
            )
        return GateResult(
            "shape_catalog_metric_model_truthful",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"metric label truthful in JSON but schema test did not pass: {evidence}",
        )
    return GateResult(
        "shape_catalog_metric_model_truthful",
        STATUS_IMPLEMENTED_UNPROVEN,
        "metric_model=atlas_summary_metrics_v1 in JSON; rerun with --run-proofs to execute test_fl4131_shape_catalog_schema.py",
    )


def check_shape6_catalog_fields_present() -> GateResult:
    catalog_path = REPO_ROOT / _SHAPE_CATALOG_REL
    if not catalog_path.exists():
        return GateResult(
            "shape6_catalog_fields_present",
            STATUS_OPEN,
            f"missing {_SHAPE_CATALOG_REL}",
        )
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            "shape6_catalog_fields_present",
            STATUS_ERROR,
            f"could not parse shape catalog: {exc!r}",
        )
    required = {
        "shape6",
        "shape6_norm",
        "shape6_density",
        "shape6_asymmetry_lr",
        "shape6_asymmetry_tb",
        "shape6_diag_ne_sw",
        "shape6_diag_nw_se",
    }
    entries = catalog.get("entries") or []
    if not isinstance(entries, list) or not entries:
        return GateResult(
            "shape6_catalog_fields_present",
            STATUS_IMPLEMENTED_UNPROVEN,
            "shape catalog has no entries",
        )
    missing_per_entry: list[tuple[int, list[str]]] = []
    bad_vector_count = 0
    for index, entry in enumerate(entries):
        missing = sorted(required - set(entry.keys()))
        if missing:
            missing_per_entry.append((index, missing))
            if len(missing_per_entry) >= 3:
                break
            continue
        s6 = entry.get("shape6")
        s6n = entry.get("shape6_norm")
        if not isinstance(s6, list) or len(s6) != 6 or not isinstance(s6n, list) or len(s6n) != 6:
            bad_vector_count += 1
    if missing_per_entry:
        sample = "; ".join(f"entry {i} missing {m}" for i, m in missing_per_entry)
        return GateResult(
            "shape6_catalog_fields_present",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"shape6 fields missing from catalog entries: {sample}",
        )
    if bad_vector_count:
        return GateResult(
            "shape6_catalog_fields_present",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"{bad_vector_count} entries have malformed shape6/shape6_norm vectors",
        )
    if RUN_PROOFS:
        ok, evidence = _run_test_script("scripts/test_fl4131_shape_catalog_schema.py")
        if ok:
            return GateResult(
                "shape6_catalog_fields_present",
                STATUS_PROOF_PASSED,
                f"{len(entries)} entries carry the seven shape6 fields; schema test green: {evidence}",
            )
        return GateResult(
            "shape6_catalog_fields_present",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"fields present in JSON but schema test did not pass: {evidence}",
        )
    return GateResult(
        "shape6_catalog_fields_present",
        STATUS_IMPLEMENTED_UNPROVEN,
        f"{len(entries)} entries carry the seven shape6 fields; rerun with --run-proofs to execute test_fl4131_shape_catalog_schema.py",
    )


def check_shape_lab_fixture_isolated() -> GateResult:
    fixture = REPO_ROOT / "assets/a3d/fl4131_shape_lab_20x20.a3d"
    sidecar = REPO_ROOT / _SHAPE_LAB_FIXTURE_SIDECAR_REL
    sandbox = REPO_ROOT / "assets/a3d/sandbox_20x20.a3d"
    if not fixture.is_file() or not sidecar.is_file():
        missing = [p.name for p in (fixture, sidecar) if not p.is_file()]
        return GateResult(
            "shape_lab_fixture_isolated",
            STATUS_OPEN,
            f"missing fixture artifacts: {missing}",
        )
    if sandbox.exists() and fixture.read_bytes() == sandbox.read_bytes():
        return GateResult(
            "shape_lab_fixture_isolated",
            STATUS_IMPLEMENTED_UNPROVEN,
            "shape-lab fixture is byte-identical to sandbox_20x20.a3d",
        )
    if RUN_PROOFS:
        ok, evidence = _run_test_script("scripts/test_fl4131_shape_lab_fixture.py")
        if ok:
            return GateResult(
                "shape_lab_fixture_isolated",
                STATUS_PROOF_PASSED,
                f"fixture + sidecar exist and isolation test green: {evidence}",
            )
        return GateResult(
            "shape_lab_fixture_isolated",
            STATUS_IMPLEMENTED_UNPROVEN,
            f"fixture exists but isolation test did not pass: {evidence}",
        )
    return GateResult(
        "shape_lab_fixture_isolated",
        STATUS_IMPLEMENTED_UNPROVEN,
        "fixture + sidecar on disk and distinct from sandbox; rerun with --run-proofs to execute test_fl4131_shape_lab_fixture.py",
    )


def check_shape_lab_asciiid_ux_present() -> GateResult:
    return GateResult(
        "shape_lab_asciiid_ux_present",
        STATUS_OPEN,
        "Shape Lab has no user-reachable HTML closed-graph leaf; historical native ImGui/CDP receipts are inadmissible",
    )


def check_shape_lab_visual_comparison_receipt_current() -> GateResult:
    return GateResult(
        "shape_lab_visual_comparison_receipt_current",
        STATUS_OPEN,
        "No HTML-only Shape Lab grouped receipt exists; historical native ImGui/CDP artifacts stay evidence-only",
    )


GATE_CHECKS: dict[str, Callable[[], GateResult]] = {
    "glyph_manifest_directory_tree_exists": check_glyph_manifest_directory_tree_exists,
    "glyph_manifest_schema_file_exists": check_glyph_manifest_schema_file_exists,
    "compile_glyph_manifest_check_wired": check_compile_glyph_manifest_check_wired,
    "extended_glyph_fixture_exists": check_extended_glyph_fixture_exists,
    "rfc8785_sha256_helper_exists": check_rfc8785_sha256_helper_exists,
    "web_atlas_of_atlases_binding": check_web_atlas_of_atlases_binding,
    "legacy_cp437_unchanged": check_legacy_cp437_unchanged,
    "glyph_id_type_pinned": check_glyph_id_type_pinned,
    "extended_xp_profile_discriminated": check_extended_xp_profile_discriminated,
    "val03_legacy_gate_preserved": check_val03_legacy_gate_preserved,
    "extended_glyph_manifest_schema_validate": check_extended_glyph_manifest_schema_validate,
    "extended_glyph_manifest_build_check": check_extended_glyph_manifest_build_check,
    "asciiid_input_model_pinned": check_asciiid_input_model_pinned,
    "asciiid_shader_manifest_lookup": check_asciiid_shader_manifest_lookup,
    "coverage_font_independent_contract": check_coverage_font_independent_contract,
    "coverage_generator_golden_rows": check_coverage_generator_golden_rows,
    "atlas_page_binding_model_pinned": check_atlas_page_binding_model_pinned,
    "cell_texture_schema_versioned": check_cell_texture_schema_versioned,
    "shader_lookup_lut_model_pinned": check_shader_lookup_lut_model_pinned,
    "recorder_extended_glyph_schema": check_recorder_extended_glyph_schema,
    "native_gl_extended_glyph_rendered": check_native_gl_extended_glyph_rendered,
    "asciiid_shader_extended_glyph_rendered": check_asciiid_shader_extended_glyph_rendered,
    "asciiid_picker_extended_glyph_authored": check_asciiid_picker_extended_glyph_authored,
    "asciiid_extended_preset_catalog": check_asciiid_extended_preset_catalog,
    "asciiid_extended_glyph_browser_visible": check_asciiid_extended_glyph_browser_visible,
    "unknown_glyph_fallback_all_targets": check_unknown_glyph_fallback_all_targets,
    "extended_material_roundtrip": check_extended_material_roundtrip,
    "extended_sprite_roundtrip": check_extended_sprite_roundtrip,
    "web_extended_glyph_buffer": check_web_extended_glyph_buffer,
    "multiplayer_manifest_hash_match": check_multiplayer_manifest_hash_match,
    "multiplayer_unknown_glyph_fallback_fixture": check_multiplayer_unknown_glyph_fallback_fixture,
    "no_silent_glyph_truncation": check_no_silent_glyph_truncation,
    "legacy_assets_no_diff_after_font_capacity": check_legacy_assets_no_diff_after_font_capacity,
    "glyph_storage_layout_decision_pinned": check_glyph_storage_layout_decision_pinned,
    "glyph_sidecar_parser_python_exists": check_glyph_sidecar_parser_python_exists,
    "glyph_sidecar_parser_engine_exists": check_glyph_sidecar_parser_engine_exists,
    "glyph_sidecar_parsers_contract_parity": check_glyph_sidecar_parsers_contract_parity,
    "engine_json_library_vendored": check_engine_json_library_vendored,
    "val03_sidecar_branch_wired": check_val03_sidecar_branch_wired,
    "bundle_compile_identity_includes_glyph_manifest_hash": check_bundle_compile_identity_includes_glyph_manifest_hash,
    "bundle_compiler_rejects_extended_glyph_without_manifest_hash": check_bundle_compiler_rejects_extended_glyph_without_manifest_hash,
    "bundle_extended_admission_set_enforced": check_bundle_extended_admission_set_enforced,
    # FL-4131 Shape Lab (Task 6).
    "shape_catalog_metric_model_truthful": check_shape_catalog_metric_model_truthful,
    "shape6_catalog_fields_present": check_shape6_catalog_fields_present,
    "shape_lab_fixture_isolated": check_shape_lab_fixture_isolated,
    "shape_lab_asciiid_ux_present": check_shape_lab_asciiid_ux_present,
    "shape_lab_visual_comparison_receipt_current": check_shape_lab_visual_comparison_receipt_current,
}


def main() -> int:
    global RUN_PROOFS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--gate", action="append", help="check only this gate (repeatable)")
    parser.add_argument("--fl", default="FL-4131", help="FL id label (informational only)")
    parser.add_argument("--run-proofs", action="store_true", help="run executable proof commands for gates that have one")
    args = parser.parse_args()
    RUN_PROOFS = args.run_proofs

    results: list[GateResult] = []
    targets = args.gate or list(GATE_CHECKS.keys())
    for name in targets:
        fn = GATE_CHECKS.get(name)
        if not fn:
            results.append(GateResult(name, STATUS_ERROR, "unknown gate name"))
            continue
        try:
            res = fn()
        except Exception as exc:
            res = GateResult(name, STATUS_ERROR, f"check raised: {exc!r}")
        if res.status not in VALID_STATUSES:
            original_status = res.status
            res.status = STATUS_ERROR
            res.evidence = f"invalid status returned: {original_status!r}; {res.evidence}"
        results.append(res)

    if args.json:
        payload = {
            "fl": args.fl,
            "gate_count_total": len(GATE_CHECKS),
            "gate_count_checked": len(results),
            "results": [
                {"gate": r.name, "status": r.status, "evidence": r.evidence}
                for r in results
            ],
            "summary": _summary_counts(results),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_table(args.fl, results)

    # Exit code policy: 0 always. Status is descriptive, not a build gate.
    return 0


def _summary_counts(results: list[GateResult]) -> dict[str, int]:
    counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for r in results:
        # Statuses outside VALID_STATUSES are not possible here because the
        # main() loop rewrites them to STATUS_ERROR before append; the direct
        # dict assignment is safe.
        counts[r.status] += 1
    return counts


def _print_table(fl: str, results: list[GateResult]) -> None:
    name_w = max((len(r.name) for r in results), default=40)
    name_w = min(max(name_w, 32), 60)
    print(f"{fl} gate status ({len(results)} of {len(GATE_CHECKS)}):")
    print(f"  {'gate':<{name_w}}  status                evidence")
    print(f"  {'-' * name_w}  --------------------  ----------------------------------------")
    for r in results:
        ev = r.evidence
        if len(ev) > 80:
            ev = ev[:77] + "..."
        print(f"  {r.name:<{name_w}}  {r.status:<20}  {ev}")
    print()
    summary = _summary_counts(results)
    print("Summary:")
    for status in (STATUS_PROOF_PASSED, STATUS_MANUAL_INSPECTION_REQUIRED, STATUS_ARTIFACT_EXISTS, STATUS_IMPLEMENTED_UNPROVEN, STATUS_OPEN, STATUS_ERROR):
        if summary.get(status, 0):
            print(f"  {status:<22} {summary[status]}")


if __name__ == "__main__":
    sys.exit(main())
