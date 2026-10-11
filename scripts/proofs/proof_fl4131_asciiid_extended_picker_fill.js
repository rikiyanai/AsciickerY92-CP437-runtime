// proof_fl4131_asciiid_extended_picker_fill.js
//
// Headed ASCIIID proof for individual extended-glyph authoring: click a visible
// manifest glyph swatch, click Fill Material, and verify the active material
// ramp is authored from that single extended GlyphId.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'phase_d', '2026-05-30');
const RECEIPT = path.join(OUT_DIR, 'phase_d_asciiid_extended_picker_fill_material.json');
const MATERIAL_ID = parseInt(process.env.PROOF_MATERIAL_ID || '0', 10);
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || String(49680 + (process.pid % 1000)), 10);

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function log(msg) { process.stderr.write(`[proof-fl4131-asciiid-picker] ${msg}\n`); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port) {
  const proc = spawn(ASCIIID, ['--cdp', String(port)], { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[asciiid-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  return proc;
}

async function connectCdp(port) {
  const deadline = Date.now() + 45000;
  let lastErr = null;
  while (Date.now() < deadline) {
    try {
      return await new Promise((resolve, reject) => {
        const s = net.connect({ host: '127.0.0.1', port }, () => {
          s.setEncoding('utf8');
          s.setTimeout(0);
          resolve(s);
        });
        s.once('error', reject);
        s.setTimeout(1000, () => {
          s.destroy();
          reject(new Error('connect timeout'));
        });
      });
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
    socket.on('error', err => this._rejectAll(err));
    socket.on('close', () => this._rejectAll(new Error('CDP socket closed')));
  }
  _rejectAll(err) {
    for (const { reject } of this.pending.values()) reject(err);
    this.pending.clear();
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
  close() { this.socket.destroy(); }
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

function parsePickerRects(result) {
  const re = /FL4131_EXTENDED_PICKER_UI_RECTS sidebar_tab=(-?\d+) extended_frames=(\d+) first_glyph_id=(\d+) first_valid=(\d+) first_x0=(-?\d+) first_y0=(-?\d+) first_x1=(-?\d+) first_y1=(-?\d+) fill_valid=(\d+) fill_x0=(-?\d+) fill_y0=(-?\d+) fill_x1=(-?\d+) fill_y1=(-?\d+)/;
  const m = re.exec(result);
  if (!m) throw new Error(`picker rect response did not parse: ${result}`);
  return {
    sidebar_tab: Number(m[1]),
    extended_frames: Number(m[2]),
    glyph_id: Number(m[3]),
    glyph_rect: { valid: m[4] === '1', x0: Number(m[5]), y0: Number(m[6]), x1: Number(m[7]), y1: Number(m[8]) },
    fill_rect: { valid: m[9] === '1', x0: Number(m[10]), y0: Number(m[11]), x1: Number(m[12]), y1: Number(m[13]) },
  };
}

function clickCenter(rect) {
  return { x: Math.floor((rect.x0 + rect.x1) / 2), y: Math.floor((rect.y0 + rect.y1) / 2) };
}

function countExtendedCells(dump) {
  let count = 0;
  for (const row of dump.glyph_ids || [])
    for (const id of row)
      if (id > 255 && id < 0xFFFFFFFE) count++;
  return count;
}

function allCellsEqual(dump, glyphId) {
  for (const row of dump.glyph_ids || [])
    for (const id of row)
      if (id !== glyphId) return false;
  return true;
}

function firstRow(dump, key) {
  return (dump[key] && dump[key][0]) || [];
}

async function waitForPickerRects(client) {
  const deadline = Date.now() + 15000;
  let last = '';
  while (Date.now() < deadline) {
    last = await client.request('FL4131_DUMP_EXTENDED_PICKER_UI_RECTS');
    const rects = parsePickerRects(last);
    if (
      rects.glyph_id > 255 &&
      rects.glyph_rect.valid && rects.glyph_rect.x1 > rects.glyph_rect.x0 &&
      rects.fill_rect.valid && rects.fill_rect.x1 > rects.fill_rect.x0
    ) return rects;
    await sleep(150);
  }
  throw new Error(`extended picker never exposed clickable rects: ${last}`);
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
  const captureDir = path.join(OUT_DIR, 'asciiid_ui_after_extended_picker_fill');
  fs.rmSync(captureDir, { recursive: true, force: true });

  let proc = null;
  let client = null;
  try {
    proc = startAsciiid(CDP_PORT);
    client = new CdpClient(await connectCdp(CDP_PORT));

    const before = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
    const rects = await waitForPickerRects(client);
    const glyphClick = clickCenter(rects.glyph_rect);
    const fillClick = clickCenter(rects.fill_rect);

    log(`clicking glyph ${rects.glyph_id} at ${glyphClick.x},${glyphClick.y}, then fill at ${fillClick.x},${fillClick.y}`);
    const glyphClickResponse = await client.request('RUN_MOUSE_CLICK_PROBE', `${glyphClick.x} ${glyphClick.y}`);
    await sleep(200);
    const fillClickResponse = await client.request('RUN_MOUSE_CLICK_PROBE', `${fillClick.x} ${fillClick.y}`);

    let after = null;
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      await sleep(200);
      after = parseMaterialDump(await client.request('FL4131_DUMP_MATERIAL_GLYPHS', String(MATERIAL_ID)));
      if (countExtendedCells(after) === 64 && allCellsEqual(after, rects.glyph_id)) break;
    }
    if (!after || countExtendedCells(after) !== 64 || !allCellsEqual(after, rects.glyph_id)) {
      throw new Error(`picker fill did not author all material cells with GlyphId ${rects.glyph_id}`);
    }

    const preview = parsePreviewResolve(await client.request('FL4131_DUMP_MATERIAL_PREVIEW_RESOLVE', String(MATERIAL_ID)));
    if (preview.extended_cells !== 64 || preview.coverage_cells !== 64 || preview.diagnostic_cells !== 0 || preview.display_not_fallback !== 64) {
      throw new Error(`picker fill preview resolve failed: ${JSON.stringify(preview)}`);
    }

    const captureResponse = await client.request('CAPTURE_UI_FRAME', captureDir, 30000);
    const screenshotPath = path.join(captureDir, 'ui_frame.png');
    await waitForFile(screenshotPath);
    await client.request('QUIT').catch(() => '');

    const receipt = {
      schema: 'fl4131_asciiid_extended_picker_fill_material.v1',
      verdict: 'PASS',
      generated_at: new Date().toISOString(),
      commit_under_test: currentCommit(),
      transport: 'headed_asciiid_cdp',
      material_id: MATERIAL_ID,
      picker_glyph_id: rects.glyph_id,
      picker_glyph_rect: rects.glyph_rect,
      fill_button_rect: rects.fill_rect,
      picker_click_point: glyphClick,
      fill_click_point: fillClick,
      glyph_click_response: glyphClickResponse.split('\n').find(line => line.includes('RUN_MOUSE_CLICK_PROBE queued')) || glyphClickResponse.trim(),
      fill_click_response: fillClickResponse.split('\n').find(line => line.includes('RUN_MOUSE_CLICK_PROBE queued')) || fillClickResponse.trim(),
      before_extended_cells: countExtendedCells(before),
      after_extended_cells: countExtendedCells(after),
      after_all_cells_match_picker_glyph: allCellsEqual(after, rects.glyph_id),
      after_row0_glyph_ids: firstRow(after, 'glyph_ids'),
      after_row0_fallback_bytes: firstRow(after, 'fallback_bytes'),
      preview_extended_cells: preview.extended_cells,
      preview_coverage_cells: preview.coverage_cells,
      preview_diagnostic_cells: preview.diagnostic_cells,
      preview_display_not_fallback: preview.display_not_fallback,
      screenshot: path.relative(REPO_ROOT, screenshotPath),
      screenshot_bytes: fs.statSync(screenshotPath).size,
      capture_response: captureResponse.split('\n').find(line => line.includes('CAPTURE_UI_FRAME')) || captureResponse.trim(),
      limits: [
        'This proves one headed ASCIIID manifest glyph browser swatch can author the active material ramp.',
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
  process.stderr.write(`[proof-fl4131-asciiid-picker] ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});
