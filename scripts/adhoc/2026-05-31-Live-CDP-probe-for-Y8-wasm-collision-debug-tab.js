// Ad hoc script: Live CDP probe for Y8 wasm collision debug tab
// Created: 2026-05-31
// Canonical gap: a reusable headed manual browser launcher/prober should own this.

'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9223';
const URL_SUBSTR = process.env.URL_SUBSTR || 'game_map_y8.a3d';
const OUT_PATH = process.env.OUT_PATH || path.join(process.cwd(), '.run', 'fl4165_live_cdp_probe.json');
const SAMPLE_COUNT = Number(process.env.SAMPLE_COUNT || 1);
const SAMPLE_MS = Number(process.env.SAMPLE_MS || 250);
const SEND_KEYS = (process.env.SEND_KEYS || '').split(',').map(s => s.trim()).filter(Boolean);
const SCREENSHOT_PATH = process.env.SCREENSHOT_PATH || '';

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

async function evalJson(page, fnName) {
  return page.evaluate((name) => {
    const mod = window.Module;
    if (!mod || !mod.cwrap) return { error: 'Module.cwrap unavailable' };
    const cacheName = '__akLiveCdp_' + name;
    if (!window[cacheName]) window[cacheName] = mod.cwrap(name, 'string', []);
    try { return JSON.parse(window[cacheName]()); }
    catch (e) { return { error: name + ' parse/eval failed: ' + String(e && e.message ? e.message : e) }; }
  }, fnName);
}

async function main() {
  const browser = await chromium.connectOverCDP(CDP_URL);
  const contexts = browser.contexts();
  const pages = contexts.flatMap(ctx => ctx.pages());
  const page = pages.find(p => p.url().includes(URL_SUBSTR)) || pages.find(p => p.url().includes('index.html'));
  if (!page) {
    throw new Error('No matching live tab found on ' + CDP_URL + '; open URL containing ' + URL_SUBSTR);
  }
  if (SEND_KEYS.length) {
    await page.bringToFront();
    await page.focus('#asciicker_canvas').catch(() => {});
    for (const key of SEND_KEYS) {
      await page.keyboard.press(key);
      await sleep(100);
    }
  }

  const rows = [];
  for (let i = 0; i < SAMPLE_COUNT; i++) {
    const rec = await evalJson(page, 'RecorderStateJson');
    const frame = await evalJson(page, 'GetCppAnsiFrameSnapshotJson');
    rows.push({
      i,
      url: page.url(),
      recorder_error: rec.error || null,
      frame_error: frame.error || null,
      server_tick: rec.server_tick || rec.tick || 0,
      local_pos: rec.local_pos || null,
      collision_debug_valid: rec.collision_debug_valid || 0,
      collision_debug_tick: rec.collision_debug_tick || 0,
      collision_debug_count: (rec.collision_debug_samples || []).length,
      collision_debug_support_source: rec.collision_debug_support_source || 0,
      collision_debug_support_item_id: rec.collision_debug_support_item_id || 0,
      collision_debug_push_source: rec.collision_debug_push_source || 0,
      collision_debug_push_item_id: rec.collision_debug_push_item_id || 0,
      auth_visible_world_count: rec.auth_visible_world_count || 0,
      auth_item_sample_count: (rec.auth_item_sample || []).length,
      frame_valid: !!frame.valid,
      frame_width: frame.width || 0,
      frame_height: frame.height || 0,
      frame_text_prefix: frame.text ? frame.text.slice(0, 240) : null,
    });
    if (i + 1 < SAMPLE_COUNT) await sleep(SAMPLE_MS);
  }

  const result = {
    ok: rows.length > 0 && !rows[rows.length - 1].recorder_error && rows[rows.length - 1].frame_valid,
    cdp_url: CDP_URL,
    matched_url: page.url(),
    sent_keys: SEND_KEYS,
    screenshot_path: SCREENSHOT_PATH || null,
    sampled_at_ms: Date.now(),
    rows,
  };
  if (SCREENSHOT_PATH) {
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
  }
  fs.mkdirSync(path.dirname(OUT_PATH), { recursive: true });
  fs.writeFileSync(OUT_PATH, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
}

main().catch(e => {
  console.error('[fl4165-live-cdp-probe] ' + (e && e.stack ? e.stack : String(e)));
  process.exit(1);
});
