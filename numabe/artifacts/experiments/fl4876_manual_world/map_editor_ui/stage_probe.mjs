// node stage_probe.mjs PORT -- FL-4876 diagnostic (2026-10-18): why ev_pose_shots fails 'element is not visible' on
// #stage. Takes the single capture lock like every capture; prints #stage layout before and after entering first person.
import { chromium } from 'playwright';
import { acquireCaptureLock } from './capture_lock.mjs';
const port = process.argv[2] || '8792';
await acquireCaptureLock('stage_probe');
const headed = process.env.PROBE_HEADED === '1';
const browser = await chromium.launch({ channel: 'chrome', headless: !headed,
  args: headed ? ['--window-position=40,40', '--window-size=1620,1100'] : [] });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
const errors = []; page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
await page.goto(`http://127.0.0.1:${port}/`, { timeout: 60000 });
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
const look = () => page.evaluate(() => {
  const s = document.getElementById('stage');
  if (!s) return { stage: 'missing' };
  const cs = getComputedStyle(s), r = s.getBoundingClientRect();
  const kids = [...s.children].map(c => { const k = c.getBoundingClientRect(), st = getComputedStyle(c);
    return `${c.tagName}#${c.id} ${Math.round(k.width)}x${Math.round(k.height)} disp=${st.display} vis=${st.visibility} op=${st.opacity}`; });
  return { display: cs.display, visibility: cs.visibility, opacity: cs.opacity, w: r.width, h: r.height, x: r.x, y: r.y,
    parentDisplay: s.parentElement && getComputedStyle(s.parentElement).display, bodyClass: document.body.className, kids };
});
console.log('window', JSON.stringify(await page.evaluate(() => ({ iw: innerWidth, ih: innerHeight, vis: document.visibilityState,
  dpr: devicePixelRatio, sw: screen.width, sh: screen.height }))));
console.log('loaded', JSON.stringify(await look()));
await page.evaluate(() => EV.set({ mode: 'fp', fp: { x: 3000, y: 3000, yaw: 0, pitch: 0, eyeM: 1.6, vfov: 60, game: false } }));
await page.waitForTimeout(1500);
console.log('fp mesh', JSON.stringify(await look()));
await page.evaluate(() => EV.set({ mode: 'top', layout: 'single', ref: 'sat', op: { a3d: 1, sat: 0, map: 0 }, top: { x: 3000, y: 3000, mpp: 0.05 } }));
await page.waitForTimeout(1500);
console.log('top', JSON.stringify(await look()));
console.log('errors', JSON.stringify(errors));
await browser.close();
