import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.automations import jobs, models, queue, router, utils
from app.automations.schemas import AutomationCreate, AutomationUpdate


class FakeAutomationDb:
    def __init__(self, automation=None):
        self.automation = automation
        self.added = None
        self.committed = False
        self.closed = False

    def add(self, value):
        self.added = value

    def commit(self):
        self.committed = True

    def refresh(self, value):
        return None

    def query(self, *_args):
        return FakeAutomationQuery(self.automation)

    def close(self):
        self.closed = True


class FakeAutomationQuery:
    def __init__(self, automation):
        self.automation = automation

    def filter(self, *_args):
        return self

    def first(self):
        return self.automation


class FakeCreateDb:
    """Track the transaction boundary used by the combined create endpoint."""

    def __init__(self):
        self.committed = False
        self.refreshed = []

    def commit(self):
        self.committed = True

    def refresh(self, value):
        self.refreshed.append(value)


def test_import_automation_skips_exported_webhook_trigger(monkeypatch):
    """Webhook metadata must not block import or recreate sensitive trigger state."""
    automation = SimpleNamespace(id="automation-1", title="Imported automation")
    ordinary_automation = SimpleNamespace(id="automation-2", title="Ordinary automation")
    create_calls = {}

    def fake_create_automation(**kwargs):
        create_calls[kwargs["title"]] = kwargs
        return automation if kwargs["title"] == automation.title else ordinary_automation

    monkeypatch.setattr(utils, "create_automation", fake_create_automation)

    result = utils.import_user_automations(
        db=SimpleNamespace(rollback=lambda: None),
        user_id="user-1",
        payload={
            "export_type": utils.AUTOMATIONS_EXPORT_TYPE,
            "export_version": utils.AUTOMATIONS_EXPORT_VERSION,
            "data": {
                "automations": [
                    {
                        "title": automation.title,
                        "prompt": "Summarize the incoming payload",
                        "model_id": "model-1",
                        "mcp_server_ids": ["notion-server"],
                        "webhook_trigger": {
                            "name": "Incoming webhook",
                            "is_enabled": False,
                            "payload_mode": "append",
                            "include_headers": False,
                            "allowed_header_names": [],
                            "max_body_bytes": 65536,
                            "rate_limit_per_minute": 30,
                        },
                    },
                    {
                        "title": ordinary_automation.title,
                        "prompt": "Run without a webhook",
                        "model_id": "model-1",
                    },
                ],
            },
        },
    )

    assert result["status"] == "success"
    assert result["errors"] == []
    assert result["skipped_webhook_triggers"] == 1
    assert set(create_calls) == {automation.title, ordinary_automation.title}
    assert create_calls[automation.title]["mcp_server_ids"] == ["notion-server"]
    assert create_calls[automation.title]["ignore_inaccessible_mcp_servers"] is True
    assert result["created"] == [
        {
            "id": automation.id,
            "title": automation.title,
            "webhook_trigger_created": False,
            "webhook_trigger_skipped": True,
        },
        {
            "id": ordinary_automation.id,
            "title": ordinary_automation.title,
            "webhook_trigger_created": False,
            "webhook_trigger_skipped": False,
        },
    ]


@pytest.mark.parametrize("version", [None, True, 0.1, 1.1, 2.0, "1.0"])
def test_import_automation_rejects_every_non_current_export_version(version):
    """Only the numeric version 1.0 automation contract is importable."""
    with pytest.raises(HTTPException) as exc_info:
        utils.import_user_automations(
            db=SimpleNamespace(rollback=lambda: None),
            user_id="user-1",
            payload={
                "export_type": utils.AUTOMATIONS_EXPORT_TYPE,
                "export_version": version,
                "data": {"automations": []},
            },
        )

    assert exc_info.value.status_code == 400
    assert "Expected '1.0'" in exc_info.value.detail


@pytest.mark.parametrize("version", [1, 1.0])
def test_import_automation_accepts_json_equivalent_current_versions(version):
    """A browser export/import roundtrip may normalize 1.0 to integer 1."""

    result = utils.import_user_automations(
        db=SimpleNamespace(rollback=lambda: None),
        user_id="user-1",
        payload={
            "export_type": utils.AUTOMATIONS_EXPORT_TYPE,
            "export_version": version,
            "data": {"automations": []},
        },
    )

    assert result["status"] == "success"
    assert result["errors"] == []


def test_create_form_can_reserve_webhook_credentials_before_saving(monkeypatch):
    """The configured public URL must override an attacker-controlled Host value."""
    expires_at = datetime.now(timezone.utc)
    audit_calls = []
    monkeypatch.setenv("PUBLIC_URL", "https://chat.example")
    monkeypatch.setattr(router, "_ensure_automations_enabled", lambda *_args: None)
    monkeypatch.setattr(router, "generate_webhook_secret", lambda: "cuiwh_reserved-secret")
    monkeypatch.setattr(
        router,
        "_create_webhook_reservation_token",
        lambda *_args, **_kwargs: ("signed-reservation", expires_at),
    )
    monkeypatch.setattr(
        router,
        "_audit_automation_event",
        lambda db_log, request, user_id, action, details: audit_calls.append(
            {
                "user_id": user_id,
                "action": action,
                "details": details,
            }
        ),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/automations/webhook/credentials",
            "headers": [(b"host", b"localhost")],
            "scheme": "https",
            "server": ("localhost", 443),
        }
    )
    db = FakeCreateDb()

    result = router.reserve_automation_webhook_credentials_route(
        request=request,
        db=db,
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert result.url.startswith("https://chat.example/api/v1/automations/webhooks/")
    assert result.url.endswith(result.trigger_id)
    assert result.secret == "cuiwh_reserved-secret"
    assert result.reservation_token == "signed-reservation"
    assert db.committed is False
    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "AUTOMATION_WEBHOOK_CREDENTIALS_RESERVED",
            "details": {
                "trigger_id": result.trigger_id,
                "expires_at": expires_at.isoformat(),
            },
        }
    ]
    assert "secret" not in audit_calls[0]["details"]
    assert "reservation_token" not in audit_calls[0]["details"]


def test_webhook_url_prefers_primary_stored_public_url(monkeypatch):
    """Stored canonical configuration must also override the request Host."""
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        router,
        "get_value_by_page_and_key",
        lambda page, key, db: [
            "https://primary.example",
            "https://secondary.example",
        ],
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"host", b"127.0.0.1")],
            "scheme": "http",
            "server": ("127.0.0.1", 80),
        }
    )

    result = router._public_webhook_url(
        request,
        "trigger-1",
        configured_base_url=router._configured_webhook_base_url(FakeCreateDb()),
    )

    assert result == (
        "https://primary.example/api/v1/automations/webhooks/trigger-1"
    )


def test_list_automations_resolves_webhook_base_url_once(monkeypatch):
    """Share one canonical URL lookup across every item in a response page."""
    db = FakeCreateDb()
    automations = [SimpleNamespace(id="automation-1"), SimpleNamespace(id="automation-2")]
    base_url_calls = []
    response_base_urls = []

    monkeypatch.setattr(router, "_ensure_automations_enabled", lambda *_args: None)
    monkeypatch.setattr(
        router,
        "list_automations",
        lambda *_args, **_kwargs: automations,
    )

    def resolve_base_url(received_db):
        base_url_calls.append(received_db)
        return "https://chat.example"

    def serialize_automation(
        automation,
        request,
        *,
        db,
        webhook_base_url,
    ):
        response_base_urls.append(webhook_base_url)
        return automation.id

    monkeypatch.setattr(router, "_configured_webhook_base_url", resolve_base_url)
    monkeypatch.setattr(router, "_automation_to_response", serialize_automation)
    # Keep this route-focused test independent of Pydantic response validation;
    # the serializer's response model is covered by the neighboring tests.
    monkeypatch.setattr(
        router,
        "AutomationListResponse",
        lambda **values: SimpleNamespace(**values),
    )

    result = router.list_automations_route(
        request=Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/automations/list",
                "headers": [(b"host", b"chat.example")],
                "scheme": "https",
                "server": ("chat.example", 443),
            }
        ),
        limit=50,
        offset=0,
        db=db,
        user=SimpleNamespace(id="user-1"),
    )

    assert base_url_calls == [db]
    assert response_base_urls == ["https://chat.example", "https://chat.example"]
    assert result.automations == ["automation-1", "automation-2"]


def test_create_automation_can_create_webhook_in_same_request(monkeypatch):
    """The create form should receive the new URL and one-time secret directly."""
    now = datetime.now(timezone.utc)
    reserved_secret = "cuiwh_0123456789abcdefghijklmnopqrstuvwxyz"
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        title="Incoming issue",
        icon="folder",
        icon_color="#FF6B6B",
        prompt="Summarize the issue",
        model_id="model-1",
        schedule_rules=[],
        schedule_timezone=None,
        skill_id=None,
        note_ids=[],
        file_ids=[],
        is_active=True,
        last_triggered_at=None,
        created_at=now,
        last_updated_at=now,
    )
    trigger = SimpleNamespace(
        id="trigger-1",
        automation_id=automation.id,
        user_id=automation.user_id,
        name=None,
        is_enabled=True,
        token_prefix="cuiwh_prefix",
        payload_mode="template",
        include_headers=True,
        allowed_header_names=[],
        max_body_bytes=65536,
        rate_limit_per_minute=30,
        last_triggered_at=None,
        created_at=now,
        last_updated_at=now,
    )
    create_calls = {}

    def fake_create_automation(**kwargs):
        create_calls["automation"] = kwargs
        return automation

    def fake_create_webhook(db, user_id, automation_id, **kwargs):
        create_calls["webhook"] = {
            "db": db,
            "user_id": user_id,
            "automation_id": automation_id,
            **kwargs,
        }
        return trigger, reserved_secret

    def fake_verify_reservation(db, **kwargs):
        create_calls["reservation"] = {"db": db, **kwargs}

    monkeypatch.setattr(router, "_ensure_automations_enabled", lambda *_args: None)
    monkeypatch.setattr(router, "create_automation", fake_create_automation)
    monkeypatch.setattr(router, "create_webhook_trigger", fake_create_webhook)
    monkeypatch.setattr(router, "_verify_webhook_reservation", fake_verify_reservation)
    monkeypatch.setattr(router, "_audit_automation_event", lambda *_args, **_kwargs: None)

    db = FakeCreateDb()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "scheme": "https",
            "server": ("chat.example", 443),
        }
    )
    payload = AutomationCreate(
        title=automation.title,
        prompt=automation.prompt,
        model_id=automation.model_id,
        webhook_trigger={
            "payload_mode": "template",
            "include_headers": True,
            "trigger_id": trigger.id,
            "secret": reserved_secret,
            "reservation_token": "signed-reservation",
        },
    )

    result = router.create_automation_route(
        payload=payload,
        request=request,
        db=db,
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id=automation.user_id),
    )

    assert create_calls["automation"]["commit"] is False
    assert create_calls["webhook"]["automation_id"] == automation.id
    assert create_calls["webhook"]["payload_mode"] == "template"
    assert create_calls["webhook"]["include_headers"] is True
    assert create_calls["webhook"]["commit"] is False
    assert create_calls["webhook"]["trigger_id"] == trigger.id
    assert create_calls["webhook"]["secret"] == reserved_secret
    assert create_calls["reservation"]["reservation_token"] == "signed-reservation"
    assert db.committed is True
    assert result.automation.webhook_trigger.url.endswith("/api/v1/automations/webhooks/trigger-1")
    assert result.automation.webhook_trigger.secret == reserved_secret


def test_update_automation_route_clears_explicit_null_skill(monkeypatch):
    captured = {}
    automation = SimpleNamespace(schedule_timezone=None, is_active=True)

    def fake_update_automation(**kwargs):
        captured.update(kwargs)
        return automation

    monkeypatch.setattr(router, "_ensure_automations_enabled", lambda *_args: None)
    monkeypatch.setattr(router, "update_automation", fake_update_automation)
    monkeypatch.setattr(router, "_audit_automation_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router, "_configured_webhook_base_url", lambda *_args: "https://chat.example")
    monkeypatch.setattr(router, "_automation_to_response", lambda *_args, **_kwargs: None)

    payload = AutomationUpdate(automation_id="automation-1", skill_id=None)
    result = router.update_automation_route(
        payload=payload,
        request=Request({"type": "http", "method": "PUT", "path": "/", "headers": []}),
        db=SimpleNamespace(),
        db_log=SimpleNamespace(),
        user=SimpleNamespace(id="user-1"),
    )

    assert "skill_id" in payload.model_fields_set
    assert captured["skill_id"] == ""
    assert result.status == "success"


def test_webhook_secret_hash_verification_is_constant_format():
    secret = models.generate_webhook_secret()
    trigger = SimpleNamespace(token_hash=models.hash_webhook_secret(secret))

    assert secret.startswith("cuiwh_")
    assert models.verify_webhook_secret(trigger, secret) is True
    assert models.verify_webhook_secret(trigger, f"{secret}-wrong") is False
    assert models.verify_webhook_secret(trigger, None) is False


def test_webhook_payload_preview_redacts_sensitive_values():
    preview = router._redact_value(
        {
            "authorization": "Bearer secret",
            "nested": {
                "api_key": "sk-secret",
                "safe": "visible",
            },
        }
    )

    assert preview["authorization"] == "[redacted]"
    assert preview["nested"]["api_key"] == "[redacted]"
    assert preview["nested"]["safe"] == "visible"


def test_webhook_template_renderer_resolves_safe_paths():
    rendered = jobs._render_webhook_template(
        "Issue: {{ webhook.body.issue.title }} / {{webhook.query.source}}",
        {
            "body": {"issue": {"title": "Bug report"}},
            "query": {"source": "github"},
        },
    )

    assert rendered == "Issue: Bug report / github"


def test_webhook_prompt_append_includes_context_json():
    prompt = jobs._build_automation_user_prompt(
        "Summarize this event",
        trigger_context={"payload_mode": "append"},
        webhook_context={"body": {"message": "hello"}},
    )

    assert "Summarize this event" in prompt
    assert "Webhook context:" in prompt
    assert '"message": "hello"' in prompt


def test_queue_passes_trigger_context_when_redis_unavailable(monkeypatch):
    captured = {}
    fake_db = FakeAutomationDb()

    monkeypatch.setattr(queue, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(queue, "get_redis_client", lambda: None)
    monkeypatch.setattr(
        queue,
        "reserve_automation_execution",
        lambda *args, **kwargs: (SimpleNamespace(id="execution-1"), "queued"),
    )

    def fake_execute(automation_id, user_id, scheduled_slot=None, trigger_context=None, execution_id=None):
        captured.update(
            {
                "automation_id": automation_id,
                "user_id": user_id,
                "scheduled_slot": scheduled_slot,
                "trigger_context": trigger_context,
                "execution_id": execution_id,
            }
        )
        return True

    monkeypatch.setattr(queue, "execute_automation_job", fake_execute)

    enqueue_result = queue.enqueue_automation_execution(
        "automation-1",
        "user-1",
        "webhook:trigger-1:delivery-1",
        {"type": "webhook", "delivery_id": "delivery-1"},
    )

    assert enqueue_result.status == "queued"
    assert captured["automation_id"] == "automation-1"
    assert captured["user_id"] == "user-1"
    assert captured["scheduled_slot"] == "webhook:trigger-1:delivery-1"
    assert captured["trigger_context"]["type"] == "webhook"
    assert captured["execution_id"] == "execution-1"
    assert fake_db.closed is True


def test_create_automation_rejects_inaccessible_model(monkeypatch):
    db = FakeAutomationDb()
    calls = []

    def deny_access(user_id, model_id, db_arg):
        calls.append((user_id, model_id, db_arg))
        raise HTTPException(status_code=404, detail="You do not have access to this model")

    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", deny_access)

    with pytest.raises(HTTPException) as exc:
        models.create_automation(
            db=db,
            user_id="user-1",
            title="Daily summary",
            prompt="Summarize new activity",
            model_id="restricted-model",
        )

    assert exc.value.status_code == 404
    assert calls == [("user-1", "restricted-model", db)]
    assert db.added is None
    assert db.committed is False


def test_create_automation_rejects_inaccessible_skill(monkeypatch):
    db = FakeAutomationDb()

    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", lambda *_args: None)
    monkeypatch.setattr(
        "app.llm.models.get_model",
        lambda *_args: SimpleNamespace(id="model-1", settings={}),
    )
    monkeypatch.setattr(
        "app.skills.models._resolve_accessible_skill_for_user",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc:
        models.create_automation(
            db=db,
            user_id="user-1",
            title="Daily summary",
            prompt="Summarize new activity",
            model_id="model-1",
            skill_id="another-users-skill",
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Skill not found or not accessible"
    assert db.added is None
    assert db.committed is False


def test_create_automation_rejects_skill_override_for_fixed_model(monkeypatch):
    db = FakeAutomationDb()

    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", lambda *_args: None)
    monkeypatch.setattr(
        "app.llm.models.get_model",
        lambda *_args: SimpleNamespace(id="model-1", settings={"skill_id": "fixed-skill"}),
    )
    monkeypatch.setattr(
        "app.skills.models._resolve_accessible_skill_for_user",
        lambda *_args, **_kwargs: ("User skill", "user-1"),
    )

    with pytest.raises(HTTPException) as exc:
        models.create_automation(
            db=db,
            user_id="user-1",
            title="Daily summary",
            prompt="Summarize new activity",
            model_id="model-1",
            skill_id="user-skill",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "This model has a fixed skill. Remove selected skills and try again."
    assert db.added is None


def test_update_automation_rejects_inaccessible_model_without_changing_existing(monkeypatch):
    automation = SimpleNamespace(id="automation-1", model_id="old-model")

    monkeypatch.setattr(models, "get_automation", lambda *_args: automation)

    def deny_access(*_args):
        raise HTTPException(status_code=404, detail="You do not have access to this model")

    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", deny_access)

    with pytest.raises(HTTPException) as exc:
        models.update_automation(
            db=FakeAutomationDb(),
            user_id="user-1",
            automation_id="automation-1",
            model_id="restricted-model",
        )

    assert exc.value.status_code == 404
    assert automation.model_id == "old-model"


def test_execute_automation_job_rechecks_model_access(monkeypatch):
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        model_id="restricted-model",
        is_active=True,
        title="Daily summary",
    )
    db = FakeAutomationDb(automation=automation)
    notifications = []

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(jobs, "get_user", lambda db_arg, user_id: SimpleNamespace(id=user_id))
    monkeypatch.setattr(jobs, "get_user_group_setting_value", lambda *_args: True)
    monkeypatch.setattr(jobs, "ensure_user_runtime_auth_allowed", lambda *_args, **_kwargs: None)

    def deny_access(*_args):
        raise HTTPException(status_code=404, detail="You do not have access to this model")

    def fail_get_model(*_args):
        pytest.fail("execution should stop before resolving the inaccessible model")

    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", deny_access)
    monkeypatch.setattr(jobs, "get_model", fail_get_model)
    monkeypatch.setattr(
        jobs,
        "_create_automation_failure_notification",
        lambda db_arg, user_id, title, error: notifications.append(
            {"user_id": user_id, "title": title, "error": error}
        ),
    )

    assert jobs.execute_automation_job("automation-1", "user-1") is False
    assert notifications == [
        {
            "user_id": "user-1",
            "title": "Daily summary",
            "error": "You do not have access to this model",
        }
    ]
    assert db.closed is True


def test_execute_automation_job_fails_before_chat_when_saved_context_is_revoked(monkeypatch):
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        model_id="model-1",
        is_active=True,
        title="Daily summary",
        prompt="Summarize activity",
    )
    db = FakeAutomationDb(automation=automation)
    notifications = []

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        jobs,
        "get_user",
        lambda _db, user_id: SimpleNamespace(id=user_id, role="user", group_id="group-1"),
    )
    monkeypatch.setattr(jobs, "get_user_group_setting_value", lambda *_args: True)
    monkeypatch.setattr(jobs, "ensure_user_runtime_auth_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", lambda *_args: None)
    monkeypatch.setattr(jobs, "get_model", lambda *_args: SimpleNamespace(id="model-1", settings={}))
    monkeypatch.setattr(
        jobs,
        "_resolve_automation_runtime_context",
        lambda *_args: (_ for _ in ()).throw(
            jobs.AutomationExecutionRejected(
                "A configured note is no longer accessible",
                status_code=404,
                notify_user=True,
            )
        ),
    )
    monkeypatch.setattr(
        jobs,
        "_create_automation_failure_notification",
        lambda _db, user_id, title, error: notifications.append((user_id, title, error)),
    )
    monkeypatch.setattr(
        jobs,
        "create_chat",
        lambda *_args, **_kwargs: pytest.fail("revoked context must be rejected before chat creation"),
    )

    assert jobs.execute_automation_job("automation-1", "user-1") is False
    assert notifications == [
        ("user-1", "Daily summary", "A configured note is no longer accessible"),
    ]
    assert db.closed is True


def test_execute_automation_job_skips_inactive_automation(monkeypatch):
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        model_id="model-1",
        is_active=False,
        title="Daily summary",
    )
    db = FakeAutomationDb(automation=automation)

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(jobs, "create_chat", lambda *_args, **_kwargs: pytest.fail("inactive automation should not create a chat"))

    assert jobs.execute_automation_job("automation-1", "user-1") is False
    assert db.closed is True


def test_execute_automation_job_skips_inactive_user(monkeypatch):
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        model_id="model-1",
        is_active=True,
        title="Daily summary",
    )
    db = FakeAutomationDb(automation=automation)

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        jobs,
        "get_user",
        lambda db_arg, user_id: SimpleNamespace(id=user_id, is_active=False, deleted_at=None),
    )
    monkeypatch.setattr(
        "app.llm.utils.ensure_user_access_to_model",
        lambda *_args: pytest.fail("inactive user should not reach model access validation"),
    )

    assert jobs.execute_automation_job("automation-1", "user-1") is False
    assert db.closed is True


def test_execute_automation_job_skips_when_automations_feature_disabled(monkeypatch):
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        model_id="model-1",
        is_active=True,
        title="Daily summary",
    )
    db = FakeAutomationDb(automation=automation)

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        jobs,
        "get_user",
        lambda db_arg, user_id: SimpleNamespace(id=user_id, is_active=True, deleted_at=None),
    )
    monkeypatch.setattr(jobs, "get_user_group_setting_value", lambda *_args: False)
    monkeypatch.setattr(
        "app.llm.utils.ensure_user_access_to_model",
        lambda *_args: pytest.fail("disabled automations should not reach model access validation"),
    )

    assert jobs.execute_automation_job("automation-1", "user-1") is False
    assert db.closed is True


def test_webhook_export_payload_never_contains_secret_material():
    trigger = SimpleNamespace(
        name="Deploy hook",
        is_enabled=True,
        payload_mode="append",
        include_headers=False,
        allowed_header_names=[],
        max_body_bytes=1024,
        rate_limit_per_minute=10,
        token_hash="hash",
        token_prefix="prefix",
    )

    payload = utils._webhook_trigger_to_export_payload(trigger)

    assert payload["name"] == "Deploy hook"
    assert payload["is_enabled"] is False
    assert "token_hash" not in payload
    assert "token_prefix" not in payload


def test_webhook_route_records_rejected_delivery_when_owner_access_is_blocked(monkeypatch):
    trigger = SimpleNamespace(
        id="trigger-1",
        automation_id="automation-1",
        user_id="user-1",
        is_enabled=True,
        rate_limit_per_minute=10,
    )
    automation = SimpleNamespace(id="automation-1", is_active=True)
    captured_delivery = {}
    captured_audit = {}

    monkeypatch.setattr(router, "get_webhook_trigger", lambda db, trigger_id: trigger)
    monkeypatch.setattr(router, "get_automation", lambda db, automation_id, user_id=None: automation)
    monkeypatch.setattr(router, "_extract_webhook_secret", lambda request: "secret")
    monkeypatch.setattr(router, "verify_webhook_secret", lambda current_trigger, secret: True)
    monkeypatch.setattr(router, "get_user", lambda db, user_id=None: SimpleNamespace(id=user_id))
    monkeypatch.setattr(
        router,
        "ensure_user_runtime_auth_allowed",
        lambda user, db, **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=423, detail="User is not active")
        ),
    )
    monkeypatch.setattr(
        router,
        "create_webhook_delivery",
        lambda db, **kwargs: captured_delivery.update(kwargs) or SimpleNamespace(id="delivery-1"),
    )
    monkeypatch.setattr(
        router,
        "_audit_automation_event",
        lambda db_log, request, user_id, action, details=None: captured_audit.update(
            {"user_id": user_id, "action": action, "details": details}
        ),
    )

    request = SimpleNamespace(headers={"user-agent": "pytest"}, client=SimpleNamespace(host="127.0.0.1"))
    response = Response()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            router.trigger_automation_webhook_route(
                "trigger-1",
                request,
                response,
                object(),
                object(),
            )
        )

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail == "User is not active"
    assert captured_delivery == {
        "trigger_id": "trigger-1",
        "automation_id": "automation-1",
        "user_id": "user-1",
        "status": "rejected",
        "status_code": 423,
        "error": "User is not active",
        "request_ip": "127.0.0.1",
        "user_agent": "pytest",
    }
    assert captured_audit == {
        "user_id": "user-1",
        "action": "AUTOMATION_WEBHOOK_ACCESS_BLOCKED",
        "details": {
            "automation_id": "automation-1",
            "trigger_id": "trigger-1",
            "status_code": 423,
        },
    }


def test_webhook_route_hides_missing_owner_behind_trigger_not_found(monkeypatch):
    trigger = SimpleNamespace(
        id="trigger-1",
        automation_id="automation-1",
        user_id="user-1",
        is_enabled=True,
        rate_limit_per_minute=10,
    )
    automation = SimpleNamespace(id="automation-1", is_active=True)
    captured_delivery = {}

    monkeypatch.setattr(router, "get_webhook_trigger", lambda db, trigger_id: trigger)
    monkeypatch.setattr(router, "get_automation", lambda db, automation_id, user_id=None: automation)
    monkeypatch.setattr(router, "_extract_webhook_secret", lambda request: "secret")
    monkeypatch.setattr(router, "verify_webhook_secret", lambda current_trigger, secret: True)
    monkeypatch.setattr(
        router,
        "get_user",
        lambda db, user_id=None: (_ for _ in ()).throw(HTTPException(status_code=404, detail="User not found")),
    )
    monkeypatch.setattr(
        router,
        "create_webhook_delivery",
        lambda db, **kwargs: captured_delivery.update(kwargs) or SimpleNamespace(id="delivery-1"),
    )

    request = SimpleNamespace(headers={"user-agent": "pytest"}, client=SimpleNamespace(host="127.0.0.1"))
    response = Response()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            router.trigger_automation_webhook_route(
                "trigger-1",
                request,
                response,
                object(),
                object(),
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Webhook trigger not found"
    assert captured_delivery == {
        "trigger_id": "trigger-1",
        "automation_id": "automation-1",
        "user_id": "user-1",
        "status": "rejected",
        "status_code": 404,
        "error": "Webhook owner not found",
        "request_ip": "127.0.0.1",
        "user_agent": "pytest",
    }


def test_job_rejects_webhook_delivery_when_runtime_auth_is_blocked(monkeypatch):
    automation = SimpleNamespace(
        id="automation-1",
        user_id="user-1",
        is_active=True,
        title="Deploy",
        model_id="model-1",
    )
    delivery_update = {}
    notification_calls = []

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return automation

    class FakeDb:
        def __init__(self):
            self.closed = False

        def query(self, _model):
            return FakeQuery()

        def close(self):
            self.closed = True

    fake_db = FakeDb()

    monkeypatch.setattr(jobs, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        jobs,
        "get_user",
        lambda db, user_id: SimpleNamespace(
            id=user_id,
            deleted_at=None,
            is_active=True,
            account_type="regular",
            temporary_expires_at=None,
            lock={},
            role="user",
            group_id="group-1",
        ),
    )
    monkeypatch.setattr(
        jobs,
        "ensure_user_runtime_auth_allowed",
        lambda user, db, **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=423, detail="User is not active")
        ),
    )
    monkeypatch.setattr(jobs, "get_user_group_setting_value", lambda *_args: True)
    monkeypatch.setattr(
        jobs,
        "update_webhook_delivery",
        lambda db, delivery_id, **kwargs: delivery_update.update(
            {"delivery_id": delivery_id, **kwargs}
        ),
    )
    monkeypatch.setattr(
        jobs,
        "create_chat",
        lambda *args, **kwargs: notification_calls.append("create_chat"),
    )
    monkeypatch.setattr(
        jobs,
        "create_user_notification",
        lambda *args, **kwargs: notification_calls.append("notification"),
    )

    result = jobs.execute_automation_job(
        "automation-1",
        "user-1",
        scheduled_slot="webhook:trigger-1:delivery-1",
        trigger_context={
            "type": "webhook",
            "trigger_id": "trigger-1",
            "delivery_id": "delivery-1",
        },
    )

    assert result is False
    assert delivery_update == {
        "delivery_id": "delivery-1",
        "status": "failed",
        "status_code": 423,
        "error": "User is not active",
    }
    assert notification_calls == []
    assert fake_db.closed is True
