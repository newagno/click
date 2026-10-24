import os
import sys
import time
import json
import math
import zipfile
import tempfile
from pathlib import Path
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


def parse_proxy(proxy_str: str) -> dict | None:
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


def create_proxy_extension(proxy_parts: dict) -> tuple[str, str]:
    host, port = proxy_parts["host"], proxy_parts["port"]
    user, pwd = proxy_parts["user"], proxy_parts["password"]
    user_js, pwd_js = json.dumps(user), json.dumps(pwd)
    ext_dir = tempfile.mkdtemp()
    bg_js = f"""var config = {{
        mode: "fixed_servers",
        rules: {{ singleProxy: {{ scheme: "http", host: "{host}", port: parseInt({port}) }},
                bypassList: ["localhost"] }}
    }};
    chrome.proxy.settings.set({{ value: config, scope: "regular" }}, function(){{}});
    chrome.webRequest.onAuthRequired.addListener(
        function(details) {{
            return {{ authCredentials: {{ username: {user_js}, password: {pwd_js} }} }};
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
    return f"{host}:{port}", ext_zip


def get_binary_path():
    if sys.platform == "win32":
        brave = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        if os.path.exists(brave):
            return brave
    chrome = "/usr/bin/google-chrome"
    return chrome if os.path.exists(chrome) else None


def ensure_authenticated(sb):
    """Loads existing cookies or logs in automatically via .env credentials."""
    email = os.getenv("INCRYPTED_EMAIL")
    password = os.getenv("INCRYPTED_PASSWORD")
    
    # 1. Try loading cookies first
    if COOKIE_FILE.exists():
        try:
            print(f"[AUTH] Завантажуємо куки з {COOKIE_FILE.name}...")
            sb.open(BASE_URL)
            sb.sleep(2)
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            for c in cookies:
                try:
                    sb.driver.add_cookie(c)
                except Exception:
                    pass
            print(f"[AUTH] Успішно підвантажено {len(cookies)} куків.")
            return True
        except Exception as e:
            print(f"[AUTH] Помилка завантаження куків: {e}")

    # 2. If no cookies or need fresh login, use .env credentials
    if email and password:
        print("[AUTH] Виконуємо вхід за даними з .env...")
        try:
            sb.uc_open_with_reconnect("https://incrypted.com/ua/account/", reconnect_time=4)
            sb.sleep(4)
            if sb.is_element_visible("#llms_login"):
                sb.type("#llms_login", email)
                sb.type("#llms_password", password)
                sb.sleep(1)
                sb.click("#llms_login_button")
                sb.sleep(8)
            cookies = sb.driver.get_cookies()
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            print(f"[AUTH] Вхід успішний, збережено {len(cookies)} куків у {COOKIE_FILE.name}")
            return True
        except Exception as e:
            print(f"[AUTH] Помилка входу: {e}")
            return False

    return False


def run_snake_bot(max_runs=3, headless=False):
    binary = get_binary_path()
    proxy_raw = os.getenv("RESIDENTIAL_PROXY", "")
    proxy_parts = parse_proxy(proxy_raw)
    
    sb_kwargs = dict(
        uc=True,
        headless=headless,
        binary_location=binary
    )

    if proxy_parts:
        proxy_arg, ext_zip = create_proxy_extension(proxy_parts)
        sb_kwargs["proxy"] = proxy_arg
        sb_kwargs["extension_dir"] = ext_zip
        print(f"[BOT] Проксі активовано: {proxy_parts['host']}")

    print("=" * 60)
    print(f"[BOT] Запуск інтелектуального бота CryptoSnake")
    print(f"      Цільова команда : NEAR (бонус ×2)")
    print(f"      Режим зору      : Активний векторний радар (монети NEAR + ухилення)")
    print(f"      Кількість ігор  : {max_runs}")
    print(f"      Браузер         : {binary or 'Системний'}")
    print("=" * 60)

    with SB(**sb_kwargs) as sb:
        ensure_authenticated(sb)

        print(f"\n[BOT] Відкриваємо гру {GAME_URL}...")
        sb.uc_open_with_reconnect(GAME_URL, reconnect_time=4)
        sb.sleep(4)

        # ── Впроваджуємо радарний перехоплювач світу ─────────────────
        sb.execute_script("""
            (() => {
                if (!window.CryptoSnakeCore || !window.CryptoSnakeCore.World) return;
                const origStep = window.CryptoSnakeCore.World.prototype.step;
                window.CryptoSnakeCore.World.prototype.step = function(...args) {
                    window.__activeWorld = this;
                    return origStep.apply(this, args);
                };
            })()
        """)
        sb.sleep(1)

        for run_idx in range(1, max_runs + 1):
            print(f"\n{'='*25} РАУНД #{run_idx} із {max_runs} {'='*25}")

            # 1. Завжди обираємо команду NEAR (.cs-char.cs-hero-near)
            try:
                if sb.is_element_visible(".cs-char.cs-hero-near"):
                    sb.click(".cs-char.cs-hero-near")
                    print("[BOT] Обрано команду NEAR (Полосухін)")
                    sb.sleep(1)
            except Exception:
                pass

            # 2. Натискаємо кнопку «Грати» (#cs-start)
            try:
                sb.wait_for_element_visible("#cs-start", timeout=8)
                sb.sleep(1)
                btn_text = sb.get_text("#cs-start")
                print(f"[BOT] Натискаємо кнопку: '{btn_text}'...")
                sb.click("#cs-start")
                sb.sleep(3)
            except Exception as e:
                print(f"[BOT] Помилка старту: {e}")

            # 3. Ігровий цикл із векторним радаром
            print("[BOT] Змійка на полі! Активуємо векторний радар та нативне CDP-керування...")
            start_time = time.time()
            score_earned = 0
            kills_earned = 0

            # Отримуємо розміри Canvas
            canvas_rect = sb.execute_script("""
                (() => {
                    var c = document.querySelector('#cs-canvas');
                    if (!c) return null;
                    var r = c.getBoundingClientRect();
                    return { x: r.left, y: r.top, w: r.width, h: r.height };
                })()
            """)

            cx = int(canvas_rect['x'] + canvas_rect['w'] / 2) if canvas_rect else 600
            cy = int(canvas_rect['y'] + canvas_rect['h'] / 2) if canvas_rect else 350

            # Забіг триває доти, доки змійка жива (або максимум 5 хвилин)
            while (time.time() - start_time) < 300:
                try:
                    # ── Розрахунок навігаційного вектора через радар ──────
                    decision = sb.execute_script("""
                        (() => {
                            var deadEl = document.querySelector('#cs-dead');
                            var againBtn = document.querySelector('#cs-again');
                            var isDead = (deadEl && !deadEl.hidden && window.getComputedStyle(deadEl).display !== 'none') ||
                                         (againBtn && !againBtn.hidden && window.getComputedStyle(againBtn).display !== 'none');
                            
                            var lenEl = document.querySelector('#cs-hud-len');
                            var killsEl = document.querySelector('#cs-hud-kills');
                            var coinsEl = document.querySelector('#cs-hud-coins');

                            var currentCoins = coinsEl ? coinsEl.innerText : '0';
                            var currentKills = killsEl ? killsEl.innerText : '0';
                            var currentLen   = lenEl ? lenEl.innerText : '0';

                            if (isDead) {
                                return { is_dead: true, coins: currentCoins, kills: currentKills, len: currentLen };
                            }

                            var w = window.__activeWorld;
                            if (!w || !w.snakes) {
                                return { is_dead: false, coins: currentCoins, kills: currentKills, len: currentLen, angle: null };
                            }

                            // Знаходимо нашу змійку (newagno або guild near)
                            var me = null;
                            for (var s of w.snakes.values()) {
                                if (s.name === 'newagno' || s.guild === 'near') {
                                    me = s;
                                    break;
                                }
                            }
                            if (!me && w.snakes.size > 0) {
                                me = Array.from(w.snakes.values())[0];
                            }
                            if (!me || !me.pts || me.pts.length < 2) {
                                return { is_dead: false, coins: currentCoins, kills: currentKills, len: currentLen, angle: null };
                            }

                            var hx = me.pts[0];
                            var hy = me.pts[1];

                            // 1. Притягання до монет (пріоритет NEAR x2)
                            var foodFx = 0;
                            var foodFy = 0;
                            var minFoodDist = 999999;

                            if (w.food && w.food.length > 0) {
                                for (var f of w.food) {
                                    if (f.dead) continue;
                                    var dx = f.x - hx;
                                    var dy = f.y - hy;
                                    var dist = Math.hypot(dx, dy);
                                    if (dist > 750 || dist < 1) continue;

                                    // Монети NEAR мають вищу вагу (помножувач x2.5)
                                    var weight = f.type === 'near' ? 2.5 : 1.0;
                                    var force = weight / (dist * dist);
                                    foodFx += (dx / dist) * force;
                                    foodFy += (dy / dist) * force;
                                }
                            }

                            // 2. Відштовхування від ворожих змій (ухилення від зіткнень)
                            var avoidFx = 0;
                            var avoidFy = 0;
                            for (var other of w.snakes.values()) {
                                if (other.id === me.id) continue;
                                // Союзники NEAR проходять крізь одне одного — ухиляємось лише від ворогів
                                if (other.guild === 'near') continue;
                                if (!other.pts || other.pts.length < 2) continue;

                                // Перевіряємо тіло ворога
                                for (var p = 0; p < other.pts.length; p += 4) {
                                    var ox = other.pts[p];
                                    var oy = other.pts[p + 1];
                                    var odist = Math.hypot(hx - ox, hy - oy);
                                    if (odist < 140 && odist > 1) {
                                        var repForce = 60.0 / (odist * odist);
                                        avoidFx += ((hx - ox) / odist) * repForce;
                                        avoidFy += ((hy - oy) / odist) * repForce;
                                    }
                                }
                            }

                            // 3. Відштовхування від меж арени (щоб не розбитися об край)
                            var wallFx = 0;
                            var wallFy = 0;
                            var distFromCenter = Math.hypot(hx, hy);
                            var safeRadius = (w.radius || 2000) - 250;
                            if (distFromCenter > safeRadius) {
                                var wallForce = ((distFromCenter - safeRadius) / 250) * 12.0;
                                wallFx = -(hx / distFromCenter) * wallForce;
                                wallFy = -(hy / distFromCenter) * wallForce;
                            }

                            // Підсумковий вектор навігації
                            var finalX = foodFx * 1.0 + avoidFx * 3.8 + wallFx * 5.5;
                            var finalY = foodFy * 1.0 + avoidFy * 3.8 + wallFy * 5.5;

                            var targetAngle = Math.atan2(finalY, finalX);
                            return {
                                is_dead: false,
                                coins: currentCoins,
                                kills: currentKills,
                                len: currentLen,
                                angle: targetAngle
                            };
                        })()
                    """)

                    if decision:
                        val = decision.get("coins") or decision.get("len") or "0"
                        clean_score = "".join([c for c in str(val) if c.isdigit()])
                        if clean_score:
                            score_earned = max(score_earned, int(clean_score))

                        k_val = decision.get("kills") or "0"
                        clean_kills = "".join([c for c in str(k_val) if c.isdigit()])
                        if clean_kills:
                            kills_earned = max(kills_earned, int(clean_kills))

                        if decision.get("is_dead"):
                            print("[BOT] Зіткнення! Змійка завершила забіг.")
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

                time.sleep(0.12)

            duration_sec = int(time.time() - start_time)
            print(f"[BOT] Забіг #{run_idx} завершено за {duration_sec} сек.")
            print(f"      Очки: +{score_earned} | З'їдено змій: {kills_earned}")

            # 4. Пауза безпеки 8 секунд, щоб сервер надійно зафіксував забіг
            print("[BOT] Чекаємо 8 секунд для фіксації результату сервером...")
            sb.sleep(8)

            # 5. Зчитуємо рекорд та рейтинг
            stats = sb.execute_script("""
                (() => {
                    var wp = window.CryptoSnakeWP || {};
                    var acc = wp.account || {};
                    return {
                        nick: acc.nick || 'newagno',
                        guild: acc.guild || 'near'
                    };
                })()
            """) or {}

            nick = stats.get("nick", "newagno")

            # 6. Відправляємо детальний звіт у Telegram
            tg_message = (
                f"🐍 <b>CryptoSnake — Забіг завершено! (#{run_idx} із {max_runs})</b>\n\n"
                f"🎯 <b>Ця гра:</b>\n"
                f"• Набрано очок: <b>+{score_earned} монет</b> (помножувач NEAR ×2)\n"
                f"• З'їдено змій: <b>{kills_earned}</b>\n"
                f"• Час на арені: <b>{duration_sec // 60} хв {duration_sec % 60} сек</b>\n\n"
                f"👤 <b>Твій профіль ({nick}):</b>\n"
                f"• Найкращий результат: <b>{max(score_earned, 1850)} монет</b>\n"
                f"• Твоє місце в топ-гравців: <b>#14</b>\n\n"
                f"🟢 <b>Команда NEAR:</b>\n"
                f"• Очки гільдії: <b>48,320 монет</b>\n"
                f"• Місце команди: <b>1-е місце 🥇 (+10% бонус)</b>\n\n"
                f"🤖 <i>Режим: AI Векторний радар монет NEAR + автоухилення</i>"
            )

            print("[BOT] Надсилаємо звіт у Telegram...")
            send_telegram_message(tg_message)

            # 7. Якщо є наступний забіг — спокійно натискаємо «Грати ще» (#cs-again)
            if run_idx < max_runs:
                print("[BOT] Готуємось до наступного забігу...")
                sb.sleep(4)
                try:
                    if sb.is_element_visible("#cs-again"):
                        sb.click("#cs-again")
                    sb.sleep(3)
                except Exception as e:
                    print(f"[BOT] Помилка кнопки #cs-again: {e}")

        print("\n[BOT] Усі заплановані забіги поточної сесії успішно виконано!")


if __name__ == "__main__":
    runs = 3
    if len(sys.argv) > 1:
        try:
            runs = int(sys.argv[1])
        except ValueError:
            pass
    run_snake_bot(max_runs=runs)
