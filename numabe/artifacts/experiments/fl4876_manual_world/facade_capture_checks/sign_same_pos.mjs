import { fileURLToPath } from 'node:url';
// node sign_same_pos.mjs RECEIPT.json PORT -- per sign and heading: cells drawn by the map's own sign instance
// (show vs hide) versus a detached preview copy of the same .xp at the SAME position (real hidden), same camera.
// Separates "where the sign stands" from "how the map instance is drawn".
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
const add = c('ClassicPreviewSpriteAdd', 'number', ['string', 'number', 'number', 'number', 'number']), rem = c('ClassicPreviewSpriteRemove', null, ['number']);
const grab = () => { const p = render(cols, rows); return Uint32Array.from(new Uint32Array(M.HEAPU8.buffer, p, cols * rows)); };
const diff = (a, b) => { let k = 0; for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) k++; return k; };
const ALL = JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs;
for (const s of ALL.slice(0, 6)) {
  const real = [], copy = [], same = [];
  if (process.env.ISOLATE) for (const o of ALL) if (o.sprite !== s.sprite) vis(o.sprite, 0); // other signs out of the way
  for (let h = 0; h < 360; h += 45) {
    cam(s.pos[0], s.pos[1], s.pos[2], (360 - h) % 360, 1, 0);
    const shown = grab(); vis(s.sprite, 0); const hidden = grab();
    const slot = add('/numabe/output.sprites/' + s.sprite, s.pos[0], s.pos[1], s.pos[2], 0); const withCopy = grab(); rem(slot); vis(s.sprite, 1);
    real.push(diff(shown, hidden)); copy.push(diff(withCopy, hidden)); same.push(diff(shown, withCopy));
    if (process.env.DUMP && s.label === process.env.DUMP && h === 45)
      for (const [nm, fr] of [['real', shown], ['copy', withCopy], ['none', hidden]])
        fs.writeFileSync(path.join(process.env.DUMP_DIR, `same_${nm}.raw`), Buffer.from(fr.buffer));
  }
  if (process.env.ISOLATE) for (const o of ALL) if (o.sprite !== s.sprite) vis(o.sprite, 1);
  console.log(JSON.stringify({ label: s.label, isolated: !!process.env.ISOLATE, real, copy_same_pos: copy, frames_differ_cells: same }));
}
