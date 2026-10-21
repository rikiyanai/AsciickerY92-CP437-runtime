"""street_imagery_probe.py -- Generic multi-provider street-level imagery prober.

PURPOSE
  One owner for the question "does ANY public imagery provider hold usable
  street-level views of this place, and what exactly did each provider answer?"

  The module answers that question the same way for every provider: a radius
  ladder from a point, an optional bbox query, a CONTROL query in a place the
  provider is expected to cover, and a per-provider verdict token. The control
  query is what makes a zero provable -- without it, a zero is
  indistinguishable from a wrong endpoint, a wrong parameter name or a silent
  auth rejection.

  This module is deliberately LOCALE-INDEPENDENT and PROJECT-INDEPENDENT. No
  place name, bounding box, station, corridor or campaign constant belongs in
  this file. Everything place-specific arrives through the probe SPEC (a JSON
  document) or the CLI. If you find yourself typing a latitude into this file,
  it belongs in the spec.

  Providers are likewise data-driven where they can be, and small adapter
  classes where the wire format demands it. Adding a provider means adding one
  adapter that normalises its records into `Picture`; it must not mean editing
  the ladder, the statistics, the verdict logic or the report writer.

VERDICT TOKENS (per provider, exactly one)
  ABSENT_IN_SURVEYED_RADIUS  every queried radius answered, all returned zero
  PRESENT                    at least one picture was returned by a query
  UNKNOWN_PENDING_ACCESS     the provider answered, but refused without a
                             credential; coverage is undetermined, NOT zero
  NOT_SURVEYED               no query was issued for this provider

  ABSENT_IN_SURVEYED_RADIUS is only emitted when the control query returned
  pictures. A provider whose control query also returned zero is reported
  UNKNOWN_PENDING_ACCESS with the control failure attached, because the
  endpoint itself is then unproven.

HTTP DISCIPLINE
  Every request is recorded verbatim in `HttpLog`: method, URL, HTTP status,
  content type, byte count, elapsed seconds, and -- when the provider wraps a
  second status inside a 200 body -- that inner code and message too. A failed
  request is a reported failure; the prober never estimates around one.

METADATA ONLY
  The prober requests coverage metadata. It never bulk-downloads imagery.
  `fetch_bytes()` exists for the bounded retention of a handful of explicitly
  open-licensed probe images, is capped by `--max-retain`, and refuses any
  licence that `source_manifest.is_open_licence` does not accept.

CLI
    python -m scripts.pipeline.street_imagery_probe --spec SPEC.json --out SURVEY.json
    python -m scripts.pipeline.street_imagery_probe --spec SPEC.json --providers kartaview,commons
    python -m scripts.pipeline.street_imagery_probe --list-providers

SPEC SHAPE
    {
      "survey_id": "...",
      "user_agent": "...",
      "target": {"lat": 0.0, "lon": 0.0, "label": "..."},
      "aoi_bbox_lonlat": [min_lon, min_lat, max_lon, max_lat],
      "aoi_polygon_lonlat": [[lon, lat], ...],          # optional
      "radius_ladder_m": [100, 250, 500, 1000],
      "viewpoint": {"min_baseline_m": 5.0, "max_range_m": 80.0, "half_fov_deg": 35.0},
      "providers": {
        "<provider_name>": {"enabled": true, "control": {...}, ...adapter options}
      },
      "retain_dir": "/abs/path"                        # optional; scratch only
    }
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

TOOL_VERSION = "street_imagery_probe/1"

STATE_ABSENT = "ABSENT_IN_SURVEYED_RADIUS"
STATE_PRESENT = "PRESENT"
STATE_UNKNOWN = "UNKNOWN_PENDING_ACCESS"
STATE_NOT_SURVEYED = "NOT_SURVEYED"

EARTH_R_M = 6371008.8  # IUGG mean radius


# --------------------------------------------------------------------------
# geodesy -- small, exact enough for coverage work at street scale
# --------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Sub-metre accurate at street scale."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_delta_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two compass angles, in [0, 180]."""
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


def bbox_around(lat: float, lon: float, radius_m: float) -> Tuple[float, float, float, float]:
    """Axis-aligned lon/lat bbox enclosing the radius disc: (min_lon, min_lat, max_lon, max_lat)."""
    dlat = math.degrees(radius_m / EARTH_R_M)
    dlon = math.degrees(radius_m / (EARTH_R_M * max(math.cos(math.radians(lat)), 1e-9)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """Ray-casting point-in-polygon on a [[lon, lat], ...] ring."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xin:
                inside = not inside
    return inside


def spacing_stats(points: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
    """Consecutive-point spacing statistics for one ordered capture sequence.

    Returns min/p25/median/p75/max, the path length, and the fraction of gaps
    inside the 2-4 m band that keyframe-based reconstruction wants.
    """
    if len(points) < 2:
        return {"pairs": 0}
    gaps = [haversine_m(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
            for i in range(len(points) - 1)]
    gaps_sorted = sorted(gaps)

    def pct(p: float) -> float:
        if not gaps_sorted:
            return 0.0
        k = (len(gaps_sorted) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        if lo == hi:
            return gaps_sorted[int(k)]
        return gaps_sorted[lo] * (hi - k) + gaps_sorted[hi] * (k - lo)

    in_band = [g for g in gaps if 2.0 <= g <= 4.0]
    return {
        "pairs": len(gaps),
        "path_length_m": round(sum(gaps), 2),
        "min": round(gaps_sorted[0], 2),
        "p25": round(pct(0.25), 2),
        "median": round(statistics.median(gaps), 2),
        "p75": round(pct(0.75), 2),
        "max": round(gaps_sorted[-1], 2),
        "fraction_in_2_to_4_m_band": round(len(in_band) / len(gaps), 3),
        "fraction_le_4_m": round(len([g for g in gaps if g <= 4.0]) / len(gaps), 3),
    }


# --------------------------------------------------------------------------
# HTTP -- every request recorded verbatim
# --------------------------------------------------------------------------

@dataclass
class HttpLog:
    method: str
    url: str
    http: Optional[int]
    content_type: Optional[str] = None
    bytes: Optional[int] = None
    elapsed_s: Optional[float] = None
    api_code: Optional[Any] = None       # provider status nested inside a 200 body
    api_message: Optional[str] = None
    error: Optional[str] = None          # transport failure or refusal text, verbatim
    note: Optional[str] = None


class Http:
    """Minimal recording HTTP client. Records first, interprets never."""

    def __init__(self, user_agent: str, timeout: float = 60.0, pause_s: float = 0.35):
        self.user_agent = user_agent
        self.timeout = timeout
        self.pause_s = pause_s
        self.log: List[HttpLog] = []
        self._ctx = ssl.create_default_context()

    def request(self, url: str, *, method: str = "GET",
                data: Optional[Dict[str, str]] = None,
                headers: Optional[Dict[str, str]] = None,
                note: Optional[str] = None) -> Tuple[HttpLog, Optional[bytes]]:
        body = urllib.parse.urlencode(data).encode() if data else None
        hdrs = {"User-Agent": self.user_agent, "Accept": "*/*"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                payload = resp.read()
                entry = HttpLog(method, _redact_url(url), resp.status,
                                resp.headers.get("Content-Type"), len(payload),
                                round(time.time() - start, 3), note=note)
        except urllib.error.HTTPError as exc:          # answered, with a refusal
            payload = exc.read()
            entry = HttpLog(method, _redact_url(url), exc.code, exc.headers.get("Content-Type") if exc.headers else None,
                            len(payload), round(time.time() - start, 3),
                            error=payload[:400].decode("utf-8", "replace"), note=note)
        except Exception as exc:                       # never answered at all
            payload = None
            entry = HttpLog(method, _redact_url(url), None, None, None, round(time.time() - start, 3),
                            error=f"{type(exc).__name__}: {exc}", note=note)
        self.log.append(entry)
        if self.pause_s:
            time.sleep(self.pause_s)
        return entry, payload

    def json(self, url: str, **kw) -> Tuple[HttpLog, Optional[Any]]:
        entry, payload = self.request(url, **kw)
        if payload is None:
            return entry, None
        try:
            doc = json.loads(payload.decode("utf-8", "replace"))
        except Exception as exc:
            entry.error = entry.error or f"non-JSON body: {type(exc).__name__}: {exc}"
            entry.note = (entry.note or "") + " | body head: " + payload[:200].decode("utf-8", "replace")
            return entry, None
        return entry, doc


def _redact_url(url: str) -> str:
    """Keep request receipts useful without persisting provider credentials."""
    parts = urllib.parse.urlsplit(url)
    sensitive = {"access_token", "api_key", "key", "token"}
    query = [
        (name, "<redacted>" if name.casefold() in sensitive else value)
        for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urllib.parse.urlencode(query),
        parts.fragment,
    ))


# --------------------------------------------------------------------------
# normalised record + per-provider result
# --------------------------------------------------------------------------

@dataclass
class Picture:
    """One camera observation, normalised across providers."""
    provider: str
    picture_id: str
    lat: float
    lon: float
    source_uri: str
    sequence_id: Optional[str] = None
    sequence_index: Optional[int] = None
    capture_date: Optional[str] = None
    heading_deg: Optional[float] = None
    licence: Optional[str] = None
    author: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    camera: Optional[str] = None
    focal_length_mm: Optional[float] = None
    focal_35mm: Optional[float] = None
    page_uri: Optional[str] = None
    asset_uri: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    provider: str
    state: str = STATE_NOT_SURVEYED
    queried: List[str] = field(default_factory=list)
    http: List[Dict[str, Any]] = field(default_factory=list)
    ladder: List[Dict[str, Any]] = field(default_factory=list)
    control: Dict[str, Any] = field(default_factory=dict)
    counts: Dict[str, Any] = field(default_factory=dict)
    pictures: List[Dict[str, Any]] = field(default_factory=list)
    sequences: Dict[str, Any] = field(default_factory=dict)
    licence: Dict[str, Any] = field(default_factory=dict)
    access: Dict[str, Any] = field(default_factory=dict)
    viewpoints: Dict[str, Any] = field(default_factory=dict)
    what_would_change_the_verdict: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# provider adapters
#
# An adapter owns ONE provider's wire format. Its only job is to turn a
# (centre, radius) or (bbox) request into `Picture` records plus the HTTP logs
# that prove what the provider actually said. It must not decide verdicts,
# compute statistics, or know anything about the survey's subject.
# --------------------------------------------------------------------------

class Adapter:
    name = "abstract"
    needs_credential = False
    default_licence: Optional[str] = None
    licence_uri: Optional[str] = None
    attribution: Optional[str] = None
    max_radius_m: Optional[float] = None

    def __init__(self, http: Http, options: Optional[Dict[str, Any]] = None):
        self.http = http
        self.options = options or {}

    # --- interface -------------------------------------------------------
    def radius_query(self, lat: float, lon: float, radius_m: float) -> Tuple[List[Picture], List[HttpLog], Optional[str]]:
        """Return (pictures, http entries, refusal reason or None)."""
        raise NotImplementedError

    def bbox_query(self, bbox: Sequence[float]) -> Tuple[List[Picture], List[HttpLog], Optional[str]]:
        """Return exact bounding-box coverage when a provider supports it."""
        return [], [], None

    def sequence_pictures(self, sequence_id: str) -> Tuple[List[Picture], List[HttpLog]]:
        """Ordered pictures of one capture sequence, when the provider has them."""
        return [], []

    def enrich(self, pictures: List[Picture]) -> List[HttpLog]:
        """Optional second pass for per-picture metadata (licence, EXIF, ...)."""
        return []

    def access_facts(self) -> Dict[str, Any]:
        """What the provider states about credentials, licence and reuse."""
        return {}


class PanoramaxAdapter(Adapter):
    """Panoramax / GeoVisio STAC search. Public, no credential."""
    name = "panoramax"
    default_licence = "CC-BY-SA-4.0"
    licence_uri = "https://creativecommons.org/licenses/by-sa/4.0/"

    def _endpoints(self) -> List[str]:
        return self.options.get("endpoints", ["https://api.panoramax.xyz/api/search"])

    def radius_query(self, lat, lon, radius_m):
        pics: List[Picture] = []
        logs: List[HttpLog] = []
        bbox = bbox_around(lat, lon, radius_m)
        for base in self._endpoints():
            url = f"{base}?bbox={bbox[0]:.7f},{bbox[1]:.7f},{bbox[2]:.7f},{bbox[3]:.7f}&limit={int(self.options.get('limit', 500))}"
            entry, doc = self.http.json(url, note=f"radius {radius_m:g} m bbox")
            logs.append(entry)
            for feat in (doc or {}).get("features", []) or []:
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates") or [None, None]
                if coords[0] is None:
                    continue
                props = feat.get("properties") or {}
                if haversine_m(lat, lon, coords[1], coords[0]) > radius_m:
                    continue
                pics.append(Picture(
                    provider=self.name, picture_id=str(feat.get("id")),
                    lat=coords[1], lon=coords[0], source_uri=url,
                    sequence_id=(feat.get("collection") or None),
                    capture_date=props.get("datetime"),
                    heading_deg=props.get("view:azimuth"),
                    licence=props.get("license") or self.default_licence,
                    author=(props.get("geovisio:producer") or None),
                    camera=props.get("pers:interior_orientation", {}).get("camera_model")
                    if isinstance(props.get("pers:interior_orientation"), dict) else None,
                ))
        return pics, logs, None

    def access_facts(self):
        return {"credential_required": False,
                "endpoints": self._endpoints(),
                "stated_licence": self.default_licence}


class KartaViewAdapter(Adapter):
    """KartaView (ex-OpenStreetCam).

    Two live API generations answer today and BOTH are probed, because their
    answers differ in shape and in cap:
      * /1.0/list/nearby-photos/   POST, returns the full page of photos in a
        radius plus `totalFilteredItems`.
      * /2.0/photo/                GET, returns the single nearest photo and
        rejects radius > 500 with an explicit validator message.
    The legacy host api.openstreetcam.org and the current host
    api.kartaview.org are both probed; whichever answers is recorded.
    """
    name = "kartaview"
    default_licence = "CC-BY-SA-4.0"
    licence_uri = "https://creativecommons.org/licenses/by-sa/4.0/"
    attribution = "(c) KartaView contributors, CC-BY-SA-4.0"
    max_radius_m = None  # 1.0 endpoint accepts wide radii; 2.0 is capped at 500

    def _hosts(self) -> List[str]:
        return self.options.get("hosts", ["https://api.kartaview.org", "https://api.openstreetcam.org"])

    def radius_query(self, lat, lon, radius_m):
        pics: List[Picture] = []
        logs: List[HttpLog] = []
        seen: set = set()
        for host in self._hosts():
            url = f"{host}/1.0/list/nearby-photos/"
            entry, doc = self.http.json(
                url, method="POST",
                data={"lat": f"{lat:.7f}", "lng": f"{lon:.7f}",
                      "radius": str(int(radius_m)), "zoom": "18", "page": "1"},
                note=f"nearby-photos radius {radius_m:g} m")
            if doc and isinstance(doc.get("status"), dict):
                entry.api_code = doc["status"].get("apiCode")
                entry.api_message = (doc["status"].get("apiMessage") or "").strip()
            logs.append(entry)
            items = (doc or {}).get("currentPageItems") or []
            total = (doc or {}).get("totalFilteredItems")
            if isinstance(total, list) and total:
                entry.note = f"{entry.note} | totalFilteredItems={total[0]}"
            for it in items:
                pid = str(it.get("id"))
                if pid in seen:
                    continue
                seen.add(pid)
                pics.append(self._to_picture(it, url))
            if items:
                break  # first host that answers with data is enough
        # second generation, for the endpoint-shape record and its cap message
        if radius_m <= 500:
            for host in self._hosts()[:1]:
                u2 = f"{host}/2.0/photo/?lat={lat:.7f}&lng={lon:.7f}&radius={int(radius_m)}"
                e2, d2 = self.http.json(u2, note=f"2.0/photo radius {radius_m:g} m")
                if d2 and isinstance(d2.get("status"), dict):
                    e2.api_code = d2["status"].get("apiCode")
                    e2.api_message = (d2["status"].get("apiMessage") or "").strip()
                    e2.note = f"{e2.note} | data={len(((d2.get('result') or {}).get('data') or []))}"
                logs.append(e2)
        else:
            u2 = f"{self._hosts()[0]}/2.0/photo/?lat={lat:.7f}&lng={lon:.7f}&radius={int(radius_m)}"
            e2, d2 = self.http.json(u2, note=f"2.0/photo radius {radius_m:g} m (expected to exceed the documented cap)")
            if d2 and isinstance(d2, dict) and "message" in d2:
                e2.api_code = d2.get("code")
                e2.api_message = d2.get("message")
            logs.append(e2)
        return pics, logs, None

    @staticmethod
    def _pick(it: Dict[str, Any], *names: str) -> Any:
        """Case-insensitive field lookup.

        The two live hosts disagree on capitalisation for the same field:
        api.openstreetcam.org sends `heading`, api.kartaview.org sends
        `Heading`. Reading only one spelling silently loses every compass
        angle from the other host, which then reads as "no headings exist".
        """
        lowered = {k.lower(): v for k, v in it.items()}
        for n in names:
            v = lowered.get(n.lower())
            if v not in (None, ""):
                return v
        return None

    def _to_picture(self, it: Dict[str, Any], url: str) -> Picture:
        heading = self._pick(it, "heading", "headers")
        seq = self._pick(it, "sequence_id", "sequenceId")
        pid = str(self._pick(it, "id"))
        idx = self._pick(it, "sequence_index", "sequenceIndex")
        return Picture(
            provider=self.name, picture_id=pid,
            lat=float(self._pick(it, "lat")), lon=float(self._pick(it, "lng")), source_uri=url,
            sequence_id=str(seq) if seq is not None else None,
            sequence_index=int(idx) if idx is not None else None,
            capture_date=self._pick(it, "shot_date", "shotDate", "date_added", "dateAdded"),
            heading_deg=float(heading) if heading is not None else None,
            licence=self.default_licence,
            width=int(self._pick(it, "width") or 0) or None,
            height=int(self._pick(it, "height") or 0) or None,
            camera=self._pick(it, "cameraParameters"),
            page_uri=f"https://kartaview.org/details/{seq}/{idx}" if seq is not None else None,
            extra={"gps_accuracy": self._pick(it, "gps_accuracy", "gpsAccuracy"),
                   "way_id": self._pick(it, "way_id", "wayId"),
                   "has_depth": it.get("hasDepth", it.get("has_depth")),
                   "projection": self._pick(it, "projection")},
        )

    def sequence_pictures(self, sequence_id: str):
        logs: List[HttpLog] = []
        for host in self._hosts():
            url = f"{host}/1.0/sequence/photo-list/"
            entry, doc = self.http.json(url, method="POST", data={"sequenceId": str(sequence_id)},
                                        note=f"sequence {sequence_id} photo-list")
            if doc and isinstance(doc.get("status"), dict):
                entry.api_code = doc["status"].get("apiCode")
                entry.api_message = (doc["status"].get("apiMessage") or "").strip()
            logs.append(entry)
            photos = ((doc or {}).get("osv") or {}).get("photos") or []
            if photos:
                pics = [self._to_picture(p, url) for p in photos]
                pics.sort(key=lambda p: (p.sequence_index if p.sequence_index is not None else 0))
                return pics, logs
        return [], logs

    def probe_extra_endpoints(self, lat: float, lon: float, radius_m: float) -> List[HttpLog]:
        """Point-cloud / depth / 3D surfaces, recorded as their own source class."""
        logs: List[HttpLog] = []
        for url, note in self.options.get("extra_endpoints", []):
            url = url.format(lat=lat, lon=lon, radius=int(radius_m))
            entry, doc = self.http.json(url, note=note)
            if isinstance(doc, dict) and "message" in doc and "status" not in doc:
                entry.api_code = doc.get("code")
                entry.api_message = doc.get("message")
            logs.append(entry)
        return logs

    def access_facts(self):
        return {"credential_required": False,
                "hosts_probed": self._hosts(),
                "stated_licence": self.default_licence,
                "licence_uri": self.licence_uri}


class MapillaryAdapter(Adapter):
    """Mapillary graph API and public coverage vector tiles.

    The adapter resolves client access from an explicit value, an environment
    variable, or an explicitly enabled public-web-client bootstrap.  The last
    route reads the client token already shipped to browsers by Mapillary's
    public application.  It never writes that token to a receipt or file.
    """
    name = "mapillary"
    needs_credential = True
    default_licence = "CC-BY-SA-4.0"

    GRAPH = "https://graph.mapillary.com/images"
    TILES = "https://tiles.mapillary.com/maps/vtp/{tileset}/2/{z}/{x}/{y}"
    WEB_APP = "https://www.mapillary.com/app/"
    CLIENT_TOKEN_RE = re.compile(rb"MLY\|[A-Za-z0-9_.%|-]{20,}")

    @staticmethod
    def _tile_xy(lat: float, lon: float, z: int) -> Tuple[int, int]:
        n = 2 ** z
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
        return x, y

    def _resolve_token(self) -> Tuple[Optional[str], List[HttpLog]]:
        if getattr(self, "_token_resolved", False):
            return self._resolved_token, []
        self._token_resolved = True
        token = self.options.get("access_token")
        self._token_source = "configured" if token else None
        token_env = self.options.get("access_token_env")
        if not token and token_env:
            token = os.environ.get(str(token_env))
            if token:
                self._token_source = "environment"
        logs: List[HttpLog] = []
        if not token and self.options.get("allow_public_web_client_token"):
            entry, app_payload = self.http.request(
                self.WEB_APP, note="Mapillary public web-client bootstrap shell"
            )
            logs.append(entry)
            script_urls: List[str] = []
            if entry.http == 200 and app_payload:
                html = app_payload.decode("utf-8", "ignore")
                script_urls = [
                    urllib.parse.urljoin(self.WEB_APP, source)
                    for source in re.findall(r'<script[^>]+src=["\']([^"\']+)', html)
                    if urllib.parse.urlsplit(source).path.rsplit("/", 1)[-1].startswith(
                        "main."
                    )
                ]
            candidates: set[str] = set()
            for script_url in script_urls:
                script_entry, script_payload = self.http.request(
                    script_url, note="Mapillary public web-client bootstrap bundle"
                )
                logs.append(script_entry)
                if script_entry.http == 200 and script_payload:
                    candidates.update(
                        value.decode("ascii")
                        for value in self.CLIENT_TOKEN_RE.findall(script_payload)
                    )
            if len(candidates) == 1:
                token = candidates.pop()
                self._token_source = "mapillary_public_web_client"
            elif len(candidates) > 1:
                logs[-1].error = "public web client exposed multiple token candidates"
        self._resolved_token = str(token) if token else None
        return self._resolved_token, logs

    def _graph_bbox(self, bbox: Sequence[float], note: str):
        token, logs = self._resolve_token()
        fields = (
            "id,geometry,computed_geometry,captured_at,compass_angle,camera_type,"
            "sequence,thumb_1024_url,creator,width,height,make,model"
        )
        first_url = (f"{self.GRAPH}?bbox={bbox[0]:.7f},{bbox[1]:.7f},{bbox[2]:.7f},{bbox[3]:.7f}"
                     f"&limit={int(self.options.get('limit', 2000))}&fields={fields}")
        hdrs = {"Authorization": f"OAuth {token}"} if token else None
        page_url: Optional[str] = first_url
        graph_rows: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        max_pages = int(self.options.get("max_pages", 20))
        doc: Optional[Any] = None
        for page in range(max_pages):
            if not page_url:
                break
            entry, doc = self.http.json(
                page_url,
                headers=hdrs,
                note=(f"{note}, page {page + 1}"
                      f"{'' if token else ' (NO token, deliberately)'}"),
            )
            if isinstance(doc, dict) and "error" in doc:
                err = doc["error"]
                entry.api_code = err.get("code")
                entry.api_message = err.get("message")
            logs.append(entry)
            if not isinstance(doc, dict) or "error" in doc:
                break
            for feat in doc.get("data", []) or []:
                picture_id = str(feat.get("id"))
                if picture_id in seen_ids:
                    continue
                seen_ids.add(picture_id)
                graph_rows.append(feat)
            page_url = (doc.get("paging") or {}).get("next")
        else:
            if page_url:
                logs[-1].note = (logs[-1].note or "") + " | pagination stopped at max_pages"

        refusal = None
        if logs and (logs[-1].http in (400, 401, 403, 500)
                     or (isinstance(doc, dict) and "error" in doc)):
            entry = logs[-1]
            refusal = f"graph.mapillary.com HTTP {entry.http}: {entry.api_message or entry.error}"
        return graph_rows, logs, refusal, first_url

    def _pictures(self, graph_rows: Sequence[Dict[str, Any]], source_uri: str) -> List[Picture]:
        pics: List[Picture] = []
        for feat in graph_rows:
            geom = feat.get("geometry") or {}
            coords = (geom.get("coordinates") or [None, None])
            if coords[0] is None:
                continue
            ts = feat.get("captured_at")
            creator = feat.get("creator") or {}
            pics.append(Picture(
                provider=self.name, picture_id=str(feat.get("id")),
                lat=coords[1], lon=coords[0], source_uri=source_uri,
                sequence_id=feat.get("sequence"),
                capture_date=(datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
                              if isinstance(ts, (int, float)) else None),
                heading_deg=feat.get("compass_angle"),
                licence=self.default_licence,
                author=creator.get("username"),
                width=feat.get("width"),
                height=feat.get("height"),
                camera=" ".join(
                    str(value).strip()
                    for value in (feat.get("make"), feat.get("model"))
                    if value
                ) or feat.get("camera_type"),
                page_uri=f"https://www.mapillary.com/app/?pKey={feat.get('id')}",
                asset_uri=feat.get("thumb_1024_url"),
                extra={
                    "camera_type": feat.get("camera_type"),
                    "creator_id": creator.get("id"),
                    "computed_geometry": feat.get("computed_geometry"),
                },
            ))
        return pics

    def bbox_query(self, bbox):
        rows, logs, refusal, source_uri = self._graph_bbox(
            bbox, "graph images exact survey AOI bbox"
        )
        pics = self._pictures(rows, source_uri)
        return pics, logs, (None if pics else refusal)

    def radius_query(self, lat, lon, radius_m):
        rows, logs, refusal, source_uri = self._graph_bbox(
            bbox_around(lat, lon, radius_m),
            f"graph images bbox, radius {radius_m:g} m",
        )
        token = self._resolved_token

        for tileset in self.options.get("tilesets", ["mly1_public", "mly1_computed_public"]):
            for z in self.options.get("tile_zooms", [14]):
                x, y = self._tile_xy(lat, lon, z)
                turl = self.TILES.format(tileset=tileset, z=z, x=x, y=y)
                if token:
                    turl += f"?access_token={token}"
                tentry, payload = self.http.request(turl, note=f"coverage tile {tileset} z{z}/{x}/{y}"
                                                               f"{'' if token else ' (NO token, deliberately)'}")
                logs.append(tentry)
                if tentry.http and tentry.http >= 400 and refusal is None:
                    refusal = f"tiles.mapillary.com HTTP {tentry.http}"

        pics = [
            pic for pic in self._pictures(rows, source_uri)
            if haversine_m(lat, lon, pic.lat, pic.lon) <= radius_m
        ]
        return pics, logs, (None if pics else refusal)

    def access_facts(self):
        token_env = self.options.get("access_token_env")
        token, _ = self._resolve_token()
        return {"credential_required": True,
                "graph_endpoint": self.GRAPH,
                "tile_endpoint": self.TILES,
                "token_supplied": bool(token),
                "token_source": getattr(self, "_token_source", None),
                "token_retained": False,
                "public_web_client_bootstrap_enabled": bool(
                    self.options.get("allow_public_web_client_token")
                ),
                "access_token_env": token_env}


class GoogleStreetViewAdapter(Adapter):
    """Street View Static API metadata endpoint.

    Metadata only, and by policy metadata only: Street View imagery may not be
    prefetched, cached or stored. The adapter therefore never requests a pixel;
    a PRESENT verdict here means "a panorama exists", never "a source corpus
    was acquired".
    """
    name = "google_street_view"
    needs_credential = True
    default_licence = "proprietary"
    META = "https://maps.googleapis.com/maps/api/streetview/metadata"

    def radius_query(self, lat, lon, radius_m):
        key = self.options.get("api_key")
        url = f"{self.META}?location={lat:.7f},{lon:.7f}&radius={int(radius_m)}"
        if key:
            url += f"&key={key}"
        entry, doc = self.http.json(url, note=f"streetview metadata radius {radius_m:g} m"
                                              f"{'' if key else ' (NO api key, deliberately)'}")
        if isinstance(doc, dict):
            entry.api_code = doc.get("status")
            entry.api_message = doc.get("error_message")
        pics: List[Picture] = []
        refusal = None
        status = (doc or {}).get("status")
        if status == "OK":
            loc = doc.get("location") or {}
            pics.append(Picture(provider=self.name, picture_id=str(doc.get("pano_id")),
                                lat=loc.get("lat"), lon=loc.get("lng"), source_uri=url,
                                capture_date=doc.get("date"), licence=self.default_licence,
                                extra={"copyright": doc.get("copyright")}))
        elif status in ("REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST", "UNKNOWN_ERROR"):
            refusal = f"{status}: {doc.get('error_message')}"
        return pics, [entry], refusal

    def access_facts(self):
        return {"credential_required": True,
                "metadata_endpoint": self.META,
                "retention_policy": "Street View imagery must not be prefetched, cached or stored; "
                                    "panorama IDs and metadata may be referenced.",
                "key_supplied": bool(self.options.get("api_key"))}


class WikimediaCommonsAdapter(Adapter):
    """Wikimedia Commons geosearch + imageinfo.

    Not a street-level capture platform: an unordered pool of geotagged
    photographs. It is probed because "is there open imagery of this place"
    and "is there a street-level capture sequence of this place" are different
    questions, and only the second one Panoramax/KartaView/Mapillary answer.
    """
    name = "wikimedia_commons"
    licence_uri = "https://commons.wikimedia.org/wiki/Commons:Licensing"

    API = "https://commons.wikimedia.org/w/api.php"

    def _api(self, note: str, **q) -> Tuple[HttpLog, Optional[Any]]:
        q.setdefault("action", "query")
        q.setdefault("format", "json")
        return self.http.json(self.API + "?" + urllib.parse.urlencode(q), note=note)

    def radius_query(self, lat, lon, radius_m):
        # geosearch caps radius at 10 km; larger ladder rungs are clamped and noted.
        r = min(int(radius_m), 10000)
        entry, doc = self._api(f"geosearch radius {radius_m:g} m"
                               + (f" (clamped to {r} m by the API cap)" if r != int(radius_m) else ""),
                               list="geosearch", gscoord=f"{lat}|{lon}", gsradius=r,
                               gslimit=int(self.options.get("limit", 100)), gsnamespace=6)
        if isinstance(doc, dict) and "error" in doc:
            entry.api_code = doc["error"].get("code")
            entry.api_message = doc["error"].get("info")
            return [], [entry], entry.api_message
        pics = []
        for it in ((doc or {}).get("query") or {}).get("geosearch", []) or []:
            pics.append(Picture(provider=self.name, picture_id=str(it["pageid"]),
                                lat=it["lat"], lon=it["lon"],
                                source_uri=self.API,
                                page_uri="https://commons.wikimedia.org/wiki/"
                                         + urllib.parse.quote(it["title"].replace(" ", "_")),
                                extra={"title": it["title"], "api_dist_m": it.get("dist")}))
        return pics, [entry], None

    def category_query(self, category: str) -> Tuple[List[Picture], List[HttpLog]]:
        """Files filed under a category, including ones with no geotag at all."""
        entry, doc = self._api(f"categorymembers {category}", list="categorymembers",
                               cmtitle=category, cmtype="file", cmlimit=500)
        if isinstance(doc, dict) and "error" in doc:
            entry.api_code = doc["error"].get("code")
            entry.api_message = doc["error"].get("info")
            return [], [entry]
        pics = []
        for it in ((doc or {}).get("query") or {}).get("categorymembers", []) or []:
            pics.append(Picture(provider=self.name, picture_id=str(it["pageid"]),
                                lat=float("nan"), lon=float("nan"), source_uri=self.API,
                                page_uri="https://commons.wikimedia.org/wiki/"
                                         + urllib.parse.quote(it["title"].replace(" ", "_")),
                                extra={"title": it["title"], "from_category": category}))
        return pics, [entry]

    def enrich(self, pictures: List[Picture]) -> List[HttpLog]:
        logs: List[HttpLog] = []
        titles = [p.extra.get("title") for p in pictures if p.extra.get("title")]
        by_title = {p.extra.get("title"): p for p in pictures}
        for i in range(0, len(titles), 20):
            entry, doc = self._api(f"imageinfo batch {i // 20}", prop="imageinfo",
                                   iiprop="url|size|mime|extmetadata|user|commonmetadata",
                                   iiextmetadatafilter="License|LicenseShortName|LicenseUrl|Artist|"
                                                       "Credit|DateTimeOriginal|UsageTerms|GPSLatitude|GPSLongitude",
                                   titles="|".join(titles[i:i + 20]))
            logs.append(entry)
            for _, page in (((doc or {}).get("query") or {}).get("pages") or {}).items():
                pic = by_title.get(page.get("title"))
                if not pic or not page.get("imageinfo"):
                    continue
                ii = page["imageinfo"][0]
                em = ii.get("extmetadata") or {}

                def val(k):
                    v = em.get(k)
                    return (v or {}).get("value") if isinstance(v, dict) else None

                cm = {x["name"]: x["value"] for x in (ii.get("commonmetadata") or [])}
                pic.width, pic.height = ii.get("width"), ii.get("height")
                pic.asset_uri = (ii.get("url") or "").split("?")[0]
                pic.licence = _spdx_from_commons(val("License"), val("LicenseShortName"))
                pic.author = _strip_html(val("Artist"))
                pic.capture_date = _strip_html(val("DateTimeOriginal"))
                pic.camera = " ".join(x for x in (cm.get("Make"), cm.get("Model")) if x) or None
                pic.focal_length_mm = _ratio(cm.get("FocalLength"))
                pic.focal_35mm = _num(cm.get("FocalLengthIn35mmFilm"))
                pic.heading_deg = _ratio(cm.get("GPSImgDirection"))
                if math.isnan(pic.lat):
                    glat, glon = _num(val("GPSLatitude")), _num(val("GPSLongitude"))
                    if glat is not None and glon is not None:
                        pic.lat, pic.lon = glat, glon
                pic.extra.update({
                    "usage_terms": _strip_html(val("UsageTerms")),
                    "licence_uri": val("LicenseUrl"),
                    "uploader": ii.get("user"),
                    "mime": ii.get("mime"),
                    "size_bytes": ii.get("size"),
                    "gps_img_direction_ref": cm.get("GPSImgDirectionRef"),
                })
        return logs

    def access_facts(self):
        return {"credential_required": False, "api": self.API,
                "note": "per-file licence is authoritative; Commons hosts several distinct licences"}


def _strip_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    out, depth = [], 0
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split()) or None


def _num(v) -> Optional[float]:
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _ratio(v) -> Optional[float]:
    """EXIF rationals arrive as 'num/den' or a bare number."""
    if v is None:
        return None
    s = str(v).strip()
    if "/" in s:
        try:
            n, d = s.split("/", 1)
            return round(float(n) / float(d), 4) if float(d) else None
        except Exception:
            return None
    return _num(s)


_COMMONS_SPDX = {
    "cc0": "CC0-1.0", "cc-by-4.0": "CC-BY-4.0", "cc-by-3.0": "CC-BY-3.0",
    "cc-by-2.0": "CC-BY-2.0", "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "cc-by-sa-3.0": "CC-BY-SA-3.0", "cc-by-sa-2.0": "CC-BY-SA-2.0",
    "pd": "public-domain", "public domain": "public-domain",
}


def _spdx_from_commons(license_field: Optional[str], short: Optional[str]) -> Optional[str]:
    key = (license_field or short or "").strip().lower()
    if key in _COMMONS_SPDX:
        return _COMMONS_SPDX[key]
    if "public domain" in key or key == "pd":
        return "public-domain"
    return license_field or short


ADAPTERS: Dict[str, type] = {
    PanoramaxAdapter.name: PanoramaxAdapter,
    KartaViewAdapter.name: KartaViewAdapter,
    MapillaryAdapter.name: MapillaryAdapter,
    GoogleStreetViewAdapter.name: GoogleStreetViewAdapter,
    WikimediaCommonsAdapter.name: WikimediaCommonsAdapter,
}


# --------------------------------------------------------------------------
# analysis shared by every provider
# --------------------------------------------------------------------------

def viewpoint_analysis(pictures: Sequence[Picture], lat: float, lon: float,
                       *, min_baseline_m: float, max_range_m: float,
                       half_fov_deg: float) -> Dict[str, Any]:
    """How many genuinely DIFFERENT viewpoints see the target.

    Two pictures taken 30 cm apart are one viewpoint, not two, so positions
    closer than `min_baseline_m` collapse into one. A picture whose recorded
    heading points away from the target does not see it and is excluded; a
    picture with no heading is counted on range alone and flagged, because an
    unverified view is weaker evidence than a verified one.
    """
    candidates = []
    for p in pictures:
        if p.lat is None or p.lon is None or math.isnan(p.lat) or math.isnan(p.lon):
            continue
        d = haversine_m(lat, lon, p.lat, p.lon)
        if d > max_range_m:
            continue
        if p.heading_deg is not None:
            to_target = bearing_deg(p.lat, p.lon, lat, lon)
            if angle_delta_deg(p.heading_deg, to_target) > half_fov_deg:
                continue
            verified = True
        else:
            verified = False
        candidates.append((d, p, verified))
    candidates.sort(key=lambda t: t[0])

    clusters: List[List[Tuple[float, Picture, bool]]] = []
    for cand in candidates:
        for cl in clusters:
            if haversine_m(cl[0][1].lat, cl[0][1].lon, cand[1].lat, cand[1].lon) < min_baseline_m:
                cl.append(cand)
                break
        else:
            clusters.append([cand])

    reps = [cl[0] for cl in clusters]
    baselines = [round(haversine_m(a[1].lat, a[1].lon, b[1].lat, b[1].lon), 1)
                 for i, a in enumerate(reps) for b in reps[i + 1:]]
    return {
        "params": {"min_baseline_m": min_baseline_m, "max_range_m": max_range_m,
                   "half_fov_deg": half_fov_deg},
        "pictures_in_range": len(candidates),
        "distinct_viewpoints": len(clusters),
        "heading_verified_viewpoints": sum(1 for cl in clusters if cl[0][2]),
        "at_least_2": len(clusters) >= 2,
        "at_least_3": len(clusters) >= 3,
        "max_baseline_m": max(baselines) if baselines else 0.0,
        "median_baseline_m": round(statistics.median(baselines), 1) if baselines else 0.0,
        "nearest_m": round(candidates[0][0], 1) if candidates else None,
        "viewpoint_ids": [cl[0][1].picture_id for cl in clusters],
    }


def decide_state(*, queried: bool, pictures_found: int, refusal: Optional[str],
                 control_pictures: Optional[int]) -> Tuple[str, List[str]]:
    """One provider, one token, and the reason it is that token."""
    if not queried:
        return STATE_NOT_SURVEYED, ["issue any query against this provider"]
    if pictures_found > 0:
        return STATE_PRESENT, ["nothing; coverage was returned and is recorded"]
    if refusal:
        return STATE_UNKNOWN, [f"a credential that clears the refusal: {refusal}"]
    if control_pictures is None:
        return STATE_UNKNOWN, ["a control query in a place the provider is expected to cover; "
                               "without one this zero is indistinguishable from a wrong endpoint"]
    if control_pictures <= 0:
        return STATE_UNKNOWN, ["a control query that RETURNS pictures; the control also returned "
                               "zero, so the endpoint itself is unproven"]
    return STATE_ABSENT, ["new uploads inside the surveyed radius, or a wider radius rung"]


def fetch_bytes(http: Http, pic: Picture, out_dir: str, *, max_bytes: int = 12 << 20
                ) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[HttpLog]]:
    """Retain ONE picture's bytes outside the repository, gated on an open licence.

    Returns (path, sha256, size, http entry). Refuses any licence that
    source_manifest does not recognise as open, so a licensing mistake cannot
    be made by forgetting a check at a call site.
    """
    from pathlib import Path
    try:
        from scripts.pipeline.source_manifest import is_open_licence
    except Exception:                                     # standalone execution
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scripts.pipeline.source_manifest import is_open_licence

    if not pic.asset_uri:
        return None, None, None, None
    if not (is_open_licence(pic.licence) or pic.licence == "public-domain"):
        return None, None, None, None
    entry, payload = http.request(pic.asset_uri, note=f"retain probe image {pic.picture_id}")
    if payload is None or (entry.http or 0) != 200 or len(payload) > max_bytes:
        return None, None, None, entry
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{pic.provider}_{pic.picture_id}_{urllib.parse.unquote(pic.asset_uri.rsplit('/', 1)[-1])}"
    name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:180]
    path = out / name
    path.write_bytes(payload)
    return str(path), hashlib.sha256(payload).hexdigest(), len(payload), entry


# --------------------------------------------------------------------------
# survey driver
# --------------------------------------------------------------------------

def run_provider(name: str, spec: Dict[str, Any], http: Http) -> ProviderResult:
    opts = dict((spec.get("providers") or {}).get(name) or {})
    result = ProviderResult(provider=name)
    if not opts.get("enabled", True):
        result.notes.append("disabled in spec")
        return result

    adapter = ADAPTERS[name](http, opts)
    target = spec["target"]
    lat, lon = float(target["lat"]), float(target["lon"])
    # A provider may need its own ladder: an unordered photo pool saturates at a
    # radius where a capture platform is still informative, and querying it
    # wider only buys unrelated pictures.
    ladder = [float(r) for r in opts.get("radius_ladder_m",
                                         spec.get("radius_ladder_m", [100, 250, 500, 1000]))]
    vp = spec.get("viewpoint") or {}

    all_pics: Dict[str, Picture] = {}
    refusal: Optional[str] = None
    aoi_bbox = spec.get("aoi_bbox_lonlat")
    if aoi_bbox:
        pics, logs, why = adapter.bbox_query(aoi_bbox)
        result.http.extend(asdict(log) for log in logs)
        if logs:
            result.queried.append("exact aoi_bbox_lonlat")
            result.ladder.append({
                "aoi_bbox_lonlat": aoi_bbox,
                "pictures": len(pics),
                "http": [log.http for log in logs],
                "refused": why,
            })
        if why and refusal is None:
            refusal = why
        for picture in pics:
            all_pics.setdefault(picture.picture_id, picture)
    for radius in ladder:
        pics, logs, why = adapter.radius_query(lat, lon, radius)
        result.http.extend(asdict(l) for l in logs)
        result.queried.append(f"radius {radius:g} m about {lat:.7f},{lon:.7f}")
        if why and refusal is None:
            refusal = why
        for p in pics:
            all_pics.setdefault(p.picture_id, p)
        result.ladder.append({
            "radius_m": radius,
            "pictures": len(pics),
            "http": [l.http for l in logs],
            "refused": why,
        })

    # extra source classes (point cloud / depth), kept out of the imagery count
    if hasattr(adapter, "probe_extra_endpoints") and opts.get("extra_endpoints"):
        logs = adapter.probe_extra_endpoints(lat, lon, ladder[-1])
        result.http.extend(asdict(l) for l in logs)
        result.notes.append("extra (non-imagery) endpoints probed; see http log entries with their notes")

    # category / keyword sweeps, for pools that are not geotag-complete
    for cat in opts.get("categories", []):
        pics, logs = adapter.category_query(cat)          # type: ignore[attr-defined]
        result.http.extend(asdict(l) for l in logs)
        result.queried.append(f"category {cat}")
        for p in pics:
            all_pics.setdefault(p.picture_id, p)

    pictures = list(all_pics.values())
    result.http.extend(asdict(l) for l in adapter.enrich(pictures))

    # control query -- the only thing that makes a zero provable
    control_count: Optional[int] = None
    ctrl = opts.get("control")
    if ctrl:
        cpics, clogs, cwhy = adapter.radius_query(float(ctrl["lat"]), float(ctrl["lon"]),
                                                  float(ctrl.get("radius_m", 500)))
        result.http.extend(asdict(l) for l in clogs)
        control_count = len(cpics)
        result.control = {
            "label": ctrl.get("label"),
            "lat": ctrl["lat"], "lon": ctrl["lon"],
            "radius_m": ctrl.get("radius_m", 500),
            "pictures": control_count,
            "http": [l.http for l in clogs],
            "refused": cwhy,
            "purpose": "proves the endpoint and its parameters work, so a zero at the target "
                       "is a real absence and not a request error",
        }

    state, change = decide_state(queried=True, pictures_found=len(pictures),
                                 refusal=refusal, control_pictures=control_count)
    result.state = state
    result.what_would_change_the_verdict = change
    result.access = adapter.access_facts()
    if refusal:
        result.access["refusal"] = refusal

    # counts, dates, licences
    # Only machine-readable dates go into the min/max pair. Commons and other
    # human-curated pools carry free-text dates ("Taken on 14 March 2010",
    # "撮影日"), and string-sorting those produces a range that means nothing.
    dates = sorted({p.capture_date for p in pictures
                    if p.capture_date and len(p.capture_date) >= 10
                    and p.capture_date[:4].isdigit() and p.capture_date[4] in "-/"})
    free_text_dates = len([p for p in pictures if p.capture_date]) - len(
        {p.capture_date for p in pictures
         if p.capture_date and len(p.capture_date) >= 10
         and p.capture_date[:4].isdigit() and p.capture_date[4] in "-/"})
    lic: Dict[str, int] = {}
    for p in pictures:
        lic[p.licence or "unstated"] = lic.get(p.licence or "unstated", 0) + 1
    inside = 0
    ring = spec.get("aoi_polygon_lonlat")
    if ring:
        inside = sum(1 for p in pictures
                     if p.lat is not None and not math.isnan(p.lat) and point_in_ring(p.lon, p.lat, ring))
    dists = sorted(round(haversine_m(lat, lon, p.lat, p.lon), 1) for p in pictures
                   if p.lat is not None and not math.isnan(p.lat))
    result.counts = {
        "pictures": len(pictures),
        "pictures_inside_aoi_polygon": inside if ring else None,
        "sequences": len({p.sequence_id for p in pictures if p.sequence_id}),
        "with_heading": sum(1 for p in pictures if p.heading_deg is not None),
        "with_capture_date": len([p for p in pictures if p.capture_date]),
        "nearest_m": dists[0] if dists else None,
        "farthest_m": dists[-1] if dists else None,
        "capture_dates_min": dates[0] if dates else None,
        "capture_dates_max": dates[-1] if dates else None,
        "capture_dates_free_text": free_text_dates,
    }
    result.licence = {"counts": lic, "licence_uri": adapter.licence_uri,
                      "attribution": adapter.attribution}

    # Per-sequence spacing, for providers that publish ordered sequences.
    # Profile the sequences that come CLOSEST to the target: a sequence id is
    # an arbitrary integer, so profiling the lowest ids would characterise
    # whichever capture happens to be oldest rather than whichever one matters.
    nearest_by_seq: Dict[str, float] = {}
    for p in pictures:
        if not p.sequence_id or p.lat is None or math.isnan(p.lat):
            continue
        d = haversine_m(lat, lon, p.lat, p.lon)
        if d < nearest_by_seq.get(p.sequence_id, float("inf")):
            nearest_by_seq[p.sequence_id] = d
    seq_ids = sorted(nearest_by_seq, key=lambda s: nearest_by_seq[s])
    for sid in seq_ids[: int(opts.get("max_sequences_profiled", 3))]:
        spics, slogs = adapter.sequence_pictures(sid)
        result.http.extend(asdict(l) for l in slogs)
        if not spics:
            continue
        pts = [(p.lat, p.lon) for p in spics]
        nearest = min(haversine_m(lat, lon, p.lat, p.lon) for p in spics)
        result.sequences[sid] = {
            "pictures": len(spics),
            "capture_date_first": spics[0].capture_date,
            "capture_date_last": spics[-1].capture_date,
            "nearest_approach_to_target_m": round(nearest, 1),
            "spacing_m": spacing_stats(pts),
            "headings_present": sum(1 for p in spics if p.heading_deg is not None),
            # depth/point-cloud availability is a DIFFERENT source class from
            # imagery, so it is counted here and never folded into the picture
            # count.
            "with_depth": sum(1 for p in spics if p.extra.get("has_depth")),
        }

    result.viewpoints = viewpoint_analysis(
        pictures, lat, lon,
        min_baseline_m=float(vp.get("min_baseline_m", 5.0)),
        max_range_m=float(vp.get("max_range_m", 80.0)),
        half_fov_deg=float(vp.get("half_fov_deg", 35.0)))

    result.pictures = [asdict(p) for p in sorted(
        pictures, key=lambda p: haversine_m(lat, lon, p.lat, p.lon)
        if p.lat is not None and not math.isnan(p.lat) else 1e12)]
    return result


def run_survey(spec: Dict[str, Any], providers: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    http = Http(spec.get("user_agent", f"{TOOL_VERSION} (coverage research)"))
    names = list(providers) if providers else list((spec.get("providers") or {}).keys())
    out: Dict[str, Any] = {}
    for name in names:
        if name not in ADAPTERS:
            raise SystemExit(f"unknown provider {name!r}; known: {sorted(ADAPTERS)}")
        out[name] = asdict(run_provider(name, spec, http))
    return {
        "schema_version": "street-imagery-survey/1",
        "survey_id": spec.get("survey_id"),
        "tool_version": TOOL_VERSION,
        "surveyed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "user_agent": http.user_agent,
        "target": spec.get("target"),
        "aoi_bbox_lonlat": spec.get("aoi_bbox_lonlat"),
        "radius_ladder_m": spec.get("radius_ladder_m"),
        "state_tokens": [STATE_ABSENT, STATE_PRESENT, STATE_UNKNOWN, STATE_NOT_SURVEYED],
        "providers": out,
        "request_count": len(http.log),
    }


def load_probe_spec(path: Path) -> Dict[str, Any]:
    """Load a probe spec and resolve an AOI polygon from a GeoJSON owner."""
    path = path.resolve()
    spec = json.loads(path.read_text(encoding="utf-8"))
    aoi_ref = spec.get("aoi_geojson")
    if not aoi_ref:
        return spec
    aoi_path = Path(str(aoi_ref))
    if not aoi_path.is_absolute():
        project_candidate = Path(__file__).resolve().parents[2] / aoi_path
        aoi_path = (
            project_candidate
            if project_candidate.is_file()
            else path.parent / aoi_path
        )
    document = json.loads(aoi_path.read_text(encoding="utf-8"))
    role = str(spec.get("aoi_feature_role") or "aoi")
    features = [
        feature for feature in document.get("features", [])
        if (feature.get("properties") or {}).get("role") == role
    ]
    if len(features) != 1:
        raise ValueError(f"AOI GeoJSON must contain exactly one role={role!r} feature")
    geometry = features[0].get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") != "Polygon" or not coordinates or len(coordinates[0]) < 4:
        raise ValueError("AOI feature must be a Polygon with an exterior ring")
    spec["aoi_polygon_lonlat"] = coordinates[0]
    spec["aoi_geometry_source"] = str(aoi_path.resolve())
    return spec


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", help="probe spec JSON")
    ap.add_argument("--out", help="write the survey JSON here")
    ap.add_argument("--providers", help="comma-separated subset of providers to run")
    ap.add_argument("--list-providers", action="store_true")
    args = ap.parse_args(argv)

    if args.list_providers:
        for name, cls in sorted(ADAPTERS.items()):
            print(f"{name:22s} credential_required={cls.needs_credential}")
        return 0
    if not args.spec:
        ap.error("--spec is required")

    spec = load_probe_spec(Path(args.spec))
    names = args.providers.split(",") if args.providers else None
    survey = run_survey(spec, names)
    text = json.dumps(survey, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        for name, res in survey["providers"].items():
            print(f"{name:22s} {res['state']:28s} pictures={res['counts'].get('pictures')} "
                  f"control={res['control'].get('pictures')}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
