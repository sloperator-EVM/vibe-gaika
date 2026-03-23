from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from math import hypot
from typing import Iterable

from gaica_bot.models import ObstacleView, Vec2


TILE_SIZE = 64.0
PLAYER_MARGIN = 10.0
SOLID_KINDS = {"wall", "door", "glass", "box", "letterbox"}
BREAKABLE_KINDS = {"glass", "box", "letterbox"}


@dataclass(frozen=True, slots=True)
class Cell:
    x: int
    y: int


@dataclass(slots=True)
class Navigator:
    floor_cells: set[Cell] = field(default_factory=set)
    _path_cache: dict[tuple[Cell, Cell, tuple[int, ...]], list[Cell]] = field(default_factory=dict)

    @classmethod
    def from_floor_tiles(cls, floor_tiles: list[dict]) -> "Navigator":
        cells: set[Cell] = set()
        for tile in floor_tiles:
            if not isinstance(tile, dict):
                continue
            px = tile.get("px") or [tile.get("x", 0), tile.get("y", 0)]
            if not isinstance(px, (list, tuple)) or len(px) < 2:
                continue
            size = float(tile.get("size") or TILE_SIZE)
            cells_x = max(1, round(size / TILE_SIZE))
            cells_y = max(1, round(size / TILE_SIZE))
            origin_x = int(round(float(px[0]) / TILE_SIZE))
            origin_y = int(round(float(px[1]) / TILE_SIZE))
            for dx in range(cells_x):
                for dy in range(cells_y):
                    cells.add(Cell(origin_x + dx, origin_y + dy))
        return cls(floor_cells=cells)

    def direction_to(self, start: Vec2, target: Vec2, obstacles: list[ObstacleView]) -> Vec2:
        path = self.path_to(start, target, obstacles)
        if len(path) < 2:
            return Vec2(target.x - start.x, target.y - start.y).normalized()
        waypoint = self.cell_center(path[1])
        return Vec2(waypoint.x - start.x, waypoint.y - start.y).normalized()

    def path_to(self, start: Vec2, target: Vec2, obstacles: list[ObstacleView]) -> list[Cell]:
        start_cell = self.nearest_floor_cell(start)
        goal_cell = self.nearest_floor_cell(target)
        if start_cell is None or goal_cell is None:
            return []
        blocked_cells = self.blocked_cells(obstacles)
        blocked_edges = self.blocked_edges(obstacles)
        if start_cell in blocked_cells or goal_cell in blocked_cells:
            return []
        cache_key = (start_cell, goal_cell, self._obstacle_signature(obstacles))
        cached = self._path_cache.get(cache_key)
        if cached is not None:
            return cached
        path = self.astar(start_cell, goal_cell, blocked_cells, blocked_edges) or []
        if len(self._path_cache) > 256:
            self._path_cache.clear()
        self._path_cache[cache_key] = path
        return path

    def astar(
        self,
        start: Cell,
        goal: Cell,
        blocked_cells: set[Cell],
        blocked_edges: set[tuple[Cell, Cell]],
    ) -> list[Cell] | None:
        if start not in self.floor_cells or goal not in self.floor_cells:
            return None
        open_heap: list[tuple[float, float, int, int, Cell]] = [(0.0, 0.0, start.x, start.y, start)]
        came_from: dict[Cell, Cell] = {}
        g_score: dict[Cell, float] = {start: 0.0}
        visited: set[Cell] = set()

        while open_heap:
            _, current_cost, _, _, current = heapq.heappop(open_heap)
            if current in visited:
                continue
            visited.add(current)
            if current == goal:
                break
            for neighbor in self.neighbors(current, blocked_cells, blocked_edges):
                step = 1.0 if (neighbor.x == current.x or neighbor.y == current.y) else 1.41421356237
                tentative = current_cost + step
                if tentative >= g_score.get(neighbor, float("inf")):
                    continue
                g_score[neighbor] = tentative
                came_from[neighbor] = current
                priority = tentative + abs(goal.x - neighbor.x) + abs(goal.y - neighbor.y)
                heapq.heappush(open_heap, (priority, tentative, neighbor.x, neighbor.y, neighbor))

        if goal != start and goal not in came_from:
            return None
        path = [goal]
        current = goal
        while current != start:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def neighbors(
        self,
        cell: Cell,
        blocked_cells: set[Cell],
        blocked_edges: set[tuple[Cell, Cell]],
    ) -> Iterable[Cell]:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nxt = Cell(cell.x + dx, cell.y + dy)
                if nxt not in self.floor_cells or nxt in blocked_cells:
                    continue
                if (cell, nxt) in blocked_edges:
                    continue
                if dx != 0 and dy != 0:
                    side_a = Cell(cell.x + dx, cell.y)
                    side_b = Cell(cell.x, cell.y + dy)
                    if side_a in blocked_cells or side_b in blocked_cells:
                        continue
                    if (cell, side_a) in blocked_edges or (cell, side_b) in blocked_edges:
                        continue
                yield nxt

    def blocked_cells(self, obstacles: list[ObstacleView]) -> set[Cell]:
        blocked: set[Cell] = set()
        for cell in self.floor_cells:
            center = self.cell_center(cell)
            for obstacle in obstacles:
                if not obstacle.solid or obstacle.kind not in SOLID_KINDS:
                    continue
                if self._point_in_obstacle(center, obstacle, PLAYER_MARGIN):
                    blocked.add(cell)
                    break
        return blocked

    def blocked_edges(self, obstacles: list[ObstacleView]) -> set[tuple[Cell, Cell]]:
        edges: set[tuple[Cell, Cell]] = set()
        for cell in self.floor_cells:
            center = self.cell_center(cell)
            for neighbor in self._raw_neighbors(cell):
                if neighbor not in self.floor_cells:
                    continue
                other = self.cell_center(neighbor)
                for obstacle in obstacles:
                    if not obstacle.solid or obstacle.kind not in SOLID_KINDS:
                        continue
                    if self.segment_hits_obstacle(center, other, obstacle, PLAYER_MARGIN):
                        edges.add((cell, neighbor))
                        break
        return edges

    def nearest_floor_cell(self, point: Vec2) -> Cell | None:
        best = None
        best_dist = float("inf")
        for cell in self.floor_cells:
            center = self.cell_center(cell)
            dist = hypot(center.x - point.x, center.y - point.y)
            if dist < best_dist:
                best_dist = dist
                best = cell
        return best

    def nearest_walkable_point(self, point: Vec2, obstacles: list[ObstacleView]) -> Vec2 | None:
        blocked = self.blocked_cells(obstacles)
        best = None
        best_dist = float("inf")
        for cell in self.floor_cells:
            if cell in blocked:
                continue
            center = self.cell_center(cell)
            dist = hypot(center.x - point.x, center.y - point.y)
            if dist < best_dist:
                best_dist = dist
                best = center
        return best

    def find_vantage_point(self, start: Vec2, enemy: Vec2, obstacles: list[ObstacleView]) -> Vec2:
        blocked = self.blocked_cells(obstacles)
        blocked_edges = self.blocked_edges(obstacles)
        start_cell = self.nearest_floor_cell(start)
        if start_cell is None:
            return enemy
        best = enemy
        best_score = float("inf")
        for cell in self.floor_cells:
            if cell in blocked:
                continue
            center = self.cell_center(cell)
            if not self.has_line_of_sight(center, enemy, obstacles, ignore_breakables=True):
                continue
            path = self.astar(start_cell, cell, blocked, blocked_edges)
            if not path:
                continue
            score = len(path) + center.distance_to(enemy) / TILE_SIZE
            if score < best_score:
                best_score = score
                best = center
        return best

    def has_line_of_sight(
        self,
        start: Vec2,
        target: Vec2,
        obstacles: list[ObstacleView],
        *,
        ignore_breakables: bool = False,
        ignored_kinds: set[str] | None = None,
    ) -> bool:
        ignored = ignored_kinds or set()
        for obstacle in obstacles:
            if not obstacle.solid or obstacle.kind not in SOLID_KINDS:
                continue
            if obstacle.kind in ignored:
                continue
            if ignore_breakables and obstacle.kind in BREAKABLE_KINDS:
                continue
            if self.segment_hits_obstacle(start, target, obstacle, 0.0):
                return False
        return True

    def first_blocker(
        self,
        start: Vec2,
        target: Vec2,
        obstacles: list[ObstacleView],
        *,
        ignored_kinds: set[str] | None = None,
    ) -> ObstacleView | None:
        ignored = ignored_kinds or set()
        best = None
        best_dist = float("inf")
        for obstacle in obstacles:
            if not obstacle.solid or obstacle.kind not in SOLID_KINDS or obstacle.kind in ignored:
                continue
            if not self.segment_hits_obstacle(start, target, obstacle, 0.0):
                continue
            dist = start.distance_to(obstacle.center)
            if dist < best_dist:
                best_dist = dist
                best = obstacle
        return best

    def cell_center(self, cell: Cell) -> Vec2:
        return Vec2((cell.x + 0.5) * TILE_SIZE, (cell.y + 0.5) * TILE_SIZE)

    def _raw_neighbors(self, cell: Cell) -> Iterable[Cell]:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                yield Cell(cell.x + dx, cell.y + dy)

    def _obstacle_signature(self, obstacles: list[ObstacleView]) -> tuple[int, ...]:
        return tuple(sorted(obstacle.obstacle_id for obstacle in obstacles if obstacle.solid and obstacle.kind in SOLID_KINDS))

    def _point_in_obstacle(self, point: Vec2, obstacle: ObstacleView, inflate: float) -> bool:
        return (
            obstacle.center.x - obstacle.half_size.x - inflate <= point.x <= obstacle.center.x + obstacle.half_size.x + inflate
            and obstacle.center.y - obstacle.half_size.y - inflate <= point.y <= obstacle.center.y + obstacle.half_size.y + inflate
        )

    def segment_hits_obstacle(self, start: Vec2, end: Vec2, obstacle: ObstacleView, inflate: float) -> bool:
        min_x = obstacle.center.x - obstacle.half_size.x - inflate
        max_x = obstacle.center.x + obstacle.half_size.x + inflate
        min_y = obstacle.center.y - obstacle.half_size.y - inflate
        max_y = obstacle.center.y + obstacle.half_size.y + inflate
        dx = end.x - start.x
        dy = end.y - start.y
        t0 = 0.0
        t1 = 1.0
        for p, q in ((-dx, start.x - min_x), (dx, max_x - start.x), (-dy, start.y - min_y), (dy, max_y - start.y)):
            if abs(p) <= 1e-9:
                if q < 0.0:
                    return False
                continue
            t = q / p
            if p < 0.0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
        return t0 <= t1 and (0.0 <= t0 <= 1.0 or 0.0 <= t1 <= 1.0)
