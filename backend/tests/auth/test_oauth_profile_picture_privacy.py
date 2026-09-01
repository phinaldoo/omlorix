import asyncio
import io
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

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

from app.auth import utils as auth_utils
from app.auth import social as auth_social
from app.settings.defaults import DEFAULT_SETTINGS
from app.users import profile_pictures
from app.users import utils as users_utils
from app.users.defaults import DEFAULT_USER_SETTINGS


@pytest.mark.parametrize(
    ("provider_cls", "settings"),
    [
        (
            auth_social.GoogleAuthProvider,
            {
                "enable_google_oauth": True,
                "enable_google_login": True,
                "google_client_id": "google-client",
                "google_client_secret": "google-secret",
            },
        ),
        (
            auth_social.GitHubAuthProvider,
            {
                "enable_github_oauth": True,
                "enable_github_login": True,
                "github_client_id": "github-client",
                "github_client_secret": "github-secret",
            },
        ),
        (
            auth_social.MicrosoftAuthProvider,
            {
                "enable_microsoft_oauth": True,
                "enable_microsoft_login": True,
                "microsoft_client_id": "microsoft-client",
                "microsoft_client_secret": "microsoft-secret",
            },
        ),
        (
            auth_social.SlackAuthProvider,
            {
                "enable_slack_oauth": True,
                "enable_slack_login": True,
                "slack_client_id": "slack-client",
                "slack_client_secret": "slack-secret",
            },
        ),
    ],
)
def test_social_provider_enablement_uses_provider_switch_without_master_toggle(
    provider_cls, settings
):
    provider = provider_cls.__new__(provider_cls)
    provider.settings = settings

    assert provider.is_enabled() is True


@pytest.mark.parametrize(
    ("provider_cls", "settings"),
    [
        (
            auth_social.GoogleAuthProvider,
            {
                "enable_google_oauth": False,
                "enable_google_login": True,
                "google_client_id": "google-client",
                "google_client_secret": "google-secret",
            },
        ),
        (
            auth_social.GitHubAuthProvider,
            {
                "enable_github_oauth": False,
                "enable_github_login": True,
                "github_client_id": "github-client",
                "github_client_secret": "github-secret",
            },
        ),
        (
            auth_social.MicrosoftAuthProvider,
            {
                "enable_microsoft_oauth": False,
                "enable_microsoft_login": True,
                "microsoft_client_id": "microsoft-client",
                "microsoft_client_secret": "microsoft-secret",
            },
        ),
        (
            auth_social.SlackAuthProvider,
            {
                "enable_slack_oauth": False,
                "enable_slack_login": True,
                "slack_client_id": "slack-client",
                "slack_client_secret": "slack-secret",
            },
        ),
    ],
)
def test_social_provider_enablement_requires_provider_oauth_toggle(
    provider_cls, settings
):
    provider = provider_cls.__new__(provider_cls)
    provider.settings = settings

    assert provider.is_enabled() is False


def test_oauth_profile_picture_import_defaults_to_disabled():
    assert DEFAULT_SETTINGS["login_social"]["enable_google_oauth"] is False
    assert DEFAULT_SETTINGS["login_social"]["enable_github_oauth"] is False
    assert DEFAULT_SETTINGS["login_social"]["enable_slack_oauth"] is False
    assert DEFAULT_SETTINGS["login_social"]["enable_microsoft_oauth"] is False
    assert (
        DEFAULT_SETTINGS["login_social"]["import_google_oauth_profile_picture"] is False
    )
    assert (
        DEFAULT_SETTINGS["login_social"]["import_github_oauth_profile_picture"] is False
    )
    assert (
        DEFAULT_SETTINGS["login_social"]["import_microsoft_oauth_profile_picture"]
        is False
    )
    assert (
        DEFAULT_SETTINGS["login_social"]["import_slack_oauth_profile_picture"] is False
    )
    assert (
        DEFAULT_USER_SETTINGS["social_login"]["oauth_profile_picture_sync_disabled"]
        is False
    )


def test_slack_profile_picture_sync_honors_admin_import_setting(monkeypatch):
    """Enabling Slack avatar import downloads and stores the signed profile URL."""
    saved = {}
    requested_settings = []
    user = SimpleNamespace(id="user-slack", custom_profile_picture=False)

    def get_setting(page, key, db):
        requested_settings.append((page, key))
        return (page, key) == (
            "login_social",
            "import_slack_oauth_profile_picture",
        )

    async def download_profile_picture(url):
        assert url == "https://avatars.slack-edge.com/person.png"
        return b"slack-image", "image/png"

    def save_profile_picture(user_id, **kwargs):
        saved["user_id"] = user_id
        saved.update(kwargs)

    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", get_setting)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: False,
    )
    monkeypatch.setattr(
        auth_utils,
        "_download_social_profile_picture",
        download_profile_picture,
    )
    monkeypatch.setattr(users_utils, "save_oauth_profile_picture", save_profile_picture)

    asyncio.run(
        auth_utils._sync_social_profile_picture(
            user,
            provider="slack",
            user_info={
                "profile_picture_url": "https://avatars.slack-edge.com/person.png",
            },
            db=object(),
        )
    )

    assert requested_settings == [
        ("login_social", "import_slack_oauth_profile_picture")
    ]
    assert saved["user_id"] == "user-slack"
    assert saved["provider"] == "slack"
    assert saved["file_content"] == b"slack-image"
    assert saved["original_filename"] == "slack_avatar.png"


def test_social_profile_picture_sync_skips_download_when_admin_setting_disabled(
    monkeypatch,
):
    calls = []
    user = SimpleNamespace(id="user-1", custom_profile_picture=False)

    monkeypatch.setattr(
        auth_utils,
        "get_value_by_page_and_key",
        lambda page, key, db: (
            False
            if (page, key) == ("login_social", "import_google_oauth_profile_picture")
            else None
        ),
    )

    async def download_profile_picture(_url):
        calls.append("download")
        return b"image", "image/png"

    def save_profile_picture(*args, **kwargs):
        calls.append("save")

    monkeypatch.setattr(
        auth_utils, "_download_social_profile_picture", download_profile_picture
    )
    monkeypatch.setattr(users_utils, "save_oauth_profile_picture", save_profile_picture)

    asyncio.run(
        auth_utils._sync_social_profile_picture(
            user,
            provider="google",
            user_info={"profile_picture_url": "https://example.test/avatar.png"},
            db=object(),
        )
    )

    assert calls == []


def test_microsoft_user_info_does_not_fetch_photo_when_import_disabled(monkeypatch):
    calls = []
    provider = auth_social.MicrosoftAuthProvider.__new__(
        auth_social.MicrosoftAuthProvider
    )
    provider.settings = {"import_microsoft_oauth_profile_picture": False}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"avatar"

        def json(self):
            return {
                "id": "microsoft-subject",
                "mail": "person@example.com",
                "displayName": "Person Example",
                "givenName": "Person",
                "surname": "Example",
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers):
            calls.append(url)
            return FakeResponse()

    async def fake_decode_id_token_verified(_id_token):
        return {
            "oid": "microsoft-subject",
            "tid": "tenant-id",
            "nonce": "request-nonce",
        }

    monkeypatch.setattr(auth_social.httpx, "AsyncClient", lambda: FakeClient())
    monkeypatch.setattr(
        provider, "_decode_id_token_verified", fake_decode_id_token_verified
    )

    user_info = asyncio.run(
        provider.get_user_info("access-token", tokens={"id_token": "id-token"})
    )

    assert calls == [provider.USERINFO_URL]
    assert user_info["email"] == "person@example.com"
    assert "profile_picture_bytes" not in user_info


def test_social_profile_picture_sync_skips_when_user_removed_imported_avatar(
    monkeypatch,
):
    calls = []
    user = SimpleNamespace(id="user-1", custom_profile_picture=False)

    monkeypatch.setattr(
        auth_utils, "get_value_by_page_and_key", lambda page, key, db: True
    )
    monkeypatch.setattr(
        auth_utils, "get_user_setting_value", lambda user_id, page, key, db: True
    )

    def save_profile_picture(*args, **kwargs):
        calls.append("save")

    monkeypatch.setattr(users_utils, "save_oauth_profile_picture", save_profile_picture)

    asyncio.run(
        auth_utils._sync_social_profile_picture(
            user,
            provider="github",
            user_info={
                "profile_picture_bytes": b"image",
                "profile_picture_content_type": "image/png",
            },
            db=object(),
        )
    )

    assert calls == []


def test_social_profile_picture_sync_saves_when_admin_and_user_allow(monkeypatch):
    saved = {}
    user = SimpleNamespace(id="user-1", custom_profile_picture=False)

    monkeypatch.setattr(
        auth_utils, "get_value_by_page_and_key", lambda page, key, db: True
    )
    monkeypatch.setattr(
        auth_utils, "get_user_setting_value", lambda user_id, page, key, db: False
    )

    def save_profile_picture(user_id, **kwargs):
        saved["user_id"] = user_id
        saved.update(kwargs)

    monkeypatch.setattr(users_utils, "save_oauth_profile_picture", save_profile_picture)

    asyncio.run(
        auth_utils._sync_social_profile_picture(
            user,
            provider="microsoft",
            user_info={
                "profile_picture_bytes": b"image",
                "profile_picture_content_type": "image/png",
            },
            db=object(),
        )
    )

    assert saved["user_id"] == "user-1"
    assert saved["provider"] == "microsoft"
    assert saved["file_content"] == b"image"
    assert saved["original_filename"] == "microsoft_avatar.png"


def test_delete_profile_picture_clears_oauth_avatar_and_blocks_future_sync(
    monkeypatch, tmp_path
):
    user_id = "user-1"
    custom_dir = tmp_path / "custom"
    oauth_dir = tmp_path / "oauth"
    custom_dir.mkdir()
    oauth_dir.mkdir()
    custom_file = custom_dir / f"{user_id}.png"
    oauth_file = oauth_dir / f"{user_id}.png"
    custom_file.write_bytes(b"custom")
    oauth_file.write_bytes(b"oauth")
    updates = {}
    profile_flags = []

    monkeypatch.setattr(profile_pictures, "CUSTOM_PROFILE_PICTURE_DIR", custom_dir)
    monkeypatch.setattr(profile_pictures, "OAUTH_PROFILE_PICTURE_DIR", oauth_dir)
    monkeypatch.setattr(
        profile_pictures,
        "update_user_profile_picture_boolean",
        lambda db, target_user_id, value: profile_flags.append((target_user_id, value)),
    )
    monkeypatch.setattr(
        profile_pictures,
        "update_user_settings_bulk",
        lambda target_user_id, payload, db: updates.setdefault(target_user_id, payload),
    )

    assert profile_pictures.delete_profile_picture(user_id, object()) == {
        "status": "success"
    }

    assert profile_flags == [(user_id, False)]
    assert not custom_file.exists()
    assert not oauth_file.exists()
    assert updates[user_id]["social_login"] == {
        "oauth_profile_picture_present": False,
        "oauth_profile_picture_provider": "",
        "oauth_profile_picture_last_synced_at": "",
        "oauth_profile_picture_sync_disabled": True,
    }


def test_profile_picture_reencode_failure_rejects_original_upload(monkeypatch):
    """A failed safety rewrite must never fall back to attacker-controlled bytes."""
    source = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(source, format="PNG")

    def fail_save(*_args, **_kwargs):
        raise OSError("private encoder detail")

    monkeypatch.setattr(Image.Image, "save", fail_save)

    with pytest.raises(HTTPException) as excinfo:
        profile_pictures._validate_and_prepare_profile_picture_bytes(
            file_content=source.getvalue(),
            original_filename="avatar.png",
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "The image could not be processed safely."
    assert "private encoder detail" not in excinfo.value.detail


def test_profile_picture_verification_failure_is_rejected_and_redacted(monkeypatch):
    """Unexpected Pillow verification errors are invalid input, not a fallback path."""

    class MalformedImage:
        format = "PNG"

        def verify(self):
            raise RuntimeError("private decoder detail")

    monkeypatch.setattr(
        profile_pictures.Image,
        "open",
        lambda *_args, **_kwargs: MalformedImage(),
    )

    with pytest.raises(HTTPException) as excinfo:
        profile_pictures._validate_and_prepare_profile_picture_bytes(
            file_content=b"malformed",
            original_filename="avatar.png",
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "File is not a valid image."
    assert "private decoder detail" not in excinfo.value.detail


def test_profile_picture_reads_do_not_repair_missing_files(monkeypatch, tmp_path):
    """GET helpers report stale state without changing settings or the database."""
    custom_dir = tmp_path / "custom"
    oauth_dir = tmp_path / "oauth"
    custom_dir.mkdir()
    oauth_dir.mkdir()
    user = SimpleNamespace(id="user-1", custom_profile_picture=True)

    monkeypatch.setattr(profile_pictures, "CUSTOM_PROFILE_PICTURE_DIR", custom_dir)
    monkeypatch.setattr(profile_pictures, "OAUTH_PROFILE_PICTURE_DIR", oauth_dir)
    monkeypatch.setattr(profile_pictures, "get_user", lambda *_args: user)
    monkeypatch.setattr(
        profile_pictures,
        "get_user_setting_value",
        lambda _user_id, _page, key, _db: key == "oauth_profile_picture_present",
    )

    def reject_write(*_args, **_kwargs):
        raise AssertionError("profile-picture reads must not mutate state")

    monkeypatch.setattr(
        profile_pictures, "update_user_profile_picture_boolean", reject_write
    )
    monkeypatch.setattr(profile_pictures, "update_user_settings_bulk", reject_write)
    monkeypatch.setattr(profile_pictures, "clear_oauth_profile_picture", reject_write)

    assert profile_pictures.get_profile_picture_status("user-1", object()) == {
        "has_profile_picture": False,
        "has_custom_profile_picture": False,
        "profile_picture_source": "initials",
        "profile_picture_provider": "",
    }
    with pytest.raises(HTTPException) as excinfo:
        profile_pictures.get_profile_picture("user-1", object())
    assert excinfo.value.status_code == 404
