import pytest

def test_watchdog_threshold():
    from src.snake.bot import is_session_timeout
    # Threshold in CI is 5500s + 60s = 5560s
    assert not is_session_timeout(elapsed=5550, is_ci=True)
    assert is_session_timeout(elapsed=5560, is_ci=True)
    assert is_session_timeout(elapsed=5600, is_ci=True)
    # Locally threshold is 5800s + 60s = 5860s
    assert not is_session_timeout(elapsed=5850, is_ci=False)
    assert is_session_timeout(elapsed=5860, is_ci=False)
    assert is_session_timeout(elapsed=5900, is_ci=False)
