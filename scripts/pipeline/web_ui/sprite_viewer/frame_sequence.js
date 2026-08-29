/**
 * frame_sequence.js -- Unified animation model for the sprite viewer.
 *
 * FrameSequence is the canonical data model shared between PNG and XP loaders.
 * It holds an array of Frame objects with angle/anim/frame indices, plus
 * metadata about the sprite (angles, anims, projs, fps).
 *
 * Both loaders (loadPNG, loadXP) produce a FrameSequence as output.
 * Controls and compare mode consume FrameSequence for playback.
 *
 * IMPORTANT: Field names in this class MUST match the Python mirror class
 * in scripts/pipeline/web_ui/sprite_viewer/frame_sequence_mirror.py.
 * A structural drift detection test enforces this parity.
 *
 * Tags: [FLOW:VIEWER] [DATA-CONTRACT:FRAME-SEQUENCE]
 */

"use strict";

// ============================================================================
// Frame
// ============================================================================

/**
 * A single animation frame with pixel data and position indices.
 *
 * @param {Object} opts
 * @param {string} opts.data       - Base64-encoded PNG image data
 * @param {number} opts.width      - Frame width in pixels
 * @param {number} opts.height     - Frame height in pixels
 * @param {number} opts.angle_idx  - Angle index (0-based)
 * @param {number} opts.anim_idx   - Animation index (0-based)
 * @param {number} opts.frame_idx  - Frame index within animation (0-based)
 * @returns {Object} Frozen Frame object
 */
function createFrame(opts) {
  if (typeof opts.data !== "string") {
    throw new Error("Frame data must be a base64 string");
  }
  if (typeof opts.width !== "number" || opts.width <= 0) {
    throw new Error("Frame width must be a positive number");
  }
  if (typeof opts.height !== "number" || opts.height <= 0) {
    throw new Error("Frame height must be a positive number");
  }

  return Object.freeze({
    data: opts.data,
    width: opts.width,
    height: opts.height,
    angle_idx: opts.angle_idx || 0,
    anim_idx: opts.anim_idx || 0,
    frame_idx: opts.frame_idx || 0
  });
}


// ============================================================================
// FrameSequence
// ============================================================================

/**
 * Unified animation model holding all frames for a sprite.
 *
 * @param {Object} opts
 * @param {Array}   opts.frames          - Array of Frame objects
 * @param {number}  opts.angles          - Number of angle views
 * @param {Array}   opts.anims           - Array of frame counts per animation
 * @param {number}  opts.projs           - Number of projections (1 or 2)
 * @param {number}  opts.fps             - Playback FPS (default 8)
 * @param {Object}  opts.metadata        - Optional additional data
 * @param {boolean} opts.truncated       - Whether response was truncated
 * @param {number}  opts.total_frames    - Total frames available (before truncation)
 * @param {number}  opts.returned_frames - Frames actually returned
 * @returns {Object} Frozen FrameSequence object
 */
function createFrameSequence(opts) {
  var frames = opts.frames || [];
  var angles = opts.angles || 1;
  var anims = opts.anims || [1];
  var projs = opts.projs || 1;
  var fps = opts.fps || 8;
  var metadata = opts.metadata || null;
  var truncated = opts.truncated || false;
  var total_frames = (opts.total_frames != null) ? opts.total_frames : frames.length;
  var returned_frames = (opts.returned_frames != null) ? opts.returned_frames : frames.length;

  // Build lookup index: angle_idx -> anim_idx -> frame_idx -> Frame
  var _index = {};
  for (var i = 0; i < frames.length; i++) {
    var f = frames[i];
    var aKey = f.angle_idx;
    var animKey = f.anim_idx;
    var fKey = f.frame_idx;

    if (!_index[aKey]) { _index[aKey] = {}; }
    if (!_index[aKey][animKey]) { _index[aKey][animKey] = {}; }
    _index[aKey][animKey][fKey] = f;
  }

  return Object.freeze({
    frames: Object.freeze(frames),
    angles: angles,
    anims: Object.freeze(anims),
    projs: projs,
    fps: fps,
    metadata: metadata,
    truncated: truncated,
    total_frames: total_frames,
    returned_frames: returned_frames,

    /**
     * Get a specific frame by its indices.
     *
     * @param {number} angleIdx - Angle index
     * @param {number} animIdx  - Animation index
     * @param {number} frameIdx - Frame index within animation
     * @returns {Object|null} Frame object or null if not found
     */
    getFrame: function(angleIdx, animIdx, frameIdx) {
      if (_index[angleIdx] && _index[angleIdx][animIdx]) {
        return _index[angleIdx][animIdx][frameIdx] || null;
      }
      return null;
    },

    /**
     * Get all frames for a specific angle.
     *
     * @param {number} angleIdx - Angle index
     * @returns {Array} Array of Frame objects for this angle
     */
    getFramesForAngle: function(angleIdx) {
      var result = [];
      for (var i = 0; i < frames.length; i++) {
        if (frames[i].angle_idx === angleIdx) {
          result.push(frames[i]);
        }
      }
      return result;
    },

    /**
     * Get frames for a specific angle and animation.
     *
     * @param {number} angleIdx - Angle index
     * @param {number} animIdx  - Animation index
     * @returns {Array} Array of Frame objects for this angle+anim combo
     */
    getFramesForAnim: function(angleIdx, animIdx) {
      var result = [];
      for (var i = 0; i < frames.length; i++) {
        if (frames[i].angle_idx === angleIdx && frames[i].anim_idx === animIdx) {
          result.push(frames[i]);
        }
      }
      return result;
    },

    /**
     * Total number of frames in this sequence.
     *
     * @returns {number}
     */
    totalFrameCount: function() {
      return frames.length;
    }
  });
}
