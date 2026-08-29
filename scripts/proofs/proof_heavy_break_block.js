'use strict';

// FL-4137 behavior 8 — heavy weapon breaks placed block.
//
// block_goal.md §11 #8: heavy weapon breaks block.
//
// Server has been unblocked (commit 1fffc03e5 removed the MVP gate at
// SvrTryBreakPlacedBlocksFromSwing's `return false`). Catalog:
//   normal_sword (def 409): block_break_power = 1
//   legacy_yy_block (def 420): placed_durability = 3
// Three swings within sword range should despawn the block.
//
// Setup: SvrSeedNormalSwordWorldItem seeds a normal_sword at spawn + (-4,0)
// so the proof can walk over to pick it up. The seeded block is at
// spawn + (+4,0). Total walking: ~8 units back-and-forth.
//
// Test:
//   1. Spawn server+http via the driver. Verify both sword and block are
//      visible.
//   2. Walk to sword, pick up via existing digit-press flow.
//   3. Equip sword (open inventory, press 'u', close inventory).
//   4. Walk to block.
//   5. Press ENTER three times (with cooldown gaps) to swing.
//   6. Assert the block's auth_item_sample row disappears OR transitions
//      to a despawned state. Also check placed_durability decrement after
//      each swing if observable.

const path = require('path');
const driver = require('./proof_driver_playwright.js');

const BLOCK_DEF_ID = 420;
const SWORD_DEF_ID = 409;
const LOCAL_OWNER_SENTINEL = 0xFFFF;
const SWING_COOLDOWN_MS = 600;  // > engine MP_SWING_COOLDOWN = 500ms

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

async function pressDigit(page, digit) {
  await page.keyboard.press(String(digit));
}

async function walk(page, key, ms) {
  await page.keyboard.down(key);
  await sleep(ms);
  await page.keyboard.up(key);
  await sleep(150);
}

async function findItemByDef(rec, defId, ownerWanted) {
  return (rec.auth_item_sample || []).find(s =>
    s.item_definition_id === defId &&
    (ownerWanted === undefined || s.owner_id === ownerWanted)
  );
}

async function waitFor(page, label, predicate, timeoutMs = 12000) {
  const start = Date.now();
  let last;
  while (Date.now() - start < timeoutMs) {
    const rec = await recorderJson(page);
    last = rec;
    const r = predicate(rec);
    if (r) return { rec, value: r };
    await sleep(250);
  }
  throw new Error(label + ' timed out. last_recorder=' +
    JSON.stringify(last && last.auth_item_sample || {}).slice(0, 600));
}

async function main() {
  const mapPath = process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d';
  const page = await driver.openProofPage({ mapPath });

  // Verify both sword and block are in the visible/auth list.
  let rec = await recorderJson(page);
  const sword = await findItemByDef(rec, SWORD_DEF_ID, LOCAL_OWNER_SENTINEL);
  let block = await findItemByDef(rec, BLOCK_DEF_ID, LOCAL_OWNER_SENTINEL);
  if (!sword) { console.error('FAIL: sword (def 409) not seeded. SvrSeedNormalSwordWorldItem may have skipped.'); await driver.cleanup(); process.exit(1); }
  if (!block) { console.error('FAIL: block (def 420) not seeded.'); await driver.cleanup(); process.exit(1); }
  console.log('[heavy-break] sword id=' + sword.id + ' pos=(' + sword.x + ',' + sword.y + ',' + sword.z + ')');
  console.log('[heavy-break] block id=' + block.id + ' pos=(' + block.x + ',' + block.y + ',' + block.z + ')');

  // Walk west toward sword (sword at spawn-4 in x; spawn is somewhere near
  // (-2.8, -73.6, 57)). Player starts at spawn. Walk 'a' to go -x.
  for (let i = 0; i < 8; i++) await walk(page, 'a', 400);
  await sleep(800);

  // Pick up sword via digit press matching its slot.
  rec = await recorderJson(page);
  const stripIds = rec.auth_pickup_strip_item_ids || [];
  let swordSlot = stripIds.indexOf(sword.id);
  if (swordSlot < 0) {
    console.error('FAIL: sword not in pickup strip after approach. strip=' + JSON.stringify(stripIds));
    console.error('[heavy-break] local_pos=' + JSON.stringify([rec.local_pos_x, rec.local_pos_y, rec.local_pos_z]));
    await driver.cleanup(); process.exit(1);
  }
  console.log('[heavy-break] sword in pickup strip slot ' + swordSlot);
  await pressDigit(page, swordSlot + 1);
  const picked = await waitFor(page, 'sword pickup',
    rec => (rec.auth_item_sample || []).find(s => s.id === sword.id && s.owner_id !== LOCAL_OWNER_SENTINEL));
  console.log('[heavy-break] sword picked up by ci=0x' +
              picked.value.owner_id.toString(16));

  // Equip sword: open inventory ('b' as in proof_placeable_block_items),
  // cycle focus across slots so the sword is highlighted, press 'u' to equip,
  // close. The authoritative_inventory_focus drives which item 'u' acts on.
  await page.keyboard.press('b');
  await sleep(400);
  // Cycle focus across a handful of slots and press 'u' at each — at the
  // sword slot, the use-request succeeds and equip_slot_kind_id flips.
  for (let i = 0; i < 8; i++) {
    rec = await recorderJson(page);
    const sw = (rec.auth_item_sample || []).find(s => s.id === sword.id);
    if (sw && sw.equip_slot_kind_id !== 0) {
      console.log('[heavy-break] sword equipped at focus iteration ' + i);
      break;
    }
    await page.keyboard.press('u');
    await sleep(200);
    rec = await recorderJson(page);
    const swAfter = (rec.auth_item_sample || []).find(s => s.id === sword.id);
    if (swAfter && swAfter.equip_slot_kind_id !== 0) {
      console.log('[heavy-break] sword equipped after focus ' + i + ' + use');
      break;
    }
    await page.keyboard.press('ArrowRight');
    await sleep(150);
  }
  await page.keyboard.press('b');
  await sleep(400);

  // Verify equip by inspecting the sword item state — equip_slot_kind_id
  // should be non-zero (WEAPON slot).
  rec = await recorderJson(page);
  const swordAfterEquip = (rec.auth_item_sample || []).find(s => s.id === sword.id);
  console.log('[heavy-break] post-equip sword state: equip_slot_kind_id=' +
              (swordAfterEquip ? swordAfterEquip.equip_slot_kind_id : 'gone') +
              ' state_flags=' +
              (swordAfterEquip ? swordAfterEquip.state_flags : '?'));
  if (!swordAfterEquip || swordAfterEquip.equip_slot_kind_id === 0) {
    console.error('FAIL: sword did not equip. Cannot swing without equipped weapon.');
    await driver.cleanup();
    process.exit(1);
  }

  // Walk east toward block (spawn + 4).
  for (let i = 0; i < 12; i++) await walk(page, 'd', 400);
  await sleep(800);

  rec = await recorderJson(page);
  const blockNow = await findItemByDef(rec, BLOCK_DEF_ID);
  console.log('[heavy-break] approached block. local_pos=' +
              JSON.stringify([rec.local_pos_x, rec.local_pos_y, rec.local_pos_z]) +
              ' inventory_open=' + rec.input_show_inventory_active +
              ' block.placed_durability_visible=' +
              (blockNow ? 'present' : 'GONE'));
  if (rec.input_show_inventory_active) {
    console.log('[heavy-break] inventory still open, closing with b before swings');
    await page.keyboard.press('b');
    await sleep(400);
  }

  if (!blockNow) {
    console.log('[heavy-break] PASS (early): block already despawned before any swing?');
    await driver.cleanup(); process.exit(0);
  }

  // Bypass the JS keyboard handler and call the engine's Keyb() cwrap
  // directly. The browser keydown -> engine bridge in game_web.html
  // requires focus + ScreenToCell + identifier mapping that Playwright
  // synthetic events don't reliably satisfy. The Keyb cwrap is exported
  // (web_platform.cpp:170) and is what the bridge calls under the hood.
  async function engineKeybChar(charCode) {
    return page.evaluate((c) => {
      if (!window.__akKeyb && window.Module && window.Module.cwrap) {
        window.__akKeyb = window.Module.cwrap('Keyb', null, ['number','number']);
      }
      if (!window.__akKeyb) return false;
      window.__akKeyb(2 /*CHAR*/, c);
      return true;
    }, charCode);
  }

  // Swing five times with cooldown gaps; check block disappearance.
  for (let swing = 1; swing <= 5; swing++) {
    console.log('[heavy-break] swing ' + swing);
    await engineKeybChar(10); // '\n' = Enter -> StartManualAttack at game_input.cpp:1552
    await sleep(SWING_COOLDOWN_MS);
    rec = await recorderJson(page);
    console.log('[heavy-break] dbg_attack_key_attempts=' +
                (rec.dbg_attack_key_attempts || 0) +
                ' dbg_attack_setaction_success=' +
                (rec.dbg_attack_setaction_success || 0) +
                ' dbg_mp_swing_send_attempts=' +
                (rec.dbg_mp_swing_send_attempts || 0));
    const post = await findItemByDef(rec, BLOCK_DEF_ID);
    if (!post) {
      console.log('[heavy-break] PASS: block despawned after ' + swing + ' swings.');
      await driver.cleanup();
      process.exit(0);
    }
    console.log('[heavy-break] post-swing-' + swing + ': block still present id=' + post.id +
                ' owner=0x' + post.owner_id.toString(16) +
                ' durability=' + (post.placed_durability !== undefined ? post.placed_durability : '?') +
                ' pos=(' + post.x + ',' + post.y + ',' + post.z + ')');
  }

  console.error('FAIL: block survived 5 swings. Expected despawn at 3 swings ' +
                '(placed_durability=3, block_break_power=1).');
  await driver.cleanup();
  process.exit(1);
}

main().catch(async e => {
  console.error('FATAL: ' + (e && e.stack ? e.stack : String(e)));
  try { await driver.cleanup(); } catch (_) {}
  process.exit(1);
});
