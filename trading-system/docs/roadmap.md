# Roadmap

Last updated: 2026-08-17

## COMPLETED (this and prior sessions)

- Crypto/stock bot merge — shared `core/` runner, orchestrator, state-matrix builder behind a single `bot.py` entrypoint with per-market adapters (`crypto_bot/adapter.py`, `stock_bot/adapter.py`).
- Moomoo SIMULATE integration.
- Grok/X sentiment with divergence reconciliation (against the existing NewsAPI/TextBlob sentiment agent).
- Stock scanner speed rewrite.
- Asset-tier classification — crypto market-cap tiers and stock cap/float/short-interest tiers, feeding position sizing.
- Position-monitor / P&L closing logic, including the direction-aware stop/target bug fix for short paper trades.
- Dashboard — read-only Streamlit log viewer, wired to real balance/status.
- Crypto tier-based position sizing wired into execution — `crypto_bot/execution.py`'s `execute_paper_trade()` was silently ignoring the risk agent's tier-computed position (`state_matrix['risk_evaluation']['position']`) and recomputing its own flat 2%/1.5%/3% sizing for every trade regardless of asset tier. Found and fixed while building buzz detection, since tier-based sizing was the load-bearing safety assumption for routing buzz triggers through the risk agent unchanged. (`stock_bot/execution.py` was already correct — only the crypto side had drifted.)
- Buzz detection pipeline, phase 1 (X/Grok-only) — see below.

## IN PROGRESS — Buzz detection pipeline

New discovery mechanism in `core/buzz/`, additive alongside the existing scanners (no changes to `crypto_bot/scanner.py`). Crypto-only, buy-only, for now.

**Built and wired in tonight:**
- `core/buzz/x_source.py` — live. Open-ended Grok/X discovery query per poll ("what's spiking," not a per-ticker lookup) — the actual discovery mechanism, since a targeted per-ticker query can only catch tickers already anticipated.
- `core/buzz/reddit_source.py` — shaped, disabled by default. Reddit closed self-service API registration; a personal script-app registration attempt was confirmed rejected under the new "Responsible Builder Policy" manual-approval gate (checked live against current reporting, not assumed from memory — self-service used to work but no longer does). Same `get_candidates()` interface as `x_source.py`, so it activates with zero pipeline changes if/when approval comes through. User is separately evaluating whether to file the approval request.
- `core/buzz/ticker_extraction.py` — validates candidate tickers against Kraken's actual tradable USD bases; also holds `extract_cashtags()` for when Reddit's raw post text comes online.
- `core/buzz/velocity.py` — pure statistical baseline (relative spike vs. trailing mention-count average, OR absolute floor), not a keyword/catalyst-type approach — deliberate, since the point is catching tickers before anyone knew to look for them. A ticker with no prior baseline can only clear the absolute floor; this is a stated limitation, not worked around with a proxy signal.
- `core/buzz/cooldown.py` — per-ticker dedup window persisted to `logs/buzz_cooldowns.json`, same restart-safety lesson as the paper-trade-history fix.
- `core/buzz/buzz_loop.py` — poll loop, wired into `core/runner.run()` via `adapter.buzz_enabled` (set on `CryptoAdapter` only), runs independent of market hours.
- Risk agent (`core/agents/risk_agent.py`) — Rule 6 (RSI extreme-check) skipped for `signal_source == 'buzz'`; tier sizing and all sentiment/trap/stable-pegged rules apply unchanged.
- RAG hard-gate (`core/orchestrator.py`'s `should_continue_after_risk`) — skipped for buzz triggers too: the textbook corpus has no coverage of social-mention-velocity as a methodology, so it would reliably fail to validate a setup it structurally can't recognize. RAG passages still run and still reach Claude's brief — informational, not a veto, for buzz specifically.
- Claude gatekeeper framing (`crypto_bot/prompts.py`) — distinct "SOCIAL-MOMENTUM-DRIVEN (no technical confirmation)" setup section vs. the existing "TECHNICALLY-CONFIRMED" one, branched on `signal_source`.

**Live-tested end-to-end (2026-08-17):** a real single-poll run against live X/Grok returned a genuine, clean, zero-candidate result (verified as a real empty response, not a swallowed error) — confirming the graceful-zero-result path. A manually-constructed synthetic buzz trigger (real price/fundamentals for SOL, clearly labeled synthetic in `buzz_metrics`) was then dispatched through the real orchestrator end-to-end — live sentiment agent, live RAG/Supabase, live risk agent (correct `blue_chip` tier sizing, RSI-rule skip confirmed working live), and a live Claude gatekeeper call that correctly read the synthetic labeling and returned a reasoned `EXECUTE: FALSE`. Found and fixed during this testing: a Windows async-DNS failure in the new buzz code (missing the Google-DNS-resolver workaround `crypto_bot/adapter.py` already has) and a rotated-but-shadowed `ANTHROPIC_API_KEY` (see DEFERRED/KNOWN GAPS below).

**Not yet done:**
- A real (non-synthetic) buzz trigger has not yet fired and run through the full pipeline — X/Grok hasn't reported a genuine spike during testing so far.
- Reddit source activation, pending the Responsible Builder Policy decision.

## IN PROGRESS — Leaderboard / copy-trading

Track known high-performing Solana wallets and mirror their buy activity, with its own dedicated safety layer distinct from the existing risk agent — specific risk that a copied wallet may be selling into the exact pump being copied. Scoped as architecturally significant, comparable to the original crypto/stock merge and the asset classifier.

**Researched (2026-08-17, checked against live docs, not memory):** Helius vs. Birdeye for real-time Solana wallet tracking without running our own RPC node. Helius: free tier 1M credits/month, 10 req/sec, webhooks included at no charge — `accountAddresses`-based HTTP push webhooks are purpose-built for watching a list of wallets, confirmed via a real captured payload. Birdeye: free tier is materially more limited (1 req/sec, described by Birdeye itself as "Limited" access); has both a historical REST wallet-tx endpoint and a real-time `SUBSCRIBE_WALLET_TXS` WebSocket, but tier-gating/limits on the wallet-specific endpoints aren't documented publicly. Helius is the stronger primary candidate.

**Built and self-tested:**
- `core/leaderboard/position_accumulator.py` — the "still accumulating" safeguard gate. Per-(wallet, token_mint) ledger of signed raw-token-count buy/sell events over a rolling 24h window; passes only if net position is positive (still net-accumulating this token, not distributing into the pump being copied). Raw token-count weighting only (no USD estimation) — an accepted, explicitly-commented limitation, not an oversight. Cold-start fail-closed is keyed on *wallet tracking duration* (`wallet_tracking_started_at`), not per-token history — a wallet's first-ever buy of a given token is the normal case this feature exists to catch and passes on its own merits; the actual risk is not having observed the wallet long enough yet to trust the window. Both the ledger and tracking-start timestamps persist to `logs/wallet_position_ledger.json`, same restart-safety pattern as buzz's cooldown store.

**Drafted, not yet finalized:**
- A Helius enhanced-webhook payload parser (`parse_buy_event`) — draft logic exists for turning one `accountData` transaction into a structured buy event (SOL/WSOL/stable spent → non-quote token received, fee-noise-floored, largest-non-quote-delta picked as "bought" token). Field names need verification against a real captured payload before this is trustworthy — a first real payload was captured and confirmed the general shape (`accountData` / `nativeBalanceChange` / `tokenBalanceChanges`), but the draft parser hasn't been checked line-by-line against it yet.
- A matching state-matrix shape (`signal_source: 'wallet_copy'`, `wallet_metrics` extras block), following the same `build_state_matrix()` convention as buzz detection.

**Known gaps, not yet addressed:**
- This is an inbound webhook, not a poll loop — needs a real, continuously-running, publicly reachable HTTP receiver, which is new infrastructure this codebase doesn't have (everything else is outbound poll/websocket-subscribe).
- `crypto_bot/execution.py` is Kraken-only (`exchange.create_order()` against Kraken pairs). Most tokens a Solana whale buys early won't be listed on Kraken at all — even a fully-approved copy-trade currently has nowhere to execute, paper or otherwise.
- `ticker` in the state matrix is a raw base58 mint address until a mint→symbol/price/fundamentals resolution step exists (Birdeye is the natural source, since CoinGecko won't have fresh meme coins indexed).
- The accumulator only has buy events to work with so far — a symmetric sell-event parser doesn't exist yet, so net position can currently only go up, not down, until that's built.
- Position-size ceiling, confirmation delay, and the dedicated safety layer itself (distinct from `core/agents/risk_agent.py`) are still unscoped.

## DEFERRED / KNOWN GAPS

- Broker-reconciled fills (phase 2) — currently estimated from polled price only.
- Z-score / jump-score signal upgrades, from blueprint mining.
- Options / ETF / index support.
- Live trading unlock — deliberately untouched (paper trading only).
- `load_dotenv()` calls throughout the codebase (`bot.py`, all agent/execution modules) don't pass `override=True`, so a rotated `.env` secret can be silently shadowed by a pre-existing ambient environment variable in unusual shell environments (found during buzz-detection live testing — an already-set `ANTHROPIC_API_KEY` in the test shell kept the old, revoked key active after rotation). Low risk in normal `bot.py` launches via `start_bots.bat`, where that ambient var shouldn't already exist; worth fixing deliberately in its own pass later rather than bundled into an unrelated commit.
