#!/usr/bin/env python3
"""map_editor.py [--port 8792] [--build .run/fl4876_builds/live_numabe] -- Numabe operator map editor.

A loopback-only web editor for the Numabe map. THE OPERATOR'S PAGE IS / (the A3D editor, map_editor_ui/a3d.html):
open that one link. The /map plan page below was rejected by the operator and /view is a legacy evidence page;
neither is handed out as the editor (operator correction 2026-10-16). Legacy description of the old two-tab layout:

  /map   top-down plan: GSI aerial mosaic, building footprints filled with their authored/inferred wall
         colour, roads, every lowered object of the build (fixtures, linear segments, custom signs), the
         346 panorama cameras with their true headings, and the operator queue rectangles.
         Tools: click/shift-click select, drag a rectangle to select everything inside it, N = note on the
         rectangle, Delete = delete the selected objects, Cmd/Ctrl+V = paste a facade photo onto the
         selected building (face = the footprint edge nearest the rectangle/click), WASD/arrows pan,
         wheel zoom, Cmd/Ctrl+Z undo the last fixture delete.
  /view  evidence for the current selection (synchronised with /map over a BroadcastChannel): the
         building's appearance record, pasted facade photos, and every panorama that saw it with a
         marker at the bearing where the object sits; facade photos can also be pasted here.

Ownership (what each action writes, in the same request, before replying):
  * notes and facade pastes   -> operator_map_review/queue.json (open items the agent then resolves)
  * pasted images             -> operator_map_review/facades/<sha256-16>.<ext> (content-addressed)
  * facade paste on building  -> authored_building_appearance.json buildings[id].operator_facade_photos
  * delete of an authored fixture (attachment att-obs-authored-SF-...) -> the row is removed from
    authored_scene_fixtures.json and the removed row is appended to operator_map_review/journal.jsonl
    (undo restores it); delete of any other object (rule/observation generated) -> a queued
    delete_request, because those objects have different owners the agent must route by hand.
The editor decides nothing about appearance itself; it records operator intent in the owners above.
The objects shown come from the lowered instances of --build, so they can lag owner edits until the
next build; the header of /map names the build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
APPEARANCE = BUNDLE / "authored_building_appearance.json"
FIXTURES = BUNDLE / "authored_scene_fixtures.json"
PALETTE = BUNDLE / "inferred_building_palette.json"
REVIEW = HERE / "operator_map_review"
QUEUE = REVIEW / "queue.json"
JOURNAL = REVIEW / "journal.jsonl"
FACADES = REVIEW / "facades"
BOOKMARKS = REVIEW / "bookmarks.json"
# FL-4876 SV-OVERLAY-01: hand alignment of the equirectangular panoramas to the A3D first-person camera (street view
# overlay, map_editor_ui/street_view_overlay.js). Owner of these offsets; never written into assets/source_bundles.
SV_ALIGNMENT = HERE / "sv_alignment.json"
AERIAL = HERE / "aerial"
# FL-4876 Tama river: extend_aerial_mosaic.py writes aerial/mosaic_wide.{jpg,json} (the original tile range plus the strip west
# of the map). The server reads the affine once at start, so the wide mosaic is used from the next server start; mosaic.jpg stays.
MOSAIC = "mosaic_wide" if (AERIAL / "mosaic_wide.json").is_file() else "mosaic"
# FL-4876 FACADE-CAPTURE-01: operator facade captures (owner read by scripts/pipeline/compile_sign_sprites.py)
CAPTURES = BUNDLE / "operator_facade_captures.v1.json"
ADDRESS_BLOCKS = HERE / "address_blocks.json"  # build_address_blocks.py: GSI town + block (番) points
CAPTURE_SPRITES = BUNDLE / "operator_facade_sprites"
SOURCE_KINDS = {  # provenance class of the photo a capture was made from
    "google_streetview_reference": "Google Street View screenshot: operator reference only, not redistributable imagery",
    "mapillary_cc_by_sa": "Mapillary panorama (CC BY-SA 4.0, attribution required)",
    "web_image_reference": "Google Images / web graphic (logo, sign artwork): operator reference only, not redistributable",
    "operator_photo": "photo taken by the operator",
    "other": "other source; see note"}
LOCK = threading.Lock()  # one writer at a time across the threaded handlers
MAX_BODY = 24 << 20
IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
SF_RE = re.compile(r"^att-obs-authored-(SF-4876-[A-Za-z0-9]+)$")


def map_stamp(build: Path) -> tuple:
    """Change stamp of a build's drawn content: output.a3d and the meshes folder (mtime_ns, size)."""
    out = []
    for p in (build / "output.a3d", build / "meshes"):
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def now() -> str:
    """UTC timestamp for owner records."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, doc) -> None:
    """Atomic JSON write (temp file + rename) so a crash never leaves a half-written owner."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


SV_SCHEMA = "y92.fl4876.sv_alignment.v1"
SV_LIMITS = {"dE": 50.0, "dN": 50.0, "dheading_deg": 180.0, "dpitch_deg": 20.0, "droll_deg": 20.0}


def sv_alignment_doc() -> dict:
    """The saved street-view alignments (empty document when the owner file does not exist yet)."""
    if SV_ALIGNMENT.is_file():
        return json.loads(SV_ALIGNMENT.read_text())
    return {"schema": SV_SCHEMA, "alignments": {}}


_SV_CAMERAS = None


def sv_camera_meta() -> dict:
    """Mapillary capture metadata per picture id from the imagery surveys (read only): camera_type ('spherical',
    'equirectangular', 'fisheye', 'perspective'), camera model, source size, and which survey said so."""
    global _SV_CAMERAS
    if _SV_CAMERAS is None:
        out = {}
        files = sorted(BUNDLE.parent.glob("*/*survey*.json")) + sorted((ROOT / "artifacts/experiments").glob("fl4810_*/imagery_survey.json"))
        for f in files:
            try:
                pics = ((json.loads(f.read_text()).get("providers") or {}).get("mapillary") or {}).get("pictures") or []
            except (ValueError, AttributeError):
                continue
            for p in pics:
                pid = str(p.get("picture_id") or "")
                ct = (p.get("extra") or {}).get("camera_type")
                if pid and ct and pid not in out:
                    out[pid] = {"camera_type": ct, "camera": p.get("camera"), "camera_parameters": (p.get("extra") or {}).get("camera_parameters"),
                                "source_size": [p.get("width"), p.get("height")], "survey": rel(f)}
        _SV_CAMERAS = out
    return _SV_CAMERAS


def sv_projection(pid: str, p: dict) -> dict:
    """Projection the overlay may assume: 'equirect' only when Mapillary says spherical/equirectangular; 'fisheye' when
    it says fisheye; 'unknown' (treated like fisheye: plain crop, no reprojection) when no survey knows the picture."""
    m = sv_camera_meta().get(pid.split("_", 1)[-1])
    ct = m and m["camera_type"]
    kind = "equirect" if ct in ("spherical", "equirectangular") else "fisheye" if ct == "fisheye" else "perspective" if ct == "perspective" else "unknown"
    return {"kind": kind, "camera_type": ct, "camera": m and m["camera"], "camera_parameters": m and m["camera_parameters"],
            "source": m["survey"] if m else "no survey record (pano_index: %s)" % (p.get("projection_basis") or p.get("projection"))}


def sv_panoramas(world) -> dict:
    """Equirectangular panoramas for the street-view overlay: pose (E/N, world x/y), compass heading, authored
    heading offset (read only) and the base heading the overlay corrects (compass + offset, compass when unaligned),
    plus the saved alignments. Perspective photos (unknown FOV) are counted, not listed."""
    app = json.loads(APPEARANCE.read_text())["panoramas"]
    out, persp, skipped = [], 0, {}
    for pid, p in json.loads((HERE / "pano_index.json").read_text())["panoramas"].items():
        if p.get("projection") != "equirectangular":
            persp += 1
            k = "%s / %s" % (p.get("projection"), sv_projection(pid, p)["camera_type"])
            skipped[k] = skipped.get(k, 0) + 1
            continue
        off = app.get(pid, {}).get("heading_offset_deg")
        off = off if isinstance(off, (int, float)) else None
        x, y = world.proj.world(p["E"], p["N"])
        out.append({"id": pid, "E": p["E"], "N": p["N"], "x": x, "y": y, "compass": p["heading_deg"], "offset": off,
                    "base_heading": (p["heading_deg"] + (off or 0.0)) % 360, "img": p.get("local_path"),
                    "timestamp": p.get("timestamp"), "size": [p.get("width"), p.get("height")], "projection": sv_projection(pid, p)})
    census = {}
    for q in out:
        c = census.setdefault(q["projection"]["kind"], {"n": 0, "camera_types": {}, "cameras": {}, "examples": []})
        c["n"] += 1
        c["camera_types"][str(q["projection"]["camera_type"])] = c["camera_types"].get(str(q["projection"]["camera_type"]), 0) + 1
        c["cameras"][str(q["projection"]["camera"])] = c["cameras"].get(str(q["projection"]["camera"]), 0) + 1
        if len(c["examples"]) < 4:
            c["examples"].append(q["id"])
    return {"panoramas": out, "perspective_skipped": persp, "skipped_by_type": skipped, "projection_census": census, "alignment": sv_alignment_doc(),
            "heading_model": "equirect u = 0.5 + (compass - (base_heading + dheading_deg)) / 360 (pano.html)"}


def sv_save_alignment(b: dict) -> dict:
    """Validate one alignment and write sv_alignment.json atomically. Body: {capture_id, dE, dN, dheading_deg,
    height_m, dpitch_deg?, droll_deg?, fov_deg?, by, note?} or {capture_id, delete: true}. The previous value is kept in the
    entry's history (last 20)."""
    pid = b.get("capture_id")
    index = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
    if not isinstance(pid, str) or pid not in index or index[pid].get("projection") != "equirectangular":
        raise ValueError("capture_id must be an equirectangular panorama of pano_index.json")
    doc = sv_alignment_doc()
    if doc.get("schema") != SV_SCHEMA or not isinstance(doc.get("alignments"), dict):
        raise ValueError("sv_alignment.json has an unknown schema; not overwriting it")
    al = doc["alignments"]
    if b.get("delete"):
        al.pop(pid, None)
    else:
        rec = {}
        for k, lim in SV_LIMITS.items():
            v = b.get(k, 0.0)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or abs(v) > lim:
                raise ValueError("%s must be a number within +-%g" % (k, lim))
            rec[k] = round(float(v), 4)
        h = b.get("height_m")
        if not isinstance(h, (int, float)) or isinstance(h, bool) or not math.isfinite(h) or not 0.3 <= h <= 10.0:
            raise ValueError("height_m must be a number in 0.3..10")
        rec["height_m"] = round(float(h), 3)
        fov = b.get("fov_deg")  # optional horizontal lens of the side-by-side view that matched the photo
        if fov is not None:
            if not isinstance(fov, (int, float)) or isinstance(fov, bool) or not math.isfinite(fov) or not 30.0 <= fov <= 120.0:
                raise ValueError("fov_deg must be a number in 30..120")
            rec["fov_deg"] = round(float(fov), 2)
        by, note = b.get("by") or "operator", b.get("note") or ""
        if not isinstance(by, str) or not isinstance(note, str):
            raise ValueError("by and note must be strings")
        rec.update({"by": by[:80], "at": now(), "note": note[:500], "base_heading_deg": None})
        app = json.loads(APPEARANCE.read_text())["panoramas"].get(pid, {}).get("heading_offset_deg")
        rec["base_heading_deg"] = round((index[pid]["heading_deg"] + (app if isinstance(app, (int, float)) else 0.0)) % 360, 3)
        old = al.get(pid)
        if old:
            rec["history"] = ([{k: v for k, v in old.items() if k != "history"}] + old.get("history", []))[:20]
        al[pid] = rec
    doc["about"] = ("FL-4876 SV-OVERLAY-01: per-panorama alignment of the Mapillary equirectangular panoramas to the A3D "
                    "first-person camera. Camera at pano E+dE, N+dN, height_m above the terrain; photo heading = "
                    "base_heading_deg (pano_index compass + authored heading_offset_deg) + dheading_deg; dpitch/droll tilt "
                    "the panorama; fov_deg (optional) = horizontal lens of the side-by-side view that matched. Written by map_editor.py POST /api/sv/alignment.")
    doc["updated"] = now()
    json.loads(json.dumps(doc))  # serialisable before the atomic write
    write_json(SV_ALIGNMENT, doc)
    return doc


def rel(p: Path) -> str:
    """Repo-relative path string for records (absolute when outside the repo, e.g. a test scratch dir)."""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(Path(p).resolve())


def utm_to_ll(E, N):
    """Inverse UTM zone 54N (WGS84) -> (lon, lat); same series as fetch_aerial.py."""
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


def ll_to_utm(lon, lat):
    """Forward UTM zone 54N (WGS84) (lon, lat) -> (E, N); Snyder series, sub-centimetre over this scope."""
    k0, a, f = 0.9996, 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon - 141.0)
    Nn = a / math.sqrt(1 - e2 * math.sin(phi) ** 2); T = math.tan(phi) ** 2; C = ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * lam
    M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi) - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    E = 500000.0 + k0 * Nn * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120)
    N = k0 * (M + Nn * math.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24
                                        + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720))
    return E, N


class Projection:
    """Classic world <-> UTM, from the build's terrain_metadata.json (schema y92.classic_base_projection.v1).

    world = (E - origin_easting) * content_scale + offset (checked: home way/725138644 centroid maps to
    the baked footprint centroid of building_instances.json exactly)."""

    def __init__(self, build: Path):
        m = json.loads((build / "terrain_metadata.json").read_text())
        self.s, self.oe, self.on = m["content_scale"], m["origin_easting"], m["origin_northing"]
        self.dx, self.dy = m["offset_x"], m["offset_y"]

    def world(self, E, N):
        return (E - self.oe) * self.s + self.dx, (N - self.on) * self.s + self.dy

    def utm(self, x, y):
        return (x - self.dx) / self.s + self.oe, (y - self.dy) / self.s + self.on


def aerial_affine(pts):
    """Least-squares affine px = A @ [E, N, 1] mapping UTM to aerial-mosaic pixels.

    The mosaic is web-mercator at zoom 18; over this ~0.5 km scope mercator vs UTM differs from an
    affine map by far less than a pixel, so the client can draw the photo with one canvas transform.
    Returns the 2x3 rows (a, b, c), (d, e, f) with px = aE + bN + c, py = dE + eN + f."""
    m = json.loads((AERIAL / (MOSAIC + ".json")).read_text())
    n = 2 ** m["zoom"]
    rows, bx, by = [], [], []
    for E, N in pts:
        lon, lat = utm_to_ll(E, N)
        tx = (lon + 180) / 360 * n
        ty = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        rows.append((E, N)); bx.append((tx - m["tile_x0"]) * 256); by.append((ty - m["tile_y0"]) * 256)
    # normal equations on centred coordinates (keeps the 3x3 system well conditioned at UTM magnitudes)
    e0 = sum(r[0] for r in rows) / len(rows); n0 = sum(r[1] for r in rows) / len(rows)
    X = [(r[0] - e0, r[1] - n0) for r in rows]
    sxx = sum(x * x for x, _ in X); sxy = sum(x * y for x, y in X); syy = sum(y * y for _, y in X)
    det = sxx * syy - sxy * sxy

    def solve(b):
        b0 = sum(b) / len(b)
        sxb = sum(x * (v - b0) for (x, _), v in zip(X, b)); syb = sum(y * (v - b0) for (_, y), v in zip(X, b))
        a1 = (syy * sxb - sxy * syb) / det; a2 = (sxx * syb - sxy * sxb) / det
        return [a1, a2, b0 - a1 * e0 - a2 * n0]
    return {"x": solve(bx), "y": solve(by), "size": [m["tiles_x"] * 256, m["tiles_y"] * 256], "source": m["source"]}


def outward_bearing(ring, i):
    """Compass bearing (deg, 0 = N, clockwise) of the outward normal of footprint edge i (E/N ring)."""
    area = sum(ring[k][0] * ring[(k + 1) % len(ring)][1] - ring[(k + 1) % len(ring)][0] * ring[k][1] for k in range(len(ring)))
    (e0, n0), (e1, n1) = ring[i][:2], ring[(i + 1) % len(ring)][:2]
    de, dn = e1 - e0, n1 - n0
    # for a CCW ring (area > 0) the outward normal of edge p->q is the right-hand perpendicular (dn, -de)
    ne, nn = (dn, -de) if area > 0 else (-dn, de)
    return round(math.degrees(math.atan2(ne, nn)) % 360, 1)


def _inside(ring, E, N):
    """Ray-cast point-in-polygon on a footprint ring."""
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[i - 1][:2]
        if (y1 > N) != (y2 > N) and E < (x2 - x1) * (N - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _seg_dist(p, a, b):
    """Distance from point p to segment a-b (metres, E/N)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy or 1e-12
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


MAPS_AT = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)((?:,[-\d.]+[a-z])*)")


def parse_maps_url(u):
    """Google Maps URL -> {lat, lon, heading, pitch, fov, pano} or None.

    Street View URLs carry '@lat,lon,3a,<fov>y,<heading>h,<tilt>t' (tilt 90 = level, 0 = straight down);
    the panorama id is the '!1s<id>' token in the data= part. pitch is returned as tilt - 90 (0 = level)."""
    m = MAPS_AT.search(u or "")
    if not m:
        return None
    out = {"lat": float(m.group(1)), "lon": float(m.group(2)), "heading": None, "pitch": None, "fov": None, "pano": None}
    for tok in m.group(3).split(",")[1:]:
        v, k = tok[:-1], tok[-1]
        try:
            v = float(v)
        except ValueError:
            continue
        if k == "h":
            out["heading"] = v % 360
        elif k == "t":
            out["pitch"] = v - 90.0
        elif k == "y":
            out["fov"] = v
    p = re.search(r"!1s([A-Za-z0-9_-]{16,})", u)
    out["pano"] = p.group(1) if p else None
    return out


class Hub:
    """Server-sent-event fan-out: every /api/events client gets each published event (pose, queue)."""

    def __init__(self):
        import queue
        self._q, self._lock, self.last = queue, threading.Lock(), {}
        self.clients = []

    def subscribe(self):
        q = self._q.Queue(maxsize=200)
        with self._lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, kind, data):
        self.last[kind] = data
        with self._lock:
            for q in list(self.clients):
                try:
                    q.put_nowait((kind, data))
                except Exception:
                    pass  # a stalled client drops events rather than blocking the server


HUB = Hub()
CLIENT_LOG = []  # recent page log lines (see /api/client_log)


class World:
    """Static geometry of one build plus the live owner files (re-read on every /api/world)."""

    def __init__(self, build: Path):
        self.build = build
        canon = json.loads((build / "canonical.world_ir.json").read_text())
        self.buildings, self.roads = {}, []
        for f in canon["canonical_features"]:
            g = f.get("geometry") or {}
            if f.get("semantic_class") == "building_footprint" and g.get("kind") == "polygon":
                ring = [q[:2] for q in g["coordinates"]]
                if len(ring) > 1 and ring[0] == ring[-1]:
                    ring = ring[:-1]
                self.buildings[f["feature_id"]] = ring
            elif g.get("kind") == "polyline" and f.get("semantic_class") in ("road", "sidewalk", "railway_boundary", "crossing"):
                self.roads.append({"cls": f["semantic_class"], "pts": [q[:2] for q in g["coordinates"]]})
        self.objects = []
        for fam in ("lower_fixtures", "lower_linear", "lower_custom"):
            p = build / (fam + "_instances.json")
            if not p.is_file():
                continue
            for r in json.loads(p.read_text()):
                if not isinstance(r, dict) or r.get("E") is None:
                    continue
                aid = r.get("attachment_id") or r.get("inst_name")
                m = SF_RE.match(aid or "")
                self.objects.append({
                    "id": r.get("inst_name") or aid, "attachment_id": aid, "family": fam.replace("lower_", ""),
                    "E": round(r["E"], 2), "N": round(r["N"], 2), "mesh": r.get("mesh_name"),
                    "module": (r.get("metric_contract") or {}).get("asset_contract", {}).get("module_id") or r.get("module_id")
                    or r.get("semantic_family") or (r.get("mesh_name") or "").split("__")[0],
                    "evidence": r.get("evidence_kind"), "feature": r.get("feature_id"),
                    "fixture_id": m.group(1) if m else None})
        pts = [q for ring in self.buildings.values() for q in ring]
        self.aerial = aerial_affine(pts[:: max(1, len(pts) // 400)]) if (AERIAL / (MOSAIC + ".json")).is_file() else None
        self.proj = Projection(build)
        self._load_osm_names()
        self._layers = None

    def _load_osm_names(self):
        """Named OSM elements of the AOI extract (roads, shops, station...) for the location label.

        Ways become E/N polylines, nodes E/N points; only elements with a name tag are kept."""
        import xml.etree.ElementTree as ET
        self.named_lines, self.named_points = [], []
        osm = BUNDLE / "numabe_connected_neighborhood_aoi.osm"
        if not osm.is_file():
            return
        root = ET.parse(osm).getroot()
        nodes = {}
        for n in root.iter("node"):
            E, N = ll_to_utm(float(n.get("lon")), float(n.get("lat")))
            nodes[n.get("id")] = (E, N)
            tags = {t.get("k"): t.get("v") for t in n.iter("tag")}
            if tags.get("name"):
                self.named_points.append({"name": tags["name"], "kind": tags.get("shop") or tags.get("amenity") or tags.get("railway")
                                          or tags.get("highway") or "place", "E": E, "N": N, "osm": "node/" + n.get("id")})
        for w in root.iter("way"):
            tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
            if not tags.get("name"):
                continue
            pts = [nodes[r.get("ref")] for r in w.iter("nd") if r.get("ref") in nodes]
            if len(pts) < 2:
                continue
            row = {"name": tags["name"], "kind": tags.get("highway") or tags.get("building") or tags.get("railway") or tags.get("waterway")
                   or tags.get("landuse") or "way", "pts": pts, "osm": "way/" + w.get("id")}
            if tags.get("highway") or tags.get("railway") or tags.get("waterway"):
                self.named_lines.append(row)
            else:  # named building/area: treat its centroid as a point
                self.named_points.append({**row, "E": sum(p[0] for p in pts) / len(pts), "N": sum(p[1] for p in pts) / len(pts)})

    def where(self, x, y):
        """Location label for a classic world point: lat/lon, E/N, building, nearest named street and place."""
        E, N = self.proj.utm(x, y)
        lon, lat = utm_to_ll(E, N)
        road = min(((min(_seg_dist((E, N), a, b) for a, b in zip(r["pts"], r["pts"][1:])), r) for r in self.named_lines),
                   key=lambda t: t[0], default=(None, None))
        place = min(((math.hypot(p["E"] - E, p["N"] - N), p) for p in self.named_points), key=lambda t: t[0], default=(None, None))
        bid = self.building_at(E, N)
        return {"x": x, "y": y, "E": round(E, 2), "N": round(N, 2), "lat": round(lat, 7), "lon": round(lon, 7),
                "building_id": bid,
                "road": {"name": road[1]["name"], "kind": road[1]["kind"], "dist_m": round(road[0], 1)} if road[1] and road[0] < 60 else None,
                "place": {"name": place[1]["name"], "kind": place[1]["kind"], "dist_m": round(place[0], 1)} if place[1] and place[0] < 60 else None,
                "streetview_url": "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=%.7f,%.7f" % (lat, lon)}

    def layers(self):
        """Overlay layers for /a3d (classic world units): panoramas, authored fixtures (with review status),
        unresolved crops (manual placement debt joined to their annotation pose + box), building labels/colours,
        and the palette proposal swatches. Rebuilt on each call (owners may change)."""
        app = json.loads(APPEARANCE.read_text())
        pal = json.loads(PALETTE.read_text()).get("buildings", {}) if PALETTE.is_file() else {}
        P = self.proj
        panos = []
        for pid, p in json.loads((HERE / "pano_index.json").read_text())["panoramas"].items():
            off = app["panoramas"].get(pid, {}).get("heading_offset_deg")
            x, y = P.world(p["E"], p["N"])
            panos.append({"id": pid, "x": x, "y": y, "heading": (p["heading_deg"] + off) % 360 if isinstance(off, (int, float)) else None,
                          "compass": p["heading_deg"], "img": p.get("local_path")})
        fixtures = []
        for r in json.loads(FIXTURES.read_text())["fixtures"]:
            x, y = P.world(r["E"], r["N"])
            fixtures.append({"id": r["fixture_id"], "module": r.get("module_id"), "x": x, "y": y, "yaw": r.get("yaw_deg"),
                             "status": (r.get("manual_review") or {}).get("status") or "pending", "host": r.get("host_building_id")})
        # unresolved crops: debt rows -> annotation (camera pose, box) -> a bearing ray from the camera
        ann = {}
        for f in (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/annotations.json",
                  ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/annotations.json"):
            if f.is_file():
                for a in json.loads(f.read_text()).get("annotations") or []:
                    ann.setdefault(a["annotation_id"], (a, f.parent))
        crops = []
        debt = self.build / "observation_registration" / "manual_placement_debt.json"
        for r in (json.loads(debt.read_text())["rows"] if debt.is_file() else []):
            a, base = ann.get(r["annotation_id"], (None, None))
            if not a:
                continue
            cs = a.get("capture_snapshot") or {}
            if cs.get("lat") is None:
                continue
            E, N = ll_to_utm(cs["lon"], cs["lat"]); x, y = P.world(E, N)
            bx = a.get("box_norm") or [0.5, 0.5, 0, 0]
            hfov = cs.get("horizontal_fov_deg") or 60.0  # observation_debt default (FL-4809 OBSERVE-01)
            # perspective frame: bearing of the box centre = heading + atan((u - 0.5) * 2 tan(hfov/2))
            u = bx[0] + bx[2] / 2
            bearing = (cs.get("heading_deg") or 0) + math.degrees(math.atan((u - 0.5) * 2 * math.tan(math.radians(hfov / 2))))
            m = r.get("mapping") or {}
            crops.append({"id": r["annotation_id"], "capture": a.get("capture_id"), "x": x, "y": y, "bearing": bearing % 360,
                          "module": m.get("module_id") or m.get("carrier_type"), "why": m.get("evidence_summary"),
                          "crop": "/api/crop/" + r["annotation_id"]})
        blds = []
        for bid, ring in self.buildings.items():
            rec = app["buildings"].get(bid, {}); inf = pal.get(bid, {})
            cx, cy = P.world(sum(q[0] for q in ring) / len(ring), sum(q[1] for q in ring) / len(ring))
            blds.append({"id": bid, "x": cx, "y": cy, "wall": rec.get("wall_rgb") or inf.get("wall_rgb"), "roof": rec.get("roof_rgb"),
                         "src": "authored" if rec.get("wall_rgb") else (inf.get("tier") or "none")})
        sw = []
        pp = HERE / "palette_proposal" / "palette_proposal.json"
        if pp.is_file():
            for k, v in json.loads(pp.read_text()).get("swatches", {}).items():
                sw.append({"key": k, "label": v.get("label"), "role": v.get("role"), "hex": v.get("hex"), "ramp": v.get("ramp_hex")})
        return {"panoramas": panos, "fixtures": fixtures, "crops": crops, "buildings": blds, "palette": sw,
                "meta": {"content_scale": P.s, "build": rel(self.build)}}

    def crop_path(self, aid):
        """Crop image of an annotation (either capture-review crops dir)."""
        for d in ("fl4813_capture_review_20260915", "fl4810_aoi_street_network_boxing_20260915"):
            p = ROOT / "artifacts/experiments" / d / "crops" / (aid + ".png")
            if p.is_file():
                return p
        return None

    def building_at(self, E, N):
        """Footprint id containing the UTM point (ray-cast), else the nearest footprint within 3 m, else None."""
        best, bd = None, 3.0
        for bid, ring in self.buildings.items():
            inside = False
            for i in range(len(ring)):
                (x1, y1), (x2, y2) = ring[i], ring[i - 1]
                if (y1 > N) != (y2 > N) and E < (x2 - x1) * (N - y1) / (y2 - y1) + x1:
                    inside = not inside
            if inside:
                return bid
            for i in range(len(ring)):
                d = _seg_dist((E, N), ring[i], ring[(i + 1) % len(ring)])
                if d < bd:
                    best, bd = bid, d
        return best

    def front_camera(self, bid, dist_m=None):
        """Street-side camera for a building: the face whose outward normal reaches a road soonest.

        For each face, march along its outward normal from the face midpoint and take the distance to the
        nearest road/sidewalk polyline; the face with the smallest is the street frontage. The camera
        stands dist_m (default max(9 m, 1.2 x face length)) out along that normal, looking back at it."""
        ring = self.buildings[bid]
        roads = [r["pts"] for r in self.roads if r["cls"] in ("road", "sidewalk", "crossing")]
        best = None
        for i in range(len(ring)):
            p, q = ring[i], ring[(i + 1) % len(ring)]
            L = math.dist(p, q)
            if L < 1.5:
                continue
            b = math.radians(outward_bearing(ring, i)); ne, nn = math.sin(b), math.cos(b)
            mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            probe = (mid[0] + ne * 4, mid[1] + nn * 4)
            dr = min((_seg_dist(probe, a, c) for pl in roads for a, c in zip(pl, pl[1:])), default=99.0)
            score = dr - 0.05 * L  # prefer the longer face when two frontages are equally close
            if best is None or score < best[0]:
                best = (score, i, mid, ne, nn, L)
        _, i, mid, ne, nn, L = best
        d = dist_m or max(9.0, 1.2 * L)
        E, N = mid[0] + ne * d, mid[1] + nn * d
        x, y = self.proj.world(E, N)
        return {"building_id": bid, "face_index": i, "E": E, "N": N, "x": x, "y": y,
                "heading_deg": (math.degrees(math.atan2(-ne, -nn)) % 360), "target_E": mid[0], "target_N": mid[1],
                "target_x": self.proj.world(*mid)[0], "target_y": self.proj.world(*mid)[1]}

    def locate(self, q):
        """Resolve operator input to a camera: Google Maps URL, 'lat,lon', 'E N' (UTM), 'x y' (world),
        a building id or bare OSM way number, or a panorama id."""
        q = q.strip()
        sv = parse_maps_url(q)
        if sv:
            return self.pose_from_ll(sv)
        m = re.fullmatch(r"(?:feature:building_footprint:)?(?:way/)?(\d{5,})", q)
        if m or q.startswith("feature:building_footprint:"):
            bid = q if q.startswith("feature:") else "feature:building_footprint:way/" + m.group(1)
            if bid in self.buildings:
                return {"kind": "building", **self.front_camera(bid)}
        pid = q if q.startswith("mapillary_") else "mapillary_" + q
        pano = json.loads((HERE / "pano_index.json").read_text())["panoramas"].get(pid)
        if pano:
            off = json.loads(APPEARANCE.read_text())["panoramas"].get(pid, {}).get("heading_offset_deg")
            off = off if isinstance(off, (int, float)) else 0.0  # unaligned panoramas fall back to the compass
            x, y = self.proj.world(pano["E"], pano["N"])
            return {"kind": "panorama", "pano": pid, "E": pano["E"], "N": pano["N"], "x": x, "y": y,
                    "heading_deg": (pano["heading_deg"] + off) % 360, "img": pano.get("local_path")}
        # place names (OSM name tags): 'familymart', 'temple', 'lawson', or any part of a name (ファミリーマート, 東光院)
        alias = {"familymart": "ファミリーマート", "family mart": "ファミリーマート", "lawson": "ローソン", "temple": "@place_of_worship",
                 "tera": "@place_of_worship", "shrine": "神社", "station": "@stop", "numabe station": "@stop", "home": "725138644"}
        key = alias.get(q.lower(), q)
        if key == "725138644":
            return {"kind": "building", "name": "home 17-16", **self.front_camera("feature:building_footprint:way/725138644")}
        hits = [p for p in self.named_points if (key.startswith("@") and p["kind"] == key[1:]) or
                (not key.startswith("@") and key.lower() in p["name"].lower())]
        if hits:
            p = hits[0]
            bid = self.building_at(p["E"], p["N"])
            if bid:
                return {"kind": "building", "name": p["name"], **self.front_camera(bid)}
            x, y = self.proj.world(p["E"], p["N"])
            return {"kind": "place", "name": p["name"], "E": p["E"], "N": p["N"], "x": x, "y": y, "heading_deg": None}
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", q)]
        if len(nums) >= 2:
            a, b = nums[:2]
            if 30 < a < 40 and 135 < b < 145:
                return self.pose_from_ll({"lat": a, "lon": b, "heading": nums[2] if len(nums) > 2 else None})
            if a > 100000:
                x, y = self.proj.world(a, b)
                return {"kind": "utm", "E": a, "N": b, "x": x, "y": y, "heading_deg": nums[2] if len(nums) > 2 else None,
                        "building_id": self.building_at(a, b)}
            E, N = self.proj.utm(a, b)
            return {"kind": "world", "E": E, "N": N, "x": a, "y": b, "heading_deg": nums[2] if len(nums) > 2 else None,
                    "building_id": self.building_at(E, N)}
        raise ValueError("cannot locate %r (use a Google Maps URL, lat,lon, E N, x y, a building id or a panorama id)" % q)

    def pose_from_ll(self, sv):
        """Street View / lat-lon pose -> UTM + classic world; heading is compass degrees (0 = north)."""
        E, N = ll_to_utm(sv["lon"], sv["lat"])
        x, y = self.proj.world(E, N)
        return {"kind": "streetview" if sv.get("heading") is not None else "latlon", "lat": sv["lat"], "lon": sv["lon"],
                "E": E, "N": N, "x": x, "y": y, "heading_deg": sv.get("heading"), "pitch_deg": sv.get("pitch"),
                "fov_deg": sv.get("fov"), "pano": sv.get("pano"), "building_id": self.building_at(E, N)}

    def map_bundle(self):
        """The build's output.a3d plus every mesh as one binary: b'NMB1', u32 count, then per entry
        u16 name length, utf-8 name, u32 size, bytes (little endian). Entry 0 is output.a3d; meshes are
        named meshes/<file>. One request instead of ~3500 lets the wasm viewer fill MEMFS quickly."""
        import struct
        files = [("output.a3d", self.build / "output.a3d")] + [
            ("meshes/" + p.name, p) for p in sorted((self.build / "meshes").iterdir()) if p.is_file()]
        # RegisterMapSprites(a3d) reads <map>.sprites/ (sign faces); the train layer reads <map>.trains.json
        sp = self.build / "output.sprites"
        if sp.is_dir():
            files += [("output.sprites/" + p.name, p) for p in sorted(sp.iterdir()) if p.is_file()]
        if (self.build / "output.trains.json").is_file():
            files.append(("output.trains.json", self.build / "output.trains.json"))
        out = [b"NMB1", struct.pack("<I", len(files))]
        for name, p in files:
            nb, data = name.encode(), p.read_bytes()
            out += [struct.pack("<H", len(nb)), nb, struct.pack("<I", len(data)), data]
        return b"".join(out)

    def payload(self):
        """Everything /map draws; owner files are re-read so edits made elsewhere show on reload."""
        app = json.loads(APPEARANCE.read_text())
        pal = json.loads(PALETTE.read_text()).get("buildings", {}) if PALETTE.is_file() else {}
        fx = {r["fixture_id"]: r for r in json.loads(FIXTURES.read_text())["fixtures"]}
        pano_idx = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
        blds = []
        for bid, ring in self.buildings.items():
            rec = app["buildings"].get(bid, {}); inf = pal.get(bid, {})
            blds.append({"id": bid, "ring": [[round(e, 2), round(n, 2)] for e, n in ring],
                         "wall": rec.get("wall_rgb") or inf.get("wall_rgb"), "roof": rec.get("roof_rgb"),
                         "wall_src": "authored" if rec.get("wall_rgb") else (inf.get("tier") or "none"),
                         "photos": len(rec.get("operator_facade_photos") or [])})
        panos = []
        for pid, p in pano_idx.items():
            a = app["panoramas"].get(pid, {})
            off = a.get("heading_offset_deg")
            panos.append({"id": pid, "E": p["E"], "N": p["N"], "compass": p.get("heading_deg"),
                          "heading": round((p["heading_deg"] + off) % 360, 1) if isinstance(off, (int, float)) else None,
                          "img": p.get("local_path"), "seen": a.get("buildings_seen") or [],
                          "taken": p.get("timestamp")})
        objs = [dict(o, deleted=bool(o["fixture_id"]) and o["fixture_id"] not in fx) for o in self.objects]
        return {"build": rel(self.build), "buildings": blds, "roads": self.roads, "objects": objs,
                "panoramas": panos, "aerial": self.aerial, "queue": load_queue()["items"]}

    def sign_stage(self):
        """The build's sign-sprite owner (compile_sign_sprites) plus this build's baked terrain and metadata,
        loaded once (~2 s). Face geometry and sprite anchors come from the same functions the build runs."""
        if getattr(self, "_sign", None) is None:
            import sys
            for p in (str(ROOT / "docs" / "agent" / "cli-anything"), str(ROOT)):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from scripts.pipeline import compile_sign_sprites as C
            from scripts.pipeline.extruded_wall import Terrain
            self._sign = (C, Terrain(self.build / "output.a3d"),
                          json.loads((self.build / "terrain_metadata.json").read_text()))
        return self._sign

    def face(self, bid, i=None, x=None, y=None):
        """Face i of a building (or the face nearest world x,y): ends in world units, street level, eave,
        width/height in metres, outward bearing, and the building's accepted captures."""
        C, T, meta = self.sign_stage()
        ring = self.buildings[bid]
        if i is None:
            E, N = self.proj.utm(float(x), float(y))
            i = min(range(len(ring)), key=lambda k: _seg_dist((E, N), ring[k], ring[(k + 1) % len(ring)]))
        i = int(i) % len(ring)
        m = C.face_metrics(T, meta, ring, i)
        caps = [c for c in load_captures()["captures"] if c["building_id"] == bid and c["status"] == "accepted"]
        # the build's generic place-front meshes on this building (a capture may replace them)
        pf = self.build / "place_front_instances.json"
        fronts = []
        for r in (json.loads(pf.read_text()) if pf.is_file() else []):
            if r.get("parent_building_feature_id") == bid and r.get("inst_name"):
                nx, ny = r.get("front_outward_normal_xy") or [0, 0]
                fronts.append({"inst_name": r["inst_name"], "label": r.get("label"), "asset_match": r.get("asset_match"),
                               "fitted_width_m": r.get("fitted_width_m"), "bearing_deg": round(math.degrees(math.atan2(nx, ny)) % 360, 1),
                               "rendered_text": r.get("rendered_text")})
        return {"building_id": bid, "face_index": i, "faces": len(ring), **m, "captures": caps, "place_fronts": fronts}

    def band_sprites(self, bid, i, bands):
        """XP bytes (base64) + build position for each sprite band, via compile_sign_sprites.xp_bytes and
        capture_anchor, so the live preview is byte- and position-identical to the next build."""
        import base64
        C, T, meta = self.sign_stage()
        ring = self.buildings[bid]
        out = []
        for b in bands:
            cells = clean_cells(b["cells"], C)
            pos, rec = C.capture_anchor(T, meta, ring, int(i), (float(b["x0_m"]) + float(b["x1_m"])) / 2, float(b["y0_m"]),
                                        b.get("out_m"))
            out.append({"pos": pos, "wall_snap": rec, "cells": [len(cells[0]), len(cells)],
                        "xp": base64.b64encode(C.xp_bytes(cells)).decode()})
        return out

    def captures_live(self):
        """Accepted captures with their sprite bands resolved (bytes + position) for re-injection on load."""
        import base64
        caps = []
        for c in load_captures()["captures"]:
            if c["status"] != "accepted" or c["building_id"] not in self.buildings:
                continue
            C, T, meta = self.sign_stage()
            sprites = []
            for k, b in enumerate(c.get("bands", [])):
                p = CAPTURES.parent / (b.get("sprite") or "")
                if b.get("mode") == "sprite" and p.is_file():
                    pos, _ = C.capture_anchor(T, meta, self.buildings[c["building_id"]], int(c["face_index"]),
                                              (float(b["x0_m"]) + float(b["x1_m"])) / 2, float(b["y0_m"]), b.get("out_m"))
                    if pos:
                        sprites.append({"band": k, "pos": pos, "xp": base64.b64encode(p.read_bytes()).decode()})
            caps.append({"capture_id": c["capture_id"], "building_id": c["building_id"], "face_index": c["face_index"],
                         "sprites": sprites, "replaces_place_fronts": c.get("replaces_place_fronts") or []})
        return {"captures": caps}

    def building_record(self, bid):
        """Full appearance + palette record for /view."""
        app = json.loads(APPEARANCE.read_text())
        pal = json.loads(PALETTE.read_text()).get("buildings", {}) if PALETTE.is_file() else {}
        ring = self.buildings.get(bid) or []
        faces = [{"index": i, "bearing": outward_bearing(ring, i),
                  "length_m": round(math.dist(ring[i], ring[(i + 1) % len(ring)]), 2)} for i in range(len(ring))]
        return {"id": bid, "appearance": app["buildings"].get(bid), "inferred": pal.get(bid), "faces": faces}

    def address(self, bid):
        """Best postal address for a building, with its precision stated:
          'listing'  - a business/place on this building has a GSI-geocoded 号-level listing address;
          'go_point' / 'go_point_within_3m' - a GSI 番-号 residence-indication point inside / within 3 m of it;
          'go_point_nearby' - inferred: the nearest 番-号 point 3-15 m away;
          'block'    - inferred: town from the GSI reverse geocoder (40 m grid, address_blocks.json) and the
                       nearest GSI block (番) representative point of that town; 号 unknown;
          'town'     - town only (no block point in that town)."""
        ring = self.buildings.get(bid)
        if not ring:
            return {"building_id": bid, "error": "unknown building"}
        cE, cN = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
        pos = json.loads((BUNDLE / "authored_observation_positions.json").read_text())
        exact = []
        for b in pos.get("business_placements") or []:
            g = b.get("address_geocode") or {}
            if b.get("host_building_id") == bid and g.get("address"):
                exact.append({"address": g["address"], "name": g.get("listing_name") or b.get("business_key"),
                              "source": "listing, GSI geocoded"})
        for m in pos.get("mapped_place_addresses") or []:
            if m.get("address") and self.building_at(m["E"], m["N"]) == bid:
                if not any(e["address"] == m["address"] for e in exact):
                    exact.append({"address": m["address"], "name": m.get("listing_name"), "source": "listing, GSI geocoded"})
        blocks_doc = json.loads(ADDRESS_BLOCKS.read_text()) if ADDRESS_BLOCKS.is_file() else {"towns": {}, "blocks": []}
        muni = blocks_doc.get("muni") or "東京都大田区"
        town = blocks_doc["towns"].get("%d,%d" % (round(cE / 40), round(cN / 40)))
        near = sorted(((math.hypot(b["E"] - cE, b["N"] - cN), b) for b in blocks_doc["blocks"] if b["town"] == town),
                      key=lambda t: t[0])
        out = {"building_id": bid, "centroid_EN": [round(cE, 2), round(cN, 2)], "town": town, "exact": exact}
        # residence-indication points (号). Measured against the 9 listing addresses: points inside the footprint or
        # within 3 m of its outline contain the listing number 9/9; exclusive nearest-footprint assignment fell to 7/9
        # (neighbours took the points), so it is not used. Otherwise the nearest point within 15 m, labelled inferred.
        near_go = []
        for g in blocks_doc.get("go") or []:
            if abs(g["E"] - cE) > 80 or abs(g["N"] - cN) > 80:
                continue
            d = 0.0 if _inside(ring, g["E"], g["N"]) else min(
                _seg_dist((g["E"], g["N"]), ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
            if d <= 15.0:
                near_go.append((d, "%s%s%d-%d" % (muni, g["town"], g["block"], g["go"])))
        near_go.sort()
        on = [a for d, a in near_go if d <= 3.0]
        if on:
            out["go_points"] = on
        if not exact and near_go:
            d0, a0 = near_go[0]
            out.update(address=a0, go_point_dist_m=round(d0, 1),
                       precision="go_point" if d0 == 0 else "go_point_within_3m" if d0 <= 3 else "go_point_nearby")
            if len(near_go) > 1:
                out["alternatives"] = ["%s (%.0f m)" % (a, d) for d, a in near_go[1:4]]
            return out
        if exact:
            out.update(address=exact[0]["address"], precision="listing")
        elif near:
            d, b = near[0]
            out.update(address="%s%s%d番" % (muni, town, b["block"]), precision="block",
                       block=b["block"], block_point_dist_m=round(d, 1),
                       alternatives=["%s%s%d番 (%.0f m)" % (muni, town, x["block"], dd) for dd, x in near[1:3]])
        elif town:
            out.update(address=muni + town, precision="town")
        else:
            out.update(address=None, precision="none")
        return out


def load_queue():
    """Operator queue owner; created on first use."""
    if QUEUE.is_file():
        return json.loads(QUEUE.read_text())
    return {"schema": "y92.fl4876.operator_map_queue.v1",
            "owner": "artifacts/experiments/fl4876_manual_world/map_editor.py (operator intent; the agent resolves items)",
            "items": []}


def load_captures():
    """FACADE-CAPTURE-01 owner; created on the first accepted capture."""
    if CAPTURES.is_file():
        return json.loads(CAPTURES.read_text())
    return {"schema": "y92.operator_facade_captures.v1",
            "owner": "artifacts/experiments/fl4876_manual_world/map_editor.py writes (operator facade capture in the "
                     "a3d editor); scripts/pipeline/compile_sign_sprites.py FACADE-CAPTURE-01 reads",
            "purpose": "Operator-rectified facade photos on one footprint face each. Sprite bands carry the CP437 "
                       "cells the operator accepted (operator_facade_sprites/*.xp, placed by capture_anchor); colour "
                       "bands with role wall/trim set authored_building_appearance <role>_rgb (journalled).",
            "source_kinds": SOURCE_KINDS, "captures": []}


def clean_cells(cells, C):
    """Validate an operator cell grid: rows top first, each cell [glyph, [r,g,b], [r,g,b]]; colours snapped to
    the Classic 6-level cube (never the XP transparency key)."""
    # The .xp header cell sits in column 1 of layer 0 (xp_bytes), so a sprite needs >= 2 columns
    # (FACADE-CAPTURE-REVIEW-20261014 P2: a 1-column grid used to reach xp_bytes and raise IndexError).
    if not cells or not cells[0] or len(cells) > 36 or not 2 <= len(cells[0]) <= 128:
        raise ValueError("cells must be 1..36 rows (engine height digits 0-9,A-Z) x 2..128 columns")
    w = len(cells[0])
    out = []
    for row in cells:
        if len(row) != w:
            raise ValueError("ragged cell rows")
        out.append([_clean_cell(c, C) for c in row])
    if all(c[2] == C.KEY and c[1] == (0, 0, 0) and c[0] == 0 for row in out for c in row):
        raise ValueError("every cell is transparent")
    return out


def fetch_image(url):
    """An image the operator dragged in as a link (Google Images in a split-view tab hands the page a URL, whose
    pixels a page cannot read cross-origin). Fetched once, here, and returned as a data: URL. http(s) only,
    image content types only, at most 15 MB."""
    import base64
    import urllib.request
    if not re.match(r"^https?://", url or ""):
        return 400, {"error": "only http(s) image links"}
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (numabe map editor)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            raw = r.read((15 << 20) + 1)
    except Exception as e:  # network/HTTP failure is reported to the page, not raised
        return 502, {"error": "fetch failed: %s" % e}
    if len(raw) > 15 << 20:
        return 413, {"error": "image larger than 15 MB"}
    if not ctype.startswith("image/"):
        return 415, {"error": "not an image (%s)" % (ctype or "no content type")}
    return 200, {"data_url": "data:%s;base64,%s" % (ctype, base64.b64encode(raw).decode()), "url": url, "bytes": len(raw)}


def _clean_cell(c, C):
    """One cell. A logo cell may be null (fully transparent: glyph 0 on the XP magenta key) or have a null
    background (a half block whose other half is transparent: the engine keys each colour component against
    layer 0's key, engine/sprite.cpp). Any real colour is snapped to the 6-level cube, never the key."""
    if c is None:
        return (0, (0, 0, 0), C.KEY)
    g, fg, bg = c
    return (max(1, min(255, int(g))), C._snap(fg), C.KEY if bg is None else C._snap(bg))


def save_image(data_url, folder):
    """Content-addressed store of a data: URL image; returns the repo-relative path."""
    import base64
    head, data = data_url.split(",", 1)
    raw = base64.b64decode(data)
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(head[5:].split(";")[0], ".png")
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (hashlib.sha256(raw).hexdigest()[:16] + ext)
    if not p.exists():
        p.write_bytes(raw)
    return rel(p)


def apply_colour(bid, role, rgb, swatch, by, capture_id=None):
    """Write buildings[bid].<role>_rgb in the appearance owner with an operator decision record and a journal
    line; returns the previous value."""
    app = json.loads(APPEARANCE.read_text())
    rec = app["buildings"].setdefault(bid, {})
    prev = rec.get(role + "_rgb")
    rec[role + "_rgb"] = rgb
    rec.setdefault("operator_colour_decisions", []).append(
        {"role": role, "rgb": rgb, "swatch": swatch, "previous": prev, "by": by, "at": now(),
         **({"capture_id": capture_id} if capture_id else {})})
    write_json(APPEARANCE, app)
    REVIEW.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now(), "op": "set_colour", "building_id": bid, "role": role, "rgb": rgb,
                             "previous": prev, "swatch": swatch, "capture_id": capture_id}) + "\n")
    return prev


def add_queue_item(item):
    """Append one operator item (note / facade_paste / delete_request) with a stable id."""
    q = load_queue()
    n = 1 + max([int(i["id"].split("-")[-1]) for i in q["items"]] or [0])
    item = {"id": "OPQ-4876-%04d" % n, "created": now(), "status": "open", **item}
    q["items"].append(item)
    REVIEW.mkdir(parents=True, exist_ok=True)
    write_json(QUEUE, q)
    HUB.publish("queue", {"id": item["id"], "kind": item["kind"]})
    return item


class Handler(BaseHTTPRequestHandler):
    """Routes: pages, /api/* JSON, /img (repo-local images only)."""
    world: World = None

    def log_message(self, fmt, *args):  # keep the terminal quiet; owners carry the record
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlsplit(self.path); q = parse_qs(u.query)
        try:
            if u.path == "/":  # the A3D editor is the front page (the top-down plan page was rejected by the operator)
                return self.send(200, (HERE / "map_editor_ui" / "a3d.html").read_text(), "text/html")
            if u.path == "/map":
                return self.send(200, (HERE / "map_editor_ui" / "map.html").read_text(), "text/html")
            if u.path == "/view":
                return self.send(200, (HERE / "map_editor_ui" / "view.html").read_text(), "text/html")
            if u.path == "/pano":
                return self.send(200, (HERE / "map_editor_ui" / "pano.html").read_text(), "text/html")
            if u.path == "/a3d":
                return self.send(200, (HERE / "map_editor_ui" / "a3d.html").read_text(), "text/html")
            if u.path.startswith("/classic/") or u.path.startswith("/fonts/"):
                base = (ROOT / ".web-classic") if u.path.startswith("/classic/") else (ROOT / "assets/fonts")
                p = (base / u.path.split("/", 2)[2]).resolve()
                if base.resolve() in p.parents and p.is_file():
                    ct = {".wasm": "application/wasm", ".js": "text/javascript", ".png": "image/png", ".data": "application/octet-stream",
                          ".html": "text/html"}.get(p.suffix, "application/octet-stream")
                    return self.send(200, p.read_bytes(), ct)
            if u.path == "/api/map_bundle":
                # MAP-EDIT-01: a saved hand edit (a3d_edit.py) changes output.a3d / meshes; rebuild the bundle then
                _stamp = map_stamp(self.world.build)
                if getattr(self.world, "_bundle_stamp", None) != _stamp:
                    self.world._bundle = self.world.map_bundle()
                    self.world._bundle_stamp = _stamp
                return self.send(200, self.world._bundle, "application/octet-stream")
            if u.path == "/api/locate":
                return self.send(200, self.world.locate(q["q"][0]))
            if u.path == "/api/building_at":
                if "x" in q:
                    E, N = self.world.proj.utm(float(q["x"][0]), float(q["y"][0]))
                else:
                    E, N = float(q["E"][0]), float(q["N"][0])
                bid = self.world.building_at(E, N)
                return self.send(200, {"E": E, "N": N, "building_id": bid})
            if u.path == "/api/client_log":
                return self.send(200, CLIENT_LOG[-200:])
            if u.path == "/api/bookmarks":
                return self.send(200, json.loads(BOOKMARKS.read_text()) if BOOKMARKS.is_file() else {"bookmarks": []})
            if u.path == "/api/where":
                return self.send(200, self.world.where(float(q["x"][0]), float(q["y"][0])))
            if u.path == "/api/layers":
                return self.send(200, self.world.layers())
            if u.path == "/api/sv":  # street-view overlay: equirect panoramas + saved alignments
                return self.send(200, sv_panoramas(self.world))
            if u.path.startswith("/api/crop/"):
                p = self.world.crop_path(u.path.rsplit("/", 1)[1])
                if p:
                    return self.send(200, p.read_bytes(), "image/png")
            if u.path == "/api/pose":
                return self.send(200, HUB.last.get("pose") or {})
            if u.path == "/api/events":
                return self.events()
            if u.path.startswith("/ui/"):
                p = (HERE / "map_editor_ui" / u.path[4:]).resolve()
                if p.parent == (HERE / "map_editor_ui").resolve() and p.suffix in (".js", ".css"):
                    return self.send(200, p.read_text(), "text/javascript" if p.suffix == ".js" else "text/css")
            if u.path == "/api/world":
                return self.send(200, self.world.payload())
            if u.path.startswith("/api/ev/"):  # editor_views.py: first-person / top-down / reference-map data
                import editor_views
                return self.send(*editor_views.route(self.world, u.path[8:], q, ll_to_utm, BUNDLE / "numabe_connected_neighborhood_aoi.osm"))
            if u.path == "/api/building":
                return self.send(200, self.world.building_record(q["id"][0]))
            if u.path == "/api/address":
                return self.send(200, self.world.address(q["building"][0]))
            if u.path == "/api/fetch_image":
                return self.send(*fetch_image(q["url"][0]))
            if u.path == "/api/face":
                return self.send(200, self.world.face(q["building"][0], q["face"][0] if "face" in q else None,
                                                      (q.get("x") or [None])[0], (q.get("y") or [None])[0]))
            if u.path == "/api/facade_captures":
                return self.send(200, self.world.captures_live())
            if u.path == "/api/queue":
                return self.send(200, load_queue())
            if u.path == "/aerial.jpg":
                return self.send(200, (AERIAL / (MOSAIC + ".jpg")).read_bytes(), "image/jpeg")
            if u.path == "/img":
                p = (ROOT / q["path"][0]).resolve()
                if ROOT in p.parents and p.suffix.lower() in IMG_EXT and p.is_file():
                    return self.send(200, p.read_bytes(), IMG_EXT[p.suffix.lower()])
            return self.send(404, {"error": "not found"})
        except Exception as e:  # report to the page, never crash the server
            return self.send(500, {"error": repr(e)})

    def do_POST(self):
        u = urlsplit(self.path)
        try:
            b = self.body()
            with LOCK:
                if u.path == "/api/client_log":
                    # page boot/progress/error lines, kept in memory so the agent can see the tab state
                    # without a browser bridge (diagnostic only; not an owner record)
                    CLIENT_LOG.append({"at": now(), **{k: str(v)[:2000] for k, v in b.items()}})
                    del CLIENT_LOG[:-500]
                    return self.send(200, {"ok": True})
                if u.path == "/api/note":
                    return self.send(200, self.note(b))
                if u.path == "/api/bookmarks":
                    # operator-named camera locations (operator_map_review/bookmarks.json)
                    bm = json.loads(BOOKMARKS.read_text()) if BOOKMARKS.is_file() else {"schema": "y92.fl4876.operator_bookmarks.v1", "bookmarks": []}
                    if b.get("delete"):
                        bm["bookmarks"] = [x for x in bm["bookmarks"] if x["name"] != b["delete"]]
                    else:
                        E, N = self.world.proj.utm(b["camera"]["x"], b["camera"]["y"])
                        lon, lat = utm_to_ll(E, N)
                        bm["bookmarks"] = [x for x in bm["bookmarks"] if x["name"] != b["name"]] + [
                            {"name": b["name"][:120], "camera": b["camera"], "E": E, "N": N, "lat": lat, "lon": lon, "at": now()}]
                    REVIEW.mkdir(parents=True, exist_ok=True); write_json(BOOKMARKS, bm)
                    return self.send(200, bm)
                if u.path == "/api/sv/alignment":
                    try:
                        return self.send(200, sv_save_alignment(b))
                    except ValueError as e:
                        return self.send(400, {"error": str(e)})
                if u.path == "/api/set_colour":
                    return self.send(200, self.set_colour(b))
                if u.path == "/api/place_crop":
                    E, N = self.world.proj.utm(b["world"][0], b["world"][1])
                    return self.send(200, add_queue_item({
                        "kind": "crop_placement", "annotation_ids": [b["annotation_id"]], "module": b.get("module"),
                        "world_points": [b["world"]], "points_EN": [[E, N, b["world"][2] if len(b["world"]) > 2 else None]],
                        "building_ids": [x for x in [self.world.building_at(E, N)] if x], "yaw_deg": b.get("yaw_deg"),
                        "text": (b.get("text") or "") or "operator placed this unresolved crop here", "camera": b.get("camera")}))
                if u.path == "/api/streetview_pose":
                    sv = parse_maps_url(b.get("url", "")) if b.get("url") else b
                    if not sv or sv.get("lat") is None:
                        return self.send(400, {"error": "no @lat,lon in that URL"})
                    pose = {**self.world.pose_from_ll(sv), "url": b.get("url"), "at": now()}
                    HUB.publish("pose", pose)
                    return self.send(200, pose)
                if u.path == "/api/delete":
                    return self.send(200, self.delete(b["objects"]))
                if u.path == "/api/undo":
                    return self.send(200, self.undo())
                if u.path == "/api/facade":
                    return self.send(200, self.facade(b))
                if u.path == "/api/facade_preview":
                    return self.send(200, {"bands": self.world.band_sprites(b["building_id"], b["face_index"], b.get("bands") or [])})
                if u.path == "/api/facade_capture":
                    return self.send(200, self.capture(b))
                if u.path == "/api/facade_capture/retract":
                    return self.send(200, self.retract(b))
                if u.path == "/api/queue/status":
                    q = load_queue()
                    for it in q["items"]:
                        if it["id"] == b["id"]:
                            it["status"] = b["status"]; it["status_changed"] = now()
                    write_json(QUEUE, q)
                    return self.send(200, q)
            return self.send(404, {"error": "not found"})
        except Exception as e:
            return self.send(500, {"error": repr(e)})

    def events(self):
        """Server-sent events: 'pose' (Street View relay) and 'queue' (operator queue changed)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = HUB.subscribe()
        try:
            if "pose" in HUB.last:
                self.wfile.write(("event: pose\ndata: %s\n\n" % json.dumps(HUB.last["pose"])).encode()); self.wfile.flush()
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    self.wfile.write(("event: %s\ndata: %s\n\n" % (kind, json.dumps(data))).encode())
                except Exception as e:
                    if isinstance(e, (BrokenPipeError, ConnectionResetError)):
                        raise
                    self.wfile.write(b": keepalive\n\n")  # queue.Empty: keep the connection open
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def note(self, b):
        """Operator note from /map (plan rect) or /a3d (world points + camera + optional screenshot/image).

        Images (the frame with the operator's drawing, and/or a dropped reference image such as a logo)
        are stored content-addressed under operator_map_review/shots/ and referenced from the queue item."""
        import base64
        shots = {}
        for key in ("shot", "image"):
            if b.get(key):
                head, data = b[key].split(",", 1)
                raw = base64.b64decode(data)
                ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(head[5:].split(";")[0], ".png")
                d = REVIEW / "shots"; d.mkdir(parents=True, exist_ok=True)
                p = d / (hashlib.sha256(raw).hexdigest()[:16] + ext)
                if not p.exists():
                    p.write_bytes(raw)
                shots[key] = rel(p)
        if b.get("image_url") and "image" not in shots:
            # operator dragged an <img> from another tab: its src is a remote URL the page cannot read
            # (CORS), so the server fetches that one image (http/https, image/* only, <= 15 MB)
            import urllib.request
            u = b["image_url"]
            if not re.match(r"^https?://", u):
                raise ValueError("image_url must be http(s)")
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (numabe map editor; operator drag)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                ct = (r.headers.get("Content-Type") or "").split(";")[0]
                raw = r.read(15 << 20)
            if not ct.startswith("image/"):
                raise ValueError("dragged URL is not an image (%s)" % ct)
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(ct, ".img")
            d = REVIEW / "shots"; d.mkdir(parents=True, exist_ok=True)
            p = d / (hashlib.sha256(raw).hexdigest()[:16] + ext)
            if not p.exists():
                p.write_bytes(raw)
            shots["image"] = rel(p); shots["image_source_url"] = u
        world = b.get("world") or []  # [[x, y, z], ...] classic world units, from the viewer's pick
        en = [list(self.world.proj.utm(w[0], w[1])) + [w[2] if len(w) > 2 else None] for w in world]
        bids = list(b.get("building_ids") or [])
        for e in en:
            bid = self.world.building_at(e[0], e[1])
            if bid and bid not in bids:
                bids.append(bid)
        return add_queue_item({"kind": b.get("kind") or "note", "text": (b.get("text") or "")[:4000], "rect_EN": b.get("rect"),
                               "building_ids": bids, "object_ids": b.get("object_ids") or [],
                               "panorama_ids": b.get("panorama_ids") or [], "world_points": world, "points_EN": en,
                               "camera": b.get("camera"), "label": b.get("label"), **shots})

    def set_colour(self, b):
        """Operator colour decision from the a3d palette: writes buildings[id].<role>_rgb in the appearance
        owner (with an operator judgement record), journals the previous value (undo), and queues a record."""
        role = b.get("role", "wall")
        if role not in ("wall", "roof", "trim"):
            raise ValueError("role must be wall/roof/trim")
        rgb = [int(v) for v in b["rgb"]][:3]
        apply_colour(b["building_id"], role, rgb, b.get("swatch"), "operator via a3d editor")
        return add_queue_item({"kind": "colour_decision", "status": "applied", "building_ids": [b["building_id"]],
                               "text": "%s set to %s (%s); takes effect on the next build" % (role, rgb, b.get("swatch") or "custom")})

    def delete(self, objs):
        """Authored fixtures are removed from their owner (journalled); other objects become requests."""
        doc = json.loads(FIXTURES.read_text())
        removed, queued = [], []
        for o in objs:
            fid = o.get("fixture_id")
            at = next((k for k, r in enumerate(doc["fixtures"]) if fid and r["fixture_id"] == fid), None)
            if at is not None:
                # keep the original index so undo restores the owner byte-identically (rows are not sorted)
                removed.append({"index": at, "row": doc["fixtures"].pop(at)})
            else:
                queued.append(o)
        if removed:
            write_json(FIXTURES, doc)
            REVIEW.mkdir(parents=True, exist_ok=True)
            with JOURNAL.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": now(), "op": "delete_fixtures", "by": "operator via map_editor",
                                     "owner": rel(FIXTURES), "rows": removed}, ensure_ascii=False) + "\n")
        item = add_queue_item({"kind": "delete_request", "objects": queued,
                               "text": "operator deleted these generated objects in the map editor; route each to its owner"}) if queued else None
        return {"removed": [r["row"]["fixture_id"] for r in removed], "queued": item}

    def undo(self):
        """Restore the rows of the most recent un-undone delete_fixtures journal entry."""
        if not JOURNAL.is_file():
            return {"restored": []}
        lines = [json.loads(x) for x in JOURNAL.read_text().splitlines() if x.strip()]
        undone = sum(1 for x in lines if x["op"] == "undo")
        dels = [x for x in lines if x["op"] == "delete_fixtures"]
        if undone >= len(dels):
            return {"restored": []}
        ent = dels[len(dels) - 1 - undone]
        doc = json.loads(FIXTURES.read_text())
        have = {r["fixture_id"] for r in doc["fixtures"]}
        back = [r for r in ent["rows"] if r["row"]["fixture_id"] not in have]
        # rows were popped in ascending selection order with indices taken after earlier pops, so
        # re-inserting in reverse pop order at the recorded index rebuilds the original sequence
        for r in reversed(back):
            doc["fixtures"].insert(min(r["index"], len(doc["fixtures"])), r["row"])
        write_json(FIXTURES, doc)
        ids = [r["row"]["fixture_id"] for r in back]
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now(), "op": "undo", "restored": ids}) + "\n")
        return {"restored": ids}

    def capture(self, b):
        """FACADE-CAPTURE-01 accept: archive the source and rectified images, write each sprite band's cells as
        an .xp beside the captures owner, apply wall/trim colour bands to the appearance owner (journalled),
        record the capture (superseding an earlier one on the same face), close the originating queue item."""
        C, _, _ = self.world.sign_stage()
        bid, face = b["building_id"], int(b["face_index"])
        if bid not in self.world.buildings:
            raise ValueError("unknown building " + bid)
        src = b.get("source") or {}
        kind = src.get("kind") or "other"
        if kind not in SOURCE_KINDS:
            raise ValueError("source kind must be one of " + ", ".join(SOURCE_KINDS))
        if src.get("data_url"):
            src_path = save_image(src["data_url"], FACADES)
        else:
            p = (ROOT / (src.get("path") or "")).resolve()
            if REVIEW.resolve() not in p.parents or not p.is_file():
                raise ValueError("source image must be pasted or an operator_map_review image")
            src_path = rel(p)
        doc = load_captures()
        cid = "FCAP-4876-%04d" % (1 + max([int(c["capture_id"].split("-")[-1]) for c in doc["captures"]] or [0]))
        bands, colours = [], []
        for k, band in enumerate(b.get("bands") or []):
            rec = {key: band.get(key) for key in ("kind", "mode", "label", "x0_m", "x1_m", "y0_m", "y1_m", "rgb", "encoding", "out_m")
                   if band.get(key) is not None}
            if band.get("mode") == "sprite":
                cells = clean_cells(band["cells"], C)
                raw = C.xp_bytes(cells)
                CAPTURE_SPRITES.mkdir(parents=True, exist_ok=True)
                p = CAPTURE_SPRITES / ("%s_%d_%s_%s.xp" % (cid, k, band.get("kind") or "band", hashlib.sha256(raw).hexdigest()[:10]))
                p.write_bytes(raw)
                rec.update(sprite=str(p.relative_to(CAPTURES.parent)), cells=[len(cells[0]), len(cells)])
            elif band.get("mode") == "colour" and band.get("kind") in ("wall", "trim") and band.get("rgb"):
                rgb = [int(v) for v in band["rgb"]][:3]
                prev = apply_colour(bid, band["kind"], rgb, "facade capture " + cid, "operator via a3d facade capture", cid)
                colours.append({"role": band["kind"], "rgb": rgb, "previous": prev})
            bands.append(rec)
        supersedes = []
        itype = "logo" if b.get("image_type") == "logo" else "photo"
        for c in doc["captures"]:
            # a new facade photo replaces the face's earlier photo capture; a logo/graphic replaces an earlier
            # logo; the two kinds stack (a photographed front plus a clean logo over its fascia)
            if (c["building_id"] == bid and int(c["face_index"]) == face and c["status"] == "accepted"
                    and (c.get("image_type") or "photo") == itype):
                c["status"] = "superseded"; c["superseded_by"] = cid
                supersedes.append(c["capture_id"])
        cap = {"capture_id": cid, "building_id": bid, "face_index": face, "status": "accepted", "at": now(),
               "supersedes": supersedes, "image_type": itype,
               "by": "operator via a3d facade capture", "face": b.get("face"),
               "source": {"image": src_path, "kind": kind, "kind_meaning": SOURCE_KINDS[kind], "url": src.get("url"),
                          "redistributable": kind not in ("google_streetview_reference", "web_image_reference")},
               "rectified": save_image(b["rectified_data_url"], FACADES) if b.get("rectified_data_url") else None,
               "corners_px": b.get("corners_px"), "bands": bands, "objects": b.get("objects") or [],
               "replaces_place_fronts": [str(x) for x in (b.get("replaces_place_fronts") or [])],
               "colours_applied": colours, "note": (b.get("text") or "")[:4000], "queue_item": b.get("queue_item")}
        doc["captures"].append(cap)
        write_json(CAPTURES, doc)
        app = json.loads(APPEARANCE.read_text())
        app["buildings"].setdefault(bid, {}).setdefault("operator_facade_photos", []).append(
            {"path": src_path, "pasted_on": now(), "by": "operator via a3d facade capture", "face_index": face,
             "face_bearing_deg": outward_bearing(self.world.buildings[bid], face), "status": "captured", "capture_id": cid})
        write_json(APPEARANCE, app)
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now(), "op": "facade_capture", "capture_id": cid, "building_id": bid,
                                 "face_index": face, "bands": len(bands), "colours": colours}) + "\n")
        q = load_queue()
        for it in q["items"]:
            if b.get("queue_item") and it["id"] == b["queue_item"]:
                it["status"] = "resolved"; it["status_changed"] = now(); it["resolution"] = "facade capture " + cid
        write_json(QUEUE, q)
        if cap["objects"]:  # things in front of the facade (vending machines, bikes...) need fixtures: agent work
            add_queue_item({"kind": "facade_objects", "building_ids": [bid], "capture_id": cid, "objects": cap["objects"],
                            "text": "objects the operator boxed on %s face %d; place each as a fixture" % (bid.split(":")[-1], face)})
        HUB.publish("queue", {"id": cid, "kind": "facade_capture"})
        return cap

    def retract(self, b):
        """Withdraw an accepted capture; its colours revert when the owner still holds the capture's value."""
        doc = load_captures()
        cap = next((c for c in doc["captures"] if c["capture_id"] == b["capture_id"]), None)
        if not cap or cap["status"] != "accepted":
            raise ValueError("no accepted capture " + b["capture_id"])
        cap["status"] = "retracted"; cap["retracted_at"] = now()
        write_json(CAPTURES, doc)
        app = json.loads(APPEARANCE.read_text())
        rec = app["buildings"].get(cap["building_id"], {})
        reverted = []
        for c in cap.get("colours_applied", []):
            if rec.get(c["role"] + "_rgb") == c["rgb"]:
                if c["previous"] is None:
                    rec.pop(c["role"] + "_rgb", None)
                else:
                    rec[c["role"] + "_rgb"] = c["previous"]
                reverted.append(c["role"])
        write_json(APPEARANCE, app)
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now(), "op": "facade_capture_retract", "capture_id": cap["capture_id"],
                                 "reverted_colours": reverted}) + "\n")
        return {"capture_id": cap["capture_id"], "status": "retracted", "reverted_colours": reverted}

    def facade(self, b):
        """Store a pasted facade image content-addressed and attach it to the building + queue."""
        import base64
        head, data = b["data_url"].split(",", 1)
        raw = base64.b64decode(data)
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(head[5:].split(";")[0], ".png")
        FACADES.mkdir(parents=True, exist_ok=True)
        p = FACADES / (hashlib.sha256(raw).hexdigest()[:16] + ext)
        if not p.exists():
            p.write_bytes(raw)
        relp = rel(p)
        bid = b["building_id"]
        ring = self.world.buildings[bid]
        face = b.get("face_index")
        entry = {"path": relp, "pasted_on": now(), "by": "operator via map_editor", "face_index": face,
                 "face_bearing_deg": outward_bearing(ring, face) if face is not None else None,
                 "note": (b.get("note") or "")[:2000], "status": "unprocessed"}
        app = json.loads(APPEARANCE.read_text())
        rec = app["buildings"].setdefault(bid, {})
        if not any(x.get("path") == relp for x in rec.get("operator_facade_photos") or []):
            rec.setdefault("operator_facade_photos", []).append(entry)
            write_json(APPEARANCE, app)
        item = add_queue_item({"kind": "facade_paste", "building_ids": [bid], "image": relp, "face_index": face,
                               "face_bearing_deg": entry["face_bearing_deg"], "text": entry["note"] or "facade photo pasted"})
        return {"image": relp, "queue": item}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8792)
    # The migrated current hand-edited world, never an old regeneration slot.
    ap.add_argument("--build", default=".run/fl4876_builds/live_numabe")
    a = ap.parse_args()
    Handler.world = World((ROOT / a.build).resolve())
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    # The A3D editor at / is the operator's page; /map (top-down plan) was rejected by the operator and /view is
    # a legacy evidence page -- do not hand either out as "the editor" (operator correction 2026-10-16).
    print("map editor http://127.0.0.1:%d/   build %s" % (a.port, a.build), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
