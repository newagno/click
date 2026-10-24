import os
import requests
from dotenv import load_dotenv

load_dotenv()
# Also try loading from project root or src/snake/.env
for env_path in [".env", "src/snake/.env", "../.env"]:
    if os.path.exists(env_path):
        load_dotenv(env_path)

def send_telegram_message(message: str) -> bool:
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("[NOTIFIER] TG_BOT_TOKEN або TG_CHAT_ID не налаштовано в .env. Пропускаємо сповіщення.")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        if data.get("ok"):
            print("[NOTIFIER] Повідомлення в Telegram успішно надіслано!")
            return True
        else:
            print(f"[NOTIFIER] Помилка Telegram API: {data}")
            return False
    except Exception as e:
        print(f"[NOTIFIER] Не вдалося надіслати повідомлення: {e}")
        return False
