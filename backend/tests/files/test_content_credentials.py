from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import c2pa

from app.admin.groups.schemas import GROUP_FORM_SCHEMA
from app.files import content_credentials
from app.files import utils as file_utils
from app.groups.defaults import DEFAULT_GROUP_SETTINGS


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_content_credentials_setting_is_disabled_boolean_by_default():
    field = next(
        field
        for section in GROUP_FORM_SCHEMA.sections
        for field in section.fields
        if field.key == "settings.compliance.enable_content_credentials"
    )

    assert DEFAULT_GROUP_SETTINGS["compliance"]["enable_content_credentials"] is False
    assert field.type == "boolean"
    assert field.default is False


def test_c2pa_claim_is_embedded_and_existing_claim_is_preserved(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(content_credentials, "CONTENT_CREDENTIALS_DIR", tmp_path)
    monkeypatch.setattr(
        content_credentials,
        "LOCAL_SIGNER_BUNDLE",
        tmp_path / "local_signer.pem",
    )

    signed, status = content_credentials.apply_content_credentials(
        file_bytes=_TINY_PNG,
        file_type="image/png",
        original_filename="generated.png",
    )

    manifest_store = json.loads(c2pa.Reader("image/png", BytesIO(signed)).json())
    active_manifest = manifest_store["manifests"][manifest_store["active_manifest"]]
    action = next(
        action
        for assertion in active_manifest["assertions"]
        if assertion["label"].startswith("c2pa.actions")
        for action in assertion["data"]["actions"]
    )
    preserved, preserved_status = content_credentials.apply_content_credentials(
        file_bytes=signed,
        file_type="image/png",
        original_filename="generated.png",
    )

    assert status == "embedded"
    assert signed != _TINY_PNG
    assert manifest_store["validation_state"] == "Valid"
    assert action["action"] == "c2pa.created"
    assert action["digitalSourceType"] == content_credentials.DIGITAL_SOURCE_TYPE
    assert action["softwareAgent"]["name"] == "Omlorix"
    assert preserved == signed
    assert preserved_status == "preserved"
    assert (tmp_path / "local_signer.pem").stat().st_mode & 0o077 == 0


def test_generated_media_persistence_applies_group_content_credentials(
    monkeypatch,
    tmp_path: Path,
):
    captured = {}
    monkeypatch.setattr(file_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        file_utils,
        "get_user_group_setting_value",
        lambda _user_id, section, key, _db: (
            section == "compliance" and key == "enable_content_credentials"
        ),
    )
    monkeypatch.setattr(
        file_utils,
        "apply_content_credentials",
        lambda **_kwargs: (b"signed-media", "embedded"),
    )

    def persist_path(_db, **kwargs):
        captured["bytes"] = Path(kwargs["source_path"]).read_bytes()
        captured["meta"] = kwargs["meta"]
        return SimpleNamespace(id=kwargs["file_id"])

    monkeypatch.setattr(file_utils, "persist_generated_file_path", persist_path)

    file_utils.persist_generated_file_bytes(
        object(),
        user_id="user-1",
        original_filename="generated.png",
        file_bytes=_TINY_PNG,
        file_type="image/png",
        file_category="image",
        meta={"origin": "assistant", "image_generation": True},
    )

    assert captured["bytes"] == b"signed-media"
    assert captured["meta"]["content_credentials"] == {
        "standard": "C2PA",
        "status": "embedded",
        "digital_source_type": "trainedAlgorithmicMedia",
        "claim_generator": "Omlorix",
        "signer": "local_instance",
    }


def test_non_generated_assistant_attachments_are_not_marked():
    assert not content_credentials.is_supported_ai_generated_media(
        "image/png",
        {"origin": "assistant", "mcp": True},
    )
    assert not content_credentials.is_supported_ai_generated_media(
        "image/png",
        {"origin": "deep_research_web_image", "image_generation": True},
    )
