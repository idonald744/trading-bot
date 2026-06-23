import ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import pandas_ta as ta
import anthropic
import openai
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client
from llama_index.core import VectorStoreIndex
from langgraph.graph import StateGraph

print("✅ ccxt version:", ccxt.__version__)
print("✅ pandas version:", pd.__version__)
print("✅ All imports successful — Phase 0 complete!")