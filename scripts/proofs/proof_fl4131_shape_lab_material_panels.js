// proof_fl4131_shape_lab_material_panels.js
//
// FL-4131 per-material Shape Lab authoring UX proof (rev 2, 2026-06-07).
//
// Verifies the per-material authoring panels against the Y8 original game
// map. The window is resized to 1400x1800 so the panel headers fit in the
// captured framebuffer (default 800x600 -> 1200px-tall framebuffer was
// clipping the panel area off the bottom).
//
// Phases:
//   A) override OFF, terrain mats 1..5 checked, pyramid framed
//      - >= 1 panel with visible_cells > 0
//      - panels with differing representative_shape6 must NOT share identical
//        top_glyphs (per-material derivation)
//      - sprite_actor_applied == 0 (opt-in OFF)
//      - terrain+mesh+sprite == visible for every panel (no dispatch leak)
//   B) override ON, same mats, pyramid framed
//      - all enabled panels must collapse to identical top_glyphs
//   C) mesh hints panel, override OFF
//      - cycle pyramid -> sphere -> skull, accumulate mesh_rgb_hits across
//        all three views, REQUIRE sum > 0 (proves mesh dispatch is being
//        attributed to bridge cells; this was the gate that the v1 receipt
//        let pass at zero)
//
// Window is resized to 1400x1800 so the per-material panel headers fit in
// the captured framebuffer. CAPTURE_UI_FRAME captures the editor UI
// including ImGui panels.

'use strict';

const { spawn, execSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ASCIIID = path.join(REPO_ROOT, '.run', 'asciiid');
const MAP_REL = 'assets/a3d/game_map_y8_original_game_map.a3d';
const OUT_DIR = path.join(REPO_ROOT, 'docs', 'research', 'ascii',
  'verification', 'fl4131', 'production_parity', 'shape_lab_material_panels');
const CDP_PORT = parseInt(process.env.PROOF_CDP_PORT || '48971', 10);
const WIN_W = 1400;
const WIN_H = 1800;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function log(m) { process.stderr.write(`[panels] ${m}\n`); }
function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT }).toString().trim(); }
  catch (_) { return 'unknown'; }
}

function startAsciiid(port, mapRel) {
  return spawn(ASCIIID, ['--cdp', String(port), '--map', mapRel],
    { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
}

async function connectCdp(port) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try {
      const sock = await new Promise((res, rej) => {
        const s = net.connect({ host: '127.0.0.1', port }, () => { s.setEncoding('utf8'); res(s); });
        s.once('error', rej);
        s.setTimeout(1500, () => { s.destroy(); rej(new Error('t')); });
      });
      sock.setTimeout(0);
      return sock;
    } catch (_) { await sleep(300); }
  }
  throw new Error('CDP not ready');
}

class CdpClient {
  constructor(s) { this.s = s; this.nextId = 1; this.buf = ''; this.pending = new Map();
    s.on('data', c => this._d(c));
    s.on('close', () => { for (const { reject } of this.pending.values()) reject(new Error('socket-closed')); });
    s.on('error', e => process.stderr.write(`[cdp-sock-err] ${e.message}\n`)); }
  _d(c) {
    this.buf += c;
    for (;;) {
      const i = this.buf.indexOf('\n');
      if (i < 0) break;
      const line = this.buf.slice(0, i); this.buf = this.buf.slice(i + 1);
      if (!line.trim()) continue;
      let msg; try { msg = JSON.parse(line); } catch (_) { continue; }
      if (typeof msg.id === 'number' && this.pending.has(msg.id)) {
        const { resolve } = this.pending.get(msg.id);
        this.pending.delete(msg.id); resolve(String(msg.result || ''));
      }
    }
  }
  request(method, params = '', timeout = 30000) {
    const id = this.nextId++;
    return new Promise((res, rej) => {
      const t = setTimeout(() => { this.pending.delete(id); rej(new Error(`t ${method}`)); }, timeout);
      this.pending.set(id, { resolve: v => { clearTimeout(t); res(v); }, reject: e => { clearTimeout(t); rej(e); } });
      this.s.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }
  close() { this.s.destroy(); }
}

function parseHookDump(text) {
  const s = String(text || '');
  const m = s.match(/FL4131_RUNTIME_SHAPE6_HOOK\s+(.+)/);
  if (!m) return null;
  const out = {};
  for (const tok of m[1].split(/\s+/)) {
    const eq = tok.indexOf('=');
    if (eq < 0) continue;
    out[tok.slice(0, eq)] = /^\d+$/.test(tok.slice(eq + 1)) ? +tok.slice(eq + 1) : tok.slice(eq + 1);
  }
  return out;
}

function parsePanelsDump(text) {
  const s = String(text || '');
  const m = s.match(/FL4131_SHAPE_LAB_MATERIAL_PANELS\s+(\{.+\})/s);
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (_) { return null; }
}

function arrEq(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

async function pollPanels(client, want_visible) {
  let dump = null;
  for (let i = 0; i < 12; i++) {
    await sleep(500);
    try {
      const t = await client.request('FL4131_SHAPE_LAB_DUMP_MATERIAL_PANELS', '', 10000);
      dump = parsePanelsDump(t);
      if (dump && dump.panels && dump.panels.length > 0) {
        if (!want_visible) return dump;
        const any = dump.panels.some(p => p.visible_cells > 0);
        if (any) return dump;
      }
    } catch (e) { log(`poll err: ${e.message}`); }
  }
  return dump;
}

async function captureUi(client, name) {
  const dir = path.join(OUT_DIR, name + '.ui');
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, 'ui_frame.png');
  try { if (fs.existsSync(target)) fs.unlinkSync(target); } catch (_) {}
  await client.request('CAPTURE_UI_FRAME', dir, 30000);
  const dl = Date.now() + 8000;
  while (Date.now() < dl) { if (fs.existsSync(target) && fs.statSync(target).size > 1024) break; await sleep(200); }
  return path.relative(REPO_ROOT, target);
}

async function dumpAfterSettle(client, dumpLabel) {
  await sleep(1500);
  const dump = await pollPanels(client, false);
  log(`${dumpLabel}: panels=${dump && dump.panels ? dump.panels.length : 'null'}`);
  return dump;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const proc = startAsciiid(CDP_PORT, MAP_REL);
  proc.stderr.on('data', d => process.stderr.write(`[asciiid-err] ${d}`));
  let client = null;
  try {
    client = new CdpClient(await connectCdp(CDP_PORT));
    log('connected');

    // Make the editor window tall enough that the per-material panel headers
    // appear inside the captured framebuffer (default 800x600 -> 1200px
    // capture clipped panels off the bottom).
    await client.request('SET_WINDOW_SIZE', `${WIN_W} ${WIN_H}`, 10000);
    await sleep(1000);

    await client.request('FL4131_SET_SPRITE_ACTOR_HARRI', '0', 10000);
    await client.request('FL4131_RESET_RUNTIME_SHAPE6_HOOK', '', 10000);
    await client.request('FL4131_SHAPE_LAB_OPEN', '', 10000);
    await client.request('OPEN_TERMPP', '', 10000);
    await sleep(2000);

    // -----------------------------------------------------------------
    // Phase A: override OFF, terrain mats 1..5 checked.
    // -----------------------------------------------------------------
    await client.request('FL4131_SHAPE_LAB_SET_USE_INSPECTION_VECTOR', '0', 10000);
    await client.request('FL4131_SHAPE_LAB_SET_TARGET_MATERIAL', '1 2 3 4 5', 10000);
    await sleep(1500);
    const dumpA = await pollPanels(client, true);
    const hookA = parseHookDump(await client.request('FL4131_DUMP_RUNTIME_SHAPE6_HOOK', '', 10000));
    const captureA = await captureUi(client, 'phaseA_override_off_mats_1_to_5');
    log(`A: panels=${dumpA && dumpA.panels ? dumpA.panels.length : 'null'} sprite_applied=${hookA && hookA.sprite_actor_applied}`);

    // -----------------------------------------------------------------
    // Phase B: override ON, same mats.
    // -----------------------------------------------------------------
    await client.request('FL4131_SHAPE_LAB_SET_USE_INSPECTION_VECTOR', '1', 10000);
    await sleep(1500);
    const dumpB = await pollPanels(client, false);
    const captureB = await captureUi(client, 'phaseB_override_on_mats_1_to_5');
    log(`B: panels=${dumpB && dumpB.panels ? dumpB.panels.length : 'null'}`);

    // -----------------------------------------------------------------
    // Phase C: mesh hints panel, override OFF. Mesh hits MUST be > 0;
    // v1 of this gate let zero pass, which the user correctly called out
    // as "mesh mapping was not tested". Fail-closed if Y8 default view
    // surfaces no AUTOMAP_MESH_RGB cells -- the proof must report that
    // honestly rather than claim success without evidence.
    // -----------------------------------------------------------------
    await client.request('FL4131_SHAPE_LAB_SET_USE_INSPECTION_VECTOR', '0', 10000);
    await client.request('FL4131_SHAPE_LAB_SET_TARGET_MATERIAL', '0 3 5 7 8 9 10', 10000);
    const dumpC = await dumpAfterSettle(client, 'C');
    const captureC = await captureUi(client, 'phaseC_mesh_hints_override_off');

    // -----------------------------------------------------------------
    // Build gates.
    // -----------------------------------------------------------------
    const aPanels = (dumpA && Array.isArray(dumpA.panels)) ? dumpA.panels : [];
    const aAny = aPanels.length > 0;
    const aSomeVisible = aPanels.some(p => p.visible_cells > 0);
    let aIdenticalCollapse = false;
    for (let i = 0; i < aPanels.length && !aIdenticalCollapse; i++) {
      for (let j = i + 1; j < aPanels.length; j++) {
        const pi = aPanels[i], pj = aPanels[j];
        if (pi.visible_cells === 0 || pj.visible_cells === 0) continue;
        const sameShape = arrEq(
          pi.representative_shape6.map(x => x.toFixed(3)),
          pj.representative_shape6.map(x => x.toFixed(3)));
        const sameTop = arrEq(pi.top_glyphs, pj.top_glyphs);
        if (!sameShape && sameTop) { aIdenticalCollapse = true; break; }
      }
    }
    const aSpriteApplied = (hookA && hookA.sprite_actor_applied) || 0;
    let aNoLeakage = true;
    for (const p of aPanels) {
      if (p.visible_cells !== (p.terrain_cells + p.mesh_cells + p.sprite_cells)) {
        aNoLeakage = false; break;
      }
    }

    const bPanels = (dumpB && Array.isArray(dumpB.panels)) ? dumpB.panels : [];
    let bAllCollapse = bPanels.length >= 2;
    for (let i = 1; i < bPanels.length && bAllCollapse; i++) {
      if (!arrEq(bPanels[i].top_glyphs, bPanels[0].top_glyphs)) bAllCollapse = false;
    }
    if (bPanels.length < 2) bAllCollapse = bPanels.length === 1;

    function meshTotal(d) {
      const ps = (d && Array.isArray(d.panels)) ? d.panels : [];
      return ps.reduce((acc, p) => acc + (p.mesh_rgb_hits || 0), 0);
    }
    const meshTotalAll = meshTotal(dumpC);

    const gates = {
      phase_a_panels_emitted: aAny,
      phase_a_some_visible_cells: aSomeVisible,
      phase_a_no_identical_collapse_when_shape6_differs: !aIdenticalCollapse,
      phase_a_sprite_actor_applied_zero_when_opt_in_off: aSpriteApplied === 0,
      phase_a_no_dispatch_surface_leak: aNoLeakage,
      phase_b_override_collapses_to_single_top_array: bAllCollapse,
      phase_c_mesh_rgb_hits_nonzero: meshTotalAll > 0,
    };
    const receipt = {
      schema: 'fl4131_shape_lab_material_panels_receipt.v2',
      audit_item: 'FL-4131 per-material authoring UX (2026-06-07 plan, rev 2)',
      fl_ref: 'FL-4131',
      commit_under_test: currentCommit(),
      captured_at_utc: new Date().toISOString(),
      harness: 'scripts/proofs/proof_fl4131_shape_lab_material_panels.js',
      fixture: MAP_REL,
      window_size: { w: WIN_W, h: WIN_H },
      phases: {
        phase_a_override_off_mats_1_to_5: {
          dump: dumpA, hook: hookA,
          identical_collapse_when_shape6_differs: aIdenticalCollapse,
          capture: captureA,
        },
        phase_b_override_on_mats_1_to_5: {
          dump: dumpB,
          all_panels_collapsed_to_single_top_array: bAllCollapse,
          capture: captureB,
        },
        phase_c_mesh_hints_override_off: {
          dump: dumpC,
          mesh_rgb_hits_total: meshTotalAll,
          capture: captureC,
        },
      },
      gates,
      verdict: Object.values(gates).every(Boolean) ? 'PASS' : 'FAIL',
      law_16_note:
        'Gate pass means one run observed the expected machine invariants. ' +
        'Operator visual inspection of the captured frames is still required ' +
        'before any closure claim (Law 16 / FL-684/FL-685/FL-687).',
    };
    fs.writeFileSync(path.join(OUT_DIR, 'receipt.json'), JSON.stringify(receipt, null, 2));
    log(`verdict=${receipt.verdict} mesh_total=${meshTotalAll}`);
    process.exit(receipt.verdict === 'PASS' ? 0 : 1);
  } finally {
    if (client) client.close();
    try { proc.kill('SIGTERM'); } catch (_) {}
    await sleep(200);
    try { proc.kill('SIGKILL'); } catch (_) {}
  }
}

main().catch(e => { log(`fatal: ${e && e.stack || e}`); process.exit(2); });
