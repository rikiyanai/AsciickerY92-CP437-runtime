"""plateau_attrs.py -- listing aid only (decides nothing): per canonical building footprint, the matched
PLATEAU building's attributes (bldg:usage code, measuredHeight, storeysAboveGround) read from the bundle's
CityGML, via the footprint's obs:plateau observation id (or its plateau/ feature id). Writes
assess/plateau_attrs.json {feature_id: {plateau_id, usage, usage_label, height_m, storeys}}."""
import json, re
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GML = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/plateau/udx/bldg"
USAGE = {"401": "office", "402": "commercial", "403": "lodging", "404": "commercial_mixed", "411": "detached_house",
         "412": "apartment", "413": "house_with_shop", "414": "apartment_with_shop", "415": "house_with_workshop",
         "421": "public_office", "422": "school_welfare", "431": "factory", "441": "agricultural",
         "451": "utility", "452": "transport", "453": "religious_other", "454": "other", "461": "unknown"}
attrs = {}
for p in sorted(GML.glob("*.gml")):
    s = p.read_text(encoding="utf-8")
    for m in re.finditer(r'<bldg:Building gml:id="([^"]+)">(.*?)</bldg:Building>', s, re.S):
        gid, body = m.group(1), m.group(2)
        u = re.search(r"<bldg:usage[^>]*>([^<]+)</bldg:usage>", body)
        h = re.search(r"<bldg:measuredHeight[^>]*>([^<]+)</bldg:measuredHeight>", body)
        st = re.search(r"<bldg:storeysAboveGround>([^<]+)</bldg:storeysAboveGround>", body)
        attrs[gid] = {"usage": u.group(1).strip() if u else None, "height_m": float(h.group(1)) if h else None,
                      "storeys": int(st.group(1)) if st else None}
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_46/canonical.world_ir.json").read_text())
out = {}
for f in canon["canonical_features"]:
    if f.get("semantic_class") != "building_footprint":
        continue
    ids = set()
    for v in (f.get("properties") or {}).values():
        for o in (v or {}).get("observation_ids", []) if isinstance(v, dict) else []:
            if str(o).startswith("obs:plateau:"):
                ids.add(str(o).split("obs:plateau:")[1])
    if "plateau/" in f["feature_id"]:
        ids.add(f["feature_id"].split("plateau/")[1])
    hit = next((i for i in sorted(ids) if i in attrs), None)
    if hit:
        a = attrs[hit]
        out[f["feature_id"]] = dict(a, plateau_id=hit, usage_label=USAGE.get(str(a["usage"]), a["usage"]))
(HERE / "assess" / "plateau_attrs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
from collections import Counter
print("plateau buildings", len(attrs), "| footprints matched", len(out), "| usage", Counter(v["usage_label"] for v in out.values()))
