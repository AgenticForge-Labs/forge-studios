from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

class TelemetrySink:
    def __init__(self, path: str | Path = '.agenticforge/telemetry.jsonl'):
        self.path=Path(path)
    def emit(self,event_type: str, **payload: Any) -> None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        event={'event_type':event_type,'recorded_at':datetime.now(timezone.utc).isoformat(),**payload}
        with self.path.open('a') as f:
            f.write(json.dumps(event,ensure_ascii=False,default=str)+'\n')
