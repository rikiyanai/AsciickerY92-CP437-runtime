#!/usr/bin/env python3
"""Multi-client building blocks for real-game terminal recordings.

capture.py records one client with a fixed key timeline. This module lets a
scenario script run one real `.run/server` and several real `.run/game_term`
clients (each in its own detached tmux server), steer them from the status
line the client prints (`yaw .. dir ..` / `pos x y z`), record any client's
pane at a fixed rate with wall-clock stamps, and map server log lines
(`[death] tick=..`) to wall-clock time so a GIF window can be cut around a
server event.

Used by record_gameplay.py. Prerequisites as for capture.py.
"""
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time

import capture  # noqa: E402  (same directory)
import cp437_render  # noqa: E402

REPO = capture.REPO
SGR = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
POS_RE = re.compile(r"pos (-?\d+\.\d+) (-?\d+\.\d+) (-?\d+\.\d+)")
YAW_RE = re.compile(r"yaw (-?\d+\.\d+) dir (-?\d+\.\d+)")


def _sigterm(signum, frame):
    raise SystemExit("terminated (signal %d)" % signum)


import signal  # noqa: E402
signal.signal(signal.SIGTERM, _sigterm)


def log(msg):
    print("[session] " + msg, flush=True)


class Server:
    def __init__(self, work, port=0, map_path=None, env=None):
        self.work = work
        self.port = port or capture.free_port()
        self.log_path = os.path.join(work, "server.log")
        cmd = ["stdbuf", "-oL", os.path.join(REPO, ".run", "server"),
               "--port", str(self.port)]
        if map_path:
            cmd += ["--map", map_path]
        e = dict(os.environ)
        # [death]/[respawn]/[skin-select] are runtime-diagnostic lines.
        e["ASCIICKER_DEBUG_RUNTIME_DIAGNOSTICS"] = "1"
        e.update(env or {})
        self.t_start = time.time()
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=e,
                                     stdout=open(self.log_path, "w"),
                                     stderr=subprocess.STDOUT)
        if not capture.wait_for(self.log_path, "SERVER listening", 60):
            raise SystemExit("server did not start; see " + self.log_path)
        self.tick0 = None

    def lines(self):
        with open(self.log_path, errors="replace") as f:
            return f.read().splitlines()

    def grep(self, needle):
        return [l for l in self.lines() if needle in l]

    def wait_line(self, needle, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            hits = self.grep(needle)
            if hits:
                return hits[0]
            time.sleep(0.1)
        return None

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Client:
    def __init__(self, name, server, work, skin=None, map_path=None,
                 cols=160, rows=50, user="player"):
        self.name = name
        self.cols, self.rows = cols, rows
        self.tmux = capture.Tmux("aks_%s_%d" % (name, os.getpid()))
        self.err = os.path.join(work, "%s.client.stderr.log" % name)
        self.pane_log = os.path.join(work, "%s.pane.log" % name)
        args = [os.path.join(REPO, ".run", "game_term")]
        if skin:
            args += ["--skin", skin]
        if map_path:
            args += ["--map", map_path]
        args += ["-url", "%s@127.0.0.1:%d/ws/y8/" % (user, server.port)]
        cmd = "cd %s && TERM=xterm-256color exec %s 2>%s" % (
            REPO, " ".join(args), self.err)
        self.cmdline = " ".join(args)
        self.tmux.run("new-session", "-d", "-s", self.tmux.target, "-x",
                      str(cols), "-y", str(rows), cmd)
        self.tmux.run("set-option", "-t", self.tmux.target, "status", "off")
        # client stdout is the screen; keep a copy so [NATIVE-AUTH-*] and
        # [skin-select] prints survive.
        self.tmux.run("pipe-pane", "-o", "-t", self.tmux.target,
                      "grep -aoE --line-buffered '(connected with ID|"
                      "\\[LOAD\\]|\\[NATIVE-AUTH|\\[skin-select\\]|"
                      "\\[FL-2896-RECV-(DROP|QUEUE)|\\[DEATH-DIAG\\])"
                      "[ -~]{0,200}' >> %s" % self.pane_log)
        self._hold = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)
        self._pump.start()

    # -- input --------------------------------------------------------------
    def send(self, key):
        self.tmux.send(key)

    def hold(self, key, seconds):
        """Re-send `key` every 40 ms for `seconds` (no key-up in a tty)."""
        with self._lock:
            self._hold[key] = time.time() + seconds

    def release(self, key=None):
        with self._lock:
            if key is None:
                self._hold.clear()
            else:
                self._hold.pop(key, None)

    def _pump_loop(self):
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                keys = [k for k, until in self._hold.items() if until > now]
                for k in [k for k, u in self._hold.items() if u <= now]:
                    del self._hold[k]
            for k in keys:
                self.tmux.send(k)
            time.sleep(0.04)

    # -- screen -------------------------------------------------------------
    def text(self):
        r = self.tmux.run("capture-pane", "-p", "-t", self.tmux.target)
        return r.stdout.decode("utf-8", errors="replace")

    def ansi(self):
        return self.tmux.capture()

    def status(self):
        t = self.text()
        pos = POS_RE.findall(t)
        yaw = YAW_RE.findall(t)
        if not pos or not yaw:
            return None
        x, y, z = map(float, pos[-1])
        return {"x": x, "y": y, "z": z, "yaw": float(yaw[-1][0]),
                "dir": float(yaw[-1][1])}

    def pane_log_text(self):
        try:
            with open(self.pane_log, errors="replace") as f:
                return SGR.sub("", f.read())
        except FileNotFoundError:
            return ""

    def enter_world(self, timeout=600):
        """Main menu: NEW GAME, wait until the level has loaded, CONTINUE.

        The menu and LOADING screen are glyph art, so progress is read from
        the client's own stdout prints (kept by pipe-pane). A second Enter
        while still loading restarts the level and drops the server
        connection (mainmenu.cpp start_new_game -> ResetGame), so CONTINUE
        is sent only after `[LOAD] progress -> 0 (done)`.
        """
        t0 = time.time()
        while "connected with ID:" not in self.pane_log_text():
            if time.time() - t0 > 60:
                return False
            time.sleep(0.2)
        time.sleep(1.5)
        self.send("Enter")
        while "[LOAD] progress -> 0 (done)" not in self.pane_log_text():
            if time.time() - t0 > timeout:
                return False
            time.sleep(0.3)
        time.sleep(1.0)
        self.send("Enter")
        while time.time() - t0 < timeout:
            if "YOU:" in self.text():
                return True
            time.sleep(0.3)
        return False

    def stop(self):
        self._stop.set()
        self.tmux.run("kill-server")


class Recorder:
    """Grab one client's pane at `fps` until stopped; frames + wall stamps."""

    def __init__(self, client, out_dir, fps=15):
        self.client = client
        self.dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        for n in os.listdir(out_dir):
            os.unlink(os.path.join(out_dir, n))
        self.fps = fps
        self.stamps = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._t.start()
        return self

    def _loop(self):
        period = 1.0 / self.fps
        t0 = time.time()
        i = 0
        while not self._stop.is_set():
            text = self.client.ansi()
            self.stamps.append(time.time())
            with open(os.path.join(self.dir, "frame_%05d.ans" % i), "w",
                      encoding="utf-8") as f:
                f.write(text)
            i += 1
            dt = t0 + i * period - time.time()
            if dt > 0:
                time.sleep(dt)

    def stop(self):
        self._stop.set()
        self._t.join(5)
        with open(os.path.join(self.dir, "stamps.json"), "w") as f:
            json.dump(self.stamps, f)

    def render(self, out_gif, t_from=None, t_to=None, png_every=0):
        """Write the frames whose wall stamp lies in [t_from, t_to]."""
        sel = [i for i, s in enumerate(self.stamps)
               if (t_from is None or s >= t_from) and (t_to is None or s <= t_to)]
        if not sel:
            raise SystemExit("no frames in window")
        sub = self.dir + ".window"
        if os.path.isdir(sub):
            shutil.rmtree(sub)
        os.makedirs(sub)
        timings = []
        for j, i in enumerate(sel):
            shutil.copy(os.path.join(self.dir, "frame_%05d.ans" % i),
                        os.path.join(sub, "frame_%05d.ans" % j))
            timings.append(self.stamps[i] - self.stamps[sel[0]])
        with open(os.path.join(sub, "timings.json"), "w") as f:
            json.dump(timings, f)
        n, secs = cp437_render.render_dir(
            sub, out_gif, self.client.cols, self.client.rows, 8,
            os.path.join(REPO, "assets", "fonts", "cp437_16x16.png"),
            self.fps, png_every=png_every)
        log("wrote %s (%d frames, %.1fs)" % (out_gif, n, secs))
        return sub, timings


def steer_to(client, target, stop_dist=3.0, timeout=30.0, tick=None):
    """Walk toward a world XY point (or a callable returning one) with
    w/a/s/d chosen from the camera yaw. When progress stalls (a wall or a
    fence), side-step for a moment, alternating sides, then try again.
    `tick` is called every loop (e.g. camera taps)."""
    t0 = time.time()
    best_d, best_t, side = 1e9, time.time(), 0
    sidestep_until = 0
    while time.time() - t0 < timeout:
        if tick:
            tick()
        st = client.status()
        goal = target() if callable(target) else target
        if not st or goal is None:
            time.sleep(0.1)
            continue
        dx, dy = goal[0] - st["x"], goal[1] - st["y"]
        d = math.hypot(dx, dy)
        if d <= stop_dist:
            client.release()
            return st
        now = time.time()
        if d < best_d - 1.0:
            best_d, best_t = d, now
        if now < sidestep_until:
            pass
        elif now - best_t > 0.8:
            # perpendicular to the goal, alternating left/right, growing
            side += 1
            sgn = 1 if side % 2 else -1
            px, py = -dy * sgn, dx * sgn
            client.release()
            client.hold(best_key(st["yaw"], px, py), 0.4 + 0.3 * min(side, 3))
            sidestep_until = now + 0.4 + 0.3 * min(side, 3)
            best_d, best_t = d, sidestep_until
        else:
            for k in best_key(st["yaw"], dx, dy, diagonal=True):
                client.hold(k, 0.2)
        time.sleep(0.08)
    client.release()
    return client.status()


def steer_steps(client, target, stop_dist=3.0, timeout=60.0, tick=None,
                settle=0.3, until=None):
    """Precise walking: one key byte per step (the client holds it ~140 ms,
    a few world units), then wait for the status line to settle. Slower
    than steer_to but it does not overshoot when the client renders
    slowly (status stale) on heavy maps. Stalls side-step 3 steps."""
    t0 = time.time()
    best_d, stall, side = 1e9, 0, 0
    while time.time() - t0 < timeout:
        if until and until():
            return client.status()
        if tick:
            tick()
        st = client.status()
        goal = target() if callable(target) else target
        if not st or goal is None:
            time.sleep(0.1)
            continue
        dx, dy = goal[0] - st["x"], goal[1] - st["y"]
        d = math.hypot(dx, dy)
        if d <= stop_dist:
            return st
        if d < best_d - 0.5:
            best_d, stall = d, 0
        else:
            stall += 1
        if stall >= 3:
            side += 1
            sgn = 1 if side % 2 else -1
            k = best_key(st["yaw"], -dy * sgn, dx * sgn)
            for _ in range(2 + min(side, 4)):
                client.send(k)
                time.sleep(settle)
            stall, best_d = 0, 1e9
            continue
        for k in best_key(st["yaw"], dx, dy, diagonal=True):
            client.send(k)
        time.sleep(settle)
    return client.status()


# World direction (unit XY) of each walk key at yaw=0, rotated by the camera
# yaw. Measured with calibrate() on the sand map at yaw -57.4 and 134.4.
KEY_BASIS = {"w": (0.0, 1.0), "a": (-1.0, 0.0), "s": (0.0, -1.0),
             "d": (1.0, 0.0)}


def best_key(yaw, dx, dy, diagonal=False):
    """Walk key(s) whose world direction at `yaw` best matches (dx, dy).

    With diagonal=True a two-key chord (e.g. "wd") may be returned; both
    keys held together walk diagonally, halving the 45-degree error of a
    single axis key."""
    a = math.radians(yaw)
    dirs = {}
    for k, (bx, by) in KEY_BASIS.items():
        dirs[k] = (bx * math.cos(a) - by * math.sin(a),
                   bx * math.sin(a) + by * math.cos(a))
    if diagonal:
        for k1, k2 in ("wd", "wa", "sd", "sa"):
            x = dirs[k1][0] + dirs[k2][0]
            y = dirs[k1][1] + dirs[k2][1]
            n = math.hypot(x, y) or 1.0
            dirs[k1 + k2] = (x / n, y / n)
    n = math.hypot(dx, dy) or 1.0
    return max(dirs, key=lambda k: dirs[k][0] * dx / n + dirs[k][1] * dy / n)


def calibrate(client, seconds=0.6):
    """Measure each walk key's world displacement and store it at yaw=0."""
    for k in "wasd":
        st0 = client.status()
        client.hold(k, seconds)
        time.sleep(seconds + 0.4)
        st1 = client.status()
        dx, dy = st1["x"] - st0["x"], st1["y"] - st0["y"]
        n = math.hypot(dx, dy) or 1.0
        a = -math.radians(st1["yaw"])
        bx = (dx * math.cos(a) - dy * math.sin(a)) / n
        by = (dx * math.sin(a) + dy * math.cos(a)) / n
        KEY_BASIS[k] = (bx, by)
        log("calibrate %s: d=(%.1f,%.1f) yaw=%.1f basis=(%.2f,%.2f)"
            % (k, dx, dy, st1["yaw"], bx, by))
    return dict(KEY_BASIS)
