// fertig

// ------------------------------------------------------------
// Sidebar Button Visibility
// ------------------------------------------------------------
const SIDEBAR_VISIBILITY_STORAGE_KEY = 'sidebar_button_visibility';

function sidebarT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function saveSidebarVisibilityToStorage(visibility) {
    try {
        localStorage.setItem(SIDEBAR_VISIBILITY_STORAGE_KEY, JSON.stringify(visibility));
    } catch (error) {
        console.error('Error saving sidebar visibility to localStorage:', error);
    }
}

function getSidebarVisibilityFromStorage() {
    try {
        const stored = localStorage.getItem(SIDEBAR_VISIBILITY_STORAGE_KEY);
        if (stored) {
            return JSON.parse(stored);
        }
    } catch (error) {
        console.error('Error reading sidebar visibility from localStorage:', error);
    }
    return null;
}

function coerceSidebarPolicyFlag(value) {
    if (value === true) return true;
    if (value === false || value === null || value === undefined) return false;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'y', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'n', 'off', ''].includes(normalized)) return false;
    }
    return false;
}

function isSidebarButtonAllowedByGroupPolicy(key) {
    // The sidebar preference is a user-level display choice; group feature
    // flags are stronger and must keep gated features hidden even when the
    // cached or saved user preference says the button should be visible.
    if (key === 'automations') {
        return typeof window !== 'undefined' && coerceSidebarPolicyFlag(window.enableAutomationsFeature);
    }
    if (key === 'projects') {
        return typeof window !== 'undefined' && coerceSidebarPolicyFlag(window.enableProjectsFeature);
    }
    return true;
}

function isSidebarButtonVisible(key, visibility) {
    return visibility[key] !== false && isSidebarButtonAllowedByGroupPolicy(key);
}

function applyVisibilityToDOM(visibility) {
    if (typeof window.ChatSidebarMid?.setUserVisibility === 'function') {
        window.ChatSidebarMid.setUserVisibility(visibility);
        return;
    }

    // Default all buttons to visible if not specified
    const defaults = {
        create_chat: true,
        search_chats: true,
        workspace: true,
        automations: true,
        projects: true
    };
    
    // Apply defaults for any missing keys
    const finalVisibility = { ...defaults, ...visibility };
    
    // Map button keys to their container IDs
    const buttonMappings = {
        create_chat: { container: null, button: 'sidebarCreateChat' },
        search_chats: { container: null, button: 'sidebarChatsSearch' },
        workspace: { container: null, button: 'sidebarWorkspace' },
        automations: { container: 'sidebarAutomationsContainer', button: 'sidebarAutomations' },
        projects: { container: 'sidebarProjects', button: null }
    };

    Object.keys(buttonMappings).forEach(key => {
        const mapping = buttonMappings[key];
        if (!mapping) return;

        const isVisible = isSidebarButtonVisible(key, finalVisibility);
        
        if (mapping.container) {
            const container = document.getElementById(mapping.container);
            if (container) {
                // Use data attribute for automations and projects (CSS will handle hiding)
                if (key === 'automations' || key === 'projects') {
                    container.setAttribute('data-sidebar-hidden', isVisible ? 'false' : 'true');
                } else {
                    container.style.setProperty('display', isVisible ? '' : 'none', 'important');
                }
            }
        }
        
        if (mapping.button) {
            const button = document.getElementById(mapping.button);
            if (button) {
                const parentElement = button.closest('.sidebar-element');
                if (parentElement) {
                    parentElement.style.setProperty('display', isVisible ? '' : 'none', 'important');
                }
            }
        }
    });
}

async function applySidebarButtonVisibility(visibilityOverride = null) {
    try {
        let sidebarButtonVisibility = visibilityOverride;
        
        if (!sidebarButtonVisibility) {
            const data = typeof window.getCachedUserSettingsInit === 'function'
                ? await window.getCachedUserSettingsInit()
                : await (async () => {
                    const response = typeof window.authedFetch === 'function'
                        ? await window.authedFetch('/api/v1/users/user-settings/init')
                        : await fetch('/api/v1/users/user-settings/init');
                    if (!response.ok) {
                        console.error('Failed to fetch user settings for sidebar button visibility');
                        return null;
                    }
                    return response.json();
                })();
            if (!data) return;
            const chatSettings = data.chat || {};
            sidebarButtonVisibility = chatSettings.sidebar_button_visibility || {};
        }
        
        // Apply to DOM
        applyVisibilityToDOM(sidebarButtonVisibility);
        
        // Save to localStorage for next page load
        saveSidebarVisibilityToStorage(sidebarButtonVisibility);
    } catch (error) {
        console.error('Error applying sidebar button visibility:', error);
    }
}

// Apply sidebar button visibility immediately from localStorage to prevent flash
function applySidebarVisibilityFromCache() {
    const cached = getSidebarVisibilityFromStorage();
    if (cached) {
        applyVisibilityToDOM(cached);
    }
}

// Apply cached visibility immediately (before DOM is fully loaded)
applySidebarVisibilityFromCache();

// Force hide automations and projects immediately if cached says so
function forceHideAutomationsAndProjects() {
    const cached = getSidebarVisibilityFromStorage();
    if (cached) {
        if (typeof window.ChatSidebarMid?.setUserVisibility === 'function') {
            window.ChatSidebarMid.setUserVisibility(cached);
            return;
        }

        const automationsContainer = document.getElementById('sidebarAutomationsContainer');
        const projectsContainer = document.getElementById('sidebarProjects');
        
        if (automationsContainer) {
            automationsContainer.setAttribute(
                'data-sidebar-hidden',
                isSidebarButtonVisible('automations', cached) ? 'false' : 'true'
            );
        }
        if (projectsContainer) {
            projectsContainer.setAttribute(
                'data-sidebar-hidden',
                isSidebarButtonVisible('projects', cached) ? 'false' : 'true'
            );
        }
    }
}

// Apply immediately to reduce flash when elements already exist
forceHideAutomationsAndProjects();

// Re-apply after DOM is ready to handle cases where elements weren't available yet
document.addEventListener('DOMContentLoaded', () => {
    applySidebarVisibilityFromCache();
    forceHideAutomationsAndProjects();
});

// Set up a MutationObserver to watch for sidebar elements appearing
let sidebarElementObserver = null;
let sidebarElementObserverTimeout = null;

function setupSidebarElementObserver() {
    if (sidebarElementObserver) {
        sidebarElementObserver.disconnect();
    }
    
    sidebarElementObserver = new MutationObserver((mutations) => {
        const automationsContainer = document.getElementById('sidebarAutomationsContainer');
        const projectsContainer = document.getElementById('sidebarProjects');
        
        if (automationsContainer || projectsContainer) {
            // Elements appeared, re-apply visibility
            applySidebarVisibilityFromCache();
            forceHideAutomationsAndProjects();
            
            // Clear timeout if elements were found
            if (sidebarElementObserverTimeout) {
                clearTimeout(sidebarElementObserverTimeout);
                sidebarElementObserverTimeout = null;
            }
        }
    });
    
    // Observe the entire body for added nodes
    sidebarElementObserver.observe(document.body, {
        childList: true,
        subtree: true
    });
    
    // Stop observing after 3 seconds to avoid unnecessary overhead
    sidebarElementObserverTimeout = setTimeout(() => {
        if (sidebarElementObserver) {
            sidebarElementObserver.disconnect();
            sidebarElementObserver = null;
        }
    }, 3000);
}

// Start observing immediately
setupSidebarElementObserver();

// Watch for style changes on automations and projects containers and re-apply visibility
let visibilityObserver = null;

function setupVisibilityObserver() {
    // Disconnect existing observer if any
    if (visibilityObserver) {
        visibilityObserver.disconnect();
    }

    const automationsContainer = document.getElementById('sidebarAutomationsContainer');
    const projectsContainer = document.getElementById('sidebarProjects');

    visibilityObserver = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                const target = mutation.target;
                if (target.id === 'sidebarAutomationsContainer' || target.id === 'sidebarProjects') {
                    // Re-apply cached visibility using data attribute
                    const cached = getSidebarVisibilityFromStorage();
                    if (cached) {
                        const key = target.id === 'sidebarAutomationsContainer' ? 'automations' : 'projects';
                        const isVisible = isSidebarButtonVisible(key, cached);
                        target.setAttribute('data-sidebar-hidden', isVisible ? 'false' : 'true');
                    }
                }
            }
        });
    });

    if (automationsContainer) {
        visibilityObserver.observe(automationsContainer, { attributes: true, attributeFilter: ['style'] });
    }
    if (projectsContainer) {
        visibilityObserver.observe(projectsContainer, { attributes: true, attributeFilter: ['style'] });
    }
}

// Single initialization function
function initializeSidebarVisibility() {
    forceHideAutomationsAndProjects();
    const setupVisibility = typeof window !== 'undefined'
        && window.chatSetup
        && typeof window.chatSetup.sidebar_button_visibility === 'object'
        ? window.chatSetup.sidebar_button_visibility
        : null;
    if (setupVisibility) {
        applySidebarButtonVisibility(setupVisibility);
    } else {
        applySidebarVisibilityFromCache();
    }
    setupVisibilityObserver();
}

// Then fetch from API and update
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        // Wait for feature flag logic to run first
        setTimeout(initializeSidebarVisibility, 500);
    });
} else {
    setTimeout(initializeSidebarVisibility, 500);
}

document.addEventListener('chatSetupReady', (event) => {
    const setupVisibility = event?.detail?.sidebar_button_visibility;
    if (setupVisibility && typeof setupVisibility === 'object') {
        applySidebarButtonVisibility(setupVisibility);
    }
});

if (typeof window !== 'undefined') {
    window.applySidebarButtonVisibility = applySidebarButtonVisibility;
    window.applySidebarVisibilityFromCache = applySidebarVisibilityFromCache;
    window.isSidebarButtonAllowedByGroupPolicy = isSidebarButtonAllowedByGroupPolicy;
}

// ------------------------------------------------------------
// Overlay-mode detection based on .main-container width
// ------------------------------------------------------------
const SIDEBAR_OVERLAY_THRESHOLD = 750; // px – chat-area width below which sidebar overlays
const SIDEBAR_DESKTOP_WIDTH = 250;    // px – width the sidebar takes in desktop mode
const SIDEBAR_OPEN_STATE_STORAGE_KEY = 'omlorix.sidebar.openState';
const SIDEBAR_OPEN_STATE_OPEN = 'open';
const SIDEBAR_OPEN_STATE_CLOSED = 'closed';
// Artifact previews temporarily need the chat's horizontal space, but that
// layout constraint must stay separate from the user's persisted preference.
// A Set lets previews hand off directly (for example Notes -> Canvas) without
// briefly restoring the main sidebar between the two panels.
const _sidebarAutoCollapseSources = new Set();
// Some split-pane experiences need the main navigation to behave like the
// responsive overlay even when the browser itself is wide. Keep those
// temporary requests separate from the user's saved open/closed preference.
const _sidebarCompactLayoutSources = new Set();

function saveSidebarOpenState(isOpen) {
    try {
        localStorage.setItem(
            SIDEBAR_OPEN_STATE_STORAGE_KEY,
            isOpen ? SIDEBAR_OPEN_STATE_OPEN : SIDEBAR_OPEN_STATE_CLOSED
        );
    } catch (error) {
        console.warn('Unable to save sidebar open state to localStorage:', error);
    }
}

function getSavedSidebarOpenState() {
    try {
        const stored = localStorage.getItem(SIDEBAR_OPEN_STATE_STORAGE_KEY);
        if (stored === SIDEBAR_OPEN_STATE_OPEN || stored === SIDEBAR_OPEN_STATE_CLOSED) {
            return stored;
        }
    } catch (error) {
        console.warn('Unable to read sidebar open state from localStorage:', error);
    }
    return null;
}

function applySavedSidebarOpenState({ fallbackState = null, deferDesktopCollapse = false } = {}) {
    const savedState = getSavedSidebarOpenState() || fallbackState;
    if (savedState === SIDEBAR_OPEN_STATE_OPEN) {
        openSidebar({ persist: false });
    } else if (savedState === SIDEBAR_OPEN_STATE_CLOSED) {
        closeSidebar({ persist: false, defer: deferDesktopCollapse });
    }
}

/**
 * Apply the sidebar state that is currently valid for the layout.
 *
 * Artifact previews take precedence while they are visible. Once the final
 * preview releases its request, the user's persisted open state becomes the
 * effective state again.
 */
function applyEffectiveSidebarOpenState({ fallbackState = null, deferDesktopCollapse = false } = {}) {
    if (_sidebarAutoCollapseSources.size > 0) {
        closeSidebar({ persist: false, defer: deferDesktopCollapse });
        return;
    }

    applySavedSidebarOpenState({ fallbackState, deferDesktopCollapse });
}

/**
 * Temporarily reserve the main sidebar's space for a named artifact preview.
 *
 * The request never changes localStorage. On desktop, releasing the final
 * request immediately restores the saved state. In overlay mode restoration
 * is left to the user or the desktop-mode transition so a saved "open" state
 * cannot unexpectedly slide an overlay over the chat.
 */
function setMainSidebarAutoCollapsed(source, shouldCollapse) {
    const normalizedSource = String(source || '').trim();
    if (!normalizedSource) {
        return;
    }

    if (shouldCollapse) {
        _sidebarAutoCollapseSources.add(normalizedSource);
        closeSidebar({ persist: false });
        return;
    }

    const sourceWasRemoved = _sidebarAutoCollapseSources.delete(normalizedSource);
    if (sourceWasRemoved && _sidebarAutoCollapseSources.size === 0 && !isOverlayMode()) {
        // With no stored choice, the desktop sidebar's established default is
        // open. Supplying the fallback restores that default after the preview.
        applySavedSidebarOpenState({ fallbackState: SIDEBAR_OPEN_STATE_OPEN });
    }
}

/**
 * Close every right-hand artifact surface except the one being opened.
 *
 * Keeping this ownership handoff in one place prevents two fixed panels and
 * two body margin classes from remaining active at the same time. Callers must
 * register their sidebar-collapse request before invoking this function so a
 * direct handoff cannot briefly reopen the main navigation sidebar.
 */
function closeOtherArtifactPreviews(activeSource) {
    const source = String(activeSource || '').trim();
    const previews = [
        ['canvas-preview', () => window.canvasMarkdownWidget?.hidePreviewPanel?.()],
        ['slide-presentation-preview', () => window.slidePresentationWidget?.hidePreviewPanel?.()],
        ['notes-preview', () => window.NotesToolSidebar?.hidePreviewPanel?.()],
        ['deep-research-preview', () => window.deepResearchWidget?.closeSidebar?.({ restoreFocus: false })],
        ['skill-draft-preview', () => window.skillDraftWidget?.closeSidebar?.({ restoreFocus: false })],
        ['latex-pdf-preview', () => window.latexPdfWidget?.hidePreviewPanel?.()],
        ['files-preview', () => window.FilesPreview?.close?.()],
    ];
    previews.forEach(([previewSource, closePreview]) => {
        if (previewSource === source) return;
        try {
            closePreview();
        } catch (error) {
            console.warn(`Unable to close ${previewSource} during artifact preview handoff:`, error);
        }
    });
}

/**
 * Force the responsive overlay layout while a named split-pane experience is
 * active. Releasing the final request lets the normal width thresholds decide
 * the mode again and restores the user's persisted desktop sidebar state.
 */
function setMainSidebarCompactLayout(source, shouldUseCompactLayout) {
    const normalizedSource = String(source || '').trim();
    if (!normalizedSource) {
        return;
    }

    if (shouldUseCompactLayout) {
        _sidebarCompactLayoutSources.add(normalizedSource);
    } else {
        _sidebarCompactLayoutSources.delete(normalizedSource);
    }
    updateSidebarMode();
}

if (typeof window !== 'undefined') {
    window.setMainSidebarAutoCollapsed = setMainSidebarAutoCollapsed;
    window.setMainSidebarCompactLayout = setMainSidebarCompactLayout;
    window.closeOtherArtifactPreviews = closeOtherArtifactPreviews;
}

function getChatAreaWidth() {
    const mainContainer = document.querySelector('.main-container');
    const measuredMainWidth = mainContainer?.offsetWidth ?? 0;
    if (measuredMainWidth > 0) {
        return measuredMainWidth;
    }

    // When layout hasn’t settled yet (e.g. during initial paint) the main container
    // can temporarily report zero width. Fall back to the full chat view width to
    // avoid incorrectly forcing overlay mode, then subtract the sidebar width only
    // when we’re in desktop mode.
    const chatView = document.getElementById('chatView') || document.querySelector('.view');
    const totalWidth = chatView?.offsetWidth || window.innerWidth;

    if (isOverlayMode()) {
        return totalWidth;
    }

    const sidebar = document.querySelector('.sidebar-container');
    const sidebarWidth = sidebar?.offsetWidth || SIDEBAR_DESKTOP_WIDTH;
    return Math.max(totalWidth - sidebarWidth, 0);
}

function isOverlayMode() {
    return document.body.classList.contains('sidebar-overlay-mode');
}

let _desktopSidebarCollapsedTimeoutId = null;

/**
 * Run a responsive-mode update without animating between the overlay's forced
 * 250px width and the desktop sidebar width. Both states are internally valid,
 * but animating between them exposes the hidden overlay width as a visual flash.
 */
function runWithoutSidebarSizeTransition(sidebar, callback) {
    if (!sidebar) {
        callback();
        return;
    }

    const previousTransition = sidebar.style.transition;
    sidebar.style.transition = 'none';
    try {
        callback();
        // Commit the final width while transitions are disabled. Restoring the
        // CSS transition afterward keeps normal user-triggered animation intact.
        void sidebar.offsetHeight;
    } finally {
        sidebar.style.transition = previousTransition;
    }
}

function setDesktopSidebarCollapsedState(isCollapsed, { defer = false } = {}) {
    const sidebar = document.querySelector('.sidebar-container');
    if (!sidebar) {
        return;
    }

    if (_desktopSidebarCollapsedTimeoutId) {
        window.clearTimeout(_desktopSidebarCollapsedTimeoutId);
        _desktopSidebarCollapsedTimeoutId = null;
    }

    if (!isCollapsed || isOverlayMode()) {
        sidebar.classList.remove('sidebar-collapsing');
        sidebar.classList.remove('sidebar-collapsed');
        return;
    }

    if (defer) {
        sidebar.classList.add('sidebar-collapsing');
        sidebar.classList.remove('sidebar-collapsed');
        _desktopSidebarCollapsedTimeoutId = window.setTimeout(() => {
            if (!isOverlayMode()) {
                sidebar.classList.remove('sidebar-collapsing');
                sidebar.classList.add('sidebar-collapsed');
            }
            _desktopSidebarCollapsedTimeoutId = null;
        }, 300);
        return;
    }

    sidebar.classList.remove('sidebar-collapsing');
    sidebar.classList.add('sidebar-collapsed');
}

let _sidebarModeTransitioning = false;
let _sidebarModePending = false;
let _sidebarModeDebounceId = null;

function updateSidebarMode() {
    if (_sidebarModeTransitioning) {
        _sidebarModePending = true;
        return;
    }

    const chatWidth = getChatAreaWidth();
    const wasOverlay = isOverlayMode();
    const compactLayoutRequested = _sidebarCompactLayoutSources.size > 0;

    // Hysteresis to prevent flashing:
    // - Enter overlay when chat area ≤ threshold.
    // - Exit overlay only when chat area is wide enough that, after the sidebar
    //   reclaims its 250px in desktop mode, the remaining space still exceeds
    //   the threshold.
    let shouldOverlay;
    if (compactLayoutRequested) {
        shouldOverlay = true;
    } else if (wasOverlay) {
        shouldOverlay = chatWidth <= SIDEBAR_OVERLAY_THRESHOLD + SIDEBAR_DESKTOP_WIDTH;
    } else {
        shouldOverlay = chatWidth <= SIDEBAR_OVERLAY_THRESHOLD;
    }

    if (shouldOverlay === wasOverlay) return;

    // Lock out further calls while the layout settles after the mode switch
    _sidebarModeTransitioning = true;

    if (shouldOverlay) {
        // Entering overlay mode – clean up desktop inline styles
        const sidebar = document.querySelector('.sidebar-container');
        runWithoutSidebarSizeTransition(sidebar, () => {
            if (sidebar) {
                sidebar.style.minWidth = '';
                sidebar.style.maxWidth = '';
                sidebar.style.width = '';
                sidebar.style.cursor = '';
            }
            setDesktopSidebarCollapsedState(false);
            document.body.classList.add('sidebar-overlay-mode');
            applyEffectiveSidebarOpenState({ fallbackState: SIDEBAR_OPEN_STATE_CLOSED });
        });
    } else {
        const sidebar = document.querySelector('.sidebar-container');
        runWithoutSidebarSizeTransition(sidebar, () => {
            // Leaving overlay mode exposes the sidebar to the desktop flex
            // layout again. Apply the final saved width in the same unanimated
            // layout transaction so the forced 250px overlay width never paints.
            document.body.classList.remove('sidebar-open');
            document.body.classList.remove('sidebar-locked');
            const backdrop = document.getElementById('sidebarBackdrop');
            if (backdrop) {
                backdrop.classList.remove('is-visible');
                backdrop.setAttribute('aria-hidden', 'true');
            }
            document.body.classList.remove('sidebar-overlay-mode');
            applyEffectiveSidebarOpenState({ fallbackState: SIDEBAR_OPEN_STATE_OPEN });
        });
    }

    // Unlock after the CSS transition completes (matches sidebar transition duration)
    setTimeout(() => {
        _sidebarModeTransitioning = false;
        if (_sidebarModePending) {
            _sidebarModePending = false;
            updateSidebarMode();
        }
    }, 350);
}

// Observe .main-container size changes (split-screen, right-sidebar, etc.)
const _mainContainerForObserver = document.querySelector('.main-container');
if (_mainContainerForObserver) {
    const _sidebarResizeObserver = new ResizeObserver(() => {
        // Debounce: collapse rapid successive resize events into one check
        cancelAnimationFrame(_sidebarModeDebounceId);
        _sidebarModeDebounceId = requestAnimationFrame(() => {
            updateSidebarMode();
        });
    });
    _sidebarResizeObserver.observe(_mainContainerForObserver);
}
// Run initial check after DOM is fully ready and layout is computed
function initializeSidebarOpenState() {
    updateSidebarMode();
    applyEffectiveSidebarOpenState();
}

if (document.readyState === 'complete') {
    // Layout already computed, run after a frame to ensure dimensions are final
    requestAnimationFrame(() => initializeSidebarOpenState());
} else {
    // Wait for load event when layout is fully computed
    window.addEventListener('load', () => {
        requestAnimationFrame(() => initializeSidebarOpenState());
    }, { once: true });
}

// The open and close functions are now responsive.
function closeSidebar(options = {}) {
    if (options?.persist !== false) {
        saveSidebarOpenState(false);
    }

    if (isOverlayMode()) {
        // Overlay close logic
        document.body.classList.remove("sidebar-open");
        document.body.classList.remove("sidebar-locked");

        const backdrop = document.getElementById("sidebarBackdrop");
        if (backdrop) {
            backdrop.classList.remove("is-visible");
            backdrop.setAttribute("aria-hidden", "true");
        }
    } else {
        // Desktop close logic
        setDesktopSidebarCollapsedState(true, { defer: options?.defer !== false });
        const sidebar = document.querySelector(".sidebar-container");
        const sidebarHeaderRight = document.getElementById("sidebarHeaderCloseButton");
        if (sidebarHeaderRight) sidebarHeaderRight.style.display = "none";
        const sidebarSectionButton = document.querySelector(".sidebar-section-button");
        if (sidebarSectionButton) sidebarSectionButton.style.display = "block";
        const sidebarMain = document.querySelector(".sidebar-main");
        if (sidebarMain) sidebarMain.style.display = "none";
        if (sidebar) {
            sidebar.style.minWidth = "50px";
            sidebar.style.maxWidth = "50px";
            sidebar.style.width = "50px";
            sidebar.style.overflow = "hidden";
        }
        const sidebarHeaderLeftButton = document.getElementById("sidebarHeaderLogoButton");
        if (sidebarHeaderLeftButton) {
            sidebarHeaderLeftButton.addEventListener("click", openSidebar);
            sidebarHeaderLeftButton.style.cursor = "pointer";
        }
    }
}

function openSidebar(options = {}) {
    if (options?.persist !== false) {
        saveSidebarOpenState(true);
    }

    const sidebarContainer = document.querySelector('.sidebar-container');
    if (sidebarContainer) {
        sidebarContainer.scrollTop = 0;
    }

    if (isOverlayMode()) {
        setDesktopSidebarCollapsedState(false);
        // Overlay open logic
        document.body.classList.add("sidebar-open");
        document.body.classList.add("sidebar-locked");
        const backdrop = document.getElementById("sidebarBackdrop");
        if (backdrop) {
            backdrop.classList.add("is-visible");
            backdrop.setAttribute("aria-hidden", "false");
        }
        // Ensure sidebar content is visible, as it might be hidden by desktop-collapsed state
        const sidebarHeaderCloseButton = document.getElementById("sidebarHeaderCloseButton");
        if (sidebarHeaderCloseButton) sidebarHeaderCloseButton.style.display = "flex";
        const sidebarSectionButton = document.querySelector(".sidebar-section-button");
        if (sidebarSectionButton) sidebarSectionButton.style.display = "none";
        const sidebarMain = document.querySelector(".sidebar-main");
        if (sidebarMain) sidebarMain.style.display = "block";
        const sidebar = document.querySelector(".sidebar-container");
        if (sidebar) sidebar.style.overflowY = "auto";
    } else {
        // Desktop open logic
        setDesktopSidebarCollapsedState(false);
        const sidebar = document.querySelector(".sidebar-container");
        const sidebarHeaderLeft = document.getElementById("sidebarHeaderLogoButton");
        if (sidebarHeaderLeft) sidebarHeaderLeft.removeEventListener("click", openSidebar);
        const sidebarHeaderRight = document.getElementById("sidebarHeaderCloseButton");
        if (sidebarHeaderRight) sidebarHeaderRight.style.display = "flex";
        const sidebarSectionButton = document.querySelector(".sidebar-section-button");
        if (sidebarSectionButton) sidebarSectionButton.style.display = "none";
        const sidebarMain = document.querySelector(".sidebar-main");
        if (sidebarMain) sidebarMain.style.display = "block";
        if (sidebar) {
            sidebar.style.minWidth = "250px";
            sidebar.style.maxWidth = "250px";
            sidebar.style.width = "250px";
            sidebar.style.overflowY = "auto";
        }
        const sidebarHeaderLeftButton = document.getElementById("sidebarHeaderLogoButton");
        if (sidebarHeaderLeftButton) sidebarHeaderLeftButton.style.cursor = "pointer";
    }
}

// Attach listener to the close button inside the sidebar
const sidebarClose = document.getElementById("sidebarHeaderCloseButton");
sidebarClose?.addEventListener("click", closeSidebar);

// Attach listener to the toggle button in the main header for mobile/tablet
const mainHeaderToggle = document.getElementById("mainContainerSidebarCloseButton");
if(mainHeaderToggle) {
    mainHeaderToggle.addEventListener("click", openSidebar);
}

// Workspace owns a dedicated header toggle while the sidebar is in overlay mode.
const workspaceMainSidebarToggles = document.querySelectorAll(".workspace-main-sidebar-toggle");
workspaceMainSidebarToggles.forEach((toggle) => {
    toggle.addEventListener("click", openSidebar);
});

// Attach listener to backdrop for closing
const sidebarBackdrop = document.getElementById("sidebarBackdrop");
if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener("click", closeSidebar);
}

// Close sidebar in overlay mode when a button inside the mid section is activated
const sidebarMid = document.querySelector('.sidebar-mid');
if (sidebarMid) {
    sidebarMid.addEventListener('click', (event) => {
        if (!isOverlayMode()) {
            return;
        }

        const targetButton = event.target.closest('.sidebar-element-button');
        if (!targetButton) {
            return;
        }

        // Allow the button's own handler to run before closing
        window.setTimeout(() => {
            if (document.body.classList.contains('sidebar-open')) {
                closeSidebar();
            }
        }, 0);
    });
}

// Add event listener to close sidebar when clicking outside in overlay mode
document.addEventListener('click', (event) => {
    if (isOverlayMode() && document.body.classList.contains('sidebar-open')) {
        const sidebar = document.querySelector('.sidebar-container');
        const clickedInsideSidebar = sidebar?.contains(event.target);
        const clickedToggle = mainHeaderToggle?.contains(event.target);
        const clickedWorkspaceToggle = event.target.closest('.workspace-main-sidebar-toggle');
        const clickedBackdrop = event.target === sidebarBackdrop;
        const clickedProfileDropdown = event.target.closest('.sidebar-profile-dropdown');

        // Check if the click is outside the sidebar and not on elements that should keep it open
        if (!clickedInsideSidebar && !clickedToggle && !clickedWorkspaceToggle && !clickedBackdrop && !clickedProfileDropdown) {
            closeSidebar();
        }
    }
});

// Handle window resizing – updateSidebarMode handles the overlay transition via ResizeObserver,
// but we still listen to window resize for edge cases (e.g. browser window itself changes).
window.addEventListener('resize', () => {
    updateSidebarMode();
});


// ------------------------------------------------------------
// Sidebar Profile Dropdown
// ------------------------------------------------------------
// Grab elements
const profileButton = document.querySelector(".sidebar-profile-button");
const profileDropdown = document.getElementById('sidebarProfileDropdown');
let accountSummary = null;
let accountList = null;
let addAccountButtonLabel = null;
let accountManager = null;
let accountManagerOpen = false;
let accountPayload = null;

// This is the single source of truth for ordinary profile-menu entries. Keeping
// permission requirements beside each item prevents rendering and click behavior
// from developing separate, contradictory admin checks.
const SIDEBAR_PROFILE_ACTIONS = Object.freeze([
    {
        action: 'settings',
        id: 'openUserSettingsButton',
        translationKey: 'sidebar_profile_settings',
        fallback: 'Settings',
        icon: 'settings',
        shortcut: true,
        shortcutAction: 'settings.toggle',
    },
    {
        action: 'admin-settings',
        id: 'openAdminSettingsButton',
        translationKey: 'sidebar_profile_admin_settings',
        fallback: 'Admin Settings',
        icon: 'security',
        adminOnly: true,
    },
    {
        action: 'archived-chats',
        id: 'sidebarArchivedChats',
        translationKey: 'sidebar_profile_archived_chats',
        fallback: 'Archived Chats',
        icon: 'archive',
    },
    {
        action: 'logout',
        id: 'sidebarLogout',
        translationKey: 'sidebar_profile_logout',
        fallback: 'Logout',
        icon: 'logout',
        danger: true,
    },
]);

function sidebarT(key, fallback) {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function isCurrentUserAdmin() {
    try {
        return normalizeAdminFlag(localStorage.getItem('is_admin'));
    } catch (error) {
        return false;
    }
}

function normalizeAdminFlag(value) {
    if (typeof value === 'boolean') {
        return value;
    }
    if (typeof value === 'number') {
        return value === 1;
    }
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (normalized === 'true' || normalized === '1' || normalized === 'admin') {
            return true;
        }
        if (normalized === 'false' || normalized === '0' || normalized === 'user' || normalized === '') {
            return false;
        }
    }
    return false;
}

/**
 * Read one shared icon from icons.js. All values are application-owned SVG
 * strings; returning an empty string keeps the menu usable if icons fail to load.
 */
function getSidebarProfileIcon(iconName) {
    if (typeof Icons === 'undefined' || typeof Icons[iconName] !== 'string') {
        return '';
    }
    return Icons[iconName];
}

/**
 * Render a translated action button from the central menu definition.
 */
function renderSidebarProfileAction(action) {
    const classNames = ['sidebar-profile-dropdown-button'];
    if (action.danger) {
        classNames.push('sidebar-profile-dropdown-button-red');
    }
    const shortcut = action.shortcut
        ? '<span class="sidebar-element-shorcut" data-shortcut-key="," data-shortcut-modifiers="shift"></span>'
        : '';
    const shortcutAction = action.shortcutAction
        ? ` data-shortcut-action="${action.shortcutAction}"`
        : '';
    const label = escapeAccountText(sidebarT(action.translationKey, action.fallback));

    return `
        <button type="button" class="${classNames.join(' ')}" id="${action.id}" data-sidebar-profile-action="${action.action}"${shortcutAction}>
            <span class="sidebar-profile-dropdown-icon" aria-hidden="true">${getSidebarProfileIcon(action.icon)}</span>
            <p data-i18n="${action.translationKey}">${label}</p>
            ${shortcut}
        </button>
    `;
}

/**
 * Build the complete dropdown from JavaScript and include privileged actions
 * only when the same normalized authentication flag says they are available.
 */
function renderSidebarProfileDropdown(isAdmin = isCurrentUserAdmin()) {
    if (!profileDropdown) return;

    const canAccessAdminSettings = normalizeAdminFlag(isAdmin);
    const actionsMarkup = SIDEBAR_PROFILE_ACTIONS
        .filter((action) => !action.adminOnly || canAccessAdminSettings)
        .map(renderSidebarProfileAction)
        .join('');
    const addAccountLabel = escapeAccountText(sidebarT('sidebar_account_add', 'Add account'));

    profileDropdown.innerHTML = `
        <div class="sidebar-profile-accounts" id="sidebarProfileAccounts">
            <div class="sidebar-account-summary" id="sidebarAccountSummary"></div>
            <div class="sidebar-account-list" id="sidebarAccountList"></div>
            <button type="button" class="sidebar-profile-dropdown-button sidebar-account-add-button" id="sidebarAddAccountButton" data-sidebar-profile-action="add-account">
                <span class="sidebar-profile-dropdown-icon" aria-hidden="true">${getSidebarProfileIcon('plus')}</span>
                <p id="sidebarAddAccountButtonLabel" data-i18n="sidebar_account_add">${addAccountLabel}</p>
            </button>
            <div class="sidebar-account-manager" id="sidebarAccountManager" hidden></div>
            <div class="sidebar-profile-dropdown-divider"></div>
        </div>
        ${actionsMarkup}
    `;

    // A full render replaces descendants, so refresh the small set of cached
    // account containers before restoring the current account state.
    accountSummary = profileDropdown.querySelector('#sidebarAccountSummary');
    accountList = profileDropdown.querySelector('#sidebarAccountList');
    addAccountButtonLabel = profileDropdown.querySelector('#sidebarAddAccountButtonLabel');
    accountManager = profileDropdown.querySelector('#sidebarAccountManager');
    profileDropdown.dataset.adminMenu = canAccessAdminSettings ? 'true' : 'false';

    // The shortcut manager initializes after sidebar.js on first load. On later
    // permission-driven renders, refresh its platform-specific accessibility
    // metadata for the newly created Settings button.
    window.ChatShortcutManager?.refresh?.();

    if (accountPayload) {
        accountControlsRenderKey = '';
        renderAccountControls(accountPayload, { force: true });
    }
}

function escapeAccountText(value) {
    if (typeof window.escapeHtml === 'function') {
        return window.escapeHtml(value);
    }
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getAccountInitials(account) {
    const source = String(formatAccountLabel(account) || 'A').trim();
    const parts = source.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
        return `${parts[0][0] || ''}${parts[1][0] || ''}`.toUpperCase();
    }
    return source.slice(0, 2).toUpperCase();
}

let accountAvatarCacheBust = Date.now();
let accountControlsRenderKey = '';
let accountAvatarStateKey = '';
const accountAvatarBlobFetches = new Map();
const accountAvatarBlobCache = new Map();

function bumpAccountAvatarCacheBust() {
    // Keep avatar URLs monotonic even when several renders happen within one
    // millisecond, so a real avatar refresh cannot accidentally reuse a URL.
    accountAvatarCacheBust = Math.max(Date.now(), accountAvatarCacheBust + 1);
}

function getAccountProfilePictureUrl(account) {
    const slot = Number(account?.slot);
    if (!account?.has_profile_picture || !Number.isInteger(slot) || slot <= 0) {
        return null;
    }
    return `/api/v1/users/profile-picture/slot/${slot}?t=${accountAvatarCacheBust}`;
}

function hasAccountProfilePicture(account) {
    const slot = Number(account?.slot);
    return Boolean(account?.has_profile_picture && Number.isInteger(slot) && slot > 0);
}

function createAccountPayloadKey(payload) {
    const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
    const normalizedAccounts = accounts.map((account) => ({
        slot: Number(account?.slot) || 0,
        active: account?.active === true,
        display_name: String(account?.display_name || ''),
        has_profile_picture: account?.has_profile_picture === true,
    })).sort((left, right) => left.slot - right.slot);

    return JSON.stringify({
        accounts: normalizedAccounts,
        active_slot: Number(payload?.active_slot) || 0,
        can_add_account: payload?.can_add_account === true,
        max_accounts: Number(payload?.max_accounts) || 0,
    });
}

function createAccountAvatarStateKey(payload) {
    const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
    return accounts.map((account) => {
        const slot = Number(account?.slot) || 0;
        return `${slot}:${account?.has_profile_picture === true ? '1' : '0'}`;
    }).sort().join('|');
}

function formatAccountLabel(account) {
    const explicitDisplayName = String(account?.display_name || '').trim();
    if (explicitDisplayName) {
        return explicitDisplayName;
    }

    const slot = Number(account?.slot);
    const safeSlot = Number.isInteger(slot) && slot > 0 ? slot : '';
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation('sidebar_account_label', 'Account {slot}', { slot: safeSlot });
    }
    return safeSlot ? `Account ${safeSlot}` : 'Account';
}

let accountAvatarObjectUrls = new Map();

function revokeAccountAvatarObjectUrls() {
    accountAvatarObjectUrls.forEach((revoke) => {
        try {
            if (typeof revoke === 'function') {
                revoke();
            }
        } catch (_error) {
            // ignore
        }
    });
    accountAvatarObjectUrls.clear();
}

function clearAccountAvatarBlobCache() {
    accountAvatarBlobFetches.clear();
    accountAvatarBlobCache.clear();
}

async function hydrateAccountAvatarImages(root = document) {
    if (!root || typeof window.authedFetch !== 'function') {
        return;
    }

    const images = root.querySelectorAll('img[data-authed-src]');
    if (!images?.length) {
        return;
    }

    await Promise.all(Array.from(images).map(async (img) => {
        const authedSrc = img.getAttribute('data-authed-src');
        if (!authedSrc) {
            return;
        }

        try {
            const { url, revoke } = await fetchAccountAvatarBlobUrl(authedSrc);
            const previousRevoke = accountAvatarObjectUrls.get(img);
            if (typeof previousRevoke === 'function') {
                try { previousRevoke(); } catch (_error) {}
            }
            accountAvatarObjectUrls.set(img, revoke);
            img.src = url;
        } catch (_error) {
            // fall back to initials (onerror handler on the img will handle visibility)
        }
    }));
}

async function fetchAccountAvatarBlobUrl(authedSrc) {
    const cachedBlob = accountAvatarBlobCache.get(authedSrc);
    if (cachedBlob) {
        const url = URL.createObjectURL(cachedBlob);
        return {
            url,
            revoke: () => URL.revokeObjectURL(url),
        };
    }

    let blobPromise = accountAvatarBlobFetches.get(authedSrc);
    if (!blobPromise) {
        // The same account avatar can be present in multiple account surfaces
        // during one render. Share the protected fetch, but create a separate
        // object URL per image so each element can be revoked independently.
        blobPromise = window.authedFetch(authedSrc, {
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-cache',
            },
        }).then(async (response) => {
            if (!response?.ok) {
                throw new Error(`Failed to fetch account avatar (${response?.status || 'no-response'})`);
            }
            const blob = await response.blob();
            accountAvatarBlobCache.set(authedSrc, blob);
            return blob;
        }).finally(() => {
            accountAvatarBlobFetches.delete(authedSrc);
        });
        accountAvatarBlobFetches.set(authedSrc, blobPromise);
    }

    const blob = await blobPromise;
    const url = URL.createObjectURL(blob);
    return {
        url,
        revoke: () => URL.revokeObjectURL(url),
    };
}

function renderAccountAvatar(account) {
    const pictureUrl = getAccountProfilePictureUrl(account);
    if (pictureUrl) {
        return `<img data-authed-src="${pictureUrl}" alt="" class="profile-initials-img" loading="lazy" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"><span class="profile-initials-fallback" style="display:none">${escapeAccountText(getAccountInitials(account))}</span>`;
    }
    return escapeAccountText(getAccountInitials(account));
}

function getAccountAvatarWrapperClass(account) {
    if (hasAccountProfilePicture(account)) {
        return 'profile-initials profile-initials--has-picture';
    }
    return 'profile-initials';
}

function renderAccountSummary(payload) {
    if (!accountSummary) return;
    const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
    const activeAccount = accounts.find((account) => account.active) || accounts[0];
    if (!activeAccount) {
        accountSummary.innerHTML = '';
        accountSummary.style.display = 'none';
        return;
    }
    accountSummary.style.display = '';
    accountSummary.innerHTML = `
        <div class="sidebar-account-row active">
            <div class="${getAccountAvatarWrapperClass(activeAccount)}">${renderAccountAvatar(activeAccount)}</div>
            <div class="sidebar-account-main">
                <div class="sidebar-account-summary-name">${escapeAccountText(formatAccountLabel(activeAccount))}</div>
            </div>
            <div class="sidebar-account-badge" data-i18n="sidebar_account_badge_active">${escapeAccountText(sidebarT('sidebar_account_badge_active', 'Active'))}</div>
        </div>
    `;
    void hydrateAccountAvatarImages(accountSummary);
}

function renderAccountList(payload) {
    if (!accountList) return;
    const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
    const secondaryAccounts = accounts.filter((account) => !account.active);
    accountList.hidden = secondaryAccounts.length === 0;
    if (secondaryAccounts.length === 0) {
        accountList.innerHTML = '';
        return;
    }
    accountList.innerHTML = secondaryAccounts.map((account) => {
        const slot = Number(account?.slot);
        if (!Number.isInteger(slot) || slot <= 0) {
            return '';
        }
        return `
        <button type="button" class="sidebar-account-row" data-account-slot="${slot}">
            <div class="${getAccountAvatarWrapperClass(account)}">${renderAccountAvatar(account)}</div>
            <div class="sidebar-account-main">
                <div class="sidebar-account-row-name">${escapeAccountText(formatAccountLabel(account))}</div>
            </div>
        </button>
    `;
    }).join('');

    void hydrateAccountAvatarImages(accountList);
}

function renderAccountManager(payload) {
    if (!accountManager) return;
    const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
    const shouldShowManager = accountManagerOpen && accounts.length > 0 && payload?.can_add_account === false;
    accountManager.hidden = !shouldShowManager;
    if (!shouldShowManager) {
        accountManager.innerHTML = '';
        return;
    }
    accountManager.innerHTML = `
        <div class="sidebar-account-manager-title" data-i18n="sidebar_account_manager_title">${escapeAccountText(sidebarT('sidebar_account_manager_title', 'Choose an account to replace or remove'))}</div>
        ${accounts.map((account) => {
            const slot = Number(account?.slot);
            if (!Number.isInteger(slot) || slot <= 0) {
                return '';
            }
            return `
            <div class="sidebar-account-manager-row" data-account-slot="${slot}">
                <div class="${getAccountAvatarWrapperClass(account)}">${renderAccountAvatar(account)}</div>
                <div class="sidebar-account-main">
                    <div class="sidebar-account-row-name">${escapeAccountText(formatAccountLabel(account))}</div>
                </div>
                <div class="sidebar-account-manager-actions">
                    <button type="button" class="sidebar-account-action" data-account-replace="${slot}" data-i18n="sidebar_account_action_replace">${escapeAccountText(sidebarT('sidebar_account_action_replace', 'Replace'))}</button>
                    <button type="button" class="sidebar-account-action sidebar-account-action--danger" data-account-remove="${slot}" data-i18n="sidebar_account_action_remove">${escapeAccountText(sidebarT('sidebar_account_action_remove', 'Remove'))}</button>
                </div>
            </div>
        `;
        }).join('')}
    `;

    void hydrateAccountAvatarImages(accountManager);
}

function renderAccountControls(payload, options = {}) {
    const { force = false, refreshAvatars = false } = options;
    const nextRenderKey = createAccountPayloadKey(payload);
    const nextAvatarStateKey = createAccountAvatarStateKey(payload);
    const avatarStateChanged = nextAvatarStateKey !== accountAvatarStateKey;

    accountPayload = payload;

    if (refreshAvatars || avatarStateChanged) {
        bumpAccountAvatarCacheBust();
        clearAccountAvatarBlobCache();
    }

    accountAvatarStateKey = nextAvatarStateKey;

    if (!force && nextRenderKey === accountControlsRenderKey) {
        return;
    }

    accountControlsRenderKey = nextRenderKey;
    revokeAccountAvatarObjectUrls();
    renderAccountSummary(payload);
    renderAccountList(payload);
    renderAccountManager(payload);
    if (addAccountButtonLabel) {
        const addAccountText = sidebarT('sidebar_account_add', 'Add account');
        const manageAccountsText = sidebarT('sidebar_account_manage', 'Manage accounts');
        addAccountButtonLabel.textContent = payload?.can_add_account === false ? manageAccountsText : addAccountText;
    }
}

async function loadProfileAccounts() {
    if (typeof window.fetchBrowserAccounts !== 'function') return;
    await window.fetchBrowserAccounts({ silent: true });
}

// The HTML contains only the stable dropdown mount point. Render its contents
// synchronously so later deferred scripts and keyboard shortcuts can find the
// established element IDs during their own initialization.
renderSidebarProfileDropdown();

// Load account data once at application startup
loadProfileAccounts().catch(() => {});

/**
 * Keep visual, focus, and assistive-technology state synchronized whenever the
 * menu opens or closes.
 */
function setProfileDropdownOpen(isOpen) {
    if (!profileDropdown) return;

    profileDropdown.classList.toggle('open', isOpen);
    profileDropdown.setAttribute('aria-hidden', String(!isOpen));
    profileDropdown.inert = !isOpen;
    profileButton?.setAttribute('aria-expanded', String(isOpen));

    if (!isOpen) {
        accountManagerOpen = false;
        renderAccountManager(accountPayload);
    }
}

// Toggle dropdown visibility when the profile button is clicked
function toggleProfileDropdown(event) {
    event.stopPropagation(); // Prevent the click from propagating to the document handler

    if (!profileDropdown) return;
    setProfileDropdownOpen(!profileDropdown.classList.contains('open'));
}

// Hide the dropdown when clicking outside of it
function hideProfileDropdown(event) {
    if (!profileDropdown) return;

    // If the click happened outside the dropdown and the profile button, hide it
    if (!profileDropdown.contains(event.target) && !profileButton.contains(event.target)) {
        setProfileDropdownOpen(false);
    }
}

// Attach event listeners (ensure they aren't duplicated)
if (profileButton) {
    profileButton.addEventListener("click", toggleProfileDropdown);
}

setProfileDropdownOpen(false);

document.addEventListener("click", hideProfileDropdown);

window.addEventListener('auth:accountsUpdated', (event) => {
    if (event?.detail) {
        renderAccountControls({
            accounts: event.detail.accounts,
            active_slot: event.detail.activeSlot,
            can_add_account: event.detail.canAddAccount,
            max_accounts: event.detail.maxAccounts,
        });
    }
});

document.addEventListener('i18n:updated', () => {
    // Rebuild static labels as well as account copy from the newly active locale.
    renderSidebarProfileDropdown(profileDropdown?.dataset.adminMenu === 'true');
});

window.addEventListener('profile-picture:changed', () => {
    accountControlsRenderKey = '';
    if (accountPayload) {
        renderAccountControls(accountPayload, { force: true, refreshAvatars: true });
        return;
    }
    bumpAccountAvatarCacheBust();
    clearAccountAvatarBlobCache();
});

window.addEventListener('auth:isAdminUpdated', (event) => {
    // Re-rendering adds or removes the admin action; there is no second hidden
    // state to synchronize and no privileged button left mounted for non-admins.
    renderSidebarProfileDropdown(event?.detail?.isAdmin);
});

// Optional: Close dropdown on ESC key
if (typeof window !== 'undefined' && window.registerEscapeHandler) {
    window.registerEscapeHandler({
        id: 'sidebar-profile-dropdown',
        priority: 60,
        isActive: () => Boolean(profileDropdown?.classList.contains('open')),
        close: () => setProfileDropdownOpen(false),
    });
} else {
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            setProfileDropdownOpen(false);
        }
    });
}

/**
 * Run an ordinary menu action from the same action names used by the renderer.
 * This delegated path survives locale and permission-driven re-renders.
 */
function runSidebarProfileAction(action) {
    setProfileDropdownOpen(false);

    if (action === 'settings') {
        if (isOverlayMode() && document.body.classList.contains('sidebar-open')) {
            closeSidebar({ persist: false });
        }
        if (typeof openUserSettings === 'function') {
            void openUserSettings();
        }
        return;
    }
    if (action === 'admin-settings') {
        // The action is rendered only for admins; the backend remains the final
        // authorization boundary when the new page validates its session.
        window.open('/admin', '_blank', 'noopener,noreferrer');
        return;
    }
    if (action === 'archived-chats') {
        // Avoid leaving the overlay sidebar and its backdrop active behind the
        // independent archived-chats modal on narrow layouts.
        if (document.body.classList.contains('sidebar-open')) {
            closeSidebar();
        }
        if (typeof openArchivedChatsModal === 'function') {
            openArchivedChatsModal();
        }
        return;
    }
    if (action === 'logout' && typeof logout === 'function') {
        void logout();
    }
}

/**
 * Handle every dynamic dropdown control from the persistent mount element.
 */
async function handleSidebarProfileDropdownClick(event) {
    const actionButton = event.target.closest('[data-sidebar-profile-action]');
    const action = actionButton?.dataset.sidebarProfileAction;

    if (action === 'add-account') {
        event.preventDefault();
        event.stopPropagation();
        if (!accountPayload) {
            try {
                await loadProfileAccounts();
            } catch (error) {
                notifyError(sidebarT('account_load_failed', 'Failed to load accounts.'));
                return;
            }
        }
        if (accountPayload?.can_add_account !== false) {
            window.startAddAccount?.();
            return;
        }
        accountManagerOpen = !accountManagerOpen;
        renderAccountManager(accountPayload);
        return;
    }

    if (action) {
        runSidebarProfileAction(action);
        return;
    }

    const replaceButton = event.target.closest('#sidebarAccountManager [data-account-replace]');
    if (replaceButton) {
        const slot = Number.parseInt(replaceButton.getAttribute('data-account-replace') || '', 10);
        if (Number.isInteger(slot)) {
            window.startAddAccount?.(slot);
        }
        return;
    }

    const removeButton = event.target.closest('#sidebarAccountManager [data-account-remove]');
    if (removeButton) {
        const slot = Number.parseInt(removeButton.getAttribute('data-account-remove') || '', 10);
        if (!Number.isInteger(slot)) return;
        const activeSlot = Number(accountPayload?.active_slot);
        try {
            const payload = await window.removeBrowserAccount(slot, { reload: slot === activeSlot });
            if (slot !== activeSlot) {
                accountPayload = payload || accountPayload;
                renderAccountControls(accountPayload);
            }
        } catch (error) {
            notifyError(sidebarT('sidebar_account_remove_failed', 'Failed to remove account.'));
        }
        return;
    }

    const accountRow = event.target.closest('#sidebarAccountList [data-account-slot]');
    if (!accountRow) return;
    const slot = Number.parseInt(accountRow.getAttribute('data-account-slot') || '', 10);
    if (!Number.isInteger(slot)) return;
    try {
        await window.switchBrowserAccount(slot);
    } catch (error) {
        notifyError(sidebarT('sidebar_account_switch_failed', 'Failed to switch account.'));
    }
}

profileDropdown?.addEventListener('click', (event) => {
    void handleSidebarProfileDropdownClick(event);
});

// ------------------------------------------------------------
// Sidebar Sections Collapse/Expand
// ------------------------------------------------------------
function toggleSidebarSectionCollapse(section) {
    section?.classList.toggle('collapsed');
}

function bindSidebarSectionCollapse(header) {
    if (!header || header.dataset.sidebarSectionCollapseBound === 'true') return;
    header.addEventListener('click', () => {
        toggleSidebarSectionCollapse(header.parentElement);
    });
    header.dataset.sidebarSectionCollapseBound = 'true';
}

(() => {
    const sectionHeaders = document.querySelectorAll('.sidebar-section-header');

    sectionHeaders.forEach((header) => {
        bindSidebarSectionCollapse(header);
    });
})();

if (typeof window !== 'undefined') {
    window.bindSidebarSectionCollapse = bindSidebarSectionCollapse;
    window.toggleSidebarSectionCollapse = toggleSidebarSectionCollapse;
}

// ------------------------------------------------------------
// Sidebar Right Border Toggle & Drag Resize
// ------------------------------------------------------------
(() => {
    const sidebar = document.querySelector('.sidebar-container');
    if (!sidebar) return;

    const BORDER_THRESHOLD = 6; // px from right edge considered clickable area
    const MIN_WIDTH = 50;       // collapsed width
    const MAX_WIDTH = 250;      // arbitrary max width

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;
    let lastWidth = 0;

    function isCollapsed() {
        return parseInt(getComputedStyle(sidebar).width, 10) <= MIN_WIDTH + 5;
    }

    // ----------------------------
    // Cursor & click toggle logic
    // ----------------------------
    function handleMouseMove(e) {
        if (isOverlayMode()) { sidebar.style.cursor = ''; return; } // Disable in overlay mode
        if (isResizing) return; // while dragging, cursor handled elsewhere
        const nearBorder = e.offsetX > sidebar.clientWidth - BORDER_THRESHOLD;
        if (nearBorder) {
            sidebar.style.cursor = isCollapsed() ? 'e-resize' : 'w-resize';
        } else {
            sidebar.style.cursor = '';
        }
    }

    function handleClick(e) {
        if (isOverlayMode()) return; // Disable in overlay mode
        if (isResizing) return; // ignore click events triggered after drag
        const nearBorder = e.offsetX > sidebar.clientWidth - BORDER_THRESHOLD;
        if (!nearBorder) return;

        if (isCollapsed()) {
            openSidebar();
        } else {
            closeSidebar();
        }
    }

    // ----------------------------
    // Drag-to-resize logic
    // ----------------------------
    function resizeSidebar(e) {
        const delta = e.clientX - startX;
        let newWidth = startWidth + delta;
        newWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, newWidth));
        lastWidth = newWidth;
        sidebar.style.width = `${newWidth}px`;
        sidebar.style.minWidth = sidebar.style.maxWidth = sidebar.style.width;
        sidebar.style.cursor = 'col-resize';
        document.body.style.cursor = 'col-resize';
    }

    function stopResize() {
        isResizing = false;
        sidebar.style.cursor = '';
        document.body.style.cursor = '';
        document.removeEventListener('mousemove', resizeSidebar);
        document.removeEventListener('mouseup', stopResize);

        // Snap to open/closed state based on final width
        if (lastWidth <= MIN_WIDTH + 5) {
            closeSidebar();
        } else if (lastWidth >= 100) {
            openSidebar();
        }
    }

    function handleMouseDown(e) {
        if (isOverlayMode()) return; // Disable in overlay mode
        const nearBorder = e.offsetX > sidebar.clientWidth - BORDER_THRESHOLD;
        if (!nearBorder) return;

        isResizing = true;
        startX = e.clientX;
        startWidth = sidebar.clientWidth;
        lastWidth = startWidth;

        document.addEventListener('mousemove', resizeSidebar);
        document.addEventListener('mouseup', stopResize);
        e.preventDefault();
    }

    // ----------------------------
    // Event listeners
    // ----------------------------
    sidebar.addEventListener('mousemove', handleMouseMove);
    sidebar.addEventListener('mouseleave', () => {
        if (!isResizing) sidebar.style.cursor = '';
    });
    sidebar.addEventListener('click', handleClick);
    sidebar.addEventListener('mousedown', handleMouseDown);
})();

// Create Chat Button
const createChatButton = document.getElementById('sidebarCreateChat');
if (createChatButton) {
    createChatButton.addEventListener('click', () => {
        if (typeof window.showChatStartContainer === 'function') {
            window.showChatStartContainer();
        }
        if (typeof window.hideProjectsContainer === 'function') {
            window.hideProjectsContainer();
        }
        if (typeof window.hideProjectSidebar === 'function') {
            window.hideProjectSidebar();
        }
    });
}

const mainHeaderCreateChatButton = document.getElementById('mainHeaderCreateChatButton');
if (mainHeaderCreateChatButton) {
    mainHeaderCreateChatButton.addEventListener('click', () => {
        if (typeof window.showChatStartContainer === 'function') {
            window.showChatStartContainer();
        }
    });
}

document.querySelector('.sidebar-section-button')?.addEventListener('click', () => {
    openSidebar();
});

// ------------------------------------------------------------
// Swipe Gesture to Open Sidebar (Overlay Mode)
// ------------------------------------------------------------
(() => {
    let touchStartX = 0;
    let touchStartY = 0;
    let touchEndX = 0;
    let touchEndY = 0;
    let isSwiping = false;
    let swipeAction = null; // 'open' or 'close'

    const SWIPE_THRESHOLD = 50; // Minimum horizontal distance for a swipe
    const EDGE_THRESHOLD_RATIO = 0.5; // Portion of screen width considered the left edge

    function getEdgeThreshold() {
        return window.innerWidth * EDGE_THRESHOLD_RATIO;
    }
    const VERTICAL_TOLERANCE = 100; // Maximum vertical movement allowed

    const sidebarElement = document.querySelector('.sidebar-container');

    function handleTouchStart(e) {
        // Only activate in overlay mode
        if (!isOverlayMode()) return;
        
        const touch = e.touches[0];
        const sidebarIsOpen = document.body.classList.contains('sidebar-open');
        const targetIsSidebar = !!touch.target.closest('.sidebar-container');

        touchStartX = touch.clientX;
        touchStartY = touch.clientY;
        touchEndX = touchStartX;
        touchEndY = touchStartY;

        if (!sidebarIsOpen && touchStartX <= getEdgeThreshold()) {
            isSwiping = true;
            swipeAction = 'open';
        } else if (sidebarIsOpen && targetIsSidebar) {
            const sidebarWidth = sidebarElement?.getBoundingClientRect().width ?? 0;
            if (touchStartX <= sidebarWidth) {
                isSwiping = true;
                swipeAction = 'close';
            }
        } else {
            isSwiping = false;
            swipeAction = null;
        }
    }

    function handleTouchMove(e) {
        if (!isSwiping || !isOverlayMode()) return;
        
        touchEndX = e.touches[0].clientX;
        touchEndY = e.touches[0].clientY;
    }

    function handleTouchEnd(e) {
        if (!isSwiping || !isOverlayMode()) return;
        
        isSwiping = false;

        const horizontalDistance = touchEndX - touchStartX;
        const verticalDistance = Math.abs(touchEndY - touchStartY);

        if (verticalDistance < VERTICAL_TOLERANCE) {
            if (
                swipeAction === 'open' &&
                horizontalDistance > SWIPE_THRESHOLD &&
                !document.body.classList.contains('sidebar-open')
            ) {
                openSidebar();
            } else if (
                swipeAction === 'close' &&
                horizontalDistance < -SWIPE_THRESHOLD &&
                document.body.classList.contains('sidebar-open')
            ) {
                closeSidebar();
            }
        }

        // Reset values
        touchStartX = 0;
        touchStartY = 0;
        touchEndX = 0;
        touchEndY = 0;
        swipeAction = null;
    }

    // Attach event listeners to document body
    document.body.addEventListener('touchstart', handleTouchStart, { passive: true });
    document.body.addEventListener('touchmove', handleTouchMove, { passive: true });
    document.body.addEventListener('touchend', handleTouchEnd, { passive: true });

    // Re-check on resize to cancel any in-progress swipe
    window.addEventListener('resize', () => {
        if (!isOverlayMode()) {
            isSwiping = false;
        }
    });
})();
