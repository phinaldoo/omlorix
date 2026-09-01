(() => {
    'use strict';

    if (window.ChatMarkdownBlockEditor) {
        return;
    }

    const TEXT_COLORS = ['#111827', '#dc2626', '#ea580c', '#d97706', '#16a34a', '#0891b2', '#2563eb', '#7c3aed', '#db2777', '#6b7280', '#f87171', '#fb923c', '#fbbf24', '#4ade80', '#22d3ee', '#60a5fa', '#a78bfa', '#f472b6'];
    const HIGHLIGHT_COLORS = ['#fff2a8', '#fde68a', '#bbf7d0', '#bae6fd', '#ddd6fe', '#fbcfe8', '#fecaca', '#e5e7eb', '#d9f99d', '#a7f3d0'];
    const OMLORIX_FILE_SCHEME = 'omlorix-file://';
    let sharedMarkdownRenderer = null;
    const registeredMarkdownPlugins = new Set();

    function mdEditorT(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function icon(name) {
        const sharedIcons = (typeof Icons === 'object' ? Icons : globalThis.Icons) || {};
        return sharedIcons.markdownEditorIcons?.[name] || '';
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function decodeHtmlEntities(value) {
        let decoded = String(value ?? '');
        for (let round = 0; round < 2; round += 1) {
            const textarea = document.createElement('textarea');
            textarea.innerHTML = decoded;
            const next = textarea.value || textarea.textContent || '';
            if (next === decoded) break;
            decoded = next;
        }
        return decoded;
    }

    function getOmlorixFileIdFromUrl(value) {
        const raw = decodeHtmlEntities(String(value || '').trim());
        if (!raw) return '';

        if (raw.toLowerCase().startsWith(OMLORIX_FILE_SCHEME)) {
            const withoutScheme = raw.slice(OMLORIX_FILE_SCHEME.length).split(/[?#]/, 1)[0];
            const decodedId = decodeURIComponent(withoutScheme || '').trim();
            return /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(decodedId) ? decodedId : '';
        }

        try {
            const parsed = new URL(raw, window.location.origin);
            if (parsed.pathname === '/api/v1/files/download') {
                const fileId = String(parsed.searchParams.get('file_id') || '').trim();
                return /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(fileId) ? fileId : '';
            }
        } catch (_) {}

        return '';
    }

    function buildOmlorixFileUrl(fileId) {
        const normalized = String(fileId || '').trim();
        return normalized ? OMLORIX_FILE_SCHEME + encodeURIComponent(normalized) : '';
    }

    function buildOmlorixFileDownloadUrl(fileId) {
        const normalized = String(fileId || '').trim();
        return normalized ? '/api/v1/files/download?file_id=' + encodeURIComponent(normalized) + '&inline=true' : '';
    }

    function prepareOmlorixFileReferences(container) {
        if (!container?.querySelectorAll) return;
        container.querySelectorAll('img[src], img[data-omlorix-file-id]').forEach((image) => {
            const fileId = getOmlorixFileIdFromUrl(image.getAttribute('data-omlorix-file-id') || image.getAttribute('src') || '');
            if (!fileId) return;
            image.setAttribute('data-omlorix-file-id', fileId);
            image.setAttribute('src', buildOmlorixFileDownloadUrl(fileId));
        });
        container.querySelectorAll('a[href], a[data-omlorix-file-id]').forEach((anchor) => {
            const fileId = getOmlorixFileIdFromUrl(anchor.getAttribute('data-omlorix-file-id') || anchor.getAttribute('href') || '');
            if (!fileId) return;
            anchor.setAttribute('data-omlorix-file-id', fileId);
            anchor.setAttribute('href', buildOmlorixFileDownloadUrl(fileId));
        });
    }

    function createInertHtmlTemplate(html) {
        const template = document.createElement('template');
        if ('content' in template) {
            template.innerHTML = String(html || '');
            return template;
        }

        const container = document.createElement('div');
        container.innerHTML = String(html || '');
        return {
            content: container,
            get innerHTML() {
                return container.innerHTML;
            },
        };
    }

    function prepareRenderedHtmlFileRefs(html) {
        const template = createInertHtmlTemplate(html);
        prepareOmlorixFileReferences(template.content);
        return template.innerHTML;
    }

    window.ChatMarkdownFileRefs = window.ChatMarkdownFileRefs || {
        scheme: OMLORIX_FILE_SCHEME,
        getFileIdFromUrl: getOmlorixFileIdFromUrl,
        buildFileUrl: buildOmlorixFileUrl,
        buildDownloadUrl: buildOmlorixFileDownloadUrl,
        prepareRenderedHtml: prepareRenderedHtmlFileRefs,
        prepareReferences: prepareOmlorixFileReferences,
        prepareImages: prepareOmlorixFileReferences,
    };

    function sanitizeMarkdownUrl(value, allowedProtocols) {
        const url = String(value ?? '').trim();
        if (!url) return '';
        const decodedUrl = decodeHtmlEntities(url).trim();
        const normalized = decodedUrl.replace(/[\u0000-\u001f\u007f\s]+/g, '');
        const schemeMatch = normalized.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):/);
        if (schemeMatch && !allowedProtocols.has(schemeMatch[1].toLowerCase() + ':')) {
            return '';
        }
        return escapeHtml(decodedUrl);
    }

    function sanitizeMarkdownLinkUrl(value) {
        return sanitizeMarkdownUrl(value, new Set(['http:', 'https:', 'mailto:', 'tel:']));
    }

    function sanitizeMarkdownImageUrl(value) {
        const url = String(value ?? '').trim();
        if (!url) return '';
        const decodedUrl = decodeHtmlEntities(url).trim();
        const normalized = decodedUrl.replace(/[\u0000-\u001f\u007f\s]+/g, '');
        const fileId = getOmlorixFileIdFromUrl(decodedUrl);
        if (fileId) return escapeHtml(buildOmlorixFileUrl(fileId));
        if (/^data:/i.test(normalized)) {
            return /^data:image\//i.test(normalized) ? escapeHtml(decodedUrl) : '';
        }
        return sanitizeMarkdownUrl(value, new Set(['http:', 'https:']));
    }

    function withLineBreaks(content) {
        return String(content ?? '').replace(/\n/g, '<br>');
    }

    function applyInlineMarkdown(text, refs) {
        let value = escapeHtml(String(text ?? ''));
        const codeSpans = [];
        value = value.replace(/`([^`]+?)`/g, (_, code) => {
            const index = codeSpans.length;
            codeSpans.push(code);
            return '\u0000CODE' + index + '\u0000';
        });
        value = value.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, alt, src) => {
            const safeSrc = sanitizeMarkdownImageUrl(src);
            return safeSrc ? '<img src="' + safeSrc + '" alt="' + alt + '" class="canvas-md-inline-image">' : alt;
        });
        if (refs) {
            value = value.replace(/!\[([^\]]*)\]\[([^\]]*)\]/g, (_, alt, ref) => {
                const safeSrc = sanitizeMarkdownImageUrl(refs[String(ref || alt || '').toLowerCase()]);
                return safeSrc ? '<img src="' + safeSrc + '" alt="' + alt + '" class="canvas-md-inline-image">' : alt;
            });
        }
        value = value.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, (_, label, href, title) => {
            const safeHref = sanitizeMarkdownLinkUrl(href);
            if (!safeHref) return label;
            const titleAttr = title ? ' title="' + escapeHtml(title) + '"' : '';
            return '<a href="' + safeHref + '"' + titleAttr + '>' + label + '</a>';
        });
        if (refs) {
            value = value.replace(/\[([^\]]+)\]\[([^\]]*)\]/g, (_, label, ref) => {
                const safeHref = sanitizeMarkdownLinkUrl(refs[String(ref || label || '').toLowerCase()]);
                return safeHref ? '<a href="' + safeHref + '">' + label + '</a>' : label;
            });
        }
        value = value
            .replace(/\*\*\*([^*]+?)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+?)\*/g, '<em>$1</em>')
            .replace(/~~([^~]+?)~~/g, '<s>$1</s>');
        value = value.replace(/\u0000CODE(\d+)\u0000/g, (_, index) => '<code>' + String(codeSpans[Number(index)] ?? '') + '</code>');
        return withLineBreaks(value);
    }

    function safeUrl(value, allowImage = false) {
        const url = String(value || '').trim();
        if (!url) return '';
        const fileId = getOmlorixFileIdFromUrl(url);
        if (fileId) return buildOmlorixFileDownloadUrl(fileId);
        const decoded = decodeHtmlEntities(url).replace(/[\u0000-\u001f\u007f\s]+/g, '');
        if (/^(javascript|vbscript):/i.test(decoded)) return '';
        if (/^data:/i.test(decoded)) return allowImage && /^data:image\//i.test(decoded) ? url : '';
        const schemeMatch = decoded.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):/);
        if (schemeMatch) {
            const protocol = schemeMatch[1].toLowerCase() + ':';
            const allowedProtocols = allowImage
                ? new Set(['http:', 'https:'])
                : new Set(['http:', 'https:', 'mailto:', 'tel:']);
            if (!allowedProtocols.has(protocol)) return '';
        }
        return url;
    }

    function normalizeMarkdownForRender(markdown) {
        if (window.ChatMarkdownUtils && typeof window.ChatMarkdownUtils.normalizeMarkdownForRender === 'function') {
            return window.ChatMarkdownUtils.normalizeMarkdownForRender(markdown);
        }
        return String(markdown ?? '');
    }

    /** Add optional plugins that became available after the shared renderer was created. */
    function registerAvailableMarkdownPlugins(renderer) {
        const plugins = [
            ['markdownitDeflist'],
            ['markdownitAbbr'],
            ['markdownitMark'],
            ['markdownitSub'],
            ['markdownitSup'],
            ['markdownitTaskLists', { enabled: true, label: true }],
            ['markdownitAlerts'],
        ];
        plugins.forEach(([globalName, options]) => {
            const plugin = window[globalName];
            if (!plugin || registeredMarkdownPlugins.has(globalName)) return;
            try {
                if (options) {
                    renderer.use(plugin, options);
                } else {
                    renderer.use(plugin);
                }
            } catch (error) {
                // A broken optional plugin must not prevent later plugins from
                // registering or repeatedly break reuse of the shared renderer.
                console.error(`Failed to register Markdown plugin "${globalName}"`, error);
            } finally {
                registeredMarkdownPlugins.add(globalName);
            }
        });
    }

    /**
     * Reuse one renderer across editor/live-preview updates while rechecking
     * optional plugins that can finish loading after the first render.
     */
    function getMarkdownRenderer() {
        if (sharedMarkdownRenderer) {
            registerAvailableMarkdownPlugins(sharedMarkdownRenderer);
            return sharedMarkdownRenderer;
        }
        if (typeof window.markdownit !== 'function') return null;

        const renderer = window.markdownit({ html: true, linkify: true, typographer: true, breaks: false });
        registerAvailableMarkdownPlugins(renderer);
        sharedMarkdownRenderer = renderer;
        return sharedMarkdownRenderer;
    }

    function renderMarkdownToHtml(markdown) {
        const source = normalizeMarkdownForRender(String(markdown ?? ''));
        if (!source.trim()) return '';
        let rendered = '';
        const renderer = getMarkdownRenderer();
        if (renderer) {
            rendered = renderer.render(source);
        } else {
            rendered = '<pre>' + escapeHtml(source) + '</pre>';
        }

        const sanitized = sanitizeEditorHtml(rendered);
        const prepared = prepareRenderedHtmlFileRefs(sanitized);
        return postProcessRenderedHtml(prepared);
    }

    function isSafeCssColor(value) {
        const color = String(value || '').trim();
        if (!color) return false;
        if (/^#[0-9a-f]{3,8}$/i.test(color)) return true;
        if (/^rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)$/i.test(color)) return true;
        return ['transparent', 'inherit', 'currentcolor'].includes(color.toLowerCase());
    }

    function getSafeStyleAttribute(styleText) {
        const allowed = [];
        String(styleText || '').split(';').forEach((declaration) => {
            const [propertyRaw, ...valueParts] = declaration.split(':');
            const property = String(propertyRaw || '').trim().toLowerCase();
            const value = valueParts.join(':').trim();
            if (!property || !value) return;
            if ((property === 'color' || property === 'background-color' || property === 'background') && isSafeCssColor(value)) {
                allowed.push(property + ': ' + value);
            }
            if (property === 'text-align' && /^(left|center|right|justify)$/i.test(value)) {
                allowed.push('text-align: ' + value.toLowerCase());
            }
        });
        return allowed.join('; ');
    }

    function scrubUnsafeEditorStyles(container) {
        container.querySelectorAll('[style]').forEach((node) => {
            const safeStyle = getSafeStyleAttribute(node.getAttribute('style') || '');
            if (safeStyle) node.setAttribute('style', safeStyle);
            else node.removeAttribute('style');
        });
    }

    function sanitizeEditorHtml(html) {
        const source = String(html || '');
        if (!source) return '';
        if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
            const clean = window.DOMPurify.sanitize(source, {
                USE_PROFILES: { html: true },
                ADD_TAGS: ['input', 'mark', 'sup', 'sub'],
                ADD_ATTR: ['align', 'alt', 'checked', 'class', 'data-omlorix-file-id', 'disabled', 'hidden', 'href', 'src', 'style', 'title', 'type'],
                ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|omlorix-file:\/\/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}(?:[?#][^\s]*)?|data:image\/|[#/]|\.\/|\.\.\/|[^a-z]|[a-z0-9+.-]+(?:[^a-z0-9+.-:]|$))/i,
                FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'frame', 'frameset', 'meta', 'base', 'link', 'form', 'button', 'select', 'textarea'],
                FORBID_ATTR: ['srcdoc'],
                ALLOW_DATA_ATTR: false,
            });
            const template = createInertHtmlTemplate(clean);
            const container = template.content;
            scrubUnsafeEditorStyles(container);
            container.querySelectorAll('input').forEach((input) => {
                if (String(input.getAttribute('type') || '').toLowerCase() !== 'checkbox') {
                    input.remove();
                }
            });
            container.querySelectorAll('a[href], a[data-omlorix-file-id]').forEach((anchor) => {
                const fileId = getOmlorixFileIdFromUrl(anchor.getAttribute('data-omlorix-file-id') || anchor.getAttribute('href') || '');
                const href = fileId ? buildOmlorixFileDownloadUrl(fileId) : safeUrl(anchor.getAttribute('href') || '');
                if (!href) {
                    anchor.removeAttribute('href');
                    return;
                }
                if (fileId) anchor.setAttribute('data-omlorix-file-id', fileId);
                anchor.setAttribute('href', href);
                anchor.setAttribute('target', '_blank');
                anchor.setAttribute('rel', 'noopener noreferrer nofollow');
            });
            container.querySelectorAll('img[src], img[data-omlorix-file-id]').forEach((image) => {
                const rawSrc = image.getAttribute('src') || '';
                const fileId = getOmlorixFileIdFromUrl(image.getAttribute('data-omlorix-file-id') || rawSrc);
                const src = fileId ? buildOmlorixFileDownloadUrl(fileId) : safeUrl(rawSrc, true);
                if (fileId) image.setAttribute('data-omlorix-file-id', fileId);
                if (src) image.setAttribute('src', src);
                else image.removeAttribute('src');
            });
            return template.innerHTML;
        }

        const sanitizer = window.ChatSanitizer;
        if (sanitizer && typeof sanitizer.sanitizeHtml === 'function') {
            return sanitizer.sanitizeHtml(source, { allowDataAttrs: false });
        }
        return escapeHtml(source);
    }

    function postProcessRenderedHtml(html) {
        const template = createInertHtmlTemplate(html);
        const container = template.content;
        container.querySelectorAll('li').forEach((li) => {
            const checkbox = li.querySelector(':scope > input[type="checkbox"]');
            if (!checkbox) return;
            checkbox.disabled = false;
            li.classList.add('task-list-item');
            const list = li.closest('ul');
            if (list) list.classList.add('task-list');
        });
        container.querySelectorAll('table').forEach((table) => {
            if (table.parentElement?.classList.contains('canvas-md-editor-table-wrap')) return;
            const wrapper = document.createElement('div');
            wrapper.className = 'canvas-md-editor-table-wrap';
            table.replaceWith(wrapper);
            wrapper.appendChild(table);
        });

        // Canvas is a rich-text editor, but fenced code must not have a second,
        // subtly different renderer. Upgrade the sanitized <pre><code> nodes
        // with the same renderer used by chat messages. This supplies the
        // shared header, syntax highlighting, Code/Preview tabs, downloads,
        // and every supported preview kind (HTML, Mermaid, Vega, SVG, data,
        // and Markdown) without allowing raw Markdown HTML
        // to inject any of those trusted controls.
        upgradeEditorFencedCodeBlocks(container);
        window.ChatMarkdownAlerts?.enhanceIcons?.(container);
        container.querySelectorAll('.markdown-alert-title').forEach((title) => {
            title.setAttribute('contenteditable', 'false');
        });
        return template.innerHTML;
    }

    /** Return the primary Markdown language stored on a rendered code node. */
    function getFencedCodeLanguage(pre) {
        const code = pre?.querySelector?.('code');
        const classNames = `${code?.className || ''} ${pre?.className || ''}`;
        const match = classNames.match(/(?:^|\s)language-([^\s]+)/i);
        return match ? match[1] : '';
    }

    /**
     * Build a fence that remains valid even when the source itself contains a
     * run of backticks. The language is derived from markdown-it's class name,
     * so only its primary, whitespace-free info token is retained.
     */
    function buildFencedMarkdown(language, source) {
        const code = String(source ?? '').replace(/\n+$/g, '');
        const longestRun = Math.max(0, ...Array.from(code.matchAll(/`+/g), (match) => match[0].length));
        const fence = '`'.repeat(Math.max(3, longestRun + 1));
        const safeLanguage = String(language || '').trim().replace(/[^a-zA-Z0-9_+.#-]/g, '');
        return `${fence}${safeLanguage}\n${code}\n${fence}`;
    }

    /** Create one trusted, shared chat code block for the Canvas editor. */
    function createSharedCodeBlockHost(pre) {
        if (!pre || typeof window.renderMarkdownContent !== 'function') return null;
        const code = pre.querySelector('code');
        const language = getFencedCodeLanguage(pre);
        const source = String(code?.textContent ?? pre.textContent ?? '');
        const staging = document.createElement('div');

        // renderMarkdownContent owns the canonical fence renderer and all of
        // its enhancement/security behavior. Rendering a reconstructed fence
        // means user-provided HTML cannot become trusted editor chrome.
        window.renderMarkdownContent(staging, buildFencedMarkdown(language, source));
        const sharedWrapper = staging.querySelector('.code-block-wrapper');
        if (!sharedWrapper) return null;

        // Preview renderers are asynchronous and attach lifecycle state to DOM
        // objects. The editor returns an HTML string, so reset the staged node
        // before serialization; it will be hydrated after insertion below.
        window.prepareMarkdownCodeBlocksForTransfer?.(staging);

        const host = document.createElement('div');
        host.className = 'canvas-md-shared-code-block markdown-body';
        host.setAttribute('contenteditable', 'false');
        host.dataset.canvasMdLanguage = language;
        host.appendChild(sharedWrapper);
        return host;
    }

    /** Replace every ordinary markdown-it fence with the canonical chat UI. */
    function upgradeEditorFencedCodeBlocks(container) {
        if (!container || typeof container.querySelectorAll !== 'function') return;
        Array.from(container.querySelectorAll('pre')).forEach((pre) => {
            if (pre.closest('.canvas-md-shared-code-block')) return;
            const host = createSharedCodeBlockHost(pre);
            if (host) pre.replaceWith(host);
        });
    }

    function escapeMarkdownText(value) {
        return String(value ?? '').replace(/([\\`*_{}\[\]()#+\-.!|>])/g, '\\$1');
    }

    function normalizeText(value) {
        return String(value ?? '').replace(/\u00a0/g, ' ');
    }

    function markdownInlineFromNode(node) {
        if (!node) return '';
        if (node.nodeType === Node.TEXT_NODE) return normalizeText(node.textContent);
        if (node.nodeType !== Node.ELEMENT_NODE) return '';

        // A browser can occasionally wrap an atomic contenteditable=false
        // block while editing around it. Recognize the shared code host even
        // from the inline serializer so toolbar and preview text can never be
        // mistaken for document content.
        if (
            node.classList.contains('canvas-md-shared-code-block')
            || node.classList.contains('code-block-wrapper')
        ) {
            return fencedCodeFromSharedBlock(node);
        }

        const tag = node.tagName.toLowerCase();
        const children = Array.from(node.childNodes).map(markdownInlineFromNode).join('');
        if (tag === 'br') return '\n';
        if (tag === 'strong' || tag === 'b') return children ? '**' + children + '**' : '';
        if (tag === 'em' || tag === 'i') return children ? '*' + children + '*' : '';
        if (tag === 's' || tag === 'del' || tag === 'strike') return children ? '~~' + children + '~~' : '';
        if (tag === 'u') return children ? '<u>' + children + '</u>' : '';
        if (tag === 'mark') return children ? '==' + children + '==' : '';
        if (tag === 'sup' || tag === 'sub') return children ? '<' + tag + '>' + children + '</' + tag + '>' : '';
        if (tag === 'span') {
            const safeStyle = getSafeStyleAttribute(node.getAttribute('style') || '');
            return safeStyle && children ? '<span style="' + escapeHtml(safeStyle) + '">' + children + '</span>' : children;
        }
        if (tag === 'code') return '`' + String(node.textContent ?? '').replace(/`/g, '\\`') + '`';
        if (tag === 'a') {
            const fileId = getOmlorixFileIdFromUrl(node.getAttribute('data-omlorix-file-id') || node.getAttribute('href') || '');
            const href = fileId ? buildOmlorixFileUrl(fileId) : safeUrl(node.getAttribute('href') || '');
            const label = children || normalizeText(node.textContent);
            const title = normalizeText(node.getAttribute('title') || '').replace(/"/g, '\\"');
            return href ? '[' + label + '](' + href + (title ? ' "' + title + '"' : '') + ')' : label;
        }
        if (tag === 'img') {
            const fileId = getOmlorixFileIdFromUrl(node.getAttribute('data-omlorix-file-id') || node.getAttribute('src') || '');
            const src = fileId ? buildOmlorixFileUrl(fileId) : safeUrl(node.getAttribute('src') || '', true);
            return src ? '![' + escapeMarkdownText(node.getAttribute('alt') || '') + '](' + src + ')' : '';
        }
        if (tag === 'input' && node.getAttribute('type') === 'checkbox') return '';
        return children;
    }

    function markdownInlineFromHtml(html) {
        const container = document.createElement('div');
        container.innerHTML = String(html ?? '');
        return Array.from(container.childNodes).map(markdownInlineFromNode).join('').replace(/[ \t]+\n/g, '\n').trim();
    }

    function fencedCodeFromPre(pre) {
        const code = pre.querySelector('code');
        return buildFencedMarkdown(
            getFencedCodeLanguage(pre),
            String(code?.textContent ?? pre.textContent ?? ''),
        );
    }

    /** Serialize a shared wrapper without including its toolbar or preview. */
    function fencedCodeFromSharedBlock(node) {
        const host = node.classList?.contains('canvas-md-shared-code-block')
            ? node
            : node.closest?.('.canvas-md-shared-code-block');
        const wrapper = node.classList?.contains('code-block-wrapper')
            ? node
            : node.querySelector?.('.code-block-wrapper');
        const code = wrapper?.querySelector?.('.code-block-panel-code code[data-code-id], .code-block-panel-code code');
        const language = host?.dataset?.canvasMdLanguage || wrapper?.dataset?.language || '';
        return buildFencedMarkdown(language, String(code?.textContent ?? ''));
    }

    function tableToMarkdown(table) {
        const rows = Array.from(table.rows || []);
        if (!rows.length) return '';
        const normalized = rows.map((row) => Array.from(row.cells || []).map((cell) => markdownInlineFromHtml(cell.innerHTML)));
        const header = normalized[0] && normalized[0].length ? normalized[0] : [''];
        const lines = [
            '| ' + header.join(' | ') + ' |',
            '| ' + header.map(() => '---').join(' | ') + ' |',
        ];
        normalized.slice(1).forEach((row) => {
            const padded = row.slice();
            while (padded.length < header.length) padded.push('');
            lines.push('| ' + padded.slice(0, header.length).join(' | ') + ' |');
        });
        return lines.join('\n');
    }

    function listToMarkdown(list, depth = 0) {
        const ordered = list.tagName.toLowerCase() === 'ol';
        let number = Number(list.getAttribute('start') || '1');
        return Array.from(list.children || [])
            .filter((child) => child.tagName && child.tagName.toLowerCase() === 'li')
            .map((li) => {
                const clone = li.cloneNode(true);
                const nestedLists = Array.from(clone.querySelectorAll(':scope > ul, :scope > ol'));
                nestedLists.forEach((nested) => nested.remove());
                const checkbox = clone.querySelector('input[type="checkbox"]');
                if (checkbox) checkbox.remove();
                const marker = checkbox
                    ? '- [' + (checkbox.checked || checkbox.hasAttribute('checked') ? 'x' : ' ') + '] '
                    : (ordered ? String(number++) + '. ' : '- ');
                const prefix = '  '.repeat(depth);
                const line = prefix + marker + markdownInlineFromHtml(clone.innerHTML);
                const nested = nestedLists.map((nestedList) => listToMarkdown(nestedList, depth + 1)).filter(Boolean).join('\n');
                return nested ? line + '\n' + nested : line;
            })
            .join('\n');
    }

    function blockToMarkdown(node) {
        if (!node || node.nodeType !== Node.ELEMENT_NODE) return '';
        const tag = node.tagName.toLowerCase();
        if (
            node.classList.contains('canvas-md-shared-code-block')
            || node.classList.contains('code-block-wrapper')
        ) {
            return fencedCodeFromSharedBlock(node);
        }
        if (tag === 'div' && node.classList.contains('mermaid-block')) {
            const source = node.querySelector('.mermaid-block-source')?.textContent || '';
            return source.trim() ? '```mermaid\n' + source.replace(/\n+$/g, '') + '\n```' : '';
        }
        const alignedHtml = (() => {
            const align = String(node.style?.textAlign || node.getAttribute('align') || '').toLowerCase();
            if (!/^(center|right|justify)$/.test(align)) return '';
            if (!['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(tag)) return '';
            return '<' + tag + ' align="' + align + '">' + markdownInlineFromHtml(node.innerHTML) + '</' + tag + '>';
        })();
        if (alignedHtml) return alignedHtml;
        if (tag === 'h1') return '# ' + markdownInlineFromHtml(node.innerHTML);
        if (tag === 'h2') return '## ' + markdownInlineFromHtml(node.innerHTML);
        if (tag === 'h3') return '### ' + markdownInlineFromHtml(node.innerHTML);
        if (tag === 'h4') return '#### ' + markdownInlineFromHtml(node.innerHTML);
        if (tag === 'h5') return '##### ' + markdownInlineFromHtml(node.innerHTML);
        if (tag === 'h6') return '###### ' + markdownInlineFromHtml(node.innerHTML);
        if (tag === 'blockquote') {
            const alertType = node.classList.contains('markdown-alert')
                ? ['note', 'tip', 'important', 'warning', 'caution']
                    .find((type) => node.classList.contains(`markdown-alert-${type}`))
                : undefined;
            if (alertType) {
                // Alert titles and icons are generated presentation. Omit them
                // when converting the rich editor back to source Markdown, and
                // serialize each body block so lists, code, and paragraphs keep
                // their original structure.
                const alertBody = node.cloneNode(true);
                alertBody.querySelector(':scope > .markdown-alert-title')?.remove();
                const bodyMarkdown = Array.from(alertBody.childNodes)
                    .map((child) => {
                        if (child.nodeType === Node.TEXT_NODE) {
                            return normalizeText(child.textContent).trim();
                        }
                        return blockToMarkdown(child);
                    })
                    .map((value) => String(value || '').trim())
                    .filter(Boolean)
                    .join('\n\n');
                const alertMarkdown = `[!${alertType.toUpperCase()}]${bodyMarkdown ? `\n${bodyMarkdown}` : ''}`;
                return alertMarkdown.split('\n').map((line) => (line ? `> ${line}` : '>')).join('\n');
            }
            const text = markdownInlineFromHtml(node.innerHTML);
            return text.split('\n').map((line) => '> ' + line).join('\n');
        }
        if (tag === 'pre') return fencedCodeFromPre(node);
        if (tag === 'ul' || tag === 'ol') return listToMarkdown(node);
        if (tag === 'hr') return '---';
        if (tag === 'table') return tableToMarkdown(node);
        if (tag === 'div' && node.classList.contains('canvas-md-editor-table-wrap')) {
            const table = node.querySelector('table');
            return table ? tableToMarkdown(table) : '';
        }
        return markdownInlineFromHtml(node.innerHTML);
    }

    function htmlToMarkdown(html) {
        const container = document.createElement('div');
        container.innerHTML = String(html ?? '');
        const blocks = Array.from(container.childNodes).map((node) => {
            if (node.nodeType === Node.TEXT_NODE) return normalizeText(node.textContent).trim();
            return blockToMarkdown(node);
        }).map((value) => String(value || '').trim()).filter(Boolean);
        return blocks.join('\n\n');
    }

    function createElement(tagName, className, text) {
        const element = document.createElement(tagName);
        if (className) element.className = className;
        if (typeof text === 'string') element.textContent = text;
        return element;
    }

    /** Clean up any mounted shared preview before replacing editor markup. */
    function cleanupEditorCodeBlockPreviews(editor) {
        if (!editor || typeof window.cleanupMarkdownCodeBlockPreviews !== 'function') return;
        window.cleanupMarkdownCodeBlockPreviews(editor);
    }

    /** Mount default/on-demand preview state only after blocks are connected. */
    function hydrateEditorCodeBlocks(editor) {
        if (!editor || typeof editor.querySelectorAll !== 'function') return;
        const hydrate = () => {
            if (!editor.isConnected) return false;
            editor.querySelectorAll('.canvas-md-shared-code-block').forEach((host) => {
                window.finalizeCodeBlockPreviewState?.(host);
            });
            return true;
        };
        if (hydrate()) return;

        // create() builds the editor before Canvas/Notes appends its shell.
        // A microtask runs after that synchronous handoff and keeps size-aware
        // Mermaid/Vega rendering out of a detached DOM.
        queueMicrotask(() => {
            if (!hydrate()) window.requestAnimationFrame(hydrate);
        });
    }

    /** Return history markup without serializing live iframe/chart internals. */
    function getEditorHistoryHtml(editor) {
        const clone = editor.cloneNode(true);
        window.prepareMarkdownCodeBlocksForTransfer?.(clone);
        return clone.innerHTML;
    }

    /** Render and install a Markdown snapshot through the editor pipeline. */
    function replaceEditorMarkdown(editor, markdown) {
        cleanupEditorCodeBlockPreviews(editor);
        editor.innerHTML = renderMarkdownToHtml(markdown);
        hydrateEditorCodeBlocks(editor);
    }

    function create(options = {}) {
        const editable = options.editable !== false;
        const shell = createElement('div', 'canvas-md-editor-shell');
        shell.classList.toggle('is-readonly', !editable);

        const toolbar = createElement('div', 'canvas-md-editor-toolbar');
        toolbar.setAttribute('role', 'toolbar');
        toolbar.setAttribute('aria-label', mdEditorT('markdown_editor_formatting_toolbar', 'Formatting toolbar'));

        const blockSelect = document.createElement('button');
        blockSelect.type = 'button';
        blockSelect.className = 'canvas-md-toolbar-btn canvas-md-editor-block-select has-caret';
        blockSelect.dataset.command = 'menu:block';
        blockSelect.setAttribute('aria-label', mdEditorT('markdown_editor_text_style', 'Text style'));
        blockSelect.innerHTML = '<span class="canvas-md-current-block-label">' + escapeHtml(mdEditorT('markdown_editor_paragraph', 'Paragraph')) + '</span>';
        toolbar.appendChild(blockSelect);
        toolbar.appendChild(toolbarSeparator());
        [
            ['bold', 'bold', mdEditorT('markdown_editor_bold', 'Bold')],
            ['italic', 'italic', mdEditorT('markdown_editor_italic', 'Italic')],
            ['underline', 'underline', mdEditorT('markdown_editor_underline', 'Underline')],
            ['strike', 'strikeThrough', mdEditorT('markdown_editor_strikethrough', 'Strikethrough')],
            ['code', 'inlineCode', mdEditorT('markdown_editor_inline_code', 'Inline code')],
        ].forEach(([iconName, command, label]) => toolbar.appendChild(toolbarButton(iconName, label, command)));
        toolbar.appendChild(toolbarSeparator());
        [
            ['color', 'menu:color', mdEditorT('markdown_editor_text_color', 'Text color'), true],
            ['highlight', 'menu:highlight', mdEditorT('markdown_editor_highlight_color', 'Highlight color'), true],
        ].forEach(([iconName, command, label, caret]) => toolbar.appendChild(toolbarButton(iconName, label, command, { caret })));
        toolbar.appendChild(toolbarSeparator());
        toolbar.appendChild(toolbarButton('alignLeft', mdEditorT('markdown_editor_alignment', 'Alignment'), 'menu:align', { caret: true }));
        toolbar.appendChild(toolbarSeparator());
        [
            ['list', 'insertUnorderedList', mdEditorT('markdown_editor_slash_bulleted_list', 'Bulleted list')],
            ['ordered', 'insertOrderedList', mdEditorT('markdown_editor_slash_numbered_list', 'Numbered list')],
            ['task', 'taskList', mdEditorT('markdown_editor_slash_todo', 'To-do')],
            ['outdent', 'outdent', mdEditorT('markdown_editor_outdent', 'Decrease indent')],
            ['indent', 'indent', mdEditorT('markdown_editor_indent', 'Increase indent')],
        ].forEach(([iconName, command, label]) => toolbar.appendChild(toolbarButton(iconName, label, command)));
        toolbar.appendChild(toolbarSeparator());
        [
            ['link', 'link', mdEditorT('markdown_editor_insert_link', 'Insert link')],
            ['image', 'image', mdEditorT('markdown_editor_insert_image', 'Insert image')],
            ['table', 'menu:table', mdEditorT('markdown_editor_insert_table', 'Insert table'), true],
        ].forEach(([iconName, command, label, caret]) => toolbar.appendChild(toolbarButton(iconName, label, command, { caret })));
        toolbar.appendChild(toolbarSeparator());
        toolbar.appendChild(toolbarButton('more', mdEditorT('markdown_editor_more_actions', 'More actions'), 'menu:more'));

        const tableToolbar = createElement('div', 'canvas-md-editor-table-toolbar');
        tableToolbar.setAttribute('role', 'toolbar');
        tableToolbar.setAttribute('aria-label', mdEditorT('markdown_editor_table_tools', 'Table tools'));
        tableToolbar.appendChild(createElement('span', 'canvas-md-table-toolbar-title', mdEditorT('markdown_editor_slash_table', 'Table')));
        [
            ['plus', 'rowAbove', mdEditorT('markdown_editor_row_above', 'Row above'), true],
            ['plus', 'rowBelow', mdEditorT('markdown_editor_row_below', 'Row below'), true],
            ['trash', 'deleteRow', mdEditorT('markdown_editor_delete_row', 'Delete row'), false],
        ].forEach(([iconName, command, label, showLabel]) => tableToolbar.appendChild(toolbarButton(iconName, label, command, { label: showLabel ? label : '' })));
        tableToolbar.appendChild(toolbarSeparator());
        [
            ['plus', 'colBefore', mdEditorT('markdown_editor_column_before', 'Column before'), true],
            ['plus', 'colAfter', mdEditorT('markdown_editor_column_after', 'Column after'), true],
            ['trash', 'deleteColumn', mdEditorT('markdown_editor_delete_column', 'Delete column'), false],
        ].forEach(([iconName, command, label, showLabel]) => tableToolbar.appendChild(toolbarButton(iconName, label, command, { label: showLabel ? label : '' })));
        tableToolbar.appendChild(toolbarSeparator());
        tableToolbar.appendChild(toolbarButton('alignLeft', mdEditorT('markdown_editor_column_alignment', 'Column alignment'), 'menu:tableAlign', { caret: true }));
        tableToolbar.appendChild(toolbarButton('table', mdEditorT('markdown_editor_toggle_header_row', 'Toggle header row'), 'toggleHeader', { label: mdEditorT('markdown_editor_header_row', 'Header') }));
        tableToolbar.appendChild(toolbarButton('clear', mdEditorT('markdown_editor_clear_cell', 'Clear cell'), 'clearCell'));
        tableToolbar.appendChild(toolbarSeparator());
        tableToolbar.appendChild(toolbarButton('trash', mdEditorT('markdown_editor_delete_table', 'Delete table'), 'deleteTable', { danger: true, label: mdEditorT('markdown_editor_delete_table', 'Delete table') }));

        const body = createElement('div', 'canvas-md-editor-body');
        const editorView = createElement('section', 'canvas-md-editor-view is-active');
        const editor = createElement('div', 'canvas-md-rich-editor');
        editor.contentEditable = editable ? 'true' : 'false';
        editor.spellcheck = true;
        editor.setAttribute('role', 'textbox');
        editor.setAttribute('aria-multiline', 'true');
        editor.setAttribute('aria-label', mdEditorT('markdown_editor_rich_editor_aria', 'Document editor'));
        editor.dataset.placeholder = mdEditorT('markdown_editor_placeholder', 'Start writing...');
        editorView.appendChild(editor);

        const sourceView = createElement('section', 'canvas-md-editor-source-view');
        sourceView.hidden = true;
        const sourceShell = createElement('div', 'canvas-md-source-shell');
        const sourceGutter = createElement('div', 'canvas-md-source-gutter');
        sourceGutter.setAttribute('aria-hidden', 'true');
        const sourceEditor = document.createElement('textarea');
        sourceEditor.className = 'canvas-md-source-editor canvas-raw-editor';
        sourceEditor.spellcheck = false;
        sourceEditor.autocapitalize = 'off';
        sourceEditor.autocomplete = 'off';
        sourceEditor.autocorrect = 'off';
        sourceEditor.readOnly = !editable;
        sourceEditor.disabled = !editable;
        sourceEditor.setAttribute('aria-label', mdEditorT('markdown_editor_source_aria', 'Markdown source'));
        sourceEditor.placeholder = mdEditorT('markdown_editor_source_placeholder', 'Start writing Markdown...');
        sourceShell.append(sourceGutter, sourceEditor);
        sourceView.appendChild(sourceShell);
        body.append(editorView, sourceView);

        let activeReferenceSelectionData = null;
        const referenceToolbarController = window.createSelectionActionTooltip({
            className: 'canvas-md-reference-toolbar',
            getSelectionText: () => (
                activeReferenceSelectionData?.text
                || readReferenceSelectionData()?.text
                || ''
            ),
            onAddReference: (text) => {
                if (typeof options.onReferenceSelection !== 'function') return;
                const selectionData = activeReferenceSelectionData || readReferenceSelectionData() || { text };
                options.onReferenceSelection({ ...selectionData, text });
            },
            clearSelection: clearReferenceSelection,
            getLabels: () => ({
                copyLabel: mdEditorT('chat_selection_copy_label', 'Copy'),
                copyTitle: mdEditorT('chat_selection_copy_title', 'Copy'),
                addReferenceLabel: mdEditorT('canvas_add_selection_reference_label', 'Add reference'),
                addReferenceTitle: mdEditorT('canvas_add_selection_reference_aria', 'Add marked selection as reference'),
            }),
        });
        const referenceToolbar = referenceToolbarController.element;

        if (editable) shell.append(toolbar, tableToolbar);
        shell.append(body, referenceToolbar);

        let currentMarkdown = String(options.value ?? '');
        let activeView = 'editor';
        let savedRange = null;
        let destroyed = false;
        let lastActiveCell = null;
        let paintFormat = null;
        const history = { stack: [], index: -1, suspended: false };
        const cleanupFns = [];

        replaceEditorMarkdown(editor, currentMarkdown);
        renderMermaidBlocksInEditor();
        sourceEditor.value = currentMarkdown;
        updatePlaceholder();
        recordHistory(true);
        updateHistoryButtons();
        updateTableToolbar();

        function toolbarButton(iconName, label, command, options = {}) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'canvas-md-toolbar-btn';
            if (options.caret) button.classList.add('has-caret');
            if (options.danger) button.classList.add('danger');
            button.dataset.command = command;
            button.title = label;
            button.setAttribute('aria-label', label);
            button.innerHTML = icon(iconName) + (options.label ? '<span class="canvas-md-toolbar-label">' + escapeHtml(options.label) + '</span>' : '');
            return button;
        }

        function toolbarSeparator() {
            const separator = createElement('span', 'canvas-md-toolbar-separator');
            separator.setAttribute('aria-hidden', 'true');
            return separator;
        }

        function addListener(target, eventName, handler, opts) {
            target.addEventListener(eventName, handler, opts);
            cleanupFns.push(() => target.removeEventListener(eventName, handler, opts));
        }

        let sourceCodeMirror = null;

        function lineNumberMarkup(value) {
            const count = Math.max(1, String(value ?? '').split('\n').length);
            let markup = '';
            for (let line = 1; line <= count; line += 1) {
                markup += '<span>' + line + '</span>';
            }
            return markup;
        }

        function refreshSourceGutter() {
            if (sourceCodeMirror) return;
            sourceGutter.innerHTML = lineNumberMarkup(sourceEditor.value);
            sourceGutter.scrollTop = sourceEditor.scrollTop;
        }

        function getSourceValue() {
            return sourceCodeMirror ? sourceCodeMirror.getValue() : sourceEditor.value;
        }

        function setSourceValue(value) {
            const next = String(value ?? '');
            if (sourceCodeMirror) {
                sourceCodeMirror.setValue(next);
                return;
            }
            sourceEditor.value = next;
            refreshSourceGutter();
        }

        function focusSourceEditor() {
            if (!editable) return;
            if (sourceCodeMirror) {
                sourceCodeMirror.focus();
                return;
            }
            sourceEditor.focus({ preventScroll: true });
        }

        function refreshSourceEditor() {
            if (sourceCodeMirror) {
                sourceCodeMirror.refresh();
                return;
            }
            refreshSourceGutter();
        }

        /** Capture every possible scroll owner before Markdown DOM is replaced. */
        function captureViewportScrollState() {
            const codeMirrorScroll = sourceCodeMirror?.getScrollInfo?.() || null;
            return {
                view: activeView,
                editorScrollTop: editorView.scrollTop,
                editorScrollLeft: editorView.scrollLeft,
                sourceScrollTop: codeMirrorScroll ? codeMirrorScroll.top : sourceEditor.scrollTop,
                sourceScrollLeft: codeMirrorScroll ? codeMirrorScroll.left : sourceEditor.scrollLeft,
            };
        }

        /** Restore immediately and after layout settles so async blocks cannot reset it. */
        function restoreViewportScrollState(snapshot) {
            if (!snapshot || typeof snapshot !== 'object') return;
            const restore = () => {
                if (destroyed) return;
                const requestedView = snapshot.view === 'source' ? 'source' : 'editor';
                if (requestedView !== activeView) {
                    // Programmatic refreshes should retain the user's mode but
                    // must not steal focus from the chat composer or toolbar.
                    switchView(requestedView, { focus: false });
                }
                editorView.scrollTop = Math.max(Number(snapshot.editorScrollTop) || 0, 0);
                editorView.scrollLeft = Math.max(Number(snapshot.editorScrollLeft) || 0, 0);
                const sourceTop = Math.max(Number(snapshot.sourceScrollTop) || 0, 0);
                const sourceLeft = Math.max(Number(snapshot.sourceScrollLeft) || 0, 0);
                if (sourceCodeMirror?.scrollTo) {
                    sourceCodeMirror.scrollTo(sourceLeft, sourceTop);
                } else {
                    sourceEditor.scrollTop = sourceTop;
                    sourceEditor.scrollLeft = sourceLeft;
                    sourceGutter.scrollTop = sourceTop;
                }
            };
            restore();
            window.requestAnimationFrame(restore);
        }

        function insertSourceText(text) {
            const start = sourceEditor.selectionStart;
            const end = sourceEditor.selectionEnd;
            const before = sourceEditor.value.slice(0, start);
            const after = sourceEditor.value.slice(end);
            sourceEditor.value = before + text + after;
            sourceEditor.selectionStart = sourceEditor.selectionEnd = start + text.length;
            syncFromSource();
            refreshSourceGutter();
        }

        function maybeContinueMarkdownList() {
            const start = sourceEditor.selectionStart;
            const end = sourceEditor.selectionEnd;
            if (start !== end) return false;
            const value = sourceEditor.value;
            const lineStart = value.lastIndexOf('\n', start - 1) + 1;
            const line = value.slice(lineStart, start);
            const match = line.match(/^(\s*)((?:[-*+])|(?:\d+[.)]))\s+(\[[ xX]\]\s+)?(.*)$/);
            if (!match) return false;

            const [, indent, marker, taskMarker = '', rest] = match;
            if (!rest.trim()) {
                sourceEditor.value = value.slice(0, lineStart) + value.slice(start);
                sourceEditor.selectionStart = sourceEditor.selectionEnd = lineStart;
                syncFromSource();
                refreshSourceGutter();
                return true;
            }

            const nextMarker = /^\d/.test(marker)
                ? String(Number(marker.match(/\d+/)?.[0] || '1') + 1) + (marker.endsWith(')') ? ')' : '.')
                : marker;
            insertSourceText('\n' + indent + nextMarker + ' ' + taskMarker);
            return true;
        }

        function updateBracketFeedback() {
            if (sourceCodeMirror) return;
            const pairs = { '(': ')', '[': ']', '{': '}', ')': '(', ']': '[', '}': '{' };
            const closers = new Set([')', ']', '}']);
            const cursor = sourceEditor.selectionStart || 0;
            const value = sourceEditor.value;
            const char = value[cursor - 1] && pairs[value[cursor - 1]] ? value[cursor - 1] : value[cursor];
            sourceShell.classList.remove('has-bracket-match', 'has-bracket-mismatch');
            if (!char || !pairs[char]) return;

            const forward = !closers.has(char);
            const open = forward ? char : pairs[char];
            const close = forward ? pairs[char] : char;
            let depth = 0;
            let matched = false;
            const startIndex = forward ? cursor : cursor - 2;
            for (let index = startIndex; forward ? index < value.length : index >= 0; index += forward ? 1 : -1) {
                const current = value[index];
                if (current === char && index === cursor - 1) continue;
                if (current === (forward ? open : close)) depth += 1;
                if (current === (forward ? close : open)) {
                    if (depth === 0) {
                        matched = true;
                        break;
                    }
                    depth -= 1;
                }
            }
            sourceShell.classList.add(matched ? 'has-bracket-match' : 'has-bracket-mismatch');
        }

        function initCodeMirrorSource() {
            if (typeof window.CodeMirror !== 'function') {
                refreshSourceGutter();
                return;
            }
            sourceShell.classList.add('uses-codemirror');
            sourceCodeMirror = window.CodeMirror.fromTextArea(sourceEditor, {
                mode: 'markdown',
                lineNumbers: true,
                lineWrapping: true,
                autoCloseBrackets: true,
                matchBrackets: true,
                smartIndent: true,
                indentUnit: 2,
                tabSize: 2,
                extraKeys: {
                    Enter: 'newlineAndIndentContinueMarkdownList',
                    Tab: (cm) => cm.execCommand('insertSoftTab'),
                    'Cmd-Y': redo,
                    'Ctrl-Y': redo,
                },
            });
            sourceCodeMirror.on('change', syncFromSource);
            sourceCodeMirror.on('cursorActivity', () => {
                if (activeView === 'source') updateReferenceToolbar();
            });
            sourceCodeMirror.on('scroll', hideReferenceToolbar);
            cleanupFns.push(() => {
                if (sourceCodeMirror) {
                    sourceCodeMirror.toTextArea();
                    sourceCodeMirror = null;
                }
            });
        }

        function notifyChange(nextMarkdown, force = false) {
            const normalized = String(nextMarkdown ?? '');
            if (!force && normalized === currentMarkdown) return;
            currentMarkdown = normalized;
            if (typeof options.onChange === 'function') options.onChange(currentMarkdown);
        }

        function syncFromRich() {
            const markdown = htmlToMarkdown(editor.innerHTML);
            setSourceValue(markdown);
            notifyChange(markdown);
        }

        function syncFromSource() {
            const markdown = getSourceValue();
            notifyChange(markdown);
            refreshSourceGutter();
        }

        function saveSelection() {
            const selection = window.getSelection();
            if (selection && selection.rangeCount && editor.contains(selection.anchorNode)) {
                savedRange = selection.getRangeAt(0).cloneRange();
            }
            return savedRange;
        }

        function restoreSelection() {
            if (!savedRange || !editable) return false;
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(savedRange);
            return true;
        }

        function focusEditor() {
            if (!editable) return;
            editor.focus({ preventScroll: true });
        }

        function selectionInEditor() {
            const selection = window.getSelection();
            return Boolean(selection && selection.rangeCount && editor.contains(selection.anchorNode));
        }

        function currentBlock() {
            const selection = window.getSelection();
            let node = selection?.anchorNode || null;
            if (node && node.nodeType === Node.TEXT_NODE) node = node.parentNode;
            while (node && node !== editor && node.parentNode !== editor) node = node.parentNode;
            return node === editor ? null : node;
        }

        function closestInEditor(selector) {
            const selection = window.getSelection();
            let node = selection?.anchorNode || null;
            if (node && node.nodeType === Node.TEXT_NODE) node = node.parentNode;
            const match = node?.closest?.(selector);
            return match && editor.contains(match) ? match : null;
        }

        function caretToEnd(node) {
            if (!node) return;
            const range = document.createRange();
            range.selectNodeContents(node);
            range.collapse(false);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }

        function recordHistory(force = false) {
            if (history.suspended || activeView !== 'editor') return;
            const html = getEditorHistoryHtml(editor);
            if (!force && history.stack[history.index] === html) return;
            history.stack = history.stack.slice(0, history.index + 1);
            history.stack.push(html);
            if (history.stack.length > 120) history.stack.shift();
            history.index = history.stack.length - 1;
            updateHistoryButtons();
        }

        function restoreHistory(html) {
            history.suspended = true;
            cleanupEditorCodeBlockPreviews(editor);
            editor.innerHTML = html;
            hydrateEditorCodeBlocks(editor);
            renderMermaidBlocksInEditor();
            history.suspended = false;
            updatePlaceholder();
            syncFromRich();
            const last = editor.lastElementChild || editor;
            focusEditor();
            caretToEnd(last);
        }

        function undo() {
            if (activeView === 'source') {
                if (sourceCodeMirror) {
                    sourceCodeMirror.undo();
                } else {
                    focusSourceEditor();
                    document.execCommand('undo');
                }
                syncFromSource();
                return;
            }
            if (history.index > 0) {
                history.index -= 1;
                restoreHistory(history.stack[history.index]);
            }
            updateHistoryButtons();
        }

        function redo() {
            if (activeView === 'source') {
                if (sourceCodeMirror) {
                    sourceCodeMirror.redo();
                } else {
                    focusSourceEditor();
                    document.execCommand('redo');
                }
                syncFromSource();
                return;
            }
            if (history.index < history.stack.length - 1) {
                history.index += 1;
                restoreHistory(history.stack[history.index]);
            }
            updateHistoryButtons();
        }

        function updateHistoryButtons() {
            if (typeof options.onStateChange === 'function') {
                options.onStateChange(getState());
            }
        }

        function getState() {
            return {
                view: activeView,
                canUndo: editable && (activeView === 'source' || history.index > 0),
                canRedo: editable && (activeView === 'source' || history.index < history.stack.length - 1),
            };
        }

        function afterEdit(forceHistory = true) {
            updatePlaceholder();
            recordHistory(forceHistory);
            syncFromRich();
            updateToolbarState();
        }

        function exec(command, value = null, useCss = false) {
            if (!editable) return;
            focusEditor();
            restoreSelection();
            try {
                document.execCommand('styleWithCSS', false, Boolean(useCss));
                document.execCommand(command, false, value);
            } catch (_) {
                // execCommand is still the most practical browser primitive for contenteditable editing.
            }
            afterEdit(true);
        }

        function setBlock(tagName) {
            if (tagName === 'PRE') {
                makeCodeBlock();
                return;
            }
            exec('formatBlock', tagName);
        }

        function toggleWrap(tagName) {
            focusEditor();
            restoreSelection();
            const existing = closestInEditor(tagName);
            if (existing) {
                const parent = existing.parentNode;
                while (existing.firstChild) parent.insertBefore(existing.firstChild, existing);
                existing.remove();
                afterEdit(true);
                return;
            }
            const selection = window.getSelection();
            if (!selection || !selection.rangeCount || selection.isCollapsed) return;
            const range = selection.getRangeAt(0);
            const wrapped = document.createElement(tagName);
            wrapped.appendChild(range.extractContents());
            range.insertNode(wrapped);
            caretToEnd(wrapped);
            afterEdit(true);
        }

        function makeCodeBlock() {
            focusEditor();
            restoreSelection();
            const block = currentBlock();
            const pre = document.createElement('pre');
            const code = document.createElement('code');
            code.textContent = block ? block.textContent : '';
            pre.appendChild(code);
            if (block) block.replaceWith(pre);
            else editor.appendChild(pre);
            caretToEnd(code);
            afterEdit(true);
        }

        function toggleInlineCode() {
            focusEditor();
            restoreSelection();
            const existing = closestInEditor('code');
            if (existing && !existing.closest('pre')) {
                const parent = existing.parentNode;
                while (existing.firstChild) parent.insertBefore(existing.firstChild, existing);
                existing.remove();
                afterEdit(true);
                return;
            }
            const selection = window.getSelection();
            if (!selection || !selection.rangeCount || selection.isCollapsed) return;
            const range = selection.getRangeAt(0);
            const code = document.createElement('code');
            code.appendChild(range.extractContents());
            range.insertNode(code);
            caretToEnd(code);
            afterEdit(true);
        }

        function toggleTaskList() {
            focusEditor();
            restoreSelection();
            document.execCommand('insertUnorderedList');
            const li = closestInEditor('li');
            if (li) {
                const list = li.closest('ul');
                if (list) list.classList.add('task-list');
                li.classList.add('task-list-item');
                if (!li.querySelector(':scope > input[type="checkbox"]')) {
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    li.insertBefore(checkbox, li.firstChild);
                }
            }
            afterEdit(true);
        }

        function insertDivider() {
            focusEditor();
            restoreSelection();
            document.execCommand('insertHTML', false, '<hr><p><br></p>');
            afterEdit(true);
        }

        function clearFormatting() {
            focusEditor();
            restoreSelection();
            try {
                document.execCommand('removeFormat', false, null);
            } catch (_) {}
            const selection = window.getSelection();
            if (selection?.rangeCount && !selection.isCollapsed) {
                const range = selection.getRangeAt(0);
                const scope = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
                    ? range.commonAncestorContainer
                    : range.commonAncestorContainer.parentElement;
                scope?.querySelectorAll?.('code, mark, sup, sub, u, span[style]').forEach((node) => {
                    if (!range.intersectsNode(node)) return;
                    if (node.matches('span[style]')) {
                        node.removeAttribute('style');
                        return;
                    }
                    const parent = node.parentNode;
                    while (node.firstChild) parent.insertBefore(node.firstChild, node);
                    node.remove();
                });
            }
            afterEdit(true);
        }

        function setAlign(direction) {
            const command = {
                left: 'justifyLeft',
                center: 'justifyCenter',
                right: 'justifyRight',
                justify: 'justifyFull',
            }[direction] || 'justifyLeft';
            exec(command, null, true);
        }

        function applyColor(color, highlight = false) {
            focusEditor();
            restoreSelection();
            try {
                document.execCommand('styleWithCSS', false, true);
                document.execCommand(highlight ? 'hiliteColor' : 'foreColor', false, color);
            } catch (_) {
                if (highlight) document.execCommand('backColor', false, color);
            }
            afterEdit(true);
        }

        function queryCommandActive(command) {
            try {
                return document.queryCommandState(command);
            } catch (_) {
                return false;
            }
        }

        function captureFormatting() {
            if (!selectionInEditor()) return;
            let node = window.getSelection()?.anchorNode || null;
            if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
            const styles = node ? window.getComputedStyle(node) : null;
            paintFormat = {
                bold: queryCommandActive('bold'),
                italic: queryCommandActive('italic'),
                underline: queryCommandActive('underline'),
                strike: queryCommandActive('strikeThrough'),
                color: styles?.color || '',
                background: styles?.backgroundColor && styles.backgroundColor !== 'rgba(0, 0, 0, 0)' ? styles.backgroundColor : '',
            };
            shell.classList.add('is-format-painting');
        }

        function applyCapturedFormatting() {
            if (!paintFormat) return;
            const selection = window.getSelection();
            if (!selection?.rangeCount || selection.isCollapsed) return;
            focusEditor();
            restoreSelection();
            if (paintFormat.bold && !queryCommandActive('bold')) document.execCommand('bold');
            if (paintFormat.italic && !queryCommandActive('italic')) document.execCommand('italic');
            if (paintFormat.underline && !queryCommandActive('underline')) document.execCommand('underline');
            if (paintFormat.strike && !queryCommandActive('strikeThrough')) document.execCommand('strikeThrough');
            document.execCommand('styleWithCSS', false, true);
            if (paintFormat.color) document.execCommand('foreColor', false, paintFormat.color);
            if (paintFormat.background) {
                try { document.execCommand('hiliteColor', false, paintFormat.background); }
                catch (_) { document.execCommand('backColor', false, paintFormat.background); }
            }
            paintFormat = null;
            shell.classList.remove('is-format-painting');
            afterEdit(true);
        }

        function insertTable(rows = 3, cols = 3) {
            const wrapper = createElement('div', 'canvas-md-editor-table-wrap');
            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');
            for (let col = 0; col < cols; col += 1) {
                const th = document.createElement('th');
                th.textContent = mdEditorT('markdown_editor_table_header', 'Header') + ' ' + (col + 1);
                headerRow.appendChild(th);
            }
            thead.appendChild(headerRow);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (let row = 1; row < rows; row += 1) {
                const tr = document.createElement('tr');
                for (let col = 0; col < cols; col += 1) {
                    const td = document.createElement('td');
                    td.innerHTML = '<br>';
                    tr.appendChild(td);
                }
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            wrapper.appendChild(table);
            focusEditor();
            restoreSelection();
            const block = currentBlock();
            const trailing = document.createElement('p');
            trailing.innerHTML = '<br>';
            if (block && block.tagName === 'P' && !block.textContent.trim()) block.replaceWith(wrapper);
            else if (block) block.after(wrapper);
            else editor.appendChild(wrapper);
            wrapper.after(trailing);
            const firstCell = wrapper.querySelector('th,td');
            if (firstCell) {
                const range = document.createRange();
                range.selectNodeContents(firstCell);
                range.collapse(true);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
            }
            afterEdit(true);
            updateTableToolbar();
        }

        function activeCell() {
            return closestInEditor('td,th');
        }

        function activeTable() {
            return closestInEditor('table');
        }

        function moveTableCell(cell, backward = false) {
            const table = activeTable();
            if (!cell || !table) return;
            const cells = Array.from(table.querySelectorAll('th,td'));
            const index = cells.indexOf(cell);
            let target = cells[index + (backward ? -1 : 1)];
            if (!target && !backward) {
                addRow(true);
                const lastRow = table.rows[table.rows.length - 1];
                target = lastRow?.cells?.[0] || null;
            }
            if (!target) return;
            const range = document.createRange();
            range.selectNodeContents(target);
            range.collapse(true);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            saveSelection();
            updateToolbarState();
        }

        function tableCell(header = false) {
            const cell = document.createElement(header ? 'th' : 'td');
            cell.innerHTML = '<br>';
            return cell;
        }

        function addRow(below) {
            const cell = activeCell();
            const table = activeTable();
            if (!cell || !table) return;
            const row = cell.parentElement;
            const cols = table.rows[0]?.cells.length || 1;
            const nextRow = document.createElement('tr');
            for (let i = 0; i < cols; i += 1) nextRow.appendChild(tableCell(false));
            const tbody = table.tBodies[0] || table.appendChild(document.createElement('tbody'));
            if (row.parentElement.tagName === 'THEAD') tbody.insertBefore(nextRow, tbody.firstChild);
            else row.parentElement.insertBefore(nextRow, below ? row.nextSibling : row);
            afterEdit(true);
        }

        function addColumn(after) {
            const cell = activeCell();
            const table = activeTable();
            if (!cell || !table) return;
            const index = cell.cellIndex;
            Array.from(table.rows).forEach((row) => {
                const header = row.parentElement.tagName === 'THEAD' || row.cells[0]?.tagName === 'TH';
                const newCell = tableCell(header);
                row.insertBefore(newCell, after ? row.cells[index]?.nextSibling || null : row.cells[index]);
            });
            afterEdit(true);
        }

        function deleteRow() {
            const cell = activeCell();
            const table = activeTable();
            if (!cell || !table) return;
            if (table.rows.length <= 1) {
                deleteTable();
                return;
            }
            cell.parentElement.remove();
            afterEdit(true);
            updateTableToolbar();
        }

        function deleteColumn() {
            const cell = activeCell();
            const table = activeTable();
            if (!cell || !table) return;
            const index = cell.cellIndex;
            if ((table.rows[0]?.cells.length || 0) <= 1) {
                deleteTable();
                return;
            }
            Array.from(table.rows).forEach((row) => row.cells[index]?.remove());
            afterEdit(true);
            updateTableToolbar();
        }

        function alignTableColumn(direction) {
            const cell = activeCell();
            const table = activeTable();
            if (!cell || !table) return;
            const index = cell.cellIndex;
            Array.from(table.rows).forEach((row) => {
                const target = row.cells[index];
                if (!target) return;
                target.style.textAlign = direction;
                target.setAttribute('align', direction);
            });
            afterEdit(true);
            updateTableToolbar();
        }

        function toggleHeaderRow() {
            const table = activeTable();
            if (!table) return;
            const thead = table.tHead;
            if (thead) {
                const headerRow = thead.rows[0];
                const body = table.tBodies[0] || table.appendChild(document.createElement('tbody'));
                const replacement = document.createElement('tr');
                Array.from(headerRow?.cells || []).forEach((headerCell) => {
                    const cell = tableCell(false);
                    cell.innerHTML = headerCell.innerHTML;
                    cell.style.textAlign = headerCell.style.textAlign || '';
                    replacement.appendChild(cell);
                });
                body.insertBefore(replacement, body.firstChild);
                thead.remove();
            } else {
                const firstRow = table.rows[0];
                if (!firstRow) return;
                const header = document.createElement('thead');
                const headerRow = document.createElement('tr');
                Array.from(firstRow.cells || []).forEach((cell) => {
                    const th = tableCell(true);
                    th.innerHTML = cell.innerHTML;
                    th.style.textAlign = cell.style.textAlign || '';
                    headerRow.appendChild(th);
                });
                header.appendChild(headerRow);
                table.insertBefore(header, table.firstChild);
                firstRow.remove();
            }
            afterEdit(true);
            updateTableToolbar();
        }

        function clearTableCell() {
            const cell = activeCell();
            if (!cell) return;
            cell.innerHTML = '<br>';
            afterEdit(true);
        }

        function deleteTable() {
            const table = activeTable();
            if (!table) return;
            const wrapper = table.closest('.canvas-md-editor-table-wrap') || table;
            const next = wrapper.nextElementSibling;
            wrapper.remove();
            if (next) caretToEnd(next);
            afterEdit(true);
            updateTableToolbar();
        }

        function updateTableToolbar() {
            const inTable = activeView === 'editor' && Boolean(activeTable());
            tableToolbar.classList.toggle('is-visible', inTable && editable);
            if (lastActiveCell) {
                lastActiveCell.classList.remove('cell-active');
                lastActiveCell = null;
            }
            if (inTable) {
                const cell = activeCell();
                if (cell) {
                    cell.classList.add('cell-active');
                    lastActiveCell = cell;
                }
            }
        }

        function openMenu(anchor, build) {
            closeMenus();
            saveSelection();
            const menu = createElement('div', 'canvas-md-editor-menu');
            menu.setAttribute('role', 'menu');
            build(menu);
            document.body.appendChild(menu);
            const rect = anchor.getBoundingClientRect();
            const left = Math.min(Math.max(rect.left, 8), window.innerWidth - menu.offsetWidth - 8);
            const top = rect.bottom + menu.offsetHeight + 8 > window.innerHeight
                ? Math.max(8, rect.top - menu.offsetHeight - 6)
                : rect.bottom + 6;
            menu.style.left = left + 'px';
            menu.style.top = top + 'px';
            const close = (event) => {
                if (event.type === 'keydown' && event.key !== 'Escape') return;
                if (event.type === 'mousedown' && menu.contains(event.target)) return;
                closeMenus();
            };
            document.addEventListener('mousedown', close, true);
            document.addEventListener('keydown', close, true);
            menu._cleanup = () => {
                document.removeEventListener('mousedown', close, true);
                document.removeEventListener('keydown', close, true);
            };
        }

        function menuLabel(text) {
            return createElement('div', 'canvas-md-editor-menu-label', text);
        }

        function menuSeparator() {
            return createElement('div', 'canvas-md-editor-menu-separator');
        }

        function menuItem(label, iconName, action, options = {}) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'canvas-md-editor-menu-item';
            button.setAttribute('role', 'menuitem');
            if (options.disabled) {
                button.disabled = true;
                button.setAttribute('aria-disabled', 'true');
            }
            const iconMarkup = options.iconHtml || (iconName ? icon(iconName) : '');
            button.innerHTML = iconMarkup + '<span>' + escapeHtml(label) + '</span>';
            button.addEventListener('click', () => {
                if (button.disabled) return;
                closeMenus();
                restoreSelection();
                action();
            });
            return button;
        }

        function closeMenus() {
            document.querySelectorAll('.canvas-md-editor-menu').forEach((menu) => {
                if (typeof menu._cleanup === 'function') menu._cleanup();
                menu.remove();
            });
        }

        function getBlockLabel(tagName) {
            const labels = {
                P: mdEditorT('markdown_editor_paragraph', 'Paragraph'),
                H1: mdEditorT('markdown_editor_slash_heading_1', 'Heading 1'),
                H2: mdEditorT('markdown_editor_slash_heading_2', 'Heading 2'),
                H3: mdEditorT('markdown_editor_slash_heading_3', 'Heading 3'),
                H4: mdEditorT('markdown_editor_heading_4', 'Heading 4'),
                H5: mdEditorT('markdown_editor_heading_5', 'Heading 5'),
                H6: mdEditorT('markdown_editor_heading_6', 'Heading 6'),
                BLOCKQUOTE: mdEditorT('markdown_editor_slash_quote', 'Quote'),
                PRE: mdEditorT('markdown_editor_code_block', 'Code block'),
            };
            return labels[tagName] || labels.P;
        }

        function openBlockMenu(anchor) {
            openMenu(anchor, (menu) => {
                ['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6'].forEach((tagName) => {
                    menu.appendChild(menuItem(getBlockLabel(tagName), null, () => setBlock(tagName)));
                });
                menu.appendChild(menuSeparator());
                menu.appendChild(menuItem(mdEditorT('markdown_editor_slash_quote', 'Quote'), 'quote', () => exec('formatBlock', 'BLOCKQUOTE')));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_code_block', 'Code block'), 'code', () => makeCodeBlock()));
            });
        }

        function openColorMenu(anchor, highlight = false) {
            openMenu(anchor, (menu) => {
                menu.appendChild(menuLabel(highlight ? mdEditorT('markdown_editor_highlight_color', 'Highlight color') : mdEditorT('markdown_editor_text_color', 'Text color')));
                const swatches = createElement('div', 'canvas-md-editor-swatches');
                const clear = createElement('button', 'canvas-md-editor-swatch none');
                clear.type = 'button';
                clear.title = mdEditorT('markdown_editor_clear_color', 'Clear color');
                clear.innerHTML = icon('clear');
                clear.addEventListener('click', () => {
                    closeMenus();
                    applyColor(highlight ? 'transparent' : 'inherit', highlight);
                });
                swatches.appendChild(clear);
                (highlight ? HIGHLIGHT_COLORS : TEXT_COLORS).forEach((color) => {
                    const swatch = createElement('button', 'canvas-md-editor-swatch');
                    swatch.type = 'button';
                    swatch.title = color;
                    swatch.style.background = color;
                    swatch.addEventListener('click', () => {
                        closeMenus();
                        applyColor(color, highlight);
                    });
                    swatches.appendChild(swatch);
                });
                menu.appendChild(swatches);
            });
        }

        function openAlignMenu(anchor) {
            openMenu(anchor, (menu) => {
                [
                    ['left', mdEditorT('markdown_editor_align_left', 'Left'), 'alignLeft'],
                    ['center', mdEditorT('markdown_editor_align_center', 'Center'), 'alignCenter'],
                    ['right', mdEditorT('markdown_editor_align_right', 'Right'), 'alignRight'],
                    ['justify', mdEditorT('markdown_editor_align_justify', 'Justify'), 'alignJustify'],
                ].forEach(([direction, label, iconName]) => {
                    menu.appendChild(menuItem(label, iconName, () => setAlign(direction)));
                });
            });
        }

        function openTableMenu(anchor) {
            openMenu(anchor, (menu) => {
                menu.appendChild(menuLabel(mdEditorT('markdown_editor_insert_table', 'Insert table')));
                const size = createElement('div', 'canvas-md-editor-grid-size', '1 x 1');
                const grid = createElement('div', 'canvas-md-editor-grid-pick');
                const cells = [];
                for (let row = 0; row < 8; row += 1) {
                    for (let col = 0; col < 10; col += 1) {
                        const cell = createElement('button', 'canvas-md-editor-grid-cell');
                        cell.type = 'button';
                        cell.dataset.row = String(row);
                        cell.dataset.col = String(col);
                        const paint = () => {
                            size.textContent = (row + 1) + ' x ' + (col + 1);
                            cells.forEach((candidate) => {
                                candidate.classList.toggle('on', Number(candidate.dataset.row) <= row && Number(candidate.dataset.col) <= col);
                            });
                        };
                        cell.addEventListener('mouseenter', paint);
                        cell.addEventListener('focus', paint);
                        cell.addEventListener('click', () => {
                            closeMenus();
                            insertTable(row + 1, col + 1);
                        });
                        cells.push(cell);
                        grid.appendChild(cell);
                    }
                }
                menu.append(grid, size, menuSeparator(), menuItem(mdEditorT('markdown_editor_insert_default_table', 'Insert 3 x 3 table'), 'table', () => insertTable(3, 3)));
            });
        }

        function isCompactEditorChrome() {
            return shell.getBoundingClientRect().width <= 600;
        }

        function openMoreMenu(anchor) {
            openMenu(anchor, (menu) => {
                if (isCompactEditorChrome()) {
                    const historyState = getState();
                    menu.appendChild(menuLabel(mdEditorT('markdown_editor_history', 'History')));
                    menu.appendChild(menuItem(mdEditorT('markdown_editor_undo', 'Undo'), 'undo', () => undo(), {
                        disabled: !historyState.canUndo,
                    }));
                    menu.appendChild(menuItem(mdEditorT('markdown_editor_redo', 'Redo'), 'redo', () => redo(), {
                        disabled: !historyState.canRedo,
                    }));
                    menu.appendChild(menuSeparator());
                }
                menu.appendChild(menuLabel(mdEditorT('markdown_editor_format', 'Format')));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_superscript', 'Superscript'), 'sup', () => toggleWrap('sup')));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_subscript', 'Subscript'), 'sub', () => toggleWrap('sub')));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_clear_formatting', 'Clear formatting'), 'clear', () => clearFormatting()));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_copy_formatting', 'Copy formatting'), 'paint', () => captureFormatting()));
                menu.appendChild(menuSeparator());
                menu.appendChild(menuLabel(mdEditorT('markdown_editor_insert', 'Insert')));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_slash_divider', 'Divider'), 'divider', () => insertDivider()));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_slash_quote', 'Quote'), 'quote', () => exec('formatBlock', 'BLOCKQUOTE')));
                menu.appendChild(menuItem(mdEditorT('markdown_editor_code_block', 'Code block'), 'code', () => makeCodeBlock()));
                const customActions = Array.isArray(options.moreActions)
                    ? options.moreActions.filter((action) => action && typeof action.onSelect === 'function' && String(action.label || '').trim())
                    : [];
                if (customActions.length) {
                    menu.appendChild(menuSeparator());
                    customActions.forEach((action) => {
                        menu.appendChild(menuItem(action.label, action.iconName || null, () => action.onSelect(), {
                            disabled: Boolean(action.disabled),
                            iconHtml: action.iconHtml || '',
                        }));
                    });
                }
            });
        }

        function openTableAlignMenu(anchor) {
            openMenu(anchor, (menu) => {
                [
                    ['left', mdEditorT('markdown_editor_align_left', 'Left'), 'alignLeft'],
                    ['center', mdEditorT('markdown_editor_align_center', 'Center'), 'alignCenter'],
                    ['right', mdEditorT('markdown_editor_align_right', 'Right'), 'alignRight'],
                ].forEach(([direction, label, iconName]) => {
                    menu.appendChild(menuItem(label, iconName, () => alignTableColumn(direction)));
                });
            });
        }

        function openDialog({ title, confirmLabel, extraButtons = [], build, onConfirm }) {
            const overlay = createElement('div', 'canvas-md-dialog-overlay shared-modal-overlay');
            overlay.setAttribute('aria-hidden', 'false');
            const dialog = createElement('div', 'canvas-md-dialog shared-modal shared-modal--compact shared-modal--fit');
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.setAttribute('aria-label', title);
            dialog.tabIndex = -1;
            const header = createElement('header', 'canvas-md-dialog-header shared-modal-header shared-modal-header--main');
            const heading = createElement('h2', 'canvas-md-dialog-title shared-modal-title', title);
            header.appendChild(heading);
            const bodyEl = createElement('div', 'canvas-md-dialog-body shared-modal-body');
            const footer = createElement('footer', 'canvas-md-dialog-footer shared-modal-footer');
            const cancelBtn = createElement('button', 'canvas-md-dialog-btn om-button border cancel', mdEditorT('markdown_editor_cancel', 'Cancel'));
            cancelBtn.type = 'button';
            const okBtn = createElement('button', 'canvas-md-dialog-btn primary om-button border submit', confirmLabel || mdEditorT('markdown_editor_insert', 'Insert'));
            okBtn.type = 'button';
            extraButtons.forEach((button) => footer.appendChild(button));
            footer.append(cancelBtn, okBtn);
            dialog.append(header, bodyEl, footer);
            overlay.appendChild(dialog);
            const bodyHadModalOpen = document.body.classList.contains('modal-open');
            const inertBackground = Array.from(document.body.children).filter((element) => !element.inert);
            document.body.appendChild(overlay);
            inertBackground.forEach((element) => { element.inert = true; });
            document.body.classList.add('modal-open');
            const api = { close };
            build(bodyEl, api);
            let isClosed = false;
            function close() {
                if (isClosed) return;
                isClosed = true;
                overlay.setAttribute('aria-hidden', 'true');
                overlay.inert = true;
                overlay.remove();
                document.removeEventListener('keydown', onKey, true);
                inertBackground.forEach((element) => {
                    if (element.isConnected) element.inert = false;
                });
                if (!bodyHadModalOpen) document.body.classList.remove('modal-open');
                focusEditor();
                restoreSelection();
            }
            function onKey(event) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    close();
                    return;
                }
                if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA') {
                    event.preventDefault();
                    onConfirm(api);
                    return;
                }
                if (event.key === 'Tab') {
                    const focusable = Array.from(dialog.querySelectorAll(
                        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
                    )).filter((element) => !element.hidden && element.getClientRects().length > 0);
                    if (!focusable.length) {
                        event.preventDefault();
                        dialog.focus({ preventScroll: true });
                        return;
                    }
                    const first = focusable[0];
                    const last = focusable[focusable.length - 1];
                    if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                        event.preventDefault();
                        last.focus();
                    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                        event.preventDefault();
                        first.focus();
                    }
                }
            }
            cancelBtn.addEventListener('click', close);
            okBtn.addEventListener('click', () => onConfirm(api));
            overlay.addEventListener('mousedown', (event) => {
                if (event.target === overlay) close();
            });
            document.addEventListener('keydown', onKey, true);
            setTimeout(() => bodyEl.querySelector('input,textarea,select')?.focus(), 0);
            return api;
        }

        function dialogField(label, input, tip = '') {
            const wrapper = createElement('label', 'canvas-md-dialog-field');
            const span = createElement('span', '', label);
            wrapper.append(span, input);
            if (tip) wrapper.appendChild(createElement('small', 'canvas-md-dialog-tip', tip));
            return wrapper;
        }

        function unwrapLink(anchor) {
            if (!anchor?.parentNode) return;
            const parent = anchor.parentNode;
            while (anchor.firstChild) parent.insertBefore(anchor.firstChild, anchor);
            anchor.remove();
            afterEdit(true);
        }

        function setAnchorHref(anchor, href) {
            const fileId = getOmlorixFileIdFromUrl(href);
            if (fileId) {
                anchor.setAttribute('data-omlorix-file-id', fileId);
                anchor.href = buildOmlorixFileDownloadUrl(fileId);
                return;
            }
            anchor.removeAttribute('data-omlorix-file-id');
            anchor.href = href;
        }

        function applyLink(existing, href, text, title) {
            focusEditor();
            restoreSelection();
            if (existing) {
                setAnchorHref(existing, href);
                existing.setAttribute('rel', 'noopener noreferrer nofollow');
                existing.setAttribute('target', '_blank');
                existing.textContent = text;
                if (title) existing.setAttribute('title', title);
                else existing.removeAttribute('title');
                afterEdit(true);
                return;
            }
            const anchor = document.createElement('a');
            setAnchorHref(anchor, href);
            anchor.textContent = text;
            anchor.rel = 'noopener noreferrer nofollow';
            anchor.target = '_blank';
            if (title) anchor.title = title;
            const selection = window.getSelection();
            if (selection?.rangeCount) {
                const range = selection.getRangeAt(0);
                range.deleteContents();
                range.insertNode(anchor);
                caretToEnd(anchor);
            } else {
                editor.appendChild(anchor);
                caretToEnd(anchor);
            }
            afterEdit(true);
        }

        function openLinkDialog(existingOverride = null) {
            saveSelection();
            const existing = existingOverride || closestInEditor('a');
            const selectionText = window.getSelection()?.toString?.() || '';
            const textInput = document.createElement('input');
            textInput.type = 'text';
            textInput.value = existing ? existing.textContent : selectionText;
            const urlInput = document.createElement('input');
            urlInput.type = 'text';
            urlInput.placeholder = 'https://example.com';
            urlInput.value = existing
                ? (buildOmlorixFileUrl(getOmlorixFileIdFromUrl(existing.getAttribute('data-omlorix-file-id') || existing.getAttribute('href') || '')) || existing.getAttribute('href') || '')
                : '';
            const titleInput = document.createElement('input');
            titleInput.type = 'text';
            titleInput.value = existing ? existing.getAttribute('title') || '' : '';
            const extraButtons = [];
            let dialogApi = null;
            if (existing) {
                const removeBtn = createElement('button', 'canvas-md-dialog-btn om-button border danger', mdEditorT('markdown_editor_remove_link', 'Remove link'));
                removeBtn.type = 'button';
                removeBtn.addEventListener('click', () => {
                    unwrapLink(existing);
                    dialogApi?.close();
                });
                extraButtons.push(removeBtn);
            }
            dialogApi = openDialog({
                title: existing ? mdEditorT('markdown_editor_edit_link', 'Edit link') : mdEditorT('markdown_editor_insert_link', 'Insert link'),
                confirmLabel: existing ? mdEditorT('markdown_editor_save', 'Save') : mdEditorT('markdown_editor_insert', 'Insert'),
                extraButtons,
                build: (bodyEl) => {
                    bodyEl.append(
                        dialogField(mdEditorT('markdown_editor_text_label', 'Text'), textInput),
                        dialogField(mdEditorT('markdown_editor_url_label', 'URL'), urlInput),
                        dialogField(mdEditorT('markdown_editor_link_title_label', 'Title (optional)'), titleInput),
                    );
                },
                onConfirm: (api) => {
                    const href = safeUrl(urlInput.value) || buildOmlorixFileUrl(getOmlorixFileIdFromUrl(urlInput.value));
                    if (!href) {
                        urlInput.focus();
                        return;
                    }
                    applyLink(existing, href, textInput.value.trim() || selectionText || href, titleInput.value.trim());
                    api.close();
                },
            });
        }

        function isImageFile(file) {
            return Boolean(file && /^image\//i.test(file.type || ''));
        }

        async function uploadImageFile(file) {
            if (!isImageFile(file)) {
                throw new Error('not-image');
            }
            if (typeof window.authedFetch !== 'function') {
                throw new Error('upload-unavailable');
            }
            const formData = new FormData();
            formData.append('file', file, file.name || 'image');
            const response = await window.authedFetch('/api/v1/files/upload', {
                method: 'POST',
                body: formData,
            });
            const payload = await response.json().catch(() => null);
            if (!response.ok || payload?.status !== 'success' || !payload.file_id) {
                throw new Error(payload?.detail || payload?.message || response.statusText || 'upload-failed');
            }
            return {
                fileId: String(payload.file_id),
                src: buildOmlorixFileUrl(payload.file_id),
                alreadyUploaded: Boolean(payload.already_uploaded),
            };
        }

        function setImageSource(image, src) {
            const fileId = getOmlorixFileIdFromUrl(src);
            if (fileId) {
                image.setAttribute('data-omlorix-file-id', fileId);
                image.src = buildOmlorixFileDownloadUrl(fileId);
                return;
            }
            image.removeAttribute('data-omlorix-file-id');
            image.src = src;
        }

        function markdownSourceForImage(image) {
            const fileId = getOmlorixFileIdFromUrl(image?.getAttribute?.('data-omlorix-file-id') || image?.getAttribute?.('src') || '');
            if (fileId) return buildOmlorixFileUrl(fileId);
            return image?.getAttribute?.('src') || '';
        }

        function applyImage(existing, src, alt) {
            focusEditor();
            if (!existing) restoreSelection();
            if (existing) {
                setImageSource(existing, src);
                existing.alt = alt;
                afterEdit(true);
                return;
            }
            const image = document.createElement('img');
            setImageSource(image, src);
            image.alt = alt;
            const selection = window.getSelection();
            if (selection?.rangeCount) {
                const range = selection.getRangeAt(0);
                range.collapse(false);
                range.insertNode(image);
                range.setStartAfter(image);
                range.collapse(true);
                selection.removeAllRanges();
                selection.addRange(range);
            } else {
                editor.appendChild(image);
            }
            afterEdit(true);
        }

        function setImageSelected(image) {
            editor.querySelectorAll('img.is-selected').forEach((candidate) => candidate.classList.remove('is-selected'));
            if (image) image.classList.add('is-selected');
        }

        function openImageDialog(existingImage = null) {
            if (existingImage) {
                const range = document.createRange();
                range.selectNode(existingImage);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                saveSelection();
            } else {
                saveSelection();
            }
            let uploadedImageSrc = existingImage ? markdownSourceForImage(existingImage) : '';
            let mode = existingImage && !getOmlorixFileIdFromUrl(uploadedImageSrc) && !/^data:/i.test(uploadedImageSrc) ? 'url' : 'upload';
            const urlInput = document.createElement('input');
            urlInput.type = 'url';
            urlInput.placeholder = 'https://example.com/image.png';
            if (existingImage && /^https?:/i.test(uploadedImageSrc)) urlInput.value = uploadedImageSrc;
            const altInput = document.createElement('input');
            altInput.type = 'text';
            altInput.placeholder = mdEditorT('markdown_editor_image_alt_placeholder', 'Describe the image');
            altInput.value = existingImage ? existingImage.getAttribute('alt') || '' : '';
            const extraButtons = [];
            let activeImageDropzone = null;
            let dialogApi = null;
            if (existingImage) {
                const removeBtn = createElement('button', 'canvas-md-dialog-btn om-button border danger', mdEditorT('markdown_editor_remove_image', 'Remove image'));
                removeBtn.type = 'button';
                removeBtn.addEventListener('click', () => {
                    existingImage.remove();
                    afterEdit(true);
                    dialogApi?.close();
                });
                extraButtons.push(removeBtn);
            }
            dialogApi = openDialog({
                title: existingImage ? mdEditorT('markdown_editor_edit_image', 'Edit image') : mdEditorT('markdown_editor_insert_image', 'Insert image'),
                confirmLabel: existingImage ? mdEditorT('markdown_editor_save', 'Save') : mdEditorT('markdown_editor_insert', 'Insert'),
                extraButtons,
                build: (bodyEl) => {
                    const hasUploadedPicker = typeof options.onSelectUploadedImage === 'function';
                    const segment = createElement('div', 'canvas-md-dialog-segment');
                    const uploadBtn = createElement('button', '', mdEditorT('markdown_editor_image_upload', 'Upload'));
                    const uploadedBtn = hasUploadedPicker
                        ? createElement('button', '', mdEditorT('markdown_editor_image_from_uploads', 'Uploaded'))
                        : null;
                    const urlBtn = createElement('button', '', mdEditorT('markdown_editor_image_from_url', 'From URL'));
                    uploadBtn.type = 'button';
                    if (uploadedBtn) uploadedBtn.type = 'button';
                    urlBtn.type = 'button';
                    segment.append(uploadBtn);
                    if (uploadedBtn) segment.appendChild(uploadedBtn);
                    segment.appendChild(urlBtn);
                    const uploadPane = createElement('div', 'canvas-md-dialog-field');
                    const dropzone = createElement('button', 'canvas-md-dropzone', mdEditorT('markdown_editor_image_dropzone', 'Click to choose an image, or drag it here'));
                    dropzone.type = 'button';
                    activeImageDropzone = dropzone;
                    const fileInput = document.createElement('input');
                    fileInput.type = 'file';
                    fileInput.accept = 'image/*';
                    fileInput.hidden = true;
                    uploadPane.append(dropzone, fileInput);
                    const urlField = dialogField(mdEditorT('markdown_editor_image_url_label', 'Image URL'), urlInput);
                    const altField = dialogField(
                        mdEditorT('markdown_editor_alt_text_label', 'Alt text'),
                        altInput,
                        mdEditorT('markdown_editor_alt_text_help', 'Used by screen readers and shown if the image fails to load.'),
                    );
                    bodyEl.append(segment, uploadPane, urlField, altField);

                    const setMode = (nextMode) => {
                        mode = nextMode;
                        uploadBtn.classList.toggle('is-active', mode === 'upload');
                        uploadedBtn?.classList.remove('is-active');
                        urlBtn.classList.toggle('is-active', mode === 'url');
                        uploadPane.hidden = mode !== 'upload';
                        urlField.hidden = mode !== 'url';
                    };
                    const setFile = async (file) => {
                        if (!isImageFile(file)) return;
                        dropzone.textContent = mdEditorT('markdown_editor_image_uploading', 'Uploading image...');
                        dropzone.disabled = true;
                        try {
                            const uploaded = await uploadImageFile(file);
                            uploadedImageSrc = uploaded.src;
                            dropzone.textContent = mdEditorT('markdown_editor_image_ready', 'Image ready') + ': ' + file.name;
                        } catch (_) {
                            dropzone.textContent = mdEditorT('markdown_editor_image_upload_failed', 'Image upload failed');
                        } finally {
                            dropzone.disabled = false;
                        }
                    };
                    uploadBtn.addEventListener('click', () => setMode('upload'));
                    uploadedBtn?.addEventListener('click', () => {
                        dialogApi?.close();
                        options.onSelectUploadedImage();
                    });
                    urlBtn.addEventListener('click', () => setMode('url'));
                    dropzone.addEventListener('click', () => fileInput.click());
                    fileInput.addEventListener('change', () => setFile(fileInput.files?.[0]));
                    ['dragenter', 'dragover'].forEach((eventName) => {
                        dropzone.addEventListener(eventName, (event) => {
                            event.preventDefault();
                            dropzone.classList.add('is-dragging');
                        });
                    });
                    ['dragleave', 'drop'].forEach((eventName) => {
                        dropzone.addEventListener(eventName, (event) => {
                            event.preventDefault();
                            dropzone.classList.remove('is-dragging');
                        });
                    });
                    dropzone.addEventListener('drop', (event) => setFile(event.dataTransfer?.files?.[0]));
                    setMode(mode);
                },
                onConfirm: (api) => {
                    const src = mode === 'url'
                        ? safeUrl(urlInput.value, true)
                        : (uploadedImageSrc || (existingImage ? markdownSourceForImage(existingImage) : ''));
                    if (!src) {
                        (mode === 'url' ? urlInput : activeImageDropzone)?.focus();
                        return;
                    }
                    applyImage(existingImage, src, altInput.value.trim());
                    api.close();
                },
            });
        }

        function insertImageFile(file) {
            if (!isImageFile(file)) return false;
            uploadImageFile(file)
                .then((uploaded) => {
                    restoreSelection();
                    applyImage(null, uploaded.src, '');
                })
                .catch(() => {
                    if (typeof window.notifyError === 'function') {
                        window.notifyError(mdEditorT('markdown_editor_image_upload_failed', 'Image upload failed'));
                    }
                });
            return true;
        }

        function cleanPastedHtml(html) {
            const cleanedSource = String(html || '')
                .replace(/<!--[\s\S]*?-->/g, '')
                .replace(/<\/?(?:o:p|xml|meta|style|link|title|head)[^>]*>/gi, '');
            const clean = sanitizeEditorHtml(cleanedSource);
            try {
                const markdown = htmlToMarkdown(clean);
                return renderMarkdownToHtml(markdown);
            } catch (_) {
                return clean;
            }
        }

        function insertPlainText(text) {
            const normalized = String(text || '').replace(/\r\n?/g, '\n');
            if (!/\n/.test(normalized)) {
                document.execCommand('insertText', false, normalized);
                afterEdit(true);
                return;
            }
            const html = normalized
                .split(/\n{2,}/)
                .map((paragraph) => '<p>' + escapeHtml(paragraph).replace(/\n/g, '<br>') + '</p>')
                .join('');
            document.execCommand('insertHTML', false, html);
            afterEdit(true);
        }

        function switchView(view, { focus = true } = {}) {
            if (view === activeView) return;
            closeMenus();
            hideReferenceToolbar();
            if (view === 'source') {
                setSourceValue(htmlToMarkdown(editor.innerHTML));
                editorView.hidden = true;
                editorView.classList.remove('is-active');
                sourceView.hidden = false;
                sourceView.classList.add('is-active');
                activeView = 'source';
                refreshSourceEditor();
                if (focus) focusSourceEditor();
            } else {
                replaceEditorMarkdown(editor, getSourceValue());
                renderMermaidBlocksInEditor();
                editorView.hidden = false;
                editorView.classList.add('is-active');
                sourceView.hidden = true;
                sourceView.classList.remove('is-active');
                activeView = 'editor';
                updatePlaceholder();
                recordHistory(true);
                if (focus) focusEditor();
            }
            updateTableToolbar();
            updateHistoryButtons();
        }

        function updatePlaceholder() {
            const empty = !editor.textContent.trim() && !editor.querySelector('img,table,hr,input');
            editor.classList.toggle('is-empty', empty);
        }

        function renderMermaidBlocksInEditor() {
            if (!editor || typeof editor.querySelectorAll !== 'function') return;
            editor.querySelectorAll('.mermaid-block').forEach((block) => {
                const sourceEl = block.querySelector('.mermaid-block-source');
                const previewEl = block.querySelector('.mermaid-diagram');
                if (!sourceEl || !previewEl) return;
                const source = String(sourceEl.textContent || '');
                if (previewEl.dataset.mermaidSource === source) return;
                previewEl.textContent = mdEditorT('code_block_mermaid_rendering', 'Rendering Mermaid diagram...');
                if (typeof window.renderMermaidDiagram === 'function') {
                    if (previewEl._canvasMdMermaidRetryTimer) {
                        window.clearTimeout(previewEl._canvasMdMermaidRetryTimer);
                    }
                    previewEl._canvasMdMermaidRetryPending = false;
                    previewEl._canvasMdMermaidRetryTimer = null;
                    previewEl.dataset.mermaidSource = source;
                    window.renderMermaidDiagram(previewEl, source).catch((error) => {
                        previewEl.classList.add('mermaid-diagram-error');
                        if (typeof window.formatTranslation === 'function') {
                            previewEl.textContent = window.formatTranslation(
                                'code_block_mermaid_render_error',
                                'Mermaid render error: {message}',
                                { message: error?.message || mdEditorT('common_unknown_error', 'Unknown error') },
                            );
                        } else {
                            previewEl.textContent = mdEditorT('code_block_mermaid_render_error', 'Mermaid render error: {message}')
                                .replace('{message}', error?.message || mdEditorT('common_unknown_error', 'Unknown error'));
                        }
                    });
                    return;
                }
                previewEl.classList.add('mermaid-diagram-error');
                previewEl.textContent = mdEditorT('code_block_mermaid_renderer_unavailable', 'Mermaid renderer is unavailable.');
                const retryCount = Number(previewEl._canvasMdMermaidRetryCount || 0);
                if (retryCount < 20 && !previewEl._canvasMdMermaidRetryPending) {
                    previewEl._canvasMdMermaidRetryCount = retryCount + 1;
                    previewEl._canvasMdMermaidRetryPending = true;
                    previewEl._canvasMdMermaidRetryTimer = window.setTimeout(() => {
                        previewEl._canvasMdMermaidRetryPending = false;
                        previewEl._canvasMdMermaidRetryTimer = null;
                        renderMermaidBlocksInEditor();
                    }, 100);
                }
            });
        }

        function updateToolbarState() {
            if (activeView !== 'editor' || !selectionInEditor()) {
                updateTableToolbar();
                return;
            }
            ['bold', 'italic', 'underline', 'strikeThrough', 'insertUnorderedList', 'insertOrderedList'].forEach((command) => {
                const button = toolbar.querySelector('[data-command="' + command + '"]');
                if (button) {
                    let active = false;
                    try { active = document.queryCommandState(command); } catch (_) { active = false; }
                    button.classList.toggle('is-active', active);
                }
            });
            toolbar.querySelector('[data-command="inlineCode"]')?.classList.toggle('is-active', Boolean(closestInEditor('code:not(pre code)')));
            toolbar.querySelector('[data-command="taskList"]')?.classList.toggle('is-active', Boolean(closestInEditor('li.task-list-item')));
            const block = currentBlock();
            const blockTag = closestInEditor('pre') ? 'PRE' : (closestInEditor('blockquote') ? 'BLOCKQUOTE' : (block?.tagName || 'P'));
            const blockLabel = blockSelect.querySelector('.canvas-md-current-block-label');
            if (blockLabel) blockLabel.textContent = getBlockLabel(blockTag);
            updateTableToolbar();
        }

        /** Read a rich-text, CodeMirror, or textarea selection uniformly. */
        function readReferenceSelectionData() {
            if (activeView === 'source') {
                if (sourceCodeMirror?.hasFocus?.()) {
                    const text = String(sourceCodeMirror.getSelection?.() || '').trim();
                    if (!text) return null;
                    const from = sourceCodeMirror.getCursor('from');
                    const to = sourceCodeMirror.getCursor('to');
                    const fromRect = sourceCodeMirror.charCoords(from, 'window');
                    const toRect = sourceCodeMirror.charCoords(to, 'window');
                    const left = Math.min(fromRect.left, toRect.left);
                    const right = Math.max(fromRect.right, toRect.right);
                    const top = Math.min(fromRect.top, toRect.top);
                    const bottom = Math.max(fromRect.bottom, toRect.bottom);
                    return {
                        text,
                        source: 'source',
                        start: sourceCodeMirror.indexFromPos(from),
                        end: sourceCodeMirror.indexFromPos(to),
                        rect: { left, right, top, bottom, width: right - left, height: bottom - top },
                    };
                }

                if (document.activeElement !== sourceEditor) return null;
                const start = Math.min(sourceEditor.selectionStart, sourceEditor.selectionEnd);
                const end = Math.max(sourceEditor.selectionStart, sourceEditor.selectionEnd);
                const text = String(sourceEditor.value || '').slice(start, end).trim();
                if (!text) return null;
                return {
                    text,
                    source: 'source',
                    start,
                    end,
                    // The native source editor is only a fallback when
                    // CodeMirror is unavailable. Centering over the control is
                    // preferable to losing the action entirely.
                    rect: sourceEditor.getBoundingClientRect(),
                };
            }

            if (!selectionInEditor()) return null;
            const selection = window.getSelection();
            const text = String(selection?.toString?.() || '').trim();
            if (!text || !selection.rangeCount) return null;
            const range = selection.getRangeAt(0);
            return {
                text,
                source: 'editor',
                range: range.cloneRange(),
                rect: range.getBoundingClientRect(),
            };
        }

        /** Collapse every supported editor selection after an action. */
        function clearReferenceSelection() {
            if (activeReferenceSelectionData?.source === 'source' && sourceCodeMirror) {
                const end = Number(activeReferenceSelectionData.end);
                sourceCodeMirror.setCursor(
                    Number.isFinite(end) ? sourceCodeMirror.posFromIndex(end) : sourceCodeMirror.getCursor('to')
                );
            } else if (activeReferenceSelectionData?.source === 'source') {
                const end = Number.isFinite(activeReferenceSelectionData.end)
                    ? activeReferenceSelectionData.end
                    : Math.max(sourceEditor.selectionStart, sourceEditor.selectionEnd);
                sourceEditor.setSelectionRange(end, end);
            } else {
                window.getSelection()?.removeAllRanges?.();
            }
            activeReferenceSelectionData = null;
        }

        function hideReferenceToolbar() {
            activeReferenceSelectionData = null;
            referenceToolbarController.hide();
            referenceToolbar.classList.remove('is-below');
        }

        function updateReferenceToolbar() {
            const canReferenceSelection = typeof options.canReferenceSelection !== 'function'
                || options.canReferenceSelection();
            if (!editable || typeof options.onReferenceSelection !== 'function' || !canReferenceSelection) {
                hideReferenceToolbar();
                return;
            }
            const selectionData = readReferenceSelectionData();
            const rect = selectionData?.rect;
            if (!selectionData || !rect || (!rect.width && !rect.height)) {
                hideReferenceToolbar();
                return;
            }
            activeReferenceSelectionData = selectionData;
            const shellRect = shell.getBoundingClientRect();
            referenceToolbarController.updateLabels();
            referenceToolbar.classList.remove('is-below');

            const toolbarRect = referenceToolbarController.measure();
            const toolbarWidth = toolbarRect.width || 0;
            const toolbarHeight = toolbarRect.height || 34;
            const margin = 12;
            const centeredLeft = rect.left - shellRect.left + (rect.width / 2) - (toolbarWidth / 2);
            const maxLeft = Math.max(margin, shellRect.width - toolbarWidth - margin);
            const left = Math.max(margin, Math.min(centeredLeft, maxLeft));
            const topAbove = rect.top - shellRect.top - toolbarHeight - 10;
            const topBelow = rect.bottom - shellRect.top + 10;
            const shouldPlaceBelow = topAbove < margin
                && topBelow + toolbarHeight < shellRect.height - margin;
            const top = shouldPlaceBelow
                ? Math.min(topBelow, shellRect.height - toolbarHeight - margin)
                : Math.max(margin, topAbove);

            referenceToolbar.classList.toggle('is-below', shouldPlaceBelow);
            referenceToolbarController.showAt(left, top);
        }

        addListener(toolbar, 'mousedown', (event) => {
            if (event.target.closest('button')) event.preventDefault();
        });
        addListener(toolbar, 'click', (event) => {
            const button = event.target.closest('[data-command]');
            if (!button || button.disabled) return;
            const command = button.dataset.command;
            if (command === 'inlineCode') toggleInlineCode();
            else if (command === 'taskList') toggleTaskList();
            else if (command === 'quote') exec('formatBlock', 'BLOCKQUOTE');
            else if (command === 'link') openLinkDialog();
            else if (command === 'image') openImageDialog();
            else if (command === 'menu:block') openBlockMenu(button);
            else if (command === 'menu:color') openColorMenu(button, false);
            else if (command === 'menu:highlight') openColorMenu(button, true);
            else if (command === 'menu:align') openAlignMenu(button);
            else if (command === 'menu:table') openTableMenu(button);
            else if (command === 'divider') insertDivider();
            else if (command === 'menu:more') openMoreMenu(button);
            else exec(command);
        });
        addListener(tableToolbar, 'mousedown', (event) => {
            if (event.target.closest('button')) event.preventDefault();
        });
        addListener(tableToolbar, 'click', (event) => {
            const command = event.target.closest('[data-command]')?.dataset.command;
            if (command === 'rowAbove') addRow(false);
            if (command === 'rowBelow') addRow(true);
            if (command === 'colBefore') addColumn(false);
            if (command === 'colAfter') addColumn(true);
            if (command === 'deleteRow') deleteRow();
            if (command === 'deleteColumn') deleteColumn();
            if (command === 'menu:tableAlign') openTableAlignMenu(event.target.closest('[data-command]'));
            if (command === 'toggleHeader') toggleHeaderRow();
            if (command === 'clearCell') clearTableCell();
            if (command === 'deleteTable') deleteTable();
        });
        addListener(editor, 'input', () => afterEdit(false));
        addListener(editor, 'mouseup', () => {
            saveSelection();
            updateToolbarState();
            if (paintFormat) applyCapturedFormatting();
        });
        addListener(editor, 'click', (event) => {
            const checkbox = event.target.closest('input[type="checkbox"]');
            const link = event.target.closest('a');
            const image = event.target.closest('img');
            setImageSelected(image);
            if (link && (event.metaKey || event.ctrlKey)) {
                const href = safeUrl(link.getAttribute('href') || '');
                if (href) window.open(href, '_blank', 'noopener');
                return;
            }
            if (!checkbox) return;
            setTimeout(() => {
                if (checkbox.checked) checkbox.setAttribute('checked', 'checked');
                else checkbox.removeAttribute('checked');
                afterEdit(true);
            }, 0);
        });
        addListener(editor, 'dblclick', (event) => {
            const image = event.target.closest('img');
            if (image && editor.contains(image)) {
                event.preventDefault();
                openImageDialog(image);
                return;
            }
            const link = event.target.closest('a');
            if (link && editor.contains(link)) {
                event.preventDefault();
                openLinkDialog(link);
            }
        });
        addListener(editor, 'paste', (event) => {
            const data = event.clipboardData;
            if (!data) return;
            const imageItem = Array.from(data.items || []).find((item) => item.kind === 'file' && /^image\//i.test(item.type || ''));
            if (imageItem) {
                event.preventDefault();
                insertImageFile(imageItem.getAsFile());
                return;
            }
            const html = data.getData('text/html');
            const text = data.getData('text/plain');
            if (html && html.trim()) {
                event.preventDefault();
                document.execCommand('insertHTML', false, cleanPastedHtml(html));
                afterEdit(true);
                return;
            }
            if (text) {
                event.preventDefault();
                insertPlainText(text);
            }
        });
        addListener(editor, 'dragover', (event) => {
            if (Array.from(event.dataTransfer?.items || []).some((item) => /^image\//i.test(item.type || ''))) {
                event.preventDefault();
                editor.classList.add('is-dragging-image');
            }
        });
        addListener(editor, 'dragleave', () => editor.classList.remove('is-dragging-image'));
        addListener(editor, 'drop', (event) => {
            editor.classList.remove('is-dragging-image');
            const file = Array.from(event.dataTransfer?.files || []).find(isImageFile);
            if (!file) return;
            event.preventDefault();
            const range = document.caretRangeFromPoint
                ? document.caretRangeFromPoint(event.clientX, event.clientY)
                : document.caretPositionFromPoint
                    ? (() => {
                        const position = document.caretPositionFromPoint(event.clientX, event.clientY);
                        if (!position) return null;
                        const nextRange = document.createRange();
                        nextRange.setStart(position.offsetNode, position.offset);
                        nextRange.collapse(true);
                        return nextRange;
                    })()
                    : null;
            if (range && editor.contains(range.startContainer)) {
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
            }
            saveSelection();
            insertImageFile(file);
        });
        addListener(editor, 'keydown', (event) => {
            const mod = event.metaKey || event.ctrlKey;
            const key = event.key.toLowerCase();
            if (mod && key === 's') {
                event.preventDefault();
                syncFromRich();
                if (typeof options.onSave === 'function') options.onSave();
                return;
            }
            if (mod && key === 'z') {
                event.preventDefault();
                event.shiftKey ? redo() : undo();
                return;
            }
            if (mod && key === 'y') { event.preventDefault(); redo(); return; }
            if (mod && key === 'b') { event.preventDefault(); exec('bold'); return; }
            if (mod && key === 'i') { event.preventDefault(); exec('italic'); return; }
            if (mod && key === 'u') { event.preventDefault(); exec('underline'); return; }
            if (mod && key === 'k') { event.preventDefault(); openLinkDialog(); return; }
            if (mod && event.shiftKey && key === 'x') { event.preventDefault(); exec('strikeThrough'); return; }
            if (event.key === 'Tab' && activeCell()) {
                event.preventDefault();
                moveTableCell(activeCell(), event.shiftKey);
                return;
            }
            if (event.key === 'Tab' && closestInEditor('li')) {
                event.preventDefault();
                exec(event.shiftKey ? 'outdent' : 'indent');
                return;
            }
            if (event.key === 'Tab' && closestInEditor('pre')) {
                event.preventDefault();
                document.execCommand('insertText', false, '  ');
                afterEdit(true);
                return;
            }
            if (event.key === 'Enter' && !event.shiftKey && closestInEditor('pre')) {
                event.preventDefault();
                document.execCommand('insertText', false, '\n');
                afterEdit(false);
            }
        });
        addListener(sourceEditor, 'input', syncFromSource);
        addListener(sourceEditor, 'keydown', (event) => {
            const mod = event.metaKey || event.ctrlKey;
            const key = event.key.toLowerCase();
            if (mod && key === 's') {
                event.preventDefault();
                syncFromSource();
                if (typeof options.onSave === 'function') options.onSave();
                return;
            }
            if (mod && key === 'y') {
                event.preventDefault();
                redo();
                return;
            }
            if (event.key === 'Tab') {
                event.preventDefault();
                insertSourceText('  ');
                return;
            }
            if (event.key === 'Enter' && !event.shiftKey && maybeContinueMarkdownList()) {
                event.preventDefault();
            }
        });
        addListener(sourceEditor, 'scroll', () => {
            sourceGutter.scrollTop = sourceEditor.scrollTop;
            hideReferenceToolbar();
        });
        addListener(editorView, 'scroll', () => {
            hideReferenceToolbar();
        });
        addListener(sourceEditor, 'keyup', updateBracketFeedback);
        addListener(sourceEditor, 'click', updateBracketFeedback);
        ['select', 'mouseup', 'keyup'].forEach((eventName) => {
            addListener(sourceEditor, eventName, () => {
                if (activeView === 'source') updateReferenceToolbar();
            });
        });
        addListener(document, 'selectionchange', () => {
            if (destroyed || activeView !== 'editor') return;
            if (selectionInEditor()) {
                saveSelection();
                updateToolbarState();
                updateReferenceToolbar();
            } else {
                hideReferenceToolbar();
            }
        });
        initCodeMirrorSource();

        return {
            element: shell,
            getValue() {
                return activeView === 'source' ? getSourceValue() : htmlToMarkdown(editor.innerHTML);
            },
            setValue(value) {
                const viewportScrollState = captureViewportScrollState();
                currentMarkdown = String(value ?? '');
                setSourceValue(currentMarkdown);
                replaceEditorMarkdown(editor, currentMarkdown);
                renderMermaidBlocksInEditor();
                updatePlaceholder();
                recordHistory(true);
                restoreViewportScrollState(viewportScrollState);
            },
            insertMarkdown(value) {
                const markdown = String(value ?? '');
                if (!editable || !markdown) return;
                if (activeView === 'source') {
                    insertSourceText(markdown);
                    return;
                }
                focusEditor();
                restoreSelection();
                document.execCommand('insertHTML', false, renderMarkdownToHtml(markdown));
                afterEdit(true);
            },
            focus() {
                if (activeView === 'source') focusSourceEditor();
                else focusEditor();
            },
            switchView(view, switchOptions = {}) {
                switchView(
                    view === 'source' || view === 'markdown' ? 'source' : 'editor',
                    switchOptions
                );
            },
            undo,
            redo,
            getState,
            getScrollState: captureViewportScrollState,
            restoreScrollState: restoreViewportScrollState,
            destroy() {
                destroyed = true;
                closeMenus();
                cleanupEditorCodeBlockPreviews(editor);
                cleanupFns.splice(0).forEach((cleanup) => cleanup());
                referenceToolbarController.destroy();
                shell.remove();
            },
        };
    }

    window.ChatMarkdownBlockEditor = {
        create,
        renderMarkdownToHtml,
        _test: {
            escapeHtml,
            withLineBreaks,
            decodeHtmlEntities,
            sanitizeMarkdownUrl,
            sanitizeMarkdownLinkUrl,
            sanitizeMarkdownImageUrl,
            applyInlineMarkdown,
            htmlToMarkdown,
            renderMarkdownToHtml,
        },
    };
})();
