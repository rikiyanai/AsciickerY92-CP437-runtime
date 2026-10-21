"""headed_capture.py MAP.a3d OUT.png [LOAD_S] -- viewing aid only: opens ONE headed presenter on MAP
(scripts/presenter_app.py, vsync on; refuses if any presenter/asciiid process is already running, FL-3714),
waits LOAD_S seconds (default 25), captures the GAME TERM window by its window id (screencapture -l, works
behind other windows), then terminates exactly the process it started. Writes OUT.png and prints the
window title and process id."""
import subprocess, sys, time
from pathlib import Path
import Quartz
ROOT = Path(__file__).resolve().parents[3]
mp, out = sys.argv[1], sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0
busy = subprocess.run(["pgrep", "-f", "asciicker-presenter|asciiid"], capture_output=True, text=True).stdout.split()
if busy:
    sys.exit("refusing: presenter/asciiid already running: %s" % busy)
subprocess.run([sys.executable, str(ROOT / "scripts/presenter_app.py"), "--no-wait", "--", mp], check=True,
               capture_output=True, text=True)
pid = None
for _ in range(20):
    ps = subprocess.run(["pgrep", "-f", "asciicker-presenter " + mp], capture_output=True, text=True).stdout.split()
    if ps:
        pid = int(ps[0]); break
    time.sleep(0.5)
if pid is None:
    sys.exit("presenter did not start")
time.sleep(wait)
win = None
for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID):
    if w.get("kCGWindowOwnerPID") == pid and str(w.get("kCGWindowName") or "").startswith("GAME TERM"):
        win = w
try:
    if win is None:
        sys.exit("no GAME TERM window for pid %d" % pid)
    subprocess.run(["screencapture", "-x", "-o", "-l%d" % win["kCGWindowNumber"], out], check=True)
    print("captured", out, "pid", pid, win.get("kCGWindowName"))
finally:
    subprocess.run(["kill", str(pid)])
    for _ in range(20):
        if subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0:
            break
        time.sleep(0.5)
