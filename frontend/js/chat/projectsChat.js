const projectSidebarSelectors = {
    sidebar: '.project-sidebar',
    headerIcon: '.project-sidebar-header-icon',
    headerTitle: '.project-sidebar-header-title',
    tabList: '.project-sidebar-tab-list',
    tabButton: '.project-sidebar-tab',
    filesSection: '.project-sidebar-files-section',
    filesContainer: '.project-sidebar-files',
    chatsContainer: '.project-sidebar-chats',
    chatsSection: '.project-sidebar-chats-section',
    uploadButton: '.project-sidebar-upload-file',
    uploadInput: '.project-sidebar-upload-input',
    createChatButton: '.project-sidebar-create-chat',
    settingsButton: '.project-sidebar-manage-memory',
};

const projectSidebarState = {
    currentProject: null,
    currentFiles: [],
    currentChats: [],
    selectedTab: 'chats',
    pendingRequests: {},
    refreshIntervalId: null,
    isSharedProject: false,
};
var chatTitleUtils = window.ChatTitleUtils || {};

function projectSidebarT(key, fallback) {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function projectSidebarFormatT(key, fallback, vars) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(projectSidebarT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars && Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
        return value == null ? '' : String(value);
    });
}

function getProjectPlaceholderTemplate() {
    return `
    <div class="project-chat-placeholder">
        <div class="project-chat-placeholder-icon">
            ${Icons.text_placeholder}
        </div>
        <p class="project-chat-placeholder-title" data-i18n="project_sidebar_placeholder_title">${projectSidebarT('project_sidebar_placeholder_title', 'Select a project chat')}</p>
        <p class="project-chat-placeholder-text" data-i18n="project_sidebar_placeholder_text">${projectSidebarT('project_sidebar_placeholder_text', 'Choose a chat on the right to resume the conversation inside this project.')}</p>
        <button type="button" class="project-chat-placeholder-action" id="projectChatPlaceholderCreateBtn">
            ${Icons.plus}
            <span data-i18n="project_sidebar_start_new_chat">${projectSidebarT('project_sidebar_start_new_chat', 'Start a new project chat')}</span>
        </button>
    </div>
`;
}

const htmlEscape = (text = '') => String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

function getProjectSidebarIcon(name, fallback = '') {
    const iconSet = typeof Icons === 'object' ? Icons : (window.Icons || {});
    return typeof iconSet[name] === 'string' ? iconSet[name] : fallback;
}

function hydrateProjectUploadButtonIcon() {
    const iconEl = getSidebarElement(projectSidebarSelectors.uploadButton)?.querySelector('.project-sidebar-upload-icon');
    const uploadIcon = getProjectSidebarIcon('share');
    if (!iconEl || !uploadIcon || iconEl.dataset.iconHydrated === 'true') return;

    iconEl.innerHTML = uploadIcon;
    iconEl.dataset.iconHydrated = 'true';
}

function isProjectChatPinned(chat) {
    return chat && chat.pinned_position !== null && typeof chat.pinned_position !== 'undefined';
}

function getProjectChatPinnedPosition(chat) {
    const position = Number(chat?.pinned_position);
    return Number.isFinite(position) ? position : Number.MAX_SAFE_INTEGER;
}

function sortProjectChatsForSidebar(chats) {
    if (!Array.isArray(chats)) return [];
    const pinned = [];
    const unpinned = [];
    chats.forEach((chat) => {
        if (isProjectChatPinned(chat)) {
            pinned.push(chat);
        } else {
            unpinned.push(chat);
        }
    });
    pinned.sort((a, b) => getProjectChatPinnedPosition(a) - getProjectChatPinnedPosition(b));
    return [...pinned, ...unpinned];
}

function createProjectChatSectionElement(titleKey, fallbackTitle, section) {
    const sectionEl = document.createElement('div');
    sectionEl.className = 'sidebar-section';
    sectionEl.dataset.section = section;
    sectionEl.innerHTML = `
        <div class="sidebar-section-header">
            <p data-i18n="${htmlEscape(titleKey)}">${htmlEscape(projectSidebarT(titleKey, fallbackTitle))}</p>
            <div class="sidebar-section-header-right">
               ${Icons.chevron}
            </div>
        </div>
        <div class="chats-container" data-section-list="${htmlEscape(section)}"></div>
    `;
    const headerEl = sectionEl.querySelector('.sidebar-section-header');
    if (typeof window.bindSidebarSectionCollapse === 'function') {
        window.bindSidebarSectionCollapse(headerEl);
    } else {
        headerEl?.addEventListener('click', () => {
            sectionEl.classList.toggle('collapsed');
        });
    }
    return sectionEl;
}

function closeProjectChatDropdowns() {
    document.querySelectorAll('.project-sidebar .select-dropdown.open').forEach((dropdown) => {
        dropdown.classList.remove('open');
    });
}

function ensureProjectChatSections(container) {
    if (!container) {
        return {
            pinnedSection: null,
            pinnedList: null,
            unpinnedSection: null,
            unpinnedList: null
        };
    }

    let pinnedSection = container.querySelector('.sidebar-section[data-section="pinned"]');
    let unpinnedSection = container.querySelector('.sidebar-section[data-section="unpinned"]');

    if (!pinnedSection) {
        pinnedSection = createProjectChatSectionElement('sidebar_section_pinned', 'Pinned', 'pinned');
        if (unpinnedSection) {
            container.insertBefore(pinnedSection, unpinnedSection);
        } else {
            container.appendChild(pinnedSection);
        }
    }

    if (!unpinnedSection) {
        unpinnedSection = createProjectChatSectionElement('sidebar_section_chats', 'Chats', 'unpinned');
        container.appendChild(unpinnedSection);
    }

    const pinnedList = pinnedSection.querySelector('[data-section-list="pinned"]');
    const unpinnedList = unpinnedSection.querySelector('[data-section-list="unpinned"]');

    return { pinnedSection, pinnedList, unpinnedSection, unpinnedList };
}

function setProjectPlaceholderMode(enabled) {
    const body = document.body;
    if (!body) return;
    body.classList.toggle('project-chat-placeholder-mode', Boolean(enabled));
}

function getCachedProjects() {
    return Array.isArray(window.projectsCache) ? window.projectsCache : [];
}

function setProjectsCache(projects) {
    if (!Array.isArray(projects)) return;
    window.projectsCache = projects;
}

function getProjectFromCache(projectId) {
    if (!projectId) return null;
    const projects = getCachedProjects();
    return projects.find(project => project.id === projectId) || null;
}

async function fetchProjectsList() {
    try {
        const response = await authedFetch('/api/v1/projects/list');
        if (!response.ok) {
            notifyError(projectSidebarFormatT(
                'project_sidebar_fetch_projects_failed_status',
                'Failed to fetch projects ({status})',
                { status: response.status }
            ));
            return [];
        }
        const data = await response.json().catch(() => []);
        const projects = Array.isArray(data) ? data : data?.projects ?? [];
        setProjectsCache(projects);
        return projects;
    } catch (error) {
        console.error('Failed to fetch projects list', error);
        return [];
    }
}

async function resolveProject(projectId) {
    if (!projectId) return null;
    let project = getProjectFromCache(projectId);
    if (project) {
        return project;
    }

    const projects = await fetchProjectsList();
    project = projects.find(p => p.id === projectId) || null;
    return project;
}

function getProjectIconSvg(project) {
    if (!project) return '';
    const iconOptions = window.WorkspaceIconUtils.getWorkspaceIconOptions(Icons.folderIconOptions);
    const resolved = window.WorkspaceIconUtils.resolveWorkspaceStoredIcon(project.settings?.icon, {
        iconOptions,
        defaultIconId: 'folder',
        defaultColor: '#888888',
        color: project.settings?.icon_color,
    });
    const iconColor = resolved.color;
    const iconSvg = resolved.svg || '';
    return `<span style="color:${iconColor};width:36px;height:36px;display:inline-flex;">${iconSvg}</span>`;
}

function getSidebarElement(selector) {
    return document.querySelector(projectSidebarSelectors.sidebar)?.querySelector(selector) || null;
}

function getSidebarElements(selector) {
    return Array.from(document.querySelector(projectSidebarSelectors.sidebar)?.querySelectorAll(selector) || []);
}

function getSidebar() {
    return document.querySelector(projectSidebarSelectors.sidebar);
}

const PROJECT_SIDEBAR_DESKTOP_BREAKPOINT = 1280;

const isDesktopViewport = () => window.innerWidth > PROJECT_SIDEBAR_DESKTOP_BREAKPOINT;

function showProjectSidebar(project) {
    const sidebar = getSidebar();
    if (!sidebar) return;
    const chatContainer = document.getElementById('chatContainer');
    if (chatContainer && project?.id) {
        chatContainer.setAttribute('data-project-id', project.id);
    }

    // Update desktop header
    const desktopHeader = sidebar.querySelector('.project-sidebar-header');
    const iconEl = desktopHeader?.querySelector(projectSidebarSelectors.headerIcon);
    const titleEl = desktopHeader?.querySelector(projectSidebarSelectors.headerTitle);

    if (iconEl) {
        iconEl.innerHTML = getProjectIconSvg(project);
    }
    if (titleEl) {
        titleEl.textContent = project?.title || projectSidebarT('project_sidebar_untitled_project', 'Untitled project');
    }

    // Update mobile header elements
    const mobileIconEl = sidebar.querySelector('.project-sidebar-mobile-icon');
    const mobileTitleEl = sidebar.querySelector('.project-sidebar-mobile-title');
    if (mobileIconEl) {
        mobileIconEl.innerHTML = getProjectIconSvg(project);
    }
    if (mobileTitleEl) {
        mobileTitleEl.textContent = project?.title || projectSidebarT('project_sidebar_untitled_project', 'Untitled project');
    }

    if (isDesktopViewport()) {
        openProjectSidebarPanel();
    } else {
        closeProjectSidebarPanel({ silentToggleState: false });
    }

    updateProjectToggleAvailability(true);
}

function hideProjectSidebar() {
    const sidebar = getSidebar();
    if (!sidebar) return;
    sidebar.classList.remove('visible');
    const chatContainer = document.getElementById('chatContainer');
    chatContainer?.removeAttribute('data-project-id');
    setProjectPlaceholderMode(false);
    
    // Stop auto-refresh when leaving project
    stopProjectRefresh();
    projectSidebarState.isSharedProject = false;

    closeProjectSidebarPanel({ silentToggleState: true });
    updateProjectToggleAvailability(false);
    setProjectToggleState(false);

    // Reload model settings schema to remove project context field
    if (typeof window.reloadModelSettingsIfNeeded === 'function') {
        window.reloadModelSettingsIfNeeded();
    }
}

function updateProjectToggleAvailability(show) {
    const toggle = document.getElementById('projectSidebarMobileToggle');
    if (!toggle) return;

    toggle.style.display = show ? 'flex' : 'none';
    if (!show) {
        toggle.removeAttribute('aria-pressed');
        toggle.removeAttribute('data-open');
    }
}

function setProjectToggleState(isOpen) {
    const toggle = document.getElementById('projectSidebarMobileToggle');
    if (!toggle) return;

    toggle.setAttribute('aria-pressed', String(Boolean(isOpen)));
    toggle.dataset.open = String(Boolean(isOpen));
    toggle.setAttribute(
        'aria-label',
        isOpen
            ? projectSidebarT('project_sidebar_hide_aria', 'Hide project panel')
            : projectSidebarT('project_sidebar_show_aria', 'Show project panel')
    );
    toggle.setAttribute(
        'data-i18n-attr',
        isOpen ? 'aria-label:project_sidebar_hide_aria' : 'aria-label:project_sidebar_show_aria'
    );
    toggle.classList.toggle('is-active', Boolean(isOpen));
}

function openProjectSidebarPanel({ silentToggleState = false } = {}) {
    const sidebar = getSidebar();
    const backdrop = document.getElementById('projectSidebarBackdrop');
    if (!sidebar) return;
    
    sidebar.classList.add('visible');

    if (isDesktopViewport()) {
        backdrop?.classList.remove('visible');
    } else if (backdrop) {
        backdrop.classList.add('visible');
    }

    if (!silentToggleState) {
        setProjectToggleState(true);
    }
}

function closeProjectSidebarPanel({ silentToggleState = false } = {}) {
    const sidebar = getSidebar();
    const backdrop = document.getElementById('projectSidebarBackdrop');
    if (!sidebar) return;
    
    sidebar.classList.remove('visible');
    backdrop?.classList.remove('visible');
    closeProjectChatDropdowns();

    if (!silentToggleState) {
        setProjectToggleState(false);
    }
}

function toggleProjectSidebarPanel() {
    const sidebar = getSidebar();
    if (!sidebar) return;
    const isOpen = sidebar.classList.contains('visible');

    if (isOpen) {
        closeProjectSidebarPanel();
    } else {
        openProjectSidebarPanel();
    }
}

function setSidebarTab(tab) {
    const sidebar = getSidebar();
    if (!sidebar) return;
    const buttons = sidebar.querySelectorAll(projectSidebarSelectors.tabButton);
    buttons.forEach(btn => {
        const isActive = btn.dataset.tab === tab;
        btn.classList.toggle('active', isActive);
    });
    projectSidebarState.selectedTab = tab;
}

function renderEmptyState(container, key, fallback) {
    if (!container) return;
    const message = projectSidebarT(key, fallback);
    container.innerHTML = `
        <div class="project-sidebar-empty">
            <div class="project-sidebar-empty-icon">
                ${Icons.info}
            </div>
            <p data-i18n="${htmlEscape(key)}">${htmlEscape(message || projectSidebarT('project_sidebar_empty_generic', 'Nothing here yet'))}</p>
        </div>
    `;
}

function toggleProjectFilePreview(file) {
    if (typeof FilesPreview === 'undefined' || typeof FilesPreview.open !== 'function') {
        return;
    }

    const fileId = file?.file_id ?? file?.id;
    if (FilesPreview.isOpen && FilesPreview.activeFileId === fileId) {
        FilesPreview.close();
        return;
    }

    FilesPreview.open(file).catch((error) => {
        console.error('Failed to open file preview', error);
        notifyError?.(projectSidebarT('project_sidebar_open_file_preview_failed', 'Failed to open file preview.'));
    });
}

function renderFilesList(files) {
    const container = getSidebarElement(projectSidebarSelectors.filesContainer);
    if (!container) return;

    if (!Array.isArray(files) || files.length === 0) {
        renderEmptyState(container, 'project_sidebar_empty_files', 'No project files attached');
        return;
    }

    const infoBoxMarkup = `
        <div class="project-sidebar-info-box">
            ${Icons.info}
            <span data-i18n="project_sidebar_files_info">${projectSidebarT('project_sidebar_files_info', 'The model has access to these files in every chat within this project.')}</span>
        </div>
    `;

    const listMarkup = files.map(file => {
        const metaName = file.meta?.original_filename || file.meta?.originalFilename || file.meta?.title;
        const displayName = metaName || projectSidebarT('files_untitled_file', 'Untitled file');
        const extension = (metaName || '').split('.').pop();
        const size = file.file_size ? (file.file_size / 1024).toFixed(1) : null;
        const categoryLabel = file.file_category ? file.file_category.replace(/_/g, ' ') : projectSidebarT('project_sidebar_file_category', 'file');
        const dateValue = file.created_at ? new Date(file.created_at) : null;
        const dateLabel = dateValue ? dateValue.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
        const fileId = file?.file_id ?? file?.id ?? '';
        const openPreviewAria = projectSidebarFormatT(
            'files_preview_open_file_aria',
            'Open file preview: {filename}',
            { filename: displayName }
        );

        return `
            <div class="project-sidebar-row project-sidebar-row--file">
                <div class="project-sidebar-row-icon" data-ext="${htmlEscape(extension || '')}"></div>
                <div class="project-sidebar-row-body">
                    <p class="project-sidebar-row-title" title="${htmlEscape(displayName)}">${htmlEscape(displayName)}</p>
                    <p class="project-sidebar-row-meta">
                        ${htmlEscape(categoryLabel)}
                        ${size ? ` · ${(size)} KB` : ''}
                        ${dateLabel ? ` · ${dateLabel}` : ''}
                    </p>
                </div>
                <button type="button" class="project-sidebar-row-action" data-file-id="${htmlEscape(fileId)}" aria-label="${htmlEscape(openPreviewAria)}">
                    ${Icons.chevron}
                </button>
            </div>
        `;
    }).join('');

    container.innerHTML = infoBoxMarkup + listMarkup;

    // Attach click handlers for file preview
    container.querySelectorAll('.project-sidebar-row--file').forEach((row, index) => {
        const file = files[index];
        if (!file) return;

        row.addEventListener('click', (event) => {
            // Don't open preview if clicking on action button
            if (event.target.closest('.project-sidebar-row-action')) {
                return;
            }

            toggleProjectFilePreview(file);
        });

        row.querySelector('.project-sidebar-row-action')?.addEventListener('click', () => {
            toggleProjectFilePreview(file);
        });

        // Make row look clickable
        row.style.cursor = 'pointer';
    });
}

function createProjectChatRowElement(chat, isActive) {
    const row = document.createElement('div');
    row.className = `sidebar-element${isActive ? ' active' : ''}`;
    row.dataset.chatId = chat.id;
    row.dataset.chatSource = chatTitleUtils.isAutomationChat?.(chat)
        ? 'automation'
        : String(chat.source ?? chat?.meta?.source ?? '').trim().toLowerCase();
    row.dataset.chatTitle = chatTitleUtils.getChatDisplayTitle?.(chat, projectSidebarT('sidebar_untitled_chat', 'Untitled chat'))
        || projectSidebarT('sidebar_untitled_chat', 'Untitled chat');
    row.dataset.lastUpdatedAt = chat.last_updated_at || '';
    row.dataset.pinned = isProjectChatPinned(chat) ? 'true' : 'false';
    const allowChatDeletion = typeof checkChatDeletionAllowed === 'function' ? checkChatDeletionAllowed() : true;
    row.dataset.allowChatDeletion = String(allowChatDeletion);
    const dropdownItemsMarkup = typeof getChatSidebarDropdownItemsMarkup === 'function'
        ? getChatSidebarDropdownItemsMarkup()
        : [
            `<div class="select-dropdown-item"><div class="select-dropdown-button edit-btn">${getProjectSidebarIcon('edit')} <p data-i18n="sidebar_chat_action_edit">${projectSidebarT('sidebar_chat_action_edit', 'Edit')}</p></div></div>`,
            `<div class="select-dropdown-item"><div class="select-dropdown-button duplicate-btn">${getProjectSidebarIcon('copy')} <p data-i18n="sidebar_chat_action_duplicate">${projectSidebarT('sidebar_chat_action_duplicate', 'Duplicate')}</p></div></div>`,
            `<div class="select-dropdown-item"><div class="select-dropdown-button archive-btn">${getProjectSidebarIcon('archive')} <p data-i18n="sidebar_chat_action_archive">${projectSidebarT('sidebar_chat_action_archive', 'Archive')}</p></div></div>`,
            allowChatDeletion ? `<div class="select-dropdown-item"><div class="select-dropdown-button select-dropdown-button-red delete-btn">${getProjectSidebarIcon('trash')} <p data-i18n="sidebar_chat_action_delete">${projectSidebarT('sidebar_chat_action_delete', 'Delete')}</p></div></div>` : ''
        ].filter(Boolean).join('');

    row.innerHTML = `
        <a class="sidebar-element-button space-between" href="/chat/${encodeURIComponent(chat.id)}">
            <p class="chat-title-with-badge" title="${htmlEscape(row.dataset.chatTitle)}"></p>
        </a>
        <button type="button" class="sidebar-element-menu-trigger" aria-label="${htmlEscape(projectSidebarT('sidebar_chat_open_menu_aria', 'Open chat menu'))}" data-i18n-attr="aria-label:sidebar_chat_open_menu_aria">${getProjectSidebarIcon('ellipsis')}</button>
        <div class="select-dropdown">${dropdownItemsMarkup}</div>
    `;
    const titleEl = row.querySelector('a.sidebar-element-button > p');
    if (typeof chatTitleUtils.setChatTitleElement === 'function') {
        chatTitleUtils.setChatTitleElement(titleEl, chat, { fallbackTitle: projectSidebarT('sidebar_untitled_chat', 'Untitled chat') });
    } else if (titleEl) {
        titleEl.textContent = row.dataset.chatTitle;
    }
    window.ChatAttention?.registerChat(chat);
    window.ChatAttention?.decorateRow(row, chat.id);

    const anchorEl = row.querySelector('.sidebar-element-button');
    const dropdown = row.querySelector('.select-dropdown');
    const trigger = row.querySelector('.sidebar-element-menu-trigger');

    anchorEl?.addEventListener('click', async (event) => {
        event.preventDefault();
        const container = getSidebarElement(projectSidebarSelectors.chatsContainer);
        if (container) {
            container.querySelectorAll('.sidebar-element[data-chat-id]').forEach((chatRow) => {
                chatRow.classList.remove('active');
            });
        }
        row.classList.add('active');
        closeProjectChatDropdowns();
        await openProjectChat(chat.id);
        if (!isDesktopViewport()) {
            closeProjectSidebarPanel();
        }
    });

    const toggleDropdown = (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!dropdown) return;
        const shouldOpen = !dropdown.classList.contains('open');
        closeProjectChatDropdowns();
        if (typeof closeAllChatDropdowns === 'function') {
            closeAllChatDropdowns();
        }
        if (shouldOpen) {
            if (typeof window.closeModelSelect === 'function') {
                window.closeModelSelect();
            }
            window.getDropdownPanelNavigator?.(dropdown)?.reset({ focus: false });
            if (typeof positionChatDropdown === 'function') {
                positionChatDropdown(dropdown, row);
            }
            dropdown.classList.add('open');
        }
    };

    trigger?.addEventListener('click', toggleDropdown);

    if (typeof attachDropdownHandlers === 'function') {
        attachDropdownHandlers(dropdown, chat);
    }
    if (typeof bindChatSidebarDropdownActionHandlers === 'function') {
        bindChatSidebarDropdownActionHandlers(row, chat, {
            getTitle: () => row.dataset.chatTitle || projectSidebarT('sidebar_untitled_chat', 'Untitled chat'),
            closePanel: () => {
                if (!isDesktopViewport() && typeof closeProjectSidebarPanel === 'function') {
                    closeProjectSidebarPanel();
                }
            },
            afterListRefresh: async () => {
                if (typeof refreshProjectSidebarChats === 'function') {
                    await refreshProjectSidebarChats();
                }
            },
        });
    }

    return row;
}

function addOrUpdateProjectChatRow(chatId, title) {
    const container = getSidebarElement(projectSidebarSelectors.chatsContainer);
    if (!container) return;

    // Check if row already exists
    let existingRow = container.querySelector(`[data-chat-id="${chatId}"]`);
    
    if (existingRow) {
        // Update title if provided
        if (title) {
            const titleEl = existingRow.querySelector('a.sidebar-element-button > p');
            if (titleEl) {
                if (typeof chatTitleUtils.setChatTitleElement === 'function') {
                    chatTitleUtils.setChatTitleElement(titleEl, { title, source: existingRow.dataset.chatSource }, { fallbackTitle: projectSidebarT('sidebar_untitled_chat', 'Untitled chat') });
                } else if (typeof typewriteText === 'function') {
                    typewriteText(titleEl, title);
                } else {
                    titleEl.textContent = title;
                    titleEl.title = title;
                }
            }
            existingRow.dataset.chatTitle = title;
        }
        return;
    }

    // Remove empty state if present
    const emptyState = container.querySelector('.project-sidebar-empty');
    if (emptyState) {
        emptyState.remove();
    }

    const { pinnedSection, pinnedList, unpinnedSection, unpinnedList } = ensureProjectChatSections(container);
    if (!unpinnedList) return;

    // Create new row
    const newRow = createProjectChatRowElement({ id: chatId, title: title || projectSidebarT('sidebar_untitled_chat', 'Untitled chat') }, true);
    
    // Deactivate other rows
    container.querySelectorAll('.sidebar-element[data-chat-id]').forEach((chatRow) => chatRow.classList.remove('active'));
    
    // Insert at top of unpinned section (after pinned section)
    unpinnedList.insertBefore(newRow, unpinnedList.firstChild);
    if (unpinnedSection) {
        unpinnedSection.style.display = '';
    }
    if (pinnedSection && pinnedList && pinnedList.children.length === 0) {
        pinnedSection.style.display = 'none';
    }
    
    // Update internal state
    if (projectSidebarState.currentChats) {
        const normalizedChats = sortProjectChatsForSidebar(projectSidebarState.currentChats);
        const insertIndex = normalizedChats.findIndex(chat => !isProjectChatPinned(chat));
        const newChat = { id: chatId, title: title || projectSidebarT('sidebar_untitled_chat', 'Untitled chat'), pinned_position: null };
        if (insertIndex === -1) {
            normalizedChats.push(newChat);
        } else {
            normalizedChats.splice(insertIndex, 0, newChat);
        }
        projectSidebarState.currentChats = normalizedChats;
    }
}

function renderChatsList(chats, currentChatId) {
    const container = getSidebarElement(projectSidebarSelectors.chatsContainer);
    if (!container) return;

    if (!Array.isArray(chats) || chats.length === 0) {
        renderEmptyState(container, 'project_sidebar_empty_chats', 'No chats are linked to this project');
        return;
    }

    const orderedChats = sortProjectChatsForSidebar(chats);
    const pinnedChats = orderedChats.filter(chat => isProjectChatPinned(chat));
    const unpinnedChats = orderedChats.filter(chat => !isProjectChatPinned(chat));

    container.innerHTML = '';
    const { pinnedSection, pinnedList, unpinnedSection, unpinnedList } = ensureProjectChatSections(container);
    if (!pinnedList || !unpinnedList) return;

    if (pinnedSection) {
        pinnedSection.style.display = pinnedChats.length ? '' : 'none';
    }
    if (unpinnedSection) {
        unpinnedSection.style.display = unpinnedChats.length ? '' : 'none';
    }

    pinnedChats.forEach(chat => {
        const isActive = String(chat.id) === String(currentChatId);
        const row = createProjectChatRowElement(chat, isActive);
        pinnedList.appendChild(row);
    });

    unpinnedChats.forEach(chat => {
        const isActive = String(chat.id) === String(currentChatId);
        const row = createProjectChatRowElement(chat, isActive);
        unpinnedList.appendChild(row);
    });
}

async function openProjectChat(chatId) {
    if (!chatId) {
        showProjectChatPlaceholder();
        return;
    }
    setProjectPlaceholderMode(false);
    if (typeof loadChatView === 'function') {
        await loadChatView(chatId);
    } else {
        console.warn('loadChatView is not available');
    }
}

function showProjectChatPlaceholder() {
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) return;
    chatAreaContainer.innerHTML = getProjectPlaceholderTemplate();
    const actionButton = chatAreaContainer.querySelector('#projectChatPlaceholderCreateBtn');
    if (actionButton) {
        const hasProject = Boolean(projectSidebarState.currentProject?.id);
        actionButton.disabled = !hasProject;
        actionButton.classList.toggle('is-disabled', !hasProject);
        actionButton.addEventListener('click', () => {
            if (hasProject) {
                handleProjectCreateChat();
            } else {
                notifyError?.(projectSidebarT('project_sidebar_select_project_to_start_chat', 'Select a project to start a chat'));
            }
        });
    }
    setProjectPlaceholderMode(true);
}

function updateProjectSidebarView(project, files, chats, chatId) {
    if (!project) {
        hideProjectSidebar();
        showProjectChatPlaceholder();
        return;
    }

    showProjectSidebar(project);
    setSidebarTab(projectSidebarState.selectedTab);
    renderFilesList(files || []);
    renderChatsList(chats || [], chatId);
    setupProjectUploadButton(project);
    setupProjectCreateChatButton(project);
    setupProjectSettingsButtons(project);
    attachTabHandlers();
}

function setupProjectUploadButton(project) {
    hydrateProjectUploadButtonIcon();

    const button = getSidebarElement(projectSidebarSelectors.uploadButton);
    const input = getSidebarElement(projectSidebarSelectors.uploadInput);
    if (!button || !input) return;

    const hasProject = Boolean(project?.id);
    button.disabled = !hasProject;
    button.classList.toggle('is-disabled', !hasProject);

    if (!button.dataset.projectUploadHandlerAttached) {
        button.addEventListener('click', () => {
            if (!projectSidebarState.currentProject?.id) {
                notifyError?.(projectSidebarT('project_sidebar_select_project_to_upload_files', 'Select a project to upload files'));
                return;
            }
            input.value = '';
            input.click();
        });
        button.dataset.projectUploadHandlerAttached = 'true';
    }

    if (!input.dataset.projectUploadHandlerAttached) {
        input.addEventListener('change', handleProjectUploadInputChange);
        input.dataset.projectUploadHandlerAttached = 'true';
    }
}

async function handleProjectUploadInputChange(event) {
    const files = Array.from(event.target?.files || []);
    const projectId = projectSidebarState.currentProject?.id;
    event.target.value = '';
    if (!files.length || !projectId) {
        return;
    }
    await uploadProjectFiles(projectId, files);
}

async function uploadProjectFiles(projectId, files) {
    const button = getSidebarElement(projectSidebarSelectors.uploadButton);
    if (button) {
        button.disabled = true;
        button.classList.add('is-loading');
    }
    let uploaded = 0;
    try {
        for (const file of files) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('project_id', projectId);
            let response;
            try {
                response = await authedFetch('/api/v1/files/upload', {
                    method: 'POST',
                    headers: {
                        'Content-Type': null,
                    },
                    body: formData,
                });
            } catch (error) {
                console.error('Project file upload network error', error);
                notifyError?.(projectSidebarFormatT('project_sidebar_upload_file_failed', 'Failed to upload {filename}', { filename: file.name }));
                continue;
            }

            if (!response.ok) {
                const detail = await response.text().catch(() => '');
                notifyError?.(detail || projectSidebarFormatT('project_sidebar_upload_file_failed', 'Failed to upload {filename}', { filename: file.name }));
                continue;
            }

            const payload = await response.json().catch(() => ({}));
            if (payload?.status === 'success') {
                uploaded += 1;
            } else {
                notifyError?.(payload?.detail || projectSidebarFormatT('project_sidebar_upload_file_failed', 'Failed to upload {filename}', { filename: file.name }));
            }
        }

        if (uploaded) {
            notifySuccess?.(projectSidebarFormatT(
                uploaded === 1 ? 'project_sidebar_upload_success_one' : 'project_sidebar_upload_success_many',
                uploaded === 1 ? '{count} file uploaded' : '{count} files uploaded',
                { count: uploaded }
            ));
            const updatedFiles = await fetchProjectFileList(projectId);
            projectSidebarState.currentFiles = updatedFiles;
            renderFilesList(updatedFiles);
        }
    } catch (error) {
        console.error('Project file upload failed', error);
        notifyError?.(projectSidebarT('project_sidebar_upload_failed', 'Failed to upload project files'));
    } finally {
        if (button) {
            button.classList.remove('is-loading');
            button.disabled = !projectSidebarState.currentProject?.id;
            button.classList.toggle('is-disabled', !projectSidebarState.currentProject?.id);
        }
    }
}

function setupProjectCreateChatButton(project) {
    const button = getSidebarElement(projectSidebarSelectors.createChatButton);
    if (!button) return;

    const hasProject = Boolean(project?.id);
    button.disabled = !hasProject;
    button.classList.toggle('is-disabled', !hasProject);

    if (!button.dataset.projectChatHandlerAttached) {
        button.addEventListener('click', handleProjectCreateChat);
        button.dataset.projectChatHandlerAttached = 'true';
    }
}

function setupProjectSettingsButtons(project) {
    const buttons = getSidebarElements(projectSidebarSelectors.settingsButton);
    if (!buttons.length) return;

    const hasProject = Boolean(project?.id);
    buttons.forEach((button) => {
        button.disabled = !hasProject;
        button.classList.toggle('is-disabled', !hasProject);
        button.setAttribute('aria-label', projectSidebarT('projects_edit_title', 'Edit project'));
        button.setAttribute('title', projectSidebarT('projects_edit_title', 'Edit project'));

        if (!button.dataset.projectSettingsHandlerAttached) {
            button.addEventListener('click', openCurrentProjectSettings);
            button.dataset.projectSettingsHandlerAttached = 'true';
        }
    });
}

async function openCurrentProjectSettings() {
    const currentProject = projectSidebarState.currentProject;
    if (!currentProject?.id) {
        return false;
    }

    if (typeof window.showProjectsContainer === 'function') {
        const projectsShown = await window.showProjectsContainer();
        if (projectsShown === false) {
            return false;
        }
    }

    if (typeof window.showProjectsEditContainer === 'function') {
        window.showProjectsEditContainer(currentProject);
        return true;
    }

    console.warn('Project settings are unavailable right now');
    return false;
}

async function handleProjectCreateChat() {
    const project = projectSidebarState.currentProject;
    if (!project?.id) {
        notifyError?.(projectSidebarT('project_sidebar_select_project_to_start_chat', 'Select a project to start a chat'));
        return false;
    }

    // Navigation can be cancelled while split screen contains unsaved work.
    // Do not mutate the current chat binding until that guard has completed.
    if (typeof window.showChatStartContainer === 'function') {
        const chatStartShown = await window.showChatStartContainer();
        if (chatStartShown === false) {
            return false;
        }
    }

    const chatContainer = document.getElementById('chatContainer');
    if (chatContainer) {
        chatContainer.setAttribute('data-project-id', project.id);
        chatContainer.removeAttribute('data-chat-id');
    }

    setProjectPlaceholderMode(false);

    if (typeof window.showChatStartContainer !== 'function') {
        showProjectChatPlaceholder();
    }

    if (typeof window.hideProjectsContainer === 'function') {
        window.hideProjectsContainer();
    }

    // Reload model settings schema to include project context field
    if (typeof window.reloadModelSettingsIfNeeded === 'function') {
        window.reloadModelSettingsIfNeeded();
    }

    // showChatStartContainer synchronizes the generic draft before the project
    // binding above exists. Switch to the project-scoped draft afterwards.
    if (typeof window.syncChatInputDraftContext === 'function') {
        window.syncChatInputDraftContext({ reason: 'project-create-chat' });
    }
    return true;
}

function attachTabHandlers() {
    const sidebar = getSidebar();
    if (!sidebar) return;
    const buttons = sidebar.querySelectorAll(projectSidebarSelectors.tabButton);
    buttons.forEach(button => {
        button.addEventListener('click', () => {
            setSidebarTab(button.dataset.tab);
            toggleSidebarTab(button.dataset.tab);
        });
    });
    toggleSidebarTab(projectSidebarState.selectedTab);
}

function toggleSidebarTab(tab) {
    const filesSection = getSidebarElement(projectSidebarSelectors.filesSection);
    const filesContainer = getSidebarElement(projectSidebarSelectors.filesContainer);
    const chatsContainer = getSidebarElement(projectSidebarSelectors.chatsContainer);
    const chatsSection = getSidebarElement(projectSidebarSelectors.chatsSection);
    if (!filesSection || !filesContainer || !chatsContainer || !chatsSection) return;
    const isFiles = tab === 'files';
    filesSection.style.display = isFiles ? 'flex' : 'none';
    filesContainer.style.display = isFiles ? 'flex' : 'none';
    chatsSection.style.display = isFiles ? 'none' : 'flex';
    chatsContainer.style.display = isFiles ? 'none' : 'flex';
}

async function fetchProjectFileList(projectId) {
    if (!projectId) {
        console.warn('fetchProjectFileList called without a projectId');
        return [];
    }

    try {
        const response = await authedFetch(`/api/v1/files/project?project_id=${encodeURIComponent(projectId)}`);

        if (!response.ok) {
            notifyError(projectSidebarFormatT(
                'project_sidebar_load_files_failed_status',
                'Failed to load project files ({status})',
                { status: response.status }
            ));
            return [];
        }

        return await response.json();
    } catch (error) {
        console.error('Failed to fetch project files', error);
        return [];
    }
}

async function fetchProjectChatList(projectId) {
    if (!projectId) {
        console.warn('fetchProjectChatList called without a projectId');
        return [];
    }

    try {
        const params = new URLSearchParams({
            project_id: projectId,
            offset: '0',
            limit: '100',
        });
        const url = `/api/v1/chats/paginated?${params.toString()}`;
        const response = await authedFetch(url);

        if (!response.ok) {
            notifyError(projectSidebarFormatT(
                'project_sidebar_load_chats_failed_status',
                'Failed to load project chats ({status})',
                { status: response.status }
            ));
            return [];
        }

        const data = await response.json();
        return [...(data.pinned || []), ...(data.items || [])];
    } catch (error) {
        console.error('Failed to fetch project chats', error);
        return [];
    }
}

async function loadProject(projectId, chatId) {
    // Stop any existing refresh interval
    stopProjectRefresh();
    
    const project = await resolveProject(projectId);
    projectSidebarState.currentProject = project;
    projectSidebarState.selectedTab = 'chats';

    if (!project) {
        console.warn('Project not found. Ensure the project exists and is accessible.');
        hideProjectSidebar();
        showProjectChatPlaceholder();
        return;
    }

    // Check if this is a shared project (user is not owner)
    const isShared = project.is_shared === true || project.is_owner === false;
    projectSidebarState.isSharedProject = isShared;

    const pendingKey = Symbol('projectRequest');
    projectSidebarState.pendingRequests[projectId] = pendingKey;

    updateProjectSidebarView(project, [], [], chatId);
    renderFilesList([]);
    renderChatsList([], chatId);

    const [files, chats] = await Promise.all([
        fetchProjectFileList(projectId),
        fetchProjectChatList(projectId),
    ]);

    if (projectSidebarState.pendingRequests[projectId] !== pendingKey) {
        return;
    }

    projectSidebarState.currentFiles = files;
    projectSidebarState.currentChats = sortProjectChatsForSidebar(chats);
    updateProjectSidebarView(project, files, chats, chatId);

    if (!chatId) {
        showProjectChatPlaceholder();
    }

    // Reload model settings schema to include project context field
    if (typeof window.reloadModelSettingsIfNeeded === 'function') {
        await window.reloadModelSettingsIfNeeded({ awaitReload: true });
    }
    
    // Start auto-refresh for shared projects (every 10 seconds)
    if (isShared) {
        startProjectRefresh(projectId, chatId);
    }
}

// Auto-refresh functions for shared projects
function startProjectRefresh(projectId, chatId) {
    stopProjectRefresh(); // Clear any existing interval
    
    projectSidebarState.refreshIntervalId = setInterval(async () => {
        if (projectSidebarState.currentProject?.id !== projectId) {
            stopProjectRefresh();
            return;
        }
        
        try {
            const [files, chats] = await Promise.all([
                fetchProjectFileList(projectId),
                fetchProjectChatList(projectId),
            ]);
            
            // Only update if we're still viewing the same project
            if (projectSidebarState.currentProject?.id === projectId) {
                // Check if data changed before updating
                const filesChanged = JSON.stringify(files.map(f => f.id)) !== JSON.stringify(projectSidebarState.currentFiles.map(f => f.id));
                const sortedChats = sortProjectChatsForSidebar(chats);
                const chatsChanged = JSON.stringify(sortedChats.map(c => c.id)) !== JSON.stringify(projectSidebarState.currentChats.map(c => c.id));
                
                if (filesChanged) {
                    projectSidebarState.currentFiles = files;
                    renderFilesList(files);
                }
                
                if (chatsChanged) {
                    projectSidebarState.currentChats = sortedChats;
                    // Preserve the selected chat ID from URL or state
                    const currentChatId = getCurrentChatId();
                    renderChatsList(sortedChats, currentChatId);
                }
            }
        } catch (error) {
            console.warn('Failed to refresh shared project data', error);
        }
    }, 10000); // 10 seconds
}

function stopProjectRefresh() {
    if (projectSidebarState.refreshIntervalId) {
        clearInterval(projectSidebarState.refreshIntervalId);
        projectSidebarState.refreshIntervalId = null;
    }
}

function getCurrentChatId() {
    // Try to get current chat ID from URL or global state
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('c') || window.currentChatId || null;
}

async function refreshProjectSidebarChats() {
    const projectId = projectSidebarState.currentProject?.id || document.getElementById('chatContainer')?.getAttribute('data-project-id');
    if (!projectId) return;

    try {
        const chats = await fetchProjectChatList(projectId);
        projectSidebarState.currentChats = sortProjectChatsForSidebar(chats);
        const currentChatId = getCurrentChatId();
        renderChatsList(projectSidebarState.currentChats, currentChatId);
    } catch (error) {
        console.warn('Failed to refresh project chat list', error);
    }
}

function initProjectSidebar() {
    const sidebar = getSidebar();
    if (!sidebar) {
        console.warn('Project sidebar element not found in DOM');
        return;
    }

    hydrateProjectUploadButtonIcon();

    sidebar.addEventListener('click', (event) => {
        const tabButton = event.target.closest(projectSidebarSelectors.tabButton);
        if (tabButton) {
            setSidebarTab(tabButton.dataset.tab);
            toggleSidebarTab(tabButton.dataset.tab);
        }
    });

    toggleSidebarTab('files');

    // Toggle button
    const mobileToggle = document.getElementById('projectSidebarMobileToggle');
    if (mobileToggle) {
        mobileToggle.addEventListener('click', () => {
            toggleProjectSidebarPanel();
        });
        mobileToggle.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                toggleProjectSidebarPanel();
            }
        });
    }

    // Mobile close button
    const mobileClose = document.getElementById('projectSidebarMobileClose');
    if (mobileClose) {
        mobileClose.addEventListener('click', () => {
            closeProjectSidebarPanel();
        });
    }

    // Backdrop click to close
    const backdrop = document.getElementById('projectSidebarBackdrop');
    if (backdrop) {
        backdrop.addEventListener('click', () => {
            closeProjectSidebarPanel();
        });
    }

    if (!window.__projectSidebarDropdownCloseBound) {
        document.addEventListener('click', (event) => {
            if (!event.target.closest('.project-sidebar .sidebar-element')) {
                closeProjectChatDropdowns();
            }
        });
        window.__projectSidebarDropdownCloseBound = true;
    }

    // Drag handle for bottom sheet dismiss
    initDragToDismiss(sidebar);

    // Update toggle visibility on resize
    const handleResize = () => {
        const chatContainer = document.getElementById('chatContainer');
        const hasProject = chatContainer?.hasAttribute('data-project-id');
        updateProjectToggleAvailability(Boolean(hasProject));

        const toggle = document.getElementById('projectSidebarMobileToggle');
        const wantsOpen = toggle?.dataset.open === 'true';
        const isCurrentlyOpen = sidebar.classList.contains('visible');
        const shouldOpen = Boolean(hasProject && (wantsOpen || isCurrentlyOpen));
        const backdropEl = document.getElementById('projectSidebarBackdrop');

        if (isDesktopViewport()) {
            if (shouldOpen) {
                openProjectSidebarPanel({ silentToggleState: true });
            } else {
                closeProjectSidebarPanel({ silentToggleState: true });
            }
            backdropEl?.classList.remove('visible');
        } else {
            if (shouldOpen) {
                openProjectSidebarPanel({ silentToggleState: true });
            } else {
                closeProjectSidebarPanel({ silentToggleState: true });
            }
        }
    };

    window.addEventListener('resize', handleResize);
}

function flushPendingProjectSidebarRestore() {
    const pendingRestore = window.__pendingProjectSidebarRestore;
    if (!pendingRestore) {
        return false;
    }

    const chatContainer = document.getElementById('chatContainer');
    const activeChatId = String(chatContainer?.getAttribute('data-chat-id') || '').trim();
    const pendingChatId = String(pendingRestore.chatId || '').trim();
    if (pendingChatId && activeChatId && pendingChatId !== activeChatId) {
        window.__pendingProjectSidebarRestore = null;
        return false;
    }

    window.__pendingProjectSidebarRestore = null;

    if (pendingRestore.projectId) {
        loadProject(pendingRestore.projectId, pendingChatId || activeChatId || undefined);
        return true;
    }

    hideProjectSidebar();
    return true;
}

function initDragToDismiss(sidebar) {
    const dragHandle = document.getElementById('projectSidebarDragHandle');
    if (!dragHandle) return;

    let startY = 0;
    let currentY = 0;
    let isDragging = false;

    const onStart = (e) => {
        if (window.innerWidth > 768) return; // Only on mobile
        isDragging = true;
        startY = e.touches ? e.touches[0].clientY : e.clientY;
        sidebar.style.transition = 'none';
    };

    const onMove = (e) => {
        if (!isDragging) return;
        currentY = e.touches ? e.touches[0].clientY : e.clientY;
        const deltaY = currentY - startY;
        if (deltaY > 0) {
            sidebar.style.transform = `translateY(${deltaY}px)`;
        }
    };

    const onEnd = () => {
        if (!isDragging) return;
        isDragging = false;
        sidebar.style.transition = '';
        const deltaY = currentY - startY;
        
        // If dragged more than 100px or 30% of sidebar height, close it
        const threshold = Math.min(100, sidebar.offsetHeight * 0.3);
        if (deltaY > threshold) {
            closeProjectSidebarPanel();
        }
        sidebar.style.transform = '';
    };

    dragHandle.addEventListener('touchstart', onStart, { passive: true });
    dragHandle.addEventListener('touchmove', onMove, { passive: true });
    dragHandle.addEventListener('touchend', onEnd);
    
    // Also support mouse for testing
    dragHandle.addEventListener('mousedown', onStart);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onEnd);
}

if (document.readyState !== 'loading') {
    initProjectSidebar();
} else {
    document.addEventListener('DOMContentLoaded', initProjectSidebar);
}

window.loadProject = loadProject;
window.hideProjectSidebar = hideProjectSidebar;
window.showProjectChatPlaceholder = showProjectChatPlaceholder;
window.addOrUpdateProjectChatRow = addOrUpdateProjectChatRow;
window.refreshProjectChatSidebarChats = refreshProjectSidebarChats;
window.flushPendingProjectSidebarRestore = flushPendingProjectSidebarRestore;

flushPendingProjectSidebarRestore();

if (typeof window !== 'undefined' && !window.__projectSidebarImportRefreshListenerBound) {
    window.addEventListener('dataControls:importedDataChanged', async (event) => {
        if (!event?.detail?.refreshProjectSidebar) {
            return;
        }
        try {
            await refreshProjectSidebarChats();
        } catch (error) {
            console.warn('[projectsChat] Failed to refresh project sidebar after imported data change', error);
        }
    });
    window.__projectSidebarImportRefreshListenerBound = true;
}
