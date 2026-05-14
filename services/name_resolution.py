"""3-tier name resolution.

Tier 1: explicit display_name (highest confidence).
Tier 2: email-heuristic split on '.', '-', '_' (medium confidence; rejects
         single-character first names like 'j.smith').
Tier 3: Tavily public lookup (lowest confidence; optional; budget-gated).

Returned by `resolve_name()` as a NameResolution or None when unresolvable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol


# Tier 1 / Tier 2 result
@dataclass
class NameResolution:
    first_name: str
    last_name: Optional[str]
    source: str  # "display_name" | "email_heuristic" | "tavily"


def _split_display_name(display_name: str) -> tuple[str, Optional[str]]:
    parts = display_name.strip().split()
    if not parts:
        return ("", None)
    if len(parts) == 1:
        return (parts[0], None)
    return (parts[0], " ".join(parts[1:]))


def heuristic_name_from_email(email: str) -> Optional[tuple[str, str]]:
    """Extract (first, last) from email local-part. Returns None when
    heuristic is not confident enough (e.g., short initials)."""
    local = email.split("@", 1)[0]
    # Try common separators
    for sep in (".", "-", "_"):
        if sep in local:
            parts = [p for p in local.split(sep) if p]
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                if len(first) >= 2 and len(last) >= 2:
                    return (first.capitalize(), last.capitalize())
                # Reject ambiguous initials like j.smith
                return None
    return None


class TavilyClient(Protocol):
    def lookup(self, query: str) -> Optional[tuple[str, str]]:
        ...


def resolve_name(
    email: str,
    display_name: Optional[str],
    tavily_client: Optional[TavilyClient] = None,
) -> Optional[NameResolution]:
    """Apply tiers in order; return first success or None."""
    if display_name and display_name.strip():
        first, last = _split_display_name(display_name)
        return NameResolution(first_name=first, last_name=last, source="display_name")

    heur = heuristic_name_from_email(email)
    if heur is not None:
        return NameResolution(first_name=heur[0], last_name=heur[1], source="email_heuristic")

    if tavily_client is not None:
        tavily_result = tavily_client.lookup(email)
        if tavily_result is not None:
            return NameResolution(
                first_name=tavily_result[0],
                last_name=tavily_result[1],
                source="tavily",
            )

    return None
