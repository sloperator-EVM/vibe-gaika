from __future__ import annotations

import heapq
import math
import os
import sys
from dataclasses import dataclass, field

from gaica_bot.models import (
    BotCommand,
    BotState,
    HelloMessage,
    ObstacleView,
    PickupView,
    RoundEndMessage,
    RoundStartMessage,
    TickMessage,
    Vec2,
)

class DummyPickup:
    pass

@dataclass(slots=True)
class SmartBot:
    """Advanced bot with True Physics Anti-Fall, Hitbox Anti-Trap, and Uzi Dynamics."""

    state: BotState = field(default_factory=BotState)
    last_enemy_pos: Vec2 | None = None
    last_tick_num: int = -1
    my_last_pos: Vec2 | None = None  # Для вычисления нашей инерции
    
    cell_size: float = 16.0
    floor_rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    pits_cells: set[tuple[int, int]] = field(default_factory=set)
    map_w: float = 1280.0
    map_h: float = 720.0
    map_initialized: bool = False
    pit_push_distance: float = 132.0
    pit_push_distance_glass: float = 108.0
    trace_enabled: bool = field(default_factory=lambda: os.getenv("GAICA_TRACE", "0") == "1")
    _last_trace_signature: tuple | None = None
    recent_positions: list[Vec2] = field(default_factory=list)
    stuck_window_ticks: int = 32
    stuck_radius: float = 18.0

    def on_hello(self, message: HelloMessage) -> None:
        self.state.hello = message

    def on_round_start(self, message: RoundStartMessage) -> None:
        self.state.current_round = message
        self.state.last_tick = None
        self.state.last_round_end = None
        self.state.command_seq = 0
        self.last_enemy_pos = None
        self.my_last_pos = None
        self.last_tick_num = -1
        self.floor_rects.clear()
        self.pits_cells.clear()
        self.map_initialized = False
        self._last_trace_signature = None
        self.recent_positions.clear()

    def on_round_end(self, message: RoundEndMessage) -> None:
        self.state.last_round_end = message

    # =========================================================================
    # ДИНАМИЧЕСКИЙ КАРТОГРАФ (Абсолютная защита границ)
    # =========================================================================
    def _init_map(self, message: TickMessage) -> None:
        if self.map_initialized: return
        
        level = getattr(message.snapshot, 'level', None)
        if not level: level = getattr(self.state.current_round, 'level', {})
        
        # Читаем РЕАЛЬНЫЕ размеры карты (исправлен баг с 448x448)
        w = getattr(level, 'width', None)
        if w is None and isinstance(level, dict): w = level.get('width', level.get('map_width'))
        h = getattr(level, 'height', None)
        if h is None and isinstance(level, dict): h = level.get('height', level.get('map_height'))
        
        self.map_w = float(w) if w else 1280.0
        self.map_h = float(h) if h else 720.0
        
        tiles = []
        for attr in ['floor_tiles', 'top_tiles', 'small_tiles']:
            t = level.get(attr, []) if isinstance(level, dict) else getattr(level, attr, [])
            tiles.extend(t)
            
        for tile in tiles:
            if isinstance(tile, dict):
                tx, ty, ts = float(tile.get('x', 0)), float(tile.get('y', 0)), float(tile.get('size', 64))
            else:
                tx, ty, ts = float(getattr(tile, 'x', 0)), float(getattr(tile, 'y', 0)), float(getattr(tile, 'size', 64))
            self.floor_rects.append((tx, ty, tx + ts, ty + ts))

        self.pits_cells.clear()
        max_cx = int(self.map_w // self.cell_size) + 1
        max_cy = int(self.map_h // self.cell_size) + 1
        
        for cx in range(-2, max_cx + 2):
            for cy in range(-2, max_cy + 2):
                px = cx * self.cell_size + self.cell_size / 2.0
                py = cy * self.cell_size + self.cell_size / 2.0
                # Если клетка не безопасна целиком - это яма
                if not self.is_safe_pos(px, py):
                    self.pits_cells.add((cx, cy))
                    
        self.map_initialized = True

    def is_safe_pos(self, x: float, y: float) -> bool:
        """Проверяет, поместится ли бот (радиус 10+1) в этой точке без падения"""
        r = 11.0 
        # 1. Жесткая граница карты
        if x < r or x > self.map_w - r or y < r or y > self.map_h - r:
            return False
            
        # 2. Если карта состоит из островов (floor_rects)
        if not self.floor_rects:
            return True
            
        # Проверяем все 4 края нашей модели
        corners = [(x, y), (x-r, y), (x+r, y), (x, y-r), (x, y+r)]
        for cx, cy in corners:
            corner_safe = False
            for min_x, min_y, max_x, max_y in self.floor_rects:
                if min_x <= cx <= max_x and min_y <= cy <= max_y:
                    corner_safe = True
                    break
            if not corner_safe:
                return False
        return True

    # =========================================================================
    # ГЕОМЕТРИЯ РУКОПАШНОГО БОЯ
    # =========================================================================
    def _check_pit_trajectory(self, start: Vec2, target: Vec2, dist: float, impassable: set) -> bool:
        direction = Vec2(target.x - start.x, target.y - start.y)
        if direction.length() < 1e-6: return False
        direction = direction.normalized()
        
        steps = int(dist / self.cell_size)
        for i in range(1, steps + 1):
            tx = target.x + direction.x * (i * self.cell_size)
            ty = target.y + direction.y * (i * self.cell_size)
            
            cx, cy = int(tx // self.cell_size), int(ty // self.cell_size)
            if (cx, cy) in self.pits_cells: return True 
            if (cx, cy) in impassable: return False 
        return False

    def _find_ideal_kick_pos(self, enemy_pos: Vec2, impassable: set) -> Vec2 | None:
        candidates = [
            Vec2(1,0), Vec2(-1,0), Vec2(0,1), Vec2(0,-1),
            Vec2(0.707, 0.707), Vec2(-0.707, 0.707), Vec2(0.707, -0.707), Vec2(-0.707, -0.707)
        ]
        for cand in candidates:
            start_mock = Vec2(enemy_pos.x - cand.x * 10, enemy_pos.y - cand.y * 10)
            if self._check_pit_trajectory(start_mock, enemy_pos, self.pit_push_distance, impassable):
                ideal_x = enemy_pos.x - cand.x * 26.0
                ideal_y = enemy_pos.y - cand.y * 26.0
                ix, iy = int(ideal_x // self.cell_size), int(ideal_y // self.cell_size)
                if (ix, iy) not in impassable and self.is_safe_pos(ideal_x, ideal_y):
                    return Vec2(ideal_x, ideal_y)
        return None

    def _has_glass_on_line(self, message: TickMessage, start: Vec2, end: Vec2) -> bool:
        for obstacle in message.snapshot.obstacles:
            if not getattr(obstacle, "solid", False):
                continue
            if getattr(obstacle, "kind", "").lower() != "glass":
                continue
            if self._segment_intersects_rect(start, end, obstacle):
                return True
        return False

    # =========================================================================
    # A* PATHFINDING (Защита от заталкивания в яму)
    # =========================================================================
    def _build_nav_grid(self, message: TickMessage) -> tuple[set, set, dict]:
        impassable = self.pits_cells.copy()
        dodge_impassable = self.pits_cells.copy()
        costs = {}
        
        for obs in message.snapshot.obstacles:
            if getattr(obs, 'solid', False):
                kind = getattr(obs, 'kind', '').lower()
                margin = 1.0
                min_x = int((obs.center.x - obs.half_size.x + margin) // self.cell_size)
                max_x = int((obs.center.x + obs.half_size.x - margin) // self.cell_size)
                min_y = int((obs.center.y - obs.half_size.y + margin) // self.cell_size)
                max_y = int((obs.center.y + obs.half_size.y - margin) // self.cell_size)
                
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        dodge_impassable.add((x, y))
                        if kind in ('box', 'glass', 'letterbox'): costs[(x, y)] = 50.0 
                        elif kind == 'door': costs[(x, y)] = 5.0
                        else: impassable.add((x, y))

        # АНТИ-КОРОБОЧКА: Запрещаем протискиваться между врагом и пропастью!
        enemy = message.enemy
        if enemy and enemy.alive:
            ecx, ecy = int(enemy.position.x // self.cell_size), int(enemy.position.y // self.cell_size)
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    check_c = (ecx + dx, ecy + dy)
                    dodge_impassable.add(check_c)
                    costs[check_c] = 200.0
                    
                    # Если эта клетка граничит с ямой - она СМЕРТЕЛЬНА, мы туда не идем
                    near_pit = False
                    for pdx, pdy in [(1,0), (-1,0), (0,1), (0,-1)]:
                        if (check_c[0] + pdx, check_c[1] + pdy) in self.pits_cells:
                            near_pit = True
                            break
                    if near_pit:
                        impassable.add(check_c)
                            
        return impassable, dodge_impassable, costs

    def _find_path_info(self, start: Vec2, target: Vec2, impassable: set, costs: dict) -> Vec2:
        start_cell = (int(start.x // self.cell_size), int(start.y // self.cell_size))
        target_cell = (int(target.x // self.cell_size), int(target.y // self.cell_size))

        impassable.discard(start_cell)
        impassable.discard(target_cell)
        if start_cell == target_cell: return target

        queue = [(0, start_cell)]
        came_from = {start_cell: None}
        cost_so_far = {start_cell: 0}
        iterations = 0

        while queue and iterations < 1500:
            iterations += 1
            _, current = heapq.heappop(queue)
            if current == target_cell: break

            cx, cy = current
            for nx, ny in [(cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1), (cx+1, cy+1), (cx-1, cy-1), (cx+1, cy-1), (cx-1, cy+1)]:
                next_cell = (nx, ny)
                if next_cell in impassable and next_cell != target_cell: continue
                if nx != cx and ny != cy and (nx, cy) in impassable and (cx, ny) in impassable: continue

                step_cost = (1.414 if nx != cx and ny != cy else 1.0) + costs.get(next_cell, 0.0)
                new_cost = cost_so_far[current] + step_cost

                if next_cell not in cost_so_far or new_cost < cost_so_far[next_cell]:
                    cost_so_far[next_cell] = new_cost
                    priority = new_cost + math.hypot(target_cell[0] - nx, target_cell[1] - ny)
                    heapq.heappush(queue, (priority, next_cell))
                    came_from[next_cell] = current

        if target_cell not in came_from:
            best_cell = start_cell
            best_dist = float('inf')
            for cell in came_from.keys():
                dist = math.hypot(cell[0] - target_cell[0], cell[1] - target_cell[1])
                if dist < best_dist:
                    best_dist = dist
                    best_cell = cell
            
            # Если до цели нет пути и она дальше 3 клеток - мы отрезаны (не жмемся к краю!)
            if best_dist > 3.0: return start
            if best_cell == start_cell: return start
            target_cell = best_cell

        current = target_cell
        path = []
        while current != start_cell:
            path.append(current)
            current = came_from[current]
        path.reverse()
        if not path: return start

        next_step = path[0]
        if len(path) > 1:
            c_x, c_y = next_step[0] * self.cell_size + self.cell_size / 2, next_step[1] * self.cell_size + self.cell_size / 2
            if math.hypot(start.x - c_x, start.y - c_y) < self.cell_size * 0.8:
                next_step = path[1]

        return Vec2(next_step[0] * self.cell_size + self.cell_size / 2, next_step[1] * self.cell_size + self.cell_size / 2)

    # =========================================================================
    # УКЛОНЕНИЕ ОТ ПУЛЬ
    # =========================================================================
    def _smart_dodge(self, message: TickMessage, dodge_impassable: set) -> Vec2:
        me = message.you
        threats: list[tuple[object, Vec2, float, float, float]] = []
        
        for p in message.snapshot.projectiles:
            if p.owner_id == me.player_id: continue
            to_me = Vec2(me.position.x - p.position.x, me.position.y - p.position.y)
            if to_me.length() > 320.0: continue 
            
            p_dir = p.velocity.normalized()
            ahead = to_me.x * p_dir.x + to_me.y * p_dir.y
            lateral = abs(to_me.x * (-p_dir.y) + to_me.y * p_dir.x)
            
            if -30.0 < ahead < 320.0 and lateral < 42.0:
                speed = max(1.0, p.velocity.length())
                tti = max(0.0, ahead) / speed
                threats.append((p, p_dir, ahead, lateral, tti))

        if not threats: return Vec2()

        best_move = Vec2(0.0, 0.0)
        best_score = -float('inf')
        
        candidates = [
            Vec2(0,0),
            Vec2(1,0), Vec2(-1,0), Vec2(0,1), Vec2(0,-1),
            Vec2(0.707, 0.707), Vec2(-0.707, 0.707), Vec2(0.707, -0.707), Vec2(-0.707, -0.707)
        ]

        for cand in candidates:
            # Проверяем позицию на нескольких горизонтах, а не только "следующий кадр".
            score = 0.0
            blocked = False
            for horizon in (18.0, 32.0, 50.0):
                test_x = me.position.x + cand.x * horizon
                test_y = me.position.y + cand.y * horizon
                cx, cy = int(test_x // self.cell_size), int(test_y // self.cell_size)

                if (cx, cy) in dodge_impassable or not self.is_safe_pos(test_x, test_y):
                    blocked = True
                    break

                min_lateral = float('inf')
                min_tti = 99.0
                for p, p_dir, _, _, _ in threats:
                    to_test = Vec2(test_x - p.position.x, test_y - p.position.y)
                    new_ahead = to_test.x * p_dir.x + to_test.y * p_dir.y
                    new_lateral = abs(to_test.x * (-p_dir.y) + to_test.y * p_dir.x)
                    if -30.0 < new_ahead < 320.0:
                        min_lateral = min(min_lateral, new_lateral)
                        speed = max(1.0, p.velocity.length())
                        min_tti = min(min_tti, max(0.0, new_ahead) / speed)

                if math.isinf(min_lateral):
                    min_lateral = 99.0
                score += min_lateral + min_tti * 120.0

            if blocked:
                continue

            # Небольшой штраф стоять на месте при угрозе.
            if cand.length() < 1e-6:
                score -= 10.0

            if score > best_score:
                best_score = score
                best_move = cand
                
        if best_score < 45.0 and threats:
            p, p_dir, _, _, _ = threats[0]
            to_me = Vec2(me.position.x - p.position.x, me.position.y - p.position.y)
            handedness = -1.0 if (to_me.x * (-p_dir.y) + to_me.y * p_dir.x) > 0 else 1.0
            return Vec2(-p_dir.y * handedness, p_dir.x * handedness).normalized()

        return best_move

    def _danger_from_bullets(self, message: TickMessage, pos: Vec2, horizon: float = 320.0) -> tuple[int, float]:
        danger = 0
        nearest_tti = 99.0
        me = message.you
        for p in message.snapshot.projectiles:
            if p.owner_id == me.player_id:
                continue
            to_pos = Vec2(pos.x - p.position.x, pos.y - p.position.y)
            if to_pos.length() > horizon:
                continue
            p_dir = p.velocity.normalized()
            ahead = to_pos.x * p_dir.x + to_pos.y * p_dir.y
            lateral = abs(to_pos.x * (-p_dir.y) + to_pos.y * p_dir.x)
            if -24.0 < ahead < horizon and lateral < 26.0:
                danger += 1
                nearest_tti = min(nearest_tti, max(0.0, ahead) / max(1.0, p.velocity.length()))
        return danger, nearest_tti

    def _is_stuck(self) -> bool:
        if len(self.recent_positions) < self.stuck_window_ticks:
            return False
        anchor = self.recent_positions[-1]
        max_dist = 0.0
        for point in self.recent_positions[-self.stuck_window_ticks:]:
            max_dist = max(max_dist, anchor.distance_to(point))
        return max_dist < self.stuck_radius

    # =========================================================================
    # ГЛАВНЫЙ ЦИКЛ ПРИНЯТИЯ РЕШЕНИЙ
    # =========================================================================
    def on_tick(self, message: TickMessage) -> BotCommand:
        self.state.last_tick = message
        self.state.command_seq += 1
        seq = self.state.command_seq

        self._init_map(message)

        me, enemy = message.you, message.enemy
        if not me.alive: return BotCommand(seq=seq)
        self.recent_positions.append(me.position)
        if len(self.recent_positions) > 90:
            self.recent_positions = self.recent_positions[-90:]

        # ВЫЧИСЛЕНИЕ СОБСТВЕННОЙ ИНЕРЦИИ
        my_vel = Vec2(0, 0)
        curr_tick = getattr(message, 'tick', seq)
        dt = max((curr_tick - self.last_tick_num) / 30.0, 0.001) if self.last_tick_num > 0 else 0.033
        
        if self.my_last_pos is not None:
            my_vel = Vec2((me.position.x - self.my_last_pos.x) / dt, (me.position.y - self.my_last_pos.y) / dt)
        self.my_last_pos = me.position

        # ВЫЧИСЛЕНИЕ СКОРОСТИ ВРАГА
        enemy_vel = Vec2(0, 0)
        if enemy.alive and self.last_enemy_pos is not None:
            enemy_vel = Vec2((enemy.position.x - self.last_enemy_pos.x) / dt, (enemy.position.y - self.last_enemy_pos.y) / dt)
        if enemy.alive:
            self.last_enemy_pos = enemy.position
            self.last_tick_num = curr_tick

        move, aim = Vec2(0.0, 0.0), Vec2(1.0, 0.0)
        shoot, kick, pickup, drop, throw_item = False, False, False, False, False

        to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y) if enemy.alive else Vec2()
        enemy_distance = to_enemy.length()
        enemy_dir = self._safe_direction(to_enemy, fallback=aim)
        bullet_danger, nearest_tti = self._danger_from_bullets(message, me.position)
        
        has_weapon = False
        low_ammo = False
        weapon_type = ""
        my_ammo = 0
        if me.weapon is not None and getattr(me.weapon, 'weapon_type', 'none').lower() != "none" and me.weapon.ammo > 0:
            has_weapon = True
            weapon_type = me.weapon.weapon_type.lower()
            my_ammo = me.weapon.ammo
            if "uzi" in weapon_type and my_ammo <= 6: low_ammo = True
            elif "revolver" in weapon_type and my_ammo <= 1: low_ammo = True

        enemy_w = getattr(enemy, 'weapon', None)
        enemy_w_type = getattr(enemy_w, 'weapon_type', 'none').lower() if enemy_w else 'none'
        enemy_armed = enemy_w_type != 'none' and getattr(enemy_w, 'ammo', 0) > 0

        impassable, dodge_impassable, costs = self._build_nav_grid(message)

        predict_target = enemy.position
        if enemy.alive:
            travel_t = enemy_distance / 500.0
            predict_target = Vec2(enemy.position.x + enemy_vel.x * travel_t, enemy.position.y + enemy_vel.y * travel_t)
        predict_dir = self._safe_direction(Vec2(predict_target.x - me.position.x, predict_target.y - me.position.y), fallback=enemy_dir)

        # === 1. УМНОЕ УКЛОНЕНИЕ ===
        dodge = self._smart_dodge(message, dodge_impassable)
        if dodge.length() > 0.0:
            move = dodge
            aim = predict_dir
            shoot = has_weapon and self._has_clear_shot(message, me.position, predict_target)
        else:
            nearby_pickup = self._best_pickup(message)
            clear_shot = enemy.alive and self._has_clear_shot(message, me.position, predict_target)
            if enemy.alive: aim = predict_dir

            # === 2. РУКОПАШНАЯ ===
            pit_dist = self.pit_push_distance
            if self._has_glass_on_line(message, me.position, enemy.position):
                pit_dist = self.pit_push_distance_glass
            we_can_pit = self._check_pit_trajectory(me.position, enemy.position, pit_dist, impassable)
            they_can_pit = self._check_pit_trajectory(enemy.position, me.position, pit_dist, impassable)
            ideal_kick_pos = self._find_ideal_kick_pos(enemy.position, impassable)
            
            melee_override = False

            if enemy.alive:
                if they_can_pit and enemy_distance <= 60.0:
                    dodge_dir = self._strafe(enemy_dir, 1.0)
                    if enemy_distance < 35.0: move = self._blend(Vec2(-to_enemy.x, -to_enemy.y).normalized(), dodge_dir, 0.6)
                    else: move = dodge_dir
                    melee_override = True
                    
                # Бьем, только если подошли вплотную
                elif we_can_pit and enemy_distance <= 26.0:
                    if me.kick_cooldown <= 0.05: kick = True
                    move = enemy_dir
                    melee_override = True
                    
                elif not has_weapon and ideal_kick_pos is not None and enemy_distance <= 50.0:
                    dist_to_pickup = float('inf')
                    if nearby_pickup: dist_to_pickup = me.position.distance_to(getattr(nearby_pickup, 'position', me.position))
                    
                    if dist_to_pickup > 60.0:
                        to_ideal = Vec2(ideal_kick_pos.x - me.position.x, ideal_kick_pos.y - me.position.y)
                        if to_ideal.length() > 4.0:
                            wp = self._find_path_info(me.position, ideal_kick_pos, impassable.copy(), costs)
                            move = Vec2(wp.x - me.position.x, wp.y - me.position.y).normalized()
                        else:
                            move = enemy_dir
                        melee_override = True

            if enemy.alive and enemy_distance <= 26.0 and me.kick_cooldown <= 0.05:
                kick = True
                if not has_weapon and not melee_override and nearby_pickup is None:
                    move = enemy_dir
                    melee_override = True

            # Если враг буквально "внутри" нас и пытается выстрелить - сразу отталкиваем.
            if enemy.alive and enemy_armed and enemy_distance <= 20.0:
                enemy_facing = self._safe_direction(enemy.facing, fallback=enemy_dir)
                facing_dot = enemy_facing.x * (-enemy_dir.x) + enemy_facing.y * (-enemy_dir.y)
                if facing_dot > 0.55:
                    if me.kick_cooldown <= 0.05:
                        kick = True
                    move = self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), self._strafe(enemy_dir, 1.0), 0.45)
                    melee_override = True

            # === 3. НАВИГАЦИЯ И ПАТРОНЫ ===
            if not melee_override:
                if not has_weapon or low_ammo:
                    if nearby_pickup is not None:
                        target_pos = getattr(nearby_pickup, 'position', None)
                        if target_pos:
                            wp = self._find_path_info(me.position, target_pos, impassable.copy(), costs)
                            to_pickup_dir = Vec2(wp.x - me.position.x, wp.y - me.position.y).normalized()
                            
                            if has_weapon and enemy.alive:
                                combat_move = self._distance_control_move(enemy_dir, enemy_distance, 90.0, 0.7, seq)
                                move = self._blend(to_pickup_dir, combat_move, 0.4)
                            else: move = to_pickup_dir
                            
                            if me.position.distance_to(target_pos) <= 22.0: pickup = True
                    elif enemy.alive and not has_weapon:
                        # В "сумо" без оружия играем заметно пассивнее.
                        desired = 82.0 if not enemy_armed else 72.0
                        
                        wp = self._find_path_info(me.position, enemy.position, impassable.copy(), costs)
                        to_wp = Vec2(wp.x - me.position.x, wp.y - me.position.y)
                        nav_dir = self._safe_direction(to_wp, fallback=Vec2(0,0))
                        
                        if nav_dir.length() > 0.1:
                            move = self._distance_control_move(nav_dir, enemy_distance, desired, 0.2, seq)
                        else:
                            st = self._strafe(enemy_dir, handedness=1.0 if (seq // 18) % 2 == 0 else -1.0)
                            move = Vec2(st.x * 0.2, st.y * 0.2)

                if has_weapon and move.length() < 0.1: 
                    preferred_distance = 90.0
                    if "uzi" in weapon_type:
                        if "revolver" in enemy_w_type and getattr(enemy, 'shoot_cooldown', 0.0) > 0.1:
                            preferred_distance = 25.0
                        else:
                            preferred_distance = 60.0 if not low_ammo else 45.0
                    elif "revolver" in weapon_type:
                        if "uzi" in enemy_w_type: preferred_distance = 220.0
                        else: preferred_distance = 150.0

                    if not clear_shot and enemy.alive and enemy_distance > 100.0:
                        wp = self._find_path_info(me.position, enemy.position, impassable.copy(), costs)
                        to_wp = Vec2(wp.x - me.position.x, wp.y - me.position.y)
                        move = self._safe_direction(to_wp, fallback=Vec2(0,0))
                    else:
                        move = self._distance_control_move(enemy_dir, enemy_distance, preferred_distance, 0.75, seq)
                    
                    if nearby_pickup is not None and self._should_upgrade_weapon(weapon_type, my_ammo, nearby_pickup):
                        target_pos = getattr(nearby_pickup, 'position', None)
                        if target_pos:
                            wp = self._find_path_info(me.position, target_pos, impassable.copy(), costs)
                            to_wp = Vec2(wp.x - me.position.x, wp.y - me.position.y)
                            nav_dir = self._safe_direction(to_wp, fallback=Vec2(0,0))
                            if me.position.distance_to(target_pos) <= 20.0: pickup = True
                            elif enemy_distance > 60.0: move = nav_dir

            # === 4. СТРЕЛЬБА ===
            if not kick:
                if has_weapon and enemy.alive and clear_shot:
                    max_shoot_dist = 220.0 if "uzi" in weapon_type else 350.0
                    if low_ammo and "uzi" in weapon_type: max_shoot_dist = 90.0 
                    if enemy_distance <= max_shoot_dist and me.shoot_cooldown <= 0.05: shoot = True
                elif enemy.alive and has_weapon and clear_shot:
                    flank = self._strafe(enemy_dir, handedness=-1.0 if seq % 2 else 1.0)
                    move = self._blend(move, flank, 0.45)

                # Не дропаем оружие автоматически, чтобы не ловить цикл "подобрал -> выкинул".
                if enemy_distance <= 18.0 and enemy_armed and me.weapon is not None and me.weapon.ammo == 1:
                    throw_item = True
                    shoot = False

            # === 5. ВАНДАЛИЗМ ===
            should_vandalize = has_weapon and ((not enemy.alive) or (enemy_distance > 90.0 and not enemy_armed))
            if should_vandalize and move.length() > 0.1 and not kick and not shoot and not pickup:
                move_dir = move.normalized()
                for obs in message.snapshot.obstacles:
                    if getattr(obs, 'solid', False) and getattr(obs, 'kind', '').lower() in ("box", "glass", "letterbox"):
                        if me.position.distance_to(obs.center) < 34.0:
                            to_obs_dir = Vec2(obs.center.x - me.position.x, obs.center.y - me.position.y).normalized()
                            if (move_dir.x * to_obs_dir.x + move_dir.y * to_obs_dir.y) > 0.5:
                                if getattr(obs, 'kind', '').lower() == 'glass' and not has_weapon: continue
                                if getattr(obs, 'kind', '').lower() in ("box", "glass") and has_weapon: shoot = True
                                else: kick = True
                                aim = to_obs_dir
                                pickup = False
                                break

            # === 5.1 АНТИ-ЗАСТРЕВАНИЕ: если стоим на месте слишком долго, активно ломаем путь ===
            if self._is_stuck() and bullet_danger == 0 and nearest_tti > 0.35 and not kick and not shoot:
                nearest_breakable = None
                nearest_dist = float("inf")
                for obs in message.snapshot.obstacles:
                    if not getattr(obs, "solid", False):
                        continue
                    kind = getattr(obs, "kind", "").lower()
                    if kind not in ("box", "glass", "letterbox"):
                        continue
                    dist = me.position.distance_to(obs.center)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest_breakable = obs

                if nearest_breakable is not None and nearest_dist < 55.0:
                    to_obs = Vec2(nearest_breakable.center.x - me.position.x, nearest_breakable.center.y - me.position.y)
                    aim = self._safe_direction(to_obs, fallback=aim)
                    if has_weapon and me.shoot_cooldown <= 0.05:
                        shoot = True
                    elif me.kick_cooldown <= 0.05:
                        kick = True
                    move = aim
                else:
                    # Если рядом ломать нечего — агрессивно выходим из "залипания" в сторону врага.
                    if enemy.alive:
                        move = self._blend(enemy_dir, self._strafe(enemy_dir, 1.0), 0.3)

        # === 6. ABS ТОРМОЗА И ФИНАЛЬНЫЙ ФИЛЬТР ЯМ ===
        if move.length() > 1e-6:
            if move.length() > 1.0: move = move.normalized()
            
            # Прогнозируем положение через 0.25 секунд с учетом нашей текущей скорости
            lookahead_t = 0.25
            tx = me.position.x + my_vel.x * lookahead_t + move.x * 20.0
            ty = me.position.y + my_vel.y * lookahead_t + move.y * 20.0
            
            if not self.is_safe_pos(tx, ty):
                safe_x = self.is_safe_pos(me.position.x + move.x * 20.0, me.position.y)
                safe_y = self.is_safe_pos(me.position.x, me.position.y + move.y * 20.0)
                
                if safe_x and not safe_y: move = Vec2(move.x, 0.0)
                elif safe_y and not safe_x: move = Vec2(0.0, move.y)
                else: 
                    # ЭКСТРЕННЫЙ РЕВЕРС (ABS): Давим в сторону, противоположную инерции!
                    if my_vel.length() > 20.0:
                        move = Vec2(-my_vel.x, -my_vel.y).normalized()
                    else:
                        move = Vec2(0.0, 0.0)
        else:
            move = Vec2(0.0, 0.0)

        if aim.length() < 1e-6: aim = Vec2(1.0, 0.0)
        if kick: shoot = False
        if pickup or drop or throw_item: kick = False

        command = BotCommand(
            seq=seq,
            move=move,
            aim=aim,
            shoot=shoot,
            kick=kick,
            pickup=pickup,
            drop=drop,
            throw_item=throw_item,
        )
        self._trace_decision(message=message, command=command, has_weapon=has_weapon, enemy_distance=enemy_distance)
        return command

    def _trace_decision(self, message: TickMessage, command: BotCommand, has_weapon: bool, enemy_distance: float) -> None:
        if not self.trace_enabled:
            return
        tick = int(getattr(message, "tick", 0))
        action_sig = (
            round(command.move.x, 1),
            round(command.move.y, 1),
            bool(command.shoot),
            bool(command.kick),
            bool(command.pickup),
            bool(command.throw_item),
            bool(has_weapon),
            int(enemy_distance // 10),
        )
        if self._last_trace_signature == action_sig and tick % 20 != 0:
            return
        self._last_trace_signature = action_sig
        print(
            f"[trace] tick={tick} dist={enemy_distance:.1f} "
            f"move=({command.move.x:.2f},{command.move.y:.2f}) "
            f"shoot={int(command.shoot)} kick={int(command.kick)} "
            f"pickup={int(command.pickup)} throw={int(command.throw_item)}",
            file=sys.stderr,
        )

    # --- Вспомогательные методы ---
    def _safe_direction(self, vector: Vec2, fallback: Vec2) -> Vec2:
        if vector.length() <= 1e-6: return fallback.normalized() if fallback.length() > 1e-6 else Vec2(0.0, 0.0)
        return vector.normalized()

    def _best_pickup(self, message: TickMessage) -> PickupView | DummyPickup | None:
        me = message.you
        enemy = message.enemy
        best_pickup = None
        best_score = float("inf")
        
        enemy_w = getattr(enemy, 'weapon', None)
        enemy_type = getattr(enemy_w, 'weapon_type', 'none').lower() if enemy_w else 'none'
        enemy_ammo = getattr(enemy_w, 'ammo', 0) if enemy_w else 0
        enemy_wants_loot = enemy_type == 'none' or enemy_ammo < 8
        
        for pickup in message.snapshot.pickups:
            if pickup.cooldown > 0.0: continue
            if pickup.ammo <= 0: continue
                
            dist = me.position.distance_to(pickup.position)
            score = dist
            
            if enemy.alive:
                enemy_dist = enemy.position.distance_to(pickup.position)
                if enemy_dist < dist - 5.0: 
                    if enemy_wants_loot or pickup.weapon_type.lower() == 'revolver':
                        score += 800.0 
            
            if pickup.weapon_type.lower() == "revolver": score -= 30.0 
            score -= min(30.0, pickup.ammo * 2.0)
            
            if score < best_score:
                best_score, best_pickup = score, pickup
                
        if best_pickup is None or best_score > 400.0:
            if me.weapon is None or getattr(me.weapon, 'weapon_type', 'none').lower() == "none" or me.weapon.ammo <= 0:
                best_box_score = float("inf")
                best_box = None
                for obs in message.snapshot.obstacles:
                    if getattr(obs, 'solid', False) and getattr(obs, 'kind', '').lower() == 'letterbox':
                        box_dist = me.position.distance_to(obs.center)
                        box_score = box_dist + 50.0 
                        
                        if enemy.alive:
                            enemy_box_dist = enemy.position.distance_to(obs.center)
                            if enemy_box_dist < box_dist - 5.0 and enemy_wants_loot:
                                box_score += 800.0
                                
                        if box_score < best_box_score:
                            best_box_score, best_box = box_score, obs
                            
                if best_box is not None and best_box_score < best_score:
                    best_score = best_box_score
                    best_pickup = DummyPickup()
                    best_pickup.position = best_box.center
                    best_pickup.weapon_type = "none"
                    best_pickup.ammo = 0
                    best_pickup.cooldown = 0.0

        return best_pickup

    def _should_upgrade_weapon(self, current_weapon_type: str, current_ammo: int, pickup) -> bool:
        current_key, pickup_key = current_weapon_type.lower(), getattr(pickup, 'weapon_type', 'none').lower()
        if current_key == "none": return True
        if current_ammo < 5 and getattr(pickup, 'ammo', 0) >= 10: return True
        return pickup_key == current_key and getattr(pickup, 'ammo', 0) > 0

    def _distance_control_move(self, enemy_dir: Vec2, distance: float, preferred: float, strafe_bias: float, seq: int) -> Vec2:
        strafe = self._strafe(enemy_dir, handedness=1.0 if (seq // 18) % 2 == 0 else -1.0)
        if distance > preferred + 18.0: return self._blend(enemy_dir, strafe, strafe_bias)
        if distance < preferred - 24.0: return self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), strafe, strafe_bias)
        return strafe

    def _has_clear_shot(self, message: TickMessage, start: Vec2, target: Vec2) -> bool:
        for obstacle in message.snapshot.obstacles:
            if not getattr(obstacle, 'solid', False): continue
            kind = getattr(obstacle, 'kind', '').lower()
            if kind not in {"wall", "door", "glass", "box", "letterbox"}: continue
            if self._segment_intersects_rect(start, target, obstacle):
                if kind in {"glass", "box"} and start.distance_to(obstacle.center) < 150.0: continue
                return False
        return True

    def _segment_intersects_rect(self, start: Vec2, end: Vec2, obstacle: ObstacleView) -> bool:
        min_x, max_x = obstacle.center.x - obstacle.half_size.x, obstacle.center.x + obstacle.half_size.x
        min_y, max_y = obstacle.center.y - obstacle.half_size.y, obstacle.center.y + obstacle.half_size.y
        dx, dy = end.x - start.x, end.y - start.y
        t0, t1 = 0.0, 1.0
        for p, q in ((-dx, start.x - min_x), (dx, max_x - start.x), (-dy, start.y - min_y), (dy, max_y - start.y)):
            if abs(p) <= 1e-9:
                if q < 0.0: return False
                continue
            t = q / p
            if p < 0.0:
                if t > t1: return False
                t0 = max(t0, t)
            else:
                if t < t0: return False
                t1 = min(t1, t)
        return t0 <= t1 and (0.02 <= t0 <= 0.98 or 0.02 <= t1 <= 0.98)

    def _strafe(self, direction: Vec2, handedness: float) -> Vec2:
        d = direction.normalized() if direction.length() > 1e-6 else Vec2(1.0, 0.0)
        return Vec2(-d.y * handedness, d.x * handedness)

    def _blend(self, primary: Vec2, secondary: Vec2, secondary_weight: float) -> Vec2:
        w2 = max(0.0, min(1.0, secondary_weight))
        w1 = 1.0 - w2
        return Vec2(primary.x * w1 + secondary.x * w2, primary.y * w1 + secondary.y * w2).normalized()
