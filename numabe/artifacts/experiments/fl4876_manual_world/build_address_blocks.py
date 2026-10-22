"""build_address_blocks.py -- lookup index (decides nothing about the map): Japanese block-level (番) address points
for the Numabe AOI, so the editor can offer 'copy address' for any building.

1. Town names (町丁目): GSI reverse geocoder (mreversegeocoder.gsi.go.jp LonLatToAddress) at every building
   footprint centroid, rounded to a 40 m grid so neighbouring buildings share one request.
2. Block points: for each town, GSI address search (msearch.gsi.go.jp AddressSearch) for "<town><n>" n = 1.. until
   eight consecutive numbers return no exact '<town><n>番' hit.
Requests are spaced 0.4 s. Writes address_blocks.json beside this script: {"towns": {grid_key: town},
"blocks": [{"town", "block", "lon", "lat", "E", "N"}], "muni": "東京都大田区", "sources": ...}. Re-running reuses
what the file already holds."""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from scripts.pipeline.register_observations import lonlat_to_epsg32654  # noqa: E402

OUT = HERE / "address_blocks.json"
doc = json.loads(OUT.read_text()) if OUT.exists() else {"towns": {}, "blocks": [], "muni": None}
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_74/canonical.world_ir.json").read_text())
osm = None


def get(url):
    time.sleep(0.4)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def to_lonlat(E, N):
    # inverse of lonlat_to_epsg32654 by Newton iteration on the forward transform (keeps one projection owner)
    lat, lon = 35.58, 139.67
    for _ in range(20):
        e0, n0 = lonlat_to_epsg32654(lat, lon)
        e1, _ = lonlat_to_epsg32654(lat, lon + 1e-5)
        _, n1 = lonlat_to_epsg32654(lat + 1e-5, lon)
        lon += (E - e0) / ((e1 - e0) / 1e-5)
        lat += (N - n0) / ((n1 - n0) / 1e-5)
    return lon, lat


cents = []
for f in canon["canonical_features"]:
    if f.get("semantic_class") != "building_footprint":
        continue
    c = f["geometry"]["coordinates"]
    r = c[0] if isinstance(c[0][0], list) else c
    cents.append((sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)))
for E, N in cents:
    key = "%d,%d" % (round(E / 40), round(N / 40))
    if key in doc["towns"]:
        continue
    lon, lat = to_lonlat(round(E / 40) * 40, round(N / 40) * 40)
    res = get("https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat=%.7f&lon=%.7f" % (lat, lon))
    rr = res.get("results") or {}
    doc["towns"][key] = rr.get("lv01Nm")
    if rr.get("muniCd") == "13111":
        doc["muni"] = "東京都大田区"
    elif rr.get("muniCd"):
        doc.setdefault("other_muni", {})[key] = rr.get("muniCd")
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
towns = sorted({t for t in doc["towns"].values() if t})
print("towns", towns)
have = {(b["town"], b["block"]) for b in doc["blocks"]}
for town in towns:
    if any(b["town"] == town for b in doc["blocks"]):
        continue
    miss, n = 0, 0
    while miss < 8 and n < 120:
        n += 1
        q = "%s%s%d" % (doc["muni"] or "東京都大田区", town, n)
        hits = get("https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(q))
        exact = [h for h in hits if (h.get("properties") or {}).get("title", "").replace("　", "") ==
                 "%s%s%s番" % (doc["muni"] or "東京都大田区", town, str(n).translate(str.maketrans("0123456789", "０１２３４５６７８９")))]
        if not exact:
            miss += 1
            continue
        miss = 0
        lon, lat = exact[0]["geometry"]["coordinates"]
        E, N = lonlat_to_epsg32654(lat, lon)
        if (town, n) not in have:
            doc["blocks"].append({"town": town, "block": n, "lon": lon, "lat": lat, "E": round(E, 2), "N": round(N, 2)})
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print(town, sum(1 for b in doc["blocks"] if b["town"] == town), "blocks")
# 3. Building numbers (号): for every block, "<town><block>-<n>" n = 1.. until eight consecutive misses. The GSI
#    point of an exact '<town><block>番<n>号' hit is the residence-indication point; the editor assigns it to the
#    footprint that contains it (or the nearest within 3 m).
if "--go" in sys.argv:
    zen = str.maketrans("0123456789", "０１２３４５６７８９")
    doc.setdefault("go", [])
    done = {(g["town"], g["block"]) for g in doc["go"]} | set(map(tuple, doc.get("go_blocks_done", [])))
    for b in sorted(doc["blocks"], key=lambda b: (b["town"], b["block"])):
        if (b["town"], b["block"]) in done:
            continue
        miss, n = 0, 0
        while miss < 8 and n < 80:
            n += 1
            base = "%s%s" % (doc["muni"], b["town"])
            hits = get("https://msearch.gsi.go.jp/address-search/AddressSearch?q=" +
                       urllib.parse.quote("%s%d-%d" % (base, b["block"], n)))
            want = "%s%s番%s号" % (base, str(b["block"]).translate(zen), str(n).translate(zen))
            exact = [h for h in hits if (h.get("properties") or {}).get("title", "").replace("　", "") == want]
            if not exact:
                miss += 1
                continue
            miss = 0
            lon, lat = exact[0]["geometry"]["coordinates"]
            E, N = lonlat_to_epsg32654(lat, lon)
            doc["go"].append({"town": b["town"], "block": b["block"], "go": n, "lon": lon, "lat": lat,
                              "E": round(E, 2), "N": round(N, 2)})
        doc.setdefault("go_blocks_done", []).append([b["town"], b["block"]])
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        print(b["town"], b["block"], "go points", sum(1 for g in doc["go"] if g["town"] == b["town"] and g["block"] == b["block"]),
              flush=True)
doc["sources"] = {"go": "GSI AddressSearch exact '<town><block>番<n>号' hits (residence-indication points)",
                  "towns":"GSI reverse geocoder LonLatToAddress (lv01Nm) at 40 m grid points of footprint centroids",
                  "blocks": "GSI AddressSearch exact '<town><n>番' hits; the point is GSI's block representative point",
                  "built_on": time.strftime("%Y-%m-%d")}
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
print("blocks", len(doc["blocks"]))
