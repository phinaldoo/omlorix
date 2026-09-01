from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace


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

from app.settings import utils as settings_utils
from app.settings.models import SENSITIVE_SETTING_RESPONSE_MASK


def test_page_settings_masks_sensitive_values_without_decrypting(monkeypatch):
    page = SimpleNamespace(
        page_name="login_general",
        data={
            "smtp_host": "smtp.example.test",
            "smtp_password": "enc:v1:not-a-real-ciphertext",
        },
    )

    def get_settings_page(_db, page_name):
        assert page_name == "login_general"
        return page

    def get_settings_page_data(_db, page_name, *, decrypt_sensitive_values=True):
        assert page_name == "login_general"
        assert decrypt_sensitive_values is False
        return page.data

    monkeypatch.setattr(settings_utils, "get_settings_page", get_settings_page)
    monkeypatch.setattr(settings_utils, "get_settings_page_data", get_settings_page_data)

    response = settings_utils.get_page_settings_by_page("login_general", object())

    assert response.data["smtp_host"] == "smtp.example.test"
    assert response.data["smtp_password"] == SENSITIVE_SETTING_RESPONSE_MASK


def test_update_page_key_value_preserves_masked_sensitive_value(monkeypatch):
    page = SimpleNamespace(
        page_name="login_general",
        data={
            "smtp_host": "smtp.example.test",
            "smtp_password": "enc:v1:stored-ciphertext",
        },
    )
    db = SimpleNamespace(commit=lambda: (_ for _ in ()).throw(AssertionError("commit called")))

    def get_settings_page(_db, page_name):
        assert page_name == "login_general"
        return page

    monkeypatch.setattr(settings_utils, "get_settings_page", get_settings_page)

    response = settings_utils.update_page_key_value_by_page_and_key(
        "login_general",
        "smtp_password",
        SENSITIVE_SETTING_RESPONSE_MASK,
        db,
    )

    assert page.data["smtp_password"] == "enc:v1:stored-ciphertext"
    assert response.data["smtp_password"] == SENSITIVE_SETTING_RESPONSE_MASK
