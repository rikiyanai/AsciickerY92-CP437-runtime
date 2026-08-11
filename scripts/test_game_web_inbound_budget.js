#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const repoRoot = path.resolve(__dirname, "..");
const htmlPath = path.join(repoRoot, "web", "game_web.html");
const html = fs.readFileSync(htmlPath, "utf8");
const start = html.indexOf("      var AK_INBOUND_GAMEPLAY_FLUSH_DELAY_MS = 8;");
const end = html.indexOf("      function ForwardNetPacketToGame(msg)", start);
assert(start >= 0, "inbound gameplay queue start marker missing");
assert(end > start, "inbound gameplay queue end marker missing");
const queueSource = html.slice(start, end);

function makeHarness() {
  let nextTimerId = 1;
  const timers = [];
  const forwarded = [];
  const context = {
    console,
    Uint8Array,
    window: {},
    ak_joined: true,
    ak_hard_disconnected: false,
    Packet: function Packet() {},
    ak_packet_ptr: 1,
    ak_inbound_gameplay_flush_timer: 0,
    ak_inbound_gameplay_flush_delay_ms: 0,
    __now: 0,
    __advanceOnForwardMs: 0,
    setTimeout(fn, delay) {
      const timer = { id: nextTimerId++, fn, delay };
      timers.push(timer);
      return timer.id;
    },
    clearTimeout(id) {
      const idx = timers.findIndex((timer) => timer.id === id);
      if (idx >= 0)
        timers.splice(idx, 1);
    },
    performance: {
      now() {
        return context.__now;
      },
    },
    ForwardNetPacketToGame(msg) {
      forwarded.push(Array.from(msg));
      context.__now += context.__advanceOnForwardMs;
    },
  };
  vm.createContext(context);
  vm.runInContext(queueSource, context, { filename: "game_web_inbound_queue.js" });
  context.ak_joined = true;
  context.ak_hard_disconnected = false;
  context.Packet = function Packet() {};
  context.ak_packet_ptr = 1;
  return {
    context,
    forwarded,
    timers,
    runNextTimer() {
      assert(timers.length > 0, "expected queued timer");
      timers.sort((a, b) => a.delay - b.delay || a.id - b.id);
      const timer = timers.shift();
      context.__now += timer.delay;
      timer.fn();
      return timer.delay;
    },
  };
}

function qSnapshot(seq, stateFlags) {
  const entitySize = 76;
  const msg = new Uint8Array(12 + entitySize);
  msg[0] = 0x71; // q
  msg[1] = 9;
  msg[2] = seq & 0xff;
  msg[3] = (seq >> 8) & 0xff;
  msg[8] = 1;
  msg[10] = entitySize;
  msg[12 + 9] = stateFlags || 0;
  return msg;
}

function packet(ch) {
  return new Uint8Array([ch.charCodeAt(0)]);
}

function testBarrierDoesNotForwardInline() {
  const h = makeHarness();
  h.context.EnqueueInboundGameplayPacket(qSnapshot(1));
  h.context.EnqueueInboundGameplayPacket(qSnapshot(2));
  h.context.EnqueueInboundGameplayPacket(packet("i"));

  assert.strictEqual(h.forwarded.length, 0, "barrier packet forwarded inline");
  assert.strictEqual(h.timers.length, 1, "expected one scheduled drain");
  assert.strictEqual(h.timers[0].delay, 0, "barrier should upgrade drain to immediate task");

  h.runNextTimer();
  assert.strictEqual(h.forwarded.length, 2);
  assert.strictEqual(h.forwarded[0][0], "q".charCodeAt(0));
  assert.strictEqual(h.forwarded[0][2], 2, "snapshot run should collapse latest-wins");
  assert.strictEqual(h.forwarded[1][0], "i".charCodeAt(0));
  assert.strictEqual(h.context.window.__ak_diag.inboundGameplayBarrierDeferred, 1);
}

function testBudgetYieldsAcrossTasks() {
  const h = makeHarness();
  h.context.__advanceOnForwardMs = 3;
  h.context.EnqueueInboundGameplayPacket(qSnapshot(1));
  h.context.EnqueueInboundGameplayPacket(packet("i"));
  h.context.EnqueueInboundGameplayPacket(packet("h"));

  h.runNextTimer();
  assert.strictEqual(h.forwarded.length, 1);
  assert.strictEqual(h.forwarded[0][0], "q".charCodeAt(0));
  assert.strictEqual(h.context.window.__ak_diag.inboundGameplayBudgetYields, 1);

  h.runNextTimer();
  assert.strictEqual(h.forwarded.length, 2);
  assert.strictEqual(h.forwarded[1][0], "i".charCodeAt(0));
  assert.strictEqual(h.context.window.__ak_diag.inboundGameplayBudgetYields, 2);

  h.runNextTimer();
  assert.strictEqual(h.forwarded.length, 3);
  assert.strictEqual(h.forwarded[2][0], "h".charCodeAt(0));
}

function testLatencyControlStillBypassesQueue() {
  const h = makeHarness();
  h.context.EnqueueInboundGameplayPacket(qSnapshot(1));
  h.context.EnqueueInboundGameplayPacket(packet("l"));

  assert.strictEqual(h.forwarded.length, 1);
  assert.strictEqual(h.forwarded[0][0], "l".charCodeAt(0));
  assert.strictEqual(h.timers.length, 1);
  assert.strictEqual(h.timers[0].delay, 8);

  h.runNextTimer();
  assert.strictEqual(h.forwarded.length, 2);
  assert.strictEqual(h.forwarded[1][0], "q".charCodeAt(0));
}

function testTombstoneSnapshotIsBarrierButNotInline() {
  const h = makeHarness();
  h.context.EnqueueInboundGameplayPacket(qSnapshot(1, 0x02));

  assert.strictEqual(h.forwarded.length, 0);
  assert.strictEqual(h.timers.length, 1);
  assert.strictEqual(h.timers[0].delay, 0);
  h.runNextTimer();
  assert.strictEqual(h.forwarded.length, 1);
  assert.strictEqual(h.forwarded[0][0], "q".charCodeAt(0));
}

function testOverflowDoesNotWipeQueuedBarriers() {
  const h = makeHarness();
  h.context.AK_INBOUND_GAMEPLAY_QUEUE_MAX = 2;
  h.context.EnqueueInboundGameplayPacket(qSnapshot(1));
  h.context.EnqueueInboundGameplayPacket(packet("i"));
  h.context.EnqueueInboundGameplayPacket(packet("h"));

  assert.strictEqual(h.context.window.__ak_diag.inboundGameplayQueueDropCount, 1);
  h.runNextTimer();
  assert.deepStrictEqual(h.forwarded.map((msg) => String.fromCharCode(msg[0])), ["i", "h"]);
}

testBarrierDoesNotForwardInline();
testBudgetYieldsAcrossTasks();
testLatencyControlStillBypassesQueue();
testTombstoneSnapshotIsBarrierButNotInline();
testOverflowDoesNotWipeQueuedBarriers();
console.log("game_web inbound budget tests passed");
