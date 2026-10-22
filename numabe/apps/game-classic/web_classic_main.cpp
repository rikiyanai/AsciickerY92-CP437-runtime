// web_classic_main.cpp - WebAssembly entry and C API for the Classic CP437 renderer.
//
// PURPOSE: Hosts the exact Classic renderer source set (makefile_game_classic_mac
// SRCS) inside a browser/node wasm module with a small camera-oriented C API.
// It replaces only the native host (engine/game_app.cpp, SDL, GL presenter).
// The renderer is driven through the same neutral Render() entry that
// engine/game_render_bridge.cpp calls (::Render with pos/yaw/zoom/light/
// perspective); no Game object, server, physics, or network is created, so the
// module works offline with a free camera.
//
// LOAD ORDER (mirrors engine/mainmenu.cpp LoadGame progress 0..3):
//   LoadSprites (once) -> LoadTerrain -> 256 material tables -> snow override
//   -> RegisterMapSprites -> LoadWorldRuntime -> UpdateMesh per mesh ->
//   LoadEnemyGens -> LoadMinimapMarkers -> RebuildWorld -> UpdateTerrainDark ->
//   CreateRenderer.
// Meshes resolve through ResolveMeshAssetPath, which honours the
// ASCIICKER_ACTIVE_MESH_ROOT environment variable; ClassicLoadMap setenv()s it
// to the caller's mesh_root (a MEMFS directory the JS host filled).
//
// CALL ORDER FOR JS: FS.writeFile map + meshes into MEMFS, ClassicLoadMap,
// ClassicSetCamera, ClassicRender (returns AnsiCell*), then ClassicPickJson /
// ClassicProjectJson against the LAST rendered frame (they read the renderer's
// sample buffer, so call ClassicRender first with the same camera).
//
// JSON strings returned by the Classic*Json functions live in static buffers
// that are overwritten by the next call of the same function.

#include <emscripten.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "terrain.h"
#include "world.h"
#include "render.h"
#include "sprite.h"
#include "sprite_registry.h"
#include "enemygen.h"
#include "mainmenu.h"
#include "game_utility.h"
#include "a3d_load_context.h"
#include "platform/time_backend.h"
#include "render/minimap_renderer.h"

// Operator 2026-10-13 "no minimap means idk where i am": the editor shows the
// game's own Classic minimap (MinimapRenderer, top-right inset of the same
// AnsiCell frame), centred on the camera target. Toggled by ClassicSetMinimap.
static int g_show_minimap = 1;

// ---------------------------------------------------------------------------
// Globals that the shared engine expects its host to own (the native host
// defines them in engine/game_app.cpp; the FL-4512 web host in web/game_web.cpp).
// ---------------------------------------------------------------------------
Terrain* terrain = 0;
World* world = 0;

namespace
{

Renderer* g_renderer = 0;
AnsiCell* g_cells = 0;
int g_cells_cap = 0;
int g_frame_w = 0;
int g_frame_h = 0;
bool g_sprites_loaded = false;
uint64_t g_frame_stamp = 0;

// Camera state fed to ::Render exactly like Game::Render feeds it.
float g_cam_pos[3] = { 0.0f, 0.0f, 0.0f };
float g_cam_yaw = 45.0f;
float g_cam_zoom = 1.0f;
int g_cam_perspective = 0;
float g_water = 55.0f;
float g_light[4] = { 1.0f, 0.0f, 1.0f, 0.5f }; // GetDefaultGameStart light

char g_json[2048];

// Deferred terrain-dark bake state (see ClassicSetLoadOptions).
int g_defer_terrain_dark = 0;
PatchIndex* g_dark_index = 0;
int g_dark_num = 0;
int g_dark_iter = 0;

// Free a previously loaded map so the same module can open another build.
void ClassicUnloadMap()
{
    if (g_dark_index)
        FreePatchIndex(g_dark_index);
    g_dark_index = 0;
    g_dark_num = 0;
    g_dark_iter = 0;
    if (g_renderer)
        DeleteRenderer(g_renderer);
    g_renderer = 0;
    if (world)
        DeleteWorld(world);
    world = 0;
    if (terrain)
        DeleteTerrain(terrain);
    terrain = 0;
    FreeMinimapMarkers();
}

} // namespace

extern "C"
{

// ClassicSetLoadOptions: defer_terrain_dark=1 makes the next ClassicLoadMap skip
// the baked terrain-shadow pass (fast load, unshadowed terrain until the host
// calls ClassicBakeTerrainDark). Default 0 = bake during ClassicLoadMap.
EMSCRIPTEN_KEEPALIVE void ClassicSetLoadOptions(int defer_terrain_dark)
{
    g_defer_terrain_dark = defer_terrain_dark ? 1 : 0;
}

// ClassicBakeTerrainDark: bake up to max_patches more terrain patches of the
// loaded map (UpdateTerrainDark, as engine/mainmenu.cpp does per frame).
// Returns the number of patches still pending (0 = done).
EMSCRIPTEN_KEEPALIVE int ClassicBakeTerrainDark(int max_patches)
{
    if (!g_dark_index || !terrain || !world)
        return 0;
    float lt[4] = { g_light[0], g_light[1], g_light[2], g_light[3] };
    int end = g_dark_iter + (max_patches > 0 ? max_patches : 0);
    if (end > g_dark_num)
        end = g_dark_num;
    for (int n = g_dark_iter; n < end; n++)
        UpdateTerrainDark(terrain, g_dark_index + n, world, lt, false);
    g_dark_iter = end;
    int pending = g_dark_num - g_dark_iter;
    if (pending <= 0)
    {
        FreePatchIndex(g_dark_index);
        g_dark_index = 0;
        g_dark_num = 0;
        g_dark_iter = 0;
        pending = 0;
    }
    return pending;
}

// ClassicLoadMap: load an .a3d (already in MEMFS) and its meshes.
// a3d_path: MEMFS path of the .a3d. mesh_root: MEMFS directory holding the
// .akm files (becomes ASCIICKER_ACTIVE_MESH_ROOT; may be null/empty to keep the
// engine default). Returns 1 on success, 0 on failure (reason printed).
EMSCRIPTEN_KEEPALIVE int ClassicLoadMap(const char* a3d_path, const char* mesh_root)
{
    if (!a3d_path || !a3d_path[0])
        return 0;

    if (mesh_root && mesh_root[0])
        setenv("ASCIICKER_ACTIVE_MESH_ROOT", mesh_root, 1);

    if (!g_sprites_loaded)
    {
        LoadSprites();
        g_sprites_loaded = true;
    }

    ClassicUnloadMap();

    strncpy(g_loaded_a3d_path, a3d_path, sizeof(g_loaded_a3d_path) - 1);
    g_loaded_a3d_path[sizeof(g_loaded_a3d_path) - 1] = 0;

    FILE* f = fopen(a3d_path, "rb");
    if (!f)
    {
        printf("[CLASSIC-WEB] cannot open %s\n", a3d_path);
        return 0;
    }

    extern Material mat[256];
    PatchIndex* index = 0;
    terrain = LoadTerrain(f, &index);
    if (!terrain)
    {
        printf("[CLASSIC-WEB] LoadTerrain failed\n");
        fclose(f);
        return 0;
    }
    int patch_num = GetTerrainPatches(terrain);

    for (int i = 0; i < 256; i++)
    {
        if (fread(mat[i].shade, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
            break;
        material_glyph_plane_free(mat[i].glyph_plane);
        mat[i].glyph_plane = NULL;
    }
    // Snow material override, identical to engine/mainmenu.cpp LoadGame.
    for (int r = 0; r < 4; r++)
        for (int s = 0; s < 16; s++)
        {
            float sf = 1.0f - (s / 16.0f) * 0.15f;
            uint8_t snow_gl[] = { '*', '+', '.', ' ' };
            mat[250].shade[r][s].fg[0] = (uint8_t)(255 * sf);
            mat[250].shade[r][s].fg[1] = (uint8_t)(255 * sf);
            mat[250].shade[r][s].fg[2] = (uint8_t)(255 * sf);
            mat[250].shade[r][s].gl = snow_gl[r];
            mat[250].shade[r][s].bg[0] = (uint8_t)(255 * sf);
            mat[250].shade[r][s].bg[1] = (uint8_t)(255 * sf);
            mat[250].shade[r][s].bg[2] = (uint8_t)(255 * sf);
            mat[250].shade[r][s].flags = 0;
        }

    RegisterMapSprites(a3d_path);
    world = LoadWorldRuntime(f);
    if (!world)
    {
        const char* why = WorldLoadRejectReason();
        printf("[CLASSIC-WEB] world load REJECTED (%s)\n", why ? why : "unknown");
        fclose(f);
        return 0;
    }

    float pos[3] = { 0, 0, 0 };
    float yaw = 0, dir = 0;
    WorldGetPlayerStart(world, pos, &yaw, &dir);

    int meshes = 0, meshes_ok = 0;
    for (Mesh* m = GetFirstMesh(world); m; m = GetNextMesh(m))
    {
        char name[256];
        GetMeshName(m, name, 256);
        char path[4096];
        ResolveMeshAssetPath(path, sizeof(path), base_path, name);
        meshes++;
        if (UpdateMesh(m, path))
            meshes_ok++;
    }
    printf("[CLASSIC-WEB] meshes %d loaded %d\n", meshes, meshes_ok);

    LoadEnemyGens(f);
    LoadMinimapMarkers(f);
    fclose(f);

    RebuildWorld(world, true);

    // Terrain-dark (baked terrain shadow) pass, same call as mainmenu.cpp.
    // It dominates load time on large maps, so a host may defer it with
    // ClassicSetLoadOptions(1) and drive ClassicBakeTerrainDark in slices.
    g_dark_index = index;
    g_dark_num = patch_num;
    g_dark_iter = 0;
    if (!g_defer_terrain_dark)
        ClassicBakeTerrainDark(patch_num + 1);

    g_frame_stamp = a3dGetTime();
    g_renderer = CreateRenderer(g_frame_stamp);
    if (g_renderer)
        RendererSetCurvatureFromPublished(g_renderer, 0, 0xFFu, 0.0f, 0);

    g_cam_pos[0] = pos[0];
    g_cam_pos[1] = pos[1];
    g_cam_pos[2] = pos[2];
    g_cam_yaw = yaw;
    printf("[CLASSIC-WEB] loaded %s patches=%d player_start=%.1f,%.1f,%.1f\n",
        a3d_path, patch_num, pos[0], pos[1], pos[2]);
    return g_renderer ? 1 : 0;
}

// ClassicSetCamera: world-unit camera target, yaw in degrees (same unit as
// Game io.yaw), zoom (1.0 = native), perspective 0/1 (session.perspective).
EMSCRIPTEN_KEEPALIVE void ClassicSetCamera(float x, float y, float z, float yaw_deg, float zoom, int perspective)
{
    g_cam_pos[0] = x;
    g_cam_pos[1] = y;
    g_cam_pos[2] = z;
    g_cam_yaw = yaw_deg;
    g_cam_zoom = zoom > 0.0f ? zoom : 1.0f;
    g_cam_perspective = perspective ? 1 : 0;
}

// ClassicGetCameraJson: the camera last set plus load/frame state.
EMSCRIPTEN_KEEPALIVE const char* ClassicGetCameraJson()
{
    snprintf(g_json, sizeof(g_json),
        "{\"x\":%.4f,\"y\":%.4f,\"z\":%.4f,\"yaw\":%.4f,\"zoom\":%.4f,\"perspective\":%d,"
        "\"water\":%.2f,\"loaded\":%d,\"cells_w\":%d,\"cells_h\":%d}",
        g_cam_pos[0], g_cam_pos[1], g_cam_pos[2], g_cam_yaw, g_cam_zoom, g_cam_perspective,
        g_water, g_renderer ? 1 : 0, g_frame_w, g_frame_h);
    return g_json;
}

// ClassicCellStride: bytes per AnsiCell (fg, bk, gl, spare) = 4.
EMSCRIPTEN_KEEPALIVE int ClassicCellStride()
{
    return (int)sizeof(AnsiCell);
}

// ClassicRender: render w_cells x h_cells and return the AnsiCell buffer
// (row-major, w*h cells, 4 bytes each). Null if no map is loaded.
EMSCRIPTEN_KEEPALIVE void* ClassicRender(int w_cells, int h_cells)
{
    if (!g_renderer || !terrain || !world || w_cells < 2 || h_cells < 2)
        return 0;
    int need = w_cells * h_cells;
    if (need > g_cells_cap)
    {
        free(g_cells);
        g_cells = (AnsiCell*)malloc(sizeof(AnsiCell) * need);
        g_cells_cap = g_cells ? need : 0;
        if (!g_cells)
            return 0;
    }
    memset(g_cells, 0, sizeof(AnsiCell) * need);
    g_frame_w = w_cells;
    g_frame_h = h_cells;

    // Same light normalisation as Game::Render (divides by n, not sqrt(n)).
    float lt[4] = { g_light[0], g_light[1], g_light[2], g_light[3] };
    float n = lt[0] * lt[0] + lt[1] * lt[1] + lt[2] * lt[2];
    if (n > 0.001f)
    {
        lt[0] /= n;
        lt[1] /= n;
        lt[2] /= n;
    }
    int ss[2] = { 0, 0 };
    g_frame_stamp = a3dGetTime();
    ::Render(g_renderer, g_frame_stamp, terrain, world, g_water, g_cam_zoom, g_cam_yaw,
        g_cam_pos, lt, w_cells, h_cells, g_cells, nullptr, ss, g_cam_perspective != 0,
        nullptr, nullptr);
    // Same call game_render_bridge.cpp makes (render_runtime_minimap), with the camera target standing in
    // for the player and its yaw as the facing arrow; frame_input = null (no NPC / remote-player dots).
    // It returns early on frames smaller than the minimap (MAP_W + 10 by MAP_H + info lines + 6).
    if (g_show_minimap)
        MinimapRenderer::Render(g_cells, w_cells, h_cells, g_cam_pos[0], g_cam_pos[1], g_cam_pos[2],
            g_cam_yaw, g_cam_yaw, g_cam_zoom, world, terrain, g_water, nullptr, nullptr, g_renderer);
    return g_cells;
}

// ClassicSetMinimap: 1 = draw the Classic minimap inset into every ClassicRender frame, 0 = off.
EMSCRIPTEN_KEEPALIVE void ClassicSetMinimap(int on)
{
    g_show_minimap = on ? 1 : 0;
}

// FL-4876 facade capture (operator 2026-10-13): live preview of an operator-converted
// XP sign. The editor writes the .xp into MEMFS and adds it as an ordinary sprite
// instance at the position the build stage (compile_sign_sprites.py) will use, so
// the operator sees the result without a rebuild. Preview instances are volatile,
// outside the BSP tree (no RebuildWorld needed) and excluded from collision.
static Inst* g_preview_inst[128];
static Sprite* g_preview_spr[128];
static unsigned g_preview_serial = 0;

EMSCRIPTEN_KEEPALIVE int ClassicPreviewSpriteAdd(const char* xp_path, float x, float y, float z, float yaw)
{
    if (!world || !xp_path || !xp_path[0])
        return -1;
    int slot = -1;
    for (int i = 0; i < 128; i++)
        if (!g_preview_inst[i])
        {
            slot = i;
            break;
        }
    if (slot < 0)
        return -1;
    char name[64];
    snprintf(name, sizeof(name), "facade_preview_%d_%u", slot, ++g_preview_serial);
    Sprite* s = LoadSprite(xp_path, name, nullptr, true, false);
    if (!s)
        return -1;
    float pos[3] = { x, y, z };
    int reps[4] = { 0, 0, 0, 0 };
    // Detached flat-list instance, no RebuildWorld. Measured equal to a map sign
    // instance at the same position (0 differing cells, 6 signs x 8 headings,
    // facade_capture_checks/sign_same_pos.mjs) once stale same-named files were
    // removed from assets/sprites; tree/registered variants drew identically.
    Inst* inst = CreateInst(world, s, INST_VISIBLE | INST_VOLATILE | INST_NO_COLLIDE, pos, yaw, 0, 0, reps, name, -1);
    if (!inst)
    {
        FreeSprite(s);
        return -1;
    }
    g_preview_inst[slot] = inst;
    g_preview_spr[slot] = s;
    return slot;
}

// Hide (visible=0) or show the mesh instances named inst_name, e.g. the generic
// place-front mesh a facade capture replaces. Returns how many matched.
// The BSP render query draws mesh instances without testing INST_VISIBLE
// (world_internal.h mesh_cb path), so HideInst alone leaves them on screen
// (measured: preview frame identical to the unhidden build). A hidden mesh is
// instead collapsed to zero scale at its own origin (bbox shrinks inside its
// BSP node, nothing rasterises); its transform is restored on show.
static Inst* g_hidden_inst[64];
static double g_hidden_tm[64][16];

EMSCRIPTEN_KEEPALIVE int ClassicSetInstVisible(const char* inst_name, int visible)
{
    if (!world || !inst_name || !inst_name[0])
        return 0;
    Inst** list = nullptr;
    int n = CollectMeshInsts(world, &list);
    int hit = 0;
    for (int i = 0; i < n; i++)
    {
        const char* nm = GetInstName(list[i]);
        if (!nm || strcmp(nm, inst_name) != 0)
            continue;
        int slot = -1, free_slot = -1;
        for (int k = 0; k < 64; k++)
        {
            if (g_hidden_inst[k] == list[i])
                slot = k;
            if (!g_hidden_inst[k] && free_slot < 0)
                free_slot = k;
        }
        if (visible && slot >= 0)
        {
            SetInstTM(list[i], g_hidden_tm[slot]);
            ShowInst(list[i]);
            g_hidden_inst[slot] = 0;
        }
        else if (!visible && slot < 0 && free_slot >= 0)
        {
            double tm[16];
            GetInstTM(list[i], tm);
            memcpy(g_hidden_tm[free_slot], tm, sizeof(tm));
            g_hidden_inst[free_slot] = list[i];
            for (int k = 0; k < 12; k++)
                if (k % 4 != 3)
                    tm[k] = 0.0;
            SetInstTM(list[i], tm);
            HideInst(list[i]);
        }
        hit++;
    }
    free(list);
    return hit;
}

// Hide (visible=0) or show map SPRITE instances named inst_name (e.g. an
// existing sign a facade capture supersedes, or a visibility measurement of a
// sign). The sprite render path does test INST_VISIBLE, so HideInst suffices;
// hidden pointers are kept because hidden sprites drop out of queries.
static Inst* g_hidden_spr[64];

struct SpriteFind
{
    const char* name;
    Inst* out[16];
    int n;
};

// Map sprite instances are created unnamed (world_serialization_a3d.cpp passes
// name 0); the record's name is the sprite asset's name, so match on that.
static const char* SpriteInstName(Inst* inst)
{
    const char* nm = GetInstName(inst);
    if (nm && nm[0])
        return nm;
    Sprite* s = GetInstSprite(inst, nullptr, nullptr, nullptr, nullptr, nullptr);
    return s ? s->name : nullptr;
}

static void SpriteFindCB(Inst* inst, Sprite*, float*, float, int, int, int*, void* cookie)
{
    SpriteFind* f = (SpriteFind*)cookie;
    const char* nm = SpriteInstName(inst);
    if (nm && f->n < 16 && strcmp(nm, f->name) == 0)
        f->out[f->n++] = inst;
}

EMSCRIPTEN_KEEPALIVE int ClassicSetSpriteVisible(const char* inst_name, int visible)
{
    if (!world || !inst_name || !inst_name[0])
        return 0;
    int hit = 0;
    if (visible)
    {
        for (int k = 0; k < 64; k++)
        {
            const char* nm = g_hidden_spr[k] ? SpriteInstName(g_hidden_spr[k]) : 0;
            if (nm && strcmp(nm, inst_name) == 0)
            {
                ShowInst(g_hidden_spr[k]);
                g_hidden_spr[k] = 0;
                hit++;
            }
        }
        return hit;
    }
    SpriteFind f = { inst_name, {}, 0 };
    QueryWorldCB cb = {};
    cb.mesh_cb = [](Inst*, Mesh*, double*, void*) {}; // QueryWorld calls mesh_cb unconditionally
    cb.sprite_cb = SpriteFindCB;
    cb.include_no_collide = true;
    QueryWorld(world, 0, nullptr, &cb, &f);
    for (int i = 0; i < f.n; i++)
        for (int k = 0; k < 64; k++)
            if (!g_hidden_spr[k])
            {
                g_hidden_spr[k] = f.out[i];
                HideInst(f.out[i]);
                hit++;
                break;
            }
    return hit;
}

EMSCRIPTEN_KEEPALIVE void ClassicPreviewSpriteRemove(int slot)
{
    if (slot < 0 || slot >= 128 || !g_preview_inst[slot])
        return;
    for (int k = 0; k < 64; k++) // never leave a hidden-list pointer to a deleted instance
        if (g_hidden_spr[k] == g_preview_inst[slot])
            g_hidden_spr[k] = 0;
    DeleteInst(g_preview_inst[slot]);
    FreeSprite(g_preview_spr[slot]);
    g_preview_inst[slot] = 0;
    g_preview_spr[slot] = 0;
}

// ClassicPickJson: world xyz under cell (cx,cy) of the last rendered frame,
// from the renderer depth buffer (UnprojectCoords2D, the engine's mouse-pick
// path). Adds the instance hit by a ray through that point when a mesh is hit.
EMSCRIPTEN_KEEPALIVE const char* ClassicPickJson(int cx, int cy)
{
    float p[3] = { 0, 0, 0 };
    int xy[2] = { cx, cy };
    bool ok = g_renderer && UnprojectCoords2D(g_renderer, xy, p);
    if (!ok)
    {
        snprintf(g_json, sizeof(g_json), "{\"hit\":false,\"cx\":%d,\"cy\":%d}", cx, cy);
        return g_json;
    }

    // UnprojectCoords* return x,y premultiplied by HEIGHT_CELLS (the renderer's
    // internal "premultiplied world", see render_scene.cpp view_pos); ProjectCoords
    // and Render() take plain world units, so divide to stay in world units.
    p[0] /= HEIGHT_CELLS;
    p[1] /= HEIGHT_CELLS;

    // Instance under the cell: cast a ray from the camera-side towards the hit.
    char inst_json[512] = "";
    if (world)
    {
        int a[3] = { cx, cy, 0 };
        int b[3] = { cx, cy, 1000000 };
        float p0[3], p1[3];
        if (UnprojectCoords3D(g_renderer, a, p0) && UnprojectCoords3D(g_renderer, b, p1))
        {
            p0[0] /= HEIGHT_CELLS; p0[1] /= HEIGHT_CELLS;
            p1[0] /= HEIGHT_CELLS; p1[1] /= HEIGHT_CELLS;
            double v[3] = { (double)p1[0] - p0[0], (double)p1[1] - p0[1], (double)p1[2] - p0[2] };
            double len = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
            if (len > 1e-9)
            {
                v[0] /= len; v[1] /= len; v[2] /= len;
                // Orient the ray away from the eye: orthographic eyes are above the
                // scene (ray points down); perspective eyes are behind the camera
                // along the yaw heading (view_dir = (-sin yaw, cos yaw)).
                const double yaw_r = (double)g_cam_yaw * M_PI / 180.0;
                const double fwd = v[0] * (-sin(yaw_r)) + v[1] * cos(yaw_r);
                const bool flip = g_cam_perspective ? (fwd < 0) : (v[2] > 0);
                if (flip) { v[0] = -v[0]; v[1] = -v[1]; v[2] = -v[2]; }
                const double L = 4096.0;
                double o[3] = { (double)p[0] - v[0] * L, (double)p[1] - v[1] * L, (double)p[2] - v[2] * L };
                double ret[4] = { 0, 0, 0, 1 }, nrm[3];
                Inst* hit = HitWorld(world, o, v, ret, nrm, true, HitFilter(false, false, false));
                // The depth-buffer point is the first visible surface; accept the
                // mesh hit only if it is that same surface (within a few cells).
                double dx = ret[0] - p[0], dy = ret[1] - p[1], dz = ret[2] - p[2];
                if (hit && sqrt(dx * dx + dy * dy + dz * dz) > 48.0)
                    hit = 0;
                if (hit)
                {
                    Mesh* mesh = GetInstMesh(hit);
                    char mname[256] = "";
                    if (mesh)
                        GetMeshName(mesh, mname, sizeof(mname));
                    const char* iname = GetInstName(hit);
                    snprintf(inst_json, sizeof(inst_json),
                        ",\"inst\":{\"id\":%llu,\"name\":\"%s\",\"mesh\":\"%s\",\"hit\":[%.3f,%.3f,%.3f]}",
                        (unsigned long long)(uintptr_t)hit, iname ? iname : "", mname,
                        ret[0], ret[1], ret[2]);
                }
            }
        }
    }
    snprintf(g_json, sizeof(g_json),
        "{\"hit\":true,\"cx\":%d,\"cy\":%d,\"x\":%.4f,\"y\":%.4f,\"z\":%.4f%s}",
        cx, cy, p[0], p[1], p[2], inst_json);
    return g_json;
}

// ClassicGroundJson: terrain height and top-most surface height (terrain or
// mesh) under world (x,y), by downward rays from above the map, the same
// HitTerrain/HitWorld calls the physics ground probe uses. Heights are in
// engine z units: 1 metre = content_scale * HEIGHT_SCALE(16) z units.
EMSCRIPTEN_KEEPALIVE const char* ClassicGroundJson(float x, float y)
{
    if (!terrain || !world)
    {
        snprintf(g_json, sizeof(g_json), "{\"hit\":false}");
        return g_json;
    }
    double p[3] = { (double)x, (double)y, 70000.0 };
    double v[3] = { 0, 0, -1 };
    double r[4] = { 0, 0, 0, 1 };
    Patch* patch = HitTerrain(terrain, p, v, r, 0, true);
    double terrain_z = patch ? r[2] : -1.0;
    double r2[4] = { 0, 0, 0, 1 }, n2[3];
    Inst* inst = HitWorld(world, p, v, r2, n2, true, HitFilter(false, false, false));
    double top_z = (inst && r2[2] > terrain_z) ? r2[2] : terrain_z;
    snprintf(g_json, sizeof(g_json),
        "{\"hit\":%s,\"x\":%.4f,\"y\":%.4f,\"terrain_z\":%.4f,\"top_z\":%.4f,\"top_is_mesh\":%s}",
        patch ? "true" : "false", x, y, terrain_z, top_z, (inst && r2[2] > terrain_z) ? "true" : "false");
    return g_json;
}

// ClassicProjectJson: cell x,y (and depth) of a world point in the last frame.
EMSCRIPTEN_KEEPALIVE const char* ClassicProjectJson(float x, float y, float z)
{
    float p[3] = { x, y, z };
    int v[3] = { 0, 0, 0 };
    bool ok = g_renderer && ProjectCoords(g_renderer, p, v);
    snprintf(g_json, sizeof(g_json),
        "{\"visible\":%s,\"cx\":%d,\"cy\":%d,\"depth\":%d}", ok ? "true" : "false", v[0], v[1], v[2]);
    return g_json;
}

} // extern "C"

// Entry point is unused: the JS host calls the exports above directly.
int main(int argc, char* argv[])
{
    (void)argc;
    (void)argv;
    return 0;
}
