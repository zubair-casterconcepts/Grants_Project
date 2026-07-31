"""USASpending FunctionTool for the OpenAI Agents SDK."""

from __future__ import annotations

import logging
from typing import Any

from services.tools._normalize import compact_source_rows

logger = logging.getLogger(__name__)


def build_usaspending_tool():
    """Construct the USASpending source tool registered on the matching agent."""
    from agents import function_tool

    from services.location_utils import normalize_location
    from services.usaspending import search_awards

    @function_tool
    def usaspending(
        keyword: str,
        priority_area: str = "",
        location_city: str = "",
        location_state: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search USASpending.gov for grant-like federal awards.

        Use this tool to retrieve awards that relate to the user's topic and
        place of performance. Results include awarding/funding agency names,
        recipient, award amount/dates, and place-of-performance location details
        provided by USASpending.

        Parameters:
            keyword: Primary search phrase from the user's focus title or description.
            priority_area: User funding category used to refine topical relevance.
            location_city: User city; retained for context and secondary filtering.
            location_state: Two-letter US state code used for place-of-performance.
            limit: Maximum number of awards to return (default 10).

        Returns:
            A list of normalized award records with source set to usaspending.
            Returns an empty list when the request times out, errors, or has no matches.
        """
        city, state = normalize_location(location_city, location_state)
        try:
            results = search_awards(
                keyword=keyword,
                priority_area=priority_area,
                location_city=city,
                location_state=state,
                limit=limit,
            )
        except Exception:
            logger.warning("usaspending tool failed", exc_info=True)
            return []
        return compact_source_rows(results, source="usaspending")

    return usaspending
