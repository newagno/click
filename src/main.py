import os
import time
from dotenv import load_dotenv
from browser import IncryptedBrowser
from notifier import send_telegram_message

load_dotenv()

# Configuration
EMAIL = os.getenv("INCRYPTED_EMAIL")
PASSWORD = os.getenv("INCRYPTED_PASSWORD")
PROXY = os.getenv("RESIDENTIAL_PROXY")
SILENT_ON_COOLDOWN = os.getenv("SILENT_ON_COOLDOWN", "True").lower() == "true"

def claim_daily_reward(max_retries=3):
    if not EMAIL or not PASSWORD:
        raise ValueError("Credentials are not set in environment variables.")

    for attempt in range(max_retries):
        try:
            # 1. Ініціалізуємо екземпляр браузера з винесеного модуля
            browser_session = IncryptedBrowser(EMAIL, PASSWORD, PROXY)
            
            # 2. Запускаємо процес виконання і отримуємо статус
            result = browser_session.execute_claim()

            # 3. Обробляємо отримані статуси
            if result == "already_claimed":
                if not SILENT_ON_COOLDOWN:
                    send_telegram_message("✅ <b>Incrypted</b>\nВже забрано сьогодні.")
                else:
                    print("Daily reward already claimed (silent mode).")
                return True
            
            if result.startswith("cooldown"):
                timer_info = result.split("|")[1]
                if not SILENT_ON_COOLDOWN:
                    send_telegram_message(f"⏳ <b>Incrypted</b>\nЩе не час. {timer_info}")
                else:
                    print(f"Daily reward in cooldown: {timer_info} (silent mode).")
                return True # Повертаємо True, щоб зупинити цикл спроб (це не помилка)
            
            if result == "claimed":
                send_telegram_message("🎉 <b>Incrypted</b>\nУспішно зібрано щоденну винагороду!")
                return True

            raise Exception(f"Received unknown result format: {result}")

        except Exception as e:
            error_msg = f"Attempt {attempt + 1} failed: {str(e)}"
            print(error_msg)
            if attempt == max_retries - 1:
                send_telegram_message(f"❌ <b>Incrypted Bot Error</b>\nFailed after {max_retries} attempts.\nError: {str(e)}")
                return False
            time.sleep(2 ** attempt) # Exponential backoff

if __name__ == "__main__":
    claim_daily_reward()