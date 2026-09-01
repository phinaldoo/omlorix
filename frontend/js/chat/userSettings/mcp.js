(() => {
    const formRenderer = window.CreateEditFormRenderer;
    if (!formRenderer) {
        throw new Error('CreateEditFormRenderer must load before mcp.js');
    }

    const state = {
        allow: false,
        loaded: false,
        loading: false,
        saving: false,
        testing: false,
        domReady: false,
        view: 'list',          // 'list' | 'editor'
        editingId: null,       // null when creating
        deleteOpen: false,
        deleteReturnFocus: null,
        pendingDeleteId: null,
        servers: [],
        editorPreview: {
            label: '',
            status: 'idle', // 'idle' | 'success' | 'error'
            tools: [],
            tested_at: null,
            error: '',
        },
        editorPreviewMinHeight: 0,
        editorPreviewSectionMinHeight: 0,
        editorIconControl: null,
        editorInitialSnapshot: null,
    };

    function t(key, fallback = '') {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function tf(key, fallback = '', variables = {}) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, variables);
        }
        return Object.entries(variables).reduce((text, [name, value]) => (
            text.replace(new RegExp(`\\{${escapeRegExp(name)}\\}`, 'g'), String(value))
        ), fallback);
    }

    function escapeRegExp(value) {
        return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function pluralKey(baseKey, count) {
        return `${baseKey}_${Number(count) === 1 ? 'one' : 'other'}`;
    }

    function getTransportOptions() {
        return [
            { value: 'streamable_http', label: t('workspace_connections_mcp_transport_streamable_http', 'Streamable HTTP') },
            { value: 'sse', label: t('workspace_connections_mcp_transport_sse', 'SSE (legacy)') },
        ];
    }

    function fieldLabel(fieldId) {
        const labels = {
            mcpField_name: t('workspace_connections_mcp_field_name', 'Server name'),
            mcpField_url: t('workspace_connections_mcp_field_url', 'Server URL'),
            mcpField_headers: t('workspace_connections_mcp_field_headers', 'Headers'),
            mcpField_timeout_seconds: t('workspace_connections_mcp_field_timeout', 'Timeout'),
        };
        return labels[fieldId] || fieldId;
    }

    function getRoot() {
        return document.getElementById('connectionsPersonalMcpRoot');
    }

    function getConnectionsSection() {
        return document.getElementById('workspaceSectionConnections');
    }

    function getPersonalSection() {
        return document.getElementById('connectionsPersonalSection');
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function safeHtmlTranslation(key, fallback = '', variables = {}) {
        const escapedVariables = Object.fromEntries(
            Object.entries(variables).map(([name, value]) => [name, escapeHtml(value)])
        );
        return tf(key, fallback, escapedVariables);
    }

    const DEFAULT_MCP_ICON_MARKUP = Icons.server.trim();

    function renderServerIcon(iconValue) {
        const rawValue = typeof iconValue === 'string' ? iconValue.trim() : '';
        const picker = window.IconPicker;
        const resolved = typeof picker?.resolveIconValue === 'function'
            ? picker.resolveIconValue(rawValue)
            : { type: rawValue ? 'custom' : 'empty' };
        const markup = typeof picker?.renderIconMarkup === 'function'
            ? picker.renderIconMarkup(rawValue, { fallback: DEFAULT_MCP_ICON_MARKUP })
            : DEFAULT_MCP_ICON_MARKUP;

        return {
            markup: markup || DEFAULT_MCP_ICON_MARKUP,
            isCustom: Boolean(rawValue),
            type: resolved?.type || 'empty',
        };
    }

    function parseJsonInput(raw, label, fallback = {}) {
        if (!raw || !String(raw).trim()) return fallback;
        try {
            return JSON.parse(raw);
        } catch (_) {
            throw new Error(tf('workspace_connections_mcp_json_valid_error', '{label} must be valid JSON.', { label }));
        }
    }

    async function fetchJson(url, options = {}) {
        const response = await window.authedFetch(url, options);
        if (!response.ok) {
            const payload = await response.json().catch(() => null);
            throw new Error(payload?.detail || payload?.message || `HTTP ${response.status}`);
        }
        return response.json();
    }

    /**
     * The active model's settings schema includes the user's available MCP
     * servers. Request a refresh after a successful mutation so the selector
     * immediately reflects the server that was added or changed.
     */
    function requestActiveModelSettingsRefresh() {
        window.dispatchEvent?.(new CustomEvent('modelSettings:refreshRequested'));
    }

    function formatTransport(value) {
        const match = getTransportOptions().find((option) => option.value === value);
        return match ? match.label : String(value || t('workspace_connections_mcp_transport_unknown', 'Unknown transport'));
    }

    function currentServer() {
        if (!state.editingId) return null;
        return state.servers.find((server) => String(server.id) === String(state.editingId)) || null;
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

    function setButtonBusy(button, busy, busyLabel) {
        if (!button) return;
        if (busy) {
            if (!button.dataset.originalText) {
                button.dataset.originalText = button.textContent || '';
            }
            button.textContent = busyLabel;
            button.disabled = true;
            return;
        }
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
    }

    /* ----------------------------------------
       View switching (list <-> editor page)
       ---------------------------------------- */

    function applyEditorVisibility() {
        const section = getConnectionsSection();
        if (!section) return;
        section.classList.toggle('mcp-editor-active', state.view === 'editor');
    }

    function editorSnapshot() {
        const values = captureEditorFormValues();
        return values ? JSON.stringify(values) : null;
    }

    function showList({ force = false } = {}) {
        if (!force && state.view === 'editor' && state.editorInitialSnapshot !== null
            && editorSnapshot() !== state.editorInitialSnapshot
            && typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            window.unsavedChangesManager.confirmIfNeeded({
                id: 'personal-mcp-editor-unsaved',
                onConfirm: () => showList({ force: true }),
            });
            return;
        }
        state.view = 'list';
        state.editingId = null;
        state.editorPreviewMinHeight = 0;
        state.editorPreviewSectionMinHeight = 0;
        state.editorIconControl = null;
        state.editorInitialSnapshot = null;
        applyEditorVisibility();
        renderRoot();
    }

    function showEditor() {
        state.view = 'editor';
        applyEditorVisibility();
        renderRoot();
        state.editorInitialSnapshot = editorSnapshot();
    }

    /* ----------------------------------------
       Server list (skills-design)
       ---------------------------------------- */

    function serverCardHtml(server) {
        const serverName = server.name || t('workspace_connections_mcp_unnamed_server', 'Unnamed server');
        const title = escapeHtml(serverName);
        const description = escapeHtml(server.description || server.url || t('workspace_connections_mcp_no_description', 'No description'));
        const toggleLabel = server.enabled
            ? t('workspace_connections_mcp_enabled', 'Enabled')
            : t('workspace_connections_mcp_disabled', 'Disabled');
        const toggleAccessibleLabel = escapeHtml(`${toggleLabel}: ${serverName}`);
        const editLabel = escapeHtml(t('workspace_connections_mcp_edit', 'Edit'));
        const deleteLabel = escapeHtml(t('workspace_connections_mcp_delete', 'Delete'));
        const oauthConnected = Boolean(server.secret_summary?.oauth_connected);
        const oauthLabel = escapeHtml(oauthConnected
            ? t('workspace_connections_mcp_oauth_reconnect', 'Reconnect OAuth')
            : t('workspace_connections_mcp_oauth_connect', 'Connect OAuth'));
        const icon = renderServerIcon(server.icon);
        return `
            <div class="workspace-skills-footer skill-card mcp-server-card-skin" data-server-id="${escapeHtml(server.id)}">
                <div class="skill-entry-main">
                    <div class="skill-entry-icon ${icon.isCustom ? 'is-custom-icon' : 'is-default-icon'}">
                        <span class="mcp-server-icon-slot" aria-hidden="true">${icon.markup}</span>
                    </div>
                    <div class="skill-entry-copy">
                        <p class="skill-entry-title">${title}</p>
                        <p class="skill-entry-content">${description}</p>
                    </div>
                </div>
                <div class="workspace-skills-footer-actions skill-footer-actions">
                    ${server.auth_mode === 'oauth' ? `
                    <button type="button" class="skill-action-btn mcp-card-icon-action" data-action="oauth" data-server-id="${escapeHtml(server.id)}" aria-label="${oauthLabel}" title="${oauthLabel}">
                        ${Icons.connections}
                    </button>` : ''}
                    <button type="button" class="skill-action-btn mcp-card-icon-action" data-action="edit" data-server-id="${escapeHtml(server.id)}" aria-label="${editLabel}" title="${editLabel}">
                        ${Icons.create}
                    </button>
                    <button type="button" class="skill-action-btn danger mcp-card-icon-action" data-action="delete" data-server-id="${escapeHtml(server.id)}" aria-label="${deleteLabel}" title="${deleteLabel}">
                        ${Icons.trash}
                    </button>
                    <label class="toggle-switch mcp-server-toggle" title="${toggleAccessibleLabel}">
                        <input
                            type="checkbox"
                            class="toggle-input"
                            role="switch"
                            data-action="toggle"
                            data-server-id="${escapeHtml(server.id)}"
                            aria-label="${toggleAccessibleLabel}"
                            ${server.enabled ? 'checked' : ''}
                        >
                        <span class="toggle-slider" aria-hidden="true"></span>
                    </label>
                </div>
            </div>
        `;
    }

    function listViewHtml() {
        let body;
        if (state.loading && !state.servers.length) {
            body = `<div class="skills-loading"><div class="skills-loading-spinner"></div><p>${escapeHtml(t('workspace_connections_mcp_loading', 'Loading MCP servers...'))}</p></div>`;
        } else if (!state.servers.length) {
            body = `
                <div class="workspace-notifications-empty workspace-empty-grid">
                    <div class="workspace-notifications-empty-icon">
                        ${Icons.server}
                    </div>
                    <p class="workspace-notifications-empty-title">${escapeHtml(t('workspace_connections_mcp_empty_title', 'No personal MCP servers yet'))}</p>
                    <p class="workspace-notifications-empty-text">${escapeHtml(t('workspace_connections_mcp_empty_text', 'Add your first remote MCP endpoint to unlock private tools in chats that support MCP.'))}</p>
                </div>
            `;
        } else {
            body = `<div class="skills-grid">${state.servers.map(serverCardHtml).join('')}</div>`;
        }

        return `
            <div class="projects-header connections-shell-header connections-action-header">
                <div>
                    <p class="projects-header-title">${escapeHtml(t('workspace_connections_mcp_list_title', 'Custom MCP connections'))}</p>
                </div>
                <div class="connections-header-actions">
                    <button class="om-button border" type="button" id="mcpAddServerBtn">
                        ${Icons.plus}<span>${escapeHtml(t('workspace_connections_mcp_add_server', 'Add server'))}</span>
                    </button>
                </div>
            </div>
            ${body}
            <div id="mcpDeleteDialogMount"></div>
        `;
    }

    /* ----------------------------------------
       Editor (separate page like projects)
       ---------------------------------------- */

    function defaultServerValues() {
        return {
            name: '',
            icon: '',
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

    function editorViewHtml() {
        const editing = currentServer();
        const values = { ...defaultServerValues(), ...(editing || {}) };
        const isEdit = Boolean(editing);
        const title = isEdit
            ? t('workspace_connections_mcp_edit_title', 'Edit MCP server')
            : t('workspace_connections_mcp_create_title', 'Create a new MCP server');

        const transportOptions = getTransportOptions().map((opt) =>
            `<option value="${opt.value}" ${values.transport === opt.value ? 'selected' : ''}>${escapeHtml(opt.label)}</option>`
        ).join('');
        const authModeOptions = [
            { value: 'headers', label: t('workspace_connections_mcp_auth_headers', 'Headers') },
            { value: 'oauth', label: t('workspace_connections_mcp_auth_oauth', 'OAuth 2.0') },
        ].map((opt) =>
            `<option value="${opt.value}" ${values.auth_mode === opt.value ? 'selected' : ''}>${escapeHtml(opt.label)}</option>`
        ).join('');

        const field = (options) => formRenderer.renderControlField(options);
        const fields = [
            field({
                label: { key: 'workspace_connections_mcp_field_name', fallback: 'Server name' },
                control: {
                    id: 'mcpField_name',
                    value: values.name,
                    placeholder: t('workspace_connections_mcp_name_placeholder', 'My MCP Server'),
                },
            }),
            formRenderer.renderField({
                label: {
                    key: 'workspace_connections_mcp_field_icon',
                    fallback: 'Icon',
                    attributes: { for: 'mcpField_iconPickerHost' },
                },
                contentHtml: `
                    <div id="mcpField_iconPickerHost" class="mcp-icon-picker-host"></div>
                    ${formRenderer.renderFieldMessage({
                        tag: 'p',
                        className: 'mcp-icon-field-hint',
                        key: 'workspace_connections_mcp_icon_hint',
                        fallback: 'Choose a preset, custom SVG, or uploaded image for this MCP server.',
                    }, 'mcp-icon-field-hint')}`,
            }),
            field({
                label: { key: 'workspace_connections_mcp_field_description', fallback: 'Description' },
                control: {
                    tag: 'textarea',
                    id: 'mcpField_description',
                    value: values.description,
                    placeholder: t('workspace_connections_mcp_description_placeholder', 'Knowledge base and internal docs'),
                    attributes: { rows: 3 },
                },
            }),
            field({
                label: { key: 'workspace_connections_mcp_field_url', fallback: 'Server URL' },
                control: { id: 'mcpField_url', type: 'url', value: values.url, placeholder: 'https://example.com/mcp' },
            }),
            field({
                label: {
                    key: 'workspace_connections_mcp_field_transport',
                    fallback: 'Transport',
                    id: 'mcpField_transportLabel',
                },
                control: {
                    tag: 'select',
                    id: 'mcpField_transport',
                    contentHtml: transportOptions,
                    attributes: { 'aria-labelledby': 'mcpField_transportLabel' },
                },
            }),
            field({
                label: {
                    key: 'workspace_connections_mcp_field_auth_mode',
                    fallback: 'Authentication',
                    id: 'mcpField_auth_modeLabel',
                },
                control: {
                    tag: 'select',
                    id: 'mcpField_auth_mode',
                    contentHtml: authModeOptions,
                    attributes: { 'aria-labelledby': 'mcpField_auth_modeLabel' },
                },
                afterControlHtml: `<small>${escapeHtml(t('workspace_connections_mcp_auth_mode_desc', 'Use static headers or authorize the saved server with OAuth.'))}</small>`,
            }),
            field({
                label: { key: 'workspace_connections_mcp_field_namespace', fallback: 'Namespace' },
                control: {
                    id: 'mcpField_namespace',
                    value: values.namespace,
                    placeholder: t('workspace_connections_mcp_namespace_placeholder', 'notion'),
                },
            }),
            field({
                label: { key: 'workspace_connections_mcp_field_headers_json', fallback: 'Headers (JSON)' },
                control: {
                    tag: 'textarea',
                    id: 'mcpField_headers',
                    className: 'projects-create-textarea mcp-code-input',
                    value: formatSecretFieldValue(values, 'headers'),
                    placeholder: '{"Authorization":"Bearer ..."}',
                    attributes: { rows: 5 },
                },
            }),
            field({
                label: { key: 'workspace_connections_mcp_field_timeout_seconds', fallback: 'Timeout (seconds)' },
                control: {
                    id: 'mcpField_timeout_seconds',
                    type: 'number',
                    value: String(values.timeout_seconds || 30),
                    attributes: { min: 1, max: 600 },
                },
            }),
            field({
                label: { key: 'workspace_connections_mcp_field_allowed_tools', fallback: 'Allowed tools' },
                control: {
                    tag: 'textarea',
                    id: 'mcpField_allowed_tools',
                    className: 'projects-create-textarea mcp-code-input',
                    value: (values.allowed_tools || []).join('\n'),
                    placeholder: 'search\nread_document',
                    attributes: { rows: 4 },
                },
                afterControlHtml: `<small>${escapeHtml(t('workspace_connections_mcp_field_allowed_tools_desc', 'Optional tool names, one per line. Leave empty to allow every discovered tool.'))}</small>`,
            }),
        ].join('');
        const enabledToggle = formRenderer.renderToggleCard({
            id: 'mcpField_enabled',
            label: { key: 'workspace_connections_mcp_enabled', fallback: 'Enabled' },
            description: { key: 'workspace_connections_mcp_enabled_desc', fallback: 'Saved and available in chats.' },
            inputAttributes: { checked: values.enabled },
        });
        const preview = `
            <section class="mcp-preview" id="mcpEditorPreviewSection" data-status="${escapeHtml(state.editorPreview.status || 'idle')}">
                <header class="mcp-preview-header">
                    <div class="mcp-preview-header-icon" aria-hidden="true">${Icons.tool}</div>
                    <div class="mcp-preview-header-text">
                        <h3>${escapeHtml(t('workspace_connections_mcp_preview_title', 'Live tool preview'))}</h3>
                        <p>${escapeHtml(t('workspace_connections_mcp_preview_desc', 'Run a connection test to discover the tools your MCP server exposes.'))}</p>
                    </div>
                    <div class="mcp-preview-header-meta">
                        <span class="mcp-preview-status-pill" id="mcpEditorPreviewStatus">
                            <span class="mcp-preview-status-dot" aria-hidden="true"></span>
                            <span class="mcp-preview-status-label" id="mcpEditorPreviewStatusLabel">${escapeHtml(t('workspace_connections_mcp_status_not_tested', 'Not tested'))}</span>
                        </span>
                        <span class="mcp-preview-count" id="mcpEditorPreviewCount" hidden></span>
                        <button class="mcp-preview-test-btn" id="mcpEditorTestBtn" type="button">${escapeHtml(t('workspace_connections_mcp_test_connection', 'Test connection'))}</button>
                    </div>
                </header>
                <div class="mcp-preview-body" id="mcpEditorPreviewHost"></div>
            </section>`;
        const actions = formRenderer.renderActions({
            className: 'projects-create-buttons',
            buttons: [
                { id: 'mcpEditorCancelBtn', className: 'om-button border', key: 'workspace_connections_mcp_cancel', fallback: 'Cancel' },
                {
                    id: 'mcpEditorSaveBtn',
                    className: 'om-button border submit',
                    key: isEdit ? 'workspace_connections_mcp_save_changes' : 'workspace_connections_mcp_create_server',
                    fallback: isEdit ? 'Save changes' : 'Create server',
                },
            ],
        });

        return formRenderer.renderPage({
            contentClass: 'projects-content mcp-editor-page',
            pageHidden: false,
            title: { key: isEdit ? 'workspace_connections_mcp_edit_title' : 'workspace_connections_mcp_create_title', fallback: title },
            bodyHtml: `${fields}${enabledToggle}${preview}${actions}`,
        });
    }

    function statusLabelFor(status) {
        if (status === 'success') return t('workspace_connections_mcp_status_connected', 'Connected');
        if (status === 'error') return t('workspace_connections_mcp_status_failed', 'Failed');
        if (status === 'testing') return t('workspace_connections_mcp_status_testing', 'Testing...');
        return t('workspace_connections_mcp_status_not_tested', 'Not tested');
    }

    function toolParamChips(tool) {
        const schema = tool.input_schema || tool.inputSchema || {};
        const props = (schema && typeof schema === 'object') ? (schema.properties || {}) : {};
        const required = new Set(Array.isArray(schema?.required) ? schema.required : []);
        const entries = Object.entries(props).slice(0, 8);
        if (!entries.length) {
            return `<p class="mcp-tool-card-no-params">${escapeHtml(t('workspace_connections_mcp_no_params', 'No input parameters.'))}</p>`;
        }
        const chips = entries.map(([key, def]) => {
            const type = (def && (def.type || (Array.isArray(def.enum) ? 'enum' : 'any'))) || 'any';
            const isReq = required.has(key);
            return `<span class="mcp-tool-param ${isReq ? 'is-required' : ''}" title="${escapeHtml(def?.description || '')}">
                <span class="mcp-tool-param-name">${escapeHtml(key)}</span>
                <span class="mcp-tool-param-type">${escapeHtml(String(type))}</span>
                ${isReq ? `<span class="mcp-tool-param-required" aria-label="${escapeHtml(t('workspace_connections_mcp_required_aria', 'required'))}">*</span>` : ''}
            </span>`;
        }).join('');
        const more = Object.keys(props).length - entries.length;
        const moreChip = more > 0 ? `<span class="mcp-tool-param is-more">${escapeHtml(tf('workspace_connections_mcp_more_params', '+{count} more', { count: more }))}</span>` : '';
        return `<div class="mcp-tool-params">${chips}${moreChip}</div>`;
    }

    function applyEditorPreviewHeightFloor(host = document.getElementById('mcpEditorPreviewHost')) {
        if (!host) return;
        const minHeight = Number(state.editorPreviewMinHeight || 0);
        if (minHeight > 0) {
            host.style.minHeight = `${minHeight}px`;
        } else {
            host.style.removeProperty('min-height');
        }
    }

    function applyEditorPreviewSectionHeightFloor(section = document.getElementById('mcpEditorPreviewSection')) {
        if (!section) return;
        const minHeight = Number(state.editorPreviewSectionMinHeight || 0);
        if (minHeight > 0) {
            section.style.minHeight = `${minHeight}px`;
        } else {
            section.style.removeProperty('min-height');
        }
    }

    function lockEditorPreviewHeightFloor() {
        const host = document.getElementById('mcpEditorPreviewHost');
        const section = document.getElementById('mcpEditorPreviewSection');
        if (!host && !section) return;

        // Keep the preview from collapsing when a retest temporarily replaces
        // a populated tool grid with the loading/empty preview state.
        if (host) {
            const currentHostHeight = Math.ceil(host.getBoundingClientRect().height);
            state.editorPreviewMinHeight = Math.max(Number(state.editorPreviewMinHeight || 0), currentHostHeight);
            applyEditorPreviewHeightFloor(host);
        }
        if (section) {
            const currentSectionHeight = Math.ceil(section.getBoundingClientRect().height);
            state.editorPreviewSectionMinHeight = Math.max(
                Number(state.editorPreviewSectionMinHeight || 0),
                currentSectionHeight
            );
            applyEditorPreviewSectionHeightFloor(section);
        }
    }

    function growEditorPreviewHeightFloor(host = document.getElementById('mcpEditorPreviewHost')) {
        if (!host || !state.editorPreviewMinHeight) return;

        // If the new tool catalog needs more room, remember the larger height
        // so later loading/error states cannot pull the bottom of the form up.
        const renderedHeight = Math.ceil(host.scrollHeight);
        if (renderedHeight > state.editorPreviewMinHeight) {
            state.editorPreviewMinHeight = renderedHeight;
            applyEditorPreviewHeightFloor(host);
        }
    }

    function renderEditorPreview() {
        const section = document.getElementById('mcpEditorPreviewSection');
        const host = document.getElementById('mcpEditorPreviewHost');
        const statusLabel = document.getElementById('mcpEditorPreviewStatusLabel');
        const countEl = document.getElementById('mcpEditorPreviewCount');
        if (!host) return;

        const status = state.editorPreview.status || 'idle';
        const tools = state.editorPreview.tools || [];

        applyEditorPreviewHeightFloor(host);
        applyEditorPreviewSectionHeightFloor(section);
        if (section) section.dataset.status = status;
        if (statusLabel) statusLabel.textContent = statusLabelFor(status);
        if (countEl) {
            if (status === 'success') {
                countEl.hidden = false;
                countEl.textContent = tf(
                    pluralKey('workspace_connections_mcp_tool_count', tools.length),
                    tools.length === 1 ? '{count} tool' : '{count} tools',
                    { count: tools.length }
                );
            } else {
                countEl.hidden = true;
                countEl.textContent = '';
            }
        }

        if (status === 'error') {
            host.innerHTML = `
                <div class="mcp-preview-empty mcp-preview-empty--error">
                    <div class="mcp-preview-empty-icon" aria-hidden="true">
                        ${Icons.error}
                    </div>
                    <h4>${escapeHtml(t('workspace_connections_mcp_preview_error_title', 'Connection failed'))}</h4>
                    <p>${escapeHtml(state.editorPreview.error || t('workspace_connections_mcp_preview_error_desc', 'Could not reach the MCP server. Check the URL, headers, and network access.'))}</p>
                </div>
            `;
            growEditorPreviewHeightFloor(host);
            return;
        }

        if (!tools.length) {
            host.innerHTML = `
                <div class="mcp-preview-empty">
                    <div class="mcp-preview-empty-icon" aria-hidden="true">
                        ${Icons.protection}
                    </div>
                    <h4>${escapeHtml(t('workspace_connections_mcp_preview_empty_title', 'No tools discovered yet'))}</h4>
                    <p>${tf('workspace_connections_mcp_preview_empty_text', 'Click <strong>Test connection</strong> to fetch the tool catalog from your MCP endpoint. We will show every tool, its description, and parameters here before you save.', {})}</p>
                </div>
            `;
            growEditorPreviewHeightFloor(host);
            return;
        }

        host.innerHTML = `<div class="mcp-tools-grid">${tools.map((tool) => {
            const display = tool.public_name || tool.tool_name || t('workspace_connections_mcp_unnamed_tool', 'Unnamed tool');
            const slug = tool.tool_name && tool.tool_name !== display ? tool.tool_name : '';
            const initial = (display.match(/[A-Za-z0-9]/) || ['T'])[0].toUpperCase();
            return `
                <article class="mcp-tool-card-v2">
                    <header class="mcp-tool-card-v2-head">
                        <span class="mcp-tool-card-v2-avatar" aria-hidden="true">${escapeHtml(initial)}</span>
                        <div class="mcp-tool-card-v2-titles">
                            <strong>${escapeHtml(display)}</strong>
                            ${slug ? `<code class="mcp-tool-card-v2-slug">${escapeHtml(slug)}</code>` : ''}
                        </div>
                        <span class="mcp-tool-card-v2-chip">MCP</span>
                    </header>
                    <p class="mcp-tool-card-v2-description">${escapeHtml(tool.description || t('workspace_connections_mcp_no_description_provided', 'No description provided.'))}</p>
                    ${toolParamChips(tool)}
                </article>
            `;
        }).join('')}</div>`;
        growEditorPreviewHeightFloor(host);
    }

    /* ----------------------------------------
       Delete dialog (reused from common warning styles)
       ---------------------------------------- */

    function mountDeleteDialog(root) {
        const mount = root?.querySelector('#mcpDeleteDialogMount');
        if (!mount || document.getElementById('mcpDeleteOverlay')) return;
        const overlay = window.DeleteWarningModal?.create({
            id: 'mcpDeleteOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'mcpDeleteTitle',
            icon: 'warning',
            title: { id: 'mcpDeleteTitle', text: escapeHtml(t('workspace_connections_mcp_delete_title', 'Delete MCP server?')) },
            descriptions: [{ id: 'mcpDeleteDesc' }],
            actions: [
                { id: 'mcpDeleteCancelBtn', role: 'cancel', variant: 'cancel', text: escapeHtml(t('workspace_connections_mcp_cancel', 'Cancel')) },
                { id: 'mcpDeleteConfirmBtn', variant: 'danger', text: escapeHtml(t('workspace_connections_mcp_delete_server', 'Delete server')) },
            ],
        });
        if (overlay) mount.replaceChildren(overlay);
    }

    function openDeleteDialog(serverId) {
        const server = state.servers.find((item) => String(item.id) === String(serverId));
        if (!server) return;
        const overlay = document.getElementById('mcpDeleteOverlay');
        const descEl = document.getElementById('mcpDeleteDesc');
        if (!overlay) return;
        state.pendingDeleteId = serverId;
        state.deleteOpen = true;
        state.deleteReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        if (descEl) {
            descEl.innerHTML = safeHtmlTranslation(
                'workspace_connections_mcp_delete_desc',
                'Remove <strong>{name}</strong> from your personal integrations. This action cannot be undone.',
                { name: server.name || t('workspace_connections_mcp_this_server', 'this server') }
            );
        }
        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        document.getElementById('mcpDeleteCancelBtn')?.focus();
    }

    function closeDeleteDialog() {
        const overlay = document.getElementById('mcpDeleteOverlay');
        state.deleteOpen = false;
        state.pendingDeleteId = null;
        if (overlay) {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
        }
        state.deleteReturnFocus?.focus?.();
        state.deleteReturnFocus = null;
    }

    async function confirmDelete() {
        if (!state.pendingDeleteId) return;
        const id = state.pendingDeleteId;
        const confirmBtn = document.getElementById('mcpDeleteConfirmBtn');
        try {
            if (confirmBtn) confirmBtn.disabled = true;
            await fetchJson(`/api/v1/llm/mcp/servers/user/${encodeURIComponent(id)}`, { method: 'DELETE' });
            requestActiveModelSettingsRefresh();
            closeDeleteDialog();
            // Reloading the list is a best-effort UI update. A successful
            // deletion must not be reported as failed if that separate read
            // request cannot complete.
            void loadServers(true, { silent: true }).catch(() => {});
            window.notifySuccess?.(t('workspace_connections_mcp_success_deleted', 'MCP server deleted.'));
            if (state.view === 'editor' && String(state.editingId) === String(id)) {
                showList();
            }
        } catch (error) {
            window.notifyError?.(t('workspace_connections_mcp_error_delete', 'Failed to delete MCP server.'));
        } finally {
            if (confirmBtn) confirmBtn.disabled = false;
        }
    }

    /* ----------------------------------------
       Editor I/O
       ---------------------------------------- */

    function clearFieldErrors() {
        const root = getRoot();
        if (!root) return;
        root.querySelectorAll('.mcp-field-error').forEach((el) => el.remove());
        root.querySelectorAll('.projects-create-input-group.has-error').forEach((el) => {
            el.classList.remove('has-error');
        });
        root.querySelectorAll('.projects-create-input.is-invalid, .projects-create-textarea.is-invalid').forEach((el) => {
            el.classList.remove('is-invalid');
            el.removeAttribute('aria-invalid');
            el.removeAttribute('aria-errormessage');
        });
    }

    function setFieldError(fieldId, message) {
        const field = document.getElementById(fieldId);
        if (!field) return;
        field.classList.add('is-invalid');
        field.setAttribute('aria-invalid', 'true');
        const group = field.closest('.projects-create-input-group');
        if (group) {
            group.classList.add('has-error');
            let msgEl = group.querySelector('.mcp-field-error');
            if (!msgEl) {
                msgEl = document.createElement('p');
                msgEl.className = 'mcp-field-error';
                msgEl.id = `${fieldId}Error`;
                group.appendChild(msgEl);
            }
            msgEl.textContent = message;
            field.setAttribute('aria-errormessage', msgEl.id);
        }
        const onInput = () => {
            field.classList.remove('is-invalid');
            field.removeAttribute('aria-invalid');
            field.removeAttribute('aria-errormessage');
            const g = field.closest('.projects-create-input-group');
            if (g) {
                g.classList.remove('has-error');
                g.querySelector('.mcp-field-error')?.remove();
            }
            field.removeEventListener('input', onInput);
            field.removeEventListener('change', onInput);
        };
        field.addEventListener('input', onInput);
        field.addEventListener('change', onInput);
    }

    function focusFirstError() {
        const root = getRoot();
        if (!root) return;
        const first = root.querySelector('.is-invalid');
        if (!first) return;
        first.scrollIntoView({ behavior: 'smooth', block: 'center' });
        try { first.focus({ preventScroll: true }); } catch (_) { first.focus(); }
    }

    function readField(id) {
        return String(document.getElementById(id)?.value ?? '').trim();
    }

    function readRawField(id) {
        return String(document.getElementById(id)?.value ?? '');
    }

    function captureEditorFormValues() {
        if (state.view !== 'editor' || !document.getElementById('mcpField_name')) return null;
        return {
            name: readRawField('mcpField_name'),
            icon: typeof state.editorIconControl?.getValue === 'function'
                ? String(state.editorIconControl.getValue() || '')
                : '',
            description: readRawField('mcpField_description'),
            url: readRawField('mcpField_url'),
            transport: readRawField('mcpField_transport') || 'streamable_http',
            auth_mode: readRawField('mcpField_auth_mode') || 'headers',
            namespace: readRawField('mcpField_namespace'),
            headersRaw: readRawField('mcpField_headers'),
            timeout_seconds: readRawField('mcpField_timeout_seconds') || '30',
            allowedToolsRaw: readRawField('mcpField_allowed_tools'),
            enabled: Boolean(document.getElementById('mcpField_enabled')?.checked),
        };
    }

    function restoreEditorFormValues(values) {
        if (!values) return;
        const setValue = (id, value) => {
            const field = document.getElementById(id);
            if (field) field.value = value;
        };
        setValue('mcpField_name', values.name);
        setValue('mcpField_description', values.description);
        setValue('mcpField_url', values.url);
        setValue('mcpField_transport', values.transport);
        setValue('mcpField_auth_mode', values.auth_mode);
        setValue('mcpField_namespace', values.namespace);
        setValue('mcpField_headers', values.headersRaw);
        setValue('mcpField_timeout_seconds', values.timeout_seconds);
        setValue('mcpField_allowed_tools', values.allowedToolsRaw);
        const enabled = document.getElementById('mcpField_enabled');
        if (enabled) enabled.checked = values.enabled;
        if (typeof state.editorIconControl?.setValue === 'function') {
            state.editorIconControl.setValue(values.icon || '');
        }
        syncEditorCustomSelects();
    }

    function isValidUrl(value) {
        try {
            const u = new URL(value);
            return u.protocol === 'http:' || u.protocol === 'https:';
        } catch (_) {
            return false;
        }
    }

    function collectPayload({ silent = false } = {}) {
        if (!silent) clearFieldErrors();

        const errors = [];
        const name = readField('mcpField_name');
        const url = readField('mcpField_url');
        const description = readField('mcpField_description');
        const namespace = readField('mcpField_namespace');
        const icon = typeof state.editorIconControl?.getValue === 'function'
            ? String(state.editorIconControl.getValue() || '').trim()
            : '';
        const transport = readField('mcpField_transport') || 'streamable_http';
        const authMode = readField('mcpField_auth_mode') || 'headers';
        const enabled = Boolean(document.getElementById('mcpField_enabled')?.checked);
        const headersRaw = document.getElementById('mcpField_headers')?.value || '';
        const timeoutRaw = readField('mcpField_timeout_seconds') || '30';
        const allowedTools = readRawField('mcpField_allowed_tools')
            .split(/\r?\n|,/g)
            .map((item) => item.trim())
            .filter(Boolean);
        const existing = currentServer() || defaultServerValues();

        if (!name) errors.push(['mcpField_name', t('workspace_connections_mcp_error_name_required', 'Server name is required.')]);
        if (!url) {
            errors.push(['mcpField_url', t('workspace_connections_mcp_error_url_required', 'Server URL is required.')]);
        } else if (!isValidUrl(url)) {
            errors.push(['mcpField_url', t('workspace_connections_mcp_error_url_invalid', 'Enter a valid http(s) URL.')]);
        }

        let headers = {};
        if (headersRaw && headersRaw.trim()) {
            try {
                const parsed = JSON.parse(headersRaw);
                if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
                    errors.push(['mcpField_headers', t('workspace_connections_mcp_error_headers_object', 'Headers must be a JSON object.')]);
                } else {
                    headers = parsed;
                }
            } catch (_) {
                errors.push(['mcpField_headers', t('workspace_connections_mcp_error_headers_json', 'Headers must be valid JSON.')]);
            }
        }

        const timeout = Number.parseInt(timeoutRaw, 10);
        if (!Number.isFinite(timeout) || timeout < 1 || timeout > 600) {
            errors.push(['mcpField_timeout_seconds', t('workspace_connections_mcp_error_timeout_range', 'Timeout must be between 1 and 600 seconds.')]);
        }

        if (errors.length) {
            if (!silent) {
                errors.forEach(([id, msg]) => setFieldError(id, msg));
                focusFirstError();
            }
            const err = new Error(errors[0][1]);
            err.fieldErrors = errors;
            throw err;
        }

        const payload = {
            owner_type: 'user',
            name,
            icon,
            description,
            namespace,
            transport,
            auth_mode: authMode,
            enabled,
            url,
            allowed_tools: allowedTools,
            timeout_seconds: timeout,
        };

        if (headersRaw.trim() || !hasRedactedSecretMap(existing, 'headers')) {
            payload.headers = headers;
        }
        return payload;
    }

    /**
     * Load the user's MCP servers.
     *
     * A post-mutation refresh is intentionally silent: the mutation already
     * succeeded, so a later read failure must not look like a failed save.
     */
    async function loadServers(force = false, { silent = false } = {}) {
        if (!state.allow || state.loading || (state.loaded && !force)) return;
        state.loading = true;
        if (state.view === 'list') renderRoot();
        try {
            state.servers = await fetchJson('/api/v1/llm/mcp/servers/user');
            state.loaded = true;
        } catch (error) {
            if (!silent) {
                window.notifyError?.(t('workspace_connections_mcp_error_load', 'Failed to load MCP servers.'));
            }
        } finally {
            state.loading = false;
            if (state.view === 'list') renderRoot();
        }
    }

    async function openEditor(serverId = null) {
        state.editorPreviewMinHeight = 0;
        state.editorPreviewSectionMinHeight = 0;
        if (serverId) {
            try {
                const server = await fetchJson(`/api/v1/llm/mcp/servers/user/${encodeURIComponent(serverId)}`);
                const idx = state.servers.findIndex((item) => String(item.id) === String(serverId));
                if (idx >= 0) state.servers.splice(idx, 1, server);
                else state.servers.push(server);
                state.editingId = serverId;
                state.editorPreview = {
                    label: tf('workspace_connections_mcp_preview_label_server', 'Run a test to preview {name}', { name: server.name || t('workspace_connections_mcp_this_server', 'this server') }),
                    status: 'idle',
                    tools: [],
                    tested_at: null,
                    error: '',
                };
            } catch (error) {
                window.notifyError?.(t('workspace_connections_mcp_error_load_details', 'Failed to load MCP server details.'));
                return;
            }
        } else {
            state.editingId = null;
            state.editorPreview = {
                label: t('workspace_connections_mcp_preview_label_default', 'Run a connection test to preview tools'),
                status: 'idle',
                tools: [],
                tested_at: null,
                error: '',
            };
        }
        showEditor();
    }

    async function testServer() {
        if (state.testing) return;
        const btn = document.getElementById('mcpEditorTestBtn');
        try {
            state.testing = true;
            setButtonBusy(btn, true, t('workspace_connections_mcp_testing', 'Testing...'));
            const payload = collectPayload();
            const editing = currentServer();
            if (payload.auth_mode === 'oauth' && !editing?.secret_summary?.oauth_connected) {
                const oauthError = new Error(t(
                    'workspace_connections_mcp_oauth_save_first',
                    'Save this server and connect OAuth before testing it.'
                ));
                oauthError.oauthRequired = true;
                throw oauthError;
            }
            if (state.editingId) {
                payload.server_id = String(state.editingId);
            }
            state.editorPreview = {
                label: tf('workspace_connections_mcp_preview_testing', 'Testing {name}...', { name: payload.name }),
                status: 'testing',
                tools: [],
                tested_at: null,
                error: '',
            };
            lockEditorPreviewHeightFloor();
            renderEditorPreview();
            const result = await fetchJson('/api/v1/llm/mcp/servers/user/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            state.editorPreview = {
                label: tf('workspace_connections_mcp_preview_live', 'Live preview for {name}', { name: payload.name }),
                status: 'success',
                tools: result?.tools || [],
                tested_at: Date.now(),
                error: '',
            };
            renderEditorPreview();
            window.notifySuccess?.(t('workspace_connections_mcp_success_connection', 'Connection successful.'));
        } catch (error) {
            if (!error.fieldErrors) {
                state.editorPreview = {
                    label: t('workspace_connections_mcp_preview_failed_label', 'Connection test failed'),
                    status: 'error',
                    tools: [],
                    tested_at: Date.now(),
                    error: t('workspace_connections_mcp_error_connect', 'Failed to connect to MCP server.'),
                };
                renderEditorPreview();
            }
            window.notifyError?.(
                (error.fieldErrors || error.oauthRequired)
                    ? error.message
                    : t('workspace_connections_mcp_error_connect', 'Failed to connect to MCP server.')
            );
        } finally {
            state.testing = false;
            setButtonBusy(btn, false, t('workspace_connections_mcp_testing', 'Testing...'));
        }
    }

    async function saveServer() {
        if (state.saving) return;
        const btn = document.getElementById('mcpEditorSaveBtn');
        try {
            state.saving = true;
            setButtonBusy(btn, true, t('workspace_connections_mcp_saving', 'Saving...'));
            const payload = collectPayload();
            const method = state.editingId ? 'PATCH' : 'POST';
            const url = state.editingId
                ? `/api/v1/llm/mcp/servers/user/${encodeURIComponent(state.editingId)}`
                : '/api/v1/llm/mcp/servers/user';
            // Ownership is selected by the endpoint and is immutable. It is
            // required for create requests, but the PATCH schema rejects it.
            if (method === 'PATCH') delete payload.owner_type;
            await fetchJson(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            requestActiveModelSettingsRefresh();
            // Keep the successful save independent from a best-effort list
            // refresh, which can fail after the mutation has already applied.
            void loadServers(true, { silent: true }).catch(() => {});
            window.notifySuccess?.(t('workspace_connections_mcp_success_saved', 'MCP server saved.'));
            showList({ force: true });
        } catch (error) {
            window.notifyError?.(error.fieldErrors ? error.message : t('workspace_connections_mcp_error_save', 'Failed to save MCP server.'));
        } finally {
            state.saving = false;
            setButtonBusy(btn, false, t('workspace_connections_mcp_saving', 'Saving...'));
        }
    }

    function updateToggleAccessibleLabel(input, server, enabled) {
        const serverName = server.name || t('workspace_connections_mcp_unnamed_server', 'Unnamed server');
        const statusLabel = enabled
            ? t('workspace_connections_mcp_enabled', 'Enabled')
            : t('workspace_connections_mcp_disabled', 'Disabled');
        const label = `${statusLabel}: ${serverName}`;
        input.setAttribute('aria-label', label);
        input.closest('.mcp-server-toggle')?.setAttribute('title', label);
    }

    function setServerCardMutationLocked(card, locked) {
        if (!card) return;
        // The toggle PATCH carries identifying fields required by the API.
        // Lock every card action so an editor cannot change those fields while
        // that snapshot is in flight and then be overwritten by its response.
        card.querySelectorAll('button, input').forEach((control) => {
            control.disabled = locked;
        });
        if (locked) card.setAttribute('aria-busy', 'true');
        else card.removeAttribute('aria-busy');
    }

    async function toggleServer(serverId, enabled, input) {
        const server = state.servers.find((item) => String(item.id) === String(serverId));
        if (!server || !input) return;

        const previousEnabled = Boolean(server.enabled);
        const card = input.closest('.mcp-server-card-skin');
        server.enabled = enabled;
        setServerCardMutationLocked(card, true);
        updateToggleAccessibleLabel(input, server, enabled);

        try {
            // The PATCH schema validates a remote server's identifying fields,
            // while omitted configuration and redacted secrets remain untouched.
            const updated = await fetchJson(`/api/v1/llm/mcp/servers/user/${encodeURIComponent(serverId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: server.name,
                    transport: server.transport,
                    enabled,
                    url: server.url,
                }),
            });
            Object.assign(server, updated);
            requestActiveModelSettingsRefresh();
        } catch (error) {
            server.enabled = previousEnabled;
            input.checked = previousEnabled;
            updateToggleAccessibleLabel(input, server, previousEnabled);
            window.notifyError?.(t('workspace_connections_mcp_error_save', 'Failed to save MCP server.'));
        } finally {
            setServerCardMutationLocked(card, false);
        }
    }

    async function connectOAuth(serverId) {
        try {
            const result = await fetchJson(`/api/v1/llm/mcp/servers/user/${encodeURIComponent(serverId)}/oauth/start`, {
                method: 'POST',
            });
            if (!result?.authorization_url) throw new Error('Missing authorization URL');
            const authorizationUrl = new URL(result.authorization_url, window.location.href);
            if (!['http:', 'https:'].includes(authorizationUrl.protocol)) {
                throw new Error('Unsafe authorization URL');
            }
            window.location.assign(authorizationUrl.href);
        } catch (_) {
            window.notifyError?.(t('workspace_connections_mcp_oauth_start_failed', 'Could not start OAuth authorization.'));
        }
    }

    function handleOAuthRedirectStatus() {
        const url = new URL(window.location.href);
        const status = url.searchParams.get('mcp_oauth_status');
        if (!status) return;
        if (status === 'connected') {
            requestActiveModelSettingsRefresh();
            window.notifySuccess?.(t('workspace_connections_mcp_oauth_success', 'OAuth connection established.'));
        } else {
            window.notifyError?.(t('workspace_connections_mcp_oauth_failed', 'OAuth authorization failed or was cancelled.'));
        }
        url.searchParams.delete('mcp_oauth_status');
        url.searchParams.delete('mcp_server_id');
        window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    }

    /* ----------------------------------------
       Rendering / event binding
       ---------------------------------------- */

    function bindListEvents() {
        document.getElementById('mcpAddServerBtn')?.addEventListener('click', () => openEditor(null));
        const root = getRoot();
        if (!root) return;
        root.querySelectorAll('[data-action="edit"]').forEach((btn) => {
            btn.addEventListener('click', () => openEditor(btn.dataset.serverId));
        });
        root.querySelectorAll('[data-action="delete"]').forEach((btn) => {
            btn.addEventListener('click', () => openDeleteDialog(btn.dataset.serverId));
        });
        root.querySelectorAll('[data-action="oauth"]').forEach((btn) => {
            btn.addEventListener('click', () => connectOAuth(btn.dataset.serverId));
        });
        root.querySelectorAll('[data-action="toggle"]').forEach((input) => {
            input.addEventListener('change', () => {
                void toggleServer(input.dataset.serverId, input.checked, input);
            });
        });
        document.getElementById('mcpDeleteCancelBtn')?.addEventListener('click', closeDeleteDialog);
        document.getElementById('mcpDeleteConfirmBtn')?.addEventListener('click', confirmDelete);
        document.getElementById('mcpDeleteOverlay')?.addEventListener('click', (e) => {
            if (e.target.id === 'mcpDeleteOverlay') closeDeleteDialog();
        });
        document.getElementById('mcpDeleteOverlay')?.addEventListener('keydown', (event) => {
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
    }

    function bindEditorEvents() {
        document.getElementById('mcpEditorCancelBtn')?.addEventListener('click', () => showList());
        document.getElementById('mcpEditorTestBtn')?.addEventListener('click', testServer);
        document.getElementById('mcpEditorSaveBtn')?.addEventListener('click', saveServer);
    }

    /**
     * Enhance MCP's transport and authentication controls with the shared
     * custom-select component. Their hidden native selects remain the source
     * of truth, preserving ordinary value reads and change events.
     */
    function upgradeEditorCustomSelect(id) {
        const select = document.getElementById(id);
        if (!select || typeof window.upgradeAdminSingleSelect !== 'function') return null;

        const meta = window.upgradeAdminSingleSelect(select, {
            key: id,
            placeholder: select.selectedOptions?.[0]?.textContent || '',
            emptyValueIsOption: true,
        });
        meta?.wrapper?.classList.add('mcp-editor-custom-select');
        return meta;
    }

    function upgradeEditorCustomSelects() {
        upgradeEditorCustomSelect('mcpField_transport');
        upgradeEditorCustomSelect('mcpField_auth_mode');
    }

    function syncEditorCustomSelects() {
        ['mcpField_transport', 'mcpField_auth_mode'].forEach((id) => {
            document.getElementById(id)?._singleSelect?.syncFromSelect?.();
        });
    }

    function initEditorIconPicker(initialValue = '') {
        const host = document.getElementById('mcpField_iconPickerHost');
        state.editorIconControl = null;
        if (!host) return;
        if (typeof window.IconPicker?.createIconPicker !== 'function') {
            host.innerHTML = `<p class="mcp-icon-field-hint">${escapeHtml(t('workspace_connections_mcp_icon_unavailable', 'Icon picker unavailable.'))}</p>`;
            return;
        }
        const picker = window.IconPicker.createIconPicker({
            value: initialValue || '',
            presetType: 'provider',
        });
        host.replaceChildren(picker.container);
        state.editorIconControl = picker;
    }

    function renderRoot() {
        if (!state.domReady || !state.allow) return;
        const root = getRoot();
        if (!root) return;
        root.querySelectorAll('.admin-select.open').forEach((select) => select._closeMenu?.());
        if (state.view === 'editor') {
            const editing = currentServer();
            const values = { ...defaultServerValues(), ...(editing || {}) };
            root.innerHTML = editorViewHtml();
            upgradeEditorCustomSelects();
            initEditorIconPicker(values.icon || '');
            bindEditorEvents();
            renderEditorPreview();
        } else {
            root.innerHTML = listViewHtml();
            mountDeleteDialog(root);
            bindListEvents();
        }
    }

    function applyVisibility() {
        const personal = getPersonalSection();
        const visible = Boolean(state.allow);
        if (personal) personal.style.display = visible ? '' : 'none';
        if (!visible) {
            const section = getConnectionsSection();
            section?.classList.remove('mcp-editor-active');
        }
    }

    function init() {
        state.domReady = true;
        handleOAuthRedirectStatus();
        window.unsavedChangesManager?.register?.({
            id: 'personal-mcp-editor-unsaved',
            priority: 170,
            isActive: () => state.view === 'editor',
            isDirty: () => state.editorInitialSnapshot !== null && editorSnapshot() !== state.editorInitialSnapshot,
            discard: () => { state.editorInitialSnapshot = editorSnapshot(); },
            getCopy: () => ({
                subtitle: t('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
            }),
        });
        if (typeof window !== 'undefined') {
            // Personal MCP access must never inherit the aggregate Connections
            // workspace flag, which may be true for managed providers.
            state.allow = window.chatSetup?.allow_mcp === true;
        }
        applyVisibility();
        if (state.allow) {
            renderRoot();
            loadServers();
        }
    }

    if (typeof window !== 'undefined' && window.registerEscapeHandler) {
        window.registerEscapeHandler({
            id: 'mcp-settings-modal',
            priority: 200,
            isActive: () => state.deleteOpen || state.view === 'editor',
            close: () => {
                if (state.deleteOpen) {
                    closeDeleteDialog();
                    return;
                }
                if (state.view === 'editor') showList();
            },
        });
    }

    if (typeof window !== 'undefined') {
        window.addEventListener('i18n:updated', () => {
            const editorValues = captureEditorFormValues();
            renderRoot();
            restoreEditorFormValues(editorValues);
        });
    }

    window.MCPSettings = {
        setPolicy(data = {}) {
            state.allow = Boolean(data.allow_mcp);
            applyVisibility();
            if (!state.allow) return;
            if (state.domReady) {
                renderRoot();
                loadServers();
            }
        },
        show() {
            if (!state.allow || !state.domReady) return;
            applyEditorVisibility();
            renderRoot();
            loadServers();
        },
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
