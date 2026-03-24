from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
from gaica_bot.navigator import Navigator


DOOR_KINDS = {"door"}
KICK_STEAL_RANGE = 42.0
KICK_ABUSE_RANGE = 44.0
CONTACT_KICK_RANGE = 24.0
PICKUP_RANGE = 18.0
LETTERBOX_KICK_RANGE = 30.0
BREAKABLE_KICK_RANGE = 26.0
DISARMED_SHOT_RANGE = 110.0
ARMED_DOOR_SHOT_RANGE = 240.0
DODGE_LOOKAHEAD = 28.0
VOID_MARGIN = 2.0
VOID_EMERGENCY_MARGIN = 0.0
THREATENED_ENEMY_RANGE = 200.0


@dataclass(slots=True)
class CombatContext:
    message: TickMessage
    enemy_dir: Vec2
    enemy_distance: float
    has_weapon: bool
    needs_drop: bool
    enemy_has_weapon: bool
    loot_target: PickupView | None
    blocker: Any
    has_attack_lane: bool
    under_threat: bool


@dataclass(slots=True)
class CombatBot:
    """Rule-based bot with explicit priorities for pickup, survival, and engagement."""

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
        if not me.alive:
            return BotCommand(seq=seq)

        self._update_stuck_state(me.position)
        ctx = self._build_context(message)

        if self._is_void_emergency(ctx):
            return BotCommand(seq=seq, move=self._void_recovery_move(ctx.message, fallback=ctx.enemy_dir), aim=ctx.enemy_dir)

        if self._should_priority_kick(ctx):
            return BotCommand(seq=seq, move=ctx.enemy_dir, aim=ctx.enemy_dir, kick=True)

        pickup_command = self._pickup_command(seq, ctx)
        if pickup_command is not None:
            return pickup_command

        if ctx.needs_drop:
            return BotCommand(seq=seq, aim=ctx.enemy_dir, drop=True)

        if not ctx.has_weapon:
            return self._unarmed_command(seq, ctx)

        breakable_command = self._breakable_command(seq, ctx)
        if breakable_command is not None:
            return breakable_command

        move = self._safe_move(ctx.message, self._combat_move(ctx))
        dodge = self._safe_move(ctx.message, self._dodge_move(ctx, move))
        if dodge.length() > 1e-6:
            move = dodge

        if ctx.enemy_distance <= CONTACT_KICK_RANGE and ctx.message.you.kick_cooldown <= 0.05:
            return BotCommand(seq=seq, move=ctx.enemy_dir, aim=ctx.enemy_dir, kick=True)

        return BotCommand(seq=seq, move=move, aim=ctx.enemy_dir, shoot=self._should_shoot_enemy(ctx))

    def _build_context(self, message: TickMessage) -> CombatContext:
        me = message.you
        enemy = message.enemy
        to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y)
        enemy_dir = self._safe_direction(to_enemy, fallback=me.facing if me.facing.length() > 0.0 else Vec2(1.0, 0.0))
        has_weapon = me.weapon is not None and me.weapon.weapon_type.lower() != "none" and me.weapon.ammo > 0
        needs_drop = me.weapon is not None and me.weapon.weapon_type.lower() != "none" and me.weapon.ammo <= 0
        enemy_has_weapon = enemy.weapon is not None and enemy.weapon.weapon_type.lower() != "none" and enemy.weapon.ammo > 0
        return CombatContext(
            message=message,
            enemy_dir=enemy_dir,
            enemy_distance=to_enemy.length(),
            has_weapon=has_weapon,
            needs_drop=needs_drop,
            enemy_has_weapon=enemy_has_weapon,
            loot_target=self._best_loot_target(message),
            blocker=self._first_blocker(message, enemy.position),
            has_attack_lane=self._has_attack_lane(message),
            under_threat=self._is_under_threat(message, enemy_has_weapon),
        )

    def _pickup_command(self, seq: int, ctx: CombatContext) -> BotCommand | None:
        if ctx.has_weapon or ctx.loot_target is None:
            return None
        me = ctx.message.you
        if me.position.distance_to(ctx.loot_target.position) <= PICKUP_RANGE and ctx.loot_target.cooldown <= 0.05:
            return BotCommand(seq=seq, aim=ctx.enemy_dir, pickup=True)
        return None

    def _unarmed_command(self, seq: int, ctx: CombatContext) -> BotCommand:
        me = ctx.message.you
        letterbox = self._nearest_ready_letterbox(ctx.message)
        pickup_target = ctx.loot_target
        if pickup_target is not None and letterbox is not None:
            pickup_score = self._travel_score(ctx.message, pickup_target.position) + max(0.0, pickup_target.cooldown) * 40.0
            box_score = self._travel_score(ctx.message, letterbox.position)
            if box_score + 4.0 < pickup_score:
                pickup_target = None

        if pickup_target is not None:
            move = self._safe_move(ctx.message, self._move_to(ctx.message, pickup_target.position))
            dodge = self._safe_move(ctx.message, self._dodge_move(ctx, move))
            if dodge.length() > 1e-6:
                move = dodge
            pickup = me.position.distance_to(pickup_target.position) <= PICKUP_RANGE and pickup_target.cooldown <= 0.05
            return BotCommand(seq=seq, move=move, aim=ctx.enemy_dir, pickup=pickup)

        if letterbox is not None:
            box_dir = self._safe_direction(
                Vec2(letterbox.position.x - me.position.x, letterbox.position.y - me.position.y),
                fallback=ctx.enemy_dir,
            )
            if me.position.distance_to(letterbox.position) <= LETTERBOX_KICK_RANGE and me.kick_cooldown <= 0.05:
                return BotCommand(seq=seq, move=box_dir, aim=box_dir, kick=True)
            move = self._safe_move(ctx.message, self._move_to(ctx.message, letterbox.position))
            return BotCommand(seq=seq, move=move, aim=ctx.enemy_dir)

        move = self._safe_move(ctx.message, self._move_to(ctx.message, ctx.message.enemy.position))
        return BotCommand(seq=seq, move=move, aim=ctx.enemy_dir)

    def _breakable_command(self, seq: int, ctx: CombatContext) -> BotCommand | None:
        if ctx.under_threat:
            return None
        blocker = ctx.blocker
        if blocker is None or blocker.kind not in {"glass", "box"}:
            return None
        me = ctx.message.you
        break_dir = self._safe_direction(
            Vec2(blocker.center.x - me.position.x, blocker.center.y - me.position.y),
            fallback=ctx.enemy_dir,
        )
        if me.position.distance_to(blocker.center) <= BREAKABLE_KICK_RANGE and me.kick_cooldown <= 0.05:
            return BotCommand(seq=seq, move=break_dir, aim=break_dir, kick=True)
        return BotCommand(seq=seq, move=self._safe_move(ctx.message, self._move_to(ctx.message, blocker.center)), aim=break_dir)

    def _combat_move(self, ctx: CombatContext) -> Vec2:
        if ctx.enemy_has_weapon and ctx.enemy_distance <= KICK_STEAL_RANGE + 12.0:
            return ctx.enemy_dir

        if ctx.has_attack_lane:
            if ctx.enemy_has_weapon:
                return self._armed_enemy_move(ctx)
            return self._disarmed_enemy_move(ctx)

        return self._safe_move(ctx.message, self._move_to(ctx.message, self._best_vantage_target(ctx.message)))

    def _armed_enemy_move(self, ctx: CombatContext) -> Vec2:
        if ctx.enemy_distance < KICK_STEAL_RANGE + 8.0:
            return ctx.enemy_dir
        retreat = Vec2(-ctx.enemy_dir.x, -ctx.enemy_dir.y)
        return self._safe_move(ctx.message, self._blend(retreat, self._strafe(ctx.enemy_dir), 0.2))

    def _disarmed_enemy_move(self, ctx: CombatContext) -> Vec2:
        if ctx.enemy_distance > DISARMED_SHOT_RANGE:
            return self._safe_move(ctx.message, self._blend(ctx.enemy_dir, self._strafe(ctx.enemy_dir), 0.15))
        if ctx.enemy_distance < 56.0:
            retreat = Vec2(-ctx.enemy_dir.x, -ctx.enemy_dir.y)
            return self._safe_move(ctx.message, self._blend(retreat, self._strafe(ctx.enemy_dir), 0.2))
        return self._safe_move(ctx.message, self._strafe(ctx.enemy_dir))

    def _should_priority_kick(self, ctx: CombatContext) -> bool:
        kick_ready = ctx.message.you.kick_cooldown <= 0.05
        if not kick_ready:
            return False
        if ctx.enemy_has_weapon and ctx.enemy_distance <= KICK_STEAL_RANGE:
            return True
        return ctx.enemy_distance <= KICK_ABUSE_RANGE

    def _should_shoot_enemy(self, ctx: CombatContext) -> bool:
        me = ctx.message.you
        if me.shoot_cooldown > 0.05 or not ctx.has_attack_lane:
            return False
        if ctx.enemy_has_weapon:
            return self._door_only_abuse(ctx.message) and ctx.enemy_distance <= ARMED_DOOR_SHOT_RANGE
        return ctx.enemy_distance <= DISARMED_SHOT_RANGE

    def _best_loot_target(self, message: TickMessage) -> PickupView | None:
        me = message.you.position
        navigator = self.navigator
        best = None
        best_score = float("inf")
        for pickup in message.snapshot.pickups:
            if pickup.cooldown > 1.5:
                continue
            score = me.distance_to(pickup.position) + pickup.cooldown * 40.0
            if navigator is not None:
                path = navigator.path_to(me, pickup.position, message.snapshot.obstacles)
                if not path:
                    continue
                score = len(path) * 14.0 + pickup.cooldown * 40.0
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

    def _travel_score(self, message: TickMessage, target: Vec2) -> float:
        me = message.you.position
        navigator = self.navigator
        if navigator is None:
            return me.distance_to(target)
        path = navigator.path_to(me, target, message.snapshot.obstacles)
        if path:
            return len(path) * 14.0
        return float("inf")

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
            return self._safe_move(message, self._blend(move, self._strafe(move), 0.45))
        return move

    def _has_attack_lane(self, message: TickMessage) -> bool:
        navigator = self.navigator
        if navigator is None:
            return True
        return navigator.has_line_of_sight(
            message.you.position,
            message.enemy.position,
            message.snapshot.obstacles,
            ignored_kinds=DOOR_KINDS,
        )

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
        return navigator.first_blocker(
            message.you.position,
            target,
            message.snapshot.obstacles,
            ignored_kinds=DOOR_KINDS,
        )

    def _strafe(self, direction: Vec2) -> Vec2:
        if direction.length() <= 1e-6:
            direction = Vec2(1.0, 0.0)
        return Vec2(-direction.y, direction.x) if (self.state.command_seq // 8) % 2 == 0 else Vec2(direction.y, -direction.x)

    def _dodge_move(self, ctx: CombatContext, base_move: Vec2) -> Vec2:
        me = ctx.message.you
        best_move = Vec2()
        best_threat = 0.0
        for projectile in ctx.message.snapshot.projectiles:
            if projectile.owner_id == me.player_id:
                continue
            threat, dodge_dir = self._projectile_threat(me.position, projectile)
            if threat <= best_threat:
                continue
            candidate = self._safe_move(ctx.message, self._blend(base_move, dodge_dir, 0.7))
            if candidate.length() <= 1e-6:
                candidate = self._safe_move(ctx.message, dodge_dir)
            if candidate.length() <= 1e-6:
                continue
            best_threat = threat
            best_move = candidate
        return best_move

    def _is_under_threat(self, message: TickMessage, enemy_has_weapon: bool) -> bool:
        if enemy_has_weapon and message.you.position.distance_to(message.enemy.position) <= THREATENED_ENEMY_RANGE:
            return True
        for projectile in message.snapshot.projectiles:
            if projectile.owner_id == message.you.player_id:
                continue
            threat, _ = self._projectile_threat(message.you.position, projectile)
            if threat > 8.0:
                return True
        return False

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

    def _safe_move(self, message: TickMessage, move: Vec2) -> Vec2:
        move = move.normalized() if move.length() > 1e-6 else Vec2()
        if move.length() <= 1e-6:
            return Vec2()
        navigator = self.navigator
        if navigator is None:
            return move
        safe_move = self._pick_safe_move_variant(message, move)
        return safe_move if safe_move.length() > 1e-6 else Vec2()

    def _pick_safe_move_variant(self, message: TickMessage, move: Vec2) -> Vec2:
        candidates = [move]
        if abs(move.x) > 1e-6:
            candidates.append(Vec2(move.x, 0.0).normalized())
        if abs(move.y) > 1e-6:
            candidates.append(Vec2(0.0, move.y).normalized())
        if abs(move.x) > 1e-6 and abs(move.y) > 1e-6:
            dominant = Vec2(move.x, 0.0) if abs(move.x) >= abs(move.y) else Vec2(0.0, move.y)
            candidates.insert(1, dominant.normalized())

        for candidate in candidates:
            if self._is_walkable_step(message, candidate):
                return candidate
        return Vec2()

    def _is_walkable_step(self, message: TickMessage, move: Vec2) -> bool:
        navigator = self.navigator
        if navigator is None or move.length() <= 1e-6:
            return move.length() <= 1e-6 or navigator is None
        start = message.you.position
        future = Vec2(start.x + move.x * DODGE_LOOKAHEAD, start.y + move.y * DODGE_LOOKAHEAD)
        safe_margin = float(globals().get("VOID_MARGIN", 2.0))
        return navigator.is_walkable_point(future, message.snapshot.obstacles, margin=safe_margin) and navigator.has_line_of_sight(
            start,
            future,
            message.snapshot.obstacles,
        )

    def _is_void_emergency(self, ctx: CombatContext) -> bool:
        navigator = self.navigator
        if navigator is None:
            return False
        return not navigator.is_floor_point(ctx.message.you.position, margin=VOID_EMERGENCY_MARGIN)

    def _void_recovery_move(self, message: TickMessage, fallback: Vec2) -> Vec2:
        navigator = self.navigator
        if navigator is None:
            return fallback
        target = navigator.nearest_walkable_point(message.you.position, message.snapshot.obstacles)
        if target is None:
            return Vec2()
        toward_safe = Vec2(target.x - message.you.position.x, target.y - message.you.position.y)
        return self._safe_move(message, toward_safe.normalized())

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
