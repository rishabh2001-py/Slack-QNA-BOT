"""
Retriever + answer generator.

retrieve_and_answer(question) →
  1. Fetch top-k chunks from vector store
  2. Build prompt with context
  3. Ask Groq (Llama 3.3 70B)
  4. Return formatted Slack reply
"""

import os
import logging
from openai import OpenAI
from vector_store import VectorStore, SearchResult

log    = logging.getLogger(__name__)
client = OpenAI(
    api_key  = os.environ["GROQ_API_KEY"],
    base_url = "https://api.groq.com/openai/v1",
)

GROQ_MODEL  = "llama-3.3-70b-versatile"
TOP_K       = 6
MAX_CONTEXT = 6000   # chars of context to pass to the LLM

SYSTEM_PROMPT = """You are an expert DevOps assistant for the engineering team.
You answer infrastructure and platform questions by reasoning over the provided knowledge base excerpts.

Rules:
- Answer only from the provided context. If the context doesn't contain enough information, say so clearly.
- Be concise and direct. Engineers want answers, not essays.
- If the answer involves a runbook or doc, mention it explicitly.
- If you're unsure, say "I'm not confident about this — please verify or ask <@on-call>."
- Never make up commands, hostnames, or config values.
- Format code blocks with triple backticks.
"""


def _format_context(results: list[SearchResult]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[{i}] Source: {r.title or r.source_url} ({r.source_type})\n"
            f"URL: {r.source_url}\n"
            f"{r.text[:1000]}"
        )
    return "\n\n---\n\n".join(parts)


def _format_slack_reply(answer: str, results: list[SearchResult]) -> str:
    seen  = set()
    links = []
    for r in results:
        if r.source_url and r.source_url not in seen:
            seen.add(r.source_url)
            label = r.title or r.source_url
            links.append(f"• <{r.source_url}|{label}> ({r.source_type})")

    sources_block = "\n".join(links) if links else "_No sources found_"
    return f"{answer}\n\n*Sources used:*\n{sources_block}"


def retrieve_and_answer(
    question: str,
    store:    VectorStore,
    *,
    source_type_filter: str | None = None,
) -> str:
    log.info(f"Retrieving for: {question[:80]!r}")

    results = store.search(question, top_k=TOP_K, source_type=source_type_filter)

    if not results:
        return (
            "I searched the knowledge base but couldn't find anything relevant. "
            "You may want to check the runbooks directly or ask the on-call engineer."
        )

    context = _format_context(results)
    if len(context) > MAX_CONTEXT:
        context = context[:MAX_CONTEXT] + "\n\n[...context truncated]"

    user_message = (
        f"Knowledge base context:\n\n{context}\n\n"
        f"---\n\nQuestion: {question}"
    )

    log.info("Calling Groq (llama-3.3-70b-versatile)…")
    response = client.chat.completions.create(
        model      = GROQ_MODEL,
        max_tokens = 1024,
        messages   = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )

    answer = response.choices[0].message.content
    return _format_slack_reply(answer, results)
