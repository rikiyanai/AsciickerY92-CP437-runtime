"""fixtures_map.py RUN MODULE OUT.png -- reading aid: the proposed fixtures of one module (red, with
sighting count) on the plan (buildings grey, roads white), plus existing registered fixtures of that
module (green). For judging duplicates and positions by eye."""
import json, sys, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
run, mod, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
r = subprocess.run([sys.executable, str(HERE / "objects_to_fixtures.py"), str(run), "--dump"], capture_output=True, text=True)
props = [o for o in json.loads(r.stdout.splitlines()[-1]) if o["module_id"] == mod]
canon = json.loads((run / "canonical.world_ir.json").read_text())
ex = [q for q in json.loads((run / "observation_registration" / "entity_instances.json").read_text()) if q.get("module_id") == mod]
xs = [o["E"] for o in props] + [q["E"] for q in ex]; ys = [o["N"] for o in props] + [q["N"] for q in ex]
x0, x1, y0, y1 = min(xs) - 15, max(xs) + 15, min(ys) - 15, max(ys) + 15
S = min(1000 / (x1 - x0), 1000 / (y1 - y0))
W, H = int((x1 - x0) * S), int((y1 - y0) * S)
to = lambda p: ((p[0] - x0) * S, H - (p[1] - y0) * S)
img = Image.new("RGB", (W, H), (16, 16, 20)); d = ImageDraw.Draw(img); F = ImageFont.load_default(size=11)
for f in canon["canonical_features"]:
    g = f.get("geometry") or {}
    cs = g.get("coordinates") or []
    if not cs or g.get("kind") not in ("polyline", "polygon"):
        continue
    if not any(x0 <= c[0] <= x1 and y0 <= c[1] <= y1 for c in cs):
        continue
    col = (80, 80, 95) if f.get("semantic_class") == "building_footprint" else (170, 170, 170)
    if f.get("semantic_class") in ("building_footprint", "road"):
        d.line([to(c) for c in cs], fill=col, width=1)
for q in ex:
    p = to((q["E"], q["N"])); d.ellipse((p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3), outline=(60, 230, 90))
for o in props:
    p = to((o["E"], o["N"])); d.ellipse((p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3), fill=(240, 60, 60))
    d.text((p[0] + 4, p[1] - 6), str(len(o["sightings"])), fill=(255, 200, 200), font=F)
d.text((4, 4), "%s: %d proposed (red, sightings), %d existing (green); %.1f px/m" % (mod, len(props), len(ex), S),
       fill=(255, 255, 255), font=F)
img.save(out); print(out, img.size)
