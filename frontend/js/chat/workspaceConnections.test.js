const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const WORKSPACE_CONNECTIONS_PATH = path.join(__dirname, 'workspaceConnections.js');
const CREATE_EDIT_RENDERER_PATH = path.join(__dirname, '..', 'common', 'createEditFormRenderer.js');
const ICONS_PATH = path.join(__dirname, '..', 'common', 'icons.js');
const INDEX_PATH = path.join(__dirname, '..', '..', 'index.html');
const DELETE_MODALS_PATH = path.join(__dirname, 'deleteWarningModals.js');

class MockClassList {
    constructor() {
        this.tokens = new Set();
    }

    add(token) {
        this.tokens.add(token);
    }

    remove(token) {
        this.tokens.delete(token);
    }

    contains(token) {
        return this.tokens.has(token);
    }
}

class MockElement {
    constructor(id = '') {
        this.id = id;
        this.value = '';
        this._innerHTML = '';
        this.queryCache = new Map();
        this.textContent = '';
        this.style = {};
        this.attributes = new Map();
        this.listeners = new Map();
        this.classList = new MockClassList();
    }

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = String(value);
        this.queryCache.clear();
    }

    addEventListener(type, handler) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(handler);
    }

    querySelectorAll(selector) {
        if (this.queryCache.has(selector)) return this.queryCache.get(selector);

        const selectorMatch = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
        if (!selectorMatch) return [];

        const [, selectorAttribute, selectorValue] = selectorMatch;
        const matches = [];
        const tagPattern = /<([a-z][\w-]*)([^>]*)>/gi;
        let tagMatch;

        while ((tagMatch = tagPattern.exec(this.innerHTML)) !== null) {
            const element = new MockElement();
            const attributePattern = /([\w-]+)="([^"]*)"/g;
            let attributeMatch;

            while ((attributeMatch = attributePattern.exec(tagMatch[2])) !== null) {
                element.setAttribute(attributeMatch[1], attributeMatch[2]);
            }

            if (
                element.hasAttribute(selectorAttribute)
                && (selectorValue === undefined || element.attributes.get(selectorAttribute) === selectorValue)
            ) {
                matches.push(element);
            }
        }

        this.queryCache.set(selector, matches);
        return matches;
    }

    setAttribute(name, value = '') {
        this.attributes.set(name, String(value));
        if (name.startsWith('data-')) {
            const datasetKey = name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
            this.dataset ||= {};
            this.dataset[datasetKey] = String(value);
        }
    }

    removeAttribute(name) {
        this.attributes.delete(name);
    }

    hasAttribute(name) {
        return this.attributes.has(name);
    }

    click() {
        const event = { target: this, preventDefault() {} };
        (this.listeners.get('click') || []).forEach((handler) => handler(event));
    }
}

function createDocument(elements) {
    const listeners = new Map();
    return {
        body: { style: {} },
        documentElement: { lang: 'en' },
        addEventListener(type, handler) {
            if (!listeners.has(type)) {
                listeners.set(type, []);
            }
            listeners.get(type).push(handler);
        },
        getElementById(id) {
            return elements.get(id) || null;
        },
    };
}

async function flushPromises() {
    for (let index = 0; index < 5; index += 1) {
        await Promise.resolve();
    }
    await new Promise((resolve) => setImmediate(resolve));
}

function loadConnectionsRuntime({
    catalogPayload,
    catalogResponse = {},
    translations = {},
    icons = null,
}) {
    const elementIds = [
        'connectionsWorkspace',
        'connectionsCatalogBlock',
        'connectionsGrid',
        'connectionsSearchInput',
        'managedConnectionPage',
        'managedConnectionPageRoot',
    ];
    const elements = new Map(elementIds.map((id) => [id, new MockElement(id)]));
    const fetchCalls = [];
    const errorNotifications = [];
    const successNotifications = [];
    const windowObject = {
        currentLanguage: 'en',
        WorkspaceManager: { getActiveTab: () => 'connections' },
        MCPSettings: { show() {} },
        authedFetch: async (url, options) => {
            fetchCalls.push({ url, options });
            return {
                ok: catalogResponse.ok ?? true,
                status: catalogResponse.status ?? 200,
                json: async () => catalogPayload,
            };
        },
        getTranslation: (key, fallback) => translations[key] || fallback,
        formatTranslation: (_key, fallback, variables = {}) => (
            Object.entries(variables).reduce((text, [name, value]) => (
                text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value))
            ), fallback)
        ),
        notifyError(message) {
            errorNotifications.push(message);
        },
        notifySuccess(message) {
            successNotifications.push(message);
        },
        addEventListener() {},
        dispatchEvent() {},
        setTimeout(callback) {
            callback();
            return 1;
        },
        clearTimeout() {},
        location: {
            search: '',
            pathname: '/workspace/connections',
            assign() {},
        },
        history: { replaceState() {} },
    };
    const context = {
        console,
        CustomEvent: class CustomEvent {
            constructor(type, init = {}) {
                this.type = type;
                this.detail = init.detail;
            }
        },
        Icons: icons || { chevronRight: '<svg></svg>' },
        URLSearchParams,
        document: createDocument(elements),
        navigator: { language: 'en' },
        setTimeout: windowObject.setTimeout,
        clearTimeout: windowObject.clearTimeout,
        window: windowObject,
    };
    context.globalThis = context;

    vm.runInNewContext(fs.readFileSync(CREATE_EDIT_RENDERER_PATH, 'utf8'), context, {
        filename: CREATE_EDIT_RENDERER_PATH,
    });
    vm.runInNewContext(fs.readFileSync(WORKSPACE_CONNECTIONS_PATH, 'utf8'), context, {
        filename: WORKSPACE_CONNECTIONS_PATH,
    });

    return {
        elements,
        errorNotifications,
        successNotifications,
        fetchCalls,
        window: windowObject,
    };
}

test('managed connection UI does not expose provider-owned server settings', () => {
    const source = fs.readFileSync(WORKSPACE_CONNECTIONS_PATH, 'utf8');
    const markup = fs.readFileSync(INDEX_PATH, 'utf8');
    const modalDefinitions = fs.readFileSync(DELETE_MODALS_PATH, 'utf8');

    assert.doesNotMatch(source, /(?:setup|edit)Field_(?:display_name|namespace|timeout_seconds)/);
    assert.doesNotMatch(source, /workspace_connections_(?:display_name|namespace|timeout_seconds|stat_timeout)/);
    assert.doesNotMatch(source, /connection\.display_name|connection\.settings/);
    assert.doesNotMatch(source, /connectionsModalOverlay|openModal|activeModal/);
    assert.doesNotMatch(source, /status\.tool_count|checked_at|last_sync_at|formatUpdatedAt/);
    assert.doesNotMatch(source, /oauth_unavailable|OAuth unavailable/);
    assert.match(markup, /id="managedConnectionPage"/);
    assert.match(markup, /id="managedConnectionPageRoot"/);
    assert.doesNotMatch(modalDefinitions, /connectionsModalOverlay|connectionsModalBody/);
});

test('configured providers show reconnect while incomplete providers have no card', async () => {
    const { elements, window } = loadConnectionsRuntime({
        catalogPayload: {
            items: [
                {
                    provider: 'github',
                    title: 'GitHub',
                    setup_mode: 'choice',
                    oauth_ready: true,
                    connection: { id: 'github-1', enabled: true, settings: {}, status: {} },
                },
                {
                    provider: 'notion',
                    title: 'Notion',
                    setup_mode: 'oauth',
                    oauth_ready: true,
                    connection: { id: 'notion-1', enabled: true, settings: {}, status: {} },
                },
                {
                    provider: 'slack',
                    title: 'Slack',
                    setup_mode: 'oauth',
                    oauth_ready: false,
                    connection: { id: 'slack-1', enabled: true, settings: {}, status: {} },
                },
            ],
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    const cards = elements.get('connectionsGrid').querySelectorAll('[data-provider]');
    const reconnectControls = () => (
        elements.get('managedConnectionPageRoot').querySelectorAll('[data-action="reconnect"]')
    );
    assert.deepEqual(
        cards.map((card) => card.dataset.provider),
        ['github', 'notion'],
        'OAuth-incomplete providers must be removed from the user catalog',
    );

    cards.find((card) => card.dataset.provider === 'github').click();
    assert.equal(elements.get('connectionsWorkspace').style.display, 'none');
    assert.equal(elements.get('managedConnectionPage').style.display, '');
    assert.equal(reconnectControls().length, 1, 'GitHub OAuth should render one reconnect control');

    cards.find((card) => card.dataset.provider === 'notion').click();
    assert.equal(reconnectControls().length, 1, 'other configured OAuth providers should remain reconnectable');

    assert.equal(cards.find((card) => card.dataset.provider === 'slack'), undefined);
});

test('enabled catalog connections require a positive connected state', async () => {
    const { elements, window } = loadConnectionsRuntime({
        catalogPayload: {
            items: [{
                provider: 'notion',
                title: 'Notion',
                setup_mode: 'oauth',
                oauth_ready: true,
                connection: {
                    id: 'notion-1',
                    enabled: true,
                    connected: false,
                    state: 'not_connected',
                },
            }],
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    const markup = elements.get('connectionsGrid').innerHTML;
    assert.match(markup, /connection-card-status-badge status-idle/);
    assert.match(markup, /connection-card-status-dot"><\/span>Not connected</);
    assert.doesNotMatch(markup, /connection-card-status-badge status-connected/);
    assert.doesNotMatch(markup, /connection-card is-connected/);
});

test('managed provider page reveals a tool count only after Show tools is selected', async () => {
    const { elements, fetchCalls, window } = loadConnectionsRuntime({
        catalogPayload: {
            items: [{
                provider: 'notion',
                title: 'Notion',
                setup_mode: 'oauth',
                oauth_ready: true,
                connection: {
                    id: 'notion-1',
                    enabled: true,
                    status: {
                        state: 'connected',
                        tool_count: 20,
                        checked_at: '2026-08-03T08:00:00Z',
                    },
                },
            }],
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    elements.get('connectionsGrid').querySelectorAll('[data-provider]')[0].click();
    const pageMarkup = elements.get('managedConnectionPageRoot').innerHTML;

    assert.match(pageMarkup, /data-action="show-tools"/);
    assert.doesNotMatch(pageMarkup, />20 tools</);
    assert.doesNotMatch(pageMarkup, /2026-08-03|hour ago|checked_at/);
    assert.doesNotMatch(pageMarkup, /managed-connection-tools-count/);

    elements.get('managedConnectionPageRoot').querySelectorAll('[data-action="show-tools"]')[0].click();
    await flushPromises();

    assert.equal(fetchCalls[1].url, '/api/v1/connections/notion-1/tools');
    assert.match(elements.get('managedConnectionPageRoot').innerHTML, /managed-connection-tools-count">0 tools/);
});

test('GitHub token failures render an actionable message instead of TaskGroup internals', async () => {
    const { elements, window } = loadConnectionsRuntime({
        catalogPayload: {
            items: [{
                provider: 'github',
                title: 'GitHub',
                setup_mode: 'choice',
                oauth_ready: true,
                connection: {
                    id: 'github-1',
                    enabled: true,
                    status: {
                        state: 'error',
                        last_error: 'unhandled errors in a TaskGroup (1 sub-exception)',
                    },
                },
            }],
        },
        translations: {
            workspace_connections_error_github_token_invalid: 'GitHub token is invalid or expired. Reconnect GitHub with a new token.',
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();
    elements.get('connectionsGrid').querySelectorAll('[data-provider]')[0].click();

    const pageMarkup = elements.get('managedConnectionPageRoot').innerHTML;
    assert.match(pageMarkup, /GitHub token is invalid or expired\. Reconnect GitHub with a new token\./);
    assert.doesNotMatch(pageMarkup, /unhandled errors in a TaskGroup/);
});

test('connections catalog empty response hides the managed connections block', async () => {
    const { elements, fetchCalls, window } = loadConnectionsRuntime({
        catalogPayload: { items: [] },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();

    assert.match(
        elements.get('connectionsGrid').innerHTML,
        /Loading connections/,
        'the loading placeholder should be shown while the catalog request is in flight',
    );
    assert.equal(elements.get('connectionsCatalogBlock').hidden, false);

    await flushPromises();

    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, '/api/v1/connections/catalog');
    assert.equal(elements.get('connectionsGrid').innerHTML, '');
    assert.equal(elements.get('connectionsCatalogBlock').hidden, true);
    assert.equal(elements.get('connectionsCatalogBlock').style.display, 'none');
    assert.doesNotMatch(elements.get('connectionsGrid').innerHTML, /Loading connections/);

    window.ConnectionsWorkspace.show();

    assert.equal(fetchCalls.length, 1, 'an already-loaded empty catalog should not be fetched again on show');
    assert.equal(elements.get('connectionsGrid').innerHTML, '');
    assert.equal(elements.get('connectionsCatalogBlock').hidden, true);
    assert.doesNotMatch(elements.get('connectionsGrid').innerHTML, /Loading connections/);
});

test('connection cards show the provider name without a tool-count subtitle', async () => {
    const { elements, window } = loadConnectionsRuntime({
        catalogPayload: {
            items: [{
                provider: 'github',
                title: 'GitHub',
                setup_mode: 'choice',
                oauth_ready: true,
                connection: {
                    enabled: true,
                    status: { state: 'connected', tool_count: 42 },
                },
            }],
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    const cardMarkup = elements.get('connectionsGrid').innerHTML;
    assert.match(cardMarkup, /connection-card-title">GitHub<\/h4>/);
    assert.doesNotMatch(cardMarkup, /42 tools available/);
    assert.doesNotMatch(cardMarkup, /connection-card-desc/);
});

test('Google Drive managed connection cards use Omlorix neutral artwork', async () => {
    const iconContext = {};
    vm.runInNewContext(fs.readFileSync(ICONS_PATH, 'utf8'), iconContext, {
        filename: ICONS_PATH,
    });
    assert.equal(typeof iconContext.Icons.chatFilesGoogleDrive, 'string');
    assert.equal(
        iconContext.Icons.getConnectionProviderIconKey('google_drive'),
        'google_drive',
    );
    assert.equal(iconContext.Icons.google_drive, iconContext.Icons.slack);
    assert.equal(iconContext.Icons.chatFilesGoogleDrive, iconContext.Icons.google_drive);

    const { elements, window } = loadConnectionsRuntime({
        icons: iconContext.Icons,
        catalogPayload: {
            items: [{
                provider: 'google_drive',
                title: 'Google Drive',
                setup_mode: 'oauth',
                oauth_ready: true,
            }],
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    const cardMarkup = elements.get('connectionsGrid').innerHTML;
    assert.match(cardMarkup, /m6\.3 9\.1 7\.4-3\.7/);
    assert.doesNotMatch(cardMarkup, /drive_2026|#ffe921|#0ebc5f/i);
    assert.doesNotMatch(cardMarkup, /connection-card-logo is-fallback/);
    assert.doesNotMatch(cardMarkup, />GO<\/span>/);
});

test('restricted Slack and Notion marks use the reviewed neutral connection artwork', async () => {
    const iconContext = {};
    vm.runInNewContext(fs.readFileSync(ICONS_PATH, 'utf8'), iconContext, {
        filename: ICONS_PATH,
    });

    assert.equal(typeof iconContext.Icons.slack, 'string');
    assert.equal(iconContext.Icons.slack, iconContext.Icons.notion);
    assert.match(iconContext.Icons.slack, /m6\.3 9\.1 7\.4-3\.7/);
    assert.doesNotMatch(iconContext.Icons.slack, /#e01e5a|#36c5f0/i);

    const { elements, window } = loadConnectionsRuntime({
        icons: iconContext.Icons,
        catalogPayload: {
            items: [{
                provider: 'slack',
                title: 'Slack',
                setup_mode: 'oauth',
                oauth_ready: true,
            }],
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    const cardMarkup = elements.get('connectionsGrid').innerHTML;
    assert.match(cardMarkup, /data-provider="slack"/);
    assert.match(cardMarkup, /m6\.3 9\.1 7\.4-3\.7/);
    assert.doesNotMatch(cardMarkup, /#e01e5a|#36c5f0/i);
    assert.doesNotMatch(cardMarkup, /M164\.09\.608/);
});

test('file source adapters show usage guidance instead of LLM tools', async () => {
    const { elements, window } = loadConnectionsRuntime({
        catalogPayload: {
            items: [{
                provider: 'google_drive',
                title: 'Google Drive',
                setup_mode: 'oauth',
                oauth_ready: true,
                managed_mcp: false,
                connection_type: 'file_source_adapter',
                file_source_available: true,
                llm_available: false,
                connection: {
                    id: 'drive-1',
                    enabled: true,
                    connected: true,
                    status: { state: 'connected' },
                },
            }],
        },
        translations: {
            workspace_connections_file_source_adapter_title: 'File source adapter',
            workspace_connections_file_source_adapter_desc: 'Use {title} in the chat file dropdown. This connection cannot be used by the LLM model.',
        },
    });

    window.ConnectionsWorkspace.setPolicy(true);
    window.ConnectionsWorkspace.show();
    await flushPromises();

    elements.get('connectionsGrid').querySelectorAll('[data-provider]')[0].click();
    const pageMarkup = elements.get('managedConnectionPageRoot').innerHTML;

    assert.match(pageMarkup, /managed-connection-file-source-info/);
    assert.match(pageMarkup, /File source adapter/);
    assert.match(pageMarkup, /chat file dropdown/);
    assert.doesNotMatch(pageMarkup, /managed-connection-tool-discovery/);
    assert.doesNotMatch(pageMarkup, /data-action="show-tools"/);
});

test('visible connections page suppresses the expected group-disabled catalog response', async () => {
    const { errorNotifications, fetchCalls, window } = loadConnectionsRuntime({
        catalogPayload: { detail: 'Connections are disabled for your group.' },
        catalogResponse: { ok: false, status: 403 },
        translations: {
            workspace_connections_error_load: 'Verbindungen konnten nicht geladen werden.',
        },
    });

    // Opening the page before chat setup has loaded leaves managed connections
    // disabled until the group policy is applied.
    window.ConnectionsWorkspace.show();
    window.ConnectionsWorkspace.setPolicy(true);
    await flushPromises();

    assert.equal(fetchCalls.length, 1);
    assert.deepEqual(errorNotifications, []);
});

test('visible connections page still reports unexpected catalog errors', async () => {
    const { errorNotifications, fetchCalls, window } = loadConnectionsRuntime({
        catalogPayload: { detail: 'Internal server error.' },
        catalogResponse: { ok: false, status: 500 },
        translations: {
            workspace_connections_error_load: 'Verbindungen konnten nicht geladen werden.',
        },
    });

    window.ConnectionsWorkspace.show();
    window.ConnectionsWorkspace.setPolicy(true);
    await flushPromises();

    assert.equal(fetchCalls.length, 1);
    assert.deepEqual(errorNotifications, [
        'Verbindungen konnten nicht geladen werden.',
    ]);
});

test('OAuth connection success messages use readable names for every managed provider', async () => {
    const providers = {
        notion: 'Notion',
        github: 'GitHub',
        gmail: 'Gmail',
        google_calendar: 'Google Calendar',
        google_drive: 'Google Drive',
        slack: 'Slack',
        // This verifies the generic fallback for a provider added in the
        // future before a dedicated branded label has been introduced.
        custom_service: 'Custom Service',
    };

    for (const [provider, expectedTitle] of Object.entries(providers)) {
        const { successNotifications, window } = loadConnectionsRuntime({
            catalogPayload: { items: [] },
        });
        window.location.search = `?connection_provider=${provider}&connection_status=connected`;

        // The callback notification is intentionally emitted before the
        // catalog request resolves, which verifies that it does not depend on
        // catalog data to turn the provider ID into a readable product name.
        window.ConnectionsWorkspace.setPolicy(true);
        window.ConnectionsWorkspace.show();
        await flushPromises();

        assert.deepEqual(successNotifications, [`${expectedTitle} connected.`], provider);
    }
});

test('managed connection page translations exist in every supported locale', () => {
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const requiredKeys = [
        'workspace_connections_status_not_connected',
        'workspace_connections_setup_page_subtitle',
        'workspace_connections_manage_page_subtitle',
        'workspace_connections_file_source_setup_page_subtitle',
        'workspace_connections_file_source_manage_page_subtitle',
        'workspace_connections_file_source_adapter_title',
        'workspace_connections_file_source_adapter_desc',
        'workspace_connections_oauth_sign_in_title',
        'workspace_connections_token_help',
        'workspace_connections_credentials_title',
        'workspace_connections_credentials_desc',
        'workspace_connections_tools_title',
        'workspace_connections_tools_on_demand_desc',
        'workspace_connections_show_tools',
        'workspace_connections_refresh_tools',
        'workspace_connections_tools_loading',
        'workspace_connections_tools_loaded_desc',
        'workspace_connections_mcp_tool_count_one',
        'workspace_connections_mcp_tool_count_other',
        'workspace_connections_tools_empty',
        'workspace_connections_error_show_tools',
        'workspace_connections_error_github_token_invalid',
        'workspace_connections_error_github_access_denied',
        'workspace_connections_error_connection_failed',
        'workspace_connections_error_authentication_failed',
        'workspace_connections_error_access_denied',
    ];

    for (const locale of fs.readdirSync(localeRoot)) {
        const localePath = path.join(localeRoot, locale, 'index.json');
        if (!fs.existsSync(localePath)) continue;
        const messages = JSON.parse(fs.readFileSync(localePath, 'utf8'));
        for (const key of requiredKeys) {
            assert.equal(typeof messages[key], 'string', `${locale} is missing ${key}`);
            assert.notEqual(messages[key].trim(), '', `${locale} has an empty ${key}`);
        }
    }
});
