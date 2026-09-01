"""Trusted proxy document for isolated interactive Canvas HTML previews.

Canvas source can contain arbitrary authored JavaScript.  Rendering that source
directly through ``srcdoc`` makes the child inherit Omlorix's application CSP,
while adding ``allow-same-origin`` would expose the authenticated application
origin to the authored code.  The proxy below is ordinary trusted HTTP content.
It receives source from its same-origin parent and mounts it in a nested iframe
whose sandbox deliberately omits ``allow-same-origin``.
"""

from __future__ import annotations


CANVAS_HTML_PREVIEW_PROXY_CSP = "; ".join(
    [
        "default-src 'none'",
        "object-src 'none'",
        # The proxy bootstrap is inline.  Broader sources are inherited by the
        # nested srcdoc document and then narrowed by its host-injected CSP.
        "script-src 'unsafe-inline' 'unsafe-eval' blob: data: http: https:",
        "style-src 'unsafe-inline' data: http: https:",
        "img-src data: blob: http: https:",
        "font-src data: blob: http: https:",
        "media-src data: blob: http: https:",
        "connect-src http: https: ws: wss:",
        "frame-src 'self' data: blob: http: https:",
        "child-src 'self' data: blob: http: https:",
        "worker-src data: blob: http: https:",
        "frame-ancestors 'self'",
        "base-uri 'none'",
        # The nested document narrows this to ``none`` until the viewer grants
        # external content.  The proxy itself contains no form.
        "form-action http: https:",
    ]
)


# This document contains trusted Omlorix bootstrap code only.  Authored HTML is
# transferred after load and is never inserted into the proxy's own DOM; it is
# mounted in the nested opaque-origin iframe created by the bootstrap.
CANVAS_HTML_PREVIEW_PROXY_DOCUMENT = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Canvas HTML preview</title>
  <style>
    html,body,#canvas-preview-root{width:100%;height:100%;margin:0;overflow:hidden;background:#fff}
    *{box-sizing:border-box}
    iframe{display:block;width:100%;height:100%;border:0;background:#fff}
  </style>
</head>
<body>
  <div id="canvas-preview-root"></div>
  <script>
  (() => {
    'use strict';

    const READY = 'omlorix-canvas-html-preview-ready';
    const READY_REQUEST = 'omlorix-canvas-html-preview-ready-request';
    const RENDER = 'omlorix-canvas-html-preview-render';
    const LOADED = 'omlorix-canvas-html-preview-loaded';
    const STORAGE = 'omlorix-canvas-html-preview-storage';
    const VISUALIZATION_TO_HOST = new Set([
      'omlorix-code-block-preview-height',
      'omlorix:visualization-request',
    ]);
    const VISUALIZATION_TO_VIEW = new Set([
      'omlorix:visualization-response',
      'omlorix:visualization-theme',
    ]);
    const root = document.getElementById('canvas-preview-root');
    const persistentStorage = new Map();
    const sessionStorage = new Map();
    // Credentialed hydration reads whole files before converting them to data
    // URLs. Keep simultaneous reads low enough that a document cannot create
    // an unbounded burst of authenticated downloads.
    const AUTHENTICATED_FILE_HYDRATION_CONCURRENCY = 4;
    let view = null;
    let renderGeneration = 0;
    let relayVisualizationMessages = false;

    function permissionCsp(allowScripts, allowExternalContent, allowEval) {
      const host = location.origin;
      const remote = allowExternalContent ? ' http: https:' : '';
      // Data and Blob URLs are local payloads, not remote connections. They
      // remain available even while network origins are blocked.
      const localConnections = 'blob: data:';
      const remoteConnections = 'http: https: ws: wss:';
      const evalSource = allowEval ? " 'unsafe-eval'" : '';
      const scripts = allowScripts
        ? `script-src 'unsafe-inline'${evalSource} blob: data: ${host}${remote}`
        : "script-src 'none'";
      return [
        "default-src 'none'",
        scripts,
        `style-src 'unsafe-inline' data: ${host}${remote}`,
        `img-src data: blob: ${host}${remote}`,
        `font-src data: blob: ${host}${remote}`,
        `media-src data: blob: ${host}${remote}`,
        `connect-src ${allowExternalContent ? `${localConnections} ${remoteConnections}` : localConnections}`,
        `frame-src ${allowExternalContent ? 'http: https:' : "'none'"}`,
        `child-src ${allowExternalContent ? 'http: https:' : "'none'"}`,
        `worker-src ${allowScripts ? `blob: data:${remote}` : "'none'"}`,
        "object-src 'none'",
        "base-uri 'none'",
        `form-action ${allowExternalContent ? 'http: https:' : "'none'"}`,
      ].join('; ');
    }

    function storageShimSource() {
      const localSeed = JSON.stringify(Object.fromEntries(persistentStorage));
      const sessionSeed = JSON.stringify(Object.fromEntries(sessionStorage));
      return `(() => {
        const makeStorage = (scope, seed) => {
          const values = new Map(Object.entries(seed || {}));
          const notify = (operation, key, value) => parent.postMessage({
            type: '${STORAGE}', scope, operation, key, value
          }, '*');
          return {
            get length() { return values.size; },
            clear() { values.clear(); notify('clear', null, null); },
            getItem(key) { key = String(key); return values.has(key) ? values.get(key) : null; },
            key(index) { return Array.from(values.keys())[Number(index)] ?? null; },
            removeItem(key) { key = String(key); values.delete(key); notify('remove', key, null); },
            setItem(key, value) { key = String(key); value = String(value); values.set(key, value); notify('set', key, value); }
          };
        };
        try { Object.defineProperty(window, 'localStorage', { configurable: true, value: makeStorage('local', ${localSeed}) }); } catch (_) {}
        try { Object.defineProperty(window, 'sessionStorage', { configurable: true, value: makeStorage('session', ${sessionSeed}) }); } catch (_) {}
      })();`;
    }

    function blobToDataUrl(blob) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener('load', () => resolve(String(reader.result || '')), { once: true });
        reader.addEventListener('error', () => reject(reader.error || new Error('File read failed')), { once: true });
        reader.readAsDataURL(blob);
      });
    }

    async function hydrateAuthenticatedCanvasFiles(serializedDocument, allowAuthenticatedFileHydration) {
      // A sandboxed opaque document has an empty site-for-cookies, so even a
      // same-origin image request cannot use Omlorix's SameSite auth cookie.
      // The trusted outer frame resolves only the file-download URLs produced
      // from omlorix-file:// references and replaces them with inert data URLs.
      const source = String(serializedDocument || '');
      // Public share pages render attacker-controlled source for arbitrary
      // viewers. They must never use the viewer's session to resolve file URLs
      // embedded in that source.
      if (!allowAuthenticatedFileHydration) return source;
      const pattern = /(?:https?:\/\/[^\s"'()<>]+)?\/api\/v1\/files\/download\?[^\s"'()<>]+/gi;
      const serializedUrls = Array.from(new Set(source.match(pattern) || []))
        // Replace strict URL prefixes only after their longer variants so a
        // query string on one resource cannot corrupt another replacement.
        .sort((left, right) => right.length - left.length);
      const replacements = new Map();

      async function hydrateOne(serializedUrl) {
        const decodedUrl = serializedUrl.replace(/&amp;/gi, '&');
        let parsed;
        try {
          parsed = new URL(decodedUrl, location.origin);
        } catch (_) {
          return;
        }
        if (
          parsed.origin !== location.origin
          || parsed.pathname !== '/api/v1/files/download'
          || !parsed.searchParams.get('file_id')
        ) return;

        try {
          const response = await fetch(parsed.href, {
            cache: 'no-store',
            credentials: 'include',
          });
          if (!response.ok) return;
          const dataUrl = await blobToDataUrl(await response.blob());
          if (dataUrl) replacements.set(serializedUrl, dataUrl);
        } catch (_) {
          // Leave an unavailable reference unchanged. The nested frame cannot
          // attach Omlorix credentials, so it still fails closed.
        }
      }

      let nextIndex = 0;
      async function hydrateWorker() {
        while (nextIndex < serializedUrls.length) {
          const serializedUrl = serializedUrls[nextIndex];
          nextIndex += 1;
          await hydrateOne(serializedUrl);
        }
      }
      await Promise.all(Array.from(
        { length: Math.min(AUTHENTICATED_FILE_HYDRATION_CONCURRENCY, serializedUrls.length) },
        () => hydrateWorker(),
      ));

      let hydrated = source;
      serializedUrls.forEach((serializedUrl) => {
        const dataUrl = replacements.get(serializedUrl);
        if (dataUrl) hydrated = hydrated.split(serializedUrl).join(dataUrl);
      });
      return hydrated;
    }

    function prepareDocument(rawHtml, allowScripts, allowExternalContent, allowEval) {
      const parser = new DOMParser();
      const doc = parser.parseFromString(
        String(rawHtml || '') || '<!doctype html><html><head></head><body></body></html>',
        'text/html'
      );

      // Authored policies, redirects, and base URLs must not override the host
      // boundary or turn relative navigation into a parent-page navigation.
      doc.querySelectorAll('meta[http-equiv]').forEach((meta) => {
        const value = String(meta.getAttribute('http-equiv') || '').trim().toLowerCase();
        if (value === 'content-security-policy' || value === 'refresh') meta.remove();
      });
      doc.querySelectorAll('base').forEach((base) => base.remove());
      doc.querySelectorAll('iframe[srcdoc], frame[srcdoc]').forEach((frame) => frame.removeAttribute('srcdoc'));

      const head = doc.head || doc.documentElement.insertBefore(doc.createElement('head'), doc.body || null);
      const charset = doc.createElement('meta');
      charset.setAttribute('charset', 'utf-8');
      const viewport = doc.createElement('meta');
      viewport.setAttribute('name', 'viewport');
      viewport.setAttribute('content', 'width=device-width,initial-scale=1');
      const csp = doc.createElement('meta');
      csp.setAttribute('http-equiv', 'Content-Security-Policy');
      csp.setAttribute('content', permissionCsp(allowScripts, allowExternalContent, allowEval));
      head.prepend(csp);
      head.prepend(viewport);
      head.prepend(charset);

      if (allowScripts) {
        // Opaque sandbox origins intentionally have no native Web Storage.
        // A small per-preview compatibility layer keeps common interactive
        // artifacts working without exposing Omlorix's real storage.
        const shim = doc.createElement('script');
        shim.textContent = storageShimSource();
        head.insertBefore(shim, head.children[3] || null);
      } else {
        doc.querySelectorAll('script').forEach((script) => script.remove());
        doc.querySelectorAll('*').forEach((node) => {
          Array.from(node.attributes || []).forEach((attribute) => {
            if (String(attribute.name || '').toLowerCase().startsWith('on')) {
              node.removeAttribute(attribute.name);
            }
          });
        });
      }

      doc.querySelectorAll('a[target="_blank"]').forEach((anchor) => {
        anchor.setAttribute('rel', 'noopener noreferrer');
        anchor.setAttribute('referrerpolicy', 'no-referrer');
      });

      const doctype = doc.doctype ? `<!doctype ${doc.doctype.name}>` : '<!doctype html>';
      return `${doctype}\n${doc.documentElement.outerHTML}`;
    }

    function updateStoredValue(message) {
      const target = message.scope === 'session' ? sessionStorage : persistentStorage;
      if (message.operation === 'clear') target.clear();
      else if (message.operation === 'remove') target.delete(String(message.key));
      else if (message.operation === 'set') target.set(String(message.key), String(message.value));
    }

    async function render(message) {
      renderGeneration += 1;
      const generation = renderGeneration;
      const allowExternalContent = message.allowExternalContent === true;
      const allowAuthenticatedFileHydration = message.allowAuthenticatedFileHydration === true;
      relayVisualizationMessages = message.relayVisualizationMessages === true;
      // Browsers allow a sandboxed iframe to navigate itself, and no shipped
      // CSP directive blocks that navigation. Therefore arbitrary authored
      // scripts may run only after the viewer grants external content.
      //
      // Static visualizations are the only exception: their caller removes
      // every authored script and requests this mode together with no eval,
      // no authenticated hydration, and the narrow visualization bridge.
      const trustedLocalScripts = message.trustedLocalScripts === true
        && message.allowEval === false
        && relayVisualizationMessages
        && !allowAuthenticatedFileHydration
        && !allowExternalContent;
      const allowScripts = message.allowScripts === true && (allowExternalContent || trustedLocalScripts);
      const allowEval = allowScripts && !trustedLocalScripts && message.allowEval !== false;
      const preparedDocument = prepareDocument(
        message.html,
        allowScripts,
        allowExternalContent,
        allowEval,
      );
      const hydratedDocument = await hydrateAuthenticatedCanvasFiles(
        preparedDocument,
        allowAuthenticatedFileHydration,
      );
      if (generation !== renderGeneration) return;

      const nextView = document.createElement('iframe');
      const sandbox = [];
      if (allowScripts) sandbox.push('allow-scripts', 'allow-modals');
      // Form submission and popups are network/navigation capabilities, not
      // prerequisites for local controls. Keep them behind the same explicit
      // external-content grant as remote resources and connections.
      if (allowScripts && allowExternalContent) sandbox.push('allow-forms', 'allow-popups', 'allow-downloads');
      nextView.setAttribute('sandbox', sandbox.join(' '));
      nextView.setAttribute('referrerpolicy', 'no-referrer');
      nextView.setAttribute('title', String(message.title || 'Canvas HTML preview'));
      nextView.addEventListener('load', () => {
        parent.postMessage({ type: LOADED, previewId: String(message.previewId || '') }, location.origin);
      }, { once: true });
      nextView.srcdoc = hydratedDocument;
      root.replaceChildren(nextView);
      view = nextView;
    }

    window.addEventListener('message', (event) => {
      if (event.source === view?.contentWindow) {
        if (event.data?.type === STORAGE) {
          updateStoredValue(event.data);
          return;
        }
        if (relayVisualizationMessages && VISUALIZATION_TO_HOST.has(event.data?.type)) {
          parent.postMessage(event.data, location.origin);
        }
        return;
      }
      if (event.source !== parent || event.origin !== location.origin) return;
      if (relayVisualizationMessages && VISUALIZATION_TO_VIEW.has(event.data?.type)) {
        view?.contentWindow?.postMessage(event.data, '*');
        return;
      }
      if (event.data?.type === READY_REQUEST) {
        parent.postMessage({ type: READY }, location.origin);
        return;
      }
      if (event.data?.type !== RENDER) return;
      void render(event.data);
    });

    // Send once during parsing and once after the outer iframe load event.  A
    // detached host iframe may not expose its contentWindow early enough for
    // the parent's first ready message lookup.
    parent.postMessage({ type: READY }, location.origin);
    window.addEventListener('load', () => {
      parent.postMessage({ type: READY }, location.origin);
    }, { once: true });
  })();
  </script>
</body>
</html>
"""


def get_canvas_html_preview_proxy_payload() -> dict[str, object]:
    """Return the trusted Canvas preview proxy and its non-cacheable headers."""

    return {
        "html": CANVAS_HTML_PREVIEW_PROXY_DOCUMENT,
        "headers": {
            "Content-Security-Policy": CANVAS_HTML_PREVIEW_PROXY_CSP,
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "Expires": "0",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    }
