(function () {
    const API_BASE = '/api/v1/custom-tools/admin';

    const state = {
        initialized: false,
        active: false,
        loading: false,
        tools: [],
        editingId: null,
        pendingDeleteToolId: null,
        createLastTestResult: null,
        editLastTestResult: null,
        createInitialSnapshot: null,
        editInitialSnapshot: null,
        unsavedGuardRegistered: false,
        escapeRegistration: null,
        importPayload: null,
        importTools: [],
        importSelected: new Set(),
        importFileName: '',
    };

    const UNSAVED_GUARD_ID = 'custom-python-tools-form-unsaved';

    const defaultSourceCode = [
        'TOOL_DEFINITION = {',
        '    "name": "example_custom_tool",',
        '    "display_name": "Example Custom Tool",',
        '    "description": "Example admin-managed tool that echoes structured input.",',
        '    "parameters": {',
        '        "type": "object",',
        '        "properties": {',
        '            "message": {',
        '                "type": "string",',
        '                "description": "Message to echo back."',
        '            }',
        '        },',
        '        "required": ["message"],',
        '        "additionalProperties": False',
        '    }',
        '}',
        '',
        'def run_tool(arguments, context):',
        '    message = arguments["message"]',
        '    return {',
        '        "content": f"Custom tool received: {message}",',
        '        "result": {',
        '            "echo": message,',
        '            "user_id": context.get("user_id"),',
        '        },',
        '    }',
    ].join('\n');

    const t = (key, fallback) => typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);

    const formatT = (key, fallback, values = {}) => {
        let text = t(key, fallback);
        Object.entries(values).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
        });
        return text;
    };

    const DOM = {
        get listPage() { return document.getElementById('page-custom-python-tools'); },
        get createPage() { return document.getElementById('page-custom-python-tools-create'); },
        get editPage() { return document.getElementById('page-custom-python-tools-edit'); },
        get list() { return document.getElementById('customPythonToolsList'); },
        get searchInput() { return document.getElementById('customPythonToolSearchInput'); },
        get searchClear() { return document.getElementById('customPythonToolSearchClear'); },
        get createBtn() { return document.getElementById('customPythonToolsCreateButton'); },
        get exportBtn() { return document.getElementById('customPythonToolsExportButton'); },
        get importBtn() { return document.getElementById('customPythonToolsImportButton'); },
        get importFileInput() { return document.getElementById('customPythonToolsImportFileInput'); },
        get importOverlay() { return document.getElementById('importCustomPythonToolsOverlay'); },
        get importClose() { return document.getElementById('importCustomPythonToolsClose'); },
        get importCancel() { return document.getElementById('importCustomPythonToolsCancel'); },
        get importConfirm() { return document.getElementById('importCustomPythonToolsConfirm'); },
        get importList() { return document.getElementById('importCustomPythonToolsList'); },
        get importSelectAll() { return document.getElementById('importCustomPythonToolsSelectAll'); },
        get importFileName() { return document.getElementById('importCustomPythonToolsFileName'); },
        get importStatus() { return document.getElementById('importCustomPythonToolsStatus'); },
        get backBtn() { return document.getElementById('customPythonToolsBack'); },
        // Create form
        get createForm() { return document.getElementById('customPythonToolsCreateForm'); },
        get createEnabled() { return document.getElementById('customPythonToolsCreateEnabled'); },
        get createTimeout() { return document.getElementById('customPythonToolsCreateTimeout'); },
        get createSource() { return document.getElementById('customPythonToolsCreateSource'); },
        get createArguments() { return document.getElementById('customPythonToolsCreateArguments'); },
        get createCancel() { return document.getElementById('customPythonToolsCreateCancel'); },
        get createTest() { return document.getElementById('customPythonToolsCreateTest'); },
        get createSubmit() { return document.getElementById('customPythonToolsCreateSubmit'); },
        get createDefinitionPreview() { return document.getElementById('customPythonToolsCreateDefinitionPreview'); },
        get createResultPreview() { return document.getElementById('customPythonToolsCreateResultPreview'); },
        get createStatus() { return document.getElementById('customPythonToolsCreateStatus'); },
        // Edit form
        get editForm() { return document.getElementById('customPythonToolsEditForm'); },
        get editEnabled() { return document.getElementById('customPythonToolsEditEnabled'); },
        get editTimeout() { return document.getElementById('customPythonToolsEditTimeout'); },
        get editSource() { return document.getElementById('customPythonToolsEditSource'); },
        get editArguments() { return document.getElementById('customPythonToolsEditArguments'); },
        get editCancel() { return document.getElementById('customPythonToolsEditCancel'); },
        get editTest() { return document.getElementById('customPythonToolsEditTest'); },
        get editSubmit() { return document.getElementById('customPythonToolsEditSubmit'); },
        get editDelete() { return document.getElementById('customPythonToolsEditDelete'); },
        get editDefinitionPreview() { return document.getElementById('customPythonToolsEditDefinitionPreview'); },
        get editResultPreview() { return document.getElementById('customPythonToolsEditResultPreview'); },
        get editStatus() { return document.getElementById('customPythonToolsEditStatus'); },
        // Delete overlay
        get deleteOverlay() { return document.getElementById('deleteCustomToolOverlay'); },
        get deleteCancel() { return document.getElementById('deleteCustomToolCancelButton'); },
        get deletePrimary() { return document.getElementById('deleteCustomToolPrimaryButton'); },
    };

    function isPageActive(pageEl) {
        return Boolean(pageEl && !pageEl.hidden);
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    async function fetchJson(url, options = {}) {
        const response = await window.authedFetch(url, options);
        const contentLength = response.headers.get('Content-Length');
        const contentType = response.headers.get('Content-Type') || '';
        const hasJsonBody = contentType.toLowerCase().includes('application/json');

        if (!response.ok) {
            const rawText = await response.text().catch(() => '');
            let payload = null;
            if (rawText) {
                try { payload = JSON.parse(rawText); } catch (_) { payload = null; }
            }
            const detail = payload?.detail;
            const message = typeof detail === 'string'
                ? detail
                : (detail?.message || payload?.message || `HTTP ${response.status}`);
            const error = new Error(message);
            error.payload = payload;
            throw error;
        }

        if (response.status === 204 || contentLength === '0' || !hasJsonBody) return null;

        const rawText = await response.text();
        if (!rawText || !rawText.trim()) return null;
        try { return JSON.parse(rawText); } catch (_) { return null; }
    }

    function parseJsonField(value, label) {
        const raw = String(value || '').trim();
        if (!raw) return {};
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
            return parsed;
        } catch (_) {
            throw new Error(formatT(
                'custom_tools_test_args_invalid_json',
                '{label} must be a valid JSON object.',
                { label }
            ));
        }
    }

    function getCustomToolErrorMessage(error, fallback) {
        const detail = error?.payload?.detail;
        if (detail?.code === 'custom_tool_argument_required' && detail.path) {
            return formatT(
                'custom_tools_argument_required',
                'The required test argument “{path}” is missing.',
                { path: detail.path }
            );
        }
        if (detail?.code === 'custom_tool_argument_invalid' && detail.path) {
            return formatT(
                'custom_tools_argument_invalid',
                'The test argument “{path}” does not match the tool definition.',
                { path: detail.path }
            );
        }
        return error?.message || fallback;
    }

    function setStatus(host, message = '', kind = '') {
        if (!host) return;
        const normalizedMessage = String(message || '').trim();
        host.textContent = normalizedMessage;
        host.hidden = !normalizedMessage;
        if (kind) {
            host.dataset.state = kind;
        } else {
            delete host.dataset.state;
        }
    }

    function setButtonLabel(button, isBusy, busyLabel) {
        if (!button) return;
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = button.textContent || '';
        }
        button.disabled = Boolean(isBusy);
        button.textContent = isBusy ? busyLabel : button.dataset.defaultLabel;
    }

    function setImportStatus(message = '', kind = '') {
        if (!DOM.importStatus) return;
        if (!message) {
            DOM.importStatus.hidden = true;
            DOM.importStatus.textContent = '';
            DOM.importStatus.className = 'provider-import-status';
            return;
        }
        DOM.importStatus.hidden = false;
        DOM.importStatus.textContent = message;
        DOM.importStatus.className = `provider-import-status ${kind}`.trim();
    }

    function resolveImportToolsFromPayload(payload, importContract = null) {
        if (!payload || typeof payload !== 'object') {
            throw new Error(t('custom_tools_import_invalid_export', 'Invalid export file.'));
        }
        const expectedType = importContract?.export_type || 'custom_python_tool';
        if (payload.export_type !== expectedType) {
            throw new Error(t('custom_tools_import_unsupported_type', 'Unsupported export file type.'));
        }
        const expectedVersion = importContract?.export_version;
        if (expectedVersion !== undefined && payload.export_version !== expectedVersion) {
            throw new Error(formatT(
                'custom_tools_import_version_mismatch',
                'Unsupported export version. Expected {version}.',
                { version: expectedVersion }
            ));
        }
        const tools = payload?.data?.tools;
        return Array.isArray(tools) ? tools : [];
    }

    function resetImportState() {
        state.importPayload = null;
        state.importTools = [];
        state.importSelected = new Set();
        state.importFileName = '';
        if (DOM.importList) DOM.importList.innerHTML = '';
        if (DOM.importFileName) DOM.importFileName.textContent = '';
        if (DOM.importSelectAll) DOM.importSelectAll.checked = false;
        setImportStatus();
    }

    function closeImportOverlay() {
        DOM.importOverlay?.classList.remove('active');
        if (DOM.importOverlay) DOM.importOverlay.hidden = true;
        resetImportState();
    }

    function openImportOverlay() {
        if (!DOM.importOverlay) return;
        DOM.importOverlay.hidden = false;
        DOM.importOverlay.classList.add('active');
        if (DOM.importFileName) DOM.importFileName.textContent = state.importFileName || '';
        if (DOM.importSelectAll) {
            DOM.importSelectAll.checked = state.importTools.length > 0
                && state.importTools.length === state.importSelected.size;
        }
        setImportStatus();
        DOM.importConfirm?.focus();
    }

    function renderImportToolsList() {
        const host = DOM.importList;
        if (!host) return;
        host.innerHTML = '';
        if (!state.importTools.length) {
            host.innerHTML = `<div class="provider-import-empty">${escapeHtml(t('custom_tools_import_empty', 'No custom Python tools found in this file.'))}</div>`;
            return;
        }
        const fragment = document.createDocumentFragment();
        state.importTools.forEach((tool, index) => {
            const selected = state.importSelected.has(index);
            const entry = document.createElement('label');
            entry.className = 'provider-import-entry';
            entry.setAttribute('role', 'option');
            entry.setAttribute('aria-selected', selected ? 'true' : 'false');

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = selected;
            checkbox.dataset.toolIndex = String(index);
            checkbox.addEventListener('change', handleImportToolToggle);
            entry.appendChild(checkbox);

            const content = document.createElement('div');
            content.className = 'provider-import-entry-content';

            const title = document.createElement('p');
            title.className = 'provider-import-entry-title';
            title.textContent = tool?.display_name || tool?.name || t('custom_tools_untitled', 'Untitled Tool');
            content.appendChild(title);

            const description = document.createElement('div');
            description.className = 'provider-import-entry-meta';
            description.textContent = tool?.description || t('custom_tools_no_description', 'No description');
            content.appendChild(description);

            const meta = document.createElement('div');
            meta.className = 'provider-import-entry-meta';
            meta.textContent = `${tool?.name || ''} · ${t('custom_tools_timeout_short', 'Timeout')}: ${tool?.timeout_seconds || 30}s`;
            content.appendChild(meta);

            entry.appendChild(content);
            fragment.appendChild(entry);
        });
        host.appendChild(fragment);
    }

    function handleImportToolToggle(event) {
        const checkbox = event.currentTarget;
        const index = Number.parseInt(checkbox.dataset.toolIndex || '', 10);
        if (Number.isNaN(index)) return;
        if (checkbox.checked) {
            state.importSelected.add(index);
        } else {
            state.importSelected.delete(index);
        }
        checkbox.closest('.provider-import-entry')?.setAttribute('aria-selected', checkbox.checked ? 'true' : 'false');
        if (DOM.importSelectAll) {
            DOM.importSelectAll.checked = state.importTools.length > 0
                && state.importTools.length === state.importSelected.size;
        }
        setImportStatus();
    }

    function toggleSelectAllImports(event) {
        const checked = Boolean(event.currentTarget?.checked);
        state.importSelected.clear();
        if (checked) {
            state.importTools.forEach((_, index) => state.importSelected.add(index));
        }
        renderImportToolsList();
        setImportStatus();
    }

    function formatImportErrorEntry(entry) {
        if (!entry || typeof entry !== 'object') return '';
        const rawIndex = entry.index !== undefined ? Number(entry.index) : NaN;
        const displayIndex = Number.isFinite(rawIndex) ? rawIndex + 1 : '?';
        const name = entry.name ? ` (${entry.name})` : '';
        const message = entry.error
            ? (typeof entry.error === 'string' ? entry.error : JSON.stringify(entry.error))
            : 'Unknown error.';
        return `• Item ${displayIndex}${name}: ${message}`;
    }

    function renderDefinitionPreview(host, definition) {
        if (!host) return;
        if (!definition) {
            host.className = 'custom-python-tools-preview-empty';
            host.textContent = t('custom_tools_definition_empty', 'Run validation or open a saved tool to inspect its resolved schema.');
            return;
        }
        host.className = 'custom-python-tools-preview-card-body';
        host.innerHTML = `
            <div class="custom-python-tools-meta">
                <div><strong>${escapeHtml(t('custom_tools_name_label', 'Name'))}:</strong> ${escapeHtml(definition.name || '')}</div>
                <div><strong>${escapeHtml(t('custom_tools_display_name_label', 'Display Name'))}:</strong> ${escapeHtml(definition.display_name || definition.name || '')}</div>
                <div><strong>${escapeHtml(t('custom_tools_description_label', 'Description'))}:</strong> ${escapeHtml(definition.description || '')}</div>
            </div>
            <pre>${escapeHtml(JSON.stringify(definition.parameters || {}, null, 2))}</pre>
        `;
    }

    function renderResultPreview(host, output) {
        if (!host) return;
        if (!output) {
            host.className = 'custom-python-tools-preview-empty';
            host.textContent = t('custom_tools_result_empty', 'No test run yet.');
            return;
        }
        host.className = 'custom-python-tools-preview-card-body';
        host.innerHTML = `<pre>${escapeHtml(JSON.stringify(output, null, 2))}</pre>`;
    }

    function getCreateFormSnapshot() {
        return JSON.stringify({
            enabled: Boolean(DOM.createEnabled?.checked),
            timeout: String(DOM.createTimeout?.value || '30'),
            source: String(DOM.createSource?.value || '').trim(),
            arguments: String(DOM.createArguments?.value || '{}').trim(),
        });
    }

    function getEditFormSnapshot() {
        return JSON.stringify({
            id: String(state.editingId || ''),
            enabled: Boolean(DOM.editEnabled?.checked),
            timeout: String(DOM.editTimeout?.value || '30'),
            source: String(DOM.editSource?.value || '').trim(),
            arguments: String(DOM.editArguments?.value || '{}').trim(),
        });
    }

    function renderToolCard(tool) {
        const statusLabel = tool.enabled
            ? t('custom_tools_status_enabled', 'Enabled')
            : t('custom_tools_status_disabled', 'Disabled');
        const iconColor = tool.enabled ? '#2563eb' : '#94a3b8';
        return `
            <div class="admin-skill-card" data-tool-id="${escapeHtml(tool.id)}">
                <div class="admin-skill-icon" style="background-color: ${iconColor}">
                    ${Icons.code}
                </div>
                <div class="settings-row-left">
                    <h4 class="settings-row-title">${escapeHtml(tool.display_name || tool.name || t('custom_tools_untitled', 'Untitled Tool'))}</h4>
                    <p class="settings-row-desc two-lines">${escapeHtml(tool.description || t('custom_tools_no_description', 'No description'))}</p>
                    <p class="settings-row-desc">${escapeHtml(tool.name || '')} · ${escapeHtml(statusLabel)} · ${escapeHtml(t('custom_tools_timeout_short', 'Timeout'))}: ${escapeHtml(tool.timeout_seconds)}s</p>
                </div>
                <div class="admin-skill-actions">
                    <button type="button" class="om-button border cancel" data-action="edit" data-tool-id="${escapeHtml(tool.id)}">
                        ${Icons.create}
                        ${t('btn_edit', 'Edit')}
                    </button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete" data-tool-id="${escapeHtml(tool.id)}" data-tool-name="${escapeHtml(tool.display_name || tool.name || '')}">
                        ${Icons?.trash || ''}
                        ${t('btn_delete', 'Delete')}
                    </button>
                </div>
            </div>
        `;
    }

    function renderEmptyState(isFiltered) {
        const title = isFiltered
            ? t('custom_tools_empty_filtered_title', 'No matching tools')
            : t('custom_tools_empty_title', 'No custom Python tools yet');
        const description = isFiltered
            ? t('custom_tools_empty_filtered_desc', 'No tools match your current search. Try adjusting or clearing the filter.')
            : t('custom_tools_empty_desc', 'Create custom Python tools here to make them available to models.');
        return `
            <div class="user-notifications-empty provider-empty-state">
                <div class="user-notifications-empty-icon">
                    ${Icons.create}
                </div>
                <h3 class="user-notifications-empty-title">${title}</h3>
                <p class="user-notifications-empty-text">${description}</p>
            </div>
        `;
    }

    function collectPayload(mode) {
        const source = mode === 'edit' ? DOM.editSource : DOM.createSource;
        const enabled = mode === 'edit' ? DOM.editEnabled : DOM.createEnabled;
        const timeout = mode === 'edit' ? DOM.editTimeout : DOM.createTimeout;
        const sourceCode = String(source?.value || '');
        if (!sourceCode.trim()) {
            throw new Error(t('custom_tools_source_required', 'Python source code is required.'));
        }
        return {
            source_code: sourceCode,
            enabled: Boolean(enabled?.checked),
            timeout_seconds: Number.parseInt(timeout?.value || '30', 10) || 30,
        };
    }

    const Manager = {
        init() {
            if (state.initialized) return;
            this.setupEventListeners();
            this.registerEscapeShortcut();
            this.registerUnsavedGuard();
            state.initialized = true;
        },

        setupEventListeners() {
            // Back to tools
            DOM.backBtn?.addEventListener('click', () => {
                window.activateAdminPage?.('tools');
            });

            // List page buttons
            DOM.createBtn?.addEventListener('click', () => this.showCreatePage());
            DOM.exportBtn?.addEventListener('click', () => this.handleExport());
            DOM.importBtn?.addEventListener('click', () => DOM.importFileInput?.click());
            DOM.importFileInput?.addEventListener('change', (event) => this.handleImportFile(event));

            // Search
            DOM.searchInput?.addEventListener('input', () => this.handleSearch());
            DOM.searchClear?.addEventListener('click', () => {
                if (DOM.searchInput) DOM.searchInput.value = '';
                this.handleSearch();
            });

            // List click delegation
            DOM.list?.addEventListener('click', (e) => {
                const editBtn = e.target.closest('[data-action="edit"]');
                if (editBtn) {
                    const toolId = editBtn.dataset.toolId;
                    if (toolId) this.showEditPage(toolId);
                    return;
                }
                const deleteBtn = e.target.closest('[data-action="delete"]');
                if (deleteBtn) {
                    const toolId = deleteBtn.dataset.toolId;
                    const toolName = deleteBtn.dataset.toolName || t('custom_tools_item_fallback', 'this tool');
                    if (toolId) this.confirmDelete(toolId, toolName);
                    return;
                }
            });

            // Create form
            DOM.createCancel?.addEventListener('click', () => this.handleBackNavigation());
            DOM.createTest?.addEventListener('click', () => this.handleTest('create'));
            DOM.createForm?.addEventListener('submit', (e) => {
                e.preventDefault();
                this.handleCreate();
            });

            // Edit form
            DOM.editCancel?.addEventListener('click', () => this.handleBackNavigation());
            DOM.editTest?.addEventListener('click', () => this.handleTest('edit'));
            DOM.editDelete?.addEventListener('click', () => {
                if (state.editingId) {
                    const tool = state.tools.find(t => String(t.id) === String(state.editingId));
                    this.confirmDelete(state.editingId, tool?.display_name || tool?.name || '');
                }
            });
            DOM.editForm?.addEventListener('submit', (e) => {
                e.preventDefault();
                this.handleUpdate();
            });

            // Delete overlay
            DOM.deleteCancel?.addEventListener('click', () => this.closeDeleteOverlay());
            DOM.deletePrimary?.addEventListener('click', () => this.handleDeleteConfirm());

            DOM.importOverlay?.addEventListener('click', (event) => {
                if (event.target === DOM.importOverlay) {
                    closeImportOverlay();
                }
            });
            DOM.importClose?.addEventListener('click', closeImportOverlay);
            DOM.importCancel?.addEventListener('click', closeImportOverlay);
            DOM.importConfirm?.addEventListener('click', () => this.submitSelectedImports());
            DOM.importSelectAll?.addEventListener('change', toggleSelectAllImports);
        },

        registerEscapeShortcut() {
            if (state.escapeRegistration || typeof window.registerEscapeHandler !== 'function') return;
            state.escapeRegistration = window.registerEscapeHandler({
                id: 'custom-python-tools-escape',
                priority: 140,
                isActive: () => isPageActive(DOM.createPage) || isPageActive(DOM.editPage),
                close: () => this.handleBackNavigation(),
            });
        },

        registerUnsavedGuard() {
            if (state.unsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') return;
            window.unsavedChangesManager.register({
                id: UNSAVED_GUARD_ID,
                priority: 170,
                isActive: () => isPageActive(DOM.createPage) || isPageActive(DOM.editPage),
                isDirty: () => this.hasPendingChanges(),
                discard: () => {
                    if (isPageActive(DOM.createPage)) state.createInitialSnapshot = getCreateFormSnapshot();
                    if (isPageActive(DOM.editPage)) state.editInitialSnapshot = getEditFormSnapshot();
                },
                getCopy: () => ({
                    subtitle: t('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
                }),
            });
            state.unsavedGuardRegistered = true;
        },

        hasPendingChanges() {
            if (isPageActive(DOM.createPage)) {
                if (state.createInitialSnapshot === null) return false;
                return getCreateFormSnapshot() !== state.createInitialSnapshot;
            }
            if (isPageActive(DOM.editPage)) {
                if (state.editInitialSnapshot === null) return false;
                return getEditFormSnapshot() !== state.editInitialSnapshot;
            }
            return false;
        },

        requestUnsavedConfirmation(onConfirm) {
            if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
                const prompted = window.unsavedChangesManager.confirmIfNeeded({
                    id: UNSAVED_GUARD_ID,
                    onConfirm,
                });
                if (prompted) return;
            }
            onConfirm?.();
        },

        handleBackNavigation() {
            if (!isPageActive(DOM.createPage) && !isPageActive(DOM.editPage)) return;
            this.requestUnsavedConfirmation(() => this.showListPage());
        },

        handleSearch() {
            const query = String(DOM.searchInput?.value || '').trim().toLowerCase();
            if (DOM.searchClear) DOM.searchClear.hidden = !query;
            this.renderTools(query);
        },

        showListPage() {
            if (typeof showPage === 'function') showPage('custom-python-tools');
        },

        showCreatePage() {
            state.editingId = null;
            state.createLastTestResult = null;

            if (DOM.createEnabled) DOM.createEnabled.checked = true;
            if (DOM.createTimeout) DOM.createTimeout.value = '30';
            if (DOM.createSource) DOM.createSource.value = defaultSourceCode;
            if (DOM.createArguments) DOM.createArguments.value = '{}';

            renderDefinitionPreview(DOM.createDefinitionPreview, null);
            renderResultPreview(DOM.createResultPreview, null);
            setStatus(DOM.createStatus, '', '');

            state.createInitialSnapshot = getCreateFormSnapshot();

            if (typeof showPage === 'function') showPage('custom-python-tools-create', { history: 'none' });
        },

        async showEditPage(toolId) {
            try {
                const tool = await fetchJson(`${API_BASE}/${encodeURIComponent(toolId)}`);
                if (!tool) {
                    window.notifyError?.(t('custom_tools_not_found', 'Tool not found. It may have been deleted.'));
                    this.showListPage();
                    return;
                }

                state.editingId = tool.id;
                state.editLastTestResult = null;

                if (DOM.editEnabled) DOM.editEnabled.checked = Boolean(tool.enabled);
                if (DOM.editTimeout) DOM.editTimeout.value = String(tool.timeout_seconds || 30);
                if (DOM.editSource) DOM.editSource.value = tool.source_code || defaultSourceCode;
                if (DOM.editArguments) DOM.editArguments.value = '{}';

                renderDefinitionPreview(DOM.editDefinitionPreview, tool.tool_schema || null);
                renderResultPreview(DOM.editResultPreview, null);
                setStatus(DOM.editStatus, '', '');

                state.editInitialSnapshot = getEditFormSnapshot();

                if (typeof showPage === 'function') showPage('custom-python-tools-edit', { history: 'none' });
            } catch (error) {
                window.notifyError?.(error.message || t('custom_tools_load_failed', 'Failed to load tool details.'));
                this.showListPage();
            }
        },

        renderTools(searchQuery) {
            const host = DOM.list;
            if (!host) return;

            let tools = state.tools;
            const query = (searchQuery || '').toLowerCase();
            if (query) {
                tools = tools.filter(tool => {
                    const name = String(tool.name || '').toLowerCase();
                    const displayName = String(tool.display_name || '').toLowerCase();
                    const description = String(tool.description || '').toLowerCase();
                    return name.includes(query) || displayName.includes(query) || description.includes(query);
                });
            }

            if (!tools.length) {
                host.innerHTML = renderEmptyState(Boolean(query));
                return;
            }
            host.innerHTML = tools.map(renderToolCard).join('');
        },

        async loadTools() {
            if (state.loading) return;
            state.loading = true;
            try {
                state.tools = await fetchJson(API_BASE) || [];
                this.renderTools(DOM.searchInput?.value || '');
            } catch (error) {
                window.notifyError?.(error.message || t('custom_tools_load_failed', 'Failed to load custom Python tools.'));
            } finally {
                state.loading = false;
            }
        },

        async handleExport() {
            try {
                setButtonLabel(DOM.exportBtn, true, t('admin_exporting_ellipsis', 'Exporting...'));
                const payload = await fetchJson(`${API_BASE}/export`);
                const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
                const timestamp = new Date().toISOString().replace(/[:\.]/g, '-');
                const link = document.createElement('a');
                link.href = URL.createObjectURL(blob);
                link.download = `custom-python-tools-${timestamp}.json`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                URL.revokeObjectURL(link.href);
                window.notifySuccess?.(t('custom_tools_export_success', 'Custom Python tools export downloaded successfully.'));
            } catch (error) {
                window.notifyError?.(error.message || t('custom_tools_export_failed', 'Failed to export custom Python tools.'));
            } finally {
                setButtonLabel(DOM.exportBtn, false, '');
            }
        },

        async handleImportFile(event) {
            const input = event?.target;
            if (!input?.files?.length) return;
            const [file] = input.files;
            input.value = '';
            const isJsonFile = file && (file.type === 'application/json' || file.name?.toLowerCase().endsWith('.json'));
            if (!isJsonFile) {
                window.notifyError?.(t('custom_tools_import_select_json', 'Please select a valid JSON file.'));
                return;
            }

            try {
                const payload = JSON.parse(await file.text());
                const importContract = await this.loadImportContract();
                const tools = resolveImportToolsFromPayload(payload, importContract);
                if (!tools.length) {
                    window.notifyWarning?.(t('custom_tools_import_empty', 'No custom Python tools found in this file.'));
                    return;
                }
                state.importPayload = payload;
                state.importTools = tools;
                state.importSelected = new Set(tools.map((_, index) => index));
                state.importFileName = file.name || 'custom-python-tools.json';
                renderImportToolsList();
                openImportOverlay();
            } catch (error) {
                window.notifyError?.(error.message || t('custom_tools_import_failed', 'Failed to import custom Python tools.'));
            }
        },

        async loadImportContract() {
            try {
                const contract = await fetchJson(`${API_BASE}/import-contract`);
                if (
                    contract
                    && contract.export_type === 'custom_python_tool'
                    && typeof contract.export_version === 'number'
                ) {
                    return contract;
                }
            } catch (_) {
                // The import endpoint remains authoritative if metadata is unavailable.
            }
            return null;
        },

        async submitSelectedImports() {
            if (!state.importPayload) {
                setImportStatus(t('custom_tools_import_choose_file_first', 'Please choose a custom Python tools file first.'));
                return;
            }
            if (!state.importSelected.size) {
                setImportStatus(t('custom_tools_import_select_one', 'Select at least one custom Python tool to import.'));
                return;
            }

            try {
                setButtonLabel(DOM.importConfirm, true, t('admin_importing_ellipsis', 'Importing...'));
                const selectedIndices = Array.from(state.importSelected).sort((a, b) => a - b);
                const payload = {
                    ...state.importPayload,
                    data: {
                        ...(state.importPayload.data || {}),
                        tools: selectedIndices.map((index) => state.importTools[index]).filter(Boolean),
                    },
                };
                const result = await fetchJson(`${API_BASE}/import`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const createdCount = Array.isArray(result?.created) ? result.created.length : 0;
                const errorCount = Array.isArray(result?.errors) ? result.errors.length : 0;
                if (createdCount) {
                    window.notifySuccess?.(
                        createdCount === 1
                            ? t('custom_tools_import_success_single', 'Imported 1 custom Python tool successfully.')
                            : formatT('custom_tools_import_success_plural', 'Imported {count} custom Python tools successfully.', { count: createdCount })
                    );
                }
                if (errorCount) {
                    const details = result.errors.map((entry) => formatImportErrorEntry(entry)).filter(Boolean).join('\n');
                    setImportStatus(details || t('custom_tools_import_partial_failed', 'Some custom Python tools could not be imported.'));
                    window.notifyWarning?.(t('custom_tools_import_partial_failed', 'Some custom Python tools could not be imported.'));
                } else {
                    closeImportOverlay();
                }
                await this.loadTools();
            } catch (error) {
                setImportStatus(error.message || t('custom_tools_import_failed', 'Failed to import custom Python tools.'));
                window.notifyError?.(error.message || t('custom_tools_import_failed', 'Failed to import custom Python tools.'));
            } finally {
                setButtonLabel(DOM.importConfirm, false, '');
            }
        },

        async handleTest(mode) {
            const statusEl = mode === 'edit' ? DOM.editStatus : DOM.createStatus;
            const defPreview = mode === 'edit' ? DOM.editDefinitionPreview : DOM.createDefinitionPreview;
            const resPreview = mode === 'edit' ? DOM.editResultPreview : DOM.createResultPreview;
            const argsEl = mode === 'edit' ? DOM.editArguments : DOM.createArguments;

            try {
                const payload = collectPayload(mode);
                payload.arguments = parseJsonField(
                    argsEl?.value || '{}',
                    t('custom_tools_test_args_label', 'Test Arguments')
                );
                const result = await fetchJson(`${API_BASE}/test`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (mode === 'edit') {
                    state.editLastTestResult = result?.output || null;
                } else {
                    state.createLastTestResult = result?.output || null;
                }
                renderDefinitionPreview(defPreview, result?.definition || null);
                renderResultPreview(resPreview, result?.output || null);
                setStatus(statusEl, t('custom_tools_test_success', 'Validation and test execution succeeded.'), 'success');
                window.notifySuccess?.(t('custom_tools_test_success', 'Validation and test execution succeeded.'));
            } catch (error) {
                if (mode === 'edit') {
                    state.editLastTestResult = null;
                } else {
                    state.createLastTestResult = null;
                }
                renderResultPreview(resPreview, null);
                const message = getCustomToolErrorMessage(
                    error,
                    t('custom_tools_test_failed', 'Validation or test execution failed.')
                );
                setStatus(statusEl, message, 'error');
                window.notifyError?.(message);
            }
        },

        async handleCreate() {
            const btn = DOM.createSubmit;
            try {
                const payload = collectPayload('create');
                const submittedSnapshot = getCreateFormSnapshot();
                if (btn) { btn.disabled = true; btn.textContent = t('admin_creating_ellipsis', 'Creating...'); }

                await fetchJson(API_BASE, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                state.createInitialSnapshot = submittedSnapshot;
                window.notifySuccess?.(t('custom_tools_save_success', 'Custom Python tool saved.'));
                this.showListPage();
            } catch (error) {
                setStatus(DOM.createStatus, error.message || t('custom_tools_save_failed', 'Failed to save custom Python tool.'), 'error');
                window.notifyError?.(error.message || t('custom_tools_save_failed', 'Failed to save custom Python tool.'));
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = t('custom_tool_create_button', 'Create Tool'); }
            }
        },

        async handleUpdate() {
            if (!state.editingId) return;
            const btn = DOM.editSubmit;
            try {
                const payload = collectPayload('edit');
                const submittedSnapshot = getEditFormSnapshot();
                if (btn) { btn.disabled = true; btn.textContent = t('admin_saving', 'Saving...'); }

                await fetchJson(`${API_BASE}/${encodeURIComponent(state.editingId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                state.editInitialSnapshot = submittedSnapshot;
                window.notifySuccess?.(t('custom_tools_save_success', 'Custom Python tool saved.'));
                this.showListPage();
            } catch (error) {
                setStatus(DOM.editStatus, error.message || t('custom_tools_save_failed', 'Failed to save custom Python tool.'), 'error');
                window.notifyError?.(error.message || t('custom_tools_save_failed', 'Failed to save custom Python tool.'));
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = t('btn_save_changes', 'Save Changes'); }
            }
        },

        confirmDelete(toolId, toolName) {
            state.pendingDeleteToolId = toolId;
            const overlay = DOM.deleteOverlay;
            const message = document.getElementById('deleteCustomToolMessage');
            if (message) {
                message.textContent = t('confirm_delete_tool', `Are you sure you want to delete "${toolName || t('this_tool', 'this tool')}"? This action cannot be undone.`);
            }
            if (overlay) {
                overlay.hidden = false;
                overlay.setAttribute('aria-hidden', 'false');
            }
        },

        async handleDeleteConfirm() {
            const toolId = state.pendingDeleteToolId;
            if (!toolId) {
                this.closeDeleteOverlay();
                return;
            }

            const primaryBtn = DOM.deletePrimary;
            if (primaryBtn && primaryBtn.disabled) {
                return;
            }
            if (primaryBtn) {
                primaryBtn.disabled = true;
                const textEl = primaryBtn.querySelector('#deleteCustomToolPrimaryText');
                textEl?.classList.add('loading');
            }

            try {
                await fetchJson(`${API_BASE}/${encodeURIComponent(toolId)}`, { method: 'DELETE' });
                window.notifySuccess?.(t('custom_tools_delete_success', 'Custom Python tool deleted.'));
                this.closeDeleteOverlay();
                // If we deleted the tool we were editing, go back to list
                if (String(state.editingId) === String(toolId)) {
                    state.editingId = null;
                    state.editInitialSnapshot = null;
                }
                if (isPageActive(DOM.editPage)) {
                    this.showListPage();
                } else {
                    await this.loadTools();
                }
            } catch (error) {
                window.notifyError?.(error.message || t('custom_tools_delete_failed', 'Failed to delete custom Python tool.'));
                this.closeDeleteOverlay();
            } finally {
                if (primaryBtn) {
                    primaryBtn.disabled = false;
                    const textEl = primaryBtn.querySelector('#deleteCustomToolPrimaryText');
                    textEl?.classList.remove('loading');
                }
                state.pendingDeleteToolId = null;
            }
        },

        closeDeleteOverlay() {
            const overlay = DOM.deleteOverlay;
            if (overlay) {
                overlay.hidden = true;
                overlay.setAttribute('aria-hidden', 'true');
            }
            if (DOM.deletePrimary) {
                DOM.deletePrimary.disabled = false;
                const textEl = DOM.deletePrimary.querySelector('#deleteCustomToolPrimaryText');
                textEl?.classList.remove('loading');
            }
            state.pendingDeleteToolId = null;
        },
    };

    window.initCustomPythonToolsPage = () => {
        state.active = true;
        Manager.init();
        Manager.loadTools();
    };

    window.teardownCustomPythonToolsPage = () => {
        state.active = false;
    };
})();
