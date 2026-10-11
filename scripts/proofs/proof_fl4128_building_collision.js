'use strict';

const driver = require('./proof_driver_playwright.js');

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

function authoritativePos(state) {
	return {
		x: typeof state.snapshot_local_pos_x_cpp === 'number' ? state.snapshot_local_pos_x_cpp : state.local_pos_x,
		y: typeof state.snapshot_local_pos_y_cpp === 'number' ? state.snapshot_local_pos_y_cpp : state.local_pos_y,
		z: typeof state.snapshot_local_pos_z_cpp === 'number' ? state.snapshot_local_pos_z_cpp : state.local_pos_z,
	};
}

function posFinite(pos) {
	return !!pos &&
		typeof pos.x === 'number' && Number.isFinite(pos.x) &&
		typeof pos.y === 'number' && Number.isFinite(pos.y) &&
		typeof pos.z === 'number' && Number.isFinite(pos.z);
}

async function recorderPos(page, label) {
	const state = await recorder(page);
	const pos = authoritativePos(state);
	if (!posFinite(pos))
		throw new Error(`${label}: authoritative position missing: ${JSON.stringify(state).slice(0, 1200)}`);
	return { state, pos };
}

async function runPulse(page, keys, ms) {
	for (const key of keys)
		await page.keyboard.down(key);
	await sleep(ms);
	for (const key of [...keys].reverse())
		await page.keyboard.up(key);
	await sleep(160);
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

class KeyboardController {
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
			const before = await recorderPos(this.page, `calibrate-${key}-before`);
			await runPulse(this.page, [key], 120);
			const after = await recorderPos(this.page, `calibrate-${key}-after`);
			this.vectors[key] = {
				x: after.pos.x - before.pos.x,
				y: after.pos.y - before.pos.y,
			};
			this.trace.push({ kind: 'calibrate', key, before: before.pos, after: after.pos, vector: this.vectors[key] });
		}
	}

	vectorFor(keys) {
		const out = { x: 0, y: 0 };
		for (const key of keys) {
			const v = this.vectors[key] || { x: 0, y: 0 };
			out.x += v.x;
			out.y += v.y;
		}
		return out;
	}

	keysToward(pos, target) {
		const toTarget = { x: target.x - pos.x, y: target.y - pos.y };
		let best = null;
		for (const keys of MOVEMENT_KEY_SETS) {
			const v = this.vectorFor(keys);
			const len = Math.hypot(v.x, v.y);
			if (len <= 0.001)
				continue;
			const score = (toTarget.x * v.x + toTarget.y * v.y) / len;
			if (!best || score > best.score)
				best = { keys, score };
		}
		if (!best || best.score <= 0.0)
			throw new Error(`no keyboard vector toward target=${JSON.stringify(target)} pos=${JSON.stringify(pos)} vectors=${JSON.stringify(this.vectors)}`);
		return best.keys;
	}

	async driveToward(target, opts = {}) {
		await this.calibrate();
		const label = opts.label || 'drive';
		const stopDist2 = typeof opts.stopDist2 === 'number' ? opts.stopDist2 : 9.0;
		const maxPulses = opts.maxPulses || 80;
		const pulseMs = opts.pulseMs || 160;
		let sample = await recorderPos(this.page, `${label}-start`);
		let dist2 = dist2To(sample.pos, target);
		let bestDist2 = dist2;
		let stale = 0;
		for (let i = 0; i < maxPulses && dist2 > stopDist2; i++) {
			const keys = this.keysToward(sample.pos, target);
			await runPulse(this.page, keys, pulseMs);
			const next = await recorderPos(this.page, `${label}-${i}`);
			const nextDist2 = dist2To(next.pos, target);
			this.trace.push({ kind: 'drive', label, i, keys, before: sample.pos, after: next.pos, target, dist2, next_dist2: nextDist2 });
			if (nextDist2 < bestDist2) {
				bestDist2 = nextDist2;
				stale = 0;
			} else {
				stale++;
			}
			sample = next;
			dist2 = nextDist2;
			if (stale >= 8)
				break;
		}
		return { state: sample.state, pos: sample.pos, dist2, bestDist2 };
	}
}

function dist2To(pos, target) {
	const dx = pos.x - target.x;
	const dy = pos.y - target.y;
	return dx * dx + dy * dy;
}

function signedWallDistance(pos, wall) {
	return (pos.x - wall.center.x) * wall.normal.x +
		(pos.y - wall.center.y) * wall.normal.y;
}

function inWallBand(pos, wall, pad = 0.0) {
	return pos.x >= wall.segment.x0 - pad && pos.x <= wall.segment.x1 + pad &&
		pos.y >= wall.segment.y0 - pad && pos.y <= wall.segment.y1 + pad;
}

function assertTraceDoesNotClipWall(trace, wall) {
	for (const step of trace) {
		if (!step || step.kind !== 'drive' || step.label !== 'drive-into-building-wall')
			continue;
		const before = step.before;
		const after = step.after;
		if (!before || !after)
			continue;
		const moveLen = Math.hypot(after.x - before.x, after.y - before.y);
		if (moveLen > 8.0 && (inWallBand(before, wall, 4.0) || inWallBand(after, wall, 4.0))) {
			throw new Error(`FL-4128 building wall collision failed: large near-wall displacement move_len=${moveLen.toFixed(3)} step=${JSON.stringify(step)} wall=${JSON.stringify(wall)}`);
		}
		const afterDistance = signedWallDistance(after, wall);
		if (afterDistance < wall.minBlockedDistance && inWallBand(after, wall, 2.0)) {
			throw new Error(`FL-4128 building wall collision failed: crossed wall plane inside wall band; signed_distance=${afterDistance.toFixed(3)} step=${JSON.stringify(step)} wall=${JSON.stringify(wall)}`);
		}
	}
}

async function main() {
	const wall = {
		mesh: 'sac_and_math_color.akm',
		face: 1793,
		center: { x: -4.09, y: -39.77 },
		normal: { x: 0.988, y: -0.153 },
		from: { x: 1.84, y: -40.69 },
		through: { x: -6.07, y: -39.46 },
		segment: { x0: -5.20, x1: -2.60, y0: -43.10, y1: -34.10 },
		minBlockedDistance: 1.0,
	};

	const page = await driver.openProofPage({
		mapPath: process.env.PROOF_MAP || 'assets/a3d/game_map_y8.a3d',
	});
	const controller = new KeyboardController(page);
	const start = await recorderPos(page, 'start');
	const staged = await controller.driveToward(wall.from, {
		label: 'stage-to-building-wall-front',
		stopDist2: 6.0,
		maxPulses: 110,
		pulseMs: 140,
	});
	const stagedDistance = signedWallDistance(staged.pos, wall);
	if (stagedDistance < wall.minBlockedDistance) {
		throw new Error(`failed to stage in front of wall; signed_distance=${stagedDistance.toFixed(3)} pos=${JSON.stringify(staged.pos)} wall=${JSON.stringify(wall)} trace_tail=${JSON.stringify(controller.trace.slice(-8))}`);
	}

	const approach = await controller.driveToward(wall.through, {
		label: 'drive-into-building-wall',
		stopDist2: 1.0,
		maxPulses: 45,
		pulseMs: 140,
	});
	const finalDistance = signedWallDistance(approach.pos, wall);
	assertTraceDoesNotClipWall(controller.trace, wall);
	const finalInSegmentBand = inWallBand(approach.pos, wall);
	// FL-4128 recurring regression gate: if the authoritative center crosses
	// this wall plane, default-map AKM/building collision is not solid. This is
	// deliberately a real-keyboard recipe over the live default map; no debug
	// teleport or client collision path is allowed to make it pass.
	if (finalDistance < wall.minBlockedDistance && finalInSegmentBand) {
		throw new Error(`FL-4128 building wall collision failed: crossed wall plane; start=${JSON.stringify(start.pos)} staged=${JSON.stringify(staged.pos)} staged_d=${stagedDistance.toFixed(3)} final=${JSON.stringify(approach.pos)} final_d=${finalDistance.toFixed(3)} wall=${JSON.stringify(wall)} trace_tail=${JSON.stringify(controller.trace.slice(-12))}`);
	}

	console.log(JSON.stringify({
		ok: true,
		wall,
		start: start.pos,
		staged: staged.pos,
		staged_signed_distance: stagedDistance,
		final: approach.pos,
		final_signed_distance: finalDistance,
		trace_tail: controller.trace.slice(-12),
	}, null, 2));
	const pause = parseInt(process.env.PROOF_FINAL_PAUSE_MS || '0', 10);
	if (pause > 0)
		await sleep(pause);
}

main()
	.catch(err => {
		console.error(err && err.stack ? err.stack : String(err));
		process.exitCode = 1;
	})
	.finally(() => driver.cleanup());
