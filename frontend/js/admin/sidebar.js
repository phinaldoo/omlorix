(function () {
    const sidebar = document.getElementById('adminSidebar');
    if (!sidebar) return;

    const collapseButton = document.getElementById('adminSidebarCollapse');
    const closeButton = document.getElementById('adminSidebarClose');
    const toggleButton = document.getElementById('adminSidebarToggle');
    const backdrop = document.getElementById('adminSidebarBackdrop');
    const body = document.body;
    const STORAGE_KEY = 'omlorix.admin.sidebar.collapsed';
    const MOBILE_BREAKPOINT = 1024;
    let resizeTimer = null;
    let collapseTimer = null;

    const isMobile = () => window.innerWidth <= MOBILE_BREAKPOINT;

    const setToggleExpanded = (expanded) => {
        toggleButton?.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    };

    const applyCollapsedState = (collapsed) => {
        if (collapseTimer) {
            window.clearTimeout(collapseTimer);
            collapseTimer = null;
        }

        if (!collapsed || isMobile()) {
            sidebar.classList.remove('collapsing');
            sidebar.classList.remove('collapsed');
            collapseButton?.setAttribute('aria-pressed', 'false');
            return;
        }

        sidebar.classList.add('collapsing');
        sidebar.classList.remove('collapsed');
        collapseButton?.setAttribute('aria-pressed', 'true');
        collapseTimer = window.setTimeout(() => {
            if (!isMobile()) {
                sidebar.classList.remove('collapsing');
                sidebar.classList.add('collapsed');
            }
            collapseTimer = null;
        }, 200);
    };

    const readStoredCollapsed = () => {
        try {
            return localStorage.getItem(STORAGE_KEY) === '1';
        } catch (error) {
            console.warn('Unable to access localStorage for admin sidebar state.', error);
            return false;
        }
    };

    const storeCollapsed = (collapsed) => {
        try {
            localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
        } catch (error) {
            console.warn('Unable to persist admin sidebar state.', error);
        }
    };

    const openMobileSidebar = () => {
        if (!isMobile()) return;
        body.classList.add('admin-mobile-nav-open');
        backdrop?.classList.add('visible');
        setToggleExpanded(true);
    };

    const closeMobileSidebar = () => {
        body.classList.remove('admin-mobile-nav-open');
        backdrop?.classList.remove('visible');
        setToggleExpanded(false);
    };

    const toggleMobileSidebar = () => {
        if (body.classList.contains('admin-mobile-nav-open')) {
            closeMobileSidebar();
        } else {
            openMobileSidebar();
        }
    };

    const handleResize = () => {
        if (resizeTimer) {
            window.clearTimeout(resizeTimer);
        }
        resizeTimer = window.setTimeout(() => {
            if (!isMobile()) {
                closeMobileSidebar();
            }
        }, 100);
    };

    collapseButton?.addEventListener('click', () => {
        const collapsed = !sidebar.classList.contains('collapsed');
        applyCollapsedState(collapsed);
        storeCollapsed(collapsed);
    });

    toggleButton?.addEventListener('click', toggleMobileSidebar);
    closeButton?.addEventListener('click', closeMobileSidebar);
    backdrop?.addEventListener('click', closeMobileSidebar);

    sidebar.addEventListener('click', (event) => {
        if (!isMobile()) return;
        const navItem = event.target.closest('.admin-nav-item');
        if (navItem) {
            window.setTimeout(() => {
                if (isMobile()) {
                    closeMobileSidebar();
                }
            }, 50);
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && body.classList.contains('admin-mobile-nav-open')) {
            closeMobileSidebar();
        }
    });

    window.addEventListener('resize', handleResize);

    if (!isMobile()) {
        applyCollapsedState(readStoredCollapsed());
    }
    closeMobileSidebar();
})();
