// FL-4876 FACADE-CAPTURE-01 — facade capture for the Numabe A3D editor (a3d.html).
// Operator 2026-10-13: "I want to screenshot facade for lawson, paste it on the currently grey/missing lawson
// face, it should let me convert to xp sprite billboard or whatever is cheapest for runtime ... right then and
// there". Flow: select a building face (click the wall; ◀ ▶ cycle) → ⌘V a photo (or open a queued image) →
// drag the 4 corners onto the face's corners → mark bands (sign / awning → CP437 XP sprite; wall / trim →
// colour into the appearance owner; glass / door → recorded layout; object → fixture request) → every change is
// converted and injected into the running Classic wasm (ClassicPreviewSpriteAdd) at the position the build
// will use (map_editor.py /api/facade_preview → compile_sign_sprites.capture_anchor) → Accept writes the owners.
// Shares the a3d.html global scope (S, RT, api, status, goPose, selectThing, renderInsp, loadNotes, ...).
'use strict';
const FC = (() => {
  const PPM = 60;                                                   // rectified pixels per metre
  const COLS_PER_WU = 1.5, Z_PER_ROW = 16 / (Math.cos(Math.PI / 6) * 4 * 0.375); // compile_sign_sprites density
  const LEVELS = [0, 51, 102, 153, 204, 255];
  const KINDS = {
    sign: {mode: 'sprite', col: '#ffd166'}, storefront: {mode: 'sprite', col: '#4dabf7'}, awning: {mode: 'sprite', col: '#ff9f43'},
    wall: {mode: 'colour', col: '#f4efe6'}, trim: {mode: 'colour', col: '#c792ea'},
    glass: {mode: 'record', col: '#55d8ef'}, door: {mode: 'record', col: '#7bd88f'},
    object: {mode: 'object', col: '#ff6b6b'}};
  const GLYPH_SETS = {
    blocks: [176, 177, 178, 220, 221, 222, 223],
    half: [223],
    glyph: [...Array(254).keys()].map(i => i + 1).filter(g => g !== 32 && g !== 219)};
  const st = {open: false, face: null, bid: null, img: null, src: null, corners: null, heightM: 3, bands: [], sel: -1,
    kind: 'sign', enc: 'blocks', contrast: 1, preview: [], accepted: {}, visited: new Set(), queueItem: null,
    rect: null, rectW: 0, rectH: 0, masks: null, serial: 0, srcPixels: null, replace: new Set(), acceptedFronts: {}};
  const frontsKeptHidden = () => new Set(Object.values(st.acceptedFronts).flat());
  const $f = id => document.getElementById(id);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const q6 = v => LEVELS.reduce((best, l) => Math.abs(l - v) < Math.abs(best - v) ? l : best, 0);

  // ---------------------------------------------------------------- glyph masks (cp437_8x8, native luminance alpha)
  async function masks() {
    if (st.masks) return st.masks;
    const img = new Image();
    await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error('font cp437_8x8')); img.src = '/fonts/cp437_8x8.png'; });
    const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height;
    const x = cv.getContext('2d'); x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, img.width, img.height).data, gw = img.width / 16, m = new Float32Array(256 * 64);
    for (let g = 0; g < 256; g++) for (let py = 0; py < 8; py++) for (let px = 0; px < 8; px++) {
      const sx = (g & 15) * gw + Math.floor(px * gw / 8), sy = (g >> 4) * gw + Math.floor(py * gw / 8), i = (sy * img.width + sx) * 4;
      m[g * 64 + py * 8 + px] = Math.floor((d[i] * 2 + d[i + 1] * 3 + d[i + 2] + 3) / 6) / 255;
    }
    const stats = {};
    for (let g = 0; g < 256; g++) {
      let s1 = 0, s2 = 0;
      for (let k = 0; k < 64; k++) { const a = m[g * 64 + k]; s1 += a; s2 += a * a; }
      const S00 = 64 - 2 * s1 + s2, S01 = s1 - s2, S11 = s2;
      stats[g] = {s1, s2, S00, S01, S11, det: S00 * S11 - S01 * S01};
    }
    st.masks = {m, stats};
    return st.masks;
  }

  // ---------------------------------------------------------------- homography (rect px -> source px)
  function solve(A, b) {
    const n = b.length, M = A.map((r, i) => [...r, b[i]]);
    for (let c = 0; c < n; c++) {
      let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
      [M[c], M[p]] = [M[p], M[c]];
      for (let r = 0; r < n; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
    }
    return M.map((r, i) => r[n] / r[i]);
  }
  function homography(dst, src) {
    const A = [], b = [];
    for (let i = 0; i < 4; i++) {
      const [u, v] = dst[i], [x, y] = src[i];
      A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]); b.push(x);
      A.push([0, 0, 0, u, v, 1, -u * y, -v * y]); b.push(y);
    }
    return [...solve(A, b), 1];
  }
  function rectify() {
    if (!st.img || !st.face) return;
    const W = Math.max(8, Math.round(st.face.length_m * PPM)), H = Math.max(8, Math.round(st.heightM * PPM));
    if (st.imgType === 'logo') { // plain face; the graphic is shown inside each sprite band where it will go
      const cv = document.createElement('canvas'); cv.width = W; cv.height = H; const x = cv.getContext('2d');
      x.fillStyle = '#3a3a40'; x.fillRect(0, 0, W, H);
      for (const b of st.bands) if (b.mode === 'sprite') x.drawImage(st.img, b.x0_m * PPM, (st.heightM - b.y1_m) * PPM, (b.x1_m - b.x0_m) * PPM, (b.y1_m - b.y0_m) * PPM);
      st.rect = x.getImageData(0, 0, W, H); st.rectW = W; st.rectH = H; return;
    }
    const h = homography([[0, 0], [W, 0], [W, H], [0, H]], st.corners), sp = st.srcPixels, sw = st.img.width, sh = st.img.height;
    const out = new ImageData(W, H), o = out.data;
    for (let v = 0; v < H; v++) for (let u = 0; u < W; u++) {
      const uu = u + .5, vv = v + .5, w = h[6] * uu + h[7] * vv + h[8];
      let x = (h[0] * uu + h[1] * vv + h[2]) / w - .5, y = (h[3] * uu + h[4] * vv + h[5]) / w - .5;
      x = clamp(x, 0, sw - 1.001); y = clamp(y, 0, sh - 1.001);
      const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, i00 = (y0 * sw + x0) * 4, i10 = i00 + 4, i01 = i00 + sw * 4, i11 = i01 + 4, j = (v * W + u) * 4;
      for (let c = 0; c < 3; c++) o[j + c] = (sp[i00 + c] * (1 - fx) + sp[i10 + c] * fx) * (1 - fy) + (sp[i01 + c] * (1 - fx) + sp[i11 + c] * fx) * fy;
      o[j + 3] = 255;
    }
    st.rect = out; st.rectW = W; st.rectH = H;
  }
  // bilinear sample of the rectified buffer at metre coordinates (x from the face's left end, y up from street)
  function sampleM(xm, ym, rgb) {
    const W = st.rectW, H = st.rectH, d = st.rect.data;
    const x = clamp(xm * PPM - .5, 0, W - 1.001), y = clamp((st.heightM - ym) * PPM - .5, 0, H - 1.001);
    const x0 = x | 0, y0 = y | 0, fx = x - x0, fy = y - y0, i00 = (y0 * W + x0) * 4, i10 = i00 + 4, i01 = i00 + W * 4, i11 = i01 + 4;
    for (let c = 0; c < 3; c++) rgb[c] = (d[i00 + c] * (1 - fx) + d[i10 + c] * fx) * (1 - fy) + (d[i01 + c] * (1 - fx) + d[i11 + c] * fx) * fy;
    return rgb;
  }

  // ---------------------------------------------------------------- conversion
  function bandGrid(b) {
    const sc = S.metaScale, wm = b.x1_m - b.x0_m, hm = b.y1_m - b.y0_m;
    // 36 rows max: the engine's per-cell height digits are 0-9, A-Z (engine/sprite.cpp layer 1)
    // >= 2 columns: the .xp header cell is layer 0 column 1 (xp_bytes; FACADE-CAPTURE-REVIEW-20261014 P2)
    return [clamp(Math.round(wm * sc * COLS_PER_WU), 2, 128), clamp(Math.round(hm * sc * 16 / Z_PER_ROW), 1, 36)];
  }
  // least-squares CP437 match of one 8x8 pixel block P (64 x rgb, contrast applied) over the glyph set
  function matchCell(P, sum, sq, set) {
    const {m, stats} = st.masks;
    // solid cell (full block, fg = bg = mean) is always a candidate
    const mean = sum.map(v => q6(v / 64));
    let best = {g: 219, fg: mean, bg: mean, e: sq - 2 * (mean[0] * sum[0] + mean[1] * sum[1] + mean[2] * sum[2]) + 64 * (mean[0] ** 2 + mean[1] ** 2 + mean[2] ** 2)};
    for (const g of set) {
      const s = stats[g]; if (Math.abs(s.det) < 1e-6) continue;
      const fg = [0, 0, 0], bg = [0, 0, 0], aP = [0, 0, 0];
      for (let i = 0; i < 64; i++) { const a = m[g * 64 + i]; if (a) { aP[0] += a * P[i * 3]; aP[1] += a * P[i * 3 + 1]; aP[2] += a * P[i * 3 + 2]; } }
      let e = sq;
      for (let q = 0; q < 3; q++) {
        const r1 = aP[q], r0 = sum[q] - r1;
        bg[q] = q6(clamp((s.S11 * r0 - s.S01 * r1) / s.det, 0, 255)); fg[q] = q6(clamp((s.S00 * r1 - s.S01 * r0) / s.det, 0, 255));
        const d = fg[q] - bg[q];
        e += -2 * bg[q] * sum[q] - 2 * d * aP[q] + 64 * bg[q] * bg[q] + 2 * bg[q] * d * s.s1 + d * d * s.s2;
      }
      if (e < best.e) best = {g, fg, bg, e};
    }
    return [best.g, best.fg, best.bg];
  }
  function convertSprite(b) {
    if (st.imgType === 'logo') return convertLogo(b);
    const [cols, rows] = bandGrid(b), set = GLYPH_SETS[st.enc] || GLYPH_SETS.blocks;
    const cw = (b.x1_m - b.x0_m) / cols, ch = (b.y1_m - b.y0_m) / rows, P = new Float32Array(64 * 3), rgb = [0, 0, 0], k = st.contrast;
    const cells = [];
    for (let r = 0; r < rows; r++) {
      const row = [];
      for (let c = 0; c < cols; c++) {
        let sum = [0, 0, 0], sq = 0;
        for (let py = 0; py < 8; py++) for (let px = 0; px < 8; px++) {
          sampleM(b.x0_m + (c + (px + .5) / 8) * cw, b.y1_m - (r + (py + .5) / 8) * ch, rgb);
          for (let q = 0; q < 3; q++) { const v = clamp((rgb[q] - 128) * k + 128, 0, 255); P[(py * 8 + px) * 3 + q] = v; sum[q] += v; sq += v * v; }
        }
        row.push(matchCell(P, sum, sq, set));
      }
      cells.push(row);
    }
    return cells;
  }
  // Logo / sign graphic (Google Images, PNG with transparency): the WHOLE image fills the band box, no corners.
  // Per cell: mostly transparent -> null (no cell drawn); half covered -> a half block whose other half is
  // transparent (fg = colour, bg = null -> XP magenta key, the engine's per-component transparency); otherwise
  // the normal least-squares match on the opaque pixels.
  function convertLogo(b) {
    const [cols, rows] = bandGrid(b), set = GLYPH_SETS[st.enc] || GLYPH_SETS.blocks, k = st.contrast;
    const W = st.img.width, H = st.img.height, sp = st.srcPixels, P = new Float32Array(64 * 3), A = new Float32Array(64);
    const cells = [];
    for (let r = 0; r < rows; r++) {
      const row = [];
      for (let c = 0; c < cols; c++) {
        let aSum = 0, oc = [0, 0, 0], on = 0;
        for (let py = 0; py < 8; py++) for (let px = 0; px < 8; px++) {
          const x = clamp(Math.floor((c + (px + .5) / 8) / cols * W), 0, W - 1), y = clamp(Math.floor((r + (py + .5) / 8) / rows * H), 0, H - 1);
          const j = (y * W + x) * 4, i = py * 8 + px, a = sp[j + 3] / 255;
          A[i] = a; aSum += a;
          for (let q = 0; q < 3; q++) P[i * 3 + q] = clamp((sp[j + q] - 128) * k + 128, 0, 255);
          if (a >= .5) { on++; for (let q = 0; q < 3; q++) oc[q] += P[i * 3 + q]; }
        }
        const meanA = aSum / 64;
        if (meanA < .2 || !on) { row.push(null); continue; }
        oc = oc.map(v => v / on);
        if (meanA < .8) {
          const half = (f) => { let s = 0, n = 0; for (let i = 0; i < 64; i++) if (f(i >> 3, i & 7)) { s += A[i]; n++; } return s / n; };
          const cand = [[223, half((y) => y < 4), half((y) => y >= 4)], [220, half((y) => y >= 4), half((y) => y < 4)],
                        [221, half((y, x) => x < 4), half((y, x) => x >= 4)], [222, half((y, x) => x >= 4), half((y, x) => x < 4)]]
            .filter(([, inA, outA]) => inA > .6 && outA < .4).sort((p, q) => (q[1] - q[2]) - (p[1] - p[2]));
          if (cand.length) {
            const g = cand[0][0], sel = {223: (y) => y < 4, 220: (y) => y >= 4, 221: (y, x) => x < 4, 222: (y, x) => x >= 4}[g];
            const col = [0, 0, 0]; let n = 0;
            for (let i = 0; i < 64; i++) if (sel(i >> 3, i & 7) && A[i] >= .5) { n++; for (let q = 0; q < 3; q++) col[q] += P[i * 3 + q]; }
            row.push([g, (n ? col.map(v => v / n) : oc).map(q6), null]); continue;
          }
          if (meanA < .5) { row.push(null); continue; }
        }
        // opaque enough: transparent pixels take the cell's opaque mean so the key colour never leaks in
        let sum = [0, 0, 0], sq = 0;
        for (let i = 0; i < 64; i++) { if (A[i] < .5) for (let q = 0; q < 3; q++) P[i * 3 + q] = oc[q];
          for (let q = 0; q < 3; q++) { sum[q] += P[i * 3 + q]; sq += P[i * 3 + q] ** 2; } }
        row.push(matchCell(P, sum, sq, set));
      }
      cells.push(row);
    }
    return cells;
  }
  function medianColour(b) {
    const ch = [[], [], []], rgb = [0, 0, 0], n = 24;
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      sampleM(b.x0_m + (i + .5) / n * (b.x1_m - b.x0_m), b.y0_m + (j + .5) / n * (b.y1_m - b.y0_m), rgb);
      for (let q = 0; q < 3; q++) ch[q].push(rgb[q]);
    }
    return ch.map(a => { a.sort((x, y) => x - y); return Math.round(a[a.length >> 1]); });
  }
  function convertAll() {
    if (!st.rect) return;
    for (const b of st.bands) {
      b.cells = null; b.rgb = null;
      if (b.mode === 'sprite') b.cells = convertSprite(b);
      else if (b.mode === 'colour' || b.mode === 'record') b.rgb = medianColour(b);
    }
  }

  // ---------------------------------------------------------------- live preview in the running wasm
  function clearPreview() { for (const s of st.preview) RT.f.pvRemove && RT.f.pvRemove(s); st.preview = []; S.dirty = true; }
  function addSprite(b64, pos) {
    const raw = Uint8Array.from(atob(b64), c => c.charCodeAt(0)), path = `/numabe/preview/fc_${++st.serial}.xp`;
    Module.FS.mkdirTree('/numabe/preview'); Module.FS.writeFile(path, raw);
    return RT.f.pvAdd ? RT.f.pvAdd(path, pos[0], pos[1], pos[2], 0) : -1;
  }
  let pvT = null, pvGen = 0;
  function schedule() { clearTimeout(pvT); pvT = setTimeout(update, 200); }
  async function update() {
    rectify(); convertAll(); drawPanel();
    const gen = ++pvGen, sb = st.bands.filter(b => b.mode === 'sprite' && b.cells);
    clearPreview();
    if (!sb.length) { drawPanel(); return; }
    try {
      const r = await api('/api/facade_preview', {building_id: st.bid, face_index: st.face.face_index,
        bands: sb.map(b => ({cells: b.cells, x0_m: b.x0_m, x1_m: b.x1_m, y0_m: b.y0_m, out_m: b.out_m ?? 0.3}))});
      if (gen !== pvGen || !st.open) return;  // closed or re-scheduled while the request was in flight
      r.bands.forEach((rb, i) => {
        sb[i].pos = rb.pos; sb[i].wall = rb.wall_snap;
        if (!rb.pos) return;
        const slot = addSprite(rb.xp, rb.pos); if (slot >= 0) st.preview.push(slot);
      });
    } catch (e) { status('preview failed: ' + e.message); }
    S.dirty = true; drawPanel();
  }

  // ---------------------------------------------------------------- faces
  async function loadFace(bid, opts) {
    const qs = opts.face != null ? `face=${opts.face}` : `x=${opts.x}&y=${opts.y}`;
    st.face = await api(`/api/face?building=${encodeURIComponent(bid)}&${qs}`); st.bid = bid; S.dirty = true;
    return st.face;
  }
  async function onSelect(t) {
    try {
      await loadFace(t.id, t.face != null ? {face: t.face} : {x: t.world[0], y: t.world[1]});
      t.face = st.face.face_index; renderInsp();
    } catch (e) { status('face lookup failed: ' + e.message); }
  }
  async function cycleFace(d) {
    if (!st.face) return;
    await loadFace(st.bid, {face: (st.face.face_index + d + st.face.faces) % st.face.faces});
    const s = S.sel.find(x => x.kind === 'building' && x.id === st.bid); if (s) s.face = st.face.face_index;
    if (st.open) { st.heightM = st.face.height_m || st.heightM; lookAtFace(); schedule(); }
    renderInsp();
  }
  function lookAtFace() {
    const f = st.face, mx = (f.left_world[0] + f.right_world[0]) / 2, my = (f.left_world[1] + f.right_world[1]) / 2;
    // zoom 1: Classic draws sprites at native density whatever the zoom (measured: at zoom 2 the anchors
    // spread 2x but the sprites do not scale), so only zoom 1 shows the sign at its true size on the wall
    S.cam.zoom = 1; goPose({x: mx, y: my, heading_deg: (f.bearing_deg + 180) % 360});
  }
  // world point on the actual (extruded) wall: x metres from the left end, y metres above street level
  function wallPt(xm, ym) {
    const f = st.face, t = xm / f.length_m, off = (f.wall_offset_m || 0) * f.content_scale;
    return [f.left_world[0] + (f.right_world[0] - f.left_world[0]) * t + f.normal[0] * off,
            f.left_world[1] + (f.right_world[1] - f.left_world[1]) * t + f.normal[1] * off,
            (f.street_z ?? zAt(f.left_world[0], f.left_world[1])) + ym * f.z_per_m];
  }
  function drawOverlay(ctx) {
    const f = st.face; if (!f || !(st.open || S.sel.some(s => s.kind === 'building' && s.id === st.bid))) return;
    const poly = (pts, col, w, dash) => {
      const ps = pts.map(p => RT.project(...p)).filter(Boolean).map(cellCss); if (ps.length < pts.length) return null;
      ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash || []); ctx.beginPath();
      ps.forEach((p, i) => i ? ctx.lineTo(...p) : ctx.moveTo(...p)); ctx.closePath(); ctx.stroke(); ctx.setLineDash([]); return ps;
    };
    const H = st.open ? st.heightM : (f.height_m || 3), L = f.length_m;
    const ps = poly([wallPt(0, 0), wallPt(L, 0), wallPt(L, H), wallPt(0, H)], '#ffd166', 2);
    if (ps) { ctx.fillStyle = '#ffd166'; ctx.fillText(`face ${f.face_index + 1}/${f.faces} · ${L.toFixed(2)} m × ${H.toFixed(2)} m · faces ${f.bearing_deg}°`, ps[3][0], ps[3][1] - 6); }
    if (st.open) st.bands.forEach((b, i) => poly([wallPt(b.x0_m, b.y0_m), wallPt(b.x1_m, b.y0_m), wallPt(b.x1_m, b.y1_m), wallPt(b.x0_m, b.y1_m)],
      KINDS[b.kind].col, i === st.sel ? 2 : 1, i === st.sel ? [] : [4, 3]));
  }

  // ---------------------------------------------------------------- panel
  const CSS = `#fc{position:fixed;top:32px;right:0;bottom:22px;width:var(--fcw);background:#15151bf7;border-left:1px solid var(--acc);z-index:7;overflow:auto;padding:8px;display:none}
#fc h3{margin:4px 0;color:var(--acc);font-size:12px}#fc canvas{display:block;width:100%;image-rendering:pixelated;border:1px solid #444;margin:4px 0;cursor:crosshair}
#fc .row{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin:3px 0}#fc .kb.on{background:#5a4a12;border-color:var(--acc)}
#fc .band{border:1px solid #333;padding:3px;margin:2px 0;display:flex;gap:4px;align-items:center;flex-wrap:wrap}#fc .band.on{border-color:var(--acc)}
#fc input[type=text],#fc input[type=number],#fc textarea{background:#0b0b0e;color:var(--ink);border:1px solid #444;font:inherit;padding:2px}
#fc textarea{width:100%;height:46px}#fc .warn{color:var(--bad)}`;
  function build() {
    if ($f('fc')) return;
    const style = document.createElement('style'); style.textContent = CSS; document.head.appendChild(style);
    const d = document.createElement('div'); d.id = 'fc';
    d.innerHTML = `<h3 id="fcTitle">Facade capture</h3>
<div class="row"><button id="fcPrev" title="previous face">◀ face</button><button id="fcNext" title="next face">face ▶</button>
 height <input id="fcH" type="number" step="0.05" min="1" max="40" style="width:64px"> m <span class="dim" id="fcFaceInfo"></span></div>
<div class="row">source <select id="fcKind"><option value="google_streetview_reference">Google Street View screenshot (reference only)</option>
 <option value="mapillary_cc_by_sa">Mapillary panorama (CC BY-SA)</option><option value="web_image_reference">Google Images / web graphic (reference only)</option><option value="operator_photo">my own photo</option><option value="other">other</option></select></div>
<div class="row">image is <select id="fcType"><option value="photo">a photo of this facade (drag 4 corners)</option><option value="logo">a logo / sign graphic (whole image into a box, keeps transparency)</option></select></div>
<div class="dim" id="fcStep1">1 · drag the 4 yellow corners onto the face's corners (bottom = street, top = eave/height)</div>
<canvas id="fcSrc"></canvas>
<div class="dim">2 · pick a band kind, then drag a box on the straightened face</div>
<div class="row" id="fcKinds"></div>
<canvas id="fcRect"></canvas>
<div id="fcBands"></div>
<div class="row">sprite glyphs <select id="fcEnc"><option value="blocks">blocks + shades</option><option value="glyph">all CP437 glyphs</option><option value="half">half-blocks (FamilyMart path)</option></select>
 contrast <input id="fcCon" type="range" min="0.6" max="1.8" step="0.05" value="1" style="width:90px"></div>
<div id="fcFronts"></div>
<div id="fcCells"></div>
<div id="fcCost" class="dim"></div>
<textarea id="fcNote" placeholder="note for this capture (optional)"></textarea>
<div class="row"><button id="fcAccept" style="border-color:var(--ok)">Accept (writes owners)</button><button id="fcNoteOnly">Save as note only</button><button id="fcCancel">Cancel (Esc)</button></div>`;
    document.body.appendChild(d);
    $f('fcKinds').innerHTML = Object.keys(KINDS).map(k => `<button class="kb" data-k="${k}" style="border-left:4px solid ${KINDS[k].col}">${k}</button>`).join('');
    $f('fcKinds').querySelectorAll('.kb').forEach(b => b.onclick = () => { st.kind = b.dataset.k; drawPanel(); });
    $f('fcPrev').onclick = () => cycleFace(-1); $f('fcNext').onclick = () => cycleFace(1);
    $f('fcH').onchange = () => { st.heightM = clamp(+$f('fcH').value || 3, 1, 40); st.bands.forEach(b => { b.y1_m = Math.min(b.y1_m, st.heightM); }); schedule(); };
    $f('fcEnc').onchange = () => { st.enc = $f('fcEnc').value; schedule(); };
    $f('fcType').onchange = () => { setImgType($f('fcType').value); schedule(); };
    $f('fcCon').oninput = () => { st.contrast = +$f('fcCon').value; schedule(); };
    $f('fcAccept').onclick = accept; $f('fcCancel').onclick = close;
    $f('fcNoteOnly').onclick = () => { const img = st.src.data_url ? {data_url: st.src.data_url} : null, w = [wallPt(st.face.length_m / 2, 1)], bid = st.bid; close();
      openDlg({kind: 'image_drop', world: w, image: img || {url: '/img?path=' + encodeURIComponent(st.src.path)}, building: bid}, `Image on ${bid.split(':').pop()}`); };
    srcCanvasEvents(); rectCanvasEvents();
    addEventListener('keydown', e => {
      if (!st.open || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || $('dlg').style.display === 'block') return;
      if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && st.sel >= 0) { e.stopImmediatePropagation(); e.preventDefault(); st.bands.splice(st.sel, 1); st.sel = -1; schedule(); }
    }, true);
  }
  function panelWidth() { return Math.min(620, Math.max(360, Math.round(innerWidth * .46))); }
  function dock(on) {
    document.documentElement.style.setProperty('--fcw', panelWidth() + 'px');
    $f('fc').style.display = on ? 'block' : 'none'; $('stage').style.right = on ? panelWidth() + 'px' : '0'; layout();
  }

  // source canvas: image + draggable corners
  let srcScale = 1, dragC = -1;
  function drawSrc() {
    const cv = $f('fcSrc'), img = st.img; if (!img) return;
    const w = cv.clientWidth || panelWidth() - 20; srcScale = w / img.width; cv.width = w; cv.height = Math.round(img.height * srcScale);
    const x = cv.getContext('2d');
    if (st.imgType === 'logo') { // checkerboard shows which parts are transparent
      for (let yy = 0; yy < cv.height; yy += 8) for (let xx = 0; xx < cv.width; xx += 8) { x.fillStyle = ((xx + yy) / 8) % 2 ? '#555' : '#777'; x.fillRect(xx, yy, 8, 8); }
      x.drawImage(img, 0, 0, cv.width, cv.height); return;
    }
    x.drawImage(img, 0, 0, cv.width, cv.height);
    const p = st.corners.map(([a, b]) => [a * srcScale, b * srcScale]);
    x.strokeStyle = '#ffd166'; x.lineWidth = 2; x.beginPath(); p.forEach((q, i) => i ? x.lineTo(...q) : x.moveTo(...q)); x.closePath(); x.stroke();
    ['TL', 'TR', 'BR', 'BL'].forEach((n, i) => { x.fillStyle = '#ffd166'; x.fillRect(p[i][0] - 5, p[i][1] - 5, 10, 10); x.fillStyle = '#000'; x.font = 'bold 9px ui-monospace'; x.fillText(n, p[i][0] - 5, p[i][1] + 3); });
  }
  function srcCanvasEvents() {
    const cv = $f('fcSrc'), at = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) / srcScale, (e.clientY - r.top) / srcScale]; };
    cv.addEventListener('mousedown', e => { if (st.imgType === 'logo') return; const p = at(e); let bd = 14 / srcScale; dragC = -1;
      st.corners.forEach((c, i) => { const d = Math.hypot(c[0] - p[0], c[1] - p[1]); if (d < bd) { bd = d; dragC = i; } }); e.preventDefault(); });
    addEventListener('mousemove', e => { if (dragC < 0) return; const p = at(e); st.corners[dragC] = [clamp(p[0], 0, st.img.width), clamp(p[1], 0, st.img.height)]; drawSrc(); });
    addEventListener('mouseup', () => { if (dragC >= 0) { dragC = -1; schedule(); } });
  }
  // rectified canvas: straightened face + band boxes
  let rScale = 1, rDrag = null;
  function m2c(xm, ym) { return [xm * PPM * rScale, (st.heightM - ym) * PPM * rScale]; }
  function c2m(cx, cy) { return [clamp(cx / (PPM * rScale), 0, st.face.length_m), clamp(st.heightM - cy / (PPM * rScale), 0, st.heightM)]; }
  function drawRect() {
    const cv = $f('fcRect'); if (!st.rect) return;
    const w = cv.clientWidth || panelWidth() - 20; rScale = w / st.rectW; cv.width = w; cv.height = Math.round(st.rectH * rScale);
    const x = cv.getContext('2d'), tmp = document.createElement('canvas'); tmp.width = st.rectW; tmp.height = st.rectH; tmp.getContext('2d').putImageData(st.rect, 0, 0);
    x.drawImage(tmp, 0, 0, cv.width, cv.height);
    // metre grid
    x.strokeStyle = '#ffffff22'; x.lineWidth = 1;
    for (let m = 1; m < st.face.length_m; m++) { const [a] = m2c(m, 0); x.beginPath(); x.moveTo(a, 0); x.lineTo(a, cv.height); x.stroke(); }
    for (let m = 1; m < st.heightM; m++) { const [, b] = m2c(0, m); x.beginPath(); x.moveTo(0, b); x.lineTo(cv.width, b); x.stroke(); }
    st.bands.forEach((b, i) => { const [x0, y0] = m2c(b.x0_m, b.y1_m), [x1, y1] = m2c(b.x1_m, b.y0_m);
      x.strokeStyle = KINDS[b.kind].col; x.lineWidth = i === st.sel ? 3 : 1.5; x.setLineDash(i === st.sel ? [] : [5, 3]); x.strokeRect(x0, y0, x1 - x0, y1 - y0); x.setLineDash([]);
      x.fillStyle = KINDS[b.kind].col; x.font = 'bold 10px ui-monospace'; x.fillText(b.kind + (b.label ? ' ' + b.label : ''), x0 + 3, y0 + 11); });
    if (rDrag && rDrag.box) { const [a, b2] = rDrag.box; x.strokeStyle = KINDS[st.kind].col; x.lineWidth = 1; x.strokeRect(a[0], a[1], b2[0] - a[0], b2[1] - a[1]); }
  }
  function rectCanvasEvents() {
    const cv = $f('fcRect'), at = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const hit = p => { // band edge / inside hit test (canvas px)
      for (let i = st.bands.length - 1; i >= 0; i--) { const b = st.bands[i], [x0, y0] = m2c(b.x0_m, b.y1_m), [x1, y1] = m2c(b.x1_m, b.y0_m), t = 6;
        if (p[0] < x0 - t || p[0] > x1 + t || p[1] < y0 - t || p[1] > y1 + t) continue;
        const e = {l: Math.abs(p[0] - x0) < t, r: Math.abs(p[0] - x1) < t, t: Math.abs(p[1] - y0) < t, b: Math.abs(p[1] - y1) < t};
        return {i, e, inside: !(e.l || e.r || e.t || e.b)}; }
      return null; };
    cv.addEventListener('mousedown', e => { e.preventDefault(); const p = at(e), h = hit(p);
      if (h) { st.sel = h.i; rDrag = {i: h.i, e: h.e, inside: h.inside, p, orig: {...st.bands[h.i]}}; }
      else rDrag = {box: [p, p]}; drawPanel(); });
    addEventListener('mousemove', e => { if (!rDrag) return; const p = at(e);
      if (rDrag.box) { rDrag.box[1] = p; drawRect(); return; }
      const b = st.bands[rDrag.i], o = rDrag.orig, [mx, my] = c2m(...p), [px, py] = c2m(...rDrag.p);
      if (rDrag.inside) { const dx = mx - px, dy = my - py, w = o.x1_m - o.x0_m, h = o.y1_m - o.y0_m;
        b.x0_m = clamp(o.x0_m + dx, 0, st.face.length_m - w); b.x1_m = b.x0_m + w; b.y0_m = clamp(o.y0_m + dy, 0, st.heightM - h); b.y1_m = b.y0_m + h; }
      else { if (rDrag.e.l) b.x0_m = Math.min(mx, b.x1_m - .1); if (rDrag.e.r) b.x1_m = Math.max(mx, b.x0_m + .1);
        if (rDrag.e.t) b.y1_m = Math.max(my, b.y0_m + .1); if (rDrag.e.b) b.y0_m = Math.min(my, b.y1_m - .1); }
      drawRect(); S.dirty = true; });
    addEventListener('mouseup', () => { if (!rDrag) return; const d = rDrag; rDrag = null;
      if (d.box) { const [a, b] = d.box; if (Math.abs(a[0] - b[0]) > 4 && Math.abs(a[1] - b[1]) > 4) {
        const [x0, y1] = c2m(Math.min(a[0], b[0]), Math.min(a[1], b[1])), [x1, y0] = c2m(Math.max(a[0], b[0]), Math.max(a[1], b[1]));
        st.bands.push({kind: st.kind, mode: KINDS[st.kind].mode, label: '', x0_m: +x0.toFixed(2), x1_m: +x1.toFixed(2), y0_m: +y0.toFixed(2), y1_m: +y1.toFixed(2)});
        st.sel = st.bands.length - 1; } }
      st.bands.forEach(b => ['x0_m', 'x1_m', 'y0_m', 'y1_m'].forEach(k => b[k] = +(+b[k]).toFixed(2)));
      schedule(); });
  }
  function drawCells() {
    const box = $f('fcCells'); box.innerHTML = '';
    const b = st.bands[st.sel]?.mode === 'sprite' ? st.bands[st.sel] : st.bands.find(x => x.mode === 'sprite' && x.cells);
    if (!b || !b.cells || !st.masks) return;
    const rows = b.cells.length, cols = b.cells[0].length, cv = document.createElement('canvas'); cv.width = cols * 8; cv.height = rows * 8;
    const x = cv.getContext('2d'), im = x.createImageData(cv.width, cv.height), m = st.masks.m;
    b.cells.forEach((row, r) => row.forEach((cell, c) => { for (let py = 0; py < 8; py++) for (let px = 0; px < 8; px++) {
      const j = ((r * 8 + py) * cv.width + c * 8 + px) * 4, chk = ((px >> 2) + (py >> 2)) % 2 ? 85 : 119; // transparent = checkerboard
      if (!cell) { im.data[j] = im.data[j + 1] = im.data[j + 2] = chk; im.data[j + 3] = 255; continue; }
      const [g, fg, bg] = cell, a = m[g * 64 + py * 8 + px];
      for (let q = 0; q < 3; q++) im.data[j + q] = bg ? bg[q] + a * (fg[q] - bg[q]) : (a >= .5 ? fg[q] : chk); im.data[j + 3] = 255; } }));
    x.putImageData(im, 0, 0);
    box.innerHTML = `<div class="dim">CP437 result of <b>${b.kind}</b> (${cols}×${rows} cells, exactly what the sprite holds)</div>`; box.appendChild(cv);
  }
  function drawBands() {
    $f('fcBands').innerHTML = st.bands.map((b, i) => {
      const [c, r] = b.mode === 'sprite' ? (b.cells ? [b.cells[0].length, b.cells.length] : bandGrid(b)) : [0, 0];
      const modes = b.kind === 'object' ? ['object'] : ['sprite', 'colour', 'record'];
      return `<div class="band ${i === st.sel ? 'on' : ''}" data-i="${i}"><span style="color:${KINDS[b.kind].col}">■</span>
 <select data-f="kind">${Object.keys(KINDS).map(k => `<option ${k === b.kind ? 'selected' : ''}>${k}</option>`).join('')}</select>
 <select data-f="mode">${modes.map(k => `<option ${k === b.mode ? 'selected' : ''}>${k}</option>`).join('')}</select>
 <input type="text" data-f="label" value="${esc(b.label || '')}" placeholder="label" style="width:90px">
 <span class="dim">${b.x0_m.toFixed(2)}–${b.x1_m.toFixed(2)} m · ${b.y0_m.toFixed(2)}–${b.y1_m.toFixed(2)} m</span>
 ${b.rgb ? `<span class="sw" style="background:rgb(${b.rgb})" title="${b.rgb}"></span>` : ''}${b.mode === 'sprite' ? `<span class="dim">${c}×${r} cells</span>
 <label title="how far the sprite stands out from the footprint face; raise it if the preview does not show (mesh walls stand further out)" style="display:inline">out <input type="number" data-f="out" min="0" max="3" step="0.1" value="${(b.out_m ?? 0.3).toFixed(1)}" style="width:46px"> m</label>` : ''}
 ${b.mode === 'sprite' && b.wall === undefined ? '' : b.mode === 'sprite' && !b.pos ? `<span class="warn">no wall behind: ${esc(b.wall?.reason || '')}</span>` : ''}
 <button data-f="del">×</button></div>`; }).join('') || '<div class="dim">no bands yet: pick a kind and drag a box on the face</div>';
    $f('fcBands').querySelectorAll('.band').forEach(el => { const i = +el.dataset.i, b = st.bands[i];
      el.onclick = e => { if (e.target.tagName === 'DIV' || e.target.tagName === 'SPAN') { st.sel = i; drawPanel(); } };
      el.querySelector('[data-f=kind]').onchange = e => { b.kind = e.target.value; b.mode = KINDS[b.kind].mode; schedule(); };
      el.querySelector('[data-f=mode]').onchange = e => { b.mode = e.target.value; schedule(); };
      el.querySelector('[data-f=label]').onchange = e => { b.label = e.target.value.trim(); drawPanel(); };
      const out = el.querySelector('[data-f=out]'); if (out) out.onchange = e => { b.out_m = clamp(+e.target.value || 0, 0, 3); schedule(); };
      el.querySelector('[data-f=del]').onclick = () => { st.bands.splice(i, 1); st.sel = -1; schedule(); }; });
  }
  // generic place-front meshes of this building: ticked ones are hidden now and dropped by the build
  function setFrontsHidden(names, hidden) { for (const n of names) RT.f.setVis && RT.f.setVis(n, hidden ? 0 : 1); S.dirty = true; }
  function drawFronts() {
    const fr = st.face.place_fronts || [];
    $f('fcFronts').innerHTML = fr.length ? '<div class="dim">generic shop front(s) the build placed on this building — ticked = replaced by this capture:</div>' +
      fr.map(p => `<label><input type="checkbox" data-n="${esc(p.inst_name)}" ${st.replace.has(p.inst_name) ? 'checked' : ''}> ${esc(p.label || '')} · ${esc(p.asset_match || '')} · ${p.fitted_width_m} m wide · faces ${p.bearing_deg}° · text “${esc(p.rendered_text || '')}”</label>`).join('') : '';
    $f('fcFronts').querySelectorAll('input').forEach(cb => cb.onchange = () => {
      if (cb.checked) st.replace.add(cb.dataset.n); else st.replace.delete(cb.dataset.n);
      setFrontsHidden([cb.dataset.n], cb.checked); drawCost(); });
  }
  function drawCost() {
    const sp = st.bands.filter(b => b.mode === 'sprite'), cells = sp.reduce((n, b) => n + (b.cells ? b.cells.length * b.cells[0].length : 0), 0);
    const col = st.bands.filter(b => b.mode === 'colour' && ['wall', 'trim'].includes(b.kind));
    $f('fcCost').innerHTML = `runtime cost: <b>${sp.length}</b> sprite(s), <b>${cells}</b> cells (blitted, no mesh) · mesh faces <b>+0</b>` +
      (col.length ? ` · ${col.map(b => `${b.kind} → <span class="sw" style="width:14px;height:10px;background:rgb(${b.rgb})"></span>${b.rgb}`).join(' ')} (appearance owner, next build)` : '') +
      (st.replace.size ? ` · generic shop front mesh removed (${st.replace.size})` : '') +
      `<br>${st.preview.length} sprite(s) live in the map now${st.bands.some(b => b.mode === 'record') ? ' · bands in <i>record</i> mode are stored as layout only (switch to sprite to draw them)' : ''}`;
  }
  function drawPanel() {
    if (!st.open) return;
    const f = st.face;
    $f('fcTitle').textContent = `Facade capture — ${st.bid.split(':').pop()} · face ${f.face_index + 1}/${f.faces}`;
    $f('fcFaceInfo').textContent = `${f.length_m.toFixed(2)} m wide · faces ${f.bearing_deg}° · measured eave ${f.height_m ?? '?'} m`;
    $f('fcH').value = st.heightM.toFixed(2);
    $f('fcKinds').querySelectorAll('.kb').forEach(b => b.classList.toggle('on', b.dataset.k === st.kind));
    drawSrc(); drawRect(); drawBands(); drawFronts(); drawCells(); drawCost();
  }

  // photo = 4-corner rectification of a facade photo; logo = the whole graphic into a sprite band (transparency kept)
  function setImgType(t) {
    st.imgType = t; if ($f('fcType')) $f('fcType').value = t;
    if ($f('fcStep1')) $f('fcStep1').textContent = t === 'logo'
      ? '1 · the whole graphic goes into the yellow sign box below: drag / resize the box on the face (checkerboard = transparent)'
      : "1 · drag the 4 yellow corners onto the face's corners (bottom = street, top = eave/height)";
    if (t === 'logo' && st.face && st.img && !st.bands.some(b => b.mode === 'sprite')) {
      const L = st.face.length_m, asp = st.img.width / st.img.height;
      let h = Math.min(1.0, st.heightM * .4), w = h * asp;
      if (w > L * .9) { w = L * .9; h = w / asp; }
      const y0 = clamp(2.4, .2, Math.max(.2, st.heightM - h - .1)), x0 = (L - w) / 2;
      st.bands.push({kind: 'sign', mode: 'sprite', label: 'logo', x0_m: +x0.toFixed(2), x1_m: +(x0 + w).toFixed(2), y0_m: +y0.toFixed(2), y1_m: +(y0 + h).toFixed(2)});
      st.sel = st.bands.length - 1;
    }
  }

  // ---------------------------------------------------------------- open / accept / close
  async function open(o) { // o: {bid, face?, x?, y?, image:{data_url}|{path}, queueItem?, kind?}
    build(); await masks();
    try { await loadFace(o.bid, o.face != null ? {face: o.face} : {x: o.x, y: o.y}); } catch (e) { status('face lookup failed: ' + e.message); return; }
    const img = new Image();
    await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error('image load')); img.src = o.image.data_url || ('/img?path=' + encodeURIComponent(o.image.path)); });
    const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height; const x = cv.getContext('2d'); x.drawImage(img, 0, 0);
    st.srcPixels = x.getImageData(0, 0, img.width, img.height).data;
    const iw = img.width, ih = img.height;
    Object.assign(st, {open: true, img, src: o.image, bands: [], sel: -1, queueItem: o.queueItem || null, heightM: st.face.height_m || 3,
      corners: [[iw * .05, ih * .08], [iw * .95, ih * .08], [iw * .95, ih * .92], [iw * .05, ih * .92]]});
    // a graphic with transparent pixels (a PNG logo from Google Images) is a logo, not a facade photo
    let clear = 0; for (let i = 3; i < st.srcPixels.length; i += 4 * 7) if (st.srcPixels[i] < 250) clear++;
    const isLogo = o.imgType ? o.imgType === 'logo' : clear > 20;
    $f('fcKind').value = o.kind || (isLogo || o.fromWeb ? 'web_image_reference' : 'google_streetview_reference'); $f('fcNote').value = o.note || '';
    setImgType(isLogo ? 'logo' : 'photo');
    // common sense default: a capture of the real shop front replaces the build's generic proxy front(s)
    st.replace = new Set((st.face.place_fronts || []).map(p => p.inst_name)); setFrontsHidden([...st.replace], true);
    const s = S.sel.find(t => t.kind === 'building' && t.id === o.bid); if (!s) selectThing({kind: 'building', id: o.bid, world: wallPt(st.face.length_m / 2, 0), face: st.face.face_index});
    dock(true); lookAtFace(); rectify(); drawPanel(); S.dirty = true;
    // a logo already has its default sign box: convert + preview it now (EDITOR-UX-02 browser check found a dropped
    // logo showed 0 cells until the operator happened to drag something)
    if (st.imgType === 'logo') schedule();
    status(st.imgType === 'logo' ? 'logo capture: move / resize the sign box on the face; the preview shows in the map'
      : 'facade capture: drag the 4 corners onto the face, then box the sign / wall bands');
  }
  function close() {
    // FACADE-CAPTURE-REVIEW-20261014 P2: cancel the pending preview and void any in-flight one
    clearTimeout(pvT); pvT = null; pvGen++;
    clearPreview(); const keep = frontsKeptHidden(); setFrontsHidden([...st.replace].filter(n => !keep.has(n)), false); st.replace = new Set();
    st.open = false; if ($f('fc')) dock(false); S.dirty = true; }
  async function accept() {
    if (!st.bands.length) { status('mark at least one band first'); return; }
    const kind = $f('fcKind').value, body = {
      building_id: st.bid, face_index: st.face.face_index,
      face: {length_m: +st.face.length_m.toFixed(3), height_m: st.heightM, measured_eave_m: st.face.height_m, bearing_deg: st.face.bearing_deg},
      source: st.src.data_url ? {data_url: st.src.data_url, kind} : {path: st.src.path, kind},
      rectified_data_url: (() => { const c = document.createElement('canvas'); c.width = st.rectW; c.height = st.rectH; c.getContext('2d').putImageData(st.rect, 0, 0); return c.toDataURL('image/png'); })(),
      corners_px: st.imgType === 'logo' ? null : st.corners.map(p => p.map(v => +v.toFixed(1))), image_type: st.imgType,
      bands: st.bands.filter(b => b.kind !== 'object').map(b => ({kind: b.kind, mode: b.mode, label: b.label, x0_m: b.x0_m, x1_m: b.x1_m, y0_m: b.y0_m, y1_m: b.y1_m,
        rgb: b.rgb, cells: b.mode === 'sprite' ? b.cells : undefined, out_m: b.mode === 'sprite' ? (b.out_m ?? 0.3) : undefined, encoding: b.mode === 'sprite' ? st.enc + (st.contrast !== 1 ? ' contrast ' + st.contrast : '') : undefined})),
      objects: st.bands.filter(b => b.kind === 'object').map(b => ({label: b.label || 'object', x0_m: b.x0_m, x1_m: b.x1_m, y0_m: b.y0_m, y1_m: b.y1_m})),
      text: $f('fcNote').value.trim(), queue_item: st.queueItem, replaces_place_fronts: [...st.replace]};
    try {
      clearTimeout(pvT); pvT = null; pvGen++;  // no late preview may land after the accept
      const cap = await api('/api/facade_capture', body);
      // FACADE-CAPTURE-REVIEW-20261014 P2: the server retired the earlier capture(s) on this face; drop their sprites
      for (const old of cap.supersedes || []) { for (const s of st.accepted[old] || []) RT.f.pvRemove(s); delete st.accepted[old]; }
      st.accepted[cap.capture_id] = st.preview; st.preview = []; st.visited.add(st.bid);
      st.acceptedFronts[cap.capture_id] = [...st.replace]; st.replace = new Set();
      st.open = false; dock(false);
      status(`accepted ${cap.capture_id}: ${cap.bands.filter(b => b.sprite).length} sprite(s) written, ${cap.colours_applied.length} colour(s) set${cap.queue_item ? ', ' + cap.queue_item + ' resolved' : ''} · next build bakes it`);
      await loadNotes(); await loadLayers(); await loadFace(st.bid, {face: st.face.face_index}); renderInsp();
    } catch (e) { status('ACCEPT FAILED: ' + e.message); }
  }
  async function retract(cid) {
    try { const r = await api('/api/facade_capture/retract', {capture_id: cid}); for (const s of st.accepted[cid] || []) RT.f.pvRemove(s); delete st.accepted[cid];
      const fr = st.acceptedFronts[cid] || []; delete st.acceptedFronts[cid]; const keep = frontsKeptHidden(); setFrontsHidden(fr.filter(n => !keep.has(n)), false);
      status(`${cid} retracted${r.reverted_colours.length ? ', reverted ' + r.reverted_colours.join('/') : ''}`); await loadFace(st.bid, {face: st.face.face_index}); await loadLayers(); renderInsp(); S.dirty = true;
    } catch (e) { status('retract failed: ' + e.message); }
  }
  // accepted captures are not in the served build yet: put their sprites back into the map on load
  async function loadAccepted() {
    try { const r = await api('/api/facade_captures');
      for (const c of r.captures) { st.accepted[c.capture_id] = c.sprites.map(s => addSprite(s.xp, s.pos)).filter(s => s >= 0); st.visited.add(c.building_id);
        st.acceptedFronts[c.capture_id] = c.replaces_place_fronts || []; setFrontsHidden(st.acceptedFronts[c.capture_id], true); }
      S.dirty = true; if (r.captures.length) clog('facade_captures', r.captures.length + ' accepted capture(s) injected');
    } catch (e) { clog('error', 'facade captures ' + e.message); }
  }
  function nextBuilding() {
    if (!S.L) return; const c = S.cam, captured = new Set(Object.keys(st.accepted).length ? [...st.visited] : []);
    const cand = S.L.buildings.filter(b => !st.visited.has(b.id) && b.id !== st.bid && !captured.has(b.id))
      .map(b => [Math.hypot(b.x - c.x, b.y - c.y), b]).sort((a, b) => a[0] - b[0]);
    if (!cand.length) { status('every building visited this session'); return; }
    if (st.bid) st.visited.add(st.bid);
    goto(cand[0][1].id.split('/').pop());
  }
  // inspector fragment for a selected building
  function inspHtml(s) {
    const f = st.face && st.bid === s.id ? st.face : null; if (!f) return '<div class="dim">face: click the wall you mean</div>';
    const caps = (f.captures || []).filter(c => +c.face_index === f.face_index);
    return `<div style="margin-top:4px"><b style="color:var(--acc)">face ${f.face_index + 1}/${f.faces}</b> · ${f.length_m.toFixed(2)} m wide × ${f.height_m ?? '?'} m · faces ${f.bearing_deg}°
 <span class="acts"><button data-fc="prev">◀</button><button data-fc="next">▶</button></span>
 ${caps.map(c => `<div class="dim">${esc(c.capture_id)} · ${esc(c.source.kind)} · ${c.bands.length} band(s) <button data-fc="retract" data-id="${esc(c.capture_id)}">Retract</button></div>`).join('')}
 <div class="acts"><button data-fc="capture" title="paste (⌘V) a photo of this face, or pick an image">Capture facade…</button><button data-fc="nextb">Next building ▶</button></div></div>`;
  }
  function bindInsp(box) {
    box.querySelectorAll('[data-fc]').forEach(b => b.onclick = () => {
      const k = b.dataset.fc;
      if (k === 'prev') cycleFace(-1); else if (k === 'next') cycleFace(1); else if (k === 'nextb') nextBuilding();
      else if (k === 'retract') retract(b.dataset.id);
      else if (k === 'capture') pickImage();
    });
  }
  function pickImage() { // file picker fallback; ⌘V over the building opens the capture directly
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*';
    inp.onchange = () => { const f = inp.files[0]; if (!f) return; const fr = new FileReader();
      fr.onload = () => open({bid: st.bid, face: st.face.face_index, image: {data_url: fr.result}}); fr.readAsDataURL(f); };
    inp.click();
  }
  return {open, close, onSelect, drawOverlay, inspHtml, bindInsp, loadAccepted, nextBuilding, isOpen: () => st.open, state: st,
    openFromNote: n => open({bid: n.building_ids[0], x: n.world_points[0][0], y: n.world_points[0][1], image: {path: n.image}, queueItem: n.id, note: n.text})};
})();
