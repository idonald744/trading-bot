# Blueprint reference (condensed)

Source: `MASTER_ARCHITECTURAL_BLUEPRINT.md` (2700+ line institutional-grade spec for a
Rust/Kafka/HMM regime-adaptive multi-asset trading system). That document was **not**
adopted as an implementation target — it describes a colocated-HFT-adjacent
institutional architecture (Rust hot path, Redpanda/Kafka event bus, TimescaleDB,
Avellaneda-Stoikov market making, HMM/MS-GARCH regime classifier, Almgren-Chriss
execution slicing) that is far beyond the scope and needs of this project: a
single-machine, paper-trading, LLM-gated day-trading bot. Rebuilding on that
infrastructure would be pure premature complexity with no corresponding benefit here.

The four pieces below *are* genuinely applicable and worth keeping in mind as this
project matures. Nothing below implies any of it is implemented yet.

## 1. Explicitly rejected assumptions

The blueprint calls these out as claims/assumptions a trading system must not encode
as fact. They're good defensive-thinking checks regardless of architecture:

- Vendor win rates, Sharpe ratios, annual returns, or "AI success rates" that are not
  independently audited.
- That Hummingbot or any retail cloud bot is true colocated HFT.
- That high volatility is automatically favorable for market making.
- That DCA is safe because its historical win rate is high.
- That a placement acknowledgment is a fill.
- That local state remains correct after a WebSocket gap.
- That market orders guarantee acceptable execution.
- That a regime classifier predicts turning points.
- That an LLM confidence score is a calibrated probability.
- **That paper trading validates production behavior.** (directly relevant — this
  codebase is paper-trading only right now)

## 2. Reconciliation-against-broker-truth principle

> Exchange/broker order state > local durable ledger > in-memory state > cache
> Exchange/broker position state > local calculated position

Local state exists for speed/convenience, but the venue (Kraken/Alpaca) is always the
authoritative source of truth for what actually happened. Practically: any P&L or
position-closing logic we build should treat our JSON logs as a *record of intent and
observation*, not as ground truth — if we ever move past paper trading, reconcile
against actual broker/exchange state before trusting local numbers.

## 3. Candidate future signal upgrades

Two formulas from the blueprint's feature engine that could be reasonable additions to
`compute_indicators` / scanner logic later, if the current RSI+MACD / ORB+VWAP setups
need a sharper edge-detection signal. Not implemented, no current plan to implement —
just worth having on file.

**Z-score** (standardized deviation from a rolling mean — useful for mean-reversion or
"is this move statistically unusual" checks):

```
z_t = (P_t - mu_t,N) / (sigma_t,N + epsilon)
```

**Robust jump score** (median/MAD-based, more outlier-resistant than a plain z-score —
useful for flagging abnormal single-bar moves, e.g. news spikes):

```
J_t = |r_t - median(r_[t-N:t])| / (1.4826 * MAD(r_[t-N:t]) + epsilon)
```

where `r_t` is the log return at time t, and MAD is the median absolute deviation over
the trailing N-bar window. The 1.4826 constant scales MAD to be a consistent estimator
of standard deviation under normality.

## 4. Stale-data-disables-new-exposure principle

Core safety invariant: **if you can't trust the data, you don't open new risk.**
From the blueprint's invariant list:

- `INV-005` — New exposure is forbidden when venue state is not trading-ready.
- `INV-006` — New exposure is forbidden when book/data staleness exceeds threshold.

And from the book-validity state machine: no new exposure is allowed while the market
data feed is in `AwaitingSnapshot`, `Desynced`, `Stale`, `Disconnected`, `Backoff`, or
`SafeMode`. Existing positions can still be managed/closed/flattened during these
states — it's specifically *new* exposure that's blocked.

This is directly applicable to both bots today: if a WebSocket stream drops, a scanner
poll fails, or a price feed goes stale, that should block new trade entries even if the
rest of the pipeline (sentiment/RAG/risk/Claude) would otherwise say "go." It should
not block us from recognizing an existing paper position needs to be closed.
