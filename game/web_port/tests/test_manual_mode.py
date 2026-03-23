from __future__ import annotations

import io
import json
from pathlib import Path
import time
import unittest
from urllib.request import Request, urlopen

from game.coordinator import MatchCoordinator
from game.level_loader import load_level
from game.simulation import GameSimulation
from game.web_server import WebServer


class TestManualMode(unittest.TestCase):
    def _coordinator(self) -> MatchCoordinator:
        level = load_level('game/web_port/assets/levels/test_ldtk_project.ldtk', level_index=0, seed=1)
        simulation = GameSimulation(level, seed=1, round_time_limit_seconds=5)
        return MatchCoordinator(simulation, manual_player_ids={2})

    def test_snapshot_exposes_manual_player_role(self) -> None:
        coordinator = self._coordinator()
        bot_id = coordinator.connect_bot(io.BytesIO())
        self.assertEqual(bot_id, 1)

        snapshot = coordinator.get_snapshot()
        self.assertEqual(snapshot['manual_player_ids'], [2])
        green_role = next(role for role in snapshot['color_roles'] if role['player_id'] == 2)
        self.assertEqual(green_role['bot_name'], 'human')

    def test_manual_command_endpoint_accepts_player_two_input(self) -> None:
        coordinator = self._coordinator()
        server = WebServer('127.0.0.1', 0, coordinator, static_dir=Path('game/web_port/static'), assets_dir=Path('game/web_port/assets'))
        server.start()
        try:
            req = Request(
                f'http://127.0.0.1:{server.actual_port}/api/manual-command',
                data=json.dumps({'player_id': 2, 'command': {'seq': 7, 'move': [1, 0], 'aim': [0, -1], 'shoot': True}}).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urlopen(req, timeout=2.0) as response:
                self.assertEqual(response.status, 200)
            time.sleep(0.05)
            self.assertEqual(coordinator._commands[2].seq, 7)
            self.assertTrue(coordinator._commands[2].shoot)
        finally:
            server.stop()


if __name__ == '__main__':
    unittest.main()
