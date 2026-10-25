# CryptoSnake Hybrid Scoring Architecture (v1: 45k+ Points Feasibility)

**Date:** 2026-10-25  
**Target:** Bridge the gap from ~14,400 to 45,000–50,000+ points on the seasonal leaderboard (Team NEAR ×2 multiplier).  
**Scope:** v1 Hybrid (3-phase growth, 650px death drop magnet with `isPathSafe` raycasting, mass-scaled `avoidRadius`, forward cone collision check, gentle core traffic dampening, and Telegram report deduplication).  
*Flank Cut-Off active hunting is deferred to v2.*

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

## 2. Key Architecture Components (v1)

### Component 1: Telegram Report Fix
- Remove `Загалом за весь час: ...` line to prevent 1:1 duplication with `Найкращий забіг`.
- Retain only `Офіційний рекорд: {best_score:,} монет` and `Місце в рейтингу: {rank_str}`.

### Component 2: Balanced `avoidRadius` (F6)
- Replaces static 220px:
  ```javascript
  var avoidRadius = Math.max(220, Math.min(400, 180 + meMass * 0.08));
  ```
  - Mass 100: 220px (safe margin > 4× base turn radius 52px).
  - Mass 1,000: 260px.
  - Mass 2,500: 380px.
  - Mass 3,000+: capped at 400px (avoids over-repelling from distant 650px drops).

### Component 3: Safe Path Raycasting (`isPathSafe` for 650px Drops) (F3)
- Expand death drop detection from 320px to 650px.
- Before triggering Boost or high attraction to any cluster at `dist > 320px`, perform 10 interpolation steps along the ray from head $(hx, hy)$ to cluster center $(cx, cy)$:
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
- If `dist > 320` and `!isPathSafe`, sprint is denied; bot falls back to local food navigation.

### Component 4: Forward Cone Danger Check
- In addition to radial distance, compute relative angle to enemy segments within 250px.
- If an enemy body or head is inside a $\pm 35^\circ$ forward cone relative to our current heading, apply an emergency lateral repulsion vector (perpendicular to enemy segment) to prevent tangential clipping during wide turns.

### Component 5: Spatial Distribution & Mild Core Dampening (F4)
- Food is uniformly distributed out to 1,760px.
- Core traffic (`distFromCenter < 450px`) has high collision cross-sections when snake is huge.
- When `meMass > 1500`, apply a gentle outward bias if inside `distFromCenter < 450px` (`coreBias = (450 - dist) / 450 * 5.0`), without creating an impassable barrier if food/drops are present.

### Component 6: CI Workflow Timeout (F7)
- Set `timeout-minutes: 50` in `.github/workflows/snake.yml`.
- A 35-minute run + 3 min startup + 2 min wrapup finishes in ~40 min, safely under the 50 min timeout.

---

## 3. Testing & Verification Protocol
1. **Static Analysis & Unit Tests:** Verify syntax, JS string integrity, and safe_eval execution with `pytest tests/ -q`.
2. **DRY_RUN Verification:** Run a 3-minute test run (`DRY_RUN=1`) to verify `isPathSafe`, `avoidRadius` scaling, cluster locking up to 650px, and Telegram message formatting.
3. **Live CI Verification:** Trigger GitHub Actions workflow with `runs: 1, dry_run: 0` to observe 25k–45k progression in production.
