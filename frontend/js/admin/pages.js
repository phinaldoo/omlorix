(function () {
    const DEFAULT_PAGE = 'dashboard';
    const ADMIN_BASE_PATH = '/admin';

    /**
     * Scroll a paginated list to its first row without fighting the app's
     * reduced-motion preference. Keeping this behavior shared prevents
     * Skills and Backup History from drifting into different page transitions.
     */
    window.scrollAdminPaginatedListToStart = (element) => {
        if (!element || typeof element.scrollIntoView !== 'function') {
            return;
        }

        let reduceMotion = false;
        if (typeof window.matchMedia === 'function') {
            try {
                reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            } catch (_error) {
                // Smooth scrolling remains the safe fallback when media queries are unavailable.
            }
        }

        element.scrollIntoView({
            behavior: reduceMotion ? 'auto' : 'smooth',
            block: 'start',
            inline: 'nearest',
        });
    };

    const pages = Object.fromEntries(
        Array.from(document.querySelectorAll('.content .page'))
            .map((page) => {
                const key = page.id?.replace('page-', '');
                return key ? [key, page] : null;
            })
            .filter(Boolean)
    );

    if (!Object.keys(pages).length) {
        return;
    }

    const buttons = Array.from(document.querySelectorAll('#adminSidebar .sidebar-element[data-page]'))
        .filter((button) => pages[button.dataset.page]);
    const toolCards = Array.from(document.querySelectorAll('.tool-card[data-target-page]'));
    const headerLogoButton = document.getElementById('adminHeaderLogoButton');
    const mainContentEl = document.querySelector('.admin-main-content');
    const contentContainerEl = document.querySelector('.admin-main-content .content');

    let active = null;

    const TOOL_DETAIL_PAGES = new Set(
        toolCards
            .map((card) => card.dataset.targetPage)
            .filter(Boolean)
    );
    TOOL_DETAIL_PAGES.add('websearch-providers-select');
    TOOL_DETAIL_PAGES.add('websearch-providers-form');
    TOOL_DETAIL_PAGES.add('mcp-settings-create');
    TOOL_DETAIL_PAGES.add('mcp-settings-edit');

    const ROUTE_ALIASES = new Map([
        ['chats', 'chat'],
        ['custom-python-tools-create', 'custom-python-tools'],
        ['custom-python-tools-edit', 'custom-python-tools'],
    ]);

    const pageGroup = (keys, handlers = {}) => ({
        keys: new Set(Array.isArray(keys) ? keys : [keys]),
        init: handlers.init,
        teardown: handlers.teardown,
    });

    const simpleLifecycleGroup = ({ pages: pageKeys, init, teardown }) =>
        pageGroup(pageKeys, {
            init: () => window[init]?.(),
            teardown: teardown ? () => window[teardown]?.() : undefined,
        });

    const lifecycleGroups = [
        pageGroup('dashboard', { init: () => initDashboardPage?.() }),
        pageGroup('admin-notifications', {
            init: () => initAdminNotificationsPage?.(),
            teardown: () => window.teardownAdminNotificationsPage?.(),
        }),
        simpleLifecycleGroup({ pages: 'audit-logs', init: 'initAuditLogsPage', teardown: 'teardownAuditLogsPage' }),
        pageGroup('providers', {
            init: () => {
                window.adminProvidersShowList?.();
                initProvidersPage?.();
            },
        }),
        pageGroup(['models', 'models-dictation-settings', 'models-read-aloud-settings', 'models-realtime-settings'], {
            init: (pageKey) => {
                if (pageKey === 'models') {
                    window.adminModelsShowList?.();
                }
                window.initModelsPage?.({
                    pageKey,
                    reloadSchema: pageKey !== 'models',
                });
            },
            teardown: () => window.teardownModelsPage?.(),
        }),
        simpleLifecycleGroup({ pages: 'groups', init: 'initGroupsPage' }),
        simpleLifecycleGroup({ pages: 'provider-groups', init: 'initProviderGroupsPage' }),
        simpleLifecycleGroup({ pages: ['rate-limits', 'rate-limits-form'], init: 'initRateLimitsPage' }),
        simpleLifecycleGroup({ pages: ['websearch-providers', 'websearch-providers-select', 'websearch-providers-form'], init: 'initWebsearchProvidersPage' }),
        simpleLifecycleGroup({ pages: 'service-connections', init: 'initServiceConnectionsPage', teardown: 'teardownServiceConnectionsPage' }),
        simpleLifecycleGroup({ pages: 'create-slide-presentation-settings', init: 'initCreateSlidePresentationSettingsPage', teardown: 'teardownCreateSlidePresentationSettingsPage' }),
        simpleLifecycleGroup({ pages: 'deep-research-settings', init: 'initDeepResearchSettingsPage', teardown: 'teardownDeepResearchSettingsPage' }),
        simpleLifecycleGroup({ pages: 'weather-tool-settings', init: 'initWeatherToolSettingsPage', teardown: 'teardownWeatherToolSettingsPage' }),
        simpleLifecycleGroup({ pages: 'image-generation-settings', init: 'initImageGenerationSettingsPage', teardown: 'teardownImageGenerationSettingsPage' }),
        simpleLifecycleGroup({ pages: 'video-generation-settings', init: 'initVideoGenerationSettingsPage', teardown: 'teardownVideoGenerationSettingsPage' }),
        simpleLifecycleGroup({ pages: 'audio-generation-settings', init: 'initAudioGenerationSettingsPage', teardown: 'teardownAudioGenerationSettingsPage' }),
        simpleLifecycleGroup({ pages: 'music-generation-settings', init: 'initMusicGenerationSettingsPage', teardown: 'teardownMusicGenerationSettingsPage' }),
        simpleLifecycleGroup({ pages: 'code-execution-settings', init: 'initCodeExecutionSettingsPage', teardown: 'teardownCodeExecutionSettingsPage' }),
        simpleLifecycleGroup({ pages: ['mcp-settings', 'mcp-settings-create', 'mcp-settings-edit'], init: 'initMcpServersPage', teardown: 'teardownMcpServersPage' }),
        simpleLifecycleGroup({ pages: ['custom-python-tools', 'custom-python-tools-create', 'custom-python-tools-edit'], init: 'initCustomPythonToolsPage', teardown: 'teardownCustomPythonToolsPage' }),
        pageGroup('users', {
            init: () => {
                initUsersPage?.();
                window.initDeletedUsersSection?.();
            },
            teardown: () => window.teardownUsersSettingsPage?.(),
        }),
        simpleLifecycleGroup({ pages: 'user-settings', init: 'initAdminUserSettingsPage', teardown: 'teardownAdminUserSettingsPage' }),
        simpleLifecycleGroup({ pages: ['users', 'user-create-single', 'user-create-bulk'], init: 'initAdminUserCreatePage', teardown: 'teardownAdminUserCreatePage' }),
        pageGroup('about', { init: () => initAboutPage?.() }),
        simpleLifecycleGroup({ pages: 'general', init: 'initGeneralSettingsPage', teardown: 'teardownGeneralSettingsPage' }),
        simpleLifecycleGroup({ pages: 'login', init: 'initLoginGeneralSettingsPage', teardown: 'teardownLoginGeneralSettingsPage' }),
        simpleLifecycleGroup({ pages: 'login-customization', init: 'initLoginCustomizationSettingsPage', teardown: 'teardownLoginCustomizationSettingsPage' }),
        simpleLifecycleGroup({ pages: 'login-social', init: 'initLoginSocialSettingsPage', teardown: 'teardownLoginSocialSettingsPage' }),
        simpleLifecycleGroup({ pages: 'login-enterprise-sso', init: 'initLoginEnterpriseSSOSettingsPage', teardown: 'teardownLoginEnterpriseSSOSettingsPage' }),
        simpleLifecycleGroup({ pages: 'login-ldap', init: 'initLoginLDAPSettingsPage', teardown: 'teardownLoginLDAPSettingsPage' }),
        simpleLifecycleGroup({ pages: 'security', init: 'initSecuritySettingsPage', teardown: 'teardownSecuritySettingsPage' }),
        simpleLifecycleGroup({ pages: 'security-ips', init: 'initSecurityIpsPage', teardown: 'teardownSecurityIpsPage' }),
        simpleLifecycleGroup({ pages: 'security-ip-analytics', init: 'initSecurityIpAnalyticsPage', teardown: 'teardownSecurityIpAnalyticsPage' }),
        simpleLifecycleGroup({ pages: 'user-notifications', init: 'initUserNotificationsPage', teardown: 'teardownUserNotificationsPage' }),
        pageGroup('privacy-policy', {
            init: () => window.initPrivacyPolicy?.(),
            teardown: () => window.teardownPrivacyPolicyPage?.(),
        }),
        pageGroup('terms-of-service', { init: () => window.initTermsOfService?.() }),
        simpleLifecycleGroup({ pages: 'chat', init: 'initDataControlsPage' }),
        simpleLifecycleGroup({ pages: 'database', init: 'initDatabasePage', teardown: 'teardownDatabasePage' }),
        simpleLifecycleGroup({ pages: 'model-statistics', init: 'initModelStatisticsPage' }),
        simpleLifecycleGroup({ pages: 'user-statistics', init: 'initUserStatisticsPage', teardown: 'teardownUserStatisticsPage' }),
        simpleLifecycleGroup({ pages: 'file-storage', init: 'initFileStoragePage' }),
        pageGroup('model-feedback', {
            init: () => window.initModelFeedbackPage?.(),
            teardown: () => window.cleanupModelFeedbackPage?.(),
        }),
    ];

    const hasBlockingToolEscapeTarget = () => {
        if (document.body?.classList.contains('admin-mobile-nav-open')) {
            return true;
        }

        return Boolean(document.querySelector([
            '.admin-select.open',
            '.admin-multiselect.open',
            '.icon-picker-dropdown:not([hidden])',
            '.shared-modal-overlay:not([hidden])',
            '.overlay-container.visible',
            '.modal-overlay.visible',
        ].join(', ')));
    };

    const registerToolsEscapeShortcut = () => {
        if (typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        window.registerEscapeHandler({
            id: 'admin-tools-pages-back-to-cards',
            priority: 90,
            isActive: () => TOOL_DETAIL_PAGES.has(active) && !hasBlockingToolEscapeTarget(),
            close: () => activate('tools'),
        });
    };

    const resetScrollPosition = () => {
        window.scrollTo?.(0, 0);
        mainContentEl?.scrollTo?.(0, 0);
        contentContainerEl?.scrollTo?.(0, 0);

        if (mainContentEl) {
            mainContentEl.scrollTop = 0;
        }

        if (contentContainerEl) {
            contentContainerEl.scrollTop = 0;
        }
    };

    const getPagePath = (pageKey) =>
        pageKey === DEFAULT_PAGE ? ADMIN_BASE_PATH : `${ADMIN_BASE_PATH}/${pageKey}`;

    const getPageFromCurrentPath = () => {
        const { pathname } = window.location;
        const normalizedPath = pathname.replace(/\/+$/, '') || '/';

        if (normalizedPath === ADMIN_BASE_PATH) {
            return DEFAULT_PAGE;
        }

        if (normalizedPath.startsWith(`${ADMIN_BASE_PATH}/`)) {
            const segment = normalizedPath.slice(ADMIN_BASE_PATH.length + 1);
            const alias = ROUTE_ALIASES.get(segment) || segment;
            if (pages[alias]) {
                return alias;
            }
        }

        return null;
    };

    const updateHistory = (pageKey, historyAction) => {
        if (!window.history || !window.history.pushState) {
            return;
        }

        const url = getPagePath(pageKey);
        const state = { page: pageKey };

        if (historyAction === 'replace') {
            window.history.replaceState(state, '', url);
        } else if (historyAction === 'push') {
            window.history.pushState(state, '', url);
        }
    };

    const runLifecycle = (pageKey) => {
        lifecycleGroups.forEach((group) => {
            if (group.keys.has(pageKey)) {
                group.init?.(pageKey);
            } else {
                group.teardown?.();
            }
        });
    };

    const performActivation = (requested = DEFAULT_PAGE, { history: historyAction = 'push' } = {}) => {
        const key = pages[requested] ? requested : DEFAULT_PAGE;
        if (key === active) {
            return;
        }

        active = key;
        Object.entries(pages).forEach(([pageKey, page]) => {
            page.hidden = pageKey !== key;
        });

        buttons.forEach((button) => {
            button.classList.toggle('active', button.dataset.page === key);
        });

        if (key !== 'chat') {
            window.adminChatOversightHideModal?.();
        }

        runLifecycle(key);

        if (historyAction !== 'none') {
            updateHistory(key, historyAction);
        }

        resetScrollPosition();
        window.dispatchEvent(new CustomEvent('admin:page-activated', { detail: { page: key } }));
    };

    const activate = (requested = DEFAULT_PAGE, options = {}) => {
        const { history: historyAction = 'push' } = options;
        const key = pages[requested] ? requested : DEFAULT_PAGE;
        if (key === active) {
            return;
        }

        const confirmNavigation = window.unsavedChangesManager?.confirmIfNeeded;
        if (active && typeof confirmNavigation === 'function') {
            const prompted = confirmNavigation({
                context: { fromPage: active, toPage: key },
                onConfirm: () => performActivation(key, { history: historyAction }),
                onCancel: historyAction === 'none' && active
                    ? () => updateHistory(active, 'replace')
                    : undefined,
            });
            if (prompted) {
                return;
            }
        }

        performActivation(key, { history: historyAction });
    };

    document.addEventListener('click', (event) => {
        const target = event.target.closest('[data-admin-target-page], #adminSidebar .sidebar-element[data-page], .tool-card[data-target-page]');
        if (!target) {
            return;
        }

        const pageKey = target.dataset.adminTargetPage || target.dataset.page || target.dataset.targetPage;
        if (!pageKey) {
            return;
        }

        event.preventDefault();
        activate(pageKey);
    });

    headerLogoButton?.addEventListener('click', () => activate('dashboard'));

    window.activateAdminPage = (pageKey, options) => activate(pageKey, options);
    window.showPage = (pageKey, options) => activate(pageKey, options);

    registerToolsEscapeShortcut();

    const initialPage = getPageFromCurrentPath();
    const runInitialActivation = () => activate(initialPage ?? DEFAULT_PAGE, { history: 'replace' });

    if (document.readyState === 'complete') {
        runInitialActivation();
    } else {
        window.addEventListener('DOMContentLoaded', runInitialActivation, { once: true });
    }

    window.addEventListener('popstate', () => {
        const pageFromPath = getPageFromCurrentPath();
        activate(pageFromPath ?? DEFAULT_PAGE, { history: 'none' });
    });
})();
