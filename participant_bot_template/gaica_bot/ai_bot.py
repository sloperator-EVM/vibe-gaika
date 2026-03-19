from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from gaica_bot.ai_features import extract_features
from gaica_bot.ai_policy import MLPPolicy
from gaica_bot.models import BotCommand, BotState, HelloMessage, RoundEndMessage, RoundStartMessage, TickMessage, Vec2


_DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "bootstrap_policy.json"


@dataclass(slots=True)
class AIBot:
    state: BotState = field(default_factory=BotState)
    model_path: Path = field(default_factory=lambda: Path(os.environ.get("GAICA_MODEL_PATH", _DEFAULT_MODEL_PATH)))
    _policy: MLPPolicy | None = None

    def __post_init__(self) -> None:
        self._policy = MLPPolicy.from_json(self.model_path)

    def on_hello(self, message: HelloMessage) -> None:
        self.state.hello = message

    def on_round_start(self, message: RoundStartMessage) -> None:
        self.state.current_round = message
        self.state.last_tick = None
        self.state.last_round_end = None
        self.state.command_seq = 0

    def on_round_end(self, message: RoundEndMessage) -> None:
        self.state.last_round_end = message

    def on_tick(self, message: TickMessage) -> BotCommand:
        self.state.last_tick = message
        seq = self.state.next_command_seq()

        if not message.you.alive:
            return BotCommand(seq=seq)

        assert self._policy is not None
        features = extract_features(message)
        output = self._policy.forward(features)

        aim = Vec2(output.aim_x, output.aim_y)
        if aim.length() <= 1e-6:
            enemy_dx = message.enemy.position.x - message.you.position.x
            enemy_dy = message.enemy.position.y - message.you.position.y
            aim = Vec2(enemy_dx, enemy_dy)
        if aim.length() <= 1e-6:
            aim = Vec2(1.0, 0.0)

        return BotCommand(
            seq=seq,
            move=Vec2(output.move_x, output.move_y).clamp_unit(),
            aim=aim.clamp_unit(),
            shoot=output.shoot >= 0.5,
            kick=output.kick >= 0.5,
            pickup=output.pickup >= 0.5,
            drop=output.drop >= 0.5,
            throw_item=output.throw >= 0.5,
            interact=False,
        )
