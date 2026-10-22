#!/usr/bin/env python3
"""Show ActorVisualProfile/hash identity for bundled XP sprite packs.

This script is intentionally a status/verification frontend over the existing
ActorVisualProfile contract. It does not load loose runtime mods.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import _color_enabled  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURRENT_DIR = REPO_ROOT / "assets" / "actor_visual_profiles" / "current"
DEFAULT_STAGING_DIR = REPO_ROOT / "assets" / "actor_visual_profiles" / "current"
DEFAULT_WEB_DIR = REPO_ROOT / ".web"


@dataclass(frozen=True)
class BundleIdentity:
    name: str
    path: str
    exists: bool
    bundle_hash: str | None
    ids_lock_hash: str | None
    actor_visual_profiles_sha256: str | None = None
    compile_report_sha256: str | None = None
    source: str = "compile_report"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
            "bundle_hash": self.bundle_hash,
            "ids_lock_hash": self.ids_lock_hash,
            "actor_visual_profiles_sha256": self.actor_visual_profiles_sha256,
            "compile_report_sha256": self.compile_report_sha256,
            "source": self.source,
        }


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _compiled_identity(name: str, bundle_dir: Path) -> BundleIdentity:
    report_path = bundle_dir / "compile_report.json"
    report = _read_json(report_path)
    return BundleIdentity(
        name=name,
        path=_repo_relative(bundle_dir),
        exists=bundle_dir.exists(),
        bundle_hash=str(report.get("bundle_hash")) if report and report.get("bundle_hash") else None,
        ids_lock_hash=str(report.get("ids_lock_hash")) if report and report.get("ids_lock_hash") else None,
        actor_visual_profiles_sha256=_sha256_file(bundle_dir / "actor_visual_profiles.compiled.json"),
        compile_report_sha256=_sha256_file(report_path),
    )


def _slot_manifest_identity(name: str, slot_manifest: Path) -> BundleIdentity:
    doc = _read_json(slot_manifest)
    web = doc.get("web", {}) if doc else {}
    return BundleIdentity(
        name=name,
        path=_repo_relative(slot_manifest),
        exists=slot_manifest.exists(),
        bundle_hash=str(web.get("bundle_hash")) if web.get("bundle_hash") else None,
        ids_lock_hash=str(web.get("ids_lock_hash")) if web.get("ids_lock_hash") else None,
        actor_visual_profiles_sha256=str(web.get("actor_visual_profiles_sha256")) if web.get("actor_visual_profiles_sha256") else None,
        compile_report_sha256=str(web.get("compile_report_sha256")) if web.get("compile_report_sha256") else None,
        source="slot_manifest",
    )


def collect_status(
    *,
    current_dir: Path = DEFAULT_CURRENT_DIR,
    staging_dir: Path = DEFAULT_STAGING_DIR,
    web_dir: Path = DEFAULT_WEB_DIR,
    slot_manifest: Path | None = None,
    expect_bundle_hash: str | None = None,
    expect_ids_lock_hash: str | None = None,
) -> dict[str, Any]:
    manifest_path = slot_manifest or (web_dir / "slot_manifest.json")
    identities = [
        _compiled_identity("current", current_dir),
        _compiled_identity("staging", staging_dir),
        _compiled_identity("web", web_dir / "appearance_bundle" / "current"),
        _slot_manifest_identity("slot_manifest", manifest_path),
    ]

    present_bundle_hashes = sorted({i.bundle_hash for i in identities if i.bundle_hash})
    present_ids_lock_hashes = sorted({i.ids_lock_hash for i in identities if i.ids_lock_hash})
    missing = [
        f"{i.name}.bundle_hash" for i in identities if i.exists and not i.bundle_hash
    ] + [
        f"{i.name}.ids_lock_hash" for i in identities if i.exists and not i.ids_lock_hash
    ]
    absent = [i.name for i in identities if not i.exists]

    expected_errors: list[str] = []
    if expect_bundle_hash:
        for identity in identities:
            if identity.bundle_hash and identity.bundle_hash != expect_bundle_hash:
                expected_errors.append(f"{identity.name}.bundle_hash")
    if expect_ids_lock_hash:
        for identity in identities:
            if identity.ids_lock_hash and identity.ids_lock_hash != expect_ids_lock_hash:
                expected_errors.append(f"{identity.name}.ids_lock_hash")

    match = (
        len(present_bundle_hashes) <= 1
        and len(present_ids_lock_hashes) <= 1
        and not missing
        and not expected_errors
    )
    return {
        "ok": match,
        "match": {
            "bundle_hash": len(present_bundle_hashes) <= 1,
            "ids_lock_hash": len(present_ids_lock_hashes) <= 1,
            "all_present": not missing,
            "expected": not expected_errors,
        },
        "missing": missing,
        "absent": absent,
        "expected_errors": expected_errors,
        "bundle_hashes": present_bundle_hashes,
        "ids_lock_hashes": present_ids_lock_hashes,
        "identities": [i.as_dict() for i in identities],
    }


def _use_color(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return _color_enabled()


def _color(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _short(value: str | None, style: str) -> str:
    if not value:
        return "-"
    return value if style == "full" else value[:12]


def render_status(status: dict[str, Any], *, color: str = "auto", hash_style: str = "short") -> str:
    enabled = _use_color(color)
    ok = bool(status["ok"])
    label = _color("PASS", "32", enabled) if ok else _color("FAIL", "31", enabled)
    lines = ["Bundled XP sprite packs", f"  match: {label}"]
    if status["missing"]:
        lines.append("  missing: " + _color(", ".join(status["missing"]), "33", enabled))
    if status["absent"]:
        lines.append("  absent: " + _color(", ".join(status["absent"]), "33", enabled))
    if status["expected_errors"]:
        lines.append("  expected mismatch: " + _color(", ".join(status["expected_errors"]), "31", enabled))
    lines.append("")
    for identity in status["identities"]:
        row_ok = identity["bundle_hash"] in status["bundle_hashes"][:1] and identity["ids_lock_hash"] in status["ids_lock_hashes"][:1]
        if not identity["exists"] or not identity["bundle_hash"] or not identity["ids_lock_hash"]:
            row_color = "33"
        elif row_ok and ok:
            row_color = "32"
        else:
            row_color = "31"
        name = _color(f"{identity['name']:<13}", row_color, enabled)
        lines.append(
            f"  {name} bundle={_short(identity['bundle_hash'], hash_style)} "
            f"ids={_short(identity['ids_lock_hash'], hash_style)} path={identity['path']}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show colored bundle identity for bundled XP sprite packs.")
    parser.add_argument("--current-dir", type=Path, default=DEFAULT_CURRENT_DIR)
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING_DIR)
    parser.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)
    parser.add_argument("--slot-manifest", type=Path)
    parser.add_argument("--expect-bundle-hash")
    parser.add_argument("--expect-ids-lock-hash")
    parser.add_argument("--hash-style", choices=("short", "full"), default="short")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--json", action="store_true", help="Print machine-readable status.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status = collect_status(
        current_dir=args.current_dir,
        staging_dir=args.staging_dir,
        web_dir=args.web_dir,
        slot_manifest=args.slot_manifest,
        expect_bundle_hash=args.expect_bundle_hash,
        expect_ids_lock_hash=args.expect_ids_lock_hash,
    )
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(render_status(status, color=args.color, hash_style=args.hash_style))
    return 0 if status["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
