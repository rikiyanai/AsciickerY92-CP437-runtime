/**
 * export_step.js -- Export controls for the Result step of the web wizard.
 *
 * Renders format radio buttons (PNG/ZIP/GIF), FPS slider, loop toggle,
 * and frame selection controls. Produces export configs compatible with
 * the GET /api/export/<job_id> endpoint.
 *
 * Depends on layout_editor.js for createExportConfig/updateExportConfig/
 * validateExportConfig/buildExportUrl.
 *
 * Tags: [FLOW:WEB-UI] [DATA-CONTRACT:EXPORT]
 */

"use strict";

// ============================================================================
// Export Step Renderer
// ============================================================================

/**
 * Render the export controls panel into the specified container.
 *
 * @param {Object} exportConfig - Current export configuration.
 * @param {Object} callbacks - { onChange: function(patch) }
 * @param {HTMLElement} container - DOM element to render into.
 * @returns {void}
 */
function renderExportControls(exportConfig, callbacks, container) {
  if (!container) { return; }

  container.innerHTML = "";

  // Format selection
  var formatGroup = _createFieldset("Export Format");
  var formats = [
    { value: "png", label: "PNG (single sheet)" },
    { value: "zip", label: "ZIP (individual frames)" },
    { value: "gif", label: "GIF (animated)" },
  ];

  formats.forEach(function (fmt) {
    var row = document.createElement("div");
    row.className = "export-radio-row";

    var radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "export-format";
    radio.value = fmt.value;
    radio.id = "export-fmt-" + fmt.value;
    radio.checked = exportConfig.format === fmt.value;
    radio.className = "export-radio";

    radio.addEventListener("change", function () {
      if (callbacks && callbacks.onChange) {
        callbacks.onChange({ format: fmt.value });
      }
    });

    var label = document.createElement("label");
    label.htmlFor = "export-fmt-" + fmt.value;
    label.className = "export-radio-label";
    label.textContent = fmt.label;

    row.appendChild(radio);
    row.appendChild(label);
    formatGroup.appendChild(row);
  });

  container.appendChild(formatGroup);

  // GIF-specific controls (only visible when GIF selected)
  var gifGroup = _createFieldset("Animation Settings");
  gifGroup.className = "export-gif-controls";
  if (exportConfig.format !== "gif") {
    gifGroup.style.display = "none";
  }

  // FPS slider
  var fpsRow = document.createElement("div");
  fpsRow.className = "form-row export-fps-row";

  var fpsLabel = document.createElement("label");
  fpsLabel.htmlFor = "export-fps";
  fpsLabel.textContent = "FPS: " + exportConfig.fps;
  fpsLabel.id = "export-fps-label";

  var fpsSlider = document.createElement("input");
  fpsSlider.type = "range";
  fpsSlider.id = "export-fps";
  fpsSlider.min = "1";
  fpsSlider.max = "60";
  fpsSlider.value = String(exportConfig.fps);
  fpsSlider.className = "export-slider";

  fpsSlider.addEventListener("input", function () {
    var newFps = parseInt(fpsSlider.value, 10);
    fpsLabel.textContent = "FPS: " + newFps;
    if (callbacks && callbacks.onChange) {
      callbacks.onChange({ fps: newFps });
    }
  });

  fpsRow.appendChild(fpsLabel);
  fpsRow.appendChild(fpsSlider);
  gifGroup.appendChild(fpsRow);

  // Loop toggle
  var loopRow = document.createElement("div");
  loopRow.className = "form-row export-loop-row";

  var loopLabel = document.createElement("label");
  loopLabel.htmlFor = "export-loop";
  loopLabel.textContent = "Loop animation";

  var loopCheck = document.createElement("input");
  loopCheck.type = "checkbox";
  loopCheck.id = "export-loop";
  loopCheck.checked = exportConfig.loop;
  loopCheck.className = "export-checkbox";

  loopCheck.addEventListener("change", function () {
    if (callbacks && callbacks.onChange) {
      callbacks.onChange({ loop: loopCheck.checked });
    }
  });

  loopRow.appendChild(loopLabel);
  loopRow.appendChild(loopCheck);
  gifGroup.appendChild(loopRow);

  // Animation index
  var animRow = document.createElement("div");
  animRow.className = "form-row export-anim-row";

  var animLabel = document.createElement("label");
  animLabel.htmlFor = "export-anim";
  animLabel.textContent = "Animation";

  var animInput = document.createElement("input");
  animInput.type = "number";
  animInput.id = "export-anim";
  animInput.min = "0";
  animInput.value = String(exportConfig.anim);
  animInput.className = "export-number";

  animInput.addEventListener("change", function () {
    var val = parseInt(animInput.value, 10) || 0;
    if (callbacks && callbacks.onChange) {
      callbacks.onChange({ anim: val });
    }
  });

  animRow.appendChild(animLabel);
  animRow.appendChild(animInput);
  gifGroup.appendChild(animRow);

  // Angle index
  var angleRow = document.createElement("div");
  angleRow.className = "form-row export-angle-row";

  var angleLabel = document.createElement("label");
  angleLabel.htmlFor = "export-angle";
  angleLabel.textContent = "Angle";

  var angleInput = document.createElement("input");
  angleInput.type = "number";
  angleInput.id = "export-angle";
  angleInput.min = "0";
  angleInput.value = String(exportConfig.angle);
  angleInput.className = "export-number";

  angleInput.addEventListener("change", function () {
    var val = parseInt(angleInput.value, 10) || 0;
    if (callbacks && callbacks.onChange) {
      callbacks.onChange({ angle: val });
    }
  });

  angleRow.appendChild(angleLabel);
  angleRow.appendChild(angleInput);
  gifGroup.appendChild(angleRow);

  container.appendChild(gifGroup);

  // Frame selection
  var selectionGroup = _createFieldset("Frame Selection");
  var selections = [
    { value: "all", label: "All frames" },
    { value: "angle_range", label: "Angle range" },
    { value: "custom", label: "Custom selection" },
  ];

  selections.forEach(function (sel) {
    var row = document.createElement("div");
    row.className = "export-radio-row";

    var radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "export-selection";
    radio.value = sel.value;
    radio.id = "export-sel-" + sel.value;
    radio.checked = exportConfig.selection === sel.value;
    radio.className = "export-radio";

    radio.addEventListener("change", function () {
      if (callbacks && callbacks.onChange) {
        callbacks.onChange({ selection: sel.value });
      }
    });

    var label = document.createElement("label");
    label.htmlFor = "export-sel-" + sel.value;
    label.className = "export-radio-label";
    label.textContent = sel.label;

    row.appendChild(radio);
    row.appendChild(label);
    selectionGroup.appendChild(row);
  });

  container.appendChild(selectionGroup);

  // Scale control
  var scaleGroup = _createFieldset("Scale");
  var scaleRow = document.createElement("div");
  scaleRow.className = "form-row export-scale-row";

  var scaleLabel = document.createElement("label");
  scaleLabel.htmlFor = "export-scale";
  scaleLabel.textContent = "Scale factor";

  var scaleInput = document.createElement("input");
  scaleInput.type = "number";
  scaleInput.id = "export-scale";
  scaleInput.min = "1";
  scaleInput.max = "8";
  scaleInput.value = String(exportConfig.scale);
  scaleInput.className = "export-number";

  scaleInput.addEventListener("change", function () {
    var val = parseInt(scaleInput.value, 10) || 1;
    if (val < 1) { val = 1; }
    if (val > 8) { val = 8; }
    if (callbacks && callbacks.onChange) {
      callbacks.onChange({ scale: val });
    }
  });

  scaleRow.appendChild(scaleLabel);
  scaleRow.appendChild(scaleInput);
  scaleGroup.appendChild(scaleRow);

  container.appendChild(scaleGroup);
}

/**
 * Create a fieldset element with a legend.
 *
 * @param {string} legendText - Text for the legend element.
 * @returns {HTMLElement} Fieldset element.
 */
function _createFieldset(legendText) {
  var fieldset = document.createElement("fieldset");
  fieldset.className = "export-fieldset";
  var legend = document.createElement("legend");
  legend.textContent = legendText;
  fieldset.appendChild(legend);
  return fieldset;
}

// ============================================================================
// Exports for testing (Node.js)
// ============================================================================

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    renderExportControls: renderExportControls,
  };
}
