/**
* Files Management Module
* Handles file upload, download, delete, and display functionality
*/

// ============================================================================
// Constants & Configuration
// ============================================================================

const FILE_TYPE_ICONS = Object.freeze({
    'application/pdf': 'pdf.svg',
    'image/png': 'png.svg',
    'image/jpeg': 'jpg.svg',
    'image/jpg': 'jpg.svg',
    'image/gif': 'gif.svg',
    'image/bmp': 'bmp.svg',
    'image/svg+xml': 'svg.svg',
    'audio/mpeg': 'mp3.svg',
    'audio/mp3': 'mp3.svg',
    'audio/wav': 'mp3.svg',
    'audio/aac': 'aac.svg',
    'video/mp4': 'mpg.svg',
    'video/avi': 'avi.svg',
    'video/mov': 'mov.svg',
    'video/wmv': 'wmv.svg',
    'video/flv': 'flv.svg',
    'text/plain': 'txt.svg',
    'text/html': 'html.svg',
    'application/xhtml+xml': 'html.svg',
    'text/css': 'css.svg',
    'application/javascript': 'js.svg',
    'text/javascript': 'js.svg',
    'application/json': 'js.svg',
    'application/xml': 'xml.svg',
    'text/xml': 'xml.svg',
    'application/vnd.ms-excel': 'xls.svg',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xls.svg',
    'application/vnd.ms-powerpoint': 'ppt.svg',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'ppt.svg',
    'application/msword': 'txt.svg',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'txt.svg',
    'application/x-sql': 'sql.svg',
});

const API_ENDPOINTS = Object.freeze({
LIST: '/api/v1/files/',
WORKSPACE: '/api/v1/files/workspace',
UPLOAD: '/api/v1/files/upload',
DOWNLOAD: '/api/v1/files/download',
DELETE: '/api/v1/files/',
EDIT: '/api/v1/files/rename',
STORAGE_USAGE: '/api/v1/files/storage/usage',
});

const PROGRESS_HIDE_DELAY = 600;
const FILE_UPLOAD_STATUS_OWNER = 'workspace-file-upload';
const DEFAULT_ICON = 'txt.svg';
const FILES_PAGE_SIZE = 50;
const FILES_SEARCH_DEBOUNCE_MS = 250;
const FILES_LOAD_MORE_THRESHOLD = 160;
const SORT_CONFIG = Object.freeze({
DEFAULT_FIELD: 'name',
DEFAULT_DIRECTION: 'asc',
FIELDS: ['name', 'size', 'type', 'category', 'created_at', 'timestamp'],
DIRECTIONS: ['asc', 'desc'],
});

function filesT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
    return window.getTranslation(key, fallback);
    }
    return fallback;
}

function filesFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
    return window.formatTranslation(key, fallback, vars);
    }
    return String(filesT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
    const value = Object.prototype.hasOwnProperty.call(vars, token) ? vars[token] : '';
    return value == null ? '' : String(value);
    });
}

function xhrFetchWithUploadProgress(input, init = {}, onProgress) {
    return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const method = init.method || 'GET';
    xhr.open(method, input);
    if (init.credentials === 'include') {
        xhr.withCredentials = true;
    }

    const headers = new Headers(init.headers || undefined);
    headers.forEach((value, key) => {
        xhr.setRequestHeader(key, value);
    });

    xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable && onProgress) {
        onProgress((event.loaded / event.total) * 100);
        }
    });

    xhr.addEventListener('load', () => {
        resolve(new Response(xhr.responseText, {
        status: xhr.status,
        statusText: xhr.statusText,
        headers: parseXhrResponseHeaders(xhr.getAllResponseHeaders()),
        }));
    });
    xhr.addEventListener('error', () => reject(new Error(filesT('files_upload_failed', 'Upload failed'))));
    xhr.addEventListener('abort', () => reject(new Error(filesT('files_upload_cancelled', 'Upload cancelled'))));
    xhr.send(init.body || null);
    });
}

function parseXhrResponseHeaders(rawHeaders) {
    const headers = new Headers();
    String(rawHeaders || '').trim().split(/[\r\n]+/).forEach((line) => {
    if (!line) return;
    const separatorIndex = line.indexOf(':');
    if (separatorIndex <= 0) return;
    headers.append(line.slice(0, separatorIndex).trim(), line.slice(separatorIndex + 1).trim());
    });
    return headers;
}

// ============================================================================
// State Management
// ============================================================================

class FilesState {
constructor() {
    this.files = [];
    this.shouldAnimate = false;
    this.isUploading = false;
    this.initialized = false;
    this.sortField = SORT_CONFIG.DEFAULT_FIELD;
    this.sortDirection = SORT_CONFIG.DEFAULT_DIRECTION;
    this.activeFileContext = null;
    this.searchQuery = '';
    this.pageSize = FILES_PAGE_SIZE;
    this.offset = 0;
    this.total = 0;
    this.hasMore = false;
    this.isLoading = false;
    this.isLoadingMore = false;
    this.lastRequestId = 0;
    this.activeRequestId = 0;
    this.counts = {
    all: 0,
    uncategorized: 0,
    folders: {},
    };
}

setFiles(files) {
    this.files = Array.isArray(files) ? files : [];
    this.offset = this.files.length;
}

appendFiles(files) {
    const existingIds = new Set(this.files.map((file) => file?.file_id ?? file?.id));
    const nextFiles = Array.isArray(files)
    ? files.filter((file) => {
        const fileId = file?.file_id ?? file?.id;
        if (!fileId || existingIds.has(fileId)) {
        return false;
        }
        existingIds.add(fileId);
        return true;
    })
    : [];
    this.files = [...this.files, ...nextFiles];
    this.offset = this.files.length;
}

setAnimationState(shouldAnimate) {
    this.shouldAnimate = shouldAnimate;
}

setUploadingState(isUploading) {
    this.isUploading = isUploading;
}

setInitialized(initialized) {
    this.initialized = initialized;
}

resetAnimation() {
    this.shouldAnimate = false;
}

beginRequest() {
    this.lastRequestId += 1;
    this.activeRequestId = this.lastRequestId;
    return this.activeRequestId;
}

isLatestRequest(requestId) {
    return requestId === this.activeRequestId;
}

setSearchQuery(query) {
    this.searchQuery = String(query || '');
}

setLoadingState(isLoading, { isLoadingMore = false } = {}) {
    this.isLoading = Boolean(isLoading);
    this.isLoadingMore = Boolean(isLoading && isLoadingMore);
}

setWorkspaceResult(result, { append = false } = {}) {
    const items = Array.isArray(result?.items) ? result.items : [];
    if (append) {
    this.appendFiles(items);
    } else {
    this.setFiles(items);
    }
    this.total = Number(result?.total) || 0;
    this.hasMore = Boolean(result?.has_more);
    this.counts = {
    all: Number(result?.counts?.all) || 0,
    uncategorized: Number(result?.counts?.uncategorized) || 0,
    folders: result?.counts?.folders && typeof result.counts.folders === 'object'
        ? result.counts.folders
        : {},
    };
}

resetWorkspaceList() {
    this.files = [];
    this.offset = 0;
    this.total = 0;
    this.hasMore = false;
}

setSortField(field) {
    if (SORT_CONFIG.FIELDS.includes(field)) {
    this.sortField = field;
    }
}

setSortDirection(direction) {
    if (SORT_CONFIG.DIRECTIONS.includes(direction)) {
    this.sortDirection = direction;
    }
}

toggleSortDirection() {
    this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
}

getFileById(fileId) {
    if (!fileId) return null;
    return this.files.find((file) => (file?.file_id ?? file?.id) === fileId) || null;
}
}

const state = new FilesState();
const fileDeleteModalState = {
fileId: '',
fileName: '',
isProcessing: false,
lastFocusedElement: null,
};
const fileStorageUsageModalState = {
isLoading: false,
lastFocusedElement: null,
lastPayload: null,
requestId: 0,
};

function formatStoredFileCount(value) {
    const count = Number(value) || 0;
    return new Intl.NumberFormat().format(count);
}

function clampUsagePercent(value) {
    const percent = Number(value);
    if (!Number.isFinite(percent)) {
    return 0;
    }
    return Math.max(0, Math.min(100, percent));
}

const FilesCache = {
files: [],
lastFetched: 0,
queryKey: '',
set(files, queryKey = '') {
    this.files = Array.isArray(files) ? files : [];
    this.lastFetched = Date.now();
    this.queryKey = queryKey;
},
get() {
    return this.files;
},
isFresh(maxAgeMs = 30000, queryKey = this.queryKey) {
    if (!this.files.length) return false;
    if (queryKey !== this.queryKey) return false;
    return (Date.now() - this.lastFetched) < maxAgeMs;
},
};

function resolveAllowFileUploadsSetting(defaultValue = true) {
    const parseBoolean = (value, fallback = defaultValue) => {
    if (value === null || typeof value === 'undefined') return fallback;
    if (typeof value === 'boolean') return value;
    const normalized = String(value).trim().toLowerCase();
    if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
    if (['false', '0', 'no', 'off'].includes(normalized)) return false;
    return fallback;
    };

    try {
    const storageValue = typeof localStorage !== 'undefined'
        ? localStorage.getItem('allow_file_uploads')
        : null;
    if (storageValue !== null) {
        return parseBoolean(storageValue, defaultValue);
    }
    } catch (_) { /* ignore storage errors */ }

    const chatSetup = typeof window !== 'undefined' ? window.chatSetup : undefined;
    if (chatSetup && Object.prototype.hasOwnProperty.call(chatSetup, 'allow_file_uploads')) {
    return parseBoolean(chatSetup.allow_file_uploads, defaultValue);
    }

    return defaultValue;
}

// ============================================================================
// DOM Helpers
// ============================================================================

const DOM = {
get workspaceSectionFiles() { return document.getElementById('workspaceSectionFiles'); },
get uploadButton() { return document.getElementById('mainContainerHeaderFilesUpload'); },
get emptyUploadButton() { return document.getElementById('filesEmptyUploadBtn'); },
get fileInput() { return document.getElementById('fileInput'); },
get filesList() { return document.getElementById('filesList'); },
get filesTable() { return document.getElementById('filesTable'); },
get filesTableBody() { return document.getElementById('filesTableBody'); },
get emptyState() { return document.getElementById('emptyState'); },
get loadingState() { return document.getElementById('filesLoadingState'); },
get listStatus() { return document.getElementById('filesListStatus'); },
get filesContainer() { return document.getElementById('filesContainer'); },
get filesSidebar() { return document.getElementById('filesFolderSidebar'); },
get filesSidebarBackdrop() { return document.getElementById('filesSidebarBackdrop'); },
get filesSidebarToggle() { return document.getElementById('filesFolderMobileSidebarToggle'); },
get searchInput() { return document.getElementById('filesSearchInput'); },
get searchClear() { return document.getElementById('filesSearchClear'); },
get fileSortHeaders() { return document.querySelectorAll('.files-table .sortable-column'); },
get filesActionsTrigger() { return document.getElementById('filesActionsTrigger'); },
get filesActionsDropdown() { return document.getElementById('filesActionsDropdown'); },
get filesStorageUsageButton() { return document.getElementById('filesStorageUsageButton'); },
get filesStorageUsageOverlay() { return document.getElementById('filesStorageUsageOverlay'); },
get filesStorageUsageClose() { return document.getElementById('filesStorageUsageClose'); },
get filesStorageUsageStatus() { return document.getElementById('filesStorageUsageStatus'); },
get filesStorageUsageUploadsDisabled() { return document.getElementById('filesStorageUsageUploadsDisabled'); },
get filesStorageUsageStorageMeter() { return document.getElementById('filesStorageUsageStorageMeter'); },
get filesStorageUsageCountMeter() { return document.getElementById('filesStorageUsageCountMeter'); },
get filesStorageUsageStorageText() { return document.getElementById('filesStorageUsageStorageText'); },
get filesStorageUsageCountText() { return document.getElementById('filesStorageUsageCountText'); },
get filesStorageUsageStorageProgress() { return document.getElementById('filesStorageUsageStorageProgress'); },
get filesStorageUsageCountProgress() { return document.getElementById('filesStorageUsageCountProgress'); },
get filesStorageUsageStorageBar() { return document.getElementById('filesStorageUsageStorageBar'); },
get filesStorageUsageCountBar() { return document.getElementById('filesStorageUsageCountBar'); },
get filesDeleteWebsearchButton() { return document.getElementById('filesDeleteWebsearchButton'); },
get filesDeleteWebsearchOverlay() { return document.getElementById('filesDeleteWebsearchOverlay'); },
get filesDeleteWebsearchCancel() { return document.getElementById('filesDeleteWebsearchCancel'); },
get filesDeleteWebsearchConfirm() { return document.getElementById('filesDeleteWebsearchConfirm'); },
get filesDeleteOverlay() { return document.getElementById('filesDeleteOverlay'); },
get filesDeleteCancel() { return document.getElementById('filesDeleteCancel'); },
get filesDeleteConfirm() { return document.getElementById('filesDeleteConfirm'); },
get filesDeleteConfirmText() { return document.getElementById('filesDeleteConfirmText'); },
get filesDeleteFileName() { return document.getElementById('filesDeleteFileName'); },
get filesPreviewSidebar() { return document.getElementById('filesPreviewSidebar'); },
get filesPreviewBackdrop() { return document.getElementById('filesPreviewBackdrop'); },
get filesPreviewResizeHandle() { return document.getElementById('filesPreviewResizeHandle'); },
get filesPreviewDragHandle() { return document.getElementById('filesPreviewDragHandle'); },
get filesPreviewClose() { return document.getElementById('filesSidebarPreviewClose'); },
get filesPreviewDownload() { return document.getElementById('filesSidebarPreviewDownload'); },
get filesPreviewBody() { return document.getElementById('filesPreviewBody'); },
get filesPreviewTitle() { return document.getElementById('filesPreviewTitle'); },
get toast() { return document.getElementById('toast'); },
get dropOverlay() { return document.getElementById('chatDropOverlay'); },
get dropOverlayTitle() { return document.getElementById('dropOverlayTitle'); },
get dropOverlaySubtitle() { return document.getElementById('dropOverlaySubtitle'); },
get filesContent() { return document.getElementById('filesContent'); },
get filesContentScroller() { return document.getElementById('filesContentScroller'); },
get fileEditModalOverlay() { return document.getElementById('filesEditModalOverlay'); },
get fileEditNameInput() { return document.getElementById('fileEditNameInput'); },
get fileEditNameError() { return document.getElementById('fileEditNameError'); },
get editFileCancelBtn() { return document.getElementById('editFileCancelBtn'); },
get saveFileChangesBtn() { return document.getElementById('saveFileChangesBtn'); },
get fileEditModalClose() { return document.getElementById('filesEditModalClose'); },
};

const ViewManager = {
showFilesListView() {
    const listView = DOM.filesContent;
    if (listView) {
    listView.style.display = '';
    listView.removeAttribute('aria-hidden');
    }
},

isFileDeleteModalOpen() {
    const overlay = DOM.filesDeleteOverlay;
    return Boolean(overlay && !overlay.hasAttribute('hidden'));
},

setFileDeleteProcessing(isProcessing) {
    fileDeleteModalState.isProcessing = Boolean(isProcessing);

    if (DOM.filesDeleteConfirm) {
    DOM.filesDeleteConfirm.disabled = fileDeleteModalState.isProcessing;
    }
    if (DOM.filesDeleteCancel) {
    DOM.filesDeleteCancel.disabled = fileDeleteModalState.isProcessing;
    }
    if (DOM.filesDeleteConfirmText) {
    DOM.filesDeleteConfirmText.textContent = fileDeleteModalState.isProcessing
        ? filesT('files_deleting', 'Deleting...')
        : filesT('files_delete_action', 'Delete');
    }
},

openFileDeleteModal(file, triggerElement = null) {
    const overlay = DOM.filesDeleteOverlay;
    if (!overlay) {
    return;
    }

    const resolvedFileId = file?.file_id ?? file?.id ?? '';
    const resolvedFileName = file?.meta?.original_filename || file?.file_name || file?.name || filesT('files_this_file', 'this file');

    fileDeleteModalState.fileId = resolvedFileId;
    fileDeleteModalState.fileName = resolvedFileName;
    fileDeleteModalState.lastFocusedElement = triggerElement instanceof HTMLElement ? triggerElement : document.activeElement;

    if (DOM.filesDeleteFileName) {
    DOM.filesDeleteFileName.textContent = resolvedFileName;
    }

    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    this.setFileDeleteProcessing(false);
    DOM.filesDeleteCancel?.focus();
},

closeFileDeleteModal({ restoreFocus = true, force = false } = {}) {
    const overlay = DOM.filesDeleteOverlay;
    if (!overlay || overlay.hasAttribute('hidden')) {
    return;
    }
    if (fileDeleteModalState.isProcessing && !force) {
    return;
    }

    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    this.setFileDeleteProcessing(false);

    const lastFocusedElement = fileDeleteModalState.lastFocusedElement;
    fileDeleteModalState.fileId = '';
    fileDeleteModalState.fileName = '';
    fileDeleteModalState.lastFocusedElement = null;

    if (restoreFocus && lastFocusedElement instanceof HTMLElement) {
    lastFocusedElement.focus();
    }
},

isStorageUsageModalOpen() {
    const overlay = DOM.filesStorageUsageOverlay;
    return Boolean(overlay && !overlay.hasAttribute('hidden'));
},

openStorageUsageModal(triggerElement = null) {
    const overlay = DOM.filesStorageUsageOverlay;
    if (!overlay) {
    return;
    }

    fileStorageUsageModalState.lastFocusedElement = triggerElement instanceof HTMLElement ? triggerElement : document.activeElement;
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    DOM.filesStorageUsageClose?.focus();
    this.refreshStorageUsage();
},

closeStorageUsageModal({ restoreFocus = true } = {}) {
    const overlay = DOM.filesStorageUsageOverlay;
    if (!overlay || overlay.hasAttribute('hidden')) {
    return;
    }

    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');

    const lastFocusedElement = fileStorageUsageModalState.lastFocusedElement;
    fileStorageUsageModalState.lastFocusedElement = null;

    if (restoreFocus && lastFocusedElement instanceof HTMLElement) {
    lastFocusedElement.focus();
    }
},

setStorageUsageLoading(isLoading) {
    fileStorageUsageModalState.isLoading = Boolean(isLoading);
    const status = DOM.filesStorageUsageStatus;
    if (status) {
    status.hidden = !fileStorageUsageModalState.isLoading;
    status.textContent = filesT('files_storage_usage_loading', 'Loading storage usage...');
    }
},

setStorageUsageError() {
    const status = DOM.filesStorageUsageStatus;
    if (status) {
    status.hidden = false;
    status.textContent = filesT('files_storage_usage_load_failed', 'Unable to load storage limits. Try again.');
    }
},

renderStorageUsage(payload) {
    fileStorageUsageModalState.lastPayload = payload;
    const status = DOM.filesStorageUsageStatus;
    if (status) {
    status.hidden = true;
    status.textContent = '';
    }

    const storageBytes = Number(payload?.storage_bytes) || 0;
    const storageLimit = payload?.storage_bytes_limit;
    const storageLimitIsFinite = storageLimit !== null
    && storageLimit !== undefined
    && Number.isFinite(Number(storageLimit))
    && Number(storageLimit) >= 0;
    const storagePercent = storageLimitIsFinite ? clampUsagePercent(payload?.storage_percent) : 0;
    const storageText = storageLimitIsFinite
    ? filesFormatT('files_storage_usage_used_of_limit', '{used} of {limit}', {
        used: Utils.formatFileSize(storageBytes),
        limit: Utils.formatFileSize(Number(storageLimit)),
    })
    : filesFormatT('files_storage_usage_used_of_unlimited', '{used} of unlimited', {
        used: Utils.formatFileSize(storageBytes),
    });

    const fileCount = Number(payload?.file_count) || 0;
    const fileLimit = payload?.file_count_limit;
    const fileLimitIsFinite = fileLimit !== null
    && fileLimit !== undefined
    && Number.isFinite(Number(fileLimit))
    && Number(fileLimit) >= 0;
    const filePercent = fileLimitIsFinite ? clampUsagePercent(payload?.file_count_percent) : 0;
    const countText = fileLimitIsFinite
    ? filesFormatT('files_storage_usage_used_of_limit', '{used} of {limit}', {
        used: formatStoredFileCount(fileCount),
        limit: formatStoredFileCount(Number(fileLimit)),
    })
    : filesFormatT('files_storage_usage_used_of_unlimited', '{used} of unlimited', {
        used: formatStoredFileCount(fileCount),
    });

    if (DOM.filesStorageUsageStorageText) {
    DOM.filesStorageUsageStorageText.textContent = storageText;
    }
    if (DOM.filesStorageUsageCountText) {
    DOM.filesStorageUsageCountText.textContent = countText;
    }
    if (DOM.filesStorageUsageStorageBar) {
    DOM.filesStorageUsageStorageBar.style.width = `${storagePercent}%`;
    }
    if (DOM.filesStorageUsageCountBar) {
    DOM.filesStorageUsageCountBar.style.width = `${filePercent}%`;
    }
    if (DOM.filesStorageUsageStorageProgress) {
    DOM.filesStorageUsageStorageProgress.setAttribute('aria-valuenow', String(Math.round(storagePercent)));
    DOM.filesStorageUsageStorageProgress.setAttribute('aria-valuetext', storageText);
    }
    if (DOM.filesStorageUsageCountProgress) {
    DOM.filesStorageUsageCountProgress.setAttribute('aria-valuenow', String(Math.round(filePercent)));
    DOM.filesStorageUsageCountProgress.setAttribute('aria-valuetext', countText);
    }
    if (DOM.filesStorageUsageUploadsDisabled) {
    DOM.filesStorageUsageUploadsDisabled.hidden = payload?.uploads_allowed !== false;
    }
},

async refreshStorageUsage({ silent = false } = {}) {
    if (!this.isStorageUsageModalOpen()) {
    return;
    }

    const requestId = fileStorageUsageModalState.requestId + 1;
    fileStorageUsageModalState.requestId = requestId;
    const isLatestRequest = () => requestId === fileStorageUsageModalState.requestId;

    if (!silent) {
    this.setStorageUsageLoading(true);
    }

    try {
    const payload = await API.fetchStorageUsage();
    if (!isLatestRequest() || !this.isStorageUsageModalOpen()) {
        return;
    }
    this.renderStorageUsage(payload);
    } catch (error) {
    if (!isLatestRequest() || !this.isStorageUsageModalOpen()) {
        return;
    }
    console.error('Failed to load file storage usage:', error);
    this.setStorageUsageError();
    } finally {
    // A newer request owns the loading state. Do not return from `finally`:
    // doing so can suppress an exception raised earlier in the refresh flow.
    if (isLatestRequest()) {
        fileStorageUsageModalState.isLoading = false;
    }
    }
},

openFileEditModal(file) {
    const overlay = DOM.fileEditModalOverlay;
    const input = DOM.fileEditNameInput;
    if (overlay) {
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    }
    if (input) {
    const originalName = file?.meta?.original_filename || file?.original_filename || '';
    input.value = originalName;
    input.focus();
    input.select();
    input.dataset.fileId = file?.file_id ?? file?.id ?? '';
    window.FormValidation?.clearInputError(input, DOM.fileEditNameError);
    }
},

closeFileEditModal() {
    const overlay = DOM.fileEditModalOverlay;
    if (overlay) {
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    }
    const input = DOM.fileEditNameInput;
    if (input) {
    input.value = '';
    input.dataset.fileId = '';
    window.FormValidation?.clearInputError(input, DOM.fileEditNameError);
    }
},
};

const isFilesViewVisible = () => {
    const container = DOM.filesContainer;
    if (!container) return false;

    const style = window.getComputedStyle(container);
    if (style.display === 'none' || style.visibility === 'hidden') {
    return false;
    }

    const rect = container.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
};

const getDropOverlayDefaults = () => ({
    title: filesT('dropOverlay.title', 'Drop files to attach'),
    subtitle: filesT('dropOverlay.subtitle', 'Your files will be uploaded to this chat.'),
});
const FILES_DROP_OVERLAY_OWNER_ATTR = 'data-drop-overlay-owner';
const FILES_DROP_OVERLAY_OWNER_FILES = 'workspace-files';

const WorkspaceDropUpload = {
    dragCounter: 0,
    listenersBound: false,
    boundDragEnter: null,
    boundDragOver: null,
    boundDragLeave: null,
    boundDrop: null,
    boundReset: null,

    isWorkspaceDragTarget(event) {
    const container = DOM.filesContainer;
    if (!container) return false;
    const overlay = DOM.dropOverlay;
    // While the Workspace Files view is active, treat the shared drop
    // overlay as part of our drag target. Once the overlay is shown it
    // covers the viewport with pointer-events:auto and intercepts the
    // next dragenter — without this, the workspace's previous element
    // dragleaves to counter 0, the overlay hides, then dragenters
    // alternate, causing a visible flicker.
    const overlayOwner = overlay ? overlay.getAttribute(FILES_DROP_OVERLAY_OWNER_ATTR) : null;
    const overlayIsOurs = overlay
        && (!overlayOwner || overlayOwner === FILES_DROP_OVERLAY_OWNER_FILES);
    const path = typeof event?.composedPath === 'function' ? event.composedPath() : null;
    if (Array.isArray(path) && path.length > 0) {
        if (path.includes(container)) return true;
        if (overlayIsOurs && overlay && path.includes(overlay)) return true;
        return false;
    }
    const target = event?.target;
    if (!(target instanceof Node)) return false;
    if (container.contains(target)) return true;
    if (overlayIsOurs && overlay && (overlay === target || overlay.contains(target))) return true;
    return false;
    },

    isFileDragEvent(event) {
    const dataTransfer = event?.dataTransfer;
    if (!dataTransfer) return false;

    const files = dataTransfer.files;
    if (files && files.length > 0) {
        return true;
    }

    const items = dataTransfer.items;
    if (items && items.length > 0) {
        for (const item of Array.from(items)) {
        if (item && item.kind === 'file') {
            return true;
        }
        }
    }

    const types = dataTransfer.types;
    if (!types) return false;
    const normalizedTypes = Array.from(types).map((type) => String(type || '').toLowerCase());
    return normalizedTypes.includes('files')
        || normalizedTypes.includes('application/x-moz-file')
        || normalizedTypes.includes('public.file-url');
    },

    isInternalWorkspaceDrag(event) {
    const types = Array.from(event?.dataTransfer?.types || []).map((type) => String(type || '').toLowerCase());
    return types.includes('application/x-file-id');
    },

    isLikelyExternalFileDrag(event) {
    if (this.isInternalWorkspaceDrag(event)) {
        return false;
    }
    const types = Array.from(event?.dataTransfer?.types || []).map((type) => String(type || '').toLowerCase());
    return types.includes('files')
        || types.includes('application/x-moz-file')
        || types.includes('public.file-url');
    },

    extractFilesFromDataTransfer(dataTransfer) {
    if (!dataTransfer) return [];
    if (dataTransfer.files && dataTransfer.files.length > 0) {
        return Array.from(dataTransfer.files);
    }
    const collected = [];
    const items = dataTransfer.items;
    if (!items || !items.length) {
        return collected;
    }
    for (const item of Array.from(items)) {
        if (!item || item.kind !== 'file') continue;
        const file = typeof item.getAsFile === 'function' ? item.getAsFile() : null;
        if (file) {
        collected.push(file);
        }
    }
    return collected;
    },

    isActiveWorkspaceView() {
    return typeof isFilesViewVisible === 'function' ? isFilesViewVisible() : false;
    },

    canUploadFiles() {
    return resolveAllowFileUploadsSetting(true);
    },

    isSkillImportModalOpen() {
    const skillImportOverlay = document.getElementById('skillImportOverlay');
    return Boolean(
        skillImportOverlay
        && !skillImportOverlay.hasAttribute('hidden')
        && skillImportOverlay.getAttribute('aria-hidden') !== 'true',
    );
    },

    setOverlayVisible(active) {
    const overlay = DOM.dropOverlay;
    if (!overlay) return;

    if (active) {
        overlay.setAttribute(FILES_DROP_OVERLAY_OWNER_ATTR, FILES_DROP_OVERLAY_OWNER_FILES);
        if (DOM.dropOverlayTitle) {
        DOM.dropOverlayTitle.textContent = filesT('files_drop_upload_title', 'Drop files to upload');
        }
        if (DOM.dropOverlaySubtitle) {
        DOM.dropOverlaySubtitle.textContent = filesT('files_drop_upload_subtitle', 'Your files will be uploaded to Workspace Files.');
        }
        overlay.classList.add('active');
        overlay.setAttribute('aria-hidden', 'false');
        return;
    }

    const overlayOwner = overlay.getAttribute(FILES_DROP_OVERLAY_OWNER_ATTR);
    if (overlayOwner && overlayOwner !== FILES_DROP_OVERLAY_OWNER_FILES) {
        return;
    }

    overlay.removeAttribute(FILES_DROP_OVERLAY_OWNER_ATTR);
    overlay.classList.remove('active');
    overlay.setAttribute('aria-hidden', 'true');
    const dropOverlayDefaults = getDropOverlayDefaults();
    if (DOM.dropOverlayTitle) {
        DOM.dropOverlayTitle.textContent = dropOverlayDefaults.title;
    }
    if (DOM.dropOverlaySubtitle) {
        DOM.dropOverlaySubtitle.textContent = dropOverlayDefaults.subtitle;
    }
    },

    reset() {
    this.dragCounter = 0;
    this.setOverlayVisible(false);
    },

    handleDragEnter(event) {
    // The skill importer is an exclusive file-drop surface. These listeners
    // run in the capture phase, so they must yield before stopping propagation
    // or the modal's own dropzone never receives the drag event.
    if (this.isSkillImportModalOpen()) {
        this.reset();
        return;
    }
    if (!this.isActiveWorkspaceView()) return;
    if (this.isInternalWorkspaceDrag(event)) return;
    if (!this.isWorkspaceDragTarget(event)) return;
    if (!this.isFileDragEvent(event) && !this.isLikelyExternalFileDrag(event)) return;

    if (event.cancelable) event.preventDefault();
    if (typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }

    this.dragCounter += 1;
    if (this.canUploadFiles()) {
        this.setOverlayVisible(true);
    }
    },

    handleDragOver(event) {
    if (this.isSkillImportModalOpen()) {
        this.reset();
        return;
    }
    if (!this.isActiveWorkspaceView()) return;
    if (this.isInternalWorkspaceDrag(event)) return;
    if (!this.isWorkspaceDragTarget(event)) return;
    if (!this.isFileDragEvent(event) && !this.isLikelyExternalFileDrag(event)) return;

    if (event.cancelable) event.preventDefault();
    if (typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }

    const canUpload = this.canUploadFiles();
    try {
        event.dataTransfer.dropEffect = canUpload ? 'copy' : 'none';
    } catch (_) {}

    if (canUpload) {
        this.setOverlayVisible(true);
    } else {
        this.setOverlayVisible(false);
    }
    },

    handleDragLeave(event) {
    if (this.isSkillImportModalOpen()) {
        this.reset();
        return;
    }
    if (this.isInternalWorkspaceDrag(event)) return;
    if (!this.isActiveWorkspaceView()) {
        this.reset();
        return;
    }

    if (!this.isWorkspaceDragTarget(event)) {
        return;
    }

    if (this.dragCounter > 0 && !this.isFileDragEvent(event) && !this.isLikelyExternalFileDrag(event)) {
        return;
    }

    if (typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }

    this.dragCounter = Math.max(0, this.dragCounter - 1);
    if (this.dragCounter === 0) {
        this.setOverlayVisible(false);
    }
    },

    handleDrop(event) {
    if (this.isSkillImportModalOpen()) {
        this.reset();
        return;
    }
    if (!this.isActiveWorkspaceView()) return;
    if (this.isInternalWorkspaceDrag(event)) return;
    if (!this.isWorkspaceDragTarget(event)) return;

    const files = this.extractFilesFromDataTransfer(event.dataTransfer);
    if (!files.length) {
        this.reset();
        return;
    }

    if (event.cancelable) event.preventDefault();
    if (typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }
    if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
    }
    this.reset();

    if (!this.canUploadFiles()) {
        notifyError?.(filesT('files_upload_disabled', 'File uploads are disabled for your account.'));
        return;
    }

    try {
        const result = FileOperations.uploadFiles(files);
        if (result && typeof result.catch === 'function') {
        result.catch((error) => {
            console.error('Workspace drop upload failed', error);
            notifyError?.(filesT('files_drop_upload_failed', 'Failed to upload dropped files.'));
        });
        }
    } catch (error) {
        console.error('Workspace drop upload failed', error);
        notifyError?.(filesT('files_drop_upload_failed', 'Failed to upload dropped files.'));
    }
    },

    bindGlobalListeners() {
    if (this.listenersBound || typeof window === 'undefined') {
        return;
    }

    this.boundDragEnter = (event) => this.handleDragEnter(event);
    this.boundDragOver = (event) => this.handleDragOver(event);
    this.boundDragLeave = (event) => this.handleDragLeave(event);
    this.boundDrop = (event) => this.handleDrop(event);
    this.boundReset = () => this.reset();

    window.addEventListener('dragenter', this.boundDragEnter, true);
    window.addEventListener('dragover', this.boundDragOver, true);
    window.addEventListener('dragleave', this.boundDragLeave, true);
    window.addEventListener('drop', this.boundDrop, true);
    window.addEventListener('dragend', this.boundReset, true);
    window.addEventListener('blur', this.boundReset);

    if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            this.reset();
        }
        });
    }

    this.listenersBound = true;
    },
};

// ============================================================================
// Utility Functions
// ============================================================================

const Utils = {
getFileExtension(filename) {
    const normalized = String(filename || '').trim();
    const lastDotIndex = normalized.lastIndexOf('.');
    if (lastDotIndex <= 0 || lastDotIndex === normalized.length - 1) {
    return '';
    }
    return normalized.slice(lastDotIndex + 1).toUpperCase();
},

formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    
    const units = ['B', 'KB', 'MB', 'GB'];
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    const size = (bytes / Math.pow(k, i)).toFixed(2);
    
    return `${size} ${units[i]}`;
},

getFileIcon(fileType) {
    return FILE_TYPE_ICONS[fileType] || DEFAULT_ICON;
},

extractFilenameFromHeader(contentDisposition) {
    if (!contentDisposition) return '';

    try {
        const filenameStarMatch = contentDisposition.match(/filename\*=([^;]+)/i);
        if (filenameStarMatch) {
        const value = filenameStarMatch[1].trim().replace(/^"|"$/g, '');
        const parts = value.split("''");
        if (parts.length === 2) {
            try {
            const decoded = decodeURIComponent(parts[1].replace(/\+/g, '%20'));
            if (decoded) {
                return decoded;
            }
            } catch (_) {
            // ignore decode errors and fall through
            }
        }

        if (value) {
            return value;
        }
        }

        const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
        if (filenameMatch && filenameMatch[1]) {
        return filenameMatch[1];
        }

        const fallbackMatch = contentDisposition.match(/filename=([^;]+)/i);
        if (fallbackMatch && fallbackMatch[1]) {
        return fallbackMatch[1].replace(/^"|"$/g, '').trim();
        }
    } catch (_) {
        // ignore parsing errors and fall through to default
    }

    return '';
    },

escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
},

};

const Sorting = {
sortFiles(files, field, direction) {
    if (!Array.isArray(files) || files.length === 0) {
    return [];
    }

    const targetField = SORT_CONFIG.FIELDS.includes(field) ? field : SORT_CONFIG.DEFAULT_FIELD;
    const targetDirection = SORT_CONFIG.DIRECTIONS.includes(direction) ? direction : SORT_CONFIG.DEFAULT_DIRECTION;
    const sorted = [...files];

    sorted.sort((a, b) => Sorting.compare(a, b, targetField));

    if (targetDirection === 'desc') {
    sorted.reverse();
    }

    return sorted;
},

compare(a, b, field) {
    const primary = Sorting.compareByField(a, b, field);
    if (primary !== 0) {
    return primary;
    }
    return Sorting.compareByField(a, b, 'name');
},

compareByField(a, b, field) {
    switch (field) {
    case 'size': {
        const aSize = Number(a?.file_size) || 0;
        const bSize = Number(b?.file_size) || 0;
        return aSize - bSize;
    }
    case 'type': {
        const aType = String(a?.file_type || '').toLowerCase();
        const bType = String(b?.file_type || '').toLowerCase();
        return aType.localeCompare(bType, undefined, { sensitivity: 'base' });
    }
    case 'category': {
        const aCategory = Sorting.getCategory(a);
        const bCategory = Sorting.getCategory(b);
        return aCategory.localeCompare(bCategory, undefined, { sensitivity: 'base' });
    }
    case 'name':
    default: {
        const aName = String(a?.meta?.original_filename || '').toLowerCase();
        const bName = String(b?.meta?.original_filename || '').toLowerCase();
        return aName.localeCompare(bName, undefined, { sensitivity: 'base' });
    }
    }
},

getCategory(file) {
    const category = String(file?.file_category || '').toLowerCase();
    if (category) {
    return category;
    }

    const fileType = String(file?.file_type || '').toLowerCase();
    if (!fileType) {
    return 'other';
    }

    if (fileType.startsWith('image/')) {
    return 'image';
    }
    if (fileType.startsWith('audio/')) {
    return 'audio';
    }
    if (fileType.startsWith('video/')) {
    return 'video';
    }
    if (
    fileType.startsWith('application/pdf') ||
    fileType.includes('msword') ||
    fileType.includes('wordprocessingml') ||
    fileType.includes('excel') ||
    fileType.includes('spreadsheetml') ||
    fileType.includes('powerpoint') ||
    fileType.includes('presentationml') ||
    fileType.startsWith('text/') ||
    fileType === 'application/json' ||
    fileType === 'application/xml'
    ) {
    return 'document';
    }

    return 'other';
},
};

// ============================================================================
// UI Components
// ============================================================================

const UI = {

uploadProgressMessage: '',

updateUploadUI(isUploading) {
    const { uploadButton, emptyUploadButton, fileInput } = DOM;

    if (uploadButton) {
    uploadButton.disabled = isUploading;
    uploadButton.classList.toggle('is-uploading', isUploading);
    uploadButton.setAttribute('aria-busy', String(isUploading));
    }

    if (emptyUploadButton) {
    emptyUploadButton.disabled = isUploading;
    emptyUploadButton.classList.toggle('is-uploading', isUploading);
    emptyUploadButton.setAttribute('aria-busy', String(isUploading));
    }

    if (fileInput) {
    fileInput.disabled = isUploading;
    }
},

showProgress(filename) {
    this.uploadProgressMessage = filesFormatT(
    'files_upload_progress_named',
    'Uploading {filename}...',
    { filename },
    );
    window.dataControlStatusBanner?.show(this.uploadProgressMessage, {
    owner: FILE_UPLOAD_STATUS_OWNER,
    busy: true,
    percent: 0,
    });
},

updateProgress(percent) {
    window.dataControlStatusBanner?.show(this.uploadProgressMessage, {
    owner: FILE_UPLOAD_STATUS_OWNER,
    busy: true,
    percent,
    });
},

hideProgress(delay = 0) {
    return new Promise(resolve => {
    setTimeout(() => {
        window.dataControlStatusBanner?.hide(FILE_UPLOAD_STATUS_OWNER);
        this.uploadProgressMessage = '';
        resolve();
    }, delay);
    });
},

renderFiles() {
    const { filesList, emptyState, loadingState, listStatus } = DOM;
    UI.updateUploadAvailability();
    UI.updateSearchControls(false);

    const files = Array.isArray(state.files) ? state.files : [];
    const hasFiles = files.length > 0;
    const showInitialLoading = state.isLoading && !state.isLoadingMore && !hasFiles;

    if (loadingState) {
    loadingState.style.display = showInitialLoading ? 'flex' : 'none';
    }

    if (showInitialLoading) {
    if (filesList) filesList.style.display = 'none';
    if (emptyState) emptyState.style.display = 'none';
    if (listStatus) listStatus.style.display = 'none';
    return;
    }

    if (!hasFiles) {
    if (filesList) {
        filesList.style.display = 'none';
        filesList.innerHTML = '';
    }
    UI.renderEmptyState();
    UI.renderListStatus();
    return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (filesList) {
    filesList.style.display = 'block';
    filesList.innerHTML = files.map(file => this.createFileItem(file)).join('');
    FilesMenus.attach(files);
    FileDragDrop.setupAllFileItems();
    }

    UI.renderListStatus();

    state.resetAnimation();
},

updateSearchControls(syncValue = false) {
    const { searchInput, searchClear } = DOM;
    if (searchInput && syncValue && searchInput.value !== state.searchQuery) {
    searchInput.value = state.searchQuery;
    }
    if (searchClear) {
    const currentValue = searchInput ? searchInput.value : state.searchQuery;
    const shouldShow = Boolean(String(currentValue || '').trim());
    searchClear.classList.toggle('visible', shouldShow);
    searchClear.hidden = !shouldShow;
    }
},

renderEmptyState() {
    const { emptyState } = DOM;
    if (!emptyState) return;

    const title = emptyState.querySelector('.files-empty-title');
    const description = emptyState.querySelector('.files-empty-description');
    const { emptyUploadButton } = DOM;
    const hasSearch = Boolean(String(state.searchQuery || '').trim());

    if (title) {
    title.textContent = hasSearch
        ? filesT('files_empty_search_title', 'No matching files')
        : filesT('files_empty_title', 'No files yet');
    }
    if (description) {
    description.textContent = hasSearch
        ? filesT('files_empty_search_description', 'Try a different name or clear the search to see all files.')
        : filesT('files_empty_description', 'Upload files to use them in your conversations.');
    }
    if (emptyUploadButton) {
    emptyUploadButton.style.display = hasSearch ? 'none' : '';
    }

    emptyState.style.display = 'flex';
},

renderListStatus() {
    const { listStatus } = DOM;
    if (!listStatus) return;

    if (state.isLoadingMore) {
    listStatus.innerHTML = `
        <span class="files-list-status-spinner" aria-hidden="true"></span>
        <span>${Utils.escapeHtml(filesT('files_loading_more', 'Loading more files...'))}</span>
    `;
    listStatus.style.display = 'flex';
    return;
    }

    if (state.files.length > 0 && !state.hasMore) {
    listStatus.textContent = filesFormatT(
        state.total === 1 ? 'files_list_status_one' : 'files_list_status_other',
        state.total === 1 ? 'Showing {shown} of {total} file' : 'Showing {shown} of {total} files',
        { shown: state.files.length, total: state.total },
    );
    listStatus.style.display = 'flex';
    return;
    }

    listStatus.textContent = '';
    listStatus.style.display = 'none';
},

updateSortControls() {
    const headerColumns = DOM.fileSortHeaders;
    if (headerColumns && headerColumns.length) {
    headerColumns.forEach(column => {
        const field = column.dataset.sortField;
        const isActive = field === state.sortField;
        column.classList.toggle('sorted', isActive);
        if (isActive) {
        column.dataset.sortDirection = state.sortDirection;
        const directionLabel = state.sortDirection === 'asc'
            ? filesT('files_sort_ascending', 'ascending')
            : filesT('files_sort_descending', 'descending');
        column.setAttribute('aria-sort', state.sortDirection === 'asc' ? 'ascending' : 'descending');
        column.setAttribute(
            'aria-label',
            filesFormatT(
                'files_sort_sorted_aria',
                '{column} sorted {direction}',
                { column: column.textContent.trim(), direction: directionLabel },
            ),
        );
        } else {
        column.removeAttribute('data-sort-direction');
        column.setAttribute('aria-sort', 'none');
        column.setAttribute(
            'aria-label',
            filesFormatT(
                'files_sort_sortable_aria',
                '{column} sortable column',
                { column: column.textContent.trim() },
            ),
        );
        }
    });
    }
},

updateUploadAvailability() {
    const allowUploads = FilesManager.isFileUploadAllowed();
    const { uploadButton, emptyUploadButton, fileInput } = DOM;

    const disable = !allowUploads;
    [uploadButton, emptyUploadButton].forEach((button) => {
    if (!button) return;
    button.disabled = disable;
    button.setAttribute('aria-disabled', disable ? 'true' : 'false');
    button.classList.toggle('is-disabled', disable);
    button.setAttribute(
        'title',
        disable
        ? filesT('files_upload_disabled', 'File uploads are disabled for your account')
        : filesT('files_upload_title', 'Upload files'),
    );
    });

    if (uploadButton) {
    if (disable) {
        uploadButton.style.display = 'none';
    } else if (typeof isFilesViewVisible === 'function' ? isFilesViewVisible() : true) {
        uploadButton.style.display = 'flex';
    }
    }

    if (fileInput) {
    fileInput.disabled = disable;
    fileInput.setAttribute('aria-disabled', disable ? 'true' : 'false');
    fileInput.classList.toggle('is-disabled', disable);
    }
},

toggleFilesActionsDropdown(forceClose = false) {
    const { filesActionsDropdown, filesActionsTrigger } = DOM;
    if (!filesActionsDropdown || !filesActionsTrigger) return;

    const isOpen = filesActionsDropdown.classList.contains('open');
    const shouldOpen = forceClose ? false : !isOpen;

    filesActionsDropdown.classList.toggle('open', shouldOpen);
    filesActionsTrigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
},

showDeleteWebsearchOverlay() {
    const { filesDeleteWebsearchOverlay } = DOM;
    if (!filesDeleteWebsearchOverlay) return;
    filesDeleteWebsearchOverlay.removeAttribute('hidden');
    filesDeleteWebsearchOverlay.setAttribute('aria-hidden', 'false');
},

hideDeleteWebsearchOverlay() {
    const { filesDeleteWebsearchOverlay } = DOM;
    if (!filesDeleteWebsearchOverlay) return;
    filesDeleteWebsearchOverlay.setAttribute('hidden', '');
    filesDeleteWebsearchOverlay.setAttribute('aria-hidden', 'true');
},

createFileItem(file) {
    const originalFilename = file?.meta?.original_filename || file?.file_name || file?.name || filesT('files_untitled_file', 'Untitled file');
    const extension = Utils.getFileExtension(originalFilename) || 'FILE';
    const icon = Utils.getFileIcon(file.file_type);
    const escapedFilename = Utils.escapeHtml(originalFilename);
    const fileId = file?.file_id ?? file?.id ?? '';
    const fileSize = Utils.formatFileSize(file.file_size);
    const downloadLabel = filesT('files_preview_download', 'Download');
    const downloadAria = filesT('files_preview_download_aria', 'Download file');
    const moveLabel = filesT('files_move', 'Move');
    const moveAria = filesT('files_move_to_folder', 'Move to folder');
    const editLabel = filesT('files_edit_title', 'Edit file');
    const deleteLabel = filesT('files_delete_action', 'Delete');
    const deleteAria = filesT('files_delete_aria', 'Delete file');
    const openPreviewAria = filesFormatT('files_preview_open_file_aria', 'Open file preview: {filename}', { filename: originalFilename });

    return `
    <div class="file-item" data-file-id="${fileId}">
        <button type="button" class="file-item-main" data-file-id="${fileId}" aria-label="${Utils.escapeHtml(openPreviewAria)}">
        <span class="file-item-icon">
            <img src="/assets/file_svgs/${icon}" alt="${extension}" />
        </span>
        <span class="file-item-info">
            <span class="file-item-name" title="${escapedFilename}">${escapedFilename}</span>
            <span class="file-item-meta">
                <span class="file-item-type">${extension}</span>
                <span class="file-item-meta-divider"></span>
                <span>${fileSize}</span>
            </span>
        </span>
        </button>
        <div class="file-item-actions">
            <button type="button" class="file-action-btn download" data-file-action="download" data-file-id="${fileId}" title="${Utils.escapeHtml(downloadLabel)}" aria-label="${Utils.escapeHtml(downloadAria)}">
                ${Icons.download}
            </button>
            <button type="button" class="file-action-btn" data-file-action="move" data-file-id="${fileId}" title="${Utils.escapeHtml(moveLabel)}" aria-label="${Utils.escapeHtml(moveAria)}" aria-haspopup="menu" aria-expanded="false">
                ${Icons.folder}
            </button>
            <button type="button" class="file-action-btn edit" data-file-action="edit" data-file-id="${fileId}" title="${Utils.escapeHtml(editLabel)}" aria-label="${Utils.escapeHtml(editLabel)}">
                ${Icons.edit}
            </button>
            <button type="button" class="file-action-btn delete" data-file-action="delete" data-file-id="${fileId}" title="${Utils.escapeHtml(deleteLabel)}" aria-label="${Utils.escapeHtml(deleteAria)}">
                ${Icons.trash}
            </button>
        </div>
    </div>
    `;
},

createFileRow(file, animationClass) {
    return this.createFileItem(file);
},

createActionButton(action, fileId, color) {
    const actionLabels = {
        delete: { key: 'files_delete_action', fallback: 'Delete', ariaKey: 'files_delete_aria', ariaFallback: 'Delete file' },
        download: { key: 'files_preview_download', fallback: 'Download', ariaKey: 'files_preview_download_aria', ariaFallback: 'Download file' },
        edit: { key: 'files_edit_title', fallback: 'Edit file', ariaKey: 'files_edit_title', ariaFallback: 'Edit file' },
    };
    const labelConfig = actionLabels[action] || null;
    const actionName = labelConfig
        ? filesT(labelConfig.key, labelConfig.fallback)
        : action.charAt(0).toUpperCase() + action.slice(1);
    const actionAria = labelConfig
        ? filesT(labelConfig.ariaKey, labelConfig.ariaFallback)
        : filesFormatT('files_action_file_aria', '{action} file', { action: actionName });
    const iconKey = action === 'delete' ? 'trash' : action;
    const iconMarkup = Icons?.[iconKey] || '';
    return `
    <button 
        class="action-button ${action}" 
        onclick="${action}File('${fileId}')" 
        title="${Utils.escapeHtml(actionName)}"
        style="color: ${color};"
        aria-label="${Utils.escapeHtml(actionAria)}"
    >
        ${iconMarkup || Utils.escapeHtml(actionName)}
    </button>
    `;
},
};

// ============================================================================
// Event Handlers
// ============================================================================

const EventHandlers = {
setupListeners() {
    if (state.initialized) {
    return;
    }

    const {
        uploadButton,
        emptyUploadButton,
        fileInput,
        filesActionsTrigger,
        filesActionsDropdown,
        filesStorageUsageButton,
        filesStorageUsageOverlay,
        filesStorageUsageClose,
        filesDeleteWebsearchButton,
        filesDeleteWebsearchCancel,
        filesDeleteWebsearchConfirm,
        filesDeleteOverlay,
        filesDeleteCancel,
        filesDeleteConfirm,
        editFileCancelBtn,
        saveFileChangesBtn,
        fileEditNameInput,
        fileEditModalOverlay,
        fileEditModalClose,
        searchInput,
        searchClear,
        filesContentScroller,
    } = DOM;

    if (uploadButton) {
    uploadButton.addEventListener('click', () => {
        if (!FilesManager.isFileUploadAllowed()) {
        notifyError?.(filesT('files_upload_disabled', 'File uploads are disabled for your account.'));
        return;
        }
        if (!state.isUploading && fileInput) {
        fileInput.click();
        }
    });
    }

    if (filesStorageUsageButton && window.Icons?.info) {
    // Keep the icon as a direct child of the button so the button has no
    // presentational wrapper in its final DOM structure.
    filesStorageUsageButton.innerHTML = window.Icons.info;
    }

    if (filesStorageUsageButton) {
    filesStorageUsageButton.addEventListener('click', (event) => {
        event.preventDefault();
        ViewManager.openStorageUsageModal(filesStorageUsageButton);
    });
    }

    if (filesStorageUsageClose) {
    filesStorageUsageClose.addEventListener('click', (event) => {
        event.preventDefault();
        ViewManager.closeStorageUsageModal();
    });
    }

    if (filesStorageUsageOverlay) {
    filesStorageUsageOverlay.addEventListener('click', (event) => {
        if (event.target === filesStorageUsageOverlay) {
        ViewManager.closeStorageUsageModal();
        }
    });
    }

    if (fileInput) {
    fileInput.addEventListener('change', async (event) => {
        if (!FilesManager.isFileUploadAllowed()) {
        notifyError?.(filesT('files_upload_disabled', 'File uploads are disabled for your account.'));
        event.target.value = '';
        return;
        }
        await FileOperations.uploadFiles(event.target.files);
        event.target.value = '';
    });
    }

    if (emptyUploadButton) {
    emptyUploadButton.addEventListener('click', () => {
        if (!FilesManager.isFileUploadAllowed()) {
        notifyError?.(filesT('files_upload_disabled', 'File uploads are disabled for your account.'));
        return;
        }
        if (!state.isUploading && fileInput) {
        fileInput.click();
        }
    });
    }

    this.initSortableHeaders();

    if (filesActionsTrigger && filesActionsDropdown) {
    this.initActionsDropdown(filesActionsTrigger, filesActionsDropdown);
    }

    this.initSearch(searchInput, searchClear);
    this.initInfiniteScroll(filesContentScroller);
    this.initFileActionButtons(DOM.filesList);

    if (filesDeleteWebsearchButton) {
        filesDeleteWebsearchButton.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            UI.toggleFilesActionsDropdown(true);
            UI.showDeleteWebsearchOverlay();
        });

        filesDeleteWebsearchCancel?.addEventListener('click', () => {
            UI.hideDeleteWebsearchOverlay();
        });

        filesDeleteWebsearchConfirm?.addEventListener('click', async () => {
            UI.hideDeleteWebsearchOverlay();
            await FilesManager.deleteAllWebsearchFiles();
        });
    }

    if (filesDeleteCancel) {
        filesDeleteCancel.addEventListener('click', (event) => {
            event.preventDefault();
            ViewManager.closeFileDeleteModal();
        });
    }

    if (filesDeleteConfirm) {
        filesDeleteConfirm.addEventListener('click', async (event) => {
            event.preventDefault();
            const fileId = fileDeleteModalState.fileId;
            if (!fileId || fileDeleteModalState.isProcessing) {
                return;
            }

            ViewManager.setFileDeleteProcessing(true);
            const deleted = await FileOperations.performDeleteFile(fileId);
            if (deleted) {
                ViewManager.closeFileDeleteModal({ restoreFocus: false, force: true });
                return;
            }
            ViewManager.setFileDeleteProcessing(false);
        });
    }

    if (filesDeleteOverlay) {
        filesDeleteOverlay.addEventListener('click', (event) => {
            if (event.target === filesDeleteOverlay) {
                ViewManager.closeFileDeleteModal();
            }
        });
    }

    if (editFileCancelBtn) {
        editFileCancelBtn.addEventListener('click', (event) => {
            event.preventDefault();
            ViewManager.closeFileEditModal();
        });
    }

    if (fileEditModalClose) {
        fileEditModalClose.addEventListener('click', () => ViewManager.closeFileEditModal());
    }

    if (fileEditModalOverlay) {
        fileEditModalOverlay.addEventListener('click', (event) => {
            if (event.target === fileEditModalOverlay) {
                ViewManager.closeFileEditModal();
            }
        });
    }

    if (typeof window !== 'undefined' && typeof window.registerEscapeHandler === 'function') {
        window.registerEscapeHandler({
            id: 'workspace-files-edit-modal',
            priority: 80,
            isActive: () => Boolean(DOM.fileEditModalOverlay && !DOM.fileEditModalOverlay.hasAttribute('hidden')),
            close: () => ViewManager.closeFileEditModal(),
        });
        window.registerEscapeHandler({
            id: 'workspace-files-storage-usage-modal',
            priority: 80,
            isActive: () => ViewManager.isStorageUsageModalOpen(),
            close: () => ViewManager.closeStorageUsageModal(),
        });
    }

    if (saveFileChangesBtn) {
        const submitEdit = async () => {
            const fileId = fileEditNameInput?.dataset?.fileId;
            const newName = fileEditNameInput?.value || '';
            if (!fileId) {
                notifyError?.(filesT('files_error_no_file_selected', 'No file selected'));
                return;
            }
            if (!newName.trim()) {
                window.FormValidation?.showInputError(
                    fileEditNameInput,
                    DOM.fileEditNameError,
                    filesT('files_name_empty_error', 'File name cannot be empty'),
                );
                return;
            }
            window.FormValidation?.clearInputError(fileEditNameInput, DOM.fileEditNameError);
            await FileOperations.editFile(fileId, newName);
        };

        saveFileChangesBtn.addEventListener('click', (event) => {
            event.preventDefault();
            submitEdit();
        });

        fileEditNameInput?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                submitEdit();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                ViewManager.closeFileEditModal();
            }
        });
        fileEditNameInput?.addEventListener('input', () => {
            window.FormValidation?.clearInputError(fileEditNameInput, DOM.fileEditNameError);
        });
    }

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') {
            return;
        }

        if (ViewManager.isFileDeleteModalOpen()) {
            event.preventDefault();
            ViewManager.closeFileDeleteModal();
            return;
        }

        if (ViewManager.isStorageUsageModalOpen()) {
            event.preventDefault();
            ViewManager.closeStorageUsageModal();
        }
    });

    // Mobile sidebar toggle
    this.initMobileSidebar();
    WorkspaceDropUpload.bindGlobalListeners();

    state.setInitialized(true);
},

initSearch(input, clearButton) {
    if (!input) {
    return;
    }

    let debounceTimer = null;

    input.addEventListener('input', (event) => {
    const nextQuery = event.target.value || '';
    UI.updateSearchControls(false);

    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        FilesManager.setSearchQuery(nextQuery);
    }, FILES_SEARCH_DEBOUNCE_MS);
    });

    clearButton?.addEventListener('click', () => {
    clearTimeout(debounceTimer);
    input.value = '';
    FilesManager.setSearchQuery('');
    input.focus();
    });

    UI.updateSearchControls(true);
},

initInfiniteScroll(scroller) {
    if (!scroller || scroller.dataset.filesInfiniteScrollInitialized === 'true') {
    return;
    }

    scroller.dataset.filesInfiniteScrollInitialized = 'true';
    scroller.addEventListener('scroll', () => {
    FilesManager.maybeLoadMore();
    });
},

initFileActionButtons(filesList) {
    if (!filesList || filesList.dataset.filesActionsInitialized === 'true') {
    return;
    }

    filesList.dataset.filesActionsInitialized = 'true';
    filesList.addEventListener('click', (event) => {
    const actionButton = event.target.closest('[data-file-action][data-file-id]');
    if (!actionButton || !filesList.contains(actionButton)) {
        return;
    }

    event.preventDefault();
    event.stopPropagation();

    const fileId = actionButton.dataset.fileId;
    const action = actionButton.dataset.fileAction;
    const file = typeof state.getFileById === 'function' ? state.getFileById(fileId) : null;

    if (action === 'download') {
        FileOperations.downloadFile(fileId);
        return;
    }

    if (action === 'delete') {
        FileOperations.requestDeleteFile(fileId, {
        skipConfirm: Boolean(event.shiftKey),
        triggerElement: actionButton,
        });
        return;
    }

    if (action === 'move') {
        if (typeof window.showMoveToFolderMenu === 'function') {
        window.showMoveToFolderMenu(fileId, actionButton);
        }
        return;
    }

    if (!file) {
        notifyError?.(filesT('files_error_not_found', 'File not found.'));
        return;
    }

    if (action === 'edit') {
        ViewManager.openFileEditModal(file);
        return;
    }

    });
},

initSortableHeaders() {
    const headers = DOM.fileSortHeaders;
    if (!headers || headers.length === 0) {
    return;
    }

    headers.forEach((header) => {
        const sortField = header.dataset.sortField;
        if (!sortField) {
        return;
        }

        header.addEventListener('click', () => {
        FilesManager.handleHeaderSort(sortField);
        });

        header.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            FilesManager.handleHeaderSort(sortField);
        }
        });
    });
},

initActionsDropdown(trigger, dropdown) {
    const toggleDropdown = (forceOpen) => {
        const shouldOpen = typeof forceOpen === 'boolean' ? forceOpen : !dropdown.classList.contains('open');
        dropdown.classList.toggle('open', shouldOpen);
        trigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    };

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggleDropdown();
    });

    dropdown.addEventListener('click', (event) => {
        event.stopPropagation();
    });

    document.addEventListener('click', (event) => {
        if (dropdown.contains(event.target) || trigger.contains(event.target)) {
            return;
        }
        toggleDropdown(false);
    });
},

initMobileSidebar() {
    const { filesSidebar, filesSidebarBackdrop, filesSidebarToggle } = DOM;

    if (!filesSidebar || !filesSidebarToggle) {
        return;
    }

    let bodyOverflowBeforeOpen = '';

    /**
     * Update every visual and accessible part of the mobile drawer together.
     * Keeping this state in one function prevents the sidebar and backdrop from
     * becoming desynchronized when it closes through a folder click or Escape.
     */
    const setSidebarOpen = (open, { restoreFocus = false } = {}) => {
        const shouldOpen = Boolean(open);
        const wasOpen = filesSidebar.classList.contains('mobile-open');

        filesSidebar.classList.toggle('mobile-open', shouldOpen);
        filesSidebarBackdrop?.classList.toggle('active', shouldOpen);
        filesSidebarBackdrop?.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
        filesSidebarToggle.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');

        if (shouldOpen && !wasOpen) {
            bodyOverflowBeforeOpen = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        } else if (!shouldOpen && wasOpen) {
            document.body.style.overflow = bodyOverflowBeforeOpen;
            if (restoreFocus) filesSidebarToggle.focus();
        }
    };

    const closeSidebar = (options) => setSidebarOpen(false, options);

    /**
     * The Markdown split preview intentionally makes the Files workspace use
     * its narrow drawer layout even on a desktop-sized browser.
     */
    const isCompactLayout = () => (
        window.innerWidth <= 768
        || document.body.classList.contains('canvas-markdown-compact-main-layout')
    );

    filesSidebarToggle.addEventListener('click', () => {
        setSidebarOpen(!filesSidebar.classList.contains('mobile-open'));
    });

    filesSidebarBackdrop?.addEventListener('click', () => closeSidebar());

    // Close sidebar when selecting a folder on mobile
    filesSidebar.addEventListener('click', (event) => {
        const folderItem = event.target.closest('.files-sidebar-item');
        if (folderItem && isCompactLayout()) {
            closeSidebar();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && filesSidebar.classList.contains('mobile-open')) {
            event.preventDefault();
            closeSidebar({ restoreFocus: true });
        }
    });

    window.addEventListener('resize', () => {
        if (!isCompactLayout() && filesSidebar.classList.contains('mobile-open')) {
            closeSidebar();
        }
    });

    // A fixed drawer can lock body scrolling. Always close it at split-layout
    // boundaries so returning to desktop cannot retain hidden mobile state.
    document.addEventListener('canvasMarkdownCompactLayoutChange', () => {
        if (filesSidebar.classList.contains('mobile-open')) {
            closeSidebar();
        }
    });
},
};

// ============================================================================
// Drag and Drop for Files to Folders
// ============================================================================

const FileDragDrop = {
    draggedFileId: null,
    dragGhost: null,

    init() {
        this.setupFolderDropZones();
        document.addEventListener('dragend', () => this.cleanup());
    },

    setupFileItemDrag(fileItem) {
        if (!fileItem || fileItem.dataset.dragInitialized) return;
        
        fileItem.setAttribute('draggable', 'true');
        fileItem.dataset.dragInitialized = 'true';

        fileItem.addEventListener('dragstart', (e) => this.handleDragStart(e, fileItem));
        fileItem.addEventListener('dragend', (e) => this.handleDragEnd(e, fileItem));
    },

    handleDragStart(e, fileItem) {
        const fileId = fileItem.dataset.fileId;
        if (!fileId) {
            e.preventDefault();
            return;
        }

        this.draggedFileId = fileId;
        fileItem.classList.add('dragging');

        // Set drag data
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', fileId);
        e.dataTransfer.setData('application/x-file-id', fileId);

        // Create custom drag ghost
        this.createDragGhost(fileItem, e);

        // Highlight valid drop targets
        this.highlightDropZones(true);
    },

    handleDragEnd(e, fileItem) {
        fileItem.classList.remove('dragging');
        this.cleanup();
    },

    createDragGhost(fileItem, e) {
        const fileName = fileItem.querySelector('.file-item-name')?.textContent || 'File';
        const iconImg = fileItem.querySelector('.file-item-icon img');
        
        this.dragGhost = document.createElement('div');
        this.dragGhost.className = 'file-drag-ghost';
        
        if (iconImg) {
            const img = document.createElement('img');
            img.src = iconImg.src;
            this.dragGhost.appendChild(img);
        }
        
        const text = document.createElement('span');
        text.textContent = fileName;
        this.dragGhost.appendChild(text);
        
        document.body.appendChild(this.dragGhost);
        
        // Position off-screen initially; browser will use this as drag image
        this.dragGhost.style.left = '-9999px';
        this.dragGhost.style.top = '-9999px';
        
        // Use as drag image
        try {
            e.dataTransfer.setDragImage(this.dragGhost, 20, 20);
        } catch (_) {
            // Fallback for browsers that don't support setDragImage
        }
    },

    setupFolderDropZones() {
        // Setup for sidebar folder items - use event delegation
        const sidebar = document.getElementById('filesFolderSidebar');
        if (sidebar && !sidebar.dataset.dropZoneInitialized) {
            sidebar.dataset.dropZoneInitialized = 'true';
            
            sidebar.addEventListener('dragover', (e) => {
                const folderItem = e.target.closest('.files-sidebar-item');
                if (folderItem && this.draggedFileId) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    
                    // Remove drop-target from other items
                    sidebar.querySelectorAll('.files-sidebar-item.drop-target').forEach(item => {
                        if (item !== folderItem) item.classList.remove('drop-target');
                    });
                    
                    folderItem.classList.add('drop-target');
                }
            });

            sidebar.addEventListener('dragleave', (e) => {
                const folderItem = e.target.closest('.files-sidebar-item');
                if (folderItem) {
                    // Only remove if we're actually leaving (not entering a child)
                    const relatedTarget = e.relatedTarget;
                    if (!folderItem.contains(relatedTarget)) {
                        folderItem.classList.remove('drop-target');
                    }
                }
            });

            sidebar.addEventListener('drop', async (e) => {
                const folderItem = e.target.closest('.files-sidebar-item');
                if (folderItem && this.draggedFileId) {
                    e.preventDefault();
                    e.stopPropagation();
                    
                    folderItem.classList.remove('drop-target');
                    
                    const folderId = folderItem.dataset.folderId;
                    const fileId = this.draggedFileId;
                    
                    await this.moveFileToFolder(fileId, folderId);
                }
                this.cleanup();
            });
        }
    },

    async moveFileToFolder(fileId, folderId) {
        if (!fileId) return;

        // Handle special folder IDs
        let targetFolderId = folderId;
        if (folderId === 'all' || folderId === 'uncategorized') {
            targetFolderId = null; // Remove from folder
        }

        try {
            // Use FolderAPI if available, otherwise call directly
            if (typeof FolderAPI !== 'undefined' && typeof FolderAPI.moveFile === 'function') {
                await FolderAPI.moveFile(fileId, targetFolderId);
            } else {
                const response = await window.authedFetch('/api/v1/file-folders/move-file', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_id: fileId, folder_id: targetFolderId }),
                });
                if (!response.ok) throw new Error(filesT('files_move_error', 'Failed to move file'));
            }

            // Show success notification
            const file = state.getFileById(fileId);
            const fileName = file?.meta?.original_filename || filesT('files_file', 'File');
            const folderName = this.getFolderName(folderId);
            
            if (typeof showNotification === 'function') {
                showNotification(filesFormatT('files_move_success', 'Moved "{filename}" to {folder}', { filename: fileName, folder: folderName }), 'success');
            }

            // Refresh files list and folder counts
            if (typeof FilesManager !== 'undefined') {
                await FilesManager.refresh();
            }
            if (typeof FileFoldersManager !== 'undefined') {
                FileFoldersManager.refreshCounts?.();
                FolderRenderer?.updateCounts?.();
            }

        } catch (error) {
            console.error('Failed to move file:', error);
            if (typeof notifyError === 'function') {
                notifyError(filesT('files_move_error', 'Failed to move file'));
            } else if (typeof showNotification === 'function') {
                showNotification(filesT('files_move_error', 'Failed to move file'), 'error');
            }
        }
    },

    getFolderName(folderId) {
        if (folderId === 'all') return filesT('files_folder_all', 'All Files');
        if (folderId === 'uncategorized' || !folderId) return filesT('files_folder_uncategorized', 'Uncategorized');
        
        if (typeof FileFoldersState !== 'undefined') {
            const folder = FileFoldersState.folders?.find(f => f.id === folderId);
            if (folder) {
                return folder.name;
            }
        }
        return filesT('files_folder_generic', 'folder');
    },

    highlightDropZones(highlight) {
        const sidebar = document.getElementById('filesFolderSidebar');
        if (sidebar) {
            sidebar.classList.toggle('has-dragging-file', highlight);
        }
    },

    cleanup() {
        this.draggedFileId = null;
        
        // Remove drag ghost
        if (this.dragGhost && this.dragGhost.parentNode) {
            this.dragGhost.parentNode.removeChild(this.dragGhost);
        }
        this.dragGhost = null;

        // Remove all drop-target classes
        document.querySelectorAll('.files-sidebar-item.drop-target').forEach(item => {
            item.classList.remove('drop-target');
        });

        // Remove highlight from sidebar
        const sidebar = document.getElementById('filesFolderSidebar');
        if (sidebar) {
            sidebar.classList.remove('has-dragging-file');
        }
    },

    // Called after file list is rendered to setup drag on new items
    setupAllFileItems() {
        const filesList = document.getElementById('filesList');
        if (filesList) {
            filesList.querySelectorAll('.file-item').forEach(item => {
                this.setupFileItemDrag(item);
            });
        }
    },
};

// ============================================================================
// API Service
// ============================================================================

const API = {
async fetchFiles(options = {}) {
    const params = new URLSearchParams();
    const {
    limit = FILES_PAGE_SIZE,
    offset = 0,
    } = options;

    params.set('limit', String(limit));
    params.set('offset', String(offset));

    const response = await window.authedFetch(`${API_ENDPOINTS.LIST}?${params.toString()}`, {
    method: 'GET',
    });

    if (!response.ok) {
    notifyError(filesFormatT('files_error_fetch_status', 'Failed to fetch files: {status}', { status: response.status }));
    }

    return response.json();
},

async fetchWorkspaceFiles(options = {}) {
    const params = new URLSearchParams();
    const {
    search = '',
    folderId = 'all',
    sortField = SORT_CONFIG.DEFAULT_FIELD,
    sortDirection = SORT_CONFIG.DEFAULT_DIRECTION,
    limit = FILES_PAGE_SIZE,
    offset = 0,
    } = options;

    const trimmedSearch = String(search || '').trim();
    if (trimmedSearch) {
    params.set('search', trimmedSearch);
    }

    const normalizedFolderId = String(folderId || 'all').trim();
    if (normalizedFolderId && normalizedFolderId !== 'all') {
    params.set('folder_id', normalizedFolderId);
    }

    params.set('sort_field', SORT_CONFIG.FIELDS.includes(sortField) ? sortField : SORT_CONFIG.DEFAULT_FIELD);
    params.set('sort_direction', SORT_CONFIG.DIRECTIONS.includes(sortDirection) ? sortDirection : SORT_CONFIG.DEFAULT_DIRECTION);
    params.set('limit', String(limit));
    params.set('offset', String(offset));

    const response = await window.authedFetch(`${API_ENDPOINTS.WORKSPACE}?${params.toString()}`, {
    method: 'GET',
    });

    if (!response.ok) {
    notifyError(filesFormatT('files_error_fetch_status', 'Failed to fetch files: {status}', { status: response.status }));
    }

    return response.json();
},

async fetchStorageUsage() {
    const response = await window.authedFetch(API_ENDPOINTS.STORAGE_USAGE, {
    method: 'GET',
    headers: {
        'accept': 'application/json',
    },
    });

    if (!response.ok) {
    throw new Error(`Storage usage request failed with status ${response.status}`);
    }

    return response.json();
},

async uploadFile(file, onProgress, options = {}) {
    const formData = new FormData();
    formData.append('file', file);
    if (options.folder_id) formData.append('folder_id', options.folder_id);

    const response = await window.authedFetch(API_ENDPOINTS.UPLOAD, {
    method: 'POST',
    headers: { 'Content-Type': null },
    body: formData,
    adapter: (input, init) => xhrFetchWithUploadProgress(input, init, onProgress),
    });

    const parsedResponse = await response.json().catch(() => null);
    if (response.ok) {
    const isSuccess = parsedResponse?.status === 'success' || response.status === 204;
    return { success: isSuccess, data: parsedResponse };
    }

    const errorDetail = String(parsedResponse?.detail || parsedResponse?.message || '').trim();
    const knownUploadErrors = {
        spreadsheet_archive_too_complex: filesT(
            'spreadsheet_archive_too_complex',
            'This workbook is too large or complex to edit safely in the browser.',
        ),
    };
    const errorMessage = knownUploadErrors[errorDetail]
        || errorDetail
        || response.statusText
        || filesT('files_upload_failed', 'Upload failed');
    return { success: false, message: errorMessage, status: response.status };
},

async downloadFile(fileId) {
    const response = await window.authedFetch(`${API_ENDPOINTS.DOWNLOAD}?file_id=${fileId}`, {
    method: 'GET',
    headers: {
        'accept': 'application/json',
    },
    });

    if (!response.ok) {
    notifyError(filesFormatT('files_download_failed_status', 'Download failed: {status}', { status: response.status }));
    }

    return {
    blob: await response.blob(),
    filename: Utils.extractFilenameFromHeader(
        response.headers.get('content-disposition')
    ),
    };
},

async deleteFile(fileId) {
    const response = await window.authedFetch(`${API_ENDPOINTS.DELETE}?file_id=${fileId}`, {
    method: 'DELETE',
    headers: {
        'accept': 'application/json',
    },
    });

    if (!response.ok) {
    notifyError(filesFormatT('files_delete_failed_status', 'Delete failed: {status}', { status: response.status }));
    }

    const result = await response.json();
    return result.status === 'success';
},

async editFile(fileId, originalFilename) {
    const response = await window.authedFetch(API_ENDPOINTS.EDIT, {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'accept': 'application/json',
    },
    body: JSON.stringify({
        file_id: fileId,
        original_filename: originalFilename,
    }),
    });

    if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    notifyError(errorData?.detail || filesFormatT('files_edit_failed_status', 'Edit failed: {status}', { status: response.status }));
    }

    return response.json();
},
};

// ============================================================================
// File Operations
// ============================================================================

const FileOperations = {
buildWorkspaceQueryOptions({ offset = 0 } = {}) {
    const activeFolderId = typeof FileFoldersManager !== 'undefined'
    && typeof FileFoldersManager.getActiveFolderId === 'function'
    ? FileFoldersManager.getActiveFolderId()
    : 'all';

    return {
    search: state.searchQuery,
    folderId: activeFolderId || 'all',
    sortField: state.sortField,
    sortDirection: state.sortDirection,
    limit: state.pageSize,
    offset,
    };
},

applyWorkspaceResult(result, { append = false, animate = false } = {}) {
    state.setAnimationState(animate);
    state.setWorkspaceResult(result, { append });
    UI.renderFiles();
    if (typeof FileFoldersManager !== 'undefined' && typeof FileFoldersManager.updateAfterFilesLoaded === 'function') {
    FileFoldersManager.updateAfterFilesLoaded();
    }
    requestAnimationFrame(() => {
    FilesManager.maybeLoadMore();
    });
},

async loadFiles(animate = false) {
    const requestId = state.beginRequest();

    try {
    state.setLoadingState(true, { isLoadingMore: false });
    state.resetWorkspaceList();
    const scroller = DOM.filesContentScroller;
    if (scroller) {
    scroller.scrollTop = 0;
    }
    UI.renderFiles();

    const result = await API.fetchWorkspaceFiles(this.buildWorkspaceQueryOptions({ offset: 0 }));
    if (!state.isLatestRequest(requestId)) {
        return;
    }

    this.applyWorkspaceResult(result, { append: false, animate });
    } catch (error) {
    if (!state.isLatestRequest(requestId)) {
        return;
    }
    console.error('Error loading files:', error);
	    notifyError(filesT('files_error_load', 'Failed to load files'));
    state.resetWorkspaceList();
    UI.renderFiles();
    } finally {
    if (state.isLatestRequest(requestId)) {
        state.setLoadingState(false, { isLoadingMore: false });
        UI.renderFiles();
    }
    }
},

async loadMoreFiles() {
    if (state.isLoading || state.isLoadingMore || !state.hasMore) {
    return;
    }

    const requestId = state.beginRequest();

    try {
    state.setLoadingState(true, { isLoadingMore: true });
    UI.renderFiles();

    const result = await API.fetchWorkspaceFiles(this.buildWorkspaceQueryOptions({ offset: state.offset }));
    if (!state.isLatestRequest(requestId)) {
        return;
    }

    this.applyWorkspaceResult(result, { append: true, animate: false });
    } catch (error) {
    if (!state.isLatestRequest(requestId)) {
        return;
    }
    console.error('Error loading more files:', error);
	    notifyError(filesT('files_error_load_more', 'Failed to load more files'));
    } finally {
    if (state.isLatestRequest(requestId)) {
        state.setLoadingState(false, { isLoadingMore: false });
        UI.renderFiles();
    }
    }
},

getCachedFiles(options = {}) {
    const { maxAgeMs = 30000 } = options;
    if (FilesCache.isFresh(maxAgeMs)) {
    return FilesCache.get();
    }
    return [];
},

setCachedFiles(files) {
    FilesCache.set(files);
},

async uploadFiles(fileList) {
    if (!fileList?.length || state.isUploading) return;
    if (!FilesManager.isFileUploadAllowed()) {
	    notifyError?.(filesT('files_upload_disabled', 'File uploads are disabled for your account.'));
    return;
    }

    const files = Array.from(fileList);
    let refreshNeeded = false;

    state.setUploadingState(true);
    UI.updateUploadUI(true);

    try {
    for (const file of files) {
        const success = await this.uploadSingleFile(file);
        refreshNeeded = refreshNeeded || success;
    }
    } catch (error) {
    console.error('Upload error:', error);
	    notifyError(filesT('files_upload_unexpected_error', 'Unexpected error during upload'));
    } finally {
    state.setUploadingState(false);
    UI.updateUploadUI(false);
    }

    if (refreshNeeded) {
    await this.loadFiles(true);
    await ViewManager.refreshStorageUsage({ silent: true });
    }
},

async uploadSingleFile(file) {
    UI.showProgress(file.name);

    try {
    // Determine folder_id: use active folder from workspace if it's a real folder
    const uploadOptions = {};
    if (typeof FileFoldersManager !== 'undefined') {
        const activeFolderId = FileFoldersManager.getActiveFolderId();
        if (activeFolderId && activeFolderId !== 'all' && activeFolderId !== 'uncategorized') {
            uploadOptions.folder_id = activeFolderId;
        }
    }
    const result = await API.uploadFile(file, (percent) => {
        UI.updateProgress(percent);
    }, uploadOptions);

    const success = result?.success ?? false;
    const alreadyAdded = Boolean(result?.data?.already_uploaded);

    if (success) {
        UI.updateProgress(100);
        if (alreadyAdded && typeof notifySuccess === 'function') {
	        notifySuccess(filesT('files_upload_already_uploaded', 'File already uploaded, reusing it'));
        }
        await UI.hideProgress(PROGRESS_HIDE_DELAY);
    } else {
        const message = result?.message;
	        const displayMessage = message
            ? `${file.name}: ${message}`
            : filesFormatT('files_upload_failed_named', 'Failed to upload {filename}', { filename: file.name });
        if (typeof notifyError === 'function') {
        notifyError(displayMessage);
        }
        await UI.hideProgress();
    }

    return success;
    } catch (error) {
	    const fallbackMessage = error?.message
        ? `${file.name}: ${error.message}`
        : filesFormatT('files_upload_failed_named', 'Failed to upload {filename}', { filename: file.name });
    if (typeof notifyError === 'function') {
        notifyError(fallbackMessage);
    }
    await UI.hideProgress();
    return false;
    }
},

async downloadFile(fileId) {
    try {
    const fileRecord = typeof state.getFileById === 'function' ? state.getFileById(fileId) : null;
    const { blob, filename } = await API.downloadFile(fileId);

    const normalizedHeaderName = String(filename || '').trim();
    const isGenericDownloadName = /^download(\.[^/\\]+)?$/i.test(normalizedHeaderName);
    const headerName = normalizedHeaderName && !isGenericDownloadName ? normalizedHeaderName : '';
    const fallbackName = fileRecord?.meta?.original_filename || fileRecord?.file_name || fileRecord?.name || '';
    const resolvedFilename = headerName || fallbackName || 'download';

    // Trigger download
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = resolvedFilename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

	    notifySuccess(filesFormatT('files_download_started', 'Downloading {filename}', { filename: resolvedFilename }));
    } catch (error) {
    console.error('Download error:', error);
	    notifyError(filesT('files_download_failed', 'Failed to download file'));
    }
},

async performDeleteFile(fileId) {
    try {
    const success = await API.deleteFile(fileId);

    if (success) {
        if (typeof window.handleFilesDeletedForChat === 'function') {
        try {
            window.handleFilesDeletedForChat({ fileIds: [fileId] });
        } catch (error) {
            console.error('handleFilesDeletedForChat threw during single delete', error);
        }
        }
	        notifySuccess(filesT('files_delete_success', 'File deleted successfully'));
        await this.loadFiles(false);
        await ViewManager.refreshStorageUsage({ silent: true });
        return true;
    } else {
	        notifyError(filesT('files_delete_error', 'Failed to delete file'));
    }
    } catch (error) {
    console.error('Delete error:', error);
	    notifyError(filesT('files_delete_error', 'Failed to delete file'));
    }
    return false;
},

async requestDeleteFile(fileId, { skipConfirm = false, triggerElement = null } = {}) {
    if (!fileId) {
	    notifyError?.(filesT('files_error_no_file_selected', 'No file selected'));
    return false;
    }

    const file = typeof state.getFileById === 'function' ? state.getFileById(fileId) : null;
    if (skipConfirm) {
    return this.performDeleteFile(fileId);
    }

    ViewManager.openFileDeleteModal(file || { file_id: fileId }, triggerElement);
    return false;
},

async editFile(fileId, originalFilename) {
    if (!originalFilename || !originalFilename.trim()) {
	    notifyError(filesT('files_name_empty_error', 'File name cannot be empty'));
    return;
    }

    try {
    const result = await API.editFile(fileId, originalFilename.trim());
        if (result) {
	        notifySuccess?.(filesT('files_edit_success', 'File updated'));
        ViewManager.closeFileEditModal();
        await this.loadFiles(false);
    } else {
	        notifyError(filesT('files_edit_error', 'Failed to edit file'));
    }
    } catch (error) {
	    const message = error?.message || filesT('files_edit_error', 'Failed to edit file');
    notifyError(message);
    }
},
};

// ============================================================================
// File Row Menus
// ============================================================================

const FilesMenus = {
attach(files) {
    if (!Array.isArray(files) || !files.length) {
        return;
    }

    files.forEach((file) => {
        const fileId = file?.file_id ?? file?.id;
        if (!fileId) {
            return;
        }

        const previewButton = document.querySelector(`.file-item-main[data-file-id="${fileId}"]`);
        if (previewButton) {
            previewButton.addEventListener('click', () => {
                // Markdown, HTML, and PDF files use the richer Canvas surface. Match
                // Canvas result cards by making a second click on the active
                // workspace file close that preview instead of re-fetching it.
                if (FilesPreview.isCanvasPreviewFile(file) && FilesPreview.isCanvasPreviewOpenForFile(fileId)) {
                    FilesPreview.closeCanvasPreview();
                    return;
                }

                if (FilesPreview.isGeneratedSlidePresentation(file)) {
                    if (FilesPreview.isSlidePresentationPreviewOpenForFile(file)) {
                        FilesPreview.closeSlidePresentationPreview();
                        return;
                    }

                    FilesPreview.openSlidePresentationPreview(file)
                        .then((opened) => {
                            if (opened) {
                                return;
                            }

                            if (FilesPreview.isOpen && FilesPreview.activeFileId === fileId) {
                                FilesPreview.close();
                                return;
                            }

                            return FilesPreview.open(file);
                        })
                        .catch((error) => {
                            console.error('Failed to open PowerPoint preview', error);
                            notifyError?.(filesT('files_preview_powerpoint_error', 'Failed to open PowerPoint preview.'));
                        });
                    return;
                }

                if (FilesPreview.isOpen && FilesPreview.activeFileId === fileId) {
                    FilesPreview.close();
                } else {
                    FilesPreview.open(file).catch((error) => {
                        console.error('Failed to open file preview', error);
                        notifyError?.(filesT('files_preview_open_error', 'Failed to open file preview.'));
                    });
                }
            });
        }

        const editButton = document.querySelector(`.file-item .file-action-btn.edit[data-file-id="${fileId}"]`);
        if (editButton) {
            editButton.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                ViewManager.openFileEditModal(file);
            });
        }
    });
},
};

const FilesPreview = {
    // Supported file types
    supportedImageTypes: ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/bmp', 'image/svg+xml', 'image/webp'],
    supportedPdfTypes: ['application/pdf'],
    supportedLatexTypes: ['text/x-tex', 'text/x-latex', 'application/x-latex'],
    supportedLatexExtensions: ['tex', 'latex'],
    supportedSpreadsheetTypes: [
        'text/csv',
        'text/tab-separated-values',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ],
    supportedSpreadsheetExtensions: ['csv', 'tsv', 'xlsx', 'xls'],
    // Storage providers and browsers do not agree on one MIME label for every
    // HTML-family document. Keep the aliases explicit so uploaded, generated,
    // and restored files all reach the same editable Canvas preview.
    supportedHtmlTypes: [
        'text/html',
        'application/html',
        'application/xhtml+xml',
        'application/x-html',
        'text/xhtml',
    ],
    supportedHtmlExtensions: ['html', 'htm', 'xhtml', 'xht', 'xhtm', 'shtml', 'shtm'],
    supportedAudioTypes: [
        'audio/mpeg',
        'audio/mp3',
        'audio/wav',
        'audio/wave',
        'audio/x-wav',
        'audio/ogg',
        'audio/aac',
        'audio/mp4',
        'audio/m4a',
        'audio/x-m4a',
        'audio/flac',
        'audio/webm',
        'audio/x-aiff',
        'audio/aiff',
    ],
    supportedVideoTypes: [
        'video/mp4',
        'video/webm',
        'video/ogg',
    ],
    supportedTextTypes: [
        'text/plain',
        'text/html',
        'application/html',
        'application/xhtml+xml',
        'application/x-html',
        'text/xhtml',
        'text/css',
        'text/markdown',
        'text/x-markdown',
        'text/x-mermaid',
        'text/x-tex',
        'text/x-latex',
        'application/x-latex',
        'application/json',
        'text/json',
        'application/javascript',
        'text/javascript',
        'application/xml',
        'text/xml',
        'application/yaml',
        'application/x-yaml',
        'text/yaml',
        'text/csv',
    ],
    extensionMimeMap: {
        // SVG storage responses are often generic binary/XML. Filename
        // detection keeps both uploaded and assistant-generated vectors
        // previewable without embedding their markup into the application DOM.
        svg: 'image/svg+xml',
        txt: 'text/plain',
        log: 'text/plain',
        md: 'text/markdown',
        markdown: 'text/markdown',
        mmd: 'text/x-mermaid',
        mermaid: 'text/x-mermaid',
        json: 'application/json',
        yaml: 'application/yaml',
        yml: 'application/yaml',
        html: 'text/html',
        htm: 'text/html',
        shtml: 'text/html',
        shtm: 'text/html',
        xhtml: 'application/xhtml+xml',
        xht: 'application/xhtml+xml',
        xhtm: 'application/xhtml+xml',
        css: 'text/css',
        js: 'application/javascript',
        mjs: 'application/javascript',
        cjs: 'application/javascript',
        ts: 'text/plain',
        tsx: 'text/plain',
        jsx: 'text/plain',
        xml: 'application/xml',
        csv: 'text/csv',
        tsv: 'text/tab-separated-values',
        xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        xls: 'application/vnd.ms-excel',
        pdf: 'application/pdf',
        tex: 'text/x-tex',
        latex: 'text/x-latex',
        // Audio formats
        mp3: 'audio/mpeg',
        wav: 'audio/wav',
        ogg: 'audio/ogg',
        aac: 'audio/aac',
        m4a: 'audio/m4a',
        flac: 'audio/flac',
        weba: 'audio/webm',
        aiff: 'audio/aiff',
        aif: 'audio/aiff',
        // Video formats
        mp4: 'video/mp4',
        m4v: 'video/mp4',
        mov: 'video/quicktime',
        qt: 'video/quicktime',
        avi: 'video/x-msvideo',
        wmv: 'video/x-ms-wmv',
        flv: 'video/x-flv',
        // Prefer audio/webm here because this extension is commonly used for voice recordings
        // and resolveInitialMimeType falls back to the server-provided MIME type when available.
        webm: 'audio/webm',
        mkv: 'video/x-matroska',
        ogv: 'video/ogg',
        mpg: 'video/mpeg',
        mpeg: 'video/mpeg',
        m2v: 'video/mpeg',
        '3gp': 'video/3gpp',
        '3g2': 'video/3gpp2',
    },

    // State
    activeObjectUrl: null,
    activeFileId: null,
    activeFile: null,
    isOpen: false,
    eventsBound: false,
    activeLayoutMode: 'panel',
    textPreviewMaxBytes: 1024 * 1024,
    binaryPreviewMaxBytes: 25 * 1024 * 1024,
    
    // Desktop resize state
    isResizing: false,
    startX: 0,
    startWidth: 0,
    
    // Mobile touch state
    isDragging: false,
    dragStartY: 0,
    dragCurrentY: 0,
    dragThreshold: 100, // px to drag before closing
    
    // Breakpoint for mobile detection
    mobileBreakpoint: 768,

    // ========================================================================
    // Helpers
    // ========================================================================

    isMobile() {
        return window.innerWidth <= FilesPreview.mobileBreakpoint;
    },

    normalizeMimeType(type) {
        if (!type) return '';
        return String(type).split(';')[0].trim().toLowerCase();
    },

    isGenericBinaryMimeType(type) {
        const normalizedType = FilesPreview.normalizeMimeType(type);
        if (!normalizedType) return true;
        return normalizedType === 'application/octet-stream'
            || normalizedType === 'binary/octet-stream'
            || normalizedType === 'application/x-binary'
            || normalizedType === 'application/download'
            || normalizedType === 'application/x-download';
    },

    getFileExtension(file) {
        const name = String(file?.meta?.original_filename || file?.file_name || file?.name || '').toLowerCase();
        const lastDotIndex = name.lastIndexOf('.');
        if (lastDotIndex === -1 || lastDotIndex === name.length - 1) return '';
        return name.slice(lastDotIndex + 1);
    },

    resolveInitialMimeType(file) {
        const fileType = FilesPreview.normalizeMimeType(file?.file_type);
        if (fileType && !FilesPreview.isGenericBinaryMimeType(fileType)) {
            return fileType;
        }
        const extension = FilesPreview.getFileExtension(file);
        if (extension) {
            const mappedType = FilesPreview.extensionMimeMap[extension];
            if (mappedType) {
                return mappedType;
            }
        }
        return fileType || '';
    },

    isTextMimeType(type) {
        if (!type) return false;
        if (type.startsWith('text/')) return true;
        return FilesPreview.supportedTextTypes.includes(type);
    },

    getFileSize(file) {
        const candidates = [
            file?.file_size,
            file?.meta?.file_size,
            file?.size,
        ];
        for (const candidate of candidates) {
            const value = Number(candidate);
            if (Number.isFinite(value) && value > 0) {
                return value;
            }
        }
        return 0;
    },

    getPreviewLimitLabel(limitBytes) {
        return Utils.formatFileSize(limitBytes);
    },

    isOverPreviewLimit(file, limitBytes) {
        const fileSize = FilesPreview.getFileSize(file);
        return fileSize > 0 && fileSize > limitBytes;
    },

    getPreviewTooLargeMessage(limitBytes) {
        return filesFormatT(
            'files_preview_too_large_limit',
            'This file is too large to preview. Previewing is limited to {size}.',
            { size: FilesPreview.getPreviewLimitLabel(limitBytes) }
        );
    },

    cancelResponseBody(response) {
        try {
            if (response?.body && typeof response.body.cancel === 'function') {
                response.body.cancel();
            }
        } catch (_) {
            // Best effort only: the preview is already switching away from this response.
        }
    },

    getContentRangeTotal(response) {
        const contentRange = String(response?.headers?.get('Content-Range') || '');
        const match = contentRange.match(/\/(\d+)$/);
        if (!match) return 0;
        const total = Number(match[1]);
        return Number.isFinite(total) && total > 0 ? total : 0;
    },

    isTextResponseTruncated(response, maxBytes) {
        const total = FilesPreview.getContentRangeTotal(response);
        if (total > maxBytes) return true;
        if (total > 0) return false;
        const contentLength = Number(response?.headers?.get('Content-Length') || 0);
        return response?.status === 206 && contentLength >= maxBytes;
    },

    async readTextPreviewContent(response, maxBytes = FilesPreview.textPreviewMaxBytes) {
        let truncated = FilesPreview.isTextResponseTruncated(response, maxBytes);
        const decoder = new TextDecoder('utf-8', { fatal: false });

        if (!response.body || typeof response.body.getReader !== 'function') {
            const blob = await response.blob();
            truncated = truncated || blob.size > maxBytes;
            const slice = truncated ? blob.slice(0, maxBytes) : blob;
            return {
                text: await slice.text(),
                truncated,
            };
        }

        const reader = response.body.getReader();
        let bytesRead = 0;
        let text = '';

        try {
            while (bytesRead < maxBytes) {
                const { done, value } = await reader.read();
                if (done) {
                    text += decoder.decode();
                    return { text, truncated };
                }

                const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
                const remaining = maxBytes - bytesRead;
                if (chunk.byteLength > remaining) {
                    text += decoder.decode(chunk.slice(0, remaining), { stream: true });
                    truncated = true;
                    await reader.cancel();
                    text += decoder.decode();
                    return { text, truncated };
                }

                text += decoder.decode(chunk, { stream: true });
                bytesRead += chunk.byteLength;
            }

            truncated = true;
            await reader.cancel();
            text += decoder.decode();
            return { text, truncated };
        } finally {
            try {
                reader.releaseLock?.();
            } catch (_) {
                // Older browsers may not expose releaseLock consistently.
            }
        }
    },

    isImageMimeType(type) {
        const normalizedType = FilesPreview.normalizeMimeType(type);
        if (!normalizedType) return false;
        return FilesPreview.supportedImageTypes.includes(normalizedType);
    },

    getPreferredLayoutMode(file, type = '') {
        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        return FilesPreview.isImageMimeType(normalizedType) ? 'image' : 'panel';
    },

    applyLayoutMode(mode = 'panel', file = null) {
        const sidebar = DOM.filesPreviewSidebar;
        const backdrop = DOM.filesPreviewBackdrop;
        const previewName = file?.meta?.original_filename || file?.file_name || file?.name || filesT('files_preview_title', 'File preview');

        FilesPreview.activeLayoutMode = mode === 'image' ? 'image' : 'panel';

        if (sidebar) {
            const isImageLayout = FilesPreview.activeLayoutMode === 'image';
            sidebar.classList.toggle('files-preview-image-modal', isImageLayout);
            if (isImageLayout) {
                sidebar.removeAttribute('aria-labelledby');
                sidebar.setAttribute('aria-label', filesFormatT('files_preview_image_aria', 'Image preview: {filename}', { filename: previewName }));
            } else {
                sidebar.setAttribute('aria-labelledby', 'filesPreviewTitle');
                sidebar.removeAttribute('aria-label');
            }
        }

        if (backdrop) {
            backdrop.classList.toggle('files-preview-image-backdrop', FilesPreview.activeLayoutMode === 'image');
        }
    },

    shouldLockBodyScroll() {
        return FilesPreview.isMobile() || FilesPreview.activeLayoutMode === 'image';
    },

    syncPreviewIcons() {
        const closeButton = DOM.filesPreviewClose;
        if (closeButton && typeof Icons === 'object' && Icons?.close) {
            closeButton.innerHTML = Icons.close;
        }
    },

    isHtmlPreviewFile(file, type = '') {
        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        if (FilesPreview.supportedHtmlTypes.includes(normalizedType)) {
            return true;
        }
        const extension = FilesPreview.getFileExtension(file);
        return FilesPreview.supportedHtmlExtensions.includes(extension);
    },

    /**
     * Return whether a workspace file should use the shared Markdown canvas.
     * File extension detection is intentional: uploads from some storage
     * providers arrive as application/octet-stream even though their original
     * filename still reliably identifies the Markdown document.
     */
    isMarkdownPreviewFile(file, type = '') {
        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        if (normalizedType === 'text/markdown' || normalizedType === 'text/x-markdown') {
            return true;
        }
        const extension = FilesPreview.getFileExtension(file);
        return extension === 'md' || extension === 'markdown';
    },

    /** Return whether MIME metadata or the filename identifies a PDF. */
    isPdfPreviewFile(file, type = '') {
        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        if (FilesPreview.supportedPdfTypes.includes(normalizedType)) {
            return true;
        }
        return FilesPreview.getFileExtension(file) === 'pdf';
    },

    /** Return whether MIME metadata or filename identifies editable LaTeX source. */
    isLatexPreviewFile(file, type = '') {
        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        if (FilesPreview.supportedLatexTypes.includes(normalizedType)) {
            return true;
        }
        return FilesPreview.supportedLatexExtensions.includes(FilesPreview.getFileExtension(file));
    },

    /** Return whether a supported delimiter or Excel file uses the grid editor. */
    isSpreadsheetPreviewFile(file, type = '') {
        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        if (FilesPreview.supportedSpreadsheetTypes.includes(normalizedType)) return true;
        return FilesPreview.supportedSpreadsheetExtensions.includes(FilesPreview.getFileExtension(file));
    },

    /** Return whether this file belongs in the shared Canvas preview panel. */
    isCanvasPreviewFile(file, type = '') {
        return FilesPreview.isMarkdownPreviewFile(file, type)
            || FilesPreview.isHtmlPreviewFile(file, type)
            || FilesPreview.isPdfPreviewFile(file, type)
            || FilesPreview.isLatexPreviewFile(file, type)
            || FilesPreview.isSpreadsheetPreviewFile(file, type);
    },

    /** Query Canvas through its public API instead of duplicating its state. */
    isCanvasPreviewOpenForFile(fileId) {
        const widget = window.canvasMarkdownWidget;
        return Boolean(
            widget
            && typeof widget.isPreviewOpenForFile === 'function'
            && widget.isPreviewOpenForFile(String(fileId || ''))
        );
    },

    /** Close Canvas without reaching into its DOM or private state. */
    closeCanvasPreview() {
        const widget = window.canvasMarkdownWidget;
        if (widget && typeof widget.hidePreviewPanel === 'function') {
            widget.hidePreviewPanel();
        }
    },

    /**
     * Open a compatible file in the same Canvas surface used by canvas tool
     * results. Returns false when Canvas is unavailable so the
     * caller can retain the generic text-preview fallback.
     */
    async openCanvasPreview(file, fileId, fileName) {
        const widget = window.canvasMarkdownWidget;
        if (!widget || typeof widget.openPreviewForFile !== 'function') {
            return false;
        }

        const extension = FilesPreview.getFileExtension(file);
        const spreadsheetMime = FilesPreview.normalizeMimeType(FilesPreview.resolveInitialMimeType(file));
        const spreadsheetTypeByMime = {
            'text/csv': 'csv',
            'text/tab-separated-values': 'tsv',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
            'application/vnd.ms-excel': 'xls',
        };
        const contentType = FilesPreview.isSpreadsheetPreviewFile(file)
            ? (FilesPreview.supportedSpreadsheetExtensions.includes(extension)
                ? extension
                : (spreadsheetTypeByMime[spreadsheetMime] || 'xlsx'))
            : (FilesPreview.isMarkdownPreviewFile(file)
            ? 'markdown'
            : (FilesPreview.isPdfPreviewFile(file)
                ? 'pdf'
                : (FilesPreview.isLatexPreviewFile(file) ? 'latex' : 'html')));
        if (FilesPreview.isOpen) {
            FilesPreview.close();
        }
        await widget.openPreviewForFile(fileId, fileName, contentType);
        return true;
    },

    isPowerPointFile(file) {
        const mimeType = FilesPreview.resolveInitialMimeType(file);
        if (
            mimeType === 'application/vnd.ms-powerpoint' ||
            mimeType === 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        ) {
            return true;
        }

        const extension = FilesPreview.getFileExtension(file);
        return extension === 'ppt' || extension === 'pptx';
    },

    isGeneratedSlidePresentation(file) {
        return Boolean(
            file?.meta?.render_slide_presentation
            || file?.meta?.slide_presentation_source
        );
    },

    isSlidePresentationPreviewOpenForFile(file) {
        const widget = window.slidePresentationWidget;
        const fileId = String(file?.file_id ?? file?.id ?? '');
        if (!widget || typeof widget.isPreviewOpen !== 'function' || typeof widget.getActiveFileId !== 'function') {
            return false;
        }
        return Boolean(widget.isPreviewOpen() && widget.getActiveFileId() === fileId);
    },

    closeSlidePresentationPreview() {
        const widget = window.slidePresentationWidget;
        if (widget && typeof widget.hidePreviewPanel === 'function') {
            widget.hidePreviewPanel();
        }
    },

    async resolveSlidePresentationPreview(file) {
        const fileId = String(file?.file_id ?? file?.id ?? '').trim();
        if (!fileId) return null;

        const response = await window.authedFetch(`/api/v1/presentations/by-file/${encodeURIComponent(fileId)}`);
        if (!response.ok) {
            return null;
        }

        const payload = await response.json().catch(() => null);
        if (!payload?.presentation_id) {
            return null;
        }

        return payload;
    },

    async openSlidePresentationPreview(file) {
        const widget = window.slidePresentationWidget;
        if (!widget || typeof widget.openExistingPresentationPreview !== 'function') {
            return false;
        }

        const resolved = await FilesPreview.resolveSlidePresentationPreview(file);
        if (!resolved) {
            return false;
        }

        FilesPreview.close();
        await widget.openExistingPresentationPreview({
            fileId: resolved.file_id || file?.file_id || file?.id || '',
            presentationId: resolved.presentation_id,
            title: resolved.title || file?.meta?.original_filename || filesT('files_presentation', 'Presentation'),
            slideCount: resolved.slide_count || 0,
        });
        return true;
    },

    // ========================================================================
    // Open / Close
    // ========================================================================

    async open(file) {
        const sidebar = DOM.filesPreviewSidebar;
        const backdrop = DOM.filesPreviewBackdrop;
        const title = DOM.filesPreviewTitle;
        const body = DOM.filesPreviewBody;
        const fileId = String(file?.file_id ?? file?.id ?? '').trim();
        const resolvedFileName = file?.meta?.original_filename || file?.file_name || file?.name || 'website.html';

        // Canonical presentation HTML is deliberately a Canvas-compatible
        // file, but its primary preview is the rendered slide sidebar. Check
        // presentation identity before the generic HTML Canvas route.
        if (fileId && FilesPreview.isGeneratedSlidePresentation(file)) {
            try {
                if (await FilesPreview.openSlidePresentationPreview(file)) {
                    return;
                }
            } catch (error) {
                console.error('Failed to open slide presentation preview', error);
                notifyError?.(filesT('files_preview_powerpoint_error', 'Failed to open PowerPoint preview.'));
            }
        }

        if (fileId && FilesPreview.isCanvasPreviewFile(file)) {
            // Chat attachments and Workspace rows both call open(). Keeping
            // the toggle here gives both entry points Canvas-card behavior.
            if (FilesPreview.isCanvasPreviewOpenForFile(fileId)) {
                FilesPreview.closeCanvasPreview();
                return;
            }
            if (await FilesPreview.openCanvasPreview(file, fileId, resolvedFileName)) {
                return;
            }
        }

        // Generic preview markup is not required for Canvas-compatible files,
        // but it must exist before rendering every other file type.
        if (!sidebar || !title || !body) return;

        // All right-hand artifact surfaces participate in one exclusive
        // handoff, so opening a generic file also closes every specialized
        // preview without each widget knowing about all the others.
        window.closeOtherArtifactPreviews?.('files-preview');

        // Cleanup previous state
        FilesPreview.cleanupObjectUrl();

        // Store file info
        FilesPreview.activeFileId = file?.file_id ?? file?.id ?? null;
        FilesPreview.activeFile = file;
        FilesPreview.isOpen = true;
        FilesPreview.applyLayoutMode(FilesPreview.getPreferredLayoutMode(file), file);
        FilesPreview.syncPreviewIcons();

        // Set title
        const fileName = file?.meta?.original_filename || filesT('files_preview_title', 'File preview');
        title.textContent = fileName;
        title.title = fileName;

        // Show loading state
        body.innerHTML = `
            <div class="files-preview-placeholder">
                ${Icons.loading_circle}
	                <span>${Utils.escapeHtml(filesT('files_preview_loading', 'Loading preview...'))}</span>
            </div>
        `;

        // Open panel with animation
        sidebar.classList.add('open');
        sidebar.setAttribute('aria-hidden', 'false');
        
        // Show backdrop (especially important for mobile)
        if (backdrop) {
            backdrop.classList.add('active');
            backdrop.setAttribute('aria-hidden', 'false');
        }

        // Lock body scroll for mobile sheets and image lightboxes
        if (FilesPreview.shouldLockBodyScroll()) {
            document.body.style.overflow = 'hidden';
        }

        // Bind events
        FilesPreview.bindEvents();
        FilesPreview.updateDownloadButtonState(file);

        // Focus management for accessibility
        setTimeout(() => {
            const closeBtn = DOM.filesPreviewClose;
            if (closeBtn) closeBtn.focus();
        }, 100);

        // Load preview content
        try {
            const previewElement = await FilesPreview.createPreviewElement(file);
            body.innerHTML = '';
            
            if (previewElement) {
                body.appendChild(previewElement);
            } else {
                body.appendChild(FilesPreview.createUnsupportedPreview(file));
            }
        } catch (error) {
            console.error('Failed to render file preview', error);
            body.innerHTML = `
                <div class="files-preview-placeholder">
                        ${Icons.info}
	                    <span>${Utils.escapeHtml(error?.message || filesT('files_preview_load_error', 'Failed to load preview'))}</span>
                </div>
            `;
        }
    },

    close() {
        const sidebar = DOM.filesPreviewSidebar;
        const backdrop = DOM.filesPreviewBackdrop;
        const body = DOM.filesPreviewBody;
        const wasImageLayout = FilesPreview.activeLayoutMode === 'image';
        
        if (!sidebar) return;

        // Cleanup
        FilesPreview.cleanupObjectUrl();
        FilesPreview.isOpen = false;

        // Close panel with animation
        sidebar.classList.remove('open', 'dragging');
        sidebar.setAttribute('aria-hidden', 'true');
        sidebar.style.transform = '';
        
        // Hide backdrop
        if (backdrop) {
            backdrop.classList.remove('active');
            backdrop.setAttribute('aria-hidden', 'true');
        }

        // Restore body scroll
        document.body.style.overflow = '';

        // Unbind events
        FilesPreview.unbindEvents();

        // Clear state
        FilesPreview.activeFileId = null;
        FilesPreview.activeFile = null;
        FilesPreview.activeLayoutMode = 'panel';
        FilesPreview.updateDownloadButtonState();

        // Clear content after animation
        setTimeout(() => {
            if (!FilesPreview.isOpen) {
                if (wasImageLayout) {
                    sidebar.classList.remove('files-preview-image-modal');
                    sidebar.setAttribute('aria-labelledby', 'filesPreviewTitle');
                    sidebar.removeAttribute('aria-label');
                    backdrop?.classList.remove('files-preview-image-backdrop');
                }
            }

            if (body && !FilesPreview.isOpen) {
                body.innerHTML = '';
            }
        }, 350);
    },

    // ========================================================================
    // Preview Content Creation
    // ========================================================================

    async createPreviewElement(file) {
        let fileType = FilesPreview.resolveInitialMimeType(file);
        const fileId = file?.file_id ?? file?.id;
        if (!fileId) return null;

        let isImage = FilesPreview.supportedImageTypes.includes(fileType);
        let isPdf = FilesPreview.supportedPdfTypes.includes(fileType);
        let isText = FilesPreview.isTextMimeType(fileType);
        let isAudio = FilesPreview.supportedAudioTypes.includes(fileType) || fileType?.startsWith('audio/');
        let isVideo = FilesPreview.supportedVideoTypes.includes(fileType) || fileType?.startsWith('video/');
        const hasKnownType = Boolean(fileType) && !FilesPreview.isGenericBinaryMimeType(fileType);

        if (!isImage && !isPdf && !isText && !isAudio && !isVideo && hasKnownType) {
            return null;
        }

        const params = new URLSearchParams({ file_id: fileId, inline: 'true' });
        const downloadUrl = `${API_ENDPOINTS.DOWNLOAD}?${params.toString()}`;

        const requestHeaders = { 'accept': '*/*', 'Content-Type': null };
        if (isText) {
            requestHeaders.Range = `bytes=0-${FilesPreview.textPreviewMaxBytes - 1}`;
        }

        if ((isImage || isPdf || isAudio || isVideo) && FilesPreview.isOverPreviewLimit(file, FilesPreview.binaryPreviewMaxBytes)) {
            FilesPreview.applyLayoutMode('panel', file);
            return FilesPreview.createPreviewTooLargeElement(file, FilesPreview.binaryPreviewMaxBytes);
        }

        let response = await window.authedFetch(downloadUrl, {
            method: 'GET',
            headers: requestHeaders,
        });

        if (response.status === 401) {
            redirectToLogin?.();
	            throw new Error(filesT('files_error_auth_required_signin', 'Authentication required. Please sign in again.'));
        }

        if (!response.ok) {
	            throw new Error(filesT('files_preview_unavailable', 'This file is no longer available.'));
        }

        const responseContentType = FilesPreview.normalizeMimeType(response.headers.get('Content-Type'));
        if (responseContentType && (!FilesPreview.isGenericBinaryMimeType(responseContentType) || !fileType)) {
            fileType = responseContentType;
        }

        FilesPreview.applyLayoutMode(FilesPreview.getPreferredLayoutMode(file, fileType), file);

        isImage = FilesPreview.supportedImageTypes.includes(fileType);
        isPdf = FilesPreview.supportedPdfTypes.includes(fileType);
        isText = FilesPreview.isTextMimeType(fileType);
        isAudio = FilesPreview.supportedAudioTypes.includes(fileType) || fileType?.startsWith('audio/');
        isVideo = FilesPreview.supportedVideoTypes.includes(fileType) || fileType?.startsWith('video/');

        if (!isText && requestHeaders.Range) {
            FilesPreview.cancelResponseBody(response);
            if ((isImage || isPdf || isAudio || isVideo) && FilesPreview.isOverPreviewLimit(file, FilesPreview.binaryPreviewMaxBytes)) {
                FilesPreview.applyLayoutMode('panel', file);
                return FilesPreview.createPreviewTooLargeElement(file, FilesPreview.binaryPreviewMaxBytes);
            }

            response = await window.authedFetch(downloadUrl, {
                method: 'GET',
                headers: { 'accept': '*/*', 'Content-Type': null },
            });

            if (response.status === 401) {
                redirectToLogin?.();
	                throw new Error(filesT('files_error_auth_required_signin', 'Authentication required. Please sign in again.'));
            }

            if (!response.ok) {
	                throw new Error(filesT('files_preview_unavailable', 'This file is no longer available.'));
            }

            const fullResponseContentType = FilesPreview.normalizeMimeType(response.headers.get('Content-Type'));
            if (fullResponseContentType && (!FilesPreview.isGenericBinaryMimeType(fullResponseContentType) || !fileType)) {
                fileType = fullResponseContentType;
            }

            FilesPreview.applyLayoutMode(FilesPreview.getPreferredLayoutMode(file, fileType), file);

            isImage = FilesPreview.supportedImageTypes.includes(fileType);
            isPdf = FilesPreview.supportedPdfTypes.includes(fileType);
            isText = FilesPreview.isTextMimeType(fileType);
            isAudio = FilesPreview.supportedAudioTypes.includes(fileType) || fileType?.startsWith('audio/');
            isVideo = FilesPreview.supportedVideoTypes.includes(fileType) || fileType?.startsWith('video/');
        }

        if (isText) {
            const { text, truncated } = await FilesPreview.readTextPreviewContent(response);
            return FilesPreview.createTextPreviewElement(text, { truncated });
        }

        if (!isImage && !isPdf && !isAudio && !isVideo) {
            FilesPreview.cancelResponseBody(response);
            return null;
        }

        if (FilesPreview.isOverPreviewLimit(file, FilesPreview.binaryPreviewMaxBytes)) {
            FilesPreview.cancelResponseBody(response);
            FilesPreview.applyLayoutMode('panel', file);
            return FilesPreview.createPreviewTooLargeElement(file, FilesPreview.binaryPreviewMaxBytes);
        }

        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        FilesPreview.activeObjectUrl = objectUrl;

        if (isImage) {
            return FilesPreview.createImagePreviewElement(objectUrl, file, fileType);
        }

        if (isPdf) {
            return FilesPreview.createPdfPreviewElement(objectUrl, file);
        }

        if (isAudio) {
            return FilesPreview.createAudioPreviewElement(objectUrl, file, fileType);
        }

        if (isVideo) {
            return FilesPreview.createVideoPreviewElement(objectUrl, file, fileType);
        }

        return null;
    },

    createVideoPreviewElement(objectUrl, file, type = '') {
        const container = document.createElement('div');
        container.className = 'files-preview-video-container';

        const normalizedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        const isSupported = FilesPreview.supportedVideoTypes.includes(normalizedType);

        if (!isSupported) {
            const fallback = document.createElement('div');
            fallback.className = 'files-preview-fallback';
            fallback.textContent = filesT('files_preview_video_unsupported', 'This video format cannot be previewed in the browser.');
            container.appendChild(fallback);
            return container;
        }

        const video = document.createElement('video');
        video.className = 'files-preview-video-player';
        video.controls = true;
        video.preload = 'metadata';
        video.playsInline = true;
        video.setAttribute('controlsList', 'nodownload');

        const source = document.createElement('source');
        source.src = objectUrl;
        if (normalizedType) {
            source.type = normalizedType;
        }

        video.appendChild(source);
        video.appendChild(document.createTextNode(filesT('files_preview_video_no_support', 'Your browser does not support the video element.')));
        container.appendChild(video);

        // Add error handling for playback failures
        const handleError = () => {
            video.controls = false;
            video.removeAttribute('controlsList');
            
            // Log detailed error
            if (video.error) {
                console.error('Video preview error:', {
                    code: video.error.code,
                    message: video.error.message,
                    type: normalizedType,
                    file: file?.meta?.original_filename
                });
            }
            
            // Revoke objectUrl to free memory
            if (objectUrl && typeof URL.revokeObjectURL === 'function') {
                URL.revokeObjectURL(objectUrl);
                if (FilesPreview.activeObjectUrl === objectUrl) {
                    FilesPreview.activeObjectUrl = null;
                }
            }
            
            // Replace video with error message
            container.innerHTML = '';
            const errorDiv = document.createElement('div');
            errorDiv.className = 'files-preview-error';
            errorDiv.setAttribute('role', 'alert');
            errorDiv.textContent = filesT('files_preview_video_unsupported', 'This video format cannot be previewed in the browser.');
            container.appendChild(errorDiv);
            
            // Remove listener to prevent leaks
            video.removeEventListener('error', handleError);
        };
        
        video.addEventListener('error', handleError);

        return container;
    },

    createImagePreviewElement(objectUrl, file, type = '') {
        const container = document.createElement('div');
        container.className = 'files-preview-image-container';

        const img = document.createElement('img');
        img.src = objectUrl;
        img.alt = file?.meta?.original_filename || filesT('files_preview_image_alt', 'Preview image');
        img.className = 'files-preview-image';
        // The data attribute lets CSS override tiny intrinsic SVG dimensions
        // while preserving the natural sizing of raster images.
        img.dataset.fileType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        img.loading = 'eager';
        img.draggable = false;
        
        container.appendChild(img);
        return container;
    },

    createPdfPreviewElement(objectUrl, file) {
        const iframe = document.createElement('iframe');
        iframe.src = objectUrl;
        iframe.title = file?.meta?.original_filename || filesT('files_preview_pdf_title', 'Preview PDF');
        iframe.className = 'files-preview-iframe';
        iframe.setAttribute('loading', 'eager');
        return iframe;
    },

    createTextPreviewElement(content, { truncated = false } = {}) {
        const pre = document.createElement('pre');
        pre.className = 'files-preview-text';
        pre.textContent = typeof content === 'string' ? content : '';
        if (!truncated) {
            return pre;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'files-preview-text-wrapper';

        const notice = document.createElement('div');
        notice.className = 'files-preview-text-notice';
        notice.setAttribute('role', 'note');
        notice.textContent = filesFormatT(
            'files_preview_text_truncated',
            'Preview truncated to the first {size}. Download the file to view everything.',
            { size: FilesPreview.getPreviewLimitLabel(FilesPreview.textPreviewMaxBytes) }
        );

        wrapper.appendChild(notice);
        wrapper.appendChild(pre);
        return wrapper;
    },

    createPreviewTooLargeElement(file, limitBytes = FilesPreview.binaryPreviewMaxBytes) {
        const container = document.createElement('div');
        container.className = 'files-preview-unsupported';

        const icon = Icons.createSvgElement(Icons.file, 'files-preview-unsupported-icon');

        const text = document.createElement('div');
        text.className = 'files-preview-unsupported-text';
        text.textContent = FilesPreview.getPreviewTooLargeMessage(limitBytes);

        const fileSize = FilesPreview.getFileSize(file);
        if (fileSize) {
            const size = document.createElement('span');
            size.className = 'files-preview-size-note';
            size.textContent = Utils.formatFileSize(fileSize);
            text.appendChild(document.createElement('br'));
            text.appendChild(size);
        }

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'files-preview-download-btn';
        button.addEventListener('click', () => FilesPreview.handleDownloadClick());
        button.textContent = filesT('files_preview_download_file', 'Download File');

        container.appendChild(icon);
        container.appendChild(text);
        container.appendChild(button);
        return container;
    },

    /**
     * Format media time for the compact audio player.
     *
     * Keeping this formatter local to FilesPreview avoids coupling the file
     * workspace to the recording utilities used elsewhere in the chat UI.
     * Unknown media durations intentionally render as zero until the browser
     * has loaded the audio metadata.
     */
    formatAudioTime(totalSeconds) {
        const safeSeconds = Number.isFinite(Number(totalSeconds))
            ? Math.max(0, Math.floor(Number(totalSeconds)))
            : 0;
        const hours = Math.floor(safeSeconds / 3600);
        const minutes = Math.floor((safeSeconds % 3600) / 60);
        const seconds = safeSeconds % 60;

        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
        return `${minutes}:${String(seconds).padStart(2, '0')}`;
    },

    /**
     * Build the custom audio preview shown in the Workspace file panel.
     *
     * The native audio element remains the playback engine while the visible
     * controls reproduce the compact hybrid design from the approved demo.
     * A native range input provides pointer, touch, and keyboard seeking
     * without reimplementing slider accessibility.
     */
    createAudioPreviewElement(objectUrl, file, type = '') {
        const fileName = file?.meta?.original_filename || filesT('files_preview_audio_file', 'Audio file');
        const resolvedFileSize = FilesPreview.getFileSize(file);
        const fileSize = resolvedFileSize ? Utils.formatFileSize(resolvedFileSize) : '';
        const extension = FilesPreview.getFileExtension(file)?.toUpperCase() || 'AUDIO';
        const playLabel = filesT('files_preview_audio_play', 'Play');
        const pauseLabel = filesT('files_preview_audio_pause', 'Pause');
        const positionLabel = filesT('files_preview_audio_position', 'Playback position');
        const unsupportedLabel = filesT(
            'files_preview_audio_no_support',
            'Your browser does not support the audio element.'
        );

        const container = document.createElement('div');
        container.className = 'files-preview-audio-container files-preview-audio-container--hybrid';

        const header = document.createElement('div');
        header.className = 'files-preview-audio-header';

        const formatEl = document.createElement('div');
        formatEl.className = 'files-preview-audio-format';
        formatEl.setAttribute('aria-hidden', 'true');
        formatEl.textContent = extension;

        const info = document.createElement('div');
        info.className = 'files-preview-audio-info';

        const titleEl = document.createElement('div');
        titleEl.className = 'files-preview-audio-title';
        titleEl.textContent = fileName;
        titleEl.title = fileName;
        info.appendChild(titleEl);

        if (fileSize) {
            const sizeEl = document.createElement('div');
            sizeEl.className = 'files-preview-audio-size';
            sizeEl.textContent = fileSize;
            info.appendChild(sizeEl);
        }

        header.appendChild(formatEl);
        header.appendChild(info);

        const controls = document.createElement('div');
        controls.className = 'files-preview-audio-controls';

        const playButton = document.createElement('button');
        playButton.type = 'button';
        playButton.className = 'files-preview-audio-play-button';
        playButton.setAttribute('aria-label', playLabel);
        playButton.innerHTML = Icons.play;

        const track = document.createElement('div');
        track.className = 'files-preview-audio-track';

        const seek = document.createElement('input');
        seek.type = 'range';
        seek.className = 'files-preview-audio-seek';
        seek.min = '0';
        seek.max = '0';
        seek.step = '0.01';
        seek.value = '0';
        seek.disabled = true;
        seek.setAttribute('aria-label', positionLabel);
        seek.setAttribute('aria-valuetext', FilesPreview.formatAudioTime(0));
        seek.style.setProperty('--audio-progress', '0%');

        const times = document.createElement('div');
        times.className = 'files-preview-audio-times';

        const currentTime = document.createElement('span');
        currentTime.className = 'files-preview-audio-time';
        currentTime.textContent = FilesPreview.formatAudioTime(0);

        const durationTime = document.createElement('span');
        durationTime.className = 'files-preview-audio-time';
        durationTime.textContent = FilesPreview.formatAudioTime(0);

        times.appendChild(currentTime);
        times.appendChild(durationTime);
        track.appendChild(seek);
        track.appendChild(times);
        controls.appendChild(playButton);
        controls.appendChild(track);

        const errorMessage = document.createElement('p');
        errorMessage.className = 'files-preview-audio-error';
        errorMessage.setAttribute('role', 'alert');
        errorMessage.textContent = unsupportedLabel;
        errorMessage.hidden = true;

        const audioEl = document.createElement('audio');
        audioEl.className = 'files-preview-audio-player';
        audioEl.preload = 'metadata';
        audioEl.hidden = true;

        const sourceEl = document.createElement('source');
        sourceEl.src = objectUrl;
        // Prefer the response MIME type discovered by createPreviewElement.
        // This keeps extensionless audio previewable when stored metadata was
        // generic but the download response supplied an accurate media type.
        const resolvedType = FilesPreview.normalizeMimeType(type || FilesPreview.resolveInitialMimeType(file));
        if (resolvedType) {
            sourceEl.type = resolvedType;
        }
        audioEl.appendChild(sourceEl);
        audioEl.appendChild(document.createTextNode(unsupportedLabel));

        let hasPlaybackError = false;

        /** Reflect the audio engine's current time in every visible control. */
        const syncProgress = () => {
            const duration = Number.isFinite(audioEl.duration) && audioEl.duration > 0
                ? audioEl.duration
                : 0;
            const elapsed = Number.isFinite(audioEl.currentTime)
                ? Math.min(Math.max(audioEl.currentTime, 0), duration || audioEl.currentTime)
                : 0;
            const progress = duration > 0 ? Math.min(100, Math.max(0, (elapsed / duration) * 100)) : 0;
            const progressLabel = `${Math.round(progress * 1000) / 1000}%`;

            currentTime.textContent = FilesPreview.formatAudioTime(elapsed);
            durationTime.textContent = FilesPreview.formatAudioTime(duration);
            seek.max = String(duration);
            seek.value = String(elapsed);
            seek.disabled = hasPlaybackError || duration <= 0;
            seek.setAttribute('aria-valuetext', FilesPreview.formatAudioTime(elapsed));
            seek.style.setProperty('--audio-progress', progressLabel);
        };

        /** Keep the icon and accessible label synchronized with playback. */
        const syncPlayingState = (isPlaying) => {
            container.classList.toggle('is-playing', isPlaying);
            playButton.innerHTML = isPlaying ? Icons.pause : Icons.play;
            playButton.setAttribute('aria-label', isPlaying ? pauseLabel : playLabel);
        };

        /** Disable unusable controls and surface a translated playback error. */
        const showPlaybackError = () => {
            hasPlaybackError = true;
            syncPlayingState(false);
            playButton.disabled = true;
            seek.disabled = true;
            errorMessage.hidden = false;
        };

        playButton.addEventListener('click', async () => {
            errorMessage.hidden = true;
            if (!audioEl.paused) {
                audioEl.pause();
                return;
            }

            if (audioEl.ended) {
                audioEl.currentTime = 0;
            }

            try {
                await audioEl.play();
            } catch (error) {
                // A quick pause, preview close, or source replacement can
                // legitimately interrupt play(). The media remains usable, so
                // do not turn that browser race into a permanent error state.
                if (error?.name === 'AbortError') {
                    return;
                }
                console.error('Audio preview playback failed', error);
                showPlaybackError();
            }
        });

        seek.addEventListener('input', () => {
            const duration = Number.isFinite(audioEl.duration) ? audioEl.duration : 0;
            const nextTime = Math.min(Math.max(Number(seek.value) || 0, 0), duration);
            audioEl.currentTime = nextTime;
            syncProgress();
        });

        audioEl.addEventListener('loadedmetadata', syncProgress);
        audioEl.addEventListener('durationchange', syncProgress);
        audioEl.addEventListener('timeupdate', syncProgress);
        audioEl.addEventListener('play', () => syncPlayingState(true));
        audioEl.addEventListener('pause', () => syncPlayingState(false));
        audioEl.addEventListener('ended', () => {
            syncPlayingState(false);
            syncProgress();
        });
        audioEl.addEventListener('error', showPlaybackError);

        container.appendChild(header);
        container.appendChild(controls);
        container.appendChild(errorMessage);
        container.appendChild(audioEl);

        return container;
    },

    /**
     * Build the fallback shown for file types that browsers cannot preview.
     *
     * The download action must be registered with addEventListener rather than
     * an inline `onclick` attribute because Omlorix's Content Security Policy
     * intentionally blocks inline script execution.
     */
    createUnsupportedPreview(file) {
        const extension = FilesPreview.getFileExtension(file) || filesT('files_preview_unknown_extension', 'unknown');
        const escapedExtension = Utils.escapeHtml(extension.toUpperCase());
        const container = document.createElement('div');
        container.className = 'files-preview-unsupported';

        const icon = Icons.createSvgElement(Icons.file, 'files-preview-unsupported-icon');

        const text = document.createElement('div');
        text.className = 'files-preview-unsupported-text';
        // Translation values contain the intentional <strong> wrapper. The
        // filename-derived extension is escaped before it enters that markup.
        text.innerHTML = filesFormatT(
            'files_preview_unsupported_text',
            '<strong>.{extension}</strong> files cannot be previewed directly.',
            { extension: escapedExtension }
        );

        const fileSize = FilesPreview.getFileSize(file);
        if (fileSize) {
            const size = document.createElement('span');
            size.className = 'files-preview-size-note';
            size.textContent = Utils.formatFileSize(fileSize);
            text.appendChild(document.createElement('br'));
            text.appendChild(size);
        }

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'files-preview-download-btn';
        button.innerHTML = `${Icons.download}${Utils.escapeHtml(filesT('files_preview_download_file', 'Download File'))}`;
        button.addEventListener('click', FilesPreview.handleDownloadClick);

        container.appendChild(icon);
        container.appendChild(text);
        container.appendChild(button);
        return container;
    },

    // ========================================================================
    // Event Handlers
    // ========================================================================

    handleKeydown(event) {
        if (!FilesPreview.isOpen) return;
        
        if (event.key === 'Escape') {
            event.preventDefault();
            FilesPreview.close();
        }
    },

    handleBackdropClick(event) {
        if (event.target === DOM.filesPreviewBackdrop) {
            FilesPreview.close();
        }
    },

    /**
     * Close a fullscreen image preview when its transparent surface is clicked.
     *
     * The fullscreen dialog covers the backdrop so that it can center the image
     * and keep the close control above it. As a result, the visually blurred
     * area is technically part of the dialog rather than the backdrop element.
     * Handle that surface explicitly while preserving clicks on the image and
     * the header controls.
     */
    handlePreviewSurfaceClick(event) {
        if (!FilesPreview.isOpen || FilesPreview.activeLayoutMode !== 'image') {
            return;
        }

        const target = event?.target;
        const isPreviewContent = typeof target?.closest === 'function'
            && target.closest('.files-preview-image, .main-container-header-buttons');
        if (isPreviewContent) {
            return;
        }

        FilesPreview.close();
    },

    // Desktop resize handlers
    handleResizeStart(event) {
        if (FilesPreview.isMobile()) return;
        
        event.preventDefault();
        const sidebar = DOM.filesPreviewSidebar;
        const handle = DOM.filesPreviewResizeHandle;
        if (!sidebar) return;

        FilesPreview.isResizing = true;
        FilesPreview.startX = event.clientX;
        FilesPreview.startWidth = sidebar.offsetWidth;

        if (handle) handle.classList.add('resizing');
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
    },

    handleResizeMove(event) {
        if (!FilesPreview.isResizing) return;

        const sidebar = DOM.filesPreviewSidebar;
        if (!sidebar) return;

        const deltaX = FilesPreview.startX - event.clientX;
        const newWidth = FilesPreview.startWidth + deltaX;
        const minWidth = 320;
        const maxWidth = window.innerWidth * 0.85;

        if (newWidth >= minWidth && newWidth <= maxWidth) {
            sidebar.style.width = `${newWidth}px`;
        }
    },

    handleResizeEnd() {
        if (!FilesPreview.isResizing) return;

        const handle = DOM.filesPreviewResizeHandle;
        FilesPreview.isResizing = false;

        if (handle) handle.classList.remove('resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    },

    // Mobile touch handlers (swipe to close)
    handleTouchStart(event) {
        if (!FilesPreview.isMobile() || !FilesPreview.isOpen) return;
        
        const touch = event.touches[0];
        if (!touch) return;

        FilesPreview.isDragging = true;
        FilesPreview.dragStartY = touch.clientY;
        FilesPreview.dragCurrentY = touch.clientY;
        
        const sidebar = DOM.filesPreviewSidebar;
        if (sidebar) sidebar.classList.add('dragging');
    },

    handleTouchMove(event) {
        if (!FilesPreview.isDragging || !FilesPreview.isMobile()) return;
        
        const touch = event.touches[0];
        if (!touch) return;

        FilesPreview.dragCurrentY = touch.clientY;
        const deltaY = FilesPreview.dragCurrentY - FilesPreview.dragStartY;
        
        // Only allow dragging down
        if (deltaY > 0) {
            const sidebar = DOM.filesPreviewSidebar;
            if (sidebar) {
                sidebar.style.transform = `translateY(${deltaY}px)`;
            }
            
            // Update backdrop opacity based on drag distance
            const backdrop = DOM.filesPreviewBackdrop;
            if (backdrop) {
                const progress = Math.min(deltaY / 300, 1);
                backdrop.style.opacity = String(1 - progress * 0.5);
            }
        }
    },

    handleTouchEnd() {
        if (!FilesPreview.isDragging) return;

        const sidebar = DOM.filesPreviewSidebar;
        const backdrop = DOM.filesPreviewBackdrop;
        const deltaY = FilesPreview.dragCurrentY - FilesPreview.dragStartY;

        FilesPreview.isDragging = false;
        
        if (sidebar) sidebar.classList.remove('dragging');
        if (backdrop) backdrop.style.opacity = '';

        // Close if dragged past threshold
        if (deltaY > FilesPreview.dragThreshold) {
            FilesPreview.close();
        } else {
            // Snap back
            if (sidebar) sidebar.style.transform = '';
        }
    },

    handleDownloadClick() {
        const fileId = FilesPreview.activeFileId;
        if (!fileId) return;

        FileOperations.downloadFile(fileId).catch((error) => {
            console.error('Failed to download from preview', error);
            notifyError?.(filesT('files_download_failed', 'Failed to download file'));
        });
    },

    handleDragHandleClick() {
        if (FilesPreview.isMobile()) {
            FilesPreview.close();
        }
    },

    // ========================================================================
    // Event Binding
    // ========================================================================

    bindEvents() {
        if (FilesPreview.eventsBound) return;

        // Keyboard
        document.addEventListener('keydown', FilesPreview.handleKeydown);
        
        // Close button
        DOM.filesPreviewClose?.addEventListener('click', FilesPreview.close);
        
        // Download button
        DOM.filesPreviewDownload?.addEventListener('click', FilesPreview.handleDownloadClick);
        
        // Backdrop click
        DOM.filesPreviewBackdrop?.addEventListener('click', FilesPreview.handleBackdropClick);

        // A fullscreen image dialog occupies the entire viewport above the
        // backdrop, so its transparent area needs its own outside-click handler.
        DOM.filesPreviewSidebar?.addEventListener('click', FilesPreview.handlePreviewSurfaceClick);
        
        // Desktop resize
        const resizeHandle = DOM.filesPreviewResizeHandle;
        if (resizeHandle) {
            resizeHandle.addEventListener('mousedown', FilesPreview.handleResizeStart);
        }
        document.addEventListener('mousemove', FilesPreview.handleResizeMove);
        document.addEventListener('mouseup', FilesPreview.handleResizeEnd);
        
        // Mobile touch gestures
        const dragHandle = DOM.filesPreviewDragHandle;
        if (dragHandle) {
            dragHandle.addEventListener('touchstart', FilesPreview.handleTouchStart, { passive: true });
            dragHandle.addEventListener('touchmove', FilesPreview.handleTouchMove, { passive: true });
            dragHandle.addEventListener('touchend', FilesPreview.handleTouchEnd, { passive: true });

            // Also allow clicking drag handle to close on mobile
            dragHandle.addEventListener('click', FilesPreview.handleDragHandleClick);
        }
        
        // Handle window resize
        window.addEventListener('resize', FilesPreview.handleWindowResize);
        FilesPreview.eventsBound = true;
    },

    unbindEvents() {
        if (!FilesPreview.eventsBound) return;

        document.removeEventListener('keydown', FilesPreview.handleKeydown);
        DOM.filesPreviewClose?.removeEventListener('click', FilesPreview.close);
        DOM.filesPreviewDownload?.removeEventListener('click', FilesPreview.handleDownloadClick);
        DOM.filesPreviewBackdrop?.removeEventListener('click', FilesPreview.handleBackdropClick);
        DOM.filesPreviewSidebar?.removeEventListener('click', FilesPreview.handlePreviewSurfaceClick);
        
        const resizeHandle = DOM.filesPreviewResizeHandle;
        if (resizeHandle) {
            resizeHandle.removeEventListener('mousedown', FilesPreview.handleResizeStart);
        }
        document.removeEventListener('mousemove', FilesPreview.handleResizeMove);
        document.removeEventListener('mouseup', FilesPreview.handleResizeEnd);
        
        const dragHandle = DOM.filesPreviewDragHandle;
        if (dragHandle) {
            dragHandle.removeEventListener('touchstart', FilesPreview.handleTouchStart);
            dragHandle.removeEventListener('touchmove', FilesPreview.handleTouchMove);
            dragHandle.removeEventListener('touchend', FilesPreview.handleTouchEnd);
            dragHandle.removeEventListener('click', FilesPreview.handleDragHandleClick);
        }
        
        window.removeEventListener('resize', FilesPreview.handleWindowResize);
        FilesPreview.eventsBound = false;
    },

    handleWindowResize() {
        // Handle switching between mobile/desktop while preview is open
        if (!FilesPreview.isOpen) return;
        
        const sidebar = DOM.filesPreviewSidebar;
        if (!sidebar) return;
        
        // Reset any inline styles that may conflict
        sidebar.style.transform = '';
        
        // Update body scroll lock based on current viewport
        if (FilesPreview.shouldLockBodyScroll()) {
            document.body.style.overflow = 'hidden';
        } else {
            document.body.style.overflow = '';
        }
    },

    // ========================================================================
    // Utilities
    // ========================================================================

    cleanupObjectUrl() {
        // Stop media immediately when the panel closes or switches files. A
        // revoked object URL can remain playable from the browser's buffer,
        // so URL cleanup alone is not enough to end audible playback.
        const activeMedia = DOM.filesPreviewBody?.querySelectorAll?.('audio, video') || [];
        activeMedia.forEach((mediaElement) => {
            try {
                mediaElement.pause?.();
            } catch (_) {
                // The element is already being discarded; cleanup is best effort.
            }
        });

        if (FilesPreview.activeObjectUrl) {
            URL.revokeObjectURL(FilesPreview.activeObjectUrl);
            FilesPreview.activeObjectUrl = null;
        }
    },

    updateDownloadButtonState(file) {
        const downloadButton = DOM.filesPreviewDownload;
        if (!downloadButton) return;

        const hasFile = Boolean(file?.file_id ?? file?.id ?? FilesPreview.activeFileId);
        downloadButton.disabled = !hasFile;
        downloadButton.classList.toggle('is-disabled', !hasFile);
    },
};

// Expose FilesPreview globally for inline handlers
if (typeof window !== 'undefined') {
    window.FilesPreview = FilesPreview;
}

// ============================================================================
// Main Module
// ============================================================================

const FilesManager = {
_hasLoadedFiles: false,

async initialize({ force = false } = {}) {
    if (!isFilesViewVisible()) {
    return;
    }
    if (this._hasLoadedFiles && !force) {
    UI.updateUploadAvailability();
    UI.updateSearchControls(true);
    return;
    }
    if (!FilesManager.isFileUploadAllowed()) {
    UI.updateUploadAvailability();
    }
    UI.updateSearchControls(true);
    await FileOperations.loadFiles(state.shouldAnimate);
    this._hasLoadedFiles = true;
},

uploadFiles(fileList) {
    return FileOperations.uploadFiles(fileList);
},

async refresh(animate = false) {
    this._hasLoadedFiles = false;
    await FileOperations.loadFiles(animate);
    this._hasLoadedFiles = true;
},

async maybeLoadMore() {
    const scroller = DOM.filesContentScroller;
    if (!scroller || state.isLoading || state.isLoadingMore || !state.hasMore || !isFilesViewVisible()) {
    return;
    }
    if (scroller.clientHeight <= 0) {
    return;
    }

    const remaining = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (remaining <= FILES_LOAD_MORE_THRESHOLD) {
    await FileOperations.loadMoreFiles();
    }
},

getCachedFiles(options = {}) {
    return FileOperations.getCachedFiles(options);
},

setCachedFiles(files) {
    FileOperations.setCachedFiles(files);
},

updateSortField(field) {
    if (!field) {
    return;
    }

    const previousField = state.sortField;
    state.setSortField(field);
    if (state.sortField !== previousField) {
    state.setSortDirection(SORT_CONFIG.DEFAULT_DIRECTION);
    }
    this.refresh();
},

toggleSortDirection() {
    state.toggleSortDirection();
    this.refresh();
},

handleHeaderSort(field) {
    if (!field || !SORT_CONFIG.FIELDS.includes(field)) {
    return;
    }

    if (state.sortField === field) {
    state.toggleSortDirection();
    } else {
    state.setSortField(field);
    state.setSortDirection(SORT_CONFIG.DEFAULT_DIRECTION);
    }

    this.refresh();
},

setSearchQuery(query) {
    const normalized = String(query || '').trim();
    if (normalized === state.searchQuery) {
    UI.updateSearchControls(true);
    return;
    }
    state.setSearchQuery(normalized);
    UI.updateSearchControls(true);
    this.refresh();
},

async deleteAllWebsearchFiles() {
    try {
    const response = await window.authedFetch(`/api/v1/files/websearch`, {
        method: 'DELETE',
    });

    if (response.status === 401) {
        redirectToLogin?.();
        return;
    }

    if (!response.ok) {
        notifyError?.(filesT('files_delete_websearch_error', 'Failed to delete web search files.'));
        return;
    }

    await this.refresh(true);
    notifySuccess?.(filesT('files_delete_websearch_success', 'Deleted all web search files.'));
    } catch (error) {
    console.error('Failed to delete web search files', error);
    notifyError?.(filesT('files_delete_websearch_error_generic', 'An error occurred deleting web search files.'));
    }
},

isFileUploadAllowed() {
    return resolveAllowFileUploadsSetting(true);
},

getChatBox() {
    return document.getElementById('chatBoxArea');
},

getChatBoxHeader() {
    return this.getChatBox()?.querySelector('.chat-box-header');
},

getChatBoxContent() {
    return this.getChatBox()?.querySelector('.chat-box-content');
},

getChatBoxFooter() {
    return this.getChatBox()?.querySelector('.chat-box-footer');
},
};

// ============================================================================
// Auto-initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
EventHandlers.setupListeners();
FileDragDrop.init();
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
module.exports = FilesManager;
}

// Expose helpers for inline handlers in generated markup
if (typeof window !== 'undefined') {
window.FilesManager = FilesManager;
window.fetchFilesPage = async (options = {}) => API.fetchWorkspaceFiles(options);
window.getCachedFilesList = async ({
    forceRefresh = false,
    search = '',
    folderId = 'all',
    sortField = 'created_at',
    sortDirection = 'desc',
    limit = FILES_PAGE_SIZE,
    offset = 0,
} = {}) => {
    const cacheKey = JSON.stringify({
    search: String(search || '').trim(),
    folderId: String(folderId || 'all'),
    sortField: String(sortField || 'created_at'),
    sortDirection: String(sortDirection || 'desc'),
    limit: Number(limit || FILES_PAGE_SIZE),
    offset: Number(offset || 0),
    });
    if (!forceRefresh && FilesCache.isFresh(30000, cacheKey)) {
    return FilesCache.get();
    }
    try {
    const payload = await API.fetchWorkspaceFiles({
        search,
        folderId,
        sortField,
        sortDirection,
        limit,
        offset,
    });
    const files = Array.isArray(payload?.items) ? payload.items : (Array.isArray(payload) ? payload : []);
    if (offset === 0) {
    FilesCache.set(files, cacheKey);
    }
    return files;
    } catch (error) {
    console.error('Failed to refresh cached files', error);
    return FilesCache.get();
    }
};
window.downloadFile = (fileId) => FileOperations.downloadFile(fileId);
window.deleteFile = (eventOrFileId, maybeFileId) => {
    const hasEvent = typeof Event !== 'undefined' && eventOrFileId instanceof Event;
    const event = hasEvent ? eventOrFileId : null;
    const fileId = hasEvent ? maybeFileId : eventOrFileId;

    if (event) {
    event.preventDefault();
    event.stopPropagation();
    }

    return FileOperations.requestDeleteFile(fileId, {
    skipConfirm: Boolean(event?.shiftKey),
    triggerElement: event?.currentTarget || null,
    });
};
window.getFileIconForType = (fileType) => Utils.getFileIcon(fileType);
window.getFileExtensionLabel = (filename) => Utils.getFileExtension(filename);

if (!window.__filesImportRefreshListenerBound) {
    window.addEventListener('dataControls:importedDataChanged', async (event) => {
    if (!event?.detail?.refreshFiles) {
        return;
    }

    try {
        await window.getCachedFilesList({ forceRefresh: true });
    } catch (error) {
        console.warn('[files] Failed to refresh cached files after imported data change', error);
    }

    try {
        if (isFilesViewVisible()) {
        await FilesManager.initialize();
        }
    } catch (error) {
        console.warn('[files] Failed to refresh files workspace after imported data change', error);
    }
    });
    window.__filesImportRefreshListenerBound = true;
}

/**
 * Render a folder's stored preset SVG with the shared workspace resolver.
 */
function renderMoveMenuFolderIcon(folder) {
    const iconUtils = window.WorkspaceIconUtils;
    const iconOptions = Icons.workspaceIconPickerOptions || Icons.folderIconOptions;
    const iconData = iconUtils.resolveWorkspaceStoredIcon(folder?.icon, {
        iconOptions,
        defaultIconId: 'folder',
    });

    return iconUtils.renderWorkspaceIcon(iconData, {
        size: 16,
        defaultIconId: 'folder',
        iconOptions,
    });
}

window.showMoveToFolderMenu = function showMoveToFolderMenu(fileId, triggerButton) {
    if (!triggerButton) return;

    const folders = (typeof FileFoldersState !== 'undefined' && Array.isArray(FileFoldersState.folders))
        ? FileFoldersState.folders.filter(f => !f.is_subscribed)
        : [];

    const currentFile = typeof state.getFileById === 'function' ? state.getFileById(fileId) : null;
    const currentFolderId = String(currentFile?.folder_id || '').trim();
    const items = [{
        folderId: null,
        label: filesT('files_folder_uncategorized', 'Uncategorized'),
        iconHtml: Icons.grid,
        checked: !currentFolderId,
    }, ...folders.map((folder) => {
        const folderId = String(folder.id || '').trim();
        return {
            folderId,
            label: folder.name,
            iconHtml: renderMoveMenuFolderIcon(folder),
            checked: Boolean(currentFolderId) && currentFolderId === folderId,
        };
    })];

    window.openDropdownMenu({
        trigger: triggerButton,
        ariaLabel: filesT('files_move_to_folder', 'Move to folder'),
        items,
        onSelect: async ({ folderId }) => {
            if (typeof FileFoldersManager !== 'undefined') {
                await FileFoldersManager.moveFileToFolder(fileId, folderId);
                if (typeof notifySuccess === 'function') notifySuccess(filesT('files_moved', 'File moved'));
            }
        },
    });
};

// Expose FileDragDrop for external access
window.FileDragDrop = FileDragDrop;
}
