"""Grants.gov FunctionTool for the OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.query_context import apply_tool_defaults
from services.tools._normalize import compact_source_rows

logger = logging.getLogger(__name__)


def build_grants_gov_tool(defaults: dict[str, Any] | None = None):
    """Construct the Grants.gov source tool. Blank args fall back to profile defaults."""
    from agents import function_tool

    from services.grants_gov import search_with_filters

    tool_defaults = dict(defaults or {})

    @function_tool
    async def grants_gov(
        keyword: str = "",
        priority_area: str = "",
        location_city: str = "",
        location_state: str = "",
        rows: int = 25,
    ) -> list[dict[str, Any]]:
        """
        Search Grants.gov for open and forecasted federal funding opportunities.

        Omit a parameter (or pass "") to use the saved user-profile default for that
        field. When the user names a location/topic/priority in the message, pass
        that override instead.

        Parameters:
            keyword: Search phrase; defaults to profile title/description.
            priority_area: Funding category; defaults to profile priority_area.
            location_city: City filter; defaults to profile city.
            location_state: Two-letter US state; defaults to profile state.
            rows: Maximum opportunities to return (default 25).
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
                search_with_filters,
                keyword=params["keyword"],
                priority_area=params["priority_area"],
                location_city=params["location_city"],
                location_state=params["location_state"],
                rows=rows,
            )
        except Exception:
            logger.warning("grants_gov tool failed", exc_info=True)
            return []
        return compact_source_rows(results, source="grants_gov")

    return grants_gov
