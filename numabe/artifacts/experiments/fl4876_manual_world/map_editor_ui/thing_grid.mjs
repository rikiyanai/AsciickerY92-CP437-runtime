// node thing_grid.mjs PORT X Y YAW ZOOM OUT.json [STEP_PX] [X0,Y0,X1,Y1] -- FL-4876 listing aid (decides nothing).
// Sets the editor camera (/ = a3d.html, headless system Chrome, throwaway profile, never presses Accept) and asks the
// editor's own hit test thingAt([x, y]) -- the function a click uses -- what is drawn at each grid point of the canvas
// (optionally only inside the pixel box X0,Y0,X1,Y1). Writes the per-point hits and a count per object to OUT.json,
// so "what is that card over the track" is answered by the same lookup the operator's click would do.
import { chromium } from 'playwright';
import fs from 'node:fs';
const [port, X, Y, YAW, ZOOM, out, stepArg, boxArg] = process.argv.slice(2);
const step = Number(stepArg || 16), box = boxArg ? boxArg.split(',').map(Number) : null;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
await page.goto(`http://127.0.0.1:${port}/`, { timeout: 60000 });
await page.waitForFunction(() => typeof S !== 'undefined' && S.ready && !document.getElementById('load'), null, { timeout: 240000 });
await page.waitForTimeout(1500);
const res = await page.evaluate(async ([x, y, yaw, zoom, step, box]) => {
  const c = S.cam; c.x = x; c.y = y; c.z = zAt(x, y); c.yaw = yaw; c.zoom = zoom; S.dirty = true; camMoved();
  await new Promise(r => setTimeout(r, 1800));
  const el = document.getElementById('gl'), w = el.clientWidth, h = el.clientHeight, hits = [];
  const [x0, y0, x1, y1] = box || [0, 0, w, h];
  for (let py = y0; py < y1; py += step) for (let px = x0; px < x1; px += step) {
    const t = await thingAt([px, py]);
    if (t) hits.push({ px, py, kind: t.kind, id: t.id, building: t.building || null, label: t.label || t.name || t.mesh || null });
  }
  return { w, h, cam: { ...c }, hits };
}, [Number(X), Number(Y), Number(YAW), Number(ZOOM), step, box]);
const count = {};
for (const h of res.hits) { const k = `${h.kind}:${h.id}`; count[k] = count[k] || { n: 0, label: h.label, sample: [h.px, h.py] }; count[k].n++; }
res.objects = Object.entries(count).sort((a, b) => b[1].n - a[1].n).map(([k, v]) => ({ object: k, ...v }));
fs.writeFileSync(out, JSON.stringify(res, null, 1) + '\n');
console.log(res.hits.length, 'hits;', res.objects.length, 'objects ->', out);
for (const o of res.objects.slice(0, 40)) console.log(String(o.n).padStart(4), o.object, o.label || '', o.sample.join(','));
await browser.close();
