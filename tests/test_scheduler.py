import pytest
import sys
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Add src to sys path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from scheduler import should_run_now, get_cycle_start, KYIV_TZ

class TestScheduler:
    def test_manual_always_runs(self):
        result = should_run_now(None, is_manual=True)
        assert result["should_run"] is True
        assert result["source"] == "manual"
    
    def test_no_previous_claim(self):
        result = should_run_now(None)
        assert result["should_run"] is True
        assert result["source"] == "unclaimed"
    
    def test_already_claimed_today(self):
        # Pretend we claimed it today at 08:30 Kyiv
        now = datetime.now(KYIV_TZ)
        today_cycle = get_cycle_start(now)
        claim_time = today_cycle + timedelta(minutes=30)
        
        result = should_run_now(claim_time.astimezone(timezone.utc).isoformat())
        assert result["should_run"] is False
        assert result["source"] == "cooldown"
        assert result["next_run"] is not None
    
    def test_claimed_yesterday(self):
        # Pretend we claimed it yesterday
        now = datetime.now(KYIV_TZ)
        today_cycle = get_cycle_start(now)
        yesterday_claim = today_cycle - timedelta(hours=5)
        
        result = should_run_now(yesterday_claim.astimezone(timezone.utc).isoformat())
        assert result["should_run"] is True
        assert result["source"] == "unclaimed"
