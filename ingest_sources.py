"""
Ingestors: Jira tickets, GitHub repos, Slack #devops history.
"""

import os
import logging
from vector_store import Chunk, VectorStore

log = logging.getLogger(__name__)

CHUNK_SIZE    = 500
CHUNK_OVERLAP = 80


def _chunk_text(text: str) -> list[str]:
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end   = start + CHUNK_SIZE
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks or [text[:800]]


# ── Jira ─────────────────────────────────────────────────────────────────────

def _adf_to_text(node) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node.get("text", "")
    parts = [_adf_to_text(child) for child in node.get("content", [])]
    return " ".join(p for p in parts if p)


def ingest_jira(store: VectorStore, full_index: bool = False) -> int:
    import requests

    url      = os.environ["JIRA_URL"]
    username = os.environ["CONFLUENCE_USERNAME"]
    token    = os.environ["CONFLUENCE_API_TOKEN"]
    project  = os.getenv("JIRA_PROJECT_KEY", "INFRA")
    hours    = int(os.getenv("INGEST_SCHEDULE_HOURS", "6"))
    auth     = (username, token)
    headers  = {"Accept": "application/json"}

    # Full index on first run (no existing Jira chunks), incremental otherwise
    is_first_run = store.count_by_source("jira") == 0
    if full_index or is_first_run:
        log.info(f"Jira: full index for project {project!r}…")
        store.delete_by_source("jira")
        jql = f'project = "{project}" ORDER BY updated DESC'
    else:
        log.info(f"Jira: incremental index — issues updated in last {hours}h…")
        jql = f'project = "{project}" AND updated >= -{hours}h ORDER BY updated DESC'
    limit      = 50
    total      = 0
    next_token = None

    while True:
        params = {"jql": jql, "maxResults": limit,
                  "fields": "summary,description,comment,status"}
        if next_token:
            params["nextPageToken"] = next_token

        r = requests.get(
            f"{url}/rest/api/3/search/jql",
            auth=auth,
            headers=headers,
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        data  = r.json()
        batch = data.get("issues", [])
        if not batch:
            break

        page_chunks: list[Chunk] = []
        for issue in batch:
            key    = issue["key"]
            fields = issue["fields"]
            title  = fields.get("summary", "")
            desc   = _adf_to_text(fields.get("description") or {})
            issue_url = f"{url}/browse/{key}"

            comments = []
            for c in (fields.get("comment") or {}).get("comments", []):
                body = _adf_to_text(c.get("body") or {})
                if body:
                    comments.append(body)

            full_text = f"{title}\n\n{desc}\n\n" + "\n\n".join(comments)

            for part in _chunk_text(full_text):
                page_chunks.append(Chunk(
                    text        = part,
                    source_url  = issue_url,
                    source_type = "jira",
                    title       = f"[{key}] {title}",
                    extra       = {"status": fields.get("status", {}).get("name", "")},
                ))

        store.upsert(page_chunks)
        total += len(page_chunks)

        if data.get("isLast", True):
            break
        next_token = data.get("nextPageToken")

    log.info(f"Jira: ingested {total} chunks")
    return total


# ── Bitbucket ─────────────────────────────────────────────────────────────────

_INDEXABLE_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".tf", ".sh"}
_SKIP_DIRS            = {"node_modules", ".git", "vendor", "dist", "build"}

_BB_BASE = "https://api.bitbucket.org/2.0"


def _bb_get(path: str, headers: dict, params: dict = None):
    """Single Bitbucket API GET, raises on non-2xx."""
    import requests
    url = path if path.startswith("http") else f"{_BB_BASE}{path}"
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r


def _bb_walk(workspace: str, repo_slug: str, headers: dict, path: str = "") -> list[dict]:
    """
    Recursively list all files in a repo using the Bitbucket src API.
    Returns a flat list of file entry dicts with keys: path, links.
    """
    import requests
    files = []
    url = f"{_BB_BASE}/repositories/{workspace}/{repo_slug}/src/HEAD/{path}"
    while url:
        r = requests.get(url, headers=headers, params={"pagelen": 100}, timeout=20)
        if not r.ok:
            break
        data = r.json()
        for entry in data.get("values", []):
            if entry["type"] == "commit_directory":
                dirname = entry["path"].split("/")[-1]
                if dirname not in _SKIP_DIRS:
                    files.extend(_bb_walk(workspace, repo_slug, headers, entry["path"]))
            elif entry["type"] == "commit_file":
                fp = entry["path"]
                ext = "." + fp.rsplit(".", 1)[-1] if "." in fp else ""
                if ext in _INDEXABLE_EXTENSIONS:
                    files.append(entry)
        url = data.get("next")
    return files


def ingest_bitbucket(store: VectorStore) -> int:
    workspace   = os.getenv("BITBUCKET_WORKSPACE", "")
    token       = os.getenv("BITBUCKET_ACCESS_TOKEN", "")
    project_key = os.getenv("BITBUCKET_PROJECT", "")
    repo_list   = [r.strip() for r in os.getenv("BITBUCKET_REPOS", "").split(",") if r.strip()]

    if not workspace or not token:
        log.warning("BITBUCKET_WORKSPACE / BITBUCKET_ACCESS_TOKEN not set — skipping")
        return 0

    headers = {"Authorization": f"Bearer {token}"}
    store.delete_by_source("bitbucket")
    chunks_all: list[Chunk] = []

    # Explicit repo list takes priority; otherwise filter by project, else all repos
    if not repo_list:
        params = {"pagelen": 100}
        if project_key:
            params["q"] = f'project.key="{project_key}"'
            log.info(f"Fetching repos in Bitbucket project {project_key!r}…")
        else:
            log.info("Fetching all repos in workspace…")
        url = f"{_BB_BASE}/repositories/{workspace}"
        while url:
            r = _bb_get(url, headers, params=params)
            data = r.json()
            repo_list += [repo["slug"] for repo in data.get("values", [])]
            url = data.get("next")
            params = {}   # next URL already has pagination baked in

    for repo_slug in repo_list:
        log.info(f"Indexing bitbucket:{workspace}/{repo_slug}…")
        try:
            entries = _bb_walk(workspace, repo_slug, headers)
        except Exception as e:
            log.warning(f"Could not walk {repo_slug}: {e}")
            continue

        for entry in entries:
            file_path = entry["path"]
            file_url = f"https://bitbucket.org/{workspace}/{repo_slug}/src/HEAD/{file_path}"
            try:
                content_url = entry["links"]["self"]["href"]
                text = _bb_get(content_url, headers).text
            except Exception:
                continue

            for part in _chunk_text(text):
                chunks_all.append(Chunk(
                    text        = part,
                    source_url  = file_url,
                    source_type = "bitbucket",
                    title       = f"{repo_slug}/{file_path}",
                    extra       = {"repo": repo_slug},
                ))

    store.upsert(chunks_all)
    log.info(f"Bitbucket: ingested {len(chunks_all)} chunks")
    return len(chunks_all)


# ── Slack history ─────────────────────────────────────────────────────────────

def ingest_slack_history(store: VectorStore, client) -> int:
    """
    Index all messages and threads from #devops.
    - Top-level messages with replies → fetch full thread
    - Top-level messages without replies → index the message itself
    - Bot messages and join/leave events are skipped
    """
    # Use a dedicated history channel if set, otherwise fall back to the bot channel
    channel_id = os.getenv("SLACK_HISTORY_CHANNEL_ID") or os.getenv("DEVOPS_CHANNEL_ID", "")
    if not channel_id:
        log.warning("SLACK_HISTORY_CHANNEL_ID not set — skipping Slack history ingest")
        return 0
    log.info(f"Ingesting Slack history from channel {channel_id}")

    log.info("Fetching Slack history from #devops…")
    store.delete_by_source("slack")

    total  = 0
    cursor = None
    pages  = 0

    while True:
        kwargs = {"channel": channel_id, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_history(**kwargs)
        msgs = resp.get("messages", [])
        pages += 1

        page_chunks: list[Chunk] = []

        for msg in msgs:
            # Skip bot messages and channel join/leave events
            if msg.get("bot_id") or msg.get("subtype"):
                continue

            ts  = msg["ts"]
            url = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"

            reply_count = msg.get("reply_count", 0)
            if reply_count > 0:
                # Fetch the full thread (parent + all replies)
                thread_resp = client.conversations_replies(channel=channel_id, ts=ts)
                thread_msgs = thread_resp.get("messages", [])
                texts = [m.get("text", "") for m in thread_msgs
                         if m.get("text") and not m.get("bot_id") and not m.get("subtype")]
            else:
                texts = [msg.get("text", "")] if msg.get("text") else []

            full_text = "\n\n".join(t for t in texts if t.strip())
            if not full_text.strip():
                continue

            # Use first non-empty line as title
            title = full_text.split("\n")[0][:80]

            for part in _chunk_text(full_text):
                page_chunks.append(Chunk(
                    text        = part,
                    source_url  = url,
                    source_type = "slack",
                    title       = title,
                    updated_at  = ts,
                ))

        if page_chunks:
            store.upsert(page_chunks)
            total += len(page_chunks)
            log.info(f"Slack: upserted {len(page_chunks)} chunks (page {pages})")

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor or pages > 20:
            break

    log.info(f"Slack history: ingested {total} chunks total")
    return total
