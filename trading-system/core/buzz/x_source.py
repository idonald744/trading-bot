"""
X/Grok discovery source for buzz detection — the only active source today
(see reddit_source.py for why Reddit isn't wired in yet).

Unlike core/agents/sentiment_agent.py's get_sentiment_grok(), which asks
Grok about ONE already-known ticker, this issues an open-ended discovery
query — "what's spiking right now" — since the whole point of buzz
detection is surfacing tickers nobody asked about yet.

Mention counts are Grok's own estimate of X activity, not a verified raw
count (same caveat that already applies to items_analyzed in
sentiment_agent.py). velocity.py's thresholds are chosen with that noise in
mind, not against a ground-truth firehose.
"""
import json
import os


def get_candidates(lookback_hours: int = 1) -> list:
    """Returns [{'ticker', 'estimated_mentions', 'note', 'source'}, ...] for
    whatever Grok reports as elevated crypto-ticker chatter on X in the
    lookback window. Returns [] if the source is unavailable for any reason
    (missing key, API error, malformed response) — callers must treat that
    as "no signal this poll", not as "confirmed zero buzz"."""
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return []

    try:
        from openai import OpenAI
        xai_client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

        prompt = f"""Look at X/Twitter activity from crypto traders and influencers over
the last {lookback_hours} hour(s). Identify cryptocurrency tickers that are seeing an
unusual SPIKE in mention volume or post velocity right now — tickers suddenly getting
a lot more attention than their normal baseline, not just tickers that are generally
popular (BTC/ETH being mentioned constantly is not a spike).

Respond with ONLY a JSON object in this exact shape:
{{
  "candidates": [
    {{
      "ticker": <string, the coin's ticker symbol e.g. "SOL">,
      "estimated_mentions": <int, your best estimate of post/mention count in the window>,
      "note": <short string, why this looks like a spike>
    }}
  ]
}}
Return an empty "candidates" list if nothing looks like a genuine spike."""

        response = xai_client.chat.completions.create(
            model="grok-3",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        candidates = result.get("candidates", [])
        return [
            {
                "ticker": str(c.get("ticker", "")).strip(),
                "estimated_mentions": int(c.get("estimated_mentions", 0)),
                "note": c.get("note", ""),
                "source": "x",
            }
            for c in candidates if c.get("ticker")
        ]
    except Exception as e:
        print(f"[!] Buzz: X/Grok discovery query failed: {e}")
        return []


if __name__ == "__main__":
    print("[*] Testing X/Grok buzz discovery...")
    print(json.dumps(get_candidates(), indent=2))
