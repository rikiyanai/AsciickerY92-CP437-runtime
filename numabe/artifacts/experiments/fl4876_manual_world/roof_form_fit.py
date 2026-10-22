"""roof_form_fit.py IN_DIR OUT_DIR -- measurement (FL-4876 roof shapes): fits flat / shed / gable / hip roof models to the
raw TMG LiDAR roof heights of every per-building roof field in IN_DIR (compile_lidar_roof_field.py --all output) and
writes OUT_DIR/<source>.roof_field.json copies whose lattice carries the fitted plane heights for pitched roofs, plus
OUT_DIR/roof_forms.json (one row per building with every model's RMS, so each choice is auditable).

Why: every Numabe building was baked as a flat-topped box (operator 2026-10-13: "why does 17-16 ... look so flat").
The bake already lowers a roof field's per-vertex roof_m into the terrain heightfield; but compile_lidar_roof_field
snaps vertices to height tiers, which turns a gable into a terrace. Here a pitched building's vertices get the clean
model height (no noise, no terrace); a flat building keeps its tier field untouched.

Models in the footprint frame (a = along the long edge, b = across, metres, centred on the footprint centroid):
  flat   z = c
  shed   z = c + k*b                       (both axes)
  gable  z = r - k*|b - b0|                (ridge along either axis; b0 searched +-1.5 m)
  hip    z = r - k*max(|b - b0|, |a - a0| - d), d = half-length minus half-width
Choice rule (stated, not tuned per building): pitched only if the roof spread p90-p10 >= 0.5 m, the best pitched model's
RMS <= 0.6 x the flat RMS, and its pitch is 10-50 deg; otherwise flat. Ties within 5% RMS prefer the simpler model."""
import json, math, sys
from pathlib import Path
import numpy as np

src_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
# hand roof decisions (AUTHORED-ROOF-01): building feature id -> appearance record
AUTHORED = json.loads((Path(__file__).resolve().parents[3] / "assets/source_bundles/"
                       "numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json")
                      .read_text())["buildings"]


def trimmed_rms(res):
    """RMS of the best-fitting 80% of vertices: porch roofs, balconies, trees and rooftop equipment inside a
    footprint are not the main roof planes and must not decide the roof form (17-16: gable rejected at full RMS)."""
    r = np.sort(np.abs(res)); r = r[: max(1, int(len(r) * 0.8))]
    return float(np.sqrt(np.mean(r ** 2)))


def fit_lin(cols, z):
    # two-pass: least squares, then refit on the best 80% (same trimming as the score)
    A = np.column_stack(cols + [np.ones_like(z)])
    coef, *_ = np.linalg.lstsq(A, z, rcond=None)
    res = A @ coef - z
    keep = np.abs(res) <= np.percentile(np.abs(res), 80)
    if keep.sum() >= len(cols) + 2:
        coef, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
    pred = A @ coef
    return coef, trimmed_rms(pred - z), pred


rows = []
for path in sorted(src_dir.glob("*.roof_field.json")):
    doc = json.loads(path.read_text())
    sc = doc["scene"]; s = float(sc["content_scale"]); st = float(doc["grid"]["step"])
    cells = [c for c in doc.get("cells") or [] if c.get("n", 0) >= 4 and c.get("raw_m") is not None]
    src = doc["building"]["source"]
    if len(cells) < 12:
        rows.append({"source": src, "form": "flat", "reason": "fewer than 12 measured vertices"}); path_out = out_dir / path.name
        path_out.write_text(json.dumps(doc)); continue
    X = np.array([c["i"] * st / s for c in cells]); Y = np.array([c["j"] * st / s for c in cells])
    Z = np.array([float(c["raw_m"]) for c in cells])
    cx, cy = X.mean(), Y.mean()
    # footprint long axis from the lattice extent's principal direction
    # axes from the minimum-area bounding rectangle of ALL lattice cells (eigen axes are arbitrary on squares)
    XA = np.array([c["i"] * st / s for c in doc["cells"]]); YA = np.array([c["j"] * st / s for c in doc["cells"]])
    best_ang, best_area, best_dims = 0.0, 1e18, (1.0, 1.0)
    for deg in range(0, 90, 2):
        t = math.radians(deg); ua_ = (math.sin(t), math.cos(t)); ub_ = (math.cos(t), -math.sin(t))
        Aa = (XA - cx) * ua_[0] + (YA - cy) * ua_[1]; Bb = (XA - cx) * ub_[0] + (YA - cy) * ub_[1]
        da, db = Aa.max() - Aa.min() + st / s, Bb.max() - Bb.min() + st / s
        if da * db < best_area:
            best_ang, best_area, best_dims = t, da * db, (da, db)
    theta = best_ang if best_dims[0] >= best_dims[1] else best_ang + math.pi / 2  # bearing of the long axis
    p10, p90 = np.percentile(Z, 10), np.percentile(Z, 90)
    flat_rms = trimmed_rms(Z - np.median(Z))
    cand = []
    for axis_name, th in (("long", theta), ("short", theta + math.pi / 2)):
        ua = np.array([math.sin(th), math.cos(th)]); ub = np.array([math.cos(th), -math.sin(th)])
        A_ = (X - cx) * ua[0] + (Y - cy) * ua[1]; B_ = (X - cx) * ub[0] + (Y - cy) * ub[1]
        ridge_brg = math.degrees(th) % 180
        (k, c0), rms, _ = fit_lin([B_], Z)
        cand.append(("shed", axis_name, rms, {"slope": float(k), "c": float(c0), "pitch_deg": math.degrees(math.atan(abs(k))),
                                              "ridge_bearing_deg": ridge_brg, "th": th}))
        for b0 in np.arange(-1.5, 1.51, 0.25):
            (k, r), rms, _ = fit_lin([-np.abs(B_ - b0)], Z)
            if k > 0:
                cand.append(("gable", axis_name, rms, {"k": float(k), "r": float(r), "b0": float(b0),
                                                       "pitch_deg": math.degrees(math.atan(k)), "ridge_bearing_deg": ridge_brg, "th": th}))
        if axis_name == "long":
            half_a = (A_.max() - A_.min()) / 2; half_b = (B_.max() - B_.min()) / 2; d = max(0.0, half_a - half_b)
            a0 = (A_.max() + A_.min()) / 2
            for b0 in np.arange(-1.0, 1.01, 0.25):
                t = np.maximum(np.abs(B_ - b0), np.abs(A_ - a0) - d)
                (k, r), rms, _ = fit_lin([-t], Z)
                if k > 0:
                    cand.append(("hip", axis_name, rms, {"k": float(k), "r": float(r), "b0": float(b0), "a0": float(a0), "d": float(d),
                                                         "pitch_deg": math.degrees(math.atan(k)), "ridge_bearing_deg": ridge_brg, "th": th}))
    order = {"shed": 1, "gable": 2, "hip": 3}
    all_cand = list(cand)  # before the shape gates: AUTHORED-ROOF-01 "fit" decisions choose among these
    cand = [c for c in cand if 10.0 <= c[3]["pitch_deg"] <= 50.0]
    # one roof model fits one roughly rectangular mass: an L- or T-shaped footprint stays on its tier field
    ua0 = np.array([math.sin(theta), math.cos(theta)]); ub0 = np.array([math.cos(theta), -math.sin(theta)])
    # rectangularity over ALL lattice cells against the minimum-area rectangle
    rect = len(XA) * (st / s) ** 2 / max(1e-6, best_area)
    if rect < 0.8:
        cand = []
    best = None
    if cand:
        m = min(c[2] for c in cand)
        best = min((c for c in cand if c[2] <= m * 1.05), key=lambda c: order[c[0]])
    # 0.5 m: the 1 m footprint erosion leaves only the strip near the ridge on a 6 m house (25 deg -> ~0.7 m spread)
    pitched = best is not None and (p90 - p10) >= 0.5 and best[2] <= 0.6 * flat_rms
    if pitched:
        # common-sense gate on the fitted shape itself (2026-10-13): an eave below 1.8 m is below a door
        # head (way/725138281 fitted a shed with eave -0.56 m), and a shed rising > 4.5 m over the cells is a
        # stepped/tiered roof mis-read as one plane (way/477599555: 41 deg, 10 m rise). Those stay flat.
        f0, _ax, _rms, P0 = best; th0 = P0["th"]
        ub0 = np.array([math.cos(th0), -math.sin(th0)])
        bb = [(c["i"] * st / s - cx) * ub0[0] + (c["j"] * st / s - cy) * ub0[1] for c in doc["cells"]]
        if f0 == "shed":
            zz = [P0["c"] + P0["slope"] * b for b in bb]
        elif f0 == "gable":
            zz = [P0["r"] - P0["k"] * abs(b - P0["b0"]) for b in bb]
        else:
            zz = None  # hip eave checked below via the same percentile
        if zz is not None:
            eave0 = float(np.percentile(zz, 2))
            if eave0 < 1.8 or (f0 == "shed" and max(zz) - eave0 > 4.5):
                pitched = False
    # AUTHORED-ROOF-01 (2026-10-16): a hand decision in authored_building_appearance.json roof_form
    # {"authored": true, "type": gable|hip, "ridge_bearing_deg", "eave_m", "ridge_m"} replaces the fit for roofs the
    # LiDAR cannot resolve (temple hall 225101048 fitted flat at 3.9 m spread; gate 225101506 fitted one shed slope).
    # The person decides form, ridge direction and heights; the lattice gives the axes' extents, as for a fit.
    hand = (AUTHORED.get("feature:building_footprint:" + src) or {}).get("roof_form") or {}
    if hand.get("authored") and hand.get("fit") and hand.get("type") in ("shed", "gable", "hip"):
        # the person decides the FORM; the LiDAR decides its numbers: best measured fit of that form, without the
        # rectangularity / pitch gates (they exist to stop guessing a form, and here the form is decided)
        pool = [c for c in all_cand if c[0] == hand["type"]]
        if pool:
            best = min(pool, key=lambda c: c[2])
            best = (best[0], "authored_fit", best[2], best[3])
            pitched = True
    elif hand.get("authored") and hand.get("type") in ("gable", "hip"):
        th = math.radians(float(hand["ridge_bearing_deg"]))
        ua = np.array([math.sin(th), math.cos(th)]); ub = np.array([math.cos(th), -math.sin(th)])
        A_ = (XA - cx) * ua[0] + (YA - cy) * ua[1]; B_ = (XA - cx) * ub[0] + (YA - cy) * ub[1]
        half_a = (A_.max() - A_.min() + st / s) / 2; half_b = (B_.max() - B_.min() + st / s) / 2
        rise = float(hand["ridge_m"]) - float(hand["eave_m"])
        # optional hand values: pitch_deg (else the roof spans the whole outline), ridge_length_m (hip), and
        # ridge_center_en [E, N] (else the outline's extent centre); the model is clamped at the eave by the bake,
        # so a steep roof over an L-shaped outline leaves the low wing flat at eave height
        k = math.tan(math.radians(float(hand["pitch_deg"]))) if hand.get("pitch_deg") else rise / half_b
        a0, b0 = float((A_.max() + A_.min()) / 2), float((B_.max() + B_.min()) / 2)
        if hand.get("ridge_center_en"):
            # lattice metres are world_x / content_scale; E = origin_E + (world_x - offset_x) / content_scale
            xm = (float(hand["ridge_center_en"][0]) - float(sc["origin_easting"])) + float(sc["offset_x"]) / s
            ym = (float(hand["ridge_center_en"][1]) - float(sc["origin_northing"])) + float(sc["offset_y"]) / s
            a0 = float((xm - cx) * ua[0] + (ym - cy) * ua[1]); b0 = float((xm - cx) * ub[0] + (ym - cy) * ub[1])
        P = {"k": k, "r": float(hand["ridge_m"]), "b0": b0,
             "pitch_deg": math.degrees(math.atan(k)), "ridge_bearing_deg": float(hand["ridge_bearing_deg"]) % 180,
             "th": th}
        if hand["type"] == "hip":
            d = float(hand["ridge_length_m"]) / 2 if hand.get("ridge_length_m") else max(0.0, half_a - half_b)
            P.update(a0=a0, d=d)
        best = (hand["type"], "authored", 0.0, P)
        pitched = True
    row = {"source": src, "n": len(cells), "rectangularity": round(float(rect), 2),
           "spread_p10_p90_m": round(float(p90 - p10), 2), "flat_rms_m": round(flat_rms, 3),
           "best": None if best is None else {"form": best[0], "axis": best[1], "rms_m": round(best[2], 3),
                                              **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in best[3].items() if k != "th"}},
           "form": best[0] if pitched else "flat"}
    if pitched:
        f, axis_name, rms, P = best; th = P["th"]
        ua = np.array([math.sin(th), math.cos(th)]); ub = np.array([math.cos(th), -math.sin(th)])
        def model(xm, ym):
            a = (xm - cx) * ua[0] + (ym - cy) * ua[1]; b = (xm - cx) * ub[0] + (ym - cy) * ub[1]
            if f == "shed":
                return P["c"] + P["slope"] * b
            if f == "gable":
                return P["r"] - P["k"] * abs(b - P["b0"])
            return P["r"] - P["k"] * max(abs(b - P["b0"]), abs(a - P["a0"]) - P["d"])
        allc = doc["cells"]
        zs = [model(c["i"] * st / s, c["j"] * st / s) for c in allc]
        eave = float(np.percentile(zs, 2)); ridge = float(max(zs))
        if axis_name == "authored":  # AUTHORED-ROOF-01: the hand eave holds; the roof never dips below it
            eave = float(hand["eave_m"])
        for c, zm in zip(allc, zs):
            c["roof_m"] = round(max(eave, zm), 3); c["model_m"] = round(zm, 3)
        doc["roof_regions"] = [{"region": 0, "elevation_m_above_ground": round(eave, 3), "vertex_count": len(allc),
                                "vertex_fraction": 1.0, "residual_rms_m": round(rms, 3),
                                "estimator": "eave of the fitted %s roof (roof_form_fit.py); lattice carries the plane heights" % f}]
        # model in the run's world frame, metres: x_m = world_x / content_scale (same frame as the lattice i*step/s)
        A_all = [(c["i"] * st / s - cx) * ua[0] + (c["j"] * st / s - cy) * ua[1] for c in allc]
        doc["roof_form"] = {"form": f, "ridge_bearing_deg": round(P["ridge_bearing_deg"], 1), "pitch_deg": round(P["pitch_deg"], 1),
                            "eave_m": round(eave, 3), "ridge_m": round(ridge, 3), "rms_m": round(rms, 3), "flat_rms_m": round(flat_rms, 3),
                            "model": {"cx_m": cx, "cy_m": cy, "th_rad": th, "a_min": float(min(A_all)), "a_max": float(max(A_all)),
                                      **{k: P[k] for k in ("k", "r", "b0", "a0", "d", "slope", "c") if k in P}},
                            "content_scale": s,
                            "fitter": "artifacts/experiments/fl4876_manual_world/roof_form_fit.py"}
        row.update({"eave_m": round(eave, 2), "ridge_m": round(ridge, 2)})
    (out_dir / path.name).write_text(json.dumps(doc))
    rows.append(row)
import collections
print("buildings", len(rows), collections.Counter(r["form"] for r in rows))
(out_dir / "roof_forms.json").write_text(json.dumps({"rule": __doc__.split("Choice rule")[1].split("\n")[0], "rows": rows}, indent=1))
