#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const TARGET = process.env.ASCIICKER_PROOF_URL || 'https://candidate-asciicker.rikiworld.com/';
const OUT_DIR = path.resolve(process.cwd(), 'output/playwright');
const RUN_ID = `candidate-audio-smoke-${new Date().toISOString().replace(/[:.]/g, '-')}`;
const OUT_PATH = path.join(OUT_DIR, `${RUN_ID}.json`);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function sample(page) {
  return page.evaluate(() => {
    const getObs = () => {
      try {
        if (!window.Module || typeof window.Module.cwrap !== 'function') return null;
        window.__audioProofObs = window.__audioProofObs ||
          window.Module.cwrap('ClientObservationJsonV1', 'string', []);
        return JSON.parse(window.__audioProofObs());
      } catch (e) {
        return { error: String(e && e.message || e) };
      }
    };
    const samplesDir = (() => {
      try {
        const root = window.FS && window.FS.root && window.FS.root.contents;
        return root && root.assets && root.assets.contents.samples &&
          root.assets.contents.samples.contents;
      } catch (e) {
        return null;
      }
      return null;
    })();
    const sampleNames = samplesDir ? Object.keys(samplesDir).sort() : [];
    return {
      href: location.href,
      title: document.title,
      obs: getObs(),
      audio_ctx_type: typeof window.audio_ctx,
      audio_ctx_state: window.audio_ctx ? window.audio_ctx.state : null,
      audio_ctx_sample_rate: window.audio_ctx ? window.audio_ctx.sampleRate : null,
      audio_node_type: typeof window.audio_node,
      audio_port_type: typeof window.audio_port,
      audio_exports: {
        Audio: !!(window.Module && window.Module._Audio),
        Sample: !!(window.Module && window.Module._Sample),
        XOgg: !!(window.Module && window.Module._XOgg),
      },
      packaged_samples: sampleNames,
      proof: window.__audioProof || null,
    };
  });
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const chromePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE ||
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({
    headless: false,
    executablePath: fs.existsSync(chromePath) ? chromePath : undefined,
    args: ['--host-resolver-rules=MAP candidate-asciicker.rikiworld.com 35.226.113.14'],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });

  const consoleLines = [];
  page.on('console', (msg) => consoleLines.push(`${msg.type()}:${msg.text()}`));
  page.on('pageerror', (err) => consoleLines.push(`pageerror:${err.message}`));
  await page.addInitScript(() => {
    window.__audioProof = {
      audio_context_constructed: 0,
      add_module_calls: [],
      add_module_resolved: 0,
      add_module_rejected: [],
      node_constructed: 0,
      resume_calls: 0,
    };
    const install = () => {
      const Orig = window.AudioContext || window.webkitAudioContext;
      if (!Orig || Orig.__asciickerAudioProofWrapped) return;
      class WrappedAudioContext extends Orig {
        constructor(...args) {
          super(...args);
          window.__audioProof.audio_context_constructed += 1;
          const worklet = this.audioWorklet;
          if (worklet && typeof worklet.addModule === 'function') {
            const origAddModule = worklet.addModule.bind(worklet);
            worklet.addModule = (url, opts) => {
              window.__audioProof.add_module_calls.push(String(url));
              return origAddModule(url, opts).then((v) => {
                window.__audioProof.add_module_resolved += 1;
                return v;
              }, (e) => {
                window.__audioProof.add_module_rejected.push(String(e && e.message || e));
                throw e;
              });
            };
          }
          const origResume = this.resume.bind(this);
          this.resume = () => {
            window.__audioProof.resume_calls += 1;
            return origResume();
          };
        }
      }
      WrappedAudioContext.__asciickerAudioProofWrapped = true;
      window.AudioContext = WrappedAudioContext;
      if (window.webkitAudioContext) window.webkitAudioContext = WrappedAudioContext;
      if (window.AudioWorkletNode && !window.AudioWorkletNode.__asciickerAudioProofWrapped) {
        const OrigNode = window.AudioWorkletNode;
        window.AudioWorkletNode = class WrappedAudioWorkletNode extends OrigNode {
          constructor(...args) {
            super(...args);
            window.__audioProof.node_constructed += 1;
          }
        };
        window.AudioWorkletNode.__asciickerAudioProofWrapped = true;
      }
    };
    install();
  });

  const startedAt = Date.now();
  const playerName = `audio_${startedAt}`;
  const url = `${TARGET}?player=${playerName}&web_recorder_bridge_mode=full&proof_run=${RUN_ID}&cache_bust=${startedAt}`;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  // FL-4132: this smoke shares the login overlay path with the two-tab recipe;
  // wait for the URL name to be applied before clicking PLAY.
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
  await page.click('#play-btn');
  const samples = [];
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const s = await sample(page);
    samples.push({ at: Date.now(), state: s });
    if (s.obs && s.obs.world_ready && s.audio_exports.Audio && s.packaged_samples.includes('jump.ogg')) break;
    await sleep(500);
  }
  await page.mouse.click(640, 430);
  await page.keyboard.press('Space');
  await sleep(1000);
  await page.keyboard.press('KeyJ');
  await sleep(1000);
  samples.push({ at: Date.now(), state: await sample(page) });

  const artifact = {
    run_id: RUN_ID,
    target: TARGET,
    started_at_unix_ms: startedAt,
    finished_at_unix_ms: Date.now(),
    summary: samples[samples.length - 1].state,
    samples,
    console: consoleLines,
  };
  fs.writeFileSync(OUT_PATH, JSON.stringify(artifact, null, 2));
  console.log(OUT_PATH);
  console.log(JSON.stringify(artifact.summary, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
