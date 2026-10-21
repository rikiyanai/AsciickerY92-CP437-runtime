import { fileURLToPath } from 'node:url';
// node fc_sweep.mjs BANDS.json -- how many rows of each test sprite the Classic renderer draws when its anchor is
// moved along Lawson face 1's outward normal (metres from the footprint face), at zoom 1, camera facing the face.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [bandsPath] = process.argv.slice(2); const cols = 160, rows = 70;
const root = fileURLToPath(new URL('../../../../', import.meta.url)); process.chdir(path.join(root, '.web-classic'));
globalThis.require = createRequire(import.meta.url); globalThis.__dirname = path.join(root, '.web-classic'); globalThis.__filename = path.join(root, '.web-classic/index.js');
const ready = new Promise(r => { globalThis.Module = { onRuntimeInitialized: () => r(globalThis.Module), print: () => {}, printErr: () => {} }; });
vm.runInThisContext(fs.readFileSync(path.join(root, '.web-classic/index.js'), 'utf8'), { filename: 'index.js' });
const M = await ready; const c = (n, r, a) => M.cwrap(n, r, a);
const api = async (u, b) => { const r = await fetch('http://127.0.0.1:8792' + u, b ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) } : {}); return r.json(); };
const buf = new Uint8Array(await (await fetch('http://127.0.0.1:8792/api/map_bundle')).arrayBuffer()), dv = new DataView(buf.buffer);
let o = 8; const n = dv.getUint32(4, true), dec = new TextDecoder(), made = new Set(); M.FS.mkdirTree('/numabe/meshes');
for (let i = 0; i < n; i++) { const L = dv.getUint16(o, true); o += 2; const name = dec.decode(buf.subarray(o, o + L)); o += L; const sz = dv.getUint32(o, true); o += 4;
  const dir = name.includes('/') ? '/numabe/' + name.slice(0, name.lastIndexOf('/')) : null; if (dir && !made.has(dir)) { M.FS.mkdirTree(dir); made.add(dir); }
  M.FS.writeFile('/numabe/' + name, buf.subarray(o, o + sz)); o += sz; }
c('ClassicSetLoadOptions', null, ['number'])(1);
c('ClassicLoadMap', 'number', ['string', 'string'])('/numabe/output.a3d', '/numabe/meshes');
const bid = 'feature:building_footprint:way/725137997';
const f = await api(`/api/face?building=${encodeURIComponent(bid)}&face=1`);
const mx = (f.left_world[0] + f.right_world[0]) / 2, my = (f.left_world[1] + f.right_world[1]) / 2, sc = f.content_scale;
const yaw = (360 - ((f.bearing_deg + 180) % 360)) % 360, z = f.street_z + 1.5 * f.z_per_m;
const cam = c('ClassicSetCamera', null, ['number', 'number', 'number', 'number', 'number', 'number']), render = c('ClassicRender', 'number', ['number', 'number']);
c('ClassicSetMinimap', null, ['number'])(0);
const grab = () => { cam(mx, my, z, yaw, 1, 0); const p = render(cols, rows); return Uint32Array.from(new Uint32Array(M.HEAPU8.buffer, p, cols * rows)); };
const add = c('ClassicPreviewSpriteAdd', 'number', ['string', 'number', 'number', 'number', 'number']), rem = c('ClassicPreviewSpriteRemove', null, ['number']);
const bands = JSON.parse(fs.readFileSync(bandsPath, 'utf8'));
const r = await api('/api/facade_preview', { building_id: bid, face_index: 1, bands });
M.FS.mkdirTree('/numabe/preview');
r.bands.forEach((b, i) => M.FS.writeFile(`/numabe/preview/s${i}.xp`, Buffer.from(b.xp, 'base64')));
const base = grab(); const [nx, ny] = f.normal;
const mid = [mx, my];
for (const [i, b] of bands.entries()) {
  const line = [];
  for (const off of [-1.0, -0.6, -0.4, -0.2, 0, 0.2, 0.4, 0.6, 1.0, 1.5, 2.0]) {
    const zz = f.street_z + b.y0_m * f.z_per_m;
    const s = add(`/numabe/preview/s${i}.xp`, mid[0] + nx * off * sc, mid[1] + ny * off * sc, zz, 0);
    const g = grab(); rem(s); const rs = new Set(); for (let k = 0; k < g.length; k++) if (g[k] !== base[k]) rs.add(Math.floor(k / cols));
    line.push(`${off}m:${rs.size}`);
  }
  console.log(b.kind, `rows=${b.cells.length}`, 'server anchor', JSON.stringify(r.bands[i].wall_snap), '\n  ', line.join(' '));
}
