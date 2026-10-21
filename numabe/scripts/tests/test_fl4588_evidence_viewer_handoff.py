# =============================================================
# test_fl4588_evidence_viewer_handoff.py
# =============================================================
# Focused fixtures for the FL-4588 ephemeral evidence-viewer credential handoff
# in the packaged launcher (scripts/package_asciicker_presenter_html_mac.py).
#
# These are LIGHTWEIGHT unit fixtures over the handoff helpers only — they never
# build, package, launch, or open Chrome (honoring the FL-4431 quiet hold). They
# prove the acceptance contract directly:
#   - the handoff is written mode-0600;
#   - it carries the read-only surface kind + minimum provenance;
#   - it stores only the DERIVED token hash, never the plaintext token;
#   - a stale handoff (even with loose permissions) is replaced, not merged;
#   - it is deleted on success and delete is resilient when already absent;
#   - the write fails closed when the runtime directory cannot be written;
#   - the launch lifecycle deletes it at start (stale recovery) and in finally
#     (success + exception + interrupt), and never puts the token in a receipt.
# =============================================================
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/package_asciicker_presenter_html_mac.py"

# A fixed, obviously-not-real token used across the fixtures. Mixed case + spaces
# so we can prove the helper trims + lowercases before hashing (matching the
# shell's transport.ts handshake), and that the raw token never lands on disk.
FAKE_TOKEN = "  AbCdEf-Evidence-TOKEN-1234  "
EXPECTED_SHA256 = hashlib.sha256(
    FAKE_TOKEN.strip().lower().encode("utf-8")
).hexdigest()


def _packager():
    """Import the packaging front door as a module (same pattern as the existing
    packaging test)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import package_asciicker_presenter_html_mac as packager  # noqa: WPS433

    return packager


@pytest.fixture()
def pkg(tmp_path, monkeypatch):
    """The packager module with PACKAGE_ROOT redirected to a throwaway runtime
    directory, so the fixtures never touch the real .run tree."""
    packager = _packager()
    runtime_dir = tmp_path / "asciiid_html_desktop"
    runtime_dir.mkdir()
    monkeypatch.setattr(packager, "PACKAGE_ROOT", runtime_dir)
    return packager


def _write(pkg, token=FAKE_TOKEN):
    return pkg.write_evidence_viewer_handoff(
        session_token=token,
        backend_pid=4242,
        git_head="deadbeefcafef00d",
        ui_registry_sha256="a" * 64,
        native_origin="http://127.0.0.1:43124",
        shell_origin="http://127.0.0.1:43125",
    )


def test_handoff_is_0600_with_provenance_and_no_plaintext_token(pkg):
    path = _write(pkg)
    # Exactly 0600 — no group/other bits.
    assert (path.stat().st_mode & 0o777) == 0o600
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    # Read-only surface kind + schema.
    assert payload["schema"] == "asciiid.web_evidence_viewer.v1"
    assert payload["read_only_surface_kind"] == "asciiid.web_evidence_viewer.v1"
    assert payload["access"] == "read_only"
    # Minimum provenance the read-only viewer needs to bind its own handshake.
    assert payload["native_origin"] == "http://127.0.0.1:43124"
    assert payload["shell_origin"] == "http://127.0.0.1:43125"
    assert payload["backend_pid"] == 4242
    assert payload["git_head"] == "deadbeefcafef00d"
    assert payload["ui_registry_sha256"] == "a" * 64
    # Credential is the DERIVED hash only — never the plaintext token.
    assert payload["session_token_sha256"] == EXPECTED_SHA256
    assert "session_token" not in payload  # no plaintext token key
    assert FAKE_TOKEN not in text
    assert FAKE_TOKEN.strip() not in text
    assert EXPECTED_SHA256 in text


def test_stale_handoff_with_loose_perms_is_replaced_not_merged(pkg):
    path = pkg._evidence_viewer_handoff_path()
    # Plant a stale, world-readable handoff with different content.
    path.write_text(json.dumps({"schema": "STALE", "leftover": True}) + "\n")
    os.chmod(path, 0o644)
    _write(pkg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    # The stale keys are gone; the fresh credential replaced the file wholesale.
    assert payload["schema"] == "asciiid.web_evidence_viewer.v1"
    assert "leftover" not in payload
    # Replacement restored strict 0600 even though the stale file was 0644.
    assert (path.stat().st_mode & 0o777) == 0o600


def test_delete_removes_handoff_and_is_resilient_when_absent(pkg):
    path = _write(pkg)
    assert path.exists()
    assert pkg.delete_evidence_viewer_handoff() is True
    assert not path.exists()
    # Deleting an already-absent handoff must not crash and returns False.
    assert pkg.delete_evidence_viewer_handoff() is False


def test_write_fails_closed_when_runtime_directory_is_unwritable(pkg):
    runtime_dir = pkg._evidence_viewer_handoff_path().parent
    os.chmod(runtime_dir, 0o500)  # read+execute, no write
    try:
        with pytest.raises(pkg.PackagingError):
            _write(pkg)
        # Fail closed: no handoff (and no leftover temp) is left behind.
        assert not pkg._evidence_viewer_handoff_path().exists()
        assert not list(runtime_dir.glob("*.tmp"))
    finally:
        # Restore so pytest can clean up the tmp tree.
        os.chmod(runtime_dir, 0o700)


def test_launch_lifecycle_deletes_handoff_first_and_in_finally(pkg):
    # Structural: the launch owns delete-first (stale recovery) at start and a
    # delete in the finally that covers success, exception, and interrupt exits.
    source = SOURCE.read_text(encoding="utf-8")
    launch = source[source.index("def launch_package("):]
    launch = launch[: launch.index("\ndef ", 1)]
    # Delete-first appears before the handoff is written.
    first_delete = launch.index("delete_evidence_viewer_handoff()")
    write_at = launch.index("write_evidence_viewer_handoff(")
    finally_at = launch.index("finally:")
    assert first_delete < write_at, "delete-first must precede the handoff write"
    # A delete lives inside the finally block (every lifecycle exit).
    finally_delete = launch.index("delete_evidence_viewer_handoff()", finally_at)
    assert finally_delete > finally_at


def test_token_is_never_placed_in_any_receipt_or_log(pkg):
    # The authoring credential is delivered through the same-origin in-memory
    # bootstrap, never through the URL, receipt, or diagnostic log. The
    # evidence-viewer handoff stores only its derived hash.
    source = SOURCE.read_text(encoding="utf-8")
    # The handoff writer takes the token only as a hashed credential.
    assert 'session_token=token' in source
    assert 'session_token_sha256' in source
    # No receipt/log ever formats the raw token value.
    assert '"session_token": token' not in source
    assert "AUTHORING_BOOTSTRAP_PATH" in source
    assert '"session_token": self._session_token' in source
    assert "token={token}" not in source
    # The receipts advertise observation, never the credential itself.
    assert '"token_observed": True' in source


def test_token_line_is_redacted_in_the_receipt_diagnostic_tail(pkg):
    # The native runtime prints the token to stdout; the bounded tail is written
    # into the runtime receipt. Prove the credential is redacted from that tail
    # while the token is still extracted for the handshake.
    secret = "SECRET-TOK-99"
    stream = io.StringIO(
        f"boot line 1\n{pkg.TOKEN_LINE_PREFIX} {secret}\nboot line 2\n"
    )
    drain = pkg.NativeStdoutTokenDrain(stream)
    drain.start()
    assert drain.wait_for_token(timeout_s=5.0) == secret  # handshake unaffected
    drain.join(timeout=2.0)
    tail = drain.diagnostic_tail()
    assert pkg.TOKEN_LINE_PREFIX in tail  # the line is still visible as evidence
    assert "[redacted]" in tail
    assert secret not in tail


def test_redact_token_line_helper_is_exact(pkg):
    out = pkg._redact_token_line(f"{pkg.TOKEN_LINE_PREFIX} abc123\n")
    assert "abc123" not in out
    assert out.endswith("[redacted]\n")
    # Non-token lines pass through untouched.
    assert pkg._redact_token_line("plain line\n") == "plain line\n"
