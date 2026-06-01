from __future__ import annotations

from typing import Any

import httpx


async def call_daemon(
    *,
    base_url: str,
    path: str,
    payload: dict[str, Any],
    timeout: float = 90.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return dict(response.json())
