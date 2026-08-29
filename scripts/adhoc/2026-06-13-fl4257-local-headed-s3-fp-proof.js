// FL-4257 — local headed proof of spherical first-person + look-steers-movement.
// Drives the web client (served from .web/) against a local native .run/server
// loaded with a spherical-curvature map. Verifies (visually, via screenshots):
//   1. game spawns and renders,
//   2. first-person toggles (F4),
//   3. turning the look (keyboard 'l' = same body-yaw owner as mouse-look) while
//      holding forward (W) moves the player along its facing,
//   4. the spherical minimap renders.
// Usage: node <this> <webPort> <serverHostPort> <outDir>
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const webPort = process.argv[2] || '8099';
  const serverAddr = process.argv[3] || 'localhost:8077';
  const outDir = process.argv[4] || '/tmp/fl4257-proof';
  const url = `http://localhost:${webPort}/index.html`;
  const jsonOnlyAfterFirstPerson = process.env.FL4257_JSON_ONLY_AFTER_FP === '1';
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({
    headless: false, // need real WebGL for the spherical GPU terrain render
    channel: 'chrome', // use system Google Chrome (installed browser version mismatch)
    args: ['--use-gl=angle', '--ignore-gpu-blocklist', '--enable-webgl']
  });
  const page = await browser.newPage({ viewport: { width: 444, height: 280 } });
  page.setDefaultTimeout(180000);
  page.on('console', m => { const t = m.text(); if (/curv|s3|spher|MAP PATH|GAME_STATE|ENTERED_WORLD|error|fail/i.test(t)) console.log('[browser]', t); });

  async function observation(label) {
    const raw = await page.evaluate(() => {
      if (!window.Module || !window.Module.cwrap) return '{"error":"no-module"}';
      if (!window.__fl4257Obs) window.__fl4257Obs = window.Module.cwrap('ClientObservationJsonV1', 'string', []);
      return window.__fl4257Obs();
    });
    const parsed = JSON.parse(raw);
    fs.writeFileSync(`${outDir}/${label}.json`, JSON.stringify(parsed, null, 2));
    console.log(`[obs:${label}]`, JSON.stringify({
      title: await page.title(),
      curvature: [parsed.s3_curvature_present, parsed.s3_curvature_kind, parsed.s3_curvature_kappa],
      first_person: parsed.s3_first_person,
      pitch: parsed.s3_free_pitch,
      s3_active: parsed.s3_render_active,
      projection_identity: parsed.s3_projection_identity,
      sky: parsed.s3_sky_sample_count,
      primary: parsed.s3_primary_image_count,
      antipodal: parsed.s3_antipodal_image_count,
      water: parsed.s3_water_termination_count,
      gpu: parsed.s3_gpu_terrain_used,
      pos: [parsed.local_pos_x, parsed.local_pos_y, parsed.local_pos_z],
      yaw: parsed.camera_yaw,
      render_us: parsed.client_render_duration_us,
    }));
    return parsed;
  }

  async function shot(name) {
    await page.screenshot({ path: `${outDir}/${name}.png`, timeout: 180000 });
  }

  console.log('goto', url);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

  await page.waitForSelector('#play-btn', { timeout: 60000 });
  await page.fill('#player-name', 'S3TEST');
  // server-addr may be hidden; set its value directly too
  await page.evaluate((addr) => {
    const s = document.getElementById('server-addr'); if (s) s.value = addr;
  }, serverAddr);
  try { await page.fill('#server-addr', serverAddr); } catch (e) {}
  console.log('clicking play, server=', serverAddr);
  await page.click('#play-btn');

  // let it download assets, connect, and load the world
  await page.waitForFunction(() => document.title.includes('GAME RUNNING'), null, { timeout: 120000 });
  await page.waitForTimeout(5000);
  await observation('01_spawn');
  await shot('01_spawn');

  // focus the canvas for keyboard input
  const canvas = await page.$('canvas');
  if (canvas) { const b = await canvas.boundingBox(); if (b) await page.mouse.click(b.x + b.width/2, b.y + b.height/2); }
  await page.waitForTimeout(1500);
  await observation('02_focused');
  await shot('02_focused');

  // enter first person
  await page.keyboard.press('F4');
  await page.waitForTimeout(1500);
  const fp = await observation('03_firstperson');
  if (fp.s3_first_person !== 1) throw new Error(`first-person did not toggle: ${fp.s3_first_person}`);
  if (fp.s3_curvature_present !== 1 || fp.s3_curvature_kind !== 1) throw new Error(`curvature missing: ${JSON.stringify([fp.s3_curvature_present, fp.s3_curvature_kind])}`);
  if (fp.s3_render_active !== 1) throw new Error(`S3 render inactive: ${fp.s3_render_active}`);
  if (fp.s3_projection_identity !== 1395869489) throw new Error(`wrong projection identity: ${fp.s3_projection_identity}`);
  await shot('03_firstperson');

  // hold forward + turn the look ('l'); body should turn and travel should follow
  await page.keyboard.down('w');
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('l');
    await page.waitForTimeout(250);
    if (i === 5) {
      await observation('04_turn_mid');
      if (!jsonOnlyAfterFirstPerson) await shot('04_turn_mid');
    }
  }
  await page.waitForTimeout(jsonOnlyAfterFirstPerson ? 12000 : 1500);
  await observation('05_turn_move');
  if (!jsonOnlyAfterFirstPerson) await shot('05_turn_move');
  await page.keyboard.up('w');

  // straight run to read the minimap track
  await page.keyboard.down('w');
  await page.waitForTimeout(jsonOnlyAfterFirstPerson ? 18000 : 4000);
  await observation('06_forward_run');
  if (!jsonOnlyAfterFirstPerson) await shot('06_forward_run');
  await page.keyboard.up('w');

  await browser.close();
  console.log('done; screenshots in', outDir);
})().catch(e => { console.error('PROOF ERROR', e); process.exit(1); });
