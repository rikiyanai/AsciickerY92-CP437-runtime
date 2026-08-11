import { defineTypes, MapSchema, Schema, view } from "@colyseus/schema";
import { ActorSchema, ProjectileSchema, WorldItemSchema } from "./ActorSchema.js";

export class WorldState extends Schema {
  declare tick: number;
  declare server_tick_duration_ms: number;
  declare server_tick_overrun_count: number;
  declare server_tick_rate_hz: number;
  declare movement_speed_per_tick: number;
  declare rtt_last_ms: number;
  declare rtt_max_ms: number;
  declare rtt_sample_count: number;
  declare actors: MapSchema<ActorSchema>;
  declare world_items: MapSchema<WorldItemSchema>;
  declare projectiles: MapSchema<ProjectileSchema>;

  constructor() {
    super();
    this.tick = 0;
    this.server_tick_duration_ms = 0;
    this.server_tick_overrun_count = 0;
    this.server_tick_rate_hz = 0;
    this.movement_speed_per_tick = 0;
    this.rtt_last_ms = 0;
    this.rtt_max_ms = 0;
    this.rtt_sample_count = 0;
    this.actors = new MapSchema<ActorSchema>();
    this.world_items = new MapSchema<WorldItemSchema>();
    this.projectiles = new MapSchema<ProjectileSchema>();
  }
}

defineTypes(WorldState, {
  tick: "number",
  server_tick_duration_ms: "number",
  server_tick_overrun_count: "number",
  server_tick_rate_hz: "number",
  movement_speed_per_tick: "number",
  rtt_last_ms: "number",
  rtt_max_ms: "number",
  rtt_sample_count: "number",
  actors: { map: ActorSchema },
  world_items: { map: WorldItemSchema },
  projectiles: { map: ProjectileSchema },
});

view()(WorldState.prototype, "actors");
view()(WorldState.prototype, "world_items");
view()(WorldState.prototype, "projectiles");
