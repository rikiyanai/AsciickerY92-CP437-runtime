// FL-4165 / FL-4164 live state probe.
// Launches a Playwright-controlled Chromium pointed at the local server,
// waits for PLAY button binding, triggers it, then evaluates RecorderStateJson
// directly so the operator does not have to bounce diagnostics off Claude.
//
// Usage:
//   PROBE_URL='http://localhost:38080/index.html?player=human&server=localhost:38400&map=assets/a3d/game_map_y8.a3d' \
//     node scripts/adhoc/2026-05-30-Live-state-probe-fl4165.js
//   PROBE_HEADLESS=0 PROBE_URL=... node ...   # see the window
//   PROBE_SCREENSHOT=/tmp/probe.png node ...  # capture a frame
//
// The script prints a JSON report to stdout summarising:
//   - browser console errors
//   - login overlay state at time-of-probe
//   - websocket / page / build hash
//   - RecorderStateJson sample (player pos, auth_item_sample subset)
//   - whether StartGame/Connect/Play resolved
// and exits non-zero if it could not even reach a valid recorder state.

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const URL_ = process.env.PROBE_URL
  || 'http://localhost:38080/index.html?player=human&server=localhost:38400&map=assets/a3d/game_map_y8.a3d';
const HEADLESS = process.env.PROBE_HEADLESS !== '0';
const SCREENSHOT = process.env.PROBE_SCREENSHOT || '';
const PROBE_WAIT_MS = parseInt(process.env.PROBE_WAIT_MS || '15000', 10);
const POST_PLAY_WAIT_MS = parseInt(process.env.PROBE_POST_PLAY_MS || '12000', 10);

function log(tag, msg) {
  process.stderr.write(`[probe-${tag}] ${msg}\n`);
}

function systemChromium() {
  const c = [
    process.env.PROOF_CHROMIUM_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ].filter(Boolean);
  for (const p of c) {
    try { if (fs.existsSync(p)) return p; } catch (_) {}
  }
  return null;
}

(async () => {
  const launchOpts = { headless: HEADLESS };
  const chromePath = systemChromium();
  if (chromePath) launchOpts.executablePath = chromePath;
  log('launch', `headless=${HEADLESS} exec=${chromePath || '(bundled)'}`);
  const browser = await chromium.launch(launchOpts);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const consoleMessages = [];
  const pageErrors = [];
  page.on('console', m => {
    consoleMessages.push({ type: m.type(), text: m.text().slice(0, 500) });
  });
  page.on('pageerror', e => {
    pageErrors.push((e && e.stack) ? e.stack.split('\n').slice(0, 2).join(' | ') : String(e));
  });

  log('nav', URL_);
  let nav_ok = true;
  try {
    await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 20000 });
  } catch (e) {
    nav_ok = false;
    log('nav-error', String(e.message || e));
  }

  log('wait', `${PROBE_WAIT_MS}ms for StartGame / login overlay`);
  await page.waitForTimeout(PROBE_WAIT_MS);

  // Pre-PLAY snapshot: is the login overlay there, is StartGame bound?
  const prePlay = await page.evaluate(() => {
    const out = {
      build: window.__akBuildVersion || null,
      startGameType: (typeof window.StartGame),
      hasModule: !!(window.Module && window.Module.cwrap),
      overlayVisible: false,
      overlayText: '',
      playBtnText: '',
      winSize: { w: window.innerWidth, h: window.innerHeight },
      docReady: document.readyState,
    };
    const overlay = document.getElementById('login-overlay')
      || document.querySelector('[id*="login"], [id*="overlay"]');
    if (overlay) {
      const cs = window.getComputedStyle(overlay);
      out.overlayVisible = cs.display !== 'none' && cs.visibility !== 'hidden';
      out.overlayText = (overlay.textContent || '').slice(0, 300).replace(/\s+/g, ' ');
    }
    const playBtn = document.getElementById('play-button')
      || document.querySelector('button[id*="play"], button[id*="Play"], .play-button');
    if (playBtn) out.playBtnText = (playBtn.textContent || '').slice(0, 100);
    return out;
  });
  log('pre-play', JSON.stringify(prePlay));

  // Try to click PLAY if button exists. Then wait POST_PLAY_WAIT_MS.
  let played = { tried: false, method: '', error: '' };
  try {
    const clicked = await page.evaluate(() => {
      const candidates = [
        document.getElementById('play-button'),
        document.querySelector('button[id*="play"], button[id*="Play"]'),
        document.querySelector('.play-button'),
        document.querySelector('button'),
      ].filter(Boolean);
      for (const b of candidates) {
        try { b.click(); return { ok: true, id: b.id || b.className, text: (b.textContent || '').slice(0, 40) }; }
        catch (e) { return { ok: false, error: String(e) }; }
      }
      if (typeof window.StartGame === 'function') {
        try { window.StartGame(); return { ok: true, id: 'StartGame()', text: '' }; }
        catch (e) { return { ok: false, error: String(e) }; }
      }
      return { ok: false, error: 'no PLAY surface found' };
    });
    played.tried = true;
    played.method = JSON.stringify(clicked);
    log('play', JSON.stringify(clicked));
  } catch (e) {
    played.error = String(e.message || e);
    log('play-error', played.error);
  }

  log('wait-post', `${POST_PLAY_WAIT_MS}ms for join/game to settle`);
  await page.waitForTimeout(POST_PLAY_WAIT_MS);

  // Probe live state.
  const probe = await page.evaluate(() => {
    const out = { build: window.__akBuildVersion || null };
    out.akJoined = window.ak_joined || null;
    out.akMpStage = window.ak_mp_stage || null;
    out.akLastWsUrl = window.ak_last_ws_url || null;
    out.akWsState = (typeof window.connection !== 'undefined' && window.connection)
      ? window.connection.readyState : 'no-conn';
    out.hasRecorderCwrap = false;
    try {
      if (window.Module && window.Module.cwrap && !window.__akRecorderStateJson) {
        window.__akRecorderStateJson = window.Module.cwrap('RecorderStateJson', 'string', []);
      }
      out.hasRecorderCwrap = !!window.__akRecorderStateJson;
    } catch (e) { out.recorderCwrapError = String(e); }
    if (out.hasRecorderCwrap) {
      try {
        const raw = window.__akRecorderStateJson();
        out.recorderLen = raw ? raw.length : 0;
        if (raw) {
          const rec = JSON.parse(raw);
          out.recorder = {
            self_x: rec.self_x, self_y: rec.self_y, self_z: rec.self_z,
            self_fly: rec.self_fly, local_grounded: rec.local_grounded,
            in_water: rec.in_water,
            render_buf_width: rec.render_buf_width,
            render_buf_height: rec.render_buf_height,
            auth_item_sample_count: (rec.auth_item_sample || []).length,
            auth_item_sample_first: (rec.auth_item_sample || []).slice(0, 3).map(s => ({
              id: s.id, owner_id: s.owner_id, x: s.x, y: s.y, z: s.z,
              half_extent: s.half_extent, collision_top_z: s.collision_top_z,
              visual_top_z: s.visual_top_z, support_top_z: s.support_top_z,
              corners_valid: s.corners_valid,
            })),
          };
        }
      } catch (e) { out.recorderError = String(e); }
    }
    // Snapshot the diagnostic overlay if it's there.
    const diag = document.getElementById('multiplayer-diag-overlay') ||
                 document.querySelector('[id*="diag"]');
    if (diag) out.diagOverlayText = (diag.textContent || '').slice(0, 800).replace(/\s+/g, ' ');
    const overlay = document.getElementById('login-overlay')
      || document.querySelector('[id*="login"], [id*="overlay"]');
    if (overlay) {
      const cs = window.getComputedStyle(overlay);
      out.overlayVisible = cs.display !== 'none' && cs.visibility !== 'hidden';
      out.overlayText = (overlay.textContent || '').slice(0, 300).replace(/\s+/g, ' ');
    }
    return out;
  }).catch(e => ({ probeEvalError: String(e.message || e) }));

  if (SCREENSHOT) {
    try {
      await page.screenshot({ path: SCREENSHOT, fullPage: false });
      log('screenshot', SCREENSHOT);
    } catch (e) { log('screenshot-error', String(e.message || e)); }
  }

  const report = {
    nav_ok,
    url: URL_,
    pre_play: prePlay,
    played,
    probe,
    page_errors: pageErrors.slice(0, 12),
    console_tail: consoleMessages.slice(-30),
  };
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');

  if (!HEADLESS && process.env.PROBE_KEEP_OPEN === '1') {
    log('hold', 'PROBE_KEEP_OPEN=1 — window left open. Ctrl+C to end.');
    await new Promise(() => {});
  }
  await browser.close();
  // Non-zero exit if no recorder reached.
  if (!probe || !probe.hasRecorderCwrap || !probe.recorder) process.exit(2);
})();
