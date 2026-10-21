// node sv_probe.mjs PORT PANO BEARING -- FL-4876 diagnostic (2026-10-18): sv captures hold the lock with no image.
// Takes the single capture lock; logs every step of one sv pose with a timeout per step so the hanging step shows.
import { chromium } from 'playwright';
import { acquireCaptureLock } from './capture_lock.mjs';
const [port = '8792', pano = 'mapillary_950915669004676', bearing = '129'] = process.argv.slice(2);
const t0 = Date.now(); const log = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1) + 's', ...a);
setTimeout(() => { log('probe watchdog: exiting'); process.exit(4); }, 300000).unref();
await acquireCaptureLock('sv_probe'); log('lock');
const browser = await chromium.launch({ channel: 'chrome', headless: true }); log('launched');
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
page.on('pageerror', e => log('PAGEERROR', String(e).slice(0, 300)));
page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') log('console', m.type(), m.text().slice(0, 200)); });
page.on('requestfailed', r => log('REQFAILED', r.url().slice(0, 150), r.failure()?.errorText));
const pending = new Map();
page.on('request', r => pending.set(r, Date.now()));
page.on('requestfinished', r => pending.delete(r));
page.on('requestfailed', r => pending.delete(r));
try { await page.goto(`http://127.0.0.1:${port}/`, { timeout: 60000 }); log('goto'); }
catch (e) {
  log('goto failed:', String(e).split('\n')[0]);
  for (const [r, t] of pending) log('PENDING', r.method(), r.url().slice(0, 200), 'for', ((Date.now() - t) / 1000).toFixed(0) + 's');
  await browser.close(); process.exit(2);
}
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 }); log('S.ready');
const step = async (name, fn, arg) => {
  const r = await Promise.race([page.evaluate(fn, arg).then(v => ['ok', v]), new Promise(ok => setTimeout(() => ok(['TIMEOUT 30s']), 30000))]);
  log(name, JSON.stringify(r).slice(0, 400)); return r;
};
await step('typeof SV / EV', () => ({ SV: typeof SV, EV: typeof EV, setGame: typeof (EV && EV.setGame) }));
await step('EV.setGame(false)', g => EV.setGame(g, 'player'), false);
await step('SV.set side', o => SV.set(o), { pano, layout: 'side' });
await step('SV.set bearing', o => SV.set(o), { pano, layout: 'side', bearing: Number(bearing), pitch: 0, fov_default: 75 });
await page.waitForTimeout(900);
await step('SV.state', () => { const s = SV.state(); return { on: s.on, ready: s.ready, rects: s.rects, err: s.err }; });
const stage = await page.$('#stage'); log('stage bbox', JSON.stringify(await stage.boundingBox()));
await page.screenshot({ path: '/private/tmp/claude-501/-Users-r-Projects-asciicker-Y9-2/0e4cdb7d-789e-4f42-9ca8-ce5af0878c03/scratchpad/sv_probe.png' }); log('screenshot done');
await browser.close(); log('closed');
