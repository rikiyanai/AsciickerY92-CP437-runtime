// editor_views.js -- FL-4876 A3D editor views (operator 2026-10-16): first-person camera, top-down ("satellite")
// view of the A3D world, a vertical split with a registered reference map below it, and an overlay with one
// opacity per layer. Loaded by a3d.html after its own script; uses its globals S, RT, zAt, camMoved, hud, status.
//
// Why a mesh preview next to the Classic runtime: the Classic renderer has a fixed 30 degree tilt, and its perspective
// mode depth-tests on height (engine/render/render_world_pass.cpp:849 `s->height < z`; perspective branch: depth = vz),
// so it can draw neither a straight-down view nor a street-level eye. The former
// "First person" label was false (operator 2026-10-21). Walk has two explicitly different views:
//   CLASSIC CAMERA (default): the game's own Classic
//   frame (wasm .web-classic, the ::Render call engine/game_render_bridge.cpp makes) with the game's camera: target =
//   the walker's feet, yaw = the walker's bearing, zoom 1, perspective on (game.cpp InitGame session.perspective = true).
//   The eye sits max(cols,rows)+2 world units behind and tan30 x that above the walker (render_scene.cpp focal), and the
//   game's default player sprite (player-0000.xp) stands at the walker. WASD / look drive it like the mesh view.
//   MESH VIEW: the WebGL preview below, eye level, for editing.
// The mesh view and top-down draw the SAME build in WebGL2: terrain patches,
// material table and every mesh instance of output.a3d with the .akm vertex colours, exported by
// editor_views.py (/api/ev/geometry). Units: classic world units x, y (east, north) and z = engine z / 16, so
// 1 m = content_scale units on every axis. World <-> UTM uses terrain_metadata.json through /api/ev/meta:
// x = (E - origin_easting) * content_scale + offset_x (the convention of map_editor.Projection and /api/where).
// Reference layers (2D canvas, local data only): the GSI aerial mosaic /aerial.jpg drawn with
// map_editor.aerial_affine (least-squares UTM -> mosaic px), and a street map drawn from the AOI OSM extract
// (/api/ev/osm). Both panels share one view (centre + metres per pixel), so pan / zoom in either moves both.
// Keys: P walk (X Classic trailing camera / eye-level geometry preview), O top-down, K cycle layout (a3d / split / overlay), J cycle reference (satellite / map /
// both). The normal Classic view, S.cam and every existing control are untouched while these modes are off.
'use strict';
window.EV = (() => {
  const $ = id => document.getElementById(id);
  const st = {
    mode: 'off', layout: 'split', ref: 'sat', op: { a3d: 1, sat: 1, map: 1 },
    top: { x: 0, y: 0, mpp: 0.25 }, fp: { x: 0, y: 0, yaw: 0, pitch: 0, eyeM: 1.6, vfov: 60, game: true, gameCam: 'player', gameZoom: 1 }, game: null, gameIdle: 0,
    meta: null, osm: null, aerial: null, geo: null, loading: null, written: null, dirty: true, keys: new Set(), shift: false, err: null
  };
  const R = { gl: null };
  // ---------------------------------------------------------------------------------------------------------
  // DOM: two bar buttons, three canvases inside #stage (WebGL world, 2D reference, 2D hud), a control panel.
  // ---------------------------------------------------------------------------------------------------------
  const css = `body.ev-on #gl,body.ev-on #ov{visibility:hidden}body.ev-on.ev-game #gl{visibility:visible}
#evgl,#evref,#evhud{position:absolute;left:0;top:0;display:none}#evhud{pointer-events:none}
#evctl{position:fixed;left:8px;bottom:30px;z-index:6;background:#15151bf2;border:1px solid #333;padding:6px 8px;display:none;line-height:1.9;max-width:560px}
#evctl .row{white-space:nowrap}#evctl input[type=range]{width:110px;vertical-align:middle}#evctl .v{display:inline-block;width:34px;text-align:right;color:var(--dim)}
#evctl b{color:var(--acc)}`;
  function injectDom() {
    const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s);
    const after = $('bPersp');
    const mk = (id, txt, title) => { const b = document.createElement('button'); b.id = id; b.textContent = txt; b.title = title; return b; };
    // Numabe camera correction (2026-10-21): Classic has a fixed tilted
    // trailing camera. Never advertise it as a first-person game camera.
    const bFP = mk('bFP', 'Walk (Classic)', 'walk with the Classic trailing camera; not first-person gameplay (P)');
    const bTop = mk('bTop', 'Top-down', 'top-down view of the A3D world, split / overlay with the GSI aerial or the OSM street map (O)');
    after.after(bFP); bFP.after(bTop);
    bFP.onclick = () => setMode(st.mode === 'fp' ? 'off' : 'fp');
    bTop.onclick = () => setMode(st.mode === 'top' ? 'off' : 'top');
    for (const id of ['evgl', 'evref', 'evhud']) { const c = document.createElement('canvas'); c.id = id; $('stage').appendChild(c); }
    const ctl = document.createElement('div'); ctl.id = 'evctl'; document.body.appendChild(ctl);
    ctl.innerHTML = `<div id="evTopCtl"><div class="row"><b>Top-down</b> layout <button data-evl="single">A3D</button><button data-evl="split">Split</button><button data-evl="overlay">Overlay</button>
 · reference <button data-evr="sat">Satellite</button><button data-evr="map">Map</button><button data-evr="both">Both</button></div>
<div class="row">opacity a3d <input type="range" min="0" max="100" data-evo="a3d"><span class="v" id="evo_a3d"></span>
 satellite <input type="range" min="0" max="100" data-evo="sat"><span class="v" id="evo_sat"></span>
 map <input type="range" min="0" max="100" data-evo="map"><span class="v" id="evo_map"></span></div>
<div class="row dim">drag / WASD pan · wheel or +/- zoom · K layout · J reference · O exit</div></div>
<div id="evFpCtl"><div class="row"><b id="evCameraKind">Classic walk camera</b> <button id="evGame" title="Classic's fixed-tilt trailing camera, not first person (X)">Classic camera</button><button id="evMesh" title="WebGL geometry preview at eye level; NOT CP437 gameplay (X)">Eye-level preview</button>
 · eye <input type="range" min="10" max="60" id="evEye"><span class="v" id="evEyeV"></span> m <span id="evGameV" class="dim"></span></div>
<div class="row dim">W/S walk · A/D step · Shift run · drag look · Q/E turn · X Classic / geometry preview · P exit</div>
<div class="row bad" id="evCameraLimit">Classic first-person gameplay is not implemented. Eye height and pitch apply only to the geometry preview.</div></div>
<div id="evMsg" class="dim"></div>`;
    ctl.querySelectorAll('[data-evl]').forEach(b => b.onclick = () => { st.layout = b.dataset.evl; relayout(); });
    ctl.querySelectorAll('[data-evr]').forEach(b => b.onclick = () => { st.ref = b.dataset.evr; ensureRef(); relayout(); });
    // sliders hand the keyboard back on release (a3d.html ignores keys while an INPUT has focus)
    ctl.querySelectorAll('[data-evo]').forEach(r => { r.oninput = () => { st.op[r.dataset.evo] = r.value / 100; relayout(); }; r.onchange = () => r.blur(); });
    $('evEye').oninput = () => { st.fp.eyeM = $('evEye').value / 10; st.dirty = true; syncCtl(); }; $('evEye').onchange = () => $('evEye').blur();
    $('evGame').onclick = () => { setGame(true); $('evGame').blur(); }; $('evMesh').onclick = () => { setGame(false); $('evMesh').blur(); };
    const help = $('help'); if (help) help.insertAdjacentHTML('beforeend', '<br><b>Views</b>: <kbd>P</kbd> Classic walk camera (NOT first-person gameplay). <kbd>X</kbd> switches to an eye-level WebGL geometry preview, NOT the CP437 game. Eye height and pitch affect only that preview. <kbd>O</kbd> top-down · <kbd>K</kbd> layout A3D / split / overlay · <kbd>J</kbd> satellite / street map / both.');
    for (const c of [$('evgl'), $('evref')]) bindPointer(c);
  }
  function syncCtl() {
    const ctl = $('evctl'); if (!ctl) return;
    ctl.style.display = st.mode === 'off' ? 'none' : 'block';
    $('evTopCtl').style.display = st.mode === 'top' ? 'block' : 'none'; $('evFpCtl').style.display = st.mode === 'fp' ? 'block' : 'none';
    ctl.querySelectorAll('[data-evl]').forEach(b => b.classList.toggle('on', b.dataset.evl === st.layout));
    ctl.querySelectorAll('[data-evr]').forEach(b => b.classList.toggle('on', b.dataset.evr === st.ref));
    ctl.querySelectorAll('[data-evo]').forEach(r => { r.value = Math.round(st.op[r.dataset.evo] * 100); $('evo_' + r.dataset.evo).textContent = r.value + '%'; });
    $('evEye').value = Math.round(st.fp.eyeM * 10); $('evEyeV').textContent = st.fp.eyeM.toFixed(1);
    $('evEye').disabled = !!st.fp.game;
    $('evCameraKind').textContent = st.fp.game ? 'Classic walk camera (not first person)' : 'Eye-level geometry preview (not gameplay)';
    $('bFP').classList.toggle('on', st.mode === 'fp'); $('bTop').classList.toggle('on', st.mode === 'top');
    $('evGame').classList.toggle('on', !!st.fp.game); $('evMesh').classList.toggle('on', !st.fp.game); if (!gameOn()) $('evGameV').textContent = '';
    $('evMsg').textContent = st.err ? 'ERROR: ' + st.err : st.loading || '';
  }
  // Panel rectangles in CSS px relative to #stage: {gl:[x,y,w,h], ref:[...]|null}
  function panels() {
    const s = $('stage'), W = s.clientWidth, H = s.clientHeight;
    if (st.mode === 'fp') return { gl: window.SV && SV.glRect ? SV.glRect(W, H) : [0, 0, W, H], ref: null }; // street view side by side: left half
    if (st.layout === 'split') { const h = Math.floor(H / 2); return { gl: [0, 0, W, h - 1], ref: [0, h + 1, W, H - h - 1] }; }
    if (st.layout === 'overlay') return { gl: [0, 0, W, H], ref: [0, 0, W, H] };
    return { gl: [0, 0, W, H], ref: null };
  }
  function place(c, r) {
    const d = devicePixelRatio || 1;
    if (!r) { c.style.display = 'none'; return; }
    c.style.display = 'block'; c.style.left = r[0] + 'px'; c.style.top = r[1] + 'px'; c.style.width = r[2] + 'px'; c.style.height = r[3] + 'px';
    const w = Math.max(1, Math.round(r[2] * d)), h = Math.max(1, Math.round(r[3] * d)); if (c.width !== w) c.width = w; if (c.height !== h) c.height = h;
  }
  const gameOn = () => st.mode === 'fp' && !!st.fp.game;
  function relayout() {
    const on = st.mode !== 'off'; document.body.classList.toggle('ev-on', on); document.body.classList.toggle('ev-game', gameOn());
    if (!gameOn()) { if (typeof gameEnd === 'function') gameEnd(); playerSprite(null); st.game = null; }  // #gl back to the Classic view
    const p = on ? panels() : { gl: null, ref: null };
    place($('evgl'), p.gl); place($('evref'), p.ref); place($('evhud'), on ? [0, 0, $('stage').clientWidth, $('stage').clientHeight] : null);
    $('evgl').style.opacity = st.mode === 'top' ? st.op.a3d : 1;
    $('evref').style.background = 'transparent';
    if (window.SV) SV.relayout(on && st.mode === 'fp', p.gl); // street-view photo layer (street_view_overlay.js)
    syncCtl(); st.dirty = true;
  }
  // ---------------------------------------------------------------------------------------------------------
  // Coordinates
  // ---------------------------------------------------------------------------------------------------------
  const P = () => st.meta.proj;
  const toEN = (x, y) => [(x - P().offset_x) / P().content_scale + P().origin_easting, (y - P().offset_y) / P().content_scale + P().origin_northing];
  const toXY = (E, N) => [(E - P().origin_easting) * P().content_scale + P().offset_x, (N - P().origin_northing) * P().content_scale + P().offset_y];
  const scale = () => (st.meta ? P().content_scale : S.metaScale);
  // ---------------------------------------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------------------------------------
  async function getJSON(u) { const r = await fetch(u); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j; }
  async function ensureMeta() { if (!st.meta) st.meta = await getJSON('/api/ev/meta'); }
  async function ensureGeo() {
    if (R.gl) return;
    if (st.geoPromise) return st.geoPromise;
    st.geoPromise = (async () => {
      st.loading = 'loading the A3D geometry of ' + (st.meta && st.meta.build) + ' (first time ~10 s)…'; syncCtl();
      const t0 = performance.now(), buf = await (await fetch('/api/ev/geometry')).arrayBuffer();
      const dv = new DataView(buf), magic = String.fromCharCode(...new Uint8Array(buf, 0, 4));
      if (magic !== 'EVG1') throw new Error('geometry: bad magic ' + magic);
      const hl = dv.getUint32(4, true), head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, hl))), base = 8 + hl;
      const A = { uint16: Uint16Array, uint8: Uint8Array, int8: Int8Array, float32: Float32Array, uint32: Uint32Array };
      const sec = {}; for (const [k, [off, len, dt, shape]] of Object.entries(head.sections)) sec[k] = { a: new A[dt](buf, base + off, len / A[dt].BYTES_PER_ELEMENT), shape };
      st.geo = { head, sec, ms: Math.round(performance.now() - t0), mb: Math.round(buf.byteLength / 1048576) };
      glInit(); st.loading = null; st.dirty = true; syncCtl();
      clog('ev_geometry', `${st.geo.mb} MB in ${st.geo.ms} ms; ${head.instances} instances, ${sec.pos.shape[0]} vertices, ${sec.idx.shape[0] / 3} triangles`);
    })().catch(e => { st.err = 'geometry: ' + e.message; st.geoPromise = null; syncCtl(); throw e; });
    return st.geoPromise;
  }
  function ensureRef() {
    if ((st.ref === 'sat' || st.ref === 'both' || st.layout === 'overlay') && !st.aerial) {
      st.aerial = new Image(); st.aerial.onload = () => { st.dirty = true; }; st.aerial.src = '/aerial.jpg';
    }
    if ((st.ref === 'map' || st.ref === 'both' || st.layout === 'overlay') && !st.osm && !st.osmPromise) {
      st.osmPromise = getJSON('/api/ev/osm').then(o => { for (const w of o.ways) { let a = 1e18, b = 1e18, c = -1e18, d = -1e18; for (const p of w.p) { a = Math.min(a, p[0]); b = Math.min(b, p[1]); c = Math.max(c, p[0]); d = Math.max(d, p[1]); } w.bb = [a, b, c, d]; } st.osm = o; st.dirty = true; })
        .catch(e => { st.err = 'osm: ' + e.message; syncCtl(); });
    }
  }
  // ---------------------------------------------------------------------------------------------------------
  // WebGL2 world renderer
  // ---------------------------------------------------------------------------------------------------------
  const CH = 64; // terrain chunk: 64 x 64 quads of 2 world units
  const TERR_VS = `#version 300 es
precision highp float;precision highp int;precision highp usampler2D;
in vec2 aIJ;uniform ivec2 uOrg;uniform ivec2 uGrid;uniform usampler2D uH;uniform mat4 uMVP;uniform vec3 uEye;uniform vec2 uW0;
out vec3 vW;out vec3 vN;out float vD;
float hAt(ivec2 g){g=clamp(g,ivec2(0),uGrid-1);return float(texelFetch(uH,g,0).r)/16.0;}
void main(){ivec2 g=min(uOrg+ivec2(aIJ),uGrid-1);float h=hAt(g);
 vN=vec3(hAt(g-ivec2(1,0))-hAt(g+ivec2(1,0)),hAt(g-ivec2(0,1))-hAt(g+ivec2(0,1)),4.0);
 vW=vec3(vec2(g)*2.0+uW0,h);vD=distance(vW,uEye);gl_Position=uMVP*vec4(vW,1.0);}`;
  const TERR_FS = `#version 300 es
precision highp float;precision highp int;precision highp usampler2D;
in vec3 vW;in vec3 vN;in float vD;uniform usampler2D uVis;uniform sampler2D uLut;uniform ivec2 uVisSize;uniform vec3 uL;uniform vec3 uSky;uniform vec2 uFog;uniform vec2 uW0;out vec4 o;
void main(){ivec2 c=clamp(ivec2(floor(vW.xy-uW0)),ivec2(0),uVisSize-1);int m=int(texelFetch(uVis,c,0).r);
 float d=0.5+0.5*max(0.0,dot(normalize(vN),uL));int shd=int(clamp(floor(d*15.0+0.5),0.0,15.0));
 vec3 col=texelFetch(uLut,ivec2(m,shd),0).rgb;float f=clamp((vD-uFog.x)/(uFog.y-uFog.x),0.0,1.0);o=vec4(mix(col,uSky,f),1.0);}`;
  const MESH_VS = `#version 300 es
precision highp float;in vec3 aP;in vec4 aN;in vec4 aC;uniform mat4 uMVP;uniform vec3 uEye;out vec3 vC;out vec3 vN;out float vD;
void main(){vC=aC.rgb;vN=aN.xyz;vD=distance(aP,uEye);gl_Position=uMVP*vec4(aP,1.0);}`;
  const MESH_FS = `#version 300 es
precision highp float;in vec3 vC;in vec3 vN;in float vD;uniform vec3 uL;uniform vec3 uSky;uniform vec2 uFog;out vec4 o;
void main(){vec3 n=vN;float l=length(n);float d=l>0.01?0.5+0.5*max(0.0,dot(n/l,uL)):0.85;
 float f=clamp((vD-uFog.x)/(uFog.y-uFog.x),0.0,1.0);o=vec4(mix(vC*d,uSky,f),1.0);}`;
  function prog(gl, vs, fs) {
    const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o)); return o; };
    const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {}; const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); for (let i = 0; i < n; i++) { const a = gl.getActiveUniform(p, i); u[a.name] = gl.getUniformLocation(p, a.name); }
    return { p, u };
  }
  // Glyph coverage of the page's CP437 atlas (same luminance -> alpha rule as a3d.html loadFont), for the LUT.
  function glyphCoverage() {
    const cov = new Float32Array(256).fill(0.35), img = S.font && S.font.img; if (!img) return cov;
    const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height; const x = cv.getContext('2d'); x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, img.width, img.height).data, g = img.width / 16;
    for (let k = 0; k < 256; k++) { let s = 0; const gx = (k & 15) * g, gy = (k >> 4) * g;
      for (let yy = 0; yy < g; yy++) for (let xx = 0; xx < g; xx++) { const i = ((gy + yy) * img.width + gx + xx) * 4; s += (d[i] * 2 + d[i + 1] * 3 + d[i + 2] + 3) / 6; }
      cov[k] = s / (g * g * 255); }
    return cov;
  }
  function glInit() {
    const c = $('evgl'), gl = c.getContext('webgl2', { antialias: true, preserveDrawingBuffer: true });
    if (!gl) throw new Error('WebGL2 unavailable');
    const g = st.geo.sec, [gh, gw] = g.heights.shape, [vh, vw] = g.visual.shape;
    R.gl = gl; R.terr = prog(gl, TERR_VS, TERR_FS); R.mesh = prog(gl, MESH_VS, MESH_FS); R.grid = [gw, gh]; R.vis = [vw, vh];
    R.w0 = (st.geo.head.origin_patch || [0, 0]).map(v => v * 8); // world position of the grid's first vertex (negative patch coordinates)
    const tex = (unit, ifmt, fmt, type, w, h, data) => { const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1); gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, w, h, 0, fmt, type, data);
      for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
      return t; };
    R.tH = tex(0, gl.R16UI, gl.RED_INTEGER, gl.UNSIGNED_SHORT, gw, gh, g.heights.a);
    R.tV = tex(1, gl.R8UI, gl.RED_INTEGER, gl.UNSIGNED_BYTE, vw, vh, g.visual.a);
    // material LUT 256 x 16 shades: the Classic terrain cell colour = bg/fg of shade ramp 3 (flat 'lo'
    // elevation, render_resolve.cpp) mixed by the glyph's ink coverage in the current font
    const cov = glyphCoverage(), M = g.matlib.a, lut = new Uint8Array(256 * 16 * 4);
    for (let m = 0; m < 256; m++) for (let s = 0; s < 16; s++) { const o = ((m * 4 + 3) * 16 + s) * 8, a = cov[M[o + 3]], q = (s * 256 + m) * 4;
      for (let k = 0; k < 3; k++) lut[q + k] = Math.round(M[o + 4 + k] * (1 - a) + M[o + k] * a); lut[q + 3] = 255; }
    R.tL = tex(2, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, 256, 16, lut);
    // terrain chunk grid (shared by every chunk) + one index buffer per LOD stride
    const n = CH + 1, ij = new Float32Array(n * n * 2); for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) { ij[(j * n + i) * 2] = i; ij[(j * n + i) * 2 + 1] = j; }
    R.terrVao = gl.createVertexArray(); gl.bindVertexArray(R.terrVao);
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, ij, gl.STATIC_DRAW);
    const la = gl.getAttribLocation(R.terr.p, 'aIJ'); gl.enableVertexAttribArray(la); gl.vertexAttribPointer(la, 2, gl.FLOAT, false, 0, 0);
    R.lod = {}; for (const s of [1, 2, 4, 8]) { const ix = []; for (let j = 0; j < CH; j += s) for (let i = 0; i < CH; i += s) { const a = j * n + i, b2 = a + s, c2 = a + s * n, d2 = c2 + s; ix.push(a, b2, d2, a, d2, c2); }
      const e = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, e); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(ix), gl.STATIC_DRAW); R.lod[s] = { e, n: ix.length }; }
    gl.bindVertexArray(null);
    // meshes: one vertex array for the whole build
    R.meshVao = gl.createVertexArray(); gl.bindVertexArray(R.meshVao);
    const attr = (name, data, size, type, norm) => { const bb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bb); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const l = gl.getAttribLocation(R.mesh.p, name); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, size, type, norm, 0, 0); };
    attr('aP', g.pos.a, 3, gl.FLOAT, false); attr('aN', g.nrm.a, 4, gl.BYTE, true); attr('aC', g.col.a, 4, gl.UNSIGNED_BYTE, true);
    const eb = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, eb); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, g.idx.a, gl.STATIC_DRAW); R.meshN = g.idx.a.length;
    gl.bindVertexArray(null);
  }
  const L = (() => { const v = [1, 0, 1], n = Math.hypot(...v); return v.map(a => a / n); })(); // engine light (web_classic_main.cpp g_light)
  const SKY = [0.62, 0.77, 0.89];
  // column-major 4x4 helpers
  const mul = (a, b) => { const o = new Float32Array(16); for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s; } return o; };
  function perspective(fovy, asp, n, f) { const t = 1 / Math.tan(fovy / 2), o = new Float32Array(16); o[0] = t / asp; o[5] = t; o[10] = (f + n) / (n - f); o[11] = -1; o[14] = 2 * f * n / (n - f); return o; }
  function lookAt(e, c, u) { const z = norm3(sub3(e, c)), x = norm3(cross3(u, z)), y = cross3(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot3(x, e), -dot3(y, e), -dot3(z, e), 1]); }
  const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]], dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]], norm3 = a => { const l = Math.hypot(...a) || 1; return a.map(v => v / l); };
  // camera for the current mode -> {mvp, eye, fog, cull(chunk) -> lod stride or 0}
  function camera(w, h) {
    const s = scale();
    if (st.mode === 'fp') {
      const f = st.fp, ez = eyeZ(), r = f.yaw * Math.PI / 180, p = f.pitch * Math.PI / 180, fw = [-Math.sin(r) * Math.cos(p), Math.cos(r) * Math.cos(p), Math.sin(p)];
      const eye = [f.x, f.y, ez], mvp = mul(perspective(f.vfov * Math.PI / 180, w / h, 0.1 * s, 2500 * s), lookAt(eye, [eye[0] + fw[0], eye[1] + fw[1], eye[2] + fw[2]], [0, 0, 1]));
      const fh = norm3([fw[0], fw[1], 0]);
      return { mvp, eye, fog: [180 * s, 1100 * s], cull: (cx, cy) => { const dx = cx - eye[0], dy = cy - eye[1], d = Math.hypot(dx, dy), rad = CH * 2 * 0.75;
        if (d > 1200 * s + rad) return 0; if (d > rad && (dx * fh[0] + dy * fh[1]) < -rad) return 0; const m = d / s; return m < 90 ? 1 : m < 220 ? 2 : m < 500 ? 4 : 8; } };
    }
    const t = st.top, wpp = t.mpp * s, sx = 2 / (w * wpp), sy = 2 / (h * wpp), zc = 300, zr = 4000;
    const mvp = new Float32Array([sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, -1 / zr, 0, -t.x * sx, -t.y * sy, zc / zr, 1]);
    const hw = w / 2 * wpp + CH * 2, hh = h / 2 * wpp + CH * 2, lod = t.mpp < 0.6 ? 1 : t.mpp < 1.2 ? 2 : t.mpp < 2.5 ? 4 : 8;
    return { mvp, eye: [t.x, t.y, 1e6], fog: [1e9, 2e9], cull: (cx, cy) => (Math.abs(cx - t.x) < hw && Math.abs(cy - t.y) < hh ? lod : 0) };
  }
  function renderGL() {
    const gl = R.gl, c = $('evgl'); if (!gl || c.style.display === 'none') return;
    // game view: the panel stays transparent on top of #gl (the Classic frame shows through) and keeps the pointer (look / lock)
    if (gameOn()) { gl.viewport(0, 0, c.width, c.height); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); return; }
    gl.viewport(0, 0, c.width, c.height); gl.clearColor(...(st.mode === 'fp' ? SKY : [0, 0, 0]), 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.disable(gl.CULL_FACE);
    const cam = camera(c.clientWidth || c.width, c.clientHeight || c.height);
    // terrain
    const T = R.terr; gl.useProgram(T.p); gl.bindVertexArray(R.terrVao);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, R.tH); gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, R.tV); gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, R.tL);
    gl.uniform1i(T.u.uH, 0); gl.uniform1i(T.u.uVis, 1); gl.uniform1i(T.u.uLut, 2);
    gl.uniformMatrix4fv(T.u.uMVP, false, cam.mvp); gl.uniform3fv(T.u.uEye, cam.eye); gl.uniform2i(T.u.uGrid, R.grid[0], R.grid[1]); gl.uniform2i(T.u.uVisSize, R.vis[0], R.vis[1]); gl.uniform2f(T.u.uW0, R.w0[0], R.w0[1]);
    gl.uniform3fv(T.u.uL, L); gl.uniform3fv(T.u.uSky, SKY); gl.uniform2fv(T.u.uFog, cam.fog);
    let chunks = 0;
    for (let gj = 0; gj < R.grid[1] - 1; gj += CH) for (let gi = 0; gi < R.grid[0] - 1; gi += CH) {
      const lod = cam.cull((gi + CH / 2) * 2 + R.w0[0], (gj + CH / 2) * 2 + R.w0[1]); if (!lod) continue;
      gl.uniform2i(T.u.uOrg, gi, gj); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, R.lod[lod].e); gl.drawElements(gl.TRIANGLES, R.lod[lod].n, gl.UNSIGNED_SHORT, 0); chunks++;
    }
    // meshes
    const M = R.mesh; gl.useProgram(M.p); gl.bindVertexArray(R.meshVao);
    gl.uniformMatrix4fv(M.u.uMVP, false, cam.mvp); gl.uniform3fv(M.u.uEye, cam.eye); gl.uniform3fv(M.u.uL, L); gl.uniform3fv(M.u.uSky, SKY); gl.uniform2fv(M.u.uFog, cam.fog);
    gl.drawElements(gl.TRIANGLES, R.meshN, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null); R.lastChunks = chunks;
  }
  // ---------------------------------------------------------------------------------------------------------
  // Game view: the Classic frame (a3d.html gamePresent -> ClassicSetCamera + ClassicRender) with the game's camera
  // ---------------------------------------------------------------------------------------------------------
  const PLAYER_XP = '/assets/sprites/player-0000.xp'; // the game's default player sprite (actor_visual_profile_table.generated.h 'player-0000')
  const TAN30 = Math.tan(Math.PI / 6);
  // z (engine units) of the surface a walker stands on: the terrain, or a walkable top at most 1.2 m above it (platform, steps)
  function groundZ(x, y) { const g = RT.ground(x, y); if (!g) return zAt(x, y); return g.top_z - g.terrain_z <= 1.2 * scale() * 16 ? g.top_z : g.terrain_z; }
  // render_scene.cpp Render() with DBL (render_internal.h:32): the sample buffer is dw = 4 + 2*cols by dh = 4 + 2*rows and
  // focal = 2*max(dw, dh) (line 762); the perspective eye (view_pos, line 767) is focal / HEIGHT_CELLS(4) = max(cols, rows) + 2
  // world units behind the target along the view direction, and the fixed 30 degree tilt (tm[5] = sin30*ds, tm[9] = cos30*ds)
  // puts it tan30 x that above the target; the optical axis is horizontal, the target is held at the frame centre.
  // 'player' = the game: target = the walker's feet (game_render_bridge.cpp:1637 cam_pos = local_display_pos), yaw =
  // bearing, zoom 1 (authoring_world_zoom 1.0f, line 2044), perspective on. Wheel changes the zoom (default 1).
  // 'eye' = probe only (EV.set({fp:{gameCam:'eye'}}), capture pose "game": "eye"): the same camera moved so its eye stands
  // at the walker, eyeM above the ground, with the zoom chosen to bring the horizon (sin30*ds*focal samples above the
  // centre) into the frame. It shows why the engine cannot stand at street level: depth = height, so above the horizon the
  // farthest surface wins, and sprites draw only 0.75..1.33 x the eye-target distance away (render_sprite_blit.cpp:228).
  function gameCamFor(rect) {
    const f = st.fp, px = S.font.px || 12, cols = Math.max(8, Math.floor(rect[2] / px)), rows = Math.max(6, Math.floor(rect[3] / px)), s = scale();
    const gz = groundZ(f.x, f.y), back = Math.max(cols, rows) + 2, up = back * TAN30;
    if (f.gameCam === 'eye') { const r = f.yaw * Math.PI / 180, fw = [-Math.sin(r), Math.cos(r)], ez = gz + f.eyeM * s * 16;
      return { kind: 'eye', cols, rows, back_m: back / s, up_m: up / s, ground_z: gz,
        cam: { x: f.x + fw[0] * back, y: f.y + fw[1] * back, z: ez - up * 16, yaw: f.yaw, zoom: 0.4 * rows / (cols + 2), persp: 1 } }; }
    return { kind: 'player', cols, rows, back_m: back / s, up_m: up / s, ground_z: gz, cam: { x: f.x, y: f.y, z: gz, yaw: f.yaw, zoom: f.gameZoom, persp: 1 } };
  }
  // the walker drawn as the game draws its player: a sprite instance at the feet, facing the bearing; moved by re-adding.
  // Sprite yaw = the game's movement facing (facing_space.h FacingWorldDirFromWorldVector: atan2(dy,dx)+90); for the
  // walker's forward (-sin yaw, cos yaw) that is yaw + 180, so with the camera at the same yaw the back row shows.
  function playerSprite(p) {
    const o = R.player;
    if (!p) { if (o && o.slot >= 0 && RT.f.pvRemove) RT.f.pvRemove(o.slot); R.player = null; return; }
    if (o && Math.abs(o.x - p.x) < 0.02 && Math.abs(o.y - p.y) < 0.02 && Math.abs(o.z - p.z) < 0.5 && Math.abs(o.yaw - p.yaw) < 0.5) return;
    if (o && o.slot >= 0 && RT.f.pvRemove) RT.f.pvRemove(o.slot);
    R.player = { ...p, slot: RT.f.pvAdd ? RT.f.pvAdd(PLAYER_XP, p.x, p.y, p.z, p.yaw) : -1 };
  }
  function renderGame() {
    if (!gameOn() || typeof gamePresent !== 'function') return;
    const rect = panels().gl, g = gameCamFor(rect), f = st.fp;
    if (g.kind === 'player') playerSprite({ x: f.x, y: f.y, z: g.ground_z, yaw: (f.yaw + 180) % 360 }); else playerSprite(null);
    const r = gamePresent(rect, g.cam);
    st.game = { ...g, rect: r && r.rect, drawn: !!r, player_slot: R.player ? R.player.slot : null };
    const el = $('evGameV'); if (el) el.textContent = `· game camera ${g.back_m.toFixed(0)} m behind, ${g.up_m.toFixed(0)} m up${g.kind === 'eye' ? ' (eye probe)' : ''} · zoom ${g.cam.zoom.toFixed(2)}`;
  }
  function setGame(on, cam) { st.fp.game = !!on; if (cam) st.fp.gameCam = cam; relayout(); return state(); }
  // ---------------------------------------------------------------------------------------------------------
  // Reference renderer (2D): GSI aerial mosaic and the OSM street map, north up, same view as the GL panel
  // ---------------------------------------------------------------------------------------------------------
  const ROADW = { motorway: 14, trunk: 12, primary: 11, secondary: 10, tertiary: 8, unclassified: 6, residential: 6, living_street: 5, service: 4, pedestrian: 4,
    track: 3, footway: 1.6, path: 1.4, cycleway: 1.6, steps: 1.6, corridor: 1.4, platform: 3 };
  const ROADC = { motorway: '#e892a2', trunk: '#f9b29c', primary: '#fcd6a4', secondary: '#f7fabf' };
  function drawRef(ctx, w, h, layers) {
    const d = devicePixelRatio || 1, t = st.top, [E0, N0] = toEN(t.x, t.y), k = 1 / t.mpp;
    const U = E => w / 2 + (E - E0) * k, V = N => h / 2 - (N - N0) * k;
    ctx.setTransform(d, 0, 0, d, 0, 0); ctx.clearRect(0, 0, w, h);
    if (layers.bg) { ctx.fillStyle = layers.bg; ctx.fillRect(0, 0, w, h); }
    const a = st.meta.aerial;
    if (layers.sat > 0 && a && st.aerial && st.aerial.complete && st.aerial.naturalWidth) {
      // image px -> screen: [E',N'] = Ainv([X,Y] - t'), t' = A [E0,N0] + t (local origin keeps the canvas matrix small)
      const [a1, a2, a3] = a.x, [b1, b2, b3] = a.y, det = a1 * b2 - a2 * b1, i11 = b2 / det, i12 = -a2 / det, i21 = -b1 / det, i22 = a1 / det;
      const tx = a1 * E0 + a2 * N0 + a3, ty = b1 * E0 + b2 * N0 + b3;
      const m11 = k * i11, m12 = k * i12, m21 = -k * i21, m22 = -k * i22;
      ctx.save(); ctx.globalAlpha = layers.sat; ctx.imageSmoothingEnabled = t.mpp > 0.2; ctx.imageSmoothingQuality = 'high';
      ctx.setTransform(d * m11, d * m21, d * m12, d * m22, d * (w / 2 - m11 * tx - m12 * ty), d * (h / 2 - m21 * tx - m22 * ty));
      ctx.drawImage(st.aerial, 0, 0); ctx.restore();
    }
    if (layers.map > 0 && st.osm) drawOsm(ctx, w, h, U, V, k, E0, N0, layers.map, !!layers.mapBg);
  }
  function drawOsm(ctx, w, h, U, V, k, E0, N0, alpha, bg) {
    const hw = w / 2 / k, hh = h / 2 / k, vis = f => f.bb[2] > E0 - hw && f.bb[0] < E0 + hw && f.bb[3] > N0 - hh && f.bb[1] < N0 + hh;
    ctx.save(); ctx.globalAlpha = alpha;
    if (bg) { ctx.fillStyle = '#f2efe9'; ctx.fillRect(0, 0, w, h); }
    const path = f => { ctx.beginPath(); f.p.forEach((q, i) => i ? ctx.lineTo(U(q[0]), V(q[1])) : ctx.moveTo(U(q[0]), V(q[1]))); };
    const areaFill = { green: '#c8facc', water: '#aad3df', parking: '#eeeeee', landuse: '#e6e1da', platform: '#bbbbbb', building: '#d9d0c9', road: '#ffffff', waterway: '#aad3df' };
    const W = st.osm.ways.filter(vis);
    for (const f of W) if (f.area && f.k !== 'building') { path(f); ctx.closePath(); ctx.fillStyle = areaFill[f.k] || '#e6e1da'; ctx.fill(); }
    for (const f of W) if (f.k === 'waterway' && !f.area) { path(f); ctx.strokeStyle = '#aad3df'; ctx.lineWidth = Math.max(1.5, 4 * k); ctx.stroke(); }
    const roads = W.filter(f => f.k === 'road' && !f.area);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (const pass of [0, 1]) for (const f of roads) { const wm = ROADW[f.c] || 4, minor = wm < 2;
      if (minor) { if (pass) { path(f); ctx.setLineDash(f.c === 'steps' ? [2, 2] : [4, 3]); ctx.strokeStyle = f.c === 'steps' ? '#fa8072' : '#fa8072'; ctx.lineWidth = Math.max(1, wm * k * 0.6); ctx.stroke(); ctx.setLineDash([]); } continue; }
      path(f); ctx.strokeStyle = pass ? (ROADC[f.c] || '#ffffff') : '#b8b0a6'; ctx.lineWidth = Math.max(pass ? 1.5 : 2.5, wm * k + (pass ? 0 : 2)); ctx.stroke(); }
    for (const f of W) if (f.k === 'building') { path(f); ctx.closePath(); ctx.fillStyle = '#d9d0c9'; ctx.fill(); ctx.strokeStyle = '#b9a99b'; ctx.lineWidth = 1; ctx.stroke(); }
    for (const f of W) if (f.k === 'rail') { path(f); ctx.strokeStyle = '#707070'; ctx.lineWidth = Math.max(2, 1.6 * k); ctx.stroke(); ctx.setLineDash([Math.max(3, 4 * k), Math.max(3, 4 * k)]); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = Math.max(1, 0.8 * k); ctx.stroke(); ctx.setLineDash([]); }
    // labels: road names along their longest visible segment, named points
    if (k > 1.2) {
      ctx.font = `${Math.round(Math.min(14, 9 + k))}px ui-sans-serif, -apple-system, "Hiragino Sans", sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const done = new Set();
      for (const f of roads) { if (!f.n || done.has(f.n)) continue; let best = null, bl = 0;
        for (let i = 1; i < f.p.length; i++) { const x0 = U(f.p[i - 1][0]), y0 = V(f.p[i - 1][1]), x1 = U(f.p[i][0]), y1 = V(f.p[i][1]), l = Math.hypot(x1 - x0, y1 - y0);
          if (l > bl && (x0 + x1) / 2 > 0 && (x0 + x1) / 2 < w && (y0 + y1) / 2 > 0 && (y0 + y1) / 2 < h) { bl = l; best = [x0, y0, x1, y1]; } }
        if (!best || bl < ctx.measureText(f.n).width + 10) continue; done.add(f.n);
        let ang = Math.atan2(best[3] - best[1], best[2] - best[0]); if (ang > Math.PI / 2) ang -= Math.PI; if (ang < -Math.PI / 2) ang += Math.PI;
        ctx.save(); ctx.translate((best[0] + best[2]) / 2, (best[1] + best[3]) / 2); ctx.rotate(ang); ctx.lineWidth = 3; ctx.strokeStyle = '#ffffffd0'; ctx.strokeText(f.n, 0, 0); ctx.fillStyle = '#333'; ctx.fillText(f.n, 0, 0); ctx.restore(); }
      for (const p of st.osm.pois) { const x = U(p.p[0]), y = V(p.p[1]); if (x < 0 || y < 0 || x > w || y > h) continue;
        ctx.fillStyle = p.k === 'station' || p.k === 'stop' || p.k === 'halt' ? '#7b2fbf' : '#c05a00'; ctx.beginPath(); ctx.arc(x, y, 3, 0, 7); ctx.fill();
        ctx.textAlign = 'left'; ctx.lineWidth = 3; ctx.strokeStyle = '#ffffffd0'; ctx.strokeText(p.n, x + 5, y); ctx.fillStyle = '#222'; ctx.fillText(p.n, x + 5, y); ctx.textAlign = 'center'; }
    }
    ctx.restore();
  }
  function renderRef() {
    const c = $('evref'); if (c.style.display === 'none' || !st.meta) return;
    const ctx = c.getContext('2d'), w = c.clientWidth, h = c.clientHeight, ov = st.layout === 'overlay';
    const sat = ov || st.ref !== 'map' ? st.op.sat : 0, map = ov || st.ref !== 'sat' ? st.op.map : 0;
    drawRef(ctx, w, h, { sat, map, bg: ov ? null : '#000', mapBg: !ov && st.ref === 'map' });
  }
  function renderHud() {
    const c = $('evhud'); if (c.style.display === 'none') return;
    const d = devicePixelRatio || 1, x = c.getContext('2d'), w = c.clientWidth, h = c.clientHeight; x.setTransform(d, 0, 0, d, 0, 0); x.clearRect(0, 0, w, h);
    x.font = 'bold 11px ui-monospace,Menlo,monospace'; x.textBaseline = 'top';
    const tag = (s, px, py) => { const tw = x.measureText(s).width; x.fillStyle = '#000b'; x.fillRect(px - 3, py - 2, tw + 6, 15); x.fillStyle = '#ffd166'; x.fillText(s, px, py); };
    if (st.mode === 'fp' && gameOn()) { const G = st.game;
      tag(`GAME VIEW · Classic renderer, the game's camera${G && G.kind === 'eye' ? ' (EYE PROBE: eye at ' + st.fp.eyeM.toFixed(1) + ' m; engine depth = height)' : ': you at the centre, eye ' + (G ? G.back_m.toFixed(0) : '?') + ' m behind, ' + (G ? G.up_m.toFixed(0) : '?') + ' m up'} · heading ${RT.HEADING_OF_YAW(st.fp.yaw).toFixed(0)}° · X mesh view`, 8, h - 22); return; }
    if (st.mode === 'fp') { const g = panels().gl, gx = g[0] + g[2] / 2, gy = g[1] + g[3] / 2;
      x.strokeStyle = '#fff8'; x.lineWidth = 1; x.beginPath(); x.moveTo(gx - 8, gy); x.lineTo(gx + 8, gy); x.moveTo(gx, gy - 8); x.lineTo(gx, gy + 8); x.stroke();
      tag(`GEOMETRY PREVIEW · NOT GAMEPLAY · eye ${st.fp.eyeM.toFixed(1)} m · heading ${RT.HEADING_OF_YAW(st.fp.yaw).toFixed(0)}° · pitch ${st.fp.pitch.toFixed(0)}°`, w - 570, h - 22); return; }
    const p = panels(), att = st.meta && st.meta.aerial ? st.meta.aerial.source : '';
    const cross = r => { const cx = r[0] + r[2] / 2, cy = r[1] + r[3] / 2; x.strokeStyle = '#ff3b3bcc'; x.lineWidth = 1.5; x.beginPath(); x.moveTo(cx - 10, cy); x.lineTo(cx + 10, cy); x.moveTo(cx, cy - 10); x.lineTo(cx, cy + 10); x.stroke(); };
    const bar = r => { const nice = [1, 2, 5, 10, 20, 50, 100, 200, 500], m = nice.find(v => v / st.top.mpp > 80) || 500, L = m / st.top.mpp, bx = r[0] + r[2] - L - 16, by = r[1] + r[3] - 18;
      x.fillStyle = '#000b'; x.fillRect(bx - 4, by - 14, L + 8, 22); x.fillStyle = '#fff'; x.fillRect(bx, by, L, 3); x.fillText(m + ' m', bx, by - 12); };
    const refName = st.layout === 'overlay' ? `overlay · a3d ${pct(st.op.a3d)} · satellite ${pct(st.op.sat)} · map ${pct(st.op.map)}` : { sat: 'GSI aerial (satellite)', map: 'OSM street map (normal map)', both: 'GSI aerial + OSM map' }[st.ref];
    tag(`A3D TOP-DOWN · ${st.top.mpp.toFixed(3)} m/px · north up`, p.gl[0] + w - 330, p.gl[1] + 6); cross(p.gl); bar(p.gl);
    if (p.ref && st.layout === 'split') { tag(refName.toUpperCase(), p.ref[0] + w - 330, p.ref[1] + 6); cross(p.ref); bar(p.ref); x.fillStyle = '#ffd166'; x.fillRect(0, p.gl[3], w, 2); }
    else if (st.layout === 'overlay') tag(refName, p.gl[0] + w - 420, p.gl[1] + 24);
    if (p.ref) { x.font = '10px ui-monospace'; const s = (st.ref !== 'map' || st.layout === 'overlay' ? att + ' · ' : '') + (st.ref !== 'sat' || st.layout === 'overlay' ? '© OpenStreetMap contributors (local AOI extract)' : '');
      const tw = x.measureText(s).width; x.fillStyle = '#000a'; x.fillRect(p.ref[0] + 4, p.ref[1] + p.ref[3] - 16, tw + 8, 14); x.fillStyle = '#ddd'; x.fillText(s, p.ref[0] + 8, p.ref[1] + p.ref[3] - 15); }
  }
  const pct = v => Math.round(v * 100) + '%';
  // ---------------------------------------------------------------------------------------------------------
  // Modes, S.cam synchronisation, input
  // ---------------------------------------------------------------------------------------------------------
  function eyeZ() { return zAt(st.fp.x, st.fp.y) / 16 + st.fp.eyeM * scale(); }
  // The normal view's camera target -> a standing point: the target itself, stepped back along the view
  // direction while it is inside a building (top surface > 1 m above the terrain), then 2 m further.
  function eyeFromCam(c) {
    const r = c.yaw * Math.PI / 180, f = [-Math.sin(r), Math.cos(r)], s = scale(); let x = c.x, y = c.y, inside = false;
    for (let k = 0; k < 80; k++) { const g = RT.ground(x, y); if (!g || g.top_z - g.terrain_z < 1 * s * 16) break; inside = true; x -= f[0] * s; y -= f[1] * s; }
    if (inside) { x -= f[0] * 2 * s; y -= f[1] * 2 * s; }
    return { x, y, yaw: c.yaw };
  }
  function writeCam() { const c = S.cam;
    if (st.mode === 'fp') { c.x = st.fp.x; c.y = st.fp.y; c.yaw = st.fp.yaw; } else { c.x = st.top.x; c.y = st.top.y; }
    c.z = zAt(c.x, c.y); st.written = { x: c.x, y: c.y, yaw: c.yaw }; camMoved(); }
  function adoptCam() { // S.cam was changed by someone else (goto, bookmark, Street View sync, a test): follow it
    const c = S.cam, w = st.written; if (w && Math.abs(c.x - w.x) < 1e-6 && Math.abs(c.y - w.y) < 1e-6 && Math.abs(c.yaw - w.yaw) < 1e-6) return false;
    if (st.mode === 'fp') { Object.assign(st.fp, eyeFromCam(c)); st.fp.pitch = 0; } else { st.top.x = c.x; st.top.y = c.y; }
    st.written = { x: c.x, y: c.y, yaw: c.yaw }; st.dirty = true; return true;
  }
  async function setMode(m) {
    if (m === st.mode) return; if (m !== 'off' && !S.ready) { status('the map is still loading'); return; }
    const prev = st.mode; st.err = null;
    if (prev === 'fp' && document.pointerLockElement) document.exitPointerLock();
    if (m === 'off') { const c = S.cam; c.z = zAt(c.x, c.y); st.mode = 'off'; relayout(); S.dirty = true; status('back to the Classic view'); return; }
    st.mode = m; st.written = null;
    if (m === 'top') { if (prev !== 'fp') { st.top.x = S.cam.x; st.top.y = S.cam.y; } else { st.top.x = st.fp.x; st.top.y = st.fp.y; } st.written = { x: S.cam.x, y: S.cam.y, yaw: S.cam.yaw }; ensureRef(); }
    if (m === 'fp') { Object.assign(st.fp, prev === 'top' ? { x: st.top.x, y: st.top.y, yaw: S.cam.yaw } : eyeFromCam(S.cam)); st.fp.pitch = 0; }
    relayout();
    try { await ensureMeta(); await ensureGeo(); } catch (e) { clog('error', 'ev ' + e.message); }
    if (st.mode === m) { writeCam(); relayout(); }
  }
  function frame(dt) { // called by a3d.html loop() instead of step()+present() while a mode is on
    adoptCam();
    const k = S.keys, s = scale();
    if (st.mode === 'fp') { const f = st.fp, run = st.shift ? 3 : 1, v = 4 * s * run * dt, r = f.yaw * Math.PI / 180, fw = [-Math.sin(r), Math.cos(r)], rt = [Math.cos(r), Math.sin(r)];
      let m = false; const go = (a, sg) => { f.x += a[0] * v * sg; f.y += a[1] * v * sg; m = true; };
      if (k.has('w')) go(fw, 1); if (k.has('s')) go(fw, -1); if (k.has('d')) go(rt, 1); if (k.has('a')) go(rt, -1);
      if (k.has('q') || st.keys.has('arrowleft')) { f.yaw = (f.yaw + 90 * dt + 360) % 360; m = true; } if (k.has('e') || st.keys.has('arrowright')) { f.yaw = (f.yaw - 90 * dt + 360) % 360; m = true; }
      if (st.keys.has('arrowup')) { f.pitch = Math.min(80, f.pitch + 60 * dt); m = true; } if (st.keys.has('arrowdown')) { f.pitch = Math.max(-80, f.pitch - 60 * dt); m = true; }
      if (m) { st.dirty = true; writeCam(); } }
    else if (st.mode === 'top') { const t = st.top, v = 450 * t.mpp * s * dt * (st.shift ? 3 : 1); let m = false;
      if (k.has('w') || st.keys.has('arrowup')) { t.y += v; m = true; } if (k.has('s') || st.keys.has('arrowdown')) { t.y -= v; m = true; }
      if (k.has('d') || st.keys.has('arrowright')) { t.x += v; m = true; } if (k.has('a') || st.keys.has('arrowleft')) { t.x -= v; m = true; }
      if (m) { st.dirty = true; writeCam(); } }
    if (gameOn()) { st.gameIdle += dt; if (st.gameIdle > 0.25) st.dirty = true; }  // the Classic view's idle refresh (water, trains, shadow bake)
    if (st.dirty) { st.dirty = false; st.gameIdle = 0; render(); }
  }
  function render() {
    try { renderGame(); renderGL(); renderRef(); renderHud(); if (window.SV && st.mode === 'fp') SV.render(); } catch (e) { st.err = e.message; syncCtl(); clog('error', 'ev render ' + e.message); }
    if (st.mode === 'fp' && gameOn()) hud(`CLASSIC TRAILING CAMERA · NOT FIRST PERSON · you at ${st.fp.x.toFixed(1)}, ${st.fp.y.toFixed(1)} · heading ${RT.HEADING_OF_YAW(st.fp.yaw).toFixed(0)}° · game camera ${st.game ? st.game.back_m.toFixed(0) + ' m behind, ' + st.game.up_m.toFixed(0) + ' m up, zoom ' + st.game.cam.zoom.toFixed(2) : '…'} · ${S.cols}×${S.rows} cells`);
    else if (st.mode === 'fp') hud(`EYE-LEVEL GEOMETRY PREVIEW · NOT GAMEPLAY · eye ${st.fp.x.toFixed(1)}, ${st.fp.y.toFixed(1)} at ${st.fp.eyeM.toFixed(1)} m · heading ${RT.HEADING_OF_YAW(st.fp.yaw).toFixed(0)}° · pitch ${st.fp.pitch.toFixed(0)}° · ${R.gl ? (R.lastChunks || 0) + ' terrain chunks' : 'loading geometry'}`);
    else if (st.mode === 'top' && st.meta) { const [E, N] = toEN(st.top.x, st.top.y); hud(`top-down · centre E ${E.toFixed(2)} N ${N.toFixed(2)} · ${st.top.mpp.toFixed(3)} m/px · layout ${st.layout} · reference ${st.ref}`); }
  }
  function zoomAt(f, u, v, r) { // keep the ground point under panel px (u, v) of panel r fixed
    const t = st.top, s = scale(), wpp = t.mpp * s, wx = t.x + (u - r[2] / 2) * wpp, wy = t.y - (v - r[3] / 2) * wpp;
    t.mpp = Math.max(0.02, Math.min(8, t.mpp * f)); const w2 = t.mpp * s; t.x = wx - (u - r[2] / 2) * w2; t.y = wy + (v - r[3] / 2) * w2; st.dirty = true; writeCam();
  }
  let drag = null;
  function panelOf(c) { const p = panels(); return c.id === 'evref' ? p.ref : p.gl; }
  function bindPointer(c) {
    c.addEventListener('contextmenu', e => e.preventDefault());
    c.addEventListener('mousedown', e => { e.preventDefault(); drag = { x: e.clientX, y: e.clientY, t: { ...st.top }, f: { ...st.fp }, moved: false }; });
    c.addEventListener('wheel', e => { e.preventDefault(); if (gameOn()) { st.fp.gameZoom = Math.max(0.5, Math.min(3, st.fp.gameZoom * Math.exp(-e.deltaY * 0.0015))); st.dirty = true; return; } if (st.mode !== 'top') return; const r = c.getBoundingClientRect(); zoomAt(Math.exp(e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top, panelOf(c)); }, { passive: false });
    c.addEventListener('dblclick', e => { if (st.mode !== 'top') return; const r = c.getBoundingClientRect(), p = panelOf(c), wpp = st.top.mpp * scale();
      st.top.x += (e.clientX - r.left - p[2] / 2) * wpp; st.top.y -= (e.clientY - r.top - p[3] / 2) * wpp; st.dirty = true; writeCam(); });
    c.addEventListener('click', () => { if (st.mode === 'fp' && drag === null && !clickMoved && c.requestPointerLock) { try { c.requestPointerLock(); } catch (e) { } } });
  }
  let clickMoved = false;
  addEventListener('mousemove', e => {
    if (st.mode === 'fp' && document.pointerLockElement === $('evgl')) { look(e.movementX, e.movementY); return; }
    if (!drag || st.mode === 'off') return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.hypot(dx, dy) > 3) drag.moved = true; if (!drag.moved) return;
    if (st.mode === 'top') { const wpp = st.top.mpp * scale(); st.top.x = drag.t.x - dx * wpp; st.top.y = drag.t.y + dy * wpp; st.dirty = true; writeCam(); }
    else if (st.mode === 'fp') { st.fp.yaw = drag.f.yaw; st.fp.pitch = drag.f.pitch; look(dx, dy); }
  });
  addEventListener('mouseup', () => { clickMoved = !!(drag && drag.moved); drag = null; setTimeout(() => { clickMoved = false; }, 0); });
  // mouse right = turn right = compass heading up = classic yaw down (yaw = -heading, RT.YAW_OF_HEADING)
  function look(dx, dy) { const f = st.fp; f.yaw = ((f.yaw - dx * 0.25) % 360 + 360) % 360; f.pitch = Math.max(-80, Math.min(80, f.pitch - dy * 0.25)); st.dirty = true; writeCam(); }
  addEventListener('keydown', e => {
    st.shift = e.shiftKey;
    if ($('dlg') && $('dlg').style.display === 'block') return; if (e.target.tagName === 'INPUT' && e.target.type !== 'range' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
    if (e.metaKey || e.ctrlKey) return; const k = e.key.toLowerCase();
    if (k === 'p') { setMode(st.mode === 'fp' ? 'off' : 'fp'); return; } if (k === 'o') { setMode(st.mode === 'top' ? 'off' : 'top'); return; }
    if (st.mode === 'off') return;
    if (st.mode === 'fp' && k === 'x') { setGame(!st.fp.game); return; }
    if (k.startsWith('arrow')) { st.keys.add(k); e.preventDefault(); return; }
    if (st.mode === 'top') {
      if (k === 'k') { st.layout = { single: 'split', split: 'overlay', overlay: 'single' }[st.layout]; ensureRef(); relayout(); }
      else if (k === 'j') { st.ref = { sat: 'map', map: 'both', both: 'sat' }[st.ref]; ensureRef(); relayout(); }
      else if (k === '=' || k === '+') { const p = panels().gl; zoomAt(1 / 1.25, p[2] / 2, p[3] / 2, p); } else if (k === '-') { const p = panels().gl; zoomAt(1.25, p[2] / 2, p[3] / 2, p); }
    }
  });
  addEventListener('keyup', e => { st.shift = e.shiftKey; st.keys.delete(e.key.toLowerCase()); });
  addEventListener('blur', () => st.keys.clear());
  addEventListener('resize', () => { if (st.mode !== 'off') relayout(); });
  injectDom(); syncCtl();
  // test / automation surface (audit scripts): set({mode, layout, ref, op:{a3d,sat,map}, top:{x,y,mpp}, fp:{x,y,yaw,pitch,
  // game (true = game view), gameCam ('player' | 'eye' probe), gameZoom}}); state().game = the last game-view frame
  async function set(o) {
    if (o.layout) st.layout = o.layout; if (o.ref) st.ref = o.ref; if (o.op) Object.assign(st.op, o.op);
    if (o.mode && o.mode !== st.mode) await setMode(o.mode);
    if (o.top) Object.assign(st.top, o.top); if (o.fp) Object.assign(st.fp, o.fp);
    if (st.mode !== 'off') { ensureRef(); if (st.osmPromise) await st.osmPromise; if (st.aerial && !st.aerial.complete) await new Promise(r => { st.aerial.addEventListener('load', r, { once: true }); st.aerial.addEventListener('error', r, { once: true }); }); writeCam(); relayout(); render(); }
    return state();
  }
  function state() { const en = st.meta && st.mode === 'top' ? toEN(st.top.x, st.top.y) : null;
    return { mode: st.mode, layout: st.layout, ref: st.ref, op: { ...st.op }, top: { ...st.top, E: en && en[0], N: en && en[1] }, fp: { ...st.fp, eye_z: st.mode === 'fp' ? eyeZ() : null },
      game: gameOn() && st.game ? { ...st.game, cam: { ...st.game.cam } } : null,
      geometry: st.geo ? { mb: st.geo.mb, load_ms: st.geo.ms, instances: st.geo.head.instances, vertices: st.geo.sec.pos.shape[0], triangles: st.geo.sec.idx.shape[0] / 3 } : null, err: st.err, chunks: R.lastChunks || 0 }; }
  // ground rectangle (UTM E/N) each top-down panel shows: [E_west, N_south, E_east, N_north]
  function extents() { const p = panels(), t = st.top, [E, N] = st.meta ? toEN(t.x, t.y) : [NaN, NaN], ext = r => r && [E - r[2] / 2 * t.mpp, N - r[3] / 2 * t.mpp, E + r[2] / 2 * t.mpp, N + r[3] / 2 * t.mpp];
    return { panels: p, gl: ext(p.gl), ref: ext(p.ref) }; }
  // street_view_overlay.js surface: the first-person camera it must match (fpCam), a way to stand it (setFp) and redraw
  function fpCam() { const c = $('evgl'); return { ...st.fp, eyeZ: st.mode === 'fp' ? eyeZ() : null, w: c.clientWidth || c.width, h: c.clientHeight || c.height, gameView: gameOn() ? st.game : null }; }
  function setFp(o) { Object.assign(st.fp, o); if (st.mode === 'fp') { st.dirty = true; writeCam(); } syncCtl(); }
  return { active: () => st.mode !== 'off', frame, setMode, set, state, extents, toEN: (x, y) => toEN(x, y), toXY: (E, N) => toXY(E, N), _st: st,
    fpCam, setFp, setGame, gameOn, redraw: () => { st.dirty = true; }, relayout: () => { if (st.mode !== 'off') relayout(); }, ensureMeta, scale };
})();
