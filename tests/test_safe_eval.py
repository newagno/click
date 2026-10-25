import os
import sys
import pytest

sys.path.insert(0, os.path.abspath("."))
from src.snake.bot import _is_cdp_mode, safe_eval


class FakeDriver:
    def __init__(self, cdp_active=False, connected=True, swap_needed=False):
        self._cdp_active = cdp_active
        self._connected = connected
        self._swap_needed = swap_needed

    def _BaseCase__is_cdp_swap_needed(self):
        return self._swap_needed

    def is_cdp_mode_active(self):
        return self._cdp_active

    def is_connected(self):
        return self._connected


class FakeSB:
    def __init__(self, is_cdp=False, driver=None):
        self.driver = driver or FakeDriver(cdp_active=is_cdp, connected=not is_cdp, swap_needed=is_cdp)
        self.last_script = None

    def execute_script(self, script):
        self.last_script = script
        return "EVAL_OK"


def test_is_cdp_mode_detection():
    # 1. WebDriver connected -> False
    sb_wd = FakeSB(is_cdp=False)
    assert not _is_cdp_mode(sb_wd)

    # 2. Driver swap needed -> True
    sb_cdp = FakeSB(is_cdp=True)
    assert _is_cdp_mode(sb_cdp)

    # 3. Fallback via driver type name
    class UndetectedCDPDriver:
        def is_connected(self):
            return False

    sb_type = FakeSB(driver=UndetectedCDPDriver())
    assert _is_cdp_mode(sb_type)


def test_safe_eval_single_line_webdriver():
    sb = FakeSB(is_cdp=False)
    res = safe_eval(sb, "return 42;")
    assert res == "EVAL_OK"
    assert sb.last_script == "return 42;"


def test_safe_eval_single_line_cdp():
    sb = FakeSB(is_cdp=True)
    res = safe_eval(sb, "return 42;")
    assert res == "EVAL_OK"
    # In CDP mode, top-level return is stripped to avoid SyntaxError
    assert sb.last_script == "42"


def test_safe_eval_multiline_with_return_webdriver():
    sb = FakeSB(is_cdp=False)
    code = """
    var wp = window.CryptoSnakeWP || {};
    return {
        userId: wp.userId || 0
    };
    """
    res = safe_eval(sb, code)
    assert res == "EVAL_OK"
    assert sb.last_script.startswith("return (() => {")
    assert sb.last_script.rstrip().endswith("})();")


def test_safe_eval_multiline_with_return_cdp():
    sb = FakeSB(is_cdp=True)
    code = """
    var wp = window.CryptoSnakeWP || {};
    return {
        userId: wp.userId || 0
    };
    """
    res = safe_eval(sb, code)
    assert res == "EVAL_OK"
    # In CDP mode, MUST NOT start with 'return '
    assert not sb.last_script.startswith("return ")
    assert sb.last_script.startswith("(() => {")
    assert sb.last_script.rstrip().endswith("})()")


def test_safe_eval_existing_iife_with_newline_cdp():
    sb = FakeSB(is_cdp=True)
    code = """return (() => {
        var canvas = document.querySelector('#cs-canvas');
        return { is_dead: true };
    })()\n\n"""
    res = safe_eval(sb, code)
    assert res == "EVAL_OK"
    # Strips outer return even with trailing newlines
    assert not sb.last_script.startswith("return ")
    assert sb.last_script.startswith("(() => {")
    assert sb.last_script.rstrip().endswith("})()")


def test_safe_eval_side_effect_without_return():
    sb = FakeSB(is_cdp=False)
    cmd = "document.querySelector('#cs-canvas').focus();"
    res = safe_eval(sb, cmd)
    assert res == "EVAL_OK"
    assert sb.last_script == cmd
