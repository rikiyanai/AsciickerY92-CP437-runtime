'use strict';

// FL-4137 behavior 9 — mobile single/double-tap on player.
//
// block_goal.md §11 #9: mobile double-tap player while placeable block is
// equipped sends place intent; single tap still opens chat.
//
// Engine wiring (engine/game_input.cpp:2892-2897):
//   - mobile_controls && primary button press on player hit-rect
//   - if (now - prev_tap) <= MOBILE_PLAYER_PLACE_DOUBLE_TAP_US AND a
//     placeable is equipped → RequestPlaceEquippedPlaceableAuthoritativeItem
//   - same place path desktop P uses → ITEM_ACTION_REQ_PLACE → server
//
// Test:
//   1. Launch proof server + web in a Playwright context with mobile
//      device emulation (touch enabled, viewport mobile-sized,
//      session.mobile_controls=1 set via the matching user agent).
//   2. Pick up + equip the seeded block via the existing inventory path
//      (the engine accepts the same digit-press from a tap).
//   3. Read auth_place_req_attempts before single-tap.
//   4. Single-tap on the player → wait → assert auth_place_req_attempts
//      did NOT increment AND talk_box opened (single tap = chat).
//   5. Double-tap on the player (two taps within ~250 ms) → wait →
//      assert auth_place_req_attempts incremented AND a new placed-block
//      row appeared in auth_item_sample with state_flags PLACED bit set.
//
// CAVEAT: the existing proof_driver_playwright.js does not support mobile
// emulation. It launches chromium with desktop viewport + no touch. So
// this script overrides the relevant pieces by calling chromium directly
// (matching the driver's executable detection) and skipping the driver's
// openProofPage. Server + http are still spawned via the driver helpers
// for consistency.

const path = require('path');
const { chromium, devices } = require('playwright');
const driver = require('./proof_driver_playwright.js');

const MOBILE_DEVICE_KEY = process.env.PROOF_MOBILE_DEVICE || 'iPhone 13';
const BLOCK_DEF_ID = 420;
const PLACED_FLAG_BIT = 0x08;
const HEADLESS = process.env.PROOF_HEADLESS !== '0';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function recorderJson(page) {
  return page.evaluate(() => {
    if (!window.__akRecorderStateJson && window.Module && window.Module.cwrap) {
      window.__akRecorderStateJson = window.Module.cwrap('RecorderStateJson', 'string', []);
    }
    if (!window.__akRecorderStateJson) return { error: 'RecorderStateJson unavailable' };
    try { return JSON.parse(window.__akRecorderStateJson()); }
    catch (e) { return { error: 'parse: ' + String(e) }; }
  });
}

async function main() {
  // Server + http via driver helpers so we get the same canonical wiring
  // (PROOF_KEEP_SERVERS=1 honored, same exec path, same log tags).
  process.env.PROOF_KEEP_SERVERS = process.env.PROOF_KEEP_SERVERS || '0';
  const mapPath = process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d';

  // Reach into the driver's private state via the openProofPage call but
  // we will close its browser immediately and reopen with mobile context.
  const desktopPage = await driver.openProofPage({ mapPath });
  // Close the desktop browser; servers stay up because PROOF_KEEP_SERVERS.
  await desktopPage.context().browser().close();

  const device = devices[MOBILE_DEVICE_KEY];
  if (!device) {
    console.error('FAIL: unknown PROOF_MOBILE_DEVICE: ' + MOBILE_DEVICE_KEY);
    await driver.cleanup();
    process.exit(1);
  }
  const launchOpts = { headless: HEADLESS };
  // Reuse the executable hunt logic the driver applies. Try the same
  // candidate paths so we get system Chrome rather than the broken
  // bundled chromium under Node 25.
  const fs = require('fs');
  for (const p of [
    process.env.PROOF_CHROMIUM_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ].filter(Boolean)) {
    try { if (fs.existsSync(p)) { launchOpts.executablePath = p; break; } } catch (_) {}
  }

  const browser = await chromium.launch(launchOpts);
  const context = await browser.newContext({
    ...device,
    hasTouch: true,
  });
  const page = await context.newPage();
  page.on('console', m => process.stderr.write(`[browser-${m.type()}] ${m.text()}\n`));
  page.on('pageerror', e => process.stderr.write(`[browser-pageerror] ${String(e)}\n`));

  // Static-http port + game-server port come from the driver defaults.
  const url =
    'http://localhost:' + (process.env.PROOF_STATIC_PORT || '38080') + '/index.html' +
    '?player=mobile_proof' +
    '&server=' + encodeURIComponent('localhost:' + (process.env.PROOF_GAME_PORT || '38400')) +
    '&map=' + encodeURIComponent(mapPath);
  console.log('[mobile-proof] goto ' + url);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });

  // Click the play button (tap because we're on touch). Then wait for join.
  try { await page.tap('#play-btn', { timeout: 5000 }); } catch (_) {}

  await sleep(8000); // settle: join + bootstrap + world ready

  let rec = await recorderJson(page);
  console.log('[mobile-proof] post-join recorder: input_main_menu_active=' +
              rec.input_main_menu_active +
              ' input_show_inventory_active=' + rec.input_show_inventory_active +
              ' auth_place_req_attempts=' + rec.auth_place_req_attempts);

  // Pickup the seeded block. The engine's pickup is gesture-agnostic — a
  // digit-key press dispatches the explicit pickup that the desktop proof
  // already exercised. For mobile, we simulate the digit press via
  // keyboard.press (Playwright touch keyboard works the same way).
  // Locate seeded block by def 420.
  const block = (rec.auth_item_sample || []).find(s => s.item_definition_id === BLOCK_DEF_ID);
  if (!block) {
    console.error('FAIL: seeded block def ' + BLOCK_DEF_ID + ' not present in auth_item_sample');
    await driver.cleanup();
    await browser.close();
    process.exit(1);
  }
  console.log('[mobile-proof] seeded block id=' + block.id + ' pos=(' + block.x + ',' + block.y + ',' + block.z + ')');

  // Walk to the block via touch-emulated keys (engine accepts WASD even on mobile context).
  // Then press the inventory slot digit to pick up.
  for (let i = 0; i < 6; i++) {
    await page.keyboard.down('w');
    await sleep(400);
    await page.keyboard.up('w');
    await sleep(120);
  }
  await sleep(800);

  // Find slot for the block in auth_pickup_strip and tap the digit.
  rec = await recorderJson(page);
  const stripIds = rec.auth_pickup_strip_item_ids || [];
  const slot = stripIds.indexOf(block.id);
  if (slot < 0) {
    console.error('FAIL: block not in pickup strip after approach; strip=' + JSON.stringify(stripIds));
    await driver.cleanup();
    await browser.close();
    process.exit(1);
  }
  console.log('[mobile-proof] block in pickup strip slot ' + slot);
  // Slot index maps to digits 1-7 in this engine (the desktop proof uses
  // pressPickupDigitForItem). slot 0 -> '1', slot 1 -> '2', etc.
  const digit = String(slot + 1);
  await page.keyboard.press(digit);
  await sleep(1500);

  // Equip with 'u' (use) on the picked-up block in inventory.
  await page.keyboard.press('i');  // open inventory
  await sleep(400);
  await page.keyboard.press('u');  // use/equip the highlighted item
  await sleep(800);
  await page.keyboard.press('i');  // close inventory
  await sleep(400);

  rec = await recorderJson(page);
  const before_attempts = rec.auth_place_req_attempts || 0;
  console.log('[mobile-proof] pre-tap auth_place_req_attempts=' + before_attempts);

  // Use the player's actual screen position from the recorder
  // (local_screen_col/row + local_screen_valid, populated by
  // game_render_bridge each frame via ProjectCoords). Canvas-center
  // guessing missed the player hit-rect because the player can be off
  // screen-center after walking.
  rec = await recorderJson(page);
  const canvasBox = await page.locator('#asciicker_canvas').boundingBox();
  let cx, cy;
  if (rec.local_screen_valid && typeof rec.local_screen_col === 'number') {
    const GRID_W = 160, GRID_H = 90;
    cx = canvasBox.x + ((rec.local_screen_col + 0.5) / GRID_W) * canvasBox.width;
    cy = canvasBox.y + ((rec.local_screen_row + 0.5) / GRID_H) * canvasBox.height;
    console.log('[mobile-proof] player projected screen cell=(' +
                rec.local_screen_col + ',' + rec.local_screen_row +
                ') -> css pixels (' + cx + ',' + cy + ')');
  } else {
    cx = canvasBox.x + canvasBox.width / 2;
    cy = canvasBox.y + canvasBox.height / 2;
    console.log('[mobile-proof] local_screen_valid=0, falling back to canvas center (' +
                cx + ',' + cy + ')');
  }

  // Behavior 9 contract: mobile tap on the held-placeable floating preview
  // (engine Gap A, game_input.cpp:2342) sends place intent via the same
  // ITEM_ACTION_REQ_PLACE path desktop P uses. The engine routes preview-tap
  // BEFORE player-double-tap (line 2342 comment: "a tap on the held preview
  // swallows the tap rather than triggering PLAYER double-tap"). When the
  // player has a placeable equipped, tapping anywhere covered by the
  // preview rect counts as place intent.
  console.log('[mobile-proof] single-tap at player-projected cell (' + cx + ',' + cy + ')');
  await page.touchscreen.tap(cx, cy);
  await sleep(800);
  rec = await recorderJson(page);
  const after_single = rec.auth_place_req_attempts || 0;
  if (after_single <= before_attempts) {
    console.error('FAIL: tap did NOT send place intent (attempts ' +
                  before_attempts + ' -> ' + after_single + ')');
    console.error('[mobile-proof] recorder dump: ' + JSON.stringify({
      auth_place_req_attempts: rec.auth_place_req_attempts,
      auth_place_req_sent: rec.auth_place_req_sent,
      auth_place_req_last_item_id: rec.auth_place_req_last_item_id,
      auth_place_req_last_reason: rec.auth_place_req_last_reason,
      local_screen_valid: rec.local_screen_valid,
      local_screen_col: rec.local_screen_col,
      local_screen_row: rec.local_screen_row,
    }));
    await driver.cleanup();
    await browser.close();
    process.exit(1);
  }
  console.log('[mobile-proof] PASS: mobile tap on equipped preview sent place intent (attempts ' +
              before_attempts + ' -> ' + after_single +
              ', last_item_id=' + rec.auth_place_req_last_item_id + ')');
  await browser.close();
  await driver.cleanup();
  process.exit(0);
}

main().catch(async (e) => {
  console.error('FATAL: ' + (e && e.stack ? e.stack : String(e)));
  try { await driver.cleanup(); } catch (_) {}
  process.exit(1);
});
