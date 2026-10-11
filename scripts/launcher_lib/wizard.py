"""Interactive Multiplayer Wizard and Asset Pipeline Wizard for launcher configuration."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable

from scripts.launcher_lib import server_env as _senv

# ---------------------------------------------------------------------------
# Asset Pipeline Wizard — HTTP-backed lifecycle (RQ-022 / FL-1186)
# ---------------------------------------------------------------------------

PIPELINE_SERVER_URL = os.environ.get("PIPELINE_SERVER_URL", "http://localhost:8090")


def _pipeline_get(path: str, timeout: float = 5.0) -> dict:
    """GET a JSON endpoint on the pipeline-v3 server."""
    url = f"{PIPELINE_SERVER_URL}{path}"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _pipeline_post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    """POST a JSON payload to the pipeline-v3 server."""
    url = f"{PIPELINE_SERVER_URL}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _run_asset_pipeline_wizard(console, input_fn: Callable[[], str] = input) -> int:
    """HTTP-backed asset pipeline wizard lifecycle.

    Lifecycle: health-check -> template-select -> POST /pipeline/run -> validate.
    Uses PIPELINE_SERVER_URL env var (default http://localhost:8090).

    Returns 0 on success, 1 on failure.
    """
    console.rule("[bold]Asset Pipeline Wizard[/bold]")
    console.print(f"  Server: {PIPELINE_SERVER_URL}")

    # --- Step 1: Health check ---
    console.print("  Checking pipeline-v3 server health...")
    try:
        health = _pipeline_get("/health")
        status = health.get("status", "unknown")
        console.print(f"  [green]✓[/green] Server healthy (status={status})")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        console.print(f"  [red]✗[/red] Pipeline server unreachable: {exc}")
        console.print(f"  Set PIPELINE_SERVER_URL or start pipeline-v3 at {PIPELINE_SERVER_URL}")
        return 1

    # --- Step 2: Template selection ---
    console.print()
    console.print("  Fetching templates...")
    try:
        templates_resp = _pipeline_get("/pipeline/templates")
        templates = templates_resp.get("templates", [])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        console.print(f"  [red]✗[/red] Failed to fetch templates: {exc}")
        return 1

    if not templates:
        console.print("  [yellow]⚠[/yellow] No templates available on the server.")
    else:
        console.print(f"  Found {len(templates)} template(s):")
        for idx, tpl in enumerate(templates, 1):
            name = tpl.get("name", "unnamed")
            desc = tpl.get("description", "")
            console.print(f"    [{idx}] {name}  {desc}")

    console.print()
    console.print("  Enter template number (or blank for custom): ", end="")
    tpl_choice = input_fn().strip()

    selected_template = None
    if tpl_choice.isdigit() and 1 <= int(tpl_choice) <= len(templates):
        selected_template = templates[int(tpl_choice) - 1]
        console.print(f"  Selected: {selected_template.get('name', 'unnamed')}")
    else:
        console.print("  Custom mode (no template).")

    console.print("  Source path (PNG sprite sheet / file path): ", end="")
    source_path = input_fn().strip()
    if not source_path:
        console.print("  [red]✗[/red] Source path is required.")
        return 1

    console.print("  Asset name (blank for auto): ", end="")
    asset_name = input_fn().strip() or "wizard_asset"

    # --- Step 3: POST /pipeline/run ---
    run_payload: dict = {
        "name": asset_name,
        "source_path": source_path,
        "source_type": "file",
    }
    if selected_template:
        run_payload["template"] = selected_template.get("name")

    console.print()
    console.print("  Submitting pipeline run...")
    try:
        run_result = _pipeline_post("/pipeline/run", run_payload)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        console.print(f"  [red]✗[/red] Pipeline run failed: {exc}")
        return 1

    success = run_result.get("success", False)
    if not success:
        error = run_result.get("error", "unknown error")
        console.print(f"  [red]✗[/red] Pipeline returned failure: {error}")
        return 1

    output_path = run_result.get("output_path", "(unknown)")
    console.print(f"  [green]✓[/green] Pipeline run succeeded — output: {output_path}")

    # --- Step 4: Validate ---
    job_id = run_result.get("job_id")
    if job_id:
        console.print("  Validating output...")
        try:
            val_result = _pipeline_post("/pipeline/validate_xp", {"job_id": job_id})
            if val_result.get("valid"):
                console.print("  [green]✓[/green] Validation passed.")
            else:
                issues = val_result.get("issues", [])
                console.print(f"  [yellow]⚠[/yellow] Validation issues: {issues}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            console.print(f"  [yellow]⚠[/yellow] Validation request failed: {exc}")

    return 0

TOPOLOGY_CHOICES = {
    "1": "none",
    "2": "full-local",
    "3": "single-vps",
    "4": "hybrid",
    "5": "two-machine",
}


def _seeded_prompt_value(env: dict[str, str], seeded_defaults: dict[str, str], key: str, fallback: str = "") -> str:
    current = env.get(key, "")
    if current:
        return current
    seeded = seeded_defaults.get(key, "")
    if seeded:
        return seeded
    return fallback


def _prompt_value(console, label: str, key: str, current: str, input_fn: Callable[[], str]) -> str:
    while True:
        console.print(f"  {label} [{current or 'blank'}]: ", end="", markup=False, highlight=False)
        raw = input_fn().strip()
        value = current if raw == "" else raw
        err = _senv.validate_field(key, value)
        if err:
            console.print(f"  [red]✗[/red]  {err}")
            continue
        return value


def _clear_slot(env: dict[str, str], prefix: str) -> None:
    for suffix in ("HOST", "SSH_USER", "SSH_KEY", "DOMAIN", "PORT"):
        env[f"AK_MP_SERVER_{prefix}_{suffix}"] = ""


def run_vps_wizard(console, input_fn: Callable[[], str] = input) -> dict[str, str]:
    env = _senv.load()
    seeded_defaults = _senv.canonical_defaults() if not env else {}
    current_topology = env.get("AK_MP_SERVER_TOPOLOGY_TYPE", "none") or "none"
    slot_keys = [
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
    ]
    console.rule("[bold]Multiplayer Wizard[/bold]")
    console.print("  Choose a topology:")
    console.print("    [1] none         disable multiplayer config and clear slot details")
    console.print("    [2] full-local   local candidate/current only, no SSH or DNS")
    console.print("    [3] single-vps   one remote host, two ports, shared SSH details")
    console.print("    [4] hybrid       local/current split across mixed hosts")
    console.print("    [5] two-machine  separate candidate/current hosts")
    while True:
        console.print(f"  Topology [{current_topology}]: ", end="")
        choice = input_fn().strip()
        if choice in TOPOLOGY_CHOICES:
            topology = TOPOLOGY_CHOICES[choice]
            break
        if choice == "" and current_topology != "none":
            topology = current_topology
            break
        if choice == "" and any(env.get(key, "") for key in slot_keys):
            console.print("  [yellow]⚠[/yellow]  Topology is currently [bold]none[/bold] with saved slot values. Choose an explicit topology to avoid clearing configuration accidentally.")
            continue
        if choice == "":
            topology = current_topology
            break
        console.print("  [red]✗[/red]  choose [1-5] or press Enter only to keep an already configured topology")
    env["AK_MP_SERVER_TOPOLOGY_TYPE"] = topology

    if topology == "none":
        _clear_slot(env, "CANDIDATE")
        _clear_slot(env, "CURRENT")
        env["AK_MP_SERVER_DNS_PROVIDER"] = "none"
        env["AK_MP_SERVER_DNS_API_TOKEN"] = ""
        _senv.save(env)
        return env

    if topology == "full-local":
        vps_fields = [
            "AK_MP_SERVER_CANDIDATE_HOST",
            "AK_MP_SERVER_CANDIDATE_SSH_USER",
            "AK_MP_SERVER_CANDIDATE_SSH_KEY",
            "AK_MP_SERVER_CANDIDATE_DOMAIN",
            "AK_MP_SERVER_CURRENT_HOST",
            "AK_MP_SERVER_CURRENT_SSH_USER",
            "AK_MP_SERVER_CURRENT_SSH_KEY",
            "AK_MP_SERVER_CURRENT_DOMAIN",
        ]
        if any(env.get(key, "").strip() for key in vps_fields):
            console.print("  This will erase your current VPS credentials — continue? [y/N]: ", end="")
            if input_fn().strip().lower() != "y":
                env["AK_MP_SERVER_TOPOLOGY_TYPE"] = current_topology
                return env
        _clear_slot(env, "CANDIDATE")
        _clear_slot(env, "CURRENT")
        env["AK_MP_SERVER_CANDIDATE_PORT"] = _prompt_value(
            console, "Candidate port", "AK_MP_SERVER_CANDIDATE_PORT",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CANDIDATE_PORT", "8080"), input_fn,
        )
        env["AK_MP_SERVER_CURRENT_PORT"] = _prompt_value(
            console, "Current port", "AK_MP_SERVER_CURRENT_PORT",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CURRENT_PORT", "8081"), input_fn,
        )
        env["AK_MP_SERVER_DNS_PROVIDER"] = "none"
        env["AK_MP_SERVER_DNS_API_TOKEN"] = ""
        _senv.save(env)
        return env

    env["AK_MP_SERVER_CANDIDATE_HOST"] = _prompt_value(
        console, "Candidate host", "AK_MP_SERVER_CANDIDATE_HOST",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CANDIDATE_HOST"), input_fn,
    )
    env["AK_MP_SERVER_CANDIDATE_SSH_USER"] = _prompt_value(
        console, "Candidate SSH user", "AK_MP_SERVER_CANDIDATE_SSH_USER",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CANDIDATE_SSH_USER", "ubuntu"), input_fn,
    )
    env["AK_MP_SERVER_CANDIDATE_SSH_KEY"] = _prompt_value(
        console, "Candidate SSH key path (e.g. ~/.ssh/id_rsa, blank = use default)", "AK_MP_SERVER_CANDIDATE_SSH_KEY",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CANDIDATE_SSH_KEY"), input_fn,
    )
    env["AK_MP_SERVER_CANDIDATE_DOMAIN"] = _prompt_value(
        console, "Candidate domain", "AK_MP_SERVER_CANDIDATE_DOMAIN",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CANDIDATE_DOMAIN"), input_fn,
    )
    env["AK_MP_SERVER_CANDIDATE_PORT"] = _prompt_value(
        console, "Candidate port", "AK_MP_SERVER_CANDIDATE_PORT",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CANDIDATE_PORT", "8080"), input_fn,
    )

    if topology == "single-vps":
        env["AK_MP_SERVER_CURRENT_HOST"] = env["AK_MP_SERVER_CANDIDATE_HOST"]
        env["AK_MP_SERVER_CURRENT_SSH_USER"] = env["AK_MP_SERVER_CANDIDATE_SSH_USER"]
        env["AK_MP_SERVER_CURRENT_SSH_KEY"] = env["AK_MP_SERVER_CANDIDATE_SSH_KEY"]
        env["AK_MP_SERVER_CURRENT_DOMAIN"] = env["AK_MP_SERVER_CANDIDATE_DOMAIN"]
    else:
        env["AK_MP_SERVER_CURRENT_HOST"] = _prompt_value(
            console, "Current host", "AK_MP_SERVER_CURRENT_HOST",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CURRENT_HOST"), input_fn,
        )
        env["AK_MP_SERVER_CURRENT_SSH_USER"] = _prompt_value(
            console, "Current SSH user", "AK_MP_SERVER_CURRENT_SSH_USER",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CURRENT_SSH_USER", "ubuntu"), input_fn,
        )
        env["AK_MP_SERVER_CURRENT_SSH_KEY"] = _prompt_value(
            console, "Current SSH key path (e.g. ~/.ssh/id_rsa, blank = use default)", "AK_MP_SERVER_CURRENT_SSH_KEY",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CURRENT_SSH_KEY"), input_fn,
        )
        env["AK_MP_SERVER_CURRENT_DOMAIN"] = _prompt_value(
            console, "Current domain", "AK_MP_SERVER_CURRENT_DOMAIN",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CURRENT_DOMAIN"), input_fn,
        )

    env["AK_MP_SERVER_CURRENT_PORT"] = _prompt_value(
        console, "Current port", "AK_MP_SERVER_CURRENT_PORT",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_CURRENT_PORT", "8081"), input_fn,
    )

    env["AK_MP_SERVER_DNS_PROVIDER"] = _prompt_value(
        console, "DNS provider (none/cloudflare)", "AK_MP_SERVER_DNS_PROVIDER",
        _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_DNS_PROVIDER", "none"), input_fn,
    )
    if env["AK_MP_SERVER_DNS_PROVIDER"] != "none":
        console.print("  DNS providers: none, cloudflare")
        env["AK_MP_SERVER_DNS_API_TOKEN"] = _prompt_value(
            console, "DNS API token", "AK_MP_SERVER_DNS_API_TOKEN",
            _seeded_prompt_value(env, seeded_defaults, "AK_MP_SERVER_DNS_API_TOKEN"), input_fn,
        )
    else:
        env["AK_MP_SERVER_DNS_API_TOKEN"] = ""

    console.print("  Mobile test config:")
    env["PLAYWRIGHT_VIEWPORT"] = _prompt_value(
        console, "Playwright viewport", "PLAYWRIGHT_VIEWPORT",
        _seeded_prompt_value(env, seeded_defaults, "PLAYWRIGHT_VIEWPORT", "375x812"), input_fn,
    )
    env["PLAYWRIGHT_DURATION"] = _prompt_value(
        console, "Playwright duration", "PLAYWRIGHT_DURATION",
        _seeded_prompt_value(env, seeded_defaults, "PLAYWRIGHT_DURATION", "60"), input_fn,
    )
    env["PLAYWRIGHT_BROWSER_ENGINE"] = _prompt_value(
        console, "Playwright browser engine", "PLAYWRIGHT_BROWSER_ENGINE",
        _seeded_prompt_value(env, seeded_defaults, "PLAYWRIGHT_BROWSER_ENGINE", "webkit"), input_fn,
    )
    env["PLAYWRIGHT_DEVICE"] = _prompt_value(
        console, "Playwright device", "PLAYWRIGHT_DEVICE",
        _seeded_prompt_value(env, seeded_defaults, "PLAYWRIGHT_DEVICE", "iPhone 14"), input_fn,
    )

    _senv.save(env)
    return env
