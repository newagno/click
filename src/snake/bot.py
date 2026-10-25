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
        return f"{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return s if s else None



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
            "https://1.1.1.1",
            proxies=proxies,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        return resp.status_code in (200, 301, 302, 403)
    except Exception as e:
        print(f"[PROXY] Проксі не пройшов перевірку через requests: {e}")
        return False


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
        driver.execute_cdp_cmd("Network.setCookie", cdp_cookie)
        return True
    except Exception:
        pass

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
            title = raw_title.lower().strip()
            # Успіх тільки коли заголовок НЕ порожній і НЕ Cloudflare challenge!
            if title and "just a moment" not in title and "cloudflare" not in title and "moment..." not in title:
                print(f"[CF] Перевірку Cloudflare пройдено (заголовок: '{raw_title}')!")
                return True
        except Exception:
            pass

        print(f"[CF] Очікуємо завантаження сторінки / Turnstile ({waited}s / {deadline}s)...")
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            pass

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
        title = raw_title.lower().strip()
        return bool(title and "just a moment" not in title and "cloudflare" not in title and "moment..." not in title)
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
    if not cur_title or not cur_url or "incrypted.com" not in cur_url:
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
        return True


    # 3. Якщо куків немає, виконуємо вхід за email + password
    if email and password:
        print("[AUTH] Куки відсутні. Виконуємо вхід за логіном/паролем...")
        try:
            sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", reconnect_time=4)
            sb.sleep(4)
            try:
                sb.wait_for_element_visible("#llms_login", timeout=12)
                sb.type("#llms_login", email)
                sb.type("#llms_password", password)
                sb.sleep(1)
                sb.click("#llms_login_button")
                sb.sleep(6)
            except Exception as ex_form:
                print(f"[AUTH] Форма на /ua/account/ недоступна: {ex_form}")

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

    proxy_raw = os.getenv("RESIDENTIAL_PROXY", "")
    proxy_clean = format_proxy(proxy_raw)

    sb_kwargs = dict(
        uc=True,
        headless=headless,
        binary_location=binary
    )

    if proxy_clean:
        print("[BOT] Перевірка доступності проксі-сервера...")
        if is_proxy_working(proxy_raw, timeout=7):
            sb_kwargs["proxy"] = proxy_clean
            host_display = proxy_clean.split("@")[-1]
            print(f"[BOT] Проксі перевірено та активовано: {host_display}")
        else:
            print("[BOT] ⚠️ Проксі не відповідає або облікові дані недійсні! Працюємо напряму без проксі.")

    print("=" * 60)
    print(f"[BOT] Запуск інтелектуального бота CryptoSnake")
    print(f"      Цільова команда : NEAR (бонус ×2)")
    print(f"      Режим зору      : Векторний радар рушія + нативне CDP-керування")
    print(f"      Кількість ігор  : {max_runs}")
    print(f"      Браузер         : {binary or 'Системний'}")
    print("=" * 60)

    with SB(**sb_kwargs) as sb:
        ensure_authenticated(sb)

        print(f"\n[BOT] Відкриваємо гру {GAME_URL}...")
        sb.uc_open_with_reconnect(GAME_URL, reconnect_time=4)
        sb.sleep(4)

        # Перевірка наявності canvas гри та вирішення Cloudflare за потреби
        if not wait_for_bypass(sb, target_url=GAME_URL, timeout=45):
            print("[BOT] ⚠️ Попередження: сторінка гри все ще може перебувати під перевіркою Cloudflare.")

        for _ in range(8):
            has_canvas = sb.execute_script("return !!document.querySelector('#cs-canvas');")
            if has_canvas:
                break
            sb.sleep(2)

        # Перевірка стану акаунта в грі
        account_status = sb.execute_script("""
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
                    sb.execute_script("document.querySelector('#cs-start')?.click();")
                    sb.sleep(2)
                    if sb.is_element_visible(".incr-popup-login"):
                        sb.type(".incr-popup-login input[type=email], #llms_login", email)
                        sb.type(".incr-popup-login input[type=password], #llms_password", password)
                        sb.sleep(1)
                        sb.click(".incr-popup-login #llms_login_button, #llms_login_button")
                        sb.sleep(5)
                        account_status = sb.execute_script("""
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

        # ── Впроваджуємо радарний перехоплювач світу ─────────────────
        inj_res = sb.execute_script("""
            return (() => {
                if (!window.CryptoSnakeCore || !window.CryptoSnakeCore.World) return 'NO_CORE';
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
                return 'OK';
            })()
        """)
        print(f"[BOT] Ін'єкція радарного перехоплювача: {inj_res}")
        sb.sleep(1)

        for run_idx in range(1, max_runs + 1):
            print(f"\n{'='*25} РАУНД #{run_idx} із {max_runs} {'='*25}")

            # Перевірка чи вікно все ще відкрите
            try:
                sb.execute_script("return !!document.body;")
            except Exception as e_win:
                print(f"[BOT] Вікно браузера закрите або не відповідає ({e_win}). Зупинка сесії.")
                break

            # 1. Якщо раунд > 1: повертаємося в головне меню через кнопку #cs-change
            if run_idx > 1:
                try:
                    sb.execute_script("""
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
                selected = sb.execute_script("""
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
                btn_ready = sb.execute_script("""
                    var btn = document.querySelector('#cs-start');
                    return btn && !btn.disabled;
                """)
                if btn_ready:
                    break
                sb.sleep(1)

            # 4. Тиснемо кнопку старту
            btn_info = sb.execute_script("""
                var btn = document.querySelector('#cs-start');
                return { text: btn?.innerText, disabled: btn?.disabled };
            """) or {}
            print(f"[BOT] Кнопка старту: '{btn_info.get('text')}' (disabled={btn_info.get('disabled')}). Тиснемо...")
            sb.execute_script("document.querySelector('#cs-start')?.click();")
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
                    sb.execute_script("""
                        document.querySelector('.cs-char.cs-hero-near')?.click();
                        document.querySelector('#cs-start')?.click();
                    """)
                    sb.sleep(2)
                except Exception as e_pop:
                    print(f"[BOT] Помилка входу через попап: {e_pop}")

            # 6. Очікуємо, поки гра ДІЙСНО перейде в стан бою (HUD активний)
            print("[BOT] Очікуємо активацію арени (поява HUD)...")
            game_active = False
            for check_i in range(25):
                hud_active = sb.execute_script("""
                    var hud = document.querySelector('#cs-hud');
                    return hud && !hud.hidden;
                """)
                if hud_active:
                    game_active = True
                    break

                # Перевіряємо статус попередження (кулдаун попереднього забігу)
                status_text = sb.execute_script("""
                    return (document.querySelector('#cs-season-status')?.innerText ||
                            document.querySelector('#cs-run-warning')?.innerText || '');
                """)
                if "активний забіг" in status_text.lower():
                    print(f"[BOT] ⏳ Сервер утримує сесію попереднього забігу. Очікуємо завершення кулдауну ({check_i+1}/25)...")
                    sb.sleep(4)
                    sb.execute_script("""
                        var btn = document.querySelector('#cs-start');
                        if (btn && !btn.disabled) { btn.click(); }
                    """)
                    continue

                # Якщо з'явилося попередження або кнопка старту все ще активна, пробуємо натиснути ще раз
                if check_i in (3, 7):
                    sb.execute_script("""
                        var btn = document.querySelector('#cs-start');
                        if (btn && !btn.disabled) { btn.click(); }
                    """)
                sb.sleep(1)

            if not game_active:
                diag = sb.execute_script("""
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

            # 7. Отримуємо розміри Canvas
            canvas_rect = sb.execute_script("""
                return (() => {
                    var c = document.querySelector('#cs-canvas');
                    if (!c) return null;
                    var r = c.getBoundingClientRect();
                    return { x: r.left, y: r.top, w: r.width, h: r.height };
                })()
            """)

            cx = int(canvas_rect['x'] + canvas_rect['w'] / 2) if canvas_rect else 600
            cy = int(canvas_rect['y'] + canvas_rect['h'] / 2) if canvas_rect else 350

            print("[BOT] Змійка на полі! Активуємо AI-радар монет NEAR та автоухилення...")
            start_time = time.time()
            score_earned = 0
            kills_earned = 0
            ticks = 0
            max_duration = 600  # До 10 хвилин виживання

            # Забіг триває доти, доки змійка жива (або максимум 10 хвилин)
            while (time.time() - start_time) < max_duration:
                try:
                    decision = sb.execute_script("""
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
                                return { is_dead: isDead, coins: currentCoins, len: currentLen, kills: currentKills, angle: null };
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
                                return { is_dead: true, coins: currentCoins, len: currentLen, kills: currentKills, angle: null };
                            }

                            currentCoins = String(Math.floor(me.coins || 0));
                            currentLen   = String(Math.round(me.mass || 0));
                            currentKills = String(me.kills || 0);

                            var hx = me.pts[0];
                            var hy = me.pts[1];

                            // 1. Пошук найближчих монет з пріоритетом NEAR (×3.0)
                            var foodFx = 0;
                            var foodFy = 0;
                            if (w.food && w.food.length > 0) {
                                for (var f of w.food) {
                                    if (f.dead) continue;
                                    var dx = f.x - hx;
                                    var dy = f.y - hy;
                                    var dist = Math.hypot(dx, dy);
                                    if (dist > 650 || dist < 1) continue;

                                    var weight = f.type === 'near' ? 3.0 : 1.0;
                                    var force = weight / (dist * dist);
                                    foodFx += (dx / dist) * force;
                                    foodFy += (dy / dist) * force;
                                }
                            }

                            // 2. Ухилення від ворожих змій з підвищеною зоною безпеки
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

                                    // Збільшена зона безпеки (220px) та підвищена небезпека від голови ворога
                                    if (odist < 220 && odist > 1) {
                                        var headMultiplier = (p === 0) ? 2.5 : 1.0;
                                        var repForce = (90.0 * headMultiplier) / (odist * odist);
                                        avoidFx += ((hx - ox) / odist) * repForce;
                                        avoidFy += ((hy - oy) / odist) * repForce;
                                    }
                                }
                            }

                            // 3. Відштовхування від меж арени
                            var wallFx = 0;
                            var wallFy = 0;
                            var distFromCenter = Math.hypot(hx, hy);
                            var safeRadius = (w.radius || 2000) - 250;
                            if (distFromCenter > safeRadius) {
                                var wallForce = ((distFromCenter - safeRadius) / 250) * 20.0;
                                wallFx = -(hx / distFromCenter) * wallForce;
                                wallFy = -(hy / distFromCenter) * wallForce;
                            }

                            // При наближенні ворога (<180px) пригнічуємо жадібність до монет заради виживання
                            var foodWeight = minEnemyDist < 180 ? 0.2 : 1.0;

                            var finalX = foodFx * foodWeight + avoidFx * 5.0 + wallFx * 7.0;
                            var finalY = foodFy * foodWeight + avoidFy * 5.0 + wallFy * 7.0;
                            var targetAngle = Math.atan2(finalY, finalX);

                            return {
                                is_dead: isDead,
                                coins: currentCoins,
                                len: currentLen,
                                kills: currentKills,
                                angle: targetAngle
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

                except Exception as ex:
                    print(f"[BOT] Помилка циклу: {ex}")
                    break

                ticks += 1
                if ticks % 25 == 0:
                    alive_time = int(time.time() - start_time)
                    print(f"[BOT] Арена: {alive_time}с | Монети: {score_earned} | З'їдено: {kills_earned}")

                time.sleep(0.12)

            duration_sec = int(time.time() - start_time)
            print(f"[BOT] Забіг #{run_idx} тривав {duration_sec} сек.")
            print(f"      Очки: +{score_earned} | З'їдено змій: {kills_earned}")

            # 8. Пауза 6 секунд, щоб екран смерті #cs-dead та сервер повністю синхронізувалися
            print("[BOT] Очікуємо синхронізацію результату із сервером (6 сек)...")
            sb.sleep(6)

            # 9. Зчитуємо реальні дані з екрана смерті, localStorage та рейтингу
            stats = sb.execute_script("""
                return (() => {
                    var deadCoins = document.querySelector('#cs-d-coins')?.innerText;
                    var deadKills = document.querySelector('#cs-d-kills')?.innerText;
                    var deadTime  = document.querySelector('#cs-d-time')?.innerText;
                    var deadRank  = document.querySelector('#cs-d-rank')?.innerText;

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
            my_guild = stats.get("my_guild") or {}
            guild_pts = my_guild.get("pts") or "—"
            guild_place = my_guild.get("place", 3)
            guild_sub = my_guild.get("sub", "")

            history = stats.get("history") or {}
            best_score = max(score_earned, history.get("best_score", score_earned))
            total_coins = history.get("total_coins", score_earned)
            solo_rank = history.get("solo_rank")
            rank_str = f"#{solo_rank}" if solo_rank else "поза топ-20"

            place_medal = "🥇" if guild_place == 1 else ("🥈" if guild_place == 2 else "🥉")

            # 10. Формуємо детальний звіт у Telegram
            tg_message = (
                f"🐍 <b>CryptoSnake — Забіг завершено! (#{run_idx} із {max_runs})</b>\n\n"
                f"🎯 <b>Результат цієї гри:</b>\n"
                f"• Набрано очок: <b>+{score_earned} монет</b> (NEAR ×2)\n"
                f"• З'їдено змій: <b>{kills_earned}</b>\n"
                f"• Час на арені: <b>{duration_sec // 60} хв {duration_sec % 60} сек</b>\n\n"
                f"👤 <b>Твій профіль ({nick}):</b>\n"
                f"• Загалом за весь час: <b>{total_coins:,} монет</b>\n"
                f"• Найкращий забіг: <b>{best_score} монет</b>\n"
                f"• Місце в рейтингу: <b>{rank_str}</b>\n\n"
                f"🟢 <b>Команда NEAR:</b>\n"
                f"• Очки гільдії: <b>{guild_pts} монет</b>\n"
                f"• Місце команди: <b>{guild_place}-е місце {place_medal}</b>\n"
                f"• Склад: <i>{guild_sub}</i>\n\n"
                f"🤖 <i>Режим: AI Векторний радар + нативне ухилення CDP</i>"
            )

            print("[BOT] Надсилаємо оновлений звіт у Telegram...")
            send_telegram_message(tg_message)

            # 11. Пауза між раундами
            if run_idx < max_runs:
                print("[BOT] Пауза 5 секунд перед наступним забігом...")
                sb.sleep(5)

        print("\n[BOT] Усі заплановані забіги сесії успішно виконано!")


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

    runs = 3
    if len(sys.argv) > 1:
        try:
            runs = int(sys.argv[1])
        except ValueError:
            pass
    run_snake_bot(max_runs=runs)
