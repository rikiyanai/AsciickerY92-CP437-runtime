#!/usr/bin/env node
// Ad hoc script: Candidate FL-810 browser export/runtime audio state probe
// Created: 2026-05-27
// Canonical gap: FL-810 needs a maintained candidate browser export/runtime audio probe.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const outDir = path.resolve(process.cwd(), 'output/playwright');
fs.mkdirSync(outDir, { recursive: true });
const runId = `candidate-fl810-export-audio-${new Date().toISOString().replace(/[:.]/g, '-')}`;
const outPath = path.join(outDir, `${runId}.json`);
const url = process.env.ASCIICKER_AUDIO_TEST_URL || 'https://candidate-asciicker.rikiworld.com/?audio_manual_test=1&player=audio_export_probe';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await chromium.launch({
    headless: false,
    executablePath: fs.existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome') ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : undefined,
    args: ['--host-resolver-rules=MAP candidate-asciicker.rikiworld.com 35.226.113.14'],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  const consoleLines = [];
  page.on('console', msg => consoleLines.push(`${msg.type()}:${msg.text()}`));
  page.on('pageerror', err => consoleLines.push(`pageerror:${err.message}`));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#play-btn', { timeout: 45000 });
  await page.click('#play-btn');
  await sleep(5000);
  const state = await page.evaluate(() => {
    const names = ['_AudioDebugPlayJump','_AudioDebugStateJson','_Audio','_Sample','_XOgg','_SwitchToScriptProcessorMode','_GetAudioMode','_AudioRestoreForestAmbient','_Load'];
    const exports = {};
    for (const n of names) exports[n] = typeof Module[n];
    let debugJson = null;
    let debugErr = null;
    try { debugJson = Module.ccall('AudioDebugStateJson', 'string', [], []); }
    catch (e) { debugErr = String(e && e.message || e); }
    let manualState = null;
    try { manualState = window.AudioDebugState ? window.AudioDebugState() : null; }
    catch (e) { manualState = { error: String(e && e.message || e) }; }
    return {
      href: location.href,
      build: window.__akBuildVersion || null,
      title: document.title,
      mpStage: window.ak_mp_stage || null,
      joined: window.ak_joined || null,
      audioCtx: window.audio_ctx ? window.audio_ctx.state : null,
      audioNode: window.audio_node && window.audio_node.constructor ? window.audio_node.constructor.name : null,
      diag: window.AK_AUDIO_DIAG || null,
      exports,
      debugJson,
      debugErr,
      manualState,
    };
  });
  const artifact = { run_id: runId, url, state, console: consoleLines };
  fs.writeFileSync(outPath, JSON.stringify(artifact, null, 2));
  console.log(outPath);
  console.log(JSON.stringify(state, null, 2));
  await browser.close();
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
