/**
 * Admin login settings management using shared helper controller.
 */
(function () {
    if (typeof window.createSettingsPageController !== 'function') {
        window.initLoginGeneralSettingsPage = () => {};
        window.teardownLoginGeneralSettingsPage = () => {};
        window.initLoginCustomizationSettingsPage = () => {};
        window.teardownLoginCustomizationSettingsPage = () => {};
        window.initLoginSocialSettingsPage = () => {};
        window.teardownLoginSocialSettingsPage = () => {};
        window.initLoginEnterpriseSSOSettingsPage = () => {};
        window.teardownLoginEnterpriseSSOSettingsPage = () => {};
        window.initLoginLDAPSettingsPage = () => {};
        window.teardownLoginLDAPSettingsPage = () => {};
        return;
    }

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const LOGIN_BG_UPLOAD_ENABLED_DESIGN = 'split_image';

    const enterpriseSsoUiState = {
        diagnosticsAbortController: null,
    };

    const OIDC_CHECK_COPY = {
        oidc_required_settings: ['auth_diagnostics_check_required', 'Required settings'],
        oidc_scopes: ['auth_diagnostics_check_scopes', 'Requested scopes'],
        oidc_discovery_reachable: ['auth_diagnostics_check_discovery', 'Discovery document'],
        oidc_issuer_match: ['auth_diagnostics_check_issuer', 'Issuer validation'],
        oidc_jwks_reachable: ['auth_diagnostics_check_jwks', 'Signing keys (JWKS)'],
        oidc_email_claims: ['auth_diagnostics_check_email_claims', 'Email claims'],
    };

    const queryLoginBgEl = (selector) => document.querySelector(`#loginCustomizationFields [data-login-bg="${selector}"]`);

    const loginSettingsController = window.createSettingsPageController({
        pageKey: 'login_general',
        containerId: 'loginSettingsFields',
        statusId: 'loginSettingsStatus',
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: 'Unable to load login settings.',
        onError: (message) => notifyError?.(message),
    });

    const loginCustomizationController = window.createSettingsPageController({
        pageKey: 'login_customization',
        containerId: 'loginCustomizationFields',
        statusId: 'loginCustomizationStatus',
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: 'Unable to load login customization settings.',
        onLoad: () => {
            mountLoginBackgroundRow();
            bindLoginCustomizationFieldListeners();
            updateLoginBackgroundRowVisibility();
        },
        onError: (message) => notifyError?.(message),
    });

    const loginSocialController = window.createSettingsPageController({
        pageKey: 'login_social',
        containerId: 'loginSocialFields',
        statusId: 'loginSocialStatus',
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: 'Unable to load OAuth settings.',
        onError: (message) => notifyError?.(message),
    });

    const loginEnterpriseSSOController = window.createSettingsPageController({
        pageKey: 'login_enterprise_sso',
        containerId: 'loginEnterpriseSSOFields',
        statusId: 'loginEnterpriseSSOStatus',
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: 'Unable to load enterprise SSO settings.',
        onError: (message) => notifyError?.(message),
    });

    const loginLDAPController = window.createSettingsPageController({
        pageKey: 'login_ldap',
        containerId: 'loginLDAPFields',
        statusId: 'loginLDAPStatus',
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: 'Unable to load LDAP settings.',
        onLoad: () => {
            mountLdapCaCertRow();
            bindLoginLDAPFieldListeners();
            updateLdapCaCertRowVisibility();
            fetchLdapCaCertStatus();
        },
        onError: (message) => notifyError?.(message),
    });

    // Login background image state
    const loginBgState = {
        backgroundImage: null,
        objectUrl: null,
    };

    const ldapCaState = {
        status: null,
    };

    function translatedTuple(copy, fallback) {
        return copy ? t(copy[0], copy[1]) : fallback;
    }

    function formatDiagnosticDetails(details) {
        if (!details || typeof details !== 'object') return '';
        if (details.configured_issuer || details.metadata_issuer) {
            return t('auth_diagnostics_issuer_values', 'Configured: {configured} · Provider: {provider}')
                .replace('{configured}', details.configured_issuer || '—')
                .replace('{provider}', details.metadata_issuer || '—');
        }
        if (Array.isArray(details.missing) && details.missing.length) {
            return t('auth_diagnostics_missing_values', 'Missing: {values}')
                .replace('{values}', details.missing.join(', '));
        }
        if (details.url) return details.url;
        if (Number.isFinite(details.key_count)) {
            return t('auth_diagnostics_key_count', 'Keys found: {count}')
                .replace('{count}', String(details.key_count));
        }
        return '';
    }

    function renderOidcConfigurationTest(payload) {
        const target = document.getElementById('oidcConfigurationTestStatus');
        if (!target) return;
        target.replaceChildren();
        const list = document.createElement('ul');
        list.className = 'auth-diagnostics-checks';
        for (const check of payload.checks || []) {
            const item = document.createElement('li');
            item.className = 'auth-diagnostics-check';
            item.dataset.status = check.status;
            const statusCopy = {
                passed: ['auth_diagnostics_passed', 'Passed'],
                warning: ['auth_diagnostics_warning', 'Warning'],
                failed: ['auth_diagnostics_failed', 'Failed'],
            }[check.status];
            const label = translatedTuple(OIDC_CHECK_COPY[check.code], check.code);
            item.textContent = `${translatedTuple(statusCopy, check.status)} — ${label}`;
            const details = formatDiagnosticDetails(check.details);
            if (details) {
                const detail = document.createElement('span');
                detail.className = 'auth-diagnostics-check-details';
                detail.textContent = details;
                item.append(detail);
            }
            list.append(item);
        }
        target.append(list);
    }

    function renderAuthDiagnostics(items) {
        const rows = document.getElementById('authDiagnosticsRows');
        const empty = document.getElementById('authDiagnosticsEmpty');
        if (!rows || !empty) return;
        rows.replaceChildren();
        empty.hidden = Boolean(items.length);
        for (const item of items) {
            const row = document.createElement('tr');
            const values = [
                new Date(item.timestamp).toLocaleString(),
                item.reference || '—',
                String(item.provider || '—').toUpperCase(),
                item.stage || '—',
                item.error_code || '—',
            ];
            values.forEach((value, index) => {
                const cell = document.createElement('td');
                if (index === 1) {
                    const code = document.createElement('code');
                    code.textContent = value;
                    cell.append(code);
                } else {
                    cell.textContent = value;
                }
                row.append(cell);
            });
            rows.append(row);
        }
    }

    async function loadAuthDiagnostics() {
        try {
            const response = await window.authedFetch('/api/v1/admin/auth-diagnostics?page_size=20', {
                signal: enterpriseSsoUiState.diagnosticsAbortController?.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            renderAuthDiagnostics(Array.isArray(payload.items) ? payload.items : []);
        } catch (error) {
            if (error?.name !== 'AbortError') {
                notifyError?.(t('auth_diagnostics_load_error', 'Unable to load authentication diagnostics.'));
            }
        }
    }

    async function runOidcConfigurationTest() {
        const button = document.getElementById('testOidcConfiguration');
        if (button) button.disabled = true;
        try {
            const response = await window.authedFetch('/api/v1/admin/auth-diagnostics/oidc/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
                signal: enterpriseSsoUiState.diagnosticsAbortController?.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            renderOidcConfigurationTest(await response.json());
        } catch (error) {
            if (error?.name !== 'AbortError') {
                notifyError?.(t('auth_diagnostics_test_error', 'Unable to test the OIDC configuration.'));
            }
        } finally {
            if (button) button.disabled = false;
        }
    }

    function initAuthDiagnostics() {
        enterpriseSsoUiState.diagnosticsAbortController?.abort();
        enterpriseSsoUiState.diagnosticsAbortController = new AbortController();
        document.getElementById('refreshAuthDiagnostics')?.addEventListener('click', loadAuthDiagnostics);
        document.getElementById('testOidcConfiguration')?.addEventListener('click', runOidcConfigurationTest);
        void loadAuthDiagnostics();
    }

    function teardownAuthDiagnostics() {
        enterpriseSsoUiState.diagnosticsAbortController?.abort();
        enterpriseSsoUiState.diagnosticsAbortController = null;
        document.getElementById('refreshAuthDiagnostics')?.removeEventListener('click', loadAuthDiagnostics);
        document.getElementById('testOidcConfiguration')?.removeEventListener('click', runOidcConfigurationTest);
    }

    /**
     * Admin upload actions on this page benefit from a persistent picker just
     * like the branding assets page. Using the shared helper avoids the browser
     * timing edge cases where a temporary detached input never fires `change`.
     *
     * @param {string} id
     * @param {string} accept
     * @returns {{ open: () => Promise<File|null>, destroy: () => void } | null}
     */
    const createAdminFilePicker = (id, accept) => {
        if (typeof window.createPersistentFilePicker !== 'function') {
            return null;
        }

        return window.createPersistentFilePicker({
            id,
            accept,
        });
    };

    const ldapCaFilePicker = createAdminFilePicker('admin-ldap-ca-cert-picker', '.pem,.crt,.cer');
    const loginBackgroundFilePicker = createAdminFilePicker('admin-login-background-picker', 'image/png,image/jpeg,image/webp');

    const queryLdapCaEl = (selector) => document.querySelector(`#loginLDAPFields [data-ldap-ca="${selector}"]`);

    const getLoginLDAPBooleanValue = (fieldKey) => {
        const row = document.querySelector(`#loginLDAPFields [data-field-key="${fieldKey}"]`);
        if (!(row instanceof HTMLElement)) return false;
        const control = row.querySelector('input[type="checkbox"]');
        if (!(control instanceof HTMLInputElement)) return false;
        return Boolean(control.checked);
    };

    const getLoginLDAPStringListValue = (fieldKey) => {
        const row = document.querySelector(`#loginLDAPFields [data-field-key="${fieldKey}"]`);
        if (!(row instanceof HTMLElement)) return [];
        const control = row.querySelector('[data-keyword-tags]');
        if (!(control instanceof HTMLElement)) return [];
        try {
            const value = JSON.parse(control.dataset.keywordTags || '[]');
            return Array.isArray(value) ? value.map((item) => String(item || '').trim()).filter(Boolean) : [];
        } catch {
            return [];
        }
    };

    const shouldShowLdapCaCertRow = () => {
        const enabled = getLoginLDAPBooleanValue('enable_ldap');
        const validateCert = getLoginLDAPBooleanValue('ldap_validate_cert');
        const useTls = getLoginLDAPStringListValue('ldap_server_uris').some((endpoint) => {
            const scheme = endpoint.toLowerCase().split('://', 1)[0];
            return scheme === 'ldaps' || scheme === 'ldap+starttls';
        });
        return enabled && validateCert && useTls;
    };

    const updateLdapCaCertPreview = (statusPayload) => {
        const previewElement = queryLdapCaEl('preview');
        if (!previewElement) return;

        const content = previewElement.querySelector('.asset-upload-preview-content');
        const placeholder = previewElement.querySelector('.asset-upload-placeholder');
        if (content) {
            content.replaceChildren();
        }

        const isUploaded = Boolean(statusPayload?.uploaded);
        const hasManualPath = Boolean(statusPayload?.configured_path) && !Boolean(statusPayload?.using_managed_path);

        if (isUploaded && content) {
            const title = document.createElement('div');
            title.textContent = statusPayload?.filename || 'ldap_ca_cert.pem';
            title.style.fontWeight = '600';

            const subtitle = document.createElement('div');
            subtitle.textContent = t(
                'ldap_ca_cert_managed_in_use',
                'Managed certificate is active for LDAP TLS validation.'
            );
            subtitle.style.fontSize = '12px';
            subtitle.style.opacity = '0.85';
            subtitle.style.marginTop = '4px';

            content.append(title, subtitle);
        } else if (hasManualPath && content) {
            const title = document.createElement('div');
            title.textContent = t('ldap_ca_cert_manual_path_title', 'Manual certificate path is configured');
            title.style.fontWeight = '600';

            const pathText = document.createElement('div');
            pathText.textContent = String(statusPayload.configured_path || '');
            pathText.style.fontSize = '12px';
            pathText.style.opacity = '0.85';
            pathText.style.marginTop = '4px';
            pathText.style.wordBreak = 'break-all';

            const hint = document.createElement('div');
            hint.textContent = t(
                'ldap_ca_cert_manual_path_hint',
                'Upload here to switch to managed storage and remove manual path handling.'
            );
            hint.style.fontSize = '12px';
            hint.style.opacity = '0.85';
            hint.style.marginTop = '6px';

            content.append(title, pathText, hint);
        }

        const hasPreview = isUploaded || hasManualPath;
        if (hasPreview) {
            previewElement.setAttribute('data-has-preview', 'true');
            if (placeholder) {
                placeholder.setAttribute('hidden', '');
            }
        } else {
            previewElement.removeAttribute('data-has-preview');
            if (placeholder) {
                placeholder.removeAttribute('hidden');
            }
        }
    };

    const updateLdapCaCertRowVisibility = () => {
        const row = queryLdapCaEl('row');
        if (!(row instanceof HTMLElement)) return;
        const shouldShow = shouldShowLdapCaCertRow();
        row.hidden = !shouldShow;
        row.style.display = shouldShow ? '' : 'none';
    };

    const handleLoginLDAPFieldValueChange = () => {
        updateLdapCaCertRowVisibility();
    };

    const bindLoginLDAPFieldListeners = () => {
        const container = document.getElementById('loginLDAPFields');
        if (!(container instanceof HTMLElement)) return;
        container.removeEventListener('change', handleLoginLDAPFieldValueChange);
        container.removeEventListener('input', handleLoginLDAPFieldValueChange);
        container.addEventListener('change', handleLoginLDAPFieldValueChange);
        container.addEventListener('input', handleLoginLDAPFieldValueChange);
    };

    const unbindLoginLDAPFieldListeners = () => {
        const container = document.getElementById('loginLDAPFields');
        if (!(container instanceof HTMLElement)) return;
        container.removeEventListener('change', handleLoginLDAPFieldValueChange);
        container.removeEventListener('input', handleLoginLDAPFieldValueChange);
    };

    const localizeLdapCaCertRow = (rowElement) => {
        if (!(rowElement instanceof HTMLElement)) return;

        const title = rowElement.querySelector('.settings-row-title');
        if (title) {
            title.textContent = t('ldap_ca_cert_row_title', 'LDAP CA Certificate');
        }

        const description = rowElement.querySelector('.settings-row-desc');
        if (description) {
            description.textContent = t(
                'ldap_ca_cert_row_desc',
                'Upload or remove the CA certificate used to validate your LDAP TLS server certificate. The file is stored and managed by Omlorix.'
            );
        }

        const placeholder = rowElement.querySelector('.asset-upload-placeholder');
        if (placeholder) {
            placeholder.textContent = t('ldap_ca_cert_placeholder', 'No certificate uploaded');
        }

        const uploadButtonText = rowElement.querySelector('#uploadLdapCaCertButton > span');
        if (uploadButtonText) {
            uploadButtonText.textContent = t('ldap_ca_cert_upload_button', 'Upload Certificate');
        }

        const deleteButtonText = rowElement.querySelector('#deleteLdapCaCertButton > span');
        if (deleteButtonText) {
            deleteButtonText.textContent = t('ldap_ca_cert_delete_button', 'Delete');
        }
    };

    const mountLdapCaCertRow = () => {
        const template = document.getElementById('ldapCaCertRowTemplate');
        if (!(template instanceof HTMLTemplateElement)) return false;

        const validateRow = document.querySelector('#loginLDAPFields [data-field-key="ldap_validate_cert"]');
        const sectionBody = validateRow?.closest('.settings-section')?.querySelector('.settings-section-body');
        if (!sectionBody) return false;

        let row = queryLdapCaEl('row');
        if (!row) {
            const rowTemplate = template.content.firstElementChild;
            if (!rowTemplate) return false;
            row = rowTemplate.cloneNode(true);
            localizeLdapCaCertRow(row);
            if (validateRow && validateRow.parentElement === sectionBody) {
                validateRow.after(row);
            } else {
                sectionBody.appendChild(row);
            }
        } else {
            localizeLdapCaCertRow(row);
        }

        bindLdapCaButtons();
        updateLdapCaCertPreview(ldapCaState.status);
        return true;
    };

    const unmountLdapCaCertRow = () => {
        const row = queryLdapCaEl('row');
        if (row) {
            row.remove();
        }
    };

    const fetchLdapCaCertStatus = async () => {
        try {
            const response = await window.authedFetch('/api/v1/settings/ldap-ca-cert/status', {
                method: 'GET',
                headers: { 'Content-Type': null },
                cache: 'no-cache',
            });

            if (!response.ok) {
                return;
            }

            const payload = await response.json();
            ldapCaState.status = payload;
            updateLdapCaCertPreview(payload);
        } catch (error) {
            console.error('Failed to load LDAP CA certificate status', error);
        }
    };

    const uploadLdapCaCert = async () => {
        if (typeof ldapCaFilePicker?.open !== 'function') {
            notifyError?.(t('ldap_ca_cert_upload_error', 'Failed to upload LDAP CA certificate.'));
            return;
        }

        const file = await ldapCaFilePicker?.open?.();
        if (!file) return;

        const uploadBtn = queryLdapCaEl('upload');
        window.setButtonLoadingState?.(uploadBtn, true, t('loading', 'Loading...'));
        try {
            const formData = new FormData();
            formData.append('certificate', file);

            const response = await window.authedFetch('/api/v1/settings/ldap-ca-cert/upload', {
                method: 'POST',
                headers: { 'Content-Type': null },
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                notifyError?.(errorData.detail || t('ldap_ca_cert_upload_error', 'Failed to upload LDAP CA certificate.'));
                return;
            }

            notifySuccess?.(t('ldap_ca_cert_upload_success', 'LDAP CA certificate uploaded successfully.'));
            await fetchLdapCaCertStatus();
        } catch (error) {
            console.error('Failed to upload LDAP CA certificate', error);
            notifyError?.(t('ldap_ca_cert_upload_error', 'Failed to upload LDAP CA certificate.'));
        } finally {
            window.setButtonLoadingState?.(uploadBtn, false);
        }
    };

    const deleteLdapCaCert = async () => {
        const deleteBtn = queryLdapCaEl('delete');
        window.setButtonLoadingState?.(deleteBtn, true, t('loading', 'Loading...'));
        try {
            const response = await window.authedFetch('/api/v1/settings/ldap-ca-cert/delete', {
                method: 'DELETE',
            });

            if (response.status === 404) {
                ldapCaState.status = null;
                updateLdapCaCertPreview(null);
                return;
            }

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                notifyError?.(errorData.detail || t('ldap_ca_cert_delete_error', 'Failed to delete LDAP CA certificate.'));
                return;
            }

            notifySuccess?.(t('ldap_ca_cert_delete_success', 'LDAP CA certificate removed successfully.'));
            await fetchLdapCaCertStatus();
        } catch (error) {
            console.error('Failed to delete LDAP CA certificate', error);
            notifyError?.(t('ldap_ca_cert_delete_error', 'Failed to delete LDAP CA certificate.'));
        } finally {
            window.setButtonLoadingState?.(deleteBtn, false);
        }
    };

    const bindLdapCaButtons = () => {
        const uploadBtn = queryLdapCaEl('upload');
        const deleteBtn = queryLdapCaEl('delete');

        if (uploadBtn) {
            uploadBtn.removeEventListener('click', uploadLdapCaCert);
            uploadBtn.addEventListener('click', uploadLdapCaCert);
        }
        if (deleteBtn) {
            deleteBtn.removeEventListener('click', deleteLdapCaCert);
            deleteBtn.addEventListener('click', deleteLdapCaCert);
        }
    };

    const unbindLdapCaButtons = () => {
        const uploadBtn = queryLdapCaEl('upload');
        const deleteBtn = queryLdapCaEl('delete');

        if (uploadBtn) {
            uploadBtn.removeEventListener('click', uploadLdapCaCert);
        }
        if (deleteBtn) {
            deleteBtn.removeEventListener('click', deleteLdapCaCert);
        }
    };

    const getLoginDesignValue = () => {
        const row = document.querySelector('#loginCustomizationFields [data-field-key="login_design"]');
        if (!(row instanceof HTMLElement)) return '';
        const control = row.querySelector('select, input, textarea');
        if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) {
            return '';
        }
        return String(control.value || '').trim().toLowerCase();
    };

    const getLoginCustomizationBooleanValue = (fieldKey) => {
        const row = document.querySelector(`#loginCustomizationFields [data-field-key="${fieldKey}"]`);
        if (!(row instanceof HTMLElement)) return false;
        const control = row.querySelector('input[type="checkbox"]');
        if (!(control instanceof HTMLInputElement)) return false;
        return Boolean(control.checked);
    };

    const updateLoginBackgroundRowVisibility = () => {
        const row = queryLoginBgEl('row');
        if (!(row instanceof HTMLElement)) return;
        const designMatches = getLoginDesignValue() === LOGIN_BG_UPLOAD_ENABLED_DESIGN;
        const showBgEnabled = getLoginCustomizationBooleanValue('show_background_image');
        const shouldShow = designMatches && showBgEnabled;
        row.hidden = !shouldShow;
        row.style.display = shouldShow ? '' : 'none';
    };

    const handleLoginCustomizationFieldValueChange = (event) => {
        const target = event?.target;
        if (!(target instanceof HTMLElement)) return;
        const fieldRow = target.closest?.('[data-field-key]');
        const fieldKey = fieldRow instanceof HTMLElement ? fieldRow.dataset.fieldKey : '';
        if (fieldKey !== 'login_design' && fieldKey !== 'show_background_image') return;
        updateLoginBackgroundRowVisibility();
    };

    const bindLoginCustomizationFieldListeners = () => {
        const container = document.getElementById('loginCustomizationFields');
        if (!(container instanceof HTMLElement)) return;
        container.removeEventListener('change', handleLoginCustomizationFieldValueChange);
        container.removeEventListener('input', handleLoginCustomizationFieldValueChange);
        container.addEventListener('change', handleLoginCustomizationFieldValueChange);
        container.addEventListener('input', handleLoginCustomizationFieldValueChange);
    };

    const unbindLoginCustomizationFieldListeners = () => {
        const container = document.getElementById('loginCustomizationFields');
        if (!(container instanceof HTMLElement)) return;
        container.removeEventListener('change', handleLoginCustomizationFieldValueChange);
        container.removeEventListener('input', handleLoginCustomizationFieldValueChange);
    };

    const mountLoginBackgroundRow = () => {
        const template = document.getElementById('loginBackgroundRowTemplate');
        if (!(template instanceof HTMLTemplateElement)) return false;

        const showBackgroundImageRow = document.querySelector('#loginCustomizationFields [data-field-key="show_background_image"]');
        const loginDesignRow = document.querySelector('#loginCustomizationFields [data-field-key="login_design"]');
        const sectionBody = (showBackgroundImageRow || loginDesignRow)?.closest('.settings-section')?.querySelector('.settings-section-body');
        if (!sectionBody) return false;

        let row = queryLoginBgEl('row');
        if (!row) {
            const rowTemplate = template.content.firstElementChild;
            if (!rowTemplate) return false;
            row = rowTemplate.cloneNode(true);
            if (showBackgroundImageRow && showBackgroundImageRow.parentElement === sectionBody) {
                showBackgroundImageRow.after(row);
            } else {
                sectionBody.appendChild(row);
            }

            if (typeof window.initI18n === 'function') {
                window.initI18n(true);
            }
        }

        bindLoginBgButtons();
        updateLoginBgPreview(loginBgState.backgroundImage);
        return true;
    };

    const unmountLoginBackgroundRow = () => {
        const row = queryLoginBgEl('row');
        if (row) {
            row.remove();
        }
    };

    const revokeLoginBgUrl = () => {
        if (loginBgState.objectUrl) {
            URL.revokeObjectURL(loginBgState.objectUrl);
            loginBgState.objectUrl = null;
        }
    };

    const updateLoginBgPreview = (entry) => {
        const previewElement = queryLoginBgEl('preview');
        if (!previewElement) return;

        const content = previewElement.querySelector('.asset-upload-preview-content');
        const placeholder = previewElement.querySelector('.asset-upload-placeholder');

        if (content) {
            content.replaceChildren();
        }

        if (entry?.url) {
            const img = document.createElement('img');
            img.src = entry.url;
            img.alt = t('login_bg_preview_alt', 'Login background preview');
            img.style.maxWidth = '100%';
            img.style.maxHeight = '100px';
            img.style.objectFit = 'cover';
            img.style.borderRadius = '8px';
            if (content) {
                content.appendChild(img);
            }
            previewElement.setAttribute('data-has-preview', 'true');
            if (placeholder) {
                placeholder.setAttribute('hidden', '');
            }
        } else {
            previewElement.removeAttribute('data-has-preview');
            if (placeholder) {
                placeholder.removeAttribute('hidden');
            }
        }
    };

    const fetchLoginBackground = async () => {
        try {
            const response = await window.authedFetch('/api/v1/settings/login-background/get', {
                method: 'GET',
                headers: { 'Content-Type': null },
                cache: 'no-cache',
            });

            if (response.status === 404) {
                revokeLoginBgUrl();
                loginBgState.backgroundImage = null;
                updateLoginBgPreview(null);
                return;
            }

            if (!response.ok) {
                return;
            }

            const blob = await response.blob();
            if (!blob || blob.size === 0) {
                revokeLoginBgUrl();
                loginBgState.backgroundImage = null;
                updateLoginBgPreview(null);
                return;
            }

            revokeLoginBgUrl();
            const url = URL.createObjectURL(blob);
            loginBgState.objectUrl = url;
            loginBgState.backgroundImage = { url };
            updateLoginBgPreview(loginBgState.backgroundImage);
        } catch (error) {
            console.error('Failed to load login background', error);
        }
    };

    const uploadLoginBackground = async () => {
        if (typeof loginBackgroundFilePicker?.open !== 'function') {
            notifyError?.(t('login_bg_upload_error', 'Failed to upload login background.'));
            return;
        }

        const file = await loginBackgroundFilePicker?.open?.();
        if (!file) return;

        try {
            const formData = new FormData();
            formData.append('image', file);

            const response = await window.authedFetch('/api/v1/settings/login-background/upload', {
                method: 'POST',
                headers: { 'Content-Type': null },
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                notifyError?.(errorData.detail || t('login_bg_upload_error', 'Failed to upload login background.'));
                return;
            }

            notifySuccess?.(t('login_bg_upload_success', 'Login background uploaded successfully.'));
            await fetchLoginBackground();
        } catch (error) {
            console.error('Failed to upload login background', error);
            notifyError?.(t('login_bg_upload_error', 'Failed to upload login background.'));
        }
    };

    const deleteLoginBackground = async () => {
        try {
            const response = await window.authedFetch('/api/v1/settings/login-background/delete', {
                method: 'DELETE',
            });

            if (response.status === 404) {
                // Already deleted
                revokeLoginBgUrl();
                loginBgState.backgroundImage = null;
                updateLoginBgPreview(null);
                return;
            }

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                notifyError?.(errorData.detail || t('login_bg_delete_error', 'Failed to delete login background.'));
                return;
            }

            notifySuccess?.(t('login_bg_delete_success', 'Login background deleted successfully.'));
            revokeLoginBgUrl();
            loginBgState.backgroundImage = null;
            updateLoginBgPreview(null);
        } catch (error) {
            console.error('Failed to delete login background', error);
            notifyError?.(t('login_bg_delete_error', 'Failed to delete login background.'));
        }
    };

    const bindLoginBgButtons = () => {
        const uploadBtn = queryLoginBgEl('upload');
        const deleteBtn = queryLoginBgEl('delete');

        if (uploadBtn) {
            uploadBtn.removeEventListener('click', uploadLoginBackground);
            uploadBtn.addEventListener('click', uploadLoginBackground);
        }
        if (deleteBtn) {
            deleteBtn.removeEventListener('click', deleteLoginBackground);
            deleteBtn.addEventListener('click', deleteLoginBackground);
        }
    };

    const unbindLoginBgButtons = () => {
        const uploadBtn = queryLoginBgEl('upload');
        const deleteBtn = queryLoginBgEl('delete');

        if (uploadBtn) {
            uploadBtn.removeEventListener('click', uploadLoginBackground);
        }
        if (deleteBtn) {
            deleteBtn.removeEventListener('click', deleteLoginBackground);
        }
    };

    window.initLoginGeneralSettingsPage = () => {
        loginSettingsController.init();
    };

    window.teardownLoginGeneralSettingsPage = () => {
        loginSettingsController.teardown();
    };

    window.initLoginCustomizationSettingsPage = () => {
        loginCustomizationController.init();
        unbindLoginBgButtons();
        fetchLoginBackground();
    };

    window.teardownLoginCustomizationSettingsPage = () => {
        unbindLoginBgButtons();
        unbindLoginCustomizationFieldListeners();
        loginCustomizationController.teardown();
        unmountLoginBackgroundRow();
        revokeLoginBgUrl();
    };

    window.initLoginSocialSettingsPage = () => {
        loginSocialController.init();
    };

    window.teardownLoginSocialSettingsPage = () => {
        loginSocialController.teardown();
    };

    window.initLoginEnterpriseSSOSettingsPage = () => {
        loginEnterpriseSSOController.init();
        initAuthDiagnostics();
    };

    window.teardownLoginEnterpriseSSOSettingsPage = () => {
        teardownAuthDiagnostics();
        loginEnterpriseSSOController.teardown();
    };

    window.initLoginLDAPSettingsPage = () => {
        loginLDAPController.init();
    };

    window.teardownLoginLDAPSettingsPage = () => {
        unbindLdapCaButtons();
        unbindLoginLDAPFieldListeners();
        loginLDAPController.teardown();
        unmountLdapCaCertRow();
        ldapCaState.status = null;
    };

    window.addEventListener('beforeunload', () => {
        loginBackgroundFilePicker?.destroy?.();
        ldapCaFilePicker?.destroy?.();
    }, { once: true });
})();
