/**
 * Advance game simulation time by a given number of milliseconds.
 *
 * Injects window.advanceTime(ms) for use in Playwright tests or manual dev
 * tools to fast-forward the game loop without waiting in real time.
 *
 * Origin: proposal BF-4f5623f1ae46 from codex session rollout-2026-02-26T05-28-39
 * Generalization: added default tick rate constant, bounds check, and fallback
 *   if `update`/`render` globals are missing.
 */

(function () {
  const DEFAULT_FPS = 60;
  const TICK_MS = 1000 / DEFAULT_FPS;

  window.advanceTime = function (ms) {
    if (typeof update !== 'function' || typeof render !== 'function') {
      console.warn('advanceTime: update/render functions not found');
      return;
    }
    const steps = Math.max(1, Math.round(ms / TICK_MS));
    for (var i = 0; i < steps; i++) {
      update(1 / DEFAULT_FPS);
    }
    render();
  };
})();
