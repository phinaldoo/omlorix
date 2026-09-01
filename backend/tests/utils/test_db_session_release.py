from app.utils.db import release_db_session_before_long_wait


class _FakeSession:
    def __init__(self, *, dirty=False):
        self.new = ()
        self.dirty = (object(),) if dirty else ()
        self.deleted = ()
        self.commits = 0

    def in_transaction(self):
        return True

    def commit(self):
        self.commits += 1


def test_release_db_session_commits_a_clean_read_transaction():
    session = _FakeSession()

    assert release_db_session_before_long_wait(session) is True
    assert session.commits == 1


def test_release_db_session_preserves_pending_writes():
    session = _FakeSession(dirty=True)

    assert release_db_session_before_long_wait(session) is False
    assert session.commits == 0
