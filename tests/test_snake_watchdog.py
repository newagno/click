import pytest

def test_watchdog_threshold():
    from src.snake.bot import is_session_timeout
    # Threshold in CI is 3100s + 60s = 3160s
    assert not is_session_timeout(elapsed=3150, is_ci=True)
    assert is_session_timeout(elapsed=3160, is_ci=True)
    assert is_session_timeout(elapsed=3200, is_ci=True)
    # Locally threshold is 3300s + 60s = 3360s
    assert not is_session_timeout(elapsed=3350, is_ci=False)
    assert is_session_timeout(elapsed=3360, is_ci=False)
    assert is_session_timeout(elapsed=3400, is_ci=False)
