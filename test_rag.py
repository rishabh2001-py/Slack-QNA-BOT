"""
test_rag.py — smoke test the retrieval pipeline without needing Slack.

Usage:
  python scripts/test_rag.py "how do I restart the nginx service?"
  python scripts/test_rag.py "what's the on-call rotation process?"
"""

import sys
import os
# Files live flat in the project root (no src/ subdirectory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from vector_store import VectorStore
from retriever    import retrieve_and_answer

def main():
    question = " ".join(sys.argv[1:]) or "how do I check disk usage on the prod servers?"
    print(f"\nQuestion: {question}\n{'─'*60}")

    store = VectorStore()
    print(f"Vector store has {store.count()} chunks indexed\n")

    if store.count() == 0:
        print("⚠️  No data indexed yet. Run ingestion first:")
        print("   python -c \"import sys; sys.path.insert(0,'src'); from dotenv import load_dotenv; load_dotenv(); from vector_store import VectorStore; from ingest_confluence import ingest_confluence; ingest_confluence(VectorStore())\"")
        return

    answer = retrieve_and_answer(question, store)
    print(answer)

if __name__ == "__main__":
    main()
