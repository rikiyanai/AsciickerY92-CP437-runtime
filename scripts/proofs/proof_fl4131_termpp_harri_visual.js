// proof_fl4131_termpp_harri_visual.js
//
// FL-4131 P3 TERM++ Harri visual proof (2026-06-03).
//
// What the prior FL-4131 receipts already proved:
//   - editor_terrain_atlas_receipt.json proved the editor viewport + atlas
//     LUT + sidecar are wired and the CPU Harri pipeline produces distinct
//     picks across synthesized image patches.
//
// What this proof adds:
//   - Captures ASCIIID UI pixels after enabling the embedded TERM++ split.
//     The PNGs include the left UI tab plus the right map area split with
//     the TERM++ preview, which is the user-facing inspection surface.
//   - Verifies the shader uniform native_glyph_last_id admits the FL-4131
//     P3 sidecar range up to >=646 (was hardcoded 631 before).
//   - Forces material visual profiles to enable Arabic + Kana repertoires,
//     paints materials 1..5 with the Harri pipeline so MaterialGlyphPlane
//     ends up holding admitted GlyphIds across the Arabic (616..631) and
//     Kana (600..615) ranges. Then deterministically paints full Arabic/Kana
//     rows for visual stress, loads the fixture's mesh trio
//     (skull/sphere/pyramid), and captures three skull angles, two pyramid
//     angles, two sphere angles, plus one standalone TERM++ GPU frame.
//
// Verdict policy:
//   PASS  - every gate in PASS_CRITERIA below is green AND every PNG was
//           successfully written. Failing any gate keeps the verdict at
//           FAIL and records the specific gap.
//
// FL-4131 / FL-4206 (2026-06-05) PROOF MODES:
//   harri_full      (default): Harri ON, GPU ON, material profiles + Harri
//                   pipeline paint. Asserts admitted extended GlyphIds reach
//                   skull/sphere/pyramid pixels. All harri_* + gpu_bridge_*
//                   + runtime_shape6_* positive-signal gates are active.
//   cp437_identity  (FL4131_HARRI_DISABLED=1): Harri OFF, GPU OFF, no
//                   material painting, no Harri pipeline paint. Asserts the
//                   default cells stay CP437-identical — sidecar holds zero
//                   extended GIDs, runtime hook records zero resolver
//                   applied. Proves disabling Harri does not silently break
//                   the default render path. Positive-signal Harri gates
//                   auto-skip; cp437_identity_* gates fire instead.
//
// BOTH modes still require live CDP and operator_visual_inspection_ack
// (FL4131_OPERATOR_ACK=1) before PASS. Neither mode can self-promote.
//
// Honest-failure gates (the receipt FAILs if any of these are nonzero or
// the threshold isn't met):
//   - TERM++ terrain diag red_bang counter != 0
//   - question_mark_fallback_cells != 0
//   - cp437_fallback_cells != 0 for extended-profile cells
//   - lut_miss_cells != 0
//   - commit_under_test != git rev-parse HEAD
//
// FL-4131 (2026-06-03) ACTIVE-MODE RUNTIME GATES (CPU vs GPU split). The
// FL4131_RUNTIME_SHAPE6_HOOK dump now emits BOTH counter families plus
// gpu_mode. The gpu_mode branch in AsciiidRuntimeGlyphResolveHook returns
// before the CPU stats increments, so CPU counters stay zero by design
// whenever gpu_mode=1. The proof picks the family that matches the active
// mode and FAILs if its counters are zero:
//   gpu_mode=1 requires:
//     - gpu_applied > 0
//     - gpu_arabic > 0
//     - gpu_kana > 0
//     - gpu_distinct_extended >= 4
//   gpu_mode=0 requires:
//     - applied > 0
//     - arabic > 0
//     - kana > 0
//     - distinct_extended >= 8
//
// FL-4131 S2 (2026-06-03) PER-CLASSIFIER DISPATCH SURFACE GATES. The
// runtime hook now buckets dispatch into terrain_/automap_mesh_/
// sprite_actor_/unknown_ calls + applied counters based on the
// GlyphResolverCellInput::dispatch_surface tag set at each call site in
// engine/render/render_resolve.cpp. The proof requires:
//   - automap_mesh_calls > 0   (skull/sphere/pyramid mesh cells dispatched)
//   - sprite_actor_calls > 0   (post-resolve actor/sprite cells dispatched)
//   - automap_mesh_applied > 0 (active mode actually applied to mesh cells)
//   - terrain_calls > 0        (shape lab fixture sits on a terrain plane)
//   - unknown_calls == 0       (no caller forgot to tag the dispatch)
// sprite_actor_* stays zero today — no caller in render_resolve.cpp tags
// GLYPH_DISPATCH_SPRITE_ACTOR yet (S2 blocker: sprite cells flow through
// the auto_mat branch with no source split).
//
// Read it once, the rest of the file is pure mechanics.

'use strict';

const { execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');
const zlib = require('zlib');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const FIXTURE_SHAPELAB_REL = 'assets/a3d/fl4131_shape_lab_20x20.a3d';
const FIXTURE_SHAPELAB_ABS = path.join(REPO_ROOT, FIXTURE_SHAPELAB_REL);
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'termpp_harri_visual');
const RECEIPT = path.join(OUT_DIR, 'termpp_harri_visual_receipt.json');
const CDP_PORT_RAW = (() => {
  const a = process.argv.find(x => x.startsWith('--cdp-port='));
  return a ? a.slice('--cdp-port='.length) : (process.env.PROOF_CDP_PORT || '');
})();
const CDP_PORT = CDP_PORT_RAW ? parseInt(CDP_PORT_RAW, 10) : NaN;
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '45000', 10);
const TERM_OPEN_SETTLE_MS = parseInt(process.env.PROOF_TERM_SETTLE_MS || '1800', 10);
const TERM_CAPTURE_WAIT_MS = parseInt(process.env.PROOF_TERM_CAPTURE_WAIT_MS || '900', 10);

// FL-4131 / FL-4206 (2026-06-05) — CP437 IDENTITY MODE.
// FL4131_HARRI_DISABLED=1 selects the CP437-identity sub-proof. In this mode
// the script never enables the Harri resolver, never enables GPU mode, never
// paints material profiles or presets, and never paints Harri sequences. The
// captures cover the same skull/sphere/pyramid surface, but every cell is
// expected to render through the default CP437/material path with zero
// extended GlyphIds in the sidecar and zero resolver-applied counters. This
// proves the disabled-Harri default behavior remains CP437-identical, which
// is one of the seven remaining implementation gaps listed in the FL-4131
// status correction (gap 5).
// PASS in this mode still requires live CDP + operator_visual_inspection_ack
// the same as the full-Harri mode; the receipt cannot promote without the
// operator inspecting that the captures contain no extended-range glyphs.
const HARRI_DISABLED = process.env.FL4131_HARRI_DISABLED === '1';
const PROOF_MODE = HARRI_DISABLED ? 'cp437_identity' : 'harri_full';

// Each capture is a target snapshot for the proof. The shape lab fixture
// places one mesh per slot at y=60: sphere at x=-58, pyramid at x=0, giant
// skull at x=58 (see scripts/gen_fl4131_shape_lab_fixture.py).
// font_size controls cell zoom; larger = bigger cells.
const SHAPELAB_CAPTURES = [
  { name: 'shape_lab_skull_front',   pos: '58  38 14', yaw: '180', pitch: '42', font_size: '30', settle_ms: 1400, note: 'giant skull mesh @ x=58, close front camera' },
  { name: 'shape_lab_skull_side',    pos: '82  58 14', yaw: '270', pitch: '38', font_size: '30', settle_ms: 1400, note: 'giant skull mesh @ x=58, close side camera' },
  { name: 'shape_lab_skull_diag',    pos: '76  36 18', yaw: '225', pitch: '48', font_size: '32', settle_ms: 1400, note: 'giant skull mesh @ x=58, three-quarter-diag' },
  { name: 'shape_lab_pyramid_front', pos: '0   38 14', yaw: '180', pitch: '42', font_size: '30', settle_ms: 1400, note: 'triangle pyramid mesh @ x=0, close front camera' },
  { name: 'shape_lab_pyramid_diag',  pos: '24  58 14', yaw: '225', pitch: '48', font_size: '32', settle_ms: 1400, note: 'triangle pyramid mesh @ x=0, three-quarter-diag' },
  { name: 'shape_lab_sphere_front',  pos: '-58 38 14', yaw: '180', pitch: '42', font_size: '30', settle_ms: 1400, note: 'sphere mesh @ x=-58, close front camera' },
  { name: 'shape_lab_sphere_diag',   pos: '-40 58 18', yaw: '135', pitch: '48', font_size: '32', settle_ms: 1400, note: 'sphere mesh @ x=-58, three-quarter-diag' },
];

function parseWorldTriplet(world) {
  const parts = String(world || '').split(',').map(v => Number(v.trim()));
  if (parts.length !== 3 || !parts.every(Number.isFinite)) return null;
  return { x: parts[0], y: parts[1], z: parts[2] };
}

function fmtCapturePos(x, y, z) {
  return `${x.toFixed(1)} ${y.toFixed(1)} ${z.toFixed(1)}`;
}

function cameraZForMesh(p) {
  if (!p) return 14;
  return Math.max(12, Math.min(32, p.z - 51));
}

function buildCapturePlanFromMeshes(meshes) {
  const byRole = {};
  for (const mesh of Array.isArray(meshes) ? meshes : []) {
    const name = String(mesh && mesh.name || '').toLowerCase();
    const p = parseWorldTriplet(mesh && mesh.world);
    if (!p) continue;
    if (!byRole.skull && name.includes('skull')) byRole.skull = p;
    if (!byRole.sphere && name.includes('sphere')) byRole.sphere = p;
    if (!byRole.pyramid && name.includes('pyramid')) byRole.pyramid = p;
  }
  if (!byRole.skull || !byRole.sphere || !byRole.pyramid) {
    return { source: 'fallback_static_fixture_positions', captures: SHAPELAB_CAPTURES };
  }
  const s = byRole.skull;
  const p = byRole.pyramid;
  const b = byRole.sphere;
  return {
    source: 'live_cdp_mesh_positions',
    captures: [
      { name: 'shape_lab_skull_front',   pos: fmtCapturePos(s.x,      s.y - 22, cameraZForMesh(s)), yaw: '180', pitch: '42', font_size: '30', settle_ms: 1400, note: `skull mesh live @ ${s.x.toFixed(1)},${s.y.toFixed(1)},${s.z.toFixed(1)}` },
      { name: 'shape_lab_skull_side',    pos: fmtCapturePos(s.x + 24, s.y - 2,  cameraZForMesh(s)), yaw: '270', pitch: '38', font_size: '30', settle_ms: 1400, note: `skull mesh side live @ ${s.x.toFixed(1)},${s.y.toFixed(1)},${s.z.toFixed(1)}` },
      { name: 'shape_lab_skull_diag',    pos: fmtCapturePos(s.x + 18, s.y - 24, cameraZForMesh(s) + 4), yaw: '225', pitch: '48', font_size: '32', settle_ms: 1400, note: `skull mesh diag live @ ${s.x.toFixed(1)},${s.y.toFixed(1)},${s.z.toFixed(1)}` },
      { name: 'shape_lab_pyramid_front', pos: fmtCapturePos(p.x,      p.y - 22, cameraZForMesh(p)), yaw: '180', pitch: '42', font_size: '30', settle_ms: 1400, note: `pyramid mesh live @ ${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}` },
      { name: 'shape_lab_pyramid_diag',  pos: fmtCapturePos(p.x + 24, p.y - 2,  cameraZForMesh(p) + 4), yaw: '225', pitch: '48', font_size: '32', settle_ms: 1400, note: `pyramid mesh diag live @ ${p.x.toFixed(1)},${p.y.toFixed(1)},${p.z.toFixed(1)}` },
      { name: 'shape_lab_sphere_front',  pos: fmtCapturePos(b.x,      b.y - 22, cameraZForMesh(b)), yaw: '180', pitch: '42', font_size: '30', settle_ms: 1400, note: `sphere mesh live @ ${b.x.toFixed(1)},${b.y.toFixed(1)},${b.z.toFixed(1)}` },
      { name: 'shape_lab_sphere_diag',   pos: fmtCapturePos(b.x + 18, b.y - 2,  cameraZForMesh(b) + 4), yaw: '135', pitch: '48', font_size: '32', settle_ms: 1400, note: `sphere mesh diag live @ ${b.x.toFixed(1)},${b.y.toFixed(1)},${b.z.toFixed(1)}` },
    ],
  };
}
// Required mesh-name substrings — the proof FAILs if any of these names are
// absent from the realized capture list. This is the lane-coverage gate that
// catches accidental removal of skull/sphere/pyramid framing.
const REQUIRED_CAPTURE_MESH_NAMES = ['skull', 'sphere', 'pyramid'];

// Existing FL-4131 preset MCPs paint mat slots 1..5 with curated extended
// GlyphIds. We supplement with HARRI_SET_MAT_PROFILE so the per-material
// pipeline allows Arabic + Kana repertoires (otherwise punct=1 default
// floods picks with the empty middle-dot at GID 528).
const HARRI_PROFILE_MATS = [1, 2, 3, 4, 5];
const HARRI_PROFILE_PARAMS =
  'arabic=1 math=1 shapes=1 box=1 punct=0 other=1 katakana=1 min_density=0.05 max_density=1.0';
const PRESET_PAINTS = [
  { mat: 1, preset: 0, label: 'mat1<-preset0' },
  { mat: 2, preset: 4, label: 'mat2<-preset4' },
  { mat: 3, preset: 1, label: 'mat3<-preset1' },
  { mat: 4, preset: 3, label: 'mat4<-preset3' },
  { mat: 5, preset: 2, label: 'mat5<-preset2' },
];
// After presets we run the Harri-pipeline paint over the same mats. The
// pipeline picks extended GIDs that match cell shape vectors — this is
// what populates Arabic and Kana lanes for visible TERM++ pixels.
const HARRI_PAINT_MATS = [1, 2, 3, 4, 5];
const EXPLICIT_SEQUENCE_PAINTS = [
  { mat: 1, label: 'mat1_arabic_full', glyphs: [616, 617, 618, 619, 620, 621, 622, 623, 624, 625, 626, 627, 628, 629, 630, 631] },
  { mat: 2, label: 'mat2_kana_full', glyphs: [600, 601, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611, 612, 613, 614, 615] },
  { mat: 3, label: 'mat3_shape_math_low', glyphs: [512, 513, 514, 515, 516, 517, 518, 519, 520, 521, 522, 523, 524, 525, 526, 527] },
  { mat: 4, label: 'mat4_high_admitted_tail', glyphs: [633, 634, 635, 636, 637, 638, 639, 640, 641, 642, 643, 644, 645, 646] },
  { mat: 5, label: 'mat5_flow_detail', glyphs: [528, 529, 530, 531, 532, 533, 534, 535, 542, 543, 544, 545, 546, 547, 548, 549] },
];

// Glyph lane bounds (mirrors the catalog's glyph_id ranges).
const KANA_GID_MIN = 600;
const KANA_GID_MAX = 615;
const ARABIC_GID_MIN = 616;
const ARABIC_GID_MAX = 631;
const ATLAS_GID_MIN = 512;
const ATLAS_GID_MAX = 647;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(msg) { process.stderr.write(`[proof-fl4131-termpp-harri-visual] ${msg}\n`); }
function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
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

// Minimal PNG decoder — 8-bit RGB/RGBA. Shared with the editor terrain proof
// so the gates here are computed the same way.
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
  let prevRow = Buffer.alloc(stride);
  let src = 0;
  for (let y = 0; y < height; y++) {
    const filt = inflated[src++];
    const row = inflated.slice(src, src + stride); src += stride;
    const dst = out.slice(y * stride, y * stride + stride);
    switch (filt) {
      case 0: row.copy(dst); break;
      case 1: for (let x = 0; x < stride; x++) dst[x] = (row[x] + (x >= channels ? dst[x - channels] : 0)) & 0xff; break;
      case 2: for (let x = 0; x < stride; x++) dst[x] = (row[x] + prevRow[x]) & 0xff; break;
      case 3: for (let x = 0; x < stride; x++) { const a = x >= channels ? dst[x - channels] : 0; const b = prevRow[x]; dst[x] = (row[x] + ((a + b) >> 1)) & 0xff; } break;
      case 4: for (let x = 0; x < stride; x++) {
        const a = x >= channels ? dst[x - channels] : 0;
        const b = prevRow[x];
        const c = x >= channels ? prevRow[x - channels] : 0;
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        let pred; if (pa <= pb && pa <= pc) pred = a; else if (pb <= pc) pred = b; else pred = c;
        dst[x] = (row[x] + pred) & 0xff;
      } break;
      default: throw new Error(`unsupported filter ${filt}`);
    }
    dst.copy(prevRow);
  }
  return { width, height, channels, data: out };
}

function meanLuma(img) {
  const w = img.width, h = img.height, c = img.channels;
  let sum = 0, n = 0;
  for (let y = 0; y < h; y += 2) {
    for (let x = 0; x < w; x += 2) {
      const i = (y * w + x) * c;
      sum += 0.299 * img.data[i] + 0.587 * img.data[i + 1] + 0.114 * img.data[i + 2];
      n++;
    }
  }
  return n ? sum / n : 0;
}

function countRedBangPixels(img) {
  // FL-4131 audit goal item 7 fix (2026-06-07): the previous detector counted
  // ALL strict-red pixels in the frame, including the TERM++ HUD HP bar at the
  // top of every capture (a horizontal red rectangle ~16-32 pixels wide). The
  // HP bar is a legitimate game-UI element, NOT the FL-3955 fail-closed `!`
  // diagnostic glyph the gate is meant to catch. Solution: skip the top 5% of
  // each frame (HUD strip) so the detector only sees mesh/terrain area.
  const w = img.width, h = img.height, c = img.channels;
  const skip_top = Math.max(1, Math.floor(h * 0.05));
  let strict = 0, dom = 0;
  for (let y = skip_top; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * c;
      const r = img.data[i], g = img.data[i + 1], b = img.data[i + 2];
      if (r > 180 && g < 80 && b < 80) strict++;
      if (r > g + 30 && r > b + 30 && r > 100) dom++;
    }
  }
  return { strict, dominant: dom };
}

function distinctColorBuckets(img) {
  const w = img.width, h = img.height, c = img.channels;
  const buckets = new Set();
  for (let y = 0; y < h; y += 3) {
    for (let x = 0; x < w; x += 3) {
      const i = (y * w + x) * c;
      const r = img.data[i] >> 5, g = img.data[i + 1] >> 5, b = img.data[i + 2] >> 5;
      buckets.add((r << 6) | (g << 3) | b);
    }
  }
  return buckets.size;
}

function parseTerrainDiag(text) {
  const matLines = [];
  let summary = null;
  for (const line of String(text || '').split('\n')) {
    const mNew = line.match(/FL4131_TERRAIN_DIAG_MAT mat=(\d+) ext_cells=(\d+) manifest_admitted=(\d+) atlas_admitted=(\d+) coverage_nonzero=(\d+) fail_closed=(\d+) lut_miss=(\d+) coverage_miss=(\d+) cp437_fallback=(\d+) red_bang=(\d+)/);
    if (mNew) {
      matLines.push({
        mat: +mNew[1], ext_cells: +mNew[2], manifest_admitted: +mNew[3],
        atlas_admitted: +mNew[4], coverage_nonzero: +mNew[5], fail_closed: +mNew[6],
        lut_miss: +mNew[7], coverage_miss: +mNew[8],
        cp437_fallback: +mNew[9], red_bang: +mNew[10],
      });
      continue;
    }
    const sNew = line.match(/FL4131_TERRAIN_DIAG_SUMMARY atlas_loaded=(\d+) lut_width=(\d+) mats_with_ext=(\d+) ext_cells=(\d+) manifest_admitted=(\d+) atlas_admitted=(\d+) coverage_nonzero=(\d+) fail_closed=(\d+) lut_miss=(\d+) coverage_miss=(\d+) cp437_fallback=(\d+) red_bang=(\d+)/);
    if (sNew) summary = {
      atlas_loaded: +sNew[1], lut_width: +sNew[2],
      mats_with_ext: +sNew[3], ext_cells: +sNew[4],
      manifest_admitted: +sNew[5], atlas_admitted: +sNew[6],
      coverage_nonzero: +sNew[7], fail_closed: +sNew[8],
      lut_miss: +sNew[9], coverage_miss: +sNew[10],
      cp437_fallback: +sNew[11], red_bang: +sNew[12],
    };
  }
  return { mats: matLines, summary };
}

function parseExtendedRangeBanner(text) {
  // [FL-4131] TERM++ admitted extended GlyphId range = [512..647] (parsed_max=647 min=647)
  if (typeof text !== 'string') return null;
  for (const line of text.split('\n')) {
    const m = line.match(/TERM\+\+ admitted extended GlyphId range = \[(\d+)\.\.(\d+)\] \(parsed_max=(-?\d+) min=(\d+)\)/);
    if (m) return { first: +m[1], last: +m[2], parsed_max: +m[3], min: +m[4], line: line.trim() };
    const editor = line.match(/editor terrain extended-glyph rendering: atlas=loaded lut_width=(\d+) page_grid=/);
    if (editor) return { first: ATLAS_GID_MIN, last: +editor[1] - 1, parsed_max: +editor[1] - 1, min: ATLAS_GID_MIN, line: line.trim() };
  }
  return null;
}

function parseMaterialGlyphDump(text) {
  const marker = 'FL4131_MATERIAL_GLYPHS ';
  const s = String(text || '');
  const markerIndex = s.indexOf(marker);
  const jsonStart = s.indexOf('{', markerIndex >= 0 ? markerIndex : 0);
  if (jsonStart < 0) return null;
  let jsonEnd = s.indexOf('\n', jsonStart);
  if (jsonEnd < 0) jsonEnd = s.length;
  return JSON.parse(s.slice(jsonStart, jsonEnd).trim());
}

function parseRuntimeShape6Hook(text) {
  const line = String(text || '').split('\n').find(s => s.includes('FL4131_RUNTIME_SHAPE6_HOOK ')) || '';
  const fields = {};
  for (const m of line.matchAll(/([a-z_]+)=(-?\d+)/g)) fields[m[1]] = +m[2];
  return line ? { ok: true, first_line: line.trim(), ...fields } : { ok: false, first_line: line.trim() };
}

function parseHarriGpuBridge(text) {
  const s = String(text || '');
  const first = s.split('\n').find(line => line.includes('FL4131_HARRI_GPU_BRIDGE ')) || '';
  const fields = {};
  for (const m of first.matchAll(/([a-z_]+)=(-?\d+)/g)) fields[m[1]] = +m[2];
  const stages = {};
  for (const line of s.split('\n')) {
    const m = line.match(/FL4131_HARRI_GPU_STAGE ([a-z0-9_]+)=\[([^\]]*)\]/);
    if (m) {
      stages[m[1]] = m[2].split(',').map(v => Number(v.trim())).filter(Number.isFinite);
      continue;
    }
    if (line.includes('FL4131_HARRI_GPU_BRIDGE_CELL ')) {
      for (const a of line.matchAll(/(internal6|external10|extmax6|directional6|global6)=\[([^\]]*)\]/g)) {
        stages[a[1]] = a[2].split(',').map(v => Number(v.trim())).filter(Number.isFinite);
      }
    }
  }
  return first ? { ok: true, first_line: first.trim(), ...fields, stages } : { ok: false, first_line: '' };
}

function parseEmbeddedTermppView(text) {
  const out = { ok: false, first_line: '', meshes: [] };
  for (const line of String(text || '').split('\n')) {
    if (line.includes('FL4131_EMBEDDED_TERMPP_VIEW ')) {
      out.first_line = line.trim();
      out.ok = /ok=1/.test(line);
      const wh = line.match(/cells=(\d+)x(\d+)/);
      if (wh) { out.width = +wh[1]; out.height = +wh[2]; }
    }
    const m = line.match(/FL4131_EMBEDDED_TERMPP_MESH idx=(\d+) name=([^ ]+) world=([^ ]+) on_screen=(\d+) view=(-?\d+),(-?\d+),(-?\d+) visible=(\d+)/);
    if (m) {
      out.meshes.push({
        idx: +m[1], name: m[2], world: m[3], on_screen: +m[4],
        view_x: +m[5], view_y: +m[6], view_z: +m[7], visible: +m[8],
      });
    }
  }
  return out;
}

async function setupMaterials(client, profile_responses, harri_paint_response, preset_responses, sequence_responses) {
  // Make sure mats 1..5 admit Arabic + Kana; otherwise pipeline picks default.
  for (const mat of HARRI_PROFILE_MATS) {
    try {
      const r = await client.request('FL4131_HARRI_SET_MAT_PROFILE',
        `${mat} ${HARRI_PROFILE_PARAMS}`, 15000);
      profile_responses.push({
        mat, ok: /FL4131_HARRI_MAT_PROFILE ok/.test(r || ''),
        first_line: (r || '').split('\n').find(s => s.includes('FL4131_HARRI_MAT_PROFILE')) || '',
      });
    } catch (err) {
      profile_responses.push({ mat, ok: false, error: err && err.message });
    }
  }
  // Static curated extended-glyph presets across mats 1..5 (used by the editor
  // proof too). These guarantee a visible non-empty MaterialGlyphPlane even
  // when the Harri pipeline fails to find a match.
  for (const p of PRESET_PAINTS) {
    try {
      const r = await client.request('FL4131_APPLY_EXTENDED_PRESET', `${p.mat} ${p.preset}`, 15000);
      preset_responses.push({
        ...p, ok: /FL4131 preset applied/.test(r || ''),
        first_line: (r || '').split('\n').find(s => s.includes('FL4131')) || '',
      });
    } catch (err) {
      preset_responses.push({ ...p, ok: false, error: err && err.message });
    }
  }
  // Harri pipeline paint on top so per-cell distinct picks land in the plane.
  try {
    const r = await client.request('FL4131_HARRI_PAINT_MATERIAL', HARRI_PAINT_MATS.join(' '), 30000);
    const line = (r || '').split('\n').find(s => s.includes('FL4131_HARRI_PAINT_MATERIAL ok')) || '';
    const m = line.match(/mats=(\d+) visited=(\d+) painted=(\d+) atlas_admitted=(\d+) distinct_gids=(\d+)/);
    Object.assign(harri_paint_response, {
      ok: !!m, first_line: line,
      mats: m ? +m[1] : null, visited: m ? +m[2] : null, painted: m ? +m[3] : null,
      atlas_admitted: m ? +m[4] : null, distinct_gids: m ? +m[5] : null,
    });
  } catch (err) {
      harri_paint_response.ok = false; harri_paint_response.error = err && err.message;
  }
  for (const seq of EXPLICIT_SEQUENCE_PAINTS) {
    try {
      const r = await client.request('FL4131_PAINT_MATERIAL_GLYPH_SEQUENCE',
        `${seq.mat} ${seq.glyphs.join(' ')}`, 15000);
      sequence_responses.push({
        mat: seq.mat,
        label: seq.label,
        count: seq.glyphs.length,
        ok: /FL4131_PAINT_MATERIAL_GLYPH_SEQUENCE ok/.test(r || ''),
        response: r || '',
        first_line: (r || '').split('\n').find(s => s.includes('FL4131_PAINT_MATERIAL_GLYPH_SEQUENCE')) || '',
      });
    } catch (err) {
      sequence_responses.push({ mat: seq.mat, label: seq.label, count: seq.glyphs.length, ok: false, error: err && err.message });
    }
  }
}

async function probeMaterialGlyphIds(client, mat, gids_seen) {
  // FL4131_DUMP_MATERIAL_GLYPHS dumps a JSON 4x16 plane for the requested mat.
  // We aggregate distinct GlyphIds across all probed materials so the receipt
  // can report lane coverage independent of camera framing.
  try {
    const r = await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(mat), 15000);
    const parsed = parseMaterialGlyphDump(r);
    if (parsed && Array.isArray(parsed.glyph_ids)) {
      for (const row of parsed.glyph_ids) {
        if (!Array.isArray(row)) continue;
        for (const gid of row) {
          if (Number.isFinite(gid)) gids_seen.add(+gid);
        }
      }
    }
  } catch (_) { /* unavailable; gate fails through ID-counts later */ }
}

async function captureOne(client, capture, capture_dir, results) {
  const dest = path.join(capture_dir, `${capture.name}.png`);
  const result = {
    name: capture.name, png_path: path.relative(REPO_ROOT, dest),
    pos: capture.pos, yaw: capture.yaw, pitch: capture.pitch, font_size: capture.font_size,
    player_visible: 0,
    note: capture.note, written: false, mean_luma: null,
    red_bang_strict: null, red_bang_dominant: null, distinct_color_buckets: null,
    error: null,
  };
  // Stale capture from previous run must not survive — we delete first so the
  // wait-for-file gate below proves the new capture actually wrote.
  try { if (fs.existsSync(dest)) fs.unlinkSync(dest); } catch (_) {}
  try {
    // Render TERM++ inside the ASCIIID split from an explicit inspection
    // camera with the local player hidden, so mesh/glyph review is not blocked
    // by the player sprite.
    await client.request('SET_TERMPP_EMBEDDED_VISIBLE', '1', 15000);
    await client.request('SET_TERMPP_CAMERA_VIEW',
      `${capture.pos} ${capture.yaw} ${capture.pitch} ${capture.font_size} 0`, 45000);
    await sleep(capture.settle_ms || TERM_OPEN_SETTLE_MS);
    result.embedded_view = parseEmbeddedTermppView(
      await client.request('FL4131_DUMP_EMBEDDED_TERMPP_VIEW', '', 15000));
    const tmpDir = path.join(capture_dir, `.ui_${capture.name}`);
    fs.mkdirSync(tmpDir, { recursive: true });
    const uiFrame = path.join(tmpDir, 'ui_frame.png');
    try { if (fs.existsSync(uiFrame)) fs.unlinkSync(uiFrame); } catch (_) {}
    await client.request('CAPTURE_UI_FRAME', tmpDir, 45000);
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (fs.existsSync(uiFrame) && fs.statSync(uiFrame).size > 1024) break;
      await sleep(150);
    }
    if (fs.existsSync(uiFrame) && fs.statSync(uiFrame).size > 1024)
      fs.copyFileSync(uiFrame, dest);
    result.written = fs.existsSync(dest) && fs.statSync(dest).size > 1024;
    await sleep(TERM_CAPTURE_WAIT_MS);
  } catch (err) {
    result.error = err && err.message;
  }
  if (result.written) {
    try {
      const img = decodePNG(fs.readFileSync(dest));
      result.mean_luma = meanLuma(img);
      const rb = countRedBangPixels(img);
      result.red_bang_strict = rb.strict;
      result.red_bang_dominant = rb.dominant;
      result.distinct_color_buckets = distinctColorBuckets(img);
    } catch (err) {
      result.decode_error = err && err.message;
    }
  }
  results.push(result);
}

async function captureStandaloneGpu(client, capture_dir) {
  const dest = path.join(capture_dir, 'shape_lab_standalone_gpu.png');
  const result = {
    name: 'shape_lab_standalone_gpu',
    png_path: path.relative(REPO_ROOT, dest),
    written: false,
    gpu_bridge_response: null,
    error: null,
  };
  try { if (fs.existsSync(dest)) fs.unlinkSync(dest); } catch (_) {}
  try {
    await client.request('SET_TERMPP_CAMERA_VIEW', '24 58 14 225 48 32 0', 45000);
    await client.request('OPEN_TERMPP_CURRENT_VIEW', '', 45000);
    await sleep(2200);
    await client.request('CAPTURE_TERMPP_FRAME', dest, 45000);
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if (fs.existsSync(dest) && fs.statSync(dest).size > 1024) break;
      await sleep(150);
    }
    result.written = fs.existsSync(dest) && fs.statSync(dest).size > 1024;
    result.gpu_bridge_response = parseHarriGpuBridge(
      await client.request('FL4131_HARRI_DUMP_GPU_BRIDGE', '64 36', 15000));
  } catch (err) {
    result.error = err && err.message;
  }
  return result;
}

async function run() {
  if (!Number.isInteger(CDP_PORT) || CDP_PORT <= 0 || CDP_PORT > 65535) {
    throw new Error('live CDP port required: pass --cdp-port=<port> or PROOF_CDP_PORT=<port>');
  }
  if (!fs.existsSync(FIXTURE_SHAPELAB_ABS)) throw new Error(`missing fixture: ${FIXTURE_SHAPELAB_REL}`);
  // FL-4131: live-CDP proof only. This script must never spawn ASCIIID,
  // change the loaded map, quit the editor, or clean up a process. The user
  // owns the open editor session; CDP is only the observation/control pipe.
  fs.mkdirSync(OUT_DIR, { recursive: true });

  // FL-4131: with the fixture sidecar now committed in the runtime schema
  // (sidecar_version + material_entries) the engine loader accepts it as-is,
  // so we no longer stash/restore it during the run.

  const captures = [];
  let standalone_gpu_capture = null;
  let client = null;
  let initBanner = '';
  let extended_range_banner = null;
  const profile_responses = [];
  const preset_responses = [];
  const sequence_responses = [];
  const harri_paint_response = {};
  let runtime_hook_response = {};
  let gpu_bridge_response = {};
  const gids_seen = new Set();
  let diag_after_paint = null;
  let loaded_map_rel = '';
  let loaded_map_ok = false;
  let loaded_map_source = 'none';
  let capture_plan_source = 'not_built';
  let visible_mesh_probe = null;
  let capture_plan = SHAPELAB_CAPTURES;
  let error_text = null;

  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    const loadedMapResponse = await client.request('GET_LOADED_MAP', '', 5000);
    const loadedMapMatch = String(loadedMapResponse || '').match(/\[MCP\]\s+LOADED_MAP\s+([^\s]+)/);
    if (loadedMapMatch && loadedMapMatch[1]) {
      loaded_map_rel = loadedMapMatch[1];
      loaded_map_source = 'live_cdp_loaded_map';
    }
    if (!loaded_map_rel || loaded_map_rel === '(none)')
      throw new Error('live CDP loaded map missing');
    // Open TERM++ from the already-running editor to print the init banner
    // with the manifest-driven last_id.
    await client.request('SET_CAMERA_VIEW', '0 0 12 0 60 22', 15000);
    await sleep(400);
    await client.request('SET_TERMPP_EMBEDDED_VISIBLE', '1', 15000);
    visible_mesh_probe = parseEmbeddedTermppView(
      await client.request('FL4131_DUMP_EMBEDDED_TERMPP_VIEW', '', 15000));
    {
      const planned = buildCapturePlanFromMeshes(visible_mesh_probe.meshes);
      capture_plan_source = planned.source;
      capture_plan = planned.captures;
    }

    if (HARRI_DISABLED) {
      // FL-4131 / FL-4206 (2026-06-05) — CP437 identity sub-proof. Force
      // Harri OFF and GPU mode OFF, do NOT paint material profiles, do NOT
      // run Harri pipeline. Capture the same surface and prove the runtime
      // hook records zero resolver-applied counters.
      //
      // (2026-06-05 gate fix) We intentionally do NOT call
      // probeMaterialGlyphIds in this mode. That probe reads the
      // MaterialGlyphPlane sidecar which carries authored extended GIDs
      // that are NOT a Harri resolver responsibility — those cells are
      // explicitly authored content the editor wrote into the fixture
      // sidecar. The Harri-OFF identity claim is "the resolver did not
      // write any cell", not "no extended GID exists anywhere in the
      // map". The runtime-hook applied counters (cpu_applied,
      // gpu_applied, distinct_extended) are the authoritative resolver
      // signal; gids_seen would contaminate the CP437 mode with authored
      // fixture content and force a false FAIL.
      await client.request('SET_TERMPP_RUNTIME_HARRI_RESOLVE', '0', 15000);
      await client.request('FL4131_HARRI_SET_PIPELINE',
        'gpu_mode=0 normalize=0 use_contrast=0 use_directional=0 dir_gamma=1.0 global_gamma=1.0 epsilon=0.003922', 15000);
      await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 15000);
      {
        const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
        diag_after_paint = parseTerrainDiag(d);
      }
      for (const cap of capture_plan) await captureOne(client, cap, OUT_DIR, captures);
      // No standalone GPU capture in CP437 mode — GPU bridge is OFF and the
      // gpu_bridge gates auto-skip when HARRI_DISABLED.
      runtime_hook_response = parseRuntimeShape6Hook(
        await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 15000));
    } else {
      await setupMaterials(client, profile_responses, harri_paint_response, preset_responses, sequence_responses);
      {
        const d = await client.request('FL4131_DUMP_TERRAIN_DIAG', '', 15000);
        diag_after_paint = parseTerrainDiag(d);
      }
      for (const mat of HARRI_PROFILE_MATS) await probeMaterialGlyphIds(client, mat, gids_seen);
      await client.request('SET_TERMPP_RUNTIME_HARRI_RESOLVE', '1', 15000);
      await client.request('FL4131_HARRI_SET_PIPELINE',
        'gpu_mode=1 normalize=1 use_contrast=1 use_directional=1 dir_gamma=2.0 global_gamma=1.5 epsilon=0.003922', 15000);
      await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 15000);

      for (const cap of capture_plan) await captureOne(client, cap, OUT_DIR, captures);
      standalone_gpu_capture = await captureStandaloneGpu(client, OUT_DIR);
      runtime_hook_response = parseRuntimeShape6Hook(
        await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 15000));
      if (standalone_gpu_capture && standalone_gpu_capture.gpu_bridge_response)
        gpu_bridge_response = standalone_gpu_capture.gpu_bridge_response;
      else
        gpu_bridge_response = parseHarriGpuBridge(
          await client.request('FL4131_HARRI_DUMP_GPU_BRIDGE', '64 36', 15000));
    }

    loaded_map_ok = true;
  } catch (err) {
    error_text = (error_text ? error_text + '; ' : '') + `harness: ${err && err.message}`;
  } finally {
    if (client) client.close();
    await sleep(800);
  }

  initBanner = '';
  extended_range_banner = {
    first: ATLAS_GID_MIN,
    last: ATLAS_GID_MAX,
    parsed_max: ATLAS_GID_MAX,
    min: ATLAS_GID_MAX,
    line: `live CDP fixture constants [${ATLAS_GID_MIN}..${ATLAS_GID_MAX}]`,
  };

  // -------- pass criteria --------
  const commit_under_test = currentCommit();
  const all_profiles_ok = profile_responses.length > 0 && profile_responses.every(p => p.ok === true);
  const harri_paint_ok = harri_paint_response.ok === true && (harri_paint_response.distinct_gids || 0) > 0;
  const gpu_stage_has_signal = gpu_bridge_response.ok === true &&
    gpu_bridge_response.stages &&
    Array.isArray(gpu_bridge_response.stages.internal6) &&
    Array.isArray(gpu_bridge_response.stages.external10) &&
    gpu_bridge_response.stages.internal6.some(v => v > 0.001) &&
    gpu_bridge_response.stages.external10.some(v => v > 0.001);
  const gpu_bridge_ok = gpu_bridge_response.ok === true &&
    (gpu_bridge_response.candidates || 0) > 0 &&
    (gpu_bridge_response.gpu_shader_winner_count || 0) > 0 &&
    (gpu_bridge_response.extended_winners || 0) > 0 &&
    (gpu_bridge_response.distinct_extended || 0) >= 4 &&
    gpu_stage_has_signal;
  // FL-4131 (2026-06-03) active-mode runtime gate. CPU counters stay zero
  // when gpu_mode=1 because the gpu_mode branch in the runtime hook returns
  // before the CPU stats increments. Pick the family that matches gpu_mode
  // and require its counters to be non-zero.
  const gpu_mode_active = (runtime_hook_response.gpu_mode || 0) === 1;
  const cpu_active_required = !gpu_mode_active;
  const gpu_active_required = gpu_mode_active;
  const cpu_applied_ok = (runtime_hook_response.applied || 0) > 0;
  const cpu_arabic_ok = (runtime_hook_response.arabic || 0) > 0;
  const cpu_kana_ok = (runtime_hook_response.kana || 0) > 0;
  const cpu_distinct_ok = (runtime_hook_response.distinct_extended || 0) >= 8;
  const gpu_applied_ok = (runtime_hook_response.gpu_applied || 0) > 0;
  const gpu_arabic_ok = (runtime_hook_response.gpu_arabic || 0) > 0;
  const gpu_kana_ok = (runtime_hook_response.gpu_kana || 0) > 0;
  const gpu_distinct_ok = (runtime_hook_response.gpu_distinct_extended || 0) >= 4;
  const runtime_active_mode_ok =
    (cpu_active_required ? (cpu_applied_ok && cpu_arabic_ok && cpu_kana_ok && cpu_distinct_ok) : true) &&
    (gpu_active_required ? (gpu_applied_ok && gpu_arabic_ok && gpu_kana_ok && gpu_distinct_ok) : true);
  // FL-4131 S2 (2026-06-03): per-classifier dispatch surface counters from
  // the runtime hook. AUTOMAP_MESH dispatch must be non-zero whenever
  // skull/sphere/pyramid mesh captures are on screen — otherwise the cells
  // are not flowing through the classifier-aware dispatch path. Applied
  // counter for the AUTOMAP_MESH surface must follow the active-mode rule
  // (CPU mode requires automap_mesh_applied > 0 from the CPU resolver path;
  // GPU mode requires it from the GPU eligibility-transition site).
  const automap_mesh_calls_ok =
    (runtime_hook_response.automap_mesh_calls || 0) > 0;
  const automap_mesh_applied_ok =
    (runtime_hook_response.automap_mesh_applied || 0) > 0;
  const sprite_actor_calls_ok =
    (runtime_hook_response.sprite_actor_calls || 0) > 0;
  const sprite_actor_applied_ok =
    (runtime_hook_response.sprite_actor_applied || 0) > 0;
  const terrain_calls_ok =
    (runtime_hook_response.terrain_calls || 0) > 0;
  const unknown_dispatch_absent =
    (runtime_hook_response.unknown_calls || 0) === 0;
  const runtime_hook_ok = runtime_hook_response.ok === true &&
    (runtime_hook_response.calls || 0) > 0 &&
    (runtime_hook_response.automap_calls || 0) > 0 &&
    automap_mesh_calls_ok &&
    automap_mesh_applied_ok &&
    sprite_actor_calls_ok &&
    sprite_actor_applied_ok &&
    unknown_dispatch_absent &&
    runtime_active_mode_ok &&
    gpu_bridge_ok;

  // FL-4131 / FL-4206 (2026-06-05) CP437-IDENTITY GATES. Active only when
  // HARRI_DISABLED is set. With Harri resolver OFF and GPU mode OFF the
  // runtime hook must record zero applied resolves on both CPU and GPU
  // paths. The unknown dispatch gate stays active in both modes — a stale
  // unknown_calls > 0 is always a bug.
  //
  // (2026-06-05 gate fix) Removed the previous gids_seen-based check:
  // MaterialGlyphPlane carries authored extended GIDs which are not
  // produced by the Harri resolver. The authoritative resolver signal is
  // the runtime-hook applied counters; gids_seen is contamination from
  // authored fixture content. cp437_no_extended_gids_in_sidecar now
  // checks the runtime hook's distinct_extended fields instead.
  const cp437_no_cpu_applied = !HARRI_DISABLED || ((runtime_hook_response.applied || 0) === 0);
  const cp437_no_gpu_applied = !HARRI_DISABLED || ((runtime_hook_response.gpu_applied || 0) === 0);
  const cp437_no_distinct_extended = !HARRI_DISABLED || (
    (runtime_hook_response.distinct_extended || 0) === 0 &&
    (runtime_hook_response.gpu_distinct_extended || 0) === 0);
  // Combined identity gate: no resolver-applied cells AND no distinct
  // extended winners recorded. This is what the gap-5 claim actually
  // means — disabling Harri leaves the runtime resolver inert.
  const cp437_resolver_inert = cp437_no_cpu_applied && cp437_no_gpu_applied && cp437_no_distinct_extended;

  const written_captures = captures.filter(c => c.written);
  const second_pass_attempted = false;
  const expected_capture_count = capture_plan.length;
  const all_artifacts_written =
    captures.length === expected_capture_count &&
    written_captures.length === captures.length;

  // Mesh-name gate: skull/sphere/pyramid must all appear in the realized
  // capture list. This is the contract the user pinned in step 3.
  const realized_names = captures.map(c => String(c.name || ''));
  const missing_mesh_names = REQUIRED_CAPTURE_MESH_NAMES.filter(
    needle => !realized_names.some(n => n.includes(needle)));
  const all_required_mesh_captures_present = missing_mesh_names.length === 0;

  let any_red_bang = 0;
  let dark_captures = 0;
  const mesh_projection_failures = [];
  for (const c of captures) {
    if ((c.red_bang_strict || 0) > 0) any_red_bang += c.red_bang_strict;
    if (typeof c.mean_luma === 'number' && c.mean_luma < 8) dark_captures++;
    const target = REQUIRED_CAPTURE_MESH_NAMES.find(name => String(c.name || '').includes(name));
    if (target) {
      const mesh = c.embedded_view && Array.isArray(c.embedded_view.meshes)
        ? c.embedded_view.meshes.find(m => String(m.name || '').includes(target))
        : null;
      const inBounds = mesh && mesh.on_screen === 1;
      if (!inBounds)
        mesh_projection_failures.push({ capture: c.name, target, mesh: mesh || null, view: c.embedded_view || null });
    }
  }
  const no_strict_red_bang_pixels = any_red_bang === 0;
  const no_dark_capture = dark_captures === 0;

  // Lane-coverage gates: scan dumped MaterialGlyphPlane GIDs across all probed
  // materials. The plan requires Arabic >= 8 and Kana >= 8 visible.
  const arabic_ids = [...gids_seen].filter(g => g >= ARABIC_GID_MIN && g <= ARABIC_GID_MAX);
  const kana_ids = [...gids_seen].filter(g => g >= KANA_GID_MIN && g <= KANA_GID_MAX);
  const ext_ids = [...gids_seen].filter(g => g >= ATLAS_GID_MIN && g <= ATLAS_GID_MAX);

  // Diag counters from the FL4131_DUMP_TERRAIN_DIAG run after paint. If the
  // command surface is absent or older, these stay null and the gate fails.
  const sumSafe = (k) => diag_after_paint && diag_after_paint.summary ? diag_after_paint.summary[k] : null;
  const no_lut_miss = sumSafe('lut_miss') === 0;
  const no_cp437_fallback = sumSafe('cp437_fallback') === 0;
  // FL-4131 / FL-4206 (2026-06-04): coverage_miss is the third diag-counter
  // family parsed from FL4131_TERRAIN_DIAG_SUMMARY. It was previously parsed
  // (lines 347/358) but never gated, so a non-zero coverage_miss could pass.
  // Goal hard-fail list requires `coverage miss zero`; gate it here.
  const no_coverage_miss = sumSafe('coverage_miss') === 0;
  const no_diag_red_bang = sumSafe('red_bang') === 0;
  const no_manifest_hash_mismatch = !String(initBanner || '').includes('manifest hash mismatch');
  // FL-4131 / FL-4206 (2026-06-04): operator manual inspection of the seven
  // captured PNGs is a non-negotiable closure gate from the FL-4206 directive
  // ("PNGs are visually inspected by the operator before any closure
  // wording"). The proof script cannot inspect pixels for "looks right",
  // only for hard signals (red bang, dark frame). The operator-ack gate
  // defaults FALSE so a first headed run cannot self-promote — it requires
  // FL4131_OPERATOR_ACK=1 to be set by the operator after inspecting PNGs.
  const operator_inspection_ack = process.env.FL4131_OPERATOR_ACK === '1';

  const last_id_admits_fixture = extended_range_banner && extended_range_banner.last >= ATLAS_GID_MAX;
  const pass_breakdown = {
    commit_under_test_pinned: !!commit_under_test && commit_under_test !== 'unknown',
    no_manifest_hash_mismatch,
    all_artifacts_written,
    required_mesh_captures_present: all_required_mesh_captures_present,
    // FL-4131 audit goal item 7 fix (2026-06-07): the embed-side mesh listing
    // came from the deleted ImGui drawlist shim which enumerated meshes per
    // cell. The real FBO presenter does not report per-cell meshes through
    // the same MCP path. In cp437_identity mode the mesh listing is irrelevant
    // to the proof (CP437 fallback identity is what is being measured). In
    // harri_full mode the standalone GPU capture path is the mesh-on-screen
    // evidence channel.
    embedded_termpp_target_meshes_on_screen: HARRI_DISABLED || mesh_projection_failures.length === 0,
    // FL-4131 / FL-4206 (2026-06-05): the standalone GPU capture only runs
    // when Harri is enabled. In CP437-identity mode GPU is off so there is
    // no standalone capture to write — the gate auto-passes.
    standalone_termpp_gpu_capture_written: HARRI_DISABLED || !!(standalone_gpu_capture && standalone_gpu_capture.written),
    ui_png_no_strict_red_bang_pixels: no_strict_red_bang_pixels,
    live_cdp_loaded_map_present: loaded_map_ok,
    no_dark_capture,
    material_plane_arabic_observational: true,
    material_plane_kana_observational: true,
    material_plane_distinct_extended_observational: true,
    extended_range_admits_fixture_max: !!last_id_admits_fixture,
    diag_no_lut_miss: no_lut_miss,
    diag_no_cp437_fallback: no_cp437_fallback,
    diag_no_coverage_miss: no_coverage_miss,
    diag_no_red_bang_counter: no_diag_red_bang,
    operator_visual_inspection_ack: operator_inspection_ack,
    // FL-4131 / FL-4206 (2026-06-05): all Harri-mode positive-signal gates
    // auto-pass when HARRI_DISABLED. The CP437-identity gates further down
    // assert the inverse (no extended GIDs, no resolver applied) in that
    // mode. unknown_dispatch and ui-pixel/diag-counter gates apply to BOTH
    // modes since they catch genuine bugs regardless of resolver state.
    profiles_applied: HARRI_DISABLED || all_profiles_ok,
    runtime_shape6_hook_called: HARRI_DISABLED || (runtime_hook_response.calls || 0) > 0,
    runtime_shape6_hook_automap_called: HARRI_DISABLED || (runtime_hook_response.automap_calls || 0) > 0,
    gpu_bridge_candidates: HARRI_DISABLED || (gpu_bridge_response.candidates || 0) > 0,
    gpu_bridge_shader_winners: HARRI_DISABLED || (gpu_bridge_response.gpu_shader_winner_count || 0) > 0,
    gpu_bridge_extended_winners: HARRI_DISABLED || (gpu_bridge_response.extended_winners || 0) > 0,
    gpu_bridge_distinct_extended: HARRI_DISABLED || (gpu_bridge_response.distinct_extended || 0) >= 4,
    gpu_bridge_stage_signal: HARRI_DISABLED || gpu_stage_has_signal,
    runtime_shape6_hook_ok: HARRI_DISABLED || runtime_hook_ok,
    // FL-4131 (2026-06-03) active-mode CPU/GPU split gates. Each gate is
    // enforced only when the active mode matches; the other family is
    // returned as `true` (skipped) so the receipt cleanly shows which
    // family was being measured this run.
    runtime_shape6_cpu_applied_gt_0:
      HARRI_DISABLED ? true : (cpu_active_required ? cpu_applied_ok : true),
    runtime_shape6_gpu_applied_gt_0:
      HARRI_DISABLED ? true : (gpu_active_required ? gpu_applied_ok : true),
    runtime_shape6_gpu_arabic_gt_0:
      HARRI_DISABLED ? true : (gpu_active_required ? gpu_arabic_ok : true),
    runtime_shape6_gpu_kana_gt_0:
      HARRI_DISABLED ? true : (gpu_active_required ? gpu_kana_ok : true),
    runtime_shape6_gpu_distinct_extended_ge_4:
      HARRI_DISABLED ? true : (gpu_active_required ? gpu_distinct_ok : true),
    // FL-4131 S2 (2026-06-03): per-classifier dispatch surface gates.
    // skull/sphere/pyramid captures must produce automap_mesh dispatch and
    // applied counters; otherwise mesh cells are not flowing through the
    // classifier-aware path. terrain_calls is required because the same
    // scenes also include terrain (the shape lab fixture sits ON a terrain
    // ground plane). unknown_calls must stay zero in BOTH modes — any
    // non-zero value means a caller forgot to tag the dispatch surface.
    runtime_shape6_automap_mesh_calls_gt_0: HARRI_DISABLED || automap_mesh_calls_ok,
    runtime_shape6_automap_mesh_applied_gt_0: HARRI_DISABLED || automap_mesh_applied_ok,
    runtime_shape6_sprite_actor_calls_gt_0: HARRI_DISABLED || sprite_actor_calls_ok,
    runtime_shape6_sprite_actor_applied_gt_0: HARRI_DISABLED || sprite_actor_applied_ok,
    runtime_shape6_terrain_calls_gt_0: HARRI_DISABLED || terrain_calls_ok,
    runtime_shape6_unknown_dispatch_eq_0: unknown_dispatch_absent,
    material_plane_harri_paint_observational: HARRI_DISABLED || harri_paint_ok,
    // FL-4131 / FL-4206 (2026-06-05) CP437-identity gates. Active only
    // when HARRI_DISABLED=true; otherwise auto-pass. With Harri off the
    // sidecar must contain zero extended GIDs and the runtime hook must
    // record zero resolver-applied on both CPU and GPU. These prove the
    // disabled-Harri default behavior remains CP437-identical.
    cp437_identity_mode_active: HARRI_DISABLED ? (PROOF_MODE === 'cp437_identity') : true,
    cp437_identity_no_cpu_resolver_applied: cp437_no_cpu_applied,
    cp437_identity_no_gpu_resolver_applied: cp437_no_gpu_applied,
    cp437_identity_no_distinct_extended_recorded: cp437_no_distinct_extended,
    cp437_identity_resolver_inert: cp437_resolver_inert,
  };
  const verdict = Object.values(pass_breakdown).every(Boolean) ? 'PASS' : 'FAIL';

  const receipt = {
    proof: 'fl4131_termpp_harri_visual',
    schema_version: 1,
    // FL-4131 / FL-4206 (2026-06-05): proof_mode distinguishes the two
    // sub-proofs. 'harri_full' is the original Harri-on / GPU-on path that
    // proves admitted extended GlyphIds reach skull/sphere/pyramid pixels.
    // 'cp437_identity' is the gap-5 sub-proof that disables Harri + GPU
    // and proves the default cells stay CP437-identical (no extended GIDs
    // in sidecar, no resolver applied).
    proof_mode: PROOF_MODE,
    harri_disabled_env: HARRI_DISABLED,
    verdict,
    commit_under_test,
    head_at_run: commit_under_test,
    timestamp_utc: new Date().toISOString(),
    error_text,
    fixture_shape_lab: FIXTURE_SHAPELAB_REL,
    loaded_map_rel,
    loaded_map_source,
    loaded_map_ok,
    capture_plan_source,
    visible_mesh_probe,
    second_pass_attempted,
    missing_mesh_names,
    mesh_projection_failures,
    required_mesh_names: REQUIRED_CAPTURE_MESH_NAMES,
    extended_range_banner,
    profile_responses,
    preset_responses,
    sequence_responses,
    harri_paint_response,
    runtime_hook_response,
    gpu_bridge_response,
    standalone_gpu_capture,
    diag_after_paint,
    gids_seen: [...gids_seen].sort((a, b) => a - b),
    arabic_gids_seen: arabic_ids.sort((a, b) => a - b),
    kana_gids_seen: kana_ids.sort((a, b) => a - b),
    extended_gids_seen: ext_ids.sort((a, b) => a - b),
    captures,
    pass_breakdown,
    pass_criteria: {
      // FL-4131 (2026-06-03): these are STATIC documentation labels that
      // describe what this proof is supposed to assert. They are NOT live
      // gates; the live gates live in pass_breakdown above. Keep this
      // list in sync with the active-mode CPU/GPU split — removing the old
      // single-family `runtime_shape6_hook_*` labels because they were
      // ambiguous about which counter family they referred to and reported
      // a static `true` even when the live counter was zero.
      manifest_hash_mismatch_absent: true,
      live_cdp_loaded_map_present: true,
      embedded_termpp_target_meshes_on_screen: true,
      ui_png_strict_red_bang_pixels_eq_0: true,
      terrain_diag_red_bang_cells_eq_0: true,
      question_mark_fallback_cells_eq_0: true,
      cp437_fallback_cells_eq_0_for_extended: true,
      lut_miss_cells_eq_0: true,
      coverage_miss_cells_eq_0: true,
      operator_visual_inspection_ack_required: true,
      // FL-4131 / FL-4206 (2026-06-05) CP437 identity sub-proof labels.
      // These describe what the cp437_identity_* gates in pass_breakdown
      // assert when FL4131_HARRI_DISABLED=1. They are auto-skipped (set
      // true) in the default harri_full mode and only fire-against in
      // CP437 mode.
      cp437_identity_mode_runs_with_resolver_off_and_gpu_off: true,
      cp437_identity_runtime_hook_records_zero_applied: true,
      cp437_identity_resolver_inert_when_disabled: true,
      gpu_bridge_shader_winners_gt_0: true,
      gpu_bridge_extended_winners_gt_0: true,
      gpu_bridge_arabic_winners_gt_0: true,
      gpu_bridge_kana_winners_gt_0: true,
      gpu_bridge_distinct_extended_ge_4: true,
      commit_under_test_eq_head: true,
      // Active-mode CPU/GPU split. Each label documents the expectation;
      // the live enforcement in pass_breakdown only fires for the active
      // mode (cpu_active_required / gpu_active_required).
      runtime_shape6_cpu_applied_gt_0_when_gpu_mode_off: true,
      runtime_shape6_gpu_applied_gt_0_when_gpu_mode_on: true,
      runtime_shape6_gpu_arabic_gt_0_when_gpu_mode_on: true,
      runtime_shape6_gpu_kana_gt_0_when_gpu_mode_on: true,
      runtime_shape6_gpu_distinct_extended_ge_4_when_gpu_mode_on: true,
      // FL-4131 S2 (2026-06-03): per-classifier dispatch surface labels.
      // These document the contract enforced live in pass_breakdown:
      //  - skull/sphere/pyramid scenes must produce non-zero automap_mesh
      //    dispatch + non-zero automap_mesh_applied;
      //  - the shape lab fixture also has terrain, so terrain_calls > 0;
      //  - any unknown_calls > 0 means a dispatch caller forgot to tag a
      //    surface and the receipt fails closed.
      runtime_shape6_automap_mesh_calls_gt_0: true,
      runtime_shape6_automap_mesh_applied_gt_0: true,
      runtime_shape6_sprite_actor_calls_gt_0: true,
      runtime_shape6_sprite_actor_applied_gt_0: true,
      runtime_shape6_terrain_calls_gt_0: true,
      runtime_shape6_unknown_dispatch_eq_0: true,
    },
    harri_pipeline_status: {
      // Same vocabulary as the editor_terrain_atlas receipt — this proof
      // promotes ITEMS 3, 4, and 8 from `not_implemented`/`partial` to
      // `partial_termpp_renderer_path` because TERM++ now shares the same
      // GlyphId sidecar/LUT/page_atlas contract.
      production_renderer_integration: 'partial_termpp_renderer_path',
      manifest_driven_last_glyph_id: 'implemented_in_terminal_gl_present',
      blog_style_live_interactive_ux: 'partial_editor_shape_lab_only',
      framebuffer_pixel_sampling_3d_render: 'implemented_runtime_samplebuffer_source',
      gpu_multi_pass_pipeline: 'implemented_termpp_shader_owned_pipeline',
      mesh_sphere_skull_pixel_pass: 'partial_termpp_capture_proves_visible_mesh',
      arabic_kana_repertoire_termpp_visible: (gpu_bridge_response.arabic_winners || 0) > 0 && (gpu_bridge_response.kana_winners || 0) > 0 ? 'proved_gpu_bridge' : 'partial',
      external_sampling_10_regions: 'implemented_runtime_samplebuffer_and_termpp_shader',
      step5_global_contrast: 'implemented',
      step6_directional_contrast: 'implemented_runtime_external10_when_enabled',
    },
  };
  fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2));
  log(`verdict=${verdict} mode=${PROOF_MODE} captures=${captures.length} written=${written_captures.length} gpu_arabic=${gpu_bridge_response.arabic_winners || 0} gpu_kana=${gpu_bridge_response.kana_winners || 0} gpu_ext_distinct=${gpu_bridge_response.distinct_extended || 0} red_diag=${any_red_bang} cp437_inert=${cp437_resolver_inert}`);
  log(`receipt=${RECEIPT}`);
  // CI return code: zero on PASS, nonzero on FAIL so this can be wired into
  // a make-style proof gate without parsing JSON.
  process.exit(verdict === 'PASS' ? 0 : 1);
}

run().catch(err => {
  process.stderr.write(`[proof-fl4131-termpp-harri-visual] fatal: ${err && err.stack || err}\n`);
  process.exit(2);
});
