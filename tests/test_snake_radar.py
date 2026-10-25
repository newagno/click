import math
import pytest
from src.snake.bot import check_path_safe_angle

def test_path_safe_blocking_enemy():
    # Snake at (0, 0), cluster at (200, 0) -> angle 0
    # Enemy at (100, 10) -> angle ~5.7 deg (< 30 deg cone) -> unsafe
    safe = check_path_safe_angle(hx=0, hy=0, cx=200, cy=0, ex=100, ey=10)
    assert safe is False

def test_path_safe_flank_enemy():
    # Snake at (0, 0), cluster at (200, 0) -> angle 0
    # Enemy at (0, 150) -> angle 90 deg -> safe
    safe = check_path_safe_angle(hx=0, hy=0, cx=200, cy=0, ex=0, ey=150)
    assert safe is True

def test_path_safe_enemy_beyond_cluster():
    # Snake at (0, 0), cluster at (200, 0)
    # Enemy at (300, 0) -> in front, but further than cluster -> safe
    safe = check_path_safe_angle(hx=0, hy=0, cx=200, cy=0, ex=300, ey=0)
    assert safe is True

def test_path_safe_enemy_behind():
    # Snake at (0, 0), cluster at (200, 0) -> angle 0
    # Enemy at (-100, 0) -> angle 180 deg (behind) -> safe
    safe = check_path_safe_angle(hx=0, hy=0, cx=200, cy=0, ex=-100, ey=0)
    assert safe is True

def test_path_safe_cone_angles():
    # Snake at (0, 0), cluster at (200, 0) -> angle 0
    # Enemy at 25 degrees (inside 30 deg cone) -> unsafe
    ex_inside = 100.0 * math.cos(math.radians(25))
    ey_inside = 100.0 * math.sin(math.radians(25))
    assert check_path_safe_angle(hx=0, hy=0, cx=200, cy=0, ex=ex_inside, ey=ey_inside) is False

    # Enemy at 35 degrees (outside 30 deg cone) -> safe
    ex_outside = 100.0 * math.cos(math.radians(35))
    ey_outside = 100.0 * math.sin(math.radians(35))
    assert check_path_safe_angle(hx=0, hy=0, cx=200, cy=0, ex=ex_outside, ey=ey_outside) is True

def test_path_safe_cone_constants():
    from src.snake.bot import PATH_SAFE_CONE_DEG, PATH_SAFE_CONE_RAD
    assert PATH_SAFE_CONE_DEG == 30.0
    assert abs(PATH_SAFE_CONE_RAD - (math.pi / 6)) < 1e-6

