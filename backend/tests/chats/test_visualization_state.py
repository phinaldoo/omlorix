import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chats.models import Chats, ChatMessages, clone_chat_message_for_new_chat
from app.chats.schemas import VisualizationStateRequest, ImportedChatMessage
from app.chats.visualization_state import visualization_state, visualization_followup_context
from app.chats.download import _serialize_chat_message_export
from app.chats.io import _import_single_chat
from app.chats.utils import _serialize_public_chat_rows
from app.database import Base


def session():
    engine = create_engine('sqlite://', execution_options={'schema_translate_map': {'app': None}})
    Base.metadata.create_all(engine, tables=[Chats.__table__, ChatMessages.__table__])
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    db.add(Chats(id='chat', user_id='owner', created_at=now, last_updated_at=now, meta={}))
    db.add(ChatMessages(id='message', chat_id='chat', model_id='model', role='assistant', created_at=now,
                        content=json.dumps([{'type': 'widget', 'content': '<div id="map">Map</div>',
                            'meta': {'widget_type': 'visualization', 'tool_call_id': 'call',
                                     'visualization': {'title': 'Map'}}}])))
    db.commit()
    return db


def test_saved_state_reload_conflict_ownership_and_model_privacy():
    with session() as db:
        assert visualization_state('owner', 'message', 'call', db).revision == 0
        payload = VisualizationStateRequest(widgetState={'modelContent': {'country': 'Germany'}, 'privateContent': {'zoom': 4, 'secret': 'PRIVATE_ONLY'}}, design={'variants': {'mockup': 'Calm'}})
        saved = visualization_state('owner', 'message', 'call', db, payload)
        assert saved.revision == 1
        db.expire_all()
        assert visualization_state('owner', 'message', 'call', db).widgetState['privateContent']['zoom'] == 4
        context = visualization_followup_context([db.get(ChatMessages, 'message')])
        assert 'Germany' in context and 'Calm' in context
        assert 'PRIVATE_ONLY' not in context and 'privateContent' not in context
        with pytest.raises(HTTPException) as conflict:
            visualization_state('owner', 'message', 'call', db, payload)
        assert conflict.value.status_code == 409
        db.rollback()
        for user, call in [('someone-else', 'call'), ('owner', 'another-call')]:
            with pytest.raises(HTTPException) as denied:
                visualization_state(user, 'message', call, db, payload)
            assert denied.value.status_code == 404
        reset = visualization_state('owner', 'message', 'call', db, VisualizationStateRequest(revision=1))
        assert reset.widgetState is None and not reset.design
        assert visualization_followup_context([db.get(ChatMessages, 'message')]) == ''


def test_state_bounds_stale_source_and_portable_clone():
    with session() as db:
        with pytest.raises(ValueError):
            VisualizationStateRequest(widgetState={'privateContent': 'é' * 9000})
        with pytest.raises(ValueError):
            VisualizationStateRequest(widgetState={'modelContent': float('nan')})
        visualization_state('owner', 'message', 'call', db, VisualizationStateRequest(widgetState={'privateContent': 42}))
        message = db.get(ChatMessages, 'message')
        clone = clone_chat_message_for_new_chat(message, 'new-chat', {})
        assert clone.visualization_states == message.visualization_states
        clone.visualization_states['call']['widgetState'] = None
        assert message.visualization_states['call']['widgetState']['privateContent'] == 42
        exported = _serialize_chat_message_export(message)
        imported = ImportedChatMessage.model_validate(exported)
        assert imported.visualization_states == message.visualization_states
        result = _import_single_chat('owner', {'chat': {'title': 'Restored'}, 'messages': [exported]}, db)
        db.commit()
        restored = db.query(ChatMessages).filter(ChatMessages.chat_id == result['chat_id']).one()
        assert visualization_state('owner', restored.id, 'call', db).widgetState['privateContent'] == 42
        assert 'privateContent' not in json.dumps(_serialize_public_chat_rows([message], lambda _: None))
        with pytest.raises(ValueError):
            ImportedChatMessage.model_validate({'role': 'assistant', 'visualization_states': {'call': {'widgetState': {'privateContent': 'x' * 17000}}}})
        message.content = message.content.replace('Map</div>', 'Changed</div>')
        db.commit()
        assert visualization_state('owner', 'message', 'call', db).widgetState is None


def test_temporary_state_is_bounded_and_private_and_context_is_capped():
    block = {'type': 'widget', 'meta': {'widget_type': 'visualization', 'visualization': {'saved_state': {'widgetState': {'modelContent': 'selected', 'privateContent': 'PRIVATE_ONLY'}}}}}
    row = {'role': 'assistant', 'content': [block]}
    context = visualization_followup_context([row] * 20)
    assert context.count('selected') == 8 and 'PRIVATE_ONLY' not in context
