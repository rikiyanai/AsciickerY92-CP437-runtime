// Ad hoc script: FL-4137 #17 click-pickup proof.
// Verifies the placed block (legacy_yy_block id=25344) can be picked up by
// clicking on its screen position with the mouse — equivalent to the
// digit-press pickup path but exercising the mouse input route.
// Created: 2026-05-31

const fs = require('fs');
const { chromium } = require('playwright');

const URL_ = process.env.PROBE_URL ||
  'http://localhost:38080/index.html?player=clickproof&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
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
      return { joined: !!window.ak_joined, items: rec.auth_item_sample || [], stripIds: rec.auth_pickup_strip_item_ids || [], rec };
    });
    if (r.joined && r.items.length > 0) break;
    await new Promise(t => setTimeout(t, 1000));
  }
  if (!r.joined) { console.error('FAIL: never joined'); process.exit(2); }
  const legacy = r.items.find(it => it.id === 25344);
  console.log('legacy:', JSON.stringify({x:legacy.x, y:legacy.y, z:legacy.z, top:legacy.collision_top_z, owner:legacy.owner_id}));
  console.log('pickup_strip_ids:', JSON.stringify(r.stripIds));
  console.log('player_start:', JSON.stringify({x: r.rec.self_x, y: r.rec.self_y, z: r.rec.self_z}));

  // Wait for legacy to be in pickup strip (player is already in range based on
  // initial recorder probe).
  let strip = null;
  for (let i = 0; i < 15; i++) {
    const s = await page.evaluate(() => {
      if (!window.__akRec) window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
      const rec = JSON.parse(window.__akRec() || '{}');
      return { stripIds: rec.auth_pickup_strip_item_ids || [], rec };
    });
    if (s.stripIds.includes(25344)) { strip = s; break; }
    // Walk slightly toward legacy to enter pickup range.
    await page.keyboard.down('d');
    await new Promise(t => setTimeout(t, 200));
    await page.keyboard.up('d');
    await new Promise(t => setTimeout(t, 100));
  }
  if (!strip) { console.error(`FAIL: legacy never in pickup strip`); process.exit(3); }
  console.log(`legacy in pickup strip; player_pos=(${strip.rec.self_x},${strip.rec.self_y},${strip.rec.self_z})`);

  // Pickup-strip click semantics: clicking on the strip slot (icon at bottom)
  // requests pickup of that strip item via the same path as a digit press.
  // The strip's screen position is in pickup_strip_xarr + pickup_strip_ylo/yhi.
  const blockScreen = await page.evaluate(() => {
    if (!window.__akRec) window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
    const rec = JSON.parse(window.__akRec() || '{}');
    const stripIds = rec.auth_pickup_strip_item_ids || [];
    const stripIdx = stripIds.indexOf(25344);
    return {
      pickup_strip_xarr: rec.pickup_strip_xarr || [],
      pickup_strip_ylo: rec.pickup_strip_ylo,
      pickup_strip_yhi: rec.pickup_strip_yhi,
      strip_idx: stripIdx,
      render_w: rec.render_buf_width || rec.local_render_buf_width || 160,
      render_h: rec.render_buf_height || rec.local_render_buf_height || 90,
    };
  });
  console.log('strip_screen:', JSON.stringify(blockScreen));
  if (!blockScreen || blockScreen.strip_idx < 0 || !blockScreen.pickup_strip_xarr.length) {
    console.error(`FAIL: legacy not in pickup strip xarr (idx=${blockScreen?.strip_idx})`);
    process.exit(4);
  }

  // Convert render-buf cell coords (e.g. 160x90) to canvas pixel coords.
  // Canvas is full-window. Find the canvas element.
  const canvasBounds = await page.evaluate(() => {
    // Game's main visible canvas is id="canvas" (class="asciicker") per
    // game_web.html. Avoid hidden offscreen canvases.
    const all = [...document.querySelectorAll('canvas')].map(c => {
      const r = c.getBoundingClientRect();
      const cs = window.getComputedStyle(c);
      return {
        id: c.id, cls: c.className,
        x: r.x, y: r.y, w: r.width, h: r.height,
        visible: cs.display !== 'none' && cs.visibility !== 'hidden' && !c.hidden,
      };
    });
    // Prefer id="canvas", else any visible non-rain canvas, else biggest.
    const c1 = all.find(c => c.id === 'canvas' && c.visible);
    if (c1) return c1;
    const c2 = all.filter(c => c.id !== 'rain-canvas' && c.visible).sort((a,b) => (b.w*b.h)-(a.w*a.h))[0];
    if (c2) return c2;
    return all[0] || null;
  });
  console.log('canvas_bounds:', JSON.stringify(canvasBounds));
  if (!canvasBounds) { console.error('FAIL: no canvas'); process.exit(5); }

  const stripCellX = blockScreen.pickup_strip_xarr[blockScreen.strip_idx];
  const stripCellY = Math.floor((blockScreen.pickup_strip_ylo + blockScreen.pickup_strip_yhi) / 2);
  const px = canvasBounds.x + (stripCellX / blockScreen.render_w) * canvasBounds.w;
  const py = canvasBounds.y + (stripCellY / blockScreen.render_h) * canvasBounds.h;
  console.log(`clicking pickup-strip slot ${blockScreen.strip_idx} at canvas (${px.toFixed(1)},${py.toFixed(1)}) for cell (${stripCellX},${stripCellY})`);
  await page.mouse.click(px, py);
  await new Promise(t => setTimeout(t, 1500));

  // Verify legacy became owned.
  let owned = null;
  for (let i = 0; i < 15; i++) {
    const s = await page.evaluate(() => {
      if (!window.__akRec) window.__akRec = window.Module.cwrap('RecorderStateJson', 'string', []);
      const rec = JSON.parse(window.__akRec() || '{}');
      const it = (rec.auth_item_sample || []).find(s => s.id === 25344);
      return it ? { id: it.id, owner_id: it.owner_id, state_flags: it.state_flags } : null;
    });
    if (s && s.owner_id !== 65535) { owned = s; break; }
    await new Promise(t => setTimeout(t, 300));
  }
  if (!owned) {
    console.error(`FAIL: legacy not picked up by click. Last state: ${JSON.stringify(owned)}`);
    process.exit(6);
  }
  console.log(`PASS: legacy block picked up by mouse click; owner=${owned.owner_id}`);
  await browser.close();
})();
