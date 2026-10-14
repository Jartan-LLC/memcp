"""HTTP transport to a self-hosted mem0 REST API.

Sends requests and maps transport failures and error responses to
`MemoryAPIError`; what each request means is the adapter's concern.
"""

from __future__ import annotations

from typing import Any

import httpx

from memcp.types import MemoryAPIError


class Mem0Client:
    """An authenticated HTTP client for one mem0 server."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=httpx.Timeout(timeout),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request; return its JSON body, or None for an empty one."""
        try:
            resp = await self._http.request(method, path, params=params, json=json)
        except httpx.TimeoutException as e:
            raise MemoryAPIError(408, f"Timeout: {e}") from e
        except httpx.RequestError as e:
            raise MemoryAPIError(503, f"Network error: {e}") from e
        if resp.status_code >= 400:
            raise MemoryAPIError(resp.status_code, resp.text)
        return resp.json() if resp.content else None

    async def close(self) -> None:
        await self._http.aclose()
