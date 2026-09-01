import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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


from app.chats import utils as chat_utils
from app.chats.models import Chats


class _FakeQuery:
    def __init__(self, result=None):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeDb:
    def __init__(self, *chat_results):
        self.chat_results = list(chat_results)

    def query(self, model):
        if model is Chats:
            result = self.chat_results.pop(0) if self.chat_results else None
            return _FakeQuery(result=result)
        return _FakeQuery()


def test_get_chat_for_read_allows_shared_project_members(monkeypatch):
    shared_chat = Chats(id="chat-1", user_id="owner-1", project_id="project-1", meta={})
    db = _FakeDb(None, shared_chat)

    monkeypatch.setattr(chat_utils, "_ensure_chat_available_for_read", lambda chat, detail="Chat not found!": None)
    monkeypatch.setattr("app.projects.models.has_project_access", lambda *_args, **_kwargs: True)

    result = chat_utils.get_chat_for_read("member-1", "chat-1", db)

    assert result is shared_chat
