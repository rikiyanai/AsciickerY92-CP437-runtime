import { fileURLToPath } from 'node:url';
// node xp_pair_render.mjs PORT RECEIPT.json LABEL A.xp B.xp -- in the served map, with sign LABEL hidden, add each
// .xp as a preview sprite at that sign's position and count cells that differ from the frame without it, from
// 8 headings. Two sprites of the same size, one with transparent (key) cells and one with those cells filled,
// show whether the key cells are drawn.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [port, receiptPath, label, a, b] = process.argv.slice(2); const cols = 160, rows = 70;
const root = fileURLToPath(new URL('../../../../', import.meta.url)); process.chdir(path.join(root, '.web-classic'));
globalThis.require = createRequire(import.meta.url); globalThis.__dirname = path.join(root, '.web-classic'); globalThis.__filename = path.join(root, '.web-classic/index.js');
const ready = new Promise(r => { globalThis.Module = { onRuntimeInitialized: () => r(globalThis.Module), print: () => {}, printErr: () => {} }; });
vm.runInThisContext(fs.readFileSync(path.join(root, '.web-classic/index.js'), 'utf8'), { filename: 'index.js' });
const M = await ready; const c = (n, r, t) => M.cwrap(n, r, t);
const buf = new Uint8Array(await (await fetch(`http://127.0.0.1:${port}/api/map_bundle`)).arrayBuffer()), dv = new DataView(buf.buffer);
let o = 8; const n = dv.getUint32(4, true), dec = new TextDecoder(), made = new Set(); M.FS.mkdirTree('/numabe/meshes');
for (let i = 0; i < n; i++) { const L = dv.getUint16(o, true); o += 2; const name = dec.decode(buf.subarray(o, o + L)); o += L; const sz = dv.getUint32(o, true); o += 4;
  const dir = name.includes('/') ? '/numabe/' + name.slice(0, name.lastIndexOf('/')) : null; if (dir && !made.has(dir)) { M.FS.mkdirTree(dir); made.add(dir); }
  M.FS.writeFile('/numabe/' + name, buf.subarray(o, o + sz)); o += sz; }
c('ClassicSetLoadOptions', null, ['number'])(1); c('ClassicLoadMap', 'number', ['string', 'string'])('/numabe/output.a3d', '/numabe/meshes'); c('ClassicSetMinimap', null, ['number'])(0);
const cam = c('ClassicSetCamera', null, ['number', 'number', 'number', 'number', 'number', 'number']), render = c('ClassicRender', 'number', ['number', 'number']);
const vis = c('ClassicSetSpriteVisible', 'number', ['string', 'number']);
const add = c('ClassicPreviewSpriteAdd', 'number', ['string', 'number', 'number', 'number', 'number']), rem = c('ClassicPreviewSpriteRemove', null, ['number']);
const grab = () => { const p = render(cols, rows); return Uint32Array.from(new Uint32Array(M.HEAPU8.buffer, p, cols * rows)); };
const diff = (x, y) => { let k = 0; for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) k++; return k; };
M.FS.mkdirTree('/numabe/pv'); M.FS.writeFile('/numabe/pv/a.xp', fs.readFileSync(a)); M.FS.writeFile('/numabe/pv/b.xp', fs.readFileSync(b));
const s = JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs.find(x => x.label === label); vis(s.sprite, 0);
const res = { keyed: [], filled: [] };
for (let h = 0; h < 360; h += 45) { cam(s.pos[0], s.pos[1], s.pos[2], (360 - h) % 360, 1, 0); const base = grab();
  for (const [k, f] of [['keyed', '/numabe/pv/a.xp'], ['filled', '/numabe/pv/b.xp']]) { const slot = add(f, s.pos[0], s.pos[1], s.pos[2], 0); res[k].push(diff(grab(), base)); rem(slot); } }
console.log(JSON.stringify(res));
