import os
import time
import json
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from browser import IncryptedBrowser
from notifier import send_telegram_message

def set_github_output(name, value):
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{name}={value}\n")

load_dotenv()

# Configuration
EMAIL = os.getenv("INCRYPTED_EMAIL")
PASSWORD = os.getenv("INCRYPTED_PASSWORD")
PROXY = os.getenv("RESIDENTIAL_PROXY")
SILENT_ON_COOLDOWN = os.getenv("SILENT_ON_COOLDOWN", "True").lower() == "true"

def load_state():
    last_claim_str = os.getenv("LAST_CLAIM_TIMESTAMP")
    if last_claim_str and last_claim_str.lower() in ["", "none", "null"]:
        last_claim_str = None
        
    streak_count_str = os.getenv("STREAK_COUNT")
    try:
        streak_count = int(streak_count_str) if streak_count_str else 0
    except ValueError:
        streak_count = 0

    return {"last_claim_time": last_claim_str, "streak_count": streak_count}


def get_kyiv_offset(dt):
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    year = dt.year
    march_last = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    march_offset = (march_last.weekday() + 1) % 7
    dst_start = march_last - timedelta(days=march_offset)
    oct_last = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    oct_offset = (oct_last.weekday() + 1) % 7
    dst_end = oct_last - timedelta(days=oct_offset)
    if dst_start <= dt < dst_end:
        return timezone(timedelta(hours=3))
    return timezone(timedelta(hours=2))

def get_kyiv_now():
    now_utc = datetime.now(timezone.utc)
    return now_utc.astimezone(get_kyiv_offset(now_utc))

def get_cycle_start(dt_kyiv):
    # Daily claim cycle resets at 08:00 AM Kyiv time
    today_eight = dt_kyiv.replace(hour=8, minute=0, second=0, microsecond=0)
    if dt_kyiv >= today_eight:
        return today_eight
    return today_eight - timedelta(days=1)

def update_streak(last_claim_str, claim_time_kyiv, streak_count):
    if not last_claim_str:
        return 1
        
    try:
        last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
        last_claim_kyiv = last_claim_time.astimezone(get_kyiv_offset(last_claim_time))
        
        # Calculate the cycle starts
        new_claim_cycle_start = get_cycle_start(claim_time_kyiv)
        prev_cycle_start = new_claim_cycle_start - timedelta(days=1)
        
        # Streak is maintained if last claim was during the previous cycle
        if prev_cycle_start <= last_claim_kyiv < new_claim_cycle_start:
            streak_count += 1
        elif last_claim_kyiv < prev_cycle_start:
            print(f"Streak broken: Last claim was at {last_claim_kyiv}, which is older than previous cycle start {prev_cycle_start}. Resetting streak to 1.")
            streak_count = 1
    except Exception as e:
        print(f"Error updating streak: {e}")
        streak_count = 1
        
    return streak_count

def parse_cooldown_to_timedelta(text):
    hours = 0
    minutes = 0
    seconds = 0
    
    # Ukrainian: г (годин), хв (хвилин), сек (секунд)
    # English: h (hours), m (minutes), s (seconds)
    h_match = re.search(r'(\d+)\s*(?:г|h|hour|hours)', text, re.IGNORECASE)
    m_match = re.search(r'(\d+)\s*(?:хв|m|min|minute|minutes)', text, re.IGNORECASE)
    s_match = re.search(r'(\d+)\s*(?:сек|с|s|sec|second|seconds)', text, re.IGNORECASE)
    
    if h_match:
        hours = int(h_match.group(1))
    if m_match:
        minutes = int(m_match.group(1))
    if s_match:
        seconds = int(s_match.group(1))
        
    if not h_match and not m_match and not s_match:
        # Fallback to HH:MM:SS
        time_match = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text)
        if time_match:
            hours = int(time_match.group(1))
            minutes = int(time_match.group(2))
            seconds = int(time_match.group(3))
            
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)

def claim_daily_reward(max_retries=3):
    if not EMAIL or not PASSWORD:
        raise ValueError("Credentials are not set in environment variables.")

    state = load_state()
    last_claim_str = state.get("last_claim_time")
    streak_count = state.get("streak_count", 0)
    is_manual = os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"

    now_kyiv = get_kyiv_now()
    current_cycle_start = get_cycle_start(now_kyiv)

    was_unclaimed_in_this_cycle = True
    if last_claim_str:
        try:
            _last_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
            _last_kyiv = _last_time.astimezone(get_kyiv_offset(_last_time))
            if _last_kyiv >= current_cycle_start:
                was_unclaimed_in_this_cycle = False
        except Exception:
            pass

    # Smart Cooldown Skip: Check if we have already successfully claimed in the current cycle
    if last_claim_str and not is_manual:
        try:
            last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
            last_claim_kyiv = last_claim_time.astimezone(get_kyiv_offset(last_claim_time))
            if last_claim_kyiv >= current_cycle_start:
                print(f"Skipping check: Already claimed in the current cycle (started at {current_cycle_start}). Last claim: {last_claim_kyiv}.")
                set_github_output("claimed", "true")
                return True
        except Exception as e:
            print(f"Error checking claim interval: {e}")

    for attempt in range(max_retries):
        try:
            browser_session = IncryptedBrowser(EMAIL, PASSWORD, PROXY)
            result = browser_session.execute_claim()
            now = datetime.now(timezone.utc)
            now_kyiv = get_kyiv_now()

            # Case 1: Successfully claimed
            if result == "claimed":
                streak_count = update_streak(last_claim_str, now_kyiv, streak_count)

                state["last_claim_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                state["streak_count"] = streak_count
                
                set_github_output("next_claim_timestamp", state["last_claim_time"])
                set_github_output("next_streak_count", state["streak_count"])

                send_telegram_message(
                    f"🎉 <b>Incrypted</b>\n"
                    f"Успішно зібрано щоденну винагороду!\n"
                    f"🔥 Днів підряд: <b>{streak_count}</b>"
                )
                set_github_output("claimed", "true")
                return True

            # Case 2: Already claimed
            if result == "already_claimed":
                streak_count = update_streak(last_claim_str, now_kyiv, streak_count)

                state["last_claim_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                state["streak_count"] = streak_count
                
                set_github_output("next_claim_timestamp", state["last_claim_time"])
                set_github_output("next_streak_count", state["streak_count"])

                if not SILENT_ON_COOLDOWN or was_unclaimed_in_this_cycle:
                    send_telegram_message(
                        f"✅ <b>Incrypted</b>\n"
                        f"Нагороду вже забрано сьогодні.\n"
                        f"🔥 Днів підряд: <b>{streak_count}</b>"
                    )
                else:
                    print(f"Daily reward already claimed (silent mode). Streak: {streak_count}")
                
                set_github_output("claimed", "true")
                return True
            
            # Case 3: Cooldown active
            if result.startswith("cooldown"):
                timer_info = result.split("|")[1]
                cooldown_td = parse_cooldown_to_timedelta(timer_info)
                
                if cooldown_td > timedelta(0):
                    elapsed = timedelta(hours=24) - cooldown_td
                    claim_time = now - elapsed if elapsed > timedelta(0) else now
                else:
                    claim_time = now - timedelta(hours=12)

                claim_time_kyiv = claim_time.astimezone(get_kyiv_offset(claim_time))
                streak_count = update_streak(last_claim_str, claim_time_kyiv, streak_count)

                state["last_claim_time"] = claim_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                state["streak_count"] = streak_count
                
                set_github_output("next_claim_timestamp", state["last_claim_time"])
                set_github_output("next_streak_count", state["streak_count"])

                if not SILENT_ON_COOLDOWN or was_unclaimed_in_this_cycle:
                    send_telegram_message(
                        f"⏳ <b>Incrypted</b>\n"
                        f"Нагорода вже забрана. {timer_info}\n"
                        f"🔥 Днів підряд: <b>{streak_count}</b>"
                    )
                else:
                    print(f"Daily reward in cooldown: {timer_info} (silent mode). Streak: {streak_count}")
                return True

            if result.startswith("error"):
                error_msg = result.split("|", 1)[1] if "|" in result else result
                raise Exception(error_msg)

            raise Exception(f"Received unknown result format: {result}")

        except Exception as e:
            error_msg = f"Attempt {attempt + 1} failed: {str(e)}"
            print(error_msg)
            if attempt == max_retries - 1:
                # Smart Alerting: Only send TG message if the last claim is older than 30 hours
                state = load_state()
                last_claim_str = state.get("last_claim_time")
                should_alert = True
                if last_claim_str:
                    try:
                        last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        if now - last_claim_time < timedelta(hours=30):
                            should_alert = False
                            print(f"Skipping Telegram error alert because last claim is recent ({last_claim_str}). Will retry next time.")
                    except Exception as ex:
                        print(f"Error parsing last claim time for alert logic: {ex}")
                
                if should_alert:
                    send_telegram_message(f"❌ <b>Incrypted Bot Error</b>\nFailed after {max_retries} attempts.\nError: {str(e)}")
                return False
            time.sleep(2 ** attempt)

if __name__ == "__main__":
    claim_daily_reward()