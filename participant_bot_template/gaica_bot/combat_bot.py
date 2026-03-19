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
from gaica_bot.navigator import BREAKABLE_KINDS, Navigator


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
        blocker = self._first_blocker(message, enemy.position)

        dodge = self._dodge_projectile(message)
        if dodge.length() > 0.0:
            return BotCommand(seq=seq, move=dodge, aim=enemy_dir)

        if enemy_distance <= 24.0 and me.kick_cooldown <= 0.05:
            return BotCommand(seq=seq, move=enemy_dir, aim=enemy_dir, kick=True)

        if needs_drop:
            return BotCommand(seq=seq, aim=enemy_dir, drop=True)

        if not has_weapon:
            loot_target = self._best_loot_target(message)
            if loot_target is not None:
                move = self._move_to(message, loot_target.position)
                pickup = loot_target.cooldown <= 0.05 and me.position.distance_to(loot_target.position) <= 18.0
                return BotCommand(seq=seq, move=move, aim=enemy_dir, pickup=pickup)
            letterbox = self._nearest_ready_letterbox(message)
            if letterbox is not None:
                to_box = Vec2(letterbox.position.x - me.position.x, letterbox.position.y - me.position.y)
                box_dir = self._safe_direction(to_box, fallback=enemy_dir)
                if me.position.distance_to(letterbox.position) <= 30.0 and me.kick_cooldown <= 0.05:
                    return BotCommand(seq=seq, move=box_dir, aim=box_dir, kick=True)
                return BotCommand(seq=seq, move=self._move_to(message, letterbox.position), aim=box_dir)
            if blocker is not None and blocker.kind in BREAKABLE_KINDS and me.position.distance_to(blocker.center) <= 30.0 and me.kick_cooldown <= 0.05:
                break_dir = self._safe_direction(Vec2(blocker.center.x - me.position.x, blocker.center.y - me.position.y), fallback=enemy_dir)
                return BotCommand(seq=seq, move=break_dir, aim=break_dir, kick=True)
            return BotCommand(seq=seq, move=self._move_to(message, enemy.position), aim=enemy_dir)

        if blocker is not None and blocker.kind in BREAKABLE_KINDS:
            break_dir = self._safe_direction(Vec2(blocker.center.x - me.position.x, blocker.center.y - me.position.y), fallback=enemy_dir)
            if me.position.distance_to(blocker.center) <= 26.0 and me.kick_cooldown <= 0.05:
                return BotCommand(seq=seq, move=break_dir, aim=break_dir, kick=True)
            if me.shoot_cooldown <= 0.05:
                return BotCommand(seq=seq, move=self._move_to(message, blocker.center), aim=break_dir, shoot=True)

        if self._clear_line(message):
            move = self._combat_position_move(enemy_dir, enemy_distance)
            shoot = enemy_distance <= 220.0 and me.shoot_cooldown <= 0.05
            return BotCommand(seq=seq, move=move, aim=enemy_dir, shoot=shoot)

        vantage = self._best_vantage_target(message)
        move = self._move_to(message, vantage)
        return BotCommand(seq=seq, move=move, aim=enemy_dir)

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
            enemy_dir = self._safe_direction(Vec2(message.enemy.position.x - me.x, message.enemy.position.y - me.y), fallback=move)
            sidestep = Vec2(-enemy_dir.y, enemy_dir.x) if (self.state.command_seq // 6) % 2 == 0 else Vec2(enemy_dir.y, -enemy_dir.x)
            return self._blend(move, sidestep, 0.55)
        return move

    def _clear_line(self, message: TickMessage) -> bool:
        navigator = self.navigator
        if navigator is None:
            return True
        return navigator.has_line_of_sight(message.you.position, message.enemy.position, message.snapshot.obstacles)

    def _first_blocker(self, message: TickMessage, target: Vec2):
        navigator = self.navigator
        if navigator is None:
            return None
        return navigator.first_blocker(message.you.position, target, message.snapshot.obstacles)

    def _combat_position_move(self, enemy_dir: Vec2, enemy_distance: float) -> Vec2:
        strafe = Vec2(-enemy_dir.y, enemy_dir.x) if (self.state.command_seq // 8) % 2 == 0 else Vec2(enemy_dir.y, -enemy_dir.x)
        if enemy_distance > 170.0:
            return self._blend(enemy_dir, strafe, 0.25)
        if enemy_distance < 72.0:
            return self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), strafe, 0.35)
        return strafe

    def _dodge_projectile(self, message: TickMessage) -> Vec2:
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
        navigator = self.navigator
        if navigator is not None:
            future = Vec2(me.position.x + best.x * 28.0, me.position.y + best.y * 28.0)
            if navigator.nearest_floor_cell(future) is None:
                return Vec2(best.x, 0.0).normalized() if abs(best.x) > abs(best.y) else Vec2()
        return best

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
