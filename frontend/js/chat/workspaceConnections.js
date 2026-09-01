(() => {
    const state = {
        allowed: false,
        initialized: false,
        loading: false,
        catalogLoaded: false,
        items: [],
        callbackStatus: null,
        callbackReloadTimer: null,
        searchQuery: '',
        view: 'list',
        activeProvider: null,
        returnFocus: null,
        removeConfirmationOpen: false,
        toolPreview: { status: 'idle', tools: [], error: '' },
    };

    const dom = {
        get root() { return document.getElementById('connectionsWorkspace'); },
        get catalogBlock() { return document.getElementById('connectionsCatalogBlock'); },
        get grid() { return document.getElementById('connectionsGrid'); },
        get searchInput() { return document.getElementById('connectionsSearchInput'); },
        get page() { return document.getElementById('managedConnectionPage'); },
        get pageRoot() { return document.getElementById('managedConnectionPageRoot'); },
    };

    // OAuth callbacks return a stable provider identifier rather than the
    // catalog item. Keep the user-facing provider names here so success toasts
    // remain readable even before the catalog has finished loading.
    const CONNECTION_PROVIDER_TITLES = Object.freeze({
        notion: 'Notion',
        github: 'GitHub',
        gmail: 'Gmail',
        google_calendar: 'Google Calendar',
        google_drive: 'Google Drive',
        slack: 'Slack',
    });
    const CONNECTION_PROVIDER_DESCRIPTIONS = Object.freeze({
        notion: 'Connect your Notion workspace through the hosted MCP server and make it available inside chats.',
        github: 'Connect your GitHub account through the hosted GitHub MCP server and use repositories, issues, pull requests, and more inside chats.',
        gmail: 'Connect Gmail to search threads, read messages, draft replies, and send email from inside chats.',
        google_calendar: 'Connect Google Calendar to view schedules, create events, and manage availability inside chats.',
        google_drive: 'Connect Google Drive to browse and import files directly in chats.',
        slack: 'Connect Slack through the hosted Slack MCP server to search conversations, read history, work with canvases, and post messages inside chats.',
    });
    const CONNECTION_PROVIDER_CATEGORIES = Object.freeze({
        notion: 'Knowledge Base',
        github: 'Developer Tools',
        gmail: 'Productivity',
        google_calendar: 'Productivity',
        google_drive: 'Storage',
        slack: 'Communication',
    });
    let svgIdCounter = 0;

    const PROVIDER_DESCRIPTION_KEYS = Object.freeze({
        notion: 'workspace_connections_provider_notion_desc',
        github: 'workspace_connections_provider_github_desc',
        gmail: 'workspace_connections_provider_gmail_desc',
        google_calendar: 'workspace_connections_provider_google_calendar_desc',
        slack: 'workspace_connections_provider_slack_desc',
        google_drive: 'workspace_connections_provider_google_drive_desc',
    });

    const CATEGORY_KEYS = Object.freeze({
        'knowledge base': 'workspace_connections_category_knowledge_base',
        'developer tools': 'workspace_connections_category_developer_tools',
        productivity: 'workspace_connections_category_productivity',
        communication: 'workspace_connections_category_communication',
        storage: 'workspace_connections_category_storage',
    });

    function t(key, fallback = '') {
        return typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    }

    function tf(key, fallback = '', variables = {}) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, variables);
        }
        return Object.entries(variables).reduce((text, [name, value]) => (
            text.replace(new RegExp(`\\{${escapeRegExp(name)}\\}`, 'g'), String(value))
        ), fallback);
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function escapeRegExp(value) {
        return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    /** Give embedded provider icons unique IDs so multiple cards cannot collide. */
    function ensureUniqueSvgIds(svg, context = 'connection') {
        if (typeof svg !== 'string' || svg.indexOf('id=') === -1) return svg;

        const suffix = `${context}-${svgIdCounter++}`;
        const ids = new Set();
        const idRegex = /id=(['"])([^"']+)\1/g;
        let match;
        while ((match = idRegex.exec(svg)) !== null) ids.add(match[2]);
        if (!ids.size) return svg;

        let updated = svg;
        ids.forEach((id) => {
            const newId = `${id}-${suffix}`;
            const escapedId = escapeRegExp(id);
            updated = updated.replace(new RegExp(`id="${escapedId}"`, 'g'), `id="${newId}"`);
            updated = updated.replace(new RegExp(`id='${escapedId}'`, 'g'), `id='${newId}'`);
            updated = updated.replace(new RegExp(`url\\(#${escapedId}\\)`, 'g'), `url(#${newId})`);
            updated = updated.replace(new RegExp(`"#${escapedId}"`, 'g'), `"#${newId}"`);
            updated = updated.replace(new RegExp(`'#${escapedId}'`, 'g'), `'#${newId}'`);
            updated = updated.replace(
                new RegExp(`(xlink:href|href)=(["'])#${escapedId}(["'])`, 'g'),
                (_, attr, startQuote, endQuote) => `${attr}=${startQuote}#${newId}${endQuote}`
            );
        });
        return updated;
    }

    function getIconsMap() {
        if (typeof Icons !== 'undefined' && Icons) return Icons;
        return typeof window !== 'undefined' && window.Icons ? window.Icons : {};
    }

    function getConnectionIcon(item) {
        const provider = String(item?.provider || '').trim().toLowerCase();
        const iconsMap = getIconsMap();
        // Use the shared resolver so chat mentions display precisely the same
        // provider artwork as these workspace cards.
        const iconKey = typeof iconsMap.getConnectionProviderIconKey === 'function'
            ? iconsMap.getConnectionProviderIconKey(provider)
            : provider;
        const iconMarkup = typeof iconsMap[iconKey] === 'string' ? iconsMap[iconKey].trim() : '';
        if (iconMarkup) {
            return {
                markup: ensureUniqueSvgIds(iconMarkup, `connection-${provider || 'default'}`),
                isFallback: false,
            };
        }
        const title = typeof item?.title === 'string' ? item.title : String(item?.title || '');
        return { markup: escapeHtml(title.slice(0, 2).toUpperCase()), isFallback: true };
    }

    async function fetchJson(url, options = {}) {
        const response = await window.authedFetch(url, { cache: 'no-store', ...options });
        if (!response.ok) {
            const payload = await response.json().catch(() => null);
            const error = new Error(payload?.detail || payload?.message || `HTTP ${response.status}`);
            error.status = response.status;
            // MCP endpoints return a stable, non-sensitive code so the same
            // backend failure can be localized consistently in this UI.
            error.code = response.headers?.get?.('X-Omlorix-MCP-Error-Code') || '';
            throw error;
        }
        return response.json();
    }

    /**
     * The active model's settings schema contains the connection-backed MCP
     * options. Refresh it only after the connection mutation succeeds so the
     * newly available provider appears in its selector immediately.
     */
    function requestActiveModelSettingsRefresh() {
        window.dispatchEvent?.(new CustomEvent('modelSettings:refreshRequested'));
    }

    function isConnectionsDisabledForGroup(error) {
        return error?.status === 403 && error?.message === 'Connections are disabled for your group.';
    }

    function setPageVisibility(element, visible) {
        if (!element) return;
        element.hidden = !visible;
        element.style.display = visible ? '' : 'none';
        element.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }

    /** Expand static presentation metadata omitted by the compact API. */
    function normalizeCatalogItem(item) {
        const provider = String(item?.provider || '').trim().toLowerCase();
        return {
            ...item,
            provider,
            title: item?.title || CONNECTION_PROVIDER_TITLES[provider] || provider,
            description: item?.description || CONNECTION_PROVIDER_DESCRIPTIONS[provider] || '',
            category: item?.category || CONNECTION_PROVIDER_CATEGORIES[provider] || '',
            setup_mode: item?.setup_mode || 'oauth',
            connection_type: item?.connection_type || 'mcp',
        };
    }

    function getConnectionState(connection) {
        return String(connection?.state || connection?.status?.state || '').trim().toLowerCase();
    }

    function getConnectionErrorCode(connection) {
        return String(connection?.error_code || connection?.status?.last_error_code || '').trim();
    }

    function formatStatus(item) {
        const connection = item.connection;
        if (!connection) return '';
        const state = getConnectionState(connection);
        if (state === 'reauthorization_required') {
            return t('workspace_connections_status_reconnect_required', 'Reconnect required');
        }
        if (state === 'error') return t('workspace_connections_status_error', 'Error');
        if (!connection.enabled) {
            return t('workspace_connections_status_disabled', 'Disabled');
        }
        if (state === 'connected' || connection.connected === true) {
            return t('workspace_connections_status_connected', 'Connected');
        }
        return t('workspace_connections_status_not_connected', 'Not connected');
    }

    function getStatusClass(item) {
        const connection = item.connection;
        if (!connection) return 'status-idle';
        const state = getConnectionState(connection);
        if (state === 'reauthorization_required' || state === 'error') return 'status-error';
        return connection.enabled && (state === 'connected' || connection.connected === true)
            ? 'status-connected'
            : 'status-idle';
    }

    function getSetupLabel(item) {
        if (item.setup_mode === 'choice') return t('workspace_connections_setup_oauth_or_token', 'OAuth or token');
        if (item.setup_mode === 'token') return t('workspace_connections_setup_access_token', 'Access token');
        return t('workspace_connections_setup_oauth_sign_in', 'OAuth sign-in');
    }

    function getProviderDescription(item) {
        const provider = String(item?.provider || '').trim().toLowerCase();
        const fallback = item?.description || getSetupLabel(item);
        const key = PROVIDER_DESCRIPTION_KEYS[provider];
        return key ? t(key, fallback) : fallback;
    }

    /**
     * File-source adapters have a different product surface from MCP-backed
     * connections. Prefer the explicit catalog contract, while retaining the
     * managed_mcp fallback for older cached catalog payloads.
     */
    function isFileSourceAdapter(item) {
        return item?.connection_type === 'file_source_adapter'
            || item?.file_source_available === true
            || item?.llm_available === false
            || item?.managed_mcp === false;
    }

    /** Render the explanation shown instead of MCP tool discovery for sources. */
    function renderFileSourceAdapterInfo(item) {
        const infoIcon = getIconsMap().info || '';
        return `
            <section class="managed-connection-file-source-info" role="note" aria-labelledby="managedConnectionFileSourceTitle">
                <span class="managed-connection-file-source-info-icon" aria-hidden="true">${infoIcon}</span>
                <div>
                    <h3 id="managedConnectionFileSourceTitle">${escapeHtml(t('workspace_connections_file_source_adapter_title', 'File source adapter'))}</h3>
                    <p>${escapeHtml(tf(
                        'workspace_connections_file_source_adapter_desc',
                        'Use {title} from the chat file dropdown to browse and add files from this source. Imported files can be attached to your chat. This connection cannot be used by the LLM model.',
                        { title: item.title },
                    ))}</p>
                </div>
            </section>
        `;
    }

    function getProviderCategory(item) {
        const category = String(item?.category || '').trim();
        const key = CATEGORY_KEYS[category.toLowerCase()];
        return key ? t(key, category) : category;
    }

    /**
     * Return a readable provider name for messages received before catalog data
     * is available. Known providers retain their official capitalization; the
     * fallback still turns a future snake_case provider ID into normal text.
     */
    function getProviderTitle(provider) {
        const normalizedProvider = String(provider || '').trim().toLowerCase();
        const catalogTitle = String(
            state.items.find((item) => (
                String(item?.provider || '').trim().toLowerCase() === normalizedProvider
            ))?.title || ''
        ).trim();
        if (catalogTitle) return catalogTitle;
        if (CONNECTION_PROVIDER_TITLES[normalizedProvider]) {
            return CONNECTION_PROVIDER_TITLES[normalizedProvider];
        }
        return normalizedProvider
            .replace(/[_-]+/g, ' ')
            .replace(/\b\w/g, (character) => character.toUpperCase());
    }

    /** Translate provider-specific connection failures without showing SDK internals. */
    function translateConnectionError(code, fallback = '') {
        switch (String(code || '').trim()) {
            case 'github_token_invalid':
                return t(
                    'workspace_connections_error_github_token_invalid',
                    'GitHub token is invalid or expired. Reconnect GitHub with a new token.',
                );
            case 'github_access_denied':
                return t(
                    'workspace_connections_error_github_access_denied',
                    'GitHub denied this token. Check its permissions, organization approval, or SSO authorization.',
                );
            case 'mcp_connection_failed':
                return t(
                    'workspace_connections_error_connection_failed',
                    'Could not connect to this service. Check the credentials and try again.',
                );
            case 'mcp_authentication_failed':
                return t(
                    'workspace_connections_error_authentication_failed',
                    'This connection\'s authentication failed. Reconnect it and try again.',
                );
            case 'mcp_access_denied':
                return t(
                    'workspace_connections_error_access_denied',
                    'This connection was denied access. Check its permissions.',
                );
            default:
                // Older persisted rows may still contain the SDK's opaque
                // ExceptionGroup text. Never render that implementation detail.
                return /unhandled errors in a taskgroup|server returned an error response/i.test(String(fallback || ''))
                    ? t(
                        'workspace_connections_error_connection_failed',
                        'Could not connect to this service. Check the credentials and try again.',
                    )
                    : fallback;
        }
    }

    function getConnectionStatusError(item) {
        const connection = item?.connection;
        const status = connection?.status || {};
        const rawError = status.last_error || '';
        // Rows written by older builds have no error code. GitHub's previous
        // MCP auth failure was consistently persisted as this opaque
        // TaskGroup wrapper, so give those existing rows the same actionable
        // token guidance immediately after an upgrade.
        const legacyGithubAuthFailure = item?.provider === 'github'
            && /unhandled errors in a taskgroup|server returned an error response/i.test(String(rawError));
        return translateConnectionError(
            getConnectionErrorCode(connection)
                || (legacyGithubAuthFailure ? 'github_token_invalid' : '')
                || (getConnectionState(connection) === 'error' ? 'mcp_connection_failed' : ''),
            rawError,
        );
    }

    function setCatalogBlockVisible(visible) {
        if (!dom.catalogBlock) return;
        dom.catalogBlock.hidden = !visible;
        dom.catalogBlock.style.display = visible ? '' : 'none';
    }

    function isProviderAvailable(item) {
        // The backend omits incomplete global integrations. Keep the same
        // invariant in the client so a stale response can never expose an
        // unusable card or an OAuth-configuration warning to end users.
        return item?.oauth_ready !== false;
    }

    function getFilteredItems() {
        if (!state.searchQuery) return state.items;
        const query = state.searchQuery.toLowerCase();
        return state.items.filter((item) => (
            (item.title || '').toLowerCase().includes(query)
            || getProviderDescription(item).toLowerCase().includes(query)
            || (item.provider || '').toLowerCase().includes(query)
            || getProviderCategory(item).toLowerCase().includes(query)
        ));
    }

    /** Render only provider identity and connection state; tool metadata belongs on demand. */
    function renderGrid() {
        const host = dom.grid;
        if (!host) return;
        if (!state.allowed) {
            host.innerHTML = '';
            setCatalogBlockVisible(false);
            return;
        }
        if (state.loading && !state.catalogLoaded && !state.items.length) {
            setCatalogBlockVisible(true);
            host.innerHTML = `<div class="connections-empty-state"><p>${escapeHtml(t('workspace_connections_loading', 'Loading connections...'))}</p></div>`;
            return;
        }

        const filtered = getFilteredItems();
        if (!filtered.length && state.searchQuery) {
            setCatalogBlockVisible(true);
            host.innerHTML = `<div class="connections-no-results">${escapeHtml(tf('workspace_connections_no_results', 'No connections match "{query}"', { query: state.searchQuery }))}</div>`;
            return;
        }
        if (!filtered.length) {
            host.innerHTML = '';
            setCatalogBlockVisible(false);
            return;
        }

        setCatalogBlockVisible(true);
        host.innerHTML = filtered.map((item) => {
            const statusText = formatStatus(item);
            const statusClass = getStatusClass(item);
            const isConnected = statusClass === 'status-connected';
            const title = typeof item.title === 'string' ? item.title : String(item.title || '');
            const icon = getConnectionIcon(item);
            return `
                <button type="button" class="connection-card ${isConnected ? 'is-connected' : ''}" data-provider="${escapeHtml(item.provider)}">
                    <span class="connection-card-logo ${icon.isFallback ? 'is-fallback' : 'has-icon'}" aria-hidden="true">${icon.markup}</span>
                    <div class="connection-card-info"><h4 class="connection-card-title">${escapeHtml(title)}</h4></div>
                    ${statusText ? `<span class="connection-card-status-badge ${statusClass}"><span class="connection-card-status-dot"></span>${escapeHtml(statusText)}</span>` : ''}
                    ${getIconsMap().chevronRight || ''}
                </button>
            `;
        }).join('');

        host.querySelectorAll('[data-provider]').forEach((button) => {
            button.addEventListener('click', () => {
                const item = state.items.find((entry) => entry.provider === button.dataset.provider);
                if (item) openConnectionPage(item, button);
            });
        });
    }

    function getActiveItem() {
        return state.items.find((item) => item.provider === state.activeProvider) || null;
    }

    function captureFormValues() {
        return {
            token: document.getElementById('managedConnectionAccessToken')?.value || '',
            enabled: document.getElementById('managedConnectionEnabled')?.checked,
        };
    }

    function restoreFormValues(values) {
        if (!values) return;
        const token = document.getElementById('managedConnectionAccessToken');
        const enabled = document.getElementById('managedConnectionEnabled');
        if (token) token.value = values.token;
        if (enabled && typeof values.enabled === 'boolean') enabled.checked = values.enabled;
    }

    function renderProviderSummary(item) {
        const icon = getConnectionIcon(item);
        const statusText = formatStatus(item);
        return `
            <div class="managed-connection-summary">
                <span class="managed-connection-summary-icon connection-card-logo ${icon.isFallback ? 'is-fallback' : 'has-icon'}" aria-hidden="true">${icon.markup}</span>
                <div class="managed-connection-summary-copy">
                    <h2>${escapeHtml(item.title)}</h2>
                    <p>${escapeHtml(getProviderDescription(item))}</p>
                </div>
                ${statusText ? `<span class="connection-card-status-badge ${getStatusClass(item)}"><span class="connection-card-status-dot"></span>${escapeHtml(statusText)}</span>` : ''}
            </div>
        `;
    }

    function renderSetupPage(item) {
        const renderer = window.CreateEditFormRenderer;
        const isChoice = item.setup_mode === 'choice';
        const isOAuth = item.setup_mode === 'oauth';
        const fileSourceAdapter = isFileSourceAdapter(item);
        const title = typeof item.title === 'string' ? item.title : String(item.title || '');
        const signinIcon = getIconsMap().signin || '';

        let setupSections = '';
        if (isOAuth || isChoice) {
            setupSections += `
                <section class="managed-connection-section">
                    <div class="managed-connection-section-heading">
                        <h3>${escapeHtml(t('workspace_connections_oauth_sign_in_title', 'Connect your account'))}</h3>
                        <p>${escapeHtml(tf('workspace_connections_oauth_intro', 'Sign in with {title} OAuth to connect automatically.', { title }))}</p>
                    </div>
                    <button type="button" class="om-button border submit managed-connection-section-action" data-action="connect" data-provider="${escapeHtml(item.provider)}">
                        ${signinIcon}${escapeHtml(isChoice ? t('workspace_connections_connect_oauth', 'Connect with OAuth') : tf('workspace_connections_connect_provider', 'Connect {title}', { title }))}
                    </button>
                </section>
            `;
        }

        if (isChoice || item.setup_mode === 'token') {
            setupSections += `
                <section class="managed-connection-section">
                    <div class="managed-connection-section-heading">
                        <h3>${escapeHtml(isChoice ? t('workspace_connections_connect_with_token_alt', 'Or connect with token') : t('workspace_connections_connect_with_access_token', 'Connect with access token'))}</h3>
                        <p>${escapeHtml(t('workspace_connections_token_help', 'The token is encrypted and is never shown again after you save it.'))}</p>
                    </div>
                    ${renderer.renderControlField({
                        label: { key: 'workspace_connections_access_token', fallback: 'Access token' },
                        control: {
                            id: 'managedConnectionAccessToken',
                            type: 'password',
                            placeholder: t('workspace_connections_token_placeholder', 'Paste your token here...'),
                            attributes: { maxlength: 4096, autocomplete: 'off' },
                        },
                    })}
                </section>
            `;
        }

        const header = renderer.renderHeader({
            className: 'projects-header managed-connection-page-header',
            contentHtml: `<div>
                <p class="projects-header-title" tabindex="-1" id="managedConnectionPageTitle">${escapeHtml(tf('workspace_connections_setup_title', 'Setup {title}', { title }))}</p>
            </div>`,
        });
        const description = isOAuth ? renderer.renderDescription({
            className: 'projects-create-description',
            titleClass: 'projects-create-description-title',
            title: { key: 'workspace_connections_how_it_works_title', fallback: 'How it works' },
            textClass: 'projects-create-description-text',
            paragraphs: [{
                key: 'workspace_connections_how_it_works_oauth',
                fallback: 'This securely connects the provider to Omlorix. Once connected, you can use it from supported chats.',
            }],
        }) : '';
        const actions = renderer.renderActions({
            className: 'projects-create-buttons',
            buttons: [
                {
                    className: 'om-button border',
                    key: 'workspace_connections_cancel',
                    fallback: 'Cancel',
                    attributes: { 'data-action': 'back' },
                },
                ...((isChoice || item.setup_mode === 'token') ? [{
                    className: 'om-button border submit',
                    key: 'workspace_connections_connect_with_token',
                    fallback: 'Connect with token',
                    type: 'submit',
                }] : []),
            ],
        });

        return `${header}
            <form class="projects-create-form managed-connection-form" id="managedConnectionSetupForm">
                ${renderProviderSummary(item)}
                ${setupSections}
                ${fileSourceAdapter ? renderFileSourceAdapterInfo(item) : ''}
                ${description}
                ${actions}
            </form>
        `;
    }

    function renderToolResults() {
        const preview = state.toolPreview;
        if (preview.status === 'idle') return '';
        if (preview.status === 'loading') {
            return `<div class="managed-connection-feedback" role="status" aria-live="polite">${escapeHtml(t('workspace_connections_tools_loading', 'Loading tools...'))}</div>`;
        }
        if (preview.status === 'error') {
            return `<div class="managed-connection-feedback is-error" role="alert">${escapeHtml(preview.error || t('workspace_connections_error_show_tools', 'Failed to load tools.'))}</div>`;
        }

        const tools = preview.tools;
        return `
            <section class="managed-connection-tools" aria-live="polite">
                <div class="managed-connection-tools-header">
                    <div>
                        <h3>${escapeHtml(t('workspace_connections_exposed_tools', 'Exposed tools'))}</h3>
                        <p>${escapeHtml(t('workspace_connections_tools_loaded_desc', 'These are the tools currently available from this connection.'))}</p>
                    </div>
                    <span class="managed-connection-tools-count">${escapeHtml(tf(
                        tools.length === 1
                            ? 'workspace_connections_mcp_tool_count_one'
                            : 'workspace_connections_mcp_tool_count_other',
                        tools.length === 1 ? '{count} tool' : '{count} tools',
                        { count: tools.length },
                    ))}</span>
                </div>
                ${tools.length ? `
                    <div class="connections-tool-list">
                        ${tools.map((tool) => `
                            <article class="connections-tool-item">
                                <div class="connections-tool-item-name">${escapeHtml(tool.public_name || tool.tool_name)}</div>
                                <div class="connections-tool-item-original">${escapeHtml(tool.tool_name || '')}</div>
                                <p>${escapeHtml(tool.description || t('workspace_connections_no_description', 'No description.'))}</p>
                            </article>
                        `).join('')}
                    </div>
                ` : `<p class="managed-connection-tools-empty">${escapeHtml(t('workspace_connections_tools_empty', 'This connection did not expose any tools.'))}</p>`}
            </section>
        `;
    }

    function renderEditPage(item) {
        const renderer = window.CreateEditFormRenderer;
        const connection = item.connection;
        const fileSourceAdapter = isFileSourceAdapter(item);
        const oauthReady = item.oauth_ready !== false;
        const statusError = getConnectionStatusError(item);
        const showReconnect = oauthReady && (
            (item.provider === 'github')
            || (item.provider !== 'github' && item.setup_mode === 'oauth')
        );
        const toolsButtonText = state.toolPreview.status === 'loading'
            ? t('workspace_connections_tools_loading', 'Loading tools...')
            : state.toolPreview.status === 'success'
                ? t('workspace_connections_refresh_tools', 'Refresh tools')
                : t('workspace_connections_show_tools', 'Show tools');

        const header = renderer.renderHeader({
            className: 'projects-header managed-connection-page-header',
            contentHtml: `<div>
                <p class="projects-header-title" tabindex="-1" id="managedConnectionPageTitle">${escapeHtml(item.title)}</p>
            </div>`,
        });
        const credentialSection = item.provider === 'github' ? `
            <section class="managed-connection-section">
                <div class="managed-connection-section-heading">
                    <h3>${escapeHtml(t('workspace_connections_credentials_title', 'Credentials'))}</h3>
                    <p>${escapeHtml(t('workspace_connections_credentials_desc', 'Replace the saved access token, or leave this field empty to keep it.'))}</p>
                </div>
                ${renderer.renderControlField({
                    label: { key: 'workspace_connections_access_token', fallback: 'Access token' },
                    control: {
                        id: 'managedConnectionAccessToken',
                        type: 'password',
                        placeholder: t('workspace_connections_keep_token_placeholder', 'Leave empty to keep current token'),
                        attributes: { maxlength: 4096, autocomplete: 'off' },
                    },
                })}
            </section>` : '';
        const enabledToggle = renderer.renderToggleCard({
            className: 'memories-card managed-connection-toggle-card',
            id: 'managedConnectionEnabled',
            label: { key: 'workspace_connections_enabled', fallback: 'Enabled' },
            description: { key: 'workspace_connections_enabled_desc', fallback: 'Disabled connections are hidden from chats.' },
            inputAttributes: { role: 'switch', checked: connection.enabled },
            switchClassName: 'managed-connection-switch',
        });
        const actions = renderer.renderActions({
            className: 'projects-create-buttons managed-connection-actions',
            buttons: [
                {
                    className: 'om-button border',
                    key: 'workspace_connections_cancel',
                    fallback: 'Cancel',
                    attributes: { 'data-action': 'back' },
                },
                ...(showReconnect ? [{
                    className: 'om-button border',
                    key: item.provider === 'github' ? 'workspace_connections_reconnect_oauth' : 'workspace_connections_reconnect',
                    fallback: item.provider === 'github' ? 'Reconnect OAuth' : 'Reconnect',
                    attributes: { 'data-action': 'reconnect', 'data-provider': item.provider },
                }] : []),
                {
                    className: 'om-button border danger',
                    key: 'workspace_connections_remove',
                    fallback: 'Remove',
                    attributes: { 'data-action': 'remove' },
                },
                {
                    className: 'om-button border submit',
                    key: 'workspace_connections_save_changes',
                    fallback: 'Save changes',
                    type: 'submit',
                },
            ],
        });

        return `${header}
            <form class="projects-create-form managed-connection-form" id="managedConnectionEditForm">
                ${renderProviderSummary(item)}
                ${statusError ? `<div class="managed-connection-feedback is-error" role="status">${escapeHtml(statusError)}</div>` : ''}
                ${credentialSection}

                ${enabledToggle}

                ${fileSourceAdapter ? renderFileSourceAdapterInfo(item) : `
                    <section class="managed-connection-tool-discovery">
                        <div>
                            <h3>${escapeHtml(t('workspace_connections_tools_title', 'Connection tools'))}</h3>
                            <p>${escapeHtml(t('workspace_connections_tools_on_demand_desc', 'Load the current tool list when you want to inspect what this connection exposes.'))}</p>
                        </div>
                        <button type="button" class="om-button border" data-action="show-tools" data-connection-id="${escapeHtml(connection.id)}" ${state.toolPreview.status === 'loading' ? 'aria-disabled="true" aria-busy="true"' : ''}>${escapeHtml(toolsButtonText)}</button>
                    </section>
                    <div id="managedConnectionToolResults">${renderToolResults()}</div>
                `}

                <div class="connection-inline-confirmation managed-connection-remove-confirmation" id="managedConnectionRemoveConfirmation" ${state.removeConfirmationOpen ? '' : 'hidden'}>
                    <div>
                        <strong>${escapeHtml(tf('workspace_connections_remove_title', 'Remove {title}', { title: item.title }))}</strong>
                        <p>${tf('workspace_connections_remove_confirm', 'Remove <strong>{title}</strong>? This cannot be undone.', { title: escapeHtml(item.title) })}</p>
                    </div>
                    <div class="connection-inline-actions">
                        <button type="button" class="om-button border" data-action="cancel-remove">${escapeHtml(t('workspace_connections_cancel', 'Cancel'))}</button>
                        <button type="button" class="om-button border danger" data-action="confirm-remove" data-connection-id="${escapeHtml(connection.id)}">${escapeHtml(t('workspace_connections_remove_connection', 'Remove connection'))}</button>
                    </div>
                </div>

                ${actions}
            </form>
        `;
    }

    function renderActivePage({ preserveForm = false, focusAction = '' } = {}) {
        if (state.view === 'list' || !dom.pageRoot) return;
        const item = getActiveItem();
        if (!item) {
            showList();
            return;
        }
        const values = preserveForm ? captureFormValues() : null;
        dom.pageRoot.innerHTML = item.connection ? renderEditPage(item) : renderSetupPage(item);
        restoreFormValues(values);
        bindPageActions(item);
        if (focusAction) {
            window.requestAnimationFrame?.(() => {
                dom.pageRoot?.querySelector(`[data-action="${focusAction}"]`)?.focus();
            });
        }
    }

    /** Switch from the shared catalog to one accessible, full-width provider page. */
    function openConnectionPage(item, trigger = null) {
        state.view = item.connection ? 'edit' : 'setup';
        state.activeProvider = item.provider;
        state.returnFocus = trigger || document.activeElement || null;
        state.removeConfirmationOpen = false;
        // Tool details are intentionally session-on-demand. Opening a provider
        // never reveals a cached/stored count or a stale checked-at timestamp.
        state.toolPreview = { status: 'idle', tools: [], error: '' };
        setPageVisibility(dom.root, false);
        setPageVisibility(dom.page, true);
        renderActivePage();
        window.requestAnimationFrame?.(() => document.getElementById('managedConnectionPageTitle')?.focus());
    }

    function showList({ restoreFocus = true } = {}) {
        const returnFocus = state.returnFocus;
        const activeProvider = state.activeProvider;
        state.view = 'list';
        state.activeProvider = null;
        state.removeConfirmationOpen = false;
        state.toolPreview = { status: 'idle', tools: [], error: '' };
        setPageVisibility(dom.page, false);
        setPageVisibility(dom.root, true);
        if (dom.pageRoot) dom.pageRoot.innerHTML = '';
        renderGrid();
        if (restoreFocus && returnFocus && activeProvider) {
            window.requestAnimationFrame?.(() => {
                // renderGrid replaces its contents, so the original trigger is
                // stale. Find the matching newly rendered provider card first.
                const nextFocus = Array.from(dom.grid?.querySelectorAll('[data-provider]') || [])
                    .find((card) => card.dataset.provider === activeProvider);
                nextFocus?.focus?.();
            });
        }
        state.returnFocus = null;
    }

    function bindPageActions(item) {
        const root = dom.pageRoot;
        if (!root) return;
        root.querySelectorAll('[data-action="back"]').forEach((button) => {
            button.addEventListener('click', () => showList());
        });
        root.querySelectorAll('[data-action="connect"]').forEach((button) => {
            button.addEventListener('click', () => startConnectionFlow(button.dataset.provider));
        });
        root.querySelectorAll('[data-action="reconnect"]').forEach((button) => {
            button.addEventListener('click', () => startConnectionFlow(button.dataset.provider));
        });
        root.querySelectorAll('[data-action="show-tools"]').forEach((button) => {
            button.addEventListener('click', () => showConnectionTools(button.dataset.connectionId));
        });
        root.querySelectorAll('[data-action="remove"]').forEach((button) => {
            button.addEventListener('click', () => {
                state.removeConfirmationOpen = true;
                renderActivePage({ preserveForm: true });
                window.requestAnimationFrame?.(() => root.querySelector('[data-action="cancel-remove"]')?.focus());
            });
        });
        root.querySelectorAll('[data-action="cancel-remove"]').forEach((button) => {
            button.addEventListener('click', () => {
                state.removeConfirmationOpen = false;
                renderActivePage({ preserveForm: true });
                window.requestAnimationFrame?.(() => root.querySelector('[data-action="remove"]')?.focus());
            });
        });
        root.querySelectorAll('[data-action="confirm-remove"]').forEach((button) => {
            button.addEventListener('click', () => removeConnection(button.dataset.connectionId));
        });

        const setupForm = document.getElementById('managedConnectionSetupForm');
        setupForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            createTokenConnection(item);
        });
        const editForm = document.getElementById('managedConnectionEditForm');
        editForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            saveConnection(item);
        });
    }

    async function loadCatalog({ silent = false, force = false } = {}) {
        if (!state.allowed || state.loading) return;
        state.loading = true;
        if (force) {
            state.catalogLoaded = false;
            state.items = [];
        }
        renderGrid();
        try {
            const payload = await fetchJson('/api/v1/connections/catalog');
            const catalogItems = Array.isArray(payload?.items)
                ? payload.items.map(normalizeCatalogItem)
                : [];
            state.items = catalogItems.filter(isProviderAvailable);
            state.catalogLoaded = true;
            window.dispatchEvent?.(new CustomEvent('connections:catalogUpdated', { detail: { items: state.items } }));
            if (state.callbackStatus?.status === 'connected' && state.callbackStatus.provider) {
                const callbackItem = state.items.find((item) => item.provider === state.callbackStatus.provider);
                if (callbackItem?.connection) {
                    state.callbackStatus = null;
                    requestActiveModelSettingsRefresh();
                } else {
                    scheduleCallbackReload(state.callbackStatus.provider);
                }
            }
            renderGrid();
            if (state.view !== 'list') renderActivePage({ preserveForm: true });
        } catch (error) {
            if (!silent && !isConnectionsDisabledForGroup(error)) {
                window.notifyError?.(t('workspace_connections_error_load', 'Failed to load connections.'));
            }
        } finally {
            state.loading = false;
            renderGrid();
        }
    }

    function scheduleCallbackReload(provider) {
        if (state.callbackReloadTimer) window.clearTimeout(state.callbackReloadTimer);
        state.callbackReloadTimer = window.setTimeout(async () => {
            state.callbackReloadTimer = null;
            try {
                await loadCatalog({ silent: true, force: true });
                if (state.items.find((item) => item.provider === provider)?.connection) renderGrid();
            } catch (error) {
                console.warn('Connections callback reload failed', error);
            }
        }, 1200);
    }

    async function startConnectionFlow(provider) {
        try {
            const returnTo = '/workspace/connections';
            const payload = await fetchJson(`/api/v1/connections/providers/${encodeURIComponent(provider)}/connect-url?return_to=${encodeURIComponent(returnTo)}`);
            const authUrl = String(payload?.url || '').trim();
            if (!authUrl) throw new Error(t('workspace_connections_error_start', 'Failed to start connection.'));
            window.location.assign(authUrl);
        } catch (error) {
            window.notifyError?.(t('workspace_connections_error_start', 'Failed to start connection.'));
        }
    }

    async function createTokenConnection(item) {
        const accessToken = String(document.getElementById('managedConnectionAccessToken')?.value || '').trim();
        if (!accessToken) {
            window.notifyError?.(t('workspace_connections_error_access_token_required', 'Access token is required.'));
            return;
        }
        try {
            const connection = await fetchJson(`/api/v1/connections/providers/${encodeURIComponent(item.provider)}/connect`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ access_token: accessToken }),
            });
            state.items = state.items.map((entry) => entry.provider === item.provider ? { ...entry, connection } : entry);
            window.dispatchEvent?.(new CustomEvent('connections:catalogUpdated', { detail: { items: state.items } }));
            requestActiveModelSettingsRefresh();
            window.notifySuccess?.(tf('workspace_connections_success_connected', '{title} connected.', { title: item.title }));
            showList();
        } catch (error) {
            window.notifyError?.(t('workspace_connections_error_connect', 'Failed to connect provider.'));
        }
    }

    async function saveConnection(item) {
        const connection = item?.connection;
        if (!connection) return;
        const payload = {
            enabled: Boolean(document.getElementById('managedConnectionEnabled')?.checked),
            access_token: String(document.getElementById('managedConnectionAccessToken')?.value || '').trim() || null,
        };
        try {
            const updated = await fetchJson(`/api/v1/connections/${encodeURIComponent(connection.id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            state.items = state.items.map((entry) => entry.provider === item.provider ? { ...entry, connection: updated } : entry);
            window.dispatchEvent?.(new CustomEvent('connections:catalogUpdated', { detail: { items: state.items } }));
            requestActiveModelSettingsRefresh();
            window.notifySuccess?.(t('workspace_connections_success_updated', 'Connection updated.'));
            showList();
        } catch (error) {
            window.notifyError?.(t('workspace_connections_error_update', 'Failed to update connection.'));
        }
    }

    /** Discover tools only after an explicit user action and render that response in-place. */
    async function showConnectionTools(connectionId) {
        if (!connectionId || state.toolPreview.status === 'loading') return;
        state.toolPreview = { status: 'loading', tools: [], error: '' };
        renderActivePage({ preserveForm: true, focusAction: 'show-tools' });
        try {
            const payload = await fetchJson(`/api/v1/connections/${encodeURIComponent(connectionId)}/tools`);
            state.toolPreview = {
                status: 'success',
                tools: Array.isArray(payload?.tools) ? payload.tools : [],
                error: '',
            };
            state.items = state.items.map((entry) => (
                String(entry.connection?.id) === String(connectionId)
                    ? { ...entry, connection: payload.connection || entry.connection }
                    : entry
            ));
            renderActivePage({ preserveForm: true, focusAction: 'show-tools' });
        } catch (error) {
            state.toolPreview = {
                status: 'error',
                tools: [],
                error: translateConnectionError(
                    error?.code || getConnectionErrorCode(getActiveItem()?.connection),
                    error?.message || t('workspace_connections_error_show_tools', 'Failed to load tools.'),
                ),
            };
            renderActivePage({ preserveForm: true, focusAction: 'show-tools' });
        }
    }

    async function removeConnection(connectionId) {
        if (!connectionId) return;
        try {
            await fetchJson(`/api/v1/connections/${encodeURIComponent(connectionId)}`, { method: 'DELETE' });
            state.items = state.items.map((item) => (
                String(item.connection?.id) === String(connectionId) ? { ...item, connection: null } : item
            ));
            window.dispatchEvent?.(new CustomEvent('connections:catalogUpdated', { detail: { items: state.items } }));
            requestActiveModelSettingsRefresh();
            window.notifySuccess?.(t('workspace_connections_success_removed', 'Connection removed.'));
            showList();
        } catch (error) {
            window.notifyError?.(t('workspace_connections_error_remove', 'Failed to remove connection.'));
        }
    }

    function consumeCallbackStatus() {
        const params = new URLSearchParams(window.location.search);
        const provider = params.get('connection_provider');
        const status = params.get('connection_status');
        if (!provider || !status) return null;
        const error = params.get('connection_error');
        state.callbackStatus = { provider, status, error };
        if (status === 'connected') {
            // The callback query contains an internal identifier such as
            // "google_drive". Never expose that implementation detail in the
            // notification shown immediately after OAuth returns to Omlorix.
            const title = getProviderTitle(provider);
            window.notifySuccess?.(tf('workspace_connections_success_connected', '{title} connected.', { title }));
        } else if (error) {
            window.notifyError?.(t('workspace_connections_error_callback', 'Connection authorization failed.'));
        }
        params.delete('connection_provider');
        params.delete('connection_status');
        params.delete('connection_error');
        const path = window.location.pathname || '/workspace/connections';
        const nextQuery = params.toString();
        window.history.replaceState({}, '', nextQuery ? `${path}?${nextQuery}` : path);
        return state.callbackStatus;
    }

    let searchDebounce = null;

    function init() {
        if (state.initialized) return;
        state.initialized = true;
        consumeCallbackStatus();
        dom.searchInput?.addEventListener('input', () => {
            clearTimeout(searchDebounce);
            searchDebounce = setTimeout(() => {
                state.searchQuery = dom.searchInput.value.trim();
                renderGrid();
            }, 200);
        });
        window.addEventListener('i18n:updated', () => {
            renderGrid();
            renderActivePage({ preserveForm: true });
        });

        const closePage = () => {
            if (state.removeConfirmationOpen) {
                state.removeConfirmationOpen = false;
                renderActivePage({ preserveForm: true });
                return;
            }
            if (state.view !== 'list') showList();
        };
        if (typeof window.registerEscapeHandler === 'function') {
            window.registerEscapeHandler({
                id: 'managed-connection-page',
                priority: 190,
                isActive: () => state.view !== 'list',
                close: closePage,
            });
        } else {
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && state.view !== 'list') closePage();
            });
        }
    }

    function show() {
        init();
        window.MCPSettings?.show?.();
        if (state.view !== 'list') {
            setPageVisibility(dom.root, false);
            setPageVisibility(dom.page, true);
            renderActivePage({ preserveForm: true });
        }
        if (!state.allowed) {
            renderGrid();
            return;
        }
        if (!state.catalogLoaded || state.callbackStatus?.status === 'connected') {
            loadCatalog({ silent: false, force: Boolean(state.callbackStatus?.status === 'connected') });
            return;
        }
        renderGrid();
    }

    function setPolicy(allowed) {
        state.allowed = Boolean(allowed);
        if (!state.allowed) {
            state.items = [];
            state.catalogLoaded = false;
            showList({ restoreFocus: false });
            return;
        }
        if (state.initialized && window.WorkspaceManager?.getActiveTab?.() === 'connections') {
            loadCatalog({ silent: false });
        }
    }

    window.ConnectionsWorkspace = { init, show, setPolicy, load: loadCatalog, showList };
    document.addEventListener('DOMContentLoaded', init);
})();
