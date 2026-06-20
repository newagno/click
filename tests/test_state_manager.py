import pytest
from unittest.mock import patch, mock_open
import sys
import os

# Add src to sys path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from state_manager import LocalStateManager

class TestLocalStateManager:
    @patch('os.path.exists', return_value=False)
    def test_load_no_file(self, mock_exists):
        sm = LocalStateManager()
        state, source = sm.load()
        assert source == "default"
        assert state["last_claim"] is None

    @patch('os.path.exists', return_value=True)
    def test_load_from_file(self, mock_exists):
        m = mock_open(read_data='{"last_claim": "2026-06-20T08:00:00+00:00", "streak": 5}')
        with patch('builtins.open', m):
            sm = LocalStateManager()
            state, source = sm.load()
            assert source == "local"
            assert state["streak"] == 5

    def test_save_to_file(self):
        m = mock_open()
        with patch('builtins.open', m):
            sm = LocalStateManager()
            result = sm.save({"last_claim": "2026-06-20T08:00:00+00:00", "streak": 6})
            assert result is True
            m.assert_called_once_with(sm.state_file, "w", encoding="utf-8")
