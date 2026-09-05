"""Trusted relay for the origin-isolated presentation editor.

The editor itself is a data document, whose origin is always opaque. Allowing
same-origin within that sandbox lets its nested srcdoc canvas share that opaque
origin for DOM editing, without sharing the authenticated application's origin.
Authored HTML is never inserted into the HTTP relay document.
"""

EDITOR_PROXY_CSP = "; ".join([
    "default-src 'none'", "script-src 'unsafe-inline'", "script-src-attr 'none'",
    "style-src 'unsafe-inline'", "img-src data: blob: https:", "font-src data:",
    "media-src data: blob:", "connect-src https:", "frame-src 'self' data: blob: https:",
    "worker-src 'none'", "object-src 'none'", "base-uri 'none'",
    "form-action 'none'", "frame-ancestors 'self'",
])

EDITOR_PROXY_HTML = r'''<!doctype html><html><head><meta charset="utf-8">
<style>html,body,iframe{margin:0;width:100%;height:100%;border:0;display:block;overflow:hidden}</style>
</head><body><script>
(() => {
    let view;
    addEventListener('message', event => {
        if (event.source === parent && event.origin === location.origin) {
            if (event.data?.type === 'omlorix-editor-mount') {
                view?.remove();
                view = document.createElement('iframe');
                view.sandbox = 'allow-scripts allow-same-origin';
                view.referrerPolicy = 'no-referrer';
                view.title = String(event.data.title || '');
                // A data URL has its own opaque origin. Never use srcdoc or
                // an HTTP/blob URL here: those would inherit our app origin.
                view.src = 'data:text/html;charset=utf-8,' + encodeURIComponent(String(event.data.html || ''));
                document.body.append(view);
            } else view?.contentWindow.postMessage(event.data, '*');
        } else if (event.source === view?.contentWindow && event.origin === 'null') {
            parent.postMessage(event.data, location.origin);
        }
    });
    parent.postMessage({type: 'omlorix-editor-proxy-ready'}, location.origin);
})();
</script></body></html>'''
