/**
 * File Folders Management Module
 * Handles folder CRUD, sidebar rendering, folder selection, sharing, and context menus.
 */

const FILE_FOLDERS_API = Object.freeze({
    LIST: '/api/v1/file-folders/',
    CREATE: '/api/v1/file-folders/',
    UPDATE: (id) => `/api/v1/file-folders/${id}`,
    DELETE: (id) => `/api/v1/file-folders/${id}`,
    ADD_FILES: (id) => `/api/v1/file-folders/${id}/files`,
    REMOVE_FILES: (id) => `/api/v1/file-folders/${id}/files`,
    MOVE_FILE: '/api/v1/file-folders/move-file',
    FOLDER_FILES: (id) => `/api/v1/file-folders/${id}/files`,
    SHARE: '/api/v1/file-folders/share',
    SHARE_STATUS: '/api/v1/file-folders/share/status',
    SHARE_DELETE: '/api/v1/file-folders/share/delete',
    SHARED_PREVIEW: (id) => `/api/v1/file-folders/shared/${id}`,
    SHARED_ACCEPT: (id) => `/api/v1/file-folders/shared/${id}/accept`,
    SHARED_UNSUBSCRIBE: (id) => `/api/v1/file-folders/shared/${id}/unsubscribe`,
    CLONE: (id) => `/api/v1/file-folders/clone/${id}`,
    INVITE: '/api/v1/file-folders/invite',
});

const FOLDER_COLORS = [
    { id: 'indigo', name: 'Indigo', translationKey: 'files_folder_color_indigo', hex: '#6366f1' },
    { id: 'blue', name: 'Blue', translationKey: 'files_folder_color_blue', hex: '#1E88E5' },
    { id: 'teal', name: 'Teal', translationKey: 'files_folder_color_teal', hex: '#00897B' },
    { id: 'green', name: 'Green', translationKey: 'files_folder_color_green', hex: '#43A047' },
    { id: 'amber', name: 'Amber', translationKey: 'files_folder_color_amber', hex: '#FFB300' },
    { id: 'orange', name: 'Orange', translationKey: 'files_folder_color_orange', hex: '#FB8C00' },
    { id: 'red', name: 'Red', translationKey: 'files_folder_color_red', hex: '#E53935' },
    { id: 'pink', name: 'Pink', translationKey: 'files_folder_color_pink', hex: '#D81B60' },
    { id: 'purple', name: 'Purple', translationKey: 'files_folder_color_purple', hex: '#8E24AA' },
    { id: 'grey', name: 'Grey', translationKey: 'files_folder_color_grey', hex: '#757575' },
];

const workspaceFolderIconUtils = window.WorkspaceIconUtils;
const FOLDER_ICONS = workspaceFolderIconUtils.getWorkspaceIconOptions();
const FILE_FOLDER_DEFAULT_ICON_ID = 'folder';

function fileFoldersT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function fileFoldersFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(fileFoldersT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
        return value == null ? '' : String(value);
    });
}

function getFolderInvitationSuccessMessage(result) {
    const invitedCount = Number(result?.invited_count);
    return fileFoldersFormatT('files_folder_share_invited_count', 'Invited {count} user(s)', {
        count: Number.isFinite(invitedCount) ? invitedCount : 0,
    });
}

function getFolderCloneSuccessMessage(_result) {
    return fileFoldersT('files_folder_clone_success', 'Folder cloned successfully!');
}

function getFolderAcceptSuccessMessage(_result) {
    return fileFoldersT('files_folder_accept_success', 'Folder added to your workspace!');
}

// ============================================================================
// State
// ============================================================================
const FileFoldersState = {
    folders: [],
    sharedFolders: [],
    activeFolderId: 'all',
    initialized: false,
    editingFolderId: null,
    // Icon picker state
    iconPicker: {
        selectedIconId: FILE_FOLDER_DEFAULT_ICON_ID,
        selectedColorIndex: 0,
        isOpen: false,
    },
    // Sharing state
    sharingFolderId: null,
    currentShareType: 'live',
    inviteShareType: 'live',
    publicUsers: [],
    selectedUserIds: [],
    // Accept modal state
    pendingShareId: null,
    pendingShareType: null,
    acceptModalInitialized: false,
    // Delete overlay state
    deletingFolderId: null,
};

// ============================================================================
// DOM References
// ============================================================================
const FolderDOM = {
    get sidebar() { return document.getElementById('filesFolderSidebar'); },
    get folderList() { return document.getElementById('filesFolderList'); },
    get dynamicList() { return document.getElementById('filesFolderDynamicList'); },
    get sharedSection() { return document.getElementById('filesFolderSharedSection'); },
    get sharedList() { return document.getElementById('filesFolderSharedList'); },
    get allItem() { return document.getElementById('filesFolderAll'); },
    get uncategorizedItem() { return document.getElementById('filesFolderUncategorized'); },
    get allCount() { return document.getElementById('filesFolderAllCount'); },
    get uncategorizedCount() { return document.getElementById('filesFolderUncategorizedCount'); },
    get addBtn() { return document.getElementById('filesFolderAddBtn'); },
    get modalOverlay() { return document.getElementById('filesFolderModalOverlay'); },
    get modal() { return document.getElementById('filesFolderModal'); },
    get modalTitle() { return document.getElementById('filesFolderModalTitle'); },
    get modalClose() { return document.getElementById('filesFolderModalClose'); },
    get modalCancel() { return document.getElementById('filesFolderModalCancel'); },
    get modalSave() { return document.getElementById('filesFolderModalSave'); },
    get nameInput() { return document.getElementById('filesFolderNameInput'); },
    get nameError() { return document.getElementById('filesFolderNameError'); },
    get iconPicker() { return document.getElementById('filesFolderIconPicker'); },
    get iconPickerTrigger() { return document.getElementById('filesFolderIconPickerTrigger'); },
    get iconPickerPreview() { return document.getElementById('filesFolderIconPickerPreview'); },
    get iconGrid() { return document.getElementById('filesFolderIconGrid'); },
    get colorGrid() { return document.getElementById('filesFolderColorGrid'); },
    get mainHeaderTitle() { return document.getElementById('filesMainHeaderTitle'); },
    get deleteOverlay() { return document.getElementById('filesFolderDeleteOverlay'); },
    get deleteName() { return document.getElementById('filesFolderDeleteName'); },
    get deleteCancelBtn() { return document.getElementById('filesFolderDeleteCancel'); },
    get deleteConfirmBtn() { return document.getElementById('filesFolderDeleteConfirm'); },
};

const FolderIconPicker = workspaceFolderIconUtils.createWorkspaceIconPicker({
    state: FileFoldersState.iconPicker,
    refs: () => ({
        picker: FolderDOM.iconPicker,
        trigger: FolderDOM.iconPickerTrigger,
        preview: FolderDOM.iconPickerPreview,
        svgGrid: FolderDOM.iconGrid,
        colorGrid: FolderDOM.colorGrid,
    }),
    iconOptions: FOLDER_ICONS,
    colors: FOLDER_COLORS,
    defaultIconId: FILE_FOLDER_DEFAULT_ICON_ID,
    defaultColor: FOLDER_COLORS[0].hex,
    translate: fileFoldersT,
});

// ============================================================================
// API
// ============================================================================
const FolderAPI = {
    async fetchFolders() {
        const response = await window.authedFetch(FILE_FOLDERS_API.LIST);
        if (!response.ok) throw new Error(fileFoldersT('files_folder_error_fetch', 'Failed to fetch folders'));
        return response.json();
    },

    async createFolder(name, icon, iconColor) {
        const response = await window.authedFetch(FILE_FOLDERS_API.CREATE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, icon, icon_color: iconColor }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || fileFoldersT('files_folder_error_create', 'Failed to create folder'));
        }
        return response.json();
    },

    async updateFolder(folderId, data) {
        const response = await window.authedFetch(FILE_FOLDERS_API.UPDATE(folderId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || fileFoldersT('files_folder_error_update', 'Failed to update folder'));
        }
        return response.json();
    },

    async deleteFolder(folderId) {
        const response = await window.authedFetch(FILE_FOLDERS_API.DELETE(folderId), {
            method: 'DELETE',
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || fileFoldersT('files_folder_error_delete', 'Failed to delete folder'));
        }
        return response.json();
    },

    async moveFile(fileId, folderId) {
        const response = await window.authedFetch(FILE_FOLDERS_API.MOVE_FILE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId, folder_id: folderId }),
        });
        if (!response.ok) throw new Error(fileFoldersT('files_move_error', 'Failed to move file'));
        return response.json();
    },

    async shareFolder(folderId, shareType = 'live') {
        const response = await window.authedFetch(FILE_FOLDERS_API.SHARE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_id: folderId, share_type: shareType }),
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_error_share', 'Failed to share folder'));
        return response.json();
    },

    async deleteFolderShare(folderId, shareType = null) {
        const body = { folder_id: folderId };
        if (shareType) body.share_type = shareType;
        const response = await window.authedFetch(FILE_FOLDERS_API.SHARE_DELETE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_error_delete_share', 'Failed to delete share'));
        return response.json();
    },

    async acceptSharedFolder(shareId) {
        const response = await window.authedFetch(FILE_FOLDERS_API.SHARED_ACCEPT(shareId), {
            method: 'POST',
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_accept_error', 'Failed to accept shared folder'));
        return response.json();
    },

    async unsubscribeFolder(folderId) {
        const response = await window.authedFetch(FILE_FOLDERS_API.SHARED_UNSUBSCRIBE(folderId), {
            method: 'POST',
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_unsubscribe_error', 'Failed to unsubscribe'));
        return response.json();
    },

    async getShareStatus(folderId) {
        const response = await window.authedFetch(`${FILE_FOLDERS_API.SHARE_STATUS}?folder_id=${encodeURIComponent(folderId)}`, {
            method: 'GET',
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_share_status_error', 'Failed to get share status'));
        return response.json();
    },

    async getSharedFolderPreview(shareId) {
        const response = await window.authedFetch(FILE_FOLDERS_API.SHARED_PREVIEW(shareId), {
            method: 'GET',
        });
        if (!response.ok) {
            let detail = fileFoldersT('files_folder_accept_error_not_found', 'Shared folder not found');
            try {
                const errorBody = await response.json();
                if (errorBody?.detail) detail = errorBody.detail;
            } catch (_) {}
            const error = new Error(detail);
            error.status = response.status;
            throw error;
        }
        return response.json();
    },

    async cloneFolder(shareId) {
        const response = await window.authedFetch(FILE_FOLDERS_API.CLONE(shareId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_clone_error', 'Failed to clone folder'));
        return response.json();
    },

    async fetchPublicUsers() {
        const users = [];
        const seenUserIds = new Set();
        let offset = 0;
        const limit = 100;
        while (true) {
            const response = await window.authedFetch(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!response.ok) throw new Error(fileFoldersT('files_folder_users_error', 'Failed to fetch public users'));
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

    async inviteUsersToFolder(folderId, userIds, shareType = 'live') {
        const response = await window.authedFetch(FILE_FOLDERS_API.INVITE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: folderId, user_ids: userIds, share_type: shareType }),
        });
        if (!response.ok) throw new Error(fileFoldersT('files_folder_invite_error', 'Failed to send invitations'));
        return response.json();
    },
};

// ============================================================================
// Rendering
// ============================================================================
const FolderRenderer = {
    renderIcon(iconData, options = {}) {
        return workspaceFolderIconUtils.renderWorkspaceIcon(iconData, {
            size: options.size || 22,
            defaultIconId: FILE_FOLDER_DEFAULT_ICON_ID,
            iconOptions: FOLDER_ICONS,
        });
    },

    renderSidebar() {
        const ownFolders = FileFoldersState.folders.filter(f => !f.is_subscribed);
        const sharedFolders = FileFoldersState.folders.filter(f => f.is_subscribed);

        // Render own folders
        const dynamicList = FolderDOM.dynamicList;
        if (dynamicList) {
            dynamicList.innerHTML = ownFolders.map(f => this.createFolderItem(f)).join('');
            this.attachFolderListeners(ownFolders);
        }

        // Render shared folders
        const sharedSection = FolderDOM.sharedSection;
        const sharedList = FolderDOM.sharedList;
        if (sharedSection && sharedList) {
            if (sharedFolders.length > 0) {
                sharedSection.style.display = '';
                sharedList.innerHTML = sharedFolders.map(f => this.createFolderItem(f, true)).join('');
                this.attachFolderListeners(sharedFolders);
            } else {
                sharedSection.style.display = 'none';
                sharedList.innerHTML = '';
            }
        }

        this.updateCounts();
        this.updateActiveState();
    },

    parseFolderIcon(icon, iconColor) {
        return workspaceFolderIconUtils.resolveWorkspaceStoredIcon(icon, {
            iconOptions: FOLDER_ICONS,
            defaultIconId: FILE_FOLDER_DEFAULT_ICON_ID,
            defaultColor: FOLDER_COLORS[0].hex,
            color: iconColor,
        });
    },

    createFolderItem(folder, isShared = false) {
        const isActive = FileFoldersState.activeFolderId === folder.id;
        const hasShare = folder.clone_share_id || folder.live_share_id || folder.collaborate_share_id;
        const sharedClass = (hasShare || isShared) ? ' is-shared' : '';
        const activeClass = isActive ? ' active' : '';
        const ownerLabel = folder.owner_name ? ` <span style="font-size:11px;color:var(--text-color-secondary);">— ${this.escapeHtml(folder.owner_name)}</span>` : '';
        const iconData = this.parseFolderIcon(folder.icon, folder.icon_color);

        return `
            <div class="files-sidebar-item has-actions${activeClass}${sharedClass}" data-folder-id="${folder.id}">
                <span class="files-sidebar-item-icon has-color" style="--icon-bg-color: ${this.escapeHtml(iconData.color)};">
                    ${this.renderIcon(iconData)}
                </span>
                <span class="files-sidebar-item-name">${this.escapeHtml(folder.name)}${ownerLabel}</span>
                <span class="files-sidebar-item-count" data-folder-count="${folder.id}">${folder.file_count || ''}</span>
                <span class="files-sidebar-item-actions">
                    <button type="button" class="files-sidebar-item-action-btn" data-folder-ctx="${folder.id}" title="${this.escapeHtml(fileFoldersT('files_more_options', 'More options'))}" aria-label="${this.escapeHtml(fileFoldersT('files_more_options', 'More options'))}" aria-haspopup="menu" aria-expanded="false">
                        ${Icons.ellipsisVertical}
                    </button>
                </span>
            </div>
        `;
    },

    attachFolderListeners(folders) {
        folders.forEach(folder => {
            const item = document.querySelector(`.files-sidebar-item[data-folder-id="${folder.id}"]`);
            if (!item) return;

            item.addEventListener('click', (e) => {
                if (e.target.closest('[data-folder-ctx]')) return;
                FileFoldersManager.selectFolder(folder.id);
            });

            const ctxBtn = item.querySelector('[data-folder-ctx]');
            if (ctxBtn) {
                ctxBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    ContextMenu.show(e, folder);
                });
            }

            item.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                ContextMenu.show(e, folder);
            });
        });
    },

    updateCounts() {
        const counts = (typeof state !== 'undefined' && state.counts) ? state.counts : { all: 0, uncategorized: 0, folders: {} };
        const allCount = Number(counts.all) || 0;
        const uncategorizedCount = Number(counts.uncategorized) || 0;

        const allCountEl = FolderDOM.allCount;
        const uncatCountEl = FolderDOM.uncategorizedCount;
        if (allCountEl) allCountEl.textContent = allCount || '';
        if (uncatCountEl) uncatCountEl.textContent = uncategorizedCount || '';

        FileFoldersState.folders.forEach(folder => {
            const countEl = document.querySelector(`[data-folder-count="${folder.id}"]`);
            if (!countEl) return;

            const queryCount = Number(counts?.folders?.[folder.id]);
            const fallbackCount = Number(folder.file_count) || 0;
            const finalCount = Number.isFinite(queryCount) ? queryCount : fallbackCount;

            countEl.textContent = finalCount || '';
        });
    },

    updateActiveState() {
        const items = document.querySelectorAll('.files-sidebar-item');
        items.forEach(item => {
            const fid = item.dataset.folderId;
            item.classList.toggle('active', fid === FileFoldersState.activeFolderId);
        });
    },

    updateMainHeader() {
        const titleEl = FolderDOM.mainHeaderTitle;
        if (!titleEl) return;

        const activeId = FileFoldersState.activeFolderId;
        let title = fileFoldersT('files_folder_all', 'All Files');

        if (activeId === 'all') {
            title = fileFoldersT('files_folder_all', 'All Files');
        } else if (activeId === 'uncategorized') {
            title = fileFoldersT('files_folder_uncategorized', 'Uncategorized');
        } else {
            const folder = FileFoldersState.folders.find(f => f.id === activeId);
            if (folder) {
                title = folder.name;
            }
        }

        titleEl.textContent = title;
    },

    escapeHtml(text) {
        return workspaceFolderIconUtils.escapeHtml(text || '');
    },
};

// ============================================================================
// Context Menu
// ============================================================================
const ContextMenu = {
    show(event, folder) {
        const items = [];
        if (!folder.is_subscribed) {
            items.push({ iconHtml: Icons.edit, label: fileFoldersT('files_folder_edit', 'Edit'), onSelect: () => FileFoldersManager.openEditModal(folder) });
            // System folders are private automatic-storage containers.  Hide
            // the unsupported action in the UI; the API independently rejects
            // sharing so this is usability, not the security boundary.
            if (!folder.system_kind) {
                items.push({ iconHtml: Icons.share, label: fileFoldersT('files_folder_share', 'Share'), onSelect: () => FileFoldersManager.shareFolder(folder) });
            }
            items.push({ iconHtml: Icons.trash, label: fileFoldersT('files_folder_delete_action_short', 'Delete'), destructive: true, onSelect: () => FileFoldersManager.confirmDeleteFolder(folder) });
        } else {
            items.push({ iconHtml: Icons.logout, label: fileFoldersT('files_folder_unsubscribe', 'Unsubscribe'), destructive: true, onSelect: () => FileFoldersManager.unsubscribeFolder(folder) });
        }

        const folderRow = event.target?.closest?.('.files-sidebar-item') || null;
        const currentTarget = event.currentTarget || null;
        const trigger = currentTarget?.matches?.('[data-folder-ctx]')
            ? currentTarget
            : folderRow?.querySelector?.('[data-folder-ctx]');
        if (!trigger) return;

        window.openDropdownMenu({
            trigger,
            items,
            ariaLabel: fileFoldersT('files_more_options', 'More options'),
        });
    },
};

// ============================================================================
// Modal
// ============================================================================
const FolderModal = {
    open(mode = 'create', folder = null) {
        const overlay = FolderDOM.modalOverlay;
        if (!overlay) return;

        FileFoldersState.editingFolderId = folder ? folder.id : null;
        const title = FolderDOM.modalTitle;
        const saveBtn = FolderDOM.modalSave;
        const nameInput = FolderDOM.nameInput;

        if (title) title.textContent = mode === 'edit'
            ? fileFoldersT('files_folder_edit_title', 'Edit Folder')
            : fileFoldersT('files_folder_new', 'New Folder');
        if (saveBtn) saveBtn.textContent = mode === 'edit'
            ? fileFoldersT('files_folder_save_changes', 'Save Changes')
            : fileFoldersT('files_folder_create', 'Create Folder');
        if (nameInput) nameInput.value = folder ? folder.name : '';
        window.FormValidation?.clearInputError(nameInput, FolderDOM.nameError);

        FolderIconPicker?.reset(folder?.icon || FILE_FOLDER_DEFAULT_ICON_ID, folder?.icon_color || FOLDER_COLORS[0].hex);
        this.renderIconPicker();
        this.updateIconPickerPreview();

        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => { if (nameInput) nameInput.focus(); });
    },

    close() {
        const overlay = FolderDOM.modalOverlay;
        if (overlay) {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
        }
        FileFoldersState.editingFolderId = null;
        FileFoldersState.iconPicker.isOpen = false;
        const picker = FolderDOM.iconPicker;
        if (picker) picker.classList.remove('open');
        window.FormValidation?.clearInputError(FolderDOM.nameInput, FolderDOM.nameError);
    },

    async save() {
        const name = FolderDOM.nameInput?.value?.trim();
        const iconData = FolderIconPicker?.getIconData?.() || FolderRenderer.parseFolderIcon(FILE_FOLDER_DEFAULT_ICON_ID, FOLDER_COLORS[0].hex);
        const icon = FolderIconPicker?.serialize?.({ includeColor: false }) || FILE_FOLDER_DEFAULT_ICON_ID;
        const iconColor = iconData.color || FOLDER_COLORS[0].hex;

        if (!name) {
            window.FormValidation?.showInputError(
                FolderDOM.nameInput,
                FolderDOM.nameError,
                fileFoldersT('files_folder_name_required', 'Folder name is required'),
            );
            return;
        }
        window.FormValidation?.clearInputError(FolderDOM.nameInput, FolderDOM.nameError);

        try {
            if (FileFoldersState.editingFolderId) {
                await FolderAPI.updateFolder(FileFoldersState.editingFolderId, {
                    name, icon, icon_color: iconColor,
                });
                if (typeof notifySuccess === 'function') notifySuccess(fileFoldersT('files_folder_updated', 'Folder updated'));
            } else {
                await FolderAPI.createFolder(name, icon, iconColor);
                if (typeof notifySuccess === 'function') notifySuccess(fileFoldersT('files_folder_created', 'Folder created'));
            }
            this.close();
            await FileFoldersManager.loadFolders();
        } catch (err) {
            if (typeof notifyError === 'function') notifyError(err.message || fileFoldersT('files_folder_save_error', 'Failed to save folder'));
        }
    },

    // Icon Picker Methods
    renderIconPicker() {
        FolderIconPicker?.render?.();
    },

    updateIconPickerPreview() {
        FolderIconPicker?.updatePreview?.();
    },

    toggleIconPicker(open) {
        FolderIconPicker?.setOpen?.(open);
    },

    selectIcon(index) {
        FolderIconPicker?.selectPreset?.(FOLDER_ICONS[index]?.id || FILE_FOLDER_DEFAULT_ICON_ID);
    },

    selectColor(index) {
        FolderIconPicker?.selectColor?.(index);
    },

};

// ============================================================================
// Main Manager
// ============================================================================
const FileFoldersManager = {
    async init() {
        if (FileFoldersState.initialized) return;
        this.setupListeners();
        await this.loadFolders();
        FileFoldersState.initialized = true;
    },

    setupListeners() {
        // Add folder button
        FolderDOM.addBtn?.addEventListener('click', () => FolderModal.open('create'));

        if (typeof window !== 'undefined' && typeof window.registerEscapeHandler === 'function') {
            window.registerEscapeHandler({
                id: 'files-folder-icon-picker',
                priority: 120,
                isActive: () => FileFoldersState.iconPicker.isOpen,
                close: () => FolderModal.toggleIconPicker(false),
            });
            window.registerEscapeHandler({
                id: 'files-folder-modal',
                priority: 80,
                isActive: () => Boolean(FolderDOM.modalOverlay && !FolderDOM.modalOverlay.hasAttribute('hidden')),
                close: () => FolderModal.close(),
            });
        }

        // Modal buttons
        FolderDOM.modalClose?.addEventListener('click', () => FolderModal.close());
        FolderDOM.modalCancel?.addEventListener('click', () => FolderModal.close());
        FolderDOM.modalSave?.addEventListener('click', () => FolderModal.save());
        FolderDOM.modalOverlay?.addEventListener('click', (e) => {
            if (e.target === FolderDOM.modalOverlay) FolderModal.close();
        });
        FolderDOM.nameInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') FolderModal.save();
        });
        FolderDOM.nameInput?.addEventListener('input', () => (
            window.FormValidation?.clearInputError(FolderDOM.nameInput, FolderDOM.nameError)
        ));

        FolderIconPicker?.bind?.();

        FolderDOM.deleteCancelBtn?.addEventListener('click', () => this.hideDeleteOverlay());
        FolderDOM.deleteOverlay?.addEventListener('click', (event) => {
            if (event.target === FolderDOM.deleteOverlay) {
                this.hideDeleteOverlay();
            }
        });
        FolderDOM.deleteConfirmBtn?.addEventListener('click', () => this.performDeleteFolder());

        // Virtual folder clicks
        FolderDOM.allItem?.addEventListener('click', () => this.selectFolder('all'));
        FolderDOM.uncategorizedItem?.addEventListener('click', () => this.selectFolder('uncategorized'));

    },

    async loadFolders() {
        try {
            const folders = await FolderAPI.fetchFolders();
            FileFoldersState.folders = Array.isArray(folders) ? folders : [];
            FolderRenderer.renderSidebar();
            FolderRenderer.updateMainHeader();
        } catch (err) {
            console.error('Failed to load folders:', err);
        }
    },

    selectFolder(folderId) {
        FileFoldersState.activeFolderId = folderId;
        FolderRenderer.updateActiveState();
        FolderRenderer.updateMainHeader();

        // Trigger file list re-render with folder filter
        if (typeof FilesManager !== 'undefined') {
            this.applyFolderFilter();
        }
    },

    applyFolderFilter() {
        if (typeof FilesManager !== 'undefined' && typeof FilesManager.refresh === 'function') {
            FilesManager.refresh();
        }
    },

    getActiveFolderId() {
        return FileFoldersState.activeFolderId;
    },

    openEditModal(folder) {
        FolderModal.open('edit', folder);
    },

    confirmDeleteFolder(folder) {
        this.showDeleteOverlay(folder);
    },

    showDeleteOverlay(folder) {
        const overlay = FolderDOM.deleteOverlay;
        if (!overlay) return;
        FileFoldersState.deletingFolderId = folder.id;
        if (FolderDOM.deleteName) {
            FolderDOM.deleteName.textContent = folder.name || 'this folder';
        }
        const confirmBtn = FolderDOM.deleteConfirmBtn;
        if (confirmBtn) {
            confirmBtn.disabled = false;
        }
        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
    },

    hideDeleteOverlay() {
        const overlay = FolderDOM.deleteOverlay;
        if (overlay) {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
        }
        const confirmBtn = FolderDOM.deleteConfirmBtn;
        if (confirmBtn) {
            confirmBtn.disabled = false;
        }
        FileFoldersState.deletingFolderId = null;
    },

    async performDeleteFolder() {
        const folderId = FileFoldersState.deletingFolderId;
        if (!folderId) {
            notifyError?.(fileFoldersT('files_folder_delete_none', 'No folder selected for deletion'));
            return;
        }
        const confirmBtn = FolderDOM.deleteConfirmBtn;
        if (confirmBtn) confirmBtn.disabled = true;
        try {
            await FolderAPI.deleteFolder(folderId);
            if (FileFoldersState.activeFolderId === folderId) {
                this.selectFolder('all');
            }
            await this.loadFolders();
            notifySuccess?.(fileFoldersT('files_folder_deleted', 'Folder deleted'));
            this.hideDeleteOverlay();
        } catch (error) {
            const message = error?.message || fileFoldersT('files_folder_error_delete', 'Failed to delete folder');
            notifyError?.(message);
            if (confirmBtn) confirmBtn.disabled = false;
        }
    },

    async shareFolder(folder) {
        if (folder?.system_kind) return;
        await FolderShareModal.showShareModal(folder.id);
    },

    async unsubscribeFolder(folder) {
        if (!await window.showDeleteConfirm({
            title: fileFoldersT('common_remove_confirm_title', 'Remove item?'),
            message: fileFoldersFormatT('files_folder_unsubscribe_confirm', 'Unsubscribe from "{name}"?', { name: folder.name }),
            confirmLabel: fileFoldersT('files_folder_unsubscribe', 'Unsubscribe'),
        })) return;
        try {
            await FolderAPI.unsubscribeFolder(folder.id);
            if (FileFoldersState.activeFolderId === folder.id) {
                this.selectFolder('all');
            }
            await this.loadFolders();
            if (typeof notifySuccess === 'function') notifySuccess(fileFoldersT('files_folder_unsubscribed', 'Unsubscribed from folder'));
        } catch (err) {
            if (typeof notifyError === 'function') notifyError(err.message || fileFoldersT('files_folder_unsubscribe_error', 'Failed to unsubscribe'));
        }
    },

    async moveFileToFolder(fileId, folderId) {
        try {
            await FolderAPI.moveFile(fileId, folderId || null);
            await this.loadFolders();
            if (typeof FilesManager !== 'undefined') await FilesManager.refresh();
        } catch (err) {
            if (typeof notifyError === 'function') notifyError(fileFoldersT('files_move_error', 'Failed to move file'));
        }
    },

    updateAfterFilesLoaded() {
        FolderRenderer.updateCounts();
        FolderRenderer.updateMainHeader();
    },
};

// ============================================================================
// Share Modal (mirrors notes sharing modal exactly)
// ============================================================================
const FolderShareModal = {
    async showShareModal(folderId) {
        const folder = FileFoldersState.folders.find(f => f.id === folderId);
        if (!folder || folder.system_kind) return;

        let overlay = document.getElementById('folderShareOverlay');
        if (!overlay) {
            const shareTitle = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_title', 'Share Folder'));
            const linkLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_link', 'Link'));
            const inviteUsersLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_invite_users', 'Invite Users'));
            const shareTypeLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_type', 'Share Type'));
            const liveLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_live_view', 'Live (View Only)'));
            const collaborateLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_collaborate', 'Collaborate'));
            const cloneLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_clone', 'Clone'));
            const liveDesc = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_live_desc', 'Recipients can view this folder with live updates. They cannot edit.'));
            const generateLinkLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_generate_link', 'Generate Link'));
            const copyLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_copy', 'Copy'));
            const selectUsersLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_select_users', 'Select Users to Invite'));
            const searchUsersPlaceholder = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_search_users', 'Search users...'));
            const loadingUsersLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_loading_users', 'Loading users...'));
            const selectedLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_selected', 'Selected'));
            const inviteSelectedLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_invite_selected', 'Invite Selected Users'));
            const activeSharesLabel = FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_active', 'Active Shares'));
            const closeLabel = FolderRenderer.escapeHtml(fileFoldersT('common_close', 'Close'));
            overlay = document.createElement('div');
            overlay.id = 'folderShareOverlay';
            overlay.className = 'notes-share-overlay shared-modal-overlay';
            overlay.innerHTML = `
                <div class="notes-share-modal folder-share-modal shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="folderShareModalTitle" tabindex="-1">
                    <div class="notes-share-modal-header shared-modal-header shared-modal-header--main">
                        <h3 class="notes-share-modal-title shared-modal-title" id="folderShareModalTitle">${shareTitle}</h3>
                        <button type="button" class="om-button shared-modal-close" id="folderShareCloseBtn" aria-label="${closeLabel}">
                            ${Icons.close}
                        </button>
                    </div>
                    <div class="notes-share-modal-body shared-modal-body">
                        <p class="notes-share-modal-name" id="folderShareName"></p>
                        
                        <div class="notes-share-mode-toggle" role="tablist">
	                            <button type="button" class="notes-share-mode-btn active" data-mode="link" id="folderShareModeLink" role="tab" aria-selected="true" aria-controls="folderShareLinkMode">
                                ${Icons.urlLink}
	                                ${linkLabel}
	                            </button>
                            <button type="button" class="notes-share-mode-btn" data-mode="invite" id="folderShareModeInvite" role="tab" aria-selected="false" aria-controls="folderShareInviteMode">
                                ${Icons.groups}
	                                ${inviteUsersLabel}
	                            </button>
                        </div>
                        
                        <!-- Link Mode Content -->
                        <div class="notes-share-mode-content" id="folderShareLinkMode" role="tabpanel" aria-labelledby="folderShareModeLink">
                            <div class="notes-share-type-section">
	                                <label class="notes-share-label" for="folderShareTypeSelect">${shareTypeLabel}</label>
                                <div class="notes-share-type-select-wrapper">
                                    <select id="folderShareTypeSelect" class="notes-share-type-select">
	                                        <option value="live">${liveLabel}</option>
	                                        <option value="collaborate">${collaborateLabel}</option>
	                                        <option value="clone">${cloneLabel}</option>
                                    </select>
                                    <span class="notes-share-select-arrow" aria-hidden="true">${Icons.chevron}</span>
                                </div>
                            </div>
                            
                            <div class="notes-share-type-desc" id="folderShareTypeDesc">
	                                <p id="folderShareTypeDescText">${liveDesc}</p>
                            </div>
                            
                            <div class="notes-share-link-section">
                                <button type="button" class="om-button border submit notes-share-generate-btn" id="folderShareGenerateBtn">
                                    ${Icons.urlLink}
	                                    ${generateLinkLabel}
                                </button>
                                <div class="notes-share-link-container" id="folderShareLinkContainer" style="display: none;">
                                    <input type="text" class="notes-share-link-input" id="folderShareLinkInput" readonly>
                                    <button type="button" class="om-button border cancel notes-share-copy-btn" id="folderShareCopyBtn">
                                        ${Icons.copy}
	                                        ${copyLabel}
                                    </button>
                                </div>
                            </div>
                        </div>
                        
                        <!-- Invite Mode Content -->
                        <div class="notes-share-mode-content" id="folderShareInviteMode" role="tabpanel" aria-labelledby="folderShareModeInvite" hidden>
                            <div class="notes-share-type-section">
	                                <label class="notes-share-label" for="folderInviteTypeSelect">${shareTypeLabel}</label>
                                <div class="notes-share-type-select-wrapper">
                                    <select id="folderInviteTypeSelect" class="notes-share-type-select">
	                                        <option value="live">${liveLabel}</option>
	                                        <option value="collaborate">${collaborateLabel}</option>
	                                        <option value="clone">${cloneLabel}</option>
                                    </select>
                                    <span class="notes-share-select-arrow" aria-hidden="true">${Icons.chevron}</span>
                                </div>
                            </div>
                            
                            <div class="notes-share-invite-section">
	                                <label class="notes-share-label">${selectUsersLabel}</label>
                                <div class="notes-share-user-search">
                                    ${Icons.magnifyingGlass}
		                                    <input type="text" id="folderInviteUserSearch" placeholder="${searchUsersPlaceholder}" class="notes-share-user-search-input" aria-describedby="folderInviteUserError" aria-invalid="false">
	                                </div>
                                    <p class="cs-field-error" id="folderInviteUserError" role="alert" hidden></p>
	                                <div class="notes-share-user-list" id="folderInviteUserList">
	                                    <div class="notes-share-user-loading">${loadingUsersLabel}</div>
                                </div>
                                <div class="notes-share-selected-users" id="folderSelectedUsers" style="display: none;">
	                                    <label class="notes-share-label">${selectedLabel} (<span id="folderSelectedCount">0</span>)</label>
                                    <div class="notes-share-selected-list" id="folderSelectedUsersList"></div>
                                </div>
                            </div>
                            
                            <div class="notes-share-invite-actions">
                                <button type="button" class="om-button border submit notes-share-invite-btn" id="folderInviteBtn" disabled>
                                    ${Icons.send}
	                                    ${inviteSelectedLabel}
                                </button>
                            </div>
                        </div>
                        
                        <!-- Active Shares Section -->
                        <div class="notes-share-active-section" id="folderShareActiveSection">
	                            <label class="notes-share-label">${activeSharesLabel}</label>
                            <div class="notes-share-active-list" id="folderShareActiveList"></div>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(overlay);

            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideShareModal(); });
            overlay.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.hideShareModal();
                    return;
                }
                if (event.key !== 'Tab') return;
                const dialog = overlay.querySelector('[role="dialog"]');
                const focusable = Array.from(dialog?.querySelectorAll(
                    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
                ) || []).filter((element) => !element.hidden && element.getClientRects().length > 0);
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (!first) {
                    event.preventDefault();
                    dialog?.focus({ preventScroll: true });
                } else if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                    event.preventDefault();
                    first.focus();
                }
            });
            document.getElementById('folderShareCloseBtn').addEventListener('click', () => this.hideShareModal());
            document.getElementById('folderShareCopyBtn').addEventListener('click', () => this.copyShareLink());
            document.getElementById('folderShareGenerateBtn').addEventListener('click', () => this.generateShareLink());
            document.getElementById('folderShareTypeSelect').addEventListener('change', (e) => this.onShareTypeChange(e.target.value));
            document.getElementById('folderShareModeLink').addEventListener('click', () => this.setShareMode('link'));
            document.getElementById('folderShareModeInvite').addEventListener('click', () => this.setShareMode('invite'));
            document.getElementById('folderInviteTypeSelect').addEventListener('change', (e) => this.onInviteTypeChange(e.target.value));
            document.getElementById('folderInviteUserSearch').addEventListener('input', (e) => {
                this.filterInviteUsers(e.target.value);
                if (FileFoldersState.selectedUserIds.length) this.clearInviteSelectionError();
            });
            document.getElementById('folderInviteBtn').addEventListener('click', () => this.sendInvitations());
        }

        // Reset state before updating controls so a reopened modal cannot keep
        // stale recipients or an enabled invite action from the prior folder.
        FileFoldersState.sharingFolderId = folderId;
        FileFoldersState.currentShareType = 'live';
        FileFoldersState.inviteShareType = 'live';
        FileFoldersState.publicUsers = [];
        FileFoldersState.selectedUserIds = [];

        this.setShareMode('link');
        document.getElementById('folderShareTypeSelect').value = 'live';
        document.getElementById('folderShareLinkContainer').style.display = 'none';
        document.getElementById('folderShareGenerateBtn').style.display = 'flex';
        document.getElementById('folderInviteTypeSelect').value = 'live';
        document.getElementById('folderInviteUserSearch').value = '';
        document.getElementById('folderInviteBtn').disabled = true;
        document.getElementById('folderSelectedUsers').style.display = 'none';
        document.getElementById('folderSelectedUsersList').innerHTML = '';
        document.getElementById('folderSelectedCount').textContent = '0';
        this.clearInviteSelectionError();
        this.onShareTypeChange('live');
        this.onInviteTypeChange('live');

        document.getElementById('folderShareName').textContent = folder.name;

        overlay._previousFocus = typeof HTMLElement !== 'undefined' && document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        if (overlay._closeTimer) {
            clearTimeout(overlay._closeTimer);
            overlay._closeTimer = null;
        }
        if (overlay.hasAttribute?.('hidden')) {
            overlay._bodyHadModalOpen = Boolean(document.body?.classList?.contains?.('modal-open'));
        }
        overlay.removeAttribute('hidden');
        overlay.inert = false;
        overlay.setAttribute('aria-hidden', 'false');
        document.body?.classList?.add?.('modal-open');
        requestAnimationFrame(() => {
            overlay.classList.add('active');
            document.getElementById('folderShareCloseBtn')?.focus?.({ preventScroll: true });
        });

        await this.loadShareStatus(folderId);
    },

    setShareMode(mode) {
        const linkBtn = document.getElementById('folderShareModeLink');
        const inviteBtn = document.getElementById('folderShareModeInvite');
        const linkMode = document.getElementById('folderShareLinkMode');
        const inviteMode = document.getElementById('folderShareInviteMode');

        // Preserve the component's flex layout while switching panels. Setting
        // an inline `display: block` here previously removed every intended gap.
        const showLinkMode = mode === 'link';
        linkBtn.classList.toggle('active', showLinkMode);
        inviteBtn.classList.toggle('active', !showLinkMode);
        linkBtn.setAttribute('aria-selected', showLinkMode ? 'true' : 'false');
        inviteBtn.setAttribute('aria-selected', showLinkMode ? 'false' : 'true');
        linkMode.hidden = !showLinkMode;
        inviteMode.hidden = showLinkMode;

        if (!showLinkMode) {
            this.loadPublicUsers();
        }
    },

    async loadPublicUsers() {
        const userList = document.getElementById('folderInviteUserList');
        userList.innerHTML = `<div class="notes-share-user-loading">${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_loading_users', 'Loading users...'))}</div>`;
        try {
            const users = await FolderAPI.fetchPublicUsers();
            FileFoldersState.publicUsers = users;
            this.renderInviteUserList(users);
        } catch (error) {
            console.error('Failed to load public users:', error);
            userList.innerHTML = `<div class="notes-share-user-empty">${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_load_users_error', 'Failed to load users'))}</div>`;
        }
    },

    renderInviteUserList(users) {
        const userList = document.getElementById('folderInviteUserList');
        if (!users || users.length === 0) {
            userList.innerHTML = `<div class="notes-share-user-empty">${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_no_users', 'No users available to invite'))}</div>`;
            return;
        }
        userList.innerHTML = users.map(user => {
            const isSelected = FileFoldersState.selectedUserIds.includes(user.id);
            const initials = this.getUserInitials(user);
            return `
                <button type="button" class="notes-share-user-item ${isSelected ? 'selected' : ''}" data-user-id="${FolderRenderer.escapeHtml(user.id)}" aria-pressed="${isSelected ? 'true' : 'false'}">
                    <div class="notes-share-user-avatar">${FolderRenderer.escapeHtml(initials)}</div>
                    <div class="notes-share-user-info">
                        <span class="notes-share-user-name">${FolderRenderer.escapeHtml(user.display_name)}</span>
                    </div>
                    <div class="notes-share-user-check">
                        ${Icons.check}
                    </div>
                </button>
            `;
        }).join('');
        userList.querySelectorAll('.notes-share-user-item').forEach(item => {
            item.addEventListener('click', () => this.toggleUserSelection(item.dataset.userId));
        });
    },

    getUserInitials(user) {
        if (user.first_name && user.last_name) return (user.first_name[0] + user.last_name[0]).toUpperCase();
        if (user.first_name) return user.first_name.substring(0, 2).toUpperCase();
        if (user.display_name) return user.display_name.substring(0, 2).toUpperCase();
        return '??';
    },

    toggleUserSelection(userId) {
        const idx = FileFoldersState.selectedUserIds.indexOf(userId);
        if (idx >= 0) {
            FileFoldersState.selectedUserIds.splice(idx, 1);
        } else {
            FileFoldersState.selectedUserIds.push(userId);
        }
        if (FileFoldersState.selectedUserIds.length) this.clearInviteSelectionError();
        this.updateSelectedUsersUI();
    },

    updateSelectedUsersUI() {
        const selectedSection = document.getElementById('folderSelectedUsers');
        const selectedList = document.getElementById('folderSelectedUsersList');
        const selectedCount = document.getElementById('folderSelectedCount');
        const inviteBtn = document.getElementById('folderInviteBtn');

        document.querySelectorAll('#folderInviteUserList .notes-share-user-item').forEach(item => {
            const isSelected = FileFoldersState.selectedUserIds.includes(item.dataset.userId);
            item.classList.toggle('selected', isSelected);
            item.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        });

        if (FileFoldersState.selectedUserIds.length === 0) {
            selectedSection.style.display = 'none';
            inviteBtn.disabled = true;
        } else {
            selectedSection.style.display = 'flex';
            selectedCount.textContent = FileFoldersState.selectedUserIds.length;
            inviteBtn.disabled = false;
            this.clearInviteSelectionError();

            const selectedUsers = FileFoldersState.publicUsers.filter(u => FileFoldersState.selectedUserIds.includes(u.id));
            selectedList.innerHTML = selectedUsers.map(user => {
                const displayName = user.display_name || fileFoldersT('chat_share_unknown_user', 'Unknown user');
                const removeUserLabel = `${fileFoldersT('chat_share_invite_remove_user_aria', 'Remove user')}: ${displayName}`;
                return `
                    <div class="notes-share-selected-tag" data-user-id="${FolderRenderer.escapeHtml(user.id)}">
                        <span>${FolderRenderer.escapeHtml(displayName)}</span>
                        <button type="button" class="notes-share-selected-remove" data-user-id="${FolderRenderer.escapeHtml(user.id)}" aria-label="${FolderRenderer.escapeHtml(removeUserLabel)}">
                            ${Icons.close}
                        </button>
                    </div>
                `;
            }).join('');

            selectedList.querySelectorAll('.notes-share-selected-remove').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.toggleUserSelection(btn.dataset.userId);
                });
            });
        }
    },

    filterInviteUsers(searchTerm) {
        const term = searchTerm.toLowerCase().trim();
        const filtered = term
            ? FileFoldersState.publicUsers.filter(u =>
                u.display_name.toLowerCase().includes(term) ||
                false
              )
            : FileFoldersState.publicUsers;
        this.renderInviteUserList(filtered);
    },

    showInviteSelectionError() {
        const input = document.getElementById('folderInviteUserSearch');
        const error = document.getElementById('folderInviteUserError');
        const message = fileFoldersT('chat_share_invite_select_error', 'Select at least one user to invite.');
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
        const input = document.getElementById('folderInviteUserSearch');
        const error = document.getElementById('folderInviteUserError');
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

    onShareTypeChange(shareType) {
        FileFoldersState.currentShareType = shareType;
        const descText = document.getElementById('folderShareTypeDescText');

        if (shareType === 'clone') {
            descText.textContent = fileFoldersT('files_folder_share_clone_desc', 'Recipients will get their own copy of this folder and its files. Your original folder stays unchanged.');
        } else if (shareType === 'collaborate') {
            descText.textContent = fileFoldersT('files_folder_share_collaborate_desc', 'Recipients can view and edit files in this folder with live sync. Changes sync for everyone.');
        } else {
            descText.textContent = fileFoldersT('files_folder_share_live_desc', 'Recipients can view this folder with live updates. They cannot edit.');
        }

        document.getElementById('folderShareLinkContainer').style.display = 'none';
        document.getElementById('folderShareGenerateBtn').style.display = 'flex';
    },

    onInviteTypeChange(shareType) {
        FileFoldersState.inviteShareType = shareType;
    },

    async generateShareLink() {
        const folderId = FileFoldersState.sharingFolderId;
        if (!folderId) return;

        const btn = document.getElementById('folderShareGenerateBtn');
        const linkContainer = document.getElementById('folderShareLinkContainer');
        const linkInput = document.getElementById('folderShareLinkInput');

        btn.disabled = true;
        btn.innerHTML = `${Icons.loading_circle} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_generating', 'Generating...'))}`;

        try {
            const shareType = FileFoldersState.currentShareType;

            const shareData = await FolderAPI.shareFolder(folderId, shareType);
            const shareUrl = (typeof shareData.share_url === 'string' && /^https?:\/\//i.test(shareData.share_url))
                ? shareData.share_url
                : `${window.location.origin}${shareData.share_url || ''}`;

            linkInput.value = shareUrl;
            linkContainer.style.display = 'flex';
            btn.style.display = 'none';

            await this.loadShareStatus(folderId);
            await FileFoldersManager.loadFolders();
        } catch (error) {
            console.error('Failed to generate share link:', error);
            if (typeof showNotification === 'function') showNotification(fileFoldersT('files_folder_share_generate_error', 'Failed to generate share link'), 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = `${Icons.urlLink} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_generate_link', 'Generate Link'))}`;
        }
    },

    async copyShareLink() {
        const input = document.getElementById('folderShareLinkInput');
        const btn = document.getElementById('folderShareCopyBtn');
        if (!input || !input.value) return;

        try {
            await navigator.clipboard.writeText(input.value);
            btn.innerHTML = `${Icons.check} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_copied', 'Copied!'))}`;
            setTimeout(() => {
                btn.innerHTML = `${Icons.copy} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_copy', 'Copy'))}`;
            }, 2000);
        } catch (e) {
            input.select();
            document.execCommand('copy');
        }
    },

    async loadShareStatus(folderId) {
        try {
            const status = await FolderAPI.getShareStatus(folderId);
            const activeList = document.getElementById('folderShareActiveList');
            const activeSection = document.getElementById('folderShareActiveSection');

            const shares = [];
            if (status.clone_share_id) shares.push({ type: 'clone', id: status.clone_share_id, label: fileFoldersT('files_folder_share_clone', 'Clone'), icon: 'copy' });
            if (status.live_share_id) shares.push({ type: 'live', id: status.live_share_id, label: fileFoldersT('files_folder_share_live_view', 'Live (View Only)'), icon: 'eye', count: status.live_subscriber_count });
            if (status.collaborate_share_id) shares.push({ type: 'collaborate', id: status.collaborate_share_id, label: fileFoldersT('files_folder_share_collaborate', 'Collaborate'), icon: 'users', count: status.collaborate_subscriber_count });

            if (shares.length === 0) {
                activeSection.style.display = 'none';
                return;
            }

            activeSection.style.display = 'block';
            activeList.innerHTML = shares.map(share => `
                <div class="notes-share-active-item" data-share-type="${share.type}" data-share-id="${share.id}">
                    <div class="notes-share-active-info">
                        <span class="notes-share-active-label">${FolderRenderer.escapeHtml(share.label)}</span>
                        ${share.count ? `<span class="notes-share-active-count">${FolderRenderer.escapeHtml(fileFoldersFormatT(share.count === 1 ? 'files_folder_share_subscriber_one' : 'files_folder_share_subscriber_other', share.count === 1 ? '{count} subscriber' : '{count} subscribers', { count: share.count }))}</span>` : ''}
                    </div>
                    <div class="notes-share-active-actions">
                        <button type="button" class="notes-share-active-copy" data-url="${window.location.origin}/folders/${share.type}/${share.id}" title="${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_copy_link', 'Copy link'))}" aria-label="${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_copy_link', 'Copy link'))}">
                            ${Icons.copy}
                        </button>
                        <button type="button" class="notes-share-active-delete" data-share-type="${share.type}" title="${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_stop', 'Stop sharing'))}" aria-label="${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_stop', 'Stop sharing'))}">
                            ${Icons.trash}
                        </button>
                    </div>
                </div>
            `).join('');

            activeList.querySelectorAll('.notes-share-active-copy').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const url = btn.dataset.url;
                    try {
                        await navigator.clipboard.writeText(url);
                        btn.innerHTML = Icons.check;
                        setTimeout(() => {
                            btn.innerHTML = Icons.copy;
                        }, 1500);
                    } catch (e) {
                        console.error('Copy failed:', e);
                    }
                });
            });

            activeList.querySelectorAll('.notes-share-active-delete').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    await this.stopSharingByType(btn.dataset.shareType);
                });
            });
        } catch (error) {
            console.error('Failed to load share status:', error);
        }
    },

    async stopSharingByType(shareType) {
        const folderId = FileFoldersState.sharingFolderId;
        if (!folderId) return;
        const shareTypeLabels = {
            clone: fileFoldersT('files_folder_share_clone', 'Clone'),
            live: fileFoldersT('files_folder_share_live_view_short', 'Live View'),
            collaborate: fileFoldersT('files_folder_share_collaborate', 'Collaborate'),
        };
        try {
            await FolderAPI.deleteFolderShare(folderId, shareType);
            await this.loadShareStatus(folderId);
            await FileFoldersManager.loadFolders();
            if (typeof showNotification === 'function') showNotification(fileFoldersFormatT('files_folder_share_stopped', '{type} sharing stopped', { type: shareTypeLabels[shareType] || shareType }), 'success');
        } catch (error) {
            console.error('Failed to stop sharing:', error);
            if (typeof showNotification === 'function') showNotification(fileFoldersT('files_folder_share_stop_error', 'Failed to stop sharing'), 'error');
        }
    },

    hideShareModal() {
        const overlay = document.getElementById('folderShareOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            overlay.setAttribute('aria-hidden', 'true');
            overlay.inert = true;
            const previousFocus = overlay._previousFocus;
            overlay._previousFocus = null;
            if (overlay._closeTimer) clearTimeout(overlay._closeTimer);
            overlay._closeTimer = setTimeout(() => {
                overlay._closeTimer = null;
                if (overlay.classList.contains('active')) return;
                overlay.setAttribute('hidden', '');
                if (!overlay._bodyHadModalOpen) document.body?.classList?.remove?.('modal-open');
                overlay._bodyHadModalOpen = false;
                if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
            }, 200);
        }
        FileFoldersState.sharingFolderId = null;
        FileFoldersState.currentShareType = null;
    },

    async sendInvitations() {
        const folderId = FileFoldersState.sharingFolderId;
        if (!folderId) return;
        if (FileFoldersState.selectedUserIds.length === 0) {
            this.showInviteSelectionError();
            return;
        }

        const btn = document.getElementById('folderInviteBtn');
        btn.disabled = true;
        btn.innerHTML = `${Icons.loading_circle} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_sending', 'Sending...'))}`;

        try {
            const shareType = FileFoldersState.inviteShareType;

            const result = await FolderAPI.inviteUsersToFolder(folderId, FileFoldersState.selectedUserIds, shareType);

            if (typeof showNotification === 'function') {
                showNotification(getFolderInvitationSuccessMessage(result), 'success');
            }

            FileFoldersState.selectedUserIds = [];
            this.hideShareModal();
        } catch (error) {
            console.error('Failed to send invitations:', error);
            if (typeof showNotification === 'function') showNotification(fileFoldersT('files_folder_invite_error', 'Failed to send invitations'), 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = `${Icons.send} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_share_invite_selected', 'Invite Selected Users'))}`;
        }
    },
};

// ============================================================================
// Accept Shared Folder Modal
// ============================================================================
const FolderAcceptModal = {
    async showAcceptModal(shareId, shareType = null) {
        FileFoldersState.pendingShareId = shareId;
        FileFoldersState.pendingShareType = shareType;

        const overlay = document.getElementById('folderAcceptOverlay');
        const titleEl = document.getElementById('folderAcceptTitle');
        const ownerEl = document.getElementById('folderAcceptOwner');
        const previewEl = document.getElementById('folderAcceptPreviewContent');
        const confirmBtn = document.getElementById('folderAcceptConfirmBtn');
        const shareTypeInfoEl = document.getElementById('folderAcceptShareTypeInfo');

        if (!overlay) return;

        titleEl.textContent = fileFoldersT('files_folder_accept_loading', 'Loading...');
        ownerEl.textContent = '';
        previewEl.innerHTML = '';
        if (shareTypeInfoEl) shareTypeInfoEl.innerHTML = '';
        confirmBtn.disabled = true;

        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('active'));

        if (!FileFoldersState.acceptModalInitialized) {
            document.getElementById('folderAcceptCancelBtn')?.addEventListener('click', () => this.hideAcceptModal());
            document.getElementById('folderAcceptConfirmBtn')?.addEventListener('click', () => this.confirmAcceptShared());
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideAcceptModal(); });
            FileFoldersState.acceptModalInitialized = true;
        }

        try {
            const data = await FolderAPI.getSharedFolderPreview(shareId);
            titleEl.textContent = data.name || fileFoldersT('files_folder_unnamed', 'Unnamed Folder');
            ownerEl.textContent = data.owner_name
                ? fileFoldersFormatT('files_folder_accept_shared_by', 'Shared by {name}', { name: data.owner_name })
                : '';

            FileFoldersState.pendingShareType = data.share_type || shareType;

            if (shareTypeInfoEl) {
                const typeLabels = {
                    'clone': { label: fileFoldersT('files_folder_share_clone', 'Clone'), desc: fileFoldersT('files_folder_accept_clone_desc', 'You\'ll get your own copy of this folder and its files.'), color: '#8b5cf6' },
                    'live': { label: fileFoldersT('files_folder_share_live_view_short', 'Live View'), desc: fileFoldersT('files_folder_accept_live_desc', 'View-only with live updates. You cannot edit files.'), color: '#3b82f6' },
                    'collaborate': { label: fileFoldersT('files_folder_share_collaborate', 'Collaborate'), desc: fileFoldersT('files_folder_accept_collaborate_desc', 'You can view and possibly edit files in this folder with live sync.'), color: '#10b981' },
                };
                const typeInfo = typeLabels[data.share_type] || typeLabels['live'];
                shareTypeInfoEl.innerHTML = `
                    <div class="note-accept-share-type" style="background-color: ${typeInfo.color}20; border-color: ${typeInfo.color};">
                        <span class="note-accept-share-type-label" style="color: ${typeInfo.color};">${FolderRenderer.escapeHtml(typeInfo.label)}</span>
                        <span class="note-accept-share-type-desc">${FolderRenderer.escapeHtml(typeInfo.desc)}</span>
                    </div>
                `;
            }

            previewEl.innerHTML = `<p>${FolderRenderer.escapeHtml(data.name)} - ${FolderRenderer.escapeHtml(fileFoldersFormatT(data.file_count === 1 ? 'files_count_one' : 'files_count_other', data.file_count === 1 ? '{count} file' : '{count} files', { count: data.file_count }))}</p>`;

            if (data.share_type === 'clone') {
                confirmBtn.innerHTML = `${Icons.copy} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_accept_clone', 'Clone to My Files'))}`;
            } else {
                confirmBtn.innerHTML = `${Icons.plus} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_accept_add', 'Add to My Files'))}`;
            }

            confirmBtn.disabled = false;
        } catch (error) {
            const isOwnerError = error && (error.status === 400);
            if (isOwnerError) {
                this.hideAcceptModal();
                const warnMessage = error?.message || fileFoldersT('files_folder_accept_owner_error', 'You cannot open your own shared folder.');
                if (typeof notifyWarning === 'function') notifyWarning(warnMessage);
                else if (typeof showNotification === 'function') showNotification(warnMessage, 'warning');
                if (typeof window !== 'undefined') {
                    const path = window.location.pathname;
                    if (/\/folders\/(clone|live|collaborate)\//.test(path)) {
                        history.replaceState(null, '', '/');
                    }
                }
                return;
            }
            console.error('Failed to load shared folder preview:', error);
            titleEl.textContent = fileFoldersT('files_folder_accept_error_title', 'Error loading folder');
            previewEl.innerHTML = `<p style="color: #ef4444;">${FolderRenderer.escapeHtml(fileFoldersT('files_folder_accept_error_body', 'Could not load this shared folder. It may no longer exist.'))}</p>`;
        }
    },

    hideAcceptModal() {
        const overlay = document.getElementById('folderAcceptOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        FileFoldersState.pendingShareId = null;
        FileFoldersState.pendingShareType = null;
    },

    async confirmAcceptShared() {
        const shareId = FileFoldersState.pendingShareId;
        const shareType = FileFoldersState.pendingShareType;
        if (!shareId) return;

        const confirmBtn = document.getElementById('folderAcceptConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = `${Icons.loading_circle} ${FolderRenderer.escapeHtml(fileFoldersT('files_processing', 'Processing...'))}`;
        }

        try {
            let message = '';
            if (shareType === 'clone') {
                const result = await FolderAPI.cloneFolder(shareId);
                message = getFolderCloneSuccessMessage(result);
            } else {
                const result = await FolderAPI.acceptSharedFolder(shareId);
                message = getFolderAcceptSuccessMessage(result);
            }

            this.hideAcceptModal();
            await FileFoldersManager.loadFolders();
            if (typeof FilesManager !== 'undefined') FilesManager.refresh?.();

            if (typeof showNotification === 'function') showNotification(message, 'success');

            const path = window.location.pathname;
            if (path.includes('/folders/clone/') || path.includes('/folders/live/') || path.includes('/folders/collaborate/')) {
                history.replaceState(null, '', '/');
            }
        } catch (error) {
            console.error('Failed to accept shared folder:', error);
            if (typeof showNotification === 'function') showNotification(fileFoldersT('files_folder_accept_add_error', 'Failed to add folder'), 'error');
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = `${Icons.plus} ${FolderRenderer.escapeHtml(fileFoldersT('files_folder_accept_add', 'Add to My Files'))}`;
            }
        }
    },

    checkForSharedLink() {
        const path = window.location.pathname;

        const cloneMatch = path.match(/\/folders\/clone\/([a-zA-Z0-9-]+)/);
        if (cloneMatch) {
            this.ensureWorkspaceVisible();
            this.showAcceptModal(cloneMatch[1], 'clone');
            return true;
        }

        const liveMatch = path.match(/\/folders\/live\/([a-zA-Z0-9-]+)/);
        if (liveMatch) {
            this.ensureWorkspaceVisible();
            this.showAcceptModal(liveMatch[1], 'live');
            return true;
        }

        const collaborateMatch = path.match(/\/folders\/collaborate\/([a-zA-Z0-9-]+)/);
        if (collaborateMatch) {
            this.ensureWorkspaceVisible();
            this.showAcceptModal(collaborateMatch[1], 'collaborate');
            return true;
        }

        return false;
    },

    ensureWorkspaceVisible() {
        if (typeof showWorkspaceContainer === 'function') {
            showWorkspaceContainer({ tab: 'files' });
            return;
        }
        if (typeof WorkspaceManager !== 'undefined') {
            WorkspaceManager.setActiveTab?.('files');
            WorkspaceManager.show?.();
            WorkspaceManager.switchToTab?.('files');
        }
    },
};

// ============================================================================
// Expose globally
// ============================================================================
if (typeof window !== 'undefined') {
    window.FileFoldersManager = FileFoldersManager;
    window.FileFoldersState = FileFoldersState;
    window.FolderShareModal = FolderShareModal;
    window.FolderAcceptModal = FolderAcceptModal;
}

// ============================================================================
// Check for shared folder link on page load
// ============================================================================
(function initializeFolderSharing() {
    FolderAcceptModal.checkForSharedLink();
})();
