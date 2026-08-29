/**
 * loaders.js -- PNG and XP sprite loaders for the viewer.
 *
 * Both loaders call the Python backend via HTTP API and return
 * a FrameSequence (via createFrameSequence from frame_sequence.js).
 *
 * Endpoints:
 *   POST /api/viewer/load-png  -- Slice PNG into viewer frames
 *   POST /api/viewer/load-xp   -- Render XP into viewer frames
 *
 * Tags: [FLOW:VIEWER] [DATA-CONTRACT:FRAME-SEQUENCE]
 */

"use strict";

// ============================================================================
// PNG Loader
// ============================================================================

/**
 * Load a PNG sprite sheet and extract frames via the backend API.
 *
 * @param {File}   file    - PNG file from file input
 * @param {Object} [config] - Optional slicing config:
 *   {angles, frames, projs, cell_w, cell_h, order, origin, angle_row_map, scale}
 * @param {Object} [pagination] - Optional {offset, limit}
 * @returns {Promise<Object>} FrameSequence object
 */
function loadPNG(file, config, pagination) {
  var apiBase = window.ASSET_API_BASE || "/api";
  var formData = new FormData();
  formData.append("file", file);

  if (config) {
    formData.append("config", JSON.stringify(config));
  }

  var params = [];
  if (pagination) {
    if (pagination.offset != null) { params.push("offset=" + pagination.offset); }
    if (pagination.limit != null) { params.push("limit=" + pagination.limit); }
  }

  var url = apiBase + "/viewer/load-png";
  if (params.length > 0) {
    url += "?" + params.join("&");
  }

  return fetch(url, {
    method: "POST",
    body: formData,
  })
    .then(function(response) {
      if (!response.ok) {
        return response.json().then(function(err) {
          throw new Error(err.error || "PNG load failed: " + response.status);
        });
      }
      return response.json();
    })
    .then(function(data) {
      return _responseToFrameSequence(data);
    });
}


// ============================================================================
// XP Loader
// ============================================================================

/**
 * Load an XP sprite file and extract rendered frames via the backend API.
 *
 * @param {File}   file    - .xp file from file input
 * @param {Object} [pagination] - Optional {offset, limit}
 * @param {number} [scale]      - Scale factor (default 1)
 * @returns {Promise<Object>} FrameSequence object
 */
function loadXP(file, pagination, scale) {
  var apiBase = window.ASSET_API_BASE || "/api";
  var formData = new FormData();
  formData.append("file", file);

  var params = [];
  if (pagination) {
    if (pagination.offset != null) { params.push("offset=" + pagination.offset); }
    if (pagination.limit != null) { params.push("limit=" + pagination.limit); }
  }
  if (scale != null && scale > 1) {
    params.push("scale=" + scale);
  }

  var url = apiBase + "/viewer/load-xp";
  if (params.length > 0) {
    url += "?" + params.join("&");
  }

  return fetch(url, {
    method: "POST",
    body: formData,
  })
    .then(function(response) {
      if (!response.ok) {
        return response.json().then(function(err) {
          throw new Error(err.error || "XP load failed: " + response.status);
        });
      }
      return response.json();
    })
    .then(function(data) {
      return _responseToFrameSequence(data);
    });
}


// ============================================================================
// Helpers
// ============================================================================

/**
 * Convert an API response dict to a FrameSequence.
 *
 * @param {Object} data - API response with shared response shape
 * @returns {Object} FrameSequence object (from createFrameSequence)
 */
function _responseToFrameSequence(data) {
  var frameObjects = (data.frames || []).map(function(fd) {
    return createFrame({
      data: fd.data,
      width: fd.width,
      height: fd.height,
      angle_idx: fd.angle_idx || 0,
      anim_idx: fd.anim_idx || 0,
      frame_idx: fd.frame_idx || 0
    });
  });

  return createFrameSequence({
    frames: frameObjects,
    angles: data.angles || 1,
    anims: data.anims || [1],
    projs: data.projs || 1,
    fps: data.fps || 8,
    metadata: data.metadata || null,
    truncated: data.truncated || false,
    total_frames: data.total_frames,
    returned_frames: data.returned_frames
  });
}


/**
 * Load a base64-encoded PNG string as an Image element.
 *
 * @param {string} base64Data - Base64-encoded PNG data
 * @returns {Promise<HTMLImageElement>} Loaded image element
 */
function loadBase64Image(base64Data) {
  return new Promise(function(resolve, reject) {
    var img = new Image();
    img.onload = function() { resolve(img); };
    img.onerror = function() { reject(new Error("Failed to load frame image")); };
    img.src = "data:image/png;base64," + base64Data;
  });
}
