/**
 * merge_panel.js -- API client for workbench merge/edit operations.
 *
 * Wraps /api/workbench/* endpoints with Promise-based methods.
 * All methods return Promises resolving to the JSON response body.
 *
 * [FLOW:WORKBENCH] [DATA-CONTRACT:SESSION]
 */
"use strict";

var MergePanel = (function () {
  var API_BASE = window.ASSET_API_BASE || "/api";
  var _jobId = null;

  function _post(path, body) {
    return fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function _postForm(path, formData) {
    return fetch(API_BASE + path, {
      method: "POST",
      body: formData,
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  return {
    /**
     * Set the active session job ID.
     * @param {string} id - Job/session ID from /workbench/start-session.
     */
    setJobId: function (id) {
      _jobId = id;
    },

    /** @returns {string|null} Current job ID. */
    getJobId: function () {
      return _jobId;
    },

    /**
     * Undo the most recent operation.
     * @returns {Promise<{undone_op_id, warning?, failed_ops?, thumbnails?}>}
     */
    undo: function () {
      return _post("/workbench/undo", { job_id: _jobId });
    },

    /**
     * Redo the most recently undone operation.
     * @returns {Promise<{redone_op_id, warning?, thumbnails?}>}
     */
    redo: function () {
      return _post("/workbench/redo", { job_id: _jobId });
    },

    /**
     * Apply a transform (flip/rotate) to selected cells.
     * @param {Array<{angle,anim,frame,proj}>} targets - Cell coordinates.
     * @param {{flip_h,flip_v,rotate_deg}} transform - Transform spec.
     * @returns {Promise<{thumbnails?}>}
     */
    transformCells: function (targets, transform) {
      return _post("/workbench/transform-cells", {
        job_id: _jobId,
        targets: targets,
        transform: transform,
      });
    },

    /**
     * Swap two cells in the grid.
     * @param {{angle,anim,frame,proj}} cellA
     * @param {{angle,anim,frame,proj}} cellB
     * @returns {Promise<{thumbnails?}>}
     */
    swapCells: function (cellA, cellB) {
      return _post("/workbench/swap-cells", {
        job_id: _jobId,
        cell_a: cellA,
        cell_b: cellB,
      });
    },

    /**
     * Copy one cell's content into one or more target cells.
     * @param {{angle,anim,frame,proj}} source
     * @param {Array<{angle,anim,frame,proj}>} targets
     * @returns {Promise<{thumbnails?}>}
     */
    fillFromSlot: function (source, targets) {
      return _post("/workbench/fill-from-slot", {
        job_id: _jobId,
        source: source,
        targets: targets,
      });
    },

    /**
     * Import an external image file into target cells.
     * @param {File} file - Image file from file input.
     * @param {Array<{angle,anim,frame,proj}>} targets
     * @param {{blendMode,fitMode}} options
     * @returns {Promise<{thumbnails?}>}
     */
    importExternal: function (file, targets, options) {
      var formData = new FormData();
      formData.append("file", file);
      formData.append("job_id", _jobId);
      formData.append("targets", JSON.stringify(targets));
      formData.append("blend_mode", options.blendMode || "replace");
      formData.append("fit_mode", options.fitMode || "nearest_stretch");
      return _postForm("/workbench/import-external", formData);
    },
  };
})();

window.MergePanel = MergePanel;
