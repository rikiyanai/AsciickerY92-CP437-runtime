#!/usr/bin/env python3
# =============================================================================
# fl4547_dominant_connector_diag.py
# =============================================================================
# WHAT THIS IS
# ------------
# A READ-ONLY diagnostic for the FL-4547 / FL-4561 walking-figure sprite lane.
# It measures, for the current best automatic result (increment 4, the
# "Stone Story" soft-stroke walk), TWO things the operator's two-level
# snake/glyph definition needs but the solver does not yet expose:
#
#   1. DEF-1  "dominant stroke identity" of every selected limb glyph, defined
#             formally as the anisotropy of its inked pixels:
#                 D = (lambda_major - lambda_minor) / (lambda_major + lambda_minor)
#             where lambda_* are the eigenvalues of the 2x2 covariance matrix of
#             the glyph's lit 8x8 pixels (PCA of the ink).  D is in [0, 1]:
#                 D -> 1  a single strongly-oriented stroke  (e.g. "/", "|", "-")
#                 D -> 0  a round / isotropic blob           (e.g. "o", "O", ".")
#             This is the SAME PCA the existing hemisphere-mirror `strokeness()`
#             already runs; that function collapses it into a blob-penalty scalar.
#             Here we keep it as (dominance D, tangent angle) so it can gate a
#             connector decision instead of only rejecting blobs.
#
#   2. CONNECTOR HEADROOM  per direction: how much a SOFT continuity connector
#             (the operator's E_join = lambda_p*dist + lambda_t*(1-|t_a.t_b|)
#             + lambda_tau*topologyMismatch) could tighten the walk, estimated as
#             the mean tangent discontinuity between CONSECUTIVE cells of the same
#             persistent limb track.  A big discontinuity inside one limb = a kink
#             a connector could straighten; a small one = little to gain.
#
# WHY THIS MATTERS FOR "the peak"
# -------------------------------
# The operator asked, per the 8 compiled directions, "how much and how should
# this improve the peak, if any?".  The peak comparison surface is the protected
# hand-authored reference T144107.  A connector/dominance refinement can only help
# where a limb actually spans MULTIPLE cells as a coherent oriented stroke (long,
# high-D runs with real kinks).  Where limbs are short/foreshortened (a figure
# walking toward or away from camera), there is little multi-cell stroke to join,
# so the honest expected gain is near zero.  This tool quantifies that per
# direction so the peak claim is evidence-backed, not hand-waved.
#
# IMPORTANT — WHAT THIS DELIBERATELY DOES NOT DO
# ----------------------------------------------
# Per the operator's correction: the "two-pixel connector band" is an EXPANDED
# ENDPOINT-SEARCH TOLERANCE for a formally dominant stroke.  It DOES NOT thicken
# the displayed raster and it is NOT a heavier glyph.  This diagnostic therefore
# never rewrites, thickens, or re-weights any glyph.  It only MEASURES dominance
# and connector headroom on the existing, accepted result.  Hard exact pixel
# ports are excluded on purpose: FL-4561 proved they produce connected Unicode
# "soup".
#
# HOW TO RUN
# ----------
#   .scratch/gradscii-art/.venv/bin/python scripts/fl4547_dominant_connector_diag.py
#
# It reads the increment-4 manifest, prints a per-direction table, and writes a
# JSON receipt + a per-direction ASCII overlay (dominant limb glyphs marked "*")
# under docs/research/ascii/verification/fl4547/dominance/.
# =============================================================================

import json                       # read the increment-4 manifest, write the receipt
import math                       # atan2/degrees for the tangent angle
import pathlib                    # locate repo-relative files portably
import sys                        # add ./scripts to the import path for rig/hm

import numpy as np                # eigen-decomposition of the 2x2 pixel covariance

# The FL-4547 solver modules live in ./scripts; import them so we can build the
# SAME glyph catalog the compiler used (scalar codepoint <-> 8x8 pixel mask).
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import fl4547_skeleton_walk as rig            # owns load_descriptor_catalog()
import fl4547_hemisphere_mirror_walk as hm    # owns glyph_angle() for cross-check

# The current best automatic result: increment 4 (Stone Story codebook + AA).
MANIFEST = (REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
            / "v20260718T092431__stonestory-5x9__walk_all_directions.frames.json")

# Where the receipt + overlays go (a dedicated, dated, tracked evidence folder).
OUT_DIR = REPO / "docs/research/ascii/verification/fl4547/dominance"

# ---- DEF-1 classification thresholds (diagnostic-only, clearly tunable) ------
# A glyph is a "dominant line" when its ink is strongly oriented; a "corner" when
# it is moderately oriented (two limbs meeting); "round" when nearly isotropic.
TAU_LINE = 0.60      # D >= 0.60  -> DOMINANT_LINE  (eligible for the 2px band)
TAU_CORNER = 0.25    # 0.25 <= D < 0.60 -> CORNER;  D < 0.25 -> ROUND/DOT


def structure_tensor_dominance(mask: int) -> tuple[float, float, int]:
    """DEF-1: measure how strongly a single 8x8 glyph mask reads as one stroke.

    Parameters
    ----------
    mask : int
        A 64-bit integer.  Bit (8*y + x) is set when sub-pixel (x, y) is inked,
        with y = 0 the TOP row and x = 0 the LEFT column — the exact convention
        used throughout the FL-4547 pipeline (see hemisphere `strokeness`).

    Returns
    -------
    (dominance, tangent_deg, pixel_count)
        dominance   : float in [0, 1].  1 = a perfectly straight stroke, 0 = a
                      round blob.  Computed as (Lmaj - Lmin)/(Lmaj + Lmin) from
                      the eigenvalues of the lit-pixel covariance (PCA of ink).
        tangent_deg : float in [0, 180).  The orientation of the ink's LONG axis
                      (the major eigenvector), i.e. the direction the stroke runs.
        pixel_count : int.  How many of the 64 sub-pixels are lit (used to tell a
                      thin oriented stroke apart from an oriented-but-heavy shape).
    """
    # Collect the (x, y) coordinates of every lit sub-pixel, same bit test the
    # rest of the pipeline uses so our geometry matches the solver's exactly.
    pts = [(x, y) for y in range(8) for x in range(8) if mask & (1 << (8 * y + x))]
    n = len(pts)
    # Fewer than two pixels cannot define an axis: treat as a non-dominant dot.
    if n < 2:
        return 0.0, 0.0, n

    # Centre the coordinates on their mean so the covariance describes SHAPE, not
    # where the glyph sits in the cell.
    arr = np.asarray(pts, dtype=float)
    arr -= arr.mean(axis=0)

    # 2x2 scatter matrix (un-normalised covariance) of the ink positions.  Its
    # eigenvectors are the ink's principal axes; its eigenvalues are how far the
    # ink spreads along each axis.
    scatter = arr.T @ arr
    evals, evecs = np.linalg.eigh(scatter)   # ascending: evals[0]=minor, [1]=major
    lam_min, lam_maj = float(evals[0]), float(evals[1])

    # Anisotropy / dominance.  Guard the degenerate all-collinear case (lam_min~0)
    # and the all-zero case.
    total = lam_maj + lam_min
    dominance = (lam_maj - lam_min) / total if total > 1e-9 else 0.0

    # The stroke's tangent is the MAJOR eigenvector's direction (the long axis).
    major_vec = evecs[:, 1]                       # column 1 corresponds to lam_maj
    tangent_deg = math.degrees(math.atan2(major_vec[1], major_vec[0])) % 180.0

    return dominance, tangent_deg, n


def classify(dominance: float) -> str:
    """Bin a dominance value into the operator's stroke-identity vocabulary."""
    if dominance >= TAU_LINE:
        return "DOMINANT_LINE"     # eligible for the 2px endpoint-tolerance band
    if dominance >= TAU_CORNER:
        return "CORNER"
    return "ROUND"


def folded_angle_gap(a: float, b: float) -> float:
    """Smallest unsigned gap between two undirected angles in [0, 180).

    Stroke tangents are undirected (a "/" pointing up-right is the same line as
    one pointing down-left), so 170 deg and 10 deg differ by only 20, not 160.
    """
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    grid_w, grid_h = manifest["grid"]                  # 5 wide, 9 tall

    # Build the SAME catalog the compiler used, then invert it to a fast
    # codepoint -> 8x8 mask lookup so we can score any selected glyph.
    catalog = rig.load_descriptor_catalog()
    scalar_to_mask: dict[int, int] = {}
    for index in range(len(catalog["scalars"])):
        scalar_to_mask[int(catalog["scalars"][index])] = int(catalog["masks"][index])

    # rig.DISPLAY_DIRS is the canonical 8-direction order; fall back to manifest.
    directions = getattr(rig, "DISPLAY_DIRS", None) or list(manifest["tracks"].keys())

    per_direction = {}
    overlays: dict[str, list[str]] = {}

    for direction in directions:
        tracks = manifest["tracks"][direction]

        # --- DEF-1 pass: dominance of every limb glyph across the whole cycle ---
        line_count = corner_count = round_count = 0
        dominance_values: list[float] = []
        # --- Connector-headroom pass: tangent kink between CONSECUTIVE cells of
        #     the SAME limb (only counts real multi-cell moves, not restated cells).
        kink_gaps: list[float] = []
        longest_dominant_run = 0

        for track_id, tk in tracks.items():
            scalars = tk["glyph_scalars"]
            cells = tk["cells"]
            active = tk["active"]
            run = 0                                   # current dominant-line run length
            prev_tangent = None
            prev_cell = None
            for fi, scalar in enumerate(scalars):
                if not active[fi]:                    # occluded frame: no glyph here
                    run = 0
                    prev_tangent = None
                    prev_cell = None
                    continue
                mask = scalar_to_mask.get(int(scalar))
                if mask is None:                      # space / unmapped -> skip
                    run = 0
                    prev_tangent = None
                    prev_cell = None
                    continue
                dominance, tangent, _n = structure_tensor_dominance(mask)
                dominance_values.append(dominance)
                kind = classify(dominance)
                if kind == "DOMINANT_LINE":
                    line_count += 1
                    run += 1
                    longest_dominant_run = max(longest_dominant_run, run)
                elif kind == "CORNER":
                    corner_count += 1
                    run = 0
                else:
                    round_count += 1
                    run = 0

                # Connector headroom: compare this cell's stroke tangent to the
                # previous cell's, but only when the limb actually MOVED to a new
                # cell (a restated same-cell glyph is not a join to straighten).
                cell = tuple(cells[fi])
                if prev_tangent is not None and prev_cell is not None and cell != prev_cell:
                    kink_gaps.append(folded_angle_gap(tangent, prev_tangent))
                prev_tangent = tangent
                prev_cell = cell

        total_glyphs = line_count + corner_count + round_count
        per_direction[direction] = {
            "dominant_line_glyphs": line_count,
            "corner_glyphs": corner_count,
            "round_glyphs": round_count,
            "total_limb_glyphs": total_glyphs,
            "dominant_fraction": round(line_count / total_glyphs, 4) if total_glyphs else 0.0,
            "mean_dominance": round(float(np.mean(dominance_values)), 4) if dominance_values else 0.0,
            "longest_dominant_run": longest_dominant_run,
            # The headroom number the peak answer hinges on: bigger = more kink a
            # soft connector could straighten; near-zero = little to gain here.
            "mean_consecutive_kink_deg": round(float(np.mean(kink_gaps)), 2) if kink_gaps else 0.0,
            "consecutive_moves_measured": len(kink_gaps),
        }

        # --- Human-readable overlay: frame 0 rows with dominant limb cells marked.
        # We mark a cell "*" when its glyph is a DOMINANT_LINE, so the operator can
        # see at a glance which strokes would be eligible for the 2px band.
        frame0 = manifest["frames"][direction][0]["rows"]
        marked_rows = []
        for row in frame0:
            marked = []
            for ch in row:
                if ch == " ":
                    marked.append(" ")
                    continue
                mask = scalar_to_mask.get(ord(ch))
                if mask is None:
                    marked.append(ch)
                    continue
                dominance, _t, _n = structure_tensor_dominance(mask)
                marked.append("*" if classify(dominance) == "DOMINANT_LINE" else ch)
            marked_rows.append("".join(marked))
        overlays[direction] = marked_rows

    # ---- Emit a checkable JSON receipt -------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": "fl4547.dominant_connector_diag.v1",
        "read_only": True,
        "does_not_thicken_raster": True,
        "source_manifest": str(MANIFEST.relative_to(REPO)),
        "source_increment": manifest.get("increment"),
        "grid": [grid_w, grid_h],
        "def1_thresholds": {"TAU_LINE": TAU_LINE, "TAU_CORNER": TAU_CORNER},
        "per_direction": per_direction,
    }
    receipt_path = OUT_DIR / "dominant_connector_diag.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))

    # ---- Print a compact table + overlays for the operator -----------------
    print(f"source: {MANIFEST.name}  (increment {manifest.get('increment')})")
    print(f"DEF-1 thresholds: DOMINANT_LINE D>={TAU_LINE}, CORNER D>={TAU_CORNER}\n")
    header = ("dir", "domLine", "corner", "round", "dom%", "meanD", "longRun", "kink°", "#moves")
    print("{:>4} {:>7} {:>6} {:>5} {:>6} {:>6} {:>7} {:>6} {:>6}".format(*header))
    for direction in directions:
        d = per_direction[direction]
        print("{:>4} {:>7} {:>6} {:>5} {:>6} {:>6} {:>7} {:>6} {:>6}".format(
            direction, d["dominant_line_glyphs"], d["corner_glyphs"], d["round_glyphs"],
            f'{d["dominant_fraction"]*100:.0f}%', d["mean_dominance"],
            d["longest_dominant_run"], d["mean_consecutive_kink_deg"],
            d["consecutive_moves_measured"]))

    print("\nframe-0 overlay ('*' = DOMINANT_LINE glyph, 2px-band eligible):")
    for direction in directions:
        print(f"  [{direction}]")
        for row in overlays[direction]:
            print(f"    |{row}|")

    print(f"\nreceipt: {receipt_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
