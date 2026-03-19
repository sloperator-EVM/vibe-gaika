from __future__ import annotations

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

    def on_tick(self, message: TickMessage) -> BotCommand:
        self.state.last_tick = message
        seq = self.state.next_command_seq()

        me = message.you
        enemy = message.enemy
        if not me.alive:
            return BotCommand(seq=seq)

        move = Vec2()
        aim = Vec2(1.0, 0.0)
        shoot = False
        pickup = False
        kick = False

        if enemy.player_id:
            to_enemy = Vec2(
                enemy.position.x - me.position.x,
                enemy.position.y - me.position.y,
            )
            if to_enemy.length() > 1e-6:
                aim = to_enemy.normalized()

        nearest_pickup = self._nearest_pickup(message)
        has_weapon = me.weapon is not None and me.weapon.weapon_type.lower() != "none"

        if not has_weapon and nearest_pickup is not None:
            to_pickup = Vec2(
                nearest_pickup.position.x - me.position.x,
                nearest_pickup.position.y - me.position.y,
            )
            if to_pickup.length() > 1e-6:
                move = to_pickup.normalized()
                aim = move
            pickup = nearest_pickup.position.distance_to(me.position) <= 24.0
        elif enemy.alive:
            to_enemy = Vec2(
                enemy.position.x - me.position.x,
                enemy.position.y - me.position.y,
            )
            distance = to_enemy.length()
            if distance > 80.0:
                move = to_enemy.normalized()
            shoot = has_weapon and distance <= 240.0
            kick = distance <= 28.0 and not shoot

        return BotCommand(
            seq=seq,
            move=move,
            aim=aim,
            shoot=shoot,
            kick=kick,
            pickup=pickup,
        )

    def _nearest_pickup(self, message: TickMessage) -> PickupView | None:
        me = message.you
        nearest: PickupView | None = None
        best_distance = float("inf")
        for pickup in message.snapshot.pickups:
            distance = me.position.distance_to(pickup.position)
            if distance < best_distance:
                best_distance = distance
                nearest = pickup
        return nearest
