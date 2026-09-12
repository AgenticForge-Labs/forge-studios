from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .contracts import EpisodePackage

def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise TypeError('JSON root must be an object')
    return value

def save_json(path: str | Path, value: Any) -> Path:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    p.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    return p

def load_package(path: str | Path) -> EpisodePackage:
    return EpisodePackage.model_validate(load_json(path))
