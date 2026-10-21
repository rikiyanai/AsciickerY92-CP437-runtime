"""pose_aids.py CAPTURE_ID... -- measuring aid (decides nothing): for each capture prints pose, timestamp, sun
azimuth/altitude at that time and place (NOAA-style low-precision formula, ~1 deg), the 4 nearest road segments
(carrier, class, distance, bearing) and the buildings within 40 m (carrier, distance, longest-edge bearing)."""
import datetime, json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
pi = json.loads((Path(__file__).resolve().parent / "pano_index.json").read_text())["panoramas"]
zones = json.loads((ROOT / ".run/fl4876_builds/build_reg_61/observation_registration/placement_zones.json").read_text())


def sun(lat, lon, ts):
    t = datetime.datetime.fromisoformat(ts[:19]).replace(tzinfo=datetime.timezone.utc)
    n = (t - datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)).total_seconds() / 86400
    L = (280.46 + 0.9856474 * n) % 360; g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.02 * math.sin(2 * g)); eps = math.radians(23.439 - 4e-7 * n)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)); dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24; ha = math.radians(gmst * 15 + lon) - ra
    la = math.radians(lat)
    alt = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha), math.tan(dec) * math.cos(la) - math.sin(la) * math.cos(ha))
    return math.degrees(az) % 360, math.degrees(alt)


def segs(pl):
    return zip(pl, pl[1:])


def dist_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy or 1e-9
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy), math.degrees(math.atan2(dx, dy)) % 360


for cid in sys.argv[1:]:
    k = cid if cid.startswith("mapillary_") else "mapillary_" + cid
    p = pi[k]; q = (p["E"], p["N"])
    az, alt = sun(p["lat"], p["lon"], p["timestamp"])
    print("%s E %.1f N %.1f compass %s %s %dx%d | sun az %.0f alt %.0f" % (k, p["E"], p["N"], p.get("heading_deg"),
          p["timestamp"][:19], p["width"], p["height"], az, alt))
    roads, blds = [], []
    for z in zones:
        pl = z.get("polyline_epsg32654") or []
        cls = str(z["carrier_class"])
        if cls.startswith("road"):
            best = min((dist_seg(q, a, b) for a, b in segs(pl)), default=None)
            if best:
                roads.append((best[0], z["carrier_id"], cls, best[1]))
        elif cls == "building":
            best = min((dist_seg(q, a, b)[0] for a, b in segs(pl)), default=99)
            if best < 40:
                e = max(segs(pl), key=lambda s: math.dist(*s))
                blds.append((best, z["carrier_id"], math.degrees(math.atan2(e[1][0] - e[0][0], e[1][1] - e[0][1])) % 180))
    for r in sorted(roads)[:4]:
        print("   road %-12s %-18s %5.1f m  bearing %.0f / %.0f" % (r[1], r[2], r[0], r[3], (r[3] + 180) % 360))
    for b in sorted(blds)[:8]:
        print("   bldg %-22s %5.1f m  long edge %.0f / %.0f" % (b[1], b[0], b[2], b[2] + 180))
