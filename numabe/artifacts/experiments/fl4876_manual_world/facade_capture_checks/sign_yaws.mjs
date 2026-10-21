import { fileURLToPath } from 'node:url';
// node sign_yaws.mjs RECEIPT.json [PORT] -- per sign: cells drawn by the real sign (show vs hide) and by a copy 1 m
// further out along its normal, from 8 camera headings (every 45 deg), zoom 1, camera target = the sign.
import fs from 'node:fs'; import path from 'node:path'; import vm from 'node:vm'; import { createRequire } from 'node:module';
const [receiptPath, port = '8792', outPath] = process.argv.slice(2); const cols = 160, rows = 70, SC = 5.1320023927966725;
const lines = [];
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
for (const s of JSON.parse(fs.readFileSync(receiptPath, 'utf8')).signs) {
  const [nx, ny] = s.normal || [0, 1], real = [], out1 = [];
  for (let h = 0; h < 360; h += 45) {
    const yaw = (360 - h) % 360; cam(s.pos[0], s.pos[1], s.pos[2], yaw, 1, 0);
    const shown = grab(); vis(s.sprite, 0); const hidden = grab(); real.push(diff(shown, hidden));
    const slot = add('/numabe/output.sprites/' + s.sprite, s.pos[0] + nx * SC, s.pos[1] + ny * SC, s.pos[2], 0);
    cam(s.pos[0], s.pos[1], s.pos[2], yaw, 1, 0); out1.push(diff(grab(), hidden)); rem(slot); vis(s.sprite, 1);
  }
  const facing = Math.round((Math.atan2(nx, ny) * 180 / Math.PI + 360) % 360);
  const line = JSON.stringify({ label: s.label, sprite: s.sprite, total: s.cells[0] * s.cells[1], normal_bearing: facing, headings: [0, 45, 90, 135, 180, 225, 270, 315], real, out1m: out1 });
  lines.push(line); console.log(line);
}
if (outPath) fs.writeFileSync(outPath, lines.join('\n') + '\n'); // durable result beside the probe
