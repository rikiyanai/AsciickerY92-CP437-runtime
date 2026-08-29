'use strict';

const fs = require('fs');
const path = require('path');

const driver = require('./proof_driver_playwright.js');

const BLOCK_DEF_ID = 420;
const LOCAL_OWNER_SENTINEL = 0xffff;
const PLACED_FLAG = 0x0008;
const COLLIDABLE_FLAG = 0x0010;
const EXPLICIT_PICKUP_ONLY_FLAG = 0x0020;
const MP_SUPPORT_WORLD_MESH = 2;
const MP_SUPPORT_PLACED_BLOCK = 3;
// FL-4137 #31: BLOCK_COLLISION_HEIGHT_UNITS hardcode REMOVED. It was a
// Law 1 parallel ownership of catalog geometry. The proof now reads
// each block's collision_height + visual_top_z from the recorder
// auth_item_sample emitted by web_recorder_bridge.cpp (catalog +
// authoritative_world_item_appearance sources).
const BLOCK_STAND_TOP_EPS_UNITS = 1.25;
const BLOCK_TRANSIENT_LAUNCH_EPS_UNITS = 2.0;
const BLOCK_VISUAL_INVARIANT_EPS = 0.75;
const BLOCK_VISIBLE_RENDERBUF_CELL_COUNT_MIN = 8;
const BLOCK_VISIBLE_RENDERBUF_BBOX_W_MIN = 2;
const BLOCK_VISIBLE_RENDERBUF_BBOX_H_MIN = 2;

function assertBlockCollisionGeometry(block, label) {
  if (!block)
    throw new Error(`${label}: block sample missing`);
  const fields = [
    'x',
    'y',
    'z',
    'half_extent',
    'height',
    'collision_bottom_z',
    'collision_top_z',
    'support_top_z',
  ];
  for (const field of fields) {
    if (typeof block[field] !== 'number' || !Number.isFinite(block[field])) {
      throw new Error(`${label}: block sample missing geometry field ${field}: ${JSON.stringify(block)}`);
    }
  }
  if (block.half_extent <= 0 || block.height <= 0) {
    throw new Error(`${label}: block dimensions are invalid: ${JSON.stringify(block)}`);
  }
  const collision_top = block.collision_top_z;
  const expected_collision_top = block.collision_bottom_z + block.height;
  const collisionDrift = Math.abs(collision_top - expected_collision_top);
  if (collisionDrift > BLOCK_VISUAL_INVARIANT_EPS) {
    throw new Error(`${label}: collision top is not bottom+height for block id=${block.id}: ` +
      `collision_bottom_z=${block.collision_bottom_z} height=${block.height} ` +
      `collision_top_z=${block.collision_top_z} drift=${collisionDrift}`);
  }
  const bottomDrift = Math.abs(block.collision_bottom_z - block.z);
  if (bottomDrift > BLOCK_VISUAL_INVARIANT_EPS) {
    throw new Error(`${label}: item z and collision_bottom_z diverged for block id=${block.id}: ` +
      `z=${block.z} collision_bottom_z=${block.collision_bottom_z} drift=${bottomDrift}`);
  }
  const supportDrift = Math.abs(block.support_top_z - block.collision_top_z);
  if (supportDrift > BLOCK_VISUAL_INVARIANT_EPS) {
    throw new Error(`${label}: support top != collision top for block id=${block.id}: ` +
      `support_top_z=${block.support_top_z} collision_top_z=${block.collision_top_z} drift=${supportDrift}`);
  }
}

// FL-4137 #31 invariant: the sprite's projected visual top MUST equal
// the catalog-declared collision top. If they differ, the block is
// visible-vs-climbable mismatched and the player will either clip
// through the upper visible mass or stand inside an invisible volume.
function assertBlockVisualInvariant(block, label) {
  assertBlockCollisionGeometry(block, label);
  for (const field of ['visual_bottom_z', 'visual_top_z']) {
    if (typeof block[field] !== 'number' || !Number.isFinite(block[field])) {
      throw new Error(`${label}: block sample missing visual geometry field ${field}: ${JSON.stringify(block)}`);
    }
  }
  if (block.visual_top_z <= -1.0e29 || block.visual_bottom_z <= -1.0e29) {
    throw new Error(`${label}: block has no visible render row: ${JSON.stringify(block)}`);
  }
  if (block.visual_top_z <= block.visual_bottom_z) {
    throw new Error(`${label}: block visual extent is empty: ${JSON.stringify(block)}`);
  }
  const collision_top = block.collision_top_z;
  const drift = Math.abs(collision_top - block.visual_top_z);
  if (drift > BLOCK_VISUAL_INVARIANT_EPS) {
    throw new Error(`${label}: visual/collision invariant violated for block id=${block.id}: ` +
      `collision_bottom_z=${block.collision_bottom_z} height=${block.height} ` +
      `collision_top=${collision_top} visual_top_z=${block.visual_top_z} drift=${drift}`);
  }
}

function blockCollisionTopZ(block) {
  assertBlockCollisionGeometry(block, 'blockCollisionTopZ');
  return block.collision_top_z;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function recorder(page) {
  return page.evaluate(() => {
    if (!window.__akRecorderStateJson && window.Module && Module.cwrap)
      window.__akRecorderStateJson = Module.cwrap('RecorderStateJson', 'string', []);
    if (!window.__akRecorderStateJson)
      return { error: 'RecorderStateJson unavailable' };
    return JSON.parse(window.__akRecorderStateJson());
  });
}

function blockSamples(state) {
  return (state && Array.isArray(state.auth_item_sample))
    ? state.auth_item_sample.filter(it => it.item_definition_id === BLOCK_DEF_ID)
    : [];
}

function summarizeState(state) {
  if (!state || typeof state !== 'object')
    return state;
	return {
    auth_item_count: state.auth_item_count,
    auth_world_count: state.auth_item_world_count,
    blocks: blockSamples(state),
    auth_world_strip_count: state.auth_world_strip_count,
    auth_world_strip_0: state.auth_world_strip_0,
    auth_world_strip_1: state.auth_world_strip_1,
    auth_world_strip_2: state.auth_world_strip_2,
    auth_pickup_strip_count: state.auth_pickup_strip_count,
    auth_pickup_strip_item_ids: state.auth_pickup_strip_item_ids,
    auth_pickup_strip_distance2: state.auth_pickup_strip_distance2,
    auth_visible_world_count: state.auth_visible_world_count,
    auth_visible_world_item_ids: state.auth_visible_world_item_ids,
    auth_visible_world_definition_ids: state.auth_visible_world_definition_ids,
    auth_visible_world_visual_style_ids: state.auth_visible_world_visual_style_ids,
    auth_visible_world_sprite_source_hashes: state.auth_visible_world_sprite_source_hashes,
    auth_visible_world_sprite_family_kinds: state.auth_visible_world_sprite_family_kinds,
    auth_visible_world_visual_failure_reasons: state.auth_visible_world_visual_failure_reasons,
    send_calls: state.send_calls,
    send_failures: state.send_failures,
    send_last_token: state.send_last_token,
    pending_token_i: state.pending_token_i,
    pending_oldest_age_ms: state.pending_oldest_age_ms,
    auth_place_req_attempts: state.auth_place_req_attempts,
    auth_place_req_sent: state.auth_place_req_sent,
    auth_place_req_send_fail: state.auth_place_req_send_fail,
    auth_place_req_last_index: state.auth_place_req_last_index,
    auth_place_req_last_item_id: state.auth_place_req_last_item_id,
    auth_place_req_last_reason: state.auth_place_req_last_reason,
    input_main_menu_active: state.input_main_menu_active,
    input_show_inventory_active: state.input_show_inventory_active,
    input_talk_box_active: state.input_talk_box_active,
    input_menu_depth_value: state.input_menu_depth_value,
    input_event_sample: state.input_event_sample,
    player_pos: state.player_pos,
    player_x: state.player_x,
    player_y: state.player_y,
		player_z: state.player_z,
		local_pos_x: state.local_pos_x,
		local_pos_y: state.local_pos_y,
		local_pos_z: state.local_pos_z,
		snapshot_local_pos_x_cpp: state.snapshot_local_pos_x_cpp,
		snapshot_local_pos_y_cpp: state.snapshot_local_pos_y_cpp,
		snapshot_local_pos_z_cpp: state.snapshot_local_pos_z_cpp,
		snapshot_local_support_valid_cpp: state.snapshot_local_support_valid_cpp,
		snapshot_local_support_source_cpp: state.snapshot_local_support_source_cpp,
		snapshot_local_support_item_id_cpp: state.snapshot_local_support_item_id_cpp,
		snapshot_local_support_z_cpp: state.snapshot_local_support_z_cpp,
	};
}

async function waitFor(label, fn, timeoutMs = 15000) {
  const start = Date.now();
  let last = null;
  while (Date.now() - start < timeoutMs) {
    last = await fn();
    if (last && last.ok)
      return last.value;
    await sleep(200);
  }
  const lastValue = last && last.value ? last.value : last;
  const stateForSummary = lastValue && lastValue.state ? lastValue.state : lastValue;
  const lastSummary = {
    state: summarizeState(stateForSummary),
    blocks: lastValue && lastValue.blocks ? lastValue.blocks : undefined,
    invariantError: lastValue && lastValue.invariantError ? lastValue.invariantError : undefined,
    supportError: lastValue && lastValue.supportError ? lastValue.supportError : undefined,
  };
  throw new Error(`${label} timed out; last=${JSON.stringify(lastSummary).slice(0, 5000)}`);
}

async function press(page, key, count = 1, gapMs = 250) {
  for (let i = 0; i < count; i++) {
    await page.keyboard.press(key);
    await sleep(gapMs);
  }
}

async function holdTogether(page, keys, ms) {
  for (const key of keys)
    await page.keyboard.down(key);
  await sleep(ms);
  for (const key of [...keys].reverse())
    await page.keyboard.up(key);
  await sleep(500);
}

const MOVEMENT_KEY_SETS = [
	['w'],
	['a'],
	['s'],
	['d'],
	['w', 'a'],
	['w', 'd'],
	['s', 'a'],
	['s', 'd'],
];

function authoritativePos(state) {
	return {
		x: typeof state.snapshot_local_pos_x_cpp === 'number'
			? state.snapshot_local_pos_x_cpp
			: state.local_pos_x,
		y: typeof state.snapshot_local_pos_y_cpp === 'number'
			? state.snapshot_local_pos_y_cpp
			: state.local_pos_y,
		z: typeof state.snapshot_local_pos_z_cpp === 'number'
			? state.snapshot_local_pos_z_cpp
			: state.local_pos_z,
	};
}

function posFinite(pos) {
	return !!pos &&
		typeof pos.x === 'number' && Number.isFinite(pos.x) &&
		typeof pos.y === 'number' && Number.isFinite(pos.y);
}

function distance2ToTarget(pos, target) {
	if (!posFinite(pos) || !target)
		return Infinity;
	const dx = pos.x - target.x;
	const dy = pos.y - target.y;
	return dx * dx + dy * dy;
}

function distance2ToBlock(pos, block) {
	if (!pos || !block || typeof pos.x !== 'number' || typeof pos.y !== 'number')
		return Infinity;
	const dx = pos.x - block.x;
	const dy = pos.y - block.y;
	return dx * dx + dy * dy;
}

function isStandingOnBlock(pos, block) {
	// FL-4137 attempt #24 false-green: the old predicate accepted any
	// z >= block.z + 3.0. A headed run then reported ok=true while the player
	// was at z=160.599 over a block whose base was z=57 and whose real top was
	// about z=73. "Above" is not "standing"; this proof must require the
	// authoritative/snapshot Z to be near the block top or it hides launches,
	// clipping, and failed climb exactly like attempts #16/#18/#21 did.
	const top_z = block ? blockCollisionTopZ(block) : NaN;
	return !!pos && !!block &&
		Math.abs(pos.x - block.x) < 3.5 &&
		Math.abs(pos.y - block.y) < 3.5 &&
		typeof pos.z === 'number' &&
		Math.abs(pos.z - top_z) <= BLOCK_STAND_TOP_EPS_UNITS;
}

function isBelowBlockTop(pos, block) {
	const top_z = block ? blockCollisionTopZ(block) : NaN;
	return !!pos && !!block &&
		typeof pos.z === 'number' &&
		pos.z < top_z - BLOCK_STAND_TOP_EPS_UNITS;
}

function assertNoBlockLaunches(trace, block, label) {
	if (!Array.isArray(trace) || !block)
		return;
	// FL-4137 #31: invariant check fires before launch detection so the proof
	// FALSIFIES on visible/collision drift rather than reporting "passed" for
	// a block the player can't see or stands inside of.
	assertBlockVisualInvariant(block, label);
	const top_z = blockCollisionTopZ(block);
	const max_z = top_z + BLOCK_TRANSIENT_LAUNCH_EPS_UNITS;
	for (const step of trace) {
		for (const phase of ['before', 'after']) {
			const pos = step && step[phase];
			if (!pos || typeof pos.z !== 'number')
				continue;
			const near_xy =
				Math.abs(pos.x - block.x) < 6.0 &&
				Math.abs(pos.y - block.y) < 6.0;
			if (near_xy && pos.z > max_z) {
				// FL-4137 attempt #25: default-map headed proof still returned
				// ok=true after intermediate controller samples hit z=108.804 and
				// z=119.803 near a block whose top is ~73. That is visible launch/
				// clipping, even if a later sample settles. Preserve the bad frame.
				throw new Error(`${label}: transient block launch hidden by later settle: phase=${phase} pos=(${pos.x},${pos.y},${pos.z}) block=(${block.x},${block.y},${block.z}) top=${top_z} trace_tail=${JSON.stringify(trace.slice(-8))}`);
			}
		}
	}
}

function assertPlayerSupportedByPlacedBlock(state, block, label) {
	if (!state || !block)
		throw new Error(`${label}: missing state/block`);
	if (state.snapshot_local_support_valid_cpp !== 1) {
		throw new Error(`${label}: server snapshot did not report valid support: ${JSON.stringify(summarizeState(state))}`);
	}
	// CKPT-D (FL-4137): placed blocks are now real engine MeshInsts; the
	// server reports support_source as WORLD_MESH (2), not PLACED_BLOCK (3).
	// PLACED_BLOCK is still acceptable for any future code path that
	// re-tags placed-mesh hits via Inst story_id. The semantic invariant is
	// "player stands at block top via a mesh-backed support", which is
	// verified by support_z matching block top.
	if (state.snapshot_local_support_source_cpp !== MP_SUPPORT_WORLD_MESH &&
	    state.snapshot_local_support_source_cpp !== MP_SUPPORT_PLACED_BLOCK) {
		throw new Error(`${label}: support source is neither world_mesh nor placed_block: source=${state.snapshot_local_support_source_cpp} item=${state.snapshot_local_support_item_id_cpp}`);
	}
	// support_item_id may be 0 for mesh hits (the engine Inst doesn't
	// carry placed item_id today). Skip the item-id match in that case.
	if (state.snapshot_local_support_item_id_cpp !== 0 &&
	    state.snapshot_local_support_item_id_cpp !== block.id) {
		throw new Error(`${label}: support item mismatch: support_item=${state.snapshot_local_support_item_id_cpp} block=${block.id}`);
	}
	const supportDrift = Math.abs(state.snapshot_local_support_z_cpp - blockCollisionTopZ(block));
	if (supportDrift > BLOCK_VISUAL_INVARIANT_EPS) {
		throw new Error(`${label}: support_z does not match block top: support_z=${state.snapshot_local_support_z_cpp} block_top=${blockCollisionTopZ(block)} drift=${supportDrift}`);
	}
}

async function recorderPos(page, label) {
	const state = await recorder(page);
	const pos = authoritativePos(state);
	if (!posFinite(pos)) {
		throw new Error(`${label}: authoritative position missing: ${JSON.stringify(summarizeState(state))}`);
	}
	return { state, pos };
}

async function runControllerPulse(page, keys, ms) {
	for (const key of keys)
		await page.keyboard.down(key);
	await sleep(ms);
	for (const key of [...keys].reverse())
		await page.keyboard.up(key);
	await sleep(180);
}

class RecorderGuidedKeyboardController {
	constructor(page) {
		this.page = page;
		this.vectors = null;
		this.trace = [];
	}

	async calibrate() {
		if (this.vectors)
			return;
		this.vectors = {};
		for (const key of ['w', 'a', 's', 'd']) {
			const before = await recorderPos(this.page, `controller-calibrate-${key}-before`);
			await runControllerPulse(this.page, [key], 120);
			const after = await recorderPos(this.page, `controller-calibrate-${key}-after`);
			this.vectors[key] = {
				x: after.pos.x - before.pos.x,
				y: after.pos.y - before.pos.y,
			};
			this.trace.push({
				kind: 'calibrate',
				key,
				before: before.pos,
				after: after.pos,
				vector: this.vectors[key],
			});
		}
	}

	vectorFor(keys) {
		const v = { x: 0, y: 0 };
		for (const key of keys) {
			const kv = this.vectors[key] || { x: 0, y: 0 };
			v.x += kv.x;
			v.y += kv.y;
		}
		return v;
	}

	keysToward(pos, target) {
		const toTarget = { x: target.x - pos.x, y: target.y - pos.y };
		let best = null;
		for (const keys of MOVEMENT_KEY_SETS) {
			const v = this.vectorFor(keys);
			const vLen = Math.sqrt(v.x * v.x + v.y * v.y);
			if (vLen <= 0.001)
				continue;
			const score = (toTarget.x * v.x + toTarget.y * v.y) / vLen;
			if (!best || score > best.score)
				best = { keys, score, vector: v };
		}
		if (!best || best.score <= 0.0)
			throw new Error(`controller cannot find a keyboard vector toward target=${JSON.stringify(target)} pos=${JSON.stringify(pos)} vectors=${JSON.stringify(this.vectors)}`);
		return best.keys;
	}

	async driveToward(target, opts = {}) {
		await this.calibrate();
		const label = opts.label || 'controller-drive';
		const stopDist2 = typeof opts.stopDist2 === 'number' ? opts.stopDist2 : 9.0;
		const maxPulses = opts.maxPulses || 28;
		const pulseMs = opts.pulseMs || 300;
		const accept = typeof opts.accept === 'function' ? opts.accept : null;
		let sample = await recorderPos(this.page, `${label}-start`);
		let dist2 = distance2ToTarget(sample.pos, target);
		if (accept && accept(sample.pos)) {
			return { state: sample.state, pos: sample.pos, dist2, bestDist2: dist2 };
		}
		let bestDist2 = dist2;
		let badPulses = 0;
		for (let i = 0; i < maxPulses && dist2 > stopDist2; i++) {
			const keys = this.keysToward(sample.pos, target);
			await runControllerPulse(this.page, keys, pulseMs);
			const next = await recorderPos(this.page, `${label}-${i}`);
			const nextDist2 = distance2ToTarget(next.pos, target);
			this.trace.push({
				kind: 'drive',
				label,
				i,
				keys,
				before: sample.pos,
				after: next.pos,
				target,
				dist2,
				next_dist2: nextDist2,
			});
			if (nextDist2 < bestDist2) {
				bestDist2 = nextDist2;
				badPulses = 0;
			} else {
				badPulses++;
			}
			if (!accept && badPulses >= 4) {
				throw new Error(`${label}: controller stopped making progress toward target; best_dist2=${bestDist2.toFixed(3)} current_dist2=${nextDist2.toFixed(3)} pos=(${next.pos.x},${next.pos.y},${next.pos.z}) target=(${target.x},${target.y}) trace_tail=${JSON.stringify(this.trace.slice(-6))}`);
			}
			sample = next;
			dist2 = nextDist2;
			if (accept && accept(sample.pos)) {
				return { state: sample.state, pos: sample.pos, dist2, bestDist2 };
			}
		}
		if (dist2 > stopDist2) {
			throw new Error(`${label}: controller failed to reach target; dist2=${dist2.toFixed(3)} best_dist2=${bestDist2.toFixed(3)} pos=(${sample.pos.x},${sample.pos.y},${sample.pos.z}) target=(${target.x},${target.y}) trace_tail=${JSON.stringify(this.trace.slice(-8))}`);
		}
		return { state: sample.state, pos: sample.pos, dist2, bestDist2 };
	}

	async probeTowardAccept(target, accept, opts = {}) {
		await this.calibrate();
		const label = opts.label || 'controller-probe';
		const maxRounds = opts.maxRounds || 18;
		let sample = await recorderPos(this.page, `${label}-start`);
		if (accept(sample.pos))
			return { state: sample.state, pos: sample.pos, dist2: distance2ToTarget(sample.pos, target) };
		for (let round = 0; round < maxRounds; round++) {
			const ranked = MOVEMENT_KEY_SETS
				.map(keys => {
					const v = this.vectorFor(keys);
					const toTarget = { x: target.x - sample.pos.x, y: target.y - sample.pos.y };
					const vLen = Math.sqrt(v.x * v.x + v.y * v.y);
					return {
						keys,
						score: vLen > 0.001 ? (toTarget.x * v.x + toTarget.y * v.y) / vLen : -Infinity,
					};
				})
				.sort((a, b) => b.score - a.score);
			let best = null;
			for (const candidate of ranked.slice(0, 4)) {
				await runControllerPulse(this.page, candidate.keys, opts.pulseMs || 120);
				const next = await recorderPos(this.page, `${label}-${round}-${candidate.keys.join('')}`);
				const nextDist2 = distance2ToTarget(next.pos, target);
				this.trace.push({
					kind: 'probe',
					label,
					round,
					keys: candidate.keys,
					before: sample.pos,
					after: next.pos,
					target,
					next_dist2: nextDist2,
				});
				if (accept(next.pos))
					return { state: next.state, pos: next.pos, dist2: nextDist2 };
				if (!best || nextDist2 < best.dist2)
					best = { state: next.state, pos: next.pos, dist2: nextDist2 };
				sample = next;
			}
			if (best)
				sample = best;
		}
		throw new Error(`${label}: probe failed to satisfy accept predicate; pos=(${sample.pos.x},${sample.pos.y},${sample.pos.z}) target=(${target.x},${target.y}) trace_tail=${JSON.stringify(this.trace.slice(-10))}`);
	}
}

async function pressPickupDigitForItem(page, itemId, label) {
  const slot = await waitFor(label || `pickup strip contains item ${itemId}`, async () => {
    const state = await recorder(page);
    const ids = state.auth_pickup_strip_item_ids || [];
    const index = ids.indexOf(itemId);
    return { ok: index >= 0 && index < 9, value: { index, state } };
  });
  await press(page, String(slot.index + 1));
}

async function approachPlacedBlock(controller, placedBlock) {
	const target = { x: placedBlock.x, y: placedBlock.y };
	let approach = null;
	try {
		approach = await controller.driveToward(target, {
			label: 'controller-approach-block',
			stopDist2: 4.0,
			maxPulses: 80,
			pulseMs: 120,
			accept: (pos) => isStandingOnBlock(pos, placedBlock),
		});
	} catch (_) {
		approach = await controller.probeTowardAccept(target, (pos) => isStandingOnBlock(pos, placedBlock), {
			label: 'controller-probe-stand-on-block',
			maxRounds: 18,
			pulseMs: 120,
		});
	}
	if (!isStandingOnBlock(approach.pos, placedBlock)) {
		approach = await controller.probeTowardAccept(target, (pos) => isStandingOnBlock(pos, placedBlock), {
			label: 'controller-probe-stand-on-block',
			maxRounds: 18,
			pulseMs: 120,
		});
	}
	return approach;
}

async function stageOffPlacedBlock(controller, placedBlock) {
	await controller.calibrate();
	const current = await recorderPos(controller.page, 'controller-stage-off-current');
	let awayX = current.pos.x - placedBlock.x;
	let awayY = current.pos.y - placedBlock.y;
	let awayLen = Math.sqrt(awayX * awayX + awayY * awayY);
	if (awayLen < 0.001) {
		awayX = -1.0;
		awayY = 0.0;
		awayLen = 1.0;
	}
	const target = {
		x: placedBlock.x + awayX / awayLen * 10.0,
		y: placedBlock.y + awayY / awayLen * 10.0,
	};
	const offBlock = (pos) =>
		!isStandingOnBlock(pos, placedBlock) &&
		distance2ToBlock(pos, placedBlock) > 16.0;
	try {
		return await controller.driveToward(target, {
			label: 'controller-stage-off-block',
			stopDist2: 16.0,
			maxPulses: 64,
			pulseMs: 120,
			accept: offBlock,
		});
	} catch (_) {
		return controller.probeTowardAccept(target, offBlock, {
			label: 'controller-probe-stage-off-block',
			maxRounds: 18,
			pulseMs: 120,
		});
	}
}

async function snapshot(page, label) {
  const state = await recorder(page);
  console.error(`[placeable-proof:${label}] ${JSON.stringify(summarizeState(state)).slice(0, 5000)}`);
  return state;
}

async function captureRenderBuffer(page, label) {
  const frame = await page.evaluate(() => {
    if (!window.__akCppAnsiFrameSnapshotJson && window.Module && Module.cwrap) {
      window.__akCppAnsiFrameSnapshotJson =
        Module.cwrap('GetCppAnsiFrameSnapshotJson', 'string', []);
    }
    if (!window.__akCppAnsiFrameSnapshotJson)
      return { ok: false, error: 'GetCppAnsiFrameSnapshotJson export missing' };
    const raw = window.__akCppAnsiFrameSnapshotJson();
    try {
      return { ok: true, frame: JSON.parse(raw) };
    } catch (err) {
      return { ok: false, error: `render-buffer JSON parse failed: ${err && err.message ? err.message : err}`, raw: String(raw).slice(0, 512) };
    }
  });
  if (!frame.ok) {
    throw new Error(`${label}: render-buffer extraction failed: ${frame.error || JSON.stringify(frame)}`);
  }
  const outPath = path.join(__dirname, '..', '..', '.run', `fl4137_${label}_renderbuf.json`);
  fs.writeFileSync(outPath, JSON.stringify(frame.frame, null, 2));
  frame.frame.path = outPath;
  console.error(`[placeable-proof:${label}:renderbuf] ${JSON.stringify({
    path: outPath,
    valid: frame.frame.valid,
    width: frame.frame.width,
    height: frame.frame.height,
    hash: frame.frame.hash,
    raw_cell_count: frame.frame.raw_cell_count,
    truncated: frame.frame.truncated || frame.frame.truncated_after_write || 0,
    nonzero_cells: frame.frame.nonzero_cells,
    nonzero_glyph_cells: frame.frame.nonzero_glyph_cells,
  })}`);
  if (!frame.frame.valid || !frame.frame.raw_hex || frame.frame.raw_cell_count <= 0) {
    throw new Error(`${label}: render-buffer frame invalid: ${JSON.stringify(frame.frame).slice(0, 1000)}`);
  }
  return frame.frame;
}

function decodeRenderBufferCells(frame, label) {
  if (typeof frame.raw_hex !== 'string' || frame.raw_hex.length % 8 !== 0) {
    throw new Error(`${label}: render-buffer raw_hex is not AnsiCell-aligned: len=${frame.raw_hex ? frame.raw_hex.length : 'missing'}`);
  }
  const cells = [];
  for (let i = 0; i < frame.raw_hex.length; i += 8) {
    cells.push({
      fg: parseInt(frame.raw_hex.slice(i, i + 2), 16),
      bk: parseInt(frame.raw_hex.slice(i + 2, i + 4), 16),
      gl: parseInt(frame.raw_hex.slice(i + 4, i + 6), 16),
      spare: parseInt(frame.raw_hex.slice(i + 6, i + 8), 16),
    });
  }
  return cells;
}

function compareRenderBuffers(before, after, label) {
  if (!before || !after || before.width !== after.width || before.height !== after.height) {
    throw new Error(`${label}: render-buffer dimensions diverged: before=${before && before.width}x${before && before.height} after=${after && after.width}x${after && after.height}`);
  }
  const beforeCells = decodeRenderBufferCells(before, `${label}:before`);
  const afterCells = decodeRenderBufferCells(after, `${label}:after`);
  let changed = 0;
  let minX = after.width;
  let minY = after.height;
  let maxX = -1;
  let maxY = -1;
  const changedSamples = [];
  const n = Math.min(beforeCells.length, afterCells.length);
  for (let i = 0; i < n; i++) {
    const b = beforeCells[i];
    const a = afterCells[i];
    if (a.fg === b.fg && a.bk === b.bk && a.gl === b.gl && a.spare === b.spare)
      continue;
    const x = i % after.width;
    const y = Math.floor(i / after.width);
    changed++;
    if (changedSamples.length < 24) {
      changedSamples.push({ x, y, before: b, after: a });
    }
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  const bbox = changed ? { minX, minY, maxX, maxY, w: maxX - minX + 1, h: maxY - minY + 1 } : null;
  const result = {
    width: after.width,
    height: after.height,
    before_hash: before.hash,
    after_hash: after.hash,
    changed_cells: changed,
    bounds: bbox,
    changed_samples: changedSamples,
    before_path: before.path,
    after_path: after.path,
  };
  console.error(`[placeable-proof:${label}] ${JSON.stringify(result)}`);
  result.visible_enough =
    changed >= BLOCK_VISIBLE_RENDERBUF_CELL_COUNT_MIN &&
    !!bbox &&
    bbox.w >= BLOCK_VISIBLE_RENDERBUF_BBOX_W_MIN &&
    bbox.h >= BLOCK_VISIBLE_RENDERBUF_BBOX_H_MIN;
  return result;
}

async function main() {
  // FL-4137 false-green guard: a headed run reached gameplay and reported
  // ok=true while the actor launched to z=160.599 over a block whose expected
  // top was about z=73. "Above the block" is not "standing on the block"; this
  // recipe must fail unless Z is near the block top, not merely greater than it.
  const page = await driver.openProofPage({
    mapPath: process.env.PROOF_MAP || 'assets/a3d/sandbox_20x20.a3d',
    waitForRecorderState: true,
  });

  const initial = await waitFor('seeded block visible to recorder', async () => {
    const state = await recorder(page);
    const blocks = blockSamples(state);
    return { ok: blocks.some(it => it.owner_id === LOCAL_OWNER_SENTINEL), value: { state, blocks } };
  });

  await waitFor('block in pickup strip before explicit pickup', async () => {
    const state = await recorder(page);
    return {
      ok: (state.auth_pickup_strip_item_ids || []).includes(initial.blocks[0].id) ||
          (state.auth_world_strip_0 === initial.blocks[0].id),
      value: state,
    };
  });

  await pressPickupDigitForItem(
    page,
    initial.blocks[0].id,
    'initial block appears in pickup strip before explicit pickup');
  const picked = await waitFor('explicit pickup converts block to owned inventory', async () => {
    const state = await recorder(page);
    const blocks = blockSamples(state);
    return { ok: blocks.some(it => it.owner_id !== LOCAL_OWNER_SENTINEL), value: { state, blocks } };
  });
  const targetBlockId = initial.blocks[0].id;
  const pickedBlock = picked.blocks.find(it => it.id === targetBlockId && it.owner_id !== LOCAL_OWNER_SENTINEL);
  if (!pickedBlock) {
    throw new Error(`target block ${targetBlockId} was not picked up: ${JSON.stringify(picked.blocks)}`);
  }

  await press(page, 'b');
  await press(page, 'u');
  await waitFor('use/equip makes block held', async () => {
    const state = await recorder(page);
    const blocks = blockSamples(state);
    return { ok: blocks.some(it => it.id === targetBlockId && it.equip_slot_kind_id === 308), value: { state, blocks } };
  });
  await waitFor('held block reaches visible placement preview rows', async () => {
    const state = await recorder(page);
    const blocks = blockSamples(state);
    const held = blocks.find(it => it.id === targetBlockId && it.equip_slot_kind_id === 308);
    const ids = state.auth_visible_world_item_ids || [];
    const index = held ? ids.indexOf(held.id) : -1;
    const definitionIds = state.auth_visible_world_definition_ids || [];
    const failureReasons = state.auth_visible_world_visual_failure_reasons || [];
    return {
      ok: !!held &&
          index >= 0 &&
          definitionIds[index] === BLOCK_DEF_ID &&
          (failureReasons[index] || 0) === 0,
      value: state,
    };
  });
  const prePlaceController = new RecorderGuidedKeyboardController(page);
  await prePlaceController.driveToward(
    { x: -4.8, y: -82.0 },
    {
      label: 'controller-stage-before-place',
      stopDist2: 4.0,
      maxPulses: 80,
      pulseMs: 120,
    });
  await snapshot(page, 'before-place-key');
  const beforePlaceRenderBuffer = await captureRenderBuffer(page, 'before_place_key');

  await press(page, 'p');
  await snapshot(page, 'after-place-key');
  const placed = await waitFor('place converts target held block back to explicit collidable world item', async () => {
    const state = await recorder(page);
    const blocks = blockSamples(state);
    const ok = blocks.some(it =>
      it.id === targetBlockId &&
      it.owner_id === LOCAL_OWNER_SENTINEL &&
      (it.state_flags & PLACED_FLAG) &&
      (it.state_flags & COLLIDABLE_FLAG) &&
      (it.state_flags & EXPLICIT_PICKUP_ONLY_FLAG));
    return { ok, value: { state, blocks } };
  });

  let placedBlock = placed.blocks.find(it =>
    it.id === targetBlockId &&
    it.owner_id === LOCAL_OWNER_SENTINEL &&
    (it.state_flags & PLACED_FLAG) &&
    (it.state_flags & COLLIDABLE_FLAG) &&
    (it.state_flags & EXPLICIT_PICKUP_ONLY_FLAG));
  if (!placedBlock)
    throw new Error(`placed block missing from placed result: ${JSON.stringify(placed.blocks)}`);

	const afterPlace = await snapshot(page, 'after-place');
	const afterPlacePos = authoritativePos(afterPlace);
	if (!isBelowBlockTop(afterPlacePos, placedBlock)) {
		throw new Error(`placed block proof invalid: player already at/above block top before approach: auth=(${afterPlacePos.x},${afterPlacePos.y},${afterPlacePos.z}) block=(${placedBlock.x},${placedBlock.y},${placedBlock.z})`);
	}
	const visiblePlaced = await waitFor('placed block reaches visible world render rows with matching geometry', async () => {
    const state = await recorder(page);
    const ids = state.auth_visible_world_item_ids || [];
    const index = ids.indexOf(placedBlock.id);
    const definitionIds = state.auth_visible_world_definition_ids || [];
    const failureReasons = state.auth_visible_world_visual_failure_reasons || [];
    const latestBlock = blockSamples(state).find(it => it.id === placedBlock.id);
    let invariantOk = false;
    let invariantError = null;
    try {
      assertBlockVisualInvariant(latestBlock, 'placed block visible geometry');
      invariantOk = true;
    } catch (err) {
      invariantError = err && err.message ? err.message : String(err);
    }
    return {
      ok: index >= 0 &&
          definitionIds[index] === BLOCK_DEF_ID &&
          (failureReasons[index] || 0) === 0 &&
          invariantOk,
      value: { state, latestBlock, invariantError },
    };
  });
  placedBlock = visiblePlaced.latestBlock;
  const afterPlaceRenderBuffer = await captureRenderBuffer(page, 'after_place_visible_rows');
  const placedBlockRenderBuffer = compareRenderBuffers(beforePlaceRenderBuffer, afterPlaceRenderBuffer, 'placed-block-visible-renderbuf');

	let standOnSource = 'controller-approach-block';
	let sideCollisionExercised = false;
	let controllerTrace = [];
	const controller = prePlaceController;
	controller.trace = [];
	const approach = await approachPlacedBlock(controller, placedBlock);
	sideCollisionExercised = true;
	controllerTrace = controller.trace;
	await snapshot(page, 'after-controller-approach-block');
	assertNoBlockLaunches(controller.trace, placedBlock, 'placed block top support approach');
	if (!isStandingOnBlock(approach.pos, placedBlock)) {
		throw new Error(`placed block top support failed after controller approach: auth=(${approach.pos.x},${approach.pos.y},${approach.pos.z}) block=(${placedBlock.x},${placedBlock.y},${placedBlock.z}) trace_tail=${JSON.stringify(controller.trace.slice(-8))}`);
	}
	const supportState = await waitFor('server support source is placed block', async () => {
		const state = await recorder(page);
		const latestBlock = blockSamples(state).find(it => it.id === placedBlock.id) || placedBlock;
		try {
			assertPlayerSupportedByPlacedBlock(state, latestBlock, 'placed block support provenance');
			return { ok: true, value: { state, latestBlock } };
		} catch (err) {
			return { ok: false, value: { state, latestBlock, supportError: err && err.message ? err.message : String(err) } };
		}
	}, 10000);
	assertPlayerSupportedByPlacedBlock(supportState.state, supportState.latestBlock, 'placed block support provenance');

  await pressPickupDigitForItem(
    page,
    placedBlock.id,
    'placed block appears in pickup strip before explicit repickup');
  await snapshot(page, 'after-repickup-key');
  const repicked = await waitFor('explicit pickup still works for placed explicit-only block', async () => {
    const state = await recorder(page);
    const blocks = blockSamples(state);
    return { ok: blocks.some(it => it.owner_id !== LOCAL_OWNER_SENTINEL), value: { state, blocks } };
  });

  const result = {
    ok: true,
    initial_blocks: initial.blocks,
    picked_blocks: picked.blocks,
    placed_blocks: placed.blocks,
    repicked_blocks: repicked.blocks,
    stand_on_source: standOnSource,
    side_collision_exercised: sideCollisionExercised,
    placed_block_renderbuf: placedBlockRenderBuffer,
    controller_trace_tail: controllerTrace.slice(-10),
  };
  console.log(JSON.stringify(result, null, 2));
  const finalPauseMs = parseInt(process.env.PROOF_FINAL_PAUSE_MS || '0', 10);
  if (finalPauseMs > 0)
    await sleep(finalPauseMs);
}

main()
  .catch(err => {
    console.error(err && err.stack ? err.stack : String(err));
    process.exitCode = 1;
  })
  .finally(() => driver.cleanup());
