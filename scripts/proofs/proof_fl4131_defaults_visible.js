// proof_fl4131_defaults_visible.js
//
// Capture .run/asciiid at startup with NO MCP setup beyond the map load.
// Confirms the FL-4131 changes (Shape Lab open, Live cell on, embedded
// TERM++ split visible) are visible by DEFAULT, so the user sees them
// the moment they launch .run/asciiid manually.
//
// CLI: node scripts/proofs/proof_fl4131_defaults_visible.js

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
// FL-4131 item 12: canonical original-Y8 path. The .run/upstream_y8/ duplicate
// is byte-identical (SHA 54c1373022eb...) but having two paths was the item-12
// drift violation. All proof scripts now use this one path.
const MAP_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification',
                          'fl4131', 'production_parity', 'defaults_visible');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48955', 10);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[defaults-visible] ${m}\n`); }

function startAsciiid(port, mapRel) {
  const proc = spawn(ASCIIID, ['--cdp', String(port), '--map', mapRel],
    { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  return proc;
}

async function connectCdp(port, deadlineMs = 60000) {
  const deadline = Date.now() + deadlineMs;
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
      const t = setTimeout(() => { this.pending.delete(id); reject(new Error(`t ${method}`)); }, timeout);
      this.pending.set(id, { resolve: v => { clearTimeout(t); resolve(v); }, reject: e => { clearTimeout(t); reject(e); } });
      this.s.write(payload);
    });
  }
  close() { this.s.destroy(); }
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, MAP_REL);
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log(`connected on :${CDP_PORT}; running with ZERO setup MCPs`);

    // Burn a few frames so any lazy-init code runs.
    for (let i = 0; i < 4; i++) {
      const tmp = path.join(OUT_DIR, `_burn_${i}.ppm`);
      try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch (_) {}
      await client.request('CAPTURE_FRAME', tmp, 60000);
      const dl = Date.now() + 3000;
      while (Date.now() < dl) {
        if (fs.existsSync(tmp) && fs.statSync(tmp).size > 1024) break;
        await sleep(120);
      }
      try { fs.unlinkSync(tmp); } catch (_) {}
    }

    // Final capture with NO MCP setup -- this is what the user sees on launch.
    const ppm = path.join(OUT_DIR, 'asciiid_default_startup.ppm');
    try { if (fs.existsSync(ppm)) fs.unlinkSync(ppm); } catch (_) {}
    await client.request('CAPTURE_FRAME', ppm, 60000);
    const dl = Date.now() + 6000;
    while (Date.now() < dl) {
      if (fs.existsSync(ppm) && fs.statSync(ppm).size > 1024) break;
      await sleep(150);
    }
    log(`captured: ${ppm} size=${fs.statSync(ppm).size}`);
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(2); });
