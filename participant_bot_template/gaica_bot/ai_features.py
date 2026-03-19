from __future__ import annotations

from gaica_bot.models import PickupView, TickMessage, Vec2

FEATURE_SIZE = 32


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: float, scale: float) -> float:
    if scale <= 1e-9:
        return 0.0
    return _clamp(value / scale, -1.0, 1.0)


def _nearest_pickup(message: TickMessage) -> PickupView | None:
    me = message.you
    best_pickup: PickupView | None = None
    best_distance = float("inf")
    for pickup in message.snapshot.pickups:
        if pickup.cooldown > 0.0:
            continue
        distance = me.position.distance_to(pickup.position)
        if distance < best_distance:
            best_distance = distance
            best_pickup = pickup
    return best_pickup


def extract_features(message: TickMessage) -> list[float]:
    me = message.you
    enemy = message.enemy
    level = message.snapshot

    to_enemy = Vec2(enemy.position.x - me.position.x, enemy.position.y - me.position.y)
    nearest_pickup = _nearest_pickup(message)

    projectile_dx = 0.0
    projectile_dy = 0.0
    projectile_vx = 0.0
    projectile_vy = 0.0
    projectile_dist = 1.0
    nearest_projectile_distance = float("inf")
    for projectile in message.snapshot.projectiles:
        if projectile.owner_id == me.player_id:
            continue
        dx = projectile.position.x - me.position.x
        dy = projectile.position.y - me.position.y
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < nearest_projectile_distance:
            nearest_projectile_distance = dist
            projectile_dx = dx
            projectile_dy = dy
            projectile_vx = projectile.velocity.x
            projectile_vy = projectile.velocity.y
            projectile_dist = dist

    pickup_dx = 0.0
    pickup_dy = 0.0
    pickup_ammo = 0.0
    pickup_is_uzi = 0.0
    pickup_is_revolver = 0.0
    if nearest_pickup is not None:
        pickup_dx = nearest_pickup.position.x - me.position.x
        pickup_dy = nearest_pickup.position.y - me.position.y
        pickup_ammo = nearest_pickup.ammo
        pickup_is_uzi = 1.0 if nearest_pickup.weapon_type.lower() == "uzi" else 0.0
        pickup_is_revolver = 1.0 if nearest_pickup.weapon_type.lower() == "revolver" else 0.0

    me_weapon = (me.weapon.weapon_type.lower() if me.weapon else "none")
    enemy_weapon = (enemy.weapon.weapon_type.lower() if enemy.weapon else "none")

    features = [
        _norm(to_enemy.x, 384.0),
        _norm(to_enemy.y, 384.0),
        _norm(to_enemy.length(), 384.0),
        _norm(me.facing.x, 1.0),
        _norm(me.facing.y, 1.0),
        1.0 if me.alive else 0.0,
        1.0 if enemy.alive else 0.0,
        1.0 if me.weapon is not None else 0.0,
        1.0 if enemy.weapon is not None else 0.0,
        1.0 if me_weapon == "uzi" else 0.0,
        1.0 if me_weapon == "revolver" else 0.0,
        1.0 if enemy_weapon == "uzi" else 0.0,
        1.0 if enemy_weapon == "revolver" else 0.0,
        _norm(me.weapon.ammo if me.weapon else 0.0, 25.0),
        _norm(enemy.weapon.ammo if enemy.weapon else 0.0, 25.0),
        _norm(me.shoot_cooldown, 1.0),
        _norm(me.kick_cooldown, 1.0),
        _norm(me.stun_remaining, 1.0),
        _norm(enemy.stun_remaining, 1.0),
        _norm(pickup_dx, 384.0),
        _norm(pickup_dy, 384.0),
        _norm((pickup_dx * pickup_dx + pickup_dy * pickup_dy) ** 0.5, 384.0),
        _norm(pickup_ammo, 25.0),
        pickup_is_uzi,
        pickup_is_revolver,
        _norm(projectile_dx, 384.0),
        _norm(projectile_dy, 384.0),
        _norm(projectile_vx, 700.0),
        _norm(projectile_vy, 700.0),
        _norm(projectile_dist, 384.0),
        _norm(level.time_seconds, max(1.0, level.time_limit_seconds)),
        1.0,
    ]
    if len(features) != FEATURE_SIZE:
        raise RuntimeError(f"expected {FEATURE_SIZE} features, got {len(features)}")
    return features
