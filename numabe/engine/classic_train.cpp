// classic_train.cpp — timetabled presentation-only trains on Classic maps (FL-4876)
//
// See classic_train.h for the contract. This translation unit is compiled
// through game_render_bridge.cpp (unity include, FL-4876 TRAIN-RUNTIME-01)
// because the per-product source lists live in apps/*/makefile_* outside the
// engine-owned paths; registering it as its own source is open work.

#include "classic_train.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "world.h"
#include "a3d_load_context.h"
#include "third_party/cjson/cJSON.h"

namespace {

static const int kMaxRoutes = 8;
static const int kMaxCars = 8;
static const int kMaxPoints = 4096;
static const double kHeightScale = 16.0; // map z units per xy unit (instance tm[10])
static const double kBogieHalfM = 6.3;   // half bogie-centre distance (est., see generator)

struct TrainCar
{
	Inst* inst;
	Mesh* mesh;
	double length_m;
	double offset_m; // car centre minus set centre, along the run direction (+ = ahead)
	bool visible;
};

struct TrainRoute
{
	char id[64];
	int n;
	double* xyz;     // n * 3 map units
	double* cum;     // n cumulative xy length in map units
	double total_units;
	double length_m;
	double stop_s_m;
	double upm;
	int direction;
	double period_s, phase_s, dwell_s, cruise_mps, accel_mps2;
	// derived timetable (run coordinate u, metres along the run direction)
	double set_len_m;
	double u_enter, u_stop, u_exit;
	double v_in, t_cruise_in, t_brake, t_acc, t_cruise_out, v_out;
	double t_run;
	TrainCar car[kMaxCars];
	int cars;
	ClassicTrainState state;
};

struct TrainSet
{
	World* world;
	uint64_t world_generation;
	bool attempted;
	bool active;
	uint64_t t0_us;
	double offset_s;
	double clock_s;
	TrainRoute route[kMaxRoutes];
	int routes;
};

TrainSet g_trains = {};

bool FileExists(const char* p)
{
	struct stat st;
	return p && p[0] && stat(p, &st) == 0 && S_ISREG(st.st_mode);
}

char* ReadWhole(const char* path)
{
	FILE* f = fopen(path, "rb");
	if (!f)
		return nullptr;
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	if (n <= 0 || n > 64L * 1024 * 1024)
	{
		fclose(f);
		return nullptr;
	}
	char* buf = (char*)malloc((size_t)n + 1);
	if (!buf)
	{
		fclose(f);
		return nullptr;
	}
	size_t got = fread(buf, 1, (size_t)n, f);
	fclose(f);
	buf[got] = 0;
	return buf;
}

double NumOr(const cJSON* obj, const char* key, double fallback)
{
	const cJSON* v = cJSON_GetObjectItemCaseSensitive(obj, key);
	return cJSON_IsNumber(v) ? v->valuedouble : fallback;
}

// Resolve a train mesh the way world meshes resolve, then fall back to
// assets/meshes/tokyu/<name>.
bool ResolveTrainMesh(char* out, int out_size, const char* name)
{
	if (ResolveMeshAssetPath(out, out_size, base_path, name) && FileExists(out))
		return true;
	snprintf(out, out_size, "%sassets/meshes/tokyu/%s", base_path, name);
	return FileExists(out);
}

void DerivedRoutePath(char* out, int out_size)
{
	out[0] = 0;
	const char* env = getenv("ASCIICKER_TRAIN_ROUTES");
	if (env && env[0])
	{
		snprintf(out, out_size, "%s", env);
		return;
	}
	if (!g_loaded_a3d_path[0])
		return;
	snprintf(out, out_size, "%s", g_loaded_a3d_path);
	size_t len = strlen(out);
	if (len >= 4 && strcmp(out + len - 4, ".a3d") == 0)
		out[len - 4] = 0;
	size_t base = strlen(out);
	snprintf(out + base, out_size - (int)base, "%s", ".trains.json");
}

// Point on the polyline at chainage s (map units), extrapolated linearly
// beyond either end along the end segment.
void PointAt(const TrainRoute& r, double s, double p[3])
{
	int seg = 0;
	if (s <= 0)
		seg = 0;
	else if (s >= r.total_units)
		seg = r.n - 2;
	else
	{
		int lo = 0, hi = r.n - 1;
		while (hi - lo > 1)
		{
			int mid = (lo + hi) / 2;
			if (r.cum[mid] <= s)
				lo = mid;
			else
				hi = mid;
		}
		seg = lo;
	}
	const double* a = r.xyz + seg * 3;
	const double* b = r.xyz + (seg + 1) * 3;
	double L = r.cum[seg + 1] - r.cum[seg];
	double t = L > 1e-9 ? (s - r.cum[seg]) / L : 0.0;
	for (int k = 0; k < 3; k++)
		p[k] = a[k] + (b[k] - a[k]) * t;
}

// The route polyline z is DEM ground (compile_classic_train_routes.py samples
// the DEM-only terrain), while the baked rail meshes put the rail head a few
// decimetres higher. Snap each vertex to the first world surface found by a
// short downward probe that starts 1.5 m above the polyline (below platform
// canopies and overbridges) when that surface lies within -0.6..+1.0 m of the
// polyline. Runs once at load, before any train instance exists.
void SnapRailTop(TrainRoute& r, World* w)
{
	const double zu = r.upm * kHeightScale; // map z units per metre
	int snapped = 0;
	double sum_dz = 0;
	for (int k = 0; k < r.n; k++)
	{
		double* p = r.xyz + k * 3;
		double probe[3] = { p[0], p[1], p[2] + 1.5 * zu };
		double down[3] = { 0, 0, -2.1 * zu };
		double ret[4] = { 0, 0, 0, 1 };
		if (HitWorld(w, probe, down, ret, 0, true))
		{
			double dz = ret[2] - p[2];
			if (dz >= -0.6 * zu && dz <= 1.0 * zu)
			{
				p[2] = ret[2];
				snapped++;
				sum_dz += dz;
			}
		}
	}
	printf("[train] route %s rail-top snap: %d/%d vertices, mean dz %.2f m\n",
		r.id, snapped, r.n, snapped ? sum_dz / snapped / zu : 0.0);
}

void DeriveTimetable(TrainRoute& r)
{
	double v = r.cruise_mps > 0.1 ? r.cruise_mps : 12.0;
	double a = r.accel_mps2 > 0.05 ? r.accel_mps2 : 0.8;
	double half = r.set_len_m * 0.5;
	double stop_run = r.direction >= 0 ? r.stop_s_m : r.length_m - r.stop_s_m;
	r.u_enter = -half;
	r.u_stop = stop_run;
	r.u_exit = r.length_m + half;
	double d_in = r.u_stop - r.u_enter;
	double d_brake = v * v / (2 * a);
	if (d_in < d_brake)
	{
		r.v_in = sqrt(2 * a * fmax(d_in, 0.0));
		d_brake = fmax(d_in, 0.0);
	}
	else
		r.v_in = v;
	r.t_cruise_in = r.v_in > 1e-6 ? (d_in - d_brake) / r.v_in : 0.0;
	r.t_brake = r.v_in / a;
	double d_out = r.u_exit - r.u_stop;
	double d_acc = v * v / (2 * a);
	if (d_out < d_acc)
	{
		r.t_acc = sqrt(2 * fmax(d_out, 0.0) / a);
		r.v_out = a * r.t_acc;
		r.t_cruise_out = 0;
	}
	else
	{
		r.t_acc = v / a;
		r.v_out = v;
		r.t_cruise_out = (d_out - d_acc) / v;
	}
	r.t_run = r.t_cruise_in + r.t_brake + r.dwell_s + r.t_acc + r.t_cruise_out;
	if (r.period_s < r.t_run)
	{
		printf("[train] %s period %.1fs shorter than run %.1fs; period raised\n", r.id, r.period_s, r.t_run);
		r.period_s = r.t_run;
	}
}

// Set-centre run position u (metres), speed and phase name at timetable time.
bool Evaluate(const TrainRoute& r, double clock_s, double* u, double* speed, const char** phase)
{
	double tau = fmod(clock_s - r.phase_s, r.period_s);
	if (tau < 0)
		tau += r.period_s;
	double a = r.accel_mps2 > 0.05 ? r.accel_mps2 : 0.8;
	double t = tau;
	if (t < r.t_cruise_in)
	{
		*u = r.u_enter + r.v_in * t;
		*speed = r.v_in;
		*phase = "approach";
		return true;
	}
	t -= r.t_cruise_in;
	if (t < r.t_brake)
	{
		double d_brake = r.v_in * r.t_brake * 0.5;
		double rem = r.t_brake - t;
		*u = r.u_stop - 0.5 * a * rem * rem;
		(void)d_brake;
		*speed = a * rem;
		*phase = "brake";
		return true;
	}
	t -= r.t_brake;
	if (t < r.dwell_s)
	{
		*u = r.u_stop;
		*speed = 0;
		*phase = "dwell";
		return true;
	}
	t -= r.dwell_s;
	if (t < r.t_acc)
	{
		*u = r.u_stop + 0.5 * a * t * t;
		*speed = a * t;
		*phase = "depart";
		return true;
	}
	t -= r.t_acc;
	if (t < r.t_cruise_out)
	{
		*u = r.u_stop + 0.5 * a * r.t_acc * r.t_acc + r.v_out * t;
		*speed = r.v_out;
		*phase = "depart";
		return true;
	}
	*u = r.u_exit;
	*speed = 0;
	*phase = "hidden";
	return false;
}

// Map-unit chainage of run position u (metres).
double ChainageUnits(const TrainRoute& r, double u_m)
{
	double s_m = r.direction >= 0 ? u_m : r.length_m - u_m;
	return s_m * r.upm;
}

void PlaceCar(TrainRoute& r, TrainCar& c, double u_centre_m, bool lead)
{
	double u = u_centre_m + c.offset_m;
	bool on_track = u >= 0.0 && u <= r.length_m;
	if (!on_track)
	{
		if (c.visible)
			HideInst(c.inst);
		c.visible = false;
		return;
	}
	// Body sits on its two bogies: centre = bogie-chord midpoint, heading
	// along the chord, pitch from the chord grade.
	double b = c.length_m > 2 * kBogieHalfM ? kBogieHalfM : c.length_m * 0.35;
	double pf[3], pr[3];
	PointAt(r, ChainageUnits(r, u + b), pf);
	PointAt(r, ChainageUnits(r, u - b), pr);
	double dx = pf[0] - pr[0], dy = pf[1] - pr[1];
	double dz = (pf[2] - pr[2]) / kHeightScale; // grade in xy units
	double h = sqrt(dx * dx + dy * dy);
	if (h < 1e-6)
		return;
	double fx = dx / h, fy = dy / h;
	double L3 = sqrt(h * h + dz * dz);
	double cp = h / L3, sp = dz / L3;
	double tm[16] = {
		fx * cp, fy * cp, sp * kHeightScale, 0,
		-fy, fx, 0, 0,
		-fx * sp, -fy * sp, cp * kHeightScale, 0,
		(pf[0] + pr[0]) * 0.5, (pf[1] + pr[1]) * 0.5, (pf[2] + pr[2]) * 0.5, 1,
	};
	SetInstTM(c.inst, tm);
	if (!c.visible)
		ShowInst(c.inst);
	c.visible = true;
	if (lead)
	{
		r.state.yaw_deg = (float)(atan2(fy, fx) * 180.0 / M_PI);
	}
}

void LoadConsist(TrainRoute& r, World* w, const char* mesh_path, const char* mesh_name)
{
	r.cars = 0;
	// "<stem>.json" next to the resolved mesh may list per-car meshes.
	char json_path[4096];
	snprintf(json_path, sizeof(json_path), "%s", mesh_path);
	size_t len = strlen(json_path);
	if (len > 4 && strcmp(json_path + len - 4, ".akm") == 0)
		json_path[len - 4] = 0;
	size_t base = strlen(json_path);
	snprintf(json_path + base, sizeof(json_path) - base, "%s", ".json");
	char* text = ReadWhole(json_path);
	cJSON* doc = text ? cJSON_Parse(text) : nullptr;
	free(text);
	const cJSON* consist = doc ? cJSON_GetObjectItemCaseSensitive(doc, "consist") : nullptr;
	if (cJSON_IsArray(consist))
	{
		const cJSON* item = nullptr;
		cJSON_ArrayForEach(item, consist)
		{
			if (r.cars >= kMaxCars)
				break;
			const cJSON* m = cJSON_GetObjectItemCaseSensitive(item, "mesh");
			if (!cJSON_IsString(m))
				continue;
			char car_path[4096];
			snprintf(car_path, sizeof(car_path), "%s", mesh_path);
			char* slash = strrchr(car_path, '/');
			if (slash)
				snprintf(slash + 1, sizeof(car_path) - (slash + 1 - car_path), "%s", m->valuestring);
			if (!FileExists(car_path) && !ResolveTrainMesh(car_path, sizeof(car_path), m->valuestring))
				continue;
			TrainCar& c = r.car[r.cars];
			c.mesh = FindOrLoadMesh(w, car_path, m->valuestring);
			c.length_m = NumOr(item, "length_m", 18.0);
			if (c.mesh)
				r.cars++;
		}
	}
	cJSON_Delete(doc);
	if (r.cars == 0)
	{
		TrainCar& c = r.car[0];
		c.mesh = FindOrLoadMesh(w, mesh_path, mesh_name);
		if (!c.mesh)
			return;
		double bb[6] = {0};
		float fb[6];
		GetMeshBBox(c.mesh, fb);
		for (int k = 0; k < 6; k++)
			bb[k] = fb[k];
		c.length_m = (bb[1] - bb[0]) / r.upm;
		r.cars = 1;
	}
	r.set_len_m = 0;
	for (int k = 0; k < r.cars; k++)
		r.set_len_m += r.car[k].length_m;
	double ahead = r.set_len_m * 0.5;
	for (int k = 0; k < r.cars; k++)
	{
		r.car[k].offset_m = ahead - r.car[k].length_m * 0.5;
		ahead -= r.car[k].length_m;
	}
}

void Load(World* w)
{
	char path[4096];
	DerivedRoutePath(path, sizeof(path));
	if (!FileExists(path))
	{
		printf("[train] no route file at %s; trains disabled\n", path[0] ? path : "(no map path)");
		fflush(stdout);
		return;
	}
	char* text = ReadWhole(path);
	cJSON* doc = text ? cJSON_Parse(text) : nullptr;
	free(text);
	if (!doc)
	{
		printf("[train] route file %s is not JSON; trains disabled\n", path);
		fflush(stdout);
		return;
	}
	const cJSON* schema = cJSON_GetObjectItemCaseSensitive(doc, "schema");
	if (!cJSON_IsString(schema) || strcmp(schema->valuestring, "y92.classic_train_routes.v1") != 0)
		printf("[train] warning: unexpected schema in %s\n", path);
	const cJSON* mesh_j = cJSON_GetObjectItemCaseSensitive(doc, "mesh");
	const char* mesh_name = cJSON_IsString(mesh_j) ? mesh_j->valuestring : "tokyu_7000_3car.akm";
	char mesh_path[4096];
	if (!ResolveTrainMesh(mesh_path, sizeof(mesh_path), mesh_name))
	{
		printf("[train] mesh %s not found; trains disabled\n", mesh_name);
		fflush(stdout);
		cJSON_Delete(doc);
		return;
	}
	const cJSON* routes = cJSON_GetObjectItemCaseSensitive(doc, "routes");
	const cJSON* rj = nullptr;
	cJSON_ArrayForEach(rj, routes)
	{
		if (g_trains.routes >= kMaxRoutes)
			break;
		const cJSON* poly = cJSON_GetObjectItemCaseSensitive(rj, "polyline");
		int n = cJSON_GetArraySize(poly);
		if (n < 2 || n > kMaxPoints)
			continue;
		TrainRoute& r = g_trains.route[g_trains.routes];
		memset(&r, 0, sizeof(r));
		const cJSON* id = cJSON_GetObjectItemCaseSensitive(rj, "id");
		snprintf(r.id, sizeof(r.id), "%s", cJSON_IsString(id) ? id->valuestring : "route");
		r.xyz = (double*)calloc((size_t)n * 3, sizeof(double));
		r.cum = (double*)calloc((size_t)n, sizeof(double));
		int k = 0;
		const cJSON* pt = nullptr;
		cJSON_ArrayForEach(pt, poly)
		{
			for (int c = 0; c < 3; c++)
			{
				const cJSON* v = cJSON_GetArrayItem(pt, c);
				r.xyz[k * 3 + c] = cJSON_IsNumber(v) ? v->valuedouble : 0.0;
			}
			if (k > 0)
			{
				double dx = r.xyz[k * 3] - r.xyz[k * 3 - 3];
				double dy = r.xyz[k * 3 + 1] - r.xyz[k * 3 - 2];
				r.cum[k] = r.cum[k - 1] + sqrt(dx * dx + dy * dy);
			}
			k++;
		}
		r.n = n;
		r.total_units = r.cum[n - 1];
		r.upm = NumOr(rj, "units_per_metre", 3.421334928531115);
		r.length_m = NumOr(rj, "length_m", r.total_units / r.upm);
		r.stop_s_m = NumOr(rj, "stop_s_m", r.length_m * 0.5);
		r.direction = NumOr(rj, "direction", 1) < 0 ? -1 : 1;
		const cJSON* tt = cJSON_GetObjectItemCaseSensitive(rj, "timetable");
		r.period_s = NumOr(tt, "period_s", 150);
		r.phase_s = NumOr(tt, "phase_s", 0);
		r.dwell_s = NumOr(tt, "dwell_s", 20);
		r.cruise_mps = NumOr(tt, "cruise_mps", 12);
		r.accel_mps2 = NumOr(tt, "accel_mps2", 0.8);
		LoadConsist(r, w, mesh_path, mesh_name);
		if (r.cars == 0)
		{
			free(r.xyz);
			free(r.cum);
			continue;
		}
		SnapRailTop(r, w);
		DeriveTimetable(r);
		for (int c = 0; c < r.cars; c++)
		{
			char name[96];
			snprintf(name, sizeof(name), "classic_train:%s:%d", r.id, c);
			double tm[16] = {1,0,0,0, 0,1,0,0, 0,0,kHeightScale,0, r.xyz[0],r.xyz[1],r.xyz[2],1};
			// Flat list (no INST_USE_TREE), skipped by save (VOLATILE), never
			// collided (NO_COLLIDE); starts hidden until the timetable shows it.
			r.car[c].inst = CreateInst(r.car[c].mesh, INST_VOLATILE | INST_NO_COLLIDE, tm, name, -1);
			r.car[c].visible = false;
		}
		snprintf(r.state.route_id, sizeof(r.state.route_id), "%s", r.id);
		r.state.cars = r.cars;
		printf("[train] route %s: %d pts %.1f m stop %.1f m dir %+d cars %d set %.1f m "
			"run %.1fs (in %.1f+%.1f dwell %.1f out %.1f+%.1f) period %.1fs phase %.1fs\n",
			r.id, r.n, r.length_m, r.stop_s_m, r.direction, r.cars, r.set_len_m, r.t_run,
			r.t_cruise_in, r.t_brake, r.dwell_s, r.t_acc, r.t_cruise_out, r.period_s, r.phase_s);
		g_trains.routes++;
	}
	cJSON_Delete(doc);
	printf("[train] loaded %d route(s) from %s mesh=%s\n", g_trains.routes, path, mesh_path);
	fflush(stdout);
	g_trains.active = g_trains.routes > 0;
}

void Reset()
{
	for (int i = 0; i < g_trains.routes; i++)
	{
		free(g_trains.route[i].xyz);
		free(g_trains.route[i].cum);
	}
	memset(&g_trains, 0, sizeof(g_trains));
}

} // namespace

void ClassicTrainsTick(World* world, uint64_t stamp_us)
{
#ifdef EDITOR
	// Authoring worlds are saved documents; no presentation trains there.
	(void)world;
	(void)stamp_us;
	return;
#endif
	if (!world)
		return;
	uint64_t gen = GetWorldGeneration(world);
	if (g_trains.attempted && (g_trains.world != world || g_trains.world_generation != gen))
		Reset(); // the previous world (and its instances) is gone
	if (!g_trains.attempted)
	{
		g_trains.attempted = true;
		g_trains.world = world;
		g_trains.world_generation = gen;
		g_trains.t0_us = stamp_us;
		const char* off = getenv("ASCIICKER_TRAIN_TIME_OFFSET_S");
		g_trains.offset_s = off && off[0] ? atof(off) : 0.0;
		Load(world);
	}
	if (!g_trains.active)
		return;
	g_trains.clock_s = (double)(stamp_us - g_trains.t0_us) * 1e-6 + g_trains.offset_s;
	for (int i = 0; i < g_trains.routes; i++)
	{
		TrainRoute& r = g_trains.route[i];
		double u = 0, speed = 0;
		const char* phase = "hidden";
		bool shown = Evaluate(r, g_trains.clock_s, &u, &speed, &phase);
		r.state.u_m = (float)u;
		r.state.s_m = (float)(r.direction >= 0 ? u : r.length_m - u);
		r.state.speed_mps = (float)speed;
		r.state.phase = phase;
		r.state.cars_visible = 0;
		for (int c = 0; c < r.cars; c++)
		{
			if (shown)
				PlaceCar(r, r.car[c], u, c == 0);
			else if (r.car[c].visible)
			{
				HideInst(r.car[c].inst);
				r.car[c].visible = false;
			}
			if (r.car[c].visible)
				r.state.cars_visible++;
		}
		double p[3] = {0, 0, 0};
		PointAt(r, ChainageUnits(r, u), p);
		for (int k = 0; k < 3; k++)
			r.state.centre_map[k] = (float)p[k];
		if (r.state.cars_visible == 0 && shown)
			r.state.phase = phase; // on the map edge, every car still off-track
	}
}

int ClassicTrainsCount()
{
	return g_trains.active ? g_trains.routes : 0;
}

bool ClassicTrainsGetState(int index, ClassicTrainState* out)
{
	if (!g_trains.active || index < 0 || index >= g_trains.routes || !out)
		return false;
	*out = g_trains.route[index].state;
	return true;
}

double ClassicTrainsClockSeconds()
{
	return g_trains.active ? g_trains.clock_s : -1.0;
}

void ClassicTrainsWriteJson(FILE* f)
{
	fprintf(f, "[");
	for (int i = 0; i < ClassicTrainsCount(); i++)
	{
		const ClassicTrainState& s = g_trains.route[i].state;
		fprintf(f, "%s{\"route_id\": \"%s\", \"clock_s\": %.3f, \"s_m\": %.3f, \"u_m\": %.3f, "
			"\"speed_mps\": %.3f, \"phase\": \"%s\", \"visible\": %s, \"cars\": %d, "
			"\"cars_visible\": %d, \"centre_map\": [%.3f, %.3f, %.3f], \"yaw_deg\": %.2f}",
			i ? ", " : "", s.route_id, g_trains.clock_s, s.s_m, s.u_m, s.speed_mps,
			s.phase ? s.phase : "hidden", s.cars_visible > 0 ? "true" : "false", s.cars,
			s.cars_visible, s.centre_map[0], s.centre_map[1], s.centre_map[2], s.yaw_deg);
	}
	fprintf(f, "]");
}
