#!/usr/bin/env node
// Ad hoc script: Playwright local FL-810 manual audio button proof for rebuilt CC0 samples
// Created: 2026-05-26
// Canonical gap: FL-810 needs a maintained local manual audio-path proof runner.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const outDir = path.resolve(process.cwd(), 'output/playwright');
fs.mkdirSync(outDir, { recursive: true });
const runId = `local-fl810-cc0-audio-${new Date().toISOString().replace(/[:.]/g, '-')}`;
const outPath = path.join(outDir, `${runId}.json`);
const url = process.env.ASCIICKER_AUDIO_TEST_URL || 'http://127.0.0.1:8000/?audio_manual_test=1&player=audio_cc0&server=wss%3A%2F%2Fcandidate-asciicker.rikiworld.com%2Fws%2Fy8';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const chromePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({
    headless: false,
    executablePath: fs.existsSync(chromePath) ? chromePath : undefined,
    args: ['--host-resolver-rules=MAP candidate-asciicker.rikiworld.com 35.226.113.14'],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  const consoleLines = [];
  page.on('console', (msg) => consoleLines.push(`${msg.type()}:${msg.text()}`));
  page.on('pageerror', (err) => consoleLines.push(`pageerror:${err.message}`));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#play-btn', { timeout: 45000 });
  await page.click('#play-btn');
  await sleep(3500);
  const before = await page.evaluate(() => ({
    diag: window.AK_AUDIO_DIAG || null,
    state: window.audio_ctx ? window.audio_ctx.state : null,
    node: window.audio_node && window.audio_node.constructor ? window.audio_node.constructor.name : null,
    samples: (() => {
      try { return Object.keys(FS.root.contents.assets.contents.samples.contents).sort(); }
      catch (e) { return []; }
    })(),
    debug: (() => {
      try { return Module.ccall('AudioDebugStateJson', 'string', [], []); }
      catch (e) { return `ERR:${String(e && e.message || e)}`; }
    })(),
  }));
  const result = await page.evaluate(() => Module.ccall('AudioDebugPlayJump', 'number', [], []));
  await sleep(1200);
  const after = await page.evaluate(() => ({
    diag: window.AK_AUDIO_DIAG || null,
    state: window.audio_ctx ? window.audio_ctx.state : null,
    node: window.audio_node && window.audio_node.constructor ? window.audio_node.constructor.name : null,
    debug: (() => {
      try { return Module.ccall('AudioDebugStateJson', 'string', [], []); }
      catch (e) { return `ERR:${String(e && e.message || e)}`; }
    })(),
  }));
  const artifact = { run_id: runId, url, before, result, after, console: consoleLines };
  fs.writeFileSync(outPath, JSON.stringify(artifact, null, 2));
  console.log(outPath);
  console.log(JSON.stringify({ result, before, after }, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
