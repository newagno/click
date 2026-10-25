import pytest

def test_watchdog_threshold():
    from src.snake.bot import is_session_timeout
    # Threshold is 3000s in CI (50 minutes)
    assert not is_session_timeout(elapsed=2900, is_ci=True)
    assert is_session_timeout(elapsed=3001, is_ci=True)
    assert is_session_timeout(elapsed=3600, is_ci=True)
    # Locally, no 3000s cutoff
    assert not is_session_timeout(elapsed=3200, is_ci=False)
