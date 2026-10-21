#!/usr/bin/env python3
"""Guard the FL-4431 deletion of render-thread network telemetry.

The native Colyseus client's ``poll`` call delivers messages synchronously.
The retired metrics path answered a server probe from inside that callback,
which could block the Godot main thread for roughly one second.  Passive frame
and snapshot gap counters remain useful because they observe local timing
without sending network traffic.

The server keeps its published RTT fields at explicit zero values.  Zero is an
honest "not measured" result now that no shipping path performs an RTT
round-trip.  The pure ``recordRttSample`` helper remains for its isolated unit
tests, but production room code must never call it.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "godot_project/scripts/net/ColyseusGameClient.gd"
SERVER_PATH = ROOT / "colyseus_server/src/rooms/AuthoritativeRoom.ts"
GENERATED_SERVER_PATH = ROOT / "colyseus_server/dist/rooms/AuthoritativeRoom.js"


def contract(
    client_source: str,
    server_source: str,
    generated_server_source: str,
) -> list[str]:
    """Return every ownership violation found in the shipping sources."""

    failures: list[str] = []

    client_forbidden = {
        "client periodic sender function": r"\bfunc\s+_maybe_send_runtime_metrics\b",
        "client periodic sender call": r"\b_maybe_send_runtime_metrics\s*\(",
        "client metrics timestamp owner": r"\b_last_metrics_sent_at\b",
        "client reentrant probe callback": r"""["']metrics_probe["']""",
        "client metrics send_message": (
            r"""room\.call\(\s*["']send_message["']\s*,\s*["']metrics["']"""
        ),
        "client metrics send": (
            r"""room\.call\(\s*["']send["']\s*,\s*["']metrics["']"""
        ),
    }
    for label, pattern in client_forbidden.items():
        if re.search(pattern, client_source):
            failures.append(label)

    # These counters are deliberately passive: they inspect elapsed local time
    # and schema snapshots, so deleting the network sender must not delete the
    # timing observations used by diagnostics and receipts.
    client_required = {
        "frame-gap call": "_record_frame_gap()",
        "frame-gap observer": "func _record_frame_gap() -> void:",
        "snapshot-gap observer": "func _record_lag_sample(snapshot: Dictionary) -> void:",
        "frame-gap maximum": "frame_gap_max_ms",
        "snapshot-gap maximum": "snapshot_gap_max_ms",
        "RTT last schema read": 'snapshot.get("rtt_last_ms", rtt_last_ms)',
        "RTT count schema read": 'snapshot.get("rtt_sample_count", 0)',
    }
    for label, token in client_required.items():
        if token not in client_source:
            failures.append(f"missing passive client fact: {label}")

    server_forbidden = {
        "server periodic cadence": r"\bMETRICS_PROBE_INTERVAL_TICKS\b",
        "server probe sequence owner": r"\bmetricsProbeSequence\b",
        "server probe sender": r"\bsendMetricsProbe\s*\(",
        "server probe packet": r"""\.send\(\s*["']metrics_probe["']""",
        "server runtime metrics handler": r"""this\.onMessage\(\s*["']metrics["']""",
        "server runtime RTT mutation": r"\brecordRttSample\(\s*this\.rttStats\b",
    }
    for label, pattern in server_forbidden.items():
        if re.search(pattern, server_source):
            failures.append(label)
        if re.search(pattern, generated_server_source):
            failures.append(f"generated {label}")

    # The server schema still exposes stable fields to existing readers.  With
    # the active probe owner deleted, their initializer and only runtime reads
    # must remain the three explicit zero-backed publication lines below.
    zero_initializer = re.compile(
        r"private\s+rttStats:\s*RttStats\s*=\s*"
        r"\{\s*lastMs:\s*0,\s*maxMs:\s*0,\s*sampleCount:\s*0\s*\};"
    )
    if not zero_initializer.search(server_source):
        failures.append("missing honest zero RTT initializer")

    server_required = {
        "RTT last publication": "this.state.rtt_last_ms = this.rttStats.lastMs;",
        "RTT maximum publication": "this.state.rtt_max_ms = this.rttStats.maxMs;",
        "RTT count publication": "this.state.rtt_sample_count = this.rttStats.sampleCount;",
    }
    for label, token in server_required.items():
        if token not in server_source:
            failures.append(f"missing server zero-field publication: {label}")

    return failures


def _mutant_periodic_client_sender(client_source: str) -> str:
    """Restore the once-per-frame call plus the retired timer-backed helper."""

    anchor = '\t\tsdk_client.call("poll")'
    if anchor not in client_source:
        raise AssertionError("client poll anchor drifted")
    mutated = client_source.replace(
        anchor,
        anchor + "\n\t_maybe_send_runtime_metrics({})",
        1,
    )
    return (
        mutated
        + "\nfunc _maybe_send_runtime_metrics(_probe: Dictionary) -> void:\n"
        + '\troom.call("send_message", "metrics", {})\n'
    )


def _mutant_reentrant_probe_reply(client_source: str) -> str:
    """Restore a metrics reply directly inside the native poll callback."""

    anchor = "\t\tingest_rejection(data)"
    if anchor not in client_source:
        raise AssertionError("client message callback anchor drifted")
    return client_source.replace(
        anchor,
        anchor
        + '\n\telif String(message_type) == "metrics_probe" and data is Dictionary:'
        + '\n\t\troom.call("send_message", "metrics", data)',
        1,
    )


def _mutant_server_probe_cadence(server_source: str) -> str:
    """Restore the server timer and broadcast that triggered the round-trip."""

    const_anchor = "const TICK_MS = 1000 / 30;"
    tick_anchor = "    this.syncSchema();\n  }\n\n  private applyVisualSequenceVisuals"
    if const_anchor not in server_source:
        raise AssertionError("server tick constant anchor drifted")
    if tick_anchor not in server_source:
        raise AssertionError("server tick-body anchor drifted")
    mutated = server_source.replace(
        const_anchor,
        const_anchor + "\nconst METRICS_PROBE_INTERVAL_TICKS = 30;",
        1,
    )
    return mutated.replace(
        tick_anchor,
        (
            "    if (this.world.tick % METRICS_PROBE_INTERVAL_TICKS === 0) {\n"
            "      for (const client of this.clients) {\n"
            '        client.send("metrics_probe", { server_time_ms: Date.now() });\n'
            "      }\n"
            "    }\n"
            + tick_anchor
        ),
        1,
    )


def _mutant_generated_probe_sender(generated_server_source: str) -> str:
    """Restore the tracked generated sender without changing TypeScript."""

    anchor = "\n}\nfunction isRecord(value)"
    if anchor not in generated_server_source:
        raise AssertionError("generated room class anchor drifted")
    return generated_server_source.replace(
        anchor,
        (
            "\n    sendMetricsProbe() {\n"
            '        this.clients[0]?.send("metrics_probe", {});\n'
            "    }\n"
            + anchor
        ),
        1,
    )


MUTANTS = {
    "periodic_client_sender_restored": (
        lambda client, server, generated: (
            _mutant_periodic_client_sender(client),
            server,
            generated,
        )
    ),
    "reentrant_probe_reply_restored": (
        lambda client, server, generated: (
            _mutant_reentrant_probe_reply(client),
            server,
            generated,
        )
    ),
    "server_probe_cadence_restored": (
        lambda client, server, generated: (
            client,
            _mutant_server_probe_cadence(server),
            generated,
        )
    ),
    "generated_probe_sender_restored": (
        lambda client, server, generated: (
            client,
            server,
            _mutant_generated_probe_sender(generated),
        )
    ),
}


def _sources() -> tuple[str, str, str]:
    return (
        CLIENT_PATH.read_text(encoding="utf-8"),
        SERVER_PATH.read_text(encoding="utf-8"),
        GENERATED_SERVER_PATH.read_text(encoding="utf-8"),
    )


def test_shipping_sources_delete_runtime_metrics_owner() -> None:
    client_source, server_source, generated_server_source = _sources()
    assert contract(client_source, server_source, generated_server_source) == []


def test_each_destructive_mutant_is_rejected() -> None:
    client_source, server_source, generated_server_source = _sources()
    for name, mutate in MUTANTS.items():
        mutant_client, mutant_server, mutant_generated = mutate(
            client_source,
            server_source,
            generated_server_source,
        )
        failures = contract(mutant_client, mutant_server, mutant_generated)
        assert failures, f"destructive mutant survived: {name}"


def main() -> int:
    client_source, server_source, generated_server_source = _sources()
    failures = contract(client_source, server_source, generated_server_source)
    if failures:
        for failure in failures:
            print(f"FL4431_COLYSEUS_RUNTIME_METRICS_DELETED_CONTRACT_FAIL {failure}")
        return 1

    caught: list[str] = []
    for name, mutate in MUTANTS.items():
        mutant_client, mutant_server, mutant_generated = mutate(
            client_source,
            server_source,
            generated_server_source,
        )
        if contract(mutant_client, mutant_server, mutant_generated):
            caught.append(name)
            continue
        print(
            "FL4431_COLYSEUS_RUNTIME_METRICS_DELETED_CONTRACT_FAIL "
            f"destructive mutant survived: {name}"
        )
        return 1

    print(
        "FL4431_COLYSEUS_RUNTIME_METRICS_DELETED_CONTRACT_PASS "
        f"mutants={len(caught)} names={','.join(caught)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
