// -----------------------\n// Projects UI logic\n// -----------------------\n
const projectsContainerMain = document.getElementById('projectsContainerMain');
const projectsContent = document.getElementById('projectsContent');
const projectsContentCreateProject = document.getElementById('projectsContentCreateProject');
const projectsContentEditProject = document.getElementById('projectsContentEditProject');

const addProjectBtn = document.getElementById('addProjectBtn');
const createProjectCancelBtn = document.getElementById('createProjectCancelBtn');
const confirmCreateProjectBtn = document.getElementById('confirmCreateProjectBtn');
const deleteProjectCancelBtn = document.getElementById('deleteProjectCancelBtn');
const confirmDeleteProjectBtn = document.getElementById('confirmDeleteProjectBtn');
const deleteProjectOverlay = document.getElementById('deleteProjectOverlay');
const editProjectCancelBtn = document.getElementById('editProjectCancelBtn');
const saveProjectChangesBtn = document.getElementById('saveProjectChangesBtn');

const projectNameInput = document.getElementById('projectNameInput');
const projectNameError = document.getElementById('projectNameError');
const projectIconButton = document.getElementById('projectIconButton');
const projectIconDropdown = document.getElementById('projectIconDropdown');
const projectIconGrid = document.getElementById('projectIconGrid');
const projectColorRow = document.getElementById('projectColorRow');
const projectIconSaveBtn = document.getElementById('projectIconSaveBtn');
const projectIconCancelBtn = document.getElementById('projectIconCancelBtn');
const projectInstructionInput = document.getElementById('projectInstructionInput');
const projectSeparateMemoryCard = document.getElementById('projectSeparateMemoryCard');
const projectSeparateMemoryToggle = document.getElementById('projectSeparateMemoryToggle');
const deleteProjectTitle = document.getElementById('deleteProjectTitle');
const projectEditNameInput = document.getElementById('projectEditNameInput');
const projectEditNameError = document.getElementById('projectEditNameError');
const projectEditIconButton = document.getElementById('projectEditIconButton');
const projectEditIconDropdown = document.getElementById('projectEditIconDropdown');
const projectEditIconGrid = document.getElementById('projectEditIconGrid');
const projectEditColorRow = document.getElementById('projectEditColorRow');
const projectEditIconSaveBtn = document.getElementById('projectEditIconSaveBtn');
const projectEditIconCancelBtn = document.getElementById('projectEditIconCancelBtn');
const projectEditInstructionInput = document.getElementById('projectEditInstructionInput');
const projectEditSeparateMemoryCard = document.getElementById('projectEditSeparateMemoryCard');
const projectEditSeparateMemoryToggle = document.getElementById('projectEditSeparateMemoryToggle');
const projectManageMemoryBtn = document.getElementById('projectManageMemoryBtn');



// Transform folderIconOptions to array of SVG strings.
const SVG_ICONS = (() => {
    const folderIcons = typeof folderIconOptions !== 'undefined' ? folderIconOptions : Icons?.folderIconOptions;
    if (!folderIcons) return [];
    const values = Array.isArray(folderIcons) ? folderIcons : Object.values(folderIcons);
    return values
        .map((option) => {
            if (typeof option === 'string') return option;
            if (option && typeof option === 'object') {
                return option.svg || option.markup || option.icon || '';
            }
            return '';
        })
        .filter(Boolean);
})();

const ICON_COLORS = [
    '#FF6B6B', '#FF8A65', '#FFB74D', '#FFE082', '#F4FF81',
    '#81C784', '#4DB6AC', '#4FC3F7', '#9575CD', '#F06292'
];

let activeProjectContext = null;

/**
 * Return whether the current user's group permits the Memory feature.
 *
 * The chat setup request is the frontend source of truth for group feature
 * availability. Requiring an explicit `true` prevents a control flash while
 * the static application shell is still initializing.
 */
function areProjectMemoryControlsAvailable() {
    return typeof window !== 'undefined' && window.enableMemoriesFeature === true;
}

/**
 * Show project-memory controls only when Memory is enabled for this user's
 * group. Existing project settings remain unchanged while the group feature
 * is disabled, so unrelated edits cannot accidentally overwrite them.
 *
 * @param {boolean} enabled Whether the current group enables Memory.
 */
function setProjectMemoryControlsVisibility(enabled) {
    const visible = enabled === true;

    [projectSeparateMemoryCard, projectEditSeparateMemoryCard, projectManageMemoryBtn].forEach((element) => {
        if (!element) return;
        element.hidden = !visible;
        // The project component classes define explicit display values. Keep
        // an inline fallback in sync so stale or partially loaded stylesheets
        // cannot expose a group-disabled Memory control.
        element.style.display = visible ? '' : 'none';
    });
}

const projectFormValidation = window.FormValidation || {
    showInputError(inputEl, errorEl, message) {
        if (!inputEl) return;
        inputEl.classList.add('input-error');
        inputEl.setAttribute('aria-invalid', 'true');
        inputEl.closest('.projects-create-input-group')?.classList.add('has-error');
        if (errorEl) {
            if (message) errorEl.textContent = message;
            errorEl.classList.add('visible');
            errorEl.setAttribute('aria-hidden', 'false');
        }
        inputEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        inputEl.focus();
    },
    clearInputError(inputEl, errorEl) {
        if (!inputEl) return;
        inputEl.classList.remove('input-error');
        inputEl.setAttribute('aria-invalid', 'false');
        inputEl.closest('.projects-create-input-group')?.classList.remove('has-error');
        if (errorEl) {
            errorEl.classList.remove('visible');
            errorEl.setAttribute('aria-hidden', 'true');
        }
    },
};

function projectT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function projectFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(projectT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function projectPlural(count, singularKey, singularFallback, pluralKey, pluralFallback, vars = {}) {
    return Number(count) === 1
        ? projectFormatT(singularKey, singularFallback, { ...vars, count })
        : projectFormatT(pluralKey, pluralFallback, { ...vars, count });
}

function projectTranslateBackendDetail(detail, fallback) {
    const normalized = typeof detail === 'string' ? detail.trim() : '';
    switch (normalized) {
        case 'Projects are disabled for your group':
            return projectT('projects_disabled_group', 'Projects are disabled for your group');
        case 'Project sharing is disabled for your group':
            return projectT('projects_share_disabled_group', 'Project sharing is disabled for your group');
        case 'Project not found':
            return projectT('projects_error_not_found', 'Project not found');
        case 'Shared project not found':
            return projectT('projects_share_not_found', 'Shared project not found');
        case 'Only project owner can invite users':
            return projectT('projects_share_invite_owner_only', 'Only project owner can invite users');
        case 'You cannot join your own project':
            return projectT('projects_accept_owner_error', 'You cannot join your own project.');
        case 'You are already a member of this project':
            return projectT('projects_accept_already_member', 'You are already a member of this project.');
        case 'Password required':
        case 'Password is required':
            return projectT('projects_accept_password_required', 'Password is required for this project');
        case 'Invalid password':
            return projectT('projects_accept_invalid_password', 'Invalid password');
        case 'Too many invalid password attempts. Please retry later.':
            return projectT('projects_accept_password_attempts_limited', 'Too many invalid password attempts. Please retry later.');
        case 'expires_at must be in the future':
            return projectT('projects_share_expiry_future', 'Expiration must be in the future');
        case 'Cannot add yourself as a member':
            return projectT('projects_share_member_self_error', 'Cannot add yourself as a member');
        case 'Only the project owner can remove members':
            return projectT('projects_share_remove_member_owner_only', 'Only the project owner can remove members');
        default:
            return normalized || fallback;
    }
}

function clearProjectNameError(mode) {
    if (mode === 'edit') {
        projectFormValidation.clearInputError(projectEditNameInput, projectEditNameError);
        return;
    }
    projectFormValidation.clearInputError(projectNameInput, projectNameError);
}

function validateProjectName(mode) {
    const inputEl = mode === 'edit' ? projectEditNameInput : projectNameInput;
    const errorEl = mode === 'edit' ? projectEditNameError : projectNameError;
    const message = mode === 'edit'
        ? projectT('projects_edit_name_error', 'Please enter a project name')
        : projectT('projects_create_name_error', 'Please enter a project name');
    if (inputEl?.value?.trim()) {
        clearProjectNameError(mode);
        return true;
    }
    projectFormValidation.showInputError(inputEl, errorEl, message);
    return false;
}

const workspaceIconUtils = window.WorkspaceIconUtils;
const PROJECT_ICON_OPTIONS = workspaceIconUtils.getWorkspaceIconOptions(
    typeof folderIconOptions !== 'undefined' ? folderIconOptions : Icons?.folderIconOptions,
);
const PROJECT_ICON_COLORS = ICON_COLORS.map((hex, index) => ({
    id: String(index),
    name: hex,
    hex,
}));
const PROJECT_DEFAULT_ICON_ID = PROJECT_ICON_OPTIONS[0]?.id || 'folder';

const ProjectState = {
    create: {
        selectedIconId: PROJECT_DEFAULT_ICON_ID,
        selectedColorIndex: 0,
        isOpen: false,
        instruction: '',
    },
    edit: {
        selectedIconId: PROJECT_DEFAULT_ICON_ID,
        selectedColorIndex: 0,
        isOpen: false,
        instruction: '',
    },
};

const ProjectUtils = {
    escapeHtml(text = '') {
        return workspaceIconUtils.escapeHtml(text);
    },
    parseIcon(iconValue, iconColor = ICON_COLORS[0]) {
        return workspaceIconUtils.resolveWorkspaceStoredIcon(iconValue, {
            iconOptions: PROJECT_ICON_OPTIONS,
            defaultIconId: PROJECT_DEFAULT_ICON_ID,
            defaultColor: ICON_COLORS[0],
            color: iconColor,
        });
    },
    renderIcon(iconData, options = {}) {
        const size = options.size || 24;
        const svg = iconData?.svg || SVG_ICONS[0];
        const color = workspaceIconUtils.normalizeColor(iconData?.color, ICON_COLORS[0]);
        return `<span style="color:${color};width:${size}px;height:${size}px;display:inline-flex;">${svg}</span>`;
    },
    serializeSelection(mode) {
        const iconData = getProjectIconPicker(mode)?.getIconData?.();
        return iconData?.iconId || PROJECT_DEFAULT_ICON_ID;
    },
    selectedColor(mode) {
        return getProjectIconPicker(mode)?.getIconData?.().color || ICON_COLORS[0];
    },
};

function getProjectPickerRefs(mode) {
    const isEdit = mode === 'edit';
    const trigger = isEdit ? projectEditIconButton : projectIconButton;
    const dropdown = isEdit ? projectEditIconDropdown : projectIconDropdown;
    return {
        picker: trigger?.closest('.svg-select'),
        trigger,
        preview: trigger,
        dropdown,
        svgGrid: isEdit ? projectEditIconGrid : projectIconGrid,
        colorGrid: isEdit ? projectEditColorRow : projectColorRow,
        saveButton: isEdit ? projectEditIconSaveBtn : projectIconSaveBtn,
        cancelButton: isEdit ? projectEditIconCancelBtn : projectIconCancelBtn,
    };
}

const ProjectCreateIconPicker = workspaceIconUtils.createWorkspaceIconPicker({
    state: ProjectState.create,
    refs: () => getProjectPickerRefs('create'),
    iconOptions: PROJECT_ICON_OPTIONS,
    colors: PROJECT_ICON_COLORS,
    defaultIconId: PROJECT_DEFAULT_ICON_ID,
    defaultColor: ICON_COLORS[0],
    translate: projectT,
    variant: 'svg-select',
});

const ProjectEditIconPicker = workspaceIconUtils.createWorkspaceIconPicker({
    state: ProjectState.edit,
    refs: () => getProjectPickerRefs('edit'),
    iconOptions: PROJECT_ICON_OPTIONS,
    colors: PROJECT_ICON_COLORS,
    defaultIconId: PROJECT_DEFAULT_ICON_ID,
    defaultColor: ICON_COLORS[0],
    translate: projectT,
    variant: 'svg-select',
});

/**
 * Return the shared picker controller for the requested project form.
 *
 * @param {'create'|'edit'} mode Project form mode.
 * @returns {object|undefined} Shared workspace icon-picker controller.
 */
function getProjectIconPicker(mode) {
    return mode === 'edit' ? ProjectEditIconPicker : ProjectCreateIconPicker;
}

/**
 * Reset one project picker from the API's stored icon and color fields.
 *
 * @param {'create'|'edit'} mode Project form mode.
 * @param {string} iconValue Stored numeric preset index.
 * @param {string} colorValue Stored icon color.
 */
function resetProjectIconPicker(mode, iconValue = PROJECT_DEFAULT_ICON_ID, colorValue = ICON_COLORS[0]) {
    const picker = getProjectIconPicker(mode);
    picker?.reset?.(iconValue, colorValue);
    picker?.render?.();
    picker?.updatePreview?.();
}

function resetCreateState() {
    ProjectState.create.instruction = '';
    resetProjectIconPicker('create');
    
    if (projectInstructionInput) {
        projectInstructionInput.value = '';
    }
}

function initIconPickers() {
    [ProjectCreateIconPicker, ProjectEditIconPicker].forEach((picker) => {
        picker?.bind?.();
        picker?.render?.();
        picker?.updatePreview?.();
    });
}





function showProjectsStartContainer() {
    projectsContent.style.display = 'block';
    projectsContentCreateProject.style.display = 'none';
    projectsContentEditProject.style.display = 'none';
    activeProjectContext = null;
    projectNameInput.value = '';
    projectEditNameInput.value = '';
    if (projectInstructionInput) projectInstructionInput.value = '';
    if (projectEditInstructionInput) projectEditInstructionInput.value = '';
    if (projectSeparateMemoryToggle) projectSeparateMemoryToggle.checked = false;
    if (projectEditSeparateMemoryToggle) projectEditSeparateMemoryToggle.checked = false;
    clearProjectNameError('create');
    clearProjectNameError('edit');
    resetCreateState();
}

function showProjectsCreateContainer() {
    projectsContent.style.display = 'none';
    projectsContentEditProject.style.display = 'none';
    projectsContentCreateProject.style.display = 'block';
    activeProjectContext = null;
    clearProjectNameError('create');
    resetCreateState();
}

function showProjectDeleteModal(project) {
    if (!deleteProjectOverlay) return;
    activeProjectContext = project ? { id: project.id, title: project.title } : null;
    if (deleteProjectTitle) {
        deleteProjectTitle.textContent = project?.title
            ? projectFormatT('projects_delete_title_named', 'Delete project "{title}"', { title: project.title })
            : projectT('projects_delete_title', 'Delete project');
    }
    deleteProjectOverlay.hidden = false;
}

function hideProjectDeleteModal() {
    if (deleteProjectOverlay) {
        deleteProjectOverlay.hidden = true;
    }
    if (activeProjectContext?.mode === 'delete') {
        activeProjectContext = null;
    }
}

function showProjectsDeleteContainer(project) {
    showProjectDeleteModal(project);
}

function showProjectsEditContainer(project) {
    projectsContent.style.display = 'none';
    projectsContentCreateProject.style.display = 'none';
    projectsContentEditProject.style.display = 'block';
    
    const iconData = ProjectUtils.parseIcon(project.settings?.icon, project.settings?.icon_color);
    
    activeProjectContext = project
        ? {
            id: project.id,
            title: project.title,
            icon: project.settings?.icon || '',
            icon_color: iconData.color,
            system_instruction: project.settings?.system_instruction ?? '',
            separate_memory_enabled: Boolean(project.settings?.separate_memory_enabled),
        }
        : null;
    
    projectEditNameInput.value = project?.title || '';
    requestAnimationFrame(() => projectEditNameInput.focus());
    clearProjectNameError('edit');
    projectEditInstructionInput.value = activeProjectContext?.system_instruction || '';
    if (projectEditSeparateMemoryToggle) {
        projectEditSeparateMemoryToggle.checked = Boolean(activeProjectContext?.separate_memory_enabled);
    }
    
    ProjectState.edit.instruction = activeProjectContext?.system_instruction || '';
    resetProjectIconPicker('edit', project.settings?.icon || PROJECT_DEFAULT_ICON_ID, iconData.color);
}

function isProjectFormModeActive() {
    return Boolean(
        (projectsContentCreateProject && projectsContentCreateProject.style.display !== 'none') ||
        (projectsContentEditProject && projectsContentEditProject.style.display !== 'none')
    );
}

function hasProjectTransientDropdown() {
    return Boolean(
        document.querySelector('.projects-content .select-dropdown.open') ||
        projectIconDropdown?.classList.contains('open') ||
        projectEditIconDropdown?.classList.contains('open')
    );
}

function closeProjectTransientDropdowns() {
    closeAllProjectDropdowns();
    ProjectCreateIconPicker?.close?.();
    ProjectEditIconPicker?.close?.();
}

// Show create project form
if (addProjectBtn && projectsContent && projectsContentCreateProject) {
    addProjectBtn.addEventListener('click', () => {
        showProjectsCreateContainer();
        projectNameInput.value = '';
        projectNameInput.focus();
    });
}

// Cancel and go back to projects list
if (createProjectCancelBtn) {
    createProjectCancelBtn.addEventListener('click', () => {
        showProjectsStartContainer();
    });
}

if (deleteProjectCancelBtn) {
    deleteProjectCancelBtn.addEventListener('click', () => {
        hideProjectDeleteModal();
    });
}

if (deleteProjectOverlay) {
    deleteProjectOverlay.addEventListener('click', (event) => {
        if (event.target === deleteProjectOverlay) {
            hideProjectDeleteModal();
        }
    });
}

if (editProjectCancelBtn) {
    editProjectCancelBtn.addEventListener('click', () => {
        showProjectsStartContainer();
    });
}

// Create project
if (confirmCreateProjectBtn && projectsContent && projectsContentCreateProject) {
    confirmCreateProjectBtn.addEventListener('click', async () => {
        // The form's Save button sits outside the shared picker. Close it first
        // so an unconfirmed preview is rolled back before the payload is read.
        ProjectCreateIconPicker?.close?.();
        const title = projectNameInput?.value.trim() || '';
        if (!validateProjectName('create')) {
            return;
        }
        try {
            const res = await window.authedFetch(`/api/v1/projects/create`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    title,
                    icon: ProjectUtils.serializeSelection('create'),
                    icon_color: ProjectUtils.selectedColor('create'),
                    system_instruction: projectInstructionInput?.value?.trim() || ProjectState.create.instruction,
                    // Omit this group-managed setting when its control is
                    // hidden. New projects still default to no project memory.
                    ...(areProjectMemoryControlsAvailable()
                        ? { separate_memory_enabled: Boolean(projectSeparateMemoryToggle?.checked) }
                        : {}),
                }),
            });
            if (res.ok) {
                if (projectNameInput) {
                    projectNameInput.value = '';
                }
                if (projectInstructionInput) {
                    projectInstructionInput.value = '';
                }
                if (projectSeparateMemoryToggle) {
                    projectSeparateMemoryToggle.checked = false;
                }
                await refreshProjects();
                showProjectsStartContainer();
            } else {
                notifyError(projectT('projects_error_create_failed', 'Failed to create project'));
                showProjectsStartContainer();
            }
        } catch (e) {
            notifyError(projectT('projects_error_create', 'Error creating project'));
            showProjectsStartContainer();
        }
    });
}

// Allow Enter key to submit
if (projectNameInput && confirmCreateProjectBtn) {
    projectNameInput.addEventListener('input', () => clearProjectNameError('create'));
    projectNameInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            confirmCreateProjectBtn.click();
        }
    });
}

if (projectEditNameInput && saveProjectChangesBtn) {
    projectEditNameInput.addEventListener('input', () => clearProjectNameError('edit'));
    projectEditNameInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            saveProjectChangesBtn.click();
        }
    });
}




async function deleteProject() {
    try {
        const id = activeProjectContext?.id;
        if (!id) {
            notifyError(projectT('projects_error_delete_none', 'No project selected for deletion'));
            return;
        }
        const res = await window.authedFetch(`/api/v1/projects/delete?project_id=${encodeURIComponent(id)}`, {
            method: 'DELETE',
        });
        if (res.ok) {
            await refreshProjects();
            hideProjectDeleteModal();
            showProjectsStartContainer();
        } else {
            notifyError(projectFormatT('projects_error_delete_failed_status', 'Failed to delete project (status {status})', { status: res.status }));
            hideProjectDeleteModal();
            showProjectsStartContainer();
        }
    } catch (err) {
        notifyError(projectT('projects_error_delete', 'Error deleting project'));
        hideProjectDeleteModal();
        showProjectsStartContainer();
    }
}
confirmDeleteProjectBtn.addEventListener('click', async () => {deleteProject();});




async function updateProject() {
    // Only the picker's own Save action commits its preview selection.
    ProjectEditIconPicker?.close?.();
    if (!validateProjectName('edit')) {
        return;
    }
    try {
        const res = await window.authedFetch(`/api/v1/projects/update`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                project_id: activeProjectContext.id,
                title: projectEditNameInput.value.trim(),
                icon: ProjectUtils.serializeSelection('edit'),
                icon_color: ProjectUtils.selectedColor('edit'),
                system_instruction: projectEditInstructionInput?.value?.trim() || ProjectState.edit.instruction,
                // Do not send false solely because group policy hid the
                // control; preserve an existing project-memory setting while
                // the user edits unrelated fields.
                ...(areProjectMemoryControlsAvailable()
                    ? { separate_memory_enabled: Boolean(projectEditSeparateMemoryToggle?.checked) }
                    : {}),
            }),
        });
        if (res.ok) {
            await refreshProjects();
            showProjectsStartContainer();
        } else {
            notifyError(projectT('projects_error_update_failed', 'Failed to update project'));
            showProjectsStartContainer();
        }
    } catch (err) {
        notifyError(projectT('projects_error_update', 'Error updating project'));
        showProjectsStartContainer();
    }
}


saveProjectChangesBtn.addEventListener('click', async () => {updateProject();});

if (projectManageMemoryBtn) {
    projectManageMemoryBtn.addEventListener('click', () => {
        if (!activeProjectContext?.id) return;
        if (typeof window.showWorkspaceContainer === 'function') {
            window.showWorkspaceContainer({
                tab: 'memories',
                memoryScope: { type: 'project', projectId: activeProjectContext.id },
            });
        }
    });
}

// Register escape handler for project delete modal
if (typeof window !== 'undefined' && window.registerEscapeHandler) {
    window.registerEscapeHandler({
        id: 'delete-project-modal',
        priority: 100,
        isActive: () => deleteProjectOverlay && !deleteProjectOverlay.hidden,
        close: () => hideProjectDeleteModal(),
    });
    window.registerEscapeHandler({
        id: 'projects-transient-dropdowns',
        priority: 120,
        isActive: () => hasProjectTransientDropdown(),
        close: () => closeProjectTransientDropdowns(),
    });
    window.registerEscapeHandler({
        id: 'projects-form-mode',
        priority: 20,
        isActive: () => isProjectFormModeActive(),
        close: () => showProjectsStartContainer(),
    });
}

if (typeof window !== 'undefined') {
    // `initWorkspaceMemories` calls this after the group feature policy loads.
    // Apply the current value for scripts that initialize after that point.
    window.setProjectMemoryControlsVisibility = setProjectMemoryControlsVisibility;
    setProjectMemoryControlsVisibility(areProjectMemoryControlsAvailable());
    window.showProjectDeleteModal = showProjectDeleteModal;
    window.hideProjectDeleteModal = hideProjectDeleteModal;
    window.showProjectsEditContainer = showProjectsEditContainer;
}


function closeAllProjectDropdowns() {
    document.querySelectorAll('.projects-content .select-dropdown.open').forEach((dd) => dd.classList.remove('open'));
    document.querySelectorAll('.projects-content .project-ellipsis[aria-expanded="true"]')
        .forEach((trigger) => trigger.setAttribute('aria-expanded', 'false'));
}

document.addEventListener('click', (e) => {
    // Close dropdowns when clicking outside
    if (!e.target.closest('.projects-content-main-element')) {
        closeAllProjectDropdowns();
    }
});

// Close dropdowns on scroll/resize to prevent misalignment when using position: fixed
window.addEventListener('resize', closeAllProjectDropdowns);
window.addEventListener('scroll', closeAllProjectDropdowns, true);


function createProjectCard(project) {
    const iconData = ProjectUtils.parseIcon(project.settings?.icon, project.settings?.icon_color);
    project._icon_data = iconData;
    const iconMarkup = `<span class="project-icon">${ProjectUtils.renderIcon(iconData, { size: 24, strokeWidth: 0.5 })}</span>`;
    
    // Check if this is a shared project (user is not owner)
    const isOwner = project.is_owner !== false;
    const isShared = project.is_shared === true;
    const ownerName = project.owner_name || '';
    const memberCount = project.member_count || 0;
    const allowShare = canManageProjectSharing(project);
    
    const menuItems = [{
        action: 'edit',
        className: 'edit-btn',
        iconHtml: Icons.edit,
        label: projectT('projects_action_edit', 'Edit'),
        onSelect: () => showProjectsEditContainer(project),
    }];
    
    // Only owners can share. Admins can disable new sharing, but already-shared
    // projects must keep the share action so owners can manage links, invites,
    // members, passwords, and expiry settings for existing shared workspaces.
    if (isOwner && allowShare) {
        menuItems.push({
            action: 'share',
            className: 'share-btn',
            iconHtml: Icons.connections,
            label: projectT('projects_action_share', 'Share'),
            onSelect: () => showProjectShareModal(project),
        });
    }
    
    // Only owner can delete, members can leave
    if (isOwner) {
        menuItems.push({
            action: 'delete',
            className: 'select-dropdown-button-red delete-btn',
            iconHtml: Icons.trash,
            label: projectT('projects_action_delete', 'Delete'),
            onSelect: () => showProjectsDeleteContainer(project),
        });
    } else {
        menuItems.push({
            action: 'leave',
            className: 'select-dropdown-button-red leave-btn',
            iconHtml: Icons.logout,
            label: projectT('projects_action_leave', 'Leave'),
            onSelect: () => leaveProject(project),
        });
    }
    
    // Build sharing indicator
    let sharingIndicator = '';
    if (isShared) {
        if (isOwner && memberCount > 0) {
            const memberTitle = projectFormatT(
                memberCount === 1 ? 'projects_member_count_one' : 'projects_member_count_many',
                memberCount === 1 ? '{count} member' : '{count} members',
                { count: memberCount },
            );
            sharingIndicator = `<span class="project-shared-badge" title="${ProjectUtils.escapeHtml(memberTitle)}">${Icons.groups}</span>`;
        } else if (!isOwner) {
            sharingIndicator = `<span class="project-shared-badge project-shared-badge-guest" title="${ProjectUtils.escapeHtml(projectFormatT('projects_shared_by', 'Shared by {owner}', { owner: ownerName }))}">${Icons.groups}</span>`;
        }
    }
    
    // Build owner info line for shared projects
    let ownerInfo = '';
    if (!isOwner && ownerName) {
        ownerInfo = `<p class="project-owner-name">${ProjectUtils.escapeHtml(projectFormatT('projects_shared_by', 'Shared by {owner}', { owner: ownerName }))}</p>`;
    }
    
    return window.EntityCardRenderer.createCard({
        dataset: { projectId: project.id },
        iconHtml: iconMarkup,
        topExtraHtml: sharingIndicator,
        title: project.title,
        bottomExtraHtml: ownerInfo,
        menuItems,
        moreOptionsLabel: projectT('files_more_options', 'More options'),
        closeDropdowns: closeAllProjectDropdowns,
        onClick: async () => {
            try {
            if (typeof window.loadProject === 'function') {
                await window.loadProject(project.id);
            }
            if (typeof window.loadChatView === 'function') {
                await window.showChatContainer?.();
            }
            if (typeof window.showProjectChatPlaceholder === 'function') {
                window.showProjectChatPlaceholder();
            }
            } catch (e) {
                console.error('Failed to open project', e);
            }
        },
    });
}

function createProjectsEmptyState() {
    const div = document.createElement('div');
    div.className = 'workspace-notifications-empty workspace-empty-grid';
    div.innerHTML = `
        <div class="workspace-notifications-empty-icon">
            ${Icons.layers}
        </div>
        <p class="workspace-notifications-empty-title">${ProjectUtils.escapeHtml(projectT('projects_empty_title', 'No projects yet'))}</p>
        <p class="workspace-notifications-empty-text">${ProjectUtils.escapeHtml(projectT('projects_empty_text', 'Projects help you organize your conversations around specific topics or goals. Each project can have its own system instructions to customize AI responses.'))}</p>
    `;

    return div;
}

async function refreshProjects() {
    try {
        const res = await window.authedFetch(`/api/v1/projects/list`, { 
            method: 'GET',
        });
        if (!res.ok) {
            notifyError(projectT('projects_error_fetch_failed', 'Failed to fetch projects'));
            showChatStartContainer();
            return;
        }
        const data = await res.json();
        const projects = Array.isArray(data) ? data : data?.projects ?? [];
        if (typeof window !== 'undefined') {
            window.projectsCache = projects;
        }
        projectsContainerMain.innerHTML = '';
        if (projects.length === 0) {
            projectsContainerMain.appendChild(createProjectsEmptyState());
        } else {
            projects.forEach(p => projectsContainerMain.appendChild(createProjectCard(p)));
        }
    } catch (err) {
        notifyError(projectT('projects_error_fetch', 'Error fetching projects'));
        showChatStartContainer();
    }
}




async function initProjects() {
    showProjectsStartContainer();
    initIconPickers();
    await refreshProjects();
}


// ============================================================================
// Project Sharing Functions
// ============================================================================

let projectShareModalOverlay = null;
let projectAcceptModalOverlay = null;

function syncProjectModalBodyState() {
    const hasOpenProjectModal = [projectShareModalOverlay, projectAcceptModalOverlay]
        .some((overlay) => overlay?.classList.contains('active'));
    document.body.classList.toggle('modal-open', hasOpenProjectModal);
}

function trapProjectModalFocus(event, overlay, closeModal) {
    if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
    }
    if (event.key !== 'Tab') return;
    const dialog = overlay.querySelector('[role="dialog"]');
    const focusable = Array.from(dialog?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
    ) || []).filter((element) => !element.hidden && element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first) {
        event.preventDefault();
        dialog?.focus({ preventScroll: true });
    } else if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
    }
}

const ProjectShareState = {
    currentProject: null,
    shareMode: 'link',
    publicUsers: [],
    selectedUserIds: [],
    pendingShareId: null,
    pendingPasswordRequired: false,
};

const isProjectSharingAllowed = () => {
    if (typeof window === 'undefined') return true;
    return window.allowProjectShareFeature !== false;
};

function projectHasExistingShareState(project) {
    if (!project) return false;
    return project.has_link_share === true || Number(project.member_count || 0) > 0;
}

function canManageProjectSharing(project) {
    return isProjectSharingAllowed() || projectHasExistingShareState(project);
}

function toIsoDateTime(localValue) {
    if (!localValue) return null;
    const parsed = new Date(localValue);
    if (Number.isNaN(parsed.getTime())) return null;
    return parsed.toISOString();
}

function toLocalDateTimeValue(isoString) {
    if (!isoString) return '';
    const parsed = new Date(isoString);
    if (Number.isNaN(parsed.getTime())) return '';
    const local = new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
}

function createProjectShareModal() {
    if (projectShareModalOverlay) return projectShareModalOverlay;
    
    const overlay = document.createElement('div');
    overlay.className = 'notes-share-overlay shared-modal-overlay';
    overlay.id = 'projectShareModalOverlay';
    overlay.innerHTML = `
        <div class="notes-share-modal shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="projectShareModalTitle" tabindex="-1">
            <div class="notes-share-modal-header shared-modal-header shared-modal-header--main">
                <h3 class="notes-share-modal-title shared-modal-title" id="projectShareModalTitle">${ProjectUtils.escapeHtml(projectT('projects_share_title', 'Share Project'))}</h3>
                <button type="button" class="om-button shared-modal-close" id="projectShareCloseBtn" aria-label="Close" data-i18n-attr="aria-label:common_close">
                    ${Icons.close}
                </button>
            </div>
            <div class="notes-share-modal-body shared-modal-body">
                <p class="notes-share-modal-name" id="projectShareName"></p>
                
                <!-- Share Mode Toggle (Link / Invitation) -->
                <div class="notes-share-mode-toggle">
                    <button type="button" class="notes-share-mode-btn active" data-mode="link" id="projectShareModeLink">
                        ${Icons.urlLink}
                        ${ProjectUtils.escapeHtml(projectT('projects_share_mode_link', 'Link'))}
                    </button>
                    <button type="button" class="notes-share-mode-btn" data-mode="invite" id="projectShareModeInvite">
                        ${Icons.groups}
                        ${ProjectUtils.escapeHtml(projectT('projects_share_mode_invite', 'Invite Users'))}
                    </button>
                </div>
                
                <!-- Link Mode Content -->
                <div class="notes-share-mode-content" id="projectShareLinkMode">
                    <div class="notes-share-type-desc">
                        <p>${ProjectUtils.escapeHtml(projectT('projects_share_link_desc', 'Anyone with this link can join the project and view all chats and files.'))}</p>
                    </div>

                    <div class="notes-share-security-grid" style="margin: 12px 0 14px 0; display: grid; gap: 10px;">
                        <div>
                            <label class="notes-share-label" for="projectSharePasswordInput">${ProjectUtils.escapeHtml(projectT('projects_share_password_label', 'Password (optional)'))}</label>
                            <input type="password" id="projectSharePasswordInput" class="notes-share-user-search-input" placeholder="${ProjectUtils.escapeHtml(projectT('projects_share_password_placeholder', 'Set password to join'))}" autocomplete="new-password">
                        </div>
                        <div>
                            <label class="notes-share-label" for="projectShareExpiryInput">${ProjectUtils.escapeHtml(projectT('projects_share_expiry_label', 'Expires at (optional)'))}</label>
                            <input type="datetime-local" id="projectShareExpiryInput" class="notes-share-user-search-input">
                        </div>
                    </div>
                    
                    <!-- Generate/Copy Link Section -->
                    <div class="notes-share-link-section">
                        <button type="button" class="om-button border submit notes-share-generate-btn" id="projectShareGenerateBtn">
                            ${Icons.urlLink}
                            ${ProjectUtils.escapeHtml(projectT('projects_share_generate_link', 'Generate Link'))}
                        </button>
                        <div class="notes-share-link-container" id="projectShareLinkContainer" style="display: none;">
                            <input type="text" class="notes-share-link-input" id="projectShareLinkInput" readonly>
                            <button type="button" class="om-button border cancel notes-share-copy-btn" id="projectShareCopyBtn">
                                ${Icons.copy}
                                ${ProjectUtils.escapeHtml(projectT('projects_share_copy', 'Copy'))}
                            </button>
                        </div>
                        <button type="button" class="notes-share-stop-btn" id="projectShareStopBtn" style="display: none;">
                            ${Icons.error}
                            ${ProjectUtils.escapeHtml(projectT('projects_share_remove_link', 'Remove Link'))}
                        </button>
                    </div>
                    <p id="projectShareSecurityStatus" style="display:none; margin-top: 10px; color: var(--text-color-secondary); font-size: 12px;"></p>
                </div>
                
                <!-- Invite Mode Content -->
                <div class="notes-share-mode-content" id="projectShareInviteMode" style="display: none;">
                    <!-- User Search -->
                    <div class="notes-share-invite-section">
                        <label class="notes-share-label">${ProjectUtils.escapeHtml(projectT('projects_share_invite_select_users', 'Select Users to Invite'))}</label>
                        <div class="notes-share-user-search">
                            ${Icons.magnifyingGlass}
                            <input type="text" id="projectInviteUserSearch" placeholder="${ProjectUtils.escapeHtml(projectT('projects_share_search_users_placeholder', 'Search users...'))}" class="notes-share-user-search-input">
                        </div>
                        <div class="notes-share-user-list" id="projectInviteUserList">
                            <div class="notes-share-user-loading">${ProjectUtils.escapeHtml(projectT('projects_share_loading_users', 'Loading users...'))}</div>
                        </div>
                        <div class="notes-share-selected-users" id="projectSelectedUsers" style="display: none;">
                            <label class="notes-share-label">${ProjectUtils.escapeHtml(projectT('projects_share_selected', 'Selected'))} (<span id="projectSelectedCount">0</span>)</label>
                            <div class="notes-share-selected-list" id="projectSelectedUsersList"></div>
                        </div>
                    </div>
                    
                    <!-- Invite Button -->
                    <div class="notes-share-invite-actions">
                        <button type="button" class="om-button border submit notes-share-invite-btn" id="projectInviteBtn" disabled>
                            ${Icons.send}
                            ${ProjectUtils.escapeHtml(projectT('projects_share_invite_selected', 'Invite Selected Users'))}
                        </button>
                    </div>
                </div>
                
                <!-- Members Section -->
                <div class="notes-share-active-section" id="projectShareMembersSection">
                    <label class="notes-share-label">${ProjectUtils.escapeHtml(projectT('projects_share_members', 'Members'))}</label>
                    <div class="notes-share-active-list" id="projectShareMembersList"></div>
                </div>
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    projectShareModalOverlay = overlay;
    
    // Event listeners
    overlay.addEventListener('click', (e) => { if (e.target === overlay) hideProjectShareModal(); });
    overlay.addEventListener('keydown', (event) => trapProjectModalFocus(event, overlay, hideProjectShareModal));
    document.getElementById('projectShareCloseBtn').addEventListener('click', () => hideProjectShareModal());
    document.getElementById('projectShareCopyBtn').addEventListener('click', () => copyProjectShareLink());
    document.getElementById('projectShareGenerateBtn').addEventListener('click', () => generateProjectShareLink());
    document.getElementById('projectShareStopBtn').addEventListener('click', () => removeProjectShareLink());
    
    // Mode toggle listeners
    document.getElementById('projectShareModeLink').addEventListener('click', () => setProjectShareMode('link'));
    document.getElementById('projectShareModeInvite').addEventListener('click', () => setProjectShareMode('invite'));
    
    // Invite mode listeners
    document.getElementById('projectInviteUserSearch').addEventListener('input', (e) => filterProjectInviteUsers(e.target.value));
    document.getElementById('projectInviteBtn').addEventListener('click', () => sendProjectInvitations());
    
    return overlay;
}

function createProjectAcceptModal() {
    if (projectAcceptModalOverlay) return projectAcceptModalOverlay;
    
    const overlay = document.createElement('div');
    overlay.className = 'notes-share-overlay shared-modal-overlay';
    overlay.id = 'projectAcceptModalOverlay';
    overlay.setAttribute('hidden', '');
    overlay.innerHTML = `
        <div class="notes-share-modal shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="projectAcceptModalTitle" tabindex="-1">
            <div class="notes-share-modal-header shared-modal-header shared-modal-header--main">
                <h3 class="notes-share-modal-title shared-modal-title" id="projectAcceptModalTitle">${ProjectUtils.escapeHtml(projectT('projects_accept_title', 'Join Project'))}</h3>
                <button type="button" class="om-button shared-modal-close" id="projectAcceptCloseBtn" aria-label="Close" data-i18n-attr="aria-label:common_close">
                    ${Icons.close}
                </button>
            </div>
            <div class="notes-share-modal-body shared-modal-body">
                <div class="note-accept-info">
                    <p class="note-accept-title" id="projectAcceptTitle">${ProjectUtils.escapeHtml(projectT('projects_accept_loading', 'Loading...'))}</p>
                    <p class="note-accept-owner" id="projectAcceptOwner"></p>
                </div>
                
                <div class="note-accept-share-type-info" id="projectAcceptShareTypeInfo">
                    <div class="note-accept-share-type" style="background-color: rgba(59, 130, 246, 0.1); border-color: #3b82f6;">
                        <span class="note-accept-share-type-label" style="color: #3b82f6;">${ProjectUtils.escapeHtml(projectT('projects_accept_access_label', 'Project Access'))}</span>
                        <span class="note-accept-share-type-desc">${ProjectUtils.escapeHtml(projectT('projects_accept_access_desc', "You'll be able to view all chats and files in this project."))}</span>
                    </div>
                </div>
                
                <div class="note-accept-preview" id="projectAcceptPreview">
                    <p class="note-accept-preview-label">${ProjectUtils.escapeHtml(projectT('projects_accept_description_label', 'Description'))}</p>
                    <div class="note-accept-preview-content" id="projectAcceptPreviewContent"></div>
                </div>

                <div class="note-accept-password" id="projectAcceptPasswordWrapper" style="display:none; margin-top: 10px;">
                    <p class="note-accept-preview-label">${ProjectUtils.escapeHtml(projectT('projects_accept_password_label', 'Password'))}</p>
                    <input type="password" id="projectAcceptPasswordInput" class="notes-share-user-search-input" placeholder="${ProjectUtils.escapeHtml(projectT('projects_accept_password_placeholder', 'Enter share password'))}" autocomplete="current-password">
                </div>
            </div>
                
                <footer class="note-accept-actions shared-modal-footer">
                    <button type="button" class="om-button border cancel notes-share-cancel-btn" id="projectAcceptCancelBtn">${ProjectUtils.escapeHtml(projectT('common_cancel', 'Cancel'))}</button>
                    <button type="button" class="om-button border submit notes-share-confirm-btn" id="projectAcceptConfirmBtn" disabled>
                        ${Icons.plus}
                        ${ProjectUtils.escapeHtml(projectT('projects_accept_join_button', 'Join Project'))}
                    </button>
                </footer>
        </div>
    `;
    
    document.body.appendChild(overlay);
    projectAcceptModalOverlay = overlay;
    
    // Event listeners
    overlay.addEventListener('click', (e) => { if (e.target === overlay) hideProjectAcceptModal(); });
    overlay.addEventListener('keydown', (event) => trapProjectModalFocus(event, overlay, hideProjectAcceptModal));
    document.getElementById('projectAcceptCloseBtn').addEventListener('click', () => hideProjectAcceptModal());
    document.getElementById('projectAcceptCancelBtn').addEventListener('click', () => hideProjectAcceptModal());
    document.getElementById('projectAcceptConfirmBtn').addEventListener('click', () => confirmJoinProject());
    
    return overlay;
}

async function showProjectShareModal(project) {
    if (!canManageProjectSharing(project)) {
        if (typeof notifyWarning === 'function') {
            notifyWarning(projectT('projects_share_disabled_message', 'Project sharing is disabled by your admin.'));
        }
        return;
    }
    ProjectShareState.currentProject = project;
    ProjectShareState.selectedUserIds = [];
    const overlay = createProjectShareModal();
    
    // Reset UI
    setProjectShareMode('link');
    document.getElementById('projectShareName').textContent = project.title;
    document.getElementById('projectShareLinkInput').value = '';
    const passwordInput = document.getElementById('projectSharePasswordInput');
    const expiryInput = document.getElementById('projectShareExpiryInput');
    const securityStatusEl = document.getElementById('projectShareSecurityStatus');
    if (passwordInput) passwordInput.value = '';
    if (expiryInput) expiryInput.value = '';
    if (securityStatusEl) {
        securityStatusEl.style.display = 'none';
        securityStatusEl.textContent = '';
    }
    document.getElementById('projectShareGenerateBtn').style.display = 'flex';
    document.getElementById('projectShareLinkContainer').style.display = 'none';
    document.getElementById('projectShareStopBtn').style.display = 'none';
    document.getElementById('projectInviteUserSearch').value = '';
    document.getElementById('projectShareMembersList').innerHTML = `<div class="notes-share-user-loading">${ProjectUtils.escapeHtml(projectT('projects_share_loading_members', 'Loading members...'))}</div>`;
    
    // Show modal
    overlay._previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
        overlay.classList.add('active');
        syncProjectModalBodyState();
        document.getElementById('projectShareCloseBtn')?.focus({ preventScroll: true });
    });
    
    // Load share status
    await loadProjectShareStatus(project.id);
    
    // Load members
    await loadProjectMembers(project.id);
}

function hideProjectShareModal() {
    if (projectShareModalOverlay) {
        projectShareModalOverlay.classList.remove('active');
        projectShareModalOverlay.setAttribute('aria-hidden', 'true');
        syncProjectModalBodyState();
        const previousFocus = projectShareModalOverlay._previousFocus;
        projectShareModalOverlay._previousFocus = null;
        setTimeout(() => {
            projectShareModalOverlay.setAttribute('hidden', '');
            if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
        }, 200);
    }
    ProjectShareState.currentProject = null;
}

function setProjectShareMode(mode) {
    ProjectShareState.shareMode = mode;
    const linkBtn = document.getElementById('projectShareModeLink');
    const inviteBtn = document.getElementById('projectShareModeInvite');
    const linkMode = document.getElementById('projectShareLinkMode');
    const inviteMode = document.getElementById('projectShareInviteMode');
    
    if (mode === 'link') {
        linkBtn.classList.add('active');
        inviteBtn.classList.remove('active');
        linkMode.style.display = 'block';
        inviteMode.style.display = 'none';
    } else {
        linkBtn.classList.remove('active');
        inviteBtn.classList.add('active');
        linkMode.style.display = 'none';
        inviteMode.style.display = 'block';
        loadProjectPublicUsers();
    }
}

async function loadProjectShareStatus(projectId) {
    try {
        const res = await window.authedFetch(`/api/v1/projects/share/status?project_id=${projectId}`);
        if (res.ok) {
            const data = await res.json();
            const passwordInput = document.getElementById('projectSharePasswordInput');
            const expiryInput = document.getElementById('projectShareExpiryInput');
            const securityStatusEl = document.getElementById('projectShareSecurityStatus');
            if (data.share_url) {
                document.getElementById('projectShareLinkInput').value = data.share_url;
                document.getElementById('projectShareGenerateBtn').style.display = 'none';
                document.getElementById('projectShareLinkContainer').style.display = 'flex';
                document.getElementById('projectShareStopBtn').style.display = 'flex';
                if (securityStatusEl) {
                    const details = [];
                    details.push(data.has_password
                        ? projectT('projects_share_status_password_protected', 'Password protected')
                        : projectT('projects_share_status_no_password', 'No password'));
                    if (data.expires_at) {
                        const expiry = new Date(data.expires_at);
                        details.push(projectFormatT('projects_share_status_expires', 'Expires {date}', { date: Number.isNaN(expiry.getTime()) ? data.expires_at : expiry.toLocaleString() }));
                    } else {
                        details.push(projectT('projects_share_status_no_expiry', 'No expiry'));
                    }
                    securityStatusEl.textContent = details.join(' • ');
                    securityStatusEl.style.display = 'block';
                }
                if (passwordInput) {
                    passwordInput.placeholder = data.has_password
                        ? projectT('projects_share_password_new_placeholder', 'Set new password (optional)')
                        : projectT('projects_share_password_placeholder', 'Set password to join');
                }
                if (expiryInput) {
                    expiryInput.value = toLocalDateTimeValue(data.expires_at);
                }
            } else {
                if (expiryInput) {
                    expiryInput.value = '';
                }
                if (securityStatusEl) {
                    securityStatusEl.style.display = 'none';
                    securityStatusEl.textContent = '';
                }
            }
        }
    } catch (e) {
        console.error('Failed to load share status', e);
    }
}

async function generateProjectShareLink() {
    const project = ProjectShareState.currentProject;
    if (!project) return;
    
    const btn = document.getElementById('projectShareGenerateBtn');
    const passwordInput = document.getElementById('projectSharePasswordInput');
    const expiryInput = document.getElementById('projectShareExpiryInput');
    const passwordValue = String(passwordInput?.value || '').trim();
    const expiryIso = toIsoDateTime(expiryInput?.value || '');
    if (expiryInput?.value && !expiryIso) {
        notifyError(projectT('projects_share_expiry_invalid', 'Please choose a valid expiry date/time'));
        return;
    }
    btn.disabled = true;
    btn.innerHTML = `${Icons.loading_circle} ${ProjectUtils.escapeHtml(projectT('projects_share_generating', 'Generating...'))}`;
    
    try {
        const res = await window.authedFetch('/api/v1/projects/share/link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: project.id,
                password: passwordValue,
                expires_at: expiryIso,
                rotate: true,
            }),
        });
        if (res.ok) {
            const data = await res.json();
            document.getElementById('projectShareLinkInput').value = data.share_url;
            document.getElementById('projectShareGenerateBtn').style.display = 'none';
            document.getElementById('projectShareLinkContainer').style.display = 'flex';
            document.getElementById('projectShareStopBtn').style.display = 'flex';
            if (passwordInput) passwordInput.value = '';
            notifySuccess(projectT('projects_share_link_created', 'Share link created'));
            await loadProjectShareStatus(project.id);
        } else {
            const err = await res.json().catch(() => ({}));
            notifyError(projectTranslateBackendDetail(err.detail, projectT('projects_share_link_create_failed', 'Failed to create share link')));
        }
    } catch (e) {
        notifyError(projectT('projects_share_link_create_failed', 'Failed to create share link'));
    } finally {
        btn.disabled = false;
        btn.innerHTML = `${Icons.urlLink} ${ProjectUtils.escapeHtml(projectT('projects_share_generate_link', 'Generate Link'))}`;
    }
}

async function removeProjectShareLink() {
    const project = ProjectShareState.currentProject;
    if (!project) return;
    
    try {
        const res = await window.authedFetch('/api/v1/projects/share/link/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: project.id }),
        });
        if (res.ok) {
            document.getElementById('projectShareLinkInput').value = '';
            document.getElementById('projectShareGenerateBtn').style.display = 'flex';
            document.getElementById('projectShareLinkContainer').style.display = 'none';
            document.getElementById('projectShareStopBtn').style.display = 'none';
            notifySuccess(projectT('projects_share_link_removed', 'Share link removed'));
        } else {
            notifyError(projectT('projects_share_link_remove_failed', 'Failed to remove share link'));
        }
    } catch (e) {
        notifyError(projectT('projects_share_link_remove_failed', 'Failed to remove share link'));
    }
}

async function copyProjectShareLink() {
    const input = document.getElementById('projectShareLinkInput');
    const btn = document.getElementById('projectShareCopyBtn');
    if (!input || !input.value) return;

    try {
        await navigator.clipboard.writeText(input.value);
        btn.innerHTML = `${Icons.check}${ProjectUtils.escapeHtml(projectT('projects_share_copied', 'Copied!'))}`;
        setTimeout(() => {
            btn.innerHTML = `${Icons.copy} ${ProjectUtils.escapeHtml(projectT('projects_share_copy', 'Copy'))}`;
        }, 2000);
    } catch (e) {
        input.select();
        document.execCommand('copy');
    }
}

async function loadProjectPublicUsers() {
    const userList = document.getElementById('projectInviteUserList');
    userList.innerHTML = `<div class="notes-share-user-loading">${ProjectUtils.escapeHtml(projectT('projects_share_loading_users', 'Loading users...'))}</div>`;
    
    try {
        const users = [];
        const seenUserIds = new Set();
        let offset = 0;
        const limit = 100;
        while (true) {
            const res = await window.authedFetch(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`);
            if (!res.ok) throw new Error(projectT('projects_share_load_users_failed', 'Failed to load users'));
            const page = await res.json();
            const pageUsers = Array.isArray(page) ? page : [];
            pageUsers.forEach((user) => {
                const userId = String(user?.id || '').trim();
                if (!userId || seenUserIds.has(userId)) return;
                seenUserIds.add(userId);
                users.push(user);
            });
            const hasMore = String(res.headers.get('X-Has-More') || '').toLowerCase() === 'true';
            if (!hasMore || pageUsers.length === 0) break;
            offset += pageUsers.length;
        }
        ProjectShareState.publicUsers = users;
        renderProjectInviteUserList(users);
    } catch (error) {
        console.error('Failed to load public users:', error);
        userList.innerHTML = `<div class="notes-share-user-empty">${ProjectUtils.escapeHtml(projectT('projects_share_load_users_failed', 'Failed to load users'))}</div>`;
    }
}

function renderProjectInviteUserList(users) {
    const userList = document.getElementById('projectInviteUserList');
    
    if (!users || users.length === 0) {
        userList.innerHTML = `<div class="notes-share-user-empty">${ProjectUtils.escapeHtml(projectT('projects_share_no_users', 'No users available to invite'))}</div>`;
        return;
    }
    
    userList.innerHTML = users.map(user => {
        const isSelected = ProjectShareState.selectedUserIds.includes(user.id);
        const initials = getProjectUserInitials(user);
        return `
            <div class="notes-share-user-item ${isSelected ? 'selected' : ''}" data-user-id="${user.id}">
                <div class="notes-share-user-avatar">${initials}</div>
                <div class="notes-share-user-info">
                    <span class="notes-share-user-name">${ProjectUtils.escapeHtml(user.display_name || user.id || projectT('projects_share_unknown_user', 'Unknown user'))}</span>
                </div>
                <div class="notes-share-user-check">
                    ${Icons.copy}
                </div>
            </div>
        `;
    }).join('');
    
    // Add click handlers
    userList.querySelectorAll('.notes-share-user-item').forEach(item => {
        item.addEventListener('click', () => {
            const userId = item.dataset.userId;
            toggleProjectUserSelection(userId);
        });
    });
}

function getProjectUserInitials(user) {
    const name = user.display_name || user.id || '';
    const parts = name.split(/[\s@]+/);
    if (parts.length >= 2) {
        return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return name.substring(0, 2).toUpperCase() || '??';
}

function toggleProjectUserSelection(userId) {
    const idx = ProjectShareState.selectedUserIds.indexOf(userId);
    if (idx >= 0) {
        ProjectShareState.selectedUserIds.splice(idx, 1);
    } else {
        ProjectShareState.selectedUserIds.push(userId);
    }
    
    // Update UI
    const item = document.querySelector(`.notes-share-user-item[data-user-id="${userId}"]`);
    if (item) {
        item.classList.toggle('selected', idx < 0);
    }
    
    updateProjectSelectedUsersUI();
}

function updateProjectSelectedUsersUI() {
    const selectedCount = ProjectShareState.selectedUserIds.length;
    const selectedSection = document.getElementById('projectSelectedUsers');
    const countEl = document.getElementById('projectSelectedCount');
    const inviteBtn = document.getElementById('projectInviteBtn');
    
    countEl.textContent = selectedCount;
    selectedSection.style.display = selectedCount > 0 ? 'block' : 'none';
    inviteBtn.disabled = selectedCount === 0;
    
    // Render selected users
    const selectedList = document.getElementById('projectSelectedUsersList');
    const selectedUsers = ProjectShareState.publicUsers.filter(u => ProjectShareState.selectedUserIds.includes(u.id));
    
    selectedList.innerHTML = selectedUsers.map(user => `
        <div class="notes-share-selected-user" data-user-id="${user.id}">
            <span>${ProjectUtils.escapeHtml(user.display_name || user.id || projectT('projects_share_unknown_user', 'Unknown user'))}</span>
            <button type="button" class="notes-share-selected-remove" aria-label="${ProjectUtils.escapeHtml(projectT('projects_share_remove_user_aria', 'Remove user'))}">
                ${Icons.close}
            </button>
        </div>
    `).join('');
    
    // Add remove handlers
    selectedList.querySelectorAll('.notes-share-selected-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const userId = btn.closest('.notes-share-selected-user').dataset.userId;
            toggleProjectUserSelection(userId);
        });
    });
}

function filterProjectInviteUsers(query) {
    const lowerQuery = query.toLowerCase().trim();
    const filtered = lowerQuery 
        ? ProjectShareState.publicUsers.filter(u => {
            const name = (u.display_name || u.id || '').toLowerCase();
            return name.includes(lowerQuery);
        })
        : ProjectShareState.publicUsers;
    renderProjectInviteUserList(filtered);
}

async function sendProjectInvitations() {
    const project = ProjectShareState.currentProject;
    if (!project || ProjectShareState.selectedUserIds.length === 0) return;
    
    const btn = document.getElementById('projectInviteBtn');
    btn.disabled = true;
    btn.innerHTML = `${Icons.loading_circle} ${ProjectUtils.escapeHtml(projectT('projects_share_sending', 'Sending...'))}`;
    
    try {
        const res = await window.authedFetch('/api/v1/projects/invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: project.id, user_ids: ProjectShareState.selectedUserIds }),
        });
        
        if (res.ok) {
            notifySuccess(projectPlural(
                ProjectShareState.selectedUserIds.length,
                'projects_share_invite_success_one',
                'Invited 1 user',
                'projects_share_invite_success_other',
                'Invited {count} users',
                { count: ProjectShareState.selectedUserIds.length },
            ));
            ProjectShareState.selectedUserIds = [];
            updateProjectSelectedUsersUI();
            renderProjectInviteUserList(ProjectShareState.publicUsers);
            await loadProjectMembers(project.id);
        } else {
            const data = await res.json().catch(() => ({}));
            notifyError(projectTranslateBackendDetail(data.detail, projectT('projects_share_invite_error', 'Failed to send invitations')));
        }
    } catch (e) {
        notifyError(projectT('projects_share_invite_error', 'Failed to send invitations'));
    } finally {
        btn.disabled = false;
        btn.innerHTML = `${Icons.send} ${ProjectUtils.escapeHtml(projectT('projects_share_invite_selected', 'Invite Selected Users'))}`;
    }
}

async function loadProjectMembers(projectId) {
    const membersList = document.getElementById('projectShareMembersList');
    if (!membersList) return;
    
    try {
        const res = await window.authedFetch(`/api/v1/projects/members?project_id=${projectId}`);
        if (!res.ok) {
            membersList.innerHTML = `<div class="notes-share-user-empty">${ProjectUtils.escapeHtml(projectT('projects_share_load_members_failed', 'Failed to load members'))}</div>`;
            return;
        }
        
        const data = await res.json();
        const members = Array.isArray(data) ? data : data?.members ?? [];
        
        if (members.length === 0) {
            membersList.innerHTML = `<div class="notes-share-user-empty">${ProjectUtils.escapeHtml(projectT('projects_share_no_members', 'No members yet'))}</div>`;
            return;
        }
        
        membersList.innerHTML = members.map(member => `
            <div class="notes-share-active-item" data-user-id="${member.user_id}">
                <div class="notes-share-active-info">
                    <span class="notes-share-active-name">${ProjectUtils.escapeHtml(member.display_name || projectT('projects_share_unknown_user', 'Unknown user'))}</span>
                    <span class="notes-share-active-type ${member.role === 'owner' ? 'owner' : ''}">${member.role === 'owner' ? ProjectUtils.escapeHtml(projectT('projects_share_role_owner', 'Owner')) : ProjectUtils.escapeHtml(projectT('projects_share_role_member', 'Member'))}</span>
                </div>
                ${member.role !== 'owner' ? `
                    <button type="button" class="om-button border danger-nofill notes-share-active-remove" title="${ProjectUtils.escapeHtml(projectT('projects_share_remove_member_title', 'Remove member'))}">
                        ${Icons.close}
                    </button>
                ` : ''}
            </div>
        `).join('');
        
        // Add click handlers for remove buttons
        membersList.querySelectorAll('.notes-share-active-remove').forEach(btn => {
            btn.onclick = async () => {
                const userId = btn.closest('.notes-share-active-item').dataset.userId;
                await removeMemberFromProject(projectId, userId);
            };
        });
    } catch (e) {
        membersList.innerHTML = `<div class="notes-share-user-empty">${ProjectUtils.escapeHtml(projectT('projects_share_load_members_failed', 'Failed to load members'))}</div>`;
    }
}

async function removeMemberFromProject(projectId, userId) {
    try {
        const res = await window.authedFetch('/api/v1/projects/members/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: projectId, user_id: userId }),
        });
        
        if (res.ok) {
            notifySuccess(projectT('projects_share_member_removed', 'Member removed'));
            await loadProjectMembers(projectId);
            await refreshProjects();
        } else {
            notifyError(projectT('projects_share_member_remove_failed', 'Failed to remove member'));
        }
    } catch (e) {
        notifyError(projectT('projects_share_member_remove_failed', 'Failed to remove member'));
    }
}

async function leaveProject(project) {
    if (!await window.showDeleteConfirm({
        title: projectT('projects_action_leave', 'Leave'),
        message: projectFormatT('projects_leave_confirm', 'Are you sure you want to leave "{title}"? You will lose access to this project.', { title: project.title }),
        confirmLabel: projectT('projects_action_leave', 'Leave'),
    })) {
        return;
    }
    
    try {
        const res = await window.authedFetch('/api/v1/projects/leave', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: project.id }),
        });
        
        if (res.ok) {
            notifySuccess(projectT('projects_leave_success', 'Left project successfully'));
            await refreshProjects();
        } else {
            notifyError(projectT('projects_leave_error', 'Failed to leave project'));
        }
    } catch (e) {
        notifyError(projectT('projects_leave_error', 'Failed to leave project'));
    }
}

// ============================================================================
// Project Accept Modal (when opening shared link)
// ============================================================================

async function showProjectAcceptModal(shareId) {
    ProjectShareState.pendingShareId = shareId;
    ProjectShareState.pendingPasswordRequired = false;
    
    const overlay = createProjectAcceptModal();
    const titleEl = document.getElementById('projectAcceptTitle');
    const ownerEl = document.getElementById('projectAcceptOwner');
    const previewEl = document.getElementById('projectAcceptPreviewContent');
    const confirmBtn = document.getElementById('projectAcceptConfirmBtn');
    const passwordWrap = document.getElementById('projectAcceptPasswordWrapper');
    const passwordInput = document.getElementById('projectAcceptPasswordInput');
    
    // Reset and show loading state
    titleEl.textContent = projectT('projects_accept_loading', 'Loading...');
    ownerEl.textContent = '';
    previewEl.innerHTML = '';
    if (passwordWrap) passwordWrap.style.display = 'none';
    if (passwordInput) passwordInput.value = '';
    confirmBtn.disabled = true;

    // Show modal
    overlay._previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
        overlay.classList.add('active');
        syncProjectModalBodyState();
        document.getElementById('projectAcceptCloseBtn')?.focus({ preventScroll: true });
    });

    // Fetch preview data
    try {
        const res = await window.authedFetch(`/api/v1/projects/shared/${shareId}`);
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            throw { status: res.status, message: projectTranslateBackendDetail(errorData.detail, projectT('projects_accept_load_failed', 'Failed to load project')) };
        }
        
        const data = await res.json();
        titleEl.textContent = data.title || projectT('projects_untitled', 'Untitled Project');
        ownerEl.textContent = data.owner_name ? projectFormatT('projects_shared_by', 'Shared by {owner}', { owner: data.owner_name }) : '';
        ProjectShareState.pendingPasswordRequired = Boolean(data.password_required);
        
        // Show description preview
        if (data.description) {
            previewEl.innerHTML = `<p>${ProjectUtils.escapeHtml(data.description)}</p>`;
        } else {
            previewEl.innerHTML = `<p style="color: var(--text-color-secondary);">${ProjectUtils.escapeHtml(projectT('projects_accept_no_description', 'No description'))}</p>`;
        }
        if (passwordWrap) {
            passwordWrap.style.display = ProjectShareState.pendingPasswordRequired ? 'block' : 'none';
        }
        
        confirmBtn.disabled = false;
    } catch (error) {
        if (error.status === 400) {
            // Owner tried to open their own shared project
            hideProjectAcceptModal();
            notifyWarning(projectTranslateBackendDetail(error.message, projectT('projects_accept_owner_error', 'You cannot join your own project.')));
            clearProjectShareUrl();
            return;
        } else if (error.status === 409) {
            // Already a member
            hideProjectAcceptModal();
            notifyError(projectTranslateBackendDetail(error.message, projectT('projects_accept_already_member', 'You are already a member of this project.')));
            clearProjectShareUrl();
            return;
        }
        console.error('Failed to load shared project preview:', error);
        titleEl.textContent = projectT('projects_accept_error_title', 'Error loading project');
        previewEl.innerHTML = `<p style="color: #ef4444;">${ProjectUtils.escapeHtml(projectT('projects_accept_load_error_body', 'Could not load this shared project. It may no longer exist.'))}</p>`;
    }
}

function hideProjectAcceptModal() {
    const overlay = projectAcceptModalOverlay;
    if (overlay) {
        overlay.classList.remove('active');
        overlay.setAttribute('aria-hidden', 'true');
        syncProjectModalBodyState();
        const previousFocus = overlay._previousFocus;
        overlay._previousFocus = null;
        setTimeout(() => {
            overlay.setAttribute('hidden', '');
            if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
        }, 200);
    }
    ProjectShareState.pendingShareId = null;
    ProjectShareState.pendingPasswordRequired = false;
}

async function confirmJoinProject() {
    const shareId = ProjectShareState.pendingShareId;
    if (!shareId) return;

    const confirmBtn = document.getElementById('projectAcceptConfirmBtn');
    const passwordInput = document.getElementById('projectAcceptPasswordInput');
    const password = String(passwordInput?.value || '').trim();
    if (ProjectShareState.pendingPasswordRequired && !password) {
        notifyError(projectT('projects_accept_password_required', 'Password is required for this project'));
        passwordInput?.focus();
        return;
    }
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = `${Icons.loading_circle} ${ProjectUtils.escapeHtml(projectT('projects_accept_joining', 'Joining...'))}`;
    }

    try {
        const res = await window.authedFetch(`/api/v1/projects/shared/${shareId}/join`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
        });
        
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            throw new Error(projectTranslateBackendDetail(errorData.detail, projectT('projects_accept_join_failed', 'Failed to join project')));
        }
        
        const data = await res.json();
        hideProjectAcceptModal();
        notifySuccess(projectT('projects_accept_join_success', 'Successfully joined project!'));
        
        // Reload projects list
        await refreshProjects();
        
        // Clear the share URL from browser
        clearProjectShareUrl();
        
        // Open the project
        if (data.project_id && typeof window.loadProject === 'function') {
            await window.loadProject(data.project_id);
        }
    } catch (error) {
        console.error('Failed to join project:', error);
        notifyError(projectTranslateBackendDetail(error.message, projectT('projects_accept_join_failed', 'Failed to join project')));
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = `${Icons.plus} ${ProjectUtils.escapeHtml(projectT('projects_accept_join_button', 'Join Project'))}`;
        }
    }
}

function clearProjectShareUrl() {
    const path = window.location.pathname;
    if (path.includes('/projects/join/')) {
        history.replaceState(null, '', '/');
    }
}

function checkForProjectSharedLink() {
    const path = window.location.pathname;
    
    // Check for share link format: /projects/join/{share_id}
    const joinMatch = path.match(/\/projects\/join\/([a-zA-Z0-9-]+)/);
    if (joinMatch) {
        showProjectAcceptModal(joinMatch[1]);
        return true;
    }
    
    return false;
}

// Export functions for global access
if (typeof window !== 'undefined') {
    window.showProjectShareModal = showProjectShareModal;
    window.hideProjectShareModal = hideProjectShareModal;
    window.leaveProject = leaveProject;
    window.showProjectAcceptModal = showProjectAcceptModal;
    window.hideProjectAcceptModal = hideProjectAcceptModal;
    window.checkForProjectSharedLink = checkForProjectSharedLink;
}

// Initialize on page load - check for shared project links
const initializeProjectSharing = () => {
    checkForProjectSharedLink();
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeProjectSharing);
} else {
    initializeProjectSharing();
}
