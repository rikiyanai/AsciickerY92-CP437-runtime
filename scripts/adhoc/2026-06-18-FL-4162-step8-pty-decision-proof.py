#!/usr/bin/env python3
"""FL-4162 Step 8 pty proof for decision capture.

Drives the real xp_uv_body_viewer.py in anchor-review mode through a pty using
temp fixture directories. It proves both Step 8 residuals:

1. A present malformed source_layer_review_decisions.jsonl is surfaced in the
   evidence sidebar and [t] is blocked.
2. A valid empty decisions file accepts a typed [t] verdict, writes one
   proposal-only row, and reloads it beside the evidence card.

No live review data is seeded beside docs/research/ascii/semantic_maps.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import fcntl
import termios
from pathlib import Path


REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
PIPELINE = REPO / "pipeline-v3"
VIEWER = PIPELINE / "scripts" / "xp_uv_body_viewer.py"
SOURCE_ANCHOR = REPO / "docs/research/ascii/semantic_maps/bigbee-0100.json"
SOURCE_CARDS = REPO / "docs/research/ascii/semantic_maps/layer_evidence_cards.jsonl"
SPRITES = REPO / "assets/sprites"
OUTDIR = REPO / "docs/research/ascii/verification/fl4162/2026-06-18-step8-decision-pty"
DECISIONS = "source_layer_review_decisions.jsonl"
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(raw: bytes) -> str:
    return ANSI.sub(b"", raw).decode("utf-8", "replace")


def _prepare_fixture(name: str, *, corrupt_decisions: bool) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"fl4162-step8-{name}-"))
    anchor = json.loads(SOURCE_ANCHOR.read_text(encoding="utf-8"))
    anchor["reference_xp"] = str((SPRITES / "bigbee-0100.xp").resolve())
    (root / "bigbee-0100.json").write_text(json.dumps(anchor, indent=2), encoding="utf-8")
    shutil.copy2(SOURCE_CARDS, root / "layer_evidence_cards.jsonl")
    if corrupt_decisions:
        (root / DECISIONS).write_text("{ this is not valid json\n", encoding="utf-8")
    return root


def _run_viewer(fixture: Path, keys: list[object], name: str) -> dict:
    anchor = fixture / "bigbee-0100.json"
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 156, 0, 0))
    proc = subprocess.Popen(
        [sys.executable, str(VIEWER), "--anchor-review", str(anchor), "--sprite-dir", str(SPRITES)],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        cwd=str(PIPELINE),
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    buf = bytearray()

    def drain(total: float = 1.2, idle: float = 0.25) -> None:
        end = time.monotonic() + total
        while time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], idle)
            if master not in ready:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)

    def send(key: str) -> None:
        payloads = {
            "ENTER": "\r",
            "ESCAPE": "\x1b",
            "BACKSPACE": "\x7f",
        }
        try:
            os.write(master, payloads.get(key, key).encode("utf-8"))
        except OSError:
            return
        time.sleep(0.18)
        drain()

    drain(total=3.0)
    for action in keys:
        if isinstance(action, dict):
            key = str(action["key"])
            expect = str(action.get("expect", ""))
            retries = int(action.get("retries", 5))
            for _ in range(retries):
                send(key)
                if not expect or expect in _strip_ansi(bytes(buf)):
                    break
        else:
            send(str(action))
    send("q")
    send("q")
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    except Exception:
        stderr = ""
    try:
        os.close(master)
    except OSError:
        pass

    raw = bytes(buf)
    stripped = _strip_ansi(raw)
    frames = [_strip_ansi(part) for part in raw.split(b"\x1b[H\x1b[2J")]
    evidence_frames = [frame for frame in frames if "EVIDENCE" in frame]
    key_frame = evidence_frames[-1] if evidence_frames else (frames[-1] if frames else stripped)
    (OUTDIR / f"{name}_transcript.ansi").write_bytes(raw)
    (OUTDIR / f"{name}_frame.log").write_text(key_frame, encoding="utf-8")
    if stderr.strip():
        (OUTDIR / f"{name}_stderr.log").write_text(stderr, encoding="utf-8")
    return {
        "returncode": proc.returncode,
        "stderr_present": bool(stderr.strip()),
        "frame": key_frame,
        "transcript": stripped,
        "frame_count": len(frames),
        "evidence_frame_count": len(evidence_frames),
    }


def _type_text(text: str) -> list[str]:
    return list(text)


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)

    failure_fixture = _prepare_fixture("failure", corrupt_decisions=True)
    happy_fixture = _prepare_fixture("happy", corrupt_decisions=False)

    failure_run = _run_viewer(failure_fixture, ["i", "t"], "failure")
    failure_frame = failure_run["frame"]
    failure_checks = {
        "evidence_panel": "EVIDENCE" in failure_frame,
        "load_failed_banner": "DECISION FILE LOAD FAILED" in failure_frame,
        "malformed_visible": "malformed JSON" in failure_frame,
        "none_recorded_hidden": "none recorded" not in failure_frame,
        "t_blocked_status": "[t] blocked" in failure_run["transcript"],
        "no_prompt_opened": "Record decision: edit role" not in failure_run["transcript"],
    }

    happy_keys: list[object] = [
        {"key": "i", "expect": "DECISION = none recorded", "retries": 3},
        {"key": "t", "expect": "Decision role:", "retries": 5},
        {"key": "ENTER", "expect": "Decision note:", "retries": 3},
    ]
    happy_keys += _type_text("pty proof temp decision")
    happy_keys += [{"key": "ENTER", "expect": "Decision recorded:", "retries": 3}]
    happy_run = _run_viewer(happy_fixture, happy_keys, "happy")
    happy_frame = happy_run["frame"]
    decisions_path = happy_fixture / DECISIONS
    decisions_text = decisions_path.read_text(encoding="utf-8") if decisions_path.exists() else ""
    (OUTDIR / "happy_decisions_snapshot.jsonl").write_text(decisions_text, encoding="utf-8")
    try:
        decisions = [json.loads(line) for line in decisions_text.splitlines() if line.strip()]
    except ValueError:
        decisions = []
    approved_role = decisions[0].get("approved_role") if decisions else ""
    happy_checks = {
        "evidence_panel": "EVIDENCE" in happy_frame,
        "decision_recorded_status": f"Decision recorded: bigbee-0100-L2 -> {approved_role}" in happy_run["transcript"],
        "decision_reloaded_panel": f"DECISION = {approved_role}" in happy_frame,
        "reviewer_note_reloaded": "pty proof temp decision" in happy_frame,
        "one_decision_row": len(decisions) == 1,
        "proposal_only": bool(decisions) and decisions[0].get("authority") is False and decisions[0].get("is_proposal") is True,
        "source_key": bool(decisions) and decisions[0].get("source_key") == "bigbee-0100-L2",
        "fingerprint_present": bool(decisions) and bool(decisions[0].get("source_card_fingerprint")),
    }

    result = {
        "all_pass": all(failure_checks.values()) and all(happy_checks.values()),
        "failure_checks": failure_checks,
        "happy_checks": happy_checks,
        "failure_fixture": str(failure_fixture),
        "happy_fixture": str(happy_fixture),
        "outdir": str(OUTDIR),
        "failure_run": {k: v for k, v in failure_run.items() if k not in {"frame", "transcript"}},
        "happy_run": {k: v for k, v in happy_run.items() if k not in {"frame", "transcript"}},
    }
    (OUTDIR / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
