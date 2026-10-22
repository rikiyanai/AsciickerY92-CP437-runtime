import { fileURLToPath } from 'node:url';
// node sign_pick.mjs RECEIPT.json PORT -- per sign: camera facing it (zoom 1), project the sign anchor and a point
// 1 m above it, and pick (ClassicPickJson) the geometry the renderer shows at those cells: terrain or which mesh.
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
const proj = c('ClassicProjectJson', 'string', ['number', 'number', 'number']), pick = c('ClassicPickJson', 'string', ['number', 'number']);
for (const s of JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs) {
  const [nx, ny] = s.normal || [0, 1], heading = (Math.atan2(-nx, -ny) * 180 / Math.PI + 360) % 360, yaw = (360 - heading) % 360;
  cam(s.pos[0], s.pos[1], s.pos[2], yaw, 1, 0); render(cols, rows);
  const at = dz => { const p = JSON.parse(proj(s.pos[0], s.pos[1], s.pos[2] + dz)); if (!p.visible) return null;
    const h = JSON.parse(pick(p.cx, p.cy)); return h.hit ? { what: h.inst ? h.inst.mesh || h.inst.name : 'terrain', z_m: +((h.z - s.pos[2]) / (SC * 16)).toFixed(2),
      dist_m: +(Math.hypot(h.x - s.pos[0], h.y - s.pos[1]) / SC).toFixed(2) } : 'none'; };
  console.log(JSON.stringify({ label: s.label, anchor: at(0), plus1m: at(SC * 16) }));
}
