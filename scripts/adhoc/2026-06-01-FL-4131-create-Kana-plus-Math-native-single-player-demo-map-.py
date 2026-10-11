# Ad hoc script: FL-4131 create Kana plus Math native single-player demo map through ASCIIID CDP
# Created: 2026-06-01
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Create an FL-4131 native single-player demo map with real extended glyph presets.

The map is authored through ASCIIID CDP, not by editing the sidecar directly:
- load assets/a3d/game_map_y8.a3d
- apply Katakana Grass preset to material 1
- apply Edge / Ridge Math preset to material 3
- paint a large visible central terrain region with material 1 and material 3
- save assets/a3d/fl4131_kana_math_demo.a3d, emitting the glyph sidecar
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASCIIID = REPO / ".run" / "asciiid"
SEED_MAP = REPO / "assets" / "a3d" / "game_map_y8.a3d"
OUT_MAP = REPO / "assets" / "a3d" / "fl4131_kana_math_demo.a3d"

KATAKANA_MATERIAL_ID = 1
KATAKANA_PRESET_INDEX = 4     # GRASS / Katakana Grass
MATH_MATERIAL_ID = 3
MATH_PRESET_INDEX = 7         # STONE / Edge / Ridge Math


def rect_cells(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    return [(x, y) for y in range(y0, y1) for x in range(x0, x1)]


def batch_params(mat_id: int, cells: list[tuple[int, int]]) -> str:
    coords = " ".join(f"{x} {y}" for x, y in cells)
    return f"{mat_id} {len(cells)} {coords}"


def batch_set_cells(client: "CdpClient", mat_id: int, cells: list[tuple[int, int]], chunk_size: int = 300) -> None:
    for start in range(0, len(cells), chunk_size):
        chunk = cells[start:start + chunk_size]
        print(client.call("BATCH_SET_CELLS", batch_params(mat_id, chunk), timeout_s=30.0).strip())


class CdpClient:
    def __init__(self, port: int, deadline_s: float = 30.0):
        self.next_id = 1
        self.buf = ""
        end = time.time() + deadline_s
        last_err = None
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None)
                return
            except OSError as exc:
                last_err = exc
                time.sleep(0.25)
        raise RuntimeError(f"CDP {port} not ready: {last_err}")

    def call(self, method: str, params: str = "", timeout_s: float = 30.0) -> str:
        req_id = self.next_id
        self.next_id += 1
        payload = json.dumps({"id": req_id, "method": method, "params": params}) + "\n"
        self.sock.sendall(payload.encode("utf-8"))
        end = time.time() + timeout_s
        while time.time() < end:
            self.sock.settimeout(max(0.1, end - time.time()))
            try:
                chunk = self.sock.recv(65536).decode("utf-8", errors="replace")
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError(f"CDP closed while waiting for {method}")
            self.buf += chunk
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if parsed.get("id") == req_id:
                    return str(parsed.get("result", ""))
        raise TimeoutError(method)

    def close(self) -> None:
        self.sock.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47748)
    ap.add_argument("--out-map", default=str(OUT_MAP))
    args = ap.parse_args()

    out_map = Path(args.out_map)
    out_sidecar = Path(str(out_map) + ".glyph_profile.json")
    out_map.parent.mkdir(parents=True, exist_ok=True)
    out_map.unlink(missing_ok=True)
    out_sidecar.unlink(missing_ok=True)

    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(args.port)],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = None
    try:
        client = CdpClient(args.port)
        print(client.call("LOAD_MAP", str(SEED_MAP), timeout_s=60.0).strip())
        print(client.call("FL4131_APPLY_EXTENDED_PRESET", f"{KATAKANA_MATERIAL_ID} {KATAKANA_PRESET_INDEX}", timeout_s=20.0).strip())
        print(client.call("FL4131_APPLY_EXTENDED_PRESET", f"{MATH_MATERIAL_ID} {MATH_PRESET_INDEX}", timeout_s=20.0).strip())

        # Large central slabs so the single-player spawn/camera has obvious terrain coverage.
        kana_cells = rect_cells(52, 52, 94, 112)
        math_cells = rect_cells(94, 52, 136, 112)
        batch_set_cells(client, KATAKANA_MATERIAL_ID, kana_cells)
        batch_set_cells(client, MATH_MATERIAL_ID, math_cells)

        print(client.call("FL4131_DUMP_MATERIAL_GLYPHS", str(KATAKANA_MATERIAL_ID), timeout_s=10.0).strip())
        print(client.call("FL4131_DUMP_MATERIAL_GLYPHS", str(MATH_MATERIAL_ID), timeout_s=10.0).strip())
        print(client.call("SAVE_MAP", str(out_map), timeout_s=60.0).strip())
        try:
            client.call("QUIT", "", timeout_s=2.0)
        except Exception:
            pass
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    if not out_map.exists():
        print(f"ERROR: map was not written: {out_map}", file=sys.stderr)
        return 2
    if not out_sidecar.exists():
        print(f"ERROR: sidecar was not written: {out_sidecar}", file=sys.stderr)
        return 3

    sidecar = json.loads(out_sidecar.read_text(encoding="utf-8"))
    glyph_ids = sorted({
        cell.get("glyph_id")
        for entry in sidecar.get("material_entries", [])
        for cell in entry.get("cells", [])
    })
    required = {513, 514, 515, 516, 517, 518, 519, 520, 556, 557, 558, 559, 560, 561, 562, 563}
    missing = sorted(required.difference(glyph_ids))
    if missing:
        print(f"ERROR: saved sidecar missing expected Kana/Math GlyphIds: {missing}", file=sys.stderr)
        return 4
    print(json.dumps({
        "verdict": "PASS",
        "map": str(out_map),
        "sidecar": str(out_sidecar),
        "map_bytes": out_map.stat().st_size,
        "sidecar_bytes": out_sidecar.stat().st_size,
        "glyph_ids": glyph_ids,
        "kana_material_id": KATAKANA_MATERIAL_ID,
        "kana_preset_index": KATAKANA_PRESET_INDEX,
        "math_material_id": MATH_MATERIAL_ID,
        "math_preset_index": MATH_PRESET_INDEX,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
