import pytest

def test_avoid_radius_scaling():
    from src.snake.bot import calculate_avoid_radius
    assert calculate_avoid_radius(100) == 220.0
    assert calculate_avoid_radius(1000) == 260.0
    assert calculate_avoid_radius(2500) == 380.0
    assert calculate_avoid_radius(3500) == 400.0

def test_phase_weights():
    from src.snake.bot import get_phase_weights
    # Phase 1: mass < 400
    p1 = get_phase_weights(350)
    assert p1["phase"] == 1
    assert p1["food"] == 1.0
    assert p1["cluster"] == 2.0

    # Phase 2: 400 <= mass <= 1500
    p2 = get_phase_weights(800)
    assert p2["phase"] == 2
    assert p2["food"] == 1.0
    assert p2["cluster"] == 2.5

    # Phase 3: mass > 1500 (critical decay compensation)
    p3 = get_phase_weights(2000)
    assert p3["phase"] == 3
    assert p3["food"] == 1.5
    assert p3["cluster"] == 3.0
