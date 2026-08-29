#!/usr/bin/env python3
"""FL-4257 Phase 1 S6 — client-side curvature intake tests.

S6 wires the local single-process client (mainmenu and the FORCE INIT
test_mode path in game_render_bridge.cpp) to read the .a3d FileHeader
curvature byte at map load time and apply it to the Renderer via
RendererSetCurvatureFromAuthoritative. Without S6, Renderer.curvature
stays absent and the spherical projection branch landed in S2/S3 is
never reached even with a spherical .a3d on disk.

These tests assert:
- a3d_load_context.h declares g_loaded_a3d_curvature_kind / _present
- a3d_load_context.cpp defines them with the canonical absent initialiser
- render.h declares RendererSetCurvatureFromAuthoritative
- render_core.cpp implements it as a thin forward to the inline setter
- mainmenu.cpp peeks at progress=0, marks presence after terrain+world
  load, and calls the wrapper after InitGame
- game_render_bridge.cpp does the same in the FORCE INIT path
- The audits remain CLEAN

Multiplayer client intake via authoritative_state network packets is
Phase 2 work; these tests are scoped to the local single-process path.
"""
from __future__ import annotations

import json as _json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_load_context_declares_curvature_globals() -> None:
    h = (REPO_ROOT / "engine" / "a3d_load_context.h").read_text()
    assert "extern unsigned char g_loaded_a3d_curvature_kind;" in h
    assert "extern unsigned char g_loaded_a3d_curvature_present;" in h
    c = (REPO_ROOT / "engine" / "a3d_load_context.cpp").read_text()
    # Definition with canonical-absent initialiser. 0xFF == ASCK_CURVATURE_KIND_NONE.
    assert "unsigned char g_loaded_a3d_curvature_kind = 0xFFu;" in c
    assert "unsigned char g_loaded_a3d_curvature_present = 0u;" in c


def test_render_h_declares_setter_wrapper() -> None:
    text = (REPO_ROOT / "engine" / "render" / "render.h").read_text()
    assert "void RendererSetCurvatureFromAuthoritative(" in text, (
        "render.h must expose the C-style wrapper so local map-load sites "
        "do not need to pull render_internal.h to call the setter"
    )


def test_render_core_implements_setter_wrapper() -> None:
    text = (REPO_ROOT / "engine" / "render" / "render_core.cpp").read_text()
    assert "RendererSetCurvatureFromAuthoritative" in text
    # Must forward to the inline setter — that keeps the strict-{0,1}
    # presence-bit contract enforced in one named owner.
    assert "r->SetCurvatureFromAuthoritative(present_wire, kind);" in text


def test_mainmenu_feeds_renderer_from_published_state() -> None:
    """FL-4257 corrective: the renderer consumes SERVER-PUBLISHED curvature
    (RSP_JOIN -> connection), never the client-side A3D peek (deleted
    wrong-direction owner, Law 2)."""
    text = (REPO_ROOT / "engine" / "mainmenu.cpp").read_text()
    assert "RendererSetCurvatureFromPublished(" in text
    assert "server->connection.curvature_present" in text
    feed = text[text.find("RendererSetCurvatureFromPublished("):]
    assert "g_loaded_a3d_curvature_present" not in feed.split(");")[0], (
        "mainmenu must not feed the renderer from the client A3D peek"
    )


def test_game_render_bridge_feeds_renderer_from_published_state() -> None:
    text = (REPO_ROOT / "engine" / "game_render_bridge.cpp").read_text()
    assert "RendererSetCurvatureFromPublished(" in text
    assert "server->connection.curvature_present" in text
    assert "RendererSetCurvatureFromAuthoritative(\n" not in text


def test_join_wire_publishes_curvature_server_to_client_only() -> None:
    text = (REPO_ROOT / "server" / "protocol" / "protocol_join.h").read_text()
    rsp = text[text.find("struct STRUCT_RSP_JOIN"):text.find("struct STRUCT_BRC_JOIN_REJECT_V2")]
    req = text[text.find("struct STRUCT_REQ_JOIN_V2"):text.find("struct STRUCT_RSP_JOIN")]
    for f in ("curvature_present", "curvature_kind", "curvature_kappa", "curvature_anchor"):
        assert f in rsp, f"RSP_JOIN must publish {f}"
        assert f not in req, f"REQ_JOIN_V2 must NOT carry {f} (client has no curvature authority)"


def test_web_join_v2_builder_matches_current_server_request_shape() -> None:
    """FL-4257 headed web proof must not stall on stale REQ_JOIN_V2 length."""
    web = (REPO_ROOT / "web" / "game_web.html").read_text()
    assert "var payload = new Uint8Array(488);" in web
    assert "writeAscii(358, 65, meta && meta.lut_hash" in web
    assert "writeAscii(423, 65, meta && meta.page_atlas_chain_hash" in web
    assert "joinV2Len" in web
    assert "new Uint8Array(358)" not in web


def test_web_rsp_join_decoder_carries_atlas_identity_to_cpp() -> None:
    web = (REPO_ROOT / "web" / "game_web.html").read_text()
    assert "decodedMsg.length >= 464" in web
    assert "readAscii(334, 65)" in web
    assert "readAscii(399, 65)" in web
    assert "'string', 'string', 'string', 'string', 'string', 'string'" in web


def test_web_rsp_join_decoder_carries_curvature_to_cpp_after_join() -> None:
    web = (REPO_ROOT / "web" / "game_web.html").read_text()
    network = (REPO_ROOT / "web" / "web_network_client.cpp").read_text()
    build = (REPO_ROOT / "build-web.sh").read_text()
    assert "var SetServerCurvatureFromJoin = null" in web
    assert "SetServerCurvatureFromJoin = Module.cwrap" in web
    assert "_SetServerCurvatureFromJoin" in build
    assert "decodedMsg.length >= 482" in web
    assert "decodedMsg[464]" in web
    assert "decodedMsg[465]" in web
    assert "getFloat32(466, true)" in web
    assert "getFloat32(470, true)" in web
    assert "getFloat32(474, true)" in web
    assert "getFloat32(478, true)" in web
    assert "SetServerCurvatureFromJoin(" in web
    assert "void SetServerCurvatureFromJoin(" in network
    assert "server->connection.curvature_present" in network
    assert "server->connection.curvature_kind" in network
    assert "server->connection.curvature_kappa" in network
    assert "server->connection.curvature_anchor[0]" in network


def test_dual_include_audit_clean() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
         "audit-no-dual-curvature-include", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = _json.loads(proc.stdout)
    assert payload["status"] == "CLEAN", payload


def test_render_ownership_audit_clean() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "analyze_runs.py"),
         "audit-render-curvature-ownership", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = _json.loads(proc.stdout)
    assert payload["status"] == "CLEAN", payload
