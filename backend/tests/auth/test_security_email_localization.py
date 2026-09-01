import json
from pathlib import Path
from types import SimpleNamespace

from app.auth import email_localization
from app.auth.email_delivery import EmailDeliveryConfig
from app.email.templates import render_outbox_message


def test_security_email_locales_are_complete_and_renderable():
    locale_root = Path(email_localization.__file__).resolve().parent / "email_locales"
    config = EmailDeliveryConfig(
        email_from="security@example.com",
        application_name="Omlorix",
        smtp_host="smtp.example.com",
    )

    for email_type in ("security", "email_change"):
        english = json.loads(
            (locale_root / "en" / f"{email_type}.json").read_text(
                encoding="utf-8"
            )
        )
        for language in email_localization.SUPPORTED_EMAIL_LANGUAGES:
            localized = json.loads(
                (locale_root / language / f"{email_type}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert set(localized) == set(english)
            assert all(str(value).strip() for value in localized.values())

    for language in email_localization.SUPPORTED_EMAIL_LANGUAGES:
        security_message = render_outbox_message(
            SimpleNamespace(
                recipient="owner@example.com",
                payload={
                    "event_type": "new_device",
                    "occurred_at": "2026-08-29T12:00:00+00:00",
                    "device": "Desktop browser",
                    "network": "203.0.113.0/24",
                },
                language_code=language,
                template_type="security_event",
                message_id=f"<security-{language}@omlorix.invalid>",
            ),
            config,
        )
        change_message = render_outbox_message(
            SimpleNamespace(
                recipient="new@example.com",
                payload={
                    "kind": "verify",
                    "action_url": "https://chat.example.com/login#email_change_token=secret",
                    "expires_in_hours": 24,
                },
                language_code=language,
                template_type="email_change",
                message_id=f"<change-{language}@omlorix.invalid>",
            ),
            config,
        )

        assert security_message["Date"]
        assert security_message.is_multipart()
        assert change_message.is_multipart()
        html = change_message.get_body(preferencelist=("html",)).get_content()
        assert f'lang="{language}"' in html
        assert f'dir="{"rtl" if language == "ar" else "ltr"}"' in html

    changed_copy = email_localization.get_email_copy("email_change", "en")
    changed_message = render_outbox_message(
        SimpleNamespace(
            recipient="old@example.com",
            payload={"kind": "changed"},
            language_code="en",
            template_type="email_change",
            message_id="<changed@omlorix.invalid>",
        ),
        config,
    )
    changed_plain = changed_message.get_body(preferencelist=("plain",)).get_content()
    assert changed_copy["changed_action"] in changed_plain
    assert changed_copy["requested_action"] not in changed_plain

    security_copy = email_localization.get_email_copy("security", "en")
    scheduled_message = render_outbox_message(
        SimpleNamespace(
            recipient="owner@example.com",
            payload={
                "event_type": "account_deletion_scheduled",
                "occurred_at": "2026-08-29T12:00:00+00:00",
                "purge_at": "2026-09-28T12:00:00+00:00",
            },
            language_code="en",
            template_type="security_event",
            message_id="<scheduled-deletion@omlorix.invalid>",
        ),
        config,
    )
    scheduled_plain = scheduled_message.get_body(
        preferencelist=("plain",)
    ).get_content()
    assert security_copy["event_account_deletion_scheduled"] in scheduled_plain
    assert security_copy["not_you"] not in scheduled_plain
