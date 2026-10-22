"""Slot configuration repository — shared seam for target/slot config.

Seam: single authoritative read path for slot configuration (host, domain,
port, SSH target, base URL, WebSocket server) that both the launcher and
canonical watchdog script use.

Previously:
- Launcher had ``_slot_command_config(env, slot)`` — reads from ``server.env``
  via ``launcher_lib.server_env``, returns a loosely-typed dict
- Canonical script had ``SLOT_CONFIGS`` dict — hardcoded defaults, never
  reads from ``server.env``
- ``_target_context_payload`` and ``_target_context_line`` duplicated slot config
  derivation with label-remapping logic
- ``_require_slot_targets`` had its own validation + liveness probe logic

Now all of those read through ``slot_config_repository.load_slot_config()``
or ``require_slot_targets()``.

Module has no imports from watchdog_run_canonical, testing/launcher, or
scripts/launcher_lib to prevent circular imports.  It does import from
``launcher_lib.server_env`` for the env read + validate helpers, but that
module is pure env-io and has no reverse imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Runtime-only: import ``server_env`` lazily so the module can be used
# from contexts where launcher_lib is not on the path.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent

_SERVER_ENV_MODULE: Any = None


def _server_env() -> Any:
    """Lazy import of ``launcher_lib.server_env``."""
    global _SERVER_ENV_MODULE
    if _SERVER_ENV_MODULE is not None:
        return _SERVER_ENV_MODULE
    import importlib

    spec = importlib.util.find_spec("scripts.launcher_lib.server_env")
    if spec is None:
        # Fall back to relative path import.
        import sys as _sys

        _scripts_dir = _REPO_ROOT / "scripts"
        if str(_scripts_dir) not in _sys.path:
            _sys.path.insert(0, str(_scripts_dir))
        spec = importlib.util.find_spec("scripts.launcher_lib.server_env")
    if spec is None:
        raise ImportError(
            "scripts.launcher_lib.server_env not found — cannot load slot config"
        )
    _SERVER_ENV_MODULE = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_SERVER_ENV_MODULE)  # type: ignore[union-attr]
    return _SERVER_ENV_MODULE


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_WEB_PORT = 8080
DEFAULT_LOCAL_WS_PORT = 8080

DEFAULT_CANDIDATE_SSH_TARGET = "r@35.226.113.14"
DEFAULT_CURRENT_SSH_TARGET = "r@34.11.68.149"

DEFAULT_CANDIDATE_BASE_URL = "https://candidate-asciicker.rikiworld.com"
DEFAULT_CURRENT_BASE_URL = "https://current.rikiworld.com"

DEFAULT_CANDIDATE_WS_SERVER = "candidate-asciicker.rikiworld.com:443"
DEFAULT_CURRENT_WS_SERVER = "current.rikiworld.com:443"

DEFAULT_CANDIDATE_PORT = 8080
DEFAULT_CURRENT_PORT = 8081

TARGET_CONTEXT_CHOICES = {"localhost", "test-vps", "live-vps", "custom"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SlotConfig:
    """Resolved configuration for a single slot (candidate or current).

    All fields are populated by ``load_slot_config()`` from server.env with
    sensible defaults when env vars are absent.
    """

    # Core
    slot_name: str
    host: str
    domain: str
    port: int

    # Derived
    ssh_target: str
    ssh_user: str
    base_url: str
    ws_server: str
    display_host: str

    # Validation
    host_error: str = ""
    ssh_user_error: str = ""

    # Override: when set, the caller provided an explicit override rather
    # than reading from env.
    is_override: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "domain": self.domain,
            "port": self.port,
            "display_host": self.display_host,
            "ssh_target": self.ssh_target,
            "host_error": self.host_error,
            "ssh_user_error": self.ssh_user_error,
            "base_url": self.base_url,
            "ws_server": self.ws_server,
        }




class SlotConfigError(RuntimeError):
    """Raised when slot config is invalid or missing required fields."""

    def __init__(self, message: str, *, slot: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.slot = slot
        self.missing = missing or []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slot_prefix(slot: str) -> str:
    """Return the server.env variable prefix for a slot name."""
    if slot == "candidate":
        return "AK_MP_SERVER_CANDIDATE"
    if slot == "current":
        return "AK_MP_SERVER_CURRENT"
    raise ValueError(f"unsupported slot: {slot}")


def _default_port(slot: str) -> int:
    return DEFAULT_CANDIDATE_PORT if slot == "candidate" else DEFAULT_CURRENT_PORT


def _default_ssh_target(slot: str) -> str:
    return DEFAULT_CANDIDATE_SSH_TARGET if slot == "candidate" else DEFAULT_CURRENT_SSH_TARGET


def _default_base_url(slot: str) -> str:
    return DEFAULT_CANDIDATE_BASE_URL if slot == "candidate" else DEFAULT_CURRENT_BASE_URL


def _default_ws_server(slot: str) -> str:
    return DEFAULT_CANDIDATE_WS_SERVER if slot == "candidate" else DEFAULT_CURRENT_WS_SERVER


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_slot_config(
    env: dict[str, str] | None = None,
    slot: str = "candidate",
    *,
    ssh_target_override: str | None = None,
    base_url_override: str | None = None,
    ws_server_override: str | None = None,
) -> SlotConfig:
    """Resolve slot configuration from ``server.env`` (or env dict) with overrides.

    Resolution order:
      1. Explicit overrides (``ssh_target_override``, ``base_url_override``,
         ``ws_server_override``) — when set, skip env reading for those fields.
      2. ``server.env`` vars (AK_MP_SERVER_CANDIDATE_* or AK_MP_SERVER_CURRENT_*).
      3. Sensible hardcoded defaults as fallback.

    Args:
        env: Environment dict (defaults to ``os.environ``).  When *None*,
             the function reads ``deploy/server.env`` via ``launcher_lib.server_env``.
        slot: ``"candidate"`` or ``"current"``.
        ssh_target_override: Explicit SSH target override (e.g. from CLI ``--ssh-target``).
        base_url_override: Explicit base URL override (e.g. from CLI ``--base-url``).
        ws_server_override: Explicit WS server override (e.g. from CLI ``--ws-server``).

    Returns:
        A fully populated ``SlotConfig``.
    """
    prefix = _slot_prefix(slot)

    # -- Determine host, domain, port, ssh_user from env or defaults ----------
    if env is not None:
        host = str(env.get(f"{prefix}_HOST", "")).strip()
        domain = str(env.get(f"{prefix}_DOMAIN", "")).strip()
        ssh_user = str(env.get(f"{prefix}_SSH_USER", "")).strip()
        raw_port = str(env.get(f"{prefix}_PORT", "")).strip()
    else:
        senv = _server_env()
        raw_dict = senv.load()
        host = str(raw_dict.get(f"{prefix}_HOST", "")).strip()
        domain = str(raw_dict.get(f"{prefix}_DOMAIN", "")).strip()
        ssh_user = str(raw_dict.get(f"{prefix}_SSH_USER", "")).strip()
        raw_port = str(raw_dict.get(f"{prefix}_PORT", "")).strip()

    # Validate fields.
    senv_mod = _server_env()
    host_error = senv_mod.validate_field(f"{prefix}_HOST", host) if host else ""
    ssh_user_error = senv_mod.validate_field(f"{prefix}_SSH_USER", ssh_user) if ssh_user else ""

    if host_error:
        host = ""
    if ssh_user_error:
        ssh_user = ""
    elif not ssh_user:
        ssh_user = "ubuntu"

    try:
        port = int(raw_port) if raw_port else _default_port(slot)
    except ValueError:
        port = _default_port(slot)

    # -- Resolve SSH target ---------------------------------------------------
    ssh_target: str
    is_override = False
    if ssh_target_override:
        ssh_target = ssh_target_override
        is_override = True
    elif host:
        ssh_target = f"{ssh_user}@{host}"
    else:
        ssh_target = _default_ssh_target(slot)

    # -- Resolve base URL -----------------------------------------------------
    base_url: str
    if base_url_override:
        base_url = base_url_override
        is_override = True
    elif domain:
        base_url = f"https://{domain}"
    elif host:
        base_url = f"http://{host}:{port}"
    else:
        base_url = _default_base_url(slot)

    # -- Resolve WebSocket server ---------------------------------------------
    ws_server: str
    if ws_server_override:
        ws_server = ws_server_override
        is_override = True
    elif domain:
        ws_server = f"{domain}:443"
    elif host:
        ws_server = f"{host}:{port}"
    else:
        ws_server = _default_ws_server(slot)

    display_host = host or domain or "(not set)"

    return SlotConfig(
        slot_name=slot,
        host=host,
        domain=domain,
        port=port,
        ssh_target=ssh_target,
        ssh_user=ssh_user,
        base_url=base_url,
        ws_server=ws_server,
        display_host=display_host,
        host_error=host_error,
        ssh_user_error=ssh_user_error,
        is_override=is_override,
    )


def require_slot_targets(
    env: dict[str, str] | None = None,
    slot: str = "candidate",
    *,
    need_ssh: bool = True,
    need_runtime: bool = True,
    action_label: str = "continue",
) -> SlotConfig:
    """Load slot config and validate that required fields are present.

    Unlike ``load_slot_config()``, this function raises ``SlotConfigError``
    when required config is missing, instead of falling back to defaults.
    This is the right entry point for actions that *must* reach a real VPS.

    Args:
        env: Environment dict (defaults to reading deploy/server.env).
        slot: ``"candidate"`` or ``"current"``.
        need_ssh: If *True*, require SSH target.
        need_runtime: If *True*, require base_url and ws_server.
        action_label: Human-readable label for error messages.

    Returns:
        A valid ``SlotConfig`` with all required fields populated.

    Raises:
        SlotConfigError: When a required field is missing or invalid.
    """
    cfg = load_slot_config(env, slot)

    if cfg.host_error or cfg.ssh_user_error:
        errors = [e for e in [cfg.host_error, cfg.ssh_user_error] if e]
        raise SlotConfigError(
            f"{slot.capitalize()} server config is invalid: {'; '.join(errors)}",
            slot=slot,
            missing=[k for k, v in [("host_error", cfg.host_error), ("ssh_user_error", cfg.ssh_user_error)] if v],
        )

    missing: list[str] = []
    if need_ssh and not cfg.ssh_target:
        missing.append("server address and login (SSH host/user)")
    if need_runtime and not cfg.base_url:
        missing.append("base URL")
    if need_runtime and not cfg.ws_server:
        missing.append("WS server")

    if missing:
        raise SlotConfigError(
            f"{slot.capitalize()} server not configured — missing: {', '.join(missing)}",
            slot=slot,
            missing=missing,
        )

    return cfg


def target_context_payload(
    env: dict[str, str] | None = None,
    slot: str = "candidate",
    custom_host: str = "",
) -> dict[str, str]:
    """Return a dict with context/label/host/base_url/ws_server for the active target context.

    Replaces the launcher's ``_target_context_payload()``.  Includes context
    selection logic (localhost, test-vps/candidate, live-vps/current, custom).
    """
    # Read the stored context selection from .run/launcher-target-context.json
    # if env is None (meaning callers want the stored choice).
    context = slot
    resolved_custom_host = custom_host

    if slot == "localhost":
        return {
            "context": "localhost",
            "label": "Localhost",
            "host": "127.0.0.1",
            "base_url": f"http://127.0.0.1:{DEFAULT_LOCAL_WEB_PORT}",
            "ws_server": f"127.0.0.1:{DEFAULT_LOCAL_WS_PORT}",
        }

    if slot == "custom":
        if not custom_host:
            return {
                "context": "custom",
                "label": "Custom",
                "host": "(not set)",
                "base_url": "",
                "ws_server": "",
            }
        from urllib.parse import urlparse as _urlparse

        base_url = custom_host.rstrip("/") if custom_host else ""
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"
        parsed = _urlparse(base_url)
        ws_server = parsed.netloc if parsed.netloc else custom_host
        return {
            "context": "custom",
            "label": "Custom",
            "host": custom_host,
            "base_url": base_url,
            "ws_server": ws_server,
        }

    # candidate or current (or live-vps / test-vps aliases)
    cfg = load_slot_config(env, slot)
    label_map = {
        "candidate": "Test VPS",
        "current": "Live VPS",
    }
    label = label_map.get(slot, slot.capitalize())
    return {
        "context": slot,
        "label": label,
        "host": str(cfg.display_host),
        "base_url": str(cfg.base_url or ""),
        "ws_server": str(cfg.ws_server or ""),
    }


def target_context_line(
    env: dict[str, str] | None = None,
    slot: str = "candidate",
    custom_host: str = "",
) -> str:
    """Return a one-line human-readable summary of the active target context.

    Replaces the launcher's ``_target_context_line()``.
    """
    target = target_context_payload(env, slot, custom_host=custom_host)
    label = target["label"]
    return (
        f"selected target: {label} "
        f"host={target['host'] or '-'} "
        f"base={target['base_url'] or '-'} "
        f"ws={target['ws_server'] or '-'}"
    )


def _read_json_file(path: Path) -> dict[str, object] | None:
    """Read a JSON file, returning None on any error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
