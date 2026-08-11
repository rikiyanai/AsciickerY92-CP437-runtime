#!/usr/bin/env node
// FL-4131 regression: web must consume compiled glyph atlas artifacts, not
// duplicate manifest admission/coverage rows in game_web.html.

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const repoRoot = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(repoRoot, "web", "game_web.html"), "utf8");
const required = [
  "material.additive.v1.atlas_of_atlases.json",
  "material.additive.v1.lut_rgba8.json",
  "fetch(ak_fl4131_atlas_base + page.url)",
  "Promise.all",
  "InitFl4131CompiledManifestTextures",
];
const forbidden = [
  "var admittedGlyphIds = [",
  "var admittedCoverageRows = [",
  "InitFl4131FixtureManifestTextures",
];
const errors = [];
for (const needle of required) {
  if (!html.includes(needle)) errors.push(`missing required web compiled-atlas binding: ${needle}`);
}
for (const needle of forbidden) {
  if (html.includes(needle)) errors.push(`forbidden hardcoded fixture atlas remains: ${needle}`);
}
const atlasDir = path.join(repoRoot, "assets", "glyphs", "atlases");
for (const file of [
  "material.additive.v1.atlas_of_atlases.json",
  "material.additive.v1.lut_rgba8.json",
]) {
  const artifact = path.join(atlasDir, file);
  if (!fs.existsSync(artifact)) errors.push(`missing compiled atlas artifact: ${artifact}`);
}
const atlasPath = path.join(atlasDir, "material.additive.v1.atlas_of_atlases.json");
if (fs.existsSync(atlasPath)) {
  const atlas = JSON.parse(fs.readFileSync(atlasPath, "utf8"));
  if (!Array.isArray(atlas.pages) || atlas.pages.length === 0) {
    errors.push("compiled atlas manifest has no selectable pages");
  } else {
    for (const page of atlas.pages) {
      if (!page || typeof page.url !== "string" || !page.url) {
        errors.push("compiled atlas page is missing its manifest-owned URL");
        continue;
      }
      const pagePath = path.join(atlasDir, page.url);
      if (!fs.existsSync(pagePath)) {
        errors.push(`missing manifest-referenced atlas page: ${pagePath}`);
        continue;
      }
      const pagePayload = JSON.parse(fs.readFileSync(pagePath, "utf8"));
      if (!Array.isArray(pagePayload.rgba8)) {
        errors.push(`atlas page has no RGBA8 payload: ${page.url}`);
        continue;
      }
      const actualHash = crypto.createHash("sha256").update(Buffer.from(pagePayload.rgba8)).digest("hex");
      if (actualHash !== page.page_hash) {
        errors.push(`atlas page hash mismatch: ${page.url}`);
      }
      if (pagePayload.page_hash !== page.page_hash) {
        errors.push(`atlas manifest/page embedded hash mismatch: ${page.url}`);
      }
    }
  }
}
if (errors.length) {
  for (const error of errors) console.error(`FAIL: ${error}`);
  process.exit(1);
}
console.log("FL-4131 web compiled atlas binding checks passed");
