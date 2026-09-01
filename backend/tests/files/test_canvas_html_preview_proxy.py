from __future__ import annotations

from app.files.html_preview import get_canvas_html_preview_proxy_payload


def test_canvas_html_preview_proxy_keeps_authored_code_in_nested_opaque_frame():
    """The trusted proxy may run scripts, but authored content never gets same-origin."""
    payload = get_canvas_html_preview_proxy_payload()
    html = str(payload["html"])
    headers = dict(payload["headers"])

    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "event.source !== parent || event.origin !== location.origin" in html
    assert "const trustedLocalScripts = message.trustedLocalScripts === true" in html
    assert "&& message.allowEval === false" in html
    assert "&& relayVisualizationMessages" in html
    assert "&& !allowAuthenticatedFileHydration" in html
    assert "&& !allowExternalContent" in html
    assert "message.allowScripts === true && (allowExternalContent || trustedLocalScripts)" in html
    assert "sandbox.push('allow-scripts', 'allow-modals')" in html
    assert "sandbox.push('allow-forms', 'allow-popups', 'allow-downloads')" in html
    assert "allow-same-origin" not in html
    assert "allowExternalContent ? 'http: https:' : \"'none'\"" in html
    assert "Object.defineProperty(window, 'localStorage'" in html
    assert "hydrateAuthenticatedCanvasFiles" in html
    assert "allowAuthenticatedFileHydration" in html
    assert "AUTHENTICATED_FILE_HYDRATION_CONCURRENCY = 4" in html
    assert ".sort((left, right) => right.length - left.length)" in html
    assert "localConnections = 'blob: data:'" in html
    assert "parsed.pathname !== '/api/v1/files/download'" in html
    assert "credentials: 'include'" in html
    assert "reader.readAsDataURL(blob)" in html
    assert "doc.querySelectorAll('meta[http-equiv]')" in html
    assert "doc.querySelectorAll('base')" in html


def test_canvas_html_preview_proxy_relays_only_visualization_bridge_messages():
    """Visualization code can reach its host bridge without escaping the sandbox."""
    payload = get_canvas_html_preview_proxy_payload()
    html = str(payload["html"])

    assert "const VISUALIZATION_TO_HOST = new Set" in html
    assert "'omlorix-code-block-preview-height'" in html
    assert "'omlorix:visualization-request'" in html
    assert "const VISUALIZATION_TO_VIEW = new Set" in html
    assert "'omlorix:visualization-response'" in html
    assert "'omlorix:visualization-theme'" in html
    assert "event.source === view?.contentWindow" in html
    assert "VISUALIZATION_TO_HOST.has(event.data?.type)" in html
    assert "event.source !== parent || event.origin !== location.origin" in html
    assert "VISUALIZATION_TO_VIEW.has(event.data?.type)" in html
    assert "permissionCsp(allowScripts, allowExternalContent, allowEval)" in html
    assert 'allowEval ? " \'unsafe-eval\'" : \'\'' in html
