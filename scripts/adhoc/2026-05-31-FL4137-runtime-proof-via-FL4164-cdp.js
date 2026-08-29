// FL-4137 runtime proof orchestrator via the FL-4164 owned CDP probe path.
//
// Reuses scripts/adhoc/2026-05-31-Live-CDP-probe-for-Y8-wasm-collision-debug-tab.js's
// attach approach (chromium.connectOverCDP), and reuses the launcher
// scripts/adhoc/2026-05-31-FL4164-owned-chrome-cdp-launcher.sh's Chrome instance.
//
// Goal: produce evidence for the 13 runtime gates the operator enumerated for
// FL-4137 closure. Captures: PlayButton click on both tabs, wait until
// RecorderStateJson reports auth_item_sample populated for the actor tab,
// sample recorder state + frame buffer + screenshot on BOTH tabs, compare
// actor and observer views, save artifacts under .run/fl4137_runtime_proof_*.
//
// Does NOT mutate gameplay state via debug cwrap exports. Only sends DOM
// keyboard input via page.keyboard.* (real user intent surface). Honours
// Law 7 (debug-mutator-free) and Law 11 (manual/scripted parity via the
// same recorder schema).
//
// Env:
//   CDP_URL=http://127.0.0.1:9223
//   OUT_PREFIX=.run/fl4137_runtime_proof
//   READY_MS=60000     (timeout to wait per tab for join)
//
// Exit codes:
//   0  baseline observation captured ok
//   1  one or more tabs failed to reach gameplay
//   2  CDP connect failed

'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9223';
const OUT_PREFIX = process.env.OUT_PREFIX ||
  path.join(process.cwd(), '.run', 'fl4137_runtime_proof');
const READY_MS = parseInt(process.env.READY_MS || '60000', 10);
const ACTOR_HINT = process.env.ACTOR_HINT || 'player=fl4137_proof&';
const OBSERVER_HINT = process.env.OBSERVER_HINT || 'player=fl4137_proof_observer';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function tabRecorder(page) {
  return page.evaluate(() => {
    const m = window.Module;
    if (!m || !m.cwrap) return { error: 'Module.cwrap missing' };
    if (!window.__akRSJ) window.__akRSJ = m.cwrap('RecorderStateJson', 'string', []);
    try { return JSON.parse(window.__akRSJ()); }
    catch (e) { return { error: 'RecorderStateJson parse: ' + String(e) }; }
  });
}
async function tabFrame(page) {
  return page.evaluate(() => {
    const m = window.Module;
    if (!m || !m.cwrap) return { error: 'Module.cwrap missing' };
    if (!window.__akFRJ) window.__akFRJ = m.cwrap('GetCppAnsiFrameSnapshotJson', 'string', []);
    try { return JSON.parse(window.__akFRJ()); }
    catch (e) { return { error: 'frame parse: ' + String(e) }; }
  });
}

async function clickPlayIfPresent(page, tag) {
  try {
    await page.waitForSelector('#play-btn', { state: 'attached', timeout: 15000 });
    const btnState = await page.evaluate(() => {
      const b = document.getElementById('play-btn');
      return b ? { exists: true, disabled: !!b.disabled, txt: (b.textContent || '').trim().slice(0, 40) } : { exists: false };
    });
    console.log(`[${tag}] #play-btn state: ${JSON.stringify(btnState)}`);
    if (btnState.exists && !btnState.disabled) {
      await page.click('#play-btn');
      console.log(`[${tag}] clicked #play-btn`);
      return true;
    }
    if (btnState.exists && btnState.disabled) {
      await page.waitForFunction(() => {
        const b = document.getElementById('play-btn');
        return !!b && !b.disabled;
      }, null, { timeout: 60000 });
      await page.click('#play-btn');
      console.log(`[${tag}] clicked #play-btn (after enable wait)`);
      return true;
    }
  } catch (e) {
    console.log(`[${tag}] play-btn unavailable: ${e.message}`);
  }
  // Fallback: Enter on player-name input.
  try {
    await page.focus('#player-name');
    await page.keyboard.press('Enter');
    console.log(`[${tag}] sent Enter on #player-name`);
    return true;
  } catch (e) {
    console.log(`[${tag}] Enter fallback failed: ${e.message}`);
  }
  return false;
}

async function waitForRecorderReady(page, tag, ms) {
  const start = Date.now();
  let lastSample = null;
  while (Date.now() - start < ms) {
    const rec = await tabRecorder(page);
    lastSample = rec;
    if (!rec.error && Array.isArray(rec.auth_item_sample) && rec.auth_item_sample.length > 0) {
      console.log(`[${tag}] recorder ready after ${Date.now() - start}ms (auth_item_sample=${rec.auth_item_sample.length})`);
      return true;
    }
    await sleep(750);
  }
  console.log(`[${tag}] recorder NOT ready after ${ms}ms; last=${JSON.stringify({err: lastSample && lastSample.error, keys: lastSample ? Object.keys(lastSample).slice(0,12) : null}).slice(0,200)}`);
  return false;
}

async function snapshotTab(page, tag) {
  const rec = await tabRecorder(page);
  const frame = await tabFrame(page);
  const ssPath = `${OUT_PREFIX}_${tag}.png`;
  try { await page.screenshot({ path: ssPath, fullPage: false }); } catch (_) {}
  const errors = await page.evaluate(() => {
    return { url: location.href, title: document.title };
  });
  return {
    tag,
    url: errors.url,
    title: errors.title,
    rec_error: rec.error || null,
    recorder_keys: rec && !rec.error ? Object.keys(rec).slice(0, 60) : null,
    auth_item_sample_count: rec && !rec.error && Array.isArray(rec.auth_item_sample) ? rec.auth_item_sample.length : 0,
    auth_visible_world_count: rec.auth_visible_world_count || 0,
    auth_visible_world_item_ids: rec.auth_visible_world_item_ids || null,
    auth_visible_world_definition_ids: rec.auth_visible_world_definition_ids || null,
    local_pos: rec.local_pos || (rec.local_pos_x !== undefined ? { x: rec.local_pos_x, y: rec.local_pos_y, z: rec.local_pos_z } : null),
    server_tick: rec.server_tick || rec.tick || 0,
    snapshot_pos: rec.snapshot_local_pos_x_cpp !== undefined ? {
      x: rec.snapshot_local_pos_x_cpp, y: rec.snapshot_local_pos_y_cpp, z: rec.snapshot_local_pos_z_cpp,
      support_valid: rec.snapshot_local_support_valid_cpp,
      support_source: rec.snapshot_local_support_source_cpp,
      support_item_id: rec.snapshot_local_support_item_id_cpp,
      support_z: rec.snapshot_local_support_z_cpp,
    } : null,
    frame_error: frame.error || null,
    frame_valid: !!frame.valid,
    frame_w: frame.width || 0,
    frame_h: frame.height || 0,
    screenshot_path: ssPath,
  };
}

async function main() {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL);
  } catch (e) {
    console.error(`CDP connect failed at ${CDP_URL}: ${e.message}`);
    process.exit(2);
  }
  const pages = browser.contexts().flatMap(c => c.pages());
  const actor = pages.find(p => p.url().includes(ACTOR_HINT)) || pages.find(p => p.url().includes('index.html'));
  const observer = pages.find(p => p.url().includes(OBSERVER_HINT));

  const phases = {};

  // Phase 0: tab discovery
  phases.phase0_discovery = {
    cdp_url: CDP_URL,
    total_tabs: pages.length,
    actor_found: !!actor,
    observer_found: !!observer,
    actor_url: actor ? actor.url() : null,
    observer_url: observer ? observer.url() : null,
  };
  console.log(JSON.stringify(phases.phase0_discovery, null, 2));
  if (!actor) {
    fs.writeFileSync(`${OUT_PREFIX}.json`, JSON.stringify({ ok: false, phases }, null, 2));
    process.exit(1);
  }

  // Phase 1: click play on both tabs
  await actor.bringToFront();
  const actorClicked = await clickPlayIfPresent(actor, 'actor');
  let observerClicked = false;
  if (observer) {
    await observer.bringToFront();
    observerClicked = await clickPlayIfPresent(observer, 'observer');
  }
  phases.phase1_play_clicks = { actorClicked, observerClicked };

  // Phase 2: wait for recorder ready on actor
  const actorReady = await waitForRecorderReady(actor, 'actor', READY_MS);
  let observerReady = false;
  if (observer) observerReady = await waitForRecorderReady(observer, 'observer', READY_MS);
  phases.phase2_ready = { actorReady, observerReady };

  // Phase 3: baseline snapshot
  phases.phase3_baseline_actor = await snapshotTab(actor, 'baseline_actor');
  if (observer) phases.phase3_baseline_observer = await snapshotTab(observer, 'baseline_observer');

  // Phase 4: visible-block presence — does either tab see placed blocks (def 420)
  // pre-place? game_map_y8 has no seeded blocks; sandbox does. For y8 expect 0.
  function definitionCounts(snap) {
    const ids = (snap && snap.auth_visible_world_definition_ids) || [];
    const ct = { 420: 0, 421: 0, 422: 0 };
    for (const id of ids) if (ct[id] !== undefined) ct[id]++;
    return ct;
  }
  phases.phase4_visible_block_defs_actor = definitionCounts(phases.phase3_baseline_actor);
  if (observer) phases.phase4_visible_block_defs_observer = definitionCounts(phases.phase3_baseline_observer);

  const out = {
    ok: actorReady && (observer ? observerReady : true) &&
        !phases.phase3_baseline_actor.frame_error &&
        phases.phase3_baseline_actor.frame_valid,
    timestamp: new Date().toISOString(),
    head: process.env.HEAD || null,
    phases,
  };
  fs.writeFileSync(`${OUT_PREFIX}.json`, JSON.stringify(out, null, 2));
  console.log(`\n[fl4137-runtime] wrote ${OUT_PREFIX}.json (ok=${out.ok})`);
  console.log(JSON.stringify({
    ok: out.ok,
    actor_visible_world_count: phases.phase3_baseline_actor.auth_visible_world_count,
    actor_block_defs: phases.phase4_visible_block_defs_actor,
    actor_frame_valid: phases.phase3_baseline_actor.frame_valid,
    actor_frame_error: phases.phase3_baseline_actor.frame_error,
    observer_visible_world_count: phases.phase3_baseline_observer ? phases.phase3_baseline_observer.auth_visible_world_count : null,
    observer_frame_valid: phases.phase3_baseline_observer ? phases.phase3_baseline_observer.frame_valid : null,
  }, null, 2));
  await browser.close();
  process.exit(out.ok ? 0 : 1);
}

main().catch(e => {
  console.error('[fl4137-runtime] FATAL: ' + (e && e.stack ? e.stack : String(e)));
  process.exit(3);
});
