"""
routes.py -- API endpoint definitions for the asset pipeline web API.

All endpoints are registered on a Blueprint for clean separation from
the application factory. Each endpoint is a thin wrapper around
AssetService or export_service methods.

ENDPOINTS:
    POST /api/upload           -- Upload PNG to staging
    POST /api/analyze          -- Analyze image geometry
    POST /api/configure        -- Validate job configuration JSON
    POST /api/run              -- Execute pipeline
    GET  /api/status/<job_id>  -- Job lookup
    GET  /api/export/<job_id>  -- Export result as PNG/ZIP/GIF
    POST /api/viewer/load-png  -- Slice PNG into viewer frames
    POST /api/viewer/load-xp   -- Render XP into viewer frames
    POST /api/viewer/load-xp-path -- Load XP from server path

    POST /api/workbench/start-session   -- Create workbench session
    GET  /api/workbench/session/<id>    -- Session status
    POST /api/workbench/load-xp        -- Upload XP and create pre-populated session
    POST /api/workbench/store-source    -- Upload source image
    POST /api/workbench/extract-sprites -- Extract sprite bboxes
    POST /api/workbench/load-server-image -- Load image from server path
    POST /api/workbench/merge-sources   -- Merge multiple sources
    POST /api/workbench/populate-frames -- Place sprites into grid
    POST /api/workbench/undo            -- Undo last operation
    POST /api/workbench/redo            -- Redo last undone operation
    POST /api/workbench/transform-cells -- Flip/rotate cells
    POST /api/workbench/swap-cells      -- Swap two cells
    POST /api/workbench/fill-from-slot  -- Copy cell into targets
    POST /api/workbench/import-external -- Import image into cells
    POST /api/workbench/export-xp       -- Export grid to XP
    GET  /api/workbench/download-xp     -- Download XP file
    POST /api/workbench/xp-tool-command -- Generate xp_tool command

    GET  /api/branches/<job_id> -- Get branch tree manifest for a job
    POST /api/branches/<job_id>/promote/<branch_id> -- Promote a branch
    POST /api/branches/<job_id>/prune/<branch_id> -- Prune a branch
    GET  /api/branches/<job_id>/<branch_id>/thumbnail -- Get branch thumbnail

Tags: [FLOW:HTTP] [DEPENDENCY:FLASK]
"""

import base64
import io
import json
import uuid
from copy import deepcopy
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app, send_file
from PIL import Image
from werkzeug.utils import secure_filename

from scripts.pipeline.service.asset_service import AssetService
from scripts.pipeline.service.job import AssetJobConfig
from scripts.pipeline.service.slicing import SlicingSpec
from scripts.pipeline.branch_enums import BranchStage, BranchStatus
from scripts.pipeline.branch_model import BranchNode, BranchTree, JobManifest
from scripts.pipeline.branch_thumbnails import generate_thumbnail
from scripts.pipeline.config_schema import PipelineConfig, generate_ui_form
from scripts.pipeline.web_api.workbench_session import (
    create_session,
    get_session,
    extract_sprites_from_source,
    save_session_debounced,
    list_sessions,
    delete_session,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Shared AssetService instance (stateful: holds job registry)
_service = AssetService()

# In-memory branch manifest registry, keyed by job_id.
# Manifests are also persisted to staging/{job_id}/manifest.json on mutation.
_manifests: dict[str, JobManifest] = {}

# Allowed upload extensions
_ALLOWED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".webp",
}

# [SEC] Maximum upload file size: 50 MB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _is_safe_path(path_str: str, base_dir: str | None = None) -> bool:
    """[SEC-04] Validate that a user-supplied path is safe to access.

    Guards against null-byte injection and path traversal. When ``base_dir``
    is provided, the resolved path must be contained within that directory
    (prevents arbitrary file reads via crafted paths).

    Args:
        path_str: User-supplied filesystem path.
        base_dir: If provided, require the path to be within this directory.
    """
    if not path_str or "\x00" in path_str:
        return False
    try:
        resolved = Path(path_str).resolve()
        if base_dir:
            base = Path(base_dir).resolve()
            return resolved == base or resolved.is_relative_to(base)
        return resolved.is_absolute()
    except (ValueError, OSError):
        return False


def _branch_root_dir() -> Path:
    """Return the single trusted filesystem root for branch artifact serving.

    All branch manifests and artifacts are written to the pipeline's
    STAGING_DIR. Using one root (instead of UPLOAD_DIR + STAGING_DIR) keeps
    containment checks simple and auditable.
    """
    try:
        from scripts.pipeline.staging import STAGING_DIR
        return STAGING_DIR.resolve()
    except ImportError:
        return Path(current_app.config.get("UPLOAD_DIR", "/tmp")).resolve()


def _allowed_file(filename: str) -> bool:
    """Check if filename has an allowed image extension."""
    return Path(filename).suffix.lower() in _ALLOWED_EXTENSIONS


_XP_META_MAX = 35


def _resolve_metadata_anims(config: AssetJobConfig) -> list[int]:
    """Resolve anim counts as they will be encoded in XP metadata."""
    anims = [int(v) for v in config.frames]
    explicit_projs = int(config.explicit_projs) if config.explicit_projs is not None else None
    reflection_policy = str(config.reflection_policy or "").strip().lower()
    if explicit_projs and explicit_projs > 1 and reflection_policy == "none":
        bad = [v for v in anims if v % explicit_projs != 0]
        if bad:
            raise ValueError(
                f"Pre-baked projs={explicit_projs} requires frame counts divisible by "
                f"{explicit_projs}, got {bad}."
            )
        return [v // explicit_projs for v in anims]
    return anims


def _validate_metadata_bounds(config: AssetJobConfig) -> None:
    """Fail early when requested metadata cannot be encoded in XP."""
    angles = int(config.angles)
    if angles > _XP_META_MAX:
        raise ValueError(
            f"angles={angles} exceeds XP metadata limit ({_XP_META_MAX}). "
            "Reduce angle count."
        )
    anims = _resolve_metadata_anims(config)
    for idx, count in enumerate(anims):
        if int(count) > _XP_META_MAX:
            raise ValueError(
                f"frames[{idx}] resolves to {count}, exceeding XP metadata limit "
                f"({_XP_META_MAX}). Use semantic sprite frame counts (not raw sheet "
                "subsection columns)."
            )


def _job_config_from_json(data: dict) -> AssetJobConfig:
    """Build an AssetJobConfig from a JSON dict.

    Maps JSON keys to AssetJobConfig fields. Unknown keys are ignored.
    Only sets fields that are present in the JSON to preserve defaults.

    Args:
        data: JSON dict from the request body.

    Returns:
        AssetJobConfig with values from the JSON.
    """
    kwargs = {}

    # String fields
    for key in (
        "name",
        "asset_type",
        "source_type",
        "source_path",
        "blender_object",
        "downscale_algorithm",
        "template_name",
        "slice_mode",
        "reflection_policy",
        "pixel_perfect_mode",
    ):
        if key in data:
            kwargs[key] = data[key]

    # Integer fields
    for key in (
        "angles",
        "projs",
        "target_cells_high",
        "render_resolution",
        "explicit_projs",
        "synthesize_angles",
    ):
        if key in data:
            val = data[key]
            kwargs[key] = int(val) if val is not None else None

    # Boolean fields
    for key in (
        "transparency",
        "normalization",
        "pre_slice_check",
        "pre_slice_check_strict",
    ):
        if key in data:
            kwargs[key] = bool(data[key])

    # Tuple field: frames
    if "frames" in data:
        frames_val = data["frames"]
        if isinstance(frames_val, (list, tuple)):
            kwargs["frames"] = tuple(int(f) for f in frames_val)
        elif isinstance(frames_val, str):
            parts = [int(x.strip()) for x in frames_val.split(",") if x.strip()]
            kwargs["frames"] = tuple(parts) if parts else (1,)

    # SlicingSpec: parse from nested dict when present
    if "slice_spec" in data and data["slice_spec"] is not None:
        ss = data["slice_spec"]
        spec_kwargs = {}
        if "cell_w_px" in ss and ss["cell_w_px"] is not None:
            spec_kwargs["cell_w_px"] = int(ss["cell_w_px"])
        if "cell_h_px" in ss and ss["cell_h_px"] is not None:
            spec_kwargs["cell_h_px"] = int(ss["cell_h_px"])
        if "cols" in ss and ss["cols"] is not None:
            spec_kwargs["cols"] = int(ss["cols"])
        if "rows" in ss and ss["rows"] is not None:
            spec_kwargs["rows"] = int(ss["rows"])
        if "margin_x_px" in ss and ss["margin_x_px"] is not None:
            spec_kwargs["margin_x_px"] = int(ss["margin_x_px"])
        if "margin_y_px" in ss and ss["margin_y_px"] is not None:
            spec_kwargs["margin_y_px"] = int(ss["margin_y_px"])
        if "spacing_x_px" in ss and ss["spacing_x_px"] is not None:
            spec_kwargs["spacing_x_px"] = int(ss["spacing_x_px"])
        if "spacing_y_px" in ss and ss["spacing_y_px"] is not None:
            spec_kwargs["spacing_y_px"] = int(ss["spacing_y_px"])
        if "order" in ss:
            spec_kwargs["order"] = ss["order"]
        if "origin" in ss:
            spec_kwargs["origin"] = ss["origin"]
        if "angle_row_map" in ss and ss["angle_row_map"] is not None:
            spec_kwargs["angle_row_map"] = [int(v) for v in ss["angle_row_map"]]
        if spec_kwargs:
            kwargs["slice_spec"] = SlicingSpec(**spec_kwargs)

    # --- Background: explicit object first, then bg_* field bridge ---
    if "background" not in kwargs:
        _bg_obj = data.get("background")
        if isinstance(_bg_obj, dict) and _bg_obj.get("mode"):
            # Tier 1: explicit BackgroundSpec dict from caller
            # "auto" means let _auto_detect_bg_spec run — skip creating BackgroundSpec
            if _bg_obj["mode"] != "auto":
                from scripts.pipeline.service.slicing import BackgroundSpec
                _raw_key = _bg_obj.get("key_color")
                if isinstance(_raw_key, (list, tuple)) and len(_raw_key) >= 3:
                    _key = tuple(int(c) for c in _raw_key[:3])
                else:
                    _key = (255, 0, 255)
                kwargs["background"] = BackgroundSpec(
                    mode=_bg_obj["mode"],
                    key_color=_key,
                    tolerance=int(_bg_obj.get("tolerance", 8)),
                    alpha_threshold=int(_bg_obj.get("alpha_threshold", 128)),
                )
        else:
            # Tier 2: bridge from flat bg_* fields (web UI path)
            _bg_colors = data.get("bg_key_colors")
            _bg_mode = data.get("bg_mode")
            _bg_tol = data.get("bg_tolerance")
            # "auto" means let _auto_detect_bg_spec run — do NOT set background
            if _bg_mode and _bg_mode != "auto":
                from scripts.pipeline.service.slicing import BackgroundSpec
                _key = (255, 0, 255)  # default magenta
                if isinstance(_bg_colors, list) and len(_bg_colors) > 0:
                    _first = _bg_colors[0]
                    if isinstance(_first, (list, tuple)) and len(_first) >= 3:
                        _key = tuple(int(c) for c in _first[:3])
                _tol = int(_bg_tol) if _bg_tol is not None else 8
                kwargs["background"] = BackgroundSpec(
                    mode=_bg_mode, key_color=_key, tolerance=_tol,
                )
            # else: bg_mode is "auto" or absent → leave background=None → auto-detect

    # Shared 4-track pipeline config (optional)
    if "pipeline_config" in data and data["pipeline_config"] is not None:
        cfg = PipelineConfig.from_dict(data["pipeline_config"])
        kwargs["pipeline_config"] = cfg.to_dict()

    return AssetJobConfig(**kwargs)


# ============================================================================
# POST /api/upload
# ============================================================================


@api_bp.route("/upload", methods=["POST"])
def upload():
    """Upload a PNG file to the staging directory.

    Expects multipart/form-data with a 'file' field.

    Returns:
        JSON with 'file_id' and 'path' on success.
        400 if no file or invalid extension.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file field in request"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # [SEC-03] Sanitize filename to prevent path traversal
    sanitized = secure_filename(uploaded.filename)
    # secure_filename can strip unicode chars, losing the extension.
    # Recover extension from original filename when sanitized has none.
    orig_ext = Path(uploaded.filename).suffix.lower() if uploaded.filename else ""
    if sanitized and not Path(sanitized).suffix and orig_ext:
        sanitized = sanitized + orig_ext
    if not sanitized:
        return jsonify({"error": "Invalid filename"}), 400

    if not _allowed_file(sanitized):
        ext = Path(sanitized).suffix
        return jsonify(
            {
                "error": f"File type '{ext}' not allowed",
                "allowed": sorted(e.lstrip(".") for e in _ALLOWED_EXTENSIONS),
            }
        ), 400

    # Generate unique filename to prevent collisions
    file_id = str(uuid.uuid4())
    ext = Path(sanitized).suffix.lower()
    safe_name = f"{file_id}{ext}"

    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    dest = upload_dir / safe_name
    uploaded.save(str(dest))

    return jsonify(
        {
            "file_id": file_id,
            "filename": uploaded.filename,
            "path": str(dest),
            "size_bytes": dest.stat().st_size,
        }
    ), 201


# ============================================================================
# POST /api/analyze
# ============================================================================


@api_bp.route("/analyze", methods=["POST"])
def analyze():
    """Analyze an uploaded image and suggest pipeline parameters.

    Expects JSON with 'path' (to uploaded file) and optional 'hints'.

    Returns:
        JSON with suggested angles, frames, cell size, etc.
        400 if path missing or file not found.
    """
    data = request.get_json(silent=True) or {}
    image_path = data.get("path")

    if not image_path:
        return jsonify({"error": "Missing 'path' field"}), 400

    # [SEC-04] Validate path is within allowed upload directory
    if not _is_safe_path(image_path):
        return jsonify({"error": "Path not within allowed directory"}), 403

    if not Path(image_path).exists():
        return jsonify({"error": f"File not found: {image_path}"}), 404

    try:
        hints = data.get("hints", {})
        result = _service.analyze(image_path, hints=hints)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {e}"}), 500


# ============================================================================
# POST /api/configure
# ============================================================================


@api_bp.route("/configure", methods=["POST"])
def configure():
    """Validate a job configuration without executing it.

    Expects JSON matching AssetJobConfig fields.

    Returns:
        JSON with the parsed config and any validation issues.
        400 if configuration is invalid.
    """
    data = request.get_json(silent=True) or {}

    try:
        config = _job_config_from_json(data)
        _validate_metadata_bounds(config)
        # Return the parsed config as confirmation
        return jsonify(
            {
                "valid": True,
                "config": {
                    "name": config.name,
                    "asset_type": config.asset_type,
                    "source_path": config.source_path,
                    "angles": config.angles,
                    "frames": list(config.frames),
                    "projs": config.projs,
                    "transparency": config.transparency,
                    "render_resolution": config.render_resolution,
                    "reflection_policy": config.reflection_policy,
                    "pixel_perfect_mode": config.pixel_perfect_mode,
                    "pipeline_config": config.pipeline_config,
                },
            }
        ), 200
    except (ValueError, TypeError) as e:
        return jsonify({"valid": False, "error": str(e)}), 400


# ============================================================================
# POST /api/run
# ============================================================================


@api_bp.route("/run", methods=["POST"])
def run_pipeline():
    """Execute the asset pipeline with the given configuration.

    Expects JSON matching AssetJobConfig fields. The 'source_path' field
    must point to an uploaded file.

    Returns:
        JSON with job_id, xp_path, checksum, and metadata.
        400 if configuration invalid.
        500 if pipeline fails.
    """
    data = request.get_json(silent=True) or {}

    try:
        config = _job_config_from_json(data)
        _validate_metadata_bounds(config)
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid configuration: {e}"}), 400

    if not config.source_path or not Path(config.source_path).exists():
        return jsonify({"error": "source_path is required and must exist"}), 400

    # [SEC-04] Validate source_path is within allowed directory
    if not _is_safe_path(config.source_path):
        return jsonify({"error": "source_path not within allowed directory"}), 403

    try:
        output = _service.run(config)
        try:
            # Prefer the pipeline's own manifest (richer, includes auto-mode
            # branch tree) over the minimal bootstrap.  The pipeline stores
            # its manifest under a separate auto_job_id which the service
            # propagates via output.metadata["manifest_job_id"].
            from scripts.pipeline.staging import STAGING_DIR

            manifest_jid = (output.metadata or {}).get("manifest_job_id")
            pipeline_manifest_path = (
                STAGING_DIR / manifest_jid / "manifest.json"
                if manifest_jid
                else None
            )
            if pipeline_manifest_path and pipeline_manifest_path.exists():
                manifest = JobManifest.load_json(pipeline_manifest_path)
                manifest.job_id = output.job_id  # align with web API job ID
            else:
                manifest = _bootstrap_manifest_from_run(config, output)
            _save_manifest(manifest)
        except Exception as manifest_err:
            current_app.logger.warning(
                "Failed to bootstrap branch manifest for job %s: %s",
                output.job_id,
                manifest_err,
            )
        return jsonify(
            {
                "job_id": output.job_id,
                "xp_path": str(output.xp_path),
                "checksum_sha256": output.checksum_sha256,
                "metadata": output.metadata,
                "created_at": output.created_at,
            }
        ), 200
    except ValueError as e:
        return jsonify({"error": f"Pipeline validation error: {e}"}), 400
    except Exception as e:
        return jsonify({"error": f"Pipeline failed: {e}"}), 500


# ============================================================================
# GET /api/status/<job_id>
# ============================================================================


@api_bp.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    """Look up a completed job by ID.

    Returns:
        JSON with job details.
        404 if job not found.
    """
    output = _service.status(job_id)
    if output is None:
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    return jsonify(
        {
            "job_id": output.job_id,
            "xp_path": str(output.xp_path),
            "checksum_sha256": output.checksum_sha256,
            "metadata": output.metadata,
            "created_at": output.created_at,
        }
    ), 200


# ============================================================================
# GET /api/export/<job_id>
# ============================================================================


@api_bp.route("/export/<job_id>", methods=["GET"])
def export(job_id: str):
    """Export a completed job's results.

    Query parameters:
        format: "png" (default), "zip", or "gif"
        anim: Animation index for GIF (default 0)
        angle: Angle index for GIF (default 0)
        fps: Frames per second for GIF (default 8)
        scale: Scale factor (default 1)

    Returns:
        Image file (PNG/GIF) or ZIP archive.
        404 if job or XP file not found.
        400 if export fails.
    """
    from scripts.pipeline.export_service import (
        export_xp_to_png,
        export_xp_to_gif,
        export_xp_to_zip,
    )

    output = _service.status(job_id)
    if output is None:
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    xp_path = str(output.xp_path)
    if not Path(xp_path).exists():
        return jsonify({"error": f"XP file not found: {xp_path}"}), 404

    export_format = request.args.get("format", "png")
    scale = int(request.args.get("scale", "1"))

    try:
        if export_format == "png":
            img = export_xp_to_png(xp_path, scale=scale)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return send_file(
                buf,
                mimetype="image/png",
                download_name=f"{Path(xp_path).stem}.png",
            )

        elif export_format == "zip":
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name
            export_xp_to_zip(xp_path, tmp_path, scale=scale)
            return send_file(
                tmp_path,
                mimetype="application/zip",
                download_name=f"{Path(xp_path).stem}_frames.zip",
                as_attachment=True,
            )

        elif export_format == "gif":
            anim_idx = int(request.args.get("anim", "0"))
            angle_idx = int(request.args.get("angle", "0"))
            fps = int(request.args.get("fps", "8"))

            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tmp:
                tmp_path = tmp.name
            export_xp_to_gif(
                xp_path,
                tmp_path,
                fps=fps,
                anim=anim_idx,
                angle=angle_idx,
                scale=scale,
            )
            return send_file(
                tmp_path,
                mimetype="image/gif",
                download_name=f"{Path(xp_path).stem}_anim{anim_idx}.gif",
            )

        elif export_format == "xp":
            return send_file(
                xp_path,
                mimetype="application/octet-stream",
                download_name=f"{Path(xp_path).stem}.xp",
                as_attachment=True,
            )

        else:
            return jsonify(
                {
                    "error": f"Unknown format: {export_format}",
                    "allowed": ["png", "zip", "gif", "xp"],
                }
            ), 400

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Export failed: {e}"}), 500


# ============================================================================
# GET /api/preview/<job_id>
# ============================================================================


@api_bp.route("/preview/<job_id>", methods=["GET"])
def preview(job_id: str):
    """Render XP visual layer as a PNG preview image.

    Uses _render_core to render layer 2 (visual) of the pipeline output.

    Returns:
        PNG image.
        404 if job or XP file not found.
        500 if font atlas missing or render fails.
    """
    from scripts.pipeline._render_core import (
        find_font_atlas,
        load_font_atlas,
        render_xp_layer_to_png,
    )
    from scripts.pipeline.xp_core import XPFile

    output = _service.status(job_id)
    if output is None:
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    xp_path = str(output.xp_path)
    if not Path(xp_path).exists():
        return jsonify({"error": f"XP file not found: {xp_path}"}), 404

    atlas_path = find_font_atlas()
    if not atlas_path:
        return jsonify({"error": "Font atlas not found (assets/fonts/cp437_12x12.png)"}), 500

    try:
        glyphs = load_font_atlas(atlas_path)
        xp = XPFile(xp_path)
        # Render visual layer (layer 2)
        layer_idx = min(2, len(xp.layers) - 1)
        rendered = render_xp_layer_to_png(xp.layers[layer_idx].data, glyphs)
        buf = io.BytesIO()
        rendered.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    except Exception as e:
        return jsonify({"error": f"Preview render failed: {e}"}), 500


# ============================================================================
# POST /api/viewer/load-png
# ============================================================================


@api_bp.route("/viewer/load-png", methods=["POST"])
def viewer_load_png():
    """Slice a PNG sprite sheet into individual viewer frames.

    Expects multipart/form-data with:
        - 'file' (required): PNG image file
        - 'config' (optional): JSON string with slicing parameters:
            angles, frames, projs, cell_w, cell_h, order, origin,
            angle_row_map, max_frames, scale

    Query parameters:
        - offset: Pagination offset (default 0)
        - limit: Max frames to return (default 256, max 256)

    If config is missing, runs grid inference. Returns 400 with
    actionable diagnostics if inference is ambiguous.

    Returns:
        JSON with shared response shape:
        {frames, angles, anims, projs, metadata, truncated,
         total_frames, returned_frames, next_offset}
    """
    from scripts.pipeline.web_ui.sprite_viewer.viewer_loaders import (
        load_png_frames,
    )

    if "file" not in request.files:
        return jsonify({"error": "No file field in request"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # [SEC-03] Sanitize filename
    sanitized = secure_filename(uploaded.filename)
    if not sanitized:
        return jsonify({"error": "Invalid filename"}), 400

    # Parse optional config from multipart field
    config = None
    config_str = request.form.get("config")
    if config_str:
        try:
            config = json.loads(config_str)
        except json.JSONDecodeError as e:
            return jsonify(
                {
                    "error": f"Invalid config JSON: {e}",
                }
            ), 400

    # Parse pagination params
    offset = int(request.args.get("offset", "0"))
    limit = int(request.args.get("limit", "256"))

    # Save uploaded file to temp location
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    file_id = str(uuid.uuid4())
    ext = Path(sanitized).suffix.lower() or ".png"
    dest = upload_dir / f"{file_id}{ext}"
    uploaded.save(str(dest))

    try:
        result = load_png_frames(
            str(dest),
            config=config,
            offset=offset,
            limit=limit,
        )
        return jsonify(result), 200
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"PNG load failed: {e}"}), 500


# ============================================================================
# POST /api/viewer/load-xp
# ============================================================================


@api_bp.route("/viewer/load-xp", methods=["POST"])
def viewer_load_xp():
    """Render an XP sprite file into individual viewer frames.

    Expects multipart/form-data with:
        - 'file' (required): .xp sprite file

    Query parameters:
        - offset: Pagination offset (default 0)
        - limit: Max frames to return (default 256, max 256)
        - scale: Scale factor (default 1)

    Returns:
        JSON with shared response shape:
        {frames, angles, anims, projs, metadata, truncated,
         total_frames, returned_frames, next_offset}
    """
    from scripts.pipeline.web_ui.sprite_viewer.viewer_loaders import (
        load_xp_frames,
    )

    if "file" not in request.files:
        return jsonify({"error": "No file field in request"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # [SEC-03] Sanitize filename (extension used for logging only here)
    sanitized = secure_filename(uploaded.filename)
    if not sanitized:
        return jsonify({"error": "Invalid filename"}), 400

    # Parse pagination and scale params
    offset = int(request.args.get("offset", "0"))
    limit = int(request.args.get("limit", "256"))
    scale = int(request.args.get("scale", "1"))

    # Save uploaded file to temp location
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    file_id = str(uuid.uuid4())
    dest = upload_dir / f"{file_id}.xp"
    uploaded.save(str(dest))

    try:
        result = load_xp_frames(
            str(dest),
            offset=offset,
            limit=limit,
            scale=scale,
        )
        return jsonify(result), 200
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"XP load failed: {e}"}), 500


# ============================================================================
# POST /api/viewer/load-xp-path
# ============================================================================


@api_bp.route("/viewer/load-xp-path", methods=["POST"])
def viewer_load_xp_path():
    """Load an XP file from a server-side path (no upload needed).

    Expects JSON with:
        - 'path' (required): Server-side path to .xp file
        - 'scale' (optional): Scale factor (default 1)

    Returns:
        JSON with frame data for the sprite viewer.
    """
    from scripts.pipeline.web_ui.sprite_viewer.viewer_loaders import (
        load_xp_frames,
    )

    data = request.get_json(silent=True) or {}
    xp_path = data.get("path")
    if not xp_path:
        return jsonify({"error": "Missing 'path' field"}), 400

    if not _is_safe_path(xp_path):
        return jsonify({"error": "Invalid path"}), 403

    if not Path(xp_path).exists():
        return jsonify({"error": f"File not found: {xp_path}"}), 404

    scale = int(data.get("scale", 1))

    try:
        result = load_xp_frames(xp_path, offset=0, limit=256, scale=scale)
        return jsonify(result), 200
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"XP load failed: {e}"}), 500


# ============================================================================
# Workbench endpoints [FLOW:WORKBENCH]
# ============================================================================

# --- Session management ---


@api_bp.route("/workbench/start-session", methods=["POST"])
def wb_start_session():
    """Create a new workbench session with grid dimensions.

    Expects JSON: {angles, frames, projs, cell_w, cell_h}
    frames can be int (single anim) or list[int] (multi-anim).

    Returns: {job_id}
    """
    data = request.get_json(silent=True) or {}
    angles = int(data.get("angles", 1))
    projs = int(data.get("projs", 1))
    cell_w = int(data.get("cell_w", 48))
    cell_h = int(data.get("cell_h", 48))

    frames_raw = data.get("frames", [1])
    if isinstance(frames_raw, int):
        frames = [frames_raw]
    elif isinstance(frames_raw, str):
        frames = [int(x.strip()) for x in frames_raw.split(",") if x.strip()]
    else:
        frames = [int(f) for f in frames_raw]

    if angles < 1 or any(f < 1 for f in frames):
        return jsonify({"error": "angles and frames must be >= 1"}), 400

    session = create_session(angles, frames, projs, cell_w, cell_h)
    return jsonify({"job_id": session.job_id}), 201


@api_bp.route("/workbench/session/<job_id>", methods=["GET"])
def wb_get_session(job_id: str):
    """Get workbench session status.

    Returns: {job_id, frame_count, op_count, redo_count}
    """
    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    return jsonify(
        {
            "job_id": session.job_id,
            "frame_count": session.frame_count,
            "op_count": session.op_count,
            "redo_count": session.redo_count,
        }
    ), 200


@api_bp.route("/workbench/load-xp", methods=["POST"])
def wb_load_xp():
    """Upload an XP file and create a session pre-populated with its frames.

    Expects multipart/form-data with:
        - 'file' (required): .xp sprite file

    Returns: {job_id, angles, anims, projs, cell_w, cell_h, populated, thumbnails}
    """
    if "file" not in request.files:
        return jsonify({"error": "No file field in request"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    sanitized = secure_filename(uploaded.filename)
    if not sanitized:
        return jsonify({"error": "Invalid filename"}), 400

    # Save uploaded XP to temp location
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    file_id = str(uuid.uuid4())
    dest = upload_dir / f"{file_id}.xp"
    uploaded.save(str(dest))

    try:
        from scripts.pipeline.web_ui.sprite_viewer.viewer_loaders import (
            load_xp_frames,
        )

        result = load_xp_frames(str(dest), offset=0, limit=256, scale=1)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"XP load failed: {e}"}), 500

    angles = result.get("angles", 1)
    anims = result.get("anims", [1])
    projs = result.get("projs", 1)
    meta = result.get("metadata", {})

    # For XP uploads, cell_w from viewer_loaders may be None (unknown).
    # Derive from first frame pixel dimensions or fall back to 48.
    raw_cell_w = meta.get("cell_w")
    raw_cell_h = meta.get("cell_h")
    if raw_cell_w is not None and raw_cell_h is not None:
        cell_w = int(raw_cell_w)
        cell_h = int(raw_cell_h)
    else:
        # Derive from first rendered frame dimensions
        first_frames = result.get("frames", [])
        if first_frames and first_frames[0].get("width"):
            cell_w = first_frames[0]["width"]
            cell_h = first_frames[0]["height"]
        else:
            cell_w = 48
            cell_h = 48

    # Create a session with the XP geometry
    session = create_session(angles, anims, projs, cell_w, cell_h)

    # Decode frame images and populate cells
    frame_images = []
    for frame in result.get("frames", []):
        if not frame or not frame.get("data"):
            continue
        img_data = base64.b64decode(frame["data"])
        img = Image.open(io.BytesIO(img_data))
        img.load()
        frame_images.append(img)

    pop_result = session.populate_from_images(frame_images)

    return jsonify(
        {
            "job_id": session.job_id,
            "angles": angles,
            "anims": anims,
            "projs": projs,
            "cell_w": cell_w,
            "cell_h": cell_h,
            "populated": pop_result.get("populated", 0),
            "frame_count": session.frame_count,
            "thumbnails": pop_result.get("thumbnails", {}),
        }
    ), 201


@api_bp.route("/workbench/load-from-job", methods=["POST"])
def wb_load_from_job():
    """Create a workbench session from a completed pipeline job.

    Expects JSON with 'job_id' (pipeline job ID from /api/run).
    Loads the XP file from the server-side path (no upload needed).

    Returns: {job_id, angles, anims, projs, cell_w, cell_h, populated, thumbnails}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    output = _service.status(job_id)
    if output is None:
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    xp_path = str(output.xp_path)
    if not Path(xp_path).exists():
        return jsonify({"error": f"XP file not found: {xp_path}"}), 404

    try:
        from scripts.pipeline.web_ui.sprite_viewer.viewer_loaders import (
            load_xp_frames,
        )

        result = load_xp_frames(xp_path, offset=0, limit=256, scale=1)
    except Exception as e:
        return jsonify({"error": f"XP load failed: {e}"}), 500

    # Use XP metadata as baseline, but allow request body overrides
    # (wizard passes user-configured values that may differ from XP metadata)
    xp_angles = result.get("angles", 1)
    xp_anims = result.get("anims", [1])
    projs = result.get("projs", 1)

    # Resolve cell_w/cell_h via priority chain (AMD-1):
    # 1. Pipeline metadata render_resolution (authoritative)
    # 2. Request body cell_w/cell_h (user override)
    # 3. Fail-closed (422)
    pipeline_meta = (output.metadata or {}) if output.metadata else {}
    render_res = pipeline_meta.get("render_resolution")

    req_cell_w = data.get("cell_w")
    req_cell_h = data.get("cell_h")

    if render_res is not None:
        cell_w = int(render_res)
        cell_h = int(render_res)
    elif req_cell_w is not None and req_cell_h is not None:
        cell_w = int(req_cell_w)
        cell_h = int(req_cell_h)
    else:
        return jsonify({
            "error": "Cannot determine cell dimensions for this job. "
                     "Pipeline metadata lacks render_resolution and no "
                     "cell_w/cell_h override provided."
        }), 422

    req_angles = data.get("angles")
    req_frames = data.get("frames")
    if req_angles and isinstance(req_angles, int) and req_angles > 0:
        angles = req_angles
    else:
        angles = xp_angles
    if req_frames:
        if isinstance(req_frames, str):
            anims = [int(x) for x in req_frames.split(",") if x.strip().isdigit()]
        elif isinstance(req_frames, list):
            anims = [int(x) for x in req_frames if isinstance(x, (int, float))]
        else:
            anims = xp_anims
        if not anims:
            anims = xp_anims
    else:
        anims = xp_anims

    session = create_session(angles, anims, projs, cell_w, cell_h)

    frame_images = []
    for frame in result.get("frames", []):
        if not frame or not frame.get("data"):
            continue
        img_data = base64.b64decode(frame["data"])
        img = Image.open(io.BytesIO(img_data))
        img.load()
        frame_images.append(img)

    pop_result = session.populate_from_images(frame_images)

    # Populate native XP cell data for lossless editing
    try:
        from scripts.pipeline.xp_core import XPFile

        xp = XPFile(xp_path)
        if xp.layers and len(xp.layers) >= 3:
            layer_w = xp.layers[0].width
            layer_h = xp.layers[0].height
            # B3 fix: use XP layer width as authoritative for indexing.
            # The grid session's total_cols should match layer_w, but if
            # there's a mismatch (e.g. stale metadata), layer_w is truth.
            total_cols = layer_w
            if total_cols != sum(anims) * projs:
                # Layout mismatch — mapping may be unsafe; skip native cells
                pass
            else:
                for idx, cell in enumerate(session.grid):
                    row = idx // total_cols
                    col = idx % total_cols
                    if col < layer_w and row < layer_h:
                        cell_layers = []
                        for layer in xp.layers:
                            # XPLayer.data is row-major: data[row][col]
                            # Cell format: (glyph, (fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b))
                            c = layer.data[row][col]
                            cell_layers.append((c[0], c[1], c[2]))
                        cell.xp_cells = cell_layers
    except Exception:
        pass  # Native cell data is optional; thumbnails still work

    # Compute layer count from first cell with native data
    layer_count = 0
    if session.has_native_cells:
        for cell in session.grid:
            if cell.xp_cells:
                layer_count = len(cell.xp_cells)
                break

    # Auto-persist the new session
    save_session_debounced(session)

    return jsonify(
        {
            "job_id": session.job_id,
            "angles": angles,
            "anims": anims,
            "projs": projs,
            "cell_w": cell_w,
            "cell_h": cell_h,
            "populated": pop_result.get("populated", 0),
            "frame_count": session.frame_count,
            "thumbnails": pop_result.get("thumbnails", {}),
            "has_native_cells": session.has_native_cells,
            "layer_count": layer_count,
        }
    ), 201


# --- Cell data API (native XP editing) ---


@api_bp.route("/workbench/<job_id>/cell/<int:idx>/data", methods=["GET"])
def wb_cell_data_read(job_id: str, idx: int):
    """Read native cell data for a grid cell across all XP layers."""
    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404
    data = session.get_cell_data(idx)
    if data is None:
        return jsonify({"error": f"No native cell data for index {idx}"}), 404
    return jsonify(data)


@api_bp.route("/workbench/<job_id>/cell/<int:idx>/data", methods=["PUT"])
def wb_cell_data_write(job_id: str, idx: int):
    """Update a single layer's cell data.

    Expects JSON: {layer: int, glyph: int, fg: "#rrggbb", bg: "#rrggbb"}
    """
    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    body = request.get_json(silent=True) or {}
    layer = body.get("layer", 2)  # default to visual layer
    glyph = body.get("glyph", 0)
    fg_hex = body.get("fg", "#ffffff")
    bg_hex = body.get("bg", "#000000")

    def hex_to_rgb(h):
        h = h.lstrip("#")
        if len(h) != 6:
            raise ValueError(f"Invalid hex color: #{h}")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    try:
        fg_rgb = hex_to_rgb(fg_hex)
        bg_rgb = hex_to_rgb(bg_hex)
    except (ValueError, IndexError) as e:
        return jsonify({"error": f"Invalid color: {e}"}), 400

    ok = session.set_cell_data(idx, layer, glyph, fg_rgb, bg_rgb)
    if not ok:
        return jsonify({"error": f"Failed to update cell {idx} layer {layer}"}), 400
    save_session_debounced(session)
    return jsonify({"ok": True, "cell": idx, "layer": layer})


@api_bp.route("/workbench/<job_id>/layer/<int:layer_idx>", methods=["GET"])
def wb_layer_data(job_id: str, layer_idx: int):
    """Read all cells for a specific XP layer."""
    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    cells = []
    for cell in session.grid:
        if cell.xp_cells and layer_idx < len(cell.xp_cells):
            glyph, fg, bg = cell.xp_cells[layer_idx]
            cells.append({
                "glyph": glyph,
                "fg": "#{:02x}{:02x}{:02x}".format(*fg),
                "bg": "#{:02x}{:02x}{:02x}".format(*bg),
            })
        else:
            cells.append(None)
    return jsonify({"layer": layer_idx, "cells": cells})


# --- Session persistence ---


@api_bp.route("/workbench/sessions", methods=["GET"])
def wb_list_sessions():
    """List all saved workbench sessions."""
    try:
        return jsonify({"sessions": list_sessions()})
    except Exception as e:
        return jsonify({"error": f"Failed to list sessions: {e}", "sessions": []}), 500


@api_bp.route("/workbench/sessions/<job_id>", methods=["DELETE"])
def wb_delete_session(job_id: str):
    """Delete a saved session."""
    if delete_session(job_id):
        return jsonify({"ok": True, "deleted": job_id})
    return jsonify({"error": f"Session not found: {job_id}"}), 404


# --- Source management ---


@api_bp.route("/workbench/store-source", methods=["POST"])
def wb_store_source():
    """Upload and store a source image for extraction.

    Expects multipart/form-data with 'file' field.

    Returns: {source_id}
    """
    if "file" not in request.files:
        return jsonify({"error": "No file field"}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    sanitized = secure_filename(uploaded.filename)
    if not sanitized:
        return jsonify({"error": "Invalid filename"}), 400

    try:
        img = Image.open(uploaded.stream)
        img.load()
    except Exception as e:
        return jsonify({"error": f"Invalid image: {e}"}), 400

    source_id = str(uuid.uuid4())

    # Save to upload dir for persistence
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    ext = Path(sanitized).suffix.lower() or ".png"
    dest = upload_dir / f"{source_id}{ext}"
    img.save(str(dest))

    # Store in any active sessions (source is global, not session-bound)
    from scripts.pipeline.web_api.workbench_session import _sessions

    for session in _sessions.values():
        session.store_source(source_id, img.copy(), filename=sanitized)

    return jsonify({"source_id": source_id}), 201


@api_bp.route("/workbench/extract-sprites", methods=["POST"])
def wb_extract_sprites():
    """Extract sprite bounding boxes from a stored source image.

    Expects JSON: {source_id, alpha_threshold?, min_size?, bg_color?}

    Returns: {sprites: [{bbox, width, height}], method, filtered_count}
    """
    data = request.get_json(silent=True) or {}
    source_id = data.get("source_id")
    if not source_id:
        return jsonify({"error": "Missing source_id"}), 400

    # Find source image in upload dir
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    candidates = list(upload_dir.glob(f"{source_id}.*"))
    if not candidates:
        return jsonify({"error": f"Source not found: {source_id}"}), 404

    try:
        img = Image.open(str(candidates[0]))
        img.load()
    except Exception as e:
        return jsonify({"error": f"Failed to load source: {e}"}), 500

    alpha_threshold = int(data.get("alpha_threshold", 128))
    min_size = int(data.get("min_size", 16))
    bg_color = data.get("bg_color")

    try:
        result = extract_sprites_from_source(
            img,
            alpha_threshold=alpha_threshold,
            min_size=min_size,
            bg_color=bg_color,
        )
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": f"Extraction failed: {e}"}), 500


@api_bp.route("/workbench/load-server-image", methods=["POST"])
def wb_load_server_image():
    """Load an image from a server-side path as a source.

    Expects JSON: {image_path}

    Returns: {source_id, data_url, width, height}
    """
    data = request.get_json(silent=True) or {}
    image_path = data.get("image_path")
    if not image_path:
        return jsonify({"error": "Missing image_path"}), 400

    if not _is_safe_path(image_path):
        return jsonify({"error": "Invalid path"}), 403

    if not Path(image_path).exists():
        return jsonify({"error": f"File not found: {image_path}"}), 404

    try:
        img = Image.open(image_path)
        img.load()
    except Exception as e:
        return jsonify({"error": f"Failed to load image: {e}"}), 400

    source_id = str(uuid.uuid4())

    # Copy to upload dir
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    ext = Path(image_path).suffix.lower() or ".png"
    dest = upload_dir / f"{source_id}{ext}"
    img.save(str(dest))

    # Generate data URL for frontend display
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(
        "ascii"
    )

    # Store in active sessions
    from scripts.pipeline.web_api.workbench_session import _sessions

    for session in _sessions.values():
        session.store_source(source_id, img.copy(), filename=Path(image_path).name)

    return jsonify(
        {
            "source_id": source_id,
            "data_url": data_url,
            "width": img.width,
            "height": img.height,
        }
    ), 200


@api_bp.route("/workbench/merge-sources", methods=["POST"])
def wb_merge_sources():
    """Merge multiple source images and extract combined sprite list.

    Expects multipart/form-data with:
        - 'files' (multiple): Image files to merge
        - 'alpha_threshold' (optional): int, default 128
        - 'min_size' (optional): int, default 16

    Returns: {sprites, total_sprites, sources}
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    alpha_threshold = int(request.form.get("alpha_threshold", "128"))
    min_size = int(request.form.get("min_size", "16"))

    all_sprites = []
    sources = []

    for uploaded in files:
        if uploaded.filename == "":
            continue
        sanitized = secure_filename(uploaded.filename)
        if not sanitized:
            continue

        try:
            img = Image.open(uploaded.stream)
            img.load()
        except Exception:
            continue

        source_id = str(uuid.uuid4())
        upload_dir = Path(current_app.config["UPLOAD_DIR"])
        ext = Path(sanitized).suffix.lower() or ".png"
        dest = upload_dir / f"{source_id}{ext}"
        img.save(str(dest))

        result = extract_sprites_from_source(
            img,
            alpha_threshold=alpha_threshold,
            min_size=min_size,
        )
        for sprite in result["sprites"]:
            sprite["source_id"] = source_id
        all_sprites.extend(result["sprites"])
        sources.append({"source_id": source_id, "filename": sanitized})

    return jsonify(
        {
            "sprites": all_sprites,
            "total_sprites": len(all_sprites),
            "sources": sources,
        }
    ), 200


# --- Frame management ---


@api_bp.route("/workbench/populate-frames", methods=["POST"])
def wb_populate_frames():
    """Place sprites from a source into grid cells.

    Expects JSON: {job_id, source_id, sprites, target_indices?}

    Returns: {populated, frame_count, thumbnails}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    source_id = data.get("source_id")
    sprites = data.get("sprites", [])
    target_indices = data.get("target_indices")

    # Ensure source is loaded into session
    if source_id and source_id not in session.sources:
        upload_dir = Path(current_app.config["UPLOAD_DIR"])
        candidates = list(upload_dir.glob(f"{source_id}.*"))
        if candidates:
            try:
                img = Image.open(str(candidates[0]))
                img.load()
                session.store_source(source_id, img)
            except Exception:
                pass

    result = session.populate_from_sprites(source_id, sprites, target_indices)
    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


# --- Edit operations ---


@api_bp.route("/workbench/undo", methods=["POST"])
def wb_undo():
    """Undo the most recent workbench operation.

    Expects JSON: {job_id}

    Returns: {undone_op_id, warning?, thumbnails?}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    result = session.undo()
    return jsonify(result), 200


@api_bp.route("/workbench/redo", methods=["POST"])
def wb_redo():
    """Redo the most recently undone operation.

    Expects JSON: {job_id}

    Returns: {redone_op_id, warning?, thumbnails?}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    result = session.redo()
    return jsonify(result), 200


@api_bp.route("/workbench/transform-cells", methods=["POST"])
def wb_transform_cells():
    """Apply flip/rotate transforms to selected cells.

    Expects JSON: {job_id, targets: [{angle, anim, frame, proj}], transform: {flip_h?, flip_v?, rotate_deg?}}

    Returns: {thumbnails}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    targets = data.get("targets", [])
    transform = data.get("transform", {})

    if not targets:
        return jsonify({"error": "No target cells specified"}), 400

    result = session.transform_cells(targets, transform)
    return jsonify(result), 200


@api_bp.route("/workbench/swap-cells", methods=["POST"])
def wb_swap_cells():
    """Swap two grid cells.

    Expects JSON: {job_id, cell_a: {angle, anim, frame, proj}, cell_b: {angle, anim, frame, proj}}

    Returns: {thumbnails}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    cell_a = data.get("cell_a")
    cell_b = data.get("cell_b")
    if not cell_a or not cell_b:
        return jsonify({"error": "Both cell_a and cell_b are required"}), 400

    result = session.swap_cells(cell_a, cell_b)
    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@api_bp.route("/workbench/fill-from-slot", methods=["POST"])
def wb_fill_from_slot():
    """Copy one cell's content into multiple target cells.

    Expects JSON: {job_id, source: {angle, anim, frame, proj}, targets: [{angle, anim, frame, proj}]}

    Returns: {thumbnails}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    source = data.get("source")
    targets = data.get("targets", [])
    if not source:
        return jsonify({"error": "Missing source cell"}), 400

    result = session.fill_from_slot(source, targets)
    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@api_bp.route("/workbench/import-external", methods=["POST"])
def wb_import_external():
    """Import an external image file into target cells.

    Expects multipart/form-data with:
        - 'file': Image file
        - 'job_id': Session ID
        - 'targets': JSON string of [{angle, anim, frame, proj}]
        - 'blend_mode': 'replace' (default) or 'overlay'
        - 'fit_mode': 'nearest_stretch' (default)

    Returns: {thumbnails}
    """
    if "file" not in request.files:
        return jsonify({"error": "No file field"}), 400

    uploaded = request.files["file"]
    job_id = request.form.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    try:
        img = Image.open(uploaded.stream)
        img.load()
    except Exception as e:
        return jsonify({"error": f"Invalid image: {e}"}), 400

    targets_str = request.form.get("targets", "[]")
    try:
        targets = json.loads(targets_str)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid targets JSON"}), 400

    blend_mode = request.form.get("blend_mode", "replace")
    fit_mode = request.form.get("fit_mode", "nearest_stretch")

    result = session.import_external(img, targets, blend_mode, fit_mode)
    return jsonify(result), 200


# --- Export ---


@api_bp.route("/workbench/export-xp", methods=["POST"])
def wb_export_xp():
    """Export the workbench grid to an XP file.

    Expects JSON: {job_id, name, repair_12px?, skip_reflections?}

    Returns: {xp_path, name, frame_count, grid_layout, repair_available?, suggested_w?, suggested_h?}
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    session = get_session(job_id)
    if session is None:
        return jsonify({"error": f"Session not found: {job_id}"}), 404

    name = data.get("name", "workbench_export")
    repair_12px = bool(data.get("repair_12px", False))
    skip_reflections = bool(data.get("skip_reflections", False))

    output_dir = Path(current_app.config["UPLOAD_DIR"])
    try:
        result = session.export_to_xp(
            name,
            output_dir,
            repair_12px=repair_12px,
            skip_reflections=skip_reflections,
        )
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": f"Export failed: {e}"}), 500


@api_bp.route("/workbench/download-xp", methods=["GET"])
def wb_download_xp():
    """Download an XP file by path.

    Query parameters:
        - path: Path to the XP file (from export-xp result)

    Returns: Binary XP file.
    """
    xp_path = request.args.get("path")
    if not xp_path:
        return jsonify({"error": "Missing 'path' query parameter"}), 400

    if not _is_safe_path(xp_path):
        return jsonify({"error": "Invalid path"}), 403

    if not Path(xp_path).exists():
        return jsonify({"error": f"File not found: {xp_path}"}), 404

    return send_file(
        xp_path,
        mimetype="application/octet-stream",
        download_name=Path(xp_path).name,
        as_attachment=True,
    )


@api_bp.route("/workbench/xp-tool-command", methods=["POST"])
def wb_xp_tool_command():
    """Generate a command to open an XP file in xp_tool.

    Expects JSON: {xp_path}

    Returns: {command}
    """
    data = request.get_json(silent=True) or {}
    xp_path = data.get("xp_path")
    if not xp_path:
        return jsonify({"error": "Missing xp_path"}), 400

    if not _is_safe_path(xp_path):
        return jsonify({"error": "Invalid path"}), 403

    command = f"python3 -m scripts.pipeline.xp_tool {xp_path}"
    return jsonify({"command": command}), 200


# ============================================================================
# Config schema endpoints [FLOW:CONFIG]
# ============================================================================


@api_bp.route("/config/schema", methods=["GET"])
def get_config_schema():
    """Return the UI form specification for PipelineConfig.

    The response contains field specs grouped by section, each with
    widget type, choices, min/max, and description. Used by the
    workbench and wizard to render config forms dynamically.

    Returns:
        JSON list of field specifications.
    """
    specs = generate_ui_form(PipelineConfig)
    return jsonify({"fields": specs}), 200


@api_bp.route("/config/validate", methods=["POST"])
def validate_config():
    """Validate a PipelineConfig from JSON.

    Expects JSON matching PipelineConfig.to_dict() shape.

    Returns:
        JSON with {valid: true, config: {...}} or {valid: false, error: "..."}.
    """
    data = request.get_json(silent=True) or {}

    try:
        config = PipelineConfig.from_dict(data)
        return jsonify({"valid": True, "config": config.to_dict()}), 200
    except (ValueError, TypeError) as exc:
        return jsonify({"valid": False, "error": str(exc)}), 400


@api_bp.route("/config/defaults", methods=["GET"])
def get_config_defaults():
    """Return the default PipelineConfig values.

    Returns:
        JSON with the default config dict.
    """
    config = PipelineConfig()
    return jsonify({"config": config.to_dict()}), 200


# ============================================================================
# Branch endpoints [FLOW:BRANCH]
# ============================================================================


def _get_manifest(job_id: str) -> JobManifest | None:
    """Look up a manifest from memory or try loading from disk.

    Searches both the web UPLOAD_DIR and the pipeline STAGING_DIR so that
    manifests persisted by the pipeline (e.g. auto-mode branch trees) are
    discoverable by the branch viewer API.
    """
    if job_id in _manifests:
        return _manifests[job_id]

    # Try loading from staging directory (web uploads)
    staging = Path(current_app.config.get("UPLOAD_DIR", "/tmp"))
    search_dirs = [staging]

    # Also check the pipeline's own staging directory
    try:
        from scripts.pipeline.staging import STAGING_DIR
        if STAGING_DIR not in search_dirs:
            search_dirs.append(STAGING_DIR)
    except ImportError:
        pass

    for base_dir in search_dirs:
        manifest_path = base_dir / job_id / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = JobManifest.load_json(manifest_path)
                _manifests[job_id] = manifest
                return manifest
            except Exception:
                continue
    return None


def _save_manifest(manifest: JobManifest) -> None:
    """Persist a manifest to both memory and disk."""
    _manifests[manifest.job_id] = manifest
    staging = Path(current_app.config.get("UPLOAD_DIR", "/tmp"))
    manifest_dir = staging / manifest.job_id
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest.save_json(manifest_dir / "manifest.json")


def _bootstrap_manifest_from_run(config: AssetJobConfig, output) -> JobManifest:
    """Create a minimal branch manifest for a completed pipeline run.

    This keeps `/api/branches/<job_id>` functional even before full multi-track
    orchestration is wired into runtime stages.
    """
    settings_snapshot = (
        deepcopy(config.pipeline_config)
        if isinstance(config.pipeline_config, dict)
        else {}
    )
    process_settings = (
        settings_snapshot.get("process_settings", {})
        if isinstance(settings_snapshot, dict)
        else {}
    )
    process_mode = str(process_settings.get("mode", "auto")).strip().lower()

    root_id = f"node_upload_{uuid.uuid4().hex[:8]}"
    final_id = f"node_assemble_{uuid.uuid4().hex[:8]}"

    source_path = str(config.source_path or "")
    xp_path = str(output.xp_path)
    source_cell_px = process_settings.get("source_cell_px")
    try:
        source_cell_px = int(source_cell_px) if source_cell_px is not None else None
    except (TypeError, ValueError):
        source_cell_px = None

    root_node = BranchNode(
        id=root_id,
        parent_id=None,
        stage=BranchStage.UPLOAD,
        track_id="upload",
        settings_snapshot=settings_snapshot,
        artifact_kind="image",
        artifact_path=source_path,
        quality_score=1.0,
        status=BranchStatus.ACTIVE,
    )
    final_node = BranchNode(
        id=final_id,
        parent_id=root_id,
        stage=BranchStage.ASSEMBLE,
        track_id=process_mode if process_mode else "quality",
        settings_snapshot=settings_snapshot,
        artifact_kind="xp_file",
        artifact_path=xp_path,
        quality_score=1.0,
        source_cell_px=source_cell_px,
        status=BranchStatus.PROMOTED,
    )

    tree = BranchTree()
    tree.add_node(root_node)
    tree.add_node(final_node)

    staging = Path(current_app.config.get("UPLOAD_DIR", "/tmp"))
    root_thumb = generate_thumbnail(source_path, output.job_id, root_id, staging)
    final_thumb = generate_thumbnail(xp_path, output.job_id, final_id, staging)
    if root_thumb:
        root_node.thumbnail_path = root_thumb
    if final_thumb:
        final_node.thumbnail_path = final_thumb

    return JobManifest(
        job_id=output.job_id,
        input_path=source_path,
        branch_tree=tree,
    )


@api_bp.route("/branches/<job_id>", methods=["GET"])
def get_job_branches(job_id: str):
    """Return the branch tree manifest for a job.

    Query parameters:
        include_pruned: "true" to include pruned branches (default: all).

    Returns:
        JSON with the full branch tree.
        404 if no manifest found.
    """
    manifest = _get_manifest(job_id)
    if manifest is None:
        return jsonify({"error": f"No branch manifest for job: {job_id}"}), 404

    return jsonify(
        {
            "job_id": manifest.job_id,
            "input_path": manifest.input_path,
            "version": manifest.version,
            "created_at": manifest.created_at.isoformat(),
            "branch_tree": manifest.branch_tree.to_dict(),
            "pruned_warnings": manifest.branch_tree.pruned_warnings,
        }
    ), 200


@api_bp.route("/branches/<job_id>/promote/<branch_id>", methods=["POST"])
def promote_job_branch(job_id: str, branch_id: str):
    """Promote a branch as the active candidate in a job manifest.

    Demotes any previously promoted branch back to active.

    Returns:
        JSON with updated branch status.
        404 if manifest or branch not found.
    """
    manifest = _get_manifest(job_id)
    if manifest is None:
        return jsonify({"error": f"No branch manifest for job: {job_id}"}), 404

    tree = manifest.branch_tree
    if branch_id not in tree.nodes:
        return jsonify({"error": f"Branch not found: {branch_id}"}), 404

    try:
        tree.promote(branch_id)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    _save_manifest(manifest)

    node = tree.nodes[branch_id]
    return jsonify(
        {
            "job_id": job_id,
            "branch_id": branch_id,
            "status": node.status.value,
        }
    ), 200


@api_bp.route("/branches/<job_id>/prune/<branch_id>", methods=["POST"])
def prune_job_branch(job_id: str, branch_id: str):
    """Mark a branch as pruned in a job manifest.

    Promoted branches cannot be pruned (demote first).

    Returns:
        JSON with updated branch status.
        404 if manifest or branch not found.
    """
    manifest = _get_manifest(job_id)
    if manifest is None:
        return jsonify({"error": f"No branch manifest for job: {job_id}"}), 404

    tree = manifest.branch_tree
    if branch_id not in tree.nodes:
        return jsonify({"error": f"Branch not found: {branch_id}"}), 404

    try:
        tree.prune(branch_id)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    _save_manifest(manifest)

    node = tree.nodes[branch_id]
    return jsonify(
        {
            "job_id": job_id,
            "branch_id": branch_id,
            "status": node.status.value,
        }
    ), 200


@api_bp.route("/branches/<job_id>/<branch_id>/thumbnail", methods=["GET"])
def get_branch_thumbnail(job_id: str, branch_id: str):
    """Return the thumbnail PNG for one branch node.

    Looks for the thumbnail at the path stored in the branch node,
    falling back to staging/thumbnails/{job_id}/{branch_id}.png.

    Returns:
        PNG image file.
        404 if manifest, branch, or thumbnail not found.
    """
    manifest = _get_manifest(job_id)
    if manifest is None:
        return jsonify({"error": f"No branch manifest for job: {job_id}"}), 404

    tree = manifest.branch_tree
    if branch_id not in tree.nodes:
        return jsonify({"error": f"Branch not found: {branch_id}"}), 404

    node = tree.nodes[branch_id]

    branch_root = str(_branch_root_dir())

    # Try node's stored thumbnail path first
    if node.thumbnail_path and Path(node.thumbnail_path).exists():
        if not _is_safe_path(node.thumbnail_path, base_dir=branch_root):
            return jsonify({"error": "Invalid thumbnail path"}), 403
        return send_file(
            node.thumbnail_path,
            mimetype="image/png",
            download_name=f"{branch_id}.png",
        )

    # Fallback: staging/thumbnails/{job_id}/{branch_id}.png
    staging = _branch_root_dir()
    fallback = staging / "thumbnails" / job_id / f"{branch_id}.png"
    if fallback.exists():
        return send_file(
            str(fallback),
            mimetype="image/png",
            download_name=f"{branch_id}.png",
        )

    # Lazy generation: generate thumbnail on-demand from artifact
    source_path = None
    if node.artifact_kind == "sprite_list" and node.artifact_refs:
        # Use first extracted sprite as thumbnail source
        for ref in node.artifact_refs:
            ref_path = Path(ref)
            if ref_path.is_file():
                source_path = ref_path
                break
    elif node.artifact_kind in {"xp_file", "image"}:
        artifact = Path(node.artifact_path)
        if artifact.is_file():
            source_path = artifact
    elif node.artifact_kind == "grid_data":
        # Grid detection has no visual artifact — use root node's source image
        for n in tree.nodes.values():
            if n.stage.value == "upload" and Path(n.artifact_path).is_file():
                source_path = Path(n.artifact_path)
                break

    if source_path is not None:
        thumb_path = generate_thumbnail(
            source_path, job_id, branch_id, str(staging)
        )
        if thumb_path and Path(thumb_path).exists():
            return send_file(
                thumb_path,
                mimetype="image/png",
                download_name=f"{branch_id}.png",
            )

    return jsonify({"error": f"Thumbnail not found for branch: {branch_id}"}), 404


@api_bp.route("/branches/<job_id>/<branch_id>/artifact", methods=["GET"])
def get_branch_artifact(job_id: str, branch_id: str):
    """Download the artifact file for one branch node.

    Returns the actual pipeline output (PNG, .xp, etc.) — not a thumbnail.

    Returns:
        File download with appropriate MIME type.
        404 if manifest, branch, or artifact file not found.
    """
    manifest = _get_manifest(job_id)
    if manifest is None:
        return jsonify({"error": f"No branch manifest for job: {job_id}"}), 404

    tree = manifest.branch_tree
    if branch_id not in tree.nodes:
        return jsonify({"error": f"Branch not found: {branch_id}"}), 404

    node = tree.nodes[branch_id]

    # Resolve the actual file to serve based on artifact_kind.
    # sprite_list nodes store a prefix in artifact_path; real files are in artifact_refs.
    # grid_data nodes have no file artifact — return metadata as JSON.
    resolved_path = None
    artifact = Path(node.artifact_path)
    if artifact.is_file():
        resolved_path = artifact
    elif node.artifact_kind == "sprite_list" and node.artifact_refs:
        # Serve first extracted sprite
        for ref in node.artifact_refs:
            ref_path = Path(ref)
            if ref_path.is_file():
                resolved_path = ref_path
                break
    elif node.artifact_kind == "grid_data":
        # Grid detection has no file — return detection metadata as JSON
        return jsonify({
            "track_id": node.track_id,
            "artifact_kind": "grid_data",
            "quality_score": node.quality_score,
            "score_type": getattr(node, "score_type", "confidence"),
            "source_cell_px": getattr(node, "source_cell_px", None),
            "settings": node.settings_snapshot,
        }), 200

    if resolved_path is None:
        return jsonify({"error": f"Artifact file not found: {artifact.name}"}), 404

    if not _is_safe_path(str(resolved_path), base_dir=str(_branch_root_dir())):
        return jsonify({"error": "Invalid artifact path"}), 403

    _MIME_MAP = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".xp": "application/octet-stream",
        ".json": "application/json",
    }
    mime = _MIME_MAP.get(resolved_path.suffix.lower(), "application/octet-stream")
    download_name = f"{node.track_id}_{node.stage.value}{resolved_path.suffix}"

    return send_file(
        str(resolved_path),
        mimetype=mime,
        as_attachment=True,
        download_name=download_name,
    )


@api_bp.route("/branches/<job_id>/download-all", methods=["GET"])
def download_all_branches(job_id: str):
    """Download all branch artifacts as a single ZIP file.

    Includes every non-pruned branch artifact (or all if include_pruned=true).

    Returns:
        ZIP file download.
        404 if no manifest found.
    """
    import zipfile

    manifest = _get_manifest(job_id)
    if manifest is None:
        return jsonify({"error": f"No branch manifest for job: {job_id}"}), 404

    include_pruned = request.args.get("include_pruned", "false").lower() == "true"
    branch_root = str(_branch_root_dir())

    buf = io.BytesIO()
    included_count = 0
    skipped: list[dict[str, str]] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for node in manifest.branch_tree.nodes.values():
            if not include_pruned and node.status == BranchStatus.PRUNED:
                continue
            artifact = Path(node.artifact_path)
            if not artifact.exists():
                skipped.append({
                    "branch_id": node.id,
                    "track_id": node.track_id,
                    "artifact_path": node.artifact_path,
                    "reason": "missing",
                })
            elif not artifact.is_file():
                skipped.append({
                    "branch_id": node.id,
                    "track_id": node.track_id,
                    "artifact_path": node.artifact_path,
                    "reason": "not_a_file",
                })
            elif not _is_safe_path(str(artifact), base_dir=branch_root):
                skipped.append({
                    "branch_id": node.id,
                    "track_id": node.track_id,
                    "artifact_path": node.artifact_path,
                    "reason": "outside_trusted_root",
                })
            else:
                arcname = f"{node.stage.value}/{node.track_id}{artifact.suffix}"
                zf.write(str(artifact), arcname)
                included_count += 1

        if skipped:
            zf.writestr(
                "_skipped_report.json",
                json.dumps(skipped, indent=2),
            )

    if included_count == 0:
        return jsonify({"error": "No downloadable branch artifacts found"}), 404

    buf.seek(0)
    resp = send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"branches_{job_id[:8]}.zip",
    )
    if skipped:
        resp.headers["X-Branch-Skipped-Count"] = str(len(skipped))
    return resp
