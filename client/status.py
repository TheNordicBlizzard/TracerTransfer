from dataclasses import dataclass, asdict
import json


@dataclass
class Status:
    pending: int = 0
    sent: int = 0
    awaiting_delivery: int = 0
    conflicts: int = 0
    uncertain: int = 0
    error: str | None = None
    paused: bool = False
    configured: bool = False
    last_heartbeat: float | None = None

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)
