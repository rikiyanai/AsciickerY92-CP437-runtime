# =============================================================
# test_fl4588_document_merge_runtime_transaction.py
# =============================================================
# Backend runtime integration for the runtime-owned document transaction.
# Exercises real packaged-base-path Open, Merge, Undo, Redo, canonical
# download, repeated Open, and invalid-byte revision invariants. This is HTTP
# execution evidence, not headed physical-browser acceptance.
# =============================================================

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

from scripts.package_asciicker_presenter_html_mac import write_runtime_asset_manifest

# FL-4770: this module's HTTP harness lives in
# scripts/tests/test_fl4588_document_history_runtime.py, which commit 8e7c9e6b4
# DELETED when it retired the legacy authoring runtime surfaces. This module was
# left behind, so the import raised ModuleNotFoundError at COLLECTION time.
#
# pytest treats a collection error as fatal for the whole run: it collected 3366
# items, hit this one module, reported "Interrupted: 1 error during collection",
# and executed ZERO tests. One unrunnable module was taking down the entire
# repository static suite for every lane.
#
# This guard is deliberately the SMALLEST possible intervention. It does not
# decide the orphan's fate -- whether these two transactions should be retired
# with their harness, or reimplemented against the surviving runtime surface, is
# FL-4588/FL-4734's call, and nothing here is deleted. It only converts a fatal
# collection abort into one explicitly-reasoned skip, so the failure stays
# VISIBLE in every run instead of hiding behind an aborted suite.
try:
    from scripts.tests.test_fl4588_document_history_runtime import (
        AuthoringClient,
        HiddenAuthoringBackend,
        REDO_LEAF,
        UNDO_LEAF,
        _CONTENT_TYPE_DOCUMENT_RESULT,
        _free_loopback_port,
        _handshake,
        _port_is_listening,
        _runtime_download,
        _runtime_file_write,
        _runtime_write,
        _snapshot,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        "FL-4770: the shared authoring-runtime harness "
        "scripts/tests/test_fl4588_document_history_runtime.py was deleted by "
        f"8e7c9e6b4 and this module still imports it ({exc}). These two "
        "transactions are UNVERIFIED, not passing. FL-4588/FL-4734 owns whether "
        "they are retired with the harness or reimplemented against the "
        "surviving runtime surface.",
        allow_module_level=True,
    )


ENGINE_BIN = ROOT / ".run" / "asciiid"
OPEN_MAP = ROOT / "assets" / "a3d" / "minimal_1x1.a3d"
MERGE_MAP = ROOT / "assets" / "a3d" / "minimal_2x2.a3d"
REGISTRY = ROOT / "assets" / "asciiid" / "ui" / "asciiid.ui_registry.v1.json"


def _packaged_runtime(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    runtime_bin = runtime_dir / "asciiid"
    shutil.copy2(ENGINE_BIN, runtime_bin)
    shutil.copy2(ROOT / "asciicker.cfg", runtime_dir / "asciicker.cfg")
    shutil.copytree(ROOT / "assets", runtime_dir / "assets", copy_function=os.link)
    write_runtime_asset_manifest(runtime_dir)
    return runtime_bin


def _registry_contract() -> tuple[str, dict[str, dict]]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    interactions = {row["leaf_id"]: row for row in registry["interactions"]}
    return registry["registry_sha256"], interactions


def _assert_revisions_unchanged(before: dict, after: dict, *, label: str) -> None:
    assert after["state_revision"] == before["state_revision"], label
    assert (
        after["runtime"]["display_revision"]
        == before["runtime"]["display_revision"]
    ), label


def test_document_open_merge_undo_redo_download_runtime_transaction(
    tmp_path: Path,
) -> None:
    assert ENGINE_BIN.is_file()
    assert OPEN_MAP.is_file()
    assert MERGE_MAP.is_file()
    registry_sha, interactions = _registry_contract()
    open_target = interactions["workspace.document.open.commit"]
    merge_target = interactions["workspace.document.merge.commit"]
    save_target = interactions["workspace.document.save_as.commit"]
    assert merge_target["payload_schema"]["required"] == ["logical_name"]
    assert "bytes" not in merge_target["payload_schema"]["properties"]
    assert "path" not in merge_target["payload_schema"]["properties"]

    runtime_bin = _packaged_runtime(tmp_path)
    port = _free_loopback_port()
    backend = HiddenAuthoringBackend(port, runtime_bin)
    client = AuthoringClient(port, f"http://127.0.0.1:{_free_loopback_port()}")
    try:
        backend.start()
        health = backend.wait_ready()
        assert health["ui_registry_sha256"] == registry_sha
        _handshake(client, backend.session_token, registry_sha)

        before_open = _snapshot(client)
        open_status, opened = _runtime_file_write(
            client, open_target, before_open,
            "minimal_1x1.a3d", OPEN_MAP.read_bytes(),
        )
        assert open_status == 200 and opened.get("ok") is True, opened
        open_effect = opened["effect"]
        assert open_effect["action_kind"] == "open_document"
        assert open_effect["logical_name"] == "minimal_1x1.a3d"
        assert open_effect["canonical_bytes"] > 0
        assert "path" not in open_effect
        after_open = _snapshot(client)
        assert after_open["state_revision"] == open_effect["new_state_revision"]

        pre_invalid = _snapshot(client)
        bad_status, bad = _runtime_file_write(
            client, merge_target, pre_invalid,
            "hostile.a3d", b"NOT_AN_A3D_DOCUMENT",
        )
        assert bad_status == 422 and bad.get("ok") is False, bad
        post_invalid = _snapshot(client)
        _assert_revisions_unchanged(
            pre_invalid, post_invalid, label="invalid merge",
        )

        pre_merge = _snapshot(client)
        merge_status, merged = _runtime_file_write(
            client, merge_target, pre_merge,
            "minimal_2x2.a3d", MERGE_MAP.read_bytes(),
        )
        assert merge_status == 200 and merged.get("ok") is True, merged
        merge_effect = merged["effect"]
        assert merge_effect["action_kind"] == "merge_document"
        assert merge_effect["logical_name"] == "minimal_2x2.a3d"
        assert merge_effect["patches_merged"] > 0
        assert isinstance(merge_effect["instances_added"], int)
        assert "bytes" not in merge_effect and "path" not in merge_effect
        after_merge = _snapshot(client)
        assert after_merge["state_revision"] == pre_merge["state_revision"] + 1
        assert after_merge["history"]["can_undo"] is True

        undo_http, undo = _runtime_write(client, UNDO_LEAF, after_merge)
        assert undo_http == 200 and undo.get("ok") is True, undo
        undo_effect = undo["effect"]
        assert undo_effect["action_kind"] == "history_undo"
        assert undo_effect["status"] == "applied"
        assert undo_effect["prior_urdo_revision"] != undo_effect["next_urdo_revision"]
        after_undo = _snapshot(client)
        assert after_undo["state_revision"] == undo_effect["new_state_revision"]
        assert after_undo["history"]["can_redo"] is True

        redo_http, redo = _runtime_write(client, REDO_LEAF, after_undo)
        assert redo_http == 200 and redo.get("ok") is True, redo
        redo_effect = redo["effect"]
        assert redo_effect["action_kind"] == "history_redo"
        assert redo_effect["status"] == "applied"
        assert redo_effect["prior_urdo_revision"] != redo_effect["next_urdo_revision"]
        after_redo = _snapshot(client)
        assert after_redo["state_revision"] == redo_effect["new_state_revision"]

        dl_status, dl_headers, saved, dl_body = _runtime_download(
            client, save_target, after_redo,
        )
        assert dl_status == 200
        assert dl_headers.get("content-type") == _CONTENT_TYPE_DOCUMENT_RESULT
        assert saved["effect"]["action_kind"] == "save_document"
        assert len(dl_body) > 0
    finally:
        backend.stop()
        assert not _port_is_listening(port)


def test_document_open_promote_regression_two_opens_invalid_bytes(
    tmp_path: Path,
) -> None:
    registry_sha, interactions = _registry_contract()
    open_target = interactions["workspace.document.open.commit"]
    merge_target = interactions["workspace.document.merge.commit"]
    save_target = interactions["workspace.document.save_as.commit"]
    runtime_bin = _packaged_runtime(tmp_path)
    port = _free_loopback_port()
    backend = HiddenAuthoringBackend(port, runtime_bin)
    client = AuthoringClient(port, f"http://127.0.0.1:{_free_loopback_port()}")
    try:
        backend.start()
        backend.wait_ready()
        _handshake(client, backend.session_token, registry_sha)
        baseline = _snapshot(client)

        bad_open_status, bad_open = _runtime_file_write(
            client, open_target, baseline,
            "hostile_open.a3d", b"NOT_AN_A3D_DOCUMENT",
        )
        assert bad_open_status == 422 and bad_open.get("ok") is False
        after_bad_open = _snapshot(client)
        _assert_revisions_unchanged(
            baseline, after_bad_open, label="invalid open",
        )

        open1_status, open1 = _runtime_file_write(
            client, open_target, after_bad_open,
            "minimal_1x1.a3d", OPEN_MAP.read_bytes(),
        )
        assert open1_status == 200 and open1.get("ok") is True, open1
        after_open1 = _snapshot(client)
        assert after_open1["state_revision"] == open1["effect"]["new_state_revision"]

        open2_status, open2 = _runtime_file_write(
            client, open_target, after_open1,
            "minimal_2x2.a3d", MERGE_MAP.read_bytes(),
        )
        assert open2_status == 200 and open2.get("ok") is True, open2
        after_open2 = _snapshot(client)
        assert after_open2["state_revision"] == open2["effect"]["new_state_revision"]
        assert after_open2["state_revision"] > after_open1["state_revision"]

        bad_merge_status, bad_merge = _runtime_file_write(
            client, merge_target, after_open2,
            "hostile_merge.a3d", b"NOT_AN_A3D_DOCUMENT",
        )
        assert bad_merge_status == 422 and bad_merge.get("ok") is False
        after_bad_merge = _snapshot(client)
        _assert_revisions_unchanged(
            after_open2, after_bad_merge, label="invalid merge",
        )

        dl_status, _, saved, dl_body = _runtime_download(
            client, save_target, after_bad_merge,
        )
        assert dl_status == 200
        assert saved["effect"]["action_kind"] == "save_document"
        assert len(dl_body) > 0
        _snapshot(client)
    finally:
        backend.stop()
        assert not _port_is_listening(port)
