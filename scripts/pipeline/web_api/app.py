"""
app.py -- Flask application factory for the asset pipeline web API.

Creates and configures the Flask app with CORS, upload limits, and
route registration. Use create_app() to get a configured app instance.

Tags: [FLOW:HTTP] [DEPENDENCY:FLASK]
"""

import os
import tempfile
from pathlib import Path

from flask import Flask, send_from_directory, abort
from flask_cors import CORS


def create_app(
    upload_dir: str = None,
    max_upload_mb: int = 50,
) -> Flask:
    """Create and configure the Flask application.

    Args:
        upload_dir: Directory for uploaded files. Defaults to a temp dir.
        max_upload_mb: Maximum upload file size in megabytes.

    Returns:
        Configured Flask app instance.
    """
    # Resolve web_ui directory for static file serving
    web_ui_dir = str(Path(__file__).resolve().parent.parent / "web_ui")

    app = Flask(__name__, static_folder=web_ui_dir, static_url_path="/static")

    # Configure upload directory
    if upload_dir is None:
        upload_dir = os.environ.get(
            "ASSET_UPLOAD_DIR",
            tempfile.mkdtemp(prefix="asciicker_uploads_"),
        )
    upload_path = Path(upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_DIR"] = str(upload_path)
    app.config["WEB_UI_DIR"] = web_ui_dir

    # Upload size limit
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024

    # CORS for local development
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register API routes
    from scripts.pipeline.web_api.routes import api_bp
    app.register_blueprint(api_bp)

    # Serve web UI at root (must not intercept /api/* routes)
    @app.route("/")
    def serve_index():
        return send_from_directory(web_ui_dir, "index.html")

    @app.route("/workbench")
    def serve_workbench():
        return send_from_directory(web_ui_dir, "workbench.html")

    @app.route("/branches")
    def serve_branches():
        return send_from_directory(web_ui_dir, "branches.html")

    @app.route("/<path:filename>")
    def serve_ui_file(filename):
        # Don't intercept API routes -- let blueprint handle those
        if filename.startswith("api/"):
            abort(404)
        file_path = Path(web_ui_dir) / filename
        if not file_path.is_file():
            abort(404)
        return send_from_directory(web_ui_dir, filename)

    return app
