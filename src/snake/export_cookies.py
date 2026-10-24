import json
import time
from pathlib import Path

cookie_file = Path("incrypted_cookies.json")
if cookie_file.exists():
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    now = time.time()
    clean_cookies = []
    for c in cookies:
        name = c["name"]
        # Skip Cloudflare clearance and bot tokens: they are bound to the specific browser & OS
        # where they were generated. Injecting Windows cf_clearance into Linux triggers Turnstile!
        if name.lower() in ("cf_clearance", "__cf_bm", "_cfuvid", "cf_chl_prog"):
            continue
        item = {
            "name": name,
            "value": c["value"],
            "domain": c.get("domain", ".incrypted.com"),
            "path": c.get("path", "/"),
        }
        if "secure" in c and isinstance(c["secure"], bool):
            item["secure"] = c["secure"]
        if "httpOnly" in c and isinstance(c["httpOnly"], bool):
            item["httpOnly"] = c["httpOnly"]
        exp = c.get("expiry")
        if exp:
            try:
                exp_f = float(exp)
                # If expired, give it 30 days
                item["expiry"] = int(now + 86400 * 30) if exp_f < now else int(exp_f)
            except (ValueError, TypeError):
                pass
        clean_cookies.append(item)

    print("=== COPY THE LINE BELOW FOR GITHUB SECRET 'INCRYPTED_COOKIES' ===")
    print(json.dumps(clean_cookies))
    print("=== END OF COOKIES ===")
else:
    print("incrypted_cookies.json not found!")

