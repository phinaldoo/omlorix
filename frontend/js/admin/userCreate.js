(function () {
    const singlePage = document.getElementById('page-user-create-single');
    const bulkPage = document.getElementById('page-user-create-bulk');

    if (!singlePage && !bulkPage) {
        window.initAdminUserCreatePage = () => {};
        window.teardownAdminUserCreatePage = () => {};
        return;
    }

    const dom = {
        single: {
            form: document.getElementById('userCreateSingleForm'),
            firstName: document.getElementById('userCreateFirstName'),
            lastName: document.getElementById('userCreateLastName'),
            email: document.getElementById('userCreateEmail'),
            password: document.getElementById('userCreatePassword'),
            group: document.getElementById('userCreateGroup'),
            forceReset: document.getElementById('userCreateForcePasswordChange'),
            submit: document.getElementById('userCreateSingleSubmit'),
            back: document.getElementById('userCreateSingleBack'),
        },
        bulk: {
            csvHint: document.getElementById('userBulkCsvHint'),
            formatXlsx: document.getElementById('userBulkFormatXlsx'),
            formatCsv: document.getElementById('userBulkFormatCsv'),
            downloadBtn: document.getElementById('userCreateBulkDownloadButton'),
            fileInput: document.getElementById('userCreateBulkFileInput'),
            uploadBtn: document.getElementById('userCreateBulkUploadButton'),
            uploadZone: document.getElementById('userBulkUploadZone'),
            results: document.getElementById('userCreateBulkResults'),
            back: document.getElementById('userCreateBulkBack'),
            optionsOverlay: document.getElementById('userBulkOptionsOverlay'),
            optionsClose: document.getElementById('userBulkOptionsClose'),
            optionsCancel: document.getElementById('userBulkOptionsCancel'),
            optionsConfirm: document.getElementById('userBulkOptionsConfirm'),
            optionsFileName: document.getElementById('userBulkOptionsFileName'),
            optionsStatus: document.getElementById('userBulkOptionsStatus'),
            defaultPassword: document.getElementById('userBulkDefaultPassword'),
            forcePasswordChange: document.getElementById('userBulkForcePasswordChange'),
        },
        triggers: {
            openSingle: document.getElementById('userCreateSingleButton'),
            openBulk: document.getElementById('userCreateBulkButton'),
        },
    };

    const state = {
        initialized: false,
        groupsLoaded: false,
        groupsPromise: null,
        boundPageListener: null,
        selectedFormat: 'xlsx',
        escapeRegistration: null,
        lastBulkOptionsFocus: null,
        singleSelects: {
            group: null,
        },
    };

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

    function init() {
        if (state.initialized) {
            syncActivePage();
            return;
        }
        state.initialized = true;
        upgradeSingleFormSelects();
        bindSingleForm();
        bindBulkInteractions();
        syncBulkFormatUi();
        bindNavigationButtons();
        registerEscapeShortcut();
        state.boundPageListener = handlePageActivated.bind(null);
        window.addEventListener('admin:page-activated', state.boundPageListener);
        syncActivePage();
    }

    function teardown() {
        if (state.boundPageListener) {
            window.removeEventListener('admin:page-activated', state.boundPageListener);
            state.boundPageListener = null;
        }
        if (state.escapeRegistration) {
            if (typeof window.unregisterEscapeHandler === 'function') {
                window.unregisterEscapeHandler(state.escapeRegistration.id);
            }
            state.escapeRegistration = null;
        }
        hideBulkResults();
        state.initialized = false;
    }

    function handlePageActivated(event) {
        const { page } = event.detail || {};
        if (!page || (page !== 'user-create-single' && page !== 'user-create-bulk')) {
            return;
        }
        syncActivePage();
    }

    function syncActivePage() {
        const isSingleActive = singlePage && !singlePage.hidden;
        if (isSingleActive) {
            ensureGroups();
        }
        const isBulkActive = bulkPage && !bulkPage.hidden;
        if (isBulkActive) {
            syncBulkFormatUi();
        }
    }

    function bindSingleForm() {
        dom.single.form?.addEventListener('submit', handleSingleSubmit);
        dom.single.form?.addEventListener('reset', () => {
            window.requestAnimationFrame(() => {
                syncSingleFormSelects();
            });
        });

        // Attach error clear listeners to required fields
        const requiredFields = [
            dom.single.firstName,
            dom.single.lastName,
            dom.single.email,
            dom.single.password,
            dom.single.group,
        ].filter(Boolean);
        requiredFields.forEach((control) => {
            if (!control) return;
            const row = resolveFieldRow(control);
            if (!row) return;
            const clearOnInput = () => {
                if (row.classList.contains('has-error')) {
                    window.FieldValidation?.clearFieldError(row);
                }
            };
            control.addEventListener('input', clearOnInput);
            control.addEventListener('change', clearOnInput);
        });
    }

    async function handleSingleSubmit(event) {
        event.preventDefault();

        const payload = collectSinglePayload();
        if (!payload) {
            return;
        }

        setButtonLoadingState(dom.single.submit, true, t('user_create_busy_creating', 'Creating…'));
        try {
            const result = await createAdminUser(payload);
            if (!result) {
                notifyError(t('user_create_error_unexpected_response', 'Unexpected server response.'));
                return;
            }
            dom.single.form.reset();
            syncSingleFormSelects();
            notifySuccess?.(t('user_create_success', 'User created successfully.'));
            window.activateAdminPage?.('users');
        } catch (error) {
            console.error('create user failed', error);
            notifyError?.(error?.message || t('user_create_error_failed', 'Failed to create user.'));
        } finally {
            setButtonLoadingState(dom.single.submit, false);
        }
    }

    function collectSinglePayload() {
        const firstName = dom.single.firstName?.value.trim();
        const lastName = dom.single.lastName?.value.trim();
        const email = dom.single.email?.value.trim();
        const password = dom.single.password?.value;

        const groupId = dom.single.group?.value.trim();

        // Validate required fields with visual feedback
        const requiredFields = [
            { control: dom.single.firstName, label: t('user_create_first_name_label', 'First name'), value: firstName },
            { control: dom.single.lastName, label: t('user_create_last_name_label', 'Last name'), value: lastName },
            { control: dom.single.email, label: t('user_create_email_label', 'Email'), value: email },
            { control: dom.single.password, label: t('user_create_password_label', 'Password'), value: password },
            { control: dom.single.group, label: t('user_create_group_label', 'Group'), value: groupId },
        ];

        const invalidRows = [];
        requiredFields.forEach(({ control, label, value }) => {
            if (!control) return;
            const row = resolveFieldRow(control);
            if (row) {
                window.FieldValidation?.clearFieldError(row);
                if (!value) {
                    window.FieldValidation?.setFieldError(
                        row,
                        formatT('validation_field_required', '{field} is required.', { field: label })
                    );
                    invalidRows.push(row);
                }
            }
        });

        if (invalidRows.length) {
            window.FieldValidation?.scrollToFirstInvalidField(invalidRows);
            notifyError?.(formatT(
                'user_create_error_required_fields',
                'Please fill in {count} required field(s).',
                { count: invalidRows.length }
            ));
            return null;
        }

        const forceReset = dom.single.forceReset?.checked ?? false;

        return {
            first_name: firstName,
            last_name: lastName,
            email,
            password,
            group_id: groupId || undefined,
            has_to_change_password: forceReset,
        };
    }

    function resolveFieldRow(control) {
        return control?.closest('.user-create-field')
            || control?.closest('.settings-row')
            || control?.closest('.form-group')
            || control?.parentElement
            || null;
    }

    function ensureSelectPlaceholderOption(select, label) {
        if (!select) {
            return;
        }
        const firstOption = select.options[0];
        if (firstOption?.value === '') {
            firstOption.textContent = label;
            return;
        }
        const placeholderOption = document.createElement('option');
        placeholderOption.value = '';
        placeholderOption.textContent = label;
        select.insertBefore(placeholderOption, firstOption || null);
    }

    function upgradeSingleSelect(select, { key, placeholder }) {
        const meta = window.upgradeAdminSingleSelect?.(select, { key, placeholder });
        if (!meta) {
            return null;
        }

        const label = select.closest('.user-create-field')?.querySelector('.user-create-label')
            || document.querySelector(`label[for="${select.id}"]`);
        if (label && meta.triggerId) {
            label.setAttribute('for', meta.triggerId);
        }

        return meta;
    }

    function upgradeSingleFormSelects() {
        ensureSelectPlaceholderOption(
            dom.single.group,
            t('group_form_select_group', 'Select a group')
        );
        state.singleSelects.group = upgradeSingleSelect(dom.single.group, {
            key: 'user-create-group',
            placeholder: t('group_form_select_group', 'Select a group'),
        });
    }

    function syncSingleFormSelects() {
        dom.single.group?._singleSelect?.syncFromSelect?.();
    }

    function bindBulkInteractions() {
        dom.bulk.downloadBtn?.addEventListener('click', handleTemplateDownload);
        dom.bulk.fileInput?.addEventListener('change', handleFileSelection);
        dom.bulk.uploadBtn?.addEventListener('click', handleBulkUpload);
        dom.bulk.optionsClose?.addEventListener('click', closeBulkOptionsModal);
        dom.bulk.optionsCancel?.addEventListener('click', closeBulkOptionsModal);
        dom.bulk.optionsConfirm?.addEventListener('click', submitBulkUpload);
        dom.bulk.optionsOverlay?.addEventListener('click', (event) => {
            if (event.target === dom.bulk.optionsOverlay) {
                closeBulkOptionsModal();
            }
        });
        dom.bulk.defaultPassword?.addEventListener('input', () => {
            setBulkOptionsStatus();
        });
        
        // Format tab switching
        dom.bulk.formatXlsx?.addEventListener('click', () => switchFormat('xlsx'));
        dom.bulk.formatCsv?.addEventListener('click', () => switchFormat('csv'));
        
        // Upload zone click to trigger file input
        dom.bulk.uploadZone?.addEventListener('click', () => {
            dom.bulk.fileInput?.click();
        });

        // Drag and drop support
        if (dom.bulk.uploadZone) {
            dom.bulk.uploadZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dom.bulk.uploadZone.classList.add('dragover');
            });

            dom.bulk.uploadZone.addEventListener('dragleave', (e) => {
                e.preventDefault();
                dom.bulk.uploadZone.classList.remove('dragover');
            });

            dom.bulk.uploadZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dom.bulk.uploadZone.classList.remove('dragover');
                const files = e.dataTransfer?.files;
                if (files?.length && dom.bulk.fileInput) {
                    dom.bulk.fileInput.files = files;
                    handleFileSelection();
                }
            });
        }
    }

    function bindNavigationButtons() {
        dom.triggers.openSingle?.addEventListener('click', () => {
            ensureGroups();
            window.activateAdminPage?.('user-create-single');
        });
        dom.triggers.openBulk?.addEventListener('click', () => {
            window.activateAdminPage?.('user-create-bulk');
        });
        dom.single.back?.addEventListener('click', () => {
            window.activateAdminPage?.('users');
        });
        dom.bulk.back?.addEventListener('click', () => {
            window.activateAdminPage?.('users');
        });
    }

    function handleFileSelection() {
        const file = dom.bulk.fileInput?.files?.[0];
        const hasFile = Boolean(file);
        
        if (dom.bulk.uploadBtn) {
            dom.bulk.uploadBtn.disabled = !hasFile;
        }

        // Update upload zone appearance
        if (dom.bulk.uploadZone) {
            if (hasFile) {
                dom.bulk.uploadZone.classList.add('has-file');
                // Update the text to show filename
                const textEl = dom.bulk.uploadZone.querySelector('.user-bulk-upload-text');
                if (textEl) {
                    textEl.textContent = t('user_bulk_file_selected', 'File selected:');
                }
                // Add or update filename display
                let filenameEl = dom.bulk.uploadZone.querySelector('.user-bulk-upload-filename');
                if (!filenameEl) {
                    filenameEl = document.createElement('div');
                    filenameEl.className = 'user-bulk-upload-filename';
                    dom.bulk.uploadZone.appendChild(filenameEl);
                }
                filenameEl.textContent = '';
                const iconEl = Icons.createSvgElement(Icons.wrapSvgBody((typeof featureIconBodies !== 'undefined' ? featureIconBodies : Icons.featureIconBodies).checkCircle24, { strokeWidth: '2', ariaHidden: false }));
                filenameEl.append(iconEl, document.createTextNode(file.name));
            } else {
                dom.bulk.uploadZone.classList.remove('has-file');
                const textEl = dom.bulk.uploadZone.querySelector('.user-bulk-upload-text');
                if (textEl) {
                    textEl.textContent = t('user_bulk_drop_text', 'Drop your file here or click to browse');
                }
                const filenameEl = dom.bulk.uploadZone.querySelector('.user-bulk-upload-filename');
                if (filenameEl) {
                    filenameEl.remove();
                }
            }
        }
    }

    function switchFormat(format) {
        state.selectedFormat = format;

        syncBulkFormatUi();
    }

    function syncBulkFormatUi() {
        const format = state.selectedFormat;

        // Update tab active states
        if (dom.bulk.formatXlsx) {
            dom.bulk.formatXlsx.classList.toggle('active', format === 'xlsx');
        }
        if (dom.bulk.formatCsv) {
            dom.bulk.formatCsv.classList.toggle('active', format === 'csv');
        }
        
        if (dom.bulk.csvHint) {
            dom.bulk.csvHint.hidden = format !== 'csv';
        }
    }

    async function handleTemplateDownload() {
        const format = state.selectedFormat;
        setButtonLoadingState(dom.bulk.downloadBtn, true, t('user_bulk_busy_downloading', 'Downloading…'));
        
        try {
            let url;
            let filename;
            
            if (format === 'csv') {
                // CSV: auto-detect language from browser, no locale param needed
                url = '/api/v1/admin/users/create/csv/template';
                filename = 'user_import.csv';
            } else {
                url = '/api/v1/admin/users/create/xlsx/template';
                filename = 'user_import.xlsx';
            }
            
            const response = await window.authedFetch(url, {
                method: 'GET',
                headers: {
                    'Content-Type': null,
                },
            });
            
            if (!response.ok) {
                notifyError(t('user_bulk_template_download_failed', 'Failed to download template.'));
                return;
            }
            
            // For CSV, try to get filename from Content-Disposition header
            if (format === 'csv') {
                const contentDisposition = response.headers.get('Content-Disposition');
                if (contentDisposition) {
                    const match = contentDisposition.match(/filename="?([^"]+)"?/);
                    if (match) {
                        filename = match[1];
                    }
                }
            }
            
            const blob = await response.blob();
            const link = document.createElement('a');
            const blobUrl = URL.createObjectURL(blob);
            link.href = blobUrl;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(blobUrl);
            notifySuccess?.(t('user_bulk_template_download_success', 'Template downloaded.'));
        } catch (error) {
            console.error('download template failed', error);
            notifyError?.(error?.message || t('user_bulk_template_download_failed', 'Failed to download template.'));
        } finally {
            setButtonLoadingState(dom.bulk.downloadBtn, false);
        }
    }

    async function handleBulkUpload() {
        const file = dom.bulk.fileInput?.files?.[0];
        if (!file) {
            notifyError?.(t('user_bulk_error_choose_file_first', 'Please choose an XLSX or CSV file first.'));
            return;
        }
        
        // Validate file extension
        const filename = file.name.toLowerCase();
        if (!filename.endsWith('.xlsx') && !filename.endsWith('.csv')) {
            notifyError?.(t('user_bulk_error_invalid_file_type', 'Please upload an XLSX or CSV file.'));
            return;
        }

        openBulkOptionsModal(file);
    }

    function openBulkOptionsModal(file) {
        if (!dom.bulk.optionsOverlay) {
            submitBulkUpload();
            return;
        }

        state.lastBulkOptionsFocus = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        setBulkOptionsStatus();
        if (dom.bulk.optionsFileName) {
            dom.bulk.optionsFileName.textContent = file?.name || '';
        }
        if (dom.bulk.forcePasswordChange) {
            dom.bulk.forcePasswordChange.checked = true;
        }
        dom.bulk.optionsOverlay.hidden = false;
        dom.bulk.optionsOverlay.classList.add('active');
        window.requestAnimationFrame(() => dom.bulk.defaultPassword?.focus());
    }

    function closeBulkOptionsModal() {
        if (dom.bulk.optionsOverlay) {
            dom.bulk.optionsOverlay.classList.remove('active');
            dom.bulk.optionsOverlay.hidden = true;
        }
        setBulkOptionsStatus();
        if (dom.bulk.optionsFileName) {
            dom.bulk.optionsFileName.textContent = '';
        }
        if (dom.bulk.defaultPassword) {
            dom.bulk.defaultPassword.value = '';
        }
        if (state.lastBulkOptionsFocus && document.contains(state.lastBulkOptionsFocus)) {
            state.lastBulkOptionsFocus.focus();
        }
        state.lastBulkOptionsFocus = null;
    }

    function setBulkOptionsStatus(message = '', tone = 'error') {
        if (!dom.bulk.optionsStatus) {
            return;
        }
        dom.bulk.optionsStatus.hidden = !message;
        dom.bulk.optionsStatus.textContent = message;
        dom.bulk.optionsStatus.dataset.tone = tone;
    }

    async function submitBulkUpload() {
        const file = dom.bulk.fileInput?.files?.[0];
        if (!file) {
            closeBulkOptionsModal();
            notifyError?.(t('user_bulk_error_choose_file_first', 'Please choose an XLSX or CSV file first.'));
            return;
        }

        const defaultPassword = dom.bulk.defaultPassword?.value?.trim() || '';
        if (!defaultPassword) {
            const message = t('users_import_default_password_required', 'Enter a default password for imported users.');
            setBulkOptionsStatus(message);
            dom.bulk.defaultPassword?.focus();
            return;
        }

        const filename = file.name.toLowerCase();
        hideBulkResults();
        setButtonLoadingState(dom.bulk.optionsConfirm, true, t('user_bulk_busy_uploading', 'Uploading…'));
        setButtonLoadingState(dom.bulk.uploadBtn, true, t('user_bulk_busy_uploading', 'Uploading…'));
        dom.bulk.uploadBtn.disabled = true;

        try {
            const formData = new FormData();
            formData.append('file', file, file.name);
            formData.append('default_password', defaultPassword);
            formData.append('force_password_change', dom.bulk.forcePasswordChange?.checked ? 'true' : 'false');

            // Use the unified bulk upload endpoint
            const response = await window.authedFetch('/api/v1/admin/users/create/bulk', {
                method: 'POST',
                headers: {
                    'Content-Type': null,
                },
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || t('user_bulk_upload_failed', 'Failed to upload users.'));
            }

            const result = await response.json();
            renderBulkResults(result);
            closeBulkOptionsModal();
            
            const fileType = filename.endsWith('.csv') ? 'CSV' : 'Excel';
            const totalCreated = typeof result.total_created === 'number' ? result.total_created : (result.created_users?.length ?? 0);
            const totalErrors = typeof result.total_errors === 'number'
                ? result.total_errors
                : Array.isArray(result.errors)
                    ? result.errors.length
                    : 0;
            const successMessage = totalErrors > 0
                ? formatT('user_bulk_import_finished_with_errors', '{fileType} import finished. {created} users created, {errors} row(s) failed.', { fileType, created: totalCreated, errors: totalErrors })
                : formatT('user_bulk_import_finished_success', '{fileType} import finished. {created} users created.', { fileType, created: totalCreated });
            
            notifySuccess?.(successMessage);
            dom.bulk.fileInput.value = '';
            handleFileSelection();

            // The response contains the only clear-text copy of every generated
            // temporary password. Keep the administrator on this page and move
            // focus to the result instead of hiding those credentials behind an
            // automatic navigation back to the user list.
            focusBulkResults();
        } catch (error) {
            console.error('bulk upload failed', error);
            const message = error?.message || t('user_bulk_upload_failed', 'Failed to upload users.');
            setBulkOptionsStatus(message);
            notifyError?.(message);
        } finally {
            setButtonLoadingState(dom.bulk.optionsConfirm, false);
            setButtonLoadingState(dom.bulk.uploadBtn, false);
            dom.bulk.uploadBtn.disabled = !dom.bulk.fileInput?.files?.length;
        }
    }

    function renderBulkResults(result = {}) {
        if (!dom.bulk.results) {
            return;
        }
        const { total_created, total_errors, errors = [], created_users = [] } = result;
        const errorCount = total_errors ?? errors.length;
        const temporaryPasswordRows = Array.isArray(created_users)
            ? created_users.filter((user) => user && typeof user.temporary_password === 'string' && user.temporary_password)
            : [];
        
        dom.bulk.results.hidden = false;
        dom.bulk.results.innerHTML = `
            <div class="user-bulk-results-header">
                <div class="user-bulk-results-stat">
                    <div class="user-bulk-results-stat-icon success">
                        ${Icons.check}       
                    </div>
                    <div class="user-bulk-results-stat-content">
                        <span class="user-bulk-results-stat-value">${total_created ?? 0}</span>
                        <span class="user-bulk-results-stat-label">${t('user_bulk_results_users_created', 'Users Created')}</span>
                    </div>
                </div>
                <div class="user-bulk-results-stat">
                    <div class="user-bulk-results-stat-icon error">
                        ${Icons.error}      
                    </div>
                    <div class="user-bulk-results-stat-content">
                        <span class="user-bulk-results-stat-value">${errorCount}</span>
                        <span class="user-bulk-results-stat-label">${t('user_bulk_results_errors', 'Errors')}</span>
                    </div>
                </div>
            </div>
        `;

        if (temporaryPasswordRows.length) {
            const credentials = document.createElement('div');
            credentials.className = 'user-bulk-results-passwords';

            const title = document.createElement('p');
            title.className = 'user-bulk-results-passwords-title';
            title.textContent = t('user_bulk_results_temp_passwords_title', 'Temporary passwords');
            credentials.appendChild(title);

            const description = document.createElement('p');
            description.className = 'user-bulk-results-passwords-desc';
            description.textContent = t('user_bulk_results_temp_passwords_desc', 'Copy these now. They are shown only once and each user must change the password after sign-in.');
            credentials.appendChild(description);

            const list = document.createElement('ul');
            list.className = 'user-bulk-results-passwords-list';
            temporaryPasswordRows.forEach((user) => {
                const item = document.createElement('li');
                const email = document.createElement('span');
                email.className = 'user-bulk-results-passwords-email';
                email.textContent = user.email || '';
                const password = document.createElement('code');
                password.className = 'user-bulk-results-passwords-value';
                password.textContent = user.temporary_password;
                item.append(email, password);
                list.appendChild(item);
            });
            credentials.appendChild(list);
            dom.bulk.results.appendChild(credentials);
        }
        
        if (errors.length) {
            const list = document.createElement('ul');
            list.className = 'user-bulk-results-errors';
            errors.slice(0, 20).forEach((message) => {
                const item = document.createElement('li');
                item.textContent = message;
                list.appendChild(item);
            });
            if (errors.length > 20) {
                const more = document.createElement('li');
                more.textContent = formatT('user_bulk_results_more_errors', '…and {count} more errors.', { count: errors.length - 20 });
                list.appendChild(more);
            }
            dom.bulk.results.appendChild(list);
        }
    }

    /**
     * Reveal the one-time import result to keyboard and screen-reader users.
     * The shared scroll helper respects both supported reduced-motion settings.
     */
    function focusBulkResults() {
        if (!dom.bulk.results || dom.bulk.results.hidden) {
            return;
        }

        dom.bulk.results.focus({ preventScroll: true });
        if (typeof window.scrollAdminPaginatedListToStart === 'function') {
            window.scrollAdminPaginatedListToStart(dom.bulk.results);
            return;
        }
        dom.bulk.results.scrollIntoView?.({ block: 'start' });
    }

    function hideBulkResults() {
        if (dom.bulk.results) {
            dom.bulk.results.hidden = true;
            dom.bulk.results.innerHTML = '';
        }
    }


    function ensureGroups() {
        if (state.groupsLoaded || state.groupsPromise || !dom.single.group) {
            return state.groupsPromise;
        }
        state.groupsPromise = fetchAdminGroupsList()
            .then((groups) => {
                if (Array.isArray(groups)) {
                    populateGroupSelect(groups);
                    state.groupsLoaded = true;
                }
            })
            .catch((error) => {
                console.error('load groups failed', error);
                notifyError?.(t('user_create_groups_load_failed', 'Failed to load groups list.'));
            })
            .finally(() => {
                state.groupsPromise = null;
            });
        return state.groupsPromise;
    }

    function populateGroupSelect(groups) {
        if (!dom.single.group) {
            return;
        }
        const existingValue = dom.single.group.value;
        dom.single.group.innerHTML = '';
        ensureSelectPlaceholderOption(
            dom.single.group,
            t('group_form_select_group', 'Select a group')
        );
        groups.forEach((group) => {
            if (!group?.id) {
                return;
            }
            const option = document.createElement('option');
            option.value = group.id;
            option.textContent = group.name || group.id;
            dom.single.group.appendChild(option);
        });
        dom.single.group.value = existingValue;
        state.singleSelects.group = upgradeSingleSelect(dom.single.group, {
            key: 'user-create-group',
            placeholder: t('group_form_select_group', 'Select a group'),
        });
        syncSingleFormSelects();
    }

    function handleBackNavigation() {
        if (dom.bulk.optionsOverlay && !dom.bulk.optionsOverlay.hidden) {
            closeBulkOptionsModal();
            return;
        }
        window.activateAdminPage?.('users');
    }

    function registerEscapeShortcut() {
        if (state.escapeRegistration || typeof window === 'undefined' || !window.registerEscapeHandler) {
            return;
        }
        state.escapeRegistration = window.registerEscapeHandler({
            id: 'admin-user-create-escape',
            priority: 140,
            isActive: () => {
                const isSingleActive = singlePage && !singlePage.hidden;
                const isBulkActive = bulkPage && !bulkPage.hidden;
                return isSingleActive || isBulkActive;
            },
            close: handleBackNavigation,
        });
    }

    window.initAdminUserCreatePage = init;
    window.teardownAdminUserCreatePage = teardown;
})();
