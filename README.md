# DevOps bot

A Slack bot that answers infra questions from `#devops` using RAG over your team's knowledge base.

**Sources:** Confluence · Jira · GitHub · Slack history · Runbooks

---

## Setup

### 1. Create the Slack app

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Under **Socket Mode**, enable it and generate an **App-Level Token** with `connections:write` scope → this is your `SLACK_APP_TOKEN`
3. Under **OAuth & Permissions**, add these **Bot Token Scopes**:
   - `channels:history`, `channels:read`
   - `chat:write`
   - `reactions:read`, `reactions:write`
   - `users:read`
4. Under **Event Subscriptions** → Subscribe to bot events:
   - `message.channels`
   - `app_mention`
5. Under **Slash Commands**, create:
   - `/devops-reindex` — triggers a manual re-index
   - `/devops-search` — debug search without LLM
6. Install the app to your workspace → copy the **Bot User OAuth Token** → `SLACK_BOT_TOKEN`
7. Invite the bot to `#devops`: `/invite @devops-bot`

### 2. Configure environment

```bash
cp .env.example .env
# Fill in all values in .env
```

### 3. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Test the pipeline (no Slack needed)

```bash
# First, ingest just Confluence to get some data
cd src
python -c "
from dotenv import load_dotenv; load_dotenv()
from vector_store import VectorStore
from ingest_confluence import ingest_confluence
ingest_confluence(VectorStore())
"

# Then test retrieval
python ../scripts/test_rag.py "how do I restart the nginx service?"
```

### 5. Run the bot

```bash
cd src
python main.py
```

---

## How it works

```
#devops message
    │
    ▼
bot.py (Slack Events API via Socket Mode)
    │
    ▼
retriever.py
    ├── embed question (OpenAI text-embedding-3-small)
    ├── search vector store (ChromaDB cosine similarity)
    └── build prompt → Claude (claude-sonnet-4-20250514)
    │
    ▼
reply posted in thread with source citations
```

Ingestion runs at startup and every 6 hours (configurable via `INGEST_SCHEDULE_HOURS`).
Trigger a manual re-index anytime with `/devops-reindex` in Slack.

---

## File structure

```
devops-bot/
├── src/
│   ├── main.py              # entrypoint — wires everything
│   ├── bot.py               # Slack skeleton (Step 1 standalone)
│   ├── vector_store.py      # ChromaDB wrapper
│   ├── retriever.py         # RAG + Claude answer generation
│   ├── ingest_confluence.py # Confluence ingestion
│   └── ingest_sources.py    # Jira + GitHub + Slack ingestion
├── scripts/
│   └── test_rag.py          # smoke test without Slack
├── data/
│   └── chroma/              # vector DB persisted here (gitignore this)
├── .env.example
├── requirements.txt
└── README.md
```

---

## Extending

| What | Where |
|---|---|
| Add a new knowledge source | Create `ingest_<source>.py`, call it in `main.py:run_ingestion()` |
| Change the answer style | Edit `SYSTEM_PROMPT` in `retriever.py` |
| Add escalation / on-call routing | Add logic in `main.py:on_message()` after getting the answer |
| Switch from ChromaDB to Pinecone | Replace `VectorStore` internals — interface stays the same |
