"""Shared normalization helpers for agent source tools."""

from __future__ import annotations

from typing import Any


RICH_FIELDS = (
    "source",
    "title",
    "agency",
    "agency_code",
    "agency_address",
    "agency_contact",
    "agency_email",
    "agency_phone",
    "top_agency",
    "deadline",
    "open_date",
    "url",
    "opp_status",
    "number",
    "id",
    "amount",
    "award_ceiling",
    "award_floor",
    "eligibility",
    "description",
    "doc_type",
    "alns",
    "funding_categories",
    "funding_instruments",
    "cost_sharing",
    "number_of_awards",
    "start_date",
    "pop_city",
    "pop_state",
    "pop_country",
    "recipient",
    "awarding_sub_agency",
    "funding_agency",
    "match_reasons",
    "fit_score",
    "details_url",
    "state",
)


def compact_source_rows(
    items: list[dict[str, Any]],
    *,
    source: str,
    description_limit: int = 600,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items:
        row: dict[str, Any] = {"source": item.get("source", source)}
        for key in RICH_FIELDS:
            if key == "source":
                continue
            value = item.get(key, "")
            if key == "description":
                value = (value or "")[:description_limit]
            elif value is None:
                value = ""
            row[key] = value
        cleaned.append(row)
    return cleaned
