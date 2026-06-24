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

def analyze_sentiment(text: str) -> float:
    """Returns polarity score: -1.0 (negative) to 1.0 (positive)"""
    return TextBlob(text).sentiment.polarity

def get_sentiment(ticker: str, direction: str) -> dict:
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
            "headlines_analyzed": 0
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
        "headlines_analyzed": len(headlines)
    }

# Future upgrade: swap in Grok API here
def get_sentiment_grok(ticker: str, direction: str) -> dict:
    """
    TODO: Uncomment when xAI API is funded
    
    from openai import OpenAI
    xai_client = OpenAI(
        api_key=os.getenv("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    prompt = f"Analyze X/Twitter sentiment for {ticker}..."
    response = xai_client.chat.completions.create(
        model="grok-3",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)
    """
    pass

if __name__ == "__main__":
    print("[*] Testing sentiment agent...")
    print("[*] Querying sentiment for SOLUSDT BUY_SIGNAL...\n")

    result = get_sentiment("SOLUSDT", "BUY_SIGNAL")
    print(json.dumps(result, indent=2))