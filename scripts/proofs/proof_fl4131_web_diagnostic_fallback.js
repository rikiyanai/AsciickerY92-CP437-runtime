// proof_fl4131_web_diagnostic_fallback.js
//
// FL-4131 Phase D: local browser proof for the web GlyphId sidecar diagnostic
// fallback path. This proves the web renderer paints the operator-required
// black "!" on red marker for unadmitted extended GlyphIds injected into the
// sidecar. It is not a multiplayer proof and does not close the all-target gate
// by itself.

'use strict';

const { chromium } = require('playwright');
const fs = require('fs');
const http = require('http');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const WEB_DIR = path.join(REPO_ROOT, '.web');
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'phase_d', '2026-05-28');
const HEADLESS = process.env.PROOF_HEADLESS !== '0';
const MODE = process.env.PROOF_FL4131_MODE || 'diagnostic';
const DEFAULT_CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CHROMIUM_PATH = process.env.PROOF_CHROMIUM_PATH
  || (fs.existsSync(DEFAULT_CHROME) ? DEFAULT_CHROME : '');

function log(msg) { process.stderr.write(`[proof-fl4131-web-d] ${msg}\n`); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.wasm': 'application/wasm',
  '.data': 'application/octet-stream',
  '.png': 'image/png',
  '.gz': 'application/gzip',
  '.json': 'application/json; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

function staticPathForUrl(reqUrl) {
  const parsed = new URL(reqUrl, 'http://127.0.0.1');
  const rel = decodeURIComponent(parsed.pathname === '/' ? '/index.html' : parsed.pathname);
  const resolved = path.resolve(WEB_DIR, `.${rel}`);
  if (resolved !== WEB_DIR && !resolved.startsWith(`${WEB_DIR}${path.sep}`)) {
    return null;
  }
  return resolved;
}

async function startStaticHttp() {
  const server = http.createServer((req, res) => {
    const filePath = staticPathForUrl(req.url || '/');
    if (!filePath) {
      res.writeHead(403);
      res.end('forbidden');
      return;
    }
    fs.readFile(filePath, (err, body) => {
      if (err) {
        res.writeHead(err.code === 'ENOENT' ? 404 : 500);
        res.end(err.code === 'ENOENT' ? 'not found' : 'read error');
        return;
      }
      res.writeHead(200, {
        'Content-Type': CONTENT_TYPES[path.extname(filePath)] || 'application/octet-stream',
        'Cache-Control': 'no-store',
      });
      res.end(body);
    });
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve();
    });
  });

  const address = server.address();
  if (!address || typeof address.port !== 'number') {
    server.close();
    throw new Error('owned HTTP server did not report a port');
  }

  return {
    server,
    port: address.port,
    close: () => new Promise(resolve => server.close(resolve)),
  };
}

function byteSampleHasExtendedAlpha(sample) {
  return Array.isArray(sample) &&
    sample.length >= 4 &&
    sample[0] > 0 &&
    sample[3] === 255;
}

function proofPasses(pixels) {
  if (!pixels || !pixels.inject_active || !pixels.offline_started || pixels.offline_error) return false;
  if (!byteSampleHasExtendedAlpha(pixels.sidecar_sample)) return false;
  if (!byteSampleHasExtendedAlpha(pixels.sidecar_upload_sample)) return false;
  if (!pixels.sidecar_gpu_sample || !byteSampleHasExtendedAlpha(pixels.sidecar_gpu_sample.x40_y40)) return false;
  const uniforms = pixels.shader_uniforms || {};
  if (uniforms.link_status !== true) return false;
  if (uniforms.sidecar_tex !== 2 || uniforms.lut_tex !== 3 || uniforms.page_atlas !== 4) return false;
  if (MODE === 'admitted') {
    const manifest = pixels.manifest_bound || {};
    if (manifest.content_pack_id !== 'material.additive.v1') return false;
    if (manifest.glyph_min !== 512 || manifest.glyph_max < 631) return false;
    if (manifest.fallback_glyph_id !== 539) return false;
    if (uniforms.lut_width <= 0 || uniforms.fallback_glyph_id !== 539) return false;
    if (pixels.red_pixels > 50000) return false;
    if (pixels.nonblack_pixels <= 1000) return false;
    return true;
  }
  if (pixels.red_pixels <= 200 || pixels.black_pixels <= 1000) return false;
  if (uniforms.lut_width !== 0 || uniforms.fallback_glyph_id !== 33) return false;
  return true;
}

async function closeStaticHttp(httpServer) {
  if (!httpServer) return;
  await httpServer.close().catch(() => {});
}

async function waitUntil(label, fn, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const value = await fn();
    if (value) return value;
    await sleep(250);
  }
  throw new Error(`timeout waiting for ${label}`);
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const httpServer = await startStaticHttp();
  let browser = null;
  try {
    browser = await chromium.launch({
      headless: HEADLESS,
      executablePath: CHROMIUM_PATH || undefined,
      args: [
        '--use-angle=swiftshader',
        '--enable-webgl',
        '--ignore-gpu-blocklist',
        '--disable-dev-shm-usage',
      ],
    });
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    const consoleLines = [];
    page.on('console', msg => consoleLines.push({ type: msg.type(), text: msg.text() }));
    page.on('pageerror', err => consoleLines.push({ type: 'pageerror', text: err.stack || String(err) }));

    // FL-4131: admitted mode binds manifest AND opts the URL-driven per-frame
    // inject into admitted-glyph writes. Without fl4131_inject_admitted=1 the
    // per-frame inject writes unadmitted glyphs, which would overwrite the
    // proof script's evaluate-injected admitted glyphs at the test cells.
    const admittedParam = MODE === 'admitted'
      ? '&fl4131_bind_manifest=1&fl4131_inject_admitted=1'
      : '';
    const url = `http://127.0.0.1:${httpServer.port}/index.html?fl4131_inject=1&fl4131_offline_proof=1${admittedParam}`;
    log(`goto ${url}`);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });

    try {
      await waitUntil('offline proof render loop + sidecar injection', async () => {
        return page.evaluate(() => {
          return !!window.__ak_fl4131_offline_proof_started &&
            !!window.__ak_fl4131_inject_active &&
            !!window.__ak_diag &&
            (window.__ak_diag.raf || 0) > 1;
        }).catch(() => false);
      }, 45000);
    } catch (err) {
      const debug = await page.evaluate(() => ({
        offline_started: !!window.__ak_fl4131_offline_proof_started,
        offline_error: window.__ak_fl4131_offline_proof_error || '',
        inject_active: !!window.__ak_fl4131_inject_active,
        has_inject_export: !!(window.Module && window.Module._GlyphSidecarTestInject),
        has_load: typeof window.Load === 'function',
        has_render: typeof window.Render === 'function',
        diag: window.__ak_diag || null,
        title: document.title,
        body_text: (document.body && document.body.innerText || '').slice(0, 1000),
      })).catch(e => ({ evaluate_error: String(e) }));
      const failReceiptPath = path.join(OUT_DIR, 'phase_d_web_diagnostic_fallback_offline_failed.json');
      const failScreenshotPath = path.join(OUT_DIR, 'phase_d_web_diagnostic_fallback_offline_failed.png');
      try { await page.screenshot({ path: failScreenshotPath, fullPage: false }); } catch (_) {}
      fs.writeFileSync(failReceiptPath, JSON.stringify({
        schema: 'fl4131_web_diagnostic_fallback_proof_v1',
        timestamp_utc: new Date().toISOString(),
        url,
        verdict: 'FAIL',
        reason: err.message,
        debug,
        console: consoleLines,
        screenshot: failScreenshotPath,
      }, null, 2) + '\n');
      throw err;
    }

    await sleep(1500);

    await page.evaluate(() => {
      const w = window.ak_width || 160;
      const h = window.ak_height || 90;
      if (window.Module && window.Module._GlyphSidecarTestInject) {
        for (let y = 0; y < h; y++) {
          for (let x = 0; x < w; x++) {
            const admittedGlyphIds = [
              512, 513, 514, 515, 516, 517, 518, 519,
              520, 521, 522, 523, 524, 525, 526, 527,
              528, 529, 530, 531, 532, 533, 534, 535,
              536, 537, 538, 539, 540, 541, 542, 543,
              544, 545, 546, 547, 548, 549, 550, 551,
              552, 553, 554, 555, 556, 557, 558, 559,
              560, 561, 562, 563, 564, 565, 566, 567,
              568, 569, 570, 571, 572, 573, 574, 575,
              576, 577, 578, 579, 580, 581, 582, 583,
              584, 585, 586, 587, 588, 589, 590, 591,
              592, 593, 594, 600, 601, 602, 603, 604,
              605, 606, 607, 608, 609, 610, 611, 612,
              613, 614, 615, 616, 617, 618, 619, 620,
              621, 622, 623, 624, 625, 626, 627, 628,
              629, 630, 631
            ];
            const glyphId = window.__ak_fl4131_admitted_mode
              ? admittedGlyphIds[(x + y) % admittedGlyphIds.length]
              : (0x300 + ((x + y) & 0xff));
            window.Module._GlyphSidecarTestInject(x, y, w, h, glyphId);
          }
        }
      }
    });
    await sleep(500);

    const pixels = await page.evaluate(() => {
      const canvas = document.getElementById('asciicker_canvas');
      const gl = window.ak_ctx || (canvas && (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')));
      if (!canvas || !gl) return { ok: false, reason: 'no_canvas_or_webgl' };
      const w = gl.drawingBufferWidth;
      const h = gl.drawingBufferHeight;
      const data = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, data);
      let red = 0;
      let black = 0;
      let nonBlack = 0;
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
        if (a > 0 && (r || g || b)) nonBlack++;
        if (r > 170 && g < 80 && b < 80) red++;
        if (r < 35 && g < 35 && b < 35 && a > 200) black++;
      }
      let sidecar_sample = null;
      let sidecar_gpu_sample = null;
      let shader_uniforms = null;
      try {
        const ptr = window.Module && window.Module._GetGlyphSidecar && window.Module._GetGlyphSidecar();
        if (ptr && window.Module.HEAPU8) {
          const idx = (40 * (window.ak_width || 160) + 40) * 4;
          sidecar_sample = Array.from(window.Module.HEAPU8.slice(ptr + idx, ptr + idx + 16));
        }
      } catch (_) {}
      try {
        if (window.ak_sidecar_tex) {
          const fb = gl.createFramebuffer();
          const p0 = new Uint8Array(4);
          const p1 = new Uint8Array(4);
          gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
          gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, window.ak_sidecar_tex, 0);
          gl.readPixels(40, 40, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p0);
          gl.readPixels(40, Math.max(0, (window.ak_height || 90) - 1 - 40), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p1);
          sidecar_gpu_sample = {
            framebuffer_status: gl.checkFramebufferStatus(gl.FRAMEBUFFER),
            x40_y40: Array.from(p0),
            x40_yflip40: Array.from(p1),
          };
          gl.bindFramebuffer(gl.FRAMEBUFFER, null);
          gl.deleteFramebuffer(fb);
        }
      } catch (e) {
        sidecar_gpu_sample = { error: String(e && e.message ? e.message : e) };
      }
      try {
        const prg = window.ak_prg || (typeof ak_prg !== 'undefined' ? ak_prg : null);
        if (prg) {
          const uniform = name => {
            const loc = gl.getUniformLocation(prg, name);
            return loc ? gl.getUniform(prg, loc) : null;
          };
          shader_uniforms = {
            is_program: gl.isProgram(prg),
            link_status: gl.getProgramParameter(prg, gl.LINK_STATUS),
            info_log: gl.getProgramInfoLog(prg),
            active_count: gl.getProgramParameter(prg, gl.ACTIVE_UNIFORMS),
            active_names: Array.from({ length: gl.getProgramParameter(prg, gl.ACTIVE_UNIFORMS) }, (_, i) => {
              const info = gl.getActiveUniform(prg, i);
              return info && info.name;
            }),
            width: uniform('width'),
            height: uniform('height'),
            tex_width: uniform('tex_width'),
            tex_height: uniform('tex_height'),
            tex: uniform('tex'),
            fnt: uniform('fnt'),
            sidecar_tex: uniform('sidecar_tex'),
            lut_tex: uniform('lut_tex'),
            page_atlas: uniform('page_atlas'),
            lut_width: uniform('lut_width'),
            fallback_glyph_id: uniform('fallback_glyph_id'),
            fallback_diag_fg: uniform('fallback_diag_fg'),
            fallback_diag_bg: uniform('fallback_diag_bg'),
          };
        }
      } catch (e) {
        shader_uniforms = { error: String(e && e.message ? e.message : e) };
      }
      return {
        ok: false,
        width: w,
        height: h,
        red_pixels: red,
        black_pixels: black,
        nonblack_pixels: nonBlack,
        diag: window.__ak_diag || {},
        inject_active: !!window.__ak_fl4131_inject_active,
        offline_started: !!window.__ak_fl4131_offline_proof_started,
        offline_error: window.__ak_fl4131_offline_proof_error || '',
        sidecar_sample,
        sidecar_gpu_sample,
        shader_uniforms,
        manifest_bound: window.__ak_fl4131_manifest_bound || null,
        sidecar_upload_sample: window.__ak_fl4131_sidecar_upload_sample || null,
      };
    });
    pixels.ok = proofPasses(pixels);

    const suffix = MODE === 'admitted' ? 'admitted_extended_offline' : 'diagnostic_fallback_offline';
    const screenshotPath = path.join(OUT_DIR, `phase_d_web_${suffix}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: false });
    const receipt = {
      schema: MODE === 'admitted' ? 'fl4131_web_admitted_extended_proof_v1' : 'fl4131_web_diagnostic_fallback_proof_v1',
      timestamp_utc: new Date().toISOString(),
      mode: MODE,
      url,
      screenshot: screenshotPath,
      pixels,
      console: consoleLines,
      verdict: pixels.ok ? 'PASS' : 'FAIL',
    };
    const receiptPath = path.join(OUT_DIR, `phase_d_web_${suffix}.json`);
    fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + '\n');
    log(`receipt ${receiptPath}`);
    log(`screenshot ${screenshotPath}`);
    if (!pixels.ok) {
      throw new Error(`${MODE} web proof failed: ${JSON.stringify(pixels)}`);
    }
  } finally {
    if (browser) await browser.close().catch(() => {});
    await closeStaticHttp(httpServer);
  }
}

main().catch(err => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
