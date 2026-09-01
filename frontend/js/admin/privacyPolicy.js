// Privacy Policy Management
const privacyPolicyState = {
    originalContent: '',
    currentContent: '',
    originalNoticeMode: 'none',
    noticeMode: 'none',
    originalNoticeMessageHtml: '',
    noticeMessageHtml: '',
    isDirty: false
};
const PRIVACY_POLICY_UNSAVED_GUARD_ID = 'admin-privacy-policy-unsaved';
let privacyPolicyUnsavedGuardRegistered = false;
let privacyPolicyI18nUpdatedHandlerRegistered = false;

const privacyPolicyT = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback !== undefined ? fallback : key;
};

const privacyPolicyFormat = (key, fallback, vars = {}) => {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    let text = privacyPolicyT(key, fallback);
    Object.entries(vars).forEach(([name, value]) => {
        text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), value);
    });
    return text;
};

const PRIVACY_NOTICE_MODES = ['none', 'modal'];
let privacyPolicyNoticeModeSelectMeta = null;

function createPrivacyPolicyText(tagName, className, key, fallback) {
    const node = document.createElement(tagName);
    if (className) {
        node.className = className;
    }
    if (key) {
        node.setAttribute('data-i18n', key);
    }
    node.textContent = privacyPolicyT(key, fallback);
    return node;
}

function createPrivacyPolicyHeader(page) {
    const header = document.createElement('div');
    header.className = 'page-header';

    const headerTop = document.createElement('div');
    headerTop.className = 'page-header-top';

    const title = createPrivacyPolicyText('div', 'title', 'privacy_policy_page_title', 'Privacy Policy');
    headerTop.appendChild(title);

    const actions = document.createElement('div');
    actions.className = 'page-header-actions';
    actions.appendChild(window.createAdminPageActionButton({
        id: 'privacyPolicyBack',
        className: 'om-button border ghost',
        labelKey: 'btn_back',
        label: 'Back',
        icon: 'chevronLeft',
    }));
    actions.appendChild(window.createAdminPageActionButton({
        id: 'privacyPolicySave',
        className: 'om-button border submit',
        labelKey: 'btn_save_changes',
        label: 'Save Changes',
        disabled: true,
    }));
    headerTop.appendChild(actions);

    header.appendChild(headerTop);
    header.appendChild(createPrivacyPolicyText('p', 'page-subtitle', 'privacy_policy_page_subtitle', 'Edit the markdown content of your privacy policy.'));
    page.appendChild(header);
}

function createPrivacyNoticeSection() {
    const section = document.createElement('section');
    section.className = 'settings-section settings-section--flush';

    const header = document.createElement('div');
    header.className = 'settings-section-header';
    header.appendChild(createPrivacyPolicyText('h3', 'settings-section-title', 'privacy_policy_notice_section_title', 'Policy Change Notice'));
    header.appendChild(createPrivacyPolicyText('p', 'settings-section-description', 'privacy_policy_notice_section_desc', 'Choose how users are informed after this privacy policy update. The notice can be shown as a dismissible modal.'));
    section.appendChild(header);

    const body = document.createElement('div');
    body.className = 'settings-section-body';

    const modeRow = document.createElement('div');
    modeRow.className = 'settings-row';
    const modeLeft = document.createElement('div');
    modeLeft.className = 'settings-row-left';
    modeLeft.appendChild(createPrivacyPolicyText('p', 'settings-row-title', 'privacy_policy_notice_mode_label', 'Notice behavior'));
    modeLeft.appendChild(createPrivacyPolicyText('p', 'settings-row-desc', 'privacy_policy_notice_mode_desc', 'Choose whether this update is silent or shown in a dismissible modal.'));
    const modeRight = document.createElement('div');
    modeRight.className = 'settings-row-right settings-row-right--min-260';
    const modeSelect = document.createElement('select');
    modeSelect.id = 'privacyPolicyNoticeMode';
    modeSelect.className = 'stats-select settings-select--full';
    modeSelect.setAttribute('aria-label', privacyPolicyT('privacy_policy_notice_mode_label', 'Notice behavior'));
    [
        ['none', 'privacy_policy_notice_mode_none', 'No notice'],
        ['modal', 'privacy_policy_notice_mode_modal', 'Dismissible modal'],
    ].forEach(([value, key, fallback]) => {
        const option = document.createElement('option');
        option.value = value;
        option.setAttribute('data-i18n', key);
        option.textContent = privacyPolicyT(key, fallback);
        modeSelect.appendChild(option);
    });

    if (typeof window.initializeAdminSingleSelect === 'function') {
        privacyPolicyNoticeModeSelectMeta = window.initializeAdminSingleSelect(modeSelect, {
            key: 'privacy-policy-notice-mode',
            placeholder: privacyPolicyT('admin_select_placeholder_single', 'Select an option...'),
        });
        if (privacyPolicyNoticeModeSelectMeta?.wrapper) {
            modeRight.appendChild(privacyPolicyNoticeModeSelectMeta.wrapper);
        } else {
            modeRight.appendChild(modeSelect);
        }
    } else {
        modeRight.appendChild(modeSelect);
    }
    modeRow.append(modeLeft, modeRight);
    body.appendChild(modeRow);

    const messageRow = document.createElement('div');
    messageRow.className = 'settings-row';
    messageRow.id = 'privacyPolicyNoticeMessageRow';
    const messageLeft = document.createElement('div');
    messageLeft.className = 'settings-row-left';
    messageLeft.appendChild(createPrivacyPolicyText('p', 'settings-row-title', 'privacy_policy_notice_message_label', 'Notice message (optional HTML)'));
    messageLeft.appendChild(createPrivacyPolicyText('p', 'settings-row-desc', 'privacy_policy_notice_message_desc', 'Optional message shown in the notice modal in addition to default text.'));
    const messageRight = document.createElement('div');
    messageRight.className = 'settings-row-right settings-row-right--min-320';
    const messageInput = document.createElement('textarea');
    messageInput.id = 'privacyPolicyNoticeMessage';
    messageInput.className = 'privacy-editor-textarea privacy-editor-textarea--short';
    messageInput.setAttribute('data-i18n-attr', 'placeholder:privacy_policy_notice_message_placeholder');
    messageInput.setAttribute(
        'placeholder',
        privacyPolicyT('privacy_policy_notice_message_placeholder', '<p>We updated our privacy policy...</p>')
    );
    messageRight.appendChild(messageInput);
    messageRow.append(messageLeft, messageRight);
    body.appendChild(messageRow);

    section.appendChild(body);
    return section;
}

function createPrivacyEditor() {
    const editorContainer = document.createElement('div');
    editorContainer.className = 'privacy-editor-container';

    const toolbar = document.createElement('div');
    toolbar.className = 'privacy-editor-toolbar';
    toolbar.appendChild(createPrivacyPolicyText('span', 'privacy-editor-toolbar-label', 'privacy_policy_editor_label', 'Markdown Editor'));

    const stats = document.createElement('span');
    stats.className = 'privacy-editor-toolbar-label';
    stats.id = 'privacyPolicyStats';
    stats.textContent = privacyPolicyFormat('privacy_policy_stats_chars', '{count} chars', { count: '0' });
    toolbar.appendChild(stats);

    const textarea = document.createElement('textarea');
    textarea.className = 'privacy-editor-textarea';
    textarea.id = 'privacyPolicyEditor';
    textarea.setAttribute('data-i18n-attr', 'placeholder:privacy_policy_editor_placeholder');
    textarea.setAttribute(
        'placeholder',
        privacyPolicyT('privacy_policy_editor_placeholder', '# Privacy Policy\n\nEnter your policy here...')
    );
    textarea.spellcheck = false;

    editorContainer.append(toolbar, textarea);
    return editorContainer;
}

function ensurePrivacyPolicyLayout(page) {
    if (!page || page.dataset.layoutReady === 'true') {
        return;
    }

    page.replaceChildren();
    createPrivacyPolicyHeader(page);
    page.appendChild(createPrivacyNoticeSection());
    page.appendChild(createPrivacyEditor());
    page.dataset.layoutReady = 'true';

    document.getElementById('privacyPolicyBack').addEventListener('click', handlePrivacyPolicyBack);
    document.getElementById('privacyPolicySave').addEventListener('click', handlePrivacyPolicySave);
    document.getElementById('privacyPolicyEditor').addEventListener('input', handlePrivacyPolicyInput);
    document.getElementById('privacyPolicyNoticeMode').addEventListener('change', handlePrivacyPolicyNoticeModeChange);
    document.getElementById('privacyPolicyNoticeMessage').addEventListener('input', handlePrivacyPolicyNoticeMessageInput);
    registerPrivacyPolicyI18nSync();
}

// Initialize Privacy Policy Page
function initPrivacyPolicy() {
    const page = document.getElementById('page-privacy-policy');
    registerPrivacyPolicyUnsavedGuard();

    ensurePrivacyPolicyLayout(page);
    loadPrivacyPolicy();
}

// Load Policy from Backend
async function loadPrivacyPolicy() {
    const textarea = document.getElementById('privacyPolicyEditor');
    const saveBtn = document.getElementById('privacyPolicySave');
    
    // Show loading state?
    textarea.disabled = true;
    
    try {
        const response = await window.authedFetch('/api/v1/privacy');
        
        if (!response.ok) throw new Error(privacyPolicyT('privacy_policy_load_error', 'Failed to load privacy policy'));
        
        const data = await response.json();
        const policyRes = await window.authedFetch('/api/v1/privacy/policy');
        const policyData = policyRes.ok ? await policyRes.json() : {};
        
        privacyPolicyState.originalContent = data.content || '';
        privacyPolicyState.currentContent = data.content || '';
        privacyPolicyState.originalNoticeMode = normalizePrivacyNoticeMode(
            policyData.stored_notice_mode || policyData.notice_mode
        );
        privacyPolicyState.noticeMode = privacyPolicyState.originalNoticeMode;
        privacyPolicyState.originalNoticeMessageHtml = typeof policyData.notice_message_html === 'string'
            ? policyData.notice_message_html
            : '';
        privacyPolicyState.noticeMessageHtml = privacyPolicyState.originalNoticeMessageHtml;
        privacyPolicyState.isDirty = false;
        
        textarea.value = privacyPolicyState.currentContent;
        const noticeModeSelect = document.getElementById('privacyPolicyNoticeMode');
        const noticeMessage = document.getElementById('privacyPolicyNoticeMessage');
        if (noticeModeSelect) {
            noticeModeSelect.value = privacyPolicyState.noticeMode;
            privacyPolicyNoticeModeSelectMeta?.syncFromSelect?.();
        }
        if (noticeMessage) {
            noticeMessage.value = privacyPolicyState.noticeMessageHtml;
        }
        updatePrivacyNoticeMessageVisibility();
        updatePrivacyStats(privacyPolicyState.currentContent.length);
        
        saveBtn.disabled = true;
    } catch (error) {
        console.error('Error loading privacy policy:', error);
        notifyError(privacyPolicyT('privacy_policy_load_error', 'Failed to load privacy policy'));
    } finally {
        textarea.disabled = false;
    }
}

// Handle Text Input
function handlePrivacyPolicyInput(e) {
    const newContent = e.target.value;
    privacyPolicyState.currentContent = newContent;
    
    updatePrivacyStats(newContent.length);
    
    // Check dirty state
    updatePrivacyDirtyState();
}

function handlePrivacyPolicyNoticeModeChange(e) {
    privacyPolicyState.noticeMode = normalizePrivacyNoticeMode(e.target.value);
    updatePrivacyNoticeMessageVisibility();
    updatePrivacyDirtyState();
}

function registerPrivacyPolicyI18nSync() {
    if (privacyPolicyI18nUpdatedHandlerRegistered) {
        return;
    }

    document.addEventListener('i18n:updated', handlePrivacyPolicyI18nUpdated);
    privacyPolicyI18nUpdatedHandlerRegistered = true;
}

function handlePrivacyPolicyI18nUpdated() {
    document.getElementById('privacyPolicyNoticeMode')?.setAttribute(
        'aria-label',
        privacyPolicyT('privacy_policy_notice_mode_label', 'Notice behavior')
    );
    privacyPolicyNoticeModeSelectMeta?.syncFromSelect?.();
}

function teardownPrivacyPolicyPage() {
    privacyPolicyNoticeModeSelectMeta?.wrapper?._closeMenu?.();
}

function handlePrivacyPolicyNoticeMessageInput(e) {
    privacyPolicyState.noticeMessageHtml = e.target.value;
    updatePrivacyDirtyState();
}

function normalizePrivacyNoticeMode(mode) {
    if (mode === 'banner') {
        return 'modal';
    }
    return PRIVACY_NOTICE_MODES.includes(mode) ? mode : 'none';
}

function updatePrivacyNoticeMessageVisibility() {
    const row = document.getElementById('privacyPolicyNoticeMessageRow');
    if (!row) return;
    row.style.display = privacyPolicyState.noticeMode === 'none' ? 'none' : '';
}

function updatePrivacyDirtyState() {
    const isChanged = (
        privacyPolicyState.currentContent !== privacyPolicyState.originalContent
        || privacyPolicyState.noticeMode !== privacyPolicyState.originalNoticeMode
        || privacyPolicyState.noticeMessageHtml !== privacyPolicyState.originalNoticeMessageHtml
    );
    privacyPolicyState.isDirty = isChanged;
    
    const saveBtn = document.getElementById('privacyPolicySave');
    saveBtn.disabled = !isChanged;
}

// Update Stats
function updatePrivacyStats(length) {
    const statsEl = document.getElementById('privacyPolicyStats');
    if (statsEl) {
        statsEl.textContent = privacyPolicyFormat('privacy_policy_stats_chars', '{count} chars', {
            count: length.toLocaleString(),
        });
    }
}

// Handle Save
async function handlePrivacyPolicySave() {
    if (!privacyPolicyState.isDirty) return;
    
    const saveBtn = document.getElementById('privacyPolicySave');
    const textarea = document.getElementById('privacyPolicyEditor');
    
    const originalText = saveBtn.innerHTML;
    saveBtn.innerHTML = `<span>${privacyPolicyT('privacy_policy_saving', 'Saving...')}</span>`;
    saveBtn.disabled = true;
    textarea.disabled = true;
    
    try {
        const response = await window.authedFetch('/api/v1/admin/privacy', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content: privacyPolicyState.currentContent,
                notice_mode: privacyPolicyState.noticeMode,
                notice_message_html: privacyPolicyState.noticeMessageHtml
            })
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || privacyPolicyT('privacy_policy_save_error', 'Failed to save privacy policy'));
        }
        
        // Update state
        privacyPolicyState.originalContent = privacyPolicyState.currentContent;
        privacyPolicyState.originalNoticeMode = privacyPolicyState.noticeMode;
        privacyPolicyState.originalNoticeMessageHtml = privacyPolicyState.noticeMessageHtml;
        privacyPolicyState.isDirty = false;
        
        notifySuccess(privacyPolicyT('privacy_policy_save_success', 'Privacy policy updated successfully'));
        
        // Go back to security page automatically
        window.activateAdminPage('security');
        
    } catch (error) {
        console.error('Error saving privacy policy:', error);
        notifyError(error.message);
        saveBtn.disabled = false;
        textarea.disabled = false;
    } finally {
        saveBtn.innerHTML = originalText;
    }
}

// Handle Back Navigation with Confirmation
function handlePrivacyPolicyBack() {
    if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
        const prompted = window.unsavedChangesManager.confirmIfNeeded({
            id: PRIVACY_POLICY_UNSAVED_GUARD_ID,
            onConfirm: () => window.activateAdminPage('security'),
        });
        if (prompted) {
            return;
        }
    }
    window.activateAdminPage('security');
}

function registerPrivacyPolicyUnsavedGuard() {
    if (privacyPolicyUnsavedGuardRegistered || typeof window.unsavedChangesManager?.register !== 'function') {
        return;
    }
    window.unsavedChangesManager.register({
        id: PRIVACY_POLICY_UNSAVED_GUARD_ID,
        priority: 180,
        isActive: () => {
            const page = document.getElementById('page-privacy-policy');
            return Boolean(page && !page.hidden);
        },
        isDirty: () => Boolean(privacyPolicyState.isDirty),
        discard: () => {
            privacyPolicyState.isDirty = false;
        },
        getCopy: () => ({
            title: privacyPolicyT('privacy_policy_discard_title', 'Discard changes?'),
            subtitle: privacyPolicyT('privacy_policy_discard_desc', 'You have unsaved changes to the privacy policy. Are you sure you want to leave without saving?'),
            confirmLabel: privacyPolicyT('privacy_policy_discard_btn', 'Discard changes'),
        }),
    });
    privacyPolicyUnsavedGuardRegistered = true;
}

// Export for use in main init
window.initPrivacyPolicy = initPrivacyPolicy;
