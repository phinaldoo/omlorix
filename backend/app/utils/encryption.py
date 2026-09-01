import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
_CIPHER_SUITE = None

def get_cipher_suite():
    global _CIPHER_SUITE
    if _CIPHER_SUITE:
        return _CIPHER_SUITE
    
    if not _ENCRYPTION_KEY:
        logger.error("ENCRYPTION_KEY environment variable is not set. Encryption will fail.")
        raise ValueError("ENCRYPTION_KEY environment variable is not set.")
    
    try:
        _CIPHER_SUITE = Fernet(_ENCRYPTION_KEY)
    except Exception as e:
        logger.error(f"Invalid ENCRYPTION_KEY: {e}")
        raise ValueError(f"Invalid ENCRYPTION_KEY: {e}")
        
    return _CIPHER_SUITE

def encrypt_value(value: str) -> str:
    """Encrypts a string value."""
    if value is None:
        return None
    if not value:
        return ""
    
    cipher = get_cipher_suite()
    encrypted_bytes = cipher.encrypt(value.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")

def decrypt_value(value: str) -> str:
    """Decrypts a string value."""
    if value is None:
        return None
    if not value:
        return ""
    
    cipher = get_cipher_suite()
    try:
        decrypted_bytes = cipher.decrypt(value.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        logger.error("Failed to decrypt value: %s", e)
        # Strict mode: do not return the original value if decryption fails to avoid persisting nulls.
        raise ValueError(
            "Failed to decrypt value. The key might be invalid or the data is corrupted."
        ) from e
