#!/usr/bin/env python3
# [DEBUG-death-sources] Static verifier for FL-3993 bug 3b hypothesis.
#
# Walks assets/actor_visual_profiles/current/actor_visual_profiles.compiled.json,
# filters profiles where presentation_kind == 602 (DEATH), and inspects each
# layer's source_path. Flags any body / mount-rear / mount-front layer whose
# source XP path does NOT contain "death" or "die" or "corpse" — these are the
# layers that determine the corpse silhouette, and reusing an idle XP for them
# makes a death profile render as standing/idle.
#
# Exit code: 0 if no suspicious layers found, 1 otherwise.
#
# Run:
#   python3 scripts/verify_death_profile_sources.py
#   python3 scripts/verify_death_profile_sources.py --json
#   python3 scripts/verify_death_profile_sources.py --compiled <path>

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPILED = (
    REPO_ROOT
    / "assets"
    / "actor_visual_profiles"
    / "current"
    / "actor_visual_profiles.compiled.json"
)

PRESENTATION_KIND_DEATH = 602

# Roles that determine corpse silhouette. Reusing an idle XP for these makes
# the dead actor render as standing/idle. weapon/head/chest/shield layers can
# legitimately reuse non-death sources (an equipped helmet looks the same on
# a dead body as on a live one).
SILHOUETTE_ROLES = {"body", "mount_rear", "mount_front"}

# Substrings whose presence in a source_path indicates a death-family XP.
DEATH_HINTS = ("death", "die", "corpse", "dead")
# Substrings whose presence STRONGLY indicates an idle/walk XP (false-positive
# for death silhouette).
IDLE_HINTS = ("idle", "walk", "stand", "run")


def find_suspicious(compiled_path: Path):
    data = json.loads(compiled_path.read_text())
    profiles = data.get("profiles") or []
    findings = []
    for profile in profiles:
        key = profile.get("key") or {}
        if key.get("presentation_kind") != PRESENTATION_KIND_DEATH:
            continue
        profile_id = profile.get("id", "<unknown>")
        layers = profile.get("layers") or []
        suspicious_layers = []
        for layer in layers:
            role = (layer.get("role") or "").lower()
            if role not in SILHOUETTE_ROLES:
                continue
            source_path = (layer.get("source_path") or "").lower()
            if not source_path:
                suspicious_layers.append(
                    {
                        "role": role,
                        "source_path": "",
                        "reason": "empty source_path",
                    }
                )
                continue
            has_death_hint = any(h in source_path for h in DEATH_HINTS)
            has_idle_hint = any(h in source_path for h in IDLE_HINTS)
            if has_idle_hint and not has_death_hint:
                suspicious_layers.append(
                    {
                        "role": role,
                        "source_path": layer.get("source_path"),
                        "source_layer_index": layer.get("source_layer_index"),
                        "reason": "source path contains idle/walk hint, no death hint",
                    }
                )
            elif not has_death_hint:
                suspicious_layers.append(
                    {
                        "role": role,
                        "source_path": layer.get("source_path"),
                        "source_layer_index": layer.get("source_layer_index"),
                        "reason": "source path lacks death/corpse hint (silhouette role)",
                    }
                )
        if suspicious_layers:
            findings.append(
                {
                    "profile_id": profile_id,
                    "key": key,
                    "suspicious_layers": suspicious_layers,
                }
            )
    return findings, len(profiles)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compiled", type=Path, default=DEFAULT_COMPILED)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of human-readable")
    args = ap.parse_args()

    if not args.compiled.exists():
        sys.stderr.write(f"error: compiled profile JSON not found at {args.compiled}\n")
        sys.exit(2)

    findings, total = find_suspicious(args.compiled)

    if args.json:
        json.dump(
            {
                "compiled_path": str(args.compiled),
                "total_profiles": total,
                "death_profile_findings": findings,
                "suspicious_death_profile_count": len(findings),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(f"verify_death_profile_sources: scanned {total} profiles")
        print(f"  filter: key.presentation_kind == {PRESENTATION_KIND_DEATH} (DEATH)")
        print(f"  silhouette roles checked: {sorted(SILHOUETTE_ROLES)}")
        print(f"  death hints: {DEATH_HINTS}")
        print(f"  idle hints: {IDLE_HINTS}")
        if not findings:
            print("RESULT: no suspicious silhouette layers in death profiles.")
        else:
            print(f"RESULT: {len(findings)} death profile(s) have silhouette layers without death sources:")
            for f in findings:
                print(f"  - {f['profile_id']}  (skin={f['key'].get('skin_id')}, mount={f['key'].get('mount_id')})")
                for layer in f["suspicious_layers"]:
                    sp = layer.get("source_path") or "<empty>"
                    print(
                        f"      role={layer['role']:<12} source={sp}#L{layer.get('source_layer_index')} -- {layer['reason']}"
                    )
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
