"""Anthropic Claude API wrapper for research enrichment.

Two call shapes:

- :func:`generate_text` — narrative summary (free-form prose). Returns a
  string.
- :func:`generate_structured` — bull/bear/watch synthesis (JSON output).
  Returns a dict whose keys match ``schema["properties"]``.

Both calls share the same system+context prefix; the second one hits the
prompt cache for ~90% input cost reduction. Cache breakpoint sits on the last
system block, so tools+system cache together (no tools used here, but the
pattern keeps cache hits stable if we add tools later).

When ``ANTHROPIC_API_KEY`` is unset, the helpers raise :class:`LLMUnavailable`
so the caller can downgrade to PARTIAL enrichment without crashing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic

from config import settings

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised when the Anthropic SDK can't run a request (no key, etc.)."""


def _client() -> anthropic.Anthropic:
    if not settings.ANTHROPIC_API_KEY:
        raise LLMUnavailable("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _system_blocks(persona: str, context: str) -> list[dict[str, Any]]:
    """Build a 2-block system array with a cache breakpoint on the last block.

    The persona is short and stable (the analyst role); the context is the
    structured research dump. Putting cache_control on the last block caches
    persona+context together for the second LLM call in the same enrichment.
    """
    return [
        {"type": "text", "text": persona},
        {
            "type": "text",
            "text": context,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _extract_text(response: anthropic.types.Message) -> str:
    """Concatenate every text block in the response. Returns '' if none."""
    parts = [b.text for b in response.content if b.type == "text"]
    return "\n".join(p.strip() for p in parts if p).strip()


def generate_text(
    *,
    persona: str,
    context: str,
    question: str,
    max_tokens: int = 1024,
) -> tuple[str, dict[str, int]]:
    """Synchronous Claude call. Returns (text, usage_dict).

    usage_dict has the four token counters from response.usage so callers can
    log cache hits.
    """
    client = _client()
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=_system_blocks(persona, context),
        messages=[{"role": "user", "content": question}],
    )
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(
            response.usage, "cache_creation_input_tokens", 0
        ) or 0,
        "cache_read_input_tokens": getattr(
            response.usage, "cache_read_input_tokens", 0
        ) or 0,
    }
    return _extract_text(response), usage


def generate_structured(
    *,
    persona: str,
    context: str,
    question: str,
    schema: dict[str, Any],
    max_tokens: int = 2048,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Synchronous Claude call constrained to a JSON schema.

    Returns (parsed_dict, usage_dict). Raises ValueError if the response
    can't be parsed as JSON matching the schema's top-level shape.
    """
    client = _client()
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=_system_blocks(persona, context),
        messages=[{"role": "user", "content": question}],
        output_config={
            "format": {"type": "json_schema", "schema": schema},
        },
    )
    raw = _extract_text(response)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned non-JSON output: {e}; raw={raw[:200]!r}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM returned non-object JSON: {type(parsed).__name__}")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(
            response.usage, "cache_creation_input_tokens", 0
        ) or 0,
        "cache_read_input_tokens": getattr(
            response.usage, "cache_read_input_tokens", 0
        ) or 0,
    }
    return parsed, usage


async def agenerate_text(**kwargs) -> tuple[str, dict[str, int]]:
    """Async wrapper — runs the blocking call in a default executor."""
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: generate_text(**kwargs)
    )


async def agenerate_structured(**kwargs) -> tuple[dict[str, Any], dict[str, int]]:
    """Async wrapper — runs the blocking call in a default executor."""
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: generate_structured(**kwargs)
    )
