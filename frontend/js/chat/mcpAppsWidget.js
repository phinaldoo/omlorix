(function () {
    'use strict';

    const MCP_APPS_PROTOCOL_VERSION = '2026-01-26';
    // Excalidraw and other deployed MCP Apps still negotiate the immediately
    // preceding stable host contract. Keep the compatibility set explicit so
    // unknown or malformed versions continue to receive a protocol error.
    const SUPPORTED_MCP_APPS_PROTOCOL_VERSIONS = new Set([
        MCP_APPS_PROTOCOL_VERSION,
        '2025-11-21',
    ]);
    // The outer proxy contains only Omlorix-owned bridge code. The untrusted MCP
    // app is loaded in the proxy's nested iframe without allow-same-origin.
    // Keeping allow-same-origin on this trusted proxy also lets the browser
    // enforce the app frame's same-origin frame-ancestor policy.
    const SANDBOX_PROXY_IFRAME_SANDBOX = 'allow-scripts allow-same-origin';
    const VIEW_IFRAME_SANDBOX = 'allow-scripts allow-forms allow-popups allow-downloads';
    const SANDBOX_PROXY_URL = '/api/v1/llm/mcp/apps/sandbox-proxy';
    const EXTERNAL_RESOURCE_CONSENT_STORAGE_KEY = 'omlorix:mcp-app:external-resource-consent:v1';
    const MAX_SAVED_EXTERNAL_RESOURCE_CONSENTS = 200;

    const _widgetEntries = new Map();
    const _windowToWidgetId = new WeakMap();
    let _widgetCounter = 0;
    let _hostRequestCounter = 0;
    let _widgetRemovalObserver = null;

    function _t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function _tf(key, fallback, vars = {}) {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(_t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function _nextWidgetId() {
        _widgetCounter += 1;
        return `mcp-app-${Date.now()}-${_widgetCounter}`;
    }

    function _isObject(value) {
        return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
    }

    function _deepClone(value) {
        if (value == null) return value;
        try {
            return JSON.parse(JSON.stringify(value));
        } catch (_) {
            return value;
        }
    }

    function _decodeBase64Text(value) {
        const text = String(value || '').trim();
        if (!text) return '';
        try {
            const normalized = text.startsWith('data:') && text.includes(',')
                ? text.split(',', 2)[1]
                : text;
            return decodeURIComponent(Array.prototype.map.call(atob(normalized), (char) => {
                return `%${char.charCodeAt(0).toString(16).padStart(2, '0')}`;
            }).join(''));
        } catch (_) {
            try {
                return atob(text);
            } catch (_error) {
                return '';
            }
        }
    }

    function _getRootTheme() {
        const root = document.documentElement;
        const mode = String(root?.dataset?.mode || '').trim().toLowerCase();
        if (mode === 'dark') return 'dark';
        return 'light';
    }

    function _buildHostCapabilities(entry) {
        const resourceMeta = _isObject(entry?.resourceMeta) ? entry.resourceMeta : {};
        return {
            logging: {},
            openLinks: {},
            serverTools: {},
            serverResources: {},
            // The handler accepts plain text plus JSON structured content.
            // Advertising the supported modalities lets conforming MCP Apps
            // feature-detect ui/message before attempting to use it.
            message: {
                text: {},
                structuredContent: {},
            },
            sandbox: {
                // The direct opaque-origin sandbox intentionally exposes no
                // sensitive device APIs.
                permissions: {},
                csp: _deepClone(resourceMeta.csp || {}),
            },
        };
    }

    function _getContainerDimensions(entry) {
        const stage = entry.stageEl;
        const width = Math.max(320, Math.round(stage?.clientWidth || entry.wrapper?.clientWidth || 0));
        const height = Math.max(320, Math.round(stage?.clientHeight || entry.iframe?.clientHeight || 720));
        return { width, height };
    }

    function _buildHostContext(entry) {
        let timeZone = '';
        try {
            timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        } catch (_) {}
        return {
            theme: _getRootTheme(),
            locale: navigator.language || 'en',
            timeZone: timeZone || undefined,
            userAgent: navigator.userAgent || undefined,
            platform: /mobile|android|iphone|ipad/i.test(navigator.userAgent || '') ? 'mobile' : 'web',
            deviceCapabilities: {
                touch: Number(navigator.maxTouchPoints || 0) > 0,
                hover: typeof window.matchMedia === 'function'
                    ? window.matchMedia('(hover: hover)').matches
                    : false,
            },
            displayMode: entry.displayMode || 'inline',
            availableDisplayModes: ['inline', 'fullscreen'],
            containerDimensions: _getContainerDimensions(entry),
            safeAreaInsets: {
                top: 0,
                right: 0,
                bottom: 0,
                left: 0,
            },
        };
    }

    function _notifyIframe(entry, method, params = {}) {
        const target = entry.iframe?.contentWindow;
        if (!target) return;
        target.postMessage({
            jsonrpc: '2.0',
            method,
            params,
        }, '*');
    }

    function _respond(entry, id, result) {
        const target = entry.iframe?.contentWindow;
        if (!target || id == null) return;
        target.postMessage({
            jsonrpc: '2.0',
            id,
            result,
        }, '*');
    }

    function _respondError(entry, id, code, message, data = undefined) {
        const target = entry.iframe?.contentWindow;
        if (!target || id == null) return;
        target.postMessage({
            jsonrpc: '2.0',
            id,
            error: {
                code,
                message,
                data,
            },
        }, '*');
    }

    function _permissionsToAllowAttr() {
        // Device access requires a separately hosted sandbox proxy. Keeping
        // this same-origin document opaque prevents an untrusted app from
        // combining scripts with origin privileges.
        return 'fullscreen *';
    }

    function _sandboxProxyUrl() {
        // A normal HTTP document receives its own response CSP. In contrast,
        // Safari inherits the embedding page's script-src policy for data URLs,
        // which blocks an inline proxy bootstrap before it can report ready.
        return SANDBOX_PROXY_URL;
    }

    const _CSP_KEYWORD_SOURCES = new Set(["'self'", "'none'"]);
    const _CSP_SCHEME_SOURCE_PATTERN = /^[a-z][a-z0-9+.-]*:$/i;
    const _CSP_HOST_SOURCE_PATTERN = /^(?:(?:[a-z][a-z0-9+.-]*):\/\/)?(?:\*|\*\.[a-z0-9-]+(?:\.[a-z0-9-]+)*|(?:[a-z0-9-]+|\[[0-9a-f:.]+\])(?:\.[a-z0-9-]+)*)(?::(?:\*|[0-9]{1,5}))?(?:\/[^\s;,"'<>]*)?$/i;

    function _isSafeCspSource(value) {
        const source = String(value || '').trim();
        if (!source || /[\s;,"'<>]/.test(source)) {
            return false;
        }
        if (_CSP_KEYWORD_SOURCES.has(source)) {
            return true;
        }
        return _CSP_SCHEME_SOURCE_PATTERN.test(source) || _CSP_HOST_SOURCE_PATTERN.test(source);
    }

    function _normalizeCspSourceList(values) {
        const input = Array.isArray(values) ? values : (values ? [values] : []);
        return input
            .map((item) => String(item || '').trim())
            .filter(_isSafeCspSource);
    }

    function _mergeResourceMeta(...values) {
        const merged = {};
        values.forEach((value) => {
            if (!_isObject(value)) return;
            Object.entries(value).forEach(([key, item]) => {
                if (key === 'csp' && _isObject(item)) {
                    const csp = _isObject(merged.csp) ? merged.csp : {};
                    Object.entries(item).forEach(([cspKey, cspValue]) => {
                        const existing = Array.isArray(csp[cspKey]) ? csp[cspKey] : (csp[cspKey] ? [csp[cspKey]] : []);
                        const incoming = Array.isArray(cspValue) ? cspValue : (cspValue ? [cspValue] : []);
                        csp[cspKey] = Array.from(new Set([...existing, ...incoming].map((source) => String(source || '').trim()).filter(Boolean)));
                    });
                    merged.csp = csp;
                    return;
                }
                merged[key] = _deepClone(item);
            });
        });
        return merged;
    }

    function _getCspSources(csp, ...keys) {
        if (!_isObject(csp)) return [];
        const values = [];
        keys.forEach((key) => {
            const raw = csp[key];
            if (Array.isArray(raw)) {
                values.push(...raw);
            } else if (raw) {
                values.push(raw);
            }
        });
        return _normalizeCspSourceList(values);
    }

    function _isExternalCspSource(source) {
        const value = String(source || '').trim();
        if (!value || value === "'self'" || value === "'none'" || value === 'data:' || value === 'blob:') {
            return false;
        }
        return true;
    }

    function _displayCspSource(source) {
        const value = String(source || '').trim();
        if (!value) return '';
        if (value === '*') return '*';
        if (value.endsWith(':') && _CSP_SCHEME_SOURCE_PATTERN.test(value)) {
            return value;
        }
        try {
            const parsed = new URL(value.includes('://') ? value : `https://${value}`);
            return parsed.host || value;
        } catch (_) {
            return value.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
        }
    }

    function _collectExternalResourceSources(meta) {
        const csp = _isObject(meta?.csp) ? meta.csp : {};
        const groupedSources = [
            ..._getCspSources(csp, 'resourceDomains', 'resource_domains'),
            ..._getCspSources(csp, 'connectDomains', 'connect_domains'),
            ..._getCspSources(csp, 'frameDomains', 'frame_domains'),
            ..._getCspSources(csp, 'baseUriDomains', 'base_uri_domains'),
        ];
        const sources = groupedSources
            .filter(_isExternalCspSource)
            .map((source) => ({
                source,
                label: _displayCspSource(source),
            }))
            .filter((item) => item.label);
        const deduped = new Map();
        sources.forEach((item) => {
            if (!deduped.has(item.label)) {
                deduped.set(item.label, item.source);
            }
        });
        return Array.from(deduped.entries())
            .map(([label, source]) => ({ label, source }))
            .sort((a, b) => a.label.localeCompare(b.label));
    }

    function _externalResourceSignature(sources) {
        return (Array.isArray(sources) ? sources : [])
            .map((item) => String(item?.source || item?.label || '').trim())
            .filter(Boolean)
            .sort()
            .join('|');
    }

    function _externalResourceConsentServerKey(serverId) {
        return String(serverId || '').trim() || 'unknown-server';
    }

    function _readSavedExternalResourceConsents() {
        try {
            const raw = window.localStorage?.getItem(EXTERNAL_RESOURCE_CONSENT_STORAGE_KEY);
            if (!raw) return {};
            const parsed = JSON.parse(raw);
            return _isObject(parsed) ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function _writeSavedExternalResourceConsents(value) {
        try {
            window.localStorage?.setItem(EXTERNAL_RESOURCE_CONSENT_STORAGE_KEY, JSON.stringify(value || {}));
        } catch (_) {
            // localStorage can be unavailable or full; consent still works for this widget.
        }
    }

    function _hasSavedExternalResourceConsent(serverId, signature) {
        const serverKey = _externalResourceConsentServerKey(serverId);
        const consentSignature = String(signature || '').trim();
        if (!consentSignature) return false;
        const saved = _readSavedExternalResourceConsents();
        return Boolean(_isObject(saved[serverKey]) && _isObject(saved[serverKey][consentSignature]));
    }

    function _saveExternalResourceConsent(serverId, signature, sources) {
        const serverKey = _externalResourceConsentServerKey(serverId);
        const consentSignature = String(signature || '').trim();
        if (!consentSignature) return;
        const saved = _readSavedExternalResourceConsents();
        const serverConsents = _isObject(saved[serverKey]) ? saved[serverKey] : {};
        serverConsents[consentSignature] = {
            savedAt: new Date().toISOString(),
            sources: (Array.isArray(sources) ? sources : []).map((item) => item.source || item.label).filter(Boolean),
        };
        saved[serverKey] = serverConsents;

        const entries = [];
        Object.entries(saved).forEach(([currentServerKey, consents]) => {
            if (!_isObject(consents)) return;
            Object.entries(consents).forEach(([currentSignature, meta]) => {
                entries.push({
                    serverKey: currentServerKey,
                    signature: currentSignature,
                    savedAt: String(meta?.savedAt || ''),
                });
            });
        });
        if (entries.length > MAX_SAVED_EXTERNAL_RESOURCE_CONSENTS) {
            entries
                .sort((a, b) => a.savedAt.localeCompare(b.savedAt))
                .slice(0, entries.length - MAX_SAVED_EXTERNAL_RESOURCE_CONSENTS)
                .forEach((entry) => {
                    if (_isObject(saved[entry.serverKey])) {
                        delete saved[entry.serverKey][entry.signature];
                    }
                });
        }

        _writeSavedExternalResourceConsents(saved);
    }

    function _bindIframeWindow(entry) {
        const frameWindow = entry?.iframe?.contentWindow;
        if (!frameWindow) {
            return;
        }
        if (entry._boundWindow && entry._boundWindow !== frameWindow) {
            _windowToWidgetId.delete(entry._boundWindow);
        }
        entry._boundWindow = frameWindow;
        _windowToWidgetId.set(frameWindow, entry.id);
    }

    function _clearIframeDocument(entry) {
        const iframe = entry?.iframe;
        if (!iframe) {
            return;
        }
        // Setting iframe.srcdoc to an empty string navigates Firefox to an
        // about:srcdoc document, which can leave a visible browser error page
        // behind if a remount races with the real frame URL. Clearing the
        // attribute and navigating to about:blank gives us one predictable
        // teardown path.
        iframe.removeAttribute('srcdoc');
        iframe.removeAttribute('src');
        try {
            iframe.src = 'about:blank';
        } catch (_) {
            // Some test doubles and older browsers expose a read-only iframe src.
        }
    }

    function _setIframeDocumentUrl(entry, frameUrl) {
        const iframe = entry?.iframe;
        if (!iframe) {
            return;
        }
        const url = String(frameUrl || '').trim();
        if (!url) {
            throw new Error(_t('mcp_app_load_failed', 'Failed to load interactive app.'));
        }
        iframe.removeAttribute('srcdoc');
        _bindIframeWindow(entry);
        iframe.src = url;
    }

    function _safeUrl(url) {
        const validator = window.ChatSanitizer?.isSafeUrl;
        if (typeof validator === 'function') {
            return validator(url);
        }
        return /^https?:\/\//i.test(String(url || '').trim());
    }

    async function _postJson(path, payload, options = {}) {
        const signal = options?.signal;
        if (typeof window.authedFetch !== 'function') {
            throw new Error(_t('mcp_app_authed_fetch_unavailable', 'Authenticated fetch is unavailable.'));
        }
        const response = await window.authedFetch(path, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            body: JSON.stringify(payload || {}),
            signal,
        });
        if (!response.ok) {
            const detail = await response.text().catch(() => '');
            const error = new Error(detail || _tf('mcp_app_request_failed_status', 'Request failed ({status})', { status: response.status }));
            error.status = response.status;
            error.detail = detail;
            throw error;
        }
        return response.json();
    }

    function _isExpiredMcpAppTokenError(error) {
        const text = String(error?.detail || error?.message || '').toLowerCase();
        return error?.status === 403 && text.includes('mcp app access token expired');
    }

    function _decodeMcpAppTokenPayload(token) {
        const rawToken = String(token || '').trim();
        if (!rawToken || !rawToken.includes('.')) return null;
        const encodedPayload = rawToken.split('.', 1)[0];
        try {
            const normalized = encodedPayload.replace(/-/g, '+').replace(/_/g, '/');
            const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
            const jsonText = decodeURIComponent(Array.prototype.map.call(atob(padded), (char) => {
                return `%${char.charCodeAt(0).toString(16).padStart(2, '0')}`;
            }).join(''));
            const payload = JSON.parse(jsonText);
            return _isObject(payload) ? payload : null;
        } catch (_) {
            return null;
        }
    }

    function _mcpAppTokenExpiresSoon(token) {
        const payload = _decodeMcpAppTokenPayload(token);
        const expiresAt = Number(payload?.exp || 0);
        if (!Number.isFinite(expiresAt) || expiresAt <= 0) {
            return false;
        }
        return expiresAt <= Math.ceil(Date.now() / 1000) + 30;
    }

    async function _refreshAppAccessToken(entry) {
        if (entry._tokenRefreshPromise) return entry._tokenRefreshPromise;
        entry._tokenRefreshPromise = (async () => {
            const result = await _postJson('/api/v1/llm/mcp/apps/token/refresh', {
                server_id: entry.meta.server_id,
                access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                app_access_token: entry.meta.app_access_token || '',
                tool_call_id: entry.meta.tool_call_id || '',
            });
            const nextToken = String(result?.app_access_token || '').trim();
            if (!nextToken) {
                throw new Error(_t('mcp_app_load_failed', 'Failed to load interactive app.'));
            }
            entry.meta.app_access_token = nextToken;
            return nextToken;
        })();
        try {
            return await entry._tokenRefreshPromise;
        } finally {
            entry._tokenRefreshPromise = null;
        }
    }

    async function _postMcpAppJson(entry, path, payload, options = {}) {
        let nextPayload = payload || {};
        if (!options?.retryingAfterTokenRefresh && path !== '/api/v1/llm/mcp/apps/token/refresh') {
            const token = String(nextPayload.app_access_token || entry.meta?.app_access_token || '').trim();
            if (_mcpAppTokenExpiresSoon(token)) {
                const nextToken = await _refreshAppAccessToken(entry);
                nextPayload = Object.assign({}, nextPayload, { app_access_token: nextToken });
            }
        }
        try {
            return await _postJson(path, nextPayload, options);
        } catch (error) {
            if (!options?.retryingAfterTokenRefresh && _isExpiredMcpAppTokenError(error)) {
                const nextToken = await _refreshAppAccessToken(entry);
                return _postMcpAppJson(
                    entry,
                    path,
                    Object.assign({}, nextPayload || {}, { app_access_token: nextToken }),
                    Object.assign({}, options, { retryingAfterTokenRefresh: true })
                );
            }
            throw error;
        }
    }

    async function _createFrameDocument(entry, html, meta, signal) {
        // The application document is served from a short-lived URL so it gets
        // an independent response CSP. This is important for Safari: srcdoc and
        // data URL documents inherit their embedder's CSP and therefore cannot
        // loosen Omlorix's production script-src policy for MCP app scripts.
        const result = await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/frame', {
            server_id: entry.meta.server_id,
            access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
            app_access_token: entry.meta.app_access_token || '',
            tool_call_id: entry.meta.tool_call_id || '',
            html: String(html || ''),
            resource_meta: _isObject(meta) ? meta : {},
        }, { signal });
        const frameUrl = String(result?.frame_url || '').trim();
        if (!frameUrl) {
            throw new Error(_t('mcp_app_load_failed', 'Failed to load interactive app.'));
        }
        return frameUrl;
    }

    async function _fetchResource(entry, signal) {
        const resource = await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/resources/read', {
            server_id: entry.meta.server_id,
            access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
            app_access_token: entry.meta.app_access_token || '',
            tool_call_id: entry.meta.tool_call_id || '',
            uri: entry.meta.resource_uri,
        }, { signal });
        const contents = Array.isArray(resource?.contents) ? resource.contents : [];
        let content = null;
        for (const candidate of contents) {
            if (!_isObject(candidate)) continue;
            const mimeType = String(candidate.mimeType || candidate.mime_type || '').trim().toLowerCase();
            const uri = String(candidate.uri || entry.meta.resource_uri || '').trim().toLowerCase();
            if (
                (typeof candidate.text === 'string' && candidate.text)
                && (mimeType.includes('html') || uri.startsWith('ui://'))
            ) {
                content = candidate;
                break;
            }
            if (
                (typeof candidate.blob === 'string' && candidate.blob)
                && (mimeType.includes('html') || uri.startsWith('ui://'))
            ) {
                content = Object.assign({}, candidate, {
                    text: _decodeBase64Text(candidate.blob),
                });
                break;
            }
        }
        if (!content || typeof content.text !== 'string' || !content.text) {
            throw new Error(_t('mcp_app_resource_missing_html', 'MCP app resource did not return HTML.'));
        }
        const resourceMeta = _mergeResourceMeta(
            _isObject(entry.meta.resource_meta) ? entry.meta.resource_meta : {},
            _isObject(content?._meta?.ui) ? content._meta.ui : {}
        );
        return {
            html: content.text,
            meta: resourceMeta,
        };
    }

    function _setStatus(entry, label, tone = 'idle') {
        if (!entry.statusEl) return;
        entry.statusEl.textContent = label;
        entry.statusEl.dataset.state = tone;
    }

    function _updateHeader(entry) {
        if (!entry) return;
        const title = String(
            entry.meta?.resource_title
            || entry.meta?.tool_info?.title
            || entry.meta?.tool_name
            || 'MCP App'
        ).trim() || 'MCP App';
        const subtitle = String(entry.meta?.server_name || '').trim();
        if (entry.titleEl) {
            entry.titleEl.textContent = title;
        }
        if (entry.subtitleEl) {
            entry.subtitleEl.textContent = subtitle;
        }
        if (entry.iframe) {
            entry.iframe.setAttribute('title', title);
        }
    }

    function _mergeEntryMeta(currentMeta, nextMeta) {
        const current = _isObject(currentMeta) ? _deepClone(currentMeta) : {};
        const next = _isObject(nextMeta) ? _deepClone(nextMeta) : {};
        const merged = Object.assign({}, current, next);

        if (_isObject(current.resource_meta) || _isObject(next.resource_meta)) {
            merged.resource_meta = Object.assign({}, current.resource_meta || {}, next.resource_meta || {});
        }
        if (_isObject(current.tool_info) || _isObject(next.tool_info)) {
            merged.tool_info = Object.assign({}, current.tool_info || {}, next.tool_info || {});
        }
        if (Object.prototype.hasOwnProperty.call(next, 'tool_input')) {
            merged.tool_input = _deepClone(next.tool_input);
        }
        if (Object.prototype.hasOwnProperty.call(next, 'tool_input_raw_prefix')) {
            merged.tool_input_raw_prefix = typeof next.tool_input_raw_prefix === 'string'
                ? next.tool_input_raw_prefix
                : '';
        }
        if (Object.prototype.hasOwnProperty.call(next, 'tool_input_done')) {
            merged.tool_input_done = Boolean(next.tool_input_done);
        }
        if (Object.prototype.hasOwnProperty.call(next, 'tool_result')) {
            merged.tool_result = _deepClone(next.tool_result);
        }
        if (Object.prototype.hasOwnProperty.call(next, 'embedded_html')) {
            merged.embedded_html = next.embedded_html;
        }
        if (Object.prototype.hasOwnProperty.call(next, 'resource_uri')) {
            merged.resource_uri = next.resource_uri;
        }
        return merged;
    }

    function _setError(entry, message) {
        if (!entry.errorEl) return;
        entry.errorEl.hidden = false;
        entry.errorEl.textContent = message;
        _setStatus(entry, _t('mcp_app_status_error', 'Error'), 'error');
    }

    function _clearError(entry) {
        if (!entry.errorEl) return;
        entry.errorEl.hidden = true;
        entry.errorEl.textContent = '';
    }

    function _serializeForSignature(value) {
        try {
            return JSON.stringify(value ?? null);
        } catch (_) {
            return String(value ?? '');
        }
    }

    function _getEntryToolInputArguments(entry) {
        return _isObject(entry?.meta?.tool_input)
            ? _deepClone(entry.meta.tool_input)
            : {};
    }

    function _getEntryToolInputRawPrefix(entry) {
        if (typeof entry?.meta?.tool_input_raw_prefix === 'string') {
            return entry.meta.tool_input_raw_prefix;
        }
        if (_isObject(entry?.meta?.tool_input)) {
            try {
                return JSON.stringify(entry.meta.tool_input);
            } catch (_) {
                return '';
            }
        }
        return '';
    }

    function _entryHasToolResult(entry) {
        return Boolean(
            entry
            && _isObject(entry.meta)
            && Object.prototype.hasOwnProperty.call(entry.meta, 'tool_result')
            && entry.meta.tool_result != null
        );
    }

    function _entryIsStreaming(entry) {
        return (
            String(entry?.wrapper?.dataset?.mcpLive || '').trim() === 'true'
            && entry?.meta?.tool_input_done !== true
            && !_entryHasToolResult(entry)
        );
    }

    function _deliverStreamingToolInput(entry, { force = false } = {}) {
        const argumentsPayload = _getEntryToolInputArguments(entry);
        const rawArgumentsPrefix = _getEntryToolInputRawPrefix(entry);
        if (!rawArgumentsPrefix && !Object.keys(argumentsPayload).length) {
            return;
        }
        const partialSignature = _serializeForSignature({
            arguments: argumentsPayload,
            rawArgumentsPrefix,
            done: false,
        });

        if (!force && partialSignature === entry.lastPartialInputSignature) {
            return;
        }

        _notifyIframe(entry, 'ui/notifications/tool-input-partial', {
            arguments: argumentsPayload,
            rawArgumentsPrefix,
            done: false,
        });

        if (_isObject(entry?.meta?.tool_input)) {
            _notifyIframe(entry, 'ui/notifications/tool-input', {
                arguments: argumentsPayload,
                partial: true,
            });
        }

        entry.lastPartialInputSignature = partialSignature;
        _setStatus(entry, _t('mcp_app_status_streaming', 'Streaming'), 'loading');
    }

    function _deliverFinalToolHydration(entry, { force = false } = {}) {
        const argumentsPayload = _getEntryToolInputArguments(entry);
        const rawArgumentsPrefix = _getEntryToolInputRawPrefix(entry);
        const partialDoneSignature = _serializeForSignature({
            arguments: argumentsPayload,
            rawArgumentsPrefix,
            done: true,
        });

        if ((force || partialDoneSignature !== entry.lastPartialDoneSignature) && (rawArgumentsPrefix || _isObject(entry?.meta?.tool_input))) {
            _notifyIframe(entry, 'ui/notifications/tool-input-partial', {
                arguments: argumentsPayload,
                rawArgumentsPrefix: rawArgumentsPrefix || _serializeForSignature(argumentsPayload),
                done: true,
            });
            entry.lastPartialDoneSignature = partialDoneSignature;
        }

        const toolInputSignature = _serializeForSignature(argumentsPayload);
        if (force || toolInputSignature !== entry.lastToolInputSignature) {
            _notifyIframe(entry, 'ui/notifications/tool-input', {
                arguments: argumentsPayload,
            });
            entry.lastToolInputSignature = toolInputSignature;
        }

        if (_entryHasToolResult(entry)) {
            const toolResultPayload = _sanitizeToolResultForIframe(entry.meta.tool_result);
            const toolResultSignature = _serializeForSignature(toolResultPayload);
            if (force || toolResultSignature !== entry.lastToolResultSignature) {
                _notifyIframe(entry, 'ui/notifications/tool-result', toolResultPayload);
                entry.lastToolResultSignature = toolResultSignature;
            }
        }

        _setStatus(entry, _t('mcp_app_status_ready', 'Ready'), 'ready');
    }

    function _syncEntryDelivery(entry, { force = false } = {}) {
        if (!entry?.ready) {
            return;
        }

        if (_entryIsStreaming(entry)) {
            _deliverStreamingToolInput(entry, { force });
            return;
        }

        _deliverFinalToolHydration(entry, { force });
    }

    function _applyDisplayMode(entry, nextMode) {
        const mode = String(nextMode || 'inline').trim().toLowerCase();
        const isFullscreen = mode === 'fullscreen' || mode === 'expanded' || mode === 'maximized';
        const requestedMode = isFullscreen ? 'fullscreen' : 'inline';
        const declaredModes = Array.isArray(entry.appCapabilities?.availableDisplayModes)
            ? entry.appCapabilities.availableDisplayModes.map((item) => String(item || '').trim().toLowerCase())
            : [];
        if (declaredModes.length && !declaredModes.includes(requestedMode)) {
            return entry.displayMode || 'inline';
        }
        entry.displayMode = requestedMode;
        entry.cardEl?.classList.toggle('mcp-app-widget-card--expanded', isFullscreen);
        if (entry.expandBtn) {
            entry.expandBtn.textContent = isFullscreen
                ? _t('mcp_app_collapse', 'Collapse')
                : _t('mcp_app_expand', 'Expand');
            entry.expandBtn.setAttribute('aria-pressed', isFullscreen ? 'true' : 'false');
        }
        document.body.classList.toggle('mcp-app-widget-expanded', isFullscreen);
        if (entry.ready) {
            _notifyIframe(entry, 'ui/notifications/host-context-changed', _buildHostContext(entry));
        }
        return entry.displayMode;
    }

    async function _mountResource(entry) {
        if (entry._currentAbortController) {
            entry._currentAbortController.abort();
        }
        const controller = new AbortController();
        entry._currentAbortController = controller;
        if (entry.reloadButton) {
            entry.reloadButton.disabled = true;
        }
        _setStatus(entry, _t('mcp_app_status_loading', 'Loading'), 'loading');
        _clearError(entry);
        try {
            const resource = entry.meta?.embedded_html
                ? {
                    html: String(entry.meta.embedded_html || ''),
                    meta: _isObject(entry.meta.resource_meta) ? entry.meta.resource_meta : {},
                }
                : await _fetchResource(entry, controller.signal);
            if (entry._currentAbortController !== controller) {
                return;
            }
            const externalSources = _collectExternalResourceSources(resource.meta);
            const externalSignature = _externalResourceSignature(externalSources);
            if (externalSources.length && entry.allowedExternalResourceSignature !== externalSignature) {
                if (_hasSavedExternalResourceConsent(entry.meta?.server_id, externalSignature)) {
                    entry.allowedExternalResourceSignature = externalSignature;
                    await _loadResourceIntoIframe(entry, resource, controller.signal);
                    return;
                }
                entry.pendingExternalResource = { resource, externalSources, externalSignature };
                _renderExternalResourceConsent(entry, externalSources);
                return;
            }
            await _loadResourceIntoIframe(entry, resource, controller.signal);
        } catch (error) {
            if (error?.name === 'AbortError') {
                return;
            }
            if (entry._currentAbortController !== controller) {
                return;
            }
            console.error('[mcp-app] Failed to mount resource', error);
            _setError(entry, error?.message || _t('mcp_app_load_failed', 'Failed to load interactive app.'));
        } finally {
            if (entry._currentAbortController === controller) {
                entry._currentAbortController = null;
                if (entry.reloadButton) {
                    entry.reloadButton.disabled = false;
                }
            }
        }
    }

    async function _loadResourceIntoIframe(entry, resource, signal = undefined) {
        if (!entry?.iframe || !entry.wrapper?.isConnected) {
            return;
        }
        const iframe = entry.iframe;
        if (entry.consentEl) {
            entry.consentEl.hidden = true;
            entry.consentEl.replaceChildren?.();
        }
        iframe.hidden = false;
        if (entry.initialized) {
            _requestResourceTeardown(entry, 'resource-reload');
        }
        iframe.setAttribute('sandbox', SANDBOX_PROXY_IFRAME_SANDBOX);
        iframe.setAttribute('allowfullscreen', 'true');
        iframe.setAttribute('allow', _permissionsToAllowAttr());
        entry.currentFrameUrl = '';
        entry.resourceMeta = resource.meta || {};
        entry.ready = false;
        entry.initialized = false;
        entry.lastPartialInputSignature = '';
        entry.lastPartialDoneSignature = '';
        entry.lastToolInputSignature = '';
        entry.lastToolResultSignature = '';
        _setStatus(entry, _t('mcp_app_status_loading', 'Loading'), 'loading');
        const appFrameUrl = await _createFrameDocument(
            entry,
            resource.html,
            resource.meta || {},
            signal
        );
        if (signal?.aborted || !entry.wrapper?.isConnected) {
            return;
        }
        entry.currentFrameUrl = appFrameUrl;
        _setIframeDocumentUrl(entry, _sandboxProxyUrl());
    }

    function _renderExternalResourceConsent(entry, externalSources) {
        if (!entry?.consentEl || !entry.iframe) {
            return;
        }
        entry.iframe.hidden = true;
        _clearIframeDocument(entry);
        entry.ready = false;
        entry.initialized = false;
        _setStatus(entry, _t('mcp_app_status_waiting_approval', 'Waiting for approval'), 'warning');
        _clearError(entry);

        entry.consentEl.replaceChildren();
        entry.consentEl.hidden = false;

        const panel = document.createElement('div');
        panel.className = 'mcp-app-widget-consent-panel';
        panel.setAttribute('role', 'group');
        panel.setAttribute('aria-labelledby', `${entry.id}-external-title`);

        const title = document.createElement('div');
        title.className = 'mcp-app-widget-consent-title';
        title.id = `${entry.id}-external-title`;
        title.textContent = _t('mcp_app_external_resources_title', 'Allow external connections?');

        const description = document.createElement('p');
        description.className = 'mcp-app-widget-consent-description';
        description.textContent = _tf(
            'mcp_app_external_resources_desc',
            'This MCP app wants to load resources from outside Omlorix. Allow it only if you trust this app and these domains.',
            {}
        );

        const list = document.createElement('ul');
        list.className = 'mcp-app-widget-consent-domains';
        externalSources.forEach((item) => {
            const row = document.createElement('li');
            row.textContent = item.label;
            list.appendChild(row);
        });

        const actions = document.createElement('div');
        actions.className = 'mcp-app-widget-consent-actions';

        const rememberLabel = document.createElement('label');
        rememberLabel.className = 'mcp-app-widget-consent-remember';
        const rememberCheckbox = document.createElement('input');
        rememberCheckbox.type = 'checkbox';
        rememberCheckbox.className = 'mcp-app-widget-consent-remember-checkbox';
        const rememberText = document.createElement('span');
        rememberText.textContent = _t(
            'mcp_app_external_resources_remember',
            'Remember for this MCP server and these domains'
        );
        rememberLabel.appendChild(rememberCheckbox);
        rememberLabel.appendChild(rememberText);

        const allowBtn = document.createElement('button');
        allowBtn.type = 'button';
        allowBtn.className = 'mcp-app-widget-consent-btn mcp-app-widget-consent-btn--primary';
        allowBtn.textContent = _t('mcp_app_external_resources_allow', 'Allow connections');

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'mcp-app-widget-consent-btn';
        cancelBtn.textContent = _t('mcp_app_external_resources_cancel', 'Cancel');

        allowBtn.addEventListener('click', () => {
            const pending = entry.pendingExternalResource;
            if (!pending?.resource) return;
            entry.allowedExternalResourceSignature = pending.externalSignature;
            if (rememberCheckbox.checked) {
                _saveExternalResourceConsent(entry.meta?.server_id, pending.externalSignature, pending.externalSources);
            }
            entry.pendingExternalResource = null;
            allowBtn.disabled = true;
            cancelBtn.disabled = true;
            void _loadResourceIntoIframe(entry, pending.resource).catch((error) => {
                console.error('[mcp-app] Failed to load approved resource', error);
                _setError(entry, error?.message || _t('mcp_app_load_failed', 'Failed to load interactive app.'));
            }).finally(() => {
                allowBtn.disabled = false;
                cancelBtn.disabled = false;
            });
        });

        cancelBtn.addEventListener('click', () => {
            entry.pendingExternalResource = null;
            entry.consentEl.hidden = true;
            _setError(entry, _t('mcp_app_external_resources_cancelled', 'External connections were not allowed.'));
        });

        actions.appendChild(allowBtn);
        actions.appendChild(cancelBtn);
        panel.appendChild(title);
        panel.appendChild(description);
        panel.appendChild(list);
        panel.appendChild(rememberLabel);
        panel.appendChild(actions);
        entry.consentEl.appendChild(panel);
        allowBtn.focus?.();
    }

    function destroyWidget(widgetId) {
        const id = String(widgetId || '').trim();
        if (!id) return false;
        const entry = _widgetEntries.get(id);
        if (!entry) return false;

        if (entry.initialized) {
            _requestResourceTeardown(entry, 'host-destroyed');
        }

        if (entry._currentAbortController) {
            entry._currentAbortController.abort();
            entry._currentAbortController = null;
        }
        if (entry._boundWindow) {
            _windowToWidgetId.delete(entry._boundWindow);
            entry._boundWindow = null;
        }

        if (entry._onReloadClick) {
            entry.reloadButton?.removeEventListener('click', entry._onReloadClick);
        }
        if (entry._onExpandClick) {
            entry.expandBtn?.removeEventListener('click', entry._onExpandClick);
        }
        if (entry._onIframeLoad) {
            entry.iframe?.removeEventListener('load', entry._onIframeLoad);
        }

        if (entry.displayMode === 'fullscreen') {
            document.body.classList.remove('mcp-app-widget-expanded');
        }
        if (entry.wrapper?.dataset?.mcpAppWidgetId === id) {
            delete entry.wrapper.dataset.mcpAppWidgetId;
        }
        if (entry.iframe) {
            _clearIframeDocument(entry);
        }

        _widgetEntries.delete(id);
        return true;
    }

    function _cleanupDisconnectedWidgets() {
        const staleIds = [];
        _widgetEntries.forEach((entry, widgetId) => {
            if (!entry.wrapper?.isConnected) {
                staleIds.push(widgetId);
            }
        });
        staleIds.forEach((widgetId) => {
            destroyWidget(widgetId);
        });
    }

    function _ensureWidgetCleanupObserver() {
        if (_widgetRemovalObserver || typeof MutationObserver !== 'function') {
            return;
        }
        const root = document.body || document.documentElement;
        if (!root) {
            return;
        }
        _widgetRemovalObserver = new MutationObserver(() => {
            _cleanupDisconnectedWidgets();
        });
        _widgetRemovalObserver.observe(root, { childList: true, subtree: true });
    }

    function _extractMessageText(contentBlocks, structuredContent) {
        if (Array.isArray(contentBlocks)) {
            const texts = contentBlocks
                .map((block) => {
                    if (!_isObject(block)) return '';
                    if (block.type === 'text') return String(block.text || '').trim();
                    return '';
                })
                .filter(Boolean);
            if (texts.length) return texts.join('\n\n');
        }
        if (_isObject(structuredContent) || Array.isArray(structuredContent)) {
            try {
                return JSON.stringify(structuredContent, null, 2);
            } catch (_) {}
        }
        return '';
    }

    function _sanitizeToolResultForIframe(toolResult) {
        const payload = _isObject(toolResult) ? _deepClone(toolResult) : {};
        if (!_isObject(payload.structuredContent)) {
            delete payload.structuredContent;
        }
        if (!Array.isArray(payload.content)) {
            delete payload.content;
        }
        return payload;
    }

    async function _handleRequest(entry, message) {
        const id = message?.id;
        const method = String(message?.method || '').trim();
        const params = _isObject(message?.params) ? message.params : {};
        try {
            if (method === 'ui/initialize') {
                const requestedProtocolVersion = String(
                    params.protocolVersion || MCP_APPS_PROTOCOL_VERSION
                ).trim() || MCP_APPS_PROTOCOL_VERSION;
                if (!SUPPORTED_MCP_APPS_PROTOCOL_VERSIONS.has(requestedProtocolVersion)) {
                    _respondError(
                        entry,
                        id,
                        -32602,
                        `Unsupported MCP Apps protocol version: ${requestedProtocolVersion}`
                    );
                    return;
                }
                entry.appInfo = _deepClone(params.appInfo || {});
                entry.appCapabilities = _deepClone(params.appCapabilities || {});
                entry.initialized = true;
                const hostCapabilities = _buildHostCapabilities(entry);
                _respond(entry, id, {
                    protocolVersion: requestedProtocolVersion,
                    hostInfo: {
                        name: window.applicationName || 'Omlorix',
                        title: window.applicationName || 'Omlorix',
                        version: '1.0',
                    },
                    hostCapabilities,
                    capabilities: hostCapabilities,
                    hostContext: _buildHostContext(entry),
                });
                return;
            }
            if (method === 'ping') {
                _respond(entry, id, {});
                return;
            }
            if (method === 'tools/list') {
                _respond(entry, id, await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/tools/list', {
                    server_id: entry.meta.server_id,
                    access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                    app_access_token: entry.meta.app_access_token || '',
                    tool_call_id: entry.meta.tool_call_id || '',
                }));
                return;
            }
            if (method === 'resources/list') {
                _respond(entry, id, await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/resources/list', {
                    server_id: entry.meta.server_id,
                    access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                    app_access_token: entry.meta.app_access_token || '',
                    tool_call_id: entry.meta.tool_call_id || '',
                }));
                return;
            }
            if (method === 'resources/read') {
                _respond(entry, id, await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/resources/read', {
                    server_id: entry.meta.server_id,
                    access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                    app_access_token: entry.meta.app_access_token || '',
                    tool_call_id: entry.meta.tool_call_id || '',
                    uri: params.uri,
                }));
                return;
            }
            if (method === 'resources/templates/list') {
                _respond(entry, id, await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/resources/templates/list', {
                    server_id: entry.meta.server_id,
                    access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                    app_access_token: entry.meta.app_access_token || '',
                    tool_call_id: entry.meta.tool_call_id || '',
                }));
                return;
            }
            if (method === 'prompts/list') {
                _respond(entry, id, await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/prompts/list', {
                    server_id: entry.meta.server_id,
                    access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                    app_access_token: entry.meta.app_access_token || '',
                    tool_call_id: entry.meta.tool_call_id || '',
                }));
                return;
            }
            if (method === 'tools/call') {
                const result = await _postMcpAppJson(entry, '/api/v1/llm/mcp/apps/tools/call', {
                    server_id: entry.meta.server_id,
                    access_server_ids: Array.isArray(entry.meta.access_server_ids) ? entry.meta.access_server_ids : [],
                    app_access_token: entry.meta.app_access_token || '',
                    tool_call_id: entry.meta.tool_call_id || '',
                    tool_name: params.name,
                    arguments: _isObject(params.arguments) ? params.arguments : {},
                });
                _respond(entry, id, result);
                return;
            }
            if (method === 'ui/open-link' || method === 'ui/download-file') {
                const url = String(params.url || params.href || '').trim();
                if (!_safeUrl(url)) {
                    throw new Error(_t('mcp_app_blocked_unsafe_url', 'Blocked unsafe URL.'));
                }
                window.open(url, '_blank', 'noopener,noreferrer');
                _respond(entry, id, {});
                return;
            }
            if (method === 'ui/message') {
                if (String(params.role || 'user').trim().toLowerCase() !== 'user') {
                    throw new Error(_t('mcp_app_invalid_message', 'MCP apps may only send user messages.'));
                }
                const content = Array.isArray(params.content) ? params.content : [params.content];
                const text = _extractMessageText(content, params.structuredContent);
                if (!text || text.length > 100000) {
                    throw new Error(_t('mcp_app_invalid_message', 'MCP app message is empty or too large.'));
                }
                if (typeof window.sendChatMessage !== 'function') {
                    throw new Error(_t('mcp_app_message_unavailable', 'Chat message sending is unavailable.'));
                }
                if (typeof window.showWarningConfirm !== 'function') {
                    throw new Error(_t('mcp_app_message_unavailable', 'Message confirmation is unavailable.'));
                }
                const confirmed = await window.showWarningConfirm({
                    title: _t('mcp_app_message_confirm_title', 'Send MCP app message?'),
                    message: _t(
                        'mcp_app_message_confirm_desc',
                        'This MCP app wants to send the following message in your chat. Review it before continuing.'
                    ),
                    confirmLabel: _t('mcp_app_message_confirm_send', 'Send message'),
                    copyText: text,
                });
                if (!confirmed) {
                    throw new Error(_t('mcp_app_message_denied', 'Message sending was denied.'));
                }
                const sendResult = await window.sendChatMessage(text);
                if (sendResult === null || sendResult === false) {
                    throw new Error(_t('mcp_app_message_unavailable', 'Chat message sending is unavailable.'));
                }
                // Record the request only after the chat dispatcher accepts
                // it. Otherwise a busy chat would acknowledge and remember a
                // message that was never sent.
                entry.lastUiMessage = {
                    text,
                    raw: _deepClone(params),
                };
                _respond(entry, id, {});
                return;
            }
            if (method === 'ui/request-display-mode') {
                _applyDisplayMode(entry, params.mode || params.displayMode || 'inline');
                _respond(entry, id, { mode: entry.displayMode || 'inline' });
                return;
            }
            if (method === 'ui/update-model-context') {
                // Omlorix does not yet have a hidden, conversation-scoped context
                // channel. Return the protocol-defined denial instead of
                // acknowledging and silently dropping sensitive app state.
                _respondError(
                    entry,
                    id,
                    -32000,
                    _t('mcp_app_context_unsupported', 'MCP app model-context updates are not supported.')
                );
                return;
            }
            if (method === 'ui/resource-teardown') {
                _respond(entry, id, {});
                return;
            }

            _respondError(entry, id, -32601, `Unsupported MCP app method: ${method}`);
        } catch (error) {
            console.error('[mcp-app] Request failed', method, error);
            _respondError(entry, id, -32000, error?.message || 'MCP app host error');
        }
    }

    function _handleNotification(entry, message) {
        const method = String(message?.method || '').trim();
        const params = _isObject(message?.params) ? message.params : {};
        if (method === 'ui/notifications/size-changed') {
            const nextHeight = Number(params.height);
            if (Number.isFinite(nextHeight) && nextHeight > 0 && entry.stageEl) {
                const clamped = Math.max(280, Math.min(nextHeight, Math.round(window.innerHeight * 0.78)));
                entry.stageEl.style.height = `${Math.round(clamped)}px`;
            }
            return;
        }
        if (method === 'ui/notifications/initialized') {
            entry.ready = true;
            entry.initialized = true;
            _syncEntryDelivery(entry, { force: true });
            return;
        }
        if (method === 'ui/notifications/sandbox-proxy-ready') {
            if (entry.currentFrameUrl) {
                _notifyIframe(entry, 'ui/notifications/sandbox-resource-ready', {
                    url: entry.currentFrameUrl,
                    sandbox: VIEW_IFRAME_SANDBOX,
                    allow: _permissionsToAllowAttr(),
                    title: entry.titleEl?.textContent || 'MCP App',
                    csp: _deepClone(entry.resourceMeta?.csp || {}),
                    permissions: _deepClone(entry.resourceMeta?.permissions || {}),
                });
            }
            return;
        }
        if (method === 'notifications/message') {
            const level = String(params.level || 'info').toLowerCase();
            const data = params.data;
            if (level === 'error' || level === 'critical' || level === 'alert' || level === 'emergency') {
                console.error('[mcp-app]', data);
            } else if (level === 'warning') {
                console.warn('[mcp-app]', data);
            } else {
                console.log('[mcp-app]', data);
            }
        }
    }

    function _handleWindowMessage(event) {
        const widgetId = _windowToWidgetId.get(event.source);
        if (!widgetId) return;
        const entry = _widgetEntries.get(widgetId);
        if (!entry) return;
        const data = event.data;
        if (!_isObject(data) || data.jsonrpc !== '2.0') return;
        if (data.method) {
            if (data.id != null) {
                void _handleRequest(entry, data);
            } else {
                _handleNotification(entry, data);
            }
        }
    }

    function _requestResourceTeardown(entry, reason) {
        const target = entry?.iframe?.contentWindow;
        if (!target) return;
        _hostRequestCounter += 1;
        target.postMessage({
            jsonrpc: '2.0',
            id: `omlorix-teardown-${_hostRequestCounter}`,
            method: 'ui/resource-teardown',
            params: { reason: String(reason || 'host-destroyed') },
        }, '*');
    }

    function _createEntry(widgetWrapper, meta) {
        const widgetId = _nextWidgetId();
        widgetWrapper.dataset.mcpAppWidgetId = widgetId;
        widgetWrapper.dataset.widgetType = 'mcp_app';
        widgetWrapper.classList.add('mcp-app-widget');

        const prefersBorder = meta?.resource_meta?.prefersBorder;
        widgetWrapper.innerHTML = '';

        const card = document.createElement('div');
        card.className = 'mcp-app-widget-card';
        if (prefersBorder === false) {
            card.classList.add('mcp-app-widget-card--borderless');
        }

        const header = document.createElement('div');
        header.className = 'mcp-app-widget-header';

        const titleWrap = document.createElement('div');
        titleWrap.className = 'mcp-app-widget-title-wrap';
        const titleEl = document.createElement('div');
        titleEl.className = 'mcp-app-widget-title';
        titleEl.textContent = String(meta?.resource_title || meta?.tool_info?.title || meta?.tool_name || 'MCP App');
        const subtitleEl = document.createElement('div');
        subtitleEl.className = 'mcp-app-widget-subtitle';
        subtitleEl.textContent = String(meta?.server_name || '').trim();
        titleWrap.appendChild(titleEl);
        titleWrap.appendChild(subtitleEl);

        const controls = document.createElement('div');
        controls.className = 'mcp-app-widget-controls';
        const statusEl = document.createElement('span');
        statusEl.className = 'mcp-app-widget-status';
        statusEl.textContent = _t('mcp_app_status_loading', 'Loading');
        statusEl.dataset.state = 'loading';

        const reloadBtn = document.createElement('button');
        reloadBtn.type = 'button';
        reloadBtn.className = 'mcp-app-widget-control-btn mcp-app-widget-reload';
        reloadBtn.textContent = _t('mcp_app_reload', 'Reload');

        const expandBtn = document.createElement('button');
        expandBtn.type = 'button';
        expandBtn.className = 'mcp-app-widget-control-btn mcp-app-widget-expand';
        expandBtn.textContent = _t('mcp_app_expand', 'Expand');
        expandBtn.setAttribute('aria-pressed', 'false');

        controls.appendChild(statusEl);
        controls.appendChild(expandBtn);
        controls.appendChild(reloadBtn);
        header.appendChild(titleWrap);
        header.appendChild(controls);

        const errorEl = document.createElement('div');
        errorEl.className = 'mcp-app-widget-error';
        errorEl.hidden = true;

        const stageEl = document.createElement('div');
        stageEl.className = 'mcp-app-widget-stage';

        const iframe = document.createElement('iframe');
        iframe.className = 'mcp-app-widget-iframe';
        iframe.setAttribute('title', titleEl.textContent || 'MCP App');
        iframe.setAttribute('loading', 'lazy');
        const consentEl = document.createElement('div');
        consentEl.className = 'mcp-app-widget-consent';
        consentEl.hidden = true;
        stageEl.appendChild(iframe);
        stageEl.appendChild(consentEl);

        card.appendChild(header);
        card.appendChild(errorEl);
        card.appendChild(stageEl);
        widgetWrapper.appendChild(card);

        const entry = {
            id: widgetId,
            wrapper: widgetWrapper,
            cardEl: card,
            meta: _deepClone(meta || {}),
            displayMode: 'inline',
            iframe,
            statusEl,
            errorEl,
            stageEl,
            consentEl,
            expandBtn,
            reloadButton: reloadBtn,
            titleEl,
            subtitleEl,
            ready: false,
            initialized: false,
            appInfo: null,
            appCapabilities: null,
            currentFrameUrl: '',
            lastModelContext: null,
            lastUiMessage: null,
            resourceMeta: _deepClone(meta?.resource_meta || {}),
            pendingExternalResource: null,
            allowedExternalResourceSignature: '',
            lastPartialInputSignature: '',
            lastPartialDoneSignature: '',
            lastToolInputSignature: '',
            lastToolResultSignature: '',
            _boundWindow: null,
            _currentAbortController: null,
            _tokenRefreshPromise: null,
            _onReloadClick: null,
            _onExpandClick: null,
            _onIframeLoad: null,
        };

        entry._onReloadClick = () => {
            void _mountResource(entry);
        };
        entry._onExpandClick = () => {
            _applyDisplayMode(entry, entry.displayMode === 'fullscreen' ? 'inline' : 'fullscreen');
        };
        entry._onIframeLoad = () => {
            _bindIframeWindow(entry);
        };

        reloadBtn.addEventListener('click', entry._onReloadClick);
        expandBtn.addEventListener('click', entry._onExpandClick);
        iframe.addEventListener('load', entry._onIframeLoad);
        _widgetEntries.set(widgetId, entry);
        return entry;
    }

    function renderWidget(widgetWrapper, meta) {
        if (!widgetWrapper || !_isObject(meta) || !_isObject(meta.mcp_app)) {
            return false;
        }
        _ensureWidgetCleanupObserver();
        const priorWidgetId = String(widgetWrapper.dataset.mcpAppWidgetId || '').trim();
        if (priorWidgetId) {
            destroyWidget(priorWidgetId);
        }
        const entry = _createEntry(widgetWrapper, meta.mcp_app);
        _applyDisplayMode(entry, 'inline');
        void _mountResource(entry);
        return true;
    }

    function updateWidget(widgetWrapper, meta) {
        if (!widgetWrapper || !_isObject(meta) || !_isObject(meta.mcp_app)) {
            return false;
        }
        const widgetId = String(widgetWrapper.dataset.mcpAppWidgetId || '').trim();
        if (!widgetId) {
            return renderWidget(widgetWrapper, meta);
        }
        const entry = _widgetEntries.get(widgetId);
        if (!entry) {
            return renderWidget(widgetWrapper, meta);
        }

        const nextMeta = meta.mcp_app;
        const prevResourceUri = String(entry.meta?.resource_uri || '').trim();
        const prevEmbeddedHtml = String(entry.meta?.embedded_html || '').trim();
        const prevResourceMetaSignature = _serializeForSignature(entry.meta?.resource_meta || {});
        entry.meta = _mergeEntryMeta(entry.meta, nextMeta);
        entry.resourceMeta = _deepClone(entry.meta?.resource_meta || {});
        _updateHeader(entry);

        const nextResourceUri = String(entry.meta?.resource_uri || '').trim();
        const nextEmbeddedHtml = String(entry.meta?.embedded_html || '').trim();
        const nextResourceMetaSignature = _serializeForSignature(entry.meta?.resource_meta || {});
        const shouldRemount = (
            nextResourceUri !== prevResourceUri
            || (!nextResourceUri && nextEmbeddedHtml && nextEmbeddedHtml !== prevEmbeddedHtml)
            || nextResourceMetaSignature !== prevResourceMetaSignature
        );

        if (shouldRemount) {
            void _mountResource(entry);
            return true;
        }

        if (entry.ready) {
            _syncEntryDelivery(entry);
            _notifyIframe(entry, 'ui/notifications/host-context-changed', _buildHostContext(entry));
        }

        return true;
    }

    window.addEventListener('message', _handleWindowMessage);
    window.addEventListener('resize', () => {
        _widgetEntries.forEach((entry) => {
            if (entry.ready) {
                _notifyIframe(entry, 'ui/notifications/host-context-changed', _buildHostContext(entry));
            }
        });
    });
    window.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        _widgetEntries.forEach((entry) => {
            if (entry.displayMode === 'fullscreen') {
                _applyDisplayMode(entry, 'inline');
            }
        });
    });

    window.mcpAppsWidget = {
        renderWidget,
        updateWidget,
        destroyWidget,
    };
})();
