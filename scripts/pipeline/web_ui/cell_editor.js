/**
 * cell_editor.js -- Grid cell selection, info panel, and native cell editing.
 *
 * Provides the base selectCell() that workbench.html inline JS overrides
 * for shift-click multi-select and swap logic.
 *
 * When native XP cell data is available (wbState.hasNativeCells), also provides:
 * - Glyph picker (16x16 CP437 grid)
 * - FG/BG color pickers
 * - Live preview canvas
 * - Save to server via PUT /api/workbench/<session>/cell/<idx>/data
 *
 * [FLOW:WORKBENCH] [DATA-CONTRACT:GRID-CELL] [DATA-CONTRACT:CP437]
 */
"use strict";

(function () {
  /**
   * Select a grid cell and update the info panel.
   *
   * The inline script in workbench.html wraps this function to add
   * multi-select, swap mode, and session-aware behavior.
   *
   * @param {number} index - Zero-based cell index in the grid.
   * @param {object} cell - Cell data: {angle, anim, frame, proj, width, height}.
   * @param {Event} [event] - Click event (used by override for shift detection).
   */
  function selectCell(index, cell, event) {
    // Deselect all, then select this one
    var allCells = document.querySelectorAll("#gridPreview .grid-cell");
    allCells.forEach(function (c) { c.classList.remove("selected"); });
    if (allCells[index]) allCells[index].classList.add("selected");

    // Update info panel
    var dirLabel = "-";
    if (typeof window.getDirectionLabel === "function" && cell.angle !== undefined) {
      var totalAngles = parseInt(document.getElementById("angles").value, 10) || 1;
      dirLabel = window.getDirectionLabel(cell.angle, totalAngles);
    }

    document.getElementById("infoSelected").textContent = "#" + index;
    document.getElementById("infoAngle").textContent = dirLabel;
    document.getElementById("infoAnim").textContent = cell.anim !== undefined ? cell.anim : "-";
    document.getElementById("infoFrame").textContent = cell.frame !== undefined ? cell.frame : "-";
    document.getElementById("infoProj").textContent = cell.proj !== undefined ? cell.proj : "-";
    document.getElementById("infoDims").textContent =
      (cell.width || "?") + "x" + (cell.height || "?") + "px";

    // Update workbench state
    if (typeof window.updateWbState === "function") {
      window.updateWbState({ selectedCell: index });
    }

    // Update status
    var statusBar = document.getElementById("statusBar");
    if (statusBar) {
      statusBar.textContent = "Selected cell #" + index +
        " (direction: " + dirLabel + ", anim: " + (cell.anim || 0) +
        ", frame: " + (cell.frame || 0) + ")";
    }
  }

  window.selectCell = selectCell;

  // ---- Native Cell Editor ----

  var editorState = {
    cellIndex: -1,
    glyph: 0,
    fg: "#ffffff",
    bg: "#000000",
    layer: 2,
  };

  /**
   * Build the cell editor panel HTML inside #cellEditorPanel.
   * Called once on DOMContentLoaded.
   */
  function initCellEditor() {
    var panel = document.getElementById("cellEditorPanel");
    if (!panel) return;

    panel.innerHTML = [
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">',
      '  <canvas id="cellEditorPreview" width="48" height="48" style="border:1px solid var(--border,#333);image-rendering:pixelated;background:#000;"></canvas>',
      '  <div style="display:flex;flex-direction:column;gap:4px;">',
      '    <label style="font-size:11px;color:var(--fg-muted,#888);">FG: <input type="color" id="cellEditorFg" value="#ffffff" style="width:32px;height:20px;border:none;padding:0;cursor:pointer;"></label>',
      '    <label style="font-size:11px;color:var(--fg-muted,#888);">BG: <input type="color" id="cellEditorBg" value="#000000" style="width:32px;height:20px;border:none;padding:0;cursor:pointer;"></label>',
      '    <span id="cellEditorGlyphLabel" style="font-size:11px;color:var(--fg-muted,#888);">Glyph: 0</span>',
      '  </div>',
      '</div>',
      '<div id="cellEditorGlyphGrid" style="display:grid;grid-template-columns:repeat(16,18px);gap:1px;max-height:200px;overflow-y:auto;margin-bottom:8px;"></div>',
      '<div style="display:flex;gap:6px;">',
      '  <button id="cellEditorSave" style="font-size:11px;padding:2px 10px;">Save</button>',
      '  <button id="cellEditorCancel" style="font-size:11px;padding:2px 10px;">Cancel</button>',
      '</div>',
    ].join("\n");

    // Build 16x16 glyph grid
    var grid = document.getElementById("cellEditorGlyphGrid");
    for (var g = 0; g < 256; g++) {
      var btn = document.createElement("div");
      btn.dataset.glyph = g;
      btn.style.cssText = "width:18px;height:18px;display:flex;align-items:center;justify-content:center;cursor:pointer;border:1px solid var(--border,#333);font-size:10px;color:var(--fg,#ccc);background:var(--bg-deep,#111);";
      // Render glyph as a tiny canvas using CP437Renderer
      btn.title = "Glyph " + g;
      btn.textContent = g; // Placeholder — replaced by canvas on atlas ready
      btn.onclick = (function (glyph) {
        return function () { pickGlyph(glyph); };
      })(g);
      grid.appendChild(btn);
    }

    // Replace placeholder text with atlas-rendered glyphs when ready
    if (typeof CP437Renderer !== "undefined") {
      CP437Renderer.onReady(function () {
        var cells = grid.querySelectorAll("div[data-glyph]");
        cells.forEach(function (cell) {
          var g = parseInt(cell.dataset.glyph, 10);
          var canvas = CP437Renderer.renderCellToCanvas(g, "#ffffff", "#000000", 1);
          canvas.style.width = "12px";
          canvas.style.height = "12px";
          canvas.style.imageRendering = "pixelated";
          cell.textContent = "";
          cell.appendChild(canvas);
        });
      });
    }

    // Wire events
    document.getElementById("cellEditorFg").addEventListener("input", function () {
      editorState.fg = this.value;
      updatePreview();
    });
    document.getElementById("cellEditorBg").addEventListener("input", function () {
      editorState.bg = this.value;
      updatePreview();
    });
    document.getElementById("cellEditorSave").addEventListener("click", saveCell);
    document.getElementById("cellEditorCancel").addEventListener("click", closeCellEditor);
  }

  function pickGlyph(g) {
    editorState.glyph = g;
    document.getElementById("cellEditorGlyphLabel").textContent = "Glyph: " + g;
    // Highlight selected glyph
    var grid = document.getElementById("cellEditorGlyphGrid");
    grid.querySelectorAll("div[data-glyph]").forEach(function (el) {
      el.style.borderColor = (parseInt(el.dataset.glyph, 10) === g)
        ? "var(--accent, #f90)" : "var(--border, #333)";
    });
    updatePreview();
  }

  function updatePreview() {
    if (typeof CP437Renderer === "undefined" || !CP437Renderer.isReady()) return;
    var canvas = document.getElementById("cellEditorPreview");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, 48, 48);
    CP437Renderer.renderCell(ctx, 0, 0, editorState.glyph, editorState.fg, editorState.bg, 4);
  }

  function saveCell() {
    var API_BASE = window.ASSET_API_BASE || "/api";
    var sessionId = null;

    // Get session ID from workbench state
    var sessionEl = document.getElementById("sessionInfo");
    if (sessionEl && sessionEl.dataset.sessionId) {
      sessionId = sessionEl.dataset.sessionId;
    }
    // Fallback: check inline script's sessionId
    if (!sessionId && window._wbSessionId) {
      sessionId = window._wbSessionId;
    }

    if (!sessionId || editorState.cellIndex < 0) {
      if (typeof window.workbenchLog === "function") {
        window.workbenchLog("Cannot save: no session or cell selected", "log-err");
      }
      return;
    }

    var activeLayer = (window.wbState && window.wbState.activeLayer !== undefined)
      ? window.wbState.activeLayer : 2;

    fetch(API_BASE + "/workbench/" + sessionId + "/cell/" + editorState.cellIndex + "/data", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        layer: activeLayer,
        glyph: editorState.glyph,
        fg: editorState.fg,
        bg: editorState.bg,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          if (typeof window.workbenchLog === "function") {
            window.workbenchLog("Cell save error: " + data.error, "log-err");
          }
          return;
        }
        // Re-render the grid cell
        var gridEls = document.querySelectorAll("#gridPreview .grid-cell");
        if (gridEls[editorState.cellIndex] && typeof window.renderNativeCellToGrid === "function") {
          window.renderNativeCellToGrid(gridEls[editorState.cellIndex], {
            glyph: editorState.glyph,
            fg: editorState.fg,
            bg: editorState.bg,
          });
        }
        if (typeof window.workbenchLog === "function") {
          window.workbenchLog("Saved cell #" + editorState.cellIndex + " (glyph " + editorState.glyph + ")");
        }
        closeCellEditor();
      })
      .catch(function (err) {
        if (typeof window.workbenchLog === "function") {
          window.workbenchLog("Cell save failed: " + err.message, "log-err");
        }
      });
  }

  /**
   * Open the cell editor for a specific grid cell index.
   * Fetches current cell data from server and populates editor.
   *
   * @param {number} index - Grid cell index.
   * @param {string} sessionId - Workbench session ID.
   */
  function openCellEditor(index, sessionId) {
    var panel = document.getElementById("cellEditorPanel");
    if (!panel) return;

    editorState.cellIndex = index;
    panel.style.display = "block";

    var API_BASE = window.ASSET_API_BASE || "/api";
    fetch(API_BASE + "/workbench/" + sessionId + "/cell/" + index + "/data")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) return;
        var activeLayer = (window.wbState && window.wbState.activeLayer !== undefined)
          ? window.wbState.activeLayer : 2;
        var layerData = data.layers && data.layers[activeLayer];
        if (layerData) {
          editorState.glyph = layerData.glyph;
          editorState.fg = layerData.fg;
          editorState.bg = layerData.bg;
          document.getElementById("cellEditorFg").value = layerData.fg;
          document.getElementById("cellEditorBg").value = layerData.bg;
          document.getElementById("cellEditorGlyphLabel").textContent = "Glyph: " + layerData.glyph;
          pickGlyph(layerData.glyph);
        } else if (typeof window.workbenchLog === "function") {
          window.workbenchLog("No data for layer " + activeLayer + " in cell #" + index, "log-warn");
        }
      })
      .catch(function () {
        // Cell data not available — use defaults
      });
  }

  function closeCellEditor() {
    var panel = document.getElementById("cellEditorPanel");
    if (panel) panel.style.display = "none";
    editorState.cellIndex = -1;
  }

  // Expose for workbench.html
  window.openCellEditor = openCellEditor;
  window.closeCellEditor = closeCellEditor;
  window.initCellEditor = initCellEditor;

  // Auto-init when DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCellEditor);
  } else {
    initCellEditor();
  }
})();
