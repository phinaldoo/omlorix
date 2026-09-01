// Terms of Service Management
const termsOfServiceState = {
    originalContent: '',
    currentContent: '',
    isDirty: false
};
const TERMS_OF_SERVICE_UNSAVED_GUARD_ID = 'admin-terms-of-service-unsaved';
let termsOfServiceUnsavedGuardRegistered = false;

const termsOfServiceT = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback !== undefined ? fallback : key;
};

const termsOfServiceFormat = (key, fallback, vars = {}) => {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    let text = termsOfServiceT(key, fallback);
    Object.entries(vars).forEach(([name, value]) => {
        text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), value);
    });
    return text;
};

function createTermsText(tagName, className, key, fallback) {
    const node = document.createElement(tagName);
    if (className) {
        node.className = className;
    }
    if (key) {
        node.setAttribute('data-i18n', key);
    }
    node.textContent = termsOfServiceT(key, fallback);
    return node;
}

function ensureTermsOfServiceLayout(page) {
    if (!page || page.dataset.layoutReady === 'true') {
        return;
    }

    page.replaceChildren();

    const header = document.createElement('div');
    header.className = 'page-header';

    const headerTop = document.createElement('div');
    headerTop.className = 'page-header-top';
    headerTop.appendChild(createTermsText('div', 'title', 'terms_of_service_page_title', 'Terms of Service'));

    const actions = document.createElement('div');
    actions.className = 'page-header-actions';
    actions.appendChild(window.createAdminPageActionButton({
        id: 'termsOfServiceBack',
        className: 'om-button border ghost',
        labelKey: 'btn_back',
        label: 'Back',
        icon: 'chevronLeft',
    }));
    actions.appendChild(window.createAdminPageActionButton({
        id: 'termsOfServiceSave',
        className: 'om-button border submit',
        labelKey: 'btn_save_changes',
        label: 'Save Changes',
        disabled: true,
    }));
    headerTop.appendChild(actions);

    header.appendChild(headerTop);
    header.appendChild(createTermsText('p', 'page-subtitle', 'terms_of_service_page_subtitle', 'Edit the markdown content of your terms of service. Enforcement is configured separately in Login settings.'));
    page.appendChild(header);

    const editorContainer = document.createElement('div');
    editorContainer.className = 'privacy-editor-container';

    const toolbar = document.createElement('div');
    toolbar.className = 'privacy-editor-toolbar';
    toolbar.appendChild(createTermsText('span', 'privacy-editor-toolbar-label', 'terms_of_service_editor_label', 'Markdown Editor'));

    const stats = document.createElement('span');
    stats.className = 'privacy-editor-toolbar-label';
    stats.id = 'termsOfServiceStats';
    stats.textContent = termsOfServiceFormat('terms_of_service_stats_chars', '{count} chars', { count: '0' });
    toolbar.appendChild(stats);

    const textarea = document.createElement('textarea');
    textarea.className = 'privacy-editor-textarea';
    textarea.id = 'termsOfServiceEditor';
    textarea.setAttribute('data-i18n-attr', 'placeholder:terms_of_service_editor_placeholder');
    textarea.setAttribute(
        'placeholder',
        termsOfServiceT('terms_of_service_editor_placeholder', '# Terms of Service\n\nEnter your terms here...')
    );
    textarea.spellcheck = false;

    editorContainer.append(toolbar, textarea);
    page.appendChild(editorContainer);
    page.dataset.layoutReady = 'true';

    document.getElementById('termsOfServiceBack').addEventListener('click', handleTermsOfServiceBack);
    document.getElementById('termsOfServiceSave').addEventListener('click', handleTermsOfServiceSave);
    document.getElementById('termsOfServiceEditor').addEventListener('input', handleTermsOfServiceInput);
}

function initTermsOfService() {
    const page = document.getElementById('page-terms-of-service');
    if (!page) {
        console.warn('Terms of service page container not found.');
        return;
    }

    registerTermsOfServiceUnsavedGuard();
    ensureTermsOfServiceLayout(page);

    loadTermsOfService();
}

async function loadTermsOfService() {
    const textarea = document.getElementById('termsOfServiceEditor');
    const saveBtn = document.getElementById('termsOfServiceSave');

    textarea.disabled = true;

    try {
        const response = await window.authedFetch('/api/v1/terms');

        if (!response.ok) throw new Error(termsOfServiceT('terms_of_service_load_error', 'Failed to load terms of service'));

        const data = await response.json();

        termsOfServiceState.originalContent = data.content || '';
        termsOfServiceState.currentContent = data.content || '';
        termsOfServiceState.isDirty = false;

        textarea.value = termsOfServiceState.currentContent;
        updateTermsOfServiceStats(termsOfServiceState.currentContent.length);

        saveBtn.disabled = true;
    } catch (error) {
        console.error('Error loading terms of service:', error);
        notifyError(termsOfServiceT('terms_of_service_load_error', 'Failed to load terms of service'));
    } finally {
        textarea.disabled = false;
    }
}

function handleTermsOfServiceInput(e) {
    const newContent = e.target.value;
    termsOfServiceState.currentContent = newContent;

    updateTermsOfServiceStats(newContent.length);

    const isChanged = newContent !== termsOfServiceState.originalContent;
    termsOfServiceState.isDirty = isChanged;

    const saveBtn = document.getElementById('termsOfServiceSave');
    saveBtn.disabled = !isChanged;
}

function updateTermsOfServiceStats(length) {
    const statsEl = document.getElementById('termsOfServiceStats');
    if (statsEl) {
        statsEl.textContent = termsOfServiceFormat('terms_of_service_stats_chars', '{count} chars', {
            count: length.toLocaleString(),
        });
    }
}

async function handleTermsOfServiceSave() {
    if (!termsOfServiceState.isDirty) return;

    const saveBtn = document.getElementById('termsOfServiceSave');
    const textarea = document.getElementById('termsOfServiceEditor');

    const originalText = saveBtn.innerHTML;
    saveBtn.innerHTML = `<span>${termsOfServiceT('terms_of_service_saving', 'Saving...')}</span>`;
    saveBtn.disabled = true;
    textarea.disabled = true;

    try {
        const response = await window.authedFetch('/api/v1/admin/terms', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content: termsOfServiceState.currentContent
            })
        });

        if (!response.ok) {
            let err = null;
            const rawText = await response.text().catch(() => '');
            const fallbackText = rawText.trim();

            if (fallbackText) {
                try {
                    err = JSON.parse(fallbackText);
                } catch (jsonError) {
                    err = null;
                }
            }

            const fallbackMessage = [
                `${response.status} ${response.statusText}`.trim(),
                fallbackText,
            ].filter(Boolean).join(': ');

            throw new Error(
                err?.detail
                || fallbackMessage
                || termsOfServiceT('terms_of_service_save_error', 'Failed to save terms of service')
            );
        }

        termsOfServiceState.originalContent = termsOfServiceState.currentContent;
        termsOfServiceState.isDirty = false;

        notifySuccess(termsOfServiceT('terms_of_service_save_success', 'Terms of service updated successfully'));

        window.activateAdminPage('security');

    } catch (error) {
        console.error('Error saving terms of service:', error);
        notifyError(error.message);
        saveBtn.disabled = false;
        textarea.disabled = false;
    } finally {
        saveBtn.innerHTML = originalText;
    }
}

function handleTermsOfServiceBack() {
    if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
        const prompted = window.unsavedChangesManager.confirmIfNeeded({
            id: TERMS_OF_SERVICE_UNSAVED_GUARD_ID,
            onConfirm: () => window.activateAdminPage('security'),
        });
        if (prompted) {
            return;
        }
    }
    window.activateAdminPage('security');
}

function registerTermsOfServiceUnsavedGuard() {
    if (termsOfServiceUnsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
        return;
    }
    window.unsavedChangesManager.register({
        id: TERMS_OF_SERVICE_UNSAVED_GUARD_ID,
        priority: 180,
        isActive: () => {
            const page = document.getElementById('page-terms-of-service');
            return Boolean(page && !page.hidden);
        },
        isDirty: () => Boolean(termsOfServiceState.isDirty),
        discard: () => {
            termsOfServiceState.isDirty = false;
        },
        getCopy: () => ({
            title: termsOfServiceT('terms_of_service_discard_title', 'Discard changes?'),
            subtitle: termsOfServiceT('terms_of_service_discard_desc', 'You have unsaved changes to the terms of service. Are you sure you want to leave without saving?'),
            confirmLabel: termsOfServiceT('terms_of_service_discard_btn', 'Discard changes'),
        }),
    });
    termsOfServiceUnsavedGuardRegistered = true;
}

window.initTermsOfService = initTermsOfService;
