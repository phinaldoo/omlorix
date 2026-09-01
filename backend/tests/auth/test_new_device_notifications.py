from copy import deepcopy
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.email import devices
from app.email.models import EmailOutbox, TrustedDeviceNotification
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User
from app.utils import encryption as encryption_utils


class _Response:
    def __init__(self):
        self.cookie = None

    def set_cookie(self, **kwargs):
        self.cookie = kwargs


class _Request:
    def __init__(self, cookie=None):
        self.cookies = {devices.DEVICE_COOKIE: cookie} if cookie else {}
        self.headers = {"User-Agent": "Mozilla/5.0 Test Browser"}


def test_new_device_notice_is_deduplicated_and_rate_limited(monkeypatch):
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    monkeypatch.setattr(devices, "should_secure_auth_cookie", lambda *_args: True)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            EmailOutbox.__table__,
            TrustedDeviceNotification.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    user = User(
        id="user-1",
        email="owner@example.com",
        group_id="group-1",
        hashed_password="password-hash",
        first_name="Device",
        last_name="Owner",
        role="user",
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        is_active=True,
        created_at=now,
        last_active_at=now,
    )
    db.add(user)
    db.commit()

    try:
        response = _Response()
        assert devices.register_login_device(
            db,
            request=_Request(),
            response=response,
            user=user,
            client_ip="203.0.113.19",
        ) is True
        cookie = response.cookie["value"]
        assert response.cookie["httponly"] is True
        assert response.cookie["secure"] is True
        assert response.cookie["samesite"] == "lax"

        assert devices.register_login_device(
            db,
            request=_Request(cookie),
            response=_Response(),
            user=user,
            client_ip="203.0.113.19",
        ) is False

        # A credential-stuffing incident or privacy-focused browser can create
        # many valid sessions without a stable cookie. Bound owner alert spam
        # while retaining markers for later recognition.
        outcomes = []
        for suffix in "ABCDE":
            outcomes.append(
                devices.register_login_device(
                    db,
                    request=_Request("x" * 40 + suffix),
                    response=_Response(),
                    user=user,
                    client_ip="198.51.100.24",
                )
            )

        assert outcomes == [True, True, True, True, False]
        assert db.query(TrustedDeviceNotification).count() == 6
        assert db.query(EmailOutbox).count() == devices.MAX_NEW_DEVICE_NOTICES_PER_DAY
    finally:
        db.close()
