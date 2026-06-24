import os
import sys
from dotenv import load_dotenv
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.core.postprocessor import MetadataReplacementPostProcessor
import vecs

load_dotenv()

COLLECTION_NAME = "trading_knowledge"

def get_embed_model():
    return OllamaEmbedding(
        model_name="nomic-embed-text",
        base_url="http://127.0.0.1:11434"
    )

def get_vecs_client():
    return vecs.create_client(os.getenv("SUPABASE_DB_URL"))

def query_textbooks(setup: str, ticker: str, rsi: float, direction: str) -> dict:
    """
    Query the trading knowledge base for methodology
    matching the current chart setup
    """
    query = f"""
    {direction} signal on {ticker}.
    RSI is at {rsi:.1f}.
    Setup: {setup}.
    What do professional trading methodologies say about 
    this specific setup? Should we enter this trade?
    What are the key risk management rules?
    """

    embed_model = get_embed_model()
    query_embedding = embed_model.get_text_embedding(query)

    vx = get_vecs_client()
    collection = vx.get_or_create_collection(
        name=COLLECTION_NAME,
        dimension=768
    )

    results = collection.query(
        data=query_embedding,
        limit=3,
        include_metadata=True
    )

    # Build response from top matches
    passages = []
    sources = []

    for result in results:
        record_id, metadata = result
        text = metadata.get('text', '')
        book = metadata.get('book', 'unknown')
        if text and len(text) > 50:
            passages.append(text[:400])
            sources.append(book)

    vx.disconnect()

    if not passages:
        return {
            "validated": False,
            "methodology": "No relevant textbook passages found",
            "sources": [],
            "recommendation": "SKIP - insufficient textbook validation"
        }

    # Determine if setup is validated based on content
    combined = ' '.join(passages).lower()
    
    bullish_keywords = ['buy', 'bullish', 'oversold', 'support', 
                        'reversal', 'accumulate', 'bounce', 'long']
    bearish_keywords = ['sell', 'bearish', 'overbought', 'resistance',
                        'breakdown', 'distribute', 'short']

    bullish_count = sum(1 for k in bullish_keywords if k in combined)
    bearish_count = sum(1 for k in bearish_keywords if k in combined)

    if direction == "BUY_SIGNAL":
        validated = bullish_count >= 2
    else:
        validated = bearish_count >= 2

    recommendation = "PROCEED" if validated else "CAUTION - weak textbook confirmation"

    return {
        "validated": validated,
        "methodology": passages[0] if passages else "",
        "supporting_passages": passages[1:],
        "sources": list(set(sources)),
        "bullish_signals": bullish_count,
        "bearish_signals": bearish_count,
        "recommendation": recommendation
    }

if __name__ == "__main__":
    print("[*] Testing RAG agent...")
    result = query_textbooks(
        setup="RSI + MACD + Bollinger Confluence",
        ticker="SOLUSDT",
        rsi=28.5,
        direction="BUY_SIGNAL"
    )
    import json
    print(json.dumps(result, indent=2))