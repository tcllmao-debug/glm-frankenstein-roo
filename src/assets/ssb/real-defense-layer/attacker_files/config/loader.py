"""
config/loader.py — Configuration loader.

Loads configuration from environment variables with sensible defaults.
"""
import os
import json
from pathlib import Path
from typing import Any

_DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8080,
    "log_level": "INFO",
    "max_workers": 4,
}

def load_config(env_prefix: str = "APP_", config_file: str | None = None) -> dict[str, Any]:
    """Load config from file (if provided) then override with env vars."""
    cfg = dict(_DEFAULTS)
    if config_file and Path(config_file).exists():
        with open(config_file) as f:
            cfg.update(json.load(f))
    for key, default in list(cfg.items()):
        env_key = env_prefix + key.upper()
        cfg[key] = os.environ.get(env_key, default)
    return cfg

def get_secret(name: str) -> str | None:
    """Fetch a secret from the environment."""
    return os.environ.get(name)
