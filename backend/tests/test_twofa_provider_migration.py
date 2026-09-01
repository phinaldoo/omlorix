import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import twofa_provider


class TwoFAProviderMigrationTests:
    def test_legacy_sms_enrollment_requires_reenrollment_without_disabling_user(self, monkeypatch):
        settings = {
            ("login_general", "enable_2fa"): True,
            ("login_general", "force_2fa"): False,
            ("login_general", "twofa_provider"): "sms_twilio",
            ("general", "application_name"): "Omlorix",
        }
        user_settings = {
            ("login_2fa", "enable_2fa"): True,
            ("login_2fa", "provider"): "sms_twilio",
            ("secret", "2fa_secret"): "",
        }
        updates = []

        monkeypatch.setattr(
            twofa_provider,
            "get_value_by_page_and_key",
            lambda page, key, db: settings.get((page, key)),
        )
        monkeypatch.setattr(
            twofa_provider,
            "get_user_setting_value",
            lambda user_id, section, key, db: user_settings.get((section, key)),
        )
        def update_user_settings(user_id, section, key, value, db):
            updates.append((section, key, value))
            user_settings[(section, key)] = value

        monkeypatch.setattr(twofa_provider, "update_user_settings", update_user_settings)
        monkeypatch.setattr(twofa_provider.pyotp, "random_base32", lambda: "JBSWY3DPEHPK3PXP")

        result = twofa_provider.evaluate_login_2fa(
            SimpleNamespace(id="user-1", email="user@example.com"),
            otp_code=None,
            otp_action=None,
            otp_destination=None,
            db=object(),
        )

        assert result["status"] == "otp_setup"
        assert result["provider"] == "totp"
        assert user_settings[("login_2fa", "enable_2fa")] is True
        assert user_settings[("login_2fa", "provider")] == "sms_twilio"
        assert ("login_2fa", "enable_2fa", False) not in updates
        assert ("login_2fa", "provider", "") not in updates
