import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
    for attr_name in (
        "short",
        "ushort",
        "intc",
        "uintc",
        "int_",
        "uint",
        "longlong",
        "ulonglong",
        "half",
        "float16",
        "float32",
        "float64",
        "single",
        "double",
        "longdouble",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "intp",
        "uintp",
        "bool_",
        "integer",
        "floating",
        "generic",
        "number",
        "ndarray",
    ):
        setattr(fake_numpy, attr_name, int if "float" not in attr_name and attr_name != "bool_" else float)
    fake_numpy.bool_ = bool
    fake_numpy.integer = int
    fake_numpy.floating = float
    fake_numpy.generic = object
    fake_numpy.number = (int, float)
    fake_numpy.ndarray = list
    sys.modules["numpy"] = fake_numpy

if "numpy.typing" not in sys.modules:
    sys.modules["numpy.typing"] = ModuleType("numpy.typing")

if "pandas" not in sys.modules:
    fake_pandas = ModuleType("pandas")
    fake_pandas.DataFrame = type("DataFrame", (), {})
    fake_pandas.to_datetime = lambda value, *args, **kwargs: value
    fake_pandas.isna = lambda value: False
    sys.modules["pandas"] = fake_pandas

if "elevenlabs" not in sys.modules:
    fake_elevenlabs = ModuleType("elevenlabs")
    fake_elevenlabs.SpeechToTextConvertRequestModelId = "scribe_v1"
    sys.modules["elevenlabs"] = fake_elevenlabs

if "elevenlabs.client" not in sys.modules:
    fake_elevenlabs_client = ModuleType("elevenlabs.client")
    fake_elevenlabs_client.ElevenLabs = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["elevenlabs.client"] = fake_elevenlabs_client

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")
    fake_markitdown.MarkItDown = type("MarkItDown", (), {"__init__": lambda self, *args, **kwargs: None})
    sys.modules["markitdown"] = fake_markitdown

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

if "app.tools.websearch.domain_filters" not in sys.modules:
    fake_domain_filters = ModuleType("app.tools.websearch.domain_filters")
    fake_domain_filters.normalize_domain_list = lambda value: []
    fake_domain_filters.resolve_websearch_provider_domain_filters = lambda *args, **kwargs: {}
    fake_domain_filters.filter_scraped_webpages_by_domains = lambda pages, *args, **kwargs: pages
    fake_domain_filters.__getattr__ = lambda _name: (lambda *args, **kwargs: [] if args else None)
    sys.modules["app.tools.websearch.domain_filters"] = fake_domain_filters


from app.agents import router as agents_router
from app.agents import utils as agents_utils
from app.agents.schemas import DeleteAgentShareRequest, ShareTypeEnum
from app.groups.defaults import DEFAULT_GROUP_SETTINGS


def _request():
    return SimpleNamespace(client=None, headers={})


def _user():
    return SimpleNamespace(id="viewer-1")


def test_agent_sharing_defaults_to_enabled():
    assert DEFAULT_GROUP_SETTINGS["agents"]["allow_agent_share"] is True


def test_existing_agent_share_state_is_share_type_specific():
    agent_query = MagicMock()
    agent_query.filter.return_value.first.return_value = SimpleNamespace(
        clone_share_id="clone-share",
        live_share_id=None,
        collaborate_share_id=None,
    )
    subscription_query = MagicMock()
    subscription_query.filter.return_value.count.return_value = 0
    db = MagicMock()
    db.query.side_effect = lambda model: (
        agent_query if getattr(model, "__name__", "") == "UserAgent" else subscription_query
    )

    assert agents_router.agent_has_existing_share_state(
        db,
        "agent-1",
        "owner-1",
        agents_router.ShareType.CLONE,
    ) is True
    assert agents_router.agent_has_existing_share_state(
        db,
        "agent-1",
        "owner-1",
        agents_router.ShareType.LIVE,
    ) is False


def test_shared_agent_preview_keeps_working_when_sharing_is_disabled(monkeypatch):
    calls = []

    monkeypatch.setattr(agents_router, "ensure_agents_enabled", lambda user, db: calls.append((user.id, db)))
    monkeypatch.setattr(
        agents_router,
        "ensure_agent_sharing_allowed",
        lambda user, db: (_ for _ in ()).throw(AssertionError("share policy should not gate existing shares")),
    )
    monkeypatch.setattr(
        agents_router,
        "get_shared_agent_preview",
        lambda db, share_id, requesting_user_id: {
            "share_id": share_id,
            "share_type": "clone",
            "name": "Shared agent",
            "icon": "bot",
            "base_model_id": "model-1",
            "owner_name": requesting_user_id,
        },
    )

    response = agents_router.get_shared_agent_route("share-1", db="db", user=_user())

    assert calls == [("viewer-1", "db")]
    assert response["share_id"] == "share-1"


def test_shared_agent_preview_blocks_action_without_base_model_access(monkeypatch):
    agent = SimpleNamespace(
        id="agent-1",
        user_id="owner-1",
        name="Shared agent",
        icon="bot",
        base_model_id="model-1",
        instruction="Use the shared agent.",
        skill_id=None,
        created_at=None,
    )
    monkeypatch.setattr(
        agents_utils,
        "detect_agent_share_type_from_id",
        lambda db, share_id: agents_utils.ShareType.LIVE,
    )
    monkeypatch.setattr(agents_utils, "get_user_agent_by_share_id", lambda *args: agent)
    monkeypatch.setattr(agents_utils, "_owner_display_name", lambda db, user_id: "Owner")

    def deny_base_model_access(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Base model unavailable")

    monkeypatch.setattr(agents_utils, "_get_accessible_base_model", deny_base_model_access)

    preview = agents_utils.get_shared_agent_preview(
        "db",
        share_id="share-1",
        requesting_user_id="viewer-1",
    )

    assert preview["base_model_accessible"] is False
    assert preview["can_complete_share_action"] is False
    assert preview["clone_skill_will_be_omitted"] is False


def test_clone_preview_warns_when_recipient_cannot_access_skill(monkeypatch):
    agent = SimpleNamespace(
        id="agent-1",
        user_id="owner-1",
        name="Shared agent",
        icon="bot",
        base_model_id="model-1",
        instruction="Use the shared agent.",
        skill_id="private-skill-1",
        created_at=None,
    )
    monkeypatch.setattr(
        agents_utils,
        "detect_agent_share_type_from_id",
        lambda db, share_id: agents_utils.ShareType.CLONE,
    )
    monkeypatch.setattr(agents_utils, "get_user_agent_by_share_id", lambda *args: agent)
    monkeypatch.setattr(agents_utils, "_owner_display_name", lambda db, user_id: "Owner")
    monkeypatch.setattr(agents_utils, "_get_accessible_base_model", lambda *args, **kwargs: SimpleNamespace())

    def deny_skill_access(*args, **kwargs):
        raise HTTPException(status_code=404, detail="Skill not found")

    monkeypatch.setattr(agents_utils, "_validate_skill_access", deny_skill_access)

    preview = agents_utils.get_shared_agent_preview(
        "db",
        share_id="share-1",
        requesting_user_id="viewer-1",
    )

    assert preview["base_model_accessible"] is True
    assert preview["can_complete_share_action"] is True
    assert preview["clone_skill_will_be_omitted"] is True


def test_accept_shared_agent_keeps_working_when_sharing_is_disabled(monkeypatch):
    calls = []

    monkeypatch.setattr(agents_router, "ensure_agents_enabled", lambda user, db: calls.append((user.id, db)))
    monkeypatch.setattr(
        agents_router,
        "ensure_agent_sharing_allowed",
        lambda user, db: (_ for _ in ()).throw(AssertionError("share policy should not gate existing shares")),
    )
    monkeypatch.setattr(
        agents_router,
        "accept_shared_agent",
        lambda db, user_id, share_id: {
            "agent_id": "agent-1",
            "name": "Shared agent",
            "share_type": "live",
            "message": f"{user_id}:{share_id}",
        },
    )
    monkeypatch.setattr(agents_router, "_audit_agent_event", lambda *args, **kwargs: None)

    response = agents_router.accept_shared_agent_route(
        "share-1",
        request=_request(),
        db="db",
        db_log="db_log",
        user=_user(),
    )

    assert calls == [("viewer-1", "db")]
    assert response["message"] == "viewer-1:share-1"


def test_clone_shared_agent_keeps_working_when_sharing_is_disabled(monkeypatch):
    calls = []

    monkeypatch.setattr(agents_router, "ensure_agents_enabled", lambda user, db: calls.append((user.id, db)))
    monkeypatch.setattr(
        agents_router,
        "ensure_agent_sharing_allowed",
        lambda user, db: (_ for _ in ()).throw(AssertionError("share policy should not gate existing shares")),
    )
    monkeypatch.setattr(
        agents_router,
        "clone_shared_agent",
        lambda db, user_id, share_id: {
            "agent_id": "agent-1",
            "name": "Cloned agent",
            "message": f"{user_id}:{share_id}",
        },
    )
    monkeypatch.setattr(agents_router, "_audit_agent_event", lambda *args, **kwargs: None)

    response = agents_router.clone_shared_agent_route(
        "share-1",
        request=_request(),
        db="db",
        db_log="db_log",
        user=_user(),
    )

    assert calls == [("viewer-1", "db")]
    assert response["message"] == "viewer-1:share-1"


def test_delete_agent_share_does_not_require_sharing_enabled(monkeypatch):
    agents_enabled_calls = []
    audit_calls = []

    monkeypatch.setattr(agents_router, "ensure_agents_enabled", lambda user, db: agents_enabled_calls.append((user.id, db)))
    monkeypatch.setattr(
        agents_router,
        "ensure_agent_sharing_allowed",
        lambda user, db: (_ for _ in ()).throw(AssertionError("share policy should not gate revocation")),
    )
    monkeypatch.setattr(
        agents_router,
        "delete_agent_share",
        lambda db, user_id, agent_id, share_type=None: {
            "ok": True,
            "user_id": user_id,
            "agent_id": agent_id,
            "share_type": share_type.value if share_type else None,
        },
    )
    monkeypatch.setattr(agents_router, "_audit_agent_event", lambda *args: audit_calls.append(args))

    response = agents_router.delete_agent_share_route(
        DeleteAgentShareRequest(agent_id="agent-1", share_type=ShareTypeEnum.LIVE),
        request=_request(),
        db="db",
        db_log="db_log",
        user=_user(),
    )

    assert agents_enabled_calls == [("viewer-1", "db")]
    assert response == {"ok": True, "user_id": "viewer-1", "agent_id": "agent-1", "share_type": "live"}
    assert len(audit_calls) == 1
