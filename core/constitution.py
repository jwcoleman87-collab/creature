"""
core/constitution.py
====================
Loads and validates the constitution YAML.
Every module imports from here — nobody reads the file directly.
The constitution is the supreme law. If it can't be loaded, Creature refuses to start.
"""

import os
import yaml


_CONSTITUTION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "constitution.yaml"
)

_loaded = None


def load() -> dict:
    """Load and return the full constitution. Cached after first load."""
    global _loaded
    if _loaded is not None:
        return _loaded

    path = os.path.abspath(_CONSTITUTION_PATH)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Constitution not found at {path}. "
            "Creature cannot start without its supreme law."
        )

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    _validate(data)
    _loaded = data
    print(f"[Constitution] Loaded v{data['creature']['version']} — mode: {data['creature']['mode']}")
    return data


def get(key_path: str, default=None):
    """
    Convenience accessor using dot notation.
    e.g. get('risk.starting_balance') → 500.0
    """
    data = load()
    keys = key_path.split(".")
    val = data
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return default
    return val


def _validate(data: dict):
    """Basic sanity checks. Raises ValueError if the constitution is malformed."""
    required = [
        "creature", "market", "risk", "strategy", "journal", "learning"
    ]
    for section in required:
        if section not in data:
            raise ValueError(f"[Constitution] Missing required section: '{section}'")

    mode = data["creature"].get("mode")
    if mode not in ("paper", "live"):
        raise ValueError(f"[Constitution] Invalid mode '{mode}'. Must be 'paper' or 'live'.")

    if mode == "live":
        raise RuntimeError(
            "[Constitution] Live mode is not permitted. "
            "Creature must prove itself on paper first."
        )

    balance = data["risk"].get("starting_balance", 0)
    if balance <= 0:
        raise ValueError("[Constitution] starting_balance must be greater than 0.")

    print("[Constitution] Validation passed.")
