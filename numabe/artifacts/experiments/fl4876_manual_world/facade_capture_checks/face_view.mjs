import { fileURLToPath } from 'node:url';
// node face_view.mjs PORT WAY FACE OUT.raw [ZOOM] -- render one building face of the build a map editor on PORT serves,
// camera targeted at the face midpoint, looking at it head-on, 1.5 m above the street, no preview injection
// (generalised fc_buildview.mjs). Output: raw Classic AnsiCell frame 160x70, bottom-up; convert with
// apps/game-classic/web_classic_cells_to_png.py then flip.py.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [port, way, face, out, zoomArg] = process.argv.slice(2); const cols = 160, rows = 70, base = 'http://127.0.0.1:' + port;
const zoom = Number(zoomArg || 1);
const root = fileURLToPath(new URL('../../../../', import.meta.url)); process.chdir(path.join(root, '.web-classic'));
globalThis.require = createRequire(import.meta.url); globalThis.__dirname = path.join(root, '.web-classic'); globalThis.__filename = path.join(root, '.web-classic/index.js');
const ready = new Promise(r => { globalThis.Module = { onRuntimeInitialized: () => r(globalThis.Module), print: () => {}, printErr: () => {} }; });
vm.runInThisContext(fs.readFileSync(path.join(root, '.web-classic/index.js'), 'utf8'), { filename: 'index.js' });
const M = await ready; const c = (n, r, a) => M.cwrap(n, r, a);
const buf = new Uint8Array(await (await fetch(base + '/api/map_bundle')).arrayBuffer()), dv = new DataView(buf.buffer);
let o = 8; const n = dv.getUint32(4, true), dec = new TextDecoder(), made = new Set(); M.FS.mkdirTree('/numabe/meshes');
for (let i = 0; i < n; i++) { const L = dv.getUint16(o, true); o += 2; const name = dec.decode(buf.subarray(o, o + L)); o += L; const sz = dv.getUint32(o, true); o += 4;
  const dir = name.includes('/') ? '/numabe/' + name.slice(0, name.lastIndexOf('/')) : null; if (dir && !made.has(dir)) { M.FS.mkdirTree(dir); made.add(dir); }
  M.FS.writeFile('/numabe/' + name, buf.subarray(o, o + sz)); o += sz; }
c('ClassicSetLoadOptions', null, ['number'])(1);
if (!c('ClassicLoadMap', 'number', ['string', 'string'])('/numabe/output.a3d', '/numabe/meshes')) { console.log('LOAD FAILED'); process.exit(1); }
const f = await (await fetch(base + '/api/face?building=' + encodeURIComponent('feature:building_footprint:way/' + way) + '&face=' + face)).json();
if (!f.left_world) { console.log('NO FACE', JSON.stringify(f)); process.exit(1); }
const mx = (f.left_world[0] + f.right_world[0]) / 2, my = (f.left_world[1] + f.right_world[1]) / 2;
const yaw = (360 - ((f.bearing_deg + 180) % 360)) % 360, z = f.street_z + 1.5 * f.z_per_m;
c('ClassicSetMinimap', null, ['number'])(0);
c('ClassicSetCamera', null, ['number', 'number', 'number', 'number', 'number', 'number'])(mx, my, z, yaw, zoom, 0);
const p = c('ClassicRender', 'number', ['number', 'number'])(cols, rows);
fs.writeFileSync(out, Buffer.from(new Uint8Array(M.HEAPU8.buffer, p, cols * rows * 4)));
console.log('rendered way', way, 'face', face, 'bearing', f.bearing_deg, 'zoom', zoom, '->', out);
