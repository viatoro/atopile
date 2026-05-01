import os
import uuid
from dataclasses import dataclass
from typing import Any

import yaml

from faebryk.libs.paths import get_config_dir
from faebryk.libs.util import ConfigFlag, once

ENABLE_TELEMETRY = ConfigFlag(
    "TELEMETRY",
    default=not os.getenv("CI"),
    descr="Enable telemetry reporting",
)


@dataclass
class TelemetryConfig:
    telemetry: bool
    id: uuid.UUID

    def __init__(
        self,
        telemetry: bool | None = None,
        id: uuid.UUID | str | None = None,
    ) -> None:
        match id:
            case str():
                self.id = uuid.UUID(id)
            case uuid.UUID():
                self.id = id
            case _:
                self.id = uuid.uuid4()

        match telemetry:
            case bool():
                self.telemetry = telemetry
            case _:
                self.telemetry = True

    def to_dict(self) -> dict:
        return {"id": str(self.id), "telemetry": self.telemetry}

    @classmethod
    @once
    def load(cls) -> "TelemetryConfig":
        if not ENABLE_TELEMETRY:
            return cls(telemetry=False)

        atopile_config_dir = get_config_dir()
        telemetry_yaml = atopile_config_dir / "telemetry.yaml"

        raw_yaml: dict[str, Any] = {}
        try:
            raw_yaml = yaml.safe_load(telemetry_yaml.read_text())
            config = cls(**(raw_yaml or {}))
        except Exception:
            config = cls()

        try:
            atopile_config_dir.mkdir(parents=True, exist_ok=True)
            obj = config.to_dict()
            if obj != raw_yaml:
                with telemetry_yaml.open("w") as f:
                    yaml.dump(obj, stream=f)
        except Exception:
            pass

        return config
