from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any


POLICY_OUTPUTS = (
    "move_x",
    "move_y",
    "aim_x",
    "aim_y",
    "shoot",
    "kick",
    "pickup",
    "drop",
    "throw",
)


@dataclass(slots=True)
class PolicyOutput:
    move_x: float
    move_y: float
    aim_x: float
    aim_y: float
    shoot: float
    kick: float
    pickup: float
    drop: float
    throw: float


@dataclass(slots=True)
class MLPPolicy:
    input_size: int
    hidden_size: int
    output_size: int
    w1: list[list[float]]
    b1: list[float]
    w2: list[list[float]]
    b2: list[float]

    @classmethod
    def from_json(cls, path: str | Path) -> "MLPPolicy":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MLPPolicy":
        return cls(
            input_size=int(payload["input_size"]),
            hidden_size=int(payload["hidden_size"]),
            output_size=int(payload["output_size"]),
            w1=[[float(v) for v in row] for row in payload["w1"]],
            b1=[float(v) for v in payload["b1"]],
            w2=[[float(v) for v in row] for row in payload["w2"]],
            b2=[float(v) for v in payload["b2"]],
        )

    def forward(self, features: list[float]) -> PolicyOutput:
        if len(features) != self.input_size:
            raise ValueError(f"feature size mismatch: {len(features)} != {self.input_size}")

        hidden: list[float] = []
        for row, bias in zip(self.w1, self.b1):
            acc = bias
            for weight, feature in zip(row, features):
                acc += weight * feature
            hidden.append(math.tanh(acc))

        outputs: list[float] = []
        for row, bias in zip(self.w2, self.b2):
            acc = bias
            for weight, hidden_value in zip(row, hidden):
                acc += weight * hidden_value
            outputs.append(acc)

        squashed = [math.tanh(value) for value in outputs[:4]] + [self._sigmoid(value) for value in outputs[4:]]
        return PolicyOutput(**dict(zip(POLICY_OUTPUTS, squashed, strict=True)))

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0.0:
            z = math.exp(-value)
            return 1.0 / (1.0 + z)
        z = math.exp(value)
        return z / (1.0 + z)
