"""Output formatting — human-readable tables and JSON mode."""

import json
import sys


def output(data, json_mode=False):
    """Print result data in human or JSON format."""
    if json_mode:
        json.dump(data, sys.stdout, indent=2)
        print()
        return

    if not isinstance(data, dict):
        print(json.dumps(data, indent=2))
        return

    if not data.get("ok", True):
        print(f"ERROR: {data.get('error', 'Unknown error')}")
        if data.get("traceback"):
            print(data["traceback"])
        return

    payload = data.get("data", data)
    if isinstance(payload, list):
        _print_table(payload)
    elif isinstance(payload, dict):
        _print_dict(payload)
    else:
        print(payload)


def _print_dict(d, indent=0):
    """Pretty-print a dict."""
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            print(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"{prefix}{k}:")
            _print_table(v, indent + 1)
        elif isinstance(v, list):
            print(f"{prefix}{k}: {', '.join(str(x) for x in v)}")
        else:
            print(f"{prefix}{k}: {v}")


def _print_table(rows, indent=0):
    """Print a list of dicts as aligned columns."""
    if not rows:
        print("  (empty)")
        return

    prefix = "  " * indent

    # Determine columns and widths
    cols = list(rows[0].keys())
    widths = {c: len(c) for c in cols}
    str_rows = []
    for row in rows:
        sr = {}
        for c in cols:
            val = row.get(c, "")
            if isinstance(val, list):
                s = ", ".join(str(x) for x in val)
            elif isinstance(val, float):
                s = f"{val:.4f}"
            elif val is None:
                s = "-"
            else:
                s = str(val)
            # Truncate long values
            if len(s) > 40:
                s = s[:37] + "..."
            sr[c] = s
            widths[c] = max(widths[c], len(s))
        str_rows.append(sr)

    # Header
    header = prefix + "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print(prefix + "  ".join("-" * widths[c] for c in cols))

    # Rows
    for sr in str_rows:
        line = prefix + "  ".join(sr[c].ljust(widths[c]) for c in cols)
        print(line)
