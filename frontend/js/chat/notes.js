/**
 * Notes Workspace Module entry point.
 *
 * Load after the scripts in frontend/js/chat/notes/ in this order:
 * state.js, api.js, dom.js, render.js, manager.js, manager-lifecycle.js,
 * manager-history.js, sidebar.js.
 */

// ============================================================================
// Global Functions
// ============================================================================

function showNotesWorkspace() {
    NotesManager.show();
}

// ============================================================================
// Initialization & Workspace Integration
// ============================================================================

const initializeNotesModule = () => {
    // Check for shared link on page load
    NotesManager.checkForSharedLink();
    
    // Integrate with WorkspaceManager to handle tab switching
    if (typeof WorkspaceManager !== 'undefined') {
        const originalSwitchToTab = WorkspaceManager.switchToTab.bind(WorkspaceManager);
        const handleTabChange = (tabId) => {
            if (tabId === 'notes') {
                NotesManager.show();
            } else {
                NotesManager.hide();
            }
        };

        WorkspaceManager.switchToTab = function(tabId) {
            originalSwitchToTab(tabId);
            if (WorkspaceManager.getActiveTab?.() === tabId) handleTabChange(tabId);
        };
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeNotesModule);
} else {
    initializeNotesModule();
}

// Expose to window for external access
if (typeof window !== 'undefined') {
    window.NotesManager = NotesManager;
    window.NotesToolSidebar = NotesToolSidebar;
    window.showNotesWorkspace = showNotesWorkspace;
}
