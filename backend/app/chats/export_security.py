import json


CHAT_SHARE_ACCESS_PUBLIC = "public"
CHAT_SHARE_ACCESS_AUTHENTICATED = "authenticated"
CHAT_SHARE_ACCESS_INVITED = "invited"
CHAT_SHARE_ACCESS_MODES = {
    CHAT_SHARE_ACCESS_PUBLIC,
    CHAT_SHARE_ACCESS_AUTHENTICATED,
    CHAT_SHARE_ACCESS_INVITED,
}

CHAT_SHARE_ACCESS_ALIASES = {
    "link": CHAT_SHARE_ACCESS_PUBLIC,
    "public_link": CHAT_SHARE_ACCESS_PUBLIC,
    "anyone": CHAT_SHARE_ACCESS_PUBLIC,
    "authenticated_users": CHAT_SHARE_ACCESS_AUTHENTICATED,
    "signed_in": CHAT_SHARE_ACCESS_AUTHENTICATED,
    "signed-in": CHAT_SHARE_ACCESS_AUTHENTICATED,
    "users": CHAT_SHARE_ACCESS_AUTHENTICATED,
    "invite": CHAT_SHARE_ACCESS_INVITED,
    "invites": CHAT_SHARE_ACCESS_INVITED,
    "invited_users": CHAT_SHARE_ACCESS_INVITED,
    "specific_users": CHAT_SHARE_ACCESS_INVITED,
}


def _normalize_export_share_access_mode(access_mode) -> str:
    raw = str(access_mode or "").strip().lower()
    if not raw:
        return CHAT_SHARE_ACCESS_PUBLIC
    normalized = CHAT_SHARE_ACCESS_ALIASES.get(raw, raw)
    if normalized in CHAT_SHARE_ACCESS_MODES:
        return normalized
    return CHAT_SHARE_ACCESS_AUTHENTICATED


def sanitize_chat_share_for_export(share) -> dict | None:
    """Return portable chat share state without reusable secrets or identifiers."""
    if not isinstance(share, dict):
        return None

    return {
        "has_password": bool(share.get("password")),
        "access_mode": _normalize_export_share_access_mode(share.get("access_mode")),
        "expires_at": share.get("expires_at"),
    }


def _coerce_chat_meta(meta) -> dict:
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str):
        stripped = meta.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def is_chat_excluded_from_default_export(chat) -> bool:
    """Return True for retained chats that must not leave storage in default exports."""
    meta = _coerce_chat_meta(getattr(chat, "meta", None))
    return bool(meta.get("shadow_deleted")) or meta.get("status") == "temp"
