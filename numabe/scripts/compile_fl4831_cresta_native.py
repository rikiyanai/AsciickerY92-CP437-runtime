#!/usr/bin/env python3
"""Compile the six approved native car drawings and authored rigid bindings.

Not an art generator/fitter or image animation player. The manual owner chooses
retained feature groups. Preserve native choices and multi-feature supports.
Source PNGs/GIFs are never read; runtime gets no whole-car plane.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import struct
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "assets/meshes/incoming/cresta_gx71.foreground.manual.json"
PART_PREFIXES = ("", "wheel.-1.0.", "wheel.1.0.", "wheel.-1.1.", "wheel.1.1.")
SUBJECTS = ("cresta_gx71", "bluebird510", "ae86", "porsche911", "kei_sambar", "teardrop1930")
INDEX = ROOT / "godot_project/assets/world_mesh/foreground/index.v1.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_owner(path):
    raw = json.loads(path.read_text())
    shared = {}
    if raw.get("mechanical_contract"):
        shared_path = ROOT / raw["mechanical_contract"]
        shared = json.loads(shared_path.read_text())
        if shared.get("schema") != "asciicker.car.foreground_mechanics.v1":
            raise ValueError("unrecognized common font/part contract")
    owner = {**shared, **raw}
    owner["bindings"] = shared.get("bindings", []) + raw["bindings"]
    return owner


def runtime_point(p):
    x, y, z = p
    return [-z, y, x]


def unspin(p, center, radians):
    """Source wheels rotate about +X; inverse restores neutral part."""
    x, y, z = [p[i] - center[i] for i in range(3)]
    c, s = math.cos(-radians), math.sin(-radians)
    return [x + center[0], c*y - s*z + center[1], s*y + c*z + center[2]]


def unit(p):
    length = math.sqrt(sum(x*x for x in p))
    if not length:
        raise ValueError("zero camera vector")
    return [x/length for x in p]


def project(p, eye, target):
    f = unit([target[i]-eye[i] for i in range(3)])
    r = unit([-f[2], 0., f[0]])
    u = [r[1]*f[2]-r[2]*f[1], r[2]*f[0]-r[0]*f[2], r[0]*f[1]-r[1]*f[0]]
    q = [p[i]-eye[i] for i in range(3)]
    depth = sum(q[i]*f[i] for i in range(3))
    return [320 + 720*sum(q[i]*r[i] for i in range(3))/depth,
            238 - 720*sum(q[i]*u[i] for i in range(3))/depth, depth]


def part_for_feature(feature, owner):
    matches = [b for b in owner["bindings"] if feature.startswith(b["prefix"])]
    return int(max(matches, key=lambda b: len(b["prefix"]))["part"]) if matches else None


def feature_motion(feature, part, owner):
    suffix = feature[len(PART_PREFIXES[part]):] if part else ""
    return 1 if part and suffix in owner["wheel_motion_binding"]["outline_suffixes"] else 2 if part else 0


def ink_depth_guides(pixels, width, token, contributions, owner, wheels, spin, eye, target,
                     fallback, plate=None, known_sites=()):
    """Transport existing per-ink feature points, NOT geometry/image depth.

    A glyph can join several depths/parts. Averaging them supplies an anchor,
    not a visibility plane. Unmatched ink inherits the nearest associated ink
    point explicitly; it never borrows the foreground occluder's depth.
    Binary records: native-rect pixel u16, part u8, motion u8, local XYZ f32.
    """
    associated = {}
    for contribution in contributions:
        feature = contribution["feature"]
        part = part_for_feature(feature, owner)
        if part is None:
            if feature not in known_sites:
                raise ValueError(f"unbound native ink feature: {feature}")
            part = next((i for i, prefix in enumerate(PART_PREFIXES) if i and feature.startswith(prefix)), 0)
        motion = feature_motion(feature, part, owner)
        indices = contribution.get("ink", [])
        points = contribution["world"]
        if indices and len(indices) != len(points):
            raise ValueError("per-ink contribution/point count differs")
        for index, point in zip(indices, points):
            if not 0 <= index < len(pixels):
                raise ValueError("native contribution ink index outside exact mask")
            # At a junction the frontmost associated feature owns the shared
            # ink depth. No additional feature or surface is invented.
            depth = project(point, eye, target)[2]
            old = associated.get(index)
            if old is None or depth < old[0]:
                associated[index] = (depth, part, motion, point)
    axes = camera_arrays(eye, target)
    encoded = bytearray()
    inferred = 0
    physically_refused = 0
    # pixels are in font-origin coordinates; derive their native ink rectangle.
    lo = pixels.min(axis=0)
    for index, pixel in enumerate(pixels):
        if plate is not None:
            page = np.array([token["x"], token["y"]]) + pixel
            ray = axes[2] + (page[0]-320)/720*axes[0] + (238-page[1])/720*axes[1]
            distance = (plate["center"][2]-eye[2])/ray[2]
            point = (np.asarray(eye)+ray*distance).tolist()
            supported = distance > 0 and all(min(p[j] for p in plate["corners"])-1e-6 <= point[j] <=
                                            max(p[j] for p in plate["corners"])+1e-6 for j in range(3))
            # Preserve disputed whole passages in the diagnostic bank, but
            # explicitly refuse unsupported physical ink. Do not stretch the
            # plate, clamp a point to its edge, or silently drop a character.
            part, motion = 0, 0 if supported else 255
            physically_refused += int(not supported)
        elif associated:
            closest = index if index in associated else min(associated,
                key=lambda other: float(np.sum((pixels[other]-pixel)**2)))
            _, part, motion, point = associated[closest]
            inferred += int(closest != index)
        else:
            # Explicit original zero-match association, already bounded by
            # selected_cloud; do not silently project onto scene geometry.
            best = min(fallback, key=lambda support: (support[0] != 0, -support[4]))
            part, x, y, z, _, motion = best
            point = [z, y, -x]
            # fallback positions are already in neutral local coordinates.
            local = [x, y, z]
            relative = pixel-lo
            native_index = int(relative[1])*width+int(relative[0])
            encoded.extend(struct.pack("<HBBfff", native_index, part, motion, *local))
            inferred += 1
            continue
        local = unspin(point, wheels[PART_PREFIXES[part]], spin) if motion == 2 else point
        relative = pixel-lo
        native_index = int(relative[1])*width+int(relative[0])
        encoded.extend(struct.pack("<HBBfff", native_index, part, motion, *runtime_point(local)))
    return base64.b64encode(encoded).decode("ascii"), inferred, physically_refused


def camera_arrays(eye, target):
    f = np.array(unit([target[i]-eye[i] for i in range(3)]))
    r = np.array(unit([-f[2], 0., f[0]]))
    return np.array([r, np.cross(r, f), f])


def project_many(points, eye, target):
    q = (np.asarray(points)-eye) @ camera_arrays(eye, target).T
    return np.column_stack((320+720*q[:, 0]/q[:, 2], 238-720*q[:, 1]/q[:, 2], q[:, 2]))


def visibility_depth(vertices, triangles, eye, target):
    """Geometry is only an occluder here; it supplies no line/glyph candidates."""
    q = project_many(vertices, eye, target)
    depth = np.full((476, 640), np.inf)
    for face in triangles:
        a, b, c = q[face]
        x0, y0 = np.maximum(np.floor(np.minimum(np.minimum(a[:2], b[:2]), c[:2])).astype(int), [0, 0])
        x1, y1 = np.minimum(np.ceil(np.maximum(np.maximum(a[:2], b[:2]), c[:2])).astype(int), [639, 475])
        if x1 < x0 or y1 < y0:
            continue
        den = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(den) < 1e-9:
            continue
        yy, xx = np.mgrid[y0:y1+1, x0:x1+1]+0.5
        u = ((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
        v = ((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den
        w = 1-u-v
        inside = (u >= 0) & (v >= 0) & (w >= 0)
        inverse = u/a[2]+v/b[2]+w/c[2]
        candidate = np.full_like(inverse, np.inf)
        np.divide(1., inverse, out=candidate, where=inside & (inverse > 0))
        view = depth[y0:y1+1, x0:x1+1]
        np.minimum(view, candidate, out=view)
    return depth


def selected_cloud(construction, eye, target, spin):
    wheels = {p["prefix"]: p for p in construction["wheel_motion"]["parts"]}
    vertices = np.array(construction["occlusion_vertices"], dtype=float)
    for prefix, wheel in wheels.items():
        lo, hi = wheel["vertices"]
        vertices[lo:hi] = [unspin(p, wheel["center"], -spin) for p in vertices[lo:hi]]
    depth = visibility_depth(vertices, np.array(construction["occlusion_triangles"]), eye, target)
    cloud = []
    for entry in construction["paths"]:
        points = np.array(entry["points"])
        for a, b in zip(points, points[1:]):
            # Mechanical sampling of SELECTED paths only, never mesh edges.
            n = max(1, math.ceil(float(np.linalg.norm(b-a))/.006))
            cloud.extend((entry["id"], (a+(b-a)*t).tolist()) for t in np.linspace(0, 1, n+1))
    tone_levels = {entry["id"]: entry["level"] for entry in construction["bounded_tone_sites"]}
    cloud.extend((entry["id"], entry["point"]) for entry in construction["bounded_tone_sites"])
    animated = []
    for feature, point in cloud:
        prefix = next((p for p in wheels if feature.startswith(p)), "")
        animated.append((feature, unspin(point, wheels[prefix]["center"], -spin) if prefix else point))
    screen = project_many([point for _, point in animated], eye, target)
    retained = []
    for item, q in zip(animated, screen):
        x, y = int(q[0]), int(q[1])
        tolerance = .12 if item[0] in tone_levels else .08
        if 0 <= x < 640 and 0 <= y < 476 and q[2] <= depth[y, x]+tolerance:
            if item[0] in tone_levels:
                # Preserve the approved tone construction's original native
                # reduction (6px columns / 17px rows), not raw point proximity.
                row, col = round((q[1]-10)/17), round(q[0]/6)
                level = tone_levels[item[0]]
                offsets = [(0, 13)] if level <= 1 else [(0, 7), (0, 13)]
                if level >= 3:
                    offsets += [(0, 14), (-1, 15)]
                if level >= 4:
                    offsets += [(3, 7), (3, 13)]
                retained.extend((item[0], item[1], [col*6+dx, row*17+dy]) for dx, dy in offsets)
            else:
                retained.append((item[0], item[1], q[:2].tolist()))
    return retained


def build(owner_path=OWNER):
    owner = load_owner(owner_path)
    if owner.get("subject") not in SUBJECTS:
        raise ValueError("subject is not one of the six approved cars")
    targets = json.loads((ROOT/"assets/meshes/incoming/tutorial_cars.source.json").read_text())["authored_turntable_targets"]["targets"]
    target_source = next(t for t in targets if t["id"] == owner["subject"])
    if target_source["construction"] != owner["construction"] \
            or target_source["construction_sha256"] != owner["construction_sha256"] \
            or digest(ROOT/target_source["gif"]) != target_source["gif_sha256"]:
        raise ValueError("manual binding differs from approved visual target")
    construction_path = ROOT / owner["construction"]
    if digest(construction_path) != owner["construction_sha256"] or digest(ROOT/owner["font"]) != owner["font_sha256"]:
        raise ValueError("approved construction/font identity changed")
    construction = json.loads(construction_path.read_text())
    wheels = {p["prefix"]: p["center"] for p in construction["wheel_motion"]["parts"]}
    if set(wheels) != set(PART_PREFIXES[1:]):
        raise ValueError("four distinct wheel attachments are required")
    catalog_path = ROOT/"godot_project/assets/glyphs/true_ff/saitamaar/catalog.json"
    catalog = json.loads(catalog_path.read_text())
    if catalog["source_font_sha256"] != owner["font_sha256"]:
        raise ValueError("runtime font differs from approved native font")
    glyphs = {g["char"]: g for g in catalog["glyphs"]}
    # Font masks are a font dependency, never a car image/frame input.
    atlas = np.asarray(Image.open(catalog_path.parent/"atlas.png").convert("L"))
    glyph_pixels = {}
    for char, glyph in glyphs.items():
        ax0, ay0, ax1, ay1 = glyph["atlas_rect"]
        ys, xs = np.nonzero(atlas[ay0:ay1, ax0:ax1] < 128)
        glyph_pixels[char] = np.column_stack((xs+glyph["ink_bounds"][0], ys+glyph["ink_bounds"][1]))
    known_sites = {p["id"] for p in construction["bounded_tone_sites"]}
    views, sources = [], []
    association_repairs = 0
    for sample in range(96):
        path = construction_path.parent/"frames"/f"{sample:03}.json"
        frame = json.loads(path.read_text())
        summary = frame["summary"]
        eye, target = summary["eye"], summary["target"]
        spin = math.radians(summary["wheel_phase_degrees"])
        plate_tokens = {(t["x"], t["y"], t["text"]): label["id"]
                        for label in summary.get("plate_labels", []) for t in label["tokens"]}
        plates = {plate["id"]: plate for plate in construction["fictional_plates"]}
        # Some original intact glyphs have zero exact-pixel temporal matches
        # although validation admitted their whole ink within the selected
        # construction's support corridor. Bind these explicitly to an existing
        # VISIBLE feature sample. This does not choose/change a glyph or invent
        # geometry. Preserve the inferred attachment as a reviewable fact.
        cloud = selected_cloud(construction, eye, target, spin)
        # The original native text constraints are selected semantic ink too.
        # Omitting these made punctuation beside a registration appear to
        # have no feature support even though it adjoins real plate lettering.
        axes = camera_arrays(eye, target)
        for (x, y, char), plate_id in plate_tokens.items():
            for dx, dy in glyph_pixels[char]:
                pixel = [x+int(dx), y+int(dy)]
                ray = axes[2]+(pixel[0]-320)/720*axes[0]+(238-pixel[1])/720*axes[1]
                if abs(ray[2]) < 1e-9:
                    raise ValueError("edge-on source semantic ink")
                distance = (plates[plate_id]["center"][2]-eye[2])/ray[2]
                point = (np.array(eye)+ray*distance).tolist()
                cloud.append((plate_id+".registration", point, pixel))
        if not cloud:
            raise ValueError("native frame has no visible feature supports")
        tree = cKDTree(np.array([screen for _, _, screen in cloud]))
        records = []
        for token in frame["contributions"]:
            source_text = token["text"]
            # Explicitly reviewed native two-scalar passage. The space has no
            # ink, but its advance and original text remain in the record.
            char = "f" if source_text == "f " and source_text in owner["native_passages"] else source_text
            if len(char) != 1 or char not in glyphs or not glyphs[char]["has_ink"]:
                raise ValueError(f"native glyph unavailable: {char!r}")
            supports, projected, count = [], [0., 0., 0.], 0
            features = []
            contributions = token["parts"]
            inferred = False
            plate_id = plate_tokens.get((token["x"], token["y"], char))
            if plate_id:
                # Manual semantic binding: registration belongs to its real
                # physical plate plane, never to whichever bumper sample is
                # nearby. No camera-facing or through-body number is admitted.
                bounds = glyphs[char]["ink_bounds"]
                x = token["x"]+(bounds[0]+bounds[2])/2
                y = token["y"]+(bounds[1]+bounds[3])/2
                axes = camera_arrays(eye, target)
                ray = axes[2]+(x-320)/720*axes[0]+(238-y)/720*axes[1]
                plate = plates[plate_id]
                if abs(ray[2]) < 1e-9:
                    raise ValueError("edge-on registration plane")
                distance = (plate["center"][2]-eye[2])/ray[2]
                point = (np.array(eye)+ray*distance).tolist()
                if distance <= 0 or not all(min(p[j] for p in plate["corners"])-1e-6 <= point[j] <=
                                             max(p[j] for p in plate["corners"])+1e-6 for j in range(3)):
                    raise ValueError("native registration outside physical plate")
                contributions = [{"feature": plate_id+".registration", "world": [point]}]
            if not any(p["world"] for p in contributions):
                bounds = glyphs[char]["ink_bounds"]
                center = [token["x"]+(bounds[0]+bounds[2])/2,
                          token["y"]+(bounds[1]+bounds[3])/2]
                distance, index = tree.query(center)
                support_limit = 8.5+math.hypot(bounds[2]-bounds[0], bounds[3]-bounds[1])/2
                if distance > support_limit:
                    raise ValueError(f"unresolved native attachment sample={sample} token={char!r} distance={distance}")
                feature, point, _ = cloud[int(index)]
                contributions = [{"feature": feature, "world": [point]}]
                inferred = True
                association_repairs += 1
            for contribution in contributions:
                feature = contribution["feature"]
                part = part_for_feature(feature, owner)
                if part is None and feature in known_sites:
                    part = next((i for i, prefix in enumerate(PART_PREFIXES) if i and feature.startswith(prefix)), 0)
                if part is None:
                    raise ValueError(f"manual binding missing: {feature}")
                points = contribution["world"]
                if not points:
                    continue
                if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
                    raise ValueError("nonfinite native 3D support")
                mean = [sum(p[j] for p in points)/len(points) for j in range(3)]
                screen = project(mean, eye, target)
                projected[0] += screen[0]*len(points)
                projected[1] += screen[1]*len(points)
                projected[2] += screen[2]*len(points)
                count += len(points)
                motion = feature_motion(feature, part, owner)
                local = unspin(mean, wheels[PART_PREFIXES[part]], spin) if motion == 2 else mean
                supports.append([part, *runtime_point(local), len(points), motion])
                features.append(feature)
            if not count:
                raise ValueError("glyph has no retained native 3D support")
            guides, inferred_ink, refused_ink = ink_depth_guides(glyph_pixels[char],
                glyphs[char]["ink_bounds"][2]-glyphs[char]["ink_bounds"][0],
                token, contributions, owner, wheels, spin, eye, target, supports,
                plates[plate_id] if plate_id else None, known_sites=known_sites)
            records.append({"cp": ord(char), "source_text": source_text,
                            "source_position": [token["x"], token["y"]],
                            "source_advance": sum(glyphs[c]["advance_px"] for c in source_text),
                            "support": supports,
                            "offset": [token["x"]-projected[0]/count, token["y"]-projected[1]/count],
                            "source_depth": projected[2]/count,
                            "features": features, "attachment_inferred": inferred,
                            "ink_guides_b64": guides, "ink_guide_inferred_count": inferred_ink,
                            "ink_physical_support_refused_count": refused_ink})
        views.append({"sample": sample, "eye": runtime_point(eye), "target": runtime_point(target), "glyphs": records})
        sources.append({"sample": sample, "sha256": digest(path)})
    result = {"schema": "asciicker.car.native_feature_glyphs.v2", "subject": owner["subject"],
            "ink_guide_abi": "pixel-u16-part-u8-motion-u8-localXYZ-f32le",
            "status": "DIAGNOSTIC_NOT_ACCEPTED", "binding_sha256": digest(owner_path),
            "construction_sha256": owner["construction_sha256"], "font_sha256": owner["font_sha256"],
            "font_px": 16, "row_pitch_px": 17, "source_focal_px": 720,
            "source_viewport": [640, 476], "inferred_attachment_count": association_repairs,
            "source_gif": target_source["gif"], "source_gif_sha256": target_source["gif_sha256"],
            "authorship": "Approved native text preserved; manual model-specific semantic bindings; mechanical3Dtransport is not newly hand-typed artwork.",
            "depiction_bindings": owner["bindings"],
            "protected_openings": [{"id": q["id"], "part": 0, "points": [runtime_point(p) for p in q["points"]]}
                                   for q in construction["protected_openings"]],
            "wheel_attachments": [{"part": part, "prefix": prefix, "center": runtime_point(wheels[prefix])}
                                  for part, prefix in enumerate(PART_PREFIXES[1:], 1)],
            "source_records": sources, "views": views}
    if owner.get("mechanical_contract"):
        result["mechanical_contract"] = owner["mechanical_contract"]
        result["mechanical_contract_sha256"] = digest(ROOT/owner["mechanical_contract"])
    return result


def compile_subject(subject, check=False):
    owner_path = ROOT / f"assets/meshes/incoming/{subject}.foreground.manual.json"
    owner = load_owner(owner_path)
    path = ROOT/owner["output"]
    doc = build(owner_path)
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n"
    different = not path.is_file() or path.read_text() != data
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    row = {"subject": subject, "file": path.name, "sha256": hashlib.sha256(data.encode()).hexdigest(),
           "bytes": len(data.encode()), "views": len(doc["views"]),
           "glyphs": sum(len(v["glyphs"]) for v in doc["views"]),
           "construction_sha256": doc["construction_sha256"], "binding_sha256": doc["binding_sha256"],
           "physical_ink_refusals": sum(g["ink_physical_support_refused_count"] for v in doc["views"] for g in v["glyphs"]),
           "different": different}
    print("CAR_NATIVE_ASSET", subject, row["views"], "views", row["glyphs"], "glyphs", row["bytes"],
          "bytes", "DIFFERENT" if check and different else "COMPILED", "runtime_acceptance=OPEN", flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--subject", choices=SUBJECTS, default="cresta_gx71")
    parser.add_argument("--all-approved", action="store_true")
    args = parser.parse_args()
    if not args.all_approved:
        return int(compile_subject(args.subject, args.check)["different"] and args.check)
    rows = []
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(compile_subject, subject, args.check) for subject in SUBJECTS]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda r: SUBJECTS.index(r["subject"]))
    index = {"schema": "asciicker.car.native_foreground_index.v1", "status": "NATIVE_ASSETS_RUNTIME_REVIEW_OPEN",
             "font_sha256": load_owner(OWNER)["font_sha256"], "view_count": 96,
             "required_runtime_review": ["continuous_rotation", "player_walk_around"],
             "cars": {r["subject"]: {k: v for k, v in r.items() if k != "different"} for r in rows}}
    data = json.dumps(index, ensure_ascii=False, indent=2)+"\n"
    index_different = not INDEX.is_file() or INDEX.read_text() != data
    if not args.check:
        INDEX.write_text(data)
    print("APPROVED_NATIVE_CARS", len(rows), "views", sum(r["views"] for r in rows),
          "runtime_rotation_walk_around=OPEN", flush=True)
    return int(args.check and (index_different or any(r["different"] for r in rows)))


if __name__ == "__main__":
    raise SystemExit(main())
