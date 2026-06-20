import os
import re
import sys
import argparse
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

from scheduler import should_run_now
from state_manager import LocalStateManager
from validator import validate_config
from notifier import send_telegram_message
from browser import IncryptedBrowser, parse_proxy

# pyrefly: ignore [missing-import]
from seleniumbase import SB

def get_binary_path():
    if sys.platform == "win32":
        brave_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        return brave_path if os.path.exists(brave_path) else None
    chrome_path = "/usr/bin/google-chrome"
    return chrome_path if os.path.exists(chrome_path) else None

def save_debug_artifacts(sb, error_msg):
    """Saves a screenshot and HTML dump on failure."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if sb:
            screenshot_path = f"debug_error_{timestamp}.png"
            html_path = f"debug_source_{timestamp}.html"
            sb.save_screenshot(screenshot_path)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(sb.get_page_source())
            print(f"Saved debug artifacts: {screenshot_path}, {html_path}")
        
        with open(f"debug_traceback_{timestamp}.txt", "w", encoding="utf-8") as f:
            f.write(error_msg)
    except Exception as e:
        print(f"Failed to save debug artifacts: {e}")

def main():
    parser = argparse.ArgumentParser(description="Incrypted Daily Claim Bot")
    parser.add_argument("--mode", type=str, default="claim", choices=["check", "claim", "update"], 
                        help="Mode of operation: 'check' (status only), 'claim' (execute claim), 'update' (update state)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode (visible browser window)")
    args = parser.parse_args()

    load_dotenv()

    # 1. Validate environment configuration for execution
    if args.mode == "claim":
        try:
            validate_config()
        except Exception as e:
            print(f"Configuration error: {e}")
            sys.exit(1)

    # 2. Initialize State Manager
    state_manager = LocalStateManager()
    state, source = state_manager.load()
    last_claim_str = state.get("last_claim")
    streak_count = state.get("streak", 0)

    # 3. Check if we should run (Mode: check or claim)
    is_manual = os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
    
    if args.mode in ["check", "claim"]:
        status = should_run_now(last_claim_str, is_manual=is_manual)
        print(f"Status check: {status['reason']} (Source: {status['source']})")
        
        if args.mode == "check":
            # For GitHub Actions output
            output_file = os.getenv("GITHUB_OUTPUT")
            if output_file:
                with open(output_file, "a") as f:
                    f.write(f"should_run={str(status['should_run']).lower()}\n")
                    if status.get("next_run"):
                        f.write(f"next_run={status['next_run']}\n")
            sys.exit(0)
            
        if not status["should_run"]:
            print("Skipping execution: Claim already performed in current cycle.")
            sys.exit(0)

    # 4. Execute Claim
    if args.mode == "claim":
        email = os.getenv("INCRYPTED_EMAIL")
        password = os.getenv("INCRYPTED_PASSWORD")
        proxy_raw = os.getenv("RESIDENTIAL_PROXY", "")

        # Parse proxy for authenticated connections.
        # SeleniumBase with uc=True on Linux requires a Chrome extension for proxy auth.
        # We build the proxy string as scheme://host:port and handle auth via extension args.
        proxy_parts = parse_proxy(proxy_raw)
        if proxy_parts:
            # SB proxy kwarg accepts: scheme://user:pass@host:port
            # With uc=True this works correctly only via the extension approach.
            # Pass full string — SeleniumBase handles extension creation internally.
            proxy_arg = proxy_raw
            print(f"DEBUG: Using proxy → {proxy_parts['scheme']}://***@{proxy_parts['host']}:{proxy_parts['port']}")
        else:
            proxy_arg = None
            print("DEBUG: No proxy configured — connecting directly.")

        # Force GUI mode. Xvfb hides the window in CI.
        # Cloudflare blocks native headless Chrome fingerprints.
        headless = False
        binary_location = get_binary_path()
        sb_context = None
        sb = None

        try:
            print("Initializing browser session...")
            sb_context = SB(
                uc=True,
                headless=headless,
                proxy=proxy_arg,
                binary_location=binary_location,
            )
            sb = sb_context.__enter__()

            browser = IncryptedBrowser(sb, email, password)
            result = browser.execute_claim()

            if result == "claimed":
                print("Claim successful!")
                new_streak = streak_count + 1
                new_state = {"last_claim": datetime.now(timezone.utc).isoformat(), "streak": new_streak}
                state_manager.save(new_state)
                send_telegram_message(f"🎉 <b>Incrypted</b>\nУспішно зібрано щоденну винагороду!\n🔥 Днів підряд: <b>{new_streak}</b>")

            elif result.startswith("cooldown") or result == "already_claimed":
                print(f"Reward already claimed. {result}")
                new_state = {"last_claim": datetime.now(timezone.utc).isoformat(), "streak": streak_count}
                state_manager.save(new_state)
                send_telegram_message(f"✅ <b>Incrypted</b>\nНагороду вже забрано.\n🔥 Днів підряд: <b>{streak_count}</b>")

            elif result.startswith("error"):
                raise Exception(f"Browser claim failed: {result}")
            else:
                raise Exception(f"Unknown result from browser: {result}")

        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"CRITICAL ERROR: {e}\n{error_trace}")
            save_debug_artifacts(sb, error_trace)
            send_telegram_message(f"❌ <b>Incrypted Bot Error</b>\n{e}")
            sys.exit(1)
        finally:
            if sb_context:
                print("Closing browser session and cleaning up resources...")
                try:
                    sb_context.__exit__(None, None, None)
                except Exception as cleanup_err:
                    print(f"Error during browser cleanup: {cleanup_err}")

if __name__ == "__main__":
    main()