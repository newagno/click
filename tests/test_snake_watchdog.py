import pytest

def test_watchdog_threshold():
    from src.snake.bot import is_session_timeout
    # Threshold in CI is 2100s + 60s = 2160s
    assert not is_session_timeout(elapsed=2150, is_ci=True)
    assert is_session_timeout(elapsed=2160, is_ci=True)
    assert is_session_timeout(elapsed=2200, is_ci=True)
    # Locally threshold is 2400s + 60s = 2460s
    assert not is_session_timeout(elapsed=2450, is_ci=False)
    assert is_session_timeout(elapsed=2460, is_ci=False)
    assert is_session_timeout(elapsed=2500, is_ci=False)
