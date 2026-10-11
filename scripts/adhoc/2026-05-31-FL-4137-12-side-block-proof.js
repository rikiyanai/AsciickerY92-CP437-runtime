// Ad hoc script: FL-4137 #12 side-block proof.
// Drives the player toward the seeded tall_yy_block (height=40 > step-up=24),
// verifies the player is laterally blocked: z stays at terrain, x/y stay
// outside cube footprint.
// Created: 2026-05-31 (FL-4170)

// FL-4137 #12 side-block proof.
// Drives the player toward the seeded tall_yy_block (height=40 > step-up=24)
// and verifies the player is laterally blocked: z stays near terrain, and
// x/y do not enter the cube footprint.

const fs = require('fs');
const { chromium } = require('playwright');

const URL_ = process.env.PROBE_URL ||
  'http://localhost:38080/index.html?player=human&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
const HEADLESS = process.env.PROBE_HEADLESS !== '0';

function chromePath() {
  for (const p of [
    process.env.PROOF_CHROMIUM_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
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
  const clickResult = await page.evaluate(() => {
    const candidates = [
      document.getElementById('play-button'),
      document.querySelector('button[id*="play"], button[id*="Play"]'),
      document.querySelector('.play-button'),
      document.querySelector('button'),
    ].filter(Boolean);
    for (const b of candidates) {
      try { b.click(); return { ok: true, id: b.id || b.className }; }
      catch (e) { return { ok: false, error: String(e) }; }
    }
    if (typeof window.StartGame === 'function') {
      try { window.StartGame(); return { ok: true, id: 'StartGame()' }; }
      catch (e) { return { ok: false, error: String(e) }; }
    }
    return { ok: false, error: 'no PLAY surface' };
  });
  console.log('clickResult:', JSON.stringify(clickResult));
  await page.waitForTimeout(12000);

  // Retry until joined + auth_item_sample is populated.
  let initial = null;
  for (let attempt = 0; attempt < 30; attempt++) {
    initial = await page.evaluate(() => {
      if (!window.__akRec)
        window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
      const raw = window.__akRec() || '{}';
      const rec = JSON.parse(raw);
      return {
        akJoined: !!window.ak_joined,
        akMpStage: window.ak_mp_stage,
        recorderLen: raw.length,
        auth_item_count: (rec.auth_item_sample || []).length,
        auth_item_ids: (rec.auth_item_sample || []).map(it => it.id),
        rec,
      };
    });
    console.log(`attempt ${attempt}: joined=${initial.akJoined} stage=${initial.akMpStage} items=${initial.auth_item_count} ids=${JSON.stringify(initial.auth_item_ids.slice(0,5))}`);
    if (initial.auth_item_count > 0) break;
    await new Promise(r => setTimeout(r, 1000));
  }
  initial = initial.rec;
  console.log('first_5_ids:', JSON.stringify((initial.auth_item_sample || []).slice(0, 5).map(it => it.id)));
  const blocks = (initial.auth_item_sample || []).filter(it =>
    it.id === 25345 || it.id === 25344);
  console.log('seeded_blocks:', JSON.stringify(blocks.map(b => ({
    id: b.id, x: b.x, y: b.y, z: b.z, half: b.half_extent, top: b.collision_top_z,
  }))));
  const tall = blocks.find(b => b.id === 25345);
  if (!tall) { console.error('FAIL: tall block (id=25345) not in recorder'); process.exit(2); }
  console.log('player_start:', JSON.stringify({
    x: initial.self_x, y: initial.self_y, z: initial.self_z,
  }));

  // Tall block at (1.2, -69.6, 57), half=4, top=97.
  // Player spawn typically near (-2.8, -73.6, ~57). Need to walk NORTH (+Y in
  // some maps, or W key direction) toward (1.2, -69.6).
  // Test: drive forward 1.5s in several directions; record z over time.
  const trace = [];
  for (const dir of ['w', 'd', 'w', 'd', 'w', 'w']) {
    await pulse(page, dir, 300);
    const r = await recorder(page);
    trace.push({ dir, x: r.self_x, y: r.self_y, z: r.self_z,
                 dx_tall: r.self_x - tall.x, dy_tall: r.self_y - tall.y });
  }
  console.log('trace:', JSON.stringify(trace, null, 2));

  // Pass criteria:
  // - max z across trace is below tall block top (player did NOT step up).
  // - at no point did the player center enter the cube footprint.
  const halfTall = tall.half_extent;
  const insideTallFootprint = trace.some(p =>
    Math.abs(p.dx_tall) < halfTall && Math.abs(p.dy_tall) < halfTall);
  const maxZ = Math.max(...trace.map(p => p.z));
  const tallTop = tall.collision_top_z;
  const tallBottom = tall.z;

  console.log('summary:', JSON.stringify({
    maxZ, tallBottom, tallTop, halfTall, insideTallFootprint,
    blocked_laterally: !insideTallFootprint,
    no_step_up_onto_tall: maxZ < tallTop - 1.0,
  }, null, 2));

  if (insideTallFootprint && maxZ < tallTop - 1.0) {
    console.error('FAIL: player entered tall block footprint WITHOUT being on top — clipped through');
    process.exit(3);
  }
  if (maxZ >= tallTop - 1.0) {
    console.error('FAIL: player z reached tall block top — auto-step happened (should have been blocked since 40 > 24)');
    process.exit(4);
  }
  console.log('PASS: tall block laterally blocks player; no step-up snap occurred.');
  await browser.close();
})();
