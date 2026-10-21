// node net_probe.mjs -- FL-4876 diagnostic (2026-10-18): can a fresh Chrome fetch anything from the editor server?
// Takes the single capture lock. Loads /api/ev/meta (tiny JSON) in system Chrome and in Playwright's bundled Chromium.
import { chromium } from 'playwright';
import { acquireCaptureLock } from './capture_lock.mjs';
setTimeout(() => { console.log('net probe watchdog: exiting'); process.exit(4); }, 120000).unref();
await acquireCaptureLock('net_probe');
const b = await chromium.launch({ channel: 'chrome', headless: true });
for (const url of ['/api/ev/meta', '/ui/street_view_overlay.js', '/ui/editor_views.js', '/ui/facade_capture.js', '/']) {
  const t0 = Date.now();
  const pg = await (await b.newContext()).newPage();
  try {
    const r = await pg.goto('http://127.0.0.1:8792' + url, { timeout: 15000, waitUntil: 'commit' });
    const body = await r.body();
    console.log(url, 'status', r.status(), 'bytes', body.length, 'in', Date.now() - t0, 'ms');
  } catch (e) { console.log(url, 'FAILED after', Date.now() - t0, 'ms:', String(e).split('\n')[0]); }
  await pg.close();
}
await b.close();
process.exit(0);
