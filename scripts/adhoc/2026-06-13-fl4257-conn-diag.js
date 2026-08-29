// FL-4257 headed-proof connection diagnostic: why the web client stalls at
// "CONNECTING..." against the local native server. Dumps console, page errors,
// WS lifecycle, and the on-page diagnostic panel over 60s.
const { chromium } = require('playwright');
(async () => {
  const webPort = process.argv[2] || '8099';
  const serverAddr = process.argv[3] || 'localhost:8077';
  const browser = await chromium.launch({ headless: false, channel: 'chrome',
    args: ['--use-gl=angle', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('console', m => console.log('[console]', m.type(), m.text().slice(0, 200)));
  page.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 200)));
  page.on('websocket', ws => {
    console.log('[ws] open ->', ws.url());
    ws.on('close', () => console.log('[ws] close', ws.url()));
    ws.on('socketerror', e => console.log('[ws] error', e));
  });
  await page.goto(`http://localhost:${webPort}/index.html`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#play-btn', { timeout: 60000 });
  await page.fill('#player-name', 'S3TEST');
  await page.evaluate(a => { const s = document.getElementById('server-addr'); if (s) s.value = a; }, serverAddr);
  console.log('clicking play');
  await page.click('#play-btn');
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(5000);
    const state = await page.evaluate(() => {
      const overlay = document.getElementById('login-overlay');
      const dbg = document.getElementById('login-debug');
      return {
        overlayVisible: overlay ? (overlay.offsetParent !== null) : 'no-overlay',
        debug: dbg ? dbg.textContent.replace(/\s+/g, ' ').slice(0, 300) : 'no-debug',
      };
    });
    console.log(`[t+${(i+1)*5}s] overlayVisible=${state.overlayVisible} :: ${state.debug}`);
  }
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
