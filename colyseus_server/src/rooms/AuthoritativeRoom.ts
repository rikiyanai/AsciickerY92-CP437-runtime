import { Client, Room } from "@colyseus/core";
import { StateView } from "@colyseus/schema";
import { ActorSchema, ProjectileSchema, WorldItemSchema } from "./schema/ActorSchema.js";
import { WorldState } from "./schema/WorldState.js";
import {
  ActorModel,
  ProjectileModel,
  WorldItemModel,
  advanceWorld,
  createNpc,
  applyInput,
  createPlayer,
  createWorld,
  MOVE_SPEED_PER_TICK,
  WorldModel,
} from "../sim/world.js";
import { hasTerrainLineOfSight } from "../sim/terrain.js";

const TICK_MS = 1000 / 30;
export const AOI_RADIUS = 24.0;
const AOI_RADIUS_SQUARED = AOI_RADIUS * AOI_RADIUS;
const WORLD_ITEM_LOS_TARGET_HEIGHT = 0.35;
const PROJECTILE_LOS_TARGET_HEIGHT = 0.2;
const METRICS_PROBE_INTERVAL_TICKS = 30;
const MAX_RTT_SAMPLE_MS = 60_000;
const PLAYER_SPAWNS: ReadonlyArray<{ x: number; z: number }> = [
  { x: 40.0, z: 32.0 },
  { x: 44.0, z: 32.0 },
];
const NPC_BEE_SPAWN = { x: 48.0, z: 28.0 };

export type RttStats = {
  lastMs: number;
  maxMs: number;
  sampleCount: number;
};

export class AuthoritativeRoom extends Room<{ state: WorldState }> {
  maxClients = 8;
  private world: WorldModel = createWorld();
  private actorBySession = new Map<string, string>();
  private visualSequenceMode = false;
  private lastTickDurationMs = 0;
  private tickOverrunCount = 0;
  private metricsProbeSequence = 0;
  private rttStats: RttStats = { lastMs: 0, maxMs: 0, sampleCount: 0 };
  private visibleEntityIdsBySession = new Map<string, Set<string>>();

  onCreate(options: unknown = {}): void {
    this.visualSequenceMode = isRecord(options) && options.visual_sequence === true;
    this.setState(new WorldState());
    this.world.actors.set(
      "npc_bee_1",
      createNpc("npc_bee_1", NPC_BEE_SPAWN.x, NPC_BEE_SPAWN.z, this.world.terrain),
    );
    this.syncSchema();
    this.setSimulationInterval(() => this.tick(), TICK_MS);
    this.onMessage("input", (client, payload) => this.handleInput(client, payload));
    this.onMessage("metrics", (_client, payload) => {
      recordRttSample(this.rttStats, payload, Date.now());
      this.syncSchema();
    });
  }

  onJoin(client: Client): void {
    const actorId = `player_${client.sessionId}`;
    const spawnIndex = this.actorBySession.size;
    const spawn = PLAYER_SPAWNS[Math.min(spawnIndex, PLAYER_SPAWNS.length - 1)];
    const actor = createPlayer(actorId, client.sessionId, spawn.x, spawn.z, this.world.terrain);
    this.actorBySession.set(client.sessionId, actorId);
    this.visibleEntityIdsBySession.set(client.sessionId, new Set());
    this.world.actors.set(actorId, actor);
    client.view = new StateView(true);
    this.syncSchema();
  }

  onLeave(client: Client): void {
    const actorId = this.actorBySession.get(client.sessionId);
    if (actorId) {
      this.world.actors.delete(actorId);
      this.actorBySession.delete(client.sessionId);
      this.visibleEntityIdsBySession.delete(client.sessionId);
      this.syncSchema();
    }
    client.view?.clear();
    client.view = undefined;
  }

  private handleInput(client: Client, payload: unknown): void {
    const actorId = this.actorBySession.get(client.sessionId);
    if (!actorId) {
      client.send("input_rejected", { reason: "no_actor" });
      return;
    }
    const result = applyInput(this.world, client.sessionId, actorId, payload);
    if (!result.accepted) {
      client.send("input_rejected", { reason: result.reason });
    }
    this.syncSchema();
  }

  private tick(): void {
    const startedAt = performance.now();
    advanceWorld(this.world, { updateNpcs: !this.visualSequenceMode });
    this.lastTickDurationMs = performance.now() - startedAt;
    if (this.lastTickDurationMs > TICK_MS) {
      this.tickOverrunCount += 1;
    }
    if (this.world.tick % METRICS_PROBE_INTERVAL_TICKS === 0) {
      this.sendMetricsProbe();
    }
    this.syncSchema();
  }

  private syncSchema(): void {
    this.state.tick = this.world.tick;
    this.state.server_tick_duration_ms = this.lastTickDurationMs;
    this.state.server_tick_overrun_count = this.tickOverrunCount;
    this.state.server_tick_rate_hz = 1000 / TICK_MS;
    this.state.movement_speed_per_tick = MOVE_SPEED_PER_TICK;
    this.state.rtt_last_ms = this.rttStats.lastMs;
    this.state.rtt_max_ms = this.rttStats.maxMs;
    this.state.rtt_sample_count = this.rttStats.sampleCount;
    for (const actorId of Array.from(this.state.actors.keys()) as string[]) {
      if (!this.world.actors.has(actorId)) {
        this.state.actors.delete(actorId);
      }
    }
    for (const [actorId, actor] of this.world.actors) {
      const schema = this.state.actors.get(actorId) ?? new ActorSchema();
      copyActor(actor, schema);
      this.state.actors.set(actorId, schema);
    }
    for (const pickupId of Array.from(this.state.world_items.keys()) as string[]) {
      if (!this.world.worldItems.has(pickupId)) {
        this.state.world_items.delete(pickupId);
      }
    }
    for (const [pickupId, item] of this.world.worldItems) {
      const schema = this.state.world_items.get(pickupId) ?? new WorldItemSchema();
      copyWorldItem(item, schema);
      this.state.world_items.set(pickupId, schema);
    }
    for (const projectileId of Array.from(this.state.projectiles.keys()) as string[]) {
      if (!this.world.projectiles.has(projectileId)) {
        this.state.projectiles.delete(projectileId);
      }
    }
    for (const [projectileId, projectile] of this.world.projectiles) {
      const schema = this.state.projectiles.get(projectileId) ?? new ProjectileSchema();
      copyProjectile(projectile, schema);
      this.state.projectiles.set(projectileId, schema);
    }
    this.updateClientViews();
  }

  private updateClientViews(): void {
    for (const client of this.clients) {
      const view = client.view;
      if (!view) {
        continue;
      }
      const viewerActorId = this.actorBySession.get(client.sessionId);
      if (!viewerActorId) {
        this.clearClientView(client);
        continue;
      }
      const viewer = this.world.actors.get(viewerActorId);
      if (!viewer) {
        this.clearClientView(client);
        continue;
      }
      const desiredEntityIds = new Set<string>();
      for (const [actorId, actor] of this.world.actors) {
        if (isVisibleToActor(this.world, viewer, actor.position)) {
          const schema = this.state.actors.get(actorId);
          if (schema) {
            desiredEntityIds.add(`actor:${actorId}`);
            this.addVisibleEntity(client, `actor:${actorId}`, schema);
          }
        }
      }
      for (const [pickupId, item] of this.world.worldItems) {
        if (isVisibleToActor(this.world, viewer, item.position, WORLD_ITEM_LOS_TARGET_HEIGHT)) {
          const schema = this.state.world_items.get(pickupId);
          if (schema) {
            desiredEntityIds.add(`world_item:${pickupId}`);
            this.addVisibleEntity(client, `world_item:${pickupId}`, schema);
          }
        }
      }
      for (const [projectileId, projectile] of this.world.projectiles) {
        if (isProjectileVisibleToActor(this.world, viewer, projectile)) {
          const schema = this.state.projectiles.get(projectileId);
          if (schema) {
            desiredEntityIds.add(`projectile:${projectileId}`);
            this.addVisibleEntity(client, `projectile:${projectileId}`, schema);
          }
        }
      }
      this.removeNoLongerVisibleEntities(client, desiredEntityIds);
    }
  }

  private clearClientView(client: Client): void {
    const visibleEntityIds = this.visibleEntityIdsBySession.get(client.sessionId);
    if (!visibleEntityIds || !client.view) {
      return;
    }
    for (const entityId of visibleEntityIds) {
      const schema = this.schemaForVisibleEntityId(entityId);
      if (schema) {
        client.view.remove(schema);
      }
    }
    visibleEntityIds.clear();
  }

  private addVisibleEntity(client: Client, entityId: string, schema: ActorSchema | WorldItemSchema | ProjectileSchema): void {
    const visibleEntityIds = this.visibleEntityIdsBySession.get(client.sessionId);
    if (!visibleEntityIds || !client.view || visibleEntityIds.has(entityId)) {
      return;
    }
    client.view.add(schema);
    visibleEntityIds.add(entityId);
  }

  private removeNoLongerVisibleEntities(client: Client, desiredEntityIds: Set<string>): void {
    const visibleEntityIds = this.visibleEntityIdsBySession.get(client.sessionId);
    if (!visibleEntityIds || !client.view) {
      return;
    }
    for (const entityId of Array.from(visibleEntityIds)) {
      if (desiredEntityIds.has(entityId)) {
        continue;
      }
      const schema = this.schemaForVisibleEntityId(entityId);
      if (schema) {
        client.view.remove(schema);
      }
      visibleEntityIds.delete(entityId);
    }
  }

  private schemaForVisibleEntityId(entityId: string): ActorSchema | WorldItemSchema | ProjectileSchema | undefined {
    const separatorIndex = entityId.indexOf(":");
    if (separatorIndex < 0) {
      return undefined;
    }
    const type = entityId.slice(0, separatorIndex);
    const id = entityId.slice(separatorIndex + 1);
    if (type === "actor") {
      return this.state.actors.get(id);
    }
    if (type === "world_item") {
      return this.state.world_items.get(id);
    }
    if (type === "projectile") {
      return this.state.projectiles.get(id);
    }
    return undefined;
  }

  private sendMetricsProbe(): void {
    const payload = {
      probe_seq: this.metricsProbeSequence,
      server_time_ms: Date.now(),
    };
    this.metricsProbeSequence += 1;
    for (const client of this.clients) {
      client.send("metrics_probe", payload);
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function recordRttSample(stats: RttStats, payload: unknown, receivedAtMs: number): boolean {
  if (!isRecord(payload)) {
    return false;
  }
  const serverTimeMs = Number(payload.server_time_ms);
  if (!Number.isFinite(serverTimeMs)) {
    return false;
  }
  const rttMs = receivedAtMs - serverTimeMs;
  if (rttMs < 0 || rttMs > MAX_RTT_SAMPLE_MS) {
    return false;
  }
  stats.lastMs = rttMs;
  stats.maxMs = Math.max(stats.maxMs, rttMs);
  stats.sampleCount += 1;
  return true;
}

function copyActor(actor: ActorModel, schema: ActorSchema): void {
  schema.actor_id = actor.actorId;
  schema.actor_kind = actor.actorKind;
  schema.position.x = actor.position.x;
  schema.position.y = actor.position.y;
  schema.position.z = actor.position.z;
  schema.velocity.x = actor.velocity.x;
  schema.velocity.y = actor.velocity.y;
  schema.velocity.z = actor.velocity.z;
  schema.facing_yaw = actor.facingYaw;
  schema.action_state = actor.actionState;
  schema.attack_anim_remaining = actor.attackAnimRemaining;
  schema.attack_hit_actor_id = actor.attackHitActorId;
  schema.attack_hit_kind = actor.attackHitKind;
  schema.attack_hit_damage = actor.attackHitDamage;
  schema.attack_hit_killed = actor.attackHitKilled;
  schema.hit_event_seq = actor.hitEventSeq;
  schema.last_hit_damage = actor.lastHitDamage;
  schema.last_hit_dir.x = actor.lastHitDir.x;
  schema.last_hit_dir.y = actor.lastHitDir.y;
  schema.last_hit_dir.z = actor.lastHitDir.z;
  schema.last_hit_killed = actor.lastHitKilled;
  schema.hp = actor.hp;
  schema.max_hp = actor.maxHp;
  schema.alive = actor.alive;
  schema.visual_id = actor.visualId;
  schema.bundle_visual_id = actor.bundleVisualId;
  schema.visual_layers = JSON.stringify(actor.visualLayers.map(layer => ({
    role: layer.role,
    source_path: layer.sourcePath,
    source_layer_index: layer.sourceLayerIndex,
    source_xp_id: layer.sourceXpId,
    source_xp_index: layer.sourceXpIndex,
    required: layer.required,
    order: layer.order,
  })));
  schema.equipment_loadout = actor.equipmentLoadout.join(",");
  schema.inventory = actor.inventory.join(",");
  schema.mount_id = actor.mountId;
  schema.frame = actor.frame;
}

function copyProjectile(projectile: ProjectileModel, schema: ProjectileSchema): void {
  schema.projectile_id = projectile.projectileId;
  schema.owner_actor_id = projectile.ownerActorId;
  schema.position.x = projectile.position.x;
  schema.position.y = projectile.position.y;
  schema.position.z = projectile.position.z;
  schema.velocity.x = projectile.velocity.x;
  schema.velocity.y = projectile.velocity.y;
  schema.velocity.z = projectile.velocity.z;
  schema.facing_yaw = projectile.facingYaw;
  schema.visual_id = projectile.visualId;
  schema.ttl_ticks = projectile.ttlTicks;
}

function copyWorldItem(item: WorldItemModel, schema: WorldItemSchema): void {
  schema.pickup_id = item.pickupId;
  schema.item_id = item.itemId;
  schema.position.x = item.position.x;
  schema.position.y = item.position.y;
  schema.position.z = item.position.z;
}

export function isInAoi(
  viewerPosition: { x: number; z: number },
  entityPosition: { x: number; z: number },
): boolean {
  const dx = entityPosition.x - viewerPosition.x;
  const dz = entityPosition.z - viewerPosition.z;
  return dx * dx + dz * dz <= AOI_RADIUS_SQUARED;
}

export function isVisibleToActor(
  world: WorldModel,
  viewer: ActorModel,
  entityPosition: { x: number; y: number; z: number },
  targetHeight?: number,
): boolean {
  if (viewer.position.x === entityPosition.x && viewer.position.z === entityPosition.z) {
    return true;
  }
  return (
    isInAoi(viewer.position, entityPosition) &&
    hasTerrainLineOfSight(world.terrain, viewer.position, entityPosition, { targetHeight })
  );
}

export function isProjectileVisibleToActor(
  world: WorldModel,
  viewer: ActorModel,
  projectile: ProjectileModel,
): boolean {
  return isVisibleToActor(world, viewer, projectile.position, PROJECTILE_LOS_TARGET_HEIGHT);
}
