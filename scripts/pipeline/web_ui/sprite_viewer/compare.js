/**
 * compare.js -- Side-by-side sprite comparison mode.
 *
 * Loads PNG and XP versions of the same asset, displays them side-by-side
 * with synchronized playback, and optionally shows a pixel diff overlay.
 *
 * Tags: [FLOW:VIEWER] [DATA-CONTRACT:FRAME-SEQUENCE]
 */

"use strict";

// ============================================================================
// Compare State
// ============================================================================

/**
 * Create comparison state from two FrameSequences.
 *
 * @param {Object} pngSequence - FrameSequence from PNG loader
 * @param {Object} xpSequence  - FrameSequence from XP loader
 * @returns {Object} Compare state
 */
function createCompareState(pngSequence, xpSequence) {
  return {
    png: pngSequence,
    xp: xpSequence,
    playing: false,
    loop: true,
    fps: Math.max(pngSequence.fps || 8, xpSequence.fps || 8),
    zoom: 2,
    background: "checker",
    currentAngle: 0,
    currentAnim: 0,
    currentFrame: 0,
    showDiff: false,
    // Animation timing
    _lastTime: 0,
    _accumulator: 0,
    _rafId: null,
    // Computed values
    angles: Math.min(pngSequence.angles, xpSequence.angles),
    anims: _mergeAnims(pngSequence.anims, xpSequence.anims)
  };
}


/**
 * Merge two anim arrays by taking the minimum frame count per anim.
 *
 * @param {Array} a - First anims array
 * @param {Array} b - Second anims array
 * @returns {Array} Merged anims array
 */
function _mergeAnims(a, b) {
  var len = Math.min(a.length, b.length);
  var result = [];
  for (var i = 0; i < len; i++) {
    result.push(Math.min(a[i], b[i]));
  }
  return result;
}


// ============================================================================
// Compare rendering
// ============================================================================

/**
 * Render both canvases with the current frame from each source.
 *
 * @param {Object} state          - Compare state
 * @param {HTMLCanvasElement} pngCanvas - Left canvas (PNG)
 * @param {HTMLCanvasElement} xpCanvas  - Right canvas (XP)
 * @param {HTMLCanvasElement} [diffCanvas] - Optional diff overlay canvas
 */
function renderCompareFrame(state, pngCanvas, xpCanvas, diffCanvas) {
  _renderSingleFrame(state.png, state, pngCanvas);
  _renderSingleFrame(state.xp, state, xpCanvas);

  if (state.showDiff && diffCanvas) {
    renderDiffOverlay(state, pngCanvas, xpCanvas, diffCanvas);
  }
}


/**
 * Render a single frame from a sequence onto a canvas.
 *
 * @param {Object} sequence - FrameSequence
 * @param {Object} state    - Compare state (for angle/anim/frame/zoom/bg)
 * @param {HTMLCanvasElement} canvas - Target canvas
 */
function _renderSingleFrame(sequence, state, canvas) {
  var ctx = canvas.getContext("2d");
  var frame = sequence.getFrame(state.currentAngle, state.currentAnim, state.currentFrame);

  if (!frame) {
    canvas.width = 64;
    canvas.height = 64;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#333";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#999";
    ctx.font = "12px monospace";
    ctx.fillText("No frame", 4, 36);
    return;
  }

  var displayW = frame.width * state.zoom;
  var displayH = frame.height * state.zoom;
  canvas.width = displayW;
  canvas.height = displayH;

  // Draw background
  if (state.background === "checker") {
    drawCheckerboard(ctx, displayW, displayH);
  } else {
    ctx.fillStyle = BACKGROUNDS[state.background] || "#000000";
    ctx.fillRect(0, 0, displayW, displayH);
  }

  // Draw frame
  var img = new Image();
  img.onload = function() {
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(img, 0, 0, displayW, displayH);
  };
  img.src = "data:image/png;base64," + frame.data;
}


// ============================================================================
// Pixel diff overlay
// ============================================================================

/**
 * Compute and render a pixel difference heatmap between two canvases.
 *
 * @param {Object} state                  - Compare state
 * @param {HTMLCanvasElement} pngCanvas    - PNG source canvas
 * @param {HTMLCanvasElement} xpCanvas     - XP source canvas
 * @param {HTMLCanvasElement} diffCanvas   - Diff output canvas
 */
function renderDiffOverlay(state, pngCanvas, xpCanvas, diffCanvas) {
  var w = Math.min(pngCanvas.width, xpCanvas.width);
  var h = Math.min(pngCanvas.height, xpCanvas.height);
  diffCanvas.width = w;
  diffCanvas.height = h;

  var pngCtx = pngCanvas.getContext("2d");
  var xpCtx = xpCanvas.getContext("2d");
  var diffCtx = diffCanvas.getContext("2d");

  try {
    var pngData = pngCtx.getImageData(0, 0, w, h);
    var xpData = xpCtx.getImageData(0, 0, w, h);
  } catch (e) {
    // Canvas may not have image data yet (async image load)
    diffCtx.fillStyle = "#333";
    diffCtx.fillRect(0, 0, w, h);
    return;
  }

  var diffImageData = diffCtx.createImageData(w, h);
  var matchCount = 0;
  var totalPixels = w * h;

  for (var i = 0; i < pngData.data.length; i += 4) {
    var dr = Math.abs(pngData.data[i] - xpData.data[i]);
    var dg = Math.abs(pngData.data[i + 1] - xpData.data[i + 1]);
    var db = Math.abs(pngData.data[i + 2] - xpData.data[i + 2]);
    var diff = dr + dg + db;

    if (diff === 0) {
      matchCount++;
      // Matching pixel: dark green
      diffImageData.data[i] = 0;
      diffImageData.data[i + 1] = 40;
      diffImageData.data[i + 2] = 0;
      diffImageData.data[i + 3] = 255;
    } else {
      // Difference: red intensity proportional to diff
      var intensity = Math.min(255, diff);
      diffImageData.data[i] = intensity;
      diffImageData.data[i + 1] = 0;
      diffImageData.data[i + 2] = 0;
      diffImageData.data[i + 3] = 255;
    }
  }

  diffCtx.putImageData(diffImageData, 0, 0);

  // Store match percentage for UI
  state._lastMatchPercent = totalPixels > 0
    ? ((matchCount / totalPixels) * 100).toFixed(1)
    : "0.0";
}


/**
 * Get per-frame pixel match metrics.
 *
 * @param {Object} state - Compare state (after renderDiffOverlay)
 * @returns {Object} {matchPercent}
 */
function getCompareMetrics(state) {
  return {
    matchPercent: state._lastMatchPercent || "N/A"
  };
}


// ============================================================================
// Compare animation loop
// ============================================================================

/**
 * Animation tick for compare mode (synchronizes both canvases).
 *
 * @param {number} timestamp - requestAnimationFrame timestamp
 * @param {Object} state     - Compare state
 * @param {Object} canvases  - {png: HTMLCanvasElement, xp: HTMLCanvasElement, diff: HTMLCanvasElement}
 * @param {Function} [onFrameChange] - Optional callback
 */
function _compareTick(timestamp, state, canvases, onFrameChange) {
  if (!state.playing) {
    return;
  }

  var delta = timestamp - state._lastTime;
  state._lastTime = timestamp;
  state._accumulator += delta;

  var interval = 1000 / state.fps;
  var advanced = false;

  while (state._accumulator >= interval) {
    _advanceCompareFrame(state);
    state._accumulator -= interval;
    advanced = true;
  }

  if (advanced) {
    renderCompareFrame(state, canvases.png, canvases.xp, canvases.diff);
    if (onFrameChange) {
      onFrameChange(state);
    }
  }

  state._rafId = requestAnimationFrame(function(ts) {
    _compareTick(ts, state, canvases, onFrameChange);
  });
}


/**
 * Advance to the next frame in compare mode.
 *
 * @param {Object} state - Compare state
 */
function _advanceCompareFrame(state) {
  var maxFrame = (state.anims[state.currentAnim] || 1) - 1;

  if (state.currentFrame < maxFrame) {
    state.currentFrame++;
    return;
  }

  if (state.loop) {
    state.currentFrame = 0;
    return;
  }

  state.playing = false;
}


// ============================================================================
// Compare playback controls
// ============================================================================

/**
 * Start synchronized playback in compare mode.
 *
 * @param {Object} state    - Compare state
 * @param {Object} canvases - {png, xp, diff} canvas elements
 * @param {Function} [onFrameChange] - Optional callback
 */
function comparePlay(state, canvases, onFrameChange) {
  if (state.playing) { return; }
  state.playing = true;
  state._lastTime = performance.now();
  state._accumulator = 0;
  state._rafId = requestAnimationFrame(function(ts) {
    _compareTick(ts, state, canvases, onFrameChange);
  });
}

/**
 * Pause synchronized playback.
 *
 * @param {Object} state - Compare state
 */
function comparePause(state) {
  state.playing = false;
  if (state._rafId != null) {
    cancelAnimationFrame(state._rafId);
    state._rafId = null;
  }
}

/**
 * Toggle synchronized play/pause.
 *
 * @param {Object} state    - Compare state
 * @param {Object} canvases - {png, xp, diff} canvas elements
 * @param {Function} [onFrameChange] - Optional callback
 * @returns {boolean} New playing state
 */
function toggleComparePlayPause(state, canvases, onFrameChange) {
  if (state.playing) {
    comparePause(state);
  } else {
    comparePlay(state, canvases, onFrameChange);
  }
  return state.playing;
}


// ============================================================================
// Compare UI builder
// ============================================================================

/**
 * Build the side-by-side comparison view.
 *
 * Creates two canvas panels with shared controls and optional diff overlay.
 *
 * @param {HTMLElement} container     - Container element
 * @param {Object}      pngSequence  - FrameSequence from PNG
 * @param {Object}      xpSequence   - FrameSequence from XP
 * @returns {Object} {state, canvases, controls}
 */
function buildCompareView(container, pngSequence, xpSequence) {
  container.innerHTML = "";

  var state = createCompareState(pngSequence, xpSequence);

  // --- Canvas panels ---
  var panelRow = document.createElement("div");
  panelRow.className = "compare-panels";

  var pngPanel = document.createElement("div");
  pngPanel.className = "compare-panel";
  var pngTitle = document.createElement("div");
  pngTitle.className = "compare-title";
  pngTitle.textContent = "PNG Source";
  var pngCanvas = document.createElement("canvas");
  pngCanvas.className = "viewer-canvas";
  pngPanel.appendChild(pngTitle);
  pngPanel.appendChild(pngCanvas);

  var xpPanel = document.createElement("div");
  xpPanel.className = "compare-panel";
  var xpTitle = document.createElement("div");
  xpTitle.className = "compare-title";
  xpTitle.textContent = "XP Rendered";
  var xpCanvas = document.createElement("canvas");
  xpCanvas.className = "viewer-canvas";
  xpPanel.appendChild(xpTitle);
  xpPanel.appendChild(xpCanvas);

  var diffPanel = document.createElement("div");
  diffPanel.className = "compare-panel compare-diff-panel";
  diffPanel.style.display = "none";
  var diffTitle = document.createElement("div");
  diffTitle.className = "compare-title";
  diffTitle.textContent = "Pixel Diff";
  var diffCanvas = document.createElement("canvas");
  diffCanvas.className = "viewer-canvas";
  diffPanel.appendChild(diffTitle);
  diffPanel.appendChild(diffCanvas);

  panelRow.appendChild(pngPanel);
  panelRow.appendChild(xpPanel);
  panelRow.appendChild(diffPanel);

  var canvases = {
    png: pngCanvas,
    xp: xpCanvas,
    diff: diffCanvas
  };

  // --- Controls ---
  var controlsContainer = document.createElement("div");
  controlsContainer.className = "compare-controls";

  // Play/pause
  var playBtn = document.createElement("button");
  playBtn.className = "viewer-btn";
  playBtn.textContent = "Play";
  playBtn.addEventListener("click", function() {
    var playing = toggleComparePlayPause(state, canvases, function(s) {
      _updateCompareUI(metricsEl, s);
    });
    playBtn.textContent = playing ? "Pause" : "Play";
  });

  // Prev/next
  var prevBtn = document.createElement("button");
  prevBtn.className = "viewer-btn";
  prevBtn.textContent = "Prev";
  prevBtn.addEventListener("click", function() {
    if (state.currentFrame > 0) {
      state.currentFrame--;
    } else if (state.loop) {
      state.currentFrame = (state.anims[state.currentAnim] || 1) - 1;
    }
    renderCompareFrame(state, pngCanvas, xpCanvas, diffCanvas);
    _updateCompareUI(metricsEl, state);
  });

  var nextBtn = document.createElement("button");
  nextBtn.className = "viewer-btn";
  nextBtn.textContent = "Next";
  nextBtn.addEventListener("click", function() {
    _advanceCompareFrame(state);
    renderCompareFrame(state, pngCanvas, xpCanvas, diffCanvas);
    _updateCompareUI(metricsEl, state);
  });

  // Diff toggle
  var diffBtn = document.createElement("button");
  diffBtn.className = "viewer-btn";
  diffBtn.textContent = "Show Diff";
  diffBtn.addEventListener("click", function() {
    state.showDiff = !state.showDiff;
    diffPanel.style.display = state.showDiff ? "" : "none";
    diffBtn.textContent = state.showDiff ? "Hide Diff" : "Show Diff";
    diffBtn.className = "viewer-btn" + (state.showDiff ? " active" : "");
    if (state.showDiff) {
      renderCompareFrame(state, pngCanvas, xpCanvas, diffCanvas);
      _updateCompareUI(metricsEl, state);
    }
  });

  // FPS
  var fpsLabel = document.createElement("label");
  fpsLabel.className = "viewer-label";
  fpsLabel.textContent = "FPS: " + state.fps;
  var fpsSlider = document.createElement("input");
  fpsSlider.type = "range";
  fpsSlider.min = "1";
  fpsSlider.max = "60";
  fpsSlider.value = String(state.fps);
  fpsSlider.className = "viewer-slider";
  fpsSlider.addEventListener("input", function() {
    state.fps = Math.max(1, Math.min(60, parseInt(fpsSlider.value, 10)));
    fpsLabel.textContent = "FPS: " + state.fps;
  });

  // Metrics display
  var metricsEl = document.createElement("div");
  metricsEl.className = "compare-metrics";

  // Assemble controls
  var row1 = document.createElement("div");
  row1.className = "viewer-controls-row";
  row1.appendChild(playBtn);
  row1.appendChild(prevBtn);
  row1.appendChild(nextBtn);
  row1.appendChild(diffBtn);
  row1.appendChild(fpsLabel);
  row1.appendChild(fpsSlider);

  controlsContainer.appendChild(row1);
  controlsContainer.appendChild(metricsEl);

  container.appendChild(panelRow);
  container.appendChild(controlsContainer);

  // Initial render
  renderCompareFrame(state, pngCanvas, xpCanvas, diffCanvas);
  _updateCompareUI(metricsEl, state);

  return {
    state: state,
    canvases: canvases,
    controls: { playBtn: playBtn, metricsEl: metricsEl }
  };
}


/**
 * Update compare UI metrics display.
 *
 * @param {HTMLElement} metricsEl - Metrics container
 * @param {Object}     state     - Compare state
 */
function _updateCompareUI(metricsEl, state) {
  var maxFrame = (state.anims[state.currentAnim] || 1);
  var text = "Frame " + (state.currentFrame + 1) + "/" + maxFrame;
  if (state.showDiff) {
    var metrics = getCompareMetrics(state);
    text += " | Match: " + metrics.matchPercent + "%";
  }
  metricsEl.textContent = text;
}
