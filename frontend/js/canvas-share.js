(function () {
    'use strict';

    let mermaidRenderCounter = 0;

    const t = function translate(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const app = document.body;
    const loadingView = document.getElementById('loadingView');
    const passwordView = document.getElementById('passwordView');
    const errorView = document.getElementById('errorView');
    const canvasContainer = document.getElementById('canvasContainer');
    const canvasContent = document.getElementById('canvasContent');
    const passwordForm = document.getElementById('passwordForm');
    const passwordInput = document.getElementById('passwordInput');
    const passwordHelpText = document.getElementById('passwordHelpText');
    const passwordError = document.getElementById('passwordError');
    const errorMessage = document.getElementById('errorMessage');
    const retryBtn = document.getElementById('retryBtn');
    let currentCanvasPayload = null;
    let currentPdfObjectUrl = '';
    let sharedHtmlPreviewState = null;
    let sharedAssetObjectUrls = new Map();
    const SHARED_ASSET_PLACEHOLDER_PREFIX = '/__omlorix_shared_canvas_asset__/';

    const CANVAS_ARTIFACT_TYPE_ALIASES = {
        markdown: 'markdown',
        md: 'markdown',
        html: 'html',
        htm: 'html',
        css: 'css',
        mermaid: 'mermaid',
        mmd: 'mermaid',
        pdf: 'pdf',
        'text/markdown': 'markdown',
        'text/x-markdown': 'markdown',
        'text/html': 'html',
        'text/css': 'css',
        'text/x-mermaid': 'mermaid',
        'application/pdf': 'pdf',
    };

    const formatT = function formatTranslation(key, fallback, vars) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        const template = t(key, fallback);
        if (!vars || typeof vars !== 'object') {
            return template;
        }
        return String(template).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    function translateBackendDetail(detail, fallback) {
        if (typeof window.translateBackendDetail === 'function') {
            return window.translateBackendDetail(detail, fallback);
        }
        return detail || fallback;
    }

    function updateDocumentTitle(payload) {
        const fileName = String(payload?.file_name || t('canvas_share_page_title', 'Shared Canvas'));
        document.title = formatT('canvas_share_document_title', '{name} · Shared Canvas', { name: fileName });
    }

    function normalizeCanvasArtifactType(value) {
        const normalized = String(value || '').trim().toLowerCase();
        return CANVAS_ARTIFACT_TYPE_ALIASES[normalized] || '';
    }

    function inferCanvasArtifactTypeFromFileName(fileName) {
        const name = String(fileName || '').trim().toLowerCase();
        if (name.endsWith('.html') || name.endsWith('.htm')) return 'html';
        if (name.endsWith('.css')) return 'css';
        if (name.endsWith('.mmd') || name.endsWith('.mermaid')) return 'mermaid';
        if (name.endsWith('.pdf')) return 'pdf';
        if (name.endsWith('.md') || name.endsWith('.markdown')) return 'markdown';
        return '';
    }

    function getCanvasArtifactType(payload) {
        const artifactType = normalizeCanvasArtifactType(payload?.artifact_type);
        if (artifactType) {
            return artifactType;
        }

        return normalizeCanvasArtifactType(payload?.mime_type)
            || inferCanvasArtifactTypeFromFileName(payload?.file_name);
    }

    function base64ToBlob(base64Value, mimeType) {
        const binary = atob(String(base64Value || ''));
        const chunks = [];
        for (let offset = 0; offset < binary.length; offset += 8192) {
            const slice = binary.slice(offset, offset + 8192);
            const bytes = new Uint8Array(slice.length);
            for (let index = 0; index < slice.length; index += 1) {
                bytes[index] = slice.charCodeAt(index);
            }
            chunks.push(bytes);
        }
        return new Blob(chunks, { type: mimeType || 'application/octet-stream' });
    }

    function resetSharedAssetObjectUrls(payload) {
        sharedAssetObjectUrls.forEach((url) => URL.revokeObjectURL(url));
        sharedAssetObjectUrls = new Map();
        (Array.isArray(payload?.assets) ? payload.assets : []).forEach((asset) => {
            const fileId = String(asset?.file_id || '').trim();
            if (!fileId || asset?.encoding !== 'base64') return;
            const blob = base64ToBlob(asset.content, asset.mime_type);
            sharedAssetObjectUrls.set(fileId, URL.createObjectURL(blob));
        });
    }

    function replaceSharedAssetReferences(content, { placeholders = false } = {}) {
        const replacement = (fileId) => {
            const normalizedId = String(fileId || '').trim();
            if (!sharedAssetObjectUrls.has(normalizedId)) return '';
            return placeholders
                ? `${SHARED_ASSET_PLACEHOLDER_PREFIX}${encodeURIComponent(normalizedId)}`
                : sharedAssetObjectUrls.get(normalizedId);
        };
        return String(content || '')
            .replace(/omlorix-file:\/\/([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})(?:[?#][^\s"'<>)]*)?/gi, (_match, fileId) => replacement(fileId))
            .replace(/(?:https?:\/\/[^\s"'()<>]+)?\/api\/v1\/files\/download\?[^\s"'()<>]+/gi, (rawUrl) => {
                try {
                    const parsed = new URL(rawUrl, window.location.origin);
                    return replacement(parsed.searchParams.get('file_id'));
                } catch (_) {
                    return '';
                }
            });
    }

    function hydrateSharedAssetPlaceholders(root) {
        root?.querySelectorAll?.('[src], [href], [poster]').forEach((node) => {
            ['src', 'href', 'poster'].forEach((attribute) => {
                const value = String(node.getAttribute(attribute) || '');
                if (!value.startsWith(SHARED_ASSET_PLACEHOLDER_PREFIX)) return;
                const fileId = decodeURIComponent(value.slice(SHARED_ASSET_PLACEHOLDER_PREFIX.length));
                const objectUrl = sharedAssetObjectUrls.get(fileId);
                if (objectUrl) node.setAttribute(attribute, objectUrl);
                else node.removeAttribute(attribute);
            });
        });
    }

    function showView(view) {
        [loadingView, passwordView, errorView, canvasContainer].forEach((node) => {
            if (!node) return;
            node.hidden = node !== view;
        });
        const viewName = view === canvasContainer
            ? 'canvas'
            : view === passwordView
                ? 'password'
                : view === errorView
                    ? 'error'
                    : 'loading';
        document.body.dataset.view = viewName;
    }

    function setPasswordError(message = '') {
        if (passwordError) {
            passwordError.textContent = message;
        }
        if (passwordInput) {
            passwordInput.setAttribute('aria-invalid', message ? 'true' : 'false');
        }
    }

    function extractShareId() {
        const pathname = String(window.location.pathname || '');
        const parts = pathname.split('/').filter(Boolean);
        const anchor = parts.findIndex((part) => part === 'canvas');
        if (anchor >= 0 && parts[anchor + 1] === 'shared' && parts[anchor + 2]) {
            return decodeURIComponent(parts[anchor + 2]);
        }
        const fromQuery = new URLSearchParams(window.location.search).get('share_id');
        return fromQuery ? decodeURIComponent(fromQuery) : '';
    }

    async function fetchSharedArtifact(shareId, password) {
        const response = await fetch('/api/v1/files/canvas/shared/access', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                share_id: shareId,
                password: password || undefined,
            }),
        });

        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }

        if (response.status === 401) {
            const detail = translateBackendDetail(
                payload?.detail,
                t('canvas_share_password_required', 'Password required')
            );
            const error = new Error(detail);
            error.requiresPassword = true;
            throw error;
        }

        if (!response.ok) {
            const fallbackMessage = response.status === 404
                ? t('canvas_share_error_not_found', 'Shared canvas not found')
                : formatT('canvas_share_request_failed_status', 'Request failed ({status})', { status: response.status });
            throw new Error(translateBackendDetail(payload?.detail, fallbackMessage));
        }

        return payload;
    }

    const RESOURCE_TAGS = 'iframe, frame, object, embed, portal, link, audio, video, source, track';
    const URL_ATTRS = new Set(['src', 'href', 'xlink:href', 'formaction', 'action', 'poster', 'data']);
    const HTML_PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; media-src 'none'; connect-src 'none'; frame-src 'none'; child-src 'none'; object-src 'none'; font-src 'none'; manifest-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'; navigate-to 'none'";

    function isSameDocumentFragmentUrl(value) {
        const text = String(value || '').trim();
        return text && text.startsWith('#') && !text.startsWith('##');
    }

    function cssContainsExternalFetch(value) {
        const text = String(value || '').toLowerCase();
        return /@import\b|url\s*\(|image-set\s*\(|expression\s*\(/i.test(text);
    }

    function isSafeArtifactAnchorUrl(value) {
        const sanitizer = window.ChatSanitizer;
        if (sanitizer && typeof sanitizer.isSafeUrl === 'function') {
            return sanitizer.isSafeUrl(value);
        }
        return /^https?:\/\//i.test(String(value || '').trim()) || isSameDocumentFragmentUrl(value);
    }

    function setSanitizedHtml(target, html, options = {}) {
        if (!target) {
            return false;
        }

        const sanitizer = window.ChatSanitizer;
        if (sanitizer && typeof sanitizer.setInnerHtml === 'function') {
            sanitizer.setInnerHtml(target, html, options);
            return true;
        }
        if (sanitizer && typeof sanitizer.sanitizeHtml === 'function') {
            target.innerHTML = sanitizer.sanitizeHtml(html, options);
            return true;
        }
        return false;
    }

    function setSanitizedSvg(target, svg) {
        if (!target) {
            return false;
        }

        const sanitizer = window.ChatSanitizer;
        if (sanitizer && typeof sanitizer.setSvg === 'function') {
            sanitizer.setSvg(target, svg);
            return true;
        }
        if (sanitizer && typeof sanitizer.sanitizeSvg === 'function') {
            target.innerHTML = sanitizer.sanitizeSvg(svg);
            return true;
        }
        return false;
    }

    function escapeHtmlAttribute(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function serializeElementAttributes(node) {
        if (!node || !node.attributes || typeof node.attributes[Symbol.iterator] !== 'function') {
            return '';
        }
        return Array.from(node.attributes)
            .map((attr) => ` ${attr.name}="${escapeHtmlAttribute(attr.value)}"`)
            .join('');
    }

    function buildSandboxedPreviewDocument(doc) {
        const htmlAttrs = serializeElementAttributes(doc?.documentElement);
        const bodyAttrs = serializeElementAttributes(doc?.body);
        const headStyles = doc?.head
            ? Array.from(doc.head.querySelectorAll('style'))
                .map((node) => node.outerHTML)
                .join('')
            : '';
        const bodyContent = doc?.body ? doc.body.innerHTML : '';

        return [
            '<!DOCTYPE html>',
            `<html${htmlAttrs}>`,
            '<head>',
            '<meta charset="utf-8">',
            `<meta http-equiv="Content-Security-Policy" content="${HTML_PREVIEW_CSP}">`,
            headStyles,
            '</head>',
            `<body${bodyAttrs}>${bodyContent}</body>`,
            '</html>',
        ].join('\n');
    }

    function sanitizeRenderedArtifactNode(root) {
        if (!root || typeof root.querySelectorAll !== 'function') {
            return root;
        }

        root.querySelectorAll(RESOURCE_TAGS).forEach((node) => node.remove());
        root.querySelectorAll('style').forEach((node) => {
            if (cssContainsExternalFetch(node.textContent || '')) {
                node.remove();
            }
        });

        root.querySelectorAll('*').forEach((node) => {
            Array.from(node.attributes || []).forEach((attr) => {
                const attrName = String(attr.name || '').toLowerCase();
                const attrValue = String(attr.value || '').trim();
                if (attrName.startsWith('on')) {
                    node.removeAttribute(attr.name);
                    return;
                }
                if (attrName === 'style' && cssContainsExternalFetch(attrValue)) {
                    node.removeAttribute(attr.name);
                    return;
                }
                if (attrName === 'srcset') {
                    node.removeAttribute(attr.name);
                    return;
                }
                if (URL_ATTRS.has(attrName)) {
                    if (node.tagName === 'A' && attrName === 'href' && isSafeArtifactAnchorUrl(attrValue)) {
                        node.setAttribute('target', '_blank');
                        node.setAttribute('rel', 'noopener noreferrer');
                        node.setAttribute('referrerpolicy', 'no-referrer');
                        return;
                    }
                    if (!isSameDocumentFragmentUrl(attrValue)) {
                        node.removeAttribute(attr.name);
                    }
                }
            });
        });
        return root;
    }

    function sanitizeHtmlForSandboxedPreview(rawHtml) {
        const html = String(rawHtml || '');
        if (!html) return '';

        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            doc.querySelectorAll('script, meta, base').forEach((node) => node.remove());
            sanitizeRenderedArtifactNode(doc);

            return buildSandboxedPreviewDocument(doc);
        } catch (_) {
            return html
                .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
                .replace(/<meta\b[^>]*>/gi, '')
                .replace(/<base\b[^>]*>/gi, '');
        }
    }

    function renderHtmlArtifact(content) {
        content = replaceSharedAssetReferences(content);
        const runtime = window.OmlorixCanvasHtmlPreview;
        const capabilities = runtime && typeof runtime.analyze === 'function'
            ? runtime.analyze(content)
            : { scripts: /<script\b|\son[a-z]+\s*=/i.test(String(content || '')), externalContent: false };
        const shell = document.createElement('section');
        shell.className = 'canvas-share-html-shell';

        const toolbar = document.createElement('div');
        toolbar.className = 'canvas-share-html-toolbar';
        toolbar.setAttribute('role', 'toolbar');
        toolbar.setAttribute('aria-label', t('canvas_share_html_permissions_aria', 'HTML preview permissions'));

        const status = document.createElement('span');
        status.className = 'canvas-share-html-toolbar-status';
        status.id = 'canvas-share-html-permission-status';
        status.textContent = capabilities.externalContent
            ? t('canvas_share_html_external_blocked_notice', 'External connections are blocked until you allow them.')
            : t('canvas_share_html_interactive_notice', 'Interactive content runs in an isolated preview.');
        toolbar.appendChild(status);

        const scriptsButton = document.createElement('button');
        scriptsButton.className = 'canvas-share-html-permission-btn is-active';
        scriptsButton.type = 'button';
        scriptsButton.setAttribute('aria-describedby', status.id);
        scriptsButton.hidden = !capabilities.scripts;

        const externalButton = document.createElement('button');
        externalButton.className = 'canvas-share-html-permission-btn';
        externalButton.type = 'button';
        externalButton.setAttribute('aria-describedby', status.id);
        // Scripted pages can build remote URLs dynamically, so viewers must
        // always be able to grant network access when scripts are present.
        externalButton.hidden = !(capabilities.externalContent || capabilities.scripts);

        const frame = document.createElement('iframe');
        frame.className = 'canvas-share-html-frame';
        frame.setAttribute(
            'title',
            formatT('canvas_share_html_preview_title', 'Shared HTML preview: {name}', {
            name: currentCanvasPayload?.file_name || t('canvas_share_page_title', 'Shared Canvas'),
            })
        );

        sharedHtmlPreviewState = {
            allowScripts: false,
            allowExternalContent: false,
            capabilities,
            content: String(content || ''),
            frame,
        };

        function syncPermissionControls() {
            const scriptsLabel = sharedHtmlPreviewState.allowScripts
                ? t('canvas_share_html_disable_interactions', 'Disable interactions')
                : t('canvas_share_html_enable_interactions', 'Enable interactions');
            scriptsButton.textContent = scriptsLabel;
            scriptsButton.setAttribute('aria-label', scriptsLabel);
            scriptsButton.setAttribute('aria-pressed', sharedHtmlPreviewState.allowScripts ? 'true' : 'false');
            scriptsButton.disabled = !sharedHtmlPreviewState.allowExternalContent;
            scriptsButton.setAttribute('aria-disabled', scriptsButton.disabled ? 'true' : 'false');
            scriptsButton.classList.toggle('is-active', sharedHtmlPreviewState.allowScripts);
            scriptsButton.hidden = !capabilities.scripts && !sharedHtmlPreviewState.allowScripts;

            const externalLabel = sharedHtmlPreviewState.allowExternalContent
                ? t('canvas_share_html_block_external_content', 'Block external content')
                : t('canvas_share_html_allow_external_content', 'Allow external content');
            externalButton.textContent = externalLabel;
            externalButton.setAttribute('aria-label', externalLabel);
            externalButton.setAttribute('aria-pressed', sharedHtmlPreviewState.allowExternalContent ? 'true' : 'false');
            externalButton.classList.toggle('is-active', sharedHtmlPreviewState.allowExternalContent);
            externalButton.hidden = !capabilities.externalContent
                && !capabilities.scripts
                && !sharedHtmlPreviewState.allowExternalContent;
            status.textContent = sharedHtmlPreviewState.allowExternalContent
                ? t('canvas_share_html_external_allowed_notice', 'External connections are allowed for this preview.')
                : (capabilities.externalContent
                    ? t('canvas_share_html_external_blocked_notice', 'External connections are blocked until you allow them.')
                    : t('canvas_share_html_interactive_notice', 'Interactive content runs in an isolated preview.'));
        }
        sharedHtmlPreviewState.syncControls = syncPermissionControls;

        function renderCurrentPermissions() {
            syncPermissionControls();
            if (runtime && typeof runtime.render === 'function') {
                runtime.render(frame, sharedHtmlPreviewState.content, {
                    title: frame.title,
                    allowScripts: sharedHtmlPreviewState.allowScripts
                        && sharedHtmlPreviewState.allowExternalContent,
                    allowExternalContent: sharedHtmlPreviewState.allowExternalContent,
                    // Never turn public artifact HTML into a credentialed
                    // file-download deputy for a logged-in viewer.
                    hydrateAuthenticatedFiles: false,
                });
                return;
            }
            // A missing proxy runtime must fail closed instead of inserting
            // active public content into the share page.
            frame.setAttribute('sandbox', '');
            frame.srcdoc = sanitizeHtmlForSandboxedPreview(sharedHtmlPreviewState.content);
        }

        scriptsButton.addEventListener('click', () => {
            if (!sharedHtmlPreviewState.allowExternalContent) return;
            sharedHtmlPreviewState.allowScripts = !sharedHtmlPreviewState.allowScripts;
            renderCurrentPermissions();
        });
        externalButton.addEventListener('click', () => {
            sharedHtmlPreviewState.allowExternalContent = !sharedHtmlPreviewState.allowExternalContent;
            if (!sharedHtmlPreviewState.allowExternalContent) {
                sharedHtmlPreviewState.allowScripts = false;
            }
            renderCurrentPermissions();
        });

        toolbar.append(externalButton, scriptsButton);
        shell.append(toolbar, frame);
        canvasContent.innerHTML = '';
        canvasContent.appendChild(shell);
        // Keep the toolbar available whenever either explicit grant applies.
        toolbar.hidden = !sharedHtmlPreviewState.allowScripts
            && !capabilities.scripts
            && !capabilities.externalContent;
        renderCurrentPermissions();
    }

    function renderPdfArtifact(payload) {
        if (currentPdfObjectUrl) {
            URL.revokeObjectURL(currentPdfObjectUrl);
            currentPdfObjectUrl = '';
        }
        const encoding = String(payload?.encoding || '').trim().toLowerCase();
        if (encoding === 'base64' && !payload?.content) {
            throw new Error(t('canvas_share_pdf_missing_content', 'Shared PDF content is missing.'));
        }
        if (encoding !== 'base64') {
            throw new Error(t('canvas_share_pdf_invalid_encoding', 'Shared PDF content is not available in a supported format.'));
        }
        const blob = base64ToBlob(payload?.content, String(payload?.mime_type || 'application/pdf'));
        currentPdfObjectUrl = URL.createObjectURL(blob);
        const frame = document.createElement('iframe');
        frame.className = 'canvas-share-pdf-frame';
        frame.setAttribute('title', formatT('canvas_share_pdf_preview_title', 'Shared PDF preview: {name}', {
            name: payload?.file_name || t('canvas_share_page_title', 'Shared Canvas'),
        }));
        frame.src = currentPdfObjectUrl;
        canvasContent.innerHTML = '';
        canvasContent.appendChild(frame);
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) {
            return '';
        }

        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function isMermaidLanguage(language) {
        const normalized = String(language || '').trim().toLowerCase();
        return normalized === 'mermaid' || normalized === 'mmd';
    }

    function getMermaidTheme() {
        const mode = String(document?.documentElement?.dataset?.mode || '').toLowerCase();
        return mode === 'dark' ? 'dark' : 'default';
    }

    async function initializeMermaidRuntime() {
        const runtime = window.OmlorixMermaidRuntime;
        if (!runtime || typeof runtime.initializeMermaidRuntime !== 'function') {
            return null;
        }
        // The public page sanitizes generated SVG before inserting it. Pure
        // SVG labels remain visible after sanitization, unlike Mermaid's
        // default foreignObject-based HTML labels.
        return runtime.initializeMermaidRuntime({
            theme: getMermaidTheme(),
            htmlLabels: false,
        });
    }

    async function renderMermaidDiagram(target, source) {
        if (!target) {
            return false;
        }
        const runtime = window.OmlorixMermaidRuntime;
        const normalizedSource = runtime && typeof runtime.normalizeMermaidSource === 'function'
            ? runtime.normalizeMermaidSource(source)
            : String(source || '');
        const code = String(normalizedSource || '').trim();
        if (!code) {
            target.classList.add('mermaid-diagram-error');
            target.textContent = t('canvas_share_mermaid_empty', 'No Mermaid content.');
            return false;
        }

        let mermaidApi = null;
        try {
            mermaidApi = await initializeMermaidRuntime();
        } catch (_) {
            mermaidApi = null;
        }
        if (!mermaidApi || typeof mermaidApi.render !== 'function') {
            target.classList.add('mermaid-diagram-error');
            target.textContent = t('canvas_share_mermaid_unavailable', 'Mermaid renderer is unavailable.');
            return false;
        }

        const renderId = `mermaid-share-diagram-${Date.now()}-${mermaidRenderCounter++}`;
        try {
            const rendered = await mermaidApi.render(renderId, code);
            const svg = typeof rendered === 'string' ? rendered : rendered?.svg;
            if (!svg) {
                throw new Error(t('canvas_share_mermaid_empty_result', 'Mermaid returned an empty render result.'));
            }
            if (!setSanitizedSvg(target, svg)) {
                throw new Error(t('canvas_share_mermaid_sanitizer_unavailable', 'The Mermaid sanitizer is unavailable.'));
            }
            target.classList.remove('mermaid-diagram-error');
            sanitizeRenderedArtifactNode(target);
            if (typeof rendered?.bindFunctions === 'function') {
                rendered.bindFunctions(target);
            }
            return true;
        } catch (error) {
            target.classList.add('mermaid-diagram-error');
            target.textContent = formatT(
                'canvas_share_mermaid_error',
                'Mermaid render error: {message}',
                {
                    message: error?.message || t('canvas_share_mermaid_unknown_error', 'Unknown error'),
                },
            );
            return false;
        }
    }

    async function renderMermaidBlocks(root) {
        if (!root || typeof root.querySelectorAll !== 'function') {
            return;
        }
        const blocks = root.querySelectorAll('.mermaid-block');
        const tasks = [];
        blocks.forEach((block) => {
            const sourceEl = block.querySelector('.mermaid-block-source');
            const previewEl = block.querySelector('.mermaid-diagram');
            if (!sourceEl || !previewEl) {
                return;
            }
            const source = String(sourceEl.textContent || '');
            if (previewEl.dataset.mermaidSource === source) {
                return;
            }
            previewEl.dataset.mermaidSource = source;
            previewEl.textContent = t('canvas_share_mermaid_rendering', 'Rendering Mermaid diagram...');
            tasks.push(renderMermaidDiagram(previewEl, source));
        });
        if (tasks.length > 0) {
            await Promise.allSettled(tasks);
        }
    }

    async function renderMarkdownArtifact(content) {
        const article = document.createElement('article');
        article.className = 'canvas-share-markdown';

        if (typeof window.markdownit === 'function') {
            const md = window.markdownit({
                html: false,
                linkify: true,
                typographer: true,
            });
            if (typeof window.markdownitAlerts === 'function') {
                md.use(window.markdownitAlerts);
            }
            const defaultFenceRule = md.renderer.rules.fence || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
            md.renderer.rules.fence = (tokens, idx, options, env, self) => {
                const token = tokens[idx];
                const lang = String((token.info || '').trim().split(/\s+/)[0] || '').toLowerCase();
                if (isMermaidLanguage(lang)) {
                    const source = String(token.content || '');
                    return `<div class="mermaid-block"><div class="mermaid-diagram">${escapeHtml(t('canvas_share_mermaid_rendering', 'Rendering Mermaid diagram...'))}</div><pre class="mermaid-block-source" hidden>${escapeHtml(source)}</pre></div>`;
                }
                return defaultFenceRule(tokens, idx, options, env, self);
            };

            const rawContent = String(content || '');
            const contentWithAssetPlaceholders = replaceSharedAssetReferences(
                rawContent,
                { placeholders: true },
            );
            const normalizedContent = window.ChatMarkdownUtils
                && typeof window.ChatMarkdownUtils.normalizeMarkdownForRender === 'function'
                ? window.ChatMarkdownUtils.normalizeMarkdownForRender(contentWithAssetPlaceholders)
                : contentWithAssetPlaceholders;
            if (!setSanitizedHtml(article, md.render(normalizedContent))) {
                article.textContent = rawContent;
                canvasContent.innerHTML = '';
                canvasContent.appendChild(article);
                return;
            }
            window.ChatMarkdownAlerts?.enhanceIcons?.(article);
            article.querySelectorAll('a[href]').forEach((link) => {
                link.setAttribute('target', '_blank');
                link.setAttribute('rel', 'noopener noreferrer');
                link.setAttribute('referrerpolicy', 'no-referrer');
            });
            sanitizeRenderedArtifactNode(article);
            // Hydration happens after sanitization. The sanitizer sees only a
            // harmless same-origin placeholder and never has to trust blob URLs.
            hydrateSharedAssetPlaceholders(article);
            await renderMermaidBlocks(article);
        } else {
            article.textContent = String(content || '');
        }

        canvasContent.innerHTML = '';
        canvasContent.appendChild(article);
    }

    function renderCssArtifact(content) {
        const pre = document.createElement('pre');
        pre.className = 'canvas-share-css-view';
        const code = document.createElement('code');
        code.className = 'language-css';
        code.textContent = String(content || '');
        pre.appendChild(code);
        canvasContent.innerHTML = '';
        canvasContent.appendChild(pre);
        if (window.Prism && typeof window.Prism.highlightElement === 'function') {
            window.Prism.highlightElement(code);
        }
    }

    async function renderMermaidArtifact(content) {
        const article = document.createElement('article');
        article.className = 'canvas-share-markdown canvas-share-mermaid';
        const source = String(content || '');

        const block = document.createElement('div');
        block.className = 'mermaid-block';

        const preview = document.createElement('div');
        preview.className = 'mermaid-diagram';
        preview.textContent = t('canvas_share_mermaid_rendering', 'Rendering Mermaid diagram...');
        block.appendChild(preview);

        const hiddenSource = document.createElement('pre');
        hiddenSource.className = 'mermaid-block-source';
        hiddenSource.hidden = true;
        hiddenSource.textContent = source;
        block.appendChild(hiddenSource);

        const sourcePre = document.createElement('pre');
        sourcePre.className = 'canvas-share-mermaid-source';
        const code = document.createElement('code');
        code.className = 'language-none';
        code.textContent = source;
        sourcePre.appendChild(code);

        article.appendChild(block);
        article.appendChild(sourcePre);

        if (code && window.Prism && typeof window.Prism.highlightElement === 'function') {
            window.Prism.highlightElement(code);
        }
        await renderMermaidBlocks(article);

        canvasContent.innerHTML = '';
        canvasContent.appendChild(article);
    }

    async function renderCanvas(payload) {
        currentCanvasPayload = payload;
        resetSharedAssetObjectUrls(payload);
        const canvasType = getCanvasArtifactType(payload);
        const content = String(payload?.content || '');
        updateDocumentTitle(payload);

        if (canvasType === 'html') {
            renderHtmlArtifact(content);
        } else if (canvasType === 'css') {
            renderCssArtifact(content);
        } else if (canvasType === 'mermaid') {
            await renderMermaidArtifact(content);
        } else if (canvasType === 'pdf') {
            renderPdfArtifact(payload);
        } else {
            await renderMarkdownArtifact(content);
        }
        showView(canvasContainer);
    }

    function showPasswordPrompt(message, hasError = false) {
        if (passwordHelpText) {
            passwordHelpText.textContent = t('canvas_share_password_help', 'Enter the password to view this canvas.');
        }
        if (hasError) {
            setPasswordError(translateBackendDetail(
                message,
                t('canvas_share_password_error_invalid', 'Invalid password. Please try again.')
            ));
        } else {
            setPasswordError('');
        }
        if (passwordInput) {
            passwordInput.value = '';
            setTimeout(() => passwordInput.focus(), 0);
        }
        showView(passwordView);
    }

    function showError(message) {
        if (errorMessage) {
            errorMessage.textContent = translateBackendDetail(
                message,
                t('canvas_share_error_default', 'The shared canvas could not be loaded.')
            );
        }
        showView(errorView);
    }

    async function loadArtifact(shareId, password) {
        const attemptedPassword = String(password || '').length > 0;
        currentCanvasPayload = null;
        showView(loadingView);
        try {
            const payload = await fetchSharedArtifact(shareId, password);
            setPasswordError('');
            await renderCanvas(payload);
        } catch (error) {
            if (error?.requiresPassword) {
                showPasswordPrompt(error.message, attemptedPassword);
                return;
            }
            showError(error?.message || t('canvas_share_error_not_found', 'Shared canvas not found'));
        }
    }

    async function bootstrap() {
        if (!app) return;
        const shareId = extractShareId();
        if (!shareId) {
            showError(t('canvas_share_error_invalid_url', 'Invalid share URL'));
            return;
        }

        if (passwordForm) {
            passwordForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                const password = passwordInput ? String(passwordInput.value || '').trim() : '';
                if (!password) {
                    setPasswordError(t('canvas_share_password_error_empty', 'Enter a password.'));
                    passwordInput?.focus();
                    return;
                }
                await loadArtifact(shareId, password);
            });
        }

        passwordInput?.addEventListener('input', () => {
            if (passwordInput.getAttribute('aria-invalid') === 'true') {
                setPasswordError('');
            }
        });

        retryBtn?.addEventListener('click', () => {
            void loadArtifact(shareId, '');
        });
        document.addEventListener('i18n:updated', () => {
            if (currentCanvasPayload) {
                updateDocumentTitle(currentCanvasPayload);
            }
            sharedHtmlPreviewState?.syncControls?.();
        });
        window.addEventListener('pagehide', (event) => {
            // A persisted page can be restored from the back/forward cache,
            // and the rendered document still references these object URLs.
            if (event.persisted) return;
            sharedAssetObjectUrls.forEach((url) => URL.revokeObjectURL(url));
            sharedAssetObjectUrls.clear();
        });

        await loadArtifact(shareId, '');
    }

    bootstrap();
})();
