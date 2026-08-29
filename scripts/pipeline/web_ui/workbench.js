/**
 * workbench.js -- Workbench state, extraction, grid rendering, and drag-drop.
 *
 * Provides the core state object (wbState), file upload handling,
 * sprite extraction via /api/analyze, grid layout rendering, and
 * drag-drop support for moving sprites between source and grid.
 *
 * [FLOW:WORKBENCH] [DATA-CONTRACT:GRID-CELL]
 */
"use strict";

/* =========================================================================
 * Global state
 * ========================================================================= */

var wbState = {
  gridAssignment: [],
  extractionResult: null,
  sourceId: null,
  sourceImageUrl: null,
  selectedCell: null,
  status: "",
  cellSizeRounded: false,
  originalCellW: null,
  originalCellH: null,
  hasNativeCells: false,
  activeLayer: 2,
  nativeCellCache: null,
};

/**
 * Merge partial state into wbState.
 * @param {object} partial - Keys to merge.
 */
function updateWbState(partial) {
  Object.keys(partial).forEach(function (k) {
    wbState[k] = partial[k];
  });
  if (partial.status !== undefined) {
    var bar = document.getElementById("statusBar");
    if (bar) bar.textContent = partial.status;
  }
}
window.updateWbState = updateWbState;

/* =========================================================================
 * DOM references (resolved on load)
 * ========================================================================= */

var sourceCanvas, extractBtn, assignBtn, drawBoxBtn;

document.addEventListener("DOMContentLoaded", function () {
  sourceCanvas = document.getElementById("sourceCanvas");
  extractBtn = document.getElementById("extractBtn");
  assignBtn = document.getElementById("assignBtn");
  drawBoxBtn = document.getElementById("drawBoxBtn");
});

/* =========================================================================
 * API base
 * ========================================================================= */

var API_BASE = window.ASSET_API_BASE || "/api";

/* =========================================================================
 * File upload: load image onto source canvas
 * ========================================================================= */

(function () {
  var fileInput = document.getElementById("fileInput");
  if (!fileInput) return;

  fileInput.addEventListener("change", function (e) {
    var file = e.target.files[0];
    if (!file) return;

    var reader = new FileReader();
    reader.onload = function (ev) {
      var img = new Image();
      img.onload = function () {
        var canvas = document.getElementById("sourceCanvas");
        canvas.width = img.width;
        canvas.height = img.height;
        var ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);

        // Store source on server
        var formData = new FormData();
        formData.append("file", file);
        fetch(API_BASE + "/workbench/store-source", {
          method: "POST",
          body: formData,
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            updateWbState({
              sourceId: data.source_id,
              sourceImageUrl: ev.target.result,
              status: "Image loaded: " + img.width + "x" + img.height + "px",
            });
            if (typeof window.workbenchLog === "function") {
              window.workbenchLog("Source uploaded: " + file.name + " (" + img.width + "x" + img.height + ")");
            }
          })
          .catch(function (err) {
            updateWbState({ status: "Upload failed: " + err.message });
          });

        // Enable extraction controls
        var eb = document.getElementById("extractBtn");
        var db = document.getElementById("drawBoxBtn");
        if (eb) eb.disabled = false;
        if (db) db.disabled = false;
      };
      img.src = ev.target.result;
    };
    reader.readAsDataURL(file);
  });
})();

/* =========================================================================
 * Sprite extraction
 * ========================================================================= */

function runExtraction() {
  if (!wbState.sourceId) {
    updateWbState({ status: "No source image. Upload one first." });
    return;
  }

  var threshold = parseInt(document.getElementById("threshold").value, 10) || 128;
  var minSize = parseInt(document.getElementById("minSize").value, 10) || 16;
  var bgColor = document.getElementById("bgColor").value || "";

  updateWbState({ status: "Extracting sprites..." });

  fetch(API_BASE + "/workbench/extract-sprites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_id: wbState.sourceId,
      alpha_threshold: threshold,
      min_size: minSize,
      bg_color: bgColor || null,
    }),
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.error) {
        updateWbState({ status: "Extraction failed: " + data.error });
        return;
      }
      updateWbState({
        extractionResult: data,
        status: "Found " + data.sprites.length + " sprites" +
          (data.filtered_count ? " (" + data.filtered_count + " filtered by size)" : ""),
      });
      renderExtractionOverlay(data);
      var ab = document.getElementById("assignBtn");
      if (ab) ab.disabled = false;
      if (typeof window.workbenchLog === "function") {
        window.workbenchLog("Extracted " + data.sprites.length + " sprites (method: " + (data.method || "auto") + ")");
      }
    })
    .catch(function (err) {
      updateWbState({ status: "Extraction failed: " + err.message });
    });
}
window.runExtraction = runExtraction;

(function () {
  var eb = document.getElementById("extractBtn");
  if (eb) eb.addEventListener("click", runExtraction);
})();

/* =========================================================================
 * Extraction overlay (bounding boxes on source image)
 * ========================================================================= */

function renderExtractionOverlay(extraction) {
  var overlay = document.getElementById("bboxOverlay");
  if (!overlay) return;
  overlay.innerHTML = "";

  var canvas = document.getElementById("sourceCanvas");
  var scaleX = canvas.clientWidth / (canvas.width || 1);
  var scaleY = canvas.clientHeight / (canvas.height || 1);

  var sprites = extraction.sprites || [];
  sprites.forEach(function (s, i) {
    var bbox = s.bbox || [0, 0, 0, 0];
    var div = document.createElement("div");
    div.className = "sprite-bbox" + (s.manual ? " manual" : "");
    div.style.left = (bbox[0] * scaleX) + "px";
    div.style.top = (bbox[1] * scaleY) + "px";
    div.style.width = (bbox[2] * scaleX) + "px";
    div.style.height = (bbox[3] * scaleY) + "px";
    div.title = "#" + i + " (" + bbox[2] + "x" + bbox[3] + ")";

    // Make draggable for dropping onto grid cells
    div.draggable = true;
    if (s.manual) div.draggable = true;
    div.dataset.bboxIndex = i;
    div.dataset.bboxX = bbox[0];
    div.dataset.bboxY = bbox[1];
    div.dataset.bboxW = bbox[2];
    div.dataset.bboxH = bbox[3];
    div.addEventListener("dragstart", function (e) {
      e.dataTransfer.setData("application/x-bbox", JSON.stringify({
        boxId: i,
        x: bbox[0],
        y: bbox[1],
        w: bbox[2],
        h: bbox[3],
      }));
      e.dataTransfer.effectAllowed = "copy";
    });

    // Click to select/deselect manual bboxes
    (function (idx) {
      div.addEventListener("click", function (e) {
        e.stopPropagation();
        var allBoxes = overlay.querySelectorAll(".sprite-bbox");
        var wasSelected = div.classList.contains("selected");
        allBoxes.forEach(function (b) { b.classList.remove("selected"); });
        if (!wasSelected) {
          div.classList.add("selected");
          wbState.selectedBoxIdx = idx;
        } else {
          wbState.selectedBoxIdx = -1;
        }
        var delBtn = document.getElementById("deleteBoxBtn");
        if (delBtn) delBtn.disabled = (wbState.selectedBoxIdx < 0);
      });
    })(i);

    overlay.appendChild(div);
  });

  // Info text
  var info = document.getElementById("extractionInfo");
  if (info) {
    info.textContent = sprites.length + " sprites detected" +
      (extraction.method ? " (" + extraction.method + ")" : "");
  }
}
window.renderExtractionOverlay = renderExtractionOverlay;

/* =========================================================================
 * Draw box mode (manual bounding boxes)
 * ========================================================================= */

(function () {
  var drawMode = false;
  var drawStart = null;
  var drawPreview = null;
  var manualBoxes = [];
  wbState.selectedBoxIdx = -1;

  var db = document.getElementById("drawBoxBtn");
  if (!db) return;

  db.addEventListener("click", function () {
    drawMode = !drawMode;
    db.classList.toggle("active", drawMode);
    var canvas = document.getElementById("sourceCanvas");
    if (canvas) canvas.style.cursor = drawMode ? "crosshair" : "";
    updateWbState({ status: drawMode ? "Draw mode: click and drag on source image to create boxes" : "Draw mode off" });
  });

  var wrap = document.getElementById("sourceWrap");
  if (!wrap) return;

  wrap.addEventListener("mousedown", function (e) {
    if (!drawMode) return;
    var rect = wrap.getBoundingClientRect();
    drawStart = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    drawPreview = document.createElement("div");
    drawPreview.className = "bbox-drawing";
    drawPreview.style.left = drawStart.x + "px";
    drawPreview.style.top = drawStart.y + "px";
    wrap.appendChild(drawPreview);
    e.preventDefault();
  });

  document.addEventListener("mousemove", function (e) {
    if (!drawPreview || !drawStart) return;
    var rect = wrap.getBoundingClientRect();
    var cx = e.clientX - rect.left;
    var cy = e.clientY - rect.top;
    var x = Math.min(drawStart.x, cx);
    var y = Math.min(drawStart.y, cy);
    var w = Math.abs(cx - drawStart.x);
    var h = Math.abs(cy - drawStart.y);
    drawPreview.style.left = x + "px";
    drawPreview.style.top = y + "px";
    drawPreview.style.width = w + "px";
    drawPreview.style.height = h + "px";
  });

  document.addEventListener("mouseup", function (e) {
    if (!drawPreview || !drawStart) return;
    var rect = wrap.getBoundingClientRect();
    var cx = e.clientX - rect.left;
    var cy = e.clientY - rect.top;
    var canvas = document.getElementById("sourceCanvas");
    var scaleX = (canvas.width || 1) / (canvas.clientWidth || 1);
    var scaleY = (canvas.height || 1) / (canvas.clientHeight || 1);

    var x = Math.round(Math.min(drawStart.x, cx) * scaleX);
    var y = Math.round(Math.min(drawStart.y, cy) * scaleY);
    var w = Math.round(Math.abs(cx - drawStart.x) * scaleX);
    var h = Math.round(Math.abs(cy - drawStart.y) * scaleY);

    drawPreview.remove();
    drawPreview = null;
    drawStart = null;

    if (w < 4 || h < 4) return; // too small

    var boxIdx = manualBoxes.length;
    manualBoxes.push({ x: x, y: y, w: w, h: h });
    updateWbState({ manualBoxes: manualBoxes });

    // Add as a manual sprite to extraction result
    var extraction = wbState.extractionResult || { sprites: [], method: "manual", filtered_count: 0 };
    extraction.sprites.push({
      bbox: [x, y, w, h],
      width: w,
      height: h,
      source_id: wbState.sourceId,
      manual: true,
    });
    updateWbState({ extractionResult: extraction });
    renderExtractionOverlay(extraction);

    var ab = document.getElementById("assignBtn");
    if (ab) ab.disabled = false;
    if (typeof window.workbenchLog === "function") {
      window.workbenchLog("Drew box #" + boxIdx + ": " + w + "x" + h + " at (" + x + "," + y + ")");
    }
  });

  // Delete box button / key
  var delBtn = document.getElementById("deleteBoxBtn");
  if (delBtn) {
    delBtn.addEventListener("click", function () {
      if (wbState.selectedBoxIdx >= 0 && wbState.extractionResult) {
        wbState.extractionResult.sprites.splice(wbState.selectedBoxIdx, 1);
        manualBoxes.splice(wbState.selectedBoxIdx, 1);
        wbState.selectedBoxIdx = -1;
        delBtn.disabled = true;
        renderExtractionOverlay(wbState.extractionResult);
      }
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Delete" && wbState.selectedBoxIdx >= 0) {
      if (delBtn) delBtn.click();
    }
    if (e.key === "Escape" && drawMode) {
      drawMode = false;
      db.classList.remove("active");
      updateWbState({ status: "Draw mode off" });
    }
  });
})();

/* =========================================================================
 * Grid assignment: arrange sprites into angle x frame grid
 * ========================================================================= */

function runAssignment() {
  var extraction = wbState.extractionResult;
  var sprites = (extraction && Array.isArray(extraction.sprites)) ? extraction.sprites : [];

  var angles = parseInt(document.getElementById("angles").value, 10) || 1;
  var framesStr = document.getElementById("frames").value || "1";
  var offsetX = parseInt(document.getElementById("gridOffsetX") && document.getElementById("gridOffsetX").value, 10) || 0;
  var offsetY = parseInt(document.getElementById("gridOffsetY") && document.getElementById("gridOffsetY").value, 10) || 0;
  var framesList = parseFramesInput(framesStr);
  var totalCols = 0;
  framesList.forEach(function (n) { totalCols += n; });
  var totalCells = angles * totalCols;

  // Build grid assignment: map sprites to cells
  var gridCells = [];
  var spriteIdx = 0;
  for (var a = 0; a < angles; a++) {
    var col = 0;
    for (var animIdx = 0; animIdx < framesList.length; animIdx++) {
      for (var f = 0; f < framesList[animIdx]; f++) {
        var sprite = sprites[spriteIdx] || null;
        gridCells.push({
          angle: a,
          anim: animIdx,
          frame: f,
          proj: 0,
          width: sprite ? (sprite.width || sprite.bbox[2]) : 0,
          height: sprite ? (sprite.height || sprite.bbox[3]) : 0,
          thumbnail: sprite ? sprite.thumbnail : null,
          imageDataUrl: sprite ? sprite.imageDataUrl : null,
          bbox: sprite ? sprite.bbox : null,
          source_id: sprite ? (sprite.source_id || wbState.sourceId) : null,
        });
        spriteIdx++;
        col++;
      }
    }
  }

  updateWbState({
    gridAssignment: gridCells,
    status: "Grid: " + angles + " direction(s) x " + totalCols + " column(s) = " + totalCells + " cells" +
      " (" + Math.min(spriteIdx, sprites.length) + " sprites assigned" +
      (sprites.length === 0 ? "; empty scaffold" : "") + ")" +
      " | offset=(" + offsetX + "," + offsetY + ")",
  });

  renderGrid(gridCells, angles, framesList, offsetX, offsetY);
}
window.runAssignment = runAssignment;

/**
 * Parse frames input string into array of integers.
 * Accepts: "8", "1,8", "1x8", "1,8,6".
 */
function parseFramesInput(value) {
  var normalized = String(value || "1").replace(/[xX]/g, ",").replace(/\s+/g, ",");
  var parsed = normalized.split(",")
    .map(function (s) { return parseInt(String(s).trim(), 10); })
    .filter(function (n) { return !isNaN(n) && n > 0; });
  return parsed.length > 0 ? parsed : [1];
}
window.parseFramesInput = parseFramesInput;

(function () {
  var ab = document.getElementById("assignBtn");
  if (ab) ab.addEventListener("click", function () { window.runAssignment(); });
  var sb = document.getElementById("setGridSizeBtn");
  if (sb) sb.addEventListener("click", function () { window.runAssignment(); });
})();

/* =========================================================================
 * Grid rendering
 * ========================================================================= */

function renderGrid(gridCells, angles, framesList, offsetX, offsetY) {
  var container = document.getElementById("gridPreview");
  if (!container) return;
  container.innerHTML = "";
  container.dataset.offsetX = String(parseInt(offsetX, 10) || 0);
  container.dataset.offsetY = String(parseInt(offsetY, 10) || 0);
  if (typeof window.applyGridPreviewZoom === "function") {
    window.applyGridPreviewZoom();
  }

  var totalCols = 0;
  framesList.forEach(function (n) { totalCols += n; });
  var cellIdx = 0;

  for (var a = 0; a < angles; a++) {
    var row = document.createElement("div");
    row.style.display = "flex";
    row.style.gap = "2px";
    row.style.marginBottom = "2px";

    for (var col = 0; col < totalCols; col++) {
      var cell = gridCells[cellIdx] || {};
      var div = document.createElement("div");
      div.className = "grid-cell";
      div.style.width = "50px";
      div.style.height = "50px";
      div.style.position = "relative";
      div.dataset.cellIndex = cellIdx;

      // Thumbnail or placeholder
      if (cell.thumbnail || cell.imageDataUrl) {
        var img = document.createElement("img");
        img.src = cell.imageDataUrl || ("data:image/png;base64," + cell.thumbnail);
        img.style.width = "48px";
        img.style.height = "48px";
        img.style.imageRendering = "pixelated";
        img.draggable = false;
        div.appendChild(img);
      } else {
        div.style.background = "#1a1a2e";
      }

      // Label
      var label = document.createElement("div");
      label.className = "grid-cell-label";
      label.textContent = cellIdx;
      div.appendChild(label);

      // Click handler
      (function (idx, c) {
        div.addEventListener("click", function (e) {
          if (typeof window.selectCell === "function") {
            window.selectCell(idx, c, e);
          }
        });
      })(cellIdx, cell);

      // Drag-drop target (receive bbox drops from source)
      div.addEventListener("dragover", function (e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        this.classList.add("drag-over");
      });
      div.addEventListener("dragleave", function () {
        this.classList.remove("drag-over");
      });
      (function (idx, c) {
        div.addEventListener("drop", function (e) {
          e.preventDefault();
          this.classList.remove("drag-over");
          var bboxData = e.dataTransfer.getData("application/x-bbox");
          if (bboxData) {
            var bbox = JSON.parse(bboxData);
            if (typeof window.handleBboxDrop === "function") {
              window.handleBboxDrop(bbox, idx, c);
            }
          }
        });
      })(cellIdx, cell);

      // Grid-to-grid drag (for reordering)
      div.draggable = true;
      (function (idx) {
        div.addEventListener("dragstart", function (e) {
          e.dataTransfer.setData("application/x-grid-cell", String(idx));
          e.dataTransfer.effectAllowed = "move";
        });
        div.addEventListener("drop", function (e) {
          var srcIdx = e.dataTransfer.getData("application/x-grid-cell");
          if (srcIdx !== "" && srcIdx !== undefined) {
            e.preventDefault();
            e.stopPropagation();
            this.classList.remove("drag-over");
            if (typeof window.triggerSwap === "function") {
              window.triggerSwap(parseInt(srcIdx, 10), idx);
            }
          }
        });
      })(cellIdx);

      row.appendChild(div);
      cellIdx++;
    }
    container.appendChild(row);
  }
}

/* =========================================================================
 * Exports for inline script access
 * ========================================================================= */

window.wbState = wbState;
