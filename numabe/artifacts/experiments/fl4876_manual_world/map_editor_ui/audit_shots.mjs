// node audit_shots.mjs PORT POSES.json OUT_DIR -- FL-4876 audit captures of the operator's editor view (/ = a3d.html).
// Drives the real page in headless system Chrome (Playwright, throwaway profile; the operator's profile is untouched),
// never presses Accept (no owner file is written). For each pose {name, x, y, yaw[], zoom?} or {name, goto, yaw[]}
// it sets the editor camera exactly as the operator's controls do (S.cam + camMoved) and saves the #gl canvas as
// OUT_DIR/<name>_y<yaw>.png, plus OUT_DIR/shots.json listing camera, location line and the PNG SHA-256 per shot.
import { chromium } from 'playwright';
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
import { acquireCaptureLock } from './capture_lock.mjs';
const [port, posesPath, outDir] = process.argv.slice(2);
await acquireCaptureLock('audit_shots ' + posesPath);  // one headless Chrome / game capture at a time
const base = `http://127.0.0.1:${port}`, poses = JSON.parse(fs.readFileSync(posesPath, 'utf8'));
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
const errors = []; page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
await page.goto(base + '/', { timeout: 60000 });
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
await page.waitForTimeout(2000);
// keep the DOM panels that float over the canvas (location bar, inspector, status, help) out of the captures
await page.addStyleTag({ content: '#insp,#where,#status,#help,#layers,#palette,#chips,#ov,#dlg{display:none!important}' });
await page.evaluate(() => { if (typeof miniOn !== 'undefined' && miniOn) toggleMini(); });   // minimap inset off
const out = { base, started: new Date().toISOString(), shots: [], errors };
for (const p of poses) {
  if (p.goto) { await page.evaluate(q => goto(q), p.goto); await page.waitForTimeout(2500); }
  for (const yaw of p.yaw || [0]) {
    const cam = await page.evaluate(([p, yaw]) => {
      const c = S.cam; if (p.x != null) { c.x = p.x; c.y = p.y; c.z = zAt(p.x, p.y); }
      c.yaw = yaw; c.zoom = p.zoom || 1; c.persp = p.persp || 0; S.dirty = true; camMoved(); return { ...c } }, [p, yaw]);
    await page.waitForTimeout(1800);
    const file = path.join(outDir, `${p.name}_y${yaw}.png`);
    await (await page.$('#gl')).screenshot({ path: file });
    const where = await page.$eval('#where', e => e.innerText.replace(/\s+/g, ' ').slice(0, 220));
    out.shots.push({ name: p.name, yaw, cam, where, png: path.basename(file),
      sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
    console.log(p.name, yaw, where.slice(0, 120));
  }
}
out.finished = new Date().toISOString();
fs.writeFileSync(path.join(outDir, 'shots.json'), JSON.stringify(out, null, 1) + '\n');
console.log(out.shots.length, 'shots;', errors.length, 'page errors; ->', outDir);
await browser.close();
