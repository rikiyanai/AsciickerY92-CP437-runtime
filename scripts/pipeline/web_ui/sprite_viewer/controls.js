/**
 * controls.js -- Playback controls for the sprite viewer.
 *
 * Provides play/pause, FPS slider, loop toggle, frame navigation,
 * zoom controls, background toggle, and angle/animation selectors.
 *
 * Uses requestAnimationFrame with delta-time throttle for smooth
 * FPS-accurate playback.
 *
 * Tags: [FLOW:VIEWER] [DATA-CONTRACT:FRAME-SEQUENCE]
 */

"use strict";

// ============================================================================
// Viewer State
// ============================================================================

/**
 * Create the viewer state object.
 *
 * @param {Object} frameSequence - FrameSequence from loaders
 * @returns {Object} Mutable viewer state
 */
function createViewerState(frameSequence) {
  return {
    sequence: frameSequence,
    playing: false,
    loop: true,
    fps: frameSequence.fps || 8,
    zoom: 2,  // Default 2x zoom for pixel art
    background: "checker",  // "checker" | "black" | "white" | "magenta"
    currentAngle: 0,
    currentAnim: 0,
    currentFrame: 0,
    // Animation timing
    _lastTime: 0,
    _accumulator: 0,
    _rafId: null
  };
}


// ============================================================================
// Background patterns
// ============================================================================

var BACKGROUNDS = {
  checker: null,  // Drawn procedurally
  black: "#000000",
  white: "#FFFFFF",
  magenta: "#FF00FF"
};

/**
 * Draw a checkerboard background pattern on a canvas.
 *
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} width  - Canvas width
 * @param {number} height - Canvas height
 * @param {number} size   - Checker square size (default 8)
 */
function drawCheckerboard(ctx, width, height, size) {
  size = size || 8;
  var light = "#CCCCCC";
  var dark = "#999999";

  for (var y = 0; y < height; y += size) {
    for (var x = 0; x < width; x += size) {
      var isEven = ((Math.floor(x / size) + Math.floor(y / size)) % 2 === 0);
      ctx.fillStyle = isEven ? light : dark;
      ctx.fillRect(x, y, size, size);
    }
  }
}


// ============================================================================
// Frame rendering
// ============================================================================

/**
 * Render the current frame onto the viewer canvas.
 *
 * @param {Object} state  - Viewer state
 * @param {HTMLCanvasElement} canvas - Target canvas element
 */
function renderCurrentFrame(state, canvas) {
  var ctx = canvas.getContext("2d");
  var seq = state.sequence;

  // Get current frame
  var frame = seq.getFrame(state.currentAngle, state.currentAnim, state.currentFrame);
  if (!frame) {
    // No frame at current indices -- clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  // Set canvas size based on frame size and zoom
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

  // Load and draw frame image
  var img = new Image();
  img.onload = function() {
    ctx.imageSmoothingEnabled = false;  // Pixel-perfect scaling
    ctx.drawImage(img, 0, 0, displayW, displayH);
  };
  img.src = "data:image/png;base64," + frame.data;
}


// ============================================================================
// Animation loop
// ============================================================================

/**
 * Animation tick function using requestAnimationFrame.
 * Advances frames based on accumulated delta time.
 *
 * @param {number} timestamp - requestAnimationFrame timestamp
 * @param {Object} state     - Viewer state
 * @param {HTMLCanvasElement} canvas - Target canvas
 * @param {Function} [onFrameChange] - Optional callback on frame advance
 */
function _tick(timestamp, state, canvas, onFrameChange) {
  if (!state.playing) {
    return;
  }

  var delta = timestamp - state._lastTime;
  state._lastTime = timestamp;
  state._accumulator += delta;

  var interval = 1000 / state.fps;
  var advanced = false;

  while (state._accumulator >= interval) {
    advanceFrame(state);
    state._accumulator -= interval;
    advanced = true;
  }

  if (advanced) {
    renderCurrentFrame(state, canvas);
    if (onFrameChange) {
      onFrameChange(state);
    }
  }

  state._rafId = requestAnimationFrame(function(ts) {
    _tick(ts, state, canvas, onFrameChange);
  });
}


// ============================================================================
// Playback controls
// ============================================================================

/**
 * Start or resume playback.
 *
 * @param {Object} state  - Viewer state
 * @param {HTMLCanvasElement} canvas - Target canvas
 * @param {Function} [onFrameChange] - Optional callback on frame advance
 */
function play(state, canvas, onFrameChange) {
  if (state.playing) { return; }
  state.playing = true;
  state._lastTime = performance.now();
  state._accumulator = 0;
  state._rafId = requestAnimationFrame(function(ts) {
    _tick(ts, state, canvas, onFrameChange);
  });
}

/**
 * Pause playback.
 *
 * @param {Object} state - Viewer state
 */
function pause(state) {
  state.playing = false;
  if (state._rafId != null) {
    cancelAnimationFrame(state._rafId);
    state._rafId = null;
  }
}

/**
 * Toggle play/pause.
 *
 * @param {Object} state  - Viewer state
 * @param {HTMLCanvasElement} canvas - Target canvas
 * @param {Function} [onFrameChange] - Optional callback
 * @returns {boolean} New playing state
 */
function togglePlayPause(state, canvas, onFrameChange) {
  if (state.playing) {
    pause(state);
  } else {
    play(state, canvas, onFrameChange);
  }
  return state.playing;
}


// ============================================================================
// Frame navigation
// ============================================================================

/**
 * Advance to the next frame in the current animation.
 *
 * @param {Object} state - Viewer state
 * @returns {boolean} True if frame was advanced, false if at end (no loop)
 */
function advanceFrame(state) {
  var anims = state.sequence.anims;
  var maxFrame = (anims[state.currentAnim] || 1) - 1;

  if (state.currentFrame < maxFrame) {
    state.currentFrame++;
    return true;
  }

  if (state.loop) {
    state.currentFrame = 0;
    return true;
  }

  // At last frame, not looping -- stop
  state.playing = false;
  return false;
}

/**
 * Go to the previous frame.
 *
 * @param {Object} state - Viewer state
 */
function prevFrame(state) {
  var anims = state.sequence.anims;
  var maxFrame = (anims[state.currentAnim] || 1) - 1;

  if (state.currentFrame > 0) {
    state.currentFrame--;
  } else if (state.loop) {
    state.currentFrame = maxFrame;
  }
}

/**
 * Go to a specific frame index.
 *
 * @param {Object} state      - Viewer state
 * @param {number} frameIdx   - Target frame index
 */
function goToFrame(state, frameIdx) {
  var anims = state.sequence.anims;
  var maxFrame = (anims[state.currentAnim] || 1) - 1;
  state.currentFrame = Math.max(0, Math.min(frameIdx, maxFrame));
}


// ============================================================================
// Parameter controls
// ============================================================================

/**
 * Set playback FPS.
 *
 * @param {Object} state - Viewer state
 * @param {number} fps   - Target FPS (1-60)
 */
function setFPS(state, fps) {
  state.fps = Math.max(1, Math.min(60, fps));
}

/**
 * Set zoom level.
 *
 * @param {Object} state - Viewer state
 * @param {number} zoom  - Zoom factor (1, 2, 4, or "fit")
 */
function setZoom(state, zoom) {
  state.zoom = Math.max(1, Math.min(8, zoom));
}

/**
 * Set background mode.
 *
 * @param {Object} state      - Viewer state
 * @param {string} background - "checker", "black", "white", or "magenta"
 */
function setBackground(state, background) {
  if (BACKGROUNDS.hasOwnProperty(background) || background === "checker") {
    state.background = background;
  }
}

/**
 * Toggle loop mode.
 *
 * @param {Object} state - Viewer state
 * @returns {boolean} New loop state
 */
function toggleLoop(state) {
  state.loop = !state.loop;
  return state.loop;
}


// ============================================================================
// Angle and animation selection
// ============================================================================

/**
 * Set the current angle index.
 *
 * @param {Object} state    - Viewer state
 * @param {number} angleIdx - Angle index
 */
function setAngle(state, angleIdx) {
  var maxAngle = state.sequence.angles - 1;
  state.currentAngle = Math.max(0, Math.min(angleIdx, maxAngle));
  state.currentFrame = 0;  // Reset to first frame when changing angle
}

/**
 * Set the current animation index.
 *
 * @param {Object} state   - Viewer state
 * @param {number} animIdx - Animation index
 */
function setAnim(state, animIdx) {
  var maxAnim = state.sequence.anims.length - 1;
  state.currentAnim = Math.max(0, Math.min(animIdx, maxAnim));
  state.currentFrame = 0;  // Reset to first frame when changing anim
}


// ============================================================================
// Angle/Anim label helpers
// ============================================================================

var ANGLE_LABELS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"];

/**
 * Get human-readable label for an angle index.
 *
 * @param {number} angleIdx - Angle index
 * @param {number} totalAngles - Total angle count
 * @returns {string} Label like "S" or "Angle 0"
 */
function getAngleLabel(angleIdx, totalAngles) {
  if (totalAngles <= 8 && angleIdx < ANGLE_LABELS.length) {
    return ANGLE_LABELS[angleIdx];
  }
  return "Angle " + angleIdx;
}


// ============================================================================
// UI builder
// ============================================================================

/**
 * Build the viewer control panel DOM elements.
 *
 * Creates all control UI elements and wires them to the viewer state.
 * Appends to the given container element.
 *
 * @param {HTMLElement} container - Container element for controls
 * @param {Object} state         - Viewer state
 * @param {HTMLCanvasElement} canvas - Viewer canvas
 * @returns {Object} Control element references for updating
 */
function buildControlPanel(container, state, canvas) {
  // Clear container
  container.innerHTML = "";

  var controls = {};

  // --- Play/Pause button ---
  var playBtn = document.createElement("button");
  playBtn.className = "viewer-btn viewer-play-btn";
  playBtn.textContent = "Play";
  playBtn.title = "Play/Pause (Space)";
  playBtn.addEventListener("click", function() {
    var playing = togglePlayPause(state, canvas, function(s) {
      _updateControlsUI(controls, s);
    });
    playBtn.textContent = playing ? "Pause" : "Play";
  });
  controls.playBtn = playBtn;

  // --- Prev/Next frame buttons ---
  var prevBtn = document.createElement("button");
  prevBtn.className = "viewer-btn";
  prevBtn.textContent = "Prev";
  prevBtn.title = "Previous frame";
  prevBtn.addEventListener("click", function() {
    prevFrame(state);
    renderCurrentFrame(state, canvas);
    _updateControlsUI(controls, state);
  });

  var nextBtn = document.createElement("button");
  nextBtn.className = "viewer-btn";
  nextBtn.textContent = "Next";
  nextBtn.title = "Next frame";
  nextBtn.addEventListener("click", function() {
    advanceFrame(state);
    renderCurrentFrame(state, canvas);
    _updateControlsUI(controls, state);
  });

  // --- FPS slider ---
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
    setFPS(state, parseInt(fpsSlider.value, 10));
    fpsLabel.textContent = "FPS: " + state.fps;
  });
  controls.fpsSlider = fpsSlider;
  controls.fpsLabel = fpsLabel;

  // --- Loop toggle ---
  var loopBtn = document.createElement("button");
  loopBtn.className = "viewer-btn" + (state.loop ? " active" : "");
  loopBtn.textContent = "Loop";
  loopBtn.title = "Toggle loop";
  loopBtn.addEventListener("click", function() {
    var looping = toggleLoop(state);
    loopBtn.className = "viewer-btn" + (looping ? " active" : "");
  });
  controls.loopBtn = loopBtn;

  // --- Zoom buttons ---
  var zoomGroup = document.createElement("span");
  zoomGroup.className = "viewer-zoom-group";
  [1, 2, 4].forEach(function(z) {
    var btn = document.createElement("button");
    btn.className = "viewer-btn" + (state.zoom === z ? " active" : "");
    btn.textContent = z + "x";
    btn.addEventListener("click", function() {
      setZoom(state, z);
      renderCurrentFrame(state, canvas);
      // Update active state
      var btns = zoomGroup.querySelectorAll("button");
      for (var i = 0; i < btns.length; i++) {
        btns[i].className = "viewer-btn" + (parseInt(btns[i].textContent, 10) === z ? " active" : "");
      }
    });
    zoomGroup.appendChild(btn);
  });
  controls.zoomGroup = zoomGroup;

  // --- Background toggle ---
  var bgGroup = document.createElement("span");
  bgGroup.className = "viewer-bg-group";
  ["checker", "black", "white", "magenta"].forEach(function(bg) {
    var btn = document.createElement("button");
    btn.className = "viewer-btn viewer-bg-btn" + (state.background === bg ? " active" : "");
    btn.textContent = bg.charAt(0).toUpperCase() + bg.slice(1);
    btn.addEventListener("click", function() {
      setBackground(state, bg);
      renderCurrentFrame(state, canvas);
      var btns = bgGroup.querySelectorAll("button");
      for (var i = 0; i < btns.length; i++) {
        btns[i].className = "viewer-btn viewer-bg-btn" +
          (btns[i].textContent.toLowerCase() === bg ? " active" : "");
      }
    });
    bgGroup.appendChild(btn);
  });
  controls.bgGroup = bgGroup;

  // --- Angle selector (if multi-angle) ---
  var angleSelect = null;
  if (state.sequence.angles > 1) {
    angleSelect = document.createElement("select");
    angleSelect.className = "viewer-select";
    for (var a = 0; a < state.sequence.angles; a++) {
      var opt = document.createElement("option");
      opt.value = String(a);
      opt.textContent = getAngleLabel(a, state.sequence.angles);
      angleSelect.appendChild(opt);
    }
    angleSelect.addEventListener("change", function() {
      setAngle(state, parseInt(angleSelect.value, 10));
      renderCurrentFrame(state, canvas);
      _updateControlsUI(controls, state);
    });
    controls.angleSelect = angleSelect;
  }

  // --- Anim selector (if multi-anim) ---
  var animSelect = null;
  if (state.sequence.anims.length > 1) {
    animSelect = document.createElement("select");
    animSelect.className = "viewer-select";
    for (var i = 0; i < state.sequence.anims.length; i++) {
      var optAnim = document.createElement("option");
      optAnim.value = String(i);
      optAnim.textContent = "Anim " + i + " (" + state.sequence.anims[i] + " frames)";
      animSelect.appendChild(optAnim);
    }
    animSelect.addEventListener("change", function() {
      setAnim(state, parseInt(animSelect.value, 10));
      renderCurrentFrame(state, canvas);
      _updateControlsUI(controls, state);
    });
    controls.animSelect = animSelect;
  }

  // --- Frame counter ---
  var frameCounter = document.createElement("span");
  frameCounter.className = "viewer-frame-counter";
  controls.frameCounter = frameCounter;

  // --- Assemble the panel ---
  var row1 = document.createElement("div");
  row1.className = "viewer-controls-row";
  row1.appendChild(playBtn);
  row1.appendChild(prevBtn);
  row1.appendChild(nextBtn);
  row1.appendChild(loopBtn);
  row1.appendChild(frameCounter);

  var row2 = document.createElement("div");
  row2.className = "viewer-controls-row";
  row2.appendChild(fpsLabel);
  row2.appendChild(fpsSlider);

  var row3 = document.createElement("div");
  row3.className = "viewer-controls-row";
  var zoomLabel = document.createElement("span");
  zoomLabel.className = "viewer-label";
  zoomLabel.textContent = "Zoom: ";
  row3.appendChild(zoomLabel);
  row3.appendChild(zoomGroup);

  var bgLabel = document.createElement("span");
  bgLabel.className = "viewer-label";
  bgLabel.textContent = " BG: ";
  row3.appendChild(bgLabel);
  row3.appendChild(bgGroup);

  var row4 = null;
  if (angleSelect || animSelect) {
    row4 = document.createElement("div");
    row4.className = "viewer-controls-row";
    if (angleSelect) {
      var aLabel = document.createElement("span");
      aLabel.className = "viewer-label";
      aLabel.textContent = "Angle: ";
      row4.appendChild(aLabel);
      row4.appendChild(angleSelect);
    }
    if (animSelect) {
      var animLabel = document.createElement("span");
      animLabel.className = "viewer-label";
      animLabel.textContent = " Anim: ";
      row4.appendChild(animLabel);
      row4.appendChild(animSelect);
    }
  }

  container.appendChild(row1);
  container.appendChild(row2);
  container.appendChild(row3);
  if (row4) { container.appendChild(row4); }

  // Initial UI update
  _updateControlsUI(controls, state);

  return controls;
}


/**
 * Update control UI elements to reflect current state.
 *
 * @param {Object} controls - Control element references
 * @param {Object} state    - Viewer state
 */
function _updateControlsUI(controls, state) {
  var maxFrame = (state.sequence.anims[state.currentAnim] || 1) - 1;
  if (controls.frameCounter) {
    controls.frameCounter.textContent =
      "Frame " + (state.currentFrame + 1) + "/" + (maxFrame + 1);
  }
}
