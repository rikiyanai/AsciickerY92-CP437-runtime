import { fileURLToPath } from 'node:url';
// node sign_sweep.mjs RECEIPT.json PORT -- per sign: a preview copy of its XP moved along its receipt normal
// (-2..+3 m from its receipt position), cells drawn when viewed facing -normal (from the street side the normal
// claims) and facing +normal (from the other side). Real sign hidden during the sweep.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [receiptPath, port = '8792'] = process.argv.slice(2); const cols = 160, rows = 70, SC = 5.1320023927966725;
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
const ground = c('ClassicGroundJson', 'string', ['number', 'number']);
const grab = () => { const p = render(cols, rows); return Uint32Array.from(new Uint32Array(M.HEAPU8.buffer, p, cols * rows)); };
const OFFS = [-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3];
for (const s of JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs) {
  const [nx, ny] = s.normal || [0, 1]; vis(s.sprite, 0);
  const res = {};
  for (const side of ['front', 'back']) {
    const sg = side === 'front' ? 1 : -1, heading = (Math.atan2(-nx * sg, -ny * sg) * 180 / Math.PI + 360) % 360, yaw = (360 - heading) % 360;
    cam(s.pos[0], s.pos[1], s.pos[2], yaw, 1, 0); const base = grab();
    res[side] = OFFS.map(off => { const x = s.pos[0] + nx * off * SC, y = s.pos[1] + ny * off * SC;
      const slot = add('/numabe/output.sprites/' + s.sprite, x, y, s.pos[2], 0); cam(s.pos[0], s.pos[1], s.pos[2], yaw, 1, 0); const g = grab(); rem(slot);
      let k = 0; for (let i = 0; i < g.length; i++) if (g[i] !== base[i]) k++; return k; });
  }
  vis(s.sprite, 1);
  const g0 = JSON.parse(ground(s.pos[0], s.pos[1]));
  console.log(JSON.stringify({ label: s.label, total: s.cells[0] * s.cells[1], z_above_ground_m: g0.hit ? +((s.pos[2] - g0.terrain_z) / (SC * 16)).toFixed(2) : null,
    offs: OFFS, front: res.front, back: res.back }));
}
