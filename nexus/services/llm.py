"""
Azure OpenAI client for NEXUS intelligence modes.

All LLM calls go through this module so we have a single place to:
- enforce the 8-second contribution SLA
- inject the NEXUS system prompt
- handle retries and logging
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import structlog
from openai import AsyncAzureOpenAI

from nexus.config import settings

log = structlog.get_logger(__name__)

# ── System prompt (loaded once) ───────────────────────────────────────────────

NEXUS_SYSTEM_PROMPT = """You are NEXUS, the intelligence engine of the Solve for X Experience Center.

You are not a chatbot. You are a cognitive engine operating beneath a structured methodology called Solve for X.
You serve the facilitator. You never address participants directly.

Output rules (strictly enforced):
- Maximum 5 bullets or 3 cards. Never output paragraphs.
- Use direct, declarative language. No qualifiers, no hedging.
- Lead with the insight, not the methodology.
- If uncertain: prefix with "Low confidence —"
- If nothing material to add: respond only with "Nothing material to contribute on this."
- For documents: write for executives. Short sentences. Clear structure. No filler.
- Previous session data referenced is always anonymized. Never reveal client names.
"""

# ── Client ────────────────────────────────────────────────────────────────────

_client: AsyncAzureOpenAI | None = None


def get_client() -> AsyncAzureOpenAI:
    global _client
    if _client is None:
        _client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
    return _client


# ── Core call ─────────────────────────────────────────────────────────────────

async def chat(
    user_prompt: str,
    *,
    system_addendum: str | None = None,
    temperature: float = 0.4,
    timeout: float | None = None,
) -> str:
    """
    Single-turn chat with NEXUS system prompt.
    timeout defaults to the contribution SLA from settings.
    """
    effective_timeout = timeout or settings.nexus_contribution_timeout_seconds
    system = NEXUS_SYSTEM_PROMPT
    if system_addendum:
        system = f"{system}\n\n{system_addendum}"

    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            get_client().chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=1024,
            ),
            timeout=effective_timeout,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        content = response.choices[0].message.content or ""
        log.info("llm_response", latency_ms=latency_ms, tokens=response.usage.total_tokens if response.usage else None)
        return content
    except asyncio.TimeoutError:
        log.warning("llm_timeout", timeout=effective_timeout)
        return "NEXUS: Response timed out. Retriggering recommended."


async def chat_stream(
    user_prompt: str,
    *,
    system_addendum: str | None = None,
    temperature: float = 0.4,
) -> AsyncIterator[str]:
    """Streaming variant — used for SYNTHESIZE (long documents)."""
    system = NEXUS_SYSTEM_PROMPT
    if system_addendum:
        system = f"{system}\n\n{system_addendum}"

    stream = await get_client().chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=4096,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def embed(text: str) -> list[float]:
    """Generate a 1536-dim embedding for knowledge base storage/search."""
    response = await get_client().embeddings.create(
        model="text-embedding-ada-002",   # or text-embedding-3-small — set as env var if needed
        input=text[:8191],                # ada-002 token limit
    )
    return response.data[0].embedding
