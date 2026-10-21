"""objects_to_fixtures.py RUN [--write] -- turn the hand-recorded panorama objects into fixture points.

The decisions are the reviewers' (what, image x, estimated distance, heading offset, camera shift)
and the person's vocabulary table below (plain words -> catalog module; transient / surface /
infrastructure / continuous-run words -> not placed here). This only does the geometry:
  bearing = metadata heading + offset + angle of x (equirectangular: (x/1024-0.5)*360;
            flat photo: atan((x/1024-0.5)*2*tan(32.5 deg)), 65 deg field of view)
  point   = camera (+ hand shift) + distance * (sin bearing, cos bearing)
and bookkeeping: sightings of one module within 2.5 m are one object (the person reviews the result
on the map); points within 2.5 m of an existing registered fixture of the same module are dropped.
Writes assets/source_bundles/.../authored_scene_fixtures.json with every source sighting listed."""
import glob, json, math, re, sys
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_scene_fixtures.json"
run = Path(sys.argv[1])
# person-written vocabulary: first matching rule wins; None = not placed as a point fixture
VOCAB = [
    (r"parked (car|white car|kei|truck|scooter)|car |scooter|cone|scaffold|construction", None),
    (r"zebra|crosswalk|manhole|road text|marking|tactile|edge line|shoulder strip|speed marking", None),
    (r"viaduct|overpass|canopy|platform sign|ticket|gate machine|antenna|hut|shed|water tower|banner|awning", None),
    (r"kerb|curb|gutter|cable trough|air.?conditioner on|wall.mounted ac", None),
    (r"pedestrian|people|train \(|series train|parked train|van|parked silver|drain|grate|hatch|bench|stair|"
     r"accordion gate|mesh gate|metal gate|doormat|forecourt|paving|promenade|embankment|deck|ramp|steps|"
     r"platform number|marker board|sign stake|railway signal|edge slabs|conduit|drain pipe|pilaster|column", None),
    (r"a-board|sign stand|standing sign|stop board|menu board|poster stand|pillar sign|pole sign|shop sign on post", "sidewalk_signboard"),
    (r"goods rack|display rack|display shelves|crate stack|merchandise", "frontage_merchandise_display"),
    (r"garbage|rubbish|net box|collection point", "waste_collection_bin_pair"),
    (r"gate post|gate pillar|granite|pedestal", "stone_marker_bollard"),
    (r"equipment box|signal / control box", "meter_and_service_cabinet"),
    (r"pole-mounted road sign|information sign on post|rectangular traffic sign|traffic sign|road sign", "regulatory_sign_family"),
    (r"(rail|railway|platform|track|trackside).*fence|fence.*(rail|railway|platform|track)", "rail_corridor_mesh_fence"),
    (r"picket", "picket_fence"),
    (r"mesh fence|chain.?link", "mesh_fence"),
    (r"lattice", "lattice_boundary_fence"),
    (r"fence", "metal_fence"),
    (r"hedge", "trimmed_hedge"),
    (r"guard ?rail|guard ?pipe|pipe guard|railguard|tubular guard", "white_railguard"),
    (r"railing", "pedestrian_guardrail_continuous"),
    (r"block wall|concrete wall|retaining wall", "concrete_block_wall"),
    (r"stone wall|stone.walled", "stone_cobble_wall"),
    (r"ivy", "ivy_wall"),
    (r"channel|waterway|stream", "stone_lined_water_channel"),
    (r"wall", None),
    (r"level.crossing|crossing (barrier|warning|signal)|barrier machine|barrier arm", "level_crossing_equipment"),
    (r"catenary|gantry", "overhead_gantry_wire"),
    (r"street ?lamp|streetlight|street light|lamp", "pole_streetlight"),
    (r"utility pole|concrete pole|power pole|telegraph pole", "utility_pole_wire_cluster"),
    (r"traffic signal|pedestrian signal|signal head", "horizontal_traffic_signal"),
    (r"mirror", "traffic_mirror"),
    (r"max.speed|regulatory|round .*sign|no.entry|stop sign|speed limit|red.rimmed|red.ringed|blue round", "regulatory_sign_family"),
    (r"direction(al)? sign|street name", "directional_road_sign"),
    (r"warning sign|warning panel", "pedestrian_warning_sign"),
    (r"inscribed stone|stone post|stone marker|monument", "stone_marker_bollard"),
    (r"bollard|delineator", "roadside_reflector_bollard"),
    (r"vending", "vending_white_red_blue"),
    (r"post box|pillar box|red post|japan post|mailbox", "red_mailbox"),
    (r"poster board|election poster", "poster_notice_carrier"),
    (r"notice board|sign board|signboard|information board|notice", "notice_sign"),
    (r"potted|planter|flower ?pot|shrub", "potted_frontage_plant"),
    (r"tree", "street_tree"),
    (r"bin|refuse|recycling|waste", "waste_collection_bin_pair"),
    (r"air.conditioner|ac unit|ac outdoor|condenser", "exterior_ac_condenser"),
    (r"bicycle", "parked_bicycles"),
    (r"cabinet|meter box|service box|tall yellow box", "meter_and_service_cabinet"),
]
pano = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
CAP = ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/captures"
sizes = {}


def is_equirect(cid):
    if cid not in sizes:
        with Image.open(CAP / (cid + ".jpg")) as im:
            sizes[cid] = im.size
    w, h = sizes[cid]
    return abs(w / h - 2.0) < 0.05


def module_for(what):
    w = str(what or "").lower()
    for pat, mod in VOCAB:
        if re.search(pat, w):
            return mod, pat
    return None, "no rule"


pts, skipped, unmatched = [], {}, {}
for g in sorted(glob.glob(str(HERE / "assess" / "group_*.json"))):
    d = json.loads(Path(g).read_text())
    for cid, p in d["panoramas"].items():
        if not p.get("aligned") or p.get("heading_offset_deg") is None or cid not in pano:
            continue
        cam = pano[cid]
        sh = p.get("camera_shift_m") or [0, 0]
        E0, N0 = cam["E"] + float(sh[0]), cam["N"] + float(sh[1])
        hd = cam["heading_deg"] + float(p["heading_offset_deg"])
        eq = is_equirect(cid)
        for o in p.get("objects") or []:
            mod, why = module_for(o.get("what"))
            if mod is None:
                skipped[why] = skipped.get(why, 0) + 1
                if why == "no rule":
                    unmatched[str(o.get("what", "")).lower()[:60]] = unmatched.get(str(o.get("what", "")).lower()[:60], 0) + 1
                continue
            x, dist = o.get("x"), o.get("distance_m_est")
            if x is None or not dist:
                skipped["no x or distance"] = skipped.get("no x or distance", 0) + 1
                continue
            u = float(x) / 1024.0 - 0.5
            ang = u * 360.0 if eq else math.degrees(math.atan(u * 2 * math.tan(math.radians(32.5))))
            b = math.radians(hd + ang)
            pts.append({"module_id": mod, "E": E0 + float(dist) * math.sin(b), "N": N0 + float(dist) * math.cos(b),
                        "sighting": {"group": Path(g).stem, "capture_id": cid, "x": x, "y": o.get("y"),
                                     "distance_m_est": dist, "what": o.get("what"), "beside": o.get("beside")}})
# one object per module within 2.5 m (greedy, nearest-first seeds)
# person's common-sense spacing: two of these closer than this are one object seen twice
MIN_SPACING_M = {"overhead_gantry_wire": 8.0, "utility_pole_wire_cluster": 6.0, "pole_streetlight": 6.0,
                 "horizontal_traffic_signal": 4.0, "level_crossing_equipment": 4.0, "red_mailbox": 4.0,
                 "regulatory_sign_family": 3.0, "directional_road_sign": 3.0, "pedestrian_warning_sign": 3.0,
                 "street_tree": 3.0, "waste_collection_bin_pair": 3.0, "parked_bicycles": 3.0,
                 "traffic_mirror": 3.0, "notice_sign": 2.5, "poster_notice_carrier": 2.5,
                 "vending_white_red_blue": 2.0, "potted_frontage_plant": 1.5, "roadside_reflector_bollard": 1.5,
                 "stone_marker_bollard": 1.5, "exterior_ac_condenser": 1.5, "meter_and_service_cabinet": 2.0,
                 # continuous runs: one run per stretch; sightings closer than this are the same run
                 "rail_corridor_mesh_fence": 8.0, "picket_fence": 5.0, "mesh_fence": 5.0, "lattice_boundary_fence": 5.0,
                 "metal_fence": 5.0, "trimmed_hedge": 5.0, "white_railguard": 6.0,
                 "pedestrian_guardrail_continuous": 6.0, "concrete_block_wall": 5.0, "stone_cobble_wall": 5.0,
                 "ivy_wall": 4.0, "stone_lined_water_channel": 10.0}
objs = []
for q in sorted(pts, key=lambda q: q["sighting"]["distance_m_est"]):
    dq = float(q["sighting"]["distance_m_est"])
    cand = [o for o in objs if o["module_id"] == q["module_id"]
            and math.dist((o["E"], o["N"]), (q["E"], q["N"])) <= max(MIN_SPACING_M.get(q["module_id"], 2.5),
                                                                     1.5 + 0.3 * max(dq, o["d0"]))]
    if cand:  # same object seen again: distance estimates are +-30%, so the merge radius grows with range
        hit = min(cand, key=lambda o: math.dist((o["E"], o["N"]), (q["E"], q["N"])))
        hit["_pts"].append((q["E"], q["N"], dq)); hit["sightings"].append(q["sighting"])
    else:
        objs.append({"module_id": q["module_id"], "E": q["E"], "N": q["N"], "d0": dq,
                     "_pts": [(q["E"], q["N"], dq)], "sightings": [q["sighting"]]})
for o in objs:  # position: sightings closer to the camera weigh more
    w = [1.0 / max(1.0, d) ** 2 for _, _, d in o["_pts"]]
    o["E"] = sum(e * wi for (e, _, _), wi in zip(o["_pts"], w)) / sum(w)
    o["N"] = sum(n * wi for (_, n, _), wi in zip(o["_pts"], w)) / sum(w)
    o.pop("_pts"); o.pop("d0")
existing = [r for r in json.loads((run / "observation_registration" / "entity_instances.json").read_text())
            if r.get("module_id")]
keep, dup = [], 0
for o in objs:
    if any(r["module_id"] == o["module_id"] and math.dist((r["E"], r["N"]), (o["E"], o["N"])) <= 2.5 for r in existing):
        dup += 1
        continue
    keep.append(o)
import collections
if "--unmatched" in sys.argv:
    for k, v in sorted(unmatched.items(), key=lambda kv: -kv[1])[:80]:
        print(v, k)
    sys.exit(0)
if "--dump" in sys.argv:
    print(json.dumps(keep)); sys.exit(0)
print("sightings placed", len(pts), "| objects", len(objs), "| already in map", dup, "| new", len(keep))
print("by module", dict(collections.Counter(o["module_id"] for o in keep).most_common()))
print("not placed", dict(sorted(skipped.items(), key=lambda kv: -kv[1])[:12]))
if "--write" in sys.argv:
    doc = {"schema": "y92.fl4876.authored_scene_fixtures.v1",
           "owner": "scripts/pipeline/resolve_identity_entities.py (FL-4876 MANUAL-FIXTURE-02; appended as generic fixtures)",
           "method": __doc__, "decided_by": "hand panorama review (assess/group_*.json) + vocabulary table in objects_to_fixtures.py",
           "fixtures": [dict(o, fixture_id="SF-4876-%04d" % (k + 1), E=round(o["E"], 2), N=round(o["N"], 2))
                        for k, o in enumerate(keep)]}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print("written", OUT)
