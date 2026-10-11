// Ad hoc script: Reproduce FL-4165 Y8 wasm crash with collision overlay recorder samples
// Created: 2026-05-31
// Canonical gap: a reusable headed Y8 render/crash probe should own this.

'use strict';

const fs = require('fs');
const path = require('path');
const driver = require('../proofs/proof_driver_playwright.js');

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function recorderJson(page) {
  return page.evaluate(() => {
    if (!window.__akRecorderStateJson && window.Module && Module.cwrap) {
      window.__akRecorderStateJson = Module.cwrap('RecorderStateJson', 'string', []);
    }
    if (!window.__akRecorderStateJson) return { error: 'RecorderStateJson unavailable' };
    try { return JSON.parse(window.__akRecorderStateJson()); }
    catch (e) { return { error: 'RecorderStateJson parse: ' + String(e) }; }
  });
}

async function frameJson(page) {
  return page.evaluate(() => {
    if (!window.__akCppAnsiFrameSnapshotJson && window.Module && Module.cwrap) {
      window.__akCppAnsiFrameSnapshotJson = Module.cwrap('GetCppAnsiFrameSnapshotJson', 'string', []);
    }
    if (!window.__akCppAnsiFrameSnapshotJson) return { error: 'GetCppAnsiFrameSnapshotJson unavailable' };
    try { return JSON.parse(window.__akCppAnsiFrameSnapshotJson()); }
    catch (e) { return { error: 'frame parse: ' + String(e) }; }
  });
}

async function main() {
  const out = {
    ok: false,
    pageerrors: [],
    console_errors: [],
    samples: [],
    overlay_toggle: null,
  };
  const page = await driver.openProofPage({
    mapPath: process.env.PROOF_MAP || 'assets/a3d/game_map_y8.a3d',
    waitForRecorderState: true,
  });
  page.on('pageerror', e => out.pageerrors.push(e && e.stack ? e.stack : String(e)));
  page.on('console', m => {
    if (m.type() === 'error') out.console_errors.push(m.text());
  });
  for (let i = 0; i < 8; i++) {
    await sleep(500);
    const rec = await recorderJson(page);
    const frame = await frameJson(page);
    out.samples.push({
      phase: 'pre_overlay',
      i,
      rec_error: rec.error || null,
      tick: rec.collision_debug_tick || rec.server_tick || rec.tick || 0,
      render_w: rec.render_buf_width || 0,
      render_h: rec.render_buf_height || 0,
      collision_valid: rec.collision_debug_valid || 0,
      collision_count: (rec.collision_debug_samples || []).length,
      auth_count: (rec.auth_item_sample || []).length,
      frame_valid: !!frame.valid,
      frame_error: frame.error || null,
    });
  }
  try {
    await page.focus('#asciicker_canvas');
    await page.keyboard.press('v');
    out.overlay_toggle = 'sent_v';
  } catch (e) {
    out.overlay_toggle = 'failed: ' + String(e && e.message ? e.message : e);
  }
  for (let i = 0; i < 10; i++) {
    await sleep(500);
    const rec = await recorderJson(page);
    const frame = await frameJson(page);
    out.samples.push({
      phase: 'post_overlay',
      i,
      rec_error: rec.error || null,
      tick: rec.collision_debug_tick || rec.server_tick || rec.tick || 0,
      render_w: rec.render_buf_width || 0,
      render_h: rec.render_buf_height || 0,
      collision_valid: rec.collision_debug_valid || 0,
      collision_count: (rec.collision_debug_samples || []).length,
      support_source: rec.collision_debug_support_source || 0,
      push_source: rec.collision_debug_push_source || 0,
      frame_valid: !!frame.valid,
      frame_error: frame.error || null,
    });
  }
  out.ok = out.pageerrors.length === 0 && out.console_errors.filter(s => /RuntimeError|memory access|Render\(\) returning 0/.test(s)).length === 0;
  const outPath = path.join(__dirname, '..', '..', '.run', 'fl4165_y8_wasm_crash_probe.json');
  fs.writeFileSync(outPath, JSON.stringify(out, null, 2));
  console.log('[fl4165-y8-crash-probe] wrote ' + outPath);
  console.log(JSON.stringify({ ok: out.ok, pageerrors: out.pageerrors.length, console_errors: out.console_errors.length, last: out.samples[out.samples.length - 1] }, null, 2));
  await driver.cleanup();
  process.exit(out.ok ? 0 : 1);
}

main().catch(async e => {
  console.error('[fl4165-y8-crash-probe] FATAL ' + (e && e.stack ? e.stack : String(e)));
  try { await driver.cleanup(); } catch (_) {}
  process.exit(2);
});
