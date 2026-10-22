// node ui_smoke.mjs [PORT] [SHOT_DIR] -- EDITOR-UX-02 browser check of the A3D editor (/) served by map_editor.py.
// Drives the real page in headless system Chrome (Playwright, throwaway profile; the operator's profile is untouched)
// and NEVER presses Accept: no owner file is written. Checks, each with the evidence it read back:
//   copy lat,lon / copy address / copy building id (location bar + inspector) -> clipboard text
//   double-click a building wall in select mode -> camera moved, zoom 1, status 'facing ...'
//   drop a transparent PNG file onto a wall -> facade capture opens in logo mode with cells
//   drop an image LINK (text/uri-list, as Google Images drags) -> server fetch -> capture opens, source = web image
// Receipt: facade_capture_checks/ui_smoke_<stamp>.json (repo); screenshots in SHOT_DIR.
import { chromium } from 'playwright';
import fs from 'node:fs'; import path from 'node:path';
const port = process.argv[2] || '8792', shots = process.argv[3] || '/tmp', base = `http://127.0.0.1:${port}`;
const HERE = path.dirname(new URL(import.meta.url).pathname);
const R = { base, started: new Date().toISOString(), checks: [], console_errors: [] };
const check = (name, pass, evidence) => { R.checks.push({ name, pass: !!pass, evidence }); console.log((pass ? 'PASS ' : 'FAIL ') + name + ' :: ' + JSON.stringify(evidence).slice(0, 300)); };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
const page = await ctx.newPage();
page.on('console', m => { if (m.type() === 'error') R.console_errors.push(m.text().slice(0, 300)); });
page.on('pageerror', e => R.console_errors.push('pageerror: ' + String(e.stack || e).slice(0, 500)));
page.on('response', r => { if (r.status() >= 400) R.console_errors.push(`HTTP ${r.status()} ${r.url().slice(0, 200)}`); });
const shot = async n => { const p = path.join(shots, `ui_${n}.png`); await page.screenshot({ path: p }); return p; };
const status = () => page.$eval('#status', e => e.textContent);
const clip = () => page.evaluate(() => navigator.clipboard.readText());
const settle = ms => page.waitForTimeout(ms);
try {
  await page.goto(base + '/', { timeout: 60000 });
  await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
  await settle(2500);
  check('page loads, map ready', true, { status: await status() });
  R.shot_home = await shot('1_home');

  // --- go to Lawson: selects the building, location bar follows the camera
  await page.evaluate(() => goto('lawson')); await settle(2500);
  const sel = await page.evaluate(() => S.sel.map(s => s.kind + ':' + s.id));
  check('goto lawson selects a building', sel.some(s => s.startsWith('building:')), { sel });
  const whereHtml = await page.$eval('#where', e => e.innerText);
  const hasLinks = await page.evaluate(() => ['wCopy', 'wAddr', 'wId'].map(i => !!document.getElementById(i)));
  check('location bar shows copy lat,lon / address / id', hasLinks.every(Boolean), { hasLinks, where: whereHtml.slice(0, 200) });
  if (hasLinks[0]) { await page.click('#wCopy'); await settle(400); const c = await clip(); check('copy lat,lon -> clipboard', /^35\.\d+, 139\.\d+$/.test(c), { clipboard: c }); }
  if (hasLinks[1]) { await page.click('#wAddr'); await settle(800); const c = await clip(); check('copy address (location bar) -> clipboard', /田園調布|大田区|東京都/.test(c), { clipboard: c, status: await status() }); }
  if (hasLinks[2]) { await page.click('#wId'); await settle(400); const c = await clip(); check('copy building id (location bar) -> clipboard', /^(way|relation)\/\d+$|^bldg_/.test(c), { clipboard: c }); }
  // inspector buttons
  await page.evaluate(() => { const p = document.getElementById('insp'); p.style.display = 'block'; }); await settle(1200);
  const addrLine = await page.$eval('#inspBody', e => (e.querySelector('[data-addr]') || {}).textContent || '');
  check('inspector shows the address line', addrLine && !/…|unknown|failed/.test(addrLine), { addrLine });
  const ib = await page.$('#inspBody [data-a="addr"]');
  if (ib) { await page.evaluate(() => navigator.clipboard.writeText('-')); await ib.click(); await settle(800); const c = await clip(); check('inspector Copy address -> clipboard', /田園調布|大田区|東京都/.test(c), { clipboard: c }); }
  const ii = await page.$('#inspBody [data-a="bid"]');
  if (ii) { await page.evaluate(() => navigator.clipboard.writeText('-')); await ii.click(); await settle(400); const c = await clip(); check('inspector Copy id -> clipboard', c && c !== '-', { clipboard: c }); }
  R.shot_lawson = await shot('2_lawson_selected');

  // --- double-click a building wall (select mode): camera stands in front of the face
  await page.evaluate(() => setMode('select'));
  const gl = await page.$('#gl'), bb = await gl.boundingBox();
  // step back and turn first, so a working double-click must visibly move and turn the camera
  await page.evaluate(() => { const c = S.cam, r = c.yaw * Math.PI / 180; c.x += Math.sin(r) * 25; c.y -= Math.cos(r) * 25; c.yaw = (c.yaw + 35) % 360; c.zoom = 0.7; S.dirty = true; camMoved(); });
  await settle(1500);
  // find a canvas point over a building near the centre
  const target = await page.evaluate(async ([w, h]) => {
    for (let r = 0; r < 300; r += 20) for (let a = 0; a < 360; a += 30) {
      const x = w / 2 + r * Math.cos(a * Math.PI / 180), y = h / 2 + r * Math.sin(a * Math.PI / 180);
      const t = await thingAt([x, y]); if (t && (t.kind === 'building' || t.building)) return { x, y, id: t.kind === 'building' ? t.id : t.building };
    } return null }, [bb.width, bb.height]);
  if (!target) check('double-click: found a building under the canvas', false, {});
  else {
    const before = await page.evaluate(() => ({ ...S.cam }));
    // record every status message the page shows (the status bar is also rewritten by the render loop)
    await page.evaluate(() => { window.__st = []; new MutationObserver(() => window.__st.push(document.getElementById('status').textContent)).observe(document.getElementById('status'), { childList: true, characterData: true, subtree: true }); });
    await page.mouse.dblclick(bb.x + target.x, bb.y + target.y); await settle(2500);
    const after = await page.evaluate(() => ({ ...S.cam })), seen = await page.evaluate(() => window.__st.filter(s => !/^target x/.test(s)));
    const moved = Math.hypot(after.x - before.x, after.y - before.y);
    check('double-click a wall -> camera moves to face it (zoom 1, facing message)', moved > 1 && after.zoom === 1 && seen.some(s => /^facing /.test(s)),
      { building: target.id, moved_units: +moved.toFixed(1), yaw_before: before.yaw, yaw_after: after.yaw, zoom_before: before.zoom, zoom_after: after.zoom, messages: seen.slice(0, 4) });
    const lastingMsg = await status();
    check('status message stays readable 2.5 s after the action', /^facing /.test(lastingMsg), { status_now: lastingMsg.slice(0, 120) });
    R.shot_dblclick = await shot('3_after_dblclick');
  }

  // --- drop a transparent PNG file (a logo) onto the wall in front of the camera
  const centre = await page.evaluate(async ([w, h]) => { for (let r = 0; r < 300; r += 15) for (let a = 0; a < 360; a += 30) {
      const x = w / 2 + r * Math.cos(a * Math.PI / 180), y = h / 2 + r * Math.sin(a * Math.PI / 180);
      const t = await thingAt([x, y]); if (t && (t.kind === 'building' || t.building)) return { x, y } } return null }, [bb.width, bb.height]);
  const dropAt = async (pt, fill) => page.evaluate(async ([x, y, rect, fillKind]) => {
    const dt = new DataTransfer();
    if (fillKind === 'file') {
      const c = new OffscreenCanvas(240, 80), g = c.getContext('2d');   // logo on a transparent background
      g.fillStyle = '#00a040'; g.fillRect(0, 0, 240, 22); g.fillStyle = '#ffffff'; g.fillRect(0, 22, 240, 36); g.fillStyle = '#0060c0'; g.fillRect(0, 58, 240, 22);
      g.clearRect(100, 30, 40, 20);                                       // a transparent hole inside the band
      const blob = await c.convertToBlob({ type: 'image/png' }); dt.items.add(new File([blob], 'logo.png', { type: 'image/png' }));
    } else { dt.setData('text/uri-list', fillKind); dt.setData('text/plain', fillKind); }
    const el = document.getElementById('gl'), o = { bubbles: true, cancelable: true, clientX: rect.x + x, clientY: rect.y + y, dataTransfer: dt };
    el.dispatchEvent(new DragEvent('dragenter', o)); el.dispatchEvent(new DragEvent('dragover', o)); el.dispatchEvent(new DragEvent('drop', o));
  }, [pt.x, pt.y, { x: bb.x, y: bb.y }, fill]);
  const fcState = () => page.evaluate(() => { const f = document.getElementById('fc'); if (!f || getComputedStyle(f).display === 'none') return { open: false };
    return { open: true, title: (document.getElementById('fcTitle') || {}).textContent, type: document.getElementById('fcType').value, kind: document.getElementById('fcKind').value,
      cells: (document.getElementById('fcCells') || {}).innerText?.slice(0, 160), cost: (document.getElementById('fcCost') || {}).innerText?.slice(0, 160) } });
  const closeFc = () => page.evaluate(() => { const b = document.getElementById('fcCancel'); if (b) b.click() });
  if (!centre) check('drop: found a building to drop on', false, {});
  else {
    await dropAt(centre, 'file'); await settle(6000);
    const s1 = await fcState();
    const live1 = await page.evaluate(() => { const m = (document.getElementById('fcCost') || {}).innerText || ''; return +((m.match(/(\d+) sprite\(s\) live/) || [])[1] || 0) });
    check('drop transparent PNG file -> capture opens in logo mode, converts, previews in the map', s1.open && s1.type === 'logo' && /cells/.test(s1.cells || '') && live1 > 0, { ...s1, live_sprites: live1, status: await status() });
    R.shot_logo_drop = await shot('4_logo_file_drop'); await closeFc(); await settle(500);
    const url = 'https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png';
    await dropAt(centre, url); await settle(6000);
    const s2 = await fcState();
    check('drop image LINK (Google Images style) -> server fetch -> capture opens as web image, logo mode, converts', s2.open && s2.kind === 'web_image_reference' && s2.type === 'logo' && /cells/.test(s2.cells || ''), { url, ...s2, status: await status() });
    R.shot_link_drop = await shot('5_link_drop'); await closeFc(); await settle(500);
  }
} catch (e) { check('run completed without exception', false, { error: String(e).slice(0, 400) }); }
R.console_errors = R.console_errors.slice(0, 40);
check('no page errors in console', R.console_errors.length === 0, { errors: R.console_errors.slice(0, 5) });
R.finished = new Date().toISOString(); R.passed = R.checks.filter(c => c.pass).length; R.total = R.checks.length;
const out = path.join(HERE, '..', 'facade_capture_checks', `ui_smoke_${R.started.replace(/[:.]/g, '-')}.json`);
fs.writeFileSync(out, JSON.stringify(R, null, 1) + '\n');
console.log(`${R.passed}/${R.total} passed; receipt ${out}`);
await browser.close();
