# CryptoSnake Hybrid Scoring Architecture (v1: 25k–35k Target, Stretch 40k+)

**Date:** 2026-10-25  
**Target:** Bridge the gap from ~14,400 to realistic 25,000–35,000 coins (stretch 40,000+), Team NEAR ×2 multiplier.  
**Scope:** v1 Hybrid (3-phase growth, 650px death drop magnet with `isPathSafe` 10-step raycasting, mass-scaled `avoidRadius` (220–400px), cluster-aware core dampening, 50-min watchdog timer, CI timeout 60 min, and Telegram report deduplication).  
*Forward Cone heuristics and Flank Cut-Off hunting are deferred to v1.1 / v2.*

---

## 1. Verified Engine Physics (`cryptosnake.min.js`)

All mechanics have been extracted and confirmed directly from `cryptosnake.min.js`:

| Parameter | Engine Definition / Formula | Concrete Values & Analysis |
|---|---|---|
| **Snake Radius (`r`)** | `_0x4213e0 = (mass) => 10 + Math.min(26, 0.9 * Math.sqrt(Math.max(0, mass)))` | Starts at 12.8px (mass 10). Reaches 28px at mass 400. **Hard-capped at 36px** for mass ≥ 835. |
| **Turn Rate (`turnSpeed`)** | `_0x276db5 = (r) => 5.6 / (1 + (r - 12) / 34)` (rad/s) | Fast at birth (~5.6 rad/s / 320°/s). Drops to 3.8 rad/s at mass 400. **Hard floor at 3.28 rad/s (~188°/s)** for mass ≥ 835. |
| **Minimum Turn Radius** | $R = \frac{v}{\omega}$ | At base speed (170 px/s): $R \approx 51.8\text{ px}$. At boost speed (340 px/s): $R \approx 103.6\text{ px}$. |
| **Background Food (`natural: true`)** | `_0x1f1aa4 = Math.random() < 0.08 ? 2 : 1` | 92% are 1 mass, 8% are 2 mass (avg 1.08). Uniformly distributed across arena up to $R = 1760\text{ px}$. |
| **Death Drops (`natural: false`)** | `deathDropShare: 0.75`, $N = \min(150, \lceil \text{mass} \cdot 0.75 / 3 \rceil)$, $v = \text{dropMass} / N$ | Dropped along dead snake body. A 1,000-mass snake drops 750 mass (~150 items of $v \approx 5.0$). Gives **1,500 coins** with NEAR ×2. |
| **Mass Decay** | `(mass - 400) * 0.01 / sec` (for mass > 400) | At mass 1,000: 6 mass/s. At mass 3,000: **26 mass/s**. Background dots alone cannot sustain mass > 1,500. Feeding on death drops is mathematically mandatory. |

---

## 2. Expected Outcomes & Success Metrics

### Realistic Targets:
* **Minimum (Rollback threshold):** < 15,000 coins in 35 min → rollback changes.
* **Target (Success):** 25,000–35,000 coins (12–17 coins/sec average over 35 min).
* **Stretch Goal:** 40,000+ coins.
* *Note on 45k+:* 45,000+ requires exceptional RNG with multiple high-mass bot deaths clustered nearby; we do not promise 45k unconditionally in v1.

### Post-Run Evaluation Metrics:
1. **Coins/sec average:** Target > 14 coins/sec (previous baseline: 9.0–12.3).
2. **Cluster hits/min:** Target > 3 successful cluster pickups per minute.
3. **Deaths/min:** Target 0 (must survive the full session duration).
4. **Peak mass reached:** Target > 2,500.
5. **Time spent at mass > 2,000:** Target > 40% of session time.

---

## 3. Key Architecture Components (v1)

### Component 1: Telegram Report Cleanup
* Remove `Загалом за весь час: ...` line to eliminate 1:1 duplication with `Найкращий забіг`.
* Display clean official personal best:
  ```
  👤 Твій профіль ({nick}):
  • Офіційний рекорд: {best_score:,} монет
  • Місце в рейтингу: {rank_str}
  ```

### Component 2: Explicit 3-Phase Growth State Machine
* **Phase 1: mass < 400 ("Early Accumulation"):**
  * Decay: 0 mass/s (inactive).
  * `foodWeight = 1.0` (active vacuuming of background dots and NEAR coins ×3.0).
  * `clusterWeight = 2.0`.
  * Boost: enabled if `cluster.mass >= 21` and `minEnemyDist > 250`.
  * Risk tolerance: medium.
* **Phase 2: 400 ≤ mass ≤ 1,500 ("Predator Drop Hunter"):**
  * Decay: 0–11 mass/s (manageable).
  * `foodWeight = 1.0`, `clusterWeight = 2.5`.
  * Boost: aggressive sprint to death drops up to 650px, strictly gated by `isPathSafe == true`.
  * Risk tolerance: high for verified drops (worth sprinting for 500–1,500 mass).
* **Phase 3: mass > 1,500 ("Titan Survival & Orbit"):**
  * Decay: 11–26+ mass/s (critical).
  * `foodWeight = 0.5` (never compromise safety for a 1-mass background dot).
  * `clusterWeight = 1.5` (prioritize safe drop intake).
  * Boost: strictly gated by `isPathSafe == true`, `minEnemyDist > 250`, `mass >= minMassForBoost`.
  * Gentle core dampening: if inside `distFromCenter < 450px`, bias outward unless a large cluster (`mass > 30`) is inside the core.
  * Risk tolerance: low (preserve snake life against high-inertia turning limits).

### Component 3: Balanced `avoidRadius` (F6)
* Replaces static 220px:
  ```javascript
  var avoidRadius = Math.max(220, Math.min(400, 180 + meMass * 0.08));
  ```
  * Mass 100: 220px (> 4× base turn radius of 52px).
  * Mass 1,000: 260px.
  * Mass 2,500: 380px.
  * Mass 3,000+: capped at 400px (prevents over-repelling from 650px drops).

### Component 4: Safe Path Raycasting (`isPathSafe` for 650px Drops) (F3)
* Expand drop detection from 320px to 650px.
* For clusters with `dist > 320px`, perform 10 interpolation steps between snake head $(hx, hy)$ and $(cx, cy)$:
  ```javascript
  function isPathSafe(fromX, fromY, toX, toY, snakes, myId, radius = 120) {
      const steps = 10;
      for (let i = 1; i <= steps; i++) {
          const t = i / steps;
          const px = fromX + (toX - fromX) * t;
          const py = fromY + (toY - fromY) * t;
          for (const s of snakes.values()) {
              if (s.id === myId || s.guild === 'near') continue;
              if (!s.pts) continue;
              for (let p = 0; p < s.pts.length; p += 4) {
                  const d = Math.hypot(px - s.pts[p], py - s.pts[p+1]);
                  if (d < radius) return false;
              }
          }
      }
      return true;
  }
  ```
* If `dist > 320` and `!isPathSafe`, boost sprint is denied and cluster force is attenuated.

### Component 5: Spatial Distribution & Mild Core Dampening (F4)
* Food is uniformly distributed up to 1,760px.
* When `meMass > 1500`, apply outward repulsion from center $(0,0)$ if `distFromCenter < 450px`, BUT cancel repulsion if `bestCluster` is located in the center.

### Component 6: CI Timeout & Hard Session Watchdog (F4)
* `.github/workflows/snake.yml`: retain `timeout-minutes: 60`.
* In `src/snake/bot.py`: implement hard session watchdog:
  ```python
  if os.getenv("GITHUB_ACTIONS") and (time.time() - session_start_time) > 3000: # 50 min
      print("[BOT] ⏰ 50 хвилин сесії вичерпано, планове завершення...")
      break
  ```

### Component 7: Deferred Features
* **Component 4 (Forward Cone):** Deferred to v1.1. In v1, rely on verified `avoidRadius` (220–400px) and `isPathSafe` raycasting to avoid oscillation risks.
* **Flank Cut-Off Hunting:** Deferred to v2.

---

## 4. Execution & Verification Roadmap

1. **Step 0:** Commit updated design spec.
2. **Step 1:** Implement DRY_RUN + 3-Phase State Machine + Balanced `avoidRadius` in `src/snake/bot.py`.
3. **Step 2:** Implement 650px Cluster Detection + `isPathSafe` raycasting in JS radar.
4. **Step 3:** Implement Telegram message cleanup + 50-minute watchdog.
5. **Step 4:** Run test suite (`pytest tests/ -q`) and 3-minute DRY_RUN test (`DRY_RUN=1`).
6. **Step 5 (Milestone Gate):** Run a 15-minute test run (`max_duration = 900`).
   * *Success criteria:* > 8,000 coins, 0 crashes, average > 9 coins/sec.
7. **Step 6:** If 15-minute gate passes, commit and deploy full 35-minute run (Target: 25k–35k).
