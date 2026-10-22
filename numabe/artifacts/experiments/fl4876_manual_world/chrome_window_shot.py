"""chrome_window_shot.py WINDOW_ID OUT.png -- viewing aid only: captures one Google Chrome window (by its
AppleScript window id) to OUT.png and a half-size OUT_s.png; prints the window bounds (screen points)
and the capture scale so clicks can be mapped back to screen coordinates."""
import subprocess, sys
from PIL import Image
wid, out = sys.argv[1], sys.argv[2]
b = subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to get bounds of (first window whose id is %s)' % wid],
                   capture_output=True, text=True, check=True).stdout.strip()
x0, y0, x1, y1 = (int(v) for v in b.split(", "))
subprocess.run(["screencapture", "-x", "-R%d,%d,%d,%d" % (x0, y0, x1 - x0, y1 - y0), out], check=True)
im = Image.open(out)
im.resize((im.size[0] // 2, im.size[1] // 2)).save(out[:-4] + "_s.png")
print("bounds", x0, y0, x1, y1, "image", im.size, "scale", im.size[0] / (x1 - x0))
