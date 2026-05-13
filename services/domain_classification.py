"""Domain classification: personal | internal | business.

Used by per-attendee three-state branching to decide whether to
create a contact, queue a signal, or skip entirely.
"""

from enum import Enum

# Curated public personal-email domain list. Kept conservative; expand
# when new personal-provider patterns emerge in production data.
PERSONAL_DOMAINS = frozenset({
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "msn.com",
    "comcast.net",
    "verizon.net",
    "att.net",
    "duck.com",
    "fastmail.com",
    "tutanota.com",
    "zoho.com",
    "mail.com",
    "gmx.com",
})


class DomainClass(Enum):
    PERSONAL = "personal"
    INTERNAL = "internal"
    BUSINESS = "business"


def normalize_domain(domain: str) -> str:
    return domain.strip().lower()


def is_personal_domain(domain: str) -> bool:
    return normalize_domain(domain) in PERSONAL_DOMAINS


def classify_domain(domain: str, internal_domains: set[str]) -> DomainClass:
    d = normalize_domain(domain)
    if d in PERSONAL_DOMAINS:
        return DomainClass.PERSONAL
    if d in {nd.lower() for nd in internal_domains}:
        return DomainClass.INTERNAL
    return DomainClass.BUSINESS


def email_domain(email: str) -> str:
    """Extract domain portion of an email; lower-cased; '' on malformed."""
    parts = email.strip().lower().split("@", 1)
    return parts[1] if len(parts) == 2 else ""
