"""FL-4512 R2: the mesh differential has one current-runtime owner.

The mesh-differential implementation used to sit at ``engine/`` while being
compiled only by the current desktop game, the current presenter, web, and
the terminal narrow adapter. This module pins the completed cut:

* one implementation plus its header under ``apps/fl4512-runtime/``;
* the old shared-root paths absent from disk;
* every current source list naming the new path and no stale root path;
* Classic and server compiling zero mesh-differential implementation;
* terminal consuming the current owner through its explicit contract;
* every C++ include consumer retargeted at the new header;
* the committed extraction manifest encoding the new paths.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "fl4512_extract_manifest.py"
MANIFEST = ROOT / (
    "docs/research/ascii/verification/fl4512/"
    "2026-09-18-fl4512-extraction-manifest.json"
)

OLD_CPP = "engine/fl4512_mesh_differential.cpp"
OLD_H = "engine/fl4512_mesh_differential.h"
NEW_CPP = "apps/fl4512-runtime/engine/fl4512_mesh_differential.cpp"
NEW_H = "apps/fl4512-runtime/engine/fl4512_mesh_differential.h"

CURRENT_SRCS_SPECS = (
    ROOT / "apps/game-fl4512/makefile_game",
    ROOT / "apps/game-fl4512/makefile_game_mac",
    ROOT / "apps/asciicker-presenter/makefile_asciiid",
    ROOT / "apps/asciicker-presenter/makefile_asciiid_mac",
    ROOT / "build-web.sh",
)
CLASSIC_SPECS = (
    ROOT / "apps/game-classic/makefile_game_classic",
    ROOT / "apps/game-classic/makefile_game_classic_mac",
)
TERMINAL_CONTRACT_MK = ROOT / "apps/terminal-runtime/terminal_runtime_sources.mk"

# The old root path as a compile token: ``engine/...`` not preceded by ``/``
# or a word character, so the new ``apps/fl4512-runtime/engine/`` path never
# matches it.
OLD_PATH_TOKEN = re.compile(
    r"(?<![/\w])engine/fl4512_mesh_differential\.(cpp|h)"
)


def _srcs_entries(makefile: Path) -> set[str]:
    lines = makefile.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("SRCS :="))
    entries: set[str] = set()
    for line in lines[start:]:
        chunk = line.split(":=", 1)[1] if ":=" in line else line
        continued = chunk.rstrip().endswith("\\")
        chunk = chunk.replace("\\", " ")
        entries.update(chunk.split())
        if not continued:
            break
    return entries


def _load_generator():
    spec = importlib.util.spec_from_file_location("fl4512_manifest", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_mesh_owner_under_current_runtime() -> None:
    assert not (ROOT / OLD_CPP).exists()
    assert not (ROOT / OLD_H).exists()
    assert (ROOT / NEW_CPP).is_file()
    # Positive header rationale: the header is co-located with its sole
    # implementation under the current-runtime root. The production include
    # consumer (render_world_pass) is current-only; terminal consumes the
    # implementation through its narrow-adapter contract without becoming a
    # second writer; Classic and server never compile the implementation.
    assert (ROOT / NEW_H).is_file()
    assert (ROOT / NEW_H).parent == (ROOT / NEW_CPP).parent


def test_current_specs_point_at_the_new_mesh_source() -> None:
    for spec in CURRENT_SRCS_SPECS:
        text = spec.read_text(encoding="utf-8")
        assert NEW_CPP in text, spec.name
        assert OLD_PATH_TOKEN.search(text) is None, spec.name


def test_classic_server_zero_terminal_consumes_current_owner() -> None:
    for spec in CLASSIC_SPECS:
        entries = _srcs_entries(spec)
        assert OLD_CPP not in entries, spec.name
        assert NEW_CPP not in entries, spec.name
    contract = TERMINAL_CONTRACT_MK.read_text(encoding="utf-8")
    assert NEW_CPP in contract
    assert OLD_PATH_TOKEN.search(contract) is None
    server_srcs = _srcs_entries(ROOT / "makefile_server")
    assert OLD_CPP not in server_srcs
    assert NEW_CPP not in server_srcs
    assert not any(
        entry.endswith("fl4512_mesh_differential.cpp") for entry in server_srcs
    )


def test_include_consumers_point_at_the_new_mesh_header() -> None:
    render_pass = (
        ROOT / "engine/render/render_world_pass.cpp"
    ).read_text(encoding="utf-8")
    assert (
        "../../apps/fl4512-runtime/engine/fl4512_mesh_differential.h"
        in render_pass
    )
    lattice = (
        ROOT / "scripts/tests/fl4512_lattice_silhouette_harness.cpp"
    ).read_text(encoding="utf-8")
    assert (
        "../../apps/fl4512-runtime/engine/fl4512_mesh_differential.h"
        in lattice
    )
    visibility = (
        ROOT / "scripts/tests/fl4512_silhouette_visibility_owner_harness.cpp"
    ).read_text(encoding="utf-8")
    assert (
        "../../apps/fl4512-runtime/engine/fl4512_mesh_differential.h"
        in visibility
    )
    assert OLD_PATH_TOKEN.search(render_pass) is None
    assert OLD_PATH_TOKEN.search(lattice) is None
    assert OLD_PATH_TOKEN.search(visibility) is None


def test_committed_manifest_encodes_the_new_mesh_owner() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_path = {entry["path"]: entry for entry in manifest["entries"]}
    for path in (NEW_CPP, NEW_H):
        assert path in manifest["files"], path
        assert by_path[path]["category"] == "current_runtime_source", path
    assert OLD_CPP not in manifest["files"]
    assert OLD_H not in manifest["files"]


def test_generator_discovers_the_new_mesh_owner() -> None:
    module = _load_generator()
    assert NEW_CPP in module.current_runtime_files()
    assert NEW_H in module.current_runtime_files()
