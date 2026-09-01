import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm import router as llm_router
from app.tools import widget_frames


@pytest.fixture(autouse=True)
def reset_widget_frame_storage(monkeypatch):
    """Keep quota tests deterministic and isolated from a developer Redis."""

    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: None)
    with widget_frames._WIDGET_FRAME_CACHE_LOCK:
        widget_frames._WIDGET_FRAME_CACHE.clear()
    yield
    with widget_frames._WIDGET_FRAME_CACHE_LOCK:
        widget_frames._WIDGET_FRAME_CACHE.clear()


def _create_frame(user_id: str, html: str = "<div>Card</div>") -> dict[str, str]:
    """Create one frame through the same helper used by the authenticated route."""

    return widget_frames.create_widget_frame_payload(
        user_id=user_id,
        html=html,
        widget_type="flashcards",
        theme_mode="dark",
    )


def test_widget_frame_payload_serves_isolated_script_frame_headers():
    created = _create_frame(
        "user-1",
        (
            '<meta http-equiv="Content-Security-Policy" content="script-src none">'
            "<div>Card</div><script>window.ready = true;</script>"
        ),
    )

    frame = widget_frames.get_widget_frame_payload(created["frame_id"])

    assert created["frame_url"].endswith(created["frame_id"])
    assert "script-src none" not in frame["html"]
    assert 'data-mode="dark"' in frame["html"]
    assert "omlorix:backend-widget-resize" in frame["html"]
    assert "Content-Security-Policy" in frame["headers"]
    assert "sandbox allow-scripts" in frame["headers"]["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in frame["headers"]["Content-Security-Policy"]
    assert "script-src 'unsafe-inline'" in frame["headers"]["Content-Security-Policy"]
    assert frame["headers"]["X-Frame-Options"] == "SAMEORIGIN"


def test_widget_frame_resize_measurement_can_shrink_with_its_content():
    """The current iframe viewport must not become a floor for later resizes."""

    resize_script = widget_frames._build_resize_script("frame-id")

    # documentElement dimensions are at least the current iframe viewport
    # height. Measuring only the content-sized body lets replayable widgets
    # report a smaller height after replacing their expanded results screen.
    assert "body ? body.scrollHeight : 0" in resize_script
    assert "body ? body.offsetHeight : 0" in resize_script
    assert "body ? body.getBoundingClientRect().height : 0" in resize_script
    assert "root ? root.scrollHeight : 0" not in resize_script
    assert "root ? root.offsetHeight : 0" not in resize_script
    assert "root ? root.getBoundingClientRect().height : 0" not in resize_script


def test_default_owner_frame_limit_supports_transcript_hydration():
    """The default object cap must not evict ordinary multi-widget transcripts."""

    created_frames = [
        _create_frame("user-1", f"<div>Widget {index}</div>")
        for index in range(11)
    ]

    for index, created in enumerate(created_frames):
        frame = widget_frames.get_widget_frame_payload(created["frame_id"])
        assert f"Widget {index}" in frame["html"]


def test_authenticated_route_passes_user_identity_to_quota_boundary(monkeypatch):
    """The HTTP entrypoint cannot create an unowned frame outside user quotas."""

    captured: dict[str, str] = {}

    def fake_create_widget_frame_payload(**kwargs):
        captured.update(kwargs)
        return {"frame_id": "frame-id", "frame_url": "/frame/frame-id"}

    monkeypatch.setattr(
        llm_router,
        "create_widget_frame_payload",
        fake_create_widget_frame_payload,
    )
    monkeypatch.setattr(llm_router, "_audit_llm_event", lambda *_args, **_kwargs: None)

    result = llm_router.create_widget_frame_route(
        payload=llm_router.WidgetFrameCreateRequest(html="<div>Widget</div>"),
        request=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id="user-123"),
    )

    assert result["frame_id"] == "frame-id"
    assert captured["user_id"] == "user-123"
    assert captured["html"] == "<div>Widget</div>"


def test_local_widget_frame_quota_evicts_only_the_owners_oldest_frame(monkeypatch):
    """A user cannot retain more objects than their configured active-frame quota."""

    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 2)

    oldest = _create_frame("user-1", "<div>Oldest</div>")
    retained = _create_frame("user-1", "<div>Retained</div>")
    other_user = _create_frame("user-2", "<div>Other user</div>")
    newest = _create_frame("user-1", "<div>Newest</div>")

    with pytest.raises(HTTPException) as expired:
        widget_frames.get_widget_frame_payload(oldest["frame_id"])

    assert expired.value.status_code == 404
    assert "Retained" in widget_frames.get_widget_frame_payload(retained["frame_id"])["html"]
    assert "Newest" in widget_frames.get_widget_frame_payload(newest["frame_id"])["html"]
    assert "Other user" in widget_frames.get_widget_frame_payload(other_user["frame_id"])["html"]


def test_local_widget_frame_quota_enforces_retained_bytes(monkeypatch):
    """The byte quota closes the many-large-objects path even below the count cap."""

    first = _create_frame("user-1", "<div>" + ("a" * 2_000) + "</div>")
    with widget_frames._WIDGET_FRAME_CACHE_LOCK:
        first_size = widget_frames._WIDGET_FRAME_CACHE[first["frame_id"]]["size_bytes"]

    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 10)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_BYTES_PER_USER", first_size + 100)
    second = _create_frame("user-1", "<div>" + ("b" * 2_000) + "</div>")

    with pytest.raises(HTTPException) as expired:
        widget_frames.get_widget_frame_payload(first["frame_id"])

    assert expired.value.status_code == 404
    assert "bbbb" in widget_frames.get_widget_frame_payload(second["frame_id"])["html"]
    with widget_frames._WIDGET_FRAME_CACHE_LOCK:
        retained_bytes = sum(
            int(frame.get("size_bytes") or 0)
            for frame in widget_frames._WIDGET_FRAME_CACHE.values()
            if frame.get("owner_hash") == widget_frames._widget_frame_owner_hash("user-1")
        )
    assert retained_bytes <= widget_frames._WIDGET_FRAME_MAX_BYTES_PER_USER


def test_local_widget_frame_storage_rejects_when_global_cap_is_full(monkeypatch):
    """Different accounts cannot collectively grow the fallback cache without bound."""

    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 10)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_LOCAL_MAX_TOTAL_FRAMES", 2)

    _create_frame("user-1")
    _create_frame("user-2")

    with pytest.raises(HTTPException) as rejected:
        _create_frame("user-3")

    assert rejected.value.status_code == 429
    assert rejected.value.detail["type"] == "widget_frame_quota_exceeded"
    assert rejected.value.detail["scope"] == "global"
    assert len(widget_frames._WIDGET_FRAME_CACHE) == 2


def test_redis_write_failure_does_not_bypass_quota_through_local_cache(monkeypatch):
    """A partial or failed Redis transaction must fail closed, not double-store."""

    class BrokenRedis:
        def pipeline(self, *_args, **_kwargs):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: BrokenRedis())

    with pytest.raises(HTTPException) as unavailable:
        _create_frame("user-1")

    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["type"] == "widget_frame_storage_unavailable"
    assert widget_frames._WIDGET_FRAME_CACHE == {}


def test_redis_widget_frame_quota_uses_shared_atomic_index(monkeypatch):
    """Redis-backed workers share the same owner quota and evict the oldest frame."""

    fakeredis = pytest.importorskip("fakeredis")
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 2)

    oldest = _create_frame("user-1", "<div>Oldest Redis</div>")
    retained = _create_frame("user-1", "<div>Retained Redis</div>")
    other_user = _create_frame("user-2", "<div>Other Redis user</div>")
    newest = _create_frame("user-1", "<div>Newest Redis</div>")

    with pytest.raises(HTTPException) as expired:
        widget_frames.get_widget_frame_payload(oldest["frame_id"])

    assert expired.value.status_code == 404
    assert "Retained Redis" in widget_frames.get_widget_frame_payload(retained["frame_id"])["html"]
    assert "Newest Redis" in widget_frames.get_widget_frame_payload(newest["frame_id"])["html"]
    assert "Other Redis user" in widget_frames.get_widget_frame_payload(other_user["frame_id"])["html"]
    assert redis_client.zcard(widget_frames._WIDGET_FRAME_REDIS_INDEX_KEY) == 3


def test_redis_widget_frame_storage_rejects_when_global_cap_is_full(monkeypatch):
    """The shared Redis index enforces a system-wide object ceiling across users."""

    fakeredis = pytest.importorskip("fakeredis")
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 10)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_TOTAL_FRAMES", 2)

    _create_frame("user-1")
    _create_frame("user-2")

    with pytest.raises(HTTPException) as rejected:
        _create_frame("user-3")

    assert rejected.value.status_code == 429
    assert rejected.value.detail["scope"] == "global"
    assert redis_client.zcard(widget_frames._WIDGET_FRAME_REDIS_INDEX_KEY) == 2


def test_redis_widget_frame_storage_enforces_global_byte_cap(monkeypatch):
    """Many accounts cannot bypass the system-wide retained-byte ceiling."""

    fakeredis = pytest.importorskip("fakeredis")
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 10)

    _create_frame("user-1", "<div>" + ("a" * 2_000) + "</div>")
    indexed_member = redis_client.zrange(
        widget_frames._WIDGET_FRAME_REDIS_INDEX_KEY,
        0,
        0,
    )[0]
    first_size = widget_frames._parse_widget_frame_index_member(indexed_member)[
        "size_bytes"
    ]
    monkeypatch.setattr(
        widget_frames,
        "_WIDGET_FRAME_MAX_TOTAL_BYTES",
        first_size + 100,
    )

    with pytest.raises(HTTPException) as rejected:
        _create_frame("user-2", "<div>" + ("b" * 2_000) + "</div>")

    assert rejected.value.status_code == 429
    assert rejected.value.detail["scope"] == "global"
    assert redis_client.zcard(widget_frames._WIDGET_FRAME_REDIS_INDEX_KEY) == 1


def test_redis_transaction_prevents_concurrent_global_quota_bypass(monkeypatch):
    """Two workers racing for one slot cannot both commit a retained frame."""

    fakeredis = pytest.importorskip("fakeredis")
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    first_reads_barrier = threading.Barrier(2)
    first_read_lock = threading.Lock()
    first_read_count = 0
    original_pipeline = redis_client.pipeline

    def synchronized_pipeline(*args, **kwargs):
        nonlocal first_read_count
        pipeline = original_pipeline(*args, **kwargs)
        original_zrange = pipeline.zrange

        def synchronized_zrange(*zrange_args, **zrange_kwargs):
            nonlocal first_read_count
            result = original_zrange(*zrange_args, **zrange_kwargs)
            with first_read_lock:
                first_read_count += 1
                should_wait = first_read_count <= 2
            if should_wait:
                first_reads_barrier.wait(timeout=5)
            return result

        pipeline.zrange = synchronized_zrange
        return pipeline

    monkeypatch.setattr(redis_client, "pipeline", synchronized_pipeline)
    monkeypatch.setattr(widget_frames, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_FRAMES_PER_USER", 10)
    monkeypatch.setattr(widget_frames, "_WIDGET_FRAME_MAX_TOTAL_FRAMES", 1)

    outcomes: list[str] = []

    def create_for(user_id: str) -> None:
        try:
            _create_frame(user_id)
            outcomes.append("stored")
        except HTTPException as exc:
            outcomes.append(f"rejected:{exc.status_code}")

    first = threading.Thread(target=create_for, args=("user-1",))
    second = threading.Thread(target=create_for, args=("user-2",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(outcomes) == ["rejected:429", "stored"]
    assert redis_client.zcard(widget_frames._WIDGET_FRAME_REDIS_INDEX_KEY) == 1
