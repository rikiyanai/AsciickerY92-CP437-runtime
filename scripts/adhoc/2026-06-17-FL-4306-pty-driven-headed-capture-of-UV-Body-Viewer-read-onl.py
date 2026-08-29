# Ad hoc script: FL-4306 pty-driven headed capture of UV Body Viewer read-only evidence sidebar; sends i + nav to idx0 + q, saves transcript/frame/result, verifies NO. BEE ONLY card
# Created: 2026-06-17
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4306 read-only headed proof: drive xp_uv_body_viewer through a pty,
toggle the evidence sidebar [i], navigate to the rejects-first card #1
(bigbee-0000-L2), quit, and verify the captured frame shows the expected card.
No gameplay/state mutation — observation only.
"""
import os, pty, sys, time, select, json, re, struct, fcntl, termios, subprocess
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
VIEWER = REPO / "pipeline-v3" / "scripts" / "xp_uv_body_viewer.py"
ANCHOR = REPO / "docs/research/ascii/semantic_maps/bigbee-0100.json"
SPRITES = REPO / "assets/sprites"
OUTDIR = REPO / "docs/research/ascii/verification/fl4306/2026-06-17-evidence-sidebar-pty"
OUTDIR.mkdir(parents=True, exist_ok=True)

# Compute how many '[' presses reach idx 0 (bigbee-0000-L2) from the initial
# position (bigbee-0100-L2), using the viewer's own loader for parity.
sys.path.insert(0, str(REPO / "pipeline-v3" / "scripts"))
import xp_uv_body_viewer as v
cards = v._load_evidence_cards_for_family(ANCHOR, "bigbee-0100")
init_idx = next((i for i, c in enumerate(cards) if c["card_id"] == "bigbee-0100-L2"), 0)
print(f"loaded {len(cards)} bigbee cards; initial evidence_idx={init_idx} (bigbee-0100-L2); "
      f"idx0={cards[0]['card_id'] if cards else '?'}")

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 46, 150, 0, 0))
proc = subprocess.Popen(
    [sys.executable, str(VIEWER), "--anchor-review", str(ANCHOR), "--sprite-dir", str(SPRITES)],
    stdin=slave, stdout=slave, stderr=subprocess.PIPE,
    cwd=str(REPO / "pipeline-v3"), close_fds=True, start_new_session=True,
)
os.close(slave)
buf = bytearray()

def drain(total=1.5, idle=0.5):
    end = time.monotonic() + total
    while time.monotonic() < end:
        r, _, _ = select.select([master], [], [], idle)
        if master in r:
            try:
                d = os.read(master, 65536)
            except OSError:
                break
            if not d:
                break
            buf.extend(d)
        else:
            break

def send(ch):
    try:
        os.write(master, ch.encode())
    except OSError:
        pass  # viewer already exited after quit; pty no longer writable
    time.sleep(0.18)

def key(ch, total=0.9):
    # drain after EVERY keypress: _read_anchor_key reads up to 16 bytes and uses
    # only data[0], and the viewer blocks on stdout.write when the pty buffer
    # fills — so keys must be sent one at a time with the frame drained between.
    send(ch)
    drain(total=total)

drain(total=3.0)                  # initial render
key("i", total=1.3)               # evidence ON (shows bigbee-0100-L2)
for _ in range(init_idx):         # navigate back to idx0 = bigbee-0000-L2
    key("[")
mark = len(buf)                   # frame(s) at idx0 begin around here
key("]")                          # prove nav forward works
key("q", total=0.6); key("q", total=0.8)
try:
    proc.wait(timeout=3)
except Exception:
    proc.terminate()
try:
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
except Exception:
    err = ""
os.close(master)

raw = bytes(buf)
(OUTDIR / "transcript.ansi").write_bytes(raw)
if err.strip():
    (OUTDIR / "viewer_stderr.log").write_text(err)

ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
def strip(b): return ANSI.sub(b"", b).decode("utf-8", "replace")
frames = [strip(p) for p in raw.split(b"\x1b[H\x1b[2J")]
# the target card frame = the last EVIDENCE frame that names bigbee-0000-L2
target = [f for f in frames if "EVIDENCE" in f and "bigbee-0000-L2" in f]
key_frame = target[-1] if target else next((f for f in reversed(frames) if "EVIDENCE" in f), frames[-1] if frames else "")
(OUTDIR / "evidence_frame_idx0.log").write_text(key_frame)

checks = {
    "EVIDENCE panel": "EVIDENCE" in key_frame,
    "card bigbee-0000-L2": "bigbee-0000-L2" in key_frame,
    "NO. BEE ONLY": "NO. BEE ONLY" in key_frame,
    "armor;mount_body_wolf": "armor;mount_body_wolf" in key_frame,
    "angles=8": "angles=8" in key_frame,
    "anims=[1, 2]": "anims=[1, 2]" in key_frame,
}
result = {
    "all_pass": all(checks.values()),
    "checks": checks,
    "total_frames": len(frames),
    "evidence_frames": sum(1 for f in frames if "EVIDENCE" in f),
    "returncode": proc.returncode,
    "stderr_present": bool(err.strip()),
    "outdir": str(OUTDIR),
}
(OUTDIR / "result.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
print("=== KEY FRAME (stripped, tail) ===")
print("\n".join(l for l in key_frame.splitlines() if l.strip())[-1400:])
