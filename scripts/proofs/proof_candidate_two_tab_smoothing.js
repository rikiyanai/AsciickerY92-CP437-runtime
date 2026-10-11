#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const TARGET = process.env.ASCIICKER_PROOF_URL || 'https://candidate-asciicker.rikiworld.com/';
const OUT_DIR = path.resolve(process.cwd(), 'output/playwright');
const RUN_ID = `candidate-two-tab-smoothing-${new Date().toISOString().replace(/[:.]/g, '-')}`;
const OUT_PATH = path.join(OUT_DIR, `${RUN_ID}.json`);
const DRIVE_MS = Number(process.env.ASCIICKER_PROOF_DRIVE_MS || 30000);
const HOLD_OPEN_MS = Number(process.env.ASCIICKER_PROOF_HOLD_OPEN_MS || 0);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function num(v, fallback = 0) {
  return Number.isFinite(Number(v)) ? Number(v) : fallback;
}

function dist3(a, b) {
  const dx = num(a?.[0]) - num(b?.[0]);
  const dy = num(a?.[1]) - num(b?.[1]);
  const dz = num(a?.[2]) - num(b?.[2]);
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

async function disableCache(context, page) {
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
}

async function sample(page, tab, phase) {
  const state = await page.evaluate(() => {
    try {
      const mod = window.Module;
      if (!mod || typeof mod.cwrap !== 'function') {
        return { error: 'module_cwrap_missing', title: document.title, href: location.href };
      }
      window.__asciickerRecorderState =
        window.__asciickerRecorderState ||
        mod.cwrap('ClientObservationJsonV1', 'string', []);
      const raw = window.__asciickerRecorderState();
      return JSON.parse(raw);
    } catch (e) {
      return { error: String(e && e.message || e), title: document.title, href: location.href };
    }
  });
  state.tab = tab;
  state.phase = phase;
  state.sample_wall_ms = Date.now();
  return state;
}

function hasJoinedMultiplayer(s) {
  if (s.error) return false;
  const tick = num(s.server_tick, 0);
  const joined = Boolean(s.server_ptr_ok) ||
    Boolean(s.server_join_active) ||
    num(s.server_local_id, -99) >= 0 ||
    num(s.snapshot_packets_cpp, 0) > 0 ||
    num(s.snapshot_local_applied_cpp, 0) > 0;
  return tick > 0 && joined;
}

async function clickPlay(page, tab, playerName, samples) {
  // FL-4132: wait for ShowLoginOverlay() to apply the URL player name before
  // clicking PLAY; otherwise both tabs can join as default "player" and one
  // disconnects before the smoothing recipe starts.
  await page.waitForFunction((name) => {
    const overlay = document.getElementById('login-overlay');
    const input = document.getElementById('player-name');
    const button = document.getElementById('play-btn');
    return overlay &&
      getComputedStyle(overlay).display !== 'none' &&
      input &&
      input.value === name &&
      button &&
      !button.disabled;
  }, playerName, { timeout: 45000 });
  await page.waitForSelector('#play-btn:not([disabled])', { timeout: 45000 });
  samples.push(await sample(page, tab, 'before_play'));
  await page.click('#play-btn');
}

async function waitReady(page, tab, samples) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    const s = await sample(page, tab, 'ready_wait');
    samples.push(s);
    if (hasJoinedMultiplayer(s)) {
      return s;
    }
    await sleep(500);
  }
  const last = samples[samples.length - 1] || {};
  throw new Error(`${tab} did not join multiplayer: ${JSON.stringify({
    error: last.error || null,
    title: last.title || null,
    server_tick: last.server_tick ?? null,
    server_ptr_ok: last.server_ptr_ok ?? null,
    server_join_active: last.server_join_active ?? null,
    server_local_id: last.server_local_id ?? null,
    snapshot_packets_cpp: last.snapshot_packets_cpp ?? null,
    snapshot_local_applied_cpp: last.snapshot_local_applied_cpp ?? null,
  })}`);
}

function summarize(tabSamples, consoleLines) {
  const ready = tabSamples.filter((s) => !s.error);
  const proof = ready.filter((s) => s.phase === 'post_ready' || s.phase === 'drive');
  const positions = proof.map((s) => [s.local_pos_x, s.local_pos_y, s.local_pos_z]);
  const visual = proof.map((s) => [s.local_visual_pos_x, s.local_visual_pos_y, s.local_visual_pos_z]);
  let localStepMax = 0;
  let visualStepMax = 0;
  let visualAuthDeltaMax = 0;
  for (let i = 1; i < positions.length; i++) {
    localStepMax = Math.max(localStepMax, dist3(positions[i], positions[i - 1]));
    visualStepMax = Math.max(visualStepMax, dist3(visual[i], visual[i - 1]));
  }
  for (let i = 0; i < Math.min(positions.length, visual.length); i++) {
    visualAuthDeltaMax = Math.max(visualAuthDeltaMax, dist3(positions[i], visual[i]));
  }
  const last = ready[ready.length - 1] || {};
  const first = ready[0] || {};
  const proofFirst = proof[0] || {};
  const proofLast = proof[proof.length - 1] || {};
  return {
    sample_count: tabSamples.length,
    valid_sample_count: ready.length,
    proof_sample_count: proof.length,
    title_last: last.title || null,
    href_last: last.href || null,
    server_tick_first: first.server_tick ?? null,
    server_tick_last: last.server_tick ?? null,
    snapshot_ack_first: first.snapshot_last_ack_seq ?? first.snapshot_ack_seq ?? null,
    snapshot_ack_last: last.snapshot_last_ack_seq ?? last.snapshot_ack_seq ?? null,
    raw_lag_ms_max: Math.max(...ready.map((s) => num(s.lag_ms, 0)), 0),
    raw_lag_yellow_count: Math.max(...ready.map((s) => num(s.lag_yellow_count, 0)), 0),
    local_render_hard_snap_start: proofFirst.local_render_hard_snap_count ?? null,
    local_render_hard_snap_end: proofLast.local_render_hard_snap_count ?? null,
    local_render_medium_snap_start: proofFirst.local_render_medium_snap_count ?? null,
    local_render_medium_snap_end: proofLast.local_render_medium_snap_count ?? null,
    reconcile_hard_snap_start: proofFirst.reconcile_hard_snap_count ?? null,
    reconcile_hard_snap_end: proofLast.reconcile_hard_snap_count ?? null,
    local_first: positions[0] || null,
    local_last: positions[positions.length - 1] || null,
    local_step_max: localStepMax,
    local_total_delta: positions.length ? dist3(positions[0], positions[positions.length - 1]) : 0,
    local_visual_step_max: visualStepMax,
    local_visual_auth_delta_max: visualAuthDeltaMax,
    invalid_presentation_lines: consoleLines.filter((l) => l.includes('invalid presentation_kind_id')),
    console_error_count: consoleLines.filter((l) => l.startsWith('error:')).length,
  };
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const chromePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE ||
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({
    headless: false,
    executablePath: fs.existsSync(chromePath) ? chromePath : undefined,
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const tabState = {
    tab1: { samples: [], console: [] },
    tab2: { samples: [], console: [] },
  };
  const startedAt = Date.now();
  try {
    const page1 = await context.newPage();
    const page2 = await context.newPage();
    await disableCache(context, page1);
    await disableCache(context, page2);
    for (const [page, key] of [[page1, 'tab1'], [page2, 'tab2']]) {
      page.on('console', (msg) => tabState[key].console.push(`${msg.type()}:${msg.text()}`));
      page.on('pageerror', (err) => tabState[key].console.push(`pageerror:${err.message}`));
    }

    const player1 = `proofA_${startedAt}`;
    const player2 = `proofB_${startedAt}`;
    const url1 = `${TARGET}?player=${player1}&web_recorder_bridge_mode=full&proof_run=${RUN_ID}&cache_bust=${startedAt}`;
    const url2 = `${TARGET}?player=${player2}&web_recorder_bridge_mode=full&proof_run=${RUN_ID}&cache_bust=${startedAt + 1}`;
    await page1.goto(url1, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page2.goto(url2, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await clickPlay(page1, 'tab1', player1, tabState.tab1.samples);
    await clickPlay(page2, 'tab2', player2, tabState.tab2.samples);
    await waitReady(page1, 'tab1', tabState.tab1.samples);
    await waitReady(page2, 'tab2', tabState.tab2.samples);
    tabState.tab1.samples.push(await sample(page1, 'tab1', 'post_ready'));
    tabState.tab2.samples.push(await sample(page2, 'tab2', 'post_ready'));

    await page1.keyboard.down('w');
    await page1.keyboard.down('d');
    await page2.keyboard.down('s');
    await page2.keyboard.down('a');

    const until = Date.now() + DRIVE_MS;
    while (Date.now() < until) {
      tabState.tab1.samples.push(await sample(page1, 'tab1', 'drive'));
      tabState.tab2.samples.push(await sample(page2, 'tab2', 'drive'));
      await sleep(250);
    }

    await page1.keyboard.up('w');
    await page1.keyboard.up('d');
    await page2.keyboard.up('s');
    await page2.keyboard.up('a');

    const artifact = {
      run_id: RUN_ID,
      target: TARGET,
      started_at_unix_ms: startedAt,
      finished_at_unix_ms: Date.now(),
      summary: {
        tab1: summarize(tabState.tab1.samples, tabState.tab1.console),
        tab2: summarize(tabState.tab2.samples, tabState.tab2.console),
      },
      tabs: tabState,
    };
    fs.writeFileSync(OUT_PATH, JSON.stringify(artifact, null, 2));
    console.log(OUT_PATH);
    console.log(JSON.stringify(artifact.summary, null, 2));
    if (HOLD_OPEN_MS > 0) {
      console.log(`holding browser open for ${HOLD_OPEN_MS}ms`);
      await sleep(HOLD_OPEN_MS);
    }
  } catch (err) {
    const artifact = {
      run_id: RUN_ID,
      target: TARGET,
      started_at_unix_ms: startedAt,
      finished_at_unix_ms: Date.now(),
      error: String(err && err.stack || err),
      summary: {
        tab1: summarize(tabState.tab1.samples, tabState.tab1.console),
        tab2: summarize(tabState.tab2.samples, tabState.tab2.console),
      },
      tabs: tabState,
    };
    fs.writeFileSync(OUT_PATH, JSON.stringify(artifact, null, 2));
    console.error(OUT_PATH);
    throw err;
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
