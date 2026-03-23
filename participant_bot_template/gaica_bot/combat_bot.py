from __future__ import annotations

from dataclasses import dataclass, field

from gaica_bot.models import (
    BotCommand,
    BotState,
    HelloMessage,
    PickupView,
    ProjectileView,
    RoundEndMessage,
    RoundStartMessage,
    TickMessage,
    Vec2,
)
from gaica_bot.navigator import BREAKABLE_KINDS, Cell, Navigator


DOOR_KINDS = {"door"}
KICK_STEAL_RANGE = 42.0
KICK_WALL_ABUSE_RANGE = 44.0
DISARMED_SHOT_RANGE = 110.0


@dataclass(slots=True)
class CombatBot:
    """Improved rule-based bot with pathfinding, breakable handling, and better positioning."""

    state: BotState = field(default_factory=BotState)
    navigator: Navigator | None = None
    _last_position: Vec2 = field(default_factory=Vec2)
    _stuck_ticks: int = 0
    _goal: Vec2 | None = None

    def on_hello(self, message: HelloMessage) -> None:
        self.state.hello = message

    def on_round_start(self, message: RoundStartMessage) -> None:
        self.state.current_round = message
        self.state.last_tick = None
        self.state.last_round_end = None
        self.state.command_seq = 0
        self.navigator = Navigator.from_floor_tiles(message.level.floor_tiles)
        self._last_position = Vec2()
        self._stuck_ticks = 0
        self._goal = None

    def on_round_end(self, message: RoundEndMessage) -> None:
        self.state.last_round_end = message

    def on_tick(self, message: TickMessage) -> BotCommand:
        self.state.last_tick = message
        seq = self.state.next_command_seq()
        me = message.you
        enemy = message.enemy
        if not me.alive:
            return BotCommand(seq=seq)

        self._update_stuck_state(me.position)
        to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y)
        enemy_dir = self._safe_direction(to_enemy, fallback=me.facing if me.facing.length() > 0 else Vec2(1.0, 0.0))
        enemy_distance = to_enemy.length()
        has_weapon = me.weapon is not None and me.weapon.weapon_type.lower() != "none" and me.weapon.ammo > 0
        needs_drop = me.weapon is not None and me.weapon.weapon_type.lower() != "none" and me.weapon.ammo <= 0
        enemy_has_weapon = enemy.weapon is not None and enemy.weapon.weapon_type.lower() != "none" and enemy.weapon.ammo > 0
        blocker = self._first_blocker(message, enemy.position)
        shoot_lane = self._has_attack_lane(message)

        if self._should_priority_kick(enemy_distance, enemy_has_weapon, me.kick_cooldown):
            return BotCommand(seq=seq, move=enemy_dir, aim=enemy_dir, kick=True)

        base_move = self._restrict_perpendicular_drift(
            message,
            self._plan_base_move(message, has_weapon, needs_drop, enemy_has_weapon, enemy_distance, enemy_dir, blocker),
        )
        dodge = self._dodge_projectile(message, base_move)
        move = dodge if dodge.length() > 0.0 else base_move

        if needs_drop:
            return BotCommand(seq=seq, move=move, aim=enemy_dir, drop=True)

        if not has_weapon:
            loot_target = self._best_loot_target(message)
            if loot_target is not None:
                pickup = loot_target.cooldown <= 0.05 and me.position.distance_to(loot_target.position) <= 18.0
                return BotCommand(seq=seq, move=move, aim=enemy_dir, pickup=pickup)
            letterbox = self._nearest_ready_letterbox(message)
            if letterbox is not None and me.position.distance_to(letterbox.position) <= 30.0 and me.kick_cooldown <= 0.05:
                box_dir = self._safe_direction(Vec2(letterbox.position.x - me.position.x, letterbox.position.y - me.position.y), fallback=enemy_dir)
                return BotCommand(seq=seq, move=box_dir, aim=box_dir, kick=True)
            return BotCommand(seq=seq, move=move, aim=enemy_dir)

        if blocker is not None and blocker.kind in BREAKABLE_KINDS:
            break_dir = self._safe_direction(Vec2(blocker.center.x - me.position.x, blocker.center.y - me.position.y), fallback=enemy_dir)
            if me.position.distance_to(blocker.center) <= 26.0 and me.kick_cooldown <= 0.05:
                return BotCommand(seq=seq, move=break_dir, aim=break_dir, kick=True)
            if me.shoot_cooldown <= 0.05:
                return BotCommand(seq=seq, move=move, aim=break_dir, shoot=True)

        shoot_enemy = False
        if shoot_lane and me.shoot_cooldown <= 0.05:
            if enemy_has_weapon:
                shoot_enemy = self._door_only_abuse(message) and enemy_distance <= 240.0
            else:
                shoot_enemy = enemy_distance <= DISARMED_SHOT_RANGE

        if enemy_distance <= 24.0 and me.kick_cooldown <= 0.05:
            return BotCommand(seq=seq, move=enemy_dir, aim=enemy_dir, kick=True)
        return BotCommand(seq=seq, move=move, aim=enemy_dir, shoot=shoot_enemy)

    def _plan_base_move(
        self,
        message: TickMessage,
        has_weapon: bool,
        needs_drop: bool,
        enemy_has_weapon: bool,
        enemy_distance: float,
        enemy_dir: Vec2,
        blocker,
    ) -> Vec2:
        me = message.you
        if needs_drop:
            return Vec2()

        if not has_weapon:
            loot_target = self._best_loot_target(message)
            if loot_target is not None:
                return self._move_to(message, loot_target.position)
            letterbox = self._nearest_ready_letterbox(message)
            if letterbox is not None:
                return self._move_to(message, letterbox.position)
            if blocker is not None and blocker.kind in BREAKABLE_KINDS and me.position.distance_to(blocker.center) <= 30.0:
                return self._safe_direction(Vec2(blocker.center.x - me.position.x, blocker.center.y - me.position.y), fallback=enemy_dir)
            return self._move_to(message, message.enemy.position)

        if enemy_has_weapon and enemy_distance <= KICK_STEAL_RANGE + 12.0:
            return enemy_dir

        if blocker is not None and blocker.kind in BREAKABLE_KINDS:
            return self._move_to(message, blocker.center)

        if self._has_attack_lane(message):
            if enemy_has_weapon and enemy_distance > KICK_STEAL_RANGE:
                return self._combat_position_move(enemy_dir, enemy_distance, retreat=True)
            if not enemy_has_weapon and enemy_distance > DISARMED_SHOT_RANGE:
                return self._blend(enemy_dir, self._strafe(enemy_dir), 0.18)
            return self._combat_position_move(enemy_dir, enemy_distance, retreat=False)

        vantage = self._best_vantage_target(message)
        return self._move_to(message, vantage)

    def _best_loot_target(self, message: TickMessage) -> PickupView | None:
        me = message.you.position
        navigator = self.navigator
        best = None
        best_score = float("inf")
        for pickup in message.snapshot.pickups:
            score = me.distance_to(pickup.position) + pickup.cooldown * 40.0
            if navigator is not None:
                path = navigator.path_to(me, pickup.position, message.snapshot.obstacles)
                if not path:
                    continue
                score = len(path) * 10.0 + pickup.cooldown * 40.0
            if pickup.weapon_type.lower() == "uzi":
                score -= 25.0
            score -= min(pickup.ammo, 35) * 0.2
            if score < best_score:
                best_score = score
                best = pickup
        return best

    def _nearest_ready_letterbox(self, message: TickMessage):
        me = message.you.position
        best = None
        best_distance = float("inf")
        for letterbox in message.snapshot.letterboxes:
            if not letterbox.ready:
                continue
            distance = me.distance_to(letterbox.position)
            if distance < best_distance:
                best_distance = distance
                best = letterbox
        return best

    def _best_vantage_target(self, message: TickMessage) -> Vec2:
        navigator = self.navigator
        if navigator is None:
            return message.enemy.position
        return navigator.find_vantage_point(message.you.position, message.enemy.position, message.snapshot.obstacles)

    def _move_to(self, message: TickMessage, target: Vec2) -> Vec2:
        navigator = self.navigator
        me = message.you.position
        if navigator is None:
            return Vec2(target.x - me.x, target.y - me.y).normalized()
        if self._goal is None or self._goal.distance_to(target) > 12.0:
            self._goal = Vec2(target.x, target.y)
        move = navigator.direction_to(me, target, message.snapshot.obstacles)
        if move.length() <= 1e-6:
            return Vec2()
        if self._stuck_ticks >= 4:
            sidestep = self._strafe(self._safe_direction(Vec2(message.enemy.position.x - me.x, message.enemy.position.y - me.y), fallback=move))
            return self._blend(move, sidestep, 0.55)
        return move

    def _has_attack_lane(self, message: TickMessage) -> bool:
        navigator = self.navigator
        if navigator is None:
            return True
        return navigator.has_line_of_sight(message.you.position, message.enemy.position, message.snapshot.obstacles, ignored_kinds=DOOR_KINDS)

    def _door_only_abuse(self, message: TickMessage) -> bool:
        navigator = self.navigator
        if navigator is None:
            return False
        blocker = navigator.first_blocker(message.you.position, message.enemy.position, message.snapshot.obstacles)
        if blocker is None:
            return False
        return blocker.kind == "door" and navigator.has_line_of_sight(
            message.you.position,
            message.enemy.position,
            message.snapshot.obstacles,
            ignored_kinds=DOOR_KINDS,
        )

    def _first_blocker(self, message: TickMessage, target: Vec2):
        navigator = self.navigator
        if navigator is None:
            return None
        return navigator.first_blocker(message.you.position, target, message.snapshot.obstacles, ignored_kinds=DOOR_KINDS)

    def _combat_position_move(self, enemy_dir: Vec2, enemy_distance: float, *, retreat: bool) -> Vec2:
        strafe = self._strafe(enemy_dir)
        if retreat:
            if enemy_distance < KICK_STEAL_RANGE + 8.0:
                return enemy_dir
            return self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), strafe, 0.25)
        if enemy_distance > DISARMED_SHOT_RANGE:
            return self._blend(enemy_dir, strafe, 0.2)
        if enemy_distance < 56.0:
            return self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), strafe, 0.3)
        return strafe

    def _strafe(self, enemy_dir: Vec2) -> Vec2:
        return Vec2(-enemy_dir.y, enemy_dir.x) if (self.state.command_seq // 8) % 2 == 0 else Vec2(enemy_dir.y, -enemy_dir.x)

    def _dodge_projectile(self, message: TickMessage, base_move: Vec2) -> Vec2:
        me = message.you
        best = Vec2()
        best_threat = 0.0
        for projectile in message.snapshot.projectiles:
            if projectile.owner_id == me.player_id:
                continue
            threat, move = self._projectile_threat(me.position, projectile)
            if threat > best_threat:
                best_threat = threat
                best = move
        if best.length() <= 1e-6:
            return Vec2()
        move = self._blend(base_move, best, 0.7) if base_move.length() > 1e-6 else best
        if not self._is_safe_move(message, move):
            if self._is_safe_move(message, base_move):
                return Vec2()
            return Vec2()
        return move

    def _projectile_threat(self, me_pos: Vec2, projectile: ProjectileView) -> tuple[float, Vec2]:
        rel = Vec2(me_pos.x - projectile.position.x, me_pos.y - projectile.position.y)
        speed = projectile.velocity.length()
        if speed <= 1e-6:
            return 0.0, Vec2()
        direction = projectile.velocity.normalized()
        forward = rel.x * direction.x + rel.y * direction.y
        lateral_signed = rel.x * (-direction.y) + rel.y * direction.x
        lateral = abs(lateral_signed)
        if forward < -8.0 or forward > 110.0 or lateral > 18.0:
            return 0.0, Vec2()
        dodge = Vec2(-direction.y, direction.x) if lateral_signed <= 0 else Vec2(direction.y, -direction.x)
        threat = (110.0 - max(0.0, forward)) + (18.0 - lateral) * 4.0
        return threat, dodge.normalized()

    def _is_safe_move(self, message: TickMessage, move: Vec2) -> bool:
        if move.length() <= 1e-6:
            return True
        navigator = self.navigator
        if navigator is None:
            return True
        start = message.you.position
        future = Vec2(start.x + move.x * 28.0, start.y + move.y * 28.0)
        nearest = navigator.nearest_walkable_point(future, message.snapshot.obstacles)
        if nearest is None:
            return False
        if nearest.distance_to(future) > 8.0:
            return False
        return navigator.has_line_of_sight(start, nearest, message.snapshot.obstacles, ignored_kinds=set())

    def _should_priority_kick(self, enemy_distance: float, enemy_has_weapon: bool, kick_cooldown: float) -> bool:
        if kick_cooldown > 0.05:
            return False
        if enemy_has_weapon and enemy_distance <= KICK_STEAL_RANGE:
            return True
        return enemy_distance <= KICK_WALL_ABUSE_RANGE

    def _restrict_perpendicular_drift(self, message: TickMessage, move: Vec2) -> Vec2:
        if abs(move.x) <= 1e-6 or abs(move.y) <= 1e-6:
            return move
        navigator = self.navigator
        if navigator is None:
            return move
        cell = navigator.nearest_floor_cell(message.you.position)
        if cell is None:
            return move
        if abs(move.x) >= abs(move.y):
            if Cell(cell.x, cell.y - 1) not in navigator.floor_cells and Cell(cell.x, cell.y + 1) not in navigator.floor_cells:
                return Vec2(move.x, 0.0).normalized()
            return move
        if Cell(cell.x - 1, cell.y) not in navigator.floor_cells and Cell(cell.x + 1, cell.y) not in navigator.floor_cells:
            return Vec2(0.0, move.y).normalized()
        return move

    def _update_stuck_state(self, current: Vec2) -> None:
        if self._last_position.length() <= 1e-6:
            self._last_position = Vec2(current.x, current.y)
            self._stuck_ticks = 0
            return
        if current.distance_to(self._last_position) < 1.5:
            self._stuck_ticks += 1
        else:
            self._stuck_ticks = 0
        self._last_position = Vec2(current.x, current.y)

    def _safe_direction(self, vector: Vec2, fallback: Vec2) -> Vec2:
        if vector.length() <= 1e-6:
            return fallback.normalized() if fallback.length() > 1e-6 else Vec2(1.0, 0.0)
        return vector.normalized()

    def _blend(self, a: Vec2, b: Vec2, b_weight: float) -> Vec2:
        b_weight = max(0.0, min(1.0, b_weight))
        a_weight = 1.0 - b_weight
        combined = Vec2(a.x * a_weight + b.x * b_weight, a.y * a_weight + b.y * b_weight)
        return combined.normalized() if combined.length() > 1e-6 else Vec2()
