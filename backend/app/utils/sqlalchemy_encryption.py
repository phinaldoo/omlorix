import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, String, TypeDecorator

from app.utils.encryption import encrypt_value, decrypt_value


logger = logging.getLogger(__name__)


def _decrypt_with_fallback(value: Any, *, field: str) -> Any:
    """Attempt to decrypt a value and fail closed when stored ciphertext is invalid."""
    if not isinstance(value, str):
        # Likely legacy plaintext JSON/object data.
        return value
    try:
        return decrypt_value(value)
    except ValueError as exc:
        logger.error("Failed to decrypt value for %s; refusing to return stored data as plaintext.", field)
        raise ValueError(f"Failed to decrypt value for {field}") from exc


class EncryptedString(TypeDecorator):
    """Encrypts/decrypts string-ish values transparently."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return value
        if not isinstance(value, str):
            value = str(value)
        return encrypt_value(value)

    def process_result_value(self, value: Any, dialect):
        if value is None:
            return value
        decrypted = _decrypt_with_fallback(value, field="EncryptedString")
        if decrypted is None:
            return None
        return decrypted


class EncryptedJSON(TypeDecorator):
    """Encrypts full JSON payloads (dict/list) at rest."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return value
        try:
            serialized = json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("EncryptedJSON columns only support JSON-serializable values") from exc
        return encrypt_value(serialized)

    def process_result_value(self, value: Any, dialect):
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            # Legacy plaintext JSON directly from the database.
            return value
        decrypted = _decrypt_with_fallback(value, field="EncryptedJSON")
        if decrypted is None:
            return None
        if isinstance(decrypted, (dict, list)):
            return decrypted
        try:
            return json.loads(decrypted)
        except (TypeError, ValueError) as exc:
            logger.error("Failed to decode decrypted JSON payload.")
            raise ValueError("Failed to decode decrypted JSON payload") from exc


class EncryptedDateTime(TypeDecorator):
    """Encrypts datetime values by storing ISO-8601 strings."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return value
        if isinstance(value, str):
            serialized = value
        elif isinstance(value, datetime):
            aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            serialized = aware.isoformat()
        else:
            raise ValueError("EncryptedDateTime columns require datetime or ISO-8601 strings")
        return encrypt_value(serialized)

    def process_result_value(self, value: Any, dialect):
        if value is None:
            return value
        decrypted = _decrypt_with_fallback(value, field="EncryptedDateTime")
        if decrypted is None:
            return None
        if isinstance(decrypted, datetime):
            return decrypted
        try:
            return datetime.fromisoformat(decrypted)
        except (TypeError, ValueError) as exc:
            logger.error("Failed to parse decrypted datetime payload.")
            raise ValueError("Failed to parse decrypted datetime payload") from exc
