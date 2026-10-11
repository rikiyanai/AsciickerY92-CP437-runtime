// proof_fl4131_live_panes_game_map.js
//
// FL-4131 #6 visual proof on the ACTUAL game map (assets/a3d/game_map_y8.a3d).
//
// What this proves:
//   1) Shape Lab live wire actually fires on the original game map (not a
//      synthetic fixture). The harness toggles live_sample_active on, then
//      moves the cell cursor and asserts that source_vector changes (which
//      it cannot do unless the wire is reading the live scene).
//   2) Captures an editor frame (PPM) at each cursor position so the user
//      can visually verify the Shape Lab panes update with real scene data.
//   3) Dumps the new CPU/GPU parity counter at the end.
//
// CLI: node scripts/proofs/proof_fl4131_live_panes_game_map.js

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
// FL-4131 #6 visual proof on the UPSTREAM ORIGINAL Y8 map (msokalski/asciicker
// master:a3d/game_map_y8.a3d, blob 345639eaf...). The local Y9-2 fork's
// assets/a3d/game_map_y8.a3d has diverged; we deliberately use the upstream
// blob so the proof runs on the original game world.
// FL-4131 item 12: canonical original-Y8 path. Byte-identical to the now-
// deprecated .run/upstream_y8/game_map_y8.a3d duplicate (SHA 54c1373022eb...).
const FIXTURE_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification',
                          'fl4131', 'production_parity', 'live_panes_upstream_y8');
const RECEIPT = path.join(OUT_DIR, 'live_panes_game_map_receipt.json');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48922', 10);
const READY_TIMEOUT_MS = 60000;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[live-panes-proof] ${m}\n`); }
function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port, mapRel) {
  const args = ['--cdp', String(port), '--map', mapRel];
  const proc = spawn(ASCIIID, args, { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  return proc;
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

function parseLiveSrc(text) {
  const out = {};
  const re = /(\w+)=(-?\d+(?:\.\d+)?)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const v = m[2];
    out[m[1]] = v.includes('.') ? Number(v) : Number(v);
  }
  return out;
}

function parseDump(text) {
  const m = text.match(/FL4131_RUNTIME_SHAPE6_HOOK\s+(.+)/);
  if (!m) return null;
  const out = {};
  for (const tok of m[1].split(/\s+/)) {
    const eq = tok.indexOf('=');
    if (eq < 0) continue;
    const k = tok.slice(0, eq);
    const v = tok.slice(eq + 1);
    out[k] = /^\d+$/.test(v) ? Number(v) : v;
  }
  return out;
}

async function capture(client, framePath, ppm) {
  try { if (fs.existsSync(framePath)) fs.unlinkSync(framePath); } catch (_) {}
  if (ppm) {
    await client.request('CAPTURE_FRAME', framePath, 60000);
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1024) break;
      await sleep(150);
    }
  } else {
    await client.request('CAPTURE_TERMPP_FRAME', framePath, 60000);
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      if (fs.existsSync(framePath) && fs.statSync(framePath).size > 1024) break;
      await sleep(150);
    }
  }
}

async function main() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  if (!fs.existsSync(path.join(REPO_ROOT, FIXTURE_REL))) throw new Error(`missing map ${FIXTURE_REL}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, FIXTURE_REL);
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log(`connected to asciiid on :${CDP_PORT} with map=${FIXTURE_REL}`);

    // Make the SDL window match the framebuffer so ImGui sidebar renders at
    // a visible size in CAPTURE_FRAME PPMs.
    await client.request('SET_WINDOW_SIZE', '1920 1080', 5000);
    await sleep(500);
    // Open Shape Lab so its UI gets composited into the editor frame.
    await client.request('FL4131_SHAPE_LAB_OPEN', '', 10000);
    // Run TERM++ in GPU mode so the bridge populates winner_gid and internal6.
    await client.request('FL4131_HARRI_SET_PIPELINE',
      'gpu_mode=1 use_contrast=1 use_directional=1 normalize=1', 10000);
    await client.request('SET_TERMPP_RUNTIME_HARRI_RESOLVE', '1', 5000);
    for (const mat of [1, 2, 3, 4, 5]) {
      await client.request('FL4131_HARRI_SET_MAT_PROFILE',
        `${mat} arabic=1 math=1 shapes=1 box=1 punct=0 other=1 katakana=1 min_density=0.05 max_density=1.0`,
        10000);
    }
    await client.request('FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS', '1 1 1 1 1 1', 5000);
    await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 5000);
    await client.request('SET_CAMERA_VIEW', '0 0 60 0 38 14', 10000);
    await client.request('SET_TERMPP_CAMERA_VIEW', '0 0 60 0 38 14 0', 10000);
    await sleep(500);

    // Burn frames via the editor (PPM, not TERM++ shader pass) so the bridge
    // begins populating without paying for TERM++ shader per burn.
    for (let i = 0; i < 2; i++) {
      const tmp = path.join(OUT_DIR, `_burn_${i}.ppm`);
      await capture(client, tmp, true);
      try { fs.unlinkSync(tmp); } catch (_) {}
    }

    // Test 1: live wire OFF -- read source_vector baseline.
    await client.request('FL4131_SHAPE_LAB_SET_LIVE_CELL', '0 0 0', 5000);
    await sleep(200);
    const liveOffRaw = await client.request('FL4131_SHAPE_LAB_DUMP_LIVE_SOURCE_VECTOR', '', 5000);
    const liveOff = parseLiveSrc(liveOffRaw);
    log(`live OFF source_vector: tl=${liveOff.tl} tr=${liveOff.tr} ml=${liveOff.ml} mr=${liveOff.mr} bl=${liveOff.bl} br=${liveOff.br}`);

    // Test 2: live wire ON at cell (10, 5). Burn one editor frame so the
    // per-frame refresh inside AsciiidShapeLabRenderPanel runs at least once
    // with live_sample_active=true.
    await client.request('FL4131_SHAPE_LAB_SET_LIVE_CELL', '1 10 5', 5000);
    const burnPpm1 = path.join(OUT_DIR, '_burn_live1.ppm');
    await capture(client, burnPpm1, true);
    try { fs.unlinkSync(burnPpm1); } catch (_) {}
    await sleep(300);
    const liveAt10_5Raw = await client.request('FL4131_SHAPE_LAB_DUMP_LIVE_SOURCE_VECTOR', '', 5000);
    const liveAt10_5 = parseLiveSrc(liveAt10_5Raw);
    log(`live ON @ (10,5): tl=${liveAt10_5.tl} tr=${liveAt10_5.tr} ml=${liveAt10_5.ml} mr=${liveAt10_5.mr} bl=${liveAt10_5.bl} br=${liveAt10_5.br}`);

    // Capture editor PPM (whole window, includes Shape Lab UI).
    const frame_a = path.join(OUT_DIR, 'editor_shape_lab_live_at_10_5.ppm');
    await capture(client, frame_a, true);

    // Test 3: move cursor to a different cell and assert source_vector differs.
    await client.request('FL4131_SHAPE_LAB_SET_LIVE_CELL', '1 80 40', 5000);
    const burnPpm2 = path.join(OUT_DIR, '_burn_live2.ppm');
    await capture(client, burnPpm2, true);
    try { fs.unlinkSync(burnPpm2); } catch (_) {}
    await sleep(300);
    const liveAt80_40Raw = await client.request('FL4131_SHAPE_LAB_DUMP_LIVE_SOURCE_VECTOR', '', 5000);
    const liveAt80_40 = parseLiveSrc(liveAt80_40Raw);
    log(`live ON @ (80,40): tl=${liveAt80_40.tl} tr=${liveAt80_40.tr} ml=${liveAt80_40.ml} mr=${liveAt80_40.mr} bl=${liveAt80_40.bl} br=${liveAt80_40.br}`);

    const frame_b = path.join(OUT_DIR, 'editor_shape_lab_live_at_80_40.ppm');
    await capture(client, frame_b, true);

    // Compute deltas.
    const keys = ['tl', 'tr', 'ml', 'mr', 'bl', 'br'];
    const deltaOffVsOn = {};
    const deltaOnVsOn = {};
    for (const k of keys) {
      deltaOffVsOn[k] = (liveAt10_5[k] || 0) - (liveOff[k] || 0);
      deltaOnVsOn[k] = (liveAt80_40[k] || 0) - (liveAt10_5[k] || 0);
    }
    const sumAbsOffOn = keys.reduce((a, k) => a + Math.abs(deltaOffVsOn[k]), 0);
    const sumAbsOnOn = keys.reduce((a, k) => a + Math.abs(deltaOnVsOn[k]), 0);

    // Final dump for parity numbers.
    const dump = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
    const parsed = parseDump(dump);

    const gates = {
      map_is_game_map_y8: FIXTURE_REL.endsWith('game_map_y8.a3d'),
      live_off_dump_parsed: Object.keys(liveOff).length > 0,
      live_on_dump_parsed: Object.keys(liveAt10_5).length > 0,
      live_cursor_moves_source_vector: sumAbsOnOn > 0.0001,
      live_toggle_changes_source_vector: sumAbsOffOn > 0.0001,
      editor_frame_a_written: fs.existsSync(frame_a) && fs.statSync(frame_a).size > 1024,
      editor_frame_b_written: fs.existsSync(frame_b) && fs.statSync(frame_b).size > 1024,
      runtime_hook_invoked: parsed && parsed.calls > 0,
      gpu_mode_active: parsed && parsed.gpu_mode === 1,
    };
    const all_passed = Object.values(gates).every(Boolean);

    const receipt = {
      schema: 'fl4131_live_panes_game_map_receipt.v1',
      commit_under_test: currentCommit(),
      captured_at_utc: new Date().toISOString(),
      harness: 'scripts/proofs/proof_fl4131_live_panes_game_map.js',
      map: FIXTURE_REL,
      frames: {
        editor_shape_lab_live_at_10_5: path.relative(REPO_ROOT, frame_a),
        editor_shape_lab_live_at_80_40: path.relative(REPO_ROOT, frame_b),
      },
      live_source_vector_off: liveOff,
      live_source_vector_at_10_5: liveAt10_5,
      live_source_vector_at_80_40: liveAt80_40,
      delta_off_vs_on_sum_abs: sumAbsOffOn,
      delta_on_vs_on_sum_abs: sumAbsOnOn,
      dump_parsed: parsed,
      dump_raw: dump.trim(),
      gates,
      verdict: all_passed
        ? 'LIVE WIRE PROVEN ON GAME MAP: source_vector changes with cursor, editor frames captured'
        : 'LIVE WIRE GATE FAILED -- see gates breakdown',
    };
    fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2));
    log(`receipt: ${RECEIPT}`);
    process.stderr.write(`[live-panes-proof] verdict=${receipt.verdict}\n`);
    process.stderr.write(`[live-panes-proof] delta_off_vs_on_sum_abs=${sumAbsOffOn.toFixed(4)} ` +
      `delta_on_vs_on_sum_abs=${sumAbsOnOn.toFixed(4)} ` +
      `parity_seen=${parsed && parsed.cpu_gpu_parity_seen} ` +
      `disagree=${parsed && parsed.cpu_gpu_disagree}\n`);
    process.exitCode = all_passed ? 0 : 1;
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(2); });
