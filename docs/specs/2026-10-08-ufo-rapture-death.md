# Spec: UFO Rapture Death

Status: DRAFT — operator decisions recorded 2026-10-08 (§9); implementation awaits operator go.
Date: 2026-10-08
Branch: `feat/spooky-skins` (compact runtime: `rikiyanai/AsciickerY92-CP437-runtime`)
Upstream target: `rikiyanai/asciicker-Y9-2` (main CP437 runtime) — confirmed by operator.

## 1. Player-facing behavior

When a player's HP reaches zero:

1. **Death pose (existing).** The skin's death animation (`plydie` sheet) plays where the player fell.
2. **UFO arrives.** A saucer descends from above the viewport and hovers over the body.
3. **Rapture beam.** A vertical cone of light opens beneath the saucer: narrow at the hull, wide at the ground, like a tower/lighthouse beam turned to point straight down. Edge cells shimmer and light motes drift upward inside it.
4. **Absorption.** The body rises up the beam axis and dissolves into the hull.
5. **Departure.** The beam retracts upward, and the saucer accelerates up and out of view.
6. **Respawn (existing).** The player reappears at their spawn point at the same moment as today.

Every client sees the same sequence for the same death. A player who joins mid-sequence sees it from the correct point. The sequence works for every skin (default, Wallace, Grandma, Dracula, Witch) because it reuses whatever death frame that skin shows.

## 2. What exists today (code survey, 2026-10-08)

| Concern | Owner | Notes |
|---|---|---|
| Tick rate | `server/server_state.h:21` | `SVR_TICK_RATE 30` |
| Respawn delay | `server/server_state.h:28` | `SVR_RESPAWN_DELAY_TICKS 90`; DEAD→RESPAWNING after 90, RESPAWNING→ALIVE after another 90, so **death to respawn = 180 ticks = 6.0 s** |
| Phase machine | `server/server_state.h:99-115` | ALIVE→DEAD→RESPAWNING→ALIVE |
| HP≤0 detection | `server/server_tick.cpp:7462-7534` (melee), `:7628-7690` (projectile) | Two duplicated sites. Each sets `death_tick`, drops items, broadcasts `STRUCT_BRC_DEATH` `'k'`, and transitions to CPHASE_DEAD |
| Death presentation | `server_tick.cpp:6086, 6099, 6155` | DEAD → presentation kind 602; `presentation_started_tick` is stamped at death |
| Respawn | `SvrProcessRespawns` `server_tick.cpp:7695` | Resets state, `PhysicsTeleport` to `spawn_pos`, broadcasts `'r'` |
| Skip-wait request | `SvrProcessRespawnReq` `server_tick.cpp:7083` | Client `'R'` skipped the DEAD wait (pre-rapture). Now disabled; see §4 |
| Wire | `protocol_snapshot.h:32` (88-byte entity, static_asserted); `protocol_join.h:154` | Both already carry `life_state`, `presentation_kind_id`, `presentation_started_tick`, `pos` |
| Client death ingest | `engine/network_ingest_combat.cpp:125`; `network_ingest_snapshot.cpp:95-120, 541-545` | Plays `AudioDie`; ALIVE flag plus position snap is the real respawn |
| Death clip playback | `engine/actor_visual_profile_runtime.h:1014, 1206-1224` | `elapsed = tick - presentation_started_tick`; FORWARD_CLAMP holds the last frame |
| Offline death | `engine/game_input.cpp:173-190` | Sets DEATH locally |
| Translucency | `engine/sprite_constants.h:35`; `render_sprite_blit.cpp:540-640` | No real alpha. **Swoosh** cells (index 254) lighten whatever is beneath them, are depth-tested and don't write depth. This is the light-cone primitive |
| Line overlay analogue | `RenderProjectileLines` `render_scene.cpp:19-78` | Depth-tested lightening `CellLine` |
| Particles | `engine/weather.{h,cpp}` `ParticlePool` | No depth test; disabled in authoritative web MP |
| Z rule | `render_scene.cpp:598-603, 637-640` (FL-1434/FL-2023) | **Server owns actor Z.** No render-side lifting of actors |

**Key consequence:** the 6 s death window already exists, and every client already gets a server-anchored start time (`presentation_started_tick`) plus authoritative `pos`. v1 needs **no wire-format change and no respawn-delay change.**

## 3. Timeline (30 Hz ticks, measured from `death_tick`)

| Phase | Ticks | Seconds | Visual |
|---|---|---|---|
| `FALL` | 0 – 30 | 0.0 – 1.0 | Existing death clip plays, then clamps to the last frame. Measure the real clip length at implementation; the phase starts at `max(clip_len, 30)` |
| `DESCEND` | 30 – 54 | 1.0 – 1.8 | UFO eases in from above the viewport to hover height `H_UFO` |
| `BEAM_OPEN` | 54 – 66 | 1.8 – 2.2 | Cone grows downward from the hull to the ground |
| `LIFT` | 66 – 114 | 2.2 – 3.8 | Body rises along the axis (ease-in-out), dimming over the top 30% |
| `BEAM_CLOSE` | 114 – 123 | 3.8 – 4.1 | Cone retracts upward; the body is hidden from here on |
| `DEPART` | 123 – 141 | 4.1 – 4.7 | UFO accelerates up and off-screen |
| _(empty)_ | 141 – 180 | 4.7 – 6.0 | Empty ground, then the existing respawn at 180 |

All boundaries live in one constants block (`RAPTURE_*_TICKS`) shared by server and client through one header, so the phase function is a single pure function: `RapturePhase RaptureEval(uint32_t ticks_since_death)` → `{phase, t01, body_z_offset, body_visible, ufo_z_offset, beam_len01}`.

## 4. Authority model

- **Time base: the server.** The phase is always `RaptureEval(authoritative_tick - presentation_started_tick)` while `life_state == DEAD/RESPAWNING`. No client timer advances it.
- **Body lift: server-owned Z** (this respects the FL-1434/FL-2023 rule). In `SvrProcessRespawns` or a sibling `SvrProcessRapture`, the server sets the dead player's `pos[2] = death_ground_z + body_z_offset` each tick while it is dead, and skips physics for that body. The snapshot carries this to every client unchanged.
- **Body visibility: derived.** Clients skip drawing a DEAD actor when `RaptureEval(...).body_visible == false`. This is a pure function of the authoritative tick, so all clients agree.
- **UFO and beam: render-only effects.** They are not server entities and have no collision, physics, targeting or `HitWorld` participation. The client positions them from the body's authoritative ground XY and `death_ground_z`. They aren't actors, so the Z rule doesn't apply to them.
- **`'R'` respawn request: disabled** (operator decision, §9). `SvrProcessRespawnReq` rejects it, with the logged reason `respawn_req_disabled_while_not_alive`, whenever the player is not ALIVE (DEAD or RESPAWNING). An alive player has nothing to skip. Respawn is always the fixed 180 ticks. The earlier "ignore until tick 141" gate was dead code, because DEAD auto-advances to RESPAWNING at tick 90, and it has been removed along with `RaptureSkipRequestAllowed`. There is no client UI hint or keybinding for it.
- **Mounted death:** the mount stays (`server_tick.cpp:6171`), and the rider and mount are lifted together as one actor.
- **Dropped items:** unchanged. They drop at the death position at death time (`SvrDropAllOwnedItemsAtPlayer`), before the lift.
- **Offline mode: deferred, out of scope for v1** (operator decision, §9). Offline frame input is invalid, so no rapture lift, cut-over, UFO or beam runs offline, and the body is never hidden.
- **Camera (revised 2026-10-08, operator-approved polish):** XY follows the local player's authoritative pos as before. Z does not follow the lifted body: while the local player is in the rapture window (`life_state == DEAD` with a server death stamp, i.e. death until respawn) the camera's follow Z is the death ground Z (`RaptureCameraFollowZ` in `rapture_timeline.h`), recovered exactly like the UFO anchor (`RaptureDeathGroundZ` on `player.pos` paired with the latest authoritative tick). The body therefore rises up the beam on screen instead of the ground sliding away. The value feeds the existing camera Z smoother, so respawn snaps or eases by the same `|dz| > 5` rule as before, now on spawn Z − death ground Z. This is render-only and local-only: no actor Z is touched and remote views are unchanged.
  - **Yaw:** a dead player's input is not applied, so the server yaw stays frozen at the death yaw. The FL-1733 local yaw resync is skipped while the local player is DEAD, so the camera keeps turning instead of snapping back between Q/E taps.
- **Companion (Gromit) during the owner's rapture:** server-owned, in `SvrUpdateNpcAI`'s companion branch. While the owner has `rapture_active` set, Gromit walks to and holds a point just outside the beam's ground footprint, on the side he is already on, then faces the body.
  - The stand-off radius is footprint + 3 = 13.67 world units (§5), with start/stop hysteresis of 1.5/0.5 on the distance to the stand point.
  - The hysteresis is kept in the existing `follow_active` owner, and the force is re-derived every tick. He moves normally and never teleports.
  - Normal following resumes after respawn.
- **Hostile NPCs:** one rule, `SvrPlayerTargetable` → `RaptureNpcMayTargetPlayer`, covers the NPC target scan, swing and projectile target resolution, and swing hit collection. A dead or raptured player is never targeted, swung at or damaged.
  - An NPC inside a raptured body's footprint + 6 (16.67 world units) backs straight out before any targeting. Outside that radius it gets no force, and the body is never a target, so it does not re-aggro on it.
  - It re-targets the player normally once the player is alive again. Spawn-camping by an NPC that reaches the spawn is ordinary aggro on an alive player and out of scope.
- **NPC simulation during the window:** NPC physics also steps while any player has `rapture_active` set; before, it stepped only while a player was ALIVE, so with a lone dead player every NPC froze. The NPC physics step rotates the intent force by `intent_dir`, its step yaw. The new stand-off and retreat paths therefore express their world direction in that frame (`RaptureNpcIntentFromWorld`). The older chase and follow code passes a world vector directly and walks at 2θ + 90°; it is unchanged (FAILURE_LOG).
- **Death bookkeeping:** `SvrApplyPlayerDeathBookkeeping` also clears the body's collision-debug samples. The body is not physics-stepped, and its last count was re-sent every tick (up to 32 `'c'` packets), which overflowed the native client's 32-slot inbound queue and dropped the delta snapshots carrying the lift.

## 5. Visual design (CP437)

Revised 2026-10-08 after operator feedback: *"The ufo can honestly be invisible but if visible beam radius must be much wider all yellow with shadows and the ufo sprite must be authored xp sprite using proper glyphs."*

- **Beam cone:** a depth-tested overlay pass, `RenderRaptureBeams` (`render_scene.cpp`), with its shape and shading in the pure header `engine/rapture_beam.h`.
  - **Width:** the half-width grows linearly from 3.5 cells at the hull to 11 cells at the ground (about 3× the first cut's 1 → 3.5).
  - **Warm yellow light, not lightening.** Every colour is the frame-buffer colour already there blended toward a yellow target, using integer cube maths. The bk targets lean toward amber because a partial blend of green grass toward pure yellow lands on yellow-green. The beam therefore reads yellow on grass, sand and dark ground, and the terrain hue still tints the bk.
  - **Shaded falloff:** band 0 is the bright core `219 █`, band 1 mid yellow `178 ▓`, band 2 amber `177 ▒`, and band 3 dark ochre `176 ░` at the rim.
  - **Sprites keep their glyph.** Cells a sprite drew this frame (body, props) keep their glyph and are only tinted, so the body stays readable. They are detected by diffing against a snapshot taken before the actor blit, which only happens while a beam is open. Terrain cells take the band glyph.
  - **Shadows:**
    - The hull's own shadow darkens the top 2 beam rows, but only when the UFO is drawn.
    - A lit footprint ellipse (13.5 × 3 cells) keeps the terrain glyph and warms it.
    - A darkened shadow ring (16 × 4) sits just outside the footprint.
  - **Depth:** the depth test uses the round cone's front surface per column (`6·sqrt(hw² − dx²)` z nearer than the axis), so the wider rim doesn't leak over nearer occluders. Footprint and ring cells test at ground height. It never writes depth.
  - **Edge flicker:** a deterministic hash of `(cell, tick pair, death-tick seed)`, the same on every client.
  - **Motes:** 6 bright yellow/white motes (`250 ·`, `249 ∙`, `15 ☼`) scroll upward.
  - **Cost:** each row scans only `RaptureBeamRowScan` columns, which is ≤ 700 cell tests per beam (unit-tested) and ≤ ~11k at the 16-beam cap.
- **Framing with the low camera (2026-10-08):** the camera stays on the death ground for the whole window (§4), so the hull top stays 128 / 12.3 + 5 ≈ 15.4 rows above the camera centre (16 measured) during LIFT too. Before, the rising camera added up to 5.9 rows of margin.
  - **Measured in the real native client:** at 50 rows the dome cap is on row 9 (rows 0–1 are HUD). At 36 rows, the minimum web viewport, it is on row 2, just under the HUD's top bar, with 2 rows to spare. It is not clipped.
  - The lifted body ends just under the hull.
- **Footprint in world units (server stand-off and retreat):** the renderer's fixed projection (`Render()` zoom 3, ds = 0.75; `ProjectCoords`) gives 1.5 columns and 0.75 rows per world unit.
  - The shadow ring (16 × 4 cells) is therefore 10.67 × 5.33 world units. It is screen-aligned and the camera turns, so the world footprint is its bounding circle, r = 10.67. The cone's own ground half-width (11 columns = 7.33 units) lies inside it.
  - Companion stand-off radius: r + 3. NPC retreat radius: r + 6.
  - The mirrored constants live in `engine/rapture_standoff.h` and are `static_assert`ed in `render_scene.cpp`.
  - When Gromit stands behind the beam relative to the camera, he is seen through it, and his head can overlap the saucer rim on screen. This is depth-correct and is not the body being covered.
- **UFO:** `assets/sprites/ufo.xp` (21×5 cells, 1 angle, 4 frames) is drawn with `RenderSprite` at `death_ground_z + H_UFO + ufo_z_offset`. It is a REXPaint-style CP437 glyph drawing:
  - `▄` glass dome cap
  - `( ░ ∙ ☻ ∙ ░ )` dome with pilot
  - `▄`/`▀`/`▒` shaded hull
  - `▐ ═══ ▌` rim band, with `☼ ° ∙` chasing running lights and a `◘` beacon
  - `\ ▀▀ ▄▄▄ ▀▀ /` underside with a warm emitter

  No cyan swoosh cells. The generator (`scripts/adhoc/2026-10-08-make-ufo-sprite.py`) asserts that no visible colour equals or quantizes to the layer-0 key (255,255,85), which `sprite.cpp` matches exactly. The preview is `docs/previews/ufo/ufo.png` (real `cp437_16x16.png` atlas). The **recommended** route is an `.xp` sprite rather than an AKM mesh: meshes join `HitWorld`/physics and need `RebuildWorld` every time they move.
- **Invisible-UFO option:** `RAPTURE_UFO_VISIBLE` in `engine/rapture_timeline.h`, default `1`. Building with `0` skips `RenderRaptureUfos` and the hull shadow. The beam, motes, footprint, lift and dissolve are unchanged. The render harness's `no_ufo=1` is the per-run stand-in (`docs/recordings/ufo-rapture/all-rapture-no-ufo.gif`).
- **Body dissolve:** over the top 30% of `LIFT`, the body's own colours fade toward the beam's yellow-white (cube 5,5,3), via `RaptureDissolvePaletteIndex` in the actor blit. At `BEAM_CLOSE` it is hidden.
- **Sound (optional, v1.1):** a hum that rises in pitch during `LIFT`, via `SetAudioSwoosh`-style continuous audio with `AudioSpatialVolume`. It is not required for v1.

## 6. Implementation plan (each step separately verifiable)

1. **Phase function.** Add `engine/rapture_timeline.h` (constants plus `RaptureEval`), shared by server and client, with a unit test of the phase boundaries, monotonic lift, visibility cut-over and clamp past 141.
2. **Server lift.** Add `SvrProcessRapture` (called next to `SvrProcessRespawns`, `server_tick.cpp:9813`). It records `death_ground_z` at death in both HP≤0 sites, factored into one helper so they can't drift. It drives `pos[2]` while the player is dead and leaves respawn and teleport untouched. `'R'` is rejected while not alive (§4).
3. **Client body visibility and dissolve** in the actor draw path.
4. **UFO sprite asset** `ufo.xp` (authored like the skins, with a preview PNG for approval) and its render-only placement.
5. **Beam overlay pass** `RenderRaptureBeams` in `render_scene.cpp`, fed through `RenderFrameInput` like `projectile_lines`.
6. ~~**Offline path** hook in `game_input.cpp`.~~ **Removed / deferred** (operator decision, §9). It was implemented in 6514d86 and then reverted: nothing lowers HP offline and there is no offline respawn, so the path was unreachable, and if it had been reached the body would have stayed hidden forever.
7. **Docs:** a FAILURE_LOG entry for the feature and a VERIFICATION section with commands, hashes and a recording receipt.

There is no change to `STRUCT_SNAPSHOT_ENTITY`, `STRUCT_BRC_JOIN`, JOIN_V2 capacity, presentation kinds or actor-visual profile rows. Reachability and table coverage gates must therefore stay byte-identical.

## 7. Acceptance criteria

- **Static:**
  - The `RaptureEval` unit test passes.
  - `./scripts/run_standalone_checks.sh` stays fully green, with actor coverage and reachability unchanged.
  - Native server, native client and web all build.
- **Server behavior** (headless test):
  - Kill a player and record per-tick `pos[2]`, which must be flat during FALL/DESCEND/BEAM_OPEN, rise monotonically during LIFT, and hold from 114 to 180.
  - Respawn happens at tick 180 at `spawn_pos`, as today.
  - `'R'` is ignored at every tick while DEAD or RESPAWNING (logged); respawn is still at tick 180.
- **Headed, multi-client** (two browser clients, real PLAY control, one kills the other):
  - Both viewports show the death clip, UFO, beam, lift and departure in sync.
  - A third client joining during LIFT sees the sequence from the correct phase.
  - Repeat with each skin: default, Wallace, Grandma, Dracula, Witch.
  - Recording plus receipt in `docs/recordings/`.
- **No side effects:**
  - The dead body can't be hit during the sequence.
  - No collision with the UFO or beam.
  - No hostile NPC targets the dead player.
  - The companion (Gromit) steps back out of the beam's footprint and holds there (§4); the killer NPC backs out of it.
- **Operator acceptance** of the headed recording is the final gate.

## 8. Upstream merge plan (to main CP437 runtime)

1. Finish and accept the feature here on a feature branch and merge it to this repo's `main`.
2. Attach `rikiyanai/asciicker-Y9-2` to the session and diff the touched owners: death sites, `SvrProcessRespawns`, render scene, snapshot ingest. That repo may predate this one's module split, in which case the port targets its equivalent files.
3. Port as one PR on Y9-2 with the same tests. The feature needs no wire or table changes, so the port is code plus one sprite asset.
4. Run that repo's own checks plus the same headed acceptance there.

## 9. Decisions

Resolved by operator (2026-10-08):
1. **Upstream:** `rikiyanai/asciicker-Y9-2` is the main CP437 runtime merge target.
2. **UFO asset:** flat `.xp` sprite.
3. **Scope:** players only for now; NPCs are out of scope for v1.
4. **`'R'` disabled while dead:** R does nothing while the player is DEAD or RESPAWNING, and respawn stays at the fixed 180 ticks.
5. **Offline deferred:** offline death and rapture are out of scope for v1; the step 6 offline path is removed.

Defaulted (operator may override):
6. **Skin flair:** same UFO for every skin in v1; per-skin variants deferred.
7. **Sound:** deferred to v1.1.
