from __future__ import annotations

from dataclasses import dataclass, field

from gaica_bot.models import (
    BotCommand,
    BotState,
    HelloMessage,
    ObstacleView,
    PickupView,
    ProjectileView,
    RoundEndMessage,
    RoundStartMessage,
    TickMessage,
    Vec2,
)


@dataclass(slots=True)
class CombatBot:
    """Purposeful rule-based bot: weapon first, then line-of-sight, then accurate fire and dodge."""

    state: BotState = field(default_factory=BotState)

    def on_hello(self, message: HelloMessage) -> None:
        self.state.hello = message

    def on_round_start(self, message: RoundStartMessage) -> None:
        self.state.current_round = message
        self.state.last_tick = None
        self.state.last_round_end = None
        self.state.command_seq = 0

    def on_round_end(self, message: RoundEndMessage) -> None:
        self.state.last_round_end = message

    def on_tick(self, message: TickMessage) -> BotCommand:
        self.state.last_tick = message
        seq = self.state.next_command_seq()

        me = message.you
        enemy = message.enemy
        if not me.alive:
            return BotCommand(seq=seq)

        to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y)
        enemy_dir = self._safe_direction(to_enemy, fallback=me.facing if me.facing.length() > 0.0 else Vec2(1.0, 0.0))
        enemy_distance = to_enemy.length()
        has_weapon = me.weapon is not None and me.weapon.weapon_type.lower() != "none" and me.weapon.ammo > 0
        move = Vec2()
        aim = enemy_dir
        shoot = False
        kick = False
        pickup = False
        drop = False

        dodge_move = self._dodge_projectile(message)
        if dodge_move.length() > 0.0:
            return BotCommand(seq=seq, move=dodge_move, aim=aim, shoot=has_weapon and self._clear_line(message), pickup=False)

        if enemy.alive and enemy_distance <= 24.0 and me.kick_cooldown <= 0.05:
            return BotCommand(seq=seq, move=enemy_dir, aim=enemy_dir, kick=True)

        nearest_pickup = self._nearest_pickup(message)
        if not has_weapon:
            if nearest_pickup is not None:
                to_pickup = Vec2(nearest_pickup.position.x - me.position.x, nearest_pickup.position.y - me.position.y)
                move = self._safe_direction(to_pickup, fallback=enemy_dir)
                pickup = me.position.distance_to(nearest_pickup.position) <= 20.0
                aim = enemy_dir
            else:
                move = enemy_dir
            return BotCommand(seq=seq, move=move, aim=aim, pickup=pickup)

        if me.weapon is not None and me.weapon.ammo <= 0:
            drop = True

        if self._clear_line(message):
            shoot = enemy.alive and enemy_distance <= 260.0 and me.shoot_cooldown <= 0.05
            move = self._combat_position_move(enemy_dir, enemy_distance)
        else:
            move = self._seek_line_of_sight_move(message)
            shoot = False

        return BotCommand(
            seq=seq,
            move=move.clamp_unit(),
            aim=aim,
            shoot=shoot,
            kick=kick,
            pickup=False,
            drop=drop,
            interact=False,
        )

    def _nearest_pickup(self, message: TickMessage) -> PickupView | None:
        me = message.you
        best: PickupView | None = None
        best_distance = float("inf")
        for pickup in message.snapshot.pickups:
            if pickup.cooldown > 0.0:
                continue
            distance = me.position.distance_to(pickup.position)
            if distance < best_distance:
                best_distance = distance
                best = pickup
        return best

    def _clear_line(self, message: TickMessage) -> bool:
        start = message.you.position
        end = message.enemy.position
        for obstacle in message.snapshot.obstacles:
            if not obstacle.solid:
                continue
            if obstacle.kind not in {"wall", "door", "glass", "box", "letterbox"}:
                continue
            if self._segment_intersects_rect(start, end, obstacle):
                return False
        return True

    def _seek_line_of_sight_move(self, message: TickMessage) -> Vec2:
        me = message.you
        enemy = message.enemy
        to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y)
        enemy_dir = self._safe_direction(to_enemy, fallback=Vec2(1.0, 0.0))

        lateral_a = Vec2(-enemy_dir.y, enemy_dir.x)
        lateral_b = Vec2(enemy_dir.y, -enemy_dir.x)
        retreat = Vec2(-enemy_dir.x, -enemy_dir.y)
        advance = enemy_dir

        candidates = [
            self._blend(lateral_a, advance, 0.25),
            self._blend(lateral_b, advance, 0.25),
            lateral_a,
            lateral_b,
            self._blend(lateral_a, retreat, 0.3),
            self._blend(lateral_b, retreat, 0.3),
            advance,
        ]

        best_move = candidates[0]
        best_score = float("inf")
        for candidate in candidates:
            candidate_pos = Vec2(me.position.x + candidate.x * 28.0, me.position.y + candidate.y * 28.0)
            blocked = False
            for obstacle in message.snapshot.obstacles:
                if not obstacle.solid:
                    continue
                if obstacle.kind not in {"wall", "door", "glass", "box", "letterbox"}:
                    continue
                if self._segment_intersects_rect(candidate_pos, enemy.position, obstacle):
                    blocked = True
                    break
            score = 1.0 if blocked else 0.0
            score += self._distance(candidate_pos, enemy.position) / 500.0
            if score < best_score:
                best_score = score
                best_move = candidate
        return best_move

    def _combat_position_move(self, enemy_dir: Vec2, enemy_distance: float) -> Vec2:
        strafe = Vec2(-enemy_dir.y, enemy_dir.x) if (self.state.command_seq // 12) % 2 == 0 else Vec2(enemy_dir.y, -enemy_dir.x)
        if enemy_distance > 170.0:
            return self._blend(enemy_dir, strafe, 0.4)
        if enemy_distance < 90.0:
            return self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), strafe, 0.55)
        return strafe

    def _dodge_projectile(self, message: TickMessage) -> Vec2:
        me = message.you
        best_threat = 0.0
        best_move = Vec2()
        for projectile in message.snapshot.projectiles:
            if projectile.owner_id == me.player_id:
                continue
            threat, move = self._projectile_threat(me.position, projectile)
            if threat > best_threat:
                best_threat = threat
                best_move = move
        return best_move

    def _projectile_threat(self, me_pos: Vec2, projectile: ProjectileView) -> tuple[float, Vec2]:
        rel = Vec2(me_pos.x - projectile.position.x, me_pos.y - projectile.position.y)
        vel = projectile.velocity
        speed = vel.length()
        if speed <= 1e-6:
            return 0.0, Vec2()

        direction = vel.normalized()
        forward = rel.x * direction.x + rel.y * direction.y
        lateral_signed = rel.x * (-direction.y) + rel.y * direction.x
        lateral = abs(lateral_signed)

        if forward < -10.0 or forward > 130.0 or lateral > 20.0:
            return 0.0, Vec2()

        threat = (130.0 - max(0.0, forward)) + (20.0 - lateral) * 4.0
        dodge_dir = Vec2(-direction.y, direction.x) if lateral_signed <= 0.0 else Vec2(direction.y, -direction.x)
        return threat, dodge_dir

    def _segment_intersects_rect(self, start: Vec2, end: Vec2, obstacle: ObstacleView) -> bool:
        min_x = obstacle.center.x - obstacle.half_size.x
        max_x = obstacle.center.x + obstacle.half_size.x
        min_y = obstacle.center.y - obstacle.half_size.y
        max_y = obstacle.center.y + obstacle.half_size.y

        dx = end.x - start.x
        dy = end.y - start.y
        t0 = 0.0
        t1 = 1.0
        for p, q in (
            (-dx, start.x - min_x),
            (dx, max_x - start.x),
            (-dy, start.y - min_y),
            (dy, max_y - start.y),
        ):
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
        return t0 <= t1 and (0.02 <= t0 <= 0.98 or 0.02 <= t1 <= 0.98)

    def _safe_direction(self, vector: Vec2, fallback: Vec2) -> Vec2:
        if vector.length() <= 1e-6:
            return fallback.normalized() if fallback.length() > 1e-6 else Vec2(1.0, 0.0)
        return vector.normalized()

    def _blend(self, a: Vec2, b: Vec2, b_weight: float) -> Vec2:
        b_weight = max(0.0, min(1.0, b_weight))
        a_weight = 1.0 - b_weight
        return Vec2(a.x * a_weight + b.x * b_weight, a.y * a_weight + b.y * b_weight).normalized()

    def _distance(self, a: Vec2, b: Vec2) -> float:
        dx = b.x - a.x
        dy = b.y - a.y
        return (dx * dx + dy * dy) ** 0.5
