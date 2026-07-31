"""GrantedAI FunctionTool for the OpenAI Agents SDK."""

from __future__ import annotations

import logging
from typing import Any

from services.tools._normalize import compact_source_rows

logger = logging.getLogger(__name__)


def build_granted_ai_tool():
    """Construct the GrantedAI source tool registered on the matching agent."""
    from agents import function_tool

    from services.granted_ai import search_grants
    from services.location_utils import normalize_location

    @function_tool
    def granted_ai(
        keyword: str,
        priority_area: str = "",
        location_city: str = "",
        location_state: str = "",
        org_type: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search GrantedAI for grants and funding opportunities.

        Use this tool to discover foundation, state, federal, and other grants from
        Granted's database and AI discovery pipeline. Pass the user's focus keyword,
        priority area, location, and organization type so results follow their filters.

        Parameters:
            keyword: Primary search phrase from the user's focus title or description.
            priority_area: User funding category used to refine topical relevance.
            location_city: User city for context.
            location_state: Two-letter US state code used for state-aware discovery.
            org_type: Organization type from the profile (e.g. 501c3, school, government).
            limit: Maximum number of grants to return (default 10).

        Returns:
            A list of normalized grant records with source set to granted_ai.
            Returns an empty list when the request fails, is rate-limited, or has no matches.
        """
        city, state = normalize_location(location_city, location_state)
        try:
            results = search_grants(
                keyword=keyword,
                priority_area=priority_area,
                location_city=city,
                location_state=state,
                org_type=org_type,
                limit=limit,
                use_discover=True,
            )
        except Exception:
            logger.warning("granted_ai tool failed", exc_info=True)
            return []
        return compact_source_rows(results, source="granted_ai")

    return granted_ai
