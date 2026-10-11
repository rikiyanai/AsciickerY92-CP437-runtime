// FL-4137 phase probe: capture one operator-driven phase via the FL-4164
// owned CDP path. Intended to be run between operator actions on the actor
// tab. Samples actor + observer recorder state, framebuffer, screenshots,
// and writes a compact phase artifact.
//
// Usage:
//   PHASE=G1_baseline node scripts/adhoc/2026-05-31-FL4137-phase-probe.js
//
// Env:
//   PHASE=name          (required) phase label, used in artifact filenames
//   CDP_URL=http://127.0.0.1:9223
//   ACTOR_HINT=player=fl4137_proof&     URL substring to find actor tab
//   OBSERVER_HINT=player=fl4137_proof_observer
//   OUT_DIR=.run         output dir for artifacts
//
// Artifacts written per phase:
//   <OUT_DIR>/fl4137_<PHASE>_actor.png       screenshot
//   <OUT_DIR>/fl4137_<PHASE>_observer.png    screenshot
//   <OUT_DIR>/fl4137_<PHASE>_actor_frame.json    framebuffer json
//   <OUT_DIR>/fl4137_<PHASE>_observer_frame.json framebuffer json
//   <OUT_DIR>/fl4137_<PHASE>_summary.json    compact recorder+derived summary
//
// Does NOT mutate gameplay (no cwrap exports called other than the two
// read-only probes RecorderStateJson + GetCppAnsiFrameSnapshotJson).

'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const PHASE = process.env.PHASE;
if (!PHASE) { console.error('PHASE env required'); process.exit(2); }
const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9223';
const ACTOR_HINT = process.env.ACTOR_HINT || 'player=fl4137_proof&';
const OBSERVER_HINT = process.env.OBSERVER_HINT || 'player=fl4137_proof_observer';
const OUT_DIR = process.env.OUT_DIR || path.join(process.cwd(), '.run');

async function readRec(page) {
  return page.evaluate(() => {
    const m = window.Module;
    if (!m || !m.cwrap) return { error: 'no Module.cwrap' };
    if (!window.__akRSJ) window.__akRSJ = m.cwrap('RecorderStateJson', 'string', []);
    try { return JSON.parse(window.__akRSJ()); }
    catch (e) { return { error: 'RecorderStateJson: ' + String(e) }; }
  });
}
async function readFrame(page) {
  return page.evaluate(() => {
    const m = window.Module;
    if (!m || !m.cwrap) return { error: 'no Module.cwrap' };
    if (!window.__akFRJ) window.__akFRJ = m.cwrap('GetCppAnsiFrameSnapshotJson', 'string', []);
    try { return JSON.parse(window.__akFRJ()); }
    catch (e) { return { error: 'frame: ' + String(e) }; }
  });
}

function decodeCells(frame) {
  // GetCppAnsiFrameSnapshotJson returns { valid, width, height, cells_base64 }
  // cells_base64 packs each cell as (gl: u16 LE, fg: u8, bk: u8) = 4 bytes
  if (!frame || !frame.valid || !frame.cells_base64) return null;
  const buf = Buffer.from(frame.cells_base64, 'base64');
  const W = frame.width, H = frame.height;
  if (buf.length < W * H * 4) return null;
  const cells = new Array(W * H);
  for (let i = 0; i < W * H; i++) {
    const o = i * 4;
    cells[i] = { gl: buf.readUInt16LE(o), fg: buf[o + 2], bk: buf[o + 3] };
  }
  return cells;
}

function summarizeBlocks(rec) {
  // Extract per-block geometry summary from auth_item_sample when present.
  const sample = (rec && rec.auth_item_sample) || [];
  return sample
    .filter(it => it && (it.item_definition_id === 420 || it.item_definition_id === 421 || it.item_definition_id === 422 ||
                         (it.def !== undefined && (it.def === 420 || it.def === 421 || it.def === 422))))
    .map(it => ({
      id: it.id || it.item_id,
      def: it.item_definition_id || it.def,
      owner: it.owner_id,
      flags: it.state_flags,
      pos: { x: it.x, y: it.y, z: it.z },
      half_extent: it.half_extent,
      height: it.height,
      collision_top_z: it.collision_top_z,
      support_top_z: it.support_top_z,
      visual_top_z: it.visual_top_z,
      visual_bottom_z: it.visual_bottom_z,
    }));
}

function projectGridFromCorners(corners) {
  // recorder publishes a `screen_corners` list for placed blocks when wireframe
  // is enabled. compute screen bounding rect.
  if (!Array.isArray(corners) || corners.length === 0) return null;
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (const c of corners) {
    if (c.col === undefined || c.row === undefined) continue;
    xmin = Math.min(xmin, c.col); xmax = Math.max(xmax, c.col);
    ymin = Math.min(ymin, c.row); ymax = Math.max(ymax, c.row);
  }
  return Number.isFinite(xmin) ? { col0: xmin, col1: xmax, row0: ymin, row1: ymax } : null;
}

async function snapshot(page, tag) {
  const rec = await readRec(page);
  const frame = await readFrame(page);
  const cells = decodeCells(frame);
  const ssPath = path.join(OUT_DIR, `fl4137_${PHASE}_${tag}.png`);
  const framePath = path.join(OUT_DIR, `fl4137_${PHASE}_${tag}_frame.json`);
  try { await page.screenshot({ path: ssPath, fullPage: false }); } catch (_) {}
  if (frame && frame.valid) fs.writeFileSync(framePath, JSON.stringify(frame));

  // Count block-like glyph signatures in the framebuffer if cells decoded.
  // BLOCK_GLYPH=219 with palette indexes used by current renderer.
  let block_glyph_count = 0;
  if (cells) {
    for (const c of cells) if (c.gl === 219) block_glyph_count++;
  }

  return {
    tag,
    url: page.url(),
    rec_error: rec.error || null,
    server_tick: rec.server_tick || rec.tick || 0,
    auth_visible_world_count: rec.auth_visible_world_count || 0,
    auth_visible_world_item_ids: rec.auth_visible_world_item_ids || null,
    auth_visible_world_definition_ids: rec.auth_visible_world_definition_ids || null,
    auth_pickup_strip_count: rec.auth_pickup_strip_count || 0,
    auth_pickup_strip_item_ids: rec.auth_pickup_strip_item_ids || null,
    auth_place_req_attempts: rec.auth_place_req_attempts || 0,
    auth_place_req_sent: rec.auth_place_req_sent || 0,
    auth_place_req_last_item_id: rec.auth_place_req_last_item_id || 0,
    auth_place_req_last_reason: rec.auth_place_req_last_reason || 0,
    local_pos: rec.local_pos_x !== undefined ? { x: rec.local_pos_x, y: rec.local_pos_y, z: rec.local_pos_z } : null,
    snapshot_pos: rec.snapshot_local_pos_x_cpp !== undefined ? {
      x: rec.snapshot_local_pos_x_cpp, y: rec.snapshot_local_pos_y_cpp, z: rec.snapshot_local_pos_z_cpp,
      support_valid: rec.snapshot_local_support_valid_cpp,
      support_source: rec.snapshot_local_support_source_cpp,
      support_item_id: rec.snapshot_local_support_item_id_cpp,
      support_z: rec.snapshot_local_support_z_cpp,
    } : null,
    blocks: summarizeBlocks(rec),
    input_show_inventory: rec.input_show_inventory_active,
    input_main_menu: rec.input_main_menu_active,
    input_talk_box: rec.input_talk_box_active,
    frame_w: frame.width || 0,
    frame_h: frame.height || 0,
    frame_valid: !!frame.valid,
    framebuf_block_glyph_count: block_glyph_count,
    screenshot_path: ssPath,
    frame_path: frame && frame.valid ? framePath : null,
  };
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  let browser;
  try { browser = await chromium.connectOverCDP(CDP_URL); }
  catch (e) { console.error('CDP connect failed: ' + e.message); process.exit(3); }
  const pages = browser.contexts().flatMap(c => c.pages());
  const actor = pages.find(p => p.url().includes(ACTOR_HINT)) || pages.find(p => p.url().includes('index.html'));
  const observer = pages.find(p => p.url().includes(OBSERVER_HINT));
  if (!actor) { console.error('actor tab not found via ' + ACTOR_HINT); process.exit(4); }

  const actorSnap = await snapshot(actor, 'actor');
  const observerSnap = observer ? await snapshot(observer, 'observer') : null;

  // Cross-tab agreement
  const actorIds = new Set(actorSnap.auth_visible_world_item_ids || []);
  const observerIds = new Set(observerSnap ? (observerSnap.auth_visible_world_item_ids || []) : []);
  const intersection = [...actorIds].filter(x => observerIds.has(x));

  const summary = {
    phase: PHASE,
    cdp_url: CDP_URL,
    timestamp: new Date().toISOString(),
    actor: actorSnap,
    observer: observerSnap,
    cross_tab: observerSnap ? {
      actor_count: actorIds.size,
      observer_count: observerIds.size,
      intersection_count: intersection.length,
      actor_only: [...actorIds].filter(x => !observerIds.has(x)),
      observer_only: [...observerIds].filter(x => !actorIds.has(x)),
    } : null,
  };
  const outPath = path.join(OUT_DIR, `fl4137_${PHASE}_summary.json`);
  fs.writeFileSync(outPath, JSON.stringify(summary, null, 2));
  console.log(`[fl4137-phase-probe] phase=${PHASE} wrote ${outPath}`);
  console.log(JSON.stringify({
    phase: PHASE,
    actor_block_glyphs: actorSnap.framebuf_block_glyph_count,
    actor_blocks: actorSnap.blocks,
    actor_local_pos: actorSnap.local_pos,
    actor_snapshot_support: actorSnap.snapshot_pos,
    actor_visible_count: actorSnap.auth_visible_world_count,
    actor_pickup_strip: actorSnap.auth_pickup_strip_item_ids,
    actor_place_req_last: { item_id: actorSnap.auth_place_req_last_item_id, reason: actorSnap.auth_place_req_last_reason, sent: actorSnap.auth_place_req_sent },
    actor_input: { inv: actorSnap.input_show_inventory, menu: actorSnap.input_main_menu, talk: actorSnap.input_talk_box },
    observer_visible_count: observerSnap && observerSnap.auth_visible_world_count,
    observer_block_glyphs: observerSnap && observerSnap.framebuf_block_glyph_count,
    cross_tab_intersection: intersection.length,
    cross_tab_actor_only: [...actorIds].filter(x => !observerIds.has(x)),
    cross_tab_observer_only: [...observerIds].filter(x => !actorIds.has(x)),
  }, null, 2));
  await browser.close();
}

main().catch(e => { console.error('FATAL: ' + (e && e.stack ? e.stack : String(e))); process.exit(5); });
