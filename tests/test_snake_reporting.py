import inspect
import pytest

def test_telegram_report_deduplication():
    from src.snake.bot import format_snake_tg_report
    msg = format_snake_tg_report(
        run_idx=1,
        max_runs=1,
        score_earned=14443,
        kills_earned=40,
        duration_sec=1166,
        boost_triggers_count=36,
        rec_line="🎯 До рекорду топ-1 сезону (48,670): ще 34,227 монет",
        nick="newagno",
        best_score=14443,
        rank_str="поза топ-20",
        guild_pts="1 549 085",
        guild_place=3,
        guild_sub="NEAR за сезон · 21 гравець"
    )
    assert "Офіційний рекорд: <b>14,443 монет</b>" in msg
    assert "Загалом за весь час" not in msg
    assert "Найкращий забіг" not in msg
    assert "+14,443 монет" in msg
    assert "• Активацій Turbo Boost: <b>36</b>" in msg

def test_telegram_report_zero_boost():
    from src.snake.bot import format_snake_tg_report
    msg = format_snake_tg_report(
        run_idx=1,
        max_runs=1,
        score_earned=5000,
        kills_earned=10,
        duration_sec=300,
        boost_triggers_count=0,
        rec_line="🎯 До рекорду...",
        nick="newagno",
        best_score=5000,
        rank_str="поза топ-20",
        guild_pts="100 000",
        guild_place=3,
        guild_sub="NEAR"
    )
    # When 0, boost line is omitted to avoid confusing user
    assert "Turbo Boost" not in msg

def test_report_called_with_actual_bot_vars():
    from src.snake.bot import _run_snake_bot_impl
    source = inspect.getsource(_run_snake_bot_impl)
    assert "boost_triggers_count = 0" in source, "boost_triggers_count не ініціалізовано"
    assert "rec_line =" in source, "rec_line не визначено"
    assert "place_medal =" not in source, "dead code place_medal все ще присутній у caller"

def test_telegram_report_top20_rank():
    from src.snake.bot import format_snake_tg_report
    msg = format_snake_tg_report(
        run_idx=1,
        max_runs=1,
        score_earned=63444,
        kills_earned=214,
        duration_sec=5387,
        boost_triggers_count=153,
        rec_line="🎯 До рекорду топ-1 сезону (136,162): <b>ще 72,718 монет</b>",
        nick="newagno",
        best_score=63444,
        rank_str="#12 (сезон)",
        guild_pts="4 215 113",
        guild_place=3,
        guild_sub="NEAR за сезон · 37 гравців"
    )
    assert "Місце в рейтингу: <b>#12 (сезон)</b>" in msg
    assert "До рекорду топ-1 сезону (136,162): <b>ще 72,718 монет</b>" in msg
    assert "Офіційний рекорд: <b>63,444 монет</b>" in msg
    assert "+63,444 монет" in msg

def test_telegram_report_new_season_record():
    from src.snake.bot import format_snake_tg_report
    msg = format_snake_tg_report(
        run_idx=1,
        max_runs=1,
        score_earned=140000,
        kills_earned=300,
        duration_sec=5400,
        boost_triggers_count=200,
        rec_line="🏆 <b>НОВИЙ РЕКОРД СЕЗОНУ!</b> Перевершено топ-1 на +3,838 монет!",
        nick="newagno",
        best_score=140000,
        rank_str="#1 (сезон)",
        guild_pts="4 500 000",
        guild_place=1,
        guild_sub="NEAR за сезон · 40 гравців"
    )
    assert "🏆 <b>НОВИЙ РЕКОРД СЕЗОНУ!</b> Перевершено топ-1 на +3,838 монет!" in msg
    assert "Місце в рейтингу: <b>#1 (сезон)</b>" in msg

def test_dead_score_does_not_overwrite_higher_telemetry():
    from src.snake.bot import _run_snake_bot_impl
    source = inspect.getsource(_run_snake_bot_impl)
    assert "if dead_score >= score_earned:" in source, "Немає захисту від затирання високого in-memory score низьким dead_score"
    assert "if dead_kills_val >= kills_earned:" in source, "Немає захисту від затирання kills_earned"
    assert "setInterval" not in source, "Небезпечний setInterval для nonce все ще присутній"

def test_run_closed_and_abandon_immunity():
    from src.snake.bot import _run_snake_bot_impl
    source = inspect.getsource(_run_snake_bot_impl)
    # 1. Navigator sendBeacon blocked
    assert "window.navigator.sendBeacon = function" in source
    assert "abandon" in source
    # 2. Window pagehide and visibility blocked
    assert "pagehide" in source
    assert "visibilitychange" in source
    assert "Object.defineProperty(document, 'hidden'" in source
    # 3. VerifiedRun start and onAbandon defusal
    assert "proto.start = function" in source
    # 4. Abandon method neutralized
    assert "proto.abandon = async function" in source
    # 5. Checkpoint timeout is 12s with rapid retry
    assert "timeouts = (action === 'finish') ? [20000, 25000, 25000] : [16000, 16000]" in source
    # 6. Chromium args include occlusion disabling
    assert "--disable-features=CalculateNativeWinOcclusion" in source


