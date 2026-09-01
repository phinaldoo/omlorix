/**
 * Managed Skills Management Module
 * Handles CRUD operations for centrally managed skills
 */

(function () {

const ADMIN_SKILLS_PAGE_SIZE = 10;
const ADMIN_SKILLS_SEARCH_DEBOUNCE_MS = 300;

const AdminSkillsState = {
    skills: [],
    isLoading: false,
    initialized: false,
    page: 1,
    pageSize: ADMIN_SKILLS_PAGE_SIZE,
    total: 0,
    totalPages: 0,
    search: '',
    searchTimer: null,
    listRequestController: null,
    listRequestSequence: 0,
    detailRequestController: null,
    editingSkillId: null,
    pendingDeleteSkillId: null,
    activeSkillContext: null,
    // Marketplace import state
    marketplaceImportData: null,
    marketplaceImportModalInitialized: false,
    marketplaceImportLastFocusedElement: null,
    unsavedGuardRegistered: false,
    escapeRegistration: null,
    createInitialSnapshot: null,
    editInitialSnapshot: null,
    create: {},
    edit: {},
    importMode: 'files',
    importFileSkills: [],
    importPasteSkill: null,
    importSkills: [],
    importSelected: new Set(),
    importFileLabel: '',
    importArchives: [],
};

const UNSAVED_GUARD_ID = 'admin-skills-form-unsaved';
const adminSkillIconUtils = window.WorkspaceIconUtils;

const ADMIN_SKILL_ICON_OPTIONS = adminSkillIconUtils.getWorkspaceIconOptions(
    typeof folderIconOptions !== 'undefined' ? folderIconOptions : Icons?.folderIconOptions,
);
const ADMIN_SKILL_ICON_COLORS = adminSkillIconUtils.WORKSPACE_ICON_COLORS;
const ADMIN_SKILL_DEFAULT_ICON_ID = ADMIN_SKILL_ICON_OPTIONS[0]?.id || 'folder';
const ADMIN_SKILL_DEFAULT_ICON_COLOR = ADMIN_SKILL_ICON_COLORS[0]?.hex || '#E53935';

const AdminSkillsDOM = {
    get listPage() { return document.getElementById('page-skills'); },
    get createPage() { return document.getElementById('page-skills-create'); },
    get editPage() { return document.getElementById('page-skills-edit'); },
    get skillsList() { return document.getElementById('adminSkillsList'); },
    get searchInput() { return document.getElementById('adminSkillSearchInput'); },
    get searchClear() { return document.getElementById('adminSkillSearchClear'); },
    get pagination() { return document.getElementById('adminSkillsPagination'); },
    get paginationInfo() { return document.getElementById('adminSkillsPaginationInfo'); },
    get paginationPages() { return document.getElementById('adminSkillsPaginationPages'); },
    get paginationPrev() { return document.getElementById('adminSkillsPrevButton'); },
    get paginationNext() { return document.getElementById('adminSkillsNextButton'); },
    get exportBtn() { return document.getElementById('exportAdminSkillsButton'); },
    get importBtn() { return document.getElementById('importAdminSkillsButton'); },
    get importFileInput() { return document.getElementById('importAdminSkillsFileInput'); },
    get importBrowse() { return document.getElementById('importAdminSkillsBrowse'); },
    get importDropzone() { return document.getElementById('importAdminSkillsDropzone'); },
    get importTabFiles() { return document.getElementById('importAdminSkillsTabFiles'); },
    get importTabPaste() { return document.getElementById('importAdminSkillsTabPaste'); },
    get importPanelFiles() { return document.getElementById('importAdminSkillsPanelFiles'); },
    get importPanelPaste() { return document.getElementById('importAdminSkillsPanelPaste'); },
    get importPasteInput() { return document.getElementById('importAdminSkillsPasteInput'); },
    get importPasteClear() { return document.getElementById('importAdminSkillsPasteClear'); },
    get importSelectionControls() { return document.getElementById('importAdminSkillsSelectionControls'); },
    get importOverlay() { return document.getElementById('importAdminSkillsOverlay'); },
    get importClose() { return document.getElementById('importAdminSkillsClose'); },
    get importCancel() { return document.getElementById('importAdminSkillsCancel'); },
    get importConfirm() { return document.getElementById('importAdminSkillsConfirm'); },
    get importList() { return document.getElementById('importAdminSkillsList'); },
    get importSelectAll() { return document.getElementById('importAdminSkillsSelectAll'); },
    get importFileName() { return document.getElementById('importAdminSkillsFileName'); },
    get importStatus() { return document.getElementById('importAdminSkillsStatus'); },
    get createBtn() { return document.getElementById('createAdminSkillButton'); },
    // Create form
    get createForm() { return document.getElementById('adminSkillCreateForm'); },
    get createName() { return document.getElementById('adminSkillCreateName'); },
    get createDescription() { return document.getElementById('adminSkillCreateDescription'); },
    get createContent() { return document.getElementById('adminSkillCreateContent'); },
    get createIconButton() { return document.getElementById('adminSkillCreateIconButton'); },
    get createIconDropdown() { return document.getElementById('adminSkillCreateIconDropdown'); },
    get createIconGrid() { return document.getElementById('adminSkillCreateIconGrid'); },
    get createColorRow() { return document.getElementById('adminSkillCreateColorRow'); },
    get createIconSave() { return document.getElementById('adminSkillCreateIconSave'); },
    get createIconCancel() { return document.getElementById('adminSkillCreateIconCancel'); },
    get createCancel() { return document.getElementById('adminSkillCreateCancel'); },
    get createSubmit() { return document.getElementById('adminSkillCreateSubmit'); },
    // Edit form
    get editForm() { return document.getElementById('adminSkillEditForm'); },
    get editId() { return document.getElementById('adminSkillEditId'); },
    get editTitle() { return document.getElementById('adminSkillEditTitle'); },
    get editDescription() { return document.getElementById('adminSkillEditDescription'); },
    get editContent() { return document.getElementById('adminSkillEditContent'); },
    get editIconButton() { return document.getElementById('adminSkillEditIconButton'); },
    get editIconDropdown() { return document.getElementById('adminSkillEditIconDropdown'); },
    get editIconGrid() { return document.getElementById('adminSkillEditIconGrid'); },
    get editColorRow() { return document.getElementById('adminSkillEditColorRow'); },
    get editIconSave() { return document.getElementById('adminSkillEditIconSave'); },
    get editIconCancel() { return document.getElementById('adminSkillEditIconCancel'); },
    get editCancel() { return document.getElementById('adminSkillEditCancel'); },
    get editSubmit() { return document.getElementById('adminSkillEditSubmit'); },
    get deleteOverlay() { return document.getElementById('deleteAdminSkillOverlay'); },
    get deleteCancel() { return document.getElementById('deleteAdminSkillCancelButton'); },
    get deletePrimary() { return document.getElementById('deleteAdminSkillPrimaryButton'); },
    // File management elements
    get editScriptsList() { return document.getElementById('adminSkillEditScriptsList'); },
    get editScriptsInput() { return document.getElementById('adminSkillEditScriptsInput'); },
    get editScriptsBtn() { return document.getElementById('adminSkillEditScriptsBtn'); },
    get editReferencesList() { return document.getElementById('adminSkillEditReferencesList'); },
    get editReferencesInput() { return document.getElementById('adminSkillEditReferencesInput'); },
    get editReferencesBtn() { return document.getElementById('adminSkillEditReferencesBtn'); },
    get editAssetsList() { return document.getElementById('adminSkillEditAssetsList'); },
    get editAssetsInput() { return document.getElementById('adminSkillEditAssetsInput'); },
    get editAssetsBtn() { return document.getElementById('adminSkillEditAssetsBtn'); },
};

function isPageActive(pageEl) {
    return Boolean(pageEl && !pageEl.hidden);
}

/**
 * Cancel a pending detail fetch before another navigation intent can supersede it.
 *
 * Clear the shared reference before aborting so the cancelled request's
 * ``finally`` block cannot erase a newer controller created in the meantime.
 */
function cancelAdminSkillDetailRequest() {
    const controller = AdminSkillsState.detailRequestController;
    AdminSkillsState.detailRequestController = null;
    controller?.abort();
}

const t = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback ?? key);
    }
    return fallback ?? key;
};

const formatT = (key, fallback, vars) => {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    const template = t(key, fallback);
    return String(template).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars?.[token];
        return value === undefined || value === null ? '' : String(value);
    });
};

const formatCountT = (key, fallback, count) => {
    const template = t(key, fallback);
    const numericCount = Number(count);
    const safeCount = Number.isFinite(numericCount) ? numericCount : 0;
    const locale = String(document.documentElement?.lang || navigator.language || 'en');

    return String(template)
        .replace(
            /\{count,\s*plural,\s*one\s*\{([^{}]*)\}\s*few\s*\{([^{}]*)\}\s*many\s*\{([^{}]*)\}\s*other\s*\{([^{}]*)\}\s*\}/g,
            (_, one, few, many, other) => {
                const category = new Intl.PluralRules(locale).select(Math.abs(safeCount));
                if (category === 'one') return one;
                if (category === 'few') return few;
                if (category === 'many') return many;
                return other;
            }
        )
        .replace(/\{count\}/g, String(count));
};

/**
 * Build the bounded list URL used for both ordinary browsing and search.
 */
function buildAdminSkillsListUrl({ page, pageSize, search = '' }) {
    const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
    });
    const normalizedSearch = String(search || '').trim();
    if (normalizedSearch) {
        params.set('search', normalizedSearch);
    }
    return `/api/v1/skills/admin?${params.toString()}`;
}

/**
 * Keep pagination compact while always exposing the first, last, and nearby pages.
 */
function generateAdminSkillPageNumbers(currentPage, totalPages) {
    if (totalPages <= 7) {
        return Array.from({ length: totalPages }, (_, index) => index + 1);
    }
    if (currentPage <= 4) {
        return [1, 2, 3, 4, 5, '…', totalPages];
    }
    if (currentPage >= totalPages - 3) {
        return [1, '…', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
    }
    return [1, '…', currentPage - 1, currentPage, currentPage + 1, '…', totalPages];
}

const AdminSkillsAPI = {
    async request(input, init) {
        if (typeof window !== 'undefined' && typeof window.authedFetch === 'function') {
            return window.authedFetch(input, init);
        }
        return fetch(input, init);
    },

    async fetchSkills({ page, pageSize, search, signal } = {}) {
        const response = await this.request(buildAdminSkillsListUrl({
            page: page || 1,
            pageSize: pageSize || ADMIN_SKILLS_PAGE_SIZE,
            search,
        }), { signal });
        if (!response.ok) throw new Error(t('admin_skills_load_failed', 'Failed to load managed skills.'));
        return response.json();
    },

    async fetchSkill(skillId, { signal } = {}) {
        const response = await this.request(`/api/v1/skills/admin/${encodeURIComponent(skillId)}`, { signal });
        if (!response.ok) {
            throw new Error(t('admin_skills_detail_load_failed', 'Failed to load skill details.'));
        }
        return response.json();
    },

    async createSkill(data) {
        const response = await this.request('/api/v1/skills/admin', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skills_create_failed', 'Failed to create skill.'));
        }
        return response.json();
    },

    async updateSkill(skillId, data) {
        const response = await this.request(`/api/v1/skills/admin/${skillId}`, {
            method: 'PATCH',
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skills_update_failed', 'Failed to update skill.'));
        }
        return response.json();
    },

    async deleteSkill(skillId) {
        const response = await this.request(`/api/v1/skills/admin/${skillId}`, {
            method: 'DELETE',
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skills_delete_failed', 'Failed to delete skill.'));
        }
        return response.json();
    },

    async exportSkills() {
        const response = await this.request('/api/v1/skills/admin/export');
        if (!response.ok) {
            throw new Error(t('admin_skills_export_failed', 'Failed to export managed skills.'));
        }
        const disposition = response.headers.get('Content-Disposition') || '';
        const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
        return {
            blob: await response.blob(),
            filename: filenameMatch?.[1] || 'managed-skills-export.zip',
        };
    },

    async importMarkdown(markdown) {
        const response = await this.request('/api/v1/skills/admin/import-markdown', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ markdown }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skills_import_failed', 'Failed to import managed skills.'));
        }
        return response.json();
    },

    async importFiles(files, archiveSelections) {
        const formData = new FormData();
        files.forEach((file) => formData.append('files', file));
        formData.append('archive_selections', JSON.stringify(archiveSelections));
        const response = await this.request('/api/v1/skills/admin/import-files', {
            method: 'POST',
            body: formData,
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skills_import_failed', 'Failed to import managed skills.'));
        }
        return response.json();
    },

    async uploadSkillFiles(skillId, folderType, files) {
        const formData = new FormData();
        for (const file of files) {
            formData.append('files', file);
        }
        const response = await this.request(`/api/v1/skills/admin/${skillId}/files/${folderType}`, {
            method: 'POST',
            headers: { 'Content-Type': null },
            body: formData,
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skill_files_upload_failed', 'Failed to upload files.'));
        }
        return response.json();
    },

    async deleteSkillFile(skillId, folderType, filename) {
        const response = await this.request(`/api/v1/skills/admin/${skillId}/files/${folderType}/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || t('admin_skill_file_delete_failed', 'Failed to delete file.'));
        }
        return response.json();
    },
};

function escapeHtml(text = '') {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function parseSkillIcon(iconData) {
    return adminSkillIconUtils.resolveWorkspaceStoredIcon(iconData, {
        iconOptions: ADMIN_SKILL_ICON_OPTIONS,
        defaultIconId: ADMIN_SKILL_DEFAULT_ICON_ID,
        defaultColor: ADMIN_SKILL_DEFAULT_ICON_COLOR,
    });
}

function formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function setButtonLoadingState(button, isLoading, loadingLabel = t('admin_loading_ellipsis', 'Loading...')) {
    if (!button) return;
    if (!button.dataset.originalLabel) {
        button.dataset.originalLabel = button.textContent?.trim() || '';
    }
    button.disabled = Boolean(isLoading);
    button.textContent = isLoading ? loadingLabel : (button.dataset.originalLabel || '');
}

function setImportStatus(message = '', kind = '') {
    const status = AdminSkillsDOM.importStatus;
    if (!status) return;
    if (!message) {
        status.hidden = true;
        status.textContent = '';
        status.className = 'provider-import-status';
        return;
    }
    status.hidden = false;
    status.textContent = message;
    status.className = `provider-import-status ${kind}`.trim();
}

function parseAdminSkillMarkdown(markdownText) {
    const normalized = String(markdownText || '').replace(/^\uFEFF/, '');
    const lines = normalized.split(/\r?\n/);
    if (lines[0]?.trim() !== '---') {
        throw new Error(t('admin_skills_import_markdown_missing_frontmatter', 'SKILL.md must start with a frontmatter block (---).'));
    }

    const closingIndex = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
    if (closingIndex < 0) {
        throw new Error(t('admin_skills_import_markdown_missing_frontmatter_end', 'SKILL.md frontmatter must end with --- on its own line.'));
    }

    const frontmatterLines = lines.slice(1, closingIndex);
    const readField = (fieldName) => {
        for (let index = 0; index < frontmatterLines.length; index += 1) {
            const match = frontmatterLines[index].match(new RegExp(`^${fieldName}:\\s*(.*)$`));
            if (!match) continue;
            const rawValue = match[1].trim();
            if (rawValue === '|' || rawValue === '>') {
                const block = [];
                for (let nestedIndex = index + 1; nestedIndex < frontmatterLines.length; nestedIndex += 1) {
                    const nestedLine = frontmatterLines[nestedIndex];
                    if (!/^\s+/.test(nestedLine) && nestedLine.trim()) break;
                    block.push(nestedLine.replace(/^\s{2}/, ''));
                }
                return block.join(rawValue === '>' ? ' ' : '\n').trim();
            }
            try {
                const parsed = JSON.parse(rawValue);
                return parsed === null ? '' : String(parsed);
            } catch {
                return rawValue.replace(/^(['"])(.*)\1$/, '$2').trim();
            }
        }
        return '';
    };

    const name = readField('name');
    const description = readField('description');
    if (!name) {
        throw new Error(t('admin_skills_import_markdown_missing_name', "SKILL.md is missing the required 'name' field."));
    }
    if (!description) {
        throw new Error(t('admin_skills_import_markdown_missing_description', "SKILL.md is missing the required 'description' field."));
    }
    if (name.length > 64 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
        throw new Error(formatT(
            'admin_skills_import_markdown_invalid_name',
            'Invalid skill name "{name}". Use at most 64 lowercase letters, numbers, and hyphens.',
            { name }
        ));
    }
    if (description.length > 1024) {
        throw new Error(t('admin_skills_import_markdown_description_too_long', 'Skill description exceeds 1024 characters.'));
    }

    return {
        name,
        description,
        content: lines.slice(closingIndex + 1).join('\n').trim(),
    };
}

function formatImportErrorEntry(entry) {
    if (!entry || typeof entry !== 'object') return '';
    const source = entry.source || entry.entry || entry.name || t('admin_skills_import_unnamed', '(Unnamed skill)');
    const message = entry.error === 'Skill import failed due to an internal error.'
        ? t('admin_skills_import_failed', 'Failed to import managed skills.')
        : entry.error
            ? (typeof entry.error === 'string' ? entry.error : JSON.stringify(entry.error))
            : t('admin_skills_import_unknown_error', 'Unknown error.');
    return `• ${source}: ${message}`;
}

function resetImportState() {
    AdminSkillsState.importMode = 'files';
    AdminSkillsState.importFileSkills = [];
    AdminSkillsState.importPasteSkill = null;
    AdminSkillsState.importSkills = [];
    AdminSkillsState.importSelected = new Set();
    AdminSkillsState.importFileLabel = '';
    AdminSkillsState.importArchives = [];
    if (AdminSkillsDOM.importFileInput) AdminSkillsDOM.importFileInput.value = '';
    if (AdminSkillsDOM.importPasteInput) AdminSkillsDOM.importPasteInput.value = '';
    if (AdminSkillsDOM.importPasteClear) AdminSkillsDOM.importPasteClear.hidden = true;
    if (AdminSkillsDOM.importList) {
        AdminSkillsDOM.importList.innerHTML = '';
    }
    if (AdminSkillsDOM.importFileName) {
        AdminSkillsDOM.importFileName.textContent = '';
    }
    if (AdminSkillsDOM.importSelectAll) {
        AdminSkillsDOM.importSelectAll.checked = false;
    }
    if (AdminSkillsDOM.importSelectionControls) {
        AdminSkillsDOM.importSelectionControls.hidden = true;
    }
    if (AdminSkillsDOM.importConfirm) {
        AdminSkillsDOM.importConfirm.disabled = true;
    }
    setImportStatus();
}

function closeImportModal() {
    AdminSkillsDOM.importOverlay?.classList.remove('active');
    if (AdminSkillsDOM.importOverlay) {
        AdminSkillsDOM.importOverlay.hidden = true;
    }
    resetImportState();
}

function openImportModal() {
    if (!AdminSkillsDOM.importOverlay) return;
    resetImportState();
    AdminSkillsDOM.importOverlay.hidden = false;
    AdminSkillsDOM.importOverlay.classList.add('active');
    setImportMode('files');
    AdminSkillsDOM.importBrowse?.focus();
}

function updateImportSelectionUi() {
    const hasSkills = AdminSkillsState.importSkills.length > 0;
    if (AdminSkillsDOM.importSelectionControls) {
        AdminSkillsDOM.importSelectionControls.hidden = !hasSkills;
    }
    if (AdminSkillsDOM.importSelectAll) {
        AdminSkillsDOM.importSelectAll.checked = hasSkills
            && AdminSkillsState.importSkills.length === AdminSkillsState.importSelected.size;
        AdminSkillsDOM.importSelectAll.indeterminate = AdminSkillsState.importSelected.size > 0
            && AdminSkillsState.importSelected.size < AdminSkillsState.importSkills.length;
    }
    if (AdminSkillsDOM.importConfirm) {
        AdminSkillsDOM.importConfirm.disabled = AdminSkillsState.importSelected.size === 0;
    }
}

function activateImportSkills(skills, fileLabel = '') {
    AdminSkillsState.importSkills = Array.isArray(skills) ? skills : [];
    AdminSkillsState.importSelected = new Set(AdminSkillsState.importSkills.map((_, index) => index));
    if (AdminSkillsDOM.importFileName) {
        AdminSkillsDOM.importFileName.textContent = fileLabel;
    }
    renderImportSkillsList();
    updateImportSelectionUi();
}

function setImportMode(mode) {
    const isPasteMode = mode === 'paste';
    AdminSkillsState.importMode = isPasteMode ? 'paste' : 'files';
    AdminSkillsDOM.importTabFiles?.classList.toggle('active', !isPasteMode);
    AdminSkillsDOM.importTabPaste?.classList.toggle('active', isPasteMode);
    AdminSkillsDOM.importTabFiles?.setAttribute('aria-selected', isPasteMode ? 'false' : 'true');
    AdminSkillsDOM.importTabPaste?.setAttribute('aria-selected', isPasteMode ? 'true' : 'false');
    AdminSkillsDOM.importTabFiles?.setAttribute('tabindex', isPasteMode ? '-1' : '0');
    AdminSkillsDOM.importTabPaste?.setAttribute('tabindex', isPasteMode ? '0' : '-1');
    if (AdminSkillsDOM.importPanelFiles) AdminSkillsDOM.importPanelFiles.hidden = isPasteMode;
    if (AdminSkillsDOM.importPanelPaste) AdminSkillsDOM.importPanelPaste.hidden = !isPasteMode;

    if (isPasteMode) {
        const skills = AdminSkillsState.importPasteSkill ? [AdminSkillsState.importPasteSkill] : [];
        activateImportSkills(skills, skills.length ? 'SKILL.md' : '');
        AdminSkillsDOM.importPasteInput?.focus();
    } else {
        activateImportSkills(AdminSkillsState.importFileSkills, AdminSkillsState.importFileLabel);
        AdminSkillsDOM.importBrowse?.focus();
    }
    setImportStatus();
}

function handleImportTabKeydown(event) {
    // Follow the ARIA tabs keyboard pattern for left/right and boundary keys.
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const usePasteTab = event.key === 'ArrowRight' || event.key === 'End';
    setImportMode(usePasteTab ? 'paste' : 'files');
    (usePasteTab ? AdminSkillsDOM.importTabPaste : AdminSkillsDOM.importTabFiles)?.focus();
}

function renderImportSkillsList() {
    const host = AdminSkillsDOM.importList;
    if (!host) return;
    host.innerHTML = '';

    if (!AdminSkillsState.importSkills.length) {
        // An empty list is the neutral initial state. Only explain that no
        // skills were found after the admin has actually selected files and
        // those files have been inspected. Pasted Markdown reports validation
        // errors through the status region instead.
        const shouldShowEmptyState = AdminSkillsState.importMode === 'files'
            && Boolean(AdminSkillsState.importFileLabel);
        host.hidden = !shouldShowEmptyState;
        if (!shouldShowEmptyState) return;

        const emptyState = document.createElement('div');
        emptyState.className = 'provider-import-empty';
        emptyState.textContent = t('admin_skills_import_empty', 'No managed skills found in this file.');
        host.appendChild(emptyState);
        return;
    }

    host.hidden = false;
    const fragment = document.createDocumentFragment();
    AdminSkillsState.importSkills.forEach((skill, index) => {
        const selected = AdminSkillsState.importSelected.has(index);
        const entry = document.createElement('label');
        entry.className = 'provider-import-entry';
        entry.setAttribute('role', 'option');
        entry.setAttribute('aria-selected', selected ? 'true' : 'false');

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = selected;
        checkbox.dataset.skillIndex = String(index);
        checkbox.addEventListener('change', handleImportSkillToggle);
        entry.appendChild(checkbox);

        const content = document.createElement('div');
        content.className = 'provider-import-entry-content';

        const title = document.createElement('p');
        title.className = 'provider-import-entry-title';
        title.textContent = skill?.name || skill?.title || t('admin_skills_import_unnamed', '(Unnamed skill)');
        content.appendChild(title);

        const description = document.createElement('div');
        description.className = 'provider-import-entry-meta';
        description.textContent = skill?.description || t('admin_skills_default_description', 'Managed skill');
        content.appendChild(description);

        const meta = document.createElement('div');
        meta.className = 'provider-import-entry-meta';
        const fileCount = Number(skill?.fileCount || 0);
        meta.textContent = `${formatT('admin_skills_import_files_meta', 'Files: {count}', { count: fileCount })} · ${skill.sourceName || 'SKILL.md'}`;
        content.appendChild(meta);

        entry.appendChild(content);
        fragment.appendChild(entry);
    });

    host.appendChild(fragment);
}

function handleImportSkillToggle(event) {
    const checkbox = event.currentTarget;
    const index = Number.parseInt(checkbox.dataset.skillIndex || '', 10);
    if (Number.isNaN(index)) return;
    if (checkbox.checked) {
        AdminSkillsState.importSelected.add(index);
    } else {
        AdminSkillsState.importSelected.delete(index);
    }
    checkbox.closest('.provider-import-entry')?.setAttribute('aria-selected', checkbox.checked ? 'true' : 'false');
    if (AdminSkillsDOM.importSelectAll) {
        AdminSkillsDOM.importSelectAll.checked = AdminSkillsState.importSkills.length > 0
            && AdminSkillsState.importSkills.length === AdminSkillsState.importSelected.size;
    }
    updateImportSelectionUi();
    setImportStatus();
}

function toggleSelectAllImports(event) {
    const checked = Boolean(event.currentTarget?.checked);
    AdminSkillsState.importSelected.clear();
    if (checked) {
        AdminSkillsState.importSkills.forEach((_, index) => AdminSkillsState.importSelected.add(index));
    }
    renderImportSkillsList();
    updateImportSelectionUi();
    setImportStatus();
}

async function handleExportAdminSkills() {
    try {
        setButtonLoadingState(AdminSkillsDOM.exportBtn, true, t('admin_exporting_ellipsis', 'Exporting...'));
        const { blob, filename } = await AdminSkillsAPI.exportSkills();
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
        if (typeof showNotification === 'function') showNotification(t('admin_skills_export_success', 'Managed skills export downloaded successfully.'), 'success');
    } catch (error) {
        if (typeof showNotification === 'function') showNotification(error.message || t('admin_skills_export_failed', 'Failed to export managed skills.'), 'error');
    } finally {
        setButtonLoadingState(AdminSkillsDOM.exportBtn, false);
    }
}

function countArchiveSkillFiles(archive, folderPrefix) {
    const prefix = `${folderPrefix}/`;
    return Object.values(archive.files).filter((entry) => (
        !entry.dir
        && entry.name.startsWith(prefix)
        && entry.name.toLowerCase() !== `${prefix}skill.md`.toLowerCase()
    )).length;
}

async function inspectAdminSkillArchive(file, archiveIndex) {
    if (typeof JSZip === 'undefined') {
        throw new Error(t('admin_skills_import_zip_unavailable', 'ZIP support is unavailable. Reload the page and try again.'));
    }
    const archive = await JSZip.loadAsync(file);
    const archiveEntries = Object.values(archive.files);
    if (archiveEntries.length > 1000) {
        throw new Error(t('admin_skills_import_zip_too_many_files', 'ZIP archive contains too many files.'));
    }
    const skillDocuments = archiveEntries.filter((entry) => (
        !entry.dir && entry.name.split('/').pop()?.toLowerCase() === 'skill.md'
    ));
    if (!skillDocuments.length) {
        throw new Error(t('admin_skills_import_zip_missing_skill', 'ZIP archive does not contain any SKILL.md files.'));
    }

    const skills = [];
    for (const documentEntry of skillDocuments) {
        const uncompressedSize = Number(documentEntry?._data?.uncompressedSize || 0);
        const compressedSize = Number(documentEntry?._data?.compressedSize || 0);
        if (uncompressedSize > 1024 * 1024) {
            throw new Error(t('admin_skills_import_markdown_too_large', 'Markdown files may not exceed 1 MB.'));
        }
        if (compressedSize > 0 && uncompressedSize / compressedSize > 100) {
            throw new Error(t('admin_skills_import_zip_compression_ratio', 'ZIP archive exceeds the allowed compression ratio.'));
        }
        const pathParts = documentEntry.name.split('/').filter(Boolean);
        if (pathParts.length < 2) {
            throw new Error(t('admin_skills_import_zip_folder_required', 'Each SKILL.md must be inside its own skill folder.'));
        }
        const folderPrefix = pathParts.slice(0, -1).join('/');
        const folderName = pathParts[pathParts.length - 2];
        const markdown = await documentEntry.async('string');
        const parsed = parseAdminSkillMarkdown(markdown);
        if (folderName !== parsed.name) {
            throw new Error(formatT(
                'admin_skills_import_zip_name_mismatch',
                'Folder "{folder}" must match the SKILL.md name "{name}".',
                { folder: folderName, name: parsed.name }
            ));
        }
        skills.push({
            ...parsed,
            markdown,
            sourceKind: 'archive',
            sourceName: file.name,
            archiveIndex,
            folderPrefix,
            fileCount: countArchiveSkillFiles(archive, folderPrefix),
        });
    }
    return { skills };
}

async function handleImportAdminSkillsFile(event) {
    const input = event?.target;
    if (!input?.files?.length) return;
    const selectedFiles = Array.from(input.files);
    input.value = '';

    try {
        const inspectedSkills = [];
        const archives = [];
        const inspectionErrors = [];
        for (const file of selectedFiles) {
            const normalizedName = String(file.name || '').toLowerCase();
            try {
                if (normalizedName.endsWith('.md')) {
                    if (file.size > 1024 * 1024) {
                        throw new Error(t('admin_skills_import_markdown_too_large', 'Markdown files may not exceed 1 MB.'));
                    }
                    const markdown = await file.text();
                    inspectedSkills.push({
                        ...parseAdminSkillMarkdown(markdown),
                        markdown,
                        sourceKind: 'markdown',
                        sourceName: file.name,
                        fileCount: 0,
                    });
                    continue;
                }
                if (normalizedName.endsWith('.zip')) {
                    if (file.size > 20 * 1024 * 1024) {
                        throw new Error(t('admin_skills_import_zip_too_large', 'ZIP files may not exceed 20 MB.'));
                    }
                    const archiveIndex = archives.length;
                    const inspected = await inspectAdminSkillArchive(file, archiveIndex);
                    archives.push({ file });
                    inspectedSkills.push(...inspected.skills);
                    continue;
                }
                throw new Error(t('admin_skills_import_file_type_error', 'Only .md and .zip files are supported.'));
            } catch (error) {
                inspectionErrors.push(`• ${file.name}: ${error.message}`);
            }
        }

        AdminSkillsState.importArchives = archives;
        AdminSkillsState.importFileSkills = inspectedSkills;
        const label = formatT(
            'admin_skills_import_selected_files',
            'Files: {fileCount} · Skills found: {skillCount}',
            { fileCount: selectedFiles.length, skillCount: inspectedSkills.length }
        );
        AdminSkillsState.importFileLabel = label;
        activateImportSkills(inspectedSkills, label);
        if (inspectionErrors.length) {
            setImportStatus(inspectionErrors.join('\n'), 'error');
        } else {
            setImportStatus();
        }
        if (!inspectedSkills.length && typeof showNotification === 'function') {
            showNotification(t('admin_skills_import_empty', 'No valid managed skills found in these files.'), 'warning');
        }
    } catch (error) {
        if (typeof showNotification === 'function') showNotification(error.message || t('admin_skills_import_failed', 'Failed to import managed skills.'), 'error');
    }
}

function refreshPastedAdminSkillPreview() {
    const markdown = AdminSkillsDOM.importPasteInput?.value || '';
    if (AdminSkillsDOM.importPasteClear) {
        AdminSkillsDOM.importPasteClear.hidden = !markdown;
    }
    if (!markdown.trim()) {
        AdminSkillsState.importPasteSkill = null;
        activateImportSkills([], '');
        setImportStatus();
        return;
    }
    try {
        AdminSkillsState.importPasteSkill = {
            ...parseAdminSkillMarkdown(markdown),
            markdown,
            sourceKind: 'paste',
            sourceName: 'SKILL.md',
            fileCount: 0,
        };
        activateImportSkills([AdminSkillsState.importPasteSkill], 'SKILL.md');
        setImportStatus();
    } catch (error) {
        AdminSkillsState.importPasteSkill = null;
        activateImportSkills([], '');
        setImportStatus(error.message, 'error');
    }
}

function buildSelectedAdminSkillUploadFiles(selectedSkills) {
    const uploadFiles = selectedSkills
        .filter((skill) => skill.sourceKind === 'markdown')
        .map((skill, index) => new File(
            [skill.markdown],
            skill.sourceName || `SKILL-${index + 1}.md`,
            { type: 'text/markdown' }
        ));
    const archiveSelections = uploadFiles.map(() => null);

    const archiveGroups = new Map();
    selectedSkills
        .filter((skill) => skill.sourceKind === 'archive')
        .forEach((skill) => {
            if (!archiveGroups.has(skill.archiveIndex)) archiveGroups.set(skill.archiveIndex, []);
            archiveGroups.get(skill.archiveIndex).push(skill);
        });

    for (const [archiveIndex, skills] of archiveGroups.entries()) {
        const source = AdminSkillsState.importArchives[archiveIndex];
        if (!source) continue;
        uploadFiles.push(source.file);
        archiveSelections.push(skills.map((skill) => skill.folderPrefix));
    }
    return { uploadFiles, archiveSelections };
}

async function submitSelectedSkillImports() {
    if (!AdminSkillsState.importSelected.size) {
        setImportStatus(t('admin_skills_import_select_one', 'Select at least one managed skill to import.'));
        return;
    }

    try {
        setButtonLoadingState(AdminSkillsDOM.importConfirm, true, t('admin_importing_ellipsis', 'Importing...'));
        const selectedIndices = Array.from(AdminSkillsState.importSelected).sort((a, b) => a - b);
        const selectedSkills = selectedIndices.map((index) => AdminSkillsState.importSkills[index]).filter(Boolean);
        let result;
        if (AdminSkillsState.importMode === 'paste') {
            result = await AdminSkillsAPI.importMarkdown(selectedSkills[0].markdown);
        } else {
            const { uploadFiles, archiveSelections } = buildSelectedAdminSkillUploadFiles(selectedSkills);
            if (!uploadFiles.length) {
                throw new Error(t('admin_skills_import_choose_file_first', 'Choose one or more .md or .zip files first.'));
            }
            result = await AdminSkillsAPI.importFiles(uploadFiles, archiveSelections);
        }
        const createdCount = Array.isArray(result?.created) ? result.created.length : 0;
        const errorCount = Array.isArray(result?.errors) ? result.errors.length : 0;

        if (createdCount && typeof showNotification === 'function') {
            showNotification(
                formatT(
                    createdCount === 1 ? 'admin_skills_import_success_single' : 'admin_skills_import_success_plural',
                    createdCount === 1 ? 'Imported {count} managed skill successfully.' : 'Imported {count} managed skills successfully.',
                    { count: createdCount }
                ),
                'success'
            );
        }

        if (errorCount) {
            const details = result.errors.map((entry) => formatImportErrorEntry(entry)).filter(Boolean).join('\n');
            setImportStatus(details || t('admin_skills_import_partial_failed', 'Some managed skills could not be imported.'));
            AdminSkillsState.importSelected.clear();
            renderImportSkillsList();
            updateImportSelectionUi();
            if (typeof showNotification === 'function') {
                showNotification(
                    t('admin_skills_import_partial_failed', 'Some managed skills could not be imported.'),
                    'warning'
                );
            }
        } else {
            closeImportModal();
        }

        if (createdCount) {
            AdminSkillsManager.resetListSearch();
        }
        await AdminSkillsManager.loadSkills();
    } catch (error) {
        setImportStatus(error.message || t('admin_skills_import_failed', 'Failed to import managed skills.'));
        if (typeof showNotification === 'function') showNotification(error.message || t('admin_skills_import_failed', 'Failed to import managed skills.'), 'error');
    } finally {
        setButtonLoadingState(AdminSkillsDOM.importConfirm, false);
        updateImportSelectionUi();
    }
}

function getFileIcon(filename) {
    const normalized = String(filename || '').trim();
    const lastDotIndex = normalized.lastIndexOf('.');
    const ext = (lastDotIndex > 0 && lastDotIndex < normalized.length - 1)
        ? normalized.slice(lastDotIndex + 1).toLowerCase()
        : '';
    const iconByExtension = {
        // Audio / video
        mp3: 'mp3.svg',
        wav: 'wav.svg',
        aac: 'aac.svg',
        m4a: 'm4a.svg',
        midi: 'midi.svg',
        mid: 'midi.svg',
        mp4: 'mp4.svg',
        mpeg: 'mpeg.svg',
        mpg: 'mpg.svg',
        mov: 'mov.svg',
        avi: 'avi.svg',
        wmv: 'wmv.svg',
        flv: 'flv.svg',
        webm: 'webm.svg',
        wma: 'wma.svg',
        aif: 'aif.svg',
        aiff: 'aiff.svg',
        amr: 'amr.svg',
        flac: 'flac.svg',
        ogg: 'ogg.svg',
        oga: 'oga.svg',
        ogv: 'ogv.svg',
        opus: 'opus.svg',
        ra: 'ra.svg',
        rm: 'rm.svg',
        m4v: 'm4v.svg',
        m2ts: 'm2ts.svg',
        mts: 'mts.svg',
        vob: 'vob.svg',
        asf: 'asf.svg',
        // Office
        xls: 'xls.svg',
        xlsx: 'xlsx.svg',
        csv: 'csv.svg',
        ppt: 'ppt.svg',
        pptx: 'pptx.svg',
        doc: 'doc.svg',
        docx: 'docx.svg',
        docm: 'docm.svg',
        dot: 'dot.svg',
        dotx: 'dotx.svg',
        odt: 'odt.svg',
        ods: 'ods.svg',
        odp: 'odp.svg',
        odf: 'odf.svg',
        rtf: 'rtf.svg',
        pages: 'pages.svg',
        key: 'key.svg',
        numbers: 'xls.svg',
        pot: 'pot.svg',
        potx: 'potx.svg',
        pps: 'pps.svg',
        ppsx: 'ppsx.svg',
        pptm: 'pptm.svg',
        xlsb: 'xlsb.svg',
        xlsm: 'xlsm.svg',
        xlt: 'xlt.svg',
        xltx: 'xltx.svg',
        // Archive / binary
        zip: 'zip.svg',
        rar: 'rar.svg',
        '7z': '7z.svg',
        tar: 'tar.svg',
        gz: 'gz.svg',
        iso: 'iso.svg',
        dll: 'dll.svg',
        dat: 'dat.svg',
        bz2: 'bz2.svg',
        xz: 'xz.svg',
        lz: 'lz.svg',
        lzma: 'lzma.svg',
        tgz: 'tgz.svg',
        deb: 'deb.svg',
        rpm: 'rpm.svg',
        pkg: 'pkg.svg',
        dmg: 'dmg.svg',
        cab: 'cab.svg',
        msi: 'msi.svg',
        img: 'img.svg',
        bin: 'bin.svg',
        exe: 'exe.svg',
        com: 'com.svg',
        bat: 'bat.svg',
        cmd: 'bat.svg',
        sys: 'sys.svg',
        drv: 'drv.svg',
        // Images
        png: 'png.svg',
        jpg: 'jpg.svg',
        jpeg: 'jpeg.svg',
        gif: 'gif.svg',
        bmp: 'bmp.svg',
        svg: 'svg.svg',
        webp: 'webp.svg',
        tif: 'tif.svg',
        tiff: 'tiff.svg',
        raw: 'raw.svg',
        psd: 'psd.svg',
        ai: 'ai.svg',
        eps: 'eps.svg',
        indd: 'indd.svg',
        cdr: 'cdr.svg',
        ico: 'ico.svg',
        tga: 'tga.svg',
        dib: 'dib.svg',
        jp2: 'jp2.svg',
        j2k: 'j2k.svg',
        apng: 'apng.svg',
        avif: 'avif.svg',
        heic: 'heic.svg',
        heif: 'heif.svg',
        jxl: 'jxl.svg',
        // 3D / CAD
        '3ds': '3ds.svg',
        '3mf': '3mf.svg',
        fbx: 'fbx.svg',
        obj: 'obj.svg',
        ply: 'ply.svg',
        stl: 'stl.svg',
        step: 'step.svg',
        stp: 'stp.svg',
        iges: 'iges.svg',
        igs: 'igs.svg',
        glb: 'glb.svg',
        gltf: 'gltf.svg',
        dae: 'dae.svg',
        blend: 'blend.svg',
        sketch: 'sketch.svg',
        skp: 'skp.svg',
        usd: 'usd.svg',
        usda: 'usda.svg',
        usdc: 'usdc.svg',
        usdz: 'usdz.svg',
        vrml: 'vrml.svg',
        wrl: 'wrl.svg',
        cad: 'cad.svg',
        dwg: 'dwg.svg',
        dxf: 'dxf.svg',
        // Fonts
        ttf: 'ttf.svg',
        otf: 'otf.svg',
        woff: 'woff.svg',
        woff2: 'woff2.svg',
        eot: 'eot.svg',
        // Code / Development
        js: 'js.svg',
        mjs: 'js.svg',
        cjs: 'js.svg',
        ts: 'ts.svg',
        tsx: 'tsx.svg',
        jsx: 'jsx.svg',
        py: 'py.svg',
        sh: 'sh.svg',
        bash: 'sh.svg',
        zsh: 'sh.svg',
        ps1: 'ps1.svg',
        rb: 'rb.svg',
        go: 'go.svg',
        rs: 'rs.svg',
        java: 'java.svg',
        c: 'c.svg',
        cpp: 'cpp.svg',
        cc: 'cpp.svg',
        cxx: 'cpp.svg',
        h: 'h.svg',
        hpp: 'hpp.svg',
        cs: 'cs.svg',
        kt: 'kt.svg',
        swift: 'swift.svg',
        lua: 'lua.svg',
        pl: 'pl.svg',
        r: 'r.svg',
        m: 'm.svg',
        mm: 'm.svg',
        php: 'php.svg',
        asp: 'asp.svg',
        aspx: 'aspx.svg',
        vb: 'vb.svg',
        vbs: 'vbs.svg',
        dart: 'dart.svg',
        vue: 'vue.svg',
        // Data / Config
        json: 'json.svg',
        jsonl: 'jsonl.svg',
        yaml: 'yaml.svg',
        yml: 'yml.svg',
        toml: 'toml.svg',
        ini: 'ini.svg',
        env: 'ini.svg',
        xml: 'xml.svg',
        sql: 'sql.svg',
        sqlite: 'sqlite.svg',
        db: 'db.svg',
        dbf: 'dbf.svg',
        log: 'log.svg',
        tsv: 'tsv.svg',
        parquet: 'parquet.svg',
        geojson: 'geojson.svg',
        gpx: 'gpx.svg',
        kml: 'kml.svg',
        kmz: 'kmz.svg',
        shp: 'shp.svg',
        // Web / Documents
        html: 'html.svg',
        htm: 'html.svg',
        css: 'css.svg',
        scss: 'scss.svg',
        sass: 'sass.svg',
        less: 'css.svg',
        md: 'md.svg',
        markdown: 'md.svg',
        tex: 'tex.svg',
        txt: 'txt.svg',
        text: 'txt.svg',
        pdf: 'pdf.svg',
        epub: 'epub.svg',
        // Security / Certificates
        pem: 'pem.svg',
        crt: 'crt.svg',
        cer: 'cer.svg',
        p12: 'p12.svg',
        pfx: 'pfx.svg',
        csr: 'csr.svg',
        // Design / Media
        fla: 'fla.svg',
        fig: 'fig.svg',
        xd: 'xd.svg',
        // Mobile
        apk: 'apk.svg',
        app: 'app.svg',
        // Other
        ps: 'ps.svg',
    };

    return iconByExtension[ext] || 'txt.svg';
}

function getFileExtensionLabel(filename) {
    const normalized = String(filename || '').trim();
    const lastDotIndex = normalized.lastIndexOf('.');
    const ext = (lastDotIndex > 0 && lastDotIndex < normalized.length - 1)
        ? normalized.slice(lastDotIndex + 1).toUpperCase()
        : '';
    return ext || 'FILE';
}

function renderFileItem(file, folderType, skillId) {
    const fileName = String(file?.name || 'File');
    const iconName = getFileIcon(fileName);
    const extension = getFileExtensionLabel(fileName);
    const size = formatFileSize(Number(file?.size));
    return `
        <div class="admin-skill-file-item" data-filename="${escapeHtml(fileName)}" data-folder="${folderType}">
            <div class="admin-skill-file-icon">
                <img src="/assets/file_svgs/${iconName}" alt="${escapeHtml(extension)}" width="24" height="24" loading="lazy">
            </div>
            <div class="admin-skill-file-info">
                <p class="admin-skill-file-name">${escapeHtml(fileName)}</p>
                <p class="admin-skill-file-size">${size}</p>
            </div>
            <button type="button" class="admin-skill-file-delete" data-skill-id="${skillId}" data-folder="${folderType}" data-filename="${escapeHtml(fileName)}" title="${escapeHtml(t('admin_skill_file_delete_title', 'Delete file'))}">
                ${Icons?.trash || ''}
            </button>
        </div>
    `;
}

function renderFilesList(files, folderType, skillId) {
    if (!files || files.length === 0) return '';
    return files.map(file => renderFileItem(file, folderType, skillId)).join('');
}

function getAdminSkillIconPickerRefs(mode) {
    const isEdit = mode === 'edit';
    const trigger = isEdit ? AdminSkillsDOM.editIconButton : AdminSkillsDOM.createIconButton;
    return {
        picker: trigger?.closest('.svg-select'),
        trigger,
        preview: trigger,
        dropdown: isEdit ? AdminSkillsDOM.editIconDropdown : AdminSkillsDOM.createIconDropdown,
        svgGrid: isEdit ? AdminSkillsDOM.editIconGrid : AdminSkillsDOM.createIconGrid,
        colorGrid: isEdit ? AdminSkillsDOM.editColorRow : AdminSkillsDOM.createColorRow,
        saveButton: isEdit ? AdminSkillsDOM.editIconSave : AdminSkillsDOM.createIconSave,
        cancelButton: isEdit ? AdminSkillsDOM.editIconCancel : AdminSkillsDOM.createIconCancel,
    };
}

function createAdminSkillIconPicker(mode) {
    return adminSkillIconUtils.createWorkspaceIconPicker({
        state: AdminSkillsState[mode],
        refs: () => getAdminSkillIconPickerRefs(mode),
        iconOptions: ADMIN_SKILL_ICON_OPTIONS,
        colors: ADMIN_SKILL_ICON_COLORS,
        defaultIconId: ADMIN_SKILL_DEFAULT_ICON_ID,
        defaultColor: ADMIN_SKILL_DEFAULT_ICON_COLOR,
        translate: t,
        variant: 'svg-select',
    });
}

const AdminSkillIconPickers = {
    create: createAdminSkillIconPicker('create'),
    edit: createAdminSkillIconPicker('edit'),
};

function getAdminSkillIconPicker(mode) {
    return AdminSkillIconPickers[mode];
}

function getCreateFormSnapshot() {
    const icon = getAdminSkillIconPicker('create').getIconData();
    return JSON.stringify({
        name: String(AdminSkillsDOM.createName?.value || '').trim(),
        description: String(AdminSkillsDOM.createDescription?.value || '').trim(),
        content: String(AdminSkillsDOM.createContent?.value || '').trim(),
        iconId: icon.iconId,
        iconColor: icon.color,
    });
}

function getEditFormSnapshot() {
    const icon = getAdminSkillIconPicker('edit').getIconData();
    return JSON.stringify({
        skillId: String(AdminSkillsState.editingSkillId || ''),
        title: String(AdminSkillsDOM.editTitle?.value || '').trim(),
        description: String(AdminSkillsDOM.editDescription?.value || '').trim(),
        content: String(AdminSkillsDOM.editContent?.value || '').trim(),
        iconId: icon.iconId,
        iconColor: icon.color,
    });
}

function renderSkillCard(skill) {
    const iconData = parseSkillIcon(skill.icon);
    const iconColor = escapeHtml(iconData.color);
    const skillId = escapeHtml(skill.id);
    const preview = skill.content_preview ?? skill.content ?? '';
    return `
        <div class="admin-skill-card" data-skill-id="${skillId}">
            <div class="admin-skill-icon" style="background-color: ${iconColor}; color: white;">
                ${iconData.svg}
            </div>
            <div class="settings-row-left">
                <h4 class="settings-row-title">${escapeHtml(skill.title)}</h4>
                <p class="settings-row-desc two-lines">${escapeHtml(preview || t('admin_skills_no_instructions', 'No instructions'))}</p>
            </div>
            <div class="admin-skill-actions">
                <button type="button" class="om-button border cancel" data-skill-id="${skillId}">
                    ${Icons?.edit || ''}
                    ${t('admin_skills_edit_btn', 'Edit')}
                </button>
                <button type="button" class="om-button border danger-nofill" data-skill-id="${skillId}" data-skill-title="${escapeHtml(skill.title)}">
                    ${Icons?.trash || ''}
                    ${t('btn_delete', 'Delete')}
                </button>
            </div>
        </div>
    `;
}

function renderEmptyState(isFiltered = false) {
    const title = isFiltered
        ? t('admin_skills_empty_filtered_title', 'No matching skills')
        : t('admin_skills_empty_title', 'No managed skills yet');
    const description = isFiltered
        ? t('admin_skills_empty_filtered_desc', 'No skills match your current search. Try adjusting or clearing the filter.')
        : t('admin_skills_empty_desc', 'Create skills here to make them available to user groups.');

    return `
        <div class="user-notifications-empty provider-empty-state">
            <div class="user-notifications-empty-icon svg-stroke-thin">
                ${Icons?.lightning || ''}
            </div>
            <h3 class="user-notifications-empty-title">${title}</h3>
            <p class="user-notifications-empty-text">${description}</p>
        </div>
    `;
}

const AdminSkillsManager = {
    init() {
        if (AdminSkillsState.initialized) return;
        this.setupEventListeners();
        this.registerEscapeShortcut();
        this.registerUnsavedGuard();
        this.initIconPickers();
        this.setupFileUploadHandlers();
        AdminSkillsState.initialized = true;
    },

    initIconPickers() {
        Object.values(AdminSkillIconPickers).forEach((picker) => {
            picker.bind();
            picker.render();
            picker.updatePreview();
        });
    },

    setupFileUploadHandlers() {
        this.setupFileUpload('scripts', AdminSkillsDOM.editScriptsBtn, AdminSkillsDOM.editScriptsInput);
        this.setupFileUpload('references', AdminSkillsDOM.editReferencesBtn, AdminSkillsDOM.editReferencesInput);
        this.setupFileUpload('assets', AdminSkillsDOM.editAssetsBtn, AdminSkillsDOM.editAssetsInput);
    },

    setupFileUpload(folderType, buttonEl, inputEl) {
        if (!buttonEl || !inputEl) return;

        const uploadContainer = buttonEl.closest('.admin-skill-files-upload');
        const sectionEl = buttonEl.closest('.dashboard-card');
        const dropTarget = sectionEl || uploadContainer || buttonEl;
        let dragDepth = 0;

        const setDragState = (isDragging) => {
            uploadContainer?.classList.toggle('dragover', isDragging);
            sectionEl?.classList.toggle('dragover', isDragging);
        };

        const handleUploadFiles = async (files) => {
            if (!files || files.length === 0) return;

            const skill = AdminSkillsState.activeSkillContext;
            if (!skill) return;

            buttonEl.classList.add('uploading');
            const originalHtml = buttonEl.innerHTML;
            buttonEl.innerHTML = `${Icons?.loading || ''} ${escapeHtml(t('admin_loading_ellipsis', 'Loading...'))}`;

            try {
                const result = await AdminSkillsAPI.uploadSkillFiles(skill.id, folderType, files);
                if (result.uploaded && result.uploaded.length > 0) {
                    if (typeof showNotification === 'function') {
                        showNotification(
                            formatCountT('admin_skill_files_upload_success_count', 'Uploaded {count} file(s) successfully.', result.uploaded.length),
                            'success'
                        );
                    }
                    // Refresh only the open skill. The paginated list deliberately
                    // does not carry bundled-file manifests.
                    const updatedSkill = await AdminSkillsAPI.fetchSkill(skill.id);
                    AdminSkillsState.activeSkillContext = updatedSkill;
                    this.renderSkillFiles(updatedSkill);
                }
                if (result.errors && result.errors.length > 0) {
                    console.error('File upload errors:', result.errors);
                    if (typeof showNotification === 'function') {
                        showNotification(
                            formatCountT('admin_skill_files_upload_failed_count', 'Failed to upload {count} file(s).', result.errors.length),
                            'error'
                        );
                    }
                }
            } catch (error) {
                console.error('File upload failed:', error);
                if (typeof showNotification === 'function') {
                    showNotification(error.message || t('admin_skill_files_upload_failed', 'Failed to upload files.'), 'error');
                }
            } finally {
                buttonEl.classList.remove('uploading');
                buttonEl.innerHTML = originalHtml;
                inputEl.value = '';
                setDragState(false);
            }
        };

        const isFileDragEvent = (event) => {
            const types = event?.dataTransfer?.types;
            if (!types) return false;
            return Array.from(types).includes('Files');
        };

        const preventDragDefaults = (event) => {
            event.preventDefault();
            event.stopPropagation();
        };

        buttonEl.addEventListener('click', () => inputEl.click());

        inputEl.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files || []);
            await handleUploadFiles(files);
        });

        dropTarget?.addEventListener('dragenter', (event) => {
            if (!isFileDragEvent(event)) return;
            preventDragDefaults(event);
            dragDepth += 1;
            setDragState(true);
        });

        dropTarget?.addEventListener('dragover', (event) => {
            if (!isFileDragEvent(event)) return;
            preventDragDefaults(event);
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = 'copy';
            }
            setDragState(true);
        });

        dropTarget?.addEventListener('dragleave', (event) => {
            if (!isFileDragEvent(event)) return;
            preventDragDefaults(event);
            dragDepth = Math.max(0, dragDepth - 1);
            if (dragDepth === 0) {
                setDragState(false);
            }
        });

        dropTarget?.addEventListener('drop', async (event) => {
            if (!isFileDragEvent(event)) return;
            preventDragDefaults(event);
            dragDepth = 0;
            setDragState(false);
            const files = Array.from(event.dataTransfer?.files || []);
            await handleUploadFiles(files);
        });
    },

    registerEscapeShortcut() {
        if (AdminSkillsState.escapeRegistration || typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }
        AdminSkillsState.escapeRegistration = window.registerEscapeHandler({
            id: 'admin-skills-escape',
            priority: 140,
            isActive: () => (
                isPageActive(AdminSkillsDOM.createPage) || isPageActive(AdminSkillsDOM.editPage)
            ) && !Object.values(AdminSkillIconPickers).some((picker) => picker.state.isOpen),
            close: () => this.handleBackNavigation(),
        });
    },

    registerUnsavedGuard() {
        if (AdminSkillsState.unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
            return;
        }
        window.unsavedChangesManager.register({
            id: UNSAVED_GUARD_ID,
            priority: 170,
            isActive: () => isPageActive(AdminSkillsDOM.createPage) || isPageActive(AdminSkillsDOM.editPage),
            isDirty: () => this.hasPendingChanges(),
            discard: () => {
                if (isPageActive(AdminSkillsDOM.createPage)) {
                    AdminSkillsState.createInitialSnapshot = getCreateFormSnapshot();
                }
                if (isPageActive(AdminSkillsDOM.editPage)) {
                    AdminSkillsState.editInitialSnapshot = getEditFormSnapshot();
                }
            },
            getCopy: () => ({
                subtitle: t('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
            }),
        });
        AdminSkillsState.unsavedGuardRegistered = true;
    },

    hasPendingChanges() {
        if (isPageActive(AdminSkillsDOM.createPage)) {
            if (AdminSkillsState.createInitialSnapshot === null) return false;
            return getCreateFormSnapshot() !== AdminSkillsState.createInitialSnapshot;
        }
        if (isPageActive(AdminSkillsDOM.editPage)) {
            if (AdminSkillsState.editInitialSnapshot === null) return false;
            return getEditFormSnapshot() !== AdminSkillsState.editInitialSnapshot;
        }
        return false;
    },

    requestUnsavedConfirmation(onConfirm) {
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            const prompted = window.unsavedChangesManager.confirmIfNeeded({
                id: UNSAVED_GUARD_ID,
                onConfirm,
            });
            if (prompted) {
                return;
            }
        }
        onConfirm?.();
    },

    handleBackNavigation() {
        if (!isPageActive(AdminSkillsDOM.createPage) && !isPageActive(AdminSkillsDOM.editPage)) {
            return;
        }
        this.requestUnsavedConfirmation(() => this.showListPage());
    },

    async handleFileDelete(skillId, folderType, filename) {
        try {
            await AdminSkillsAPI.deleteSkillFile(skillId, folderType, filename);
            if (typeof showNotification === 'function') {
                showNotification(t('admin_skill_file_delete_success', 'File deleted successfully.'), 'success');
            }
            // File mutations only affect the open detail view, so avoid an
            // unrelated list-page request.
            const updatedSkill = await AdminSkillsAPI.fetchSkill(skillId);
            AdminSkillsState.activeSkillContext = updatedSkill;
            this.renderSkillFiles(updatedSkill);
        } catch (error) {
            console.error('File delete failed:', error);
            if (typeof showNotification === 'function') {
                showNotification(error.message || t('admin_skill_file_delete_failed', 'Failed to delete file.'), 'error');
            }
        }
    },

    renderSkillFiles(skill) {
        const files = skill.files || { scripts: [], references: [], assets: [] };
        
        if (AdminSkillsDOM.editScriptsList) {
            AdminSkillsDOM.editScriptsList.innerHTML = renderFilesList(files.scripts, 'scripts', skill.id);
        }
        if (AdminSkillsDOM.editReferencesList) {
            AdminSkillsDOM.editReferencesList.innerHTML = renderFilesList(files.references, 'references', skill.id);
        }
        if (AdminSkillsDOM.editAssetsList) {
            AdminSkillsDOM.editAssetsList.innerHTML = renderFilesList(files.assets, 'assets', skill.id);
        }
    },

    setupEventListeners() {
        // Create button
        AdminSkillsDOM.createBtn?.addEventListener('click', () => this.showCreatePage());
        if (AdminSkillsDOM.exportBtn && AdminSkillsDOM.exportBtn.dataset.bound !== 'true') {
            AdminSkillsDOM.exportBtn.addEventListener('click', handleExportAdminSkills);
            AdminSkillsDOM.exportBtn.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importBtn && AdminSkillsDOM.importBtn.dataset.bound !== 'true') {
            AdminSkillsDOM.importBtn.addEventListener('click', openImportModal);
            AdminSkillsDOM.importBtn.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importFileInput && AdminSkillsDOM.importFileInput.dataset.bound !== 'true') {
            AdminSkillsDOM.importFileInput.addEventListener('change', handleImportAdminSkillsFile);
            AdminSkillsDOM.importFileInput.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importBrowse && AdminSkillsDOM.importBrowse.dataset.bound !== 'true') {
            AdminSkillsDOM.importBrowse.addEventListener('click', (event) => {
                event.stopPropagation();
                AdminSkillsDOM.importFileInput?.click();
            });
            AdminSkillsDOM.importBrowse.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importDropzone && AdminSkillsDOM.importDropzone.dataset.bound !== 'true') {
            const dropzone = AdminSkillsDOM.importDropzone;
            dropzone.addEventListener('click', (event) => {
                if (event.target !== AdminSkillsDOM.importBrowse) AdminSkillsDOM.importFileInput?.click();
            });
            ['dragenter', 'dragover'].forEach((eventName) => {
                dropzone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropzone.classList.add('drag-over');
                });
            });
            ['dragleave', 'drop'].forEach((eventName) => {
                dropzone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropzone.classList.remove('drag-over');
                });
            });
            dropzone.addEventListener('drop', (event) => {
                const droppedFiles = event.dataTransfer?.files;
                if (droppedFiles?.length) {
                    handleImportAdminSkillsFile({ target: { files: droppedFiles, value: '' } });
                }
            });
            dropzone.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importTabFiles && AdminSkillsDOM.importTabFiles.dataset.bound !== 'true') {
            AdminSkillsDOM.importTabFiles.addEventListener('click', () => setImportMode('files'));
            AdminSkillsDOM.importTabFiles.addEventListener('keydown', handleImportTabKeydown);
            AdminSkillsDOM.importTabFiles.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importTabPaste && AdminSkillsDOM.importTabPaste.dataset.bound !== 'true') {
            AdminSkillsDOM.importTabPaste.addEventListener('click', () => setImportMode('paste'));
            AdminSkillsDOM.importTabPaste.addEventListener('keydown', handleImportTabKeydown);
            AdminSkillsDOM.importTabPaste.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importPasteInput && AdminSkillsDOM.importPasteInput.dataset.bound !== 'true') {
            AdminSkillsDOM.importPasteInput.addEventListener('input', refreshPastedAdminSkillPreview);
            AdminSkillsDOM.importPasteInput.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importPasteClear && AdminSkillsDOM.importPasteClear.dataset.bound !== 'true') {
            AdminSkillsDOM.importPasteClear.addEventListener('click', () => {
                if (AdminSkillsDOM.importPasteInput) AdminSkillsDOM.importPasteInput.value = '';
                refreshPastedAdminSkillPreview();
                AdminSkillsDOM.importPasteInput?.focus();
            });
            AdminSkillsDOM.importPasteClear.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importOverlay && AdminSkillsDOM.importOverlay.dataset.bound !== 'true') {
            AdminSkillsDOM.importOverlay.addEventListener('click', (event) => {
                if (event.target === AdminSkillsDOM.importOverlay) {
                    closeImportModal();
                }
            });
            AdminSkillsDOM.importOverlay.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importClose && AdminSkillsDOM.importClose.dataset.bound !== 'true') {
            AdminSkillsDOM.importClose.addEventListener('click', closeImportModal);
            AdminSkillsDOM.importClose.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importCancel && AdminSkillsDOM.importCancel.dataset.bound !== 'true') {
            AdminSkillsDOM.importCancel.addEventListener('click', closeImportModal);
            AdminSkillsDOM.importCancel.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importConfirm && AdminSkillsDOM.importConfirm.dataset.bound !== 'true') {
            AdminSkillsDOM.importConfirm.addEventListener('click', submitSelectedSkillImports);
            AdminSkillsDOM.importConfirm.dataset.bound = 'true';
        }
        if (AdminSkillsDOM.importSelectAll && AdminSkillsDOM.importSelectAll.dataset.bound !== 'true') {
            AdminSkillsDOM.importSelectAll.addEventListener('change', toggleSelectAllImports);
            AdminSkillsDOM.importSelectAll.dataset.bound = 'true';
        }

        // Create form
        AdminSkillsDOM.createCancel?.addEventListener('click', () => this.handleBackNavigation());
        AdminSkillsDOM.createForm?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleCreate();
        });

        // Edit form
        AdminSkillsDOM.editCancel?.addEventListener('click', () => this.handleBackNavigation());
        AdminSkillsDOM.editForm?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleUpdate();
        });

        // Skills list click delegation
        AdminSkillsDOM.skillsList?.addEventListener('click', (e) => {
            const deleteBtn = e.target.closest('.om-button.border.danger-nofill');
            if (deleteBtn) {
                const skillId = deleteBtn.dataset.skillId;
                if (skillId) {
                    const title = deleteBtn.dataset.skillTitle || t('admin_skills_item_fallback', 'this skill');
                    this.confirmDeleteSkill(skillId, title);
                }
                return;
            }
            const editBtn = e.target.closest('.om-button.border.cancel');
            if (editBtn) {
                const skillId = editBtn.dataset.skillId;
                if (skillId) void this.showEditPage(skillId, editBtn);
            }
        });

        // File delete event delegation for edit form
        AdminSkillsDOM.editForm?.addEventListener('click', (e) => {
            const deleteBtn = e.target.closest('.admin-skill-file-delete');
            if (deleteBtn) {
                const skillId = deleteBtn.dataset.skillId;
                const folder = deleteBtn.dataset.folder;
                const filename = deleteBtn.dataset.filename;
                if (skillId && folder && filename) {
                    this.handleFileDelete(skillId, folder, filename);
                }
            }
        });

        // Search
        if (AdminSkillsDOM.searchInput && AdminSkillsDOM.searchInput.dataset.bound !== 'true') {
            AdminSkillsDOM.searchInput.addEventListener('input', () => {
                this.updateSearchClearVisibility();
                AdminSkillsState.search = AdminSkillsDOM.searchInput.value.trim();
                AdminSkillsState.page = 1;
                window.clearTimeout(AdminSkillsState.searchTimer);
                // Stop an older page or query from replacing the list during
                // the debounce window for the newly entered search.
                AdminSkillsState.listRequestController?.abort();

                // Clearing the query should feel immediate. Non-empty searches
                // are debounced to avoid one backend request per keystroke.
                if (!AdminSkillsState.search) {
                    void this.loadSkills();
                    return;
                }
                AdminSkillsState.searchTimer = window.setTimeout(() => {
                    void this.loadSkills();
                }, ADMIN_SKILLS_SEARCH_DEBOUNCE_MS);
            });
            AdminSkillsDOM.searchInput.dataset.bound = 'true';
            AdminSkillsState.search = AdminSkillsDOM.searchInput.value.trim();
            this.updateSearchClearVisibility();
        }

        if (AdminSkillsDOM.searchClear && AdminSkillsDOM.searchClear.dataset.bound !== 'true') {
            AdminSkillsDOM.searchClear.addEventListener('click', (event) => {
                event.preventDefault();
                if (!AdminSkillsDOM.searchInput) {
                    return;
                }
                AdminSkillsDOM.searchInput.value = '';
                AdminSkillsDOM.searchInput.focus();
                AdminSkillsDOM.searchInput.dispatchEvent(new Event('input', { bubbles: true }));
            });
            AdminSkillsDOM.searchClear.dataset.bound = 'true';
            this.updateSearchClearVisibility();
        }

        if (AdminSkillsDOM.paginationPrev && AdminSkillsDOM.paginationPrev.dataset.bound !== 'true') {
            AdminSkillsDOM.paginationPrev.addEventListener('click', () => {
                void this.goToPage(AdminSkillsState.page - 1);
            });
            AdminSkillsDOM.paginationPrev.dataset.bound = 'true';
        }

        if (AdminSkillsDOM.paginationNext && AdminSkillsDOM.paginationNext.dataset.bound !== 'true') {
            AdminSkillsDOM.paginationNext.addEventListener('click', () => {
                void this.goToPage(AdminSkillsState.page + 1);
            });
            AdminSkillsDOM.paginationNext.dataset.bound = 'true';
        }

        if (AdminSkillsDOM.paginationPages && AdminSkillsDOM.paginationPages.dataset.bound !== 'true') {
            AdminSkillsDOM.paginationPages.addEventListener('click', (event) => {
                const pageButton = event.target.closest('[data-admin-skills-page]');
                const page = Number(pageButton?.dataset.adminSkillsPage);
                if (Number.isInteger(page)) {
                    void this.goToPage(page);
                }
            });
            AdminSkillsDOM.paginationPages.dataset.bound = 'true';
        }

        AdminSkillsDOM.deleteCancel?.addEventListener('click', () => this.closeDeleteOverlay());
        AdminSkillsDOM.deleteOverlay?.addEventListener('click', (e) => {
            if (e.target === AdminSkillsDOM.deleteOverlay) {
                this.closeDeleteOverlay();
            }
        });
    },

    async loadSkills({ scrollToList = false } = {}) {
        const list = AdminSkillsDOM.skillsList;
        if (!list) return;

        AdminSkillsState.listRequestController?.abort();
        const controller = new AbortController();
        const requestSequence = AdminSkillsState.listRequestSequence + 1;
        AdminSkillsState.listRequestController = controller;
        AdminSkillsState.listRequestSequence = requestSequence;
        AdminSkillsState.isLoading = true;
        list.setAttribute('aria-busy', 'true');
        list.classList.add('is-page-loading');
        this.renderPagination();

        // Retain the rendered cards during page navigation. Replacing ten
        // cards with a short loading placeholder temporarily collapses the
        // scroll container and produces a visible jump near the page footer.
        if (!AdminSkillsState.skills.length) {
            list.innerHTML = `<div class="user-notifications-empty provider-empty-state"><p class="user-notifications-empty-text">${escapeHtml(t('admin_skills_loading', 'Loading skills...'))}</p></div>`;
        }
        if (scrollToList) {
            if (typeof window.scrollAdminPaginatedListToStart === 'function') {
                window.scrollAdminPaginatedListToStart(list);
            } else {
                list.scrollIntoView({ block: 'start' });
            }
        }

        try {
            const response = await AdminSkillsAPI.fetchSkills({
                page: AdminSkillsState.page,
                pageSize: AdminSkillsState.pageSize,
                search: AdminSkillsState.search,
                signal: controller.signal,
            });
            if (requestSequence !== AdminSkillsState.listRequestSequence) return;

            AdminSkillsState.skills = Array.isArray(response?.items) ? response.items : [];
            AdminSkillsState.page = Number(response?.page) || 1;
            AdminSkillsState.pageSize = Number(response?.page_size) || ADMIN_SKILLS_PAGE_SIZE;
            AdminSkillsState.total = Number(response?.total) || 0;
            AdminSkillsState.totalPages = Number(response?.total_pages) || 0;
            AdminSkillsState.isLoading = false;
            this.renderSkills();
        } catch (error) {
            if (error?.name === 'AbortError') return;
            if (requestSequence !== AdminSkillsState.listRequestSequence) return;
            console.error('Failed to load managed skills:', error);
            list.innerHTML = `<div class="user-notifications-empty provider-empty-state"><p class="user-notifications-empty-text">${escapeHtml(t('admin_skills_load_failed_retry', 'Failed to load skills. Please try again.'))}</p></div>`;
            if (AdminSkillsDOM.pagination) AdminSkillsDOM.pagination.hidden = true;
            if (typeof showNotification === 'function') showNotification(t('admin_skills_load_failed', 'Failed to load managed skills.'), 'error');
        } finally {
            if (requestSequence === AdminSkillsState.listRequestSequence) {
                AdminSkillsState.isLoading = false;
                list.setAttribute('aria-busy', 'false');
                list.classList.remove('is-page-loading');
                AdminSkillsState.listRequestController = null;
            }
        }
    },

    renderSkills() {
        const list = AdminSkillsDOM.skillsList;
        if (!list) return;

        if (AdminSkillsState.skills.length === 0) {
            list.innerHTML = renderEmptyState(Boolean(AdminSkillsState.search));
        } else {
            list.innerHTML = AdminSkillsState.skills.map(renderSkillCard).join('');
        }

        this.renderPagination();
    },

    renderPagination() {
        const pagination = AdminSkillsDOM.pagination;
        const pages = AdminSkillsDOM.paginationPages;
        if (!pagination || !pages) return;

        // A pager adds no useful controls for a zero- or one-page result set.
        pagination.hidden = AdminSkillsState.totalPages <= 1;
        if (pagination.hidden) {
            pages.replaceChildren();
            return;
        }

        const start = (AdminSkillsState.page - 1) * AdminSkillsState.pageSize + 1;
        const end = Math.min(AdminSkillsState.page * AdminSkillsState.pageSize, AdminSkillsState.total);
        if (AdminSkillsDOM.paginationInfo) {
            AdminSkillsDOM.paginationInfo.textContent = formatT(
                'admin_skills_pagination_showing',
                'Showing {start}–{end} of {total} skills',
                { start, end, total: AdminSkillsState.total }
            );
        }
        if (AdminSkillsDOM.paginationPrev) {
            AdminSkillsDOM.paginationPrev.disabled = AdminSkillsState.isLoading || AdminSkillsState.page <= 1;
        }
        if (AdminSkillsDOM.paginationNext) {
            AdminSkillsDOM.paginationNext.disabled = AdminSkillsState.isLoading || AdminSkillsState.page >= AdminSkillsState.totalPages;
        }

        pages.replaceChildren();
        generateAdminSkillPageNumbers(AdminSkillsState.page, AdminSkillsState.totalPages).forEach((page) => {
            if (page === '…') {
                const ellipsis = document.createElement('span');
                ellipsis.className = 'user-notifications-pagination-ellipsis';
                ellipsis.textContent = '…';
                ellipsis.setAttribute('aria-hidden', 'true');
                pages.appendChild(ellipsis);
                return;
            }

            const button = document.createElement('button');
            button.type = 'button';
            button.className = `user-notifications-pagination-page${page === AdminSkillsState.page ? ' active' : ''}`;
            button.dataset.adminSkillsPage = String(page);
            button.textContent = String(page);
            button.setAttribute(
                'aria-label',
                formatT('admin_skills_page_aria', 'Page {page}', { page })
            );
            button.disabled = AdminSkillsState.isLoading;
            if (page === AdminSkillsState.page) {
                button.setAttribute('aria-current', 'page');
                button.disabled = true;
            }
            pages.appendChild(button);
        });
    },

    async goToPage(page) {
        if (
            AdminSkillsState.isLoading
            || !Number.isInteger(page)
            || page < 1
            || page > AdminSkillsState.totalPages
            || page === AdminSkillsState.page
        ) {
            return;
        }
        AdminSkillsState.page = page;
        await this.loadSkills({ scrollToList: true });
    },

    updateSearchClearVisibility() {
        if (!AdminSkillsDOM.searchInput || !AdminSkillsDOM.searchClear) {
            return;
        }
        const hasValue = Boolean(AdminSkillsDOM.searchInput.value && AdminSkillsDOM.searchInput.value.trim().length);
        AdminSkillsDOM.searchClear.hidden = !hasValue;
    },

    resetListSearch() {
        window.clearTimeout(AdminSkillsState.searchTimer);
        AdminSkillsState.searchTimer = null;
        AdminSkillsState.search = '';
        AdminSkillsState.page = 1;
        if (AdminSkillsDOM.searchInput) {
            AdminSkillsDOM.searchInput.value = '';
        }
        this.updateSearchClearVisibility();
    },

    showListPage() {
        // An explicit return to the list supersedes any detail navigation that
        // may still be waiting on the network, even when the list is already
        // the active page and the page router emits no activation event.
        cancelAdminSkillDetailRequest();
        const wasListPageActive = isPageActive(AdminSkillsDOM.listPage);
        if (typeof showPage === 'function') {
            showPage('skills');
        }
        // Page activation already triggers a refresh. If the list was already
        // active, no activation event fires, so refresh it directly.
        if (wasListPageActive) {
            void this.loadSkills();
        }
    },

    showCreatePage() {
        AdminSkillsState.editingSkillId = null;
        AdminSkillsState.activeSkillContext = null;

        // Reset form
        if (AdminSkillsDOM.createName) AdminSkillsDOM.createName.value = '';
        if (AdminSkillsDOM.createDescription) AdminSkillsDOM.createDescription.value = '';
        if (AdminSkillsDOM.createContent) AdminSkillsDOM.createContent.value = '';

        // Reset icon picker
        getAdminSkillIconPicker('edit').close();
        const createIconPicker = getAdminSkillIconPicker('create');
        createIconPicker.reset(ADMIN_SKILL_DEFAULT_ICON_ID, ADMIN_SKILL_DEFAULT_ICON_COLOR);
        createIconPicker.render();
        createIconPicker.updatePreview();

        AdminSkillsState.createInitialSnapshot = getCreateFormSnapshot();

        if (typeof showPage === 'function') {
            showPage('skills-create');
        }
    },

    async showEditPage(skillId, triggerButton = null) {
        cancelAdminSkillDetailRequest();
        const controller = new AbortController();
        AdminSkillsState.detailRequestController = controller;
        const wasDisabled = Boolean(triggerButton?.disabled);
        if (triggerButton) {
            triggerButton.disabled = true;
            triggerButton.setAttribute('aria-busy', 'true');
        }

        try {
            // List cards intentionally contain summaries only. Fetch the complete
            // skill and its bundled-file manifest when Edit is actually opened.
            const skill = await AdminSkillsAPI.fetchSkill(skillId, { signal: controller.signal });
            if (AdminSkillsState.detailRequestController !== controller) return;

            AdminSkillsState.editingSkillId = skillId;
            AdminSkillsState.activeSkillContext = skill;

            if (AdminSkillsDOM.editId) AdminSkillsDOM.editId.value = skillId;
            if (AdminSkillsDOM.editTitle) AdminSkillsDOM.editTitle.value = skill.title || '';
            if (AdminSkillsDOM.editDescription) AdminSkillsDOM.editDescription.value = skill.description || '';
            if (AdminSkillsDOM.editContent) AdminSkillsDOM.editContent.value = skill.content || '';

            getAdminSkillIconPicker('create').close();
            const editIconPicker = getAdminSkillIconPicker('edit');
            editIconPicker.reset(skill.icon, ADMIN_SKILL_DEFAULT_ICON_COLOR);
            editIconPicker.render();
            editIconPicker.updatePreview();

            this.renderSkillFiles(skill);
            AdminSkillsState.editInitialSnapshot = getEditFormSnapshot();

            if (typeof showPage === 'function') {
                showPage('skills-edit', { history: 'none' });
            }
        } catch (error) {
            if (error?.name === 'AbortError') return;
            console.error('Failed to load managed skill details:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    error.message || t('admin_skills_detail_load_failed', 'Failed to load skill details.'),
                    'error'
                );
            }
        } finally {
            if (triggerButton) {
                triggerButton.disabled = wasDisabled;
                triggerButton.removeAttribute('aria-busy');
            }
            if (AdminSkillsState.detailRequestController === controller) {
                AdminSkillsState.detailRequestController = null;
            }
        }
    },

    async handleCreate() {
        const name = AdminSkillsDOM.createName?.value.trim();
        const description = AdminSkillsDOM.createDescription?.value.trim() || t('admin_skills_default_description', 'Managed skill');
        const content = AdminSkillsDOM.createContent?.value.trim();

        if (!name) {
            if (typeof showNotification === 'function') showNotification(t('admin_skill_name_required', 'Please enter a skill name.'), 'error');
            AdminSkillsDOM.createName?.focus();
            return;
        }

        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
            if (typeof showNotification === 'function') showNotification(t('admin_skill_name_invalid', 'Name must use lowercase letters, numbers, and hyphens only.'), 'error');
            AdminSkillsDOM.createName?.focus();
            return;
        }

        const iconPicker = getAdminSkillIconPicker('create');
        iconPicker.close();
        const iconJson = iconPicker.serialize();

        const btn = AdminSkillsDOM.createSubmit;
        if (btn) { btn.disabled = true; btn.textContent = t('admin_creating_ellipsis', 'Creating...'); }

        try {
            await AdminSkillsAPI.createSkill({
                name,
                description,
                content: content || '',
                icon: iconJson,
            });
            AdminSkillsState.createInitialSnapshot = getCreateFormSnapshot();
            if (typeof showNotification === 'function') showNotification(t('admin_skills_create_success', 'Skill created successfully.'), 'success');
            this.resetListSearch();
            this.showListPage();
        } catch (error) {
            console.error('Failed to create skill:', error);
            if (typeof showNotification === 'function') showNotification(error.message || t('admin_skills_create_failed', 'Failed to create skill.'), 'error');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = t('skills_create_btn', 'Create Skill'); }
        }
    },

    async handleUpdate() {
        const skillId = AdminSkillsState.editingSkillId;
        if (!skillId) return;

        const title = AdminSkillsDOM.editTitle?.value.trim();
        const description = AdminSkillsDOM.editDescription?.value.trim();
        const content = AdminSkillsDOM.editContent?.value.trim();

        if (!title) {
            if (typeof showNotification === 'function') showNotification(t('admin_skill_title_required', 'Please enter a skill title.'), 'error');
            AdminSkillsDOM.editTitle?.focus();
            return;
        }

        if (!description) {
            if (typeof showNotification === 'function') showNotification(t('admin_field_required', 'This field is required'), 'error');
            AdminSkillsDOM.editDescription?.focus();
            return;
        }

        const iconPicker = getAdminSkillIconPicker('edit');
        iconPicker.close();
        const iconJson = iconPicker.serialize();

        const btn = AdminSkillsDOM.editSubmit;
        if (btn) { btn.disabled = true; btn.textContent = t('admin_saving', 'Saving...'); }

        try {
            await AdminSkillsAPI.updateSkill(skillId, {
                title,
                description,
                content: content || '',
                icon: iconJson,
            });
            AdminSkillsState.editInitialSnapshot = getEditFormSnapshot();
            if (typeof showNotification === 'function') showNotification(t('admin_skills_update_success', 'Skill updated successfully.'), 'success');
            this.showListPage();
        } catch (error) {
            console.error('Failed to update skill:', error);
            if (typeof showNotification === 'function') showNotification(error.message || t('admin_skills_update_failed', 'Failed to update skill.'), 'error');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = t('btn_save_changes', 'Save Changes'); }
        }
    },

    confirmDeleteSkill(skillId, title) {
        AdminSkillsState.pendingDeleteSkillId = skillId;
        const overlay = document.getElementById('deleteAdminSkillOverlay');
        const message = document.getElementById('deleteAdminSkillMessage');
        const primaryBtn = document.getElementById('deleteAdminSkillPrimaryButton');
        if (message) {
            message.textContent = formatT(
                'admin_skills_delete_confirm_named',
                'Are you sure you want to delete "{title}"? This action cannot be undone.',
                { title }
            );
        }
        if (overlay) {
            overlay.hidden = false;
            overlay.setAttribute('aria-hidden', 'false');
        }
        primaryBtn?.addEventListener('click', this.handleDeleteConfirm, { once: true });
    },

    async handleDeleteConfirm() {
        const skillId = AdminSkillsState.pendingDeleteSkillId;
        if (!skillId) {
            AdminSkillsManager.closeDeleteOverlay();
            return;
        }

        const primaryBtn = document.getElementById('deleteAdminSkillPrimaryButton');
        if (primaryBtn) {
            primaryBtn.disabled = true;
            primaryBtn.querySelector('#deleteAdminSkillPrimaryText')?.classList.add('loading');
        }

        try {
            await AdminSkillsAPI.deleteSkill(skillId);
            if (typeof showNotification === 'function') showNotification(t('admin_skills_delete_success', 'Skill deleted successfully.'), 'success');
            AdminSkillsManager.closeDeleteOverlay();
            await AdminSkillsManager.loadSkills();
        } catch (error) {
            console.error('Failed to delete skill:', error);
            if (typeof showNotification === 'function') showNotification(error.message || t('admin_skills_delete_failed', 'Failed to delete skill.'), 'error');
            AdminSkillsManager.closeDeleteOverlay();
        } finally {
            if (primaryBtn) {
                primaryBtn.disabled = false;
                primaryBtn.querySelector('#deleteAdminSkillPrimaryText')?.classList.remove('loading');
            }
            AdminSkillsState.pendingDeleteSkillId = null;
        }
    },

    closeDeleteOverlay() {
        const overlay = document.getElementById('deleteAdminSkillOverlay');
        const primaryBtn = document.getElementById('deleteAdminSkillPrimaryButton');
        if (overlay) {
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
        }
        if (primaryBtn) {
            primaryBtn.disabled = false;
            primaryBtn.querySelector('#deleteAdminSkillPrimaryText')?.classList.remove('loading');
        }
        AdminSkillsState.pendingDeleteSkillId = null;
    },

    // ========================================================================
    // Marketplace Import Methods
    // ========================================================================

    checkForMarketplaceImport() {
        const urlParams = new URLSearchParams(window.location.search);
        const isMarketplaceImport = urlParams.get('marketplace_import') === '1';
        
        if (!isMarketplaceImport) return false;
        
        const encodedData = urlParams.get('skill_data');
        const timestamp = urlParams.get('ts');
        
        if (!encodedData || !timestamp) {
            console.warn('Admin Marketplace import: Missing required parameters');
            this.clearMarketplaceImportParams();
            return false;
        }
        
        // Validate timestamp (must be recent and not from the future).
        const importTime = parseInt(timestamp, 10);
        const now = Date.now();
        const maxAge = 30 * 60 * 1000; // 30 minutes
        const maxFutureSkew = 5 * 60 * 1000; // 5 minutes
        
        if (isNaN(importTime) || now - importTime > maxAge || importTime - now > maxFutureSkew) {
            console.warn('Admin Marketplace import: Import link has expired');
            if (typeof showNotification === 'function') {
                showNotification(t('admin_marketplace_import_expired', 'This import link has expired. Please request a new import link.'), 'warning');
            }
            this.clearMarketplaceImportParams();
            return false;
        }
        
        // Decode and parse skill data
        try {
            const jsonString = decodeURIComponent(atob(encodedData));
            const skillData = JSON.parse(jsonString);
            
            // Validate required fields
            if (!skillData.name || !skillData.content) {
                throw new Error('Missing required skill fields');
            }
            
            // Sanitize the data
            AdminSkillsState.marketplaceImportData = {
                name: this.sanitizeSkillName(skillData.name),
                description: this.sanitizeText(skillData.description || ''),
                content: this.sanitizeText(skillData.content || ''),
                category: this.sanitizeText(skillData.category || 'general'),
                version: this.sanitizeText(skillData.version || '1.0.0'),
                author: this.sanitizeText(skillData.author || t('admin_marketplace_import_unknown_author', 'Unknown source')),
                source: 'url_import',
                sourceUrl: this.sanitizeUrl(skillData.sourceUrl || ''),
                importedAt: skillData.importedAt || new Date().toISOString(),
            };
            
            // Show the import confirmation modal
            this.showMarketplaceImportModal();
            return true;
            
        } catch (error) {
            console.error('Admin Marketplace import: Failed to parse skill data', error);
            if (typeof showNotification === 'function') {
                showNotification(t('admin_marketplace_import_parse_failed', 'Failed to parse skill data. Please try again.'), 'error');
            }
            this.clearMarketplaceImportParams();
            return false;
        }
    },


    sanitizeSkillName(name) {
        // Convert to lowercase, replace spaces with hyphens, remove invalid chars
        return String(name || '')
            .toLowerCase()
            .trim()
            .replace(/\s+/g, '-')
            .replace(/[^a-z0-9-]/g, '')
            .replace(/-+/g, '-')
            .replace(/^-|-$/g, '')
            .substring(0, 64) || 'imported-skill';
    },

    sanitizeText(text) {
        // Basic text sanitization - remove script tags and limit length
        return String(text || '')
            .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
            .substring(0, 10000);
    },

    sanitizeUrl(url) {
        try {
            const parsed = new URL(url);
            if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
                return parsed.origin;
            }
        } catch {}
        return '';
    },

    clearMarketplaceImportParams() {
        // Remove marketplace import params from URL without page reload
        const url = new URL(window.location.href);
        url.searchParams.delete('marketplace_import');
        url.searchParams.delete('skill_data');
        url.searchParams.delete('ts');
        url.searchParams.delete('sig');
        window.history.replaceState({}, '', url.pathname + url.search);
    },

    showMarketplaceImportModal() {
        const data = AdminSkillsState.marketplaceImportData;
        if (!data) return;

        // Create modal if it doesn't exist
        let overlay = document.getElementById('adminMarketplaceImportOverlay');
        if (!overlay) {
            overlay = window.DeleteWarningModal?.create({
                id: 'adminMarketplaceImportOverlay',
                cardClass: 'shared-modal-card',
                cardStyle: '--shared-modal-width: 540px;',
                ariaLabelledby: 'adminMarketplaceImportTitle',
                contentHtml: `
                    <header class="shared-modal-header shared-modal-header--main">
                        <div class="shared-modal-heading" style="display: flex; align-items: flex-start; gap: 16px;">
                            <div id="adminMarketplaceImportIcon" class="svg-stroke-white svg-stroke-thin svg-size-24" style="width: 48px; height: 48px; border-radius: 12px; background: var(--accent-color); display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
                                ${Icons?.lightning || ''}
                            </div>
                            <div style="flex: 1; min-width: 0;">
                                <p class="svg-size-14" style="font-size: 12px; color: var(--accent-color); font-weight: 500; margin: 0 0 4px; display: flex; align-items: center; gap: 6px;">
                                    ${Icons?.globe || ''}
                                    ${t('admin_marketplace_import_source', 'Unverified skill import')}
                                </p>
                                <h3 class="delete-warning-card-title shared-modal-title" id="adminMarketplaceImportTitle" style="margin: 0; text-align: left;">${t('admin_marketplace_import_skill_name', 'Skill Name')}</h3>
                                <p id="adminMarketplaceImportMeta" style="font-size: 12px; color: var(--text-tertiary); margin: 4px 0 0;"></p>
                            </div>
                        </div>
                    </header>
                    <div class="shared-modal-body">
                        <div id="adminMarketplaceImportDescription" style="color: var(--text-color-secondary); font-size: 14px; margin-bottom: 16px;"></div>
                        <div style="margin-bottom: 16px;">
                            <label style="font-size: 12px; font-weight: 500; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: block;">${t('admin_marketplace_import_preview_label', 'Skill Content Preview')}</label>
                            <div id="adminMarketplaceImportPreview" style="background: var(--bg-tertiary); border-radius: 8px; padding: 12px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 13px; white-space: pre-wrap; word-break: break-word;"></div>
                        </div>
                        <div class="svg-stroke-accent svg-size-16 svg-flex-shrink" style="display: flex; align-items: center; gap: 8px; padding: 12px; background: var(--bg-tertiary); border-radius: 8px;">
                            ${Icons?.security || ''}
                            <p style="font-size: 13px; color: var(--text-color-secondary); margin: 0;">${t('admin_marketplace_import_notice', 'This import link is not verified by Omlorix. Only import skills from sources you trust; the managed skill can be assigned to user groups after import.')}</p>
                        </div>
                    </div>
                `,
                actions: [
                    { id: 'adminMarketplaceImportCancelBtn', role: 'cancel', variant: 'cancel', text: t('common_cancel', 'Cancel') },
                    { id: 'adminMarketplaceImportConfirmBtn', variant: 'submit', className: 'svg-size-16', text: t('admin_marketplace_import_button', 'Import as Managed Skill') },
                ],
            });
            if (!overlay) return;
            document.body.appendChild(overlay);
        }

        // Populate modal content
        const titleEl = document.getElementById('adminMarketplaceImportTitle');
        const metaEl = document.getElementById('adminMarketplaceImportMeta');
        const descEl = document.getElementById('adminMarketplaceImportDescription');
        const previewEl = document.getElementById('adminMarketplaceImportPreview');
        const iconEl = document.getElementById('adminMarketplaceImportIcon');

        if (titleEl) titleEl.textContent = data.name.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        if (metaEl) {
            const metaParts = [];
            if (data.category) metaParts.push(data.category);
            if (data.version) metaParts.push(`v${data.version}`);
            if (data.author) metaParts.push(`by ${data.author}`);
            metaEl.textContent = metaParts.join(' • ');
        }
        if (descEl) {
            descEl.textContent = data.description || t('admin_marketplace_import_no_description', 'No description provided.');
        }
        if (previewEl) {
            const preview = data.content.length > 500 
                ? data.content.substring(0, 500) + '...' 
                : data.content;
            previewEl.textContent = preview;
        }

        // Set icon based on category
        if (iconEl) {
            const categoryIconMap = {
                development: Icons?.code,
                rendering: Icons?.grid,
                design: Icons?.globe,
                language: Icons?.globe,
                general: Icons?.layers,
            };
            const iconSvg = categoryIconMap[data.category] || categoryIconMap.general;
            iconEl.className = 'svg-stroke-white svg-stroke-thin svg-size-24';
            iconEl.innerHTML = iconSvg || '';
        }

        // Show overlay
        AdminSkillsState.marketplaceImportLastFocusedElement = document.activeElement;
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => document.getElementById('adminMarketplaceImportCancelBtn')?.focus());

        // Setup event listeners (only once)
        if (!AdminSkillsState.marketplaceImportModalInitialized) {
            document.getElementById('adminMarketplaceImportCancelBtn')?.addEventListener('click', () => this.hideMarketplaceImportModal());
            document.getElementById('adminMarketplaceImportConfirmBtn')?.addEventListener('click', () => this.confirmMarketplaceImport());
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideMarketplaceImportModal(); });
            
            // Escape key to close
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && !overlay.hidden) {
                    this.hideMarketplaceImportModal();
                }
            });
            
            AdminSkillsState.marketplaceImportModalInitialized = true;
        }
    },

    hideMarketplaceImportModal() {
        const overlay = document.getElementById('adminMarketplaceImportOverlay');
        if (overlay) {
            overlay.setAttribute('aria-hidden', 'true');
            overlay.hidden = true;
        }
        AdminSkillsState.marketplaceImportLastFocusedElement?.focus?.();
        AdminSkillsState.marketplaceImportLastFocusedElement = null;
        AdminSkillsState.marketplaceImportData = null;
        this.clearMarketplaceImportParams();
    },

    async confirmMarketplaceImport() {
        const data = AdminSkillsState.marketplaceImportData;
        if (!data) return;

        const confirmBtn = document.getElementById('adminMarketplaceImportConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.classList.add('svg-size-16', 'svg-animate-spin');
            confirmBtn.innerHTML = `${Icons?.loading || ''} ${escapeHtml(t('admin_importing_ellipsis', 'Importing...'))}`;
        }

        try {
            // Build icon JSON
            const iconJson = JSON.stringify({
                preset: ADMIN_SKILL_DEFAULT_ICON_ID,
                color: ADMIN_SKILL_DEFAULT_ICON_COLOR,
            });

            // Create the skill via API
            await AdminSkillsAPI.createSkill({
                name: data.name,
                description: data.description || t('admin_marketplace_imported_description', 'Imported from an unverified link'),
                content: data.content,
                icon: iconJson,
            });

            this.hideMarketplaceImportModal();
            
            if (typeof showNotification === 'function') {
                showNotification(t('admin_marketplace_import_success', 'Managed skill imported successfully!'), 'success');
            }

            // Navigate to skills page and reload
            this.resetListSearch();
            this.showListPage();

        } catch (error) {
            console.error('Admin Marketplace import: Failed to create skill', error);
            if (typeof showNotification === 'function') {
                showNotification(error.message || t('admin_marketplace_import_failed', 'Failed to import skill. Please try again.'), 'error');
            }
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = `${Icons?.download || ''} ${escapeHtml(t('admin_marketplace_import_button', 'Import as Managed Skill'))}`;
            }
        }
    },
};

// Initialize once DOM is ready (handles both deferred and late script execution)
function initializeAdminSkillsModule() {
    AdminSkillsManager.init();
    // Check for marketplace import params in URL
    AdminSkillsManager.checkForMarketplaceImport();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeAdminSkillsModule);
} else {
    initializeAdminSkillsModule();
}

// Hook into page switching
if (typeof window !== 'undefined') {
    window.AdminSkillsManager = AdminSkillsManager;

    document.addEventListener('i18n:updated', () => {
        if (
            AdminSkillsState.initialized
            && !AdminSkillsState.isLoading
            && isPageActive(AdminSkillsDOM.listPage)
        ) {
            // The page already contains only one bounded result page; rebuild
            // its cards and range summary in the newly selected language.
            AdminSkillsManager.renderSkills();
        }
    });

    window.addEventListener('admin:page-activated', (event) => {
        const activatedPage = event?.detail?.page;
        if (activatedPage === 'skills') {
            cancelAdminSkillDetailRequest();
            void AdminSkillsManager.loadSkills();
            return;
        }

        // Loading a detail record is an asynchronous navigation intent. If the
        // administrator chooses another page before it resolves, cancel that
        // intent so the late response cannot pull them back into the editor.
        // The detail request itself activates ``skills-edit`` after it has
        // resolved, so that one destination must remain valid.
        if (activatedPage !== 'skills-edit') {
            cancelAdminSkillDetailRequest();
        }

        // Do not let delayed searches or in-flight list requests continue after
        // the administrator leaves the Skills list. The selected query and page
        // remain in state and are refreshed when the page is opened again.
        window.clearTimeout(AdminSkillsState.searchTimer);
        AdminSkillsState.searchTimer = null;
        AdminSkillsState.listRequestController?.abort();
    });
}

})();
