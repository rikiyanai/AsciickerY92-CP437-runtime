// street_view_overlay.js -- FL-4876 SV-OVERLAY-01, a street-view guide for the first-person view (editor_views.js).
// Operator 2026-10-18: "for the available street view camera POV, since we have first person now, it should be compared";
// then, after watching the first pixel overlay: "the street view overlay should be a qualitative guide ... it makes correct
// looking authorings feel a lot more incorrect than they are". The question it serves: would it be reasonable for a
// Japanese suburb to have fixture X at size Y at this position?
// So the DEFAULT is SIDE BY SIDE: the A3D first-person view on the left and the panorama on the right, both rectilinear
// with the same compass bearing, pitch and the same natural lens (horizontal FOV, default 75 deg; each panel 4:3 with
// square pixels), re-rendered together on every look / walk. Overlay, wipe and photo edges are secondary modes, labelled
// approximate, because the panorama poses are off by metres.
// Projection per capture comes from the Mapillary metadata (map_editor.py /api/sv, imagery surveys): equirectangular /
// spherical captures are reprojected exactly (pano.html model: u = 0.5 + (compass - H) / 360, v = 0.5 - latitude / 180,
// H = pano_index compass + authored heading_offset_deg + the optional hand dheading_deg). Any other or unknown projection
// is shown as a plain crop of the source image centred on the bearing, labelled approximate (no false rectilinear warp).
// Optional alignment (owner sv_alignment.json, POST /api/sv/alignment): camera at pano E+dE, N+dN, height_m above the
// terrain; dpitch/droll tilt the panorama sphere. Keys (first person): V on/off · , . previous / next panorama ·
// C layout · [ ] photo heading -/+0.5 deg (Shift x4).
'use strict';
window.SV = (() => {
  const $ = id => document.getElementById(id);
  const DEF_H = 2.5;   // Mapillary 360 rigs (car roof / bicycle pole): ~2.0-2.6 m above the road; pass-4 assessments used 2.5 m
  const STEP = { dE: 0.25, dN: 0.25, dh: 0.5, dp: 0.25, dr: 0.25, h: 0.1 };
  const LAYOUTS = ['side', 'overlay', 'wipe', 'edges'];
  const APPROX = 'approximate: panorama pose is off by metres; use side by side to judge';
  const st = { on: false, list: null, al: {}, pano: null, ready: false, layout: 'side', opacity: 0.35, wipe: 0.5, hfov: 75,
    off: { dE: 0, dN: 0, dh: 0, dp: 0, dr: 0, h: DEF_H }, order: [], err: null, loading: null, imgs: new Map(), listPromise: null, msg: '',
    prevVfov: null, alignOpen: false };
  const R = { gl: null };
  const D2R = Math.PI / 180;
  // ---------------------------------------------------------------------------------------------------------------
  // DOM
  // ---------------------------------------------------------------------------------------------------------------
  const css = `#evsv{position:absolute;left:0;top:0;display:none;pointer-events:none}
#evwipe{position:absolute;top:0;width:16px;margin-left:-8px;cursor:ew-resize;display:none;z-index:2}
#evwipe:after{content:'';position:absolute;left:7px;top:0;bottom:0;width:2px;background:#ffd166;box-shadow:0 0 3px #000}
#svCtl .row{white-space:normal}#svCtl button{padding:1px 6px}#svSel{max-width:250px}
#svNote{width:200px;background:#0b0b0e;color:var(--ink);border:1px solid #444;font:inherit;padding:2px}`;
  function injectDom() {
    const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s);
    const c = document.createElement('canvas'); c.id = 'evsv'; $('stage').insertBefore(c, $('evhud'));
    const w = document.createElement('div'); w.id = 'evwipe'; w.title = 'drag: photo on the left, A3D on the right'; $('stage').appendChild(w);
    const box = document.createElement('div'); box.id = 'svCtl';
    box.innerHTML = `<div class="row"><b>Street view</b> <button id="svOn" title="A3D and the nearest Mapillary panorama side by side, same bearing (V)">off</button>
 <button id="svPrev" title="previous panorama (,)">◀</button><select id="svSel" title="360 panoramas, nearest first"></select><button id="svNext" title="next panorama (.)">▶</button>
 <button id="svNear" title="re-sort by distance from the camera and take the nearest">nearest</button></div>
<div id="svMore"><div class="row">view <button data-svl="side" title="A3D left, photo right, same bearing and lens">Side by side</button> · approximate: <button data-svl="overlay">Overlay</button><button data-svl="wipe">Wipe</button><button data-svl="edges">Edges</button> (C)
 · lens <input type="range" min="40" max="110" id="svFov" title="horizontal field of view of both panels"><span class="v" id="svFovV"></span>
 <span id="svOpBox">· opacity <input type="range" min="0" max="100" id="svOp"><span class="v" id="svOpV"></span></span></div>
<div class="row"><button id="svAlignT" title="optional: nudge the panorama pose and save it">alignment (optional) ▸</button> <span id="svSaved" class="dim"></span></div>
<div id="svAlign"><div class="row">nudge <button data-svn="dE:-1" title="camera 0.25 m west">W</button><button data-svn="dE:1" title="camera 0.25 m east">E</button><button data-svn="dN:1" title="camera 0.25 m north">N</button><button data-svn="dN:-1" title="camera 0.25 m south">S</button>
 · heading <button data-svn="dh:-1" title="photo heading -0.5° ([)">⟲</button><button data-svn="dh:1" title="photo heading +0.5° (])">⟳</button>
 · pitch <button data-svn="dp:1" title="tilt the panorama up 0.25°">▲</button><button data-svn="dp:-1" title="tilt the panorama down 0.25°">▼</button>
 · roll <button data-svn="dr:-1" title="roll the panorama 0.25°">↺</button><button data-svn="dr:1" title="roll the panorama 0.25°">↻</button>
 · height <button data-svn="h:-1" title="camera 0.1 m lower">−</button><button data-svn="h:1" title="camera 0.1 m higher">+</button> <span class="dim">(Shift-click ×4)</span></div>
<div class="row" id="svOff"></div>
<div class="row"><input id="svNote" type="text" placeholder="alignment note (optional)"> <button id="svSave" title="write sv_alignment.json">Save alignment</button> <button id="svReset" title="back to the saved alignment (or zero)">Reset</button></div></div></div>`;
    $('evFpCtl').appendChild(box);
    $('svOn').onclick = () => toggle();
    $('svPrev').onclick = () => step(-1); $('svNext').onclick = () => step(1);
    $('svNear').onclick = () => { sortOrder(); if (st.order[0]) choose(st.order[0]); };
    $('svSel').onchange = () => { const id = $('svSel').value; $('svSel').blur(); if (id) choose(id); };
    box.querySelectorAll('[data-svl]').forEach(b => b.onclick = () => setLayout(b.dataset.svl));
    $('svOp').oninput = () => { st.opacity = $('svOp').value / 100; relayoutNow(); }; $('svOp').onchange = () => $('svOp').blur();
    $('svFov').oninput = () => { st.hfov = +$('svFov').value; relayoutNow(); }; $('svFov').onchange = () => $('svFov').blur();
    $('svAlignT').onclick = () => { st.alignOpen = !st.alignOpen; sync(); };
    box.querySelectorAll('[data-svn]').forEach(b => b.onclick = e => { const [k, sg] = b.dataset.svn.split(':'); nudge(k, +sg * (e.shiftKey ? 4 : 1)); });
    $('svSave').onclick = () => save({ note: $('svNote').value.trim() });
    $('svNote').addEventListener('keydown', e => { if (e.key === 'Enter') { save({ note: $('svNote').value.trim() }); $('svNote').blur(); } });
    $('svReset').onclick = () => { st.off = savedOff(st.pano && st.pano.id) || defOff(); placeCam(); sync(); };
    let wd = null;
    w.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); wd = true; });
    addEventListener('mousemove', e => { if (!wd) return; const r = $('evsv').getBoundingClientRect(); st.wipe = Math.max(0, Math.min(1, (e.clientX - r.left) / (r.width || 1))); relayoutNow(); });
    addEventListener('mouseup', () => { wd = null; });
    // side by side: dragging on the photo panel looks around too (handed to the A3D panel's own drag handler)
    c.addEventListener('mousedown', e => { if (st.layout === 'side') $('evgl').dispatchEvent(new MouseEvent('mousedown', e)); });
    c.addEventListener('contextmenu', e => e.preventDefault());
    const help = $('help'); if (help) help.insertAdjacentHTML('beforeend', '<br><b>Street view</b> (first person): <kbd>V</kbd> A3D and the nearest Mapillary 360 panorama side by side, same bearing and lens; drag either side to look · <kbd>,</kbd>/<kbd>.</kbd> previous / next panorama · <kbd>C</kbd> view side by side / overlay / wipe / edges (the last three are approximate: panorama poses are off by metres) · <kbd>[</kbd>/<kbd>]</kbd> photo heading −/+0.5° · optional alignment nudge + save in the first-person panel.');
  }
  // ---------------------------------------------------------------------------------------------------------------
  // Data
  // ---------------------------------------------------------------------------------------------------------------
  async function getJSON(u, body) {
    const r = await fetch(u, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
    const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j;
  }
  function ensureList() {
    if (st.list) return Promise.resolve();
    if (!st.listPromise) st.listPromise = getJSON('/api/sv').then(j => { st.list = j.panoramas; st.al = (j.alignment && j.alignment.alignments) || {}; st.census = j.projection_census; })
      .catch(e => { st.listPromise = null; st.err = '/api/sv: ' + e.message; sync(); throw e; });
    return st.listPromise;
  }
  const byId = id => st.list && st.list.find(p => p.id === id);
  const defOff = () => ({ dE: 0, dN: 0, dh: 0, dp: 0, dr: 0, h: DEF_H });
  function savedOff(id) { const a = id && st.al[id]; return a ? { dE: a.dE || 0, dN: a.dN || 0, dh: a.dheading_deg || 0, dp: a.dpitch_deg || 0, dr: a.droll_deg || 0, h: a.height_m || DEF_H } : null; }
  function camEN() { const f = EV.fpCam(); return EV.toEN(f.x, f.y); }
  function sortOrder() { const [E, N] = camEN(); st.anchor = [E, N]; st.order = st.list.map(p => [p.id, Math.hypot(p.E - E, p.N - N)]).sort((a, b) => a[1] - b[1]).map(a => a[0]); fillSel(); }
  function fillSel() {
    const [E, N] = st.anchor || camEN();
    $('svSel').innerHTML = st.order.map(id => { const p = byId(id), d = Math.hypot(p.E - E, p.N - N);
      return `<option value="${id}" ${st.pano && st.pano.id === id ? 'selected' : ''}>${d.toFixed(0)} m · ${id.slice(10)}${st.al[id] ? ' ★' : ''}${p.projection.kind !== 'equirect' ? ' (' + p.projection.kind + ')' : ''}</option>`; }).join('');
  }
  function loadImg(p) {
    if (st.imgs.has(p.id)) return st.imgs.get(p.id);
    const pr = new Promise((ok, bad) => { const im = new Image(); im.onload = () => ok(im); im.onerror = () => bad(new Error('panorama image ' + p.img)); im.src = '/img?path=' + encodeURIComponent(p.img); });
    st.imgs.set(p.id, pr); pr.catch(() => st.imgs.delete(p.id)); return pr;
  }
  const isEq = () => st.pano && st.pano.projection && st.pano.projection.kind === 'equirect';
  const projLabel = () => !st.pano ? '' : isEq() ? `equirect 360 (${st.pano.projection.camera_type}, ${st.pano.projection.camera || '?'})`
    : `${st.pano.projection.kind === 'fisheye' ? 'fisheye' : st.pano.projection.kind + ' projection'}, approximate (plain crop, not reprojected)`;
  // ---------------------------------------------------------------------------------------------------------------
  // Camera
  // ---------------------------------------------------------------------------------------------------------------
  function station() { const p = st.pano; return EV.toXY(p.E + st.off.dE, p.N + st.off.dN); }
  // Panorama GPS is off by metres and often lands inside a building footprint, where the A3D panel would show only a
  // wall. Without a saved alignment the camera then steps to the nearest open ground (nothing > 0.5 m above the terrain
  // at the point and 1 m around it; 0.5 m rings up to 15 m), labelled 'auto' and not saved.
  function blockedAt(E, N) { const [x, y] = EV.toXY(E, N), g = RT.ground(x, y); return !!g && (g.top_z - g.terrain_z) > 0.5 * EV.scale() * 16; }
  function openNear(E, N) {
    const ok = (e, n) => !blockedAt(e, n) && [0, 90, 180, 270].every(a => !blockedAt(e + Math.sin(a * D2R), n + Math.cos(a * D2R)));
    if (ok(E, N)) return [0, 0];
    for (let r = 0.5; r <= 15; r += 0.5) for (let k = 0; k < 48; k++) { const a = k * 7.5 * D2R, e = E + r * Math.sin(a), n = N + r * Math.cos(a); if (ok(e, n)) return [e - E, n - N]; }
    return null;
  }
  function placeCam(extra) { if (!st.pano) return; const [x, y] = station(); EV.setFp({ x, y, eyeM: st.off.h, ...(extra || {}) }); }
  async function choose(id, o) {
    o = o || {}; const p = byId(id); if (!p) throw new Error('not a listed 360 panorama: ' + id);
    st.pano = p; st.ready = false; st.off = savedOff(id) || defOff(); st.err = null; st.loading = 'loading ' + id + '…'; sync();
    const extra = {}; if (o.bearing != null) extra.yaw = ((-o.bearing % 360) + 360) % 360; if (o.pitch != null) extra.pitch = o.pitch;
    for (const [k, a] of [['dE', 'dE'], ['dN', 'dN'], ['dheading_deg', 'dh'], ['dpitch_deg', 'dp'], ['droll_deg', 'dr'], ['height_m', 'h']]) if (o[k] != null) st.off[a] = o[k];
    st.auto = null;
    if (o.fov == null) st.hfov = (st.al[id] && st.al[id].fov_deg) || o.fov_default || st.hfov;   // the lens saved with the alignment
    if (!st.al[id] && o.dE == null && o.dN == null && o.auto !== false) {
      const d = openNear(p.E, p.N);
      if (d && (d[0] || d[1])) { st.off.dE = +d[0].toFixed(2); st.off.dN = +d[1].toFixed(2); st.auto = { moved_m: +Math.hypot(d[0], d[1]).toFixed(2), dE: st.off.dE, dN: st.off.dN }; }
      else if (!d) st.auto = { moved_m: null, note: 'inside a building, no open ground within 15 m' };
    }
    placeCam(extra);
    try { const im = await loadImg(p); if (st.pano !== p) return; upload(im); st.ready = true; st.loading = null; }
    catch (e) { st.err = e.message; st.loading = null; }
    fillSel(); sync(); EV.redraw();
  }
  function step(dir) {
    if (!st.list) return; if (!st.order.length) sortOrder();
    const i = st.pano ? st.order.indexOf(st.pano.id) : -1, j = Math.max(0, Math.min(st.order.length - 1, i + dir));
    if (st.order[j] && (!st.pano || st.order[j] !== st.pano.id)) choose(st.order[j]);
  }
  function nudge(k, n) { if (!st.pano) return; st.off[k] = +(st.off[k] + n * STEP[k]).toFixed(4); if (k === 'h') st.off.h = Math.max(0.3, Math.min(10, st.off.h));
    if (k === 'dE' || k === 'dN' || k === 'h') placeCam(); else EV.redraw(); sync(); }
  async function toggle(force) {
    const on = force == null ? !st.on : !!force;
    if (on && EV.state().mode !== 'fp') await EV.setMode('fp');
    if (on && EV.state().mode !== 'fp') return;   // map still loading
    if (on && !st.on) st.prevVfov = EV.fpCam().vfov;
    if (!on && st.on && st.prevVfov) EV.setFp({ vfov: st.prevVfov });
    st.on = on;
    if (on) { try { await ensureList(); } catch (e) { return; } if (!st.pano) { sortOrder(); if (st.order[0]) await choose(st.order[0]); } else { fillSel(); placeCam(); } }
    relayoutAll();
  }
  function setLayout(l) { if (!LAYOUTS.includes(l)) return; st.layout = l; relayoutAll(); }
  // ---------------------------------------------------------------------------------------------------------------
  // Panels: side by side = two 4:3 frames (A3D left, photo right); the other layouts share the full stage
  // ---------------------------------------------------------------------------------------------------------------
  function sideRects(W, H) { const pw = Math.floor((W - 6) / 2), ph = Math.min(H - 40, Math.round(pw * 0.75)), y0 = Math.max(20, Math.floor((H - ph) / 2));
    return { gl: [0, y0, pw, ph], photo: [W - pw, y0, pw, ph] }; }
  function rects() { const s = $('stage'), W = s.clientWidth, H = s.clientHeight;
    return st.on && fpOn && st.layout === 'side' ? sideRects(W, H) : { gl: [0, 0, W, H], photo: [0, 0, W, H] }; }
  function glRect(W, H) { return st.on && st.layout === 'side' ? sideRects(W, H).gl : [0, 0, W, H]; }
  // the A3D lens follows the street-view lens: same horizontal FOV, vertical FOV from the A3D panel's aspect
  function syncLens() { if (!st.on || !fpOn) return; const g = rects().gl, v = 2 * Math.atan(Math.tan(st.hfov * D2R / 2) * g[3] / g[2]) / D2R;
    if (Math.abs(EV.fpCam().vfov - v) > 1e-6) EV.setFp({ vfov: v }); }
  // ---------------------------------------------------------------------------------------------------------------
  // Photo layer (WebGL2)
  // ---------------------------------------------------------------------------------------------------------------
  const VS = `#version 300 es
in vec2 q;out vec2 s;void main(){s=q;gl_Position=vec4(q,0.0,1.0);}`;
  // equirect (crop==0): camera ray (x right, y up, z forward; tanv = tan of the half FOVs) -> M -> panorama-local ray
  // (x right of the panorama centre, y up, z = panorama centre H) -> longitude / latitude -> (u, v) as pano.html.
  // plain crop (crop==1, non-equirect sources): source pixels mapped linearly around (c0), half extent cr, no warp.
  const FS = `#version 300 es
precision highp float;in vec2 s;out vec4 o;uniform sampler2D t;uniform mat3 M;uniform vec2 tanv;uniform int mode;uniform int crop;uniform vec2 c0;uniform vec2 cr;uniform vec2 texel;uniform float alpha;
const float PI=3.14159265358979;
vec2 uvOf(vec2 q){if(crop==1)return vec2(fract(c0.x+q.x*cr.x),c0.y-q.y*cr.y);
 vec3 d=normalize(M*vec3(q.x*tanv.x,q.y*tanv.y,1.0));return vec2(fract(0.5+atan(d.x,d.z)/(2.0*PI)),0.5-asin(clamp(d.y,-1.0,1.0))/PI);}
float lum(vec2 uv){return dot(texture(t,uv).rgb,vec3(0.299,0.587,0.114));}
void main(){vec2 uv=uvOf(s);
 if(mode==0){o=vec4(texture(t,uv).rgb,1.0)*alpha;return;}
 vec2 e=texel*1.5;float a=lum(uv-e),b=lum(uv+vec2(0.0,-e.y)),c=lum(uv+vec2(e.x,-e.y)),l=lum(uv+vec2(-e.x,0.0)),r=lum(uv+vec2(e.x,0.0)),
  g=lum(uv+vec2(-e.x,e.y)),h=lum(uv+vec2(0.0,e.y)),i=lum(uv+e);
 float gx=(c+2.0*r+i)-(a+2.0*l+g),gy=(g+2.0*h+i)-(a+2.0*b+c),m=smoothstep(0.32,0.8,length(vec2(gx,gy)));
 o=vec4(1.0,0.15,0.85,1.0)*m*alpha;}`;
  function glInit() {
    const gl = $('evsv').getContext('webgl2', { antialias: false, preserveDrawingBuffer: true, premultipliedAlpha: true });
    if (!gl) throw new Error('WebGL2 unavailable for the photo layer');
    const sh = (k, src) => { const x = gl.createShader(k); gl.shaderSource(x, src); gl.compileShader(x); if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x)); return x; };
    const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    gl.useProgram(p); const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const a = gl.getAttribLocation(p, 'q'); gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
    const u = {}; for (const n of ['t', 'M', 'tanv', 'mode', 'crop', 'c0', 'cr', 'texel', 'alpha']) u[n] = gl.getUniformLocation(p, n);
    const tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
    // no mipmaps: the 1024 px panorama is magnified on screen, and a mip chain would draw a seam where u wraps (fract)
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.REPEAT], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    gl.uniform1i(u.t, 0); R.gl = gl; R.u = u; R.tex = tex;
  }
  function upload(im) {
    if (!R.gl) glInit(); const gl = R.gl; gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, R.tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, im); R.size = [im.naturalWidth, im.naturalHeight];
  }
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]], dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const photoHeading = () => ((st.pano.base_heading + st.off.dh) % 360 + 360) % 360;
  const bearingOf = f => ((-f.yaw % 360) + 360) % 360;
  // world axes: x east, y north, z up. Camera: bearing = -yaw (RT.YAW_OF_HEADING), forward / right / up as editor_views lookAt.
  function matrix(f) {
    const b = bearingOf(f) * D2R, p = f.pitch * D2R, fw = [Math.sin(b) * Math.cos(p), Math.cos(b) * Math.cos(p), Math.sin(p)], rt = [Math.cos(b), -Math.sin(b), 0], up = cross(rt, fw);
    const H = photoHeading() * D2R, tp = st.off.dp * D2R, tr = st.off.dr * D2R;
    const f0 = [Math.sin(H), Math.cos(H), 0], r0 = [Math.cos(H), -Math.sin(H), 0], u0 = [0, 0, 1];
    const f1 = f0.map((v, i) => v * Math.cos(tp) + u0[i] * Math.sin(tp)), u1 = u0.map((v, i) => v * Math.cos(tp) - f0[i] * Math.sin(tp));
    const r2 = r0.map((v, i) => v * Math.cos(tr) + u1[i] * Math.sin(tr)), u2 = u1.map((v, i) => v * Math.cos(tr) - r0[i] * Math.sin(tr));
    const loc = v => [dot(v, r2), dot(v, u2), dot(v, f1)];
    return new Float32Array([...loc(rt), ...loc(up), ...loc(fw)]);   // column-major: images of camera x, y, z
  }
  function render() {
    syncLens();
    const c = $('evsv'); if (!st.on || c.style.display === 'none') { hud(); return; }
    if (!st.ready || !R.gl) { if (R.gl) { R.gl.clearColor(0, 0, 0, 0); R.gl.clear(R.gl.COLOR_BUFFER_BIT); } hud(); return; }
    const gl = R.gl, f = EV.fpCam(), pr = rects().photo, asp = pr[2] / pr[3], th = Math.tan(st.hfov * D2R / 2);
    gl.viewport(0, 0, c.width, c.height); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniformMatrix3fv(R.u.M, false, matrix(f)); gl.uniform2f(R.u.tanv, th, th / asp);   // square pixels: tan(vfov/2) = tan(hfov/2) / aspect
    gl.uniform1i(R.u.crop, isEq() ? 0 : 1);
    const hu = st.hfov / 720, u0 = 0.5 + (((bearingOf(f) - photoHeading()) % 360 + 540) % 360 - 180) / 360;   // plain crop: same source px per screen px in x and y
    gl.uniform2f(R.u.c0, u0, 0.5 - f.pitch / 180); gl.uniform2f(R.u.cr, hu, hu * R.size[0] / R.size[1] / asp);
    gl.uniform1i(R.u.mode, st.layout === 'edges' ? 1 : 0);
    gl.uniform2f(R.u.texel, 1 / R.size[0], 1 / R.size[1]); gl.uniform1f(R.u.alpha, st.layout === 'overlay' ? st.opacity : 1);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, R.tex); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    hud();
  }
  function distM() { if (!st.pano) return 0; const f = EV.fpCam(), [x, y] = station(); return Math.hypot(f.x - x, f.y - y) / EV.scale(); }
  function hud() {
    const c = $('evhud'); if (!st.on || !st.pano || !fpOn || c.style.display === 'none') return;
    const d = devicePixelRatio || 1, x = c.getContext('2d'), w = c.clientWidth, h = c.clientHeight; x.setTransform(d, 0, 0, d, 0, 0);
    x.font = 'bold 12px ui-monospace,Menlo,monospace'; x.textBaseline = 'top';
    const tag = (s, px, py, col) => { const tw = x.measureText(s).width; px = Math.max(4, Math.min(w - tw - 6, px)); x.fillStyle = '#000c'; x.fillRect(px - 3, py - 2, tw + 6, 16); x.fillStyle = col || '#55d8ef'; x.fillText(s, px, py); return tw; };
    const p = st.pano, o = st.off, f = EV.fpCam(), dm = distM(), r = rects(), brg = bearingOf(f).toFixed(0).padStart(3, '0');
    const off = Math.hypot(o.dE, o.dN), au = st.auto && st.auto.moved_m != null && Math.abs(o.dE - st.auto.dE) < 1e-6 && Math.abs(o.dN - st.auto.dN) < 1e-6;
    const sa = st.al[p.id], saved = sa && Math.abs(o.dE - sa.dE) < 1e-6 && Math.abs(o.dN - sa.dN) < 1e-6;
    const dPano = `pano ${dm < 0.05 ? 'at the camera' : dm.toFixed(1) + ' m from the camera (parallax)'} · camera ${off < 0.01 ? 'at the GPS point' : off.toFixed(1) + ' m from the GPS point' + (au ? ' (auto: GPS inside a building, unsaved)' : saved ? ' (saved alignment)' : ' (nudged, unsaved)')}`;
    const phot = `photo heading ${photoHeading().toFixed(1)}° = compass ${p.compass.toFixed(1)}${p.offset == null ? ' (no authored offset)' : ' ' + (p.offset >= 0 ? '+' : '') + p.offset}${o.dh ? ' ' + (o.dh >= 0 ? '+' : '') + o.dh : ''}`;
    if (st.layout === 'side') {
      const g = r.gl, q = r.photo;
      const gv = f.gameView;   // editor_views.js game view: the Classic frame from the game's camera behind and above the camera point
      tag(gv ? `GAME VIEW (Classic) · game camera ${gv.back_m.toFixed(0)} m behind, ${gv.up_m.toFixed(0)} m above the camera point · bearing ${brg}° · photo eye ${o.h.toFixed(1)} m`
        : `A3D first person · bearing ${brg}° · pitch ${f.pitch.toFixed(0)}° · lens ${st.hfov.toFixed(0)}° h · eye ${o.h.toFixed(1)} m`, g[0] + 6, g[1] - 18, '#ffd166');
      tag(`PHOTO ${p.id} · ${(p.timestamp || '').slice(0, 10)} · bearing ${brg}° · same lens`, q[0] + 6, q[1] - 18, '#55d8ef');
      tag(`${projLabel()} · ${phot}${st.loading ? ' · ' + st.loading : ''}${st.err ? ' · ERROR ' + st.err : ''}`, q[0] + 6, q[1] + q[3] + 4, isEq() ? '#9fd8e6' : '#ff9f43');
      tag(dPano, q[0] + 6, q[1] + q[3] + 22, au ? '#ff9f43' : '#9fd8e6');
      x.font = '10px ui-monospace,Menlo,monospace'; tag('photo © Mapillary contributors, CC BY-SA 4.0 · qualitative guide: judge plausibility, not pixel alignment', g[0] + 6, g[1] + g[3] + 6, '#ccc');
      return;
    }
    tag(`STREET VIEW ${p.id} · ${st.layout}${st.layout === 'overlay' ? ' ' + Math.round(st.opacity * 100) + '%' : ''} · bearing ${brg}° · ${dPano}`, w - 640, 8);
    tag(`${projLabel()} · ${phot}`, w - 640, 26);
    x.font = 'bold 14px ui-monospace,Menlo,monospace'; tag(APPROX.toUpperCase(), 10, 8, '#ff9f43');
    x.font = '10px ui-monospace,Menlo,monospace'; tag('photo © Mapillary contributors, CC BY-SA 4.0', 8, h - 40, '#ddd'); x.font = 'bold 12px ui-monospace,Menlo,monospace';
    if (st.layout === 'wipe') { const px = st.wipe * w; tag('◀ PHOTO', px - 80, 48, '#ffd166'); tag('A3D ▶', px + 10, 48, '#ffd166'); }
  }
  // ---------------------------------------------------------------------------------------------------------------
  // Layout + panel
  // ---------------------------------------------------------------------------------------------------------------
  let fpOn = false;
  function relayout(on) { fpOn = !!on; apply(); }
  function relayoutNow() { apply(); EV.redraw(); }
  function relayoutAll() { if (EV.state().mode === 'fp') EV.relayout(); else apply(); EV.redraw(); }  // EV.relayout re-places the A3D panel, then calls relayout()
  function apply() {
    const c = $('evsv'), wp = $('evwipe'), show = fpOn && st.on;
    if (!show) { c.style.display = 'none'; wp.style.display = 'none'; sync(); return; }
    const d = devicePixelRatio || 1, r = rects().photo;
    c.style.display = 'block'; c.style.left = r[0] + 'px'; c.style.top = r[1] + 'px'; c.style.width = r[2] + 'px'; c.style.height = r[3] + 'px';
    c.style.pointerEvents = st.layout === 'side' ? 'auto' : 'none'; c.style.cursor = st.layout === 'side' ? 'grab' : '';
    const w = Math.max(1, Math.round(r[2] * d)), h = Math.max(1, Math.round(r[3] * d)); if (c.width !== w) c.width = w; if (c.height !== h) c.height = h;
    if (st.layout === 'wipe') { const px = Math.round(st.wipe * r[2]); c.style.clipPath = `inset(0px ${r[2] - px}px 0px 0px)`;
      wp.style.display = 'block'; wp.style.left = (r[0] + px) + 'px'; wp.style.top = r[1] + 'px'; wp.style.height = r[3] + 'px'; }
    else { c.style.clipPath = 'none'; wp.style.display = 'none'; }
    syncLens(); sync();
  }
  function sync() {
    if (!$('svOn')) return;
    $('svOn').textContent = st.on ? 'on' : 'off'; $('svOn').classList.toggle('on', st.on); $('svMore').style.display = st.on ? 'block' : 'none';
    for (const id of ['svPrev', 'svNext', 'svSel', 'svNear']) $(id).style.display = st.on ? '' : 'none';
    document.querySelectorAll('[data-svl]').forEach(b => b.classList.toggle('on', b.dataset.svl === st.layout));
    $('svOpBox').style.display = st.layout === 'overlay' ? '' : 'none';
    $('svOp').value = Math.round(st.opacity * 100); $('svOpV').textContent = $('svOp').value + '%';
    $('svFov').value = Math.round(st.hfov); $('svFovV').textContent = Math.round(st.hfov) + '°';
    $('svAlign').style.display = st.alignOpen ? 'block' : 'none'; $('svAlignT').textContent = 'alignment (optional) ' + (st.alignOpen ? '▾' : '▸');
    const o = st.off, sg = v => (v >= 0 ? '+' : '') + v.toFixed(2);
    $('svOff').innerHTML = st.pano ? `offsets <b>dE ${sg(o.dE)} m · dN ${sg(o.dN)} m · heading ${sg(o.dh)}° · pitch ${sg(o.dp)}° · roll ${sg(o.dr)}° · height ${o.h.toFixed(2)} m</b>` : '<span class="dim">no panorama</span>';
    const sv = st.pano && savedOff(st.pano.id), a = st.pano && st.al[st.pano.id], same = sv && Object.keys(o).every(k => Math.abs(o[k] - sv[k]) < 1e-6);
    $('svSaved').textContent = st.msg || (!st.pano ? '' : !a ? (same === false || Object.keys(o).some(k => o[k] !== defOff()[k]) ? 'unsaved nudge' : '') : same ? `saved ${a.at} by ${a.by}` : 'UNSAVED changes (saved ' + a.at + ')');
  }
  async function save(o) {
    if (!st.pano) return null; o = o || {};
    const body = { capture_id: st.pano.id, dE: st.off.dE, dN: st.off.dN, dheading_deg: st.off.dh, dpitch_deg: st.off.dp, droll_deg: st.off.dr, height_m: st.off.h, fov_deg: st.hfov, by: o.by || 'operator', note: o.note || '' };
    try { const doc = await getJSON('/api/sv/alignment', body); st.al = doc.alignments || {}; st.msg = ''; fillSel(); sync(); if (typeof status === 'function') status('saved alignment of ' + st.pano.id + ' → sv_alignment.json'); return st.al[st.pano.id]; }
    catch (e) { st.msg = 'SAVE FAILED: ' + e.message; sync(); throw e; }
  }
  // ---------------------------------------------------------------------------------------------------------------
  // Keys (first person only; the page ignores keys while a text field has focus)
  // ---------------------------------------------------------------------------------------------------------------
  addEventListener('keydown', e => {
    if ($('dlg') && $('dlg').style.display === 'block') return;
    if (e.target.tagName === 'INPUT' && e.target.type !== 'range' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === 'v') { toggle(); return; }
    if (!st.on || EV.state().mode !== 'fp') return;
    if (k === ',') step(-1); else if (k === '.') step(1);
    else if (k === 'c') setLayout(LAYOUTS[(LAYOUTS.indexOf(st.layout) + 1) % LAYOUTS.length]);
    else if (k === '[' || k === '{') nudge('dh', k === '{' ? -4 : -1); else if (k === ']' || k === '}') nudge('dh', k === '}' ? 4 : 1);
  });
  // ---------------------------------------------------------------------------------------------------------------
  // Automation (ev_pose_shots.mjs): set({on, pano, bearing, pitch, fov (horizontal, both panels), height_m, dE, dN,
  // dheading_deg, dpitch_deg, droll_deg, layout, opacity, wipe}) -> state(). Saved alignments apply first; explicit
  // fields override them (for that view only; nothing is saved).
  // ---------------------------------------------------------------------------------------------------------------
  async function set(o) {
    o = o || {};
    if (o.layout) st.layout = o.layout; if (o.opacity != null) st.opacity = o.opacity; if (o.wipe != null) st.wipe = o.wipe; if (o.fov != null) st.hfov = o.fov;
    if (o.on !== false) { await toggle(true); if (o.pano && (!st.pano || st.pano.id !== o.pano || Object.keys(o).some(k => ['bearing', 'pitch', 'height_m', 'dE', 'dN', 'dheading_deg', 'dpitch_deg', 'droll_deg'].includes(k)))) await choose(o.pano, o); }
    else await toggle(false);
    relayoutAll(); return state();
  }
  function state() {
    const f = window.EV && EV.fpCam ? EV.fpCam() : null, en = f ? EV.toEN(f.x, f.y) : [null, null], p = st.pano;
    return { on: st.on, layout: st.layout, opacity: st.opacity, ready: st.ready, err: st.err, pano: p && p.id, pano_E: p && p.E, pano_N: p && p.N,
      projection: p && p.projection, base_heading_deg: p && p.base_heading, authored_offset_deg: p && p.offset, compass_deg: p && p.compass, photo_heading_deg: p ? photoHeading() : null,
      offsets: { ...st.off }, auto_step_out: st.auto || null, saved_alignment: p ? st.al[p.id] || null : null, hfov_deg: st.hfov, rects: rects(),
      camera: f && { E: en[0], N: en[1], eye_z_world: f.eyeZ, height_m: f.eyeM, bearing_deg: bearingOf(f), pitch_deg: f.pitch, a3d_vfov_deg: f.vfov, a3d_aspect: f.w / f.h },
      dist_from_station_m: p ? distM() : null, image_px: R.size || null };
  }
  injectDom(); sync();
  return { relayout, render, set, state, save, toggle, choose, nudge, glRect, _st: st };
})();
