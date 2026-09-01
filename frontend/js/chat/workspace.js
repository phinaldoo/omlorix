/**
 * Workspace Management Module
 * Handles workspace container tabs and section switching
 */

// ============================================================================
// State Management
// ============================================================================
const DEFAULT_WORKSPACE_TAB = 'notifications';
const PROMPT_LIBRARY_PAGE_LIMIT = 200;
const PROMPT_SHARE_PENDING_STORAGE_KEY = 'omlorix_pending_prompt_share';
const PROMPT_SHARE_TYPES = new Set(['clone', 'live', 'collaborate']);

function normalizePromptShareIntent(value) {
    const shareId = String(value?.shareId || '').trim();
    const shareType = String(value?.shareType || '').trim().toLowerCase();
    if (
        !shareId
        || shareId.length > 512
        || shareId.includes('/')
        || shareId.includes('\\')
        || !PROMPT_SHARE_TYPES.has(shareType)
    ) return null;
    return { shareId, shareType };
}

function getPromptShareAcceptanceEndpoint(shareType, shareId) {
    const normalizedType = String(shareType || '').trim().toLowerCase();
    const normalizedId = String(shareId || '').trim();
    if (!PROMPT_SHARE_TYPES.has(normalizedType) || !normalizedId) return null;
    const encodedId = encodeURIComponent(normalizedId);
    return normalizedType === 'clone'
        ? `/api/v1/prompts/clone/${encodedId}`
        : `/api/v1/prompts/shared/${encodedId}/accept`;
}

/** Return whether logical horizontal navigation currently runs right-to-left. */
function rootDirectionIsRtl() {
    const declaredDirection = document.documentElement.getAttribute('dir');
    if (declaredDirection) return declaredDirection.toLowerCase() === 'rtl';
    return window.getComputedStyle(document.documentElement).direction === 'rtl';
}

function unwrapPromptLibraryPage(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
}

const WorkspaceState = {
    activeTab: DEFAULT_WORKSPACE_TAB,
    initialized: false,
    pendingMemoryScope: null,
    mobileMenuReturnFocus: null,
};

// ============================================================================
// DOM Helpers
// ============================================================================

const WorkspaceDOM = {
    get container() { return document.getElementById('workspaceContainer'); },
    get headerTabs() { return document.getElementById('mainHeaderWorkspace'); },
    get headerTabsMobile() { return document.getElementById('mainHeaderWorkspaceMobile'); },
    get tabs() { return document.querySelectorAll('[data-workspace-tab]'); },
    get sections() { return document.querySelectorAll('.workspace-section'); },
    get mobileTrigger() { return document.getElementById('workspaceMobileTrigger'); },
    get mobileDropdown() { return document.getElementById('workspaceMobileDropdown'); },
    get mobileOptions() { return document.querySelectorAll('.workspace-header-mobile-option'); },
    getSectionById(tabId) {
        const sectionId = `workspaceSection${tabId.charAt(0).toUpperCase() + tabId.slice(1)}`;
        return document.getElementById(sectionId);
    },
    get mainContainerHeaderFilesUpload() { return document.getElementById('mainContainerHeaderFilesUpload'); },
    get filesCategoryFilterButton() { return document.getElementById('filesCategoryFilterButton'); },
};

// ============================================================================
// Workspace Manager
// ============================================================================

const WorkspaceManager = {
    isTabAllowed(tabId) {
        if (tabId === 'agents' && typeof window !== 'undefined' && window.enableAgentsFeature === false) {
            return false;
        }
        if (tabId === 'bookmarks' && typeof window !== 'undefined' && window.enableBookmarksFeature === false) {
            return false;
        }
        if (tabId === 'connections') {
            if (typeof window === 'undefined') return true;
            if (window.enableConnectionsFeature === false) return false;
            if (window.connectionsAllowed === false) return false;
        }
        return true;
    },

    init() {
        if (WorkspaceState.initialized) return;

        this.setupTabListeners();
        this.setupHeaderTabAccessibility();
        this.setupWorkspaceNavigationKeyboard();
        this.setupMobileDropdown();
        this.updateAllowedTabs();
        document.addEventListener('i18n:updated', () => {
            this.updateMobileDropdownState(WorkspaceState.activeTab);
        });
        document.addEventListener('chatSetupReady', () => {
            this.updateAllowedTabs();
            this.initializeActiveFilesAfterSetup();
        });
        WorkspaceState.initialized = true;
    },

    /**
     * Retry the files initializer after authentication and chat setup reveal
     * the app shell. A direct /workspace/files route is selected before that
     * reveal, so its first visibility-gated initialization intentionally exits.
     */
    initializeActiveFilesAfterSetup() {
        if (WorkspaceState.activeTab !== 'files' || !this.isVisible()) {
            return;
        }

        requestAnimationFrame(() => {
            if (
                WorkspaceState.activeTab !== 'files'
                || !this.isVisible()
                || typeof FilesManager === 'undefined'
            ) {
                return;
            }

            const initialization = FilesManager.initialize();
            initialization?.catch?.((error) => {
                console.warn('[workspace] Failed to initialize files after app setup', error);
            });
        });
    },

    setupTabListeners() {
        const tabs = WorkspaceDOM.tabs;
        if (!tabs || tabs.length === 0) return;

        tabs.forEach(tab => {
            if (tab.dataset.workspaceNavBound === 'true') return;
            tab.addEventListener('click', (e) => {
                e.preventDefault();
                const tabId = tab.dataset.workspaceTab;
                if (tabId) {
                    this.switchToTab(tabId);
                }
            });
            tab.dataset.workspaceNavBound = 'true';
        });
    },

    /**
     * Apply the WAI-ARIA tab pattern to the desktop Workspace header and keep
     * keyboard navigation scoped to tabs that the current user can access.
     */
    setupHeaderTabAccessibility() {
        const header = WorkspaceDOM.headerTabs;
        if (!header || header.dataset.workspaceTabA11yBound === 'true') return;

        header.setAttribute('role', 'tablist');
        const headerTabs = Array.from(header.querySelectorAll('.workspace-header-tab[data-workspace-tab]'));
        headerTabs.forEach((tab) => {
            const tabId = tab.dataset.workspaceTab;
            const section = WorkspaceDOM.getSectionById(tabId);
            tab.type = 'button';
            tab.id = `workspaceTab${tabId.charAt(0).toUpperCase()}${tabId.slice(1)}`;
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', tabId === WorkspaceState.activeTab ? 'true' : 'false');
            tab.tabIndex = tabId === WorkspaceState.activeTab ? 0 : -1;
            if (section) {
                tab.setAttribute('aria-controls', section.id);
                section.setAttribute('role', 'tabpanel');
                section.setAttribute('aria-labelledby', tab.id);
                section.tabIndex = 0;
            }
        });

        header.addEventListener('keydown', (event) => {
            const currentTab = event.target.closest('.workspace-header-tab[role="tab"]');
            if (!currentTab || !header.contains(currentTab)) return;
            const availableTabs = headerTabs.filter((tab) => (
                !tab.hidden
                && tab.style.display !== 'none'
                && this.isTabAllowed(tab.dataset.workspaceTab)
            ));
            if (!availableTabs.length) return;

            const currentIndex = Math.max(0, availableTabs.indexOf(currentTab));
            const isRtl = rootDirectionIsRtl();
            let nextIndex = null;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = availableTabs.length - 1;
            if (event.key === 'ArrowRight') nextIndex = currentIndex + (isRtl ? -1 : 1);
            if (event.key === 'ArrowLeft') nextIndex = currentIndex + (isRtl ? 1 : -1);
            if (nextIndex == null) return;

            event.preventDefault();
            nextIndex = (nextIndex + availableTabs.length) % availableTabs.length;
            const nextTab = availableTabs[nextIndex];
            this.switchToTab(nextTab.dataset.workspaceTab);
            nextTab.focus();
        });
        header.dataset.workspaceTabA11yBound = 'true';
    },

    /** Add vertical arrow-key navigation to the compact title menu. */
    setupWorkspaceNavigationKeyboard() {
        const configurations = [
            {
                host: WorkspaceDOM.mobileDropdown,
                selector: '.workspace-header-mobile-option:not([hidden])',
                previousKeys: ['ArrowUp'],
                nextKeys: ['ArrowDown'],
            },
        ];

        configurations.forEach((configuration) => {
            const { host, selector } = configuration;
            if (!host || host.dataset.workspaceArrowNavBound === 'true') return;
            host.addEventListener('keydown', (event) => {
                const current = event.target.closest(selector);
                if (!current || !host.contains(current)) return;
                const items = Array.from(host.querySelectorAll(selector)).filter((item) => (
                    !item.hidden && item.style.display !== 'none'
                ));
                if (!items.length) return;
                const currentIndex = Math.max(0, items.indexOf(current));
                let nextIndex = null;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = items.length - 1;
                if (configuration.previousKeys.includes(event.key)) nextIndex = currentIndex - 1;
                if (configuration.nextKeys.includes(event.key)) nextIndex = currentIndex + 1;
                if (nextIndex == null) return;
                event.preventDefault();
                nextIndex = (nextIndex + items.length) % items.length;
                items[nextIndex].focus();
            });
            host.dataset.workspaceArrowNavBound = 'true';
        });
    },

    /**
     * Build the compact title menu from the translated desktop tab labels.
     * Cloning the hardcoded data-i18n attributes keeps one canonical list of
     * destinations in markup while preserving runtime locale changes.
     */
    ensureMobileDropdownOptions() {
        const dropdown = WorkspaceDOM.mobileDropdown;
        const headerTabs = WorkspaceDOM.headerTabs;
        if (!dropdown || !headerTabs || dropdown.children.length > 0) return;

        headerTabs.querySelectorAll('.workspace-header-tab[data-workspace-tab]').forEach((tab) => {
            const label = tab.querySelector('[data-i18n]');
            if (!label) return;

            const option = document.createElement('button');
            option.type = 'button';
            option.className = 'workspace-header-mobile-option';
            option.dataset.workspaceTab = tab.dataset.workspaceTab;
            option.setAttribute('role', 'menuitemradio');
            option.setAttribute('aria-checked', 'false');
            if (tab.style.display === 'none') {
                option.style.display = 'none';
                option.setAttribute('aria-hidden', 'true');
                option.disabled = true;
            }
            option.appendChild(label.cloneNode(true));
            dropdown.appendChild(option);
        });
    },

    setupMobileDropdown() {
        const trigger = WorkspaceDOM.mobileTrigger;
        const dropdown = WorkspaceDOM.mobileDropdown;
        if (!trigger || !dropdown || dropdown.dataset.workspaceMenuBound === 'true') return;

        this.ensureMobileDropdownOptions();
        dropdown.setAttribute('aria-hidden', 'true');
        dropdown.setAttribute('inert', '');

        trigger.addEventListener('click', (event) => {
            event.stopPropagation();
            this.toggleMobileDropdown(!dropdown.classList.contains('open'));
        });

        dropdown.addEventListener('click', (event) => {
            const option = event.target.closest('.workspace-header-mobile-option[data-workspace-tab]');
            if (!option || !dropdown.contains(option)) return;
            event.preventDefault();
            this.switchToTab(option.dataset.workspaceTab);
            this.toggleMobileDropdown(false, { restoreFocus: true });
        });

        document.addEventListener('pointerdown', (event) => {
            const mobileHeader = WorkspaceDOM.headerTabsMobile;
            if (dropdown.classList.contains('open') && !mobileHeader?.contains(event.target)) {
                this.toggleMobileDropdown(false);
            }
        });

        document.addEventListener('focusin', (event) => {
            const mobileHeader = WorkspaceDOM.headerTabsMobile;
            if (dropdown.classList.contains('open') && !mobileHeader?.contains(event.target)) {
                this.toggleMobileDropdown(false);
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && dropdown.classList.contains('open')) {
                event.preventDefault();
                this.toggleMobileDropdown(false, { restoreFocus: true });
            }
        });

        dropdown.dataset.workspaceMenuBound = 'true';
    },

    /**
     * Toggle the compact title menu. It is a non-modal menu, so normal Tab
     * navigation remains available and only Escape restores trigger focus.
     */
    toggleMobileDropdown(open, { restoreFocus = false } = {}) {
        const trigger = WorkspaceDOM.mobileTrigger;
        const dropdown = WorkspaceDOM.mobileDropdown;
        if (!trigger || !dropdown) return;

        if (open) {
            WorkspaceState.mobileMenuReturnFocus = document.activeElement instanceof HTMLElement
                ? document.activeElement
                : trigger;
            dropdown.classList.add('open');
            trigger.setAttribute('aria-expanded', 'true');
            dropdown.setAttribute('aria-hidden', 'false');
            dropdown.removeAttribute('inert');
            requestAnimationFrame(() => {
                const activeOption = dropdown.querySelector('.workspace-header-mobile-option.active:not([hidden])');
                const firstOption = dropdown.querySelector('.workspace-header-mobile-option:not([hidden])');
                (activeOption || firstOption)?.focus();
            });
            return;
        }

        const wasOpen = dropdown.classList.contains('open');
        dropdown.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
        dropdown.setAttribute('aria-hidden', 'true');
        dropdown.setAttribute('inert', '');

        const returnFocus = WorkspaceState.mobileMenuReturnFocus || trigger;
        WorkspaceState.mobileMenuReturnFocus = null;
        if (wasOpen && restoreFocus && typeof returnFocus?.focus === 'function') {
            returnFocus.focus();
        }
    },

    updateAllowedTabs() {
        const tabs = WorkspaceDOM.tabs;
        tabs.forEach((tab) => {
            const allowed = this.isTabAllowed(tab.dataset.workspaceTab);
            tab.hidden = !allowed;
            tab.classList.toggle('hidden', !allowed);
        });
        if (!this.isTabAllowed(WorkspaceState.activeTab)) {
            this.switchToTab(DEFAULT_WORKSPACE_TAB);
            return;
        }
        this.updateMobileDropdownState(WorkspaceState.activeTab);
    },

    /** Keep the mobile page title and its menu selection synchronized. */
    updateMobileDropdownState(tabId) {
        const trigger = WorkspaceDOM.mobileTrigger;
        const triggerText = trigger?.querySelector('.workspace-mobile-trigger-text');
        const options = WorkspaceDOM.mobileOptions;
        const activeOption = Array.from(options).find((option) => option.dataset.workspaceTab === tabId);

        if (triggerText && activeOption) {
            const optionText = activeOption.querySelector('[data-i18n]');
            if (optionText) {
                triggerText.textContent = optionText.textContent;
                triggerText.setAttribute('data-i18n', optionText.getAttribute('data-i18n'));
            }
        }

        options.forEach((option) => {
            const isActive = option.dataset.workspaceTab === tabId;
            option.classList.toggle('active', isActive);
            option.setAttribute('aria-checked', isActive ? 'true' : 'false');
            if (isActive) {
                option.setAttribute('aria-current', 'page');
            } else {
                option.removeAttribute('aria-current');
            }
        });
    },
    switchToTab(tabId) {
        if (!tabId) return;
        if (!this.isTabAllowed(tabId)) {
            tabId = DEFAULT_WORKSPACE_TAB;
        }

        // A todo create/edit form is a routed page with potentially unsaved
        // input. Workspace-tab navigation therefore uses the same accessible
        // discard guard as the page's Back and Cancel actions.
        if (
            WorkspaceState.activeTab === 'todo' &&
            tabId !== 'todo' &&
            window.TodosState?.listEditorMode &&
            !window.TodosState.listEditorNavigationBypass
        ) {
            window.TodosManager?.requestListEditorExit?.(() => {
                window.TodosState.listEditorNavigationBypass = true;
                window.TodosManager.closeListEditorPage({ updateHistory: false, restoreFocus: false });
                this.switchToTab(tabId);
                window.TodosState.listEditorNavigationBypass = false;
            });
            return;
        }

        // Notes autosave normally makes tab changes seamless. If the pending
        // save fails (especially with a collaborator conflict), keep the user
        // on the note so its recovery UI remains available.
        if (
            WorkspaceState.activeTab === 'notes'
            && tabId !== 'notes'
            && window.NotesManager?.hasPendingEdits?.()
            && !window.NotesManager?.isNavigationBypassed?.()
            && typeof window.NotesManager?.requestWorkspaceExit === 'function'
        ) {
            window.NotesManager.requestWorkspaceExit(() => {
                window.NotesManager.setNavigationBypass?.(true);
                this.switchToTab(tabId);
                window.NotesManager.setNavigationBypass?.(false);
            });
            return;
        }

        // Update active tab in state
        WorkspaceState.activeTab = tabId;

        // Update workspace navigation buttons
        const tabs = WorkspaceDOM.tabs;
        tabs.forEach(tab => {
            const isActive = tab.dataset.workspaceTab === tabId;
            tab.classList.toggle('active', isActive);
            if (tab.getAttribute('role') === 'tab') {
                tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
                tab.tabIndex = isActive ? 0 : -1;
            }
            if (tab.getAttribute('role') === 'menuitemradio') {
                tab.setAttribute('aria-checked', isActive ? 'true' : 'false');
            }
            if (isActive) {
                tab.setAttribute('aria-current', 'page');
            } else {
                tab.removeAttribute('aria-current');
            }
        });

        // Update mobile dropdown state
        this.updateMobileDropdownState(tabId);

        // Hide all sections
        const sections = WorkspaceDOM.sections;
        sections.forEach(section => {
            section.style.display = 'none';
            section.setAttribute('aria-hidden', 'true');
        });

        // Show the target section
        const targetSection = WorkspaceDOM.getSectionById(tabId);
        if (targetSection) {
            targetSection.style.display = '';
            targetSection.removeAttribute('aria-hidden');
        }

        const container = WorkspaceDOM.container;
        if (container) {
            const noPaddingTabs = ['files', 'todo', 'notes'];
            container.classList.toggle('full-bleed', noPaddingTabs.includes(tabId));
        }

        // Handle files-specific header elements
        this.updateFilesHeaderElements(tabId === 'files');

        // Initialize files if switching to files tab
        if (tabId === 'files' && typeof FilesManager !== 'undefined') {
            FilesManager.initialize();
            if (typeof FileFoldersManager !== 'undefined') {
                FileFoldersManager.init();
            }
        }

        // Initialize todos if switching to todo tab
        if (tabId === 'todo' && typeof TodosManager !== 'undefined') {
            TodosManager.init();
            TodosManager.loadLists();
        }

        // Initialize notes if switching to notes tab
        if (tabId === 'notes' && typeof NotesManager !== 'undefined') {
            NotesManager.show();
        }

        // Initialize connections if switching to connections tab
        if (tabId === 'connections' && typeof window.ConnectionsWorkspace !== 'undefined') {
            window.ConnectionsWorkspace.show();
        }

        // Initialize memories if switching to memories tab
        if (tabId === 'memories' && typeof MemoriesManager !== 'undefined') {
            MemoriesManager.show();
        }

        // Show agents workspace when tabId is 'agents' and manager exists
        if (tabId === 'agents' && typeof window.AgentsWorkspaceManager !== 'undefined') {
            window.AgentsWorkspaceManager.show();
        }

        // Initialize prompt library if switching to prompts tab
        if (tabId === 'prompts' && typeof PromptLibraryManager !== 'undefined') {
            PromptLibraryManager.init();
            PromptLibraryManager.loadPrompts();
        }
        if (tabId === 'bookmarks' && typeof BookmarksManager !== 'undefined') {
            if (document.getElementById('bookmarksList')) {
                BookmarksManager.initControls();
                BookmarksManager.loadBookmarks();
            }
        }

        // Update URL without full navigation
        const urlMap = {
            'notifications': '/workspace/notifications',
            'messages': '/workspace/messages',
            'connections': '/workspace/connections',
            'files': '/workspace/files',
            'skills': '/workspace/skills',
            'agents': '/workspace/agents',
            'todo': '/workspace/todo',
            'notes': '/workspace/notes',
            'memories': '/workspace/memories',
            'prompts': '/workspace/prompts',
            'bookmarks': '/workspace/bookmarks',
        };
        const currentPath = window.location.pathname;
        const isTodoListEditorRoute = tabId === 'todo'
            && /^\/workspace\/todo\/lists\/(?:new|[^/]+\/edit)$/.test(currentPath);
        const newUrl = isTodoListEditorRoute
            ? currentPath
            : (urlMap[tabId] || urlMap[DEFAULT_WORKSPACE_TAB] || '/workspace/notifications');

        // Opening a nested todo editor through a direct URL must not be
        // flattened back to /workspace/todo or duplicated in browser history.
        if (isTodoListEditorRoute) {
            window.history.replaceState(
                { ...(window.history.state || {}), workspaceTab: tabId, todosListEditor: true },
                '',
                newUrl,
            );
        } else {
            window.history.pushState({ workspaceTab: tabId }, '', newUrl);
        }
    },

    updateFilesHeaderElements(show) {
        const uploadBtn = WorkspaceDOM.mainContainerHeaderFilesUpload;
        const filterBtn = WorkspaceDOM.filesCategoryFilterButton;

        if (uploadBtn) {
            uploadBtn.style.display = show ? 'flex' : 'none';
        }
        if (filterBtn) {
            filterBtn.style.display = show ? 'flex' : 'none';
        }
    },

    show() {
        const container = WorkspaceDOM.container;

        document.body?.classList.add('workspace-view-active');

        if (container) {
            container.style.display = 'grid';
        }

        // Initialize workspace if not already
        this.init();

        // Show the current active tab section
        this.switchToTab(WorkspaceState.activeTab);
    },

    hide() {
        const container = WorkspaceDOM.container;

        document.body?.classList.remove('workspace-view-active');

        if (container) {
            container.style.display = 'none';
        }

        // Close mobile dropdown if open
        this.toggleMobileDropdown(false);

        // Also hide files header elements
        this.updateFilesHeaderElements(false);
    },

    isVisible() {
        const container = WorkspaceDOM.container;
        if (!container) return false;
        return container.style.display !== 'none';
    },

    getActiveTab() {
        return WorkspaceState.activeTab;
    },

    setActiveTab(tabId) {
        if (tabId && ['notifications', 'messages', 'connections', 'files', 'skills', 'agents', 'todo', 'notes', 'memories', 'prompts', 'bookmarks'].includes(tabId)) {
            WorkspaceState.activeTab = this.isTabAllowed(tabId) ? tabId : DEFAULT_WORKSPACE_TAB;
        }
    },
};

// ============================================================================
// Global Functions
// ============================================================================

async function showWorkspaceContainer(options = {}) {
    // Leaving chat can require confirmation when split-screen owns unsaved or
    // streaming panel state. Do not mutate the workspace route until that
    // guarded teardown has completed.
    if (typeof hideChatContainer === 'function' && !await hideChatContainer()) {
        return false;
    }
    if (typeof window.realtimeCall?.isCallRouteActive === 'function' && window.realtimeCall.isCallRouteActive()) {
        window.realtimeCall.deactivateCallRoute({ restorePath: false, stopActive: true });
    }
    // Hide other containers
    if (typeof hideAutomationsContainer === 'function') hideAutomationsContainer();
    if (typeof hideProjectsContainer === 'function') hideProjectsContainer();
    if (typeof hideChatsSearchContainer === 'function') hideChatsSearchContainer();
    if (typeof window.hideProjectSidebar === 'function') window.hideProjectSidebar();

    // Update page title
    if (typeof window.setAppSectionTitle === 'function') {
        window.setAppSectionTitle('workspace');
    } else if (typeof window.setDocumentTitleWithAppName === 'function') {
        const section = typeof window.getTranslation === 'function'
            ? window.getTranslation('document_title_workspace', 'Workspace')
            : 'Workspace';
        window.setDocumentTitleWithAppName(section, { sectionKey: 'document_title_workspace' });
    } else {
        const applicationName = window.applicationName || 'Omlorix';
        const section = typeof window.getTranslation === 'function'
            ? window.getTranslation('document_title_workspace', 'Workspace')
            : 'Workspace';
        document.title = applicationName + ' - ' + section;
    }

    // Determine target tab (defaults to notifications when not specified)
    const requestedTab = typeof options.tab === 'string' ? options.tab : DEFAULT_WORKSPACE_TAB;
    WorkspaceState.pendingMemoryScope = options.memoryScope || null;
    if (typeof window !== 'undefined') {
        window.__workspacePendingMemoryScope = WorkspaceState.pendingMemoryScope;
    }
    WorkspaceManager.setActiveTab(requestedTab);

    // Show workspace
    WorkspaceManager.show();
    return true;
}

function hideWorkspaceContainer() {
    WorkspaceManager.hide();
}

function showFilesContainer() {
    showWorkspaceContainer({ tab: 'files' });
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Attach sidebar workspace button listener
    const sidebarWorkspaceButton = document.getElementById('sidebarWorkspace');
    if (sidebarWorkspaceButton) {
        sidebarWorkspaceButton.addEventListener('click', () => {
            showWorkspaceContainer();
        });
    }

    // Initialize workspace manager
    WorkspaceManager.init();
});

// ============================================================================
// Prompt Library Manager
// ============================================================================

const PromptLibraryManager = {
    initialized: false,
    i18nListenerBound: false,
    escapeHandlersRegistered: false,
    prompts: [],
    currentFilter: 'all', // all | mine | shared
    searchQuery: '',
    activePromptId: null,
    activePromptRevision: null,
    activePromptLatest: null,
    promptEditorInitialSnapshot: null,
    promptEditorSyncTimerId: null,
    promptEditorSyncInFlight: false,
    activeSharePromptId: null,
    promptShareMode: 'list',
    promptShareType: 'live',
    promptShareAction: 'link',
    promptShareStatus: null,
    promptShareUsers: [],
    promptShareUsersLoaded: false,
    promptShareUsersLoading: false,
    promptShareSelectedUserIds: [],
    pendingDeletePrompt: null,
    deleteConfirmDefaultText: '',
    pendingPromptShareId: null,
    pendingPromptShareType: null,
    promptAcceptOpening: false,
    promptAcceptModalInitialized: false,
    promptAcceptReturnFocus: null,

    isSharingAllowed() {
        if (typeof window === 'undefined') return true;
        return window.allowPromptShareFeature !== false;
    },

    promptHasExistingShareState(prompt) {
        return Boolean(
            prompt?.clone_share_id ||
            prompt?.live_share_id ||
            prompt?.collaborate_share_id ||
            Number(prompt?.subscriber_count || 0) > 0
        );
    },

    canManagePromptSharing(prompt) {
        return this.isSharingAllowed() || this.promptHasExistingShareState(prompt);
    },

    async request(input, init) {
        if (typeof window !== 'undefined' && typeof window.authedFetch === 'function') {
            return window.authedFetch(input, init);
        }
        return fetch(input, init);
    },

    t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    },

    tf(key, fallback, vars = {}) {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(this.t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    },

    setI18nText(element, key, fallback) {
        if (!element) return;
        element.setAttribute('data-i18n', key);
        element.textContent = this.t(key, fallback);
    },

    formatText(key, fallback, vars = {}) {
        const template = this.t(key, fallback);
        return String(template || fallback).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    },

    setPromptShareNotice(message, type = 'info') {
        const notice = document.getElementById('promptShareNotice');
        if (!notice) return;
        notice.className = `cs-notice cs-notice-${type}`;
        notice.textContent = message;
        notice.setAttribute('aria-hidden', 'false');
        notice.setAttribute('role', type === 'error' ? 'alert' : 'status');
    },

    clearPromptShareNotice() {
        const notice = document.getElementById('promptShareNotice');
        if (!notice) return;
        notice.className = 'cs-notice';
        notice.textContent = '';
        notice.setAttribute('aria-hidden', 'true');
        notice.setAttribute('role', 'status');
    },

    init() {
        if (this.initialized) return;

        this.renderEmptyStateText();
        if (!this.i18nListenerBound && typeof document !== 'undefined') {
            document.addEventListener('i18n:updated', () => this.renderEmptyStateText());
            this.i18nListenerBound = true;
        }

        const createBtn = document.getElementById('promptLibraryCreateBtn');
        if (createBtn) {
            createBtn.addEventListener('click', () => this.openEditor());
        }

        const searchInput = document.getElementById('promptLibrarySearchInput');
        if (searchInput) {
            searchInput.addEventListener('input', (event) => {
                this.searchQuery = String(event.target?.value || '').trim().toLowerCase();
                this.renderPrompts();
            });
        }

        const filterSelect = document.getElementById('promptLibraryFilterSelect');
        filterSelect?.addEventListener('customSelectChange', (event) => {
            const nextFilter = event.detail?.value || 'all';
            this.currentFilter = nextFilter;
            this.updateFilterSelect();
            this.renderPrompts();
        });

        this.initEditorScreen();
        this.initShareModal();
        this.initPromptAcceptModal();
        this.initDeleteOverlay();
        this.registerEscapeHandlers();
        this.initialized = true;
        window.setTimeout(() => void this.resumePendingPromptShare(), 0);
    },

    registerEscapeHandlers() {
        if (this.escapeHandlersRegistered || typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        window.registerEscapeHandler({
            id: 'prompt-library-editor',
            priority: 80,
            isActive: () => {
                const editor = document.getElementById('promptLibraryEditorContent');
                const section = document.getElementById('workspaceSectionPrompts');
                return Boolean(editor && editor.style.display !== 'none' && section && section.style.display !== 'none');
            },
            close: () => this.closeEditor(),
        });

        window.registerEscapeHandler({
            id: 'prompt-library-share',
            priority: 80,
            isActive: () => {
                const overlay = document.getElementById('promptShareOverlay');
                return Boolean(overlay && !overlay.hidden);
            },
            close: () => this.closeShareModal(),
        });

        window.registerEscapeHandler({
            id: 'prompt-library-share-accept',
            priority: 90,
            isActive: () => {
                const overlay = document.getElementById('promptAcceptOverlay');
                return Boolean(overlay && !overlay.hidden);
            },
            close: () => this.hidePromptAcceptModal(),
        });

        window.registerEscapeHandler({
            id: 'prompt-library-delete',
            priority: 80,
            isActive: () => {
                const overlay = document.getElementById('promptDeleteOverlay');
                return Boolean(overlay && !overlay.hidden);
            },
            close: () => this.hideDeleteOverlay(),
        });

        this.escapeHandlersRegistered = true;
    },

    initPromptAcceptModal() {
        if (this.promptAcceptModalInitialized) return;
        const overlay = document.getElementById('promptAcceptOverlay');
        const cancelBtn = document.getElementById('promptAcceptCancelBtn');
        const confirmBtn = document.getElementById('promptAcceptConfirmBtn');
        if (!overlay || !cancelBtn || !confirmBtn) return;

        cancelBtn.addEventListener('click', () => this.hidePromptAcceptModal());
        confirmBtn.addEventListener('click', () => void this.confirmPromptShareAcceptance());
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay && !confirmBtn.disabled) this.hidePromptAcceptModal();
        });
        this.promptAcceptModalInitialized = true;
    },

    storePendingPromptShare(intent) {
        const normalized = normalizePromptShareIntent(intent);
        if (!normalized) return null;
        try {
            window.sessionStorage?.setItem(PROMPT_SHARE_PENDING_STORAGE_KEY, JSON.stringify(normalized));
        } catch (error) {
            console.warn('Could not preserve the prompt share through sign-in:', error);
        }
        return normalized;
    },

    readPendingPromptShare() {
        try {
            const serialized = window.sessionStorage?.getItem(PROMPT_SHARE_PENDING_STORAGE_KEY);
            if (!serialized) return null;
            const normalized = normalizePromptShareIntent(JSON.parse(serialized));
            if (!normalized) this.clearStoredPendingPromptShare();
            return normalized;
        } catch (_) {
            this.clearStoredPendingPromptShare();
            return null;
        }
    },

    clearStoredPendingPromptShare() {
        try {
            window.sessionStorage?.removeItem(PROMPT_SHARE_PENDING_STORAGE_KEY);
        } catch (_) {
            // Storage can be unavailable in privacy-restricted browser contexts.
        }
    },

    handleSharedPromptRoute(intent) {
        const normalized = this.storePendingPromptShare(intent);
        if (!normalized) return false;

        // Capture the bearer capability before removing it from history. Keeping
        // only the workspace route prevents later referrers and Back navigation
        // from redisclosing a token that the client has already consumed.
        window.history.replaceState(
            { ...(window.history.state || {}), workspaceTab: 'prompts', pendingPromptShare: true },
            '',
            '/workspace/prompts',
        );
        if (typeof showWorkspaceContainer === 'function') {
            showWorkspaceContainer({ tab: 'prompts' });
        }
        this.init();
        window.setTimeout(() => void this.resumePendingPromptShare(), 0);
        return true;
    },

    async resumePendingPromptShare() {
        if (this.promptAcceptOpening || this.pendingPromptShareId) return false;
        const intent = this.readPendingPromptShare();
        if (!intent) return false;
        return this.showPromptSharePreview(intent);
    },

    setPromptAcceptButtonLabel(key, fallback) {
        const button = document.getElementById('promptAcceptConfirmBtn');
        const label = button?.querySelector('span');
        if (!button || !label) return;
        label.setAttribute('data-i18n', key);
        label.textContent = this.t(key, fallback);
    },

    getPromptAcceptTypeDescription(shareType) {
        return this.getPromptShareTypeDescription(shareType);
    },

    async promptShareResponseError(response, fallback) {
        const payload = await response.json().catch(() => null);
        const detail = typeof payload?.detail === 'string'
            ? payload.detail
            : (typeof payload?.message === 'string' ? payload.message : fallback);
        const error = new Error(detail || fallback);
        error.status = response.status;
        return error;
    },

    async showPromptSharePreview(intent) {
        const normalized = normalizePromptShareIntent(intent);
        if (!normalized || this.promptAcceptOpening) return false;
        this.promptAcceptOpening = true;
        this.pendingPromptShareId = normalized.shareId;
        this.pendingPromptShareType = normalized.shareType;
        this.promptAcceptReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

        const overlay = document.getElementById('promptAcceptOverlay');
        const titleEl = document.getElementById('promptAcceptTitle');
        const ownerEl = document.getElementById('promptAcceptOwner');
        const descriptionEl = document.getElementById('promptAcceptDescription');
        const typeInfoEl = document.getElementById('promptAcceptShareTypeInfo');
        const previewEl = document.getElementById('promptAcceptPreviewContent');
        const confirmBtn = document.getElementById('promptAcceptConfirmBtn');
        const cancelBtn = document.getElementById('promptAcceptCancelBtn');
        if (!overlay || !titleEl || !ownerEl || !descriptionEl || !typeInfoEl || !previewEl || !confirmBtn) {
            this.promptAcceptOpening = false;
            return false;
        }

        titleEl.textContent = this.t('workspace_skills_accept_loading', 'Loading...');
        ownerEl.textContent = '';
        descriptionEl.textContent = '';
        typeInfoEl.textContent = '';
        previewEl.textContent = '';
        confirmBtn.disabled = true;
        this.setPromptAcceptButtonLabel('workspace_notifications_accept', 'Accept');
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        // Deep links may finish loading in a background tab where animation
        // frames are throttled. Activate synchronously so the dialog is visible
        // as soon as that tab is shown.
        overlay.classList.add('active');

        try {
            const response = await this.request(`/api/v1/prompts/shared/${encodeURIComponent(normalized.shareId)}`, {
                method: 'GET',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) {
                throw await this.promptShareResponseError(
                    response,
                    this.t('prompt_accept_load_error_desc', 'Could not load this shared prompt. It may no longer exist.'),
                );
            }
            const payload = await response.json();
            const actualShareType = String(payload?.share_type || '').trim().toLowerCase();
            if (!PROMPT_SHARE_TYPES.has(actualShareType)) {
                throw new Error(this.t('prompt_accept_load_error_desc', 'Could not load this shared prompt. It may no longer exist.'));
            }

            // The API result, not the path label, determines the granted mode.
            this.pendingPromptShareType = actualShareType;
            titleEl.textContent = String(payload?.title || this.t('prompt_share_prompt_fallback', 'Prompt'));
            ownerEl.textContent = payload?.owner_name
                ? this.formatText('workspace_skills_shared_by', 'Shared by {name}', { name: payload.owner_name })
                : '';
            descriptionEl.textContent = String(payload?.description || '');

            const typeLabel = this.getPromptShareTypeLabel(actualShareType);
            const typeDescription = this.getPromptAcceptTypeDescription(actualShareType);
            const typeTitle = document.createElement('strong');
            const typeText = document.createElement('span');
            typeTitle.textContent = typeLabel;
            typeText.textContent = typeDescription;
            typeInfoEl.replaceChildren(typeTitle, typeText);
            previewEl.textContent = String(
                payload?.content_preview || this.t('prompt_accept_empty_content', 'This prompt has no content.'),
            );
            this.setPromptAcceptButtonLabel(
                actualShareType === 'clone' ? 'prompt_share_type_clone_title' : 'workspace_notifications_accept',
                actualShareType === 'clone' ? 'Clone' : 'Accept',
            );
            confirmBtn.disabled = false;
            window.setTimeout(() => confirmBtn.focus(), 0);
            return true;
        } catch (error) {
            titleEl.textContent = this.t('prompt_accept_load_error_title', 'Error loading prompt');
            descriptionEl.textContent = error?.message || this.t(
                'prompt_accept_load_error_desc',
                'Could not load this shared prompt. It may no longer exist.',
            );
            previewEl.textContent = '';
            typeInfoEl.textContent = '';
            confirmBtn.disabled = true;
            window.setTimeout(() => cancelBtn?.focus(), 0);
            return false;
        } finally {
            this.promptAcceptOpening = false;
        }
    },

    hidePromptAcceptModal({ preserveStoredIntent = false } = {}) {
        const overlay = document.getElementById('promptAcceptOverlay');
        overlay?.classList.remove('active');
        if (overlay) {
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
        }
        if (!preserveStoredIntent) this.clearStoredPendingPromptShare();
        this.pendingPromptShareId = null;
        this.pendingPromptShareType = null;
        this.promptAcceptOpening = false;
        const returnFocus = this.promptAcceptReturnFocus;
        this.promptAcceptReturnFocus = null;
        if (returnFocus?.isConnected) window.setTimeout(() => returnFocus.focus(), 0);
    },

    async confirmPromptShareAcceptance() {
        const shareId = this.pendingPromptShareId;
        const shareType = this.pendingPromptShareType;
        if (!shareId || !PROMPT_SHARE_TYPES.has(shareType)) return;

        const confirmBtn = document.getElementById('promptAcceptConfirmBtn');
        if (!confirmBtn) return;
        confirmBtn.disabled = true;
        this.setPromptAcceptButtonLabel('workspace_notifications_accepting', 'Accepting...');
        try {
            const endpoint = getPromptShareAcceptanceEndpoint(shareType, shareId);
            if (!endpoint) return;
            const response = await this.request(endpoint, {
                method: 'POST',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) {
                throw await this.promptShareResponseError(
                    response,
                    this.t('workspace_notifications_accept_error', 'Failed to accept invitation'),
                );
            }
            const payload = await response.json().catch(() => ({}));
            this.hidePromptAcceptModal();
            await this.loadPrompts();
            const successMessage = payload?.message || this.t('workspace_notifications_accept_success', 'Invitation accepted. The item has been added to your workspace.');
            if (typeof notifySuccess === 'function') notifySuccess(successMessage);
        } catch (error) {
            const message = error?.message || this.t('workspace_notifications_accept_error', 'Failed to accept invitation');
            if (typeof notifyError === 'function') notifyError(message);
            this.setPromptAcceptButtonLabel(
                shareType === 'clone' ? 'prompt_share_type_clone_title' : 'workspace_notifications_accept',
                shareType === 'clone' ? 'Clone' : 'Accept',
            );
            confirmBtn.disabled = false;
            confirmBtn.focus();
        }
    },

    initDeleteOverlay() {
        const overlay = document.getElementById('promptDeleteOverlay');
        const cancelBtn = document.getElementById('promptDeleteCancelBtn');
        const confirmBtn = document.getElementById('promptDeleteConfirmBtn');
        const confirmText = document.getElementById('promptDeleteConfirmText');

        if (!this.deleteConfirmDefaultText) {
            this.deleteConfirmDefaultText = confirmText?.textContent?.trim() || this.t('prompt_library_delete_confirm', 'Delete Prompt');
        }

        cancelBtn?.addEventListener('click', () => this.hideDeleteOverlay());
        confirmBtn?.addEventListener('click', () => this.confirmDeletePrompt());
        overlay?.addEventListener('click', (event) => {
            if (event.target === overlay) {
                this.hideDeleteOverlay();
            }
        });
    },

    initEditorScreen() {
        const cancelBtn = document.getElementById('promptEditorCancelBtn');
        const saveBtn = document.getElementById('promptEditorSaveBtn');
        const titleInput = document.getElementById('promptEditorTitleInput');

        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => this.closeEditor());
        }
        if (titleInput) titleInput.addEventListener('input', () => this.clearEditorErrors());
        ['promptEditorTitleInput', 'promptEditorDescriptionInput', 'promptEditorContentInput'].forEach((id) => {
            document.getElementById(id)?.addEventListener('input', () => this.updatePromptEditorMeta());
        });
        document.getElementById('promptConflictCopyBtn')?.addEventListener('click', () => void this.copyPromptDraft());
        document.getElementById('promptConflictReloadBtn')?.addEventListener('click', () => this.useLatestPromptVersion());
        document.getElementById('promptConflictKeepBtn')?.addEventListener('click', () => this.keepPromptDraftAsNextVersion());
        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const titleInput = document.getElementById('promptEditorTitleInput');
                const descriptionInput = document.getElementById('promptEditorDescriptionInput');
                const contentInput = document.getElementById('promptEditorContentInput');
                const title = String(titleInput?.value || '').trim();
                const description = String(descriptionInput?.value || '').trim();
                const content = String(contentInput?.value || '').trim();

                if (!title) {
                    this.showEditorTitleError();
                    return;
                }
                saveBtn.disabled = true;
                try {
                    if (this.activePromptId) {
                        const updated = await this.updatePrompt(this.activePromptId, {
                            title,
                            description,
                            content,
                            expected_revision: this.activePromptRevision,
                        });
                        this.applyPromptToEditor(updated);
                        if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_editor_updated', 'Prompt updated'));
                    } else {
                        await this.createPrompt({ title, description, content });
                        if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_editor_created', 'Prompt created'));
                    }
                    this.closeEditor();
                    await this.loadPrompts();
                } catch (error) {
                    console.error('Failed to save prompt:', error);
                    if (error?.code === 'prompt_revision_conflict' || error?.status === 409) {
                        await this.showLatestPromptConflict();
                    } else if (typeof notifyError === 'function') {
                        notifyError(error.message || this.t('prompt_editor_save_error', 'Failed to save prompt'));
                    }
                } finally {
                    saveBtn.disabled = false;
                    this.updateEditorSaveButton();
                }
            });
        }
    },

    clearEditorErrors() {
        const titleInput = document.getElementById('promptEditorTitleInput');
        const titleError = document.getElementById('promptEditorTitleError');
        window.FormValidation?.clearInputError(titleInput, titleError);
    },

    showEditorTitleError() {
        const titleInput = document.getElementById('promptEditorTitleInput');
        const titleError = document.getElementById('promptEditorTitleError');
        window.FormValidation?.showInputError(
            titleInput,
            titleError,
            this.t('prompt_editor_title_required', 'Please enter a prompt title'),
        );
    },

    updateEditorSaveButton() {
        const saveBtn = document.getElementById('promptEditorSaveBtn');
        if (!saveBtn) return;
        const isEditing = Boolean(this.activePromptId);
        const key = isEditing ? 'prompt_editor_save_edit' : 'prompt_editor_save_create';
        const fallback = isEditing ? 'Save prompt' : 'Create prompt';
        saveBtn.setAttribute('data-i18n', key);
        saveBtn.textContent = this.t(key, fallback);
    },

    getPromptEditorDraft() {
        return {
            title: String(document.getElementById('promptEditorTitleInput')?.value || '').trim(),
            description: String(document.getElementById('promptEditorDescriptionInput')?.value || '').trim(),
            content: String(document.getElementById('promptEditorContentInput')?.value || '').trim(),
        };
    },

    promptSnapshotsEqual(left, right) {
        return ['title', 'description', 'content'].every((key) => String(left?.[key] || '').trim() === String(right?.[key] || '').trim());
    },

    isPromptEditorDirty() {
        return Boolean(this.activePromptId && !this.promptSnapshotsEqual(this.getPromptEditorDraft(), this.promptEditorInitialSnapshot));
    },

    updatePromptEditorMeta(prompt = this.activePromptLatest) {
        const meta = document.getElementById('promptEditorRevisionMeta');
        if (!meta) return;
        if (!this.activePromptId || !prompt) {
            meta.textContent = '';
            meta.hidden = true;
            return;
        }
        const revision = Number(prompt.revision || this.activePromptRevision || 1);
        const editor = String(prompt.last_updated_by_name || this.t('prompt_editor_unknown_editor', 'Unknown editor'));
        const updated = this.formatPromptUpdatedAt(prompt.updated_at);
        meta.textContent = this.formatText(
            'prompt_editor_revision_meta',
            'Revision {revision} · Last updated by {name}{time}',
            { revision, name: editor, time: updated ? ` · ${updated}` : '' },
        );
        meta.hidden = false;
    },

    applyPromptToEditor(prompt, { keepDraft = false } = {}) {
        if (!prompt) return;
        if (!keepDraft) {
            const titleInput = document.getElementById('promptEditorTitleInput');
            const descriptionInput = document.getElementById('promptEditorDescriptionInput');
            const contentInput = document.getElementById('promptEditorContentInput');
            if (titleInput) titleInput.value = prompt.title || '';
            if (descriptionInput) descriptionInput.value = prompt.description || '';
            if (contentInput) contentInput.value = prompt.content || '';
            this.promptEditorInitialSnapshot = {
                title: prompt.title || '',
                description: prompt.description || '',
                content: prompt.content || '',
            };
        }
        this.activePromptRevision = Number(prompt.revision || 1);
        this.activePromptLatest = prompt;
        this.updatePromptEditorMeta(prompt);
    },

    formatPromptConflictSnapshot(prompt) {
        return [
            `${this.t('prompt_editor_title_label', 'Title')}: ${prompt?.title || ''}`,
            `${this.t('prompt_editor_description_label', 'Description')}: ${prompt?.description || ''}`,
            '',
            String(prompt?.content || ''),
        ].join('\n');
    },

    showPromptConflict(latest) {
        if (!latest || !this.activePromptId) return;
        const previousLatestRevision = Number(this.activePromptLatest?.revision || 0);
        const incomingRevision = Number(latest.revision || 1);
        this.activePromptLatest = latest;
        const panel = document.getElementById('promptEditorConflict');
        const wasShowingThisRevision = Boolean(
            panel
            && !panel.hidden
            && previousLatestRevision === incomingRevision
        );
        const local = document.getElementById('promptConflictLocalContent');
        const remote = document.getElementById('promptConflictRemoteContent');
        const remoteMeta = document.getElementById('promptConflictRemoteMeta');
        if (local) local.textContent = this.formatPromptConflictSnapshot(this.getPromptEditorDraft());
        if (remote) remote.textContent = this.formatPromptConflictSnapshot(latest);
        if (remoteMeta) {
            remoteMeta.textContent = this.formatText(
                'prompt_conflict_saved_by',
                'Revision {revision}, saved by {name}',
                {
                    revision: Number(latest.revision || 1),
                    name: latest.last_updated_by_name || this.t('prompt_editor_unknown_editor', 'Unknown editor'),
                },
            );
        }
        if (panel) panel.hidden = false;
        if (!wasShowingThisRevision) document.getElementById('promptConflictReloadBtn')?.focus();
    },

    hidePromptConflict() {
        const panel = document.getElementById('promptEditorConflict');
        if (panel) panel.hidden = true;
    },

    async showLatestPromptConflict() {
        if (!this.activePromptId) return;
        try {
            const latest = await this.fetchPrompt(this.activePromptId);
            this.showPromptConflict(latest);
        } catch (_) {
            if (typeof notifyError === 'function') {
                notifyError(this.t('prompt_conflict_load_error', 'The latest prompt could not be loaded. Your draft is still in the editor.'));
            }
        }
    },

    useLatestPromptVersion() {
        if (!this.activePromptLatest) return;
        this.applyPromptToEditor(this.activePromptLatest);
        this.hidePromptConflict();
        if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_conflict_latest_loaded', 'Latest saved version loaded'));
    },

    keepPromptDraftAsNextVersion() {
        if (!this.activePromptLatest) return;
        // Advancing the token is an explicit user choice made after comparing
        // both versions. Reuse the normal save path so another concurrent edit
        // is still rejected instead of being overwritten.
        this.applyPromptToEditor(this.activePromptLatest, { keepDraft: true });
        this.hidePromptConflict();
        document.getElementById('promptEditorSaveBtn')?.click();
    },

    async copyPromptDraft() {
        const draft = this.formatPromptConflictSnapshot(this.getPromptEditorDraft());
        try {
            await navigator.clipboard.writeText(draft);
            if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_conflict_draft_copied', 'Draft copied'));
        } catch (_) {
            if (typeof notifyError === 'function') notifyError(this.t('prompt_conflict_copy_error', 'Could not copy the draft'));
        }
    },

    startPromptEditorSync() {
        this.stopPromptEditorSync();
        if (!this.activePromptId) return;
        this.promptEditorSyncTimerId = window.setInterval(() => void this.syncPromptEditor(), 5000);
    },

    stopPromptEditorSync() {
        if (this.promptEditorSyncTimerId) window.clearInterval(this.promptEditorSyncTimerId);
        this.promptEditorSyncTimerId = null;
        this.promptEditorSyncInFlight = false;
    },

    async syncPromptEditor() {
        if (!this.activePromptId || this.promptEditorSyncInFlight || document.hidden) return;
        const promptId = this.activePromptId;
        this.promptEditorSyncInFlight = true;
        try {
            const latest = await this.fetchPrompt(promptId);
            if (this.activePromptId !== promptId) return;
            if (Number(latest.revision || 1) === Number(this.activePromptRevision || 1)) return;
            if (this.isPromptEditorDirty()) {
                this.showPromptConflict(latest);
            } else {
                this.applyPromptToEditor(latest);
                if (typeof notifyWarning === 'function') notifyWarning(this.t('prompt_sync_remote_update', 'A collaborator updated this prompt. The editor has been refreshed.'));
            }
        } catch (_) {
            // Background freshness checks are best-effort. An explicit save or
            // editor reload still reports an actionable error to the user.
        } finally {
            this.promptEditorSyncInFlight = false;
        }
    },

    initShareModal() {
        const overlay = document.getElementById('promptShareOverlay');
        const closeBtn = document.getElementById('promptShareCloseBtn');
        const primaryBtn = document.getElementById('promptSharePrimaryBtn');
        const secondaryBtn = document.getElementById('promptShareSecondaryBtn');
        const inviteSearch = document.getElementById('promptShareInviteSearch');

        if (overlay) {
            overlay.addEventListener('click', (event) => {
                if (event.target === overlay) {
                    this.closeShareModal();
                }
            });
        }
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closeShareModal());
        }
        primaryBtn?.addEventListener('click', async () => {
            if (this.promptShareMode === 'list') {
                this.enterPromptShareCreateMode();
                return;
            }
            if (this.getPromptShareAction() === 'invite') {
                await this.handleSendInvites();
                return;
            }
            await this.handleCreateShareLink();
        });
        secondaryBtn?.addEventListener('click', () => {
            const hasShares = Boolean(
                this.promptShareStatus?.clone_share_id ||
                this.promptShareStatus?.live_share_id ||
                this.promptShareStatus?.collaborate_share_id
            );
            if (this.promptShareMode === 'list' || !hasShares) {
                this.closeShareModal();
                return;
            }
            this.promptShareMode = 'list';
            this.renderPromptShareView();
        });
        inviteSearch?.addEventListener('input', () => {
            this.filterPromptShareUsers();
            if (this.promptShareSelectedUserIds.length) this.clearPromptShareInviteError();
        });
        document.querySelectorAll('input[name="promptShareType"]').forEach((input) => {
            input.addEventListener('change', () => this.renderPromptShareView());
        });
        document.querySelectorAll('input[name="promptShareAction"]').forEach((input) => {
            input.addEventListener('change', () => {
                this.promptShareAction = this.getPromptShareAction();
                this.clearPromptShareInviteError();
                this.renderPromptShareView();
            });
        });
    },

    getPromptShareType() {
        const checked = document.querySelector('input[name="promptShareType"]:checked');
        return String(checked?.value || this.promptShareType || 'live');
    },

    getPromptShareAction() {
        const checked = document.querySelector('input[name="promptShareAction"]:checked');
        return String(checked?.value || this.promptShareAction || 'link');
    },

    getPromptShareTypeLabel(shareType) {
        const normalized = String(shareType || '').trim().toLowerCase();
        if (normalized === 'clone') return this.t('prompt_share_type_clone_title', 'Clone');
        if (normalized === 'collaborate') return this.t('prompt_share_type_collaborate_title', 'Collaborate');
        return this.t('prompt_share_type_live_title', 'Live');
    },

    getPromptShareTypeDescription(shareType) {
        const normalized = String(shareType || '').trim().toLowerCase();
        if (normalized === 'clone') return this.t('prompt_share_type_clone_desc', 'Creates an independent copy');
        if (normalized === 'collaborate') return this.t('prompt_share_type_collaborate_desc', 'Recipients can work with a synced copy');
        return this.t('prompt_share_type_live_desc', 'Subscribers receive read-only updates');
    },

    getPromptShareUrl(shareType, shareId) {
        const prefix = {
            clone: '/prompts/clone',
            collaborate: '/prompts/collaborate',
            live: '/prompts/live',
        }[String(shareType || '').trim().toLowerCase()] || '/prompts/live';
        return `${window.location.origin}${prefix}/${encodeURIComponent(String(shareId || ''))}`;
    },

    renderPromptShareLinkCard(share) {
        const subscriberChip = share.count
            ? `<span class="cs-chip cs-chip-muted">${this.escapeHtml(`${share.count} subscriber${share.count === 1 ? '' : 's'}`)}</span>`
            : '';
        const copyLabel = this.t('prompt_share_action_copy', 'Copy');
        const openLabel = this.t('prompt_share_action_open', 'Open');
        const editLabel = this.t('prompt_share_action_edit', 'Edit');
        const deleteLabel = this.t('prompt_share_action_delete', 'Delete');
        return `
            <div class="cs-link-card" data-share-type="${this.escapeHtml(share.type)}">
                <div class="cs-link-url-row">
                    <input type="text" class="cs-link-url" value="${this.escapeHtml(share.url)}" readonly aria-label="${this.escapeHtml(this.t('prompt_share_link_aria', 'Prompt share link'))}">
                </div>
                <div class="cs-link-meta">
                    <span class="cs-chip">${this.escapeHtml(this.getPromptShareTypeLabel(share.type))}</span>
                    ${subscriberChip}
                </div>
                <div class="cs-link-actions">
                    <button type="button" class="om-button border cancel" data-action="copy">
                        ${Icons.copy}
                        ${this.escapeHtml(copyLabel)}
                    </button>
                    <button type="button" class="om-button border cancel" data-action="open">
                        ${Icons.open_window}
                        ${this.escapeHtml(openLabel)}
                    </button>
                    <button type="button" class="om-button border cancel" data-action="edit">
                        ${Icons.create}
                        ${this.escapeHtml(editLabel)}
                    </button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete">
                        ${Icons.trash}
                        ${this.escapeHtml(deleteLabel)}
                    </button>
                </div>
            </div>
        `;
    },

    bindPromptShareLinkActions() {
        const list = document.getElementById('promptShareLinkList');
        if (!list) return;
        list.querySelectorAll('.cs-link-card').forEach((card) => {
            const shareType = card.dataset.shareType || 'live';
            const url = String(card.querySelector('.cs-link-url')?.value || '');
            card.querySelector('[data-action="copy"]')?.addEventListener('click', async () => {
                if (!url) return;
                try {
                    await navigator.clipboard.writeText(url);
                    if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_share_link_copied', 'Share link copied'));
                } catch (_) {
                    if (typeof notifyError === 'function') notifyError(this.t('prompt_share_link_copy_failed', 'Failed to copy share link'));
                }
            });
            card.querySelector('[data-action="open"]')?.addEventListener('click', () => {
                if (url) window.open(url, '_blank', 'noopener,noreferrer');
            });
            card.querySelector('[data-action="edit"]')?.addEventListener('click', () => this.enterPromptShareCreateMode(shareType));
            card.querySelector('[data-action="delete"]')?.addEventListener('click', () => this.handleStopShare(shareType));
        });
    },

    async loadPromptShareStatus(promptId) {
        const response = await this.request(`/api/v1/prompts/share/status?prompt_id=${encodeURIComponent(promptId)}`, { method: 'GET' });
        if (!response.ok) {
            throw new Error(this.t('prompt_share_status_load_failed', 'Failed to load share status'));
        }
        this.promptShareStatus = await response.json();
    },

    showPromptShareInviteError() {
        const input = document.getElementById('promptShareInviteSearch');
        const error = document.getElementById('promptShareInviteError');
        const message = this.t('chat_share_invite_select_error', 'Select at least one user to invite.');
        if (window.FormValidation?.showInputError) {
            window.FormValidation.showInputError(input, error, message, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        if (error) {
            error.textContent = message;
            error.hidden = false;
        }
        input?.classList.add('cs-input-error');
        input?.setAttribute('aria-invalid', 'true');
        input?.focus();
    },

    clearPromptShareInviteError() {
        const input = document.getElementById('promptShareInviteSearch');
        const error = document.getElementById('promptShareInviteError');
        if (window.FormValidation?.clearInputError) {
            window.FormValidation.clearInputError(input, error, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        if (error) {
            error.hidden = true;
            error.textContent = '';
        }
        input?.classList.remove('cs-input-error');
        input?.setAttribute('aria-invalid', 'false');
    },

    renderPromptShareInviteUsers(users) {
        const userList = document.getElementById('promptShareInviteUserList');
        if (!userList) return;
        if (!users.length) {
            userList.innerHTML = `<div class="cs-invite-state">${this.escapeHtml(this.t('prompt_share_no_invite_users', 'No users available to invite.'))}</div>`;
            return;
        }
        userList.innerHTML = users.map((user) => {
            const id = String(user.id || '');
            const selected = this.promptShareSelectedUserIds.includes(id);
            const label = String(user.display_name || user.id || this.t('prompt_share_unknown_user', 'Unknown user'));
            return `
                <button type="button" class="cs-invite-user-item ${selected ? 'is-selected' : ''}" data-user-id="${this.escapeHtml(id)}">
                    <span class="cs-invite-avatar">${this.escapeHtml(label.slice(0, 2).toUpperCase())}</span>
                    <span class="cs-invite-user-info">
                        <span class="cs-invite-user-name">${this.escapeHtml(label)}</span>
                    </span>
                    <span class="cs-invite-check" aria-hidden="true">
                        ${Icons.check}
                    </span>
                </button>
            `;
        }).join('');
        userList.querySelectorAll('.cs-invite-user-item').forEach((item) => {
            item.addEventListener('click', () => this.togglePromptShareUser(item.dataset.userId));
        });
    },

    updatePromptShareSelectedUsers() {
        const selected = document.getElementById('promptShareInviteSelected');
        const count = document.getElementById('promptShareInviteSelectedCount');
        const list = document.getElementById('promptShareInviteSelectedList');
        if (!selected || !count || !list) return;
        const users = this.promptShareUsers.filter((user) => this.promptShareSelectedUserIds.includes(String(user.id || '')));
        selected.hidden = users.length === 0;
        count.textContent = String(users.length);
        list.innerHTML = users.map((user) => `
            <span class="cs-invite-selected-chip">
                <span>${this.escapeHtml(String(user.display_name || user.id || this.t('prompt_share_unknown_user', 'Unknown user')))}</span>
                <button type="button" data-user-id="${this.escapeHtml(String(user.id || ''))}" aria-label="${this.escapeHtml(this.t('prompt_share_remove_user_aria', 'Remove user'))}">
                    ${Icons.close}
                </button>
            </span>
        `).join('');
        list.querySelectorAll('button[data-user-id]').forEach((button) => {
            button.addEventListener('click', () => this.togglePromptShareUser(button.dataset.userId));
        });
    },

    togglePromptShareUser(userId) {
        const normalized = String(userId || '').trim();
        if (!normalized) return;
        const idx = this.promptShareSelectedUserIds.indexOf(normalized);
        if (idx >= 0) {
            this.promptShareSelectedUserIds.splice(idx, 1);
        } else {
            this.promptShareSelectedUserIds.push(normalized);
        }
        if (this.promptShareSelectedUserIds.length) this.clearPromptShareInviteError();
        this.filterPromptShareUsers();
        this.updatePromptShareSelectedUsers();
    },

    filterPromptShareUsers() {
        const input = document.getElementById('promptShareInviteSearch');
        const term = String(input?.value || '').trim().toLowerCase();
        const users = term
            ? this.promptShareUsers.filter((user) => `${user.display_name || ''}`.toLowerCase().includes(term))
            : this.promptShareUsers;
        this.renderPromptShareInviteUsers(users);
        this.updatePromptShareSelectedUsers();
    },

    async loadInviteUsers() {
        const userList = document.getElementById('promptShareInviteUserList');
        if (!userList || this.promptShareUsersLoading || this.promptShareUsersLoaded) return;
        this.promptShareUsersLoading = true;
        userList.innerHTML = `<div class="cs-invite-state">${this.escapeHtml(this.t('prompt_share_loading_users', 'Loading users...'))}</div>`;
        try {
            const users = [];
            const seenUserIds = new Set();
            let offset = 0;
            const limit = 100;
            while (true) {
                const response = await this.request(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, { method: 'GET' });
                if (!response.ok) throw new Error(this.t('prompt_share_load_users_failed', 'Failed to load users.'));
                const page = await response.json();
                const pageUsers = Array.isArray(page) ? page : [];
                pageUsers.forEach((user) => {
                    const userId = String(user?.id || '').trim();
                    if (!userId || seenUserIds.has(userId)) return;
                    seenUserIds.add(userId);
                    users.push(user);
                });
                const hasMore = String(response.headers.get('X-Has-More') || '').toLowerCase() === 'true';
                if (!hasMore || pageUsers.length === 0) break;
                offset += pageUsers.length;
            }
            this.promptShareUsers = users;
            this.promptShareUsersLoaded = true;
            this.filterPromptShareUsers();
        } catch (error) {
            userList.innerHTML = `<div class="cs-invite-state">${this.escapeHtml(this.t('prompt_share_load_users_failed', 'Failed to load users.'))}</div>`;
            if (typeof notifyError === 'function') notifyError(error.message || this.t('prompt_share_load_users_failed', 'Failed to load users.'));
        } finally {
            this.promptShareUsersLoading = false;
        }
    },

    enterPromptShareCreateMode(shareType = null) {
        this.promptShareMode = 'create';
        this.promptShareAction = 'link';
        if (shareType) {
            this.promptShareType = String(shareType);
            const radio = document.querySelector(`input[name="promptShareType"][value="${CSS.escape(this.promptShareType)}"]`);
            if (radio) radio.checked = true;
        }
        const actionLink = document.getElementById('promptShareActionLink');
        if (actionLink) actionLink.checked = true;
        this.renderPromptShareView();
    },

    renderPromptShareView() {
        const linksSection = document.getElementById('promptShareLinksSection');
        const linkList = document.getElementById('promptShareLinkList');
        const emptySection = document.getElementById('promptShareEmptySection');
        const formSection = document.getElementById('promptShareForm');
        const formTitle = document.getElementById('promptShareFormTitle');
        const primaryBtn = document.getElementById('promptSharePrimaryBtn');
        const secondaryBtn = document.getElementById('promptShareSecondaryBtn');
        const inviteField = document.getElementById('promptShareInviteField');
        const hasShares = Boolean(
            this.promptShareStatus?.clone_share_id ||
            this.promptShareStatus?.live_share_id ||
            this.promptShareStatus?.collaborate_share_id
        );

        this.promptShareType = this.getPromptShareType();
        this.promptShareAction = this.getPromptShareAction();
        this.clearPromptShareInviteError();
        this.clearPromptShareNotice();

        if (this.promptShareMode === 'list') {
            if (linksSection) linksSection.hidden = !hasShares;
            if (emptySection) emptySection.hidden = hasShares;
            if (formSection) formSection.hidden = true;
            this.setI18nText(primaryBtn, hasShares ? 'prompt_share_new_link' : 'prompt_share_create_link', hasShares ? 'New link' : 'Create link');
            this.setI18nText(secondaryBtn, 'prompt_share_done', 'Done');

            if (linkList) {
                const shares = [];
                if (this.promptShareStatus?.clone_share_id) {
                    shares.push({ type: 'clone', url: this.getPromptShareUrl('clone', this.promptShareStatus.clone_share_id), count: 0 });
                }
                if (this.promptShareStatus?.live_share_id) {
                    shares.push({ type: 'live', url: this.getPromptShareUrl('live', this.promptShareStatus.live_share_id), count: this.promptShareStatus.live_subscriber_count || 0 });
                }
                if (this.promptShareStatus?.collaborate_share_id) {
                    shares.push({ type: 'collaborate', url: this.getPromptShareUrl('collaborate', this.promptShareStatus.collaborate_share_id), count: this.promptShareStatus.collaborate_subscriber_count || 0 });
                }
                linkList.innerHTML = shares.map((share) => this.renderPromptShareLinkCard(share)).join('');
                this.bindPromptShareLinkActions();
            }
            return;
        }

        if (linksSection) linksSection.hidden = true;
        if (emptySection) emptySection.hidden = true;
        if (formSection) formSection.hidden = false;
        this.setI18nText(
            formTitle,
            this.promptShareAction === 'invite' ? 'prompt_share_form_invite_title' : 'prompt_share_create_new_link',
            this.promptShareAction === 'invite' ? 'Invite users' : 'Create new link'
        );
        this.setI18nText(
            primaryBtn,
            this.promptShareAction === 'invite' ? 'prompt_share_send_invites' : 'prompt_share_create_link',
            this.promptShareAction === 'invite' ? 'Send invites' : 'Create link'
        );
        this.setI18nText(secondaryBtn, hasShares ? 'prompt_share_cancel' : 'prompt_share_done', hasShares ? 'Cancel' : 'Done');
        if (inviteField) inviteField.hidden = this.promptShareAction !== 'invite';
        if (this.promptShareAction === 'invite') {
            if (this.promptShareUsersLoaded) {
                this.filterPromptShareUsers();
            } else {
                void this.loadInviteUsers();
            }
        }
    },

    async loadPrompts() {
        if (typeof window !== 'undefined' && window.enablePromptsFeature === false) {
            this.prompts = [];
            this.renderPrompts();
            return;
        }

        const loadingEl = document.getElementById('promptLibraryLoading');
        const listEl = document.getElementById('promptLibraryList');
        const emptyEl = document.getElementById('promptLibraryEmpty');

        if (loadingEl) loadingEl.style.display = 'flex';
        if (emptyEl) emptyEl.style.display = 'none';
        if (listEl) listEl.innerHTML = '';

        try {
            const params = new URLSearchParams({
                limit: String(PROMPT_LIBRARY_PAGE_LIMIT),
                offset: '0',
            });
            const response = await this.request(`/api/v1/prompts/?${params.toString()}`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!response.ok) {
                throw new Error(this.t('prompt_library_load_failed', 'Failed to load prompts'));
            }
            const prompts = unwrapPromptLibraryPage(await response.json());
            this.prompts = Array.isArray(prompts) ? prompts : [];
            this.renderPrompts();
        } catch (error) {
            console.error('Failed to load prompts:', error);
            if (typeof notifyError === 'function') notifyError(this.t('prompt_library_load_failed', 'Failed to load prompts'));
        } finally {
            if (loadingEl) loadingEl.style.display = 'none';
        }
    },

    getFilteredPrompts() {
        const q = this.searchQuery;
        return this.prompts.filter((prompt) => {
            const isMine = !prompt.is_subscribed;
            if (this.currentFilter === 'mine' && !isMine) return false;
            if (this.currentFilter === 'shared' && isMine) return false;

            if (!q) return true;
            const haystacks = [
                prompt.title,
                prompt.description,
                prompt.content_preview,
                prompt.owner_name,
            ].map((value) => String(value || '').toLowerCase());
            return haystacks.some((text) => text.includes(q));
        });
    },

    renderPrompts() {
        const listEl = document.getElementById('promptLibraryList');
        const emptyEl = document.getElementById('promptLibraryEmpty');
        if (!listEl) return;

        this.renderEmptyStateText();

        listEl.innerHTML = '';
        const prompts = this.getFilteredPrompts();
        if (!prompts.length) {
            if (emptyEl) emptyEl.style.display = 'flex';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';

        const fragment = document.createDocumentFragment();
        prompts.forEach((prompt) => {
            fragment.appendChild(this.createPromptCard(prompt));
        });
        listEl.appendChild(fragment);
    },

    renderEmptyStateText() {
        const emptyTextEl = document.getElementById('promptLibraryEmptyText');
        if (!emptyTextEl) return;

        const fallback = 'Save reusable prompt templates, share them with your team, and insert them via {trigger} in chat.';
        const template = (typeof window.getTranslation === 'function')
            ? window.getTranslation('prompt_library_empty_text', fallback)
            : fallback;
        const tokenPlaceholder = '{trigger}';
        const parts = String(template || fallback).split(tokenPlaceholder);
        const escapedParts = parts.map((part) => this.escapeHtml(part));

        if (escapedParts.length > 1) {
            emptyTextEl.innerHTML = escapedParts.join('<code>@</code>');
            return;
        }

        emptyTextEl.innerHTML = `${this.escapeHtml(String(template || fallback))} <code>@</code>`;
    },

    updateFilterSelect() {
        if (typeof window.setCustomSelectValue === 'function') {
            window.setCustomSelectValue('prompt_library_filter', this.currentFilter || 'all');
        }
    },

    createPromptCard(prompt) {
        const card = document.createElement('article');
        card.className = 'prompt-library-card user-prompt-card';
        const isMine = !prompt.is_subscribed;
        const canEdit = isMine || prompt.share_type === 'collaborate';
        const shareBadge = isMine
            ? this.t('prompt_library_badge_mine', 'Mine')
            : (prompt.share_type === 'collaborate'
                ? this.t('prompt_library_badge_shared_collaborate', 'Shared - Collaborate')
                : this.t('prompt_library_badge_shared_live', 'Shared - Live'));
        const ownerLine = !isMine && prompt.owner_name ? `<span class="prompt-library-owner">${this.escapeHtml(prompt.owner_name)}</span>` : '';
        const useLabel = this.t('prompt_library_use', 'Use');
        const editLabel = this.t('prompt_share_action_edit', 'Edit');
        const shareLabel = this.t('prompt_library_share', 'Share');
        const deleteLabel = this.t('prompt_share_action_delete', 'Delete');
        const removeLabel = this.t('prompt_library_remove', 'Remove');
        const updatedAt = this.formatPromptUpdatedAt(prompt.updated_at)
            || this.formatPromptUpdatedAt(prompt.created_at);
        const editorName = String(prompt.last_updated_by_name || '').trim();
        const updatedLabel = updatedAt && editorName
            ? this.formatText('prompt_library_updated_by', 'Updated {time} by {name}', { time: updatedAt, name: editorName })
            : (updatedAt ? `${this.t('prompt_library_updated_prefix', 'Updated')} ${updatedAt}` : '');

        card.innerHTML = `
            <div class="prompt-library-card-header">
                <div>
                    <h3 class="prompt-library-card-title">${this.escapeHtml(prompt.title || this.t('prompt_library_untitled', 'Untitled prompt'))}</h3>
                    <div class="prompt-library-card-meta">
                        <span class="prompt-library-badge ${isMine ? 'mine' : 'shared'}">${this.escapeHtml(shareBadge)}</span>
                        ${ownerLine}
                    </div>
                </div>
            </div>
            ${prompt.description ? `<p class="prompt-library-card-description">${this.escapeHtml(prompt.description)}</p>` : ''}
            <pre class="prompt-library-card-content">${this.escapeHtml(prompt.content_preview || '')}</pre>
            ${updatedLabel ? `<div class="prompt-library-card-updated"><span>${this.escapeHtml(updatedLabel)}</span></div>` : ''}
            <div class="prompt-library-card-actions">
                <button type="button" class="prompt-card-btn use" data-action="use">${this.escapeHtml(useLabel)}</button>
                ${canEdit ? `<button type="button" class="prompt-card-btn" data-action="edit">${this.escapeHtml(editLabel)}</button>` : ''}
                ${isMine && this.canManagePromptSharing(prompt) ? `<button type="button" class="prompt-card-btn" data-action="share">${this.escapeHtml(shareLabel)}</button>` : ''}
                <button type="button" class="prompt-card-btn danger" data-action="${isMine ? 'delete' : 'remove'}">${this.escapeHtml(isMine ? deleteLabel : removeLabel)}</button>
            </div>
        `;

        card.querySelector('[data-action="use"]')?.addEventListener('click', () => this.usePrompt(prompt));
        card.querySelector('[data-action="edit"]')?.addEventListener('click', async () => {
            try {
                await this.openEditorWithContent(prompt.id);
            } catch (error) {
                console.error('Failed to load prompt editor:', error);
                if (typeof notifyError === 'function') notifyError(this.t('prompt_library_load_prompt_failed', 'Failed to load prompt'));
            }
        });
        card.querySelector('[data-action="share"]')?.addEventListener('click', () => this.openShareModal(prompt));
        card.querySelector('[data-action="delete"]')?.addEventListener('click', () => this.deletePrompt(prompt));
        card.querySelector('[data-action="remove"]')?.addEventListener('click', () => this.unsubscribePrompt(prompt));

        return card;
    },

    /**
     * Formats prompt timestamps using the viewer's locale while keeping the
     * compact date-and-time treatment used by the memory cards.
     */
    formatPromptUpdatedAt(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },

    usePrompt(prompt) {
        if (typeof window.showChatStartContainer === 'function') {
            window.showChatStartContainer({});
        } else if (typeof window.showChatContainer === 'function') {
            window.showChatContainer();
        }
        if (typeof window.addPromptAttachment === 'function') {
            window.addPromptAttachment(prompt);
            if (typeof notifySuccess === 'function') {
                notifySuccess(this.t('prompt_library_attached_to_chat', 'Prompt attached to chat'));
            }
        } else if (typeof notifyWarning === 'function') {
            notifyWarning(this.t('prompt_library_attachments_unavailable', 'Prompt attachments are not available yet.'));
        }
    },

    suggestTitleFromContent(content) {
        const normalized = String(content || '')
            .split('\n')
            .map((line) => line.trim())
            .find(Boolean) || '';
        const collapsed = normalized.replace(/\s+/g, ' ').trim();
        if (!collapsed) {
            return 'Saved prompt';
        }
        return collapsed.length > 140 ? `${collapsed.slice(0, 137).trimEnd()}...` : collapsed;
    },

    ensurePromptLibraryVisible() {
        if (typeof showWorkspaceContainer === 'function') {
            showWorkspaceContainer({ tab: 'prompts' });
            return;
        }

        if (typeof WorkspaceManager !== 'undefined') {
            WorkspaceManager.setActiveTab('prompts');
            WorkspaceManager.show();
        }
    },

    openCreateFromContent(content, options = {}) {
        const normalizedContent = String(content || '').trim();
        if (!normalizedContent) {
            if (typeof notifyWarning === 'function') notifyWarning(this.t('prompt_library_nothing_to_save', 'Nothing to save to prompt library'));
            return;
        }
        if (typeof window !== 'undefined' && window.enablePromptsFeature === false) {
            if (typeof notifyWarning === 'function') notifyWarning(this.t('prompt_library_unavailable', 'Prompt library is not available'));
            return;
        }

        this.ensurePromptLibraryVisible();
        this.init();
        this.openEditor({
            title: String(options.title || '').trim() || this.suggestTitleFromContent(normalizedContent),
            description: String(options.description || '').trim(),
            content: normalizedContent,
        }, { isDraft: true });

        const titleInput = document.getElementById('promptEditorTitleInput');
        requestAnimationFrame(() => {
            titleInput?.focus();
            titleInput?.select();
        });
    },

    openEditor(prompt = null, options = {}) {
        const isDraft = options.isDraft === true;
        const isEditing = Boolean(prompt?.id) && !isDraft;
        this.activePromptId = isEditing ? prompt.id : null;
        this.activePromptRevision = isEditing ? Number(prompt.revision || 1) : null;
        this.activePromptLatest = isEditing ? prompt : null;
        this.promptEditorInitialSnapshot = {
            title: prompt?.title || '',
            description: prompt?.description || '',
            content: prompt?.content || '',
        };
        const listContent = document.getElementById('promptLibraryContent');
        const editorContent = document.getElementById('promptLibraryEditorContent');
        const heading = document.getElementById('promptEditorHeading');
        const titleInput = document.getElementById('promptEditorTitleInput');
        const descriptionInput = document.getElementById('promptEditorDescriptionInput');
        const contentInput = document.getElementById('promptEditorContentInput');

        this.clearEditorErrors();
        if (heading) {
            const key = isEditing ? 'prompt_editor_edit_title' : 'prompt_editor_create_title';
            const fallback = isEditing ? 'Edit Prompt' : 'Create Prompt';
            heading.setAttribute('data-i18n', key);
            heading.textContent = this.t(key, fallback);
        }
        if (titleInput) titleInput.value = prompt?.title || '';
        if (descriptionInput) descriptionInput.value = prompt?.description || '';
        if (contentInput) contentInput.value = prompt?.content || '';
        this.hidePromptConflict();
        this.updatePromptEditorMeta(isEditing ? prompt : null);
        this.updateEditorSaveButton();

        if (listContent) listContent.style.display = 'none';
        if (editorContent) editorContent.style.display = '';
        if (isEditing) this.startPromptEditorSync();
        else this.stopPromptEditorSync();
        requestAnimationFrame(() => titleInput?.focus());
    },

    async fetchPrompt(promptId) {
        const response = await this.request(`/api/v1/prompts/${encodeURIComponent(promptId)}`, { method: 'GET' });
        if (!response.ok) throw new Error(this.t('prompt_library_load_prompt_failed', 'Failed to load prompt'));
        return response.json();
    },

    async openEditorWithContent(promptId) {
        const prompt = await this.fetchPrompt(promptId);
        this.openEditor(prompt);
    },

    closeEditor() {
        const listContent = document.getElementById('promptLibraryContent');
        const editorContent = document.getElementById('promptLibraryEditorContent');
        if (editorContent) editorContent.style.display = 'none';
        if (listContent) listContent.style.display = '';
        this.stopPromptEditorSync();
        this.activePromptId = null;
        this.activePromptRevision = null;
        this.activePromptLatest = null;
        this.promptEditorInitialSnapshot = null;
        this.hidePromptConflict();
        this.clearEditorErrors();
        this.updatePromptEditorMeta(null);
        this.updateEditorSaveButton();
    },

    async openShareModal(prompt) {
        if (!prompt?.id) return;
        if (!this.canManagePromptSharing(prompt)) return;
        this.activeSharePromptId = prompt.id;
        const overlay = document.getElementById('promptShareOverlay');
        const subtitleEl = document.getElementById('promptShareSubtitle');
        if (subtitleEl) {
            const title = String(prompt.title || this.t('prompt_share_prompt_fallback', 'Prompt'));
            subtitleEl.removeAttribute('data-i18n');
            subtitleEl.textContent = this.t('prompt_share_sharing_prompt', 'Sharing "{title}"').replace('{title}', title);
        }
        this.promptShareMode = 'list';
        this.promptShareType = 'live';
        this.promptShareAction = 'link';
        this.promptShareStatus = null;
        this.promptShareSelectedUserIds = [];
        const typeRadio = document.getElementById('promptShareTypeLive');
        const actionRadio = document.getElementById('promptShareActionLink');
        if (typeRadio) typeRadio.checked = true;
        if (actionRadio) actionRadio.checked = true;
        const inviteSearch = document.getElementById('promptShareInviteSearch');
        if (inviteSearch) inviteSearch.value = '';
        this.clearPromptShareInviteError();

        if (overlay) {
            overlay.hidden = false;
            overlay.setAttribute('aria-hidden', 'false');
            requestAnimationFrame(() => overlay.classList.add('cs-active'));
        }
        await this.loadPromptShareStatus(prompt.id);
        this.renderPromptShareView();
    },

    closeShareModal() {
        const overlay = document.getElementById('promptShareOverlay');
        if (overlay) {
            overlay.classList.remove('cs-active');
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
        }
        this.activeSharePromptId = null;
        this.promptShareMode = 'list';
    },

    async handleCreateShareLink() {
        if (!this.activeSharePromptId) return;
        const shareType = this.getPromptShareType();
        const primaryBtn = document.getElementById('promptSharePrimaryBtn');
        try {
            if (primaryBtn) primaryBtn.disabled = true;
            const response = await this.request('/api/v1/prompts/share', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt_id: this.activeSharePromptId, share_type: shareType }),
            });
            if (!response.ok) throw new Error(this.t('prompt_share_error_create_link', 'Failed to create share link'));
            const payload = await response.json();
            if (payload.share_url && navigator?.clipboard?.writeText) {
                try {
                    await navigator.clipboard.writeText(payload.share_url);
                } catch (_) {
                    // ignore clipboard failure
                }
            }
            const noticeMessage = this.t('prompt_share_notice_link_ready', 'Share link ready');
            if (typeof notifySuccess === 'function') notifySuccess(noticeMessage);
            await this.loadPromptShareStatus(this.activeSharePromptId);
            await this.loadPrompts();
            this.promptShareMode = 'list';
            this.renderPromptShareView();
            this.setPromptShareNotice(noticeMessage);
        } catch (error) {
            console.error('Failed to share prompt:', error);
            const errorMessage = this.t('prompt_share_error_create_link', 'Failed to create share link');
            if (typeof notifyError === 'function') notifyError(errorMessage);
            this.setPromptShareNotice(errorMessage, 'error');
        } finally {
            if (primaryBtn) primaryBtn.disabled = false;
        }
    },

    async handleStopShare(shareType = null) {
        if (!this.activeSharePromptId) return;
        try {
            const response = await this.request('/api/v1/prompts/share/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt_id: this.activeSharePromptId, share_type: shareType || this.getPromptShareType() }),
            });
            if (!response.ok) throw new Error(this.t('prompt_share_error_remove', 'Failed to remove sharing'));
            const noticeMessage = this.t('prompt_share_notice_link_removed', 'Sharing removed');
            if (typeof notifySuccess === 'function') notifySuccess(noticeMessage);
            await this.loadPromptShareStatus(this.activeSharePromptId);
            await this.loadPrompts();
            this.promptShareMode = 'list';
            this.renderPromptShareView();
            this.setPromptShareNotice(noticeMessage);
        } catch (error) {
            console.error('Failed to stop share:', error);
            const errorMessage = this.t('prompt_share_error_remove', 'Failed to remove sharing');
            if (typeof notifyError === 'function') notifyError(errorMessage);
            this.setPromptShareNotice(errorMessage, 'error');
        }
    },

    async handleSendInvites() {
        if (!this.activeSharePromptId) return;
        if (!this.promptShareSelectedUserIds.length) {
            this.showPromptShareInviteError();
            return;
        }
        const shareType = this.getPromptShareType();
        const primaryBtn = document.getElementById('promptSharePrimaryBtn');
        try {
            if (primaryBtn) primaryBtn.disabled = true;
            const response = await this.request('/api/v1/prompts/invite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    item_id: this.activeSharePromptId,
                    user_ids: this.promptShareSelectedUserIds,
                    share_type: shareType,
                }),
            });
            if (!response.ok) throw new Error(this.t('prompt_share_error_send_invites', 'Failed to send invitations'));
            await response.json().catch(() => ({}));
            const noticeMessage = this.t('prompt_share_notice_invites_sent', 'Invitations sent');
            if (typeof notifySuccess === 'function') notifySuccess(noticeMessage);
            this.promptShareSelectedUserIds = [];
            this.promptShareMode = 'list';
            this.renderPromptShareView();
            this.setPromptShareNotice(noticeMessage);
        } catch (error) {
            console.error('Failed to invite users:', error);
            const errorMessage = this.t('prompt_share_error_send_invites', 'Failed to send invitations');
            if (typeof notifyError === 'function') notifyError(errorMessage);
            this.setPromptShareNotice(errorMessage, 'error');
        } finally {
            if (primaryBtn) primaryBtn.disabled = false;
        }
    },

    async createPrompt(payload) {
        const response = await this.request('/api/v1/prompts/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            throw new Error(this.t('prompt_create_failed', 'Failed to create prompt'));
        }
        return response.json();
    },

    async updatePrompt(promptId, payload) {
        const response = await this.request(`/api/v1/prompts/${encodeURIComponent(promptId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const body = await response.json().catch(() => null);
            const detail = body?.detail;
            const error = new Error(
                (typeof detail === 'object' && detail?.message)
                || (typeof detail === 'string' && detail)
                || this.t('prompt_update_failed', 'Failed to update prompt'),
            );
            error.status = response.status;
            error.code = typeof detail === 'object' ? detail?.code : null;
            throw error;
        }
        return response.json();
    },

    async deletePrompt(prompt) {
        if (!prompt?.id) {
            return;
        }

        this.showDeleteOverlay(prompt);
    },

    showDeleteOverlay(prompt) {
        const overlay = document.getElementById('promptDeleteOverlay');
        const description = document.getElementById('promptDeleteDescription');
        const confirmBtn = document.getElementById('promptDeleteConfirmBtn');
        const confirmText = document.getElementById('promptDeleteConfirmText');
        if (!overlay || !description) return;

        const title = String(prompt?.title || this.t('prompt_share_prompt_fallback', 'Prompt')).trim();
        this.pendingDeletePrompt = prompt;
        description.textContent = this.formatText(
            'prompt_library_delete_desc',
            'Are you sure you want to delete "{title}"? This action cannot be undone.',
            { title },
        );
        if (confirmBtn) {
            confirmBtn.disabled = false;
        }
        if (confirmText) {
            confirmText.textContent = this.deleteConfirmDefaultText || this.t('prompt_library_delete_confirm', 'Delete Prompt');
        }

        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => {
            (document.getElementById('promptDeleteCancelBtn') || confirmBtn)?.focus();
        });
    },

    hideDeleteOverlay() {
        const overlay = document.getElementById('promptDeleteOverlay');
        const confirmBtn = document.getElementById('promptDeleteConfirmBtn');
        const confirmText = document.getElementById('promptDeleteConfirmText');
        if (overlay) {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
        }
        if (confirmBtn) {
            confirmBtn.disabled = false;
        }
        if (confirmText) {
            confirmText.textContent = this.deleteConfirmDefaultText || this.t('prompt_library_delete_confirm', 'Delete Prompt');
        }
        this.pendingDeletePrompt = null;
    },

    async confirmDeletePrompt() {
        const prompt = this.pendingDeletePrompt;
        const confirmBtn = document.getElementById('promptDeleteConfirmBtn');
        const confirmText = document.getElementById('promptDeleteConfirmText');
        if (!prompt?.id) {
            this.hideDeleteOverlay();
            return;
        }

        if (confirmBtn) {
            confirmBtn.disabled = true;
        }
        try {
            const response = await this.request(`/api/v1/prompts/${encodeURIComponent(prompt.id)}`, {
                method: 'DELETE',
            });
            if (!response.ok) throw new Error(this.t('prompt_library_delete_error', 'Failed to delete prompt'));
            this.hideDeleteOverlay();
            if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_library_delete_success', 'Prompt deleted'));
            await this.loadPrompts();
        } catch (error) {
            console.error('Failed to delete prompt:', error);
            if (typeof notifyError === 'function') notifyError(this.t('prompt_library_delete_error', 'Failed to delete prompt'));
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
            }
            if (confirmText) {
                confirmText.textContent = this.deleteConfirmDefaultText || this.t('prompt_library_delete_confirm', 'Delete Prompt');
            }
        }
    },

    async unsubscribePrompt(prompt) {
        try {
            const response = await this.request(`/api/v1/prompts/shared/${encodeURIComponent(prompt.id)}/unsubscribe`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!response.ok) throw new Error(this.t('prompt_remove_from_library_failed', 'Failed to remove prompt from library'));
            if (typeof notifySuccess === 'function') notifySuccess(this.t('prompt_remove_from_library_success', 'Prompt removed from library'));
            await this.loadPrompts();
        } catch (error) {
            console.error('Failed to unsubscribe prompt:', error);
            if (typeof notifyError === 'function') notifyError(this.t('prompt_remove_failed', 'Failed to remove prompt'));
        }
    },

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};

// ============================================================================
// Bookmarks Manager
// ============================================================================

const BookmarksManager = {
    initialized: false,
    i18nListenerBound: false,
    bookmarks: [],
    currentSort: 'newest', // 'newest', 'oldest'
    currentFilter: 'all', // 'all', 'user', 'assistant'

    t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    },

    async loadBookmarks() {
        const loadingEl = document.getElementById('bookmarksLoading');
        const emptyEl = document.getElementById('bookmarksEmpty');
        const listEl = document.getElementById('bookmarksList');
        const controlsEl = document.getElementById('bookmarksControls');

        if (!listEl) return;

        // Show loading state
        if (loadingEl) loadingEl.style.display = 'flex';
        if (emptyEl) emptyEl.style.display = 'none';
        if (controlsEl) controlsEl.style.display = 'none';
        listEl.innerHTML = '';

        try {
            const response = await window.authedFetch('/api/v1/chats/bookmarks');
            if (!response.ok) {
                throw new Error(this.t('bookmarks_load_failed', 'Failed to load bookmarks'));
            }

            this.bookmarks = await response.json();

            // Hide loading
            if (loadingEl) loadingEl.style.display = 'none';

            if (!this.bookmarks || this.bookmarks.length === 0) {
                // Show empty state
                if (emptyEl) emptyEl.style.display = 'flex';
                return;
            }

            // Show controls and render bookmarks
            if (controlsEl) controlsEl.style.display = 'grid';
            this.renderBookmarks(listEl);

        } catch (error) {
            console.error('Failed to load bookmarks:', error);
            if (loadingEl) loadingEl.style.display = 'none';
            if (emptyEl) emptyEl.style.display = 'flex';
            if (typeof notifyError === 'function') {
                notifyError(this.t('bookmarks_load_failed', 'Failed to load bookmarks'));
            }
        }
    },

    sortBookmarks(bookmarks) {
        const sorted = [...bookmarks];
        switch (this.currentSort) {
            case 'oldest':
                sorted.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
                break;
            case 'newest':
            default:
                sorted.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                break;
        }
        return sorted;
    },

    filterBookmarks(bookmarks) {
        if (this.currentFilter === 'all') return bookmarks;
        return bookmarks.filter(b => b.role === this.currentFilter);
    },

    setSort(sortType) {
        this.currentSort = sortType;
        this.updateSortSelect();
        const listEl = document.getElementById('bookmarksList');
        if (listEl) this.renderBookmarks(listEl);
    },

    setFilter(filterType) {
        this.currentFilter = filterType;
        this.updateFilterSelect();
        const listEl = document.getElementById('bookmarksList');
        if (listEl) this.renderBookmarks(listEl);
    },

    updateSortSelect() {
        if (typeof window.setCustomSelectValue === 'function') {
            window.setCustomSelectValue('bookmarks_sort', this.currentSort || 'newest');
        }
    },

    updateFilterSelect() {
        if (typeof window.setCustomSelectValue === 'function') {
            window.setCustomSelectValue('bookmarks_filter', this.currentFilter || 'all');
        }
    },

    getNoResultsText() {
        if (this.currentFilter === 'user') {
            return this.t('bookmarks_no_results_user', 'No bookmarked user messages found');
        }
        if (this.currentFilter === 'assistant') {
            return this.t('bookmarks_no_results_assistant', 'No bookmarked AI responses found');
        }
        return this.t('bookmarks_no_results_all', 'No bookmarks found');
    },

    renderBookmarks(container) {
        if (!container) return;
        container.innerHTML = '';

        let displayBookmarks = this.filterBookmarks(this.bookmarks);
        displayBookmarks = this.sortBookmarks(displayBookmarks);

        if (displayBookmarks.length === 0) {
            const noResultsEl = document.createElement('div');
            noResultsEl.className = 'bookmarks-no-results';
            noResultsEl.innerHTML = `
                <p>${this.escapeHtml(this.getNoResultsText())}</p>
            `;
            container.appendChild(noResultsEl);
            return;
        }

        displayBookmarks.forEach(bookmark => {
            const card = this.createBookmarkCard(bookmark);
            container.appendChild(card);
        });
    },

    createBookmarkCard(bookmark) {
        const card = document.createElement('div');
        card.className = `bookmark-card bookmark-card-${bookmark.role || 'assistant'}`;
        card.dataset.messageId = bookmark.id;
        card.dataset.chatId = bookmark.chat_id;
        card.dataset.role = bookmark.role || 'assistant';
        const allowBookmarkShare = typeof window !== 'undefined'
            && window.allowBookmarkShareFeature === true
            && Boolean(window.chatSetup?.enable_chat_sharing);

        // Parse content to get preview text
        let previewText = '';
        try {
            const content = typeof bookmark.content === 'string' 
                ? JSON.parse(bookmark.content) 
                : bookmark.content;
            if (Array.isArray(content)) {
                // For user messages, look for 'user' type block first
                const userBlock = content.find(p => p.type === 'user');
                if (userBlock) {
                    previewText = userBlock.content || '';
                } else {
                    // Fall back to content or text type
                    const textPart = content.find(p => p.type === 'content' || p.type === 'text');
                    previewText = textPart?.content || '';
                }
            } else if (typeof content === 'string') {
                previewText = content;
            }
        } catch (_) {
            previewText = String(bookmark.content || '');
        }

        // Truncate preview
        const maxLength = 200;
        if (previewText.length > maxLength) {
            previewText = previewText.substring(0, maxLength) + '...';
        }

        // Format date
        const date = bookmark.created_at 
            ? new Date(bookmark.created_at).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            })
            : '';

        // Role indicator
        const isUser = bookmark.role === 'user';
        const roleIcon = isUser
            ? Icons.user
            : Icons.assistant;
        const roleLabel = isUser
            ? this.t('bookmarks_role_user_short', 'You')
            : this.t('bookmarks_role_assistant_short', 'AI');
        const roleTitle = isUser
            ? this.t('bookmarks_role_user_title', 'Your message')
            : this.t('bookmarks_role_assistant_title', 'AI response');
        const chatTitle = bookmark.chat_title || this.t('chat_reference_untitled', 'Untitled chat');
        const openTitle = this.t('bookmarks_open_in_chat', 'Open in chat');
        const shareTitle = this.t('bookmarks_share_chat', 'Share chat');
        const removeTitle = this.t('bookmarks_remove_bookmark', 'Remove bookmark');

        card.innerHTML = `
            <div class="bookmark-card-header">
                <div class="bookmark-card-title-row">
                    <span class="bookmark-card-role-badge ${isUser ? 'role-user' : 'role-assistant'}" title="${this.escapeHtml(roleTitle)}">
                        ${roleIcon}
                        <span>${this.escapeHtml(roleLabel)}</span>
                    </span>
                    <div class="bookmark-card-chat-title">${this.escapeHtml(chatTitle)}</div>
                </div>
                <div class="bookmark-card-actions">
                    <button type="button" class="bookmark-card-action-btn bookmark-card-open" title="${this.escapeHtml(openTitle)}" aria-label="${this.escapeHtml(openTitle)}">
                        ${Icons.open_window}
                    </button>
                    ${allowBookmarkShare ? `
                    <button type="button" class="bookmark-card-action-btn bookmark-card-share" title="${this.escapeHtml(shareTitle)}" aria-label="${this.escapeHtml(shareTitle)}">
                        ${Icons.connections}
                    </button>` : ''}
                    <button type="button" class="bookmark-card-action-btn bookmark-card-remove" title="${this.escapeHtml(removeTitle)}" aria-label="${this.escapeHtml(removeTitle)}">
                        ${Icons.bookmarkFilled}
                    </button>
                </div>
            </div>
            <div class="bookmark-card-content">${this.escapeHtml(previewText)}</div>
            <div class="bookmark-card-meta">
                <span class="bookmark-card-date">${date}</span>
            </div>
        `;

        // Event listeners
        const openBtn = card.querySelector('.bookmark-card-open');
        if (openBtn) {
            openBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                void this.openBookmarkInChat(bookmark.chat_id, bookmark.id);
            });
        }

        const shareBtn = card.querySelector('.bookmark-card-share');
        if (shareBtn) {
            shareBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (typeof window.ChatShareModal?.openForChat !== 'function') {
                    if (typeof notifyError === 'function') {
                        notifyError(this.t('bookmarks_chat_share_unavailable', 'Chat sharing is not available right now'));
                    }
                    return;
                }
                try {
                    await window.ChatShareModal.openForChat(bookmark.chat_id);
                } catch (error) {
                    console.error('Failed to open bookmark share flow:', error);
                    if (typeof notifyError === 'function') {
                        notifyError(error?.message || this.t('bookmarks_share_open_failed', 'Failed to open chat share'));
                    }
                }
            });
        }

        const removeBtn = card.querySelector('.bookmark-card-remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeBookmark(bookmark.id, card);
            });
        }

        // Click on card opens chat
        card.addEventListener('click', () => {
            void this.openBookmarkInChat(bookmark.chat_id, bookmark.id);
        });

        return card;
    },

    async removeBookmark(messageId, cardElement) {
        try {
            const response = await window.authedFetch('/api/v1/chats/messages/bookmark', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_id: messageId }),
            });

            if (!response.ok) {
                throw new Error(this.t('bookmarks_remove_failed', 'Failed to remove bookmark'));
            }

            // Remove from local array
            this.bookmarks = this.bookmarks.filter(b => b.id !== messageId);

            // Animate card removal
            if (cardElement) {
                cardElement.style.transition = 'opacity 0.2s, transform 0.2s';
                cardElement.style.opacity = '0';
                cardElement.style.transform = 'scale(0.95)';
                setTimeout(() => {
                    cardElement.remove();
                    // Check if list is now empty after filter
                    const listEl = document.getElementById('bookmarksList');
                    const emptyEl = document.getElementById('bookmarksEmpty');
                    const controlsEl = document.getElementById('bookmarksControls');
                    
                    if (this.bookmarks.length === 0) {
                        if (emptyEl) emptyEl.style.display = 'flex';
                        if (controlsEl) controlsEl.style.display = 'none';
                    } else if (listEl && listEl.children.length === 0) {
                        // Re-render in case filter shows nothing
                        this.renderBookmarks(listEl);
                    }
                }, 200);
            }

            if (typeof notifySuccess === 'function') {
                notifySuccess(this.t('bookmarks_remove_success', 'Bookmark removed'));
            }
        } catch (error) {
            console.error('Failed to remove bookmark:', error);
            if (typeof notifyError === 'function') {
                notifyError(this.t('bookmarks_remove_failed', 'Failed to remove bookmark'));
            }
        }
    },

    async openBookmarkInChat(chatId, messageId) {
        const normalizedChatId = String(chatId || '').trim();
        const normalizedMessageId = String(messageId || '').trim();
        if (!normalizedChatId || !normalizedMessageId || typeof window.loadChatView !== 'function') {
            return false;
        }

        try {
            const loaded = await window.loadChatView(normalizedChatId, false, {
                focusMessageId: normalizedMessageId,
            });
            if (!loaded) return false;

            if (typeof window.restoreProjectSidebarForChat === 'function') {
                await window.restoreProjectSidebarForChat(normalizedChatId);
            }
            return true;
        } catch (error) {
            console.error('Failed to open bookmark in chat:', error);
            if (typeof notifyError === 'function') {
                notifyError(this.t('bookmarks_open_failed', 'Failed to open the bookmarked message'));
            }
            return false;
        }
    },

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    initControls() {
        if (this.initialized) {
            return;
        }
        const sortSelect = document.getElementById('bookmarksSortSelect');
        sortSelect?.addEventListener('customSelectChange', (event) => {
            this.setSort(event.detail?.value || 'newest');
        });

        const filterSelect = document.getElementById('bookmarksFilterSelect');
        filterSelect?.addEventListener('customSelectChange', (event) => {
            this.setFilter(event.detail?.value || 'all');
        });

        if (!this.i18nListenerBound && typeof document !== 'undefined') {
            document.addEventListener('i18n:updated', () => {
                const listEl = document.getElementById('bookmarksList');
                if (listEl && this.bookmarks.length > 0) {
                    this.renderBookmarks(listEl);
                }
            });
            this.i18nListenerBound = true;
        }

        this.updateSortSelect();
        this.updateFilterSelect();
        this.initialized = true;
    }
};

// Expose to window for external access
if (typeof window !== 'undefined') {
    window.showWorkspaceContainer = showWorkspaceContainer;
    window.hideWorkspaceContainer = hideWorkspaceContainer;
    window.showFilesContainer = showFilesContainer;
    window.WorkspaceManager = WorkspaceManager;
    window.PromptLibraryManager = PromptLibraryManager;
    window.BookmarksManager = BookmarksManager;
}
