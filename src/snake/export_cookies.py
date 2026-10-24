import json
from pathlib import Path

cookie_file = Path("incrypted_cookies.json")
if cookie_file.exists():
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    print("=== COPY THE LINE BELOW FOR GITHUB SECRET 'INCRYPTED_COOKIES' ===")
    print(json.dumps(cookies))
    print("=== END OF COOKIES ===")
else:
    print("incrypted_cookies.json not found!")
