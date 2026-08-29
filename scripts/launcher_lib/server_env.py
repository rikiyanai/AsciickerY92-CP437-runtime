"""server_env.py — Read/write deploy/server.env (Schema B).

Schema B fields:
  AK_MP_SERVER_TOPOLOGY_TYPE      none|full-local|single-vps|hybrid|two-machine
  AK_MP_SERVER_CANDIDATE_HOST     IP or hostname
  AK_MP_SERVER_CANDIDATE_SSH_USER SSH user (default: ubuntu)
  AK_MP_SERVER_CANDIDATE_SSH_KEY  path to passwordless private key
  AK_MP_SERVER_CANDIDATE_DOMAIN   domain (optional, for TLS)
  AK_MP_SERVER_CANDIDATE_PORT     port (default: 8080)
  AK_MP_SERVER_CURRENT_HOST
  AK_MP_SERVER_CURRENT_SSH_USER
  AK_MP_SERVER_CURRENT_SSH_KEY
  AK_MP_SERVER_CURRENT_DOMAIN
  AK_MP_SERVER_CURRENT_PORT       port (default: 8081)
  AK_MP_SERVER_DNS_PROVIDER       none|cloudflare
  AK_MP_SERVER_DNS_API_TOKEN      API token (if DNS_PROVIDER != none)
  PLAYWRIGHT_VIEWPORT             mobile viewport as WIDTHxHEIGHT
  PLAYWRIGHT_DURATION             hold-open duration in seconds
  PLAYWRIGHT_BROWSER_ENGINE       chromium|webkit|firefox
  PLAYWRIGHT_DEVICE               mobile device label
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT  = Path(__file__).parent.parent.parent.resolve()
SERVER_ENV = REPO_ROOT / "deploy" / "server.env"

SCHEMA_FIELDS = [
    "AK_MP_SERVER_TOPOLOGY_TYPE",
    "AK_MP_SERVER_CANDIDATE_HOST",
    "AK_MP_SERVER_CANDIDATE_SSH_USER",
    "AK_MP_SERVER_CANDIDATE_SSH_KEY",
    "AK_MP_SERVER_CANDIDATE_DOMAIN",
    "AK_MP_SERVER_CANDIDATE_PORT",
    "AK_MP_SERVER_CURRENT_HOST",
    "AK_MP_SERVER_CURRENT_SSH_USER",
    "AK_MP_SERVER_CURRENT_SSH_KEY",
    "AK_MP_SERVER_CURRENT_DOMAIN",
    "AK_MP_SERVER_CURRENT_PORT",
    "AK_MP_SERVER_DNS_PROVIDER",
    "AK_MP_SERVER_DNS_API_TOKEN",
    "PLAYWRIGHT_VIEWPORT",
    "PLAYWRIGHT_DURATION",
    "PLAYWRIGHT_BROWSER_ENGINE",
    "PLAYWRIGHT_DEVICE",
]

VALID_TOPOLOGIES = {"none", "full-local", "single-vps", "hybrid", "two-machine"}
HIDDEN_FIELDS    = {"AK_MP_SERVER_DNS_API_TOKEN"}
_DOMAIN_RE       = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$"
)
_HOST_LABEL_RE   = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")
_METACHAR_RE     = re.compile(r'[;\n$`"\'\\]')
_SSH_USER_RE     = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,31}$")
_VIEWPORT_RE     = re.compile(r"^(?P<width>\d{2,4})x(?P<height>\d{2,4})$")


def _git_info_exclude_path() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (REPO_ROOT / git_dir).resolve()
    return git_dir / "info" / "exclude"


def _ensure_git_ignored(path: Path) -> None:
    rel = str(path.relative_to(REPO_ROOT))
    check = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", rel],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if check.returncode == 0:
        return

    exclude_path = _git_info_exclude_path()
    if exclude_path is not None:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        try:
            if exclude_path.exists():
                existing = exclude_path.read_text()
            if rel not in {line.strip() for line in existing.splitlines()}:
                with exclude_path.open("a", encoding="utf-8") as handle:
                    if existing and not existing.endswith("\n"):
                        handle.write("\n")
                    handle.write(f"{rel}\n")
        except OSError:
            pass

        check = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", rel],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if check.returncode == 0:
            return

    raise RuntimeError(
        f"{rel} is not gitignored; refusing to write credentials.\n"
        f"Add '{rel}' to .gitignore or .git/info/exclude first."
    )


def _canonical_defaults() -> dict[str, str]:
    ssh_key = Path.home() / ".ssh" / "google_compute_engine"
    ssh_key_value = str(ssh_key) if ssh_key.exists() else ""
    return {
        "AK_MP_SERVER_TOPOLOGY_TYPE": "two-machine",
        "AK_MP_SERVER_CANDIDATE_HOST": "192.0.2.10",
        "AK_MP_SERVER_CANDIDATE_SSH_USER": "ubuntu",
        "AK_MP_SERVER_CANDIDATE_SSH_KEY": ssh_key_value,
        "AK_MP_SERVER_CANDIDATE_DOMAIN": "candidate.test",
        "AK_MP_SERVER_CANDIDATE_PORT": "8080",
        "AK_MP_SERVER_CURRENT_HOST": "192.0.2.11",
        "AK_MP_SERVER_CURRENT_SSH_USER": "ubuntu",
        "AK_MP_SERVER_CURRENT_SSH_KEY": ssh_key_value,
        "AK_MP_SERVER_CURRENT_DOMAIN": "current.test",
        "AK_MP_SERVER_CURRENT_PORT": "8081",
        "AK_MP_SERVER_DNS_PROVIDER": "none",
        "AK_MP_SERVER_DNS_API_TOKEN": "",
        "PLAYWRIGHT_VIEWPORT": "",
        "PLAYWRIGHT_DURATION": "",
        "PLAYWRIGHT_BROWSER_ENGINE": "",
        "PLAYWRIGHT_DEVICE": "iPhone 14",
    }


def canonical_defaults() -> dict[str, str]:
    return dict(_canonical_defaults())


def _should_seed_defaults(values: dict[str, str]) -> bool:
    return bool(values) and all(not values.get(key, "") for key in SCHEMA_FIELDS)


def load() -> dict[str, str]:
    """Read server.env into a dict. Returns {} if file missing."""
    if not SERVER_ENV.exists():
        return {}
    result: dict[str, str] = {}
    try:
        raw = SERVER_ENV.read_text()
    except OSError:
        return {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    if _should_seed_defaults(result):
        seeded = canonical_defaults()
        try:
            save(seeded)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return seeded
        return seeded
    return result


def save(fields: dict[str, str]) -> None:
    """Write fields to server.env atomically. Sets file to 0o600.

    Raises RuntimeError if deploy/server.env is not gitignored.
    """
    _ensure_git_ignored(SERVER_ENV)

    tmp = SERVER_ENV.with_name(".server.env.tmp")
    lines = ["# deploy/server.env — multiplayer configuration (DO NOT COMMIT)\n"]
    for key in SCHEMA_FIELDS:
        val = fields.get(key, "")
        lines.append(f"{key}={val}\n")

    try:
        tmp.write_text("".join(lines))
        os.chmod(str(tmp), 0o600)
        tmp.replace(SERVER_ENV)
        os.chmod(str(SERVER_ENV), 0o600)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def validate_field(key: str, value: str) -> str | None:
    """Return an error string if invalid, None if OK."""
    if key == "AK_MP_SERVER_TOPOLOGY_TYPE":
        if value not in VALID_TOPOLOGIES:
            return f"must be one of: {', '.join(sorted(VALID_TOPOLOGIES))}"
    elif key in ("AK_MP_SERVER_CANDIDATE_HOST", "AK_MP_SERVER_CURRENT_HOST"):
        if not value:
            return None
        if value.startswith("-"):
            return "host must not start with '-'"
        if _METACHAR_RE.search(value) or any(ch.isspace() for ch in value):
            return "host contains invalid characters"
        try:
            ipaddress.ip_address(value)
            return None
        except ValueError:
            pass
        labels = value.split(".")
        if not all(_HOST_LABEL_RE.match(label) for label in labels):
            return "not a valid host or IP address"
    elif key in ("AK_MP_SERVER_CANDIDATE_SSH_USER", "AK_MP_SERVER_CURRENT_SSH_USER"):
        if not value:
            return None
        if value.startswith("-"):
            return "ssh user must not start with '-'"
        if _METACHAR_RE.search(value) or any(ch.isspace() for ch in value):
            return "ssh user contains invalid characters"
        if not _SSH_USER_RE.match(value):
            return "ssh user contains invalid characters"
    elif key in ("AK_MP_SERVER_CANDIDATE_SSH_KEY", "AK_MP_SERVER_CURRENT_SSH_KEY"):
        if not value:
            return None  # optional
        p = Path(value).expanduser().resolve()
        if not p.exists():
            return f"file not found: {p}"
        try:
            header = p.read_bytes()[:20].decode("ascii", errors="ignore")
            if "-----BEGIN" not in header:
                return "not a PEM key file (must start with -----BEGIN)"
        except (OSError, UnicodeDecodeError):
            return "could not read key file"
        if oct(p.stat().st_mode)[-3:] not in ("600", "400"):
            return None  # warn-only, not blocking
    elif key in ("AK_MP_SERVER_CANDIDATE_DOMAIN", "AK_MP_SERVER_CURRENT_DOMAIN"):
        if not value:
            return None  # optional
        if _METACHAR_RE.search(value):
            return "domain contains invalid characters"
        if not _DOMAIN_RE.match(value):
            return "not a valid domain name"
    elif key in ("AK_MP_SERVER_CANDIDATE_PORT", "AK_MP_SERVER_CURRENT_PORT"):
        if not value:
            return None  # optional — will use default
        try:
            port = int(value)
            if not (1024 <= port <= 65535):
                raise ValueError
        except ValueError:
            return "must be a number between 1024 and 65535"
    elif key == "PLAYWRIGHT_VIEWPORT":
        if not value:
            return None
        match = _VIEWPORT_RE.match(value.strip().lower())
        if not match:
            return "must be WIDTHxHEIGHT (for example 375x812)"
        width = int(match.group("width"))
        height = int(match.group("height"))
        if width < 100 or height < 100:
            return "viewport must be at least 100x100"
    elif key == "PLAYWRIGHT_DURATION":
        if not value:
            return None
        try:
            seconds = int(value)
            if seconds <= 0:
                raise ValueError
        except ValueError:
            return "must be a positive integer number of seconds"
    elif key == "PLAYWRIGHT_BROWSER_ENGINE":
        if not value:
            return None
        if value not in {"chromium", "webkit", "firefox"}:
            return "must be one of: chromium, firefox, webkit"
    elif key == "PLAYWRIGHT_DEVICE":
        if not value:
            return "is required"
    return None
