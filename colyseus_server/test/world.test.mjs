import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import {
  advanceWorld,
  createNpc,
  applyInput,
  createPlayer,
  createWorld,
  MOVE_SPEED_PER_TICK,
  resolveServerVisualIdentity,
  SERVER_VISUAL_CATALOG,
} from "../src/sim/world.ts";
import { loadA3DTerrain, loadDefaultTerrain } from "../src/sim/terrain.ts";
import {
  AOI_RADIUS,
  isInAoi,
  isProjectileVisibleToActor,
  isVisibleToActor,
  recordRttSample,
} from "../src/rooms/AuthoritativeRoom.ts";
import { FLAT_TERRAIN, hasTerrainLineOfSight } from "../src/sim/terrain.ts";

const DEFAULT_PLAYER_PROFILE = "normal_player.idle_walk.default.unmounted";
const WOLF_MOUNT_PROFILE = "normal_player.idle_walk.default.wolf_mount";
const WOLF_MOUNT_SWORD_PROFILE = "normal_player.idle_walk.default.wolf_mount.normal_sword";
const WOLF_MOUNT_CROSSBOW_PROFILE = "normal_player.idle_walk.default.wolf_mount.weapon_crossbow";

test("rejects direct gameplay truth from client payload", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));

  const result = applyInput(world, "session_a", "player_a", {
    hp: 0,
	    position: { x: 99, y: 0, z: 99 },
	    bundle_visual_id: "wolfie-0002",
	    visual_layers: [{ sourcePath: "assets/sprites/player-body.xp" }],
	    equipment_loadout: ["weapon_crossbow"],
    mount_id: "wolf_mount",
    attack_intent: true,
  });

  assert.equal(result.accepted, false);
  assert.match(result.reason, /forbidden_field/);
  assert.equal(world.actors.get("player_a").hp, 100);
  assert.equal(world.actors.get("player_a").position.x, 0);
  assert.equal(world.actors.get("player_a").bundleVisualId, DEFAULT_PLAYER_PROFILE);
  assert.deepEqual(world.actors.get("player_a").equipmentLoadout, []);
  assert.equal(world.actors.get("player_a").mountId, "");
});

test("server-owned visual contract fields stay on actor model", () => {
  const player = createPlayer("player_a", "session_a");
  player.bundleVisualId = WOLF_MOUNT_CROSSBOW_PROFILE;
  player.equipmentLoadout = ["weapon_crossbow"];
  player.mountId = "wolf_mount";

  assert.equal(player.visualId, DEFAULT_PLAYER_PROFILE);
  assert.equal(player.bundleVisualId, WOLF_MOUNT_CROSSBOW_PROFILE);
  assert.deepEqual(player.equipmentLoadout, ["weapon_crossbow"]);
  assert.equal(player.mountId, "wolf_mount");
});

test("server visual identity is derived by content resolver", () => {
  const player = createPlayer("player_a", "session_a");

  assert.match(SERVER_VISUAL_CATALOG.sourcePath, /colyseus_server\/runtime\/visual_catalog\/current\/actor_visual_profiles\.compiled\.json$/);
  player.mountId = "wolf_mount";
  assert.equal(resolveServerVisualIdentity(player), true);

	  assert.equal(SERVER_VISUAL_CATALOG.profiles.has("101:600:1:950:0:0:0:0:0:0"), true);
	  assert.equal(player.visualId, WOLF_MOUNT_PROFILE);
	  assert.equal(player.bundleVisualId, WOLF_MOUNT_PROFILE);
	  assert.deepEqual(player.visualLayers.map(layer => layer.sourcePath), [
	    "assets/sprites/wolfie-body-rear.xp",
	    "assets/sprites/wolfie-mounted-idle-rider-body.xp",
	    "assets/sprites/wolfie-body-front.xp",
	  ]);

	  player.equipmentLoadout = ["weapon_crossbow"];
	  assert.equal(resolveServerVisualIdentity(player), true);

	  assert.equal(player.visualId, WOLF_MOUNT_CROSSBOW_PROFILE);
	  assert.equal(player.bundleVisualId, WOLF_MOUNT_CROSSBOW_PROFILE);
	  assert.deepEqual(player.visualLayers.map(layer => layer.sourcePath), [
	    "assets/sprites/wolfie-body-rear.xp",
	    "assets/sprites/wolfie-mounted-idle-rider-body.xp",
	    "assets/sprites/wolfie-weapon-crossbow.xp",
	    "assets/sprites/wolfie-body-front.xp",
	  ]);
	  assert.deepEqual(player.visualLayers.map(layer => layer.role), [
	    "mount_rear",
	    "body",
	    "weapon",
	    "mount_front",
	  ]);
	  assert.equal(player.visualLayers[0].sourceLayerIndex, 2);
	  assert.equal(player.visualLayers[0].sourceXpId, "wolfie_body_rear");
	  assert.equal(player.visualLayers[0].sourceXpIndex, 71);
	  assert.equal(player.visualLayers[0].frameMap.length, 144);

  player.mountId = "";
  player.equipmentLoadout = [];
  assert.equal(resolveServerVisualIdentity(player), true);

  assert.equal(player.visualId, DEFAULT_PLAYER_PROFILE);
  assert.equal(player.bundleVisualId, DEFAULT_PLAYER_PROFILE);
});

test("server visual identity fails closed when compiled profile row is missing", () => {
  const player = createPlayer("player_a", "session_a");
  player.mountId = "wolf_mount";

  const result = resolveServerVisualIdentity(player, {
    ...SERVER_VISUAL_CATALOG,
    profiles: new Map(),
  });

	  assert.equal(result, false);
	  assert.equal(player.visualId, "");
	  assert.equal(player.bundleVisualId, "");
	  assert.deepEqual(player.visualLayers, []);
	});

test("server-owned NPC visual identity publishes compiled bee layers", () => {
  const npc = createNpc("npc_bee_1");

  assert.equal(npc.visualId, "normal_player.idle_walk.default.bee_mount");
  assert.equal(npc.bundleVisualId, "normal_player.idle_walk.default.bee_mount");
  assert.deepEqual(npc.visualLayers.map(layer => layer.sourcePath), [
    "assets/sprites/bigbee-0000.xp",
    "assets/sprites/bigbee-0000.xp",
  ]);
  assert.deepEqual(npc.visualLayers.map(layer => layer.role), [
    "mount_rear",
    "body",
  ]);
  assert.equal(npc.visualLayers[1].sourceLayerIndex, 3);
  assert.equal(npc.visualLayers[1].sourceXpId, "bigbee_0000_L3");
  assert.equal(npc.visualLayers[1].sourceXpIndex, 5);
  assert.equal(npc.visualLayers[1].frameMap.length, 48);
});

test("player starts without static mount or weapon loadout", () => {
  const player = createPlayer("player_a", "session_a");

  assert.equal(player.visualId, DEFAULT_PLAYER_PROFILE);
  assert.equal(player.bundleVisualId, DEFAULT_PLAYER_PROFILE);
  assert.deepEqual(player.equipmentLoadout, []);
  assert.deepEqual(player.inventory, []);
  assert.equal(player.mountId, "");
});

test("server-owned pickup equip swap and drop drive visual loadout", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a", -1, 0));
  const actor = world.actors.get("player_a");

  assert.equal(applyInput(world, "session_a", "player_a", {
    pickup_id: "pickup_mount_wolf",
    equip_item_id: "wolf_mount",
  }).accepted, true);
  assert.equal(actor.mountId, "wolf_mount");
  assert.equal(actor.bundleVisualId, WOLF_MOUNT_PROFILE);
  assert.equal(world.worldItems.has("pickup_mount_wolf"), false);

  assert.equal(applyInput(world, "session_a", "player_a", {
    pickup_id: "pickup_weapon_sword",
    equip_item_id: "weapon_sword",
  }).accepted, true);
  assert.deepEqual(actor.equipmentLoadout, ["weapon_sword"]);
  assert.equal(actor.bundleVisualId, WOLF_MOUNT_SWORD_PROFILE);

  assert.equal(applyInput(world, "session_a", "player_a", {
    pickup_id: "pickup_weapon_crossbow",
    swap_item_id: "weapon_crossbow",
  }).accepted, true);
  assert.deepEqual(actor.equipmentLoadout, ["weapon_crossbow"]);
  assert.equal(actor.bundleVisualId, WOLF_MOUNT_CROSSBOW_PROFILE);

  assert.equal(applyInput(world, "session_a", "player_a", {
    drop_item_id: "weapon_crossbow",
  }).accepted, true);
  assert.deepEqual(actor.equipmentLoadout, []);
  assert.equal(actor.inventory.includes("weapon_crossbow"), false);
  assert.equal(Array.from(world.worldItems.values()).some(item => item.itemId === "weapon_crossbow"), true);
  assert.equal(actor.bundleVisualId, WOLF_MOUNT_PROFILE);
});

test("server rejects swap for unowned item before loadout mutation", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  const actor = world.actors.get("player_a");
  actor.inventory.push("weapon_sword");
  actor.equipmentLoadout = ["weapon_sword"];

  const result = applyInput(world, "session_a", "player_a", {
    swap_item_id: "weapon_crossbow",
  });

  assert.equal(result.accepted, false);
  assert.equal(result.reason, "item_not_owned");
  assert.deepEqual(actor.equipmentLoadout, ["weapon_sword"]);
});

test("rejects client-selected attack target id", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", {
    attack_intent: true,
    attack_target_actor_id: "player_b",
  });

  assert.equal(result.accepted, false);
  assert.match(result.reason, /forbidden_field:attack_target_actor_id/);
  assert.equal(world.actors.get("player_b").hp, 100);
});

test("server resolves valid attack target from server-owned positions", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", { attack_intent: true });

  assert.equal(result.accepted, true);
  const attacker = world.actors.get("player_a");
  const target = world.actors.get("player_b");
  assert.equal(target.hp, 90);
  assert.equal(attacker.attackHitActorId, "player_b");
  assert.equal(attacker.attackHitKind, "melee");
  assert.equal(attacker.attackHitDamage, 10);
  assert.equal(attacker.attackHitKilled, false);
  assert.equal(target.hitEventSeq, 1);
  assert.equal(target.lastHitDamage, 10);
  assert.equal(target.lastHitKilled, false);
  assert.equal(target.position.z > 1.5, true);
  assert.equal(target.lastHitDir.z > 0.9, true);
});

test("server applies crossbow projectile hit result from server-owned projectile state", () => {
  const world = createWorld();
  const attacker = createPlayer("player_a", "session_a");
  attacker.mountId = "wolf_mount";
  attacker.equipmentLoadout = ["weapon_crossbow"];
  attacker.bundleVisualId = "wolfie-0002";
  world.actors.set("player_a", attacker);
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", { attack_intent: true });

  assert.equal(result.accepted, true);
  assert.equal(world.projectiles.size, 1);
  const projectile = Array.from(world.projectiles.values())[0];
  assert.equal(projectile.ownerActorId, "player_a");
  assert.equal(projectile.visualId, "crossbow_arrow");
  assert.equal(projectile.ttlTicks, 1200);
  assert.equal(world.actors.get("player_b").hp, 100);
  const zBefore = projectile.position.z;

  advanceWorld(world);
  assert.equal(world.projectiles.size, 1);
  assert.ok(Array.from(world.projectiles.values())[0].position.z > zBefore);

  for (let i = 0; i < 20 && world.projectiles.size > 0; i += 1) {
    advanceWorld(world);
  }
  assert.equal(world.projectiles.size, 0);
  assert.equal(world.actors.get("player_b").hp, 90);
});

test("crossbow projectile fire does not require a melee-selected target", () => {
  const world = createWorld();
  const attacker = createPlayer("player_a", "session_a");
  attacker.mountId = "wolf_mount";
  attacker.equipmentLoadout = ["weapon_crossbow"];
  attacker.bundleVisualId = "wolfie-0002";
  world.actors.set("player_a", attacker);
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 4));

  const result = applyInput(world, "session_a", "player_a", { attack_intent: true });

  assert.equal(result.accepted, true);
  assert.equal(world.projectiles.size, 1);
  assert.equal(world.actors.get("player_b").hp, 100);

  for (let i = 0; i < 60 && world.projectiles.size > 0; i += 1) {
    advanceWorld(world);
  }
  assert.equal(world.projectiles.size, 0);
  assert.equal(world.actors.get("player_b").hp, 90);
});

test("rejects client-authored projectile state", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", {
    attack_intent: true,
    projectile_id: "forged_arrow",
  });

  assert.equal(result.accepted, false);
  assert.match(result.reason, /forbidden_field:projectile_id/);
  assert.equal(world.projectiles.size, 0);
  assert.equal(world.actors.get("player_b").hp, 100);
});

test("rejects nested client-authored authority fields before mutation", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", {
    move_intent: { dx: 1, dz: 0, projectile: { visual_id: "forged_arrow" } },
    attack_intent: true,
  });

  assert.equal(result.accepted, false);
  assert.match(result.reason, /forbidden_field:projectile/);
  assert.equal(world.actors.get("player_a").position.x, 0);
  assert.equal(world.actors.get("player_b").hp, 100);
});

test("rejects unknown client input fields before mutation", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", {
    move_intent: { dx: 1, dz: 0 },
    world_item: { pickup_id: "forged_pickup" },
    attack_intent: true,
  });

  assert.equal(result.accepted, false);
  assert.match(result.reason, /forbidden_field:world_item/);
  assert.equal(world.actors.get("player_a").position.x, 0);
  assert.equal(world.actors.get("player_b").hp, 100);
});

test("non-crossbow attack does not invent projectile evidence", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5));

  const result = applyInput(world, "session_a", "player_a", { attack_intent: true });

  assert.equal(result.accepted, true);
  assert.equal(world.projectiles.size, 0);
});

test("server accepts out-of-cone attack as a miss animation without damage", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  world.actors.set("player_b", createPlayer("player_b", "session_b", 1.5, 0));

  const result = applyInput(world, "session_a", "player_a", { attack_intent: true });

  assert.equal(result.accepted, true);
  assert.equal(result.reason, "accepted");
  assert.equal(world.actors.get("player_a").actionState, "attack");
  assert.equal(world.actors.get("player_a").attackAnimRemaining, 12);
  assert.equal(world.actors.get("player_b").hp, 100);
});

test("movement intent advances server-owned position", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));

  const result = applyInput(world, "session_a", "player_a", {
    move_intent: { dx: 1, dz: 0 },
  });
  advanceWorld(world);

  assert.equal(result.accepted, true);
  assert.equal(world.actors.get("player_a").position.x, 0.22);
});

test("movement intent advances server-owned position in negative X on known terrain", () => {
  const world = createWorld(FLAT_TERRAIN);
  world.actors.set("player_a", createPlayer("player_a", "session_a", 0, 0, world.terrain));

  const result = applyInput(world, "session_a", "player_a", {
    move_intent: { dx: -1, dz: 0 },
  });
  advanceWorld(world);

  const actor = world.actors.get("player_a");
  assert.equal(result.accepted, true);
  assert.equal(actor.position.x, -MOVE_SPEED_PER_TICK);
  assert.equal(actor.velocity.x, -MOVE_SPEED_PER_TICK);
});

test("west A3D edge blocks left off-map step but allows right recovery", () => {
  const terrain = loadDefaultTerrain();
  const world = createWorld(terrain);
  const startX = -127.9;
  const startZ = -6.9;
  assert.equal(terrain.hasHeight?.(startX, startZ), true);
  assert.equal(terrain.hasHeight?.(startX - MOVE_SPEED_PER_TICK, startZ), false);
  assert.equal(terrain.hasHeight?.(startX + MOVE_SPEED_PER_TICK, startZ), true);
  world.actors.set("player_a", createPlayer("player_a", "session_a", startX, startZ, world.terrain));

  const blockedLeft = applyInput(world, "session_a", "player_a", {
    move_intent: { dx: -1, dz: 0 },
  });
  advanceWorld(world);

  const actor = world.actors.get("player_a");
  assert.equal(blockedLeft.accepted, true);
  assert.equal(actor.position.x, startX);
  assert.equal(actor.position.z, startZ);
  assert.equal(actor.actionState, "idle");
  assert.equal(actor.velocity.x, 0);

  const recoveredRight = applyInput(world, "session_a", "player_a", {
    move_intent: { dx: 1, dz: 0 },
  });
  advanceWorld(world);

  assert.equal(recoveredRight.accepted, true);
  assert.equal(actor.position.x, startX + MOVE_SPEED_PER_TICK);
  assert.equal(actor.velocity.x, MOVE_SPEED_PER_TICK);
});

test("jump intent is server-owned vertical movement over terrain", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  const actor = world.actors.get("player_a");
  const groundY = actor.position.y;

  const result = applyInput(world, "session_a", "player_a", {
    jump_intent: true,
  });
  advanceWorld(world);

  assert.equal(result.accepted, true);
  assert.equal(actor.position.y > groundY, true);
  assert.equal(actor.velocity.y > 0, true);

  const secondJump = applyInput(world, "session_a", "player_a", {
    jump_intent: true,
  });

  assert.equal(secondJump.accepted, false);
  assert.equal(secondJump.reason, "jump_airborne");
});

test("respawn intent is accepted only for dead server-owned actor", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a"));
  const actor = world.actors.get("player_a");

  assert.equal(applyInput(world, "session_a", "player_a", {
    respawn_intent: true,
  }).reason, "actor_alive");

  actor.hp = 0;
  actor.alive = false;
  actor.actionState = "dead";
  actor.velocity = { x: 0.1, y: 0.2, z: 0.3 };

  const result = applyInput(world, "session_a", "player_a", {
    respawn_intent: true,
  });

  assert.equal(result.accepted, true);
  assert.equal(actor.alive, true);
  assert.equal(actor.hp, actor.maxHp);
  assert.deepEqual(actor.velocity, { x: 0, y: 0, z: 0 });
  assert.equal(actor.actionState, "idle");
});

test("default Colyseus world uses A3D terrain height instead of flat y=0", () => {
  const terrain = loadDefaultTerrain();
  assert.notEqual(terrain.source, "flat");

  const world = createWorld(terrain);
  const player = createPlayer("player_a", "session_a", 0, 0, world.terrain);
  world.actors.set("player_a", player);

  assert.equal(player.position.y, terrain.sampleHeight(0, 0));
  assert.notEqual(player.position.y, 0);

  applyInput(world, "session_a", "player_a", { move_intent: { dx: 1, dz: 0 } });
  advanceWorld(world);

  const moved = world.actors.get("player_a");
  assert.equal(moved.position.y, terrain.sampleHeight(moved.position.x, moved.position.z));
  assert.notEqual(moved.position.y, 0);
});

test("default terrain fails closed when configured A3D map is missing", () => {
  withTerrainEnv({ ASCIICKER_COLYSEUS_A3D_MAP: "/definitely/missing/game_map_y8.a3d" }, () => {
    assert.throws(() => loadDefaultTerrain(), /A3D terrain missing/);
  });
});

test("flat terrain fallback requires explicit dev mode", () => {
  withTerrainEnv({
    ASCIICKER_COLYSEUS_A3D_MAP: "/definitely/missing/game_map_y8.a3d",
    ASCIICKER_COLYSEUS_ALLOW_FLAT_TERRAIN: "1",
  }, () => {
    assert.equal(loadDefaultTerrain().source, "flat");
  });
});

test("empty A3D terrain fails closed outside explicit dev mode", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "asciicker-a3d-empty-"));
  const emptyA3DPath = path.join(tmpDir, "empty.a3d");
  const data = Buffer.alloc(16 + 256 * 512);
  data.write("AS3D", 0, "ascii");
  data.writeUInt32LE(16, 4);
  data.writeUInt32LE(0, 8);
  fs.writeFileSync(emptyA3DPath, data);
  try {
    withTerrainEnv({}, () => {
      assert.throws(() => loadA3DTerrain(emptyA3DPath), /A3D terrain empty/);
    });
    withTerrainEnv({ ASCIICKER_COLYSEUS_ALLOW_FLAT_TERRAIN: "1" }, () => {
      assert.equal(loadA3DTerrain(emptyA3DPath).source, "flat");
    });
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("actor creation fails closed on unknown terrain cells", () => {
  const terrain = {
    source: "partial",
    sampleHeight: () => 4,
    hasHeight: (x) => x <= 0,
  };

  assert.throws(() => createPlayer("player_a", "session_a", 1, 0, terrain), /A3D terrain missing height/);
  assert.throws(() => createNpc("npc_bee_1", 1, 0, terrain), /A3D terrain missing height/);
});

test("server movement refuses to cross into unknown terrain", () => {
  const terrain = {
    source: "partial",
    sampleHeight: () => 4,
    hasHeight: (x) => x <= 0.05,
  };
  const world = createWorld(terrain);
  world.actors.set("player_a", createPlayer("player_a", "session_a", 0, 0, world.terrain));

  const result = applyInput(world, "session_a", "player_a", { move_intent: { dx: 1, dz: 0 } });
  advanceWorld(world);

  const actor = world.actors.get("player_a");
  assert.equal(result.accepted, true);
  assert.equal(actor.position.x, 0);
  assert.equal(actor.position.y, 4);
  assert.equal(actor.actionState, "idle");
  assert.equal(actor.velocity.x, 0);
});

test("server projectiles despawn before writing unknown terrain height", () => {
  const terrain = {
    source: "partial",
    sampleHeight: () => 4,
    hasHeight: (x) => x <= 0.05,
  };
  const world = createWorld(terrain);
  world.projectiles.set("projectile_1", {
    projectileId: "projectile_1",
    ownerActorId: "player_a",
    position: { x: 0, y: 4.95, z: 0 },
    velocity: { x: 0.08, y: 0, z: 0 },
    facingYaw: Math.PI / 2,
    visualId: "crossbow_arrow",
    ttlTicks: 1200,
  });

  advanceWorld(world, { updateNpcs: false });

  assert.equal(world.projectiles.size, 0);
});

test("server-owned item drops and crossbow projectile follow terrain height", () => {
  const terrain = loadDefaultTerrain();
  const world = createWorld(terrain);
  const attacker = createPlayer("player_a", "session_a", 0, 0, world.terrain);
  attacker.inventory.push("weapon_crossbow");
  attacker.mountId = "wolf_mount";
  attacker.equipmentLoadout = ["weapon_crossbow"];
  attacker.bundleVisualId = "wolfie-0002";
  world.actors.set("player_a", attacker);
  world.actors.set("player_b", createPlayer("player_b", "session_b", 0, 1.5, world.terrain));

  assert.equal(applyInput(world, "session_a", "player_a", { attack_intent: true }).accepted, true);
  const projectile = Array.from(world.projectiles.values())[0];
  assert.equal(projectile.position.y, terrain.sampleHeight(projectile.position.x, projectile.position.z) + 0.95);

  assert.equal(applyInput(world, "session_a", "player_a", { drop_item_id: "weapon_crossbow" }).accepted, true);
  const droppedCrossbow = Array.from(world.worldItems.values()).find(item => item.itemId === "weapon_crossbow");
  assert.ok(droppedCrossbow);
  assert.equal(droppedCrossbow.position.y, terrain.sampleHeight(droppedCrossbow.position.x, droppedCrossbow.position.z));
});

test("server-owned NPC moves toward player and can apply attack", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a", 0, 0));
  world.actors.set("npc_bee_1", createNpc("npc_bee_1", 1.0, 0));

  advanceWorld(world);

  const npc = world.actors.get("npc_bee_1");
  assert.equal(npc.actorKind, "npc");
  assert.equal(npc.ownerSessionId, "server");
  assert.equal(npc.actionState, "attack");
  assert.equal(npc.attackAnimRemaining, 11);
  assert.equal(world.actors.get("player_a").hp, 90);
});

test("server-owned NPC moves under room tick when outside melee range", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a", 0, 0));
  world.actors.set("npc_bee_1", createNpc("npc_bee_1", 2.8, 0));
  const before = world.actors.get("npc_bee_1").position.x;

  advanceWorld(world);

  const npc = world.actors.get("npc_bee_1");
  assert.equal(npc.actionState, "move");
  assert.equal(npc.facingYaw, -Math.PI / 2);
  assert.ok(npc.position.x < before);
});

test("player can kill server-owned NPC through server target resolution", () => {
  const world = createWorld();
  world.actors.set("player_a", createPlayer("player_a", "session_a", 0, 0));
  world.actors.set("npc_bee_1", createNpc("npc_bee_1", 0, 1.0));

  for (let i = 0; i < 3; i += 1) {
    const result = applyInput(world, "session_a", "player_a", { attack_intent: true });
    assert.equal(result.accepted, true);
    for (let tick = 0; tick < 24; tick += 1) {
      advanceWorld(world);
    }
  }

  const npc = world.actors.get("npc_bee_1");
  assert.equal(npc.hp, 0);
  assert.equal(npc.alive, false);
  assert.equal(npc.actionState, "dead");
});

test("server AOI visibility is keyed from authoritative actor position", () => {
  const viewer = createPlayer("player_a", "session_a", 0, 0);
  const nearActor = createPlayer("player_b", "session_b", AOI_RADIUS - 1, 0);
  const farActor = createPlayer("player_c", "session_c", AOI_RADIUS + 1, 0);

  assert.equal(isInAoi(viewer.position, viewer.position), true);
  assert.equal(isInAoi(viewer.position, nearActor.position), true);
  assert.equal(isInAoi(viewer.position, farActor.position), false);
});

test("server LOS fails closed when terrain height is unknown", () => {
  const terrain = {
    source: "partial",
    sampleHeight: () => 0,
    hasHeight: (x, z) => x >= 0 && z >= 0,
  };

  assert.equal(
    hasTerrainLineOfSight(terrain, { x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }),
    true,
  );
  assert.equal(
    hasTerrainLineOfSight(terrain, { x: 0, y: 0, z: 0 }, { x: -2, y: 0, z: 0 }),
    false,
  );
});

test("server LOS fails closed when terrain sampling throws", () => {
  const terrain = {
    source: "throwing",
    sampleHeight: () => {
      throw new Error("sample failed");
    },
    hasHeight: () => true,
  };

  assert.equal(
    hasTerrainLineOfSight(terrain, { x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }),
    false,
  );
});

test("server LOS blocks hidden entities behind terrain height", () => {
  const terrain = {
    source: "wall",
    sampleHeight: (x) => (Math.abs(x - 1) < 0.1 ? 4 : 0),
    hasHeight: () => true,
  };

  assert.equal(
    hasTerrainLineOfSight(FLAT_TERRAIN, { x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }),
    true,
  );
  assert.equal(
    hasTerrainLineOfSight(terrain, { x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }),
    false,
  );
});

test("server visibility combines AOI with TerrainAuthority LOS", () => {
  const terrain = {
    source: "wall",
    sampleHeight: (x) => (Math.abs(x - 1) < 0.1 ? 4 : 0),
    hasHeight: () => true,
  };
  const world = createWorld(terrain);
  const viewer = createPlayer("player_a", "session_a", 0, 0, world.terrain);
  const hiddenActor = createPlayer("player_b", "session_b", 2, 0, world.terrain);

  assert.equal(isInAoi(viewer.position, hiddenActor.position), true);
  assert.equal(isVisibleToActor(world, viewer, hiddenActor.position), false);
});

test("server AOI does not leak owner projectile truth outside visibility", () => {
  const world = createWorld();
  const viewer = createPlayer("player_a", "session_a", 0, 0, world.terrain);
  const projectile = {
    projectileId: "projectile_far",
    ownerActorId: "player_a",
    position: { x: AOI_RADIUS + 1, y: 0.95, z: 0 },
    velocity: { x: 0, y: 0, z: 0 },
    facingYaw: 0,
    visualId: "crossbow_arrow",
    ttlTicks: 1200,
  };

  assert.equal(isInAoi(viewer.position, projectile.position), false);
  assert.equal(isProjectileVisibleToActor(world, viewer, projectile), false);
});

test("server RTT metrics accept only echoed server probe timestamps", () => {
  const stats = { lastMs: 0, maxMs: 0, sampleCount: 0 };

  assert.equal(recordRttSample(stats, { server_time_ms: 1000, probe_seq: 1 }, 1042), true);
  assert.deepEqual(stats, { lastMs: 42, maxMs: 42, sampleCount: 1 });

  assert.equal(recordRttSample(stats, { server_time_ms: 1000, probe_seq: 2 }, 1084), true);
  assert.deepEqual(stats, { lastMs: 84, maxMs: 84, sampleCount: 2 });

  assert.equal(recordRttSample(stats, { server_time_ms: 1200 }, 1100), false);
  assert.equal(recordRttSample(stats, { client_time_ms: 1200 }, 1300), false);
  assert.deepEqual(stats, { lastMs: 84, maxMs: 84, sampleCount: 2 });
});

function withTerrainEnv(values, body) {
  const previous = {
    ASCIICKER_COLYSEUS_A3D_MAP: process.env.ASCIICKER_COLYSEUS_A3D_MAP,
    ASCIICKER_COLYSEUS_ALLOW_FLAT_TERRAIN: process.env.ASCIICKER_COLYSEUS_ALLOW_FLAT_TERRAIN,
  };
  try {
    for (const key of Object.keys(previous)) {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        process.env[key] = values[key];
      } else {
        delete process.env[key];
      }
    }
    body();
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}
