// proof_fl4131_asciiid_cdp_preset_workflow.js
//
// FL-4131 headed ASCIIID workflow proof. Drives the real ASCIIID process via
// its CDP-style TCP command server, applies every available extended material
// preset through the editor command surface, checks undo/redo, then saves and
// reopens the map to verify the material glyph sidecar persists.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'phase_d', '2026-05-30');
const RECEIPT = path.join(OUT_DIR, 'phase_d_asciiid_cdp_preset_save_reopen.json');
const MAP_PATH = process.env.PROOF_MAP_PATH || '.run/fl4131_asciiid_cdp_all_presets.a3d';
const MATERIAL_ID = parseInt(process.env.PROOF_MATERIAL_ID || '1', 10);
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || String(47680 + (process.pid % 1000)), 10);
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '45000', 10);
const GENERATED_PRESETS = path.join(REPO_ROOT, 'assets', 'glyphs', 'generated', 'material_shape_presets.json');

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function log(msg) { process.stderr.write(`[proof-fl4131-asciiid-cdp] ${msg}\n`); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function expectedGeneratedPresetCount() {
  const doc = JSON.parse(fs.readFileSync(GENERATED_PRESETS, 'utf8'));
  if (!Array.isArray(doc.presets)) throw new Error(`generated preset JSON has no presets array: ${GENERATED_PRESETS}`);
  return doc.presets.length;
}

function startAsciiid(port) {
  const proc = spawn(ASCIIID, ['--cdp', String(port)], {
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
        resolve: value => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: err => {
          clearTimeout(timer);
          reject(err);
        },
      });
      this.socket.write(payload);
    });
  }

  close() {
    this.socket.destroy();
  }
}

function parsePresets(result) {
  const rows = [];
  const re = /FL4131_PRESET index=(\d+) family=([^ ]+) name=(.*?) available=(\d+) first_missing=(\d+) count=(\d+)/g;
  let match;
  while ((match = re.exec(result))) {
    rows.push({
      index: Number(match[1]),
      family: match[2],
      name: match[3],
      available: match[4] === '1',
      first_missing: Number(match[5]),
      count: Number(match[6]),
    });
  }
  return rows;
}

function parseMaterialDump(result) {
  const marker = 'FL4131_MATERIAL_GLYPHS ';
  const start = result.indexOf(marker);
  if (start < 0) throw new Error(`material glyph dump marker missing: ${result}`);
  const jsonStart = result.indexOf('{', start);
  const jsonEnd = result.indexOf('\n', jsonStart);
  const raw = result.slice(jsonStart, jsonEnd >= 0 ? jsonEnd : undefined).trim();
  return JSON.parse(raw);
}

function parsePreviewResolve(result) {
  const marker = 'FL4131_MATERIAL_PREVIEW_RESOLVE ';
  const start = result.indexOf(marker);
  if (start < 0) throw new Error(`material preview resolve marker missing: ${result}`);
  const jsonStart = result.indexOf('{', start);
  const jsonEnd = result.indexOf('\n', jsonStart);
  const raw = result.slice(jsonStart, jsonEnd >= 0 ? jsonEnd : undefined).trim();
  return JSON.parse(raw);
}

function parseTooltip(result) {
  const marker = 'FL4131_PRESET_TOOLTIP ';
  const start = result.indexOf(marker);
  if (start < 0) throw new Error(`preset tooltip marker missing: ${result}`);
  const lineEnd = result.indexOf('\n', start);
  const line = result.slice(start, lineEnd >= 0 ? lineEnd : undefined);
  const match = /^FL4131_PRESET_TOOLTIP index=(\d+) available=(\d+) text=(.*)$/.exec(line.trim());
  if (!match) throw new Error(`preset tooltip line did not parse: ${line}`);
  return {
    index: Number(match[1]),
    available: match[2] === '1',
    text: match[3],
  };
}

function stateKey(dump) {
  return JSON.stringify({
    glyph_ids: dump.glyph_ids,
    fallback_bytes: dump.fallback_bytes,
  });
}

function countExtendedCells(dump) {
  let count = 0;
  for (const row of dump.glyph_ids || [])
    for (const id of row)
      if (id > 255 && id < 0xFFFFFFFE) count++;
  return count;
}

function firstRow(dump, key) {
  return (dump[key] && dump[key][0]) || [];
}

async function waitForFile(filePath, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(filePath)) return;
    await sleep(100);
  }
  throw new Error(`timed out waiting for ${filePath}`);
}

async function driveWorkflow(client, captureDir) {
  const expectedPresetCount = expectedGeneratedPresetCount();
  const listResult = await client.request('FL4131_LIST_EXTENDED_PRESETS');
  const presets = parsePresets(listResult);
  if (presets.length !== expectedPresetCount) throw new Error(`expected ${expectedPresetCount} generated shape presets, got ${presets.length}`);
  const disabled = presets.filter(p => !p.available);
  if (disabled.length) throw new Error(`presets unexpectedly disabled: ${JSON.stringify(disabled)}`);

  const tooltips = [];
  for (const preset of presets) {
    const tooltip = parseTooltip(await client.request('FL4131_DUMP_EXTENDED_PRESET_TOOLTIP', String(preset.index)));
    if (tooltip.index !== preset.index || tooltip.available !== preset.available) {
      throw new Error(`tooltip identity mismatch for preset ${preset.index}: ${JSON.stringify(tooltip)}`);
    }
    if (!tooltip.text.includes('GlyphIds/fallbacks:')) {
      throw new Error(`tooltip missing GlyphIds/fallbacks label for preset ${preset.index}: ${tooltip.text}`);
    }
    tooltips.push(tooltip);
  }

  let previousDump = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
  const originalKey = stateKey(previousDump);
  const applied = [];

  for (const preset of presets) {
    const applyResponse = await client.request('FL4131_APPLY_EXTENDED_PRESET', `${MATERIAL_ID} ${preset.index}`);
    if (!applyResponse.includes('FL4131 preset applied')) {
      throw new Error(`apply failed for preset ${preset.index}: ${applyResponse}`);
    }
    const afterApply = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
    const tooltip = tooltips.find(t => t.index === preset.index);
    const previewResolve = parsePreviewResolve(await client.request('FL4131_DUMP_MATERIAL_PREVIEW_RESOLVE', String(MATERIAL_ID)));
    const extendedCells = countExtendedCells(afterApply);
    if (extendedCells !== 64) {
      throw new Error(`preset ${preset.index} left ${extendedCells} extended cells, expected 64`);
    }
    if (
      previewResolve.extended_cells !== 64 ||
      previewResolve.coverage_cells !== 64 ||
      previewResolve.diagnostic_cells !== 0 ||
      previewResolve.display_not_fallback !== 64
    ) {
      throw new Error(`preset ${preset.index} preview resolve failed: ${JSON.stringify(previewResolve)}`);
    }
    for (const glyphId of firstRow(afterApply, 'glyph_ids')) {
      if (!tooltip || !tooltip.text.includes(`${glyphId}/0x`)) {
        throw new Error(`tooltip for preset ${preset.index} missing row glyph ${glyphId}: ${tooltip && tooltip.text}`);
      }
    }

    const undoResponse = await client.request('UNDO');
    const afterUndo = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
    if (!undoResponse.includes('Success: undone') || stateKey(afterUndo) !== stateKey(previousDump)) {
      throw new Error(`undo mismatch after preset ${preset.index}`);
    }

    const redoResponse = await client.request('REDO');
    const afterRedo = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
    if (!redoResponse.includes('Success: redone') || stateKey(afterRedo) !== stateKey(afterApply)) {
      throw new Error(`redo mismatch after preset ${preset.index}`);
    }

    applied.push({
      ...preset,
      apply_response: applyResponse.split('\n').find(line => line.includes('FL4131 preset applied')) || applyResponse.trim(),
      undo_response: undoResponse.split('\n').find(line => line.includes('Success: undone')) || undoResponse.trim(),
      redo_response: redoResponse.split('\n').find(line => line.includes('Success: redone')) || redoResponse.trim(),
      extended_cells: extendedCells,
      preview_extended_cells: previewResolve.extended_cells,
      preview_coverage_cells: previewResolve.coverage_cells,
      preview_diagnostic_cells: previewResolve.diagnostic_cells,
      preview_display_not_fallback: previewResolve.display_not_fallback,
      preview_row0_glyphs: firstRow(previewResolve, 'preview_glyphs'),
      row0_glyph_ids: firstRow(afterApply, 'glyph_ids'),
      row0_fallback_bytes: firstRow(afterApply, 'fallback_bytes'),
      tooltip_text: tooltip ? tooltip.text : '',
      tooltip_has_glyph_fallback_pairs: !!tooltip && firstRow(afterApply, 'glyph_ids').every(id => tooltip.text.includes(`${id}/0x`)),
    });
    previousDump = afterApply;
  }

  const saveResponse = await client.request('SAVE_MAP', MAP_PATH, 60000);
  if (!saveResponse.includes('Map saved')) throw new Error(`save failed: ${saveResponse}`);

  const captureResponse = await client.request('CAPTURE_CLEAN_FRAME', captureDir, 30000);
  const capturePng = path.join(captureDir, 'frame.png');
  await waitForFile(capturePng);

  const quitPromise = client.request('QUIT').catch(() => '');
  await quitPromise;
  return {
    presets,
    tooltips,
    applied,
    original_key: originalKey,
    final_dump: previousDump,
    capture_response: captureResponse.split('\n').find(line => line.includes('CAPTURE_CLEAN_FRAME')) || captureResponse.trim(),
    screenshot: path.relative(REPO_ROOT, capturePng),
    save_response: saveResponse.split('\n').find(line => line.includes('Map saved')) || saveResponse.trim(),
  };
}

async function run() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const fullMap = path.join(REPO_ROOT, MAP_PATH);
  const sidecarPath = `${fullMap}.glyph_profile.json`;
  const captureDir = path.join(OUT_DIR, 'asciiid_clean_after_final_preset');
  fs.rmSync(fullMap, { force: true });
  fs.rmSync(sidecarPath, { force: true });
  fs.rmSync(captureDir, { recursive: true, force: true });

  let proc = null;
  let client = null;
  let workflow;
  try {
    proc = startAsciiid(CDP_PORT);
    client = new CdpClient(await connectCdp(CDP_PORT));
    workflow = await driveWorkflow(client, captureDir);
  } finally {
    if (client) client.close();
    if (proc && !proc.killed) proc.kill('SIGTERM');
    await sleep(500);
  }

  if (!fs.existsSync(fullMap)) throw new Error(`saved map missing: ${fullMap}`);
  if (!fs.existsSync(sidecarPath)) throw new Error(`saved sidecar missing: ${sidecarPath}`);
  const sidecar = JSON.parse(fs.readFileSync(sidecarPath, 'utf8'));
  const sidecarCells = (sidecar.material_entries || []).reduce((acc, entry) => acc + ((entry.cells || []).length), 0);
  if (sidecarCells !== 64) throw new Error(`sidecar cell count ${sidecarCells}, expected 64`);

  const reopenPort = CDP_PORT + 1;
  let reopenProc = null;
  let reopenClient = null;
  let loadResponse = '';
  let reopenedDump = null;
  try {
    reopenProc = startAsciiid(reopenPort);
    reopenClient = new CdpClient(await connectCdp(reopenPort));
    loadResponse = await reopenClient.request('LOAD_MAP', MAP_PATH, 60000);
    reopenedDump = parseMaterialDump(await reopenClient.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
    await reopenClient.request('QUIT').catch(() => '');
  } finally {
    if (reopenClient) reopenClient.close();
    if (reopenProc && !reopenProc.killed) reopenProc.kill('SIGTERM');
    await sleep(500);
  }

  const reopenedMatches = stateKey(reopenedDump) === stateKey(workflow.final_dump);
  if (!reopenedMatches) throw new Error('reopened material glyph state did not match saved final preset');

  const receipt = {
    schema: 'fl4131_asciiid_cdp_preset_save_reopen.v4',
    verdict: 'PASS',
    generated_at: new Date().toISOString(),
    commit_under_test: currentCommit(),
    transport: 'headed_asciiid_cdp',
    cdp_port: CDP_PORT,
    material_id: MATERIAL_ID,
    map_path: MAP_PATH,
    sidecar_path: path.relative(REPO_ROOT, sidecarPath),
    screenshot: workflow.screenshot,
    listed_presets: workflow.presets.length,
    all_listed_presets_available: workflow.presets.every(p => p.available),
    applied_preset_count: workflow.applied.length,
    all_preview_resolves_passed: workflow.applied.every(p =>
      p.preview_extended_cells === 64 &&
      p.preview_coverage_cells === 64 &&
      p.preview_diagnostic_cells === 0 &&
      p.preview_display_not_fallback === 64),
    all_tooltips_show_glyph_fallback_pairs: workflow.applied.every(p => p.tooltip_has_glyph_fallback_pairs),
    tooltip_samples: workflow.tooltips.slice(0, 4),
    applied_presets: workflow.applied,
    save_response: workflow.save_response,
    capture_response: workflow.capture_response,
    load_response: loadResponse.split('\n').find(line => line.includes('Map loaded') || line.includes('Material glyph sidecar loaded')) || loadResponse.trim(),
    sidecar_cells: sidecarCells,
    reopened_matches_saved: reopenedMatches,
    final_row0_glyph_ids: firstRow(workflow.final_dump, 'glyph_ids'),
    reopened_row0_glyph_ids: firstRow(reopenedDump, 'glyph_ids'),
    limits: [
      'This is a headed ASCIIID CDP editor workflow receipt for all available extended presets.',
      'It does not prove native GL headed rendering or VPS two-tab multiplayer closure.',
    ],
  };
  fs.writeFileSync(RECEIPT, `${JSON.stringify(receipt, null, 2)}\n`);
  process.stderr.write(`[proof-fl4131-asciiid-cdp] wrote ${path.relative(REPO_ROOT, RECEIPT)}\n`);
}

run().catch(err => {
  process.stderr.write(`[proof-fl4131-asciiid-cdp] ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});
