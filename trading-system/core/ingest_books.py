import os
import sys
import re
from dotenv import load_dotenv
from llama_index.embeddings.ollama import OllamaEmbedding
import vecs
import fitz  # pymupdf

load_dotenv()

BOOKS_DIR = "./books"
COLLECTION_NAME = "trading_knowledge"

BOOKS = {
    "murphy": "murphy.pdf",
    "douglas": "douglas.pdf",
    "elder": "elder.pdf",
    "weinstein": "weinstein.pdf",
    "oneil": "oneil.pdf",
    "aziz": "aziz.pdf",
    "minervini": "minervini.pdf",
    "grimes": "grimes.pdf"
}

def get_embed_model():
    return OllamaEmbedding(
        model_name="nomic-embed-text",
        base_url="http://127.0.0.1:11434"
    )

def get_vecs_client():
    return vecs.create_client(os.getenv("SUPABASE_DB_URL"))

def clean_text(text: str) -> str:
    # Remove null bytes and non-printable characters
    text = text.replace('\x00', '')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_chunks_from_pdf(filepath: str, chunk_size: int = 500) -> list:
    doc = fitz.open(filepath)
    chunks = []
    full_text = ""

    print(f"[*] Extracting text from {len(doc)} pages...")

    for page_num, page in enumerate(doc):
        page_text = page.get_text()
        page_text = clean_text(page_text)
        if len(page_text) > 50:  # Skip nearly empty pages
            full_text += " " + page_text

    doc.close()

    # Split into overlapping chunks
    words = full_text.split()
    step = chunk_size // 2  # 50% overlap between chunks

    for i in range(0, len(words), step):
        chunk = ' '.join(words[i:i+chunk_size])
        if len(chunk) > 100:  # Skip tiny chunks
            chunks.append(chunk)

    print(f"[✓] Created {len(chunks)} clean text chunks")
    return chunks

def ingest_single_book(book_key: str):
    if book_key not in BOOKS:
        print(f"[!] Unknown book: {book_key}")
        print(f"[*] Available: {list(BOOKS.keys())}")
        return

    filepath = os.path.join(BOOKS_DIR, BOOKS[book_key])
    if not os.path.exists(filepath):
        print(f"[!] File not found: {filepath}")
        return

    print(f"[*] Loading {BOOKS[book_key]}...")
    chunks = extract_chunks_from_pdf(filepath)

    if not chunks:
        print("[!] No text extracted. PDF may be scanned/image-based.")
        return

    # Sample every 2nd chunk to reduce volume
    chunks = chunks[::2]
    print(f"[*] Using {len(chunks)} chunks after sampling")

    embed_model = get_embed_model()
    vx = get_vecs_client()

    collection = vx.get_or_create_collection(
        name=COLLECTION_NAME,
        dimension=768
    )

    batch_size = 10
    records = []
    total_batches = (len(chunks) - 1) // batch_size + 1

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        print(f"[*] Batch {i//batch_size + 1}/{total_batches}...")

        embeddings = embed_model.get_text_embedding_batch(batch)

        for j, (embedding, text) in enumerate(zip(embeddings, batch)):
            records.append((
                f"{book_key}_{i+j}",
                embedding,
                {
                    "text": text[:2000],
                    "book": book_key
                }
            ))

    print(f"[*] Uploading {len(records)} records to Supabase...")
    collection.upsert(records=records)
    collection.create_index()

    print(f"[✓] {book_key} successfully ingested!")
    vx.disconnect()

def query_knowledge_base(user_query: str):
    print(f"\n[Q]: {user_query}")

    embed_model = get_embed_model()
    query_embedding = embed_model.get_text_embedding(user_query)

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

    print(f"\n[A]: Top 3 matches:\n")
    for i, result in enumerate(results):
        record_id, metadata = result
        print(f"--- Result {i+1} (from {metadata.get('book', 'unknown')}) ---")
        print(metadata.get('text', '')[:500])
        print()
        
    vx.disconnect()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python ingest_books.py ingest <book_key>")
        print("  python ingest_books.py query")
        print(f"  Books: {list(BOOKS.keys())}")
    elif sys.argv[1] == "ingest":
        if len(sys.argv) < 3:
            print(f"[!] Specify book: {list(BOOKS.keys())}")
        else:
            ingest_single_book(sys.argv[2])
    elif sys.argv[1] == "query":
        query_knowledge_base(
            "What does Aziz say about the opening range breakout and VWAP for momentum day trading?"
        )