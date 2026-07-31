"""Project helpers. Dashboard currently uses dummy third-party matches."""


def get_dummy_grant_matches():
    """Placeholder matches until live Grants.gov / USASpending / ProPublica wiring."""
    return [
        {
            "id": 1,
            "funder_name": "Community Development Block Grant",
            "source": "grants_gov",
            "source_label": "Grants.gov",
            "score": 0.92,
            "status": "pending",
            "reasoning": (
                "Strong alignment with housing and neighborhood revitalization; "
                "eligible applicant type and budget range fit."
            ),
            "amount": "$250,000",
            "deadline": "Sep 15, 2026",
        },
        {
            "id": 2,
            "funder_name": "Workforce Innovation Opportunity Fund",
            "source": "usaspending",
            "source_label": "USASpending",
            "score": 0.87,
            "status": "pending",
            "reasoning": (
                "Prior awards in your state funded similar job-training programs "
                "with comparable request sizes."
            ),
            "amount": "$180,000",
            "deadline": "Oct 2, 2026",
        },
        {
            "id": 3,
            "funder_name": "Local Arts & Culture Alliance",
            "source": "propublica",
            "source_label": "ProPublica",
            "score": 0.81,
            "status": "confirmed",
            "reasoning": (
                "Foundation history shows consistent support for community arts "
                "and youth creative programming."
            ),
            "amount": "$75,000",
            "deadline": "Aug 28, 2026",
        },
        {
            "id": 4,
            "funder_name": "Public Health Equity Initiative",
            "source": "grants_gov",
            "source_label": "Grants.gov",
            "score": 0.76,
            "status": "pending",
            "reasoning": (
                "Matches health priority area; food access and prevention "
                "language overlap with opportunity abstract."
            ),
            "amount": "$120,000",
            "deadline": "Nov 10, 2026",
        },
        {
            "id": 5,
            "funder_name": "Youth Literacy Catalyst Grant",
            "source": "propublica",
            "source_label": "ProPublica",
            "score": 0.71,
            "status": "rejected",
            "reasoning": (
                "Partial thematic fit, but typical award size is below your "
                "requested budget."
            ),
            "amount": "$40,000",
            "deadline": "Sep 30, 2026",
        },
        {
            "id": 6,
            "funder_name": "Downtown Economic Recovery Program",
            "source": "usaspending",
            "source_label": "USASpending",
            "score": 0.68,
            "status": "pending",
            "reasoning": (
                "Geographic and economic-development overlap; verify small "
                "business eligibility requirements."
            ),
            "amount": "$300,000",
            "deadline": "Dec 1, 2026",
        },
    ]
