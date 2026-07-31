"""Grants.gov FunctionTool for the OpenAI Agents SDK."""

from __future__ import annotations

import logging
from typing import Any

from services.tools._normalize import compact_source_rows

logger = logging.getLogger(__name__)


def build_grants_gov_tool():
    """Construct the Grants.gov source tool registered on the matching agent."""
    from agents import function_tool

    from services.grants_gov import search_with_filters
    from services.location_utils import normalize_location

    @function_tool
    def grants_gov(
        keyword: str,
        priority_area: str = "",
        location_city: str = "",
        location_state: str = "",
        rows: int = 25,
    ) -> list[dict[str, Any]]:
        """
        Search Grants.gov for open and forecasted federal funding opportunities.

        Use this tool to retrieve current opportunity listings that align with the
        user's funding focus and location. Results include provider/agency details
        (name, code, contact/address when available), award ranges, eligibility,
        ALN/CFDA codes, and opportunity metadata from Grants.gov.

        Parameters:
            keyword: Primary search phrase from the user's focus title or description.
            priority_area: User funding category used to narrow opportunity topics.
            location_city: User city; included in location-aware filtering.
            location_state: Two-letter US state code from the user profile.
            rows: Maximum number of opportunities to return (default 25).

        Returns:
            A list of normalized opportunity records with source set to grants_gov.
            Returns an empty list when the search fails or yields no matches.
        """
        city, state = normalize_location(location_city, location_state)
        try:
            results = search_with_filters(
                keyword=keyword,
                priority_area=priority_area,
                location_city=city,
                location_state=state,
                rows=rows,
            )
        except Exception:
            logger.warning("grants_gov tool failed", exc_info=True)
            return []
        return compact_source_rows(results, source="grants_gov")

    return grants_gov
