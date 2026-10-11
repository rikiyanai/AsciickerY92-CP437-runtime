// proof_fl4131_native_material_render.js
//
// FL-4131 Phase D: native material sidecar render-resolve proof.
//
// This drives the product binary's native material sidecar proof entry point and
// records the evidence that admitted extended material GlyphIds survive loading
// and resolve to coverage-derived display glyphs instead of their CP437 fallback
// bytes.

'use strict';

const { spawnSync, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const GAME = path.join(REPO_ROOT, '.run', 'game');
const OUT_DIR = process.env.PROOF_OUT_DIR
  || path.join(REPO_ROOT, 'docs', 'research', 'ascii', 'verification', 'fl4131', 'phase_d', '2026-05-30');
const RECEIPT = path.join(OUT_DIR, 'phase_d_native_material_render_resolve.json');

function currentCommit() {
  try { return execSync('git rev-parse HEAD', { cwd: REPO_ROOT }).toString().trim(); }
  catch (_) { return 'unknown'; }
}

function tail(text, maxLines) {
  return String(text || '').split(/\r?\n/).slice(-maxLines).join('\n');
}

function parseProof(stdout) {
  const start = stdout.indexOf('[FL4131_NATIVE_MATERIAL_SIDECAR_PROOF_START]');
  const end = stdout.indexOf('[FL4131_NATIVE_MATERIAL_SIDECAR_PROOF_END]');
  if (start < 0 || end < 0 || end <= start) {
    throw new Error('native FL-4131 proof markers not found in stdout');
  }
  const jsonStart = stdout.indexOf('{', start);
  const jsonEnd = stdout.lastIndexOf('}', end);
  if (jsonStart < 0 || jsonEnd < jsonStart) {
    throw new Error('native FL-4131 proof JSON not found between markers');
  }
  return JSON.parse(stdout.slice(jsonStart, jsonEnd + 1));
}

function assertProof(proof) {
  const failures = [];
  if (proof.schema !== 'fl4131_native_material_sidecar_load.v1') failures.push(`schema=${proof.schema}`);
  if (proof.verdict !== 'PASS') failures.push(`verdict=${proof.verdict}`);
  if (proof.loaded !== true) failures.push('material sidecar did not load');
  if (proof.applied_cells !== 120) failures.push(`applied_cells=${proof.applied_cells}`);
  if (proof.expected_cells !== 120) failures.push(`expected_cells=${proof.expected_cells}`);
  if (proof.first_glyph_id !== 512) failures.push(`first_glyph_id=${proof.first_glyph_id}`);
  if (proof.last_glyph_id !== 631) failures.push(`last_glyph_id=${proof.last_glyph_id}`);
  if (proof.cells_ok !== true) failures.push('cells_ok is not true');
  if (proof.coverage_cells !== 120) failures.push(`coverage_cells=${proof.coverage_cells}`);
  if (proof.coverage_ok !== true) failures.push('coverage_ok is not true');
  if (proof.display_cells !== 120) failures.push(`display_cells=${proof.display_cells}`);
  if (proof.display_not_fallback_bytes !== true) failures.push('display glyphs still match fallback bytes');
  if (failures.length) {
    throw new Error(`native material render proof failed: ${failures.join('; ')}`);
  }
}

function main() {
  if (!fs.existsSync(GAME)) {
    throw new Error(`missing ${GAME}; run: make -f makefile_game_mac .run/game`);
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const result = spawnSync(GAME, ['--fl4131-native-material-sidecar-proof'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  const stdout = result.stdout || '';
  const stderr = result.stderr || '';
  const proof = parseProof(stdout);
  assertProof(proof);
  if (result.status !== 0) {
    throw new Error(`native proof exited status=${result.status}`);
  }

  const receipt = {
    schema: 'fl4131_native_material_render_resolve_receipt.v1',
    verdict: 'PASS',
    generated_at: new Date().toISOString(),
    commit_under_test: currentCommit(),
    command: `${path.relative(REPO_ROOT, GAME)} --fl4131-native-material-sidecar-proof`,
    proof,
    assertions: {
      admitted_extended_cells_loaded: proof.applied_cells === 104,
      coverage_backed_display_cells: proof.coverage_cells === 104,
      display_glyphs_not_fallback_bytes: proof.display_cells === 104 && proof.display_not_fallback_bytes === true,
    },
    process: {
      status: result.status,
      signal: result.signal,
      stdout_tail: tail(stdout, 80),
      stderr_tail: tail(stderr, 80),
    },
  };
  fs.writeFileSync(RECEIPT, `${JSON.stringify(receipt, null, 2)}\n`);
  process.stderr.write(`[proof-fl4131-native] wrote ${path.relative(REPO_ROOT, RECEIPT)}\n`);
}

try {
  main();
} catch (err) {
  process.stderr.write(`[proof-fl4131-native] ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
}
