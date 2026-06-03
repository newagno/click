import os
import time
import json
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from browser import IncryptedBrowser
from notifier import send_telegram_message

load_dotenv()

# Configuration
EMAIL = os.getenv("INCRYPTED_EMAIL")
PASSWORD = os.getenv("INCRYPTED_PASSWORD")
PROXY = os.getenv("RESIDENTIAL_PROXY")
SILENT_ON_COOLDOWN = os.getenv("SILENT_ON_COOLDOWN", "True").lower() == "true"
STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading state.json: {e}")
    return {"last_claim_time": None, "streak_count": 0}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving state.json: {e}")

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

    # Smart Cooldown Skip: Check if 23 hours have passed since the last claim
    if last_claim_str and not is_manual:
        try:
            last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            time_since_last_claim = now - last_claim_time
            if time_since_last_claim < timedelta(hours=23):
                hours_left = timedelta(hours=23) - time_since_last_claim
                print(f"Skipping check: Last claim was at {last_claim_str}. Time since last claim: {time_since_last_claim}. Next check in: {hours_left}.")
                return True
        except Exception as e:
            print(f"Error checking claim interval: {e}")

    for attempt in range(max_retries):
        try:
            browser_session = IncryptedBrowser(EMAIL, PASSWORD, PROXY)
            result = browser_session.execute_claim()
            now = datetime.now(timezone.utc)

            # Case 1: Successfully claimed
            if result == "claimed":
                if last_claim_str:
                    try:
                        last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
                        time_diff = now - last_claim_time
                        if timedelta(hours=20) <= time_diff < timedelta(hours=40):
                            streak_count += 1
                        elif time_diff >= timedelta(hours=40):
                            print(f"Streak broken: time difference is {time_diff} (>= 40 hours). Resetting to 1.")
                            streak_count = 1
                    except Exception as e:
                        print(f"Error updating streak: {e}")
                        streak_count = 1
                else:
                    streak_count = 1

                state["last_claim_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                state["streak_count"] = streak_count
                save_state(state)

                send_telegram_message(
                    f"🎉 <b>Incrypted</b>\n"
                    f"Успішно зібрано щоденну винагороду!\n"
                    f"🔥 Днів підряд: <b>{streak_count}</b>"
                )
                return True

            # Case 2: Already claimed
            if result == "already_claimed":
                if last_claim_str:
                    try:
                        last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
                        time_diff = now - last_claim_time
                        if timedelta(hours=20) <= time_diff < timedelta(hours=40):
                            streak_count += 1
                        elif time_diff >= timedelta(hours=40):
                            streak_count = 1
                    except Exception as e:
                        print(f"Error updating streak: {e}")
                        streak_count = 1
                else:
                    streak_count = 1

                state["last_claim_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                state["streak_count"] = streak_count
                save_state(state)

                if not SILENT_ON_COOLDOWN:
                    send_telegram_message(
                        f"✅ <b>Incrypted</b>\n"
                        f"Вже забрано сьогодні.\n"
                        f"🔥 Днів підряд: <b>{streak_count}</b>"
                    )
                else:
                    print(f"Daily reward already claimed (silent mode). Streak: {streak_count}")
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

                if last_claim_str:
                    try:
                        last_claim_time = datetime.fromisoformat(last_claim_str.replace("Z", "+00:00"))
                        time_diff = claim_time - last_claim_time
                        if timedelta(hours=20) <= time_diff < timedelta(hours=40):
                            streak_count += 1
                        elif time_diff >= timedelta(hours=40):
                            streak_count = 1
                    except Exception as e:
                        print(f"Error updating streak: {e}")
                        streak_count = 1
                else:
                    streak_count = 1

                state["last_claim_time"] = claim_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                state["streak_count"] = streak_count
                save_state(state)

                if not SILENT_ON_COOLDOWN:
                    send_telegram_message(
                        f"⏳ <b>Incrypted</b>\n"
                        f"Ще не час. {timer_info}\n"
                        f"🔥 Днів підряд: <b>{streak_count}</b>"
                    )
                else:
                    print(f"Daily reward in cooldown: {timer_info} (silent mode). Streak: {streak_count}")
                return True

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