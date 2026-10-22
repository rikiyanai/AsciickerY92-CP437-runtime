"""sv_go.py URL OUT_PREFIX [WAIT_S] -- viewing aid only (decides nothing): loads URL into the ACTIVE tab of
the operator's front Google Chrome window (no new tab), waits WAIT_S seconds (default 8, minimum 6 so
loads stay at human pace), reads the tab's resolved URL (pano position + view), captures the window to
OUT_PREFIX.png, and draws the building brackets (sv_overlay.py) to OUT_PREFIX_ov.png plus a half-size
preview OUT_PREFIX_ovs.png. The Street View canvas rectangle is derived from the window bounds
(sidebar 240 pt, toolbar 58 pt in the operator's layout; check by eye and pass X0,Y0,X1,Y1 to
sv_overlay.py directly if the layout differs)."""
import subprocess, sys, time
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
url, pre = sys.argv[1], sys.argv[2]
wait = max(6.0, float(sys.argv[3]) if len(sys.argv) > 3 else 8.0)
osa = lambda s: subprocess.run(["osascript", "-e", s], capture_output=True, text=True).stdout.strip()
osa('tell application "Google Chrome" to set URL of active tab of front window to "%s"' % url.replace('"', '%22'))
time.sleep(wait)
b = [int(v) for v in osa('tell application "Google Chrome" to get bounds of front window').split(", ")]
cur = osa('tell application "Google Chrome" to get URL of active tab of front window')
x, y, w, h = b[0], b[1], b[2] - b[0], b[3] - b[1]
subprocess.run(["screencapture", "-x", "-R%d,%d,%d,%d" % (x, y, w, h), pre + ".png"], check=True)
im = Image.open(pre + ".png")
s = im.size[0] / w  # retina scale
X0, Y0, X1, Y1 = int(240 * s), int(80 * s), im.size[0], im.size[1]
print("resolved", cur[:160])
print("canvas", X0, Y0, X1, Y1)
r = subprocess.run([sys.executable, str(HERE / "sv_overlay.py"), pre + ".png", cur, str(X0), str(Y0), str(X1), str(Y1),
                    pre + "_ov.png"], capture_output=True, text=True)
print(r.stdout.strip() or r.stderr.strip()[-400:])
if Path(pre + "_ov.png").is_file():
    o = Image.open(pre + "_ov.png"); o.resize((o.size[0] // 3, o.size[1] // 3)).save(pre + "_ovs.png")
