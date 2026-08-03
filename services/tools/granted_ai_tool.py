"""GrantedAI FunctionTool for the OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.query_context import apply_tool_defaults
from services.tools._normalize import compact_source_rows

logger = logging.getLogger(__name__)


def build_granted_ai_tool(defaults: dict[str, Any] | None = None):
    """Construct the GrantedAI source tool. Blank args fall back to profile defaults."""
    from agents import function_tool

    from services.granted_ai import search_grants

    tool_defaults = dict(defaults or {})

    @function_tool
    async def granted_ai(
        keyword: str = "",
        priority_area: str = "",
        location_city: str = "",
        location_state: str = "",
        org_type: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search GrantedAI for grants and funding opportunities.

        Omit a parameter (or pass "") to use the saved user-profile default for that
        field. Pass overrides when the user names a different location/topic/org type.

        Parameters:
            keyword: Search phrase; defaults to profile title/description.
            priority_area: Funding category; defaults to profile priority_area.
            location_city: City; defaults to profile city.
            location_state: Two-letter US state; defaults to profile state.
            org_type: Organization type; defaults to profile org_type.
            limit: Maximum grants to return (default 10).
        """
        params = apply_tool_defaults(
            keyword=keyword,
            priority_area=priority_area,
            location_city=location_city,
            location_state=location_state,
            org_type=org_type,
            defaults=tool_defaults,
        )
        try:
            results = await asyncio.to_thread(
                search_grants,
                keyword=params["keyword"],
                priority_area=params["priority_area"],
                location_city=params["location_city"],
                location_state=params["location_state"],
                org_type=params["org_type"],
                limit=limit,
                use_discover=True,
            )
        except Exception:
            logger.warning("granted_ai tool failed", exc_info=True)
            return []
        return compact_source_rows(results, source="granted_ai")

    return granted_ai
