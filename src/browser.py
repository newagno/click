from seleniumbase import SB
import sys

class IncryptedBrowser:
    def __init__(self, email, password, proxy):
        self.email = email
        self.password = password
        self.proxy = proxy

    def _get_binary_path(self):
        if sys.platform == "win32":
            return r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        return "/usr/bin/google-chrome"

    def execute_claim(self) -> str:
        """
        Виконує логін, проходить Cloudflare, перетягує слайдер збору нагород.
        Повертає 'claimed', 'already_claimed' або викидає помилку з описом стану.
        """
        # Змінити headless=True при деплої на GitHub Actions
        with SB(uc=True, headless=False, proxy=self.proxy, binary_location=self._get_binary_path()) as sb:
            
            # 1. Авторизація на головній сторінці через попап
            sb.uc_open_with_reconnect("https://incrypted.com/ua/", 5)
            
            try:
                sb.wait_for_element('.js-login-popup-btn', timeout=15)
                sb.click('.js-login-popup-btn')
                sb.sleep(3)
            except Exception as e:
                return f"error|Could not open login popup: {str(e)}"

            try:
                sb.wait_for_element('#llms_login', timeout=15)
                sb.type('#llms_login', self.email)
                sb.type('#llms_password', self.password)
                sb.execute_script("document.getElementById('llms_login_button').click();")
                sb.sleep(8) 
            except Exception as e:
                return f"error|Login form interaction failed: {str(e)}"

            # 2. Перехід на сторінку особистого кабінету з нагородами
            sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", 5)
            sb.sleep(5)

            # Визначення селекторів елементів слайдера
            disabled_indicator = ".inc-btn-checkin-disabled"
            slider_handle = "#locker"
            success_indicator = ".inc-drag-to-collect-icon-collected"

            # Перевірка наявності таймера кулдауну
            if sb.is_element_visible(disabled_indicator):
                return "already_claimed"

            # Логіка виконання Drag-and-Drop жесту
            if sb.is_element_visible(slider_handle):
                try:
                    # Метод затискає елемент #locker і тягне його вправо по осі X.
                    # Зсув на 350 пікселів гарантує проходження повного треку слайдера.
                    sb.drag_and_drop_by_offset(slider_handle, 350, 0)
                    sb.sleep(4)
                    
                    # Верифікація успішного виконання операції
                    if sb.is_element_visible(success_indicator) or not sb.is_element_visible(slider_handle):
                        return "claimed"
                    
                    return "error|Slider moved, but verification element 'Собрано' not detected"
                except Exception as e:
                    return f"error|Slider mechanical drag failed: {str(e)}"
            
            return "error|Daily claim interactive elements not found on dashboard"