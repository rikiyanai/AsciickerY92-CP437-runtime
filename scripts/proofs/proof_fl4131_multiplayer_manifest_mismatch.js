// proof_fl4131_multiplayer_manifest_mismatch.js
//
// FL-4131 Phase 7 local two-tab proof: a normal CP437-only web client joins,
// while a second client declaring an unbound glyph manifest hash is rejected
// with glyph_manifest_mismatch. This exercises the real JoinV2 wire path and
// server reject reason without mutating gameplay state.

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
const RECEIPT = path.join(OUT_DIR, 'phase_d_multiplayer_manifest_mismatch_local_two_tab.json');
const STATIC_PORT = parseInt(process.env.PROOF_STATIC_PORT || '38131', 10);
const GAME_PORT = parseInt(process.env.PROOF_GAME_PORT || '38531', 10);
const HEADLESS = process.env.PROOF_HEADLESS !== '0';
const MAP = process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d';
const READY_TIMEOUT_MS = parseInt(process.env.PROOF_READY_TIMEOUT_MS || '90000', 10);
const BOGUS_HASH = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

const state = { serverProc: null, httpProc: null, browser: null, cleanedUp: false };

function log(msg) { process.stderr.write(`[proof-fl4131-mp-manifest] ${msg}\n`); }
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

async function openBrowser() {
  const launchOpts = { headless: HEADLESS };
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(chrome)) launchOpts.executablePath = chrome;
  state.browser = await chromium.launch(launchOpts);
  return state.browser.newContext({ viewport: { width: 1280, height: 800 } });
}

async function clickPlay(page) {
  await page.waitForSelector('#play-btn', { state: 'attached', timeout: 30000 });
  await page.waitForFunction(() => {
    const b = document.getElementById('play-btn');
    return !!b && !b.disabled;
  }, null, { timeout: 60000 });
  await page.click('#play-btn');
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

async function cleanup() {
  if (state.cleanedUp) return;
  state.cleanedUp = true;
  if (state.browser) { try { await state.browser.close(); } catch (_) {} state.browser = null; }
  if (state.serverProc) { try { state.serverProc.kill('SIGTERM'); } catch (_) {} state.serverProc = null; }
  if (state.httpProc) { try { state.httpProc.kill('SIGTERM'); } catch (_) {} state.httpProc = null; }
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

  const context = await openBrowser();
  const tab1 = await context.newPage();
  const tab1Console = [];
  const tab2Console = [];
  tab1.on('console', msg => tab1Console.push({ type: msg.type(), text: msg.text() }));

  const base = `http://localhost:${STATIC_PORT}/index.html`;
  const tab1Url = `${base}?player=proof_fl4131_ok&server=ws://localhost:${GAME_PORT}`;
  const tab2Url = `${base}?player=proof_fl4131_bad&server=ws://localhost:${GAME_PORT}&fl4131_join_glyph_manifest_hash=${BOGUS_HASH}&fl4131_join_content_pack_id=proof.bad`;

  log(`tab1 goto ${tab1Url}`);
  await tab1.goto(tab1Url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  log('tab1 clicking #play-btn');
  await clickPlay(tab1);
  const tab1Joined = await waitUntil('tab1 joined gameplay', async () => {
    const diag = await readDiag(tab1);
    return (diag.forwardPostJoinPackets || 0) > 0 ? diag : null;
  }, READY_TIMEOUT_MS);

  const tab2 = await context.newPage();
  tab2.on('console', msg => tab2Console.push({ type: msg.type(), text: msg.text() }));
  log(`tab2 goto ${tab2Url}`);
  await tab2.goto(tab2Url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  log('tab2 clicking #play-btn');
  await clickPlay(tab2);
  const tab2Rejected = await waitUntil('tab2 glyph_manifest_mismatch reject', async () => {
    const diag = await readDiag(tab2);
    if (diag.joinRejectReason === 'glyph_manifest_mismatch') return diag;
    const title = await tab2.title().catch(() => '');
    if (title.includes('JOIN REJECTED: glyph_manifest_mismatch'))
      return { ...diag, joinRejectReason: 'glyph_manifest_mismatch', title };
    return null;
  }, READY_TIMEOUT_MS);

  const tab1Recorder = await readRecorderState(tab1).catch(error => ({ error: String(error && error.message ? error.message : error) }));
  const tab2Recorder = await readRecorderState(tab2).catch(error => ({ error: String(error && error.message ? error.message : error) }));
  const tab1Shot = path.join(OUT_DIR, 'phase_d_multiplayer_manifest_match_tab1.png');
  const tab2Shot = path.join(OUT_DIR, 'phase_d_multiplayer_manifest_mismatch_tab2.png');
  await tab1.screenshot({ path: tab1Shot });
  await tab2.screenshot({ path: tab2Shot });

  const tab1RejectReason = tab1Recorder.appearance_contract_reject_reason || '';
  const passed = !!tab1Joined &&
    !!tab2Rejected &&
    tab2Rejected.joinRejectReason === 'glyph_manifest_mismatch' &&
    tab1Recorder.glyph_manifest_hash_match === true &&
    tab1RejectReason === '' &&
    tab2Recorder.appearance_contract_reject_reason === 'glyph_manifest_mismatch';

  const receipt = {
    schema: 'fl4131_multiplayer_manifest_mismatch_local_two_tab.v1',
    verdict: passed ? 'PASS' : 'FAIL',
    generated_at: new Date().toISOString(),
    commit: currentCommit(),
    mode: 'local_two_tab',
    server: {
      port: GAME_PORT,
      map: path.relative(REPO_ROOT, fullMap),
    },
    tab1_match: {
      url: tab1Url,
      diag: tab1Joined,
      recorder: tab1Recorder,
      screenshot: tab1Shot,
    },
    tab2_mismatch: {
      url: tab2Url,
      injected_glyph_manifest_hash: BOGUS_HASH,
      injected_content_pack_id: 'proof.bad',
      diag: tab2Rejected,
      recorder: tab2Recorder,
      screenshot: tab2Shot,
    },
    console: {
      tab1_tail: tab1Console.slice(-80),
      tab2_tail: tab2Console.slice(-80),
    },
    proof_points: {
      scenario_two_tab_join_handshake_reject_observed: !!tab2Rejected && tab2Rejected.joinRejectReason === 'glyph_manifest_mismatch',
      evidence_recorder_captured_reject_reason_code: tab2Recorder.appearance_contract_reject_reason === 'glyph_manifest_mismatch',
      gameplay_no_silent_glyph_truncation_in_either_tab: false,
      gameplay_fallback_render_agreement_between_tabs: false,
      accepted_tab_joined_without_reject: !!tab1Joined && tab1RejectReason === '',
      accepted_tab_recorder_hash_match: tab1Recorder.glyph_manifest_hash_match === true,
    },
    note: [
      'Local two-tab proof for JoinV2 glyph manifest identity mismatch.',
      'This is not a VPS closure artifact and does not prove fallback render agreement.',
    ].join(' '),
  };
  fs.writeFileSync(RECEIPT, `${JSON.stringify(receipt, null, 2)}\n`);
  log(`receipt: ${RECEIPT}`);
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
