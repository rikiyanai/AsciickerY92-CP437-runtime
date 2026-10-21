// classic_train.h — timetabled presentation-only trains on Classic maps (FL-4876)
//
// PURPOSE:
// At map load Classic reads an optional train-route sidecar
// (schema y92.classic_train_routes.v1, produced by
// scripts/pipeline/compile_classic_train_routes.py) and, every frame, moves
// one mesh instance per car along each route polyline by the route timetable:
// enter, cruise, brake, stop centred on stop_s_m, dwell, accelerate, exit,
// then hidden until the next period.
//
// ROUTE FILE:
//   <loaded map path with ".a3d" replaced by ".trains.json">, or the path in
//   env ASCIICKER_TRAIN_ROUTES. No file => no trains, nothing else changes.
// MESH:
//   top-level "mesh" (default tokyu_7000_3car.akm), resolved through
//   ResolveMeshAssetPath (ASCIICKER_ACTIVE_MESH_ROOT, fixtures, assets/meshes)
//   with a fallback to assets/meshes/tokyu/. When "<mesh stem>.json" next to
//   the mesh lists a "consist" of per-car meshes, one instance per car is
//   placed on its own bogie chord, so the set bends at car boundaries.
// COLLISION:
//   Train instances carry INST_NO_COLLIDE and live in the world's flat list
//   (never INST_USE_TREE). World::Query skips INST_NO_COLLIDE instances unless
//   the caller sets QueryWorldCB::include_no_collide (only the renderer does),
//   and MeshInst::HitFace refuses them, so physics soup collection, the
//   multiplayer step collector and HitWorld ground probes never see a train.
// TIME:
//   Timetable clock = seconds since the trains were created, plus env
//   ASCIICKER_TRAIN_TIME_OFFSET_S (default 0).

#pragma once

#include <stdio.h>
#include <stdint.h>

struct World;

struct ClassicTrainState
{
	char route_id[64];
	float s_m;          // set-centre chainage along the polyline (metres from polyline start)
	float u_m;          // set-centre distance travelled along the run direction
	float speed_mps;
	const char* phase;  // "approach" | "brake" | "dwell" | "depart" | "hidden"
	int cars;
	int cars_visible;
	float centre_map[3]; // set centre in map units (x, y, z) when visible
	float yaw_deg;       // heading of the leading car
};

// Load (once per world) and advance all trains; stamp_us from a3dGetTime().
void ClassicTrainsTick(World* world, uint64_t stamp_us);
int ClassicTrainsCount();
bool ClassicTrainsGetState(int index, ClassicTrainState* out);
// Writes a JSON array value (no trailing newline) describing every train.
void ClassicTrainsWriteJson(FILE* f);
// Seconds on the timetable clock (including the offset), or -1 when inactive.
double ClassicTrainsClockSeconds();
