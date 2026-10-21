"""review_plan.py E N R OUT.png -- drawing aid only (decides nothing): plan of radius R m around E,N with
zone carriers (grey buildings, white roads, orange rails, blue water; id tails) and every reviewed fixture
from assess/fixture_review_*.json (green = accepted with its id tail and module head, red x = rejected
at its projected point), plus any already-accepted owner fixtures not from the batch reviews (cyan)."""
import glob, json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
E0, N0, R, out = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
PX = 900
S = PX / (2 * R)
to = lambda q: (PX / 2 + (q[0] - E0) * S, PX / 2 - (q[1] - N0) * S)
inside = lambda q: abs(q[0] - E0) < R and abs(q[1] - N0) < R
zones = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/observation_registration/placement_zones.json").read_text())
owner = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json").read_text())
proj = {f["fixture_id"]: f for f in owner["fixtures"]}
im = Image.new("RGB", (PX, PX), (14, 14, 18)); d = ImageDraw.Draw(im); F = ImageFont.load_default(size=11)
for z in zones:
    pl = z.get("polyline_epsg32654") or []
    if not any(inside(q) for q in pl):
        continue
    c = str(z.get("carrier_class"))
    col = (100, 100, 115) if c == "building" else ((240, 150, 60) if c.startswith("rail") else
                                                   ((80, 160, 255) if c.startswith("water") else (200, 200, 200)))
    d.line([to(q) for q in pl], fill=col, width=1)
    m = pl[len(pl) // 2]
    if inside(m):
        d.text(to(m), str(z["carrier_id"])[-6:], fill=col, font=F)
for i in range(-int(R), int(R) + 1, 5):
    d.text((2, PX / 2 - i * S), "%+d" % i, fill=(60, 60, 60), font=F)
seen = set()
for p in sorted(glob.glob(str(HERE / "assess" / "fixture_review_*.json"))):
    for fid, r in json.loads(Path(p).read_text())["reviews"].items():
        seen.add(fid)
        if r.get("status") == "accepted":
            q = (r["E"], r["N"])
            if inside(q):
                x, y = to(q); d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(60, 230, 90))
                d.text((x + 5, y - 6), fid[-4:] + " " + r["module_id"][:16], fill=(160, 255, 170), font=F)
        else:
            f = proj.get(fid)
            if f and inside((f["E"], f["N"])):
                x, y = to((f["E"], f["N"])); d.line([(x - 3, y - 3), (x + 3, y + 3)], fill=(220, 60, 60)); d.line([(x - 3, y + 3), (x + 3, y - 3)], fill=(220, 60, 60))
for f in owner["fixtures"]:
    if f["fixture_id"] not in seen and inside((f["E"], f["N"])):
        x, y = to((f["E"], f["N"])); d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(0, 220, 255))
        d.text((x + 5, y - 6), f["fixture_id"][-5:] + " " + f["module_id"][:16], fill=(0, 220, 255), font=F)
im.save(out); print(out)
