"""
web_api -- Flask HTTP API wrapping AssetService for browser-based interaction.

Provides REST endpoints for uploading PNG sheets, analyzing sprite geometry,
configuring pipeline parameters, running the pipeline, and exporting results.

ARCHITECTURE:
    Thin HTTP layer over AssetService. No business logic here -- all
    pipeline operations delegate to AssetService or export_service.

    Flask chosen over FastAPI because:
    - Pipeline is synchronous (no async I/O advantage)
    - Lighter weight (fewer dependencies)
    - Codebase favors simplicity

ENDPOINTS:
    POST /api/upload           -- Upload a PNG file to staging
    POST /api/analyze          -- Analyze uploaded image geometry
    POST /api/configure        -- Validate a job configuration
    POST /api/run              -- Execute the pipeline
    GET  /api/status/<job_id>  -- Check job status
    GET  /api/export/<job_id>  -- Export results (PNG/ZIP/GIF)

Tags: [FLOW:HTTP] [DEPENDENCY:FLASK]
"""

from scripts.pipeline.web_api.app import create_app

__all__ = ["create_app"]
