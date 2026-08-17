"""
Per-ticker cooldown/dedup window, persisted across restarts — same lesson
as the paper-trade-history restart bug: state that resets silently on
restart is its own class of bug, not a neutral default.
"""
import json
import os
import time

COOLDOWN_FILE = "logs/buzz_cooldowns.json"
COOLDOWN_SECONDS = 6 * 3600  # don't re-trigger the same ticker within 6 hours

_last_triggered = {}
_loaded = False


def _load():
    global _last_triggered, _loaded
    if _loaded:
        return
    _loaded = True
    if not os.path.exists(COOLDOWN_FILE):
        return
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    now = time.time()
    _last_triggered = {
        ticker: ts for ticker, ts in raw.items()
        if now - ts <= COOLDOWN_SECONDS
    }


def _save():
    os.makedirs(os.path.dirname(COOLDOWN_FILE), exist_ok=True)
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(_last_triggered, f, indent=2)


def is_on_cooldown(ticker: str) -> bool:
    _load()
    last = _last_triggered.get(ticker)
    return last is not None and (time.time() - last) <= COOLDOWN_SECONDS


def mark_triggered(ticker: str) -> None:
    _load()
    _last_triggered[ticker] = time.time()
    _save()
