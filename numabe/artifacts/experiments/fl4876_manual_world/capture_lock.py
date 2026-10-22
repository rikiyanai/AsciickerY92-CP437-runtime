"""FL-4876 CAPTURE-LOCK-01 (Python side of map_editor_ui/capture_lock.mjs): a bounded number of heavy captures at a
time on this machine. Slot directories .run/capture_lock (slot 0) and .run/capture_lock_<k> exist while a capture runs
and hold 'pid'; a dead pid's slot is taken over. SLOTS = FL4876_CAPTURE_SLOTS (default 1 since the 2026-10-17 crash with
two captures plus unlocked probes on the larger map; see capture_lock.mjs)."""
import atexit
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / ".run/capture_lock"
SLOTS = int(os.environ.get("FL4876_CAPTURE_SLOTS", "1"))


def _slot(k: int) -> Path:
    return ROOT if k == 0 else ROOT.with_name(f"capture_lock_{k}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _try(d: Path, label: str) -> bool:
    try:
        d.mkdir()
    except FileExistsError:
        try:
            pid = int((d / "pid").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        if pid and not _alive(pid):
            shutil.rmtree(d, ignore_errors=True)
            return _try(d, label)
        return False
    (d / "pid").write_text(f"{os.getpid()} {label}")
    return True


def acquire(label: str) -> None:
    waited = 0
    held = None
    while held is None:
        for k in range(SLOTS):
            if _try(_slot(k), label):
                held = _slot(k)
                break
        if held is None:
            if waited % 30 == 0:
                print(f"all {SLOTS} capture slots busy; waiting ({waited}s)", flush=True)
            time.sleep(2)
            waited += 2

    def release():
        try:
            if int((held / "pid").read_text().split()[0]) == os.getpid():
                shutil.rmtree(held, ignore_errors=True)
        except (OSError, ValueError, IndexError):
            pass
    atexit.register(release)
