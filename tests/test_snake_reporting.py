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
