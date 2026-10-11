// proof_fl4131_shape_lab_asciiid.js
//
// FL-4131 Task 5 headed ASCIIID Shape Lab UX proof.
//
// This is a runtime/headed observation of the real ASCIIID editor. It is NOT a
// source-grep test. The proof must FAIL before the Shape Lab panel exists, and
// it must fail by observing the running editor over CDP -- not by reading
// source.
//
// What it does:
//   1. Launches `.run/asciiid --cdp <port>`.
//   2. Connects to the JSON-RPC TCP command server.
//   3. Loads `assets/a3d/fl4131_shape_lab_20x20.a3d` (the isolated shape-lab fixture).
//   4. Probes the MCP command surface the Shape Lab UX is contracted to expose:
//        - FL4131_SHAPE_LAB_OPEN
//        - FL4131_SHAPE_LAB_DUMP_STATE
//        - FL4131_SHAPE_LAB_SET_SOURCE_VECTOR
//        - FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS
//        - FL4131_SHAPE_LAB_SET_REPERTOIRE
//      For each, it records whether the editor responded with a positive marker
//      or with an `[MCP] Error:` line indicating the command is missing/broken.
//   5. If FL4131_SHAPE_LAB_SET_SOURCE_VECTOR + FL4131_SHAPE_LAB_DUMP_STATE both
//      look available, attempts two-vector mutation and checks the shape6
//      candidate column for at least one changed cell between dumps. This is
//      the "change-detection" gate the plan doc calls for.
  //   6. Applies opt-in extended-glyph presets to the shape-lab fixture so the
  //      capture shows the comparison lane instead of only default CP437.
  //   7. CAPTURE_UI_FRAME captures the full composited frame (game + ImGui
//      panels) so the operator can do real visual inspection of the editor
//      state on this commit.
//   8. Writes
//      docs/research/ascii/verification/fl4131/shape_lab/shape_lab_asciiid_receipt.json
//      with verdict, commit_under_test, panel observations, runtime comparison
//      state, and the screenshot path.
//
// Verdict policy:
//   PASS  - all five MCP commands respond positively, change-detection shows
//           at least one candidate cell changed between two distinct source
//           vectors, the fixture receives opt-in extended presets, and a
//           headed screenshot is captured.
//   FAIL  - any one of the above is not satisfied. The receipt records WHICH
//           observation failed so the next implementer knows the gap.
//
// This proof intentionally does NOT call FL-4131 closure. It is a UX presence
// observation; production-renderer claims live elsewhere.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const FIXTURE_REL = 'assets/a3d/fl4131_shape_lab_20x20.a3d';
const FIXTURE_ABS = path.join(REPO_ROOT, FIXTURE_REL);
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'shape_lab');
const RECEIPT = path.join(OUT_DIR, 'shape_lab_asciiid_receipt.json');
const CAPTURE_DIR = path.join(OUT_DIR, 'asciiid_shape_lab_ui_frame');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || String(48700 + (process.pid % 200)), 10);
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '45000', 10);

const PROBE_COMMANDS = [
  { method: 'FL4131_SHAPE_LAB_OPEN', params: '', purpose: 'open Shape Lab panel' },
  { method: 'FL4131_SHAPE_LAB_DUMP_STATE', params: '', purpose: 'dump CP437/checkpoint/shape6 comparison state' },
  { method: 'FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', params: '0.20 0.20 0.50 0.50 0.80 0.80', purpose: 'set six-region source vector tl/tr/ml/mr/bl/br' },
  { method: 'FL4131_SHAPE_LAB_SET_ROLE_WEIGHTS', params: '1.0 1.0 1.0 1.0 1.0 1.0', purpose: 'set role/scoring weights' },
  { method: 'FL4131_SHAPE_LAB_SET_REPERTOIRE', params: 'arabic math shapes box', purpose: 'set repertoire/language filters' },
];

const VECTOR_A = '0.10 0.10 0.10 0.10 0.10 0.10';
const VECTOR_B = '0.90 0.10 0.90 0.10 0.90 0.10';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(msg) { process.stderr.write(`[proof-fl4131-shape-lab] ${msg}\n`); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port) {
  // Launch with the shape-lab fixture as the startup map so the editor's
  // primary terrain pointer owns the fixture's 400 patches and the runtime
  // renderer actually has something to draw.
  const proc = spawn(ASCIIID, ['--cdp', String(port), '--map', FIXTURE_REL], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  return proc;
}

async function connectCdp(port) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      const socket = await new Promise((resolve, reject) => {
        const s = net.connect({ host: '127.0.0.1', port }, () => {
          s.setTimeout(0);
          resolve(s);
        });
        s.once('error', reject);
        s.setTimeout(1000, () => {
          s.destroy();
          reject(new Error('connect timeout'));
        });
      });
      socket.setEncoding('utf8');
      return socket;
    } catch (err) {
      lastErr = err;
      await sleep(250);
    }
  }
  throw new Error(`CDP port ${port} did not become ready: ${lastErr && lastErr.message}`);
}

class CdpClient {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.buffer = '';
    this.pending = new Map();
    socket.on('data', chunk => this._onData(chunk));
    socket.on('error', err => {
      for (const { reject } of this.pending.values()) reject(err);
      this.pending.clear();
    });
    socket.on('close', () => {
      for (const { reject } of this.pending.values()) reject(new Error('CDP socket closed'));
      this.pending.clear();
    });
  }
  _onData(chunk) {
    this.buffer += chunk;
    for (;;) {
      const idx = this.buffer.indexOf('\n');
      if (idx < 0) break;
      const line = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 1);
      if (!line.trim()) continue;
      let msg;
      try { msg = JSON.parse(line); } catch (_) { continue; }
      if (typeof msg.id === 'number' && this.pending.has(msg.id)) {
        const { resolve } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        resolve(String(msg.result || ''));
      }
    }
  }
  request(method, params = '', timeoutMs = 30000) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params }) + '\n';
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`timeout waiting for ${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: v => { clearTimeout(timer); resolve(v); },
        reject: e => { clearTimeout(timer); reject(e); },
      });
      this.socket.write(payload);
    });
  }
  close() { this.socket.destroy(); }
}

function classifyResponse(text) {
  // Heuristic for "the editor recognised this MCP command and ran it":
  // - any line that starts with `[MCP] Error:` indicates the command was
  //   parsed but rejected (missing arg, bad value, or completely unknown).
  // - a missing positive `[MCP]` line means no handler accepted it at all.
  if (typeof text !== 'string') return { available: false, reason: 'no response' };
  const lines = text.split('\n').filter(s => s.trim().length);
  let sawPositive = false;
  let sawError = false;
  let errorLine = '';
  for (const line of lines) {
    if (/^\[MCP\]\s+Error[:\s]/i.test(line)) { sawError = true; errorLine = line.trim(); }
    else if (/^\[MCP\]\s+FL4131_SHAPE_LAB_/i.test(line)) { sawPositive = true; }
  }
  if (sawPositive && !sawError) return { available: true, reason: 'positive marker', sample: lines.slice(0, 4) };
  if (sawError) return { available: false, reason: `MCP error: ${errorLine}`, sample: lines.slice(0, 4) };
  return { available: false, reason: 'no Shape Lab marker emitted', sample: lines.slice(0, 4) };
}

async function waitForFile(filePath, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(filePath)) return true;
    await sleep(150);
  }
  return false;
}

function parseShapeLabDump(text) {
  // Reserved for the future implementation. Expected format (when the editor
  // supports it):
  //   [MCP] FL4131_SHAPE_LAB_STATE { ...json... }
  // We extract the JSON body. If the marker is missing, return null so the
  // proof can record `dump_unavailable` rather than crashing.
  if (typeof text !== 'string') return null;
  const marker = 'FL4131_SHAPE_LAB_STATE';
  const start = text.indexOf(marker);
  if (start < 0) return null;
  const jsonStart = text.indexOf('{', start);
  if (jsonStart < 0) return null;
  const jsonEnd = text.indexOf('\n', jsonStart);
  const raw = text.slice(jsonStart, jsonEnd >= 0 ? jsonEnd : undefined).trim();
  try { return JSON.parse(raw); } catch (_) { return null; }
}

function candidateColumn(dump) {
  if (!dump) return null;
  if (Array.isArray(dump.shape6_candidate_column)) return dump.shape6_candidate_column;
  if (Array.isArray(dump.candidate_column)) return dump.candidate_column;
  if (Array.isArray(dump.candidates)) return dump.candidates;
  return null;
}

function diffCandidateCells(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return -1;
  let n = 0;
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) if (a[i] !== b[i]) n++;
  return n;
}

async function probePanel(client) {
  const probes = [];
  for (const cmd of PROBE_COMMANDS) {
    try {
      const response = await client.request(cmd.method, cmd.params);
      probes.push({
        method: cmd.method,
        params: cmd.params,
        purpose: cmd.purpose,
        classification: classifyResponse(response),
        response_lines: response.split('\n').slice(0, 6),
      });
    } catch (err) {
      probes.push({
        method: cmd.method,
        params: cmd.params,
        purpose: cmd.purpose,
        classification: { available: false, reason: `request error: ${err && err.message}` },
        response_lines: [],
      });
    }
  }
  return probes;
}

async function attemptChangeDetection(client) {
  // Only meaningful if both SET_SOURCE_VECTOR and DUMP_STATE work; otherwise
  // record `skipped` with the reason.
  let dumpA;
  let dumpB;
  let setA;
  let setB;
  try {
    setA = await client.request('FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', VECTOR_A);
    dumpA = await client.request('FL4131_SHAPE_LAB_DUMP_STATE');
    setB = await client.request('FL4131_SHAPE_LAB_SET_SOURCE_VECTOR', VECTOR_B);
    dumpB = await client.request('FL4131_SHAPE_LAB_DUMP_STATE');
  } catch (err) {
    return { skipped: true, reason: `MCP error during change-detection: ${err && err.message}` };
  }
  const parsedA = parseShapeLabDump(dumpA);
  const parsedB = parseShapeLabDump(dumpB);
  if (!parsedA || !parsedB) {
    return {
      skipped: true,
      reason: 'no FL4131_SHAPE_LAB_STATE marker in dump output (panel not implemented)',
      set_response_a_first_line: (setA || '').split('\n')[0] || '',
      set_response_b_first_line: (setB || '').split('\n')[0] || '',
      dump_response_a_first_line: (dumpA || '').split('\n')[0] || '',
      dump_response_b_first_line: (dumpB || '').split('\n')[0] || '',
    };
  }
  const candA = candidateColumn(parsedA);
  const candB = candidateColumn(parsedB);
  const changed = diffCandidateCells(candA, candB);
  return {
    skipped: false,
    candidate_column_a: candA,
    candidate_column_b: candB,
    changed_candidate_cells: changed,
    pass: changed > 0,
  };
}

async function run() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  if (!fs.existsSync(FIXTURE_ABS)) throw new Error(`missing fixture: ${FIXTURE_REL}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.rmSync(CAPTURE_DIR, { recursive: true, force: true });
  fs.mkdirSync(CAPTURE_DIR, { recursive: true });

  // The fixture has an authoring-metadata sidecar
  // (schema fl4131_shape_lab_glyph_profile_v0) at
  // assets/a3d/fl4131_shape_lab_20x20.a3d.glyph_profile.json. The editor's
  // --map loader expects a RUNTIME material-glyph sidecar
  // (extended_material_glyph_v1, with sidecar_version). The two contracts
  // share the .glyph_profile.json suffix. Temporarily move the authoring
  // sidecar aside so the editor's --map path loads cleanly; restore after.
  const sidecarOrig = FIXTURE_ABS + '.glyph_profile.json';
  const sidecarStash = sidecarOrig + '.shape_lab_stash';
  let sidecarStashed = false;
  if (fs.existsSync(sidecarOrig)) {
    fs.renameSync(sidecarOrig, sidecarStash);
    sidecarStashed = true;
    log(`stashed authoring sidecar -> ${path.basename(sidecarStash)}`);
  }

  let proc = null;
  let client = null;
  let loadResponse = '';
  let probes = [];
  let changeDetection = { skipped: true, reason: 'not attempted' };
  let captureResponse = '';
  let screenshotPath = null;
  let asciiid_launched = false;
  let cdp_connected = false;
  let fixture_loaded = false;
  let error_text = null;
  let presetPaintResponses = [];
  let cameraResponse = '';
  try {
    proc = startAsciiid(CDP_PORT);
    asciiid_launched = true;
    client = new CdpClient(await connectCdp(CDP_PORT));
    cdp_connected = true;
    loadResponse = await client.request('LOAD_MAP', FIXTURE_REL, 60000);
    fixture_loaded = /Map loaded|loaded/i.test(loadResponse);
    probes = await probePanel(client);
    changeDetection = await attemptChangeDetection(client);
    // Point the camera at the 20x20 fixture so the SPHERE / PYRAMID / SKULL
    // mesh instances are actually inside the captured viewport. After
    // LOAD_MAP, the default editor pose sits far above the small fixture and
    // the right half of the frame is empty.
    // Fixture is 20x20 patches (160x160 visual cells) centered at (0,0); heights
    // 20-121. Try a sequence of camera placements over the fixture and dump
    // each one's response so we can SEE which one is active in the capture.
    // PAINT THE WORLD with extended-glyph presets BEFORE capture. This is
    // what makes the rendered terrain show Arabic / math / shapes glyphs
    // instead of default CP437. Material id <- preset index mapping is from
    // the generated preset table:
    //   mat 1 (GRASS)  <- preset 0 (soft_top_curve)
    //   mat 3 (WATER)  <- preset 1 (curve_current)
    //   mat 2 (DIRT)   <- preset 4 (horizontal_bands)
    //   mat 4 (STONE)  <- preset 3 (vertical_angular)
    const presetPaints = [
      { mat: 1, preset: 0, label: 'GRASS->soft_top_curve' },
      { mat: 3, preset: 1, label: 'WATER->curve_current' },
      { mat: 2, preset: 4, label: 'DIRT->horizontal_bands' },
      { mat: 4, preset: 3, label: 'STONE->vertical_angular' },
    ];
    const paintResponses = [];
    for (const p of presetPaints) {
      try {
        const r = await client.request('FL4131_APPLY_EXTENDED_PRESET', `${p.mat} ${p.preset}`, 15000);
        const ok = /FL4131 preset applied/.test(r);
        paintResponses.push({ ...p, ok, first_line: (r || '').split('\n').find(s => s.includes('FL4131')) || '' });
        log(`paint ${p.label}: ${ok ? 'OK' : 'FAIL'}`);
      } catch (err) {
        paintResponses.push({ ...p, ok: false, error: err && err.message });
      }
    }
    presetPaintResponses = paintResponses;

    try {
      // Zoom in close enough to actually see individual glyph pixels. The
      // fixture is 160 visual cells wide; at font_size=3.5 each cell is ~3px
      // (too small to read). Use a tight iso-ish view of the fixture center.
      // SET_CAMERA_VIEW <x> <y> <z> <yaw> <pitch> <font_size>
      // Meshes are at (-50,60), (0,60), (50,60) scale 6. Aim from south at the
      // y=60 plateau with a near-top angle so all three mesh instances are in
      // the frame and individual glyph cells are readable.
      cameraResponse = await client.request('SET_CAMERA_VIEW', '0 60 90 0 70 14', 15000);
      log(`SET_CAMERA_VIEW result: ${(cameraResponse || '').replace(/\n/g, ' | ').slice(0, 240)}`);
    } catch (err) {
      error_text = `SET_CAMERA_VIEW failed: ${err && err.message}`;
    }
    // Re-open the Shape Lab tab after the view change in case the tab focus
    // was reset.
    try { await client.request('FL4131_SHAPE_LAB_OPEN', '', 5000); } catch (_) {}
    // Small idle so the new camera renders before capture.
    await sleep(400);
    try {
      captureResponse = await client.request('CAPTURE_UI_FRAME', CAPTURE_DIR, 30000);
      const ui_png = path.join(CAPTURE_DIR, 'ui_frame.png');
      const ok = await waitForFile(ui_png);
      if (ok) screenshotPath = path.relative(REPO_ROOT, ui_png);
    } catch (err) {
      error_text = `CAPTURE_UI_FRAME failed: ${err && err.message}`;
    }
    try { await client.request('QUIT', '', 5000); } catch (_) { /* ignore */ }
  } catch (err) {
    error_text = (error_text ? error_text + '; ' : '') + `harness error: ${err && err.message}`;
  } finally {
    if (client) client.close();
    if (proc && !proc.killed) proc.kill('SIGTERM');
    await sleep(800);
    if (sidecarStashed && fs.existsSync(sidecarStash)) {
      fs.renameSync(sidecarStash, sidecarOrig);
      log(`restored authoring sidecar`);
    }
  }

  const required_methods = PROBE_COMMANDS.map(c => c.method);
  const available_methods = probes.filter(p => p.classification.available).map(p => p.method);
  const missing_methods = required_methods.filter(m => !available_methods.includes(m));
  const panel_ux_present = missing_methods.length === 0;
  const change_detection_pass = changeDetection && changeDetection.pass === true;
  const preset_paint_pass =
    presetPaintResponses.length === 4 &&
    presetPaintResponses.every(p => p.ok === true);
  const screenshot_captured = Boolean(screenshotPath);
  const verdict =
    (fixture_loaded && panel_ux_present && change_detection_pass && preset_paint_pass && screenshot_captured)
      ? 'PASS'
      : 'FAIL';

  const receipt = {
    schema: 'fl4131_shape_lab_asciiid_receipt.v1',
    scope:
      'Headed runtime CDP observation of ASCIIID. Does NOT prove production-renderer closure. ' +
      'Verdict is PASS only when the Shape Lab MCP surface is present AND a source-vector change ' +
      'mutates at least one shape6 candidate cell AND opt-in extended presets are applied before ' +
      'a headed screenshot is captured. UX validation is real headed observation, not source grep.',
    verdict,
    generated_at: new Date().toISOString(),
    commit_under_test: currentCommit(),
    transport: 'headed_asciiid_cdp',
    cdp_port: CDP_PORT,
    fixture: FIXTURE_REL,
    asciiid_binary: path.relative(REPO_ROOT, ASCIIID),
    asciiid_launched,
    cdp_connected,
    fixture_load_response_first_line: (loadResponse || '').split('\n').find(Boolean) || '',
    fixture_load_response_lines: (loadResponse || '').split('\n').slice(0, 8),
    fixture_loaded,
    panel_ux_present,
    required_mcp_methods: required_methods,
    available_mcp_methods: available_methods,
    missing_mcp_methods: missing_methods,
    probe_observations: probes,
    change_detection: changeDetection,
    preset_paint_pass,
    preset_paint_responses: presetPaintResponses,
    camera_view_response: (cameraResponse || '').split('\n').slice(0, 6),
    screenshot_captured,
    capture_ui_frame_response: (captureResponse || '').split('\n').find(s => s.includes('CAPTURE_UI_FRAME')) || (captureResponse || '').split('\n')[0] || '',
    screenshot: screenshotPath,
    screenshot_directory: path.relative(REPO_ROOT, CAPTURE_DIR),
    error: error_text,
    limits: [
      'This receipt is a UX presence observation. It does not validate production renderer fidelity.',
      'A PASS verdict only proves that the Shape Lab MCP surface responds, reacts to source-vector changes, applies opt-in extended presets to the fixture, and captures a headed screenshot; visual quality remains an operator inspection.',
      'A FAIL verdict means the Shape Lab UX is missing or incomplete; the receipt records which MCP method is unavailable.',
    ],
  };
  fs.writeFileSync(RECEIPT, JSON.stringify(receipt, null, 2) + '\n');
  log(`wrote ${path.relative(REPO_ROOT, RECEIPT)} verdict=${verdict}`);
  if (verdict !== 'PASS') {
    log(`missing methods: ${JSON.stringify(missing_methods)}`);
    if (changeDetection.skipped) log(`change-detection skipped: ${changeDetection.reason}`);
    process.exit(1);
  }
}

run().catch(err => {
  process.stderr.write(`[proof-fl4131-shape-lab] ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});
