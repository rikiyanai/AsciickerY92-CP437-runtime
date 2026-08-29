# Ad hoc script: FL-4162 per-family pty proof: drive read-only Source Layer Contract Viewer across player/plydie/wolfie/wolack/attack; prove rendered layer + role + topology class + blocker + exact/near visible, and zero writes to maps/decisions/profiles/anchors via before/after sha snapshot
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 per-family pty proof for source_layer_contract_viewer.py (read-only).

Drives the real viewer through a pty for one representative stem per actor family
and proves, per stem: a raw XP layer is rendered (truecolor cells), the proposed
role / topology class / blocker state / glyph exact/near evidence are visible.
Also proves the viewer writes NOTHING: it sha256-snapshots the four FL-4162
artifacts (+ each stem's .xp) before and after the whole run and asserts identity.
"""
from __future__ import annotations
import hashlib, json, os, pty, re, select, struct, subprocess, sys, time, fcntl, termios
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
PIPELINE = REPO / "pipeline-v3"
VIEWER = PIPELINE / "scripts" / "source_layer_contract_viewer.py"
SM = REPO / "docs/research/ascii/semantic_maps"
SPRITES = REPO / "assets/sprites"
OUTDIR = REPO / "docs/research/ascii/verification/fl4162/2026-06-22-source-layer-contract-viewer-family-pty"
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
STEMS = ["player-1100", "plydie-1102", "wolfie-1012", "wolack-0101", "attack-0101"]
ARTIFACTS = ["layer_evidence_cards.jsonl", "source_layer_review_decisions.jsonl",
             "manual_candidate_review.json", "family_topology_contracts.json",
             "actor_visual_profile_entries.json"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _snapshot() -> dict:
    snap = {f"sm/{a}": _sha(SM / a) for a in ARTIFACTS}
    for stem in STEMS:
        snap[f"xp/{stem}"] = _sha(SPRITES / f"{stem}.xp")
    snap["__semantic_maps_filecount"] = str(len(list(SM.glob("*"))))
    return snap


def _strip(raw: bytes) -> str:
    return ANSI.sub(b"", raw).decode("utf-8", "replace")


def run(stem: str) -> bytes:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 52, 170, 0, 0))
    proc = subprocess.Popen(
        [sys.executable, str(VIEWER), stem, "--sprites", str(SPRITES), "--sm", str(SM)],
        stdin=slave, stdout=slave, stderr=subprocess.PIPE, cwd=str(PIPELINE),
        close_fds=True, start_new_session=True)
    os.close(slave)
    buf = bytearray()

    def drain(total=1.5, idle=0.3):
        end = time.monotonic() + total
        while time.monotonic() < end:
            r, _, _ = select.select([master], [], [], idle)
            if master not in r:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)

    drain(total=3.0)
    # advance one frame, then cycle through every layer (>= 5 presses covers all stems)
    for k in ["n", "]", "]", "]", "]", "]", "q"]:
        try:
            os.write(master, k.encode())
        except OSError:
            break
        time.sleep(0.18)
        drain()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        os.close(master)
    except OSError:
        pass
    return bytes(buf)


def check(stem: str, raw: bytes) -> dict:
    text = _strip(raw)
    has_truecolor = b"\x1b[38;2;" in raw
    role_lines = [ln for ln in text.splitlines() if ln.startswith("PROPOSED ROLE:")]
    role_nonempty = any("<none>" not in ln for ln in role_lines)
    blocker_lines = [ln for ln in text.splitlines() if ln.startswith("blockers:")]
    blocker_nonnone = any(ln.strip() != "blockers: none" for ln in blocker_lines)
    peers_lines = [ln for ln in text.splitlines() if ln.startswith("glyph exact-match peers:")]
    return {
        "viewer_started": "SOURCE LAYER CONTRACT VIEWER" in text,
        "read_only_banner": "READ-ONLY" in text,
        "raw_layer_rendered": ("raw layer L" in text) and has_truecolor,
        "proposed_role_visible": bool(role_lines) and role_nonempty,
        "topology_class_visible": "topology_class:" in text,
        "blocker_state_visible": bool(blocker_lines) and blocker_nonnone,
        "exact_near_evidence_visible": bool(peers_lines) and "near-match peers:" in text,
        "role_grid_present": "ROLE GRID" in text,
    }


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    before = _snapshot()
    per_stem = {}
    for stem in STEMS:
        raw = run(stem)
        (OUTDIR / f"{stem}_transcript.ansi").write_bytes(raw)
        frames = [_strip(p) for p in raw.split(b"\x1b[H\x1b[2J")]
        ev = [f for f in frames if "CONTRACT" in f]
        (OUTDIR / f"{stem}_key_frame.log").write_text(ev[-1] if ev else (frames[-1] if frames else ""), encoding="utf-8")
        per_stem[stem] = check(stem, raw)
    after = _snapshot()
    no_writes = before == after
    all_checks_pass = all(all(c.values()) for c in per_stem.values())
    result = {
        "all_pass": all_checks_pass and no_writes,
        "stems": STEMS,
        "per_stem_checks": per_stem,
        "no_writes_proof": {"identical": no_writes,
                            "changed_keys": [k for k in before if before[k] != after.get(k)]},
        "outdir": str(OUTDIR),
    }
    (OUTDIR / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
