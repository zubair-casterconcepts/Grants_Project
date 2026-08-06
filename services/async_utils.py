"""Shared async helpers: sync bridges and HTTP client factory for source APIs."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, TypeVar

import httpx

T = TypeVar("T")

DEFAULT_USER_AGENT = "GrantsMatcher/1.0"


def run_sync(coro: Awaitable[T]) -> T:
    """
    Run a coroutine from sync code (Django views, management commands).

    Uses the current thread when no loop is running; otherwise isolates the
    coroutine on a worker thread so nested-loop callers still work.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, T] = {}
    error: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result["value"]


def build_async_client(
    *,
    connect_timeout: float,
    read_timeout: float,
    headers: dict[str, str] | None = None,
    retries: int = 0,
    max_connections: int = 10,
) -> httpx.AsyncClient:
    """AsyncClient with per-source timeouts. `retries` covers connection errors only."""
    base_headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if headers:
        base_headers.update(headers)

    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        headers=base_headers,
        transport=httpx.AsyncHTTPTransport(retries=retries),
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
        ),
        follow_redirects=True,
    )


def json_body(response: httpx.Response) -> Any:
    """Parse JSON without raising on malformed payloads."""
    try:
        return response.json()
    except ValueError:
        return None
