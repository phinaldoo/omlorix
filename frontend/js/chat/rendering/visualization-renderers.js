function mountHtmlCodePreview(previewPane, source, wrapper, options = {}) {
    if (!(previewPane instanceof Element)) {
        return false;
    }
    const capabilities = analyzeHtmlPreviewCapabilities(source);
    // Permissions can only be enabled when the source actually advertises the
    // capability. This prevents stale state from a previously edited preview
    // from silently carrying over to unrelated HTML.
    const allowExternalContent = capabilities.externalContent && options.allowExternalContent === true;
    const allowScripts = capabilities.scripts
        && options.allowScripts === true
        && allowExternalContent;
    previewPane.dataset.htmlPreviewScripts = allowScripts ? 'enabled' : 'disabled';
    previewPane.dataset.htmlPreviewExternalContent = allowExternalContent ? 'enabled' : 'blocked';
    if (wrapper instanceof Element) {
        wrapper.dataset.htmlPreviewScripts = allowScripts ? 'true' : 'false';
        wrapper.dataset.htmlPreviewExternalContent = allowExternalContent ? 'true' : 'false';
        syncHtmlPreviewCapabilityControls(wrapper, capabilities);
    }

    const frameShell = document.createElement('div');
    frameShell.className = 'code-block-html-preview-shell';

    const iframe = document.createElement('iframe');
    iframe.className = 'code-block-html-preview-frame';
    iframe.setAttribute('loading', 'lazy');
    iframe.setAttribute('title', allowScripts
        ? getChatPreviewTranslation('code_block_html_preview_interactive_frame_title', 'HTML preview with scripts enabled')
        : getChatPreviewTranslation('code_block_html_preview_static_frame_title', 'Static HTML preview'));
    iframe.setAttribute('data-i18n-attr', allowScripts
        ? 'title:code_block_html_preview_interactive_frame_title'
        : 'title:code_block_html_preview_static_frame_title');
    iframe.style.height = '320px';

    frameShell.appendChild(iframe);
    previewPane.innerHTML = '';
    previewPane.appendChild(frameShell);

    const runtime = typeof window !== 'undefined' ? window.OmlorixCanvasHtmlPreview : null;
    if (!runtime || typeof runtime.render !== 'function') {
        // Never fall back to active srcdoc rendering: it would reintroduce CSP
        // inheritance and could tempt callers to weaken the application policy.
        iframe.setAttribute('sandbox', '');
        iframe.srcdoc = '<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>';
        previewPane.dataset.previewState = 'error';
        return false;
    }

    const rendered = runtime.render(iframe, source, {
        title: iframe.title,
        allowScripts,
        allowExternalContent,
        // Assistant-authored HTML must not use the trusted outer proxy to read
        // authenticated Omlorix file URLs on the viewer's behalf.
        hydrateAuthenticatedFiles: false,
    });
    previewPane.dataset.previewState = rendered ? 'ready' : 'error';
    return rendered;
}

function buildVisualizerPreviewContentSecurityPolicy(allowScripts = false) {
    // The host bootstrap always runs so even the static preview can report its
    // real height. Authored scripts are physically removed until the viewer
    // opts in, while the opaque-origin sandbox remains the final isolation
    // boundary in both modes.
    const scriptPolicy = "script-src 'unsafe-inline';";
    return [
        "default-src 'none';",
        "img-src data: blob:;",
        "media-src data: blob:;",
        "style-src 'unsafe-inline';",
        "font-src data:;",
        scriptPolicy,
        "connect-src 'none';",
        "frame-src 'none';",
        "base-uri 'none';",
        "form-action 'none';",
    ].join(' ');
}

function stripVisualizerAuthoredScripts(source) {
    const markup = String(source || '');
    const isFullDocument = /<html[\s>]/i.test(markup);
    const container = isFullDocument
        ? new DOMParser().parseFromString(markup, 'text/html')
        : document.createElement('template');
    if (!isFullDocument) {
        container.innerHTML = markup;
    }
    const root = isFullDocument ? container : container.content;
    root.querySelectorAll('script').forEach((script) => script.remove());
    root.querySelectorAll('*').forEach((element) => {
        Array.from(element.attributes || []).forEach((attribute) => {
            const name = String(attribute.name || '').toLowerCase();
            const value = String(attribute.value || '').trim().toLowerCase();
            if (name.startsWith('on') || name === 'srcdoc' || value.startsWith('javascript:')) {
                element.removeAttribute(attribute.name);
            }
        });
    });
    return isFullDocument ? container.documentElement.outerHTML : container.innerHTML;
}

function escapeEmbeddedScriptSource(source) {
    return String(source || '').replace(/<\/script/gi, '<\\/script');
}

function buildVisualizerBridgeScript(previewId, capabilities = {}) {
    const previewIdLiteral = JSON.stringify(String(previewId || ''));
    const capabilitiesLiteral = JSON.stringify({
        scripts: capabilities?.scripts !== false,
        external_data: capabilities?.external_data === true,
        chat_followup: capabilities?.chat_followup === true,
        download: capabilities?.download === true,
    });
    return `<script>
(() => {
    'use strict';
    const previewId = ${previewIdLiteral};
    const capabilities = Object.freeze(${capabilitiesLiteral});
    const pending = new Map();
    let requestCounter = 0;
    let resizeQueued = false;

    function postHeight() {
        resizeQueued = false;
        const body = document.body;
        const shell = document.querySelector('.omlorix-visualizer-shell');
        // documentElement.scrollHeight is never smaller than the current iframe
        // viewport, which creates a one-way ratchet and prevents short visuals
        // from shrinking. Body and shell measurements track authored content.
        const height = Math.ceil(Math.max(
            body ? body.scrollHeight : 0,
            body ? body.offsetHeight : 0,
            shell ? shell.scrollHeight : 0,
            shell ? shell.offsetHeight : 0
        ));
        parent.postMessage({ type: '${CODE_BLOCK_HTML_PREVIEW_MESSAGE_TYPE}', previewId, height }, '*');
    }

    function queueHeight() {
        if (resizeQueued) return;
        resizeQueued = true;
        requestAnimationFrame(postHeight);
    }

    function request(action, payload) {
        const requestId = previewId + ':' + (++requestCounter) + ':' + Date.now();
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                pending.delete(requestId);
                reject(new Error('The Omlorix visualization request timed out.'));
            }, 30000);
            pending.set(requestId, { resolve, reject, timeout });
            parent.postMessage({
                type: '${VISUALIZATION_HOST_REQUEST_MESSAGE_TYPE}',
                previewId,
                requestId,
                action,
                payload: payload || {}
            }, '*');
        });
    }

    window.addEventListener('message', (event) => {
        const data = event.data || {};
        if (data.type === '${VISUALIZATION_HOST_RESPONSE_MESSAGE_TYPE}' && data.previewId === previewId) {
            const entry = pending.get(String(data.requestId || ''));
            if (!entry) return;
            clearTimeout(entry.timeout);
            pending.delete(String(data.requestId || ''));
            if (data.ok) entry.resolve(data.result);
            else entry.reject(new Error(String(data.error || 'The visualization request failed.')));
            return;
        }
        if (data.type === '${VISUALIZATION_THEME_MESSAGE_TYPE}' && data.previewId === previewId) {
            const tokens = data.tokens && typeof data.tokens === 'object' ? data.tokens : {};
            Object.entries(tokens).forEach(([name, value]) => {
                if (/^--[a-z0-9-]+$/i.test(name)) {
                    document.documentElement.style.setProperty(name, String(value));
                }
            });
            if (data.mode === 'light' || data.mode === 'dark') {
                document.documentElement.dataset.mode = data.mode;
                document.documentElement.style.colorScheme = data.mode;
            }
            queueHeight();
        }
    });

    window.omlorix = Object.freeze({
        visualization: Object.freeze({
            capabilities,
            sendFollowUpMessage(options) {
                if (!capabilities.chat_followup) {
                    return Promise.reject(new Error('Chat follow-up capability was not requested.'));
                }
                return request('send-follow-up', options || {});
            },
            requestExternalData(options) {
                if (!capabilities.external_data) {
                    return Promise.reject(new Error('External data capability was not requested.'));
                }
                return request('external-data', options || {});
            },
            download(options) {
                if (!capabilities.download) {
                    return Promise.reject(new Error('Download capability was not requested.'));
                }
                return request('download', options || {});
            },
            reportHeight: queueHeight
        })
    });

    function setupTooltips() {
        let tooltip = null;
        function hide() {
            if (tooltip) tooltip.hidden = true;
        }
        function show(target) {
            const text = String(target?.dataset?.tooltip || '').trim();
            if (!text) return;
            if (!tooltip) {
                tooltip = document.createElement('div');
                tooltip.className = 'tooltip';
                tooltip.setAttribute('role', 'tooltip');
                document.body.appendChild(tooltip);
            }
            tooltip.textContent = text;
            tooltip.hidden = false;
            const targetRect = target.getBoundingClientRect();
            const bodyRect = document.body.getBoundingClientRect();
            const tooltipRect = tooltip.getBoundingClientRect();
            const top = Math.max(4, targetRect.top - bodyRect.top - tooltipRect.height - 6);
            const left = Math.max(4, Math.min(
                targetRect.left - bodyRect.left + (targetRect.width - tooltipRect.width) / 2,
                Math.max(4, bodyRect.width - tooltipRect.width - 4)
            ));
            tooltip.style.top = top + 'px';
            tooltip.style.left = left + 'px';
        }
        document.addEventListener('pointerover', (event) => {
            const target = event.target instanceof Element ? event.target.closest('[data-tooltip]') : null;
            if (target) show(target);
        });
        document.addEventListener('pointerout', hide);
        document.addEventListener('focusin', (event) => {
            const target = event.target instanceof Element ? event.target.closest('[data-tooltip]') : null;
            if (target) show(target);
        });
        document.addEventListener('focusout', hide);
    }

    function initialize() {
        setupTooltips();
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons({ attrs: { width: 16, height: 16 } });
        }
        if (window.ResizeObserver) {
            const observer = new ResizeObserver(queueHeight);
            if (document.body) observer.observe(document.body);
        }
        if (window.MutationObserver && document.body) {
            const observer = new MutationObserver(queueHeight);
            observer.observe(document.body, { attributes: true, childList: true, characterData: true, subtree: true });
        }
        queueHeight();
        setTimeout(queueHeight, 120);
        setTimeout(queueHeight, 420);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
    window.addEventListener('resize', queueHeight);
})();
</script>`;
}

function buildVisualizerPreviewDocument(source, previewId, options = {}) {
    const allowScripts = options.allowScripts === true;
    const emptyLabel = String(options.emptyLabel || 'No visualization content.');
    const themeMode = getPreviewThemeMode();
    const themeVariables = buildVisualizerThemeCssVariables();
    const runtimeCss = String(options.runtimeCss || '');
    const runtimeLibraries = allowScripts
        ? [options.d3, options.topojson, options.lucide]
            .filter(Boolean)
            .map((library) => `<script>${escapeEmbeddedScriptSource(library)}</script>`)
            .join('')
        : '';
    const bridgeScript = buildVisualizerBridgeScript(previewId, options.capabilities || {});
    const helperHead = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        `<meta http-equiv="Content-Security-Policy" content="${buildVisualizerPreviewContentSecurityPolicy(allowScripts)}">`,
        `<style>
            :root {
                color-scheme: ${themeMode};
                ${themeVariables}
                background: transparent;
            }
            html, body {
                margin: 0;
                padding: 0;
                min-width: 0;
                background: transparent;
                color: var(--foreground);
            }
            *, *::before, *::after {
                box-sizing: border-box;
            }
            .omlorix-visualizer-shell {
                position: relative;
                width: 100%;
                min-width: 0;
            }
            .omlorix-visualizer-empty {
                min-height: 160px;
                display: grid;
                place-items: center;
                text-align: center;
                color: var(--muted-foreground);
            }
            ${runtimeCss.replace(/<\/style/gi, '<\\/style')}
        </style>`,
        runtimeLibraries,
        bridgeScript,
    ].join('');

    const rawSourceText = String(source || '').trim();
    const sourceText = allowScripts ? rawSourceText : stripVisualizerAuthoredScripts(rawSourceText);
    if (!sourceText) {
        return `<!doctype html><html data-omlorix-preview-mode="${themeMode}"><head>${helperHead}</head><body><div class="omlorix-visualizer-shell"><div class="omlorix-visualizer-empty">${escapeHtml(emptyLabel)}</div></div></body></html>`;
    }

    if (/<html[\s>]/i.test(sourceText)) {
        let documentHtml = sourceText;
        if (/<head[\s>]/i.test(documentHtml)) {
            documentHtml = documentHtml.replace(/<head([^>]*)>/i, `<head$1>${helperHead}`);
        } else {
            documentHtml = documentHtml.replace(/<html([^>]*)>/i, `<html$1><head>${helperHead}</head>`);
        }
        if (/<body([^>]*)>/i.test(documentHtml)) {
            documentHtml = documentHtml.replace(/<body([^>]*)>/i, '<body$1><div class="omlorix-visualizer-shell">');
        } else {
            if (/<\/html>/i.test(documentHtml)) {
                documentHtml = documentHtml.replace(
                    /<\/html>/i,
                    '<body><div class="omlorix-visualizer-shell"></div></body></html>'
                );
                return documentHtml;
            }
            documentHtml += '<body><div class="omlorix-visualizer-shell">';
        }
        if (/<\/body>/i.test(documentHtml)) {
            documentHtml = documentHtml.replace(/<\/body>/i, '</div></body>');
        } else {
            documentHtml += '</div></body>';
        }
        return documentHtml;
    }

    return `<!doctype html><html data-omlorix-preview-mode="${themeMode}"><head>${helperHead}</head><body><div class="omlorix-visualizer-shell">${sourceText}</div></body></html>`;
}

function renderSvgCodePreview(target, source) {
    if (!(target instanceof Element)) {
        return false;
    }
    let sanitized = '';
    try {
        if (window.ChatSanitizer && typeof window.ChatSanitizer.sanitizeSvg === 'function') {
            sanitized = window.ChatSanitizer.sanitizeSvg(String(source || ''));
        }
    } catch (error) {
        console.error('SVG preview sanitization failed:', error);
    }
    if (!sanitized) {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(getChatPreviewTranslation('code_block_svg_preview_unavailable', 'SVG preview is unavailable for this block.'))}</div>`;
        return false;
    }
    target.innerHTML = `<div class="code-block-svg-preview code-block-svg-preview-panel">${sanitized}</div>`;
    return true;
}

function renderMarkdownCodePreview(target, source) {
    if (!(target instanceof Element)) {
        return false;
    }
    const preview = document.createElement('div');
    preview.className = 'code-block-markdown-preview';
    target.innerHTML = '';
    target.appendChild(preview);
    renderMarkdownContent(preview, source);
    return true;
}

function parseDelimitedText(source, delimiter) {
    const text = String(source || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const rows = [];
    let row = [];
    let cell = '';
    let inQuotes = false;

    for (let i = 0; i < text.length; i += 1) {
        const char = text[i];
        const next = text[i + 1];
        if (char === '"') {
            if (inQuotes && next === '"') {
                cell += '"';
                i += 1;
            } else {
                inQuotes = !inQuotes;
            }
            continue;
        }
        if (!inQuotes && char === delimiter) {
            row.push(cell);
            cell = '';
            continue;
        }
        if (!inQuotes && char === '\n') {
            row.push(cell);
            rows.push(row);
            row = [];
            cell = '';
            continue;
        }
        cell += char;
    }

    if (cell.length || row.length) {
        row.push(cell);
        rows.push(row);
    }

    return rows.filter((cells) => cells.some((value) => String(value || '').trim() !== ''));
}

function renderDelimitedPreview(target, source, delimiter) {
    if (!(target instanceof Element)) {
        return false;
    }
    const rows = parseDelimitedText(source, delimiter);
    if (!rows.length) {
        target.innerHTML = `<div class="code-block-preview-status">${escapeHtml(getChatPreviewTranslation('code_block_data_preview_empty', 'No rows to preview.'))}</div>`;
        return false;
    }

    const tableWrapper = document.createElement('div');
    tableWrapper.className = 'code-block-data-table';
    const table = document.createElement('table');
    const headerRow = rows[0];
    const thead = document.createElement('thead');
    const tbody = document.createElement('tbody');

    const headerTr = document.createElement('tr');
    headerRow.forEach((value) => {
        const th = document.createElement('th');
        th.textContent = String(value || '');
        headerTr.appendChild(th);
    });
    thead.appendChild(headerTr);

    rows.slice(1).forEach((cells) => {
        const tr = document.createElement('tr');
        headerRow.forEach((_, index) => {
            const td = document.createElement('td');
            td.textContent = String(cells[index] || '');
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });

    table.appendChild(thead);
    table.appendChild(tbody);
    tableWrapper.appendChild(table);
    target.innerHTML = '';
    target.appendChild(tableWrapper);
    return true;
}

function buildDataPreviewNode(key, value) {
    const row = document.createElement('div');
    row.className = 'code-block-data-node';

    if (key !== null && key !== undefined) {
        const keyEl = document.createElement('div');
        keyEl.className = 'code-block-data-key';
        keyEl.textContent = String(key);
        row.appendChild(keyEl);
    }

    if (value && typeof value === 'object') {
        const valueContainer = document.createElement('div');
        valueContainer.className = 'code-block-data-children';
        const entries = Array.isArray(value)
            ? value.map((item, index) => [index, item])
            : Object.entries(value);
        entries.forEach(([childKey, childValue]) => {
            valueContainer.appendChild(buildDataPreviewNode(childKey, childValue));
        });
        if (!entries.length) {
            const emptyEl = document.createElement('div');
            emptyEl.className = 'code-block-data-value is-empty';
            emptyEl.textContent = Array.isArray(value) ? '[]' : '{}';
            valueContainer.appendChild(emptyEl);
        }
        row.appendChild(valueContainer);
        return row;
    }

    const valueEl = document.createElement('div');
    valueEl.className = 'code-block-data-value';
    valueEl.textContent = value === null
        ? 'null'
        : typeof value === 'boolean'
            ? (value ? 'true' : 'false')
            : String(value ?? '');
    row.appendChild(valueEl);
    return row;
}

function renderStructuredDataPreview(target, data) {
    if (!(target instanceof Element)) {
        return false;
    }
    const container = document.createElement('div');
    container.className = 'code-block-data-preview';
    container.appendChild(buildDataPreviewNode(null, data));
    target.innerHTML = '';
    target.appendChild(container);
    return true;
}

function parseSimpleYamlValue(rawValue) {
    const value = String(rawValue || '').trim();
    if (!value.length) return '';
    if (value === 'true') return true;
    if (value === 'false') return false;
    if (value === 'null' || value === '~') return null;
    if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
    if (value.startsWith('"') && value.endsWith('"')) {
        const inner = value.slice(1, -1);
        return inner.replace(/\\(["\\/bfnrt])/g, (_match, escaped) => {
            switch (escaped) {
                case 'b':
                    return '\b';
                case 'f':
                    return '\f';
                case 'n':
                    return '\n';
                case 'r':
                    return '\r';
                case 't':
                    return '\t';
                default:
                    return escaped;
            }
        }).replace(/\\u([0-9a-fA-F]{4})/g, (_match, hex) => {
            try {
                return String.fromCharCode(parseInt(hex, 16));
            } catch (_) {
                return _match;
            }
        });
    }
    if (value.startsWith("'") && value.endsWith("'")) {
        return value.slice(1, -1).replace(/''/g, "'");
    }
    return value;
}

function parseSimpleYaml(source) {
    const lines = String(source || '').replace(/\r\n/g, '\n').split('\n');
    const root = {};
    const stack = [{ indent: -1, value: root }];

    const ensureContainerForIndent = (indent) => {
        while (stack.length > 1 && indent <= stack[stack.length - 1].indent) {
            stack.pop();
        }
        return stack[stack.length - 1];
    };

    for (let i = 0; i < lines.length; i += 1) {
        const rawLine = lines[i];
        if (!rawLine.trim() || /^\s*#/.test(rawLine)) {
            continue;
        }

        const indent = rawLine.match(/^\s*/)?.[0]?.length || 0;
        const line = rawLine.trim();
        const parentFrame = ensureContainerForIndent(indent);
        let parentValue = parentFrame.value;

        if (line.startsWith('- ')) {
            if (!Array.isArray(parentValue)) {
                if (Array.isArray(parentFrame.pendingParent?.[parentFrame.pendingKey])) {
                    parentValue = parentFrame.pendingParent[parentFrame.pendingKey];
                } else if (parentFrame.pendingParent && parentFrame.pendingKey) {
                    parentFrame.pendingParent[parentFrame.pendingKey] = [];
                    parentValue = parentFrame.pendingParent[parentFrame.pendingKey];
                } else {
                    throw new Error(getChatPreviewTranslation('code_block_unsupported_yaml_structure', 'Unsupported YAML structure'));
                }
                parentFrame.value = parentValue;
            }

            const itemContent = line.slice(2);
            if (!itemContent.includes(':')) {
                parentValue.push(parseSimpleYamlValue(itemContent));
                continue;
            }

            const objectItem = {};
            parentValue.push(objectItem);
            const separatorIndex = itemContent.indexOf(':');
            const itemKey = itemContent.slice(0, separatorIndex).trim();
            const itemValue = itemContent.slice(separatorIndex + 1).trim();
            if (itemValue) {
                objectItem[itemKey] = parseSimpleYamlValue(itemValue);
                stack.push({ indent, value: objectItem });
            } else {
                objectItem[itemKey] = {};
                stack.push({
                    indent,
                    value: objectItem[itemKey],
                    pendingParent: objectItem,
                    pendingKey: itemKey,
                });
            }
            continue;
        }

        const separatorIndex = line.indexOf(':');
        if (separatorIndex === -1) {
            throw new Error(getChatPreviewTranslation('code_block_unsupported_yaml_structure', 'Unsupported YAML structure'));
        }

        const key = line.slice(0, separatorIndex).trim();
        const value = line.slice(separatorIndex + 1).trim();

        if (Array.isArray(parentValue)) {
            const objectItem = {};
            parentValue.push(objectItem);
            parentValue = objectItem;
        }

        if (!value) {
            const nextLine = lines.slice(i + 1).find((candidate) => candidate.trim() && !/^\s*#/.test(candidate));
            const nextTrimmed = nextLine ? nextLine.trim() : '';
            const nextIndent = nextLine ? (nextLine.match(/^\s*/)?.[0]?.length || 0) : indent;
            const containerValue = nextTrimmed.startsWith('- ') && nextIndent > indent ? [] : {};
            parentValue[key] = containerValue;
            stack.push({
                indent,
                value: containerValue,
                pendingParent: parentValue,
                pendingKey: key,
            });
            continue;
        }

        parentValue[key] = parseSimpleYamlValue(value);
    }

    return root;
}

function renderYamlOutlinePreview(target, source) {
    if (!(target instanceof Element)) {
        return false;
    }
    const lines = String(source || '').replace(/\r\n/g, '\n').split('\n');
    const outline = document.createElement('div');
    outline.className = 'code-block-yaml-preview';

    lines.forEach((rawLine) => {
        if (!rawLine.trim() || /^\s*#/.test(rawLine)) {
            return;
        }
        const indent = rawLine.match(/^\s*/)?.[0]?.length || 0;
        const line = rawLine.trim();
        const row = document.createElement('div');
        row.className = 'code-block-yaml-row';
        row.style.paddingLeft = `${Math.min(indent * 0.75, 32)}px`;

        if (line.startsWith('- ')) {
            const bullet = document.createElement('span');
            bullet.className = 'code-block-yaml-bullet';
            bullet.textContent = '•';
            row.appendChild(bullet);

            const content = line.slice(2);
            const separatorIndex = content.indexOf(':');
            if (separatorIndex === -1) {
                const value = document.createElement('span');
                value.className = 'code-block-yaml-value';
                value.textContent = content;
                row.appendChild(value);
            } else {
                const key = document.createElement('span');
                key.className = 'code-block-yaml-key';
                key.textContent = content.slice(0, separatorIndex).trim();
                const value = document.createElement('span');
                value.className = 'code-block-yaml-value';
                value.textContent = content.slice(separatorIndex + 1).trim();
                row.appendChild(key);
                row.appendChild(value);
            }
        } else {
            const separatorIndex = line.indexOf(':');
            if (separatorIndex === -1) {
                const value = document.createElement('span');
                value.className = 'code-block-yaml-value';
                value.textContent = line;
                row.appendChild(value);
            } else {
                const key = document.createElement('span');
                key.className = 'code-block-yaml-key';
                key.textContent = line.slice(0, separatorIndex).trim();
                const value = document.createElement('span');
                value.className = 'code-block-yaml-value';
                value.textContent = line.slice(separatorIndex + 1).trim();
                row.appendChild(key);
                row.appendChild(value);
            }
        }

        outline.appendChild(row);
    });

    target.innerHTML = '';
    target.appendChild(outline);
    return true;
}

