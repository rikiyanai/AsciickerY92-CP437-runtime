// proof_fl4131_embedded_termpp_y8.js
//
// FL-4131 #6 visible proof on upstream original Y8:
//   1. Calls FL4131_ORIGINAL_Y8_INSPECTION (single deterministic preset).
//   2. Calls SET_TERMPP_EMBEDDED_VISIBLE 1 (un-retired) to bring the
//      real-game embedded TERM++ panel into the editor frame.
//   3. Captures the editor PPM. The lower-right half should now show the
//      real TERM++ rendering inside the editor.
//
// CLI: node scripts/proofs/proof_fl4131_embedded_termpp_y8.js

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
// FL-4131 item 12: canonical original-Y8 path. Byte-identical to the now-
// deprecated .run/upstream_y8/game_map_y8.a3d duplicate (SHA 54c1373022eb...).
const FIXTURE_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification',
                          'fl4131', 'production_parity', 'embedded_termpp_y8');
const RECEIPT = path.join(OUT_DIR, 'receipt.json');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48933', 10);
const READY_TIMEOUT_MS = 60000;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[embed-y8-proof] ${m}\n`); }
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

async function capture(client, ppmPath) {
  try { if (fs.existsSync(ppmPath)) fs.unlinkSync(ppmPath); } catch (_) {}
  await client.request('CAPTURE_FRAME', ppmPath, 60000);
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    if (fs.existsSync(ppmPath) && fs.statSync(ppmPath).size > 1024) break;
    await sleep(150);
  }
}

async function main() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  if (!fs.existsSync(path.join(REPO_ROOT, FIXTURE_REL))) throw new Error(`missing ${FIXTURE_REL}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, FIXTURE_REL);
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log(`connected on :${CDP_PORT} with map=${FIXTURE_REL}`);

    // The deterministic preset configures everything in one call.
    const presetResp = await client.request('FL4131_ORIGINAL_Y8_INSPECTION', '', 10000);
    log(`preset: ${presetResp.trim()}`);

    // Position camera (the preset prints suggested coords).
    await client.request('SET_CAMERA_VIEW', '0 0 60 0 38 14', 10000);
    await sleep(400);

    // Un-retired: turn on the embedded TERM++ panel.
    const embResp = await client.request('SET_TERMPP_EMBEDDED_VISIBLE', '1', 10000);
    log(`embedded: ${embResp.trim()}`);

    // Burn frames so the embedded panel renders.
    for (let i = 0; i < 3; i++) {
      const tmp = path.join(OUT_DIR, `_burn_${i}.ppm`);
      await capture(client, tmp);
      try { fs.unlinkSync(tmp); } catch (_) {}
    }

    // Capture the editor frame; the lower-right half should be the real
    // embedded TERM++ output.
    const ppm = path.join(OUT_DIR, 'editor_with_embedded_termpp.ppm');
    await capture(client, ppm);

    // Dump runtime hook + parity counters.
    const dump = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);

    const receipt = {
      schema: 'fl4131_embedded_termpp_y8_receipt.v1',
      commit_under_test: currentCommit(),
      captured_at_utc: new Date().toISOString(),
      harness: 'scripts/proofs/proof_fl4131_embedded_termpp_y8.js',
      map: FIXTURE_REL,
      preset_response: presetResp.trim(),
      embedded_response: embResp.trim(),
      capture_ppm: path.relative(REPO_ROOT, ppm),
      capture_written: fs.existsSync(ppm) && fs.statSync(ppm).size > 1024,
      runtime_hook_dump: dump.trim(),
    };
    fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2));
    log(`receipt: ${RECEIPT}`);
    process.exitCode = receipt.capture_written ? 0 : 1;
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(2); });
