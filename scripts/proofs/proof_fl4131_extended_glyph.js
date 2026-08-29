// proof_fl4131_extended_glyph.js
//
// FL-4131 Phase F: Gameplay-entered web proof driver.
//
// Proves that gameplay can be entered past the menu via the web/current
// runtime and that no console-visible FL-4131 FAIL signal was emitted.
// Records the forwardPostJoinPackets > 0 marker (main_menu=0 equivalent)
// plus input chronology and gameplay screenshots per receipt §C.
//
// Self-contained: starts its own game server + HTTP server + browser.
// Does not inherit the FL-4079 wearable-probe wait from proof_driver_playwright.js.
//
// Usage:
//   node scripts/proofs/proof_fl4131_extended_glyph.js
//
// Env vars:
//   PROOF_HEADLESS=0           run chromium with a visible window (default: 1)
//   PROOF_KEEP_SERVERS=1       leave server/http processes running on exit
//   PROOF_STATIC_PORT=NNNN     HTTP port (default: 38085)
//   PROOF_GAME_PORT=NNNN       game server WebSocket port (default: 38405)
//   PROOF_MAP=path             map (default: assets/a3d/sandbox_20x20.a3d)
//   PROOF_READY_TIMEOUT_MS=N   join timeout ms (default: 90000)
//   PROOF_OUT_DIR=path         receipt output directory
//   PROOF_CHROMIUM_PATH=path   override Chromium executable

'use strict';

const { chromium } = require('playwright');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const net = require('net');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SERVER_BIN = path.join(REPO_ROOT, '.run', 'server');
const WEB_DIR = path.join(REPO_ROOT, '.web');
const DEFAULT_MAP = 'assets/a3d/sandbox_20x20.a3d';

const STATIC_PORT = parseInt(process.env.PROOF_STATIC_PORT || '38085', 10);
const GAME_PORT = parseInt(process.env.PROOF_GAME_PORT || '38405', 10);
const HEADLESS = process.env.PROOF_HEADLESS !== '0';
const KEEP_SERVERS = process.env.PROOF_KEEP_SERVERS === '1';
const JOIN_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '90000', 10);
const MOVEMENT_HOLD_MS = 2000;

const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'manual');

const _state = { serverProc: null, httpProc: null, browser: null, cleanedUp: false };

function log(msg) { process.stderr.write(`[proof-fl4131] ${msg}\n`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function waitUntil(label, fn, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await fn()) return;
    await sleep(300);
  }
  throw new Error(`timed out (${timeoutMs}ms): ${label}`);
}

function startGameServer(mapPath) {
  log(`launching server --map ${mapPath} --port ${GAME_PORT}`);
  const proc = spawn('./.run/server',
    ['--map', mapPath, '--port', String(GAME_PORT), '--max-players', '4'],
    { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', d => process.stderr.write(`[srv-out] ${d}`));
  proc.stderr.on('data', d => process.stderr.write(`[srv-err] ${d}`));
  proc.on('exit', (code, sig) => { log(`server exited code=${code} sig=${sig}`); _state.serverProc = null; });
  return proc;
}

function startStaticHttp() {
  log(`serving ${WEB_DIR} on :${STATIC_PORT}`);
  const proc = spawn('python3',
    ['-m', 'http.server', String(STATIC_PORT), '--bind', '127.0.0.1'],
    { cwd: WEB_DIR, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stderr.on('data', () => {});
  proc.on('exit', (code, sig) => { log(`http exited code=${code} sig=${sig}`); _state.httpProc = null; });
  return proc;
}

async function waitForHttp() {
  for (let i = 0; i < 50; i++) {
    const ok = await new Promise(resolve => {
      const req = http.request({ host: '127.0.0.1', port: STATIC_PORT, method: 'HEAD', path: '/', timeout: 500 },
        res => { resolve(res.statusCode < 500); res.resume(); });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
      req.end();
    });
    if (ok) return;
    await sleep(100);
  }
  throw new Error(`HTTP server on :${STATIC_PORT} never responded`);
}

async function waitForGameServer() {
  for (let i = 0; i < 100; i++) {
    const ok = await new Promise(resolve => {
      const s = net.connect({ host: '127.0.0.1', port: GAME_PORT }, () => { s.end(); resolve(true); });
      s.on('error', () => resolve(false));
      s.setTimeout(500, () => { s.destroy(); resolve(false); });
    });
    if (ok) return;
    await sleep(200);
  }
  throw new Error(`game server on :${GAME_PORT} never bound`);
}

async function openBrowser() {
  const launchOpts = { headless: HEADLESS };
  const candidates = [
    process.env.PROOF_CHROMIUM_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ].filter(Boolean);
  for (const p of candidates) {
    try { if (fs.existsSync(p)) { launchOpts.executablePath = p; break; } } catch (_) {}
  }
  _state.browser = await chromium.launch(launchOpts);
  const context = await _state.browser.newContext({ viewport: { width: 1280, height: 800 } });
  return context.newPage();
}

async function cleanup() {
  if (_state.cleanedUp) return;
  _state.cleanedUp = true;
  if (_state.browser) { try { await _state.browser.close(); } catch (_) {} _state.browser = null; }
  if (KEEP_SERVERS) { log('PROOF_KEEP_SERVERS=1: leaving processes running'); return; }
  if (_state.serverProc) { try { _state.serverProc.kill('SIGTERM'); } catch (_) {} _state.serverProc = null; }
  if (_state.httpProc) { try { _state.httpProc.kill('SIGTERM'); } catch (_) {} _state.httpProc = null; }
}

async function getDiag(page) {
  return page.evaluate(() => {
    const d = window.__ak_diag || {};
    return {
      forwardPostJoinPackets: d.forwardPostJoinPackets || 0,
      inboundGameplayForwarded: d.inboundGameplayForwarded || 0,
      gameLoadingState: d.gameLoadingState || 0,
    };
  });
}

async function captureScreenshot(page, name) {
  const p = path.join(OUT_DIR, `${name}.png`);
  try { await page.screenshot({ path: p }); log(`screenshot: ${p}`); return p; }
  catch (e) { log(`screenshot failed: ${e.message}`); return null; }
}

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT }).toString().trim(); }
  catch (_) { return 'unknown'; }
}

async function run() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const mapPath = process.env.PROOF_MAP || DEFAULT_MAP;
  const fullMap = path.isAbsolute(mapPath) ? mapPath : path.join(REPO_ROOT, mapPath);

  if (!fs.existsSync(SERVER_BIN))
    throw new Error(`server binary not found at ${SERVER_BIN} — build with: make server`);
  if (!fs.existsSync(WEB_DIR) || !fs.existsSync(path.join(WEB_DIR, 'index.html')))
    throw new Error(`web build not found at ${WEB_DIR} — run: ./build-web.sh`);
  if (!fs.existsSync(fullMap))
    throw new Error(`map not found: ${fullMap}`);

  _state.serverProc = startGameServer(fullMap);
  _state.httpProc = startStaticHttp();
  await Promise.all([waitForGameServer(), waitForHttp()]);

  const page = await openBrowser();

  const consoleErrors = [];
  const fl4131Messages = [];
  page.on('console', m => {
    const t = m.text();
    if (m.type() === 'error') consoleErrors.push(t);
    if (t.includes('FL-4131')) fl4131Messages.push(t);
  });
  page.on('pageerror', e => {
    const t = e && e.stack ? e.stack : String(e);
    // suppress repeated audio_node.onaudioprocess errors (known noise)
    if (!t.includes('audio_node.onaudioprocess'))
      consoleErrors.push(t);
  });

  const url = `http://localhost:${STATIC_PORT}/index.html?player=proof_fl4131&server=ws://localhost:${GAME_PORT}`;
  log(`goto ${url}`);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });

  // Dismiss the login overlay (same pattern as proof_driver_playwright.js).
  try {
    await page.waitForSelector('#play-btn', { state: 'attached', timeout: 30000 });
    await page.waitForFunction(() => {
      const b = document.getElementById('play-btn');
      return !!b && !b.disabled;
    }, null, { timeout: 60000 });
    log('clicking #play-btn');
    await page.click('#play-btn');
  } catch (e) {
    log(`play-btn click failed (${e.message}); pressing Enter as fallback`);
    try { await page.focus('#player-name'); } catch (_) {}
    try { await page.keyboard.press('Enter'); } catch (_) {}
  }

  const t0 = Date.now();

  // Stage 1: wait for join completion — main_menu=0 equivalent.
  log('stage1: waiting for forwardPostJoinPackets > 0 (join completed)...');
  let postJoinDiag;
  try {
    await waitUntil('forwardPostJoinPackets > 0', async () => {
      const d = await getDiag(page);
      return d.forwardPostJoinPackets > 0;
    }, JOIN_TIMEOUT_MS);
    postJoinDiag = await getDiag(page);
    log(`stage1: PASS — forwardPostJoinPackets=${postJoinDiag.forwardPostJoinPackets}`);
  } catch (e) {
    postJoinDiag = await getDiag(page).catch(() => ({}));
    log(`stage1: FAIL — ${e.message}; diag=${JSON.stringify(postJoinDiag)}`);
    await captureScreenshot(page, 'stage1-join-timeout');
    await cleanup();
    process.exit(1);
  }

  const ssJoin = await captureScreenshot(page, 'gameplay-entered');

  // Stage 2: user-reachable movement input (WASD).
  log('stage2: delivering movement input...');
  const inputChronology = [];
  try { await page.focus('canvas'); } catch (_) {}

  const moves = [
    { keys: ['w'], ms: MOVEMENT_HOLD_MS },
    { keys: ['d'], ms: 800 },
    { keys: ['a'], ms: 800 },
    { keys: ['s'], ms: 800 },
  ];
  for (const { keys, ms } of moves) {
    for (const k of keys) await page.keyboard.down(k);
    await sleep(ms);
    for (const k of [...keys].reverse()) await page.keyboard.up(k);
    await sleep(200);
    inputChronology.push({ t_ms: Date.now() - t0, keys: keys.join('+'), held_ms: ms });
  }
  log('stage2: PASS — movement input delivered');

  const ssMovement = await captureScreenshot(page, 'gameplay-after-movement');
  const finalDiag = await getDiag(page);

  // Stage 3: FL-4131 specific signal check.
  const fl4131Failures = fl4131Messages.filter(m => m.includes('[FL-4131] FAIL'));
  const passed = fl4131Failures.length === 0 && postJoinDiag.forwardPostJoinPackets > 0;

  const receipt = {
    schema: 'fl4131_gameplay_entered_web_proof_v1',
    result: passed ? 'PASS' : 'FAIL',
    timestamp_utc: new Date().toISOString(),
    commit: currentCommit(),
    gameplay_entered: postJoinDiag.forwardPostJoinPackets > 0,
    forward_post_join_packets: postJoinDiag.forwardPostJoinPackets,
    inbound_gameplay_forwarded_after_movement: finalDiag.inboundGameplayForwarded,
    input_chronology: inputChronology,
    screenshots: {
      gameplay_entered: ssJoin,
      after_movement: ssMovement,
    },
    fl4131_console_messages: fl4131Messages,
    fl4131_console_failures: fl4131Failures,
    console_error_count: consoleErrors.length,
    diag_at_join: postJoinDiag,
    diag_final: finalDiag,
    note: [
      'Gameplay-entered proof per FL-4131 receipt §C.',
      'forwardPostJoinPackets > 0 is the main_menu=0 equivalent for the web client.',
      'Extended glyph rendering (Phase 3 renderer) is not yet in the web build;',
      'this receipt proves gameplay entry and absence of FL-4131 FAIL console signals.',
    ].join(' '),
  };

  const date = new Date().toISOString().slice(0, 10);
  const receiptPath = path.join(OUT_DIR, `${date}-gameplay-entered-web-proof.json`);
  fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2));
  log(`receipt: ${receiptPath}`);

  if (passed) {
    log('RESULT: PASS');
    process.stdout.write(JSON.stringify(receipt, null, 2) + '\n');
  } else {
    log(`RESULT: FAIL — fl4131_failures=${JSON.stringify(fl4131Failures)}`);
    process.stderr.write(JSON.stringify(receipt, null, 2) + '\n');
  }

  await cleanup();
  process.exit(passed ? 0 : 1);
}

process.on('exit', () => { cleanup(); });
process.on('SIGINT', () => { cleanup().finally(() => process.exit(130)); });
process.on('SIGTERM', () => { cleanup().finally(() => process.exit(143)); });

run().catch(async e => {
  log(`fatal: ${e.stack || e.message || String(e)}`);
  await cleanup().catch(() => {});
  process.exit(1);
});
