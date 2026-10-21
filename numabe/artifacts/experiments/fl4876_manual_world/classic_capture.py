"""classic_capture.py MAP.a3d OUT_PREFIX [LOAD_S] -- viewing aid only: opens ONE headed ASCIIID Classic
(.run/asciiid-classic --map MAP; normal headed mode, vsync on, never --mcp -- the --mcp loop runs vsync-off
and was implicated in the 2026-10-11 WindowServer watchdog panic), waits LOAD_S seconds (default 30),
captures every titled window of that process (screencapture -l, works behind other windows) to
OUT_PREFIX_<n>.png, writes its stdout to OUT_PREFIX.log, then terminates exactly that process.
Refuses to start if any asciiid / presenter process is already running (FL-3714 single instance)."""
import subprocess, sys, time
from pathlib import Path
import Quartz
ROOT = Path(__file__).resolve().parents[3]
mp, pre = sys.argv[1], sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
if subprocess.run(["pgrep", "-f", "asciicker-presenter|asciiid"], capture_output=True, text=True).stdout.split():
    sys.exit("refusing: an asciiid/presenter process is already running")
import os
env = dict(os.environ)
if len(sys.argv) > 4:  # the build's mesh folder (engine ReadActiveMeshRoot reads this env var first)
    env["ASCIICKER_ACTIVE_MESH_ROOT"] = str(Path(sys.argv[4]).resolve())
log = open(pre + ".log", "w")
p = subprocess.Popen([str(ROOT / ".run/asciiid-classic"), "--map", mp], cwd=str(ROOT), env=env,
                     stdout=log, stderr=subprocess.STDOUT)
try:
    time.sleep(wait)
    zoom = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    if zoom:  # mouse-wheel steps over the editor viewport centre (wheel = zoom; never a click/drag)
        subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                        '(first process whose unix id is %d) to true' % p.pid], capture_output=True)
        time.sleep(0.8)
        cx, cy = 650.0, 390.0
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (cx, cy), Quartz.kCGMouseButtonLeft))
        time.sleep(0.3)
        for _ in range(abs(zoom)):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateScrollWheelEvent(
                None, Quartz.kCGScrollEventUnitLine, 1, 1 if zoom > 0 else -1))
            time.sleep(0.25)
        time.sleep(2.0)
    n = 0
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerPID") != p.pid or not w.get("kCGWindowName"):
            continue
        b = w.get("kCGWindowBounds") or {}
        if b.get("Width", 0) < 200:
            continue
        out = "%s_%d.png" % (pre, n)
        subprocess.run(["screencapture", "-x", "-o", "-l%d" % w["kCGWindowNumber"], out])
        print("captured", out, str(w["kCGWindowName"])[:70], dict(b))
        n += 1
finally:
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
    log.close()
print("log", pre + ".log")
