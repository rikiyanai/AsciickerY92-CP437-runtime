# Ad hoc script: FL-4131 Step 1: freeze authored source/content identity at HEAD 6b17ce304 for the 11-step web/runtime E2E proof chain
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4131 Step 1: freeze authored source + content identity at current HEAD.

Records:
- HEAD SHA + commit message
- Dirty worktree file list with content hashes
- Authored content hashes (a3d map + sidecar + manifest)
- Binary mtimes + sha256 for .run/asciiid + .run/game
- Editor + engine source file hashes for FL-4131-critical paths
- Receipt written under docs/research/ascii/verification/fl4131/phase_d/<DATE>/source_identity_freeze/
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def sh(cmd, **kw):
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, **kw)


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stat_or_none(path: Path):
    if not path.exists():
        return None
    s = path.stat()
    return {"size": s.st_size, "mtime_unix": s.st_mtime}


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = REPO / "docs/research/ascii/verification/fl4131/phase_d" / today / "source_identity_freeze"
    out_dir.mkdir(parents=True, exist_ok=True)

    head_sha = sh(["git", "rev-parse", "HEAD"]).stdout.strip()
    head_msg = sh(["git", "log", "-1", "--format=%s"]).stdout.strip()
    head_iso = sh(["git", "log", "-1", "--format=%cI"]).stdout.strip()

    # NOTE: do NOT .strip() before splitlines; porcelain lines can begin with a
    # significant space (e.g., " M path" = worktree-modified).
    status = sh(["git", "status", "--porcelain"]).stdout.splitlines()
    dirty = []
    for line in status:
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if path.startswith("??"):
            continue
        # path may contain "->", handle rename
        if " -> " in path:
            path = path.split(" -> ")[-1]
        p = REPO / path
        is_dir = p.is_dir() if p.exists() else False
        dirty.append({
            "code": code,
            "path": path,
            "exists": p.exists(),
            "is_dir": is_dir,
            "sha256": sha256_file(p) if (p.exists() and not is_dir) else ("DIR" if is_dir else "MISSING"),
            "size": p.stat().st_size if (p.exists() and not is_dir) else None,
        })

    # FL-4131 critical sources (editor + engine glyph paths + manifest compiler).
    sources = [
        "editor/asciiid.cpp",
        "engine/render/render_resolve.cpp",
        "engine/material_glyph_plane.h",
        "engine/material_glyph_plane.cpp",
        "engine/glyph_sidecar.h",
        "engine/glyph_sidecar.cpp",
        "engine/glyph_id.h",
        "engine/sprite.cpp",
        "platform/term.h",
        "platform/terminal.cpp",
        "platform/terminal_gl_present.cpp",
        "scripts/compile_glyph_manifest.py",
        "scripts/glyph_sidecar.py",
        "build-web.sh",
        "makefile_game_mac",
        "makefile_asciiid_mac",
    ]
    src_hashes = {p: sha256_file(REPO / p) for p in sources}

    # Authored content.
    authored = {
        ".run/asciiid": {"sha256": sha256_file(REPO / ".run/asciiid"), "stat": stat_or_none(REPO / ".run/asciiid")},
        ".run/game": {"sha256": sha256_file(REPO / ".run/game"), "stat": stat_or_none(REPO / ".run/game")},
        ".run/fl4131_asciiid_cdp_all_presets.a3d": {
            "sha256": sha256_file(REPO / ".run/fl4131_asciiid_cdp_all_presets.a3d"),
            "stat": stat_or_none(REPO / ".run/fl4131_asciiid_cdp_all_presets.a3d"),
        },
        ".run/fl4131_asciiid_cdp_all_presets.a3d.glyph_profile.json": {
            "sha256": sha256_file(REPO / ".run/fl4131_asciiid_cdp_all_presets.a3d.glyph_profile.json"),
            "stat": stat_or_none(REPO / ".run/fl4131_asciiid_cdp_all_presets.a3d.glyph_profile.json"),
        },
    }

    # Manifest + content-pack identity.
    # The truth surface: schema + atlases + admission allowlist + generated coverage + fixtures.
    content_paths = []
    content_paths += list((REPO / "assets/glyphs/schema").rglob("*.json"))
    content_paths += list((REPO / "assets/glyphs/atlases").rglob("*.json"))
    content_paths += list((REPO / "assets/glyphs/generated").rglob("*.json"))
    content_paths += [REPO / "assets/glyphs/admission_allowlist.json"]
    # Fixtures that are part of the lane proof contract.
    fixture_paths = [
        REPO / "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
        REPO / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json",
        REPO / "assets/glyphs/fixtures/legacy_cp437_baseline.json",
        REPO / "assets/glyphs/fixtures/multiplayer_unknown_glyph_fallback.json",
        REPO / "assets/glyphs/fixtures/material_glyph_sidecar_example.json",
        REPO / "assets/glyphs/fixtures/sidecar_parity_corpus.json",
        REPO / "assets/glyphs/fixtures/terrain_shade_perf_baseline.json",
    ]
    manifest_hashes = {}
    for p in content_paths:
        if p.exists() and p.is_file():
            manifest_hashes[str(p.relative_to(REPO))] = sha256_file(p)
    fixture_hashes = {}
    for p in fixture_paths:
        if p.exists() and p.is_file():
            fixture_hashes[str(p.relative_to(REPO))] = sha256_file(p)

    receipt = {
        "schema": "fl4131_source_identity_freeze.v1",
        "fl": "FL-4131",
        "step": 1,
        "step_name": "freeze_source_content_identity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": {
            "sha": head_sha,
            "subject": head_msg,
            "committed_at": head_iso,
        },
        "worktree": {
            "is_clean": len(dirty) == 0,
            "dirty_tracked_files": dirty,
            "dirty_file_count": len(dirty),
        },
        "fl4131_critical_source_hashes": src_hashes,
        "authored_content": authored,
        "manifest_files": manifest_hashes,
        "fixture_files": fixture_hashes,
        "scope_acknowledged": {
            "binary_asciiid_built_after_dc98bd9_coverage_fix": True,
            "binary_game_built_before_engine_lane_commits": True,
            "binary_game_must_be_rebuilt_at_step_4": True,
            "web_binary_must_be_rebuilt_at_step_5": True,
        },
        "fl4137_dirty_files_acknowledged": [
            d for d in dirty
            if d["path"] in {
                "assets/glyphs/fixtures/legacy_cp437_baseline.json",
                "engine/game_input.cpp",
            }
        ],
    }

    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"[freeze] HEAD: {head_sha}")
    print(f"[freeze] dirty tracked files: {len(dirty)}")
    print(f"[freeze] manifest files: {len(manifest_hashes)}")
    print(f"[freeze] receipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
