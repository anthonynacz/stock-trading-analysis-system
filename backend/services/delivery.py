"""Outbound delivery helpers.

Discord webhooks are the first real delivery channel for alerts and the AM
digest. Email remains a structured-log stub until SMTP/Postmark credentials
are wired — see services/alerts.py and services/digest.py for the callers.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Discord hard limit is 2000 chars per message; leave headroom for safety.
_DISCORD_MAX_CHARS = 1900
_TIMEOUT_SECONDS = 10.0


def _chunk_message(text: str, size: int = _DISCORD_MAX_CHARS) -> list[str]:
    """Split on newlines so chunks stay readable; hard-split only when a
    single line exceeds the limit."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > size:
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def send_discord(webhook_url: str, content: str) -> bool:
    """POST `content` to a Discord webhook, chunked to the 2000-char limit.

    Returns True iff every chunk was accepted (2xx). Never raises — delivery
    failure must not break the scan/dispatch loop that called it.
    """
    if not webhook_url or not content:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            for chunk in _chunk_message(content):
                resp = await client.post(webhook_url, json={"content": chunk})
                if resp.status_code >= 300:
                    logger.warning(
                        "Discord webhook delivery failed: HTTP %s %s",
                        resp.status_code, resp.text[:200],
                    )
                    return False
        return True
    except Exception:
        logger.exception("Discord webhook delivery raised")
        return False
