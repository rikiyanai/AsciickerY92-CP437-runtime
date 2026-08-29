/**
 * layout_editor.js -- Frame/angle/animation layout manipulation for the web UI.
 *
 * Provides drag/drop reorder, delete/restore, undo/reset, and regression
 * snapshot capture. Integrates with app.js via updateState() calls.
 *
 * All state mutations return new objects (immutable pattern matching app.js).
 * The undo stack records operations for reproducibility and backend alignment.
 *
 * Tags: [FLOW:WEB-UI] [DATA-CONTRACT:JOB-CONFIG]
 */

"use strict";

// ============================================================================
// Constants
// ============================================================================

var MAX_UNDO_STACK = 20;
var DRAG_CLASSNAME = "layout-dragging";
var DROP_TARGET_CLASSNAME = "layout-drop-target";

// ============================================================================
// Layout State
// ============================================================================

/**
 * Create a fresh layout editor state.
 *
 * @param {Array} items - Initial array of layout items (frames/angles/anims).
 * @returns {Object} Frozen layout state.
 */
function createLayoutState(items) {
  var frozen = Object.freeze(items.map(function (item, idx) {
    return Object.freeze({
      id: item.id || "item-" + idx,
      label: item.label || "Frame " + idx,
      type: item.type || "frame",
      originalIndex: idx,
      deleted: false,
      data: Object.freeze(item.data || {}),
    });
  }));

  return Object.freeze({
    items: frozen,
    undoStack: Object.freeze([]),
    initialItems: frozen,
    snapshotLog: Object.freeze([]),
  });
}

/**
 * Get only the active (non-deleted) items from layout state.
 *
 * @param {Object} layoutState - Current layout state.
 * @returns {Array} Active items in current order.
 */
function getActiveItems(layoutState) {
  return layoutState.items.filter(function (item) {
    return !item.deleted;
  });
}

/**
 * Get deleted items for the restore list.
 *
 * @param {Object} layoutState - Current layout state.
 * @returns {Array} Deleted items.
 */
function getDeletedItems(layoutState) {
  return layoutState.items.filter(function (item) {
    return item.deleted;
  });
}

// ============================================================================
// Operations (all return new state -- immutable)
// ============================================================================

/**
 * Push the current items onto the undo stack before a mutation.
 *
 * @param {Object} layoutState - Current layout state.
 * @param {string} operationName - Name of the operation being performed.
 * @returns {Object} New state with updated undo stack.
 */
function _pushUndo(layoutState, operationName) {
  var newEntry = Object.freeze({
    items: layoutState.items,
    operation: operationName,
    timestamp: Date.now(),
  });

  var stack = layoutState.undoStack.slice();
  stack.push(newEntry);

  // Enforce max undo depth
  if (stack.length > MAX_UNDO_STACK) {
    stack = stack.slice(stack.length - MAX_UNDO_STACK);
  }

  return Object.freeze(Object.assign({}, layoutState, {
    undoStack: Object.freeze(stack),
  }));
}

/**
 * Reorder items by moving one item to a new position.
 *
 * @param {Object} layoutState - Current layout state.
 * @param {number} fromIndex - Source index in items array.
 * @param {number} toIndex - Destination index in items array.
 * @returns {Object} New layout state with reordered items.
 */
function reorderItem(layoutState, fromIndex, toIndex) {
  if (fromIndex === toIndex) {
    return layoutState;
  }
  if (fromIndex < 0 || fromIndex >= layoutState.items.length) {
    return layoutState;
  }
  if (toIndex < 0 || toIndex >= layoutState.items.length) {
    return layoutState;
  }

  var withUndo = _pushUndo(layoutState, "reorder");
  var newItems = layoutState.items.slice();
  var moved = newItems.splice(fromIndex, 1)[0];
  newItems.splice(toIndex, 0, moved);

  return Object.freeze(Object.assign({}, withUndo, {
    items: Object.freeze(newItems),
  }));
}

/**
 * Mark an item as deleted (soft delete -- preserves data).
 *
 * @param {Object} layoutState - Current layout state.
 * @param {string} itemId - ID of the item to delete.
 * @returns {Object} New layout state with item marked deleted.
 */
function deleteItem(layoutState, itemId) {
  var found = false;
  var newItems = layoutState.items.map(function (item) {
    if (item.id === itemId && !item.deleted) {
      found = true;
      return Object.freeze(Object.assign({}, item, { deleted: true }));
    }
    return item;
  });

  if (!found) {
    return layoutState;
  }

  var withUndo = _pushUndo(layoutState, "delete:" + itemId);
  return Object.freeze(Object.assign({}, withUndo, {
    items: Object.freeze(newItems),
  }));
}

/**
 * Restore a previously deleted item.
 *
 * @param {Object} layoutState - Current layout state.
 * @param {string} itemId - ID of the item to restore.
 * @returns {Object} New layout state with item restored.
 */
function restoreItem(layoutState, itemId) {
  var found = false;
  var newItems = layoutState.items.map(function (item) {
    if (item.id === itemId && item.deleted) {
      found = true;
      return Object.freeze(Object.assign({}, item, { deleted: false }));
    }
    return item;
  });

  if (!found) {
    return layoutState;
  }

  var withUndo = _pushUndo(layoutState, "restore:" + itemId);
  return Object.freeze(Object.assign({}, withUndo, {
    items: Object.freeze(newItems),
  }));
}

/**
 * Undo the most recent operation.
 *
 * @param {Object} layoutState - Current layout state.
 * @returns {Object} Previous layout state, or unchanged if nothing to undo.
 */
function undoOperation(layoutState) {
  if (layoutState.undoStack.length === 0) {
    return layoutState;
  }

  var stack = layoutState.undoStack.slice();
  var previous = stack.pop();

  return Object.freeze(Object.assign({}, layoutState, {
    items: previous.items,
    undoStack: Object.freeze(stack),
  }));
}

/**
 * Reset layout to its initial state, clearing undo history.
 *
 * @param {Object} layoutState - Current layout state.
 * @returns {Object} Fresh layout state with initial items.
 */
function resetLayout(layoutState) {
  return Object.freeze(Object.assign({}, layoutState, {
    items: layoutState.initialItems,
    undoStack: Object.freeze([]),
  }));
}

// ============================================================================
// Regression Snapshots
// ============================================================================

/**
 * Capture a snapshot of the current layout for regression testing.
 *
 * @param {Object} layoutState - Current layout state.
 * @param {string} operationName - What triggered this snapshot.
 * @returns {Object} New state with snapshot appended to log.
 */
function captureSnapshot(layoutState, operationName) {
  var snapshot = Object.freeze({
    operation: operationName,
    timestamp: Date.now(),
    itemCount: layoutState.items.length,
    activeCount: getActiveItems(layoutState).length,
    deletedCount: getDeletedItems(layoutState).length,
    itemOrder: Object.freeze(layoutState.items.map(function (item) {
      return item.id;
    })),
    deletedIds: Object.freeze(getDeletedItems(layoutState).map(function (item) {
      return item.id;
    })),
    undoDepth: layoutState.undoStack.length,
  });

  var log = layoutState.snapshotLog.slice();
  log.push(snapshot);

  return Object.freeze(Object.assign({}, layoutState, {
    snapshotLog: Object.freeze(log),
  }));
}

// ============================================================================
// Serialization (for backend sync)
// ============================================================================

/**
 * Serialize layout state to a config patch for updateState().
 * This produces the frame ordering that the backend uses for assembly.
 *
 * @param {Object} layoutState - Current layout state.
 * @returns {Object} Patch object compatible with app.js updateState().
 */
function toLayoutConfig(layoutState) {
  var active = getActiveItems(layoutState);
  var frameOrder = active.map(function (item) {
    return item.originalIndex;
  });
  var deletedIds = getDeletedItems(layoutState).map(function (item) {
    return item.id;
  });

  return {
    layoutFrameOrder: Object.freeze(frameOrder),
    layoutDeletedIds: Object.freeze(deletedIds),
    layoutUndoDepth: layoutState.undoStack.length,
    layoutSnapshotCount: layoutState.snapshotLog.length,
  };
}

// ============================================================================
// DOM Rendering
// ============================================================================

/**
 * Render the layout editor into the specified container.
 *
 * @param {Object} layoutState - Current layout state.
 * @param {HTMLElement} container - DOM element to render into.
 * @param {Object} callbacks - Event handler callbacks.
 * @returns {void}
 */
function renderLayoutEditor(layoutState, container, callbacks) {
  if (!container) { return; }

  container.innerHTML = "";

  // Active items list
  var activeSection = document.createElement("div");
  activeSection.className = "layout-active-items";

  var activeItems = getActiveItems(layoutState);
  activeItems.forEach(function (item, displayIdx) {
    var el = _createItemElement(item, displayIdx, callbacks);
    activeSection.appendChild(el);
  });

  container.appendChild(activeSection);

  // Deleted items section (if any)
  var deletedItems = getDeletedItems(layoutState);
  if (deletedItems.length > 0) {
    var deletedSection = document.createElement("div");
    deletedSection.className = "layout-deleted-items";

    var deletedLabel = document.createElement("h4");
    deletedLabel.textContent = "Deleted (" + deletedItems.length + ")";
    deletedLabel.className = "layout-deleted-header";
    deletedSection.appendChild(deletedLabel);

    deletedItems.forEach(function (item) {
      var el = _createDeletedItemElement(item, callbacks);
      deletedSection.appendChild(el);
    });

    container.appendChild(deletedSection);
  }

  // Controls bar
  var controls = document.createElement("div");
  controls.className = "layout-controls";

  var undoBtn = document.createElement("button");
  undoBtn.className = "btn btn-secondary layout-btn-undo";
  undoBtn.textContent = "Undo";
  undoBtn.disabled = layoutState.undoStack.length === 0;
  undoBtn.addEventListener("click", function () {
    if (callbacks && callbacks.onUndo) { callbacks.onUndo(); }
  });
  controls.appendChild(undoBtn);

  var resetBtn = document.createElement("button");
  resetBtn.className = "btn btn-secondary layout-btn-reset";
  resetBtn.textContent = "Reset";
  resetBtn.addEventListener("click", function () {
    if (callbacks && callbacks.onReset) { callbacks.onReset(); }
  });
  controls.appendChild(resetBtn);

  container.appendChild(controls);
}

/**
 * Create a DOM element for an active layout item.
 *
 * @param {Object} item - Layout item.
 * @param {number} displayIdx - Position in active items list.
 * @param {Object} callbacks - Event handler callbacks.
 * @returns {HTMLElement}
 */
function _createItemElement(item, displayIdx, callbacks) {
  var el = document.createElement("div");
  el.className = "layout-item";
  el.setAttribute("draggable", "true");
  el.setAttribute("data-item-id", item.id);
  el.setAttribute("data-item-index", String(displayIdx));

  var label = document.createElement("span");
  label.className = "layout-item-label";
  label.textContent = item.label;
  el.appendChild(label);

  var typeTag = document.createElement("span");
  typeTag.className = "layout-item-type";
  typeTag.textContent = item.type;
  el.appendChild(typeTag);

  var deleteBtn = document.createElement("button");
  deleteBtn.className = "btn btn-secondary layout-item-delete";
  deleteBtn.textContent = "Delete";
  deleteBtn.setAttribute("aria-label", "Delete " + item.label);
  deleteBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    if (callbacks && callbacks.onDelete) { callbacks.onDelete(item.id); }
  });
  el.appendChild(deleteBtn);

  // HTML5 Drag and Drop handlers
  el.addEventListener("dragstart", function (e) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", item.id);
    el.classList.add(DRAG_CLASSNAME);
  });

  el.addEventListener("dragend", function () {
    el.classList.remove(DRAG_CLASSNAME);
    _clearDropTargets(el.parentNode);
  });

  el.addEventListener("dragover", function (e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    el.classList.add(DROP_TARGET_CLASSNAME);
  });

  el.addEventListener("dragleave", function () {
    el.classList.remove(DROP_TARGET_CLASSNAME);
  });

  el.addEventListener("drop", function (e) {
    e.preventDefault();
    el.classList.remove(DROP_TARGET_CLASSNAME);

    var draggedId = e.dataTransfer.getData("text/plain");
    var targetId = item.id;

    if (draggedId !== targetId && callbacks && callbacks.onReorder) {
      callbacks.onReorder(draggedId, targetId);
    }
  });

  return el;
}

/**
 * Create a DOM element for a deleted layout item with restore button.
 *
 * @param {Object} item - Deleted layout item.
 * @param {Object} callbacks - Event handler callbacks.
 * @returns {HTMLElement}
 */
function _createDeletedItemElement(item, callbacks) {
  var el = document.createElement("div");
  el.className = "layout-item layout-item-deleted";
  el.setAttribute("data-item-id", item.id);

  var label = document.createElement("span");
  label.className = "layout-item-label";
  label.textContent = item.label;
  el.appendChild(label);

  var restoreBtn = document.createElement("button");
  restoreBtn.className = "btn btn-secondary layout-item-restore";
  restoreBtn.textContent = "Restore";
  restoreBtn.setAttribute("aria-label", "Restore " + item.label);
  restoreBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    if (callbacks && callbacks.onRestore) { callbacks.onRestore(item.id); }
  });
  el.appendChild(restoreBtn);

  return el;
}

/**
 * Clear all drop target highlights within a container.
 *
 * @param {HTMLElement} container
 */
function _clearDropTargets(container) {
  if (!container) { return; }
  var targets = container.querySelectorAll("." + DROP_TARGET_CLASSNAME);
  targets.forEach(function (el) {
    el.classList.remove(DROP_TARGET_CLASSNAME);
  });
}

// ============================================================================
// Index Resolution
// ============================================================================

/**
 * Find the index of an item by its ID in the items array.
 *
 * @param {Object} layoutState - Current layout state.
 * @param {string} itemId - Item ID to find.
 * @returns {number} Index in items array, or -1 if not found.
 */
function findItemIndex(layoutState, itemId) {
  for (var i = 0; i < layoutState.items.length; i++) {
    if (layoutState.items[i].id === itemId) {
      return i;
    }
  }
  return -1;
}

// ============================================================================
// Export Configuration
// ============================================================================

/**
 * Default export configuration matching the backend export API parameters.
 *
 * These fields correspond to query params on GET /api/export/<job_id>:
 *   format: "png" | "zip" | "gif"
 *   fps: 1-60 (GIF only)
 *   loop: true/false (GIF only)
 *   anim: animation index (GIF only)
 *   angle: angle index (GIF only)
 *   scale: render scale factor
 *
 * @returns {Object} Frozen default export config.
 */
function createExportConfig() {
  return Object.freeze({
    format: "png",
    fps: 8,
    loop: true,
    anim: 0,
    angle: 0,
    scale: 1,
    selection: "all",
  });
}

/**
 * Update export config immutably.
 *
 * @param {Object} config - Current export config.
 * @param {Object} patch - Fields to update.
 * @returns {Object} New frozen export config.
 */
function updateExportConfig(config, patch) {
  return Object.freeze(Object.assign({}, config, patch));
}

/**
 * Validate export config values.
 *
 * @param {Object} config - Export config to validate.
 * @returns {Object} { valid: boolean, errors: string[] }
 */
function validateExportConfig(config) {
  var errors = [];

  var validFormats = ["png", "zip", "gif"];
  if (validFormats.indexOf(config.format) < 0) {
    errors.push("Invalid format: " + config.format + ". Must be png, zip, or gif.");
  }

  if (typeof config.fps !== "number" || config.fps < 1 || config.fps > 60) {
    errors.push("FPS must be between 1 and 60.");
  }

  if (typeof config.loop !== "boolean") {
    errors.push("Loop must be a boolean.");
  }

  if (typeof config.anim !== "number" || config.anim < 0) {
    errors.push("Animation index must be a non-negative integer.");
  }

  if (typeof config.angle !== "number" || config.angle < 0) {
    errors.push("Angle index must be a non-negative integer.");
  }

  if (typeof config.scale !== "number" || config.scale < 1) {
    errors.push("Scale must be a positive integer.");
  }

  var validSelections = ["all", "angle_range", "custom"];
  if (validSelections.indexOf(config.selection) < 0) {
    errors.push("Invalid selection: " + config.selection);
  }

  return Object.freeze({
    valid: errors.length === 0,
    errors: Object.freeze(errors),
  });
}

/**
 * Build the export URL query string from config.
 *
 * @param {string} jobId - Job identifier.
 * @param {Object} exportConfig - Export configuration.
 * @param {string} apiBase - API base URL.
 * @returns {string} Full export URL.
 */
function buildExportUrl(jobId, exportConfig, apiBase) {
  var base = (apiBase || "/api") + "/export/" + jobId;
  var params = ["format=" + encodeURIComponent(exportConfig.format)];

  if (exportConfig.format === "gif") {
    params.push("fps=" + exportConfig.fps);
    params.push("anim=" + exportConfig.anim);
    params.push("angle=" + exportConfig.angle);
  }

  if (exportConfig.scale > 1) {
    params.push("scale=" + exportConfig.scale);
  }

  return base + "?" + params.join("&");
}

/**
 * Get the list of supported export formats.
 *
 * @returns {string[]} Supported format names.
 */
function getExportFormats() {
  return ["png", "zip", "gif"];
}

/**
 * Get the list of export config fields.
 * Used by parity tests to verify alignment with backend API.
 *
 * @returns {string[]} Sorted field names.
 */
function getExportConfigFields() {
  return ["angle", "anim", "format", "fps", "loop", "scale", "selection"];
}

// ============================================================================
// Exports for testing (Node.js / contract parity)
// ============================================================================

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    // Layout state
    createLayoutState: createLayoutState,
    getActiveItems: getActiveItems,
    getDeletedItems: getDeletedItems,

    // Operations
    reorderItem: reorderItem,
    deleteItem: deleteItem,
    restoreItem: restoreItem,
    undoOperation: undoOperation,
    resetLayout: resetLayout,

    // Snapshots
    captureSnapshot: captureSnapshot,

    // Serialization
    toLayoutConfig: toLayoutConfig,
    findItemIndex: findItemIndex,

    // DOM rendering
    renderLayoutEditor: renderLayoutEditor,

    // Export config
    createExportConfig: createExportConfig,
    updateExportConfig: updateExportConfig,
    validateExportConfig: validateExportConfig,
    buildExportUrl: buildExportUrl,
    getExportFormats: getExportFormats,
    getExportConfigFields: getExportConfigFields,

    // Constants
    MAX_UNDO_STACK: MAX_UNDO_STACK,
  };
}
