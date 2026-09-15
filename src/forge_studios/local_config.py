from __future__ import annotations
import json, os
from pathlib import Path
from platformdirs import user_config_dir

_ENV_NAMES={'fal':'FAL_KEY'}

class LocalSecretStore:
    """Resolve local API credentials from environment or a user-only config file."""
    def __init__(self, path: str | Path | None = None):
        self.path=Path(path or Path(user_config_dir('agenticforge'))/'credentials.json')
    def resolve(self,name: str) -> str | None:
        if value:=os.getenv(_ENV_NAMES.get(name,name.upper())):
            return value
        data=self._read(); value=data.get(name); return str(value) if value else None
    def set(self,name: str,value: str) -> str:
        if not value: raise ValueError('value cannot be empty')
        data=self._read(); data[name]=value; self._write(data); return str(self.path)
    def list_names(self) -> list[str]:
        return sorted(set(self._read()) | {name for name,env in _ENV_NAMES.items() if os.getenv(env)})
    def _read(self) -> dict:
        if not self.path.exists(): return {}
        try:
            value=json.loads(self.path.read_text()); return value if isinstance(value,dict) else {}
        except Exception: return {}
    def _write(self,data: dict) -> None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try: os.chmod(self.path.parent,0o700)
        except OSError: pass
        self.path.write_text(json.dumps(data,indent=2)+'\n')
        try: os.chmod(self.path,0o600)
        except OSError: pass
