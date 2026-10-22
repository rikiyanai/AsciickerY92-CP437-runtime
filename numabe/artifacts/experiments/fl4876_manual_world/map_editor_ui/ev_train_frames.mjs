// node ev_train_frames.mjs PORT OUT_DIR [FRAMES=40] [INTERVAL_MS=1500] -- FL-4876 MAP-EDIT-01: the train GIF capture, made in the
// operator's editor page (/ = a3d.html) with the Classic view (#gl) at E 379771.1 N 3938490.6 (a3d 417.7, 421.1), yaw 0, zoom 1.
// Modelled on ev_pose_shots.mjs: headless system Chrome, throwaway profile, one page load, capture lock, never presses Accept.
// Frames are written as production_view-K.png (the names train_gif_assemble.py reads) with frames.json (SHA-256 per frame,
// wall-clock stamp per frame, whether the page exposes a train clock and the runtime's exported train symbols).
// FINDING (checked in the page, see frames.json "runtime"): the editor's wasm Classic runtime does not link
// engine/classic_train.cpp (no ClassicTrains* export, no "train" string in .web-classic/index.wasm), and a3d.html neither loads
// output.trains.json nor has a time or offset control, so every frame is the same still map. This script records that
// (distinct_sha) instead of faking motion.
import { chromium } from 'playwright';
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
import { acquireCaptureLock } from './capture_lock.mjs';
const [port, outDir, nArg, ivArg] = process.argv.slice(2);
const N = parseInt(nArg || '40', 10), IV = parseInt(ivArg || '1500', 10);
await acquireCaptureLock('ev_train_frames ' + outDir);
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 })).newPage();
const errors = []; page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
await page.goto(`http://127.0.0.1:${port}/`, { timeout: 60000 });
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
await page.waitForTimeout(2000);
await page.addStyleTag({ content: '#insp,#where,#help,#layers,#palette,#chips,#ov,#dlg{display:none!important}' });
await page.evaluate(() => { if (typeof miniOn !== 'undefined' && miniOn) toggleMini(); });
const P = (await (await fetch(`http://127.0.0.1:${port}/api/ev/meta`)).json()).proj;
const [x, y] = [(379771.1 - P.origin_easting) * P.content_scale + P.offset_x, (3938490.6 - P.origin_northing) * P.content_scale + P.offset_y];
await page.evaluate(() => EV.set({ mode: 'off' }));
const cam = await page.evaluate(([x, y]) => { const c = S.cam; c.x = x; c.y = y; c.z = zAt(x, y); c.yaw = 0; c.zoom = 1; c.persp = 0;
  S.dirty = true; camMoved(); return { ...c }; }, [x, y]);
const runtime = await page.evaluate(() => {
  const M = window.Module || {};
  const exp = Object.keys(M).filter(k => /^_?Classic/.test(k));
  return { classic_exports: exp, train_exports: exp.filter(k => /train/i.test(k)), page_mentions_train: /train/i.test(document.documentElement.outerHTML) };
});
await page.waitForTimeout(2200);
const frames = [];
for (let k = 0; k < N; k++) {
  const t0 = Date.now();
  const file = path.join(outDir, `production_view-${k}.png`);
  await (await page.$('#gl')).screenshot({ path: file });
  frames.push({ shot: k, png: path.basename(file), sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'), at: new Date().toISOString() });
  console.log('frame', k);
  const wait = IV - (Date.now() - t0); if (wait > 0 && k < N - 1) await page.waitForTimeout(wait);
}
const out = { port, pose: { E: 379771.1, N: 3938490.6, yaw: 0, zoom: 1 }, cam, interval_ms: IV, runtime, frames, errors,
  distinct_sha: new Set(frames.map(f => f.sha256)).size };
fs.writeFileSync(path.join(outDir, 'frames.json'), JSON.stringify(out, null, 1) + '\n');
console.log(N, 'frames;', out.distinct_sha, 'distinct;', errors.length, 'page errors ->', outDir);
await browser.close();
