/**
 * Expose game state as serializable JSON for debugging and Playwright inspection.
 *
 * Injects window.render_game_to_text() so browser-based tests or manual dev
 * tools can snapshot the full game state in a compact JSON payload.
 *
 * Origin: proposal BF-c0ca4ae715f6 from codex session rollout-2026-02-26T05-28-39
 * Generalization: parameterized render function to accept custom state reference
 *   (defaults to global `state`), added entity filtering, and added doc comment.
 */

(function () {
  const DEFAULT_STATE_VAR = typeof state !== 'undefined' ? state : null;

  window.render_game_to_text = function (stateRef) {
    const st = stateRef || DEFAULT_STATE_VAR;
    if (!st) {
      return JSON.stringify({ error: 'game state not found' });
    }
    const payload = {
      mode: st.mode,
      player: { x: st.player.x, y: st.player.y, r: st.player.r },
      entities: (st.entities || []).map(function (e) { return { x: e.x, y: e.y, r: e.r }; }),
      score: st.score,
    };
    return JSON.stringify(payload);
  };
})();
