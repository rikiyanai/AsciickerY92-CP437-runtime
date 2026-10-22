import { fileURLToPath } from 'node:url';
// node sign_frame.mjs RECEIPT.json PORT LABEL HEADING OUT_PREFIX [ZOOM] -- render one sign of a build (camera target =
// the sign, given compass HEADING the camera looks toward, zoom default 1) twice: with the sign shown and hidden, as raw
// Classic AnsiCell frames OUT_PREFIX_shown.raw / OUT_PREFIX_hidden.raw (160x70). Convert with
// apps/game-classic/web_classic_cells_to_png.py then flip.py (the buffer is bottom-up).
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [receiptPath, port, label, heading, outPrefix, zoomArg] = process.argv.slice(2); const cols = 160, rows = 70;
const zoom = Number(zoomArg || 1);
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
const grab = () => { const p = render(cols, rows); return Buffer.from(Uint8Array.from(new Uint8Array(M.HEAPU8.buffer, p, cols * rows * 4))); };
const s = JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs.find(x => x.label === label);
if (!s) throw new Error('no sign labelled ' + label);
cam(s.pos[0], s.pos[1], s.pos[2], (360 - Number(heading)) % 360, zoom, 0);
fs.writeFileSync(outPrefix + '_shown.raw', grab()); vis(s.sprite, 0);
fs.writeFileSync(outPrefix + '_hidden.raw', grab()); vis(s.sprite, 1);
console.log(JSON.stringify({ label, sprite: s.sprite, pos: s.pos, heading: Number(heading), zoom, cols, rows }));
