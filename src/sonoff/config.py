import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Rule(Model):
    action: Literal["on", "off", "hold"]
    delay_minutes: float = Field(default=0, ge=0, le=1440)


class Socket(Model):
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    channel: int | None = Field(default=None, ge=0, le=7)
    mode: Literal["auto", "manual"] = "auto"
    mains_present: Rule = Field(default_factory=lambda: Rule(action="on", delay_minutes=20))
    mains_absent: Rule = Field(default_factory=lambda: Rule(action="off"))


class SensorConfig(Model):
    backend: Literal["mock", "gpio"] = "mock"
    chip: str = "/dev/gpiochip0"
    line: int = Field(default=17, ge=0, le=53)
    present_level: Literal[0, 1] = 0
    debounce_seconds: float = Field(default=0.3, ge=0, le=10)


class Config(Model):
    sensor: SensorConfig = Field(default_factory=SensorConfig)
    sockets: list[Socket] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_devices(self):
        ids = [s.device_id for s in self.sockets]
        if len(ids) != len(set(ids)):
            raise ValueError("Only one configured channel per device is supported")
        return self

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate(json.loads(path.read_text()))
