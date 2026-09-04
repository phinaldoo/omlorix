/**
 * Workspace Memories Module
 * Manages saved user memories, settings, and memory import.
 */

const MemoriesState = {
    initialized: false,
    memories: [],
    profile: null,
    projects: [],
    projectsLoaded: false,
    selectedMemoryId: null,
    searchQuery: '',
    editorOpen: false,
    scope: { type: 'personal', projectId: null },
    profilePollTimer: null,
    profilePollDeadline: 0,
};

const MEMORIES_PAGE_LIMIT = 100;

const MEMORY_KIND_LABELS = {
    identity: ['workspace_memories_kind_identity', 'Identity'],
    preference: ['workspace_memories_kind_preference', 'Preference'],
    project: ['workspace_memories_kind_project', 'Project'],
    relationship: ['workspace_memories_kind_relationship', 'Relationship'],
    constraint: ['workspace_memories_kind_constraint', 'Constraint'],
    experience: ['workspace_memories_kind_experience', 'Experience'],
    goal: ['workspace_memories_kind_goal', 'Goal'],
    other: ['workspace_memories_kind_other', 'Other'],
};

const MEMORY_STABILITY_LABELS = {
    stable: ['workspace_memories_stability_stable', 'Stable'],
    slow: ['workspace_memories_stability_slow', 'Slow-changing'],
    changing: ['workspace_memories_stability_changing', 'Changing'],
    ephemeral: ['workspace_memories_stability_ephemeral', 'Short-lived'],
};

function unwrapMemoriesPage(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
}

const MemoryImportState = {
    initialized: false,
    parsedItems: [],
    isSubmitting: false,
    promptExpanded: false,
    copyResetTimer: null,
};

const MEMORY_IMPORT_PROMPT = [
    'Export **all stored memories** you have about me from past conversations.',
    '',
    '### Output Format',
    '',
    'Return **ONLY valid JSON**.',
    '',
    'The output must be a **single JSON array**.',
    'Each element in the array must be a JSON object with the following exact schema:',
    '',
    '```',
    '{',
    '  "date": "YYYY-MM-DD | unknown",',
    '  "content": "memory text"',
    '}',
    '```',
    '',
    '### Rules',
    '',
    '1. Include **every stored memory** you have about me.',
    '2. **Do not summarize or rewrite** memories. Preserve the stored text **as close to verbatim as possible**.',
    '3. If the exact date of a memory is unknown, use `"unknown"` as the value.',
    '4. **Do not invent, infer, or guess** memories that are not explicitly stored.',
    '5. **Do not omit any stored memories.** The export must be complete.',
    '6. The response must contain **no explanations, comments, headings, or markdown** - only the JSON array.',
    '7. Ensure the JSON is **valid and parseable**.',
    '',
    '### Example',
    '',
    '```',
    '[',
    '  {',
    '    "date": "2025-02-01",',
    '    "content": "Always return code in a single file."',
    '  },',
    '  {',
    '    "date": "2024-11-03",',
    '    "content": "The user works at a major software company."',
    '  }',
    ']',
    '```',
    '',
    'Return **only the JSON array**.',
].join('\n');

function t(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function formatT(key, fallback, variables) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, variables);
    }
    return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => String(variables?.[token] ?? ''));
}

function normalizeMemoryScope(scope) {
    if (scope && scope.type === 'project' && scope.projectId) {
        return { type: 'project', projectId: String(scope.projectId) };
    }
    return { type: 'personal', projectId: null };
}

function showMemoryContentError() {
    window.FormValidation?.showInputError(
        MemoriesDOM.contentInput,
        MemoriesDOM.contentError,
        t('workspace_memories_error_content_required', 'Memory content is required'),
    );
}

function clearMemoryContentError() {
    window.FormValidation?.clearInputError(MemoriesDOM.contentInput, MemoriesDOM.contentError);
}

function showMemoryImportError(message) {
    if (MemoriesDOM.importErrorMessage) {
        MemoriesDOM.importErrorMessage.textContent = message || t('workspace_memories_import_error_generic', 'Failed to import memories');
    }
    window.FormValidation?.showInputError(MemoriesDOM.importInput, MemoriesDOM.importError, '', {
        errorVisibleClass: null,
        focus: false,
    });
}

function clearMemoryImportError() {
    window.FormValidation?.clearInputError(MemoriesDOM.importInput, MemoriesDOM.importError, {
        errorVisibleClass: null,
    });
    if (MemoriesDOM.importErrorMessage) MemoriesDOM.importErrorMessage.textContent = '';
}

const MemoriesAPI = {
    async send(url, { method = 'GET', body, fallback }) {
        const request = window.authedFetch || fetch;
        const response = await request(url, {
            method,
            credentials: 'include',
            ...(body === undefined ? {} : {
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            }),
        });
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        let payload = null;
        if (contentType.includes('application/json')) {
            try { payload = await response.json(); } catch (error) { payload = null; }
        } else {
            try { const text = await response.text(); payload = text ? { detail: text } : null; } catch (error) { payload = null; }
        }
        if (!response.ok) {
            throw new Error(this.extractErrorMessage(payload, fallback));
        }
        return payload;
    },

    extractErrorMessage(payload, fallbackMessage) {
        const detail = payload?.detail;
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (Array.isArray(detail) && detail.length > 0) {
            return detail
                .map((item) => {
                    if (!item) return null;
                    if (typeof item === 'string') return item;
                    const location = Array.isArray(item.loc) ? item.loc.join('.') : '';
                    const message = String(item.msg || item.message || '').trim();
                    return [location, message].filter(Boolean).join(': ');
                })
                .filter(Boolean)
                .join('; ') || fallbackMessage;
        }
        if (typeof payload?.message === 'string' && payload.message.trim()) return payload.message.trim();
        return fallbackMessage;
    },

    getScopeUrl(scope, path = '', params = {}) {
        const normalizedScope = normalizeMemoryScope(scope);
        const searchParams = new URLSearchParams(params);
        if (normalizedScope.type === 'project' && normalizedScope.projectId) {
            searchParams.set('project_id', normalizedScope.projectId);
        }
        const query = searchParams.toString();
        return `/api/v1/memories${path}${query ? `?${query}` : ''}`;
    },

    async fetchMemories(scope) {
        const url = this.getScopeUrl(scope, '', {
            limit: String(MEMORIES_PAGE_LIMIT),
            offset: '0',
        });
        return unwrapMemoriesPage(await this.send(url, {
            fallback: t('workspace_memories_error_load', 'Failed to load memories'),
        }));
    },

    async createMemory(scope, payload) {
        return this.send(this.getScopeUrl(scope), {
            method: 'POST',
            body: payload,
            fallback: t('workspace_memories_error_save', 'Failed to save memory'),
        });
    },

    async updateMemory(scope, memoryId, payload) {
        return this.send(this.getScopeUrl(scope, `/${encodeURIComponent(memoryId)}`), {
            method: 'PATCH',
            body: payload,
            fallback: t('workspace_memories_error_save', 'Failed to save memory'),
        });
    },

    async deleteMemory(scope, memoryId) {
        return this.send(this.getScopeUrl(scope, `/${encodeURIComponent(memoryId)}`), {
            method: 'DELETE',
            fallback: t('workspace_memories_error_delete', 'Failed to delete memory'),
        });
    },

    async confirmMemory(scope, memoryId) {
        return this.send(this.getScopeUrl(scope, `/${encodeURIComponent(memoryId)}/confirm`), {
            method: 'POST',
            fallback: t('workspace_memories_error_confirm', 'Failed to confirm memory'),
        });
    },

    async fetchProfile() {
        return this.send('/api/v1/memories/profile', {
            fallback: t('workspace_memories_error_load_profile', 'Failed to load memory profile'),
        });
    },

    async importMemories(scope, payload) {
        const normalizedScope = normalizeMemoryScope(scope);
        if (normalizedScope.type !== 'project' || !normalizedScope.projectId) {
            throw new Error(t('workspace_memories_import_error_generic', 'Failed to import memories'));
        }
        const path = `/api/v1/projects/${encodeURIComponent(normalizedScope.projectId)}/memories/import`;
        return this.send(path, {
            method: 'POST',
            body: payload,
            fallback: t('workspace_memories_import_error_generic', 'Failed to import memories'),
        });
    },

    async fetchProjectsList() {
        const payload = await this.send('/api/v1/projects/list', {
            fallback: 'Failed to load projects',
        });
        const projects = Array.isArray(payload) ? payload : payload?.projects;
        return Array.isArray(projects) ? projects : [];
    },
};

const MemoriesDOM = {
    get list() { return document.getElementById('memoriesList'); },
    get loadingState() { return document.getElementById('memoriesLoadingState'); },
    get emptyState() { return document.getElementById('memoriesEmptyState'); },
    get emptyTitle() { return document.getElementById('memoriesEmptyTitle'); },
    get emptyText() { return document.getElementById('memoriesEmptyText'); },
    get createBtn() { return document.getElementById('memoriesCreateBtn'); },
    get importBtn() { return document.getElementById('memoriesImportBtn'); },
    get profilePanel() { return document.getElementById('memoriesProfilePanel'); },
    get profileStatus() { return document.getElementById('memoriesProfileStatus'); },
    get profileFactCount() { return document.getElementById('memoriesProfileFactCount'); },
    get profileReviewCount() { return document.getElementById('memoriesProfileReviewCount'); },
    get profileVersion() { return document.getElementById('memoriesProfileVersion'); },
    get profileContent() { return document.getElementById('memoriesProfileContent'); },

    get editorOverlay() { return document.getElementById('memoriesEditorOverlay'); },
    get editorCloseBtn() { return document.getElementById('memoriesEditorCloseBtn'); },
    get saveBtn() { return document.getElementById('memoriesSaveBtn'); },
    get searchInput() { return document.getElementById('memoriesSearchInput'); },
    get scopeSelect() { return document.getElementById('memoriesScopeSelect'); },
    get scopeCustomSelect() { return document.getElementById('memoriesScopeCustomSelect'); },
    get scopeDescription() { return document.getElementById('memoriesScopeDescription'); },
    get contentInput() { return document.getElementById('memoriesContentInput'); },
    get contentError() { return document.getElementById('memoriesContentError'); },
    get formTitle() { return document.getElementById('memoriesFormTitle'); },
    get formSubtitle() { return document.getElementById('memoriesFormSubtitle'); },
    get metaText() { return document.getElementById('memoriesMetaText'); },
    get workspace() { return document.getElementById('memoriesWorkspace'); },
    get importContent() { return document.getElementById('memoriesImportContent'); },
    get importInline() { return document.getElementById('memoriesImportInline'); },
    get importModalBody() { return document.getElementById('memoriesImportModalBody'); },
    get importCloseBtn() { return document.getElementById('memoriesImportCloseBtn'); },
    get importCancelBtn() { return document.getElementById('memoriesImportCancelBtn'); },
    get importConfirmBtn() { return document.getElementById('memoriesImportConfirmBtn'); },
    get importConfirmText() { return document.getElementById('memoriesImportConfirmText'); },
    get importPromptCard() { return document.getElementById('memoriesImportPromptCard'); },
    get importPromptText() { return document.getElementById('memoriesImportPromptText'); },
    get importPromptToggle() { return document.getElementById('memoriesImportPromptToggle'); },
    get importPromptToggleText() { return document.getElementById('memoriesImportPromptToggleText'); },
    get importCopyBtn() { return document.getElementById('memoriesImportCopyBtn'); },
    get importInput() { return document.getElementById('memoriesImportInput'); },
    get importClearBtn() { return document.getElementById('memoriesImportClearBtn'); },
    get importError() { return document.getElementById('memoriesImportError'); },
    get importErrorMessage() { return document.getElementById('memoriesImportErrorMessage'); },
    get importPreview() { return document.getElementById('memoriesImportPreview'); },
    get importPreviewSummary() { return document.getElementById('memoriesImportPreviewSummary'); },
    get importPreviewMeta() { return document.getElementById('memoriesImportPreviewMeta'); },
    get importPreviewList() { return document.getElementById('memoriesImportPreviewList'); },
};

const MemoriesManager = {
    init() {
        if (MemoriesState.initialized) return;
        MemoriesState.initialized = true;
        this.bindEvents();
        this.bindEditorEvents();
        this.bindImportEvents();
        this.resetForm();
        if (MemoriesDOM.importPromptText) {
            MemoriesDOM.importPromptText.textContent = this.getImportPrompt();
        }
    },

    getImportPrompt() {
        if (this.getScope().type === 'project') {
            return MEMORY_IMPORT_PROMPT.replace(/about me/g, 'about this project').replace(/me\./g, 'this project.');
        }
        return MEMORY_IMPORT_PROMPT;
    },

    bindEvents() {
        const handleScopeChange = async (value) => {
            this.setScopeFromSelectValue(value);
            await this.loadMemories();
        };

        MemoriesDOM.createBtn?.addEventListener('click', () => this.openCreateModal());
        MemoriesDOM.importBtn?.addEventListener('click', () => this.showImportModal());
        MemoriesDOM.saveBtn?.addEventListener('click', () => this.saveCurrentMemory());
        MemoriesDOM.searchInput?.addEventListener('input', (event) => {
            MemoriesState.searchQuery = String(event.target.value || '').trim().toLowerCase();
            this.renderMemories();
        });
        MemoriesDOM.list?.addEventListener('click', async (event) => {
            const button = event.target.closest('[data-memory-action]');
            const memoryId = button?.closest('[data-memory-id]')?.dataset.memoryId;
            if (!button || !memoryId) return;
            if (button.dataset.memoryAction === 'edit') {
                this.openEditorForMemory(memoryId);
                return;
            }
            if (button.dataset.memoryAction === 'confirm') {
                await this.confirmMemoryFromCard(memoryId, button);
                return;
            }
            if (button.dataset.memoryAction !== 'delete' || typeof window.showDeleteConfirm !== 'function') return;
            const confirmed = await window.showDeleteConfirm({
                confirmLabel: t('workspace_memories_delete', 'Delete'),
            });
            if (confirmed) await this.deleteMemoryFromCard(memoryId, button);
        });
        MemoriesDOM.scopeSelect?.addEventListener('change', async (event) => {
            await handleScopeChange(event.target.value);
        });
        MemoriesDOM.scopeCustomSelect?.addEventListener('customSelectChange', async (event) => {
            const value = event.detail?.value || 'personal';
            if (MemoriesDOM.scopeSelect) {
                MemoriesDOM.scopeSelect.value = value;
            }
            await handleScopeChange(value);
        });
        document.addEventListener('i18n:updated', () => {
            this.renderScopeOptions();
            this.renderProfile();
            this.renderMemories();
            if (MemoriesDOM.importPromptText) {
                MemoriesDOM.importPromptText.textContent = this.getImportPrompt();
            }
        });
        document.addEventListener('chatSetupReady', () => {
            // Direct workspace routes can render before feature flags finish loading.
            // Refresh the controls once setup arrives so actions like import are not
            // left disabled after an otherwise valid direct page load.
            this.renderScopeDetails();
        });
    },

    bindEditorEvents() {
        MemoriesDOM.editorCloseBtn?.addEventListener('click', () => this.hideEditorModal());
        MemoriesDOM.contentInput?.addEventListener('input', () => clearMemoryContentError());
        MemoriesDOM.editorOverlay?.addEventListener('click', (event) => {
            if (event.target === MemoriesDOM.editorOverlay) this.hideEditorModal();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && MemoriesState.editorOpen) {
                this.hideEditorModal();
            }
        });
    },

    bindImportEvents() {
        if (MemoryImportState.initialized) return;
        MemoryImportState.initialized = true;

        MemoriesDOM.importCancelBtn?.addEventListener('click', () => this.hideImportModal());
        MemoriesDOM.importCloseBtn?.addEventListener('click', () => this.hideImportModal());
        MemoriesDOM.importPromptToggle?.addEventListener('click', () => {
            this.setImportPromptExpanded(!MemoryImportState.promptExpanded);
        });
        MemoriesDOM.importCopyBtn?.addEventListener('click', () => this.copyImportPrompt());
        MemoriesDOM.importInput?.addEventListener('input', (event) => this.handleImportInput(event.target.value));
        MemoriesDOM.importClearBtn?.addEventListener('click', () => this.clearImportInput());
        MemoriesDOM.importConfirmBtn?.addEventListener('click', () => this.submitImport());
        MemoriesDOM.importContent?.addEventListener('click', (event) => {
            if (event.target === MemoriesDOM.importContent) this.hideImportModal();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && this.isImportScreenVisible()) {
                this.hideImportModal();
            }
        });
    },

    async show() {
        this.init();
        this.stopProfilePolling();
        MemoriesState.profilePollDeadline = Date.now() + 120000;
        await this.loadProjects();
        this.applyPendingScopeRequest();
        this.renderScopeOptions();
        await this.loadMemories();
    },

    stopProfilePolling() {
        if (MemoriesState.profilePollTimer !== null) {
            clearTimeout(MemoriesState.profilePollTimer);
            MemoriesState.profilePollTimer = null;
        }
        MemoriesState.profilePollDeadline = 0;
    },

    isMemoryWorkspaceVisible() {
        const section = document.getElementById('workspaceSectionMemories');
        if (!section) return false;
        return !section.hidden
            && section.style.display !== 'none'
            && section.getAttribute('aria-hidden') !== 'true';
    },

    scheduleProfilePoll() {
        if (MemoriesState.profilePollTimer !== null) return;
        if (this.getScope().type !== 'personal' || MemoriesState.profile?.last_run_status !== 'processing') return;
        if (!MemoriesState.profilePollDeadline) {
            MemoriesState.profilePollDeadline = Date.now() + 120000;
        }
        if (Date.now() >= MemoriesState.profilePollDeadline || !this.isMemoryWorkspaceVisible()) return;

        MemoriesState.profilePollTimer = setTimeout(async () => {
            MemoriesState.profilePollTimer = null;
            if (!this.isMemoryWorkspaceVisible() || this.getScope().type !== 'personal') return;
            try {
                const profile = await MemoriesAPI.fetchProfile();
                const completed = profile?.last_run_status !== 'processing';
                MemoriesState.profile = profile;
                if (completed) {
                    MemoriesState.memories = await MemoriesAPI.fetchMemories(this.getScope());
                    this.renderMemories();
                }
                this.renderProfile();
            } catch (error) {
                this.scheduleProfilePoll();
            }
        }, 2000);
        MemoriesState.profilePollTimer?.unref?.();
    },

    getScope() {
        return normalizeMemoryScope(MemoriesState.scope);
    },

    getScopeProject(scope = this.getScope()) {
        if (scope.type !== 'project') return null;
        return MemoriesState.projects.find((item) => item.id === scope.projectId) || null;
    },

    isScopeWritable(scope = this.getScope()) {
        if (typeof window !== 'undefined') {
            const hasExplicitFeatureDeny = window.enableMemoriesFeature === false
                || window.chatSetup?.enable_memories === false;
            if (hasExplicitFeatureDeny) {
                return false;
            }
        }
        if (scope.type !== 'project') {
            return true;
        }
        return Boolean(this.getScopeProject(scope)?.settings?.separate_memory_enabled);
    },

    getScopeDescription(scope = this.getScope()) {
        if (scope.type === 'project') {
            const project = MemoriesState.projects.find((item) => item.id === scope.projectId);
            const projectTitle = project?.title || t('project_memories_scope_fallback_project', 'this project');
            return formatT(
                'project_memories_scope_description',
                'Shared memory for {project}. Every project member sees and edits the same memory.',
                { project: projectTitle },
            );
        }
        return t(
            'workspace_memories_subtitle',
            'Short user facts and preferences the assistant can reuse in future chats.',
        );
    },

    setScope(scope) {
        MemoriesState.scope = normalizeMemoryScope(scope);
        if (MemoriesState.scope.type !== 'personal') {
            this.stopProfilePolling();
        }
    },

    setScopeFromSelectValue(value) {
        const normalized = String(value || 'personal').trim();
        if (normalized.startsWith('project:')) {
            this.setScope({ type: 'project', projectId: normalized.slice('project:'.length) });
            return;
        }
        this.setScope({ type: 'personal', projectId: null });
    },

    getSelectValueForScope(scope = this.getScope()) {
        return scope.type === 'project' && scope.projectId ? `project:${scope.projectId}` : 'personal';
    },

    async loadProjects() {
        if (MemoriesState.projectsLoaded) return;
        try {
            const projects = await MemoriesAPI.fetchProjectsList();
            MemoriesState.projects = projects;
            // Cache only a successful response. A transient request failure
            // must remain retryable the next time the workspace is opened.
            MemoriesState.projectsLoaded = true;
        } catch (error) {
            MemoriesState.projects = [];
        }
    },

    applyPendingScopeRequest() {
        const requestedScope = normalizeMemoryScope(window.__workspacePendingMemoryScope);
        window.__workspacePendingMemoryScope = null;
        if (requestedScope.type === 'project') {
            const hasAccess = MemoriesState.projects.some((project) => project.id === requestedScope.projectId);
            if (hasAccess) {
                this.setScope(requestedScope);
                return;
            }
        }

        const currentScope = this.getScope();
        if (currentScope.type === 'project') {
            const hasAccess = MemoriesState.projects.some((project) => project.id === currentScope.projectId);
            if (!hasAccess) {
                this.setScope({ type: 'personal', projectId: null });
            }
        }
    },

    renderScopeOptions() {
        const select = MemoriesDOM.scopeSelect;
        if (!select) return;

        const selectedValue = this.getSelectValueForScope();
        const scopeOptions = [
            {
                value: 'personal',
                label: t('workspace_memories_scope_personal', 'Personal memory'),
                i18nKey: 'workspace_memories_scope_personal',
            },
            ...MemoriesState.projects.map(
                (project) => {
                    const title = project.title || t('workspace_memories_scope_project_untitled', 'Untitled project');
                    const label = formatT(
                        'workspace_memories_scope_project_option',
                        '{title} (shared)',
                        { title },
                    );
                    return {
                        value: `project:${project.id}`,
                        label,
                    };
                },
            ),
        ];
        select.innerHTML = scopeOptions.map((option) => (
            `<option value="${this.escapeHtml(option.value)}"${option.i18nKey ? ` data-i18n="${option.i18nKey}"` : ''}>${this.escapeHtml(option.label)}</option>`
        )).join('');
        select.value = selectedValue;
        window.refreshCustomSelect?.(MemoriesDOM.scopeCustomSelect, {
            options: scopeOptions,
            value: selectedValue,
        });
        window.setCustomSelectValue?.('memories_scope', selectedValue);
        this.renderScopeDetails();
    },

    renderScopeDetails() {
        if (MemoriesDOM.scopeDescription) {
            MemoriesDOM.scopeDescription.textContent = this.getScopeDescription();
        }
        this.renderProfile();
        this.updateActionAvailability();
    },

    updateActionAvailability() {
        const canWrite = this.isScopeWritable();
        const canImport = canWrite && this.getScope().type === 'project';

        [
            MemoriesDOM.createBtn,
            MemoriesDOM.saveBtn,
            MemoriesDOM.contentInput,
        ].forEach((element) => {
            if (element) {
                element.disabled = !canWrite;
            }
        });
        if (MemoriesDOM.importBtn) {
            MemoriesDOM.importBtn.hidden = !canImport;
            MemoriesDOM.importBtn.disabled = !canImport;
        }
    },

    showWorkspaceListScreen() {
        if (MemoriesDOM.workspace) MemoriesDOM.workspace.style.display = '';
        if (MemoriesDOM.importContent) {
            MemoriesDOM.importContent.hidden = true;
            MemoriesDOM.importContent.setAttribute('aria-hidden', 'true');
        }
    },

    showImportScreen() {
        if (MemoriesDOM.workspace) MemoriesDOM.workspace.style.display = '';
        if (MemoriesDOM.importContent) {
            MemoriesDOM.importContent.hidden = false;
            MemoriesDOM.importContent.setAttribute('aria-hidden', 'false');
        }
    },

    isImportScreenVisible() {
        return Boolean(MemoriesDOM.importContent && !MemoriesDOM.importContent.hidden);
    },

    async loadMemories() {
        const list = MemoriesDOM.list;
        const loading = MemoriesDOM.loadingState;
        if (loading) loading.style.display = 'block';
        if (list) list.innerHTML = '';

        try {
            const scope = this.getScope();
            const [memories, profile] = await Promise.all([
                MemoriesAPI.fetchMemories(scope),
                scope.type === 'personal' ? MemoriesAPI.fetchProfile() : Promise.resolve(null),
            ]);
            MemoriesState.memories = memories;
            MemoriesState.profile = profile;
            const selectedExists = MemoriesState.memories.some((memory) => memory.id === MemoriesState.selectedMemoryId);
            if (!selectedExists) {
                MemoriesState.selectedMemoryId = null;
                this.resetForm(false);
            }
            this.renderScopeDetails();
            this.renderMemories();
        } catch (error) {
            MemoriesState.memories = [];
            MemoriesState.profile = null;
            this.renderScopeDetails();
            this.renderMemories();
            if (typeof notifyError === 'function') notifyError(error.message || t('workspace_memories_error_load', 'Failed to load memories'));
        } finally {
            if (loading) loading.style.display = 'none';
        }
    },

    getFilteredMemories() {
        return MemoriesState.memories.filter((memory) => {
            const haystack = `${memory.content || ''} ${memory.memory_key || ''} ${memory.kind || ''}`.toLowerCase();
            const matchesSearch = !MemoriesState.searchQuery || haystack.includes(MemoriesState.searchQuery);
            return matchesSearch;
        });
    },

    renderProfile() {
        const panel = MemoriesDOM.profilePanel;
        if (!panel) return;
        const isPersonal = this.getScope().type === 'personal';
        panel.hidden = !isPersonal;
        if (!isPersonal) return;

        const profile = MemoriesState.profile || {};
        const factCount = Number(profile.active_fact_count || 0);
        const maxFactCount = Number(profile.max_fact_count || 100);
        if (MemoriesDOM.profileFactCount) {
            MemoriesDOM.profileFactCount.textContent = `${factCount} / ${maxFactCount}`;
        }
        if (MemoriesDOM.profileReviewCount) {
            MemoriesDOM.profileReviewCount.textContent = String(Number(profile.review_fact_count || 0));
        }
        if (MemoriesDOM.profileVersion) {
            MemoriesDOM.profileVersion.textContent = String(Number(profile.version || 0));
        }
        if (MemoriesDOM.profileContent) {
            const content = String(profile.content || '').trim();
            MemoriesDOM.profileContent.textContent = content || t(
                'workspace_memories_profile_empty',
                'Your profile will appear after a message contains reusable information about you.',
            );
            MemoriesDOM.profileContent.classList.toggle('is-empty', !content);
        }

        const statuses = {
            processing: ['workspace_memories_profile_status_processing', 'Updating…'],
            updated: ['workspace_memories_profile_status_updated', 'Updated'],
            unchanged: ['workspace_memories_profile_status_unchanged', 'Checked — no changes'],
            failed: ['workspace_memories_profile_status_failed', 'Last update failed'],
        };
        const statusEntry = statuses[profile.last_run_status];
        if (MemoriesDOM.profileStatus) {
            MemoriesDOM.profileStatus.textContent = statusEntry
                ? t(statusEntry[0], statusEntry[1])
                : t('workspace_memories_profile_status_waiting', 'Waiting for your first memory update');
            MemoriesDOM.profileStatus.dataset.status = profile.last_run_status || 'waiting';
            MemoriesDOM.profileStatus.title = profile.last_run_status === 'failed'
                ? t('workspace_memories_profile_status_failed_help', 'The memory provider could not complete the last update. A later message will try again.')
                : '';
        }
        if (profile.last_run_status === 'processing') {
            this.scheduleProfilePoll();
        } else if (MemoriesState.profilePollTimer !== null) {
            clearTimeout(MemoriesState.profilePollTimer);
            MemoriesState.profilePollTimer = null;
        }
    },

    renderMemories() {
        const list = MemoriesDOM.list;
        const empty = MemoriesDOM.emptyState;
        if (!list) return;

        const memories = this.getFilteredMemories();
        if (empty) {
            empty.style.display = memories.length === 0 ? 'flex' : 'none';
        }
        this.renderEmptyState(MemoriesState.memories.length > 0);

        if (memories.length === 0) {
            list.innerHTML = '';
            return;
        }

        list.innerHTML = memories.map((memory) => {
            const isActive = memory.id === MemoriesState.selectedMemoryId;
            const updatedLabel = `${t('workspace_memories_meta_updated_prefix', 'Updated')} ${this.formatDate(memory.updated_at)}`;
            const sourceDateLabel = memory.source_date ? this.getSourceDateLabel(memory.source_date) : '';
            const editLabel = t('workspace_memories_form_edit_title', 'Edit memory');
            const deleteLabel = t('workspace_memories_delete', 'Delete');
            const confirmLabel = t('workspace_memories_confirm', 'Confirm');
            const editIcon = window.Icons?.edit || '';
            const deleteIcon = window.Icons?.trash || '';
            const confirmIcon = window.Icons?.check || '';
            const disabledAttribute = this.isScopeWritable() ? '' : ' disabled';
            const kindEntry = MEMORY_KIND_LABELS[memory.kind] || MEMORY_KIND_LABELS.other;
            const stabilityEntry = MEMORY_STABILITY_LABELS[memory.stability] || MEMORY_STABILITY_LABELS.slow;
            const needsReview = memory.lifecycle_state === 'review';
            const expiryLabel = memory.expires_at
                ? formatT(
                    'workspace_memories_expires',
                    'Expires {date}',
                    { date: this.formatDate(memory.expires_at) },
                )
                : '';
            return `
                <article class="memory-item ${isActive ? 'active' : ''}" data-memory-id="${this.escapeHtml(memory.id)}">
                    <p class="memory-item-content">${this.escapeHtml(memory.content || '')}</p>
                    <div class="memory-item-badges">
                        <span>${this.escapeHtml(t(kindEntry[0], kindEntry[1]))}</span>
                        <span>${this.escapeHtml(t(stabilityEntry[0], stabilityEntry[1]))}</span>
                        ${needsReview ? `<span class="needs-review">${this.escapeHtml(t('workspace_memories_needs_review', 'Needs review'))}</span>` : ''}
                    </div>
                    <div class="memory-item-footer">
                        ${sourceDateLabel ? `<span class="memory-item-date">${this.escapeHtml(sourceDateLabel)}</span>` : ''}
                        <span class="memory-item-updated">${this.escapeHtml(updatedLabel)}</span>
                        ${expiryLabel ? `<span class="memory-item-expiry">${this.escapeHtml(expiryLabel)}</span>` : ''}
                    </div>
                    <div class="memory-item-actions" role="group" aria-label="${this.escapeHtml(t('workspace_memories_actions_aria', 'Memory actions'))}">
                        ${needsReview ? `<button type="button" class="memory-item-action memory-item-confirm" data-memory-action="confirm" title="${this.escapeHtml(confirmLabel)}" aria-label="${this.escapeHtml(confirmLabel)}"${disabledAttribute}>
                            <span aria-hidden="true">${confirmIcon}</span>
                            <span>${this.escapeHtml(confirmLabel)}</span>
                        </button>` : ''}
                        <button type="button" class="memory-item-action memory-item-edit" data-memory-action="edit" title="${this.escapeHtml(editLabel)}" aria-label="${this.escapeHtml(editLabel)}"${disabledAttribute}>
                            <span aria-hidden="true">${editIcon}</span>
                            <span>${this.escapeHtml(editLabel)}</span>
                        </button>
                        <button type="button" class="memory-item-action memory-item-delete" data-memory-action="delete" title="${this.escapeHtml(deleteLabel)}" aria-label="${this.escapeHtml(deleteLabel)}"${disabledAttribute}>
                            <span aria-hidden="true">${deleteIcon}</span>
                            <span>${this.escapeHtml(deleteLabel)}</span>
                        </button>
                    </div>
                </article>
            `;
        }).join('');

    },

    async confirmMemoryFromCard(memoryId, triggerButton = null) {
        const targetId = String(memoryId || '').trim();
        if (!targetId || !this.isScopeWritable()) return;
        if (triggerButton) triggerButton.disabled = true;
        try {
            await MemoriesAPI.confirmMemory(this.getScope(), targetId);
            await this.loadMemories();
            if (typeof notifySuccess === 'function') {
                notifySuccess(t('workspace_memories_success_confirmed', 'Memory confirmed'));
            }
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('workspace_memories_error_confirm', 'Failed to confirm memory'));
            }
        } finally {
            if (triggerButton?.isConnected) triggerButton.disabled = false;
        }
    },

    renderEmptyState(hasAnyMemories) {
        if (!MemoriesDOM.emptyTitle || !MemoriesDOM.emptyText) return;
        if (hasAnyMemories) {
            MemoriesDOM.emptyTitle.textContent = t('workspace_memories_empty_filtered_title', 'No memories match your search');
            MemoriesDOM.emptyText.textContent = t(
                'workspace_memories_empty_filtered_text',
                'Try a different search.',
            );
            return;
        }

        MemoriesDOM.emptyTitle.textContent = t('workspace_memories_empty_title', 'Bring your saved context here');
        MemoriesDOM.emptyText.textContent = this.getScope().type === 'project'
            ? t(
                'project_memories_empty_text',
                'Build a shared memory for this project so future project chats keep the same context for every member.',
            )
            : t(
                'workspace_memories_empty_text',
                'Reusable facts will appear here automatically after you mention them in chat. You can also add one by hand.',
            );
    },

    selectMemory(memoryId) {
        const memory = MemoriesState.memories.find((item) => item.id === memoryId);
        if (!memory) return;
        MemoriesState.selectedMemoryId = memory.id;
        if (MemoriesDOM.contentInput) MemoriesDOM.contentInput.value = memory.content || '';
        clearMemoryContentError();
        if (MemoriesDOM.formTitle) MemoriesDOM.formTitle.textContent = t('workspace_memories_form_edit_title', 'Edit memory');
        if (MemoriesDOM.formSubtitle) {
            MemoriesDOM.formSubtitle.textContent = this.getScope().type === 'project'
                ? t('project_memories_form_edit_subtitle', 'Update this shared project memory.')
                : t('workspace_memories_form_edit_subtitle', 'Update this saved memory.');
        }
        if (MemoriesDOM.metaText) {
            const sourceDate = memory.source_date ? this.getSourceDateLabel(memory.source_date) : '';
            const pieces = [];
            if (sourceDate) pieces.push(sourceDate);
            pieces.push(`${t('workspace_memories_meta_updated_prefix', 'Updated')} ${this.formatDate(memory.updated_at)}`);
            MemoriesDOM.metaText.textContent = pieces.join(' • ');
        }
        this.updateActionAvailability();
        this.renderMemories();
    },

    resetForm(clearSelection = true) {
        if (clearSelection) {
            MemoriesState.selectedMemoryId = null;
        }
        if (MemoriesDOM.contentInput) MemoriesDOM.contentInput.value = '';
        clearMemoryContentError();
        if (MemoriesDOM.formTitle) MemoriesDOM.formTitle.textContent = t('workspace_memories_form_create_title', 'Create memory');
        if (MemoriesDOM.formSubtitle) {
            MemoriesDOM.formSubtitle.textContent = this.getScope().type === 'project'
                ? t('project_memories_form_create_subtitle', 'Save a concise fact your team wants this project to remember later.')
                : t(
                    'workspace_memories_form_create_subtitle',
                    'Save a concise fact the assistant should remember later.',
                );
        }
        if (MemoriesDOM.metaText) {
            MemoriesDOM.metaText.textContent = this.getScope().type === 'project'
                ? t(
                    'project_memories_form_meta',
                    'Only store durable project facts or preferences that should stay shared across project chats.',
                )
                : t(
                    'workspace_memories_form_meta',
                    'Only store durable facts or preferences that will matter again later.',
                );
        }
        this.updateActionAvailability();
        this.renderMemories();
    },

    openCreateModal() {
        if (!this.isScopeWritable()) return;
        this.resetForm();
        this.showEditorModal();
    },

    openEditorForMemory(memoryId) {
        this.selectMemory(memoryId);
        this.showEditorModal();
    },

    showEditorModal() {
        this.init();
        if (!MemoriesDOM.editorOverlay) return;
        MemoriesState.editorOpen = true;
        MemoriesDOM.editorOverlay.removeAttribute('hidden');
        MemoriesDOM.editorOverlay.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => MemoriesDOM.contentInput?.focus());
    },

    hideEditorModal() {
        if (!MemoriesDOM.editorOverlay) return;
        MemoriesState.editorOpen = false;
        MemoriesDOM.editorOverlay.setAttribute('hidden', '');
        MemoriesDOM.editorOverlay.setAttribute('aria-hidden', 'true');
    },

    async saveCurrentMemory() {
        const content = String(MemoriesDOM.contentInput?.value || '').trim();
        if (!content) {
            showMemoryContentError();
            return;
        }
        clearMemoryContentError();

        MemoriesDOM.saveBtn?.setAttribute('disabled', 'true');
        try {
            let memory;
            if (MemoriesState.selectedMemoryId) {
                memory = await MemoriesAPI.updateMemory(this.getScope(), MemoriesState.selectedMemoryId, { content });
                if (typeof notifySuccess === 'function') notifySuccess(this.getScope().type === 'project'
                    ? t('project_memories_success_updated', 'Project memory updated')
                    : t('workspace_memories_success_updated', 'Memory updated'));
            } else {
                memory = await MemoriesAPI.createMemory(this.getScope(), { content });
                if (typeof notifySuccess === 'function') notifySuccess(this.getScope().type === 'project'
                    ? t('project_memories_success_created', 'Project memory created')
                    : t('workspace_memories_success_created', 'Memory created'));
            }
            await this.loadMemories();
            this.selectMemory(memory.id);
            this.hideEditorModal();
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('workspace_memories_error_save', 'Failed to save memory'));
            }
        } finally {
            MemoriesDOM.saveBtn?.removeAttribute('disabled');
        }
    },

    /**
     * Deletes the memory selected by a card action. The initiating button is
     * disabled during the request so repeated clicks cannot issue duplicate
     * deletes while the list is waiting to refresh.
     */
    async deleteMemoryFromCard(memoryId, triggerButton = null) {
        const targetId = String(memoryId || '').trim();
        if (!targetId || !this.isScopeWritable()) return;

        if (triggerButton) triggerButton.disabled = true;
        try {
            await MemoriesAPI.deleteMemory(this.getScope(), targetId);
            const deletedSelectedMemory = MemoriesState.selectedMemoryId === targetId;
            if (deletedSelectedMemory) MemoriesState.selectedMemoryId = null;
            await this.loadMemories();
            if (deletedSelectedMemory) this.resetForm(false);
            if (typeof notifySuccess === 'function') notifySuccess(this.getScope().type === 'project'
                ? t('project_memories_success_deleted', 'Project memory deleted')
                : t('workspace_memories_success_deleted', 'Memory deleted'));
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('workspace_memories_error_delete', 'Failed to delete memory'));
            }
        } finally {
            if (triggerButton?.isConnected) triggerButton.disabled = false;
        }
    },

    showImportModal() {
        if (this.getScope().type !== 'project' || !this.isScopeWritable()) return;
        this.init();
        this.resetImportState();
        if (!MemoriesDOM.importContent) return;
        if (MemoriesDOM.importPromptText) {
            MemoriesDOM.importPromptText.textContent = this.getImportPrompt();
        }
        this.showImportScreen();
        this.resetImportModalScrollPosition();
        requestAnimationFrame(() => {
            this.resetImportModalScrollPosition();
            this.focusImportScreenHeader();
        });
    },

    hideImportModal() {
        this.showWorkspaceListScreen();
        this.resetImportState();
    },

    resetImportState() {
        MemoryImportState.parsedItems = [];
        MemoryImportState.isSubmitting = false;
        this.setImportPromptExpanded(false);

        if (MemoriesDOM.importInput) MemoriesDOM.importInput.value = '';
        clearMemoryImportError();
        if (MemoriesDOM.importPreview) MemoriesDOM.importPreview.setAttribute('hidden', '');
        if (MemoriesDOM.importPreviewList) MemoriesDOM.importPreviewList.innerHTML = '';
        if (MemoriesDOM.importErrorMessage) MemoriesDOM.importErrorMessage.textContent = '';
        if (MemoriesDOM.importPreviewSummary) MemoriesDOM.importPreviewSummary.textContent = '';
        if (MemoriesDOM.importPreviewMeta) MemoriesDOM.importPreviewMeta.textContent = '';
        if (MemoriesDOM.importClearBtn) MemoriesDOM.importClearBtn.setAttribute('hidden', '');
        if (MemoriesDOM.importConfirmBtn) MemoriesDOM.importConfirmBtn.disabled = true;
        if (MemoriesDOM.importConfirmText) {
            MemoriesDOM.importConfirmText.textContent = t('workspace_memories_import_confirm', 'Import memories');
        }

        clearTimeout(MemoryImportState.copyResetTimer);
        if (MemoriesDOM.importCopyBtn) {
            MemoriesDOM.importCopyBtn.textContent = t('workspace_memories_import_prompt_copy', 'Copy prompt');
        }
    },

    /**
     * Expands or collapses the long export prompt without changing its text.
     * Keeping the state on both the controller and card lets CSS own the
     * visual truncation while assistive technology receives the same state.
     */
    setImportPromptExpanded(expanded) {
        const nextExpanded = Boolean(expanded);
        MemoryImportState.promptExpanded = nextExpanded;

        if (MemoriesDOM.importPromptCard) {
            MemoriesDOM.importPromptCard.dataset.expanded = nextExpanded ? 'true' : 'false';
        }
        if (MemoriesDOM.importPromptToggle) {
            MemoriesDOM.importPromptToggle.setAttribute('aria-expanded', nextExpanded ? 'true' : 'false');
        }
        if (MemoriesDOM.importPromptToggleText) {
            const key = nextExpanded
                ? 'workspace_memories_import_prompt_show_less'
                : 'workspace_memories_import_prompt_show_more';
            const fallback = nextExpanded ? 'Show less' : 'Show more';
            MemoriesDOM.importPromptToggleText.setAttribute('data-i18n', key);
            MemoriesDOM.importPromptToggleText.textContent = t(key, fallback);
        }
    },

    resetImportModalScrollPosition() {
        // Reset every scrollable layer so reopening the import screen always starts at the top.
        [MemoriesDOM.importContent, MemoriesDOM.importInline, MemoriesDOM.importModalBody].forEach((element) => {
            if (element) {
                element.scrollTop = 0;
            }
        });

        const memoriesSection = document.getElementById('workspaceSectionMemories');
        if (memoriesSection) {
            memoriesSection.scrollTop = 0;
        }

        if (typeof window !== 'undefined') {
            window.scrollTo(0, 0);
        }

        // Also reset the textarea's internal scroll in case a previous import payload was long.
        if (MemoriesDOM.importInput) {
            MemoriesDOM.importInput.scrollTop = 0;
            MemoriesDOM.importInput.scrollLeft = 0;
        }
    },

    focusImportScreenHeader() {
        const focusTarget = MemoriesDOM.importContent;
        if (!focusTarget || typeof focusTarget.focus !== 'function') {
            return;
        }

        try {
            focusTarget.focus({ preventScroll: true });
        } catch (error) {
            focusTarget.focus();
        }
    },

    async copyImportPrompt() {
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(this.getImportPrompt());
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = this.getImportPrompt();
                textarea.setAttribute('readonly', 'true');
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            }
            if (MemoriesDOM.importCopyBtn) {
                MemoriesDOM.importCopyBtn.textContent = t('workspace_memories_import_prompt_copied', 'Copied');
            }
            clearTimeout(MemoryImportState.copyResetTimer);
            MemoryImportState.copyResetTimer = setTimeout(() => {
                if (MemoriesDOM.importCopyBtn) {
                    MemoriesDOM.importCopyBtn.textContent = t('workspace_memories_import_prompt_copy', 'Copy prompt');
                }
            }, 1800);
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(t('workspace_memories_import_error_copy', 'Failed to copy the import prompt'));
            }
        }
    },

    clearImportInput() {
        if (MemoriesDOM.importInput) {
            MemoriesDOM.importInput.value = '';
            MemoriesDOM.importInput.focus();
        }
        this.handleImportInput('');
    },

    handleImportInput(rawValue) {
        const value = String(rawValue || '');
        if (MemoriesDOM.importClearBtn) {
            if (value.trim()) {
                MemoriesDOM.importClearBtn.removeAttribute('hidden');
            } else {
                MemoriesDOM.importClearBtn.setAttribute('hidden', '');
            }
        }

        if (!value.trim()) {
            MemoryImportState.parsedItems = [];
            clearMemoryImportError();
            if (MemoriesDOM.importPreview) MemoriesDOM.importPreview.setAttribute('hidden', '');
            if (MemoriesDOM.importConfirmBtn) MemoriesDOM.importConfirmBtn.disabled = true;
            return;
        }

        try {
            const items = this.parseImportPayload(value);
            MemoryImportState.parsedItems = items;
            this.renderImportPreview(items);
            clearMemoryImportError();
            if (MemoriesDOM.importPreview) MemoriesDOM.importPreview.removeAttribute('hidden');
            if (MemoriesDOM.importConfirmBtn) MemoriesDOM.importConfirmBtn.disabled = false;
        } catch (error) {
            MemoryImportState.parsedItems = [];
            if (MemoriesDOM.importPreview) MemoriesDOM.importPreview.setAttribute('hidden', '');
            showMemoryImportError(error.message || t('workspace_memories_import_error_generic', 'Failed to import memories'));
            if (MemoriesDOM.importConfirmBtn) MemoriesDOM.importConfirmBtn.disabled = true;
        }
    },

    parseImportPayload(rawValue) {
        const normalizedInput = this.unwrapImportPayload(rawValue);
        let parsed;
        try {
            parsed = JSON.parse(normalizedInput);
        } catch (error) {
            throw new Error(t('workspace_memories_import_error_invalid_json', 'Paste a valid JSON array before importing.'));
        }

        if (!Array.isArray(parsed)) {
            throw new Error(t('workspace_memories_import_error_invalid_json', 'Paste a valid JSON array before importing.'));
        }
        if (parsed.length === 0) {
            throw new Error(t('workspace_memories_import_error_empty', 'The JSON array is empty.'));
        }
        if (parsed.length > 100) {
            throw new Error(t('workspace_memories_import_error_too_many', 'You can import up to 100 memories at once.'));
        }

        return parsed.map((item, index) => this.validateImportItem(item, index));
    },

    unwrapImportPayload(rawValue) {
        const trimmed = String(rawValue || '').trim();
        const fencedMatch = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
        return fencedMatch ? fencedMatch[1].trim() : trimmed;
    },

    validateImportItem(item, index) {
        const position = index + 1;
        if (!item || typeof item !== 'object' || Array.isArray(item)) {
            throw new Error(`${t('workspace_memories_import_error_item_shape', 'Each entry must be a JSON object.')} (#${position})`);
        }

        const content = typeof item.content === 'string' ? item.content.trim() : '';
        if (!content) {
            throw new Error(`${t('workspace_memories_import_error_item_content', 'Each entry needs a non-empty content field.')} (#${position})`);
        }
        if (content.length > 500) {
            throw new Error(`${t('workspace_memories_import_error_item_too_long', 'Each memory must stay within 500 characters.')} (#${position})`);
        }

        const rawDate = typeof item.date === 'string' ? item.date.trim() : '';
        if (!rawDate) {
            throw new Error(`${t('workspace_memories_import_error_item_date', 'Each entry needs a date set to YYYY-MM-DD or unknown.')} (#${position})`);
        }
        const normalizedDate = rawDate.toLowerCase() === 'unknown' ? 'unknown' : rawDate;
        if (normalizedDate !== 'unknown' && !this.isValidIsoDate(normalizedDate)) {
            throw new Error(`${t('workspace_memories_import_error_item_date', 'Each entry needs a date set to YYYY-MM-DD or unknown.')} (#${position})`);
        }

        return {
            date: normalizedDate,
            content,
        };
    },

    renderImportPreview(items) {
        if (!MemoriesDOM.importPreviewSummary || !MemoriesDOM.importPreviewMeta || !MemoriesDOM.importPreviewList) return;

        const datedCount = items.filter((item) => item.date !== 'unknown').length;
        const unknownCount = items.length - datedCount;

        MemoriesDOM.importPreviewSummary.textContent = `${items.length} ${t('workspace_memories_import_preview_ready', 'memories ready')}`;
        MemoriesDOM.importPreviewMeta.textContent = `${datedCount} ${t('workspace_memories_import_preview_source_dates', 'with source dates')} • ${unknownCount} ${t('workspace_memories_import_preview_unknown_dates', 'with unknown dates')}`;

        const previewItems = items.slice(0, 4).map((item) => `
            <article class="memories-import-preview-item">
                <div class="memories-import-preview-item-top">
                    <span class="memories-import-preview-item-date">${this.escapeHtml(this.formatImportPreviewDate(item.date))}</span>
                </div>
                <p class="memories-import-preview-item-content">${this.escapeHtml(item.content)}</p>
            </article>
        `);

        if (items.length > 4) {
            previewItems.push(`
                <div class="memories-import-preview-more">
                    +${items.length - 4} ${this.escapeHtml(t('workspace_memories_import_preview_more', 'more memories'))}
                </div>
            `);
        }

        MemoriesDOM.importPreviewList.innerHTML = previewItems.join('');
    },

    async submitImport() {
        if (MemoryImportState.isSubmitting || MemoryImportState.parsedItems.length === 0) return;

        MemoryImportState.isSubmitting = true;
        if (MemoriesDOM.importConfirmBtn) MemoriesDOM.importConfirmBtn.disabled = true;
        if (MemoriesDOM.importConfirmText) {
            MemoriesDOM.importConfirmText.textContent = t('workspace_memories_import_confirming', 'Importing...');
        }

        try {
            const response = await MemoriesAPI.importMemories(this.getScope(), MemoryImportState.parsedItems);
            MemoriesState.searchQuery = '';
            if (MemoriesDOM.searchInput) MemoriesDOM.searchInput.value = '';
            if (MemoriesDOM.scopeSelect) MemoriesDOM.scopeSelect.value = this.getSelectValueForScope();

            await this.loadMemories();
            if (Array.isArray(response.items) && response.items.length > 0) {
                this.selectMemory(response.items[0].id);
            }
            this.hideImportModal();

            if (typeof notifySuccess === 'function') {
                const createdCount = Number(response.created_count || 0);
                const dedupedCount = Number(response.deduped_count || 0);
                notifySuccess(
                    `${this.getScope().type === 'project'
                        ? t('project_memories_import_success', 'Project memories imported')
                        : t('workspace_memories_import_success', 'Memories imported')} (${createdCount} new, ${dedupedCount} existing)`,
                );
            }
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('workspace_memories_import_error_generic', 'Failed to import memories'));
            }
        } finally {
            MemoryImportState.isSubmitting = false;
            if (MemoriesDOM.importConfirmText) {
                MemoriesDOM.importConfirmText.textContent = t('workspace_memories_import_confirm', 'Import memories');
            }
            if (MemoriesDOM.importConfirmBtn) {
                MemoriesDOM.importConfirmBtn.disabled = MemoryImportState.parsedItems.length === 0;
            }
        }
    },

    getSourceDateLabel(sourceDate) {
        return `${t('workspace_memories_meta_source_date_prefix', 'Source date')} ${this.formatSourceDate(sourceDate)}`;
    },

    formatImportPreviewDate(value) {
        if (!value || value === 'unknown') {
            return t('workspace_memories_meta_source_date_unknown', 'Source date unknown');
        }
        return `${t('workspace_memories_meta_source_date_prefix', 'Source date')} ${this.formatSourceDate(value)}`;
    },

    formatDate(value) {
        if (!value) return t('relative_time_now', 'just now');
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return t('relative_time_now', 'just now');
        return date.toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },

    formatSourceDate(value) {
        const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!match) return t('workspace_memories_meta_source_date_unknown', 'Source date unknown');
        const year = Number(match[1]);
        const month = Number(match[2]) - 1;
        const day = Number(match[3]);
        const date = new Date(Date.UTC(year, month, day, 12, 0, 0));
        if (Number.isNaN(date.getTime())) return t('workspace_memories_meta_source_date_unknown', 'Source date unknown');
        return date.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            timeZone: 'UTC',
        });
    },

    isValidIsoDate(value) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ''))) return false;
        const date = new Date(`${value}T00:00:00Z`);
        if (Number.isNaN(date.getTime())) return false;
        return date.toISOString().slice(0, 10) === value;
    },

    escapeHtml(value) {
        if (typeof window.escapeHtml === 'function') {
            return window.escapeHtml(value);
        }
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },
};

if (typeof window !== 'undefined') {
    window.MemoriesAPI = MemoriesAPI;
    window.MemoriesManager = MemoriesManager;
}
