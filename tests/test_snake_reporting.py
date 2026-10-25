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
