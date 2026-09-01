_SHARED_ITEM_URL_PREFIXES = {
    "chat": "/chats/shared",
    "artifact": "/canvas/shared",
    "project": "/projects/join",
    "note": {
        "clone": "/notes/clone",
        "live": "/notes/live",
        "collaborate": "/notes/collaborate",
    },
    "todo": {
        "clone": "/todos/clone",
        "live": "/todos/live",
        "collaborate": "/todos/collaborate",
    },
    "skill": {
        "clone": "/skills/clone",
        "live": "/skills/live",
        "collaborate": "/skills/collaborate",
    },
    "prompt": {
        "clone": "/prompts/clone",
        "live": "/prompts/live",
        "collaborate": "/prompts/collaborate",
    },
    "agent": {
        "clone": "/agents/clone",
        "live": "/agents/live",
        "collaborate": "/agents/collaborate",
    },
    "folder": {
        "clone": "/folders/clone",
        "live": "/folders/live",
        "collaborate": "/folders/collaborate",
    },
}

_SHARED_ITEM_CAPABILITIES = {
    "chat": {
        "password": True,
        "expiry": True,
        "share_type": False,
        "rotate_link": False,
    },
    "artifact": {
        "password": True,
        "expiry": True,
        "share_type": False,
        "rotate_link": False,
    },
    "project": {
        "password": True,
        "expiry": True,
        "share_type": False,
        "rotate_link": True,
    },
    "note": {
        "password": False,
        "expiry": False,
        "share_type": True,
        "rotate_link": False,
    },
    "todo": {
        "password": False,
        "expiry": False,
        "share_type": True,
        "rotate_link": False,
    },
    "skill": {
        "password": False,
        "expiry": False,
        "share_type": True,
        "rotate_link": False,
    },
    "prompt": {
        "password": False,
        "expiry": False,
        "share_type": True,
        "rotate_link": False,
    },
    "agent": {
        "password": False,
        "expiry": False,
        "share_type": True,
        "rotate_link": False,
    },
    "folder": {
        "password": False,
        "expiry": False,
        "share_type": True,
        "rotate_link": False,
    },
}


def build_shared_item_url(public_url: str, item_type: str, share_id: str, share_type: str | None = None) -> str | None:
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        return None

    base_url = str(public_url or "").rstrip("/")
    prefix_config = _SHARED_ITEM_URL_PREFIXES.get(str(item_type or "").strip())
    if not prefix_config:
        return None

    if isinstance(prefix_config, dict):
        prefix = prefix_config.get(str(share_type or "").strip())
        if not prefix:
            return None
        return f"{base_url}{prefix}/{cleaned_share_id}"

    return f"{base_url}{prefix_config}/{cleaned_share_id}"


def get_shared_item_capabilities(item_type: str) -> dict:
    capabilities = _SHARED_ITEM_CAPABILITIES.get(str(item_type or "").strip(), {})
    return {
        "password": bool(capabilities.get("password")),
        "expiry": bool(capabilities.get("expiry")),
        "share_type": bool(capabilities.get("share_type")),
        "rotate_link": bool(capabilities.get("rotate_link")),
    }
