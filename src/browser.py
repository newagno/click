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

class IncryptedBrowser:
    def __init__(self, email, password, proxy):
        self.email = email
        self.password = password
        self.proxy = proxy

    def execute_claim(self) -> str:
        # Determine the binary path dynamically depending on OS
        binary_location = get_binary_path()
        
        # Set headless mode: False for local Windows testing so we can watch it,
        # and True on Linux/GitHub Actions to comply with server constraints.
        headless_mode = True if sys.platform != "win32" else False
        
        print(f"DEBUG: Starting browser (headless={headless_mode}, binary={binary_location})...")
        
        # Start SeleniumBase with UC mode enabled
        with SB(uc=True, headless=headless_mode, proxy=self.proxy, binary_location=binary_location) as sb:
            print("DEBUG: Opening direct account page...")
            sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 10)
            sb.sleep(3)
            
            # 3. Fill in the login form if present
            if sb.is_element_visible("#llms_login"):
                print("DEBUG: Login form detected. Logging in...")
                sb.type("#llms_login", self.email)
                sb.type("#llms_password", self.password)
                sb.sleep(1)
                
                # Precise login button selector by unique ID to avoid multiple buttons conflict
                sb.click("#llms_login_button")
                sb.sleep(8)
            
            # 4. Check for Cloudflare Turnstile / challenge if present
            if sb.is_element_visible('iframe[src*="turnstile"]'):
                print("DEBUG: Cloudflare Turnstile detected after login. Attempting to bypass...")
                try:
                    sb.switch_to_frame('iframe[src*="turnstile"]')
                    sb.click('span.mark')
                    sb.switch_to_default_content()
                    sb.sleep(5)
                except Exception as e:
                    print(f"DEBUG: Could not click Turnstile: {e}")
            
            # 5. Verify we successfully reached the account page
            current_url = sb.get_current_url()
            if "account" not in current_url:
                sb.save_screenshot("debug_error.png")
                return "error|Failed to reach account page, stuck at login or Cloudflare?"
            
            # If the check-in section isn't visible, check if we had invalid credentials
            if not sb.is_element_visible(".account-chekin-balance-section"):
                sb.save_screenshot("debug_error.png")
                try:
                    with open("debug_source.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                except:
                    pass
                
                body_text = sb.get_text("body")
                if "Неправильний пароль" in body_text or "Невірний" in body_text or "Incorrect password" in body_text:
                    return "error|Incorrect credentials"
                return "error|Daily claim section not found on the account dashboard"
            
            # 6. Check if daily reward has already been claimed / is in cooldown
            is_disabled = False
            try:
                classes = sb.get_attribute(".drag-daily-check", "class")
                if "disabled" in classes:
                    is_disabled = True
            except:
                pass
                
            if sb.is_element_visible(".inc-btn-checkin-disabled") or sb.is_element_visible("#checkin-hours") or is_disabled:
                print("DEBUG: Daily reward already claimed. Parsing cooldown timer...")
                try:
                    hours = sb.get_text("#checkin-hours").strip()
                    minutes = sb.get_text("#checkin-minutes").strip()
                    seconds = sb.get_text("#checkin-seconds").strip()
                    return f"cooldown|{hours}:{minutes}:{seconds}"
                except:
                    return "already_claimed"
            
            # 7. Unclaimed state: perform the dynamic drag-and-drop swipe action
            print("DEBUG: Daily reward unclaimed. Starting collection slider drag...")
            try:
                sb.wait_for_element("#inc-drag-to-collect-slider", timeout=10)
                
                # Get the width of the swipe track container dynamically to handle different screen resolutions
                width = sb.execute_script("return document.getElementById('inc-drag-to-collect').offsetWidth;")
                drag_distance = width - 40
                
                print(f"DEBUG: Dragging slider by {drag_distance}px...")
                sb.drag_and_drop_by_offset("#inc-drag-to-collect-slider", drag_distance, 0)
                sb.sleep(5)
                
                # Verify that the state changed to disabled (claimed)
                classes_after = sb.get_attribute(".drag-daily-check", "class")
                if "disabled" in classes_after or sb.is_element_visible(".inc-btn-checkin-disabled"):
                    print("DEBUG: Claim successfully completed!")
                    return "claimed"
                else:
                    # Give it a few extra seconds to process
                    sb.sleep(3)
                    if sb.is_element_visible(".inc-btn-checkin-disabled"):
                        return "claimed"
                    return "error|Slider was dragged, but claim state did not update"
            except Exception as e:
                return f"error|Failed to drag slider: {str(e)}"