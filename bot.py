"""
DevOps bot — Slack listener (Step 1 skeleton).
Listens to #devops, responds in-thread.
Steps 2-5 will plug into handle_devops_message().
"""

import os
import logging
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = App(token=os.environ["SLACK_BOT_TOKEN"])

BOT_USER_ID: str | None = None


def get_bot_user_id() -> str:
    global BOT_USER_ID
    if BOT_USER_ID is None:
        result = app.client.auth_test()
        BOT_USER_ID = result["user_id"]
    return BOT_USER_ID


# ── Core handler ─────────────────────────────────────────────────────────────

def handle_devops_message(text: str, user: str, thread_ts: str, channel: str) -> str:
    """
    Central dispatch — called for every message in #devops.
    Returns the reply string.

    Right now: echoes back.
    Step 4 will replace this with: retrieve_context(text) → ask_claude(context, text)
    """
    log.info(f"Handling message from {user}: {text[:80]!r}")
    return (
        f"👋 Got your message! (Bot skeleton — RAG not wired up yet)\n"
        f"> {text[:200]}"
    )


# ── Slack event listeners ─────────────────────────────────────────────────────

@app.event("message")
def on_message(event: dict, say):
    """Fires on every message in channels the bot has joined."""
    # Ignore bot messages, edits, deletes
    if event.get("bot_id") or event.get("subtype"):
        return
    # Ignore messages from the bot itself
    if event.get("user") == get_bot_user_id():
        return

    channel   = event["channel"]
    text      = event.get("text", "").strip()
    user      = event.get("user", "unknown")
    thread_ts = event.get("thread_ts") or event["ts"]  # stay in existing thread if any

    if not text:
        return

    log.info(f"Message in {channel} from {user}")

    reply = handle_devops_message(text, user, thread_ts, channel)

    say(text=reply, thread_ts=thread_ts)


@app.event("app_mention")
def on_mention(event: dict, say):
    """
    Also fires when the bot is @mentioned anywhere.
    Delegates to the same handler so behaviour is consistent.
    """
    on_message(event, say)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log.info("Starting DevOps bot (Socket Mode)…")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()


if __name__ == "__main__":
    main()
