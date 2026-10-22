/**
 * app.js -- Web UI step manager for the Asciicker asset pipeline.
 *
 * Implements a 6-step wizard flow:
 *   1. Upload   -- File input, preview
 *   2. Analyze  -- Grid detection via POST /api/analyze
 *   3. Configure -- Pipeline parameter editing
 *   4. Confirm  -- Summary review + POST /api/configure validation
 *   5. Run      -- POST /api/run execution
 *   6. Result   -- Output display, export links
 *
 * State is maintained in an immutable-style WebPipelineState object.
 * toJobConfig() serializes state to the AssetJobConfig schema used by
 * CLI, Textual TUI, and the Flask backend.
 *
 * Tags: [FLOW:WEB-UI] [DATA-CONTRACT:JOB-CONFIG]
 */

"use strict";

// ============================================================================
// Configuration
// ============================================================================

var API_BASE = window.ASSET_API_BASE || "/api";

// Step order -- matches nav li[data-step] attributes
var STEPS = ["upload", "analyze", "configure", "confirm", "run", "result"];

// ============================================================================
// State
// ============================================================================

/**
 * Create a fresh pipeline state object.
 * All mutations return new objects (immutable pattern).
 *
 * @returns {Object} Initial pipeline state
 */
function createInitialState() {
  return Object.freeze({
    currentStep: "upload",
    completedSteps: Object.freeze([]),

    // Upload
    fileId: null,
    filePath: null,
    fileName: null,
    fileSizeBytes: 0,
    previewDataUrl: null,

    // Analyze
    analysis: null,

    // Configure
    config: Object.freeze({
      name: "unnamed",
      asset_type: "custom",
      angles: 1,
      frames: "1",
      render_resolution: 24,
      bg_mode: "key_color",
      bg_tolerance: 8,
      reflection_policy: "",
      explicit_projs: "",
      transparency: false,
      cell_w: "",
      cell_h: "",
      grid_offset_x: 0,
      grid_offset_y: 0,
      order: "angle_major",
      angle_row_map: "",
      extraction_mode: "both",
      extraction_alpha_threshold: 128,
      extraction_bg_color: "",
      extraction_min_size: 16,
      extraction_fill_policy: "fail",
      bg_key_colors: Object.freeze([]),
    }),

    // Confirm
    validationResult: null,

    // Run
    jobId: null,
    runError: null,

    // Result
    result: null,
  });
}

var state = createInitialState();
var layoutState = null;

/**
 * Update state immutably and re-render affected parts.
 *
 * @param {Object} patch - Fields to merge into state
 * @returns {Object} New frozen state
 */
function updateState(patch) {
  var entries = Object.keys(patch).reduce(function (acc, key) {
    acc[key] = patch[key];
    return acc;
  }, {});
  state = Object.freeze(Object.assign({}, state, entries));
  return state;
}

// ============================================================================
// AssetJobConfig Serialization
// ============================================================================

/**
 * Convert current web state to an AssetJobConfig-compatible dict.
 * This is the shared contract with CLI/Textual/MCP adapters.
 *
 * @param {Object} st - Current pipeline state
 * @returns {Object} AssetJobConfig-shaped object
 */
function toJobConfig(st) {
  var cfg = st.config;
  var framesParsed = parseFramesString(cfg.frames);
  var pipelineConfig = (typeof collectWizardPipelineConfig === "function")
    ? collectWizardPipelineConfig()
    : null;

  // Build slice_spec only when user provided explicit grid info
  var sliceSpec = null;
  var hasCellW = cfg.cell_w && parseInt(cfg.cell_w, 10) > 0;
  var hasCellH = cfg.cell_h && parseInt(cfg.cell_h, 10) > 0;
  var hasOffsetX = Number.isFinite(parseInt(cfg.grid_offset_x, 10)) && parseInt(cfg.grid_offset_x, 10) !== 0;
  var hasOffsetY = Number.isFinite(parseInt(cfg.grid_offset_y, 10)) && parseInt(cfg.grid_offset_y, 10) !== 0;
  var hasOrder = cfg.order && cfg.order !== "angle_major";
  var hasRowMap = cfg.angle_row_map && cfg.angle_row_map.trim().length > 0;

  if (hasCellW || hasCellH || hasOffsetX || hasOffsetY || hasOrder || hasRowMap) {
    sliceSpec = {};
    if (hasCellW) { sliceSpec.cell_w_px = parseInt(cfg.cell_w, 10); }
    if (hasCellH) { sliceSpec.cell_h_px = parseInt(cfg.cell_h, 10); }
    if (hasOffsetX) { sliceSpec.margin_x_px = parseInt(cfg.grid_offset_x, 10); }
    if (hasOffsetY) { sliceSpec.margin_y_px = parseInt(cfg.grid_offset_y, 10); }
    if (hasOrder) { sliceSpec.order = cfg.order; }
    if (hasRowMap) {
      sliceSpec.angle_row_map = cfg.angle_row_map.split(",")
        .map(function (s) { return parseInt(s.trim(), 10); })
        .filter(function (n) { return !isNaN(n); });
    }
  }

  if (_wizardSelectionBBox &&
      _wizardSelectionBBox.w > 0 &&
      _wizardSelectionBBox.h > 0) {
    if (!pipelineConfig || typeof pipelineConfig !== "object") {
      pipelineConfig = {};
    }
    if (!pipelineConfig.extract_settings ||
        typeof pipelineConfig.extract_settings !== "object") {
      pipelineConfig.extract_settings = {};
    }
    pipelineConfig.extract_settings.use_selection_roi = true;
    pipelineConfig.extract_settings.selection_roi = {
      x: Math.max(0, parseInt(_wizardSelectionBBox.x, 10) || 0),
      y: Math.max(0, parseInt(_wizardSelectionBBox.y, 10) || 0),
      w: Math.max(1, parseInt(_wizardSelectionBBox.w, 10) || 1),
      h: Math.max(1, parseInt(_wizardSelectionBBox.h, 10) || 1),
    };
  }

  return {
    name: cfg.name || "unnamed",
    asset_type: cfg.asset_type || "custom",
    source_type: "file",
    source_path: st.filePath || null,
    blender_object: null,
    angles: parseInt(cfg.angles, 10) || 1,
    frames: framesParsed,
    projs: 1,
    transparency: cfg.transparency || false,
    normalization: false,
    target_cells_high: 0,
    render_resolution: parseInt(cfg.render_resolution, 10) || 24,
    downscale_algorithm: null,
    template_name: null,
    slice_spec: sliceSpec,
    background: null,
    bg_mode: cfg.bg_mode || "key_color",
    bg_tolerance: parseInt(cfg.bg_tolerance, 10) || 8,
    slice_mode: "auto",
    explicit_projs: (cfg.explicit_projs && parseInt(cfg.explicit_projs, 10) > 0) ? parseInt(cfg.explicit_projs, 10) : null,
    reflection_policy: cfg.reflection_policy || null,
    synthesize_angles: null,
    pre_slice_check: false,
    pre_slice_check_strict: false,
    pixel_perfect_mode: "off",
    keyframe_ranges: null,
    extraction_mode: cfg.extraction_mode || "both",
    extraction_alpha_threshold: parseInt(cfg.extraction_alpha_threshold, 10) || 128,
    extraction_bg_color: cfg.extraction_bg_color || null,
    extraction_min_size: parseInt(cfg.extraction_min_size, 10) || 16,
    extraction_fill_policy: cfg.extraction_fill_policy || "fail",
    bg_key_colors: (cfg.bg_key_colors && cfg.bg_key_colors.length > 0) ? cfg.bg_key_colors.slice() : null,
    pipeline_config: pipelineConfig,
  };
}

/**
 * Parse comma-separated frames string to array of ints.
 *
 * @param {string} framesStr - e.g. "1,8,4"
 * @returns {number[]} Parsed frame counts
 */
function parseFramesString(framesStr) {
  if (!framesStr || typeof framesStr !== "string") {
    return [1];
  }
  var parts = framesStr.split(",")
    .map(function (s) { return s.trim(); })
    .filter(function (s) { return s.length > 0; })
    .map(function (s) { return parseInt(s, 10); })
    .filter(function (n) { return !isNaN(n) && n > 0; });
  return parts.length > 0 ? parts : [1];
}

/**
 * Get the list of fields that comprise AssetJobConfig.
 * Used by contract parity tests.
 *
 * @returns {string[]} Sorted field names
 */
function getJobConfigFields() {
  return [
    "angles", "asset_type", "background", "bg_key_colors",
    "bg_mode", "bg_tolerance",
    "blender_object", "downscale_algorithm", "explicit_projs",
    "extraction_alpha_threshold", "extraction_bg_color",
    "extraction_fill_policy", "extraction_min_size", "extraction_mode",
    "frames", "keyframe_ranges", "name", "pipeline_config",
    "normalization", "pixel_perfect_mode", "pre_slice_check",
    "pre_slice_check_strict", "projs", "reflection_policy",
    "render_resolution", "slice_mode", "slice_spec", "source_path",
    "source_type", "synthesize_angles", "target_cells_high",
    "template_name", "transparency",
  ];
}

// ============================================================================
// Step Navigation
// ============================================================================

/**
 * Navigate to a specific step, respecting guards.
 *
 * @param {string} stepName - Target step name
 * @returns {boolean} Whether navigation succeeded
 */
function goToStep(stepName) {
  var targetIdx = STEPS.indexOf(stepName);
  if (targetIdx < 0) {
    return false;
  }

  // Can only go forward to completed+1, or backward to any completed step
  var currentIdx = STEPS.indexOf(state.currentStep);
  var maxReachable = state.completedSteps.length;

  if (targetIdx > maxReachable) {
    return false;  // Guard: can't skip steps
  }

  updateState({ currentStep: stepName });
  renderNavigation();
  renderPanels();
  return true;
}

/**
 * Mark the current step as completed and advance to next.
 *
 * @returns {boolean} Whether advance succeeded
 */
function advanceStep() {
  var currentIdx = STEPS.indexOf(state.currentStep);
  if (currentIdx < 0 || currentIdx >= STEPS.length - 1) {
    return false;
  }

  var completed = state.completedSteps.slice();
  if (completed.indexOf(state.currentStep) < 0) {
    completed.push(state.currentStep);
  }

  var nextStep = STEPS[currentIdx + 1];
  updateState({
    currentStep: nextStep,
    completedSteps: Object.freeze(completed),
  });

  renderNavigation();
  renderPanels();
  return true;
}

// ============================================================================
// Rendering
// ============================================================================

/**
 * Update the step navigation bar to reflect current state.
 */
function renderNavigation() {
  var navItems = document.querySelectorAll("#step-nav li");
  navItems.forEach(function (li) {
    var step = li.getAttribute("data-step");
    var idx = STEPS.indexOf(step);

    li.className = "";
    if (step === state.currentStep) {
      li.className = "active";
    } else if (state.completedSteps.indexOf(step) >= 0) {
      li.className = "done";
    } else {
      li.className = "locked";
    }
  });
}

// Wizard configure viewer panel instance
var _wizardViewerPanel = null;
var _wizardSelectionBBox = null;

function readGridOverlayFromForm() {
  var cellW = parseInt(getFormValue("cfg-cell-w", "0"), 10) || 0;
  var cellH = parseInt(getFormValue("cfg-cell-h", "0"), 10) || 0;
  var offsetX = parseInt(getFormValue("cfg-grid-offset-x", "0"), 10) || 0;
  var offsetY = parseInt(getFormValue("cfg-grid-offset-y", "0"), 10) || 0;
  return {
    enabled: cellW > 0 && cellH > 0,
    cell_w_px: cellW,
    cell_h_px: cellH,
    offset_x_px: offsetX,
    offset_y_px: offsetY,
  };
}

function syncWizardGridOverlay() {
  if (!_wizardViewerPanel || typeof _wizardViewerPanel.setGridOverlay !== "function") {
    return;
  }
  _wizardViewerPanel.setGridOverlay(readGridOverlayFromForm());
}

function applySelectionAsCellSize() {
  if (!_wizardSelectionBBox) {
    return;
  }
  var cellW = document.getElementById("cfg-cell-w");
  var cellH = document.getElementById("cfg-cell-h");
  if (cellW && _wizardSelectionBBox.w > 0) {
    cellW.value = String(_wizardSelectionBBox.w);
  }
  if (cellH && _wizardSelectionBBox.h > 0) {
    cellH.value = String(_wizardSelectionBBox.h);
  }
  syncWizardGridOverlay();
}

/**
 * Show the active panel, hide all others.
 */
function renderPanels() {
  STEPS.forEach(function (step) {
    var panel = document.getElementById("panel-" + step);
    if (!panel) { return; }
    if (step === state.currentStep) {
      panel.classList.remove("hidden");
      panel.classList.add("active");
    } else {
      panel.classList.add("hidden");
      panel.classList.remove("active");
    }
  });

  // Configure step: initialize/destroy viewer panel
  var viewerMount = document.getElementById("wizard-viewer-panel");
  if (state.currentStep === "configure" && viewerMount) {
    if (!_wizardViewerPanel && typeof createViewerPanel === "function") {
      _wizardViewerPanel = createViewerPanel(viewerMount, {
        mode: "select",
        zoom: 1,
        showControls: true,
        onSelectionChange: function(bbox) {
          _wizardSelectionBBox = bbox ? {
            x: bbox.x,
            y: bbox.y,
            w: bbox.w,
            h: bbox.h,
          } : null;
        }
      });
      syncWizardGridOverlay();
    }
    // Load uploaded image into viewer
    if (_wizardViewerPanel && state.previewDataUrl) {
      _wizardViewerPanel.loadImage(state.previewDataUrl);
      syncWizardGridOverlay();
    }
  } else if (state.currentStep !== "configure" && _wizardViewerPanel) {
    _wizardViewerPanel.destroy();
    _wizardViewerPanel = null;
  }
}

/**
 * Render the confirm summary from current config.
 */
function renderConfirmSummary() {
  var el = document.getElementById("confirm-summary");
  if (!el) { return; }

  var cfg = state.config;
  var lines = [
    "Name:                " + cfg.name,
    "Asset Type:          " + cfg.asset_type,
    "Angles:              " + cfg.angles,
    "Frames:              " + cfg.frames,
    "Render Resolution:   " + cfg.render_resolution + " px",
    "Cell Size:           " + (cfg.cell_w ? cfg.cell_w + "x" + cfg.cell_h + " px" : "auto"),
    "Grid Offset:         " + ((cfg.grid_offset_x || cfg.grid_offset_y) ? (cfg.grid_offset_x + "," + cfg.grid_offset_y + " px") : "0,0"),
    "Row Order:           " + cfg.order,
    "Angle Row Map:       " + (cfg.angle_row_map || "none"),
    "Background Mode:     " + cfg.bg_mode,
    "Background Colors:   " + ((cfg.bg_key_colors && cfg.bg_key_colors.length > 0) ? cfg.bg_key_colors.join(", ") : "(default magenta)"),
    "Background Tolerance:" + cfg.bg_tolerance,
    "Reflection Policy:   " + (cfg.reflection_policy || "auto (generate)"),
    "",
    "Source:              " + (state.fileName || "(none)"),
  ];
  el.textContent = lines.join("\n");
}

/**
 * Render analysis results into the analyze table.
 *
 * @param {Object} analysis - Analysis result from API
 */
function renderAnalysisResults(analysis) {
  var tbody = document.getElementById("analyze-table-body");
  if (!tbody) { return; }

  tbody.innerHTML = "";
  // Map to actual /api/analyze response fields (see asset_service.py:analyze())
  var dims = analysis.dimensions;
  var dimStr = Array.isArray(dims) ? dims[0] + "x" + dims[1] + " px" : "unknown";

  var cellW = parseInt(analysis.suggested_cell_w, 10) || 0;
  var cellH = parseInt(analysis.suggested_cell_h, 10) || 0;
  var cellStr = (cellW > 0 && cellH > 0) ? cellW + "x" + cellH + " px" : "auto";

  var gridDiag = analysis.grid_diagnostics || {};
  var gridStr = gridDiag.method
    ? gridDiag.method + (gridDiag.divisible ? " (divisible)" : " (remainder " + gridDiag.remainder_x + "x" + gridDiag.remainder_y + ")")
    : "unknown";
  var confStr = (gridDiag.confidence !== undefined && gridDiag.confidence !== null)
    ? String(gridDiag.confidence)
    : "unknown";

  var rows = [
    ["Image Size", dimStr],
    ["Suggested Angles", analysis.suggested_angles || "1"],
    ["Suggested Frames", Array.isArray(analysis.suggested_frames) ? analysis.suggested_frames.join(", ") : (analysis.suggested_frames || "1")],
    ["Suggested Columns", analysis.suggested_cols || "unknown"],
    ["Cell Size", cellStr],
    ["Grid Diagnostics", gridStr],
    ["Confidence", confStr],
  ];
  if (Array.isArray(analysis.warnings) && analysis.warnings.length > 0) {
    rows.push(["Warnings", analysis.warnings.join(" | ")]);
  }

  rows.forEach(function (row) {
    var tr = document.createElement("tr");
    var td1 = document.createElement("td");
    td1.textContent = row[0];
    var td2 = document.createElement("td");
    td2.textContent = String(row[1]);
    tr.appendChild(td1);
    tr.appendChild(td2);
    tbody.appendChild(tr);
  });
}

/**
 * Compute and render the pipeline intelligence card from analysis results.
 * Shows what the pipeline will auto-select: mode, background, layout.
 *
 * @param {Object} analysis - Analysis results from API
 */
function renderIntelligenceCard(analysis) {
  var card = document.getElementById("analyze-intelligence");
  if (!card || !analysis) { return; }

  var cellW = parseInt(analysis.suggested_cell_w, 10) || 0;
  var detectedBg = analysis.detected_background || "unknown";
  var layouts = analysis.layout_suggestions || [];

  // Determine auto-selected processing mode per dispatch.py logic
  var modeName, modeDetail;
  if (cellW > 24) {
    modeName = "quality";
    modeDetail = "cells are " + cellW + "x" + (analysis.suggested_cell_h || cellW) + "px (above 24px threshold)";
  } else {
    modeName = "standard";
    modeDetail = "cells are " + cellW + "x" + (analysis.suggested_cell_h || cellW) + "px";
  }

  // Background description
  var bgDesc;
  if (detectedBg === "alpha") {
    bgDesc = "alpha (transparency detected)";
  } else if (detectedBg === "key_color") {
    bgDesc = "key color (solid background detected)";
  } else {
    bgDesc = detectedBg;
  }

  // Layout suggestion
  var layoutDesc = "unknown";
  var layoutConf = "";
  if (layouts.length > 0) {
    layoutDesc = layouts[0].label || layouts[0].order || "unknown";
    layoutConf = layouts[0].confidence ? " (" + layouts[0].confidence + " confidence)" : "";
  }

  var html = '<div class="intel-header">Pipeline will auto-select:</div>';
  html += '<div class="intel-row"><span class="intel-label">Mode:</span>';
  html += '<span class="intel-value">' + escapeHtml(modeName) + '</span>';
  html += ' &mdash; ' + escapeHtml(modeDetail) + '</div>';
  html += '<div class="intel-row"><span class="intel-label">Background:</span>';
  html += '<span class="intel-value">' + escapeHtml(bgDesc) + '</span></div>';
  html += '<div class="intel-row"><span class="intel-label">Layout:</span>';
  html += '<span class="intel-value">' + escapeHtml(layoutDesc) + escapeHtml(layoutConf) + '</span></div>';

  // Note about literal mode
  if (cellW === 1) {
    html += '<div class="intel-note">Override to "literal" in Configure step for 1:1 pixel copy.</div>';
  }

  card.innerHTML = html;
  card.classList.remove("hidden");
}

/**
 * Update the auto-mode badge in the Configure step from analysis results.
 */
function updateAutoModeBadge() {
  var label = document.getElementById("auto-mode-label");
  if (!label || !state.analysis) { return; }

  var cellW = parseInt(state.analysis.suggested_cell_w, 10) || 0;
  var text;
  if (cellW > 24) {
    text = "quality (subcell dithering) \u2014 cells " + cellW + "px > 24px threshold";
  } else if (cellW > 0) {
    text = "standard \u2014 cells " + cellW + "px";
  } else {
    text = "auto (will resolve at run time)";
  }
  label.textContent = text;
}

/**
 * Render the result step with job output.
 *
 * @param {Object} result - Pipeline output from API
 */
function renderResult(result) {
  var tbody = document.getElementById("result-table-body");
  if (!tbody) { return; }

  tbody.innerHTML = "";
  var rows = [
    ["Job ID", result.job_id || ""],
    ["XP Path", result.xp_path || ""],
    ["Checksum", result.checksum_sha256 || ""],
    ["Created", result.created_at || ""],
  ];

  rows.forEach(function (row) {
    var tr = document.createElement("tr");
    var td1 = document.createElement("td");
    td1.textContent = row[0];
    var td2 = document.createElement("td");
    td2.textContent = String(row[1]);
    tr.appendChild(td1);
    tr.appendChild(td2);
    tbody.appendChild(tr);
  });

  // Render XP preview image
  var previewContainer = document.getElementById("result-preview-container");
  if (previewContainer && result.job_id) {
    previewContainer.innerHTML = "";
    var preview = document.createElement("img");
    preview.src = API_BASE + "/preview/" + encodeURIComponent(result.job_id);
    preview.className = "result-preview";
    preview.alt = "XP sprite preview";
    preview.style.imageRendering = "pixelated";
    preview.onerror = function() { previewContainer.innerHTML = ""; };
    previewContainer.appendChild(preview);
  }
}

// ============================================================================
// API Communication
// ============================================================================

/**
 * Upload a file to the backend.
 *
 * @param {File} file - File object from input
 * @returns {Promise<Object>} Upload response
 */
function apiUpload(file) {
  var formData = new FormData();
  formData.append("file", file);

  return fetch(API_BASE + "/upload", {
    method: "POST",
    body: formData,
  }).then(function (resp) {
    if (!resp.ok) {
      return resp.json().then(function (err) {
        throw new Error(err.error || "Upload failed");
      });
    }
    return resp.json();
  });
}

/**
 * Analyze an uploaded image.
 *
 * @param {string} filePath - Path to uploaded file
 * @returns {Promise<Object>} Analysis results
 */
function apiAnalyze(filePath) {
  return fetch(API_BASE + "/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: filePath }),
  }).then(function (resp) {
    if (!resp.ok) {
      return resp.json().then(function (err) {
        throw new Error(err.error || "Analysis failed");
      });
    }
    return resp.json();
  });
}

/**
 * Validate configuration without running the pipeline.
 *
 * @param {Object} jobConfig - AssetJobConfig-shaped object
 * @returns {Promise<Object>} Validation result
 */
function apiConfigure(jobConfig) {
  return fetch(API_BASE + "/configure", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(jobConfig),
  }).then(function (resp) {
    return resp.json().then(function (data) {
      return { status: resp.status, data: data };
    });
  });
}

/**
 * Run the pipeline.
 *
 * @param {Object} jobConfig - AssetJobConfig-shaped object
 * @returns {Promise<Object>} Pipeline result
 */
function apiRun(jobConfig) {
  return fetch(API_BASE + "/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(jobConfig),
  }).then(function (resp) {
    if (!resp.ok) {
      return resp.json().then(function (err) {
        throw new Error(err.error || "Pipeline failed");
      });
    }
    return resp.json();
  });
}

/**
 * Get export URL for a completed job.
 *
 * @param {string} jobId - Job identifier
 * @param {string} format - "png", "zip", or "gif"
 * @returns {string} Export URL
 */
function getExportUrl(jobId, format) {
  return API_BASE + "/export/" + jobId + "?format=" + format;
}

// ============================================================================
// Event Handlers
// ============================================================================

/**
 * Handle file selection or drag-and-drop.
 *
 * @param {File} file - Selected file
 */
function handleFileSelect(file) {
  if (!file) { return; }
  _wizardSelectionBBox = null;

  // Show preview
  var previewArea = document.getElementById("upload-preview");
  var previewImg = document.getElementById("preview-img");
  var uploadInfo = document.getElementById("upload-info");

  if (previewArea && previewImg) {
    var reader = new FileReader();
    reader.onload = function (e) {
      previewImg.src = e.target.result;
      previewArea.classList.remove("hidden");
      uploadInfo.textContent = file.name + " (" + formatBytes(file.size) + ")";
      updateState({ previewDataUrl: e.target.result });
    };
    reader.readAsDataURL(file);
  }

  // Upload to backend
  var nextBtn = document.getElementById("btn-upload-next");
  if (nextBtn) { nextBtn.disabled = true; }

  apiUpload(file)
    .then(function (resp) {
      updateState({
        fileId: resp.file_id,
        filePath: resp.path,
        fileName: resp.filename,
        fileSizeBytes: resp.size_bytes,
      });
      if (nextBtn) { nextBtn.disabled = false; }
    })
    .catch(function (err) {
      if (uploadInfo) {
        uploadInfo.textContent = "Upload error: " + err.message;
      }
    });
}

/**
 * Run analysis when entering the Analyze step.
 */
function runAnalysis() {
  var statusEl = document.getElementById("analyze-status");
  var resultsEl = document.getElementById("analyze-results");
  var nextBtn = document.getElementById("btn-analyze-next");

  if (statusEl) { statusEl.classList.remove("hidden"); }
  if (resultsEl) { resultsEl.classList.add("hidden"); }
  if (nextBtn) { nextBtn.disabled = true; }

  if (!state.filePath) {
    if (statusEl) { statusEl.innerHTML = "No file uploaded."; }
    return;
  }

  apiAnalyze(state.filePath)
    .then(function (analysis) {
      updateState({ analysis: analysis });
      renderAnalysisResults(analysis);
      renderIntelligenceCard(analysis);
      if (statusEl) { statusEl.classList.add("hidden"); }
      if (resultsEl) { resultsEl.classList.remove("hidden"); }
      if (nextBtn) { nextBtn.disabled = false; }

      // Pre-fill configure form from analysis
      prefillConfigFromAnalysis(analysis);
    })
    .catch(function (err) {
      if (statusEl) {
        statusEl.innerHTML = '<span style="color:var(--error)">Analysis failed: ' +
          escapeHtml(err.message) + '</span>';
      }
      // Allow proceeding with defaults even if analysis fails
      if (nextBtn) { nextBtn.disabled = false; }
    });
}

/**
 * Pre-fill configuration form from analysis results.
 *
 * @param {Object} analysis - Analysis results from API
 */
function prefillConfigFromAnalysis(analysis) {
  if (!analysis) { return; }

  var anglesEl = document.getElementById("cfg-angles");
  var framesEl = document.getElementById("cfg-frames");
  var cellWEl = document.getElementById("cfg-cell-w");
  var cellHEl = document.getElementById("cfg-cell-h");

  if (analysis.suggested_angles && anglesEl) {
    anglesEl.value = String(analysis.suggested_angles);
  }
  var XP_META_MAX = 35;
  if (analysis.suggested_frames && framesEl) {
    var parsed = Array.isArray(analysis.suggested_frames)
      ? analysis.suggested_frames.map(function(v) { return parseInt(v, 10) || 0; })
      : [parseInt(analysis.suggested_frames, 10) || 0];
    var metadataSafe = parsed.every(function(v) { return v > 0 && v <= 35; });
    if (metadataSafe) {
      framesEl.value = parsed.join(",");
    } else {
      // Don't prefill unsafe values; show inline hint and log details.
      var hint = framesEl.parentElement.querySelector(".prefill-warning");
      if (!hint) {
        hint = document.createElement("span");
        hint.className = "form-hint prefill-warning";
        hint.style.color = "#e67e22";
        framesEl.parentElement.appendChild(hint);
      }
      hint.textContent = "Detected " + parsed.join(",") + " cols \u2014 enter per-animation counts (e.g. 1,8)";
      console.warn(
        "Analyze suggested non-semantic frame counts; keeping existing frames value",
        analysis.suggested_frames
      );
    }
  }

  var suggestedCellW = parseInt(analysis.suggested_cell_w, 10) || 0;
  var suggestedCellH = parseInt(analysis.suggested_cell_h, 10) || 0;
  if (cellWEl && suggestedCellW > 0) {
    cellWEl.value = String(suggestedCellW);
  }
  if (cellHEl && suggestedCellH > 0) {
    cellHEl.value = String(suggestedCellH);
  }

  syncWizardGridOverlay();
}

/**
 * Read configuration form values into state.
 */
function readConfigForm() {
  var newConfig = Object.freeze({
    name: getFormValue("cfg-name", "unnamed"),
    asset_type: getFormValue("cfg-asset-type", "custom"),
    angles: parseInt(getFormValue("cfg-angles", "1"), 10) || 1,
    frames: getFormValue("cfg-frames", "1"),
    render_resolution: parseInt(getFormValue("cfg-render-res", "24"), 10) || 24,
    bg_mode: getFormValue("cfg-bg-mode", "key_color"),
    bg_tolerance: parseInt(getFormValue("cfg-bg-tolerance", "8"), 10) || 8,
    reflection_policy: getFormValue("cfg-reflection", ""),
    explicit_projs: getFormValue("cfg-explicit-projs", ""),
    transparency: document.getElementById("cfg-transparency") ? document.getElementById("cfg-transparency").checked : false,
    cell_w: getFormValue("cfg-cell-w", ""),
    cell_h: getFormValue("cfg-cell-h", ""),
    grid_offset_x: parseInt(getFormValue("cfg-grid-offset-x", "0"), 10) || 0,
    grid_offset_y: parseInt(getFormValue("cfg-grid-offset-y", "0"), 10) || 0,
    order: getFormValue("cfg-order", "angle_major"),
    angle_row_map: getFormValue("cfg-angle-row-map", ""),
    bg_key_colors: state.config.bg_key_colors ? state.config.bg_key_colors.slice() : [],
  });
  updateState({ config: newConfig });
}

/**
 * Run validation on the Confirm step.
 */
function runValidation() {
  var validationEl = document.getElementById("confirm-validation");
  var validationMsg = document.getElementById("confirm-validation-msg");
  var runBtn = document.getElementById("btn-confirm-run");

  if (validationEl) { validationEl.classList.add("hidden"); }
  if (runBtn) { runBtn.disabled = true; }

  var jobConfig = toJobConfig(state);

  apiConfigure(jobConfig)
    .then(function (resp) {
      updateState({ validationResult: resp.data });

      if (validationEl && validationMsg) {
        validationEl.classList.remove("hidden");
        if (resp.data.valid) {
          validationEl.className = "validation-block valid";
          validationMsg.textContent = "Configuration is valid.";
          if (runBtn) { runBtn.disabled = false; }
        } else {
          validationEl.className = "validation-block invalid";
          validationMsg.textContent = "Validation error: " + (resp.data.error || "Unknown");
        }
      }
    })
    .catch(function (err) {
      if (validationEl && validationMsg) {
        validationEl.classList.remove("hidden");
        validationEl.className = "validation-block invalid";
        validationMsg.textContent = "Validation request failed: " + err.message;
      }
    });
}

/**
 * Set the active stage chip during pipeline run (timed estimate).
 *
 * @param {string} stageName - "generate", "slice", "process", or "assemble"
 */
function setActiveStageChip(stageName) {
  var chips = document.querySelectorAll("#stage-chips .stage-chip");
  var found = false;
  chips.forEach(function(chip) {
    if (found) {
      chip.className = "stage-chip";
    } else if (chip.getAttribute("data-stage") === stageName) {
      chip.className = "stage-chip active";
      found = true;
    } else {
      chip.className = "stage-chip done";
    }
  });
}

/**
 * Mark all stage chips as done.
 */
function completeAllStageChips() {
  var chips = document.querySelectorAll("#stage-chips .stage-chip");
  chips.forEach(function(chip) { chip.className = "stage-chip done"; });
}

/**
 * Reset all stage chips to inactive.
 */
function resetStageChips() {
  var chips = document.querySelectorAll("#stage-chips .stage-chip");
  chips.forEach(function(chip) { chip.className = "stage-chip"; });
}

/**
 * Execute the pipeline on the Run step.
 */
function runPipeline() {
  var progressFill = document.getElementById("run-progress-fill");
  var statusText = document.getElementById("run-status-text");
  var errorBlock = document.getElementById("run-error");
  var resultBtn = document.getElementById("btn-run-result");

  if (progressFill) { progressFill.style.width = "10%"; }
  if (statusText) { statusText.textContent = "Submitting job..."; }
  if (errorBlock) { errorBlock.classList.add("hidden"); }
  if (resultBtn) { resultBtn.classList.add("hidden"); }
  resetStageChips();

  var jobConfig = toJobConfig(state);

  if (progressFill) { progressFill.style.width = "15%"; }
  if (statusText) { statusText.textContent = "Running pipeline..."; }

  // Timed stage chip animation (estimates, not real progress)
  setActiveStageChip("generate");
  var stageTimers = [
    setTimeout(function() { setActiveStageChip("slice"); if (progressFill) progressFill.style.width = "35%"; }, 2000),
    setTimeout(function() { setActiveStageChip("process"); if (progressFill) progressFill.style.width = "55%"; }, 5000),
    setTimeout(function() { setActiveStageChip("assemble"); if (progressFill) progressFill.style.width = "80%"; }, 10000),
  ];

  apiRun(jobConfig)
    .then(function (result) {
      stageTimers.forEach(clearTimeout);
      if (progressFill) { progressFill.style.width = "100%"; }
      if (statusText) { statusText.textContent = "Complete!"; }
      completeAllStageChips();

      updateState({
        jobId: result.job_id,
        result: result,
        runError: null,
      });

      if (resultBtn) { resultBtn.classList.remove("hidden"); }

      // Show "View Branches" button if a manifest exists for this job
      var branchBtn = document.getElementById("btn-view-branches");
      if (branchBtn && result.job_id) {
        fetch(API_BASE + "/branches/" + encodeURIComponent(result.job_id))
          .then(function(r) {
            if (r.ok) { branchBtn.style.display = ""; }
          })
          .catch(function() { /* no manifest, keep hidden */ });
      }
    })
    .catch(function (err) {
      stageTimers.forEach(clearTimeout);
      if (progressFill) { progressFill.style.width = "0%"; }
      if (statusText) { statusText.textContent = "Pipeline failed."; }
      if (errorBlock) {
        errorBlock.classList.remove("hidden");
        errorBlock.textContent = err.message;
      }
      resetStageChips();

      updateState({ runError: err.message });
    });
}

/**
 * Reset to initial state for a new run.
 */
function startOver() {
  state = createInitialState();
  layoutState = null;

  // Reset form elements
  var fileInput = document.getElementById("file-input");
  if (fileInput) { fileInput.value = ""; }
  var previewArea = document.getElementById("upload-preview");
  if (previewArea) { previewArea.classList.add("hidden"); }
  var nextBtn = document.getElementById("btn-upload-next");
  if (nextBtn) { nextBtn.disabled = true; }

  // Clear layout editor container and heading
  var layoutContainer = document.getElementById("layout-editor-container");
  if (layoutContainer) { layoutContainer.innerHTML = ""; }
  var layoutHeading = document.querySelector(".layout-editor-heading");
  if (layoutHeading) { layoutHeading.remove(); }

  renderNavigation();
  renderPanels();
}

// ============================================================================
// Utility
// ============================================================================

/**
 * Get form element value by ID.
 *
 * @param {string} id - Element ID
 * @param {string} fallback - Default value
 * @returns {string} Element value or fallback
 */
function getFormValue(id, fallback) {
  var el = document.getElementById(id);
  return el ? el.value : fallback;
}

/**
 * Format bytes to human-readable string.
 *
 * @param {number} bytes
 * @returns {string}
 */
function formatBytes(bytes) {
  if (bytes < 1024) { return bytes + " B"; }
  if (bytes < 1048576) { return (bytes / 1024).toFixed(1) + " KB"; }
  return (bytes / 1048576).toFixed(1) + " MB";
}

/**
 * Escape HTML to prevent XSS.
 *
 * @param {string} str - Raw string
 * @returns {string} Escaped string
 */
function escapeHtml(str) {
  var div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ============================================================================
// Background Color Chips
// ============================================================================

/**
 * Add a background color chip to the chips container.
 * Updates state.config.bg_key_colors immutably.
 *
 * @param {string} hex - Color hex string like "#FF00FF"
 */
function addBgColor(hex) {
  var normalized = hex.toUpperCase();
  var existing = state.config.bg_key_colors || [];

  // Prevent duplicates
  if (existing.indexOf(normalized) >= 0) {
    return;
  }

  var updated = existing.slice();
  updated.push(normalized);
  updateState({
    config: Object.freeze(Object.assign({}, state.config, {
      bg_key_colors: Object.freeze(updated),
    })),
  });
  renderBgColorChips();
}

/**
 * Remove a background color chip.
 * Updates state.config.bg_key_colors immutably.
 *
 * @param {string} hex - Color hex string to remove
 */
function removeBgColor(hex) {
  var normalized = hex.toUpperCase();
  var existing = state.config.bg_key_colors || [];

  var updated = existing.filter(function (c) {
    return c !== normalized;
  });
  updateState({
    config: Object.freeze(Object.assign({}, state.config, {
      bg_key_colors: Object.freeze(updated),
    })),
  });
  renderBgColorChips();
}

/**
 * Render color chips in the #bg-color-chips container from state.
 */
function renderBgColorChips() {
  var container = document.getElementById("bg-color-chips");
  if (!container) { return; }

  container.innerHTML = "";
  var colors = state.config.bg_key_colors || [];

  colors.forEach(function (hex) {
    var chip = document.createElement("span");
    chip.className = "color-chip";

    var swatch = document.createElement("span");
    swatch.className = "color-chip-swatch";
    swatch.style.backgroundColor = hex;

    var label = document.createElement("span");
    label.textContent = hex;

    var removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "color-chip-remove";
    removeBtn.textContent = "\u00D7";
    removeBtn.setAttribute("aria-label", "Remove " + hex);
    removeBtn.addEventListener("click", function () {
      removeBgColor(hex);
    });

    chip.appendChild(swatch);
    chip.appendChild(label);
    chip.appendChild(removeBtn);
    container.appendChild(chip);
  });
}

// ============================================================================
// Template Info Panel
// ============================================================================

/**
 * Canonical direction labels for 8-angle sprites.
 * Matches CANONICAL_ANGLE_ORDER in fixture_schema.py.
 */
var DIRECTION_LABELS_8 = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"];
var DIRECTION_LABELS_4 = ["S", "W", "N", "E"];

/**
 * Build a human-readable layout description based on asset type, angles, and frames.
 *
 * @param {string} assetType - "character", "item", "ui", or "custom"
 * @param {number} angles - Number of directions
 * @param {number[]} framesList - Parsed frames array
 * @returns {Object} Description fields: family, layout, rowOrder, mirror, grid
 */
function buildTemplateInfo(assetType, angles, framesList) {
  var totalFrames = framesList.reduce(function (sum, n) { return sum + n; }, 0);
  var totalCells = angles * totalFrames;
  var framesLabel = framesList.length > 1
    ? framesList.join(" + ") + " = " + totalFrames + " frames/direction"
    : totalFrames + " frame(s)/direction";

  var family = "";
  var layout = "";
  var rowOrder = "";
  var mirror = "";

  if (assetType === "character") {
    if (angles === 8) {
      family = "8-direction character";
      layout = "Each row is one facing direction with " + framesLabel + ".";
      rowOrder = DIRECTION_LABELS_8.join(", ");
      mirror = "Left-facing directions (SW, W, NW) are auto-mirrored from right-facing counterparts.";
    } else if (angles === 4) {
      family = "4-direction character";
      layout = "Each row is one facing direction with " + framesLabel + ".";
      rowOrder = DIRECTION_LABELS_4.join(", ");
      mirror = "Left/right mirroring may be applied if reflection policy is set.";
    } else if (angles === 1) {
      family = "Single-direction character";
      layout = "One row with " + framesLabel + ".";
      rowOrder = "Single direction (no rotation).";
      mirror = "None.";
    } else {
      family = angles + "-direction character";
      layout = "Each row is one facing direction with " + framesLabel + ".";
      rowOrder = angles + " rows, one per direction.";
      mirror = "Mirroring depends on reflection policy.";
    }
  } else if (assetType === "item") {
    if (angles <= 1) {
      family = "Static item";
      layout = "One row, typically " + totalFrames + " frame(s).";
      rowOrder = "Single row (no rotation).";
      mirror = "None.";
    } else {
      family = "Rotating item";
      layout = angles + " rotations, one per row, " + framesLabel + ".";
      rowOrder = angles + " rows, one per rotation angle.";
      mirror = "None (items typically do not mirror).";
    }
  } else if (assetType === "ui") {
    family = "UI element";
    layout = "No rotation. Single row with " + totalFrames + " frame(s).";
    rowOrder = "Single row.";
    mirror = "None.";
  } else {
    family = "Custom layout";
    layout = angles + " direction(s), " + framesLabel + ".";
    rowOrder = angles === 1
      ? "Single row."
      : angles + " rows, one per direction.";
    mirror = "Depends on reflection policy.";
  }

  var grid = angles + " row(s) x " + totalFrames + " column(s) = " + totalCells + " cell(s)";

  return {
    family: family,
    layout: layout,
    rowOrder: rowOrder,
    mirror: mirror,
    grid: grid,
  };
}

/**
 * Read current configure-step values and update the template info panel.
 */
function updateTemplateInfoPanel() {
  var placeholder = document.getElementById("template-info-placeholder");
  var content = document.getElementById("template-info-content");
  if (!placeholder || !content) { return; }

  var assetType = getFormValue("cfg-asset-type", "custom");
  var angles = parseInt(getFormValue("cfg-angles", "1"), 10) || 1;
  var framesStr = getFormValue("cfg-frames", "1");
  var framesList = parseFramesString(framesStr);

  var info = buildTemplateInfo(assetType, angles, framesList);

  placeholder.classList.add("hidden");
  content.classList.remove("hidden");

  var familyEl = document.getElementById("tinfo-family");
  var layoutEl = document.getElementById("tinfo-layout");
  var rowOrderEl = document.getElementById("tinfo-row-order");
  var mirrorEl = document.getElementById("tinfo-mirror");
  var gridEl = document.getElementById("tinfo-grid");

  if (familyEl) { familyEl.textContent = info.family; }
  if (layoutEl) { layoutEl.textContent = info.layout; }
  if (rowOrderEl) { rowOrderEl.textContent = info.rowOrder; }
  if (mirrorEl) { mirrorEl.textContent = info.mirror; }
  if (gridEl) { gridEl.textContent = info.grid; }
}

// ============================================================================
// Layout Editor Integration
// ============================================================================

/**
 * Initialize the layout editor from current config form values.
 * Creates one draggable row per angle/direction.
 * Skips rendering for single-angle sprites (nothing to reorder).
 */
function initLayoutEditor() {
  var container = document.getElementById("layout-editor-container");
  if (!container) { return; }

  var angles = parseInt(getFormValue("cfg-angles", "1"), 10) || 1;
  var framesStr = getFormValue("cfg-frames", "1");
  var framesList = parseFramesString(framesStr);
  var totalFrames = framesList.reduce(function (sum, n) { return sum + n; }, 0);

  // Only show layout editor when there are multiple rows to reorder
  if (angles < 2) {
    container.innerHTML = "";
    layoutState = null;
    return;
  }

  var dirLabels = angles === 8 ? DIRECTION_LABELS_8 :
                  angles === 4 ? DIRECTION_LABELS_4 : null;

  var items = [];
  for (var i = 0; i < angles; i++) {
    var dirLabel = dirLabels ? dirLabels[i] : "Row " + i;
    var framesLabel = framesList.length > 1
      ? framesList.join("+") + " frames"
      : totalFrames + " frame(s)";
    items.push({
      id: "row-" + i,
      label: dirLabel + " (" + framesLabel + ")",
      type: "angle",
      data: { angleIndex: i, frameCount: totalFrames },
    });
  }

  layoutState = createLayoutState(items);
  renderLayoutEditorUI();
}

/**
 * Re-render the layout editor with current layoutState and wired callbacks.
 */
function renderLayoutEditorUI() {
  var container = document.getElementById("layout-editor-container");
  if (!container || !layoutState) { return; }

  // Add heading on first render
  var heading = container.previousElementSibling;
  if (!heading || heading.tagName !== "H3" || heading.className !== "layout-editor-heading") {
    var h3 = document.createElement("h3");
    h3.className = "layout-editor-heading";
    h3.textContent = "Row Layout (drag to reorder)";
    container.parentNode.insertBefore(h3, container);
  }

  renderLayoutEditor(layoutState, container, {
    onReorder: function (draggedId, targetId) {
      var fromIdx = findItemIndex(layoutState, draggedId);
      var toIdx = findItemIndex(layoutState, targetId);
      if (fromIdx >= 0 && toIdx >= 0) {
        layoutState = reorderItem(layoutState, fromIdx, toIdx);
        renderLayoutEditorUI();
        syncLayoutToConfig();
      }
    },
    onDelete: function (itemId) {
      layoutState = deleteItem(layoutState, itemId);
      renderLayoutEditorUI();
      syncLayoutToConfig();
    },
    onRestore: function (itemId) {
      layoutState = restoreItem(layoutState, itemId);
      renderLayoutEditorUI();
      syncLayoutToConfig();
    },
    onUndo: function () {
      layoutState = undoOperation(layoutState);
      renderLayoutEditorUI();
      syncLayoutToConfig();
    },
    onReset: function () {
      layoutState = resetLayout(layoutState);
      renderLayoutEditorUI();
      syncLayoutToConfig();
    },
  });
}

/**
 * Sync layout editor state to the angle_row_map config field.
 * Active items in their current order become the row mapping.
 */
function syncLayoutToConfig() {
  if (!layoutState) { return; }
  var active = getActiveItems(layoutState);
  var order = active.map(function (item) { return item.originalIndex; });

  var rowMapEl = document.getElementById("cfg-angle-row-map");
  if (rowMapEl) {
    rowMapEl.value = order.join(",");
  }
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener("DOMContentLoaded", function () {
  // File input handler
  var fileInput = document.getElementById("file-input");
  if (fileInput) {
    fileInput.addEventListener("change", function (e) {
      if (e.target.files && e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
      }
    });
  }

  // Drag and drop
  var uploadZone = document.getElementById("upload-zone");
  if (uploadZone) {
    uploadZone.addEventListener("dragover", function (e) {
      e.preventDefault();
      uploadZone.classList.add("drag-over");
    });
    uploadZone.addEventListener("dragleave", function () {
      uploadZone.classList.remove("drag-over");
    });
    uploadZone.addEventListener("drop", function (e) {
      e.preventDefault();
      uploadZone.classList.remove("drag-over");
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        handleFileSelect(e.dataTransfer.files[0]);
      }
    });
  }

  // Upload next button
  var btnUploadNext = document.getElementById("btn-upload-next");
  if (btnUploadNext) {
    btnUploadNext.addEventListener("click", function () {
      if (state.filePath) {
        advanceStep();
        runAnalysis();
      }
    });
  }

  // Analyze next button
  var btnAnalyzeNext = document.getElementById("btn-analyze-next");
  if (btnAnalyzeNext) {
    btnAnalyzeNext.addEventListener("click", function () {
      advanceStep();
      updateAutoModeBadge();
      initLayoutEditor();
      loadWizardPipelineConfig();
    });
  }

  // Configure next button
  var btnConfigureNext = document.getElementById("btn-configure-next");
  if (btnConfigureNext) {
    btnConfigureNext.addEventListener("click", function () {
      readConfigForm();
      advanceStep();
      renderConfirmSummary();
      runValidation();
    });
  }

  // Confirm run button
  var btnConfirmRun = document.getElementById("btn-confirm-run");
  if (btnConfirmRun) {
    btnConfirmRun.addEventListener("click", function () {
      advanceStep();
      runPipeline();
    });
  }

  // Run result button
  var btnRunResult = document.getElementById("btn-run-result");
  if (btnRunResult) {
    btnRunResult.addEventListener("click", function () {
      advanceStep();
      if (state.result) {
        renderResult(state.result);
      }
    });
  }

  // Result action buttons: Edit in Workbench + Download .xp
  var btnResultWorkbench = document.getElementById("btn-result-workbench");
  if (btnResultWorkbench) {
    btnResultWorkbench.addEventListener("click", function () {
      var params = [];
      if (state.jobId) params.push("pipeline_job_id=" + encodeURIComponent(state.jobId));
      if (state.result && state.result.xp_path) {
        params.push("xp_path=" + encodeURIComponent(state.result.xp_path));
      }
      // Pass user-configured angles and frames so workbench uses the same values
      var cfg = state.config;
      if (cfg.angles) params.push("angles=" + encodeURIComponent(cfg.angles));
      if (cfg.frames) params.push("frames=" + encodeURIComponent(cfg.frames));
      window.location.href = "/workbench?" + params.join("&");
    });
  }
  var btnExportXp = document.getElementById("btn-export-xp");
  if (btnExportXp) {
    btnExportXp.addEventListener("click", function () {
      if (state.jobId) {
        window.open(getExportUrl(state.jobId, "xp"), "_blank");
      }
    });
  }

  // Export buttons
  var btnExportPng = document.getElementById("btn-export-png");
  if (btnExportPng) {
    btnExportPng.addEventListener("click", function () {
      if (state.jobId) {
        window.open(getExportUrl(state.jobId, "png"), "_blank");
      }
    });
  }
  var btnExportZip = document.getElementById("btn-export-zip");
  if (btnExportZip) {
    btnExportZip.addEventListener("click", function () {
      if (state.jobId) {
        window.open(getExportUrl(state.jobId, "zip"), "_blank");
      }
    });
  }
  var btnExportGif = document.getElementById("btn-export-gif");
  if (btnExportGif) {
    btnExportGif.addEventListener("click", function () {
      if (state.jobId) {
        window.open(getExportUrl(state.jobId, "gif"), "_blank");
      }
    });
  }

  // View Branches button
  var btnViewBranches = document.getElementById("btn-view-branches");
  if (btnViewBranches) {
    btnViewBranches.addEventListener("click", function () {
      if (state.jobId) {
        window.open("/branches?job_id=" + encodeURIComponent(state.jobId), "_blank");
      }
    });
  }

  // Start over button
  var btnStartOver = document.getElementById("btn-start-over");
  if (btnStartOver) {
    btnStartOver.addEventListener("click", startOver);
  }

  // Back buttons (generic handler)
  var backButtons = document.querySelectorAll(".btn-back");
  backButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = btn.getAttribute("data-target");
      if (target) {
        goToStep(target);
      }
    });
  });

  // Nav clicks for completed steps
  var navItems = document.querySelectorAll("#step-nav li");
  navItems.forEach(function (li) {
    li.addEventListener("click", function () {
      if (li.classList.contains("done")) {
        var step = li.getAttribute("data-step");
        goToStep(step);
      }
    });
  });

  // Angle row map preset buttons
  document.querySelectorAll('.preset-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.getElementById('cfg-angle-row-map').value = this.dataset.preset;
    });
  });

  // Background color chip controls
  var btnAddBgColor = document.getElementById("btn-add-bg-color");
  if (btnAddBgColor) {
    btnAddBgColor.addEventListener("click", function () {
      var picker = document.getElementById("cfg-bg-color-picker");
      if (picker) {
        addBgColor(picker.value);
      }
    });
  }

  // Template info panel + layout editor -- update on asset-type, angles, or frames change
  var templateInfoInputs = ["cfg-asset-type", "cfg-angles", "cfg-frames"];
  templateInfoInputs.forEach(function (id) {
    var el = document.getElementById(id);
    if (el) {
      el.addEventListener("change", function () {
        updateTemplateInfoPanel();
        initLayoutEditor();
      });
      el.addEventListener("input", function () {
        updateTemplateInfoPanel();
        initLayoutEditor();
      });
    }
  });

  // Grid overlay controls in Configure step
  ["cfg-cell-w", "cfg-cell-h", "cfg-grid-offset-x", "cfg-grid-offset-y"].forEach(function(id) {
    var el = document.getElementById(id);
    if (!el) return;
    var handler = function() { syncWizardGridOverlay(); };
    el.addEventListener("input", handler);
    el.addEventListener("change", handler);
  });

  var btnApplyRoiCell = document.getElementById("btn-apply-roi-cellsize");
  if (btnApplyRoiCell) {
    btnApplyRoiCell.addEventListener("click", function() {
      applySelectionAsCellSize();
    });
  }

  // Reflection choice description updater
  var reflSelect = document.getElementById("cfg-reflection");
  var reflDesc = document.getElementById("reflection-choice-desc");
  var REFL_DESCS = {
    "": "Detects if the sheet already has reflections by comparing left/right half brightness. If not found, generates them automatically. This is the safest default.",
    "generate": "Skips detection and always generates reflections by doubling the sheet width with a dimmed copy. Use this if auto-detect gets it wrong and you know your sheet does NOT have reflections.",
    "none": "Skips reflection handling entirely. Use this only if: (a) your sprite is single-angle (angles=1), or (b) your sheet already has reflections baked in AND you set Projections to 2 above."
  };
  if (reflSelect && reflDesc) {
    reflSelect.addEventListener("change", function() {
      reflDesc.textContent = REFL_DESCS[this.value] || "";
    });
  }

  // Initial render
  renderNavigation();
  renderPanels();
});

// ============================================================================
// Exports for testing (Node.js / contract parity)
// ============================================================================

// ============================================================================
// 4-Track Pipeline Config in Wizard (Phase 19-03)
// Loads field specs from /api/config/schema and renders in the Configure step.
// ============================================================================
var _wizardPipelineConfig = null;

function loadWizardPipelineConfig() {
  var container = document.getElementById("wizard-config-fields");
  if (!container) return;

  Promise.all([
    fetch(API_BASE + "/config/schema").then(function(r) { return r.json(); }),
    fetch(API_BASE + "/config/defaults").then(function(r) { return r.json(); })
  ]).then(function(results) {
    var fields = results[0].fields || [];
    var defaults = results[1].config || {};
    _wizardPipelineConfig = defaults;
    container.innerHTML = "";
    var currentSection = "";

    fields.forEach(function(f) {
      if (f.section !== currentSection) {
        currentSection = f.section;
        var header = document.createElement("div");
        header.style.cssText = "font-weight: 600; color: var(--text-accent, #7c83ff); margin-top: 8px; font-size: 12px; text-transform: uppercase;";
        header.textContent = currentSection.replace(/_/g, " ");
        container.appendChild(header);
      }

      var row = document.createElement("div");
      row.className = "form-row";

      var label = document.createElement("label");
      label.textContent = f.label;
      label.title = f.description;

      var sectionData = defaults[f.section] || {};
      var val = sectionData[f.field_name];
      var input;

      if (f.ui_widget === "select" && f.choices && f.choices.length) {
        input = document.createElement("select");
        f.choices.forEach(function(c) {
          var opt = document.createElement("option");
          opt.value = c; opt.textContent = c;
          if (String(val) === c) opt.selected = true;
          input.appendChild(opt);
        });
      } else if (f.ui_widget === "checkbox") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = !!val;
      } else if (f.ui_widget === "number") {
        input = document.createElement("input");
        input.type = "number";
        if (f.min_val !== null) input.min = f.min_val;
        if (f.max_val !== null) input.max = f.max_val;
        input.step = (f.max_val !== null && f.max_val <= 1) ? "0.01" : "1";
        input.value = val !== null && val !== undefined ? val : "";
      } else {
        input = document.createElement("input");
        input.type = "text";
        input.value = val !== null && val !== undefined ? (Array.isArray(val) ? JSON.stringify(val) : String(val)) : "";
      }

      input.dataset.section = f.section;
      input.dataset.field = f.field_name;
      input.dataset.widget = f.ui_widget;
      input.className = "wizard-config-input";

      row.appendChild(label);
      row.appendChild(input);
      container.appendChild(row);
    });
  }).catch(function(err) {
    container.innerHTML = '<p style="color: var(--error, #e94560); font-size: 12px;">Failed to load pipeline config: ' + err + '</p>';
  });
}

function collectWizardPipelineConfig() {
  var config = JSON.parse(JSON.stringify(_wizardPipelineConfig || {}));
  document.querySelectorAll(".wizard-config-input").forEach(function(el) {
    var section = el.dataset.section;
    var field = el.dataset.field;
    if (!config[section]) config[section] = {};
    if (el.dataset.widget === "checkbox") {
      config[section][field] = el.checked;
    } else if (el.dataset.widget === "number") {
      var v = el.value.trim();
      config[section][field] = v === "" ? null : Number(v);
    } else if (el.dataset.widget === "color_list") {
      try { config[section][field] = JSON.parse(el.value); }
      catch (_) { config[section][field] = el.value; }
    } else {
      var raw = el.value;
      var trimmed = (raw || "").trim();
      if (trimmed === "") {
        config[section][field] = "";
      } else if ((trimmed[0] === "[" && trimmed[trimmed.length - 1] === "]") ||
                 (trimmed[0] === "{" && trimmed[trimmed.length - 1] === "}")) {
        try { config[section][field] = JSON.parse(trimmed); }
        catch (_) { config[section][field] = raw; }
      } else if (field === "tie_break_order") {
        config[section][field] = trimmed.split(",").map(function(x) { return x.trim(); }).filter(Boolean);
      } else {
        config[section][field] = raw;
      }
    }
  });

  // Merge process override selectors into pipeline_config.process_settings
  var processMode = getFormValue("cfg-process-mode", "");
  var errorDiffusion = getFormValue("cfg-error-diffusion", "");
  var colorMetric = getFormValue("cfg-color-metric", "");
  if (processMode || errorDiffusion || colorMetric) {
    if (!config.process_settings) { config.process_settings = {}; }
    if (processMode) { config.process_settings.process_mode = processMode; }
    if (errorDiffusion) { config.process_settings.error_diffusion = errorDiffusion; }
    if (colorMetric) { config.process_settings.color_metric = colorMetric; }
  }

  return config;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    createInitialState: createInitialState,
    updateState: updateState,
    toJobConfig: toJobConfig,
    parseFramesString: parseFramesString,
    getJobConfigFields: getJobConfigFields,
    buildTemplateInfo: buildTemplateInfo,
    updateTemplateInfoPanel: updateTemplateInfoPanel,
    addBgColor: addBgColor,
    removeBgColor: removeBgColor,
    renderBgColorChips: renderBgColorChips,
    STEPS: STEPS,
    goToStep: goToStep,
    advanceStep: advanceStep,
    initLayoutEditor: initLayoutEditor,
    syncLayoutToConfig: syncLayoutToConfig,
  };
}
