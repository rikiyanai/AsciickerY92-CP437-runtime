import { fileURLToPath } from 'node:url';
// node ev_pose_shots.mjs PORT POSES.json OUT_DIR -- FL-4876 MAP-EDIT-01 verification captures at any pose, from the
// operator's page (/ = a3d.html), headless system Chrome, throwaway profile (the operator's profile is untouched), never
// presses Accept. One page load per run, so run it AFTER saving a map edit (the page loads the current output.a3d).
// Poses (E/N in EPSG:32654 metres):
//   {name, view:"fp",  E, N, look_E, look_N | bearing, pitch?, eye_m?, vfov?, game?}   first person (eye default 1.6 m)
//        game: true  = the GAME VIEW (editor_views.js): the Classic renderer with the game's own camera following the
//                      first-person point (target = the point, yaw = bearing, zoom 1, perspective, player sprite there);
//        game: "eye" = probe: the same engine camera moved to an eye-level eye (shows the depth = height limit);
//        absent/false = the mesh view (the earlier output). The UI default is the game view; captures stay explicit.
//        keys: [["w", 1500], ["e", 400]] holds each key that long after the pose is set (walk / turn through the page's own
//        key handlers); drag: [dx, dy] drags the mouse that many px on the first-person panel (look). shots.json then
//        holds cam.before (the pose) and cam.after (EV.state() after the input).
//   {name, view:"top", E, N, mpp?, sat?}                                         straight down; sat > 0 overlays the
//                                                                                GSI aerial at that opacity (0..1)
//   {name, view:"classic", E, N, yaw, zoom?, persp?}                             the Classic glyph render (#gl), the
//                                                                                camera set the way audit_shots.mjs does
//   {name, view:"sv", pano:"mapillary_<id>", bearing?, pitch?, fov?, height_m?,
//    dE?, dN?, dheading_deg?, dpitch_deg?, droll_deg?, overlay?, wipe?, edges?, opacity?, game?}
//        street-view guide (street_view_overlay.js): first-person camera at the panorama (sv_alignment.json applied when
//        saved; explicit fields override it for this view), bearing default = the panorama's photo heading, pitch 0,
//        fov = horizontal lens of both panels (default 75). Writes <name>_a3d.png and <name>_photo.png (the two 4:3
//        panels, same bearing and lens) and <name>_side.png (both with pano id, bearing, distance, projection labels);
//        <name>_overlay.png (opacity default 0.35) / _wipe.png / _edges.png only when that field is true.
//        game: true renders the left (A3D) panel in the game view (as for fp); absent = the mesh view.
// Writes OUT_DIR/<name>.png and OUT_DIR/shots.json (pose, resolved camera, SHA-256 per shot, page errors).
import { chromium } from 'playwright';
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
import { acquireCaptureLock } from './capture_lock.mjs';
const [port, posesPath, outDir] = process.argv.slice(2);
const poses = JSON.parse(fs.readFileSync(posesPath, 'utf8'));
await acquireCaptureLock('ev_pose_shots ' + posesPath);  // one headless Chrome / game capture at a time
fs.mkdirSync(outDir, { recursive: true });
// Operator 2026-10-17: "can i see it headed ... i should be able to see it in a chrome tab". The capture window is
// visible while the flag file .run/capture_headed exists (or FL4876_CAPTURE_HEADED=1), so the operator can watch what
// every agent's capture looks at; running agents pick it up at their next capture. Still a throwaway profile.
const headed = process.env.FL4876_CAPTURE_HEADED === '1' ||
  (process.env.FL4876_CAPTURE_HEADED !== '0' && fs.existsSync(fileURLToPath(new URL('../../../../.run/capture_headed', import.meta.url))));
// 2026-10-18: a capture whose (visible) browser window was closed hung for 30 min holding the single capture lock and
// stalled every other agent. Exit at once if the browser goes away; the lock module frees a dead pid's slot.
let closingOnPurpose = false;
// 2026-10-18: an sv batch held the single capture lock 11+ min with no image and stalled three agents. Watchdog:
// page load allowance plus 90 s per pose; past it the run exits non-zero (the lock frees with the dead pid).
const watchdogMs = 240000 + 90000 * poses.length;
setTimeout(() => { console.error(`capture watchdog: no finish after ${Math.round(watchdogMs / 1000)} s, exiting`); process.exit(4); },
  watchdogMs).unref();
const open = async (visible) => {
  const b = await chromium.launch({ channel: 'chrome', headless: !visible,
    args: visible ? ['--window-position=40,40', '--window-size=1620,1100', '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding', '--disable-background-timer-throttling'] : [] });
  b.on('disconnected', () => { if (closingOnPurpose) return; console.error('capture browser closed or crashed: exiting'); process.exit(3); });
  const pg = await (await b.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 })).newPage();
  await pg.goto(`http://127.0.0.1:${port}/`, { timeout: 60000 });
  return [b, pg];
};
let [browser, page] = await open(headed);
// 2026-10-18: with the Mac display asleep or locked a visible window gets innerWidth 0, #stage is never laid out and
// every screenshot times out 'element is not visible'. Fall back to an invisible capture for this run.
if (headed && await page.evaluate(() => innerWidth) === 0) {
  console.error('visible capture window has no size (display asleep or locked?): capturing headless this run');
  closingOnPurpose = true; await browser.close(); closingOnPurpose = false;
  [browser, page] = await open(false);
}
const errors = []; page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
await page.waitForTimeout(2000);
await page.addStyleTag({ content: '#insp,#where,#help,#layers,#palette,#chips,#ov,#dlg{display:none!important}' +
  // street-view panel rows: hidden so fp captures keep their earlier panel; sv captures hide the whole panel
  '#svCtl{display:none!important}body.svshot #evctl{display:none!important}' });
await page.evaluate(() => { if (typeof miniOn !== 'undefined' && miniOn) toggleMini(); });
const out = { port, started: new Date().toISOString(), shots: [], errors };
// world units from metres: the server's projection (terrain_metadata.json), the same one EV and /api/where use
const P = (await (await fetch(`http://127.0.0.1:${port}/api/ev/meta`)).json()).proj;
const toXY = (E, N) => [(E - P.origin_easting) * P.content_scale + P.offset_x, (N - P.origin_northing) * P.content_scale + P.offset_y];
const sha = f => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
let svUsed = false;
for (const p of poses) {
  let cam;
  // game view on / off for this pose (fp, sv); explicit per pose so nothing carries over
  await page.evaluate(g => EV.setGame(g[0], g[1]), [!!p.game, p.game === 'eye' ? 'eye' : 'player']);
  if (p.view === 'sv') {
    // per-pose defaults, nothing carried over from the previous pose: side by side, pitch 0, 75 deg horizontal lens
    // fov: the pose's, else the lens saved with the alignment, else 75
    const o = { pano: p.pano, layout: 'side', opacity: p.opacity ?? 0.35, pitch: p.pitch ?? 0, fov_default: 75 };
    for (const k of ['bearing', 'fov', 'height_m', 'dE', 'dN', 'dheading_deg', 'dpitch_deg', 'droll_deg']) if (p[k] != null) o[k] = p[k];
    if (o.bearing == null) {  // default: look along the panorama's photo heading (after any saved alignment)
      const s0 = await page.evaluate(o => SV.set(o), { pano: p.pano, layout: 'side' });
      o.bearing = s0.photo_heading_deg;
    }
    cam = await page.evaluate(o => SV.set(o), o);
    await page.waitForTimeout(900);
    const pngs = {};
    await page.evaluate(() => document.body.classList.add('svshot'));
    const stage = await page.$('#stage'), sb = await stage.boundingBox();
    const shoot = async (tag, rect) => { const file = path.join(outDir, `${p.name}_${tag}.png`);
      if (rect) await page.screenshot({ path: file, clip: { x: sb.x + rect[0], y: sb.y + rect[1], width: rect[2], height: rect[3] } });
      else await stage.screenshot({ path: file });
      pngs[tag] = { png: path.basename(file), sha256: sha(file) }; };
    const sideState = await page.evaluate(() => SV.state());
    await shoot('a3d', sideState.rects.gl); await shoot('photo', sideState.rects.photo); await shoot('side', null);
    for (const layout of ['overlay', 'wipe', 'edges']) if (p[layout]) {  // optional secondary views ("overlay": true ...)
      await page.evaluate(l => SV.set({ layout: l }), layout); await page.waitForTimeout(500); await shoot(layout, null);
    }
    if (p.overlay || p.wipe || p.edges) await page.evaluate(() => SV.set({ layout: 'side' }));
    const after = await page.evaluate(() => SV.state()), game = await page.evaluate(() => EV.state().game);
    out.shots.push({ pose: p, cam, cam_after: after, game, pngs });
    console.log(p.name, p.view, cam.pano, 'heading', cam.photo_heading_deg, cam.err || '');
    await page.evaluate(() => document.body.classList.remove('svshot'));
    svUsed = true;
    continue;
  }
  if (svUsed) await page.evaluate(() => SV.set({ on: false }));  // photo layer off again for the older pose kinds
  if (p.view === 'classic') {
    await page.evaluate(() => EV.set({ mode: 'off' }));
    cam = await page.evaluate(([p, x, y]) => { const c = S.cam; c.x = x; c.y = y; c.z = zAt(x, y);
      c.yaw = p.yaw || 0; c.zoom = p.zoom || 1; c.persp = p.persp || 0; S.dirty = true; camMoved(); return { ...c }; }, [p, ...toXY(p.E, p.N)]);
    await page.waitForTimeout(2200);
  } else if (p.view === 'fp') {
    const [x, y] = toXY(p.E, p.N);
    let bearing = p.bearing;
    if (bearing == null) bearing = Math.atan2(p.look_E - p.E, p.look_N - p.N) * 180 / Math.PI;
    let pitch = p.pitch;
    if (pitch == null && p.look_E != null) { const d = Math.hypot(p.look_E - p.E, p.look_N - p.N); pitch = d > 0 ? -Math.atan2((p.eye_m || 1.6) - (p.look_h_m ?? 1.6), d) * 180 / Math.PI : 0; }
    cam = await page.evaluate(o => EV.set(o), { mode: 'fp', fp: { x, y, yaw: -bearing, pitch: pitch || 0, eyeM: p.eye_m || 1.6, vfov: p.vfov || 60,
      game: !!p.game, gameCam: p.game === 'eye' ? 'eye' : 'player' } });
    await page.waitForTimeout(900);
    for (const [k, ms] of p.keys || []) { await page.keyboard.down(k); await page.waitForTimeout(ms); await page.keyboard.up(k); }
    if (p.drag) { const b = await (await page.$('#evgl')).boundingBox(), cx = b.x + b.width / 2, cy = b.y + b.height / 2;
      await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + p.drag[0], cy + p.drag[1], { steps: 10 }); await page.mouse.up(); }
    if (p.keys || p.drag) { await page.waitForTimeout(800); cam = { before: cam, after: await page.evaluate(() => EV.state()) }; }
  } else {
    const [x, y] = toXY(p.E, p.N);
    const sat = p.sat || 0;
    cam = await page.evaluate(o => EV.set(o), { mode: 'top', layout: sat > 0 ? 'overlay' : 'single', ref: 'sat',
      op: { a3d: 1, sat, map: 0 }, top: { x, y, mpp: p.mpp || 0.05 } });
    await page.waitForTimeout(900);
  }
  const file = path.join(outDir, `${p.name}.png`);
  await (await page.$(p.view === 'classic' ? '#gl' : '#stage')).screenshot({ path: file });
  out.shots.push({ pose: p, cam, png: path.basename(file), sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
  console.log(p.name, p.view);
}
out.finished = new Date().toISOString();
fs.writeFileSync(path.join(outDir, 'shots.json'), JSON.stringify(out, null, 1) + '\n');
console.log(out.shots.length, 'shots;', errors.length, 'page errors ->', outDir);
closingOnPurpose = true;
await browser.close();
