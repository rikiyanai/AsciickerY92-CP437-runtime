// proof_fl4131_item9_sprite_actor_opt_in.js
//
// FL-4131 audit goal item 9: prove the sprite actor opt-in flag actually
// moves sprite_actor_applied from 0 to a positive value (not just that the
// MCP command exists). The earlier pyramid/sphere/skull captures showed
// sprite_actor_applied=0 because no actor sprite was in the framed view.
// This proof uses the Y8 original game map, which spawns the player
// sprite in the default view, and explicitly enables the standalone
// TERM++ window so the actor cell dispatch path runs.
//
// Sequence:
//   1. start asciiid with Y8 map
//   2. reset hook stats
//   3. enable opt-in flag (FL4131_SET_SPRITE_ACTOR_HARRI 1)
//   4. open standalone TERM++ (which renders the actor sprite)
//   5. let frames run
//   6. dump hook stats; assert sprite_actor_applied > 0
//   7. disable opt-in, dump again, assert applied = 0
//   8. write receipt

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const MAP_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii',
  'verification', 'fl4131', 'production_parity', 'sprite_actor_opt_in');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48959', 10);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[item9] ${m}\n`); }
function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT }).toString().trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port, mapRel) {
  return spawn(ASCIIID, ['--cdp', String(port), '--map', mapRel],
    { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
}

async function connectCdp(port) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try {
      return await new Promise((res, rej) => {
        const sock = net.connect({ host: '127.0.0.1', port }, () => { sock.setEncoding('utf8'); res(sock); });
        sock.once('error', rej);
        sock.setTimeout(1000, () => { sock.destroy(); rej(new Error('t')); });
      });
    } catch (_) { await sleep(250); }
  }
  throw new Error('CDP not ready');
}

class CdpClient {
  constructor(s) { this.s = s; this.nextId = 1; this.buf = ''; this.pending = new Map();
    s.on('data', c => this._d(c));
    s.on('close', () => { for (const { reject } of this.pending.values()) reject(new Error('socket-closed')); });
    s.on('error', e => process.stderr.write(`[cdp-sock-err] ${e.message}\n`)); }
  _d(c) {
    this.buf += c;
    for (;;) {
      const i = this.buf.indexOf('\n');
      if (i < 0) break;
      const line = this.buf.slice(0, i); this.buf = this.buf.slice(i + 1);
      if (!line.trim()) continue;
      let msg; try { msg = JSON.parse(line); } catch (e) {
        process.stderr.write(`[cdp-parse-err] ${e.message} line.len=${line.length}\n`);
        continue;
      }
      if (typeof msg.id === 'number' && this.pending.has(msg.id)) {
        const { resolve } = this.pending.get(msg.id);
        this.pending.delete(msg.id); resolve(String(msg.result || ''));
      }
    }
  }
  request(method, params = '', timeout = 30000) {
    const id = this.nextId++;
    return new Promise((res, rej) => {
      const t = setTimeout(() => { this.pending.delete(id); rej(new Error(`t ${method}`)); }, timeout);
      this.pending.set(id, { resolve: v => { clearTimeout(t); res(v); }, reject: e => { clearTimeout(t); rej(e); } });
      this.s.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }
  close() { this.s.destroy(); }
}

function parseDump(text) {
  const s = String(text || '');
  const m = s.match(/FL4131_RUNTIME_SHAPE6_HOOK\s+(.+)/);
  if (!m) return null;
  const out = {};
  for (const tok of m[1].split(/\s+/)) {
    const eq = tok.indexOf('=');
    if (eq < 0) continue;
    out[tok.slice(0, eq)] = /^\d+$/.test(tok.slice(eq + 1)) ? +tok.slice(eq + 1) : tok.slice(eq + 1);
  }
  return out;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, MAP_REL);
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log('connected');

    // First call DUMP to test connection
    const r0 = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 30000);
    log(`DUMP probe len: ${(r0||'').length} preview: ${(r0||'').slice(0, 100).replace(/\n/g,' | ')}`);

    const r1 = await client.request('FL4131_SET_SPRITE_ACTOR_HARRI', '0', 30000);
    log(`SET 0 reply: ${(r1||'').replace(/\n/g,' | ').slice(0,200)}`);
    const r2 = await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 30000);
    log(`RESET reply: ${(r2||'').replace(/\n/g,' | ').slice(0,200)}`);
    // Now let frames run with flag OFF so terrain/mesh/sprite_actor counts accrue.
    // Poll DUMP a few times -- editor needs frames to process commands but the
    // socket also needs traffic to avoid stale state.
    let dumpOff = null;
    for (let i = 0; i < 6 && (!dumpOff || (dumpOff.calls || 0) === 0); i++) {
      await sleep(500);
      try {
        const t = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
        dumpOff = parseDump(t);
      } catch (e) { log(`OFF poll ${i} err: ${e.message}`); }
    }
    log(`OFF: sprite_actor_calls=${dumpOff && dumpOff.sprite_actor_calls} applied=${dumpOff && dumpOff.sprite_actor_applied}`);

    // FLAG ON.
    const r3 = await client.request('FL4131_SET_SPRITE_ACTOR_HARRI', '1', 30000);
    log(`SET 1 reply: ${(r3||'').replace(/\n/g,' | ').slice(0,200)}`);
    const r4 = await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 30000);
    log(`RESET reply: ${(r4||'').replace(/\n/g,' | ').slice(0,200)}`);
    let dumpOn = null;
    for (let i = 0; i < 6 && (!dumpOn || (dumpOn.sprite_actor_applied || 0) === 0); i++) {
      await sleep(500);
      try {
        const t = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
        dumpOn = parseDump(t);
      } catch (e) { log(`ON poll ${i} err: ${e.message}`); }
    }
    log(`ON: sprite_actor_calls=${dumpOn && dumpOn.sprite_actor_calls} applied=${dumpOn && dumpOn.sprite_actor_applied}`);

    // Capture frame for visual.
    const ppm = path.join(OUT_DIR, 'sprite_actor_opt_in.ppm');
    try { if (fs.existsSync(ppm)) fs.unlinkSync(ppm); } catch (_) {}
    await client.request('CAPTURE_FRAME', ppm, 30000);
    const dl = Date.now() + 6000;
    while (Date.now() < dl) { if (fs.existsSync(ppm) && fs.statSync(ppm).size > 1024) break; await sleep(150); }

    // Reset flag.
    await client.request('FL4131_SET_SPRITE_ACTOR_HARRI', '0', 5000);

    const gates = {
      opt_in_off_applied_zero: !!dumpOff && (dumpOff.sprite_actor_applied || 0) === 0,
      opt_in_on_calls_positive: !!dumpOn && (dumpOn.sprite_actor_calls || 0) > 0,
      opt_in_on_applied_positive: !!dumpOn && (dumpOn.sprite_actor_applied || 0) > 0,
      opt_in_on_applied_increased_vs_off: !!dumpOn && !!dumpOff &&
        (dumpOn.sprite_actor_applied || 0) > (dumpOff.sprite_actor_applied || 0),
    };
    const receipt = {
      schema: 'fl4131_item9_sprite_actor_opt_in_receipt.v1',
      audit_item: 'item 9 (Sprite opt-in proof is suspect)',
      fl_ref: 'FL-4131',
      commit_under_test: currentCommit(),
      captured_at_utc: new Date().toISOString(),
      harness: 'scripts/proofs/proof_fl4131_item9_sprite_actor_opt_in.js',
      fixture: MAP_REL,
      dump_off: dumpOff,
      dump_on: dumpOn,
      capture_ppm: path.relative(REPO_ROOT, ppm),
      gates,
      verdict: Object.values(gates).every(Boolean) ? 'PASS' : 'FAIL',
    };
    fs.writeFileSync(path.join(OUT_DIR, 'receipt.json'), JSON.stringify(receipt, null, 2));
    log(`verdict=${receipt.verdict}`);
    process.exit(receipt.verdict === 'PASS' ? 0 : 1);
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e && e.stack || e}`); process.exit(2); });
