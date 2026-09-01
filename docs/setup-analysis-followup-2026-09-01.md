# Setup Analysis — Follow-up & Named Examples (2026-09-01)

Answers to the review questions on `setup-analysis-2026-08-30.md`, with specific
trades. Re-run against **213 analyzable closed trades** (needs full 3-month price
history before entry + 20 trading days after). "Worked" = ≥ +10% favourable move
in the underlying within 20 trading days of entry. All `setup_score` values are
the **current intent-aware v2 score** (the original doc quoted the older v1 —
that's why some numbers shifted).

---

## Definitions you asked about

**Range position** — where the stock sat inside its **high–low range over the ~3
months (63 trading days) before entry**. `0%` = at the 3-month *low*, `100%` = at
the 3-month *high*, `50%` = the midpoint. For puts it's flipped (`0%` = at the
high). "Mid/upper range" = you bought near recent highs; "low in the range" = you
bought closer to the recent lows.

**Continuation vs Chop vs Reversal** — the buckets are:

| bucket | definition (measured at entry) |
|---|---|
| **reversal** | range ≤ 35% **and** below the 20-day average — beaten down, still under its mean |
| **continuation** | range ≥ 55% **and** above the 20-day average **and** outperforming SPY over the last 20 days — with the trend, near highs, leading the market |
| **chop** | **everything else** — a residual bucket, not a positive definition |

So "chop" ≈ *no clear directional posture at entry*: mid-range entries, or
above-the-average but lagging SPY, or mixed signals. Its ~55% worked / −$3 avg is
basically the base rate, which is what you'd expect from a catch-all.

**"40% off the low"** — off the **3-month low** (lowest close in the ~63 trading
days before entry), **not** the 52-week low. Banded (see §5), the 52-week figure
is shown alongside for the named trades.

---

## 1. Win rate by Setup Score — the score is not a reliable filter

| setup_score | n | worked | avg P&L | avg 20d peak |
|---|---|---|---|---|
| ≤ 0 (weak) | 86 | 56% | −$9 | — |
| 1–3 (ok) | 80 | **66%** | **+$10** | — |
| ≥ 4 (strong) | 47 | **47%** | −$1 | — |
| — score 3 | 12 | 92% | +$25 | +40% |
| — score 4 | 18 | 39% | +$9 | +12% |
| — score 6 (max) | 22 | 45% | −$12 | +14% |

The middle tier (1–3) outperforms the top tier (≥4) on both hit-rate and P&L, and
the maximum score (6) worked only 45%. This is the same anti-predictive pattern
the first doc flagged, and it **persists in the retuned v2 score**. Treat the
numeric Setup Score as weak signal — directionally useful at the structure
extremes, not trustworthy as a cutoff.

---

## 2. "Cleaner setups worked less" — confirmed

Testing the raw claim directly (independent of the score):

| entry profile | n | worked | avg P&L |
|---|---|---|---|
| **messy + low in range** (range ≤ 45, LH/LL or mixed structure) | 50 | **70%** | +$17 |
| **clean + high in range** (range ≥ 60, HH/HL or flat structure) | 59 | **51%** | +$14 |

### 2a. Clean, high-score setups that FAILED (score ≥ 3, HH/HL / flat, range ≥ 55)

| trade | score | struct | range | off 3m-low | held | P&L | return |
|---|---|---|---|---|---|---|---|
| AFRM 28 May → 5 Jun C | 4 | HH/HL | 100% | +72% | 7.9d | **−$304** | −46% |
| MTCH 15 Jul → 22 Jul C | 4 | HH/HL | 100% | +19% | 6.7d | −$82 | −43% |
| GLXY 26 May → 28 May C | 6 | HH/HL | 84% | +73% | 2.0d | −$76 | −29% |
| NUAI 30 Jun → 1 Jul C | 6 | HH/HL | 90% | +73% | 1.0d | −$61 | −41% |
| ARKF 4 May → 5 May C | 3 | flat | 89% | +19% | 1.2d | −$60 | −24% |
| TSSI 22 Apr → 24 Apr C | 4 | HH/HL | 100% | **+121%** | 1.7d | −$59 | −25% |

Common thread: bought at 84–100% of range (right at the highs), often stretched
far off the low, then **cut in ≤ 2 days**.

### 2b. Clean, high-score setups that WORKED (score ≥ 3, HH/HL / flat)

| trade | score | struct | range | off 3m-low | held | P&L | return |
|---|---|---|---|---|---|---|---|
| SMCI 26 May → 3 Jun C | 4 | HH/HL | 100% | +81% | 8.0d | **+$503** | +124% |
| RIOT 20 Jul → 23 Jul C | 3 | HH/HL | 31% *(reversal)* | +25% | 3.0d | +$355 | +208% |
| EOSE 2 Jun → 9 Jun P | 3 | HH/HL | 0% *(reversal)* | — | 7.1d | +$185 | +92% |
| AEVA 1 May → 15 May C | 5 | HH/HL | 96% | +45% | 13.9d | +$133 | +60% |
| MSTR 12 Jun → 16 Jun C | 4 | HH/HL | 11% *(reversal)* | +7% | 4.0d | +$116 | +14% |
| CIFR 29 Apr → 12 May C | 6 | HH/HL | 66% | +41% | 12.7d | +$99 | +45% |

Common thread: the clean-setup winners either **held 8–14 days** or were actually
**reversal** entries (low range). Only SMCI is a genuine "bought the breakout,
held ~8 days, paid" case.

---

## 3. Cut too quick vs held too long

### 3a. Cut too quick — held ≤ 3 days, a real move still available afterward

Ranked by favourable move from *your exit to the contract's expiry* (`if held`),
which is the honest "you left this on the table" number:

| trade | held | your P&L | move after exit → expiry | 20d peak (from entry) |
|---|---|---|---|---|
| BLSH 3 Aug → 6 Aug C | 3.0d | +$6 | **+45%** | +48% at t+19d |
| CIFR 20 May → 21 May C | 1.1d | +$30 | **+36%** | +50% at t+20d |
| SMCI 20 May → 21 May C | 1.0d | −$63 | +11% | +50% at t+8d |
| NVTS 15 Apr → 15 Apr C | 0.1d | −$11 | +8% | +121% at t+18d (round-tripped) |

Caveat: several "big peak, small if-held" trades (ENPH, SPCE, NVTS) **spiked then
gave it all back** — the peak was real but not capturable without a mid-trade
exit. BLSH and CIFR are the clean cases where simply holding longer paid.

### 3b. Held too long — held > 14 days, lost money, thesis never developed

| trade | held | P&L | return | 20d peak |
|---|---|---|---|---|
| AEVA 1 Jul → 7 Aug C | **37d** | **−$409** | −72% | −13% (t+2d) |
| QUBT 24 Jun → 17 Jul C | 23d | −$241 | −227% | +2% (t+3d) |
| RIVN 2 Jul → 31 Jul C | 29d | −$208 | −93% | +8% (t+1d) |
| GME 4 May → 17 Jul C | **74d** | −$197 | −101% | +6% (t+2d) |
| FCX 12 May → 28 May C | 16d | −$189 | −44% | +9% (t+14d) |
| UBER 26 Jun → 13 Jul C | 17d | −$109 | −25% | −1% (t+1d) |

Every one peaked at ≤ +9% favourable — the move never came, and they were held
15–74 days anyway. These are the "lottery ticket to expiry" trades.

---

## 4. Buckets — top 5 / worst 5, and the reversal-sample question

### Reversal (n = 23, worked 78%, avg P&L +$34)

**Top 5:** RIOT +$355 · SLV +$268 · EOSE(P) +$185 · WEAT +$162 · MSTR +$116
**Worst 5:** KTOS −$119 (held 0.1d) · NFLX −$90 (1.0d) · GLXY(P) −$64 (0.1d) ·
GLXY −$54 (0.1d) · RBLX −$47 (0.9d)

**Every one of the 5 reversal losses was cut in ≤ 1 day**, and 4 of the 5 still
had a +10–28% move available in the next 20 days. The reversal book's failures
are an *execution* problem (bailing instantly), not a *setup* problem.

**Will the 78% hold as sample size grows?** Honest answer — maybe not.
- 18/23 = 78%, but the 95% confidence interval is **~61% – 95%** (small N).
- Split chronologically: first half 7/11 (64%), second half 11/12 (92%) — it has
  *improved*, not regressed, but that's 12 trades.
- The "deep" subset (range ≤ 15%, right on the floor): 16/18 = 89%.
- **Plan around ~65% and rising with size, not 78%.** Even the lower CI bound
  (61%) beats continuation (56%), so it's probably a real edge — just don't
  bank the headline number.

### Continuation (n = 102, worked 56%, avg P&L −$6)

**Top 5:** SMCI +$503 · AAPL +$368 · ZETA +$249 · APLD +$161 · HIMS +$137
**Worst 5:** AEVA −$409 · AFRM −$304 · RIVN −$208 · POET −$132 · HIMS −$116

Losers were bought far more extended — POET **+203%** off the 3-month low, AEVA
**+122%**, RIVN +44%, AFRM +72%.

### Chop (n = 88, worked 55%, avg P&L −$3)

**Top 5:** ENPH +$603 · RIOT +$220 · RIOT +$196 · GLXY +$142 · BNO +$123
**Worst 5:** QUBT −$241 · GLXY(P) −$203 · HOOD −$200 · GME −$197 · FCX −$189

The chop *winners* mostly have low range positions (32%, 41%, 32%) — i.e. they're
really reversal-ish trades that the bucket rule put in "chop" because a secondary
condition (below-MA, or negative RS) wasn't met. Reinforces that "chop" is a
fuzzy residual.

---

## 5. "Contract length is the continuation lever" — you were right to push back

Re-run with the fuller data, the entry charts are **not** identical:

| continuation subset | n | worked | avg P&L | entry: 20d return | off 3m-low | rel. strength vs SPY | setup score |
|---|---|---|---|---|---|---|---|
| short-dated (≤ 65 DTE) | 68 | 57% | −$4 | +19% | +56% | +16 | 2.2 |
| long-dated (≥ 85 DTE) | 19 | **32%** | **−$38** | **+32%** | +59% | **+27** | **0.8** |

The long-DTE continuation trades were bought **more extended** (+32% vs +19% over
20 days), with **higher relative strength** (chasing a hotter move: +27 vs +16),
and a **lower setup score** (0.8 vs 2.2). Contract length isn't the cause — it's a
**tell that you bought later into a run**. Consistent with your own read: you
reach for a longer contract when you're less sure of the timing, and "less sure"
correlates with a worse entry.

Also: within continuation, **winners held longer than losers** (6.2d vs 4.4d),
and winners' contracts averaged 58 DTE vs losers' 92. Nothing here says "hold to
expiry" — it says *the trades that gapped against you early were the ones you'd
bought a long contract for and the ones you bailed on fastest.*

The actionable rule isn't "buy 2-month contracts." It's: **don't initiate a
continuation trade on a move that's already run > ~25% over 20 days or sits > 40%
off its 3-month low** — and if you do, size it small, because (next section) the
downside is asymmetric.

---

## 6. "Avoid stocks up 40% off the low" — refined

Banded by how far above the 3-month low the stock was at entry:

| off 3-month low | n | worked | avg P&L |
|---|---|---|---|
| 0–25% | 65 | 46% | +$11 |
| 25–40% | 30 | 53% | +$14 |
| **40–60%** | 35 | **69%** | **−$9** |
| **60%+** | 44 | **66%** | **−$15** |

The twist: buying extended did **not** lower the "worked" rate — a move was often
still available. But the **average P&L is negative** past +40%, because when these
fail they fail hard. The eight worst extended trades:

| trade | off 3m-low | off 52wk-low | P&L | return | 20d peak |
|---|---|---|---|---|---|
| AEVA 1 Jul C | +122% | +203% | −$409 | −72% | −13% |
| AFRM 28 May C | +72% | +72% | −$304 | −46% | +9% |
| QUBT 24 Jun C | +54% | +54% | −$241 | −227% | +2% |
| RIVN 2 Jul C | +44% | +49% | −$208 | −93% | +8% |
| POET 3 Jun C | **+203%** | +269% | −$132 | −47% | +1% |
| HIMS 4 Jun C | +78% | +93% | −$116 | −29% | +37% |
| AEVA 22 Jun C | +117% | +175% | −$112 | −28% | +14% |
| CIFR 14 May C | +86% | +86% | −$106 | −38% | +18% |

**Rule:** past +40% off the 3-month low, either pass, or take a small position
with a hard stop — the payoff is negatively skewed (capped upside, fat left tail).

---

## 7. Time to develop — confirmed and a bit stronger

Of the 123 trades where a ≥ 10% move was available:

| peaked within | count | share | → took longer |
|---|---|---|---|
| 3 trading days | 8 | 7% | 93% |
| 5 trading days | 18 | 15% | 85% |
| 10 trading days | 43 | **35%** | **65%** |
| 15 trading days | 65 | 53% | 47% |

Median time to the peak: **14 trading days** (mean 13.2). **Yes — ~2/3 of your
winning moves take longer than 10 trading days.** By bucket: reversal median
**18 td**, continuation and chop median 14 td.

Allowing **15 trading days (~3 calendar weeks)** for a trade to develop is right,
and **reversal trades need closer to 4 weeks**. A 2-day exit rule is structurally
incompatible with this.

---

## Bottom line (updated from the first doc)

1. **Setup Score isn't a usable filter** — the ≥4 tier worked *less* than the 1–3
   tier. Don't gate entries on it.
2. **Reversal is probably a real edge but 78% is optimistic** — plan around ~65%,
   improving with size; its losses are almost entirely same-day panic exits.
3. **The continuation "DTE lever" was a mis-read** — long contracts flag *chasing
   an extended move*, they don't cause the loss. The real filter is
   extension-at-entry (> 25% / 20d, > 40% off the low).
4. **Extended entries: not a lower hit-rate, a worse skew** — cap size, hard stop.
5. **Hold ~15 trading days minimum** (≈ 3 weeks), ~4 weeks for reversals.
6. **Held-too-long losers all peaked ≤ +9%** — if it hasn't shown you anything by
   ~t+10, it isn't going to; that's the time-stop, not "ride it to expiry."
