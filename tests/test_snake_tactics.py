import math
import pytest


def calculate_wall_buffer(mass: float) -> float:
    """Calculates wall buffer based on snake mass (dynamic safeRadius: 380-600px)."""
    return max(380.0, min(600.0, mass * 0.08))


def calculate_decay_rate(mass: float) -> float:
    """Calculates decay loss per second based on cryptosnake.min.js engine formula:
    me.mass > 400 && (me.mass -= (me.mass - 400) * 0.01 * dt)
    """
    if mass > 400:
        return (mass - 400) * 0.01
    return 0.0


def should_boost(cluster_mass: float, min_enemy_dist: float, me_mass: float, cluster_dist: float, dist_from_center: float, safe_radius: float) -> bool:
    """Evaluates whether to trigger Turbo Boost impulse."""
    min_mass_for_boost = max(100.0, me_mass * 0.5)
    return (
        cluster_mass >= 21.0 and
        min_enemy_dist > 250.0 and
        me_mass >= min_mass_for_boost and
        cluster_dist > 40.0 and
        dist_from_center < (safe_radius - 100.0)
    )


def test_wall_buffer_scaling():
    assert calculate_wall_buffer(10.0) == 380.0
    assert calculate_wall_buffer(100.0) == 380.0
    assert calculate_wall_buffer(5000.0) == 400.0
    assert calculate_wall_buffer(7500.0) == 600.0
    assert calculate_wall_buffer(15000.0) == 600.0


def test_decay_formula():
    assert calculate_decay_rate(100.0) == 0.0
    assert calculate_decay_rate(400.0) == 0.0
    assert calculate_decay_rate(1000.0) == pytest.approx(6.0)
    assert calculate_decay_rate(5000.0) == pytest.approx(46.0)


def test_boost_safety_guards():
    # Healthy case: big cluster, safe enemy distance, good mass
    assert should_boost(
        cluster_mass=35.0,
        min_enemy_dist=300.0,
        me_mass=300.0,
        cluster_dist=120.0,
        dist_from_center=500.0,
        safe_radius=1600.0
    ) is True

    # Blocked by low cluster mass (< 21)
    assert should_boost(
        cluster_mass=15.0,
        min_enemy_dist=300.0,
        me_mass=300.0,
        cluster_dist=120.0,
        dist_from_center=500.0,
        safe_radius=1600.0
    ) is False

    # Blocked by close enemy (<= 250px)
    assert should_boost(
        cluster_mass=35.0,
        min_enemy_dist=240.0,
        me_mass=300.0,
        cluster_dist=120.0,
        dist_from_center=500.0,
        safe_radius=1600.0
    ) is False

    # Blocked by low mass (< 100)
    assert should_boost(
        cluster_mass=35.0,
        min_enemy_dist=300.0,
        me_mass=80.0,
        cluster_dist=120.0,
        dist_from_center=500.0,
        safe_radius=1600.0
    ) is False

    # Blocked by close proximity to wall (dist_from_center >= safe_radius - 100)
    assert should_boost(
        cluster_mass=35.0,
        min_enemy_dist=300.0,
        me_mass=300.0,
        cluster_dist=120.0,
        dist_from_center=1550.0,
        safe_radius=1600.0
    ) is False
