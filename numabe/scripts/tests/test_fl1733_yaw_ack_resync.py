"""FL-1733 causal contract for authoritative yaw reconciliation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INGEST = (ROOT / "engine/network_ingest_snapshot.cpp").read_text()


def test_snapshot_yaw_resync_requires_latest_sent_command_acknowledgement():
    assert "latest_yaw_command_acknowledged" in INGEST
    assert "move.last_acked_seq == move.diag_last_sent_seq" in INGEST
    assert (
        "if (!torque_recently_active && latest_yaw_command_acknowledged)"
        in INGEST
    )


def test_elapsed_grace_alone_cannot_authorize_camera_rewind():
    assert "if (!torque_recently_active)\n" not in INGEST
