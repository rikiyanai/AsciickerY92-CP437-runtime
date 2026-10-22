import { fileURLToPath } from 'node:url';
// node sign_vis.mjs RECEIPT.json -- for every sign sprite in a build's sign_sprites_receipt.json: camera facing it
// (zoom 1, looking along -normal), render with the sign shown and hidden (ClassicSetSpriteVisible), count the
// rows / cells the sign actually changes, versus its own cell grid.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [receiptPath, port = '8792'] = process.argv.slice(2); const cols = 160, rows = 70;
const root = fileURLToPath(new URL('../../../../', import.meta.url)); process.chdir(path.join(root, '.web-classic'));
globalThis.require = createRequire(import.meta.url); globalThis.__dirname = path.join(root, '.web-classic'); globalThis.__filename = path.join(root, '.web-classic/index.js');
const ready = new Promise(r => { globalThis.Module = { onRuntimeInitialized: () => r(globalThis.Module), print: () => {}, printErr: () => {} }; });
vm.runInThisContext(fs.readFileSync(path.join(root, '.web-classic/index.js'), 'utf8'), { filename: 'index.js' });
const M = await ready; const c = (n, r, a) => M.cwrap(n, r, a);
const buf = new Uint8Array(await (await fetch(`http://127.0.0.1:${port}/api/map_bundle`)).arrayBuffer()), dv = new DataView(buf.buffer);
let o = 8; const n = dv.getUint32(4, true), dec = new TextDecoder(), made = new Set(); M.FS.mkdirTree('/numabe/meshes');
for (let i = 0; i < n; i++) { const L = dv.getUint16(o, true); o += 2; const name = dec.decode(buf.subarray(o, o + L)); o += L; const sz = dv.getUint32(o, true); o += 4;
  const dir = name.includes('/') ? '/numabe/' + name.slice(0, name.lastIndexOf('/')) : null; if (dir && !made.has(dir)) { M.FS.mkdirTree(dir); made.add(dir); }
  M.FS.writeFile('/numabe/' + name, buf.subarray(o, o + sz)); o += sz; }
c('ClassicSetLoadOptions', null, ['number'])(1);
c('ClassicLoadMap', 'number', ['string', 'string'])('/numabe/output.a3d', '/numabe/meshes');
c('ClassicSetMinimap', null, ['number'])(0);
const cam = c('ClassicSetCamera', null, ['number', 'number', 'number', 'number', 'number', 'number']), render = c('ClassicRender', 'number', ['number', 'number']);
const vis = c('ClassicSetSpriteVisible', 'number', ['string', 'number']);
const grab = () => { const p = render(cols, rows); return Uint32Array.from(new Uint32Array(M.HEAPU8.buffer, p, cols * rows)); };
const signs = JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs;
const out = []; let serial = 0;
for (const s of signs) {
  const [nx, ny] = s.normal || [0, 1], heading = (Math.atan2(-nx, -ny) * 180 / Math.PI + 360) % 360, yaw = (360 - heading) % 360;
  cam(s.pos[0], s.pos[1], s.pos[2], yaw, 1, 0);
  const shown = grab(); const h = vis(s.sprite, 0); const hidden = grab(); vis(s.sprite, 1);
  const rs = new Set(); let cells = 0;
  for (let k = 0; k < shown.length; k++) if (shown[k] !== hidden[k]) { rs.add(Math.floor(k / cols)); cells++; }
  // positive control: the same XP as a preview sprite 1 m further out (anchors >= 0 m from the face draw, sweep)
  const sc = 5.1320023927966725, add = c('ClassicPreviewSpriteAdd', 'number', ['string', 'number', 'number', 'number', 'number']);
  const slot = add('/numabe/output.sprites/' + s.sprite, s.pos[0] + nx * 1.0 * sc, s.pos[1] + ny * 1.0 * sc, s.pos[2], 0);
  // hide-path proof on the control (wasm names previews facade_preview_<slot>_<serial>, serial = adds so far)
  const withCtl = grab(); const ch = vis(`facade_preview_${slot}_${++serial}`, 0); const hiddenCtl = grab(); vis(`facade_preview_${slot}_${serial}`, 1);
  let hid = 0; for (let k = 0; k < withCtl.length; k++) if (withCtl[k] !== hiddenCtl[k]) hid++;
  c('ClassicPreviewSpriteRemove', null, ['number'])(slot); const noCtl = grab(); s.hide_proof = hid;
  let ctl = 0; for (let k = 0; k < withCtl.length; k++) if (withCtl[k] !== noCtl[k]) ctl++;
  out.push({ control_cells_toggled: ctl, control_found: ch, control_cells_hidden_by_hide_call: s.hide_proof, sprite: s.sprite, label: s.label, kind: s.kind, grid: s.cells, found: h, rows_drawn: rs.size, cells_drawn: cells,
    cells_total: s.cells ? s.cells[0] * s.cells[1] : null, height_m: s.wall_snap?.height_above_street_m, moved_m: s.wall_snap?.moved_along_normal_m });
}
for (const r of out) console.log(JSON.stringify(r));
