#!/usr/bin/env python3
"""Record the native terminal client playing against a local server -> GIF.

Pipeline: start .run/server on a free port -> start .run/game_term in a
detached tmux pane of fixed size -> wait for the server to log the player's
spawn -> play a key timeline with `tmux send-keys` while grabbing
`tmux capture-pane -p -e` at --fps -> render frames with the CP437 atlas
(cp437_render.py) -> GIF.

Prerequisites: `make server`, `make -f makefile_game_term V8_FROM=system`
(or the vendored V8), tmux, `pip install pyte Pillow`. The client asserts on
PulseAudio at startup; if no PulseAudio server answers, this script starts a
user daemon with a null sink (`apt-get install pulseaudio`).

Timeline: JSON list of {"at": sec, "key": KEY, "hold": sec}. KEY is a literal
character (e.g. "w", "q") or a tmux key name (e.g. "Left", "PageUp", "DC").
The terminal has no key-up events, so the client releases a key 140 ms after
its last byte (engine/input_state.cpp); held keys are therefore re-sent every
--repeat-ms (default 40 ms) for the hold duration.

Client keybindings (engine/local_player_authority.cpp):
  walk:          w a s d  or arrow keys
  camera rotate: q / e  (also Delete|PageUp|F1 = left, Insert|PageDown|F2 = right)

Example:
  python3 scripts/terminal_capture/capture.py \
      --out docs/recordings/terminal/first-light.gif --duration 8
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import cp437_render  # noqa: E402

DEFAULT_TIMELINE = [
    {"at": 0.6, "key": "w", "hold": 1.5},   # walk forward
    {"at": 2.4, "key": "e", "hold": 1.0},   # rotate camera right (~120 deg)
    {"at": 3.7, "key": "w", "hold": 1.4},   # walk the new heading
    {"at": 5.4, "key": "q", "hold": 0.6},   # rotate back left
]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def ensure_pulse(log):
    if subprocess.run(["pactl", "info"], capture_output=True).returncode == 0:
        return
    if not shutil.which("pulseaudio"):
        log("WARN: no PulseAudio server and pulseaudio not installed; "
            "the client will abort in InitAudio()")
        return
    log("starting PulseAudio daemon with a null sink")
    subprocess.run(["pulseaudio", "-D", "--exit-idle-time=-1", "-n",
                    "--load=module-null-sink",
                    "--load=module-native-protocol-unix"],
                   capture_output=True)
    time.sleep(1.0)


def wait_for(path, needle, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    if needle in line:
                        return line.strip()
        except FileNotFoundError:
            pass
        time.sleep(0.2)
    return None


class Tmux:
    def __init__(self, sock, target="cap"):
        self.base = ["tmux", "-L", sock]
        self.target = target

    def run(self, *args, **kw):
        return subprocess.run(self.base + list(args), capture_output=True,
                              **kw)

    def send(self, key):
        if len(key) == 1:
            self.run("send-keys", "-t", self.target, "-l", key)
        else:
            self.run("send-keys", "-t", self.target, key)

    def capture(self):
        r = self.run("capture-pane", "-p", "-e", "-t", self.target)
        return r.stdout.decode("utf-8", errors="replace")


def wait_pane(tmux, needle, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = tmux.run("capture-pane", "-p", "-t", tmux.target)
        text = r.stdout.decode("utf-8", errors="replace")
        if needle in text:
            return True
        time.sleep(0.3)
    return False


def play_timeline(tmux, timeline, t0, repeat, stop):
    events = []
    for ev in timeline:
        hold = float(ev.get("hold", 0))
        n = max(1, int(hold / repeat)) if hold > 0 else 1
        for i in range(n):
            events.append((ev["at"] + i * repeat, ev["key"]))
    events.sort()
    for at, key in events:
        while not stop.is_set():
            dt = t0 + at - time.time()
            if dt <= 0:
                break
            time.sleep(min(dt, 0.01))
        if stop.is_set():
            return
        tmux.send(key)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default=os.path.join(
        REPO, "docs", "recordings", "terminal", "capture.gif"))
    ap.add_argument("--work", default=None,
                    help="dir for frames/logs (default: <out>.work beside "
                         "/tmp)")
    ap.add_argument("--cols", type=int, default=160)
    ap.add_argument("--rows", type=int, default=50)
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--duration", type=float, default=8.0,
                    help="seconds recorded after spawn")
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds to wait after spawn before recording")
    ap.add_argument("--timeline", default=None, help="JSON timeline file")
    ap.add_argument("--repeat-ms", type=float, default=40)
    ap.add_argument("--menu-keys", default="Enter,Enter",
                    help="comma-separated keys sent to leave the main menu "
                         "(NEW GAME, then CONTINUE); '' to skip")
    ap.add_argument("--ingame-marker", default="YOU:",
                    help="pane text that proves the world view is up")
    ap.add_argument("--cell", type=int, default=8)
    ap.add_argument("--user", default="player")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--server", default=os.path.join(REPO, ".run", "server"))
    ap.add_argument("--client", default=os.path.join(REPO, ".run",
                                                     "game_term"))
    ap.add_argument("--term", default="xterm-256color")
    ap.add_argument("--keep", action="store_true",
                    help="leave server/client running")
    a = ap.parse_args(argv)

    for b in (a.server, a.client):
        if not os.access(b, os.X_OK):
            sys.exit("missing %s (build it first)" % b)
    if not shutil.which("tmux"):
        sys.exit("tmux is required")

    timeline = DEFAULT_TIMELINE
    if a.timeline:
        with open(a.timeline) as f:
            timeline = json.load(f)

    work = a.work or os.path.join("/tmp", "terminal_capture_%d" % os.getpid())
    frames_dir = os.path.join(work, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    for n in os.listdir(frames_dir):
        os.unlink(os.path.join(frames_dir, n))

    def log(msg):
        print("[capture] " + msg, flush=True)

    ensure_pulse(log)
    port = a.port or free_port()
    srv_log = os.path.join(work, "server.log")
    cli_log = os.path.join(work, "client.stderr.log")
    log("server port %d, logs in %s" % (port, work))
    srv = subprocess.Popen([a.server, "--port", str(port)], cwd=REPO,
                           stdout=open(srv_log, "w"),
                           stderr=subprocess.STDOUT)
    tmux = Tmux("akcap%d" % os.getpid())
    try:
        if not wait_for(srv_log, "SERVER listening", 30):
            sys.exit("server did not start; see " + srv_log)
        url = "%s@127.0.0.1:%d/ws/y8/" % (a.user, port)
        cmd = "cd %s && TERM=%s exec %s -url %s 2>%s" % (
            REPO, a.term, a.client, url, cli_log)
        tmux.run("new-session", "-d", "-s", tmux.target, "-x", str(a.cols),
                 "-y", str(a.rows), cmd)
        tmux.run("set-option", "-t", tmux.target, "status", "off")
        spawn = wait_for(srv_log, "[SPAWN-DECISION]", 60)
        if not spawn:
            sys.exit("player never spawned; see %s and %s" % (srv_log,
                                                              cli_log))
        log("spawned: " + spawn)
        # main menu (engine/mainmenu.cpp) is drawn as glyph art, so there is
        # no text to wait for; give it a moment, then select the entries.
        for key in [k for k in a.menu_keys.split(",") if k]:
            time.sleep(1.5)
            tmux.send(key)
        if not wait_pane(tmux, a.ingame_marker, 30):
            sys.exit("world view never appeared (marker %r)"
                     % a.ingame_marker)
        log("world view up (pane shows %r)" % a.ingame_marker)
        time.sleep(a.settle)

        stop = threading.Event()
        t0 = time.time()
        player = threading.Thread(target=play_timeline,
                                  args=(tmux, timeline, t0,
                                        a.repeat_ms / 1000.0, stop),
                                  daemon=True)
        player.start()
        timings = []
        period = 1.0 / a.fps
        i = 0
        while True:
            now = time.time() - t0
            if now >= a.duration:
                break
            text = tmux.capture()
            timings.append(time.time() - t0)
            with open(os.path.join(frames_dir, "frame_%05d.ans" % i), "w",
                      encoding="utf-8") as f:
                f.write(text)
            i += 1
            nxt = t0 + i * period
            dt = nxt - time.time()
            if dt > 0:
                time.sleep(dt)
        stop.set()
        with open(os.path.join(frames_dir, "timings.json"), "w") as f:
            json.dump(timings, f)
        eff = len(timings) / max(1e-6, timings[-1] - timings[0]) \
            if len(timings) > 1 else 0
        log("captured %d frames (effective %.1f fps)" % (len(timings), eff))
    finally:
        if not a.keep:
            tmux.run("kill-server")
            srv.terminate()
            try:
                srv.wait(5)
            except subprocess.TimeoutExpired:
                srv.kill()

    n, secs = cp437_render.render_dir(
        frames_dir, a.out, a.cols, a.rows, a.cell,
        os.path.join(REPO, "assets", "fonts", "cp437_16x16.png"), a.fps,
        png_every=max(1, int(a.fps)))
    log("wrote %s (%d frames, %.1fs)" % (a.out, n, secs))


if __name__ == "__main__":
    main()
