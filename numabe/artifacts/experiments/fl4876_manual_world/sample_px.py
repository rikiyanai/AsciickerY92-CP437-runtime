"""sample_px.py IMAGE x,y[,r] ... -- measuring tool only: mean RGB of a (2r+1)^2 patch (default r=6) at
each point the person chose, in the image's own native pixels (for Street View screen captures)."""
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGB")
W, H = im.size
px = im.load()
for arg in sys.argv[2:]:
    v = [int(t) for t in arg.split(",")]
    x, y, r = v[0], v[1], (v[2] if len(v) > 2 else 6)
    acc = [0, 0, 0]; n = 0
    for yy in range(max(0, y - r), min(H - 1, y + r) + 1):
        for xx in range(max(0, x - r), min(W - 1, x + r) + 1):
            c = px[xx, yy]; acc = [a + b for a, b in zip(acc, c)]; n += 1
    print("%5d,%-5d rgb %3d %3d %3d" % (x, y, *(round(a / n) for a in acc)))
