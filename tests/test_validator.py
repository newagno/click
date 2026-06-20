import pytest
import sys
import os
from unittest.mock import patch

# Add src to sys path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from validator import validate_config

class TestValidator:
    def test_valid_email(self):
        with patch.dict(os.environ, {"INCRYPTED_EMAIL": "test@example.com"}):
            assert validate_config() is True
    
    def test_invalid_email(self):
        with patch.dict(os.environ, {"INCRYPTED_EMAIL": "not-an-email"}):
            with pytest.raises(ValueError, match="Invalid or missing INCRYPTED_EMAIL"):
                validate_config()
                
    def test_valid_proxy(self):
        with patch.dict(os.environ, {
            "INCRYPTED_EMAIL": "test@example.com",
            "RESIDENTIAL_PROXY": "http://user:pass@host:8080"
        }):
            assert validate_config() is True
            
    def test_invalid_proxy(self):
        with patch.dict(os.environ, {
            "INCRYPTED_EMAIL": "test@example.com",
            "RESIDENTIAL_PROXY": "invalid-proxy"
        }):
            with pytest.raises(ValueError, match="Invalid RESIDENTIAL_PROXY format"):
                validate_config()
