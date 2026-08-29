"""Weather system control."""

from cli_anything.asciiid.core import editor


WEATHER_STATES = {
    "clear": 0,
    "light_snow": 1,
    "heavy_snow": 2,
    "blizzard": 3,
}

WEATHER_NAMES = {v: k for k, v in WEATHER_STATES.items()}


def get() -> dict:
    """Get current weather state.

    Returns:
        Dict with state (int), name (str), and response lines.
    """
    lines = editor.send("GET_WEATHER", timeout=5.0)
    state = 0
    for line in lines:
        for part in line.split():
            try:
                state = int(part)
                break
            except ValueError:
                continue
    return {
        "state": state,
        "name": WEATHER_NAMES.get(state, f"unknown({state})"),
        "response": lines,
    }


def set(state) -> dict:
    """Set weather state.

    Args:
        state: Integer 0-3, or name string (clear/light_snow/heavy_snow/blizzard).

    Returns:
        Dict with status.
    """
    if isinstance(state, str):
        state_int = WEATHER_STATES.get(state.lower().replace("-", "_"))
        if state_int is None:
            valid = ", ".join(WEATHER_STATES.keys())
            raise ValueError(f"Unknown weather state '{state}'. Valid: {valid}")
    else:
        state_int = int(state)
        if state_int < 0 or state_int > 3:
            raise ValueError(f"Weather state must be 0-3, got {state_int}")

    lines = editor.send(f"SET_WEATHER {state_int}", timeout=5.0)
    return {
        "status": "set",
        "state": state_int,
        "name": WEATHER_NAMES.get(state_int, f"unknown({state_int})"),
        "response": lines,
    }
