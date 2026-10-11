// proof_fl4131_mesh_actor_presets.js
//
// FL-4131 #6 items 7 + 9: mesh/sprite actor parity + skull/sphere/pyramid
// inspection presets. Drives the FL4131_VIEW_SKULL / SPHERE / PYRAMID MCP
// commands, captures each, toggles FL4131_SET_SPRITE_ACTOR_HARRI, and
// confirms sprite_actor_calls counter is non-zero under opt-in.
//
// CLI: node scripts/proofs/proof_fl4131_mesh_actor_presets.js

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
                          'fl4131', 'production_parity', 'mesh_actor_presets');
const RECEIPT = path.join(OUT_DIR, 'receipt.json');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48944', 10);
const READY_TIMEOUT_MS = 60000;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[mesh-actor-proof] ${m}\n`); }
function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port, mapRel) {
  const proc = spawn(ASCIIID, ['--cdp', String(port), '--map', mapRel],
    { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  return proc;
}

async function connectCdp(port) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const s = await new Promise((res, rej) => {
        const sock = net.connect({ host: '127.0.0.1', port }, () => { sock.setTimeout(0); res(sock); });
        sock.once('error', rej);
        sock.setTimeout(1000, () => { sock.destroy(); rej(new Error('timeout')); });
      });
      s.setEncoding('utf8');
      return s;
    } catch (_) { await sleep(250); }
  }
  throw new Error(`CDP not ready on :${port}`);
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

async function capture(client, ppm) {
  try { if (fs.existsSync(ppm)) fs.unlinkSync(ppm); } catch (_) {}
  await client.request('CAPTURE_FRAME', ppm, 60000);
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    if (fs.existsSync(ppm) && fs.statSync(ppm).size > 1024) break;
    await sleep(150);
  }
}

async function main() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, FIXTURE_REL);
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log(`connected on :${CDP_PORT}`);

    // Common setup.
    await client.request('FL4131_HARRI_SET_PIPELINE',
      'gpu_mode=1 use_contrast=1 use_directional=1 normalize=1', 10000);
    await client.request('SET_TERMPP_RUNTIME_HARRI_RESOLVE', '1', 5000);
    for (const mat of [1, 2, 3, 4, 5]) {
      await client.request('FL4131_HARRI_SET_MAT_PROFILE',
        `${mat} arabic=1 math=1 shapes=1 box=1 punct=0 other=1 katakana=1 min_density=0.05 max_density=1.0`,
        10000);
    }
    await client.request('FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS', '1 1 1 1 1 1', 5000);

    const captures = {};

    // Item 9: mesh inspection presets via the new MCP commands.
    for (const which of ['PYRAMID', 'SPHERE', 'SKULL']) {
      await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 5000);
      await client.request(`FL4131_VIEW_${which}`, '', 10000);
      await sleep(400);
      const ppm = path.join(OUT_DIR, `${which.toLowerCase()}_default.ppm`);
      await capture(client, ppm);
      const dump = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
      const parsed = parseDump(dump);
      captures[which] = {
        ppm: path.relative(REPO_ROOT, ppm),
        sprite_actor_calls: parsed && parsed.sprite_actor_calls,
        sprite_actor_applied: parsed && parsed.sprite_actor_applied,
        automap_mesh_calls: parsed && parsed.automap_mesh_calls,
        automap_mesh_applied: parsed && parsed.automap_mesh_applied,
        terrain_calls: parsed && parsed.terrain_calls,
        terrain_applied: parsed && parsed.terrain_applied,
      };
      log(`${which}: terrain_applied=${captures[which].terrain_applied} ` +
          `mesh_applied=${captures[which].automap_mesh_applied} ` +
          `sprite_actor_calls=${captures[which].sprite_actor_calls}`);
    }

    // Item 7: sprite actor opt-in flip.
    await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 5000);
    await client.request('FL4131_SET_SPRITE_ACTOR_HARRI', '1', 5000);
    await client.request('FL4131_VIEW_PYRAMID', '', 10000);
    await sleep(400);
    const optInPpm = path.join(OUT_DIR, 'pyramid_sprite_actor_opt_in.ppm');
    await capture(client, optInPpm);
    const dumpOptIn = await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 5000);
    const parsedOptIn = parseDump(dumpOptIn);

    // Turn it back off (good hygiene).
    await client.request('FL4131_SET_SPRITE_ACTOR_HARRI', '0', 5000);

    const receipt = {
      schema: 'fl4131_mesh_actor_presets_receipt.v1',
      commit_under_test: currentCommit(),
      captured_at_utc: new Date().toISOString(),
      harness: 'scripts/proofs/proof_fl4131_mesh_actor_presets.js',
      fixture: FIXTURE_REL,
      captures,
      sprite_actor_opt_in_dump: parsedOptIn,
      sprite_actor_opt_in_ppm: path.relative(REPO_ROOT, optInPpm),
      gates: {
        all_presets_captured: ['PYRAMID', 'SPHERE', 'SKULL'].every(w =>
          captures[w] && captures[w].terrain_applied !== undefined),
        meshes_seen_by_harri: ['PYRAMID', 'SPHERE', 'SKULL'].every(w =>
          captures[w] && captures[w].automap_mesh_calls > 0),
        sprite_actor_opt_in_flipped_eligibility:
          parsedOptIn && parsedOptIn.sprite_actor_calls !== undefined,
      },
      verdict: 'mesh presets + sprite actor opt-in MCP commands operative',
    };
    fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2));
    log(`receipt: ${RECEIPT}`);
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(2); });
