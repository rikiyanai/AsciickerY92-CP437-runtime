/**
 * sprite_viewer_panel.js -- Reusable sprite preview panel component.
 *
 * Provides a zoomable image viewer with optional selection mode for
 * drag-box extraction seed. Used by branches.html, wizard configure,
 * and workbench.
 *
 * API:
 *   var panel = createViewerPanel(container, options)
 *   panel.loadImage(url)              -- Load static image from URL
 *   panel.loadFrameSequence(apiData)  -- Load animated FrameSequence (requires frame_sequence.js)
 *   panel.loadXPFromPath(path)        -- Load XP file from server path (animation)
 *   panel.getSelection()              -- Get drag-box bbox {x, y, w, h} or null
 *   panel.destroy()                   -- Clean up event listeners
 *
 * Events (via options callbacks):
 *   onSelectionChange(bbox)    -- When drag-box selection changes
 *   onZoomChange(level)        -- When zoom level changes
 *
 * Tags: [FLOW:VIEWER] [DATA-CONTRACT:FRAME-SEQUENCE]
 */

"use strict";

function createViewerPanel(container, options) {
  options = options || {};
  var mode = options.mode || "preview";    // "preview" or "select"
  var showControls = options.showControls !== false;
  var initialZoom = options.zoom || 1;
  var autoFitOnLoad = options.fitOnLoad !== false;
  var onSelectionChange = options.onSelectionChange || function() {};
  var onZoomChange = options.onZoomChange || function() {};

  // State
  var zoom = initialZoom;
  var panX = 0, panY = 0;
  var isDragging = false;
  var isSelecting = false;
  var dragStartX = 0, dragStartY = 0;
  var lastMouseX = 0, lastMouseY = 0;
  var selection = null;  // {x, y, w, h} in image pixels
  var currentImage = null;
  var gridOverlay = {
    enabled: false,
    cellW: 0,
    cellH: 0,
    offsetX: 0,
    offsetY: 0
  };

  // Animation state (active when loadFrameSequence is called)
  var animSeq = null;       // FrameSequence object or null
  var animPlaying = false;
  var animLoop = true;
  var animAngle = 0;
  var animAnim = 0;
  var animFrameIdx = 0;
  var animFps = 8;
  var animRafId = null;
  var animLastTime = 0;
  var animAccum = 0;
  var frameImageCache = {};  // "angle-anim-frame" -> Image
  var animNeedsInitialFit = false;

  // DOM structure
  var wrapper = document.createElement("div");
  wrapper.className = "viewer-panel";
  wrapper.style.cssText = "display:flex;flex-direction:column;height:100%;background:#0a0a1a;border-radius:6px;overflow:hidden;";

  // Controls bar
  var controlBar = document.createElement("div");
  controlBar.style.cssText = "display:flex;gap:6px;padding:6px 8px;background:#111827;align-items:center;font-size:11px;color:#9ca3af;";

  var zoomOutBtn = document.createElement("button");
  zoomOutBtn.textContent = "\u2212";
  zoomOutBtn.title = "Zoom out";
  zoomOutBtn.style.cssText = "border:1px solid #333;background:none;color:#ccc;padding:2px 6px;border-radius:3px;cursor:pointer;";

  var zoomLabel = document.createElement("span");
  zoomLabel.textContent = zoom + "x";
  zoomLabel.style.cssText = "min-width:28px;text-align:center;";

  var zoomInBtn = document.createElement("button");
  zoomInBtn.textContent = "+";
  zoomInBtn.title = "Zoom in";
  zoomInBtn.style.cssText = zoomOutBtn.style.cssText;

  var resetBtn = document.createElement("button");
  resetBtn.textContent = "Reset";
  resetBtn.title = "Reset zoom and pan";
  resetBtn.style.cssText = "border:1px solid #333;background:none;color:#888;padding:2px 8px;border-radius:3px;cursor:pointer;margin-left:auto;font-size:10px;";

  var infoLabel = document.createElement("span");
  infoLabel.style.cssText = "color:#6b7280;font-size:10px;margin-left:8px;";

  if (showControls) {
    controlBar.appendChild(zoomOutBtn);
    controlBar.appendChild(zoomLabel);
    controlBar.appendChild(zoomInBtn);
    controlBar.appendChild(resetBtn);
    controlBar.appendChild(infoLabel);
    wrapper.appendChild(controlBar);
  }

  // Canvas area
  var canvasWrapper = document.createElement("div");
  canvasWrapper.style.cssText = "flex:1;position:relative;overflow:hidden;cursor:grab;";

  var canvas = document.createElement("canvas");
  canvas.style.cssText = "position:absolute;top:0;left:0;image-rendering:pixelated;";
  canvasWrapper.appendChild(canvas);

  // Selection overlay
  var selBox = document.createElement("div");
  selBox.style.cssText = "position:absolute;border:2px solid #3b82f6;background:rgba(59,130,246,0.15);pointer-events:none;display:none;";
  var selLabel = document.createElement("div");
  selLabel.style.cssText = "position:absolute;bottom:-18px;left:0;font-size:10px;color:#3b82f6;white-space:nowrap;";
  selBox.appendChild(selLabel);
  canvasWrapper.appendChild(selBox);

  wrapper.appendChild(canvasWrapper);

  // === Animation controls bar (hidden until loadFrameSequence) ===
  var animControlBar = document.createElement("div");
  animControlBar.style.cssText = "display:none;gap:4px;padding:4px 8px;background:#111827;align-items:center;font-size:11px;color:#9ca3af;flex-wrap:wrap;";

  var btnStyle = "border:1px solid #333;background:none;color:#ccc;padding:1px 6px;border-radius:3px;cursor:pointer;font-size:11px;";

  var animPlayBtn = document.createElement("button");
  animPlayBtn.textContent = "Play";
  animPlayBtn.title = "Play / Pause";
  animPlayBtn.style.cssText = btnStyle;
  animControlBar.appendChild(animPlayBtn);

  var animPrevBtn = document.createElement("button");
  animPrevBtn.textContent = "\u25C0";
  animPrevBtn.title = "Previous frame";
  animPrevBtn.style.cssText = btnStyle;
  animControlBar.appendChild(animPrevBtn);

  var animNextBtn = document.createElement("button");
  animNextBtn.textContent = "\u25B6";
  animNextBtn.title = "Next frame";
  animNextBtn.style.cssText = btnStyle;
  animControlBar.appendChild(animNextBtn);

  var animFrameLabel = document.createElement("span");
  animFrameLabel.style.cssText = "color:#9ca3af;min-width:50px;text-align:center;font-size:10px;";
  animControlBar.appendChild(animFrameLabel);

  var animSep1 = document.createElement("span");
  animSep1.textContent = "|";
  animSep1.style.cssText = "color:#333;margin:0 2px;";
  animControlBar.appendChild(animSep1);

  var animAngleLabel = document.createElement("span");
  animAngleLabel.textContent = "Angle:";
  animAngleLabel.style.cssText = "color:#6b7280;font-size:10px;display:none;";
  animControlBar.appendChild(animAngleLabel);

  var animAngleSelect = document.createElement("select");
  animAngleSelect.style.cssText = "background:#1a1a2e;color:#ccc;border:1px solid #333;border-radius:3px;padding:1px 2px;font-size:10px;display:none;";
  animControlBar.appendChild(animAngleSelect);

  var animAnimLabel = document.createElement("span");
  animAnimLabel.textContent = "Anim:";
  animAnimLabel.style.cssText = "color:#6b7280;font-size:10px;display:none;margin-left:4px;";
  animControlBar.appendChild(animAnimLabel);

  var animAnimSelect = document.createElement("select");
  animAnimSelect.style.cssText = animAngleSelect.style.cssText;
  animControlBar.appendChild(animAnimSelect);

  var animFpsLabel = document.createElement("span");
  animFpsLabel.textContent = "8 fps";
  animFpsLabel.style.cssText = "color:#6b7280;font-size:10px;margin-left:auto;";
  animControlBar.appendChild(animFpsLabel);

  var animFpsSlider = document.createElement("input");
  animFpsSlider.type = "range";
  animFpsSlider.min = "1";
  animFpsSlider.max = "30";
  animFpsSlider.value = "8";
  animFpsSlider.style.cssText = "width:50px;";
  animControlBar.appendChild(animFpsSlider);

  wrapper.appendChild(animControlBar);

  container.appendChild(wrapper);

  var ctx = canvas.getContext("2d");
  var onWindowResize = function() {
    if (!currentImage) return;
    if (autoFitOnLoad) {
      fitToViewport();
    }
    render();
  };
  window.addEventListener("resize", onWindowResize);

  function setZoom(newZoom) {
    zoom = Math.max(0.25, Math.min(16, newZoom));
    zoomLabel.textContent = zoom + "x";
    render();
    onZoomChange(zoom);
  }

  function fitToViewport() {
    if (!currentImage) return;
    var imgW = currentImage.naturalWidth || currentImage.width || 1;
    var imgH = currentImage.naturalHeight || currentImage.height || 1;
    var vw = canvasWrapper.clientWidth || 200;
    var vh = canvasWrapper.clientHeight || 200;
    var fit = Math.min(vw / imgW, vh / imgH);
    if (!isFinite(fit) || fit <= 0) fit = 1;
    zoom = Math.max(0.125, Math.min(16, fit));
    panX = Math.round((vw - (imgW * zoom)) / 2);
    panY = Math.round((vh - (imgH * zoom)) / 2);
    zoomLabel.textContent = (Math.round(zoom * 100) / 100) + "x";
    onZoomChange(zoom);
  }

  function render() {
    var dpr = window.devicePixelRatio || 1;

    if (!currentImage) {
      var cw = canvasWrapper.clientWidth || 200;
      var ch = canvasWrapper.clientHeight || 200;
      canvas.width = cw * dpr;
      canvas.height = ch * dpr;
      canvas.style.width = cw + "px";
      canvas.style.height = ch + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = "#0a0a1a";
      ctx.fillRect(0, 0, cw, ch);
      ctx.fillStyle = "#555";
      ctx.font = "12px monospace";
      ctx.textAlign = "center";
      ctx.fillText("No image loaded", cw / 2, ch / 2);
      return;
    }

    var imgW = currentImage.naturalWidth || currentImage.width;
    var imgH = currentImage.naturalHeight || currentImage.height;
    var displayW = Math.floor(imgW * zoom);
    var displayH = Math.floor(imgH * zoom);

    var logicalW = Math.max(displayW, canvasWrapper.clientWidth || 200);
    var logicalH = Math.max(displayH, canvasWrapper.clientHeight || 200);
    canvas.width = logicalW * dpr;
    canvas.height = logicalH * dpr;
    canvas.style.width = logicalW + "px";
    canvas.style.height = logicalH + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;

    // Checker background
    drawChecker(ctx, logicalW, logicalH);

    // Draw image with pan offset — integer coords for pixel-perfect rendering
    ctx.drawImage(currentImage, Math.round(panX), Math.round(panY), displayW, displayH);

    infoLabel.textContent = imgW + "\u00d7" + imgH + "px";

    // Optional grid overlay for cell/offset calibration
    if (gridOverlay.enabled && gridOverlay.cellW > 0 && gridOverlay.cellH > 0 && zoom > 0) {
      var startX = Math.round(panX + gridOverlay.offsetX * zoom);
      var startY = Math.round(panY + gridOverlay.offsetY * zoom);
      var imgLeft = Math.round(panX);
      var imgTop = Math.round(panY);
      var endX = Math.round(panX + displayW);
      var endY = Math.round(panY + displayH);
      var stepX = Math.max(1, Math.round(gridOverlay.cellW * zoom));
      var stepY = Math.max(1, Math.round(gridOverlay.cellH * zoom));

      ctx.save();
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      for (var gx = startX; gx <= endX; gx += stepX) {
        var majorX = (Math.round((gx - startX) / stepX) % 8) === 0;
        ctx.strokeStyle = "rgba(0,0,0,0.70)";
        ctx.beginPath();
        ctx.moveTo(gx + 1, imgTop);
        ctx.lineTo(gx + 1, endY);
        ctx.stroke();
        ctx.strokeStyle = majorX ? "rgba(255,215,0,0.90)" : "rgba(255,255,255,0.75)";
        ctx.beginPath();
        ctx.moveTo(gx, imgTop);
        ctx.lineTo(gx, endY);
        ctx.stroke();
      }
      for (var gy = startY; gy <= endY; gy += stepY) {
        var majorY = (Math.round((gy - startY) / stepY) % 8) === 0;
        ctx.strokeStyle = "rgba(0,0,0,0.70)";
        ctx.beginPath();
        ctx.moveTo(imgLeft, gy + 1);
        ctx.lineTo(endX, gy + 1);
        ctx.stroke();
        ctx.strokeStyle = majorY ? "rgba(255,215,0,0.90)" : "rgba(255,255,255,0.75)";
        ctx.beginPath();
        ctx.moveTo(imgLeft, gy);
        ctx.lineTo(endX, gy);
        ctx.stroke();
      }
      // Emphasize configured offset origin.
      ctx.strokeStyle = "rgba(0,255,255,0.95)";
      ctx.beginPath();
      ctx.moveTo(startX, imgTop);
      ctx.lineTo(startX, endY);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(imgLeft, startY);
      ctx.lineTo(endX, startY);
      ctx.stroke();
      ctx.restore();
    }

    // Draw selection box on canvas (pixel-accurate)
    if (selection) {
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 2]);
      ctx.strokeRect(
        Math.round(panX + selection.x * zoom),
        Math.round(panY + selection.y * zoom),
        Math.round(selection.w * zoom),
        Math.round(selection.h * zoom)
      );
      ctx.setLineDash([]);
    }
  }

  function drawChecker(ctx, w, h) {
    var size = 8;
    ctx.fillStyle = "#1a1a2e";
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#16213e";
    for (var y = 0; y < h; y += size) {
      for (var x = 0; x < w; x += size) {
        if ((Math.floor(x / size) + Math.floor(y / size)) % 2 === 0) {
          ctx.fillRect(x, y, size, size);
        }
      }
    }
  }

  function clientToImage(clientX, clientY) {
    var rect = canvasWrapper.getBoundingClientRect();
    var canvasX = clientX - rect.left;
    var canvasY = clientY - rect.top;
    return {
      x: Math.floor((canvasX - panX) / zoom),
      y: Math.floor((canvasY - panY) / zoom)
    };
  }

  // Mouse handlers
  canvasWrapper.addEventListener("mousedown", function(e) {
    e.preventDefault();
    var imgCoord = clientToImage(e.clientX, e.clientY);

    if (mode === "select" && e.button === 0 && !e.shiftKey) {
      // Start selection
      isSelecting = true;
      dragStartX = imgCoord.x;
      dragStartY = imgCoord.y;
      selection = null;
      selBox.style.display = "none";
    } else {
      // Start panning
      isDragging = true;
      lastMouseX = e.clientX;
      lastMouseY = e.clientY;
      canvasWrapper.style.cursor = "grabbing";
    }
  });

  document.addEventListener("mousemove", function(e) {
    if (isSelecting) {
      var imgCoord = clientToImage(e.clientX, e.clientY);
      var x = Math.min(dragStartX, imgCoord.x);
      var y = Math.min(dragStartY, imgCoord.y);
      var w = Math.abs(imgCoord.x - dragStartX);
      var h = Math.abs(imgCoord.y - dragStartY);
      if (w > 0 && h > 0) {
        selection = { x: x, y: y, w: w, h: h };
        render();
      }
    } else if (isDragging) {
      var dx = e.clientX - lastMouseX;
      var dy = e.clientY - lastMouseY;
      panX += dx;
      panY += dy;
      lastMouseX = e.clientX;
      lastMouseY = e.clientY;
      render();
    }
  });

  document.addEventListener("mouseup", function() {
    if (isSelecting) {
      isSelecting = false;
      if (selection) {
        selLabel.textContent = selection.w + "\u00d7" + selection.h + "px @ (" + selection.x + "," + selection.y + ")";
        onSelectionChange(selection);
      }
    }
    if (isDragging) {
      isDragging = false;
      canvasWrapper.style.cursor = mode === "select" ? "crosshair" : "grab";
    }
  });

  canvasWrapper.addEventListener("wheel", function(e) {
    e.preventDefault();
    var delta = e.deltaY < 0 ? 1.25 : 0.8;
    setZoom(Math.round(zoom * delta * 4) / 4);
  }, { passive: false });

  // Zoom button handlers
  zoomOutBtn.addEventListener("click", function() { setZoom(Math.max(0.25, zoom / 2)); });
  zoomInBtn.addEventListener("click", function() { setZoom(Math.min(16, zoom * 2)); });
  resetBtn.addEventListener("click", function() {
    if (autoFitOnLoad && currentImage) {
      fitToViewport();
      render();
    } else {
      panX = 0; panY = 0;
      setZoom(initialZoom);
    }
    selection = null;
    selBox.style.display = "none";
    onSelectionChange(null);
  });

  // Set cursor based on mode
  if (mode === "select") {
    canvasWrapper.style.cursor = "crosshair";
  }

  // ===================================================================
  // Animation functions
  // ===================================================================

  var ANGLE_NAMES = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"];

  function clearAnimState() {
    stopPlayback();
    animSeq = null;
    animControlBar.style.display = "none";
    frameImageCache = {};
    animAngle = 0;
    animAnim = 0;
    animFrameIdx = 0;
  }

  function loadFrameSequenceImpl(apiData) {
    // Guard: frame_sequence.js must be loaded
    if (typeof createFrame !== "function" || typeof createFrameSequence !== "function") {
      // Fallback: show first frame as static image if available
      if (apiData.frames && apiData.frames.length > 0 && apiData.frames[0].data) {
        var img = new Image();
        img.onload = function() { currentImage = img; render(); };
        img.src = "data:image/png;base64," + apiData.frames[0].data;
      }
      return;
    }

    clearAnimState();

    // Convert API response to FrameSequence
    var frameObjects = (apiData.frames || []).map(function(fd) {
      return createFrame({
        data: fd.data,
        width: fd.width,
        height: fd.height,
        angle_idx: fd.angle_idx || 0,
        anim_idx: fd.anim_idx || 0,
        frame_idx: fd.frame_idx || 0
      });
    });

    animSeq = createFrameSequence({
      frames: frameObjects,
      angles: apiData.angles || 1,
      anims: apiData.anims || [1],
      projs: apiData.projs || 1,
      fps: apiData.fps || 8
    });

    animFps = animSeq.fps;
    animFpsSlider.value = String(animFps);
    animFpsLabel.textContent = animFps + " fps";

    // Build angle selector
    animAngleSelect.innerHTML = "";
    if (animSeq.angles > 1) {
      for (var a = 0; a < animSeq.angles; a++) {
        var opt = document.createElement("option");
        opt.value = String(a);
        opt.textContent = (animSeq.angles <= 8 && a < ANGLE_NAMES.length)
          ? ANGLE_NAMES[a] : "Angle " + a;
        animAngleSelect.appendChild(opt);
      }
      animAngleLabel.style.display = "";
      animAngleSelect.style.display = "";
    } else {
      animAngleLabel.style.display = "none";
      animAngleSelect.style.display = "none";
    }

    // Build anim selector
    animAnimSelect.innerHTML = "";
    if (animSeq.anims.length > 1) {
      for (var i = 0; i < animSeq.anims.length; i++) {
        var optA = document.createElement("option");
        optA.value = String(i);
        optA.textContent = "Anim " + i + " (" + animSeq.anims[i] + "f)";
        animAnimSelect.appendChild(optA);
      }
      animAnimLabel.style.display = "";
      animAnimSelect.style.display = "";
    } else {
      animAnimLabel.style.display = "none";
      animAnimSelect.style.display = "none";
    }

    // Show animation controls
    animControlBar.style.display = "flex";

    // Reset pan and selection, load first frame
    panX = 0;
    panY = 0;
    selection = null;
    animNeedsInitialFit = autoFitOnLoad;
    showAnimFrame();
  }

  function showAnimFrame() {
    if (!animSeq) return;
    var frame = animSeq.getFrame(animAngle, animAnim, animFrameIdx);
    if (!frame) return;
    loadFrameImage(frame).then(function(img) {
      currentImage = img;
      if (animNeedsInitialFit) {
        fitToViewport();
        animNeedsInitialFit = false;
      }
      render();
      updateAnimUI();
    });
  }

  function loadFrameImage(frame) {
    var key = frame.angle_idx + "-" + frame.anim_idx + "-" + frame.frame_idx;
    if (frameImageCache[key]) {
      return Promise.resolve(frameImageCache[key]);
    }
    return new Promise(function(resolve, reject) {
      var img = new Image();
      img.onload = function() {
        frameImageCache[key] = img;
        resolve(img);
      };
      img.onerror = function() { reject(new Error("Failed to load frame")); };
      img.src = "data:image/png;base64," + frame.data;
    });
  }

  function updateAnimUI() {
    if (!animSeq) return;
    var maxFrame = animSeq.anims[animAnim] || 1;
    animFrameLabel.textContent = "Frame " + (animFrameIdx + 1) + "/" + maxFrame;
    animPlayBtn.textContent = animPlaying ? "Pause" : "Play";
  }

  function startPlayback() {
    if (animPlaying || !animSeq) return;
    animPlaying = true;
    animLastTime = performance.now();
    animAccum = 0;
    animRafId = requestAnimationFrame(animTick);
    updateAnimUI();
  }

  function stopPlayback() {
    animPlaying = false;
    if (animRafId) {
      cancelAnimationFrame(animRafId);
      animRafId = null;
    }
  }

  function animTick(timestamp) {
    if (!animPlaying || !animSeq) return;
    var delta = timestamp - animLastTime;
    animLastTime = timestamp;
    animAccum += delta;

    var interval = 1000 / animFps;
    var advanced = false;

    while (animAccum >= interval) {
      var maxFrame = (animSeq.anims[animAnim] || 1) - 1;
      if (animFrameIdx < maxFrame) {
        animFrameIdx++;
        advanced = true;
      } else if (animLoop) {
        animFrameIdx = 0;
        advanced = true;
      } else {
        stopPlayback();
        updateAnimUI();
        return;
      }
      animAccum -= interval;
    }

    if (advanced) {
      showAnimFrame();
    }

    animRafId = requestAnimationFrame(animTick);
  }

  // Wire animation control handlers
  animPlayBtn.addEventListener("click", function() {
    if (animPlaying) { stopPlayback(); } else { startPlayback(); }
    updateAnimUI();
  });

  animPrevBtn.addEventListener("click", function() {
    if (!animSeq) return;
    stopPlayback();
    var maxFrame = (animSeq.anims[animAnim] || 1) - 1;
    animFrameIdx = animFrameIdx > 0 ? animFrameIdx - 1 : (animLoop ? maxFrame : 0);
    showAnimFrame();
  });

  animNextBtn.addEventListener("click", function() {
    if (!animSeq) return;
    stopPlayback();
    var maxFrame = (animSeq.anims[animAnim] || 1) - 1;
    animFrameIdx = animFrameIdx < maxFrame ? animFrameIdx + 1 : (animLoop ? 0 : maxFrame);
    showAnimFrame();
  });

  animAngleSelect.addEventListener("change", function() {
    animAngle = parseInt(this.value, 10);
    animFrameIdx = 0;
    showAnimFrame();
  });

  animAnimSelect.addEventListener("change", function() {
    animAnim = parseInt(this.value, 10);
    animFrameIdx = 0;
    showAnimFrame();
  });

  animFpsSlider.addEventListener("input", function() {
    animFps = parseInt(this.value, 10) || 8;
    animFpsLabel.textContent = animFps + " fps";
  });

  // ===================================================================
  // Public API
  // ===================================================================
  return {
    loadImage: function(url) {
      clearAnimState();
      var img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = function() {
        currentImage = img;
        if (autoFitOnLoad) {
          fitToViewport();
        } else {
          panX = 0;
          panY = 0;
        }
        selection = null;
        render();
      };
      img.onerror = function() {
        currentImage = null;
        render();
      };
      img.src = url;
    },

    loadFrameSequence: function(apiData) {
      loadFrameSequenceImpl(apiData);
    },

    loadXPFromPath: function(path) {
      var apiBase = window.ASSET_API_BASE || "/api";
      fetch(apiBase + "/viewer/load-xp-path", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path, scale: 1 })
      })
        .then(function(r) {
          if (!r.ok) throw new Error("XP load failed: " + r.status);
          return r.json();
        })
        .then(function(data) {
          loadFrameSequenceImpl(data);
        })
        .catch(function(err) {
          console.error("Viewer XP load failed", err);
          clearAnimState();
          currentImage = null;
          render();
        });
    },

    getSelection: function() {
      return selection ? {
        x: selection.x,
        y: selection.y,
        w: selection.w,
        h: selection.h
      } : null;
    },

    setMode: function(newMode) {
      mode = newMode;
      canvasWrapper.style.cursor = mode === "select" ? "crosshair" : "grab";
    },

    setGridOverlay: function(spec) {
      if (!spec || !spec.enabled) {
        gridOverlay.enabled = false;
        render();
        return;
      }
      gridOverlay.enabled = true;
      gridOverlay.cellW = Math.max(1, parseInt(spec.cell_w_px, 10) || 0);
      gridOverlay.cellH = Math.max(1, parseInt(spec.cell_h_px, 10) || 0);
      gridOverlay.offsetX = parseInt(spec.offset_x_px, 10) || 0;
      gridOverlay.offsetY = parseInt(spec.offset_y_px, 10) || 0;
      render();
    },

    destroy: function() {
      stopPlayback();
      window.removeEventListener("resize", onWindowResize);
      if (wrapper.parentNode) {
        wrapper.parentNode.removeChild(wrapper);
      }
      currentImage = null;
      animSeq = null;
      frameImageCache = {};
    }
  };
}
