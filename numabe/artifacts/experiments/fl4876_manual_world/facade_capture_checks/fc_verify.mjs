import { fileURLToPath } from 'node:url';
// node fc_verify.mjs BANDS.json OUTDIR -- headless check of the facade-capture preview path on the served build:
// /api/face (face 1 of Lawson) -> /api/facade_preview (server XP bytes + build anchor) -> ClassicPreviewSpriteAdd +
// ClassicSetInstVisible(place front, 0) in the same Classic wasm the editor runs; renders before/after raw frames.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [bandsPath, outDir, zoomArg = '2.5', tag = ''] = process.argv.slice(2); const cols = 160, rows = 70, ZOOM = +zoomArg;
const root = fileURLToPath(new URL('../../../../', import.meta.url)); process.chdir(path.join(root, '.web-classic'));
globalThis.require = createRequire(import.meta.url); globalThis.__dirname = path.join(root, '.web-classic'); globalThis.__filename = path.join(root, '.web-classic/index.js');
const ready = new Promise(r => { globalThis.Module = { onRuntimeInitialized: () => r(globalThis.Module), print: s => { if (/CLASSIC-WEB.*(fail|REJECT)/i.test(s)) console.log(s) }, printErr: () => {} }; });
vm.runInThisContext(fs.readFileSync(path.join(root, '.web-classic/index.js'), 'utf8'), { filename: 'index.js' });
const M = await ready; const c = (n, r, a) => M.cwrap(n, r, a);
const api = async (u, b) => { const r = await fetch('http://127.0.0.1:8792' + u, b ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) } : {}); const j = await r.json(); if (!r.ok) throw new Error(j.error); return j; };
const buf = new Uint8Array(await (await fetch('http://127.0.0.1:8792/api/map_bundle')).arrayBuffer()), dv = new DataView(buf.buffer);
let o = 8; const n = dv.getUint32(4, true), dec = new TextDecoder(), made = new Set(); M.FS.mkdirTree('/numabe/meshes');
for (let i = 0; i < n; i++) { const L = dv.getUint16(o, true); o += 2; const name = dec.decode(buf.subarray(o, o + L)); o += L; const sz = dv.getUint32(o, true); o += 4;
  const dir = name.includes('/') ? '/numabe/' + name.slice(0, name.lastIndexOf('/')) : null; if (dir && !made.has(dir)) { M.FS.mkdirTree(dir); made.add(dir); }
  M.FS.writeFile('/numabe/' + name, buf.subarray(o, o + sz)); o += sz; }
c('ClassicSetLoadOptions', null, ['number'])(1);
if (!c('ClassicLoadMap', 'number', ['string', 'string'])('/numabe/output.a3d', '/numabe/meshes')) { console.log('LOAD FAILED'); process.exit(1); }
const bid = 'feature:building_footprint:way/725137997';
const f = await api(`/api/face?building=${encodeURIComponent(bid)}&face=1`);
const mx = (f.left_world[0] + f.right_world[0]) / 2, my = (f.left_world[1] + f.right_world[1]) / 2;
const yaw = (360 - ((f.bearing_deg + 180) % 360)) % 360, z = f.street_z + 1.5 * f.z_per_m;
const cam = c('ClassicSetCamera', null, ['number', 'number', 'number', 'number', 'number', 'number']), render = c('ClassicRender', 'number', ['number', 'number']);
c('ClassicSetMinimap', null, ['number'])(0);
const shot = (name) => { cam(mx, my, z, yaw, ZOOM, 0); const p = render(cols, rows); fs.writeFileSync(path.join(outDir, tag + name),Buffer.from(new Uint8Array(M.HEAPU8.buffer, p, cols * rows * 4))); };
shot('before.raw');
const bands = JSON.parse(fs.readFileSync(bandsPath, 'utf8'));
const r = await api('/api/facade_preview', { building_id: bid, face_index: 1, bands });
const add = c('ClassicPreviewSpriteAdd', 'number', ['string', 'number', 'number', 'number', 'number']);
M.FS.mkdirTree('/numabe/preview');
const slots = r.bands.map((b, i) => { if (!b.pos) return { i, err: b.wall_snap }; const p = `/numabe/preview/t${i}.xp`; M.FS.writeFile(p, Buffer.from(b.xp, 'base64')); return { i, slot: add(p, b.pos[0], b.pos[1], b.pos[2], 0), pos: b.pos, cells: b.cells, wall: b.wall_snap }; });
const hidden = f.place_fronts.map(p => [p.inst_name, c('ClassicSetInstVisible', 'number', ['string', 'number'])(p.inst_name, 0)]);
shot('after.raw');
const grab = () => { cam(mx, my, z, yaw, ZOOM, 0); const p = render(cols, rows); return Uint32Array.from(new Uint32Array(M.HEAPU8.buffer, p, cols * rows)); };
const proj = c('ClassicProjectJson', 'string', ['number', 'number', 'number']);
const full = grab(), rowsOf = (a, b) => { const rs = new Set(); for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) rs.add(Math.floor(i / cols)); return [...rs].sort((x, y) => x - y); };
const rem = c('ClassicPreviewSpriteRemove', null, ['number']);
const probe = slots.map(s => { rem(s.slot); const without = grab(); const rr = rowsOf(full, without); const pr = JSON.parse(proj(...s.pos));
  const p2 = JSON.parse(proj(s.pos[0], s.pos[1], s.pos[2] + 213)); s.slot = add(`/numabe/preview/t${s.i}.xp`, ...s.pos, 0);
  return { i: s.i, z: s.pos[2], rows_bottom_up: [rr[0], rr[rr.length - 1]], anchor_proj: pr, plus_2_6m_proj: p2 }; });
console.log('PROBE ' + JSON.stringify(probe));
rem(slots[0].slot);
shot('removed_sign.raw');
console.log(JSON.stringify({ face: { i: f.face_index, len: f.length_m, h: f.height_m, bearing: f.bearing_deg }, cam: [mx, my, z, yaw], slots, hidden }));
