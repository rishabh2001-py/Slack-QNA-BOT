"""
Ingestor: Confluence pages + runbooks.
Fetches all pages from the configured space, chunks them, upserts into vector store.
"""

import os
import logging
import re
from atlassian import Confluence
from vector_store import Chunk, VectorStore

log = logging.getLogger(__name__)

CHUNK_SIZE    = 500   # ~tokens per chunk
CHUNK_OVERLAP = 80


def _clean_html(html: str) -> str:
    """Strip Confluence HTML macros/tags to plain text."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _chunk_text(text: str, title: str) -> list[str]:
    """Split text into overlapping chunks."""
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


def ingest_confluence(store: VectorStore, full_index: bool = False) -> int:
    cf = Confluence(
        url      = os.environ["CONFLUENCE_URL"],
        username = os.environ["CONFLUENCE_USERNAME"],
        password = os.environ["CONFLUENCE_API_TOKEN"],
        cloud    = True,
    )

    space_key = os.getenv("CONFLUENCE_SPACE_KEY", "DEVOPS")
    hours     = int(os.getenv("INGEST_SCHEDULE_HOURS", "6"))

    is_first_run = store.count_by_source("confluence") == 0
    if full_index or is_first_run:
        log.info(f"Confluence: full index of space {space_key!r}…")
        store.delete_by_source("confluence")
        extra_params = {}
    else:
        log.info(f"Confluence: incremental index — pages updated in last {hours}h…")
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        extra_params = {"lastModified": since}

    chunks_all: list[Chunk] = []
    start       = 0
    limit       = 50
    total_pages = 0

    use_cql = not (full_index or is_first_run)
    if use_cql:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        cql = (
            f'space = "{space_key}" AND lastmodified >= "{since}" '
            f'ORDER BY lastmodified DESC'
        )

    while True:
        if use_cql:
            resp  = cf.cql(cql, start=start, limit=limit, expand="body.storage,version")
            pages = resp.get("results", [])
        else:
            pages = cf.get_all_pages_from_space(
                space_key,
                start  = start,
                limit  = limit,
                expand = "body.storage,version",
            )

        if not pages:
            break

        total_pages += len(pages)

        for page in pages:
            title      = page.get("title", "Untitled")
            page_id    = page["id"]
            html       = page.get("body", {}).get("storage", {}).get("value", "")
            text       = _clean_html(html)
            updated_at = page.get("version", {}).get("when", "")
            url        = f"{os.environ['CONFLUENCE_URL']}/wiki/spaces/{space_key}/pages/{page_id}"

            for part in _chunk_text(text, title):
                chunks_all.append(Chunk(
                    text        = part,
                    source_url  = url,
                    source_type = "confluence",
                    title       = title,
                    updated_at  = updated_at,
                ))

        if len(pages) < limit:
            break
        start += limit

    store.upsert(chunks_all)
    log.info(f"Confluence: ingested {len(chunks_all)} chunks from {total_pages} pages")
    return len(chunks_all)
