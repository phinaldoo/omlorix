(function () {
    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const root = document.getElementById('serviceConnectionsRoot');
    const backButton = document.getElementById('serviceConnectionsBack');
    if (!root) {
        window.initServiceConnectionsPage = () => {};
        window.teardownServiceConnectionsPage = () => {};
        return;
    }

    const state = {
        initialized: false,
        loading: false,
        connections: [],
        search: '',
        editingId: null,
        pendingDeleteId: null,
        formLastFocusedElement: null,
        deleteLastFocusedElement: null,
        escapeRegistration: null,
    };
    let i18nListenerBound = false;

    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    };

    const sharedIcons = globalThis.Icons || {};
    const serviceConnectionActionIcons = {
        "plus": sharedIcons.plus,
        "refresh": sharedIcons.refresh,
        "edit": sharedIcons.edit,
        "search": sharedIcons.magnifyingGlass,
        "close": sharedIcons.close,
        "clipboard": sharedIcons.clipboard || sharedIcons.paste || sharedIcons.plus,
    };

    const icon = (name) => {
        if (name === 'trash') {
            return sharedIcons.trash || '';
        }
        return serviceConnectionActionIcons[name] || '';
    };

    const api = {
        async list() {
            const res = await window.authedFetch('/api/v1/service-connections');
            if (!res.ok) throw new Error(t('service_connections_load_failed', 'Unable to load service connections.'));
            return res.json();
        },
        async create(payload) {
            const res = await window.authedFetch('/api/v1/service-connections', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || t('service_connection_save_failed', 'Unable to save service connection.'));
            }
            return res.json();
        },
        async update(id, payload) {
            const res = await window.authedFetch(`/api/v1/service-connections/${encodeURIComponent(id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || t('service_connection_save_failed', 'Unable to save service connection.'));
            }
            return res.json();
        },
        async remove(id) {
            const res = await window.authedFetch(`/api/v1/service-connections/${encodeURIComponent(id)}`, {
                method: 'DELETE',
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || t('service_connection_delete_failed', 'Unable to delete service connection.'));
            }
            return res.json();
        },
        async refreshStatus(id) {
            const res = await window.authedFetch(`/api/v1/service-connections/${encodeURIComponent(id)}/status`, {
                method: 'POST',
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || t('service_connection_status_failed', 'Unable to refresh service status.'));
            }
            return res.json();
        },
    };

    const statusLabel = (status) => {
        switch (String(status || '').toLowerCase()) {
            case 'up':
                return t('service_status_available', 'Available');
            case 'down':
                return t('service_status_unavailable', 'Unavailable');
            case 'disabled':
                return t('service_status_disabled', 'Disabled');
            default:
                return t('service_status_unknown', 'Unknown');
        }
    };

    const apiKeyStatusLabel = (status) => {
        switch (String(status || '').toLowerCase()) {
            case 'valid':
                return t('service_connection_api_key_valid', 'Valid');
            case 'invalid':
                return t('service_connection_api_key_invalid', 'Invalid');
            case 'disabled':
                return t('service_status_disabled', 'Disabled');
            default:
                return t('service_status_unknown', 'Unknown');
        }
    };

    const purposeBadges = (connection) => {
        const badges = [];
        if (connection.enabled_for_code_execution) {
            badges.push(`<span class="service-connection-purpose">${t('service_connections_code_execution', 'Code execution')}</span>`);
        }
        if (connection.enabled_for_latex_pdf) {
            badges.push(`<span class="service-connection-purpose">${t('service_connections_latex_pdf', 'LaTeX rendering')}</span>`);
        }
        if (connection.enabled_for_slide_renderer) {
            badges.push(`<span class="service-connection-purpose">${t('service_connections_slide_renderer', 'Slide renderer')}</span>`);
        }
        return badges.length ? badges.join('') : `<span class="service-connection-purpose muted">${t('service_connections_no_purposes', 'None')}</span>`;
    };

    const statusDetails = (connection) => {
        const status = connection.status || {};
        const purposes = [];
        if (connection.enabled_for_code_execution) {
            purposes.push({
                label: t('service_connections_code_short', 'Code'),
                status: status.code_execution,
                auth: status.code_execution_auth,
            });
        }
        if (connection.enabled_for_latex_pdf) {
            purposes.push({
                label: t('service_connections_latex_short', 'PDF'),
                status: status.latex_pdf,
                auth: status.latex_pdf_auth,
            });
        }
        if (connection.enabled_for_slide_renderer) {
            purposes.push({
                label: t('service_connections_slide_short', 'Slides'),
                status: status.slide_renderer,
                auth: status.slide_renderer_auth,
            });
        }

        const parts = purposes.map((purpose) => `${purpose.label}: ${statusLabel(purpose.status)}`);
        const authPurposes = purposes.filter((purpose) => purpose.auth && purpose.auth !== 'disabled');
        const distinctAuthStatuses = new Set(authPurposes.map((purpose) => purpose.auth));
        if (distinctAuthStatuses.size === 1) {
            const authStatus = authPurposes[0]?.auth;
            parts.push(`${t('service_connection_api_key_label', 'API Key')}: ${apiKeyStatusLabel(authStatus)}`);
        } else if (distinctAuthStatuses.size > 1) {
            authPurposes.forEach((purpose) => {
                parts.push(
                    `${purpose.label} ${t('service_connection_api_key_label', 'API Key')}: ${apiKeyStatusLabel(purpose.auth)}`
                );
            });
        }
        return parts.join(' · ');
    };

    const filteredConnections = () => {
        const query = state.search.trim().toLowerCase();
        if (!query) return state.connections;
        return state.connections.filter((connection) => {
            return [connection.name, connection.base_url]
                .some((value) => String(value || '').toLowerCase().includes(query));
        });
    };

    const applyStaticTranslations = () => {
        if (!root) {
            return;
        }

        // The page shell is injected with JavaScript, so it needs a scoped
        // translation pass once the language bundle is ready.
        root.querySelectorAll('[data-i18n]').forEach((element) => {
            const key = element.getAttribute('data-i18n');
            if (!key) {
                return;
            }
            element.textContent = t(key, element.textContent || '');
        });

        // Keep translated attributes in sync as well, especially aria labels
        // and placeholders that are easy to miss in a direct-load path.
        root.querySelectorAll('[data-i18n-attr]').forEach((element) => {
            const spec = element.getAttribute('data-i18n-attr');
            if (!spec) {
                return;
            }

            spec.split(';').forEach((pair) => {
                const [attr, key] = pair.split(':').map((value) => value && value.trim());
                if (!attr || !key) {
                    return;
                }
                element.setAttribute(attr, t(key, element.getAttribute(attr) || ''));
            });
        });

        // If the delete confirmation is already open, refresh its text too so
        // a language change does not leave the modal in the old locale.
        if (state.pendingDeleteId) {
            const message = root.querySelector('#serviceConnectionDeleteMessage');
            if (message) {
                message.textContent = t('service_connection_delete_desc', 'This connection will be removed from routing.');
            }
        }
    };

    const renderShell = () => {
        root.innerHTML = `
            <div class="admin-toolbar service-connections-toolbar">
                <div class="admin-toolbar-left">
                    <div class="admin-table-search" role="search">
                        ${Icons.magnifyingGlass}
                        <input id="serviceConnectionSearchInput" class="admin-search-input" type="text" data-i18n-attr="placeholder:service_connections_search_placeholder;aria-label:service_connections_search_placeholder" placeholder="${escapeHtml(t('service_connections_search_placeholder', 'Search connections'))}" aria-label="${escapeHtml(t('service_connections_search_placeholder', 'Search connections'))}" autocomplete="off" spellcheck="false">
                        <button type="button" class="admin-search-clear" id="serviceConnectionSearchClear" data-i18n-attr="aria-label:search_clear_aria" aria-label="${escapeHtml(t('search_clear_aria', 'Clear search'))}" hidden>${icon('close')}</button>
                    </div>
                </div>
                <div class="admin-toolbar-right">
                    <button type="button" class="om-button border cancel" id="serviceConnectionsRefreshButton">${icon('refresh')}<span data-i18n="service_connections_refresh_all">${t('service_connections_refresh_all', 'Refresh Status')}</span></button>
                    <button type="button" class="om-button border cancel" id="serviceConnectionsImportLauncherButton">${icon('clipboard')}<span data-i18n="service_connections_import_launcher">${t('service_connections_import_launcher', 'Paste from launcher')}</span></button>
                    <button type="button" class="om-button border submit service-connections-primary-action" id="serviceConnectionsCreateButton">${icon('plus')}<span data-i18n="service_connections_new_btn">${t('service_connections_new_btn', 'New Connection')}</span></button>
                </div>
            </div>
            <div class="service-connections-table-wrapper stats-table-wrapper">
                <table class="service-connections-table stats-table">
                    <caption class="sr-only" data-i18n="page_service_connections">${t('page_service_connections', 'Service Connections')}</caption>
                    <thead>
                        <tr>
                            <th data-i18n="service_connections_col_name">${t('service_connections_col_name', 'Connection')}</th>
                            <th data-i18n="service_connections_col_base_url">${t('service_connections_col_base_url', 'Base URL')}</th>
                            <th data-i18n="service_connections_col_enabled">${t('service_connections_col_enabled', 'Enabled for')}</th>
                            <th data-i18n="service_connections_col_weight">${t('service_connections_col_weight', 'Weight')}</th>
                            <th data-i18n="service_connections_col_status">${t('service_connections_col_status', 'Status')}</th>
                            <th data-i18n="service_connections_col_actions">${t('service_connections_col_actions', 'Actions')}</th>
                        </tr>
                    </thead>
                    <tbody id="serviceConnectionsTableBody"></tbody>
                </table>
            </div>
            <div class="service-connection-modal shared-modal-overlay" id="serviceConnectionModal" aria-hidden="true" hidden>
                <section class="service-connection-dialog shared-modal shared-modal--fit shared-modal--wide" role="dialog" aria-modal="true" aria-labelledby="serviceConnectionModalTitle" tabindex="-1">
                    <header class="service-connection-dialog-header shared-modal-header shared-modal-header--main">
                        <div class="shared-modal-heading">
                            <h2 class="shared-modal-title" id="serviceConnectionModalTitle" data-i18n="service_connection_create_title">${t('service_connection_create_title', 'New Service Connection')}</h2>
                        </div>
                        <button type="button" class="shared-modal-close" id="serviceConnectionModalClose" data-i18n-attr="aria-label:modal_close_dialog_aria" aria-label="${escapeHtml(t('modal_close_dialog_aria', 'Close dialog'))}">${icon('close')}</button>
                    </header>
                    <form id="serviceConnectionForm" class="service-connection-form shared-modal-form" novalidate>
                        <div class="service-connection-form-fields shared-modal-body">
                        <input type="hidden" id="serviceConnectionIdInput">
                        <div class="service-connection-field">
                            <label for="serviceConnectionNameInput" data-i18n="service_connection_name_label">${t('service_connection_name_label', 'Name')}</label>
                            <input type="text" id="serviceConnectionNameInput" autocomplete="off" data-i18n-attr="placeholder:service_connection_name_placeholder" placeholder="${escapeHtml(t('service_connection_name_placeholder', 'Primary execution service'))}">
                        </div>
                        <div class="service-connection-field">
                            <label for="serviceConnectionBaseUrlInput" data-i18n="service_connection_base_url_label">${t('service_connection_base_url_label', 'Base URL')}</label>
                            <input type="url" id="serviceConnectionBaseUrlInput" required placeholder="http://localhost:8080">
                        </div>
                        <div class="service-connection-field">
                            <label for="serviceConnectionApiKeyInput" data-i18n="service_connection_api_key_label">${t('service_connection_api_key_label', 'API Key')}</label>
                            <input type="password" id="serviceConnectionApiKeyInput" autocomplete="new-password" data-i18n-attr="placeholder:service_connection_api_key_placeholder" placeholder="${escapeHtml(t('service_connection_api_key_placeholder', 'Leave blank if not required'))}">
                            <label class="service-connection-checkbox" id="serviceConnectionClearKeyRow" hidden>
                                <input type="checkbox" id="serviceConnectionClearKeyInput">
                                <span data-i18n="service_connection_clear_key">${t('service_connection_clear_key', 'Clear saved API key')}</span>
                            </label>
                        </div>
                        <div class="service-connection-toggle-grid">
                            <label class="service-connection-toggle">
                                <!-- Reuse the shared toggle-switch track wrapper so the slider keeps the
                                     correct positioning and animation inside the create/edit modal. -->
                                <span class="toggle-switch">
                                    <input type="checkbox" id="serviceConnectionCodeToggle" class="toggle-input">
                                    <span class="toggle-slider" aria-hidden="true"></span>
                                </span>
                                <span class="service-connection-toggle-label" data-i18n="service_connections_code_execution">${t('service_connections_code_execution', 'Code execution')}</span>
                            </label>
                            <label class="service-connection-toggle">
                                <span class="toggle-switch">
                                    <input type="checkbox" id="serviceConnectionLatexToggle" class="toggle-input">
                                    <span class="toggle-slider" aria-hidden="true"></span>
                                </span>
                                <span class="service-connection-toggle-label" data-i18n="service_connections_latex_pdf">${t('service_connections_latex_pdf', 'LaTeX rendering')}</span>
                            </label>
                            <label class="service-connection-toggle">
                                <span class="toggle-switch">
                                    <input type="checkbox" id="serviceConnectionSlideToggle" class="toggle-input">
                                    <span class="toggle-slider" aria-hidden="true"></span>
                                </span>
                                <span class="service-connection-toggle-label" data-i18n="service_connections_slide_renderer">${t('service_connections_slide_renderer', 'Slide renderer')}</span>
                            </label>
                        </div>
                        <div class="service-connection-field compact">
                            <label for="serviceConnectionWeightInput" data-i18n="service_connection_weight_label">${t('service_connection_weight_label', 'Weight')}</label>
                            <input type="number" id="serviceConnectionWeightInput" min="1" max="100" value="1">
                        </div>
                        </div>
                        <footer class="service-connection-dialog-actions shared-modal-footer">
                            <button type="button" class="om-button border cancel" id="serviceConnectionCancelButton"><span data-i18n="btn_cancel">${t('btn_cancel', 'Cancel')}</span></button>
                            <button type="submit" class="om-button border submit" id="serviceConnectionSaveButton"><span data-i18n="service_connection_save_btn">${t('service_connection_save_btn', 'Save Connection')}</span></button>
                        </footer>
                    </form>
                </section>
            </div>
        `;
        const deleteOverlay = window.DeleteWarningModal?.create({
            id: 'serviceConnectionDeleteOverlay',
            icon: 'trash',
            title: { i18n: 'service_connection_delete_title', text: t('service_connection_delete_title', 'Delete Service Connection?') },
            descriptions: [{ id: 'serviceConnectionDeleteMessage' }],
            actions: [
                { id: 'serviceConnectionDeleteCancel', role: 'cancel', variant: 'cancel', i18n: 'btn_cancel', text: t('btn_cancel', 'Cancel') },
                { id: 'serviceConnectionDeleteConfirm', variant: 'danger', i18n: 'btn_delete', text: t('btn_delete', 'Delete') },
            ],
        });
        if (deleteOverlay) root.appendChild(deleteOverlay);

        root.querySelector('#serviceConnectionSearchInput')?.addEventListener('input', (event) => {
            state.search = event.target.value || '';
            const clear = root.querySelector('#serviceConnectionSearchClear');
            if (clear) clear.hidden = !state.search.trim();
            renderTable();
        });
        root.querySelector('#serviceConnectionSearchClear')?.addEventListener('click', () => {
            state.search = '';
            const input = root.querySelector('#serviceConnectionSearchInput');
            if (input) input.value = '';
            const clear = root.querySelector('#serviceConnectionSearchClear');
            if (clear) clear.hidden = true;
            renderTable();
        });
        root.querySelector('#serviceConnectionsCreateButton')?.addEventListener('click', () => openForm());
        root.querySelector('#serviceConnectionsImportLauncherButton')?.addEventListener('click', importLauncherConnection);
        root.querySelector('#serviceConnectionsRefreshButton')?.addEventListener('click', refreshAllStatuses);
        root.querySelector('#serviceConnectionForm')?.addEventListener('submit', handleFormSubmit);
        root.querySelector('#serviceConnectionModalClose')?.addEventListener('click', closeForm);
        root.querySelector('#serviceConnectionCancelButton')?.addEventListener('click', closeForm);
        root.querySelector('#serviceConnectionModal')?.addEventListener('click', (event) => {
            if (event.target === event.currentTarget) closeForm();
        });
        root.querySelector('#serviceConnectionDeleteCancel')?.addEventListener('click', closeDelete);
        root.querySelector('#serviceConnectionDeleteConfirm')?.addEventListener('click', confirmDelete);
        root.querySelector('#serviceConnectionDeleteOverlay')?.addEventListener('click', (event) => {
            if (event.target === event.currentTarget) closeDelete();
        });

        if (!state.escapeRegistration && typeof window.registerEscapeHandler === 'function') {
            state.escapeRegistration = window.registerEscapeHandler({
                id: 'admin-service-connections-modal',
                priority: 180,
                isActive: () => Boolean(
                    (root.querySelector('#serviceConnectionModal') && !root.querySelector('#serviceConnectionModal').hidden)
                    || (root.querySelector('#serviceConnectionDeleteOverlay') && !root.querySelector('#serviceConnectionDeleteOverlay').hidden)
                ),
                close: () => {
                    const deleteOverlay = root.querySelector('#serviceConnectionDeleteOverlay');
                    if (deleteOverlay && !deleteOverlay.hidden) closeDelete();
                    else closeForm();
                },
            });
        }

        applyStaticTranslations();
    };

    const renderTable = () => {
        const body = root.querySelector('#serviceConnectionsTableBody');
        if (!body) return;

        if (state.loading) {
            body.innerHTML = `
                <tr class="stats-table-empty">
                    <td colspan="6" class="admin-loading-cell">${t('service_connections_loading', 'Loading service connections…')}</td>
                </tr>
            `;
            return;
        }

        const rows = filteredConnections();
        if (!rows.length) {
            body.innerHTML = `
                <tr class="stats-table-empty">
                    <td colspan="6">${state.connections.length ? t('service_connections_no_results', 'No connections match the search.') : t('service_connections_empty', 'No service connections yet.')}</td>
                </tr>
            `;
            return;
        }

        body.innerHTML = rows.map((connection) => {
            const status = connection.status || {};
            const statusValue = String(status.available || 'unknown').toLowerCase();
            const details = statusDetails(connection);
            // These stable, translated labels are visible in the responsive
            // card layout and preserve the table's meaning after its desktop
            // header is hidden on small screens.
            const labels = {
                name: escapeHtml(t('service_connections_col_name', 'Connection')),
                baseUrl: escapeHtml(t('service_connections_col_base_url', 'Base URL')),
                enabled: escapeHtml(t('service_connections_col_enabled', 'Enabled for')),
                weight: escapeHtml(t('service_connections_col_weight', 'Weight')),
                status: escapeHtml(t('service_connections_col_status', 'Status')),
                actions: escapeHtml(t('service_connections_col_actions', 'Actions')),
            };
            return `
                <tr data-connection-id="${escapeHtml(connection.id)}">
                    <td class="service-connection-cell service-connection-cell-name" data-label="${labels.name}">
                        <div class="service-connection-name">${escapeHtml(connection.name || connection.base_url)}</div>
                        ${connection.has_api_key ? `<div class="service-connection-subtle">${t('service_connection_key_saved', 'API key saved')}</div>` : ''}
                    </td>
                    <td class="service-connection-cell service-connection-cell-url" data-label="${labels.baseUrl}"><span class="service-connection-url">${escapeHtml(connection.base_url || '')}</span></td>
                    <td class="service-connection-cell service-connection-cell-purposes" data-label="${labels.enabled}"><div class="service-connection-purposes">${purposeBadges(connection)}</div></td>
                    <td class="service-connection-cell service-connection-cell-weight" data-label="${labels.weight}">${escapeHtml(connection.weight || 1)}</td>
                    <td class="service-connection-cell service-connection-cell-status" data-label="${labels.status}">
                        <span class="service-connection-status ${statusValue}">${statusLabel(statusValue)}</span>
                        ${details ? `<div class="service-connection-subtle service-connection-status-details">${escapeHtml(details)}</div>` : ''}
                    </td>
                    <td class="service-connection-cell service-connection-cell-actions" data-label="${labels.actions}">
                        <div class="service-connection-actions">
                            <button type="button" class="service-connection-icon-btn" data-action="refresh" title="${escapeHtml(t('service_connection_refresh_status', 'Refresh status'))}" aria-label="${escapeHtml(t('service_connection_refresh_status', 'Refresh status'))}">${icon('refresh')}</button>
                            <button type="button" class="service-connection-icon-btn" data-action="edit" title="${escapeHtml(t('provider_group_edit_title', 'Edit'))}" aria-label="${escapeHtml(t('provider_group_edit_title', 'Edit'))}">${icon('edit')}</button>
                            <button type="button" class="service-connection-icon-btn danger" data-action="delete" title="${escapeHtml(t('btn_delete', 'Delete'))}" aria-label="${escapeHtml(t('btn_delete', 'Delete'))}">${icon('trash')}</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        body.querySelectorAll('button[data-action]').forEach((button) => {
            button.addEventListener('click', () => {
                const row = button.closest('tr[data-connection-id]');
                const id = row?.dataset.connectionId;
                if (!id) return;
                const action = button.dataset.action;
                const connection = state.connections.find((item) => item.id === id);
                if (action === 'edit') openForm(connection);
                if (action === 'delete') openDelete(connection);
                if (action === 'refresh') refreshOneStatus(id, button);
            });
        });
    };

    const findConnectionAction = (connectionId, action) => {
        if (!connectionId || !action) return null;
        const row = Array.from(root.querySelectorAll('tr[data-connection-id]')).find(
            (candidate) => candidate.dataset.connectionId === String(connectionId)
        );
        return row?.querySelector(`button[data-action="${action}"]`) || null;
    };

    const restoreLastFocusedElement = (stateKey) => {
        const previousFocus = state[stateKey];
        state[stateKey] = null;
        const target = (previousFocus?.isConnected ? previousFocus : null)
            || root.querySelector('#serviceConnectionsCreateButton');
        target?.focus?.();
    };

    const loadConnections = async () => {
        state.loading = true;
        renderTable();
        try {
            state.connections = await api.list();
            renderTable();
        } catch (error) {
            window.notifyError?.(error.message || t('service_connections_load_failed', 'Unable to load service connections.'));
        } finally {
            state.loading = false;
            renderTable();
        }
    };

    const openForm = (connection = null) => {
        state.formLastFocusedElement = document.activeElement;
        state.editingId = connection?.id || null;
        root.querySelector('#serviceConnectionModalTitle').textContent = connection
            ? t('service_connection_edit_title', 'Edit Service Connection')
            : t('service_connection_create_title', 'New Service Connection');
        root.querySelector('#serviceConnectionIdInput').value = connection?.id || '';
        root.querySelector('#serviceConnectionNameInput').value = connection?.name || '';
        root.querySelector('#serviceConnectionBaseUrlInput').value = connection?.base_url || '';
        root.querySelector('#serviceConnectionApiKeyInput').value = '';
        root.querySelector('#serviceConnectionCodeToggle').checked = Boolean(connection?.enabled_for_code_execution);
        root.querySelector('#serviceConnectionLatexToggle').checked = Boolean(connection?.enabled_for_latex_pdf);
        root.querySelector('#serviceConnectionSlideToggle').checked = Boolean(connection?.enabled_for_slide_renderer);
        root.querySelector('#serviceConnectionWeightInput').value = connection?.weight || 1;
        const clearRow = root.querySelector('#serviceConnectionClearKeyRow');
        const clearInput = root.querySelector('#serviceConnectionClearKeyInput');
        if (clearRow) clearRow.hidden = !(connection?.has_api_key);
        if (clearInput) clearInput.checked = false;
        const modal = root.querySelector('#serviceConnectionModal');
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        window.requestAnimationFrame(() => root.querySelector('#serviceConnectionNameInput')?.focus());
    };

    /**
     * Load the secret connection handoff copied by the desktop launcher.
     * Clipboard access only occurs after an explicit button click, and the
     * secret remains in the password field until the administrator saves it.
     */
    const importLauncherConnection = async () => {
        try {
            const raw = await navigator.clipboard.readText();
            const payload = JSON.parse(raw);
            if (!payload || typeof payload !== 'object'
                || typeof payload.base_url !== 'string'
                || !payload.base_url.trim()
                || typeof payload.api_key !== 'string'
                || !payload.api_key) {
                throw new Error('invalid launcher payload');
            }
            openForm();
            root.querySelector('#serviceConnectionNameInput').value = String(payload.name || '');
            root.querySelector('#serviceConnectionBaseUrlInput').value = payload.base_url.trim();
            root.querySelector('#serviceConnectionApiKeyInput').value = payload.api_key;
            root.querySelector('#serviceConnectionCodeToggle').checked = payload.enabled_for_code_execution !== false;
            root.querySelector('#serviceConnectionLatexToggle').checked = payload.enabled_for_latex_pdf !== false;
            root.querySelector('#serviceConnectionSlideToggle').checked = payload.enabled_for_slide_renderer !== false;
            root.querySelector('#serviceConnectionWeightInput').value = Math.max(
                1,
                Math.min(100, Number(payload.weight || 1)),
            );
            root.querySelector('#serviceConnectionNameInput')?.focus();
            window.notifySuccess?.(t(
                'service_connections_launcher_loaded',
                'Connection details loaded. Review them, then save.',
            ));
        } catch (_error) {
            window.notifyError?.(t(
                'service_connections_launcher_invalid',
                'The clipboard does not contain valid launcher connection details.',
            ));
        }
    };

    const closeForm = ({ restoreFocus = true } = {}) => {
        const modal = root.querySelector('#serviceConnectionModal');
        modal.setAttribute('aria-hidden', 'true');
        modal.hidden = true;
        state.editingId = null;
        if (restoreFocus) {
            restoreLastFocusedElement('formLastFocusedElement');
        }
    };

    const handleFormSubmit = async (event) => {
        event.preventDefault();
        const name = root.querySelector('#serviceConnectionNameInput')?.value?.trim() || '';
        const baseUrl = root.querySelector('#serviceConnectionBaseUrlInput')?.value?.trim() || '';
        const apiKey = root.querySelector('#serviceConnectionApiKeyInput')?.value || '';
        const enabledForCode = Boolean(root.querySelector('#serviceConnectionCodeToggle')?.checked);
        const enabledForLatex = Boolean(root.querySelector('#serviceConnectionLatexToggle')?.checked);
        const enabledForSlides = Boolean(root.querySelector('#serviceConnectionSlideToggle')?.checked);
        const weight = Number(root.querySelector('#serviceConnectionWeightInput')?.value || 1);
        const clearApiKey = Boolean(root.querySelector('#serviceConnectionClearKeyInput')?.checked);

        if (!baseUrl) {
            window.notifyError?.(t('service_connection_base_url_required', 'Base URL is required.'));
            root.querySelector('#serviceConnectionBaseUrlInput')?.focus();
            return;
        }
        if (!enabledForCode && !enabledForLatex && !enabledForSlides) {
            window.notifyError?.(t('service_connection_enable_one', 'Enable at least one service.'));
            return;
        }

        const payload = {
            name,
            base_url: baseUrl,
            api_key: apiKey,
            clear_api_key: clearApiKey,
            enabled_for_code_execution: enabledForCode,
            enabled_for_latex_pdf: enabledForLatex,
            enabled_for_slide_renderer: enabledForSlides,
            weight: Math.max(1, Math.min(100, Number.isFinite(weight) ? weight : 1)),
        };

        const saveButton = root.querySelector('#serviceConnectionSaveButton');
        saveButton.disabled = true;
        try {
            let savedConnection;
            if (state.editingId) {
                savedConnection = await api.update(state.editingId, payload);
                window.notifySuccess?.(t('service_connection_update_success', 'Service connection updated.'));
                state.connections = state.connections.map((item) => (
                    item.id === state.editingId ? savedConnection : item
                ));
            } else {
                savedConnection = await api.create(payload);
                window.notifySuccess?.(t('service_connection_create_success', 'Service connection created.'));
                state.connections = [...state.connections, savedConnection];
            }
            closeForm({ restoreFocus: false });
            try {
                renderTable();
            } finally {
                restoreLastFocusedElement('formLastFocusedElement');
            }
            // Refresh every visible status so the badges and stored health checks
            // stay in sync without a manual page reload. The stable toolbar
            // already owns focus while these potentially slow requests run.
            await refreshAllStatuses();
        } catch (error) {
            window.notifyError?.(error.message || t('service_connection_save_failed', 'Unable to save service connection.'));
        } finally {
            saveButton.disabled = false;
        }
    };

    const refreshOneStatus = async (id, button = null) => {
        const shouldRestoreFocus = Boolean(button && document.activeElement === button);
        if (button) button.disabled = true;
        try {
            const updated = await api.refreshStatus(id);
            state.connections = state.connections.map((item) => item.id === id ? updated : item);
            renderTable();
        } catch (error) {
            window.notifyError?.(error.message || t('service_connection_status_failed', 'Unable to refresh service status.'));
        } finally {
            if (button) {
                const currentButton = button.isConnected
                    ? button
                    : findConnectionAction(id, button.dataset.action);
                if (currentButton) currentButton.disabled = false;
                if (shouldRestoreFocus && document.activeElement !== currentButton) {
                    (currentButton || root.querySelector('#serviceConnectionsCreateButton'))?.focus?.();
                }
            }
        }
    };

    const refreshAllStatuses = async () => {
        const button = root.querySelector('#serviceConnectionsRefreshButton');
        const shouldRestoreFocus = Boolean(button && document.activeElement === button);
        if (button) button.disabled = true;
        try {
            for (const connection of [...state.connections]) {
                await refreshOneStatus(connection.id);
            }
        } finally {
            if (button) {
                button.disabled = false;
                if (shouldRestoreFocus && document.activeElement !== button) {
                    button.focus();
                }
            }
        }
    };

    const openDelete = (connection) => {
        if (!connection) return;
        state.deleteLastFocusedElement = document.activeElement;
        state.pendingDeleteId = connection.id;
        const message = root.querySelector('#serviceConnectionDeleteMessage');
        if (message) {
            message.textContent = t('service_connection_delete_desc', 'This connection will be removed from routing.');
        }
        const overlay = root.querySelector('#serviceConnectionDeleteOverlay');
        if (overlay) {
            overlay.hidden = false;
            overlay.setAttribute('aria-hidden', 'false');
            window.requestAnimationFrame(() => root.querySelector('#serviceConnectionDeleteCancel')?.focus());
        }
    };

    const closeDelete = ({ restoreFocus = true } = {}) => {
        state.pendingDeleteId = null;
        const overlay = root.querySelector('#serviceConnectionDeleteOverlay');
        if (overlay) {
            overlay.setAttribute('aria-hidden', 'true');
            overlay.hidden = true;
        }
        if (restoreFocus) {
            restoreLastFocusedElement('deleteLastFocusedElement');
        }
    };

    const confirmDelete = async () => {
        if (!state.pendingDeleteId) return;
        const id = state.pendingDeleteId;
        try {
            await api.remove(id);
            window.notifySuccess?.(t('service_connection_delete_success', 'Service connection deleted.'));
            closeDelete({ restoreFocus: false });
            try {
                await loadConnections();
            } finally {
                restoreLastFocusedElement('deleteLastFocusedElement');
            }
        } catch (error) {
            window.notifyError?.(error.message || t('service_connection_delete_failed', 'Unable to delete service connection.'));
        }
    };

    const handleBack = () => window.activateAdminPage?.('tools');
    const handleI18nUpdated = () => {
        if (!state.initialized) {
            return;
        }

        applyStaticTranslations();
        renderTable();
    };

    window.initServiceConnectionsPage = () => {
        if (!state.initialized) {
            renderShell();
            backButton?.addEventListener('click', handleBack);
            state.initialized = true;

            if (!i18nListenerBound) {
                document.addEventListener('i18n:updated', handleI18nUpdated);
                i18nListenerBound = true;
            }
        }
        applyStaticTranslations();
        loadConnections();
    };

    window.teardownServiceConnectionsPage = () => {};
})();
