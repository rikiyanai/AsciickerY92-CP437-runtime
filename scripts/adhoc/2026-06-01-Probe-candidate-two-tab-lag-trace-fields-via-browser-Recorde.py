// Ad hoc script: Probe candidate two-tab lag trace fields via browser RecorderStateJson
// Created: 2026-06-01
// Canonical gap: candidate two-tab lag trace sampling from RecorderStateJson.

const { chromium } = require('playwright');

const RUN = Date.now();
const PLAYER_A = process.env.PLAYER_A || `lag_probe_a_${RUN}`;
const PLAYER_B = process.env.PLAYER_B || `lag_probe_b_${RUN}`;
const URL = process.env.URL || `https://candidate-asciicker.rikiworld.com/index.html?player=${PLAYER_A}&server=candidate-asciicker.rikiworld.com%3A443&map=assets%2Fa3d%2Fgame_map_y8.a3d&cache_bust=${RUN}`;
const URL2 = process.env.URL2 || `https://candidate-asciicker.rikiworld.com/index.html?player=${PLAYER_B}&server=candidate-asciicker.rikiworld.com%3A443&map=assets%2Fa3d%2Fgame_map_y8.a3d&cache_bust=${RUN + 1}`;
const OUT = process.env.OUT || '.run/candidate_lag_probe.json';
const HEADLESS = process.env.HEADLESS === '0' ? false : true;
const KEEP_OPEN = process.env.KEEP_OPEN === '1';

async function rec(page) {
  return await page.evaluate(() => {
    if (!window.__akRSJ && window.Module && window.Module.cwrap) {
      window.__akRSJ = window.Module.cwrap('RecorderStateJson', 'string', []);
    }
    if (!window.__akRSJ) return { error: 'RecorderStateJson unavailable' };
    try { return JSON.parse(window.__akRSJ()); }
    catch (e) { return { error: String(e) }; }
  });
}

async function waitJoined(page, label) {
  const end = Date.now() + 90000;
  let last = null;
  while (Date.now() < end) {
    last = await rec(page).catch(e => ({ error: String(e) }));
    if (last && !last.error &&
        ((last.server_tick || 0) > 0 ||
         (last.server_local_id !== undefined && last.server_local_id >= 0) ||
         (last.snapshot_packets_cpp || 0) > 0)) {
      return last;
    }
    await page.waitForTimeout(500);
  }
  throw new Error(label + ' did not join: ' + JSON.stringify(last));
}

async function clickPlay(page, playerName) {
  await page.waitForFunction((name) => {
    const input = document.getElementById('player-name');
    const button = document.getElementById('play-btn');
    return input && input.value === name && button && !button.disabled;
  }, playerName, { timeout: 45000 });
  await page.waitForSelector('#play-btn', { state: 'attached', timeout: 45000 });
  await page.click('#play-btn');
}

function pick(s) {
  const keys = [
    'server_tick','server_local_id','snapshot_packets_cpp','lag_ms','lag_rtt_raw_ms','lag_wait','lag_request_count','lag_response_count','lag_wait_timeout_count','lag_wait_age_ms','lag_response_age_ms','lag_measurement_stale',
    'lag_trace_client_send_to_packet_us','lag_trace_packet_to_proc_us','lag_trace_wasm_packet_proc_us','lag_trace_proc_stamp_minus_request_us','lag_trace_proc_entry_minus_request_us','lag_trace_server_rx_to_enqueue_us','lag_trace_server_enqueue_to_flush_start_us','lag_trace_server_flush_us',
    'remote0_pos_x','remote0_pos_y','remote0_pos_z','remote0_post_interp_pos_x','remote0_post_interp_pos_y','remote0_post_interp_pos_z','remote0_view_x','remote0_view_y','remote0_post_interp_view_x','remote0_post_interp_view_y','visible_remote','body_visible_remote','label_only_remote','remote0_final_label_drawn','remote0_final_body_drawn','remote0_post_interp_has_inst','dbg_remote0_interp_active','dbg_remote0_interp_delay_ms','dbg_remote0_interp_fallback_mode'
  ];
  const out = {};
  for (const k of keys) out[k] = s ? s[k] : undefined;
  return out;
}

(async () => {
  const chromePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE ||
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({
    headless: HEADLESS,
    executablePath: chromePath,
    args: ['--host-resolver-rules=MAP candidate-asciicker.rikiworld.com 35.226.113.14']
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 720 } });
  const a = await ctx.newPage();
  const b = await ctx.newPage();
  const errors = [];
  for (const p of [a,b]) {
    p.on('pageerror', e => errors.push(String(e)));
    p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  }
  await Promise.all([a.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 }), b.goto(URL2, { waitUntil: 'domcontentloaded', timeout: 60000 })]);
  await b.bringToFront();
  await a.bringToFront();
  await Promise.all([clickPlay(a, PLAYER_A), clickPlay(b, PLAYER_B)]);
  const joinedA = await waitJoined(a, 'a');
  const joinedB = await waitJoined(b, 'b');
  const samples = [];
  for (let i = 0; i < 90; i++) {
    if (i === 10) await a.keyboard.down('d');
    if (i === 40) await a.keyboard.up('d');
    const [ra, rb] = await Promise.all([rec(a), rec(b)]);
    samples.push({ t: Date.now(), a: pick(ra), b: pick(rb) });
    await a.waitForTimeout(100);
  }
  await a.keyboard.up('d').catch(() => {});
  const [rawLastA, rawLastB] = await Promise.all([rec(a), rec(b)]);
  await a.screenshot({ path: OUT.replace(/\.json$/, '_a.png'), fullPage: false }).catch(() => {});
  await b.screenshot({ path: OUT.replace(/\.json$/, '_b.png'), fullPage: false }).catch(() => {});
  const fs = require('fs');
  fs.mkdirSync(require('path').dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify({ url: URL, url2: URL2, joinedA: pick(joinedA), joinedB: pick(joinedB), errors, samples, rawLastA, rawLastB }, null, 2));
  console.log(OUT);
  console.log(JSON.stringify({ joinedA: pick(joinedA), joinedB: pick(joinedB), errors: errors.slice(0,5), last: samples[samples.length-1] }, null, 2));
  if (KEEP_OPEN) {
    console.log('KEEP_OPEN=1: browser left open for manual testing');
    await new Promise(() => {});
  }
  await browser.close();
})().catch(e => { console.error(e.stack || e); process.exit(1); });
