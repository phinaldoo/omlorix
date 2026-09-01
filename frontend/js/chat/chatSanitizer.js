(function () {
    'use strict';

    const root = typeof window !== 'undefined' ? window : globalThis;

    const SAFE_RENDERED_DATA_ATTRS = [
        'data-code-block-id',
        'data-code-id',
        'data-content-id',
        'data-lang',
        'data-language',
        'data-language-display',
        'data-mermaid-action',
        'data-preview-action',
        'data-preview-kind',
        'data-preview-state',
        'data-view',
        // HTML preview permission switches are rendered by Markdown and use
        // this stable value to tell the delegated change handler which grant
        // to update. Keep this explicit allowlist entry narrow instead of
        // enabling arbitrary data attributes for user-authored Markdown.
        'data-html-preview-permission',
    ];
    const POLICY_NOTICE_ALLOWED_TAGS = ['a', 'b', 'br', 'code', 'em', 'i', 'li', 'ol', 'p', 'strong', 'u', 'ul'];
    const POLICY_NOTICE_ALLOWED_ATTR = ['href', 'rel', 'target', 'title'];

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function isSafeUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) {
            return false;
        }
        if (raw.startsWith('#') || raw.startsWith('/') || raw.startsWith('./') || raw.startsWith('../')) {
            return true;
        }
        const schemeMatch = raw.match(/^([a-zA-Z][a-zA-Z\d+\-.]*):/);
        if (!schemeMatch) {
            return true;
        }
        const protocol = schemeMatch[1].toLowerCase();
        return protocol === 'http' || protocol === 'https' || protocol === 'mailto' || protocol === 'tel';
    }

    function withDomPurify(callback, fallbackValue) {
        const purify = root.DOMPurify;
        if (!purify || typeof purify.sanitize !== 'function') {
            return fallbackValue;
        }
        return callback(purify);
    }

    function sanitizeHtml(html, options = {}) {
        const source = String(html || '');
        if (!source) {
            return '';
        }

        const allowDataAttrs = options && options.allowDataAttrs === true;

        const fallback = escapeHtml(source);
        const sanitized = withDomPurify((purify) => purify.sanitize(source, {
            USE_PROFILES: { html: true, svg: true },
            FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'frame', 'frameset', 'meta', 'base', 'link'],
            FORBID_ATTR: ['style', 'srcdoc'],
            ALLOW_DATA_ATTR: allowDataAttrs,
            ADD_ATTR: SAFE_RENDERED_DATA_ATTRS,
        }), fallback);

        if (typeof DOMParser === 'undefined') {
            return sanitized;
        }

        // Enforce safe URLs and safe external link behavior after sanitization.
        const parser = new DOMParser();
        const doc = parser.parseFromString(sanitized, 'text/html');
        const candidates = doc.querySelectorAll('a[href], img[src], video[src], audio[src], source[src], iframe[src], use[xlink\\:href], [xlink\\:href]');
        candidates.forEach((node) => {
            if (node.hasAttribute('href')) {
                const href = node.getAttribute('href') || '';
                if (!isSafeUrl(href)) {
                    node.removeAttribute('href');
                }
            }
            if (node.hasAttribute('src')) {
                const src = node.getAttribute('src') || '';
                if (!isSafeUrl(src)) {
                    node.removeAttribute('src');
                }
            }
            if (node.hasAttribute('xlink:href')) {
                const href = node.getAttribute('xlink:href') || '';
                if (!isSafeUrl(href)) {
                    node.removeAttribute('xlink:href');
                }
            }
            if (node.tagName && node.tagName.toLowerCase() === 'a' && node.hasAttribute('href')) {
                node.setAttribute('target', '_blank');
                node.setAttribute('rel', 'noopener noreferrer nofollow');
            }
        });

        return doc.body.innerHTML;
    }

    function sanitizeSvg(svg) {
        const source = String(svg || '');
        if (!source) {
            return '';
        }
        return withDomPurify((purify) => purify.sanitize(source, {
            USE_PROFILES: { svg: true },
            FORBID_ATTR: ['style', 'srcdoc'],
            ALLOW_DATA_ATTR: false,
        }), '');
    }

    function setInnerHtml(target, html, options = {}) {
        if (!target) {
            return '';
        }

        const sanitized = sanitizeHtml(html, options);
        target.innerHTML = sanitized;
        return sanitized;
    }

    function setSvg(target, svg) {
        if (!target) {
            return '';
        }

        const sanitized = sanitizeSvg(svg);
        target.innerHTML = sanitized;
        return sanitized;
    }

    function sanitizePolicyNoticeHtml(html) {
        const source = String(html || '');
        if (!source) {
            return '';
        }

        const fallback = escapeHtml(source);
        const sanitized = withDomPurify((purify) => purify.sanitize(source, {
            ALLOWED_TAGS: POLICY_NOTICE_ALLOWED_TAGS,
            ALLOWED_ATTR: POLICY_NOTICE_ALLOWED_ATTR,
            FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'frame', 'frameset', 'meta', 'base', 'link'],
            FORBID_ATTR: ['style', 'src', 'srcdoc'],
            ALLOW_DATA_ATTR: false,
        }), fallback);

        if (typeof DOMParser === 'undefined') {
            return sanitized;
        }

        const parser = new DOMParser();
        const doc = parser.parseFromString(sanitized, 'text/html');
        doc.querySelectorAll('*').forEach((node) => {
            for (const attr of Array.from(node.attributes || [])) {
                if (attr.name.toLowerCase().startsWith('on')) {
                    node.removeAttribute(attr.name);
                }
            }
        });
        doc.querySelectorAll('a[href]').forEach((node) => {
            const href = node.getAttribute('href') || '';
            if (!isSafeUrl(href)) {
                node.removeAttribute('href');
                return;
            }
            node.setAttribute('target', '_blank');
            node.setAttribute('rel', 'noopener noreferrer nofollow');
        });

        return doc.body.innerHTML;
    }

    const api = {
        isSafeUrl,
        sanitizeHtml,
        sanitizePolicyNoticeHtml,
        sanitizeSvg,
        setInnerHtml,
        setSvg,
    };

    root.ChatSanitizer = api;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})();
