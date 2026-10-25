import sys
from pathlib import Path

# Add src to sys.path
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from snake.bot import run_snake_bot

if __name__ == "__main__":
    max_runs = 1
    headless = False
    
    # Optional CLI args: [number of runs] [--headless]
    for arg in sys.argv[1:]:
        if arg.isdigit():
            max_runs = int(arg)
        elif arg.lower() in ["--headless", "-h"]:
            headless = True

    print(f"[*] Запуск бота CryptoSnake на {max_runs} забігів (headless={headless})...")
    run_snake_bot(max_runs=max_runs, headless=headless)
