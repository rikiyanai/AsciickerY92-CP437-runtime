// web_classic_smoke.mjs - node smoke test for the Classic CP437 wasm renderer.
//
// PURPOSE: loads .web-classic/index.js in node, writes a built Numabe map (a3d,
// its .sprites dir and the .akm meshes) into MEMFS, calls ClassicLoadMap, aims
// the camera at the home building (OSM way/725138644, "17-16") and renders raw
// AnsiCell frames to an output directory. web_classic_cells_to_png.py turns the
// frames into PNGs.
//
// Usage: node apps/game-classic/web_classic_smoke.mjs [build_dir] [out_dir]
//   build_dir default .run/fl4876_builds/build_reg_64
//   out_dir   default <cwd>/.web-classic/smoke
//
// CORRECT FRAME: CP437 cells showing building roofs/walls as shaded block and
// line glyphs, street/terrain as textured ground glyphs, varied fg/bk palette
// indices, hundreds of distinct (fg,bk,glyph) triples.
// FAILURE: all cells identical or blank (gl 0/32 with one bk), a single flat
// colour, or all-zero buffer.

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const buildDir = path.resolve(process.argv[2] || path.join(root, '.run/fl4876_builds/build_reg_64'));
const outDir = path.resolve(process.argv[3] || path.join(root, '.web-classic/smoke'));
fs.mkdirSync(outDir, { recursive: true });

// --- load the wasm module (non-modularized emscripten script) ---
process.chdir(path.join(root, '.web-classic'));
// The glue is a classic script (var Module at top level), so run it in the
// global context where it honours a pre-defined globalThis.Module; require()
// would hide that global behind the CJS wrapper.
globalThis.require = createRequire(import.meta.url);
globalThis.__dirname = path.join(root, '.web-classic');
globalThis.__filename = path.join(root, '.web-classic/index.js');
const ready = new Promise((resolve) => {
  globalThis.Module = { onRuntimeInitialized: () => resolve(globalThis.Module), print: (s) => console.log('[wasm]', s), printErr: (s) => console.log('[wasm-err]', s) };
});
vm.runInThisContext(fs.readFileSync(path.join(root, '.web-classic/index.js'), 'utf8'), { filename: 'index.js' });
const M = await ready;
const FS = M.FS;

// --- stage map into MEMFS ---
const t0 = Date.now();
FS.mkdir('/maps');
FS.mkdir('/maps/meshes');
FS.writeFile('/maps/output.a3d', fs.readFileSync(path.join(buildDir, 'output.a3d')));
const spritesDir = path.join(buildDir, 'output.sprites');
if (fs.existsSync(spritesDir)) {
  FS.mkdir('/maps/output.sprites');
  for (const f of fs.readdirSync(spritesDir)) FS.writeFile('/maps/output.sprites/' + f, fs.readFileSync(path.join(spritesDir, f)));
}
const trains = path.join(buildDir, 'output.trains.json');
if (fs.existsSync(trains)) FS.writeFile('/maps/output.trains.json', fs.readFileSync(trains));
let nMesh = 0;
for (const f of fs.readdirSync(path.join(buildDir, 'meshes'))) {
  if (f.endsWith('.akm')) { FS.writeFile('/maps/meshes/' + f, fs.readFileSync(path.join(buildDir, 'meshes', f))); nMesh++; }
}
console.log(`staged a3d + ${nMesh} meshes in ${Date.now() - t0} ms`);

const loadMap = M.cwrap('ClassicLoadMap', 'number', ['string', 'string']);
const setCamera = M.cwrap('ClassicSetCamera', null, ['number', 'number', 'number', 'number', 'number', 'number']);
const cameraJson = M.cwrap('ClassicGetCameraJson', 'string', []);
const render = M.cwrap('ClassicRender', 'number', ['number', 'number']);
const pickJson = M.cwrap('ClassicPickJson', 'string', ['number', 'number']);
const groundJson = M.cwrap('ClassicGroundJson', 'string', ['number', 'number']);
const projectJson =M.cwrap('ClassicProjectJson', 'string', ['number', 'number', 'number']);
const stride = M.ccall('ClassicCellStride', 'number', [], []);

// CLASSIC_FAST=1 skips the (slow, ~minutes on this 51k-patch map) baked terrain
// shadow pass for quick iteration; the proof run leaves it unset.
if (process.env.CLASSIC_FAST) M.ccall('ClassicSetLoadOptions', null, ['number'], [1]);
const t1 = Date.now();
const ok = loadMap('/maps/output.a3d', '/maps/meshes');
console.log(`ClassicLoadMap -> ${ok} in ${Date.now() - t1} ms`);
if (!ok) process.exit(2);

// --- home building: UTM centroid -> world units (verified against building_instances.json) ---
const meta = JSON.parse(fs.readFileSync(path.join(buildDir, 'terrain_metadata.json'), 'utf8'));
const ir = JSON.parse(fs.readFileSync(path.join(buildDir, 'canonical.world_ir.json'), 'utf8'));
const feat = ir.canonical_features.find((f) => f.feature_id === 'feature:building_footprint:way/725138644');
const ring = feat.geometry.coordinates.slice(0, -1);
const E = ring.reduce((a, p) => a + p[0], 0) / ring.length;
const N = ring.reduce((a, p) => a + p[1], 0) / ring.length;
const wx = (E - meta.origin_easting) * meta.content_scale + meta.offset_x;
const wy = (N - meta.origin_northing) * meta.content_scale + meta.offset_y;
console.log(`home centroid E=${E.toFixed(3)} N=${N.toFixed(3)} -> world x=${wx.toFixed(2)} y=${wy.toFixed(2)}`);

const W = 160, H = 90;
function frame(name) {
  const ptr = render(W, H);
  if (!ptr) throw new Error('ClassicRender returned null');
  const bytes = M.HEAPU8.slice(ptr, ptr + W * H * stride);
  fs.writeFileSync(path.join(outDir, name + '.raw'), bytes);
  const seen = new Set();
  for (let i = 0; i < bytes.length; i += 4) seen.add(bytes[i] | (bytes[i + 1] << 8) | (bytes[i + 2] << 16));
  console.log(`${name}: ${W}x${H} cells, distinct (fg,bk,gl)=${seen.size}`);
  return seen.size;
}

// Ground height under the home XY from the engine's terrain/mesh ray probe.
// z units: 1 metre = content_scale * HEIGHT_SCALE(16) z units.
const zPerMetre = meta.content_scale * 16;
const ground = JSON.parse(groundJson(wx, wy));
console.log('ground', JSON.stringify(ground), `roof above terrain = ${((ground.top_z - ground.terrain_z) / zPerMetre).toFixed(2)} m (plateau measured 8.9 m)`);
const groundZ = ground.terrain_z;

setCamera(wx, wy, groundZ, 45, 1, 0);
const nOrtho = frame('ortho_home');
console.log('camera', cameraJson());
console.log('pick centre', pickJson(W >> 1, H >> 1));
const pc = JSON.parse(pickJson(10, 10));
console.log('pick corner ', JSON.stringify(pc));
if (pc.hit) console.log('round trip: project(pick corner) ->', projectJson(pc.x, pc.y, pc.z), '(expect cx=10 cy=10)');
console.log('project home', projectJson(wx, wy, groundZ));

// Wider ortho view for context (zoom < 1 shows more world).
setCamera(wx, wy, groundZ, 45, 0.5, 0);
frame('ortho_home_zoom_half');

// Street level perspective: eye 1.6 m above ground, in content_scale units.
// Four headings so the street direction can be chosen by inspection.
const eyeZ = groundZ + 1.6 * zPerMetre;
for (const yaw of [0, 90, 180, 270]) {
  setCamera(wx, wy, eyeZ, yaw, 1, 1);
  frame('persp_street_yaw' + yaw);
  console.log(`persp yaw ${yaw} camera ${cameraJson()}`);
}
console.log(nOrtho > 20 ? 'SMOKE PASS (non-uniform ortho frame)' : 'SMOKE FAIL (uniform/blank ortho frame)');
