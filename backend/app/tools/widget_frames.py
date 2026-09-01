"""Short-lived frame documents for backend-rendered tool widgets."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from html import escape
import json
import logging
import re
import secrets
import threading
import time
from typing import Any

from fastapi import HTTPException

from app.redis_client import get_redis_client


logger = logging.getLogger(__name__)

# Redis is optional in development and in several test deployments. Keep the
# module importable without redis-py; the fallback is used only for exception
# matching after a Redis client has already been obtained.
try:
    from redis.exceptions import WatchError
except ImportError:  # pragma: no cover - exercised by import-only environments
    WatchError = RuntimeError


# Widget frames are temporary render artifacts, not durable chat data. These
# backend-owned limits preserve the five-million-character input contract while
# bounding the retained objects and bytes attributable to one account and to
# the whole service. The lower local caps protect process memory when Redis is
# unavailable.
_WIDGET_FRAME_TTL_SECONDS = 300
_WIDGET_FRAME_MAX_HTML_CHARACTERS = 5_000_000
_WIDGET_FRAME_MAX_FRAMES_PER_USER = 100
_WIDGET_FRAME_MAX_BYTES_PER_USER = 25 * 1024 * 1024
_WIDGET_FRAME_MAX_TOTAL_FRAMES = 2_000
_WIDGET_FRAME_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_WIDGET_FRAME_LOCAL_MAX_TOTAL_FRAMES = 256
_WIDGET_FRAME_LOCAL_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_WIDGET_FRAME_REDIS_TRANSACTION_RETRIES = 8

# All Redis keys use one hash tag so the optimistic transaction remains valid
# when an operator uses a Redis Cluster rather than the bundled single node.
_WIDGET_FRAME_REDIS_PREFIX = "tool:widget:{widget_frames}:frame:"
_WIDGET_FRAME_REDIS_INDEX_KEY = "tool:widget:{widget_frames}:index"
_WIDGET_FRAME_CACHE: dict[str, dict[str, Any]] = {}
_WIDGET_FRAME_CACHE_LOCK = threading.Lock()
_WIDGET_CSP_META_PATTERN = re.compile(
    r"<meta\b(?=[^>]*\bhttp-equiv\s*=\s*(['\"]?)content-security-policy\1)[^>]*>",
    re.IGNORECASE,
)


def _widget_frame_header_csp() -> str:
    """Return the HTTP CSP for isolated backend widget frame documents."""

    return "; ".join(
        [
            "sandbox allow-scripts",
            "default-src 'none'",
            "base-uri 'none'",
            "object-src 'none'",
            "frame-src 'none'",
            "child-src 'none'",
            "connect-src 'none'",
            "form-action 'none'",
            "img-src data: blob:",
            "media-src data: blob:",
            "font-src data:",
            "style-src 'unsafe-inline'",
            "script-src 'unsafe-inline'",
            "worker-src 'none'",
            "frame-ancestors 'self'",
        ]
    )


def _widget_frame_meta_csp(header_csp: str) -> str:
    """Return the CSP subset that is valid and useful inside an HTML meta tag."""

    directives: list[str] = []
    for directive in str(header_csp or "").split(";"):
        text = directive.strip()
        if not text:
            continue
        name = text.split(None, 1)[0].lower()
        if name in {"frame-ancestors", "sandbox"}:
            continue
        directives.append(text)
    return "; ".join(directives)


def _inject_head_markup(html: str, additions: str) -> str:
    """Insert host-controlled head markup into a widget document."""

    source = str(html or "")
    head_match = re.search(r"<head[^>]*>", source, flags=re.IGNORECASE)
    if head_match:
        index = head_match.end()
        return f"{source[:index]}{additions}{source[index:]}"
    html_match = re.search(r"<html[^>]*>", source, flags=re.IGNORECASE)
    if html_match:
        index = html_match.end()
        return f"{source[:index]}<head>{additions}</head>{source[index:]}"
    return f"<!doctype html><html><head>{additions}</head><body>{source}</body></html>"


def _build_resize_script(frame_id: str) -> str:
    """Build the frame-to-parent resize reporter script."""

    frame_id_json = json.dumps(str(frame_id), ensure_ascii=False)
    return f"""<script>
(() => {{
    'use strict';
    const frameId = {frame_id_json};
    let queued = false;

    function postHeight() {{
        queued = false;
        const body = document.body;
        // The document element is always at least as tall as the iframe's
        // current viewport. Including its scrollHeight or offsetHeight would
        // therefore make an expanded frame unable to shrink when a widget
        // replaces a tall view (such as quiz results) with shorter content.
        // The body remains content-sized and still includes overflowing
        // descendants through scrollHeight, so it is the correct resize
        // boundary in both directions.
        const height = Math.ceil(Math.max(
            body ? body.scrollHeight : 0,
            body ? body.offsetHeight : 0,
            body ? body.getBoundingClientRect().height : 0
        ));
        parent.postMessage({{
            type: 'omlorix:backend-widget-resize',
            frameId,
            height
        }}, '*');
    }}

    function scheduleHeight() {{
        if (queued) return;
        queued = true;
        requestAnimationFrame(postHeight);
    }}

    window.addEventListener('load', scheduleHeight);
    window.addEventListener('resize', scheduleHeight);
    if (window.ResizeObserver) {{
        const observer = new ResizeObserver(scheduleHeight);
        if (document.documentElement) observer.observe(document.documentElement);
        if (document.body) observer.observe(document.body);
    }}
    if (window.MutationObserver && document.body) {{
        const observer = new MutationObserver(scheduleHeight);
        observer.observe(document.body, {{
            attributes: true,
            childList: true,
            characterData: true,
            subtree: true
        }});
    }}
    scheduleHeight();
}})();
</script>"""


def _build_widget_frame_html(
    *,
    html: str,
    frame_id: str,
    theme_mode: str | None = None,
) -> str:
    """Wrap backend widget HTML as a complete iframe document."""

    mode = str(theme_mode or "").strip()
    html_without_csp = _WIDGET_CSP_META_PATTERN.sub("", str(html or ""))
    header_csp = _widget_frame_header_csp()
    root_attrs = f' data-mode="{escape(mode, quote=True)}"' if mode else ""
    additions = (
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(_widget_frame_meta_csp(header_csp), quote=True)}">'
        "<style>"
        "html,body{margin:0;padding:0;background:transparent;color:var(--text-color,#111827);"
        'font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}'
        "*{box-sizing:border-box}"
        "</style>"
    )
    document_html = _inject_head_markup(html_without_csp, additions)
    document_html = re.sub(r"<html\b([^>]*)>", f"<html\\1{root_attrs}>", document_html, count=1, flags=re.IGNORECASE)
    if "</body>" in document_html.lower():
        return re.sub(
            r"</body>",
            _build_resize_script(frame_id) + "</body>",
            document_html,
            count=1,
            flags=re.IGNORECASE,
        )
    return document_html + _build_resize_script(frame_id)


def _prune_local_widget_frames(now: int) -> None:
    """Remove expired local widget frame records."""

    expired_ids = [
        frame_id
        for frame_id, frame in _WIDGET_FRAME_CACHE.items()
        if int(frame.get("expires_at") or 0) <= now
    ]
    for frame_id in expired_ids:
        _WIDGET_FRAME_CACHE.pop(frame_id, None)


def _widget_frame_owner_hash(user_id: str) -> str:
    """Return a non-identifying stable owner key for quota accounting."""

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise HTTPException(
            status_code=400,
            detail={"type": "widget_frame_owner_required"},
        )
    return hashlib.sha256(normalized_user_id.encode("utf-8")).hexdigest()


def _widget_frame_index_member(
    owner_hash: str,
    frame_id: str,
    size_bytes: int,
) -> str:
    """Encode bounded quota metadata into one Redis sorted-set member."""

    return f"{owner_hash}:{frame_id}:{int(size_bytes)}"


def _parse_widget_frame_index_member(member: Any) -> dict[str, Any] | None:
    """Parse one internal Redis quota record without trusting stored metadata."""

    if isinstance(member, bytes):
        member = member.decode("utf-8", errors="replace")
    member_text = str(member or "")
    parts = member_text.split(":", 2)
    if len(parts) != 3:
        return None
    owner_hash, frame_id, raw_size = parts
    if not re.fullmatch(r"[a-f0-9]{64}", owner_hash):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,160}", frame_id):
        return None
    try:
        size_bytes = int(raw_size)
    except (TypeError, ValueError):
        return None
    if size_bytes < 0:
        return None
    return {
        "member": member_text,
        "owner_hash": owner_hash,
        "frame_id": frame_id,
        "size_bytes": size_bytes,
    }


def _raise_widget_frame_quota(scope: str) -> None:
    """Return one stable machine-readable quota response to API callers."""

    raise HTTPException(
        status_code=429,
        detail={
            "type": "widget_frame_quota_exceeded",
            "scope": scope,
        },
    )


def _validate_frame_fits_user_byte_quota(size_bytes: int) -> None:
    """Reject one frame that can never fit inside an account's retained quota."""

    if size_bytes > _WIDGET_FRAME_MAX_BYTES_PER_USER:
        raise HTTPException(
            status_code=413,
            detail={
                "type": "widget_frame_too_large",
                "max_bytes": _WIDGET_FRAME_MAX_BYTES_PER_USER,
            },
        )


def _store_widget_frame_redis(
    client,
    *,
    owner_hash: str,
    frame_id: str,
    serialized_frame: str,
    size_bytes: int,
) -> None:
    """Atomically enforce shared quotas and store one frame in Redis.

    The single sorted-set index is the authority for both per-user and global
    accounting. Watching it prevents concurrent workers from all observing the
    same remaining capacity and overcommitting memory.
    """

    _validate_frame_fits_user_byte_quota(size_bytes)
    now = time.time()
    expires_at = now + _WIDGET_FRAME_TTL_SECONDS
    frame_key = f"{_WIDGET_FRAME_REDIS_PREFIX}{frame_id}"
    new_member = _widget_frame_index_member(owner_hash, frame_id, size_bytes)

    for _attempt in range(_WIDGET_FRAME_REDIS_TRANSACTION_RETRIES):
        with client.pipeline() as pipeline:
            try:
                pipeline.watch(_WIDGET_FRAME_REDIS_INDEX_KEY)
                indexed_rows = pipeline.zrange(
                    _WIDGET_FRAME_REDIS_INDEX_KEY,
                    0,
                    -1,
                    withscores=True,
                )

                # Ignore expired records for quota decisions. The transaction
                # removes them before inserting the new authoritative record.
                active_entries: list[dict[str, Any]] = []
                unknown_active_count = 0
                for raw_member, raw_expiry in indexed_rows:
                    if float(raw_expiry) <= now:
                        continue
                    parsed_member = _parse_widget_frame_index_member(raw_member)
                    if parsed_member is None:
                        # Corrupt metadata is conservatively counted as one
                        # object until its score expires, but never trusted for
                        # owner or byte accounting.
                        unknown_active_count += 1
                        continue
                    parsed_member["expires_at"] = float(raw_expiry)
                    active_entries.append(parsed_member)

                owner_entries = sorted(
                    (
                        entry
                        for entry in active_entries
                        if entry["owner_hash"] == owner_hash
                    ),
                    key=lambda entry: (entry["expires_at"], entry["frame_id"]),
                )
                owner_bytes = sum(entry["size_bytes"] for entry in owner_entries)
                evicted_entries: list[dict[str, Any]] = []

                # Evict only this user's oldest frames. This keeps normal
                # interaction flowing while preventing one tenant from
                # displacing another tenant's still-active render artifacts.
                while (
                    len(owner_entries) + 1 > _WIDGET_FRAME_MAX_FRAMES_PER_USER
                    or owner_bytes + size_bytes > _WIDGET_FRAME_MAX_BYTES_PER_USER
                ):
                    if not owner_entries:
                        pipeline.unwatch()
                        _raise_widget_frame_quota("user")
                    evicted_entry = owner_entries.pop(0)
                    owner_bytes -= evicted_entry["size_bytes"]
                    evicted_entries.append(evicted_entry)

                evicted_bytes = sum(
                    entry["size_bytes"] for entry in evicted_entries
                )
                active_total_count = len(active_entries) + unknown_active_count
                active_total_bytes = sum(
                    entry["size_bytes"] for entry in active_entries
                )
                projected_total_count = (
                    active_total_count - len(evicted_entries) + 1
                )
                projected_total_bytes = (
                    active_total_bytes - evicted_bytes + size_bytes
                )
                if (
                    projected_total_count > _WIDGET_FRAME_MAX_TOTAL_FRAMES
                    or projected_total_bytes > _WIDGET_FRAME_MAX_TOTAL_BYTES
                ):
                    pipeline.unwatch()
                    _raise_widget_frame_quota("global")

                pipeline.multi()
                pipeline.zremrangebyscore(
                    _WIDGET_FRAME_REDIS_INDEX_KEY,
                    "-inf",
                    now,
                )
                for evicted_entry in evicted_entries:
                    pipeline.zrem(
                        _WIDGET_FRAME_REDIS_INDEX_KEY,
                        evicted_entry["member"],
                    )
                    pipeline.delete(
                        f"{_WIDGET_FRAME_REDIS_PREFIX}{evicted_entry['frame_id']}"
                    )
                pipeline.set(
                    frame_key,
                    serialized_frame,
                    ex=_WIDGET_FRAME_TTL_SECONDS,
                )
                pipeline.zadd(
                    _WIDGET_FRAME_REDIS_INDEX_KEY,
                    {new_member: expires_at},
                )
                # The index outlives the newest frame briefly, but disappears
                # automatically when widget traffic stops entirely.
                pipeline.expire(
                    _WIDGET_FRAME_REDIS_INDEX_KEY,
                    _WIDGET_FRAME_TTL_SECONDS + 60,
                )
                pipeline.execute()
                return
            except WatchError:
                # Another worker changed quota state after our read. Retry from
                # the new authoritative index rather than accepting a race.
                continue

    raise HTTPException(
        status_code=503,
        detail={"type": "widget_frame_storage_busy"},
    )


def _store_widget_frame_local(
    *,
    owner_hash: str,
    frame_id: str,
    frame: dict[str, Any],
    size_bytes: int,
) -> None:
    """Enforce hard process-memory limits for the Redis fallback."""

    _validate_frame_fits_user_byte_quota(size_bytes)
    now = int(time.time())
    created_at_ns = time.time_ns()
    with _WIDGET_FRAME_CACHE_LOCK:
        _prune_local_widget_frames(now)

        owner_entries = sorted(
            (
                (stored_frame_id, stored_frame)
                for stored_frame_id, stored_frame in _WIDGET_FRAME_CACHE.items()
                if stored_frame.get("owner_hash") == owner_hash
            ),
            key=lambda item: (
                int(item[1].get("created_at") or 0),
                item[0],
            ),
        )
        owner_bytes = sum(
            int(stored_frame.get("size_bytes") or 0)
            for _, stored_frame in owner_entries
        )
        evicted_ids: list[str] = []
        evicted_bytes = 0
        while (
            len(owner_entries) + 1 > _WIDGET_FRAME_MAX_FRAMES_PER_USER
            or owner_bytes + size_bytes > _WIDGET_FRAME_MAX_BYTES_PER_USER
        ):
            if not owner_entries:
                _raise_widget_frame_quota("user")
            evicted_id, evicted_frame = owner_entries.pop(0)
            evicted_size = int(evicted_frame.get("size_bytes") or 0)
            owner_bytes -= evicted_size
            evicted_bytes += evicted_size
            evicted_ids.append(evicted_id)

        local_frame_limit = min(
            _WIDGET_FRAME_MAX_TOTAL_FRAMES,
            _WIDGET_FRAME_LOCAL_MAX_TOTAL_FRAMES,
        )
        local_byte_limit = min(
            _WIDGET_FRAME_MAX_TOTAL_BYTES,
            _WIDGET_FRAME_LOCAL_MAX_TOTAL_BYTES,
        )
        current_total_bytes = sum(
            int(stored_frame.get("size_bytes") or 0)
            for stored_frame in _WIDGET_FRAME_CACHE.values()
        )
        projected_total_count = (
            len(_WIDGET_FRAME_CACHE) - len(evicted_ids) + 1
        )
        projected_total_bytes = (
            current_total_bytes - evicted_bytes + size_bytes
        )
        if (
            projected_total_count > local_frame_limit
            or projected_total_bytes > local_byte_limit
        ):
            _raise_widget_frame_quota("global")

        for evicted_id in evicted_ids:
            _WIDGET_FRAME_CACHE.pop(evicted_id, None)
        _WIDGET_FRAME_CACHE[frame_id] = {
            **frame,
            "owner_hash": owner_hash,
            "size_bytes": size_bytes,
            "created_at": created_at_ns,
            "expires_at": now + _WIDGET_FRAME_TTL_SECONDS,
        }


def _store_widget_frame(
    owner_hash: str,
    frame_id: str,
    frame: dict[str, Any],
) -> None:
    """Store a quota-accounted short-lived frame in Redis or bounded memory."""

    serialized_frame = json.dumps(
        frame,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    size_bytes = len(serialized_frame.encode("utf-8"))

    client = get_redis_client()
    if client is not None:
        try:
            _store_widget_frame_redis(
                client,
                owner_hash=owner_hash,
                frame_id=frame_id,
                serialized_frame=serialized_frame,
                size_bytes=size_bytes,
            )
            return
        except HTTPException:
            # Quota and contention responses are security decisions, not Redis
            # outages. Falling back here would bypass shared accounting.
            raise
        except Exception:
            logger.warning("Failed to store widget frame in Redis", exc_info=True)
            # Redis transactions do not roll back commands that fail at
            # execution time. Remove any partially written record before
            # failing closed; otherwise a damaged index could produce
            # unaccounted frame keys on every retry.
            try:
                with client.pipeline(transaction=False) as cleanup:
                    cleanup.delete(f"{_WIDGET_FRAME_REDIS_PREFIX}{frame_id}")
                    cleanup.zrem(
                        _WIDGET_FRAME_REDIS_INDEX_KEY,
                        _widget_frame_index_member(
                            owner_hash,
                            frame_id,
                            size_bytes,
                        ),
                    )
                    cleanup.execute()
            except Exception:
                logger.warning(
                    "Failed to clean up a partial widget frame Redis write",
                    exc_info=True,
                )
            raise HTTPException(
                status_code=503,
                detail={"type": "widget_frame_storage_unavailable"},
            )

    # When no Redis connection can be established at all, retain development
    # and single-process resilience through the separately bounded cache.
    _store_widget_frame_local(
        owner_hash=owner_hash,
        frame_id=frame_id,
        frame=frame,
        size_bytes=size_bytes,
    )


def _load_widget_frame(frame_id: str) -> dict[str, Any] | None:
    """Load a stored widget frame by opaque frame identifier."""

    normalized_id = str(frame_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,160}", normalized_id):
        return None

    client = get_redis_client()
    if client is not None:
        try:
            raw = client.get(f"{_WIDGET_FRAME_REDIS_PREFIX}{normalized_id}")
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else None
        except Exception:
            logger.warning("Failed to read widget frame from Redis", exc_info=True)

    now = int(time.time())
    with _WIDGET_FRAME_CACHE_LOCK:
        _prune_local_widget_frames(now)
        frame = _WIDGET_FRAME_CACHE.get(normalized_id)
        return deepcopy(frame) if isinstance(frame, dict) else None


def create_widget_frame_payload(
    *,
    user_id: str,
    html: str,
    widget_type: str | None = None,
    theme_mode: str | None = None,
) -> dict[str, str]:
    """Create a short-lived iframe document for backend-rendered widget HTML."""

    html_text = str(html or "")
    if not html_text.strip():
        raise HTTPException(status_code=400, detail="Widget HTML is required.")
    if len(html_text) > _WIDGET_FRAME_MAX_HTML_CHARACTERS:
        raise HTTPException(status_code=413, detail="Widget HTML is too large.")

    owner_hash = _widget_frame_owner_hash(user_id)
    frame_id = secrets.token_urlsafe(32)
    frame_html = _build_widget_frame_html(
        html=html_text,
        frame_id=frame_id,
        theme_mode=theme_mode,
    )
    _store_widget_frame(
        owner_hash,
        frame_id,
        {
            "html": frame_html,
            "widget_type": str(widget_type or "unknown").strip()[:120],
            "csp": _widget_frame_header_csp(),
        },
    )
    return {
        "frame_id": frame_id,
        "frame_url": f"/api/v1/llm/widgets/frame/{frame_id}",
    }


def get_widget_frame_payload(frame_id: str) -> dict[str, Any]:
    """Return a stored widget frame document and its security headers."""

    frame = _load_widget_frame(frame_id)
    if not isinstance(frame, dict):
        raise HTTPException(status_code=404, detail="Widget frame expired.")
    html = str(frame.get("html") or "")
    csp = str(frame.get("csp") or "").strip()
    if not html or not csp:
        raise HTTPException(status_code=404, detail="Widget frame expired.")
    return {
        "html": html,
        "headers": {
            "Content-Security-Policy": csp,
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "Expires": "0",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    }
