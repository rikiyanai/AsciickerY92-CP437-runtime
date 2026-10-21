"""fixture_card.py FIXTURE_ID OUT.png -- reading aid for the manual fixture review (decides nothing).

Left: the photo of the fixture's closest sighting, cropped around the recorded x,y (yellow cross),
shown x2. Right: plan 30 m around the projected point (red dot): building footprints (grey) with
their zone carrier id tails, roads and railways (white / orange) with their carrier id tails, and the
camera of that sighting (cyan) with its bearing ray. North up."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
fid, out = sys.argv[1], sys.argv[2]
fx = next(f for f in json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                                        "authored_scene_fixtures.json").read_text())["fixtures"] if f["fixture_id"] == fid)
pano = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
zones = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/observation_registration/placement_zones.json").read_text())
s = min(fx["sightings"], key=lambda s: float(s.get("distance_m_est") or 99.0))
CAP = ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/captures"
im = Image.open(CAP / (s["capture_id"] + ".jpg")).convert("RGB").resize((1024, 512))
x, y = int(s.get("x") or 512), int(s.get("y") or 300)
crop = im.crop((max(0, x - 90), max(0, y - 150), min(1024, x + 90), min(512, y + 60))).resize((360, 420))
dc = ImageDraw.Draw(crop)
cx, cy = (x - max(0, x - 90)) * 2, (y - max(0, y - 150)) * 2
dc.line([(cx - 10, cy), (cx + 10, cy)], fill=(255, 255, 0), width=2); dc.line([(cx, cy - 10), (cx, cy + 10)], fill=(255, 255, 0), width=2)
PX, R = 420, 30.0
S = PX / (2 * R)
c0 = (fx["E"], fx["N"])
to = lambda q: (PX / 2 + (q[0] - c0[0]) * S, PX / 2 - (q[1] - c0[1]) * S)
plan = Image.new("RGB", (PX, PX), (16, 16, 20)); d = ImageDraw.Draw(plan); F = ImageFont.load_default(size=11)
for z in zones:
    pl = z.get("polyline_epsg32654") or []
    if not any(abs(q[0] - c0[0]) < R and abs(q[1] - c0[1]) < R for q in pl):
        continue
    cls = str(z.get("carrier_class"))
    col = (110, 110, 125) if cls == "building" else ((240, 150, 60) if cls.startswith("rail") else
                                                     ((80, 160, 255) if cls.startswith("water") else (210, 210, 210)))
    d.line([to(q) for q in pl], fill=col, width=1)
    mid = pl[len(pl) // 2]
    if abs(mid[0] - c0[0]) < R and abs(mid[1] - c0[1]) < R:
        d.text(to(mid), str(z["carrier_id"])[-6:], fill=col, font=F)
cam = pano.get(s["capture_id"])
if cam:
    q = to((cam["E"], cam["N"])); d.ellipse((q[0] - 3, q[1] - 3, q[0] + 3, q[1] + 3), fill=(0, 220, 255))
    d.line([q, to(c0)], fill=(0, 160, 200), width=1)
q = to(c0); d.ellipse((q[0] - 4, q[1] - 4, q[0] + 4, q[1] + 4), fill=(255, 50, 50))
card = Image.new("RGB", (360 + PX + 10, max(420, PX) + 40), (0, 0, 0))
card.paste(crop, (0, 40)); card.paste(plan, (370, 40))
dd = ImageDraw.Draw(card); G = ImageFont.load_default(size=13)
dd.text((4, 4), "%s %s  sightings %d  nearest: %s %.0fm x%d" % (fid, fx["module_id"], len(fx["sightings"]),
        s["capture_id"][-8:], float(s.get("distance_m_est") or 0), x), fill=(255, 255, 0), font=G)
dd.text((4, 20), str(s.get("what"))[:70] + " | beside: " + str(s.get("beside"))[:40], fill=(200, 255, 200), font=G)
card.save(out); print(out)
