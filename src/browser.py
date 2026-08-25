import os
import re
import json
from drag_engine import DragEngine


def parse_proxy(proxy_str: str) -> dict | None:
    """
    Parses proxy string into components using rpartition to safely handle '@' in passwords.
    Accepts formats:
      http://user:pass@host:port
      user:pass@host:port
    Returns dict with keys: scheme, user, password, host, port
    """
    if not proxy_str:
        return None
        
    scheme = "http"
    s = proxy_str
    
    if s.startswith("http://"):
        scheme, s = "http", s[7:]
    elif s.startswith("https://"):
        scheme, s = "https", s[8:]
        
    credentials, _, host_port = s.rpartition('@')
    if not credentials or not host_port:
        return None
        
    user, _, password = credentials.partition(':')
    host, _, port = host_port.partition(':')
    
    if not user or not password or not host or not port:
        return None
        
    return {
        "scheme": scheme,
        "user": user,
        "password": password,
        "host": host,
        "port": int(port),
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
    CHROME_ERR_MARKERS = ["this site can’t be reached", "err_too_many_retries",
                          "err_connection", "err_timed_out", "err_proxy", "dns_probe"]
    for url in PROBE_URLS:
        print(f"DEBUG: Proxy probe → {url}")
        try:
            sb.open(url)
            sb.sleep(3)
            body = sb.get_text("body").strip()
            source = sb.get_page_source().strip()
            body_lower = body.lower()
            print(f"DEBUG: Probe body: {body[:300]!r}")

            if any(m in body_lower for m in CHROME_ERR_MARKERS):
                print(f"DEBUG: Chrome network error detected via {url}. Proxy transport broken.")
                continue
            if source in ("", "<html><head></head><body></body></html>"):
                try:
                    print(f"DEBUG: Proxy empty DOM from {url}")
                    print("DEBUG: FAILED_PROBE_SOURCE_START")
                    print(sb.get_page_source()[:1000])
                    print("DEBUG: FAILED_PROBE_SOURCE_END")
                except Exception:
                    pass
                continue
            if body:
                print(f"DEBUG: Proxy connectivity OK. Visible IP/trace: {body[:200]}")
                return  
        except RuntimeError:
            raise
        except Exception as e:
            print(f"DEBUG: Probe {url} raised exception: {e}")

    raise RuntimeError(
        "Proxy connection failed: all probe URLs returned errors. "
        "Check RESIDENTIAL_PROXY secret — proxy may be offline, misconfigured, or auth is failing."
    )


def wait_for_bypass(sb, timeout=60):
    """Click the Turnstile checkbox until it clears.

    IMPORTANT: in UC mode any CDP call (get_page_source, get_text, execute_script...)
    before/during the challenge can burn it. So: click first, check title only after.
    """
    print(f"DEBUG: Cloudflare bypass started ({timeout}s)...")
    deadline = timeout
    step = 5
    waited = 0
    reconnects = 0
    while waited < deadline:
        # Click the "verify you are human" checkbox. No CDP calls before this!
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            print(f"DEBUG: click attempt failed: {e}")

        # Only now one cheap check: did the title clear?
        try:
            title = sb.get_title().lower()
            if "just a moment" not in title and "cloudflare" not in title:
                print(f"DEBUG: Challenge cleared after ~{waited + step}s.")
                return True
        except Exception:
            pass  # page may be mid-reload; keep clicking

        waited += step
        if waited >= deadline:
            break
        # Every 3rd cycle get a fresh challenge via UC reconnect (also CDP-safe)
        if waited % 15 == 0 and reconnects < 4:
            reconnects += 1
            print(f"DEBUG: Reconnect #{reconnects} for a fresh challenge...")
            try:
                sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 4)
            except Exception as e:
                print(f"DEBUG: reconnect failed: {e}")
        sb.sleep(step)

    print("DEBUG: Cloudflare bypass timeout.")
    return False


COOKIE_FILE = "incrypted_cookies.json"


def _cookie_path() -> str:
    return COOKIE_FILE


def save_cookies(sb, path: str | None = None) -> None:
    """Persist current browser cookies to disk."""
    path = path or _cookie_path()
    try:
        cookies = sb.driver.get_cookies()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
        print(f"DEBUG: Saved {len(cookies)} cookies to {path}")
    except Exception as e:
        print(f"DEBUG: Failed to save cookies: {e}")


def load_cookies(sb, url: str, path: str | None = None) -> bool:
    """Load cookies for the given origin. Returns True if cookies were loaded."""
    path = path or _cookie_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        sb.open(url)
        for c in cookies:
            try:
                sb.driver.add_cookie(c)
            except Exception:
                pass
        # Reload to apply cookies effectively
        sb.open(url)
        print(f"DEBUG: Loaded {len(cookies)} cookies from {path}")
        return True
    except Exception as e:
        print(f"DEBUG: Failed to load cookies: {e}")
        return False


def clear_cookies(path: str | None = None) -> None:
    path = path or _cookie_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


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

        # ── STEP 1: Try cookie-based bypass first ──────────────────────
        print("DEBUG: Loading page via UC mode...")
        self.sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 5)
        self.sb.sleep(3)

        # ── STEP 2: Handle initial Cloudflare Turnstile FIRST — zero CDP before this! ──
        bypassed = wait_for_bypass(self.sb, timeout=60)
        self.sb.sleep(2)

        # Cookie/dashboard checks only AFTER challenge cleared (CDP is safe now)
        if bypassed:
            cookie_loaded = load_cookies(self.sb, "https://incrypted.com/ua/account/")
            log_page_state(self.sb, "After bypass" + (" with cookies" if cookie_loaded else ""))
            dashboard_ready = bool(find_checkin_element(self.sb) or self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled"))
            if dashboard_ready:
                print("DEBUG: Dashboard visible after bypass.")
                return self._finish_claim()

        # ── STEP 3: Wait for either login form OR dashboard to appear ──
        print("DEBUG: Waiting for login form or dashboard elements...")
        found = False
        for i in range(40):
            wait_for_bypass(self.sb, timeout=2)

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
            try:
                print("DEBUG: TIMEOUT SOURCE DUMP START")
                print(self.sb.get_page_source()[:5000])
                print("DEBUG: TIMEOUT SOURCE DUMP END")
            except Exception:
                pass
            body_lower = ""
            try:
                body_lower = (self.sb.get_text("body") or "").lower()
            except Exception:
                pass
            if "cloudflare" in self.sb.get_title().lower() or "just a moment" in self.sb.get_title().lower() or "_cf_chl_opt" in body_lower or "performing security verification" in body_lower:
                return "error|Cloudflare challenge not solved after initial load; uc_gui_click_captcha ineffective or blocked"
            log_page_state(self.sb, "TIMEOUT - neither login form nor dashboard appeared")
            return "error|Page did not load expected content after 80s"

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
            wait_for_bypass(self.sb, timeout=5)
            self.sb.sleep(3)
            log_page_state(self.sb, "After login attempt")

        # ── STEP 5: Verify we successfully reached the account page ──
        current_url = self.sb.get_current_url()
        if "account" not in current_url:
            log_page_state(self.sb, "ERROR - not on account page")
            return "error|Failed to reach account page, stuck at login or Cloudflare?"

        return self._finish_claim()

    def _finish_claim(self) -> str:
        # ── STEP 6: Check if the daily claim section is present ────────
        checkin_selector = find_checkin_element(self.sb)
        disabled_visible = self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled")
        print(f"DEBUG: Daily claim section visible: {bool(checkin_selector) or disabled_visible}")

        if not (checkin_selector or disabled_visible):
            log_page_state(self.sb, "ERROR - daily claim section not found")
            body_text = self.sb.get_text("body")
            if any(kw in body_text for kw in ["Неправильний пароль", "Невірний", "Incorrect password"]):
                clear_cookies()
                return "error|Incorrect credentials"
            return "error|Daily claim section not found on the account dashboard"

        # ── STEP 7: Check cooldown / already claimed ───────────────────
        if disabled_visible:
            print("DEBUG: Daily reward already claimed. Parsing cooldown timer...")
            try:
                timer_text = self.sb.get_text(".drag-daily-check .inc-btn-checkin-disabled").strip()
                print(f"DEBUG: Cooldown timer text: {timer_text}")
                save_cookies(self.sb)
                return f"cooldown|{timer_text}"
            except Exception as e:
                print(f"DEBUG: Could not parse timer: {e}")
                save_cookies(self.sb)
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
            wait_for_bypass(self.sb, timeout=5)
            self.sb.sleep(3)

            print("DEBUG: Drag completed. Waiting dynamically for claim to register...")
            try:
                self.sb.wait_for_element(".drag-daily-check .inc-btn-checkin-disabled", timeout=15)
                print("DEBUG: Claim successfully confirmed dynamically!")
                save_cookies(self.sb)
                return "claimed"
            except Exception:
                print("DEBUG: Cooldown timer not found dynamically. Refreshing page to verify server state...")
                self.sb.refresh()
                self.sb.sleep(5)

                if self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled"):
                    print("DEBUG: Claim successfully confirmed after refresh!")
                    save_cookies(self.sb)
                    return "claimed"
                else:
                    print("DEBUG: Cooldown timer not found after refresh.")
                    clear_cookies()
                    return "error|Slider was dragged, but claim state did not persist on the server"
        except Exception as e:
            clear_cookies()
            return f"error|Failed to drag slider: {str(e)}"
