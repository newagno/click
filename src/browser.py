import os
import re
from drag_engine import DragEngine


def parse_proxy(proxy_str: str) -> dict | None:
    """
    Parses proxy string into components.
    Accepts formats:
      http://user:pass@host:port
      user:pass@host:port
    Returns dict with keys: scheme, user, password, host, port
    """
    if not proxy_str:
        return None
    # Strip scheme if present
    scheme = "http"
    s = proxy_str
    m = re.match(r'^(https?)://', s)
    if m:
        scheme = m.group(1)
        s = s[len(m.group(0)):]
    # Parse user:pass@host:port
    m = re.match(r'^([^:]+):([^@]+)@([^:]+):(\d+)$', s)
    if not m:
        return None
    return {
        "scheme": scheme,
        "user": m.group(1),
        "password": m.group(2),
        "host": m.group(3),
        "port": int(m.group(4)),
    }


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
            "Dashboard section": "[data-action='daily-checkin']",
            "Disabled button": ".drag-daily-check .inc-btn-checkin-disabled",
            "Slider #locker": "#locker",
        }
        print("  Elements visible:")
        for name, sel in checks.items():
            try:
                visible = sb.is_element_visible(sel)
            except Exception:
                visible = "ERROR"
            print(f"    {'[YES]' if visible is True else '[NO] ' if visible is False else '[ERR]'} {name}")

        try:
            body = sb.get_text("body")
            print(f"  Body text (first 500 chars): {body[:500].strip()!r}")
        except Exception:
            pass
        try:
            source = sb.get_page_source()
            print(f"  Page source (first 1500 chars):\n{source[:1500]}")
        except Exception:
            pass
        print('='*60 + "\n")
    except Exception as e:
        print(f"[STATE] Could not read page state: {e}")


def check_proxy_connectivity(sb):
    """
    FAIL-FAST proxy diagnostic.
    Opens a simple IP echo endpoint before touching the target site.
    Raises RuntimeError if connection fails or returns empty DOM.
    """
    PROBE_URLS = [
        "https://cloudflare.com/cdn-cgi/trace",
        "https://api.ipify.org?format=text",
    ]
    for url in PROBE_URLS:
        print(f"DEBUG: Proxy probe → {url}")
        try:
            sb.open(url)
            sb.sleep(3)
            body = sb.get_text("body").strip()
            source = sb.get_page_source().strip()
            print(f"DEBUG: Probe body: {body[:300]!r}")

            # Detect empty DOM: Cloudflare returns rich HTML, ipify returns just an IP.
            if source in ("", "<html><head></head><body></body></html>"):
                raise RuntimeError(
                    f"Proxy connection failed: empty DOM received from {url}. "
                    "Check RESIDENTIAL_PROXY secret — proxy may be offline, misconfigured, or auth is failing."
                )
            if body:
                print(f"DEBUG: Proxy connectivity OK. Visible IP/trace: {body[:200]}")
                return  # success
        except RuntimeError:
            raise
        except Exception as e:
            print(f"DEBUG: Probe {url} raised exception: {e}")

    raise RuntimeError(
        "Proxy connection failed: all probe URLs returned errors. "
        "Check RESIDENTIAL_PROXY secret — proxy may be offline, misconfigured, or auth is failing."
    )


def bypass_turnstile(sb):
    """Attempt to bypass Cloudflare Turnstile if present."""
    print("DEBUG: Checking for Turnstile iframe...")
    try:
        visible = sb.is_element_visible('iframe[src*="turnstile"]')
    except Exception as e:
        print(f"DEBUG: Error checking Turnstile visibility: {e}")
        visible = False

    if visible:
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
            except Exception:
                pass
    return False


def find_checkin_element(sb):
    """Resilient selector for the claim section."""
    selectors = [
        "#inc-drag-to-collect",
        ".drag-daily-check",
        "[data-action='daily-checkin']",
        ".account-checkin-balance-section",
    ]
    for sel in selectors:
        try:
            if sb.is_element_visible(sel):
                return sel
        except Exception:
            continue
    return None


class IncryptedBrowser:
    def __init__(self, sb, email, password):
        self.sb = sb
        self.email = email
        self.password = password

    def execute_claim(self) -> str:
        # ── STEP 0: Proxy connectivity fail-fast check ─────────────────
        print("DEBUG: Running proxy connectivity check...")
        check_proxy_connectivity(self.sb)
        print("DEBUG: Proxy check passed. Proceeding.")

        # ── Page load timeout ──────────────────────────────────────────
        print("DEBUG: Setting page load timeout to 45s...")
        try:
            self.sb.driver.set_page_load_timeout(45)
        except Exception as e:
            print(f"DEBUG: Could not set page load timeout: {e}")

        # ── STEP 1: Open the account page directly ─────────────────────
        print("DEBUG: Opening account page...")
        self.sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 10)
        self.sb.sleep(4)
        log_page_state(self.sb, "After initial page load")

        # ── STEP 2: Handle initial Cloudflare Turnstile ────────────────
        bypass_turnstile(self.sb)
        self.sb.sleep(2)

        # ── STEP 3: Wait for either login form OR dashboard to appear ──
        print("DEBUG: Waiting for login form or dashboard elements...")
        found = False
        for i in range(20):
            if self.sb.is_element_visible('iframe[src*="turnstile"]'):
                print("DEBUG: Turnstile detected during wait, attempting bypass...")
                bypass_turnstile(self.sb)
                self.sb.sleep(1)

            if self.sb.is_element_visible("#llms_login"):
                print(f"DEBUG: Login form appeared after {(i+1)*2}s")
                found = True
                break

            if find_checkin_element(self.sb) or self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled"):
                print(f"DEBUG: Dashboard (claim section) appeared after {(i+1)*2}s - already logged in!")
                found = True
                break

            print(f"DEBUG: Waiting... ({(i+1)*2}s elapsed)")
            self.sb.sleep(2)

        if not found:
            log_page_state(self.sb, "TIMEOUT - neither login form nor dashboard appeared")
            return "error|Page did not load expected content after 40s"

        # ── STEP 4: Log in if form is visible ─────────────────────────
        if self.sb.is_element_visible("#llms_login"):
            print("DEBUG: Filling login credentials...")
            self.sb.type("#llms_login", self.email)
            self.sb.type("#llms_password", self.password)
            self.sb.sleep(1)
            self.sb.click("#llms_login_button")
            print("DEBUG: Login button clicked. Waiting 10s...")
            self.sb.sleep(10)

            print("DEBUG: Checking Turnstile after login click...")
            bypass_turnstile(self.sb)
            self.sb.sleep(3)
            log_page_state(self.sb, "After login attempt")

        # ── STEP 5: Verify we successfully reached the account page ──
        current_url = self.sb.get_current_url()
        if "account" not in current_url:
            log_page_state(self.sb, "ERROR - not on account page")
            return "error|Failed to reach account page, stuck at login or Cloudflare?"

        # ── STEP 6: Check if the daily claim section is present ────────
        checkin_selector = find_checkin_element(self.sb)
        disabled_visible = self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled")
        print(f"DEBUG: Daily claim section visible: {bool(checkin_selector) or disabled_visible}")

        if not (checkin_selector or disabled_visible):
            log_page_state(self.sb, "ERROR - daily claim section not found")
            body_text = self.sb.get_text("body")
            if any(kw in body_text for kw in ["Неправильний пароль", "Невірний", "Incorrect password"]):
                return "error|Incorrect credentials"
            return "error|Daily claim section not found on the account dashboard"

        # ── STEP 7: Check cooldown / already claimed ───────────────────
        if disabled_visible:
            print("DEBUG: Daily reward already claimed. Parsing cooldown timer...")
            try:
                timer_text = self.sb.get_text(".drag-daily-check .inc-btn-checkin-disabled").strip()
                print(f"DEBUG: Cooldown timer text: {timer_text}")
                return f"cooldown|{timer_text}"
            except Exception as e:
                print(f"DEBUG: Could not parse timer: {e}")
                return "already_claimed"

        # ── STEP 8: Perform the drag-and-drop swipe to claim ──────────
        print("DEBUG: Daily reward unclaimed. Starting slider drag...")
        slider_selector = "#locker"
        print(f"DEBUG: Using slider selector: {slider_selector}")

        try:
            self.sb.wait_for_element(slider_selector, timeout=10)

            try:
                width = self.sb.execute_script(
                    "return document.querySelector('.inc-swipe-btn') ? document.querySelector('.inc-swipe-btn').offsetWidth : 350;"
                )
                drag_distance = width - 20
            except Exception:
                drag_distance = 350

            engine = DragEngine(self.sb)
            engine.perform_drag(slider_selector, drag_distance)

            print("DEBUG: Checking for Turnstile post-drag...")
            bypass_turnstile(self.sb)
            self.sb.sleep(3)

            print("DEBUG: Drag completed. Waiting dynamically for claim to register...")
            try:
                self.sb.wait_for_element(".drag-daily-check .inc-btn-checkin-disabled", timeout=15)
                print("DEBUG: Claim successfully confirmed dynamically!")
                return "claimed"
            except Exception:
                print("DEBUG: Cooldown timer not found dynamically. Refreshing page to verify server state...")
                self.sb.refresh()
                self.sb.sleep(5)

                if self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled"):
                    print("DEBUG: Claim successfully confirmed after refresh!")
                    return "claimed"
                else:
                    print("DEBUG: Cooldown timer not found after refresh.")
                    return "error|Slider was dragged, but claim state did not persist on the server"
        except Exception as e:
            return f"error|Failed to drag slider: {str(e)}"