'use strict';

// FL-4137 visibility regression — operator-canonical PASS gate.
//
// PASS = block sprite pixels appear inside the projected screen rectangle of
// each placed block visible in the recorder's auth_item_sample.
//
// This extends the FL-4079 expected-cells / ROI oracle pattern (which the
// armor/shield proof already uses) to placed blocks. The recorder now publishes
// per-block screen projection (screen_top_col/row, screen_bottom_col/row,
// screen_valid) computed via the same ProjectCoords the renderer uses, so this
// test does not re-implement projection. For each visible block it samples the
// framebuffer cells inside that projected rect and asserts a block-body cell
// (glyph 219, palette pair fg=145 bk=102 from the procedurally generated XPs)
// appears in the TOPMOST row of that rect — i.e. visible_top_row matches the
// world_top_row the server published.
//
// "Total glyph-219 count >= N" is NOT a PASS by itself — that's the coarse
// check this test had before. Position equality per variant is the contract
// per operator (2026-05-28: "their top has to match the world fucking top").
//
// What this test asserts (in order):
//   1. All 3 expected definitions (420 / 421 / 422) surface in
//      auth_visible_world_item_ids — server saw all 3 variants.
//   2. Their auth_item_sample rows carry screen_valid=1 — projection succeeded.
//   3. For each block: a glyph-219 cell exists inside its projected rect
//      [screen_top_col..screen_bottom_col] x [screen_top_row..screen_bottom_row].
//   4. For each block: the TOPMOST glyph-219 cell row inside that rect is at
//      screen_top_row +- 1 cell. That is the "visible top == world top"
//      equality.

const fs = require('fs');
const path = require('path');
const driver = require('./proof_driver_playwright.js');

const BLOCK_GLYPH = 219;
const BLOCK_FG = 145;  // palette index for (170,170,170) per current renderer mapping
const BLOCK_BK = 102;  // palette index for (85,85,85)
const EXPECTED_DEFINITION_IDS = [420, 421, 422];
const TOP_ROW_TOLERANCE = 1;

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function recorderJson(page) {
    return page.evaluate(() => {
        if (!window.__akRecorderStateJson && window.Module && window.Module.cwrap) {
            window.__akRecorderStateJson = window.Module.cwrap('RecorderStateJson', 'string', []);
        }
        if (!window.__akRecorderStateJson) return { error: 'RecorderStateJson unavailable' };
        try { return JSON.parse(window.__akRecorderStateJson()); }
        catch (e) { return { error: 'RecorderStateJson parse: ' + String(e) }; }
    });
}

async function captureFrame(page) {
    return page.evaluate(() => {
        if (!window.__akCppAnsiFrameSnapshotJson && window.Module && window.Module.cwrap) {
            window.__akCppAnsiFrameSnapshotJson =
                window.Module.cwrap('GetCppAnsiFrameSnapshotJson', 'string', []);
        }
        if (!window.__akCppAnsiFrameSnapshotJson) {
            return { ok: false, error: 'GetCppAnsiFrameSnapshotJson cwrap unavailable' };
        }
        try { return { ok: true, frame: JSON.parse(window.__akCppAnsiFrameSnapshotJson()) }; }
        catch (e) { return { ok: false, error: 'snapshot parse: ' + String(e) }; }
    });
}

function decodeCells(frame) {
    const cells = [];
    if (typeof frame.raw_hex !== 'string' || frame.raw_hex.length % 8 !== 0) return cells;
    for (let i = 0; i < frame.raw_hex.length; i += 8) {
        cells.push({
            fg: parseInt(frame.raw_hex.slice(i, i + 2), 16),
            bk: parseInt(frame.raw_hex.slice(i + 2, i + 4), 16),
            gl: parseInt(frame.raw_hex.slice(i + 4, i + 6), 16),
        });
    }
    return cells;
}

function cellAt(cells, W, x, y) {
    if (x < 0 || y < 0 || x >= W || y >= cells.length / W) return null;
    return cells[y * W + x];
}

// Returns { topRow, count, samples } where topRow is the topmost row inside
// the projected rect that contains a block-body cell, count is the total
// block-body cells in the rect, samples is up to 8 (x,y,gl,fg,bk) tuples.
function inspectBlockRect(cells, W, rect) {
    const x0 = Math.min(rect.screen_top_col, rect.screen_bottom_col);
    const x1 = Math.max(rect.screen_top_col, rect.screen_bottom_col);
    // ProjectCoords y is row index — in this engine the top of the sprite has
    // the SMALLER y value (screen_top_row < screen_bottom_row in cell space).
    const y0 = Math.min(rect.screen_top_row, rect.screen_bottom_row);
    const y1 = Math.max(rect.screen_top_row, rect.screen_bottom_row);
    // Pad the rect by 1 cell in each direction so we don't fail on sub-cell
    // rounding between ProjectCoords integer truncation and sprite render.
    const PAD = 1;
    let topRow = null;
    let count = 0;
    const samples = [];
    for (let y = y0 - PAD; y <= y1 + PAD; y++) {
        for (let x = x0 - PAD; x <= x1 + PAD; x++) {
            const c = cellAt(cells, W, x, y);
            if (!c) continue;
            if (c.gl === BLOCK_GLYPH && c.fg === BLOCK_FG && c.bk === BLOCK_BK) {
                if (topRow === null || y < topRow) topRow = y;
                count++;
                if (samples.length < 8) samples.push({ x, y, gl: c.gl, fg: c.fg, bk: c.bk });
            }
        }
    }
    return { topRow, count, samples, rect: { x0, x1, y0, y1, pad: PAD } };
}

async function main() {
    const page = await driver.openProofPage({
        mapPath: process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d',
        player: 'proof_block_visibility_regression',
    });

    // Settle the framebuffer after join so the seeded blocks have rendered.
    await sleep(2000);

    const rec = await recorderJson(page);
    if (rec.error) {
        console.error('FAIL: recorder json: ' + rec.error);
        await driver.cleanup();
        process.exit(1);
    }

    // The recorder's auth_item_sample carries our new screen_* fields per block.
    const samples = Array.isArray(rec.auth_item_sample) ? rec.auth_item_sample : [];
    const placedBlocks = samples.filter(s =>
        EXPECTED_DEFINITION_IDS.indexOf(s.item_definition_id) >= 0 &&
        (s.state_flags & 1) !== 0  // PLACED bit
    );
    console.log('[fl4137-vis-regression] auth_item_sample placed-block rows: ' +
                JSON.stringify(placedBlocks.map(b => ({
                    id: b.id,
                    def: b.item_definition_id,
                    screen_valid: b.screen_valid,
                    screen_top: [b.screen_top_col, b.screen_top_row],
                    screen_bot: [b.screen_bottom_col, b.screen_bottom_row],
                    world_pos: [b.x, b.y, b.z],
                    visual_top_z: b.visual_top_z,
                }))));

    // Gate 1: all 3 expected definitions present.
    const seenDefs = new Set(placedBlocks.map(b => b.item_definition_id));
    const missingDefs = EXPECTED_DEFINITION_IDS.filter(d => !seenDefs.has(d));
    if (missingDefs.length > 0) {
        console.error('FAIL: missing definition_ids in auth_item_sample placed blocks: ' +
                      JSON.stringify(missingDefs));
        await driver.cleanup();
        process.exit(1);
    }

    // Gate 2: every placed block has screen_valid=1.
    const projFails = placedBlocks.filter(b => !b.screen_valid);
    if (projFails.length > 0) {
        console.error('FAIL: ' + projFails.length + ' placed block(s) have screen_valid=0 — ' +
                      'ProjectCoords failed for them (off-screen or behind camera). ids=' +
                      JSON.stringify(projFails.map(b => b.id)));
        await driver.cleanup();
        process.exit(1);
    }

    const snap = await captureFrame(page);
    if (!snap.ok) {
        console.error('FAIL: framebuffer snapshot: ' + snap.error);
        await driver.cleanup();
        process.exit(1);
    }
    const frame = snap.frame;
    if (!frame.valid || !frame.raw_hex) {
        console.error('FAIL: framebuffer invalid: ' + JSON.stringify(frame).slice(0, 256));
        await driver.cleanup();
        process.exit(1);
    }
    const cells = decodeCells(frame);
    const W = frame.width;

    try {
        const outPath = path.join(__dirname, '..', '..', '.run',
            'fl4137_block_visibility_regression_renderbuf.json');
        fs.writeFileSync(outPath, JSON.stringify(frame));
        console.log('[fl4137-vis-regression] renderbuf saved to ' + outPath);
    } catch (_) {}

    // Gates 3 & 4: per-block, block-body cell exists inside projected rect AND
    // its topmost row matches screen_top_row within tolerance.
    const failures = [];
    const passes = [];
    for (const b of placedBlocks) {
        const info = inspectBlockRect(cells, W, b);
        if (info.count === 0) {
            failures.push({
                id: b.id,
                def: b.item_definition_id,
                reason: 'no_block_glyph_in_projected_rect',
                rect: info.rect,
                screen_top_row: b.screen_top_row,
                screen_bottom_row: b.screen_bottom_row,
                world_pos: [b.x, b.y, b.z],
                visual_top_z: b.visual_top_z,
            });
            continue;
        }
        const delta = info.topRow - b.screen_top_row;
        if (Math.abs(delta) > TOP_ROW_TOLERANCE) {
            failures.push({
                id: b.id,
                def: b.item_definition_id,
                reason: 'top_row_mismatch',
                actual_top_row: info.topRow,
                expected_top_row: b.screen_top_row,
                delta: delta,
                tolerance: TOP_ROW_TOLERANCE,
                glyph_cells_in_rect: info.count,
                samples: info.samples,
            });
        } else {
            passes.push({
                id: b.id,
                def: b.item_definition_id,
                top_row_delta: delta,
                glyph_cells_in_rect: info.count,
                screen_top_row: b.screen_top_row,
                actual_top_row: info.topRow,
            });
        }
    }

    console.log('[fl4137-vis-regression] per-block verdicts:');
    console.log('  PASS=' + passes.length + ' FAIL=' + failures.length);
    for (const p of passes) console.log('  PASS ' + JSON.stringify(p));
    for (const f of failures) console.log('  FAIL ' + JSON.stringify(f));

    if (failures.length > 0) {
        console.error('FAIL: ' + failures.length + '/' + placedBlocks.length +
                      ' placed block(s) failed visible-top-vs-world-top contract.');
        console.error('       Operator rule: visible top must equal world support_top_z.');
        await driver.cleanup();
        process.exit(1);
    }

    console.log('PASS: ' + passes.length + ' placed block(s) — visible-top-vs-world-top ' +
                'contract holds for all definitions ' + JSON.stringify(EXPECTED_DEFINITION_IDS) +
                ' within tolerance ' + TOP_ROW_TOLERANCE + ' cell(s).');
    await driver.cleanup();
    process.exit(0);
}

main().catch(async (err) => {
    console.error('FAIL: uncaught: ' + (err && err.stack ? err.stack : String(err)));
    try { await driver.cleanup(); } catch (_) {}
    process.exit(1);
});
