#!/usr/bin/env python3
"""Real-game recordings: real .run/server + real .run/game_term clients.

Modes (each writes <out>.gif and <out>.receipt.json):

  walk    --skin S   join with --skin S on the default sand map, stand idle
                     1.5 s, then walk (w held) while tapping e so the camera
                     keeps turning; ~8 s.
  cycle              join with the default skin and press k every 1.6 s; the
                     server cycles wallace -> grandma -> dracula -> witch ->
                     wallace.
  rapture --skin S   real death by a hostile NPC. The default sand map has no
                     EnemyGen, so server and clients load the repo's
                     assets/a3d/game_map_y8.a3d.pre-npc-add (5 EnemyGens).
                     The victim lures NPC 0 out of its walled yard to open
                     ground and stands; the NPC's swings go through the
                     normal combat path, so HP<=0 reaches
                     SvrApplyPlayerDeathBookkeeping. Since the 2026-10-08
                     polish fixes the server backs the killer out of the
                     beam footprint and the companion steps back by itself,
                     so by default only the victim joins. --decoy restores
                     the old second player (default skin, not recorded) that
                     drew the NPC off the body. With --observer the second
                     client is instead an
                     observer (the server allows 2 connections per IP) that
                     stands ~54 units away, outside the NPC's 50-unit aggro
                     radius, with its camera turned so the victim is on
                     screen. EXPERIMENTAL: no attempt has produced a usable
                     sync recording yet (see docs/FAILURE_LOG.md). The victim's camera taps e throughout. The GIF
                     window is cut around the server's [death] line: 3 s
                     before to 7.6 s after (respawn at +6 s).

Receipt: commit, sha256 of both binaries, the client command lines, server
JOIN/[skin-select]/[death]/[respawn] lines, and frame timing.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session as ss  # noqa: E402

REPO = ss.REPO
NPC_MAP = "assets/a3d/game_map_y8.a3d.pre-npc-add"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=REPO, capture_output=True,
                          text=True).stdout.strip()


def server_lines(server, needles):
    return [l for l in server.lines() if any(n in l for n in needles)]


def receipt(out, mode, server, clients, extra):
    rec = {
        "mode": mode,
        "gif": os.path.relpath(out + ".gif", REPO),
        "commit": git("rev-parse", "HEAD"),
        "worktree_dirty": bool(git("status", "--porcelain", "--", "engine",
                                   "server", "platform", "net")),
        "binaries": {
            ".run/server": sha256(os.path.join(REPO, ".run", "server")),
            ".run/game_term": sha256(os.path.join(REPO, ".run", "game_term")),
        },
        "server_cmd": " ".join(server.proc.args),
        "clients": {c.name: c.cmdline for c in clients},
        "server_log": {
            "join": server_lines(server, ["joined as ID", "[skin-select]",
                                          "[SPAWN-DECISION]"]),
            "death": server_lines(server, ["[death]"]),
            "respawn": server_lines(server, ["[respawn]"]),
            "rapture": server_lines(server, ["[rapture"]),
            "companion": server_lines(server, ["[companion]",
                                               "[rapture-standoff]"]),
            "npc_retreat": server_lines(server, ["[rapture-retreat]"]),
        },
        "client_log": {c.name: c.pane_log_text().splitlines()[-12:]
                       for c in clients},
    }
    rec.update(extra)
    with open(out + ".receipt.json", "w") as f:
        json.dump(rec, f, indent=2)
    ss.log("wrote %s.receipt.json" % out)


class CameraTapper:
    """Tap e every `period` s (one byte -> ~140 ms of held rotate)."""

    def __init__(self, client, period):
        self.client, self.period = client, period
        self.next = time.time()

    def poll(self):
        if self.period and time.time() >= self.next:
            self.client.send("e")
            self.next += self.period


def run_walk(a, work):
    srv = ss.Server(work)
    c = ss.Client("victim", srv, work, skin=a.skin)
    try:
        if not c.enter_world():
            raise SystemExit("client never reached the world")
        time.sleep(1.0)
        # step sideways first so the dog companion (it stops 7 units from
        # its owner) settles beside the player instead of on top of it.
        c.hold("d", 1.0)
        time.sleep(2.2)
        rec = ss.Recorder(c, os.path.join(work, "frames"), a.fps).start()
        t0 = time.time()
        time.sleep(1.5)                          # idle
        tap = CameraTapper(c, a.tap)
        # Movement is camera-relative, so w alone keeps the same facing
        # towards the camera; cycle w/d/s/a while the camera keeps turning so
        # every facing is drawn.
        keys = "wdsa"
        while time.time() - t0 < a.duration:
            seg = int((time.time() - t0 - 1.5) / a.seg)
            c.hold(keys[seg % 4], 0.2)
            tap.poll()
            time.sleep(0.05)
        c.release()
        rec.stop()
        _, timings = rec.render(a.out + ".gif", png_every=a.fps)
        receipt(a.out, "walk", srv, [c], {
            "skin": a.skin, "frames": len(timings),
            "seconds": round(timings[-1], 2),
            "script": "idle 1.5 s, then w/d/s/a held %.1f s each in turn "
                      "while e is tapped every %.2f s" % (a.seg, a.tap)})
    finally:
        c.stop()
        srv.stop()


def run_cycle(a, work):
    srv = ss.Server(work)
    c = ss.Client("victim", srv, work, skin=a.skin)
    try:
        if not c.enter_world():
            raise SystemExit("client never reached the world")
        time.sleep(2.0)
        rec = ss.Recorder(c, os.path.join(work, "frames"), a.fps).start()
        t0 = time.time()
        presses = []
        tap = CameraTapper(c, a.tap)
        nxt = 1.2
        while time.time() - t0 < a.duration:
            if time.time() - t0 >= nxt and len(presses) < 4:
                c.send("k")
                presses.append(round(time.time() - t0, 2))
                nxt += 1.8
            # a short step each second keeps the dog companion off the body
            if int((time.time() - t0) * 2) % 4 == 0:
                c.hold("s", 0.1)
            tap.poll()
            time.sleep(0.05)
        rec.stop()
        _, timings = rec.render(a.out + ".gif", png_every=a.fps)
        receipt(a.out, "cycle", srv, [c], {
            "k_presses_s": presses, "frames": len(timings),
            "seconds": round(timings[-1], 2)})
    finally:
        c.stop()
        srv.stop()


NPC_EID = 50  # first EnemyGen NPC on the NPC map (SVR_MAX_CLIENTS + 0)
LURE_NEAR = (29.5, 13.5)
LURE_PATH = [(20.0, -45.0), (35.0, -10.0), LURE_NEAR]
DECOY_FLEE_PATH = [(20.0, -45.0), (-2.8, -73.6), (-30.0, -110.0)]
LURE_XY = (40.0, -8.0)


def npc_live_pos(srv, eid=NPC_EID):
    """Latest server-side position of an NPC from its AI/spawn lines."""
    ai = [l for l in srv.grep("[SVR-NPC-AI]") if " eid=%d " % eid in l]
    if ai:
        p = ai[-1].split(" npc=(")[1].split(")")[0].split(",")
        return float(p[0]), float(p[1])
    for l in srv.grep("[SVR-NPC-SPAWN]"):
        if " eid=%d " % eid in l:
            p = l.split(" pos=(")[1].split(")")[0].split(",")
            return float(p[0]), float(p[1])
    raise SystemExit("NPC %d not spawned" % eid)


def screen_axes(yaw):
    """World XY of screen-right and screen-up (away from camera) at yaw."""
    r = math.radians(yaw)

    def rot(v):
        return (v[0] * math.cos(r) - v[1] * math.sin(r),
                v[0] * math.sin(r) + v[1] * math.cos(r))
    return rot(ss.KEY_BASIS["d"]), rot(ss.KEY_BASIS["w"])


def unit(dx, dy):
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def turn_camera_to(client, want_yaw, tol=6.0, timeout=8.0):
    """Tap q/e until the camera yaw is within `tol` degrees of want_yaw."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.status()
        if not st:
            time.sleep(0.1)
            continue
        d = (want_yaw - st["yaw"] + 180.0) % 360.0 - 180.0
        if abs(d) <= tol:
            return st
        # e turns yaw up (measured: -57.4 -> 134.4 for ~1.4 s of e)
        client.send("e" if d > 0 else "q")
        time.sleep(0.12)
    return client.status()


def status_track(win_dir, timings, every=3):
    """[t, yaw, z] from the client's own status line, every `every` frames
    of a rendered window (evidence for the turning camera and the lift)."""
    out = []
    for i in range(0, len(timings), every):
        with open(os.path.join(win_dir, "frame_%05d.ans" % i),
                  encoding="utf-8") as f:
            t = ss.SGR.sub("", f.read())
        pos, yaw = ss.POS_RE.findall(t), ss.YAW_RE.findall(t)
        out.append([round(timings[i], 2),
                    float(yaw[-1][0]) if yaw else None,
                    float(pos[-1][2]) if pos else None])
    return out


def run_rapture(a, work):
    import threading
    srv = ss.Server(work, map_path=NPC_MAP)
    clients = []
    try:
        npc_live_pos(srv)
        v = ss.Client("victim", srv, work, skin=a.skin, map_path=NPC_MAP,
                      cols=a.cols, rows=a.rows)
        clients.append(v)

        def wait_connected(c):
            t0 = time.time()
            while "connected with ID" not in c.pane_log_text():
                if time.time() - t0 > 60:
                    raise SystemExit("%s never connected" % c.name)
                time.sleep(0.2)
        # join order fixes player ids: victim 0, then decoy or observer.
        # The server accepts only 2 connections per IP (RQ-103), so a run
        # has either a decoy (victim GIFs) or an observer (sync GIF).
        wait_connected(v)
        dcy = None
        o = None
        if a.decoy and not a.observer:
            dcy = ss.Client("decoy", srv, work, skin="default",
                            map_path=NPC_MAP, user="decoy")
            clients.append(dcy)
        elif a.observer:
            o = ss.Client("observer", srv, work, skin="default",
                          map_path=NPC_MAP, user="observer",
                          cols=a.obs_cols, rows=a.obs_rows)
            clients.append(o)
        ok = {}
        ths = [threading.Thread(target=lambda c=c: ok.__setitem__(
            c.name, c.enter_world())) for c in clients]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        if not all(ok.values()):
            raise SystemExit("a client never reached the world: %r" % ok)
        time.sleep(1.0)
        recs = {"victim": ss.Recorder(v, os.path.join(work, "frames_victim"),
                                      a.fps).start()}
        if o:
            recs["observer"] = ss.Recorder(
                o, os.path.join(work, "frames_observer"), a.fps).start()
        tap_v = CameraTapper(v, a.tap)

        def hit_on_victim():
            return [l for l in srv.grep("outcome=damage")
                    if "resolved_target=0 " in l and "target_kind=player" in l]

        def victim_death():
            return [l for l in srv.grep("[death]")
                    if "target_id=0 " in l and "target_kind=player" in l]

        phase = {"p": "approach"}
        stop = threading.Event()

        # Decoy: a melee NPC stands on its kill, hiding the body. A second
        # player trails the victim at ~30 units; once the victim is dead the
        # NPC retargets the decoy (nearest player within its 50-unit aggro
        # radius) and walks off the body; when it gets close the decoy runs
        # beyond the radius so the NPC stops there and the decoy survives.
        def decoy_loop():
            fled = False
            while not stop.is_set():
                vs, ds = v.status(), dcy.status()
                if not vs or not ds:
                    time.sleep(0.1)
                    continue
                if phase["p"] != "dead":
                    ss.steer_steps(dcy, (vs["x"], vs["y"]),
                                   stop_dist=a.decoy_gap, timeout=1.0)
                    time.sleep(0.1)
                    continue
                n = npc_live_pos(srv)
                if not fled and math.hypot(n[0] - ds["x"], n[1] - ds["y"]) < 14:
                    # run back toward the player spawn: open ground, and
                    # away from the other EnemyGens (NPC 52 killed a decoy
                    # that fled north-east)
                    # fast (continuous) walking along the open approach
                    # path; the NPC walks ~20 units/s, players ~55.
                    for wp in DECOY_FLEE_PATH:
                        ss.steer_to(dcy, wp, stop_dist=6, timeout=4.0)
                    dcy.release()
                    fled = True
                time.sleep(0.1)

        # Observer: once the NPC is hitting the victim, stand ~obs_dist from
        # the victim on the side away from the decoy (outside the NPC's
        # aggro radius) and turn its camera so the victim is on screen-right.
        def observer_loop():
            placed = False
            while not stop.is_set():
                vs, os_ = v.status(), o.status()
                if not vs or not os_:
                    time.sleep(0.1)
                    continue
                if phase["p"] == "approach":
                    # wait at the spawn, far outside the NPC's aggro radius
                    time.sleep(0.1)
                    continue
                if not placed:
                    # Stand obs_dist from the NPC, on the far side of the
                    # victim (the NPC shoves the victim ~10 units towards us
                    # while it swings), with the camera turned so the victim
                    # lands screen-left and below centre, where neither the
                    # HUD nor the minimap is drawn: offset (right*h + up*v)
                    # with v/h = 22/48 lies 24.6 degrees left of screen-right.
                    # Placed as soon as the NPC is lured, from the predicted
                    # kill spot (LURE_XY) and the NPC's way in (LURE_NEAR).
                    ux, uy = unit(LURE_XY[0] - LURE_NEAR[0],
                                  LURE_XY[1] - LURE_NEAR[1])
                    want = math.degrees(math.atan2(uy, ux)) - 24.6
                    turn_camera_to(o, want)
                    goal = (LURE_XY[0] + ux * (a.obs_dist - 4),
                            LURE_XY[1] + uy * (a.obs_dist - 4))
                    ss.steer_steps(o, goal, stop_dist=3, timeout=5.0,
                                   settle=0.15)
                    placed = True
                # The NPC shoves the victim around while it swings, so keep
                # outside its aggro radius reactively: back straight away
                # from it whenever it is closer than keep_dist.
                n = npc_live_pos(srv)
                dn = math.hypot(os_["x"] - n[0], os_["y"] - n[1])
                if dn < a.obs_keep:
                    ax, ay = unit(os_["x"] - n[0], os_["y"] - n[1])
                    step = a.obs_keep - dn + 3
                    ss.steer_steps(o, (os_["x"] + ax * step, os_["y"] + ay * step),
                                   stop_dist=2, timeout=1.5, settle=0.12)
                time.sleep(0.1)

        ths = []
        if dcy:
            ths.append(threading.Thread(target=decoy_loop, daemon=True))
        if o:
            ths.append(threading.Thread(target=observer_loop, daemon=True))
        for t in ths:
            t.start()
        # Victim: NPC 0 lives in a walled yard (NPCs walk straight at their
        # target and stick on walls); it gets out through a gap near
        # (25, 22), and kills near the yard are hidden by a roof. Stand at
        # LURE_NEAR (inside the 50-unit aggro radius, on the NPC->gap line),
        # then once the NPC is coming walk to LURE_XY, open ground further
        # down the same line, and wait there.
        # Walk there by fixed open-ground waypoints with the camera still
        # (a turning camera makes the camera-relative steering zig-zag into
        # walls); camera taps start once the victim is in position.
        t_walk = time.time()
        for wp in LURE_PATH:
            ss.steer_steps(v, wp, stop_dist=4.0, timeout=60.0)
        v.release()
        tap_v.next = time.time()
        while not [l for l in srv.grep("[SVR-NPC-AI]")
                   if " eid=%d target=0 " % NPC_EID in l]:
            if time.time() - t_walk > 90:
                raise SystemExit("NPC never aggroed; see %s" % srv.log_path)
            tap_v.poll()
            time.sleep(0.05)
        phase["p"] = "lured"
        time.sleep(0.3)
        # stop walking at the first hit, or the NPC may switch to the decoy
        ss.steer_steps(v, LURE_XY, stop_dist=3.0, timeout=12.0,
                       tick=tap_v.poll, until=lambda: bool(hit_on_victim()))
        v.release()
        t_stand = time.time()
        while not hit_on_victim() and time.time() - t_stand < 15:
            tap_v.poll()
            time.sleep(0.05)
        while not hit_on_victim():
            if time.time() - t_walk > 200:
                raise SystemExit("NPC never reached the victim; see %s"
                                 % srv.log_path)
            st = v.status()
            g = npc_live_pos(srv)
            if st and math.hypot(g[0] - st["x"], g[1] - st["y"]) <= 14:
                v.release()
                tap_v.poll()
                time.sleep(0.1)
                continue
            # one long call so the stall detector can side-step walls
            ss.steer_to(v, lambda: npc_live_pos(srv), stop_dist=12,
                        timeout=10.0, tick=tap_v.poll)
        v.release()
        phase["p"] = "hit"
        ss.log("first hit: " + hit_on_victim()[0])
        death_line, t_death = None, None
        t_wait = time.time()
        while time.time() - t_wait < 30:
            hits = victim_death()
            if hits:
                death_line, t_death = hits[0], time.time()
                break
            tap_v.poll()
            time.sleep(0.05)
        if not death_line:
            raise SystemExit("victim was not killed; see %s" % srv.log_path)
        phase["p"] = "dead"
        ss.log("death: " + death_line)
        while time.time() - t_death < a.post + 1.0:
            tap_v.poll()
            time.sleep(0.05)
        stop.set()
        for r in recs.values():
            r.stop()
        extra = {"skin": a.skin, "map": NPC_MAP, "killer_npc_eid": NPC_EID,
                 "death_seen_wall": t_death,
                 "window": "[death] -%.1f s .. +%.1f s (wall clock when the "
                           "server log line appeared)" % (a.pre, a.post),
                 "other_player_deaths": [l for l in srv.grep("[death]")
                                         if "target_kind=player" in l
                                         and "target_id=0 " not in l],
                 "status_end": {c.name: c.status() for c in clients}}
        win, timings = recs["victim"].render(
            a.out + ".gif", t_death - a.pre, t_death + a.post,
            png_every=max(1, a.fps // 3))
        extra["frames"] = len(timings)
        extra["seconds"] = round(timings[-1], 2)
        extra["camera_tap_s"] = a.tap
        extra["status_track"] = status_track(win, timings)
        receipt(a.out, "rapture", srv, clients, dict(extra,
                recorded_from="victim client"))
        if o:
            obs_out = a.observer_out or os.path.join(
                os.path.dirname(a.out), "observer-rapture")
            _, t2 = recs["observer"].render(
                obs_out + ".gif", t_death - a.pre, t_death + a.post,
                png_every=max(1, a.fps // 3))
            extra = dict(extra, frames=len(t2), seconds=round(t2[-1], 2),
                         recorded_from="observer client")
            receipt(obs_out, "rapture-observer", srv, clients, extra)
    finally:
        for c in clients:
            c.stop()
        srv.stop()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", choices=["walk", "cycle", "rapture"])
    ap.add_argument("--out", required=True, help="path without .gif")
    ap.add_argument("--skin", default=None)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--duration", type=float, default=8.0)
    ap.add_argument("--tap", type=float, default=0.6,
                    help="seconds between camera e taps (0 = none)")
    ap.add_argument("--seg", type=float, default=1.6,
                    help="walk mode: seconds per walk key")
    ap.add_argument("--cols", type=int, default=160,
                    help="rapture: victim terminal columns")
    ap.add_argument("--rows", type=int, default=50,
                    help="rapture: victim terminal rows (36 = the minimum "
                         "web viewport, for the UFO top-margin check)")
    ap.add_argument("--observer", action="store_true")
    ap.add_argument("--decoy", action="store_true",
                    help="rapture: add the old decoy player that draws the "
                         "NPC off the body (not needed since the server-side "
                         "NPC retreat)")
    ap.add_argument("--observer-out", default=None)
    ap.add_argument("--obs-dist", type=float, default=62.0,
                    help="observer distance from the NPC at the first hit "
                         "(NPC aggro radius is 50; ~1.4 columns and ~0.9 "
                         "rows per world unit)")
    ap.add_argument("--obs-keep", type=float, default=55.0,
                    help="observer backs away when the NPC is closer")
    ap.add_argument("--obs-cols", type=int, default=160)
    ap.add_argument("--obs-rows", type=int, default=50)
    ap.add_argument("--decoy-gap", type=float, default=45.0)
    ap.add_argument("--pre", type=float, default=3.0)
    ap.add_argument("--post", type=float, default=7.6)
    ap.add_argument("--work", default=None)
    a = ap.parse_args()
    a.out = os.path.abspath(a.out[:-4] if a.out.endswith(".gif") else a.out)
    work = a.work or a.out + ".work"
    os.makedirs(work, exist_ok=True)
    ss.capture.ensure_pulse(ss.log)
    {"walk": run_walk, "cycle": run_cycle, "rapture": run_rapture}[a.mode](
        a, work)


if __name__ == "__main__":
    main()
