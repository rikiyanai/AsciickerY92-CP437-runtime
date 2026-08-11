"""glyph_sidecar.py — FL-4131 Phase 0 Python glyph sidecar parser.

Gate: glyph_sidecar_parser_python_exists

A glyph sidecar (glyph_profile) file sits alongside an .xp sprite at:
  <sprite_path>.glyph_profile.json

It declares that the accompanying .xp uses extended GlyphIds (>255) and
references the glyph manifest that defines those IDs. The sidecar is the
discriminator for the extended-glyph path at LoadSprite time (VAL-03 branch).

Sidecar contract (profile_kind = "extended_glyph_v1", Phase 0):
  {
    "sidecar_version": 1,
    "profile_kind": "extended_glyph_v1",
    "content_pack_id": "<string>",
    "glyph_manifest_hash": "<64-hex-char SHA-256>",
    "glyph_manifest_path": "<relative-to-repo-root or null>"
  }

Hard rules (fail on any violation):
  - profile_kind != "extended_glyph_v1"          → REJECTED
  - sidecar_version != 1                          → REJECTED
  - glyph_manifest_hash missing or malformed      → REJECTED
  - GlyphId > 255 in XP without a valid sidecar   → legacy VAL-03 rejection preserved
  - GLYPH_ID_NONE / GLYPH_ID_UNRESOLVED in hash  → N/A (hash is a string field)

Parity contract:
  The parse result of this Python parser must match engine/glyph_sidecar.cpp
  field-for-field on the corpus in assets/glyphs/fixtures/sidecar_parity_corpus.json.
  Gate: glyph_sidecar_parsers_contract_parity.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

SIDECAR_SUFFIX = ".glyph_profile.json"
CURRENT_SIDECAR_VERSION = 1
CURRENT_PROFILE_KIND = "extended_glyph_v1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


# ── Result types ──────────────────────────────────────────────────────────────

class GlyphSidecarError(Exception):
    """Raised when a sidecar file is invalid or rejected fail-closed."""
    pass


class GlyphSidecar:
    """Parsed, validated glyph sidecar descriptor.

    All fields are present and validated when this object is returned.
    This is the parity surface shared with engine/glyph_sidecar.h:GlyphSidecar.
    """
    __slots__ = (
        "sidecar_version",
        "profile_kind",
        "content_pack_id",
        "glyph_manifest_hash",
        "glyph_manifest_path",
        "source_path",
    )

    def __init__(
        self,
        sidecar_version: int,
        profile_kind: str,
        content_pack_id: str,
        glyph_manifest_hash: str,
        glyph_manifest_path: Optional[str],
        source_path: str,
    ):
        self.sidecar_version = sidecar_version
        self.profile_kind = profile_kind
        self.content_pack_id = content_pack_id
        self.glyph_manifest_hash = glyph_manifest_hash
        self.glyph_manifest_path = glyph_manifest_path
        self.source_path = source_path

    def to_dict(self) -> dict:
        """Return a parity-comparable dict for the corpus test runner."""
        return {
            "sidecar_version": self.sidecar_version,
            "profile_kind": self.profile_kind,
            "content_pack_id": self.content_pack_id,
            "glyph_manifest_hash": self.glyph_manifest_hash,
            "glyph_manifest_path": self.glyph_manifest_path,
        }

    def __repr__(self) -> str:
        return (
            f"GlyphSidecar(profile_kind={self.profile_kind!r}, "
            f"content_pack_id={self.content_pack_id!r}, "
            f"glyph_manifest_hash={self.glyph_manifest_hash[:8]}..., "
            f"source_path={self.source_path!r})"
        )


# ── Core API ──────────────────────────────────────────────────────────────────

def sidecar_path_for(xp_path: str) -> str:
    """Return the expected sidecar path for a given .xp sprite path."""
    return xp_path + SIDECAR_SUFFIX


def sidecar_exists(xp_path: str) -> bool:
    """Return True if a sidecar file exists alongside the given .xp path."""
    return os.path.isfile(sidecar_path_for(xp_path))


def parse_sidecar(sidecar_path: str) -> GlyphSidecar:
    """Parse and validate a sidecar JSON file. Raises GlyphSidecarError on any failure.

    Fail-closed: any invalid field, wrong version, wrong profile_kind, or
    malformed manifest_hash raises GlyphSidecarError. The caller MUST treat
    GlyphSidecarError as a hard load failure for the associated .xp file.

    This is the primary parity surface: the C engine parser in
    glyph_sidecar.cpp must produce equivalent results for every valid fixture
    and equivalent rejection for every invalid fixture.
    """
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise GlyphSidecarError(f"sidecar not found: {sidecar_path}")
    except json.JSONDecodeError as e:
        raise GlyphSidecarError(f"sidecar JSON parse error in {sidecar_path}: {e}")

    if not isinstance(raw, dict):
        raise GlyphSidecarError(f"sidecar root must be an object: {sidecar_path}")

    # ── sidecar_version ──
    sv = raw.get("sidecar_version")
    if sv is None:
        raise GlyphSidecarError(f"sidecar missing 'sidecar_version': {sidecar_path}")
    if not isinstance(sv, int) or sv != CURRENT_SIDECAR_VERSION:
        raise GlyphSidecarError(
            f"sidecar 'sidecar_version' must be {CURRENT_SIDECAR_VERSION}, got {sv!r}: {sidecar_path}"
        )

    # ── profile_kind ──
    pk = raw.get("profile_kind")
    if pk is None:
        raise GlyphSidecarError(f"sidecar missing 'profile_kind': {sidecar_path}")
    if not isinstance(pk, str) or pk != CURRENT_PROFILE_KIND:
        raise GlyphSidecarError(
            f"sidecar 'profile_kind' must be '{CURRENT_PROFILE_KIND}', got {pk!r}: {sidecar_path}. "
            f"Fail-closed: extended loader not implemented for this profile_kind until the relevant Phase ships."
        )

    # ── content_pack_id ──
    cpid = raw.get("content_pack_id")
    if cpid is None:
        raise GlyphSidecarError(f"sidecar missing 'content_pack_id': {sidecar_path}")
    if not isinstance(cpid, str) or not cpid or len(cpid) > 128:
        raise GlyphSidecarError(
            f"sidecar 'content_pack_id' must be a non-empty string <=128 chars: {sidecar_path}"
        )

    # ── glyph_manifest_hash ──
    mh = raw.get("glyph_manifest_hash")
    if mh is None:
        raise GlyphSidecarError(f"sidecar missing 'glyph_manifest_hash': {sidecar_path}")
    if not isinstance(mh, str) or not _HASH_RE.match(mh):
        raise GlyphSidecarError(
            f"sidecar 'glyph_manifest_hash' must be a 64-char lowercase hex SHA-256, got {mh!r}: {sidecar_path}"
        )

    # ── glyph_manifest_path (optional, may be null) ──
    mp = raw.get("glyph_manifest_path")
    if mp is not None and not isinstance(mp, str):
        raise GlyphSidecarError(
            f"sidecar 'glyph_manifest_path' must be a string or null: {sidecar_path}"
        )

    return GlyphSidecar(
        sidecar_version=sv,
        profile_kind=pk,
        content_pack_id=cpid,
        glyph_manifest_hash=mh,
        glyph_manifest_path=mp,
        source_path=sidecar_path,
    )


def try_parse_sidecar(sidecar_path: str) -> Optional[GlyphSidecar]:
    """Parse a sidecar file and return None on any failure (non-raising wrapper).

    Use this ONLY when a missing/invalid sidecar is acceptable (i.e., the caller
    has already confirmed that the associated .xp has no extended glyphs).
    For the VAL-03 sidecar branch, prefer parse_sidecar() to get the explicit
    error message.
    """
    try:
        return parse_sidecar(sidecar_path)
    except GlyphSidecarError:
        return None


# ── Parity test helpers ───────────────────────────────────────────────────────

def parse_corpus(corpus_path: str) -> dict:
    """Parse the parity corpus file and return it as a dict.

    The corpus is assets/glyphs/fixtures/sidecar_parity_corpus.json.
    Each entry has:
      - "id": test case ID
      - "sidecar_json": the raw sidecar JSON string to parse
      - "expect": "ok" or "error"
      - "expected_fields": dict of expected GlyphSidecar.to_dict() fields (when expect="ok")
      - "error_contains": substring expected in error message (when expect="error")
    """
    with open(corpus_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_corpus_case(case: dict, tmp_dir: str) -> tuple[bool, str]:
    """Run one corpus case. Returns (passed: bool, detail: str).

    Writes a temp sidecar file to tmp_dir for parsing (avoids the real filesystem
    coupling in parse_sidecar while keeping the parse path identical).
    """
    import tempfile

    case_id = case.get("id", "?")
    sidecar_json = case["sidecar_json"]
    expect = case["expect"]

    tmp_path = os.path.join(tmp_dir, f"case_{case_id}.xp.sidecar.json")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(sidecar_json)

    if expect == "ok":
        try:
            result = parse_sidecar(tmp_path)
            parsed = result.to_dict()
            expected = case.get("expected_fields", {})
            mismatches = []
            for key, val in expected.items():
                if parsed.get(key) != val:
                    mismatches.append(f"{key}: expected {val!r}, got {parsed.get(key)!r}")
            if mismatches:
                return False, f"[{case_id}] field mismatch: {'; '.join(mismatches)}"
            return True, f"[{case_id}] OK"
        except GlyphSidecarError as e:
            return False, f"[{case_id}] expected OK but got error: {e}"
    elif expect == "error":
        try:
            parse_sidecar(tmp_path)
            return False, f"[{case_id}] expected error but parse succeeded"
        except GlyphSidecarError as e:
            needle = case.get("error_contains", "")
            if needle and needle not in str(e):
                return False, f"[{case_id}] error missing expected substring {needle!r}: {e}"
            return True, f"[{case_id}] OK (rejected as expected)"
    else:
        return False, f"[{case_id}] unknown 'expect' value: {expect!r}"


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="FL-4131 glyph sidecar parser CLI")
    parser.add_argument("sidecar", nargs="?", help="Sidecar file to parse and print")
    parser.add_argument("--corpus", metavar="PATH", help="Run parity corpus")
    parser.add_argument("--xp", metavar="PATH", help="Check sidecar existence for an .xp path")
    args = parser.parse_args()

    if args.corpus:
        import tempfile
        corpus = parse_corpus(args.corpus)
        cases = corpus.get("cases", [])
        passed = 0
        failed = 0
        with tempfile.TemporaryDirectory() as tmp:
            for case in cases:
                ok, detail = run_corpus_case(case, tmp)
                print(detail)
                if ok:
                    passed += 1
                else:
                    failed += 1
        print(f"\n{passed} passed, {failed} failed")
        sys.exit(0 if failed == 0 else 1)

    elif args.xp:
        sp = sidecar_path_for(args.xp)
        if sidecar_exists(args.xp):
            try:
                s = parse_sidecar(sp)
                print(json.dumps(s.to_dict(), indent=2))
            except GlyphSidecarError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"No sidecar: {sp}")
        sys.exit(0)

    elif args.sidecar:
        try:
            s = parse_sidecar(args.sidecar)
            print(json.dumps(s.to_dict(), indent=2))
        except GlyphSidecarError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
