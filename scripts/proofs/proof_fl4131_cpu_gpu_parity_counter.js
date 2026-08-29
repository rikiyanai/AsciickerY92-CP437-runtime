// proof_fl4131_cpu_gpu_parity_counter.js
//
// FL-4131 #6 CDP-driven proof of the new CPU/GPU parity counter.
//
// Drives the same asciiid CDP harness as proof_fl4131_runtime_hook_slider.js
// but enables gpu_mode=1 so the TERM++ GL shader populates winner_gid[].
// Both code paths in AsciiidRuntimeGlyphResolveHook (CPU branch + gpu_mode
// branch) bump cpu_gpu_parity_seen / cpu_gpu_disagree when winner_gid carries
// an extended GlyphId. After N frames the dump is asserted.
//
// CLI: node scripts/proofs/proof_fl4131_cpu_gpu_parity_counter.js

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const FIXTURE_REL = 'assets/a3d/fl4131_shape_lab_20x20.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification',
                          'fl4131', 'production_parity');
const RECEIPT = path.join(OUT_DIR, 'cpu_gpu_parity_counter_receipt.json');
const CAPTURE_PNG = path.join(OUT_DIR, 'parity_capture.png');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48911', 10);
const READY_TIMEOUT_MS = 45000;
const NUM_FRAMES = 3;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[parity-proof] ${m}\n`); }
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

async function main() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, FIXTURE_REL);
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log(`connected on :${CDP_PORT}`);

    await client.request('SET_CAMERA_VIEW', '0 60 18 180 38 24', 15000);
    await sleep(400);
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
    await client.request('SET_TERMPP_CAMERA_VIEW', '0 60 18 180 38 24 0', 15000);

    // Burn frames so the GPU shader populates winner_gid[].
    for (let i = 0; i < NUM_FRAMES; i++) {
      try { if (fs.existsSync(CAPTURE_PNG)) fs.unlinkSync(CAPTURE_PNG); } catch (_) {}
      await client.request('CAPTURE_TERMPP_FRAME', CAPTURE_PNG, 60000);
      const deadline = Date.now() + 4000;
      while (Date.now() < deadline) {
        if (fs.existsSync(CAPTURE_PNG) && fs.statSync(CAPTURE_PNG).size > 1024) break;
        await sleep(120);
      }
      await sleep(120);
    }

    const dump = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
    const parsed = parseDump(dump);
    if (!parsed) throw new Error('failed to parse dump');
    log(`parsed.calls=${parsed.calls} gpu_applied=${parsed.gpu_applied} ` +
        `parity_seen=${parsed.cpu_gpu_parity_seen} disagree=${parsed.cpu_gpu_disagree}`);

    const ratio = parsed.cpu_gpu_parity_seen > 0
      ? parsed.cpu_gpu_disagree / parsed.cpu_gpu_parity_seen
      : null;

    const gates = {
      hook_enabled: parsed.enabled === 1,
      gpu_mode_active: parsed.gpu_mode === 1,
      hook_invoked: parsed.calls > 0,
      gpu_winner_grid_populated: parsed.cpu_gpu_parity_seen > 0,
      no_catalog_fallback_zero: parsed.no_catalog_fallback === 0,
      no_match_fallback_zero: parsed.no_match_fallback === 0,
    };
    const all_passed = Object.values(gates).every(Boolean);

    const receipt = {
      schema: 'fl4131_cpu_gpu_parity_counter_receipt.v1',
      commit_under_test: currentCommit(),
      captured_at_utc: new Date().toISOString(),
      harness: 'scripts/proofs/proof_fl4131_cpu_gpu_parity_counter.js',
      fixture: FIXTURE_REL,
      num_frames_captured: NUM_FRAMES,
      capture_png: path.relative(REPO_ROOT, CAPTURE_PNG),
      dump_raw: dump.trim(),
      dump_parsed: parsed,
      cpu_gpu_disagree_ratio: ratio,
      gates,
      verdict: all_passed
        ? 'CDP-DRIVEN PARITY COUNTER OBSERVABLE'
        : 'PARITY COUNTER DID NOT OBSERVE EXPECTED CONDITIONS',
    };
    fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2));
    log(`receipt: ${RECEIPT}`);
    process.stderr.write(`[parity-proof] verdict=${receipt.verdict} ` +
      `parity_seen=${parsed.cpu_gpu_parity_seen} disagree=${parsed.cpu_gpu_disagree} ` +
      `ratio=${ratio === null ? 'n/a' : ratio.toFixed(4)} calls=${parsed.calls} ` +
      `gpu_mode=${parsed.gpu_mode}\n`);
    process.exitCode = all_passed ? 0 : 1;
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(2); });
