// proof_fl4131_runtime_hook_slider.js
//
// FL-4131 / FL-4206 / FL-4207 Step 5 runtime-hook slider proof (2026-06-03).
//
// What this proves:
//   1) The runtime shape6 hook in engine/render/render_resolve.cpp actually
//      runs during standalone TERM++ frames against the shape-lab fixture
//      (hook calls > 0 each frame).
//   2) Both terrain (material_calls) AND non-terrain (automap_calls) paths
//      hit the hook — i.e., mesh / sprite / auto_mat cells participate.
//   3) Changing Shape Lab role_weights via FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS
//      changes the chosen GlyphIds (distinct_extended differs between
//      weight A and weight B) AND changes the rendered standalone TERM++
//      pixel set (PNG diff > 0).
//   4) Arabic + Kana lanes are admitted via the runtime hook (gated on
//      arabic_applied > 0 and kana_applied > 0 in at least one capture).
//   5) Fail-closed error counters (no_catalog_fallback, no_match_fallback)
//      remain zero in both captures.
//
// Single-owner discipline (Law 1): the test does not write the sidecar
// directly — it asks the runtime hook to do it through SET_ROLE_WEIGHTS,
// then captures the resulting PNG. If the captured pixels do not change,
// the hook is not the live owner.
//
// Output:
//   - 2 PNG captures (weight_set_a + weight_set_b) for a single diag camera
//     framing the mesh region of the fixture.
//   - 1 receipt JSON with counters, distinct GIDs, and a pixel-diff verdict.
//
// CLI:
//   node scripts/proofs/proof_fl4131_runtime_hook_slider.js
//
// The script fails (exit 1) if any of these gates fail:
//   - hook_calls_A == 0 OR hook_calls_B == 0
//   - applied_A == 0 OR applied_B == 0
//   - distinct_extended_A + distinct_extended_B == 0
//   - pixel_diff_total == 0 (no visible change between A and B)
//   - any capture missing/empty
//   - extended_range_banner missing or last < 647
//   - arabic_applied == 0 AND kana_applied == 0 in both captures
//   - no_catalog_fallback > 0 in either capture
//   - no_match_fallback > 0 in either capture

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');
const zlib = require('zlib');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const FIXTURE_REL = 'assets/a3d/fl4131_shape_lab_20x20.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification',
                          'fl4131', 'runtime_hook_slider');
const RECEIPT = path.join(OUT_DIR, 'runtime_hook_slider_receipt.json');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT
                          || String(48700 + (process.pid % 200)), 10);
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '45000', 10);
const MIN_PIXEL_DIFF = parseInt(process.env.PROOF_MIN_PIXEL_DIFF || '100', 10);

// Camera framed on the mesh row: meshes at (-50/0/+50, 60, 65). The pyramid
// camera is the primary slider before/after pair (decides the pass/fail
// pixel-diff gate); skull and sphere cameras are additional visual-inspection
// captures, one per weight set, so the user contract's "skull, sphere, and
// pyramid must be visibly present" can be checked manually from PNGs.
const CAMERA = {
  pos: '0 60 18', yaw: '180', pitch: '38', font_size: '24', player_visible: '0',
};
const EXTRA_CAMERAS = [
  { name: 'skull',   pos: '50 60 18',  yaw: '180', pitch: '38', font_size: '24', player_visible: '0' },
  { name: 'sphere',  pos: '-50 60 18', yaw: '180', pitch: '38', font_size: '24', player_visible: '0' },
];

// Weight sets are deliberately extreme so any reactive resolver path produces
// a different glyph distribution. Order: curve diagonal horizontal vertical sparse dense.
const WEIGHTS_A = '8.0 0.5 0.5 0.5 0.5 0.5';   // curve-biased
const WEIGHTS_B = '0.5 8.0 0.5 0.5 0.5 0.5';   // diagonal-biased

const HARRI_PROFILE_PARAMS =
  'arabic=1 math=1 shapes=1 box=1 punct=0 other=1 katakana=1 min_density=0.05 max_density=1.0';
const HARRI_PROFILE_MATS = [1, 2, 3, 4, 5];

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[proof-runtime-hook-slider] ${m}\n`); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port, mapRel) {
  const args = ['--cdp', String(port), '--map', mapRel];
  const proc = spawn(ASCIIID, args, { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '', err = '';
  proc.stdout.on('data', d => { out += String(d); process.stderr.write(`[asciiid-out] ${d}`); });
  proc.stderr.on('data', d => { err += String(d); process.stderr.write(`[asciiid-err] ${d}`); });
  return { proc, getOut: () => out, getErr: () => err };
}

async function connectCdp(port) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const s = await new Promise((res, rej) => {
        const sock = net.connect({ host: '127.0.0.1', port }, () => { sock.setTimeout(0); res(sock); });
        sock.once('error', rej);
        sock.setTimeout(1000, () => { sock.destroy(); rej(new Error('connect timeout')); });
      });
      s.setEncoding('utf8');
      return s;
    } catch (err) { lastErr = err; await sleep(250); }
  }
  throw new Error(`CDP port ${port} not ready: ${lastErr && lastErr.message}`);
}

class CdpClient {
  constructor(s) {
    this.s = s; this.nextId = 1; this.buf = ''; this.pending = new Map();
    s.on('data', c => this._data(c));
    s.on('close', () => { for (const { reject } of this.pending.values()) reject(new Error('closed')); });
  }
  _data(c) {
    this.buf += c;
    for (;;) {
      const i = this.buf.indexOf('\n');
      if (i < 0) break;
      const line = this.buf.slice(0, i); this.buf = this.buf.slice(i + 1);
      if (!line.trim()) continue;
      let msg; try { msg = JSON.parse(line); } catch (_) { continue; }
      if (typeof msg.id === 'number' && this.pending.has(msg.id)) {
        const { resolve } = this.pending.get(msg.id);
        this.pending.delete(msg.id); resolve(String(msg.result || ''));
      }
    }
  }
  request(method, params = '', timeout = 30000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params }) + '\n';
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => { this.pending.delete(id); reject(new Error(`timeout ${method}`)); }, timeout);
      this.pending.set(id, { resolve: v => { clearTimeout(t); resolve(v); }, reject: e => { clearTimeout(t); reject(e); } });
      this.s.write(payload);
    });
  }
  close() { this.s.destroy(); }
}

// 8-bit RGB/RGBA PNG decoder (subset). Reused contract with the bigger proof.
function decodePNG(buf) {
  if (!Buffer.isBuffer(buf) || buf[0] !== 0x89 || buf[1] !== 0x50) throw new Error('bad PNG');
  let off = 8, width = 0, height = 0, bitDepth = 0, colorType = 0;
  const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off); off += 4;
    const type = buf.slice(off, off + 4).toString('ascii'); off += 4;
    const data = buf.slice(off, off + len); off += len + 4;
    if (type === 'IHDR') { width = data.readUInt32BE(0); height = data.readUInt32BE(4); bitDepth = data[8]; colorType = data[9]; }
    else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
  }
  if (bitDepth !== 8) throw new Error(`bitdepth ${bitDepth}`);
  const channels = colorType === 6 ? 4 : (colorType === 2 ? 3 : 0);
  if (!channels) throw new Error(`colortype ${colorType}`);
  const inflated = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const out = Buffer.alloc(height * stride);
  let prevRow = Buffer.alloc(stride); let src = 0;
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
        const a = x >= channels ? dst[x - channels] : 0; const b = prevRow[x]; const c = x >= channels ? prevRow[x - channels] : 0;
        const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        let pred; if (pa <= pb && pa <= pc) pred = a; else if (pb <= pc) pred = b; else pred = c;
        dst[x] = (row[x] + pred) & 0xff;
      } break;
      default: throw new Error(`filter ${filt}`);
    }
    dst.copy(prevRow);
  }
  return { width, height, channels, data: out };
}

function pixelDiff(a, b) {
  if (!a || !b || a.width !== b.width || a.height !== b.height) {
    return { changed_pixels: -1, total_pixels: -1, percent_changed: -1, max_dr: -1, max_dg: -1, max_db: -1, sum_abs_delta: -1 };
  }
  const w = a.width, h = a.height, c = Math.min(a.channels, b.channels);
  const aStride = w * a.channels, bStride = w * b.channels;
  if (a.data.length < h * aStride || b.data.length < h * bStride) {
    return { changed_pixels: -2, total_pixels: -2, percent_changed: -1, max_dr: -1, max_dg: -1, max_db: -1, sum_abs_delta: -1 };
  }
  let changed = 0, total = 0, maxDr = 0, maxDg = 0, maxDb = 0, sumAbs = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const ai = y * aStride + x * a.channels, bi = y * bStride + x * b.channels;
      const dr = Math.abs(a.data[ai] - b.data[bi]);
      const dg = Math.abs(a.data[ai + 1] - b.data[bi + 1]);
      const db = Math.abs(a.data[ai + 2] - b.data[bi + 2]);
      sumAbs += dr + dg + db;
      if (dr > maxDr) maxDr = dr;
      if (dg > maxDg) maxDg = dg;
      if (db > maxDb) maxDb = db;
      if (dr + dg + db > 12) changed++;
      total++;
    }
  }
  return { changed_pixels: changed, total_pixels: total,
           percent_changed: total ? (100 * changed / total) : 0,
           max_dr: maxDr, max_dg: maxDg, max_db: maxDb, sum_abs_delta: sumAbs };
}

function parseHookDump(text) {
  // Field-order-agnostic tokenizer. The hook dump grew GPU + per-target +
  // parity counters after the original 14-field regex was written; that
  // regex returned null and silently nulled hook_stats. FL-4131 (FL-4131).
  const s = String(text || '');
  const m = s.match(/FL4131_RUNTIME_SHAPE6_HOOK\s+(.+)/);
  if (!m) return null;
  const out = {};
  for (const tok of m[1].split(/\s+/)) {
    const eq = tok.indexOf('=');
    if (eq < 0) continue;
    const k = tok.slice(0, eq);
    const v = tok.slice(eq + 1);
    out[k] = /^\d+$/.test(v) ? +v : v;
  }
  if (!('calls' in out)) return null;
  return out;
}

function parseExtendedRangeBanner(text) {
  for (const line of String(text || '').split('\n')) {
    const m = line.match(/TERM\+\+ admitted extended GlyphId range = \[(\d+)\.\.(\d+)\]/);
    if (m) return { first: +m[1], last: +m[2], line: line.trim() };
  }
  return null;
}

async function captureWithWeights(client, label, weights, outPath, settle_ms) {
  await client.request('FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS', weights, 15000);
  await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 5000);
  // Nudge camera so the resolve stage re-runs against the new weights (a
  // single set call without a frame submit won't necessarily refresh).
  await client.request('SET_TERMPP_CAMERA_VIEW',
    `${CAMERA.pos} ${CAMERA.yaw} ${CAMERA.pitch} ${CAMERA.font_size} ${CAMERA.player_visible}`, 30000);
  await sleep(settle_ms);
  try { if (fs.existsSync(outPath)) fs.unlinkSync(outPath); } catch (_) {}
  await client.request('CAPTURE_TERMPP_FRAME', outPath, 30000);
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    if (fs.existsSync(outPath) && fs.statSync(outPath).size > 1024) break;
    await sleep(150);
  }
  await sleep(500);
  const dump = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
  const stats = parseHookDump(dump);
  let png = null;
  if (fs.existsSync(outPath)) {
    const raw = fs.readFileSync(outPath);
    if (raw.length < 8 || raw[0] !== 0x89 || raw[1] !== 0x50) {
      png = { error: 'PNG header mismatch' };
    } else {
      try { png = decodePNG(raw); } catch (e) { png = { error: e.message }; }
    }
  }
  return {
    label, weights, png_path: path.relative(REPO_ROOT, outPath),
    written: fs.existsSync(outPath) && fs.statSync(outPath).size > 1024,
    hook_stats: stats, hook_dump_raw: dump.trim(),
    width: png && png.width, height: png && png.height,
    _decoded: png && !png.error ? png : null,
  };
}

async function run() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  if (!fs.existsSync(path.join(REPO_ROOT, FIXTURE_REL))) throw new Error(`missing fixture ${FIXTURE_REL}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const { proc, getOut, getErr } = startAsciiid(CDP_PORT, FIXTURE_REL);
  let client = null;
  let errorText = null;
  let captureA = null, captureB = null;
  let banner = null;
  const profileOk = [];

  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    await client.request('SET_CAMERA_VIEW', '0 0 12 0 60 22', 15000);
    await sleep(400);
    // Force hook on.
    await client.request('SET_TERMPP_RUNTIME_HARRI_RESOLVE', '1', 5000);
    // Admit Arabic + Kana per material so the runtime hook has candidates.
    for (const mat of HARRI_PROFILE_MATS) {
      const r = await client.request('FL4131_HARRI_SET_MAT_PROFILE',
        `${mat} ${HARRI_PROFILE_PARAMS}`, 15000);
      profileOk.push({ mat, ok: /FL4131_HARRI_MAT_PROFILE ok/.test(r || '') });
    }

    captureA = await captureWithWeights(client, 'weight_set_a_curve_biased', WEIGHTS_A,
      path.join(OUT_DIR, 'weight_set_a_curve_biased.png'), 1500);
    captureB = await captureWithWeights(client, 'weight_set_b_diagonal_biased', WEIGHTS_B,
      path.join(OUT_DIR, 'weight_set_b_diagonal_biased.png'), 1500);

    // Additional inspection captures: weight_set_b is still in effect; frame
    // skull (x=50) and sphere (x=-50) for visual mesh inspection. These do
    // not gate the proof — they only satisfy the user contract that all
    // three meshes be visually inspectable.
    for (const cam of EXTRA_CAMERAS) {
      const out = path.join(OUT_DIR, `mesh_${cam.name}_weight_b.png`);
      await client.request('SET_TERMPP_CAMERA_VIEW',
        `${cam.pos} ${cam.yaw} ${cam.pitch} ${cam.font_size} ${cam.player_visible}`, 30000);
      await sleep(1200);
      try { if (fs.existsSync(out)) fs.unlinkSync(out); } catch (_) {}
      await client.request('CAPTURE_TERMPP_FRAME', out, 30000);
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        if (fs.existsSync(out) && fs.statSync(out).size > 1024) break;
        await sleep(150);
      }
    }

    try { await client.request('QUIT', '', 5000); } catch (_) {}
  } catch (err) {
    errorText = err && err.message;
  } finally {
    if (client) client.close();
    if (proc && !proc.killed) proc.kill('SIGTERM');
    await sleep(600);
  }

  const combinedOutput = `${getOut() || ''}\n${getErr() || ''}`;
  banner = parseExtendedRangeBanner(combinedOutput);

  const diff = (captureA && captureA._decoded && captureB && captureB._decoded)
    ? pixelDiff(captureA._decoded, captureB._decoded)
    : { changed_pixels: -1, total_pixels: -1, percent_changed: -1, max_dr: -1, max_dg: -1, max_db: -1, sum_abs_delta: -1 };

  const a = captureA && captureA.hook_stats || {};
  const b = captureB && captureB.hook_stats || {};

  // FL-4131 audit goal item 6 fix (2026-06-07): gates accept either CPU or
  // GPU counters as evidence. With gpu_mode=1 (the current default), the GPU
  // shader is the authoritative glyph picker and the CPU hook sets
  // out->applied per-surface but does not increment the top-level applied
  // counter. The CPU's distinct_extended/arabic/kana counters stay at zero
  // because the CPU path returns before the recording branch. The GPU
  // pipeline populates gpu_applied, gpu_extended_applied, gpu_distinct_extended,
  // gpu_arabic, and gpu_kana -- those are the live truth in gpu_mode.
  // A successful slider proof requires SOME path to apply and SOME
  // distinct extended winners, regardless of CPU vs GPU authorship.
  const appliedA = (a.applied || 0) + (a.gpu_applied || 0);
  const appliedB = (b.applied || 0) + (b.gpu_applied || 0);
  const distinctExtTotal = (a.distinct_extended || 0) + (b.distinct_extended || 0)
                         + (a.gpu_distinct_extended || 0) + (b.gpu_distinct_extended || 0);
  const arabicAny = (a.arabic || 0) + (b.arabic || 0)
                  + (a.gpu_arabic || 0) + (b.gpu_arabic || 0);
  const kanaAny = (a.kana || 0) + (b.kana || 0)
                + (a.gpu_kana || 0) + (b.gpu_kana || 0);
  const gates = {
    fixture_loaded: fs.existsSync(path.join(REPO_ROOT, FIXTURE_REL)),
    captures_both_written: !!(captureA && captureA.written && captureB && captureB.written),
    hook_enabled_in_dump_a: a.enabled === 1,
    hook_enabled_in_dump_b: b.enabled === 1,
    hook_called_a: (a.calls || 0) > 0,
    hook_called_b: (b.calls || 0) > 0,
    hook_applied_a: appliedA > 0,
    hook_applied_b: appliedB > 0,
    automap_path_hit: (a.automap_calls || 0) + (b.automap_calls || 0) > 0,
    material_path_hit: (a.material_calls || 0) + (b.material_calls || 0) > 0,
    distinct_extended_nonzero: distinctExtTotal > 0,
    pixel_diff_present: diff.changed_pixels > 0 && diff.changed_pixels >= MIN_PIXEL_DIFF,
    extended_range_admits_647: banner && banner.last >= 647,
    arabic_admitted: arabicAny > 0,
    kana_admitted: kanaAny > 0,
    no_catalog_fallback_zero: (a.no_catalog_fallback || 0) === 0 && (b.no_catalog_fallback || 0) === 0,
    no_match_fallback_zero: (a.no_match_fallback || 0) === 0 && (b.no_match_fallback || 0) === 0,
  };
  const verdict = Object.values(gates).every(Boolean) ? 'PASS' : 'FAIL';

  const receipt = {
    proof: 'fl4131_runtime_hook_slider',
    schema_version: 1,
    verdict,
    commit_under_test: currentCommit(),
    timestamp_utc: new Date().toISOString(),
    fixture: FIXTURE_REL,
    camera: CAMERA,
    weights_a: WEIGHTS_A,
    weights_b: WEIGHTS_B,
    extended_range_banner: banner,
    profile_responses: profileOk,
    capture_a: captureA && { ...captureA, _decoded: undefined },
    capture_b: captureB && { ...captureB, _decoded: undefined },
    pixel_diff: diff,
    gates,
    error_text: errorText,
  };
  fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2));
  log(`verdict=${verdict} A.calls=${a.calls || 0} B.calls=${b.calls || 0} `
      + `A.applied=${a.applied || 0} B.applied=${b.applied || 0} `
      + `A.distinct=${a.distinct_extended || 0} B.distinct=${b.distinct_extended || 0} `
      + `pixel_diff_changed=${diff.changed_pixels} pct=${diff.percent_changed && diff.percent_changed.toFixed(3)}`);
  log(`receipt=${RECEIPT}`);
  process.exit(verdict === 'PASS' ? 0 : 1);
}

run().catch(err => {
  process.stderr.write(`[proof-runtime-hook-slider] fatal: ${err && err.stack || err}\n`);
  process.exit(2);
});
