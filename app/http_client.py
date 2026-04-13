"""
Shared httpx AsyncClient and lifecycle helpers for the PathFinder app.

Usage:
- Call `await init_http_client()` during application startup (lifespan).
- Call `await close_http_client()` during application shutdown (lifespan).
- Use the FastAPI dependency `get_http_client()` in route handlers to receive the shared client.

Design notes:
- The module keeps a single AsyncClient instance for reuse across the app to reduce
  connection setup overhead and improve performance.
- The client is configured with sensible defaults (timeout, connection limits, user-agent).
- Callers should NOT close the client — it is closed by `close_http_client()`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

# Default configuration for shared AsyncClient
_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_MAX_CONNECTIONS = 20
_DEFAULT_MAX_KEEPALIVE = 10
_DEFAULT_USER_AGENT = "PathFinder/2.0 (+https://example.org/pathfinder)"

_client: httpx.AsyncClient | None = None


async def init_http_client(
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    max_connections: int = _DEFAULT_MAX_CONNECTIONS,
    max_keepalive: int = _DEFAULT_MAX_KEEPALIVE,
    headers: dict | None = None,
) -> None:
    """
    Initialize the module-level AsyncClient.

    Safe to call multiple times; subsequent calls are no-ops while the client exists.
    Call this from application startup/lifespan.

    Args:
        timeout_seconds: Request timeout (seconds)
        max_connections: Maximum number of simultaneous connections
        max_keepalive: Maximum number of keep-alive connections
        headers: Optional default headers merged with default user-agent header
    """
    global _client
    if _client is not None:
        logger.debug("HTTP client already initialized")
        return

    timeout = httpx.Timeout(timeout_seconds)
    limits = httpx.Limits(
        max_connections=max_connections, max_keepalive_connections=max_keepalive
    )

    default_headers = {"User-Agent": _DEFAULT_USER_AGENT}
    if headers:
        default_headers.update(headers)

    _client = httpx.AsyncClient(timeout=timeout, limits=limits, headers=default_headers)
    logger.info(
        "Initialized shared AsyncClient (timeout=%ss, max_connections=%d, max_keepalive=%d)",
        timeout_seconds,
        max_connections,
        max_keepalive,
    )


async def close_http_client() -> None:
    """
    Close the shared AsyncClient if it exists.

    Call this from application shutdown/lifespan.
    """
    global _client
    if _client is None:
        logger.debug("HTTP client already closed or not initialized")
        return

    try:
        await _client.aclose()
        logger.info("Shared AsyncClient closed")
    except Exception:
        logger.exception("Error while closing shared AsyncClient")
    finally:
        _client = None


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    FastAPI dependency that yields the shared AsyncClient.

    If the client hasn't been initialized yet, this will initialize it lazily.
    It does not close the client when the dependency scope ends.
    """
    global _client
    if _client is None:
        # Lazy init if startup did not initialize; keep defaults
        await init_http_client()

    # mypy/static checkers: at this point _client is guaranteed non-None
    assert _client is not None
    yield _client


def client_instance() -> httpx.AsyncClient | None:
    """
    Return the current AsyncClient instance (or None if not initialized).

    Useful for code that cannot use FastAPI dependency injection.
    """
    return _client


__all__ = [
    "init_http_client",
    "close_http_client",
    "get_http_client",
    "client_instance",
]
