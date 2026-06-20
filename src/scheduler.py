import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Optional

KYIV_TZ = ZoneInfo("Europe/Kyiv")
CYCLE_HOUR = 8  # 08:00 Kyiv time

def get_cycle_start(dt_kyiv: datetime) -> datetime:
    """Get the start of the current claim cycle (08:00 Kyiv)."""
    today = dt_kyiv.replace(hour=CYCLE_HOUR, minute=0, second=0, microsecond=0)
    if dt_kyiv >= today:
        return today
    return today - timedelta(days=1)

def parse_cooldown(text: str) -> timedelta:
    """Parse cooldown text into timedelta."""
    # Format HH:MM:SS
    if match := re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text):
        h, m, s = map(int, match.groups())
        return timedelta(hours=h, minutes=m, seconds=s)
        
    # Format "9г 29хв 27сек" or english
    h_match = re.search(r'(\d+)\s*(?:г|h|hour|hours)', text, re.IGNORECASE)
    m_match = re.search(r'(\d+)\s*(?:хв|m|min|minute|minutes)', text, re.IGNORECASE)
    s_match = re.search(r'(\d+)\s*(?:сек|с|s|sec|second|seconds)', text, re.IGNORECASE)
    
    h = int(h_match.group(1)) if h_match else 0
    m = int(m_match.group(1)) if m_match else 0
    s = int(s_match.group(1)) if s_match else 0
    
    return timedelta(hours=h, minutes=m, seconds=s)

def should_run_now(last_claim_str: Optional[str], is_manual: bool = False) -> Dict:
    """
    Returns:
    {
        "should_run": bool,
        "reason": str,
        "next_run": str,  # ISO8601 UTC
        "source": str      # "manual", "unclaimed", "cooldown", "error"
    }
    """
    if is_manual:
        return {
            "should_run": True,
            "reason": "Manual trigger",
            "next_run": None,
            "source": "manual"
        }
    
    if not last_claim_str:
        return {
            "should_run": True,
            "reason": "No previous claim found",
            "next_run": None,
            "source": "unclaimed"
        }
    
    try:
        last_claim = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
        last_claim_kyiv = last_claim.astimezone(KYIV_TZ)
        now_kyiv = datetime.now(KYIV_TZ)
        
        current_cycle = get_cycle_start(now_kyiv)
        
        if last_claim_kyiv >= current_cycle:
            next_cycle = current_cycle + timedelta(days=1)
            return {
                "should_run": False,
                "reason": f"Already claimed in current cycle (since {current_cycle})",
                "next_run": next_cycle.astimezone(ZoneInfo("UTC")).isoformat(),
                "source": "cooldown"
            }
        
        return {
            "should_run": True,
            "reason": f"Last claim {last_claim_kyiv} is before current cycle {current_cycle}",
            "next_run": None,
            "source": "unclaimed"
        }
    except Exception as e:
        return {
            "should_run": True,
            "reason": f"Error parsing last claim: {e}",
            "next_run": None,
            "source": "error"
        }
