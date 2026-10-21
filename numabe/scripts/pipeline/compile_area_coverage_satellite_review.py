#!/usr/bin/env python3
"""Compile a satellite/OSM review map for covered and missing route sections.

Remote tiles are displayed in the browser and are never downloaded or admitted
as reconstruction evidence.  The output exposes coverage truth, required
landmarks, and the operator-reported search zone without pretending that aerial
pixels replace missing street-level observations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# FL-4810: keep personal aerial notes spatial, autosaved, and pixel-free.
SPATIAL_NOTES_SCHEMA = "y92.personal_spatial_notes.v1"
NOTE_ID_RE = re.compile(r"note-[0-9]+-[a-z0-9]+")
MAX_NOTE_CHARS = 4000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _feature(geojson: dict[str, Any], role: str) -> dict[str, Any]:
    rows = [
        row for row in geojson.get("features", [])
        if (row.get("properties") or {}).get("role") == role
    ]
    if len(rows) != 1:
        raise ValueError(f"scope geometry must contain one role={role!r} feature")
    return rows[0]


def _canonical_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SpatialNotesStore:
    """Atomic persistence for operator-authored geographic notes only."""

    def __init__(self, path: Path, *, scope_id: str):
        self.path = path.resolve()
        self.scope_id = scope_id
        self._lock = threading.Lock()

    def empty_receipt(self) -> dict[str, Any]:
        return {
            "schema": SPATIAL_NOTES_SCHEMA,
            "scope_id": self.scope_id,
            "stage": "operator_personal_review_in_progress",
            "created_at": _now(),
            "updated_at": _now(),
            "source_policy": {
                "role": "personal_visual_note",
                "remote_imagery_pixels_retained": False,
                "reconstruction_evidence": False,
            },
            "notes": [],
        }

    def validate(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("schema") != SPATIAL_NOTES_SCHEMA:
            raise ValueError(f"notes schema must be {SPATIAL_NOTES_SCHEMA!r}")
        if payload.get("scope_id") != self.scope_id:
            raise ValueError("notes scope_id does not match the served review")
        rows = payload.get("notes")
        if not isinstance(rows, list):
            raise ValueError("notes must be a list")
        normalized = self.empty_receipt()
        normalized["created_at"] = str(payload.get("created_at") or _now())
        normalized["updated_at"] = str(payload.get("updated_at") or _now())
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"notes[{index}] must be an object")
            note_id = row.get("note_id")
            if not isinstance(note_id, str) or not NOTE_ID_RE.fullmatch(note_id):
                raise ValueError(f"notes[{index}].note_id is invalid")
            if note_id in seen:
                raise ValueError(f"duplicate note_id: {note_id}")
            seen.add(note_id)
            bounds = row.get("bounds_swen")
            if not isinstance(bounds, list) or len(bounds) != 4 or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in bounds
            ):
                raise ValueError(f"notes[{index}].bounds_swen must contain four numbers")
            south, west, north, east = (float(value) for value in bounds)
            if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
                raise ValueError(f"notes[{index}].bounds_swen is invalid")
            label = row.get("label") or "unclassified_spatial_note"
            text = row.get("note") or ""
            if not isinstance(label, str) or not label.strip() or len(label) > 120:
                raise ValueError(f"notes[{index}].label is invalid")
            if not isinstance(text, str) or len(text) > MAX_NOTE_CHARS:
                raise ValueError(f"notes[{index}].note is invalid")
            normalized["notes"].append({
                "note_id": note_id,
                "bounds_swen": [south, west, north, east],
                "center_latlon": [(south + north) / 2, (west + east) / 2],
                "label": label.strip(),
                "note": text,
                "custom_authoring_candidate": bool(row.get("custom_authoring_candidate", False)),
                "review_state": "operator_authored_personal_note",
                "source": "remote_satellite_visual_reference",
                "created_at": str(row.get("created_at") or _now()),
                "updated_at": str(row.get("updated_at") or _now()),
            })
        return normalized

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.is_file():
                return self.empty_receipt()
            return self.validate(_read(self.path))

    def save(self, payload: Any) -> dict[str, Any]:
        with self._lock:
            receipt = self.validate(payload)
            receipt["updated_at"] = _now()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            try:
                temporary.write_bytes(_canonical_bytes(receipt))
                temporary.replace(self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return receipt


def compile_review(
    *, scope_path: Path, capture_index_path: Path, route_manifest_path: Path,
    output_html: Path, output_receipt: Path,
) -> dict[str, Any]:
    scope_path = scope_path.resolve()
    capture_index_path = capture_index_path.resolve()
    route_manifest_path = route_manifest_path.resolve()
    scope = _read(scope_path)
    capture_index = _read(capture_index_path)
    route_manifest = _read(route_manifest_path)
    if scope.get("schema") != "y92.connected_product_scope.v1":
        raise ValueError("unsupported product scope schema")
    if capture_index.get("schema") != "y92.capture_source_index.v1":
        raise ValueError("unsupported capture index schema")
    if route_manifest.get("schema") != "y92.route_capture_manifest.v1":
        raise ValueError("unsupported route manifest schema")

    geometry_path = scope_path.with_name("connected_scope.geojson")
    scope_receipt_path = scope_path.with_name("scope_receipt.json")
    geometry = _read(geometry_path)
    scope_receipt = _read(scope_receipt_path)
    aoi = _feature(geometry, "aoi")["geometry"]["coordinates"][0]
    route_feature = _feature(geometry, "connected_playable_route")
    route = route_feature["geometry"]["coordinates"]
    prefix_m = float(
        scope_receipt["route_composition"]["preserved_base_route_length_m"]
    )
    captures = [
        {
            "capture_id": row["capture_id"],
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "chainage_m": float(
                row["acquisition_route_projection"]["chainage_m"]
            ),
        }
        for row in capture_index.get("captures", [])
    ]
    added = [row for row in captures if row["chainage_m"] > prefix_m]
    coverage = route_manifest.get("coverage") or {}
    payload = {
        "schema": "y92.area_coverage_satellite_review.v1",
        "warning": (
            "Satellite is visual verification only. It is not retained, boxed, "
            "or admitted as street-level reconstruction evidence."
        ),
        "scope_id": scope["scope_id"],
        "aoi_ring_lonlat": aoi,
        "route_lonlat": route,
        "route_length_m": float(
            (route_feature.get("properties") or {})["length_m"]
        ),
        "preserved_prefix_length_m": prefix_m,
        "aggregate_covered_intervals_m": coverage.get(
            "aggregate_covered_intervals_m", []
        ),
        "captures": captures,
        "mapped_landmarks": scope.get("mapped_landmarks", []),
        "route_landmarks": scope.get("route_landmarks", []),
        "operator_reported_search_zones": scope.get(
            "operator_reported_search_zones", []
        ),
        "counts": {
            "retained_cameras": len(captures),
            "cameras_on_required_additions": len(added),
            "required_additions_have_street_imagery": bool(added),
        },
        "sources": {
            "scope": {"path": str(scope_path), "sha256": _sha256(scope_path)},
            "geometry": {
                "path": str(geometry_path), "sha256": _sha256(geometry_path)
            },
            "scope_receipt": {
                "path": str(scope_receipt_path),
                "sha256": _sha256(scope_receipt_path),
            },
            "capture_index": {
                "path": str(capture_index_path),
                "sha256": _sha256(capture_index_path),
            },
            "route_manifest": {
                "path": str(route_manifest_path),
                "sha256": _sha256(route_manifest_path),
            },
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Numabe required-area coverage verification</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body,#map{{height:100%;margin:0}} body{{font:13px/1.4 ui-monospace,Menlo,monospace}}
.panel{{position:absolute;z-index:1000;top:10px;left:50px;max-width:660px;background:#111e;color:#eee;padding:10px 12px;border:1px solid #777;border-radius:5px}}
.bad{{color:#ff7373;font-weight:700}} .good{{color:#71d59a}} .legend{{margin-top:6px}}
.note-panel{{position:absolute;z-index:1000;right:10px;top:10px;width:330px;max-height:calc(100% - 20px);overflow:auto;background:#111e;color:#eee;padding:12px;border:1px solid #777;border-radius:5px}}
button,input,textarea{{font:inherit}} button{{margin:3px 2px;padding:5px 7px}} textarea,input{{box-sizing:border-box;width:100%;margin-top:5px;background:#222;color:#fff;border:1px solid #777;padding:6px}} textarea{{height:88px}} .chips button{{font-size:11px}} .selected{{outline:2px solid #71d59a}} .note-row{{border-top:1px solid #555;padding:7px 0;cursor:pointer}} .hint{{color:#bbb;font-size:11px}} #save-state{{color:#71d59a}}
</style></head><body><div id="map"></div><div class="panel">
<div class="bad">REQUIRED ADDITIONS: ZERO STREET-LEVEL CAMERAS</div>
<div>Satellite/OSM verification only. Satellite pixels are not retained or admitted as reconstruction evidence.</div>
<div class="legend"><span class="good">GREEN</span> captured route · ORANGE uncovered preserved prefix · <span class="bad">RED required additions</span> · BLUE camera</div>
<div><button id="draw">DRAW BOX</button> Drag a box over the map; its coordinates save immediately.</div></div>
<aside class="note-panel"><strong>PERSONAL SPATIAL NOTES</strong>
<div class="hint">Only coordinates, category, and your text are saved. No satellite pixels are copied.</div>
<div id="save-state">loading notes…</div>
<div class="chips" id="chips"></div>
<input id="label" placeholder="category (new boxes start unclassified)">
<textarea id="note" placeholder="What should the pipeline learn or author here?"></textarea>
<label><input id="custom" type="checkbox" style="width:auto"> custom authoring / identity-specific</label><br>
<button id="delete">DELETE SELECTED</button> <a href="https://www.google.com/maps/@35.5849672,139.6747716,18z/data=!3m1!1e3" target="_blank" style="color:#7fd7ff">open Google satellite</a>
<div id="note-list"></div></aside>
<script>const D={encoded};
const map=L.map('map');
const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{attribution:'Tiles: Esri, Maxar, Earthstar',maxZoom:20}}).addTo(map);
const latlng=p=>[p[1],p[0]];
const covered=x=>D.aggregate_covered_intervals_m.some(r=>x>=r[0]&&x<=r[1]);
L.polygon(D.aoi_ring_lonlat.map(latlng),{{color:'#f0b44d',weight:2,fill:false,dashArray:'6,5'}}).addTo(map).bindPopup('Complete product AOI');
let cumulative=0; for(let i=1;i<D.route_lonlat.length;i++){{const a=D.route_lonlat[i-1],b=D.route_lonlat[i];const dy=(b[1]-a[1])*111320,dx=(b[0]-a[0])*111320*Math.cos((a[1]+b[1])/2*Math.PI/180);const length=Math.hypot(dx,dy),mid=cumulative+length/2;const color=mid>D.preserved_prefix_length_m?'#ff4d4d':(covered(mid)?'#35d07f':'#f0a33a');L.polyline([latlng(a),latlng(b)],{{color,weight:mid>D.preserved_prefix_length_m?7:5,opacity:.95}}).addTo(map).bindPopup(`route ${{cumulative.toFixed(1)}}–${{(cumulative+length).toFixed(1)}} m`);cumulative+=length;}}
D.captures.forEach(c=>L.circleMarker([c.lat,c.lon],{{radius:3,color:'#4ac7ff',weight:1,fillOpacity:.8}}).addTo(map).bindPopup(`${{c.capture_id}}<br>${{c.chainage_m.toFixed(1)}} m`));
D.mapped_landmarks.forEach(x=>L.marker(latlng(x.lonlat)).addTo(map).bindPopup(`${{x.name}}<br>${{x.source_ref}}`));
D.route_landmarks.forEach(x=>(x.required_route_pass_points_lonlat||[]).forEach(p=>L.circleMarker(latlng(p),{{radius:8,color:'#ff66dd',weight:3,fill:false}}).addTo(map).bindPopup(`${{x.name}} required route point`)));
D.operator_reported_search_zones.forEach(x=>L.circle(latlng(x.center_lonlat),{{radius:x.radius_m,color:'#fff',weight:2,fillOpacity:.08}}).addTo(map).bindPopup(`${{x.zone_id}}<br>${{x.note}}`));
const bounds=L.latLngBounds(D.aoi_ring_lonlat.map(latlng));map.fitBounds(bounds.pad(.05));
const $=id=>document.getElementById(id), categoryOptions=['boundary_wall','pedestrian_surface','crossing_or_curb','rail_guard_fence','vegetation','building_setback','roof_or_overhang','utility_fixture','landmark','custom_identity_feature'];
let receipt=null,selectedId=null,drawing=false,start=null,draft=null,saveTimer=null;const noteLayers=new Map();
function esc(x){{return String(x??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}
function selected(){{return receipt?.notes.find(x=>x.note_id===selectedId)}}
function render(){{for(const layer of noteLayers.values())map.removeLayer(layer);noteLayers.clear();for(const row of receipt?.notes||[]){{const layer=L.rectangle([[row.bounds_swen[0],row.bounds_swen[1]],[row.bounds_swen[2],row.bounds_swen[3]]],{{color:row.note_id===selectedId?'#71d59a':'#ffe66d',weight:3,fillOpacity:.1}}).addTo(map);layer.on('click',()=>select(row.note_id));noteLayers.set(row.note_id,layer)}}const row=selected();$('label').value=row?.label||'';$('note').value=row?.note||'';$('custom').checked=!!row?.custom_authoring_candidate;$('note-list').innerHTML=(receipt?.notes||[]).slice().reverse().map(x=>`<div class="note-row ${{x.note_id===selectedId?'selected':''}}" data-id="${{esc(x.note_id)}}"><strong>${{esc(x.label)}}</strong><br><span class="hint">${{esc(x.center_latlon.map(v=>v.toFixed(7)).join(', '))}} · ${{esc(x.note||'no note yet')}}</span></div>`).join('');document.querySelectorAll('.note-row').forEach(x=>x.onclick=()=>select(x.dataset.id))}}
function select(id){{selectedId=id;render()}}
async function save(){{if(!receipt)return;$('save-state').textContent='saving…';const response=await fetch('/api/spatial-notes',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(receipt)}});if(!response.ok)throw new Error(await response.text());receipt=await response.json();$('save-state').textContent=`saved ${{receipt.notes.length}} note${{receipt.notes.length===1?'':'s'}}`;render()}}
function queueSave(){{clearTimeout(saveTimer);saveTimer=setTimeout(()=>save().catch(e=>$('save-state').textContent='SAVE FAILED: '+e.message),250)}}
function updateSelected(){{const row=selected();if(!row)return;row.label=$('label').value.trim()||'unclassified_spatial_note';row.note=$('note').value;row.custom_authoring_candidate=$('custom').checked;row.updated_at=new Date().toISOString();render();queueSave()}}
$('chips').innerHTML=categoryOptions.map(x=>`<button data-category="${{x}}">${{x.replaceAll('_',' ')}}</button>`).join('');document.querySelectorAll('[data-category]').forEach(x=>x.onclick=()=>{{if(!selected())return;$('label').value=x.dataset.category;updateSelected()}});$('label').oninput=updateSelected;$('note').oninput=updateSelected;$('custom').onchange=updateSelected;
$('delete').onclick=async()=>{{if(!selected())return;receipt.notes=receipt.notes.filter(x=>x.note_id!==selectedId);selectedId=null;await save()}};
$('draw').onclick=()=>{{drawing=!drawing;$('draw').textContent=drawing?'CANCEL DRAW':'DRAW BOX';map.getContainer().style.cursor=drawing?'crosshair':'';if(drawing)map.dragging.disable();else map.dragging.enable()}};
map.getContainer().addEventListener('mousedown',event=>{{if(!drawing)return;event.preventDefault();start=map.mouseEventToLatLng(event);draft=L.rectangle([start,start],{{color:'#71d59a',weight:3,dashArray:'5,4'}}).addTo(map)}});
window.addEventListener('mousemove',event=>{{if(!draft||!start)return;draft.setBounds(L.latLngBounds(start,map.mouseEventToLatLng(event)))}});
window.addEventListener('mouseup',async event=>{{if(!draft||!start)return;const end=map.mouseEventToLatLng(event),south=Math.min(start.lat,end.lat),north=Math.max(start.lat,end.lat),west=Math.min(start.lng,end.lng),east=Math.max(start.lng,end.lng);map.removeLayer(draft);draft=null;start=null;drawing=false;map.dragging.enable();map.getContainer().style.cursor='';$('draw').textContent='DRAW BOX';if(north-south<1e-7||east-west<1e-7)return;const now=Date.now(),row={{note_id:`note-${{now}}-${{Math.random().toString(36).slice(2,8)}}`,bounds_swen:[south,west,north,east],center_latlon:[(south+north)/2,(west+east)/2],label:'unclassified_spatial_note',note:'',custom_authoring_candidate:false,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}};receipt.notes.push(row);selectedId=row.note_id;render();try{{await save()}}catch(e){{$('save-state').textContent='SAVE FAILED: '+e.message}}}});
satellite.on('tileerror',()=>$('save-state').textContent='SATELLITE TILE FAILED — use the Google satellite link');
(async()=>{{try{{const response=await fetch('/api/spatial-notes');if(!response.ok)throw new Error(await response.text());receipt=await response.json();$('save-state').textContent=`saved ${{receipt.notes.length}} notes`;render()}}catch(e){{$('save-state').textContent='NOTES API UNAVAILABLE — reopen through the loopback server'}}}})();
</script></body></html>"""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")
    payload["output"] = {
        "html_path": str(output_html.resolve()),
        "html_sha256": _sha256(output_html),
        "remote_tile_persistence": False,
        "evidence_role": "visual_verification_only",
    }
    output_receipt.write_bytes(_canonical_bytes(payload))
    return payload


def make_handler(*, html_path: Path, store: SpatialNotesStore):
    html_path = html_path.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Y92SpatialReview/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path in ("/", "/coverage_satellite_review.html"):
                    self._send(HTTPStatus.OK, html_path.read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/spatial-notes":
                    self._send(HTTPStatus.OK, _canonical_bytes(store.load()), "application/json; charset=utf-8")
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
            except (OSError, ValueError) as error:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, (str(error) + "\n").encode(), "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/spatial-notes":
                self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2_000_000:
                    raise ValueError("invalid request size")
                saved = store.save(json.loads(self.rfile.read(length)))
                self._send(HTTPStatus.OK, _canonical_bytes(saved), "application/json; charset=utf-8")
            except (json.JSONDecodeError, OSError, ValueError) as error:
                self._send(HTTPStatus.BAD_REQUEST, (str(error) + "\n").encode(), "text/plain; charset=utf-8")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--capture-index", type=Path, required=True)
    parser.add_argument("--route-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    result = compile_review(
        scope_path=args.scope, capture_index_path=args.capture_index,
        route_manifest_path=args.route_manifest, output_html=args.out,
        output_receipt=args.receipt,
    )
    print(json.dumps(result["counts"], sort_keys=True))
    print(f"wrote {args.out}")
    if args.serve:
        if args.notes is None:
            parser.error("--serve requires --notes")
        store = SpatialNotesStore(args.notes, scope_id=result["scope_id"])
        server = ThreadingHTTPServer(
            (args.host, args.port), make_handler(html_path=args.out, store=store)
        )
        url = f"http://{args.host}:{args.port}/coverage_satellite_review.html"
        print(f"serving {url}")
        print(f"personal notes: {args.notes.resolve()}")
        if args.open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    elif args.open_browser:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
