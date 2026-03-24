from __future__ import annotations

from gaica_bot.combat_bot import CombatBot
from gaica_bot.models import RoundStartMessage, TickMessage
from gaica_bot.navigator import Cell, Navigator


def _floor_tiles(row_count: int = 3, col_count: int = 5):
    return [{"px": [x * 64, y * 64], "src": [0, 0], "t": 7} for y in range(row_count) for x in range(col_count)]


def _tick_payload(*, weapon=None, pickups=None, projectiles=None, obstacles=None, enemy_weapon=None, enemy_pos=(220.0, 96.0), me_pos=(80.0, 96.0), kick_cd=0.0, floor_tiles=None):
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
            "kick_cooldown": kick_cd,
            "stun_remaining": 0.0,
        },
        "enemy": {
            "id": 2,
            "position": list(enemy_pos),
            "facing": [-1.0, 0.0],
            "alive": True,
            "color": "#0f0",
            "character": "lime",
            "weapon": enemy_weapon,
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
        "level": floor_tiles or _floor_tiles(),
    }


def _round_start_payload(floor_tiles=None):
    return {
        "player_id": 1,
        "enemy_id": 2,
        "tick_rate": 30,
        "level": {
            "identifier": "TestLevel",
            "width": 320,
            "height": 192,
            "floor_tiles": floor_tiles or _floor_tiles(),
            "top_tiles": [],
            "small_tiles": [],
            "player_spawns": [[64.0, 96.0], [224.0, 96.0]],
        },
        "series": {"enabled": False, "round": 1, "total_rounds": 1, "completed_rounds": 0, "score": {"1": 0, "2": 0}},
    }


def _bot(floor_tiles=None) -> CombatBot:
    bot = CombatBot()
    bot.on_round_start(RoundStartMessage.from_payload(_round_start_payload(floor_tiles=floor_tiles)))
    return bot


def test_navigator_astar_avoids_blocked_center() -> None:
    nav = Navigator.from_floor_tiles(_floor_tiles())
    blocked = {Cell(2, 1)}
    path = nav.astar(Cell(1, 1), Cell(3, 1), blocked, set())
    assert path is not None
    assert Cell(2, 1) not in path
    assert len(path) >= 3


def test_bot_goes_to_nearest_weapon_when_unarmed() -> None:
    bot = _bot()
    payload = _tick_payload(
        pickups=[
            {"id": 1, "type": "Revolver", "ammo": 8, "position": [120.0, 96.0], "cooldown": 0.0},
            {"id": 2, "type": "Uzi", "ammo": 25, "position": [240.0, 96.0], "cooldown": 0.0},
        ]
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.move.x > 0.3
    assert command.pickup is False


def test_bot_picks_up_weapon_immediately_when_in_range() -> None:
    bot = _bot()
    payload = _tick_payload(
        pickups=[{"id": 1, "type": "Revolver", "ammo": 8, "position": [92.0, 96.0], "cooldown": 0.0}],
        enemy_weapon={"type": "Uzi", "ammo": 20},
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.pickup is True


def test_bot_pathfinds_around_wall_to_pickup() -> None:
    bot = _bot()
    payload = _tick_payload(
        pickups=[{"id": 1, "type": "Revolver", "ammo": 8, "position": [220.0, 96.0], "cooldown": 0.0}],
        obstacles=[{"id": 10, "kind": "wall", "center": [150.0, 96.0], "half_size": [8.0, 32.0], "solid": True}],
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.move.length() > 0.2


def test_bot_targets_glass_with_kick_only() -> None:
    bot = _bot()
    payload = _tick_payload(
        weapon={"type": "Revolver", "ammo": 8},
        enemy_weapon=None,
        obstacles=[{"id": 10, "kind": "glass", "center": [100.0, 96.0], "half_size": [2.0, 16.0], "solid": True}],
        enemy_pos=(220.0, 96.0),
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.shoot is False
    assert command.kick is True or command.move.length() > 0.2
    assert command.aim.x > 0.5


def test_bot_uses_letterbox_when_no_pickups_exist() -> None:
    bot = _bot()
    payload = _tick_payload(enemy_pos=(224.0, 96.0), me_pos=(64.0, 96.0))
    payload["snapshot"]["letterboxes"] = [{"id": 21, "position": [88.0, 96.0], "cooldown": 0.0, "ready": True}]
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.kick is True or command.move.x > 0.2


def test_priority_kick_disarms_armed_enemy() -> None:
    bot = _bot()
    payload = _tick_payload(weapon={"type": "Revolver", "ammo": 8}, enemy_weapon={"type": "Uzi", "ammo": 20}, enemy_pos=(116.0, 96.0))
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.kick is True
    assert command.shoot is False


def test_bot_only_shoots_close_disarmed_enemy() -> None:
    bot = _bot()
    far_payload = _tick_payload(weapon={"type": "Revolver", "ammo": 8}, enemy_weapon=None, enemy_pos=(250.0, 96.0))
    far_command = bot.on_tick(TickMessage.from_payload(far_payload))
    assert far_command.shoot is False

    close_payload = _tick_payload(weapon={"type": "Revolver", "ammo": 8}, enemy_weapon=None, enemy_pos=(150.0, 96.0))
    close_command = bot.on_tick(TickMessage.from_payload(close_payload))
    assert close_command.shoot is True


def test_bot_shoots_through_doors() -> None:
    bot = _bot()
    payload = _tick_payload(
        weapon={"type": "Revolver", "ammo": 8},
        enemy_weapon={"type": "Revolver", "ammo": 8},
        enemy_pos=(192.0, 96.0),
        obstacles=[{"id": 40, "kind": "door", "center": [128.0, 96.0], "half_size": [6.0, 32.0], "solid": True}],
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.shoot is True


def test_bot_abuses_wall_kick_range() -> None:
    bot = _bot()
    payload = _tick_payload(enemy_weapon={"type": "Revolver", "ammo": 8}, enemy_pos=(122.0, 96.0), obstacles=[{"id": 11, "kind": "wall", "center": [101.0, 96.0], "half_size": [3.0, 32.0], "solid": True}])
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.kick is True


def test_dodge_does_not_override_into_void() -> None:
    floor_tiles = _floor_tiles(row_count=1, col_count=4)
    bot = _bot(floor_tiles=floor_tiles)
    payload = _tick_payload(
        weapon={"type": "Revolver", "ammo": 8},
        enemy_weapon={"type": "Revolver", "ammo": 8},
        me_pos=(96.0, 58.0),
        enemy_pos=(192.0, 58.0),
        projectiles=[{"id": 3, "owner": 2, "type": "Revolver", "position": [20.0, 58.0], "velocity": [500.0, 0.0], "remaining_life": 1.0}],
        floor_tiles=floor_tiles,
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert abs(command.move.y) < 0.2


def test_bot_ignores_glass_breaking_when_under_threat() -> None:
    bot = _bot()
    payload = _tick_payload(
        weapon={"type": "Revolver", "ammo": 8},
        enemy_weapon={"type": "Revolver", "ammo": 8},
        obstacles=[{"id": 10, "kind": "glass", "center": [100.0, 96.0], "half_size": [2.0, 16.0], "solid": True}],
        enemy_pos=(170.0, 96.0),
    )
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.kick is False


def test_bot_prefers_near_letterbox_over_far_pickup_when_unarmed() -> None:
    bot = _bot()
    payload = _tick_payload(
        pickups=[{"id": 1, "type": "Revolver", "ammo": 8, "position": [260.0, 96.0], "cooldown": 0.0}],
        me_pos=(64.0, 96.0),
    )
    payload["snapshot"]["letterboxes"] = [{"id": 21, "position": [92.0, 96.0], "cooldown": 0.0, "ready": True}]
    command = bot.on_tick(TickMessage.from_payload(payload))
    assert command.kick is True

