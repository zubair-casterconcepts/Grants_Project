"""USASpending FunctionTool for the OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.query_context import apply_tool_defaults
from services.tools._normalize import compact_source_rows

logger = logging.getLogger(__name__)


def build_usaspending_tool(defaults: dict[str, Any] | None = None):
    """Construct the USASpending source tool. Blank args fall back to profile defaults."""
    from agents import function_tool

    from services.usaspending import search_awards

    tool_defaults = dict(defaults or {})

    @function_tool
    async def usaspending(
        keyword: str = "",
        priority_area: str = "",
        location_city: str = "",
        location_state: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search USASpending.gov for grant-like federal awards.

        Omit a parameter (or pass "") to use the saved user-profile default for that
        field. Pass an override when the user specifies a different location/topic.

        Parameters:
            keyword: Search phrase; defaults to profile title/description.
            priority_area: Funding category; defaults to profile priority_area.
            location_city: City context; defaults to profile city.
            location_state: Two-letter US state for place-of-performance.
            limit: Maximum awards to return (default 10).
        """
        params = apply_tool_defaults(
            keyword=keyword,
            priority_area=priority_area,
            location_city=location_city,
            location_state=location_state,
            defaults=tool_defaults,
        )
        try:
            results = await asyncio.to_thread(
                search_awards,
                keyword=params["keyword"],
                priority_area=params["priority_area"],
                location_city=params["location_city"],
                location_state=params["location_state"],
                limit=limit,
            )
        except Exception:
            logger.warning("usaspending tool failed", exc_info=True)
            return []
        return compact_source_rows(results, source="usaspending")

    return usaspending
