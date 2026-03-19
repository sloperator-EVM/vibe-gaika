from __future__ import annotations

import unittest

from bots.smart_core import Cell, Navigator, SmartBrain


class TestSmartBots(unittest.TestCase):
    def _simple_level(self) -> dict:
        floor_tiles = []
        for y in range(0, 4 * 64, 64):
            for x in range(0, 4 * 64, 64):
                floor_tiles.append({"x": x, "y": y, "size": 64})
        return {
            "identifier": "NavTest",
            "width": 256,
            "height": 256,
            "floor_tiles": floor_tiles,
            "top_tiles": [],
            "small_tiles": [],
        }

    def _single_row_level(self) -> dict:
        floor_tiles = [{"x": x, "y": 0, "size": 64} for x in range(0, 4 * 64, 64)]
        return {
            "identifier": "SingleRow",
            "width": 256,
            "height": 64,
            "floor_tiles": floor_tiles,
            "top_tiles": [],
            "small_tiles": [],
        }

    def _disconnected_level(self) -> dict:
        return {
            "identifier": "Disconnected",
            "width": 192,
            "height": 64,
            "floor_tiles": [
                {"x": 0, "y": 0, "size": 64},
                {"x": 128, "y": 0, "size": 64},
            ],
            "top_tiles": [],
            "small_tiles": [],
        }

    def test_astar_avoids_blocked_center(self) -> None:
        nav = Navigator(self._simple_level())
        blocked_cells = {
            Cell(1, 1),
            Cell(2, 1),
        }
        blocked_edges: set[tuple[Cell, Cell]] = set()

        path = nav.astar(Cell(0, 1), Cell(3, 1), blocked_cells, blocked_edges)
        self.assertIsNotNone(path)
        assert path is not None

        for cell in path:
            self.assertNotIn(cell, blocked_cells)

        self.assertGreater(len(path), 4)

    def test_astar_respects_blocked_edges(self) -> None:
        nav = Navigator(self._simple_level())
        blocked_cells: set[Cell] = set()
        blocked_edges = {
            (Cell(0, 1), Cell(1, 1)),
            (Cell(1, 1), Cell(0, 1)),
            (Cell(1, 1), Cell(2, 1)),
            (Cell(2, 1), Cell(1, 1)),
        }

        path = nav.astar(Cell(0, 1), Cell(3, 1), blocked_cells, blocked_edges)
        self.assertIsNotNone(path)
        assert path is not None

        traversed = set(zip(path, path[1:]))
        self.assertNotIn((Cell(0, 1), Cell(1, 1)), traversed)
        self.assertGreater(len(path), 4)

    def test_strategies_choose_different_moves(self) -> None:
        level = self._simple_level()

        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "players": [
                {
                    "id": 1,
                    "position": [64.0, 64.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [64.0 + 150.0, 64.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        assault = SmartBrain("assault", seed=1)
        tactical = SmartBrain("tactical", seed=1)
        assault.on_round_start(round_start)
        tactical.on_round_start(round_start)

        cmd_assault = assault.on_tick(tick)
        cmd_tactical = tactical.on_tick(tick)

        self.assertNotEqual(cmd_assault["move"], cmd_tactical["move"])

    def test_tactical_shoots_at_safe_distance(self) -> None:
        level = self._simple_level()

        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "projectiles": [],
            "players": [
                {
                    "id": 1,
                    "position": [64.0, 64.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [244.0, 64.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        tactical = SmartBrain("tactical", seed=2)
        tactical.on_round_start(round_start)
        cmd = tactical.on_tick(tick)

        self.assertTrue(cmd["shoot"])
        self.assertLess(cmd["move"][0], 0.35)

    def test_assault_targets_glass_when_enemy_blocked(self) -> None:
        level = self._simple_level()

        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [
                {
                    "id": 10,
                    "kind": "glass",
                    "center": [128.0, 64.0],
                    "half_size": [2.0, 32.0],
                    "solid": True,
                }
            ],
            "pickups": [],
            "players": [
                {
                    "id": 1,
                    "position": [64.0, 64.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [192.0, 64.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        assault = SmartBrain("assault", seed=3)
        assault.on_round_start(round_start)
        cmd = assault.on_tick(tick)

        self.assertFalse(cmd["shoot"])
        self.assertGreater(cmd["move"][0], 0.5)
        self.assertGreater(cmd["aim"][0], 0.5)

    def test_loot_fallback_to_letterbox_kick(self) -> None:
        level = self._simple_level()
        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [
                {
                    "id": 21,
                    "kind": "letterbox",
                    "center": [88.0, 64.0],
                    "half_size": [16.0, 4.0],
                    "solid": True,
                }
            ],
            "pickups": [],
            "players": [
                {
                    "id": 1,
                    "position": [64.0, 64.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": None,
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [224.0, 64.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": None,
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        assault = SmartBrain("assault", seed=3)
        assault.on_round_start(round_start)
        cmd = assault.on_tick(tick)

        self.assertTrue(cmd["kick"])
        self.assertGreater(cmd["aim"][0], 0.5)

    def test_no_loot_sources_fallback_chases_enemy(self) -> None:
        level = self._simple_level()
        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "players": [
                {
                    "id": 1,
                    "position": [64.0, 64.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": None,
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [224.0, 64.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": None,
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        tactical = SmartBrain("tactical", seed=4)
        tactical.on_round_start(round_start)
        cmd = tactical.on_tick(tick)
        self.assertGreater(cmd["move"][0], 0.4)
        self.assertFalse(cmd["shoot"])

    def test_evades_incoming_bullet(self) -> None:
        level = self._simple_level()
        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "projectiles": [
                {
                    "id": 77,
                    "owner": 2,
                    "type": "Revolver",
                    "position": [10.0, 64.0],
                    "velocity": [500.0, 0.0],
                    "remaining_life": 1.0,
                }
            ],
            "players": [
                {
                    "id": 1,
                    "position": [64.0, 64.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [192.0, 64.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        assault = SmartBrain("assault", seed=5)
        assault.on_round_start(round_start)
        cmd = assault.on_tick(tick)

        # Bullet flies along +X into player; dodge should be mostly vertical.
        self.assertGreater(abs(cmd["move"][1]), 0.5)
        self.assertFalse(cmd["shoot"])

    def test_tactical_retreat_avoids_corner(self) -> None:
        level = self._simple_level()
        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "projectiles": [],
            "players": [
                {
                    "id": 1,
                    "position": [32.0, 96.0],  # left border cell center
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [96.0, 96.0],
                    "facing": [-1.0, 0.0],  # aiming directly at tactical bot
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }

        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        tactical = SmartBrain("tactical", seed=6)
        tactical.on_round_start(round_start)
        cmd = tactical.on_tick(tick)

        # Should not retreat deeper into border/corner (negative X); prefer lateral escape.
        self.assertGreater(cmd["move"][0], -0.2)
        self.assertGreater(abs(cmd["move"][1]), 0.35)

    def test_evade_on_narrow_strip_keeps_bot_on_floor(self) -> None:
        level = self._single_row_level()
        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "projectiles": [
                {
                    "id": 90,
                    "owner": 2,
                    "type": "Revolver",
                    "position": [10.0, 32.0],
                    "velocity": [500.0, 0.0],
                    "remaining_life": 1.0,
                }
            ],
            "players": [
                {
                    "id": 1,
                    "position": [96.0, 32.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [192.0, 32.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": {"type": "Revolver", "ammo": 10},
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }
        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        assault = SmartBrain("assault", seed=7)
        assault.on_round_start(round_start)
        cmd = assault.on_tick(tick)

        # No floor above/below: bot must avoid vertical dodge into void.
        self.assertLess(abs(cmd["move"][1]), 0.2)

    def test_disconnected_enemy_island_does_not_cause_suicide_run(self) -> None:
        level = self._disconnected_level()
        round_start = {
            "type": "round_start",
            "player_id": 1,
            "level": level,
        }

        snapshot = {
            "level": level,
            "obstacles": [],
            "pickups": [],
            "projectiles": [],
            "players": [
                {
                    "id": 1,
                    "position": [32.0, 32.0],
                    "facing": [1.0, 0.0],
                    "alive": True,
                    "weapon": None,
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
                {
                    "id": 2,
                    "position": [160.0, 32.0],
                    "facing": [-1.0, 0.0],
                    "alive": True,
                    "weapon": None,
                    "shoot_cooldown": 0.0,
                    "kick_cooldown": 0.0,
                    "stun_remaining": 0.0,
                },
            ],
        }
        tick = {
            "type": "tick",
            "you": snapshot["players"][0],
            "enemy": snapshot["players"][1],
            "snapshot": snapshot,
        }

        tactical = SmartBrain("tactical", seed=8)
        tactical.on_round_start(round_start)
        cmd = tactical.on_tick(tick)

        # No traversable neighbor cell: bot should hold instead of walking into gap.
        self.assertLess(abs(cmd["move"][0]), 0.2)
        self.assertLess(abs(cmd["move"][1]), 0.2)


if __name__ == "__main__":
    unittest.main()
