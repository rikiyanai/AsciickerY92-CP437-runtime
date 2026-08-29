"""health.py — Two-level health probes for the launcher.

Level 1 (fast): file-existence + mtime checks. Used for the status bar on
                every menu draw. No subprocess, no SSH.
Level 2 (full): 14-signal table. Triggered by [h] at the main menu.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import socket
import subprocess
import time
from enum import Enum
from typing import Literal
from urllib.request import Request, urlopen
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from scripts.launcher_lib import blender_paths as _blender_paths
from scripts.launcher_lib import local_config as _local_config
from scripts.launcher_lib import option_tree as _option_tree
from scripts.launcher_lib import server_env as _server_env

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
RUN_DIR   = REPO_ROOT / ".run"
ASSET_DIR = REPO_ROOT / "assets"
_SOURCE_MTIME_CACHE: tuple[float, float] = (0.0, 0.0)
FULL_HEALTH_CACHE = RUN_DIR / "launcher-full-health-cache.json"
FULL_HEALTH_CACHE_MAX_AGE_S = 15 * 60
MP_PROBE_CACHE_MAX_AGE_S = 5 * 60
RUNTIME_PROOF_MAX_AGE_S = 24 * 60 * 60
PROBE_FRESH_S  =  60   # < 1 min  → "fresh"
PROBE_RECENT_S = 300   # 1–5 min  → "recent"; > 5 min → "stale"; no cache → "unknown"


class ProbeState(str, Enum):
    """Semantic health signal state.  health.py produces these; renderer maps them to icons."""
    OK             = "ok"
    WARN           = "warn"
    FAIL           = "fail"
    NOT_CONFIGURED = "not_configured"
    UNKNOWN        = "unknown"
    SKIP           = "skip"


# Map ProbeState → emoji icon.  Owned here so launcher.py can import without re-defining.
PROBE_ICONS: dict[ProbeState, str] = {
    ProbeState.OK:             "🟢",
    ProbeState.WARN:           "🟡",
    ProbeState.FAIL:           "🔴",
    ProbeState.NOT_CONFIGURED: "⚪",
    ProbeState.UNKNOWN:        "🟡",
    ProbeState.SKIP:           "—",
}

_STR_TO_PROBE: dict[str, ProbeState] = {p.value: p for p in ProbeState}
# Backward-compat: old mp-probe-cache files stored emoji
_EMOJI_TO_PROBE: dict[str, ProbeState] = {
    "🟢": ProbeState.OK,
    "🔴": ProbeState.FAIL,
    "🟡": ProbeState.UNKNOWN,
}

# ---------------------------------------------------------------------------
# Level 1 — fast probes (status bar)
# ---------------------------------------------------------------------------

def _binary_ok(name: str) -> bool:
    """Binary exists in .run/."""
    return (RUN_DIR / name).is_file()


def _source_mtimes() -> float:
    """Latest mtime across gameplay/editor/platform/server source trees."""
    global _SOURCE_MTIME_CACHE
    now = time.time()
    cached_at, cached_value = _SOURCE_MTIME_CACHE
    if now - cached_at < 2.0:
        return cached_value

    latest = 0.0
    roots = [
        REPO_ROOT,
        REPO_ROOT / "engine",
        REPO_ROOT / "editor",
        REPO_ROOT / "platform",
        REPO_ROOT / "server",
    ]
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("*.cpp", "*.h"):
            for path in root.rglob(pattern) if root != REPO_ROOT else root.glob(pattern):
                latest = max(latest, path.stat().st_mtime)
    _SOURCE_MTIME_CACHE = (now, latest)
    return latest


def _binary_fresh(name: str) -> bool:
    """Binary exists AND is newer than the latest source file."""
    bin_path = RUN_DIR / name
    if not bin_path.is_file():
        return False
    return bin_path.stat().st_mtime >= _source_mtimes()


def _venv_ok() -> bool:
    candidates = [
        REPO_ROOT / ".venv" / "bin" / "python3",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    return any(candidate.is_file() for candidate in candidates)


def _git_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "branch", "--show-current"],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() or "?"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return "?"


def _mp_probe_states() -> tuple[ProbeState, ProbeState]:
    """Return (candidate, current) ProbeState from config, full-health cache, or mp-probe-cache.

    Priority:
      1. Not configured → NOT_CONFIGURED (user needs to run wizard)
      2. Full health cache fresh (< FULL_HEALTH_CACHE_MAX_AGE_S) → cached health signal first,
         fall back to WS signal.  The health endpoint (FL-2416 fix) is the authoritative
         health owner; WS upgrade probes are a secondary reachability check.
      3. mp-probe-cache fresh (< MP_PROBE_CACHE_MAX_AGE_S) → stored probe result
      4. Otherwise → UNKNOWN (configured but not recently probed)
    """
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    if topology in ("", "none"):
        return ProbeState.NOT_CONFIGURED, ProbeState.NOT_CONFIGURED

    cand_addr = env.get("AK_MP_SERVER_CANDIDATE_HOST", "") or env.get("AK_MP_SERVER_CANDIDATE_DOMAIN", "")
    curr_addr = env.get("AK_MP_SERVER_CURRENT_HOST", "") or env.get("AK_MP_SERVER_CURRENT_DOMAIN", "")
    if not cand_addr or not curr_addr:
        return ProbeState.NOT_CONFIGURED, ProbeState.NOT_CONFIGURED

    # Full health cache (authoritative when fresh)
    snapshot = _read_full_health_cache()
    if snapshot:
        # Prefer health endpoint signal (FL-2416 fix: game-server-aware truth)
        cand_health = _cache_signal(snapshot, "MP candidate health")
        curr_health = _cache_signal(snapshot, "MP current health")
        if cand_health and curr_health:
            return (
                _STR_TO_PROBE.get(cand_health[0], ProbeState.UNKNOWN),
                _STR_TO_PROBE.get(curr_health[0], ProbeState.UNKNOWN),
            )
        # Fall back to WS signal if health endpoint is not yet cached
        cand_ws = _cache_signal(snapshot, "MP candidate WS")
        curr_ws = _cache_signal(snapshot, "MP current WS")
        if cand_ws and curr_ws:
            return (
                _STR_TO_PROBE.get(cand_ws[0], ProbeState.UNKNOWN),
                _STR_TO_PROBE.get(curr_ws[0], ProbeState.UNKNOWN),
            )

    # mp-probe-cache (written by live TCP probe)
    cache_file = RUN_DIR / "mp-probe-cache"
    if cache_file.exists():
        try:
            data: dict[str, str] = {}
            for line in cache_file.read_text().splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip()
            ts = float(data.get("last_check_ts", "0"))
            if time.time() - ts < MP_PROBE_CACHE_MAX_AGE_S:
                def _parse(v: str) -> ProbeState:
                    return _STR_TO_PROBE.get(v) or _EMOJI_TO_PROBE.get(v, ProbeState.UNKNOWN)
                return _parse(data.get("candidate_status", "")), _parse(data.get("current_status", ""))
        except (OSError, ValueError):
            pass

    return ProbeState.UNKNOWN, ProbeState.UNKNOWN


def mp_probe(host: str, port: int = 22, timeout: float = 3.0) -> str:
    """Fast TCP reachability probe for multiplayer status."""
    if not host:
        return "skip"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "ok"
    except OSError:
        return "fail"


def write_mp_probe_cache(candidate_status: ProbeState, current_status: ProbeState) -> None:
    cache_file = RUN_DIR / "mp-probe-cache"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        "\n".join(
            [
                f"last_check_ts={time.time()}",
                f"candidate_status={candidate_status.value}",
                f"current_status={current_status.value}",
            ]
        )
        + "\n"
    )


def _read_server_env() -> dict[str, str]:
    return _server_env.load()


def _binary_state(name: str) -> ProbeState:
    """Return OK (exists) or FAIL (not built) for a .run/ binary."""
    return ProbeState.OK if (RUN_DIR / name).is_file() else ProbeState.FAIL


def _venv_state() -> ProbeState:
    """Return OK (set up) or WARN (not yet set up) for the venv."""
    return ProbeState.OK if _venv_ok() else ProbeState.WARN


def _worst_support_state(states: list[str]) -> tuple[ProbeState, str]:
    if "deferred_or_unproven" in states:
        return ProbeState.WARN, "deferred"
    if "implemented_with_known_gaps" in states:
        return ProbeState.WARN, "known gaps"
    return ProbeState.OK, ""


def menu_badge(status: str, detail: str) -> str | None:
    """Return a launcher menu badge string for a status/detail pair.

    The launcher uses detail as a noun-key hint so the shared main-menu badge
    contract stays aligned with the front-door thresholds captured in FL-838.
    """

    status = str(status or "").strip().lower()
    detail = str(detail or "").strip().lower()

    if status == "fail":
        if detail in {"build", "binary missing", "missing"}:
            return "[bold red]✗ binary missing[/bold red]"
        if detail in {"down", "join server down", "ws down"}:
            return "[bold red]✗ join server down[/bold red]"
        if detail in {"map missing"}:
            return "[bold red]✗ map missing[/bold red]"
        if detail in {"pipeline", "unsupported branch truth", "pipeline unavailable"}:
            return "[bold red]✗ pipeline unavailable[/bold red]"
        if detail in {"candidate unreachable"}:
            return "[bold red]✗ candidate unreachable[/bold red]"
        return f"[bold red]✗ {detail or 'failure'}[/bold red]"

    if status == "warn":
        if detail in {"setup", "host", "setup incomplete", "not configured"}:
            return "[bold yellow]⚠ not configured[/bold yellow]"
        if detail in {"known gaps", "test stale", "deferred", "no proof"}:
            return None
        return f"[bold yellow]⚠ {detail}[/bold yellow]" if detail else None

    return None


@lru_cache(maxsize=1)
def _root_surface_truth() -> dict[str, tuple[str, str]]:
    tree = _option_tree.option_tree()
    menus = {menu["id"]: menu for menu in tree.get("menus", [])}
    main = menus.get(tree.get("root_menu", "main"), {})

    def collect_item_states(item: dict) -> list[str]:
        states: list[str] = []
        support_state = item.get("support_state")
        if support_state:
            states.append(support_state)
        submenu = item.get("submenu_id")
        if isinstance(submenu, str):
            for child_id in submenu.split("|"):
                states.extend(collect_menu_states(child_id))
        return states

    def collect_menu_states(menu_id: str) -> list[str]:
        menu = menus.get(menu_id)
        if not menu:
            return []
        states: list[str] = []
        for item in menu.get("items", []):
            states.extend(collect_item_states(item))
        return states

    truth: dict[str, tuple[str, str]] = {}
    for item in main.get("items", []):
        truth[str(item.get("key", ""))] = _worst_support_state(collect_item_states(item))
    return truth


def _game_surface_state() -> tuple[ProbeState, str]:
    game_state = _binary_state("game")
    default_map = ASSET_DIR / "a3d" / "game_map_y8.a3d"
    if not default_map.is_file():
        return ProbeState.FAIL, "map missing"
    if game_state != ProbeState.OK:
        return game_state, "build"
    return game_state, ""


def _multiplayer_surface_state(candidate: ProbeState, current: ProbeState) -> tuple[ProbeState, str]:
    if candidate == ProbeState.NOT_CONFIGURED or current == ProbeState.NOT_CONFIGURED:
        return ProbeState.NOT_CONFIGURED, "setup"
    if ProbeState.FAIL in (candidate, current):
        return ProbeState.FAIL, "down"
    if ProbeState.UNKNOWN in (candidate, current):
        return ProbeState.WARN, "probe"
    return ProbeState.OK, "live"


def _read_full_health_cache() -> dict[str, object] | None:
    if not FULL_HEALTH_CACHE.exists():
        return None
    try:
        data = json.loads(FULL_HEALTH_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ts = float(data.get("ts", 0.0))
    if ts <= 0.0 or (time.time() - ts) > FULL_HEALTH_CACHE_MAX_AGE_S:
        return None
    signals = data.get("signals")
    if not isinstance(signals, dict):
        return None
    return data




def _cache_signal(snapshot: dict[str, object] | None, name: str) -> tuple[str, str] | None:
    if not snapshot:
        return None
    signals = snapshot.get("signals")
    if not isinstance(signals, dict):
        return None
    payload = signals.get(name)
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status", "warn"))
    detail = str(payload.get("detail", ""))
    return status, detail


@dataclass
class StatusBar:
    game: ProbeState
    server: ProbeState       # .run/server — native C++ game server
    venv: ProbeState
    mp_candidate: ProbeState
    mp_current: ProbeState
    branch: str
    game_detail: str = ""
    map_tools: ProbeState = ProbeState.OK
    map_detail: str = ""
    multiplayer: ProbeState = ProbeState.WARN
    multiplayer_detail: str = ""
    asset_pipeline: ProbeState = ProbeState.OK
    asset_detail: str = ""
    staleness: Literal["fresh", "recent", "stale", "unknown"] = "unknown"

    @staticmethod
    def _status_icon(status: ProbeState) -> str:
        icons: dict[ProbeState, str] = {
            ProbeState.OK:             "[bold green]✓[/bold green]",
            ProbeState.WARN:           "[bold yellow]⚠[/bold yellow]",
            ProbeState.FAIL:           "[bold red]✗[/bold red]",
            ProbeState.NOT_CONFIGURED: "[dim]⚪[/dim]",
            ProbeState.UNKNOWN:        "[bold yellow]⚠[/bold yellow]",
            ProbeState.SKIP:           "[dim]—[/dim]",
        }
        return icons.get(status, icons[ProbeState.WARN])

    def _surface_part(self, label: str, status: ProbeState, detail: str) -> str:
        suffix = f" {detail}" if detail else ""
        return f"{label} {self._status_icon(status)}{suffix}"

    def render(self) -> str:
        g = self._status_icon(self.game)
        s = self._status_icon(self.server)
        v = self._status_icon(self.venv)
        stale_badge = {
            "fresh":   "",
            "recent":  "  [dim yellow](recent)[/dim yellow]",
            "stale":   "  [yellow]⚠ stale[/yellow]",
            "unknown": "",
        }.get(self.staleness, "")
        mp_cand = PROBE_ICONS.get(self.mp_candidate, "?")
        mp_curr = PROBE_ICONS.get(self.mp_current, "?")
        return (
            f"game {g}  svr {s}  venv {v}  "
            f"mp: staging{mp_cand} live{mp_curr}{stale_badge}  "  # FL-1352: label slots
            f"[dim]branch:{self.branch}[/dim]"
        )

    def render_front_door(self) -> str:
        return "front-door  " + "  ".join(
            [
                self._surface_part("GAME", self.game, self.game_detail),
                self._surface_part("MAP", self.map_tools, self.map_detail),
                self._surface_part("MP", self.multiplayer, self.multiplayer_detail),
                self._surface_part("ASSET", self.asset_pipeline, self.asset_detail),
            ]
        )


def _fast_venv_status(snapshot: dict[str, object] | None) -> ProbeState:
    cached = _cache_signal(snapshot, "Python venv")
    if cached is not None:
        return _STR_TO_PROBE.get(cached[0], ProbeState.WARN)
    return _STR_TO_PROBE.get(_check_venv_full().status, ProbeState.WARN)


def _health_cache_age_s() -> float | None:
    """Seconds since the full health cache was written, or None if no cache."""
    if not FULL_HEALTH_CACHE.exists():
        return None
    try:
        data = json.loads(FULL_HEALTH_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    ts = float(data.get("ts", 0.0))
    if ts <= 0.0:
        return None
    return time.time() - ts


def _compute_staleness() -> Literal["fresh", "recent", "stale", "unknown"]:
    age = _health_cache_age_s()
    if age is None:
        return "unknown"
    if age < PROBE_FRESH_S:
        return "fresh"
    if age < PROBE_RECENT_S:
        return "recent"
    return "stale"


def fast_probes() -> StatusBar:
    snapshot = _read_full_health_cache()
    cand, curr = _mp_probe_states()
    game_state, game_detail = _game_surface_state()
    mp_state, mp_detail = _multiplayer_surface_state(cand, curr)
    root_truth = _root_surface_truth()
    map_state, map_detail = root_truth.get("2", (ProbeState.OK, ""))
    asset_state, asset_detail = root_truth.get("5", (ProbeState.OK, ""))

    staleness = _compute_staleness()

    # Downgrade MP icons when cached data is too old to trust
    if staleness in ("stale", "unknown"):
        cand = ProbeState.UNKNOWN
        curr = ProbeState.UNKNOWN

    return StatusBar(
        game=game_state,
        server=_binary_state("server"),
        venv=_fast_venv_status(snapshot),
        mp_candidate=cand,
        mp_current=curr,
        branch=_git_branch(),
        game_detail=game_detail,
        map_tools=map_state,
        map_detail=map_detail,
        multiplayer=mp_state,
        multiplayer_detail=mp_detail,
        asset_pipeline=asset_state,
        asset_detail=asset_detail,
        staleness=staleness,
    )


# ---------------------------------------------------------------------------
# Level 2 — full 14-signal health table
# ---------------------------------------------------------------------------

@dataclass
class HealthSignal:
    name: str
    description: str
    status: str   # "ok" | "fail" | "warn" | "skip"
    detail: str = ""


def _slot_prefix(slot: str) -> str:
    return f"AK_MP_SERVER_{slot.upper()}"


def _slot_targets(slot: str) -> list[tuple[bool, str, int]]:
    env = _read_server_env()
    prefix = _slot_prefix(slot)
    domain = env.get(f"{prefix}_DOMAIN", "").strip()
    host = env.get(f"{prefix}_HOST", "").strip()
    raw_port = env.get(f"{prefix}_PORT", "").strip()
    default_port = 8080 if slot == "candidate" else 8081
    try:
        direct_port = int(raw_port) if raw_port else default_port
    except ValueError:
        direct_port = default_port

    targets: list[tuple[bool, str, int]] = []
    seen: set[tuple[bool, str, int]] = set()

    def push(tls: bool, addr: str, port: int) -> None:
        key = (tls, addr, port)
        if not addr or key in seen:
            return
        seen.add(key)
        targets.append(key)

    if domain:
        push(True, domain, 443)
        push(False, domain, 80)
    if host:
        push(False, host, direct_port)
    return targets


def _probe_health_endpoint(host: str, port: int, *, tls: bool, timeout: float = 6.0) -> tuple[str, str, dict[str, object] | None]:
    """Probe the game server's /health endpoint.

    Returns (status, detail, data) where:
    - status is "ok" / "warn" / "fail"
    - detail is a human-readable description
    - data is the parsed JSON dict on success, or None

    WHY: The game server's built-in /health endpoint is the single
    authoritative health owner (FL-2416 fix).  It replaces the old
    nginx static-200 stub that returned 200 even when the game
    process was dead.  Returns JSON fields:
      process_alive, tick_rate_hz, connected_players, memory_mb,
      uptime_seconds.
    Returns 503 (Service Unavailable) when the game server process
    is dead or the tick loop is not running.
    """
    scheme = "https" if tls else "http"
    url = f"{scheme}://{host}:{port}/health"
    try:
        req = Request(url, headers={"User-Agent": "asciicker-testing-launcher/1"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            status_code = getattr(resp, "status", 200)
    except OSError as exc:
        return "fail", f"{url} ({exc})", None
    except ValueError as exc:
        return "fail", f"{url} (invalid URL: {exc})", None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return "warn", f"{url} (non-JSON response: {exc})", None

    if not isinstance(data, dict):
        return "warn", f"{url} (response not a dict)", None

    alive = data.get("process_alive", False)
    players = data.get("connected_players", -1)
    uptime = data.get("uptime_seconds", -1)
    mem = data.get("memory_mb", -1)

    if 200 <= status_code < 300 and alive:
        detail = (
            f"players={players} "
            f"uptime={int(uptime)}s "
            f"mem={mem}MB"
        )
        return "ok", detail, data

    detail = f"503 (process_alive={alive}, players={players})"
    return "fail", f"{url} {detail}", data


def _probe_ws_upgrade(host: str, port: int, *, tls: bool, timeout: float = 3.0) -> tuple[str, str]:
    scheme = "wss" if tls else "ws"
    target = f"{scheme}://{host}:{port}/ws/y8/"
    headers = [
        "GET /ws/y8/ HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode('ascii')}",
        "Sec-WebSocket-Version: 13",
        "",
        "",
    ]
    payload = "\r\n".join(headers).encode("ascii")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            conn = sock
            if tls:
                context = ssl.create_default_context()
                conn = context.wrap_socket(sock, server_hostname=host)
            conn.sendall(payload)
            response = conn.recv(512).decode("ascii", errors="replace")
    except (OSError, ssl.SSLError) as exc:
        return "fail", f"{target} ({exc})"

    status_line = response.splitlines()[0].strip() if response else "no response"
    if " 101 " in f" {status_line} ":
        return "ok", target
    return "fail", f"{target} ({status_line})"


def _check_mp_ws_slot(slot: str) -> HealthSignal:
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    prefix = _slot_prefix(slot)

    if not topology or topology == "none":
        return HealthSignal(f"MP {slot} WS", "/ws/y8/ upgrade", "skip", "not configured — run launcher → Settings to configure multiplayer")
    if not env.get(f"{prefix}_HOST", "").strip() and not env.get(f"{prefix}_DOMAIN", "").strip():
        return HealthSignal(f"MP {slot} WS", "/ws/y8/ upgrade", "warn", f"Host or domain not set for {slot} slot — run launcher → Settings → Multiplayer")

    attempts: list[str] = []
    for tls, addr, port in _slot_targets(slot):
        status, detail = _probe_ws_upgrade(addr, port, tls=tls)
        if status == "ok":
            return HealthSignal(f"MP {slot} WS", "/ws/y8/ upgrade", "ok", detail)
        attempts.append(detail)

    summary = attempts[0] if attempts else "no probe target"
    return HealthSignal(f"MP {slot} WS", "/ws/y8/ upgrade", "fail", summary)


def _check_mp_health_slot(slot: str) -> HealthSignal:
    """Probe the game server's authoritative /health endpoint for a slot.

    This is the single authoritative health owner (FL-2416 fix).  The old
    nginx static-200 stub was retired in the same pass that added this
    probe — there is no second nginx-only health truth path remaining.

    Returns:
      ok:  200 + process_alive=true  (game server is running)
      fail: 503 or no response        (game server is dead or unreachable)
      skip: slot not configured
    """
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    prefix = _slot_prefix(slot)
    host = env.get(f"{prefix}_HOST", "").strip()
    domain = env.get(f"{prefix}_DOMAIN", "").strip()

    if not topology or topology == "none":
        return HealthSignal(f"MP {slot} health", "/health JSON", "skip", "not configured")
    if not host and not domain:
        return HealthSignal(f"MP {slot} health", "/health JSON", "warn", f"Host or domain not set for {slot} slot")

    attempts: list[str] = []
    for tls, addr, port in _slot_targets(slot):
        status, detail, data = _probe_health_endpoint(addr, port, tls=tls)
        if status == "ok":
            return HealthSignal(f"MP {slot} health", "/health JSON", "ok", detail)
        attempts.append(detail)

    summary = attempts[0] if attempts else "no probe target"
    return HealthSignal(f"MP {slot} health", "/health JSON", "fail", summary)


def _format_age(age_seconds: float) -> str:
    if age_seconds < 3600:
        return f"{int(age_seconds // 60)}m"
    return f"{int(age_seconds // 3600)}h"


def _check_runtime_proof() -> HealthSignal:
    audit_path = RUN_DIR / "watchdog_trust_audit" / "latest.json"
    if not audit_path.exists():
        return HealthSignal("Watchdog proof", "watchdog trust audit", "warn", "No watchdog trust audit found — run a watchdog Full Run to generate one")

    try:
        data = json.loads(audit_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return HealthSignal("Watchdog proof", "watchdog trust audit", "fail", f"Trust audit file is corrupt or unreadable ({exc}) — delete .run/watchdog_trust_audit/ and re-run watchdog")

    ts_raw = str(data.get("ts_utc", "")).strip()
    if not ts_raw:
        return HealthSignal("Watchdog proof", "watchdog trust audit", "fail", "missing ts_utc")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return HealthSignal("Watchdog proof", "watchdog trust audit", "fail", f"bad ts_utc: {ts_raw}")

    runtime_gate = next(
        (gate for gate in data.get("gates", []) if isinstance(gate, dict) and gate.get("id") == "runtime_smoke"),
        None,
    )
    if runtime_gate is None:
        return HealthSignal("Watchdog proof", "watchdog trust audit", "fail", "runtime_smoke gate missing")

    age_seconds = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    run_id = ""
    for note in runtime_gate.get("notes", []) or []:
        if isinstance(note, str) and note.startswith("runtime smoke run_id: "):
            run_id = note.split(": ", 1)[1]
            break
    detail_prefix = run_id or "latest trust audit"

    if runtime_gate.get("verdict") != "PASS":
        return HealthSignal("Watchdog proof", "watchdog trust audit", "fail", f"{detail_prefix} runtime_smoke={runtime_gate.get('verdict')}")
    if data.get("overall_verdict") != "PASS" or not data.get("trustworthy_for_gameplay_debugging", False):
        verdict = data.get("overall_verdict", "?")
        return HealthSignal("Watchdog proof", "watchdog trust audit", "fail", f"{detail_prefix} overall={verdict}")
    if age_seconds > RUNTIME_PROOF_MAX_AGE_S:
        return HealthSignal("Watchdog proof", "watchdog trust audit", "warn", f"{detail_prefix} stale {_format_age(age_seconds)}")
    return HealthSignal("Watchdog proof", "watchdog trust audit", "ok", f"{detail_prefix} {_format_age(age_seconds)} old")


def _check_binary_signal(bin_name: str, label: str) -> HealthSignal:
    path = RUN_DIR / bin_name
    if not path.is_file():
        return HealthSignal(label, f".run/{bin_name}", "fail", "not found — run: make " + bin_name.replace("asciiid", "editor").replace("game_term", "terminal"))
    fresh = _binary_fresh(bin_name)
    stale_detail = (f"binary may be stale (source newer) — run: make {bin_name}"
                    .replace("asciiid", "editor").replace("game_term", "terminal") + " to rebuild")
    return HealthSignal(label, f".run/{bin_name}", "ok" if fresh else "warn",
                        "" if fresh else stale_detail)


def _check_node() -> HealthSignal:
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return HealthSignal("Node.js", "node in PATH", "ok", r.stdout.strip())
        return HealthSignal("Node.js", "node in PATH", "fail", "not found")
    except FileNotFoundError:
        return HealthSignal("Node.js", "node in PATH", "fail", "not found — install via nvm or brew")


def _check_playwright() -> HealthSignal:
    try:
        r = subprocess.run(["playwright", "--version"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return HealthSignal("Playwright", "playwright CLI", "ok", r.stdout.strip())
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return HealthSignal("Playwright", "playwright CLI", "warn", "timed out checking playwright")
    # Also check via npx
    try:
        r = subprocess.run(["npx", "playwright", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return HealthSignal("Playwright", "playwright CLI", "ok", r.stdout.strip())
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return HealthSignal("Playwright", "playwright CLI", "warn", "timed out checking npx playwright")
    return HealthSignal("Playwright", "playwright CLI", "fail", "not found — run: npx playwright install")


def _check_emscripten() -> HealthSignal:
    try:
        r = subprocess.run(["emcc", "--version"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            ver = r.stdout.splitlines()[0] if r.stdout else "?"
            return HealthSignal("Emscripten", "emcc in PATH", "ok", ver)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return HealthSignal("Emscripten", "emcc in PATH", "warn", "timed out checking emcc")
    emsdk_env = Path.home() / "emsdk" / "emsdk_env.sh"
    if emsdk_env.exists():
        return HealthSignal("Emscripten", "emcc in PATH", "warn", "emsdk found but not activated — source ~/emsdk/emsdk_env.sh")
    return HealthSignal("Emscripten", "emcc in PATH", "skip", "not installed — optional (make web)")


def _check_venv_full() -> HealthSignal:
    candidates = [
        REPO_ROOT / ".venv" / "bin" / "python3",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    venv_python = next((candidate for candidate in candidates if candidate.exists()), None)
    if venv_python is None:
        return HealthSignal("Python venv", ".venv/", "fail", "not found — run: make setup")
    try:
        r = subprocess.run(
            [str(venv_python), "-c", "import rich; import playwright; print('ok')"],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return HealthSignal("Python venv", ".venv/", "warn", "timed out checking venv imports")
    if r.returncode == 0:
        return HealthSignal("Python venv", ".venv/", "ok", "rich + playwright importable")
    missing = []
    for pkg in ("rich", "playwright"):
        try:
            r2 = subprocess.run([str(venv_python), "-c", f"import {pkg}"], capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            missing.append(f"{pkg}(timeout)")
            continue
        if r2.returncode != 0:
            missing.append(pkg)
    return HealthSignal("Python venv", ".venv/", "warn", f"missing: {', '.join(missing)} — run: make setup")


def _check_blender_addons() -> HealthSignal:
    status = _blender_paths.probe()
    if not status.blender_path:
        return HealthSignal("Blender addons", "addon probe", "warn", "Blender not found — install from blender.org (optional; needed for OSM map import)")
    if not status.version or not status.addon_dir:
        return HealthSignal("Blender addons", "addon probe", "warn", status.detail or "Could not detect Blender version or addon directory — check Blender installation")

    missing = [name for name, present in status.addons.items() if not present]
    if not missing:
        return HealthSignal("Blender addons", f"addons dir ({status.version})", "ok", str(status.addon_dir))
    return HealthSignal("Blender addons", f"addons dir ({status.version})", "warn",
                        f"missing: {', '.join(missing)} — run: python3 scripts/setup_addon.py")


def _check_mcp_servers() -> HealthSignal:
    mcp_dir = REPO_ROOT / "docs/agent/mcp"
    servers = sorted(mcp_dir.glob("*.py")) if mcp_dir.exists() else []
    if not servers:
        return HealthSignal("MCP servers", "mounted/unmounted status", "skip", "no docs/agent/mcp/*.py scripts found")
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return HealthSignal("MCP servers", "mounted/unmounted status", "warn", "Could not check running processes (OS error) — MCP mount status unknown")
    if result.returncode != 0:
        return HealthSignal("MCP servers", "mounted/unmounted status", "warn", "Process list check failed — MCP mount status unknown")
    lines = result.stdout.splitlines()
    mounted = []
    for script in servers:
        script_text = str(script)
        rel_text = str(script.relative_to(REPO_ROOT))
        if any(script_text in line or rel_text in line for line in lines):
            mounted.append(script.name)
    if mounted:
        return HealthSignal(
            "MCP servers",
            "mounted/unmounted status",
            "ok",
            f"mounted {len(mounted)}/{len(servers)}: {', '.join(mounted)}",
        )
    return HealthSignal("MCP servers", "mounted/unmounted status", "warn", f"No MCP servers mounted ({len(servers)} script(s) available in docs/agent/mcp/) — launch your AI assistant to mount them")


def _check_mp_ssh_slot(slot: str) -> HealthSignal:
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    prefix = _slot_prefix(slot)
    host = env.get(f"{prefix}_HOST", "").strip()
    if not topology or topology == "none":
        return HealthSignal(f"MP {slot} TCP/22", "ssh tcp liveness", "skip", "not configured")
    if not host:
        return HealthSignal(f"MP {slot} TCP/22", "ssh tcp liveness", "warn", f"Host not set for {slot} slot — run launcher → Settings → Multiplayer to configure")
    status = mp_probe(host, 22)
    detail = f"{host}:22" if status == "ok" else f"{host}:22 — VPS may be down or firewall blocking port 22. Run launcher → Settings → Multiplayer to verify host config."
    return HealthSignal(f"MP {slot} TCP/22", "ssh tcp liveness", "ok" if status == "ok" else "fail", detail)


def _check_mp_https_manifest_slot(slot: str) -> HealthSignal:
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    prefix = _slot_prefix(slot)
    domain = env.get(f"{prefix}_DOMAIN", "").strip()
    if not topology or topology == "none":
        return HealthSignal(f"MP {slot} HTTPS", "slot_manifest.json", "skip", "not configured")
    if not domain:
        return HealthSignal(f"MP {slot} HTTPS", "slot_manifest.json", "skip", "domain not set")
    url = f"https://{domain}/slot_manifest.json"
    try:
        request = Request(url, headers={"User-Agent": "asciicker-testing-launcher/1"})
        with urlopen(request, timeout=3.0) as response:
            status = getattr(response, "status", 0)
            response.read(1)
    except OSError as exc:
        return HealthSignal(f"MP {slot} HTTPS", "slot_manifest.json", "fail", f"{url} ({exc})")
    if 200 <= int(status) < 300:
        return HealthSignal(f"MP {slot} HTTPS", "slot_manifest.json", "ok", url)
    return HealthSignal(f"MP {slot} HTTPS", "slot_manifest.json", "fail", f"{url} HTTP {status}")


def _check_bundle_parity() -> HealthSignal:
    try:
        from scripts import bundle_hash_status

        status = bundle_hash_status.collect_status()
    except Exception as exc:
        return HealthSignal("Bundle parity", "current/staging/web/slot hashes", "warn", f"unavailable: {exc}")
    bundle_hashes = status.get("bundle_hashes") or []
    ids_lock_hashes = status.get("ids_lock_hashes") or []
    detail = (
        f"bundle_hash={','.join(bundle_hashes) if bundle_hashes else '-'} "
        f"ids_lock_hash={','.join(ids_lock_hashes) if ids_lock_hashes else '-'}"
    )
    if status.get("missing"):
        detail += f" missing={','.join(status['missing'])}"
    if status.get("absent"):
        detail += f" absent={','.join(status['absent'])}"
    return HealthSignal("Bundle parity", "current/staging/web/slot hashes", "ok" if status.get("ok") else "fail", detail)


def _check_mp_topology() -> HealthSignal:
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    valid = {"none", "full-local", "single-vps", "hybrid", "two-machine"}
    if not topology:
        return HealthSignal("MP topology", "AK_MP_SERVER_TOPOLOGY_TYPE", "skip", "not configured — run launcher → [3] Multiplayer")
    if topology not in valid:
        return HealthSignal("MP topology", "AK_MP_SERVER_TOPOLOGY_TYPE", "fail", f"Unknown topology value: {topology!r}. Valid values: none, full-local, single-vps, hybrid, two-machine. Run launcher → Settings to fix.")
    if topology == "none":
        return HealthSignal("MP topology", "AK_MP_SERVER_TOPOLOGY_TYPE", "skip", "multiplayer disabled (none)")
    return HealthSignal("MP topology", "AK_MP_SERVER_TOPOLOGY_TYPE", "ok", topology)


def _check_mp_slot(slot: str, host_key: str) -> HealthSignal:
    env = _read_server_env()
    topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "")
    if not topology or topology == "none":
        return HealthSignal(f"MP {slot}", f"{host_key}", "skip", "not configured")
    host = env.get(host_key, "")
    if not host:
        return HealthSignal(f"MP {slot}", host_key, "warn", "host not set — run: launcher → [3]")
    return HealthSignal(f"MP {slot}", host, "ok", "(live probe available in launcher → [3])")


def _check_asciicker_conf() -> HealthSignal:
    conf = _local_config.load()
    conf_path = REPO_ROOT / ".asciicker.conf"
    if not conf_path.exists():
        return HealthSignal(".asciicker.conf", "project config", "warn",
                            "not found — will be created by make setup")
    asset_dir = conf.get("ASSET_DIR", "")
    if asset_dir and Path(asset_dir).exists():
        return HealthSignal(".asciicker.conf", "project config", "ok", f"ASSET_DIR={asset_dir}")
    return HealthSignal(".asciicker.conf", "project config", "warn", "ASSET_DIR in .asciicker.conf is missing or points to a non-existent directory — run: make setup")


def full_health_check() -> list[HealthSignal]:
    """Run all health probes. Network probes run concurrently to avoid 20s+ freezes (FL-1834)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # --- Fast probes: local filesystem / instant checks (run sequentially) ---
    fast_signals = [
        _check_binary_signal("game",     "Game binary"),
        _check_binary_signal("asciiid",  "Editor binary"),
        _check_binary_signal("server",   "Server binary"),
        HealthSignal("Web build", ".web/index.html",
                     "ok" if (REPO_ROOT / ".web" / "index.html").exists() else "fail",
                     "" if (REPO_ROOT / ".web" / "index.html").exists() else "not built — run: make web"),
        _check_emscripten(),
        _check_blender_addons(),
        _check_runtime_proof(),
        _check_mp_topology(),
        _check_asciicker_conf(),
    ]

    # --- Slow probes: network I/O + subprocess calls (run concurrently) ---
    # Each has a 2-5s timeout; sequential worst case is ~30s. Concurrent: ~5s max.
    slow_checks: list[tuple[int, object]] = [
        (0, _check_venv_full),
        (1, _check_node),
        (2, _check_playwright),
        (3, _check_mcp_servers),
        (4, _check_bundle_parity),
        (5, lambda: _check_mp_ssh_slot("candidate")),
        (6, lambda: _check_mp_ssh_slot("current")),
        (7, lambda: _check_mp_health_slot("candidate")),
        (8, lambda: _check_mp_health_slot("current")),
        (9, lambda: _check_mp_ws_slot("candidate")),
        (10, lambda: _check_mp_ws_slot("current")),
        (11, lambda: _check_mp_https_manifest_slot("candidate")),
        (12, lambda: _check_mp_https_manifest_slot("current")),
    ]

    slow_results: dict[int, HealthSignal] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_to_idx = {pool.submit(fn): idx for idx, fn in slow_checks}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                slow_results[idx] = future.result()
            except Exception as exc:
                # Defensive: if a probe crashes, record it as a warning instead of killing the check
                slow_results[idx] = HealthSignal("probe error", str(exc), "warn", str(exc))

    slow_signals = [slow_results[i] for i in range(len(slow_checks))]

    signals = fast_signals + slow_signals
    _write_full_health_cache(signals)
    return signals


def _write_full_health_cache(signals: list[HealthSignal]) -> None:
    payload = {
        "ts": time.time(),
        "signals": {
            signal.name: {
                "status": signal.status,
                "detail": signal.detail,
            }
            for signal in signals
        },
    }
    try:
        FULL_HEALTH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        FULL_HEALTH_CACHE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def full_health_check_json() -> list[dict[str, str]]:
    return [asdict(signal) for signal in full_health_check()]
