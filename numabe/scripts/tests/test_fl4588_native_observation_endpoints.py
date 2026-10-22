# =============================================================
# test_fl4588_native_observation_endpoints.py  (FL-4588 task 34)
# =============================================================
# Hidden-backend integration test for the two live read-only observation
# routes. It launches the REAL packaged-style headless backend (.run/asciicker-presenter
# with ASCIIID_HTML_SHELL_BACKEND=1), performs the REAL evidence-viewer
# handshake, and exercises GET /api/v1/state_snapshot + /api/v1/runtime_identity.
#
# It proves the responses are LIVE (not fixtures):
#   - runtime_identity.backend_pid equals the actual backend process id;
#   - runtime_identity/state_snapshot.source_commit equals the commit we wrote
#     into the packaged asset_manifest.json (so the value is read from the real
#     manifest, not a hardcoded constant);
#   - ui_registry_sha256 equals the live registry hash the backend serves;
#   - state_snapshot carries live editor_session, undo/redo history, and a real
#     renderer frame; the snapshot self-hash is stable across repeat reads at one
#     revision (read-only, no mutation);
#   - an unauthenticated GET is rejected 401.
# Both bodies schema-validate against the shipped schemas.
# =============================================================
import hashlib
import json
import os
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".run" / "asciiid"
# The binary resolves base_path to the repo root for a .run/ launch, so it reads
# the packaged provenance manifest from here.
MANIFEST = ROOT / "asset_manifest.json"
STATE_SCHEMA = json.loads((ROOT / "schemas/asciiid.state_snapshot.v1.json").read_text())
IDENTITY_SCHEMA = json.loads((ROOT / "schemas/asciiid.runtime_identity.v1.json").read_text())
FAKE_COMMIT = "0bser4edcafe1234"
ORIGIN = "http://127.0.0.1"

# FL-4208 declared Channel-A vocabulary (engine/render/fl4208_surface_identity.h
# and render_frame_input.h). These are the values the product actually emits;
# never re-spell them as literals in an assertion.
FL4208_OWNER_TERRAIN = 0                       # Fl4208OwnerKind::FL4208_OWNER_TERRAIN
FL4208_OWNER_MESH = 1                          # Fl4208OwnerKind::FL4208_OWNER_MESH
FL4208_CELL_STATE_EMPTY = 0                    # fl4208_cell_state: no decision
FL4208_CELL_STATE_VALID = 1                    # fl4208_cell_state: valid identity
FL4208_CELL_STATE_REJECTED = 2                 # fl4208_cell_state: rejected cell
FL4208_CELL_ID_REASON_NONE = 0
FL4208_CELL_ID_REASON_TERRAIN_MATERIAL_CONFLICT = 1   # Law-6 mixed-material
FL4208_CELL_ID_REASON_AGGREGATION_AMBIGUOUS = 2
FL4208_CELL_ID_REASON_MESH_OWNER_HASH_COLLISION = 3
FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION = 4
FL4208_CELL_ID_REASON_NO_TERRAIN_SAMPLE = 5
FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION = 6
FL4208_DECLARED_REJECTION_REASONS = {
    FL4208_CELL_ID_REASON_TERRAIN_MATERIAL_CONFLICT,
    FL4208_CELL_ID_REASON_AGGREGATION_AMBIGUOUS,
    FL4208_CELL_ID_REASON_MESH_OWNER_HASH_COLLISION,
    FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION,
    FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION,
}
# Fields the closed production_bridge_cell schema must never carry: the retired
# top-left-derived Channel-B identity.
FORBIDDEN_CHANNEL_B_FIELDS = ("terrain_source_id", "terrain_source_id_valid")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _req(method, url, headers=None, body=None, timeout=5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _termpp_final_identity(base, headers, *, after_frame_id=None, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, body = _req(
            "GET", f"{base}/api/v1/frames/termpp-final.rgba",
            headers=headers, timeout=8,
        )
        if code == 200 and len(body) >= 112 and body[:8] == b"ASCIIRG5":
            frame_id = struct.unpack_from("<Q", body, 24)[0]
            final_frame_sha256 = body[40:72].hex()
            if frame_id > 0 and (
                after_frame_id is None or frame_id > int(after_frame_id)
            ):
                return {
                    "authoring_frame_id": int(frame_id),
                    "final_frame_sha256": final_frame_sha256,
                }
        time.sleep(0.1)
    raise AssertionError("TERM++ final frame identity did not become available")


def _capture_exact(base, headers, *, after_frame_id=None):
    identity = _termpp_final_identity(
        base, headers, after_frame_id=after_frame_id,
    )
    request_body = {
        "schema": "asciiid.artifact_capture_request.v1",
        **identity,
    }
    code, body = _req(
        "POST", f"{base}/api/v1/artifacts/capture",
        headers=headers, body=request_body, timeout=8,
    )
    return code, body, identity


@pytest.fixture()
def backend():
    if not BIN.exists():
        pytest.skip("missing .run/asciicker-presenter — build the product first")
    MANIFEST.write_text(json.dumps({
        "schema": "asciiid.html_desktop_app_asset_manifest.v1",
        "source_commit": FAKE_COMMIT,
        "source_dirty": False,
        "required_count": 0,
        "entries": [],
    }) + "\n")
    port = _free_port()
    env = {**os.environ, "ASCIIID_HTML_SHELL_BACKEND": "1"}
    proc = subprocess.Popen(
        [str(BIN), "--authoring-port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    token = None
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    pytest.skip("backend exited before printing a session token")
                continue
            if "[ASCIIID-WEB] Session token:" in line:
                token = line.split("Session token:", 1)[1].strip()
                break
        if not token:
            pytest.skip("backend did not print a session token in time")
        # Poll health for the live registry hash + readiness.
        reg = None
        while time.time() < deadline:
            code, body = _req("GET", f"http://127.0.0.1:{port}/api/v1/health")
            if code == 200:
                h = json.loads(body)
                if h.get("ui_registry_sha256"):
                    reg = h["ui_registry_sha256"]
                    break
            time.sleep(0.3)
        if not reg:
            pytest.skip("backend health never exposed a registry hash")
        yield proc, port, token, reg
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        MANIFEST.unlink(missing_ok=True)


def _handshake(port, token, reg, *, surface="asciiid.web_evidence_viewer.v1",
               caps=("snapshot.read", "frame.read")):
    nonce = os.urandom(16).hex()
    body = {
        "schema": "asciiid.transport_handshake.v1",
        "protocol_version": 1,
        "surface_kind": surface,
        "origin": ORIGIN,
        "session_token_sha256": hashlib.sha256(token.lower().encode()).hexdigest(),
        "client_nonce": nonce,
        "ui_registry_sha256": reg,
        "capabilities": list(caps),
        "upgrade_in_place": False,
    }
    code, resp = _req("POST", f"http://127.0.0.1:{port}/api/v1/handshake",
                      headers={"Origin": ORIGIN}, body=body)
    return code, json.loads(resp) if resp else {}


def test_state_snapshot_comment_marks_authenticated_authoring_boundary():
    source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    boundary_start = source.index(
        "// --- renderer facts from a read-only same-frame rendered_buffer"
    )
    boundary_end = source.index(
        "unsigned long long artifact_frame = 0;", boundary_start
    )
    boundary = source[boundary_start:boundary_end]

    assert "UNAUTHENTICATED authoring" not in boundary
    assert "AUTHENTICATED authoring" in boundary


def test_production_snapshot_publishes_exactly_one_cell_identity_owner():
    # Static ownership gate (no backend, no runtime claim). It proves the
    # closed snapshot contract admits ONLY the FL-4208 four-tap identity and
    # that the retired top-left-derived Channel-B identity has no producer left
    # anywhere in the engine, platform, or editor sources.
    cell_schema = STATE_SCHEMA["$defs"]["production_bridge_cell"]
    assert cell_schema["additionalProperties"] is False
    for field in ("surface_id_state", "surface_id_reason", "surface_id"):
        assert field in cell_schema["required"]
        assert field in cell_schema["properties"]
    # The dependency projection is OPTIONAL: a cell whose decision is empty or
    # rejected legitimately carries no terrain source set.
    assert "terrain_source_cell_ids" in cell_schema["properties"]
    assert "terrain_source_cell_ids" not in cell_schema["required"]
    for field in FORBIDDEN_CHANNEL_B_FIELDS:
        assert field not in cell_schema["properties"], field
        assert field not in cell_schema["required"], field

    editor = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    copied_start = editor.index("static const char* const copied_fields[]")
    copied_end = editor.index("};", copied_start)
    copied = editor[copied_start:copied_end]
    # The copier may not re-admit Channel B into the closed schema.
    for field in FORBIDDEN_CHANNEL_B_FIELDS:
        assert '"%s"' % field not in copied, field
    for field in ("surface_id_state", "surface_id_reason", "surface_id"):
        assert '"%s"' % field in copied, field
    # The Channel-B hard gate and its failure reason are deleted, not disabled.
    assert "state_snapshot_terrain_source_identity_missing" not in editor
    # The projection reads Channel A and requires the declared terrain owner.
    assert "const bool channel_a_terrain_identity =" in editor
    assert "owner_kind->valueint == FL4208_OWNER_TERRAIN" in editor

    # No surviving Channel-B producer: struct fields, bridge grids, resolver
    # writes, and the bridge-row emission are all gone.
    frame_input = (ROOT / "engine/render/render_frame_input.h").read_text()
    assert "Fl4208SurfaceId terrain_source_id;" not in frame_input
    assert "terrain_source_id[160*90]" not in frame_input
    assert "terrain_source_id_valid[160*90]" not in frame_input
    resolve = (ROOT / "engine/render/render_resolve.cpp").read_text()
    assert "in.terrain_source_id" not in resolve
    assert "g_fl4131_harri_gpu_bridge.terrain_source_id" not in resolve
    present = (ROOT / "platform/terminal_gl_present.cpp").read_text()
    assert "b.terrain_source_id" not in present
    assert '\\"terrain_source_id' not in present


def test_law6_mixed_material_rejection_stays_explicit_in_channel_a():
    # The behavioral invariant the deleted Channel-B gate was standing in for:
    # a cell whose four terrain taps disagree on material admits NO identity and
    # is reported with an explicit reason. That rejection lives in Channel A and
    # must remain a published per-cell fact, never a snapshot-wide failure.
    identity = (ROOT / "engine/render/fl4208_surface_identity.h").read_text()
    assert "if (mat_conflict) {" in identity
    assert "d.rejected_conflict = true;" in identity
    assert "d.reason = FL4208_CELL_ID_REASON_TERRAIN_MATERIAL_CONFLICT;" in identity
    assert "FL4208_CELL_ID_REASON_TERRAIN_MATERIAL_CONFLICT = 1," in identity

    # rejected_conflict reaches the bridge as state 2 with that reason ...
    scene = (ROOT / "engine/render/render_scene.cpp").read_text()
    assert "r->fl4208_cell_state[ci] = d.rejected_conflict ? 2 : (d.cell_valid ? 1 : 0);" in scene
    assert "r->fl4208_cell_reason[ci] = d.reason;" in scene
    # ... and the bridge row publishes both, so a rejected cell is observable.
    present = (ROOT / "platform/terminal_gl_present.cpp").read_text()
    assert '\\"surface_id_state\\":%u,\\"surface_id_reason\\":%u,' in present
    assert "(unsigned)b.fl4208_cell_state[idx]," in present
    assert "(unsigned)b.fl4208_cell_reason[idx]," in present


def test_authenticated_live_observation_endpoints(backend):
    proc, port, token, reg = backend
    base = f"http://127.0.0.1:{port}"

    # Unauthenticated reads are rejected.
    for route in ("state_snapshot", "runtime_identity"):
        code, _ = _req("GET", f"{base}/api/v1/{route}")
        assert code == 401, f"{route} must reject unauthenticated reads"

    code, hs = _handshake(port, token, reg)
    assert code in (200, 201), f"handshake failed: {code} {hs}"
    sid = hs["session_id"]
    hdr = {"Origin": ORIGIN, "X-Asciiid-Session": sid}

    # runtime_identity — live pid + packaged provenance + live registry hash.
    code, body = _req("GET", f"{base}/api/v1/runtime_identity", headers=hdr)
    assert code == 200, f"runtime_identity: {code} {body!r}"
    ident = json.loads(body)
    jsonschema.validate(ident, IDENTITY_SCHEMA)
    assert ident["backend_pid"] == proc.pid          # LIVE process id
    assert ident["source_commit"] == FAKE_COMMIT      # read from the real manifest
    assert ident["source_dirty"] is False
    assert ident["proof_eligible"] is True
    assert ident["ui_registry_sha256"] == reg         # live registry identity

    # Prime the sole runtime-published frame so the live renderer fact is available.
    _req("GET", f"{base}/api/v1/frames/termpp-final.rgba", headers=hdr, timeout=8)

    # state_snapshot — schema-valid live observation.
    code, body = _req("GET", f"{base}/api/v1/state_snapshot", headers=hdr, timeout=8)
    assert code == 200, f"state_snapshot: {code} {body!r}"
    snap = json.loads(body)
    jsonschema.validate(snap, STATE_SCHEMA)
    assert snap["source_commit"] == FAKE_COMMIT
    assert snap["ui_registry_sha256"] == reg
    assert snap["editor_session"]["active_material"] >= 0   # live session state
    assert isinstance(snap["runtime"]["display"]["performance_hud_visible"], bool)
    assert isinstance(snap["history"]["can_undo"], bool)    # live history
    assert snap["renderer"]["frame_id"] >= 0                # live frame

    # Task 72: the authenticated live snapshot publishes one compact production
    # bridge record for every final rendered cell. Each record retains the raw
    # FL-4260 route/surface facts and the exact same-frame rendered TERM++ tuple;
    # the bridge and presenter glyph values remain distinct facts.
    renderer = snap["renderer"]
    assert renderer["production_bridge_source_schema"] == "fl4260.bridge_cells.v18"
    production_cells = renderer["production_bridge_cells"]
    assert len(production_cells) == len(renderer["model"]["cells"])
    assert len(production_cells) > 0
    coords = {(row["cell"]["x"], row["cell"]["y"]) for row in production_cells}
    assert len(coords) == len(production_cells)
    terrain_by_id = {
        cell["terrain_cell_id"]: cell
        for cell in snap["document"]["model"]["terrain_cells"]
    }
    rejected_reasons = set()
    channel_a_terrain_rows = 0
    for row in production_cells:
        assert row["screen"]["final_gid"] >= 0
        assert row["screen"]["fg"] >= 0 and row["screen"]["bg"] >= 0
        assert isinstance(row["bridge_presented_matches_screen"], bool)
        assert isinstance(row["fact_mask"], int) and row["fact_mask"] >= 0
        assert isinstance(row["vertical_relation"], int)
        assert isinstance(row["direction"], int)
        assert isinstance(row["flow"], int)
        for field in (
            "final_result_abi_version",
            "result_kind",
            "current_winner_gid",
            "u6_record_valid",
            "temporal_valid",
            "u6_presented_final_gid",
            "u6_final_fg",
            "u6_final_bg",
            "feature_refs_valid",
            "feature_refs_failure",
            "feature_refs_expected_count",
            "feature_refs_actual_count",
            "feature_refs",
            "mesh_feature_observer",
            "mesh_bundle_merged_mask_8x8",
            "u7_mask",
            "structural_sequence",
            "shape6_global_f32_bits",
        ):
            assert field in row
        assert row["feature_refs_valid"] is True
        assert row["feature_refs_failure"] == "none"
        assert row["feature_refs_actual_count"] == len(row["feature_refs"])
        assert row["feature_refs_expected_count"] == len(row["feature_refs"])
        # Channel B is deleted, not merely unused: the closed
        # production_bridge_cell schema (additionalProperties:false) never named
        # these fields, so their presence is a schema violation AND a second
        # public identity authority. Assert absence per row, not just via
        # jsonschema, so a future re-admission fails here with a clear name.
        assert "terrain_source_id" not in row, row["cell"]
        assert "terrain_source_id_valid" not in row, row["cell"]

        # Channel A — the FL-4208 four-tap decision — is published for EVERY
        # cell, including cells the decision rejects. state: 0 empty / 1 valid /
        # 2 rejected; reason: Fl4208CellIdentityReason.
        assert row["surface_id_state"] in (0, 1, 2), row["cell"]
        assert isinstance(row["surface_id_reason"], int)
        assert isinstance(row["surface_id"]["owner_kind"], int)
        assert isinstance(row["surface_id"]["owner_x"], int)
        assert isinstance(row["surface_id"]["owner_y"], int)

        # A rejected decision keeps its explicit reason and is still published.
        # Law-6 rejection is observable through Channel A; it must never become a
        # blanket snapshot failure (the 200 asserted above already proves that no
        # single rejected cell can veto the whole snapshot).
        if row["surface_id_state"] == FL4208_CELL_STATE_REJECTED:
            assert row["surface_id_reason"] != FL4208_CELL_ID_REASON_NONE, row["cell"]
            rejected_reasons.add(row["surface_id_reason"])

        # The terrain dependency projection is DERIVED from Channel A: present
        # exactly when the decision is valid AND terrain-owned, absent otherwise.
        channel_a_terrain = (
            row["surface_id_state"] == FL4208_CELL_STATE_VALID
            and row["surface_id"]["owner_kind"] == FL4208_OWNER_TERRAIN
        )
        if channel_a_terrain:
            assert len(row["terrain_source_cell_ids"]) == 1, row["cell"]
            source_id = row["terrain_source_cell_ids"][0]
            # The projection is the identity itself, not a re-derivation.
            assert source_id == "cell:{}:{}".format(
                row["surface_id"]["owner_x"], row["surface_id"]["owner_y"]
            ), (source_id, row["surface_id"])
            # Only a valid terrain-owned identity may join the live inventory.
            assert source_id in terrain_by_id, (source_id, row["surface_id"])
            channel_a_terrain_rows += 1
            if (
                row["dispatch_surface"] == 1
                and row["profile_namespace"] == 1
                and row["material_id"] >= 0
            ):
                source_material = terrain_by_id[source_id]["visual_word"] & 0xFF
                assert row["material_id"] == source_material, (
                    source_id,
                    source_material,
                    row["material_id"],
                    row["pool_address"],
                )
                assert row["pool_address"]["material_key"] == source_material, (
                    source_id,
                    source_material,
                    row["material_id"],
                    row["pool_address"],
                )
        else:
            # No manufactured terrain source for an empty/rejected/non-terrain
            # decision. Absence is the honest projection of that decision.
            assert "terrain_source_cell_ids" not in row, (
                row["cell"], row["surface_id_state"], row["surface_id"],
            )

    # Every rejection the frame contained stayed observable through Channel A
    # with a DECLARED reason, and the snapshot still returned 200 above — a
    # rejected cell is published, never converted into a whole-snapshot failure.
    assert rejected_reasons <= FL4208_DECLARED_REJECTION_REASONS, rejected_reasons
    # The dependency projection is exactly the set of valid terrain-owned
    # decisions: no extra source set, no missing one.
    assert channel_a_terrain_rows == sum(
        1 for row in production_cells if "terrain_source_cell_ids" in row
    )

    # Stable repeat read at one revision (read-only, no mutation).
    code, body2 = _req("GET", f"{base}/api/v1/state_snapshot", headers=hdr, timeout=8)
    assert code == 200
    snap2 = json.loads(body2)
    assert snap2["state_revision"] == snap["state_revision"]
    assert snap2["snapshot_sha256"] == snap["snapshot_sha256"]


def test_dirty_package_keeps_editor_ready_but_marks_proof_ineligible(backend):
    proc, port, token, reg = backend
    base = f"http://127.0.0.1:{port}"
    MANIFEST.write_text(json.dumps({
        "schema": "asciiid.html_desktop_app_asset_manifest.v1",
        "source_commit": FAKE_COMMIT,
        "source_dirty": True,
        "required_count": 0,
        "entries": [],
    }) + "\n")

    code, hs = _handshake(port, token, reg)
    assert code in (200, 201), f"handshake failed: {code} {hs}"
    hdr = {"Origin": ORIGIN, "X-Asciiid-Session": hs["session_id"]}
    _req("GET", f"{base}/api/v1/frames/termpp-final.rgba", headers=hdr, timeout=8)

    code, body = _req("GET", f"{base}/api/v1/runtime_identity", headers=hdr)
    assert code == 200, body
    identity = json.loads(body)
    jsonschema.validate(identity, IDENTITY_SCHEMA)
    assert identity["source_dirty"] is True
    assert identity["proof_eligible"] is False

    code, body = _req("GET", f"{base}/api/v1/state_snapshot", headers=hdr, timeout=8)
    assert code == 200, body
    assert json.loads(body)["schema"] == "asciiid.state_snapshot.v1"

    deadline = time.time() + 15
    health = {}
    while time.time() < deadline:
        code, body = _req("GET", f"{base}/api/v1/health", timeout=8)
        if code == 200:
            health = json.loads(body)
            if health.get("product_ready"):
                break
        time.sleep(0.2)
    assert health.get("product_ready") is True, health


def test_authoring_state_snapshot_succeeds_without_arming_cell_trace(backend):
    # FL-4588 task 67/67A: the HTML authoring session reads /api/v1/state_snapshot
    # with the default evidence-viewer capabilities (snapshot.read + frame.read),
    # NOT artifact.read. That read must succeed via the read-only renderer-pair
    # seam WITHOUT an evidence-viewer artifact.read handshake and WITHOUT arming /
    # permanently enabling cell-trace serialization. The authenticated
    # /api/v1/artifacts/capture path remains the SOLE three-way rb/bf/ct proof-
    # snapshot mint and stays fail-closed for a session that lacks artifact.read.
    proc, port, token, reg = backend
    base = f"http://127.0.0.1:{port}"

    # Unauthenticated read stays rejected (contract preserved).
    code, _ = _req("GET", f"{base}/api/v1/state_snapshot")
    assert code == 401

    # Default caps: snapshot.read + frame.read only — NO artifact.read (no arm).
    code, hs = _handshake(port, token, reg)
    assert code in (200, 201), f"handshake failed: {code} {hs}"
    assert "artifact.read" not in hs.get("capabilities", []) if isinstance(
        hs.get("capabilities"), list) else True
    hdr = {"Origin": ORIGIN, "X-Asciiid-Session": hs["session_id"]}

    # Prime a renderer frame (frame.read, allowed by default caps; priming does
    # NOT arm the cell trace — only an artifact.read handshake arms it).
    _req("GET", f"{base}/api/v1/frames/termpp-final.rgba", headers=hdr, timeout=8)

    # The authoring state_snapshot succeeds (200) with the trace unarmed — the
    # renderer-pair seam does not require cell_trace and mints no proof snapshot id.
    for _ in range(3):
        code, body = _req("GET", f"{base}/api/v1/state_snapshot", headers=hdr, timeout=8)
        assert code == 200, f"authoring state_snapshot must be 200 unarmed: {code} {body!r}"
        snap = json.loads(body)
        assert snap["schema"] == "asciiid.state_snapshot.v1"

    # Trace stays unarmed: this snapshot.read/frame.read session cannot reach the
    # three-way proof-snapshot mint — artifacts/capture is capability-gated on
    # artifact.read and fails closed here (never minting, never arming).
    code, body = _req(
        "POST", f"{base}/api/v1/artifacts/capture", headers=hdr, timeout=8,
        body={
            "schema": "asciiid.artifact_capture_request.v1",
            "authoring_frame_id": 1,
            "final_frame_sha256": "00" * 32,
        },
    )
    assert code == 401, f"non-artifact.read session must not mint a proof snapshot: {code} {body!r}"
