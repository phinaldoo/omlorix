const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');


const INIT_PATH = path.join(__dirname, 'init.js');
const PRIVACY_NOTICE_PATH = path.join(__dirname, 'privacyPolicyNotice.js');
const WELCOME_PATH = path.join(__dirname, 'welcome.js');
const INDEX_PATH = path.join(__dirname, '..', '..', 'index.html');


/** Add the browser event-target methods used for modal handoff events. */
function addWindowEventSupport(windowObject) {
    const listeners = new Map();
    windowObject.addEventListener = (type, listener, options = {}) => {
        const entries = listeners.get(type) || [];
        entries.push({ listener, once: Boolean(options.once) });
        listeners.set(type, entries);
    };
    windowObject.dispatchEvent = (event) => {
        const entries = [...(listeners.get(event.type) || [])];
        entries.forEach((entry) => entry.listener(event));
        listeners.set(event.type, entries.filter((entry) => !entry.once));
        return true;
    };
    return listeners;
}


/** Execute chat bootstrap with the minimal browser contract needed by init.js. */
async function runChatBootstrapWithLocaleFailure() {
    const setup = { language: 'en' };
    const dispatchedEvents = [];
    const warnings = [];
    const noOpInitializers = [
        'initUserProfileUI',
        'initProjectsSidebar',
        'initAutomationsSidebar',
        'initWorkspaceTodos',
        'initWorkspaceNotes',
        'initWorkspaceMemories',
        'initWorkspaceBookmarks',
        'initWorkspaceConnections',
        'initWorkspaceSkills',
        'initWorkspaceAgents',
        'initWorkspacePrompts',
        'initChatFullWidth',
        'initChatBoxWarning',
        'initProfilePicture',
        'setTheme',
        'setColorTheme',
        'initializeThemeSettings',
        'initWelcomeMessage',
        'initNotificationBadge',
    ];
    const windowObject = {
        authedFetch: async () => ({ ok: true, json: async () => setup }),
        applyDetectedLocaleDefaults: async () => {
            throw new Error('locale update failed');
        },
    };
    const context = {
        console: {
            warn(...args) { warnings.push(args); },
        },
        CustomEvent: class CustomEvent {
            constructor(type, options) {
                this.type = type;
                this.detail = options?.detail;
            }
        },
        document: {
            body: { style: {} },
            addEventListener() {},
            dispatchEvent(event) { dispatchedEvents.push(event); },
        },
        localStorage: { setItem() {} },
        window: windowObject,
    };
    noOpInitializers.forEach((name) => { context[name] = () => {}; });
    context.globalThis = context;

    // Capture the normally fire-and-forget promise so the test can observe the
    // complete bootstrap without changing production exports.
    const source = fs.readFileSync(INIT_PATH, 'utf8').replace(
        /initChatSetup\(\);\s*$/,
        'this.__initPromise = initChatSetup();',
    );
    vm.runInNewContext(source, context, { filename: INIT_PATH });
    await context.__initPromise;
    return { dispatchedEvents, setup, warnings, window: windowObject };
}


/** Build one lightweight element that records click handlers. */
function createElement() {
    const attributes = new Map();
    const classes = new Set();
    return {
        attributes,
        classList: {
            add(...names) { names.forEach((name) => classes.add(name)); },
            contains(name) { return classes.has(name); },
            remove(...names) { names.forEach((name) => classes.delete(name)); },
        },
        dataset: {},
        disabled: false,
        focusCount: 0,
        hidden: false,
        isConnected: true,
        listeners: {},
        style: {},
        textContent: '',
        addEventListener(type, listener) {
            this.listeners[type] = listener;
        },
        focus() { this.focusCount += 1; },
        getAttribute(name) { return attributes.get(name) ?? null; },
        hasAttribute(name) { return attributes.has(name); },
        querySelectorAll() { return []; },
        removeAttribute(name) { attributes.delete(name); },
        removeEventListener(type, listener) {
            if (this.listeners[type] === listener) delete this.listeners[type];
        },
        setAttribute(name, value) { attributes.set(name, String(value)); },
    };
}


test('privacy-policy notice provides a complete accessible modal lifecycle', async () => {
    const pageControl = createElement();
    const preInertRegion = createElement();
    const viewButton = createElement();
    const dismissButton = createElement();
    const actionError = createElement();
    const dialog = createElement();
    const overlay = createElement();
    const body = createElement();
    const documentListeners = new Map();
    let chatSetupReadyListener = null;
    let escapeHandler = null;
    let escapeUnregistered = false;
    let overlayRemoved = false;
    let resolvedEvent = null;
    const noticeRequests = [];
    let noticeAttempt = 0;
    let notificationCount = 0;

    preInertRegion.setAttribute('inert', '');
    actionError.hidden = true;
    body.children = [pageControl, preInertRegion];
    body.appendChild = (element) => {
        body.children.push(element);
        element.isConnected = true;
    };

    const documentObject = {
        activeElement: pageControl,
        body,
        addEventListener(type, listener) {
            if (type === 'chatSetupReady') chatSetupReadyListener = listener;
            const listeners = documentListeners.get(type) || [];
            listeners.push(listener);
            documentListeners.set(type, listeners);
        },
        createElement() { return overlay; },
        removeEventListener(type, listener) {
            const listeners = documentListeners.get(type) || [];
            documentListeners.set(type, listeners.filter((candidate) => candidate !== listener));
        },
    };
    [pageControl, viewButton, dismissButton, dialog].forEach((element) => {
        element.focus = (options) => {
            element.focusCount += 1;
            element.lastFocusOptions = options;
            documentObject.activeElement = element;
        };
    });
    dialog.contains = (element) => [dialog, viewButton, dismissButton, actionError].includes(element);
    dialog.querySelectorAll = () => [viewButton, dismissButton];
    overlay.querySelector = (selector) => ({
        '.warning-card': dialog,
        '[data-action="view"]': viewButton,
        '[data-action="dismiss"]': dismissButton,
        '[data-action-error]': actionError,
    }[selector] || null);
    overlay.remove = () => {
        overlayRemoved = true;
        overlay.isConnected = false;
        body.children = body.children.filter((element) => element !== overlay);
    };

    const windowObject = {
        ChatSanitizer: {
            sanitizePolicyNoticeHtml(value) { return value; },
        },
        async authedFetch(url, options) {
            noticeRequests.push({ url, options });
            noticeAttempt += 1;
            if (noticeAttempt === 1) {
                return { ok: false, status: 500, json: async () => ({}) };
            }
            return { ok: true };
        },
        chatSetup: {
            privacy_policy_notice: {
                revision: 7,
                notice_mode: 'modal',
                notice_message_html: '',
                should_show_notice: true,
            },
        },
        dispatchEvent(event) { resolvedEvent = event; },
        getTranslation(_key, fallback) { return fallback; },
        notifyError() { notificationCount += 1; },
        open() {},
        registerEscapeHandler(handler) {
            escapeHandler = handler;
            return {
                id: handler.id,
                unregister() { escapeUnregistered = true; },
            };
        },
        requestAnimationFrame(callback) { callback(); },
    };
    const context = {
        console: { error() {} },
        CustomEvent: class CustomEvent {
            constructor(type, options = {}) {
                this.type = type;
                this.detail = options.detail;
            }
        },
        document: documentObject,
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(PRIVACY_NOTICE_PATH, 'utf8'), context, {
        filename: PRIVACY_NOTICE_PATH,
    });

    chatSetupReadyListener();

    assert.match(overlay.innerHTML, /role="dialog" aria-modal="true" aria-labelledby="privacyPolicyNoticeModalTitle"/);
    assert.match(overlay.innerHTML, /<h3[^>]*id="privacyPolicyNoticeModalTitle"/);
    assert.match(overlay.innerHTML, /data-action-error role="alert" hidden/);
    assert.equal(pageControl.hasAttribute('inert'), true);
    assert.equal(preInertRegion.hasAttribute('inert'), true);
    assert.equal(preInertRegion.hasAttribute('data-privacy-policy-notice-managed-inert'), false);
    assert.equal(documentObject.activeElement, viewButton);
    assert.equal(viewButton.lastFocusOptions.preventScroll, true);
    assert.equal(escapeHandler.id, 'privacy-policy-notice-modal');
    assert.equal(escapeHandler.isActive(), true);

    const forwardTab = {
        key: 'Tab',
        shiftKey: false,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
    };
    documentObject.activeElement = dismissButton;
    documentListeners.get('keydown')[0](forwardTab);
    assert.equal(forwardTab.defaultPrevented, true);
    assert.equal(documentObject.activeElement, viewButton);

    const backwardTab = {
        key: 'Tab',
        shiftKey: true,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
    };
    documentObject.activeElement = viewButton;
    documentListeners.get('keydown')[0](backwardTab);
    assert.equal(backwardTab.defaultPrevented, true);
    assert.equal(documentObject.activeElement, dismissButton);

    documentObject.activeElement = pageControl;
    documentListeners.get('focusin')[0]({ target: pageControl });
    assert.equal(documentObject.activeElement, viewButton);

    await escapeHandler.close();

    assert.equal(overlayRemoved, false);
    assert.equal(actionError.hidden, false);
    assert.equal(actionError.textContent, 'Failed to update privacy policy notice status.');
    assert.equal(notificationCount, 0);
    assert.equal(pageControl.hasAttribute('inert'), true);
    assert.equal(escapeHandler.isActive(), true);

    await escapeHandler.close();

    assert.equal(noticeRequests.length, 2);
    assert.equal(noticeRequests[1].url, '/api/v1/users/privacy-policy/notice');
    assert.deepEqual(JSON.parse(noticeRequests[1].options.body), { action: 'dismiss', revision: 7 });
    assert.equal(overlayRemoved, true);
    assert.equal(pageControl.hasAttribute('inert'), false);
    assert.equal(preInertRegion.hasAttribute('inert'), true);
    assert.equal(documentObject.activeElement, pageControl);
    assert.equal(escapeUnregistered, true);
    assert.equal(documentListeners.get('keydown').length, 0);
    assert.equal(documentListeners.get('focusin').length, 0);
    assert.equal(resolvedEvent.type, 'privacyPolicyNoticeResolved');
    assert.equal(resolvedEvent.detail.dismissed, true);
});


test('locale-default persistence failure does not stop chat setup', async () => {
    const result = await runChatBootstrapWithLocaleFailure();

    assert.equal(result.window.chatSetup, result.setup);
    assert.equal(result.dispatchedEvents.length, 1);
    assert.equal(result.dispatchedEvents[0].type, 'chatSetupReady');
    assert.equal(result.dispatchedEvents[0].detail, result.setup);
    assert.equal(result.warnings.length, 1);
});


test('privacy review stays on the welcome modal when dismissal fails', async () => {
    const elements = {
        firstRunWelcomeOverlay: createElement(),
        firstRunWelcomeModal: createElement(),
        firstRunWelcomePrivacy: createElement(),
        firstRunWelcomeCloseButton: createElement(),
        welcomeReviewPrivacyBtn: createElement(),
        welcomeDismissBtn: createElement(),
        chatBoxInput: createElement(),
    };
    let settingsOpenCount = 0;
    let errorCount = 0;
    const windowObject = {
        authedFetch: async () => ({ ok: false, status: 500 }),
        chatSetup: { show_welcome_card: true },
        clearTimeout() {},
        notifyError() { errorCount += 1; },
        openUserSettings() { settingsOpenCount += 1; },
        requestAnimationFrame(callback) { callback(); },
        setTimeout(callback) { callback(); return 1; },
    };
    const context = {
        console: { error() {} },
        document: {
            activeElement: null,
            body: createElement(),
            documentElement: { getAttribute() { return 'true'; } },
            getElementById(id) { return elements[id] || null; },
        },
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(WELCOME_PATH, 'utf8'), context, {
        filename: WELCOME_PATH,
    });

    context.initFirstRunWelcomeCard({
        personal_info_access_enabled: false,
        show_welcome_card: true,
    });
    const dismissed = await context.dismissFirstRunWelcomeModal({ reviewPrivacy: true });

    assert.equal(dismissed, false);
    assert.equal(settingsOpenCount, 0);
    assert.equal(errorCount, 1);
    assert.equal(elements.firstRunWelcomeOverlay.hidden, false);
    assert.equal(elements.firstRunWelcomeOverlay.getAttribute('aria-hidden'), 'false');
    assert.equal(windowObject.chatSetup.show_welcome_card, true);
});


test('successful privacy review closes the modal before opening security settings', async () => {
    const elements = {
        firstRunWelcomeOverlay: createElement(),
        firstRunWelcomeModal: createElement(),
        firstRunWelcomePrivacy: createElement(),
        firstRunWelcomeCloseButton: createElement(),
        welcomeReviewPrivacyBtn: createElement(),
        welcomeDismissBtn: createElement(),
        chatBoxInput: createElement(),
    };
    const openedSections = [];
    const windowObject = {
        authedFetch: async () => ({ ok: true, status: 200 }),
        chatSetup: { show_welcome_card: true },
        clearTimeout() {},
        async openUserSettings(section) {
            // Settings must capture a visible page control, never the hidden
            // welcome review button, as its eventual return-focus target.
            assert.equal(elements.chatBoxInput.focusCount, 1);
            openedSections.push(section);
        },
        requestAnimationFrame(callback) { callback(); },
        setTimeout(callback) { callback(); return 1; },
    };
    const body = createElement();
    body.style.overflow = 'clip';
    const context = {
        console: { error() {} },
        document: {
            activeElement: body,
            body,
            documentElement: { getAttribute() { return 'true'; } },
            getElementById(id) { return elements[id] || null; },
        },
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(WELCOME_PATH, 'utf8'), context, {
        filename: WELCOME_PATH,
    });

    context.initFirstRunWelcomeCard({
        personal_info_access_enabled: true,
        show_welcome_card: true,
    });
    assert.equal(elements.firstRunWelcomeOverlay.hidden, false);
    assert.equal(elements.firstRunWelcomeOverlay.getAttribute('aria-hidden'), 'false');
    assert.equal(body.style.overflow, 'hidden');

    const dismissed = await context.dismissFirstRunWelcomeModal({ reviewPrivacy: true });

    assert.equal(dismissed, true);
    assert.equal(elements.firstRunWelcomeOverlay.hidden, true);
    assert.equal(elements.firstRunWelcomeOverlay.getAttribute('aria-hidden'), 'true');
    assert.equal(body.style.overflow, 'clip');
    assert.equal(windowObject.chatSetup.show_welcome_card, false);
    assert.deepEqual(openedSections, ['security']);
});


test('reopening cancels an animated welcome close without a stale hide or pending promise', async () => {
    const elements = {
        firstRunWelcomeOverlay: createElement(),
        firstRunWelcomeModal: createElement(),
        firstRunWelcomePrivacy: createElement(),
        firstRunWelcomeCloseButton: createElement(),
        welcomeReviewPrivacyBtn: createElement(),
        welcomeDismissBtn: createElement(),
        chatBoxInput: createElement(),
    };
    const timers = new Map();
    let nextTimer = 1;
    const windowObject = {
        clearTimeout(timer) { timers.delete(timer); },
        matchMedia() { return { matches: false }; },
        requestAnimationFrame(callback) { callback(); },
        setTimeout(callback) {
            const timer = nextTimer;
            nextTimer += 1;
            timers.set(timer, callback);
            return timer;
        },
    };
    const context = {
        console,
        document: {
            activeElement: null,
            body: createElement(),
            documentElement: { getAttribute() { return null; } },
            getElementById(id) { return elements[id] || null; },
        },
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(WELCOME_PATH, 'utf8'), context, {
        filename: WELCOME_PATH,
    });
    const visibleSetup = {
        personal_info_access_enabled: false,
        show_welcome_card: true,
    };

    context.initFirstRunWelcomeCard(visibleSetup);
    const firstClose = context.closeFirstRunWelcomeModal();
    const staleAnimationEnd = elements.firstRunWelcomeModal.listeners.animationend;

    assert.equal(context.closeFirstRunWelcomeModal(), firstClose);
    assert.equal(typeof staleAnimationEnd, 'function');
    assert.equal(timers.size, 1);

    context.initFirstRunWelcomeCard(visibleSetup);

    assert.equal(await firstClose, false);
    assert.equal(timers.size, 0);
    assert.equal(elements.firstRunWelcomeModal.listeners.animationend, undefined);
    assert.equal(elements.firstRunWelcomeOverlay.hidden, false);
    assert.equal(elements.firstRunWelcomeOverlay.getAttribute('aria-hidden'), 'false');

    // Even a queued callback that escaped browser cancellation cannot hide the
    // reopened modal because the old close operation is already settled.
    staleAnimationEnd({ target: elements.firstRunWelcomeModal });
    assert.equal(elements.firstRunWelcomeOverlay.hidden, false);

    const policyClose = context.closeFirstRunWelcomeModal();
    const secondStaleAnimationEnd = elements.firstRunWelcomeModal.listeners.animationend;
    context.initFirstRunWelcomeCard({ show_welcome_card: false });

    assert.equal(await policyClose, false);
    secondStaleAnimationEnd({ target: elements.firstRunWelcomeModal });
    assert.equal(timers.size, 0);
    assert.equal(elements.firstRunWelcomeModal.listeners.animationend, undefined);
    assert.equal(elements.firstRunWelcomeOverlay.hidden, true);
    assert.equal(elements.firstRunWelcomeOverlay.getAttribute('aria-hidden'), 'true');
});


test('welcome waits for a pending privacy-policy modal to resolve', () => {
    const elements = {
        firstRunWelcomeOverlay: createElement(),
        firstRunWelcomeModal: createElement(),
        firstRunWelcomePrivacy: createElement(),
        firstRunWelcomeCloseButton: createElement(),
        welcomeReviewPrivacyBtn: createElement(),
        welcomeDismissBtn: createElement(),
        chatBoxInput: createElement(),
    };
    elements.firstRunWelcomeOverlay.hidden = true;
    elements.firstRunWelcomeOverlay.setAttribute('aria-hidden', 'true');
    const chatSetup = {
        personal_info_access_enabled: false,
        show_welcome_card: true,
        privacy_policy_notice: {
            revision: 3,
            notice_mode: 'modal',
            should_show_notice: true,
        },
    };
    const windowObject = {
        chatSetup,
        clearTimeout() {},
        requestAnimationFrame(callback) { callback(); },
        setTimeout(callback) { callback(); return 1; },
    };
    const eventListeners = addWindowEventSupport(windowObject);
    const context = {
        console,
        document: {
            activeElement: null,
            body: createElement(),
            documentElement: { getAttribute() { return 'true'; } },
            getElementById(id) { return elements[id] || null; },
        },
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(WELCOME_PATH, 'utf8'), context, {
        filename: WELCOME_PATH,
    });

    context.initFirstRunWelcomeCard(chatSetup);
    context.initFirstRunWelcomeCard(chatSetup);

    assert.equal(elements.firstRunWelcomeOverlay.hidden, true);
    assert.equal(elements.welcomeReviewPrivacyBtn.focusCount, 0);
    assert.equal(eventListeners.get('privacyPolicyNoticeResolved').length, 1);

    chatSetup.privacy_policy_notice.should_show_notice = false;
    windowObject.dispatchEvent({ type: 'privacyPolicyNoticeResolved' });

    assert.equal(elements.firstRunWelcomeOverlay.hidden, false);
    assert.equal(elements.firstRunWelcomeOverlay.getAttribute('aria-hidden'), 'false');
    assert.equal(elements.welcomeReviewPrivacyBtn.focusCount, 1);
    assert.equal(eventListeners.get('privacyPolicyNoticeResolved').length, 0);
});


test('privacy-policy resolution removes its modal before handing off to welcome', async () => {
    const viewButton = createElement();
    const dismissButton = createElement();
    const overlay = createElement();
    let overlayRemoved = false;
    let chatSetupReadyListener = null;
    let handoffCount = 0;
    overlay.querySelector = (selector) => (
        selector === '[data-action="view"]' ? viewButton : dismissButton
    );
    overlay.remove = () => { overlayRemoved = true; };

    const windowObject = {
        ChatSanitizer: {
            sanitizePolicyNoticeHtml(value) { return value; },
        },
        authedFetch: async () => ({ ok: true }),
        chatSetup: {
            privacy_policy_notice: {
                revision: 4,
                notice_mode: 'modal',
                notice_message_html: '',
                should_show_notice: true,
            },
        },
        dispatchEvent(event) {
            assert.equal(event.type, 'privacyPolicyNoticeResolved');
            assert.equal(overlayRemoved, true);
            handoffCount += 1;
        },
        getTranslation(_key, fallback) { return fallback; },
        open() {},
    };
    const context = {
        console,
        CustomEvent: class CustomEvent {
            constructor(type, options = {}) {
                this.type = type;
                this.detail = options.detail;
            }
        },
        document: {
            addEventListener(type, listener) {
                if (type === 'chatSetupReady') chatSetupReadyListener = listener;
            },
            body: { appendChild() {} },
            createElement() { return overlay; },
        },
        window: windowObject,
    };
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(PRIVACY_NOTICE_PATH, 'utf8'), context, {
        filename: PRIVACY_NOTICE_PATH,
    });

    chatSetupReadyListener();
    await dismissButton.listeners.click();

    assert.equal(handoffCount, 1);
    assert.equal(windowObject.chatSetup.privacy_policy_notice.should_show_notice, false);
});


test('welcome experience is an accessible shared modal outside the chat container', () => {
    const index = fs.readFileSync(INDEX_PATH, 'utf8');
    const chatWelcomeStart = index.indexOf('<div class="chat-container-welcome"');
    const chatBoxStart = index.indexOf('<div class="chat-box-area"', chatWelcomeStart);
    const modalStart = index.indexOf('id="firstRunWelcomeOverlay"');
    const archivedModalStart = index.indexOf('id="archivedChatsOverlay"');
    const chatWelcomeMarkup = index.slice(chatWelcomeStart, chatBoxStart);

    assert.doesNotMatch(chatWelcomeMarkup, /firstRunWelcome(?:Overlay|Modal)/);
    assert.ok(modalStart > chatBoxStart);
    assert.ok(modalStart < archivedModalStart);
    assert.match(index, /class="first-run-welcome-overlay search-modal-overlay shared-modal-overlay"/);
    assert.match(index, /id="firstRunWelcomeModal" role="dialog" aria-modal="true"[^>]*tabindex="-1"/);
    assert.match(index, /aria-labelledby="firstRunWelcomeTitle" aria-describedby="firstRunWelcomeBody firstRunWelcomePrivacy"/);
});
