#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(repoRoot, "web", "game_web.html"), "utf8");
const proof = fs.readFileSync(path.join(repoRoot, "scripts", "proofs", "proof_fl4131_multiplayer_fallback_agreement.js"), "utf8");

assert.match(
  html,
  /fl4131_inject_admitted/,
  "web proof injection must expose an admitted GlyphId mode"
);
assert.match(
  html,
  /fl4131_inject_admitted[\s\S]*fl4131_bind_manifest/,
  "admitted injection mode must require a bound manifest"
);
assert.match(
  html,
  /0x200\s*\+\s*\(\s*x\s*-\s*40\s*\)/,
  "admitted injection must stamp GlyphIds from the admitted 512+ material range"
);
assert.match(
  proof,
  /PROOF_ADMITTED/,
  "multiplayer proof must be able to run admitted-content mode"
);
assert.match(
  proof,
  /phase_d_multiplayer_admitted_extended_local_two_tab\.json/,
  "admitted-content proof must write a distinct receipt"
);

console.log("FL-4131 multiplayer admitted injection source checks passed");
