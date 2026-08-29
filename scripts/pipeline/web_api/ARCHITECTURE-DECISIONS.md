# Web API Architecture Decisions

## Decision 1: Web Framework -- Flask

**Chosen:** Flask 3.x
**Rejected:** FastAPI
**Date:** 2026-02-10

### Rationale

1. **Pipeline is synchronous.** The asset pipeline (generate -> slice -> process -> assemble) is CPU-bound PIL/numpy work. No async I/O advantage from FastAPI.
2. **Lighter weight.** Flask has fewer dependencies (no pydantic, no uvicorn). The project already has enough dependency surface.
3. **Codebase favors simplicity.** The C++ engine uses direct struct access and global pointers. The Python pipeline uses plain dataclasses. Flask's simplicity matches this ethos.
4. **OpenAPI is not critical.** The API has 6 endpoints consumed by our own web UI. Auto-generated OpenAPI docs are nice-to-have, not a requirement.

### Tradeoffs Accepted

- No auto-generated API documentation (can add flask-apispec later if needed)
- No request/response validation via type hints (manual in `_job_config_from_json()`)
- No async support (not needed; pipeline is CPU-bound)

---

## Decision 2: Deployment via --serve Flag

**Chosen:** `--serve` flag on existing CLI
**Rejected:** Separate `web_server.py` entry point

### Rationale

1. **Single entry point.** Users already know `python -m scripts.pipeline`. Adding `--serve` is discoverable.
2. **Consistent with TUI flags.** `--tui`, `--tui-textual`, and `--wizard` are all on the same CLI. `--serve` follows the pattern.
3. **Development mode only.** Flask's built-in server is for development. Production would use gunicorn or similar, configured separately.

---

## Decision 3: Export Strategy -- Backend-Rendered

**Chosen:** Server-side rendering via PIL (export_service.py)
**Rejected:** Client-side rendering in JavaScript

### Rationale

1. **Reuses existing code.** xp_viewer.py already renders XP cells to PIL images. The export service wraps it.
2. **Engine alignment.** Server-side rendering uses the same CP437 font atlas and metadata parsing as the engine. Client-side would need a JS reimplementation.
3. **Format flexibility.** PIL handles PNG, GIF, and ZIP natively. Client-side would need additional libraries.
4. **Consistency.** All pipeline output (XP, PNG previews, analysis) flows through the same Python stack.

### Future: Client-Side Rendering

For real-time sprite preview in the browser (Plan 13-01), client-side rendering may be added later using the CP437 web atlas. This does not replace the export service; it supplements it for interactive display.

---

## Decision 4: CP437 Web Font -- Bitmap Atlas PNG

**Chosen:** Copy engine's `fonts/cp437_12x12.png` to `web_ui/assets/`
**Rejected:** Web font file (.woff2) or SVG atlas

### Rationale

1. **Exact pixel match.** The engine uses bitmap fonts. Using the same PNG ensures visual parity.
2. **Simple loading.** A PNG atlas loads as a single HTTP request. JavaScript crops individual glyphs via canvas.
3. **Already exists.** The engine ships CP437 atlases at multiple sizes (10, 12, 14, 16, 18, 20, 24px).
4. **Retina support.** A 2x scaled atlas (cp437_atlas_2x.png) provides crisp rendering on high-DPI displays.
