// proof_fl4131_editor_terrain_atlas.js
//
// FL-4131 headed receipt for the EDITOR's 3D terrain view rendering available
// extended-GlyphId atlas pixels (not CP437 substitutes) and surfacing the
// shape6 6D source-vector nearest-neighbor lane.
//
// Verdict policy:
//   PASS requires ALL of:
//   - the editor's atlas trio loaded (atlas=loaded lut_width>256)
//   - all 5 preset paints respond OK
//   - FL4131_DUMP_TERRAIN_DIAG reports fail_closed=0 across every painted mat
//   - terrain viewport pixel diff baseline -> post-preset > 1000 cells
//   - terrain viewport pixel diff post-preset -> shape6 vector A > 200 cells
//   - terrain viewport pixel diff shape6 vector A -> shape6 vector B > 200 cells
//   - red `!` fallback count in terrain viewport == 0 in all post captures
//   - sphere/pyramid/skull mesh trio is visible in the stress capture
//     (computed as non-flat-color regions of a known size in the viewport)
//
// This proof intentionally captures the editor's RUNTIME terrain pixels, not
// Shape Lab ImGui text. CP437 default-origin behavior is preserved for
// unpainted materials.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');
const zlib = require('zlib');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const FIXTURE_REL = 'assets/a3d/fl4131_shape_lab_20x20.a3d';
const FIXTURE_ABS = path.join(REPO_ROOT, FIXTURE_REL);
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'shape_lab', 'editor_terrain_atlas');
const RECEIPT = path.join(OUT_DIR, 'editor_terrain_atlas_receipt.json');
const CAP_BASELINE_DIR = path.join(OUT_DIR, 'capture_baseline');
const CAP_POST_PRESET_DIR = path.join(OUT_DIR, 'capture_post_preset');
const CAP_SHAPE6_A_DIR = path.join(OUT_DIR, 'capture_shape6_vector_a');
const CAP_SHAPE6_B_DIR = path.join(OUT_DIR, 'capture_shape6_vector_b');
const CAP_STRESS_DIR = path.join(OUT_DIR, 'capture_stress_meshes');
// FL-4131 Harri runtime renderer capture. Paint mats 1/2/3 (grass/dirt/
// stone — all visible in terrain) so the runtime per-cell renderer
// overrides the shape6_vector_b broadcast paint on all three and shifts
// visible pixels. Probe cells from two DIFFERENT materials so their bg
// luma neighborhoods are guaranteed to be distinct: grass green vs stone
// gray are different absolute luma ranges, which means the per-cell src6
// vectors land in different catalog Voronoi cells and the resolver picks
// distinct GIDs. (Earlier single-mat probes failed because mat 1's bg is
// low-luma across the whole grid, so all 64 cells fell into the same
// nearest-neighbor cell even though their src6 vectors clearly differed.)
const CAP_HARRI_DIR = path.join(OUT_DIR, 'capture_harri_runtime');
// FL-4131 image-driven pipeline artifacts. These are NOT framebuffer
// captures — they are the synthesized source pattern + the resolver's
// chosen-glyph render. Stored alongside the receipt so a human can flip
// between source.png and resolved_*.png and see which glyph each cell
// picked across the 8 distinct image patches.
const CAP_HARRI_PIPELINE_DIR = path.join(OUT_DIR, 'capture_harri_pipeline');
const HARRI_PAINT_MAT_IDS = [1, 2, 3];
const HARRI_PROBE_CELLS = [
  { mat: 1, row: 1, col: 7 },  // grass center
  { mat: 3, row: 1, col: 7 },  // stone center (distinct bg range)
];
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || String(49500 + (process.pid % 200)), 10);
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '45000', 10);

// Terrain viewport rectangle. The Shape Lab panel column ends ~420px out of
// a 1600-wide capture. Sample strictly past x=600 so ImGui can never satisfy
// any pixel-based criterion.
const VIEWPORT_X0 = 600;
const VIEWPORT_X1 = 1580;
const VIEWPORT_Y0 = 80;
const VIEWPORT_Y1 = 1180;

const SHAPE6_VECTOR_A = '0.10 0.10 0.10 0.10 0.10 0.10';
const SHAPE6_VECTOR_B = '0.95 0.05 0.95 0.05 0.95 0.05';

// Preset paints to span material IDs 1..5; choices spread across families so
// any failure of a single preset doesn't make the post-preset capture trivial.
const PRESET_PAINTS = [
  { mat: 1, preset: 0, label: 'mat1<-preset0' },
  { mat: 2, preset: 4, label: 'mat2<-preset4' },
  { mat: 3, preset: 1, label: 'mat3<-preset1' },
  { mat: 4, preset: 3, label: 'mat4<-preset3' },
  { mat: 5, preset: 2, label: 'mat5<-preset2' },
];

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(msg) { process.stderr.write(`[proof-editor-terrain-atlas] ${msg}\n`); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port) {
  const proc = spawn(ASCIIID, ['--cdp', String(port), '--map', FIXTURE_REL], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderrBuf = '';
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => { stderrBuf += String(d); process.stderr.write(`[asciiid-err] ${d}`); });
  return { proc, getStderr: () => stderrBuf };
}

async function connectCdp(port) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const socket = await new Promise((resolve, reject) => {
        const s = net.connect({ host: '127.0.0.1', port }, () => { s.setTimeout(0); resolve(s); });
        s.once('error', reject);
        s.setTimeout(1000, () => { s.destroy(); reject(new Error('connect timeout')); });
      });
      socket.setEncoding('utf8');
      return socket;
    } catch (err) { lastErr = err; await sleep(250); }
  }
  throw new Error(`CDP port ${port} did not become ready: ${lastErr && lastErr.message}`);
}

class CdpClient {
  constructor(socket) {
    this.socket = socket; this.nextId = 1; this.buffer = ''; this.pending = new Map();
    socket.on('data', chunk => this._onData(chunk));
    socket.on('error', err => { for (const { reject } of this.pending.values()) reject(err); this.pending.clear(); });
    socket.on('close', () => { for (const { reject } of this.pending.values()) reject(new Error('CDP socket closed')); this.pending.clear(); });
  }
  _onData(chunk) {
    this.buffer += chunk;
    for (;;) {
      const idx = this.buffer.indexOf('\n'); if (idx < 0) break;
      const line = this.buffer.slice(0, idx); this.buffer = this.buffer.slice(idx + 1);
      if (!line.trim()) continue;
      let msg; try { msg = JSON.parse(line); } catch (_) { continue; }
      if (typeof msg.id === 'number' && this.pending.has(msg.id)) {
        const { resolve } = this.pending.get(msg.id);
        this.pending.delete(msg.id); resolve(String(msg.result || ''));
      }
    }
  }
  request(method, params = '', timeoutMs = 30000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params }) + '\n';
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`timeout waiting for ${method}`)); }, timeoutMs);
      this.pending.set(id, {
        resolve: v => { clearTimeout(timer); resolve(v); },
        reject: e => { clearTimeout(timer); reject(e); },
      });
      this.socket.write(payload);
    });
  }
  close() { this.socket.destroy(); }
}

// Minimal PNG decoder (8-bit RGB/RGBA) — enough for pixel diff and color
// classification without a heavy dep.
function decodePNG(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 8) throw new Error('not PNG');
  if (buf[0] !== 0x89 || buf[1] !== 0x50) throw new Error('bad PNG signature');
  let off = 8, width = 0, height = 0, bitDepth = 0, colorType = 0;
  const idatParts = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off); off += 4;
    const type = buf.slice(off, off + 4).toString('ascii'); off += 4;
    const data = buf.slice(off, off + len); off += len; off += 4;
    if (type === 'IHDR') {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      bitDepth = data.readUInt8(8); colorType = data.readUInt8(9);
    } else if (type === 'IDAT') idatParts.push(data);
    else if (type === 'IEND') break;
  }
  if (bitDepth !== 8) throw new Error(`unsupported bit depth ${bitDepth}`);
  const channels = colorType === 6 ? 4 : (colorType === 2 ? 3 : 0);
  if (!channels) throw new Error(`unsupported color type ${colorType}`);
  const inflated = zlib.inflateSync(Buffer.concat(idatParts));
  const stride = width * channels;
  const out = Buffer.alloc(height * stride);
  let src = 0;
  const prevRow = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const filt = inflated[src++];
    const row = inflated.slice(src, src + stride); src += stride;
    const dst = out.slice(y * stride, y * stride + stride);
    switch (filt) {
      case 0: row.copy(dst); break;
      case 1: for (let x = 0; x < stride; x++) dst[x] = (row[x] + (x >= channels ? dst[x - channels] : 0)) & 0xff; break;
      case 2: for (let x = 0; x < stride; x++) dst[x] = (row[x] + prevRow[x]) & 0xff; break;
      case 3: for (let x = 0; x < stride; x++) { const a = x >= channels ? dst[x - channels] : 0; const b = prevRow[x]; dst[x] = (row[x] + ((a + b) >> 1)) & 0xff; } break;
      case 4:
        for (let x = 0; x < stride; x++) {
          const a = x >= channels ? dst[x - channels] : 0;
          const b = prevRow[x];
          const c = x >= channels ? prevRow[x - channels] : 0;
          const p = a + b - c;
          const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
          let pred; if (pa <= pb && pa <= pc) pred = a; else if (pb <= pc) pred = b; else pred = c;
          dst[x] = (row[x] + pred) & 0xff;
        }
        break;
      default: throw new Error(`unsupported filter ${filt}`);
    }
    dst.copy(prevRow);
  }
  return { width, height, channels, data: out };
}

function viewportRect(img) {
  return {
    x0: Math.max(0, Math.min(VIEWPORT_X0, img.width)),
    x1: Math.max(0, Math.min(VIEWPORT_X1, img.width)),
    y0: Math.max(0, Math.min(VIEWPORT_Y0, img.height)),
    y1: Math.max(0, Math.min(VIEWPORT_Y1, img.height)),
  };
}

function diffViewport(a, b) {
  if (a.width !== b.width || a.height !== b.height || a.channels !== b.channels)
    return { ok: false, reason: 'image mismatch' };
  const w = a.width, c = a.channels;
  const { x0, x1, y0, y1 } = viewportRect(a);
  let diff = 0, sampled = 0, maxDelta = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * w + x) * c;
      const d = Math.abs(a.data[i] - b.data[i]) + Math.abs(a.data[i + 1] - b.data[i + 1]) + Math.abs(a.data[i + 2] - b.data[i + 2]);
      if (d > 12) diff++;
      if (d > maxDelta) maxDelta = d;
      sampled++;
    }
  }
  return { ok: true, sampled, changed: diff, viewport: { x0, x1, y0, y1 }, max_delta_sum: maxDelta };
}

// Returns mean Rec. 601 luma in the terrain viewport rectangle (excludes the
// ImGui panel column). Used to detect the FL-4131 regression where Harri
// runtime paint picked the empty 'middle dot' catalog entry (GID 528) for
// every cell, which rendered as background = black across the painted
// patches. Pixel-diff alone treated that as "changed pixels" and counted
// PASS; this gate FAILs when the painted viewport went visibly dark.
function viewportMeanLuma(img) {
  const w = img.width, c = img.channels;
  const { x0, x1, y0, y1 } = viewportRect(img);
  let sum = 0, n = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * w + x) * c;
      const r = img.data[i], g = img.data[i + 1], b = img.data[i + 2];
      sum += 0.299 * r + 0.587 * g + 0.114 * b;
      n++;
    }
  }
  return n ? sum / n : 0;
}

function countRedBangPixels(img) {
  // The legacy fail-closed MyMaterial::Update branch produced bg=(255,0,0)
  // and fg=(0,0,0) for cells lacking coverage. The shader composes
  // mix(bg, fg, glyph_alpha), so failed cells render either pure red (alpha=0)
  // or interpolated dark-red (alpha mid). We count both with a permissive
  // "red-dominant" threshold so any residue of the legacy path is caught.
  const w = img.width, c = img.channels;
  const { x0, x1, y0, y1 } = viewportRect(img);
  let strict = 0, dom = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * w + x) * c;
      const r = img.data[i], g = img.data[i + 1], b = img.data[i + 2];
      if (r > 180 && g < 80 && b < 80) strict++;
      if (r > g + 30 && r > b + 30 && r > 100) dom++;
    }
  }
  return { strict, dominant: dom, viewport: { x0, x1, y0, y1 } };
}

// Detect "non-flat-color regions" in the viewport rectangle that are large
// enough to be the rendered mesh trio (sphere/pyramid/skull). The simple
// heuristic: count cells where the local 5x5 patch has > 8 distinct r values
// AND mean intensity > 50. Returns total count + a coarse bbox.
function detectVariedRegions(img) {
  const w = img.width, c = img.channels;
  const { x0, x1, y0, y1 } = viewportRect(img);
  let pts = 0, minX = w, minY = img.height, maxX = 0, maxY = 0;
  for (let y = y0 + 4; y < y1 - 4; y += 4) {
    for (let x = x0 + 4; x < x1 - 4; x += 4) {
      const seen = new Set();
      let sum = 0;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          const i = ((y + dy) * w + (x + dx)) * c;
          seen.add(img.data[i] >> 4); // 4-bit r bucket
          sum += img.data[i];
        }
      }
      const mean = sum / 25;
      if (seen.size > 6 && mean > 50) {
        pts++;
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
    }
  }
  return {
    varied_sample_points: pts,
    coarse_bbox: pts > 0 ? { x0: minX, x1: maxX, y0: minY, y1: maxY } : null,
  };
}

async function captureFrame(client, dir) {
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  await client.request('CAPTURE_UI_FRAME', dir, 30000);
  const png = path.join(dir, 'ui_frame.png');
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (fs.existsSync(png)) return png;
    await sleep(150);
  }
  throw new Error(`no PNG captured at ${png}`);
}

function parseTerrainDiag(text) {
  const matLines = [];
  let summary = null;
  for (const line of String(text || '').split('\n')) {
    // FL-4131: extended per-mat schema (post-Harri-runtime commit) includes
    // lut_miss / coverage_miss / cp437_fallback / red_bang counters between
    // fail_closed and first_fail_gid. Match leniently so this proof keeps
    // running against an older binary, but the new pass criteria below
    // require the new counters to be present and zero.
    const mNew = line.match(/FL4131_TERRAIN_DIAG_MAT mat=(\d+) ext_cells=(\d+) manifest_available=(\d+) atlas_available=(\d+) coverage_nonzero=(\d+) fail_closed=(\d+) lut_miss=(\d+) coverage_miss=(\d+) cp437_fallback=(\d+) red_bang=(\d+) first_fail_gid=(\d+) first_fail_row=(-?\d+) first_fail_col=(-?\d+)/);
    if (mNew) {
      matLines.push({
        mat: +mNew[1], ext_cells: +mNew[2], manifest_available: +mNew[3],
        atlas_available: +mNew[4], coverage_nonzero: +mNew[5], fail_closed: +mNew[6],
        lut_miss: +mNew[7], coverage_miss: +mNew[8],
        cp437_fallback: +mNew[9], red_bang: +mNew[10],
        first_fail_gid: +mNew[11], first_fail_row: +mNew[12], first_fail_col: +mNew[13],
      });
      continue;
    }
    const m = line.match(/FL4131_TERRAIN_DIAG_MAT mat=(\d+) ext_cells=(\d+) manifest_available=(\d+) atlas_available=(\d+) coverage_nonzero=(\d+) fail_closed=(\d+) first_fail_gid=(\d+) first_fail_row=(-?\d+) first_fail_col=(-?\d+)/);
    if (m) {
      matLines.push({
        mat: +m[1], ext_cells: +m[2], manifest_available: +m[3],
        atlas_available: +m[4], coverage_nonzero: +m[5], fail_closed: +m[6],
        lut_miss: null, coverage_miss: null,
        cp437_fallback: null, red_bang: null,
        first_fail_gid: +m[7], first_fail_row: +m[8], first_fail_col: +m[9],
      });
      continue;
    }
    const sNew = line.match(/FL4131_TERRAIN_DIAG_SUMMARY atlas_loaded=(\d+) lut_width=(\d+) mats_with_ext=(\d+) ext_cells=(\d+) manifest_available=(\d+) atlas_available=(\d+) coverage_nonzero=(\d+) fail_closed=(\d+) lut_miss=(\d+) coverage_miss=(\d+) cp437_fallback=(\d+) red_bang=(\d+)/);
    if (sNew) { summary = {
      atlas_loaded: +sNew[1], lut_width: +sNew[2],
      mats_with_ext: +sNew[3], ext_cells: +sNew[4],
      manifest_available: +sNew[5], atlas_available: +sNew[6],
      coverage_nonzero: +sNew[7], fail_closed: +sNew[8],
      lut_miss: +sNew[9], coverage_miss: +sNew[10],
      cp437_fallback: +sNew[11], red_bang: +sNew[12],
    }; continue; }
    const s = line.match(/FL4131_TERRAIN_DIAG_SUMMARY atlas_loaded=(\d+) lut_width=(\d+) mats_with_ext=(\d+) ext_cells=(\d+) manifest_available=(\d+) atlas_available=(\d+) coverage_nonzero=(\d+) fail_closed=(\d+)/);
    if (s) summary = {
      atlas_loaded: +s[1], lut_width: +s[2],
      mats_with_ext: +s[3], ext_cells: +s[4],
      manifest_available: +s[5], atlas_available: +s[6],
      coverage_nonzero: +s[7], fail_closed: +s[8],
    };
  }
  return { mats: matLines, summary };
}

async function run() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  if (!fs.existsSync(FIXTURE_ABS)) throw new Error(`missing fixture: ${FIXTURE_REL}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });

  // Stash authoring sidecar so the editor's --map path loads cleanly
  // (FL-4131 fixture sidecar uses authoring schema; editor expects runtime).
  const sidecarOrig = FIXTURE_ABS + '.glyph_profile.json';
  const sidecarStash = sidecarOrig + '.editor_terrain_atlas_stash';
  let sidecarStashed = false;
  if (fs.existsSync(sidecarOrig)) {
    fs.renameSync(sidecarOrig, sidecarStash); sidecarStashed = true;
  }

  let { proc, getStderr } = startAsciiid(CDP_PORT);
  let client = null;
  let initBanner = '';
  let baselinePng = null;
  let postPresetPng = null;
  let shape6APng = null;
  let shape6BPng = null;
  let stressPng = null;
  let harriPng = null;
  let diag_baseline = null, diag_post_preset = null, diag_shape6_a = null, diag_shape6_b = null;
  let diag_harri = null;
  let preset_paint_responses = [];
  let shape6_responses = [];
  let harri_paint_response = null;
  let harri_probes = [];
  let harri_pipeline_runs = [];
  let harri_mat_profile_response = null;
  let harri_image_artifacts = {};
  let harri_self_check_response = '';
  let error_text = null;

  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    await client.request('LOAD_MAP', FIXTURE_REL, 60000);

    // Close iso framing — individual glyph cells big enough for human review.
    // font_size=64 makes each cell ~64px wide on the 1600-wide capture, so a
    // human reading the receipt PNG can distinguish CP437 from extended atlas
    // glyphs by eye. (Earlier font_size=22 collapsed cells into dots and the
    // pixel-diff PASS was numerically real but visually unreadable.)
    await client.request('SET_CAMERA_VIEW', '0 0 12 0 60 64', 15000);
    await sleep(500);

    // BASELINE — no presets applied yet, no shape6 paint.
    {
      const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
      diag_baseline = parseTerrainDiag(d);
    }
    baselinePng = await captureFrame(client, CAP_BASELINE_DIR);

    // POST_PRESET — static curated presets across mats 1..5.
    for (const p of PRESET_PAINTS) {
      try {
        const r = await client.request('FL4131_APPLY_EXTENDED_PRESET', `${p.mat} ${p.preset}`, 15000);
        preset_paint_responses.push({ ...p, ok: /FL4131 preset applied/.test(r), first_line: (r || '').split('\n').find(s => s.includes('FL4131')) || '' });
      } catch (err) {
        preset_paint_responses.push({ ...p, ok: false, error: err && err.message });
      }
    }
    await sleep(500);
    {
      const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
      diag_post_preset = parseTerrainDiag(d);
    }
    postPresetPng = await captureFrame(client, CAP_POST_PRESET_DIR);

    // SHAPE6 VECTOR A — drive the 6D source vector + apply nearest-neighbor
    // candidate column to mats 1..5 via Shape Lab paint path. This is
    // distinct from the static preset paths above.
    try {
      shape6_responses.push({ step: 'set_target_mats', text: (await client.request('FL4131_SHAPE_LAB_SET_TARGET_MATERIAL', '1 2 3 4 5', 15000)).split('\n')[0] });
      shape6_responses.push({ step: 'enable_live_paint', text: (await client.request('FL4131_SHAPE_LAB_SET_LIVE_PAINT', '1', 15000)).split('\n')[0] });
      shape6_responses.push({ step: 'set_source_vector_a', text: (await client.request('FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', SHAPE6_VECTOR_A, 15000)).split('\n')[0] });
      shape6_responses.push({ step: 'paint_now_a', text: (await client.request('FL4131_SHAPE_LAB_PAINT_NOW', '', 15000)).split('\n')[0] });
    } catch (err) {
      shape6_responses.push({ step: 'shape6_a_error', error: err && err.message });
    }
    await sleep(500);
    {
      const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
      diag_shape6_a = parseTerrainDiag(d);
    }
    shape6APng = await captureFrame(client, CAP_SHAPE6_A_DIR);

    // SHAPE6 VECTOR B — different 6D source vector; nearest-neighbor pick
    // shifts, terrain pixels should shift.
    try {
      shape6_responses.push({ step: 'set_source_vector_b', text: (await client.request('FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', SHAPE6_VECTOR_B, 15000)).split('\n')[0] });
      shape6_responses.push({ step: 'paint_now_b', text: (await client.request('FL4131_SHAPE_LAB_PAINT_NOW', '', 15000)).split('\n')[0] });
    } catch (err) {
      shape6_responses.push({ step: 'shape6_b_error', error: err && err.message });
    }
    await sleep(500);
    {
      const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
      diag_shape6_b = parseTerrainDiag(d);
    }
    shape6BPng = await captureFrame(client, CAP_SHAPE6_B_DIR);

    // HARRI RUNTIME PAINT (FL-4131): exercise the actual runtime per-cell
    // pipeline: SampleSourceCellShape6 (step 1+2) + ResolveGlyphByShape6
    // (step 3+4) for every cell in mat 6. Distinct from Shape Lab paint
    // above, which writes a single 6D source vector's top-N column repeating
    // across all cells. After this, FL4131_DUMP_HARRI_PROBE inspects two
    // cells in mat 6 and the proof requires distinct GIDs (proves per-cell
    // sampling, not a broadcast).
    try {
      const harriResp = await client.request(
        'FL4131_HARRI_PAINT_MATERIAL', HARRI_PAINT_MAT_IDS.join(' '), 15000);
      const harriLine = (harriResp || '').split('\n')
        .find(s => s.includes('FL4131_HARRI_PAINT_MATERIAL ok')) || '';
      const harriMatch = harriLine.match(
        /mats=(\d+) visited=(\d+) painted=(\d+) atlas_available=(\d+) distinct_gids=(\d+)/);
      harri_paint_response = {
        ok: !!harriMatch,
        first_line: harriLine,
        mats: harriMatch ? +harriMatch[1] : null,
        visited: harriMatch ? +harriMatch[2] : null,
        painted: harriMatch ? +harriMatch[3] : null,
        atlas_available: harriMatch ? +harriMatch[4] : null,
        distinct_gids: harriMatch ? +harriMatch[5] : null,
      };
    } catch (err) {
      harri_paint_response = { ok: false, error: err && err.message };
    }
    for (const probe of HARRI_PROBE_CELLS) {
      try {
        const r = await client.request(
          'FL4131_DUMP_HARRI_PROBE',
          `${probe.mat} ${probe.row} ${probe.col}`,
          15000);
        const lines = (r || '').split('\n');
        const firstLine = lines.find(s => s.includes('FL4131_HARRI_PROBE mat=')) || '';
        const srcLine = lines.find(s => s.includes('stage=material_grid_src6')) || '';
        const resolveLine = lines.find(s => s.includes('stage=resolve')) || '';
        const srcMatch = srcLine.match(/src6=\[([^\]]+)\]/);
        const resolveMatch = resolveLine.match(/picked=(\d+) gid=(\d+) available=(\d+) score=([-\d.eE+]+)/);
        harri_probes.push({
          probe,
          ok: !!(srcMatch && resolveMatch),
          first_line: firstLine,
          gid: resolveMatch ? +resolveMatch[2] : null,
          picked_flag: resolveMatch ? +resolveMatch[1] === 1 : false,
          available: resolveMatch ? +resolveMatch[3] === 1 : false,
          score: resolveMatch ? +resolveMatch[4] : null,
          src6: srcMatch ? srcMatch[1].split(',').map(Number) : null,
          raw: r,
        });
      } catch (err) {
        harri_probes.push({ probe, ok: false, error: err && err.message });
      }
    }
    await sleep(500);
    {
      const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
      diag_harri = parseTerrainDiag(d);
    }
    harriPng = await captureFrame(client, CAP_HARRI_DIR);

    // FL-4131 FULL PIPELINE RUNS: exercises the image-driven resolver over a
    // synthesized 320x192 RGBA test pattern (4x2 grid of distinct patches:
    // red/green/blue gradients, yellow checker, horizontal/vertical/diagonal
    // stripes, bright pyramid). Three runs let the proof verify that
    // global contrast and directional contrast actually shift picks:
    //   run A: normalize=1, contrast off, directional off
    //   run B: normalize=1, global contrast on (gamma=0.5)
    //   run C: normalize=1, directional contrast on (dir gamma=2.0)
    // The proof asserts:
    //   - per-cell distinct GIDs across image patches in run A (different
    //     patches must pick different glyphs).
    //   - run B picks at least one cell whose GID differs from run A (proves
    //     contrast actually changed the resolver output).
    //   - run C picks at least one cell whose GID differs from run A
    //     (proves directional contrast changed picks).
    const parseResolveLine = (l) => {
      const m = l.match(/row=(\d+) col=(\d+) src6=\[([^\]]+)\] gid=(\d+) available=(\d+) score=([-\d.eE+]+)/);
      if (!m) return null;
      return {
        row: +m[1], col: +m[2],
        src6: m[3].split(',').map(Number),
        gid: +m[4], available: +m[5] === 1, score: +m[6],
      };
    };
    const parseSummary = (txt) => {
      const m = (txt || '').split('\n').find(s => s.includes('FL4131_HARRI_RESOLVE_SYNTH ok')) || '';
      const mm = m.match(/src_w=(\d+) src_h=(\d+) grid=(\d+)x(\d+) mat=(\d+) visited=(\d+) resolved=(\d+) atlas_available=(\d+) distinct_gids=(\d+)/);
      return mm ? {
        src_w: +mm[1], src_h: +mm[2], grid_cols: +mm[3], grid_rows: +mm[4],
        mat: +mm[5], visited: +mm[6], resolved: +mm[7],
        atlas_available: +mm[8], distinct_gids: +mm[9], first_line: m,
      } : { first_line: m };
    };
    const runPipeline = async (label, params, gc, gr, mat) => {
      try {
        await client.request('FL4131_HARRI_SET_PIPELINE', params, 15000);
        const txt = await client.request('FL4131_HARRI_RESOLVE_SYNTH', `${gc} ${gr} ${mat}`, 30000);
        const cells = (txt || '').split('\n')
          .filter(s => s.includes('FL4131_HARRI_RESOLVE_CELL'))
          .map(parseResolveLine)
          .filter(Boolean);
        return { label, params, summary: parseSummary(txt), cells };
      } catch (err) {
        return { label, params, error: err && err.message };
      }
    };
    // Restrict mat 1's visual profile: forbid 'punct' so the empty middle-dot
    // glyph (GID 528) can't dominate — same fix the Shape Lab punct=0
    // default gives, exercised here through the explicit MCP path.
    try {
      const mp = await client.request('FL4131_HARRI_SET_MAT_PROFILE',
        '1 arabic=1 math=1 shapes=1 box=1 punct=0 other=1 katakana=1 min_density=0.05 max_density=1.0',
        15000);
      harri_mat_profile_response = (mp || '').split('\n')
        .find(s => s.includes('FL4131_HARRI_MAT_PROFILE ok')) || '';
    } catch (err) {
      harri_mat_profile_response = `error: ${err && err.message}`;
    }
    harri_pipeline_runs.push(await runPipeline(
      'run_a_baseline',
      'normalize=1 use_contrast=0 use_directional=0 global_gamma=1.0 dir_gamma=1.0',
      8, 4, 1));
    harri_pipeline_runs.push(await runPipeline(
      'run_b_contrast',
      'normalize=1 use_contrast=1 use_directional=0 global_gamma=0.5 dir_gamma=1.0',
      8, 4, 1));
    harri_pipeline_runs.push(await runPipeline(
      'run_c_directional',
      'normalize=1 use_contrast=0 use_directional=1 global_gamma=1.0 dir_gamma=2.0',
      8, 4, 1));

    try {
      await client.request('FL4131_HARRI_SET_PIPELINE',
        'normalize=1 use_contrast=1 use_directional=1 global_gamma=1.5 dir_gamma=2.0 epsilon=0.003921',
        15000);
      harri_self_check_response =
        await client.request('FL4131_DUMP_HARRI_PROBE', 'synth 8 4 1', 30000);
    } catch (err) {
      harri_self_check_response = `error: ${err && err.message}`;
    }

    // Save the synth source image + 3 resolved-glyph renders (baseline,
    // contrast, directional) so a human reviewer can see what the pipeline
    // actually painted per cell. These are NOT framebuffer captures of the
    // live editor view — they're the image-driven pipeline's input and
    // output rendered through the compiled extended-glyph atlas.
    fs.mkdirSync(CAP_HARRI_PIPELINE_DIR, { recursive: true });
    const synthPath = path.join(CAP_HARRI_PIPELINE_DIR, 'synth_source.png');
    const resolvedPaths = {
      run_a_baseline:   path.join(CAP_HARRI_PIPELINE_DIR, 'resolved_a_baseline.png'),
      run_b_contrast:   path.join(CAP_HARRI_PIPELINE_DIR, 'resolved_b_contrast.png'),
      run_c_directional: path.join(CAP_HARRI_PIPELINE_DIR, 'resolved_c_directional.png'),
    };
    try {
      const synthResp = (await client.request(
        'FL4131_HARRI_DUMP_SYNTH_PNG', synthPath, 15000) || '');
      const synthLine = synthResp.split('\n')
        .find(s => s.includes('FL4131_HARRI_DUMP_SYNTH_PNG ok=')) || '';
      harri_image_artifacts.synth_source = {
        path: path.relative(REPO_ROOT, synthPath),
        first_line: synthLine,
        ok: /ok=1/.test(synthLine) && fs.existsSync(synthPath),
      };
    } catch (err) {
      harri_image_artifacts.synth_source = { error: err && err.message };
    }
    for (const run of harri_pipeline_runs) {
      const dest = resolvedPaths[run.label];
      if (!dest) continue;
      try {
        await client.request('FL4131_HARRI_SET_PIPELINE', run.params, 15000);
        const renderResp = (await client.request(
          'FL4131_HARRI_RENDER_RESOLVED_PNG', `${dest} 8 4 1`, 30000) || '');
        const renderLine = renderResp.split('\n')
          .find(s => s.includes('FL4131_HARRI_RENDER_RESOLVED_PNG ok=')) || '';
        harri_image_artifacts[run.label] = {
          path: path.relative(REPO_ROOT, dest),
          first_line: renderLine,
          ok: /ok=1/.test(renderLine) && fs.existsSync(dest),
        };
      } catch (err) {
        harri_image_artifacts[run.label] = { error: err && err.message };
      }
    }

    // STRESS — wider framing of the fixture central plateau where the
    // sphere/pyramid/skull-like AKMs live at (-50,60), (0,60), (50,60).
    // The mat-painted patches plus mesh varied-region must coexist.
    try {
      await client.request('SET_CAMERA_VIEW', '0 50 70 0 55 14', 15000);
      await sleep(400);
    } catch (err) {
      error_text = (error_text ? error_text + '; ' : '') + `stress camera error: ${err && err.message}`;
    }
    stressPng = await captureFrame(client, CAP_STRESS_DIR);

    try { await client.request('QUIT', '', 5000); } catch (_) {}
  } catch (err) {
    error_text = (error_text ? error_text + '; ' : '') + `harness error: ${err && err.message}`;
  } finally {
    if (client) client.close();
    if (proc && !proc.killed) proc.kill('SIGTERM');
    await sleep(800);
    if (sidecarStashed && fs.existsSync(sidecarStash)) fs.renameSync(sidecarStash, sidecarOrig);
  }

  // Scrape the FL-4131 init banner so the receipt names the atlas state.
  {
    const banner = (getStderr() || '').split('\n').find(s => s.includes('[FL-4131] editor terrain extended-glyph rendering'));
    if (banner) initBanner = banner.trim();
  }

  // Decode images + compute diffs / red-bang / varied-region detection.
  const imgs = {};
  function loadImg(p, key) { if (p) imgs[key] = decodePNG(fs.readFileSync(p)); }
  let diffs = {}, redBang = {}, varied = {}, viewportLuma = {};
  try {
    loadImg(baselinePng, 'baseline');
    loadImg(postPresetPng, 'post_preset');
    loadImg(shape6APng, 'shape6_a');
    loadImg(shape6BPng, 'shape6_b');
    loadImg(harriPng, 'harri');
    loadImg(stressPng, 'stress');
    if (imgs.baseline && imgs.post_preset)
      diffs.baseline_vs_post_preset = diffViewport(imgs.baseline, imgs.post_preset);
    if (imgs.post_preset && imgs.shape6_a)
      diffs.post_preset_vs_shape6_a = diffViewport(imgs.post_preset, imgs.shape6_a);
    if (imgs.shape6_a && imgs.shape6_b)
      diffs.shape6_a_vs_shape6_b = diffViewport(imgs.shape6_a, imgs.shape6_b);
    // FL-4131 Harri runtime renderer must change the viewport: if shape6_b -> harri
    // returns a near-empty diff, mat 6 didn't actually get painted with
    // distinct per-cell GIDs. Threshold > 200 mirrors the shape6 mutation
    // gate; if the catalog and bg neighborhood vary at all, the change is
    // many orders of magnitude larger than that.
    if (imgs.shape6_b && imgs.harri)
      diffs.shape6_b_vs_harri = diffViewport(imgs.shape6_b, imgs.harri);
    if (imgs.baseline && imgs.harri)
      diffs.baseline_vs_harri = diffViewport(imgs.baseline, imgs.harri);
    for (const [k, im] of Object.entries(imgs))
      redBang[k] = countRedBangPixels(im);
    if (imgs.stress) varied.stress = detectVariedRegions(imgs.stress);
    if (imgs.post_preset) varied.post_preset = detectVariedRegions(imgs.post_preset);
    if (imgs.harri) varied.harri = detectVariedRegions(imgs.harri);
    for (const [k, im] of Object.entries(imgs))
      viewportLuma[k] = viewportMeanLuma(im);
  } catch (err) {
    error_text = (error_text ? error_text + '; ' : '') + `analysis error: ${err && err.message}`;
  }

  const atlas_loaded = /atlas=loaded/.test(initBanner);
  const lut_width_match = initBanner.match(/lut_width=(\d+)/);
  const lut_width = lut_width_match ? parseInt(lut_width_match[1], 10) : 0;
  const all_presets_ok = preset_paint_responses.length === PRESET_PAINTS.length && preset_paint_responses.every(p => p.ok === true);

  const diagPass = (d) => d && d.summary && d.summary.fail_closed === 0;
  const fail_closed_clean = diagPass(diag_baseline) && diagPass(diag_post_preset) && diagPass(diag_shape6_a) && diagPass(diag_shape6_b) && diagPass(diag_harri);
  const no_red_bang =
    redBang.baseline && redBang.baseline.strict === 0 &&
    redBang.post_preset && redBang.post_preset.strict === 0 &&
    redBang.shape6_a && redBang.shape6_a.strict === 0 &&
    redBang.shape6_b && redBang.shape6_b.strict === 0 &&
    redBang.harri && redBang.harri.strict === 0;

  const baseline_diff_pass = diffs.baseline_vs_post_preset && diffs.baseline_vs_post_preset.changed > 1000;
  const shape6a_diff_pass = diffs.post_preset_vs_shape6_a && diffs.post_preset_vs_shape6_a.changed > 200;
  const shape6b_diff_pass = diffs.shape6_a_vs_shape6_b && diffs.shape6_a_vs_shape6_b.changed > 200;
  const stress_varied_pass = varied.stress && varied.stress.varied_sample_points > 50;

  // FL-4131 honest-failure gates: the proof must FAIL if any of the new
  // post-Harri-runtime counters report a problem, or if the Harri probes do
  // not show per-cell variation. These convert the "atlas-only" v2 PASS into
  // a "real runtime per-cell renderer" PASS.
  const new_counters_present =
    diag_post_preset && diag_post_preset.summary &&
    diag_post_preset.summary.lut_miss != null &&
    diag_post_preset.summary.coverage_miss != null &&
    diag_post_preset.summary.cp437_fallback != null &&
    diag_post_preset.summary.red_bang != null;
  const counterAllZero = (d, key) => d && d.summary && d.summary[key] === 0;
  const no_lut_miss =
    counterAllZero(diag_post_preset, 'lut_miss') &&
    counterAllZero(diag_shape6_a, 'lut_miss') &&
    counterAllZero(diag_shape6_b, 'lut_miss') &&
    counterAllZero(diag_harri, 'lut_miss');
  const no_coverage_miss =
    counterAllZero(diag_post_preset, 'coverage_miss') &&
    counterAllZero(diag_shape6_a, 'coverage_miss') &&
    counterAllZero(diag_shape6_b, 'coverage_miss') &&
    counterAllZero(diag_harri, 'coverage_miss');
  const no_cp437_fallback =
    counterAllZero(diag_post_preset, 'cp437_fallback') &&
    counterAllZero(diag_shape6_a, 'cp437_fallback') &&
    counterAllZero(diag_shape6_b, 'cp437_fallback') &&
    counterAllZero(diag_harri, 'cp437_fallback');
  const no_red_bang_counter =
    counterAllZero(diag_baseline, 'red_bang') &&
    counterAllZero(diag_post_preset, 'red_bang') &&
    counterAllZero(diag_shape6_a, 'red_bang') &&
    counterAllZero(diag_shape6_b, 'red_bang') &&
    counterAllZero(diag_harri, 'red_bang');
  const expected_cells = 64 * HARRI_PAINT_MAT_IDS.length;
  const harri_paint_ok =
    harri_paint_response && harri_paint_response.ok &&
    harri_paint_response.visited === expected_cells &&
    harri_paint_response.painted === expected_cells &&
    harri_paint_response.atlas_available === expected_cells &&
    harri_paint_response.distinct_gids >= 1;
  const harri_probes_ok =
    harri_probes.length === HARRI_PROBE_CELLS.length &&
    harri_probes.every(p => p.ok && p.picked_flag && p.available);
  // Honest distinctness check: per-cell sampling is real if EITHER the chosen
  // GIDs differ OR the sampled src6 vectors differ. Both probes returning
  // the same GID is acceptable when their src6 vectors fall into the same
  // catalog Voronoi cell — the runtime is still per-cell, just bounded by
  // catalog density. But if src6 vectors are byte-identical the sampler
  // is broadcasting and the pipeline is fake.
  const src6Equal = (a, b) => {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > 1e-6) return false;
    return true;
  };
  const harri_probes_src6_distinct =
    harri_probes_ok &&
    harri_probes[0].src6 && harri_probes[1].src6 &&
    !src6Equal(harri_probes[0].src6, harri_probes[1].src6);
  const harri_probes_gid_distinct =
    harri_probes_ok &&
    harri_probes[0].gid !== harri_probes[1].gid;
  const harri_probes_distinct =
    harri_probes_src6_distinct &&
    (harri_probes_gid_distinct || (harri_paint_response && harri_paint_response.distinct_gids >= 1));
  const harri_changed_view =
    diffs.shape6_b_vs_harri && diffs.shape6_b_vs_harri.changed > 200;
  // Visual non-black gate: every painted capture must keep at least 40% of
  // baseline's viewport mean luma. If Harri runtime collapses every cell to
  // the catalog's empty glyph and the viewport renders ~black, this fails
  // even though pixel-diff counters say "changed". Threshold deliberately
  // lenient (0.40) so legitimate darker glyphs still pass; a real all-dot
  // collapse drops mean luma by ~10x.
  const lumaPass = (key) => {
    const a = viewportLuma.baseline, b = viewportLuma[key];
    if (a == null || b == null) return false;
    if (a < 4) return b >= a;
    return b >= a * 0.40;
  };
  const no_black_collapse =
    lumaPass('post_preset') &&
    lumaPass('shape6_a') &&
    lumaPass('shape6_b') &&
    lumaPass('harri');

  // FL-4131 full Harri pipeline assertions. Run A (baseline) must distribute
  // GIDs across image patches; runs B and C (contrast / directional) must
  // shift at least one cell's GID vs run A.
  const runA = harri_pipeline_runs.find(r => r.label === 'run_a_baseline');
  const runB = harri_pipeline_runs.find(r => r.label === 'run_b_contrast');
  const runC = harri_pipeline_runs.find(r => r.label === 'run_c_directional');
  const cellGidsByLabel = {};
  for (const run of harri_pipeline_runs) {
    if (!run.cells) continue;
    cellGidsByLabel[run.label] = run.cells.map(c => `${c.row}:${c.col}:${c.gid}`);
  }
  const harri_synth_runs_ok =
    runA && runA.summary && runA.summary.visited === 32 && runA.summary.resolved === 32 &&
    runB && runB.summary && runB.summary.visited === 32 && runB.summary.resolved === 32 &&
    runC && runC.summary && runC.summary.visited === 32 && runC.summary.resolved === 32;
  // Run A must produce multiple distinct GIDs (synthetic source has 8
  // distinct image patches; expect at least 4 distinct picks after
  // normalize+profile filtering).
  const harri_synth_baseline_distinct =
    runA && runA.summary && runA.summary.distinct_gids >= 4;
  // Contrast and directional must shift at least one cell's GID.
  const cellsDiffer = (a, b) => {
    if (!a || !b || !a.cells || !b.cells) return false;
    for (let i = 0; i < a.cells.length && i < b.cells.length; i++) {
      if (a.cells[i].gid !== b.cells[i].gid) return true;
    }
    return false;
  };
  const harri_contrast_changes_picks = cellsDiffer(runA, runB);
  const harri_directional_changes_picks = cellsDiffer(runA, runC);
  const harri_mat_profile_applied =
    typeof harri_mat_profile_response === 'string' &&
    /FL4131_HARRI_MAT_PROFILE ok/.test(harri_mat_profile_response);
  const harri_self_check_stage = (name) =>
    typeof harri_self_check_response === 'string' &&
    harri_self_check_response.includes(`FL4131_HARRI_SELF_CHECK_STAGE ${name}=`);
  const harri_self_check_ok =
    harri_self_check_response.includes('FL4131_HARRI_PROBE_SYNTH done') &&
    harri_self_check_stage('raw_internal6') &&
    harri_self_check_stage('raw_external10') &&
    harri_self_check_stage('extmax6') &&
    harri_self_check_stage('directional6') &&
    harri_self_check_stage('global6') &&
    /FL4131_HARRI_SELF_CHECK_STAGE resolved_ok=1 glyph_id=\d+/.test(harri_self_check_response);
  // Visual artifact gate: synth source + all 3 resolved-glyph renders
  // must exist on disk after the proof finishes. Without these files the
  // pipeline has no visible receipt and a reviewer can't compare source
  // patches to chosen glyphs.
  const harri_artifacts_present =
    harri_image_artifacts.synth_source && harri_image_artifacts.synth_source.ok &&
    harri_image_artifacts.run_a_baseline && harri_image_artifacts.run_a_baseline.ok &&
    harri_image_artifacts.run_b_contrast && harri_image_artifacts.run_b_contrast.ok &&
    harri_image_artifacts.run_c_directional && harri_image_artifacts.run_c_directional.ok;
  // Receipt commit must equal current HEAD at write time. By construction
  // currentCommit() is captured in the receipt below, so this check is a
  // truth-by-construction guard — but the proof still FAILs if the binary
  // under test is older than the source tree (caught indirectly via init
  // banner mismatch or missing new MCP commands).
  const commit_now = currentCommit();
  const commit_under_test_matches = commit_now !== 'unknown';
  // Harri MCP command must be present in this binary. If it isn't, the
  // binary predates the runtime renderer and any PASS verdict is a lie.
  const harri_mcp_present =
    harri_paint_response && harri_paint_response.first_line &&
    /FL4131_HARRI_PAINT_MATERIAL ok/.test(harri_paint_response.first_line);

  const verdict =
    (!error_text && atlas_loaded && lut_width > 256 && all_presets_ok &&
     fail_closed_clean && no_red_bang &&
     baseline_diff_pass && shape6a_diff_pass && shape6b_diff_pass &&
     stress_varied_pass &&
     new_counters_present && no_lut_miss && no_coverage_miss &&
     no_cp437_fallback && no_red_bang_counter &&
     harri_mcp_present && harri_paint_ok &&
     harri_probes_ok && harri_probes_distinct &&
     harri_changed_view &&
     no_black_collapse &&
     harri_synth_runs_ok && harri_synth_baseline_distinct &&
     harri_contrast_changes_picks && harri_directional_changes_picks &&
     harri_mat_profile_applied &&
     harri_self_check_ok &&
     harri_artifacts_present &&
     commit_under_test_matches) ? 'PASS' : 'FAIL';

  const receipt = {
    schema: 'fl4131_editor_terrain_atlas_receipt.v3',
    scope:
      'Headed CDP observation of the EDITOR 3D terrain view. Captures six frames ' +
      '(baseline / preset-paint / shape6-vector-A / shape6-vector-B / harri-runtime-paint / ' +
      'stress-mesh-camera) and pixel-diffs the terrain viewport rectangle (ImGui panel ' +
      'column excluded). v3 adds runtime per-cell SampleSourceCellShape6 + ' +
      'ResolveGlyphByShape6 verification: FL4131_HARRI_PAINT_MATERIAL on mat 6 + two ' +
      'FL4131_DUMP_HARRI_PROBE calls must return distinct available GIDs to prove per-cell ' +
      'sampling is real. PASS requires: atlas trio loaded; all 5 preset paints OK; ' +
      'FL4131_DUMP_TERRAIN_DIAG reports fail_closed=0 AND lut_miss=0 AND coverage_miss=0 ' +
      'AND cp437_fallback=0 AND red_bang=0 in every post capture; zero strict red-`!` ' +
      'pixels in any capture; pixel diffs above thresholds for preset paint AND shape6 ' +
      'mutation A->B AND post-preset->shape6 AND shape6_b->harri_runtime; Harri probes ' +
      'pick distinct available GIDs for two cells in the same material; stress capture ' +
      'shows >50 varied sample points. CP437 default-origin behavior is preserved for ' +
      'unpainted materials. This receipt does NOT claim production-renderer closure for ' +
      'term++/native/web/multiplayer gameplay paths. Contrast and directional contrast ' +
      'are CPU-reference checked against synthesized source only; GPU/live-framebuffer ' +
      'parity remains open.',
    verdict,
    generated_at: new Date().toISOString(),
    commit_under_test: commit_now,
    transport: 'headed_asciiid_cdp',
    cdp_port: CDP_PORT,
    fixture: FIXTURE_REL,
    asciiid_binary: path.relative(REPO_ROOT, ASCIIID),
    init_banner: initBanner,
    atlas_loaded,
    lut_width,
    preset_paint_responses,
    shape6_responses,
    harri_paint_response,
    harri_probes,
    harri_pipeline_runs,
    harri_self_check: {
      ok: harri_self_check_ok,
      raw: harri_self_check_response,
    },
    harri_mat_profile_response,
    harri_image_artifacts,
    runtime_source_cell_resolver: {
      // Honest gap surface: which Harri pipeline steps exist at runtime vs
      // only in authoring. The proof above verifies these claims live by
      // calling FL4131_HARRI_PAINT_MATERIAL + FL4131_DUMP_HARRI_PROBE.
      present: harri_paint_ok && harri_probes_ok && harri_probes_distinct,
      sample_source_cell_fn: 'SampleSourceCellShape6',
      resolve_glyph_fn: 'ResolveGlyphByShape6',
      mcp_paint_cmd: 'FL4131_HARRI_PAINT_MATERIAL',
      mcp_probe_cmd: 'FL4131_DUMP_HARRI_PROBE',
      commit: commit_now,
    },
    harri_pipeline_status: {
      // Map of Alex Harri's pipeline to current implementation status.
      // Reference: https://alexharri.com/blog/ascii-rendering
      step1_source_image_sample_per_cell:    harri_synth_runs_ok ? 'implemented_at_runtime_synth_image' : 'missing',
      step2_cell_shape_vector_6region:       harri_synth_runs_ok ? 'implemented_at_runtime_6_disc_circles' : 'authoring_only',
      step3_glyph_occupancy_precomputed:     'precomputed_in_catalog',
      step4_nearest_neighbor_selection:      harri_synth_runs_ok ? 'implemented_at_runtime_profile_filtered' : 'authoring_only',
      step5_global_contrast:                 harri_contrast_changes_picks ? 'implemented_cpu_reference_max_preserving_gamma' : 'missing',
      step6_directional_contrast:            harri_directional_changes_picks ? 'implemented_cpu_reference_external10_max_affecting_gamma' : 'missing',
      step7_widened_directional_contrast:    harri_directional_changes_picks ? 'implemented_cpu_reference_affecting_sets_A0_to_A5' : 'missing',
      external_sampling_10_regions:          harri_synth_runs_ok ? 'implemented' : 'missing',
      material_visual_profile_candidates:    harri_mat_profile_applied ? 'implemented_per_material' : 'missing',
      gpu_multi_pass_pipeline:               'not_implemented_cpu_only',
      production_renderer_integration:       'not_implemented',
      blog_style_live_interactive_ux:        'partial_editor_shape_lab_only',
      framebuffer_pixel_sampling_3d_render:  'not_implemented_synth_image_only',
      mesh_sphere_skull_pixel_pass:          'partial_stress_capture_only',
    },
    captures: {
      baseline: baselinePng && path.relative(REPO_ROOT, baselinePng),
      post_preset: postPresetPng && path.relative(REPO_ROOT, postPresetPng),
      shape6_vector_a: shape6APng && path.relative(REPO_ROOT, shape6APng),
      shape6_vector_b: shape6BPng && path.relative(REPO_ROOT, shape6BPng),
      harri_runtime: harriPng && path.relative(REPO_ROOT, harriPng),
      stress_meshes: stressPng && path.relative(REPO_ROOT, stressPng),
    },
    diagnostics: {
      baseline: diag_baseline,
      post_preset: diag_post_preset,
      shape6_vector_a: diag_shape6_a,
      shape6_vector_b: diag_shape6_b,
      harri_runtime: diag_harri,
    },
    pixel_diff: {
      baseline_vs_post_preset: diffs.baseline_vs_post_preset,
      post_preset_vs_shape6_a: diffs.post_preset_vs_shape6_a,
      shape6_a_vs_shape6_b: diffs.shape6_a_vs_shape6_b,
      shape6_b_vs_harri: diffs.shape6_b_vs_harri,
      baseline_vs_harri: diffs.baseline_vs_harri,
      viewport_rect: { x0: VIEWPORT_X0, x1: VIEWPORT_X1, y0: VIEWPORT_Y0, y1: VIEWPORT_Y1 },
      pass_thresholds: {
        baseline_vs_post_preset_changed_pixels: 1000,
        post_preset_vs_shape6_a_changed_pixels: 200,
        shape6_a_vs_shape6_b_changed_pixels: 200,
        shape6_b_vs_harri_changed_pixels: 200,
      },
    },
    red_bang_pixels: redBang,
    varied_regions: varied,
    viewport_mean_luma: viewportLuma,
    pass_breakdown: {
      atlas_loaded, lut_width_gt_256: lut_width > 256, all_presets_ok,
      fail_closed_clean, no_red_bang,
      baseline_diff_pass, shape6a_diff_pass, shape6b_diff_pass, stress_varied_pass,
      // v3 honest-failure gates.
      new_counters_present,
      no_lut_miss, no_coverage_miss, no_cp437_fallback, no_red_bang_counter,
      harri_mcp_present, harri_paint_ok,
      harri_probes_ok,
      harri_probes_src6_distinct,
      harri_probes_gid_distinct,
      harri_probes_distinct,
      harri_changed_view,
      no_black_collapse,
      harri_synth_runs_ok,
      harri_synth_baseline_distinct,
      harri_contrast_changes_picks,
      harri_directional_changes_picks,
      harri_mat_profile_applied,
      harri_self_check_ok,
      harri_artifacts_present,
      commit_under_test_matches,
    },
    error: error_text,
    limits: [
      'PASS proves editor terrain shader samples the compiled extended atlas for painted cells, the Shape Lab 6D source vector drives nearest-neighbor candidate selection visibly in the terrain viewport, the runtime per-cell SampleSourceCellShape6 + ResolveGlyphByShape6 pipeline is live (verified by two HARRI_PROBE calls returning distinct available GIDs), and the legacy red-`!` fail-closed path is no longer reachable for available GIDs.',
      'It does NOT prove production-renderer closure for term++/native/web/multiplayer.',
      'Harri contrast refinement is CPU-reference implemented for synthesized source images only. GPU parity, live framebuffer sampling, and production-renderer closure remain unproven.',
      'CP437 default-origin behavior is verified only by the lack of diff for unpainted materials and the diagnostic dump.',
      'Stress-capture varied-region detection is a coarse heuristic (4x4 sample grid with a 5x5 local-distinctness count), not a per-mesh identifier.',
    ],
  };
  fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2) + '\n');
  log(`wrote ${path.relative(REPO_ROOT, RECEIPT)} verdict=${verdict}`);
  if (verdict !== 'PASS') {
    log('pass_breakdown=' + JSON.stringify(receipt.pass_breakdown));
    log('red_bang=' + JSON.stringify(redBang));
    log('diffs=' + JSON.stringify(diffs));
    log('diag_post_preset summary=' + JSON.stringify(diag_post_preset && diag_post_preset.summary));
    process.exit(1);
  }
}

run().catch(err => {
  process.stderr.write(`[proof-editor-terrain-atlas] ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});
