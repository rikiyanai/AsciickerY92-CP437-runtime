# Asciicker Player Guide

A browser-based multiplayer game rendered in CP437/ASCII style. Connect via the
game URL, enter your name, and click **Start**.

---

## Controls

### Movement

| Key | Action |
|-----|--------|
| W / Arrow Up | Move forward |
| S / Arrow Down | Move backward |
| A / Arrow Left | Strafe left |
| D / Arrow Right | Strafe right |
| Space | Jump |
| Q / F1 / Page Up / Delete | Rotate camera left |
| E / F2 / Page Down / Insert | Rotate camera right |

### Chat

| Key | Action |
|-----|--------|
| Tab | Open chat box |
| Tab (with text) | Send message |
| Escape | Close chat box without sending |
| Arrow keys | Move cursor in chat box |
| F5–F8 | Recall saved message slots |

### Inventory

| Key | Action |
|-----|--------|
| I or B | Toggle inventory panel |
| Escape | Close inventory |
| U | Use / equip focused item |

### View

| Key | Action |
|-----|--------|
| Ctrl + / Ctrl - | Zoom font in / out (browser-level cell sizing) |
| F | Toggle fly mode |

### Misc

| Key | Action |
|-----|--------|
| Escape | Cancel menus / contacts |
| F10 | Debug snapshot (dev) |

> **Gamepad:** Xbox-compatible controllers are supported. Buttons mirror the
> keyboard layout; the in-game gamepad config screen maps specific actions.

---

## Connection Troubleshooting

**Black screen after loading**
The WASM bundle is still downloading. Wait for the spinner to finish; on slow
connections this can take 10–30 s. Reload if it hangs past 60 s.

**"Connecting…" never resolves**
The WebSocket to the game server failed. Check that your network allows
WebSocket traffic (port 443/WSS). Corporate or school firewalls often block
non-HTTP protocols. Try a mobile hotspot to confirm.

**Kicked immediately after joining**
The server may be full or restarting. Wait 30 s and retry. If the issue
persists, the server may be offline — check the community Discord for status.

**Choppy movement / rubber-banding**
High latency to the server. The game interpolates remote players but your own
character is still affected above ~300 ms RTT. A wired connection or a region
closer to the server host will help.

**Controls not responding**
Click once inside the game canvas to give it keyboard focus. The browser
requires a user gesture to capture input; switching tabs and returning resets
focus.
