// proof_driver_playwright.js
//
// FL-4079 GREEN-3: Playwright driver for the wearable proof harness.
//
// Spawns the native server (with --map sandbox_20x20.a3d, the authored map
// that seeds normal_armour 411 at (-15, -75, 57)), a static HTTP server for
// the emcc .web build, and a Chromium browser. Returns a Playwright Page to
// scripts/proofs/proof_wearable_equipped_shows_in_buffer.js, which polls
// window.GetActorWearableProofProbe(0) and asserts the L2 invariant.
//
// Not part of the deleted watchdog corpus. Not a recipe. Standalone harness
// per FL-4079 GREEN-3 scope.
//
// Usage:
//   PROOF_DRIVER=$PWD/scripts/proofs/proof_driver_playwright.js \
//     node scripts/proofs/proof_wearable_equipped_shows_in_buffer.js
//
//   Or directly:
//     node scripts/proofs/proof_driver_playwright.js
//
// Env vars:
//   PROOF_HEADLESS=0           run chromium with a visible window
//   PROOF_KEEP_SERVERS=1       leave server/http processes running on exit
//   PROOF_STATIC_PORT=NNNN     override the default HTTP port (38080)
//   PROOF_GAME_PORT=NNNN       override the game-server WebSocket port (38400)
//   PROOF_MAP=path             override the map (default sandbox_20x20.a3d)
//   PROOF_READY_TIMEOUT_MS=N   override the "world ready" wait (default 60000)

'use strict';

const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SERVER_BIN = path.join(REPO_ROOT, '.run', 'server');
const SERVER_SPAWN = './.run/server';
const WEB_DIR = path.join(REPO_ROOT, '.web');
const DEFAULT_MAP = 'assets/a3d/sandbox_20x20.a3d';

const STATIC_PORT = parseInt(process.env.PROOF_STATIC_PORT || '38080', 10);
const GAME_PORT = parseInt(process.env.PROOF_GAME_PORT || '38400', 10);
const HEADLESS = process.env.PROOF_HEADLESS !== '0';
const KEEP_SERVERS = process.env.PROOF_KEEP_SERVERS === '1';
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '60000', 10);
const SLOWMO_MS = parseInt(process.env.PROOF_SLOWMO_MS || '0', 10);

const _state = {
  serverProc: null,
  httpProc: null,
  browser: null,
  page: null,
  cleanedUp: false,
};

function logTagged(tag, msg) {
  process.stderr.write(`[proof-driver:${tag}] ${msg}\n`);
}

async function waitFor(predicate, timeoutMs, label) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) return;
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`waitFor timed out after ${timeoutMs}ms: ${label}`);
}

function checkPrereqs(mapPath) {
  if (!fs.existsSync(SERVER_BIN)) {
    throw new Error(`server binary not found at ${SERVER_BIN}. Build it first.`);
  }
  if (!fs.existsSync(WEB_DIR) || !fs.existsSync(path.join(WEB_DIR, 'index.html'))) {
    throw new Error(`web build not found at ${WEB_DIR}. Run build-web.sh first.`);
  }
  const fullMap = path.isAbsolute(mapPath) ? mapPath : path.join(REPO_ROOT, mapPath);
  if (!fs.existsSync(fullMap)) {
    throw new Error(`map not found at ${fullMap}`);
  }
  return fullMap;
}

function startGameServer(mapPath) {
  // FL-4137: Under Codex workspace sandboxing, Node-spawned child servers can
  // fail bind() with errno=1 even though the same .run/server command binds
  // when launched directly or under escalated/full-access permissions. Do not
  // report that pre-gameplay bind failure as a block-collision result; it is a
  // harness-permission failure until the browser actually enters gameplay.
  logTagged('server', `launching ${SERVER_SPAWN} --map ${mapPath} --port ${GAME_PORT}`);
  const proc = spawn(SERVER_SPAWN,
    ['--map', mapPath, '--port', String(GAME_PORT), '--max-players', '4'],
    { cwd: REPO_ROOT, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[srv-stdout] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[srv-stderr] ${d}`));
  proc.on('exit', (code, signal) => {
    logTagged('server', `exited code=${code} signal=${signal}`);
    _state.serverProc = null;
  });
  return proc;
}

function startStaticHttp() {
  logTagged('http', `serving ${WEB_DIR} on http://localhost:${STATIC_PORT}`);
  const proc = spawn('python3',
    ['-m', 'http.server', String(STATIC_PORT), '--bind', '127.0.0.1'],
    { cwd: WEB_DIR, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stderr.on('data', () => {}); // http.server logs hits to stderr; drop
  proc.on('exit', (code, signal) => {
    logTagged('http', `exited code=${code} signal=${signal}`);
    _state.httpProc = null;
  });
  return proc;
}

async function waitForHttpReady() {
  // Use plain node http.request rather than fetch() to avoid Node version drift.
  const http = require('http');
  for (let attempt = 0; attempt < 50; attempt++) {
    const ok = await new Promise(resolve => {
      const req = http.request({
        host: '127.0.0.1', port: STATIC_PORT, method: 'HEAD', path: '/',
        timeout: 500,
      }, res => { resolve(res.statusCode === 200 || res.statusCode === 301); res.resume(); });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
      req.end();
    });
    if (ok) return;
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error(`HTTP server on :${STATIC_PORT} never responded`);
}

async function waitForGameServerReady() {
  // Server binds to a TCP/WebSocket port; we poll with a TCP connect.
  const net = require('net');
  for (let attempt = 0; attempt < 100; attempt++) {
    const ok = await new Promise(resolve => {
      const s = net.connect({ host: '127.0.0.1', port: GAME_PORT }, () => {
        s.end(); resolve(true);
      });
      s.on('error', () => resolve(false));
      s.setTimeout(500, () => { s.destroy(); resolve(false); });
    });
    if (ok) return;
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`game server on :${GAME_PORT} never bound`);
}

async function captureDiagnostics(page, label) {
  try {
    const ssPath = `/tmp/claude-proof-${label}.png`;
    await page.screenshot({ path: ssPath, fullPage: false });
    logTagged('diag', `screenshot saved to ${ssPath}`);
  } catch (e) { logTagged('diag', `screenshot failed: ${e.message}`); }
  try {
    const probe = await page.evaluate(() => {
      const out = {
        location: location.href,
        title: document.title,
        has_probe: typeof window.GetActorWearableProofProbe === 'function',
        has_keyb: typeof window.Keyb === 'function',
        probe_sample: null,
      };
      try {
        if (typeof window.GetActorWearableProofProbe === 'function')
          out.probe_sample = window.GetActorWearableProofProbe(0);
      } catch (e) { out.probe_sample = { error: String(e.message || e) }; }
      const overlay = document.querySelector('[class*="overlay"], [id*="overlay"]');
      if (overlay) out.overlay_text = overlay.textContent.slice(0, 500);
      return out;
    });
    logTagged('diag', `page state: ${JSON.stringify(probe).slice(0, 1500)}`);
  } catch (e) { logTagged('diag', `page eval failed: ${e.message}`); }
}

async function openPage(opts = {}) {
  // Prefer system Chrome / Chromium when the bundled Playwright Chromium isn't
  // installed (common on this checkout — Playwright CLI is broken under
  // Node 25, see feedback_playwright_mobile_tests memory). Fall back to
  // bundled Chromium for users who ran `npx playwright install chromium`.
  const launchOpts = { headless: HEADLESS };
  if (SLOWMO_MS > 0)
    launchOpts.slowMo = SLOWMO_MS;
  const candidates = [
    process.env.PROOF_CHROMIUM_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ].filter(Boolean);
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        launchOpts.executablePath = p;
        logTagged('browser', `using executable ${p}`);
        break;
      }
    } catch (_) {}
  }
  _state.browser = await chromium.launch(launchOpts);
  const context = await _state.browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  let repeatedAudioErrorCount = 0;
  page.on('console', m => {
    const t = m.type();
    if (t === 'error' && m.text() === '[post-exception status] Exception thrown, see JavaScript console' &&
        repeatedAudioErrorCount > 0) {
      return;
    }
    // Log everything during the proof so failure modes are diagnosable.
    process.stderr.write(`[browser-${t}] ${m.text()}\n`);
  });
  page.on('pageerror', e => {
    const text = e && e.stack ? e.stack : (e && e.message ? e.message : String(e));
    if (text.includes('audio_node.onaudioprocess') &&
        text.includes("Cannot read properties of undefined")) {
      repeatedAudioErrorCount++;
      if (repeatedAudioErrorCount === 1)
        process.stderr.write(`[browser-pageerror] ${text}\n`);
      else if (repeatedAudioErrorCount === 2)
        process.stderr.write('[browser-pageerror] repeated audio_node.onaudioprocess errors suppressed\n');
      return;
    }
    process.stderr.write(`[browser-pageerror] ${text}\n`);
  });

  // FL-4137 / map-mismatch fix: forward the same map the server was started
  // with into the URL via ?map=. Without this the browser defaults to
  // game_map_y8.a3d while the server is on sandbox_20x20.a3d — that mismatch
  // is why prior headed runs saw building visuals client-side that had no
  // server-side collision soup, and is the FL the operator flagged when they
  // asked "check what map you thought you deployed".
  const mapParam = opts.mapPath
    ? `&map=${encodeURIComponent(opts.mapPath)}`
    : '';
  const url =
    `http://localhost:${STATIC_PORT}/index.html` +
    `?player=proof_wearable` +
    `&server=${encodeURIComponent(`localhost:${GAME_PORT}`)}` +
    mapParam;
  logTagged('browser', `goto ${url}`);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  try {
    await page.fill('#player-name', 'proof_wearable');
  } catch (_) {}
  try {
    await page.fill('#server-url', `localhost:${GAME_PORT}`);
  } catch (_) {}
  await dismissLoginOverlay(page);
  return page;
}

// The web build (game_web.html) renders a login overlay with a PLAY button
// that calls StartGame() on click; URL params ?player= and ?server= populate
// the inputs but do NOT auto-call StartGame. Click PLAY to start the join.
async function dismissLoginOverlay(page) {
  try {
    // Wait for the button to exist (it lives inside game_web.html static markup)
    await page.waitForSelector('#play-btn', { state: 'attached', timeout: 30000 });
    // Wait for it to be clickable (not just attached) — the page may briefly
    // disable it during WASM/IDBFS bootstrap. Poll until it is.
    await page.waitForFunction(() => {
      const btn = document.getElementById('play-btn');
      return !!btn && !btn.disabled;
    }, null, { timeout: 60000 });
    logTagged('browser', 'clicking #play-btn to trigger StartGame()');
    await page.click('#play-btn');
  } catch (e) {
    // Fall back to Enter-key on the focused input — same trigger path
    // (game_web.html:5653 binds Enter -> StartGame on player-name input).
    logTagged('browser', `play-btn click failed (${e.message}); pressing Enter as fallback`);
    try { await page.focus('#player-name'); } catch (_) {}
    try { await page.keyboard.press('Enter'); } catch (_) {}
  }
}

async function waitForProbeReady(page) {
  // Two-stage wait: (1) cwrap available, (2) probe returns a sane object.
  try {
    await page.waitForFunction(
      () => typeof window.GetActorWearableProofProbe === 'function',
      null,
      { timeout: READY_TIMEOUT_MS });
  } catch (e) {
    await captureDiagnostics(page, 'stage1-probe-missing');
    throw new Error('stage1: window.GetActorWearableProofProbe never appeared — ' + e.message);
  }
  logTagged('ready', 'stage1: probe export bound');
  try {
    await page.waitForFunction(
      () => {
        try {
          const p = window.GetActorWearableProofProbe(0);
          return p && typeof p === 'object' && !p.error
            && p.server_truth
            && p.server_truth.presentation_kind_id === 600;
        } catch (e) { return false; }
      },
      null,
      { timeout: READY_TIMEOUT_MS });
  } catch (e) {
    await captureDiagnostics(page, 'stage2-not-idlewalk');
    throw new Error('stage2: probe never reported presentation_kind_id=600 (IDLE_WALK) — ' + e.message);
  }
}

async function waitForRecorderStateReady(page) {
  try {
    await page.waitForFunction(
      () => {
        try {
          if (!window.__akRecorderStateJson && window.Module && Module.cwrap)
            window.__akRecorderStateJson = Module.cwrap('RecorderStateJson', 'string', []);
          if (!window.__akRecorderStateJson)
            return false;
          const state = JSON.parse(window.__akRecorderStateJson());
          return state && !state.error && Array.isArray(state.auth_item_sample) &&
            state.auth_item_sample.length > 0;
        } catch (e) {
          return false;
        }
      },
      null,
      { timeout: READY_TIMEOUT_MS });
  } catch (e) {
    await captureDiagnostics(page, 'recorder-state-not-ready');
    throw new Error('recorder: RecorderStateJson never became usable — ' + e.message);
  }
  logTagged('ready', 'recorder state responsive');
}

// Export: openProofPage(opts) — used by the harness via require(PROOF_DRIVER).
async function openProofPage(opts = {}) {
  const mapPath = process.env.PROOF_MAP || opts.mapPath || DEFAULT_MAP;
  const fullMap = checkPrereqs(mapPath);

  _state.serverProc = startGameServer(fullMap);
  _state.httpProc = startStaticHttp();
  await Promise.all([waitForGameServerReady(), waitForHttpReady()]);

  _state.page = await openPage({ mapPath });
  if (opts.waitForRecorderState)
    await waitForRecorderStateReady(_state.page);
  else
    await waitForProbeReady(_state.page);

  logTagged('ready', opts.waitForRecorderState
    ? 'recorder responsive'
    : 'probe responsive, server presentation IDLE_WALK reached');
  return _state.page;
}

// Send a Keyb event via the engine's cwrap. type/val per engine/game_input.cpp.
// Exposed for the harness to walk the actor toward the armor pickup tile.
async function keyb(page, type, val) {
  await page.evaluate(([t, v]) => {
    if (typeof window.Keyb === 'function') window.Keyb(t | 0, v | 0);
  }, [type, val]);
}

// Drive the actor with DOM keyboard events for `ms` milliseconds. WASD maps to
// the game's input force vector via the game_web.html keydown/keyup handlers.
// `dir` is one of "n"/"s"/"e"/"w" (or pairs like "sw"). After the duration,
// all keys are released. Used to walk the local actor into pickup range of the
// armor ItemInst seeded by sandbox_20x20.a3d.
async function walkFor(page, dir, ms) {
  const KEY_BY_AXIS = { n: 'w', s: 's', e: 'd', w: 'a' };
  const keys = String(dir || '').toLowerCase().split('').map(c => KEY_BY_AXIS[c]).filter(Boolean);
  if (keys.length === 0) return;
  logTagged('walk', `dir=${dir} keys=[${keys.join(',')}] ${ms}ms`);
  // Focus the canvas first; game_web.html binds keydown listeners on its canvas.
  try { await page.focus('canvas'); } catch (_) {}
  for (const k of keys) await page.keyboard.down(k);
  await new Promise(r => setTimeout(r, ms));
  for (const k of keys) await page.keyboard.up(k);
}

async function cleanup() {
  if (_state.cleanedUp) return;
  _state.cleanedUp = true;
  if (_state.browser) {
    try { await _state.browser.close(); } catch (e) {}
    _state.browser = null;
  }
  if (KEEP_SERVERS) {
    logTagged('cleanup', 'PROOF_KEEP_SERVERS=1; leaving game server + http server running');
    return;
  }
  if (_state.serverProc) {
    try { _state.serverProc.kill('SIGTERM'); } catch (e) {}
    _state.serverProc = null;
  }
  if (_state.httpProc) {
    try { _state.httpProc.kill('SIGTERM'); } catch (e) {}
    _state.httpProc = null;
  }
}

process.on('exit', () => { cleanup(); });
process.on('SIGINT', () => { cleanup().finally(() => process.exit(130)); });
process.on('SIGTERM', () => { cleanup().finally(() => process.exit(143)); });

module.exports = {
  openProofPage,
  keyb,
  walkFor,
  cleanup,
};

// CLI: when run directly, open the page and run the wearable proof end-to-end.
if (require.main === module) {
  (async () => {
    try {
      const page = await openProofPage();
      // Walk the actor toward the armor ItemInst at world (-15, -75) seeded in
      // sandbox_20x20.a3d. Spawn defaults to ~origin; armor sits south-southwest.
      // Drives the production UpdateMobileAutoPickup -> ITEM_ACTION_REQ_PICKUP ->
      // SvrPickupEquippableItem chain. No verifier bypass; no synthetic Packet.
      const walkMs = parseInt(process.env.PROOF_WALK_MS || '12000', 10);
      const walkDir = process.env.PROOF_PREWALK_DIR || process.env.PROOF_WALK_DIR || 'sw'; // south + west
      await walkFor(page, walkDir, walkMs);
      // Defer to the proof harness for the assertion logic.
      const proof = require(path.join(__dirname, 'proof_wearable_equipped_shows_in_buffer.js'));
      const defId = parseInt(process.env.PROOF_DEF_ID || String(proof.ARMOR_DEF_ID), 10);
      const slotKind = parseInt(process.env.PROOF_SLOT_KIND || String(proof.ARMOR_SLOT_KIND), 10);
      const visualStyle = parseInt(process.env.PROOF_VISUAL_STYLE || String(proof.ARMOR_VISUAL_STYLE), 10);
      const out = await proof.proofWearableEquippedShowsInBuffer(page, {
        defId,
        slotKind,
        visualStyle,
        expectedCellsField: process.env.PROOF_EXPECTED_CELLS_FIELD || 'expected_armor_cells',
        label: process.env.PROOF_WEARABLE_LABEL || 'armor',
        requireMount: process.env.PROOF_REQUIRE_MOUNT === '1',
      });
      console.log(JSON.stringify(out, null, 2));
      await cleanup();
      process.exit(0);
    } catch (e) {
      if (e && e.message) logTagged('error', e.message);
      else logTagged('error', String(e));
      if (e && e.stage) logTagged('error', `stage=${e.stage} detail=${e.detail || ''}`);
      await cleanup();
      process.exit(1);
    }
  })();
}
