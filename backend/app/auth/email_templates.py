from html import escape

from app.auth.email_localization import get_email_copy, resolve_email_language


def _normalize_application_name(application_name: str | None) -> tuple[str, str]:
    plain_name = str(application_name or "").replace("\r", " ").replace("\n", " ").strip() or "Omlorix"
    return plain_name, escape(plain_name)


def _format_copy(copy_template: str, *, html: bool, **values: object) -> str:
    formatted_values: dict[str, object] = {}
    for key, value in values.items():
        if html:
            formatted_values[key] = escape(str(value))
        else:
            formatted_values[key] = value
    return copy_template.format(**formatted_values)


def get_2fa_email_html(
    code: str,
    expires_in_minutes: int,
    application_name: str = "Omlorix",
    language_code: str = "en",
) -> str:
    plain_name, _ = _normalize_application_name(application_name)
    copy = get_email_copy("twofa", resolve_email_language(language_code))
    html_lang = escape(copy["lang"], quote=True)
    html_dir = escape(copy["dir"], quote=True)
    html_align = escape(copy["align"], quote=True)
    title = _format_copy(copy["html_title"], html=True, application_name=plain_name)
    headline = _format_copy(copy["headline"], html=True)
    expires_text = _format_copy(copy["expires_text"], html=True, expires_in_minutes=expires_in_minutes)
    ignore_text = _format_copy(copy["ignore_text"], html=True)
    html_name = escape(plain_name)
    html_code = escape(str(code))
    return f"""<!doctype html>
<html lang="{html_lang}" dir="{html_dir}" xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="x-apple-disable-message-reformatting" />
    <meta name="color-scheme" content="light dark" />
    <meta name="supported-color-schemes" content="light dark" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: light dark;
        supported-color-schemes: light dark;
      }}

      body,
      table,
      td,
      p,
      a {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }}

      @media only screen and (max-width: 600px) {{
        .container {{
          width: 100% !important;
        }}

        .content {{
          padding: 32px 20px !important;
        }}

        .code {{
          font-size: 32px !important;
          letter-spacing: 6px !important;
        }}
      }}

      @media (prefers-color-scheme: dark) {{
        body,
        .email-bg {{
          background: #0b0b0c !important;
        }}

        .card {{
          background: #0b0b0c !important;
        }}

        .logo,
        .headline,
        .code {{
          color: #ffffff !important;
        }}

        .muted {{
          color: #a1a1aa !important;
        }}

        .rule {{
          border-color: #27272a !important;
        }}
      }}
    </style>
  </head>
  <body
    class="email-bg"
    style="margin:0;padding:0;background:#ffffff;color:#111111;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;direction:{html_dir};"
  >
    <table
      role="presentation"
      cellpadding="0"
      cellspacing="0"
      border="0"
      width="100%"
      style="border-collapse:collapse;background:#ffffff;margin:0;padding:0;width:100%;"
    >
      <tr>
        <td align="center" style="padding:0;">
          <table
            role="presentation"
            cellpadding="0"
            cellspacing="0"
            border="0"
            width="100%"
            class="container card"
            style="border-collapse:collapse;max-width:560px;width:100%;margin:0 auto;background:#ffffff;"
          >
            <tr>
              <td class="content" style="padding:40px 24px 40px 24px;text-align:{html_align};">
                <div
                  class="logo"
                  style="font-size:18px;line-height:1.2;font-weight:700;letter-spacing:0.2px;color:#111111;margin:0 0 28px 0;"
                >
                  {html_name}
                </div>

                <div
                  class="headline muted"
                  style="font-size:14px;line-height:1.5;color:#666666;margin:0 0 14px 0;"
                >
                  {headline}
                </div>

                <div
                  class="code"
                  style="font-size:40px;line-height:1.1;font-weight:700;letter-spacing:8px;color:#111111;margin:0 0 20px 0;"
                >
                  {html_code}
                </div>

                <div
                  class="rule"
                  style="border-top:1px solid #e5e5e5;font-size:1px;line-height:1px;height:1px;margin:0 0 20px 0;"
                >
                  &nbsp;
                </div>

                <p
                  class="muted"
                  style="margin:0 0 8px 0;font-size:14px;line-height:1.6;color:#666666;"
                >
                  {expires_text}
                </p>

                <p
                  class="muted"
                  style="margin:0;font-size:14px;line-height:1.6;color:#666666;"
                >
                  {ignore_text}
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def get_2fa_deactivated_email_html(
    application_name: str = "Omlorix",
    language_code: str = "en",
) -> str:
    """Render the localized out-of-band notice for a 2FA deactivation."""
    plain_name, html_name = _normalize_application_name(application_name)
    copy = get_email_copy("twofa", resolve_email_language(language_code))
    html_lang = escape(copy["lang"], quote=True)
    html_dir = escape(copy["dir"], quote=True)
    html_align = escape(copy["align"], quote=True)
    title = _format_copy(copy["deactivated_html_title"], html=True, application_name=plain_name)
    headline = _format_copy(copy["deactivated_headline"], html=True)
    body = _format_copy(copy["deactivated_body"], html=True, application_name=plain_name)
    action = _format_copy(copy["deactivated_action"], html=True, application_name=plain_name)
    return f"""<!doctype html>
<html lang="{html_lang}" dir="{html_dir}" xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="color-scheme" content="light dark" />
    <title>{title}</title>
  </head>
  <body style="margin:0;padding:0;background:#ffffff;color:#111111;direction:{html_dir};">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;background:#ffffff;">
      <tr>
        <td align="center" style="padding:0;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;max-width:560px;width:100%;margin:0 auto;background:#ffffff;">
            <tr>
              <td style="padding:40px 24px;text-align:{html_align};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
                <div style="font-size:18px;line-height:1.2;font-weight:700;margin:0 0 28px 0;">{html_name}</div>
                <h1 style="font-size:22px;line-height:1.35;margin:0 0 18px 0;">{headline}</h1>
                <p style="font-size:15px;line-height:1.6;margin:0 0 14px 0;">{body}</p>
                <p style="font-size:15px;line-height:1.6;font-weight:600;margin:0;">{action}</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def get_password_reset_email_html(
    reset_link: str,
    application_name: str = "Omlorix",
    language_code: str = "en",
) -> str:
    plain_name, _ = _normalize_application_name(application_name)
    copy = get_email_copy("password_reset", resolve_email_language(language_code))
    html_lang = escape(copy["lang"], quote=True)
    html_dir = escape(copy["dir"], quote=True)
    html_align = escape(copy["align"], quote=True)
    title = _format_copy(copy["html_title"], html=True, application_name=plain_name)
    headline = _format_copy(copy["headline"], html=True)
    intro = _format_copy(copy["intro"], html=True, application_name=plain_name)
    button_text = _format_copy(copy["button_text"], html=True)
    expires_text = _format_copy(copy["expires_text"], html=True, expires_in_minutes=30)
    ignore_text = _format_copy(copy["ignore_text"], html=True)
    html_name = escape(plain_name)
    html_reset_link = escape(reset_link, quote=True)
    return f"""<!doctype html>
<html lang="{html_lang}" dir="{html_dir}" xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="x-apple-disable-message-reformatting" />
    <meta name="color-scheme" content="light dark" />
    <meta name="supported-color-schemes" content="light dark" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: light dark;
        supported-color-schemes: light dark;
      }}

      body,
      table,
      td,
      p,
      a {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }}

      @media only screen and (max-width: 600px) {{
        .container {{
          width: 100% !important;
        }}

        .content {{
          padding: 32px 20px !important;
        }}

        .btn-wrap {{
          width: 100% !important;
        }}

        .btn {{
          display: block !important;
          width: auto !important;
          max-width: 100% !important;
          box-sizing: border-box !important;
          text-align: center !important;
        }}
      }}

      @media (prefers-color-scheme: dark) {{
        body,
        .email-bg {{
          background: #0b0b0c !important;
        }}

        .card {{
          background: #0b0b0c !important;
        }}

        .logo,
        .headline {{
          color: #ffffff !important;
        }}

        .muted {{
          color: #a1a1aa !important;
        }}

        .rule {{
          border-color: #27272a !important;
        }}
      }}
    </style>
  </head>
  <body
    class="email-bg"
    style="margin:0;padding:0;background:#ffffff;color:#111111;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;direction:{html_dir};"
  >
    <table
      role="presentation"
      cellpadding="0"
      cellspacing="0"
      border="0"
      width="100%"
      style="border-collapse:collapse;background:#ffffff;margin:0;padding:0;width:100%;"
    >
      <tr>
        <td align="center" style="padding:0;">
          <table
            role="presentation"
            cellpadding="0"
            cellspacing="0"
            border="0"
            width="100%"
            class="container card"
            style="border-collapse:collapse;max-width:560px;width:100%;margin:0 auto;background:#ffffff;"
          >
            <tr>
              <td class="content" style="padding:40px 24px 40px 24px;text-align:{html_align};">
                <div
                  class="logo"
                  style="font-size:18px;line-height:1.2;font-weight:700;letter-spacing:0.2px;color:#111111;margin:0 0 28px 0;"
                >
                  {html_name}
                </div>

                <div
                  class="headline muted"
                  style="font-size:24px;line-height:1.5;font-weight:700;color:#111111;margin:0 0 14px 0;"
                >
                  {headline}
                </div>

                <p
                  class="muted"
                  style="margin:0 0 20px 0;font-size:15px;line-height:1.6;color:#666666;"
                >
                  {intro}
                </p>

                <div class="btn-wrap" style="margin:0 0 24px 0;max-width:100%;text-align:{html_align};">
                  <a
                    href="{html_reset_link}"
                    class="btn"
                    style="display:inline-block;max-width:100%;box-sizing:border-box;padding:12px 24px;background-color:#111111;color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;border-radius:6px;line-height:1.2;"
                  >
                    {button_text}
                  </a>
                </div>

                <div
                  class="rule"
                  style="border-top:1px solid #e5e5e5;font-size:1px;line-height:1px;height:1px;margin:0 0 20px 0;"
                >
                  &nbsp;
                </div>

                <p
                  class="muted"
                  style="margin:0 0 8px 0;font-size:14px;line-height:1.6;color:#666666;"
                >
                  {expires_text}
                </p>

                <p
                  class="muted"
                  style="margin:0;font-size:14px;line-height:1.6;color:#666666;"
                >
                  {ignore_text}
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
