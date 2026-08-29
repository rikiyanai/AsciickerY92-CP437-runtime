# Ad hoc script: FL-4260 headed Material Look FG BG color click feedback proof
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs/research/ascii/verification/fl4260/2026-06-22-material-look-fg-bg-click-feedback"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8767"))


def cdp(method: str, params: str | None = None, timeout: float = 10.0) -> str:
    msg = {"id": 1, "method": method}
    if params is not None:
        msg["params"] = params
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(msg) + "\n").encode())
        out = b""
        while b"\n" not in out:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
    text = out.decode("utf-8", "replace").strip()
    try:
        return str(json.loads(text).get("result", ""))
    except json.JSONDecodeError:
        return text


def parse_rects(text: str) -> list[dict]:
    rects: list[dict] = []
    for line in text.splitlines():
        if "CTRL_RECT" not in line:
            continue
        item: dict = {"raw": line}
        for part in line.split():
            if part.startswith("label="):
                item["label"] = part.split("=", 1)[1]
            elif part.startswith("x="):
                item["x"] = float(part.split("=", 1)[1])
            elif part.startswith("y="):
                item["y"] = float(part.split("=", 1)[1])
            elif part.startswith("w="):
                item["w"] = float(part.split("=", 1)[1])
            elif part.startswith("h="):
                item["h"] = float(part.split("=", 1)[1])
        if "label" in item:
            rects.append(item)
    return rects


def capture_rects(name: str) -> tuple[Path, list[dict]]:
    frame_dir = OUT / name
    frame_dir.mkdir(parents=True, exist_ok=True)
    cdp("FL4260_CTRL_RECTS_RECORD", "1")
    time.sleep(0.20)
    cdp("CAPTURE_UI_FRAME", str(frame_dir), timeout=20.0)
    time.sleep(0.20)
    rect_text = cdp("FL4260_CTRL_RECTS_RECORD", "0")
    (frame_dir / "ctrl_rects.txt").write_text(rect_text)
    rects = parse_rects(rect_text)
    (frame_dir / "ctrl_rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rects))
    return frame_dir / "ui_frame.png", rects


def find_rect(rects: list[dict], label: str) -> dict:
    for rect in rects:
        if rect.get("label") == label:
            return rect
    raise RuntimeError(f"missing rect {label}")


def is_visible(rect: dict) -> bool:
    return rect["x"] >= 0 and rect["x"] + rect["w"] <= 800 and rect["y"] >= 0 and rect["y"] + rect["h"] <= 600


def click_rect(rect: dict) -> str:
    x = int(rect["x"] + rect["w"] * 0.5)
    y = int(rect["y"] + rect["h"] * 0.5)
    return cdp("RUN_MOUSE_CLICK_PROBE", f"{x} {y}")


def close_popup() -> str:
    return cdp("RUN_SDL_KEY", "41 1")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    transcript = (OUT / "action_transcript.jsonl").open("w")

    def log(event: str, **kw) -> None:
        transcript.write(json.dumps({"event": event, **kw}, sort_keys=True) + "\n")
        transcript.flush()

    for method, params in [
        ("NEW_MAP", None),
        ("FL4260_SET_SIDEBAR_WIDTH", "1120"),
        ("FL4260_RENDERING_PROOF", "1 -1 0"),
        ("FL4260_LOCK_SIDEBAR_TAB", "9"),
    ]:
        result = cdp(method, params)
        log("cdp", method=method, params=params, result=result[:800])
        time.sleep(0.35)

    _, rects = capture_rects("open_colors_before")
    color_presets = find_rect(rects, "starters.color_presets")
    if not is_visible(color_presets):
        raise RuntimeError(f"Color Presets button not visible before opening colors: {color_presets}")
    open_response = click_rect(color_presets)
    log("open_colors", label="starters.color_presets", clicked_rect=color_presets, click_response=open_response)
    time.sleep(0.55)
    scroll_y = 900.0
    cdp("FL4260_SCROLL_Y", f"{scroll_y:.1f}")
    time.sleep(0.35)

    checks = []
    click_plan = [
        ("color.fg.r0", 439),
        ("color.bg.r0", 440),
        ("color.fg.r1", 441),
        ("color.bg.r1", 442),
        ("color.fg.r2", 443),
        ("color.bg.r2", 444),
        ("color.fg.r3", 445),
        ("color.bg.r3", 446),
    ]
    for label, row in click_plan:
        rect = None
        for attempt in range(5):
            _, rects = capture_rects(f"before_{label.replace('.', '_')}_{attempt}")
            rect = find_rect(rects, label)
            if is_visible(rect):
                break
            scroll_y += rect["y"] - 340.0
            cdp("FL4260_SCROLL_Y", f"{scroll_y:.1f}")
            time.sleep(0.35)
        if rect is None or not is_visible(rect):
            raise RuntimeError(f"rect {label} not visible before click: {rect}")
        click_response = click_rect(rect)
        time.sleep(0.55)
        after_frame, after_rects = capture_rects(f"after_{label.replace('.', '_')}")
        item = {
            "label": label,
            "row": row,
            "clicked_rect": rect,
            "click_response": click_response,
            "after_frame": str(after_frame.relative_to(OUT)),
            "after_rect_count": len(after_rects),
            "status": "headed_click_captured",
        }
        checks.append(item)
        log("click", **item)
        close_response = close_popup()
        log("close_popup", label=label, response=close_response)
        time.sleep(0.45)

    cdp("FL4260_LOCK_SIDEBAR_TAB", "-1")
    summary = {
        "artifact_class": "headed Material Look FG/BG color click feedback proof",
        "rows_targeted": [row for _, row in click_plan],
        "checks": checks,
        "out_dir": str(OUT.relative_to(REPO)),
        "claim_boundary": "local headed UI click feedback only; no Law 15, Law 16, product acceptance, backend closure, native parity, nor operator signoff",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (OUT / "README.md").write_text(
        "# FL-4260 Material Look FG/BG click feedback\n\n"
        "Local headed CDP proof that the patched Material Look foreground/background color swatches accept real mouse clicks and produce after-click UI captures. "
        "This is a per-control UI feedback checkpoint only. It does not claim backend closure, native parity, Law 15, Law 16, product acceptance, nor operator signoff.\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
