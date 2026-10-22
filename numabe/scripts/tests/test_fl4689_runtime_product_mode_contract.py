#!/usr/bin/env python3
"""FL-4689: product mode precedes map intake and fails closed."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_product_mode_header_contract_compiles_and_runs() -> None:
    source = r'''
#include "runtime_product_mode.h"
int main() {
    AsciickerRuntimeProductSelectionV1 s = {
        ASCIICKER_WORLD_GEOMETRY_EUCLIDEAN_V1,
        ASCIICKER_RENDERER_CLASSIC_CP437_V1
    };
    if (!AsciickerProductSelectionSupportedV1(s)) return 1;
    if (!AsciickerMapGeometryCompatibleV1(s.world_geometry, 0)) return 2;
    s.world_geometry = ASCIICKER_WORLD_GEOMETRY_SPHERICAL_V1;
    if (!AsciickerProductSelectionSupportedV1(s)) return 3;
    if (!AsciickerMapGeometryCompatibleV1(s.world_geometry, 1)) return 4;
    if (AsciickerMapGeometryCompatibleV1(s.world_geometry, 0)) return 5;
    s.renderer = ASCIICKER_RENDERER_FL4512_UNICODE_V1;
    if (AsciickerProductSelectionSupportedV1(s)) return 6;
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="fl4689-mode-") as tmp:
        src = Path(tmp) / "mode.cpp"
        binary = Path(tmp) / "mode"
        src.write_text(source)
        compile_run = subprocess.run(
            ["c++", "-std=c++11", "-I", str(ROOT / "engine"), str(src), "-o", str(binary)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert compile_run.returncode == 0, compile_run.stderr
        run = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr


def test_game_selects_mode_before_runtime_and_passes_geometry_to_local_server() -> None:
    text = (ROOT / "engine" / "game_app.cpp").read_text()
    main = text[text.find("int main(int argc, char* argv[])"):]
    assert main.find("ConfigureRequestedProductMode(argc, argv)") < main.find("init_v8();")
    spawn = text[text.find("static bool SpawnOwnedLocalAuthoritativeServer"):]
    assert '"--world-geometry", world_geometry' in spawn
    assert "SERVER_GEOMETRY_INCOMPATIBLE" in text


def test_server_validates_map_declaration_before_loading_terrain() -> None:
    text = (ROOT / "server" / "game_svr.cpp").read_text()
    validation = text.find("AsciickerMapGeometryCompatibleV1")
    load = text.find("terrain = LoadTerrain(f)", validation)
    assert validation >= 0
    assert load > validation
    assert "MAP_GEOMETRY_INCOMPATIBLE" in text
    assert "SvrSetWorldGeometryMode" in text


def test_client_map_loaders_cannot_select_geometry() -> None:
    texts = [
        (ROOT / "engine" / "mainmenu.cpp").read_text(),
        (ROOT / "engine" / "game_render_bridge.cpp").read_text(),
        (ROOT / "engine" / "a3d_load_context.h").read_text(),
        (ROOT / "engine" / "a3d_load_context.cpp").read_text(),
    ]
    combined = "\n".join(texts)
    assert "g_loaded_a3d_curvature" not in combined
    assert "ReadA3DCurvatureKind" not in texts[0]
    assert "ReadA3DCurvatureKind" not in texts[1]


def test_asciiid_curvature_is_document_metadata_not_runtime_mode() -> None:
    text = (ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp").read_text()
    assert "g_asciiid_document_curvature_kind" in text
    assert "g_asciiid_document_curvature_present" in text
    assert "g_loaded_a3d_curvature" not in text
    assert "never selects game/runtime\n// geometry" in text


@pytest.mark.parametrize(
    ("geometry", "map_name", "expected_code", "expected_marker"),
    (
        ("euclidean", "game_map_y8.a3d", 0, "MAP_GEOMETRY_COMPATIBLE"),
        ("spherical", "game_map_y8_s3_local.a3d", 0, "MAP_GEOMETRY_COMPATIBLE"),
        ("spherical", "game_map_y8.a3d", 2, "MAP_GEOMETRY_INCOMPATIBLE"),
        ("euclidean", "game_map_y8_s3_local.a3d", 2, "MAP_GEOMETRY_INCOMPATIBLE"),
    ),
)
def test_built_server_enforces_selected_geometry_before_map_load(
    geometry: str, map_name: str, expected_code: int, expected_marker: str
) -> None:
    server = ROOT / ".run" / "server"
    if not server.is_file():
        pytest.skip("native server is not built")
    run = subprocess.run(
        [
            str(server),
            "--world-geometry", geometry,
            "--map", str(ROOT / "assets" / "a3d" / map_name),
            "--check-map-geometry",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == expected_code, run.stdout + run.stderr
    assert expected_marker in run.stdout + run.stderr
