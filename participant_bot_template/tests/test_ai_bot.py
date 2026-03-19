from __future__ import annotations

from gaica_bot.ai_bot import AIBot
from gaica_bot.ai_features import FEATURE_SIZE, extract_features
from gaica_bot.ai_policy import MLPPolicy
from gaica_bot.models import TickMessage


def _tick_payload(*, weapon=None, pickups=None, projectiles=None, enemy_pos=(220.0, 96.0), me_pos=(80.0, 96.0)):
    return {
        "tick": 1,
        "time_seconds": 0.1,
        "you": {
            "id": 1,
            "position": list(me_pos),
            "facing": [1.0, 0.0],
            "alive": True,
            "color": "#f00",
            "character": "orange",
            "weapon": weapon,
            "shoot_cooldown": 0.0,
            "kick_cooldown": 0.0,
            "stun_remaining": 0.0,
        },
        "enemy": {
            "id": 2,
            "position": list(enemy_pos),
            "facing": [-1.0, 0.0],
            "alive": True,
            "color": "#0f0",
            "character": "lime",
            "weapon": None,
            "shoot_cooldown": 0.0,
            "kick_cooldown": 0.0,
            "stun_remaining": 0.0,
        },
        "snapshot": {
            "status": "running",
            "tick": 1,
            "time_seconds": 0.1,
            "time_limit_seconds": 180.0,
            "players": [],
            "pickups": pickups or [],
            "projectiles": projectiles or [],
            "obstacles": [],
            "breakables": [],
            "letterboxes": [],
            "effects": [],
            "result": None,
        },
    }


def test_feature_vector_has_fixed_size() -> None:
    msg = TickMessage.from_payload(
        _tick_payload(pickups=[{"id": 7, "type": "Uzi", "ammo": 25, "position": [100.0, 120.0], "cooldown": 0.0}])
    )
    features = extract_features(msg)
    assert len(features) == FEATURE_SIZE
    assert all(-1.0 <= value <= 1.0 for value in features)


def test_policy_forward_returns_finite_outputs() -> None:
    msg = TickMessage.from_payload(_tick_payload(weapon={"type": "Revolver", "ammo": 8}))
    bot = AIBot()
    features = extract_features(msg)
    policy = MLPPolicy.from_json(bot.model_path)
    output = policy.forward(features)
    assert -1.0 <= output.move_x <= 1.0
    assert -1.0 <= output.move_y <= 1.0
    assert 0.0 <= output.shoot <= 1.0


def test_ai_bot_emits_valid_command_payload() -> None:
    bot = AIBot()
    msg = TickMessage.from_payload(_tick_payload(weapon={"type": "Revolver", "ammo": 8}))
    command = bot.on_tick(msg)
    payload = command.to_payload()
    assert payload["type"] == "command"
    assert len(payload["move"]) == 2
    assert len(payload["aim"]) == 2
    assert isinstance(payload["shoot"], bool)
