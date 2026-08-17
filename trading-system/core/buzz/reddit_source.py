"""
Reddit discovery source for buzz detection — SHAPED BUT DISABLED.

Reddit closed self-service API registration; new OAuth clients now go
through a manual "Responsible Builder Policy" approval process, and a
personal script-app registration attempt was confirmed rejected under this
gate (checked directly, not assumed — see docs/roadmap.md). This module
keeps the same get_candidates() interface as x_source.py so it drops into
buzz_loop.py with zero changes elsewhere once/if an approved app exists.

Enable by setting BUZZ_REDDIT_ENABLED=true and REDDIT_CLIENT_ID /
REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT in .env.
"""
import os

# Fixed subreddit list — mainstream crypto discussion plus the low-cap/meme
# communities where early buzz on speculative-tier names tends to surface
# first. Read-only monitoring only, never posts/comments.
SUBREDDITS = ['CryptoCurrency', 'CryptoMoonShots', 'SatoshiStreetBets', 'CryptoMarkets']

_warned = False


def _enabled() -> bool:
    return os.getenv("BUZZ_REDDIT_ENABLED", "").lower() in ("1", "true", "yes")


def get_candidates(lookback_hours: int = 1) -> list:
    """Same return shape as x_source.get_candidates(). Returns [] while
    disabled (the default) — callers must treat that as "no signal this
    poll", exactly like any other source outage, not as confirmed zero
    buzz."""
    global _warned
    if not _enabled():
        if not _warned:
            print("[*] Buzz: Reddit source disabled (BUZZ_REDDIT_ENABLED not set) — running X/Grok-only")
            _warned = True
        return []

    try:
        import praw
    except ImportError:
        print("[!] Buzz: BUZZ_REDDIT_ENABLED is set but praw isn't installed — pip install praw")
        return []

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")
    if not (client_id and client_secret and user_agent):
        print("[!] Buzz: BUZZ_REDDIT_ENABLED is set but Reddit credentials are missing in .env")
        return []

    try:
        from core.buzz.ticker_extraction import extract_cashtags

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
        reddit.read_only = True

        mention_counts = {}
        for sub_name in SUBREDDITS:
            subreddit = reddit.subreddit(sub_name)
            for submission in subreddit.new(limit=100):
                text = f"{submission.title} {submission.selftext}"
                for ticker in extract_cashtags(text):
                    mention_counts[ticker] = mention_counts.get(ticker, 0) + 1

        return [
            {
                "ticker": ticker,
                "estimated_mentions": count,
                "note": f"{count} mentions across {len(SUBREDDITS)} subreddits",
                "source": "reddit",
            }
            for ticker, count in mention_counts.items()
        ]
    except Exception as e:
        print(f"[!] Buzz: Reddit source error: {e}")
        return []
