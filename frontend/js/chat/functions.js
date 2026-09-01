function initUserProfileUI(first_name, last_name, email) {
    if (typeof window !== 'undefined') {
        window.activeUserProfile = {
            first_name: first_name || '',
            last_name: last_name || '',
            email: email || '',
        };
    }

    // ---------------------------
    // Sidebar Profile Name
    // ---------------------------
    const sidebarName = document.getElementById("sidebarName");
    if (sidebarName) {
        sidebarName.textContent = `${first_name} ${last_name}`;
    }

    // ---------------------------
    // User Settings Profile Page Input Values
    // ---------------------------
    const userFirstName = document.getElementById("usUserFirstName");
    const userLastName = document.getElementById("usUserLastName");
    const userEmail = document.getElementById("usUserEmail");
    if (userFirstName) {
        userFirstName.value = first_name;
    }
    if (userLastName) {
        userLastName.value = last_name;
    }
    if (userEmail) {
        userEmail.value = email;
    }
}


function isWorkspaceTabActive(tabName) {
    if (
        typeof WorkspaceManager !== 'undefined' &&
        typeof WorkspaceManager.getActiveTab === 'function'
    ) {
        return WorkspaceManager.getActiveTab() === tabName;
    }
    if (typeof WorkspaceState !== 'undefined') {
        return WorkspaceState.activeTab === tabName;
    }
    return false;
}

function updateWorkspaceFeatureVisibility(tabName, isEnabled) {
    if (typeof window === 'undefined') return;

    const workspaceNavItems = document.querySelectorAll(`[data-workspace-tab="${tabName}"]`);
    const sectionId = `workspaceSection${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`;
    const workspaceSection = document.getElementById(sectionId);

    workspaceNavItems.forEach((item) => {
        item.style.display = isEnabled ? '' : 'none';
        item.setAttribute('aria-hidden', isEnabled ? 'false' : 'true');
        item.disabled = !isEnabled;
    });

    if (workspaceSection) {
        const shouldShow = isEnabled && isWorkspaceTabActive(tabName);
        if (shouldShow) {
            workspaceSection.style.display = '';
            workspaceSection.removeAttribute('aria-hidden');
        } else {
            workspaceSection.style.display = 'none';
            workspaceSection.setAttribute('aria-hidden', 'true');
        }
    }

    if (!isEnabled) {
        if (typeof WorkspaceState !== 'undefined' && WorkspaceState.activeTab === tabName) {
            WorkspaceState.activeTab = 'notifications';
        }
        if (
            typeof WorkspaceManager !== 'undefined' &&
            typeof WorkspaceManager.getActiveTab === 'function' &&
            WorkspaceManager.getActiveTab() === tabName
        ) {
            WorkspaceManager.setActiveTab('notifications');
            WorkspaceManager.switchToTab('notifications');
        }
    }
}


function initWorkspaceTodos(enable_todo, allow_todo_share = true) {
    const isEnabled = enable_todo === true;
    const allowShare = allow_todo_share !== false;
    updateWorkspaceFeatureVisibility('todo', isEnabled);
    if (typeof window !== 'undefined') {
        window.enableTodoFeature = isEnabled;
        window.allowTodoListShareFeature = allowShare;
    }
}


function initWorkspaceNotes(enable_notes, allow_notes_share = true) {
    const isEnabled = enable_notes === true;
    const allowShare = allow_notes_share !== false;
    updateWorkspaceFeatureVisibility('notes', isEnabled);
    if (typeof window !== 'undefined') {
        window.enableNotesFeature = isEnabled;
        window.allowNoteShareFeature = allowShare;
    }
}


function initWorkspaceMemories(enable_memories) {
    const isEnabled = enable_memories === true;
    updateWorkspaceFeatureVisibility('memories', isEnabled);
    if (typeof window !== 'undefined') {
        window.enableMemoriesFeature = isEnabled;

        // Project forms load before the group-specific chat setup response.
        // Sync their memory controls once that response establishes the policy
        // for the signed-in user's group.
        window.setProjectMemoryControlsVisibility?.(isEnabled);
    }
}


function initWorkspaceBookmarks(enable_bookmarks, allow_bookmark_share = true) {
    const isEnabled = enable_bookmarks === true;
    const allowShare = allow_bookmark_share === true;
    updateWorkspaceFeatureVisibility('bookmarks', isEnabled);
    if (typeof window !== 'undefined') {
        window.enableBookmarksFeature = isEnabled;
        window.allowBookmarkShareFeature = allowShare;
    }
}


function initWorkspaceConnections(policy = {}) {
    const normalizedPolicy = typeof policy === 'object' && policy !== null
        ? policy
        : { allow_mcp: policy === true };
    const allowPersonalMcp = normalizedPolicy.allow_mcp === true;
    const allowManagedConnections = normalizedPolicy.allow_workspace_connections === true;
    const isAllowed = allowManagedConnections || allowPersonalMcp;
    updateWorkspaceFeatureVisibility('connections', isAllowed);
    if (typeof window !== 'undefined') {
        window.enableConnectionsFeature = isAllowed;
        window.connectionsAllowed = isAllowed;
        if (typeof window.MCPSettings?.setPolicy === 'function') {
            window.MCPSettings.setPolicy({ allow_mcp: allowPersonalMcp });
        }
        if (typeof window.ConnectionsWorkspace?.setPolicy === 'function') {
            window.ConnectionsWorkspace.setPolicy(allowManagedConnections);
        }
    }
}


function initWorkspaceSkills(enable_skills, allow_skill_share = true) {
    const isEnabled = enable_skills === true;
    const allowShare = allow_skill_share !== false;
    updateWorkspaceFeatureVisibility('skills', isEnabled);
    if (typeof window !== 'undefined') {
        window.enableSkillsFeature = isEnabled;
        window.allowSkillShareFeature = allowShare;
    }
}


function initWorkspaceAgents(enable_agents = true, allow_agent_share = true) {
    const isEnabled = enable_agents === true;
    updateWorkspaceFeatureVisibility('agents', isEnabled);
    if (typeof window !== 'undefined') {
        window.enableAgentsFeature = isEnabled;
        window.allowAgentShareFeature = allow_agent_share === true;
    }
}


function initWorkspacePrompts(enable_prompts, allow_prompt_share = true) {
    const isEnabled = enable_prompts === true;
    const allowShare = allow_prompt_share !== false;
    updateWorkspaceFeatureVisibility('prompts', isEnabled);
    if (typeof window !== 'undefined') {
        window.enablePromptsFeature = isEnabled;
        window.allowPromptShareFeature = allowShare;
    }
}


function coerceFeatureEnabled(value) {
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


function initAutomationsSidebar(enable_automations) {
    const isEnabled = coerceFeatureEnabled(enable_automations);
    if (typeof window !== 'undefined') {
        window.enableAutomationsFeature = isEnabled;
    }

    if (typeof window.ChatSidebarMid?.setFeatureAvailability === 'function') {
        window.ChatSidebarMid.setFeatureAvailability('automations', isEnabled);
    }

    const automationsContainer = document.getElementById("sidebarAutomationsContainer");
    const automationsButton = document.getElementById("sidebarAutomations");
    const targetEl = automationsContainer || automationsButton;
    if (targetEl && typeof window.ChatSidebarMid?.setFeatureAvailability !== 'function') {
        targetEl.style.display = isEnabled ? "" : "none";
    }
    if (automationsContainer && typeof window.ChatSidebarMid?.setFeatureAvailability !== 'function') {
        automationsContainer.setAttribute('data-sidebar-hidden', isEnabled ? 'false' : 'true');
    }
    if (typeof window !== 'undefined' && typeof window.applySidebarVisibilityFromCache === 'function') {
        window.applySidebarVisibilityFromCache();
    }
}


function initProjectsSidebar(enable_projects, allow_project_share = true) {
    const isEnabled = coerceFeatureEnabled(enable_projects);
    if (typeof window !== 'undefined') {
        window.enableProjectsFeature = isEnabled;
        window.allowProjectShareFeature = coerceFeatureEnabled(allow_project_share);
        if (
            window.enableProjectsFeature &&
            !window._chatProjectsMenuInitialized &&
            typeof initChatList === 'function'
        ) {
            window._chatProjectsMenuInitialized = true;
            // Defer refresh to allow current call stack (and DOM rendering) to finish
            setTimeout(() => {
                try {
                    initChatList();
                } catch (err) {
                    console.error('Failed to refresh chat list after enabling projects', err);
                }
            }, 0);
        }
    }
    if (typeof window.ChatSidebarMid?.setFeatureAvailability === 'function') {
        window.ChatSidebarMid.setFeatureAvailability('projects', isEnabled);
        return;
    }

    const projectsContainer = document.getElementById("sidebarProjects");
    if (projectsContainer) {
        projectsContainer.style.display = isEnabled ? "block" : "none";
    }
    if (typeof window !== 'undefined' && typeof window.applySidebarVisibilityFromCache === 'function') {
        window.applySidebarVisibilityFromCache();
    }
}



function applyChatFullWidthPreference(chat_full_width) {
    const chatArea = document.getElementById("chatAreaContainer");
    if (!chatArea) {
        return;
    }

    const shouldBeFullWidth = chat_full_width === true ||
        chat_full_width === "true" ||
        chat_full_width === 1 ||
        chat_full_width === "1";

    try {
        localStorage.setItem("chat_full_width", shouldBeFullWidth ? "true" : "false");
    } catch (_) {
        // Ignore localStorage access issues
    }

    if (shouldBeFullWidth) {
        chatArea.style.maxWidth = "100%";
        chatArea.style.minWidth = "100%";
    } else {
        chatArea.style.maxWidth = "";
        chatArea.style.minWidth = "";
    }
}

// ------------------------------------------------------------
// Auto-compact chat area based on .main-container width
// ------------------------------------------------------------
const CHAT_COMPACT_THRESHOLD = 1000; // px – main-container width below which chat goes full-width
const SUPPORTS_CHAT_LAYOUT_CONTAINER_QUERIES = typeof CSS !== 'undefined'
    && typeof CSS.supports === 'function'
    && CSS.supports('container-type', 'inline-size');

function updateChatCompactMode() {
    if (SUPPORTS_CHAT_LAYOUT_CONTAINER_QUERIES) {
        document.body?.classList.remove('chat-area-compact');
        return;
    }

    const mainContainer = document.querySelector('.main-container');
    if (!mainContainer) return;
    if (document.body?.style.display === 'none') return;

    const width = mainContainer.getBoundingClientRect().width;
    if (!Number.isFinite(width) || width <= 0) return;

    document.body.classList.toggle('chat-area-compact', width <= CHAT_COMPACT_THRESHOLD);
}

(() => {
    const mainContainer = document.querySelector('.main-container');
    if (!mainContainer || SUPPORTS_CHAT_LAYOUT_CONTAINER_QUERIES) return;

    const scheduleCompactModeUpdate = () => {
        requestAnimationFrame(updateChatCompactMode);
    };

    if (typeof ResizeObserver === 'function') {
        const observer = new ResizeObserver(() => {
            scheduleCompactModeUpdate();
        });
        observer.observe(mainContainer);
    } else {
        window.addEventListener('resize', scheduleCompactModeUpdate);
    }

    document.addEventListener('chatSetupReady', scheduleCompactModeUpdate);
    scheduleCompactModeUpdate();
})();

function initChatFullWidth(chat_full_width) {
    applyChatFullWidthPreference(chat_full_width);
}


function initChatBoxWarning(show_chat_box_warning, chat_box_warning_message) {
    const chatBoxWarning = document.getElementById("chatBoxWarning");
    const shouldShow = show_chat_box_warning === true;
    chatBoxWarning.style.display = shouldShow ? "flex" : "none";
    if (shouldShow) {
        chatBoxWarning.textContent = chat_box_warning_message;
    }
}


if (typeof window !== "undefined") {
    window.applyChatFullWidthPreference = applyChatFullWidthPreference;
}
