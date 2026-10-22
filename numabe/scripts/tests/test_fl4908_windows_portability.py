#!/usr/bin/env python3
"""Focused Windows portability tests for the FL-4908 PR301 integration."""
from __future__ import annotations

import errno
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
for import_root in (REPO_ROOT, SCRIPTS):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from maintainer.lib import failure_log  # noqa: E402
import setup_addon  # noqa: E402


class _FakeMsvcrt:
    LK_NBLCK = 1
    LK_UNLCK = 2
    LK_LOCK = 3

    def __init__(
        self,
        *,
        busy_attempts: int = 0,
        failure: OSError | None = None,
    ) -> None:
        self.busy_attempts = busy_attempts
        self.failure = failure
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, size: int) -> None:
        self.calls.append((fd, mode, size))
        if mode == self.LK_NBLCK and self.busy_attempts:
            self.busy_attempts -= 1
            raise OSError(errno.EACCES, "sentinel byte is busy")
        if self.failure is not None:
            raise self.failure


def test_windows_lock_uses_msvcrt_and_releases_same_byte(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _FakeMsvcrt(busy_attempts=1)
    monkeypatch.setattr(failure_log, "fcntl", None)
    monkeypatch.setattr(failure_log, "msvcrt", fake)
    lock_target = tmp_path / "docs" / "FAILURE_LOG.md"

    with failure_log.fl_write_lock(lock_target, timeout_seconds=0.3):
        assert lock_target.parent.exists()

    assert [mode for _, mode, _ in fake.calls] == [
        fake.LK_NBLCK,
        fake.LK_NBLCK,
        fake.LK_UNLCK,
    ]
    assert all(size == 1 for _, _, size in fake.calls)
    assert lock_target.with_suffix(".md.lock").read_bytes() == b"\0"


def test_windows_lock_rewinds_to_sentinel_byte_before_each_operation(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _FakeMsvcrt(busy_attempts=1)
    monkeypatch.setattr(failure_log, "fcntl", None)
    monkeypatch.setattr(failure_log, "msvcrt", fake)
    seeks: list[tuple[int, int]] = []
    real_lseek = failure_log.os.lseek

    def record_seek(fd: int, offset: int, whence: int) -> int:
        seeks.append((offset, whence))
        return real_lseek(fd, offset, whence)

    monkeypatch.setattr(failure_log.os, "lseek", record_seek)
    lock_target = tmp_path / "FAILURE_LOG.md"

    with failure_log.fl_write_lock(lock_target, timeout_seconds=0.3):
        pass

    assert [offset for offset, _ in seeks] == [0, 0, 0]
    assert all(whence == failure_log.os.SEEK_SET for _, whence in seeks)


def test_windows_lock_times_out_on_contention(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeMsvcrt(busy_attempts=100)
    monkeypatch.setattr(failure_log, "fcntl", None)
    monkeypatch.setattr(failure_log, "msvcrt", fake)

    with pytest.raises(TimeoutError, match="timed out"):
        with failure_log.fl_write_lock(
            tmp_path / "FAILURE_LOG.md", timeout_seconds=0.06
        ):
            pass

    assert all(mode == fake.LK_NBLCK for _, mode, _ in fake.calls)


def test_windows_lock_propagates_unexpected_lock_errors(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _FakeMsvcrt(failure=OSError(errno.EBADF, "bad descriptor"))
    monkeypatch.setattr(failure_log, "fcntl", None)
    monkeypatch.setattr(failure_log, "msvcrt", fake)

    with pytest.raises(OSError, match="bad descriptor"):
        with failure_log.fl_write_lock(
            tmp_path / "FAILURE_LOG.md", timeout_seconds=0.3
        ):
            pass

    assert len(fake.calls) == 1


def test_windows_lock_non_timed_mode_uses_crt_blocking_operation(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _FakeMsvcrt()
    monkeypatch.setattr(failure_log, "fcntl", None)
    monkeypatch.setattr(failure_log, "msvcrt", fake)

    with failure_log.fl_write_lock(tmp_path / "FAILURE_LOG.md", timeout_seconds=0):
        pass

    assert [mode for _, mode, _ in fake.calls] == [fake.LK_LOCK, fake.LK_UNLCK]


def test_noop_fallback_is_only_used_without_either_lock_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(failure_log, "fcntl", None)
    monkeypatch.setattr(failure_log, "msvcrt", None)
    entered: list[str] = []

    with failure_log.fl_write_lock(tmp_path / "FAILURE_LOG.md"):
        entered.append("critical section")

    assert entered == ["critical section"]


def test_file_link_fallback_retries_symbolic_link_after_cross_volume_hardlink(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "Blender Addons" / "blender_mcp_addon.py"
    source = tmp_path / "Repository" / "blender_mcp_addon.py"
    source.parent.mkdir()
    source.write_text("source", encoding="utf-8")
    calls: list[str] = []

    def fake_run(args: str, **_kwargs):
        calls.append(args)
        if "/H" in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="different volume")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup_addon.subprocess, "run", fake_run)

    assert setup_addon._windows_link_fallback(target, source)
    assert calls[0] == (
        'cmd /d /v:off /c mklink /H "%__ASCIICKER_MKLINK_TARGET%" '
        '"%__ASCIICKER_MKLINK_SOURCE%"'
    )
    assert calls[1] == (
        'cmd /d /v:off /c mklink "%__ASCIICKER_MKLINK_TARGET%" '
        '"%__ASCIICKER_MKLINK_SOURCE%"'
    )


def test_mklink_paths_are_only_passed_through_environment(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "addon & ! % (target).py"
    source = tmp_path / "source | ! % (source).py"
    source.write_text("source", encoding="utf-8")
    calls: list[tuple[str, dict]] = []

    def fake_run(args: str, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup_addon.subprocess, "run", fake_run)

    assert setup_addon._windows_link_fallback(target, source)
    args, kwargs = calls[0]
    assert str(target) not in args
    assert str(source) not in args
    assert isinstance(args, str)
    assert args.startswith("cmd /d /v:off /c mklink ")
    assert '\\"' not in args  # Never CRT-escape cmd's quoted variables.
    assert '"%__ASCIICKER_MKLINK_TARGET%"' in args
    assert '"%__ASCIICKER_MKLINK_SOURCE%"' in args
    assert kwargs["env"]["__ASCIICKER_MKLINK_TARGET"] == str(target)
    assert kwargs["env"]["__ASCIICKER_MKLINK_SOURCE"] == str(source)
    assert "atomic source replacement" in capsys.readouterr().out


def test_file_link_fallback_fails_loudly_without_copying(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "addon.py"
    source = tmp_path / "source.py"
    source.write_text("source", encoding="utf-8")
    attempts: list[str] = []

    def fake_run(args: str, **_kwargs):
        attempts.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="link denied")

    monkeypatch.setattr(setup_addon.subprocess, "run", fake_run)

    assert not setup_addon._windows_link_fallback(target, source)
    assert len(attempts) == 2
    assert not target.exists()


def test_directory_link_fallback_retries_directory_symlink(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "addon"
    source = tmp_path / "source"
    source.mkdir()
    attempts: list[str] = []

    def fake_run(args: str, **_kwargs):
        attempts.append(args)
        return SimpleNamespace(
            returncode=0 if "/D" in args else 1,
            stdout="",
            stderr="junction denied" if "/J" in args else "",
        )

    monkeypatch.setattr(setup_addon.subprocess, "run", fake_run)

    assert setup_addon._windows_link_fallback(target, source)
    assert ["/J" in args for args in attempts] == [True, False]
    assert ["/D" in args for args in attempts] == [False, True]
