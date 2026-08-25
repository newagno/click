import os
import re
import json
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
    m = re.match(r'^(<sup>[\[:\]](#fn-:)</sup>+):(<sup>[\[@\]](#fn-@)</sup>+)@(<sup>[\[:\]](#fn-:)</sup>+):(\d+)$', s)
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


def wait_for_bypass(sb, timeout=45):
    """Aggressive bypass retry until Cloudflare page clears or timeout."""
    print("DEBUG: Aggressive Cloudflare bypass started...")
    deadline = timeout
    step = 4
    waited = 0
    while waited < timeout:
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            print(f"DEBUG: uc_gui_click_captcha attempt failed: {e}")
        try:
            title = sb.get_title().lower()
            body = (sb.get_text("body") or "").lower()
            if (
                "just a moment" not in title
                and "cloudflare" not in title
                and "_cf_chl_opt" not in body
                and "performing security verification" not in body
            ):
                print("DEBUG: Cloudflare challenge cleared.")
                return True
        except Exception:
            pass
        sb.sleep(step)
        waited += step
    print("DEBUG: Cloudflare bypass timeout.")
    return False


def bypass_turnstile(sb):
    """Compatibility wrapper; use wait_for_bypass() for waits."""
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
        print("DEBUG: Trying cookie-based session bypass...")
        cookie_loaded = load_cookies(self.sb, "https://incrypted.com/ua/account/")
        self.sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 5)
        self.sb.sleep(3)
        log_page_state(self.sb, "After initial page load" + (" with cookies" if cookie_loaded else ""))

        dashboard_ready = bool(find_checkin_element(self.sb) or self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled"))
        if cookie_loaded and dashboard_ready:
            print("DEBUG: Cookie bypass worked, dashboard visible.")
            return self._finish_claim()

        # ── STEP 2: Handle initial Cloudflare Turnstile ────────────────
        wait_for_bypass(self.sb, timeout=45)
        self.sb.sleep(2)

        # ── STEP 3: Wait for either login form OR dashboard to appear ──
        print("DEBUG: Waiting for l
