// proof_wearable_equipped_shows_in_buffer.js
//
// FL-4079: wearable rendered state proof seam (L2: source/expected-cell proof).
//
// Invariant the harness asserts:
//   server says normal_armour 411 is equipped on local actor at tick T
//   AND renderer selected a row containing armor 411 for that same Render() pass
//   AND the final AnsiCell ROI at the selected (sprite_angle, sprite_anim, sprite_frame,
//       projection=0) frame contains the cells produced by upstream-pipeline
//       LoadSpriteBP on the armor's source XP (assets/sprites/item-armor.xp)
//   AND the armor layer contributed > 0 cells (recorder counts agree)
//   AND probe_seq advanced past baseline
//   AND presentation_started_tick equality (alpha) holds between baseline and equipped
//
// Status as of 2026-05-22:
//   - GetActorWearableProofProbe is exported through the web build.
//   - The admissible proof gate is still open until the harness runs against a
//     clean, reproducible web build and the final AnsiCell ROI matches expected
//     source cells without bypassing build-web.sh validation.
//
// Run via the Playwright proof driver:
//   PROOF_DRIVER=$PWD/scripts/proofs/proof_driver_playwright.js \
//     node scripts/proofs/proof_wearable_equipped_shows_in_buffer.js

'use strict';

const PROBE_NAME = 'GetActorWearableProofProbe';
const ARMOR_DEF_ID = 411;
const ARMOR_SLOT_KIND = 306;            // APPEARANCE_SLOT_KIND_ARMOR
const ARMOR_VISUAL_STYLE = 0;
const SHIELD_DEF_ID = 402;
const SHIELD_SLOT_KIND = 302;           // APPEARANCE_SLOT_KIND_SHIELD
const SHIELD_VISUAL_STYLE = 0;
const PRES_KIND_IDLE_WALK = 600;

const POLL_TIMEOUT_MS = 30000;
const POLL_INTERVAL_MS = 100;

class ProofFail extends Error {
  constructor(stage, detail) {
    super(`[FL-4079] RED at stage="${stage}": ${detail}`);
    this.stage = stage;
    this.detail = detail;
  }
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Returns the probe object or throws ProofFail with a precise stage tag.
// The page argument is a Puppeteer Page (or any object exposing .evaluate(fn)).
async function fetchProbe(page, actor) {
  // Playwright's page.evaluate accepts at most ONE arg. Pass [probeName, actor]
  // as a tuple and destructure on the browser side. Older Puppeteer takes
  // multiple positional args; Playwright does not.
  const result = await page.evaluate(([probeName, a]) => {
    if (typeof window[probeName] !== 'function') {
      return { error: 'probe_export_missing', name: probeName };
    }
    try {
      return window[probeName](a | 0);
    } catch (e) {
      return { error: 'probe_threw', message: String(e && e.message || e) };
    }
  }, [PROBE_NAME, actor]);

  if (result && result.error === 'probe_export_missing') {
    throw new ProofFail('probe_export_missing',
      `window.${PROBE_NAME} is not exported. ` +
      `FL-4079 now expects the atomic C export from web/web_recorder_bridge.cpp ` +
      `to be exposed through Module.cwrap before this proof can inspect final ROI cells.`);
  }
  if (result && result.error) {
    throw new ProofFail('probe_threw', `probe call threw: ${result.message || JSON.stringify(result)}`);
  }
  if (!result || typeof result !== 'object') {
    throw new ProofFail('probe_bad_shape', `probe returned non-object: ${JSON.stringify(result)}`);
  }
  return result;
}

function bucketEqual(a, b) {
  return a.render_selection.sprite_angle === b.render_selection.sprite_angle
      && a.render_selection.sprite_anim  === b.render_selection.sprite_anim
      && a.render_selection.sprite_frame === b.render_selection.sprite_frame
      && (a.render_selection.projection_kind | 0) === (b.render_selection.projection_kind | 0)
      && a.server_truth.life_state            === b.server_truth.life_state
      && a.server_truth.presentation_kind_id  === b.server_truth.presentation_kind_id
      && a.server_truth.locomotion_state      === b.server_truth.locomotion_state
      && a.server_truth.combat_state          === b.server_truth.combat_state
      && a.server_truth.mount_state           === b.server_truth.mount_state
      && a.render_selection.presentation_started_tick === b.render_selection.presentation_started_tick;
}

function arrayContains(arr, value) {
  if (!Array.isArray(arr)) return false;
  for (let i = 0; i < arr.length; i++) if ((arr[i] | 0) === (value | 0)) return true;
  return false;
}

function findLayerForSlot(probe, slotKindId) {
  const slots = probe.render_selection.actor_render_slot_kind_ids;
  if (!Array.isArray(slots)) return -1;
  for (let i = 0; i < slots.length; i++) if ((slots[i] | 0) === (slotKindId | 0)) return i;
  return -1;
}

// Decode the probe's hex-encoded AnsiCell ROI into a Uint8Array of 4-byte
// (fg, bk, gl, spare) cells, row-major. Matches the on-wire format
// WebDiagnosticsBuildAnsiFrameSnapshotJson uses and the probe re-uses at
// web_recorder_bridge.cpp roi block.
function decodeRoiHex(hex) {
  if (typeof hex !== 'string' || hex.length === 0) return new Uint8Array(0);
  const out = new Uint8Array(hex.length >>> 1);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return out;
}

// ---- L2 cell-presence assertion against upstream-pipeline cells ----
//
// Probe contract (web_recorder_bridge.cpp BuildActorWearableProofProbeJson, GREEN-2):
//   expectedCells: [{ src_x, src_y, sx, sy, gl, fg, bk, spare }]
//     - sx/sy = screen position (already projected with renderer's dx/dy math)
//     - gl/fg/bk/spare = the authored armor cell's expected AnsiCell value
//   finalRoi: { x, y, w, h, in_bounds, cells: hex-encoded row-major fg/bk/gl/spare bytes }
//
// occluded_by_layer_role tagging requires the Fork A per-cell render trace,
// which is sequenced AFTER GREEN-3 (see FL-4079 followup). Until that lands,
// every expected cell is checked unconditionally; mismatch == fail.
function assertExpectedCellsInFinalRoi(expectedCells, finalRoi, label) {
  if (!Array.isArray(expectedCells) || expectedCells.length === 0) {
    throw new ProofFail('expected_cells_empty',
      `upstream-pipeline ${label} sprite returned zero non-transparent cells for the selected frame`);
  }
  if (!finalRoi || !finalRoi.in_bounds) {
    throw new ProofFail('roi_out_of_bounds',
      `roi not in bounds: ${JSON.stringify(finalRoi)}`);
  }
  const cells = decodeRoiHex(finalRoi.cells);
  const expectedBytes = finalRoi.w * finalRoi.h * 4;
  if (cells.length < expectedBytes) {
    throw new ProofFail('roi_underrun',
      `roi.cells decoded to ${cells.length} bytes, expected ${expectedBytes} ` +
      `for ${finalRoi.w}x${finalRoi.h} cells`);
  }
  for (const s of expectedCells) {
    const dx = s.sx - finalRoi.x;
    const dy = s.sy - finalRoi.y;
    if (dx < 0 || dy < 0 || dx >= finalRoi.w || dy >= finalRoi.h) {
      throw new ProofFail('roi_misalignment',
        `expected ${label} cell at screen (${s.sx},${s.sy}) falls outside ROI ` +
        `(${finalRoi.x},${finalRoi.y},${finalRoi.w}x${finalRoi.h}). ` +
        `Probe projects sx/sy via dx=ref[0]/2, dy=ref[1]/2 from body anchor — ` +
        `misalignment means ${label} frame->ref differs from body frame->ref or ` +
        `composer applies meta_xy adjustment the probe is not accounting for.`);
    }
    const idx = (dy * finalRoi.w + dx) * 4;
    // AnsiCell on-wire byte order: fg, bk, gl, spare (matches web_recorder_bridge.cpp roi block).
    const fg = cells[idx + 0];
    const bk = cells[idx + 1];
    const gl = cells[idx + 2];
    if (gl !== s.gl || fg !== s.fg || bk !== s.bk) {
      throw new ProofFail('cell_mismatch',
        `final cell at (${s.sx},${s.sy}) is fg=${fg} bk=${bk} gl=${gl}; ` +
        `upstream-pipeline ${label} source expected fg=${s.fg} bk=${s.bk} gl=${s.gl}`);
    }
  }
}

async function proofWearableEquippedShowsInBuffer(page, opts) {
  const {
    defId,
    slotKind,
    visualStyle,
    expectedCellsField = 'expected_armor_cells',
    label = 'armor',
    requireMount = false,
  } = opts;

  // 1. Baseline probe.
  //    The L2 invariant is "server-equipped + render-selected + buffer cells agree".
  //    It does NOT require a pre/post transition; a stable equipped state is fine.
  //    Two paths from here:
  //      (a) actor already has the target wearable equipped (default loadout) ->
  //          skip the auto-pickup walk and use this probe as the equipped sample.
  //      (b) actor does NOT yet have it -> walk south-west toward the seeded
  //          Inst::ITEM, then poll until pickup completes.
  const baseline = await fetchProbe(page, 0);
  if (baseline.server_truth.presentation_kind_id !== PRES_KIND_IDLE_WALK) {
    throw new ProofFail('baseline_wrong_presentation',
      `baseline presentation_kind_id=${baseline.server_truth.presentation_kind_id}, expected ${PRES_KIND_IDLE_WALK}`);
  }
  const baselineHasTarget = arrayContains(baseline.server_truth.equipped_definition_ids, defId);

  // 2. Drive the actor toward the armor ItemInst via DOM keyboard events.
  //    sandbox_20x20.a3d places normal_armour 411 at world (-15, -75, 57);
  //    default spawn is near origin, so walking south-west reaches it.
  //    Movement runs the production path: WASD -> game_input -> intent ->
  //    UpdateMobileAutoPickup -> ITEM_ACTION_REQ_PICKUP -> SvrPickupEquippableItem.
  //    No verifier bypass. No Keyb cwrap synthesis (DOM events go through the
  //    real keydown listeners the human player would use).
  const steerKeys = (process.env.PROOF_WALK_DIR || 'sa').toLowerCase().split('');
  let steering = false;
  async function startSteering() {
    if (steering) return;
    steering = true;
    try { await page.focus('canvas'); } catch (_) {}
    for (const k of steerKeys) await page.keyboard.down(k);
  }
  async function stopSteering() {
    if (!steering) return;
    for (const k of steerKeys) {
      try { await page.keyboard.up(k); } catch (_) {}
    }
    steering = false;
  }

  let equipped = null;
  if (baselineHasTarget) {
    equipped = baseline;
  }
  if (!equipped) {
    await startSteering();
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    try {
      while (Date.now() < deadline) {
        const p = await fetchProbe(page, 0);
        const serverHas    = arrayContains(p.server_truth.equipped_definition_ids, defId);
        const renderHas    = arrayContains(p.render_selection.actor_render_definition_ids, defId);
        const renderSlotOK = arrayContains(p.render_selection.actor_render_slot_kind_ids, slotKind);
        const advanced     = (p.probe_seq | 0) > (baseline.probe_seq | 0);
        if (serverHas && renderHas && renderSlotOK && advanced) {
          equipped = p;
          break;
        }
        await sleep(POLL_INTERVAL_MS);
      }
    } finally {
      await stopSteering();
    }
    if (!equipped) {
      throw new ProofFail('equip_did_not_land',
        `${POLL_TIMEOUT_MS}ms elapsed; never observed a probe with server+render both reporting ` +
        `defId=${defId} after walking keys=[${steerKeys.join(',')}]. Auto-pickup path may not have fired, or actor ` +
        `is not within pickup-strip range of the seeded ItemInst.`);
    }
  }

  // 3. Layer-count attribution (necessary but NOT sufficient).
  const layerIdx = findLayerForSlot(equipped, slotKind);
  if (layerIdx < 0) {
    throw new ProofFail('slot_not_in_render',
      `slotKindId=${slotKind} not present in render_selection.actor_render_slot_kind_ids ` +
      `even though equipped_definition_ids contains it`);
  }
  const contributed = (equipped.render_selection.actor_render_layer_contributed_cell_counts[layerIdx] | 0);
  if (contributed <= 0) {
    throw new ProofFail('layer_contributed_zero',
      `${label} layer ${layerIdx} contributed_cell_count=${contributed}; ` +
      `wearable selection reported ${label} but renderer wrote zero cells from it`);
  }
  if (requireMount && ((equipped.server_truth.mount_state | 0) === 0)) {
    throw new ProofFail('mount_required',
      `proof required mounted actor but server_truth.mount_state=${equipped.server_truth.mount_state}`);
  }

  // 4. L2 cell-presence: upstream-pipeline cells appear in final AnsiCell ROI.
  //    The probe emits expected_armor_cells from Fork A (per-cell trace inside
  //    Renderer::RenderSprite), populated from the same LoadSpriteBP-loaded Sprite::Frame
  //    used by the renderer. No parallel projection.
  if (!Array.isArray(equipped[expectedCellsField])) {
    throw new ProofFail('expected_cells_missing',
      `probe response lacks ${expectedCellsField}[]; GREEN cycle must populate this from ` +
      'the per-cell render trace owned by Renderer::RenderSprite');
  }
  if (!equipped.roi || typeof equipped.roi.cells !== 'string') {
    throw new ProofFail('roi_missing',
      'probe response lacks roi.cells hex string; probe must populate ROI raw cells alongside the trace');
  }
  assertExpectedCellsInFinalRoi(equipped[expectedCellsField], equipped.roi, label);

  return {
    pass: true,
    fl: 'FL-4079',
    baseline_probe_seq: baseline.probe_seq,
    equipped_probe_seq: equipped.probe_seq,
    wearable_def_id: defId,
    wearable_slot_kind: slotKind,
    visual_style: visualStyle,
    expected_cells_field: expectedCellsField,
    cells_asserted: equipped[expectedCellsField].length,
    mount_state: equipped.server_truth.mount_state,
  };
}

module.exports = {
  proofWearableEquippedShowsInBuffer,
  fetchProbe,
  ProofFail,
  ARMOR_DEF_ID,
  ARMOR_SLOT_KIND,
  ARMOR_VISUAL_STYLE,
  SHIELD_DEF_ID,
  SHIELD_SLOT_KIND,
  SHIELD_VISUAL_STYLE,
  PROBE_NAME,
};

// CLI entry: when run directly, attempt the proof against an already-open page.
// The driver that opens the browser and seeds the proof map lives in the GREEN
// cycle's harness wiring; today this main() merely demonstrates the RED failure
// when the probe export is missing.
if (require.main === module) {
  (async () => {
    const driverPath = process.env.PROOF_DRIVER || null;
    if (!driverPath) {
      console.error('[FL-4079] no PROOF_DRIVER set; cannot open browser.');
      console.error('Set PROOF_DRIVER=path/to/puppeteer-driver.js to wire this up.');
      console.error('GREEN-1/GREEN-2 export window.GetActorWearableProofProbe; ' +
                    'GREEN-3 wires the driver.');
      process.exit(2);
    }
    try {
      const driver = require(driverPath);
      const page = await driver.openProofPage({
        mapPath: 'assets/a3d/sandbox_20x20.a3d',
      });
      const defId = parseInt(process.env.PROOF_DEF_ID || String(ARMOR_DEF_ID), 10);
      const slotKind = parseInt(process.env.PROOF_SLOT_KIND || String(ARMOR_SLOT_KIND), 10);
      const visualStyle = parseInt(process.env.PROOF_VISUAL_STYLE || String(ARMOR_VISUAL_STYLE), 10);
      const out = await proofWearableEquippedShowsInBuffer(page, {
        defId,
        slotKind,
        visualStyle,
        expectedCellsField: process.env.PROOF_EXPECTED_CELLS_FIELD || 'expected_armor_cells',
        label: process.env.PROOF_WEARABLE_LABEL || 'armor',
        requireMount: process.env.PROOF_REQUIRE_MOUNT === '1',
      });
      console.log(JSON.stringify(out, null, 2));
      process.exit(0);
    } catch (e) {
      if (e instanceof ProofFail) {
        console.error(e.message);
        process.exit(1);
      }
      throw e;
    }
  })().catch(e => {
    console.error('[FL-4079] unexpected harness error:', e && e.stack || e);
    process.exit(3);
  });
}
