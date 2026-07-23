# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo contains **two parallel implementations** of an AI-gated trading bot, at different stages of a migration:

- **`trading-system/`** — the current architecture. A single shared entrypoint `bot.py` takes a market-type argument (`python bot.py crypto` / `python bot.py stock`) and selects a **market adapter** (`crypto_bot/adapter.py` or `stock_bot/adapter.py`) that plugs into the shared run-loop in `core/runner.py`. Shared logic lives in `core/` (orchestrator, LangGraph agents, state-matrix builder, runner, DB, book ingestion); market-specific scanners, execution, prompts, and risk rules stay in `crypto_bot/` and `stock_bot/`.
- **`trading_bot/`** — the original, self-contained crypto bot, kept as the reference implementation until the merged crypto path is validated live. All modules live flat in one directory and import each other directly. **Slated for retirement** — don't add features here; once `python bot.py crypto` has run cleanly end-to-end, this tree should be archived/deleted.

Each of `trading_bot/` and `trading-system/` has its own `venv/`, `.env`, and `books/` (identical PDF set in both — trading knowledge base source material). Treat these as independent Python environments; don't assume a change in one is visible in the other. Sentiment/risk/rag agent logic is still duplicated between `trading_bot/agents/` and `trading-system/core/agents/` until the old tree is retired.

## Running the bots

```bat
start_bots.bat
```
Launches three terminal windows: Ollama (`ollama serve`, needed by the RAG agent), the crypto bot (`trading-system/bot.py crypto`, runs 24/7), and the stock bot (`trading-system/bot.py stock`, only active 9:30am–4:00pm EST). Both bots run from `trading-system/` with its `venv` activated.

To run a single component directly (after activating the relevant `venv`):

```bash
# trading-system/ (current architecture)
python bot.py crypto                   # live crypto bot (scanner + WS streams + orchestrator)
python bot.py stock                    # live stock day-trading bot (polling, market-hours gated)
python crypto_bot/scanner.py           # run crypto market scanner standalone
python stock_bot/scanner.py            # run stock scanner standalone
python core/orchestrator.py            # runs orchestrator against a hardcoded test trigger

# trading_bot/ (legacy crypto tree)
python bot.py                          # legacy live crypto bot
python backtest.py                     # 2-year backtest over BTC/ETH/SOL using Yahoo Finance data
python ingest_books.py ingest <book>   # embed one book (murphy|douglas|elder|weinstein|oneil) into Supabase
python ingest_books.py query           # sample RAG query against the ingested knowledge base
python trigger_log.py                  # print all logged triggers
python execution.py                    # print paper trading performance summary
python test_imports.py                 # sanity-check all third-party imports resolve
```

There is no configured test runner, linter, or formatter in either tree (`pytest` is present in `requirements.txt` as a transitive dependency, but no test suite exists). Verification is done via each module's `if __name__ == "__main__"` block against hardcoded sample data, or via `backtest.py`.

Dependencies: `pip install -r requirements.txt` inside the relevant tree's venv (only `trading_bot/requirements.txt` currently exists; mirror it for `trading-system/` if needed).

Both `.env` files hold live secrets (Kraken, Alpaca, Anthropic, xAI, Gemini, DeepSeek, Supabase, NewsAPI keys) and are gitignored — never print or log their contents.

## Architecture: the trigger → orchestrator → execution pipeline

Both markets follow the same shape:

1. **Scanner** (`crypto_bot/scanner.py` / `stock_bot/scanner.py`) periodically screens a universe of symbols (top-volume Kraken USD pairs for crypto; a fixed `STOCK_UNIVERSE` list for stocks) for a technical setup (RSI + MACD confluence for crypto; ORB + VWAP + volume momentum for stocks) and produces a watchlist.
2. **Live loop** (`core/runner.py`, driven by the market adapter) — the crypto adapter uses the runner's **stream loop** (`ccxt.pro` websocket candles per watchlisted symbol, buffer management, indicator recompute via `compute_indicators`, adapter-supplied signal thresholds); the stock adapter uses the **poll loop** (market-hours/ORB gating, scanner call every 5 minutes, max 3 setups per scan). When a signal fires, a `state_matrix` dict is built via `core/state_matrix.py` (session id, ticker, `quant_trigger`, `market_metrics`, and for stocks `momentum_metrics`/`catalyst` extras) and dispatched to the orchestrator via `run_in_executor` so the async event loop isn't blocked. After the orchestrator returns, the runner hands the decision to the adapter's execution module.
3. **Orchestrator** (`orchestrator.py` / `core/orchestrator.py`) is a LangGraph `StateGraph` with four sequential nodes:
   - `sentiment_agent` — pulls recent news via NewsAPI, scores polarity with TextBlob, flags "trap" conditions (extreme sentiment against a signal's direction).
   - `rag_agent` — embeds the setup as a query (Ollama `nomic-embed-text`), searches a Supabase pgvector collection (`trading_knowledge`, seeded by `ingest_books.py` from the trading books in `books/`) for supporting passages, and validates the setup against bullish/bearish keyword counts.
   - `risk_agent` — hardcoded, non-LLM risk rules with **absolute veto power**: position sizing, stop loss/take profit, sentiment score bounds, RSI/volume/VWAP/ORB/catalyst-strength checks depending on market. If this rejects, or if the RAG agent doesn't validate, execution routes straight to an `abort` node and skips the LLM entirely.
   - `claude_gatekeeper` — only reached if risk + RAG both pass. Sends a structured brief (setup, sentiment, RAG findings, risk/position numbers) to Claude and requires a strict `EXECUTE: TRUE`/`EXECUTE: FALSE\nREASON: ...` response; falls back to a rule-based decision if the API call fails.
   - In `trading-system`, node behavior branches on a `prompt_type` field (`'crypto'` vs `'stock'`) injected into the state matrix, which selects the risk-rule module and the Claude prompt template (`crypto_bot/prompts.py` vs `stock_bot/prompts.py`).
4. **Execution** (`execution.py` / `stock_bot/execution.py`) only fires on `EXECUTE: TRUE`. This is **paper trading only** (`PAPER_TRADING = True`) — crypto submits a Kraken order with `params={'validate': True}` (validates without executing); stocks simulate locally pending Alpaca/IBKR wiring. Every trade, executed or not, is appended to a JSON log (`logs/paper_trades.json`, `logs/stock_paper_trades.json`) alongside a running paper balance.
5. **Trigger logging** (`trigger_log.py` / `core/trigger_log.py`) appends every enriched `state_matrix` (regardless of final decision) to `logs/triggers.json` for later review/backtesting.

When editing risk logic, remember it is intentionally hardcoded and treated as a hard veto — don't route around it or let the LLM override a risk rejection.
