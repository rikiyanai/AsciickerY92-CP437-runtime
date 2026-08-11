import { defineTypes, Schema } from "@colyseus/schema";

export class Vec3Schema extends Schema {
  declare x: number;
  declare y: number;
  declare z: number;

  constructor() {
    super();
    this.x = 0;
    this.y = 0;
    this.z = 0;
  }
}

export class ActorSchema extends Schema {
  declare actor_id: string;
  declare actor_kind: string;
  declare position: Vec3Schema;
  declare velocity: Vec3Schema;
  declare facing_yaw: number;
  declare action_state: string;
  declare attack_anim_remaining: number;
  declare attack_hit_actor_id: string;
  declare attack_hit_kind: string;
  declare attack_hit_damage: number;
  declare attack_hit_killed: boolean;
  declare hit_event_seq: number;
  declare last_hit_damage: number;
  declare last_hit_dir: Vec3Schema;
  declare last_hit_killed: boolean;
  declare hp: number;
  declare max_hp: number;
  declare alive: boolean;
  declare visual_id: string;
  declare bundle_visual_id: string;
  declare visual_layers: string;
  declare equipment_loadout: string;
  declare inventory: string;
  declare mount_id: string;
  declare frame: number;

  constructor() {
    super();
    this.actor_id = "";
    this.actor_kind = "";
    this.position = new Vec3Schema();
    this.velocity = new Vec3Schema();
    this.facing_yaw = 0;
    this.action_state = "idle";
    this.attack_anim_remaining = 0;
    this.attack_hit_actor_id = "";
    this.attack_hit_kind = "";
    this.attack_hit_damage = 0;
    this.attack_hit_killed = false;
    this.hit_event_seq = 0;
    this.last_hit_damage = 0;
    this.last_hit_dir = new Vec3Schema();
    this.last_hit_killed = false;
    this.hp = 0;
    this.max_hp = 0;
    this.alive = true;
    this.visual_id = "";
    this.bundle_visual_id = "";
    this.visual_layers = "";
    this.equipment_loadout = "";
    this.inventory = "";
    this.mount_id = "";
    this.frame = 0;
  }
}

defineTypes(Vec3Schema, {
  x: "number",
  y: "number",
  z: "number",
});

export class ProjectileSchema extends Schema {
  declare projectile_id: string;
  declare owner_actor_id: string;
  declare position: Vec3Schema;
  declare velocity: Vec3Schema;
  declare facing_yaw: number;
  declare visual_id: string;
  declare ttl_ticks: number;

  constructor() {
    super();
    this.projectile_id = "";
    this.owner_actor_id = "";
    this.position = new Vec3Schema();
    this.velocity = new Vec3Schema();
    this.facing_yaw = 0;
    this.visual_id = "";
    this.ttl_ticks = 0;
  }
}

defineTypes(ProjectileSchema, {
  projectile_id: "string",
  owner_actor_id: "string",
  position: Vec3Schema,
  velocity: Vec3Schema,
  facing_yaw: "number",
  visual_id: "string",
  ttl_ticks: "number",
});

export class WorldItemSchema extends Schema {
  declare pickup_id: string;
  declare item_id: string;
  declare position: Vec3Schema;

  constructor() {
    super();
    this.pickup_id = "";
    this.item_id = "";
    this.position = new Vec3Schema();
  }
}

defineTypes(WorldItemSchema, {
  pickup_id: "string",
  item_id: "string",
  position: Vec3Schema,
});

defineTypes(ActorSchema, {
  actor_id: "string",
  actor_kind: "string",
  position: Vec3Schema,
  velocity: Vec3Schema,
  facing_yaw: "number",
  action_state: "string",
  attack_anim_remaining: "number",
  attack_hit_actor_id: "string",
  attack_hit_kind: "string",
  attack_hit_damage: "number",
  attack_hit_killed: "boolean",
  hit_event_seq: "number",
  last_hit_damage: "number",
  last_hit_dir: Vec3Schema,
  last_hit_killed: "boolean",
  hp: "number",
  max_hp: "number",
  alive: "boolean",
  visual_id: "string",
  bundle_visual_id: "string",
  visual_layers: "string",
  equipment_loadout: "string",
  inventory: "string",
  mount_id: "string",
  frame: "number",
});
