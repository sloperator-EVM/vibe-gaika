from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from gaica_bot.models import (
    BotCommand,
    BotState,
    HelloMessage,
    PickupView,
    RoundEndMessage,
    RoundStartMessage,
    TickMessage,
    Vec2,
)

@dataclass(slots=True)
class SampleBot:
    state: BotState = field(default_factory=BotState)

    def on_hello(self, message: HelloMessage) -> None:
        self.state.hello = message

    def on_round_start(self, message: RoundStartMessage) -> None:
        self.state.current_round = message
        self.state.last_tick = None
        self.state.last_round_end = None

    def on_round_end(self, message: RoundEndMessage) -> None:
        self.state.last_round_end = message

    # --- ПОДСИСТЕМА 1: УКЛОНЕНИЕ С ДЕТЕКТОРОМ УКРЫТИЙ ---
    def _calculate_evasion(self, message: TickMessage, impassable: set[tuple[int, int]], cell_size: float) -> Vec2:
        me = message.you
        evade_x, evade_y, danger_count = 0.0, 0.0, 0

        for proj in message.snapshot.projectiles:
            if proj.owner_id == me.player_id: continue 

            to_me_x, to_me_y = me.position.x - proj.position.x, me.position.y - proj.position.y
            dist_to_proj = math.hypot(to_me_x, to_me_y)

            if dist_to_proj > 250.0: continue
            
            # ИСПРАВЛЕНИЕ 1: Проверяем укрытие! 
            # Если между нами и пулей есть стена или ящик, игнорируем пулю.
            los = self._has_line_of_sight(proj.position, me.position, message)
            if los is not True: 
                continue # Пуля встрянет в преграду, мы в безопасности!

            proj_dir = proj.velocity.normalized()
            dot_product = to_me_x * proj_dir.x + to_me_y * proj_dir.y
            cross = to_me_x * proj_dir.y - to_me_y * proj_dir.x

            if dot_product > 0 and abs(cross) < 36.0:
                push_x = -proj_dir.y if cross > 0 else proj_dir.y
                push_y = proj_dir.x if cross > 0 else -proj_dir.x
                
                test_x = me.position.x + push_x * 24.0
                test_y = me.position.y + push_y * 24.0
                if (int(test_x // cell_size), int(test_y // cell_size)) not in impassable:
                    weight = 1.0 / max(dist_to_proj, 1.0)
                    evade_x += push_x * weight
                    evade_y += push_y * weight
                    danger_count += 1

        return Vec2(evade_x, evade_y).normalized() if danger_count > 0 else Vec2(0.0, 0.0)

    # --- ПОДСИСТЕМА 2: ЛУЧ ЗРЕНИЯ ---
    def _has_line_of_sight(self, start: Vec2, end: Vec2, message: TickMessage) -> bool | str:
        dir_x, dir_y = end.x - start.x, end.y - start.y
        if math.hypot(dir_x, dir_y) < 1e-6: return True
        hit_breakable = False

        for obs in message.snapshot.obstacles:
            if not getattr(obs, 'solid', False): continue
            
            min_x, max_x = obs.center.x - obs.half_size.x, obs.center.x + obs.half_size.x
            min_y, max_y = obs.center.y - obs.half_size.y, obs.center.y + obs.half_size.y
            tmin, tmax, intersect = 0.0, 1.0, True

            for origin, direction, slab_min, slab_max in [
                (start.x, dir_x, min_x, max_x), (start.y, dir_y, min_y, max_y)
            ]:
                if abs(direction) < 1e-9:
                    if origin < slab_min or origin > slab_max: intersect = False; break
                    continue
                t1, t2 = (slab_min - origin) / direction, (slab_max - origin) / direction
                if t1 > t2: t1, t2 = t2, t1
                tmin, tmax = max(tmin, t1), min(tmax, t2)
                if tmin > tmax: intersect = False; break

            if intersect and tmin < 1.0 and tmax > 0.0:
                kind = getattr(obs, 'kind', '').lower()
                if kind in ('glass', 'box'): hit_breakable = True
                else: return False 

        return 'breakable' if hit_breakable else True

    # --- ПОДСИСТЕМА 3: A* НАВИГАТОР С ВЕСАМИ И СТРАХОВКОЙ ---
    def _build_nav_grid(self, message: TickMessage, cell_size: float) -> tuple[set, dict, set]:
        impassable = set()
        costs = {}
        pits = set()
        
        level_name = str(getattr(getattr(self.state.current_round, 'level', None), 'name', '')).lower()
        if 'level_3' in level_name:
            for x in range(int(360 // cell_size), int(920 // cell_size)):
                for y in range(int(200 // cell_size), int(520 // cell_size)):
                    pits.add((x, y))
            for x in range(0, int(1280 // cell_size) + 1):
                for y in range(0, int(720 // cell_size) + 1):
                    if x < int(220 // cell_size) or x > int(1060 // cell_size) or y < int(80 // cell_size) or y > int(640 // cell_size):
                        pits.add((x, y))

        for obs in message.snapshot.obstacles:
            if getattr(obs, 'solid', False):
                kind = getattr(obs, 'kind', '').lower()
                
                margin = 1.0
                min_x = int((obs.center.x - obs.half_size.x + margin) // cell_size)
                max_x = int((obs.center.x + obs.half_size.x - margin) // cell_size)
                min_y = int((obs.center.y - obs.half_size.y + margin) // cell_size)
                max_y = int((obs.center.y + obs.half_size.y - margin) // cell_size)
                
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        if kind == 'box': costs[(x, y)] = 500.0 
                        elif kind == 'glass': costs[(x, y)] = 15.0
                        elif kind == 'door': costs[(x, y)] = 5.0 
                        else: impassable.add((x, y))
                            
        return impassable | pits, costs, pits

    def _find_path_info(self, start: Vec2, target: Vec2, impassable: set[tuple[int, int]], costs: dict, cell_size: float) -> tuple[Vec2, float]:
        start_cell = (int(start.x // cell_size), int(start.y // cell_size))
        target_cell = (int(target.x // cell_size), int(target.y // cell_size))

        impassable.discard(start_cell)
        impassable.discard(target_cell)

        if start_cell == target_cell: return target, 0.0

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
                if nx < 0 or ny < 0 or nx > 150 or ny > 150: continue
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

        if target_cell not in came_from: return target, float('inf')

        current = target_cell
        path = []
        while current != start_cell:
            path.append(current)
            current = came_from[current]
        path.reverse()
        if not path: return target, 0.0

        next_step = path[0]
        if len(path) > 1:
            c_x, c_y = next_step[0] * cell_size + cell_size / 2, next_step[1] * cell_size + cell_size / 2
            if math.hypot(start.x - c_x, start.y - c_y) < cell_size * 0.8:
                next_step = path[1]

        return Vec2(next_step[0] * cell_size + cell_size / 2, next_step[1] * cell_size + cell_size / 2), cost_so_far[target_cell]

    # --- ГЛАВНЫЙ ЦИКЛ ---
    def on_tick(self, message: TickMessage) -> BotCommand:
        self.state.last_tick = message
        seq = self.state.next_command_seq()

        me, enemy = message.you, message.enemy
        if not me.alive: return BotCommand(seq=seq)

        move, aim = Vec2(0.0, 0.0), Vec2(1.0, 0.0)
        shoot, do_pickup, kick, throw_item = False, False, False, False 
        
        cell_size = 16.0
        impassable, costs, pits = self._build_nav_grid(message, cell_size)

        has_w = False
        is_weak_w = False
        if me.weapon is not None and getattr(me.weapon, 'weapon_type', 'none').lower() != "none":
            if getattr(me.weapon, 'ammo', 1) > 0: 
                has_w = True
                w_type = getattr(me.weapon, 'weapon_type', '').lower()
                if 'revolver' in w_type or 'pistol' in w_type or 'handgun' in w_type:
                    is_weak_w = True
            else: 
                throw_item = True 

        to_enemy_direct = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y) if enemy.alive else Vec2()
        dist_to_enemy = to_enemy_direct.length()

        # =========================================================================
        # ЭТАП 1: СМАРТ-ЛУТЕР
        # =========================================================================
        target_pos = None
        has_los = self._has_line_of_sight(me.position, enemy.position, message) if enemy.alive else False
        
        want_upgrade = False
        if not has_w: want_upgrade = True
        elif is_weak_w and not (enemy.alive and has_los and dist_to_enemy < 250.0): want_upgrade = True

        if want_upgrade:
            best_score = float('inf')
            for pickup in message.snapshot.pickups:
                if getattr(pickup, 'ammo', 1) <= 0: continue
                p_type = getattr(pickup, 'weapon_type', '').lower()
                is_strong = not ('revolver' in p_type or 'pistol' in p_type or 'handgun' in p_type)
                if has_w and is_weak_w and not is_strong: continue
                
                euclid = math.hypot(me.position.x - pickup.position.x, me.position.y - pickup.position.y)
                if euclid > 600.0: continue
                
                wp, path_cost = self._find_path_info(me.position, pickup.position, impassable.copy(), costs, cell_size)
                score = path_cost
                if is_strong: score -= 150.0 
                
                if score < best_score:
                    best_score, target_pos = score, pickup.position
                    if euclid <= 32.0: do_pickup = True

            if best_score > 0: 
                for obs in message.snapshot.obstacles:
                    if getattr(obs, 'solid', False) and getattr(obs, 'kind', '').lower() == 'letterbox':
                        nx = max(obs.center.x - obs.half_size.x, min(me.position.x, obs.center.x + obs.half_size.x))
                        ny = max(obs.center.y - obs.half_size.y, min(me.position.y, obs.center.y + obs.half_size.y))
                        euclid = math.hypot(me.position.x - nx, me.position.y - ny)
                        if euclid > 80.0: continue 
                        
                        wp, path_cost = self._find_path_info(me.position, Vec2(nx, ny), impassable.copy(), costs, cell_size)
                        score = path_cost - 50.0 
                        if score < best_score:
                            best_score, target_pos = score, Vec2(nx, ny)
                            do_pickup = False

            if has_w and is_weak_w and best_score < 0:
                throw_item = True
                has_w = False
                
            if target_pos is None and enemy.alive: target_pos = enemy.position
        else:
            if enemy.alive:
                if dist_to_enemy < 120.0 and has_los is True:
                    move = Vec2(-to_enemy_direct.x, -to_enemy_direct.y).normalized() 
                elif dist_to_enemy > 160.0 or has_los is False:
                    target_pos = enemy.position

        # =========================================================================
        # ЭТАП 2: НАВИГАЦИЯ И УКЛОНЕНИЕ
        # =========================================================================
        is_evading = False
        evade_move = self._calculate_evasion(message, impassable, cell_size)
        if evade_move.length() > 0:
            move = evade_move
            is_evading = True
        elif target_pos is not None and move.length() < 0.1:
            wp, _ = self._find_path_info(me.position, target_pos, impassable, costs, cell_size)
            way_dir = Vec2(wp.x - me.position.x, wp.y - me.position.y)
            if way_dir.length() > 1e-6:
                norm_dir = way_dir.normalized()
                if has_w and enemy.alive and has_los is False and dist_to_enemy < 250.0:
                    move = Vec2(norm_dir.x * 0.3, norm_dir.y * 0.3)
                    aim = norm_dir 
                else:
                    move = norm_dir

        # =========================================================================
        # ЭТАП 3: БОЙ
        # =========================================================================
        if enemy.alive:
            if dist_to_enemy <= 28.0:
                kick = True
                forward = Vec2(to_enemy_direct.x / max(dist_to_enemy, 1e-6), to_enemy_direct.y / max(dist_to_enemy, 1e-6))
                aim = forward
                if not is_evading:
                    strafe = Vec2(-forward.y, forward.x)
                    move = Vec2(forward.x * 0.6 + strafe.x * 0.4, forward.y * 0.6 + strafe.y * 0.4).normalized()
            elif has_w and has_los in (True, 'breakable'):
                aim = Vec2(to_enemy_direct.x / max(dist_to_enemy, 1e-6), to_enemy_direct.y / max(dist_to_enemy, 1e-6))
                if not is_evading: shoot = True

        # =========================================================================
        # ЭТАП 4: ИСПРАВЛЕННЫЙ ВАНДАЛИЗМ (СТРОГАЯ ГЕОМЕТРИЯ)
        # =========================================================================
        if not kick and not shoot and move.length() > 0.1 and not is_evading and not do_pickup:
            for obs in message.snapshot.obstacles:
                kind = getattr(obs, 'kind', '').lower()
                if getattr(obs, 'solid', False) and kind in ('glass', 'box', 'letterbox'):
                    nx = max(obs.center.x - obs.half_size.x, min(me.position.x, obs.center.x + obs.half_size.x))
                    ny = max(obs.center.y - obs.half_size.y, min(me.position.y, obs.center.y + obs.half_size.y))
                    
                    # ИСПРАВЛЕНИЕ 2: Дистанция срабатывания уменьшена (бот должен почти тереться об коробку)
                    if math.hypot(me.position.x - nx, me.position.y - ny) <= 20.0:
                        to_obs = Vec2(nx - me.position.x, ny - me.position.y)
                        obs_dist = to_obs.length()
                        is_target = (target_pos is not None and abs(target_pos.x - obs.center.x) < 1.0 and abs(target_pos.y - obs.center.y) < 1.0)
                        
                        if obs_dist > 1e-6:
                            dir_to_obs = Vec2(to_obs.x / obs_dist, to_obs.y / obs_dist)
                            move_dir = move.normalized()
                            
                            # ИСПРАВЛЕНИЕ 3: Угол увеличен до 0.92 (около 23 градусов). 
                            # Бот ударит только если упрется в ящик прямо лбом. Скользящие удары отменены.
                            if (move_dir.x * dir_to_obs.x + move_dir.y * dir_to_obs.y > 0.92) or is_target:
                                if kind in ('glass', 'box') and has_w: shoot = True
                                else: kick = True
                                
                                aim = dir_to_obs
                                move = Vec2(0.0, 0.0) 
                                break

        if not kick and not shoot and enemy.alive and move.length() > 1e-6:
            if not (has_w and has_los is False and dist_to_enemy < 250.0):
                aim = move.normalized()

        # Страховка Сумо
        if move.length() > 0.01:
            next_pos = Vec2(me.position.x + move.x * 16.0, me.position.y + move.y * 16.0)
            if (int(next_pos.x // cell_size), int(next_pos.y // cell_size)) in pits:
                move = Vec2(0.0, 0.0)

        return BotCommand(seq=seq, move=move, aim=aim, shoot=shoot, kick=kick, pickup=do_pickup, throw_item=throw_item)