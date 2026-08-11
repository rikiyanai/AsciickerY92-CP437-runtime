import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FLAT_TERRAIN, TerrainAuthority, loadDefaultTerrain } from "./terrain.js";

export type ActorKind = "player" | "npc";
export type ActionState = "idle" | "move" | "attack" | "dead";

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface ActorModel {
  actorId: string;
  actorKind: ActorKind;
  position: Vec3;
  velocity: Vec3;
  facingYaw: number;
  actionState: ActionState;
  attackAnimRemaining: number;
  attackHitActorId: string;
  attackHitKind: string;
  attackHitDamage: number;
  attackHitKilled: boolean;
  hitEventSeq: number;
  lastHitDamage: number;
  lastHitDir: Vec3;
  lastHitKilled: boolean;
  hp: number;
  maxHp: number;
  alive: boolean;
  visualId: string;
  bundleVisualId: string;
  visualLayers: VisualLayerModel[];
  equipmentLoadout: string[];
  inventory: string[];
  mountId: string;
  frame: number;
  ownerSessionId: string;
  lastAttackTick: number;
}

export interface VisualLayerModel {
  role: string;
  sourcePath: string;
  sourceLayerIndex: number;
  sourceXpId: string;
  sourceXpIndex: number;
  frameMap: number[];
  required: boolean;
  order: number;
}

export interface WorldItemModel {
  pickupId: string;
  itemId: string;
  position: Vec3;
}

export interface ProjectileModel {
  projectileId: string;
  ownerActorId: string;
  position: Vec3;
  velocity: Vec3;
  facingYaw: number;
  visualId: string;
  ttlTicks: number;
}

export interface WorldModel {
  tick: number;
  actors: Map<string, ActorModel>;
  worldItems: Map<string, WorldItemModel>;
  projectiles: Map<string, ProjectileModel>;
  nextProjectileId: number;
  terrain: TerrainAuthority;
}

export interface InputMessage {
  move_intent?: { dx?: unknown; dz?: unknown };
  jump_intent?: unknown;
  respawn_intent?: unknown;
  attack_intent?: unknown;
  pickup_id?: unknown;
  equip_item_id?: unknown;
  swap_item_id?: unknown;
  drop_item_id?: unknown;
  seq?: unknown;
}

export interface InputResult {
  accepted: boolean;
  reason: string;
}

const ALLOWED_INPUT_FIELDS = new Set([
  "move_intent",
  "jump_intent",
  "respawn_intent",
  "attack_intent",
  "pickup_id",
  "equip_item_id",
  "swap_item_id",
  "drop_item_id",
  "seq",
]);

const ALLOWED_MOVE_INTENT_FIELDS = new Set(["dx", "dz"]);

// =============================================================================
// MOVEMENT-FEEL CONTRACT — FL-4094 T3.5 (GODOT_GOAL.MD)
// =============================================================================
// Authority law: this server is the sole owner of player position, velocity,
// terrain Y, jump/step/ledge outcomes, and death/fall outcomes. Godot is a
// render-only mirror (Main.gd:apply_local_player_snapshot). Any tuning here
// must keep that boundary; client-side prediction/interpolation/camera damping
// is render-only smoothing, never a second gameplay owner.
//
// Server tick rate: 30 Hz (TICK_MS = 1000/30 at AuthoritativeRoom.ts:19).
//
// CURRENT (as of HEAD): a deliberately simple model used as the T3.5 baseline.
//   - Ground speed:   MOVE_SPEED_PER_TICK = 0.22 u/tick  → 6.60 u/s.
//                     applyMove() normalizes (dx,dz) and writes constant
//                     magnitude. No accel/decel: input release = instant stop.
//                     Doc tuning target stays 0.22; NPC_MOVE_SPEED_PER_TICK
//                     (0.05 = 1.50 u/s) must not be raised to make player feel
//                     faster.
//   - Turn response:  instant. applyMove() sets facingYaw = atan2(dx, dz) on
//                     the same tick the intent arrives; no angular rate cap.
//   - Jump:           JUMP_SPEED_PER_TICK = 0.34 vertical impulse. applyJump()
//                     requires ground contact (rejects airborne / upward
//                     velocity). No double-jump, no air control, no variable
//                     jump height.
//   - Gravity:        GRAVITY_PER_TICK = 0.032 u/tick² → ~28.8 u/s². Applied
//                     to player every tick in advanceWorld(); NPCs are
//                     terrain-snapped (no vertical sim).
//   - Step-up/climb:  implicit only. advanceWorld() sets
//                     position.y = max(position.y, nextY) on the horizontal
//                     step, so hills climb at terrain rate. There is no
//                     explicit step-height cap, no slope-limit gate, no
//                     auto-jump, and no "lift the foot" affordance separate
//                     from terrain Y.
//   - Slope blocking: NOT IMPLEMENTED. A near-vertical terrain transition
//                     will be climbed in one tick (visible step-pop) or be
//                     accepted as a fall on the descent half.
//   - Ledge/drop:     no specialized handling. Walking off a height becomes
//                     a gravity arc; entering off-map terrain (nextY === null)
//                     freezes the actor instead of falling.
//   - Fall/death:     NOT IMPLEMENTED. No fall-damage threshold, no killz, no
//                     terrain-hole death. HP is only mutated by combat/attack.
//   - Render smooth.: forward extrapolation + camera-pivot damping.
//                     Main.gd records target position, velocity, and
//                     timestamp on each snapshot apply, then
//                     _process_colyseus_render_smoothing predicts player
//                     position between snapshots using velocity *
//                     elapsed-since-snapshot, capped at 100 ms.
//                     _process_colyseus_camera_damping then counter-
//                     offsets CameraPivot.position when XZ player motion
//                     exceeds a per-frame snap threshold (~0.35 u), so
//                     spawn / teleport / reconciliation snaps decay into
//                     the camera over ~170 ms instead of cutting. Server
//                     still owns velocity / facing_yaw / HP. There is no
//                     previous-snapshot interpolation buffer yet; sharp
//                     server velocity changes still snap the player
//                     sprite even though the camera absorbs the jump.
//
// KNOWN GAPS that movement-feel work must close (in roughly this order):
//   1. Server-side acceleration / deceleration on horizontal velocity so input
//      changes are not single-tick steps. Keep MOVE_SPEED_PER_TICK as the
//      ceiling magnitude.
//   2. Client-side render-only interpolation: FORWARD EXTRAPOLATION LANDED
//      (Main.gd:_process_colyseus_render_smoothing, velocity * elapsed,
//      capped at 100 ms). Previous-snapshot interpolation BUFFER
//      EVALUATED AND DEFERRED: a delayed-render lerp between [prev,
//      current] was prototyped in commit 3df5cacb5 and rolled back via
//      113a2e717. Reason: switching to interp-primary added a constant
//      33 ms of local-player input-to-action latency (one server tick
//      of render delay), and the cost is paid on every input even
//      though the snap-on-arrival it was meant to smooth is a rare
//      event (server velocity flip on input direction change, terrain
//      block, attack stun). Camera damping (gap #3) already absorbs
//      most of the visible snap. If a future agent wants to revisit:
//      the correct pattern for local player is OPT-IN hybrid blending
//      (extrapolate normally; detect snap-on-arrival and apply a one-
//      shot blend over ~50 ms), NOT delayed-render interpolation.
//      Delayed-render interpolation remains the right pattern for
//      REMOTE-player rendering, which is a separate seam.
//   3. Camera damping / sub-tick presentation smoothing for orbit + pivot
//      without re-introducing a second gameplay owner of facing_yaw.
//      PIVOT POSITION DAMPING LANDED (Main.gd:_process_colyseus_camera_damping;
//      XZ snap > 0.35 u counter-offsets CameraPivot.position with ~167 ms
//      exp decay; warps > 5 u skip damping; Y excluded). Still missing:
//      orbit-yaw smoothing under noisy / dropped mouse input — orbit is
//      currently delta-based in player.gd:_process_keyboard_rotation and
//      delta-based mouse rotation, which is already frame-rate-smooth but
//      has no critically-damped follow for snapped server target if a
//      future task ever lets the server author facing_yaw target.
//   4. Slope-limit / step-height contract: define the maximum traversable
//      Δheight per horizontal step. Above the limit, block on the server
//      (reject the next-XZ, stop velocity); below, allow as smooth climb.
//   5. Auto-jump / step-up affordance for small obstacles (matches the C++
//      runtime feel without restoring client authority).
//   6. Fall/death threshold (killz + fall-damage formula) and explicit
//      off-map handling that fails closed visibly rather than freezing.
//
// HARD BANS during T3.5 work (Law 2/3):
//   - No Godot script may write player.global_position, velocity,
//     facing_yaw, hp, attack_target_actor_id, or any gameplay-truth field
//     under --colyseus-client outside the documented render-only snapshot
//     mirror path.
//   - No revival of GameServer.gd as a Colyseus-path authority. Local mode
//     reference only.
//
// PROOF REQUIREMENTS (Required Runtime Proof Receipt Bar, GODOT_GOAL.MD):
//   - Capture configured MOVE_SPEED_PER_TICK, observed displacement/sec,
//     frame-gap p95/p99/max, RTT min/avg/max, server-tick overrun count,
//     snapshot apply cadence, actor-count, and headed + ASCII captures from
//     the same Colyseus runtime session before claiming feel is improved.
// =============================================================================

export const MOVE_SPEED_PER_TICK = 0.22;
const JUMP_SPEED_PER_TICK = 0.34;
const GRAVITY_PER_TICK = 0.032;
const GROUND_EPSILON = 0.001;
const ATTACK_RANGE = 2.25;
const ATTACK_COOLDOWN_TICKS = 24;
const ATTACK_DAMAGE = 10;
const ATTACK_ANIM_TICKS = 12;
const ATTACK_CONE_COS = Math.cos(Math.PI / 3);
const ATTACK_KNOCKBACK_DISTANCE = 0.42;
const PROJECTILE_HIT_RADIUS = 0.35;
const MAX_INPUT_COMPONENT = 1.0;
const NPC_MOVE_SPEED_PER_TICK = 0.05;
const NPC_MELEE_DISTANCE = 1.2;
const DEFAULT_PLAYER_VISUAL_ID = "player_default";
const WOLF_MOUNT_ITEM_ID = "wolf_mount";
const PROJECTILE_TERRAIN_MISSING_TTL_TICKS = 0;
const DEFAULT_SKIN_SLUG = "normal_player";
const DEFAULT_PRESENTATION_SLUG = "idle_walk";
const DEFAULT_VISUAL_STYLE_ID = 500;
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const ACTOR_VISUAL_PROFILES_PATH =
  process.env.ASCIICKER_COLYSEUS_ACTOR_VISUAL_PROFILES ??
  path.resolve(REPO_ROOT, "colyseus_server/runtime/visual_catalog/current/actor_visual_profiles.compiled.json");
const APPEARANCE_BUNDLE_PATH =
  process.env.ASCIICKER_COLYSEUS_APPEARANCE_BUNDLE ??
  path.resolve(REPO_ROOT, "colyseus_server/runtime/visual_catalog/current/appearance_bundle.json");

interface VisualIdentity {
  visualId: string;
  bundleVisualId: string;
  visualLayers: VisualLayerModel[];
}

interface ServerVisualCatalog {
  sourcePath: string;
  itemIds: Map<string, number>;
  mountIds: Map<string, number>;
  skinIds: Map<string, number>;
  presentationKindIds: Map<string, number>;
  profiles: Map<string, VisualIdentity>;
}

export const SERVER_VISUAL_CATALOG: ServerVisualCatalog = loadServerVisualCatalog();

interface ServerVisualKey {
  skinId: number;
  presentationKindId: number;
  rigId: number;
  mountId: number;
  weaponItemId: number;
  weaponStyleId: number;
  chestItemId: number;
  chestStyleId: number;
  headItemId: number;
  headStyleId: number;
}

function loadServerVisualCatalog(): ServerVisualCatalog {
  const compiledProfiles = readJsonObject(ACTOR_VISUAL_PROFILES_PATH);
  const appearanceBundle = readJsonObject(APPEARANCE_BUNDLE_PATH);
  const catalog = readRecordField(appearanceBundle, "catalog", APPEARANCE_BUNDLE_PATH);
  const itemIds = readSlugIdMap(catalog, "item_definitions", APPEARANCE_BUNDLE_PATH);
  const mountIds = readSlugIdMap(catalog, "mount_definitions", APPEARANCE_BUNDLE_PATH);
  const skinIds = readSlugIdMap(catalog, "skin_definitions", APPEARANCE_BUNDLE_PATH);
  const presentationKindIds = readSlugIdMap(catalog, "presentation_kinds", APPEARANCE_BUNDLE_PATH);
  const profiles = new Map<string, VisualIdentity>();

  for (const profile of readArrayField(compiledProfiles, "profiles", ACTOR_VISUAL_PROFILES_PATH)) {
    const profileId = readStringField(profile, "id", ACTOR_VISUAL_PROFILES_PATH);
    const profileKey = readRecordField(profile, "key", ACTOR_VISUAL_PROFILES_PATH);
    profiles.set(visualKeyString(visualKeyFromCompiledRow(profileKey, ACTOR_VISUAL_PROFILES_PATH)), {
      visualId: profileId,
      bundleVisualId: profileId,
      visualLayers: readVisualLayers(profile, ACTOR_VISUAL_PROFILES_PATH),
    });
  }

  return {
    sourcePath: ACTOR_VISUAL_PROFILES_PATH,
    itemIds,
    mountIds,
    skinIds,
    presentationKindIds,
    profiles,
  };
}

function readVisualLayers(profile: Record<string, unknown>, sourcePath: string): VisualLayerModel[] {
  return readArrayField(profile, "layers", sourcePath)
    .map((layer, index) => ({
      role: readStringField(layer, "role", sourcePath),
      sourcePath: readStringField(layer, "source_path", sourcePath),
      sourceLayerIndex: readNumberField(layer, "source_layer_index", sourcePath),
      sourceXpId: readStringField(layer, "source_xp_id", sourcePath),
      sourceXpIndex: readNumberField(layer, "source_xp_index", sourcePath),
      frameMap: readNumberArrayField(layer, "frame_map", sourcePath),
      required: readBooleanField(layer, "required", sourcePath),
      order: readOptionalNumberField(layer, "order", sourcePath) ?? index,
    }))
    .sort((a, b) => a.order - b.order);
}

function requireDefaultPlayerVisual(): VisualIdentity {
  const visual = resolveCompiledPlayerVisual(
    {
      actorId: "default_visual_probe",
      actorKind: "player",
      position: { x: 0, y: 0, z: 0 },
      velocity: { x: 0, y: 0, z: 0 },
      facingYaw: 0,
      actionState: "idle",
      attackAnimRemaining: 0,
      attackHitActorId: "",
      attackHitKind: "",
      attackHitDamage: 0,
      attackHitKilled: false,
      hitEventSeq: 0,
      lastHitDamage: 0,
      lastHitDir: { x: 0, y: 0, z: 0 },
      lastHitKilled: false,
      hp: 100,
      maxHp: 100,
      alive: true,
      visualId: DEFAULT_PLAYER_VISUAL_ID,
      bundleVisualId: "",
      visualLayers: [],
      equipmentLoadout: [],
      inventory: [],
      mountId: "",
      frame: 0,
      ownerSessionId: "server",
      lastAttackTick: -ATTACK_COOLDOWN_TICKS,
    },
    SERVER_VISUAL_CATALOG,
  );
  if (!visual) {
    throw new Error(`compiled visual catalog missing default player row from ${SERVER_VISUAL_CATALOG.sourcePath}`);
  }
  return visual;
}

function requireDefaultNpcVisual(): VisualIdentity {
  const visual = resolveCompiledProfileVisual({
    skinSlug: DEFAULT_SKIN_SLUG,
    presentationSlug: DEFAULT_PRESENTATION_SLUG,
    mountSlug: "bee_mount",
    weaponSlug: null,
    chestSlug: null,
    headSlug: null,
  }, SERVER_VISUAL_CATALOG);
  if (!visual) {
    throw new Error(`compiled visual catalog missing default NPC row from ${SERVER_VISUAL_CATALOG.sourcePath}`);
  }
  return visual;
}

function resolveCompiledPlayerVisual(actor: ActorModel, catalog: ServerVisualCatalog): VisualIdentity | null {
  return resolveCompiledProfileVisual({
    skinSlug: DEFAULT_SKIN_SLUG,
    presentationSlug: DEFAULT_PRESENTATION_SLUG,
    mountSlug: actor.mountId === "" ? null : actor.mountId,
    weaponSlug: firstLoadoutSlug(actor.equipmentLoadout, "weapon_"),
    chestSlug: firstLoadoutSlug(actor.equipmentLoadout, "armor_") ?? firstLoadoutSlug(actor.equipmentLoadout, "normal_armour"),
    headSlug: firstLoadoutSlug(actor.equipmentLoadout, "helmet_") ?? firstLoadoutSlug(actor.equipmentLoadout, "normal_helmet"),
  }, catalog);
}

function resolveCompiledProfileVisual(
  request: {
    skinSlug: string;
    presentationSlug: string;
    mountSlug: string | null;
    weaponSlug: string | null;
    chestSlug: string | null;
    headSlug: string | null;
  },
  catalog: ServerVisualCatalog,
): VisualIdentity | null {
  const skinId = catalog.skinIds.get(request.skinSlug);
  const presentationKindId = catalog.presentationKindIds.get(request.presentationSlug);
  if (skinId === undefined || presentationKindId === undefined) {
    return null;
  }
  const mountId = request.mountSlug === null ? 0 : catalog.mountIds.get(request.mountSlug);
  if (mountId === undefined) {
    return null;
  }
  const weaponItemId = optionalItemId(catalog, request.weaponSlug);
  if (weaponItemId === null) {
    return null;
  }
  const chestItemId = optionalItemId(catalog, request.chestSlug);
  if (chestItemId === null) {
    return null;
  }
  const headItemId = optionalItemId(catalog, request.headSlug);
  if (headItemId === null) {
    return null;
  }

  return catalog.profiles.get(visualKeyString({
    skinId,
    presentationKindId,
    rigId: mountId === 0 ? 0 : 1,
    mountId,
    weaponItemId,
    weaponStyleId: weaponItemId === 0 ? 0 : DEFAULT_VISUAL_STYLE_ID,
    chestItemId,
    chestStyleId: chestItemId === 0 ? 0 : DEFAULT_VISUAL_STYLE_ID,
    headItemId,
    headStyleId: headItemId === 0 ? 0 : DEFAULT_VISUAL_STYLE_ID,
  })) ?? null;
}

function optionalItemId(catalog: ServerVisualCatalog, itemSlug: string | null): number | null {
  if (itemSlug === null) {
    return 0;
  }
  const canonicalSlug = canonicalItemSlug(itemSlug);
  const itemId = catalog.itemIds.get(canonicalSlug);
  return itemId === undefined ? null : itemId;
}

function firstLoadoutSlug(loadout: string[], prefix: string): string | null {
  return loadout.find(itemId => itemId.startsWith(prefix) || itemId === prefix) ?? null;
}

function canonicalItemSlug(itemSlug: string): string {
  if (itemSlug === "weapon_sword") {
    return "normal_sword";
  }
  if (itemSlug === "armor_normal" || itemSlug === "normal_armor") {
    return "normal_armour";
  }
  if (itemSlug === "helmet_normal") {
    return "normal_helmet";
  }
  return itemSlug;
}

function visualKeyString(key: ServerVisualKey): string {
  return [
    key.skinId,
    key.presentationKindId,
    key.rigId,
    key.mountId,
    key.weaponItemId,
    key.weaponStyleId,
    key.chestItemId,
    key.chestStyleId,
    key.headItemId,
    key.headStyleId,
  ].join(":");
}

function visualKeyFromCompiledRow(row: Record<string, unknown>, sourcePath: string): ServerVisualKey {
  return {
    skinId: readNumberField(row, "skin_id", sourcePath),
    presentationKindId: readNumberField(row, "presentation_kind_id", sourcePath),
    rigId: readNumberField(row, "rig_id", sourcePath),
    mountId: readNumberField(row, "mount_id", sourcePath),
    weaponItemId: readNumberField(row, "weapon_item_id", sourcePath),
    weaponStyleId: readNumberField(row, "weapon_style_id", sourcePath),
    chestItemId: readNumberField(row, "chest_item_id", sourcePath),
    chestStyleId: readNumberField(row, "chest_style_id", sourcePath),
    headItemId: readNumberField(row, "head_item_id", sourcePath),
    headStyleId: readNumberField(row, "head_style_id", sourcePath),
  };
}

function readJsonObject(filePath: string): Record<string, unknown> {
  const parsed = JSON.parse(fs.readFileSync(filePath, "utf8")) as unknown;
  if (!isRecord(parsed)) {
    throw new Error(`expected JSON object in ${filePath}`);
  }
  return parsed;
}

function readSlugIdMap(source: Record<string, unknown>, field: string, sourcePath: string): Map<string, number> {
  const rows = readArrayField(source, field, sourcePath);
  const ids = new Map<string, number>();
  for (const row of rows) {
    ids.set(readStringField(row, "slug", sourcePath), readNumberField(row, "id", sourcePath));
  }
  return ids;
}

function readRecordField(source: Record<string, unknown>, field: string, sourcePath: string): Record<string, unknown> {
  const value = source[field];
  if (!isRecord(value)) {
    throw new Error(`expected object field ${field} in ${sourcePath}`);
  }
  return value;
}

function readArrayField(source: Record<string, unknown>, field: string, sourcePath: string): Record<string, unknown>[] {
  const value = source[field];
  if (!Array.isArray(value) || !value.every(isRecord)) {
    throw new Error(`expected object array field ${field} in ${sourcePath}`);
  }
  return value;
}

function readStringField(source: Record<string, unknown>, field: string, sourcePath: string): string {
  const value = source[field];
  if (typeof value !== "string" || value === "") {
    throw new Error(`expected non-empty string field ${field} in ${sourcePath}`);
  }
  return value;
}

function readNumberField(source: Record<string, unknown>, field: string, sourcePath: string): number {
  const value = source[field];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`expected finite number field ${field} in ${sourcePath}`);
  }
  return value;
}

function readNumberArrayField(source: Record<string, unknown>, field: string, sourcePath: string): number[] {
  const value = source[field];
  if (!Array.isArray(value) || !value.every(entry => typeof entry === "number" && Number.isFinite(entry))) {
    throw new Error(`expected finite number array field ${field} in ${sourcePath}`);
  }
  return value;
}

function readOptionalNumberField(source: Record<string, unknown>, field: string, sourcePath: string): number | null {
  const value = source[field];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`expected finite optional number field ${field} in ${sourcePath}`);
  }
  return value;
}

function readBooleanField(source: Record<string, unknown>, field: string, sourcePath: string): boolean {
  const value = source[field];
  if (typeof value !== "boolean") {
    throw new Error(`expected boolean field ${field} in ${sourcePath}`);
  }
  return value;
}

const FORBIDDEN_INPUT_FIELDS = new Set([
  "actor_id",
  "actorId",
  "actor_kind",
  "actorKind",
  "position",
  "velocity",
  "hp",
  "maxHp",
  "dead",
  "death",
  "alive",
  "attack_anim_remaining",
  "attackAnimRemaining",
  "attack_hit_actor_id",
  "attackHitActorId",
  "attack_hit_kind",
  "attackHitKind",
  "attack_hit_damage",
  "attackHitDamage",
  "attack_hit_killed",
  "attackHitKilled",
  "hit_event_seq",
  "hitEventSeq",
  "last_hit_damage",
  "lastHitDamage",
  "last_hit_dir",
  "lastHitDir",
  "last_hit_killed",
  "lastHitKilled",
  "visualId",
  "visual_id",
  "bundleVisualId",
  "bundle_visual_id",
  "equipmentLoadout",
  "equipment_loadout",
  "equipment",
  "loadout",
  "inventory",
  "world_item",
  "worldItem",
  "world_items",
  "worldItems",
  "npc",
  "npcs",
  "weapon_id",
  "mountId",
  "mount_id",
  "visual_sprite_path",
  "visual_layers",
  "frame",
  "target",
  "targetId",
  "attack_target_actor_id",
  "attackTargetActorId",
  "damage",
  "projectile",
  "projectiles",
  "projectile_id",
  "projectileId",
  "projectile_visual_id",
  "projectileVisualId",
]);

export function createWorld(terrain: TerrainAuthority = loadDefaultTerrain()): WorldModel {
  return {
    tick: 0,
    actors: new Map(),
    worldItems: createDefaultWorldItems(terrain),
    projectiles: new Map(),
    nextProjectileId: 1,
    terrain,
  };
}

function createDefaultWorldItems(terrain: TerrainAuthority): Map<string, WorldItemModel> {
  return new Map([
    ["pickup_mount_wolf", createWorldItem("pickup_mount_wolf", WOLF_MOUNT_ITEM_ID, -1.0, 0, terrain)],
    ["pickup_weapon_crossbow", createWorldItem("pickup_weapon_crossbow", "weapon_crossbow", -1.0, 0, terrain)],
    ["pickup_weapon_sword", createWorldItem("pickup_weapon_sword", "weapon_sword", -1.0, 0, terrain)],
  ]);
}

function createWorldItem(
  pickupId: string,
  itemId: string,
  x: number,
  z: number,
  terrain: TerrainAuthority = FLAT_TERRAIN,
): WorldItemModel {
  return { pickupId, itemId, position: { x, y: requireTerrainHeight(terrain, x, z), z } };
}

export function createPlayer(
  actorId: string,
  ownerSessionId: string,
  x = 0,
  z = 0,
  terrain: TerrainAuthority = FLAT_TERRAIN,
): ActorModel {
  const defaultVisual = requireDefaultPlayerVisual();
  return {
    actorId,
    actorKind: "player",
    position: { x, y: requireTerrainHeight(terrain, x, z), z },
    velocity: { x: 0, y: 0, z: 0 },
    facingYaw: 0,
    actionState: "idle",
    attackAnimRemaining: 0,
    attackHitActorId: "",
    attackHitKind: "",
    attackHitDamage: 0,
    attackHitKilled: false,
    hitEventSeq: 0,
    lastHitDamage: 0,
    lastHitDir: { x: 0, y: 0, z: 0 },
    lastHitKilled: false,
    hp: 100,
    maxHp: 100,
    alive: true,
    visualId: defaultVisual.visualId,
    bundleVisualId: defaultVisual.bundleVisualId,
    visualLayers: cloneVisualLayers(defaultVisual.visualLayers),
    equipmentLoadout: [],
    inventory: [],
    mountId: "",
    frame: 0,
    ownerSessionId,
    lastAttackTick: -ATTACK_COOLDOWN_TICKS,
  };
}

export function createNpc(actorId: string, x = 2.4, z = 0, terrain: TerrainAuthority = FLAT_TERRAIN): ActorModel {
  const defaultVisual = requireDefaultNpcVisual();
  return {
    actorId,
    actorKind: "npc",
    position: { x, y: requireTerrainHeight(terrain, x, z), z },
    velocity: { x: 0, y: 0, z: 0 },
    facingYaw: -Math.PI / 2,
    actionState: "idle",
    attackAnimRemaining: 0,
    attackHitActorId: "",
    attackHitKind: "",
    attackHitDamage: 0,
    attackHitKilled: false,
    hitEventSeq: 0,
    lastHitDamage: 0,
    lastHitDir: { x: 0, y: 0, z: 0 },
    lastHitKilled: false,
    hp: 30,
    maxHp: 30,
    alive: true,
    visualId: defaultVisual.visualId,
    bundleVisualId: defaultVisual.bundleVisualId,
    visualLayers: cloneVisualLayers(defaultVisual.visualLayers),
    equipmentLoadout: [],
    inventory: [],
    mountId: "",
    frame: 0,
    ownerSessionId: "server",
    lastAttackTick: -ATTACK_COOLDOWN_TICKS,
  };
}

export function applyInput(world: WorldModel, sessionId: string, actorId: string, payload: unknown): InputResult {
  const actor = world.actors.get(actorId);
  if (!actor) {
    return { accepted: false, reason: "unknown_actor" };
  }
  if (actor.ownerSessionId !== sessionId) {
    return { accepted: false, reason: "peer_actor_mismatch" };
  }
  if (!isRecord(payload)) {
    return { accepted: false, reason: "bad_payload" };
  }
  const inputFieldResult = validateInputFields(payload, ALLOWED_INPUT_FIELDS);
  if (!inputFieldResult.accepted) {
    return inputFieldResult;
  }
  const forbiddenNestedField = findForbiddenInputField(payload);
  if (forbiddenNestedField !== null) {
    return { accepted: false, reason: `forbidden_field:${forbiddenNestedField}` };
  }
  if (isRecord(payload.move_intent)) {
    const moveFieldResult = validateInputFields(payload.move_intent, ALLOWED_MOVE_INTENT_FIELDS);
    if (!moveFieldResult.accepted) {
      return moveFieldResult;
    }
  }
  const message = payload as InputMessage;
  if (!actor.alive) {
    if (message.respawn_intent) {
      return applyRespawn(world, actor);
    }
    return { accepted: false, reason: "actor_dead" };
  }
  if (message.respawn_intent) {
    return { accepted: false, reason: "actor_alive" };
  }
  if (message.pickup_id !== undefined) {
    const result = applyPickup(world, actor, message.pickup_id);
    if (!result.accepted) {
      return result;
    }
  }
  if (message.equip_item_id !== undefined) {
    const result = applyEquip(actor, message.equip_item_id);
    if (!result.accepted) {
      return result;
    }
  }
  if (message.swap_item_id !== undefined) {
    const result = applySwap(actor, message.swap_item_id);
    if (!result.accepted) {
      return result;
    }
  }
  if (message.drop_item_id !== undefined) {
    const result = applyDrop(world, actor, message.drop_item_id);
    if (!result.accepted) {
      return result;
    }
  }
  if (message.move_intent !== undefined) {
    const result = applyMove(actor, message.move_intent);
    if (!result.accepted) {
      return result;
    }
  }
  if (message.jump_intent) {
    const result = applyJump(world, actor);
    if (!result.accepted) {
      return result;
    }
  }
  if (message.attack_intent) {
    return applyAttack(world, actor);
  }
  return { accepted: true, reason: "accepted" };
}

function validateInputFields(payload: Record<string, unknown>, allowedFields: Set<string>): InputResult {
  for (const key of Object.keys(payload)) {
    if (!allowedFields.has(key)) {
      if (FORBIDDEN_INPUT_FIELDS.has(key)) {
        return { accepted: false, reason: `forbidden_field:${key}` };
      }
      return { accepted: false, reason: `unknown_field:${key}` };
    }
  }
  return { accepted: true, reason: "accepted" };
}

function findForbiddenInputField(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const entry of value) {
      const nested = findForbiddenInputField(entry);
      if (nested !== null) {
        return nested;
      }
    }
    return null;
  }
  if (!isRecord(value)) {
    return null;
  }
  for (const [key, nestedValue] of Object.entries(value)) {
    if (FORBIDDEN_INPUT_FIELDS.has(key)) {
      return key;
    }
    const nested = findForbiddenInputField(nestedValue);
    if (nested !== null) {
      return nested;
    }
  }
  return null;
}

function applyPickup(world: WorldModel, actor: ActorModel, pickupIdValue: unknown): InputResult {
  if (typeof pickupIdValue !== "string" || pickupIdValue === "") {
    return { accepted: false, reason: "bad_pickup" };
  }
  const item = world.worldItems.get(pickupIdValue);
  if (!item) {
    return { accepted: false, reason: "pickup_missing" };
  }
  const distance = Math.hypot(item.position.x - actor.position.x, item.position.z - actor.position.z);
  if (distance > 2.5) {
    return { accepted: false, reason: "pickup_out_of_range" };
  }
  if (!actor.inventory.includes(item.itemId)) {
    actor.inventory.push(item.itemId);
  }
  world.worldItems.delete(pickupIdValue);
  return { accepted: true, reason: "accepted" };
}

function applyEquip(actor: ActorModel, itemIdValue: unknown): InputResult {
  if (typeof itemIdValue !== "string" || itemIdValue === "") {
    return { accepted: false, reason: "bad_equip" };
  }
  if (!actor.inventory.includes(itemIdValue)) {
    return { accepted: false, reason: "item_not_owned" };
  }
  return equipOwnedItem(actor, itemIdValue);
}

function applySwap(actor: ActorModel, itemIdValue: unknown): InputResult {
  if (typeof itemIdValue !== "string" || itemIdValue === "") {
    return { accepted: false, reason: "bad_swap" };
  }
  if (!actor.inventory.includes(itemIdValue)) {
    return { accepted: false, reason: "item_not_owned" };
  }
  return equipOwnedItem(actor, itemIdValue);
}

function equipOwnedItem(actor: ActorModel, itemIdValue: string): InputResult {
  const previousMountId = actor.mountId;
  const previousLoadout = [...actor.equipmentLoadout];
  const previousVisualId = actor.visualId;
  const previousBundleVisualId = actor.bundleVisualId;
  if (itemIdValue === WOLF_MOUNT_ITEM_ID) {
    actor.mountId = itemIdValue;
  } else if (itemIdValue.startsWith("weapon_")) {
    actor.equipmentLoadout = actor.equipmentLoadout.filter(itemId => !itemId.startsWith("weapon_"));
    actor.equipmentLoadout.push(itemIdValue);
  } else {
    actor.equipmentLoadout = actor.equipmentLoadout.filter(itemId => itemId !== itemIdValue);
    actor.equipmentLoadout.push(itemIdValue);
  }
  if (!resolveServerVisualIdentity(actor)) {
    actor.mountId = previousMountId;
    actor.equipmentLoadout = previousLoadout;
    actor.visualId = previousVisualId;
    actor.bundleVisualId = previousBundleVisualId;
    return { accepted: false, reason: "visual_identity_missing" };
  }
  return { accepted: true, reason: "accepted" };
}

function applyDrop(world: WorldModel, actor: ActorModel, itemIdValue: unknown): InputResult {
  if (typeof itemIdValue !== "string" || itemIdValue === "") {
    return { accepted: false, reason: "bad_drop" };
  }
  const index = actor.inventory.indexOf(itemIdValue);
  if (index < 0) {
    return { accepted: false, reason: "item_not_owned" };
  }
  const previousInventory = [...actor.inventory];
  const previousMountId = actor.mountId;
  const previousLoadout = [...actor.equipmentLoadout];
  const previousVisualId = actor.visualId;
  const previousBundleVisualId = actor.bundleVisualId;
  actor.inventory.splice(index, 1);
  actor.equipmentLoadout = actor.equipmentLoadout.filter(itemId => itemId !== itemIdValue);
  if (actor.mountId === itemIdValue) {
    actor.mountId = "";
  }
  if (!resolveServerVisualIdentity(actor)) {
    actor.inventory = previousInventory;
    actor.mountId = previousMountId;
    actor.equipmentLoadout = previousLoadout;
    actor.visualId = previousVisualId;
    actor.bundleVisualId = previousBundleVisualId;
    return { accepted: false, reason: "visual_identity_missing" };
  }
  const pickupId = `drop_${itemIdValue}_${world.tick}_${world.worldItems.size + 1}`;
  world.worldItems.set(pickupId, createWorldItem(pickupId, itemIdValue, actor.position.x, actor.position.z, world.terrain));
  return { accepted: true, reason: "accepted" };
}

export function resolveServerVisualIdentity(actor: ActorModel, catalog: ServerVisualCatalog = SERVER_VISUAL_CATALOG): boolean {
  const visual = actor.actorKind === "player"
    ? resolveCompiledPlayerVisual(actor, catalog)
    : resolveCompiledProfileVisual({
      skinSlug: DEFAULT_SKIN_SLUG,
      presentationSlug: DEFAULT_PRESENTATION_SLUG,
      mountSlug: "bee_mount",
      weaponSlug: null,
      chestSlug: null,
      headSlug: null,
    }, catalog);
  if (!visual) {
    actor.visualId = "";
    actor.bundleVisualId = "";
    actor.visualLayers = [];
    return false;
  }
  actor.visualId = visual.visualId;
  actor.bundleVisualId = visual.bundleVisualId;
  actor.visualLayers = cloneVisualLayers(visual.visualLayers);
  return true;
}

function cloneVisualLayers(layers: VisualLayerModel[]): VisualLayerModel[] {
  return layers.map(layer => ({ ...layer }));
}

export function advanceWorld(world: WorldModel, options: { updateNpcs?: boolean } = {}): void {
  world.tick += 1;
  if (options.updateNpcs !== false) {
    updateNpcs(world);
  }
  updateProjectiles(world);
  for (const actor of world.actors.values()) {
    if (!actor.alive) {
      actor.actionState = "dead";
      actor.velocity = { x: 0, y: 0, z: 0 };
      continue;
    }
    actor.attackAnimRemaining = Math.max(0, actor.attackAnimRemaining - 1);
    const currentGroundY = gameplayTerrainHeight(world.terrain, actor.position.x, actor.position.z);
    if (currentGroundY === null) {
      actor.velocity = { x: 0, y: 0, z: 0 };
      actor.actionState = "idle";
      continue;
    }
    if (actor.velocity.x !== 0 || actor.velocity.z !== 0) {
      const nextX = actor.position.x + actor.velocity.x;
      const nextZ = actor.position.z + actor.velocity.z;
      const nextY = gameplayTerrainHeight(world.terrain, nextX, nextZ);
      if (nextY === null) {
        actor.velocity = { x: 0, y: 0, z: 0 };
        actor.actionState = "idle";
        continue;
      }
      actor.position.x = nextX;
      actor.position.z = nextZ;
      actor.position.y = Math.max(actor.position.y, nextY);
    }
    if (actor.actorKind === "player") {
      actor.velocity.y -= GRAVITY_PER_TICK;
      const groundY = gameplayTerrainHeight(world.terrain, actor.position.x, actor.position.z);
      if (groundY === null) {
        actor.velocity = { x: 0, y: 0, z: 0 };
        actor.actionState = "idle";
        continue;
      }
      const nextY = actor.position.y + actor.velocity.y;
      if (nextY <= groundY + GROUND_EPSILON) {
        actor.position.y = groundY;
        actor.velocity.y = 0;
      } else {
        actor.position.y = nextY;
      }
    } else {
      actor.position.y = gameplayTerrainHeight(world.terrain, actor.position.x, actor.position.z) ?? currentGroundY;
      actor.velocity.y = 0;
    }
    if (actor.velocity.x !== 0 || actor.velocity.y !== 0 || actor.velocity.z !== 0) {
      actor.frame = (actor.frame + 1) % 16;
    }
  }
}

function updateProjectiles(world: WorldModel): void {
  for (const [projectileId, projectile] of world.projectiles) {
    const nextX = projectile.position.x + projectile.velocity.x;
    const nextZ = projectile.position.z + projectile.velocity.z;
    const nextY = gameplayTerrainHeight(world.terrain, nextX, nextZ);
    if (nextY === null) {
      projectile.ttlTicks = PROJECTILE_TERRAIN_MISSING_TTL_TICKS;
    } else {
      projectile.position.x = nextX;
      projectile.position.z = nextZ;
      projectile.position.y = nextY + projectileGroundClearance(projectile);
      projectile.ttlTicks -= 1;
    }
    if (projectile.ttlTicks > 0) {
      const target = projectileImpactTarget(world, projectile);
      if (target) {
        applyDamage(world, target, ATTACK_DAMAGE, {
          x: projectile.velocity.x,
          y: 0,
          z: projectile.velocity.z,
        }, "projectile");
        projectile.ttlTicks = 0;
      }
    }
    if (projectile.ttlTicks <= 0) {
      world.projectiles.delete(projectileId);
    }
  }
}

function updateNpcs(world: WorldModel): void {
  for (const actor of world.actors.values()) {
    if (actor.actorKind !== "npc" || !actor.alive) {
      continue;
    }
    const target = nearestLivingTarget(world, actor, new Set(["player"]));
    if (!target) {
      actor.velocity = { x: 0, y: 0, z: 0 };
      actor.actionState = "idle";
      continue;
    }
    const dx = target.position.x - actor.position.x;
    const dz = target.position.z - actor.position.z;
    const distance = Math.hypot(dx, dz);
    actor.facingYaw = Math.atan2(dx, dz);
    if (distance > NPC_MELEE_DISTANCE) {
      actor.velocity = {
        x: (dx / distance) * NPC_MOVE_SPEED_PER_TICK,
        y: 0,
        z: (dz / distance) * NPC_MOVE_SPEED_PER_TICK,
      };
      actor.actionState = "move";
    } else {
      actor.velocity = { x: 0, y: 0, z: 0 };
      applyAttack(world, actor, new Set(["player"]));
    }
  }
}

function applyMove(actor: ActorModel, move: InputMessage["move_intent"]): InputResult {
  if (!isRecord(move)) {
    return { accepted: false, reason: "bad_move" };
  }
  const dx = finiteNumber(move.dx);
  const dz = finiteNumber(move.dz);
  if (dx === null || dz === null) {
    return { accepted: false, reason: "bad_move" };
  }
  if (Math.abs(dx) > MAX_INPUT_COMPONENT || Math.abs(dz) > MAX_INPUT_COMPONENT) {
    return { accepted: false, reason: "move_out_of_bounds" };
  }
  const length = Math.hypot(dx, dz);
  if (length === 0) {
    actor.velocity = { x: 0, y: actor.velocity.y, z: 0 };
    actor.actionState = "idle";
    return { accepted: true, reason: "accepted" };
  }
  actor.velocity = {
    x: (dx / length) * MOVE_SPEED_PER_TICK,
    y: actor.velocity.y,
    z: (dz / length) * MOVE_SPEED_PER_TICK,
  };
  actor.facingYaw = Math.atan2(dx, dz);
  actor.actionState = "move";
  return { accepted: true, reason: "accepted" };
}

function applyJump(world: WorldModel, actor: ActorModel): InputResult {
  const groundY = gameplayTerrainHeight(world.terrain, actor.position.x, actor.position.z);
  if (groundY === null) {
    return { accepted: false, reason: "jump_terrain_missing" };
  }
  if (actor.position.y > groundY + GROUND_EPSILON || actor.velocity.y > 0) {
    return { accepted: false, reason: "jump_airborne" };
  }
  actor.position.y = groundY;
  actor.velocity.y = JUMP_SPEED_PER_TICK;
  actor.actionState = "move";
  return { accepted: true, reason: "accepted" };
}

function applyRespawn(world: WorldModel, actor: ActorModel): InputResult {
  const groundY = gameplayTerrainHeight(world.terrain, actor.position.x, actor.position.z);
  if (groundY === null) {
    return { accepted: false, reason: "respawn_terrain_missing" };
  }
  actor.position.y = groundY;
  actor.velocity = { x: 0, y: 0, z: 0 };
  actor.hp = actor.maxHp;
  actor.alive = true;
  actor.actionState = "idle";
  actor.attackAnimRemaining = 0;
  actor.lastAttackTick = world.tick - ATTACK_COOLDOWN_TICKS;
  actor.lastHitDamage = 0;
  actor.lastHitDir = { x: 0, y: 0, z: 0 };
  actor.lastHitKilled = false;
  clearAttackHit(actor);
  return { accepted: true, reason: "accepted" };
}

function applyAttack(world: WorldModel, actor: ActorModel, targetKinds: Set<ActorKind> | null = null): InputResult {
  if (world.tick - actor.lastAttackTick < ATTACK_COOLDOWN_TICKS) {
    return { accepted: false, reason: "attack_cooldown" };
  }
  if (canSpawnAttackProjectile(actor)) {
    if (!spawnAttackProjectile(world, actor)) {
      return { accepted: false, reason: "projectile_terrain_missing" };
    }
    actor.lastAttackTick = world.tick;
    actor.actionState = "attack";
    actor.attackAnimRemaining = ATTACK_ANIM_TICKS;
    clearAttackHit(actor);
    return { accepted: true, reason: "accepted" };
  }
  const target = nearestEligibleTarget(world, actor, targetKinds);
  actor.lastAttackTick = world.tick;
  actor.actionState = "attack";
  actor.attackAnimRemaining = ATTACK_ANIM_TICKS;
  clearAttackHit(actor);
  if (target) {
    const hit = applyDamage(world, target, ATTACK_DAMAGE, hitDirection(actor, target), "melee");
    actor.attackHitActorId = target.actorId;
    actor.attackHitKind = hit.kind;
    actor.attackHitDamage = hit.damage;
    actor.attackHitKilled = hit.killed;
  }
  return { accepted: true, reason: "accepted" };
}

function canSpawnAttackProjectile(actor: ActorModel): boolean {
  return actor.actorKind === "player"
    && actor.mountId === WOLF_MOUNT_ITEM_ID
    && actor.equipmentLoadout.includes("weapon_crossbow");
}

function spawnAttackProjectile(world: WorldModel, actor: ActorModel): boolean {
  const dirX = Math.sin(actor.facingYaw);
  const dirZ = Math.cos(actor.facingYaw);
  const projectileX = actor.position.x + dirX * 0.55;
  const projectileZ = actor.position.z + dirZ * 0.55;
  const projectileY = gameplayTerrainHeight(world.terrain, projectileX, projectileZ);
  if (projectileY === null) {
    return false;
  }
  const projectileId = `projectile_${world.nextProjectileId}`;
  world.nextProjectileId += 1;
  world.projectiles.set(projectileId, {
    projectileId,
    ownerActorId: actor.actorId,
    position: {
      x: projectileX,
      y: projectileY + 0.95,
      z: projectileZ,
    },
    velocity: { x: dirX * 0.08, y: 0, z: dirZ * 0.08 },
    facingYaw: actor.facingYaw,
    visualId: "crossbow_arrow",
    ttlTicks: 1200,
  });
  return true;
}

function projectileImpactTarget(world: WorldModel, projectile: ProjectileModel): ActorModel | null {
  let best: ActorModel | null = null;
  let bestDist = Number.POSITIVE_INFINITY;
  for (const candidate of world.actors.values()) {
    if (candidate.actorId === projectile.ownerActorId || !candidate.alive) {
      continue;
    }
    const distance = Math.hypot(
      candidate.position.x - projectile.position.x,
      candidate.position.z - projectile.position.z,
    );
    if (distance <= PROJECTILE_HIT_RADIUS && distance < bestDist) {
      best = candidate;
      bestDist = distance;
    }
  }
  return best;
}

function clearAttackHit(actor: ActorModel): void {
  actor.attackHitActorId = "";
  actor.attackHitKind = "";
  actor.attackHitDamage = 0;
  actor.attackHitKilled = false;
}

function applyDamage(
  world: WorldModel,
  target: ActorModel,
  amount: number,
  direction: Vec3,
  kind: "melee" | "projectile",
): { damage: number; killed: boolean; kind: string } {
  const hitDir = normalizeHorizontal(direction);
  target.hp = Math.max(0, target.hp - amount);
  target.hitEventSeq += 1;
  target.lastHitDamage = amount;
  target.lastHitDir = hitDir;
  if (target.hp === 0) {
    target.alive = false;
    target.actionState = "dead";
    target.lastHitKilled = true;
  } else {
    target.lastHitKilled = false;
    applyKnockback(world, target, hitDir, ATTACK_KNOCKBACK_DISTANCE);
  }
  return { damage: amount, killed: !target.alive, kind };
}

function hitDirection(attacker: ActorModel, target: ActorModel): Vec3 {
  return {
    x: target.position.x - attacker.position.x,
    y: 0,
    z: target.position.z - attacker.position.z,
  };
}

function normalizeHorizontal(value: Vec3): Vec3 {
  const len = Math.hypot(value.x, value.z);
  if (len <= 0.0001) {
    return { x: 0, y: 0, z: 1 };
  }
  return { x: value.x / len, y: 0, z: value.z / len };
}

function applyKnockback(world: WorldModel, target: ActorModel, direction: Vec3, distance: number): void {
  const nextX = target.position.x + direction.x * distance;
  const nextZ = target.position.z + direction.z * distance;
  const nextY = gameplayTerrainHeight(world.terrain, nextX, nextZ);
  if (nextY === null) {
    target.velocity = { x: 0, y: 0, z: 0 };
    return;
  }
  target.position.x = nextX;
  target.position.z = nextZ;
  target.position.y = nextY;
  target.velocity = { x: direction.x * 0.04, y: 0, z: direction.z * 0.04 };
}

function projectileGroundClearance(projectile: ProjectileModel): number {
  return projectile.visualId === "crossbow_arrow" ? 0.95 : 0;
}

function requireTerrainHeight(terrain: TerrainAuthority, x: number, z: number): number {
  const height = gameplayTerrainHeight(terrain, x, z);
  if (height === null) {
    throw new Error(`A3D terrain missing height at x=${x} z=${z}`);
  }
  return height;
}

function gameplayTerrainHeight(terrain: TerrainAuthority, x: number, z: number): number | null {
  if (terrain.hasHeight && !terrain.hasHeight(x, z)) {
    return null;
  }
  const height = terrain.sampleHeight(x, z);
  return Number.isFinite(height) ? height : null;
}

function nearestLivingTarget(world: WorldModel, attacker: ActorModel, targetKinds: Set<ActorKind> | null = null): ActorModel | null {
  let best: ActorModel | null = null;
  let bestDist = Number.POSITIVE_INFINITY;
  for (const candidate of world.actors.values()) {
    if (candidate.actorId === attacker.actorId || !candidate.alive) {
      continue;
    }
    if (targetKinds !== null && !targetKinds.has(candidate.actorKind)) {
      continue;
    }
    const distance = Math.hypot(
      candidate.position.x - attacker.position.x,
      candidate.position.z - attacker.position.z,
    );
    if (distance < bestDist) {
      best = candidate;
      bestDist = distance;
    }
  }
  return best;
}

function nearestEligibleTarget(world: WorldModel, attacker: ActorModel, targetKinds: Set<ActorKind> | null = null): ActorModel | null {
  let best: ActorModel | null = null;
  let bestDist = Number.POSITIVE_INFINITY;
  for (const candidate of world.actors.values()) {
    if (candidate.actorId === attacker.actorId || !candidate.alive) {
      continue;
    }
    if (targetKinds !== null && !targetKinds.has(candidate.actorKind)) {
      continue;
    }
    const dx = candidate.position.x - attacker.position.x;
    const dz = candidate.position.z - attacker.position.z;
    const distance = Math.hypot(dx, dz);
    if (distance === 0 || distance > ATTACK_RANGE) {
      continue;
    }
    const facingX = Math.sin(attacker.facingYaw);
    const facingZ = Math.cos(attacker.facingYaw);
    const dot = facingX * (dx / distance) + facingZ * (dz / distance);
    if (dot >= ATTACK_CONE_COS && distance < bestDist) {
      best = candidate;
      bestDist = distance;
    }
  }
  return best;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
