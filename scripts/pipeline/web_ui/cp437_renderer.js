/**
 * cp437_renderer.js -- Browser-side CP437 cell rendering using canvas.
 *
 * Loads the cp437_atlas.png (192x192, 16x16 grid of 12x12 glyphs) and
 * renders cells (glyph + fg + bg) to a canvas context. Used by the
 * workbench grid when native XP cell data is available.
 *
 * [FLOW:WORKBENCH] [DATA-CONTRACT:CP437]
 */
"use strict";

var CP437Renderer = (function () {
  var GLYPH_W = 12;
  var GLYPH_H = 12;
  var ATLAS_COLS = 16;
  var atlas = null;
  var atlasReady = false;
  var onReadyCallbacks = [];

  /**
   * Load the font atlas image. Call once on page init.
   * @param {string} [src] - Path to atlas PNG. Defaults to "assets/cp437_atlas.png".
   */
  function loadAtlas(src) {
    src = src || "assets/cp437_atlas.png";
    atlas = new Image();
    atlas.onload = function () {
      atlasReady = true;
      onReadyCallbacks.forEach(function (cb) { cb(); });
      onReadyCallbacks = [];
    };
    atlas.onerror = function () {
      console.error("CP437Renderer: Failed to load atlas from " + src);
    };
    atlas.src = src;
  }

  /**
   * Register a callback for when the atlas is loaded.
   * @param {function} cb
   */
  function onReady(cb) {
    if (atlasReady) {
      cb();
    } else {
      onReadyCallbacks.push(cb);
    }
  }

  /**
   * @returns {boolean} Whether the atlas is loaded and ready.
   */
  function isReady() {
    return atlasReady;
  }

  /**
   * Render a single CP437 cell to a canvas context.
   *
   * @param {CanvasRenderingContext2D} ctx - Target canvas context.
   * @param {number} x - Destination X (pixels).
   * @param {number} y - Destination Y (pixels).
   * @param {number} glyph - CP437 code point (0-255).
   * @param {string} fg - Foreground color as CSS string (e.g. "#ff6600").
   * @param {string} bg - Background color as CSS string (e.g. "#000000").
   * @param {number} [scale=1] - Render scale multiplier.
   */
  function renderCell(ctx, x, y, glyph, fg, bg, scale) {
    scale = scale || 1;
    var w = GLYPH_W * scale;
    var h = GLYPH_H * scale;

    // Draw background
    ctx.fillStyle = bg;
    ctx.fillRect(x, y, w, h);

    // Skip glyph 0 (transparent/empty)
    if (glyph === 0) return;
    if (!atlasReady) return;

    // Source rect in atlas
    var sx = (glyph % ATLAS_COLS) * GLYPH_W;
    var sy = Math.floor(glyph / ATLAS_COLS) * GLYPH_H;

    // Use offscreen canvas for colorization
    var offscreen = document.createElement("canvas");
    offscreen.width = GLYPH_W;
    offscreen.height = GLYPH_H;
    var octx = offscreen.getContext("2d");

    // Draw glyph from atlas (white-on-black or white-on-transparent)
    octx.drawImage(atlas, sx, sy, GLYPH_W, GLYPH_H, 0, 0, GLYPH_W, GLYPH_H);

    // Colorize: replace non-black pixels with fg color
    octx.globalCompositeOperation = "source-in";
    octx.fillStyle = fg;
    octx.fillRect(0, 0, GLYPH_W, GLYPH_H);

    // Draw to target
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(offscreen, 0, 0, GLYPH_W, GLYPH_H, x, y, w, h);
  }

  /**
   * Render a grid of cells to a canvas.
   *
   * @param {CanvasRenderingContext2D} ctx - Target canvas context.
   * @param {Array} cells - Array of {glyph, fg, bg} objects.
   * @param {number} cols - Number of columns in the grid.
   * @param {number} [scale=1] - Render scale.
   */
  function renderGrid(ctx, cells, cols, scale) {
    scale = scale || 1;
    var w = GLYPH_W * scale;
    var h = GLYPH_H * scale;
    for (var i = 0; i < cells.length; i++) {
      var cell = cells[i];
      if (!cell) continue;
      var col = i % cols;
      var row = Math.floor(i / cols);
      renderCell(ctx, col * w, row * h, cell.glyph, cell.fg, cell.bg, scale);
    }
  }

  /**
   * Render a single cell to a standalone canvas element.
   * Useful for thumbnails.
   *
   * @param {number} glyph - CP437 code point.
   * @param {string} fg - Foreground CSS color.
   * @param {string} bg - Background CSS color.
   * @param {number} [scale=1]
   * @returns {HTMLCanvasElement}
   */
  function renderCellToCanvas(glyph, fg, bg, scale) {
    scale = scale || 1;
    var canvas = document.createElement("canvas");
    canvas.width = GLYPH_W * scale;
    canvas.height = GLYPH_H * scale;
    var ctx = canvas.getContext("2d");
    renderCell(ctx, 0, 0, glyph, fg, bg, scale);
    return canvas;
  }

  return {
    GLYPH_W: GLYPH_W,
    GLYPH_H: GLYPH_H,
    loadAtlas: loadAtlas,
    onReady: onReady,
    isReady: isReady,
    renderCell: renderCell,
    renderGrid: renderGrid,
    renderCellToCanvas: renderCellToCanvas,
  };
})();
