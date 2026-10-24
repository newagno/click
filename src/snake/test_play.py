import os
import sys
import time
import json
from seleniumbase import SB

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

brave = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
binary = brave if os.path.exists(brave) else None

print("[PLAY] Launching game test run...")

with SB(uc=True, headless=False, binary_location=binary) as sb:
    sb.uc_open_with_reconnect("https://incrypted.com/cryptosnake/", reconnect_time=4)
    sb.sleep(4)

    # 1. Check if user is logged in
    wp_data = sb.execute_script("""
        return {
            wp: window.CryptoSnakeWP || null,
            config: window.CRYPTOSNAKE_CONFIG_DATA || null
        };
    """)
    print("[PLAY] WP Info:", json.dumps(wp_data.get("wp"), indent=2, ensure_ascii=False))

    # 2. Select Character (Solana - Anatoliy Yakovenko)
    print("[PLAY] Selecting Solana character...")
    sb.click(".cs-char.cs-hero-sol")
    sb.sleep(1)

    # 3. Check Start Button
    start_btn = sb.execute_script("""
        var btn = document.querySelector('#cs-start');
        return { text: btn ? btn.innerText : null, disabled: btn ? btn.disabled : true };
    """)
    print("[PLAY] Start button state:", start_btn)

    # 4. Click Start to enter the arena!
    print("[PLAY] Clicking #cs-start to enter game...")
    sb.click("#cs-start")
    sb.sleep(2)

    # 5. Play for 15 seconds by moving mouse via CDP (ensures isTrusted: true)
    canvas_rect = sb.execute_script("""
        var c = document.querySelector('#cs-canvas');
        if (!c) return null;
        var r = c.getBoundingClientRect();
        return { x: r.left, y: r.top, w: r.width, h: r.height };
    """)
    print("[PLAY] Canvas rect:", canvas_rect)

    if canvas_rect:
        cx = int(canvas_rect['x'] + canvas_rect['w'] / 2)
        cy = int(canvas_rect['y'] + canvas_rect['h'] / 2)
        import math
        print("[PLAY] Steering snake with circular movements for 15 seconds...")
        for t in range(30):
            angle = t * 0.3
            mx = int(cx + 150 * math.cos(angle))
            my = int(cy + 150 * math.sin(angle))
            sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": mx,
                "y": my
            })
            time.sleep(0.5)

    sb.sleep(3)
    sb.save_screenshot("snake_play_test.png")
    print("[PLAY] Saved snake_play_test.png screenshot.")

    # 6. Check game state after playing
    hud_info = sb.execute_script("""
        return {
            len: document.querySelector('#cs-hud-len')?.innerText,
            kills: document.querySelector('#cs-hud-kills')?.innerText,
            rank: document.querySelector('#cs-hud-rank')?.innerText,
            warning: document.querySelector('#cs-run-warning')?.innerText,
            is_dead: !document.querySelector('#cs-again')?.hidden
        };
    """)
    print("[PLAY] HUD state:", json.dumps(hud_info, indent=2, ensure_ascii=False))
