"""roof_review.py BLOCK [accept|all] [move TAIL=x,y ...] [reject TAIL=reason ...] -- records the
person's per-building roof decisions for one aerial block and measures the roof colour (mean of a
5x5 px patch on the GSI aerial mosaic) at each accepted or moved point. Buildings whose suggested
dot lies in the block and that are neither moved nor rejected are 'accepted as looked at'.
Appends to aerial/roofs_reviewed.json (feature id -> decision, point, rgb, block)."""
import json, sys
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
A = HERE / "aerial"
blk = sys.argv[1]
r, c = (int(v) for v in blk.split("_"))
S = 128
fp = json.loads((A / "footprints_px.json").read_text())
mos = Image.open(A / "mosaic.jpg").convert("RGB"); px = mos.load()
moves, rejects, mode = {}, {}, None
for a in sys.argv[2:]:
    if a in ("move", "reject", "accept"):
        mode = a; continue
    if mode == "move":
        t, xy = a.split("="); moves[t] = tuple(float(v) for v in xy.split(","))
    elif mode == "reject":
        t, why = a.split("=", 1); rejects[t] = why
out_p = A / "roofs_reviewed.json"
rev = json.loads(out_p.read_text()) if out_p.is_file() else {}


def tail(fid):
    lab = fid.split("/")[-1]
    return lab[-4:] if not lab.startswith("bldg_") else "P" + lab[5:8]


n = {"accepted": 0, "moved": 0, "rejected": 0}
for fid, v in fp.items():
    x, y = v["dot"]
    if not (c * S <= x < (c + 1) * S and r * S <= y < (r + 1) * S):
        continue
    t = tail(fid)
    if t in rejects:
        rev[fid] = {"decision": "rejected", "reason": rejects[t], "block": blk}
        n["rejected"] += 1; continue
    dec = "moved" if t in moves else "accepted"
    x, y = moves.get(t, (x, y))
    acc = [0, 0, 0]; k = 0
    for yy in range(int(y) - 2, int(y) + 3):
        for xx in range(int(x) - 2, int(x) + 3):
            if 0 <= xx < mos.width and 0 <= yy < mos.height:
                acc = [s + q for s, q in zip(acc, px[xx, yy])]; k += 1
    rgb = [round(s / k) for s in acc]
    rev[fid] = {"decision": dec, "point_px": [round(x, 1), round(y, 1)], "roof_rgb": rgb, "block": blk}
    n[dec] += 1
out_p.write_text(json.dumps(rev, indent=0))
print(blk, n, "total reviewed", len(rev))
