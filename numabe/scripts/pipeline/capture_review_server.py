#!/usr/bin/env python3
"""Serve a local, pose-synchronized review UI for licensed capture indexes.

FL-4813: this tool records operator annotations against retained source images.
It does not promote geometry, accept a camera solve, or satisfy a reconstruction
gate by itself.  The server binds to loopback and never fetches remote imagery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import threading
import webbrowser
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil, floor
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from PIL import Image, UnidentifiedImageError


CAPTURE_INDEX_SCHEMA = "y92.capture_source_index.v1"
REVIEW_SCHEMA = "y92.capture_review.v1"
AGENT_SCHEMA = "y92.capture_review_agent.v1"
MAX_REQUEST_BYTES = 8 << 20
MAX_NOTE_CHARS = 4000
ANNOTATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
COMMON_FIXTURE_CLASSES = (
    "utility_pole",
    "vending_machine",
    "traffic_sign",
    "street_lamp",
    "post_box",
    "public_telephone",
    "bicycle_parking",
    "bus_stop",
    "stop_sign",
    "street_cabinet",
    "pedestrian_crossing_marker",
    "tree",
    "traffic_signal",
    "advertising_sign",
    "bollard",
    "information_board",
    "bench",
    "fire_hydrant",
)
SURFACE_SUGGESTIONS = (
    "air_conditioner",
    "facade",
    "shopfront",
    "door",
    "window",
    "shutter",
    "awning",
    "balcony",
    "fence",
    "guardrail",
    "retaining_wall",
    "hedge",
    "tactile_paving",
    "road_marking",
    "crosswalk",
    "curb",
    "sidewalk",
    "overhead_wire",
)


APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Y92 licensed capture review</title>
<style>
:root { color-scheme: dark; --ink:#e9e3d4; --dim:#9a958a; --panel:#171716;
  --line:#393733; --accent:#f0b44d; --ok:#71d59a; --danger:#ff7373; }
* { box-sizing:border-box; }
body { margin:0; background:#0d0d0c; color:var(--ink); font:14px/1.35 ui-monospace,Menlo,monospace; }
button,input,select,textarea { font:inherit; color:inherit; background:#23221f; border:1px solid var(--line); border-radius:4px; }
button { padding:7px 11px; cursor:pointer; } button:hover { border-color:var(--accent); }
header { display:flex; gap:9px; align-items:center; padding:9px 12px; border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; background:#111; }
header .grow { flex:1; } #status { color:var(--ok); }
header input { width:190px; padding:7px; }
main { display:grid; grid-template-columns:minmax(480px,1fr) 390px; min-height:calc(100vh - 50px); }
#viewer { padding:12px; min-width:0; }
#stage { position:relative; width:100%; height:calc(100vh - 150px); min-height:360px; background:#050505; overflow:hidden; user-select:none; }
#image { width:100%; height:100%; object-fit:contain; display:block; }
#overlay { position:absolute; inset:0; width:100%; height:100%; cursor:crosshair; }
#timeline { width:100%; }
#controls { display:grid; grid-template-columns:auto auto 1fr auto; gap:8px; align-items:center; padding-top:9px; }
aside { border-left:1px solid var(--line); background:var(--panel); padding:12px; overflow:auto; max-height:calc(100vh - 50px); }
h2 { margin:12px 0 7px; font-size:14px; color:var(--accent); }
.field { display:grid; gap:4px; margin-bottom:8px; } input,select,textarea { width:100%; padding:7px; }
textarea { min-height:70px; resize:vertical; }
#map { width:100%; height:220px; background:#0b1011; border:1px solid var(--line); }
#meta { white-space:pre-wrap; color:var(--dim); font-size:12px; }
.row { display:flex; gap:7px; }.row>* { flex:1; }
#suggestions { display:grid; gap:7px; }
.suggestion-group { display:flex; flex-wrap:wrap; gap:5px; }
.suggestion-group h3 { width:100%; margin:2px 0; color:var(--dim); font-size:12px; }
.chip { padding:4px 7px; font-size:11px; }
.chip.active { color:#111; background:var(--accent); border-color:var(--accent); }
#ann-list { display:grid; gap:6px; }
.ann { border:1px solid var(--line); padding:7px; border-radius:4px; }
.ann.selected { border-color:var(--ok); }
.ann strong { color:var(--accent); }.ann button { float:right; padding:2px 6px; }
.ann-main { cursor:pointer; display:grid; grid-template-columns:76px 1fr; gap:8px; align-items:start; }
.ann img { width:76px; height:60px; object-fit:contain; background:#050505; border:1px solid var(--line); }
.api-link { color:var(--ok); }
.hint { color:var(--dim); font-size:12px; }.danger { color:var(--danger); }
@media (max-width:900px) { main { grid-template-columns:1fr; } aside { border-left:0; border-top:1px solid var(--line); max-height:none; } }
</style>
</head>
<body>
<header>
  <button id="prev" title="Previous frame (left arrow)">◀</button>
  <button id="play" title="Play or pause (space)">▶</button>
  <button id="next" title="Next frame (right arrow)">▶|</button>
  <select id="sequence" title="Sequence filter"></select>
  <input id="jump" placeholder="capture ID or chainage" title="Press Enter to jump">
  <span class="grow" id="frame-title">Loading…</span>
  <span id="status">local licensed media</span>
</header>
<main>
  <section id="viewer">
    <div id="stage"><img id="image" alt="Licensed source capture"><canvas id="overlay"></canvas></div>
    <input id="timeline" type="range" min="0" max="0" value="0">
    <div id="controls">
      <span id="counter">0 / 0</span>
      <label>FPS <input id="fps" type="number" min="0.25" max="12" step="0.25" value="2" style="width:70px"></label>
      <span class="hint">Drag a box on the image. Arrow keys move frames. Space toggles playback.</span>
      <button id="clear-box">Clear box</button>
    </div>
  </section>
  <aside>
    <canvas id="map"></canvas>
    <div id="meta"></div>
    <h2>One-shot type / selected box</h2>
    <div id="suggestions"></div>
    <div class="field"><label>Label</label><input id="label" placeholder="Choose one type, then draw one box"></div>
    <div class="field"><label>Parent OSM feature</label><input id="parent" placeholder="way/1507403695"></div>
    <div class="field"><label>Evidence role</label><select id="role">
      <option value="appearance_observation">appearance observation</option>
      <option value="semantic_observation">semantic observation</option>
      <option value="ground_contact">ground contact</option>
      <option value="vertical_direction">vertical direction</option>
      <option value="correspondence_candidate">correspondence candidate</option>
      <option value="reject">reject</option>
    </select></div>
    <div class="field"><label>Notes</label><textarea id="note" placeholder="What is visible, what is uncertain, and why this correspondence is credible."></textarea></div>
    <div class="row"><button id="save">Save fields now</button><button id="download">Download receipt</button></div>
    <p class="hint">A valid drag saves immediately, then clears the type so it cannot leak into the next box. A drag without a chosen type saves as unclassified_fixture. Select an existing crop to edit it; field edits auto-save after 350 ms.</p>
    <p><a class="api-link" href="/api/agent" target="_blank">Open agent JSON (camera + crops)</a> · <a class="api-link" href="/review">Review similarity groups</a></p>
    <h2>Current-frame annotations</h2>
    <div id="ann-list"></div>
  </aside>
</main>
<script>
"use strict";
const S = { config:null, receipt:null, frames:[], index:0, draft:null, drawing:false,
  play:null, selectedId:null, fieldTimer:null, persisting:null, dirty:false, revision:0 };
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function jsonFetch(url, options={}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `${response.status}`);
  return body;
}
function chainage(c) { return c.acquisition_route_projection?.chainage_m ?? Number.POSITIVE_INFINITY; }
function areaOrder(c) { const p=c.street_network_projection||{}; return [Number(p.way_id)||Number.MAX_SAFE_INTEGER,p.way_chainage_m??Number.POSITIVE_INFINITY]; }
function rebuildFrames() {
  const wanted = $('sequence').value;
  S.frames = S.config.captures.filter(c => wanted === '*' || c.sequence_id === wanted)
    .sort((a,b) => { if(S.config.area_coverage){const x=areaOrder(a),y=areaOrder(b),d=x[0]-y[0]||x[1]-y[1];if(d)return d;} return chainage(a)-chainage(b) || String(a.sequence_id).localeCompare(String(b.sequence_id)) || (a.sequence_index??0)-(b.sequence_index??0); });
  S.index = Math.min(S.index, Math.max(0, S.frames.length-1));
  $('timeline').max = Math.max(0, S.frames.length-1);
  showFrame();
}
function current() { return S.frames[S.index]; }
function imageContentRect() {
  const img = $('image'), outer = img.getBoundingClientRect();
  if (!img.naturalWidth || !img.naturalHeight) return {x:0,y:0,w:outer.width,h:outer.height};
  const scale = Math.min(outer.width/img.naturalWidth, outer.height/img.naturalHeight);
  const w=img.naturalWidth*scale, h=img.naturalHeight*scale;
  return {x:(outer.width-w)/2, y:(outer.height-h)/2, w, h};
}
function drawOverlay() {
  const canvas=$('overlay'), dpr=window.devicePixelRatio||1, rect=canvas.getBoundingClientRect();
  canvas.width=Math.round(rect.width*dpr); canvas.height=Math.round(rect.height*dpr);
  const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr); const ir=imageContentRect();
  const anns=S.receipt.annotations.filter(a => current() && a.capture_id===current().capture_id);
  for (const [box,color] of [...anns.map(a=>[a.box_norm,a.annotation_id===S.selectedId?'#71d59a':'#f0b44d']), ...(S.draft?[[S.draft,'#71d59a']]:[])]) {
    ctx.strokeStyle=color; ctx.lineWidth=2; ctx.strokeRect(ir.x+box[0]*ir.w,ir.y+box[1]*ir.h,box[2]*ir.w,box[3]*ir.h);
  }
}
function drawMap() {
  const canvas=$('map'), dpr=window.devicePixelRatio||1, r=canvas.getBoundingClientRect();
  canvas.width=Math.round(r.width*dpr); canvas.height=Math.round(r.height*dpr);
  const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr); ctx.clearRect(0,0,r.width,r.height);
  const points=S.config.captures.filter(c=>Number.isFinite(c.lat)&&Number.isFinite(c.lon));
  const route=(S.config.route?.coordinates_lonlat||[]).filter(p=>Array.isArray(p)&&Number.isFinite(p[0])&&Number.isFinite(p[1]));
  const area=S.config.area_coverage||{}, ring=area.aoi?.ring_lonlat||[], segments=area.segments||[];
  if (!points.length && !route.length && !ring.length) return;
  const all=[...points.map(p=>[p.lon,p.lat]),...route,...ring,...segments.flatMap(s=>[s.start_lonlat,s.end_lonlat])], lats=all.map(p=>p[1]), lons=all.map(p=>p[0]), pad=16;
  const minLat=Math.min(...lats), maxLat=Math.max(...lats), minLon=Math.min(...lons), maxLon=Math.max(...lons);
  const xyLonLat=p=>[pad+(p[0]-minLon)/(maxLon-minLon||1)*(r.width-2*pad), r.height-pad-(p[1]-minLat)/(maxLat-minLat||1)*(r.height-2*pad)];
  const xy=p=>xyLonLat([p.lon,p.lat]);
  if(ring.length){ctx.strokeStyle='#f0b44d';ctx.lineWidth=1;ctx.beginPath();ring.forEach((p,i)=>{const [x,y]=xyLonLat(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.closePath();ctx.stroke();}
  ctx.strokeStyle='#334a50';ctx.lineWidth=1;for(const s of segments){const a=xyLonLat(s.start_lonlat),b=xyLonLat(s.end_lonlat);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
  if (route.length) {
    ctx.strokeStyle='#9a958a'; ctx.lineWidth=3; ctx.beginPath(); route.forEach((p,i)=>{const [x,y]=xyLonLat(p); i?ctx.lineTo(x,y):ctx.moveTo(x,y);}); ctx.stroke();
    for (const [p,label] of [[route[0],'H'],[route[route.length-1],'S']]) { const [x,y]=xyLonLat(p); ctx.fillStyle='#e9e3d4'; ctx.beginPath(); ctx.arc(x,y,7,0,2*Math.PI); ctx.fill(); ctx.fillStyle='#111'; ctx.font='10px ui-monospace'; ctx.textAlign='center'; ctx.textBaseline='middle'; ctx.fillText(label,x,y); }
  }
  const ordered=[...points].sort((a,b)=>chainage(a)-chainage(b));
  ctx.strokeStyle='#4f666a'; ctx.lineWidth=2; ctx.beginPath(); ordered.forEach((p,i)=>{const [x,y]=xy(p); i?ctx.lineTo(x,y):ctx.moveTo(x,y);}); ctx.stroke();
  ctx.fillStyle='#52676b'; for (const p of points) { const [x,y]=xy(p); ctx.fillRect(x-1,y-1,2,2); }
  const c=current(); if (!c || !Number.isFinite(c.lat)||!Number.isFinite(c.lon)) return;
  const [x,y]=xy(c), angle=(Number(c.heading_deg||0)-90)*Math.PI/180;
  ctx.fillStyle='#f0b44d'; ctx.beginPath(); ctx.arc(x,y,5,0,2*Math.PI); ctx.fill();
  ctx.strokeStyle='#f0b44d'; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(x+18*Math.cos(angle),y+18*Math.sin(angle)); ctx.stroke();
}
function showFrame() {
  const c=current(); if (!c) return;
  S.draft=null; S.selectedId=null; $('image').src=c.image_url; $('timeline').value=S.index;
  $('counter').textContent=`${S.index+1} / ${S.frames.length}`;
  $('frame-title').textContent=c.capture_id;
  const p=c.acquisition_route_projection||{};
  const n=c.street_network_projection||{};
  $('meta').textContent=[
    `lat/lon  ${c.lat}, ${c.lon}`, `heading  ${c.heading_deg ?? 'unknown'}°`,
    `chainage ${p.chainage_m ?? 'unknown'} m   lateral ${p.lateral_m ?? 'unknown'} m`,
    `street   way/${n.way_id ?? 'unknown'} ${n.name ?? ''} (${n.highway ?? 'unknown'})`,
    `street m ${n.way_chainage_m ?? 'unknown'}   lateral ${n.lateral_m ?? 'unknown'} m`,
    `sequence ${c.sequence_id ?? 'none'} #${c.sequence_index ?? '?'}`, `projection ${c.projection_model}`,
    `licence  ${c.license}`, `credit   ${c.attribution}`, `sha256   ${c.sha256}`
  ].join('\n');
  $('note').value='';
  drawMap(); renderAnnotations();
}
function move(delta) { if (!S.frames.length) return; S.index=(S.index+delta+S.frames.length)%S.frames.length; showFrame(); }
function jump() {
  const query=$('jump').value.trim(); if(!query)return;
  let found=S.frames.findIndex(c=>c.capture_id===query||c.capture_id===`mapillary_${query}`);
  if(found<0 && Number.isFinite(Number(query))) {
    const target=Number(query); let best=Number.POSITIVE_INFINITY;
    S.frames.forEach((c,i)=>{const d=Math.abs(chainage(c)-target);if(d<best){best=d;found=i;}});
  }
  if(found>=0){S.index=found;showFrame();$('status').textContent='jumped to synchronized frame';}
  else $('status').textContent='capture not found in current sequence filter';
}
function stop() { if (S.play) clearInterval(S.play); S.play=null; $('play').textContent='▶'; }
function togglePlay() { if (S.play) return stop(); const fps=Math.max(.25,Math.min(12,Number($('fps').value)||2)); S.play=setInterval(()=>move(1),1000/fps); $('play').textContent='Ⅱ'; }
function pointNorm(event) {
  const canvas=$('overlay'), outer=canvas.getBoundingClientRect(), ir=imageContentRect();
  const x=event.clientX-outer.left-ir.x, y=event.clientY-outer.top-ir.y;
  if (x<0||y<0||x>ir.w||y>ir.h) return null;
  return [x/ir.w,y/ir.h];
}
function renderAnnotations() {
  const c=current(), list=$('ann-list'); if (!c) return;
  const anns=S.receipt.annotations.filter(a=>a.capture_id===c.capture_id); list.innerHTML='';
  if (!anns.length) list.innerHTML='<span class="hint">None yet.</span>';
  for (const a of anns) { const div=document.createElement('div'); div.className=`ann${a.annotation_id===S.selectedId?' selected':''}`;
    const camera=a.capture_snapshot||{}, route=camera.acquisition_route_projection||{};
    div.innerHTML=`<button data-delete="${esc(a.annotation_id)}" title="Delete annotation">×</button><div class="ann-main" data-select="${esc(a.annotation_id)}"><img src="${esc(a.crop?.url)}" alt="Crop for ${esc(a.label)}"><div><strong>${esc(a.label)}</strong><br>${esc(a.evidence_role)}${a.parent_feature_id?` · ${esc(a.parent_feature_id)}`:''}<br><span class="hint">${esc(camera.lat)}, ${esc(camera.lon)} · ${esc(camera.heading_deg)}°<br>chainage ${esc(route.chainage_m)} m · crop ${esc(a.crop?.width)}×${esc(a.crop?.height)} px<br>${esc(a.note)}</span></div></div>`; list.appendChild(div); }
  list.querySelectorAll('[data-select]').forEach(node=>node.onclick=()=>selectAnnotation(node.dataset.select));
  list.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>removeAnnotation(b.dataset.delete)); drawOverlay();
}
function setStatus(message, danger=false) { $('status').textContent=message; $('status').className=danger?'danger':''; }
function clearEditorForNextBox() {
  S.selectedId=null; $('label').value=''; $('parent').value=''; $('role').value='appearance_observation'; $('note').value='';
  renderSuggestions(); renderAnnotations();
}
async function persist() {
  S.dirty=true;
  if (S.persisting) return S.persisting;
  S.persisting=(async()=>{
    while(S.dirty) {
      S.dirty=false; setStatus('saving…'); const submittedRevision=S.revision;
      const saved=await jsonFetch('/api/annotations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(S.receipt)});
      if(submittedRevision===S.revision)S.receipt=saved;
      else { const enriched=new Map(saved.annotations.map(a=>[a.annotation_id,a]));
        for(const currentRow of S.receipt.annotations) { const serverRow=enriched.get(currentRow.annotation_id); if(serverRow){currentRow.crop=serverRow.crop;currentRow.capture_snapshot=serverRow.capture_snapshot;} }
      }
      setStatus('saved locally — crop ready for agent'); renderAnnotations();
    }
  })();
  try { await S.persisting; }
  catch (error) { S.dirty=false; setStatus(`save failed: ${error.message}`,true); }
  finally { S.persisting=null; }
}
async function removeAnnotation(id) {
  S.receipt.annotations=S.receipt.annotations.filter(a=>a.annotation_id!==id);
  S.revision+=1;
  if(S.selectedId===id)S.selectedId=null;
  await persist();
}
function selectAnnotation(id) {
  const a=S.receipt.annotations.find(row=>row.annotation_id===id); if(!a)return;
  S.selectedId=id; $('label').value=a.label; $('parent').value=a.parent_feature_id||'';
  $('role').value=a.evidence_role; $('note').value=a.note||''; renderSuggestions(); renderAnnotations();
}
function updateSelectedFromFields() {
  const a=S.receipt.annotations.find(row=>row.annotation_id===S.selectedId); if(!a)return false;
  a.label=$('label').value.trim()||'unclassified_fixture';
  a.parent_feature_id=$('parent').value.trim()||null; a.evidence_role=$('role').value;
  a.note=$('note').value.trim(); S.revision+=1; return true;
}
function queueFieldSave() {
  renderSuggestions();
  if(!updateSelectedFromFields())return;
  clearTimeout(S.fieldTimer); setStatus('field change pending…');
  S.fieldTimer=setTimeout(()=>persist(),350);
}
async function createAnnotationFromDraft() {
  const c=current(); if(!c||!S.draft)return;
  const annotation={
    annotation_id:`ann-${Date.now()}-${Math.random().toString(16).slice(2,8)}`,
    capture_id:c.capture_id, box_norm:S.draft.map(v=>Number(v.toFixed(7))),
    label:$('label').value.trim()||'unclassified_fixture',
    parent_feature_id:$('parent').value.trim()||null, evidence_role:$('role').value,
    note:$('note').value.trim(), review_state:'operator_authored'
  };
  S.receipt.annotations.push(annotation); S.revision+=1; S.draft=null;
  clearEditorForNextBox(); await persist();
}
async function saveFieldsNow() {
  clearTimeout(S.fieldTimer);
  if(!updateSelectedFromFields()){setStatus('type is ready for the next drag');return;}
  await persist();
}
function renderSuggestions() {
  const root=$('suggestions'), selected=$('label').value.trim(); root.innerHTML='';
  for(const group of S.config.suggestions?.groups||[]) {
    const section=document.createElement('section'); section.className='suggestion-group';
    const title=document.createElement('h3'); title.textContent=group.title; section.appendChild(title);
    for(const item of group.items) { const button=document.createElement('button'); button.type='button';
      button.className=`chip${selected===item.label?' active':''}`; button.textContent=item.display; button.title=item.template_id||item.source;
      button.onclick=()=>{$('label').value=item.label;$('role').value=item.evidence_role;queueFieldSave();}; section.appendChild(button); }
    root.appendChild(section);
  }
}
function download() { const blob=new Blob([JSON.stringify(S.receipt,null,2)+'\n'],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='capture_review_annotations.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000); }
async function init() {
  S.config=await jsonFetch('/api/config'); S.receipt=await jsonFetch('/api/annotations');
  renderSuggestions();
  const seq=[...new Set(S.config.captures.map(c=>c.sequence_id).filter(Boolean))].sort();
  $('sequence').innerHTML='<option value="*">all sequences</option>'+seq.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');
  rebuildFrames();
  const requested=new URLSearchParams(location.search).get('capture'); if(requested){$('jump').value=requested;jump();}
  $('status').textContent=`${S.config.captures.length} licensed local frames`;
}
$('prev').onclick=()=>move(-1); $('next').onclick=()=>move(1); $('play').onclick=togglePlay; $('sequence').onchange=()=>{S.index=0;rebuildFrames();};
$('timeline').oninput=e=>{S.index=Number(e.target.value);showFrame();}; $('clear-box').onclick=()=>{S.draft=null;drawOverlay();}; $('save').onclick=saveFieldsNow; $('download').onclick=download;
$('label').oninput=queueFieldSave; $('parent').oninput=queueFieldSave; $('role').onchange=queueFieldSave; $('note').oninput=queueFieldSave;
$('jump').onkeydown=e=>{if(e.key==='Enter')jump();};
$('image').onload=()=>{drawOverlay();drawMap();}; window.onresize=()=>{drawOverlay();drawMap();};
$('overlay').onpointerdown=e=>{const p=pointNorm(e); if(!p)return; stop(); if(S.selectedId)clearEditorForNextBox(); S.drawing=true; S.draft=[p[0],p[1],0,0]; $('overlay').setPointerCapture(e.pointerId);};
$('overlay').onpointermove=e=>{if(!S.drawing)return; const p=pointNorm(e); if(!p)return; const [x0,y0]=S.draft; S.draft=[Math.min(x0,p[0]),Math.min(y0,p[1]),Math.abs(p[0]-x0),Math.abs(p[1]-y0)]; drawOverlay();};
$('overlay').onpointerup=async e=>{if(!S.drawing)return; S.drawing=false; if(S.draft[2]<.002||S.draft[3]<.002){S.draft=null;drawOverlay();return;} await createAnnotationFromDraft();};
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return; if(e.key==='ArrowLeft')move(-1); else if(e.key==='ArrowRight')move(1); else if(e.key===' '){e.preventDefault();togglePlay();}});
init().catch(error=>{$('status').textContent=`load failed: ${error.message}`;$('status').className='danger';});
</script>
</body>
</html>
"""


GROUP_REVIEW_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Y92 grouped semantic review</title><style>
:root{color-scheme:dark;--bg:#0d0d0c;--panel:#191817;--line:#44413b;--ink:#eee7da;--dim:#a7a096;--accent:#f0b44d;--ok:#72d69a;--no:#ff7777;--capture:#55d8ef}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 ui-monospace,Menlo,monospace}a{color:#8fdfff}header{position:sticky;top:0;z-index:3;display:flex;gap:10px;align-items:center;padding:10px 14px;background:#111;border-bottom:1px solid var(--line)}header .grow{flex:1}button,input,textarea,select{font:inherit;color:inherit;background:#25231f;border:1px solid var(--line);border-radius:5px;padding:8px}button{cursor:pointer}.yes{border-color:var(--ok)}.no{border-color:var(--no)}.correct{border-color:var(--accent)}#coverage{margin:14px 14px 0;padding:12px;border:2px solid var(--no);background:#331b1b;color:#ffd6d6;border-radius:7px}#coverage strong{display:block;font-size:16px}.coverage-detail{margin-top:5px;color:#ffc1c1}main{display:grid;grid-template-columns:minmax(500px,1fr) 390px;gap:14px;padding:14px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:12px}.members{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:9px}.member{border:1px solid var(--line);padding:7px;border-radius:5px;cursor:pointer}.member.selected{border:2px solid var(--accent);padding:6px}.member img{width:100%;height:150px;object-fit:contain;background:#050505}.member small{display:block;color:var(--dim);word-break:break-word;margin-top:6px}.hero img{display:block;width:100%;height:min(45vh,430px);object-fit:contain;background:#050505}.spatial{display:grid;grid-template-columns:minmax(280px,1fr) minmax(260px,.8fr);gap:10px;margin-top:10px}.location{white-space:pre-wrap;word-break:break-word;color:var(--ink)}#map{display:block;width:100%;height:250px;background:#090b0c;border:1px solid var(--line);border-radius:5px}.legend{color:var(--dim);font-size:12px;margin-top:5px}.product-key{color:var(--accent)}.capture-key{color:var(--capture)}label{display:block;margin-top:10px;color:var(--dim)}input,textarea,select{width:100%;margin-top:4px}textarea{min-height:80px}.review-suggestions{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.review-suggestions button{font-size:11px;padding:4px 6px}.review-suggestions h4{width:100%;margin:5px 0 0;color:var(--dim)}.actions{display:grid;grid-template-columns:1fr 1fr 1fr;gap:7px;margin-top:12px}.hint{color:var(--dim)}#status{color:var(--ok)}@media(max-width:900px){main{grid-template-columns:1fr}.spatial{grid-template-columns:1fr}}
</style></head><body><header><button id="prev">←</button><button id="next">→</button><strong id="counter">loading</strong><span class="grow"></span><a href="/">box mode</a><span id="status"></span></header>
<section id="coverage"><strong>LOADING CAPTURE COVERAGE — do not treat these boxes as full-route completion</strong></section>
<main><section><div class="panel hero"><h2 id="title"></h2><img id="hero"><p id="summary" class="hint"></p><div class="spatial"><div><canvas id="map"></canvas><div class="legend"><span class="product-key">━ expanded product route</span> · <span class="capture-key">━ current partial capture route</span> · ● crop camera</div></div><div id="location" class="panel location">Select a crop to inspect its camera pose.</div></div></div><h3>Candidate-similar crops (not merged; each remains an instance)</h3><div id="members" class="members"></div></section>
<aside class="panel"><p class="hint">This is the semantic gate after local visual grouping. The old box label is only a clue. Yes/Correct routes every member; No rejects this grouping and leaves its members unresolved.</p>
<label>Semantic class<input id="label" placeholder="utility_pole, hedge, exterior_air_conditioner…"></label>
<div id="review-suggestions" class="review-suggestions"></div>
<label><input id="custom" type="checkbox" style="width:auto"> custom extraction / authoring required</label>
<div id="custom-fields"><label>Custom asset key<input id="asset" placeholder="numabe_station_identity_sign"></label><label>Authoring kind<select id="kind"><option value="identity_sign_face">identity sign face</option><option value="storefront_identity">storefront identity</option><option value="unique_facade_detail">unique facade detail</option><option value="bespoke_prop">bespoke prop</option></select></label><label>Reason<input id="reason" placeholder="location-specific text/logo/shape"></label><label>Generic carrier class (optional)<input id="carrier" placeholder="sign_panel"></label></div>
<label>Correction/review note<textarea id="note"></textarea></label><div class="actions"><button class="yes" id="yes">YES / accept class</button><button class="correct" id="correct">CORRECT + accept</button><button class="no" id="no">NO / wrong grouping</button></div>
<p class="hint">Every decision autosaves and recompiles the generic/custom/unresolved routing receipt. Agent endpoint: <a href="/api/review-queue">/api/review-queue</a>. Routing: <a href="/api/routing">/api/routing</a>.</p></aside></main>
<script>
const $=id=>document.getElementById(id);let queue=null,groups=[],decisions=null,index=0,selectedMemberId=null;
async function j(url,opt){const r=await fetch(url,opt);const x=await r.json();if(!r.ok)throw Error(x.error||r.status);return x}
function esc(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function fixed(value,digits=1){return Number.isFinite(Number(value))?Number(value).toFixed(digits):'unavailable'}
function current(){return groups[index]}
function existing(){const g=current();return g.review_scope==='annotation'?decisions.annotation_reviews.find(x=>x.annotation_id===g.annotation_id):decisions.group_reviews.find(x=>x.group_id===g.group_id)}
function coverage(){const p=queue?.product_scope,c=queue?.capture_route,n=queue?.counts?.annotations??'the current',fraction=c?.coverage?.aggregate_coverage_fraction;if(!p){$('coverage').innerHTML='<strong>PARTIAL CAPTURE CORPUS — expanded product scope was not supplied to this server.</strong>';return}const state=p.coverage_state||{};$('coverage').innerHTML=`<strong>PARTIAL IMAGERY ONLY: ${esc(n)} boxes come from the existing corpus measured against the ${fixed(c?.length_m,3)} m capture route; they do not establish coverage of the ${fixed(p.route?.length_m,3)} m product route.</strong><div class="coverage-detail">Existing corpus aggregate route coverage: ${Number.isFinite(Number(fraction))?fixed(Number(fraction)*100,1)+'%':'unavailable'}<br>Route: ${esc((p.route_order||[]).join(' → '))}<br>Sakurazaka imagery: ${esc(state.sakurazaka_imagery||'unknown')} · Lawson/coin-laundry extension: ${esc(state.lawson_coin_laundry_extension_imagery||'unknown')}<br>Required next receipt: ${esc(state.required_next_receipt||'expanded route coverage evidence')}</div>`}
function chosen(){const g=current();return g?.members.find(m=>m.annotation_id===selectedMemberId)||g?.members[0]}
function showLocation(){const m=chosen();if(!m)return;const c=m.camera||{},r=m.route_projection||{},p=m.product_route_projection||{},n=c.street_network_projection||{};$('location').innerHTML=`<strong>CAMERA / AREA LOCATION</strong>\nannotation: ${esc(m.annotation_id)}\ncapture_id: ${esc(c.capture_id||m.capture_id||'unavailable')}\nlat: ${fixed(c.lat,7)}\nlon: ${fixed(c.lon,7)}\nheading_deg: ${fixed(c.heading_deg,1)}\nstreet: way/${esc(n.way_id||'unavailable')} ${esc(n.name||'')} (${esc(n.highway||'unavailable')})\nstreet_chainage_m: ${fixed(n.way_chainage_m,2)}\nstreet_lateral_m: ${fixed(n.lateral_m,2)}\ncapture_route_chainage_m: ${fixed(r.chainage_m,2)}\ncapture_route_lateral_m: ${fixed(r.lateral_m,2)}\nproduct_route_chainage_m: ${fixed(p.chainage_m,2)}\nproduct_route_lateral_m: ${fixed(p.lateral_m,2)}\nsequence_id: ${esc(c.sequence_id||'unavailable')}\nsequence_index: ${esc(c.sequence_index??'unavailable')}\n<a href="${esc(m.source_image_url)}" target="_blank">open synchronized source frame</a>`}
function renderReviewSuggestions(){const root=$('review-suggestions');root.innerHTML='';for(const group of queue?.fixture_vocabulary?.groups||[]){const title=document.createElement('h4');title.textContent=group.title;root.appendChild(title);for(const item of group.items){const b=document.createElement('button');b.type='button';b.textContent=item.display;b.title=[item.population_role,item.carrier,item.implementation_state].filter(Boolean).join(' · ')||item.source;b.onclick=()=>{$('label').value=item.label;$('custom').checked=false;toggleCustom()};root.appendChild(b)}}}
function drawMap(){const canvas=$('map'),rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;canvas.width=Math.max(1,Math.round(rect.width*ratio));canvas.height=Math.max(1,Math.round(rect.height*ratio));const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);const w=rect.width,h=rect.height,pad=14;const product=queue?.product_scope?.route?.coordinates_lonlat||[],capture=queue?.capture_route?.coordinates_lonlat||[],members=current()?.members||[],points=[...product,...capture,...members.map(m=>[m.camera?.lon,m.camera?.lat]).filter(p=>p.every(Number.isFinite))];if(!points.length)return;const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),dx=Math.max(maxX-minX,1e-7),dy=Math.max(maxY-minY,1e-7),project=p=>[pad+(p[0]-minX)/dx*(w-2*pad),h-pad-(p[1]-minY)/dy*(h-2*pad)];function line(rows,color,width){if(rows.length<2)return;ctx.beginPath();rows.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke()}line(product,'#f0b44d',3);line(capture,'#55d8ef',3);members.forEach(m=>{const c=m.camera||{};if(!Number.isFinite(c.lon)||!Number.isFinite(c.lat))return;const [x,y]=project([c.lon,c.lat]),selected=m.annotation_id===chosen()?.annotation_id;ctx.beginPath();ctx.arc(x,y,selected?6:4,0,Math.PI*2);ctx.fillStyle=selected?'#ffffff':'#ff7777';ctx.fill();if(Number.isFinite(c.heading_deg)){const angle=(c.heading_deg-90)*Math.PI/180,length=selected?18:12;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+Math.cos(angle)*length,y+Math.sin(angle)*length);ctx.strokeStyle=selected?'#ffffff':'#ff7777';ctx.lineWidth=2;ctx.stroke()}})}
function selectMember(id){selectedMemberId=id;const g=current(),m=g.members.find(row=>row.annotation_id===id);if(m)$('hero').src=`/api/crop/${encodeURIComponent(m.annotation_id)}`;document.querySelectorAll('.member').forEach(node=>node.classList.toggle('selected',node.dataset.id===id));showLocation();drawMap()}
function show(){const g=current();if(!g)return;selectedMemberId=g.representative_annotation_id;$('counter').textContent=`${index+1}/${groups.length}`;$('title').textContent=`${g.group_id} · ${g.member_count} crop${g.member_count===1?'':'s'}`;$('hero').src=`/api/crop/${encodeURIComponent(g.representative_annotation_id)}`;$('summary').textContent=`scope: ${g.review_scope} · proposal: ${g.proposed_semantic_label||'none'} · local: ${(g.local_classifier_label_candidates||[]).join(', ')||'none'} · agent triage: ${(g.agent_label_candidates||[]).join(', ')||'none'} [${(g.agent_population_role_candidates||[]).join(', ')||'none'}] · old first-pass clues (non-authoritative): ${(g.first_pass_label_candidates||[]).join(', ')||'none'}`;$('members').innerHTML=g.members.map(m=>{const c=m.camera||{},r=m.route_projection||{},p=m.product_route_projection||{},t=m.agent_label_triage||{};return `<article class="member ${m.annotation_id===selectedMemberId?'selected':''}" data-id="${esc(m.annotation_id)}"><img src="/api/crop/${encodeURIComponent(m.annotation_id)}"><small><strong>${esc(m.annotation_id)}</strong><br>lat/lon: ${fixed(c.lat,7)}, ${fixed(c.lon,7)}<br>heading: ${fixed(c.heading_deg,1)}°<br>capture route: ${fixed(r.chainage_m,2)} m · lateral ${fixed(r.lateral_m,2)} m<br>product route: ${fixed(p.chainage_m,2)} m · lateral ${fixed(p.lateral_m,2)} m<br>capture: ${esc(c.capture_id||m.capture_id||'unavailable')}<br>sequence: ${esc(c.sequence_id||'unavailable')} #${esc(c.sequence_index??'unavailable')}<br>old: ${esc(m.first_pass_label||'none')}<br>local: ${esc((m.local_classifier_proposals||[]).filter(x=>x.semantic_candidate).map(x=>x.semantic_candidate+' '+x.confidence.toFixed(2)).join(', ')||'none')}<br>agent: ${esc(t.proposed_semantic_label||'none')} [${esc(t.population_role||'untriaged')}]<br>distance: ${esc(m.distance_to_representative)}<br><a href="${esc(m.source_image_url)}" target="_blank">source frame</a></small></article>`}).join('');document.querySelectorAll('.member').forEach(node=>node.onclick=()=>selectMember(node.dataset.id));showLocation();drawMap();const d=existing();$('label').value=d?.semantic_label||g.proposed_semantic_label||'';$('custom').checked=!!d?.custom_extraction_required;$('asset').value=d?.custom_meta?.asset_key||'';$('kind').value=d?.custom_meta?.authoring_kind||'identity_sign_face';$('reason').value=d?.custom_meta?.reason||'';$('carrier').value=d?.custom_meta?.generic_carrier_class||'';$('note').value=d?.correction_note||'';$('no').textContent=g.review_scope==='annotation'?'NO / discard crop':'NO / wrong grouping';toggleCustom();$('status').textContent=d?`saved: ${d.action}`:'unreviewed'}
function toggleCustom(){$('custom-fields').style.display=$('custom').checked?'block':'none'}$('custom').onchange=toggleCustom;
async function decide(action){const g=current(),label=$('label').value.trim(),custom=$('custom').checked;if(g.review_scope==='annotation'&&action==='reject_similarity')action='discard';if((action==='accept'||action==='correct')&&!label){$('status').textContent='semantic class required';return}const row={action,semantic_label:label||null,custom_extraction_required:(action==='accept'||action==='correct')?custom:null,custom_meta:custom?{asset_key:$('asset').value.trim(),authoring_kind:$('kind').value,reason:$('reason').value.trim(),generic_carrier_class:$('carrier').value.trim()||null}:null,correction_note:$('note').value.trim()};const list=g.review_scope==='annotation'?decisions.annotation_reviews:decisions.group_reviews;const key=g.review_scope==='annotation'?'annotation_id':'group_id';row[key]=g.review_scope==='annotation'?g.annotation_id:g.group_id;const at=list.findIndex(x=>x[key]===row[key]);if(at>=0)list[at]=row;else list.push(row);try{decisions=await j('/api/review-decisions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(decisions)});const refreshed=await j('/api/review-queue');groups=refreshed.groups;index=Math.min(index+1,groups.length-1);show();$('status').textContent='saved + routing rebuilt'}catch(e){$('status').textContent=`save failed: ${e.message}`}}
$('yes').onclick=()=>decide('accept');$('correct').onclick=()=>decide('correct');$('no').onclick=()=>decide('reject_similarity');$('prev').onclick=()=>{index=Math.max(0,index-1);show()};$('next').onclick=()=>{index=Math.min(groups.length-1,index+1);show()};
window.addEventListener('resize',drawMap);Promise.all([j('/api/review-queue'),j('/api/review-decisions')]).then(([g,d])=>{queue=g;groups=g.groups;decisions=d;renderReviewSuggestions();coverage();show()}).catch(e=>$('status').textContent=e.message);
</script></body></html>"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class CaptureReviewStore:
    """Validated capture index plus atomic annotation-receipt persistence."""

    def __init__(
        self,
        capture_index_path: Path,
        annotations_path: Path,
        route_manifest_path: Path | None = None,
        fixture_pack_path: Path | None = None,
        groups_path: Path | None = None,
        decisions_path: Path | None = None,
        routing_path: Path | None = None,
        product_scope_path: Path | None = None,
    ):
        self.capture_index_path = capture_index_path.resolve()
        self.bundle_root = self.capture_index_path.parent
        self.annotations_path = annotations_path.resolve()
        self.crops_path = self.annotations_path.parent / "crops"
        self._lock = threading.RLock()
        self.index = json.loads(self.capture_index_path.read_text(encoding="utf-8"))
        if self.index.get("schema") != CAPTURE_INDEX_SCHEMA:
            raise ValueError(
                f"capture index schema must be {CAPTURE_INDEX_SCHEMA!r}"
            )
        self.area_coverage = self.index.get("area_coverage")
        captures = self.index.get("captures")
        if not isinstance(captures, list) or not captures:
            raise ValueError("capture index must contain at least one capture")
        self.captures: dict[str, dict[str, Any]] = {}
        self.image_paths: dict[str, Path] = {}
        for row in captures:
            capture_id = row.get("capture_id")
            if not isinstance(capture_id, str) or not capture_id:
                raise ValueError("every capture must have a non-empty capture_id")
            if capture_id in self.captures:
                raise ValueError(f"duplicate capture_id: {capture_id}")
            local_path = row.get("local_path")
            if not isinstance(local_path, str) or not local_path:
                raise ValueError(f"capture {capture_id} has no local_path")
            image_path = (self.bundle_root / local_path).resolve()
            if not image_path.is_relative_to(self.bundle_root):
                raise ValueError(f"capture {capture_id} escapes the bundle root")
            if not image_path.is_file():
                raise ValueError(f"capture {capture_id} image is absent: {image_path}")
            self.captures[capture_id] = dict(row)
            self.image_paths[capture_id] = image_path
        self.index_sha256 = _sha256(self.capture_index_path)
        if route_manifest_path is None:
            candidate = self.bundle_root / "route_capture_manifest.json"
            route_manifest_path = candidate if candidate.is_file() else None
        self.route_manifest_path = (
            route_manifest_path.resolve() if route_manifest_path is not None else None
        )
        self.route: dict[str, Any] | None = None
        if self.route_manifest_path is not None:
            manifest = json.loads(self.route_manifest_path.read_text(encoding="utf-8"))
            route = manifest.get("route") or {}
            coordinates = route.get("coordinates_lonlat")
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                raise ValueError("route manifest has no coordinates_lonlat polyline")
            if not all(
                isinstance(point, list)
                and len(point) >= 2
                and _is_number(point[0])
                and _is_number(point[1])
                for point in coordinates
            ):
                raise ValueError("route manifest coordinates_lonlat are invalid")
            self.route = {
                "coordinates_lonlat": coordinates,
                "length_m": route.get(
                    "measured_length_m",
                    route.get("length_m", route.get("declared_length_m")),
                ),
                "manifest_path": str(self.route_manifest_path),
                "manifest_sha256": _sha256(self.route_manifest_path),
                "coverage": manifest.get("coverage"),
                "counts": manifest.get("counts"),
            }
        self.product_scope_path = (
            product_scope_path.resolve() if product_scope_path is not None else None
        )
        self.product_scope: dict[str, Any] | None = None
        self._product_route_measure = None
        if self.product_scope_path is not None:
            scope = json.loads(self.product_scope_path.read_text(encoding="utf-8"))
            if scope.get("schema") != "y92.connected_product_scope.v1":
                raise ValueError("product scope must be y92.connected_product_scope.v1")
            scope_id = scope.get("scope_id")
            route_order = scope.get("route_order")
            coverage_state = scope.get("coverage_state")
            if not isinstance(scope_id, str) or not scope_id:
                raise ValueError("product scope has no scope_id")
            if not isinstance(route_order, list) or not route_order:
                raise ValueError("product scope has no route_order")
            if not isinstance(coverage_state, dict):
                raise ValueError("product scope has no coverage_state")
            geojson_path = self.product_scope_path.with_name("connected_scope.geojson")
            if not geojson_path.is_file():
                raise ValueError(f"product scope geometry is absent: {geojson_path}")
            geojson = json.loads(geojson_path.read_text(encoding="utf-8"))
            route_features = [
                feature
                for feature in geojson.get("features", [])
                if (feature.get("properties") or {}).get("role")
                == "connected_playable_route"
            ]
            if len(route_features) != 1:
                raise ValueError("product scope must contain one connected_playable_route")
            route_feature = route_features[0]
            route_geometry = route_feature.get("geometry") or {}
            coordinates = route_geometry.get("coordinates")
            if (
                route_geometry.get("type") != "LineString"
                or not isinstance(coordinates, list)
                or len(coordinates) < 2
            ):
                raise ValueError("product scope connected route geometry is invalid")
            if not all(
                isinstance(point, list)
                and len(point) >= 2
                and _is_number(point[0])
                and _is_number(point[1])
                for point in coordinates
            ):
                raise ValueError("product scope route coordinates are invalid")
            properties = route_feature.get("properties") or {}
            route_length_m = properties.get("length_m")
            if not _is_number(route_length_m):
                raise ValueError("product scope connected route has no numeric length_m")
            self.product_scope = {
                "schema": scope["schema"],
                "scope_id": scope_id,
                "route_order": route_order,
                "coverage_state": coverage_state,
                "route": {
                    "coordinates_lonlat": coordinates,
                    "length_m": route_length_m,
                },
                "scope_path": str(self.product_scope_path),
                "scope_sha256": _sha256(self.product_scope_path),
                "geometry_path": str(geojson_path.resolve()),
                "geometry_sha256": _sha256(geojson_path),
            }
            from scripts.pipeline.route_capture_manifest import RouteMeasure

            self._product_route_measure = RouteMeasure(coordinates)
        if fixture_pack_path is None:
            fixture_pack_path = (
                Path(__file__).resolve().parents[2]
                / "assets/world_templates/japan_urban/pack.json"
            )
        self.fixture_pack_path = fixture_pack_path.resolve()
        self.suggestions = self._load_suggestions()
        self.groups_path = groups_path.resolve() if groups_path is not None else None
        self.decisions_path = decisions_path.resolve() if decisions_path is not None else None
        self.routing_path = routing_path.resolve() if routing_path is not None else None
        if any(path is not None for path in (self.groups_path, self.decisions_path, self.routing_path)):
            if not all(path is not None for path in (self.groups_path, self.decisions_path, self.routing_path)):
                raise ValueError("group review requires --groups, --decisions, and --routing together")
            self._validate_group_inputs()

    def _load_suggestions(self) -> dict[str, Any]:
        fixture_rows: list[dict[str, Any]] = []
        pack_metadata: dict[str, Any] | None = None
        if self.fixture_pack_path.is_file():
            pack = json.loads(self.fixture_pack_path.read_text(encoding="utf-8"))
            templates = pack.get("templates") or []
            by_class = {
                row.get("semantic_class"): row
                for row in templates
                if isinstance(row, dict) and isinstance(row.get("semantic_class"), str)
            }
            ordered = [
                *COMMON_FIXTURE_CLASSES,
                *(name for name in sorted(by_class) if name not in COMMON_FIXTURE_CLASSES),
            ]
            fixture_rows = [
                {
                    "label": name,
                    "display": name.replace("_", " "),
                    "semantic_class": name,
                    "geometry_family": by_class[name].get("geometry_family"),
                    "template_id": by_class[name].get("template_id"),
                    "evidence_role": "semantic_observation",
                    "source": "japan_urban_pack",
                }
                for name in ordered
                if name in by_class
            ]
            pack_metadata = {
                "path": str(self.fixture_pack_path),
                "sha256": _sha256(self.fixture_pack_path),
                "schema": pack.get("schema"),
                "pack_id": pack.get("pack_id"),
            }
        surfaces = [
            {
                "label": name,
                "display": name.replace("_", " "),
                "evidence_role": "appearance_observation",
                "source": "capture_review_surface_vocabulary",
            }
            for name in SURFACE_SUGGESTIONS
        ]
        morphology_path = self.fixture_pack_path.with_name("generic_morphology.json")
        morphology_rows: list[dict[str, Any]] = []
        morphology_metadata: dict[str, Any] | None = None
        if morphology_path.is_file():
            morphology = json.loads(morphology_path.read_text(encoding="utf-8"))
            morphology_rows = [
                {
                    "label": row["semantic_class"],
                    "display": row["display"],
                    "evidence_role": "appearance_observation",
                    "source": "generic_morphology_vocabulary",
                    "population_role": "generic_morphology",
                    "carrier": row["carrier"],
                    "placement_rule": row["placement_rule"],
                    "implementation_state": row["implementation_state"],
                }
                for row in morphology.get("classes", [])
            ]
            morphology_metadata = {
                "path": str(morphology_path),
                "sha256": _sha256(morphology_path),
                "schema": morphology.get("schema"),
            }
        return {
            "fixture_pack": pack_metadata,
            "generic_morphology": morphology_metadata,
            "groups": [
                {"id": "japan_fixtures", "title": "Japan fixtures", "items": fixture_rows},
                {"id": "generic_morphology", "title": "Generic morphology gaps", "items": morphology_rows},
                {"id": "surfaces", "title": "Structures & surfaces", "items": surfaces},
            ],
        }

    def public_config(self) -> dict[str, Any]:
        captures = []
        for capture_id, row in self.captures.items():
            public = {
                key: value
                for key, value in row.items()
                if key not in {"asset_uri", "source_uri"}
            }
            public["image_url"] = f"/api/image/{quote(capture_id, safe='')}"
            captures.append(public)
        return {
            "schema": "y92.capture_review_config.v1",
            "capture_index": {
                "path": str(self.capture_index_path),
                "sha256": self.index_sha256,
                "schema": self.index.get("schema"),
            },
            "route": self.route,
            "area_coverage": self.area_coverage,
            "product_scope": self.product_scope,
            "suggestions": self.suggestions,
            "captures": captures,
            "group_review_url": "/review" if self.groups_path is not None else None,
        }

    def _validate_group_inputs(self) -> None:
        from scripts.pipeline.compile_capture_review_routing import (
            GROUPS_SCHEMA,
            empty_decisions,
            validate_decisions,
            write_routing,
        )

        assert self.groups_path is not None
        assert self.decisions_path is not None
        assert self.routing_path is not None
        groups = json.loads(self.groups_path.read_text(encoding="utf-8"))
        if groups.get("schema") != GROUPS_SCHEMA:
            raise ValueError(f"groups are not {GROUPS_SCHEMA}")
        annotations_sha = _sha256(self.annotations_path) if self.annotations_path.is_file() else None
        if (groups.get("input") or {}).get("sha256") != annotations_sha:
            raise ValueError("similarity groups refer to different annotations")
        if not self.decisions_path.is_file():
            self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
            self.decisions_path.write_text(
                json.dumps(empty_decisions(self.groups_path), ensure_ascii=False,
                           indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        decisions = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        validate_decisions(decisions, groups, self.groups_path)
        write_routing(
            self.annotations_path, self.groups_path, self.decisions_path, self.routing_path
        )

    def group_review_view(self) -> dict[str, Any]:
        if self.groups_path is None:
            raise ValueError("group review is not configured")
        groups = json.loads(self.groups_path.read_text(encoding="utf-8"))
        agent_by_id = {
            row["annotation_id"]: row for row in self.agent_view()["annotations"]
        }
        document = deepcopy(groups)
        document["capture_route"] = self.route
        document["area_coverage"] = self.area_coverage
        document["product_scope"] = self.product_scope
        document["fixture_vocabulary"] = self.suggestions
        group_reviews = {
            row["group_id"]: row
            for row in self.load_semantic_decisions()["group_reviews"]
        }
        expanded_groups = []
        for group in document["groups"]:
            group["members"] = [
                {
                    **member,
                    "crop": agent_by_id[member["annotation_id"]]["crop"],
                    "camera": agent_by_id[member["annotation_id"]]["camera"],
                    "route_projection": agent_by_id[member["annotation_id"]]["route_projection"],
                    "product_route_projection": agent_by_id[member["annotation_id"]]["product_route_projection"],
                    "source_image_url": agent_by_id[member["annotation_id"]]["source_image_url"],
                }
                for member in group["members"]
            ]
            group["review_scope"] = "group"
            group["source_group_id"] = group["group_id"]
            review = group_reviews.get(group["group_id"])
            if review is not None and review["action"] == "reject_similarity":
                for member in group["members"]:
                    expanded_groups.append({
                        **deepcopy(group),
                        "review_scope": "annotation",
                        "annotation_id": member["annotation_id"],
                        "group_id": "individual-" + member["annotation_id"],
                        "representative_annotation_id": member["annotation_id"],
                        "member_count": 1,
                        "members": [deepcopy(member)],
                        "proposed_semantic_label": None,
                        "state": "individual_review_after_rejected_similarity",
                    })
            else:
                expanded_groups.append(group)
        document["groups"] = expanded_groups
        document["counts"]["review_cards"] = len(expanded_groups)
        return document

    def load_semantic_decisions(self) -> dict[str, Any]:
        if self.groups_path is None or self.decisions_path is None:
            raise ValueError("group review is not configured")
        from scripts.pipeline.compile_capture_review_routing import validate_decisions

        groups = json.loads(self.groups_path.read_text(encoding="utf-8"))
        payload = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        return validate_decisions(payload, groups, self.groups_path)

    def save_semantic_decisions(self, payload: Any) -> dict[str, Any]:
        if self.groups_path is None or self.decisions_path is None or self.routing_path is None:
            raise ValueError("group review is not configured")
        from scripts.pipeline.compile_capture_review_routing import (
            portable_path,
            validate_decisions,
            write_routing,
        )

        with self._lock:
            groups = json.loads(self.groups_path.read_text(encoding="utf-8"))
            decisions = validate_decisions(payload, groups, self.groups_path)
            decisions["updated_at"] = _now()
            temporary = self.decisions_path.with_name(
                f".{self.decisions_path.name}.{os.getpid()}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(self.decisions_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            routing = write_routing(
                self.annotations_path, self.groups_path, self.decisions_path, self.routing_path
            )
            pipeline_receipt_path = self.routing_path.parent / "pipeline_receipt.json"
            if pipeline_receipt_path.is_file():
                pipeline_receipt = json.loads(
                    pipeline_receipt_path.read_text(encoding="utf-8")
                )
                if pipeline_receipt.get("schema") == "y92.capture_review_pipeline_receipt.v1":
                    pipeline_receipt["stage"] = (
                        "semantic_review_complete"
                        if routing["promotion"]["semantic_review_complete"]
                        else "semantic_review_in_progress"
                    )
                    pipeline_receipt["outputs"]["semantic_review"] = {
                        "path": portable_path(self.decisions_path),
                        "sha256": _sha256(self.decisions_path),
                    }
                    pipeline_receipt["outputs"]["authoring_routing"] = {
                        "path": portable_path(self.routing_path),
                        "sha256": _sha256(self.routing_path),
                    }
                    pipeline_receipt["counts"].update(routing["counts"])
                    pipeline_receipt["promotion"] = routing["promotion"]
                    temporary_receipt = pipeline_receipt_path.with_name(
                        f".{pipeline_receipt_path.name}.{os.getpid()}.tmp"
                    )
                    try:
                        temporary_receipt.write_text(
                            json.dumps(pipeline_receipt, ensure_ascii=False,
                                       indent=2, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                        temporary_receipt.replace(pipeline_receipt_path)
                    finally:
                        if temporary_receipt.exists():
                            temporary_receipt.unlink()
        return decisions

    def routing_view(self) -> dict[str, Any]:
        if self.routing_path is None:
            raise ValueError("group review is not configured")
        return json.loads(self.routing_path.read_text(encoding="utf-8"))

    def empty_receipt(self) -> dict[str, Any]:
        return {
            "schema": REVIEW_SCHEMA,
            "stage": "operator_review_in_progress",
            "capture_index": {
                "path": str(self.capture_index_path),
                "sha256": self.index_sha256,
            },
            "created_at": _now(),
            "updated_at": _now(),
            "annotations": [],
            "promotion": {
                "camera_registration_accepted": False,
                "world_ir_promotion_ready": False,
            },
        }

    def load_receipt(self) -> dict[str, Any]:
        with self._lock:
            if not self.annotations_path.is_file():
                return self.empty_receipt()
            original = json.loads(self.annotations_path.read_text(encoding="utf-8"))
            receipt = self.validate_receipt(original)
            if receipt != original:
                receipt["updated_at"] = _now()
                self._write_receipt_unlocked(receipt)
            return receipt

    def _capture_snapshot(self, capture_id: str) -> dict[str, Any]:
        row = self.captures[capture_id]
        return {
            key: row.get(key)
            for key in (
                "capture_id",
                "sha256",
                "license",
                "license_uri",
                "attribution",
                "lat",
                "lon",
                "altitude_m",
                "heading_deg",
                "pitch_deg",
                "roll_deg",
                "horizontal_fov_deg",
                "projection_model",
                "sequence_id",
                "sequence_index",
                "timestamp",
                "acquisition_route_projection",
                "source_dimensions_px",
                "width",
                "height",
            )
        }

    def validate_receipt(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("annotation receipt must be an object")
        if payload.get("schema") != REVIEW_SCHEMA:
            raise ValueError(f"annotation receipt schema must be {REVIEW_SCHEMA!r}")
        annotations = payload.get("annotations")
        if not isinstance(annotations, list):
            raise ValueError("annotations must be a list")
        normalized = self.empty_receipt()
        normalized["created_at"] = str(payload.get("created_at") or _now())
        normalized["updated_at"] = str(payload.get("updated_at") or _now())
        snapshot = payload.get("snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, dict) or snapshot.get("schema") != "y92.capture_review_snapshot.v1":
                raise ValueError("annotation receipt snapshot metadata is invalid")
            normalized["snapshot"] = deepcopy(snapshot)
        seen: set[str] = set()
        for index, row in enumerate(annotations):
            if not isinstance(row, dict):
                raise ValueError(f"annotations[{index}] must be an object")
            annotation_id = row.get("annotation_id")
            if not isinstance(annotation_id, str) or not annotation_id:
                raise ValueError(f"annotations[{index}].annotation_id is required")
            if not ANNOTATION_ID_RE.fullmatch(annotation_id):
                raise ValueError(f"annotations[{index}].annotation_id is unsafe")
            if annotation_id in seen:
                raise ValueError(f"duplicate annotation_id: {annotation_id}")
            seen.add(annotation_id)
            capture_id = row.get("capture_id")
            if capture_id not in self.captures:
                raise ValueError(f"unknown capture_id: {capture_id!r}")
            box = row.get("box_norm")
            if not isinstance(box, list) or len(box) != 4 or not all(
                _is_number(value) for value in box
            ):
                raise ValueError(f"annotations[{index}].box_norm must contain 4 numbers")
            x, y, width, height = (float(value) for value in box)
            if (
                x < 0
                or y < 0
                or width <= 0
                or height <= 0
                or x + width > 1.0000001
                or y + height > 1.0000001
            ):
                raise ValueError(f"annotations[{index}].box_norm is outside the image")
            label = row.get("label")
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"annotations[{index}].label is required")
            note = row.get("note") or ""
            if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
                raise ValueError(f"annotations[{index}].note is invalid")
            evidence_role = row.get("evidence_role") or "appearance_observation"
            if not isinstance(evidence_role, str) or not evidence_role:
                raise ValueError(f"annotations[{index}].evidence_role is required")
            parent = row.get("parent_feature_id")
            if parent is not None and not isinstance(parent, str):
                raise ValueError(f"annotations[{index}].parent_feature_id must be text")
            normalized_row = {
                "annotation_id": annotation_id,
                "capture_id": capture_id,
                "box_norm": [x, y, width, height],
                "label": label.strip(),
                "parent_feature_id": parent.strip() if parent else None,
                "evidence_role": evidence_role,
                "note": note,
                "review_state": "operator_authored",
                "capture_snapshot": self._capture_snapshot(capture_id),
            }
            normalized_row["crop"] = self._ensure_crop(
                normalized_row, row.get("crop")
            )
            normalized["annotations"].append(normalized_row)
        return normalized

    def _ensure_crop(
        self, annotation: dict[str, Any], existing: Any
    ) -> dict[str, Any]:
        annotation_id = annotation["annotation_id"]
        capture_id = annotation["capture_id"]
        source_path = self.image_paths[capture_id]
        expected_source_sha = self.captures[capture_id].get("sha256")
        if not isinstance(expected_source_sha, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", expected_source_sha
        ):
            raise ValueError(f"capture {capture_id} has no valid frozen sha256")
        try:
            with Image.open(source_path) as image:
                source_width, source_height = image.size
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError(f"capture {capture_id} is not a readable image") from error
        x, y, width, height = annotation["box_norm"]
        left = max(0, min(source_width - 1, floor(x * source_width + 1e-9)))
        top = max(0, min(source_height - 1, floor(y * source_height + 1e-9)))
        right = max(
            left + 1,
            min(source_width, ceil((x + width) * source_width - 1e-9)),
        )
        bottom = max(
            top + 1,
            min(source_height, ceil((y + height) * source_height - 1e-9)),
        )
        pixel_box = [left, top, right - left, bottom - top]
        crop_path = (self.crops_path / f"{annotation_id}.png").resolve()
        if not crop_path.is_relative_to(self.crops_path.resolve()):
            raise ValueError(f"annotation {annotation_id} crop path escapes its root")
        saved_path = str(crop_path)
        if isinstance(existing, dict) and isinstance(existing.get("path"), str):
            existing_raw = Path(existing["path"])
            existing_resolved = (
                existing_raw.resolve() if existing_raw.is_absolute()
                else (self.annotations_path.parent / existing_raw).resolve()
            )
            if existing_resolved == crop_path:
                saved_path = existing["path"]
        expected = {
            "path": saved_path,
            "url": f"/api/crop/{quote(annotation_id, safe='')}",
            "mime_type": "image/png",
            "source_sha256": expected_source_sha.lower(),
            "box_norm": annotation["box_norm"],
            "pixel_box_xywh": pixel_box,
            "width": pixel_box[2],
            "height": pixel_box[3],
        }
        if (
            isinstance(existing, dict)
            and crop_path.is_file()
            and all(existing.get(key) == value for key, value in expected.items())
            and isinstance(existing.get("sha256"), str)
            and _sha256(crop_path) == existing["sha256"]
        ):
            return dict(existing)
        actual_source_sha = _sha256(source_path)
        if actual_source_sha.lower() != expected_source_sha.lower():
            raise ValueError(
                f"capture {capture_id} sha256 mismatch: expected "
                f"{expected_source_sha.lower()}, got {actual_source_sha.lower()}"
            )
        self.crops_path.mkdir(parents=True, exist_ok=True)
        temporary = crop_path.with_name(f".{crop_path.name}.{os.getpid()}.tmp")
        try:
            with Image.open(source_path) as image:
                crop = image.crop((left, top, right, bottom))
                crop.save(temporary, format="PNG", optimize=False, compress_level=9)
            temporary.replace(crop_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        expected["sha256"] = _sha256(crop_path)
        return expected

    def _write_receipt_unlocked(self, receipt: dict[str, Any]) -> None:
        self.annotations_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.annotations_path.with_name(
            f".{self.annotations_path.name}.{os.getpid()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.annotations_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def save_receipt(self, payload: Any) -> dict[str, Any]:
        with self._lock:
            receipt = self.validate_receipt(payload)
            receipt["updated_at"] = _now()
            self._write_receipt_unlocked(receipt)
        return receipt

    def agent_view(self) -> dict[str, Any]:
        receipt = self.load_receipt()
        rows = []
        for annotation in receipt["annotations"]:
            row = deepcopy(annotation)
            snapshot = row.pop("capture_snapshot")
            route_projection = snapshot.pop("acquisition_route_projection", None)
            row["camera"] = snapshot
            row["route_projection"] = route_projection
            row["product_route_projection"] = None
            if (
                self._product_route_measure is not None
                and _is_number(snapshot.get("lon"))
                and _is_number(snapshot.get("lat"))
            ):
                projection = self._product_route_measure.project(
                    float(snapshot["lon"]), float(snapshot["lat"])
                )
                row["product_route_projection"] = {
                    "chainage_m": round(projection.chainage_m, 3),
                    "lateral_m": round(projection.lateral_m, 3),
                }
            row["source_image_url"] = (
                f"/api/image/{quote(annotation['capture_id'], safe='')}"
            )
            rows.append(row)
        return {
            "schema": AGENT_SCHEMA,
            "stage": receipt["stage"],
            "capture_index": receipt["capture_index"],
            "route": self.route,
            "area_coverage": self.area_coverage,
            "product_scope": self.product_scope,
            "fixture_vocabulary": self.suggestions,
            "annotations": rows,
            "promotion": receipt["promotion"],
        }

    def crop_for_annotation(self, annotation_id: str) -> tuple[Path, str] | None:
        for annotation in self.load_receipt()["annotations"]:
            if annotation["annotation_id"] != annotation_id:
                continue
            crop = annotation["crop"]
            raw_path = Path(crop["path"])
            crop_path = (
                raw_path.resolve() if raw_path.is_absolute()
                else (self.annotations_path.parent / raw_path).resolve()
            )
            if not crop_path.is_relative_to(self.crops_path.resolve()):
                raise ValueError("saved crop path escapes its root")
            if not crop_path.is_file() or _sha256(crop_path) != crop["sha256"]:
                raise ValueError(f"saved crop digest mismatch: {annotation_id}")
            return crop_path, crop["mime_type"]
        return None


def make_handler(store: CaptureReviewStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Y92CaptureReview/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
            )
            self.end_headers()

        def _send_bytes(
            self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            self._headers(status, content_type, len(payload))
            self.wfile.write(payload)

        def _send_json(
            self, payload: Any, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", status)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            if path == "/":
                self._send_bytes(APP_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/review":
                if store.groups_path is None:
                    self._error(HTTPStatus.NOT_FOUND, "group review is not configured")
                    return
                self._send_bytes(
                    GROUP_REVIEW_HTML.encode("utf-8"), "text/html; charset=utf-8"
                )
                return
            if path == "/api/config":
                self._send_json(store.public_config())
                return
            if path == "/api/annotations":
                self._send_json(store.load_receipt())
                return
            if path == "/api/agent":
                self._send_json(store.agent_view())
                return
            if path in {"/api/groups", "/api/review-queue"}:
                try:
                    self._send_json(store.group_review_view())
                except ValueError as error:
                    self._error(HTTPStatus.NOT_FOUND, str(error))
                return
            if path == "/api/review-decisions":
                try:
                    self._send_json(store.load_semantic_decisions())
                except ValueError as error:
                    self._error(HTTPStatus.NOT_FOUND, str(error))
                return
            if path == "/api/routing":
                try:
                    self._send_json(store.routing_view())
                except ValueError as error:
                    self._error(HTTPStatus.NOT_FOUND, str(error))
                return
            crop_prefix = "/api/crop/"
            if path.startswith(crop_prefix):
                annotation_id = unquote(path[len(crop_prefix) :])
                try:
                    crop = store.crop_for_annotation(annotation_id)
                except ValueError as error:
                    self._error(HTTPStatus.CONFLICT, str(error))
                    return
                if crop is None:
                    self._error(HTTPStatus.NOT_FOUND, "unknown annotation")
                    return
                crop_path, content_type = crop
                self._send_bytes(crop_path.read_bytes(), content_type)
                return
            prefix = "/api/image/"
            if path.startswith(prefix):
                capture_id = unquote(path[len(prefix) :])
                image_path = store.image_paths.get(capture_id)
                if image_path is None:
                    self._error(HTTPStatus.NOT_FOUND, "unknown capture")
                    return
                content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
                self._send_bytes(image_path.read_bytes(), content_type)
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            request_path = urlsplit(self.path).path
            if request_path not in {"/api/annotations", "/api/review-decisions"}:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
                return
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid request size")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                receipt = (
                    store.save_receipt(payload)
                    if request_path == "/api/annotations"
                    else store.save_semantic_decisions(payload)
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
                return
            self._send_json(receipt)

    return Handler


def build_server(
    store: CaptureReviewStore, bind: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    if bind not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("capture review server must bind to loopback")
    return ThreadingHTTPServer((bind, port), make_handler(store))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture-index", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument(
        "--route-manifest",
        type=Path,
        help="Optional route_capture_manifest.json; inferred beside the capture index",
    )
    parser.add_argument(
        "--product-scope",
        type=Path,
        help="Expanded connected-product scope_spec.json shown separately from capture coverage",
    )
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--groups", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--routing", type=Path)
    args = parser.parse_args(argv)

    store = CaptureReviewStore(
        args.capture_index,
        args.annotations,
        route_manifest_path=args.route_manifest,
        product_scope_path=args.product_scope,
        groups_path=args.groups,
        decisions_path=args.decisions,
        routing_path=args.routing,
    )
    if args.check:
        print(
            f"CAPTURE REVIEW CHECK PASS: {len(store.captures)} local licensed frames; "
            f"annotations={len(store.load_receipt()['annotations'])}"
        )
        return 0
    server = build_server(store, args.bind, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"CAPTURE REVIEW READY: {url}", flush=True)
    print(f"  capture index: {store.capture_index_path}", flush=True)
    print(f"  annotations:   {store.annotations_path}", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
