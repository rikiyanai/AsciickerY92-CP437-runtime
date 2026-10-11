// proof_fl4131_asciiid_mouse_click_visible_grid.js
//
// Drives headed ASCIIID through the normal ImGui button hitbox: locate a real
// extended-preset button, send a mouse click at its visible rectangle, then
// verify the active material ramp changed to extended GlyphIds and capture the
// composited editor UI frame.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'phase_d', '2026-05-30');
const RECEIPT = path.join(OUT_DIR, 'phase_d_asciiid_mouse_click_visible_material_grid.json');
const MATERIAL_ID = parseInt(process.env.PROOF_MATERIAL_ID || '0', 10);
const PRESET_INDEX = parseInt(process.env.PROOF_PRESET_INDEX || '0', 10);
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || String(48680 + (process.pid % 1000)), 10);
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '45000', 10);

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function log(msg) { process.stderr.write(`[proof-fl4131-asciiid-mouse] ${msg}\n`); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
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

function parseMaterialDump(result) {
  const marker = 'FL4131_MATERIAL_GLYPHS ';
  const start = result.indexOf(marker);
  if (start < 0) throw new Error(`material glyph dump marker missing: ${result}`);
  const jsonStart = result.indexOf('{', start);
  const jsonEnd = result.indexOf('\n', jsonStart);
  return JSON.parse(result.slice(jsonStart, jsonEnd >= 0 ? jsonEnd : undefined).trim());
}

function parsePreviewResolve(result) {
  const marker = 'FL4131_MATERIAL_PREVIEW_RESOLVE ';
  const start = result.indexOf(marker);
  if (start < 0) throw new Error(`material preview resolve marker missing: ${result}`);
  const jsonStart = result.indexOf('{', start);
  const jsonEnd = result.indexOf('\n', jsonStart);
  return JSON.parse(result.slice(jsonStart, jsonEnd >= 0 ? jsonEnd : undefined).trim());
}

function parsePresetRects(result) {
  const rects = [];
  const re = /FL4131_PRESET_UI_RECT index=(\d+) family=([^ ]+) name=(.*?) valid=(\d+) x0=(-?\d+) y0=(-?\d+) x1=(-?\d+) y1=(-?\d+)/g;
  let match;
  while ((match = re.exec(result))) {
    rects.push({
      index: Number(match[1]),
      family: match[2],
      name: match[3],
      valid: match[4] === '1',
      x0: Number(match[5]),
      y0: Number(match[6]),
      x1: Number(match[7]),
      y1: Number(match[8]),
    });
  }
  return rects;
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

async function waitForPresetRect(client, presetIndex) {
  const deadline = Date.now() + 15000;
  let lastResult = '';
  while (Date.now() < deadline) {
    lastResult = await client.request('FL4131_DUMP_PRESET_UI_RECTS');
    const rects = parsePresetRects(lastResult);
    const rect = rects.find(r => r.index === presetIndex);
    if (rect && rect.valid && rect.x1 > rect.x0 && rect.y1 > rect.y0) return rect;
    await sleep(150);
  }
  throw new Error(`preset ${presetIndex} never exposed a valid visible UI rectangle: ${lastResult}`);
}

async function waitForFile(filePath, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(filePath) && fs.statSync(filePath).size > 0) return;
    await sleep(100);
  }
  throw new Error(`timed out waiting for ${filePath}`);
}

async function run() {
  if (!fs.existsSync(ASCIIID)) throw new Error(`missing ${ASCIIID}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const captureDir = path.join(OUT_DIR, 'asciiid_ui_after_mouse_click');
  fs.rmSync(captureDir, { recursive: true, force: true });

  let proc = null;
  let client = null;
  try {
    proc = startAsciiid(CDP_PORT);
    client = new CdpClient(await connectCdp(CDP_PORT));

    const before = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
    const beforeExtended = countExtendedCells(before);
    const rect = await waitForPresetRect(client, PRESET_INDEX);
    const click = {
      x: Math.floor((rect.x0 + rect.x1) / 2),
      y: Math.floor((rect.y0 + rect.y1) / 2),
    };
    log(`clicking preset ${PRESET_INDEX} ${rect.family}:${rect.name} at ${click.x},${click.y}`);

    const clickResponse = await client.request('RUN_MOUSE_CLICK_PROBE', `${click.x} ${click.y}`);
    if (!clickResponse.includes('RUN_MOUSE_CLICK_PROBE queued')) {
      throw new Error(`mouse click command failed: ${clickResponse}`);
    }

    let after = null;
    let afterExtended = 0;
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      await sleep(200);
      after = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
      afterExtended = countExtendedCells(after);
      if (afterExtended === 64 && stateKey(after) !== stateKey(before)) break;
    }
    if (!after || afterExtended !== 64 || stateKey(after) === stateKey(before)) {
      throw new Error(`mouse click did not author extended material cells: before=${beforeExtended} after=${afterExtended}`);
    }

    const preview = parsePreviewResolve(await client.request('FL4131_DUMP_MATERIAL_PREVIEW_RESOLVE', String(MATERIAL_ID)));
    if (
      preview.extended_cells !== 64 ||
      preview.coverage_cells !== 64 ||
      preview.diagnostic_cells !== 0 ||
      preview.display_not_fallback !== 64
    ) {
      throw new Error(`visible material preview resolve failed after mouse click: ${JSON.stringify(preview)}`);
    }

    const captureResponse = await client.request('CAPTURE_UI_FRAME', captureDir, 30000);
    const screenshotPath = path.join(captureDir, 'ui_frame.png');
    await waitForFile(screenshotPath);

    await client.request('QUIT').catch(() => '');

    const receipt = {
      schema: 'fl4131_asciiid_mouse_click_visible_material_grid.v1',
      verdict: 'PASS',
      generated_at: new Date().toISOString(),
      commit_under_test: currentCommit(),
      transport: 'headed_asciiid_cdp',
      cdp_port: CDP_PORT,
      material_id: MATERIAL_ID,
      preset_index: PRESET_INDEX,
      preset_family: rect.family,
      preset_name: rect.name,
      button_rect: rect,
      click_point: click,
      click_response: clickResponse.split('\n').find(line => line.includes('RUN_MOUSE_CLICK_PROBE queued')) || clickResponse.trim(),
      before_extended_cells: beforeExtended,
      after_extended_cells: afterExtended,
      before_row0_glyph_ids: firstRow(before, 'glyph_ids'),
      after_row0_glyph_ids: firstRow(after, 'glyph_ids'),
      after_row0_fallback_bytes: firstRow(after, 'fallback_bytes'),
      preview_extended_cells: preview.extended_cells,
      preview_coverage_cells: preview.coverage_cells,
      preview_diagnostic_cells: preview.diagnostic_cells,
      preview_display_not_fallback: preview.display_not_fallback,
      preview_row0_glyphs: firstRow(preview, 'preview_glyphs'),
      screenshot: path.relative(REPO_ROOT, screenshotPath),
      screenshot_bytes: fs.statSync(screenshotPath).size,
      capture_response: captureResponse.split('\n').find(line => line.includes('CAPTURE_UI_FRAME')) || captureResponse.trim(),
      proof_points: {
        real_visible_button_rect_used: true,
        actual_mouse_click_was_queued: true,
        active_material_ramp_received_extended_glyph_ids: afterExtended === 64,
        material_preview_resolved_to_extended_atlas_glyphs: preview.display_not_fallback === 64,
        composited_ui_frame_captured_after_click: true,
      },
      limits: [
        'This proves one headed ASCIIID visible preset button can be clicked through the normal mouse path.',
        'It does not close native, web, multiplayer, or unknown-glyph parity.',
      ],
    };
    fs.writeFileSync(RECEIPT, `${JSON.stringify(receipt, null, 2)}\n`);
    log(`wrote ${path.relative(REPO_ROOT, RECEIPT)}`);
  } finally {
    if (client) client.close();
    if (proc && !proc.killed) proc.kill('SIGTERM');
    await sleep(500);
  }
}

run().catch(err => {
  process.stderr.write(`[proof-fl4131-asciiid-mouse] ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});
