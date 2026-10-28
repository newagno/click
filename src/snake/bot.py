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
    # Handle host:port:user:pass format from providers (where pass may contain colons)
    if "@" not in s and s.count(":") >= 3:
        parts = s.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit() and ":" in parts[2]:
            host, port, auth = parts
            user, _, pwd = auth.partition(":")
            return f"{user}:{pwd}@{host}:{port}"
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


PATH_SAFE_CONE_DEG = 30.0
PATH_SAFE_CONE_RAD = math.radians(PATH_SAFE_CONE_DEG)


def is_session_timeout(elapsed: float, is_ci: bool) -> bool:
    """Hard watchdog check for runs (cutoff at limit + 60s buffer for sync/reporting)."""
    base_limit = 5500 if is_ci else 5800
    try:
        env_max = int(os.getenv("MAX_DURATION", "0") or "0")
    except ValueError:
        env_max = 0
    limit = max(base_limit, env_max + 200)
    return elapsed >= (limit + 60)


def calculate_avoid_radius(mass: float) -> float:
    """Dynamic avoidance radius scaled to snake turning physics (capped at 400px)."""
    return max(220.0, min(400.0, 180.0 + mass * 0.08))


def get_phase_weights(mass: float, current_phase: int = 1) -> dict:
    """3-phase weight distribution with hysteresis for food, clusters, and decay compensation."""
    phase = current_phase
    if mass < 350:
        phase = 1
    elif phase == 3 and mass < 1400:
        phase = 2
    elif phase != 3 and mass >= 1600:
        phase = 3
    elif phase == 1 and mass >= 400:
        phase = 2

    if phase == 3:
        return {"phase": 3, "food": 1.5, "cluster": 3.0}
    elif phase == 2:
        return {"phase": 2, "food": 1.0, "cluster": 2.5}
    else:
        return {"phase": 1, "food": 1.0, "cluster": 2.0}


def check_path_safe_angle(hx: float, hy: float, cx: float, cy: float, ex: float, ey: float) -> bool:
    """Check if enemy at (ex, ey) blocks path from head (hx, hy) to cluster (cx, cy) within +/-30 deg."""
    cluster_dist = math.hypot(cx - hx, cy - hy)
    enemy_dist = math.hypot(ex - hx, ey - hy)
    if enemy_dist > cluster_dist:
        return True
    path_angle = math.atan2(cy - hy, cx - hx)
    enemy_angle = math.atan2(ey - hy, ex - hx)
    angle_diff = abs(((enemy_angle - path_angle + math.pi) % (2 * math.pi)) - math.pi)
    return angle_diff >= PATH_SAFE_CONE_RAD


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
        binary_location=binary,
        chromium_arg="--disable-background-timer-throttling,--disable-backgrounding-occluded-windows,--disable-renderer-backgrounding"
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

            run_idx = 1
            attempts = 0
            max_attempts = max_runs * 4  # Дозволяємо до 4 спроб на 1 забіг
            while run_idx <= max_runs and attempts < max_attempts:
                attempts += 1
                print(f"\n{'='*25} РАУНД #{run_idx} (Спроба {attempts}/{max_attempts}) {'='*25}")

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
                safe_eval(sb, """
                    var btn = document.querySelector('#cs-start');
                    if (btn && !btn.disabled) {
                        btn.scrollIntoView({ block: 'center' });
                        btn.click();
                    }
                """)
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
                            var btn = document.querySelector('#cs-start');
                            if (btn && !btn.disabled) {
                                btn.scrollIntoView({ block: 'center' });
                                btn.click();
                            }
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

                    # Перевіряємо статус попередження (кулдаун попереднього забігу тільки якщо елемент НЕ прихований)
                    status_text = safe_eval(sb, """
                        var s = document.querySelector('#cs-season-status');
                        var w = document.querySelector('#cs-run-warning');
                        return ((s && !s.hidden) ? (s.innerText || '') : ((w && !w.hidden) ? (w.innerText || '') : ''));
                    """)
                    if "активний забіг" in status_text.lower():
                        print(f"[BOT] ⏳ Сервер утримує сесію попереднього забігу. Очікуємо завершення кулдауну ({check_i+1}/25)...")
                        sb.sleep(4)
                        safe_eval(sb, """
                            var btn = document.querySelector('#cs-start');
                            if (btn && !btn.disabled) {
                                btn.scrollIntoView({ block: 'center' });
                                btn.click();
                            }
                        """)
                        continue

                    # Якщо кнопка старту все ще активна, пробуємо натиснути ще раз
                    if check_i in (4, 10, 16):
                        safe_eval(sb, """
                            var btn = document.querySelector('#cs-start');
                            if (btn && !btn.disabled) {
                                btn.scrollIntoView({ block: 'center' });
                                btn.click();
                            }
                        """)
                    sb.sleep(1)

                if not game_active:
                    diag = safe_eval(sb, """
                        var s = document.querySelector('#cs-season-status');
                        var w = document.querySelector('#cs-run-warning');
                        var activeWarn = ((s && !s.hidden) ? s.innerText : ((w && !w.hidden) ? w.innerText : ''));
                        return {
                            title: document.title,
                            userId: window.CryptoSnakeWP?.userId || 0,
                            btnText: document.querySelector('#cs-start')?.innerText,
                            btnDisabled: document.querySelector('#cs-start')?.disabled,
                            hasPopup: !!document.querySelector('.incr-popup-login'),
                            warn: activeWarn
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
                    max_duration = int(os.getenv("MAX_DURATION", "180"))
                    print(f"[BOT] 🧪 УВІМКНЕНО DRY_RUN ({max_duration}с тест). CDP Space імітується, вимірюємо кластери та запобіжники.")
                else:
                    default_duration = 5400 if os.getenv("GITHUB_ACTIONS") else 5600
                    max_duration = int(os.getenv("MAX_DURATION", default_duration))
                    print(f"[BOT] Змійка на полі! Марафон на {max_duration // 60} хв: Turbo Vacuum (Space) + Smart Predator + Dynamic Walls...")

                block_steering_on_unsafe = os.getenv("BLOCK_STEERING_ON_UNSAFE", "0") == "1"
                enable_core_fx = os.getenv("ENABLE_CORE_FX", "0") == "1"
                min_mass_for_boost = int(os.getenv("MIN_MASS_FOR_BOOST", "350"))
                safe_eval(sb, f"""
                    window.__blockSteeringOnUnsafe = {'true' if block_steering_on_unsafe else 'false'};
                    window.__enableCoreFx = {'true' if enable_core_fx else 'false'};
                    window.__minMassForBoost = {min_mass_for_boost};
                """)

                boost_active = False
                boost_start_time = 0.0
                boost_start_mass = 0.0
                boost_cooldown_until = 0.0
                boost_triggers_count = 0
                path_safe_blocked_count = 0
                path_safe_steering_blocked_count = 0
                core_fx_activations = 0
                max_cluster_seen = 0.0
                last_raycast_log_time = 0.0
                raycast_panics_count = 0
                last_telemetry = {}
                finish_logged_printed = False
                boost_win_count = 0
                boost_loss_count = 0
                boost_net_mass = 0.0
                kill_coins_est = 0
                starvation_events = 0
                mass_sum_accum = 0.0

                # Забіг триває доти, доки змійка жива (або вичерпано час сесії)
                last_phase = 1
                last_mass_val = 0
                last_mass_time = time.time()
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
                                var myGuild = me.guild || 'near';
                                var meR = me.r || (10 + Math.min(26, 0.9 * Math.sqrt(meMass)));
                                var headingX = (me.pts.length >= 4) ? (hx - me.pts[2]) : Math.cos(me.ang || 0);
                                var headingY = (me.pts.length >= 4) ? (hy - me.pts[3]) : Math.sin(me.ang || 0);
                                var hNorm = Math.hypot(headingX, headingY) || 1.0;
                                headingX /= hNorm;
                                headingY /= hNorm;
                                var currentHeading = (me.ang !== undefined) ? me.ang : Math.atan2(headingY, headingX);

                                // 1. Пошук монет із курсовим пріоритетом та сканер трупів (640px, NEAR x2)
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

                                        var ux = dx / dist;
                                        var uy = dy / dist;
                                        var cosAngle = ux * headingX + uy * headingY;

                                        // Ігноруємо дрібні монети у сліпій зоні розвороту позаду голови (< 55px і кут > 95°)
                                        if (dist < 55 && cosAngle < -0.1 && f.natural !== false) continue;

                                        var isMyGuildCoin = (f.type === myGuild);
                                        if (dist <= 820) {
                                            var valMult = (f.natural === false && f.v) ? Math.min(2.5, Math.sqrt(f.v) * 1.15) : 1.0;
                                            var alignMult = Math.max(0.65, 1.0 + 0.55 * cosAngle);
                                            // Попутний пилосос (Transit Grazing): не пропускаємо близькі монети прямо за курсом під час польоту до кластера
                                            if (dist < 150 && cosAngle > 0.72) alignMult *= 2.35;
                                            var guildWeight = isMyGuildCoin ? 4.8 : 1.0;
                                            var effDist = dist * (dist + 35.0);
                                            var force = (guildWeight * valMult * alignMult) / effDist;
                                            foodFx += ux * force;
                                            foodFy += uy * force;
                                        }

                                        // Скануємо трупи до 950px (далі 950px втрати на decay перевищують вигоду)
                                        if (f.natural === false && dist <= 950) {
                                            var effectiveVal = (f.v || 1) * (isMyGuildCoin ? 2.2 : 1.0);
                                            drops.push({ x: f.x, y: f.y, v: effectiveVal, dist: dist });
                                        }
                                    }
                                }

                                // 2. Ланцюгова BFS-кластеризація дропів (радіус зв'язності 65px об'єднує всю тушу змії)
                                var clusterFx = 0;
                                var clusterFy = 0;
                                var bestCluster = null;

                                if (drops.length >= 3) {
                                    var visited = new Uint8Array(drops.length);
                                    for (var i = 0; i < drops.length; i++) {
                                        if (visited[i]) continue;
                                        visited[i] = 1;
                                        var queue = [i];
                                        var cMass = 0;
                                        var cCount = 0;
                                        var sumX = 0;
                                        var sumY = 0;
                                        var minDropDist = 9999;
                                        var nearestX = drops[i].x;
                                        var nearestY = drops[i].y;

                                        while (queue.length > 0) {
                                            var curr = queue.pop();
                                            var dItem = drops[curr];
                                            cMass += dItem.v;
                                            cCount += 1;
                                            sumX += dItem.x * dItem.v;
                                            sumY += dItem.y * dItem.v;
                                            if (dItem.dist < minDropDist) {
                                                minDropDist = dItem.dist;
                                                nearestX = dItem.x;
                                                nearestY = dItem.y;
                                            }
                                            for (var j = 0; j < drops.length; j++) {
                                                if (visited[j]) continue;
                                                var ddx = dItem.x - drops[j].x;
                                                var ddy = dItem.y - drops[j].y;
                                                if (ddx * ddx + ddy * ddy <= 4225) { // 65px ланцюгова зв'язність
                                                    visited[j] = 1;
                                                    queue.push(j);
                                                }
                                            }
                                        }

                                        if (cMass >= 21) {
                                            var cx_drop = 0.50 * (sumX / cMass) + 0.50 * nearestX;
                                            var cy_drop = 0.50 * (sumY / cMass) + 0.50 * nearestY;
                                            var cDist = Math.hypot(cx_drop - hx, cy_drop - hy);
                                            if (cDist > 550 && cMass < 55) continue;
                                            var cAngle = Math.atan2(cy_drop - hy, cx_drop - hx);
                                            var cCos = Math.cos(cAngle - currentHeading);
                                            var dirBonus = 1.0 + 0.35 * cCos;
                                            // Бонус стабільності цілі (1.30x), щоб не кидати великий кластер на півдорозі
                                            var stickyBonus = (window.__lastClusterX !== undefined && Math.hypot(cx_drop - window.__lastClusterX, cy_drop - window.__lastClusterY) < 95) ? 1.30 : 1.0;
                                            var cScore = (Math.pow(cMass, 1.15) * dirBonus * stickyBonus) / Math.max(45, Math.pow(cDist, 0.85));
                                            if (!bestCluster || cScore > (bestCluster.score || 0)) {
                                                bestCluster = {
                                                    mass: cMass,
                                                    count: cCount,
                                                    x: cx_drop,
                                                    y: cy_drop,
                                                    dist: cDist,
                                                    nearestDist: minDropDist,
                                                    score: cScore
                                                };
                                            }
                                        }
                                    }
                                }
                                if (bestCluster) {
                                    window.__lastClusterX = bestCluster.x;
                                    window.__lastClusterY = bestCluster.y;
                                } else {
                                    window.__lastClusterX = undefined;
                                }

                                // v3.7: надійний коридор безпеки до кластера (360px, конус ±20° = 0.35 рад) без сліпої зони біля ядра
                                var isPathSafe = true;
                                if (bestCluster) {
                                    var pathAngle = Math.atan2(bestCluster.y - hy, bestCluster.x - hx);
                                    for (var other of w.snakes.values()) {
                                        if (other.id === me.id || !other.alive || other.guild === myGuild) continue;
                                        if (!other.pts || other.pts.length < 2) continue;
                                        var startIdx = (other.id === huntedEnemyId && other.pts.length > 8) ? 4 : 0;
                                        var step = other.pts.length > 40 ? 4 : 2;
                                        for (var p = startIdx; p < other.pts.length; p += step) {
                                            var ox = other.pts[p], oy = other.pts[p + 1];
                                            var enemyDist = Math.hypot(ox - hx, oy - hy);
                                            if (enemyDist > Math.min(bestCluster.dist, 360)) continue;
                                            var enemyAngle = Math.atan2(oy - hy, ox - hx);
                                            var angleDiff = Math.abs(Math.atan2(Math.sin(enemyAngle - pathAngle), Math.cos(enemyAngle - pathAngle)));
                                            if (angleDiff < 0.35) {
                                                isPathSafe = false;
                                                break;
                                            }
                                        }
                                        if (!isPathSafe) break;
                                    }
                                }

                                var pathSafeSteeringBlocked = Boolean(bestCluster && bestCluster.dist > 1 && !isPathSafe);
                                var blockSteeringOnUnsafe = Boolean(window.__blockSteeringOnUnsafe) && meMass > 400;

                                // 3. Ухилення від ворожих змій (підвищений буфер 230px на спавні < 220 маси)
                                var avoidRadius = (meMass < 220) ? 230 : Math.max(205, Math.min(255, 180 + meMass * 0.04));
                                var avoidFx = 0;
                                var avoidFy = 0;
                                var minEnemyDist = 9999;
                                var minFrontEnemyDist = 9999;
                                var nearestEnemy = null;
                                var huntedEnemyId = null;
                                var nowMsPre = Date.now();
                                var evadeActiveNow = Boolean(window.__evadeLockUntil && nowMsPre < window.__evadeLockUntil);
                                for (var other of w.snakes.values()) {
                                    if (other.id === me.id || !other.alive) continue;
                                    if (other.guild === myGuild) continue; // friendlyFire: false
                                    if (!other.pts || other.pts.length < 2) continue;

                                    var hasShield = (other.shield || 0) > 0.1;
                                    var isSmallerEnemy = !hasShield && ((other.mass || 0) < meMass * 0.78);
                                    var stepP = other.pts.length <= 8 ? 2 : 4;
                                    for (var p = 0; p < other.pts.length; p += stepP) {
                                        var ox = other.pts[p];
                                        var oy = other.pts[p + 1];
                                        var odist = Math.hypot(hx - ox, hy - oy);
                                        var segCos = ((ox - hx) / (odist || 1)) * headingX + ((oy - hy) / (odist || 1)) * headingY;

                                        // Завжди фіксуємо реальну дистанцію до ворога (крім атакованої голови huntedEnemyId)
                                        if (!(p === 0 && other.id === huntedEnemyId) && odist < minEnemyDist) {
                                            minEnemyDist = odist;
                                            nearestEnemy = {
                                                id: other.id,
                                                name: other.name || ('Snake#' + other.id),
                                                mass: Math.round(other.mass || 0),
                                                dist: Math.round(odist),
                                                is_head: (p === 0)
                                            };
                                        }
                                        // Окремо фіксуємо дистанцію у передній півсфері (±75°), щоб хвости позаду не блокували Boost вперед
                                        if (!(p === 0 && other.id === huntedEnemyId) && segCos > 0.25 && odist < minFrontEnemyDist) {
                                            minFrontEnemyDist = odist;
                                        }

                                        // Активне полювання (Head-Hunter): тільки на мобільних масах 500-1350 і коли не активний маневр ухилення
                                        if (!evadeActiveNow && p === 0 && isSmallerEnemy && (other.mass || 0) >= 60 && meMass >= 500 && meMass < 1350 && odist < 115 && odist > 15 && other.pts.length >= 6) {
                                            var eHx = ox - other.pts[2];
                                            var eHy = oy - other.pts[3];
                                            var eNorm = Math.hypot(eHx, eHy) || 1.0;
                                            var enemyApproachingCos = ((hx - ox) / odist) * (eHx / eNorm) + ((hy - oy) / odist) * (eHy / eNorm);
                                            if (segCos > 0.76 && enemyApproachingCos > 0.60) {
                                                var huntForce = 115.0 / (odist * (odist + 20.0));
                                                foodFx += ((ox - hx) / odist) * huntForce;
                                                foodFy += ((oy - hy) / odist) * huntForce;
                                                huntedEnemyId = other.id;
                                            }
                                        }

                                        if (odist < avoidRadius && odist > 1) {
                                            // Враховуємо реальну відстань між оболонками (skin-to-skin) та рух голови ворога назустріч
                                            var oR = other.r || (10 + Math.min(26, 0.9 * Math.sqrt(other.mass || 0)));
                                            var skinDist = Math.max(12.0, odist - (meR + oR) * 0.85);
                                            var headMultiplier = (p === 0) ? ((other.id === huntedEnemyId) ? 0.30 : ((isSmallerEnemy && meMass < 1200) ? 0.65 : 2.8)) : 1.0;
                                            if (p === 0 && other.pts.length >= 4) {
                                                var evx = ox - other.pts[2];
                                                var evy = oy - other.pts[3];
                                                var evNorm = Math.hypot(evx, evy) || 1.0;
                                                var appCos = ((hx - ox) / odist) * (evx / evNorm) + ((hy - oy) / odist) * (evy / evNorm);
                                                if (appCos > 0.2) headMultiplier *= (1.0 + 0.65 * appCos);
                                            }
                                            var closeBoost = (odist < 155) ? ((odist < 105) ? 3.0 : 1.85) : 1.0;
                                            var repForce = (105.0 * headMultiplier * closeBoost) / (skinDist * skinDist);
                                            avoidFx += ((hx - ox) / odist) * repForce;
                                            avoidFy += ((hy - oy) / odist) * repForce;
                                        }
                                    }
                                }

                                // Далекобійний магніт: безпечна тяга з v3.4.1 (cappedMass 260, суворе гасіння при ворогах < 165px)
                                if (bestCluster && bestCluster.dist > 45 && (!blockSteeringOnUnsafe || isPathSafe)) {
                                    var cDist = bestCluster.dist;
                                    var cappedMass = Math.min(bestCluster.mass, 260);
                                    var safetyDamp = isPathSafe ? (minEnemyDist < 150 ? 0.75 : 1.45) : (minEnemyDist > 175 ? 0.55 : 0.22);
                                    var cForce = (cappedMass / (cDist * (110.0 + 0.32 * cDist))) * safetyDamp;
                                    clusterFx = ((bestCluster.x - hx) / cDist) * cForce;
                                    clusterFy = ((bestCluster.y - hy) / cDist) * cForce;
                                }

                                // 4. Відштовхування від меж арени (динамічний safeRadius від 280px до 400px)
                                var wallFx = 0;
                                var wallFy = 0;
                                var distFromCenter = Math.hypot(hx, hy);
                                var arenaRadius = w.radius || 2000;
                                var wallBuffer = Math.max(250, Math.min(320, meMass * 0.05));
                                var safeRadius = arenaRadius - wallBuffer;

                                if (distFromCenter > safeRadius) {
                                    var wallForce = ((distFromCenter - safeRadius) / wallBuffer) * 20.0;
                                    wallFx = -(hx / distFromCenter) * wallForce;
                                    wallFy = -(hy / distFromCenter) * wallForce;
                                }

                                // 5. 3-фазний розподіл ваг та захист від розпаду (Decay Compensation з hysteresis)
                                var phase = (window.__phase || 1);
                                if (meMass < 350) phase = 1;
                                else if (phase === 3 && meMass < 1400) phase = 2;
                                else if (phase !== 3 && meMass >= 1600) phase = 3;
                                else if (phase === 1 && meMass >= 400) phase = 2;
                                window.__phase = phase;

                                var foodWeight = 1.0;
                                var clusterWeight = 2.0;
                                if (phase === 3) {
                                    // Баланс v3.4.1 (5388с марафон): достатній для росту маси, але не перебиває avoidFx
                                    foodWeight = 2.05 + Math.min(0.55, Math.max(0, meMass - 1600) * 0.0007);
                                    clusterWeight = 3.4;
                                } else if (phase === 2) {
                                    foodWeight = 1.25;
                                    clusterWeight = 2.7;
                                }

                                // Двоступеневе гасіння магнітів їжі/кластерів при зближенні з ворогом (< 175px та < 115px)
                                if (minEnemyDist < 115) {
                                    foodWeight *= 0.45;
                                    clusterWeight *= 0.15;
                                } else if (minEnemyDist < 175) {
                                    foodWeight *= 0.72;
                                    clusterWeight *= 0.38;
                                }

                                // М'яке відхилення від ядра (coreFx): вимкнено при наявності будь-якого кластера, щоб не втрачати м'ясо в центрі
                                var coreFxActive = Boolean(meMass > 1500 && distFromCenter < 450 && !bestCluster);
                                var enableCoreFx = Boolean(window.__enableCoreFx);
                                var coreFx = 0;
                                var coreFy = 0;
                                if (enableCoreFx && coreFxActive) {
                                    var coreForce = ((450 - distFromCenter) / 450) * 4.0;
                                    coreFx = (hx / (distFromCenter || 1)) * coreForce;
                                    coreFy = (hy / (distFromCenter || 1)) * coreForce;
                                }

                                var finalX = foodFx * foodWeight + clusterFx * clusterWeight + avoidFx * 5.0 + wallFx * 7.5 + coreFx;
                                var finalY = foodFy * foodWeight + clusterFy * clusterWeight + avoidFy * 5.0 + wallFy * 7.5 + coreFy;
                                var targetAngle = Math.atan2(finalY, finalX);

                                var checkRaySafety = function(angle) {
                                    var maxR = arenaRadius - 1.5 * meR;
                                    var maxRSq = maxR * maxR;
                                    // Постійний множник 1.15 усуває фальшиві само-аборти бусту в перші 0.1с після натискання Space
                                    var speedMult = 1.15;
                                    var rayDists = [
                                        (meR + 12) * speedMult,
                                        (2.2 * meR + 25) * speedMult,
                                        (3.4 * meR + 50) * speedMult,
                                        (4.6 * meR + 85) * speedMult
                                    ];
                                    for (var i = 0; i < rayDists.length; i++) {
                                        var d = rayDists[i];
                                        var danger = rayDists.length - i; // 4=впритул, 3=близько, 2=середньо, 1=далеко
                                        // v3.4.2: повернено вуса [-0.15, 0, 0.15] на i==3, щоб бачити діагональних бустерів на 200-250px
                                        var spread = (i <= 1) ? [-0.42, -0.21, 0, 0.21, 0.42] : ((i === 2) ? [-0.22, 0, 0.22] : [-0.15, 0, 0.15]);
                                        for (var sIdx = 0; sIdx < spread.length; sIdx++) {
                                            var a = angle + spread[sIdx];
                                            var px = hx + Math.cos(a) * d;
                                            var py = hy + Math.sin(a) * d;
                                            if (px * px + py * py > maxRSq) return danger;

                                            for (var other of w.snakes.values()) {
                                                if (other.id === me.id || !other.alive) continue;
                                                if (other.guild === myGuild) continue;
                                                if (!other.pts || other.pts.length < 2) continue;

                                                var otherR = other.r || (10 + Math.min(26, 0.9 * Math.sqrt(other.mass || 0)));
                                                var hitDist = otherR + meR + (spread[sIdx] === 0 ? 12 : 8);
                                                var hitDistSq = hitDist * hitDist;
                                                var startP = (other.id === huntedEnemyId && other.pts.length > 8) ? 4 : 0;

                                                // Векторна екстраполяція голови ворога на 0.20с та 0.40с вперед (проти зустрічних і бокових перерізань)
                                                if (startP === 0 && other.pts.length >= 4) {
                                                    var ehx = other.pts[0];
                                                    var ehy = other.pts[1];
                                                    var edist = Math.hypot(ehx - hx, ehy - hy);
                                                    if (edist < 320) {
                                                        var vx = ehx - other.pts[2];
                                                        var vy = ehy - other.pts[3];
                                                        var vNorm = Math.hypot(vx, vy) || 1.0;
                                                        vx /= vNorm;
                                                        vy /= vNorm;
                                                        var appCosRay = ((hx - ehx) / (edist || 1)) * vx + ((hy - ehy) / (edist || 1)) * vy;
                                                        if (appCosRay > -0.15) {
                                                            var leadL = Math.max(34.0, Math.min(72.0, edist * 0.34)) * (1.0 + 0.35 * Math.max(0, appCosRay));
                                                            var p1x = ehx + vx * (0.5 * leadL) - px;
                                                            var p1y = ehy + vy * (0.5 * leadL) - py;
                                                            var p2x = ehx + vx * leadL - px;
                                                            var p2y = ehy + vy * leadL - py;
                                                            if ((p1x * p1x + p1y * p1y < hitDistSq) || (p2x * p2x + p2y * p2y < hitDistSq)) {
                                                                return danger;
                                                            }
                                                        }
                                                    }
                                                }

                                                for (var p = startP; p < other.pts.length; p += 2) {
                                                    var sx = other.pts[p] - px;
                                                    var sy = other.pts[p + 1] - py;
                                                    if (sx * sx + sy * sy < hitDistSq) return danger;
                                                }
                                            }
                                        }
                                    }
                                    return 0;
                                };

                                var nowMs = Date.now();
                                if (window.__evadeLockUntil && nowMs < window.__evadeLockUntil && window.__evadeLockAngle !== undefined) {
                                    // Утримуємо замок ТІЛЬКИ якщо промінь замка чистий (<= 1), як у 5388-секундному забігу v3.4.1
                                    if (checkRaySafety(window.__evadeLockAngle) <= 1) {
                                        targetAngle = window.__evadeLockAngle;
                                    }
                                }

                                var primaryDanger = checkRaySafety(targetAngle);
                                var headingDanger = checkRaySafety(currentHeading);
                                var raycastPanicked = false;
                                var raycastDeltaDeg = 0;
                                var raycastDanger = Math.max(primaryDanger, headingDanger);
                                // Не тригеримо фальшивий evade lv.0, коли ворог позаду, а всі промені попереду чисті (raycastDanger === 0)
                                var closeEmergency = (minEnemyDist < 98 && raycastDanger >= 1);
                                // Hard Evade v3.8: екстрапольований радар + trapPenalty проти підрулювання на заблокований targetAngle + shallowPenalty
                                if (primaryDanger >= 2 || headingDanger >= 3 || closeEmergency) {
                                    var candidateDeltas = [0, 0.28, -0.28, 0.55, -0.55, 0.82, -0.82, 1.12, -1.12, 1.45, -1.45, 1.75, -1.75];
                                    var minCost = 999999;
                                    var safestAngle = targetAngle;
                                    var prevSign = (window.__lastEvadeTime && (nowMs - window.__lastEvadeTime < 1800)) ? (window.__lastEvadeSign || 0) : 0;
                                    for (var delta of candidateDeltas) {
                                        var testAngle = currentHeading + delta;
                                        var dScore = checkRaySafety(testAngle);
                                        var absDelta = Math.abs(delta);
                                        var angleDiff = Math.abs(Math.atan2(Math.sin(testAngle - targetAngle), Math.cos(testAngle - targetAngle)));
                                        // Штраф 16.0 (> 7.0*2) блокує реверси на ΔdScore<=2, але дозволяє вихід із глухого кута (7.0*4 = 28.0 > 16.0)
                                        var flipPenalty = (prevSign !== 0 && delta !== 0 && Math.sign(delta) !== prevSign) ? (minEnemyDist < 170 ? 16.0 : 5.5) : 0.0;
                                        // 1) Якщо ніс під загрозою (headingDanger >= 3 або closeEmergency) -> забороняємо кути < 45° (0.78 рад)
                                        // 2) Якщо targetAngle заблокований ворогом (primaryDanger >= 3) -> забороняємо кути, ближчі ніж 45° до targetAngle (trapPenalty = 8.5)
                                        var shallowPenalty = ((headingDanger >= 3 || closeEmergency) && absDelta < 0.78) ? 9.5 : 0.0;
                                        var trapPenalty = (primaryDanger >= 3 && angleDiff < 0.78) ? 8.5 : ((primaryDanger === 2 && angleDiff < 0.45) ? 4.5 : 0.0);
                                        var outCos = (distFromCenter > safeRadius - 120) ? ((hx * Math.cos(testAngle) + hy * Math.sin(testAngle)) / (distFromCenter || 1)) : -1.0;
                                        var wallPenalty = (outCos > 0) ? (3.8 * outCos) : 0.0;
                                        // turnWeight (0.26) суворо більший за goalWeight (0.18) при небезпеці, щоб не тягнуло на +16° у бік ворога
                                        var goalWeight = (closeEmergency || raycastDanger >= 3) ? 0.18 : 0.85;
                                        var cost = 7.0 * dScore + shallowPenalty + trapPenalty + goalWeight * angleDiff + 0.26 * absDelta + flipPenalty + wallPenalty;
                                        if (cost < minCost) {
                                            minCost = cost;
                                            safestAngle = testAngle;
                                        }
                                    }
                                    var rawDiff = Math.atan2(Math.sin(safestAngle - currentHeading), Math.cos(safestAngle - currentHeading));
                                    var maxTurnRad = (raycastDanger >= 4 || closeEmergency) ? 1.57 : ((raycastDanger === 3) ? 1.15 : 0.78);
                                    var clampedDiff = Math.max(-maxTurnRad, Math.min(maxTurnRad, rawDiff));
                                    safestAngle = currentHeading + clampedDiff;
                                    if (Math.abs(clampedDiff) >= 0.09) {
                                        raycastPanicked = true;
                                        raycastDeltaDeg = Math.round(clampedDiff * 180 / Math.PI);
                                        window.__evadeLockAngle = safestAngle;
                                        window.__evadeLockUntil = nowMs + 480;
                                        window.__lastEvadeSign = Math.sign(clampedDiff);
                                        window.__lastEvadeTime = nowMs;
                                    }
                                    targetAngle = safestAngle;
                                }

                                // 6. Умови для безпечного та прибуткового Boost (Turbo Vacuum Wave 3):
                                // - Кластер з deathDrop >= 21 маси (поріг окупності)
                                // - Жодного ворога ближче 250px (строгий запобіжник!)
                                // - Безпечний коридор до кластера (isPathSafe === true)
                                // - Достатня маса для ривка (minMassForBoost = 350, Phase 2+)
                                // - Відстань до кластера > 40px (ще не досягли)
                                // - Безпечна відстань від краю арени
                                var minMassForBoost = window.__minMassForBoost || 350;
                                // v3.7: відновлено строгий круговий буфер minEnemyDist > safeEnemyDistForBoost (225-260px), щоб не влітати у ворогів після бусту
                                var minClusterForBoost = (meMass >= 1700) ? 110 : ((meMass >= 1100) ? 68 : ((meMass >= 650) ? 42 : 28));
                                var safeEnemyDistForBoost = Math.max(225, Math.min(260, 185 + meMass * 0.035));
                                var maxBoostDist = (bestCluster && bestCluster.mass >= 220) ? 180 : 155;
                                var nearEdgeOk = Boolean(bestCluster && (bestCluster.nearestDist || bestCluster.dist) <= 145);
                                var clusterAlignCos = bestCluster ? Math.cos(Math.atan2(bestCluster.y - hy, bestCluster.x - hx) - currentHeading) : -1.0;
                                var boostCandidate = Boolean(
                                    bestCluster &&
                                    bestCluster.mass >= minClusterForBoost &&
                                    nearEdgeOk &&
                                    clusterAlignCos > 0.84 &&
                                    primaryDanger === 0 &&
                                    headingDanger === 0 &&
                                    minEnemyDist > safeEnemyDistForBoost &&
                                    meMass >= minMassForBoost &&
                                    bestCluster.dist > 38 &&
                                    bestCluster.dist <= maxBoostDist &&
                                    distFromCenter < (safeRadius - 120)
                                );
                                var shouldBoost = boostCandidate && isPathSafe;
                                var pathSafeBlocked = boostCandidate && !isPathSafe;

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
                                    nearest_enemy: nearestEnemy,
                                    dist_center: distFromCenter,
                                    safe_radius: safeRadius,
                                    cluster_mass: bestCluster ? bestCluster.mass : 0,
                                    cluster_dist: bestCluster ? bestCluster.dist : 0,
                                    is_path_safe: isPathSafe,
                                    path_safe_blocked: pathSafeBlocked,
                                    path_safe_steering_blocked: pathSafeSteeringBlocked,
                                    core_fx_active: coreFxActive,
                                    should_boost: shouldBoost,
                                    raycast_panicked: raycastPanicked,
                                    raycast_danger: raycastDanger,
                                    raycast_delta_deg: raycastDeltaDeg
                                };
                            })()
                        """)

                        if decision:
                            me_mass = float(decision.get("mass") or 0)
                            prev_score = score_earned
                            val = decision.get("coins") or "0"
                            clean_score = "".join([c for c in str(val) if c.isdigit()])
                            if clean_score:
                                score_earned = max(score_earned, int(clean_score))

                            k_val = decision.get("kills") or "0"
                            clean_kills = "".join([c for c in str(k_val) if c.isdigit()])
                            if clean_kills:
                                current_kills_int = int(clean_kills)
                                if current_kills_int > kills_earned:
                                    kill_delta = current_kills_int - kills_earned
                                    kills_earned = current_kills_int
                                    kill_coins_est += max(0, score_earned - prev_score)
                                    alive_t = int(time.time() - start_time)
                                    print(f"[BOT] ⚔️ ВБИТО ЗМІЮ! Kills: {kills_earned} (+{kill_delta}) | Маса: {me_mass:.0f} | Монети: {score_earned} (Час: {alive_t}с)")

                            if decision.get("is_dead"):
                                death_sec = int(time.time() - start_time)
                                d_mass = last_telemetry.get("mass", me_mass)
                                d_phase = last_telemetry.get("phase", 1)
                                d_boost = " [під час BOOST]" if last_telemetry.get("boost_active") else ""
                                d_enemy = last_telemetry.get("enemy")
                                d_edist = last_telemetry.get("enemy_dist", 9999)
                                d_center = last_telemetry.get("dist_center", 0)
                                d_radius = last_telemetry.get("safe_radius", 1750)

                                if d_enemy and d_enemy.get("dist", 9999) < 260:
                                    enemy_desc = f"{d_enemy.get('name', 'Snake')} (маса {d_enemy.get('mass', 0):.0f}, dist ~{d_enemy.get('dist', 0)}px)"
                                elif d_center > (d_radius - 30):
                                    enemy_desc = f"Межа арени (відстань від центру {d_center:.0f}px / радіус {d_radius:.0f}px)"
                                else:
                                    enemy_desc = f"Ворог на дистанції {d_edist:.0f}px"

                                print(f"\n[BOT] 💀 СМЕРТЬ ЗМІЙКИ на {death_sec}с/{max_duration}с{d_boost}!")
                                print(f"      • Фаза: {d_phase} | Маса перед загибеллю: {d_mass:.0f} | Монети: {score_earned} | Kills: {kills_earned}")
                                print(f"      • Обставини: {enemy_desc}")
                                print(f"[BOT] Забіг завершено (зіткнення/кінець гри).")
                                break

                            target_angle = decision.get("angle")
                            elapsed_run = time.time() - start_time
                            # За 15 секунд до кінця марафону скеровуємо змійку в найближчу стіну для гарантованої фіксації рекорду на сервері
                            if not DRY_RUN and elapsed_run >= (max_duration - 15):
                                if not finish_logged_printed:
                                    print(f"[BOT] 🏁 ФІНІШ МАРАФОНУ ({int(elapsed_run)}с/{max_duration}с): плановий вихід у стіну для збереження рекорду ({score_earned} монет)!")
                                    finish_logged_printed = True
                                target_angle = safe_eval(sb, """
                                    var w = window.__activeWorld;
                                    var me = w && window.__mySnakeId !== undefined ? w.snakes.get(window.__mySnakeId) : null;
                                    return (me && me.pts) ? Math.atan2(me.pts[1], me.pts[0]) : 0.0;
                                """)
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
                            min_enemy_dist = float(decision.get("enemy_dist") or 9999)
                            dist_center = float(decision.get("dist_center") or 0)
                            safe_radius = float(decision.get("safe_radius") or 1750)
                            cluster_mass = float(decision.get("cluster_mass") or 0)
                            cluster_dist = float(decision.get("cluster_dist") or 0)
                            should_boost = bool(decision.get("should_boost"))
                            is_path_safe = bool(decision.get("is_path_safe", True))
                            path_safe_blocked = bool(decision.get("path_safe_blocked", False))
                            now = time.time()

                            # Зберігаємо телеметрію живого стану для діагностики смерті
                            last_telemetry = {
                                "mass": me_mass,
                                "phase": decision.get("phase", 1),
                                "enemy": decision.get("nearest_enemy"),
                                "enemy_dist": min_enemy_dist,
                                "dist_center": dist_center,
                                "safe_radius": safe_radius,
                                "boost_active": boost_active
                            }

                            # Лог паніки Raycast (коли він різко відвертає від курсу їжі/кластера)
                            raycast_panicked = bool(decision.get("raycast_panicked"))
                            raycast_delta_deg = int(decision.get("raycast_delta_deg") or 0)
                            raycast_danger = int(decision.get("raycast_danger") or 0)
                            if raycast_panicked:
                                raycast_panics_count += 1
                                if now - last_raycast_log_time >= 0.4:
                                    last_raycast_log_time = now
                                    turn_dir = "вправо" if raycast_delta_deg > 0 else "вліво"
                                    print(f"[BOT] ⚠️ Raycast Evade! Небезпека lv.{raycast_danger} (ворог {min_enemy_dist:.0f}px), різкий маневр {turn_dir} на {abs(raycast_delta_deg)}° від курсу (маса {me_mass:.0f})")

                            if path_safe_blocked and (now >= boost_cooldown_until):
                                path_safe_blocked_count += 1
                            if decision.get("path_safe_steering_blocked"):
                                path_safe_steering_blocked_count += 1
                            if decision.get("core_fx_active"):
                                core_fx_activations += 1

                            if cluster_mass > max_cluster_seen:
                                max_cluster_seen = cluster_mass

                            # 1. Запобіжники вимкнення активного Boost
                            if boost_active:
                                time_boosting = now - boost_start_time
                                stop_reason = None
                                if time_boosting >= 0.50:
                                    stop_reason = "таймаут імпульсу 0.50с"
                                elif raycast_danger >= 2 or min_enemy_dist < 210:
                                    stop_reason = f"наближення ворога ({min_enemy_dist:.0f}px)"
                                elif dist_center > safe_radius:
                                    stop_reason = "наближення до стіни арени"
                                elif me_mass < min_mass_for_boost:
                                    stop_reason = f"захист мінімальної маси ({me_mass:.0f} < {min_mass_for_boost})"
                                elif cluster_mass < 21 or cluster_dist < 35:
                                    stop_reason = "кластер зібрано / ціль зникла"

                                if stop_reason:
                                    mass_diff = me_mass - boost_start_mass
                                    boost_net_mass += mass_diff
                                    if mass_diff > 0:
                                        boost_win_count += 1
                                        print(f"[BOT] 💰 BOOST ОКУПИВСЯ: +{mass_diff:.0f} маси (імпульс {time_boosting:.2f}с)")
                                    else:
                                        boost_loss_count += 1
                                        print(f"[BOT] 📉 BOOST В МІНУС: {mass_diff:.0f} маси (імпульс {time_boosting:.2f}с)")
                                    
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
                                    # Каскадний буст тільки після плюсового ривка; після мінусового — штраф 3.5с
                                    cd_sec = 0.75 if (mass_diff > 0 and cluster_mass >= 60 and min_enemy_dist > 240) else (3.5 if mass_diff <= 0 else 2.2)
                                    boost_cooldown_until = now + cd_sec
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
                    mass_sum_accum += me_mass
                    phase_val = decision.get("phase", 1) if decision else 1
                    
                    if phase_val != last_phase:
                        print(f"[BOT] 📈 Фаза змінена: {last_phase} ➔ {phase_val} (маса: {me_mass:.0f})")
                        last_phase = phase_val
                    
                    if cluster_mass > 80 and cluster_dist > 400 and cluster_mass > max_cluster_seen:
                        print(f"[BOT] 🥩 МЕГА-КЛАСТЕР: {cluster_mass:.0f} маси на {cluster_dist:.0f}px (Ворог: {min_enemy_dist:.0f}px)")
                        max_cluster_seen = cluster_mass
                        
                    if time.time() - last_mass_time > 10:
                        mass_loss = last_mass_val - me_mass
                        if mass_loss > 100 and last_mass_val > 0:
                            starvation_events += 1
                            print(f"[BOT] ⏳ ГОЛОДУВАННЯ: спад маси {last_mass_val:.0f} ➔ {me_mass:.0f} за 10с (-{mass_loss/10:.1f} mass/s)")
                        last_mass_val = me_mass
                        last_mass_time = time.time()
                    avoid_r_val = decision.get("avoid_radius", 220.0) if decision else 220.0
                    food_w_val = decision.get("food_weight", 1.0) if decision else 1.0
                    cluster_w_val = decision.get("cluster_weight", 2.0) if decision else 2.0

                    if DRY_RUN and (ticks % 10 == 0):
                        print(f"[DRY] mass={me_mass:.0f} phase={phase_val} avoidR={avoid_r_val:.0f} foodW={food_w_val:.1f} clusterW={cluster_w_val:.1f}")

                    if ticks % 40 == 0:
                        alive_time = int(time.time() - start_time)
                        boost_tag = " [BOOST]" if boost_active else ""
                        print(f"[BOT] Арена: {alive_time}с/{max_duration}с | Монети: {score_earned} | З'їдено: {kills_earned} | Маса: {me_mass:.0f}{boost_tag} | Фаза {phase_val} (avoidR {avoid_r_val:.0f}) | Boosts: {boost_triggers_count} (block B:{path_safe_blocked_count}/S:{path_safe_steering_blocked_count})")

                    time.sleep(0.06)

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
                avg_run_mass = (mass_sum_accum / ticks) if ticks > 0 else 0.0
                print(f"[BOT] Забіг #{run_idx} тривав {duration_sec} сек.")
                print(f"      Очки: +{score_earned} | З'їдено змій: {kills_earned} | Активацій boost: {boost_triggers_count} | Raycast ухилень: {raycast_panics_count} | isPathSafe блоковано (boost/steering): {path_safe_blocked_count}/{path_safe_steering_blocked_count} | coreFx активностей: {core_fx_activations} | Макс. кластер: {max_cluster_seen:.0f}")
                print(f"      [TELEMETRY] Середня маса: {avg_run_mass:.0f} | Темп: {score_earned / max(1, duration_sec):.2f} монет/с | Kill-монети (миттєві): +{kill_coins_est} | Boost (+/-): {boost_win_count}/{boost_loss_count} (net {boost_net_mass:+.0f} маси) | Голодувань: {starvation_events}")

                # 8. Пауза 6 секунд, щоб екран смерті #cs-dead та сервер повністю синхронізувалися
                print("[BOT] Очікуємо синхронізацію результату із сервером (6 сек)...")
                try:
                    sb.sleep(6)
                except Exception:
                    pass

                # 9. Зчитуємо реальні дані з екрана смерті, localStorage та рейтингу (із захистом від закритого вікна)
                try:
                    stats = safe_eval(sb, """
                    return (() => {
                        var deadCoins = document.querySelector('#cs-d-coins')?.innerText;
                        var deadKills = document.querySelector('#cs-d-kills')?.innerText;
                        var deadTime  = document.querySelector('#cs-d-time')?.innerText;
                        var deadRank  = document.querySelector('#cs-d-rank')?.innerText;

                        // Рекорд сезону (#1 podium)
                        var p1 = document.querySelector('.cs-pod[data-p="1"]');
                        var seasonRecord = 0;
                        if (p1) {
                            var clone = p1.querySelector('.vl') ? p1.querySelector('.vl').cloneNode(true) : p1.cloneNode(true);
                            clone.querySelectorAll('.cs-bonus-tag, small, i').forEach(el => el.remove());
                            seasonRecord = parseInt(clone.innerText.replace(/[^0-9]/g, '')) || 0;
                        }
                        if (!seasonRecord) {
                            var topPtsEl = document.querySelector('#cs-podium .cs-pod[data-p="1"] .cs-pod-pts') || document.querySelector('.cs-pod-pts');
                            seasonRecord = topPtsEl ? parseInt(topPtsEl.innerText.replace(/[^0-9]/g, '')) : 0;
                        }
                        if (!seasonRecord || seasonRecord < 50000) seasonRecord = 136162;

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
                        var myNick = (acc && acc.nick) || 'newagno';
                        var storageKey = 'cryptosnake-io-v1:wp:' + wp.userId;
                        var localData = null;
                        try {
                            localData = JSON.parse(localStorage.getItem(storageKey));
                        } catch(e) {}

                        var runs = localData && localData.runs ? localData.runs : [];
                        var allCoins = runs.map(r => r.coins || 0);
                        var bestScore = allCoins.length ? Math.max(...allCoins) : 0;
                        var totalCoins = allCoins.reduce((a, b) => a + b, 0);

                        // Соло-рейтинг користувача (багаторівневий пошук: .me -> нік у топ-20 -> подіум -> розрахунок за балами)
                        var soloRankRaw = 
                            document.querySelector('#cs-solo .cs-prow.me .rk')?.innerText
                            || document.querySelector('.cs-prow.me .rk')?.innerText
                            || document.querySelector('[data-me="1"] .rk')?.innerText
                            || null;

                        if (!soloRankRaw) {
                            var rows = Array.from(document.querySelectorAll('.cs-prow'));
                            for (var r of rows) {
                                var nk = r.querySelector('.nk')?.innerText || '';
                                if (nk.trim().toLowerCase() === myNick.toLowerCase()) {
                                    soloRankRaw = r.querySelector('.rk')?.innerText || null;
                                    break;
                                }
                            }
                        }

                        if (!soloRankRaw) {
                            var pods = Array.from(document.querySelectorAll('.cs-pod'));
                            for (var pod of pods) {
                                var nk = pod.querySelector('.nk')?.innerText || '';
                                if (nk.trim().toLowerCase() === myNick.toLowerCase()) {
                                    soloRankRaw = pod.getAttribute('data-p') || null;
                                    break;
                                }
                            }
                        }

                        var currentRunCoins = deadCoins ? parseInt(String(deadCoins).replace(/[^0-9]/g, '')) : 0;
                        var myEffectiveBest = Math.max(bestScore, currentRunCoins);
                        if (!soloRankRaw && myEffectiveBest > 0) {
                            var allScores = [];
                            var pEls = Array.from(document.querySelectorAll('.cs-pod'));
                            for (var pod of pEls) {
                                var pNum = parseInt(pod.getAttribute('data-p')) || 0;
                                var clone = pod.querySelector('.vl') ? pod.querySelector('.vl').cloneNode(true) : pod.cloneNode(true);
                                clone.querySelectorAll('.cs-bonus-tag, small, i').forEach(el => el.remove());
                                var pts = parseInt(clone.innerText.replace(/[^0-9]/g, '')) || 0;
                                if (pNum && pts) allScores.push({ rank: pNum, pts: pts });
                            }
                            var rEls = Array.from(document.querySelectorAll('.cs-prow'));
                            for (var r of rEls) {
                                var rk = parseInt(r.querySelector('.rk')?.innerText.replace(/[^0-9]/g, '')) || 0;
                                var clone = r.querySelector('.vl') ? r.querySelector('.vl').cloneNode(true) : r.cloneNode(true);
                                clone.querySelectorAll('.cs-bonus-tag, small, i').forEach(el => el.remove());
                                var pts = parseInt(clone.innerText.replace(/[^0-9]/g, '')) || 0;
                                if (rk && pts) allScores.push({ rank: rk, pts: pts });
                            }
                            allScores.sort((a, b) => b.pts - a.pts);
                            if (allScores.length > 0) {
                                var betterCount = allScores.filter(s => s.pts > myEffectiveBest).length;
                                if (betterCount < 20) {
                                    soloRankRaw = String(betterCount + 1);
                                }
                            }
                        }

                        var deadRankRaw = document.querySelector('#cs-d-rank')?.innerText || null;

                        return {
                            nick: myNick,
                            season_record: seasonRecord,
                            solo_rank: soloRankRaw,
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
                                solo_rank: soloRankRaw,
                                dead_rank: deadRankRaw
                            }
                        };
                    })()
                    """) or {}
                except Exception as e_stats:
                    print(f"[BOT] ⚠️ Вікно браузера недоступне для зчитування фінальної таблиці ({e_stats}). Формуємо звіт із телеметрії раунду.")
                    stats = {}

                # Якщо екран смерті містить верифіковану кількість монет, беремо її
                dead_data = stats.get("dead") or {}
                final_coins = dead_data.get("coins")
                if final_coins and any(c.isdigit() for c in str(final_coins)):
                    score_earned = int("".join(c for c in str(final_coins) if c.isdigit()))

                final_kills = dead_data.get("kills")
                if final_kills and any(c.isdigit() for c in str(final_kills)):
                    kills_earned = int("".join(c for c in str(final_kills) if c.isdigit()))

                nick = stats.get("nick", "newagno")
                season_record = stats.get("season_record") or 136162
                if score_earned > season_record:
                    delta_record = score_earned - season_record
                    rec_line = f"🏆 <b>НОВИЙ РЕКОРД СЕЗОНУ!</b> Перевершено топ-1 на +{delta_record:,} монет!"
                else:
                    delta_record = season_record - score_earned
                    rec_line = f"🎯 До рекорду топ-1 сезону ({season_record:,}): <b>ще {delta_record:,} монет</b>"

                my_guild = stats.get("my_guild") or {}
                guild_pts = my_guild.get("pts") or "—"
                guild_place = my_guild.get("place", 3)
                guild_sub = my_guild.get("sub", "")

                history = stats.get("history") or {}
                best_score = max(score_earned, history.get("best_score", score_earned))
                total_coins = history.get("total_coins", score_earned)

                import re as _re
                def _extract_rank_int(text):
                    if not text:
                        return None
                    m = _re.search(r'\d+', str(text))
                    return int(m.group()) if m else None

                solo_rank_raw = stats.get("solo_rank") or history.get("solo_rank")
                dead_rank_raw = stats.get("dead_rank") or (stats.get("dead") or {}).get("rank")
                season_rank_num = _extract_rank_int(solo_rank_raw)
                round_rank_num  = _extract_rank_int(dead_rank_raw)

                if season_rank_num:
                    rank_str = f"#{season_rank_num} (сезон)"
                elif round_rank_num:
                    rank_str = f"Поза топ-20 сезону (місце в забігу: #{round_rank_num})"
                else:
                    rank_str = "Поза топ-20 сезону"

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

                is_spawn_death = duration_sec < 45 and not DRY_RUN and attempts < max_attempts
                if not is_spawn_death:
                    print("[BOT] Надсилаємо оновлений звіт у Telegram...")
                    send_telegram_message(tg_message)
                else:
                    print(f"[BOT] ⚠️ Спавн-смерть на {duration_sec}с (<45с). Пропускаємо Telegram і перезапускаємо спробу {attempts+1} через меню...")
                    try:
                        safe_eval(sb, """
                            var changeBtn = document.querySelector('#cs-change') || document.querySelector('#cs-again');
                            if (changeBtn && !changeBtn.hidden) changeBtn.click();
                        """)
                        sb.sleep(2)
                    except Exception:
                        pass
                    continue

                run_idx += 1
                if run_idx <= max_runs:
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
