# Trade Analysis & Setup Score — 2026-08-30

Based on 255 closed round-trip option trades, Mar–Aug 2026. Realized −$286 before
commissions, −$824 after ($538 in commissions). Win rate 36%, profit factor 0.96.

---

## 1. The core finding: hold time, not setup

Grouping every trade by the entry setup **and** how long it was held:

| Setup, held 2–14 days | n | win % | $/trade |
|---|---|---|---|
| Extended | 24 | 62% | **+$52** |
| Falling knife | 11 | **82%** | +$38 |
| Range / no trend | 38 | 42% | +$17 |
| Momentum breakout | 35 | 49% | +$16 |
| Early reversal | 14 | 64% | +$8 |

| Same setup, flipped < 2 days | n | win % | $/trade |
|---|---|---|---|
| Extended | 17 | **6%** | **−$37** |
| Momentum breakout | 44 | 18% | −$24 (−$1,063 total) |
| Early reversal | 21 | 19% | −$24 |
| Falling knife | 12 | 17% | −$20 |
| Range / no trend | 26 | 31% | −$10 |

**Every setup makes money held 2–14 days. Every setup loses money flipped in under 2 days.**
The setup you pick is barely the variable. The hold is.

## 2. What separates winners from losers

56 trades netting > +$30 vs 84 trades netting < −$30:

| | Winners | Losers |
|---|---|---|
| Held ≥ 2 days | 80% | 46% |
| Strike ATM or ITM (≤5% OTM) | 59% | 35% |
| DTE 46–90 days at entry | 62% | 45% |
| On a "graveyard" ticker | **5%** | **26%** |
| Top setup | momentum breakout | momentum breakout |

Setup type does not separate them. Three things do: **hold length, strike selection,
and ticker.**

## 3. Contract selection

| Days to expiry at entry | n | win % | $/trade |
|---|---|---|---|
| 46–90 days | 136 | 40% | +$1.49 |
| 21–45 days | 63 | 33% | **−$11** |
| 90+ days (LEAP-ish) | 42 | 21% | **−$7** |
| < 21 days | 14 | 57% | −$3 |

| Moneyness at entry | n | win % | $/trade |
|---|---|---|---|
| ITM | 31 | **58%** | +$6.56 |
| ATM (±5%) | 103 | 41% | +$6.15 (+$633 total) |
| OTM 5–15% | 85 | 33% | −$11 (−$955 total) |
| Deep OTM 15%+ | 36 | **11%** | −$20 (−$705 total) |

**Buy ATM or ITM, 46–90 DTE. Deep-OTM lottery tickets and short-dated theta-bleeders
both lose.**

## 4. Graveyard tickers

SOFI, GLXY, AMC, UNG, BMNR, AEVA, QUBT — 46 trades, **11% win rate, −$1,856 net.**
18% of all trades, in names that essentially never work. Removing these alone flips
the account from −$824 to roughly +$1,000.

## 5. Time trend (discipline decay)

| Month | n | win % | net after comm | avg hold |
|---|---|---|---|---|
| Apr | 41 | 46% | **+$591** | 5.1d |
| May | 43 | 44% | **+$754** | 4.3d |
| Jun | 61 | 43% | −$134 | 5.1d |
| Jul | 57 | 28% | **−$819** | 5.2d |
| Aug | 48 | 23% | **−$1,015** | 3.2d |

Apr–May were profitable and on-plan. Something broke in July — win rate halved, trade
count rose, and by August the average hold collapsed to 3.2 days (trading faster into
losses = tilt).

---

## The Setup / Trade Score

### Entry score — computable *before* the trade plays out

| Factor | Points |
|---|---|
| Strike ATM or ITM (≤ 5% OTM) | **+3** |
| Strike 5–15% OTM | 0 |
| Strike > 15% OTM | **−3** |
| DTE 46–90 days | **+2** |
| DTE 21–45 days | **−2** |
| DTE < 21 or > 90 days | −1 |
| Ticker not on the graveyard list | **+2** |
| Ticker on the graveyard list | **−4** |
| Setup = extended or falling knife | +1 |

### Execution score — applied at exit

| Factor | Points |
|---|---|
| Held ≥ 2 days | **+3** |
| Held < 2 days (flip) | **−4** |
| Held > 21 days (hoping) | −1 |

### Grade = entry + execution

| Grade | Total | Meaning |
|---|---|---|
| A | ≥ 6 | textbook |
| B | 2–5 | solid |
| C | −1 to 1 | marginal |
| D | ≤ −2 | bad idea, badly executed |

### Validation against the 255 trades

**Entry score alone** (no hindsight):

| Score | n | win % | net | $/trade |
|---|---|---|---|---|
| High (≥ 5) | 72 | 54% | +$1,772 | **+$24.61** |
| Mid (1–4) | 124 | 38% | −$245 | −$1.98 |
| Low (≤ 0) | 59 | **10%** | **−$2,350** | −$39.83 |

**Full grade** (entry + execution):

| Grade | n | win % | net | $/trade |
|---|---|---|---|---|
| A | 92 | 64% | +$2,240 | +$24.35 |
| B | 42 | 26% | +$333 | +$7.94 |
| C | 61 | 33% | −$1,140 | −$18.68 |
| D | 60 | **3%** | **−$2,257** | −$37.62 |

The score is monotonic and separates hard. The "low entry score" bucket (59 trades)
contains essentially the entire account loss. Not taking those trades would have left
the account up ~$1,500.

---

## Action list

1. **Minimum entry score to take the trade.** Skip anything scoring ≤ 0 on entry.
2. **No exit before day 2** unless a pre-set stop or target triggers.
3. **Blacklist the graveyard tickers.** Off the watchlist.
4. **Contract rule: ATM/ITM, 46–90 DTE.** No deep-OTM, no LEAPs, no < 3 weeks.
5. **Cap trade frequency** — profitable months ran ~40 trades, losing months ~60.
6. **Tag every trade's strategy** (dashboard dropdown) to enable setup-level tracking.
7. **Retrospective on July** — the discipline break is the highest-value thing to
   understand; the data can't explain it.

## Build note

The score can be computed in the sync (`trade_score_entry`, `trade_score_total`,
`score_reasons`) from data already stored, and shown as a column + in the expand
panel. Not yet built.
