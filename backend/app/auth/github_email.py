from typing import Any, Optional


def select_preferred_github_email(emails: Any) -> tuple[str, Optional[bool]]:
    """Select the best email candidate from GitHub /user/emails response."""
    if not isinstance(emails, list):
        return "", None

    def pick_email(predicate):
        """Pick email from entries matching predicate."""
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            email = (entry.get("email") or "").strip().lower()
            if email and predicate(entry):
                return email, bool(entry.get("verified"))
        return None

    selected = pick_email(lambda e: e.get("primary") and e.get("verified"))
    if selected:
        return selected

    selected = pick_email(lambda e: e.get("verified"))
    if selected:
        return selected

    selected = pick_email(lambda e: e.get("primary"))
    if selected:
        return selected

    selected = pick_email(lambda e: True)
    if selected:
        return selected

    return "", None


def resolve_github_email_verification(emails: Any, preferred_email: str | None = None) -> tuple[str, Optional[bool]]:
    """Resolve verification for a known GitHub profile email, or select the best fallback email."""
    normalized_preferred = (preferred_email or "").strip().lower()
    if isinstance(emails, list) and normalized_preferred:
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            email = (entry.get("email") or "").strip().lower()
            if email == normalized_preferred:
                return normalized_preferred, bool(entry.get("verified"))

    if normalized_preferred:
        return normalized_preferred, None

    return select_preferred_github_email(emails)
