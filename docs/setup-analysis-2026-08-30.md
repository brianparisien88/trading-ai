# Setup / TA Commonality Analysis — 2026-08-30

Follow-on to `trade-analysis-2026-08-30.md`. Question: every trade felt like a
good idea at entry. What actually separated the ones that would have worked from
the ones that wouldn't — at the chart level, before the outcome was known?

## Method

- **Population:** 191 of 255 closed trades (needs ≥ 20 trading days of price data
  both before and after entry).
- **"Worked" = the underlying moved ≥ +10% in the trade's direction within the
  first 20 trading days after entry** (held or not — "if you'd stayed"). Direction-
  adjusted: up for calls, down for puts.
- Sensitivity: ≥ +8% → 64% worked · ≥ +10% → **57%** · ≥ +12% → 52% · ≥ +15% → 46%.
- RS = the stock's 20-day return minus SPY's 20-day return at entry.
- Each entry bucketed as **reversal** (bottom/​top of range, against its moving
  average — intentional counter-trend), **continuation** (with the trend, above
  MAs, positive RS), or **chop** (neither).

**Headline:** 57% of trades had a real ≥ 10% move available within 20 days. The
thesis was right far more often than "1 in 3" — the earlier figure understated it
because it measured to expiry and ignored the near-term move that the "out in 2
days" habit throws away.

---

## 1. Worked vs didn't — the discriminators

| At entry | Worked (108) | Didn't (83) |
|---|---|---|
| Days to expiry on the contract | **57** | **81** |
| Range position | 65% | 73% |
| Setup Score (generic trend logic) | **1.4** | **2.2** |
| LH/LL structure (share) | 24% | 12% |
| Days actually held | **3.5** | **7.0** |
| 20-day return at entry | 9.2% | 10.9% |
| Stretch vs 20-day avg | 6.7% | 7.8% |
| RS vs SPY (20-day) | 8.2 | 7.3 |
| VIX at entry | 17.7 | 17.6 |
| Strike moneyness | 6.3% | 8.0% |

Three things separate them, and two of them are counter-intuitive:

1. **Shorter-dated contracts worked.** 57 DTE vs 81. The failures skew toward
   80–90+ day contracts. Longer expiry ≠ more chance to be right — it looks like a
   proxy for a weaker, less-timely impulse ("I'll give it room").
2. **Cleaner-looking setups worked *less*.** Higher Setup Score, less LH/LL
   structure, more mid/upper range — the "textbook continuation" entries
   underperformed. Your messier, lower-in-the-range entries did better.
3. **You cut the good ones faster.** Winners held 3.5 days, failures 7.0. Your
   instinct to bail early is firing on the *wrong* trades.

## 2. By entry type — the biggest finding

| Bucket | n | % worked | avg 20-day peak | avg net P&L |
|---|---|---|---|---|
| **Reversal / bottom-fish** | 25 | **76%** | +19% | **+$13** |
| Continuation | 100 | 54% | +18% | **−$6** |
| Chop | 66 | 53% | +18% | +$8 |

**Your reversal entries are your real edge — 76% of them had a ≥10% move within
20 days.** Your continuation entries — the bucket you use most (100 trades) — are
a coin flip and lose money on average.

## 3. What separates worked from didn't, *within* each bucket

### Reversal (bottom-fish)

| | Worked (19) | Didn't (6) |
|---|---|---|
| 60-day return at entry | **−25%** | −11% |
| Range position | **11%** | 20% |
| Days held | **2.3** | 13.9 |
| 20-day peak after entry | +23% | +4% |

The reversal trades that worked were bought **deeper in the hole** — a bigger
prior decline, still pinned at the very bottom of the range. The ones that failed
were bought *after* a bounce had already started (higher in the range, less
prior decline). And you cut the winners in 2 days while holding the failures for
two weeks — exactly backwards.

### Continuation

| | Worked (59) | Didn't (46) |
|---|---|---|
| 20-day return at entry | 23% | 21% |
| Range position | 89% | 89% |
| Above the 50-day avg | 100% | 100% |
| **Days to expiry** | **58** | **96** |
| Days held | 4.0 | 5.6 |
| avg net P&L | +$25 | **−$48** |

The entry charts are **nearly identical** — same momentum, same range position,
same trend. The one real difference is **contract length**: the winners were
~2-month contracts, the failures ~3+ months. When you commit to a tighter
contract you trade the continuation better; the long-dated ones are the bleed
(46 trades × −$48 ≈ −$2,200).

### Chop

| | Worked (30) | Didn't (31) |
|---|---|---|
| Range position | 51% | 59% |
| % off the recent low | +19% | +6% |
| Above the 50-day avg | 57% | 74% |
| Days held | 3.2 | 7.7 |

Same pattern as reversal: the chop entries that worked were bought a bit lower,
a bit further off the low; the failures were bought higher and held longer.

## 4. The big trades

**Big realized winners (net > +$100, n=17):** moderate everything — 20-day
return +8%, range 63%, 65% above the 50-day, ~60-day contracts, held ~8 days.
No extremes. Mixed buckets and structures.

**Big realized losers (net < −$100, n=15):** bought **more extended** — range
73%, already **+49% off the recent low**, +10% 20-day return — with **messy
structure** (mixed / LH-LL, only 2 of 15 clean HH/HL), then **held 15 days** as
it went nowhere (only 33% had a ≥10% move in 20 days). These are chased,
late-stage moves held in hope.

## 5. Factors that show up in *both* pools (non-discriminating)

These were basically identical for winners and losers — they don't tell you
anything at entry, so stop weighting them:

- **VIX** (17.6 vs 17.7) — confirmed again, no signal.
- **20-day return / stretch vs the moving average** — winners and losers were
  equally "up" and equally "stretched" at entry.
- **Strike moneyness** (6.3% vs 8.0% OTM) — mild at best.
- **Within continuation trades, the entire entry chart** — momentum, range,
  trend were the same for the ones that worked and the ones that didn't. The
  only lever there is the contract, not the TA.

## 6. Time to the move

Of the trades that worked, the 20-day peak arrived a **median of 15 trading
days** after entry (mean 13). Only **16 of 108** peaked within 5 days; **36 of
108** within 10 days.

**Two-thirds of your winning moves take longer than 10 trading days to develop.**
A 2-day exit rule structurally guarantees you miss most of them.

---

## Conclusions — strategy implications

**Lean into the reversal book.**
- Your bottom-fish entries work 76% of the time. Take more of them, fewer
  continuations.
- Buy them *deep* — big prior decline, still at the low. If it's already bounced
  off the bottom of the range, the edge is mostly gone (worked rate drops from
  ~90% to ~30% once it's up off the low).

**Fix the continuation book or shrink it.**
- The entry chart won't save you here — winners and losers look the same. The
  one thing you control is the contract: **~60-day expiry, not 90+**. The
  long-dated continuation trades are −$2k.
- Or just take fewer of them. It's a coin flip that costs commissions.

**Stop chasing.**
- Big losers = bought +49% off the low, upper range, messy structure, then held.
  A hard rule: if it's already run >25–30% off its recent low, don't initiate.

**The hold problem, now quantified.**
- The move takes ~15 trading days. Minimum hold of ~2 weeks on anything that
  isn't stopped out, especially reversal trades (which you currently cut in 2.3
  days despite a 76% hit rate).

**Retune the Setup Score.**
- The current generic "trend continuation" logic is anti-predictive here
  (failures scored higher). A version that rewards *reversal* structure — deep
  prior decline, bottom of range, still below MAs — would match where your edge
  actually is.
