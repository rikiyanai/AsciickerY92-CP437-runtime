// proof_fl4131_item4_termpp_button.js
//
// FL-4131 audit goal item 4: the user-facing TERM++ button must mount the
// in-window FBO panel (not the standalone window). The button calls
// AsciiidEmbeddedTermSetVisible(!visible) -- the SAME handler the MCP
// command SET_TERMPP_EMBEDDED_VISIBLE uses. This proof drives that handler
// via CDP and captures before/after frames.
//
// Sequence:
//   1. burn frames
//   2. capture default startup (expect embed visible)
//   3. SET_TERMPP_EMBEDDED_VISIBLE 0
//   4. capture (expect no embed in bottom-right)
//   5. SET_TERMPP_EMBEDDED_VISIBLE 1
//   6. capture (expect embed visible again)

'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const MAP_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification',
                          'fl4131', 'production_parity', 'item4_termpp_button');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48956', 10);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[item4] ${m}\n`); }

function startAsciiid(port, mapRel) {
  const proc = spawn(ASCIIID, ['--cdp', String(port), '--map', mapRel],
    { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  return proc;
}

async function connectCdp(port) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try {
      const s = await new Promise((res, rej) => {
        const sock = net.connect({ host: '127.0.0.1', port }, () => { sock.setTimeout(0); res(sock); });
        sock.once('error', rej);
        sock.setTimeout(1000, () => { sock.destroy(); rej(new Error('t')); });
      });
      s.setEncoding('utf8');
      return s;
    } catch (_) { await sleep(250); }
  }
  throw new Error('CDP not ready');
}

class CdpClient {
  constructor(s) {
    this.s = s; this.nextId = 1; this.buf = ''; this.pending = new Map();
    s.on('data', c => this._data(c));
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
      const t = setTimeout(() => { this.pending.delete(id); reject(new Error(`t ${method}`)); }, timeout);
      this.pending.set(id, { resolve: v => { clearTimeout(t); resolve(v); }, reject: e => { clearTimeout(t); reject(e); } });
      this.s.write(payload);
    });
  }
  close() { this.s.destroy(); }
}

async function captureFrame(client, ppmName) {
  const ppm = path.join(OUT_DIR, ppmName);
  try { if (fs.existsSync(ppm)) fs.unlinkSync(ppm); } catch (_) {}
  await client.request('CAPTURE_FRAME', ppm, 60000);
  const dl = Date.now() + 6000;
  while (Date.now() < dl) {
    if (fs.existsSync(ppm) && fs.statSync(ppm).size > 1024) break;
    await sleep(150);
  }
  return ppm;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, MAP_REL);
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log('connected; burning frames');
    for (let i = 0; i < 4; i++) {
      await captureFrame(client, `_burn_${i}.ppm`);
      try { fs.unlinkSync(path.join(OUT_DIR, `_burn_${i}.ppm`)); } catch (_) {}
    }
    log('capture: default startup (embed visible)');
    await captureFrame(client, 'A_default_visible.ppm');
    log('command: SET_TERMPP_EMBEDDED_VISIBLE 0');
    const r0 = await client.request('SET_TERMPP_EMBEDDED_VISIBLE', '0');
    log(`reply: ${r0.replace(/\n/g, ' | ')}`);
    for (let i = 0; i < 3; i++) {
      await captureFrame(client, `_warm_${i}.ppm`);
      try { fs.unlinkSync(path.join(OUT_DIR, `_warm_${i}.ppm`)); } catch (_) {}
    }
    log('capture: after embed hidden');
    await captureFrame(client, 'B_embed_hidden.ppm');
    log('command: SET_TERMPP_EMBEDDED_VISIBLE 1');
    const r1 = await client.request('SET_TERMPP_EMBEDDED_VISIBLE', '1');
    log(`reply: ${r1.replace(/\n/g, ' | ')}`);
    for (let i = 0; i < 3; i++) {
      await captureFrame(client, `_warm2_${i}.ppm`);
      try { fs.unlinkSync(path.join(OUT_DIR, `_warm2_${i}.ppm`)); } catch (_) {}
    }
    log('capture: after embed re-shown');
    await captureFrame(client, 'C_embed_reshown.ppm');
    log('DONE');
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(2); });
