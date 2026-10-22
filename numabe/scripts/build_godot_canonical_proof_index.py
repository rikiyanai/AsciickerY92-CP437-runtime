#!/usr/bin/env python3
"""Build the generated Godot canonical proof index from the registry.

FL-4781: config/godot_proof_pose_registry.json remains the mutable identity
owner. docs/proofs/index.json is a generated discovery surface only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RELATIVE_PATH = Path("config") / "godot_proof_pose_registry.json"
REGISTRY_PATH = ROOT / REGISTRY_RELATIVE_PATH
INDEX_RELATIVE_PATH = Path("docs") / "proofs" / "index.json"
INDEX_PATH = ROOT / INDEX_RELATIVE_PATH
READER_PATH = ROOT / "docs" / "proofs" / "README.md"


def _load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: registry root must be an object")
    return data


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SystemExit(f"{REGISTRY_RELATIVE_PATH}: {key} must be a list of objects")
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _classification_map(data: dict[str, Any]) -> dict[str, str]:
    publication = data.get("publication_classifications", {})
    if not isinstance(publication, dict):
        return {}
    by_source = publication.get("by_source_path", {})
    if not isinstance(by_source, dict):
        return {}
    return {str(key): str(value) for key, value in by_source.items()}


def _lineage(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index_meta = data.get("canonical_proof_index", {})
    if not isinstance(index_meta, dict):
        return {}
    lineage = index_meta.get("recovered_lineage_by_identity", {})
    if not isinstance(lineage, dict):
        return {}
    return {str(key): value for key, value in lineage.items() if isinstance(value, dict)}


def _route_distinction(route: dict[str, Any], extra: dict[str, Any]) -> str:
    if "distinction" in extra:
        return str(extra["distinction"])
    status = str(route.get("status", ""))
    designation = str(route.get("designation", ""))
    if status == "rejected-conflicted":
        return "rejected-conflicted route; preserve as non-accepted historical evidence"
    if "MISSING_POSE" in designation or status == "missing-executable-pose":
        return "designated subject or target lacks an executable proof pose"
    if "NOT_OPERATOR_DESIGNATED" in designation:
        return "canonical-historical route, not operator-designated"
    if "OPERATOR_DESIGNATED" in designation:
        return "operator-designated route; coverage remains limited by status and linked evidence"
    return "registry route row; see source and status"


def build_index(data: dict[str, Any], registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    proof_rows = _rows(data, "proof_scripts")
    pose_rows = _rows(data, "poses")
    route_rows = _rows(data, "routes")
    classifications = _classification_map(data)
    lineage = _lineage(data)
    index_meta = data.get("canonical_proof_index", {})
    if not isinstance(index_meta, dict):
        index_meta = {}

    proof_scripts: list[dict[str, Any]] = []
    for row in proof_rows:
        copied = {**row,
            "id": row.get("id"),
            "kind": row.get("kind"),
            "menu_group": row.get("menu_group"),
            "source_path": row.get("source_path"),
            "fl_refs": row.get("fl_refs", []),
            "rq_refs": row.get("rq_refs", []),
            "pose_refs": row.get("pose_refs", []),
            "status": row.get("status"),
        }
        source_path = row.get("source_path")
        if isinstance(source_path, str) and source_path in classifications:
            copied["publication_classification"] = classifications[source_path]
        proof_scripts.append(copied)

    routes: list[dict[str, Any]] = []
    for row in route_rows:
        row_id = str(row.get("id", ""))
        extra = lineage.get(row_id, {})
        copied = {**row,
            "route_number": row.get("route_number"),
            "id": row.get("id"),
            "designation": row.get("designation"),
            "status": row.get("status"),
            "pose_refs": row.get("pose_refs", []),
            "source": row.get("source"),
            "distinction": _route_distinction(row, extra),
        }
        if extra:
            copied["recovered_lineage"] = extra.get("lineage", [])
        for key in (
            "player_position",
            "camera_position",
            "yaw_radians",
            "pitch_radians",
            "fov_degrees",
            "mode",
        ):
            if key in row:
                copied[key] = row[key]
        routes.append(copied)

    route_status_counts: dict[str, int] = {}
    route_designation_counts: dict[str, int] = {}
    for row in routes:
        status = str(row.get("status") or "unknown")
        designation = str(row.get("designation") or "unknown")
        route_status_counts[status] = route_status_counts.get(status, 0) + 1
        route_designation_counts[designation] = route_designation_counts.get(designation, 0) + 1

    temporal = data.get("temporal_regression_requirements", {})
    if not isinstance(temporal, dict):
        temporal = {}
    temporal_index = {
        "fl_ref": temporal.get("fl_ref"),
        "rq_ref": temporal.get("rq_ref"),
        "presentation_modes": temporal.get("presentation_modes", []),
        "presentation_mode_policy": temporal.get("presentation_mode_policy", ""),
        "route_families": temporal.get("route_families", {}),
        "sampling_axes": temporal.get("sampling_axes", []),
        "runtime_sampling_domain": temporal.get("runtime_sampling_domain", {}),
        "defect_classes": temporal.get("defect_classes", []),
        "required_witnesses": temporal.get("required_witnesses", []),
        "sensitivity_controls": temporal.get("sensitivity_controls", []),
        "acceptance_rules": temporal.get("acceptance_rules", []),
    }
    temporal_lineage = lineage.get("temporal.camera_turn_five_frame")
    if temporal_lineage:
        temporal_index["camera_turn_five_frame_lineage"] = temporal_lineage

    return {
        "version": "godot-canonical-proof-index.v1",
        "generated_from": {
            "registry_path": str(REGISTRY_RELATIVE_PATH),
            "registry_version": data.get("version"),
            "registry_owner": data.get("owner"),
            "registry_sha256": _sha256(registry_path),
        },
        "reader": index_meta.get("reader", "docs/proofs/README.md"),
        "mutable_identity_owner": data.get("owner"),
        "contract": index_meta.get(
            "contract",
            "Generated/read-only discovery surface for the registry.",
        ),
        "mode_policy": {
            "source": "config/godot_proof_pose_registry.json:temporal_regression_requirements",
            "presentation_modes": temporal_index["presentation_modes"],
            "presentation_mode_policy": temporal_index["presentation_mode_policy"],
        },
        "unresolved_retrieval": index_meta.get("unresolved_retrieval", []),
        "counts": {
            "proof_scripts": len(proof_scripts),
            "poses": len(pose_rows),
            "routes": len(routes),
            "route_status": dict(sorted(route_status_counts.items())),
            "route_designations": dict(sorted(route_designation_counts.items())),
        },
        "proof_scripts": proof_scripts,
        "poses": pose_rows,
        "routes": routes,
        "temporal_regression_requirements": temporal_index,
    }


def _encoded_index(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def render_reader(index: dict[str, Any]) -> str:
    """FL-4781: human and machine discovery share the same generated facts."""
    def cell(value: Any) -> str:
        return str(value or "—").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Godot canonical proof index", "",
        "Generated from [config/godot_proof_pose_registry.json](../../config/godot_proof_pose_registry.json).",
        "Do not edit this reader or [docs/proofs/index.json](index.json) as another inventory.",
        "Update the registry, then run `python3 scripts/build_godot_canonical_proof_index.py`.", "",
        "Check freshness: `python3 scripts/build_godot_canonical_proof_index.py --check`.",
        "Find identities and evidence: `python3 scripts/build_godot_canonical_proof_index.py --find camera_turn`.",
        "Lookup reads the current registry even if generated files are stale. No capture is launched.", "",
        "Launcher: `python3 scripts/launcher.py` → Godot Port → Proofs & Poses → Canonical proof index summary.",
        "[Publication protocol](../agent/godot-proof-publication.md) · [Registry contract](../agent/godot-proof-pose-registry.md)", "",
        "[Failure log](../FAILURE_LOG.md) · [Canonical requirements](../plans/2026-03-22-multiplayer-canonical-spec.md) · [Godot goal](../plans/GODOT_GOAL.MD)", "",
        "## Temporal proof contract", "", index["mode_policy"]["presentation_mode_policy"], "",
        "These are required coverage and registered identities, not claims of executed or accepted coverage.",
        "Missing local media does not erase a designation. Resolve gaps through linked records before asking the operator to repeat one.", "",
    ]
    temporal = index["temporal_regression_requirements"]
    for name, family in temporal["route_families"].items():
        lines.extend([f"### {name}", "", "```json", json.dumps(family, indent=2), "```", ""])
        receipt = family.get("canonical_capture_recipe", {}).get("identity_receipt")
        if receipt:
            lines.extend([f"[Historical identity receipt](../../{receipt}) — identity evidence, not an acceptance promotion.", ""])
    lines.extend([
        "## Runtime sampling domain", "", "```json",
        json.dumps(temporal["runtime_sampling_domain"], indent=2), "```", "",
        "## Detectability and traceability", "", "```json",
        json.dumps({
            "defect_classes": temporal["defect_classes"],
            "required_witnesses": temporal["required_witnesses"],
            "sensitivity_controls": temporal["sensitivity_controls"],
            "sampling_axes": temporal["sampling_axes"],
            "acceptance_rules": temporal["acceptance_rules"],
        }, indent=2), "```", "",
    ])
    lines.extend(["## Retrieval records", ""])
    for row in index["unresolved_retrieval"]:
        lines.extend([f"### {row['id']}", "", "```json", json.dumps(row, indent=2), "```", ""])
    for section, title in (("proof_scripts", "Proof scripts and gates"), ("routes", "Routes"), ("poses", "Poses and markers")):
        lines.extend([f"## {title}", "", "| Identity | Status / kind | Source / designation |", "|---|---|---|"])
        for row in index[section]:
            source = row.get("source_path")
            label = f"[{cell(source)}](../../{source})" if source else cell(row.get("designation", row.get("source", "")))
            lines.append(f"| `{row['id']}` | {cell(row.get('status', row.get('kind')))} | {label} |")
        lines.append("")
    lines.extend(["## Recovered lineage", ""])
    for row in index["routes"]:
        if row.get("recovered_lineage"):
            lines.extend([f"### {row['id']}", "", row["distinction"], "", "```json", json.dumps(row["recovered_lineage"], indent=2), "```", ""])
    lines.extend(["Use exact run/media names in the native FL lookup; line numbers are navigation hints, not immutable identities.",
                  "`python3 scripts/analyze_runs.py fl context FL-4359 --task \"<exact run, media, or proof identity>\"`", ""])
    return "\n".join(lines)


def find_identities(index: dict[str, Any], query: str) -> list[dict[str, Any]]:
    rows = [row for section in ("proof_scripts", "poses", "routes", "unresolved_retrieval") for row in index[section]]
    rows += [{"id": "temporal." + name, **row} for name, row in index["temporal_regression_requirements"]["route_families"].items()]
    def normalized(text: str) -> str:
        return text.casefold().replace("_", " ").replace("-", " ")
    needle = normalized(query)
    return [row for row in rows if needle in normalized(json.dumps(row))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if docs/proofs/index.json is stale")
    parser.add_argument("--print", action="store_true", help="print the generated index JSON")
    parser.add_argument("--find", metavar="TEXT", help="read-only lookup of current identities, protocols and evidence")
    args = parser.parse_args()

    data = _load_registry()
    index = build_index(data)
    encoded = _encoded_index(index)
    reader = render_reader(index)
    if args.find is not None:
        rows = find_identities(index, args.find)
        print(json.dumps(rows, indent=2))
        return 0 if rows else 1
    if args.print:
        print(encoded, end="")
        return 0
    if args.check:
        for path, expected in ((INDEX_PATH, encoded), (READER_PATH, reader)):
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                print(f"{path.relative_to(ROOT)}: missing/stale; run scripts/build_godot_canonical_proof_index.py")
                return 1
        print("GODOT_CANONICAL_PROOF_INDEX_OK")
        return 0
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(encoded, encoding="utf-8")
    READER_PATH.write_text(reader, encoding="utf-8")
    print(f"wrote {INDEX_RELATIVE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
