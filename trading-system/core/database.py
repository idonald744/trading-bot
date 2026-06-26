import os
import vecs
from dotenv import load_dotenv

load_dotenv()

def get_vecs_client():
    """Get Supabase vector database connection"""
    return vecs.create_client(os.getenv("SUPABASE_DB_URL"))

def get_collection(client, name: str = "trading_knowledge", dimension: int = 768):
    """Get or create a vector collection"""
    return client.get_or_create_collection(
        name=name,
        dimension=dimension
    )