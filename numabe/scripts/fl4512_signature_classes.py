#!/usr/bin/env python3
# =============================================================
# fl4512_signature_classes.py
# =============================================================
# FL-4512 universal research target, step 3: exact-signature
# equivalence classes.
#
# The descriptor census (fl4512.descriptor_census.v1.jsonl) gives
# every admitted glyph one exact signature string: the SS-C.10
# quantized shape6 tuple plus the full u7 record (mask8, endpoint
# count, junction cluster count). This script groups the supply by
# STRING EQUALITY of that signature — nothing else. Equality is
# reflexive, symmetric, and transitive, so these are genuine
# equivalence classes; threshold "near duplicate" clustering is
# deliberately absent because threshold similarity is not
# transitive and must never be called an equivalence.
#
# Each class gets one canonical representative: the member with
# the LOWEST GlyphId. That matches the runtime's deterministic
# tie rule (equal score -> lower GlyphId wins), so replacing any
# class member with its representative cannot change a runtime
# winner decided at this descriptor resolution.
#
# Run:  python3 scripts/fl4512_signature_classes.py
# Out:  assets/glyphs/generated/fl4512.signature_classes.v1.json
# =============================================================

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "assets/glyphs/generated"
CENSUS = GENERATED / "fl4512.descriptor_census.v1.jsonl"
CENSUS_MANIFEST = GENERATED / "fl4512.descriptor_census.v1.manifest.json"
OUT = GENERATED / "fl4512.signature_classes.v1.json"


def main() -> int:
    manifest = json.loads(CENSUS_MANIFEST.read_text())
    census_bytes = CENSUS.read_bytes()
    census_sha = hashlib.sha256(census_bytes).hexdigest()
    # Refuse to group a census that does not match its own manifest —
    # a stale or hand-edited census would silently poison every
    # downstream equivalence claim.
    if census_sha != manifest["census_sha256"]:
        raise SystemExit("census sha256 does not match its manifest — regenerate the census first")

    # signature -> sorted member GlyphIds. Insertion order does not
    # matter; members are sorted, and classes are emitted sorted by
    # their representative, so the artifact is deterministic.
    members: dict[str, list[int]] = {}
    for line in census_bytes.decode().splitlines():
        record = json.loads(line)
        members.setdefault(record["signature"], []).append(int(record["glyph_id"]))

    classes = []
    multi = 0
    largest = 0
    for signature, gids in members.items():
        gids.sort()
        if len(gids) > 1:
            multi += 1
        largest = max(largest, len(gids))
        classes.append({
            "signature": signature,
            "representative": gids[0], # lowest GlyphId — the runtime tie rule
            "size": len(gids),
            "members": gids,
        })
    classes.sort(key=lambda c: c["representative"])

    out = {
        "schema": "fl4512.signature_classes.v1",
        "equivalence": "exact string equality of fl4512.descriptor_census.v1 signatures; no thresholds",
        "representative_rule": "lowest GlyphId in the class (matches the runtime equal-score tie rule)",
        "census_sha256": census_sha,
        "class_count": len(classes),
        "multi_member_class_count": multi,
        "largest_class_size": largest,
        "member_count": sum(c["size"] for c in classes),
        "classes": classes,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":"), sort_keys=False) + "\n")
    print(f"classes: {len(classes)} (multi-member: {multi}, largest: {largest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
