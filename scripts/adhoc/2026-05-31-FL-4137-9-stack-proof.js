// Ad hoc script: FL-4137 #9 stacking proof.
// Pickup the legacy_yy_block (id=25344), walk over the tall_yy_block (id=25345)
// footprint, and place — server place handler stack search should lift the
// placed block onto the tall block's top (z = tall.collision_top_z = 97).
// Created: 2026-05-31

const fs = require('fs');
const { chromium } = require('playwright');

const URL_ = process.env.PROBE_URL ||
  'http://localhost:38080/index.html?player=stackproof&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
const HEADLESS = process.env.PROBE_HEADLESS !== '0';

function chromePath() {
  for (const p of [
    process.env.PROOF_CHROMIUM_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean)) {
    try { if (fs.existsSync(p)) return p; } catch (_) {}
  }
  return null;
}

async function recorder(page) {
  return await page.evaluate(() => {
    if (!window.__akRec)
      window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
    return JSON.parse(window.__akRec() || '{}');
  });
}

async function pulse(page, key, ms) {
  await page.keyboard.down(key);
  await new Promise(r => setTimeout(r, ms));
  await page.keyboard.up(key);
  await new Promise(r => setTimeout(r, 50));
}

(async () => {
  const opts = { headless: HEADLESS };
  const cp = chromePath();
  if (cp) opts.executablePath = cp;
  const browser = await chromium.launch(opts);
  const page = await browser.newContext({ viewport: { width: 1440, height: 900 } }).then(c => c.newPage());

  await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(15000);
  await page.evaluate(() => {
    const candidates = [
      document.getElementById('play-button'),
      document.querySelector('button[id*="play"], button[id*="Play"]'),
      document.querySelector('.play-button'),
      document.querySelector('button'),
    ].filter(Boolean);
    for (const b of candidates) { try { b.click(); break; } catch (_) {} }
  });
  await page.waitForTimeout(12000);

  // Wait for join + items.
  let r = null;
  for (let i = 0; i < 30; i++) {
    r = await page.evaluate(() => {
      if (!window.__akRec) window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
      const rec = JSON.parse(window.__akRec() || '{}');
      return { joined: !!window.ak_joined, items: rec.auth_item_sample || [], rec };
    });
    if (r.joined && r.items.length > 0) break;
    await new Promise(t => setTimeout(t, 1000));
  }
  if (!r.joined) { console.error('FAIL: never joined'); process.exit(2); }
  const tall = r.items.find(it => it.id === 25345);
  const legacy = r.items.find(it => it.id === 25344);
  if (!tall || !legacy) {
    console.error(`FAIL: missing seeded blocks: tall=${!!tall} legacy=${!!legacy}`);
    process.exit(3);
  }
  console.log('seed: legacy=', JSON.stringify({x:legacy.x,y:legacy.y,z:legacy.z,top:legacy.collision_top_z}));
  console.log('seed: tall=  ', JSON.stringify({x:tall.x,y:tall.y,z:tall.z,top:tall.collision_top_z}));

  // Player starts at (-2.8, -73.6, 73.25) ON TOP of legacy block. Pickup is
  // hard while standing on the block. Walk SOUTH off the block first.
  console.log('phase: walk south off legacy block');
  for (let i = 0; i < 6; i++) {
    await page.keyboard.down('s');
    await new Promise(t => setTimeout(t, 400));
    await page.keyboard.up('s');
    await new Promise(t => setTimeout(t, 100));
  }
  const cleared = await recorder(page);
  console.log(`cleared at pos=(${cleared.self_x?.toFixed(2)},${cleared.self_y?.toFixed(2)},${cleared.self_z?.toFixed(2)})`);

  // Now walk back NORTH/EAST toward legacy until in pickup range.
  console.log('phase: approach legacy for pickup');
  for (let i = 0; i < 15; i++) {
    const s = await recorder(page);
    if ((s.auth_pickup_strip_item_ids || []).includes(25344)) {
      console.log(`legacy in pickup strip after ${i} approach pulses; pos=(${s.self_x?.toFixed(2)},${s.self_y?.toFixed(2)},${s.self_z?.toFixed(2)})`);
      break;
    }
    // Walk toward legacy XY (1.2, -73.6) from current.
    const dx = legacy.x - s.self_x;
    const dy = legacy.y - s.self_y;
    const key = (Math.abs(dx) > Math.abs(dy)) ? (dx > 0 ? 'w' : 's') : (dy > 0 ? 'd' : 'a');
    await page.keyboard.down(key);
    await new Promise(t => setTimeout(t, 250));
    await page.keyboard.up(key);
    await new Promise(t => setTimeout(t, 100));
  }
  const before = await recorder(page);
  const stripIds = before.auth_pickup_strip_item_ids || [];
  const idx = stripIds.indexOf(25344);
  if (idx < 0) { console.error(`FAIL: legacy block never entered pickup strip: ${JSON.stringify(stripIds)}`); process.exit(4); }
  console.log(`legacy at pickup strip idx=${idx}`);

  // Press the corresponding digit key (1-based) — pressing '1' picks the first
  // strip slot, etc.
  await page.keyboard.press(String(idx + 1));
  await new Promise(t => setTimeout(t, 1000));

  // Wait for legacy to become player-owned.
  let owned = null;
  for (let i = 0; i < 10; i++) {
    const s = await recorder(page);
    const lg = (s.auth_item_sample || []).find(it => it.id === 25344);
    if (lg && lg.owner_id !== 65535) { owned = lg; break; }
    await new Promise(t => setTimeout(t, 500));
  }
  if (!owned) { console.error('FAIL: legacy never became player-owned after pickup'); process.exit(5); }
  console.log(`pickup OK: legacy owner_id=${owned.owner_id}`);

  // Equip via b+u (use proof's keyboard.press pattern).
  await page.keyboard.press('b');
  await new Promise(t => setTimeout(t, 250));
  await page.keyboard.press('u');
  await new Promise(t => setTimeout(t, 500));
  // Wait for held state (equip_slot_kind_id === 308).
  let held = null;
  for (let i = 0; i < 20; i++) {
    const s = await recorder(page);
    const lg = (s.auth_item_sample || []).find(it => it.id === 25344);
    if (lg && lg.equip_slot_kind_id === 308) { held = lg; break; }
    await new Promise(t => setTimeout(t, 300));
  }
  if (!held) { console.error('FAIL: legacy never became held (equip_slot_kind_id=308)'); process.exit(5); }
  console.log(`equip OK: legacy held`);

  // Walk NORTH (W) over tall block footprint. Tall block at (1.2, -69.6),
  // half=4 — XY footprint is (-2.8..5.2, -73.6..-65.6).
  // Player currently at ~(1.2, -73.6) on top of LEGACY block (z=73).
  // To stack on TALL block, player needs to be at TALL block xy (-2.8..5.2, -73.6..-65.6).
  // Player IS within tall block X range. Player y=-73.6 == tall.y-4 (boundary).
  // Walk north a bit (W key = +Y in some maps).
  // Drive over TALL block footprint using press-and-wait loop with recorder
  // feedback. Player after pickup is at ~(1.2,-73.6,73.25) on top of legacy.
  // Tall block at (1.2,-69.6) — need to move +Y by 4. The W key moves NW in
  // isometric — try keys that produce +Y in this map's coordinate system.
  console.log('phase: walk toward tall block footprint');
  for (let i = 0; i < 25; i++) {
    const s = await recorder(page);
    const dx = tall.x - s.self_x;
    const dy = tall.y - s.self_y;
    const dist = Math.sqrt(dx*dx + dy*dy);
    console.log(`step ${i}: pos=(${s.self_x?.toFixed(2)},${s.self_y?.toFixed(2)},${s.self_z?.toFixed(2)}) dist_to_tall=${dist.toFixed(2)}`);
    if (dist < 1.0) break;
    // Pick best key by sign of dx/dy.
    const key = (Math.abs(dy) > Math.abs(dx))
      ? (dy > 0 ? 'd' : 'a')   // +Y or -Y heuristic
      : (dx > 0 ? 'w' : 's');  // +X or -X heuristic
    await page.keyboard.down(key);
    await new Promise(t => setTimeout(t, 200));
    await page.keyboard.up(key);
    await new Promise(t => setTimeout(t, 100));
  }
  const beforePlace = await recorder(page);
  console.log(`pre-place pos=(${beforePlace.self_x},${beforePlace.self_y},${beforePlace.self_z})`);

  // Place via 'p' key.
  console.log('phase: place');
  await page.keyboard.press('p');
  await new Promise(t => setTimeout(t, 2000));

  // Read recorder — find where legacy block ended up. If stacked on tall, z=97.
  const after = await recorder(page);
  const placedLegacy = (after.auth_item_sample || []).find(it => it.id === 25344);
  const tallAfter = (after.auth_item_sample || []).find(it => it.id === 25345);
  console.log('after-place: legacy=', JSON.stringify({x:placedLegacy?.x, y:placedLegacy?.y, z:placedLegacy?.z, owner:placedLegacy?.owner_id, top:placedLegacy?.collision_top_z}));
  console.log('after-place: tall=  ', JSON.stringify({x:tallAfter?.x, y:tallAfter?.y, z:tallAfter?.z, top:tallAfter?.collision_top_z}));

  if (!placedLegacy || placedLegacy.owner_id !== 65535) {
    console.error(`FAIL: legacy not placed as world-owned after 'p': ${JSON.stringify(placedLegacy)}`);
    process.exit(6);
  }

  // Stack pass criteria: the placed legacy block's bottom z should be at or
  // above the tall block's top z. If they overlap in XY, the place handler
  // should stack; otherwise placement was off the tall footprint and
  // sits on terrain (this proof tolerates "stacked OR off-footprint" but
  // logs which one happened for #9 verification.)
  const tallTop = tallAfter ? tallAfter.collision_top_z : (tall.collision_top_z);
  const xyOnTall = Math.abs(placedLegacy.x - tall.x) <= tall.half_extent &&
                   Math.abs(placedLegacy.y - tall.y) <= tall.half_extent;
  if (xyOnTall && Math.abs(placedLegacy.z - tallTop) < 1.0) {
    console.log(`PASS: legacy stacked on tall (z=${placedLegacy.z} == tall_top=${tallTop})`);
  } else if (xyOnTall && placedLegacy.z < tallTop) {
    console.error(`FAIL: placement landed inside tall block footprint but z<tall_top: z=${placedLegacy.z} < tall_top=${tallTop}`);
    process.exit(7);
  } else {
    console.log(`PARTIAL: legacy placed at (${placedLegacy.x},${placedLegacy.y},${placedLegacy.z}); not stacked on tall footprint (${tall.x},${tall.y}); this run did not demonstrate stack-snap`);
    process.exit(8);
  }
  await browser.close();
})();
