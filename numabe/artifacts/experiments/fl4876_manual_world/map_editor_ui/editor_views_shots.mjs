// node editor_views_shots.mjs PORT OUT_DIR -- FL-4876 evidence captures of the editor's extra views (editor_views.js)
// on the operator's page (/ = a3d.html), driven like audit_shots.mjs: headless system Chrome, throwaway profile (the
// operator's profile is untouched), never presses Accept (no owner file is written). Per site (goto 'station',
// goto 'temple'):
//   fp_*        first person, eye 1.6 m above zAt(), the goto heading and the opposite heading, plus a 2.5 s walk on W
//   split_sat_* top-down split, GSI aerial below; z0 = 0.30 m/px, z1 = after a real mouse-wheel zoom on the TOP panel
//   split_map_* the same with the OSM street map; z1 = after a wheel zoom on the BOTTOM panel
//   overlay_*   overlay at a3d 100 % + satellite 50 %, a3d 40 % + satellite 100 %, a3d 100 % + map 60 %
//   reg_*       one layer per capture (a3d only / aerial only, hud hidden) at 0.06 m/px for the registration check
// Writes the PNGs and OUT_DIR/shots.json (per shot: EV state, panel ground extents in UTM, S.cam, the location line,
// SHA-256), all before it exits.
import { chromium } from 'playwright';
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
const [port, outDir] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 })).newPage();
const errors = []; page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 300)); });
await page.goto(base + '/', { timeout: 60000 });
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
await page.waitForTimeout(2000);
// the normal-view panels (inspector opened by goto, layers, help) stay out of the captures; bar, status and the
// view controls stay in so each shot shows its mode, layout and opacity values
await page.addStyleTag({ content: '#insp,#where,#help,#layers,#palette,#chips,#dlg{display:none!important}' });
const out = { base, started: new Date().toISOString(), viewport: [1600, 1000], shots: [], errors };
const settle = ms => page.waitForTimeout(ms);
async function shot(name, kind, site, sel) {
  await settle(700);
  const file = path.join(outDir, name + '.png');
  if (sel) await (await page.$(sel)).screenshot({ path: file }); else await page.screenshot({ path: file });
  const info = await page.evaluate(() => ({ state: EV.state(), extents: EV.extents(), cam: { ...S.cam }, status: document.getElementById('status').textContent,
    where: document.getElementById('where').innerText.replace(/\s+/g, ' ').slice(0, 220) }));
  out.shots.push({ name, kind, site, png: path.basename(file), element: sel || 'page', ...info, sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
  console.log(name, info.state.mode, info.state.layout, info.state.ref, JSON.stringify(info.state.op), info.state.top.mpp.toFixed(3));
}
const ev = o => page.evaluate(o => EV.set(o), o);
async function wheelOn(panel, dy) { // real wheel events over the centre of the top ('gl') or bottom ('ref') panel
  const r = await page.evaluate(p => { const e = EV.extents().panels[p], s = document.getElementById('stage').getBoundingClientRect(); return [s.left + e[0] + e[2] / 2, s.top + e[1] + e[3] / 2]; }, panel);
  await page.mouse.move(r[0], r[1]); for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, dy / 6); await settle(60); }
}
for (const site of ['station', 'temple']) {
  await page.evaluate(q => goto(q), site); await settle(2500);
  const home = await page.evaluate(() => ({ x: S.cam.x, y: S.cam.y, yaw: S.cam.yaw }));
  // --- first person
  await ev({ mode: 'fp' }); await settle(500);
  await shot(`fp_${site}_fwd`, 'first_person', site);
  const f = await page.evaluate(() => EV.state().fp);
  await ev({ fp: { yaw: (f.yaw + 180) % 360 } }); await shot(`fp_${site}_back`, 'first_person', site);
  await ev({ fp: { yaw: f.yaw } }); await page.keyboard.down('w'); await settle(2500); await page.keyboard.up('w');
  await shot(`fp_${site}_walked`, 'first_person_after_walk', site);
  // --- top-down split, satellite then map, before / after a wheel zoom-in
  await ev({ mode: 'top', layout: 'split', ref: 'sat', op: { a3d: 1, sat: 1, map: 1 }, top: { x: home.x, y: home.y, mpp: 0.3 } });
  await shot(`split_sat_${site}_z0`, 'split_satellite', site);
  await wheelOn('gl', -600); await shot(`split_sat_${site}_z1`, 'split_satellite_after_wheel_zoom_on_top_panel', site);
  await ev({ ref: 'map', top: { x: home.x, y: home.y, mpp: 0.3 } });
  await shot(`split_map_${site}_z0`, 'split_map', site);
  await wheelOn('ref', -600); await shot(`split_map_${site}_z1`, 'split_map_after_wheel_zoom_on_bottom_panel', site);
  // --- overlay, three opacity pairs
  await ev({ layout: 'overlay', ref: 'sat', top: { x: home.x, y: home.y, mpp: 0.12 }, op: { a3d: 1, sat: 0.5, map: 0 } });
  await shot(`overlay_${site}_a100_s50`, 'overlay', site);
  await ev({ op: { a3d: 0.4, sat: 1, map: 0 } }); await shot(`overlay_${site}_a40_s100`, 'overlay', site);
  await ev({ op: { a3d: 1, sat: 0, map: 0.6 } }); await shot(`overlay_${site}_a100_m60`, 'overlay', site);
  // --- registration pair: one layer each, same view, hud and controls hidden
  const hide = await page.addStyleTag({ content: '#evhud,#evctl{visibility:hidden!important}' });
  await ev({ op: { a3d: 1, sat: 0, map: 0 }, top: { x: home.x, y: home.y, mpp: 0.06 } }); await shot(`reg_${site}_a3d`, 'registration_a3d_only', site, '#stage');
  await ev({ op: { a3d: 0, sat: 1, map: 0 } }); await shot(`reg_${site}_sat`, 'registration_aerial_only', site, '#stage');
  await ev({ op: { a3d: 1, sat: 0, map: 0 }, top: { x: home.x, y: home.y, mpp: 0.15 } }); await shot(`reg_${site}_wide_a3d`, 'registration_a3d_only', site, '#stage');
  await ev({ op: { a3d: 0, sat: 1, map: 0 } }); await shot(`reg_${site}_wide_sat`, 'registration_aerial_only', site, '#stage');
  await hide.evaluate(e => e.remove());
  await ev({ mode: 'off', op: { a3d: 1, sat: 1, map: 1 } }); await settle(1500);
  await shot(`normal_${site}_after`, 'normal_view_after_leaving', site);
}
out.finished = new Date().toISOString();
fs.writeFileSync(path.join(outDir, 'shots.json'), JSON.stringify(out, null, 1) + '\n');
console.log(out.shots.length, 'shots;', errors.length, 'page errors; ->', path.join(outDir, 'shots.json'));
await browser.close();
