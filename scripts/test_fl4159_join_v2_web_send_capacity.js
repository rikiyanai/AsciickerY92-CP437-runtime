#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(repoRoot, "web", "game_web.html"), "utf8");
const webHeader = fs.readFileSync(path.join(repoRoot, "web", "web_network_client.h"), "utf8");
const webClient = fs.readFileSync(path.join(repoRoot, "web", "web_network_client.cpp"), "utf8");
const serverState = fs.readFileSync(path.join(repoRoot, "server", "server_state.h"), "utf8");
const serverTick = fs.readFileSync(path.join(repoRoot, "server", "server_tick.cpp"), "utf8");

assert.match(
  serverState,
  /static_assert\s*\(\s*SVR_INBOUND_MSG_MAX\s*>=\s*sizeof\s*\(\s*STRUCT_REQ_JOIN_V2\s*\)/,
  "server inbound capacity must be statically tied to STRUCT_REQ_JOIN_V2"
);
assert.match(
  serverState,
  /uint8_t\s+data\s*\[\s*SVR_INBOUND_MSG_MAX\s*\]\s*;\s*uint16_t\s+size/,
  "ClientIO inbound ring must use SVR_INBOUND_MSG_MAX"
);
assert.match(
  serverTick,
  /uint8_t\s+data\s*\[\s*SVR_INBOUND_MSG_MAX\s*\]/,
  "ServerTick pending message buffer must use SVR_INBOUND_MSG_MAX"
);
assert.match(
  serverTick,
  /uint8_t\s+payload\s*\[\s*SVR_INBOUND_MSG_MAX\s*\]/,
  "IOThreadEntry websocket payload buffer must use SVR_INBOUND_MSG_MAX"
);

assert.match(
  webHeader,
  /uint8_t\s+send_buf\s*\[\s*2\s*\+\s*WEB_OUTBOUND_MSG_MAX\s*\]/,
  "web outbound send buffer must have a two-byte length prefix and protocol-sized payload capacity"
);
assert.doesNotMatch(
  webClient,
  /if\s*\(\s*size\s*>\s*256\s*\)/,
  "web Server::Send must not retain the 256-byte pre-JOIN_V2 clamp"
);
assert.match(
  webClient,
  /if\s*\(\s*size\s*>\s*WEB_OUTBOUND_MSG_MAX\s*\)/,
  "web Server::Send must clamp against WEB_OUTBOUND_MSG_MAX"
);
assert.match(
  html,
  /Module\.HEAPU8\.buffer,\s*ak_packet_ptr,\s*2\s*\+\s*WEB_OUTBOUND_MSG_MAX/,
  "game_web.html Send/ConsoleLog must read the protocol-sized buffer"
);
assert.match(
  html,
  /view\[0\]\s*\|\s*\(\s*view\[1\]\s*<<\s*8\s*\)/,
  "game_web.html must decode the two-byte outbound length prefix"
);

console.log("FL-4159 JOIN_V2 web send capacity source checks passed");
