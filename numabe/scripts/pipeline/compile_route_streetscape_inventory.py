#!/usr/bin/env python3
"""Compile a per-bin route streetscape inventory (FL-4809 INVENTORY-01).

Bins the pedestrian route by arc length and assigns every canonical feature
whose geometry lies within the route-visible corridor to the bin of its
nearest route station. Per bin per streetscape group the inventory counts
observed, represented, proxy, debt, omitted_with_reason and vanished rows.

Completion predicate: vanished total is 0 across every bin, else exit 1
naming every vanished feature id with its bin and class. A route-visible
observed feature must never disappear without a decision.

No new .md files; no /tmp writes; no runtime launch.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402

SCHEMA = "y92.route_streetscape_inventory.v1"

# Recorded DEFAULT facts, proposed as rules facts in the receipt. Read from
# --rules (rules.inventory) when present, else these documented DEFAULTs.
DEFAULT_BIN_LENGTH_M = 25.0
DEFAULT_ROUTE_VISIBLE_CORRIDOR_M = 20.0

EMITTING_DISPOSITIONS = ("terrain_mass", "mesh_fixture", "surface_decal", "mesh_facade")
OMITTED_DISPOSITIONS = ("intentional_omission", "minimap_only")
DEBT_DISPOSITIONS = ("observation_owed", "representation_owed")

GROUPS = (
    "buildings",
    "storefronts_and_places",
    "poles_and_lamps",
    "fences_walls_guardrails",
    "vegetation",
    "signs",
    "crossings_and_signals",
    "rail_side",
    "waterways_and_bridges",
    "street_furniture",
    "ground",
    "unmapped_class",
)


def _decode_polyline6(shape: str) -> list[tuple[float, float]]:
    """Valhalla shape (precision 6) -> [(lon, lat)]."""
    coords: list[tuple[float, float]] = []
    lat = lon = 0
    values = [ord(c) - 63 for c in shape]
    i = 0
    while i < len(values):
        for _ in range(2):
            result = shift = 0
            while True:
                b = values[i]
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if (result & 1) else (result >> 1)
            if _ == 0:
                lat += d
            else:
                lon += d
        coords.append((lon / 1e6, lat / 1e6))
    return coords


def _group_for(semantic_class: str, tags: dict) -> str:
    if semantic_class == "building_footprint":
        return "buildings"
    if semantic_class == "place":
        return "storefronts_and_places"
    if semantic_class == "sign":
        return "signs"
    if semantic_class == "crossing":
        return "crossings_and_signals"
    if semantic_class in ("railway_boundary", "station_entrance"):
        return "rail_side"
    # FL-4809 NUMABE-MERGE-01: Mapillary classes land here by semantic
    # class, not tags (Mapillary observations carry no OSM tags).
    if semantic_class in ("utility_pole", "utility_wire"):
        return "poles_and_lamps"
    if semantic_class in ("road", "sidewalk", "terrain"):
        return "ground"
    # FL-4809 NUMABE-MERGE-01: paint sits on the ground plane.
    if semantic_class == "road_marking":
        return "ground"
    if semantic_class == "fixture":
        if tags.get("highway") in ("traffic_signals", "stop", "crossing"):
            return "crossings_and_signals"
        if tags.get("highway") == "street_lamp" or tags.get("man_made") in (
                "utility_pole", "street_light"):
            return "poles_and_lamps"
        if tags.get("barrier") in ("fence", "wall", "guard_rail", "bollard"):
            return "fences_walls_guardrails"
        return "street_furniture"
    # FL-4809 NUMABE-MERGE-01: a class with no mapping decision counts
    # under unmapped_class, never silently under other.
    return "unmapped_class"

def _sample_points(geometry: dict) -> list[tuple[float, float]]:
    kind = geometry.get("kind")
    coords = geometry.get("coordinates") or []
    if kind == "point" and len(coords) >= 2:
        return [(float(coords[0]), float(coords[1]))]
    if kind in ("polyline", "polygon") and coords:
        ring = coords[0] if isinstance(coords[0][0], (list, tuple)) else coords
        return [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
    return []


def _plan_rows_by_feature(plan_doc: dict) -> dict[str, dict]:
    return {str(r.get("feature_id")): r for r in plan_doc.get("rows", [])}


def _bridge_ids(entities_doc: dict | None) -> dict[str, set[str]]:
    """feature id -> equivalent ids across the OSM/PLATEAU generation split."""
    bridge: dict[str, set[str]] = {}
    if not entities_doc:
        return bridge
    per_entity: list[set[str]] = []
    for entity in entities_doc.get("entities", []):
        ids: set[str] = set()
        for source in entity.get("source_bridge", []):
            if source.get("source") == "osm":
                ids.add("feature:building_footprint:" + str(source["source_object_id"]))
            elif source.get("source") == "plateau":
                ids.add("feature:building_footprint:plateau/" + str(source["source_object_id"]))
        per_entity.append(ids)
    for ids in per_entity:
        for fid in ids:
            bridge.setdefault(fid, set()).update(ids - {fid})
    return bridge


def _emission_ids(build_dir: Path) -> tuple[set[str], dict[str, bool]]:
    manifest_path = build_dir / "emission_manifest.json"
    present = {"emission_manifest.json": manifest_path.is_file()}
    if not manifest_path.is_file():
        return set(), present
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("errors"):
        raise ValueError("emission manifest contains terminal-state errors")
    emitted = {
        str(row["feature_id"])
        for row in manifest.get("emissions", [])
        if row.get("terminal_state") == "emitted" and row.get("feature_id")
    }
    return emitted, present


def _debt_ids(debt_doc: dict | None) -> tuple[set[str], set[str]]:
    owed: set[str] = set()
    seen: set[str] = set()
    if not debt_doc:
        return owed, seen
    for row in debt_doc.get("features", []):
        fid = str(row.get("feature_id"))
        seen.add(fid)
        if (row.get("observation_state") in ("observation_owed", "representation_owed")
                or row.get("debt_state") in ("observation_owed", "representation_owed")):
            owed.add(fid)
    return owed, seen


def build(route_path: Path, world_ir_path: Path, plan_path: Path,
          build_dir: Path, debt_path: Path | None,
          entities_path: Path | None, extra_observations: list[Path],
          rules_path: Path | None,
          generated_at: str) -> tuple[dict, dict]:
    route_doc = json.loads(route_path.read_text(encoding="utf-8"))
    shape = route_doc["trip"]["legs"][0]["shape"]
    route_lonlat = _decode_polyline6(shape)
    route_xy = [GF.utm_forward(lon, lat) for lon, lat in route_lonlat]

    bin_length_m = DEFAULT_BIN_LENGTH_M
    corridor_m = DEFAULT_ROUTE_VISIBLE_CORRIDOR_M
    rules_basis = "module DEFAULT"
    if rules_path is not None and rules_path.is_file():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        inv = (rules.get("inventory") or {})
        if inv.get("bin_length_m") is not None:
            bin_length_m = float(inv["bin_length_m"])
            rules_basis = "rules.inventory"
        if inv.get("route_visible_corridor_m") is not None:
            corridor_m = float(inv["route_visible_corridor_m"])
            rules_basis = "rules.inventory"
    stations: list[tuple[float, float]] = [route_xy[0]]
    acc = 0.0
    for a, b in zip(route_xy, route_xy[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        while acc + seg >= bin_length_m:
            take = bin_length_m - acc
            frac = take / seg if seg > 0 else 0.0
            nxt = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
            stations.append(nxt)
            a = nxt
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            acc = 0.0
        acc += seg
    stations.append(route_xy[-1])
    total_m = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                  for a, b in zip(route_xy, route_xy[1:]))
    n_bins = max(1, len(stations) - 1)
    bins: list[dict] = []
    for idx in range(n_bins):
        bins.append({
            "bin": idx,
            "from_m": round(idx * bin_length_m, 3),
            "to_m": round(min((idx + 1) * bin_length_m, total_m), 3),
            "endpoints": [list(stations[idx]), list(stations[idx + 1])],
            "groups": {group: {"observed": 0, "represented": 0, "proxy": 0,
                                "observed_physical": 0,
                                "debt": 0, "omitted_with_reason": 0,
                                "vanished": 0} for group in GROUPS},
            "vanished_ids": [],
        })

    world = json.loads(world_ir_path.read_text(encoding="utf-8"))
    observations = {str(o["observation_id"]): o
                    for o in world.get("source_observations", [])}

    def _tags_of(feature: dict) -> dict:
        for obs_id in feature.get("supporting_observation_ids", []):
            obs = observations.get(str(obs_id))
            if obs is not None:
                tags = (obs.get("attributes") or {}).get("tags")
                if isinstance(tags, dict):
                    return tags
        return {}

    features: list[dict] = []
    unmapped_classes: set[str] = set()
    for feature in world.get("canonical_features", []):
        cls = str(feature.get("semantic_class") or "")
        group = _group_for(cls, _tags_of(feature))
        if group == "unmapped_class":
            unmapped_classes.add(cls)
        pts = _sample_points(feature.get("geometry", {}))
        if not pts:
            continue
        features.append({
            "feature_id": str(feature.get("feature_id")),
            "semantic_class": cls,
            "group": group,
            "points": pts,
            "source": "canonical",
        })

    positioned_extra = 0
    for extra_path in extra_observations:
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        extra_obs = {str(o["observation_id"]): o
                     for o in extra.get("source_observations", [])}

        def _extra_tags(feat: dict) -> dict:
            for obs_id in feat.get("supporting_observation_ids", []):
                obs = extra_obs.get(str(obs_id))
                if obs is not None:
                    tags = (obs.get("attributes") or {}).get("tags")
                    if isinstance(tags, dict):
                        return tags
            return {}

        for feat in extra.get("canonical_features", []):
            pts = _sample_points(feat.get("geometry", {}))
            if not pts:
                continue
            cls = str(feat.get("semantic_class") or "")
            group = _group_for(cls, _extra_tags(feat))
            duplicate = False
            for known in features:
                if known["group"] != group:
                    continue
                if min(math.hypot(px - qx, py - qy)
                       for px, py in pts for qx, qy in known["points"]) <= 2.0:
                    duplicate = True
                    break
            if duplicate:
                continue
            positioned_extra += 1
            features.append({
                "feature_id": str(feat.get("feature_id")),
                "semantic_class": cls,
                "group": group,
                "points": pts,
                "source": extra_path.name,
            })

    def _assign(feature: dict) -> tuple[int, float] | None:
        best = None
        for idx in range(len(stations)):
            sx, sy = stations[idx]
            dist = min(math.hypot(px - sx, py - sy) for px, py in feature["points"])
            if best is None or dist < best[1]:
                best = (idx, dist)
        if best is None or best[1] > corridor_m:
            return None
        station = best[0]
        return (min(station, n_bins - 1), best[1])


    corridor: list[dict] = []
    outside_corridor = 0
    for feature in features:
        hit = _assign(feature)
        if hit is None:
            outside_corridor += 1
            continue
        feature["bin"] = hit[0]
        feature["corridor_distance_m"] = round(hit[1], 3)
        corridor.append(feature)

    plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_rows = _plan_rows_by_feature(plan_doc)
    entities_doc = (json.loads(entities_path.read_text(encoding="utf-8"))
                    if entities_path is not None and entities_path.is_file() else None)
    bridge = _bridge_ids(entities_doc)

    def _plan_row_for(fid: str) -> dict | None:
        if fid in plan_rows:
            return plan_rows[fid]
        for alt in bridge.get(fid, ()):
            if alt in plan_rows:
                return plan_rows[alt]
        return None

    emitted, emission_files = _emission_ids(build_dir)

    def _emitted(fid: str) -> bool:
        if fid in emitted:
            return True
        return any(alt in emitted for alt in bridge.get(fid, ()))

    debt_doc = (json.loads(debt_path.read_text(encoding="utf-8"))
                if debt_path is not None and debt_path.is_file() else None)
    debt_owed, _ = _debt_ids(debt_doc)

    vanished: list[dict] = []
    out_of_corridor_plan_gaps: list[dict] = []
    for feature in corridor:
        fid = feature["feature_id"]
        group = feature["group"]
        cell = bins[feature["bin"]]["groups"][group]
        cell["observed"] += 1
        row = _plan_row_for(fid)
        is_emitted = _emitted(fid)
        row_emits = row is not None and row.get("disposition") in EMITTING_DISPOSITIONS
        ev_complete = True if row is None else row.get("evidence_complete", True)
        if row_emits and is_emitted and ev_complete:
            cell["represented"] += 1
            if row.get("representation_kind") == "synthetic_identity_proxy":
                cell["proxy"] += 1
            elif row.get("representation_kind") == "observed_physical":
                cell["observed_physical"] += 1
        elif (fid in debt_owed or (row is not None
                                   and row.get("disposition") in DEBT_DISPOSITIONS)
                or (row is not None and not ev_complete)):
            cell["debt"] += 1
        elif (row is not None and row.get("disposition") in OMITTED_DISPOSITIONS
                and not is_emitted):
            cell["omitted_with_reason"] += 1
        else:
            cell["vanished"] += 1
            bins[feature["bin"]]["vanished_ids"].append(fid)
            if is_emitted:
                reason = "emission without emitting plan row"
            elif row is not None:
                reason = "emitting plan row without emission"
            else:
                reason = "no plan row, no emission"
            vanished.append({"feature_id": fid, "bin": feature["bin"],
                             "group": group,
                             "semantic_class": feature["semantic_class"],
                             "reason": reason})
    vanished.sort(key=lambda r: (r["bin"], r["group"], r["feature_id"]))

    for fid, row in plan_rows.items():
        if row.get("disposition") not in EMITTING_DISPOSITIONS:
            continue
        if _emitted(fid) or any(_emitted(alt) for alt in bridge.get(fid, ())):
            continue
        if any(f["feature_id"] == fid for f in corridor):
            continue
        out_of_corridor_plan_gaps.append(
            {"feature_id": fid, "disposition": row.get("disposition"),
             "semantic_class": row.get("semantic_class")})

    direct: dict[str, int] = {group: 0 for group in GROUPS}
    double_binned: list[str] = []
    seen_bins: dict[str, int] = {}
    for feature in corridor:
        direct[feature["group"]] += 1
        if feature["feature_id"] in seen_bins:
            double_binned.append(feature["feature_id"])
    summed = {group: sum(b["groups"][group]["observed"] for b in bins) for group in GROUPS}
    assert summed == direct, f"bin sums {summed} != direct {direct}"
    assert not double_binned, f"features in two bins: {double_binned}"

    totals = {group: {"observed": 0, "represented": 0, "proxy": 0,
                      "observed_physical": 0, "debt": 0,
                      "omitted_with_reason": 0, "vanished": 0} for group in GROUPS}
    for b in bins:
        for group in GROUPS:
            for key in totals[group]:
                totals[group][key] += b["groups"][group][key]

    lines = []
    for b in bins:
        parts = []
        for group in GROUPS:
            cell = b["groups"][group]
            if cell["observed"] == 0:
                continue
            chunk = (f"{group} observed {cell['observed']} "
                     f"represented {cell['represented']}")
            split = []
            if cell["observed_physical"]:
                split.append(f"physical {cell['observed_physical']}")
            if cell["proxy"]:
                split.append(f"proxy {cell['proxy']}")
            if split:
                chunk += " [" + ", ".join(split) + "]"
            extras = []
            if cell["debt"]:
                extras.append(f"debt {cell['debt']}")
            if cell["omitted_with_reason"]:
                extras.append(f"omitted {cell['omitted_with_reason']}")
            if cell["vanished"]:
                extras.append(f"vanished {cell['vanished']}")
            if extras:
                chunk += " (" + ", ".join(extras) + ")"
            parts.append(chunk)
        lines.append(f"{b['from_m']:g}-{b['to_m']:g} m: " + "; ".join(parts))

    document = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "inputs": {
            "route": str(route_path),
            "world_ir": str(world_ir_path),
            "plan": str(plan_path),
            "build_dir": str(build_dir),
            "debt": str(debt_path) if debt_path is not None else None,
            "entities": str(entities_path) if entities_path is not None else None,
            "extra_observations": [str(p) for p in extra_observations],
            "rules": str(rules_path) if rules_path is not None else None,
        },
        "rules": {
            "bin_length_m": bin_length_m,
            "route_visible_corridor_m": corridor_m,
            "rules_basis": rules_basis,
            "proposed_rules_facts": {
                "inventory.bin_length_m": DEFAULT_BIN_LENGTH_M,
                "inventory.route_visible_corridor_m": DEFAULT_ROUTE_VISIBLE_CORRIDOR_M,
            },
        },
        "route": {"length_m": round(total_m, 3), "bins": n_bins},
        "counts": {
            "corridor_features": len(corridor),
            "outside_corridor": outside_corridor,
            "positioned_extra_observations": positioned_extra,
            "unmapped_semantic_classes": sorted(unmapped_classes),
        },
        "emission_files_present": emission_files,
        "bins": bins,
        "totals": totals,
        "vanished": vanished,
        "out_of_corridor_plan_gaps": sorted(
            out_of_corridor_plan_gaps, key=lambda r: r["feature_id"]),
        "text_table": lines,
    }
    receipt = {
        "schema": "y92.route_streetscape_inventory_receipt.v1",
        "inputs": document["inputs"],
        "route_length_m": round(total_m, 3),
        "bins": n_bins,
        "corridor_features": len(corridor),
        "outside_corridor": outside_corridor,
        "totals": totals,
        "vanished_total": len(vanished),
        "vanished_ids": [r["feature_id"] for r in vanished],
        "emission_files_present": emission_files,
        "text_table": lines,
    }
    return document, receipt


def render_png(document: dict, png_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    bins = document["bins"]
    ratios = []
    for b in bins:
        observed = sum(cell["observed"] for cell in b["groups"].values())
        represented = sum(cell["represented"] for cell in b["groups"].values())
        ratios.append(represented / observed if observed else 1.0)
    route_doc_shape = document["inputs"]["route"]
    _ = route_doc_shape
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.set_title("Route represented ratio per 25 m bin")
    ax.set_xlabel("E (EPSG:32654)")
    ax.set_ylabel("N (EPSG:32654)")
    segments = []
    values = []
    for b in bins:
        segments.append([b["endpoints"][0], b["endpoints"][1]])
        values.append(ratios[b["bin"]])
    lc = LineCollection(segments, cmap="RdYlGn", norm=plt.Normalize(0, 1),
                        linewidths=4)
    lc.set_array(values)
    ax.add_collection(lc)
    fig.colorbar(lc, ax=ax, label="represented / observed")
    ax.autoscale_view()
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--build-dir", type=Path, required=True)
    ap.add_argument("--debt", type=Path, default=None)
    ap.add_argument("--entities", type=Path, default=None)
    ap.add_argument("--extra-observations", type=Path, nargs="*", default=[])
    ap.add_argument("--extra-world-ir", type=Path, nargs="*", default=[],
                    help="repeatable: extra world IR documents (e.g. OBSERVE-03 "
                         "mapillary_objects.world_ir.json) joining the observed "
                         "side generically by semantic_class")
    ap.add_argument("--rules", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--png-out", type=Path, default=None)
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    args = ap.parse_args(argv)
    document, receipt = build(
        args.route, args.world_ir, args.plan, args.build_dir, args.debt,
        args.entities,
        list(args.extra_observations) + list(args.extra_world_ir),
        args.rules, args.generated_at)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, indent=1,
                                           sort_keys=True) + "\n", encoding="utf-8")
    if args.png_out is not None:
        args.png_out.parent.mkdir(parents=True, exist_ok=True)
        render_png(document, args.png_out)

    print(f"[inventory] bins={document['route']['bins']} "
          f"corridor={document['counts']['corridor_features']} "
          f"outside={document['counts']['outside_corridor']} "
          f"vanished={receipt['vanished_total']}")
    for group in GROUPS:
        cell = receipt["totals"][group]
        if cell["observed"] == 0:
            continue
        ratio = cell["represented"] / cell["observed"] if cell["observed"] else 0.0
        print(f"[inventory] {group}: observed={cell['observed']} "
              f"represented={cell['represented']} "
              f"(physical={cell['observed_physical']} proxy={cell['proxy']}) "
              f"ratio={ratio:.2f} "
              f"debt={cell['debt']} omitted={cell['omitted_with_reason']} "
              f"vanished={cell['vanished']}")
    if receipt["vanished_total"]:
        print(f"[inventory] VANISHED {receipt['vanished_total']}:")
        for row in document["vanished"]:
            print(f"[inventory] vanished bin={row['bin']} group={row['group']} "
                  f"{row['feature_id']} ({row['reason']})")
        return 1
    print("[inventory] COMPLETE: vanished total is 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
