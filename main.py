
"""
main.py — wires everything together (Step 5).

On startup:
  1. Run full ingestion across all sources
  2. Start the Slack bot
  3. Re-ingest on a schedule (every N hours)

The bot's handle_devops_message() now calls retrieve_and_answer().
"""

import os
import logging
import threading
import schedule
import time

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from vector_store    import VectorStore
from retriever       import retrieve_and_answer
from ingest_confluence import ingest_confluence
from ingest_sources  import ingest_jira, ingest_slack_history

# ── Globals ───────────────────────────────────────────────────────────────────

app   = App(token=os.environ["SLACK_BOT_TOKEN"])
store = VectorStore()

BOT_USER_ID: str | None = None

def get_bot_user_id() -> str:
    global BOT_USER_ID
    if not BOT_USER_ID:
        BOT_USER_ID = app.client.auth_test()["user_id"]
    return BOT_USER_ID


# ── Ingestion ─────────────────────────────────────────────────────────────────

def run_ingestion(full_index: bool = False):
    log.info("=== Starting ingestion run ===")
    total = 0

    try:
        total += ingest_confluence(store, full_index=full_index)
    except Exception as e:
        log.error(f"Confluence ingest failed: {e}")

    try:
        total += ingest_jira(store, full_index=full_index)
    except Exception as e:
        log.error(f"Jira ingest failed: {e}")

    try:
        total += ingest_slack_history(store, app.client)
    except Exception as e:
        log.error(f"Slack history ingest failed: {e}")

    log.info(f"=== Ingestion complete: {total} total chunks, {store.count()} in store ===")


def schedule_ingestion():
    """Run ingestion in a background thread on a schedule."""
    hours = int(os.getenv("INGEST_SCHEDULE_HOURS", "6"))
    schedule.every(hours).hours.do(run_ingestion)
    log.info(f"Ingestion scheduled every {hours}h")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Slack event handlers ──────────────────────────────────────────────────────

THINKING_EMOJI = "hourglass_flowing_sand"

@app.event("message")
def on_message(event: dict, say):
    if event.get("bot_id") or event.get("subtype"):
        return
    if event.get("user") == get_bot_user_id():
        return

    channel   = event["channel"]
    text      = event.get("text", "").strip()
    user      = event.get("user", "unknown")
    thread_ts = event.get("thread_ts") or event["ts"]
    msg_ts    = event["ts"]

    if not text:
        return

    log.info(f"Message in {channel} from {user}: {text[:80]!r}")

    # Add a thinking reaction so the user knows the bot is working
    try:
        app.client.reactions_add(channel=channel, name=THINKING_EMOJI, timestamp=msg_ts)
    except Exception:
        pass

    try:
        reply = retrieve_and_answer(text, store)
    except Exception as e:
        log.error(f"Error generating answer: {e}")
        reply = "Sorry, I ran into an error. Please try again or ask the on-call engineer."

    say(text=reply, thread_ts=thread_ts)

    # Remove the thinking reaction
    try:
        app.client.reactions_remove(channel=channel, name=THINKING_EMOJI, timestamp=msg_ts)
    except Exception:
        pass


@app.event("app_mention")
def on_mention(event: dict, say):
    on_message(event, say)


# ── Slash command: /devops-reindex ────────────────────────────────────────────

@app.command("/devops-reindex")
def handle_reindex(ack, respond, command):
    ack()
    full = (command.get("text", "").strip().lower() == "full")
    msg  = "Full re-index started…" if full else "Incremental re-index started…"
    respond(f"{msg} I'll be smarter in a few minutes.")
    threading.Thread(target=run_ingestion, kwargs={"full_index": full}, daemon=True).start()


# ── Slash command: /devops-search (debug) ─────────────────────────────────────

@app.command("/devops-search")
def handle_search(ack, respond, command):
    ack()
    query   = command.get("text", "").strip()
    if not query:
        respond("Usage: `/devops-search <your query>`")
        return

    results = store.search(query, top_k=3)
    if not results:
        respond("No results found.")
        return

    lines = [f"*Top {len(results)} results for:* `{query}`\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. <{r.source_url}|{r.title}> ({r.source_type}) — score: {r.score:.3f}")

    respond("\n".join(lines))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # 1. Start the Slack bot first so it's responsive immediately
    log.info("Starting Slack bot…")
    threading.Thread(target=lambda: SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start(), daemon=True).start()

    # 2. Run initial ingestion in background (bot answers from existing index while this runs)
    log.info("Running initial ingestion in background…")
    threading.Thread(target=run_ingestion, daemon=True).start()

    # 3. Background scheduled re-ingestion
    schedule_ingestion()   # blocks — keeps main thread alive


if __name__ == "__main__":
    main()
