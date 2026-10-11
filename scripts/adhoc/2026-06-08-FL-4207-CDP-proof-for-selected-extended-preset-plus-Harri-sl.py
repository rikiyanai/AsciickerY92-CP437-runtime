// Ad hoc script: FL-4207 CDP proof for selected extended preset plus Harri slider TERM++ active cells
// Created: 2026-06-08
// Canonical gap: FL-4207 needs a canonical headed CDP operator proof for preset-locked TERM++ active cells.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO, '.run', 'asciiid');
const MAP = process.env.FL4207_MAP || 'assets/a3d/fl4131_shape_lab_20x20.a3d';
const OUT_DIR = process.env.FL4207_OUT_DIR || path.join(REPO, 'docs', 'research', 'ascii', 'verification', 'fl4207', 'operator_e2e_preset_harri');
const PORT = parseInt(process.env.FL4207_CDP_PORT || String(48900 + (process.pid % 200)), 10);
const READY_MS = parseInt(process.env.FL4207_READY_MS || '45000', 10);

const PRESET_0 = new Set([616,623,624,625,512,617,626,627,620,622,628,629,633,634,632,628]);
const PRESET_1 = new Set([544,545,542,543,626,627,628,629,512,632,568,569,520,521,546,617]);
const PRESETS = [PRESET_0, PRESET_1];

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function rel(p) { return path.relative(REPO, p); }
function sh(cmd) { return execSync(cmd, { cwd: REPO, encoding: 'utf8' }).trim(); }
function mkdirp(p) { fs.mkdirSync(p, { recursive: true }); }
function fail(msg) { const e = new Error(msg); e.fl4207 = true; throw e; }
function allFlatGlyphs(matDump) { return (matDump.glyph_ids || []).flat(); }
function sameArray(a, b) { return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]); }
function insideSet(values, set) { return values.length > 0 && values.every(v => set.has(Number(v))); }
function diffCount(a, b) { const n = Math.min(a.length, b.length); let d = Math.abs(a.length - b.length); for (let i = 0; i < n; i++) if (a[i] !== b[i]) d++; return d; }

function parseJsonMarker(text, marker) {
  if (typeof text !== 'string') fail(`missing text for ${marker}`);
  const lines = text.split('\n').filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    const at = line.indexOf(marker);
    if (at < 0) continue;
    const js = line.indexOf('{', at);
    if (js < 0) continue;
    return JSON.parse(line.slice(js));
  }
  fail(`missing marker ${marker}: ${text.slice(0, 500)}`);
}

function parseHook(text) {
  const line = String(text || '').split('\n').find(l => l.includes('FL4131_RUNTIME_SHAPE6_HOOK'));
  if (!line) fail('missing hook stats');
  const out = {};
  for (const m of line.matchAll(/([a-zA-Z0-9_]+)=(-?[0-9]+)/g)) out[m[1]] = Number(m[2]);
  return out;
}

function startAsciiid() {
  mkdirp(OUT_DIR);
  const outLog = fs.openSync(path.join(OUT_DIR, 'asciiid.stdout.log'), 'w');
  const errLog = fs.openSync(path.join(OUT_DIR, 'asciiid.stderr.log'), 'w');
  const env = Object.assign({}, process.env, {
    ASCIICKER_INITIAL_WINDOW_WIDTH: '1800',
    ASCIICKER_INITIAL_WINDOW_HEIGHT: '1200',
  });
  const proc = spawn(ASCIIID, ['--cdp', String(PORT), '--map', MAP], {
    cwd: REPO,
    env,
    stdio: ['ignore', outLog, errLog],
  });
  return { proc, outLog, errLog };
}

async function connect() {
  const deadline = Date.now() + READY_MS;
  let last = null;
  while (Date.now() < deadline) {
    try {
      return await new Promise((resolve, reject) => {
        const sock = net.connect({ host: '127.0.0.1', port: PORT }, () => {
          sock.setTimeout(0);
          sock.setEncoding('utf8');
          resolve(sock);
        });
        sock.once('error', reject);
        sock.setTimeout(1000, () => { sock.destroy(); reject(new Error('connect timeout')); });
      });
    } catch (err) {
      last = err;
      await sleep(250);
    }
  }
  fail(`CDP port ${PORT} not ready: ${last && last.message}`);
}

class Client {
  constructor(sock) {
    this.sock = sock;
    this.nextId = 1;
    this.buf = '';
    this.pending = new Map();
    sock.on('data', c => this.onData(c));
    sock.on('close', () => {
      for (const p of this.pending.values()) p.reject(new Error('cdp closed'));
      this.pending.clear();
    });
  }
  onData(c) {
    this.buf += c;
    for (;;) {
      const i = this.buf.indexOf('\n');
      if (i < 0) break;
      const line = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 1);
      if (!line.trim()) continue;
      let msg = null;
      try { msg = JSON.parse(line); } catch (_) { continue; }
      if (typeof msg.id === 'number' && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        p.resolve(String(msg.result || ''));
      }
    }
  }
  req(method, params = '', timeoutMs = 30000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params }) + '\n';
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`timeout ${method}`)); }, timeoutMs);
      this.pending.set(id, { resolve: v => { clearTimeout(timer); resolve(v); }, reject: e => { clearTimeout(timer); reject(e); } });
      this.sock.write(payload);
    });
  }
  close() { this.sock.destroy(); }
}

async function waitForFile(file, ms = 20000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try { if (fs.statSync(file).size > 0) return true; } catch (_) {}
    await sleep(200);
  }
  return false;
}

async function materialDump(client, matId) {
  return parseJsonMarker(await client.req('FL4131_DUMP_MATERIAL_GLYPHS', String(matId)), 'FL4131_MATERIAL_GLYPHS');
}
async function operatorState(client) {
  return parseJsonMarker(await client.req('FL4207_DUMP_OPERATOR_STATE'), 'FL4207_OPERATOR_STATE');
}
async function activeCells(client) {
  return parseJsonMarker(await client.req('FL4207_DUMP_RUNTIME_ACTIVE_CELLS'), 'FL4207_RUNTIME_ACTIVE_CELLS');
}
async function captureTerm(client, name) {
  const file = path.join(OUT_DIR, `${name}.png`);
  try { fs.unlinkSync(file); } catch (_) {}
  await client.req('CAPTURE_TERMPP_FRAME', file, 30000);
  if (!(await waitForFile(file))) fail(`TERM++ capture missing ${file}`);
  return file;
}

function assertStateDefaults(state) {
  if (state.restrict_to_selected_preset !== 1) fail('selected preset lock disabled');
  if (state.single_target_follows_active_material !== 1) fail('Shape target does not follow active material');
  if (state.active_target_enabled !== 1) fail('active material not enabled as paint target');
  if (state.live_sample_active !== 0) fail('selected cell source is active and would overwrite manual sliders');
  if (state.manual_source_vector_authoring !== 1) fail('manual source vector authoring disabled');
  if (state.live_paint !== 1) fail('live paint disabled');
}

async function assertRuntime(client, label, presetSet, receipt) {
  const cells = await activeCells(client);
  if (!cells.valid) fail(`${label}: runtime bridge invalid`);
  if (cells.active_cells <= 0) fail(`${label}: no active-material cells in TERM++ bridge`);
  if (cells.terrain_cells <= 0) fail(`${label}: no active-material terrain cells in TERM++ bridge`);
  if (cells.cpu_extended <= 0) fail(`${label}: no authored extended CPU winners for active material`);
  if (cells.mismatched_cpu !== 0) fail(`${label}: CPU winner outside selected preset`);
  if (cells.cpu_in_selected_preset !== cells.cpu_extended) fail(`${label}: selected preset count mismatch`);
  const badSample = (cells.samples || []).find(s => Number(s.cpu_gid) > 255 && !presetSet.has(Number(s.cpu_gid)));
  if (badSample) fail(`${label}: bad sample ${JSON.stringify(badSample)}`);
  receipt.runtime_steps.push({ label, cells });
  return cells;
}

async function main() {
  mkdirp(OUT_DIR);
  const receipt = {
    schema: 'fl4207_operator_e2e_preset_harri.v1',
    commit: sh('git rev-parse HEAD'),
    map: MAP,
    transport: 'headed_asciiid_cdp',
    cdp_port: PORT,
    verdict: 'FAIL',
    selected_preset_sets: {
      0: Array.from(PRESET_0),
      1: Array.from(PRESET_1),
    },
    states: [],
    material_steps: [],
    runtime_steps: [],
    captures: [],
    slider_steps: [],
    negative_gates: [],
  };
  let launched = null;
  let client = null;
  try {
    launched = startAsciiid();
    client = new Client(await connect());
    await sleep(1500);
    await client.req('FL4131_SHAPE_LAB_OPEN');
    await client.req('FL4131_RESET_RUNTIME_SHAPE6_HOOK');
    let state = await operatorState(client);
    assertStateDefaults(state);
    receipt.states.push({ label: 'fresh_shape_lab_open', state });
    const matId = state.active_material;
    if (!(matId >= 0 && matId < 256)) fail(`bad active material ${matId}`);

    await client.req('FL4131_APPLY_EXTENDED_PRESET', `${matId} 0`);
    state = await operatorState(client);
    if (state.selected_preset_index !== 0) fail('preset 0 not selected');
    let matDump = await materialDump(client, matId);
    let flat = allFlatGlyphs(matDump);
    if (!insideSet(flat, PRESET_0)) fail(`material ${matId} contains glyph outside preset 0`);
    receipt.material_steps.push({ label: 'after_preset_0', material_id: matId, glyphs: flat });

    await client.req('OPEN_TERMPP', 'harri=1', 30000);
    await client.req('SET_TERMPP_RUNTIME_HARRI_RESOLVE', '1');
    await sleep(2500);
    let cap = await captureTerm(client, 'termpp_preset0');
    receipt.captures.push({ label: 'termpp_preset0', path: rel(cap), bytes: fs.statSync(cap).size });
    await assertRuntime(client, 'preset0_initial', PRESET_0, receipt);

    const sliderOps = [
      { label: 'source_tl', cmd: 'FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', params: '1 0 0 0 0 0', set: PRESET_0 },
      { label: 'source_mr', cmd: 'FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', params: '0 0 0 1 0 0', set: PRESET_0 },
      { label: 'dense_weight', cmd: 'FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS', params: '1 1 1 1 1 5', set: PRESET_0 },
      { label: 'directional_gamma', cmd: 'FL4131_HARRI_SET_PIPELINE', params: 'dir_gamma=3.0 global_gamma=1.5 gpu_mode=1', set: PRESET_0 },
    ];
    let prevGlyphs = flat.slice();
    for (const op of sliderOps) {
      await client.req(op.cmd, op.params);
      await sleep(900);
      matDump = await materialDump(client, matId);
      flat = allFlatGlyphs(matDump);
      if (!insideSet(flat, op.set)) fail(`${op.label}: material left selected preset`);
      const d = diffCount(prevGlyphs, flat);
      state = await operatorState(client);
      const badCandidate = (state.candidate_glyph_ids || []).find(g => Number(g) > 0 && !op.set.has(Number(g)));
      if (badCandidate) fail(`${op.label}: candidate outside selected preset ${badCandidate}`);
      const cells = await assertRuntime(client, op.label, op.set, receipt);
      receipt.slider_steps.push({ label: op.label, command: op.cmd, params: op.params, material_diff_from_previous: d, glyphs: flat, candidate_glyph_ids: state.candidate_glyph_ids, runtime_active_cells: cells.active_cells });
      prevGlyphs = flat.slice();
    }

    await client.req('FL4131_APPLY_EXTENDED_PRESET', `${matId} 1`);
    await sleep(900);
    state = await operatorState(client);
    if (state.selected_preset_index !== 1) fail('preset 1 not selected');
    matDump = await materialDump(client, matId);
    flat = allFlatGlyphs(matDump);
    if (!insideSet(flat, PRESET_1)) fail(`material ${matId} contains glyph outside preset 1`);
    receipt.material_steps.push({ label: 'after_preset_1', material_id: matId, glyphs: flat });
    cap = await captureTerm(client, 'termpp_preset1');
    receipt.captures.push({ label: 'termpp_preset1', path: rel(cap), bytes: fs.statSync(cap).size });
    await assertRuntime(client, 'preset1_initial', PRESET_1, receipt);

    const reject = await client.req('FL4131_APPLY_EXTENDED_PRESET', `${matId} 2`);
    const rejected = reject.includes('[MCP] Error:') && reject.includes('disabled');
    receipt.negative_gates.push({ label: 'preset_2_rejected', rejected, response: reject.trim().split('\n').slice(-2) });
    if (!rejected) fail('preset index 2 was not rejected');

    const hook = parseHook(await client.req('FL4131_DUMP_RUNTIME_SHAPE6_HOOK'));
    receipt.hook_stats = hook;
    if (hook.calls <= 0) fail('runtime hook did not fire');
    if (hook.terrain_calls <= 0) fail('terrain hook did not fire');
    if (hook.extended_applied <= 0) fail('no extended glyphs applied by hook');

    receipt.verdict = 'PASS';
    receipt.expected_operator_result = [
      `Open ASCIIID on ${MAP}. Active material is mat ${matId}.`,
      'Click preset 0: active-material cells in TERM++ must use only preset 0 GlyphIds.',
      'Move TL, MR, Dense, Directional gamma controls: material repaint stays inside preset 0, TERM++ active cells stay inside preset 0.',
      'Click preset 1: active-material cells in TERM++ switch to preset 1 GlyphIds.',
      'Preset index 2 is disabled for this proof lane.',
    ];
  } catch (err) {
    receipt.error = err && err.stack ? err.stack : String(err);
    receipt.verdict = 'FAIL';
  } finally {
    const receiptPath = path.join(OUT_DIR, 'receipt.json');
    fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2));
    if (client) client.close();
    if (launched && launched.proc && !launched.proc.killed) launched.proc.kill('SIGTERM');
    if (launched) { fs.closeSync(launched.outLog); fs.closeSync(launched.errLog); }
    console.log(`[fl4207-proof] receipt=${rel(receiptPath)} verdict=${receipt.verdict}`);
    if (receipt.verdict !== 'PASS') process.exit(1);
  }
}

main();
