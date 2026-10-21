// minimap_renderer.cpp — In-game minimap rendering
//
// Extracted from engine/game_render_bridge.cpp (RenderMinimap) and
// engine/game.cpp (helper functions).
// SEE ALSO: minimap_renderer.h, game.h, world.h

#include "minimap_renderer.h"
#include "render_frame_input.h"
#include "render_frame_report.h"
#include "render.h"
#include "render_s3.h"
#include "world.h"
#include "terrain.h"
#include "talkbox.h"

#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
#include <algorithm>
#include <vector>

// Global material array (defined in game_app.cpp).
extern Material mat[256];

// MINIMAP-01: labels that fit neither side of their glyph are suppressed
// (the glyph stays). The per-frame count lives here because the report and
// telemetry-state headers are owned outside this lane; game_utility.cpp reads
// it through GetMinimapMarkerLabelsSuppressed() for the debug dump.
static int g_minimap_marker_labels_suppressed = 0;
int GetMinimapMarkerLabelsSuppressed(void)
{
    return g_minimap_marker_labels_suppressed;
}

struct MinimapFootprintRaster
{
    double tm[16];
    bool* cells;
    int width;
    int height;
    float center_x;
    float center_y;
    float scale;
    // FL-4257 — non-null under spherical curvature: each face vertex is mapped
    // to its player-centred tangent offset (RenderS3MinimapProject) so building
    // footprints bend onto the same transported geodesic frame as the terrain
    // background. Null/Euclidean keeps the chart-linear footprint.
    const Renderer* renderer;
};

// ── Forward declarations for internal helpers (static — internal linkage) ──

// DrawMiniText is declared in minimap_renderer.h (external linkage) — no forward decl needed.
uint8_t MinimapRgbToAnsiIndex(int r, int g, int b);
static uint8_t MinimapMaterialGlyph(int mat_id);
void MinimapApplyTerrainCell(AnsiCell* cell, int mat_id, uint16_t h, float water_level);
static bool MinimapPointInTriangle(double px, double py,
    double ax, double ay, double bx, double by, double cx, double cy);
static bool MinimapMarkerHasNumericCloneSuffix(const char* name, int dot_idx);
static bool MinimapMarkerNameIsSeparator(char c);
static bool MinimapMarkerIsGenericBuildingName(const char* name);
static int  SanitizeMinimapMarkerName(const char* src, char* out, int out_size);
int  BuildMinimapMarkerDisplayLabel(MinimapMarker* marker, char* out, int out_size);
int  FitMinimapMarkerLabel(const char* src, int available, char* out, int out_size);
static MinimapMarker* FindAutoShotProofMarker();

static void MinimapRasterizeMeshFace(float coords[9], uint8_t colors[12],
    float normals[9], uint8_t normal_source, uint32_t visual, void* cookie);

// ============================================================================
// DrawMiniText
// ============================================================================

void DrawMiniText(AnsiCell* ptr, int width, int height, int x, int y,
                  const char* text, uint8_t fg, uint8_t bk, int max_w)
{
    if (y < 0 || y >= height)
        return;
    if (x < 0 || x >= width)
        return;

    int limit = x + max_w;
    if (limit > width)
        limit = width;

    for (int i = 0; text[i] && x + i < limit; i++)
    {
        if (text[i] == '\n')
            break;
        AnsiCell* cell = ptr + y * width + (x + i);
        cell->fg = fg;
        cell->bk = bk;
        cell->gl = (uint8_t)text[i];
    }
}

// ============================================================================
// MinimapRgbToAnsiIndex
// ============================================================================

uint8_t MinimapRgbToAnsiIndex(int r, int g, int b)
{
    if (r < 0) r = 0; else if (r > 255) r = 255;
    if (g < 0) g = 0; else if (g > 255) g = 255;
    if (b < 0) b = 0; else if (b > 255) b = 255;
    return (uint8_t)(16 + 36 * ((r + 25) / 51) + 6 * ((g + 25) / 51) + ((b + 25) / 51));
}

// ============================================================================
// MinimapMaterialGlyph
// ============================================================================

static uint8_t MinimapMaterialGlyph(int mat_id)
{
    if (mat_id < 0 || mat_id >= 256)
        return '.';
    uint8_t gl = mat[mat_id].shade[0][8].gl;
    return (gl >= 32 && gl < 127) ? gl : (uint8_t)'.';
}

// ============================================================================
// MinimapApplyTerrainCell
// ============================================================================

void MinimapApplyTerrainCell(AnsiCell* cell, int mat_id, uint16_t h, float water_level)
{
    if (!cell)
        return;

    if ((float)h < water_level)
    {
        cell->bk = MinimapRgbToAnsiIndex(0, 25, 80);
        cell->fg = MinimapRgbToAnsiIndex(40, 100, 200);
        cell->gl = '~';
        return;
    }

    if (mat_id == 5)
    {
        cell->bk = MinimapRgbToAnsiIndex(80, 0, 0);
        cell->fg = MinimapRgbToAnsiIndex(255, 80, 80);
        cell->gl = '*';
        return;
    }

    if (mat_id < 0 || mat_id >= 256)
        mat_id = 1;

    const MatCell& shade = mat[mat_id].shade[0][8];
    int elev = ((int)h - (int)water_level) >> 13;
    if (elev < 0) elev = 0;
    if (elev > 3) elev = 3;

    int bg_r = shade.bg[0] + elev * 10;
    int bg_g = shade.bg[1] + elev * 10;
    int bg_b = shade.bg[2] + elev * 10;
    if (bg_r > 255) bg_r = 255;
    if (bg_g > 255) bg_g = 255;
    if (bg_b > 255) bg_b = 255;

    cell->bk = MinimapRgbToAnsiIndex(bg_r, bg_g, bg_b);
    cell->fg = MinimapRgbToAnsiIndex(shade.fg[0], shade.fg[1], shade.fg[2]);
    cell->gl = MinimapMaterialGlyph(mat_id);
}

// ============================================================================
// MinimapPointInTriangle
// ============================================================================

static bool MinimapPointInTriangle(double px, double py,
    double ax, double ay, double bx, double by, double cx, double cy)
{
    double d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
    double d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
    double d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
    bool has_neg = (d1 < 0.0) || (d2 < 0.0) || (d3 < 0.0);
    bool has_pos = (d1 > 0.0) || (d2 > 0.0) || (d3 > 0.0);
    return !(has_neg && has_pos);
}

// ============================================================================
// Minimap marker label helpers
// ============================================================================

static bool MinimapMarkerHasNumericCloneSuffix(const char* name, int dot_idx)
{
    if (!name || dot_idx < 0 || name[dot_idx] != '.')
        return false;
    int digits = 0;
    for (int i = dot_idx + 1; name[i]; i++)
    {
        if (name[i] < '0' || name[i] > '9')
            return false;
        digits++;
    }
    return digits == 3;
}

static bool MinimapMarkerNameIsSeparator(char c)
{
    return c == '_' || c == '-' || c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

static bool MinimapMarkerIsGenericBuildingName(const char* name)
{
    if (!name || !name[0])
        return false;

    int end = (int)strlen(name);
    for (int i = 0; name[i]; i++)
    {
        if (name[i] == '.' && MinimapMarkerHasNumericCloneSuffix(name, i))
        {
            end = i;
            break;
        }
    }

    static const char* prefix = "Building_";
    const int prefix_len = (int)strlen(prefix);
    if (end <= prefix_len || strncmp(name, prefix, prefix_len) != 0)
        return false;
    for (int i = prefix_len; i < end; i++)
    {
        if (name[i] < '0' || name[i] > '9')
            return false;
    }
    return true;
}

static int SanitizeMinimapMarkerName(const char* src, char* out, int out_size)
{
    if (!out || out_size <= 0)
        return 0;
    out[0] = 0;
    if (!src || !src[0])
        return 0;

    int end = (int)strlen(src);
    for (int i = 0; src[i]; i++)
    {
        if (src[i] == '.' && MinimapMarkerHasNumericCloneSuffix(src, i))
        {
            end = i;
            break;
        }
    }

    int written = 0;
    bool in_separator = false;
    for (int i = 0; i < end && written + 1 < out_size; i++)
    {
        char c = src[i];
        if (MinimapMarkerNameIsSeparator(c))
        {
            if (written > 0)
                in_separator = true;
            continue;
        }
        if (in_separator && written + 2 < out_size)
            out[written++] = ' ';
        out[written++] = c;
        in_separator = false;
    }
    out[written] = 0;
    return written;
}

int BuildMinimapMarkerDisplayLabel(MinimapMarker* marker, char* out, int out_size)
{
    if (!out || out_size <= 0)
        return 0;
    out[0] = 0;
    if (!marker)
        return 0;

    const char* name = GetMinimapMarkerName(marker);
    if (GetMinimapMarkerType(marker) == 1)
    {
        if (MinimapMarkerIsGenericBuildingName(name))
            return 0;
        const char* label = GetMinimapMarkerLabel(marker);
        if (label && label[0])
        {
            snprintf(out, out_size, "%s", label);
            return (int)strlen(out);
        }
        return SanitizeMinimapMarkerName(name, out, out_size);
    }

    const char* label = GetMinimapMarkerLabel(marker);
    if (label && label[0])
    {
        snprintf(out, out_size, "%s", label);
        return (int)strlen(out);
    }
    return SanitizeMinimapMarkerName(name, out, out_size);
}

int FitMinimapMarkerLabel(const char* src, int available, char* out, int out_size)
{
    if (!out || out_size <= 0)
        return 0;
    out[0] = 0;
    if (!src || !src[0] || available <= 0)
        return 0;

    int src_len = (int)strlen(src);
    if (src_len <= available)
    {
        snprintf(out, out_size, "%s", src);
        return (int)strlen(out);
    }

    int keep = available;
    if (available >= 4)
        keep = available - 3;
    if (keep < 0)
        keep = 0;

    int written = 0;
    for (int i = 0; i < keep && src[i] && written + 1 < out_size; i++)
        out[written++] = src[i];
    if (available >= 4)
    {
        for (int i = 0; i < 3 && written + 1 < out_size; i++)
            out[written++] = '.';
    }
    out[written] = 0;
    return written;
}

// ============================================================================
// FindAutoShotProofMarker
// ============================================================================

static MinimapMarker* FindAutoShotProofMarker()
{
    MinimapMarker* fallback = nullptr;
    for (MinimapMarker* marker = GetFirstMinimapMarker(); marker;
         marker = GetNextMinimapMarker(marker))
    {
        if (!fallback)
            fallback = marker;
        if (GetMinimapMarkerType(marker) != 1)
            continue;
        const char* name = GetMinimapMarkerName(marker);
        if (MinimapMarkerIsGenericBuildingName(name))
            continue;
        return marker;
    }
    return fallback;
}

// ============================================================================
// MinimapRasterizeMeshFace
// ============================================================================

static void MinimapRasterizeMeshFace(float coords[9], uint8_t colors[12],
    float normals[9], uint8_t normal_source, uint32_t visual, void* cookie)
{
    (void)colors;
    (void)normals;
	(void)normal_source;
    (void)visual;
    MinimapFootprintRaster* raster = (MinimapFootprintRaster*)cookie;
    if (!raster || !raster->cells)
        return;

    // Per-vertex PLAYER-CENTRED tangent offsets (world units). Under spherical
    // curvature each world vertex is mapped through the server-transported frame
    // (RenderS3MinimapProject, FL-4257) so the footprint bends with the same
    // geodesic frame as the terrain background; the chart-linear offset is the
    // fallback (Euclidean or no authoritative pose), keeping that path
    // byte-identical.
    double ex[3];
    double ey[3];
    for (int i = 0; i < 3; i++)
    {
        double lx = coords[i * 3 + 0];
        double ly = coords[i * 3 + 1];
        double lz = coords[i * 3 + 2];
        double wxi = raster->tm[0] * lx + raster->tm[4] * ly + raster->tm[8] * lz + raster->tm[12];
        double wyi = raster->tm[1] * lx + raster->tm[5] * ly + raster->tm[9] * lz + raster->tm[13];
        float em, nm;
        if (RenderS3MinimapProject(raster->renderer, raster->center_x, raster->center_y,
                                   (float)wxi, (float)wyi, &em, &nm))
        {
            ex[i] = em;
            ey[i] = nm;
        }
        else
        {
            ex[i] = wxi - raster->center_x;
            ey[i] = wyi - raster->center_y;
        }
    }

    int hw = raster->width / 2;
    int hh = raster->height / 2;

    double min_x = ex[0], max_x = ex[0];
    double min_y = ey[0], max_y = ey[0];
    for (int i = 1; i < 3; i++)
    {
        if (ex[i] < min_x) min_x = ex[i];
        if (ex[i] > max_x) max_x = ex[i];
        if (ey[i] < min_y) min_y = ey[i];
        if (ey[i] > max_y) max_y = ey[i];
    }

    int gx0 = (int)floor(min_x / raster->scale + hw);
    int gx1 = (int)floor(max_x / raster->scale + hw) + 1;
    int gy0 = (int)floor(min_y / raster->scale + hh);
    int gy1 = (int)floor(max_y / raster->scale + hh) + 1;
    if (gx0 < 0) gx0 = 0;
    if (gy0 < 0) gy0 = 0;
    if (gx1 >= raster->width) gx1 = raster->width - 1;
    if (gy1 >= raster->height) gy1 = raster->height - 1;

    const double half = raster->scale * 0.5;
    for (int gy = gy0; gy <= gy1; gy++)
    {
        for (int gx = gx0; gx <= gx1; gx++)
        {
            double px = (gx - hw) * raster->scale + half;
            double py = (gy - hh) * raster->scale + half;
            if (MinimapPointInTriangle(px, py, ex[0], ey[0], ex[1], ey[1], ex[2], ey[2]))
                raster->cells[gy * raster->width + gx] = true;
        }
    }
}

// ============================================================================
// MinimapRenderer::Render  (public)
// ============================================================================

void MinimapRenderer::Render(
    AnsiCell* ptr, int width, int height,
    float player_x, float player_y, float player_z,
    float player_dir,
    float yaw, float zoom,
    World* world,
    Terrain* terrain,
    float water_level,
    const RenderFrameInput* frame_input,
    RenderFrameReport* out_report,
    const Renderer* renderer)
{
    const int MAP_INFO_LINES = 2;
    const int MAP_W = 32;
    const int MAP_H = 16;
    const int MAP_X = width - MAP_W - 1;
    const int MAP_Y = 1 + MAP_INFO_LINES;
    // FL-1146/FL-3690: the SBU/SAC corrected spawn can be several hundred
    // world units from the nearest named building marker. The old 16-unit
    // scale only covered 512x256 world units, so the player spawned correctly
    // but the minimap still looked empty/wrong. Use a campus-scale inset and
    // keep mesh-footprint filtering proportional to the zoomed-out grid.
    const float SCALE = 48.0f;
    static const int FOOTPRINT_MIN_CELLS = 2;
    if (width < MAP_W + 10 || height < MAP_H + MAP_INFO_LINES + 6)
        return;

    g_minimap_marker_labels_suppressed = 0;

    // ── Info lines above minimap ──
    int info_y = MAP_Y - 1 - MAP_INFO_LINES;
    if (info_y >= 0)
    {
        char line1[64];
        char line2[64];
        snprintf(line1, sizeof(line1), "pos %.2f %.2f %.2f", player_x, player_y, player_z);
        snprintf(line2, sizeof(line2), "yaw %.1f dir %.1f zm %.2f", yaw, player_dir, zoom);
        DrawMiniText(ptr, width, height, MAP_X, info_y + 0, line1, 0x07, 0x00, MAP_W);
        DrawMiniText(ptr, width, height, MAP_X, info_y + 1, line2, 0x07, 0x00, MAP_W);
    }

    // ── Terrain background ──
    for (int y = 0; y < MAP_H; y++)
    {
        for (int x = 0; x < MAP_W; x++)
        {
            int sx = MAP_X + x;
            int sy = MAP_Y + y;
            if (sx < 0 || sx >= width || sy < 0 || sy >= height)
                continue;

            AnsiCell* cell = ptr + sy * width + sx;
            // FL-4257 — under spherical curvature, sample the terrain along
            // the great circle leaving the player (player-centred azimuthal-
            // equidistant) so forward motion reads straight; otherwise the
            // flat-chart offset, byte-identical to the Euclidean path.
            float east_m = (x - MAP_W / 2) * SCALE;
            float north_m = (y - MAP_H / 2) * SCALE;
            float wx, wy;
            if (!RenderS3MinimapSample(renderer, player_x, player_y,
                                       east_m, north_m, &wx, &wy))
            {
                wx = player_x + east_m;
                wy = player_y + north_m;
            }
            int px = (int)floor(wx / (HEIGHT_CELLS * 2));
            int py = (int)floor(wy / (HEIGHT_CELLS * 2));
            Patch* patch = terrain ? GetTerrainPatch(terrain, px, py) : nullptr;

            if (patch)
            {
                uint16_t* hmap = GetTerrainHeightMap(patch);
                uint16_t* vmap = GetTerrainVisualMap(patch);
                float lx = fmodf(wx, HEIGHT_CELLS * 2);
                float ly = fmodf(wy, HEIGHT_CELLS * 2);
                if (lx < 0) lx += HEIGHT_CELLS * 2;
                if (ly < 0) ly += HEIGHT_CELLS * 2;

                int hx = (int)(lx / 2) % (HEIGHT_CELLS + 1);
                int hy = (int)(ly / 2) % (HEIGHT_CELLS + 1);
                int vx = (int)(lx) % VISUAL_CELLS;
                int vy = (int)(ly) % VISUAL_CELLS;

                uint16_t h = hmap[hy * (HEIGHT_CELLS + 1) + hx];
                uint16_t v = vmap[vy * VISUAL_CELLS + vx];
                int mat_id = v & 0xFF;
                MinimapApplyTerrainCell(cell, mat_id, h, water_level);
            }
            else
            {
                cell->bk = 0x00;
                cell->fg = 0x08;
                cell->gl = ' ';
            }
        }
    }

    // ── Mesh geometry footprints ──
    // (World* passed explicitly via bridge; never read from global.)
    bool occupied[MAP_W * MAP_H] = {};
    if (world)
    {
        Inst** insts = nullptr;
        int inst_count = CollectMeshInsts(world, &insts);
        // FL-4257 — under the spherical transported-frame projection a building
        // whose chart bbox sits just outside the flat window can still bend onto
        // the minimap. Widen the chart-space pre-cull so RenderS3MinimapProject
        // gets the chance to place it (the per-vertex raster still clips to the
        // map cells). Euclidean keeps the exact flat window. The margin is a
        // conservative heuristic, not exhaustive antipodal coverage.
        double cull = RenderS3MinimapActive(renderer) ? 2.0 : 1.0;
        double view_min_x = player_x - cull * (MAP_W / 2.0) * SCALE;
        double view_max_x = player_x + cull * (MAP_W / 2.0) * SCALE;
        double view_min_y = player_y - cull * (MAP_H / 2.0) * SCALE;
        double view_max_y = player_y + cull * (MAP_H / 2.0) * SCALE;

        for (int i = 0; i < inst_count; i++)
        {
            Inst* inst = insts[i];
            if (!inst) continue;
            if (!(GetInstFlags(inst) & INST_VISIBLE)) continue;

            double bbox[6];
            GetInstBBox(inst, bbox);
            if (bbox[1] < view_min_x || bbox[0] > view_max_x ||
                bbox[3] < view_min_y || bbox[2] > view_max_y)
                continue;

            double tm[16];
            if (!GetInstTM(inst, tm)) continue;

            Mesh* mesh = GetInstMesh(inst);
            if (!mesh) continue;

            bool inst_cells[MAP_W * MAP_H] = {};
            MinimapFootprintRaster raster = {};
            memcpy(raster.tm, tm, sizeof(tm));
            raster.cells = inst_cells;
            raster.width = MAP_W;
            raster.height = MAP_H;
            raster.center_x = player_x;
            raster.center_y = player_y;
            raster.scale = SCALE;
            raster.renderer = renderer; // FL-4257 transported-frame footprint
            QueryMesh(mesh, MinimapRasterizeMeshFace, &raster);

            int hits = 0;
            for (int idx = 0; idx < MAP_W * MAP_H; idx++)
            {
                if (inst_cells[idx]) hits++;
            }
            if (hits < FOOTPRINT_MIN_CELLS) continue;

            for (int idx = 0; idx < MAP_W * MAP_H; idx++)
            {
                if (inst_cells[idx]) occupied[idx] = true;
            }
        }
        free(insts);
    }

    // ── Footprint rendering ──
    for (int y = 0; y < MAP_H; y++)
    {
        for (int x = 0; x < MAP_W; x++)
        {
            if (!occupied[y * MAP_W + x]) continue;
            bool top = (y > 0) && occupied[(y - 1) * MAP_W + x];
            bool bot = (y + 1 < MAP_H) && occupied[(y + 1) * MAP_W + x];
            bool lft = (x > 0) && occupied[y * MAP_W + (x - 1)];
            bool rgt = (x + 1 < MAP_W) && occupied[y * MAP_W + (x + 1)];
            bool v_border = !top || !bot;
            bool h_border = !lft || !rgt;

            AnsiCell* cell = ptr + (MAP_Y + y) * width + (MAP_X + x);
            if (!v_border && !h_border)
            {
                cell->bk = MinimapRgbToAnsiIndex(72, 45, 15);
                cell->fg = MinimapRgbToAnsiIndex(110, 70, 25);
                cell->gl = ' ';
                continue;
            }
            cell->bk = MinimapRgbToAnsiIndex(20, 20, 20);
            cell->fg = MinimapRgbToAnsiIndex(220, 220, 220);
            cell->gl = (v_border && h_border) ? '+' : (v_border ? '-' : '|');
        }
    }

    // MINIMAP-01: strict-adjacency label layout (deletes the FL-4814 drift
    // that stacked labels as a column in the right half). A label is drawn
    // only on its own marker row, immediately right of the glyph (mx+1),
    // falling back to immediately left (mx-len) when the right side is
    // clipped or taken. No row drift. Markers are served nearest-to-player
    // first with the station (priority 0) ahead of all; a label whose cells
    // would overlap an already drawn label or glyph is suppressed and only
    // its glyph stays.
    bool label_occupied[MAP_W * MAP_H] = {};
    bool icon_drawn[MAP_W * MAP_H] = {};
    const int MAP_CX = MAP_X + MAP_W / 2;
    const int MAP_CY = MAP_Y + MAP_H / 2;
    struct VisibleMarker { MinimapMarker* marker; int mx; int my; int dist_sq; };
    std::vector<VisibleMarker> visible_markers;
    auto marker_cell = [&](MinimapMarker* marker, int& mx, int& my) {
        float east, north;
        if (!RenderS3MinimapProject(renderer, player_x, player_y,
                GetMinimapMarkerX(marker), GetMinimapMarkerY(marker), &east, &north))
        {
            east = GetMinimapMarkerX(marker) - player_x;
            north = GetMinimapMarkerY(marker) - player_y;
        }
        mx = MAP_X + MAP_W / 2 + (int)(east / SCALE);
        my = MAP_Y + MAP_H / 2 + (int)(north / SCALE);
        return mx >= MAP_X && mx < MAP_X + MAP_W && my >= MAP_Y && my < MAP_Y + MAP_H;
    };
    for (MinimapMarker* marker = GetFirstMinimapMarker(); marker;
         marker = GetNextMinimapMarker(marker))
    {
        int mx, my;
        if (!marker_cell(marker, mx, my)) continue;
        int dx = mx - MAP_CX, dy = my - MAP_CY;
        visible_markers.push_back({marker, mx, my, dx * dx + dy * dy});
        label_occupied[(my-MAP_Y)*MAP_W + mx-MAP_X] = true;
    }
    // Reserve the player before labels; actor dots are drawn afterwards.
    for (int y = MAP_H/2-1; y <= MAP_H/2+1; ++y)
        for (int x = MAP_W/2-1; x <= MAP_W/2+1; ++x)
            label_occupied[y*MAP_W+x] = true;
    auto priority = [](MinimapMarker* marker) {
        const char* name = GetMinimapMarkerName(marker);
        return name && (!strcmp(name, "home_start") || !strcmp(name, "numabe_station")) ? 0 : 1;
    };
    std::stable_sort(visible_markers.begin(), visible_markers.end(),
        [&](const VisibleMarker& a, const VisibleMarker& b) {
            int pa = priority(a.marker), pb = priority(b.marker);
            if (pa != pb) return pa < pb;
            return a.dist_sq < b.dist_sq;
        });
    for (const VisibleMarker& vm : visible_markers)
    {
        MinimapMarker* marker = vm.marker;
        int mx = vm.mx, my = vm.my;
        if (mx < MAP_X || mx >= MAP_X + MAP_W || my < MAP_Y || my >= MAP_Y + MAP_H)
            continue;
        if (out_report)
            out_report->minimap.minimap_marker_visible_count++;

        const int icon_index = (my-MAP_Y)*MAP_W + mx-MAP_X;
        if (icon_drawn[icon_index]) continue;
        icon_drawn[icon_index] = true;
        AnsiCell* cell = ptr + my * width + mx;
        cell->fg = GetMinimapMarkerFg(marker);
        cell->bk = black;
        cell->gl = GetMinimapMarkerGlyph(marker);

        char label_raw[256];
        int raw_len = BuildMinimapMarkerDisplayLabel(marker, label_raw, sizeof(label_raw));
        // The 'P' category glyph already sits in the marker cell above; drop
        // the duplicated prefix from the text so "PPub" draws as "Pub". The
        // stored place labels are always 'P'-prefixed, so stripping exactly
        // one restores the name; other glyph classes are untouched.
        if (raw_len > 1 && label_raw[0] == 'P' && GetMinimapMarkerGlyph(marker) == 'P')
        {
            memmove(label_raw, label_raw + 1, (size_t)raw_len);
            raw_len -= 1;
        }
        if (raw_len <= 0) continue;

        // Right-half predicate keeps the meaning of
        // minimap_marker_right_half_visible_count: it counts right-half
        // markers, not placed labels, exactly as before.
        int right_space = MAP_X + MAP_W - (mx + 1);
        int left_space = mx - MAP_X;
        if (left_space > right_space && out_report)
            out_report->minimap.minimap_marker_right_half_visible_count++;

        char label_fit[256];
        int label_len = FitMinimapMarkerLabel(label_raw, MAP_W, label_fit, sizeof(label_fit));
        if (label_len <= 0) continue;
        int label_start_x = -1;
        bool draw_left = false;
        const int right_x = mx + 1;
        const int left_x = mx - label_len;
        if (right_x + label_len <= MAP_X + MAP_W)
        {
            bool free = true;
            for (int i = 0; i < label_len; ++i)
                if (label_occupied[(my-MAP_Y)*MAP_W + right_x + i - MAP_X]) { free = false; break; }
            if (free) label_start_x = right_x;
        }
        if (label_start_x < 0 && left_x >= MAP_X)
        {
            bool free = true;
            for (int i = 0; i < label_len; ++i)
                if (label_occupied[(my-MAP_Y)*MAP_W + left_x + i - MAP_X]) { free = false; break; }
            if (free) { label_start_x = left_x; draw_left = true; }
        }
        if (label_start_x < 0)
        {
            g_minimap_marker_labels_suppressed++;
            continue;
        }
        for (int i = 0; i < label_len; ++i)
            label_occupied[(my-MAP_Y)*MAP_W + label_start_x + i - MAP_X] = true;

        for (int i = 0; i < label_len; i++)
        {
            int lx = label_start_x + i;
            if (lx < MAP_X || lx >= MAP_X + MAP_W) continue;
            AnsiCell* lcell = ptr + my * width + lx;
            lcell->fg = GetMinimapMarkerFg(marker);
            lcell->bk = black;
            lcell->gl = (uint8_t)label_fit[i];
            if (out_report)
            {
                out_report->minimap.minimap_marker_label_chars_drawn++;
                if (draw_left)
                    out_report->minimap.minimap_marker_right_half_label_chars_drawn++;
            }
        }
    }

    // ── NPC/enemy dots ──
    if (frame_input)
    {
        for (int i = 0; i < frame_input->npc_dot_count; i++)
        {
            const MinimapNpcDot& npc = frame_input->npc_dots[i];
            // FL-4257 — transported-frame geodesic offset under spherical
            // curvature; chart-linear fallback otherwise.
            float dx, dy;
            if (!RenderS3MinimapProject(renderer, player_x, player_y,
                                        npc.pos[0], npc.pos[1], &dx, &dy))
            {
                dx = npc.pos[0] - player_x;
                dy = npc.pos[1] - player_y;
            }
            int mx = MAP_X + MAP_W / 2 + (int)(dx / SCALE);
            int my = MAP_Y + MAP_H / 2 + (int)(dy / SCALE);
            if (mx >= MAP_X && mx < MAP_X + MAP_W && my >= MAP_Y && my < MAP_Y + MAP_H)
            {
                AnsiCell* cell = ptr + my * width + mx;
                if (npc.is_enemy)
                {
                    cell->fg = 0xC4;
                    cell->gl = '*';
                }
                else if (npc.has_data)
                {
                    cell->fg = 0x2F;
                    cell->gl = 'o';
                }
            }
        }
    }

    // ── Remote player dots ──
    if (frame_input)
    {
        for (int i = 0; i < frame_input->remote_dot_count; i++)
        {
            const MinimapRemoteDot& remote = frame_input->remote_dots[i];
            if (!remote.alive)
                continue;

            // FL-4257 — transported-frame geodesic offset under spherical
            // curvature; chart-linear fallback otherwise.
            float dx, dy;
            if (!RenderS3MinimapProject(renderer, player_x, player_y,
                                        remote.pos[0], remote.pos[1], &dx, &dy))
            {
                dx = remote.pos[0] - player_x;
                dy = remote.pos[1] - player_y;
            }
            int mx = MAP_X + MAP_W / 2 + (int)(dx / SCALE);
            int my = MAP_Y + MAP_H / 2 + (int)(dy / SCALE);
            if (mx < MAP_X || mx >= MAP_X + MAP_W || my < MAP_Y || my >= MAP_Y + MAP_H)
                continue;
            if (out_report)
                out_report->minimap.minimap_remote_expected_count++;

            AnsiCell* cell = ptr + my * width + mx;
            cell->fg = 0x2F;
            cell->gl = 'o';
            if (out_report)
                out_report->minimap.minimap_remote_drawn_count++;
        }
    }

    // ── Player center marker ──
    {
        int px = MAP_X + MAP_W / 2;
        int py = MAP_Y + MAP_H / 2;
        if (px >= 0 && px < width && py >= 0 && py < height)
        {
            AnsiCell* cell = ptr + py * width + px;
            cell->fg = 0xFF;
            cell->bk = 0x00;
            cell->gl = '@';
        }

        float rad = player_dir * (float)(3.14159265358979323846 / 180.0);
        int dx = (int)(sin(rad) * 2.0f);
        int dy = (int)(-cos(rad) * 1.0f);
        int ax = px + dx;
        int ay = py + dy;
        if (ax >= MAP_X && ax < MAP_X + MAP_W && ay >= MAP_Y && ay < MAP_Y + MAP_H)
        {
            AnsiCell* arrow = ptr + ay * width + ax;
            arrow->fg = 0xFE;
            if (abs(dx) > abs(dy))
                arrow->gl = dx > 0 ? '>' : '<';
            else
                arrow->gl = dy > 0 ? 'v' : '^';
        }
    }

    // ── Border ──
    for (int x = MAP_X - 1; x <= MAP_X + MAP_W; x++)
    {
        if (x >= 0 && x < width)
        {
            if (MAP_Y - 1 >= 0)
            {
                AnsiCell* top = ptr + (MAP_Y - 1) * width + x;
                top->fg = 0x07;
                top->gl = '-';
            }
            if (MAP_Y + MAP_H < height)
            {
                AnsiCell* bot = ptr + (MAP_Y + MAP_H) * width + x;
                bot->fg = 0x07;
                bot->gl = '-';
            }
        }
    }
    for (int y = MAP_Y; y < MAP_Y + MAP_H; y++)
    {
        if (y >= 0 && y < height)
        {
            if (MAP_X - 1 >= 0)
            {
                AnsiCell* left = ptr + y * width + (MAP_X - 1);
                left->fg = 0x07;
                left->gl = '|';
            }
            if (MAP_X + MAP_W < width)
            {
                AnsiCell* right = ptr + y * width + (MAP_X + MAP_W);
                right->fg = 0x07;
                right->gl = '|';
            }
        }
    }
}

// ============================================================================
// MinimapRenderer::PrimeAutoShotProofCapture  (public)
// ============================================================================

bool MinimapRenderer::PrimeAutoShotProofCapture(
    const RenderFrameInput* frame_input,
    float local_display_pos[3],
    float* local_display_dir)
{
    static bool primed = false;
    if (primed || !frame_input || !frame_input->auto_shot_enabled)
        return false;
    MinimapMarker* marker = FindAutoShotProofMarker();
    if (!marker)
        return false;
    float marker_x = GetMinimapMarkerX(marker);
    float marker_y = GetMinimapMarkerY(marker);
    local_display_pos[0] = marker_x;
    local_display_pos[1] = marker_y;
    if (local_display_dir)
        *local_display_dir = 0.0f;
    printf("[auto-shot] snapped proof capture to marker name=%s label=%s x=%.1f y=%.1f\n",
        GetMinimapMarkerName(marker),
        GetMinimapMarkerLabel(marker),
        marker_x, marker_y);
    fflush(stdout);
    primed = true;
    return true;
}
