const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const SIDEBAR_PATH = path.join(__dirname, 'sidebar.js');
const INDEX_PATH = path.join(__dirname, '..', '..', 'index.html');
const ICONS_PATH = path.join(__dirname, '..', 'common', 'icons.js');

/**
 * Execute the production menu definition and renderer with the minimum DOM
 * surface required to inspect its permission-aware output.
 */
function loadProfileDropdownRenderer() {
    const source = fs.readFileSync(SIDEBAR_PATH, 'utf8');
    const start = source.indexOf('const SIDEBAR_PROFILE_ACTIONS = Object.freeze([');
    const end = source.indexOf('function getAccountInitials(', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);

    const profileDropdown = {
        dataset: {},
        innerHTML: '',
        querySelector() {
            return null;
        },
    };
    const window = {
        getTranslation(key) {
            return `[${key}]`;
        },
    };
    const localStorage = {
        getItem() {
            return 'false';
        },
    };
    const Icons = {
        settings: '<svg data-icon="settings"></svg>',
        security: '<svg data-icon="admin-settings"></svg>',
        archive: '<svg data-icon="archive"></svg>',
        logout: '<svg data-icon="logout"></svg>',
        plus: '<svg data-icon="plus"></svg>',
    };

    const renderer = Function(
        'window',
        'localStorage',
        'profileDropdown',
        'Icons',
        `let accountSummary = null;
         let accountList = null;
         let addAccountButtonLabel = null;
         let accountManager = null;
         let accountPayload = null;
         let accountControlsRenderKey = '';
         function renderAccountControls() {}
         ${source.slice(start, end)}
         return { renderSidebarProfileDropdown };`,
    )(window, localStorage, profileDropdown, Icons);

    return { profileDropdown, renderer };
}

/**
 * Execute the production delegated click handler with focused account-service
 * fakes so error and slot-type behavior are tested without a browser DOM.
 */
function loadProfileDropdownClickHandler({
    initialAccountPayload = null,
    loadAccounts = async () => null,
    removeAccount = async () => null,
} = {}) {
    const source = fs.readFileSync(SIDEBAR_PATH, 'utf8');
    const start = source.indexOf('async function handleSidebarProfileDropdownClick(event)');
    const end = source.indexOf("profileDropdown?.addEventListener('click'", start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);

    const notifications = [];
    const removeCalls = [];
    const renderCalls = [];
    const addAccountCalls = [];
    const window = {
        startAddAccount(slot) {
            addAccountCalls.push(slot);
        },
        async removeBrowserAccount(slot, options) {
            removeCalls.push({ slot, options });
            return removeAccount(slot, options);
        },
        async switchBrowserAccount() {},
    };

    const handler = Function(
        'window',
        'initialAccountPayload',
        'loadProfileAccounts',
        'notifyError',
        'sidebarT',
        'runSidebarProfileAction',
        'renderAccountManager',
        'renderAccountControls',
        `let accountPayload = initialAccountPayload;
         let accountManagerOpen = false;
         ${source.slice(start, end)}
         return handleSidebarProfileDropdownClick;`,
    )(
        window,
        initialAccountPayload,
        loadAccounts,
        (message) => notifications.push(message),
        (key, fallback) => ({ key, fallback }),
        () => {},
        () => renderCalls.push('manager'),
        () => renderCalls.push('controls'),
    );

    return { addAccountCalls, handler, notifications, removeCalls, renderCalls };
}

function runSettingsProfileAction({ overlay, sidebarOpen }) {
    const source = fs.readFileSync(SIDEBAR_PATH, 'utf8');
    const start = source.indexOf('function runSidebarProfileAction(action)');
    const end = source.indexOf('/**\n * Handle every dynamic dropdown control', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);

    const events = [];
    const runAction = Function(
        'document',
        'isOverlayMode',
        'setProfileDropdownOpen',
        'closeSidebar',
        'openUserSettings',
        'window',
        `${source.slice(start, end)}\nreturn runSidebarProfileAction;`,
    )(
        { body: { classList: { contains: name => name === 'sidebar-open' && sidebarOpen } } },
        () => overlay,
        isOpen => events.push(['profile', isOpen]),
        options => events.push(['sidebar', options]),
        () => events.push(['settings']),
        {},
    );

    runAction('settings');
    return events;
}

test('profile dropdown HTML is only an empty JavaScript mount point', () => {
    const html = fs.readFileSync(INDEX_PATH, 'utf8');

    assert.match(
        html,
        /<div class="sidebar-profile-dropdown" id="sidebarProfileDropdown" aria-hidden="true" inert><\/div>/,
    );
    for (const dynamicId of [
        'sidebarProfileAccounts',
        'sidebarAddAccountButton',
        'openUserSettingsButton',
        'openAdminSettingsButton',
        'sidebarArchivedChats',
        'sidebarLogout',
    ]) {
        assert.doesNotMatch(html, new RegExp(`id=["']${dynamicId}["']`));
    }
});

test('profile dropdown renderer includes admin settings only for admins', () => {
    const runtime = loadProfileDropdownRenderer();
    const iconsSource = fs.readFileSync(ICONS_PATH, 'utf8');

    runtime.renderer.renderSidebarProfileDropdown(false);
    assert.match(runtime.profileDropdown.innerHTML, /id="openUserSettingsButton"/);
    assert.match(runtime.profileDropdown.innerHTML, /id="sidebarArchivedChats"/);
    assert.match(runtime.profileDropdown.innerHTML, /id="sidebarLogout"/);
    assert.doesNotMatch(runtime.profileDropdown.innerHTML, /id="openAdminSettingsButton"/);
    assert.equal(runtime.profileDropdown.dataset.adminMenu, 'false');

    runtime.renderer.renderSidebarProfileDropdown('admin');
    assert.match(runtime.profileDropdown.innerHTML, /id="openAdminSettingsButton"/);
    assert.match(runtime.profileDropdown.innerHTML, /data-icon="admin-settings"/);
    assert.equal(runtime.profileDropdown.dataset.adminMenu, 'true');
    assert.match(iconsSource, /^\s*security:\s*['"]<svg/m);
});

test('opening settings closes an open overlay sidebar first', () => {
    assert.deepEqual(runSettingsProfileAction({ overlay: true, sidebarOpen: true }), [
        ['profile', false],
        ['sidebar', { persist: false }],
        ['settings'],
    ]);
    assert.deepEqual(runSettingsProfileAction({ overlay: false, sidebarOpen: true }), [
        ['profile', false],
        ['settings'],
    ]);
});

test('add-account load rejection reports the translated error and stops', async () => {
    const runtime = loadProfileDropdownClickHandler({
        loadAccounts: async () => {
            throw new Error('accounts unavailable');
        },
    });
    const event = {
        preventDefault() {},
        stopPropagation() {},
        target: {
            closest(selector) {
                return selector === '[data-sidebar-profile-action]'
                    ? { dataset: { sidebarProfileAction: 'add-account' } }
                    : null;
            },
        },
    };

    await runtime.handler(event);

    assert.deepEqual(runtime.notifications, [{
        key: 'account_load_failed',
        fallback: 'Failed to load accounts.',
    }]);
    assert.deepEqual(runtime.addAccountCalls, []);
    assert.deepEqual(runtime.renderCalls, []);
});

test('removing a string-valued active slot preserves active-account reload', async () => {
    const runtime = loadProfileDropdownClickHandler({
        initialAccountPayload: { active_slot: '2' },
    });
    const removeButton = {
        getAttribute(name) {
            return name === 'data-account-remove' ? '2' : null;
        },
    };
    const event = {
        target: {
            closest(selector) {
                if (selector === '#sidebarAccountManager [data-account-remove]') {
                    return removeButton;
                }
                return null;
            },
        },
    };

    await runtime.handler(event);

    assert.deepEqual(runtime.removeCalls, [{ slot: 2, options: { reload: true } }]);
    assert.deepEqual(runtime.renderCalls, []);
});

test('dynamic profile controls use one persistent delegated click handler', () => {
    const sidebarSource = fs.readFileSync(SIDEBAR_PATH, 'utf8');
    const archivedChatsSource = fs.readFileSync(path.join(__dirname, 'archivedChats.js'), 'utf8');
    const userSettingsSource = fs.readFileSync(path.join(__dirname, 'userSettings', 'init.js'), 'utf8');

    assert.match(sidebarSource, /profileDropdown\?\.addEventListener\('click'/);
    assert.match(sidebarSource, /async function handleSidebarProfileDropdownClick\(event\)/);
    assert.doesNotMatch(archivedChatsSource, /getElementById\('sidebarArchivedChats'\)/);
    assert.doesNotMatch(userSettingsSource, /getElementById\('openUserSettingsButton'\)/);
});
