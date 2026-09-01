from app.settings.models import (
    decrypt_sensitive_settings_page_data,
    ensure_sensitive_settings_page_encrypted,
)


def test_encrypt_sensitive_settings_page_encrypts_enterprise_sso_client_secret(monkeypatch):
    encrypted_values: list[str] = []

    def fake_encrypt_value(value: str) -> str:
        encrypted_values.append(value)
        return f"encrypted:{value}"

    monkeypatch.setattr("app.settings.models.encrypt_value", fake_encrypt_value)
    monkeypatch.setattr("app.settings.models.decrypt_value", lambda value: value)

    changed, encrypted = ensure_sensitive_settings_page_encrypted(
        "login_enterprise_sso",
        {
            "oidc_client_id": "oidc-client",
            "oidc_client_secret": "plain-oidc-secret",
        },
        treat_values_as_plaintext=False,
    )

    assert changed is True
    assert encrypted["oidc_client_id"] == "oidc-client"
    assert encrypted["oidc_client_secret"] == "enc:v1:encrypted:plain-oidc-secret"
    assert encrypted_values == ["plain-oidc-secret"]


def test_decrypt_sensitive_settings_page_decrypts_enterprise_sso_client_secret(monkeypatch):
    monkeypatch.setattr("app.settings.models.decrypt_value", lambda value: f"decrypted:{value}")

    decrypted = decrypt_sensitive_settings_page_data(
        "login_enterprise_sso",
        {
            "oidc_client_id": "oidc-client",
            "oidc_client_secret": "enc:v1:stored-oidc-secret",
        },
    )

    assert decrypted["oidc_client_id"] == "oidc-client"
    assert decrypted["oidc_client_secret"] == "decrypted:stored-oidc-secret"
