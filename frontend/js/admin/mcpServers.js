(function () {
    const state = {
        active: false,
        loading: false,
        servers: [],
        editingId: null,
        searchQuery: '',
        preview: {
            mode: 'empty',
            loading: false,
            serverId: null,
            title: '',
            tools: [],
        },
        createInitialSnapshot: null,
        editInitialSnapshot: null,
        importPayload: null,
        importServers: [],
        importSelected: new Set(),
        importFileName: '',
        importReturnFocus: null,
    };

    const t = (key, fallback) => typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);

    const formatT = (key, fallback, values = {}) => {
        let text = t(key, fallback);
        Object.entries(values).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
        });
        return text;
    };

    const TRANSPORT_OPTIONS = [
        { value: 'streamable_http', label: 'Streamable HTTP', key: 'mcp_transport_streamable_http' },
        { value: 'sse', label: 'SSE (legacy)', key: 'mcp_transport_sse_legacy' },
    ];
    let i18nListenerBound = false;

    function root() {
        return document.getElementById('mcpAdminRoot');
    }

    function getDefaultServerValues() {
        return {
            name: '',
            description: '',
            namespace: '',
            transport: 'streamable_http',
            enabled: true,
            url: '',
            headers: {},
            auth_mode: 'headers',
            allowed_tools: [],
            timeout_seconds: 30,
        };
    }

    function getServerById(serverId) {
        return state.servers.find((server) => String(server.id) === String(serverId)) || null;
    }

    function parseJsonInput(raw, label, fallback = {}) {
        if (!raw || !String(raw).trim()) return fallback;
        try {
            return JSON.parse(raw);
        } catch (_) {
            throw new Error(formatT('mcp_error_invalid_json', '{label} must be valid JSON.', { label }));
        }
    }

    async function fetchJson(url, options = {}) {
        const response = await window.authedFetch(url, options);
        if (!response.ok) {
            const payload = await response.json().catch(() => null);
            throw new Error(payload?.detail || payload?.message || `HTTP ${response.status}`);
        }
        const contentLength = response.headers.get('Content-Length');
        const contentType = response.headers.get('Content-Type') || '';
        if (response.status === 204 || contentLength === '0' || !contentType.toLowerCase().includes('application/json')) {
            return null;
        }
        return response.json().catch(() => null);
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function getTransportLabel(transport) {
        switch (String(transport || '')) {
            case 'streamable_http':
                return t('mcp_transport_streamable_http', 'Streamable HTTP');
            case 'sse':
                return t('mcp_transport_sse_legacy', 'SSE (legacy)');
            default:
                return String(transport || '');
        }
    }

    function getServerSummary(server) {
        if (server.url) return server.url;
        return getTransportLabel(server.transport);
    }

    function hasRedactedSecretMap(server, fieldName) {
        if (!server || typeof server !== 'object') return false;
        const secretSummary = server.secret_summary && typeof server.secret_summary === 'object'
            ? server.secret_summary
            : {};
        const count = Number(secretSummary.header_count || 0);
        const value = server[fieldName];
        const isEmptyMap = value && typeof value === 'object' && !Array.isArray(value)
            ? Object.keys(value).length === 0
            : !value;
        return count > 0 && isEmptyMap;
    }

    function formatSecretFieldValue(server, fieldName) {
        if (hasRedactedSecretMap(server, fieldName)) {
            return '';
        }
        const value = server?.[fieldName];
        return JSON.stringify(value || {}, null, 2);
    }

    function getServerIcon(server) {
        if (String(server.transport) === 'sse') {
            return {
                color: 'var(--admin-warning)',
            };
        }
        return {
            color: 'var(--admin-accent)',
        };
    }

    function getPreviewIcon() {
        return null;
    }

    function renderEmptyState(title, description, icon) {
        return `
            <div class="user-notifications-empty provider-empty-state">
                <div class="user-notifications-empty-icon">
                    ${Icons.wrapSvgBody(icon)}
                </div>
                <h3 class="user-notifications-empty-title">${escapeHtml(title)}</h3>
                <p class="user-notifications-empty-text">${escapeHtml(description)}</p>
            </div>
        `;
    }

    function filterServers() {
        const query = String(state.searchQuery || '').trim().toLowerCase();
        if (!query) return state.servers.slice();
        return state.servers.filter((server) => {
            const haystack = [
                server.name,
                server.description,
                server.namespace,
                server.transport,
                server.url,
            ]
                .map((value) => String(value || '').toLowerCase())
                .join('\n');
            return haystack.includes(query);
        });
    }

    function renderServerCard(server) {
        const icon = getServerIcon(server);
        const statusLabel = server.enabled
            ? t('mcp_status_enabled', 'Enabled')
            : t('mcp_status_disabled', 'Disabled');
        const description = server.description || t('mcp_field_description_desc', 'Optional note about the integration.');
        const oauthConnected = Boolean(server.secret_summary?.oauth_connected);
        const oauthAction = server.auth_mode === 'oauth'
            ? `<button type="button" class="om-button border cancel" data-action="oauth" data-id="${escapeHtml(server.id)}">${escapeHtml(oauthConnected ? t('mcp_oauth_reconnect', 'Reconnect OAuth') : t('mcp_oauth_connect', 'Connect OAuth'))}</button>`
            : '';

        return `
            <div class="admin-skill-card" data-server-id="${escapeHtml(server.id)}">
                <div class="admin-skill-icon" style="background-color: ${escapeHtml(icon.color)}">
                    ${Icons.wrapSvgBody(icon.svg, { strokeWidth: '1.7' })}
                </div>
                <div class="settings-row-left">
                    <h4 class="settings-row-title">${escapeHtml(server.name || t('mcp_field_name_label', 'Name'))}</h4>
                    <p class="settings-row-desc two-lines">${escapeHtml(description)}</p>
                    <div class="mcp-admin-meta-row">
                        <span class="mcp-admin-meta-chip">${escapeHtml(getTransportLabel(server.transport))}</span>
                        <span class="mcp-admin-meta-text">${escapeHtml(getServerSummary(server))}</span>
                    </div>
                    <div class="mcp-admin-meta-row">
                        <span class="mcp-admin-meta-text">${escapeHtml(statusLabel)}</span>
                        ${server.auth_mode === 'oauth' ? `<span class="mcp-admin-meta-text">${escapeHtml(oauthConnected ? t('mcp_oauth_connected', 'OAuth connected') : t('mcp_oauth_not_connected', 'OAuth not connected'))}</span>` : ''}
                        ${server.namespace ? `<span class="mcp-admin-meta-text">${escapeHtml(server.namespace)}</span>` : ''}
                    </div>
                </div>
                <div class="admin-skill-actions">
                    ${oauthAction}
                    <button type="button" class="om-button border cancel" data-action="edit" data-id="${escapeHtml(server.id)}">${t('mcp_action_edit', 'Edit')}</button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete" data-id="${escapeHtml(server.id)}">${t('mcp_action_delete', 'Delete')}</button>
                </div>
            </div>
        `;
    }

    function renderPreviewCard(tool) {
        return `
            <div class="admin-skill-card">
                <div class="admin-skill-icon" style="background-color: var(--admin-accent)">
                    ${Icons.wrapSvgBody(getPreviewIcon(), { strokeWidth: '1.6' })}
                </div>
                <div class="settings-row-left">
                    <h4 class="settings-row-title">${escapeHtml(tool.public_name || tool.tool_name || t('mcp_preview_tool_fallback', 'Tool'))}</h4>
                    <p class="settings-row-desc">${escapeHtml(tool.tool_name || '')}</p>
                    <p class="settings-row-desc two-lines">${escapeHtml(tool.description || t('mcp_preview_tool_no_description', 'No description provided.'))}</p>
                </div>
            </div>
        `;
    }

    function getEmptyPreviewCopy() {
        if (state.preview.mode === 'error') {
            return {
                title: t('mcp_preview_error_title', 'Tool discovery failed'),
                description: t(
                    'mcp_preview_error_desc',
                    'The connection test failed, so no successful tool discovery result is available.'
                ),
            };
        }
        if (state.preview.mode === 'draft') {
            return {
                title: t('mcp_preview_empty_title', 'No tools returned'),
                description: t(
                    'mcp_preview_empty_desc',
                    'The server responded, but it did not expose any tools for preview.'
                ),
            };
        }
        return {
            title: t('mcp_section_preview_title', 'Tool Preview'),
            description: t('mcp_tools_preview_empty', 'Run a connection test to preview available MCP tools.'),
        };
    }

    function getPreviewSubtitle() {
        if (state.preview.loading) {
            return t('mcp_preview_loading_desc', 'Loading available tools from the selected server.');
        }
        if (Array.isArray(state.preview.tools) && state.preview.tools.length) {
            return formatT(
                'mcp_preview_tools_count',
                '{count} tools available from this server.',
                { count: state.preview.tools.length }
            );
        }
        return getEmptyPreviewCopy().description;
    }

    function updatePreviewSubtitle(prefix) {
        const subtitle = document.getElementById(`${prefix}PreviewSubtitle`);
        if (subtitle) {
            subtitle.textContent = getPreviewSubtitle();
        }
    }

    function captureFormDraft(prefix) {
        const form = document.getElementById(`${prefix}Form`);
        if (!form) return null;
        return {
            id: String(document.getElementById(`${prefix}Id`)?.value || ''),
            name: String(document.getElementById(`${prefix}Name`)?.value || ''),
            description: String(document.getElementById(`${prefix}Description`)?.value || ''),
            namespace: String(document.getElementById(`${prefix}Namespace`)?.value || ''),
            transport: String(document.getElementById(`${prefix}Transport`)?.value || 'streamable_http'),
            authMode: String(document.getElementById(`${prefix}AuthMode`)?.value || 'headers'),
            timeout: String(document.getElementById(`${prefix}Timeout`)?.value || '30'),
            url: String(document.getElementById(`${prefix}Url`)?.value || '').trim(),
            headers: String(document.getElementById(`${prefix}Headers`)?.value || ''),
            allowedTools: String(document.getElementById(`${prefix}AllowedTools`)?.value || ''),
            enabled: Boolean(document.getElementById(`${prefix}Enabled`)?.checked),
        };
    }

    function restoreFormDraft(prefix, draft) {
        if (!draft) return;
        const fieldMap = {
            Id: draft.id,
            Name: draft.name,
            Description: draft.description,
            Namespace: draft.namespace,
            Transport: draft.transport,
            AuthMode: draft.authMode,
            Timeout: draft.timeout,
            Url: draft.url,
            Headers: draft.headers,
            AllowedTools: draft.allowedTools,
        };

        Object.entries(fieldMap).forEach(([field, value]) => {
            const element = document.getElementById(`${prefix}${field}`);
            if (element) {
                element.value = value;
            }
        });

        const enabled = document.getElementById(`${prefix}Enabled`);
        if (enabled) enabled.checked = Boolean(draft.enabled);

        syncFormSelects(prefix);
    }

    /**
     * Replace the two native-looking MCP form selects with Omlorix's shared,
     * keyboard-accessible single-select widget. The hidden native select stays
     * the source of truth so payload collection and existing change listeners
     * continue to use the normal form-control API.
     */
    function upgradeFormSelect(prefix, suffix) {
        const select = document.getElementById(`${prefix}${suffix}`);
        if (!select || typeof window.upgradeAdminSingleSelect !== 'function') return null;

        const meta = window.upgradeAdminSingleSelect(select, {
            key: select.id,
            placeholder: select.selectedOptions?.[0]?.textContent || '',
            emptyValueIsOption: true,
        });
        meta?.wrapper?.classList.add('mcp-server-custom-select');
        return meta;
    }

    function upgradeFormSelects(prefix) {
        upgradeFormSelect(prefix, 'Transport');
        upgradeFormSelect(prefix, 'AuthMode');
    }

    function syncFormSelects(prefix) {
        ['Transport', 'AuthMode'].forEach((suffix) => {
            document.getElementById(`${prefix}${suffix}`)?._singleSelect?.syncFromSelect?.();
        });
    }

    function renderFormPage(mode) {
        const prefix = mode === 'edit' ? 'mcpServerEdit' : 'mcpServerCreate';
        const isEdit = mode === 'edit';

        return `
            <form id="${prefix}Form" class="skill-form mcp-server-form">
                ${isEdit ? `<input type="hidden" id="${prefix}Id">` : ''}
                <div class="skill-form-row">
                    <label for="${prefix}Name">${escapeHtml(t('mcp_field_name_label', 'Name'))}</label>
                    <input type="text" id="${prefix}Name" required>
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_name_desc', 'Human-friendly server label.'))}</p>
                </div>
                <div class="skill-form-row">
                    <label for="${prefix}Description">${escapeHtml(t('mcp_field_description_label', 'Description'))}</label>
                    <textarea id="${prefix}Description" rows="3"></textarea>
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_description_desc', 'Optional note about the integration.'))}</p>
                </div>
                <div class="skill-form-row">
                    <label for="${prefix}Namespace">${escapeHtml(t('mcp_field_namespace_label', 'Namespace'))}</label>
                    <input type="text" id="${prefix}Namespace">
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_namespace_desc', 'Optional prefix for generated tool names.'))}</p>
                </div>
                <div class="skill-form-row">
                    <label id="${prefix}TransportLabel" for="${prefix}Transport">${escapeHtml(t('mcp_field_transport_label', 'Transport'))}</label>
                    <select id="${prefix}Transport" class="provider-form-control" aria-labelledby="${prefix}TransportLabel">
                        ${TRANSPORT_OPTIONS.map((option) => `
                            <option value="${escapeHtml(option.value)}">${escapeHtml(t(option.key, option.label))}</option>
                        `).join('')}
                    </select>
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_transport_desc', 'Choose how Omlorix connects to the server.'))}</p>
                </div>
                <div class="skill-form-row">
                    <label for="${prefix}Timeout">${escapeHtml(t('mcp_field_timeout_label', 'Timeout'))}</label>
                    <input type="number" id="${prefix}Timeout" min="1" max="600" value="30">
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_timeout_desc', 'Connection timeout in seconds.'))}</p>
                </div>
                <div class="skill-form-row" id="${prefix}RowUrl">
                    <label for="${prefix}Url">${escapeHtml(t('mcp_field_url_label', 'Server URL'))}</label>
                    <input type="url" id="${prefix}Url">
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_url_desc', 'Remote MCP endpoint URL.'))}</p>
                </div>
                <div class="skill-form-row" id="${prefix}RowAuthMode">
                    <label id="${prefix}AuthModeLabel" for="${prefix}AuthMode">${escapeHtml(t('mcp_field_auth_mode_label', 'Authentication'))}</label>
                    <select id="${prefix}AuthMode" class="provider-form-control" aria-labelledby="${prefix}AuthModeLabel">
                        <option value="headers">${escapeHtml(t('mcp_auth_mode_headers', 'Headers'))}</option>
                        <option value="oauth">${escapeHtml(t('mcp_auth_mode_oauth', 'OAuth 2.0'))}</option>
                    </select>
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_auth_mode_desc', 'Use static headers or authorize this saved server with OAuth.'))}</p>
                </div>
                <div class="skill-form-row" id="${prefix}RowHeaders">
                    <label for="${prefix}Headers">${escapeHtml(t('mcp_field_headers_label', 'Headers (JSON)'))}</label>
                    <textarea id="${prefix}Headers" rows="4" spellcheck="false"></textarea>
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_headers_desc', 'Optional HTTP headers for remote servers.'))}</p>
                </div>
                <div class="skill-form-row">
                    <label for="${prefix}AllowedTools">${escapeHtml(t('mcp_field_allowed_tools_label', 'Allowed tools'))}</label>
                    <textarea id="${prefix}AllowedTools" rows="4" spellcheck="false"></textarea>
                    <p class="skill-form-hint">${escapeHtml(t('mcp_field_allowed_tools_desc', 'Optional tool names, one per line. Leave empty to allow every discovered tool.'))}</p>
                </div>
                <section class="dashboard-card">
                    <div class="admin-skill-files-header">
                        <div>
                            <h4 class="admin-skill-files-title">
                                ${Icons.connections}
                                <span>${escapeHtml(t('mcp_section_runtime_title', 'Runtime Options'))}</span>
                            </h4>
                            <p class="admin-skill-files-desc">${escapeHtml(t('mcp_section_runtime_desc', 'Choose how the server is exposed.'))}</p>
                        </div>
                    </div>
                    <div class="mcp-settings-toggle-stack">
                        <div class="mcp-settings-toggle-row">
                            <div class="mcp-settings-toggle-label" id="${prefix}EnabledLabel">
                                <strong>${escapeHtml(t('mcp_field_enabled_label', 'Enabled'))}</strong>
                                <small>${escapeHtml(t('mcp_field_enabled_desc', 'Disabled servers stay saved but are never exposed.'))}</small>
                            </div>
                            <label class="toggle-switch">
                                <input type="checkbox" class="toggle-input" id="${prefix}Enabled" checked aria-labelledby="${prefix}EnabledLabel">
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                </section>
                <section class="dashboard-card">
                    <div class="dashboard-card-header">
                        <div>
                            <p class="dashboard-card-title">${escapeHtml(t('mcp_section_preview_title', 'Tool Preview'))}</p>
                            <p class="dashboard-card-description" id="${prefix}PreviewSubtitle">${escapeHtml(t('mcp_tools_preview_empty', 'Run a connection test to preview available MCP tools.'))}</p>
                        </div>
                    </div>
                    <div id="${prefix}ToolPreview"></div>
                </section>
                <div class="provider-form-actions">
                    <button type="button" class="om-button border cancel" id="${prefix}Cancel">${escapeHtml(t('btn_cancel', 'Cancel'))}</button>
                    <button type="button" class="om-button border cancel" id="${prefix}Test">${escapeHtml(t('mcp_action_test_connection', 'Test Connection'))}</button>
                    <button type="submit" class="om-button border submit" id="${prefix}Submit">${escapeHtml(t('mcp_action_save_server', 'Save Server'))}</button>
                </div>
            </form>
        `;
    }

    function renderFormPages({ force = false } = {}) {
        const createRoot = document.getElementById('mcpServerCreatePageRoot');
        if (createRoot && (force || createRoot.dataset.rendered !== 'true')) {
            createRoot.querySelectorAll('.admin-select.open').forEach((select) => select._closeMenu?.());
            createRoot.innerHTML = renderFormPage('create');
            createRoot.dataset.rendered = 'true';
            upgradeFormSelects('mcpServerCreate');
        }

        const editRoot = document.getElementById('mcpServerEditPageRoot');
        if (editRoot && (force || editRoot.dataset.rendered !== 'true')) {
            editRoot.querySelectorAll('.admin-select.open').forEach((select) => select._closeMenu?.());
            editRoot.innerHTML = renderFormPage('edit');
            editRoot.dataset.rendered = 'true';
            upgradeFormSelects('mcpServerEdit');
        }
    }

    function refreshTranslations() {
        const createDraft = captureFormDraft('mcpServerCreate');
        const editDraft = captureFormDraft('mcpServerEdit');
        const importOverlay = document.getElementById('mcpAdminImportOverlay');
        const importOverlayOpen = Boolean(importOverlay && !importOverlay.hidden && importOverlay.classList.contains('active'));

        renderFormPages({ force: true });
        restoreFormDraft('mcpServerCreate', createDraft);
        restoreFormDraft('mcpServerEdit', editDraft);
        setupCreateFormListeners();
        setupEditFormListeners();

        render();
        renderImportServersList();
        if (importOverlayOpen && state.importServers.length) {
            openImportOverlay();
        }

        renderToolPreview('mcpServerCreate');
        renderToolPreview('mcpServerEdit');
    }

    function renderServerList() {
        const host = document.getElementById('mcpAdminServerList');
        if (!host) return;

        const filteredServers = filterServers();

        if (!filteredServers.length) {
            host.innerHTML = state.searchQuery
                ? renderEmptyState(
                    t('mcp_empty_filtered_title', 'No matching servers'),
                    t('mcp_empty_filtered_desc', 'Try a different search term or clear the current filter.'),
                    null
                )
                : renderEmptyState(
                    t('mcp_list_empty_title', 'No admin MCP servers yet'),
                    t('mcp_list_empty', 'No admin MCP servers configured yet.'),
                    null
                );
            return;
        }

        host.innerHTML = filteredServers.map(renderServerCard).join('');
    }

    function renderToolPreview(prefix) {
        const host = document.getElementById(`${prefix}ToolPreview`);
        if (!host) return;
        updatePreviewSubtitle(prefix);

        if (state.preview.loading) {
            host.innerHTML = renderEmptyState(
                t('mcp_preview_loading_title', 'Loading preview'),
                t('mcp_preview_loading_desc', 'Loading available tools from the selected server.'),
                null
            );
            return;
        }

        if (!Array.isArray(state.preview.tools) || !state.preview.tools.length) {
            const copy = getEmptyPreviewCopy();
            host.innerHTML = renderEmptyState(copy.title, copy.description, getPreviewIcon());
            return;
        }

        host.innerHTML = `
            <div class="mcp-admin-meta-row" style="margin-bottom: 12px;">
                <span class="mcp-admin-meta-chip">${escapeHtml(t('mcp_preview_draft_label', 'Draft preview'))}</span>
                <span class="mcp-admin-meta-text">${escapeHtml(state.preview.title || '')}</span>
            </div>
            <div class="admin-skills-list">
                ${state.preview.tools.map(renderPreviewCard).join('')}
            </div>
        `;
    }

    function setPreviewState(nextPreview, prefix) {
        state.preview = {
            mode: nextPreview.mode || 'empty',
            loading: Boolean(nextPreview.loading),
            serverId: nextPreview.serverId ?? null,
            title: nextPreview.title || '',
            tools: Array.isArray(nextPreview.tools) ? nextPreview.tools : [],
        };
        renderToolPreview(prefix);
    }

    function isPageActive(pageEl) {
        return Boolean(pageEl && !pageEl.hidden);
    }

    function applyFormValues(server, prefix) {
        const values = {
            name: server.name || '',
            description: server.description || '',
            namespace: server.namespace || '',
            transport: server.transport || 'streamable_http',
            auth_mode: server.auth_mode || 'headers',
            enabled: Boolean(server.enabled),
            url: server.url || '',
            headers: formatSecretFieldValue(server, 'headers'),
            allowed_tools: Array.isArray(server.allowed_tools) ? server.allowed_tools.join('\n') : '',
            timeout_seconds: String(server.timeout_seconds || 30),
        };

        const fieldNames = {
            auth_mode: 'AuthMode',
            timeout_seconds: 'Timeout',
            allowed_tools: 'AllowedTools',
        };
        Object.entries(values).forEach(([key, value]) => {
            const suffix = fieldNames[key] || `${key.charAt(0).toUpperCase()}${key.slice(1)}`;
            const element = document.getElementById(`${prefix}${suffix}`);
            if (!element) return;
            if (element.type === 'checkbox') {
                element.checked = Boolean(value);
            } else {
                element.value = value;
            }
        });

        syncFormSelects(prefix);
    }

    function collectPayload(prefix) {
        const transport = String(document.getElementById(`${prefix}Transport`)?.value || 'streamable_http').trim();
        const existingServer = prefix === 'mcpServerEdit' ? getServerById(state.editingId) : null;
        const headersRaw = document.getElementById(`${prefix}Headers`)?.value || '';
        const payload = {
            owner_type: 'admin',
            name: String(document.getElementById(`${prefix}Name`)?.value || '').trim(),
            description: String(document.getElementById(`${prefix}Description`)?.value || '').trim(),
            namespace: String(document.getElementById(`${prefix}Namespace`)?.value || '').trim(),
            transport,
            auth_mode: String(document.getElementById(`${prefix}AuthMode`)?.value || 'headers'),
            enabled: Boolean(document.getElementById(`${prefix}Enabled`)?.checked),
            url: String(document.getElementById(`${prefix}Url`)?.value || '').trim(),
            timeout_seconds: Number.parseInt(document.getElementById(`${prefix}Timeout`)?.value || '30', 10) || 30,
            allowed_tools: String(document.getElementById(`${prefix}AllowedTools`)?.value || '')
                .split(/\r?\n|,/g)
                .map((item) => item.trim())
                .filter(Boolean),
        };

        if (headersRaw.trim() || !hasRedactedSecretMap(existingServer, 'headers')) {
            payload.headers = parseJsonInput(
                headersRaw || '{}',
                t('mcp_field_headers_label', 'Headers (JSON)'),
                {}
            );
        }
        if (!payload.name) {
            throw new Error(t('mcp_validation_name_required', 'Server name is required.'));
        }
        if (!payload.url) {
            throw new Error(t('mcp_validation_url_required', 'Server URL is required for remote servers.'));
        }

        return payload;
    }

    function setButtonBusy(buttonId, busy, label) {
        const button = document.getElementById(buttonId);
        if (!button) return;
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = button.textContent || '';
        }
        button.disabled = Boolean(busy);
        button.textContent = busy ? label : button.dataset.defaultLabel;
    }

    function currentMcpExportVersion() {
        return 2.0;
    }

    function setImportStatus(message = '', kind = '') {
        const status = document.getElementById('mcpAdminImportStatus');
        if (!status) return;
        if (!message) {
            status.hidden = true;
            status.textContent = '';
            status.className = 'provider-import-status';
            return;
        }
        status.hidden = false;
        status.textContent = message;
        status.className = `provider-import-status ${kind}`.trim();
    }

    function resetImportState() {
        state.importPayload = null;
        state.importServers = [];
        state.importSelected = new Set();
        state.importFileName = '';
        const list = document.getElementById('mcpAdminImportList');
        if (list) list.innerHTML = '';
        const fileName = document.getElementById('mcpAdminImportFileName');
        if (fileName) fileName.textContent = '';
        const selectAll = document.getElementById('mcpAdminImportSelectAll');
        if (selectAll) selectAll.checked = false;
        setImportStatus();
    }

    function closeImportOverlay() {
        const overlay = document.getElementById('mcpAdminImportOverlay');
        overlay?.classList.remove('active');
        if (overlay) {
            overlay.setAttribute('aria-hidden', 'true');
            overlay.hidden = true;
        }
        state.importReturnFocus?.focus?.();
        state.importReturnFocus = null;
        resetImportState();
    }

    function openImportOverlay() {
        const overlay = document.getElementById('mcpAdminImportOverlay');
        if (!overlay) return;
        if (!state.importReturnFocus) {
            state.importReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('active');
        const fileName = document.getElementById('mcpAdminImportFileName');
        if (fileName) fileName.textContent = state.importFileName || '';
        const selectAll = document.getElementById('mcpAdminImportSelectAll');
        if (selectAll) {
            selectAll.checked = state.importServers.length > 0
                && state.importServers.length === state.importSelected.size;
        }
        setImportStatus();
        document.getElementById('mcpAdminImportConfirm')?.focus();
    }

    function resolveImportServersFromPayload(payload) {
        if (!payload || typeof payload !== 'object') {
            throw new Error(t('mcp_import_invalid_export', 'Invalid export file.'));
        }
        if (payload.export_type !== 'mcp_server') {
            throw new Error(t('mcp_import_unsupported_type', 'Unsupported export file type.'));
        }
        if (payload.export_version !== currentMcpExportVersion()) {
            throw new Error(t('mcp_import_version_mismatch', 'Unsupported export version. Expected 2.0.'));
        }
        const servers = payload?.data?.servers;
        return Array.isArray(servers) ? servers : [];
    }

    function renderImportServersList() {
        const host = document.getElementById('mcpAdminImportList');
        if (!host) return;
        host.innerHTML = '';
        if (!state.importServers.length) {
            host.innerHTML = `<div class="provider-import-empty">${escapeHtml(t('mcp_import_empty', 'No MCP servers found in this file.'))}</div>`;
            return;
        }

        const fragment = document.createDocumentFragment();
        state.importServers.forEach((server, index) => {
            const selected = state.importSelected.has(index);
            const entry = document.createElement('label');
            entry.className = 'provider-import-entry';
            entry.setAttribute('role', 'option');
            entry.setAttribute('aria-selected', selected ? 'true' : 'false');

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = selected;
            checkbox.dataset.serverIndex = String(index);
            checkbox.addEventListener('change', handleImportServerToggle);
            entry.appendChild(checkbox);

            const content = document.createElement('div');
            content.className = 'provider-import-entry-content';

            const title = document.createElement('p');
            title.className = 'provider-import-entry-title';
            title.textContent = server?.name || t('mcp_field_name_label', 'Name');
            content.appendChild(title);

            const description = document.createElement('div');
            description.className = 'provider-import-entry-meta';
            description.textContent = server?.description || getTransportLabel(server?.transport);
            content.appendChild(description);

            const meta = document.createElement('div');
            meta.className = 'provider-import-entry-meta';
            meta.textContent = `${getTransportLabel(server?.transport)} · ${getServerSummary(server)}`;
            content.appendChild(meta);

            entry.appendChild(content);
            fragment.appendChild(entry);
        });
        host.appendChild(fragment);
    }

    function handleImportServerToggle(event) {
        const checkbox = event.currentTarget;
        const index = Number.parseInt(checkbox.dataset.serverIndex || '', 10);
        if (Number.isNaN(index)) return;
        if (checkbox.checked) {
            state.importSelected.add(index);
        } else {
            state.importSelected.delete(index);
        }
        checkbox.closest('.provider-import-entry')?.setAttribute('aria-selected', checkbox.checked ? 'true' : 'false');
        const selectAll = document.getElementById('mcpAdminImportSelectAll');
        if (selectAll) {
            selectAll.checked = state.importServers.length > 0
                && state.importServers.length === state.importSelected.size;
        }
        setImportStatus();
    }

    function toggleSelectAllImports(event) {
        const checked = Boolean(event.currentTarget?.checked);
        state.importSelected.clear();
        if (checked) {
            state.importServers.forEach((_, index) => state.importSelected.add(index));
        }
        renderImportServersList();
        setImportStatus();
    }

    function formatImportErrorEntry(entry) {
        if (!entry || typeof entry !== 'object') return '';
        const rawIndex = entry.index !== undefined ? Number(entry.index) : NaN;
        const displayIndex = Number.isFinite(rawIndex) ? rawIndex + 1 : '?';
        const name = entry.name ? ` (${entry.name})` : '';
        const message = entry.error
            ? (typeof entry.error === 'string' ? entry.error : JSON.stringify(entry.error))
            : t('mcp_import_error_unknown', 'Unknown error.');
        return `• ${formatT('mcp_import_error_item', 'Item {index}{name}: {message}', {
            index: displayIndex,
            name,
            message,
        })}`;
    }

    function getCreateFormSnapshot() {
        return JSON.stringify({
            name: String(document.getElementById('mcpServerCreateName')?.value || '').trim(),
            description: String(document.getElementById('mcpServerCreateDescription')?.value || '').trim(),
            namespace: String(document.getElementById('mcpServerCreateNamespace')?.value || '').trim(),
            transport: String(document.getElementById('mcpServerCreateTransport')?.value || 'streamable_http'),
            authMode: String(document.getElementById('mcpServerCreateAuthMode')?.value || 'headers'),
            url: String(document.getElementById('mcpServerCreateUrl')?.value || '').trim(),
            headers: String(document.getElementById('mcpServerCreateHeaders')?.value || '').trim(),
            allowedTools: String(document.getElementById('mcpServerCreateAllowedTools')?.value || '').trim(),
            timeout: String(document.getElementById('mcpServerCreateTimeout')?.value || '30'),
            enabled: Boolean(document.getElementById('mcpServerCreateEnabled')?.checked),
        });
    }

    function getEditFormSnapshot() {
        return JSON.stringify({
            serverId: String(state.editingId || ''),
            name: String(document.getElementById('mcpServerEditName')?.value || '').trim(),
            description: String(document.getElementById('mcpServerEditDescription')?.value || '').trim(),
            namespace: String(document.getElementById('mcpServerEditNamespace')?.value || '').trim(),
            transport: String(document.getElementById('mcpServerEditTransport')?.value || 'streamable_http'),
            authMode: String(document.getElementById('mcpServerEditAuthMode')?.value || 'headers'),
            url: String(document.getElementById('mcpServerEditUrl')?.value || '').trim(),
            headers: String(document.getElementById('mcpServerEditHeaders')?.value || '').trim(),
            allowedTools: String(document.getElementById('mcpServerEditAllowedTools')?.value || '').trim(),
            timeout: String(document.getElementById('mcpServerEditTimeout')?.value || '30'),
            enabled: Boolean(document.getElementById('mcpServerEditEnabled')?.checked),
        });
    }

    function showListPage() {
        if (typeof window.showPage === 'function') {
            window.showPage('mcp-settings');
        }
        loadServers();
    }

    function showCreatePage() {
        state.editingId = null;
        const defaultValues = getDefaultServerValues();
        applyFormValues(defaultValues, 'mcpServerCreate');
        setPreviewState({ mode: 'empty', loading: false, serverId: null, title: '', tools: [] }, 'mcpServerCreate');
        state.createInitialSnapshot = getCreateFormSnapshot();
        if (typeof window.showPage === 'function') {
            window.showPage('mcp-settings-create');
        }
    }

    async function showEditPage(serverId) {
        try {
            const server = await fetchJson(`/api/v1/llm/mcp/servers/admin/${encodeURIComponent(serverId)}`);
            state.editingId = serverId;
            const existingIndex = state.servers.findIndex((item) => String(item.id) === String(serverId));
            if (existingIndex >= 0) {
                state.servers.splice(existingIndex, 1, server);
            } else {
                state.servers.push(server);
            }
            applyFormValues(server, 'mcpServerEdit');
            renderServerList();
            setPreviewState({ mode: 'empty', loading: false, serverId: null, title: '', tools: [] }, 'mcpServerEdit');
            state.editInitialSnapshot = getEditFormSnapshot();
            if (typeof window.showPage === 'function') {
                window.showPage('mcp-settings-edit');
            }
        } catch (error) {
            window.notifyError?.(error.message || t('mcp_load_details_failed', 'Failed to load MCP server details.'));
        }
    }

    function resetFormValues(prefix) {
        const snapshot = prefix === 'mcpServerCreate' ? state.createInitialSnapshot : state.editInitialSnapshot;
        if (snapshot) {
            const parsed = JSON.parse(snapshot);
            applyFormValues(parsed, prefix);
        } else {
            applyFormValues(getDefaultServerValues(), prefix);
        }
    }

    async function loadServers() {
        if (state.loading) return;
        state.loading = true;
        try {
            const servers = await fetchJson('/api/v1/llm/mcp/servers/admin');
            if (!state.active) return;
            state.servers = Array.isArray(servers) ? servers : [];

            renderServerList();
        } catch (error) {
            window.notifyError?.(error.message || t('mcp_load_list_failed', 'Failed to load MCP servers.'));
        } finally {
            state.loading = false;
        }
    }

    async function exportServers() {
        try {
            setButtonBusy('mcpAdminExportBtn', true, t('admin_exporting_ellipsis', 'Exporting...'));
            const payload = await fetchJson('/api/v1/llm/mcp/servers/admin/export');
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const timestamp = new Date().toISOString().replace(/[:\.]/g, '-');
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `mcp-servers-${timestamp}.json`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);
            window.notifySuccess?.(t('mcp_export_success', 'MCP servers export downloaded successfully.'));
        } catch (error) {
            window.notifyError?.(error.message || t('mcp_export_failed', 'Failed to export MCP servers.'));
        } finally {
            setButtonBusy('mcpAdminExportBtn', false, '');
        }
    }

    async function handleImportFile(event) {
        const input = event?.target;
        if (!input?.files?.length) return;
        const [file] = input.files;
        input.value = '';
        const isJsonFile = file && (file.type === 'application/json' || file.name?.toLowerCase().endsWith('.json'));
        if (!isJsonFile) {
            window.notifyError?.(t('mcp_import_select_json', 'Please select a valid JSON file.'));
            return;
        }

        try {
            const payload = JSON.parse(await file.text());
            const servers = resolveImportServersFromPayload(payload);
            if (!servers.length) {
                window.notifyWarning?.(t('mcp_import_empty', 'No MCP servers found in this file.'));
                return;
            }
            state.importPayload = payload;
            state.importServers = servers;
            state.importSelected = new Set(servers.map((_, index) => index));
            state.importFileName = file.name || 'mcp-servers.json';
            renderImportServersList();
            openImportOverlay();
        } catch (error) {
            window.notifyError?.(error.message || t('mcp_import_failed', 'Failed to import MCP servers.'));
        }
    }

    async function submitSelectedImports() {
        if (!state.importPayload) {
            setImportStatus(t('mcp_import_choose_file_first', 'Please choose an MCP servers file first.'));
            return;
        }
        if (!state.importSelected.size) {
            setImportStatus(t('mcp_import_select_one', 'Select at least one MCP server to import.'));
            return;
        }

        try {
            setButtonBusy('mcpAdminImportConfirm', true, t('admin_importing_ellipsis', 'Importing...'));
            const selectedIndices = Array.from(state.importSelected).sort((a, b) => a - b);
            const payload = {
                ...state.importPayload,
                data: {
                    ...(state.importPayload.data || {}),
                    servers: selectedIndices.map((index) => state.importServers[index]).filter(Boolean),
                },
            };
            const result = await fetchJson('/api/v1/llm/mcp/servers/admin/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const createdCount = Array.isArray(result?.created) ? result.created.length : 0;
            const errorCount = Array.isArray(result?.errors) ? result.errors.length : 0;
            if (createdCount) {
                window.notifySuccess?.(
                    createdCount === 1
                        ? t('mcp_import_success_single', 'Imported 1 MCP server successfully.')
                        : formatT('mcp_import_success_plural', 'Imported {count} MCP servers successfully.', { count: createdCount })
                );
            }
            if (errorCount) {
                const details = result.errors.map((entry) => formatImportErrorEntry(entry)).filter(Boolean).join('\n');
                setImportStatus(details || t('mcp_import_partial_failed', 'Some MCP servers could not be imported.'));
                window.notifyWarning?.(t('mcp_import_partial_failed', 'Some MCP servers could not be imported.'));
            } else {
                closeImportOverlay();
            }
            await loadServers();
        } catch (error) {
            setImportStatus(error.message || t('mcp_import_failed', 'Failed to import MCP servers.'));
            window.notifyError?.(error.message || t('mcp_import_failed', 'Failed to import MCP servers.'));
        } finally {
            setButtonBusy('mcpAdminImportConfirm', false, '');
        }
    }

    async function testServer(prefix) {
        const buttonId = prefix === 'mcpServerCreate' ? 'mcpServerCreateTest' : 'mcpServerEditTest';
        setButtonBusy(buttonId, true, t('admin_testing', 'Testing...'));
        try {
            const payload = collectPayload(prefix);
            try {
                if (prefix === 'mcpServerEdit' && state.editingId) {
                    payload.server_id = String(state.editingId);
                }
                const result = await fetchJson('/api/v1/llm/mcp/servers/admin/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                setPreviewState({
                    mode: 'draft',
                    loading: false,
                    serverId: state.editingId,
                    title: payload.name || t('mcp_preview_draft_label', 'Draft preview'),
                    tools: result?.tools || [],
                }, prefix);
                window.notifySuccess?.(t('mcp_connect_success', 'Connection successful.'));
            } catch (_) {
                setPreviewState({
                    mode: 'error',
                    loading: false,
                    serverId: state.editingId,
                    title: payload.name,
                    tools: [],
                }, prefix);
                // Backend MCP details are deliberately safe but backend-authored
                // English. Keep request failures locale-owned at the UI boundary.
                window.notifyError?.(t('mcp_connect_failed', 'Failed to connect to MCP server.'));
            }
        } catch (error) {
            // Payload validation errors are generated from translated frontend
            // copy and should remain actionable.
            window.notifyError?.(error.message || t('mcp_connect_failed', 'Failed to connect to MCP server.'));
        } finally {
            setButtonBusy(buttonId, false, '');
        }
    }

    async function saveServer(prefix) {
        const buttonId = prefix === 'mcpServerCreate' ? 'mcpServerCreateSubmit' : 'mcpServerEditSubmit';
        setButtonBusy(buttonId, true, t('admin_saving', 'Saving...'));
        try {
            const payload = collectPayload(prefix);
            const method = state.editingId ? 'PATCH' : 'POST';
            const url = state.editingId
                ? `/api/v1/llm/mcp/servers/admin/${encodeURIComponent(state.editingId)}`
                : '/api/v1/llm/mcp/servers/admin';
            // Ownership is selected by the endpoint and is immutable. It is
            // required for create requests, but the PATCH schema rejects it.
            if (method === 'PATCH') delete payload.owner_type;
            await fetchJson(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            await loadServers();
            showListPage();
            window.notifySuccess?.(t('mcp_save_success', 'MCP server saved.'));
        } catch (error) {
            window.notifyError?.(error.message || t('mcp_save_failed', 'Failed to save MCP server.'));
        } finally {
            setButtonBusy(buttonId, false, '');
        }
    }

    async function connectOAuth(serverId) {
        try {
            const result = await fetchJson(`/api/v1/llm/mcp/servers/admin/${encodeURIComponent(serverId)}/oauth/start`, {
                method: 'POST',
            });
            if (!result?.authorization_url) throw new Error('Missing authorization URL');
            const authorizationUrl = new URL(result.authorization_url, window.location.href);
            if (!['http:', 'https:'].includes(authorizationUrl.protocol)) {
                throw new Error('Unsafe authorization URL');
            }
            window.location.assign(authorizationUrl.href);
        } catch (_) {
            window.notifyError?.(t('mcp_oauth_start_failed', 'Could not start OAuth authorization.'));
        }
    }

    function handleOAuthRedirectStatus() {
        const url = new URL(window.location.href);
        const status = url.searchParams.get('mcp_oauth_status');
        if (!status) return;
        if (status === 'connected') {
            window.notifySuccess?.(t('mcp_oauth_connect_success', 'OAuth connection established.'));
        } else {
            window.notifyError?.(t('mcp_oauth_connect_failed', 'OAuth authorization failed or was cancelled.'));
        }
        url.searchParams.delete('mcp_oauth_status');
        url.searchParams.delete('mcp_server_id');
        window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    }

    async function deleteServer(serverId) {
        if (!await window.showDeleteConfirm({
            message: t('mcp_delete_confirm', 'Delete this MCP server?'),
            confirmLabel: t('btn_delete', 'Delete'),
        })) return;
        try {
            await fetchJson(`/api/v1/llm/mcp/servers/admin/${encodeURIComponent(serverId)}`, { method: 'DELETE' });
            if (String(state.preview.serverId) === String(serverId)) {
                state.preview = { mode: 'empty', loading: false, serverId: null, title: '', tools: [] };
            }
            await loadServers();
            window.notifySuccess?.(t('mcp_delete_success', 'MCP server deleted.'));
        } catch (error) {
            window.notifyError?.(error.message || t('mcp_delete_failed', 'Failed to delete MCP server.'));
        }
    }

    function hasPendingChanges() {
        const createPage = document.getElementById('page-mcp-settings-create');
        const editPage = document.getElementById('page-mcp-settings-edit');
        if (isPageActive(createPage)) {
            if (state.createInitialSnapshot === null) return false;
            return getCreateFormSnapshot() !== state.createInitialSnapshot;
        }
        if (isPageActive(editPage)) {
            if (state.editInitialSnapshot === null) return false;
            return getEditFormSnapshot() !== state.editInitialSnapshot;
        }
        return false;
    }

    function handleBackNavigation() {
        const createPage = document.getElementById('page-mcp-settings-create');
        const editPage = document.getElementById('page-mcp-settings-edit');
        if (!isPageActive(createPage) && !isPageActive(editPage)) {
            return;
        }
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            window.unsavedChangesManager.confirmIfNeeded({
                onConfirm: () => showListPage(),
            });
        } else {
            showListPage();
        }
    }

    function render() {
        const container = root();
        if (!container) return;

        container.innerHTML = `
            <div class="admin-toolbar">
                <div class="admin-toolbar-left">
                    <div class="admin-table-search" role="search">
                        ${Icons.magnifyingGlass}
                        <input id="mcpAdminSearchInput" class="admin-search-input" type="text" placeholder="${escapeHtml(t('mcp_search_placeholder', 'Search MCP servers'))}" autocomplete="off" spellcheck="false">
                        <button type="button" class="admin-search-clear" id="mcpAdminSearchClear" aria-label="${escapeHtml(t('search_clear_aria', 'Clear search'))}" hidden>
                        ${Icons.close}
                        </button>
                    </div>
                </div>
                <div class="admin-toolbar-right">
                    <button type="button" class="om-button border cancel" id="mcpAdminExportBtn">${t('mcp_export_all_btn', 'Export All')}</button>
                    <button type="button" class="om-button border cancel" id="mcpAdminImportBtn">${t('mcp_import_all_btn', 'Import All')}</button>
                    <input type="file" id="mcpAdminImportFileInput" accept=".json,application/json" hidden>
                    <button type="button" class="om-button border submit" id="mcpAdminAddBtn">${t('mcp_action_add_server', 'Add Server')}</button>
                </div>
            </div>
            <div class="admin-skills-list" id="mcpAdminServerList"></div>
            <div id="mcpAdminImportModalMount"></div>
        `;

        document.getElementById('mcpAdminImportModalMount')?.appendChild(window.DeleteWarningModal.create({
            id: 'mcpAdminImportOverlay',
            cardClass: 'delete-warning-card--import shared-modal--wide',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'mcpAdminImportTitle',
            ariaDescribedby: 'mcpAdminImportSubtitle',
            contentHtml: `
                <header class="provider-import-header shared-modal-header shared-modal-header--main">
                    <div class="shared-modal-heading">
                        <h2 class="provider-import-title shared-modal-title" id="mcpAdminImportTitle">${t('mcp_import_title', 'Import MCP Servers')}</h2>
                        <p class="provider-import-subtitle shared-modal-subtitle" id="mcpAdminImportSubtitle">${t('mcp_import_subtitle', 'Select the MCP servers you want to import from this file.')}</p>
                    </div>
                    <button type="button" class="provider-import-close shared-modal-close" id="mcpAdminImportClose" aria-label="${escapeHtml(t('modal_close_import_aria', 'Close import dialog'))}">${Icons.close}</button>
                </header>
                <div class="provider-import-shared-body shared-modal-body">
                    <div class="provider-import-controls">
                        <label class="provider-import-select-all">
                            <input type="checkbox" id="mcpAdminImportSelectAll" checked>
                            <span>${t('modal_select_all', 'Select all')}</span>
                        </label>
                        <div class="provider-import-file" id="mcpAdminImportFileName"></div>
                    </div>
                    <div class="provider-import-list" id="mcpAdminImportList" role="listbox" aria-multiselectable="true"></div>
                    <div class="provider-import-status" id="mcpAdminImportStatus" role="alert" hidden></div>
                </div>
            `,
            actions: [
                { id: 'mcpAdminImportCancel', role: 'cancel', variant: 'cancel', text: t('btn_cancel', 'Cancel') },
                { id: 'mcpAdminImportConfirm', variant: 'submit', text: t('modal_import_selected', 'Import Selected') },
            ],
        }));

        document.getElementById('mcpAdminAddBtn')?.addEventListener('click', showCreatePage);
        document.getElementById('mcpAdminExportBtn')?.addEventListener('click', exportServers);
        document.getElementById('mcpAdminImportBtn')?.addEventListener('click', () => document.getElementById('mcpAdminImportFileInput')?.click());
        document.getElementById('mcpAdminImportFileInput')?.addEventListener('change', handleImportFile);
        document.getElementById('mcpAdminImportOverlay')?.addEventListener('click', (event) => {
            if (event.target === document.getElementById('mcpAdminImportOverlay')) {
                closeImportOverlay();
            }
        });
        document.getElementById('mcpAdminImportOverlay')?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeImportOverlay();
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(event.currentTarget.querySelectorAll('button:not(:disabled), input:not(:disabled)'));
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
        document.getElementById('mcpAdminImportClose')?.addEventListener('click', closeImportOverlay);
        document.getElementById('mcpAdminImportCancel')?.addEventListener('click', closeImportOverlay);
        document.getElementById('mcpAdminImportConfirm')?.addEventListener('click', submitSelectedImports);
        document.getElementById('mcpAdminImportSelectAll')?.addEventListener('change', toggleSelectAllImports);

        const searchInput = document.getElementById('mcpAdminSearchInput');
        const searchClear = document.getElementById('mcpAdminSearchClear');
        if (searchInput) {
            searchInput.value = state.searchQuery;
        }
        if (searchClear) {
            searchClear.hidden = !String(state.searchQuery || '').trim();
        }
        searchInput?.addEventListener('input', () => {
            state.searchQuery = String(searchInput.value || '');
            if (searchClear) searchClear.hidden = !state.searchQuery.trim();
            renderServerList();
        });
        searchClear?.addEventListener('click', () => {
            if (searchInput) {
                searchInput.value = '';
                searchInput.focus();
            }
            state.searchQuery = '';
            searchClear.hidden = true;
            renderServerList();
        });

        document.getElementById('mcpAdminServerList')?.addEventListener('click', (event) => {
            const actionEl = event.target.closest('[data-action]');
            if (!actionEl) return;
            const serverId = actionEl.dataset.id;
            if (!serverId) return;
            if (actionEl.dataset.action === 'edit') {
                showEditPage(serverId);
                return;
            }
            if (actionEl.dataset.action === 'oauth') {
                connectOAuth(serverId);
                return;
            }
            if (actionEl.dataset.action === 'delete') {
                deleteServer(serverId);
            }
        });

        renderServerList();
    }

    function setupCreateFormListeners() {
        const form = document.getElementById('mcpServerCreateForm');
        if (!form || form.dataset.bound === 'true') return;

        document.getElementById('mcpServerCreateCancel')?.addEventListener('click', handleBackNavigation);
        document.getElementById('mcpServerCreateTransport')?.addEventListener('change', () => syncFormSelects('mcpServerCreate'));
        document.getElementById('mcpServerCreateTest')?.addEventListener('click', () => testServer('mcpServerCreate'));
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            saveServer('mcpServerCreate');
        });
        form.dataset.bound = 'true';
    }

    function setupEditFormListeners() {
        const form = document.getElementById('mcpServerEditForm');
        if (!form || form.dataset.bound === 'true') return;

        document.getElementById('mcpServerEditCancel')?.addEventListener('click', handleBackNavigation);
        document.getElementById('mcpServerEditTransport')?.addEventListener('change', () => syncFormSelects('mcpServerEdit'));
        document.getElementById('mcpServerEditTest')?.addEventListener('click', () => testServer('mcpServerEdit'));
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            saveServer('mcpServerEdit');
        });
        form.dataset.bound = 'true';
    }

    function registerUnsavedGuard() {
        if (typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: 'mcp-servers-form-unsaved',
            priority: 170,
            isActive: () => {
                const createPage = document.getElementById('page-mcp-settings-create');
                const editPage = document.getElementById('page-mcp-settings-edit');
                return isPageActive(createPage) || isPageActive(editPage);
            },
            isDirty: () => hasPendingChanges(),
            discard: () => {
                const createPage = document.getElementById('page-mcp-settings-create');
                const editPage = document.getElementById('page-mcp-settings-edit');
                if (isPageActive(createPage)) {
                    state.createInitialSnapshot = getCreateFormSnapshot();
                }
                if (isPageActive(editPage)) {
                    state.editInitialSnapshot = getEditFormSnapshot();
                }
            },
            getCopy: () => ({
                subtitle: t('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
            }),
        });
    }

    window.initMcpServersPage = () => {
        state.active = true;
        handleOAuthRedirectStatus();
        renderFormPages();
        render();
        const backButton = document.getElementById('mcpSettingsBack');
        if (backButton && backButton.dataset.bound !== 'true') {
            backButton.addEventListener('click', () => window.activateAdminPage?.('tools'));
            backButton.dataset.bound = 'true';
        }
        setupCreateFormListeners();
        setupEditFormListeners();

        if (isPageActive(document.getElementById('page-mcp-settings-create')) && state.createInitialSnapshot === null) {
            applyFormValues(getDefaultServerValues(), 'mcpServerCreate');
            setPreviewState({ mode: 'empty', loading: false, serverId: null, title: '', tools: [] }, 'mcpServerCreate');
            state.createInitialSnapshot = getCreateFormSnapshot();
        }

        registerUnsavedGuard();
        if (!i18nListenerBound) {
            document.addEventListener('i18n:updated', () => {
                if (state.active) {
                    refreshTranslations();
                }
            });
            i18nListenerBound = true;
        }
        loadServers();
    };

    window.teardownMcpServersPage = () => {
        state.active = false;
        const container = root();
        if (container) container.innerHTML = '';
    };
})();
