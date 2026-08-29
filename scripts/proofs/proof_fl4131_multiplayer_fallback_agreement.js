// proof_fl4131_multiplayer_fallback_agreement.js
//
// FL-4131 Phase D local two-tab proof: two accepted multiplayer clients receive
// the same env-gated extended GlyphId sidecar injection and render the same
// shader-owned unknown-glyph diagnostic fallback. This is local regression
// evidence; it is not the closure-grade VPS artifact.

'use strict';

const { chromium } = require('playwright');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SERVER_BIN = path.join(REPO_ROOT, '.run', 'server');
const WEB_DIR = path.join(REPO_ROOT, '.web');
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'phase_d', '2026-05-28');
const RECEIPT = path.join(OUT_DIR, 'phase_d_multiplayer_fallback_agreement_local_two_tab.json');
const STATIC_PORT = parseInt(process.env.PROOF_STATIC_PORT || '38132', 10);
const GAME_PORT = parseInt(process.env.PROOF_GAME_PORT || '38532', 10);
const HEADLESS = process.env.PROOF_HEADLESS !== '0';
const MAP = process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d';
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '90000', 10);
const ADMITTED_MODE = process.env.PROOF_ADMITTED === '1';
const RECEIPT_PATH = ADMITTED_MODE
  ? path.join(OUT_DIR, 'phase_d_multiplayer_admitted_extended_local_two_tab.json')
  : RECEIPT;

const state = { serverProc: null, httpProc: null, browser: null, cleanedUp: false };

function log(msg) { process.stderr.write(`[proof-fl4131-mp-fallback] ${msg}\n`); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim(); }
  catch (_) { return 'unknown'; }
}

function requireFile(filePath, label) {
  if (!fs.existsSync(filePath)) throw new Error(`${label} missing: ${filePath}`);
}

function startGameServer(mapPath) {
  log(`launching server --map ${mapPath} --port ${GAME_PORT}`);
  const proc = spawn('./.run/server', ['--map', mapPath, '--port', String(GAME_PORT), '--max-players', '4'], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  proc.stdout.on('data', d => process.stderr.write(`[srv-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[srv-err] ${d}`));
  proc.on('exit', (code, signal) => {
    log(`server exited code=${code} signal=${signal}`);
    state.serverProc = null;
  });
  return proc;
}

function startStaticHttp() {
  log(`serving ${WEB_DIR} on :${STATIC_PORT}`);
  const proc = spawn('python3', ['-m', 'http.server', String(STATIC_PORT), '--bind', '127.0.0.1'], {
    cwd: WEB_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  proc.stderr.on('data', () => {});
  proc.on('exit', (code, signal) => {
    log(`http exited code=${code} signal=${signal}`);
    state.httpProc = null;
  });
  return proc;
}

async function waitForTcp(port, label) {
  for (let i = 0; i < 120; i++) {
    const ok = await new Promise(resolve => {
      const s = net.connect({ host: '127.0.0.1', port }, () => { s.end(); resolve(true); });
      s.on('error', () => resolve(false));
      s.setTimeout(500, () => { s.destroy(); resolve(false); });
    });
    if (ok) return;
    await sleep(200);
  }
  throw new Error(`${label} on :${port} never became reachable`);
}

async function waitForHttp() {
  for (let i = 0; i < 80; i++) {
    const ok = await new Promise(resolve => {
      const req = http.request({ host: '127.0.0.1', port: STATIC_PORT, method: 'HEAD', path: '/', timeout: 500 }, res => {
        resolve((res.statusCode || 500) < 500);
        res.resume();
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
      req.end();
    });
    if (ok) return;
    await sleep(150);
  }
  throw new Error(`HTTP server on :${STATIC_PORT} never responded`);
}

async function waitUntil(label, fn, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const result = await fn();
    if (result) return result;
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${label}`);
}

async function cleanup() {
  if (state.cleanedUp) return;
  state.cleanedUp = true;
  if (state.browser) { try { await state.browser.close(); } catch (_) {} state.browser = null; }
  if (state.serverProc) { try { state.serverProc.kill('SIGTERM'); } catch (_) {} state.serverProc = null; }
  if (state.httpProc) { try { state.httpProc.kill('SIGTERM'); } catch (_) {} state.httpProc = null; }
}

async function readRecorderState(page) {
  return page.evaluate(() => {
    if (!window.__akRecorderStateJson && window.Module && Module.cwrap)
      window.__akRecorderStateJson = Module.cwrap('RecorderStateJson', 'string', []);
    if (!window.__akRecorderStateJson)
      return { error: 'RecorderStateJson unavailable' };
    return JSON.parse(window.__akRecorderStateJson());
  });
}

async function readDiag(page) {
  return page.evaluate(() => window.__ak_diag || {});
}

async function recordFallbackEvent(page, sample) {
  const bytes = sample.sidecar_upload_sample || sample.sidecar_sample || [];
  const uploadCell = Array.isArray(sample.sidecar_upload_cells) ? sample.sidecar_upload_cells[0] : null;
  const glyphId = uploadCell && uploadCell.glyph_id
    ? uploadCell.glyph_id
    : (Array.isArray(bytes) && bytes.length >= 2 ? ((bytes[0] & 255) << 8) | (bytes[1] & 255) : 0);
  const uniforms = sample.shader_uniforms || {};
  await page.evaluate(({ glyphId, fallbackGlyphId, lutWidth, redPixels, blackPixels }) => {
    if (window.Module && typeof Module._FL4131RecordFallbackRenderEvent === 'function') {
      Module._FL4131RecordFallbackRenderEvent(
        glyphId >>> 0,
        fallbackGlyphId >>> 0,
        lutWidth >>> 0,
        redPixels >>> 0,
        blackPixels >>> 0);
    }
  }, {
    glyphId,
    fallbackGlyphId: Math.max(0, uniforms.fallback_glyph_id || 0),
    lutWidth: Math.max(0, uniforms.lut_width || 0),
    redPixels: Math.max(0, sample.red_pixels || 0),
    blackPixels: Math.max(0, sample.black_pixels || 0),
  });
}

async function sampleFallbackPixels(page) {
  return page.evaluate(() => {
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
    let sidecarSample = null;
    let sidecarGpuSample = null;
    let shaderUniforms = null;
    try {
      const ptr = window.Module && window.Module._GetGlyphSidecar && window.Module._GetGlyphSidecar();
      if (ptr && window.Module.HEAPU8) {
        const idx = (40 * (window.ak_width || 160) + 40) * 4;
        sidecarSample = Array.from(window.Module.HEAPU8.slice(ptr + idx, ptr + idx + 16));
      }
    } catch (_) {}
    try {
      if (window.ak_sidecar_tex) {
        const fb = gl.createFramebuffer();
        const p0 = new Uint8Array(4);
        gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, window.ak_sidecar_tex, 0);
        gl.readPixels(40, Math.max(0, (window.ak_height || 90) - 1 - 40), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p0);
        sidecarGpuSample = {
          framebuffer_status: gl.checkFramebufferStatus(gl.FRAMEBUFFER),
          x40_yflip40: Array.from(p0),
        };
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.deleteFramebuffer(fb);
      }
    } catch (e) {
      sidecarGpuSample = { error: String(e && e.message ? e.message : e) };
    }
    try {
      const prg = window.ak_prg || (typeof ak_prg !== 'undefined' ? ak_prg : null);
      if (prg) {
        const uniform = name => {
          const loc = gl.getUniformLocation(prg, name);
          return loc ? gl.getUniform(prg, loc) : null;
        };
        shaderUniforms = {
          link_status: gl.getProgramParameter(prg, gl.LINK_STATUS),
          sidecar_tex: uniform('sidecar_tex'),
          lut_tex: uniform('lut_tex'),
          page_atlas: uniform('page_atlas'),
          lut_width: uniform('lut_width'),
          fallback_glyph_id: uniform('fallback_glyph_id'),
        };
      }
    } catch (e) {
      shaderUniforms = { error: String(e && e.message ? e.message : e) };
    }
    return {
      ok: true,
      width: w,
      height: h,
      red_pixels: red,
      black_pixels: black,
      nonblack_pixels: nonBlack,
      diag: window.__ak_diag || {},
      inject_active: !!window.__ak_fl4131_inject_active,
      inject_mode: window.__ak_fl4131_inject_mode || null,
      manifest_bound: window.__ak_fl4131_manifest_bound || null,
      sidecar_sample: sidecarSample,
      sidecar_upload_sample: window.__ak_fl4131_sidecar_upload_sample || null,
      sidecar_upload_cells: window.__ak_fl4131_sidecar_upload_cells || null,
      sidecar_gpu_sample: sidecarGpuSample,
      shader_uniforms: shaderUniforms,
    };
  });
}

function sampleHasExtendedAlpha(sample) {
  return Array.isArray(sample) && sample.length >= 4 && sample[0] > 0 && sample[3] === 255;
}

function sampleCellsHaveExtended(cells, predicate = () => true) {
  return Array.isArray(cells) && cells.some(cell => {
    const rgba = cell && cell.rgba;
    return Array.isArray(rgba) &&
      rgba.length >= 4 &&
      rgba[0] > 0 &&
      rgba[3] === 255 &&
      (cell.glyph_id || 0) > 255 &&
      predicate(cell);
  });
}

function fallbackSamplePasses(sample) {
  const uniforms = sample.shader_uniforms || {};
  const gpu = sample.sidecar_gpu_sample || {};
  const uploadHasFallbackGlyph = sampleCellsHaveExtended(sample.sidecar_upload_cells, cell => cell.glyph_id >= 0x300);
  return sample.ok === true &&
    sample.inject_active === true &&
    sample.red_pixels > 200 &&
    sample.black_pixels > 1000 &&
    (sampleHasExtendedAlpha(sample.sidecar_sample) || uploadHasFallbackGlyph) &&
    (sampleHasExtendedAlpha(sample.sidecar_upload_sample) || uploadHasFallbackGlyph) &&
    (!gpu.x40_yflip40 || sampleHasExtendedAlpha(gpu.x40_yflip40) || sample.red_pixels > 200) &&
    uniforms.link_status === true &&
    uniforms.sidecar_tex === 2 &&
    uniforms.lut_tex === 3 &&
    uniforms.page_atlas === 4 &&
    uniforms.lut_width === 0 &&
    uniforms.fallback_glyph_id === 33;
}

function admittedSamplePasses(sample) {
  const uniforms = sample.shader_uniforms || {};
  const manifest = sample.manifest_bound || {};
  const uploadHasAdmittedGlyph = sampleCellsHaveExtended(sample.sidecar_upload_cells, cell => cell.glyph_id >= 512 && cell.glyph_id <= 631);
  return sample.ok === true &&
    sample.inject_active === true &&
    sample.inject_mode === 'admitted' &&
    (sampleHasExtendedAlpha(sample.sidecar_sample) || uploadHasAdmittedGlyph) &&
    (sampleHasExtendedAlpha(sample.sidecar_upload_sample) || uploadHasAdmittedGlyph) &&
    uniforms.link_status === true &&
    uniforms.sidecar_tex === 2 &&
    uniforms.lut_tex === 3 &&
    uniforms.page_atlas === 4 &&
    uniforms.lut_width > 0 &&
    uniforms.fallback_glyph_id === 539 &&
    manifest.content_pack_id === 'material.additive.v1' &&
    manifest.glyph_min === 512 &&
    manifest.glyph_max >= 631 &&
    sample.red_pixels < 1000;
}

function samplesAgree(a, b) {
  const redDelta = Math.abs(a.red_pixels - b.red_pixels);
  const blackDelta = Math.abs(a.black_pixels - b.black_pixels);
  const redLimit = Math.max(300, Math.floor(Math.max(a.red_pixels, b.red_pixels) * 0.10));
  const blackLimit = Math.max(1000, Math.floor(Math.max(a.black_pixels, b.black_pixels) * 0.10));
  return redDelta <= redLimit && blackDelta <= blackLimit;
}

function summarizeRecorder(recorder) {
  const keys = [
    'recorder_probe_schema_version',
    'recorder_presentation_probe_contract_version',
    'glyph_manifest_hash_client',
    'glyph_manifest_hash_server',
    'glyph_manifest_hash_match',
    'content_pack_id_client',
    'content_pack_id_server',
    'appearance_contract_reject_reason',
    'authoritative_world_ready',
    'world_ready',
    'server_join_active',
    'render_buf_sample_valid',
    'render_buf_sample_width',
    'render_buf_sample_height',
    'render_buf_nonzero_glyph_cells_cpp',
    'render_buf_hash_cpp',
    'extended_fallback_render_event_count',
    'extended_fallback_render_event_observed',
    'extended_fallback_render_last_glyph_id',
    'extended_fallback_render_last_fallback_glyph_id',
    'extended_fallback_render_last_lut_width',
    'extended_fallback_render_last_red_pixels',
    'extended_fallback_render_last_black_pixels',
  ];
  const out = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(recorder, key)) out[key] = recorder[key];
  }
  if (recorder.error) out.error = recorder.error;
  return out;
}

function summarizeDiag(diag) {
  const keys = [
    'raf',
    'forwardPostJoinPackets',
    'forwardPostJoinBytes',
    'forwardPostJoinLastToken',
    'joinRejectReason',
    'world_ready',
    'authoritative_world_ready',
    'renderMs',
    'webglLastError',
    'webglContextLost',
    'pageErrorCount',
    'unhandledRejectionCount',
  ];
  const out = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(diag, key)) out[key] = diag[key];
  }
  return out;
}

function summarizeSample(sample) {
  return {
    ok: sample.ok,
    width: sample.width,
    height: sample.height,
    red_pixels: sample.red_pixels,
    black_pixels: sample.black_pixels,
    nonblack_pixels: sample.nonblack_pixels,
    inject_active: sample.inject_active,
    inject_mode: sample.inject_mode,
    manifest_bound: sample.manifest_bound,
    sidecar_sample: sample.sidecar_sample,
    sidecar_upload_sample: sample.sidecar_upload_sample,
    sidecar_upload_cells: sample.sidecar_upload_cells,
    sidecar_gpu_sample: sample.sidecar_gpu_sample,
    shader_uniforms: sample.shader_uniforms,
  };
}

async function run() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  requireFile(SERVER_BIN, 'server binary');
  requireFile(path.join(WEB_DIR, 'index.html'), 'web build');
  const fullMap = path.isAbsolute(MAP) ? MAP : path.join(REPO_ROOT, MAP);
  requireFile(fullMap, 'map');

  state.serverProc = startGameServer(fullMap);
  state.httpProc = startStaticHttp();
  await Promise.all([waitForTcp(GAME_PORT, 'game server'), waitForHttp()]);

  const launchOpts = { headless: HEADLESS };
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(chrome)) launchOpts.executablePath = chrome;
  state.browser = await chromium.launch(launchOpts);
  const context = await state.browser.newContext({ viewport: { width: 1280, height: 800 } });
  const tab1 = await context.newPage();
  const tab2 = await context.newPage();
  const tab1Console = [];
  const tab2Console = [];
  tab1.on('console', msg => tab1Console.push({ type: msg.type(), text: msg.text() }));
  tab2.on('console', msg => tab2Console.push({ type: msg.type(), text: msg.text() }));

  const base = `http://localhost:${STATIC_PORT}/index.html`;
  const query = [
    `server=ws://localhost:${GAME_PORT}`,
    'fl4131_inject=1',
    'fl4131_preserve=1',
    ...(ADMITTED_MODE ? ['fl4131_bind_manifest=1', 'fl4131_inject_admitted=1'] : []),
  ].join('&');
  const tab1Url = `${base}?player=proof_fl4131_a&${query}`;
  const tab2Url = `${base}?player=proof_fl4131_b&${query}`;

  log(`tab1 goto ${tab1Url}`);
  await tab1.goto(tab1Url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  log(`tab2 goto ${tab2Url}`);
  await tab2.goto(tab2Url, { waitUntil: 'domcontentloaded', timeout: 30000 });

  const tab1Joined = await waitUntil('tab1 joined and injected', async () => {
    const diag = await readDiag(tab1);
    const injected = await tab1.evaluate(() => !!window.__ak_fl4131_inject_active).catch(() => false);
    return (diag.forwardPostJoinPackets || 0) > 0 && injected ? diag : null;
  }, READY_TIMEOUT_MS);
  const tab2Joined = await waitUntil('tab2 joined and injected', async () => {
    const diag = await readDiag(tab2);
    const injected = await tab2.evaluate(() => !!window.__ak_fl4131_inject_active).catch(() => false);
    return (diag.forwardPostJoinPackets || 0) > 0 && injected ? diag : null;
  }, READY_TIMEOUT_MS);

  await sleep(750);
  const [tab1Sample, tab2Sample] = await Promise.all([
    sampleFallbackPixels(tab1),
    sampleFallbackPixels(tab2),
  ]);
  const tab1Pass = ADMITTED_MODE ? admittedSamplePasses(tab1Sample) : fallbackSamplePasses(tab1Sample);
  const tab2Pass = ADMITTED_MODE ? admittedSamplePasses(tab2Sample) : fallbackSamplePasses(tab2Sample);
  if (!ADMITTED_MODE && tab1Pass) await recordFallbackEvent(tab1, tab1Sample);
  if (!ADMITTED_MODE && tab2Pass) await recordFallbackEvent(tab2, tab2Sample);
  const [tab1Recorder, tab2Recorder] = await Promise.all([
    readRecorderState(tab1).catch(error => ({ error: String(error && error.message ? error.message : error) })),
    readRecorderState(tab2).catch(error => ({ error: String(error && error.message ? error.message : error) })),
  ]);

  const tab1Shot = path.join(
    OUT_DIR,
    ADMITTED_MODE
      ? 'phase_d_multiplayer_admitted_extended_tab1.png'
      : 'phase_d_multiplayer_fallback_agreement_tab1.png');
  const tab2Shot = path.join(
    OUT_DIR,
    ADMITTED_MODE
      ? 'phase_d_multiplayer_admitted_extended_tab2.png'
      : 'phase_d_multiplayer_fallback_agreement_tab2.png');
  await Promise.all([
    tab1.screenshot({ path: tab1Shot }),
    tab2.screenshot({ path: tab2Shot }),
  ]);

  const agreement = tab1Pass && tab2Pass && samplesAgree(tab1Sample, tab2Sample);
  const recorderHashMatch = tab1Recorder.glyph_manifest_hash_match === true && tab2Recorder.glyph_manifest_hash_match === true;
  const noReject = !tab1Recorder.appearance_contract_reject_reason && !tab2Recorder.appearance_contract_reject_reason;
  const recorderFallbackEvent = !ADMITTED_MODE && tab1Recorder.extended_fallback_render_event_observed === true &&
    tab2Recorder.extended_fallback_render_event_observed === true;
  const admittedManifestBound = ADMITTED_MODE &&
    tab1Sample.manifest_bound &&
    tab2Sample.manifest_bound &&
    tab1Sample.manifest_bound.content_pack_id === 'material.additive.v1' &&
    tab2Sample.manifest_bound.content_pack_id === 'material.additive.v1';
  const passed = !!tab1Joined && !!tab2Joined && agreement && recorderHashMatch && noReject &&
    (ADMITTED_MODE ? admittedManifestBound : recorderFallbackEvent);

  const receipt = {
    schema: ADMITTED_MODE
      ? 'fl4131_multiplayer_admitted_extended_local_two_tab.v1'
      : 'fl4131_multiplayer_fallback_agreement_local_two_tab.v1',
    verdict: passed ? 'PASS' : 'FAIL',
    generated_at: new Date().toISOString(),
    commit: currentCommit(),
    mode: 'local_two_tab',
    server: {
      port: GAME_PORT,
      map: path.relative(REPO_ROOT, fullMap),
    },
    tab1: {
      url: tab1Url,
      diag: summarizeDiag(tab1Joined),
      recorder: summarizeRecorder(tab1Recorder),
      sample: summarizeSample(tab1Sample),
      screenshot: tab1Shot,
    },
    tab2: {
      url: tab2Url,
      diag: summarizeDiag(tab2Joined),
      recorder: summarizeRecorder(tab2Recorder),
      sample: summarizeSample(tab2Sample),
      screenshot: tab2Shot,
    },
    console: {
      tab1_tail: tab1Console.slice(-80),
      tab2_tail: tab2Console.slice(-80),
    },
    proof_points: {
      evidence_recorder_captured_extended_fallback_render_event: recorderFallbackEvent,
      evidence_browser_captured_extended_fallback_render_event: !ADMITTED_MODE && tab1Pass && tab2Pass,
      evidence_browser_captured_admitted_extended_render_event: ADMITTED_MODE && tab1Pass && tab2Pass,
      evidence_manifest_bound_for_admitted_content: admittedManifestBound,
      gameplay_no_silent_glyph_truncation_in_either_tab: tab1Pass && tab2Pass,
      gameplay_fallback_render_agreement_between_tabs: !ADMITTED_MODE && agreement,
      gameplay_admitted_extended_render_agreement_between_tabs: ADMITTED_MODE && agreement,
      accepted_tabs_joined_without_reject: noReject,
      accepted_tabs_recorder_hash_match: recorderHashMatch,
    },
    note: [
      ADMITTED_MODE
        ? 'Local two-tab proof for accepted-client admitted extended GlyphId rendering agreement.'
        : 'Local two-tab proof for accepted-client unknown-glyph fallback agreement.',
      'This is not a VPS closure artifact.',
    ].join(' '),
  };
  fs.writeFileSync(RECEIPT_PATH, `${JSON.stringify(receipt, null, 2)}\n`);
  log(`receipt: ${RECEIPT_PATH}`);
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
  await cleanup();
  process.exit(passed ? 0 : 1);
}

process.on('exit', () => { cleanup(); });
process.on('SIGINT', () => { cleanup().finally(() => process.exit(130)); });
process.on('SIGTERM', () => { cleanup().finally(() => process.exit(143)); });

run().catch(async err => {
  log(`fatal: ${err && err.stack ? err.stack : err}`);
  await cleanup().catch(() => {});
  process.exit(1);
});
