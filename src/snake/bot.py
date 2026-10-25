import os
import sys
import time
import json
import math
import zipfile
import tempfile
from pathlib import Path
# pyrefly: ignore[missing-import]
from dotenv import load_dotenv
from seleniumbase import SB

load_dotenv()
for env_path in [".env", "src/snake/.env", "../.env"]:
    if os.path.exists(env_path):
        load_dotenv(env_path)

# Ensure UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
COOKIE_FILE = PROJECT_ROOT / "incrypted_cookies.json"
GAME_URL = "https://incrypted.com/cryptosnake/"
BASE_URL = "https://incrypted.com"
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in ("1", "true", "yes")

try:
    from .notifier import send_telegram_message
except ImportError:
    from notifier import send_telegram_message


def format_proxy(proxy_str: str) -> str | None:
    """Format proxy string for SeleniumBase (username:password@host:port or host:port).
    Supports both user:pass@host:port and host:port:user:pass formats.
    Strips protocols, trailing slashes, and path components.
    """
    if not proxy_str:
        return None
    s = proxy_str.strip()
    for prefix in ("http://", "https://", "socks5://", "socks4://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    # Strip trailing slashes or path parameters (e.g. host:port/ or user:pass@host:port/)
    s = s.split("/")[0].strip()
    # Handle host:port:user:pass format from providers
    if "@" not in s and s.count(":") == 3:
        parts = s.split(":")
        clean_port = "".join(c for c in parts[1] if c.isdigit())
        return f"{parts[2]}:{parts[3]}@{parts[0]}:{clean_port}"
    if "@" in s and ":" in s:
        auth, _, host_port = s.rpartition("@")
        if ":" in host_port:
            host, _, port = host_port.partition(":")
            clean_port = "".join(c for c in port if c.isdigit())
            return f"{auth}@{host}:{clean_port}"
    return s if s else None



def build_proxy_extension(proxy_clean: str) -> str | None:
    """Build Chrome MV2 proxy auth extension identical to src/main.py."""
    if not proxy_clean or "@" not in proxy_clean:
        return None
    credentials, _, host_port = proxy_clean.rpartition("@")
    user, _, pwd = credentials.partition(":")
    host, _, port = host_port.partition(":")
    port_clean = "".join(c for c in port if c.isdigit())
    if not (user and pwd and host and port_clean):
        return None
    port_num = int(port_clean)
    ext_dir = tempfile.mkdtemp()
    bg_js = f"""var config = {{
        mode: "fixed_servers",
        rules: {{ singleProxy: {{ scheme: "http", host: "{host}", port: {port_num} }},
                  bypassList: ["localhost"] }}
    }};
    chrome.proxy.settings.set({{ value: config, scope: "regular" }}, function(){{}});
    chrome.webRequest.onAuthRequired.addListener(
        function(details) {{
            return {{ authCredentials: {{ username: {json.dumps(user)}, password: {json.dumps(pwd)} }} }};
        }},
        {{ urls: ["<all_urls>"] }},
        ["blocking"]
    );"""
    manifest = json.dumps({
        "version": "1.0.0", "manifest_version": 2, "name": "Proxy Auth",
        "permissions": ["proxy", "tabs", "unlimitedStorage", "storage",
                        "<all_urls>", "webRequest", "webRequestBlocking"],
        "background": {"scripts": ["background.js"]},
    })
    ext_zip = os.path.join(ext_dir, "proxy_auth.zip")
    with zipfile.ZipFile(ext_zip, "w") as z:
        z.writestr("background.js", bg_js)
        z.writestr("manifest.json", manifest)
    return ext_zip


def is_proxy_working(proxy_raw: str, timeout: int = 7) -> bool:
    """Check if the proxy is alive and can connect to the internet."""
    if not proxy_raw:
        return False
    import requests
    try:
        clean = format_proxy(proxy_raw)
        if not clean:
            return False
        proxy_url = f"http://{clean}"
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get(
            "https://cloudflare.com/cdn-cgi/trace",
            proxies=proxies,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        return resp.status_code in (200, 301, 302, 403)
    except Exception as e:
        print(f"[PROXY] Проксі не пройшов перевірку через requests: {e}")
        return False


_cdp_mode_logged = False


def _is_cdp_mode(sb) -> bool:
    """Helper to detect if SeleniumBase is actively operating in CDP Mode."""
    global _cdp_mode_logged
    branch = None
    try:
        driver = getattr(sb, "driver", None)
        if driver is not None:
            # 1. Driver-level private method check if available
            if hasattr(driver, "_BaseCase__is_cdp_swap_needed") and driver._BaseCase__is_cdp_swap_needed():
                branch = "driver._BaseCase__is_cdp_swap_needed"
            # 2. SeleniumBase shared_utils check
            elif not branch:
                try:
                    from seleniumbase.fixtures import shared_utils
                    if shared_utils.is_cdp_swap_needed(driver):
                        branch = "shared_utils.is_cdp_swap_needed"
                except Exception:
                    pass
            # 3. Direct driver cdp mode active check
            if not branch and hasattr(driver, "is_cdp_mode_active") and driver.is_cdp_mode_active():
                if hasattr(driver, "is_connected") and not driver.is_connected():
                    branch = "driver.is_cdp_mode_active && !is_connected"
            # 4. Fallback via driver type name
            if not branch:
                driver_type = type(driver).__name__.lower()
                if ("cdp" in driver_type or "undetected" in driver_type) and hasattr(driver, "is_connected") and not driver.is_connected():
                    branch = f"driver_type ({type(driver).__name__}) && !is_connected"
        # 5. sb-level check
        if not branch and hasattr(sb, "_BaseCase__is_cdp_swap_needed") and sb._BaseCase__is_cdp_swap_needed():
            branch = "sb._BaseCase__is_cdp_swap_needed"
    except Exception:
        pass

    if branch:
        if not _cdp_mode_logged:
            print(f"[CDP] Режим детекції активний через: {branch}")
            _cdp_mode_logged = True
        return True
    return False


def safe_eval(sb, code: str):
    """
    Safely executes JavaScript via sb.execute_script() across both CDP mode and WebDriver mode.
    - Strips illegal outer 'return' statements when in CDP mode to prevent SyntaxError: Illegal return statement.
    - Ensures code is enclosed in an IIFE so multiline statements with returns evaluate correctly.
    - In WebDriver mode, ensures outer 'return' is present so values are returned to Python.
    - Always delegates to sb.execute_script() to preserve SeleniumBase retries, routing, and logging.
    """
    clean = code.strip()
    is_cdp = _is_cdp_mode(sb)

    clean_r = clean.rstrip().rstrip(";")
    if clean_r.startswith("return (") and (clean_r.endswith(")()") or clean_r.endswith("})()")):
        inner_iife = clean_r[len("return "):].strip()
    elif clean_r.startswith("return (") and clean_r.endswith(")") and not (clean_r.endswith(")()") or clean_r.endswith("})()")):
        expr = clean_r[len("return "):].strip()
        script_to_run = expr if is_cdp else f"return {expr};"
        return sb.execute_script(script_to_run)
    elif clean_r.startswith("(()") or clean_r.startswith("(function"):
        inner_iife = clean_r
    else:
        lines = [l.strip() for l in clean.splitlines() if l.strip()]
        if len(lines) == 1 and lines[0].startswith("return "):
            expr = lines[0][7:].rstrip(";").strip()
            script_to_run = expr if is_cdp else f"return {expr};"
            return sb.execute_script(script_to_run)
        elif "return " in clean:
            inner_iife = f"(() => {{\n{clean}\n}})()"
        else:
            return sb.execute_script(clean)

    script_to_run = inner_iife if is_cdp else f"return {inner_iife};"
    return sb.execute_script(script_to_run)


def get_binary_path():
    if sys.platform == "win32":
        brave = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        if os.path.exists(brave):
            return brave
    chrome = "/usr/bin/google-chrome"
    return chrome if os.path.exists(chrome) else None


def parse_cookies_from_env(raw: str) -> list | None:
    if not raw:
        return None
    raw = raw.strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1].strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, list):
            return parsed
    except Exception as e:
        print(f"[AUTH] Помилка розбору JSON із INCRYPTED_COOKIES: {e}")
    return None


def inject_cookie_safely(driver, c: dict) -> bool:
    """Safely inject a single cookie via CDP Network.setCookie and standard driver."""
    name = c.get("name")
    val = c.get("value")
    if not name or val is None:
        return False

    # Skip Cloudflare clearance and bot tokens: they are bound to the specific browser & OS
    # where they were generated. Injecting Windows cf_clearance into Linux triggers Turnstile!
    if name.lower() in ("cf_clearance", "__cf_bm", "_cfuvid", "cf_chl_prog"):
        return True

    now = time.time()
    clean = {
        "name": str(name),
        "value": str(val),
        "path": c.get("path", "/"),
    }
    raw_domain = c.get("domain", "")
    domain = raw_domain if raw_domain else ".incrypted.com"
    clean["domain"] = domain

    exp = c.get("expiry")
    if exp is not None:
        try:
            exp_val = float(exp)
            if exp_val < now:
                clean["expiry"] = int(now + 86400 * 30)
            else:
                clean["expiry"] = int(exp_val)
        except (ValueError, TypeError):
            pass

    # 1. Native CDP injection (immune to "invalid cookie domain")
    try:
        cdp_cookie = {
            "name": clean["name"],
            "value": clean["value"],
            "domain": clean["domain"],
            "path": clean["path"],
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False))
        }
        if "expiry" in clean:
            cdp_cookie["expires"] = clean["expiry"]
        res = driver.execute_cdp_cmd("Network.setCookie", cdp_cookie)
        if isinstance(res, dict) and res.get("success"):
            return True
    except Exception as e_cdp:
        print(f"[AUTH DEBUG] CDP setCookie failed for {name}: {e_cdp}")

    # 2. Fallback to standard WebDriver add_cookie
    try:
        driver.add_cookie(clean)
        return True
    except Exception:
        pass
    if domain.startswith("."):
        try:
            clean["domain"] = domain.lstrip(".")
            driver.add_cookie(clean)
            return True
        except Exception:
            pass

    try:
        clean_no_domain = {k: v for k, v in clean.items() if k != "domain"}
        driver.add_cookie(clean_no_domain)
        return True
    except Exception as e_add:
        if not getattr(inject_cookie_safely, "_logged", False):
            print(f"[AUTH DEBUG] Не вдалося додати {name}: {e_add} (Поточний URL: {driver.current_url})")
            inject_cookie_safely._logged = True
        return False


CF_CHALLENGE_MARKERS = (
    "just a moment",
    "moment...",
    "moment…",
    "cloudflare",
    "трохи зачекайте",
    "зачекайте",
    "подождите",
    "минуточку",
    "attention required",
    "security check",
    "challenge",
)


def is_cf_challenge(title: str) -> bool:
    if not title:
        return True
    t = title.lower().strip()
    return any(m in t for m in CF_CHALLENGE_MARKERS)


def format_snake_tg_report(
    run_idx: int,
    max_runs: int,
    score_earned: int,
    kills_earned: int,
    duration_sec: int,
    rec_line: str,
    nick: str,
    best_score: int,
    rank_str: str,
    guild_pts: str,
    guild_place: int,
    guild_sub: str,
    boost_triggers_count: int = 0
) -> str:
    place_medal = "🥇" if guild_place == 1 else ("🥈" if guild_place == 2 else "🥉")
    boost_line = f"• Активацій Turbo Boost: <b>{boost_triggers_count}</b>\n" if boost_triggers_count > 0 else ""
    return (
        f"🐍 <b>CryptoSnake — Забіг завершено! (#{run_idx} із {max_runs})</b>\n\n"
        f"🎯 <b>Результат цієї гри:</b>\n"
        f"• Набрано очок: <b>+{score_earned:,} монет</b> (NEAR ×2)\n"
        f"• З'їдено змій: <b>{kills_earned}</b>\n"
        f"• Час на арені: <b>{duration_sec // 60} хв {duration_sec % 60} сек</b>\n"
        f"{boost_line}"
        f"• {rec_line}\n\n"
        f"👤 <b>Твій профіль ({nick}):</b>\n"
        f"• Офіційний рекорд: <b>{best_score:,} монет</b>\n"
        f"• Місце в рейтингу: <b>{rank_str}</b>\n\n"
        f"🟢 <b>Команда NEAR:</b>\n"
        f"• Очки гільдії: <b>{guild_pts} монет</b>\n"
        f"• Місце команди: <b>{guild_place}-е місце {place_medal}</b>\n"
        f"• Склад: <i>{guild_sub}</i>\n\n"
        f"🤖 <i>Режим: Turbo Vacuum (Space) + Smart Predator + Dynamic Walls</i>"
    )


def is_session_timeout(elapsed: float, is_ci: bool) -> bool:
    """Hard watchdog check for CI runs (cutoff at 3000s / 50 min)."""
    if is_ci and elapsed >= 3000:
        return True
    return False


def calculate_avoid_radius(mass: float) -> float:
    """Dynamic avoidance radius scaled to snake turning physics (capped at 400px)."""
    return max(220.0, min(400.0, 180.0 + mass * 0.08))


def get_phase_weights(mass: float) -> dict:
    """3-phase weight distribution for food, clusters, and decay compensation."""
    if mass < 400:
        return {"phase": 1, "food": 1.0, "cluster": 2.0}
    elif mass <= 1500:
        return {"phase": 2, "food": 1.0, "cluster": 2.5}
    else:
        # Phase 3: decay is 11-26 mass/sec -> must eat more aggressively!
        return {"phase": 3, "food": 1.5, "cluster": 3.0}


def wait_for_bypass(sb, target_url="https://incrypted.com/cryptosnake/", timeout=50) -> bool:
    """Wait for page to finish loading and pass Cloudflare Turnstile if present.
    In UC mode: do NOT make intrusive CDP calls while Turnstile is active.
    """
    deadline = timeout
    step = 4
    waited = 0
    reconnects = 0
    while waited < deadline:
        try:
            raw_title = sb.get_title() or ""
            # Успіх тільки коли заголовок НЕ порожній і НЕ Cloudflare challenge!
            if raw_title and not is_cf_challenge(raw_title):
                print(f"[CF] Перевірку Cloudflare пройдено (заголовок: '{raw_title}')!")
                return True
        except Exception as e_title:
            print(f"[CF] Не вдалося прочитати заголовок: {e_title}")

        print(f"[CF] Очікуємо завантаження сторінки / Turnstile ({waited}s / {deadline}s)...")
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            print(f"[CF] Спроба кліку по капчі не вдалася: {e}")

        sb.sleep(step)
        waited += step

        # Every 16s refresh challenge with UC reconnect
        if waited % 16 == 0 and reconnects < 3:
            reconnects += 1
            print(f"[CF] Перепідключення #{reconnects} для оновлення виклику...")
            try:
                sb.uc_open_with_reconnect(target_url, reconnect_time=4)
                sb.sleep(3)
            except Exception as e:
                print(f"[CF] Помилка uc_open_with_reconnect: {e}")

    try:
        raw_title = sb.get_title() or ""
        return bool(raw_title and not is_cf_challenge(raw_title))
    except Exception:
        return False


def ensure_authenticated(sb):
    """Loads existing cookies or logs in automatically via .env credentials."""
    email = os.getenv("INCRYPTED_EMAIL")
    password = os.getenv("INCRYPTED_PASSWORD")
    cookies_raw = os.getenv("INCRYPTED_COOKIES")

    print("[AUTH] Ініціалізація браузера на домені...")
    sb.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
    sb.sleep(4)

    # Якщо заголовок порожній або "Just a moment...", очікуємо завантаження
    if not wait_for_bypass(sb, target_url=BASE_URL, timeout=40):
        print("[AUTH] ⚠️ Не вдалося пройти перевірку на головній сторінці за 40 сек.")

    cur_url = sb.get_current_url()
    cur_title = sb.get_title() or ""
    print(f"[AUTH] Поточний URL: {cur_url} | Заголовок: '{cur_title}'")
    if not cur_title or not cur_url or "incrypted.com" not in cur_url or is_cf_challenge(cur_title):
        print("[AUTH] Сторінка не завантажилася повністю, відкриваємо BASE_URL повторно...")
        try:
            sb.open(BASE_URL)
            sb.sleep(4)
            print(f"[AUTH] Заголовок після повторного відкриття: '{sb.get_title()}'")
        except Exception as e_open:
            print(f"[AUTH] Помилка відкриття BASE_URL: {e_open}")

    # 1. Спробувати завантажити куки з env (INCRYPTED_COOKIES)
    cookies = parse_cookies_from_env(cookies_raw)
    source = "секрету INCRYPTED_COOKIES"

    # 2. Якщо в env немає, спробувати з локального файлу incrypted_cookies.json
    if not cookies and COOKIE_FILE.exists():
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            source = f"файлу {COOKIE_FILE.name}"
        except Exception as e:
            print(f"[AUTH] Не вдалося прочитати {COOKIE_FILE.name}: {e}")

    if cookies:
        print(f"[AUTH] Інжектуємо куки із {source} (без cf_clearance)...")
        added = 0
        for c in cookies:
            if inject_cookie_safely(sb.driver, c):
                added += 1
        print(f"[AUTH] Успішно інжектовано {added} куків.")
        sb.uc_open_with_reconnect(GAME_URL, reconnect_time=4)
        sb.sleep(3)
        wait_for_bypass(sb, target_url=GAME_URL, timeout=30)
        uid = safe_eval(sb, "return (window.CryptoSnakeWP && window.CryptoSnakeWP.userId) || 0;")
        if uid:
            print(f"[AUTH] Сесію підтверджено через куки (userId={uid}).")
            return True
        print("[AUTH] ⚠️ Інжектовані куки не авторизували сесію (userId=0). Переходимо до входу за паролем...")

    # 3. Якщо куків немає або вони недійсні, виконуємо вхід за email + password
    if email and password:
        print("[AUTH] Куки відсутні. Виконуємо вхід за логіном/паролем...")
        try:
            sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", reconnect_time=4)
            sb.sleep(4)
            wait_for_bypass(sb, target_url="https://incrypted.com/ua/account/", timeout=40)
            try:
                sb.wait_for_element_visible("#llms_login", timeout=12)
                sb.type("#llms_login", email)
                sb.type("#llms_password", password)
                sb.sleep(1)
                sb.click("#llms_login_button")
                sb.sleep(6)
                wait_for_bypass(sb, target_url="https://incrypted.com/ua/account/", timeout=10)
            except Exception as ex_form:
                print(f"[AUTH] Форма на /ua/account/ недоступна: {ex_form}")
                return False

            cookies = sb.driver.get_cookies()
            try:
                with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, indent=2)
                print(f"[AUTH] Збережено {len(cookies)} свіжих куків.")
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"[AUTH] Помилка авторизації: {e}")
            return False

    return False


def _run_snake_bot_impl(max_runs=3, headless=False):
    binary = get_binary_path()

    primary_proxy = os.getenv("RESIDENTIAL_PROXY") or os.getenv("PROXY") or ""
    backup_proxy = os.getenv("BACKUP_PROXY") or os.getenv("SECONDARY_PROXY") or ""

    proxy_raw = ""
    if primary_proxy:
        print("[BOT] Перевірка доступності основного проксі (RESIDENTIAL_PROXY)...")
        if is_proxy_working(primary_proxy, timeout=7):
            proxy_raw = primary_proxy
        else:
            print("[BOT] ⚠️ Основний проксі не відповідає!")
            if backup_proxy:
                print("[BOT] Перевірка резервного проксі (BACKUP_PROXY)...")
                if is_proxy_working(backup_proxy, timeout=7):
                    print("[BOT] ✅ Резервний проксі активний! Перемикаємось на BACKUP_PROXY.")
                    proxy_raw = backup_proxy
                else:
                    print("[BOT] ⚠️ Резервний проксі також не відповідає.")
    elif backup_proxy:
        print("[BOT] Перевірка резервного проксі (BACKUP_PROXY)...")
        if is_proxy_working(backup_proxy, timeout=7):
            proxy_raw = backup_proxy

    proxy_clean = format_proxy(proxy_raw)

    sb_kwargs = dict(
        uc=True,
        headless=headless,
        binary_location=binary
    )

    proxy_ext_dir = None
    if proxy_clean:
        ext_zip = build_proxy_extension(proxy_clean)
        if ext_zip:
            proxy_ext_dir = os.path.dirname(ext_zip)
            sb_kwargs["extension_zip"] = ext_zip
        else:
            sb_kwargs["proxy"] = proxy_clean
        host_display = proxy_clean.split("@")[-1]
        print(f"[BOT] Проксі активовано: {host_display}")
    else:
        if primary_proxy or backup_proxy:
            print("[BOT] ⚠️ Усі вказані проксі недоступні! Спроба прямого підключення.")
        else:
            print("[BOT] Проксі не налаштовано — працюємо напряму.")

    print("=" * 60)
    print(f"[BOT] Запуск інтелектуального бота CryptoSnake")
    print(f"      Цільова команда : NEAR (бонус ×2)")
    print(f"      Режим зору      : Векторний радар рушія + нативне CDP-керування")
    print(f"      Кількість ігор  : {max_runs}")
    print(f"      Браузер         : {binary or 'Системний'}")
    print("=" * 60)

    try:
        with SB(**sb_kwargs) as sb:
            ensure_authenticated(sb)

            if "cryptosnake" not in (sb.get_current_url() or ""):
                print(f"\n[BOT] Відкриваємо гру {GAME_URL}...")
                sb.uc_open_with_reconnect(GAME_URL, reconnect_time=4)
                sb.sleep(4)
                if not wait_for_bypass(sb, target_url=GAME_URL, timeout=45):
                    print("[BOT] ⚠️ Попередження: сторінка гри все ще може перебувати під перевіркою Cloudflare.")
            else:
                print(f"\n[BOT] Сторінка гри {GAME_URL} вже відкрита та верифікована.")

            for _ in range(8):
                has_canvas = safe_eval(sb, "return !!document.querySelector('#cs-canvas');")
                if has_canvas:
                    break
                sb.sleep(2)

            # Перевірка стану акаунта в грі
            account_status = safe_eval(sb, """
                var wp = window.CryptoSnakeWP || {};
                return {
                    userId: wp.userId || 0,
                    nick: wp.account?.nick || null,
                    guild: wp.account?.guild || null
                };
            """)
            print(f"[BOT] Стан облікового запису на арені: {account_status}")

            # Якщо сесія не авторизована, спробувати увійти через попап/форму
            if not account_status.get("userId"):
                email = os.getenv("INCRYPTED_EMAIL")
                password = os.getenv("INCRYPTED_PASSWORD")
                if email and password:
                    print("[BOT] Акаунт не авторизовано (userId=0). Спробуємо увійти через попап...")
                    try:
                        safe_eval(sb, "document.querySelector('#cs-start')?.click();")
                        sb.sleep(2)
                        if sb.is_element_visible(".incr-popup-login"):
                            sb.type(".incr-popup-login input[type=email], #llms_login", email)
                            sb.type(".incr-popup-login input[type=password], #llms_password", password)
                            sb.sleep(1)
                            sb.click(".incr-popup-login #llms_login_button, #llms_login_button")
                            sb.sleep(5)
                            account_status = safe_eval(sb, """
                                var wp = window.CryptoSnakeWP || {};
                                return {
                                    userId: wp.userId || 0,
                                    nick: wp.account?.nick || null,
                                    guild: wp.account?.guild || null
                                };
                            """)
                            print(f"[BOT] Стан після спроби входу: {account_status}")
                    except Exception as e_login:
                        print(f"[BOT] Помилка автоматичного входу: {e_login}")

            def ensure_radar_injected():
                return safe_eval(sb, """
                    return (() => {
                        if (!window.CryptoSnakeCore || !window.CryptoSnakeCore.World) return 'NO_CORE';
                        if (window.__radarHooked) return 'ALREADY_OK';
                        const origStep = window.CryptoSnakeCore.World.prototype.step;
                        window.CryptoSnakeCore.World.prototype.step = function(...args) {
                            window.__activeWorld = this;
                            return origStep.apply(this, args);
                        };
                        const origAddSnake = window.CryptoSnakeCore.World.prototype.addSnake;
                        window.CryptoSnakeCore.World.prototype.addSnake = function(opts) {
                            const s = origAddSnake.apply(this, arguments);
                            if (opts && !opts.bot) {
                                window.__mySnakeId = s.id;
                            }
                            return s;
                        };
                        window.__radarHooked = true;
                        return 'OK';
                    })()
                """)

            print(f"[BOT] Ін'єкція радарного перехоплювача: {ensure_radar_injected()}")
            sb.sleep(1)

            session_start_time = time.time()
            is_ci = bool(os.getenv("GITHUB_ACTIONS"))

            for run_idx in range(1, max_runs + 1):
                print(f"\n{'='*25} РАУНД #{run_idx} із {max_runs} {'='*25}")

                if is_session_timeout(time.time() - session_start_time, is_ci):
                    print("[BOT] ⏰ 50 хв сесії вичерпано (watchdog), планове завершення сесії перед лімітом CI.")
                    break

                # Перевірка чи вікно все ще відкрите
                try:
                    safe_eval(sb, "return !!document.body;")
                except Exception as e_win:
                    print(f"[BOT] Вікно браузера закрите або не відповідає ({e_win}). Зупинка сесії.")
                    break

                # 1. Якщо раунд > 1: повертаємося в головне меню через кнопку #cs-change
                if run_idx > 1:
                    try:
                        safe_eval(sb, """
                            var changeBtn = document.querySelector('#cs-change');
                            if (changeBtn && !changeBtn.hidden) {
                                changeBtn.click();
                            }
                        """)
                        sb.sleep(3)
                    except Exception:
                        pass

                # 2. Обираємо команду NEAR (.cs-char.cs-hero-near)
                print("[BOT] Вибираємо команду NEAR...")
                for _ in range(6):
                    selected = safe_eval(sb, """
                        var el = document.querySelector('.cs-char.cs-hero-near');
                        if (el) { el.click(); return true; }
                        return false;
                    """)
                    if selected:
                        print("[BOT] Команду NEAR успішно обрано.")
                        break
                    sb.sleep(1)

                # 3. Чекаємо готовності кнопки старту
                for _ in range(6):
                    btn_ready = safe_eval(sb, """
                        var btn = document.querySelector('#cs-start');
                        return btn && !btn.disabled;
                    """)
                    if btn_ready:
                        break
                    sb.sleep(1)

                # 4. Тиснемо кнопку старту
                btn_info = safe_eval(sb, """
                    var btn = document.querySelector('#cs-start');
                    return { text: btn?.innerText, disabled: btn?.disabled };
                """) or {}
                print(f"[BOT] Кнопка старту: '{btn_info.get('text')}' (disabled={btn_info.get('disabled')}). Тиснемо...")
                safe_eval(sb, "document.querySelector('#cs-start')?.click();")
                sb.sleep(2)

                # 5. Якщо з'явився попап входу, вводимо логін/пароль на місці
                email = os.getenv("INCRYPTED_EMAIL")
                password = os.getenv("INCRYPTED_PASSWORD")
                if sb.is_element_visible(".incr-popup-login") and email and password:
                    print("[BOT] З'явилося вікно входу на полі гри. Авторизуємося...")
                    try:
                        sb.type(".incr-popup-login input[type=email], #llms_login", email)
                        sb.type(".incr-popup-login input[type=password], #llms_password", password)
                        sb.sleep(1)
                        sb.click(".incr-popup-login #llms_login_button, #llms_login_button")
                        sb.sleep(5)
                        safe_eval(sb, """
                            document.querySelector('.cs-char.cs-hero-near')?.click();
                            document.querySelector('#cs-start')?.click();
                        """)
                        sb.sleep(2)
                    except Exception as e_pop:
                        print(f"[BOT] Помилка входу через попап: {e_pop}")

                ensure_radar_injected()
                # 6. Очікуємо, поки гра ДІЙСНО перейде в стан бою (HUD активний)
                print("[BOT] Очікуємо активацію арени (поява HUD)...")
                game_active = False
                for check_i in range(25):
                    hud_active = safe_eval(sb, """
                        var hud = document.querySelector('#cs-hud');
                        return hud && !hud.hidden;
                    """)
                    if hud_active:
                        game_active = True
                        break

                    # Перевіряємо статус попередження (кулдаун попереднього забігу)
                    status_text = safe_eval(sb, """
                        return (document.querySelector('#cs-season-status')?.innerText ||
                                document.querySelector('#cs-run-warning')?.innerText || '');
                    """)
                    if "активний забіг" in status_text.lower():
                        print(f"[BOT] ⏳ Сервер утримує сесію попереднього забігу. Очікуємо завершення кулдауну ({check_i+1}/25)...")
                        sb.sleep(4)
                        safe_eval(sb, """
                            var btn = document.querySelector('#cs-start');
                            if (btn && !btn.disabled) { btn.click(); }
                        """)
                        continue

                    # Якщо з'явилося попередження або кнопка старту все ще активна, пробуємо натиснути ще раз
                    if check_i in (3, 7):
                        safe_eval(sb, """
                            var btn = document.querySelector('#cs-start');
                            if (btn && !btn.disabled) { btn.click(); }
                        """)
                    sb.sleep(1)

                if not game_active:
                    diag = safe_eval(sb, """
                        return {
                            title: document.title,
                            userId: window.CryptoSnakeWP?.userId || 0,
                            btnText: document.querySelector('#cs-start')?.innerText,
                            btnDisabled: document.querySelector('#cs-start')?.disabled,
                            hasPopup: !!document.querySelector('.incr-popup-login'),
                            warn: document.querySelector('#cs-run-warning')?.innerText || document.querySelector('#cs-season-status')?.innerText
                        };
                    """) or {}
                    print(f"[BOT] ПОМИЛКА: Гра не перейшла в режим бою. Діагностика: {diag}")
                    try:
                        sb.save_screenshot(f"game_not_started_round_{run_idx}.png")
                    except Exception:
                        pass

                    # Надсилаємо діагностику в Telegram, щоб користувач точно знав причину
                    err_msg = (
                        f"⚠️ <b>CryptoSnake — Помилка старту раунду #{run_idx} із {max_runs}</b>\n\n"
                        f"• User ID: <code>{diag.get('userId')}</code>\n"
                        f"• Кнопка: <code>{diag.get('btnText')} (disabled={diag.get('btnDisabled')})</code>\n"
                        f"• Вікно входу: <code>{'Відкрито' if diag.get('hasPopup') else 'Закрито'}</code>\n"
                        f"• Заголовок: <code>{diag.get('title')}</code>\n"
                        f"• Попередження сайту: <code>{diag.get('warn') or 'Немає'}</code>\n\n"
                        f"<i>Скріншот збережено в artifacts GitHub Actions.</i>"
                    )
                    send_telegram_message(err_msg)
                    continue

                # 7. Підготовка canvas та розмірів
                safe_eval(sb, """
                    var c = document.querySelector('#cs-canvas');
                    if (c) {
                        if (!c.hasAttribute('tabindex')) {
                            c.setAttribute('tabindex', '-1');
                        }
                        c.focus();
                    }
                """)
                canvas_rect = safe_eval(sb, """
                    return (() => {
                        var c = document.querySelector('#cs-canvas');
                        if (!c) return null;
                        var r = c.getBoundingClientRect();
                        return { x: r.left, y: r.top, w: r.width, h: r.height };
                    })()
                """)

                cx = int(canvas_rect['x'] + canvas_rect['w'] / 2) if canvas_rect else 600
                cy = int(canvas_rect['y'] + canvas_rect['h'] / 2) if canvas_rect else 350

                start_time = time.time()
                score_earned = 0
                kills_earned = 0
                ticks = 0

                if DRY_RUN:
                    max_duration = 180  # 3 хвилини для тестової перевірки DRY_RUN
                    print("[BOT] 🧪 УВІМКНЕНО DRY_RUN (3 хв тест). CDP Space імітується, вимірюємо кластери та запобіжники.")
                else:
                    default_duration = 2100 if os.getenv("GITHUB_ACTIONS") else 2400
                    max_duration = int(os.getenv("MAX_DURATION", default_duration))
                    print(f"[BOT] Змійка на полі! Марафон на {max_duration // 60} хв: Turbo Vacuum (Space) + Smart Predator + Dynamic Walls...")

                boost_active = False
                boost_start_time = 0.0
                boost_start_mass = 0.0
                boost_cooldown_until = 0.0
                boost_triggers_count = 0
                max_cluster_seen = 0.0

                # Забіг триває доти, доки змійка жива (або вичерпано час сесії)
                while (time.time() - start_time) < max_duration:
                    if is_session_timeout(time.time() - session_start_time, is_ci):
                        print("[BOT] ⏰ 50 хв сесії вичерпано (watchdog), планове завершення поточного забігу.")
                        break

                    try:
                        decision = safe_eval(sb, """
                            return (() => {
                                var canvas = document.querySelector('#cs-canvas');
                                if (!canvas) return { is_dead: true };

                                var deadEl = document.querySelector('#cs-dead');
                                var hudEl  = document.querySelector('#cs-hud');
                                var isDead = (deadEl && !deadEl.hidden) || (hudEl && hudEl.hidden);

                                var coinsEl = document.querySelector('#cs-hud-coins');
                                var lenEl   = document.querySelector('#cs-hud-len');
                                var killsEl = document.querySelector('#cs-hud-kills');

                                var currentCoins = coinsEl ? coinsEl.innerText : '0';
                                var currentLen   = lenEl ? lenEl.innerText : '0';
                                var currentKills = killsEl ? killsEl.innerText : '0';

                                var w = window.__activeWorld;
                                if (!w || !w.snakes) {
                                    return { is_dead: isDead, coins: currentCoins, len: currentLen, kills: currentKills, angle: null, should_boost: false };
                                }

                                // Знаходимо саме змійку гравця
                                var me = null;
                                if (window.__mySnakeId !== undefined && w.snakes.has(window.__mySnakeId)) {
                                    me = w.snakes.get(window.__mySnakeId);
                                }
                                if (!me) {
                                    for (var s of w.snakes.values()) {
                                        if (!s.bot) {
                                            me = s;
                                            window.__mySnakeId = s.id;
                                            break;
                                        }
                                    }
                                }

                                if (!me || !me.alive || !me.pts || me.pts.length < 2) {
                                    return { is_dead: true, coins: currentCoins, len: currentLen, kills: currentKills, angle: null, should_boost: false };
                                }

                                var meMass = me.mass || 0;
                                currentCoins = String(Math.floor(me.coins || 0));
                                currentLen   = String(Math.round(meMass));
                                currentKills = String(me.kills || 0);

                                var hx = me.pts[0];
                                var hy = me.pts[1];

                                // 1. Пошук монет та виявлення дропу після загибелі змій (!f.natural)
                                var foodFx = 0;
                                var foodFy = 0;
                                var drops = [];

                                if (w.food && w.food.length > 0) {
                                    for (var f of w.food) {
                                        if (f.dead) continue;
                                        var dx = f.x - hx;
                                        var dy = f.y - hy;
                                        var dist = Math.hypot(dx, dy);
                                        if (dist < 1) continue;

                                        // Базовий радар їжі (до 650px) з пріоритетом гільдійських монет NEAR (×3.0)
                                        if (dist <= 650) {
                                            var weight = f.type === 'near' ? 3.0 : 1.0;
                                            var force = weight / (dist * dist);
                                            foodFx += (dx / dist) * force;
                                            foodFy += (dy / dist) * force;
                                        }

                                        // Дроп після загибелі змій (f.natural === false) для кластеризації (радіус 320px)
                                        // Використовуємо поле f.v (маса монети в рушії cryptosnake)
                                        if (f.natural === false && dist <= 320) {
                                            drops.push({ x: f.x, y: f.y, v: (f.v || 1), dist: dist });
                                        }
                                    }
                                }

                                // 2. Кластеризація дропів за відстанню (радіус суміжності 50px)
                                var clusterFx = 0;
                                var clusterFy = 0;
                                var bestCluster = null;

                                if (drops.length >= 3) {
                                    var visited = new Uint8Array(drops.length);
                                    for (var i = 0; i < drops.length; i++) {
                                        if (visited[i]) continue;
                                        var cMass = drops[i].v;
                                        var cCount = 1;
                                        var sumX = drops[i].x * drops[i].v;
                                        var sumY = drops[i].y * drops[i].v;
                                        visited[i] = 1;

                                        for (var j = i + 1; j < drops.length; j++) {
                                            if (visited[j]) continue;
                                            var ddx = drops[i].x - drops[j].x;
                                            var ddy = drops[i].y - drops[j].y;
                                            if (ddx * ddx + ddy * ddy < 2500) { // відстань < 50px
                                                visited[j] = 1;
                                                cMass += drops[j].v;
                                                cCount += 1;
                                                sumX += drops[j].x * drops[j].v;
                                                sumY += drops[j].y * drops[j].v;
                                            }
                                        }

                                        // Поріг окупності: cluster.mass >= 21 (в 3 рази більше за boostCost: 7)
                                        if (cMass >= 21) {
                                            var cx_drop = sumX / cMass;
                                            var cy_drop = sumY / cMass;
                                            var cDist = Math.hypot(cx_drop - hx, cy_drop - hy);
                                            if (!bestCluster || cMass > bestCluster.mass) {
                                                bestCluster = {
                                                    mass: cMass,
                                                    count: cCount,
                                                    x: cx_drop,
                                                    y: cy_drop,
                                                    dist: cDist
                                                };
                                            }
                                        }
                                    }
                                }

                                if (bestCluster && bestCluster.dist > 1) {
                                    var cDist = bestCluster.dist;
                                    var cForce = (bestCluster.mass / (cDist * cDist)) * 1.0;
                                    clusterFx = ((bestCluster.x - hx) / cDist) * cForce;
                                    clusterFy = ((bestCluster.y - hy) / cDist) * cForce;
                                }

                                // 3. Ухилення від ворожих змій з динамічною зоною безпеки за масою (220-400px)
                                var avoidRadius = Math.max(220, Math.min(400, 180 + meMass * 0.08));
                                var avoidFx = 0;
                                var avoidFy = 0;
                                var minEnemyDist = 9999;
                                for (var other of w.snakes.values()) {
                                    if (other.id === me.id) continue;
                                    if (other.guild === 'near') continue; // союзники проходять крізь одне одного
                                    if (!other.pts || other.pts.length < 2) continue;

                                    for (var p = 0; p < other.pts.length; p += 4) {
                                        var ox = other.pts[p];
                                        var oy = other.pts[p + 1];
                                        var odist = Math.hypot(hx - ox, hy - oy);
                                        if (odist < minEnemyDist) minEnemyDist = odist;

                                        // Динамічна зона безпеки avoidRadius та підвищена небезпека від голови ворога
                                        if (odist < avoidRadius && odist > 1) {
                                            var headMultiplier = (p === 0) ? 2.5 : 1.0;
                                            var repForce = (90.0 * headMultiplier) / (odist * odist);
                                            avoidFx += ((hx - ox) / odist) * repForce;
                                            avoidFy += ((hy - oy) / odist) * repForce;
                                        }
                                    }
                                }

                                // 4. Відштовхування від меж арени (динамічний safeRadius від 380px до 600px)
                                var wallFx = 0;
                                var wallFy = 0;
                                var distFromCenter = Math.hypot(hx, hy);
                                var arenaRadius = w.radius || 2000;
                                var wallBuffer = Math.max(380, Math.min(600, meMass * 0.08));
                                var safeRadius = arenaRadius - wallBuffer;

                                if (distFromCenter > safeRadius) {
                                    var wallForce = ((distFromCenter - safeRadius) / wallBuffer) * 20.0;
                                    wallFx = -(hx / distFromCenter) * wallForce;
                                    wallFy = -(hy / distFromCenter) * wallForce;
                                }

                                // 5. 3-фазний розподіл ваг та захист від розпаду (Decay Compensation)
                                var phase = 1;
                                var foodWeight = 1.0;
                                var clusterWeight = 2.0;
                                if (meMass > 1500) {
                                    phase = 3;
                                    foodWeight = 1.5;   // decay 11-26 mass/s -> компенсація активним живленням
                                    clusterWeight = 3.0;
                                } else if (meMass >= 400) {
                                    phase = 2;
                                    foodWeight = 1.0;
                                    clusterWeight = 2.5;
                                }

                                if (minEnemyDist < 180) {
                                    foodWeight *= 0.5;
                                }

                                // М'яке відхилення від хаотичного ядра (< 450px) для великої змійки, якщо в ядрі немає великого кластера
                                var coreFx = 0;
                                var coreFy = 0;
                                if (meMass > 1500 && distFromCenter < 450 && (!bestCluster || bestCluster.dist > 300)) {
                                    var coreForce = ((450 - distFromCenter) / 450) * 4.0;
                                    coreFx = (hx / (distFromCenter || 1)) * coreForce;
                                    coreFy = (hy / (distFromCenter || 1)) * coreForce;
                                }

                                var finalX = foodFx * foodWeight + clusterFx * clusterWeight + avoidFx * 5.0 + wallFx * 7.5 + coreFx;
                                var finalY = foodFy * foodWeight + clusterFy * clusterWeight + avoidFy * 5.0 + wallFy * 7.5 + coreFy;
                                var targetAngle = Math.atan2(finalY, finalX);

                                // 6. Умови для безпечного та прибуткового Boost (Turbo Vacuum):
                                // - Кластер з deathDrop >= 21 маси
                                // - Жодного ворога ближче 250px (строгий запобіжник!)
                                // - Достатня маса для ривка (minMassForBoost = max(100, mass * 0.5))
                                // - Відстань до кластера > 40px (ще не досягли)
                                // - Безпечна відстань від краю арени
                                var minMassForBoost = Math.max(100, meMass * 0.5);
                                var shouldBoost = Boolean(
                                    bestCluster &&
                                    bestCluster.mass >= 21 &&
                                    minEnemyDist > 250 &&
                                    meMass >= minMassForBoost &&
                                    bestCluster.dist > 40 &&
                                    distFromCenter < (safeRadius - 100)
                                );

                                return {
                                    is_dead: isDead,
                                    coins: currentCoins,
                                    len: currentLen,
                                    kills: currentKills,
                                    angle: targetAngle,
                                    mass: meMass,
                                    phase: phase,
                                    avoid_radius: avoidRadius,
                                    food_weight: foodWeight,
                                    cluster_weight: clusterWeight,
                                    enemy_dist: minEnemyDist,
                                    dist_center: distFromCenter,
                                    safe_radius: safeRadius,
                                    cluster_mass: bestCluster ? bestCluster.mass : 0,
                                    cluster_dist: bestCluster ? bestCluster.dist : 0,
                                    should_boost: shouldBoost
                                };
                            })()
                        """)

                        if decision:
                            val = decision.get("coins") or "0"
                            clean_score = "".join([c for c in str(val) if c.isdigit()])
                            if clean_score:
                                score_earned = max(score_earned, int(clean_score))

                            k_val = decision.get("kills") or "0"
                            clean_kills = "".join([c for c in str(k_val) if c.isdigit()])
                            if clean_kills:
                                kills_earned = max(kills_earned, int(clean_kills))

                            if decision.get("is_dead"):
                                print(f"[BOT] Забіг завершено (зіткнення/кінець гри).")
                                break

                            target_angle = decision.get("angle")
                            if target_angle is not None:
                                radius = 160
                                mx = int(cx + radius * math.cos(target_angle))
                                my = int(cy + radius * math.sin(target_angle))
                                sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                                    "type": "mouseMoved",
                                    "x": mx,
                                    "y": my
                                })

                            # Опрацювання Boost (Turbo Vacuum)
                            me_mass = float(decision.get("mass") or 0)
                            min_enemy_dist = float(decision.get("enemy_dist") or 9999)
                            dist_center = float(decision.get("dist_center") or 0)
                            safe_radius = float(decision.get("safe_radius") or 1750)
                            cluster_mass = float(decision.get("cluster_mass") or 0)
                            cluster_dist = float(decision.get("cluster_dist") or 0)
                            should_boost = bool(decision.get("should_boost"))
                            now = time.time()

                            if cluster_mass > max_cluster_seen:
                                max_cluster_seen = cluster_mass

                            # 1. Запобіжники вимкнення активного Boost
                            if boost_active:
                                time_boosting = now - boost_start_time
                                stop_reason = None
                                if time_boosting >= 1.0:
                                    stop_reason = "таймаут імпульсу 1.0с"
                                elif min_enemy_dist < 200:
                                    stop_reason = f"наближення ворога ({min_enemy_dist:.0f}px < 200px)"
                                elif dist_center > safe_radius:
                                    stop_reason = "наближення до стіни арени"
                                elif me_mass < 100 or (boost_start_mass > 0 and me_mass < boost_start_mass * 0.5):
                                    stop_reason = f"захист маси ({me_mass:.0f} < {max(100, boost_start_mass * 0.5):.0f})"
                                elif not should_boost:
                                    stop_reason = "кластер зібрано / ціль зникла"

                                if stop_reason:
                                    if not DRY_RUN:
                                        try:
                                            sb.driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                                                "type": "keyUp",
                                                "windowsVirtualKeyCode": 32,
                                                "nativeVirtualKeyCode": 32,
                                                "code": "Space",
                                                "key": " ",
                                                "text": " ",
                                                "unmodifiedText": " "
                                            })
                                        except Exception as e_cdp:
                                            print(f"[BOT] Помилка keyUp Space: {e_cdp}")
                                    boost_active = False
                                    boost_cooldown_until = now + 2.5
                                    if DRY_RUN:
                                        print(f"[DRY_RUN BOOST] ⏹️ Зупинка: {stop_reason} (імпульс {time_boosting:.2f}с, маса: {me_mass:.0f})")

                            # 2. Активація Boost при сприятливих умовах
                            elif should_boost and (now >= boost_cooldown_until):
                                if DRY_RUN:
                                    print(f"[DRY_RUN BOOST] 🚀 АКТИВАЦІЯ (імітація)! Кластер: {cluster_mass:.1f} маси на {cluster_dist:.0f}px, Ворог: {min_enemy_dist:.0f}px, Маса: {me_mass:.0f}")
                                    boost_active = True
                                    boost_start_time = now
                                    boost_start_mass = me_mass
                                    boost_triggers_count += 1
                                else:
                                    try:
                                        safe_eval(sb, "var c = document.querySelector('#cs-canvas'); if (c) { if (!c.hasAttribute('tabindex')) c.setAttribute('tabindex', '-1'); c.focus(); }")
                                        sb.driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                                            "type": "keyDown",
                                            "windowsVirtualKeyCode": 32,
                                            "nativeVirtualKeyCode": 32,
                                            "code": "Space",
                                            "key": " ",
                                            "text": " ",
                                            "unmodifiedText": " "
                                        })
                                        print(f"[BOT] 🚀 BOOST! Кластер {cluster_mass:.0f} маси (ворог {min_enemy_dist:.0f}px, маса {me_mass:.0f})")
                                    except Exception as e_cdp:
                                        print(f"[BOT] Помилка keyDown Space: {e_cdp}")
                                    else:
                                        boost_active = True
                                        boost_start_time = now
                                        boost_start_mass = me_mass
                                        boost_triggers_count += 1

                    except Exception as ex:
                        print(f"[BOT] Помилка циклу: {ex}")
                        break

                    ticks += 1
                    phase_val = decision.get("phase", 1) if decision else 1
                    avoid_r_val = decision.get("avoid_radius", 220.0) if decision else 220.0
                    food_w_val = decision.get("food_weight", 1.0) if decision else 1.0
                    cluster_w_val = decision.get("cluster_weight", 2.0) if decision else 2.0

                    if DRY_RUN and (ticks % 10 == 0):
                        print(f"[DRY] mass={me_mass:.0f} phase={phase_val} avoidR={avoid_r_val:.0f} foodW={food_w_val:.1f} clusterW={cluster_w_val:.1f}")

                    if ticks % 25 == 0:
                        alive_time = int(time.time() - start_time)
                        boost_tag = " [BOOST]" if boost_active else ""
                        print(f"[BOT] Арена: {alive_time}с/{max_duration}с | Монети: {score_earned} | З'їдено: {kills_earned} | Маса: {me_mass:.0f}{boost_tag} | Фаза {phase_val} (avoidR {avoid_r_val:.0f}) | Boosts: {boost_triggers_count}")

                    time.sleep(0.12)

                # Забезпечуємо відпускання клавіші Space після завершення забігу
                if boost_active:
                    if not DRY_RUN:
                        try:
                            sb.driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                                "type": "keyUp",
                                "windowsVirtualKeyCode": 32,
                                "nativeVirtualKeyCode": 32,
                                "code": "Space",
                                "key": " ",
                                "text": " ",
                                "unmodifiedText": " "
                            })
                        except Exception:
                            pass
                    boost_active = False

                duration_sec = int(time.time() - start_time)
                print(f"[BOT] Забіг #{run_idx} тривав {duration_sec} сек.")
                print(f"      Очки: +{score_earned} | З'їдено змій: {kills_earned} | Активацій boost: {boost_triggers_count} | Макс. кластер: {max_cluster_seen:.0f}")

                # 8. Пауза 6 секунд, щоб екран смерті #cs-dead та сервер повністю синхронізувалися
                print("[BOT] Очікуємо синхронізацію результату із сервером (6 сек)...")
                sb.sleep(6)

                # 9. Зчитуємо реальні дані з екрана смерті, localStorage та рейтингу
                stats = safe_eval(sb, """
                    return (() => {
                        var deadCoins = document.querySelector('#cs-d-coins')?.innerText;
                        var deadKills = document.querySelector('#cs-d-kills')?.innerText;
                        var deadTime  = document.querySelector('#cs-d-time')?.innerText;
                        var deadRank  = document.querySelector('#cs-d-rank')?.innerText;

                        // Рекорд сезону (#1 podium)
                        var topPtsEl = document.querySelector('#cs-podium .cs-pod[data-p="1"] .cs-pod-pts');
                        var seasonRecord = topPtsEl ? (parseInt(topPtsEl.innerText.replace(/\\D/g, '')) || 48670) : 48670;

                        // Список гільдій
                        var guildEls = Array.from(document.querySelectorAll('.cs-g'));
                        var guilds = guildEls.map((el, idx) => ({
                            place: idx + 1,
                            name: el.querySelector('.cs-g-name')?.innerText || el.innerText.split('\\n')[0],
                            pts: el.querySelector('.cs-g-pts')?.innerText,
                            sub: el.querySelector('.cs-g-sub')?.innerText,
                            is_mine: el.classList.contains('mine'),
                            is_lead: el.classList.contains('lead')
                        }));

                        var myGuild = guilds.find(g => g.is_mine) ||
                                      guilds.find(g => (g.name || '').includes('NEAR')) || {
                                          place: 3,
                                          name: 'Гільдія NEAR',
                                          pts: '—',
                                          sub: ''
                                      };

                        // Дані користувача з WordPress та localStorage
                        var wp = window.CryptoSnakeWP || {};
                        var acc = wp.account || {};
                        var storageKey = 'cryptosnake-io-v1:wp:' + wp.userId;
                        var localData = null;
                        try {
                            localData = JSON.parse(localStorage.getItem(storageKey));
                        } catch(e) {}

                        var runs = localData && localData.runs ? localData.runs : [];
                        var allCoins = runs.map(r => r.coins || 0);
                        var bestScore = allCoins.length ? Math.max(...allCoins) : 0;
                        var totalCoins = allCoins.reduce((a, b) => a + b, 0);

                        // Соло-рейтинг користувача
                        var myRow = document.querySelector('#cs-solo .cs-prow.me');
                        var soloRank = myRow ? myRow.querySelector('.rk')?.innerText : null;

                        return {
                            nick: acc.nick || 'newagno',
                            season_record: seasonRecord,
                            dead: {
                                coins: deadCoins,
                                kills: deadKills,
                                time: deadTime,
                                rank: deadRank
                            },
                            my_guild: myGuild,
                            history: {
                                best_score: bestScore,
                                total_coins: totalCoins,
                                total_runs: runs.length,
                                solo_rank: soloRank
                            }
                        };
                    })()
                """) or {}

                # Якщо екран смерті містить верифіковану кількість монет, беремо її
                dead_data = stats.get("dead") or {}
                final_coins = dead_data.get("coins")
                if final_coins and any(c.isdigit() for c in str(final_coins)):
                    score_earned = int("".join(c for c in str(final_coins) if c.isdigit()))

                final_kills = dead_data.get("kills")
                if final_kills and any(c.isdigit() for c in str(final_kills)):
                    kills_earned = int("".join(c for c in str(final_kills) if c.isdigit()))

                nick = stats.get("nick", "newagno")
                season_record = stats.get("season_record", 48670)
                delta_record = season_record - score_earned
                if delta_record <= 0:
                    rec_line = f"🏆 <b>НОВИЙ РЕКОРД СЕЗОНУ!</b> Перевершено топ-1 на +{abs(delta_record):,} монет!"
                else:
                    rec_line = f"🎯 До рекорду топ-1 сезону ({season_record:,}): <b>ще {delta_record:,} монет</b>"

                my_guild = stats.get("my_guild") or {}
                guild_pts = my_guild.get("pts") or "—"
                guild_place = my_guild.get("place", 3)
                guild_sub = my_guild.get("sub", "")

                history = stats.get("history") or {}
                best_score = max(score_earned, history.get("best_score", score_earned))
                total_coins = history.get("total_coins", score_earned)
                solo_rank = history.get("solo_rank")
                rank_str = f"#{solo_rank}" if solo_rank else "поза топ-20"

                # 10. Формуємо детальний звіт у Telegram
                tg_message = format_snake_tg_report(
                    run_idx=run_idx,
                    max_runs=max_runs,
                    score_earned=score_earned,
                    kills_earned=kills_earned,
                    duration_sec=duration_sec,
                    boost_triggers_count=boost_triggers_count,
                    rec_line=rec_line,
                    nick=nick,
                    best_score=best_score,
                    rank_str=rank_str,
                    guild_pts=guild_pts,
                    guild_place=guild_place,
                    guild_sub=guild_sub
                )

                print("[BOT] Надсилаємо оновлений звіт у Telegram...")
                send_telegram_message(tg_message)

                # 11. Пауза між раундами
                if run_idx < max_runs:
                    print("[BOT] Пауза 5 секунд перед наступним забігом...")
                    sb.sleep(5)

            print("\n[BOT] Усі заплановані забіги сесії успішно виконано!")
    finally:
        if proxy_ext_dir and os.path.exists(proxy_ext_dir):
            import shutil
            shutil.rmtree(proxy_ext_dir, ignore_errors=True)


def run_snake_bot(max_runs=3, headless=False):
    """Wrapper that catches any unexpected startup/runtime crashes and sends Telegram alert."""
    try:
        _run_snake_bot_impl(max_runs=max_runs, headless=headless)
    except Exception as e:
        print(f"\n[BOT] 🚨 КРИТИЧНА ПОМИЛКА СЕСІЇ: {e}")
        import traceback
        traceback.print_exc()
        try:
            err_line = str(e).strip()
            if "\n" in err_line:
                err_line = err_line.split("\n")[0]
            send_telegram_message(
                f"🚨 <b>CryptoSnake — Помилка запуску бота!</b>\n\n"
                f"• Помилка: <code>{err_line[:300]}</code>\n"
                f"• Статус: сесія була перервана до завершення забігів."
            )
        except Exception as e_tg:
            print(f"[NOTIFIER] Помилка надсилання сповіщення про збій: {e_tg}")
        raise


if __name__ == "__main__":

    runs = 1
    if len(sys.argv) > 1:
        try:
            runs = int(sys.argv[1])
        except ValueError:
            pass
    run_snake_bot(max_runs=runs)
