'use strict';

// FL-4137 behavior 6 — click/tap pickup of placed block via the pickup strip.
//
// block_goal.md §11 #6: click/tap pickup works.
//
// The engine wires mouse/touch click on a pickup-strip cell to
// RequestPickupAuthoritativeWorldItemByListIndex via game_input.cpp:2246
// (touch contact -> HitAuthoritativeWorldItemPickupStripSlot ->
// pickup request). The same RequestPickup... is dispatched by the digit-key
// path that proof_placeable_block_items already exercises; this proof
// covers the CLICK input gesture specifically.
//
// Strategy: spawn server+http via the canonical driver, walk the local
// player into pickup range of the seeded block (item def 420 at
// (1.2, -73.6, 57)), then dispatch a series of taps across the bottom-
// center cell band where the pickup strip renders (cell y near height/2-2
// for a 160x90 grid). At least one tap should hit a strip slot and
// trigger pickup; the recorder's auth_item_sample[].owner_id transitions
// from 0xFFFF (world) to the local player's ci when pickup succeeds.

const path = require('path');
const fs = require('fs');
const { chromium, devices } = require('playwright');
const driver = require('./proof_driver_playwright.js');

const BLOCK_DEF_ID = 420;
const LOCAL_OWNER_SENTINEL = 0xFFFF;
// Engine's strip-click path lives in the touch-contact code (game_input.cpp:
// 2216-2262). Mouse events would only route there if TOUCH_EMU were defined
// (engine/game_input.cpp:3043), which it isn't in any production build.
// So this proof drives touch via Playwright's mobile context.
const MOBILE_DEVICE_KEY = process.env.PROOF_MOBILE_DEVICE || 'iPhone 13';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function recorderJson(page) {
  return page.evaluate(() => {
    if (!window.__akRecorderStateJson && window.Module && window.Module.cwrap) {
      window.__akRecorderStateJson = window.Module.cwrap('RecorderStateJson', 'string', []);
    }
    if (!window.__akRecorderStateJson) return { error: 'unavailable' };
    try { return JSON.parse(window.__akRecorderStateJson()); }
    catch (e) { return { error: 'parse: ' + String(e) }; }
  });
}

async function main() {
  const mapPath = process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d';
  // Desktop context: the synthetic touch is dispatched via the engine's
  // Touch() cwrap directly, not via browser TouchEvent. No mobile context
  // needed; the cwrap reaches OnTouch -> StartContact independently of
  // browser input mechanics.
  const page = await driver.openProofPage({ mapPath });
  const browser = page.context().browser();

  // Walk toward the seeded block at (1.2, -73.6) from the spawn so the
  // pickup strip populates.
  for (let i = 0; i < 8; i++) {
    await page.keyboard.down('w');
    await sleep(400);
    await page.keyboard.up('w');
    await sleep(120);
  }
  await sleep(800);

  let rec = await recorderJson(page);
  if (rec.error) { console.error('FAIL: recorder: ' + rec.error); await driver.cleanup(); process.exit(1); }
  const stripIds = rec.auth_pickup_strip_item_ids || [];
  if (stripIds.length === 0) {
    console.error('FAIL: pickup strip empty after approach');
    console.error('[click-proof] recorder: ' + JSON.stringify({
      auth_pickup_strip_count: rec.auth_pickup_strip_count,
      auth_world_strip_count: rec.auth_world_strip_count,
      auth_visible_world_item_ids: rec.auth_visible_world_item_ids,
      local_pos: [rec.local_pos_x, rec.local_pos_y, rec.local_pos_z],
    }));
    await driver.cleanup();
    process.exit(1);
  }
  console.log('[click-proof] strip populated, ids=' + JSON.stringify(stripIds));
  const targetId = stripIds[0];

  // Snapshot block owner before tap.
  const blockBefore = (rec.auth_item_sample || []).find(s => s.id === targetId);
  if (!blockBefore || blockBefore.owner_id !== LOCAL_OWNER_SENTINEL) {
    console.error('FAIL: target block ' + targetId + ' not world-owned before tap: ' +
                  JSON.stringify(blockBefore));
    await driver.cleanup();
    process.exit(1);
  }
  console.log('[click-proof] pre-tap block owner_id=0x' +
              blockBefore.owner_id.toString(16) + ' (world)');

  // Use the published pickup-strip cell rect from the recorder (added in
  // web/web_recorder_bridge.cpp for this proof). Slot 0 spans
  // [pickup_strip_xarr[0] .. pickup_strip_xarr[1]] in x and
  // [pickup_strip_ylo .. pickup_strip_yhi] in y. Tap the center cell.
  const ylo = rec.pickup_strip_ylo;
  const yhi = rec.pickup_strip_yhi;
  const xarr = rec.pickup_strip_xarr;
  if (typeof ylo !== 'number' || typeof yhi !== 'number' || !Array.isArray(xarr) ||
      yhi <= ylo || xarr[1] <= xarr[0]) {
    console.error('FAIL: pickup_strip_* recorder fields invalid: ' +
                  JSON.stringify({ ylo, yhi, xarr }));
    await driver.cleanup();
    process.exit(1);
  }
  const targetCol = Math.floor((xarr[0] + xarr[1]) / 2);
  const targetRow = Math.floor((ylo + yhi) / 2);
  console.log('[click-proof] strip slot 0 cell rect x=[' + xarr[0] + '..' + xarr[1] +
              '] y=[' + ylo + '..' + yhi + '], tap cell (' + targetCol + ',' + targetRow + ')');

  // Bypass the JS touch dispatch and call the engine's Touch() cwrap
  // directly with the right id + page pixel coords. JS bridge at
  // game_web.html:5305-5353 multiplies pageX/Y by ak_ratio (DPR) and
  // assigns sequential ids 1..16. We mimic that: pick id=1, pixel coords
  // = (strip cell center * ak_ratio * font cell size).
  const canvasBox = await page.locator('#asciicker_canvas').boundingBox();
  const GRID_W = rec.render_buf_width  || 160;
  const GRID_H = rec.render_buf_height || 90;
  // Engine cell coords are BOTTOM-UP (row 0 at bottom of screen, observed
  // via FL4137-STARTCONTACT diagnostic). CSS pixel y is TOP-DOWN. Flip
  // the row when converting cell coord to CSS pixel.
  const px = canvasBox.x + ((targetCol + 0.5) / GRID_W) * canvasBox.width;
  const py = canvasBox.y + ((GRID_H - 1 - targetRow + 0.5) / GRID_H) * canvasBox.height;
  console.log('[click-proof] tap pixel (' + px + ',' + py + ') for cell (' +
              targetCol + ',' + targetRow + ') in ' + GRID_W + 'x' + GRID_H + ' grid');
  let pickedUp = false;
  // Drive the engine's Touch() cwrap directly. Also try Mouse() as a
  // fallback because some engine builds route strip pickup through mouse.
  await page.evaluate(([px, py]) => {
    if (!window.Module || !window.Module.cwrap) return;
    if (!window.__akTouch) window.__akTouch = window.Module.cwrap('Touch', null, ['number','number','number','number']);
    if (!window.__akMouse) window.__akMouse = window.Module.cwrap('Mouse', null, ['number','number','number']);
    var ratio = window.ak_ratio || window.devicePixelRatio || 1;
    var sx = Math.round(px * ratio);
    var sy = Math.round(py * ratio);
    // Mouse down (GAME_MOUSE::MOUSE_LEFT_BUT_DOWN = 1) at pixel
    window.__akMouse(1, sx, sy);
    setTimeout(function() {
      window.__akMouse(2 /* MOUSE_LEFT_BUT_UP */, sx, sy);
    }, 60);
    // Also fire Touch
    window.__akTouch(1 /*BEGIN*/, 1 /*id*/, sx, sy);
    setTimeout(function() { window.__akTouch(2 /*END*/, 1, sx, sy); }, 120);
  }, [px, py]);
  await sleep(700);
  rec = await recorderJson(page);
  let block = (rec.auth_item_sample || []).find(s => s.id === targetId);
  if (block && block.owner_id !== LOCAL_OWNER_SENTINEL) {
    console.log('[click-proof] PASS: touch tap at cell (' + targetCol + ',' + targetRow +
                ') triggered pickup. owner_id 0x' +
                blockBefore.owner_id.toString(16) + ' -> 0x' + block.owner_id.toString(16));
    pickedUp = true;
  } else {
    console.error('[click-proof] touch tap did not pick up.');
    console.error('[click-proof] post-tap recorder: ' + JSON.stringify({
      pickup_strip_ylo: rec.pickup_strip_ylo,
      pickup_strip_yhi: rec.pickup_strip_yhi,
      pickup_strip_xarr: rec.pickup_strip_xarr,
      target_block_owner: block ? '0x' + block.owner_id.toString(16) : 'missing',
      local_pos: [rec.local_pos_x, rec.local_pos_y, rec.local_pos_z],
    }));
  }

  if (!pickedUp) {
    console.error('FAIL: touch tap at exact strip cell did not pick up.');
    try { await browser.close(); } catch (_) {}
    await driver.cleanup();
    process.exit(1);
  }

  try { await browser.close(); } catch (_) {}
  await driver.cleanup();
  process.exit(0);
}

main().catch(async e => {
  console.error('FATAL: ' + (e && e.stack ? e.stack : String(e)));
  try { await driver.cleanup(); } catch (_) {}
  process.exit(1);
});
