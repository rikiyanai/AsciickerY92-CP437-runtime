#!/usr/bin/env node
/**
 * Compute a SHA-256 content hash of web build artifacts.
 *
 * Origin: proposal ee1e2425 (FL-017) from claude session bc3e41c4
 * Generalized: accepts file list as args, outputs first 16 hex chars of hash.
 *
 * Usage:
 *   node scripts/adhoc/compute_build_hash.js .web/index.js .web/index.wasm .web/index.data
 */

const crypto = require('crypto');
const fs = require('fs');

const files = process.argv.slice(2);
if (files.length === 0) {
  console.error('Usage: compute_build_hash.js <file1> [file2 ...]');
  process.exit(1);
}

const hasher = crypto.createHash('sha256');
for (const rel of files) {
  try {
    hasher.update(fs.readFileSync(rel));
  } catch (e) {
    console.error(`ERROR: cannot read ${rel}: ${e.message}`);
    process.exit(1);
  }
}

process.stdout.write(hasher.digest('hex').slice(0, 16) + '\n');
