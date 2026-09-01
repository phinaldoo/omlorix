function isMermaidLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'mermaid' || normalized === 'mmd';
}

function isVegaLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'vega' || normalized === 'vg';
}

function isVegaLiteLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'vega-lite' || normalized === 'vegalite' || normalized === 'vl';
}

function isHtmlPreviewLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'html' || normalized === 'htm';
}

/**
 * Return whether a URL-like value would require the preview to contact a
 * remote origin. Data, blob, fragment, and about:blank references are kept in
 * the isolated preview and therefore do not need the external-content grant.
 *
 * This helper only drives the visibility of permission controls. The iframe's
 * Content Security Policy remains the security boundary, so a missed or
 * malformed reference stays blocked.
 */
function isHtmlPreviewExternalReference(value) {
    const normalized = String(value || '').trim().replace(/^['"]|['"]$/g, '');
    if (!normalized) {
        return false;
    }
    return !/^(?:data:|blob:|about:blank(?:[#?]|$)|#)/i.test(normalized);
}

/**
 * Inspect HTML source for capabilities that are intentionally disabled in the
 * default preview. The inspection is conservative and never grants a
 * capability by itself; it only decides whether the corresponding control is
 * useful to show.
 */
function analyzeHtmlPreviewCapabilities(source) {
    const html = String(source || '');
    // Comments are not rendered markup and should not surface permission
    // controls merely because they contain an example tag or URL.
    const markup = html.replace(/<!--[\s\S]*?-->/g, '');

    // Script elements, inline event handlers, javascript: URLs, and srcdoc
    // frames can all execute authored code once iframe scripts are enabled.
    let scripts = /<[a-z][^>]*\son[a-z][\w:.-]*\s*=/i.test(markup)
        || /<[a-z][^>]*\b(?:href|src|action|formaction|xlink:href)\s*=\s*(?:["']\s*javascript:|javascript:)/i.test(markup)
        || /<iframe\b[^>]*\bsrcdoc\s*=/i.test(markup);
    const scriptTagPattern = /<script\b([^>]*)>/gi;
    let scriptMatch;
    while (!scripts && (scriptMatch = scriptTagPattern.exec(markup)) !== null) {
        const attributes = scriptMatch[1] || '';
        const typeMatch = attributes.match(/\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
        const type = String(typeMatch?.[1] || typeMatch?.[2] || typeMatch?.[3] || '').trim().toLowerCase();
        // Common JSON/template script blocks are inert data containers. All
        // unknown types remain conservative and surface the scripts control.
        scripts = !/^(?:application\/(?:ld\+)?json|text\/(?:template|x-handlebars-template))$/.test(type);
    }

    let externalContent = false;
    const resourceTagPattern = /<(script|link|img|source|video|audio|track|iframe|input|image|use)\b([^>]*)>/gi;
    let tagMatch;
    while (!externalContent && (tagMatch = resourceTagPattern.exec(markup)) !== null) {
        const tagName = tagMatch[1].toLowerCase();
        const attributes = tagMatch[2] || '';

        // Only resource-loading link elements need a network grant. Ordinary
        // anchors are deliberately not part of this scan.
        if (tagName === 'link') {
            const relMatch = attributes.match(/\brel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
            const rel = String(relMatch?.[1] || relMatch?.[2] || relMatch?.[3] || '').toLowerCase();
            if (!/(?:^|\s)(?:stylesheet|icon|preload|modulepreload|prefetch|dns-prefetch)(?:\s|$)/.test(rel)) {
                continue;
            }
        }

        const referencePattern = /\b(src|srcset|href|poster)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi;
        let referenceMatch;
        while ((referenceMatch = referencePattern.exec(attributes)) !== null) {
            const attributeName = String(referenceMatch[1] || '').toLowerCase();
            const rawValue = referenceMatch[2] || referenceMatch[3] || referenceMatch[4] || '';
            // srcset can contain several comma-separated candidates, each
            // optionally followed by a density or width descriptor.
            const references = attributeName === 'srcset'
                ? rawValue.split(',').map((candidate) => candidate.trim().split(/\s+/)[0])
                : [rawValue];
            if (references.some(isHtmlPreviewExternalReference)) {
                externalContent = true;
                break;
            }
        }
    }

    if (!externalContent) {
        // Inline CSS can load stylesheets, fonts, and media without using an
        // HTML resource attribute.
        const cssSources = [];
        const styleBlockPattern = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
        let styleBlockMatch;
        while ((styleBlockMatch = styleBlockPattern.exec(markup)) !== null) {
            cssSources.push(styleBlockMatch[1] || '');
        }
        const htmlTagPattern = /<[a-z][^>]*>/gi;
        let htmlTagMatch;
        while ((htmlTagMatch = htmlTagPattern.exec(markup)) !== null) {
            const styleMatch = htmlTagMatch[0].match(/\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
            if (styleMatch) {
                cssSources.push(styleMatch[1] || styleMatch[2] || styleMatch[3] || '');
            }
        }
        const cssReferencePattern = /(?:url\(\s*([^)]*?)\s*\)|@import\s+(?:url\(\s*)?(["'][^"']+["']|[^\s;\)]+))/gi;
        for (const cssSource of cssSources) {
            cssReferencePattern.lastIndex = 0;
            let cssMatch;
            while ((cssMatch = cssReferencePattern.exec(cssSource)) !== null) {
                if (isHtmlPreviewExternalReference(cssMatch[1] || cssMatch[2])) {
                    externalContent = true;
                    break;
                }
            }
            if (externalContent) {
                break;
            }
        }
    }

    // Arbitrary authored JavaScript can navigate its own sandboxed frame even
    // when CSP blocks fetches, forms, frames, and resource attributes. Treat
    // every executable document as external-content capable; URL discovery is
    // only a UI aid and must never decide whether scripts are safe to run.
    return { scripts, externalContent: scripts || externalContent };
}

function isMarkdownPreviewLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'markdown' || normalized === 'md';
}

function isStructuredDataPreviewLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'json' || normalized === 'yaml' || normalized === 'yml';
}

function isDelimitedPreviewLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized === 'csv' || normalized === 'tsv';
}

function parseVegaPreviewSpec(source) {
    const raw = String(source || '').trim();
    if (!raw) {
        throw new Error(getChatPreviewTranslation('code_block_vega_no_spec', 'No Vega specification.'));
    }
    if (raw.length > VEGA_PREVIEW_MAX_SPEC_LENGTH) {
        throw new Error(getChatPreviewTranslation('code_block_vega_too_large', 'This visualization is too large to render inline.'));
    }

    let parsed;
    try {
        parsed = JSON.parse(raw);
    } catch (_) {
        throw new Error(getChatPreviewTranslation('code_block_vega_requires_json', 'Vega and Vega-Lite previews require valid JSON.'));
    }

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(getChatPreviewTranslation('code_block_vega_requires_object', 'Vega and Vega-Lite previews require a JSON object specification.'));
    }

    return parsed;
}

function inferVegaPreviewKindFromSpec(spec) {
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
        return '';
    }

    const schema = String(spec.$schema || '').trim().toLowerCase();
    if (schema.includes('vega-lite')) {
        return 'vega-lite';
    }
    if (schema.includes('/vega/')) {
        return 'vega';
    }
    if (spec.mark && spec.encoding) {
        return 'vega-lite';
    }
    if (Array.isArray(spec.marks) && (Array.isArray(spec.scales) || Array.isArray(spec.axes) || Array.isArray(spec.signals))) {
        return 'vega';
    }
    return '';
}

function getVegaPreviewKind(language, source = '') {
    if (isVegaLiteLanguage(language)) {
        return 'vega-lite';
    }
    if (isVegaLanguage(language)) {
        return 'vega';
    }

    const normalized = String(language || '').trim().toLowerCase();
    const mayContainJsonSpec = !normalized
        || normalized === 'json'
        || normalized === 'javascript'
        || normalized === 'js'
        || normalized === 'typescript'
        || normalized === 'ts'
        || normalized === 'plaintext'
        || normalized === 'text';

    if (!mayContainJsonSpec) {
        return '';
    }

    try {
        return inferVegaPreviewKindFromSpec(parseVegaPreviewSpec(source));
    } catch (_) {
        return '';
    }
}

function getCodePreviewKind(language, source = '') {
    const vegaPreviewKind = getVegaPreviewKind(language, source);
    if (vegaPreviewKind) {
        return vegaPreviewKind;
    }
    if (isMermaidLanguage(language)) {
        return 'mermaid';
    }
    if (isHtmlPreviewLanguage(language)) {
        return 'html';
    }
    if (isMarkdownPreviewLanguage(language)) {
        return 'markdown';
    }
    if (isStructuredDataPreviewLanguage(language)) {
        return String(language || '').trim().toLowerCase() === 'json' ? 'json' : 'yaml';
    }
    if (isDelimitedPreviewLanguage(language)) {
        return String(language || '').trim().toLowerCase() === 'tsv' ? 'tsv' : 'csv';
    }
    if (String(language || '').trim().toLowerCase() === 'svg' || /<svg[\s>]/i.test(String(source || ''))) {
        return 'svg';
    }
    return '';
}

function getCodeBlockSyntaxLanguage(primaryLanguage, normalizedLanguage, previewKind) {
    if (previewKind === 'mermaid') {
        return 'none';
    }
    if (previewKind === 'vega' || previewKind === 'vega-lite') {
        return 'json';
    }
    return normalizedLanguage || normalizeHighlightLanguage(primaryLanguage);
}

function getCodePreviewLabel(previewKind) {
    const normalized = String(previewKind || '').trim().toLowerCase();
    if (normalized === 'vega-lite') {
        return 'Vega-Lite';
    }
    if (normalized === 'vega') {
        return 'Vega';
    }
    if (normalized === 'mermaid') {
        return 'Mermaid';
    }
    return 'Code';
}

function getMermaidTheme() {
    const mode = String(document?.documentElement?.dataset?.mode || '').toLowerCase();
    return mode === 'dark' ? 'dark' : 'default';
}

function isElementReadyForPreviewRender(element) {
    if (!(element instanceof Element) || !element.isConnected) {
        return false;
    }
    let current = element;
    while (current instanceof Element) {
        if (current.hidden) {
            return false;
        }
        if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
            const style = window.getComputedStyle(current);
            if (style.display === 'none' || style.visibility === 'hidden') {
                return false;
            }
        }
        current = current.parentElement;
    }
    return true;
}

function waitForPreviewRenderReady(element, maxFrames = 12) {
    return new Promise((resolve) => {
        const raf = typeof requestAnimationFrame === 'function'
            ? requestAnimationFrame
            : (callback) => setTimeout(callback, 16);
        let attempts = 0;
        const check = () => {
            if (isElementReadyForPreviewRender(element)) {
                resolve(true);
                return;
            }
            attempts += 1;
            if (attempts >= maxFrames) {
                resolve(isElementReadyForPreviewRender(element));
                return;
            }
            raf(check);
        };
        check();
    });
}

