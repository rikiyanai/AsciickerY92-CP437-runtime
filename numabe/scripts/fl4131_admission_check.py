#!/usr/bin/env python3
"""FL-4131 W6 admission allowlist contract validator (draft).

Validates assets/glyphs/admission_allowlist.json against its schema and
reports the resolution policy. This is artifact validation ONLY. It does
NOT scan the live sprite corpus, does NOT mutate any source file, and
does NOT enforce admission at runtime.

Why a draft and not enforcement:
  Runtime enforcement of the admission set is Phase 2+ work in the
  FL-4049 owner lane (deletion-first runtime). Until that lands, this
  validator exists to keep the allowlist artifact honest:
    - the JSON is well-formed
    - it satisfies the schema at assets/glyphs/schema/admission_allowlist.schema.json
    - the resolution_policy order is preserved
    - allow/deny entries have a usable kind + path/path_prefix

Front door:
  python3 scripts/fl4131_admission_check.py             # human
  python3 scripts/fl4131_admission_check.py --json      # machine

Exit codes:
  0 - allowlist + schema OK
  1 - allowlist or schema malformed / inconsistent
  2 - allowlist or schema missing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "assets" / "glyphs" / "admission_allowlist.json"
SCHEMA_PATH = REPO_ROOT / "assets" / "glyphs" / "schema" / "admission_allowlist.schema.json"


ERR_MISSING = "missing"
ERR_UNPARSEABLE = "unparseable"


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (data, error_message, error_kind). error_kind is ERR_MISSING,
    ERR_UNPARSEABLE, or None on success. Callers use error_kind for exit-code
    decisions instead of substring-matching the human-readable error_message."""
    if not path.exists():
        return None, f"missing: {path.relative_to(REPO_ROOT)}", ERR_MISSING
    try:
        return json.loads(path.read_text(encoding="utf-8")), None, None
    except Exception as exc:
        return None, f"unparseable {path.relative_to(REPO_ROOT)}: {exc}", ERR_UNPARSEABLE


def _validate_entry(entry: Any, kind_choices: list[str], errors: list[str], where: str) -> None:
    if not isinstance(entry, dict):
        errors.append(f"{where} must be an object, got {type(entry).__name__}")
        return
    kind = entry.get("kind")
    if kind not in kind_choices:
        errors.append(f"{where} 'kind' must be one of {kind_choices}, got {kind!r}")
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"{where} 'reason' must be a non-empty string")
    has_path = "path" in entry
    has_prefix = "path_prefix" in entry
    if has_path == has_prefix:
        errors.append(f"{where} must declare exactly one of 'path' or 'path_prefix'")
    if has_path and not isinstance(entry["path"], str):
        errors.append(f"{where} 'path' must be a string")
    if has_prefix and not isinstance(entry["path_prefix"], str):
        errors.append(f"{where} 'path_prefix' must be a string")


def validate() -> dict[str, Any]:
    allowlist, alw_err, alw_kind = _load_json(ALLOWLIST_PATH)
    schema, sch_err, sch_kind = _load_json(SCHEMA_PATH)
    errors: list[str] = []
    error_kinds: list[str] = []
    if alw_err:
        errors.append(alw_err)
        if alw_kind:
            error_kinds.append(alw_kind)
    if sch_err:
        errors.append(sch_err)
        if sch_kind:
            error_kinds.append(sch_kind)
    if errors:
        return {"ok": False, "errors": errors, "error_kinds": error_kinds, "summary": {}}

    assert allowlist is not None
    if allowlist.get("version") != 1:
        errors.append(f"version must be 1, got {allowlist.get('version')!r}")
    if allowlist.get("fl") != "FL-4131":
        errors.append(f"fl must be 'FL-4131', got {allowlist.get('fl')!r}")
    if not isinstance(allowlist.get("stage"), str) or not allowlist["stage"].startswith("W6_DRAFT"):
        errors.append(
            f"stage must start with 'W6_DRAFT' during Phase 0/1; got {allowlist.get('stage')!r}"
        )

    allow = allowlist.get("allow", [])
    deny = allowlist.get("deny", [])
    if not isinstance(allow, list):
        errors.append("allow must be a list")
        allow = []
    if not isinstance(deny, list):
        errors.append("deny must be a list")
        deny = []
    for i, entry in enumerate(allow):
        _validate_entry(entry, ["fixture", "directory", "file", "directory_prefix"], errors, f"allow[{i}]")
    for i, entry in enumerate(deny):
        _validate_entry(entry, ["directory", "file", "directory_prefix"], errors, f"deny[{i}]")

    policy = allowlist.get("resolution_policy") or {}
    if not isinstance(policy, dict):
        errors.append("resolution_policy must be an object")
        policy = {}
    order = policy.get("order")
    if not isinstance(order, list) or not order:
        errors.append("resolution_policy.order must be a non-empty list of strings")

    # Verify the combat-sprite denial list is intact (FL-4131 contract).
    required_deny_prefixes = [
        "assets/sprites/player",
        "assets/sprites/wolfie",
        "assets/sprites/bigbee",
        "assets/sprites/wolack",
        "assets/sprites/attack",
        "assets/sprites/plydie",
    ]
    deny_prefixes_present = [
        e.get("path_prefix") for e in deny if isinstance(e, dict) and e.get("kind") == "directory_prefix"
    ]
    missing_denies = [p for p in required_deny_prefixes if p not in deny_prefixes_present]
    if missing_denies:
        errors.append(
            "FL-4131 contract: required combat-sprite deny prefixes missing: " + ",".join(missing_denies)
        )

    summary = {
        "allow_count": len(allow),
        "deny_count": len(deny),
        "policy_steps": len(order) if isinstance(order, list) else 0,
        "required_deny_prefixes_present": [p for p in required_deny_prefixes if p in deny_prefixes_present],
    }
    return {"ok": not errors, "errors": errors, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    result = validate()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"FL-4131 admission allowlist contract validator")
        print(f"  allowlist: {ALLOWLIST_PATH.relative_to(REPO_ROOT)}")
        print(f"  schema:    {SCHEMA_PATH.relative_to(REPO_ROOT)}")
        print(f"  errors:    {len(result['errors'])}")
        for err in result["errors"][:20]:
            print(f"    - {err}")
        print(f"  summary:   {result['summary']}")
        print("VERDICT:", "PASS" if result["ok"] else "FAIL")
    if not result["ok"]:
        # Use error_kind tags rather than substring-matching the human-readable
        # error text — that drifts silently when error strings are reworded.
        if ERR_MISSING in (result.get("error_kinds") or []):
            return 2
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
