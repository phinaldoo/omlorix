// Cached controls that open/close the settings view or its sidebar
const sidebarProfileDropdown = document.getElementById('sidebarProfileDropdown');
const userSettingsHeaderCloseButton = document.getElementById('userSettingsHeaderCloseButton');
const userSettingsLogoutButton = document.getElementById('userSettingsLogoutButton');
const userSettingsNavToggle = document.getElementById('userSettingsNavToggle');
const userSettingsSidebar = document.getElementById('userSettingsSidebar');
const userSettingsSidebarBackdrop = document.getElementById('userSettingsSidebarBackdrop');
const userSettingsSidebarMobileClose = document.getElementById('userSettingsSidebarMobileClose');
const userSettingsDragHandle = document.getElementById('userSettingsDragHandle');

// Root containers and header copy that flip when the section changes
const chatView = document.getElementById('chatView');
const userSettingsView = document.getElementById('userSettingsView');
const userSettingsContainer = userSettingsView?.querySelector('.us-container');
const userSettingsHeaderTitle = document.querySelector('.us-settings-header h1');
const DEFAULT_USER_SETTINGS_SECTION = 'profile';
// Keep this query aligned with the drawer breakpoint in userSettings/style.css.
// Below this width the persistent desktop navigation becomes an overlay drawer.
const USER_SETTINGS_SIDEBAR_DRAWER_MEDIA_QUERY = '(max-width: 1024px)';
// Phones use a two-screen bottom sheet instead of the tablet drawer: the
// category list is the first screen and every settings page has a back button.
const USER_SETTINGS_MOBILE_SHEET_MEDIA_QUERY = '(max-width: 640px)';
const userSettingsSheetDrag = {
    active: false,
    currentY: 0,
    lastTime: 0,
    lastY: 0,
    moved: false,
    pointerId: null,
    skipClick: false,
    startY: 0,
    velocity: 0,
};
const dataControlElements = {
    navSection: document.getElementById('dataControlNavSection'),
    navItem: document.getElementById('dataControlNavItem'),
    page: document.getElementById('dataControlPage'),
};
const memoryElements = {
    navItem: document.getElementById('memoryNavItem'),
    page: document.getElementById('memorySettingsPage'),
};
const passkeyElements = {
    section: document.getElementById('passkeySection'),
};
const managedGroupsElements = {
    navItem: document.getElementById('managedGroupsNavItem'),
    page: document.getElementById('managedGroupsPage'),
};
const twoFactorElements = {
    section: document.getElementById('twoFactorSettingsSection'),
};
const temporaryChatPreferenceSetting = document
    .querySelector('input.toggle-input[data-setting-key="always_use_temporary_chat"]')
    ?.closest('.us-setting-item');


const usT = (key, fallback) => {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
};


/**
 * Convert the one self-service portability policy returned by the settings API
 * into a strict boolean before it is used to show the account archive page.
 *
 * @param {Record<string, unknown>} flags Raw data-control policy values.
 * @returns {Record<string, boolean>} Normalized data-control permissions.
 */
function normalizeDataControlFlags(flags = {}) {
    return {
        allow_user_data: Boolean(flags.allow_user_data),
    };
}

function applyDataControlVisibility(flags = {}) {
    const normalized = normalizeDataControlFlags(flags);
    let status = {
        anyEnabled: Object.values(normalized).some(Boolean),
        allEnabled: Object.values(normalized).every(Boolean),
    };

    if (typeof window !== 'undefined' && typeof window.updateDataControlAvailability === 'function') {
        try {
            status = window.updateDataControlAvailability(normalized) || status;
        } catch (error) {
            console.error('[userSettings] Failed to update data control availability', error);
        }
    }

    const shouldShowDataControls = status.anyEnabled;

    if (dataControlElements.navSection) {
        dataControlElements.navSection.style.display = shouldShowDataControls ? '' : 'none';
    }
    if (dataControlElements.navItem) {
        dataControlElements.navItem.style.display = shouldShowDataControls ? '' : 'none';
    }
    if (dataControlElements.page) {
        dataControlElements.page.style.display = shouldShowDataControls ? '' : 'none';
        if (!shouldShowDataControls && dataControlElements.page.classList.contains('active')) {
            setActiveSection(DEFAULT_USER_SETTINGS_SECTION);
        }
    }

    return status;
}

const userSettingHeaders = {
    profile: {
        title: () => usT('us_page_profile_title', 'Profile Settings'),
    },
    security: {
        title: () => usT('us_page_security_title', 'Security'),
    },
    appearance: {
        title: () => usT('us_page_appearance_title', 'Appearance'),
    },
    general: {
        title: () => usT('us_page_general_title', 'General Settings'),
    },
    chat: {
        title: () => usT('us_page_chat_title', 'Chat Settings'),
    },
    memory: {
        title: () => usT('us_page_memory_title', 'Memory'),
    },
    byok: {
        title: () => usT('us_page_byok_title', 'Bring Your Own Key'),
    },
    help: {
        title: () => usT('us_page_help_title', 'Help & Shortcuts'),
    },
    'data-control': {
        title: () => usT('us_page_data_control_title', 'Data Control'),
    },
    'rate-limits': {
        title: () => usT('us_page_rate_limits_title', 'Usage Limits'),
    },
    'shared-items': {
        title: () => usT('us_page_shared_items_title', 'Shared Items'),
    },
    'managed-groups': {
        title: () => usT('us_page_managed_groups_title', 'Managed Groups'),
    },
};

// Collections refreshed lazily so dynamically injected links also work
let navItems = Array.from(document.querySelectorAll('.us-nav-item'));
let pages = Array.from(document.querySelectorAll('.us-page'));
// Each settings open owns its loading state. Keeping only the active invocation
// here lets navigation clicks update the correct request and makes stale async
// continuations easy to identify without sharing selection state across opens.
let activeUserSettingsOpenInvocation = null;

/**
 * Make the existing settings navigation keyboard-operable without changing
 * its dynamically feature-gated DOM nodes.
 */
function initializeUserSettingsNavigationAccessibility() {
    navItems.forEach((item) => {
        item.setAttribute('role', 'button');
        item.tabIndex = 0;
        // Chevrons are present in the DOM for assistive-technology consistency,
        // but CSS only displays them in the phone category list.
        if (!item.querySelector('.us-nav-chevron')) {
            const chevron = document.createElement('span');
            chevron.className = 'us-nav-chevron';
            chevron.setAttribute('aria-hidden', 'true');
            chevron.innerHTML = window.Icons?.chevronRight || '';
            item.appendChild(chevron);
        }
        item.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                item.click();
                return;
            }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            const availableItems = navItems.filter((candidate) => (
                !candidate.hidden && candidate.style.display !== 'none'
            ));
            if (!availableItems.length) return;
            const currentIndex = Math.max(0, availableItems.indexOf(item));
            let nextIndex = currentIndex;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = availableItems.length - 1;
            if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % availableItems.length;
            if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + availableItems.length) % availableItems.length;
            event.preventDefault();
            availableItems[nextIndex].focus();
        });
    });
}


function handleNavItemClick(event) {
    const item = event.currentTarget;
    const section = item.dataset.section;
    event.preventDefault();
    if (activeUserSettingsOpenInvocation) {
        activeUserSettingsOpenInvocation.userSelectedSectionDuringSettingsLoad = true;
    }
    setActiveSection(section);
    if (isUserSettingsSidebarDrawer()) {
        closeUserSettingsSidebar();
        if (isUserSettingsMobileSheet()) {
            requestAnimationFrame(() => {
                if (!userSettingsHeaderTitle) return;
                userSettingsHeaderTitle.tabIndex = -1;
                userSettingsHeaderTitle.focus();
            });
        }
    }
}

function isPageAvailable(page) {
    return Boolean(page) && page.style.display !== 'none' && !page.hidden;
}

function setActiveSection(sectionKey, options = {}) {
    const hasMatchingPage = pages.some((page) => page.dataset.usPage === sectionKey && isPageAvailable(page));
    const activeSection = hasMatchingPage ? sectionKey : DEFAULT_USER_SETTINGS_SECTION;

    navItems.forEach((item) => {
        const isActive = item.dataset.section === activeSection;
        item.classList.toggle('active', isActive);
        if (isActive) item.setAttribute('aria-current', 'page');
        else item.removeAttribute('aria-current');
    });

    pages.forEach((page) => {
        const isActive = page.dataset.usPage === activeSection;
        page.classList.toggle('active', isActive);
    });

    const header = userSettingHeaders[activeSection];
    const title = typeof header.title === 'function' ? header.title() : header.title;
    userSettingsHeaderTitle.textContent = title;
    const targets = [userSettingsView, userSettingsView?.querySelector('.us-main-content')].filter(Boolean);
    targets.forEach((element) => {
        element.scrollTop = 0;
    });

    // Statistics are a live view of persisted BYOK activity. Refresh them
    // whenever this page is activated so returning to it never shows the
    // snapshot cached by an earlier visit.
    if (activeSection === 'byok' && options.refreshData !== false) {
        void window.BYOK?.refreshStatistics?.();
    }
}

function applyMemoryVisibility(enabled) {
    const visible = Boolean(enabled);
    if (memoryElements.navItem) {
        memoryElements.navItem.style.display = visible ? '' : 'none';
    }
    if (memoryElements.page) {
        memoryElements.page.style.display = visible ? '' : 'none';
        if (!visible && memoryElements.page.classList.contains('active')) {
            setActiveSection(DEFAULT_USER_SETTINGS_SECTION);
        }
    }
    if (typeof window.MemorySettingsPage?.setVisibility === 'function') {
        window.MemorySettingsPage.setVisibility(visible);
    }
}

function applyTemporaryChatPreferenceVisibility(allowed) {
    if (!temporaryChatPreferenceSetting) {
        return;
    }
    temporaryChatPreferenceSetting.style.display = allowed === false ? 'none' : '';
}

function applyPasskeyVisibility(enabled) {
    const visible = Boolean(enabled);
    if (typeof window !== 'undefined' && typeof window.setPasskeySectionEnabled === 'function') {
        window.setPasskeySectionEnabled(visible);
    } else if (passkeyElements.section) {
        passkeyElements.section.style.display = visible ? '' : 'none';
    }
    return visible;
}

function applyTwoFactorVisibility(enabled) {
    // 2FA is only relevant when the global policy turns it on.
    const visible = enabled !== false;
    if (twoFactorElements.section) {
        twoFactorElements.section.hidden = !visible;
    }
    return visible;
}

function applyTwoFactorSettingsState(data = {}) {
    const visible = applyTwoFactorVisibility(data?.two_factor_authentication_enabled);
    if (typeof window.setTwoFactorSettingsState === 'function') {
        window.setTwoFactorSettingsState({
            featureEnabled: visible,
            enrolled: data?.two_factor_authentication_setup,
            forced: data?.two_factor_authentication_forced,
        });
    }
    return visible;
}

function applyExternalAccountVisibility(data = {}) {
    const externallyManaged = data?.externally_managed === true;
    const notice = document.getElementById('externallyManagedAccountNotice');
    const personalInformation = document.getElementById('personalInformationSettingsSection');
    const signInMethods = document.getElementById('signInMethodsSettingsSection');

    if (notice) notice.hidden = !externallyManaged;
    if (personalInformation) personalInformation.hidden = externallyManaged;
    if (signInMethods) signInMethods.hidden = externallyManaged;
    return externallyManaged;
}

function applyManagedGroupsVisibility(enabled) {
    const visible = Boolean(enabled);
    if (managedGroupsElements.navItem) {
        managedGroupsElements.navItem.style.display = visible ? '' : 'none';
    }
    if (managedGroupsElements.page) {
        managedGroupsElements.page.style.display = visible ? '' : 'none';
        if (!visible && managedGroupsElements.page.classList.contains('active')) {
            setActiveSection(DEFAULT_USER_SETTINGS_SECTION);
        }
    }
    if (typeof window.ManagedGroupsSettings?.setVisibility === 'function') {
        window.ManagedGroupsSettings.setVisibility(visible);
    }
}

/**
 * Apply the conditional navigation flags supplied by the initial chat setup.
 * Detailed settings requests may repeat these values later, but the first
 * paint of the settings sidebar should already have its final structure.
 */
function applyUserSettingsNavigationAvailability(setup = {}) {
    // Memory access is already part of the chat bootstrap for workspace
    // initialization, so reuse it here instead of waiting for settings init.
    if (Object.prototype.hasOwnProperty.call(setup, 'enable_memories')) {
        applyMemoryVisibility(setup.enable_memories);
    }

    const availability = setup?.user_settings_navigation;
    if (!availability || typeof availability !== 'object') {
        return;
    }

    if (Object.prototype.hasOwnProperty.call(availability, 'managed_groups')) {
        applyManagedGroupsVisibility(availability.managed_groups);
    }
    if (
        Object.prototype.hasOwnProperty.call(availability, 'rate_limits')
        && typeof window.setRateLimitsVisibility === 'function'
    ) {
        window.setRateLimitsVisibility(Boolean(availability.rate_limits));
    }
}

// The setup request usually finishes after this deferred script executes, but
// also handle cached/very fast responses that completed before registration.
applyUserSettingsNavigationAvailability(window.chatSetup || {});
document.addEventListener('chatSetupReady', (event) => {
    applyUserSettingsNavigationAvailability(event?.detail || {});
});

let isClosingUserSettingsView = false;
let userSettingsClosingTimeoutId;
let userSettingsScrollResetFrameId;
let closeAnimationHandler;
let userSettingsCloseCallbacks = [];
let userSettingsReturnFocus = null;
let chatViewWasInert = false;
let userSettingsBodyHadModalOpen = false;

const isUserSettingsViewVisible = () => Boolean(userSettingsView && !userSettingsView.hidden);

const stopClosingTimer = () => {
    if (!userSettingsClosingTimeoutId) {
        return;
    }

    clearTimeout(userSettingsClosingTimeoutId);
    userSettingsClosingTimeoutId = undefined;
};

const cancelClosingAnimation = () => {
    if (!userSettingsContainer) {
        return;
    }

    userSettingsView?.classList.remove('is-closing');

    if (closeAnimationHandler) {
        userSettingsContainer.removeEventListener('animationend', closeAnimationHandler);
        closeAnimationHandler = undefined;
    }
};

const resetUserSettingsScrollState = () => {
    const scrollContainers = [
        userSettingsSidebar,
        userSettingsSidebar?.querySelector('nav'),
        userSettingsView,
        userSettingsView?.querySelector('.us-main-content'),
    ].filter(Boolean);

    scrollContainers.forEach((element) => {
        element.scrollTop = 0;
        element.scrollLeft = 0;
    });
};

const scheduleUserSettingsScrollReset = () => {
    if (userSettingsScrollResetFrameId) {
        cancelAnimationFrame(userSettingsScrollResetFrameId);
    }

    userSettingsScrollResetFrameId = requestAnimationFrame(() => {
        resetUserSettingsScrollState();
        userSettingsScrollResetFrameId = undefined;
    });
};

const releaseUserSettingsFocus = () => {
    const activeElement = document.activeElement;
    if (activeElement instanceof HTMLElement && userSettingsView?.contains(activeElement)) {
        activeElement.blur();
    }
};

/**
 * Return whether the settings navigation is currently rendered as a drawer.
 *
 * `matchMedia` follows the same CSS media-query semantics as the layout. The
 * inner-width fallback keeps the behavior working in older embedded browsers.
 *
 * @returns {boolean} Whether the settings sidebar overlays the page content.
 */
function isUserSettingsSidebarDrawer() {
    if (typeof window.matchMedia === 'function') {
        return window.matchMedia(USER_SETTINGS_SIDEBAR_DRAWER_MEDIA_QUERY).matches;
    }

    return window.innerWidth <= 1024;
}

/**
 * Return whether settings uses the phone-specific bottom-sheet navigation.
 *
 * @returns {boolean} Whether navigation and detail are separate sheet screens.
 */
function isUserSettingsMobileSheet() {
    if (typeof window.matchMedia === 'function') {
        return window.matchMedia(USER_SETTINGS_MOBILE_SHEET_MEDIA_QUERY).matches;
    }

    return window.innerWidth <= 640;
}

function resetUserSettingsSheetDrag() {
    userSettingsSheetDrag.active = false;
    userSettingsSheetDrag.currentY = 0;
    userSettingsSheetDrag.lastTime = 0;
    userSettingsSheetDrag.lastY = 0;
    userSettingsSheetDrag.moved = false;
    userSettingsSheetDrag.pointerId = null;
    userSettingsSheetDrag.skipClick = false;
    userSettingsSheetDrag.startY = 0;
    userSettingsSheetDrag.velocity = 0;
    userSettingsContainer?.classList.remove('us-mobile-sheet-dragging');
    if (userSettingsContainer) {
        userSettingsContainer.style.animation = '';
        userSettingsContainer.style.transform = '';
        userSettingsContainer.style.transition = '';
    }
}

function beginUserSettingsSheetDrag(clientY, pointerId) {
    if (!isUserSettingsMobileSheet() || !isUserSettingsViewVisible() || isClosingUserSettingsView) return;
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    userSettingsSheetDrag.active = true;
    userSettingsSheetDrag.currentY = clientY;
    userSettingsSheetDrag.lastTime = now;
    userSettingsSheetDrag.lastY = clientY;
    userSettingsSheetDrag.moved = false;
    userSettingsSheetDrag.pointerId = pointerId;
    userSettingsSheetDrag.startY = clientY;
    userSettingsSheetDrag.velocity = 0;
    userSettingsContainer?.classList.add('us-mobile-sheet-dragging');
    if (userSettingsContainer) userSettingsContainer.style.animation = 'none';
}

function updateUserSettingsSheetDrag(clientY) {
    if (!userSettingsSheetDrag.active || !userSettingsContainer) return;
    const deltaY = Math.max(0, clientY - userSettingsSheetDrag.startY);
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    const elapsed = now - userSettingsSheetDrag.lastTime;
    if (elapsed > 0) {
        const velocity = (clientY - userSettingsSheetDrag.lastY) / elapsed;
        userSettingsSheetDrag.velocity = velocity > 0 ? velocity : 0;
    }
    userSettingsSheetDrag.currentY = clientY;
    userSettingsSheetDrag.lastTime = now;
    userSettingsSheetDrag.lastY = clientY;
    userSettingsSheetDrag.moved ||= deltaY > 6;
    userSettingsContainer.style.transform = deltaY > 0 ? `translateY(${deltaY}px)` : '';
}

function finishUserSettingsSheetDrag() {
    if (!userSettingsSheetDrag.active || !userSettingsContainer) return;
    const deltaY = Math.max(0, userSettingsSheetDrag.currentY - userSettingsSheetDrag.startY);
    const sheetHeight = userSettingsContainer.getBoundingClientRect().height;
    const closeThreshold = Math.min(100, Math.max(48, sheetHeight * 0.18));
    const shouldClose = deltaY >= closeThreshold
        || (userSettingsSheetDrag.velocity > 0.65 && deltaY > 10);
    const shouldSkipClick = userSettingsSheetDrag.moved;
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

    userSettingsSheetDrag.active = false;
    userSettingsSheetDrag.pointerId = null;
    userSettingsContainer.classList.remove('us-mobile-sheet-dragging');
    userSettingsContainer.style.transition = reduceMotion
        ? 'none'
        : 'transform 0.22s cubic-bezier(0.16, 1, 0.3, 1)';

    if (shouldClose) {
        if (reduceMotion) {
            closeUserSettings({ immediate: true });
            return;
        }
        userSettingsSheetDrag.skipClick = true;
        userSettingsContainer.style.transform = 'translateY(100%)';
        let didClose = false;
        const finishTransition = (event) => {
            if (event.target !== userSettingsContainer || event.propertyName !== 'transform') return;
            finishClose();
        };
        const finishClose = () => {
            if (didClose) return;
            didClose = true;
            userSettingsContainer.removeEventListener('transitionend', finishTransition);
            closeUserSettings({ immediate: true });
        };
        userSettingsContainer.addEventListener('transitionend', finishTransition);
        setTimeout(finishClose, 250);
        return;
    }

    userSettingsSheetDrag.skipClick = shouldSkipClick;
    userSettingsContainer.style.transform = '';
}

function findUserSettingsTouch(touchList) {
    if (!touchList) return null;
    return Array.from(touchList).find((touch) => touch.identifier === userSettingsSheetDrag.pointerId)
        || touchList[0]
        || null;
}

/** Keep the navigation button and mobile sheet state synchronized. */
function updateUserSettingsNavigationState(isOpen) {
    const mobileSheetIsOpen = isOpen && isUserSettingsMobileSheet();
    userSettingsContainer?.classList.toggle('us-mobile-navigation-open', mobileSheetIsOpen);
    userSettingsNavToggle?.setAttribute('aria-expanded', String(isOpen));

    if (userSettingsNavToggle) {
        userSettingsNavToggle.hidden = !isUserSettingsSidebarDrawer();
        userSettingsNavToggle.setAttribute(
            'aria-label',
            isUserSettingsMobileSheet()
                ? usT('us_btn_back', 'Back')
                : usT('us_nav_toggle', 'Open navigation'),
        );
    }
}

/**
 * Put the settings navigation in the correct state whenever the view opens.
 * Phones start with the category list in front of them. Tablet drawers start
 * closed, while desktop layouts keep the sidebar permanently visible.
 */
function showUserSettingsNavigationOnOpen() {
    if (isUserSettingsMobileSheet()) {
        openUserSettingsSidebar();
        return;
    }

    // Clear drawer state left behind by a viewport change. On tablets this
    // opens the content page; on desktop the regular sidebar remains visible.
    closeUserSettingsSidebar();
}

const resetUserSettingsClosedState = () => {
    releaseUserSettingsFocus();
    resetUserSettingsScrollState();
    userSettingsSidebar?.classList.remove('active');
    userSettingsSidebarBackdrop?.classList.remove('active');
    userSettingsContainer?.classList.remove('us-mobile-navigation-open');
    document.body.classList.remove('us-sidebar-open');
    userSettingsNavToggle?.setAttribute('aria-expanded', 'false');
};

const finalizeUserSettingsClose = () => {
    const returnFocus = userSettingsReturnFocus;
    userSettingsReturnFocus = null;
    cancelClosingAnimation();
    stopClosingTimer();
    isClosingUserSettingsView = false;
    resetUserSettingsSheetDrag();
    userSettingsView.hidden = true;
    userSettingsView.setAttribute('aria-hidden', 'true');
    if (!userSettingsBodyHadModalOpen) document.body.classList.remove('modal-open');
    userSettingsBodyHadModalOpen = false;
    chatView.inert = chatViewWasInert;
    resetUserSettingsClosedState();
    const safeReturnFocus = returnFocus?.closest?.('#sidebarProfileDropdown')
        ? document.querySelector('.sidebar-profile-button')
        : returnFocus;
    if (safeReturnFocus?.isConnected && typeof safeReturnFocus.focus === 'function') {
        safeReturnFocus.focus();
    }
    const callbacks = userSettingsCloseCallbacks;
    userSettingsCloseCallbacks = [];
    callbacks.forEach((callback) => {
        try {
            callback();
        } catch (error) {
            console.error('[userSettings] close callback failed', error);
        }
    });
};

function showUserSettingsView() {
    const wasVisible = isUserSettingsViewVisible();
    stopClosingTimer();
    isClosingUserSettingsView = false;
    cancelClosingAnimation();
    resetUserSettingsScrollState();
    if (!wasVisible) {
        chatViewWasInert = chatView.inert;
        userSettingsBodyHadModalOpen = document.body.classList.contains('modal-open');
    }
    chatView.inert = true;
    resetUserSettingsSheetDrag();
    userSettingsView.hidden = false;
    userSettingsView.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    showUserSettingsNavigationOnOpen();
    scheduleUserSettingsScrollReset();
    sidebarProfileDropdown?.classList.remove('open');
}

function closeChatPreviewPanelsForUserSettings() {
    [
        window.slidePresentationWidget,
        window.canvasMarkdownWidget,
        window.deepResearchWidget,
        window.latexPdfWidget,
        window.NotesToolSidebar,
    ].forEach((widget) => {
        if (widget && typeof widget.hidePreviewPanel === 'function') {
            try {
                widget.hidePreviewPanel();
            } catch (_) {}
        }
    });
}

function hideUserSettingsView(options = {}) {
    if (isClosingUserSettingsView) {
        return;
    }

    resetUserSettingsSheetDrag();

    const shouldCloseImmediately = options.immediate === true;
    const shouldSkipAnimation = !userSettingsContainer
        || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

    if (shouldSkipAnimation || shouldCloseImmediately) {
        finalizeUserSettingsClose();
        return;
    }

    isClosingUserSettingsView = true;
    userSettingsView.classList.add('is-closing');

    closeAnimationHandler = (event) => {
        if (event.target === userSettingsContainer) {
            finalizeUserSettingsClose();
        }
    };

    userSettingsContainer.addEventListener('animationend', closeAnimationHandler);
    userSettingsClosingTimeoutId = setTimeout(finalizeUserSettingsClose, 400);
}

async function openUserSettings(initialSection = DEFAULT_USER_SETTINGS_SECTION) {
    if (!isUserSettingsViewVisible()) {
        userSettingsReturnFocus = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
    }
    const invocation = {
        userSelectedSectionDuringSettingsLoad: false,
    };
    activeUserSettingsOpenInvocation = invocation;
    navItems.forEach((item) => item.addEventListener('click', handleNavItemClick));

    closeChatPreviewPanelsForUserSettings();
    showUserSettingsView();
    // The page is activated again after its settings payload is applied. Delay
    // data-backed section loading until then to avoid duplicate requests.
    setActiveSection(initialSection, { refreshData: false });
    requestAnimationFrame(() => {
        const focusTarget = isUserSettingsMobileSheet()
            ? navItems.find((item) => item.classList.contains('active') && item.style.display !== 'none')
            : userSettingsHeaderTitle;
        if (focusTarget) {
            focusTarget.tabIndex = focusTarget === userSettingsHeaderTitle ? -1 : focusTarget.tabIndex;
            focusTarget.focus();
        }
    });

    const data = await fetchUserSettingsInit();
    if (activeUserSettingsOpenInvocation !== invocation) {
        return;
    }
    if (!data) {
        notifyError(usT('us_settings_fetch_data_failed', 'Failed to fetch user settings data!'));
        closeUserSettings();
        return;
    }
    applyDataControlVisibility(data?.data_controls || {});
    const externallyManaged = applyExternalAccountVisibility(data);
    applyTemporaryChatPreferenceVisibility(data?.temporary_chat_allowed);
    initUserSettingsToogle(data);
    initUserSettingsSelect(data);
    if (typeof window.initTwoFASetupProvider === 'function') {
        window.initTwoFASetupProvider(data?.twofa_provider || '');
    }
    applyTwoFactorSettingsState(data);
    if (typeof window.initUserSettingsSpeech === 'function') {
        window.initUserSettingsSpeech(data);
    }
    if (typeof window.initLLMAccessControls === 'function') {
        window.initLLMAccessControls({
            permissions: data?.allow_llm_to_access_personal_information,
            preset: data?.allow_llm_to_access_personal_information_preset,
        });
    }
    if (typeof window.initUserPersonalitySettings === 'function') {
        window.initUserPersonalitySettings(data);
    }
    if (typeof window.BYOK?.setPolicy === 'function') {
        window.BYOK.setPolicy(data);
    }
    if (typeof window.MCPSettings?.setPolicy === 'function') {
        window.MCPSettings.setPolicy(data);
    }
    applyMemoryVisibility(window.enableMemoriesFeature === true);
    if (typeof window.MemorySettingsPage?.load === 'function' && window.enableMemoriesFeature === true) {
        window.MemorySettingsPage.load();
    }
    loadActiveSessions();
    const passkeysEnabled = applyPasskeyVisibility(data?.enable_passkeys);
    if (passkeysEnabled && typeof window.loadPasskeys === 'function') {
        window.loadPasskeys();
    }
    loadUserInfo(data);
    initUserLocation(data?.location);
    initChangePasswordSection(data.allow_change_password, data.needs_password_setup);
    bindChangePasswordEventListener();
    if (!externallyManaged && typeof window.loadSignInMethods === 'function') {
        window.loadSignInMethods();
    }
    initDeleteAccountSection(data.allow_self_deletion, data.user_deletion_policy);
    bindDeleteAccountEventListener();

    // Rate limits — load and conditionally show
    if (typeof window.initRateLimits === 'function') {
        window.initRateLimits();
    }

    // Shared items — load lazily when section is activated
    if (typeof window.SharedItemsSettings?.load === 'function') {
        window.SharedItemsSettings.load();
    }
    applyManagedGroupsVisibility(data?.managed_groups_available);
    if (data?.managed_groups_available && typeof window.ManagedGroupsSettings?.load === 'function') {
        window.ManagedGroupsSettings.load();
    }

    // Initialize sidebar button visibility settings
    if (typeof window.initializeSidebarButtonSettings === 'function') {
        await window.initializeSidebarButtonSettings(data);
    }
    if (activeUserSettingsOpenInvocation !== invocation) {
        return;
    }
    if (typeof window.initializeSidebarButtonEventListeners === 'function') {
        window.initializeSidebarButtonEventListeners();
    }

    // A mobile user may already have selected a category from the drawer while
    // the initialization request was pending. Preserve that explicit choice.
    if (
        activeUserSettingsOpenInvocation === invocation
        && !invocation.userSelectedSectionDuringSettingsLoad
    ) {
        setActiveSection(initialSection);
    }
    if (activeUserSettingsOpenInvocation === invocation) {
        activeUserSettingsOpenInvocation = null;
    }
}

function closeUserSettings(options = {}) {
    // Invalidate any pending settings initialization so it cannot update the
    // closed view or overwrite the section selected by a later open.
    activeUserSettingsOpenInvocation = null;
    if (typeof options.onClosed === 'function') {
        userSettingsCloseCallbacks.push(options.onClosed);
    }
    navItems.forEach((item) => item.removeEventListener('click', handleNavItemClick));
    hideUserSettingsView(options);
    removeChangePasswordEventListener();
    removeDeleteAccountEventListener();
    if (typeof window.teardownRateLimits === 'function') {
        window.teardownRateLimits();
    }
}

if (typeof window !== 'undefined') {
    window.openUserSettings = openUserSettings;
    window.closeUserSettings = closeUserSettings;
    window.setUserSettingsActiveSection = setActiveSection;
}

if (typeof window !== 'undefined' && window.registerEscapeHandler) {
    window.registerEscapeHandler({
        id: 'user-settings-view',
        priority: 120,
        isActive: () => isUserSettingsViewVisible(),
        close: () => {
            if (isUserSettingsViewVisible()) {
                closeUserSettings();
            }
        }
    });
}

async function fetchUserSettingsInit() {
    try {
        return typeof window.getCachedUserSettingsInit === 'function'
            ? await window.getCachedUserSettingsInit()
            : await (async () => {
                const res = await window.authedFetch('/api/v1/users/user-settings/init', {
                    method: 'GET',
                });

                if (!res.ok) {
                    notifyError(usT('us_settings_fetch_init_failed', 'Failed to fetch user settings init data'));
                    return null;
                }

                return res.json();
            })();
    } catch (error) {
        console.error('Failed to fetch user settings init data', error);
        notifyError(usT('us_settings_fetch_init_failed', 'Failed to fetch user settings init data'));
        return null;
    }
}

// The profile dropdown is rendered and delegated by sidebar.js. Keeping its
// open action there lets permission and locale-driven menu renders stay atomic.
userSettingsHeaderCloseButton.addEventListener('click', () => closeUserSettings());

if (userSettingsDragHandle) {
    userSettingsDragHandle.addEventListener('click', (event) => {
        if (!isUserSettingsMobileSheet()) return;
        event.preventDefault();
        if (userSettingsSheetDrag.skipClick) {
            userSettingsSheetDrag.skipClick = false;
            return;
        }
        closeUserSettings();
    });
    userSettingsDragHandle.addEventListener('keydown', (event) => {
        if (!isUserSettingsMobileSheet() || !['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        closeUserSettings();
    });

    if (typeof window.PointerEvent === 'function') {
        userSettingsDragHandle.addEventListener('pointerdown', (event) => {
            if (event.pointerType === 'mouse' && event.button !== 0) return;
            userSettingsDragHandle.setPointerCapture?.(event.pointerId);
            beginUserSettingsSheetDrag(event.clientY, event.pointerId);
        });
        userSettingsDragHandle.addEventListener('pointermove', (event) => {
            if (!userSettingsSheetDrag.active || event.pointerId !== userSettingsSheetDrag.pointerId) return;
            event.preventDefault();
            updateUserSettingsSheetDrag(event.clientY);
        });
        const finishPointerDrag = (event) => {
            if (!userSettingsSheetDrag.active || event.pointerId !== userSettingsSheetDrag.pointerId) return;
            userSettingsSheetDrag.currentY = event.clientY;
            userSettingsDragHandle.releasePointerCapture?.(event.pointerId);
            finishUserSettingsSheetDrag();
        };
        userSettingsDragHandle.addEventListener('pointerup', finishPointerDrag);
        userSettingsDragHandle.addEventListener('pointercancel', finishPointerDrag);
    } else {
        userSettingsDragHandle.addEventListener('touchstart', (event) => {
            const touch = event.touches?.[0];
            if (!touch) return;
            beginUserSettingsSheetDrag(touch.clientY, touch.identifier);
        }, { passive: true });
        userSettingsDragHandle.addEventListener('touchmove', (event) => {
            if (!userSettingsSheetDrag.active) return;
            const touch = findUserSettingsTouch(event.touches);
            if (!touch) return;
            event.preventDefault();
            updateUserSettingsSheetDrag(touch.clientY);
        }, { passive: false });
        userSettingsDragHandle.addEventListener('touchend', (event) => {
            if (!userSettingsSheetDrag.active) return;
            const touch = findUserSettingsTouch(event.changedTouches);
            if (touch) userSettingsSheetDrag.currentY = touch.clientY;
            finishUserSettingsSheetDrag();
        }, { passive: true });
        userSettingsDragHandle.addEventListener('touchcancel', finishUserSettingsSheetDrag, { passive: true });
    }
}

// Backdrop dismissal and a local focus loop give this large settings surface
// the same mouse and keyboard behavior as the app's smaller modal dialogs.
userSettingsView.addEventListener('click', (event) => {
    if (event.target === userSettingsView) {
        closeUserSettings();
    }
});

userSettingsView.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(userSettingsContainer.querySelectorAll(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !element.hidden && element.offsetParent !== null);
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

// Mobile sidebar close button (visible on small screens with full-width sidebar)
if (userSettingsSidebarMobileClose) {
    userSettingsSidebarMobileClose.addEventListener('click', () => closeUserSettingsSidebar());
}

userSettingsLogoutButton.addEventListener('click', () => {
    if (typeof logout === 'function') {
        logout();
    }
});

// Mobile sidebar toggle functionality
function openUserSettingsSidebar() {
    if (!userSettingsSidebar || !userSettingsSidebarBackdrop) return;
    
    userSettingsSidebar.classList.add('active');
    userSettingsSidebarBackdrop.classList.add('active');
    document.body.classList.add('us-sidebar-open');
    updateUserSettingsNavigationState(true);
}

function closeUserSettingsSidebar() {
    if (!userSettingsSidebar || !userSettingsSidebarBackdrop) return;
    
    userSettingsSidebar.classList.remove('active');
    userSettingsSidebarBackdrop.classList.remove('active');
    document.body.classList.remove('us-sidebar-open');
    updateUserSettingsNavigationState(false);
}

// Toggle sidebar on hamburger button click
if (userSettingsNavToggle) {
    userSettingsNavToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (userSettingsSidebar?.classList.contains('active')) {
            closeUserSettingsSidebar();
        } else {
            openUserSettingsSidebar();
        }
    });
}

// Close sidebar when clicking the backdrop
if (userSettingsSidebarBackdrop) {
    userSettingsSidebarBackdrop.addEventListener('click', () => {
        closeUserSettingsSidebar();
    });
}

// Add keyboard behavior and phone-only disclosure chevrons to navigation rows.
initializeUserSettingsNavigationAccessibility();

// Close sidebar on window resize if moving out of the drawer layout
window.addEventListener('resize', () => {
    if (!isUserSettingsSidebarDrawer()) {
        closeUserSettingsSidebar();
    } else {
        updateUserSettingsNavigationState(Boolean(userSettingsSidebar?.classList.contains('active')));
    }
});

// Reuse the centralized icon catalogue instead of duplicating SVG markup.
const userSettingsBackIcon = document.querySelector('.us-settings-nav-back-icon');
if (userSettingsBackIcon) {
    userSettingsBackIcon.innerHTML = window.Icons?.chevronLeft || '';
}
