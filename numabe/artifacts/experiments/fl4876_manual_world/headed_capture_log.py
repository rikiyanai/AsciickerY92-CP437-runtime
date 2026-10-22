"""headed_capture_log.py MAP.a3d OUT_PREFIX [LOAD_S] -- viewing aid only: like headed_capture.py but starts
.run/asciicker-presenter directly (headed, vsync on, no --mcp) with its stdout/stderr written to
OUT_PREFIX.log, captures both the editor window and the GAME TERM window after LOAD_S seconds, then
terminates exactly that process. Refuses if any presenter/asciiid process is already running (FL-3714)."""
import subprocess, sys, time
from pathlib import Path
import Quartz
ROOT = Path(__file__).resolve().parents[3]
mp, pre = sys.argv[1], sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0
if subprocess.run(["pgrep", "-f", "asciicker-presenter|asciiid"], capture_output=True, text=True).stdout.split():
    sys.exit("refusing: presenter/asciiid already running")
log = open(pre + ".log", "w")
p = subprocess.Popen([str(ROOT / ".run/asciicker-presenter"), mp], cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
try:
    time.sleep(wait)
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerPID") != p.pid or not w.get("kCGWindowName"):
            continue
        name = str(w["kCGWindowName"])
        tag = "term" if name.startswith("GAME TERM") else "editor" if "PRESENTER" in name else None
        if tag:
            subprocess.run(["screencapture", "-x", "-o", "-l%d" % w["kCGWindowNumber"], "%s_%s.png" % (pre, tag)])
            print("captured", tag, name[:60])
finally:
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
    log.close()
print("log", pre + ".log")
