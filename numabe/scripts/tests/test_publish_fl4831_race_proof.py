from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from publish_fl4831_race_proof import SELECTED_TAGS, _accept, _capture_sources


def accepted_receipt(client: int = 1) -> dict:
    return {
        "client": client,
        "passed": True,
        "laps": 3,
        "map_id": "race_circuit_y8",
        "drift_script": {"tail_sustain_ticks": 3},
        "boost": {"top_speed_u_s": 81.0, "unboosted_ceiling_u_s": 60.0},
        "crash": {"entry_elapsed": 100, "exit_elapsed": 130},
        "physics_vectors": {"ray_bits_seen": 15, "max_force": 1.0, "max_torque": 1.0},
    }


class AcceptanceTests(unittest.TestCase):
    def test_accepts_complete_shipping_receipts(self) -> None:
        _accept([accepted_receipt(1), accepted_receipt(2)])

    def test_rejects_nominal_boost_that_never_exceeds_baseline(self) -> None:
        receipt = accepted_receipt()
        receipt["boost"]["top_speed_u_s"] = 60.0
        with self.assertRaisesRegex(ValueError, "boost_above_baseline"):
            _accept([receipt])

    def test_capture_set_must_be_complete_and_inside_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp).resolve()
            captures = []
            for tag in SELECTED_TAGS:
                path = run_dir / f"{tag}.png"
                path.write_bytes(b"png")
                captures.append({"tag": tag, "path": str(path)})
            receipt = accepted_receipt()
            receipt["captures"] = captures
            selected = _capture_sources(run_dir, [receipt])
            self.assertEqual(set(selected[0]), set(SELECTED_TAGS))

            receipt["captures"][0]["path"] = str(run_dir.parent / "stale.png")
            with self.assertRaisesRegex(ValueError, "escapes run directory"):
                _capture_sources(run_dir, [receipt])


if __name__ == "__main__":
    unittest.main()
