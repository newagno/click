# CryptoSnake Hybrid Scoring Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the v1 CryptoSnake Hybrid Scoring Architecture to target 25,000–35,000 coins (stretch 40,000+) by optimizing drop magnet reach (650px), raycast safety gating (`isPathSafe`), balanced mass-scaled avoidance (220–400px), 3-phase growth, and Telegram deduplication.

**Architecture:** Extend the client-side JavaScript radar inside `src/snake/bot.py` with 10-step raycast validation, 3-phase growth weights, and scaled avoidance margins. Wrap session loops with a 50-minute watchdog while retaining the 60-minute CI timeout.

**Tech Stack:** Python 3.11/3.14, SeleniumBase (CDP mode), Chrome/Brave, JavaScript (Canvas/World injection), Pytest.

## Global Constraints
- Strictly route all JS execution through `safe_eval(sb, ...)` — never raw `sb.cdp.evaluate()` or top-level returns.
- Preserve Telegram final reporting without intermediate spam.
- Preserve verified engine constants: `deathDropShare: 0.75`, `f.natural === false`, `f.v`.
- Flank Cut-Off active hunting and Forward Cone heuristics are strictly deferred to v1.1 / v2.

---

### Task 1: Telegram Report Deduplication & Formatting

**Files:**
- Modify: `src/snake/bot.py:1240-1250`
- Create: `tests/test_snake_reporting.py`

**Interfaces:**
- Consumes: `score_earned`, `best_score`, `nick`, `rank_str`
- Produces: Telegram formatted message with `Офіційний рекорд: {best_score:,} монет` and no duplicate total coins line.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snake_reporting.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snake_reporting.py -v`
Expected: FAIL (ImportError or AttributeError: `format_snake_tg_report` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/snake/bot.py`:
Extract helper function `format_snake_tg_report(...)`:
```python
def format_snake_tg_report(
    run_idx: int,
    max_runs: int,
    score_earned: int,
    kills_earned: int,
    duration_sec: int,
    boost_triggers_count: int,
    rec_line: str,
    nick: str,
    best_score: int,
    rank_str: str,
    guild_pts: str,
    guild_place: int,
    guild_sub: str
) -> str:
    place_medal = "🥇" if guild_place == 1 else ("🥈" if guild_place == 2 else "🥉")
    return (
        f"🐍 <b>CryptoSnake — Забіг завершено! (#{run_idx} із {max_runs})</b>\n\n"
        f"🎯 <b>Результат цієї гри:</b>\n"
        f"• Набрано очок: <b>+{score_earned:,} монет</b> (NEAR ×2)\n"
        f"• З'їдено змій: <b>{kills_earned}</b>\n"
        f"• Час на арені: <b>{duration_sec // 60} хв {duration_sec % 60} сек</b>\n"
        f"• Активацій Turbo Boost: <b>{boost_triggers_count}</b>\n"
        f"• {rec_line}\n\n"
        f"👤 <b>Твій профіль ({nick}):</b>\n"
        f"• Офіційний рекорд: <b>{best_score:,} монет</b>\n"
        f"• Місце в рейтингу: <b>{rank_str}</b>\n\n"
        f"🟢 <b>Команда NEAR:</b>\n"
        f"• Очки гільдії: <b>{guild_pts} монет</b>\n"
        f"• Місце команди: <b>{guild_place}-е місце {place_medal}</b>\n"
        f"• Склад: <i>{guild_sub}</i>\n\n"
        f"🤖 <i>Режим: Turbo Vacuum (Space) + Smart Predator + Dynamic Walls</i>"
    )
```
Update line ~1236 in `bot.py` to call `format_snake_tg_report(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snake_reporting.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/snake/bot.py tests/test_snake_reporting.py
git commit -m "feat(snake): deduplicate telegram profile report to show official best score"
```

---

### Task 2: Balanced `avoidRadius` & 3-Phase State Machine Logic

**Files:**
- Modify: `src/snake/bot.py:915-970`
- Test: `tests/test_snake_physics.py`

**Interfaces:**
- Consumes: `meMass`, `minEnemyDist`, `distFromCenter`
- Produces: `avoidRadius`, `foodWeight`, `clusterWeight`, `coreBias` in radar JS

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snake_physics.py
import pytest

def test_avoid_radius_scaling():
    from src.snake.bot import calculate_avoid_radius
    assert calculate_avoid_radius(100) == 220
    assert calculate_avoid_radius(1000) == 260
    assert calculate_avoid_radius(2500) == 380
    assert calculate_avoid_radius(3500) == 400

def test_phase_weights():
    from src.snake.bot import get_phase_weights
    # Phase 1: mass < 400
    w1 = get_phase_weights(350)
    assert w1["food"] == 1.0
    assert w1["cluster"] == 2.0
    # Phase 2: 400 <= mass <= 1500
    w2 = get_phase_weights(800)
    assert w2["food"] == 1.0
    assert w2["cluster"] == 2.5
    # Phase 3: mass > 1500
    w3 = get_phase_weights(2000)
    assert w3["food"] == 0.5
    assert w3["cluster"] == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snake_physics.py -v`
Expected: FAIL (ImportError: `calculate_avoid_radius` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/snake/bot.py`:
Add Python helper functions for testing & mirror them in JS radar:
```python
def calculate_avoid_radius(mass: float) -> float:
    return max(220.0, min(400.0, 180.0 + mass * 0.08))

def get_phase_weights(mass: float) -> dict:
    if mass < 400:
        return {"food": 1.0, "cluster": 2.0}
    elif mass <= 1500:
        return {"food": 1.0, "cluster": 2.5}
    else:
        return {"food": 0.5, "cluster": 1.5}
```
Update JS radar in `src/snake/bot.py`:
```javascript
// Збалансований радіус безпеки за масою
var avoidRadius = Math.max(220, Math.min(400, 180 + meMass * 0.08));

// 3-фазний розподіл ваг
var foodWeight = 1.0;
var clusterWeight = 2.0;
if (meMass > 1500) {
    foodWeight = 0.5;
    clusterWeight = 1.5;
} else if (meMass >= 400) {
    foodWeight = 1.0;
    clusterWeight = 2.5;
}

// М'яке відхилення від ядра (< 450px) для великої змійки, якщо в ядрі немає великого кластера
var coreFx = 0, coreFy = 0;
if (meMass > 1500 && distFromCenter < 450 && (!bestCluster || bestCluster.dist > 300 || distFromCenter < 200)) {
    var coreForce = ((450 - distFromCenter) / 450) * 4.0;
    coreFx = (hx / (distFromCenter || 1)) * coreForce;
    coreFy = (hy / (distFromCenter || 1)) * coreForce;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snake_physics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/snake/bot.py tests/test_snake_physics.py
git commit -m "feat(snake): add mass-scaled avoidRadius and 3-phase growth state machine"
```

---

### Task 3: Safe Path Raycasting (`isPathSafe`) for 650px Death Drop Clusters

**Files:**
- Modify: `src/snake/bot.py:850-915`, `src/snake/bot.py:960-985`
- Test: `tests/test_snake_radar.py`

**Interfaces:**
- Consumes: `drops` array up to 650px, `snakes` map, `hx`, `hy`
- Produces: `isPathSafe` boolean gating `should_boost` and cluster attraction

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snake_radar.py
import pytest

def test_is_path_safe_js_syntax():
    from src.snake.bot import get_is_path_safe_js_code
    code = get_is_path_safe_js_code()
    assert "function isPathSafe" in code
    assert "steps = 10" in code
    assert "radius = 120" in code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snake_radar.py -v`
Expected: FAIL (ImportError: `get_is_path_safe_js_code` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/snake/bot.py`:
Expose `get_is_path_safe_js_code()` and inject it into the radar IIFE:
```javascript
function isPathSafe(fromX, fromY, toX, toY, snakes, myId, radius) {
    if (!radius) radius = 120;
    var steps = 10;
    for (var i = 1; i <= steps; i++) {
        var t = i / steps;
        var px = fromX + (toX - fromX) * t;
        var py = fromY + (toY - fromY) * t;
        for (var s of snakes.values()) {
            if (s.id === myId || s.guild === 'near') continue;
            if (!s.pts || s.pts.length < 2) continue;
            for (var p = 0; p < s.pts.length; p += 4) {
                var d = Math.hypot(px - s.pts[p], py - s.pts[p+1]);
                if (d < radius) return false;
            }
        }
    }
    return true;
}
```
Update drop detection distance from `dist <= 320` to `dist <= 650`.
For cluster boost:
```javascript
var pathIsSafe = (!bestCluster || bestCluster.dist <= 320) ? true : isPathSafe(hx, hy, bestCluster.x, bestCluster.y, w.snakes, me.id, 120);
var shouldBoost = Boolean(
    bestCluster &&
    bestCluster.mass >= 21 &&
    minEnemyDist > 250 &&
    meMass >= minMassForBoost &&
    bestCluster.dist > 40 &&
    distFromCenter < (safeRadius - 100) &&
    pathIsSafe
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snake_radar.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/snake/bot.py tests/test_snake_radar.py
git commit -m "feat(snake): expand drop radar to 650px with 10-step isPathSafe raycasting"
```

---

### Task 4: Session Hard Watchdog & CI Workflow Validation

**Files:**
- Modify: `src/snake/bot.py:760-785`
- Check: `.github/workflows/snake.yml`
- Test: `tests/test_snake_watchdog.py`

**Interfaces:**
- Consumes: `session_start_time`, `os.getenv("GITHUB_ACTIONS")`
- Produces: Clean graceful exit at 3000 seconds (50 min) before CI kills the process

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snake_watchdog.py
import pytest

def test_watchdog_threshold():
    from src.snake.bot import is_session_timeout
    # Threshold is 3000s in CI
    assert not is_session_timeout(elapsed=2900, is_ci=True)
    assert is_session_timeout(elapsed=3001, is_ci=True)
    # Locally, no 3000s cutoff
    assert not is_session_timeout(elapsed=3200, is_ci=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_snake_watchdog.py -v`
Expected: FAIL (ImportError: `is_session_timeout` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/snake/bot.py`:
```python
def is_session_timeout(elapsed: float, is_ci: bool) -> bool:
    if is_ci and elapsed >= 3000:
        return True
    return False
```
Integrate check into game loop:
```python
session_start = time.time()
# Inside while loop:
if is_session_timeout(time.time() - session_start, bool(os.getenv("GITHUB_ACTIONS"))):
    print("[BOT] ⏰ 50 хв сесії вичерпано (watchdog), планове завершення забігу...")
    break
```
Confirm `.github/workflows/snake.yml` has `timeout-minutes: 60`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_snake_watchdog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/snake/bot.py tests/test_snake_watchdog.py
git commit -m "feat(snake): add 50-minute hard session watchdog in CI"
```

---

### Task 5: DRY_RUN & 15-Minute Milestone Verification

**Files:**
- Test all components combined in `src/snake/bot.py`

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -q`
Expected: All tests pass.

- [ ] **Step 2: Run 3-minute DRY_RUN test**

Run: `python -c "import os; os.environ['DRY_RUN']='1'; from src.snake.bot import run_snake_bot; ..."` or standard command.
Verify:
- `isPathSafe` returns true/false without JS exceptions.
- Clusters detected up to 650px.
- `avoidRadius` scales with mass.
- Telegram report formatted with `Офіційний рекорд:` and no duplicates.

- [ ] **Step 3: Milestone 15-Minute Run Gate**

Run a 15-minute game run (`max_duration = 900`).
Verify:
- Score > 8,000 coins.
- 0 collisions / crashes.
- Rate > 9–10 coins/sec.

- [ ] **Step 4: Commit and Push**

```bash
git push origin main
```
