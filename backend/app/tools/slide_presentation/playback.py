"""Executable presentation documents for playback, editing and external rendering."""
from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.tools.widget_frames import store_isolated_frame_document

NETWORK_META = {"omlorix-connect-src", "omlorix-frame-src", "omlorix-img-src"}
_RUNTIME = Path(__file__).with_name("playback.js").read_text(encoding="utf-8")


def public_https_origin(value: str, *, app_origin: str = "") -> str | None:
    """Accept explicit HTTPS origins, never CSP wildcards, credentials or local hosts."""
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if (url.scheme != "https" or not host or url.username or url.password
                or url.port not in {None, 443} or url.path not in {"", "/"}
                or url.query or url.fragment or any(c in value for c in "\"'<>;*\\ \t\r\n")):
            return None
        if host == (urlsplit(app_origin).hostname or "").lower().rstrip("."):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", host) or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
                return None
        return f"https://[{host}]" if ":" in host else f"https://{host}"
    except ValueError:
        return None


def network_origins(soup: BeautifulSoup, name: str, app_origin: str = "") -> list[str]:
    values = []
    for meta in soup.find_all("meta", attrs={"name": name}):
        for value in str(meta.get("content") or "").split():
            origin = public_https_origin(value, app_origin=app_origin)
            if origin and origin not in values:
                values.append(origin)
    return values[:16]


def prepare_presentation_document(html: str, *, mode: str = "render", app_origin: str = "", slide_index: int = 0) -> dict:
    from app.tools.slide_presentation.sanitizer import sanitize_slide_presentation_html, validate_slide_presentation_html

    html = sanitize_slide_presentation_html(html)
    count = validate_slide_presentation_html(html)
    soup = BeautifulSoup(html, "html.parser")
    for meta in soup.find_all("meta", attrs={"http-equiv": True}):
        meta.decompose()
    channel = secrets.token_urlsafe(24)
    connect = network_origins(soup, "omlorix-connect-src", app_origin)
    frames = network_origins(soup, "omlorix-frame-src", app_origin)
    images = network_origins(soup, "omlorix-img-src", app_origin)
    csp = "; ".join([
        "default-src 'none'", "base-uri 'none'", "object-src 'none'",
        "form-action 'none'", "frame-ancestors 'self'", "script-src 'unsafe-inline'",
        "script-src-attr 'none'", "style-src 'unsafe-inline'", "font-src data:",
        "worker-src 'none'", "media-src data: blob:",
        "connect-src " + (" ".join(connect) or "'none'"),
        "frame-src " + (" ".join(frames) or "'none'"),
        "img-src data: blob: " + " ".join(images),
    ])
    # Lazy embeds only become network-active when their slide is entered.
    for frame in soup.find_all("iframe"):
        frame["sandbox"] = "allow-scripts"
        frame["referrerpolicy"] = "no-referrer"
        frame.attrs.pop("allow", None)
        src = str(frame.get("src") or "")
        url = urlsplit(src)
        origin = public_https_origin(f"{url.scheme}://{url.netloc}", app_origin=app_origin)
        frame["data-omlorix-embed-src"] = src if origin in frames else ""
        frame.attrs.pop("src", None)
    soup.html["data-omlorix-mode"] = mode
    config = json.dumps({"channel": channel, "initialIndex": max(0, min(slide_index, count - 1)), "connect": connect, "mode": mode})
    bootstrap = soup.new_tag("script")
    bootstrap.string = f"({_RUNTIME})({config});"
    policy = soup.new_tag("meta", attrs={"http-equiv": "Content-Security-Policy", "content": csp.replace("frame-ancestors 'self'; ", "")})
    soup.head.insert(0, bootstrap)
    soup.head.insert(0, policy)
    style = soup.new_tag("style")
    style.string = """
html,body{width:1920px!important;height:1080px!important;margin:0!important;padding:0!important;overflow:hidden!important}
body{display:block!important;background:Canvas;color-scheme:light}
section.slide{position:absolute!important;inset:0!important;margin:0!important;width:1920px!important;height:1080px!important;overflow:hidden;box-sizing:border-box}
section.slide:not(.omlorix-active):not(.omlorix-leaving){display:none!important}
section.slide.omlorix-leaving{pointer-events:none!important}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
"""
    if mode == "present":
        soup.head.append(style)
    return {"html": str(soup), "source": html, "csp": csp, "runtime": bootstrap.string,
            "channel_id": channel, "slide_count": count}


def create_playback_frame(*, user_id: str, html: str, app_origin: str, slide_index: int = 0) -> dict:
    document = prepare_presentation_document(html, mode="present", app_origin=app_origin, slide_index=slide_index)
    result = store_isolated_frame_document(user_id=user_id, html=document["html"],
                                          csp="sandbox allow-scripts; " + document["csp"],
                                          widget_type="slide_presentation")
    return {**result, "channel_id": document["channel_id"], "slide_count": document["slide_count"]}


def build_slide_render_document(html: str) -> str:
    """Supply live HTML and the presentation API to the JavaScript-capable renderer."""
    return prepare_presentation_document(html)["html"]


def prepare_preview_source(html: str) -> str:
    """Close streamed markup, but never execute an unfinished script/style."""
    from app.tools.slide_presentation.sanitizer import MAX_PRESENTATION_HTML_BYTES

    if len(html.encode('utf-8')) > MAX_PRESENTATION_HTML_BYTES:
        raise ValueError('presentation_html_too_large')
    # Streaming can stop anywhere in a raw-text element. Discard that unfinished
    # tail until its closing tag arrives; BeautifulSoup repairs other markup.
    for tag in ("script", "style"):
        starts = list(re.finditer(rf"<{tag}\b[^>]*>", html, re.I))
        if starts and not re.search(rf"</{tag}\s*>", html[starts[-1].end():], re.I):
            html = html[:starts[-1].start()]
    soup = BeautifulSoup(html, "html.parser")
    # The last slide's opening attributes may still be incomplete. Only expose
    # a valid sequential prefix; later chunks fill in the missing slide.
    for index, slide in enumerate(soup.select("section.slide"), 1):
        if str(slide.get("data-slide-index")) != str(index) or not slide.get("data-slide-title"):
            slide.decompose()
    return "<!DOCTYPE html>" + re.sub(r"^\s*(?:<!DOCTYPE[^>]*>\s*)+", "", str(soup), flags=re.I)
