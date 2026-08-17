"""
Rolling per-ticker mention-count baseline and the two independent spike
tests: relative spike vs. absolute floor. Either is sufficient to trigger —
no mutual corroboration required between sources.

In-memory only (unlike cooldown.py) — losing baseline history on a bot
restart just means a slower re-warm-up of "normal" for each ticker, not a
duplicate trade, so it isn't worth the persistence complexity cooldown
state needs.
"""
import time

BASELINE_WINDOW_SECONDS = 6 * 3600   # trailing window used to compute "normal"
RELATIVE_SPIKE_MULTIPLIER = 4.0      # mention count >= 4x trailing baseline
MIN_BASELINE_FLOOR = 3               # baseline below this is too thin to multiply meaningfully
ABSOLUTE_MENTION_FLOOR = 40          # mentions in one poll, regardless of baseline

# {ticker: [(timestamp, mention_count), ...]}
_history = {}


def _prune(ticker: str, now: float) -> list:
    entries = _history.get(ticker, [])
    entries = [(ts, count) for ts, count in entries if now - ts <= BASELINE_WINDOW_SECONDS]
    _history[ticker] = entries
    return entries


def record_and_check(ticker: str, mention_count: int) -> dict:
    """Records this poll's count for `ticker` and returns
    {'triggered', 'trigger_type', 'reason', 'baseline', 'mention_count'}.
    Call once per ticker per poll, after merging counts across all sources.

    A ticker with no prior history (baseline == 0, below MIN_BASELINE_FLOOR)
    can only clear the absolute floor — the relative test is meaningless
    against zero/near-zero history and is intentionally not satisfied by
    it. That's a stated limitation, not a proxy workaround: a genuinely new
    ticker needs a big enough absolute spike to trigger on its first
    appearance.
    """
    now = time.time()
    prior = _prune(ticker, now)

    baseline = (sum(c for _, c in prior) / len(prior)) if prior else 0.0
    _history.setdefault(ticker, []).append((now, mention_count))

    absolute_hit = mention_count >= ABSOLUTE_MENTION_FLOOR
    relative_hit = (
        baseline >= MIN_BASELINE_FLOOR
        and mention_count >= baseline * RELATIVE_SPIKE_MULTIPLIER
    )

    if relative_hit:
        reason = f"{mention_count} mentions vs. {baseline:.1f} baseline ({mention_count / baseline:.1f}x)"
    elif absolute_hit:
        reason = f"{mention_count} mentions >= absolute floor ({ABSOLUTE_MENTION_FLOOR}), baseline={baseline:.1f} (thin/no history)"
    else:
        reason = "no spike"

    return {
        "triggered": relative_hit or absolute_hit,
        "trigger_type": "relative" if relative_hit else ("absolute" if absolute_hit else None),
        "reason": reason,
        "baseline": round(baseline, 1),
        "mention_count": mention_count,
    }
