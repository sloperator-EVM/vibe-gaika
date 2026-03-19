from __future__ import annotations

from gaica_bot.models import TickMessage
from gaica_bot.smart_bot import SmartBot


def _tick_payload(*, weapon=None, pickups=None, projectiles=None, obstacles=None, enemy_pos=(220.0, 96.0), me_pos=(80.0, 96.0)):
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
            "obstacles": obstacles or [],
            "breakables": [],
            "letterboxes": [],
            "effects": [],
            "result": None,
        },
    }


def test_bot_moves_to_pickup_when_unarmed() -> None:
    bot = SmartBot()
    msg = TickMessage.from_payload(
        _tick_payload(
            pickups=[{"id": 3, "type": "Uzi", "ammo": 25, "position": [110.0, 96.0], "cooldown": 0.0}],
        )
    )

    command = bot.on_tick(msg)

    assert command.pickup is False
    assert command.move.x > 0.2
    assert abs(command.move.y) < 0.2


def test_bot_shoots_when_enemy_visible_and_armed() -> None:
    bot = SmartBot()
    msg = TickMessage.from_payload(
        _tick_payload(weapon={"type": "Revolver", "ammo": 8})
    )

    command = bot.on_tick(msg)

    assert command.shoot is True
    assert command.aim.x > 0.5


def test_bot_dodges_projectile() -> None:
    bot = SmartBot()
    msg = TickMessage.from_payload(
        _tick_payload(
            weapon={"type": "Revolver", "ammo": 8},
            projectiles=[
                {
                    "id": 1,
                    "owner": 2,
                    "type": "Revolver",
                    "position": [40.0, 96.0],
                    "velocity": [500.0, 0.0],
                    "remaining_life": 1.0,
                }
            ],
        )
    )

    command = bot.on_tick(msg)

    assert abs(command.move.y) > 0.5
