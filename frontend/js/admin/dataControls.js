(function () {
    const dom = {
        importBtn: document.getElementById('dataControlsImportChatsBtn'),
        importInput: document.getElementById('dataControlsImportChatsInput'),
        importArchivedBtn: document.getElementById('dataControlsImportArchivedChatsBtn'),
        importArchivedInput: document.getElementById('dataControlsImportArchivedChatsInput'),
        overlay: document.getElementById('dataControlsUserSelectOverlay'),
        userSearch: document.getElementById('dataControlsUserSearch'),
        userList: document.getElementById('dataControlsUserList'),
        importSummary: document.getElementById('dataControlsImportSummary'),
        cancelBtn: document.getElementById('dataControlsUserSelectCancel'),
        confirmBtn: document.getElementById('dataControlsUserSelectConfirm'),
    };

    let users = [];
    let selectedUserId = null;
    let pendingFileData = null;
    let forceArchived = false;
    let activeBtn = null;
    let initialized = false;

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const formatT = (key, fallback, vars = {}) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        let text = t(key, fallback);
        Object.entries(vars).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), value);
        });
        return text;
    };

    function init() {
        if (initialized) return;
        initialized = true;

        dom.importBtn?.addEventListener('click', () => { forceArchived = false; activeBtn = dom.importBtn; dom.importInput?.click(); });
        dom.importInput?.addEventListener('change', handleFileSelected);
        dom.importArchivedBtn?.addEventListener('click', () => { forceArchived = true; activeBtn = dom.importArchivedBtn; dom.importArchivedInput?.click(); });
        dom.importArchivedInput?.addEventListener('change', handleFileSelected);
        dom.cancelBtn?.addEventListener('click', closeModal);
        dom.confirmBtn?.addEventListener('click', handleConfirmImport);
        dom.userSearch?.addEventListener('input', handleUserSearch);

        dom.overlay?.addEventListener('click', (e) => {
            if (e.target === dom.overlay) closeModal();
        });
    }

    async function handleFileSelected(event) {
        const file = event.target?.files?.[0];
        if (!file) return;

        let parsed;
        try {
            const text = await file.text();
            parsed = JSON.parse(text);
        } catch {
            notifyError(t('dc_invalid_json', 'The selected file does not contain valid JSON.'));
            resetInput();
            return;
        }

        if (!Array.isArray(parsed) || parsed.length === 0) {
            notifyError(t('dc_invalid_array', 'The JSON file must contain an array of chat objects.'));
            resetInput();
            return;
        }

        pendingFileData = parsed;

        const triggerBtn = activeBtn || dom.importBtn;
        setButtonLoadingState(triggerBtn, true, t('dc_loading_users', 'Loading users...'));
        try {
            await loadUsers();
        } catch {
            notifyError(t('dc_load_users_error', 'Failed to load user list.'));
            resetInput();
            return;
        } finally {
            setButtonLoadingState(triggerBtn, false);
        }

        showSummary(
            'info',
            forceArchived
                ? formatT('dc_archived_file_summary', '{count} archived chat(s) found in file. Select a user to import them to.', { count: parsed.length })
                : formatT('dc_file_summary', '{count} chat(s) found in file. Select a user to import them to.', { count: parsed.length })
        );
        openModal();
        resetInput();
    }

    async function loadUsers() {
        const data = await fetchAdminJson('/api/v1/admin/users', {}, 'Failed to load users.');
        if (!data) throw new Error('no data');
        users = data;
        renderUserList(users);
    }

    function renderUserList(list) {
        if (!dom.userList) return;
        if (!list.length) {
            dom.userList.innerHTML = `<div class="dc-user-list-empty">${escapeHtml(t('dc_no_users_found', 'No users found.'))}</div>`;
            return;
        }

        dom.userList.innerHTML = list.map((u) => {
            const name = [u.first_name, u.last_name].filter(Boolean).join(' ') || u.email;
            const initials = getInitials(u.first_name, u.last_name, u.email);
            const selected = u.id === selectedUserId ? ' selected' : '';
            return `<div class="dc-user-item${selected}" data-user-id="${u.id}">
                <div class="dc-user-item-avatar">${initials}</div>
                <div class="dc-user-item-info">
                    <span class="dc-user-item-name">${escapeHtml(name)}</span>
                    <span class="dc-user-item-email">${escapeHtml(u.email)}</span>
                </div>
            </div>`;
        }).join('');

        dom.userList.querySelectorAll('.dc-user-item').forEach((el) => {
            el.addEventListener('click', () => selectUser(el.dataset.userId));
        });
    }

    function getInitials(first, last, email) {
        if (first && last) return (first[0] + last[0]).toUpperCase();
        if (first) return first.slice(0, 2).toUpperCase();
        if (email) return email.slice(0, 2).toUpperCase();
        return '??';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function selectUser(userId) {
        selectedUserId = userId;
        dom.userList?.querySelectorAll('.dc-user-item').forEach((el) => {
            el.classList.toggle('selected', el.dataset.userId === userId);
        });
        if (dom.confirmBtn) dom.confirmBtn.disabled = !userId;
    }

    function handleUserSearch() {
        const query = (dom.userSearch?.value || '').toLowerCase().trim();
        if (!query) {
            renderUserList(users);
            return;
        }
        const filtered = users.filter((u) => {
            const fullName = [u.first_name, u.last_name].filter(Boolean).join(' ').toLowerCase();
            return fullName.includes(query) || (u.email || '').toLowerCase().includes(query);
        });
        renderUserList(filtered);
    }

    async function handleConfirmImport() {
        if (!selectedUserId || !pendingFileData) return;

        setButtonLoadingState(dom.confirmBtn, true, t('dc_importing', 'Importing...'));
        try {
            const response = await window.authedFetch('/api/v1/admin/import/openwebui/chats', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: selectedUserId,
                    chats: pendingFileData,
                    force_archived: forceArchived,
                }),
            });

            if (!response.ok) {
                let detail = t('dc_import_failed', 'Import failed.');
                try {
                    const err = await response.json();
                    detail = err.detail || detail;
                } catch {}
                notifyError(detail);
                showSummary('error', detail);
                return;
            }

            const result = await response.json();
            const msg = formatT('dc_import_success', 'Successfully imported {chats} chat(s) with {messages} message(s).', {
                chats: result.imported_chats,
                messages: result.imported_messages,
            });
            const details = [];
            if (result.imported_branches > 0) {
                details.push(formatT('dc_import_branch_chats_suffix', '{count} branch chat(s) created.', {
                    count: result.imported_branches,
                }));
            }
            if (result.skipped_branches > 0) {
                details.push(formatT('dc_import_branches_skipped', '{count} branch(es) skipped.', {
                    count: result.skipped_branches,
                }));
            }
            if (result.skipped_messages > 0) {
                details.push(formatT('dc_import_messages_skipped', '{count} message(s) skipped.', {
                    count: result.skipped_messages,
                }));
            }
            let successMessage = msg;
            if (result.skipped_chats > 0) {
                successMessage = formatT('dc_import_success_with_skipped', '{message} ({skipped} skipped)', {
                    message: msg,
                    skipped: result.skipped_chats,
                });
            }
            if (details.length) {
                successMessage += ` ${details.join(' ')}`;
            }
            notifySuccess(successMessage);
            closeModal();
        } catch (error) {
            console.error('import failed', error);
            notifyError(error?.message || t('dc_import_failed', 'Import failed.'));
            showSummary('error', error?.message || t('dc_import_failed', 'Import failed.'));
        } finally {
            setButtonLoadingState(dom.confirmBtn, false);
        }
    }

    function showSummary(kind, message) {
        if (!dom.importSummary) return;
        dom.importSummary.hidden = false;
        dom.importSummary.textContent = message;
        dom.importSummary.className = `dc-import-summary ${kind}`;
    }

    function openModal() {
        selectedUserId = null;
        if (dom.confirmBtn) dom.confirmBtn.disabled = true;
        if (dom.userSearch) dom.userSearch.value = '';
        if (dom.overlay) dom.overlay.hidden = false;
    }

    function closeModal() {
        if (dom.overlay) dom.overlay.hidden = true;
        pendingFileData = null;
        selectedUserId = null;
        if (dom.importSummary) {
            dom.importSummary.hidden = true;
            dom.importSummary.textContent = '';
        }
        if (dom.userSearch) dom.userSearch.value = '';
        if (dom.userList) dom.userList.innerHTML = '';
    }

    function resetInput() {
        if (dom.importInput) dom.importInput.value = '';
        if (dom.importArchivedInput) dom.importArchivedInput.value = '';
    }

    // =========================================================================
    // Bulk Import – All Users (CSV + JSON)
    // =========================================================================
    const bulkRows = document.querySelectorAll('#dataControlsBulkImportOverlay .dc-bulk-file-row');
    const bulk = {
        btn: document.getElementById('dataControlsBulkImportBtn'),
        overlay: document.getElementById('dataControlsBulkImportOverlay'),
        csvBtn: document.getElementById('dcBulkCsvBtn'),
        csvInput: document.getElementById('dcBulkCsvInput'),
        csvStatus: document.getElementById('dcBulkCsvStatus'),
        csvNum: bulkRows[0]?.querySelector('.dc-bulk-file-num') || null,
        jsonBtn: document.getElementById('dcBulkJsonBtn'),
        jsonInput: document.getElementById('dcBulkJsonInput'),
        jsonStatus: document.getElementById('dcBulkJsonStatus'),
        jsonNum: bulkRows[1]?.querySelector('.dc-bulk-file-num') || null,
        summary: document.getElementById('dcBulkSummary'),
        cancelBtn: document.getElementById('dcBulkCancelBtn'),
        confirmBtn: document.getElementById('dcBulkConfirmBtn'),
    };

    let bulkCsvText = null;
    let bulkJsonData = null;

    function initBulk() {
        bulk.btn?.addEventListener('click', openBulkModal);
        bulk.csvBtn?.addEventListener('click', () => bulk.csvInput?.click());
        bulk.csvInput?.addEventListener('change', handleBulkCsv);
        bulk.jsonBtn?.addEventListener('click', () => bulk.jsonInput?.click());
        bulk.jsonInput?.addEventListener('change', handleBulkJson);
        bulk.cancelBtn?.addEventListener('click', closeBulkModal);
        bulk.confirmBtn?.addEventListener('click', handleBulkConfirm);
        bulk.overlay?.addEventListener('click', (e) => {
            if (e.target === bulk.overlay) closeBulkModal();
        });
    }

    function openBulkModal() {
        bulkCsvText = null;
        bulkJsonData = null;
        if (bulk.csvStatus) bulk.csvStatus.textContent = '';
        if (bulk.jsonStatus) bulk.jsonStatus.textContent = '';
        if (bulk.csvNum) bulk.csvNum.classList.remove('done');
        if (bulk.jsonNum) bulk.jsonNum.classList.remove('done');
        if (bulk.csvInput) bulk.csvInput.value = '';
        if (bulk.jsonInput) bulk.jsonInput.value = '';
        if (bulk.confirmBtn) bulk.confirmBtn.disabled = true;
        if (bulk.summary) { bulk.summary.hidden = true; bulk.summary.textContent = ''; }
        if (bulk.overlay) bulk.overlay.hidden = false;
    }

    function closeBulkModal() {
        if (bulk.overlay) bulk.overlay.hidden = true;
        bulkCsvText = null;
        bulkJsonData = null;
    }

    function updateBulkConfirmState() {
        if (bulk.confirmBtn) bulk.confirmBtn.disabled = !(bulkCsvText && bulkJsonData);
    }

    async function handleBulkCsv(event) {
        const file = event.target?.files?.[0];
        if (!file) return;
        try {
            bulkCsvText = await file.text();
            if (bulk.csvStatus) bulk.csvStatus.textContent = file.name;
            if (bulk.csvNum) bulk.csvNum.classList.add('done');
        } catch {
            notifyError(t('dc_bulk_read_csv_error', 'Failed to read the CSV file.'));
            bulkCsvText = null;
        }
        if (bulk.csvInput) bulk.csvInput.value = '';
        updateBulkConfirmState();
    }

    async function handleBulkJson(event) {
        const file = event.target?.files?.[0];
        if (!file) return;
        try {
            const text = await file.text();
            bulkJsonData = JSON.parse(text);
            if (!Array.isArray(bulkJsonData) || bulkJsonData.length === 0) {
                notifyError(t('dc_bulk_invalid_array', 'JSON file must contain a non-empty array of chat objects.'));
                bulkJsonData = null;
                return;
            }
            if (bulk.jsonStatus) bulk.jsonStatus.textContent = formatT('dc_bulk_status_chats_file', '{count} chats - {file}', {
                count: bulkJsonData.length,
                file: file.name,
            });
            if (bulk.jsonNum) bulk.jsonNum.classList.add('done');
        } catch {
            notifyError(t('dc_bulk_invalid_json', 'The selected file does not contain valid JSON.'));
            bulkJsonData = null;
        }
        if (bulk.jsonInput) bulk.jsonInput.value = '';
        updateBulkConfirmState();
    }

    function showBulkSummary(kind, message) {
        if (!bulk.summary) return;
        bulk.summary.hidden = false;
        bulk.summary.textContent = message;
        bulk.summary.className = `dc-import-summary ${kind}`;
    }

    async function handleBulkConfirm() {
        if (!bulkCsvText || !bulkJsonData) return;

        setButtonLoadingState(bulk.confirmBtn, true, t('dc_bulk_importing', 'Importing...'));
        try {
            const response = await window.authedFetch('/api/v1/admin/import/openwebui/chats/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    users_csv: bulkCsvText,
                    chats: bulkJsonData,
                }),
            });

            if (!response.ok) {
                let detail = t('dc_bulk_import_failed', 'Bulk import failed.');
                try {
                    const err = await response.json();
                    detail = err.detail || detail;
                } catch {}
                notifyError(detail);
                showBulkSummary('error', detail);
                return;
            }

            const result = await response.json();
            let msg = formatT('dc_bulk_import_success', 'Imported {chats} chat(s) with {messages} message(s) for {users} user(s).', {
                chats: result.imported_chats,
                messages: result.imported_messages,
                users: result.matched_users,
            });
            if (result.skipped_users > 0) {
                msg += ` ${formatT('dc_bulk_import_users_missing', '{count} user(s) not found locally.', { count: result.skipped_users })}`;
            }
            if (result.skipped_chats > 0) {
                msg += ` ${formatT('dc_bulk_import_chats_skipped', '{count} chat(s) skipped.', { count: result.skipped_chats })}`;
            }
            if (result.imported_branches > 0) {
                msg += ` ${formatT('dc_import_branch_chats_suffix', '{count} branch chat(s) created.', { count: result.imported_branches })}`;
            }
            if (result.skipped_branches > 0) {
                msg += ` ${formatT('dc_import_branches_skipped', '{count} branch(es) skipped.', { count: result.skipped_branches })}`;
            }
            if (result.skipped_messages > 0) {
                msg += ` ${formatT('dc_import_messages_skipped', '{count} message(s) skipped.', { count: result.skipped_messages })}`;
            }
            notifySuccess(msg);
            showBulkSummary('success', msg);
        } catch (error) {
            console.error('bulk import failed', error);
            notifyError(error?.message || t('dc_bulk_import_failed', 'Bulk import failed.'));
            showBulkSummary('error', error?.message || t('dc_bulk_import_failed', 'Bulk import failed.'));
        } finally {
            setButtonLoadingState(bulk.confirmBtn, false);
        }
    }

    initBulk();

    window.initDataControlsPage = init;
    init();
})();
