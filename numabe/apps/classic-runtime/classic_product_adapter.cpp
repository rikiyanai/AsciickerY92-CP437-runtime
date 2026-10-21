// apps/classic-runtime/classic_product_adapter.cpp
// Classic product owner: AsciickerProductInstallV1 + CP437 slot
// implementations.
//
// The Classic game is the shared renderer compiled with
// ASCIICKER_RENDERER_CLASSIC_CP437, driven through the same neutral ports
// as every product. The resolve slots below own the Classic CP437 cell
// mapping, moved delete-first out of the shared resolve TU: the CP437 copy
// for final cells lives here, fail-closed codes cover product-only routes,
// and measurement returns the null-equivalent contract values. No FL
// product behavior, no second renderer, no shared-code branch.

#include "../../engine/render/renderer_ports_v1.h"

#if !defined(ASCIICKER_RENDERER_CLASSIC_CP437)
#error "classic product adapter linked into a non-Classic build"
#endif

#include "../../engine/render/render.h" // AnsiCell, Material, MatCell (shared, neutral)
#include "../../platform/term.h" // TermCellInspectionV1 (shared, neutral)
#include "../../engine/render/render_internal.h" // Sample, Renderer, RenderSampleMaterialId, HEIGHT_SCALE (shared, neutral)
#include "../../engine/material_glyph_plane.h" // coverage display glyph (shared, neutral)

#include <string.h>
#include <algorithm> // std::max (silhouette dimming)

// Shared material table (engine/game_app.cpp), the Classic CP437 source.
// Declared here exactly as the shared scene TU declares it.
extern void* GetMaterialArr();
extern uint8_t auto_mat[];
namespace
{

uint64_t g_classic_present_frame = 0;

struct ClassicState
{
    // Live Classic product state. Non-null by contract so the shared
    // dispatch gates reach the Classic slots below; every Classic cell
    // paint lives in those slots.
    uint64_t frames;
};

AsciickerProductStateV1* ClassicCreate(void)
{
    ClassicState* st = new ClassicState();
    if (st)
        st->frames = 0;
    return (AsciickerProductStateV1*)st;
}

void ClassicDestroy(AsciickerProductStateV1* st)
{
    delete (ClassicState*)st;
}

void ClassicBeginFrame(AsciickerProductStateV1* st, Renderer* r,
    const AsciickerProductFrameV1* frame)
{
    ClassicState* cs = (ClassicState*)st;
    if (cs)
        cs->frames++;
    (void)r;
    (void)frame;
}

void ClassicResolvePrePass(AsciickerProductStateV1* st, Renderer* r, int w,
    int h)
{
    // Identity copy, scene-fact plane init, and observer-carrier copy are
    // FL4512-bridge-owned; Classic keeps no such planes.
    (void)st;
    (void)r;
    (void)w;
    (void)h;
}

// Byte-domain coverage display for one resolved Classic cell. Moved verbatim
// from the retired shared resolve arm: an admitted extended glyph paints its
// coverage-derived display byte, a zero-coverage cell paints the diagnostic.
static void ClassicApplyMaterialGlyphPlaneCell(Renderer* r, AnsiCell* cell,
    const Material* material, int elev, int shade)
{
    if (!material || !material->glyph_plane)
        return;
    GlyphId glyph_id = material_glyph_plane_lookup(material->glyph_plane, elev, shade);
    if (!glyph_id_is_extended(glyph_id))
        return;

    uint16_t coverage = material_glyph_plane_lookup_coverage(material->glyph_plane, elev, shade);
    r->material_glyph_report.extended_cells_seen++;
    r->material_glyph_report.last_glyph_id = glyph_id;
    r->material_glyph_report.last_coverage = coverage;
    if (coverage == 0)
    {
        cell->fg = 16;
        cell->bk = 16 + 36 * 5;
        cell->gl = '!';
        cell->spare = 0xFF;
        r->material_glyph_report.diagnostic_cells_rendered++;
        r->material_glyph_report.last_display_glyph = '!';
        return;
    }
    uint8_t display_glyph = material_glyph_plane_coverage_display_glyph(coverage);
    cell->gl = display_glyph;
    r->material_glyph_report.coverage_cells_rendered++;
    r->material_glyph_report.last_display_glyph = display_glyph;
}
// 2x2 sample vote accumulator owned by the Classic cell slots below. Moved
// verbatim from the retired shared resolve arm: four tiny votes about the
// final cell's material, light, and depth situation (top/bottom vs left/right
// split errors plus averaged rgb). One owner in this TU; both Classic cell
// slots finish from these votes.
struct ClassicCellVotes
{
    int bg[3];
    int fg[3];
    int err_h;
    int err_v;
    int bg_h[2][3];
    int bg_v[2][3];
};

static void ClassicAccumulateCellVotes(const Sample* src, int stride,
    const uint8_t diffuses[4], const Material* matlib, int elv,
    ClassicCellVotes* v)
{
    int spr[4] = { src[0].spare & 11, src[1].spare & 11, src[stride].spare & 11, src[stride + 1].spare & 11 };
    int mat[4] = {
        RenderSampleMaterialId(src[0]) >= 0 ? RenderSampleMaterialId(src[0]) : (src[0].visual & 0x00FF),
        RenderSampleMaterialId(src[1]) >= 0 ? RenderSampleMaterialId(src[1]) : (src[1].visual & 0x00FF),
        RenderSampleMaterialId(src[stride]) >= 0 ? RenderSampleMaterialId(src[stride]) : (src[stride].visual & 0x00FF),
        RenderSampleMaterialId(src[stride + 1]) >= 0 ? RenderSampleMaterialId(src[stride + 1]) : (src[stride + 1].visual & 0x00FF)
    };
    int vis[4] = { src[0].visual, src[1].visual, src[stride].visual, src[stride + 1].visual };

    v->bg[0] = 0; v->bg[1] = 0; v->bg[2] = 0;
    v->fg[0] = 0; v->fg[1] = 0; v->fg[2] = 0;
    v->err_h = 0;
    v->err_v = 0;
    v->bg_h[0][0] = 0; v->bg_h[0][1] = 0; v->bg_h[0][2] = 0;
    v->bg_h[1][0] = 0; v->bg_h[1][1] = 0; v->bg_h[1][2] = 0;
    v->bg_v[0][0] = 0; v->bg_v[0][1] = 0; v->bg_v[0][2] = 0;
    v->bg_v[1][0] = 0; v->bg_v[1][1] = 0; v->bg_v[1][2] = 0;

    for (int m = 0; m < 2; m++)
    {
        for (int i = 0; i < 4; i++)
        {
            //if (spr[i])
            {
                if (spr[i] & 0x8)
                {
                    int r = ((vis[i] & 0x1F) * 527 + 23) >> 6;
                    int g = (((vis[i] >> 5) & 0x1F) * 527 + 23) >> 6;
                    int b = (((vis[i] >> 10) & 0x1F) * 527 + 23) >> 6;

                    if ((spr[i] & 0x3) == 3)
                    {
                        r = r * diffuses[i] / 400;
                        g = g * diffuses[i] / 400;
                        b = b * diffuses[i] / 400;
                    }
                    else
                    {
                        r = r * diffuses[i] / 255;
                        g = g * diffuses[i] / 255;
                        b = b * diffuses[i] / 255;
                    }

                    if (i == 0 || i == 1)
                    {
                        if (m)
                        {
                            v->err_h += abs(v->bg_h[0][0] - 4 * r);
                            v->err_h += abs(v->bg_h[0][1] - 4 * g);
                            v->err_h += abs(v->bg_h[0][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_h[0][0] += 2 * r;
                            v->bg_h[0][1] += 2 * g;
                            v->bg_h[0][2] += 2 * b;
                        }
                    }
                    if (i == 2 || i == 3)
                    {
                        if (m)
                        {
                            v->err_h += abs(v->bg_h[1][0] - 4 * r);
                            v->err_h += abs(v->bg_h[1][1] - 4 * g);
                            v->err_h += abs(v->bg_h[1][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_h[1][0] += 2 * r;
                            v->bg_h[1][1] += 2 * g;
                            v->bg_h[1][2] += 2 * b;
                        }
                    }

                    if (i == 0 || i == 2)
                    {
                        if (m)
                        {
                            v->err_v += abs(v->bg_v[0][0] - 4 * r);
                            v->err_v += abs(v->bg_v[0][1] - 4 * g);
                            v->err_v += abs(v->bg_v[0][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_v[0][0] += 2 * r;
                            v->bg_v[0][1] += 2 * g;
                            v->bg_v[0][2] += 2 * b;
                        }
                    }
                    if (i == 1 || i == 3)
                    {
                        if (m)
                        {
                            v->err_v += abs(v->bg_v[1][0] - 4 * r);
                            v->err_v += abs(v->bg_v[1][1] - 4 * g);
                            v->err_v += abs(v->bg_v[1][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_v[1][0] += 2 * r;
                            v->bg_v[1][1] += 2 * g;
                            v->bg_v[1][2] += 2 * b;
                        }
                    }

                    if (!m)
                    {
                        v->bg[0] += r;
                        v->bg[1] += g;
                        v->bg[2] += b;
                    }
                }
                else
                {
                    int s = diffuses[i] / 17;
                    int r = matlib[mat[i]].shade[elv][s].bg[0];
                    int g = matlib[mat[i]].shade[elv][s].bg[1];
                    int b = matlib[mat[i]].shade[elv][s].bg[2];

                    if ((spr[i] & 0x3) == 3)
                    {
                        r = r * 255 / 400;
                        g = g * 255 / 400;
                        b = b * 255 / 400;
                    }

                    if (i == 0 || i == 1)
                    {
                        if (m)
                        {
                            v->err_h += abs(v->bg_h[0][0] - 4 * r);
                            v->err_h += abs(v->bg_h[0][1] - 4 * g);
                            v->err_h += abs(v->bg_h[0][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_h[0][0] += 2 * r;
                            v->bg_h[0][1] += 2 * g;
                            v->bg_h[0][2] += 2 * b;
                        }
                    }
                    if (i == 2 || i == 3)
                    {
                        if (m)
                        {
                            v->err_h += abs(v->bg_h[1][0] - 4 * r);
                            v->err_h += abs(v->bg_h[1][1] - 4 * g);
                            v->err_h += abs(v->bg_h[1][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_h[1][0] += 2 * r;
                            v->bg_h[1][1] += 2 * g;
                            v->bg_h[1][2] += 2 * b;
                        }
                    }

                    if (i == 0 || i == 2)
                    {
                        if (m)
                        {
                            v->err_v += abs(v->bg_v[0][0] - 4 * r);
                            v->err_v += abs(v->bg_v[0][1] - 4 * g);
                            v->err_v += abs(v->bg_v[0][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_v[0][0] += 2 * r;
                            v->bg_v[0][1] += 2 * g;
                            v->bg_v[0][2] += 2 * b;
                        }
                    }
                    if (i == 1 || i == 3)
                    {
                        if (m)
                        {
                            v->err_v += abs(v->bg_v[1][0] - 4 * r);
                            v->err_v += abs(v->bg_v[1][1] - 4 * g);
                            v->err_v += abs(v->bg_v[1][2] - 4 * b);
                        }
                        else
                        {
                            v->bg_v[1][0] += 2 * r;
                            v->bg_v[1][1] += 2 * g;
                            v->bg_v[1][2] += 2 * b;
                        }
                    }

                    if (!m)
                    {
                        v->bg[0] += r;
                        v->bg[1] += g;
                        v->bg[2] += b;

                        if ((spr[i] & 0x3) == 0x3)
                        {
                            v->fg[0] += matlib[mat[i]].shade[elv][s].fg[0] * 255 / 400;
                            v->fg[1] += matlib[mat[i]].shade[elv][s].fg[1] * 255 / 400;
                            v->fg[2] += matlib[mat[i]].shade[elv][s].fg[2] * 255 / 400;
                        }
                        else
                        {
                            v->fg[0] += matlib[mat[i]].shade[elv][s].fg[0];
                            v->fg[1] += matlib[mat[i]].shade[elv][s].fg[1];
                            v->fg[2] += matlib[mat[i]].shade[elv][s].fg[2];
                        }
                    }
                }
            }
        }
    }
}

static uint8_t ClassicResolveTerrainCell(AsciickerProductStateV1* st,
    Renderer* r, AnsiCell* cell, int cx, int cy, int w, int h, int mat_id,
    int has_material, uint8_t dispatch, int elv, int shd, const Sample* src,
    int stride, const uint8_t diffuses[4], int water_owned);

uint8_t ClassicResolveAutomapCell(AsciickerProductStateV1* st, Renderer* r,
    AnsiCell* cell, int cx, int cy, int w, int h, int mat_id, int has_material,
    uint8_t dispatch, int elv, int shd, const Sample* src, int stride,
    const uint8_t diffuses[4])
{
    // Live Classic automap finish. The retired shared sequence painted the
    // half-block arm and then unconditionally repainted the terrain
    // baseline over it for stateless cells, so the baseline IS the Classic
    // automap finish; the half-block paint never survived. Delegate to the
    // terrain slot below: one owner, identical pixels.
    return ClassicResolveTerrainCell(st, r, cell, cx, cy, w, h, mat_id,
        has_material, dispatch, elv, shd, src, stride, diffuses, 0);
}

uint8_t ClassicResolveTerrainCell(AsciickerProductStateV1* st, Renderer* r,
    AnsiCell* cell, int cx, int cy, int w, int h, int mat_id, int has_material,
    uint8_t dispatch, int elv, int shd, const Sample* src, int stride,
    const uint8_t diffuses[4], int water_owned)
{
    // Classic terrain-cell finish: the CP437 terrain baseline moved verbatim
    // from the retired shared else arm, then the coverage display below.
    // Reached whenever the installed Classic state selects this slot.
    // Reports applied when it paints.
    (void)st;
    (void)cx;
    (void)cy;
    (void)w;
    (void)h;
    (void)has_material;
    (void)dispatch;
    (void)water_owned;
    if (!r || !cell)
        return 0;
    if (mat_id < 0 || mat_id >= 256)
        return 0;
    Material* matlib = (Material*)GetMaterialArr();
    if (!matlib)
        return 0;
    if (!src || stride <= 0)
        return 0;
    ClassicCellVotes v;
    ClassicAccumulateCellVotes(src, stride, diffuses, matlib, elv, &v);
    int bk_rgb[3] =
    {
        (v.bg[0] + 102) / 204,
        (v.bg[1] + 102) / 204,
        (v.bg[2] + 102) / 204
    };

    // Final terrain-only write: convert averaged RGB into terminal palette
    // indices and store the chosen glyph into the visible AnsiCell.
    int gl = matlib[mat_id].shade[elv][shd].gl;
    cell->gl = (uint8_t)gl;
    cell->bk = (uint8_t)(16 + 36*bk_rgb[0] + bk_rgb[1] * 6 + bk_rgb[2]);
    cell->fg = (uint8_t)(16 + (36*((v.fg[0] + 102) / 204) + (((v.fg[1] + 102) / 204) * 6) + ((v.fg[2] + 102) / 204)));
    cell->spare = 0xFF;

    // FL-4260 RQ-153b: linecase and silhouette are baseline height-based
    // edge heuristics for cells with no live Material Look. FL-3955.
    {
        if (elv == 3) // only low elevation
        {
            int linecase = ((src[0].spare & 0x4) >> 2) | ((src[1].spare & 0x4) >> 1) | (src[stride].spare & 0x4) | ((src[stride + 1].spare & 0x4) << 1);
            static const int linecase_glyph[] = { 0, ',', ',', ',', '`', ';', ';', ';', '`', ';', ';', ';', '`', ';', ';', ';' };
            if (linecase)
                cell->gl = (uint8_t)linecase_glyph[linecase];
        }

        // FL-4260 RQ-153b: silhouette — CP437 path only. FL-3955.
        if (elv == 1 || elv == 3) // no elev change
        {
            // silhouette repetitoire:  _-/\| (should not be used by materials?)
            float z_hi = src[stride].height + src[stride + 1].height;
            float z_lo = src[0].height + src[1].height;
            float z_pr = src[-stride].height + src[1 - stride].height;

            float minus = z_lo - z_hi;
            float under = z_pr - z_lo;

            static const float thresh = 1 * HEIGHT_SCALE;

            if (minus > under)
            {
                if (minus > thresh)
                {
                    cell->gl = 0xC4; // '-'
                    bk_rgb[0] = std::max(0, bk_rgb[0] - 1);
                    bk_rgb[1] = std::max(0, bk_rgb[1] - 1);
                    bk_rgb[2] = std::max(0, bk_rgb[2] - 1);
                    cell->fg = (uint8_t)(16 + 36 * bk_rgb[0] + bk_rgb[1] * 6 + bk_rgb[2]);
                }
            }
            else
            {
                if (under > thresh)
                {
                    cell->gl = 0x5F; // '_'
                    bk_rgb[0] = std::max(0, bk_rgb[0] - 1);
                    bk_rgb[1] = std::max(0, bk_rgb[1] - 1);
                    bk_rgb[2] = std::max(0, bk_rgb[2] - 1);
                    cell->fg = (uint8_t)(16 + 36 * bk_rgb[0] + bk_rgb[1] * 6 + bk_rgb[2]);
                }
            }
        }
    }
    ClassicApplyMaterialGlyphPlaneCell(r, cell, &matlib[mat_id], elv, shd);
    return 1;
}

uint8_t ClassicResolveSingleSampleCell(AsciickerProductStateV1* st,
    Renderer* r, AnsiCell* cell, int x, int y, int w, int h, int visual)
{
    // Classic no-color fill moved verbatim from the retired shared
    // single-sample arm: straight from the material recipe, then the
    // coverage display.
    (void)st;
    (void)x;
    (void)y;
    (void)w;
    (void)h;
    if (!r || !cell)
        return 0;
    Material* matlib = (Material*)GetMaterialArr();
    if (!matlib)
        return 0;
    int mat = visual & 0x00FF;
    int shd = 0; // (src[0].visual >> 8) & 0x007F;
    int elv = 0; // (src[0].visual >> 15) & 0x0001;

    // fill from material
    const MatCell* mcell = &(matlib[mat].shade[1][shd]);
    const uint8_t* mbl = matlib[mat].shade[1][shd].bg;
    const uint8_t* mfg = matlib[mat].shade[1][shd].fg;

    cell->gl = mcell->gl;
    cell->bk = (uint8_t)(16 + (((mbl[0] + 25) / 51) * 36 + ((mbl[1] + 25) / 51) * 6 + ((mbl[2] + 25) / 51)));
    cell->fg = (uint8_t)(16 + (((mfg[0] + 25) / 51) * 36 + ((mfg[1] + 25) / 51) * 6 + ((mfg[2] + 25) / 51)));
    cell->spare = 0xFF;
    ClassicApplyMaterialGlyphPlaneCell(r, cell, &matlib[mat], elv, shd);
    return 1;
}

void ClassicSealWorldCell(AsciickerProductStateV1* st, int idx, uint8_t fg,
    uint8_t bk, uint8_t gl, uint8_t spare, uint8_t valid)
{
    // World-cell bridge seal is FL4512-owned; Classic keeps no bridge plane.
    (void)st;
    (void)idx;
    (void)fg;
    (void)bk;
    (void)gl;
    (void)spare;
    (void)valid;
}

void ClassicPublishFrameCarriers(AsciickerProductStateV1* st)
{
    // Frame carriers (sequence claims, counterfactual refs) are FL4512-owned;
    // Classic publishes none.
    (void)st;
}

void ClassicFinalizeProjectedFeatures(AsciickerProductStateV1* st,
    Renderer* r)
{
    // Projected-feature bundle finalization is FL4512-owned; Classic builds
    // no bundle.
    (void)st;
    (void)r;
}

void ClassicBuildPreGlyph(AsciickerProductStateV1* st, Renderer* r, int w,
    int h)
{
    // Pre-glyph scene build feeds the FL4512 resolver; Classic finishes its
    // cells in its automap slot instead.
    (void)st;
    (void)r;
    (void)w;
    (void)h;
}

void ClassicCloseWorldFrame(const AnsiCell* cells, int w, int h, int stride)
{
    // World-frame close writes the FL4512 bridge; Classic keeps no bridge.
    (void)cells;
    (void)w;
    (void)h;
    (void)stride;
}

void ClassicRegisterProjectedTriangle(AsciickerProductStateV1* st,
    Renderer* r, const float coords[9], const int pv[3][4],
    const float normal[3], uint32_t prim_id)
{
    // Projected-triangle registry is FL4512 product-state-owned; Classic
    // stores none.
    (void)st;
    (void)r;
    (void)coords;
    (void)pv;
    (void)normal;
    (void)prim_id;
}

void ClassicMeshSubmitBegin(AsciickerProductStateV1* st, Renderer* r,
    const Inst* inst, const Mesh* mesh, const double tm[16])
{
    // Submit bracketing and normal-matrix scratch are FL4512-owned; Classic
    // rasterizes without sidecars.
    (void)st;
    (void)r;
    (void)inst;
    (void)mesh;
    (void)tm;
}

void ClassicMeshSubmitEnd(AsciickerProductStateV1* st, Renderer* r,
    const Mesh* mesh)
{
    // Submit-end scratch teardown is FL4512-owned; Classic holds no scratch.
    (void)st;
    (void)r;
    (void)mesh;
}

void ClassicCaptureStrokeReprojection(AsciickerProductStateV1* st,
    Renderer* r)
{
    // Stroke reprojection capture feeds the FL4512 Active Stroke store;
    // Classic keeps none.
    (void)st;
    (void)r;
}

void ClassicMeshToneBeginFace(AsciickerProductStateV1* st,
    const Sample* sample_base, float ambient, uint8_t normal_source)
{
    // Mesh tone context is FL4512-owned; Classic shades without sidecars.
    (void)st;
    (void)sample_base;
    (void)ambient;
    (void)normal_source;
}

void ClassicMeshToneWriteSample(AsciickerProductStateV1* st,
    const Sample* sample_base, Sample* s, int16_t mu)
{
    // Mesh tone sidecar store is FL4512-owned; Classic writes no sidecar.
    (void)st;
    (void)sample_base;
    (void)s;
    (void)mu;
}

void ClassicMeshToneComputeVertex(AsciickerProductStateV1* st,
    const float normals[9], const float light[4], int s3_active,
    float mu_out[3], float tone_out[3])
{
    // Per-vertex tone scratch is FL4512-owned; Classic leaves the shared
    // zeroed result.
    (void)st;
    (void)normals;
    (void)light;
    (void)s3_active;
    (void)mu_out;
    (void)tone_out;
}

void ClassicBridgeUploadExtent(int w, int h, int* out_w, int* out_h)
{
    // Moved from the Classic arm of the present path: the Classic upload
    // covers the full viewport (the values flow into the commit call, which
    // ignores them, exactly as before).
    if (out_w)
        *out_w = w;
    if (out_h)
        *out_h = h;
}

void ClassicSpriteTrackComposition(Renderer* r, int x, int y, int w, int h,
    const AnsiCell* base, const AnsiCell* source, uint8_t fg_op,
    uint8_t bk_op, uint8_t fg_mask, uint8_t bk_mask)
{
    // Composition attribution recording is FL4512-owned; Classic composition
    // runs inline in shared code.
    (void)r;
    (void)x;
    (void)y;
    (void)w;
    (void)h;
    (void)base;
    (void)source;
    (void)fg_op;
    (void)bk_op;
    (void)fg_mask;
    (void)bk_mask;
}

void ClassicSpriteDispatchShape6(Renderer* r, AnsiCell* cell, int x, int y,
    int w, int h)
{
    // Shape6 resolver dispatch is FL4512-owned; Classic cells are finished
    // in its automap slot.
    (void)r;
    (void)cell;
    (void)x;
    (void)y;
    (void)w;
    (void)h;
}

uint8_t ClassicMeasureDispatchSurface(const Sample* src, int stride,
    uint8_t fallback)
{
    (void)src;
    (void)stride;
    return fallback;
}

int ClassicMeasureCellMaterialVote(const Sample* src, int stride)
{
    (void)src;
    (void)stride;
    return -1;
}

void ClassicCommitPresentationCells(AnsiCell* cells, GlyphId* final_glyphs,
    int w, int h)
{
    // Moved from the Classic arm of TermCommitPresentationCellsV1: the
    // Classic final plane is the CP437 source byte, copied directly.
    if (!cells || !final_glyphs)
        return;
    ++g_classic_present_frame;
    for (int index = 0; index < w * h; ++index)
        final_glyphs[index] = (GlyphId)cells[index].gl;
}

bool ClassicResolveAtlasPagePath(const char* name, char* out_path,
    int out_cap)
{
    (void)name;
    (void)out_path;
    (void)out_cap;
    return false;
}

void ClassicBridgeResetForFrame(int w, int h)
{
    // Bridge frame reset targets the FL4512 bridge; Classic keeps no bridge.
    (void)w;
    (void)h;
}


uint64_t ClassicBridgeFrameId(void)
{
    return g_classic_present_frame;
}
bool ClassicPresentsCompiledAtlas(void)
{
    return false;
}

void ClassicCommitWebFrame(AnsiCell* cells, int w, int h)
{
    // The browser path does not exist in the Classic product.
    (void)cells;
    (void)w;
    (void)h;
}
int ClassicInspectCell(const AnsiCell* cells, const GlyphId* final_glyphs,
    int w, int h, int x, int y, TermCellInspectionV1* out)
{
    (void)cells;
    (void)final_glyphs;
    (void)w;
    (void)h;
    (void)x;
    (void)y;
    // Moved from the Classic arm of the inspection fill: Unicode
    // scene-fact inspection has no owner in the Classic product.
    if (out)
        memset(out, 0, sizeof(*out));
    return -6;
}

int ClassicDumpBridgeCells(const char* path, int term_w, int term_h,
    AsciickerCellDepositV1 deposit, void* user)
{
    (void)path;
    (void)term_w;
    (void)term_h;
    (void)deposit;
    (void)user;
    return -2;
}

void ClassicEmitCellTrace(const AnsiCell* cells, const GlyphId* final_glyphs,
    int w, int h, bool armed, AsciickerCellDepositV1 deposit, void* user)
{
    // Cell-trace deposit flows through the FL4512 bridge; Classic emits none.
    (void)cells;
    (void)final_glyphs;
    (void)w;
    (void)h;
    (void)armed;
    (void)deposit;
    (void)user;
}

bool ClassicProfileReady(void)
{
    // Moved from the Classic arm of the capture gate: CP437 baseline
    // captures any frame.
    return true;
}

bool ClassicCaptureReady(bool require_active_stroke)
{
    (void)require_active_stroke;
    return ClassicProfileReady();
}
int ClassicReadSceneFactRows(AsciickerSceneFactRowV1* rows, int cap,
    uint8_t* plane_valid_out, uint16_t* plane_w_out,
    uint16_t* plane_h_out, uint16_t* valid_count_out,
    uint64_t* bridge_frame_out)
{
    if (plane_valid_out)
        *plane_valid_out = 0;
    if (plane_w_out)
        *plane_w_out = 0;
    if (plane_h_out)
        *plane_h_out = 0;
    if (valid_count_out)
        *valid_count_out = 0;
    if (bridge_frame_out)
        *bridge_frame_out = 0;
    (void)rows;
    (void)cap;
    return 0;
}

int ClassicDiagnosticsCensusJson(char* buf, int cap)
{
    if (buf && cap > 0)
        buf[0] = 0;
    return 0;
}

int ClassicStartupConfigureFromArgv(int argc, char** argv,
    const char* base_path)
{
    (void)argc;
    (void)argv;
    (void)base_path;
    return 0;
}

void ClassicEnsureRuntimeInstalled(void)
{
    // Classic carries no installable runtime; activation is a no-op.
}

static const RendererProductV1 kClassicRenderer = {
    1,
    &ClassicCreate,
    &ClassicDestroy,
    &ClassicBeginFrame,
    &ClassicResolvePrePass,
    &ClassicResolveAutomapCell,
    &ClassicResolveTerrainCell,
    &ClassicSealWorldCell,
    &ClassicPublishFrameCarriers,
    &ClassicFinalizeProjectedFeatures,
    &ClassicBuildPreGlyph,
    &ClassicCloseWorldFrame,
    &ClassicRegisterProjectedTriangle,
    &ClassicMeshSubmitBegin,
    &ClassicMeshSubmitEnd,
    &ClassicCaptureStrokeReprojection,
    &ClassicMeshToneBeginFace,
    &ClassicMeshToneWriteSample,
    &ClassicMeshToneComputeVertex,
    &ClassicSpriteTrackComposition,
    &ClassicSpriteDispatchShape6,
    &ClassicMeasureDispatchSurface,
    &ClassicMeasureCellMaterialVote,
    &ClassicResolveSingleSampleCell,
};

static const PresentationBackendV1 kClassicPresentation = {
    1,
    &ClassicCommitPresentationCells,
    &ClassicResolveAtlasPagePath,
    &ClassicBridgeResetForFrame,
    &ClassicBridgeUploadExtent,
    &ClassicBridgeFrameId,
    &ClassicPresentsCompiledAtlas,
    &ClassicCommitWebFrame,
};

static const InspectionPortV1 kClassicInspection = {
    1,
    &ClassicInspectCell,
    &ClassicDumpBridgeCells,
    &ClassicEmitCellTrace,
    &ClassicProfileReady,
    &ClassicCaptureReady,
    &ClassicReadSceneFactRows,
    &ClassicDiagnosticsCensusJson,
};


static const ProductStartupV1 kClassicStartup = {
    1,
    &ClassicStartupConfigureFromArgv,
    &ClassicEnsureRuntimeInstalled,
    NULL, // This product supplies no application tool.
};

} // namespace

void AsciickerProductInstallV1(void)
{
    static const AsciickerProductPortsV1 kPorts = {
        1,
        &kClassicRenderer,
        &kClassicPresentation,
        &kClassicInspection,
        &kClassicStartup,
        NULL, // FL-4512: Classic has no authoring capability or stub owner.
    };
    AsciickerProductSetPortsV1(&kPorts);
}
