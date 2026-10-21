"""Focused tests for the P0 Harness Doctor (behavior, not source text)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DOCTOR_PATH = (
    Path(__file__).resolve().parents[1] / "maintainer" / "p0" / "doctor.py"
)
REPO_ROOT = Path(__file__).resolve().parents[2]

EXT_CHECK = "omp-extension-files-present"
HOST_REASON = (
    "python cannot observe OMP loaded extensions; "
    "same-session loaded-proof NOT IMPLEMENTED"
)


def load_doctor():
    spec = importlib.util.spec_from_file_location("p0_doctor", DOCTOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def by_name(report, name):
    for check in report["checks"]:
        if check["name"] == name:
            return check
    raise AssertionError("missing check %r" % name)


def _write(path, text="export const x = 1;\n"):
    Path(path).write_text(text, encoding="utf-8")


def test_all_ok_on_repo_layout():
    # Updated: extension check renamed (reason: false-green review found the
    # old name implied OMP trust) and fingerprint keys are repo-relative
    # under the new repo_root binding.
    doctor = load_doctor()
    ext = REPO_ROOT / ".omp" / "extensions" / "doc-placement-guard.ts"
    report = doctor.run_doctor(paths=[str(ext)], repo_root=REPO_ROOT)
    assert report["ok"] is True
    assert all(c["ok"] for c in report["checks"])
    assert report["contract_version"] == 1
    rel = ext.relative_to(REPO_ROOT).as_posix()
    assert rel in report["fingerprint"]["scoped_hashes"]
    ext_check = by_name(report, EXT_CHECK)
    assert ext_check["ok"] is True
    assert "doc-placement-guard.ts@" in ext_check["detail"]
    assert "adhoc-command-materializer.ts@" in ext_check["detail"]


def test_fail_when_extensions_dir_emptied(tmp_path, monkeypatch):
    # Updated for the renamed check (reason: same rename as above).
    doctor = load_doctor()
    monkeypatch.setenv("P0_DOCTOR_EXT_DIR", str(tmp_path))
    report = doctor.run_doctor(repo_root=REPO_ROOT)
    assert report["ok"] is False
    assert by_name(report, EXT_CHECK)["ok"] is False


def test_fail_when_hooks_path_wrong(monkeypatch):
    doctor = load_doctor()
    monkeypatch.setenv("P0_DOCTOR_HOOKS_PATH", "/tmp/definitely-not-githooks")
    report = doctor.run_doctor(repo_root=REPO_ROOT)
    assert report["ok"] is False
    assert by_name(report, "hooks-path")["ok"] is False


def test_cli_receipt_written_and_reparseable(tmp_path):
    receipt = tmp_path / "receipt.json"
    proc = subprocess.run(
        [sys.executable, str(DOCTOR_PATH), "--receipt", str(receipt)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    stdout_doc = json.loads(proc.stdout)
    assert stdout_doc["ok"] is True
    assert stdout_doc["host_attestation"]["attested"] is False
    disk_doc = json.loads(receipt.read_text(encoding="utf-8"))
    assert disk_doc == stdout_doc


def test_cli_exit_code_2_on_failure(tmp_path, monkeypatch):
    # Updated for the renamed check (reason: same rename as above).
    env = dict(os.environ, P0_DOCTOR_EXT_DIR=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, str(DOCTOR_PATH)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
        env=env,
    )
    assert proc.returncode == 2, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is False
    assert by_name(doc, EXT_CHECK)["ok"] is False


def test_repeat_fingerprint_stable():
    doctor = load_doctor()
    ext = REPO_ROOT / ".omp" / "extensions" / "doc-placement-guard.ts"
    first = doctor.run_doctor(paths=[str(ext)], repo_root=REPO_ROOT)
    second = doctor.run_doctor(paths=[str(ext)], repo_root=REPO_ROOT)
    assert (
        first["fingerprint"]["fingerprint"] == second["fingerprint"]["fingerprint"]
    )


def test_extension_check_renamed_and_old_name_gone():
    doctor = load_doctor()
    report = doctor.run_doctor(repo_root=REPO_ROOT)
    names = [c["name"] for c in report["checks"]]
    assert EXT_CHECK in names
    assert "omp-extensions" not in names


def test_extension_docstring_disclaims_omp_loading():
    doctor = load_doctor()
    doc = doctor.check_omp_extension_files_present.__doc__
    assert "FILESYSTEM PREFLIGHT ONLY" in doc
    assert (
        "proves nothing about OMP discovery/trust/loading/handler registration"
        in doc
    )


def test_single_stray_ts_fails_extension_check(tmp_path):
    doctor = load_doctor()
    _write(tmp_path / "whatever.ts")
    report = doctor.run_doctor(repo_root=REPO_ROOT, ext_dir=str(tmp_path))
    check = by_name(report, EXT_CHECK)
    assert check["ok"] is False
    assert report["ok"] is False


def test_extra_ts_fails_closed_and_lists_unexpected(tmp_path):
    doctor = load_doctor()
    for name in (
        "doc-placement-guard.ts",
        "adhoc-command-materializer.ts",
        "evil-extra.ts",
    ):
        _write(tmp_path / name)
    report = doctor.run_doctor(repo_root=REPO_ROOT, ext_dir=str(tmp_path))
    check = by_name(report, EXT_CHECK)
    assert check["ok"] is False
    assert "evil-extra.ts" in check["detail"]
    assert report["ok"] is False


def test_missing_expected_adapter_fails(tmp_path):
    doctor = load_doctor()
    _write(tmp_path / "doc-placement-guard.ts")
    report = doctor.run_doctor(repo_root=REPO_ROOT, ext_dir=str(tmp_path))
    check = by_name(report, EXT_CHECK)
    assert check["ok"] is False
    assert "adhoc-command-materializer.ts" in check["detail"]
    assert report["ok"] is False


def test_missing_scoped_file_fails_fingerprint():
    doctor = load_doctor()
    ghost = REPO_ROOT / "no-such-scoped-file-p0-doctor-test.ts"
    assert not ghost.exists()
    report = doctor.run_doctor(paths=[str(ghost)], repo_root=REPO_ROOT)
    check = by_name(report, "scoped-fingerprint")
    assert check["ok"] is False
    assert report["ok"] is False


def test_receipt_contains_unattested_host_attestation():
    doctor = load_doctor()
    ext = REPO_ROOT / ".omp" / "extensions" / "doc-placement-guard.ts"
    report = doctor.run_doctor(paths=[str(ext)], repo_root=REPO_ROOT)
    assert report["host_attestation"] == {
        "attested": False,
        "reason": HOST_REASON,
    }
