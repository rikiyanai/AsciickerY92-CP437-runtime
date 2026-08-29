'use strict';

// FL-4137 wireframe diagnostic: prove whether the SVG corners shift on screen
// as the camera moves. Reads auth_item_sample[].corner_cols/rows directly
// from the recorder before and after walking — if cells change, C++ projection
// is updating per frame; if cells freeze, the bug is in C++.

const driver = require('./proof_driver_playwright.js');

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function recorderJson(page) {
  return page.evaluate(() => {
    if (!window.__akRecorderStateJson && window.Module && window.Module.cwrap) {
      window.__akRecorderStateJson = window.Module.cwrap('RecorderStateJson', 'string', []);
    }
    if (!window.__akRecorderStateJson) return { error: 'unavailable' };
    try { return JSON.parse(window.__akRecorderStateJson()); }
    catch (e) { return { error: 'parse: ' + String(e) }; }
  });
}

async function main() {
  const page = await driver.openProofPage({
    mapPath: process.env.PROOF_MAP || 'assets/a3d/game_map_y8.a3d',
  });
  await sleep(2000);

  // Sample 1: at spawn
  let rec = await recorderJson(page);
  const block1 = (rec.auth_item_sample || []).find(s => s.item_definition_id === 420);
  if (!block1 || !block1.corners_valid) {
    console.error('FAIL: block or corners not present in initial recorder');
    await driver.cleanup(); process.exit(1);
  }
  console.log('[wireframe-diag] sample 1 @ player=(' +
              rec.local_pos_x + ',' + rec.local_pos_y + ',' + rec.local_pos_z +
              ') block_corner_cols=' + JSON.stringify(block1.corner_cols) +
              ' rows=' + JSON.stringify(block1.corner_rows));

  // Focus canvas then walk
  try { await page.focus('#asciicker_canvas'); } catch (_) {}
  try { await page.click('#asciicker_canvas'); } catch (_) {}
  // Walk west using 'a' — same pattern proven to work in heavy-break proof.
  for (let i = 0; i < 6; i++) {
    await page.keyboard.down('a');
    await sleep(400);
    await page.keyboard.up('a');
    await sleep(100);
  }
  await sleep(500);

  // Sample 2: after walking south
  rec = await recorderJson(page);
  const block2 = (rec.auth_item_sample || []).find(s => s.item_definition_id === 420);
  console.log('[wireframe-diag] sample 2 @ player=(' +
              rec.local_pos_x + ',' + rec.local_pos_y + ',' + rec.local_pos_z +
              ') block_corner_cols=' + JSON.stringify(block2 ? block2.corner_cols : 'gone') +
              ' rows=' + JSON.stringify(block2 ? block2.corner_rows : 'gone'));

  // Compare
  const moved_world = Math.abs(rec.local_pos_y - (block1 ? block1.x : 0)); // sanity
  const cellsDiffer = !block2 || JSON.stringify(block1.corner_cols) !== JSON.stringify(block2.corner_cols) ||
                                  JSON.stringify(block1.corner_rows) !== JSON.stringify(block2.corner_rows);
  if (cellsDiffer) {
    console.log('[wireframe-diag] PASS: corner cells changed between samples — C++ projection IS updating per frame.');
    console.log('[wireframe-diag] Diff: cols delta=' +
                JSON.stringify(block1.corner_cols.map((v,i) => (block2.corner_cols[i] - v))) +
                ' rows delta=' +
                JSON.stringify(block1.corner_rows.map((v,i) => (block2.corner_rows[i] - v))));
  } else {
    console.error('[wireframe-diag] FAIL: corner cells UNCHANGED across player movement. C++ projection is stuck on stale data.');
  }

  await driver.cleanup();
  process.exit(cellsDiffer ? 0 : 1);
}

main().catch(async e => {
  console.error('FATAL: ' + (e && e.stack ? e.stack : String(e)));
  try { await driver.cleanup(); } catch (_) {}
  process.exit(1);
});
