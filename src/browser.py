from drag_engine import DragEngine

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
            print(f"  Body text (first 400 chars): {body[:400].strip()!r}")
        except Exception:
            pass
        try:
            source = sb.get_page_source()
            print(f"  Page Source (first 1000 chars): {source[:1000].strip()!r}")
        except Exception:
            pass
        print('='*60 + "\n")
    except Exception as e:
        print(f"[STATE] Could not read page state: {e}")

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
        "#inc-drag-to-collect",           # primary
        ".drag-daily-check",              # fallback
        "[data-action='daily-checkin']",  # resilient
        "//button[contains(text(), 'Claim')]",  # XPath fallback
        ".account-checkin-balance-section"
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
        # ── Connection & Proxy Diagnosis ──────────────────────────────
        print("DEBUG: Setting page load timeout to 30s...")
        try:
            self.sb.driver.set_page_load_timeout(30)
        except Exception as e:
            print(f"DEBUG: Could not set page load timeout: {e}")

        # ── STEP 1: Open the account page directly ─────────────────────
        print("DEBUG: Opening account page...")
        self.sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 10)
        self.sb.sleep(3)
        log_page_state(self.sb, "After initial page load")

        # ── STEP 2: Handle initial Cloudflare Turnstile ────────────────
        bypass_turnstile(self.sb)
        self.sb.sleep(2)

        # ── STEP 3: Wait for either login form OR dashboard to appear ──
        print("DEBUG: Waiting for login form or dashboard elements...")
        found = False
        for i in range(15):
            if self.sb.is_element_visible('iframe[src*="turnstile"]'):
                print("DEBUG: Turnstile detected during wait, attempting bypass...")
                bypass_turnstile(self.sb)
                self.sb.sleep(1)

            if self.sb.is_element_visible("#llms_login"):
                print(f"DEBUG: Login form appeared after {i*2}s")
                found = True
                break
            
            # Use our resilient selector finder
            if find_checkin_element(self.sb) or self.sb.is_element_visible(".drag-daily-check .inc-btn-checkin-disabled"):
                print(f"DEBUG: Dashboard (claim section) appeared after {i*2}s - already logged in!")
                found = True
                break
            
            print(f"DEBUG: Waiting... ({(i+1)*2}s elapsed)")
            self.sb.sleep(2)

        if not found:
            log_page_state(self.sb, "TIMEOUT - neither login form nor dashboard appeared")
            return "error|Page did not load expected content after 30s"

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
            
            # Determine drag distance
            try:
                width = self.sb.execute_script(
                    "return document.querySelector('.inc-swipe-btn') ? document.querySelector('.inc-swipe-btn').offsetWidth : 350;"
                )
                drag_distance = width - 20
            except Exception:
                drag_distance = 350
            
            # Execute Hybrid Drag
            engine = DragEngine(self.sb)
            engine.perform_drag(slider_selector, drag_distance)

            # Check for Turnstile after drag
            print("DEBUG: Checking for Turnstile post-drag...")
            bypass_turnstile(self.sb)
            self.sb.sleep(3)

            print("DEBUG: Drag completed. Waiting dynamically for claim to register...")
            try:
                # Wait for the disabled button to appear
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