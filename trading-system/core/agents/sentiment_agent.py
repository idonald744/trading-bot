import os
import json
from newsapi import NewsApiClient
from textblob import TextBlob
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

newsapi = NewsApiClient(api_key=os.getenv("NEWS_API_KEY"))

# Ticker to search term mapping
TICKER_KEYWORDS = {
    "BTCUSDT": "Bitcoin BTC",
    "ETHUSDT": "Ethereum ETH",
    "SOLUSDT": "Solana SOL",
    "BTCUSD": "Bitcoin BTC",
    "ETHUSD": "Ethereum ETH",
    "SOLUSD": "Solana SOL"
}

# Two independent sources normally land within ~2 points of each other on the
# 1-10 scale; a 3+ point gap means they're describing different crowds.
DIVERGENCE_THRESHOLD = 3

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
RANK_CONFIDENCE = {0: "low", 1: "medium", 2: "high"}


def analyze_sentiment(text: str) -> float:
    """Returns polarity score: -1.0 (negative) to 1.0 (positive)"""
    return TextBlob(text).sentiment.polarity


def _regime(score: int) -> str:
    if score <= 3:
        return "fear"
    if score >= 8:
        return "greed"
    return "neutral"


def _get_sentiment_newsapi(ticker: str, direction: str) -> dict:
    search_term = TICKER_KEYWORDS.get(ticker, ticker)

    # Fetch last 7 days of news (free tier works better with wider window)
    from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    try:
        articles = newsapi.get_everything(
            q=search_term,
            from_param=from_date,
            language='en',
            sort_by='publishedAt',
            page_size=20
        )
        headlines = [
            a['title'] for a in articles.get('articles', [])
            if a.get('title')
        ]
    except Exception as e:
        print(f"[!] NewsAPI error: {e}")
        headlines = []

    if not headlines:
        return {
            "sentiment_score": 5,
            "smart_money_signal": "neutral",
            "retail_signal": "neutral",
            "key_narratives": ["No recent news found"],
            "trap_warning": False,
            "trap_reason": None,
            "confidence": "low",
            "items_analyzed": 0
        }

    # Analyze sentiment of each headline
    scores = [analyze_sentiment(h) for h in headlines]
    avg_score = sum(scores) / len(scores)

    # Convert -1 to 1 scale → 1 to 10 scale
    sentiment_score = round((avg_score + 1) * 4.5 + 1)
    sentiment_score = max(1, min(10, sentiment_score))

    # Determine signals
    if avg_score > 0.3:
        smart_money = "accumulating"
        retail = "fomo"
    elif avg_score < -0.3:
        smart_money = "distributing"
        retail = "panic"
    else:
        smart_money = "neutral"
        retail = "neutral"

    # Trap detection
    trap_warning = False
    trap_reason = None

    if direction == "BUY_SIGNAL" and sentiment_score >= 8:
        trap_warning = True
        trap_reason = "Extreme positive sentiment on buy signal — potential bull trap"
    elif direction == "SELL_SIGNAL" and sentiment_score <= 2:
        trap_warning = True
        trap_reason = "Extreme negative sentiment on sell signal — potential bear trap"

    # Extract key narratives from top headlines
    key_narratives = headlines[:3]

    # Confidence based on number of articles
    if len(headlines) >= 10:
        confidence = "high"
    elif len(headlines) >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "sentiment_score": sentiment_score,
        "smart_money_signal": smart_money,
        "retail_signal": retail,
        "key_narratives": key_narratives,
        "trap_warning": trap_warning,
        "trap_reason": trap_reason,
        "confidence": confidence,
        "items_analyzed": len(headlines)
    }


def get_sentiment_grok(ticker: str, direction: str) -> dict | None:
    """
    Queries Grok/X for crowd sentiment. Returns the same per-source shape as
    _get_sentiment_newsapi(), or None if the source is unavailable for any
    reason (missing key, API error, malformed response) — callers must treat
    a missing Grok read as "only one source available", not as neutral data.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return None

    search_term = TICKER_KEYWORDS.get(ticker, ticker)

    try:
        from openai import OpenAI
        xai_client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

        prompt = f"""Analyze current X/Twitter sentiment for {search_term} ({ticker}) among
crypto/stock traders and influencers. Consider recent post volume, tone, and
whether the crowd looks fearful, greedy, or neutral.

Respond with ONLY a JSON object in this exact shape:
{{
  "sentiment_score": <int 1-10, 1=extreme fear, 10=extreme greed>,
  "smart_money_signal": "accumulating"|"distributing"|"neutral",
  "retail_signal": "fomo"|"panic"|"neutral",
  "key_narratives": [<up to 3 short strings summarizing the dominant narratives on X>],
  "trap_warning": <bool, true if sentiment looks manipulated or overheated relative to a {direction} setup>,
  "trap_reason": <string or null>,
  "confidence": "high"|"medium"|"low",
  "items_analyzed": <int, approximate number of posts/signals considered>
}}"""

        response = xai_client.chat.completions.create(
            model="grok-3",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)

        return {
            "sentiment_score": max(1, min(10, int(result.get("sentiment_score", 5)))),
            "smart_money_signal": result.get("smart_money_signal", "neutral"),
            "retail_signal": result.get("retail_signal", "neutral"),
            "key_narratives": list(result.get("key_narratives", []))[:3],
            "trap_warning": bool(result.get("trap_warning", False)),
            "trap_reason": result.get("trap_reason"),
            "confidence": result.get("confidence", "low"),
            "items_analyzed": int(result.get("items_analyzed", 0))
        }
    except Exception as e:
        print(f"[!] Grok API error: {e}")
        return None


def _reconcile(newsapi_result: dict, grok_result: dict | None, direction: str) -> dict:
    """
    Combines the two per-source reads into the public sentiment dict.
    Sources that roughly agree get averaged into one confident score.
    Sources that diverge stay flagged rather than being averaged away:
    a moderate gap forces confidence down to "low"; a fear-vs-greed
    polarity conflict additionally forces trap_warning True.
    """
    if grok_result is None:
        news_score = newsapi_result["sentiment_score"]
        return {
            "sentiment_score": news_score,
            "smart_money_signal": newsapi_result["smart_money_signal"],
            "retail_signal": newsapi_result["retail_signal"],
            "key_narratives": newsapi_result["key_narratives"],
            "trap_warning": newsapi_result["trap_warning"],
            "trap_reason": newsapi_result["trap_reason"],
            "confidence": newsapi_result["confidence"],
            "headlines_analyzed": newsapi_result["items_analyzed"],
            "sources_analyzed": 1,
            "divergence": {
                "detected": False,
                "polarity_conflict": False,
                "score_gap": None,
                "reason": "Only one source available (Grok/X unavailable)",
                "newsapi": {
                    "score": news_score,
                    "regime": _regime(news_score),
                    "confidence": newsapi_result["confidence"]
                },
                "grok": {"score": None, "regime": None, "confidence": None}
            }
        }

    news_score = newsapi_result["sentiment_score"]
    grok_score = grok_result["sentiment_score"]
    score_gap = abs(news_score - grok_score)
    news_regime = _regime(news_score)
    grok_regime = _regime(grok_score)
    polarity_conflict = {news_regime, grok_regime} == {"fear", "greed"}
    detected = score_gap >= DIVERGENCE_THRESHOLD

    sentiment_score = max(1, min(10, round((news_score + grok_score) / 2)))

    if detected:
        confidence = "low"
    else:
        best_rank = max(CONFIDENCE_RANK[newsapi_result["confidence"]], CONFIDENCE_RANK[grok_result["confidence"]])
        confidence = RANK_CONFIDENCE[min(best_rank + 1, 2)]

    if sentiment_score >= 7:
        smart_money, retail = "accumulating", "fomo"
    elif sentiment_score <= 4:
        smart_money, retail = "distributing", "panic"
    else:
        smart_money, retail = "neutral", "neutral"

    key_narratives = (newsapi_result["key_narratives"][:2] + grok_result["key_narratives"][:2])[:3]

    reason = None
    if detected:
        reason = (
            f"NewsAPI reads {news_regime} ({news_score}/10) while Grok/X reads "
            f"{grok_regime} ({grok_score}/10) — sources disagree on crowd psychology"
        )

    trap_warning = False
    trap_reason = None
    if direction == "BUY_SIGNAL" and sentiment_score >= 8:
        trap_warning = True
        trap_reason = "Extreme positive sentiment on buy signal — potential bull trap"
    elif direction == "SELL_SIGNAL" and sentiment_score <= 2:
        trap_warning = True
        trap_reason = "Extreme negative sentiment on sell signal — potential bear trap"

    if polarity_conflict:
        trap_warning = True
        conflict_reason = (
            f"Sentiment sources conflict: NewsAPI shows {news_regime} ({news_score}/10) "
            f"while Grok/X shows {grok_regime} ({grok_score}/10) — treat as trap risk"
        )
        trap_reason = f"{trap_reason}; {conflict_reason}" if trap_reason else conflict_reason

    return {
        "sentiment_score": sentiment_score,
        "smart_money_signal": smart_money,
        "retail_signal": retail,
        "key_narratives": key_narratives,
        "trap_warning": trap_warning,
        "trap_reason": trap_reason,
        "confidence": confidence,
        "headlines_analyzed": newsapi_result["items_analyzed"] + grok_result["items_analyzed"],
        "sources_analyzed": 2,
        "divergence": {
            "detected": detected,
            "polarity_conflict": polarity_conflict,
            "score_gap": score_gap,
            "reason": reason,
            "newsapi": {"score": news_score, "regime": news_regime, "confidence": newsapi_result["confidence"]},
            "grok": {"score": grok_score, "regime": grok_regime, "confidence": grok_result["confidence"]}
        }
    }


def get_sentiment(ticker: str, direction: str) -> dict:
    newsapi_result = _get_sentiment_newsapi(ticker, direction)
    grok_result = get_sentiment_grok(ticker, direction)
    return _reconcile(newsapi_result, grok_result, direction)


if __name__ == "__main__":
    print("[*] Testing sentiment agent...")
    print("[*] Querying sentiment for SOLUSDT BUY_SIGNAL...\n")

    result = get_sentiment("SOLUSDT", "BUY_SIGNAL")
    print(json.dumps(result, indent=2))
