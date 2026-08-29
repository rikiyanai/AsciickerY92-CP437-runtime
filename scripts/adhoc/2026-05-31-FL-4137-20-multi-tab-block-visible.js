// Ad hoc script: FL-4137 #20 multi-tab block visibility.
// Opens two independent browser contexts (player A + player B) to the same
// local server, joins both, and asserts both recorders see the same placed
// blocks (id=25344, id=25345) at the same positions.
// Created: 2026-05-31

const fs = require('fs');
const { chromium } = require('playwright');

const URL_TAB1 = 'http://localhost:38080/index.html?player=tab1&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
const URL_TAB2 = 'http://localhost:38080/index.html?player=tab2&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
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

async function bootPage(browser, url, label) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(15000);
  await page.evaluate(() => {
    const candidates = [
      document.getElementById('play-button'),
      document.querySelector('button[id*="play"], button[id*="Play"]'),
      document.querySelector('.play-button'),
      document.querySelector('button'),
    ].filter(Boolean);
    for (const b of candidates) {
      try { b.click(); break; } catch (_) {}
    }
  });
  await page.waitForTimeout(12000);
  // Wait until joined + items populated.
  for (let i = 0; i < 30; i++) {
    const state = await page.evaluate(() => {
      if (!window.__akRec)
        window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
      const rec = JSON.parse(window.__akRec() || '{}');
      return {
        joined: !!window.ak_joined,
        items: (rec.auth_item_sample || []).map(it => ({
          id: it.id, x: it.x, y: it.y, z: it.z,
          half_extent: it.half_extent, collision_top_z: it.collision_top_z,
        })),
      };
    });
    if (state.joined && state.items.length > 0) {
      console.log(`[${label}] joined, ${state.items.length} items visible`);
      return { ctx, page, items: state.items };
    }
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error(`${label}: did not join + see items after 30s`);
}

(async () => {
  const opts = { headless: HEADLESS };
  const cp = chromePath();
  if (cp) opts.executablePath = cp;
  const browser = await chromium.launch(opts);

  const a = await bootPage(browser, URL_TAB1, 'tab1');
  const b = await bootPage(browser, URL_TAB2, 'tab2');

  const idsA = new Set(a.items.map(it => it.id));
  const idsB = new Set(b.items.map(it => it.id));
  console.log('tab1_ids:', JSON.stringify([...idsA]));
  console.log('tab2_ids:', JSON.stringify([...idsB]));

  const requiredIds = [25344, 25345];
  const missingA = requiredIds.filter(id => !idsA.has(id));
  const missingB = requiredIds.filter(id => !idsB.has(id));

  if (missingA.length) {
    console.error(`FAIL: tab1 missing block ids: ${missingA.join(',')}`);
    process.exit(2);
  }
  if (missingB.length) {
    console.error(`FAIL: tab2 missing block ids: ${missingB.join(',')}`);
    process.exit(3);
  }

  // Per-block position parity: both tabs must agree on x/y/z/half/top.
  for (const id of requiredIds) {
    const itA = a.items.find(it => it.id === id);
    const itB = b.items.find(it => it.id === id);
    const drift = (k) => Math.abs(itA[k] - itB[k]);
    const tol = 0.01;
    const mismatches = ['x','y','z','half_extent','collision_top_z']
      .filter(k => drift(k) > tol);
    if (mismatches.length) {
      console.error(`FAIL: block ${id} parity mismatch on [${mismatches.join(',')}]: tab1=${JSON.stringify(itA)} tab2=${JSON.stringify(itB)}`);
      process.exit(4);
    }
    console.log(`block ${id}: tab1==tab2 at pos=(${itA.x},${itA.y},${itA.z}) top=${itA.collision_top_z}`);
  }

  console.log('PASS: both tabs see the same placed blocks at the same positions.');
  await browser.close();
})();
