import os
import sys
from seleniumbase import SB


def get_binary_path():
    if sys.platform == "win32":
        brave_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        return brave_path if os.path.exists(brave_path) else None
    # Path for GitHub Actions Linux server
    chrome_path = "/usr/bin/google-chrome"
    return chrome_path if os.path.exists(chrome_path) else None


def log_page_state(sb, label=""):
    """Print detailed state of the page for debugging."""
    try:
        url = sb.get_current_url()
        title = sb.get_title()
        print(f"\n{'='*60}")
        print(f"[STATE] {label}")
        print(f"  URL  : {url}")
        print(f"  TITLE: {title}")

        checks = {
            "Cloudflare iframe": 'iframe[src*="turnstile"]',
            "Login form #llms_login": "#llms_login",
            "Login button #llms_login_button": "#llms_login_button",
            ".drag-daily-check": ".drag-daily-check",
            ".inc-btn-checkin-disabled": ".inc-btn-checkin-disabled",
            "#inc-drag-to-collect": "#inc-drag-to-collect",
            "#inc-drag-to-collect-slider": "#inc-drag-to-collect-slider",
            "#checkin-hours": "#checkin-hours",
            ".account-checkin-balance-section": ".account-checkin-balance-section",
            ".account-chekin-balance-section": ".account-chekin-balance-section",
        }
        print("  Elements visible:")
        for name, sel in checks.items():
            try:
                visible = sb.is_element_visible(sel)
            except:
                visible = "ERROR"
            print(f"    {'[YES]' if visible is True else '[NO] ' if visible is False else '[ERR]'} {name}")

        try:
            body = sb.get_text("body")
            print(f"  Body text (first 400 chars): {body[:400].strip()!r}")
        except:
            pass
        print('='*60 + "\n")
    except Exception as e:
        print(f"[STATE] Could not read page state: {e}")


def bypass_turnstile(sb):
    """Attempt to bypass Cloudflare Turnstile if present."""
    if sb.is_element_visible('iframe[src*="turnstile"]'):
        print("DEBUG: Cloudflare Turnstile detected. Attempting to bypass...")
        try:
            sb.switch_to_frame('iframe[src*="turnstile"]')
            sb.click('span.mark')
            sb.switch_to_default_content()
            sb.sleep(5)
            print("DEBUG: Turnstile bypass attempted successfully.")
            return True
        except Exception as e:
            print(f"DEBUG: Could not click Turnstile: {e}")
            try:
                sb.switch_to_default_content()
            except:
                pass
    return False


class IncryptedBrowser:
    def __init__(self, email, password, proxy):
        self.email = email
        self.password = password
        self.proxy = proxy

    def execute_claim(self) -> str:
        binary_location = get_binary_path()
        # IMPORTANT: Always use headless=False.
        # On Linux (GitHub Actions) we run under xvfb-run which provides a virtual display.
        # headless=True is detected by Cloudflare and results in a completely empty page.
        headless_mode = False

        print(f"DEBUG: Platform={sys.platform}, headless={headless_mode}, binary={binary_location}")
        print(f"DEBUG: Email configured: {'YES' if self.email else 'NO'}")
        print(f"DEBUG: Proxy configured: {'YES' if self.proxy else 'NO'}")

        with SB(uc=True, headless=headless_mode, proxy=self.proxy, binary_location=binary_location) as sb:

            # ── STEP 1: Open the account page ──────────────────────────────
            print("DEBUG: Opening account page...")
            sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 10)
            sb.sleep(3)
            log_page_state(sb, "After initial page load")

            # ── STEP 2: Handle Cloudflare Turnstile on first load ──────────
            bypass_turnstile(sb)
            sb.sleep(2)
            log_page_state(sb, "After initial Turnstile bypass attempt")

            # ── STEP 3: Wait for either login form OR dashboard to appear ──
            print("DEBUG: Waiting for login form or dashboard elements...")
            found = False
            for i in range(8):
                if sb.is_element_visible("#llms_login"):
                    print(f"DEBUG: Login form appeared after {i*2}s")
                    found = True
                    break
                if (sb.is_element_visible(".drag-daily-check")
                        or sb.is_element_visible(".inc-btn-checkin-disabled")
                        or sb.is_element_visible("#inc-drag-to-collect")):
                    print(f"DEBUG: Dashboard (claim section) appeared after {i*2}s - already logged in!")
                    found = True
                    break
                print(f"DEBUG: Waiting... ({(i+1)*2}s elapsed)")
                sb.sleep(2)

            if not found:
                log_page_state(sb, "TIMEOUT - neither login form nor dashboard appeared")
                sb.save_screenshot("debug_error.png")
                return "error|Page did not load expected content after 16s"

            # ── STEP 4: Log in if form is visible ─────────────────────────
            if sb.is_element_visible("#llms_login"):
                print("DEBUG: Filling login credentials...")
                sb.type("#llms_login", self.email)
                sb.type("#llms_password", self.password)
                sb.sleep(1)
                sb.click("#llms_login_button")
                print("DEBUG: Login button clicked. Waiting 10s...")
                sb.sleep(10)

                # Bypass Turnstile again in case it appears after login
                bypass_turnstile(sb)
                sb.sleep(3)
                log_page_state(sb, "After login attempt")

            # ── STEP 5: Verify we are on the account page ─────────────────
            current_url = sb.get_current_url()
            if "account" not in current_url:
                sb.save_screenshot("debug_error.png")
                log_page_state(sb, "ERROR - not on account page")
                return "error|Failed to reach account page, stuck at login or Cloudflare?"

            # ── STEP 6: Check if the daily claim section is present ────────
            section_selectors = [
                ".account-checkin-balance-section",
                ".account-chekin-balance-section",
                ".drag-daily-check",
                ".inc-btn-checkin-disabled",
                "#inc-drag-to-collect",
            ]
            section_visible = any(sb.is_element_visible(sel) for sel in section_selectors)
            print(f"DEBUG: Daily claim section visible: {section_visible}")

            if not section_visible:
                log_page_state(sb, "ERROR - daily claim section not found")
                sb.save_screenshot("debug_error.png")
                try:
                    with open("debug_source.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                except:
                    pass

                body_text = sb.get_text("body")
                if any(kw in body_text for kw in ["Неправильний пароль", "Невірний", "Incorrect password"]):
                    return "error|Incorrect credentials"
                return "error|Daily claim section not found on the account dashboard"

            # ── STEP 7: Check cooldown / already claimed ───────────────────
            is_disabled = False
            try:
                classes = sb.get_attribute(".drag-daily-check", "class")
                if "disabled" in classes:
                    is_disabled = True
                    print(f"DEBUG: .drag-daily-check classes: {classes}")
            except Exception as e:
                print(f"DEBUG: Could not get .drag-daily-check class: {e}")

            cooldown_active = (
                sb.is_element_visible(".inc-btn-checkin-disabled")
                or sb.is_element_visible("#checkin-hours")
                or is_disabled
            )
            print(f"DEBUG: Cooldown active: {cooldown_active}")

            if cooldown_active:
                print("DEBUG: Daily reward already claimed. Parsing cooldown timer...")
                try:
                    hours = sb.get_text("#checkin-hours").strip()
                    minutes = sb.get_text("#checkin-minutes").strip()
                    seconds = sb.get_text("#checkin-seconds").strip()
                    print(f"DEBUG: Cooldown timer: {hours}:{minutes}:{seconds}")
                    return f"cooldown|{hours}:{minutes}:{seconds}"
                except Exception as e:
                    print(f"DEBUG: Could not parse timer: {e}")
                    return "already_claimed"

            # ── STEP 8: Perform the drag-and-drop swipe to claim ──────────
            print("DEBUG: Daily reward unclaimed. Starting slider drag...")
            try:
                sb.wait_for_element("#inc-drag-to-collect-slider", timeout=10)
                width = sb.execute_script(
                    "return document.getElementById('inc-drag-to-collect').offsetWidth;"
                )
                drag_distance = width - 40
                print(f"DEBUG: Dragging slider by {drag_distance}px...")
                sb.drag_and_drop_by_offset("#inc-drag-to-collect-slider", drag_distance, 0)
                sb.sleep(5)

                classes_after = sb.get_attribute(".drag-daily-check", "class")
                if "disabled" in classes_after or sb.is_element_visible(".inc-btn-checkin-disabled"):
                    print("DEBUG: Claim successfully completed!")
                    return "claimed"
                else:
                    sb.sleep(3)
                    if sb.is_element_visible(".inc-btn-checkin-disabled"):
                        return "claimed"
                    return "error|Slider was dragged, but claim state did not update"
            except Exception as e:
                return f"error|Failed to drag slider: {str(e)}"