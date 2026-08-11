// minimap_renderer.cpp — In-game minimap rendering
//
// Extracted from engine/game_render_bridge.cpp (RenderMinimap) and
// engine/game.cpp (helper functions).
// SEE ALSO: minimap_renderer.h, game.h, world.h

#include "minimap_renderer.h"
#include "render_frame_input.h"
#include "render_frame_report.h"
#include "render.h"
#include "world.h"
#include "terrain.h"
#include "talkbox.h"

#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

// Global material array (defined in game_app.cpp).
extern Material mat[256];

struct MinimapFootprintRaster
{
    double tm[16];
    bool* cells;
    int width;
    int height;
    float center_x;
    float center_y;
    float scale;
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
    uint32_t visual, void* cookie);

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
    uint32_t visual, void* cookie)
{
    (void)colors;
    (void)visual;
    MinimapFootprintRaster* raster = (MinimapFootprintRaster*)cookie;
    if (!raster || !raster->cells)
        return;

    double wx[3];
    double wy[3];
    for (int i = 0; i < 3; i++)
    {
        double lx = coords[i * 3 + 0];
        double ly = coords[i * 3 + 1];
        double lz = coords[i * 3 + 2];
        wx[i] = raster->tm[0] * lx + raster->tm[4] * ly + raster->tm[8] * lz + raster->tm[12];
        wy[i] = raster->tm[1] * lx + raster->tm[5] * ly + raster->tm[9] * lz + raster->tm[13];
    }

    int hw = raster->width / 2;
    int hh = raster->height / 2;

    double min_x = wx[0], max_x = wx[0];
    double min_y = wy[0], max_y = wy[0];
    for (int i = 1; i < 3; i++)
    {
        if (wx[i] < min_x) min_x = wx[i];
        if (wx[i] > max_x) max_x = wx[i];
        if (wy[i] < min_y) min_y = wy[i];
        if (wy[i] > max_y) max_y = wy[i];
    }

    int gx0 = (int)floor((min_x - raster->center_x) / raster->scale + hw);
    int gx1 = (int)floor((max_x - raster->center_x) / raster->scale + hw) + 1;
    int gy0 = (int)floor((min_y - raster->center_y) / raster->scale + hh);
    int gy1 = (int)floor((max_y - raster->center_y) / raster->scale + hh) + 1;
    if (gx0 < 0) gx0 = 0;
    if (gy0 < 0) gy0 = 0;
    if (gx1 >= raster->width) gx1 = raster->width - 1;
    if (gy1 >= raster->height) gy1 = raster->height - 1;

    const double half = raster->scale * 0.5;
    for (int gy = gy0; gy <= gy1; gy++)
    {
        for (int gx = gx0; gx <= gx1; gx++)
        {
            double px = raster->center_x + (gx - hw) * raster->scale + half;
            double py = raster->center_y + (gy - hh) * raster->scale + half;
            if (MinimapPointInTriangle(px, py, wx[0], wy[0], wx[1], wy[1], wx[2], wy[2]))
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
    RenderFrameReport* out_report)
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
            float wx = player_x + (x - MAP_W / 2) * SCALE;
            float wy = player_y + (y - MAP_H / 2) * SCALE;
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
        double view_min_x = player_x - (MAP_W / 2.0) * SCALE;
        double view_max_x = player_x + (MAP_W / 2.0) * SCALE;
        double view_min_y = player_y - (MAP_H / 2.0) * SCALE;
        double view_max_y = player_y + (MAP_H / 2.0) * SCALE;

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

    // ── Minimap markers ──
    for (MinimapMarker* marker = GetFirstMinimapMarker(); marker;
         marker = GetNextMinimapMarker(marker))
    {
        int mx = MAP_X + MAP_W / 2 + (int)((GetMinimapMarkerX(marker) - player_x) / SCALE);
        int my = MAP_Y + MAP_H / 2 + (int)((GetMinimapMarkerY(marker) - player_y) / SCALE);
        if (mx < MAP_X || mx >= MAP_X + MAP_W || my < MAP_Y || my >= MAP_Y + MAP_H)
            continue;
        if (out_report)
            out_report->minimap.minimap_marker_visible_count++;

        AnsiCell* cell = ptr + my * width + mx;
        cell->fg = GetMinimapMarkerFg(marker);
        cell->bk = black;
        cell->gl = GetMinimapMarkerGlyph(marker);

        char label_raw[256];
        int raw_len = BuildMinimapMarkerDisplayLabel(marker, label_raw, sizeof(label_raw));
        if (raw_len <= 0) continue;

        int right_space = MAP_X + MAP_W - (mx + 1);
        int left_space = mx - MAP_X;
        bool draw_left = left_space > right_space;
        if (draw_left && out_report)
            out_report->minimap.minimap_marker_right_half_visible_count++;

        int label_space = draw_left ? left_space : right_space;
        char label_fit[256];
        int label_len = FitMinimapMarkerLabel(label_raw, label_space, label_fit, sizeof(label_fit));
        int label_start_x = draw_left ? (mx - label_len) : (mx + 1);
        int label_y = my;
        if (label_len <= 0)
        {
            label_space = MAP_W;
            label_len = FitMinimapMarkerLabel(label_raw, label_space, label_fit, sizeof(label_fit));
            if (label_len <= 0) continue;
            label_start_x = MAP_X + (MAP_W - label_len) / 2;
            label_y = (my + 1 < MAP_Y + MAP_H) ? my + 1 : my - 1;
            if (label_y < MAP_Y || label_y >= MAP_Y + MAP_H)
                continue;
        }

        for (int i = 0; i < label_len; i++)
        {
            int lx = label_start_x + i;
            if (lx < MAP_X || lx >= MAP_X + MAP_W) continue;
            AnsiCell* lcell = ptr + label_y * width + lx;
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
            float dx = npc.pos[0] - player_x;
            float dy = npc.pos[1] - player_y;
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

            float dx = remote.pos[0] - player_x;
            float dy = remote.pos[1] - player_y;
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
