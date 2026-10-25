# CryptoSnake Hybrid Scoring Architecture Implementation Plan (v1: 3 Waves)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the v1 CryptoSnake Hybrid Scoring Architecture targeting 25,000–35,000 coins (stretch 40,000+) in 3 waves: Safe Changes (Telegram dedup & 50m watchdog) -> Core Growth (3-phase state machine with foodWeight=1.5 on high mass & avoidRadius 220-400px) -> Drop Magnet (400px radar with throttled isPathSafe raycasting).

**Architecture:** Extend client-side JavaScript radar inside `src/snake/bot.py` with 400px drop search, throttled raycasting, 3-phase decay-compensating weights (`foodWeight=1.5`, `clusterWeight=3.0` at mass > 1500), and mass-scaled avoidance margins (220–400px). Wrap session loops with a 50-minute watchdog while retaining the 60-minute CI timeout.

**Tech Stack:** Python 3.11/3.14, SeleniumBase (CDP mode), Chrome/Brave, JavaScript (Canvas/World injection), Pytest.

## Global Constraints
- Strictly route all JS execution through `safe_eval(sb, ...)` — never raw `sb.cdp.evaluate()` or top-level returns.
- Preserve Telegram final reporting without intermediate spam.
- Preserve verified engine constants: `deathDropShare: 0.75`, `f.natural === false`, `f.v`.
- v1 Scope: Drop magnet capped at 400px (not 650px); Forward Cone and Flank Cut-Off hunting deferred to v1.1 / v2.
- Gate requirement: 15-minute verification run must exceed **12 coins/sec** (not just flat score) with 0 deaths and peak mass > 1,000.

---

### Wave 1: Safe Foundation (Task 1: Telegram Dedup + Task 4: 50m Watchdog)

#### Task 1: Telegram Report Deduplication & Formatting
**Files:**
- Modify: `src/snake/bot.py:1230-1255`
- Test: `tests/test_snake_reporting.py`

- [ ] **Step 1: Write test for format_snake_tg_report**
- [ ] **Step 2: Implement format_snake_tg_report and update bot.py**
- [ ] **Step 3: Run pytest tests/test_snake_reporting.py**

#### Task 4: Session Hard Watchdog & CI Workflow Validation
**Files:**
- Modify: `src/snake/bot.py:760-785`
- Check: `.github/workflows/snake.yml`
- Test: `tests/test_snake_watchdog.py`

- [ ] **Step 1: Write test for is_session_timeout**
- [ ] **Step 2: Implement session watchdog in game loop (3000s threshold in CI)**
- [ ] **Step 3: Run pytest tests/test_snake_watchdog.py**
- [ ] **Step 4: Commit Wave 1 and show git diff to user**

---

### Wave 2: Core Growth & Avoidance (Task 2: 3-Phase State Machine & avoidRadius)

#### Task 2: 3-Phase Growth & avoidRadius Scaling
**Files:**
- Modify: `src/snake/bot.py:915-970`
- Test: `tests/test_snake_physics.py`

- [ ] **Step 1: Write tests for calculate_avoid_radius and get_phase_weights**
  - Phase 1 (mass < 400): food=1.0, cluster=2.0
  - Phase 2 (400-1500): food=1.0, cluster=2.5
  - Phase 3 (mass > 1500): food=1.5 (decay compensation), cluster=3.0 (death drops critical)
- [ ] **Step 2: Implement in bot.py JS radar & Python helpers**
- [ ] **Step 3: Run pytest and 5-min DRY_RUN verifying phase transitions in telemetry**
- [ ] **Step 4: Commit Wave 2**

---

### Wave 3: Drop Magnet & Raycasting (Task 3: 400px Drop Radar & isPathSafe)

#### Task 3: 400px Drop Detection & Throttled isPathSafe Raycast
**Files:**
- Modify: `src/snake/bot.py:850-915`, `src/snake/bot.py:960-985`
- Test: `tests/test_snake_radar.py`

- [ ] **Step 1: Implement isPathSafe with 10-step raycast in JS radar**
- [ ] **Step 2: Expand drop search to 400px and throttle raycast calculation**
- [ ] **Step 3: Functional test in DRY_RUN verifying pathSafe=False occurs in logs**
- [ ] **Step 4: Commit Wave 3**

---

### Final Milestone Gate (15-Minute Test Run)
- [ ] **15-Minute Live/Staging Run**
  - Success criteria:
    - >= 12 coins/sec (minimum for 25k in 35 min)
    - 0 deaths
    - Peak mass > 1,000
    - isPathSafe=False logged at least once
