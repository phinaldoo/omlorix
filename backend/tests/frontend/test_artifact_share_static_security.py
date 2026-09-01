from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CANVAS_SHARE_JS = REPO_ROOT / "frontend" / "js" / "canvas-share.js"


def test_public_canvas_share_runs_active_html_only_through_isolated_proxy():
    source = CANVAS_SHARE_JS.read_text(encoding="utf-8")

    assert "sanitizeRenderedArtifactNode" in source
    assert "sanitizeRenderedArtifactNode(target);" in source
    assert "RESOURCE_TAGS" in source
    assert "'src', 'href', 'xlink:href', 'formaction', 'action', 'poster', 'data'" in source
    assert "attrName === 'srcset'" in source
    assert "@import\\b|url\\s*\\(|image-set\\s*\\(|expression\\s*\\(" in source
    assert "Content-Security-Policy" in source
    assert "script, meta, base" in source
    assert "buildSandboxedPreviewDocument" in source
    assert "navigate-to 'none'" in source
    assert "window.OmlorixCanvasHtmlPreview" in source
    assert "allowScripts: sharedHtmlPreviewState.allowScripts" in source
    assert "allowExternalContent: sharedHtmlPreviewState.allowExternalContent" in source
    assert "sharedHtmlPreviewState.allowExternalContent = !sharedHtmlPreviewState.allowExternalContent" in source
    assert "frame.setAttribute('sandbox', '')" in source  # fail-closed runtime fallback
    assert "doc.documentElement.outerHTML" not in source
