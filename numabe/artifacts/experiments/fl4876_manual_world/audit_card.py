"""audit_card.py INSTANCE_ID OUT.png -- reading aid only (decides nothing): for one derived observation
instance named by an authored spatial decision, the annotation crop(s) it came from (left) and a 20 m
plan (right) around its build_reg_43 point (red) with zone carriers and every accepted authored scene
fixture (green dot, id tail + module) so the reviewer can judge whether an authored fixture already owns
the same physical object."""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
iid, out = sys.argv[1], sys.argv[2]
R43 = ROOT / ".run/fl4876_builds/build_reg_43"
inst = next(i for i in json.loads((R43 / "observation_registration/registered_instances.json").read_text())
            if i["instance_id"] == iid)
rows = {}
for l in (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/numabe_full_727_mapping_20260915/"
          "observation_module_mapping.jsonl").read_text().splitlines():
    if l.strip():
        r = json.loads(l); rows[r["annotation_id"]] = r
crops = []
BOX = ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915"
boxes = {a["annotation_id"]: a for a in json.loads((BOX / "annotations.json").read_text())["annotations"]}
for a in inst.get("annotation_ids") or []:
    p = Path(str((rows.get(a) or {}).get("crop_path") or ""))
    got = False
    for cand in (p, ROOT / "artifacts/experiments/fl4813_capture_review_20260915/crops" / p.name):
        if cand.is_file():
            crops.append(Image.open(cand).convert("RGB")); got = True; break
    if not got and a in boxes:  # cut the operator box (padded) from the capture itself
        b = boxes[a]; cap = BOX / "captures" / (b["capture_id"] + ".jpg")
        if cap.is_file():
            im = Image.open(cap).convert("RGB"); W, H = im.size
            x, y0, w, h = b["box_norm"]
            px, py = 0.25 * w, 0.25 * h
            crops.append(im.crop((int(max(0, x - px) * W), int(max(0, y0 - py) * H),
                                  int(min(1, x + w + px) * W), int(min(1, y0 + h + py) * H))))
left = Image.new("RGB", (420, 420), (0, 0, 0))
y = 0
for c in crops[:2]:
    c.thumbnail((420, 205)); left.paste(c, (0, y)); y += 210
E0, N0 = float(inst["E"]), float(inst["N"])
R, PX = 20.0, 420
S = PX / (2 * R)
to = lambda q: (PX / 2 + (q[0] - E0) * S, PX / 2 - (q[1] - N0) * S)
inside = lambda q: abs(q[0] - E0) < R and abs(q[1] - N0) < R
plan = Image.new("RGB", (PX, PX), (14, 14, 18)); d = ImageDraw.Draw(plan); F = ImageFont.load_default(size=11)
for z in json.loads((R43 / "observation_registration/placement_zones.json").read_text()):
    pl = z.get("polyline_epsg32654") or []
    if any(inside(q) for q in pl):
        c = str(z["carrier_class"])
        col = (100, 100, 115) if c == "building" else ((240, 150, 60) if c.startswith("rail") else
                                                       ((80, 160, 255) if c.startswith("water") else (200, 200, 200)))
        d.line([to(q) for q in pl], fill=col, width=1)
for f in json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
                            "authored_scene_fixtures.json").read_text())["fixtures"]:
    if (f.get("manual_review") or {}).get("status") == "accepted" and inside((f["E"], f["N"])):
        x, yy = to((f["E"], f["N"])); d.ellipse((x - 3, yy - 3, x + 3, yy + 3), fill=(60, 230, 90))
        d.text((x + 4, yy - 6), f["fixture_id"][-4:] + " " + f["module_id"][:14], fill=(160, 255, 170), font=F)
x, yy = to((E0, N0)); d.ellipse((x - 5, yy - 5, x + 5, yy + 5), outline=(255, 60, 60), width=2)
card = Image.new("RGB", (850, 460), (0, 0, 0)); card.paste(left, (0, 40)); card.paste(plan, (430, 40))
ImageDraw.Draw(card).text((4, 6), "%s %s %s  E %.1f N %.1f" % (iid, inst["module_id"], inst.get("variant"), E0, N0),
                          fill=(255, 255, 0), font=ImageFont.load_default(size=14))
card.save(out); print(out, len(crops), "crops")
