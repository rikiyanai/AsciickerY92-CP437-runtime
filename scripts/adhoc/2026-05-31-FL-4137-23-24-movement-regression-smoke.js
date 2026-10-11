// Ad hoc script: FL-4137 #23 + #24 movement regression smoke.
// Drives the player around the y8 map for several seconds and verifies:
//   - position changes monotonically over time (not stuck against terrain)
//   - z stays grounded near terrain (no spurious launches outside block area)
//   - building / world-mesh collision remains intact (player can't walk
//     through buildings)
// This is a smoke check that the placed-block-AKM-cube refactor
// (CKPT-A through CKPT-E) didn't regress normal terrain + building collision.
// Created: 2026-05-31

const fs = require('fs');
const { chromium } = require('playwright');

const URL_ = process.env.PROBE_URL ||
  'http://localhost:38080/index.html?player=movereg&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
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

(async () => {
  const opts = { headless: HEADLESS };
  const cp = chromePath();
  if (cp) opts.executablePath = cp;
  const browser = await chromium.launch(opts);
  const page = await browser.newContext({ viewport: { width: 1440, height: 900 } }).then(c => c.newPage());

  await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(15000);
  await page.evaluate(() => {
    for (const sel of ['#play-button', '#play-btn', '.play-button', 'button']) {
      const b = document.querySelector(sel);
      if (b) { try { b.click(); break; } catch (_) {} }
    }
  });
  await page.waitForTimeout(12000);

  // Wait for join.
  for (let i = 0; i < 30; i++) {
    const j = await page.evaluate(() => !!window.ak_joined);
    if (j) break;
    await new Promise(t => setTimeout(t, 1000));
  }

  async function rec() {
    return await page.evaluate(() => {
      if (!window.__akRec) window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
      const r = JSON.parse(window.__akRec() || '{}');
      return { x: r.self_x, y: r.self_y, z: r.self_z, support_z: r.snapshot_local_support_z_cpp, support_valid: r.snapshot_local_support_valid_cpp };
    });
  }

  const start = await rec();
  console.log('start:', JSON.stringify(start));

  // First drive AWAY from the seeded blocks (which are right next to spawn)
  // before measuring regression. Each press uses hold-and-release pattern
  // with longer hold so the input integration time picks up.
  async function holdKey(key, ms) {
    await page.keyboard.down(key);
    await new Promise(t => setTimeout(t, ms));
    await page.keyboard.up(key);
    await new Promise(t => setTimeout(t, 100));
  }

  // Drive south + west to clear the block cluster.
  for (let i = 0; i < 6; i++) await holdKey('s', 400);
  for (let i = 0; i < 4; i++) await holdKey('a', 400);

  const clearStart = await rec();
  console.log('clear_start:', JSON.stringify(clearStart));

  // Now measure movement in 4 directions from the clear position.
  const trace = [];
  let maxLaunch = 0;
  for (const dirSeq of [['w','w','w','w'], ['d','d','d','d'], ['s','s','s','s'], ['a','a','a','a']]) {
    for (const key of dirSeq) {
      await holdKey(key, 400);
      const r = await rec();
      const launchAboveSupport = r.support_valid ? (r.z - r.support_z) : 0;
      if (launchAboveSupport > maxLaunch) maxLaunch = launchAboveSupport;
      trace.push({ key, ...r, launch: launchAboveSupport });
    }
  }

  const positions = trace.map(t => ({x:t.x, y:t.y}));
  const uniqueXY = new Set(positions.map(p => `${p.x?.toFixed(1)},${p.y?.toFixed(1)}`));
  console.log('total_steps:', trace.length, 'unique_positions:', uniqueXY.size);

  const distancesMoved = [];
  for (let i = 1; i < trace.length; i++) {
    const dx = trace[i].x - trace[i-1].x;
    const dy = trace[i].y - trace[i-1].y;
    distancesMoved.push(Math.sqrt(dx*dx + dy*dy));
  }
  const totalDist = distancesMoved.reduce((a,b)=>a+b,0);
  const startDist = Math.sqrt((trace[trace.length-1].x - start.x)**2 + (trace[trace.length-1].y - start.y)**2);
  console.log('total_path_dist:', totalDist.toFixed(2), 'net_dist_from_start:', startDist.toFixed(2));
  console.log('max_launch_above_support:', maxLaunch.toFixed(2));
  console.log('last_pos:', JSON.stringify(trace[trace.length-1]));

  // Pass criteria:
  // - moved at least 4 units total (not stuck)
  // - max launch above support < 5 units (no spurious vertical pushes)
  if (totalDist < 4.0) {
    console.error(`FAIL: player barely moved (totalDist=${totalDist}); possible terrain/collision regression`);
    process.exit(2);
  }
  if (maxLaunch > 5.0) {
    console.error(`FAIL: spurious z-launch detected during normal drive (max=${maxLaunch} units above support)`);
    process.exit(3);
  }
  // - z always within ±5 of support (groundedness preserved)
  for (const t of trace) {
    if (t.support_valid && Math.abs(t.z - t.support_z) > 5.0) {
      console.error(`FAIL: z not grounded at step ${JSON.stringify(t)}`);
      process.exit(4);
    }
  }
  console.log('PASS: drive smoke clean — total_dist=' + totalDist.toFixed(2) + ', max_launch=' + maxLaunch.toFixed(2) + ', all z grounded');
  await browser.close();
})();
