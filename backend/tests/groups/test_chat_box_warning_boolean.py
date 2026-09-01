"""Tests for the boolean chat-composer warning group setting."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.init import get_group_settings
from app.groups.models import Group
from app.admin.groups.schemas import GROUP_FORM_SCHEMA, GroupValuesUpdatePayload


def _chat_warning_fields():
    """Return the warning toggle and its dependent message field."""
    fields = {
        field.key: field
        for section in GROUP_FORM_SCHEMA.sections
        for field in section.fields
    }
    return (
        fields["settings.chat.show_chat_box_warning"],
        fields["settings.chat.chat_box_warning_message"],
    )


def test_chat_box_warning_uses_boolean_default_and_schema():
    """Expose the group setting as a boolean toggle throughout the form contract."""
    warning_field, message_field = _chat_warning_fields()

    assert DEFAULT_GROUP_SETTINGS["chat"]["show_chat_box_warning"] is False
    assert warning_field.type == "boolean"
    assert warning_field.default is False
    assert message_field.dependency_value is True


def test_chat_box_warning_update_rejects_string_booleans():
    """Require API clients to send a JSON boolean instead of a string."""
    with pytest.raises(ValidationError, match="Chat warning level must be true or false"):
        GroupValuesUpdatePayload(
            settings={"chat": {"show_chat_box_warning": "true"}}
        )


def test_chat_box_warning_read_repairs_legacy_string_to_boolean():
    """Migrate an older stored string without changing its enabled state."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Group.__table__])
    db = sessionmaker(bind=engine)()
    settings = deepcopy(DEFAULT_GROUP_SETTINGS)
    settings["chat"]["show_chat_box_warning"] = "true"
    now = datetime.now(timezone.utc)
    db.add(
        Group(
            id="legacy-chat-warning",
            name="Legacy Chat Warning",
            settings=settings,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    normalized = get_group_settings("legacy-chat-warning", db)

    persisted = db.query(Group).filter(Group.id == "legacy-chat-warning").one()
    assert normalized["chat"]["show_chat_box_warning"] is True
    assert persisted.settings["chat"]["show_chat_box_warning"] is True
