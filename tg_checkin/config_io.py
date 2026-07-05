from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    return data


def save_config(path: str, config: dict[str, Any]) -> None:
    target = Path(path)
    tmp = target.with_name(f".{target.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
