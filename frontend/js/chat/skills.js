/**
 * Skills Workspace Module
 * Manages user skills with CRUD operations using screen-based navigation
 */

// ============================================================================
// State Management
// ============================================================================

const SkillsState = {
    skills: [],
    isLoading: false,
    initialized: false,
    i18nListenerBound: false,
    searchQuery: '',
    activeSkillContext: null,
    detailReturnSkillId: null,
    deleteConfirmDefaultText: null,
    pendingShareId: null,
    pendingShareType: null,
    acceptModalInitialized: false,
    // Marketplace import state
    marketplaceImportData: null,
    marketplaceImportModalInitialized: false,
    marketplaceImportAwaitingChatSetup: false,
    // Sharing state
    sharingSkillId: null,
    shareMode: 'list',
    shareAction: 'link',
    shareStatus: null,
    currentShareType: 'live',
    currentCanEdit: false,
    inviteShareType: 'live',
    inviteCanEdit: false,
    publicUsers: [],
    publicUsersLoaded: false,
    publicUsersLoading: false,
    selectedUserIds: [],
    create: {
        selectedIconId: 'tool',
        selectedColorIndex: 0,
        isOpen: false,
    },
    edit: {
        selectedIconId: 'tool',
        selectedColorIndex: 0,
        isOpen: false,
    },
};

const WORKSPACE_SKILLS_CHANGED_EVENT = 'workspaceSkills:changed';

function notifyWorkspaceSkillsChanged(detail = {}) {
    if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
    window.dispatchEvent(new CustomEvent(WORKSPACE_SKILLS_CHANGED_EVENT, { detail }));
}

const workspaceSkillIconUtils = window.WorkspaceIconUtils;
const SKILL_ICONS = workspaceSkillIconUtils.getWorkspaceIconOptions();
const SKILL_DEFAULT_ICON_ID = 'tool';
const SKILL_ICON_COLORS = workspaceSkillIconUtils.WORKSPACE_ICON_COLORS;

// ============================================================================
// API Helpers
// ============================================================================

const SkillsAPI = {
    async request(input, init) {
        if (typeof window !== 'undefined' && typeof window.authedFetch === 'function') {
            return window.authedFetch(input, init);
        }
        return fetch(input, init);
    },

    async fetchSkills() {
        const response = await this.request('/api/v1/skills', {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) {
            throw new Error(skillsTranslate('workspace_skills_load_error_notification', 'Failed to load skills'));
        }
        return response.json();
    },

    async createSkill(name, description, content, icon, compatibility, license, metadata) {
        const payload = { name, description, content, icon };
        if (compatibility) payload.compatibility = compatibility;
        if (license) payload.license = license;
        if (metadata) payload.metadata = metadata;
        
        const response = await this.request('/api/v1/skills', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_create_error', 'Failed to create skill')));
        }
        return response.json();
    },

    async updateSkill(skillId, data) {
        const response = await this.request(`/api/v1/skills/${skillId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_edit_error', 'Failed to update skill')));
        }
        return response.json();
    },

    async deleteSkill(skillId) {
        const response = await this.request(`/api/v1/skills/${skillId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) {
            throw new Error(skillsTranslate('workspace_skills_delete_error', 'Failed to delete skill'));
        }
        return response.json();
    },

    async uploadSkillFiles(skillId, folderType, files) {
        const formData = new FormData();
        for (const file of files) {
            formData.append('files', file);
        }
        const response = await this.request(`/api/v1/skills/${skillId}/files/${folderType}`, {
            method: 'POST',
            headers: { 'Content-Type': null },
            credentials: 'include',
            body: formData,
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_files_upload_error', 'Failed to upload files')));
        }
        return response.json();
    },

    async deleteSkillFile(skillId, folderType, filename) {
        const response = await this.request(`/api/v1/skills/${skillId}/files/${folderType}/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
            credentials: 'include',
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_files_delete_error', 'Failed to delete file')));
        }
        return response.json();
    },

    // Sharing APIs
    async shareSkill(skillId, shareType = 'live') {
        const response = await this.request('/api/v1/skills/share', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ skill_id: skillId, share_type: shareType }),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_share_error_create', 'Failed to share skill')));
        }
        return response.json();
    },

    async getShareStatus(skillId) {
        const response = await this.request(`/api/v1/skills/share/status?skill_id=${encodeURIComponent(skillId)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_share_status_error', 'Failed to get share status')));
        }
        return response.json();
    },

    async deleteShare(skillId, shareType = null) {
        const body = { skill_id: skillId };
        if (shareType) body.share_type = shareType;
        const response = await this.request('/api/v1/skills/share/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_share_remove_error', 'Failed to remove sharing')));
        }
        return response.json();
    },

    async fetchPublicUsers() {
        const users = [];
        const seenUserIds = new Set();
        let offset = 0;
        const limit = 100;
        while (true) {
            const response = await this.request(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_share_invite_load_users_error', 'Failed to load users')));
            }
            const page = await response.json();
            const pageUsers = Array.isArray(page) ? page : [];
            pageUsers.forEach((user) => {
                const userId = String(user?.id || '').trim();
                if (!userId || seenUserIds.has(userId)) return;
                seenUserIds.add(userId);
                users.push(user);
            });
            const hasMore = String(response.headers.get('X-Has-More') || '').toLowerCase() === 'true';
            if (!hasMore || pageUsers.length === 0) break;
            offset += pageUsers.length;
        }
        return users;
    },

    async inviteUsersToSkill(skillId, userIds, shareType = 'live') {
        const response = await this.request('/api/v1/skills/invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ item_id: skillId, user_ids: userIds, share_type: shareType }),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_share_invite_error', 'Failed to send invitations')));
        }
        return response.json();
    },

    async getSharedSkillPreview(shareId) {
        const response = await this.request(`/api/v1/skills/shared/${encodeURIComponent(shareId)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) {
            let detail = skillsTranslate('workspace_skills_accept_error_not_found', 'Shared skill not found');
            try {
                const errorBody = await response.json();
                if (errorBody?.detail) detail = errorBody.detail;
            } catch (_) { /* ignore parse errors */ }
            const error = new Error(detail);
            error.status = response.status;
            throw error;
        }
        return response.json();
    },

    async acceptSharedSkill(shareId) {
        const response = await this.request(`/api/v1/skills/shared/${encodeURIComponent(shareId)}/accept`, {
            method: 'POST',
            credentials: 'include',
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_accept_error_accept', 'Failed to accept shared skill')));
        }
        return response.json();
    },

    async cloneSkill(shareId) {
        const response = await this.request(`/api/v1/skills/clone/${encodeURIComponent(shareId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_accept_error_clone', 'Failed to clone skill')));
        }
        return response.json();
    },

    async unsubscribeFromSkill(skillId) {
        const response = await this.request(`/api/v1/skills/shared/${encodeURIComponent(skillId)}/unsubscribe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(skillsTranslateBackendDetail(errorData.detail, skillsTranslate('workspace_skills_remove_unsubscribe_error', 'Failed to unsubscribe from skill')));
        }
        return response.json();
    },
};

function skillHasExistingShareState(skill) {
    if (!skill) return false;
    return Boolean(skill.clone_share_id || skill.live_share_id || skill.collaborate_share_id || Number(skill.subscriber_count || 0) > 0);
}

function canManageSkillSharing(skill) {
    const sharingEnabled = typeof window === 'undefined' || window.allowSkillShareFeature !== false;
    return sharingEnabled || skillHasExistingShareState(skill);
}

function canEditSkill(skill) {
    if (!skill || skill.is_admin_skill === true) return false;
    if (skill.is_subscribed !== true) return true;
    return skill.share_type === 'collaborate';
}

// ============================================================================
// DOM Elements
// ============================================================================

const SkillsDOM = {
    get skillsContent() { return document.getElementById('skillsContent'); },
    get skillsContentCreate() { return document.getElementById('skillsContentCreate'); },
    get skillsContentView() { return document.getElementById('skillsContentView'); },
    get skillsContentEdit() { return document.getElementById('skillsContentEdit'); },
    get skillsGrid() { return document.getElementById('skillsGrid'); },
    get skillsResultsPanel() { return document.getElementById('skillsResultsPanel'); },
    get skillsSearchInput() { return document.getElementById('skillsSearchInput'); },
    get skillsSearchClear() { return document.getElementById('skillsSearchClear'); },
    get addSkillBtn() { return document.getElementById('workspaceAddSkillBtn'); },
    // Share modal
    get skillShareOverlay() { return document.getElementById('skillsShareOverlay'); },
    get skillShareName() { return document.getElementById('skillShareName'); },
    get skillShareLinkInput() { return document.getElementById('skillShareLinkInput'); },
    get skillShareCopyBtn() { return document.getElementById('skillShareCopyBtn'); },
    get skillShareCloseBtn() { return document.getElementById('skillShareCloseBtn'); },
    get skillShareStopBtn() { return document.getElementById('skillShareStopBtn'); },
    // Accept shared skill modal
    get skillAcceptOverlay() { return document.getElementById('skillAcceptOverlay'); },
    get skillAcceptIcon() { return document.getElementById('skillAcceptIcon'); },
    get skillAcceptTitle() { return document.getElementById('skillAcceptTitle'); },
    get skillAcceptDescription() { return document.getElementById('skillAcceptDescription'); },
    get skillAcceptPreview() { return document.getElementById('skillAcceptPreview'); },
    get skillAcceptCancelBtn() { return document.getElementById('skillAcceptCancelBtn'); },
    get skillAcceptConfirmBtn() { return document.getElementById('skillAcceptConfirmBtn'); },
    get marketplaceImportOverlay() { return document.getElementById('marketplaceImportOverlay'); },
    // Create form
    get skillNameInput() { return document.getElementById('skillNameInput'); },
    get skillDescriptionInput() { return document.getElementById('skillDescriptionInput'); },
    get skillContentInput() { return document.getElementById('skillContentInput'); },
    get skillCompatibilityInput() { return document.getElementById('skillCompatibilityInput'); },
    get skillLicenseInput() { return document.getElementById('skillLicenseInput'); },
    get skillMetadataInput() { return document.getElementById('skillMetadataInput'); },
    get skillIconPicker() { return document.getElementById('skillIconPicker'); },
    get skillIconButton() { return document.getElementById('skillIconButton'); },
    // The shared compact picker renders its preview directly in the trigger.
    get skillIconPreview() { return document.getElementById('skillIconButton'); },
    get skillIconDropdown() { return document.getElementById('skillIconDropdown'); },
    get skillIconGrid() { return document.getElementById('skillIconGrid'); },
    get skillColorRow() { return document.getElementById('skillColorRow'); },
    get skillIconSaveBtn() { return document.getElementById('skillIconSaveBtn'); },
    get skillIconCancelBtn() { return document.getElementById('skillIconCancelBtn'); },
    get createSkillCancelBtn() { return document.getElementById('createSkillCancelBtn'); },
    get confirmCreateSkillBtn() { return document.getElementById('confirmCreateSkillBtn'); },
    get skillNameError() { return document.getElementById('skillNameError'); },
    get skillDescriptionError() { return document.getElementById('skillDescriptionError'); },
    get skillContentError() { return document.getElementById('skillContentError'); },
    get skillMetadataError() { return document.getElementById('skillMetadataError'); },
    // Managed skill read-only detail view
    get skillViewName() { return document.getElementById('skillViewName'); },
    get skillViewDescription() { return document.getElementById('skillViewDescription'); },
    get skillViewIcon() { return document.getElementById('skillViewIcon'); },
    get skillViewManagedBadgeIcon() { return document.getElementById('skillViewManagedBadgeIcon'); },
    get skillViewManagedNoticeIcon() { return document.getElementById('skillViewManagedNoticeIcon'); },
    get skillViewContent() { return document.getElementById('skillViewContent'); },
    get skillViewDetailsSection() { return document.getElementById('skillViewDetailsSection'); },
    get skillViewAuthorRow() { return document.getElementById('skillViewAuthorRow'); },
    get skillViewAuthor() { return document.getElementById('skillViewAuthor'); },
    get skillViewCompatibilityRow() { return document.getElementById('skillViewCompatibilityRow'); },
    get skillViewCompatibility() { return document.getElementById('skillViewCompatibility'); },
    get skillViewLicenseRow() { return document.getElementById('skillViewLicenseRow'); },
    get skillViewLicense() { return document.getElementById('skillViewLicense'); },
    get skillViewMetadataRow() { return document.getElementById('skillViewMetadataRow'); },
    get skillViewMetadata() { return document.getElementById('skillViewMetadata'); },
    get skillViewResourcesSection() { return document.getElementById('skillViewResourcesSection'); },
    get skillViewScriptsSection() { return document.getElementById('skillViewScriptsSection'); },
    get skillViewScriptsList() { return document.getElementById('skillViewScriptsList'); },
    get skillViewReferencesSection() { return document.getElementById('skillViewReferencesSection'); },
    get skillViewReferencesList() { return document.getElementById('skillViewReferencesList'); },
    get skillViewAssetsSection() { return document.getElementById('skillViewAssetsSection'); },
    get skillViewAssetsList() { return document.getElementById('skillViewAssetsList'); },
    get skillViewBackBtn() { return document.getElementById('skillViewBackBtn'); },
    // Edit form
    get skillEditTitleInput() { return document.getElementById('skillEditTitleInput'); },
    get skillEditContentInput() { return document.getElementById('skillEditContentInput'); },
    get skillEditCompatibilityInput() { return document.getElementById('skillEditCompatibilityInput'); },
    get skillEditLicenseInput() { return document.getElementById('skillEditLicenseInput'); },
    get skillEditMetadataInput() { return document.getElementById('skillEditMetadataInput'); },
    get skillEditIconPicker() { return document.getElementById('skillEditIconPicker'); },
    get skillEditIconButton() { return document.getElementById('skillEditIconButton'); },
    get skillEditIconPreview() { return document.getElementById('skillEditIconButton'); },
    get skillEditIconDropdown() { return document.getElementById('skillEditIconDropdown'); },
    get skillEditIconGrid() { return document.getElementById('skillEditIconGrid'); },
    get skillEditColorRow() { return document.getElementById('skillEditColorRow'); },
    get skillEditIconSaveBtn() { return document.getElementById('skillEditIconSaveBtn'); },
    get skillEditIconCancelBtn() { return document.getElementById('skillEditIconCancelBtn'); },
    get editSkillCancelBtn() { return document.getElementById('editSkillCancelBtn'); },
    get saveSkillChangesBtn() { return document.getElementById('saveSkillChangesBtn'); },
    get skillEditTitleError() { return document.getElementById('skillEditTitleError'); },
    get skillEditContentError() { return document.getElementById('skillEditContentError'); },
    get skillEditMetadataError() { return document.getElementById('skillEditMetadataError'); },
    // Delete overlay
    get skillsDeleteOverlay() { return document.getElementById('skillsDeleteOverlay'); },
    get skillsDeleteName() { return document.getElementById('skillsDeleteName'); },
    get skillsDeleteCancelBtn() { return document.getElementById('skillsDeleteCancelBtn'); },
    get skillsDeleteConfirmBtn() { return document.getElementById('skillsDeleteConfirmBtn'); },
    get skillsDeleteConfirmText() { return document.getElementById('skillsDeleteConfirmText'); },
    // Import skill modal
    get importSkillBtn() { return document.getElementById('workspaceImportSkillBtn'); },
    get skillImportOverlay() { return document.getElementById('skillImportOverlay'); },
    get skillImportCloseBtn() { return document.getElementById('skillImportCloseBtn'); },
    get skillImportCancelBtn() { return document.getElementById('skillImportCancelBtn'); },
    get skillImportConfirmBtn() { return document.getElementById('skillImportConfirmBtn'); },
    get skillImportConfirmText() { return document.getElementById('skillImportConfirmText'); },
    get skillImportTabFile() { return document.getElementById('skillImportTabFile'); },
    get skillImportTabPaste() { return document.getElementById('skillImportTabPaste'); },
    get skillImportPanelFile() { return document.getElementById('skillImportPanelFile'); },
    get skillImportPanelPaste() { return document.getElementById('skillImportPanelPaste'); },
    get skillImportDropzone() { return document.getElementById('skillImportDropzone'); },
    get skillImportFileInput() { return document.getElementById('skillImportFileInput'); },
    get skillImportBrowseBtn() { return document.getElementById('skillImportBrowseBtn'); },
    get skillImportDropzoneContent() { return document.getElementById('skillImportDropzoneContent'); },
    get skillImportFileSelected() { return document.getElementById('skillImportFileSelected'); },
    get skillImportFileName() { return document.getElementById('skillImportFileName'); },
    get skillImportFileSize() { return document.getElementById('skillImportFileSize'); },
    get skillImportFileList() { return document.getElementById('skillImportFileList'); },
    get skillImportFileRemove() { return document.getElementById('skillImportFileRemove'); },
    get skillImportPasteInput() { return document.getElementById('skillImportPasteInput'); },
    get skillImportPasteClear() { return document.getElementById('skillImportPasteClear'); },
    get skillImportFeedback() { return document.getElementById('skillImportFeedback'); },
    get skillImportError() { return document.getElementById('skillImportError'); },
    get skillImportErrorMessage() { return document.getElementById('skillImportErrorMessage'); },
    get skillImportPreview() { return document.getElementById('skillImportPreview'); },
    get skillImportPreviewName() { return document.getElementById('skillImportPreviewName'); },
    get skillImportPreviewDescription() { return document.getElementById('skillImportPreviewDescription'); },
    get skillImportPreviewMeta() { return document.getElementById('skillImportPreviewMeta'); },
    get skillImportPreviewBody() { return document.getElementById('skillImportPreviewBody'); },
    get skillImportPreviewBodyText() { return document.getElementById('skillImportPreviewBodyText'); },
    // Edit form - file sections
    get skillEditScriptsList() { return document.getElementById('skillEditScriptsList'); },
    get skillEditScriptsInput() { return document.getElementById('skillEditScriptsInput'); },
    get skillEditScriptsBtn() { return document.getElementById('skillEditScriptsBtn'); },
    get skillEditReferencesList() { return document.getElementById('skillEditReferencesList'); },
    get skillEditReferencesInput() { return document.getElementById('skillEditReferencesInput'); },
    get skillEditReferencesBtn() { return document.getElementById('skillEditReferencesBtn'); },
    get skillEditAssetsList() { return document.getElementById('skillEditAssetsList'); },
    get skillEditAssetsInput() { return document.getElementById('skillEditAssetsInput'); },
    get skillEditAssetsBtn() { return document.getElementById('skillEditAssetsBtn'); },
};

const SkillCreateIconPicker = workspaceSkillIconUtils.createWorkspaceIconPicker({
    state: SkillsState.create,
    refs: () => ({
        picker: SkillsDOM.skillIconPicker,
        trigger: SkillsDOM.skillIconButton,
        preview: SkillsDOM.skillIconPreview,
        dropdown: SkillsDOM.skillIconDropdown,
        svgGrid: SkillsDOM.skillIconGrid,
        colorGrid: SkillsDOM.skillColorRow,
        saveButton: SkillsDOM.skillIconSaveBtn,
        cancelButton: SkillsDOM.skillIconCancelBtn,
    }),
    iconOptions: SKILL_ICONS,
    colors: SKILL_ICON_COLORS,
    defaultIconId: SKILL_DEFAULT_ICON_ID,
    defaultColor: SKILL_ICON_COLORS[0].hex,
    translate: skillsTranslate,
    variant: 'svg-select',
});

const SkillEditIconPicker = workspaceSkillIconUtils.createWorkspaceIconPicker({
    state: SkillsState.edit,
    refs: () => ({
        picker: SkillsDOM.skillEditIconPicker,
        trigger: SkillsDOM.skillEditIconButton,
        preview: SkillsDOM.skillEditIconPreview,
        dropdown: SkillsDOM.skillEditIconDropdown,
        svgGrid: SkillsDOM.skillEditIconGrid,
        colorGrid: SkillsDOM.skillEditColorRow,
        saveButton: SkillsDOM.skillEditIconSaveBtn,
        cancelButton: SkillsDOM.skillEditIconCancelBtn,
    }),
    iconOptions: SKILL_ICONS,
    colors: SKILL_ICON_COLORS,
    defaultIconId: SKILL_DEFAULT_ICON_ID,
    defaultColor: SKILL_ICON_COLORS[0].hex,
    translate: skillsTranslate,
    variant: 'svg-select',
});

// ============================================================================
// Utility Functions
// ============================================================================

const SkillsUtils = {
    escapeHtml(text = '') {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    showInputError(inputEl, errorEl, message) {
        if (window.FormValidation?.showInputError) {
            return window.FormValidation.showInputError(inputEl, errorEl, message);
        }
        if (!inputEl) return false;
        inputEl.classList?.add('input-error');
        inputEl.setAttribute?.('aria-invalid', 'true');
        inputEl.closest?.('.projects-create-input-group')?.classList?.add('has-error');
        if (errorEl) {
            errorEl.textContent = message;
            errorEl.classList?.add('visible');
            errorEl.hidden = false;
            errorEl.removeAttribute?.('hidden');
            errorEl.setAttribute?.('aria-hidden', 'false');
        }
        inputEl.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
        inputEl.focus?.();
        return true;
    },

    clearInputError(inputEl, errorEl) {
        if (window.FormValidation?.clearInputError) {
            return window.FormValidation.clearInputError(inputEl, errorEl);
        }
        if (!inputEl) return false;
        inputEl.classList?.remove('input-error');
        inputEl.setAttribute?.('aria-invalid', 'false');
        inputEl.closest?.('.projects-create-input-group')?.classList?.remove('has-error');
        if (errorEl) {
            errorEl.classList?.remove('visible');
            errorEl.hidden = true;
            errorEl.setAttribute?.('hidden', '');
            errorEl.setAttribute?.('aria-hidden', 'true');
        }
        return true;
    },

    parseMetadata(rawValue = '') {
        if (!rawValue) return { value: null, errorKey: null };
        let value;
        try {
            value = JSON.parse(rawValue);
        } catch (_) {
            return { value: null, errorKey: 'workspace_skills_validation_metadata_invalid' };
        }
        if (value === null || typeof value !== 'object' || Array.isArray(value)) {
            return { value: null, errorKey: 'workspace_skills_validation_metadata_object' };
        }
        return { value, errorKey: null };
    },

    showMetadataError(inputEl, errorEl, errorKey) {
        const fallback = errorKey === 'workspace_skills_validation_metadata_object'
            ? 'Metadata must be a JSON object'
            : 'Invalid JSON in metadata field';
        const message = skillsTranslate(errorKey, fallback);
        errorEl?.setAttribute('data-i18n', errorKey);
        this.showInputError(inputEl, errorEl, message);
        if (typeof showNotification === 'function') showNotification(message, 'error');
    },

    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    },

    getFileIcon(filename) {
        const ext = filename.split('.').pop()?.toLowerCase() || '';
        const codeExts = ['js', 'ts', 'py', 'sh', 'bash', 'ps1', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'php'];
        const docExts = ['md', 'txt', 'doc', 'docx', 'pdf', 'rtf'];
        const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'ico', 'bmp'];
        const dataExts = ['json', 'xml', 'yaml', 'yml', 'csv', 'toml'];
        const sharedIcons = globalThis.Icons || {};
        const icons = {
            "code": sharedIcons.code,
            "document": sharedIcons.attachment_file,
            "image": sharedIcons.image_gen,
            "data": sharedIcons.attachment_file,
            "file": sharedIcons.attachment_file,
        };
        
        if (codeExts.includes(ext)) {
            return icons.code || '';
        } else if (docExts.includes(ext)) {
            return icons.document || '';
        } else if (imageExts.includes(ext)) {
            return icons.image || '';
        } else if (dataExts.includes(ext)) {
            return icons.data || '';
        }
        return icons.file || '';
    },

    parseIcon(iconData) {
        const fallbackColor = SKILL_ICON_COLORS[0].hex;
        return workspaceSkillIconUtils.resolveWorkspaceStoredIcon(iconData, {
            iconOptions: SKILL_ICONS,
            defaultIconId: SKILL_DEFAULT_ICON_ID,
            defaultColor: fallbackColor,
        });
    },

    buildIconJson(mode = 'create') {
        const picker = mode === 'edit' ? SkillEditIconPicker : SkillCreateIconPicker;
        return picker?.serialize?.({ includeColor: true })
            || JSON.stringify({ preset: SKILL_DEFAULT_ICON_ID, color: SKILL_ICON_COLORS[0].hex });
    },

    normalizeSearchQuery(value = '') {
        return String(value).trim().toLowerCase();
    },
};

function skillsTranslate(key, fallback, vars) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    if (!vars || typeof vars !== 'object') {
        return fallback;
    }
    return String(fallback).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function skillsTranslateBackendDetail(detail, fallback) {
    let translationKey = '';
    switch (String(detail || '').trim()) {
        case 'Skills feature disabled for your group':
        case "Skills are disabled by your group's data controls.":
            translationKey = 'workspace_skills_feature_disabled';
            break;
        case 'Skill sharing is disabled for your group':
            translationKey = 'workspace_skills_share_error_disabled';
            break;
        case 'Shared skill not found':
            translationKey = 'workspace_skills_accept_error_not_found';
            break;
        case 'You cannot subscribe to your own skill':
        case 'You cannot open your own shared skill':
            translationKey = 'workspace_skills_accept_owner_error';
            break;
        case 'You already added this shared skill':
            translationKey = 'workspace_skills_accept_error_already_added';
            break;
        case 'No markdown content provided':
            translationKey = 'workspace_skills_import_error_no_markdown';
            break;
        case 'Skill import failed due to an internal error.':
            translationKey = 'workspace_skills_import_error_try_again';
            break;
        default:
            break;
    }
    if (translationKey) {
        return skillsTranslate(translationKey, fallback ?? detail ?? '');
    }
    if (typeof window !== 'undefined' && typeof window.translateBackendDetail === 'function') {
        return window.translateBackendDetail(detail, fallback);
    }
    return fallback ?? detail ?? '';
}

function skillsPlural(count, singularKey, singularFallback, pluralKey, pluralFallback, vars = {}) {
    let pluralCategory = count === 1 ? 'one' : 'other';
    try {
        const locale = document.documentElement?.lang || navigator.language || 'en';
        pluralCategory = new Intl.PluralRules(locale).select(Math.abs(Number(count) || 0));
    } catch (_) {
        // Preserve the existing one/other behavior where Intl is unavailable.
    }

    if (pluralCategory === 'one') {
        return skillsTranslate(singularKey, singularFallback, { ...vars, count });
    }

    // Locales such as Russian require few/many forms. Use one when it exists,
    // while allowing every other locale to fall back to its translated
    // `_other` value instead of an English category fallback.
    if (
        pluralCategory !== 'other'
        && pluralKey.endsWith('_other')
        && typeof window !== 'undefined'
        && typeof window.getTranslation === 'function'
    ) {
        const categoryKey = pluralKey.replace(/_other$/, `_${pluralCategory}`);
        const categoryValue = window.getTranslation(categoryKey);
        if (categoryValue !== categoryKey) {
            return skillsTranslate(categoryKey, categoryValue, { ...vars, count });
        }
    }

    return skillsTranslate(pluralKey, pluralFallback, { ...vars, count });
}

function skillsButtonContent(iconSvg, label) {
    return `${iconSvg}${SkillsUtils.escapeHtml(label)}`;
}

function getSkillShareTypeLabel(shareType) {
    switch (shareType) {
        case 'clone':
            return skillsTranslate('us_shared_items_share_type_clone', 'Clone');
        case 'live':
            return skillsTranslate('us_shared_items_share_type_live', 'Live view');
        case 'collaborate':
            return skillsTranslate('us_shared_items_share_type_collaborate', 'Collaborate');
        default:
            return shareType || '';
    }
}

function getSkillShareTypeDescription(shareType) {
    switch (shareType) {
        case 'clone':
            return skillsTranslate(
                'workspace_skills_share_type_desc_clone',
                'Recipients will get their own copy of this skill. They can edit and delete their copy freely. Your original skill stays unchanged.',
            );
        case 'collaborate':
            return skillsTranslate(
                'workspace_skills_share_type_desc_collaborate_edit',
                'Recipients can view and edit this skill with live sync. Changes sync for everyone. Only you can delete it.',
            );
        default:
            return skillsTranslate(
                'workspace_skills_share_type_desc_live_view',
                'Recipients can view this skill with live updates. They cannot edit or delete it.',
            );
    }
}

function getSkillFolderLabel(folderType) {
    switch (folderType) {
        case 'scripts':
            return skillsTranslate('workspace_skills_files_scripts_title', 'Scripts');
        case 'references':
            return skillsTranslate('workspace_skills_files_references_title', 'References');
        case 'assets':
            return skillsTranslate('workspace_skills_files_assets_title', 'Assets');
        default:
            return folderType || '';
    }
}

function commitSkillIconSelection(mode) {
    (mode === 'edit' ? SkillEditIconPicker : SkillCreateIconPicker)?.setOpen?.(false);
}

// ============================================================================
// Render Functions
// ============================================================================

const SkillsRender = {
    fileItem(file, folderType, skillId, { readOnly = false } = {}) {
        const iconSvg = SkillsUtils.getFileIcon(file.name);
        const size = SkillsUtils.formatFileSize(file.size);
        return `
            <div class="skill-file-item" data-filename="${SkillsUtils.escapeHtml(file.name)}" data-folder="${folderType}">
                <div class="skill-file-icon">
                    ${iconSvg}
                </div>
                <div class="skill-file-info">
                    <p class="skill-file-name">${SkillsUtils.escapeHtml(file.name)}</p>
                    <p class="skill-file-size">${size}</p>
                </div>
                ${readOnly ? '' : `<button
                    type="button"
                    class="skill-file-delete"
                    data-skill-id="${skillId}"
                    data-folder="${folderType}"
                    data-filename="${SkillsUtils.escapeHtml(file.name)}"
                    title="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_files_delete_title', 'Delete file'))}"
                    aria-label="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_files_delete_title', 'Delete file'))}"
                >
                    ${Icons.trash}
                </button>`}
            </div>
        `;
    },

    filesList(files, folderType, skillId, options = {}) {
        if (!files || files.length === 0) return '';
        return files.map(file => this.fileItem(file, folderType, skillId, options)).join('');
    },

    skillCard(skill) {
        const iconData = SkillsUtils.parseIcon(skill.icon);
        const isAdminSkill = skill.is_admin_skill === true;
        const isSubscribed = skill.is_subscribed === true;
        const allowShare = canManageSkillSharing(skill);
        const isShared = skillHasExistingShareState(skill) && !isSubscribed;
        const subscriberCount = skill.subscriber_count;
        const editLabel = skillsTranslate('workspace_skills_action_edit', 'Edit');
        const deleteLabel = skillsTranslate('workspace_skills_action_delete', 'Delete');
        
        let actionsHtml;
        if (isAdminSkill) {
            actionsHtml = `
                <div class="workspace-skills-footer-actions skill-footer-actions">
                    <span class="skill-admin-badge">
                        ${Icons.security}
                        ${skillsTranslate('workspace_skills_admin_badge', 'Managed Skill')}
                    </span>
                    <button
                        type="button"
                        class="skill-action-btn"
                        data-action="view"
                        data-skill-id="${skill.id}"
                        title="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_action_open', 'Open'))}"
                    >
                        ${Icons.eye}
                        ${skillsTranslate('workspace_skills_action_open', 'Open')}
                    </button>
                </div>
            `;
        } else if (isSubscribed) {
            actionsHtml = `
                <div class="workspace-skills-footer-actions skill-footer-actions">
                    <span class="skill-subscribed-badge">
                        ${Icons.groups}
                        ${skillsTranslate('workspace_skills_shared_by', 'Shared by {name}', {
                            name: SkillsUtils.escapeHtml(skill.owner_name || skillsTranslate('workspace_skills_unknown_owner', 'Unknown')),
                        })}
                    </span>
                    ${canEditSkill(skill) ? `<button
                        type="button"
                        class="skill-action-btn"
                        data-action="edit"
                        data-skill-id="${skill.id}"
                        title="${SkillsUtils.escapeHtml(editLabel)}"
                    >
                        ${Icons.create}
                        ${editLabel}
                    </button>` : ''}
                    <button
                        type="button"
                        class="skill-action-btn danger"
                        data-action="unsubscribe"
                        data-skill-id="${skill.id}"
                        title="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_action_remove', 'Remove'))}"
                    >
                        ${Icons.error}
                        ${skillsTranslate('workspace_skills_action_remove', 'Remove')}
                    </button>
                </div>
            `;
        } else {
            const shareLabel = isShared
                ? (subscriberCount
                    ? skillsTranslate('workspace_skills_share_button_shared_count', 'Shared ({count})', { count: subscriberCount })
                    : skillsTranslate('workspace_skills_share_button_shared', 'Shared'))
                : skillsTranslate('workspace_skills_share_button', 'Share');
            actionsHtml = `
                <div class="workspace-skills-footer-actions skill-footer-actions">
                    ${allowShare ? `<button
                        type="button"
                        class="skill-action-btn${isShared ? ' shared' : ''}"
                        data-action="share"
                        data-skill-id="${skill.id}"
                        title="${SkillsUtils.escapeHtml(isShared
                            ? skillsTranslate('workspace_skills_share_manage', 'Manage sharing')
                            : skillsTranslate('workspace_skills_share_button', 'Share'))}"
                    >
                        ${Icons.connections}
                        ${shareLabel}
                    </button>` : ''}
                    <button type="button" class="skill-action-btn" data-action="edit" data-skill-id="${skill.id}" title="${SkillsUtils.escapeHtml(editLabel)}">
                        ${Icons.create}
                        ${editLabel}
                    </button>
                    <button type="button" class="skill-action-btn danger" data-action="delete" data-skill-id="${skill.id}" title="${SkillsUtils.escapeHtml(deleteLabel)}">
                        ${Icons.trash}
                        ${deleteLabel}
                    </button>
                </div>
            `;
        }
        
        const cardClass = `workspace-skills-footer skill-card${isAdminSkill ? ' admin-skill' : ''}${isSubscribed ? ' subscribed-skill' : ''}`;
        
        return `
            <div class="${cardClass}" data-skill-id="${skill.id}" data-is-admin="${isAdminSkill}" data-is-subscribed="${isSubscribed}">
                <div class="skill-entry-main">
                    <div class="skill-entry-icon" style="background-color: ${iconData.color}">
                        ${workspaceSkillIconUtils.renderWorkspaceIcon(iconData, {
                            size: 20,
                            defaultIconId: SKILL_DEFAULT_ICON_ID,
                            iconOptions: SKILL_ICONS,
                        })}
                    </div>
                    <div class="skill-entry-copy">
                        <p class="skill-entry-title">${SkillsUtils.escapeHtml(skill.title)}</p>
                        <p class="skill-entry-content">${SkillsUtils.escapeHtml(skill.content || skillsTranslate('workspace_skills_no_instructions', 'No instructions'))}</p>
                    </div>
                </div>
                ${actionsHtml}
            </div>
        `;
    },

    emptyState({ isFiltered = false, query = '' } = {}) {
        if (isFiltered) {
            return `
                <div class="workspace-notifications-empty workspace-empty-grid" id="skillsEmptyState">
                    <div class="workspace-notifications-empty-icon">
                        ${Icons.magnifyingGlass}
                    </div>
                    <p class="workspace-notifications-empty-title">${skillsTranslate('workspace_skills_empty_filtered_title', 'No matching skills')}</p>
                    <p class="workspace-notifications-empty-text">${skillsTranslate('workspace_skills_empty_filtered_text', 'No skills matched "{query}". Try a different search term.', { query: SkillsUtils.escapeHtml(query) })}</p>
                </div>
            `;
        }

        return `
            <div class="workspace-notifications-empty workspace-empty-grid" id="skillsEmptyState">
                <div class="workspace-notifications-empty-icon">
                    ${Icons.lightning}
                </div>
                <p class="workspace-notifications-empty-title">${skillsTranslate('workspace_skills_empty_title', 'No skills yet')}</p>
                <p class="workspace-notifications-empty-text">${skillsTranslate('workspace_skills_empty_text', 'Skills are reusable instructions that enhance AI responses. Create skills for writing styles, code formatting, language preferences, or any specialized behavior you want the AI to follow.')}</p>
            </div>
        `;
    },

    loadingState() {
        return `<div class="skills-loading"><div class="skills-loading-spinner"></div><p>${skillsTranslate('workspace_skills_loading', 'Loading skills...')}</p></div>`;
    },

    getUserInitials(user = {}) {
        if (user.first_name && user.last_name) {
            return (user.first_name[0] + user.last_name[0]).toUpperCase();
        }
        if (user.first_name) {
            return user.first_name.substring(0, 2).toUpperCase();
        }
        if (user.display_name) {
            return user.display_name.substring(0, 2).toUpperCase();
        }
        return '??';
    },
};

// ============================================================================
// Skills Manager
// ============================================================================

const SkillsManager = {
    init() {
        if (SkillsState.initialized) return;
        this.setupEventListeners();
        this.initIconPickers();
        this.registerEscapeHandlers();
        if (!SkillsState.i18nListenerBound && typeof document !== 'undefined') {
            document.addEventListener('i18n:updated', () => this.handleI18nUpdated());
            SkillsState.i18nListenerBound = true;
        }
        SkillsState.initialized = true;
    },

    registerEscapeHandlers() {
        if (typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        window.registerEscapeHandler({
            id: 'workspace-skills-transient-dropdowns',
            priority: 120,
            isActive: () => this.hasTransientDropdown(),
            close: () => this.closeTransientDropdowns(),
        });

        window.registerEscapeHandler({
            id: 'workspace-skills-form-mode',
            priority: 20,
            isActive: () => this.isFormScreenActive(),
            close: () => this.showListScreen(),
        });
    },

    isFormScreenActive() {
        return Boolean(
            (SkillsDOM.skillsContentCreate && SkillsDOM.skillsContentCreate.style.display !== 'none') ||
            (SkillsDOM.skillsContentView && SkillsDOM.skillsContentView.style.display !== 'none') ||
            (SkillsDOM.skillsContentEdit && SkillsDOM.skillsContentEdit.style.display !== 'none')
        );
    },

    hasTransientDropdown() {
        return Boolean(SkillsState.create.isOpen || SkillsState.edit.isOpen);
    },

    closeTransientDropdowns() {
        SkillCreateIconPicker?.close?.();
        SkillEditIconPicker?.close?.();
    },

    handleI18nUpdated() {
        if (SkillsDOM.skillsDeleteConfirmText) {
            SkillsState.deleteConfirmDefaultText = skillsTranslate('workspace_skills_delete_confirm', 'Delete Skill');
            if (!SkillsDOM.skillsDeleteConfirmBtn?.disabled) {
                SkillsDOM.skillsDeleteConfirmText.textContent = SkillsState.deleteConfirmDefaultText;
            }
        }

        if (SkillsState.activeSkillContext && SkillsDOM.skillsContentEdit?.style.display === 'flex') {
            this.renderSkillFiles(SkillsState.activeSkillContext);
        }

        if (SkillsState.activeSkillContext?.is_admin_skill === true && SkillsDOM.skillsContentView?.style.display === 'flex') {
            this.renderManagedSkillView(SkillsState.activeSkillContext);
        }

        if (!SkillsState.isLoading) {
            this.renderSkills();
        }

        if (SkillsState.sharingSkillId && !SkillsDOM.skillShareOverlay?.hasAttribute('hidden')) {
            this.showShareModal(SkillsState.sharingSkillId);
        }

        if (SkillsState.pendingShareId && !SkillsDOM.skillAcceptOverlay?.hasAttribute('hidden')) {
            this.showAcceptModal(SkillsState.pendingShareId, SkillsState.pendingShareType);
        }

        if (SkillsState.marketplaceImportData && SkillsDOM.marketplaceImportOverlay?.classList.contains('active')) {
            this.showMarketplaceImportModal();
        }

        if (!SkillsDOM.skillImportOverlay?.hasAttribute('hidden')) {
            if (this._importState.activeTab === 'file') {
                this._importUpdateFileUI();
            } else if (this._importState.parsedData) {
                this._importShowPreview(this._importState.parsedData);
            }
            if (
                this._importState.activeTab === 'paste'
                && this._importState.pendingMarkdown
                && !this._importState.parsedData
            ) {
                this._importValidateAndPreview(this._importState.pendingMarkdown);
            }
            if (
                this._importState.activeTab === 'paste'
                && !this._importState.pendingMarkdown
                && SkillsDOM.skillImportConfirmText
            ) {
                SkillsDOM.skillImportConfirmText.textContent = skillsTranslate('workspace_skills_import_confirm', 'Import Skill');
            }
        }
    },

    initIconPickers() {
        SkillCreateIconPicker?.bind?.();
        SkillEditIconPicker?.bind?.();
        SkillCreateIconPicker?.render?.();
        SkillEditIconPicker?.render?.();
        SkillCreateIconPicker?.updatePreview?.();
        SkillEditIconPicker?.updatePreview?.();
    },

    setupEventListeners() {
        // Add skill button -> show create screen
        SkillsDOM.addSkillBtn?.addEventListener('click', () => this.showCreateScreen());

        SkillsDOM.skillsSearchInput?.addEventListener('input', (event) => {
            this.setSearchQuery(event?.target?.value || '');
        });
        SkillsDOM.skillsSearchInput?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && SkillsDOM.skillsSearchInput?.value) {
                event.preventDefault();
                this.clearSearch();
            }
        });
        SkillsDOM.skillsSearchClear?.addEventListener('click', () => this.clearSearch());
        
        // Import skill button -> show import modal
        SkillsDOM.importSkillBtn?.addEventListener('click', () => this.showImportModal());
        
        // Create form buttons
        SkillsDOM.createSkillCancelBtn?.addEventListener('click', () => this.showListScreen());
        SkillsDOM.confirmCreateSkillBtn?.addEventListener('click', () => this.handleCreate());
        
        // Clear name error on input
        SkillsDOM.skillNameInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillNameInput, SkillsDOM.skillNameError);
        });
        
        // Clear description error on input
        SkillsDOM.skillDescriptionInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillDescriptionInput, SkillsDOM.skillDescriptionError);
        });
        
        // Clear content error on input
        SkillsDOM.skillContentInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillContentInput, SkillsDOM.skillContentError);
        });

        SkillsDOM.skillMetadataInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillMetadataInput, SkillsDOM.skillMetadataError);
        });
        
        // Edit form buttons
        SkillsDOM.editSkillCancelBtn?.addEventListener('click', () => this.showListScreen());
        SkillsDOM.saveSkillChangesBtn?.addEventListener('click', () => this.handleUpdate());

        // The managed-skill detail view has no mutating controls. Its only
        // action returns to the workspace list and restores keyboard focus.
        SkillsDOM.skillViewBackBtn?.addEventListener('click', () => this.showListScreen());
        
        // Clear title error on input
        SkillsDOM.skillEditTitleInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillEditTitleInput, SkillsDOM.skillEditTitleError);
        });
        
        // Clear content error on input
        SkillsDOM.skillEditContentInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillEditContentInput, SkillsDOM.skillEditContentError);
        });

        SkillsDOM.skillEditMetadataInput?.addEventListener('input', () => {
            SkillsUtils.clearInputError(SkillsDOM.skillEditMetadataInput, SkillsDOM.skillEditMetadataError);
        });
        
        // Delete overlay buttons
        if (!SkillsState.deleteConfirmDefaultText && SkillsDOM.skillsDeleteConfirmText) {
            SkillsState.deleteConfirmDefaultText = SkillsDOM.skillsDeleteConfirmText.textContent?.trim()
                || skillsTranslate('workspace_skills_action_delete', 'Delete');
        }
        SkillsDOM.skillsDeleteCancelBtn?.addEventListener('click', () => this.hideDeleteOverlay());
        SkillsDOM.skillsDeleteConfirmBtn?.addEventListener('click', () => this.handleDelete());
        const deleteOverlay = SkillsDOM.skillsDeleteOverlay;
        deleteOverlay?.addEventListener('click', (e) => {
            if (e.target === deleteOverlay) this.hideDeleteOverlay();
        });
        
        // Skills grid event delegation
        SkillsDOM.skillsGrid?.addEventListener('click', (e) => {
            const actionBtn = e.target.closest('.skill-action-btn');
            if (actionBtn) {
                const skillId = actionBtn.dataset.skillId;
                const action = actionBtn.dataset.action;
                if (action === 'view') this.showManagedSkillScreen(skillId);
                else if (action === 'edit') this.showEditScreen(skillId);
                else if (action === 'delete') this.showDeleteScreen(skillId);
                else if (action === 'share') this.showShareModal(skillId);
                else if (action === 'unsubscribe') this.handleUnsubscribe(skillId);
            }
        });
        
        // File upload buttons
        this.setupFileUpload('scripts', SkillsDOM.skillEditScriptsBtn, SkillsDOM.skillEditScriptsInput);
        this.setupFileUpload('references', SkillsDOM.skillEditReferencesBtn, SkillsDOM.skillEditReferencesInput);
        this.setupFileUpload('assets', SkillsDOM.skillEditAssetsBtn, SkillsDOM.skillEditAssetsInput);
        
        // File delete event delegation
        SkillsDOM.skillsContentEdit?.addEventListener('click', (e) => {
            const deleteBtn = e.target.closest('.skill-file-delete');
            if (deleteBtn) {
                const skillId = deleteBtn.dataset.skillId;
                const folder = deleteBtn.dataset.folder;
                const filename = deleteBtn.dataset.filename;
                this.handleFileDelete(skillId, folder, filename);
            }
        });
    },

    setupFileUpload(folderType, buttonEl, inputEl) {
        if (!buttonEl || !inputEl) return;
        
        buttonEl.addEventListener('click', () => inputEl.click());
        
        inputEl.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files || []);
            if (files.length === 0) return;
            
            const skill = SkillsState.activeSkillContext;
            if (!skill || skill.is_admin_skill === true || !canEditSkill(skill)) return;
            
            buttonEl.classList.add('loading');
            const originalText = buttonEl.innerHTML;
            buttonEl.innerHTML = skillsButtonContent(
                '',
                skillsTranslate('workspace_skills_files_uploading', 'Uploading...'),
            );
            
            try {
                const result = await SkillsAPI.uploadSkillFiles(skill.id, folderType, files);
                if (result.uploaded && result.uploaded.length > 0) {
                    if (typeof showNotification === 'function') {
                        showNotification(
                            skillsPlural(
                                result.uploaded.length,
                                'workspace_skills_files_upload_success_one',
                                'Uploaded 1 file successfully',
                                'workspace_skills_files_upload_success_other',
                                'Uploaded {count} files successfully',
                                { count: result.uploaded.length },
                            ),
                            'success',
                        );
                    }
                    // Refresh skills to get updated file lists
                    await this.loadSkills();
                    const updatedSkill = SkillsState.skills.find(s => s.id === skill.id);
                    if (updatedSkill) {
                        SkillsState.activeSkillContext = updatedSkill;
                        this.renderSkillFiles(updatedSkill);
                    }
                }
                if (result.errors && result.errors.length > 0) {
                    console.error('File upload errors:', result.errors);
                    if (typeof showNotification === 'function') {
                        showNotification(
                            skillsPlural(
                                result.errors.length,
                                'workspace_skills_files_upload_error_count_one',
                                'Failed to upload 1 file',
                                'workspace_skills_files_upload_error_count_other',
                                'Failed to upload {count} files',
                                { count: result.errors.length },
                            ),
                            'error',
                        );
                    }
                }
            } catch (error) {
                console.error('File upload failed:', error);
                if (typeof showNotification === 'function') {
                    showNotification(
                        skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_files_upload_error', 'Failed to upload files')),
                        'error',
                    );
                }
            } finally {
                buttonEl.classList.remove('loading');
                buttonEl.innerHTML = originalText;
                inputEl.value = '';
            }
        });
    },

    async handleFileDelete(skillId, folderType, filename) {
        // Managed skills live under the administrator-owned namespace. Never
        // let a forged workspace event enter the personal file-delete flow.
        const skill = SkillsState.skills.find(item => item.id === skillId);
        if (!skill || skill.is_admin_skill === true || !canEditSkill(skill)) return;

        if (!await window.showDeleteConfirm({
            message: skillsTranslate(
                'workspace_skills_files_delete_confirm',
                'Delete "{filename}" from {folderType}?',
                {
                    filename,
                    folderType: getSkillFolderLabel(folderType),
                },
            ),
            confirmLabel: skillsTranslate('workspace_skills_files_delete_title', 'Delete file'),
        })) return;
        
        try {
            await SkillsAPI.deleteSkillFile(skillId, folderType, filename);
            if (typeof showNotification === 'function') {
                showNotification(skillsTranslate('workspace_skills_files_delete_success', 'File deleted successfully'), 'success');
            }
            // Refresh skills to get updated file lists
            await this.loadSkills();
            const updatedSkill = SkillsState.skills.find(s => s.id === skillId);
            if (updatedSkill) {
                SkillsState.activeSkillContext = updatedSkill;
                this.renderSkillFiles(updatedSkill);
            }
        } catch (error) {
            console.error('File delete failed:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_files_delete_error', 'Failed to delete file')),
                    'error',
                );
            }
        }
    },

    // Screen Navigation
    showListScreen() {
        const returnSkillId = SkillsState.detailReturnSkillId;
        SkillsDOM.skillsContent && (SkillsDOM.skillsContent.style.display = 'flex');
        SkillsDOM.skillsContentCreate && (SkillsDOM.skillsContentCreate.style.display = 'none');
        SkillsDOM.skillsContentView && (SkillsDOM.skillsContentView.style.display = 'none');
        SkillsDOM.skillsContentEdit && (SkillsDOM.skillsContentEdit.style.display = 'none');
        SkillsState.activeSkillContext = null;
        SkillsState.detailReturnSkillId = null;
        this.updateSearchClearButton();

        // The list can rerender while the detail page is open (for example
        // after a locale change), so resolve the return target by skill id
        // instead of retaining a potentially detached DOM node.
        if (returnSkillId && typeof window !== 'undefined') {
            const restoreDetailTriggerFocus = () => {
                Array.from(SkillsDOM.skillsGrid?.querySelectorAll('.skill-action-btn[data-action="view"]') || [])
                    .find(button => button.dataset.skillId === returnSkillId)
                    ?.focus();
            };
            if (typeof window.queueMicrotask === 'function') {
                window.queueMicrotask(restoreDetailTriggerFocus);
            } else {
                window.setTimeout(restoreDetailTriggerFocus, 0);
            }
        }
    },

    showCreateScreen() {
        // Reset form
        if (SkillsDOM.skillNameInput) SkillsDOM.skillNameInput.value = '';
        if (SkillsDOM.skillDescriptionInput) SkillsDOM.skillDescriptionInput.value = '';
        if (SkillsDOM.skillContentInput) SkillsDOM.skillContentInput.value = '';
        if (SkillsDOM.skillCompatibilityInput) SkillsDOM.skillCompatibilityInput.value = '';
        if (SkillsDOM.skillLicenseInput) SkillsDOM.skillLicenseInput.value = '';
        if (SkillsDOM.skillMetadataInput) SkillsDOM.skillMetadataInput.value = '';
        
        // Clear any previous error state
        SkillsUtils.clearInputError(SkillsDOM.skillNameInput, SkillsDOM.skillNameError);
        SkillsUtils.clearInputError(SkillsDOM.skillDescriptionInput, SkillsDOM.skillDescriptionError);
        SkillsUtils.clearInputError(SkillsDOM.skillContentInput, SkillsDOM.skillContentError);
        SkillsUtils.clearInputError(SkillsDOM.skillMetadataInput, SkillsDOM.skillMetadataError);
        
        SkillCreateIconPicker?.reset?.(SKILL_DEFAULT_ICON_ID, SKILL_ICON_COLORS[0].hex);
        SkillCreateIconPicker?.render?.();
        SkillCreateIconPicker?.updatePreview?.();
        
        // Show screen
        SkillsDOM.skillsContent && (SkillsDOM.skillsContent.style.display = 'none');
        SkillsDOM.skillsContentCreate && (SkillsDOM.skillsContentCreate.style.display = 'flex');
        SkillsDOM.skillsContentView && (SkillsDOM.skillsContentView.style.display = 'none');
        SkillsDOM.skillsContentEdit && (SkillsDOM.skillsContentEdit.style.display = 'none');
        
        SkillsDOM.skillNameInput?.focus();
    },

    showEditScreen(skillId) {
        const skill = SkillsState.skills.find(s => s.id === skillId);
        if (!skill) return;

        // Managed skills are intentionally inspectable but immutable from the
        // user workspace. Route direct calls to the read-only experience too.
        if (skill.is_admin_skill === true) {
            this.showManagedSkillScreen(skillId);
            return;
        }
        if (!canEditSkill(skill)) return;
        
        SkillsState.activeSkillContext = skill;
        
        // Clear any previous error state
        SkillsUtils.clearInputError(SkillsDOM.skillEditTitleInput, SkillsDOM.skillEditTitleError);
        SkillsUtils.clearInputError(SkillsDOM.skillEditContentInput, SkillsDOM.skillEditContentError);
        SkillsUtils.clearInputError(SkillsDOM.skillEditMetadataInput, SkillsDOM.skillEditMetadataError);
        
        // Populate form
        if (SkillsDOM.skillEditTitleInput) SkillsDOM.skillEditTitleInput.value = skill.title || '';
        if (SkillsDOM.skillEditContentInput) SkillsDOM.skillEditContentInput.value = skill.content || '';
        if (SkillsDOM.skillEditCompatibilityInput) SkillsDOM.skillEditCompatibilityInput.value = skill.compatibility || '';
        if (SkillsDOM.skillEditLicenseInput) SkillsDOM.skillEditLicenseInput.value = skill.license || '';
        if (SkillsDOM.skillEditMetadataInput) {
            SkillsDOM.skillEditMetadataInput.value = skill.metadata ? JSON.stringify(skill.metadata, null, 2) : '';
        }
        
        const iconData = SkillsUtils.parseIcon(skill.icon);
        SkillEditIconPicker?.reset?.(
            iconData.iconId,
            iconData.color || SKILL_ICON_COLORS[0].hex,
        );
        SkillEditIconPicker?.render?.();
        SkillEditIconPicker?.updatePreview?.();
        
        // Render file lists
        this.renderSkillFiles(skill);
        
        // Show screen
        SkillsDOM.skillsContent && (SkillsDOM.skillsContent.style.display = 'none');
        SkillsDOM.skillsContentCreate && (SkillsDOM.skillsContentCreate.style.display = 'none');
        SkillsDOM.skillsContentView && (SkillsDOM.skillsContentView.style.display = 'none');
        SkillsDOM.skillsContentEdit && (SkillsDOM.skillsContentEdit.style.display = 'flex');
        
        SkillsDOM.skillEditTitleInput?.focus();
    },

    /**
     * Open a managed skill in a purpose-built read-only detail screen.
     * Keeping this separate from the edit form makes the permission boundary
     * obvious and avoids presenting disabled controls that look broken.
     */
    showManagedSkillScreen(skillId) {
        const skill = SkillsState.skills.find(item => item.id === skillId);
        if (!skill || skill.is_admin_skill !== true) return;

        SkillsState.activeSkillContext = skill;
        SkillsState.detailReturnSkillId = skill.id;
        this.renderManagedSkillView(skill);

        SkillsDOM.skillsContent && (SkillsDOM.skillsContent.style.display = 'none');
        SkillsDOM.skillsContentCreate && (SkillsDOM.skillsContentCreate.style.display = 'none');
        SkillsDOM.skillsContentView && (SkillsDOM.skillsContentView.style.display = 'flex');
        SkillsDOM.skillsContentEdit && (SkillsDOM.skillsContentEdit.style.display = 'none');

        SkillsDOM.skillViewName?.focus();
    },

    /** Render all managed-skill data already authorized by the list endpoint. */
    renderManagedSkillView(skill) {
        if (!skill || skill.is_admin_skill !== true) return;

        if (SkillsDOM.skillViewName) SkillsDOM.skillViewName.textContent = skill.title || '';
        if (SkillsDOM.skillViewDescription) {
            SkillsDOM.skillViewDescription.textContent = skill.description
                || skillsTranslate('workspace_skills_marketplace_no_description', 'No description provided.');
        }

        const iconData = SkillsUtils.parseIcon(skill.icon);
        if (SkillsDOM.skillViewIcon) {
            SkillsDOM.skillViewIcon.style.backgroundColor = iconData.color;
            SkillsDOM.skillViewIcon.innerHTML = workspaceSkillIconUtils.renderWorkspaceIcon(iconData, {
                size: 24,
                defaultIconId: SKILL_DEFAULT_ICON_ID,
                iconOptions: SKILL_ICONS,
            });
        }
        if (SkillsDOM.skillViewManagedBadgeIcon) SkillsDOM.skillViewManagedBadgeIcon.innerHTML = Icons.security;
        if (SkillsDOM.skillViewManagedNoticeIcon) SkillsDOM.skillViewManagedNoticeIcon.innerHTML = Icons.security;

        if (SkillsDOM.skillViewContent) {
            const content = skill.content || skillsTranslate('workspace_skills_no_instructions', 'No instructions');
            if (window.ChatMarkdownBlockEditor?.renderMarkdownToHtml) {
                SkillsDOM.skillViewContent.classList.remove('is-plain-text');
                SkillsDOM.skillViewContent.innerHTML = window.ChatMarkdownBlockEditor.renderMarkdownToHtml(content);
            } else {
                SkillsDOM.skillViewContent.classList.add('is-plain-text');
                SkillsDOM.skillViewContent.textContent = content;
            }
        }

        const detailRows = [
            [SkillsDOM.skillViewAuthorRow, SkillsDOM.skillViewAuthor, skill.author],
            [SkillsDOM.skillViewCompatibilityRow, SkillsDOM.skillViewCompatibility, skill.compatibility],
            [SkillsDOM.skillViewLicenseRow, SkillsDOM.skillViewLicense, skill.license],
        ];
        let hasDetails = false;
        detailRows.forEach(([row, valueElement, value]) => {
            const hasValue = typeof value === 'string' ? value.trim().length > 0 : value != null;
            row?.toggleAttribute('hidden', !hasValue);
            if (hasValue && valueElement) valueElement.textContent = String(value);
            hasDetails ||= hasValue;
        });

        const hasMetadata = Boolean(skill.metadata && Object.keys(skill.metadata).length > 0);
        SkillsDOM.skillViewMetadataRow?.toggleAttribute('hidden', !hasMetadata);
        if (hasMetadata && SkillsDOM.skillViewMetadata) {
            SkillsDOM.skillViewMetadata.textContent = JSON.stringify(skill.metadata, null, 2);
        }
        hasDetails ||= hasMetadata;
        SkillsDOM.skillViewDetailsSection?.toggleAttribute('hidden', !hasDetails);

        const files = skill.files || { scripts: [], references: [], assets: [] };
        const resourceGroups = [
            [files.scripts || [], 'scripts', SkillsDOM.skillViewScriptsSection, SkillsDOM.skillViewScriptsList],
            [files.references || [], 'references', SkillsDOM.skillViewReferencesSection, SkillsDOM.skillViewReferencesList],
            [files.assets || [], 'assets', SkillsDOM.skillViewAssetsSection, SkillsDOM.skillViewAssetsList],
        ];
        let hasResources = false;
        resourceGroups.forEach(([items, folderType, section, list]) => {
            const hasItems = items.length > 0;
            section?.toggleAttribute('hidden', !hasItems);
            if (list) list.innerHTML = SkillsRender.filesList(items, folderType, skill.id, { readOnly: true });
            hasResources ||= hasItems;
        });
        SkillsDOM.skillViewResourcesSection?.toggleAttribute('hidden', !hasResources);
    },

    renderSkillFiles(skill) {
        const files = skill.files || { scripts: [], references: [], assets: [] };
        
        if (SkillsDOM.skillEditScriptsList) {
            SkillsDOM.skillEditScriptsList.innerHTML = SkillsRender.filesList(files.scripts, 'scripts', skill.id);
        }
        if (SkillsDOM.skillEditReferencesList) {
            SkillsDOM.skillEditReferencesList.innerHTML = SkillsRender.filesList(files.references, 'references', skill.id);
        }
        if (SkillsDOM.skillEditAssetsList) {
            SkillsDOM.skillEditAssetsList.innerHTML = SkillsRender.filesList(files.assets, 'assets', skill.id);
        }
    },

    showDeleteScreen(skillId) {
        const skill = SkillsState.skills.find(s => s.id === skillId);
        if (!skill || skill.is_subscribed === true) return;

        if (skill.is_admin_skill === true) {
            this.showManagedSkillScreen(skillId);
            return;
        }
        
        SkillsState.activeSkillContext = skill;
        
        if (SkillsDOM.skillsDeleteName) {
            SkillsDOM.skillsDeleteName.textContent = skill.title || skill.name || '';
        }
        if (SkillsDOM.skillsDeleteConfirmBtn) {
            SkillsDOM.skillsDeleteConfirmBtn.disabled = false;
        }
        if (SkillsDOM.skillsDeleteConfirmText && SkillsState.deleteConfirmDefaultText) {
            SkillsDOM.skillsDeleteConfirmText.textContent = SkillsState.deleteConfirmDefaultText;
        }
        
        this.showDeleteOverlay();
    },
    
    showDeleteOverlay() {
        const overlay = SkillsDOM.skillsDeleteOverlay;
        if (!overlay) return;
        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
    },
    
    hideDeleteOverlay() {
        const overlay = SkillsDOM.skillsDeleteOverlay;
        if (overlay) {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
        }
        if (SkillsDOM.skillsDeleteConfirmBtn) {
            SkillsDOM.skillsDeleteConfirmBtn.disabled = false;
        }
        if (SkillsDOM.skillsDeleteConfirmText && SkillsState.deleteConfirmDefaultText) {
            SkillsDOM.skillsDeleteConfirmText.textContent = SkillsState.deleteConfirmDefaultText;
        }
        SkillsState.activeSkillContext = null;
    },

    // CRUD Operations
    async loadSkills() {
        const grid = SkillsDOM.skillsGrid;
        if (!grid) return;

        SkillsState.isLoading = true;
        grid.innerHTML = SkillsRender.loadingState();

        try {
            const skills = await SkillsAPI.fetchSkills();
            SkillsState.skills = skills;
            this.renderSkills();
        } catch (error) {
            console.error('Failed to load skills:', error);
            grid.innerHTML = `<div class="skills-error"><p>${skillsTranslate('workspace_skills_load_error', 'Failed to load skills. Please try again.')}</p></div>`;
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_load_error_notification', 'Failed to load skills')),
                    'error',
                );
            }
        } finally {
            SkillsState.isLoading = false;
        }
    },

    renderSkills() {
        const grid = SkillsDOM.skillsGrid;
        if (!grid) return;

        const filteredSkills = this.getFilteredSkills();
        const hasActiveSearch = SkillsState.searchQuery.length > 0;

        if (SkillsState.skills.length === 0) {
            grid.innerHTML = SkillsRender.emptyState();
            return;
        }

        if (filteredSkills.length === 0) {
            grid.innerHTML = SkillsRender.emptyState({
                isFiltered: hasActiveSearch,
                query: SkillsState.searchQuery,
            });
            return;
        }

        grid.innerHTML = filteredSkills.map(skill => SkillsRender.skillCard(skill)).join('');
    },

    updateSearchClearButton() {
        const clearBtn = SkillsDOM.skillsSearchClear;
        if (!clearBtn) return;
        const hasQuery = SkillsState.searchQuery.length > 0;
        clearBtn.style.display = hasQuery ? 'flex' : 'none';
        clearBtn.toggleAttribute('hidden', !hasQuery);
    },

    setSearchQuery(value = '') {
        SkillsState.searchQuery = String(value).trim();
        if (SkillsDOM.skillsSearchInput && SkillsDOM.skillsSearchInput.value !== SkillsState.searchQuery) {
            SkillsDOM.skillsSearchInput.value = SkillsState.searchQuery;
        }
        this.updateSearchClearButton();
        this.scrollResultsToTop();
        this.renderSkills();
    },

    clearSearch() {
        this.setSearchQuery('');
        SkillsDOM.skillsSearchInput?.focus();
    },

    scrollResultsToTop() {
        if (SkillsDOM.skillsResultsPanel) {
            SkillsDOM.skillsResultsPanel.scrollTop = 0;
        }
    },

    getFilteredSkills() {
        const query = SkillsUtils.normalizeSearchQuery(SkillsState.searchQuery);
        if (!query) {
            return SkillsState.skills;
        }

        return SkillsState.skills.filter((skill) => {
            const haystack = [
                skill?.title,
                skill?.name,
                skill?.description,
                skill?.content,
                skill?.compatibility,
                skill?.license,
                skill?.owner_name,
            ]
                .filter(Boolean)
                .join(' ')
                .toLowerCase();

            return haystack.includes(query);
        });
    },

    async handleCreate() {
        commitSkillIconSelection('create');
        const name = SkillsDOM.skillNameInput?.value.trim();
        const description = SkillsDOM.skillDescriptionInput?.value.trim();
        const content = SkillsDOM.skillContentInput?.value.trim();
        const compatibility = SkillsDOM.skillCompatibilityInput?.value.trim();
        const license = SkillsDOM.skillLicenseInput?.value.trim();
        const metadataRaw = SkillsDOM.skillMetadataInput?.value.trim();
        
        if (!name) {
            SkillsUtils.showInputError(
                SkillsDOM.skillNameInput,
                SkillsDOM.skillNameError,
                skillsTranslate('workspace_skills_validation_name_required', 'Please enter a skill name')
            );
            return;
        }
        
        // Validate name format (lowercase, numbers, hyphens)
        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
            SkillsUtils.showInputError(
                SkillsDOM.skillNameInput,
                SkillsDOM.skillNameError,
                skillsTranslate('skills_create_name_error', 'Name must use lowercase letters, numbers, and hyphens only (e.g., my-skill-name)')
            );
            return;
        }
        
        if (!description) {
            SkillsUtils.showInputError(
                SkillsDOM.skillDescriptionInput,
                SkillsDOM.skillDescriptionError,
                skillsTranslate('skills_create_description_error', 'Please enter a short description')
            );
            return;
        }
        
        if (!content) {
            SkillsUtils.showInputError(
                SkillsDOM.skillContentInput,
                SkillsDOM.skillContentError,
                skillsTranslate('skills_create_content_error', 'Please enter skill instructions')
            );
            return;
        }
        
        // Parse and validate metadata JSON
        const metadataResult = SkillsUtils.parseMetadata(metadataRaw);
        if (metadataResult.errorKey) {
            SkillsUtils.showMetadataError(
                SkillsDOM.skillMetadataInput,
                SkillsDOM.skillMetadataError,
                metadataResult.errorKey,
            );
            return;
        }
        const metadata = metadataResult.value;
        
        const iconJson = SkillsUtils.buildIconJson('create');
        
        const btn = SkillsDOM.confirmCreateSkillBtn;
        if (btn) { btn.disabled = true; btn.textContent = skillsTranslate('workspace_skills_create_confirming', 'Creating...'); }
        
        try {
            await SkillsAPI.createSkill(name, description, content, iconJson, compatibility || null, license || null, metadata);
            notifyWorkspaceSkillsChanged({ reason: 'created' });
            if (typeof showNotification === 'function') showNotification(skillsTranslate('workspace_skills_create_success', 'Skill created successfully'), 'success');
            await this.loadSkills();
            this.showListScreen();
        } catch (error) {
            console.error('Failed to create skill:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_create_error', 'Failed to create skill')),
                    'error',
                );
            }
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = skillsTranslate('skills_create_confirm', 'Create skill'); }
        }
    },

    async handleUpdate() {
        const skill = SkillsState.activeSkillContext;
        if (!skill || skill.is_admin_skill === true || !canEditSkill(skill)) return;
        commitSkillIconSelection('edit');

        const title = SkillsDOM.skillEditTitleInput?.value.trim();
        const content = SkillsDOM.skillEditContentInput?.value.trim();
        const compatibility = SkillsDOM.skillEditCompatibilityInput?.value.trim();
        const license = SkillsDOM.skillEditLicenseInput?.value.trim();
        const metadataRaw = SkillsDOM.skillEditMetadataInput?.value.trim();
        
        if (!title) {
            SkillsUtils.showInputError(
                SkillsDOM.skillEditTitleInput,
                SkillsDOM.skillEditTitleError,
                skillsTranslate('skills_edit_title_error', 'Please enter a skill title')
            );
            return;
        }
        
        if (!content) {
            SkillsUtils.showInputError(
                SkillsDOM.skillEditContentInput,
                SkillsDOM.skillEditContentError,
                skillsTranslate('skills_edit_content_error', 'Please enter skill instructions')
            );
            return;
        }
        
        // Parse and validate metadata JSON
        const metadataResult = SkillsUtils.parseMetadata(metadataRaw);
        if (metadataResult.errorKey) {
            SkillsUtils.showMetadataError(
                SkillsDOM.skillEditMetadataInput,
                SkillsDOM.skillEditMetadataError,
                metadataResult.errorKey,
            );
            return;
        }
        const metadata = metadataResult.value;
        
        const iconJson = SkillsUtils.buildIconJson('edit');
        
        const updateData = { title, content, icon: iconJson };
        if (compatibility) updateData.compatibility = compatibility;
        if (license) updateData.license = license;
        if (metadata) updateData.metadata = metadata;
        
        const btn = SkillsDOM.saveSkillChangesBtn;
        if (btn) { btn.disabled = true; btn.textContent = skillsTranslate('workspace_skills_edit_confirming', 'Saving...'); }
        
        try {
            await SkillsAPI.updateSkill(skill.id, updateData);
            notifyWorkspaceSkillsChanged({ reason: 'updated', skillId: skill.id });
            if (typeof showNotification === 'function') showNotification(skillsTranslate('workspace_skills_edit_success', 'Skill updated successfully'), 'success');
            await this.loadSkills();
            this.showListScreen();
        } catch (error) {
            console.error('Failed to update skill:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_edit_error', 'Failed to update skill')),
                    'error',
                );
            }
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = skillsTranslate('skills_edit_save', 'Save changes'); }
        }
    },

    async handleDelete() {
        const skill = SkillsState.activeSkillContext;
        if (!skill || skill.is_admin_skill === true || skill.is_subscribed === true) return;
        
        const btn = SkillsDOM.skillsDeleteConfirmBtn;
        const btnText = SkillsDOM.skillsDeleteConfirmText;
        if (btn) btn.disabled = true;
        if (btnText) btnText.textContent = skillsTranslate('workspace_skills_delete_confirming', 'Deleting...');
        
        try {
            await SkillsAPI.deleteSkill(skill.id);
            notifyWorkspaceSkillsChanged({ reason: 'deleted', skillId: skill.id });
            if (typeof showNotification === 'function') showNotification(skillsTranslate('workspace_skills_delete_success', 'Skill deleted successfully'), 'success');
            await this.loadSkills();
            this.hideDeleteOverlay();
            this.showListScreen();
        } catch (error) {
            console.error('Failed to delete skill:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_delete_error', 'Failed to delete skill')),
                    'error',
                );
            }
            if (btn) btn.disabled = false;
            if (btnText && SkillsState.deleteConfirmDefaultText) {
                btnText.textContent = SkillsState.deleteConfirmDefaultText;
            }
        }
    },

    async handleUnsubscribe(skillId) {
        const skill = SkillsState.skills.find(s => s.id === skillId);
        if (!skill || skill.is_admin_skill === true) return;
        
        if (!await window.showDeleteConfirm({
            title: skillsTranslate('common_remove_confirm_title', 'Remove item?'),
            message: skillsTranslate(
                'workspace_skills_remove_confirm',
                'Remove "{title}" from your workspace? You can add it back later using the share link.',
                { title: skill.title },
            ),
            confirmLabel: skillsTranslate('workspace_skills_action_remove', 'Remove'),
        })) {
            return;
        }
        
        try {
            await SkillsAPI.unsubscribeFromSkill(skillId);
            notifyWorkspaceSkillsChanged({ reason: 'unsubscribed', skillId });
            if (typeof showNotification === 'function') {
                showNotification(skillsTranslate('workspace_skills_remove_success', 'Skill removed from workspace'), 'success');
            }
            await this.loadSkills();
        } catch (error) {
            console.error('Failed to unsubscribe from skill:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_remove_error', 'Failed to remove skill')),
                    'error',
                );
            }
        }
    },

    show() {
        this.init();
        this.showListScreen();
        this.loadSkills();
    },

    // ========================================================================
    // Sharing Methods
    // ========================================================================

    async showShareModal(skillId) {
        const skill = SkillsState.skills.find(s => s.id === skillId);
        if (!skill || skill.is_admin_skill === true) return;
        if (!canManageSkillSharing(skill)) return;

        let overlay = document.getElementById('skillsShareOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'skillsShareOverlay';
            overlay.className = 'cs-overlay shared-modal-overlay';
            document.body.appendChild(overlay);
        }
        SkillsState.sharingSkillId = skillId;
        SkillsState.shareMode = 'list';
        SkillsState.shareAction = 'link';
        SkillsState.currentShareType = 'live';
        SkillsState.selectedUserIds = [];
        this.renderShareModal(skill);

        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('cs-active'));
        await this.loadShareStatus(skillId);
    },

    getShareAction() {
        return String(document.querySelector('input[name="skillsShareAction"]:checked')?.value || SkillsState.shareAction || 'link');
    },

    getShareTypeSelection() {
        return String(document.querySelector('input[name="skillsShareType"]:checked')?.value || SkillsState.currentShareType || 'live');
    },

    renderShareModal(skill) {
        const overlay = document.getElementById('skillsShareOverlay');
        if (!overlay || !skill) return;

        const status = SkillsState.shareStatus || {};
        const hasShares = Boolean(status.clone_share_id || status.live_share_id || status.collaborate_share_id);
        const isListMode = SkillsState.shareMode === 'list';
        const isInvite = SkillsState.shareAction === 'invite';

        const shares = [];
        if (status.clone_share_id) {
            shares.push({ type: 'clone', id: status.clone_share_id, count: 0 });
        }
        if (status.live_share_id) {
            shares.push({ type: 'live', id: status.live_share_id, count: status.live_subscriber_count || 0 });
        }
        if (status.collaborate_share_id) {
            shares.push({ type: 'collaborate', id: status.collaborate_share_id, count: status.collaborate_subscriber_count || 0 });
        }

        overlay.innerHTML = `
            <div class="cs-modal shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="skillsShareTitle" tabindex="-1">
                <header class="cs-header shared-modal-header shared-modal-header--main">
                    <div class="cs-header-text shared-modal-heading">
                        <h3 class="cs-title shared-modal-title" id="skillsShareTitle">${skillsTranslate('workspace_skills_share_title', 'Share Skill')}</h3>
                        <p class="cs-subtitle shared-modal-subtitle">${SkillsUtils.escapeHtml(skill.title || skill.name || skillsTranslate('workspace_skills_marketplace_title_fallback', 'Skill'))}</p>
                    </div>
                    <button type="button" class="cs-icon-btn shared-modal-close" id="skillsShareCloseBtn" aria-label="${SkillsUtils.escapeHtml(skillsTranslate('common_cancel', 'Cancel'))}">
                        ${Icons.close}
                    </button>
                </header>

                <div class="cs-body shared-modal-body">
                    <section class="cs-section" ${isListMode && hasShares ? '' : 'hidden'}>
                        <div class="cs-section-head">
                            <span class="cs-section-label">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_active', 'Active Shares'))}</span>
                        </div>
                        <div class="cs-link-list" id="skillsShareLinkList">
                            ${shares.map((share) => this.renderSkillShareLinkCard(share)).join('')}
                        </div>
                    </section>

                    <section class="cs-empty" ${isListMode && !hasShares ? '' : 'hidden'}>
                        <div class="cs-empty-icon" aria-hidden="true">
                           ${Icons.urlLink}
                        </div>
                        <p class="cs-empty-title">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_empty_title', 'No share link yet'))}</p>
                        <p class="cs-empty-desc">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_empty_desc', 'Create one or more links to share this skill.'))}</p>
                    </section>

                    <section class="cs-form" ${isListMode ? 'hidden' : ''}>
                        <div class="cs-section-head">
                            <span class="cs-section-label">${SkillsUtils.escapeHtml(isInvite ? skillsTranslate('workspace_skills_share_invite_title', 'Invite users') : skillsTranslate('workspace_skills_share_create_title', 'Create new link'))}</span>
                        </div>

                        <div class="cs-field">
                            <label class="cs-field-label">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_type_label', 'Share Type'))}</label>
                            <div class="cs-radio-group" role="radiogroup" aria-label="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_type_aria', 'Skill share type'))}">
                                ${['live', 'collaborate', 'clone'].map((shareType) => `
                                    <label class="cs-radio">
                                        <input type="radio" name="skillsShareType" value="${SkillsUtils.escapeHtml(shareType)}" ${SkillsState.currentShareType === shareType ? 'checked' : ''}>
                                        <div class="cs-radio-content">
                                            <span class="cs-radio-title">${SkillsUtils.escapeHtml(getSkillShareTypeLabel(shareType))}</span>
                                            <span class="cs-radio-desc">${SkillsUtils.escapeHtml(getSkillShareTypeDescription(shareType))}</span>
                                        </div>
                                    </label>
                                `).join('')}
                            </div>
                        </div>

                        <div class="cs-field">
                            <label class="cs-field-label">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_delivery_label', 'Delivery'))}</label>
                            <div class="cs-radio-group" role="radiogroup" aria-label="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_delivery_aria', 'Skill share delivery'))}">
                                <label class="cs-radio">
                                    <input type="radio" name="skillsShareAction" value="link" ${SkillsState.shareAction === 'link' ? 'checked' : ''}>
                                    <div class="cs-radio-content">
                                        <span class="cs-radio-title">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_mode_link', 'Link'))}</span>
                                        <span class="cs-radio-desc">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_delivery_link_desc', 'Generate a reusable link for the selected share type.'))}</span>
                                    </div>
                                </label>
                                <label class="cs-radio">
                                    <input type="radio" name="skillsShareAction" value="invite" ${SkillsState.shareAction === 'invite' ? 'checked' : ''}>
                                    <div class="cs-radio-content">
                                        <span class="cs-radio-title">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_mode_invite', 'Invite Users'))}</span>
                                        <span class="cs-radio-desc">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_delivery_invite_desc', 'Send a workspace invitation with the selected share type.'))}</span>
                                    </div>
                                </label>
                            </div>
                        </div>

                        <div class="cs-field cs-invite-field" id="skillsShareInviteField" ${isInvite ? '' : 'hidden'}>
                            <label class="cs-field-label" for="skillsInviteUserSearch">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_select_users', 'Select Users to Invite'))}</label>
                            <div class="cs-invite-search">
                                ${Icons.magnifyingGlass}
                                <input type="text" id="skillsInviteUserSearch" class="cs-input cs-invite-search-input" placeholder="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_search_placeholder', 'Search users...'))}" aria-describedby="skillsInviteUserError" aria-invalid="false">
                            </div>
                            <p class="cs-field-error" id="skillsInviteUserError" role="alert" hidden></p>
                            <div class="cs-invite-user-list" id="skillsInviteUserList">
                                <div class="cs-invite-state">${SkillsState.publicUsersLoaded ? SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_no_users', 'No users available to invite')) : SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_loading_users', 'Loading users...'))}</div>
                            </div>
                            <div class="cs-invite-selected" id="skillsSelectedUsers" hidden>
                                <div class="cs-invite-selected-head">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_selected', 'Selected'))} (<span id="skillsSelectedCount">0</span>)</div>
                                <div class="cs-invite-selected-list" id="skillsSelectedUsersList"></div>
                            </div>
                        </div>
                    </section>
                </div>

                <footer class="cs-footer shared-modal-footer">
                    <button type="button" class="cs-btn cs-btn-ghost om-button border cancel" id="skillsShareSecondaryBtn">${SkillsUtils.escapeHtml(isListMode ? skillsTranslate('common_done', 'Done') : (hasShares ? skillsTranslate('common_cancel', 'Cancel') : skillsTranslate('common_done', 'Done')))}</button>
                    <button type="button" class="cs-btn cs-btn-primary om-button border submit" id="skillsSharePrimaryBtn">${SkillsUtils.escapeHtml(isListMode ? (hasShares ? skillsTranslate('workspace_skills_share_new_link', 'New link') : skillsTranslate('workspace_skills_share_generate', 'Create link')) : (isInvite ? skillsTranslate('workspace_skills_share_invite_button', 'Invite Selected Users') : skillsTranslate('workspace_skills_share_generate', 'Create Link')))}</button>
                </footer>
            </div>
        `;

        overlay.onclick = (event) => {
            if (event.target === overlay) this.hideShareModal();
        };
        document.getElementById('skillsShareCloseBtn')?.addEventListener('click', () => this.hideShareModal());
        document.getElementById('skillsShareSecondaryBtn')?.addEventListener('click', () => {
            if (SkillsState.shareMode === 'list' || !hasShares) {
                this.hideShareModal();
                return;
            }
            SkillsState.shareMode = 'list';
            SkillsState.shareAction = 'link';
            this.renderShareModal(skill);
        });
        document.getElementById('skillsSharePrimaryBtn')?.addEventListener('click', async () => {
            if (SkillsState.shareMode === 'list') {
                SkillsState.shareMode = 'create';
                SkillsState.shareAction = 'link';
                this.renderShareModal(skill);
                return;
            }
            if (this.getShareAction() === 'invite') {
                await this.sendInvitations();
                return;
            }
            await this.generateShareLink();
        });

        overlay.querySelectorAll('input[name="skillsShareType"]').forEach((input) => {
            input.addEventListener('change', () => {
                SkillsState.currentShareType = input.value;
                this.renderShareModal(skill);
            });
        });
        overlay.querySelectorAll('input[name="skillsShareAction"]').forEach((input) => {
            input.addEventListener('change', () => {
                SkillsState.shareAction = input.value;
                this.renderShareModal(skill);
            });
        });

        this.bindSkillShareLinkActions(skill);

        const inviteSearch = document.getElementById('skillsInviteUserSearch');
        inviteSearch?.addEventListener('input', (event) => {
            this.filterInviteUsers(event.target.value);
            if (SkillsState.selectedUserIds.length) this.clearInviteSelectionError();
        });
        if (SkillsState.shareAction === 'invite') {
            if (SkillsState.publicUsersLoaded) {
                this.filterInviteUsers(inviteSearch?.value || '');
            } else {
                void this.loadPublicUsers();
            }
        }
    },

    renderSkillShareLinkCard(share) {
        const shareUrl = `${window.location.origin}/skills/${share.type}/${share.id}`;
        const subscriberChip = share.count
            ? `<span class="cs-chip cs-chip-muted">${SkillsUtils.escapeHtml(skillsPlural(share.count, 'workspace_skills_share_subscribers_one', '1 subscriber', 'workspace_skills_share_subscribers_other', '{count} subscribers', { count: share.count }))}</span>`
            : '';
        return `
            <div class="cs-link-card" data-share-type="${SkillsUtils.escapeHtml(share.type)}" data-share-url="${SkillsUtils.escapeHtml(shareUrl)}">
                <div class="cs-link-url-row">
                    <input type="text" class="cs-link-url" value="${SkillsUtils.escapeHtml(shareUrl)}" readonly aria-label="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_link_aria', 'Skill share link'))}">
                </div>
                <div class="cs-link-meta">
                    <span class="cs-chip">${SkillsUtils.escapeHtml(getSkillShareTypeLabel(share.type))}</span>
                    ${subscriberChip}
                </div>
                <div class="cs-link-actions">
                    <button type="button" class="om-button border cancel" data-action="copy">
                        ${Icons.copy}
                        ${SkillsUtils.escapeHtml(skillsTranslate('us_shared_items_action_copy', 'Copy'))}
                    </button>
                    <button type="button" class="om-button border cancel" data-action="open">
                        ${Icons.open_window}
                        ${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_action_open', 'Open'))}
                    </button>
                    <button type="button" class="om-button border cancel" data-action="edit">
                        ${Icons.create}
                        ${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_action_edit', 'Edit'))}
                    </button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete">
                        ${Icons.trash}
                        ${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_action_delete', 'Delete'))}
                    </button>
                </div>
            </div>
        `;
    },

    bindSkillShareLinkActions(skill) {
        const overlay = document.getElementById('skillsShareOverlay');
        if (!overlay) return;
        overlay.querySelectorAll('.cs-link-card').forEach((card) => {
            const shareType = card.dataset.shareType;
            const shareUrl = card.dataset.shareUrl;
            card.querySelector('[data-action="copy"]')?.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(shareUrl);
                    if (typeof showNotification === 'function') {
                        showNotification(skillsTranslate('workspace_skills_share_copied', 'Copied!'), 'success');
                    }
                } catch (error) {
                    console.error('Copy failed:', error);
                }
            });
            card.querySelector('[data-action="open"]')?.addEventListener('click', () => {
                if (shareUrl) window.open(shareUrl, '_blank', 'noopener,noreferrer');
            });
            card.querySelector('[data-action="edit"]')?.addEventListener('click', () => {
                SkillsState.shareMode = 'create';
                SkillsState.shareAction = 'link';
                SkillsState.currentShareType = shareType;
                this.renderShareModal(skill);
            });
            card.querySelector('[data-action="delete"]')?.addEventListener('click', async () => {
                await this.stopSharingByType(shareType);
            });
        });
    },

    async loadPublicUsers() {
        const userList = document.getElementById('skillsInviteUserList');
        if (!userList || SkillsState.publicUsersLoading || SkillsState.publicUsersLoaded) return;

        SkillsState.publicUsersLoading = true;
        userList.innerHTML = `<div class="cs-invite-state">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_loading_users', 'Loading users...'))}</div>`;
        try {
            SkillsState.publicUsers = await SkillsAPI.fetchPublicUsers();
            SkillsState.publicUsersLoaded = true;
            this.filterInviteUsers('');
        } catch (error) {
            console.error('Failed to load public users:', error);
            userList.innerHTML = `<div class="cs-invite-state">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_load_users_error', 'Failed to load users'))}</div>`;
        } finally {
            SkillsState.publicUsersLoading = false;
        }
    },

    renderInviteUserList(users = []) {
        const userList = document.getElementById('skillsInviteUserList');
        if (!userList) return;
        if (!users.length) {
            userList.innerHTML = `<div class="cs-invite-state">${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_invite_no_users', 'No users available to invite'))}</div>`;
            return;
        }

        userList.innerHTML = users.map((user) => {
            const isSelected = SkillsState.selectedUserIds.includes(user.id);
            const label = user.display_name || user.id || skillsTranslate('workspace_skills_share_unknown_user', 'Unknown user');
            return `
                <button type="button" class="cs-invite-user-item ${isSelected ? 'is-selected' : ''}" data-user-id="${SkillsUtils.escapeHtml(user.id)}">
                    <span class="cs-invite-avatar">${SkillsUtils.escapeHtml(this.getUserInitials(user))}</span>
                    <span class="cs-invite-user-info">
                        <span class="cs-invite-user-name">${SkillsUtils.escapeHtml(label)}</span>
                                            </span>
                    <span class="cs-invite-check" aria-hidden="true">
                        ${Icons.check}
                    </span>
                </button>
            `;
        }).join('');

        userList.querySelectorAll('.cs-invite-user-item').forEach((item) => {
            item.addEventListener('click', () => this.toggleUserSelection(item.dataset.userId));
        });
    },

    getUserInitials(user = {}) {
        if (user.first_name && user.last_name) {
            return (user.first_name[0] + user.last_name[0]).toUpperCase();
        }
        if (user.first_name) {
            return user.first_name.substring(0, 2).toUpperCase();
        }
        if (user.display_name) {
            return user.display_name.substring(0, 2).toUpperCase();
        }
        return '??';
    },

    showInviteSelectionError() {
        const input = document.getElementById('skillsInviteUserSearch');
        const error = document.getElementById('skillsInviteUserError');
        const message = skillsTranslate('chat_share_invite_select_error', 'Select at least one user to invite.');
        if (window.FormValidation?.showInputError) {
            window.FormValidation.showInputError(input, error, message, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        if (error) {
            error.textContent = message;
            error.hidden = false;
        }
        input?.classList.add('cs-input-error');
        input?.setAttribute('aria-invalid', 'true');
        input?.focus();
    },

    clearInviteSelectionError() {
        const input = document.getElementById('skillsInviteUserSearch');
        const error = document.getElementById('skillsInviteUserError');
        if (window.FormValidation?.clearInputError) {
            window.FormValidation.clearInputError(input, error, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        if (error) {
            error.hidden = true;
            error.textContent = '';
        }
        input?.classList.remove('cs-input-error');
        input?.setAttribute('aria-invalid', 'false');
    },

    toggleUserSelection(userId) {
        if (!userId) return;
        const idx = SkillsState.selectedUserIds.indexOf(userId);
        if (idx >= 0) {
            SkillsState.selectedUserIds.splice(idx, 1);
        } else {
            SkillsState.selectedUserIds.push(userId);
        }
        if (SkillsState.selectedUserIds.length) this.clearInviteSelectionError();
        this.updateSelectedUsersUI();
    },

    updateSelectedUsersUI() {
        const selectedSection = document.getElementById('skillsSelectedUsers');
        const selectedList = document.getElementById('skillsSelectedUsersList');
        const selectedCount = document.getElementById('skillsSelectedCount');
        const userItems = document.querySelectorAll('#skillsInviteUserList .cs-invite-user-item');

        userItems.forEach(item => {
            if (SkillsState.selectedUserIds.includes(item.dataset.userId)) {
                item.classList.add('is-selected');
            } else {
                item.classList.remove('is-selected');
            }
        });

        if (!SkillsState.selectedUserIds.length) {
            if (selectedSection) selectedSection.hidden = true;
            if (selectedList) selectedList.innerHTML = '';
            if (selectedCount) selectedCount.textContent = '0';
            return;
        }

        if (selectedSection) selectedSection.hidden = false;
        if (selectedCount) selectedCount.textContent = SkillsState.selectedUserIds.length;

        const selectedUsers = SkillsState.publicUsers.filter(u => SkillsState.selectedUserIds.includes(u.id));
        if (selectedList) {
            selectedList.innerHTML = selectedUsers.map(user => `
                <span class="cs-invite-selected-chip">
                    <span>${SkillsUtils.escapeHtml(user.display_name || user.id || skillsTranslate('workspace_skills_share_unknown_user', 'Unknown user'))}</span>
                    <button type="button" data-user-id="${SkillsUtils.escapeHtml(user.id)}" aria-label="${SkillsUtils.escapeHtml(skillsTranslate('workspace_skills_share_remove_user_aria', 'Remove user'))}">
                       ${Icons.close}
                    </button>
                </span>
            `).join('');
            selectedList.querySelectorAll('button[data-user-id]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.toggleUserSelection(btn.dataset.userId);
                });
            });
        }
    },

    filterInviteUsers(searchTerm = '') {
        const term = searchTerm.toLowerCase().trim();
        const filtered = term
            ? SkillsState.publicUsers.filter(u =>
                (u.display_name && u.display_name.toLowerCase().includes(term)) ||
                false)
            : SkillsState.publicUsers;
        this.renderInviteUserList(filtered);
        this.updateSelectedUsersUI();
    },

    async sendInvitations() {
        const skillId = SkillsState.sharingSkillId;
        if (!skillId) return;
        if (SkillsState.selectedUserIds.length === 0) {
            this.showInviteSelectionError();
            return;
        }

        const btn = document.getElementById('skillsSharePrimaryBtn');
        if (btn) btn.disabled = true;

        try {
            const result = await SkillsAPI.inviteUsersToSkill(skillId, SkillsState.selectedUserIds, this.getShareTypeSelection());

            if (typeof showNotification === 'function') {
                const invitedCount = result.invited_count || SkillsState.selectedUserIds.length;
                showNotification(
                    skillsPlural(
                        invitedCount,
                        'workspace_skills_share_invite_success_one',
                        'Invited 1 user',
                        'workspace_skills_share_invite_success_other',
                        'Invited {count} users',
                        { count: invitedCount },
                    ),
                    'success',
                );
            }

            SkillsState.selectedUserIds = [];
            SkillsState.shareMode = 'list';
            this.renderShareModal(SkillsState.skills.find(s => s.id === skillId));
        } catch (error) {
            console.error('Failed to send invitations:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_share_invite_error', 'Failed to send invitations')),
                    'error',
                );
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    async loadShareStatus(skillId) {
        try {
            SkillsState.shareStatus = await SkillsAPI.getShareStatus(skillId);
            const idx = SkillsState.skills.findIndex(s => s.id === skillId);
            if (idx >= 0) {
                SkillsState.skills[idx].clone_share_id = SkillsState.shareStatus.clone_share_id;
                SkillsState.skills[idx].live_share_id = SkillsState.shareStatus.live_share_id;
                SkillsState.skills[idx].collaborate_share_id = SkillsState.shareStatus.collaborate_share_id;
            }
            this.renderShareModal(SkillsState.skills.find(s => s.id === skillId));
        } catch (error) {
            console.error('Failed to load share status:', error);
        }
    },

    async generateShareLink() {
        const skillId = SkillsState.sharingSkillId;
        if (!skillId) return;

        const btn = document.getElementById('skillsSharePrimaryBtn');
        if (btn) btn.disabled = true;
        try {
            const shareData = await SkillsAPI.shareSkill(skillId, this.getShareTypeSelection());
            const shareUrl = `${window.location.origin}${shareData.share_url}`;
            try {
                await navigator.clipboard.writeText(shareUrl);
            } catch (_) {
                // ignore clipboard failure
            }
            await this.loadShareStatus(skillId);
            this.renderSkills();
            SkillsState.shareMode = 'list';
            this.renderShareModal(SkillsState.skills.find(s => s.id === skillId));
        } catch (error) {
            console.error('Failed to generate share link:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_share_error_generate', 'Failed to generate share link')),
                    'error',
                );
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    hideShareModal() {
        const overlay = document.getElementById('skillsShareOverlay');
        if (overlay) {
            overlay.classList.remove('cs-active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        SkillsState.sharingSkillId = null;
        SkillsState.shareMode = 'list';
    },

    async stopSharingByType(shareType) {
        const skillId = SkillsState.sharingSkillId;
        if (!skillId) return;

        try {
            await SkillsAPI.deleteShare(skillId, shareType);
            await this.loadShareStatus(skillId);
            this.renderSkills();
            
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslate('workspace_skills_share_stopped', '{shareType} sharing stopped', {
                        shareType: getSkillShareTypeLabel(shareType),
                    }),
                    'success',
                );
            }
        } catch (error) {
            console.error('Failed to stop sharing:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_share_error_stop', 'Failed to stop sharing')),
                    'error',
                );
            }
        }
    },

    // ========================================================================
    // Accept Shared Skill Methods
    // ========================================================================

    async showAcceptModal(shareId, shareType = null) {
        SkillsState.pendingShareId = shareId;
        SkillsState.pendingShareType = shareType;
        
        const overlay = document.getElementById('skillAcceptOverlay');
        if (!overlay) return;

        const titleEl = document.getElementById('skillAcceptTitle');
        const ownerEl = document.getElementById('skillAcceptOwner');
        const previewEl = document.getElementById('skillAcceptPreviewContent');
        const confirmBtn = document.getElementById('skillAcceptConfirmBtn');
        const shareTypeInfoEl = document.getElementById('skillAcceptShareTypeInfo');

        titleEl.textContent = skillsTranslate('workspace_skills_accept_loading', 'Loading...');
        if (ownerEl) ownerEl.textContent = '';
        if (previewEl) previewEl.innerHTML = '';
        if (shareTypeInfoEl) shareTypeInfoEl.innerHTML = '';
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = skillsButtonContent(
                Icons.plus,
                skillsTranslate('workspace_skills_accept_add', 'Add to My Skills'),
            );
        }

        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('active'));

        if (!SkillsState.acceptModalInitialized) {
            document.getElementById('skillAcceptCancelBtn')?.addEventListener('click', () => this.hideAcceptModal());
            document.getElementById('skillAcceptCloseBtn')?.addEventListener('click', () => this.hideAcceptModal());
            document.getElementById('skillAcceptConfirmBtn')?.addEventListener('click', () => this.confirmAcceptShared());
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideAcceptModal(); });
            SkillsState.acceptModalInitialized = true;
        }

        try {
            const data = await SkillsAPI.getSharedSkillPreview(shareId);
            titleEl.textContent = data.title || skillsTranslate('workspace_skills_untitled', 'Untitled Skill');
            if (ownerEl) {
                ownerEl.textContent = data.owner_name
                    ? skillsTranslate('workspace_skills_shared_by', 'Shared by {name}', { name: data.owner_name })
                    : '';
            }
            
            SkillsState.pendingShareType = data.share_type || shareType;
            
            if (shareTypeInfoEl) {
                const typeLabels = {
                    'clone': { label: skillsTranslate('workspace_skills_accept_type_clone_label', 'Clone'), desc: skillsTranslate('workspace_skills_accept_type_clone_desc', 'You will get your own copy that you can edit and delete freely.'), color: '#8b5cf6' },
                    'live': { label: skillsTranslate('workspace_skills_accept_type_live_label', 'Live View'), desc: skillsTranslate('workspace_skills_accept_type_live_desc', 'View-only with live updates. You cannot edit this skill.'), color: '#3b82f6' },
                    'collaborate': { label: skillsTranslate('workspace_skills_accept_type_collaborate_label', 'Collaborate'), desc: skillsTranslate('workspace_skills_accept_type_collaborate_desc', 'You can view and possibly edit this skill with live sync.'), color: '#10b981' },
                };
                const typeInfo = typeLabels[data.share_type] || typeLabels['live'];
                shareTypeInfoEl.innerHTML = `
                    <div style="background-color: ${typeInfo.color}20; border: 1px solid ${typeInfo.color}40; border-radius: 8px; padding: 10px 12px;">
                        <span style="color: ${typeInfo.color}; font-weight: 600; font-size: 0.85rem;">${typeInfo.label}</span>
                        <span style="display: block; font-size: 0.8rem; color: var(--text-color-secondary); margin-top: 2px;">${typeInfo.desc}</span>
                    </div>
                `;
            }
            
            if (data.content_preview) {
                if (previewEl) {
                    previewEl.innerHTML = `<pre style="margin: 0; white-space: pre-wrap; font-family: inherit;">${SkillsUtils.escapeHtml(data.content_preview)}</pre>`;
                }
            } else {
                if (previewEl) {
                    previewEl.innerHTML = `<p style="color: var(--text-color-secondary);">${skillsTranslate('workspace_skills_accept_no_preview', 'No preview available')}</p>`;
                }
            }
            
            if (data.share_type === 'clone') {
                confirmBtn.innerHTML = skillsButtonContent(
                    Icons.copy,
                    skillsTranslate('workspace_skills_accept_clone', 'Clone to My Skills'),
                );
            } else {
                confirmBtn.innerHTML = skillsButtonContent(
                    Icons.plus,
                    skillsTranslate('workspace_skills_accept_add', 'Add to My Skills'),
                );
            }
            
            confirmBtn.disabled = false;
        } catch (error) {
            const isOwnerError = error && error.status === 400;
            if (isOwnerError) {
                console.warn('Owner attempted to open own shared skill');
                this.hideAcceptModal();
                const warnMessage = skillsTranslateBackendDetail(error?.message, skillsTranslate('workspace_skills_accept_owner_error', 'You cannot open your own shared skill.'));
                if (typeof notifyWarning === 'function') {
                    notifyWarning(warnMessage);
                } else if (typeof showNotification === 'function') {
                    showNotification(warnMessage, 'warning');
                }
                if (typeof window !== 'undefined') {
                    const path = window.location.pathname;
                    const isSharePath = /\/skills\/(clone|live|collaborate)\//.test(path);
                    if (isSharePath) {
                        history.replaceState(null, '', '/workspace/skills');
                    }
                }
                return;
            }
            console.error('Failed to load shared skill preview:', error);
            titleEl.textContent = skillsTranslate('workspace_skills_accept_error_title', 'Error loading skill');
            if (previewEl) {
                previewEl.innerHTML = `<p style="color: #ef4444;">${skillsTranslateBackendDetail(error?.message, skillsTranslate('workspace_skills_accept_error_body', 'Could not load this shared skill. It may no longer exist.'))}</p>`;
            }
        }
    },

    hideAcceptModal() {
        const overlay = document.getElementById('skillAcceptOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        SkillsState.pendingShareId = null;
        SkillsState.pendingShareType = null;
    },

    async confirmAcceptShared() {
        const shareId = SkillsState.pendingShareId;
        const shareType = SkillsState.pendingShareType;
        if (!shareId) return;

        const confirmBtn = document.getElementById('skillAcceptConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = skillsButtonContent(
                Icons.omlorix,
                skillsTranslate('workspace_skills_accept_processing', 'Processing...'),
            );
        }

        try {
            if (shareType === 'clone') {
                await SkillsAPI.cloneSkill(shareId);
            } else {
                await SkillsAPI.acceptSharedSkill(shareId);
            }
            
            this.hideAcceptModal();
            notifyWorkspaceSkillsChanged({ reason: shareType === 'clone' ? 'cloned' : 'subscribed' });
            await this.loadSkills();
            
            if (typeof showNotification === 'function') {
                showNotification(
                    shareType === 'clone'
                        ? skillsTranslate('workspace_skills_accept_success_clone', 'Skill cloned successfully')
                        : skillsTranslate('workspace_skills_accept_success_added', 'Skill added to your workspace'),
                    'success',
                );
            }
            
            const path = window.location.pathname;
            if (path.includes('/skills/clone/') || path.includes('/skills/live/') || path.includes('/skills/collaborate/')) {
                history.replaceState(null, '', '/workspace/skills');
            }
        } catch (error) {
            console.error('Failed to accept shared skill:', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error?.message, skillsTranslate('workspace_skills_accept_error_add', 'Failed to add skill')),
                    'error',
                );
            }
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = skillsButtonContent(
                    Icons.plus,
                    skillsTranslate('workspace_skills_accept_add', 'Add to My Skills'),
                );
            }
        }
    },

    checkForSharedSkillLink() {
        const path = window.location.pathname;
        
        const cloneMatch = path.match(/\/skills\/clone\/([a-zA-Z0-9-]+)/);
        if (cloneMatch) {
            this.showAcceptModal(cloneMatch[1], 'clone');
            return true;
        }
        
        const liveMatch = path.match(/\/skills\/live\/([a-zA-Z0-9-]+)/);
        if (liveMatch) {
            this.showAcceptModal(liveMatch[1], 'live');
            return true;
        }
        
        const collaborateMatch = path.match(/\/skills\/collaborate\/([a-zA-Z0-9-]+)/);
        if (collaborateMatch) {
            this.showAcceptModal(collaborateMatch[1], 'collaborate');
            return true;
        }
        
        return false;
    },

    // ========================================================================
    // Marketplace Import Methods
    // ========================================================================

    getSkillsFeatureEnabled() {
        if (typeof window === 'undefined' || !window.chatSetup) {
            return null;
        }
        if (!Object.prototype.hasOwnProperty.call(window.chatSetup, 'enable_skills')) {
            return null;
        }
        return Boolean(window.chatSetup.enable_skills);
    },

    notifySkillsFeatureDisabled() {
        const message = (typeof window !== 'undefined' && typeof window.getTranslation === 'function')
            ? window.getTranslation('workspace_skills_feature_disabled', 'This feature is not enabled for your account.')
            : skillsTranslate('workspace_skills_feature_disabled', 'This feature is not enabled for your account.');

        if (typeof window !== 'undefined' && typeof window.notifyError === 'function') {
            window.notifyError(message);
            return;
        }
        if (typeof showNotification === 'function') {
            showNotification(message, 'error');
        }
    },

    checkForMarketplaceImport() {
        const urlParams = new URLSearchParams(window.location.search);
        const isMarketplaceImport = urlParams.get('marketplace_import') === '1';
        
        if (!isMarketplaceImport) return false;

        const skillsEnabled = this.getSkillsFeatureEnabled();
        if (skillsEnabled === null) {
            if (!SkillsState.marketplaceImportAwaitingChatSetup) {
                SkillsState.marketplaceImportAwaitingChatSetup = true;
                document.addEventListener('chatSetupReady', () => {
                    SkillsState.marketplaceImportAwaitingChatSetup = false;
                    this.checkForMarketplaceImport();
                }, { once: true });
                // Immediate re-check in case chatSetupReady already fired
                if (window.chatSetup) {
                    SkillsState.marketplaceImportAwaitingChatSetup = false;
                    this.checkForMarketplaceImport();
                }
            }
            return false;
        }
        if (!skillsEnabled) {
            this.notifySkillsFeatureDisabled();
            this.clearMarketplaceImportParams();
            return false;
        }
        
        const encodedData = urlParams.get('skill_data');
        const timestamp = urlParams.get('ts');
        
        if (!encodedData || !timestamp) {
            console.warn('Marketplace import: Missing required parameters');
            this.clearMarketplaceImportParams();
            return false;
        }
        
        // Validate timestamp (must be recent and not from the future).
        const importTime = parseInt(timestamp, 10);
        const now = Date.now();
        const maxAge = 30 * 60 * 1000; // 30 minutes
        const maxFutureSkew = 5 * 60 * 1000; // 5 minutes
        
        if (isNaN(importTime) || now - importTime > maxAge || importTime - now > maxFutureSkew) {
            console.warn('Marketplace import: Import link has expired');
            if (typeof showNotification === 'function') {
                showNotification(skillsTranslate('workspace_skills_marketplace_link_expired', 'This import link has expired. Please request a new import link.'), 'warning');
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
                throw new Error(skillsTranslate('workspace_skills_marketplace_invalid_data', 'Invalid import data. Please request a new import link.'));
            }
            
            // Sanitize the data
            SkillsState.marketplaceImportData = {
                name: this.sanitizeSkillName(skillData.name),
                description: this.sanitizeText(skillData.description || ''),
                content: this.sanitizeText(skillData.content || ''),
                category: this.sanitizeText(skillData.category || 'general'),
                version: this.sanitizeText(skillData.version || '1.0.0'),
                author: this.sanitizeText(skillData.author || skillsTranslate('workspace_skills_marketplace_unknown_author', 'Unknown source')),
                source: 'url_import',
                sourceUrl: this.sanitizeUrl(skillData.sourceUrl || ''),
                importedAt: skillData.importedAt || new Date().toISOString(),
            };
            
            // Show the import confirmation modal
            this.showMarketplaceImportModal();
            return true;
            
        } catch (error) {
            console.error('Marketplace import: Failed to parse skill data', error);
            if (typeof showNotification === 'function') {
                showNotification(skillsTranslate('workspace_skills_marketplace_parse_error', 'Failed to parse skill data. Please try again.'), 'error');
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
        const data = SkillsState.marketplaceImportData;
        if (!data) return;

        // Create modal if it doesn't exist
        let overlay = document.getElementById('marketplaceImportOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'marketplaceImportOverlay';
            overlay.className = 'marketplace-import-overlay shared-modal-overlay';
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
            document.body.appendChild(overlay);
        }

        overlay.innerHTML = `
            <div class="marketplace-import-modal shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="marketplaceImportTitle" tabindex="-1">
                <div class="marketplace-import-modal-header shared-modal-header shared-modal-header--main">
                    <div class="marketplace-import-icon" id="marketplaceImportIcon">
                        ${Icons.lightning}
                    </div>
                    <div class="marketplace-import-header-text shared-modal-heading">
                        <p class="marketplace-import-badge">
                            ${Icons.globe}
                            ${skillsTranslate('workspace_skills_marketplace_badge', 'Unverified skill import')}
                        </p>
                        <h3 class="marketplace-import-title shared-modal-title" id="marketplaceImportTitle">${skillsTranslate('workspace_skills_marketplace_title_fallback', 'Skill')}</h3>
                        <p class="marketplace-import-meta shared-modal-subtitle" id="marketplaceImportMeta"></p>
                    </div>
                    <button type="button" class="om-button shared-modal-close" id="marketplaceImportCloseBtn" aria-label="${SkillsUtils.escapeHtml(skillsTranslate('common_cancel', 'Cancel'))}">
                        ${Icons.close}
                    </button>
                </div>
                <div class="marketplace-import-modal-body shared-modal-body">
                    <div class="marketplace-import-description" id="marketplaceImportDescription"></div>
                    <div class="marketplace-import-preview-section">
                        <label class="marketplace-import-preview-label">${skillsTranslate('workspace_skills_marketplace_preview_label', 'Skill Content Preview')}</label>
                        <div class="marketplace-import-preview" id="marketplaceImportPreview"></div>
                    </div>
                    <div class="marketplace-import-security-note">
                        ${Icons.security}
                        <p>${skillsTranslate('workspace_skills_marketplace_security_note', 'This import link is not verified by Omlorix. Only import skills from sources you trust; you can edit or delete the skill at any time.')}</p>
                    </div>
                </div>
                <div class="marketplace-import-modal-footer shared-modal-footer">
                    <button type="button" class="om-button border cancel" id="marketplaceImportCancelBtn">${skillsTranslate('common_cancel', 'Cancel')}</button>
                    <button type="button" class="om-button border submit" id="marketplaceImportConfirmBtn">
                        ${Icons.download}
                        ${skillsTranslate('workspace_skills_import_confirm', 'Import Skill')}
                    </button>
                </div>
            </div>
        `;

        // Populate modal content
        const titleEl = document.getElementById('marketplaceImportTitle');
        const metaEl = document.getElementById('marketplaceImportMeta');
        const descEl = document.getElementById('marketplaceImportDescription');
        const previewEl = document.getElementById('marketplaceImportPreview');
        const iconEl = document.getElementById('marketplaceImportIcon');

        if (titleEl) titleEl.textContent = data.name.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        if (metaEl) {
            const metaParts = [];
            if (data.category) metaParts.push(data.category);
            if (data.version) metaParts.push(`v${data.version}`);
            if (data.author) metaParts.push(skillsTranslate('workspace_skills_marketplace_meta_by', 'by {author}', { author: data.author }));
            metaEl.textContent = metaParts.join(' • ');
        }
        if (descEl) {
            descEl.textContent = data.description || skillsTranslate('workspace_skills_marketplace_no_description', 'No description provided.');
        }
        if (previewEl) {
            const preview = data.content.length > 500 
                ? data.content.substring(0, 500) + '...' 
                : data.content;
            previewEl.innerHTML = `<pre>${SkillsUtils.escapeHtml(preview)}</pre>`;
        }

        // Set icon based on category
        if (iconEl) {
            const sharedIcons = globalThis.Icons || {};
            const categoryIcons = {
                "development": sharedIcons.code,
                "rendering": sharedIcons.grid,
                "design": sharedIcons.sun,
                "language": sharedIcons.globe,
                "general": sharedIcons.layout,
            };
            iconEl.innerHTML = categoryIcons[data.category] || categoryIcons.general || '';
        }

        // Show overlay
        overlay._previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        requestAnimationFrame(() => overlay.classList.add('active'));

        // Setup event listeners (only once)
        if (!SkillsState.marketplaceImportModalInitialized) {
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideMarketplaceImportModal(); });
            
            // Escape key to close
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && overlay.classList.contains('active')) {
                    e.preventDefault();
                    this.hideMarketplaceImportModal();
                    return;
                }
                if (e.key === 'Tab' && overlay.classList.contains('active')) {
                    const dialog = overlay.querySelector('[role="dialog"]');
                    const focusable = Array.from(dialog?.querySelectorAll(
                        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
                    ) || []).filter((element) => !element.hidden && element.getClientRects().length > 0);
                    const first = focusable[0];
                    const last = focusable[focusable.length - 1];
                    if (!first) {
                        e.preventDefault();
                        dialog?.focus({ preventScroll: true });
                    } else if (e.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                        e.preventDefault();
                        last.focus();
                    } else if (!e.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                        e.preventDefault();
                        first.focus();
                    }
                }
            });
            
            SkillsState.marketplaceImportModalInitialized = true;
        }

        document.getElementById('marketplaceImportCloseBtn')?.addEventListener('click', () => this.hideMarketplaceImportModal());
        document.getElementById('marketplaceImportCancelBtn')?.addEventListener('click', () => this.hideMarketplaceImportModal());
        document.getElementById('marketplaceImportConfirmBtn')?.addEventListener('click', () => this.confirmMarketplaceImport());
        requestAnimationFrame(() => document.getElementById('marketplaceImportCloseBtn')?.focus({ preventScroll: true }));
    },

    hideMarketplaceImportModal() {
        const overlay = document.getElementById('marketplaceImportOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            overlay.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('modal-open');
            const previousFocus = overlay._previousFocus;
            overlay._previousFocus = null;
            setTimeout(() => {
                overlay.setAttribute('hidden', '');
                if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
            }, 200);
        }
        SkillsState.marketplaceImportData = null;
        this.clearMarketplaceImportParams();
    },

    // ========================================================================
    // Import Skill from Markdown Methods
    // ========================================================================

    _importState: {
        activeTab: 'file',
        pendingMarkdown: null,
        parsedData: null,
        fileEntries: [],
        fileReadToken: null,
        initialized: false,
    },

    showImportModal() {
        this._importState.activeTab = 'file';
        this._importState.pendingMarkdown = null;
        this._importState.parsedData = null;
        this._importState.fileEntries = [];
        this._importState.fileReadToken = null;

        const overlay = SkillsDOM.skillImportOverlay;
        if (!overlay) return;

        // Reset UI
        this._importResetUI();

        // Setup listeners once
        if (!this._importState.initialized) {
            this._importSetupListeners();
            this._importState.initialized = true;
        }

        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => SkillsDOM.skillImportTabFile?.focus());
    },

    hideImportModal() {
        const overlay = SkillsDOM.skillImportOverlay;
        if (!overlay) return;
        overlay.setAttribute('hidden', '');
        overlay.setAttribute('aria-hidden', 'true');
        this._importState.pendingMarkdown = null;
        this._importState.parsedData = null;
        this._importState.fileEntries = [];
        this._importState.fileReadToken = null;
    },

    _importResetUI() {
        // Tab: file active
        this._importSwitchTab('file');

        // Clear dropzone state
        SkillsDOM.skillImportDropzone?.classList.remove('drag-over');

        // Clear file selection
        const fi = SkillsDOM.skillImportFileInput;
        if (fi) fi.value = '';
        SkillsDOM.skillImportDropzoneContent?.removeAttribute('hidden');
        SkillsDOM.skillImportFileSelected?.setAttribute('hidden', '');
        if (SkillsDOM.skillImportFileName) SkillsDOM.skillImportFileName.textContent = '';
        if (SkillsDOM.skillImportFileSize) SkillsDOM.skillImportFileSize.textContent = '';
        if (SkillsDOM.skillImportFileList) SkillsDOM.skillImportFileList.replaceChildren();
        this._importState.fileEntries = [];
        this._importState.fileReadToken = null;

        // Clear paste
        if (SkillsDOM.skillImportPasteInput) SkillsDOM.skillImportPasteInput.value = '';
        SkillsDOM.skillImportPasteClear?.setAttribute('hidden', '');

        // Clear feedback
        SkillsDOM.skillImportFeedback?.setAttribute('hidden', '');
        SkillsDOM.skillImportError?.setAttribute('hidden', '');
        SkillsDOM.skillImportPreview?.setAttribute('hidden', '');

        // Disable confirm
        const btn = SkillsDOM.skillImportConfirmBtn;
        if (btn) {
            btn.disabled = true;
            btn.classList.remove('loading');
        }
        if (SkillsDOM.skillImportConfirmText) {
            SkillsDOM.skillImportConfirmText.textContent = skillsTranslate('workspace_skills_import_confirm', 'Import Skill');
        }
    },

    _importSetupListeners() {
        // Close / cancel
        SkillsDOM.skillImportCloseBtn?.addEventListener('click', () => this.hideImportModal());
        SkillsDOM.skillImportCancelBtn?.addEventListener('click', () => this.hideImportModal());
        SkillsDOM.skillImportOverlay?.addEventListener('click', (e) => {
            if (e.target === SkillsDOM.skillImportOverlay) this.hideImportModal();
        });
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((eventName) => {
            SkillsDOM.skillImportOverlay?.addEventListener(eventName, (e) => {
                // Drops outside the inner dropzone still belong to this modal:
                // suppress browser navigation and prevent global upload zones
                // from treating them as chat or Workspace Files attachments.
                if (e.target.closest('#skillImportDropzone')) return;
                e.preventDefault();
                e.stopPropagation();
            });
        });

        // Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && SkillsDOM.skillImportOverlay && !SkillsDOM.skillImportOverlay.hasAttribute('hidden')) {
                this.hideImportModal();
            }
        });

        // Tabs
        SkillsDOM.skillImportTabFile?.addEventListener('click', () => this._importSwitchTab('file'));
        SkillsDOM.skillImportTabPaste?.addEventListener('click', () => this._importSwitchTab('paste'));

        // File browse button
        SkillsDOM.skillImportBrowseBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            SkillsDOM.skillImportFileInput?.click();
        });

        // Dropzone click
        SkillsDOM.skillImportDropzone?.addEventListener('click', (e) => {
            if (!e.target.closest('.skill-import-file-selected')) {
                SkillsDOM.skillImportFileInput?.click();
            }
        });

        // Drag and drop
        const dz = SkillsDOM.skillImportDropzone;
        if (dz) {
            dz.addEventListener('dragenter', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dz.classList.add('drag-over');
            });
            dz.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dz.classList.add('drag-over');
            });
            dz.addEventListener('dragleave', (e) => {
                e.stopPropagation();
                if (!dz.contains(e.relatedTarget)) dz.classList.remove('drag-over');
            });
            dz.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dz.classList.remove('drag-over');
                const files = Array.from(e.dataTransfer?.files || []);
                if (files.length) this._importHandleFiles(files);
            });
        }

        // File input change
        SkillsDOM.skillImportFileInput?.addEventListener('change', (e) => {
            const files = Array.from(e.target.files || []);
            if (files.length) this._importHandleFiles(files);
        });

        // Clear all selected files.
        SkillsDOM.skillImportFileRemove?.addEventListener('click', (e) => {
            e.stopPropagation();
            this._importClearFiles();
        });

        // Individual remove buttons are rendered after files are read.
        SkillsDOM.skillImportFileList?.addEventListener('click', (e) => {
            const removeButton = e.target.closest('[data-skill-import-remove-index]');
            if (!removeButton) return;
            e.stopPropagation();
            const index = Number(removeButton.dataset.skillImportRemoveIndex);
            if (!Number.isInteger(index)) return;
            this._importState.fileEntries.splice(index, 1);
            const fi = SkillsDOM.skillImportFileInput;
            if (fi) fi.value = '';
            this._importUpdateFileUI();
        });

        // Paste textarea - live validation with debounce
        let pasteDebounce = null;
        SkillsDOM.skillImportPasteInput?.addEventListener('input', (e) => {
            const val = e.target.value.trim();
            const clearBtn = SkillsDOM.skillImportPasteClear;
            if (clearBtn) {
                if (val) clearBtn.removeAttribute('hidden');
                else clearBtn.setAttribute('hidden', '');
            }
            clearTimeout(pasteDebounce);
            if (!val) {
                this._importState.pendingMarkdown = null;
                this._importState.parsedData = null;
                SkillsDOM.skillImportFeedback?.setAttribute('hidden', '');
                SkillsDOM.skillImportError?.setAttribute('hidden', '');
                SkillsDOM.skillImportPreview?.setAttribute('hidden', '');
                const btn = SkillsDOM.skillImportConfirmBtn;
                if (btn) btn.disabled = true;
                return;
            }
            pasteDebounce = setTimeout(() => {
                this._importValidateAndPreview(val);
            }, 400);
        });

        // Clear paste
        SkillsDOM.skillImportPasteClear?.addEventListener('click', () => {
            if (SkillsDOM.skillImportPasteInput) SkillsDOM.skillImportPasteInput.value = '';
            SkillsDOM.skillImportPasteClear?.setAttribute('hidden', '');
            this._importState.pendingMarkdown = null;
            this._importState.parsedData = null;
            SkillsDOM.skillImportFeedback?.setAttribute('hidden', '');
            SkillsDOM.skillImportError?.setAttribute('hidden', '');
            SkillsDOM.skillImportPreview?.setAttribute('hidden', '');
            const btn = SkillsDOM.skillImportConfirmBtn;
            if (btn) btn.disabled = true;
            SkillsDOM.skillImportPasteInput?.focus();
        });

        // Confirm import
        SkillsDOM.skillImportConfirmBtn?.addEventListener('click', () => this._importConfirm());
    },

    _importSwitchTab(tab) {
        this._importState.activeTab = tab;

        const fileTab = SkillsDOM.skillImportTabFile;
        const pasteTab = SkillsDOM.skillImportTabPaste;
        const filePanel = SkillsDOM.skillImportPanelFile;
        const pastePanel = SkillsDOM.skillImportPanelPaste;

        if (tab === 'file') {
            fileTab?.classList.add('active');
            fileTab?.setAttribute('aria-selected', 'true');
            pasteTab?.classList.remove('active');
            pasteTab?.setAttribute('aria-selected', 'false');
            filePanel?.removeAttribute('hidden');
            pastePanel?.setAttribute('hidden', '');
        } else {
            pasteTab?.classList.add('active');
            pasteTab?.setAttribute('aria-selected', 'true');
            fileTab?.classList.remove('active');
            fileTab?.setAttribute('aria-selected', 'false');
            pastePanel?.removeAttribute('hidden');
            filePanel?.setAttribute('hidden', '');
            SkillsDOM.skillImportPasteInput?.focus();
        }

        // Each tab retains its own input, so users can compare an uploaded file
        // with pasted Markdown without losing either draft.
        if (tab === 'file') {
            this._importUpdateFileUI();
        } else if (this._importState.pendingMarkdown && this._importState.parsedData) {
            this._importShowPreview(this._importState.parsedData);
            if (SkillsDOM.skillImportConfirmText) {
                SkillsDOM.skillImportConfirmText.textContent = skillsTranslate('workspace_skills_import_confirm', 'Import Skill');
            }
        } else {
            SkillsDOM.skillImportFeedback?.setAttribute('hidden', '');
            SkillsDOM.skillImportError?.setAttribute('hidden', '');
            SkillsDOM.skillImportPreview?.setAttribute('hidden', '');
            const btn = SkillsDOM.skillImportConfirmBtn;
            if (btn) btn.disabled = true;
        }
    },

    _importClearFiles() {
        const fi = SkillsDOM.skillImportFileInput;
        if (fi) fi.value = '';
        this._importState.fileEntries = [];
        this._importState.fileReadToken = null;
        this._importUpdateFileUI();
    },

    _importReadFile(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (event) => {
                const text = event.target?.result;
                if (typeof text === 'string') resolve(text);
                else reject(new Error(skillsTranslate('workspace_skills_import_file_read_error', 'Failed to read file. Please try again.')));
            };
            reader.onerror = () => {
                reject(new Error(skillsTranslate('workspace_skills_import_file_read_error', 'Failed to read file. Please try again.')));
            };
            reader.readAsText(file);
        });
    },

    async _importHandleFiles(files) {
        const candidates = Array.from(files || []).filter(Boolean);
        if (!candidates.length) return;

        // A token prevents a slow FileReader from a previous selection from
        // overwriting a newer drop or file-picker selection.
        const readToken = Symbol('skill-import-files');
        this._importState.fileReadToken = readToken;
        this._importState.fileEntries = candidates.map((file) => ({
            file,
            markdown: null,
            parsed: null,
            error: null,
        }));
        this._importUpdateFileUI();

        await Promise.all(this._importState.fileEntries.map(async (entry) => {
            const filename = String(entry.file.name || '');
            if (!filename.toLowerCase().endsWith('.md')) {
                entry.error = skillsTranslate('workspace_skills_import_file_type_error', 'Only .md (Markdown) files are accepted.');
                return;
            }
            if (entry.file.size > 1024 * 1024) {
                entry.error = skillsTranslate('workspace_skills_import_file_size_error', 'File is too large. Maximum size is 1 MB.');
                return;
            }
            try {
                entry.markdown = await this._importReadFile(entry.file);
                entry.parsed = this._importParseMarkdown(entry.markdown);
            } catch (error) {
                entry.error = skillsTranslateBackendDetail(
                    error.message,
                    skillsTranslate('workspace_skills_import_invalid_markdown', 'Invalid skill markdown'),
                );
            }
        }));

        if (this._importState.fileReadToken !== readToken) return;
        this._importUpdateFileUI();
    },

    _importUpdateFileUI() {
        const entries = this._importState.fileEntries;
        const selectedCount = entries.length;
        const validEntries = entries.filter((entry) => entry.parsed && entry.markdown && !entry.error);
        const hasInvalidEntries = entries.some((entry) => Boolean(entry.error));

        SkillsDOM.skillImportDropzoneContent?.toggleAttribute('hidden', selectedCount > 0);
        SkillsDOM.skillImportFileSelected?.toggleAttribute('hidden', selectedCount === 0);
        if (SkillsDOM.skillImportFileName) {
            SkillsDOM.skillImportFileName.textContent = skillsPlural(
                selectedCount,
                'workspace_skills_import_selected_files_one',
                '1 skill file selected',
                'workspace_skills_import_selected_files_other',
                '{count} skill files selected',
                { count: selectedCount },
            );
        }
        if (SkillsDOM.skillImportFileSize) {
            const totalBytes = entries.reduce((total, entry) => total + (Number(entry.file?.size) || 0), 0);
            SkillsDOM.skillImportFileSize.textContent = SkillsUtils.formatFileSize(totalBytes);
        }

        const list = SkillsDOM.skillImportFileList;
        if (list) {
            list.innerHTML = entries.map((entry, index) => {
                const filename = entry.file?.name || '';
                const statusText = entry.error
                    || entry.parsed?.name
                    || skillsTranslate('workspace_skills_import_file_reading', 'Reading file...');
                const errorClass = entry.error ? ' is-error' : '';
                const ariaLabel = skillsTranslate(
                    'workspace_skills_import_remove_named_file_aria',
                    'Remove {name}',
                    { name: filename },
                );
                return `
                    <div class="skill-import-file-list-item${errorClass}" role="listitem">
                        <div class="skill-import-file-list-copy">
                            <span class="skill-import-file-list-name">${SkillsUtils.escapeHtml(filename)}</span>
                            <span class="skill-import-file-list-status">${SkillsUtils.escapeHtml(statusText)}</span>
                        </div>
                        <button type="button" class="skill-import-file-remove skill-import-file-list-remove"
                            data-skill-import-remove-index="${index}"
                            aria-label="${SkillsUtils.escapeHtml(ariaLabel)}">${Icons.close}</button>
                    </div>
                `;
            }).join('');
        }

        SkillsDOM.skillImportFeedback?.setAttribute('hidden', '');
        SkillsDOM.skillImportError?.setAttribute('hidden', '');
        SkillsDOM.skillImportPreview?.setAttribute('hidden', '');
        if (selectedCount === 1 && validEntries.length === 1 && this._importState.activeTab === 'file') {
            this._importShowPreview(validEntries[0].parsed);
        }

        const btn = SkillsDOM.skillImportConfirmBtn;
        if (btn) {
            btn.disabled = this._importState.activeTab !== 'file'
                || validEntries.length === 0
                || hasInvalidEntries;
        }
        if (SkillsDOM.skillImportConfirmText && this._importState.activeTab === 'file') {
            SkillsDOM.skillImportConfirmText.textContent = validEntries.length > 1
                ? skillsTranslate('workspace_skills_import_confirm_multiple', 'Import {count} Skills', { count: validEntries.length })
                : skillsTranslate('workspace_skills_import_confirm', 'Import Skill');
        }
    },

    _importParseMarkdown(text) {
        // Must start with ---
        const lines = text.split('\n');
        if (!lines.length || lines[0].trim() !== '---') {
            throw new Error(skillsTranslate('workspace_skills_import_markdown_missing_frontmatter', 'Skill markdown must start with a frontmatter block (---)'));
        }

        // Find closing ---
        let closeIdx = -1;
        for (let i = 1; i < lines.length; i++) {
            if (lines[i].trim() === '---') {
                closeIdx = i;
                break;
            }
        }
        if (closeIdx === -1) {
            throw new Error(skillsTranslate('workspace_skills_import_markdown_missing_frontmatter_end', 'Frontmatter block must end with --- on its own line'));
        }

        // Parse frontmatter
        const frontmatterLines = lines.slice(1, closeIdx);
        const frontmatter = {};
        let i = 0;
        while (i < frontmatterLines.length) {
            const line = frontmatterLines[i];
            const stripped = line.trim();
            if (!stripped) { i++; continue; }

            if (stripped === 'metadata:') {
                i++;
                const meta = {};
                while (i < frontmatterLines.length && frontmatterLines[i].startsWith('  ')) {
                    const [k, ...vParts] = frontmatterLines[i].trim().split(':');
                    if (k) meta[k.trim()] = vParts.join(':').trim().replace(/^"|"$/g, '');
                    i++;
                }
                frontmatter.metadata = meta;
                continue;
            }

            if (stripped.endsWith(': |')) {
                const key = stripped.slice(0, -3).trim();
                i++;
                const blockLines = [];
                while (i < frontmatterLines.length && (frontmatterLines[i].startsWith('  ') || !frontmatterLines[i].trim())) {
                    blockLines.push(frontmatterLines[i].startsWith('  ') ? frontmatterLines[i].slice(2) : '');
                    i++;
                }
                frontmatter[key] = blockLines.join('\n').trimEnd();
                continue;
            }

            const colonIdx = stripped.indexOf(':');
            if (colonIdx > 0) {
                const key = stripped.slice(0, colonIdx).trim();
                const val = stripped.slice(colonIdx + 1).trim().replace(/^"|"$/g, '');
                frontmatter[key] = val;
            }
            i++;
        }

        // Validate required fields
        const name = (frontmatter.name || '').trim();
        const description = (frontmatter.description || '').trim();

        if (!name) {
            throw new Error(skillsTranslate('workspace_skills_import_markdown_missing_name', 'Missing required field: name (e.g., name: my-skill-name)'));
        }
        if (!description) {
            throw new Error(skillsTranslate('workspace_skills_import_markdown_missing_description', 'Missing required field: description (e.g., description: What this skill does)'));
        }

        // Validate name pattern
        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
            throw new Error(skillsTranslate('workspace_skills_import_markdown_invalid_name', 'Invalid name "{name}". Use lowercase letters, numbers, and hyphens only (e.g., my-skill-name)', { name }));
        }

        // Body content
        const body = lines.slice(closeIdx + 1).join('\n').trim();

        return {
            name,
            description,
            license: (frontmatter.license || '').trim() || null,
            compatibility: (frontmatter.compatibility || '').trim() || null,
            metadata: frontmatter.metadata || null,
            body,
        };
    },

    _importValidateAndPreview(text) {
        try {
            const parsed = this._importParseMarkdown(text);
            this._importState.pendingMarkdown = text;
            this._importState.parsedData = parsed;
            this._importShowPreview(parsed);
        } catch (err) {
            this._importState.pendingMarkdown = null;
            this._importState.parsedData = null;
            this._importShowError(skillsTranslateBackendDetail(err.message, skillsTranslate('workspace_skills_import_invalid_markdown', 'Invalid skill markdown')));
        }
    },

    _importShowError(message) {
        const feedback = SkillsDOM.skillImportFeedback;
        const errorEl = SkillsDOM.skillImportError;
        const previewEl = SkillsDOM.skillImportPreview;
        const msgEl = SkillsDOM.skillImportErrorMessage;

        if (msgEl) msgEl.textContent = message;
        errorEl?.removeAttribute('hidden');
        previewEl?.setAttribute('hidden', '');
        feedback?.removeAttribute('hidden');

        const btn = SkillsDOM.skillImportConfirmBtn;
        if (btn) btn.disabled = true;
    },

    _importShowPreview(parsed) {
        const feedback = SkillsDOM.skillImportFeedback;
        const errorEl = SkillsDOM.skillImportError;
        const previewEl = SkillsDOM.skillImportPreview;

        errorEl?.setAttribute('hidden', '');
        previewEl?.removeAttribute('hidden');
        feedback?.removeAttribute('hidden');

        // Name & description
        if (SkillsDOM.skillImportPreviewName) SkillsDOM.skillImportPreviewName.textContent = parsed.name;
        if (SkillsDOM.skillImportPreviewDescription) SkillsDOM.skillImportPreviewDescription.textContent = parsed.description;

        // Meta tags
        const metaEl = SkillsDOM.skillImportPreviewMeta;
        if (metaEl) {
            const tags = [];
            if (parsed.license) {
                tags.push(`<span class="skill-import-meta-tag">${Icons.security}${SkillsUtils.escapeHtml(parsed.license)}</span>`);
            }
            if (parsed.compatibility) {
                tags.push(`<span class="skill-import-meta-tag">${Icons.code}${SkillsUtils.escapeHtml(parsed.compatibility)}</span>`);
            }
            if (parsed.metadata && Object.keys(parsed.metadata).length > 0) {
                const count = Object.keys(parsed.metadata).length;
                tags.push(`<span class="skill-import-meta-tag">${Icons.info}${skillsPlural(count, 'workspace_skills_import_preview_metadata_one', '1 metadata field', 'workspace_skills_import_preview_metadata_other', '{count} metadata fields', { count })}</span>`);
            }
            metaEl.innerHTML = tags.join('');
        }

        // Body preview
        const bodyEl = SkillsDOM.skillImportPreviewBody;
        const bodyTextEl = SkillsDOM.skillImportPreviewBodyText;
        if (parsed.body && bodyEl && bodyTextEl) {
            bodyTextEl.textContent = parsed.body.length > 300 ? parsed.body.slice(0, 300) + '…' : parsed.body;
            bodyEl.removeAttribute('hidden');
        } else if (bodyEl) {
            bodyEl.setAttribute('hidden', '');
        }

        // Enable confirm
        const btn = SkillsDOM.skillImportConfirmBtn;
        if (btn) btn.disabled = false;
    },

    async _importConfirm() {
        const isFileImport = this._importState.activeTab === 'file';
        const fileEntries = this._importState.fileEntries.filter(
            (entry) => entry.file && entry.markdown && entry.parsed && !entry.error,
        );
        const markdown = this._importState.pendingMarkdown;
        const parsed = this._importState.parsedData;
        if (isFileImport ? fileEntries.length === 0 : (!markdown || !parsed)) return;

        const btn = SkillsDOM.skillImportConfirmBtn;
        const btnText = SkillsDOM.skillImportConfirmText;

        if (btn) {
            btn.disabled = true;
            btn.classList.add('loading');
        }
        if (btnText) btnText.textContent = skillsTranslate('workspace_skills_import_confirming', 'Importing...');

        try {
            let response;
            if (isFileImport) {
                const formData = new FormData();
                fileEntries.forEach((entry) => formData.append('files', entry.file, entry.file.name));
                response = await SkillsAPI.request('/api/v1/skills/import-markdown-files', {
                    method: 'POST',
                    credentials: 'include',
                    body: formData,
                });
            } else {
                response = await SkillsAPI.request('/api/v1/skills/import-markdown', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ markdown }),
                });
            }

            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(skillsTranslateBackendDetail(err.detail, skillsTranslate('workspace_skills_import_error', 'Failed to import skill')));
            }

            const batchResult = isFileImport ? await response.json() : null;
            const created = Array.isArray(batchResult?.created) ? batchResult.created : [];
            const errors = Array.isArray(batchResult?.errors) ? batchResult.errors : [];
            if (!isFileImport || created.length > 0) {
                notifyWorkspaceSkillsChanged({ reason: 'imported' });
                await this.loadSkills();
                this.showListScreen();
            }

            if (!isFileImport) {
                this.hideImportModal();
                if (typeof showNotification === 'function') {
                    showNotification(
                        skillsTranslate('workspace_skills_import_success', 'Skill "{name}" imported successfully', { name: parsed.name }),
                        'success',
                    );
                }
                return;
            }

            if (errors.length === 0) {
                this.hideImportModal();
            } else {
                // Successfully created files are removed from the retry state.
                // Failed rows keep their backend message so users can correct
                // the exact documents without accidentally reimporting siblings.
                const errorsByIndex = new Map(errors.map((error) => [error.index, error.error]));
                this._importState.fileEntries = fileEntries
                    .map((entry, index) => ({ entry, index }))
                    .filter(({ index }) => errorsByIndex.has(index))
                    .map(({ entry, index }) => ({
                        ...entry,
                        parsed: null,
                        error: skillsTranslateBackendDetail(
                            errorsByIndex.get(index),
                            skillsTranslate('workspace_skills_import_error', 'Failed to import skill'),
                        ),
                    }));
                this._importUpdateFileUI();
                this._importShowError(skillsPlural(
                    errors.length,
                    'workspace_skills_import_files_failed_one',
                    '1 file could not be imported. Review it below.',
                    'workspace_skills_import_files_failed_other',
                    '{count} files could not be imported. Review them below.',
                    { count: errors.length },
                ));
            }

            if (typeof showNotification === 'function' && created.length > 0) {
                showNotification(
                    errors.length > 0
                        ? skillsTranslate(
                            'workspace_skills_import_files_partial',
                            'Imported {created} skills; {failed} failed',
                            { created: created.length, failed: errors.length },
                        )
                        : skillsPlural(
                            created.length,
                            'workspace_skills_import_files_success_one',
                            'Imported 1 skill successfully',
                            'workspace_skills_import_files_success_other',
                            'Imported {count} skills successfully',
                            { count: created.length },
                        ),
                    errors.length > 0 ? 'warning' : 'success',
                );
            }
        } catch (error) {
            console.error('Import skill failed:', error);
            this._importShowError(skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_import_error_try_again', 'Failed to import skill. Please try again.')));
            if (btn) {
                btn.disabled = isFileImport ? fileEntries.length === 0 : false;
                btn.classList.remove('loading');
            }
            if (btnText) {
                btnText.textContent = isFileImport && fileEntries.length > 1
                    ? skillsTranslate('workspace_skills_import_confirm_multiple', 'Import {count} Skills', { count: fileEntries.length })
                    : skillsTranslate('workspace_skills_import_confirm', 'Import Skill');
            }
        } finally {
            btn?.classList.remove('loading');
        }
    },

    async confirmMarketplaceImport() {
        const data = SkillsState.marketplaceImportData;
        if (!data) return;

        if (this.getSkillsFeatureEnabled() === false) {
            this.hideMarketplaceImportModal();
            this.notifySkillsFeatureDisabled();
            return;
        }

        const confirmBtn = document.getElementById('marketplaceImportConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.classList.add('loading');
            confirmBtn.innerHTML = skillsButtonContent(
                '',
                skillsTranslate('workspace_skills_marketplace_importing', 'Importing...'),
            );
        }

        try {
            // Build metadata for the skill
            const metadata = {
                imported_from: 'url_import',
                source_url: data.sourceUrl,
                original_author: data.author,
                original_version: data.version,
                imported_at: data.importedAt,
            };

            const iconJson = JSON.stringify({ preset: SKILL_DEFAULT_ICON_ID, color: SKILL_ICON_COLORS[0].hex });

            // Create the skill via API
            await SkillsAPI.createSkill(
                data.name,
                data.description,
                data.content,
                iconJson,
                null, // compatibility
                null, // license
                metadata
            );
            notifyWorkspaceSkillsChanged({ reason: 'imported-from-url' });

            this.hideMarketplaceImportModal();
            
            if (typeof showNotification === 'function') {
                showNotification(skillsTranslate('workspace_skills_marketplace_success', 'Skill imported successfully!'), 'success');
            }

            // Reload skills list
            await this.loadSkills();
            
            // Navigate to skills workspace if not already there
            if (typeof WorkspaceManager !== 'undefined' && typeof WorkspaceManager.switchToTab === 'function') {
                WorkspaceManager.switchToTab('skills');
            }

        } catch (error) {
            console.error('Marketplace import: Failed to create skill', error);
            if (typeof showNotification === 'function') {
                showNotification(
                    skillsTranslateBackendDetail(error.message, skillsTranslate('workspace_skills_marketplace_error', 'Failed to import skill. Please try again.')),
                    'error',
                );
            }
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.classList.remove('loading');
                confirmBtn.innerHTML = skillsButtonContent(
                    Icons.download,
                    skillsTranslate('workspace_skills_import_confirm', 'Import Skill'),
                );
            }
        }
    },
};

// ============================================================================
// Workspace Integration
// ============================================================================

// Extend WorkspaceManager to initialize skills when switching to skills tab
if (typeof WorkspaceManager !== 'undefined') {
    const originalSwitchToTab = WorkspaceManager.switchToTab;
    WorkspaceManager.switchToTab = function(tabId) {
        originalSwitchToTab.call(this, tabId);
        const container = document.getElementById('workspaceContainer');
        container?.classList.toggle('workspace-skills-active', tabId === 'skills');
        if (tabId === 'skills') {
            SkillsManager.show();
        }
    };
}

// Initialize once DOM is ready (handles both deferred and late script execution)
function initializeSkillsModule() {
    SkillsManager.init();
    // Check for shared skill links in URL
    SkillsManager.checkForSharedSkillLink();
    // Check for marketplace import params in URL
    SkillsManager.checkForMarketplaceImport();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeSkillsModule);
} else {
    initializeSkillsModule();
}

// Expose to window
if (typeof window !== 'undefined') {
    window.SkillsManager = SkillsManager;
    window.SkillsState = SkillsState;
}
