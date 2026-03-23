from __future__ import annotations

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


@dataclass(slots=True)
class SmartBot:
    """Heuristic bot focused on survivability, weapon control and stable aim."""

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

        move = Vec2()
        aim = self._safe_direction(me.facing, fallback=Vec2(1.0, 0.0))
        shoot = False
        kick = False
        pickup = False
        drop = False
        throw_item = False

        to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y)
        enemy_distance = to_enemy.length()
        enemy_dir = self._safe_direction(to_enemy, fallback=aim)
        has_weapon = me.weapon is not None and me.weapon.weapon_type.lower() != "none" and me.weapon.ammo > 0

        dodge = self._projectile_dodge(message)
        if dodge.length() > 0.0:
            move = dodge
            aim = enemy_dir
            shoot = has_weapon and self._has_clear_shot(message, me.position, enemy.position)
            return BotCommand(
                seq=seq,
                move=move,
                aim=aim,
                shoot=shoot,
                kick=False,
                pickup=False,
                drop=False,
                throw_item=False,
                interact=False,
            )

        nearby_pickup = self._best_pickup(message)
        clear_shot = enemy.alive and self._has_clear_shot(message, me.position, enemy.position)
        enemy_armed = enemy.weapon is not None and enemy.weapon.weapon_type.lower() != "none" and enemy.weapon.ammo > 0

        if enemy.alive:
            aim = enemy_dir

        if enemy.alive and enemy_distance <= 26.0 and me.kick_cooldown <= 0.05:
            kick = True
            move = enemy_dir
            aim = enemy_dir
            return BotCommand(seq=seq, move=move, aim=aim, kick=kick)

        if not has_weapon:
            if nearby_pickup is not None:
                to_pickup = Vec2(
                    nearby_pickup.position.x - me.position.x,
                    nearby_pickup.position.y - me.position.y,
                )
                move = self._safe_direction(to_pickup, fallback=enemy_dir)
                aim = enemy_dir if enemy.alive else move
                pickup = me.position.distance_to(nearby_pickup.position) <= 22.0
            elif enemy.alive:
                desired = 68.0 if enemy_armed else 28.0
                move = self._distance_control_move(enemy_dir, enemy_distance, desired, strafe_bias=0.55)
                aim = enemy_dir
                kick = enemy_distance <= 24.0 and me.kick_cooldown <= 0.05
        else:
            preferred_distance = self._preferred_distance(me.weapon.weapon_type if me.weapon else "")
            move = self._distance_control_move(enemy_dir, enemy_distance, preferred_distance, strafe_bias=0.9)
            if nearby_pickup is not None and self._should_upgrade_weapon(me.weapon.weapon_type if me.weapon else "", nearby_pickup):
                to_pickup = Vec2(
                    nearby_pickup.position.x - me.position.x,
                    nearby_pickup.position.y - me.position.y,
                )
                if me.position.distance_to(nearby_pickup.position) <= 20.0:
                    pickup = True
                elif enemy_distance > 60.0:
                    move = self._safe_direction(to_pickup, fallback=move)

            if enemy.alive and clear_shot:
                shoot = enemy_distance <= 250.0 and me.shoot_cooldown <= 0.05
            elif enemy.alive:
                flank = self._strafe(enemy_dir, handedness=-1.0 if seq % 2 else 1.0)
                move = self._blend(move, flank, 0.45)

            if me.weapon is not None and me.weapon.ammo <= 0:
                drop = True
            if enemy_distance <= 18.0 and enemy_armed and me.weapon is not None and me.weapon.ammo == 1:
                throw_item = True
                shoot = False

        move = move.clamp_unit()
        aim = self._safe_direction(aim, fallback=Vec2(1.0, 0.0))
        if kick:
            shoot = False
        if pickup or drop or throw_item:
            # Avoid mixing too many item actions on one tick.
            kick = False

        return BotCommand(
            seq=seq,
            move=move,
            aim=aim,
            shoot=shoot,
            kick=kick,
            pickup=pickup,
            drop=drop,
            throw_item=throw_item,
            interact=False,
        )

    def _best_pickup(self, message: TickMessage) -> PickupView | None:
        me = message.you
        best_pickup: PickupView | None = None
        best_score = float("inf")
        for pickup in message.snapshot.pickups:
            if pickup.cooldown > 0.0:
                continue
            distance = me.position.distance_to(pickup.position)
            score = distance
            if pickup.weapon_type.lower() == "uzi":
                score -= 24.0
            score -= min(20.0, pickup.ammo * 0.4)
            if score < best_score:
                best_score = score
                best_pickup = pickup
        return best_pickup

    def _preferred_distance(self, weapon_type: str) -> float:
        weapon_key = weapon_type.lower()
        if weapon_key == "uzi":
            return 110.0
        if weapon_key == "revolver":
            return 155.0
        return 90.0

    def _should_upgrade_weapon(self, current_weapon_type: str, pickup: PickupView) -> bool:
        current_key = current_weapon_type.lower()
        pickup_key = pickup.weapon_type.lower()
        if current_key == "none":
            return True
        if current_key == "revolver" and pickup_key == "uzi":
            return True
        return pickup_key == current_key and pickup.ammo >= 8

    def _distance_control_move(self, enemy_dir: Vec2, distance: float, preferred: float, strafe_bias: float) -> Vec2:
        strafe = self._strafe(enemy_dir, handedness=1.0 if (self.state.command_seq // 18) % 2 == 0 else -1.0)
        if distance > preferred + 18.0:
            return self._blend(enemy_dir, strafe, strafe_bias)
        if distance < preferred - 24.0:
            return self._blend(Vec2(-enemy_dir.x, -enemy_dir.y), strafe, strafe_bias)
        return strafe

    def _projectile_dodge(self, message: TickMessage) -> Vec2:
        me = message.you
        best_threat = 0.0
        best_move = Vec2()
        for projectile in message.snapshot.projectiles:
            if projectile.owner_id == me.player_id:
                continue
            offset = Vec2(me.position.x - projectile.position.x, me.position.y - projectile.position.y)
            velocity = projectile.velocity
            speed = velocity.length()
            if speed <= 1e-6:
                continue
            direction = velocity.normalized()
            ahead = offset.x * direction.x + offset.y * direction.y
            if ahead < -12.0 or ahead > 120.0:
                continue
            lateral = abs(offset.x * (-direction.y) + offset.y * direction.x)
            if lateral > 18.0:
                continue
            threat = (120.0 - max(0.0, ahead)) + (18.0 - lateral) * 3.0
            if threat > best_threat:
                best_threat = threat
                handedness = -1.0 if (offset.x * (-direction.y) + offset.y * direction.x) > 0 else 1.0
                best_move = self._strafe(direction, handedness=handedness)
        return best_move

    def _has_clear_shot(self, message: TickMessage, start: Vec2, target: Vec2) -> bool:
        for obstacle in message.snapshot.obstacles:
            if not obstacle.solid:
                continue
            if obstacle.kind not in {"wall", "door", "glass", "box", "letterbox"}:
                continue
            if self._segment_intersects_rect(start, target, obstacle):
                return False
        return True

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
            return fallback.normalized()
        return vector.normalized()

    def _strafe(self, direction: Vec2, handedness: float) -> Vec2:
        direction = self._safe_direction(direction, fallback=Vec2(1.0, 0.0))
        return Vec2(-direction.y * handedness, direction.x * handedness)

    def _blend(self, primary: Vec2, secondary: Vec2, secondary_weight: float) -> Vec2:
        w2 = max(0.0, min(1.0, secondary_weight))
        w1 = 1.0 - w2
        return Vec2(primary.x * w1 + secondary.x * w2, primary.y * w1 + secondary.y * w2).normalized()
