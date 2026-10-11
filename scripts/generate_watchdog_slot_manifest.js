#!/usr/bin/env node
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

function argValue(name, fallback) {
  const idx = process.argv.indexOf(name);
  if (idx >= 0 && idx + 1 < process.argv.length) return process.argv[idx + 1];
  return fallback;
}

function sha256File(file) {
  if (!fs.existsSync(file)) return null;
  const h = crypto.createHash('sha256');
  h.update(fs.readFileSync(file));
  return h.digest('hex');
}

function git(repoRoot, args) {
  try {
    return execFileSync('git', args, { cwd: repoRoot, encoding: 'utf8' }).trim();
  } catch (_) {
    return '';
  }
}

function rel(repoRoot, p) {
  const relative = path.relative(repoRoot, p).replace(/\\/g, '/');
  return relative || '.';
}

const repoRoot = path.resolve(argValue('--repo-root', '.'));
const webDir = path.resolve(repoRoot, argValue('--web-dir', '.web'));
const output = path.resolve(repoRoot, argValue('--output', path.join('.web', 'slot_manifest.json')));
const serverPath = path.resolve(repoRoot, argValue('--server-path', path.join('.run', 'server')));
const configFile = path.resolve(repoRoot, argValue('--config-file', path.join('web', 'asciicker.json')));
const runtimeRoot = path.resolve(repoRoot, argValue('--runtime-root', '.'));
const slotName = argValue('--slot-name', 'candidate');
const machineRole = argValue('--machine-role', 'candidate');
const allowMissingServer = process.argv.includes('--allow-missing-server');

const generatedTable = path.join(repoRoot, 'engine', 'actor_visual_profile_table.generated.h');
const serverIdentity = path.join(repoRoot, 'server', 'actor_visual_reachability_identity.generated.h');

const manifest = {
  schema_id: 'asciicker.watchdog.slot_manifest.v1',
  slot_name: slotName,
  machine_role: machineRole,
  source_ref: git(repoRoot, ['rev-parse', '--verify', 'HEAD']),
  git_head: git(repoRoot, ['rev-parse', '--verify', 'HEAD']),
  git_dirty: git(repoRoot, ['status', '--porcelain']).length > 0,
  runtime_root: rel(repoRoot, runtimeRoot),
  config_file: rel(repoRoot, configFile),
  generated_at_unix_ms: Date.now(),
  web: {
    index_html_sha256: sha256File(path.join(webDir, 'index.html')),
    index_js_sha256: sha256File(path.join(webDir, 'index.js')),
    index_wasm_sha256: sha256File(path.join(webDir, 'index.wasm')),
    index_data_sha256: sha256File(path.join(webDir, 'index.data')),
    generated_table_sha256: sha256File(generatedTable),
    server_reachability_identity_sha256: sha256File(serverIdentity),
  },
  server: {
    path: rel(repoRoot, serverPath),
    sha256: sha256File(serverPath),
  },
};

for (const [key, value] of Object.entries(manifest.web)) {
  if (!value) throw new Error(`missing web artifact hash for ${key}`);
}
if (!manifest.server.sha256) {
  if (allowMissingServer) {
    console.warn(`warning: server binary not found at ${serverPath} — slot_manifest.server.sha256 will be null (web-only build)`);
  } else {
    throw new Error(`missing server binary: ${serverPath}`);
  }
}

fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, JSON.stringify(manifest, null, 2) + '\n');
console.log(`wrote ${rel(repoRoot, output)}`);
