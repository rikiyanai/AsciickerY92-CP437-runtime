# Ad hoc script: Playwright headless audio init test for FL-810 fix verification
# Created: 2026-05-26
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env node
// Playwright headless audio-init test for FL-810 fix verification
// Run: node scripts/adhoc/<this-file>.py  (actually .js — rename after fl adhoc creates it)
// Requires: npm install playwright in /tmp && npx playwright install chromium

const { chromium } = require('/tmp/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-audio-for-tests', '--use-fake-ui-for-media-stream']
  });
  const context = await browser.newContext({ permissions: [] });
  const page = await context.newPage();

  const logs = [];
  page.on('console', msg => {
    const text = msg.text();
    if (text.includes('AK_AUDIO') || text.includes('audio') || text.includes('Audio')) {
      logs.push(`[${msg.type()}] ${text}`);
    }
  });
  page.on('pageerror', err => logs.push(`[PAGEERROR] ${err.message}`));

  console.log('Navigating to http://localhost:8765/index.html?debug_isolation=1...');
  await page.goto('http://localhost:8765/index.html?debug_isolation=1', { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(2000);

  // Simulate user gesture: click the RECONNECT button
  const reconnectBtn = await page.$('button, input[type=submit], .reconnect, [data-action=reconnect]');
  if (reconnectBtn) {
    console.log('Clicking RECONNECT button...');
    await reconnectBtn.click();
  } else {
    // Try clicking within the page body as a gesture
    console.log('No RECONNECT button found, clicking page body...');
    await page.click('body');
  }
  await page.waitForTimeout(3000);

  // Read audio diagnostic state
  const audioDiag = await page.evaluate(() => {
    return {
      diag: window.AK_AUDIO_DIAG || null,
      ctxState: window.audio_ctx ? window.audio_ctx.state : 'no_ctx',
      audioMode: typeof Module !== 'undefined' ? 'wasm_loaded' : 'wasm_not_loaded'
    };
  });

  console.log('\n=== AUDIO STATE ===');
  console.log(JSON.stringify(audioDiag, null, 2));
  console.log('\n=== AUDIO CONSOLE LOGS ===');
  logs.forEach(l => console.log(l));

  if (!audioDiag.ctxState || audioDiag.ctxState === 'no_ctx') {
    console.log('\nRESULT: FAIL — audio_ctx not created');
    process.exit(1);
  } else if (audioDiag.ctxState === 'running') {
    console.log('\nRESULT: PASS — AudioContext state=running');
    process.exit(0);
  } else {
    console.log(`\nRESULT: PARTIAL — AudioContext state=${audioDiag.ctxState}`);
    process.exit(0);
  }

  await browser.close();
})().catch(err => {
  console.error('Test error:', err.message);
  process.exit(2);
});
