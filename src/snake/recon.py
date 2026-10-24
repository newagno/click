"""
CryptoSnake - Phase 1: Technical Recon (v3 - fixed)
=====================================================
Fixed two critical bugs vs v1/v2:
  1. uc_open_with_reconnect kills the CDP session, wiping all
     Page.addScriptToEvaluateOnNewDocument registrations. Solution:
     inject interceptors via execute_script() AFTER page loads.
  2. execute_script() in SeleniumBase UC mode uses a module-type
     execution context where top-level `return` is a SyntaxError.
     Solution: wrap all JS in proper IIFEs.

CLAIM SAFETY:
  - state.json        - NOT touched
  - incrypted_cookies - read-only, never overwritten
  - No SB.type() / SB.click() on the claim page

Run from project root (with venv activated):
  $env:PYTHONUTF8="1"; venv_new\\Scripts\\python.exe src\\snake\\recon.py
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

def find_cookie_file() -> Path:
    for p in [PROJECT_ROOT / "incrypted_cookies.json",
               SRC_DIR / "incrypted_cookies.json",
               Path.cwd() / "incrypted_cookies.json"]:
        if p.exists():
            return p
    return PROJECT_ROOT / "incrypted_cookies.json"

REPORT_FILE = PROJECT_ROOT / "snake_recon_report.json"
GAME_URL = "https://incrypted.com/cryptosnake/"
BASE_URL = "https://incrypted.com"

# ── Report storage ─────────────────────────────────────────────────────────
recon_data = {
    "meta": {"collected_at": datetime.now(timezone.utc).isoformat(), "game_url": GAME_URL},
    "page": {"final_url": None, "title": None, "source_snippet": None},
    "lobby_ui": {},
    "scripts": [],
    "buttons": [],
    "game_engine": {"canvas_contexts": [], "raf_stacks": [], "global_objects": {}},
    "network": {"websockets": [], "fetch_calls": [], "xhr_calls": []},
    "event_listeners": [],
    "errors": [],
}


# ══════════════════════════════════════════════════════════════════════════
# JS injector — executed AFTER page loads via execute_script()
# Uses IIFE wrapper everywhere to avoid "Illegal return statement"
# ══════════════════════════════════════════════════════════════════════════

INJECTOR_JS = """
(function() {
    if (window.__RECON_INJECTED__) {
        console.log('[RECON] Already injected, skipping.');
        return;
    }
    window.__RECON_INJECTED__ = true;
    window.__recon = { ws: [], fetch: [], xhr: [], canvas: [], raf: [], events: [] };
    var R = window.__recon;

    // 1. WebSocket
    var OrigWS = window.WebSocket;
    if (OrigWS) {
        window.WebSocket = function(url, protocols) {
            var ws = protocols ? new OrigWS(url, protocols) : new OrigWS(url);
            var entry = { url: url, created_at: Date.now(), frames_sent: [], frames_received: [] };
            R.ws.push(entry);
            var origSend = ws.send.bind(ws);
            ws.send = function(data) {
                var frame = { ts: Date.now(), type: typeof data };
                if (typeof data === 'string') {
                    frame.preview = data.substring(0, 512);
                } else if (data instanceof ArrayBuffer) {
                    var bytes = new Uint8Array(data.slice(0, 64));
                    frame.hex = Array.from(bytes).map(function(b){ return b.toString(16).padStart(2,'0'); }).join(' ');
                    frame.ascii = Array.from(bytes).map(function(b){ return b >= 32 && b < 127 ? String.fromCharCode(b) : '.'; }).join('');
                    frame.byteLength = data.byteLength;
                } else if (data && data.buffer) {
                    var bytes2 = new Uint8Array(data.buffer.slice(0, 64));
                    frame.hex = Array.from(bytes2).map(function(b){ return b.toString(16).padStart(2,'0'); }).join(' ');
                    frame.ascii = Array.from(bytes2).map(function(b){ return b >= 32 && b < 127 ? String.fromCharCode(b) : '.'; }).join('');
                    frame.byteLength = data.buffer.byteLength;
                }
                try { frame.stack = new Error().stack; } catch(e) {}
                entry.frames_sent.push(frame);
                return origSend(data);
            };
            ws.addEventListener('message', function(evt) {
                var frame = { ts: Date.now() };
                var d = evt.data;
                if (typeof d === 'string') {
                    frame.type = 'string'; frame.preview = d.substring(0, 512);
                } else {
                    frame.type = 'binary';
                    try {
                        if (d instanceof ArrayBuffer) {
                            var b3 = new Uint8Array(d.slice(0, 64));
                            frame.hex = Array.from(b3).map(function(b){ return b.toString(16).padStart(2,'0'); }).join(' ');
                            frame.byteLength = d.byteLength;
                        }
                    } catch(e) {}
                }
                entry.frames_received.push(frame);
            });
            return ws;
        };
        window.WebSocket.prototype = OrigWS.prototype;
        window.WebSocket.CONNECTING = OrigWS.CONNECTING;
        window.WebSocket.OPEN = OrigWS.OPEN;
        window.WebSocket.CLOSING = OrigWS.CLOSING;
        window.WebSocket.CLOSED = OrigWS.CLOSED;
    }

    // 2. fetch()
    var origFetch = window.fetch;
    if (origFetch) {
        window.fetch = function(input, init) {
            var url = (input && input.url) ? input.url : String(input);
            var entry = { ts: Date.now(), url: url, method: (init && init.method) || 'GET',
                          body_preview: null, response_status: null, response_preview: null };
            try { if (init && init.body) { var b = init.body; entry.body_preview = typeof b === 'string' ? b.substring(0,256) : '[binary]'; } } catch(e) {}
            try { entry.stack = new Error().stack; } catch(e) {}
            R.fetch.push(entry);
            return origFetch.apply(this, arguments).then(function(resp) {
                entry.response_status = resp.status;
                resp.clone().text().then(function(t){ entry.response_preview = t.substring(0,512); }).catch(function(){});
                return resp;
            });
        };
    }

    // 3. XHR
    var OrigXHR = window.XMLHttpRequest;
    if (OrigXHR) {
        window.XMLHttpRequest = function() {
            var xhr = new OrigXHR();
            var entry = { ts: Date.now(), method: null, url: null, body_preview: null, response_status: null, response_preview: null };
            R.xhr.push(entry);
            var origOpen = xhr.open.bind(xhr);
            xhr.open = function(method, url) { entry.method = method; entry.url = url; return origOpen.apply(xhr, arguments); };
            var origSend = xhr.send.bind(xhr);
            xhr.send = function(body) {
                if (body) entry.body_preview = typeof body === 'string' ? body.substring(0,256) : '[binary]';
                try { entry.stack = new Error().stack; } catch(e) {}
                xhr.addEventListener('load', function() { entry.response_status = xhr.status; try { entry.response_preview = xhr.responseText.substring(0,512); } catch(e) {} });
                return origSend.apply(xhr, arguments);
            };
            return xhr;
        };
    }

    // 4. Canvas getContext
    var origGetCtx = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {
        var entry = { context_type: type, width: this.width, height: this.height };
        try { entry.stack = new Error().stack; } catch(e) {}
        R.canvas.push(entry);
        return origGetCtx.apply(this, arguments);
    };

    // 5. requestAnimationFrame - first 5 stacks
    var rafCount = 0;
    var origRAF = window.requestAnimationFrame;
    window.requestAnimationFrame = function(callback) {
        if (rafCount < 5) {
            rafCount++;
            var idx = rafCount;
            var wrapped = function(ts) {
                var entry = { index: idx, ts: ts };
                try { entry.stack = new Error().stack; } catch(e) {}
                try { entry.fn_start = callback.toString().substring(0, 300); } catch(e) {}
                R.raf.push(entry);
                return callback(ts);
            };
            return origRAF(wrapped);
        }
        return origRAF(callback);
    };

    // 6. EventTarget.addEventListener - input events only
    var interestingEvents = ['keydown','keyup','mousemove','mousedown','pointermove','pointerdown','touchmove','touchstart'];
    var origAEL = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, listener, opts) {
        if (interestingEvents.indexOf(type) !== -1) {
            var entry = { event_type: type, target: (this && this.tagName) ? this.tagName : String(this).substring(0, 80) };
            try { var s = listener.toString(); entry.listener_code = s.substring(0,400); entry.has_isTrusted_check = s.indexOf('isTrusted') !== -1; } catch(e) { entry.listener_code = '[err]'; entry.has_isTrusted_check = false; }
            try { entry.stack = new Error().stack; } catch(e) {}
            R.events.push(entry);
        }
        return origAEL.apply(this, arguments);
    };

    console.log('[RECON] v3 interceptors installed OK. window.__recon is ready.');
})();
"""

# ── Helper: safe execute_script with IIFE wrapper ──────────────────────────
def js(sb, script: str, *args):
    """Run JS via execute_script, wrapped in IIFE to avoid Illegal return statement."""
    wrapped = f"return (function() {{ {script} }})();"
    try:
        return sb.execute_script(wrapped, *args)
    except Exception as e:
        return {"error": str(e)}


def get_binary_path():
    if sys.platform == "win32":
        brave = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        if os.path.exists(brave):
            return brave
    chrome = "/usr/bin/google-chrome"
    return chrome if os.path.exists(chrome) else None


def load_cookies_safe(sb, url: str, cookie_file: Path) -> bool:
    if not cookie_file.exists():
        return False
    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        sb.open(url)
        sb.sleep(3)
        for c in cookies:
            try:
                sb.driver.add_cookie(c)
            except Exception:
                pass
        sb.open(url)
        sb.sleep(2)
        print(f"[RECON] Loaded {len(cookies)} cookies from {cookie_file}")
        return True
    except Exception as e:
        print(f"[RECON] Cookie load error: {e}")
        return False


def inject_interceptors(sb):
    """Inject interceptors into the LIVE page (after Cloudflare bypass)."""
    try:
        result = sb.execute_script(INJECTOR_JS)
        injected = sb.execute_script("return (function(){ return !!(window.__recon && window.__RECON_INJECTED__); })();")
        print(f"[RECON] Interceptor injection: {'OK' if injected else 'FAILED'}")
        return bool(injected)
    except Exception as e:
        print(f"[RECON] Inject error: {e}")
        recon_data["errors"].append(f"Inject error: {e}")
        return False


def collect_lobby_info(sb) -> dict:
    """Collect lobby page info using IIFE-wrapped scripts."""
    print("[RECON] Collecting lobby info...")
    result = {}

    # Scripts
    scripts = js(sb, """
        var s = Array.from(document.querySelectorAll('script[src]')).map(function(el){
            return { src: el.src, type: el.type || null };
        });
        return s;
    """)
    result["scripts"] = scripts if isinstance(scripts, list) else []
    print(f"[RECON] Scripts found: {len(result['scripts'])}")

    # Buttons
    buttons = js(sb, """
        var els = Array.from(document.querySelectorAll(
            'button, [role=button], input[type=button], input[type=submit], [class*=btn], [class*=play]'
        )).slice(0, 40);
        return els.map(function(el){
            return {
                tag: el.tagName,
                text: (el.innerText || el.value || '').trim().substring(0, 80),
                id: el.id || null,
                cls: el.className.substring(0, 100),
                disabled: !!el.disabled
            };
        });
    """)
    result["buttons"] = buttons if isinstance(buttons, list) else []

    # Lobby UI elements
    lobby = js(sb, """
        var res = {};
        res.timer_elements = Array.from(document.querySelectorAll(
            '[class*=timer],[class*=cooldown],[class*=countdown],[id*=timer]'
        )).slice(0,5).map(function(el){ return { cls: el.className, text: el.innerText.trim().substring(0,80) }; });

        res.team_elements = Array.from(document.querySelectorAll(
            '[class*=team],[class*=guild],[class*=faction],[data-team]'
        )).slice(0,8).map(function(el){
            return { cls: el.className, text: el.innerText.trim().substring(0,80) };
        });

        var main = document.querySelector('main') || document.body;
        res.page_text = main ? main.innerText.substring(0, 3000) : '';
        return res;
    """)
    result["lobby_ui"] = lobby if isinstance(lobby, dict) else {}

    return result


def collect_game_globals(sb) -> dict:
    """Check for known game engine globals."""
    return js(sb, """
        var keys = ['Phaser','PIXI','THREE','unityInstance','io','Colyseus',
                    'game','Game','Snake','snake','GameState','ws','socket',
                    'Socket','App','__vue_app__','React','__NEXT_DATA__'];
        var res = {};
        keys.forEach(function(k) {
            try {
                var v = window[k];
                if (v !== undefined) {
                    var t = typeof v;
                    res[k] = t === 'object' && v !== null
                        ? { type: t, keys: Object.keys(v).slice(0,20) }
                        : { type: t, value: String(v).substring(0,100) };
                }
            } catch(e) { res[k] = { type: 'error', value: String(e) }; }
        });
        return res;
    """) or {}


def collect_intercepted(sb) -> dict:
    """Read captured data from window.__recon."""
    try:
        data = sb.execute_script("return (function(){ return window.__recon || null; })();")
        return data or {}
    except Exception as e:
        return {"error": str(e)}


def countdown_timer(seconds: int):
    print("\n" + "="*60)
    print(f"[RECON] *** YOUR TURN - you have {seconds} seconds ***")
    print("[RECON] WHAT TO DO:")
    print("  1. Enter a nickname in the 'Nik na areni' field")
    print("  2. Click 'Obery personazha' (Choose character)")
    print("  3. Pick a guild (ETH / NEAR / SOL)")
    print("  4. Click 'Uviyty' (Enter/Play)")
    print("  5. Move mouse to control snake")
    print("  6. Let the snake DIE (hit wall or other snake)")
    print("="*60 + "\n")
    for remaining in range(seconds, 0, -5):
        print(f"[TIMER] Time left: {remaining}s...")
        time.sleep(5)
    print("[RECON] Timer done. Collecting telemetry...\n")


def run_recon():
    """Main recon function - v3."""
    # Force UTF-8 on Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    try:
        from seleniumbase import SB
    except ImportError:
        print("[ERROR] seleniumbase not found. Activate venv!")
        sys.exit(1)

    cookie_file = find_cookie_file()
    binary = get_binary_path()

    print(f"[RECON] v3 - post-load injection (no CDP pre-injection)")
    print(f"[RECON] Browser : {binary or 'default Chrome'}")
    print(f"[RECON] Cookies : {cookie_file} (exists: {cookie_file.exists()})")
    print(f"[RECON] Report  : {REPORT_FILE}")
    print()

    with SB(uc=True, headless=False, binary_location=binary) as sb:

        # ── 1. Auth via cookies or manual login ───────────────────────
        print("[RECON] Step 1: Authentication...")
        cookie_ok = load_cookies_safe(sb, BASE_URL, cookie_file)
        if not cookie_ok:
            print("[RECON] No cookies found - manual login required.")
            print("[RECON] Opening login page, please log in...")
            sb.open("https://incrypted.com/ua/account/")
            for i in range(6):
                time.sleep(5)
                print(f"[RECON] Waiting for login: {30-(i+1)*5}s left...")
                try:
                    url = sb.get_current_url()
                    if "account" in url:
                        print("[RECON] Login detected!")
                        break
                except Exception:
                    pass

        # ── 2. Navigate to game (UC mode for Cloudflare bypass) ───────
        print(f"\n[RECON] Step 2: Navigating to {GAME_URL}...")
        try:
            sb.uc_open_with_reconnect(GAME_URL, reconnect_time=4)
            sb.sleep(6)
        except Exception:
            sb.open(GAME_URL)
            sb.sleep(7)

        # ── 3. Page state ─────────────────────────────────────────────
        try:
            recon_data["page"]["final_url"] = sb.get_current_url()
            recon_data["page"]["title"] = sb.get_title()
            recon_data["page"]["source_snippet"] = sb.get_page_source()[:2000]
            print(f"[RECON] Page: {recon_data['page']['title']} @ {recon_data['page']['final_url']}")
        except Exception as e:
            recon_data["errors"].append(f"Page state: {e}")

        # ── 4. Inject interceptors AFTER page load ────────────────────
        print("\n[RECON] Step 4: Injecting interceptors into live page...")
        injected = inject_interceptors(sb)
        if not injected:
            print("[RECON] WARNING: Injection failed - telemetry will be empty!")

        # ── 5. Lobby DOM (with IIFE-wrapped scripts) ──────────────────
        print("\n[RECON] Step 5: Lobby analysis...")
        lobby_data = collect_lobby_info(sb)
        recon_data["scripts"] = lobby_data.get("scripts", [])
        recon_data["buttons"] = lobby_data.get("buttons", [])
        recon_data["lobby_ui"] = lobby_data.get("lobby_ui", {})

        # ── 6. Game engine globals ────────────────────────────────────
        print("\n[RECON] Step 6: Game engine globals...")
        recon_data["game_engine"]["global_objects"] = collect_game_globals(sb)

        # ── 7. YOUR TURN ──────────────────────────────────────────────
        countdown_timer(90)

        # ── 8. Collect telemetry ──────────────────────────────────────
        print("[RECON] Step 8: Collecting intercepted telemetry...")
        intercepted = collect_intercepted(sb)
        recon_data["network"]["websockets"] = intercepted.get("ws", [])
        recon_data["network"]["fetch_calls"] = intercepted.get("fetch", [])
        recon_data["network"]["xhr_calls"]   = intercepted.get("xhr", [])
        recon_data["game_engine"]["canvas_contexts"] = intercepted.get("canvas", [])
        recon_data["game_engine"]["raf_stacks"]      = intercepted.get("raf", [])
        recon_data["event_listeners"]                = intercepted.get("events", [])

        # ── 9. Screenshot ─────────────────────────────────────────────
        try:
            shot = str(PROJECT_ROOT / "snake_recon_screenshot.png")
            sb.save_screenshot(shot)
            print(f"[RECON] Screenshot: {shot}")
        except Exception as e:
            print(f"[RECON] Screenshot error: {e}")

        # ── 10. Save report ───────────────────────────────────────────
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(recon_data, f, ensure_ascii=False, indent=2, default=str)
        print(f"[RECON] Report saved: {REPORT_FILE}")

        sb.sleep(5)

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[RECON] SUMMARY")
    print("="*60)
    ws   = recon_data["network"]["websockets"]
    ftch = recon_data["network"]["fetch_calls"]
    xhr  = recon_data["network"]["xhr_calls"]
    cvs  = recon_data["game_engine"]["canvas_contexts"]
    evts = recon_data["event_listeners"]
    glbs = [k for k, v in recon_data["game_engine"]["global_objects"].items()
            if isinstance(v, dict) and "error" not in v]
    print(f"  WebSocket connections : {len(ws)}")
    for w in ws:
        print(f"    -> {w.get('url')} | sent={len(w.get('frames_sent',[]))} recv={len(w.get('frames_received',[]))}")
    print(f"  Fetch calls           : {len(ftch)}")
    for f in ftch[:5]:
        print(f"    -> [{f.get('method')}] {f.get('url','')[:80]} status={f.get('response_status')}")
    print(f"  XHR calls             : {len(xhr)}")
    print(f"  Canvas contexts       : {len(cvs)}")
    for c in cvs:
        print(f"    -> {c.get('context_type')} {c.get('width')}x{c.get('height')}")
    print(f"  Event listeners       : {len(evts)}")
    for e in evts[:5]:
        print(f"    -> [{e.get('event_type')}] on {e.get('target')} | isTrusted check: {e.get('has_isTrusted_check')}")
    print(f"  Game globals          : {glbs}")
    print("="*60)
    print(f"Full report: {REPORT_FILE}")


if __name__ == "__main__":
    run_recon()
