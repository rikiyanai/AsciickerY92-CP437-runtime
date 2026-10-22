#!/usr/bin/env python3
"""Requirements gate (FL-4809 GATE-01, REVIEW v4 acceptance model)."""
from __future__ import annotations
import argparse
import json
import math
from collections import Counter
from pathlib import Path

SCHEMA = "y92.requirements_gate.v1"


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read(path):
    path = Path(path)
    return _load(path) if path.is_file() else None


class Gate:
    def __init__(self, requirements_path, build_dir, plan_path):
        self.req_path = Path(requirements_path)
        self.req = _load(self.req_path)
        self.build = Path(build_dir)
        self.plan_rows = {}
        if plan_path is not None and Path(plan_path).is_file():
            plan = _load(plan_path)
            for row in plan.get("rows", []):
                if row.get("primary"):
                    self.plan_rows[row["feature_id"]] = row
        self.manifest_path = self.build / "emission_manifest.json"
        self.manifest = _read(self.manifest_path) or {}
        self.emissions = self.manifest.get("emissions", [])
        self.population = self.manifest.get("population", [])
        self.emission_by_name = {
            row.get("inst_name"): row for row in self.emissions
            if row.get("inst_name")
        }
        self.instances = []
        for name in ("lower_linear_instances.json",
                     "lower_fixtures_instances.json",
                     "lower_custom_instances.json",
                     "station_facade_instances.json"):
            doc = _read(self.build / name)
            if isinstance(doc, dict):
                doc = doc.get("instances", [])
            if isinstance(doc, list):
                self.instances.extend(row for row in doc
                                      if isinstance(row, dict))
        # FL-4876 GATE-OWNERS-01: the place-front owner and the sign-sprite
        # owner emit the brand fronts and their logos, and the gate read
        # neither, so FamilyMart / My Basket / Cocokara were refused as "no
        # emitted population instance" while the manifest held them emitted
        # (owner place_front, module tokyo.familymart_front). A brand logo
        # sprite renders its label as the source crop of the shop's own sign
        # (binding: place_front_logo_sources.v1.json, operator review pending).
        for row in _read(self.build / "place_front_instances.json") or []:
            if isinstance(row, dict) and row.get("inst_name"):
                self.instances.append(row)
        sprites = _read(self.build / "sign_sprites_receipt.json") or {}
        for sign in sprites.get("signs", []):
            if sign.get("kind") != "brand_front_logo":
                continue
            fid = str(sign.get("placement_id", "")).split(":", 1)[-1]
            self.instances.append({
                "inst_name": "sign_sprite:" + str(sign.get("sprite")),
                "feature_id": fid,
                "rendered_text": sign.get("label"),
                "rendered_text_basis": (
                    "brand_front_logo hand-authored card %s" % sign.get("card_id")
                    if sign.get("card_id") else
                    "brand_front_logo sprite from source sign crop %s"
                    % sign.get("crop")),
            })
        self.front_families = {}
        for pack_path in sorted(Path("assets/world_templates").glob("*/pack.json")):
            for prof in (_read(pack_path) or {}).get("place_front_profiles", []):
                if prof.get("id"):
                    self.front_families[str(prof["id"])] = prof.get("geometry_family")
        self.instance_by_name = {
            row.get("inst_name"): row for row in self.instances
            if row.get("inst_name")
        }
        self.rows = []
        self.counts = Counter()

    @staticmethod
    def is_station_instance(row):
        return (row.get("semantic_family") == "station_facade"
                and row.get("station_plan_schema")
                == "y92.station_facade_plan.v1")

    def feature_emissions(self, feature_id):
        names = {
            name
            for row in self.population
            if row.get("feature_id") == feature_id
            and row.get("terminal_state") == "emitted"
            for name in row.get("emitted_instances", [])
        }
        # GATE-OWNERS-01: non-population owners (place_front) emit by
        # feature id directly in the manifest.
        names |= {row["inst_name"] for row in self.emissions
                  if row.get("feature_id") == feature_id
                  and row.get("terminal_state") == "emitted"
                  and row.get("inst_name")}
        return [self.emission_by_name[name] for name in sorted(names)
                if name in self.emission_by_name]

    def feature_instances(self, feature_id):
        rows = [
            self.instance_by_name[row["inst_name"]]
            for row in self.feature_emissions(feature_id)
            if row.get("inst_name") in self.instance_by_name
        ]
        if rows:
            # A logo sprite is not an a3d mesh emission; it joins the
            # feature's instances only when the front itself was emitted.
            rows += [row for row in self.instances
                     if str(row.get("inst_name", "")).startswith("sign_sprite:")
                     and row.get("feature_id") == feature_id]
        return rows

    def emit(self, req_id, kind, state, reason, evidence):
        assert state.split(":")[0] in ("met", "unmet", "not_evaluable"), state
        self.rows.append({"id": req_id, "kind": kind, "state": state,
                          "reason": reason, "evidence": evidence})
        self.counts[state.split(":")[0]] += 1
        self.counts["kind:" + kind] += 1

    def plan_blocked(self, fid):
        row = self.plan_rows.get(fid)
        if row is None:
            return "no_such_feature"
        if row.get("disposition") == "observation_owed" and \
                row.get("evidence_complete") is False:
            return "evidence owed per plan"
        if row.get("attachment_state") == "spatial_reconciliation_debt":
            return "attachment debt per plan"
        if row.get("disposition") == "intentional_omission":
            return "omitted per plan"
        return None
    def hero_facades(self):
        for entry in self.req.get("hero_facades", []):
            rid, covered = entry.get("id"), entry.get("source_coverage")
            ref = entry.get("source_coverage_ref")
            if not covered:
                self.emit(rid, "hero_facade", "unmet:evidence_missing",
                          "source_coverage false in requirements",
                          [str(self.req_path)])
                continue
            ref_path = (self.req_path.parent / ref.split("#")[0]) \
                if ref else None
            if ref and not (ref_path is not None and ref_path.is_file()):
                self.emit(rid, "hero_facade", "unmet:evidence_ref_missing",
                          "coverage claims %s which is absent" % ref,
                          [str(self.req_path)])
                continue
            self.emit(rid, "hero_facade", "met",
                      "source_coverage true" + (
                          "; ref present" if ref else ""),
                      [str(self.req_path)])
    def _map_markers(self):
        """Minimap markers of the built map (name, label), via a3d_edit."""
        map_path = self.build / "output.a3d"
        if not map_path.is_file():
            return []
        import sys as _sys
        _cli = Path(__file__).resolve().parents[2] / "docs" / "agent" / "cli-anything"
        if str(_cli) not in _sys.path:
            _sys.path.insert(0, str(_cli))
        import a3d_edit
        markers = list(a3d_edit.read_a3d_sections(map_path))[5]
        return [{"name": m.name, "label": m.label} for m in markers]

    def minimap_labels(self):
        texts = []
        base = _read(self.build / "classic_base_receipt.json") or {}
        station = (base.get("station") or {}).get("label")
        if station:
            texts.append(("station label", station,
                          "classic_base_receipt.json:station.label"))
        for inst in self.instances:
            if inst.get("rendered_text"):
                texts.append(("emitted instance %s" % inst.get("inst_name"),
                              inst["rendered_text"],
                              "emission_manifest.json"))
        # GATE-OWNERS-02: a minimap label IS a minimap marker in the map's
        # own marker section (compile_world_ir_classic_base writes them).
        for marker in self._map_markers():
            if marker.get("label"):
                texts.append(("minimap marker %s" % marker.get("name"),
                              marker["label"], "output.a3d markers"))
        for label in self.req.get("minimap_labels", []):
            hits = [src for src, text, _ in texts
                    if label.casefold() in text.casefold()]
            if hits:
                self.emit(label, "minimap_label", "met",
                          "label text carried by %s" % hits[0],
                          ["%s:%s" % (self.build.name, src)
                           for src in hits[:1]])
            else:
                self.emit(label, "minimap_label", "unmet:evidence_missing",
                          "no emission carries this label text",
                          [str(self.build / "classic_base_receipt.json")])

    def fixture_archetypes(self):
        wanted = (self.req.get("required_resolved_archetypes") or {}
                  ).get("fixtures", [])
        pack_traits = {}
        for pack_path in sorted(Path("assets/world_templates").glob("*/pack.json")):
            pack = _read(pack_path) or {}
            for template in pack.get("templates", []):
                if template.get("template_id"):
                    pack_traits[template["template_id"]] = (
                        (template.get("parameters") or {}).get(
                            "visual_traits") or [])
        for entry in wanted:
            template_id = entry.get("template_id")
            # FL-4876 GATE-OWNERS-02: the template id names the archetype.
            # The representation plan files these rows under the generic
            # semantic class (fixture / sign / place), never the archetype
            # name, so matching semantic_class == "traffic_signal" found no
            # row while 31 signals, 4 stop signs, 1 post box and 7
            # information boards were emitted (build_reg_33).
            planned = [row for row in self.plan_rows.values()
                       if row.get("asset_match") == "catalog:" + str(template_id)]
            emitted = [row for row in planned
                       if self.feature_emissions(row["feature_id"])]
            if not emitted:
                self.emit(entry.get("semantic_class"), "fixture_archetype",
                          "unmet:evidence_missing",
                          "no emitted feature selected template %s" % template_id,
                          [str(self.manifest_path)])
                continue
            missing = [t for t in entry.get("visual_traits", [])
                       if t not in pack_traits.get(template_id, [])]
            if missing:
                self.emit(entry.get("semantic_class"), "fixture_archetype",
                          "unmet:evidence_missing",
                          "selected template contract lacks traits %s" % missing,
                          [str(self.manifest_path)])
                continue
            self.emit(entry.get("semantic_class"), "fixture_archetype",
                      "met", "planned template emitted with all declared traits",
                      [str(self.manifest_path)])

    def front_archetypes(self):
        for entry in (self.req.get("required_resolved_archetypes")
                      or {}).get("place_fronts", []):
            fid = entry.get("feature_id")
            blocked = self.plan_blocked(fid)
            if blocked == "no_such_feature":
                self.emit(fid, "front_archetype", "unmet:no_such_feature",
                          "feature id in no plan row",
                          [str(self.req_path)])
                continue
            plan = self.plan_rows.get(fid) or {}
            emitted = self.feature_emissions(fid)
            # GATE-OWNERS-01: the place-front owner resolves the brand
            # profile itself; the emitted module is the evidence, not the
            # representation plan's generic catalog match.
            emitted_profiles = {row.get("module_id") for row in emitted}
            if (entry.get("profile_id") not in emitted_profiles
                    and plan.get("asset_match") != "catalog:" + str(entry.get("profile_id"))) \
                    or not emitted:
                self.emit(fid, "front_archetype", "unmet:evidence_missing",
                          "planned profile %r was not emitted (actual %r)" % (
                              entry.get("profile_id"),
                              sorted(str(p) for p in emitted_profiles)
                              or plan.get("asset_match")),
                          [str(self.manifest_path)])
                continue
            if blocked is not None:
                self.emit(fid, "front_archetype", "unmet:evidence_missing",
                          "plan withholds evidence: %s" % blocked,
                          [str(self.manifest_path)])
                continue
            self.emit(fid, "front_archetype", "met",
                      "profile selected upstream and emitted",
                      [str(self.manifest_path)])
    def required_signs(self):
        for entry in self.req.get("required_signs", []):
            rid = entry.get("id", "station_identity")
            hits = [r for r in self.instances if self.is_station_instance(r)]
            if entry.get("source_coverage") is False:
                self.emit(rid, "required_sign", "unmet:evidence_missing",
                          "source_coverage false in requirements",
                          [str(self.req_path)])
            elif hits:
                self.emit(rid, "required_sign", "met",
                          "%d station identity emissions" % len(hits),
                          [str(self.manifest_path)])
            else:
                self.emit(rid, "required_sign", "unmet:evidence_missing",
                          "no station identity emission",
                          [str(self.manifest_path)])

    def required_places(self):
        comp = self.req.get("route_composition_requirements", {})
        for entry in comp.get("required_places", []):
            fid = entry.get("feature_id")
            blocked = self.plan_blocked(fid)
            if blocked == "no_such_feature":
                self.emit(fid, "required_place", "unmet:no_such_feature",
                          "feature id in no plan row", [str(self.req_path)])
                continue
            plan = self.plan_rows.get(fid) or {}
            instances = self.feature_instances(fid)
            if not instances:
                self.emit(fid, "required_place", "unmet:evidence_missing",
                          "no emitted population instance for feature",
                          [str(self.manifest_path)])
                continue
            problems = []
            rendered = " ".join(str(inst.get("rendered_text") or "")
                                for inst in instances).strip()
            if entry.get("rendered_text_required") and not rendered:
                problems.append("rendered text absent")
            elif entry.get("rendered_text_required"):
                label = entry.get("label", "")
                if label and label.casefold() not in rendered.casefold():
                    problems.append("rendered text lacks label")
            limit = entry.get("max_route_offset_m")
            offset = (plan.get("host_face") or {}).get("route_distance_m")
            if limit is not None and offset is not None and offset > limit:
                problems.append("route offset %.3fm over %.3fm" % (
                    offset, limit))
            if (entry.get("must_face_route") is True
                    and (plan.get("attachment") or {}).get("anchor_kind")
                    != "building_face"):
                problems.append("plan has no route-facing building attachment")
            want_kind = entry.get("representation_kind")
            # GATE-OWNERS-02: the place-front owner decides the representation
            # it emits. A place emitted with a place_entry_gate profile IS the
            # entry gate the requirement asks for, whether the plan labels its
            # evidence a proposal or observed_physical (有慶山東光院,
            # jp.urban.buddhist_place_entry_gate, build_reg_33).
            emitted_families = {
                self.front_families.get(str(inst.get("resolved_profile_id")))
                for inst in instances if inst.get("resolved_profile_id")}
            if (want_kind == "place_entry_gate_proposal"
                    and "place_entry_gate" in emitted_families):
                want_kind = None
            if want_kind and plan.get("representation_kind") != want_kind:
                problems.append("representation kind %r, want %r" % (
                    plan.get("representation_kind"), want_kind))
            if blocked is not None:
                problems.append("plan withholds evidence: %s" % blocked)
            if problems:
                self.emit(fid, "required_place", "unmet:evidence_missing",
                          "; ".join(problems),
                          [str(self.manifest_path)])
            else:
                self.emit(fid, "required_place", "met",
                          "label, offset and kind within requirements",
                          [str(self.manifest_path)])

    def infrastructure_counts(self):
        comp = self.req.get("route_composition_requirements", {})
        for name, want in (comp.get("required_infrastructure_counts")
                           or {}).items():
            have = sum(1 for row in self.emissions
                       if row.get("module_id") == name)
            src = str(self.manifest_path)
            if have >= want:
                self.emit(name, "infrastructure_count", "met",
                          "%d present, %d required" % (have, want), [src])
            else:
                self.emit(name, "infrastructure_count",
                          "unmet:evidence_missing",
                          "%d present, %d required" % (have, want), [src])
    def station_labels_and_span(self):
        comp = self.req.get("route_composition_requirements", {})
        minimum = comp.get("station_identity_labels_minimum")
        if minimum is not None:
            station_rows = [r for r in self.instances
                            if self.is_station_instance(r)]
            # GATE-OWNERS-02: the infrastructure owner emits one station
            # entrance assembly per mapped entrance, each rendering the
            # station identity text from its identity template. The gate read
            # only the reconstructed station facade (1 in build_reg_33).
            infra = _read(self.build / "infrastructure_receipt.json") or {}
            entrance_ids = sorted({
                str(row.get("feature_id")) for row in infra.get("instances", [])
                if row.get("semantic_class") == "station_entrance_assembly_proposal"
                and (row.get("dimensions") or {}).get("rendered_identity_text")})
            have = len(station_rows) + len(entrance_ids)
            if have >= minimum:
                self.emit("station_identity_labels", "station_labels",
                          "met", "%d identity emissions (%d station facade, "
                          "%d entrance assemblies with identity text), "
                          "minimum %d" % (have, len(station_rows),
                                          len(entrance_ids), minimum),
                          [str(self.manifest_path),
                           str(self.build / "infrastructure_receipt.json")])
            else:
                self.emit("station_identity_labels", "station_labels",
                          "unmet:evidence_missing",
                          "%d identity emissions, minimum %d" % (
                              have, minimum),
                          [str(self.manifest_path)])
        if comp.get("station_assembly_max_span_m") is not None:
            station_rows = [r for r in self.instances
                            if self.is_station_instance(r)]
            points = []
            for row in station_rows:
                if row.get("E") is not None and row.get("N") is not None:
                    points.append((float(row["E"]), float(row["N"])))
                    continue
                transform = row.get("transform")
                if isinstance(transform, list) and len(transform) >= 14:
                    points.append((float(transform[12]), float(transform[13])))
            span = max((math.dist(a, b) for a in points for b in points),
                       default=0.0)
            limit = float(comp["station_assembly_max_span_m"])
            state = "met" if points and span <= limit \
                else "unmet:evidence_missing"
            reason = ("station member span %.3fm within %.3fm" % (span, limit)
                      if points else "station assembly member positions absent")
            self.emit("station_assembly_span", "station_span", state,
                      reason, [str(self.manifest_path)])

    def streetscape_coverage(self):
        comp = self.req.get("route_composition_requirements", {})
        table = {"street_handrail": ("fences_walls_guardrails",),
                 "street_lamp": ("poles_and_lamps",),
                 "tree": ("vegetation",),
                 "utility_pole": ("poles_and_lamps",)}
        inventory = _read(
            self.build / "route_streetscape_inventory.json")
        for module, spec in (comp.get("streetscape_route_coverage")
                             or {}).items():
            groups = table.get(module)
            if groups is None:
                self.emit(module, "streetscape_coverage",
                          "not_evaluable:missing_input",
                          "no inventory group mapping for module",
                          [str(self.req_path)])
                continue
            if inventory is None:
                self.emit(module, "streetscape_coverage",
                          "not_evaluable:missing_input",
                          "no route_streetscape_inventory.json in build",
                          [str(self.build)])
                continue
            width = spec.get("bin_m")
            minimum = spec.get("minimum_covered_fraction")
            if width is None or minimum is None:
                self.emit(module, "streetscape_coverage",
                          "not_evaluable:missing_input",
                          "requirement omits bin_m or minimum fraction",
                          [str(self.req_path)])
                continue
            rebinned = {}
            for native_bin in inventory.get("bins", []):
                slot = int(native_bin.get("from_m", 0.0) // width) \
                    if width > 0 else 0
                cell = rebinned.setdefault(
                    slot, {"observed": 0, "represented": 0})
                for group in groups:
                    stats = (native_bin.get("groups") or {}).get(group, {})
                    cell["observed"] += int(stats.get("observed", 0))
                    cell["represented"] += int(stats.get("represented", 0))
            scored = [c for c in rebinned.values() if c["observed"] > 0]
            if not scored:
                self.emit(module, "streetscape_coverage",
                          "not_evaluable:missing_input",
                          "no observed bins for mapped groups %s" % (
                              list(groups)),
                          [str(self.build /
                               "route_streetscape_inventory.json")])
                continue
            covered = sum(1 for c in scored if c["represented"] > 0)
            fraction = covered / len(scored)
            if fraction >= minimum:
                self.emit(module, "streetscape_coverage", "met",
                          "%.3f over %d bins, minimum %.3f (groups %s)" % (
                              fraction, len(scored), minimum, list(groups)),
                          [str(self.build /
                               "route_streetscape_inventory.json")])
            else:
                self.emit(module, "streetscape_coverage",
                          "unmet:evidence_missing",
                          "%.3f over %d bins, minimum %.3f (groups %s)" % (
                              fraction, len(scored), minimum, list(groups)),
                          [str(self.build /
                               "route_streetscape_inventory.json")])

    def home_start(self):
        base = _read(self.build / "classic_base_receipt.json") or {}
        marker = self.req.get("home_start_marker")
        if not marker:
            return
        point = self.req.get("home_start_point") or {}
        spawn = base.get("spawn", {})
        if spawn.get("source") == marker and marker:
            self.emit("home_start", "home_start", "met",
                      "spawn source matches marker; point recorded "
                      "as basis (%.6f, %.6f)" % (
                          point.get("lat"), point.get("lon")),
                      [str(self.build / "classic_base_receipt.json")])
        else:
            self.emit("home_start", "home_start", "unmet:evidence_missing",
                      "no spawn with source %r" % (marker,),
                      [str(self.build / "classic_base_receipt.json")])

    def input_files(self):
        doc = self.req
        bundle = self.req_path.parent

        def paths(node, prefix=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from paths(value, prefix + "/" + str(key))
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    yield from paths(value, "%s[%d]" % (prefix, i))
            elif isinstance(node, str) and (
                    node.endswith((".json", ".osm")) or "/" in node):
                yield prefix, node

        for block in ("fl4813_image_corpora", "fl4813_observation_artifacts",
                      "compiler_inputs"):
            missing, total = [], 0
            for where, ref in paths(doc.get(block)):
                total += 1
                if not (bundle / ref).is_file() and \
                        not Path(ref).is_file():
                    missing.append(ref)
            if total == 0:
                continue
            if missing:
                self.emit(block, "input_file", "unmet:evidence_missing",
                          "%d/%d references absent" % (
                              len(missing), total),
                          [str(self.req_path)])
            else:
                self.emit(block, "input_file", "met",
                          "%d/%d references present" % (total, total),
                          [str(self.req_path)])

    def run_all(self):
        self.hero_facades()
        self.minimap_labels()
        self.fixture_archetypes()
        self.front_archetypes()
        self.required_signs()
        self.required_places()
        self.infrastructure_counts()
        self.station_labels_and_span()
        self.streetscape_coverage()
        self.home_start()
        self.input_files()
        self.rows.sort(key=lambda r: (r["kind"], r["id"]))
        empty = not any(k in self.req for k in (
            "hero_facades", "minimap_labels", "required_resolved_archetypes",
            "required_signs", "route_composition_requirements",
            "home_start_marker", "fl4813_image_corpora",
            "fl4813_observation_artifacts", "compiler_inputs"))
        return {"schema": SCHEMA, "rows": self.rows,
                "counts": {"rows": len(self.rows),
                           "suspicious_empty": bool(empty)}}


def run(*, requirements: Path, build_dir: Path, plan: Path) -> dict:
    """FL-4809: shared verdict owner for the CLI and end-to-end lane."""
    gate = Gate(requirements, build_dir, plan)
    document = gate.run_all()
    document["counts"].update(gate.counts)
    return document


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirements", type=Path, required=True)
    ap.add_argument("--build-dir", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    args = ap.parse_args(argv)
    document = run(requirements=args.requirements, build_dir=args.build_dir,
                   plan=args.plan)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(
        {"schema": "y92.requirements_gate_receipt.v1",
         "inputs": {"requirements": str(args.requirements),
                    "build_dir": str(args.build_dir),
                    "plan": str(args.plan)},
         "counts": document["counts"]}, indent=1, sort_keys=True) + "\n")
    bad = sum(1 for r in document["rows"]
              if not r["state"].startswith("met"))
    suspicious = document["counts"]["suspicious_empty"]
    print("[gate] rows=%d met=%d unmet=%d not_evaluable=%d%s" % (
        len(document["rows"]), document["counts"].get("met", 0),
        document["counts"].get("unmet", 0), document["counts"].get("not_evaluable", 0),
        " SUSPICIOUS-EMPTY" if suspicious else ""))
    if bad or suspicious:
        print("[gate] refusing: unmet or unevaluated requirements present")
        return 1
    print("[gate] all requirements met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
