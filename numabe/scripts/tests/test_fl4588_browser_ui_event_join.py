from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RECORDER = ROOT / "web/asciiid-authoring-shell/src/uiEventRecorder.ts"
TRANSPORT = ROOT / "web/asciiid-authoring-shell/src/transport.ts"
APP = ROOT / "web/asciiid-authoring-shell/src/App.tsx"
TOOLBAR = ROOT / "web/asciiid-authoring-shell/src/Toolbar.tsx"


def test_browser_recorder_joins_registry_control_event_to_typed_action_identity():
    source = RECORDER.read_text(encoding="utf-8")

    assert '"[data-leaf-id][data-control-uid]"' in source
    assert 'schema: "asciiid.ui_event.v1"' in source
    assert '`${NATIVE_ORIGIN}/api/v1/event.record`' in source
    assert 'is_trusted: event.isTrusted' in source
    assert "allocateSequenceInteractionId" in source
    assert "pendingInteractions" in source
    assert "consumePhysicalInteractionId" in source
    assert "consumeSequenceInteractionId" in source
    owner = (ROOT / "web/asciiid-authoring-shell/src/interactionSequenceOwner.ts").read_text(encoding="utf-8")
    assert "allocateSequenceInteractionId" in owner
    assert "return existing.interactionId" in owner


def test_browser_recorder_is_observation_only_and_fails_proof_open():
    source = RECORDER.read_text(encoding="utf-8")

    assert "/api/v1/action.commit" not in source
    assert "Product input must remain usable when observation fails" in source
    assert "keeps the proof package PROOF_OPEN" in source


def test_runtime_intent_owns_terrain_identity_and_retired_committers_are_absent():
    source = TRANSPORT.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert '"event.read", "event.record"' in source
    # App owns the sole capture-phase DOM observation. Transport consumes raw
    # physical facts through RuntimeIntentOwner and must not import a recorder.
    assert 'from "./uiEventRecorder"' in app
    assert 'from "./uiEventRecorder"' not in source
    assert "const interactionId = crypto.randomUUID();" not in source
    assert "submitRuntimePhysicalIntent" in source
    assert 'schema: "asciiid.runtime_physical_intent.v1"' in source
    assert "observed_witness: snapshot.runtime_witness" in source
    for retired in (
        "commitTerrainMaterialPaint",
        "commitTerrainSculptPaint",
        "commitTerrainSculptBlurSharpen",
        "commitTerrainDiagonalFlip",
        "commitTerrainProbeHeight",
        "commitTerrainPatchTopology",
        "commitTerrainMaterialEyedropper",
        "commitMaterialElevationPaint",
        "commitMaterialElevationEyedropper",
    ):
        assert retired not in source


def test_live_shell_capture_boundary_and_toolbar_expose_registry_identity():
    app = APP.read_text(encoding="utf-8")
    toolbar = TOOLBAR.read_text(encoding="utf-8")

    assert 'from "./uiEventRecorder"' in app
    assert "capturePhysicalUiEvent(sessionId, event.nativeEvent)" in app
    for handler in (
        "onClickCapture",
        "onChangeCapture",
        "onInputCapture",
        "onPointerUpCapture",
        "onKeyDownCapture",
        "onWheelCapture",
        "onFocusCapture",
    ):
        assert handler in app
    assert "data-leaf-id={leaf?.leaf_id}" in toolbar
    assert "data-control-uid={leaf?.control_uid}" in toolbar
