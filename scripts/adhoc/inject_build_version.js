#!/usr/bin/env node
/**
 * Inject build version string into .web/index.html.
 *
 * Origin: proposal 79dce868 from claude session bc3e41c4
 * Generalized: accepts version as CLI arg, replaces __AK_WEB_BUILD_VERSION__
 *
 * Usage:
 *   node scripts/adhoc/inject_build_version.js v1.2.3
 *   node scripts/adhoc/inject_build_version.js $(git describe --always --dirty)
 */

const fs = require('fs');

const version = process.argv[2];
if (!version) {
  console.error('Usage: inject_build_version.js <version-string>');
  process.exit(1);
}

const htmlPath = '.web/index.html';
let html;
try {
  html = fs.readFileSync(htmlPath, 'utf8');
} catch (e) {
  console.error(`ERROR: cannot read ${htmlPath}: ${e.message}`);
  process.exit(1);
}

if (!html.includes('__AK_WEB_BUILD_VERSION__')) {
  console.error('ERROR: missing __AK_WEB_BUILD_VERSION__ placeholder in .web/index.html');
  process.exit(1);
}

if (!html.includes('__AK_WEB_BUILD_COMMIT__')) {
  console.error('ERROR: missing __AK_WEB_BUILD_COMMIT__ placeholder in .web/index.html');
  process.exit(1);
}

html = html.replace('__AK_WEB_BUILD_VERSION__', version);
html = html.replace('__AK_WEB_BUILD_COMMIT__', version);

fs.writeFileSync(htmlPath, html);
console.log(`Injected version "${version}" into ${htmlPath}`);
