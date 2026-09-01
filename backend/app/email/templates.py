from __future__ import annotations

from datetime import datetime, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime
from html import escape

from app.auth.email_delivery import EmailDeliveryConfig
from app.auth.email_localization import get_email_copy, resolve_email_language
from app.auth.email_templates import get_2fa_email_html, get_password_reset_email_html
from app.email.address import normalize_single_mailbox


def _safe_text(value: object, *, fallback: str = "") -> str:
    return str(value if value is not None else fallback).replace("\r", " ").replace("\n", " ").strip()


def _branded_html(
    *,
    application_name: str,
    language_code: str,
    headline: str,
    intro: str,
    action_url: str | None = None,
    action_label: str | None = None,
    facts: list[tuple[str, str]] | None = None,
    footer: str | None = None,
) -> str:
    lang = resolve_email_language(language_code)
    direction = "rtl" if lang == "ar" else "ltr"
    align = "right" if direction == "rtl" else "left"
    facts_html = "".join(
        f'<tr><th scope="row" style="padding:4px 14px 4px 0;text-align:{align};font-size:13px;color:#666;font-weight:600;vertical-align:top;">{escape(label)}</th>'
        f'<td style="padding:4px 0;font-size:13px;color:#222;vertical-align:top;">{escape(value)}</td></tr>'
        for label, value in (facts or [])
        if value
    )
    button_html = ""
    if action_url and action_label:
        button_html = (
            f'<div style="margin:22px 0;"><a href="{escape(action_url, quote=True)}" '
            'style="display:inline-block;padding:12px 20px;border-radius:6px;background:#111;color:#fff;'
            f'text-decoration:none;font-weight:600;">{escape(action_label)}</a></div>'
        )
    footer_html = (
        f'<p style="margin:22px 0 0;font-size:14px;line-height:1.6;color:#666;">{escape(footer)}</p>'
        if footer
        else ""
    )
    return f"""<!doctype html>
<html lang="{escape(lang, quote=True)}" dir="{direction}">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>{escape(headline)}</title></head>
  <body style="margin:0;background:#fff;color:#111;direction:{direction};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;"><tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;max-width:560px;"><tr><td style="padding:40px 24px;text-align:{align};">
        <div style="font-size:18px;font-weight:700;margin:0 0 28px;">{escape(application_name)}</div>
        <h1 style="font-size:24px;line-height:1.35;margin:0 0 16px;">{escape(headline)}</h1>
        <p style="font-size:15px;line-height:1.6;margin:0 0 18px;color:#444;">{escape(intro)}</p>
        {button_html}
        <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:18px 0;">{facts_html}</table>
        {footer_html}
      </td></tr></table>
    </td></tr></table>
  </body>
</html>"""


def _new_message(
    *,
    recipient: str,
    subject: str,
    plain_text: str,
    html: str,
    message_id: str,
    config: EmailDeliveryConfig,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.sender_header
    message["To"] = Address(addr_spec=normalize_single_mailbox(recipient))
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Auto-Submitted"] = "auto-generated"
    message["X-Auto-Response-Suppress"] = "All"
    message.set_content(plain_text)
    message.add_alternative(html, subtype="html")
    return message


def _render_security(
    *,
    recipient: str,
    payload: dict,
    language_code: str,
    message_id: str,
    config: EmailDeliveryConfig,
) -> EmailMessage:
    copy = get_email_copy("security", language_code)
    event_type = _safe_text(payload.get("event_type"))
    event_title = copy.get(f"event_{event_type}", copy["headline"])
    facts = [
        (copy["when_label"], _safe_text(payload.get("occurred_at"))),
        (copy["device_label"], _safe_text(payload.get("device"))),
        (copy["network_label"], _safe_text(payload.get("network"))),
        (copy["purge_label"], _safe_text(payload.get("purge_at"))),
    ]
    intro = copy["intro"].format(application_name=config.application_name)
    plain_facts = "\n".join(f"{label}: {value}" for label, value in facts if value)
    plain = f"{event_title}\n\n{intro}"
    if plain_facts:
        plain += f"\n\n{plain_facts}"
    if event_type not in {
        "account_deleted",
        "account_deactivated",
        "account_deletion_scheduled",
    }:
        plain += f"\n\n{copy['not_you']}"
    return _new_message(
        recipient=recipient,
        subject=copy["subject"].format(application_name=config.application_name),
        plain_text=plain,
        html=_branded_html(
            application_name=config.application_name,
            language_code=language_code,
            headline=event_title,
            intro=intro,
            facts=facts,
            footer=(
                copy["not_you"]
                if event_type
                not in {
                    "account_deleted",
                    "account_deactivated",
                    "account_deletion_scheduled",
                }
                else None
            ),
        ),
        message_id=message_id,
        config=config,
    )


def _render_email_change(
    *,
    recipient: str,
    payload: dict,
    language_code: str,
    message_id: str,
    config: EmailDeliveryConfig,
) -> EmailMessage:
    copy = get_email_copy("email_change", language_code)
    kind = _safe_text(payload.get("kind"), fallback="verify")
    if kind == "verify":
        prefix = "verify"
        action_url = _safe_text(payload.get("action_url"))
        action_label = copy["verify_button"]
        footer = f"{copy['verify_expires'].format(expires_in_hours=int(payload.get('expires_in_hours') or 24))} {copy['verify_ignore']}"
    elif kind == "requested":
        prefix = "requested"
        action_url = _safe_text(payload.get("action_url"))
        action_label = copy["cancel_button"]
        footer = copy["requested_action"]
    elif kind == "cancelled":
        prefix = "cancelled"
        action_url = None
        action_label = None
        footer = None
    else:
        prefix = "changed"
        action_url = None
        action_label = None
        footer = copy["changed_action"]
    subject = copy[f"{prefix}_subject"].format(application_name=config.application_name)
    headline = copy[f"{prefix}_headline"]
    intro = copy[f"{prefix}_intro"].format(application_name=config.application_name)
    plain = f"{headline}\n\n{intro}"
    if action_url:
        plain += f"\n\n{action_label}: {action_url}"
    if footer:
        plain += f"\n\n{footer}"
    return _new_message(
        recipient=recipient,
        subject=subject,
        plain_text=plain,
        html=_branded_html(
            application_name=config.application_name,
            language_code=language_code,
            headline=headline,
            intro=intro,
            action_url=action_url,
            action_label=action_label,
            footer=footer,
        ),
        message_id=message_id,
        config=config,
    )


def render_outbox_message(row, config: EmailDeliveryConfig) -> EmailMessage:
    """Render a versioned outbox row only inside the isolated mail worker."""

    recipient = _safe_text(row.recipient)
    payload = dict(row.payload or {})
    language_code = resolve_email_language(row.language_code)
    if row.template_type == "password_reset":
        copy = get_email_copy("password_reset", language_code)
        reset_link = _safe_text(payload.get("reset_link"))
        plain = (
            f"{copy['plain_text_intro'].format(application_name=config.application_name)}\n\n"
            f"{copy['plain_text_link'].format(reset_link=reset_link)}\n\n"
            f"{copy['expires_text'].format(expires_in_minutes=int(payload.get('expires_in_minutes') or 30))}\n"
            f"{copy['ignore_text']}"
        )
        return _new_message(
            recipient=recipient,
            subject=copy["subject"].format(application_name=config.application_name),
            plain_text=plain,
            html=get_password_reset_email_html(
                reset_link,
                application_name=config.application_name,
                language_code=language_code,
            ),
            message_id=row.message_id,
            config=config,
        )
    if row.template_type == "twofa_otp":
        copy = get_email_copy("twofa", language_code)
        code = _safe_text(payload.get("code"))
        minutes = int(payload.get("expires_in_minutes") or 5)
        return _new_message(
            recipient=recipient,
            subject=copy["subject"].format(application_name=config.application_name),
            plain_text=copy["plain_text_body"].format(
                application_name=config.application_name,
                code=code,
                expires_in_minutes=minutes,
            ),
            html=get_2fa_email_html(
                code,
                minutes,
                application_name=config.application_name,
                language_code=language_code,
            ),
            message_id=row.message_id,
            config=config,
        )
    if row.template_type == "security_event":
        return _render_security(
            recipient=recipient,
            payload=payload,
            language_code=language_code,
            message_id=row.message_id,
            config=config,
        )
    if row.template_type == "email_change":
        return _render_email_change(
            recipient=recipient,
            payload=payload,
            language_code=language_code,
            message_id=row.message_id,
            config=config,
        )
    raise ValueError("Unsupported email outbox template")
