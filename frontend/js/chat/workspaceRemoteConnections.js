/**
 * User-owned SSH and ACP connection pages.
 *
 * The list remains part of Workspace > Connections, while create/edit actions
 * replace that list with a full page just like the project create/edit flow.
 * Icons reuse the shared model icon picker so presets and ACP custom SVGs
 * receive the same preview, accessibility, and sanitization behavior.
 */
(() => {
    const state = {
        initialized: false,
        loading: false,
        allowSsh: false,
        allowAcp: false,
        ssh: [],
        acp: [],
        view: 'list',
        editing: null,
        returnFocus: null,
        sshIconPicker: null,
        acpIconPicker: null,
        savingSsh: false,
        savingAcp: false,
    };

    const SSH_ICON_KEYS = ['laptop', 'desktop', 'pc', 'mobile', 'server', 'terminal', 'home'];
    const ACP_ICON_KEYS = ['codex', 'opencode', 'terminal', 'openai', 'anthropic', 'gemini', 'ollama', 'openrouter', 'omlorix'];
    const SSH_PRIVATE_KEY_MAX_BYTES = 65536;
    const SSH_PRIVATE_KEY_LABELS = new Set([
        'OPENSSH PRIVATE KEY',
        'PRIVATE KEY',
        'RSA PRIVATE KEY',
        'EC PRIVATE KEY',
        'DSA PRIVATE KEY',
    ]);
    const REMOTE_FIELD_IDS = Object.freeze({
        name: { ssh: 'sshName', acp: 'acpName' },
        host: { ssh: 'sshHost' },
        port: { ssh: 'sshPort' },
        username: { ssh: 'sshUsername' },
        host_key: { ssh: 'sshHostKey' },
        private_key: { ssh: 'sshPrivateKey' },
        workspace_root: { ssh: 'sshWorkspace', acp: 'acpWorkspace' },
        ssh_connection_id: { acp: 'acpSsh' },
        executable: { acp: 'acpExecutable' },
        arguments: { acp: 'acpArguments' },
        cwd: { acp: 'acpCwd' },
        additional_directories: { acp: 'acpAdditionalDirectories' },
        mode: { acp: 'acpMode' },
        permission_mode: { acp: 'acpPermission' },
        permission_timeout_seconds: { acp: 'acpPermissionTimeout' },
        prompt_timeout_seconds: { acp: 'acpPromptTimeout' },
    });
    const el = (id) => document.getElementById(id);
    const t = (key, fallback) => window.getTranslation?.(key, fallback) || fallback;
    const escapeHtml = (value) => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    function localizedRemoteDetail(detail) {
        const codeMessages = {
            ssh_private_key_required: ['workspace_ssh_private_key_required', 'Enter a private key before saving.'],
            ssh_private_key_invalid: ['workspace_ssh_private_key_invalid', 'Enter a complete OpenSSH or PEM private key.'],
            ssh_private_key_encrypted: ['workspace_ssh_private_key_encrypted', 'Encrypted private keys are not supported. Use a dedicated key without a passphrase.'],
            acp_enable_ssh_first: ['workspace_acp_enable_ssh_first', 'Enable the selected SSH device before enabling this ACP agent.'],
            ssh_delete_dependencies: ['workspace_ssh_delete_dependencies', 'Remove the ACP agents that use this SSH device first.'],
        };
        const knownMessages = {
            'A private key is required.': ['workspace_ssh_private_key_required', 'Enter a private key before saving.'],
            'A private key is required for the connection test.': ['workspace_ssh_private_key_required', 'Enter a private key before saving.'],
            'Enable the SSH connection before enabling this ACP profile.': ['workspace_acp_enable_ssh_first', 'Enable the selected SSH device before enabling this ACP agent.'],
            'Delete dependent ACP profiles before removing this SSH connection.': ['workspace_ssh_delete_dependencies', 'Remove the ACP agents that use this SSH device first.'],
        };
        const errorCode = detail && typeof detail === 'object' ? String(detail.code || '') : '';
        const translation = codeMessages[errorCode]
            || knownMessages[typeof detail === 'string' ? detail : ''];
        return translation
            ? t(translation[0], translation[1])
            : t('workspace_remote_request_failed', 'The request could not be completed. Please try again.');
    }

    function createRemoteApiError(response, payload) {
        const validationIssues = Array.isArray(payload.detail) ? payload.detail : [];
        const fieldErrors = validationIssues.map((issue) => {
            const location = Array.isArray(issue?.loc) ? issue.loc : [];
            return {
                field: String(location[location.length - 1] || ''),
                message: t('workspace_remote_validation_field', 'Check this field and try again.'),
            };
        }).filter((issue) => issue.field);
        const detailCode = payload.detail && typeof payload.detail === 'object'
            ? String(payload.detail.code || '')
            : '';
        if (detailCode.startsWith('ssh_private_key_')) {
            fieldErrors.push({
                field: 'private_key',
                message: localizedRemoteDetail(payload.detail),
            });
        }
        const error = new Error(
            validationIssues.length
                ? t('workspace_remote_validation_error', 'Check the highlighted fields.')
                : localizedRemoteDetail(payload.detail),
        );
        error.status = response.status;
        error.fieldErrors = fieldErrors;
        error.isRemoteApiError = true;
        return error;
    }

    function normalizeSshPrivateKey(value) {
        let normalized = String(value || '').replace(/\r\n?/g, '\n').trim();
        if (normalized.startsWith('\uFEFF')) normalized = normalized.slice(1).trim();
        return normalized;
    }

    function inspectSshPrivateKey(value) {
        const normalized = normalizeSshPrivateKey(value);
        if (!normalized) return { normalized, code: 'empty' };
        const lines = normalized.split('\n');
        const begin = lines[0].match(/^-----BEGIN ([A-Z0-9 ]+)-----$/);
        const label = begin?.[1] || '';
        if (
            label.includes('ENCRYPTED PRIVATE KEY')
            || normalized.includes('Proc-Type: 4,ENCRYPTED')
        ) {
            return { normalized, code: 'encrypted' };
        }
        const expectedEnd = label ? `-----END ${label}-----` : '';
        if (!SSH_PRIVATE_KEY_LABELS.has(label) || lines.at(-1) !== expectedEnd || lines.length < 3) {
            return { normalized, code: 'invalid' };
        }
        return { normalized, code: 'ready' };
    }

    function setPrivateKeyStatus(message = '', tone = '') {
        const status = el('sshPrivateKeyStatus');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('is-success', tone === 'success');
        status.classList.toggle('is-error', tone === 'error');
    }

    function setSshConnectionFeedback(message = '', tone = '') {
        const feedback = el('sshConnectionFeedback');
        if (!feedback) return;
        feedback.textContent = message;
        feedback.hidden = !message;
        feedback.classList.toggle('is-success', tone === 'success');
        feedback.classList.toggle('is-error', tone === 'error');
    }

    function privateKeyMessage(code) {
        if (code === 'encrypted') {
            return t(
                'workspace_ssh_private_key_encrypted',
                'Encrypted private keys are not supported. Use a dedicated key without a passphrase.',
            );
        }
        return t(
            'workspace_ssh_private_key_invalid',
            'Enter a complete OpenSSH or PEM private key.',
        );
    }

    function preparePrivateKey({ required = false, showFieldError = false } = {}) {
        const field = el('sshPrivateKey');
        const original = field?.value || '';
        const inspection = inspectSshPrivateKey(original);
        if (field && inspection.normalized !== original) field.value = inspection.normalized;

        if (inspection.code === 'empty') {
            setPrivateKeyStatus();
            if (required && showFieldError) {
                setFieldError(
                    'sshPrivateKey',
                    t('workspace_ssh_private_key_required', 'Enter a private key before saving.'),
                );
            }
            return !required;
        }
        if (inspection.code !== 'ready') {
            const message = privateKeyMessage(inspection.code);
            setPrivateKeyStatus(message, 'error');
            if (showFieldError) setFieldError('sshPrivateKey', message);
            return false;
        }

        const normalizedCopy = inspection.normalized !== original;
        setPrivateKeyStatus(
            normalizedCopy
                ? t('workspace_ssh_private_key_ready_normalized', 'Private key recognized. Extra surrounding whitespace was removed.')
                : t('workspace_ssh_private_key_ready', 'Private key format recognized.'),
            'success',
        );
        return true;
    }

    function localizedSshTestFailure(errorCode) {
        const messages = {
            ssh_private_key_invalid: ['workspace_ssh_private_key_invalid', 'Enter a complete OpenSSH or PEM private key.'],
            ssh_private_key_encrypted: ['workspace_ssh_private_key_encrypted', 'Encrypted private keys are not supported. Use a dedicated key without a passphrase.'],
            ssh_authentication_failed: ['workspace_ssh_authentication_failed', 'Authentication was rejected. Check the username and that this public key is authorized on the device.'],
            ssh_host_key_verification_failed: ['workspace_ssh_host_key_verification_failed', 'The device host key does not match the pinned key. Verify the device fingerprint.'],
            ssh_connection_timeout: ['workspace_ssh_connection_timeout', 'The connection timed out. Check the host, port, firewall, and device availability.'],
            ssh_connection_unreachable: ['workspace_ssh_connection_unreachable', 'The SSH device could not be reached. Check the host, port, network, and SSH service.'],
            ssh_connection_failed: ['workspace_ssh_error', 'Connection failed'],
        };
        const message = messages[String(errorCode || '')] || messages.ssh_connection_failed;
        return t(message[0], message[1]);
    }

    async function importSshPrivateKeyFile() {
        const fileInput = el('sshPrivateKeyFile');
        const file = fileInput?.files?.[0];
        if (!file) return;
        clearFieldErrors('sshConnectionForm');
        setSshConnectionFeedback();
        try {
            if (file.size > SSH_PRIVATE_KEY_MAX_BYTES) {
                const message = t(
                    'workspace_ssh_private_key_file_too_large',
                    'The key file is too large. Choose a file smaller than 64 KB.',
                );
                setPrivateKeyStatus(message, 'error');
                setFieldError('sshPrivateKey', message);
                return;
            }
            el('sshPrivateKey').value = await file.text();
            preparePrivateKey({ required: true, showFieldError: true });
            el('sshPrivateKey').focus();
        } catch (_) {
            const message = t(
                'workspace_ssh_private_key_file_read_failed',
                'The key file could not be read. Try pasting its contents instead.',
            );
            setPrivateKeyStatus(message, 'error');
            setFieldError('sshPrivateKey', message);
        } finally {
            // Reset the control so selecting the same corrected file triggers
            // another change event in every browser.
            if (fileInput) fileInput.value = '';
        }
    }

    async function api(url, options = {}) {
        const response = await window.authedFetch(url, {
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
            ...options,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw createRemoteApiError(response, payload);
        return payload;
    }

    function clearFieldErrors(formId) {
        const form = el(formId);
        if (!form) return;
        form.querySelectorAll('.remote-field-error').forEach((error) => error.remove());
        form.querySelectorAll('.projects-create-input-group.has-error').forEach((group) => {
            group.classList.remove('has-error');
        });
        form.querySelectorAll('.is-invalid').forEach((field) => {
            field.classList.remove('is-invalid');
            field.removeAttribute('aria-invalid');
            field.removeAttribute('aria-errormessage');
        });
    }

    function setFieldError(fieldId, message) {
        const field = el(fieldId);
        const group = field?.closest('.projects-create-input-group');
        if (!field || !group) return;
        field.classList.add('is-invalid');
        field.setAttribute('aria-invalid', 'true');
        group.classList.add('has-error');
        const error = document.createElement('p');
        error.className = 'remote-field-error';
        error.id = `${fieldId}Error`;
        error.textContent = message;
        group.appendChild(error);
        field.setAttribute('aria-errormessage', error.id);
        const clear = () => {
            field.classList.remove('is-invalid');
            field.removeAttribute('aria-invalid');
            field.removeAttribute('aria-errormessage');
            group.classList.remove('has-error');
            error.remove();
            field.removeEventListener('input', clear);
            field.removeEventListener('change', clear);
        };
        field.addEventListener('input', clear);
        field.addEventListener('change', clear);
    }

    function focusFirstError(formId) {
        const field = el(formId)?.querySelector('.is-invalid');
        if (!field) return;
        field.scrollIntoView({ behavior: 'smooth', block: 'center' });
        try { field.focus({ preventScroll: true }); } catch (_) { field.focus(); }
    }

    function presentApiError(error, kind) {
        const formId = kind === 'acp' ? 'acpProfileForm' : 'sshConnectionForm';
        clearFieldErrors(formId);
        for (const issue of error?.fieldErrors || []) {
            const fieldId = REMOTE_FIELD_IDS[issue.field]?.[kind];
            if (fieldId) {
                setFieldError(fieldId, issue.message);
                if (fieldId === 'sshPrivateKey') {
                    setPrivateKeyStatus(issue.message, 'error');
                }
            }
        }
        focusFirstError(formId);
        window.notifyError?.(
            error?.isRemoteApiError
                ? error.message
                : t('workspace_remote_request_failed', 'The request could not be completed. Please try again.'),
        );
    }

    function iconMarkup(value, fallbackKey) {
        const fallback = window.Icons?.[fallbackKey] || '';
        return window.IconPicker?.renderIconMarkup?.(value, {
            fallback,
            imageAlt: '',
        }) || fallback;
    }

    function statusText(connection) {
        const status = connection.status || {};
        if (!connection.enabled) return t('workspace_connections_status_disabled', 'Disabled');
        if (status.state === 'error') return t('workspace_ssh_error', 'Connection failed');
        if (status.state === 'connected') return t('workspace_ssh_connected', 'Connected');
        return t('workspace_ssh_not_tested', 'Not tested');
    }

    function statusClass(connection) {
        if (!connection.enabled) return 'status-idle';
        if (connection.status?.state === 'error') return 'status-error';
        return connection.status?.state === 'connected'
            ? 'status-connected'
            : 'status-idle';
    }

    function setPageVisibility(element, visible) {
        if (!element) return;
        element.style.display = visible ? '' : 'none';
        element.setAttribute('aria-hidden', String(!visible));
    }

    function renderList() {
        const remoteSection = el('connectionsRemoteSection');
        const sshSection = el('connectionsSshSection');
        const acpSection = el('connectionsAcpSection');
        if (remoteSection) {
            remoteSection.hidden = !state.allowSsh;
            remoteSection.setAttribute('aria-hidden', String(!state.allowSsh));
        }
        if (sshSection) sshSection.hidden = !state.allowSsh;
        if (acpSection) acpSection.hidden = !state.allowAcp;
        if (el('addSshConnectionBtn')) el('addSshConnectionBtn').hidden = !state.allowSsh;
        if (el('addAcpProfileBtn')) el('addAcpProfileBtn').hidden = !state.allowAcp;

        const sshGrid = el('sshConnectionsGrid');
        const acpGrid = el('acpProfilesGrid');
        if (!sshGrid || !acpGrid) return;

        // SSH cards use a two-row grid layout (see .remote-connection-card in
        // connections.css): title + status badge on the first row, the
        // truncated technical detail line underneath spanning the full width.
        sshGrid.innerHTML = state.ssh.length ? state.ssh.map((connection) => `
            <button type="button" class="connection-card remote-connection-card ${statusClass(connection) === 'status-connected' ? 'is-connected' : ''}" data-ssh-id="${escapeHtml(connection.id)}">
                <span class="connection-card-logo remote-connection-card-icon" aria-hidden="true">${iconMarkup(connection.icon, 'server')}</span>
                <span class="connection-card-title">${escapeHtml(connection.name)}</span>
                <span class="connection-card-status-badge ${statusClass(connection)}"><span class="connection-card-status-dot"></span><span class="connection-card-status-label">${escapeHtml(statusText(connection))}</span></span>
                <span class="connection-card-desc">${escapeHtml(connection.username)}@${escapeHtml(connection.host)}:${escapeHtml(connection.port)}</span>
            </button>
        `).join('') : `<div class="connections-empty-state"><p>${escapeHtml(t('workspace_ssh_empty', 'No SSH devices configured.'))}</p></div>`;

        acpGrid.innerHTML = state.acp.length ? state.acp.map((profile) => `
            <button type="button" class="connection-card remote-connection-card ${profile.enabled ? 'is-connected' : ''}" data-acp-id="${escapeHtml(profile.id)}">
                <span class="connection-card-logo remote-connection-card-icon" aria-hidden="true">${iconMarkup(profile.icon, 'terminal')}</span>
                <span class="connection-card-title">${escapeHtml(profile.name)}</span>
                <span class="connection-card-status-badge ${profile.enabled ? 'status-connected' : 'status-idle'}"><span class="connection-card-status-dot"></span><span class="connection-card-status-label">${escapeHtml(profile.enabled ? t('workspace_acp_available', 'Available in model picker') : t('workspace_connections_status_disabled', 'Disabled'))}</span></span>
                <span class="connection-card-desc">${escapeHtml([profile.executable, ...(profile.arguments || [])].join(' '))}</span>
            </button>
        `).join('') : `<div class="connections-empty-state"><p>${escapeHtml(t('workspace_acp_empty', 'No personal ACP agents configured.'))}</p></div>`;

        sshGrid.querySelectorAll('[data-ssh-id]').forEach((button) => {
            button.addEventListener('click', () => {
                openSshEditor(state.ssh.find((item) => item.id === button.dataset.sshId), button);
            });
        });
        acpGrid.querySelectorAll('[data-acp-id]').forEach((button) => {
            button.addEventListener('click', () => {
                openAcpEditor(state.acp.find((item) => item.id === button.dataset.acpId), button);
            });
        });
    }

    function ensureIconPickers() {
        if (!state.sshIconPicker && el('sshIconPicker') && window.IconPicker?.createIconPicker) {
            state.sshIconPicker = window.IconPicker.createIconPicker({
                value: 'server',
                presetKeys: SSH_ICON_KEYS,
                allowCustomSvg: false,
                allowImage: false,
            });
            el('sshIconPicker').replaceChildren(state.sshIconPicker.container);
        }
        if (!state.acpIconPicker && el('acpIconPicker') && window.IconPicker?.createIconPicker) {
            state.acpIconPicker = window.IconPicker.createIconPicker({
                value: 'opencode',
                presetKeys: ACP_ICON_KEYS,
                allowCustomSvg: true,
                allowImage: false,
            });
            el('acpIconPicker').replaceChildren(state.acpIconPicker.container);
        }
    }

    function showList({ restoreFocus = true } = {}) {
        const returnFocus = state.returnFocus;
        state.view = 'list';
        state.editing = null;
        setPageVisibility(el('connectionsWorkspace'), true);
        setPageVisibility(el('sshConnectionEditorPage'), false);
        setPageVisibility(el('acpConnectionEditorPage'), false);
        if (el('sshDeleteConfirmation')) el('sshDeleteConfirmation').hidden = true;
        if (el('acpDeleteConfirmation')) el('acpDeleteConfirmation').hidden = true;
        if (el('sshPrivateKey')) el('sshPrivateKey').value = '';
        setPrivateKeyStatus();
        setSshConnectionFeedback();
        state.returnFocus = null;
        if (restoreFocus) requestAnimationFrame(() => returnFocus?.focus?.());
    }

    function showEditor(kind, item, trigger) {
        state.view = kind;
        state.editing = item || null;
        state.returnFocus = trigger || document.activeElement;
        setPageVisibility(el('connectionsWorkspace'), false);
        setPageVisibility(el('sshConnectionEditorPage'), kind === 'ssh');
        setPageVisibility(el('acpConnectionEditorPage'), kind === 'acp');
        requestAnimationFrame(() => el(kind === 'ssh' ? 'sshName' : 'acpName')?.focus());
    }

    function openSshEditor(connection = null, trigger = null) {
        if (!state.allowSsh) return;
        ensureIconPickers();
        showEditor('ssh', connection, trigger);
        const isEdit = Boolean(connection);
        el('sshConnectionEditorTitle').textContent = isEdit
            ? t('workspace_ssh_edit_title', 'Edit SSH device')
            : t('workspace_ssh_create_title', 'Add SSH device');
        el('sshName').value = connection?.name || '';
        el('sshHost').value = connection?.host || '';
        el('sshPort').value = connection?.port || 22;
        el('sshUsername').value = connection?.username || '';
        el('sshWorkspace').value = connection?.workspace_root || '';
        el('sshHostKey').value = connection?.host_key || '';
        el('sshPrivateKey').value = '';
        el('sshPrivateKey').placeholder = isEdit
            ? t('workspace_ssh_keep_key', 'Leave empty to keep the stored key')
            : '-----BEGIN OPENSSH PRIVATE KEY-----';
        setPrivateKeyStatus();
        setSshConnectionFeedback();
        el('sshEnabled').checked = connection?.enabled !== false;
        el('sshDiscoveredKeys').replaceChildren();
        el('deleteSshBtn').hidden = !isEdit;
        el('sshDeleteConfirmation').hidden = true;
        state.sshIconPicker?.setValue(connection?.icon || 'server');
    }

    function openAcpEditor(profile = null, trigger = null) {
        if (!state.allowAcp) return;
        if (!state.ssh.length) {
            window.notifyError?.(t('workspace_acp_requires_ssh', 'Add an SSH device first.'));
            return;
        }
        ensureIconPickers();
        showEditor('acp', profile, trigger);
        const isEdit = Boolean(profile);
        el('acpConnectionEditorTitle').textContent = isEdit
            ? t('workspace_acp_edit_title', 'Edit ACP agent')
            : t('workspace_acp_create_title', 'Add ACP agent');
        el('acpSsh').innerHTML = state.ssh.map((connection) => `
            <option value="${escapeHtml(connection.id)}">${escapeHtml(connection.name)}</option>
        `).join('');
        el('acpName').value = profile?.name || 'OpenCode';
        el('acpSsh').value = profile?.ssh_connection_id || state.ssh[0].id;
        el('acpExecutable').value = profile?.executable || 'opencode';
        el('acpArguments').value = (profile?.arguments || ['acp']).join('\n');
        const selectedSsh = state.ssh.find((item) => item.id === el('acpSsh').value) || state.ssh[0];
        el('acpWorkspace').value = profile?.workspace_root || selectedSsh?.workspace_root || '';
        el('acpCwd').value = profile?.cwd || '.';
        el('acpAdditionalDirectories').value = (profile?.additional_directories || []).join('\n');
        el('acpMode').value = profile?.mode || '';
        el('acpPermission').value = profile?.permission_mode || 'ask';
        el('acpPermissionTimeout').value = profile?.permission_timeout_seconds || 300;
        el('acpPromptTimeout').value = profile?.prompt_timeout_seconds || 1800;
        el('acpEnabled').checked = profile?.enabled !== false;
        el('testAcpBtn').hidden = !isEdit;
        el('deleteAcpBtn').hidden = !isEdit;
        el('acpDeleteConfirmation').hidden = true;
        state.acpIconPicker?.setValue(profile?.icon || 'opencode');
    }

    async function discoverKeys() {
        const button = el('discoverSshKeyBtn');
        button.disabled = true;
        clearFieldErrors('sshConnectionForm');
        try {
            const result = await api('/api/v1/remote-connections/ssh/discover-host-keys', {
                method: 'POST',
                body: JSON.stringify({
                    host: el('sshHost').value.trim(),
                    port: Number(el('sshPort').value || 22),
                }),
            });
            el('sshDiscoveredKeys').innerHTML = (result.keys || []).map((key, index) => `
                <button type="button" class="connections-btn full-width ssh-host-key-option" data-key-index="${index}" title="${escapeHtml(key.key_type)} · ${escapeHtml(key.fingerprint)}">
                    <span>${escapeHtml(key.key_type)}</span><code>${escapeHtml(key.fingerprint)}</code>
                </button>
            `).join('');
            el('sshDiscoveredKeys').querySelectorAll('[data-key-index]').forEach((keyButton) => {
                keyButton.addEventListener('click', () => {
                    el('sshHostKey').value = result.keys[Number(keyButton.dataset.keyIndex)].host_key;
                });
            });
        } catch (error) {
            presentApiError(error, 'ssh');
        } finally {
            button.disabled = false;
        }
    }

    function sshPayload() {
        return {
            name: el('sshName').value.trim(),
            icon: state.sshIconPicker?.getValue() || 'server',
            host: el('sshHost').value.trim(),
            port: Number(el('sshPort').value || 22),
            username: el('sshUsername').value.trim(),
            host_key: el('sshHostKey').value.trim(),
            private_key: normalizeSshPrivateKey(el('sshPrivateKey').value) || null,
            workspace_root: el('sshWorkspace').value.trim(),
            connect_timeout_seconds: 15,
            enabled: el('sshEnabled').checked,
        };
    }

    async function saveSsh(event) {
        event.preventDefault();
        if (state.savingSsh) return;
        clearFieldErrors('sshConnectionForm');
        setSshConnectionFeedback();
        const requiresPrivateKey = !state.editing?.has_private_key;
        if (!preparePrivateKey({ required: requiresPrivateKey, showFieldError: true })) {
            focusFirstError('sshConnectionForm');
            return;
        }
        state.savingSsh = true;
        const saveButton = el('saveSshBtn');
        const form = el('sshConnectionForm');
        if (saveButton) saveButton.disabled = true;
        form?.setAttribute('aria-busy', 'true');
        const connection = state.editing;
        try {
            await api(
                connection
                    ? `/api/v1/remote-connections/ssh/${encodeURIComponent(connection.id)}`
                    : '/api/v1/remote-connections/ssh',
                {
                    method: connection ? 'PUT' : 'POST',
                    body: JSON.stringify(sshPayload()),
                },
            );
            await load();
            showList({ restoreFocus: false });
            window.notifySuccess?.(t('workspace_ssh_saved', 'SSH device saved.'));
        } catch (error) {
            presentApiError(error, 'ssh');
        } finally {
            state.savingSsh = false;
            if (saveButton) saveButton.disabled = false;
            form?.removeAttribute('aria-busy');
        }
    }

    async function testSsh() {
        const button = el('testSshBtn');
        clearFieldErrors('sshConnectionForm');
        setSshConnectionFeedback();
        const requiresPrivateKey = !state.editing?.has_private_key;
        if (!preparePrivateKey({ required: requiresPrivateKey, showFieldError: true })) {
            focusFirstError('sshConnectionForm');
            return;
        }
        button.disabled = true;
        try {
            const testUrl = state.editing
                ? `/api/v1/remote-connections/ssh/${encodeURIComponent(state.editing.id)}/test`
                : '/api/v1/remote-connections/ssh/test';
            // Always test the live form. The edit endpoint merges an omitted
            // private key with the saved credential without reusing stale host,
            // fingerprint, username, or workspace values.
            const result = await api(testUrl, {
                method: 'POST',
                body: JSON.stringify(sshPayload()),
            });
            const connected = result.state === 'connected';
            if (connected) {
                const message = t('workspace_ssh_test_success', 'SSH connection succeeded.');
                setSshConnectionFeedback(message, 'success');
                window.notifySuccess?.(message);
            } else {
                const message = localizedSshTestFailure(result.error_code);
                setSshConnectionFeedback(message, 'error');
                if (String(result.error_code || '').startsWith('ssh_private_key_')) {
                    setPrivateKeyStatus(message, 'error');
                    setFieldError('sshPrivateKey', message);
                    focusFirstError('sshConnectionForm');
                }
                window.notifyError?.(message);
            }
        } catch (error) {
            presentApiError(error, 'ssh');
        } finally {
            button.disabled = false;
        }
    }

    function lineValues(id) {
        return el(id).value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    }

    function acpPayload() {
        return {
            name: el('acpName').value.trim(),
            icon: state.acpIconPicker?.getValue() || 'terminal',
            ssh_connection_id: el('acpSsh').value,
            executable: el('acpExecutable').value.trim(),
            arguments: lineValues('acpArguments'),
            workspace_root: el('acpWorkspace').value.trim(),
            cwd: el('acpCwd').value.trim() || '.',
            additional_directories: lineValues('acpAdditionalDirectories'),
            mode: el('acpMode').value.trim() || null,
            permission_mode: el('acpPermission').value,
            permission_timeout_seconds: Number(el('acpPermissionTimeout').value || 300),
            prompt_timeout_seconds: Number(el('acpPromptTimeout').value || 1800),
            enabled: el('acpEnabled').checked,
        };
    }

    async function saveAcp(event) {
        event.preventDefault();
        if (state.savingAcp) return;
        clearFieldErrors('acpProfileForm');
        state.savingAcp = true;
        const saveButton = el('saveAcpBtn');
        const form = el('acpProfileForm');
        if (saveButton) saveButton.disabled = true;
        form?.setAttribute('aria-busy', 'true');
        const profile = state.editing;
        try {
            await api(
                profile
                    ? `/api/v1/remote-connections/acp/${encodeURIComponent(profile.id)}`
                    : '/api/v1/remote-connections/acp',
                {
                    method: profile ? 'PUT' : 'POST',
                    body: JSON.stringify(acpPayload()),
                },
            );
            await load();
            showList({ restoreFocus: false });
            // ACP saves create or update the private model row on the backend.
            // Bypass the shared user-model cache so the model picker reflects
            // that row immediately instead of waiting for a full page reload.
            await window.ModelSelectLoadModels?.({ forceRefresh: true });
            window.notifySuccess?.(t('workspace_acp_saved', 'ACP agent saved and added to the model picker.'));
        } catch (error) {
            presentApiError(error, 'acp');
        } finally {
            state.savingAcp = false;
            if (saveButton) saveButton.disabled = false;
            form?.removeAttribute('aria-busy');
        }
    }

    async function testAcp() {
        if (!state.editing) return;
        const button = el('testAcpBtn');
        button.disabled = true;
        try {
            // Testing must cover unsaved executable, argument, workspace, and
            // SSH-target edits rather than the profile currently in storage.
            const result = await api(
                `/api/v1/remote-connections/acp/${encodeURIComponent(state.editing.id)}/test`,
                {
                    method: 'POST',
                    body: JSON.stringify(acpPayload()),
                },
            );
            if (result.state === 'connected') {
                window.notifySuccess?.(t('workspace_acp_test_success', 'ACP handshake succeeded.'));
            } else {
                window.notifyError?.(t('workspace_acp_test_failed', 'ACP handshake failed.'));
            }
        } catch (error) {
            presentApiError(error, 'acp');
        } finally {
            button.disabled = false;
        }
    }

    async function deleteCurrent(kind) {
        const item = state.editing;
        if (!item) return;
        const button = el(kind === 'ssh' ? 'confirmDeleteSshBtn' : 'confirmDeleteAcpBtn');
        button.disabled = true;
        try {
            await api(`/api/v1/remote-connections/${kind}/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
            await load();
            showList({ restoreFocus: false });
            // Deleting an ACP profile also deletes its private model row, so
            // invalidate the same cached list before repainting the picker.
            await window.ModelSelectLoadModels?.({ forceRefresh: true });
        } catch (error) {
            presentApiError(error, kind);
        } finally {
            button.disabled = false;
        }
    }

    async function load() {
        if (state.loading) return;
        if (!state.allowSsh) {
            state.ssh = [];
            state.acp = [];
            renderList();
            return;
        }
        state.loading = true;
        try {
            const [sshConnections, acpProfiles] = await Promise.all([
                api('/api/v1/remote-connections/ssh'),
                state.allowAcp ? api('/api/v1/remote-connections/acp') : Promise.resolve([]),
            ]);
            state.ssh = sshConnections;
            state.acp = acpProfiles;
            renderList();
        } catch (error) {
            window.notifyError?.(t('workspace_remote_load_error', 'Failed to load your remote connections.'));
        } finally {
            state.loading = false;
        }
    }

    function setPolicy(policy = {}) {
        const allowSsh = policy.allow_ssh_connections === true;
        const allowAcp = allowSsh && policy.allow_custom_acp_connections === true;
        // A direct /workspace/connections route is rendered before the async
        // chat setup response is available. Remember whether that response
        // just enabled either connection family so the already-visible page
        // can load without requiring a second tab switch.
        const becameAvailable = (
            (!state.allowSsh && allowSsh)
            || (!state.allowAcp && allowAcp)
        );
        const changed = state.allowSsh !== allowSsh || state.allowAcp !== allowAcp;
        state.allowSsh = allowSsh;
        state.allowAcp = allowAcp;
        if (!allowSsh) state.ssh = [];
        if (!allowAcp) state.acp = [];
        if (
            changed
            && ((state.view === 'ssh' && !allowSsh) || (state.view === 'acp' && !allowAcp))
        ) {
            showList({ restoreFocus: false });
        }
        renderList();
        const routePath = String(window.location?.pathname || '').replace(/\/+$/, '');
        const connectionsPageActive = (
            window.WorkspaceManager?.getActiveTab?.() === 'connections'
            || routePath === '/workspace/connections'
        );
        if (state.initialized && becameAvailable && connectionsPageActive) {
            void load();
        }
    }

    function bindToggleCard(card) {
        const checkbox = card?.querySelector('input[type="checkbox"]');
        if (!card || !checkbox || card.dataset.remoteToggleBound === 'true') return;
        card.dataset.remoteToggleBound = 'true';
        card.addEventListener('click', (event) => {
            if (event.target.closest('input, label, button, a')) return;
            checkbox.click();
        });
    }

    function refreshTranslatedContent() {
        renderList();
        if (state.view === 'ssh') {
            el('sshConnectionEditorTitle').textContent = state.editing
                ? t('workspace_ssh_edit_title', 'Edit SSH device')
                : t('workspace_ssh_create_title', 'Add SSH device');
            if (el('sshPrivateKey')?.value) preparePrivateKey();
            setSshConnectionFeedback();
        } else if (state.view === 'acp') {
            el('acpConnectionEditorTitle').textContent = state.editing
                ? t('workspace_acp_edit_title', 'Edit ACP agent')
                : t('workspace_acp_create_title', 'Add ACP agent');
        }
    }

    function init() {
        if (state.initialized) return;
        state.initialized = true;
        ensureIconPickers();
        el('addSshConnectionBtn')?.addEventListener('click', (event) => openSshEditor(null, event.currentTarget));
        el('addAcpProfileBtn')?.addEventListener('click', (event) => openAcpEditor(null, event.currentTarget));
        el('cancelSshEditorBtn')?.addEventListener('click', () => showList());
        el('cancelAcpEditorBtn')?.addEventListener('click', () => showList());
        el('sshConnectionForm')?.addEventListener('submit', saveSsh);
        el('acpProfileForm')?.addEventListener('submit', saveAcp);
        el('discoverSshKeyBtn')?.addEventListener('click', discoverKeys);
        el('testSshBtn')?.addEventListener('click', testSsh);
        el('chooseSshPrivateKeyBtn')?.addEventListener('click', () => el('sshPrivateKeyFile')?.click());
        el('sshPrivateKeyFile')?.addEventListener('change', importSshPrivateKeyFile);
        el('sshPrivateKey')?.addEventListener('input', () => {
            setPrivateKeyStatus();
            setSshConnectionFeedback();
        });
        el('sshPrivateKey')?.addEventListener('paste', () => {
            // Wait until the browser has inserted clipboard contents, then
            // normalize and describe the result without requiring another click.
            setTimeout(() => preparePrivateKey(), 0);
        });
        el('sshPrivateKey')?.addEventListener('blur', () => {
            if (el('sshPrivateKey').value) preparePrivateKey();
        });
        el('testAcpBtn')?.addEventListener('click', testAcp);
        el('deleteSshBtn')?.addEventListener('click', () => { el('sshDeleteConfirmation').hidden = false; });
        el('deleteAcpBtn')?.addEventListener('click', () => { el('acpDeleteConfirmation').hidden = false; });
        el('cancelDeleteSshBtn')?.addEventListener('click', () => { el('sshDeleteConfirmation').hidden = true; });
        el('cancelDeleteAcpBtn')?.addEventListener('click', () => { el('acpDeleteConfirmation').hidden = true; });
        el('confirmDeleteSshBtn')?.addEventListener('click', () => deleteCurrent('ssh'));
        el('confirmDeleteAcpBtn')?.addEventListener('click', () => deleteCurrent('acp'));
        el('acpSsh')?.addEventListener('change', () => {
            if (state.editing) return;
            const connection = state.ssh.find((item) => item.id === el('acpSsh').value);
            if (connection) el('acpWorkspace').value = connection.workspace_root || '';
        });
        document.querySelectorAll('.remote-connection-toggle-card').forEach(bindToggleCard);
        window.addEventListener('i18n:updated', refreshTranslatedContent);
    }

    function show() {
        init();
        setPolicy({
            allow_ssh_connections: window.chatSetup?.allow_ssh_connections === true,
            allow_custom_acp_connections: window.chatSetup?.allow_custom_acp_connections === true,
        });
        showList({ restoreFocus: false });
        load();
    }

    window.RemoteConnectionsWorkspace = {
        init,
        show,
        load,
        setPolicy,
        openSshEditor,
        openAcpEditor,
        showList,
    };
    document.addEventListener('DOMContentLoaded', init);
})();
