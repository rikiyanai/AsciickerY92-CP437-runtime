"""fetch_aerial.py -- data acquisition only: GSI seamless aerial photo tiles (zoom 18) covering every
canonical building footprint, fetched one at a time (0.5 s apart) and stitched into aerial/mosaic.jpg
with aerial/mosaic.json (tile range, zoom, pixel <-> lon/lat). Source: 国土地理院 シームレス空中写真
https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg (CC BY 4.0, attribution: 国土地理院).
Decides nothing about any building."""
import json, math, subprocess, sys, time
from pathlib import Path
from PIL import Image
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
Z = 18
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())


def utm_to_ll(E, N):  # inverse UTM zone 54N (WGS84)
    k0, a, f = 0.9996, 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    x, y = E - 500000.0, N
    mu = y / k0 / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu) \
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
    C = ep2 * math.cos(p) ** 2; T = math.tan(p) ** 2
    Nn = a / math.sqrt(1 - e2 * math.sin(p) ** 2); R = a * (1 - e2) / (1 - e2 * math.sin(p) ** 2) ** 1.5
    D = x / (Nn * k0)
    lat = p - (Nn * math.tan(p) / R) * (D ** 2 / 2 - (5 + 3 * T + 10 * C - 4 * C ** 2 - 9 * ep2) * D ** 4 / 24)
    lon = (D - (1 + 2 * T + C) * D ** 3 / 6) / math.cos(p)
    return math.degrees(lon) + 141.0, math.degrees(lat)


def tile_xy(lon, lat):
    n = 2 ** Z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


pts = [utm_to_ll(*q[:2]) for f in canon["canonical_features"] if f.get("semantic_class") == "building_footprint"
       for q in f["geometry"]["coordinates"]]
xs, ys = zip(*(tile_xy(lo, la) for lo, la in pts))
tx0, tx1, ty0, ty1 = int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys))
out = HERE / "aerial"; (out / "tiles").mkdir(parents=True, exist_ok=True)
print("tiles x %d-%d y %d-%d (%d)" % (tx0, tx1, ty0, ty1, (tx1 - tx0 + 1) * (ty1 - ty0 + 1)))
mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
for tx in range(tx0, tx1 + 1):
    for ty in range(ty0, ty1 + 1):
        p = out / "tiles" / ("%d_%d_%d.jpg" % (Z, tx, ty))
        if not p.is_file():
            url = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/%d/%d/%d.jpg" % (Z, tx, ty)
            subprocess.run(["curl", "-s", "--max-time", "30", "-o", str(p), url], check=False)
            time.sleep(0.5)
        try:
            mosaic.paste(Image.open(p).convert("RGB"), ((tx - tx0) * 256, (ty - ty0) * 256))
        except Exception as e:
            print("tile failed", p.name, e)
mosaic.save(out / "mosaic.jpg", quality=92)
(out / "mosaic.json").write_text(json.dumps({
    "source": "国土地理院 シームレス空中写真 (seamlessphoto), CC BY 4.0", "zoom": Z,
    "tile_x0": tx0, "tile_y0": ty0, "tiles_x": tx1 - tx0 + 1, "tiles_y": ty1 - ty0 + 1,
    "pixel_of_lonlat": "px = (tile_x(lon) - tile_x0) * 256, py = (tile_y(lat) - tile_y0) * 256 (web mercator)"}, indent=1))
print("mosaic", mosaic.size)
