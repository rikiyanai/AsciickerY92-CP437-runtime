# Ad hoc script: Headed read-only probe: open webbuild fl4131_offline_proof URL directly, poll top-level ak_canvas readPixels maxRGB (FL-4260 path-A pre-edit proof)
# Created: 2026-06-18
# Canonical gap: <describe what tool should own this>

// FL-4260 path-A pre-edit probe (read-only, no clone edit, no relay).
// Opens the webbuild's OWN offline-renderer URL directly (top-level window,
// ak_canvas is a top-level var) and polls readPixels maxRGB ignoring alpha.
// Run from the alexharri-website dir so `playwright` resolves.
import { chromium } from "playwright";
import fs from "fs";
import path from "path";

const URL = "http://localhost:8080/asciicker-web/index.html?fl4131_offline_proof=1&fl4131_preserve=1";
const OUT = process.env.AK_OUT ||
  "/Users/r/Downloads/asciicker-Y9-2/.external/alexharri-website/output/playwright/fl4260-offline-probe";
fs.mkdirSync(OUT, { recursive: true });

const readMaxRGB = () => {
  const c = window.ak_canvas;
  if (!c) return { ok: false, reason: "no-canvas" };
  const gl = c.getContext("webgl2") || c.getContext("webgl");
  if (!gl) return { ok: false, reason: "no-gl" };
  const w = Math.min(c.width, 64), h = Math.min(c.height, 64);
  if (!w || !h) return { ok: false, reason: "zero-dim" };
  const buf = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  let maxRGB = 0;
  for (let i = 0; i < buf.length; i++) { if (i % 4 === 3) continue; if (buf[i] > maxRGB) maxRGB = buf[i]; }
  return { ok: true, w: c.width, h: c.height, maxRGB };
};

(async () => {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  page.on("console", (m) => { const t = m.text(); if (/error|fail|webgl|asciicker|ak_canvas|FL-4131|offline/i.test(t)) console.log("  [page]", t); });
  console.log("→ loading", URL);
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 90_000 });
  let best = { maxRGB: 0 };
  for (let s = 0; s <= 120; s += 5) {
    const r = await page.evaluate(readMaxRGB);
    if (r.ok && r.maxRGB > best.maxRGB) best = r;
    console.log(`  …t+${s}s`, JSON.stringify(r));
    if (r.ok && r.maxRGB > 0 && s >= 10) break;
    await page.waitForTimeout(5000);
  }
  await page.screenshot({ path: path.join(OUT, "offline_probe.png") });
  console.log("BEST:", JSON.stringify(best));
  console.log("VERDICT:", best.maxRGB > 0 ? "PASS — webbuild renders non-black offline" : "FAIL — offline canvas still black");
  console.log("artifacts:", OUT);
  await browser.close();
})().catch((e) => { console.error("PROBE_ERROR", e && e.message ? e.message : e); process.exit(1); });
