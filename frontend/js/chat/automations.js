// -----------------------
// Automations UI logic
// -----------------------
const automationsContainerMain = document.getElementById('automationsContainerMain');
const automationsContent = document.getElementById('automationsContent');
const automationsContentCreateAutomation = document.getElementById('automationsContentCreateAutomation');
const automationsContentEditAutomation = document.getElementById('automationsContentEditAutomation');

const addAutomationBtn = document.getElementById('addAutomationBtn');
const createAutomationCancelBtn = document.getElementById('createAutomationCancelBtn');
const confirmCreateAutomationBtn = document.getElementById('confirmCreateAutomationBtn');
const deleteAutomationCancelBtn = document.getElementById('deleteAutomationCancelBtn');
const confirmDeleteAutomationBtn = document.getElementById('confirmDeleteAutomationBtn');
const deleteAutomationOverlay = document.getElementById('deleteAutomationOverlay');
const editAutomationCancelBtn = document.getElementById('editAutomationCancelBtn');
const saveAutomationChangesBtn = document.getElementById('saveAutomationChangesBtn');

const AUTOMATIONS_PAGE_LIMIT = 200;

function unwrapAutomationsPage(payload, key = 'items') {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.[key])) return payload[key];
    if (Array.isArray(payload?.items)) return payload.items;
    return [];
}

const automationNameInput = document.getElementById('automationNameInput');
const automationNameError = document.getElementById('automationNameError');
const automationPromptInput = document.getElementById('automationPromptInput');
const automationPromptError = document.getElementById('automationPromptError');
const automationIconButton = document.getElementById('automationIconButton');
const automationIconDropdown = document.getElementById('automationIconDropdown');
const automationIconGrid = document.getElementById('automationIconGrid');
const automationColorRow = document.getElementById('automationColorRow');
const automationIconSaveBtn = document.getElementById('automationIconSaveBtn');
const automationIconCancelBtn = document.getElementById('automationIconCancelBtn');
const automationModelSelect = document.getElementById('automationModelSelect');
const automationConnectionsSelect = document.getElementById('automationConnectionsSelect');
const automationScheduleRules = document.getElementById('automationScheduleRules');
const automationScheduleError = document.getElementById('automationScheduleError');
const automationActiveToggle = document.getElementById('automationActiveToggle');

const automationEditNameInput = document.getElementById('automationEditNameInput');
const automationEditNameError = document.getElementById('automationEditNameError');
const automationEditPromptInput = document.getElementById('automationEditPromptInput');
const automationEditPromptError = document.getElementById('automationEditPromptError');
const automationEditIconButton = document.getElementById('automationEditIconButton');
const automationEditIconDropdown = document.getElementById('automationEditIconDropdown');
const automationEditIconGrid = document.getElementById('automationEditIconGrid');
const automationEditColorRow = document.getElementById('automationEditColorRow');
const automationEditIconSaveBtn = document.getElementById('automationEditIconSaveBtn');
const automationEditIconCancelBtn = document.getElementById('automationEditIconCancelBtn');
const automationEditModelSelect = document.getElementById('automationEditModelSelect');
const automationEditConnectionsSelect = document.getElementById('automationEditConnectionsSelect');
const automationEditScheduleRules = document.getElementById('automationEditScheduleRules');
const automationEditScheduleError = document.getElementById('automationEditScheduleError');
const automationEditActiveToggle = document.getElementById('automationEditActiveToggle');
const automationFilesSelected = document.getElementById('automationFilesSelected');
const automationFileInput = document.getElementById('automationFileInput');
const automationFileUploadBtn = document.getElementById('automationFileUploadBtn');
const automationFileLibraryBtn = document.getElementById('automationFileLibraryBtn');
const automationFileLibraryDropdown = document.getElementById('automationFileLibraryDropdown');
const automationEditFilesSelected = document.getElementById('automationEditFilesSelected');
const automationEditFileInput = document.getElementById('automationEditFileInput');
const automationEditFileUploadBtn = document.getElementById('automationEditFileUploadBtn');
const automationEditFileLibraryBtn = document.getElementById('automationEditFileLibraryBtn');
const automationEditFileLibraryDropdown = document.getElementById('automationEditFileLibraryDropdown');

const deleteAutomationTitle = document.getElementById('deleteAutomationTitle');

const AUTOMATION_ICON_SOURCE = typeof folderIconOptions !== 'undefined'
    ? folderIconOptions
    : Icons?.folderIconOptions;

// Transform the shared icon source to an array of SVG strings for rendering.
const AUTOMATION_SVG_ICONS = (() => {
    if (!AUTOMATION_ICON_SOURCE) return [];
    const values = Array.isArray(AUTOMATION_ICON_SOURCE)
        ? AUTOMATION_ICON_SOURCE
        : Object.values(AUTOMATION_ICON_SOURCE);
    return values
        .map((option) => {
            if (typeof option === 'string') return option;
            if (option && typeof option === 'object') {
                return option.svg || option.markup || option.icon || '';
            }
            return '';
        })
        .filter(Boolean);
})();
const FALLBACK_AUTOMATION_SVG_ICON = AUTOMATION_SVG_ICONS[0] || '';

const AUTOMATION_ICON_COLORS = [
    '#FF6B6B', '#FF8A65', '#FFB74D', '#FFE082', '#F4FF81',
    '#81C784', '#4DB6AC', '#4FC3F7', '#9575CD', '#F06292'
];

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DEFAULT_SCHEDULE_TIME = '09:00';
const DEFAULT_SCHEDULE_DAYS = [0, 1, 2, 3, 4];
const DEFAULT_ONE_TIME_DELAY_MINUTES = 10;

let activeAutomationContext = null;
let automationsModelsCache = [];
let automationsSkillsCache = [];
let automationsNotesCache = [];
let automationsFilesCache = [];
const automationsFilesMetaMap = new Map();
let automationFilesDropdownOutsideListenerBound = false;
let automationFileLibraryOpenMode = null;
const workspaceAutomationIconUtils = window.WorkspaceIconUtils;
const AUTOMATION_ICON_OPTIONS = workspaceAutomationIconUtils.getWorkspaceIconOptions(
    AUTOMATION_ICON_SOURCE,
);
const AUTOMATION_PICKER_COLORS = AUTOMATION_ICON_COLORS.map((hex, index) => ({
    id: String(index),
    name: hex,
    hex,
}));
const AUTOMATION_DEFAULT_ICON_ID = AUTOMATION_ICON_OPTIONS[0]?.id || 'folder';

const AutomationState = {
    create: {
        selectedIconId: AUTOMATION_DEFAULT_ICON_ID,
        selectedColorIndex: 0,
        isOpen: false,
        selectedModelId: null,
        modelSearchQuery: '',
        selectedMcpServerIds: [],
        availableConnections: [],
        connectionsLoading: false,
        connectionsError: false,
        scheduleRules: [],
        scheduleTimezone: null,
        selectedSkillId: null,
        selectedNoteIds: [],
        selectedFileIds: [],
        fileMetadata: {},
        fileLibrarySearch: '',
        isFilesLoading: false,
        isActive: true,
        triggerType: 'schedule',
        webhookTrigger: null,
        webhookSecret: null,
        webhookPayloadMode: 'append',
        webhookIncludeHeaders: false,
    },
    edit: {
        selectedIconId: AUTOMATION_DEFAULT_ICON_ID,
        selectedColorIndex: 0,
        isOpen: false,
        selectedModelId: null,
        modelSearchQuery: '',
        selectedMcpServerIds: [],
        availableConnections: [],
        connectionsLoading: false,
        connectionsError: false,
        scheduleRules: [],
        scheduleTimezone: null,
        selectedSkillId: null,
        selectedNoteIds: [],
        selectedFileIds: [],
        fileMetadata: {},
        fileLibrarySearch: '',
        isFilesLoading: false,
        isActive: true,
        triggerType: 'schedule',
        webhookTrigger: null,
        webhookSecret: null,
        webhookPayloadMode: 'append',
        webhookIncludeHeaders: false,
    },
};

let automationsModelSelectOutsideHandlerBound = false;
let automationSkillSelectOutsideHandlerBound = false;
let automationNotesSelectOutsideHandlerBound = false;
let automationConnectionsSelectOutsideHandlerBound = false;

function automationT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function automationFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(automationT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

const automationFormValidation = (typeof window !== 'undefined' && window.FormValidation) || {
    showInputError(inputEl, errorEl, message, options = {}) {
        if (!inputEl && !errorEl) {
            notifyError?.(message);
            return;
        }
        inputEl?.classList.add('input-error');
        inputEl?.setAttribute('aria-invalid', 'true');
        inputEl?.closest?.('.projects-create-input-group')?.classList.add('has-error');
        if (errorEl) {
            if (message) errorEl.textContent = message;
            errorEl.hidden = false;
            errorEl.classList.add('visible');
            errorEl.setAttribute('aria-hidden', 'false');
        }
        if (options.scroll !== false) {
            inputEl?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
        }
        if (options.focus !== false) {
            inputEl?.focus?.();
        }
    },
    clearInputError(inputEl, errorEl) {
        inputEl?.classList.remove('input-error');
        inputEl?.setAttribute('aria-invalid', 'false');
        inputEl?.closest?.('.projects-create-input-group')?.classList.remove('has-error');
        if (errorEl) {
            errorEl.hidden = true;
            errorEl.classList.remove('visible');
            errorEl.setAttribute('aria-hidden', 'true');
        }
    },
};

function getAutomationValidationRefs(mode) {
    return mode === 'edit'
        ? {
            nameInput: automationEditNameInput,
            nameError: automationEditNameError,
            promptInput: automationEditPromptInput,
            promptError: automationEditPromptError,
            scheduleContainer: automationEditScheduleRules,
            scheduleError: automationEditScheduleError,
        }
        : {
            nameInput: automationNameInput,
            nameError: automationNameError,
            promptInput: automationPromptInput,
            promptError: automationPromptError,
            scheduleContainer: automationScheduleRules,
            scheduleError: automationScheduleError,
        };
}

function clearAutomationFieldError(mode, field) {
    const refs = getAutomationValidationRefs(mode);
    if (field === 'name') {
        automationFormValidation.clearInputError(refs.nameInput, refs.nameError);
    } else if (field === 'prompt') {
        automationFormValidation.clearInputError(refs.promptInput, refs.promptError);
    } else if (field === 'schedule') {
        automationFormValidation.clearInputError(refs.scheduleContainer, refs.scheduleError);
    }
}

function clearAutomationValidationErrors(mode) {
    clearAutomationFieldError(mode, 'name');
    clearAutomationFieldError(mode, 'prompt');
    clearAutomationFieldError(mode, 'schedule');
}

function showAutomationScheduleError(mode, message) {
    const refs = getAutomationValidationRefs(mode);
    automationFormValidation.showInputError(refs.scheduleContainer, refs.scheduleError, message, { focus: false });
    const focusTarget = refs.scheduleContainer?.querySelector?.('input, button, [tabindex]:not([tabindex="-1"])');
    focusTarget?.focus?.();
}

function validateAutomationRequiredFields(mode) {
    const refs = getAutomationValidationRefs(mode);
    const name = refs.nameInput?.value?.trim() || '';
    const prompt = refs.promptInput?.value?.trim() || '';

    if (!name) {
        automationFormValidation.showInputError(
            refs.nameInput,
            refs.nameError,
            automationT('automations_error_name_required', 'Automation name is required'),
        );
        return null;
    }
    clearAutomationFieldError(mode, 'name');

    if (!prompt) {
        automationFormValidation.showInputError(
            refs.promptInput,
            refs.promptError,
            automationT('automations_error_prompt_required', 'Automation prompt is required'),
        );
        return null;
    }
    clearAutomationFieldError(mode, 'prompt');

    return { title: name, prompt };
}

function getAutomationState(mode) {
    return mode === 'edit' ? AutomationState.edit : AutomationState.create;
}

function getPreferredAutomationTimezone() {
    const rawTimezone = typeof window !== 'undefined' ? window.chatSetup?.timezone : null;
    if (typeof rawTimezone !== 'string') return null;
    const normalized = rawTimezone.trim();
    return normalized || null;
}

const AutomationUtils = {
    escapeHtml(text = '') {
        return workspaceAutomationIconUtils.escapeHtml(text);
    },
    sanitizeTimeValue(value) {
        if (typeof value !== 'string') return null;
        const trimmed = value.trim();
        const match = /^(\d{1,2}):(\d{2})$/.exec(trimmed);
        if (!match) return null;
        const hours = parseInt(match[1], 10);
        const minutes = parseInt(match[2], 10);
        if (
            Number.isNaN(hours) ||
            Number.isNaN(minutes) ||
            hours < 0 ||
            hours > 23 ||
            minutes < 0 ||
            minutes > 59
        ) {
            return null;
        }
        return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`;
    },
    zeroPad(value) {
        return String(value).padStart(2, '0');
    },
    sanitizeDateTimeLocalValue(value) {
        if (typeof value !== 'string') return null;
        const trimmed = value.trim();
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(trimmed);
        if (!match) return null;
        const date = new Date(
            parseInt(match[1], 10),
            parseInt(match[2], 10) - 1,
            parseInt(match[3], 10),
            parseInt(match[4], 10),
            parseInt(match[5], 10),
            0,
            0,
        );
        if (Number.isNaN(date.getTime())) return null;
        return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}`;
    },
};

const AutomationIconUtils = {
    parse(iconValue, iconColor = AUTOMATION_ICON_COLORS[0]) {
        const normalizedValue = String(iconValue || '').trim();
        const presetOption = AUTOMATION_ICON_OPTIONS.find((option) => option.id === normalizedValue)
            || AUTOMATION_ICON_OPTIONS.find((option) => option.id === AUTOMATION_DEFAULT_ICON_ID)
            || AUTOMATION_ICON_OPTIONS[0];
        const resolved = {
            type: 'preset',
            iconId: presetOption?.id || AUTOMATION_DEFAULT_ICON_ID,
            svg: presetOption?.svg || FALLBACK_AUTOMATION_SVG_ICON,
        };
        return {
            ...resolved,
            color: workspaceAutomationIconUtils.normalizeColor(iconColor, AUTOMATION_ICON_COLORS[0]),
        };
    },
    render(iconData, options = {}) {
        const size = options.size || 24;
        const svg = iconData?.svg || FALLBACK_AUTOMATION_SVG_ICON;
        const color = workspaceAutomationIconUtils.normalizeColor(iconData?.color, AUTOMATION_ICON_COLORS[0]);
        return `<span style="color:${color};width:${size}px;height:${size}px;display:inline-flex;">${svg}</span>`;
    },
    serialize(mode) {
        const iconData = getAutomationIconPicker(mode)?.getIconData?.();
        return iconData?.iconId || AUTOMATION_DEFAULT_ICON_ID;
    },
    selectedColor(mode) {
        return getAutomationIconPicker(mode)?.getIconData?.().color || AUTOMATION_ICON_COLORS[0];
    },
};

function getAutomationSkillLabel(skill) {
    if (!skill) return '';
    return String(skill.title || skill.name || '');
}

const DEFAULT_AUTOMATION_SKILL_ICON_COLOR = '#E53935';
const DEFAULT_AUTOMATION_SKILL_ICON_ID = 'tool';
const DEFAULT_AUTOMATION_SKILL_ICON_BODY = (typeof featureIconBodies !== 'undefined' ? featureIconBodies : Icons.featureIconBodies).skillDefault;
const DEFAULT_AUTOMATION_SKILL_ICON = Icons.wrapSvgBody(DEFAULT_AUTOMATION_SKILL_ICON_BODY, { width: '16', height: '16' });
const automationSkillIconUtils = window.WorkspaceIconUtils;
const AUTOMATION_SKILL_ICON_OPTIONS = automationSkillIconUtils.getWorkspaceIconOptions();

function sanitizeAutomationSkillColor(color) {
    if (typeof color !== 'string') return DEFAULT_AUTOMATION_SKILL_ICON_COLOR;
    const trimmed = color.trim();
    return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(trimmed) ? trimmed : DEFAULT_AUTOMATION_SKILL_ICON_COLOR;
}

function getAutomationSkillIconData(iconValue) {
    const resolved = automationSkillIconUtils.resolveWorkspaceStoredIcon(iconValue, {
        iconOptions: AUTOMATION_SKILL_ICON_OPTIONS,
        defaultIconId: DEFAULT_AUTOMATION_SKILL_ICON_ID,
        defaultColor: DEFAULT_AUTOMATION_SKILL_ICON_COLOR,
    });
    return {
        ...resolved,
        type: 'preset',
        iconId: resolved?.iconId || DEFAULT_AUTOMATION_SKILL_ICON_ID,
        svg: resolved?.svg || DEFAULT_AUTOMATION_SKILL_ICON,
        color: sanitizeAutomationSkillColor(resolved?.color),
    };
}

function renderAutomationSkillIconMarkup(iconData, size = 16) {
    return automationSkillIconUtils.renderWorkspaceIcon(iconData, {
        size,
        iconOptions: AUTOMATION_SKILL_ICON_OPTIONS,
        defaultIconId: DEFAULT_AUTOMATION_SKILL_ICON_ID,
    });
}

const AUTOMATION_FILE_ICON_FALLBACK = 'txt.svg';

const AutomationFileUtils = {
    formatSize(bytes) {
        if (!Number.isFinite(bytes) || bytes <= 0) return '—';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }
        return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
    },
    iconName(fileType) {
        if (typeof window.getFileIconForType === 'function') {
            const iconName = window.getFileIconForType(fileType);
            if (iconName && typeof iconName === 'string') return iconName;
        }
        return AUTOMATION_FILE_ICON_FALLBACK;
    },
    extension(filename) {
        if (typeof window.getFileExtensionLabel === 'function') {
            return window.getFileExtensionLabel(filename);
        }
        const parts = String(filename || '').split('.');
        return parts.length > 1 ? parts.pop().toUpperCase() : 'FILE';
    },
};

function upsertAutomationFileMeta(record) {
    if (!record || typeof record !== 'object') return null;
    const id = record.file_id || record.id;
    if (!id) return null;
    const normalized = {
        id,
        name: record.meta?.original_filename || record.file_name || record.fileName || record.name || `File ${id.slice(0, 6)}`,
        size: record.file_size ?? record.size ?? 0,
        type: record.file_type || record.type || '',
        category: record.file_category || record.category || 'document',
        created_at: record.created_at,
    };
    automationsFilesMetaMap.set(id, normalized);
    return normalized;
}

function ensureAutomationFileMeta(fileId) {
    if (!fileId) return null;
    if (automationsFilesMetaMap.has(fileId)) return automationsFilesMetaMap.get(fileId);
    const fallback = {
        id: fileId,
        name: `File ${fileId.slice(0, 6)}`,
        size: 0,
        type: '',
        category: 'document',
    };
    automationsFilesMetaMap.set(fileId, fallback);
    return fallback;
}

function recordAutomationFileMeta(mode, meta) {
    if (!meta || !meta.id) return;
    const state = getAutomationState(mode);
    if (!state.fileMetadata) state.fileMetadata = {};
    state.fileMetadata[meta.id] = meta;
    automationsFilesMetaMap.set(meta.id, meta);
}

function resolveAutomationFileMeta(fileId, mode) {
    if (!fileId) return null;
    const state = getAutomationState(mode);
    if (state.fileMetadata?.[fileId]) return state.fileMetadata[fileId];
    if (automationsFilesMetaMap.has(fileId)) return automationsFilesMetaMap.get(fileId);

    const fromCache = automationsFilesCache.find((file) => (file.file_id || file.id) === fileId);
    if (fromCache) {
        const meta = {
            id: fromCache.file_id || fromCache.id,
            name: fromCache.file_name || fromCache.meta?.original_filename || `File ${fileId.slice(0, 6)}`,
            size: fromCache.file_size,
            type: fromCache.file_type,
            category: fromCache.file_category,
            created_at: fromCache.created_at,
        };
        recordAutomationFileMeta(mode, meta);
        return meta;
    }

    return ensureAutomationFileMeta(fileId);
}

function addAutomationFileId(mode, fileId) {
    if (!fileId) return;
    const state = getAutomationState(mode);
    const unique = new Set(state.selectedFileIds || []);
    unique.add(fileId);
    state.selectedFileIds = Array.from(unique);
}

function removeAutomationFileId(mode, fileId) {
    if (!fileId) return;
    const state = getAutomationState(mode);
    state.selectedFileIds = (state.selectedFileIds || []).filter((id) => id !== fileId);
    if (state.fileMetadata) {
        delete state.fileMetadata[fileId];
    }
}

function setAutomationFilesLoading(mode, isLoading) {
    const state = getAutomationState(mode);
    state.isFilesLoading = Boolean(isLoading);
}

const automationFilesUIConfig = {
    create: {
        selectedContainer: automationFilesSelected,
        uploadBtn: automationFileUploadBtn,
        fileInput: automationFileInput,
        libraryBtn: automationFileLibraryBtn,
        dropdown: automationFileLibraryDropdown,
    },
    edit: {
        selectedContainer: automationEditFilesSelected,
        uploadBtn: automationEditFileUploadBtn,
        fileInput: automationEditFileInput,
        libraryBtn: automationEditFileLibraryBtn,
        dropdown: automationEditFileLibraryDropdown,
    },
};

function getAutomationFilesUI(mode) {
    return mode === 'edit' ? automationFilesUIConfig.edit : automationFilesUIConfig.create;
}

function renderAutomationFilesSelected(mode) {
    const ui = getAutomationFilesUI(mode);
    if (!ui.selectedContainer) return;
    const state = getAutomationState(mode);
    const fileIds = state.selectedFileIds || [];
    if (!fileIds.length) {
        ui.selectedContainer.innerHTML = `
            <div class="shared-files-placeholder">
                <span>${AutomationUtils.escapeHtml(automationT('automations_files_empty_optional', 'No files attached (optional)'))}</span>
            </div>
        `;
        return;
    }

    const chipsHtml = fileIds.map((fileId) => {
        const meta = resolveAutomationFileMeta(fileId, mode);
        const iconName = AutomationFileUtils.iconName(meta?.type);
        const extension = AutomationFileUtils.extension(meta?.name || meta?.id);
        const size = AutomationFileUtils.formatSize(meta?.size);
        const safeName = AutomationUtils.escapeHtml(meta?.name || fileId);
        return `
            <div class="shared-file-chip" data-file-id="${fileId}">
                <div class="shared-file-chip-icon">
                    <img src="/assets/file_svgs/${iconName}" alt="${extension}" width="24" height="24" loading="lazy">
                </div>
                <div class="shared-file-chip-body">
                    <p class="shared-file-chip-name" title="${safeName}">${safeName}</p>
                    <p class="shared-file-chip-meta">${size}</p>
                </div>
                <button type="button" class="shared-file-chip-remove" data-file-id="${fileId}" aria-label="${AutomationUtils.escapeHtml(automationT('automations_files_remove_file_aria', 'Remove file'))}">
                    ${Icons.close}
                </button>
            </div>
        `;
    }).join('');

    ui.selectedContainer.innerHTML = `<div class="shared-file-chip-list">${chipsHtml}</div>`;

    ui.selectedContainer.querySelectorAll('.shared-file-chip-remove').forEach((btn) => {
        btn.addEventListener('click', (event) => {
            event.stopPropagation();
            const fileId = btn.dataset.fileId;
            removeAutomationFileId(mode, fileId);
            renderAutomationFilesSelected(mode);
            renderAutomationFileLibrary(mode);
        });
    });
}

function toggleAutomationFileLibrary(mode, open) {
    const ui = getAutomationFilesUI(mode);
    if (!ui.libraryBtn || !ui.dropdown) {
        if (automationFileLibraryOpenMode === mode) {
            automationFileLibraryOpenMode = null;
        }
        return;
    }
    const shouldOpen = typeof open === 'boolean' ? open : (automationFileLibraryOpenMode !== mode);

    if (shouldOpen) {
        automationFileLibraryOpenMode = mode;
        ui.libraryBtn.setAttribute('aria-expanded', 'true');
        ui.dropdown.classList.add('open');
        renderAutomationFileLibrary(mode);
    } else {
        if (automationFileLibraryOpenMode === mode) {
            automationFileLibraryOpenMode = null;
        }
        ui.libraryBtn.setAttribute('aria-expanded', 'false');
        ui.dropdown.classList.remove('open');
    }
}

function renderAutomationFileLibrary(mode) {
    const ui = getAutomationFilesUI(mode);
    if (!ui.dropdown) return;
    const state = getAutomationState(mode);
    const searchValue = (state.fileLibrarySearch || '').trim().toLowerCase();
    const isLoading = state.isFilesLoading;
    const selected = new Set(state.selectedFileIds || []);

    let filteredFiles = automationsFilesCache;
    if (searchValue) {
        filteredFiles = automationsFilesCache.filter((file) => {
            const name = file.file_name || file.meta?.original_filename || file.id || '';
            return name.toLowerCase().includes(searchValue);
        });
    }

    const listHtml = filteredFiles.map((file) => {
        const fileId = file.file_id || file.id;
        const name = file.file_name || file.meta?.original_filename || fileId;
        const iconName = AutomationFileUtils.iconName(file.file_type);
        const extension = AutomationFileUtils.extension(name);
        const size = AutomationFileUtils.formatSize(file.file_size);
        const isSelected = selected.has(fileId);
        const escapedFileId = AutomationUtils.escapeHtml(String(fileId || ''));
        const escapedName = AutomationUtils.escapeHtml(name);
        const escapedIconName = AutomationUtils.escapeHtml(iconName);
        const escapedExtension = AutomationUtils.escapeHtml(extension);
        return `
            <button type="button" class="shared-file-library-item ${isSelected ? 'selected' : ''}" data-file-id="${escapedFileId}" aria-pressed="${isSelected ? 'true' : 'false'}" aria-label="${escapedName}">
                <span class="shared-file-library-item-icon">
                    <img src="/assets/file_svgs/${escapedIconName}" alt="${escapedExtension}" width="24" height="24" loading="lazy">
                </span>
                <span class="shared-file-library-item-body">
                    <span class="shared-file-library-item-name" title="${escapedName}">${escapedName}</span>
                    <span class="shared-file-library-item-meta">${size}</span>
                </span>
                <span class="shared-file-library-item-check">
                    ${Icons.check}
                </span>
            </button>
        `;
    }).join('');

    ui.dropdown.innerHTML = `
        <div class="shared-file-library-panel ${isLoading ? 'loading' : ''}">
            <div class="shared-file-library-header">
                <input
                    type="text"
                    class="shared-file-library-search"
                    placeholder="${AutomationUtils.escapeHtml(automationT('automations_files_search_placeholder', 'Search files...'))}"
                    value="${AutomationUtils.escapeHtml(state.fileLibrarySearch || '')}"
                    aria-label="${AutomationUtils.escapeHtml(automationT('automations_files_search_aria', 'Search files'))}"
                >
                <button type="button" class="shared-file-library-refresh" data-action="refresh" aria-label="${AutomationUtils.escapeHtml(automationT('automations_files_refresh_aria', 'Refresh files'))}" title="${AutomationUtils.escapeHtml(automationT('automations_files_refresh_aria', 'Refresh files'))}">
                    ${Icons.refresh}
                </button>
            </div>
            <div class="shared-file-library-content">
                ${filteredFiles.length === 0 ? `
                    <div class="shared-file-library-empty">
                        ${AutomationUtils.escapeHtml(automationsFilesCache.length === 0
                            ? automationT('automations_files_library_empty', 'No files uploaded yet')
                            : automationT('automations_files_library_no_match', 'No files match your search'))}
                    </div>
                ` : `<div class="shared-file-library-list">${listHtml}</div>`}
            </div>
        </div>
    `;

    const searchInput = ui.dropdown.querySelector('.shared-file-library-search');
    searchInput?.addEventListener('input', (event) => {
        state.fileLibrarySearch = event.target.value || '';
        renderAutomationFileLibrary(mode);
    });

    ui.dropdown.querySelector('[data-action="refresh"]')?.addEventListener('click', async () => {
        state.isFilesLoading = true;
        renderAutomationFileLibrary(mode);
        await loadAutomationFiles();
        state.isFilesLoading = false;
        renderAutomationFileLibrary(mode);
    });

    ui.dropdown.querySelectorAll('.shared-file-library-item').forEach((item) => {
        item.addEventListener('click', () => {
            const fileId = item.dataset.fileId;
            if (!fileId) return;
            if (selected.has(fileId)) {
                removeAutomationFileId(mode, fileId);
            } else {
                addAutomationFileId(mode, fileId);
            }
            renderAutomationFilesSelected(mode);
            renderAutomationFileLibrary(mode);
        });
    });
}

async function uploadAutomationFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    const response = await window.authedFetch('/api/v1/files/upload', {
        method: 'POST',
        headers: { 'Content-Type': null },
        body: formData,
    });

    let payload = null;
    try {
        payload = await response.json();
    } catch (_) {
        payload = null;
    }

    if (!response.ok || payload?.status !== 'success' || !payload?.file_id) {
        const detail = payload?.detail || payload?.message || automationFormatT('automations_files_upload_failed_status', 'Upload failed ({status})', { status: response.status });
        throw new Error(detail);
    }

    return payload;
}

async function handleAutomationFilesUpload(mode, fileList) {
    const files = Array.isArray(fileList) ? fileList : Array.from(fileList || []);
    if (!files.length) return;
    const ui = getAutomationFilesUI(mode);
    const fileInput = ui.fileInput;
    setAutomationFilesLoading(mode, true);
    renderAutomationFileLibrary(mode);
    try {
        for (const file of files) {
            try {
                const result = await uploadAutomationFile(file);
                const fileId = result.file_id;
                const meta = {
                    id: fileId,
                    name: file.name,
                    size: file.size,
                    type: file.type,
                    category: result.file_category,
                };
                recordAutomationFileMeta(mode, meta);
                addAutomationFileId(mode, fileId);
                if (result.already_uploaded) {
                    notifySuccess?.(automationT('automations_files_reuse_existing', 'File already uploaded, reusing existing copy'));
                }
            } catch (error) {
                console.error('Automation file upload failed', error);
                notifyError?.(error?.message || automationT('automations_files_upload_failed', 'Failed to upload file'));
            }
        }
        await loadAutomationFiles();
        renderAutomationFilesSelected(mode);
        renderAutomationFileLibrary(mode);
    } finally {
        setAutomationFilesLoading(mode, false);
        renderAutomationFileLibrary(mode);
        if (fileInput) {
            fileInput.value = '';
        }
    }
}

function closeAllAutomationFileLibraries() {
    toggleAutomationFileLibrary('create', false);
    toggleAutomationFileLibrary('edit', false);
}

function setupAutomationFilesUI(mode) {
    const ui = getAutomationFilesUI(mode);
    if (!ui) return;

    if (ui.uploadBtn && ui.uploadBtn.dataset.bound !== 'true') {
        ui.uploadBtn.addEventListener('click', (event) => {
            event.preventDefault();
            ui.fileInput?.click();
        });
        ui.uploadBtn.dataset.bound = 'true';
    }

    if (ui.fileInput && ui.fileInput.dataset.bound !== 'true') {
        ui.fileInput.addEventListener('change', (event) => {
            if (event.target?.files?.length) {
                handleAutomationFilesUpload(mode, event.target.files);
            }
        });
        ui.fileInput.dataset.bound = 'true';
    }

    if (ui.libraryBtn && ui.libraryBtn.dataset.bound !== 'true') {
        ui.libraryBtn.addEventListener('click', (event) => {
            event.preventDefault();
            toggleAutomationFileLibrary(mode);
        });
        ui.libraryBtn.dataset.bound = 'true';
    }
}

function initAutomationFilesUI() {
    setupAutomationFilesUI('create');
    setupAutomationFilesUI('edit');

    if (!automationFilesDropdownOutsideListenerBound) {
        document.addEventListener('click', (event) => {
            if (!automationFileLibraryOpenMode) return;
            const ui = getAutomationFilesUI(automationFileLibraryOpenMode);
            if (!ui || !ui.dropdown || !ui.libraryBtn) return;
            const target = event.target;
            if (ui.dropdown.contains(target) || ui.libraryBtn.contains(target)) {
                return;
            }
            toggleAutomationFileLibrary(automationFileLibraryOpenMode, false);
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && automationFileLibraryOpenMode) {
                toggleAutomationFileLibrary(automationFileLibraryOpenMode, false);
            }
        });

        automationFilesDropdownOutsideListenerBound = true;
    }
}

function getDefaultOneTimeLocalValue() {
    const now = new Date();
    const rounded = new Date(now.getTime() + DEFAULT_ONE_TIME_DELAY_MINUTES * 60 * 1000);
    rounded.setSeconds(0, 0);
    return `${rounded.getFullYear()}-${AutomationUtils.zeroPad(rounded.getMonth() + 1)}-${AutomationUtils.zeroPad(rounded.getDate())}T${AutomationUtils.zeroPad(rounded.getHours())}:${AutomationUtils.zeroPad(rounded.getMinutes())}`;
}

function createDefaultScheduleRule(type = 'recurring') {
    if (type === 'once') {
        return {
            type: 'once',
            run_at_local: getDefaultOneTimeLocalValue(),
            label: '',
        };
    }
    return {
        type: 'recurring',
        times: [DEFAULT_SCHEDULE_TIME],
        days: [...DEFAULT_SCHEDULE_DAYS],
        label: '',
    };
}

function normalizeScheduleRule(rule) {
    if (!rule || typeof rule !== 'object') {
        return createDefaultScheduleRule('recurring');
    }

    const rawType = String(rule.type || '').trim().toLowerCase();
    const isOneTime = rawType === 'once' || typeof rule.run_at === 'string' || typeof rule.run_at_local === 'string';
    if (isOneTime) {
        let runAtLocal = AutomationUtils.sanitizeDateTimeLocalValue(rule.run_at_local);
        if (!runAtLocal && typeof rule.run_at === 'string') {
            const parsedUtc = new Date(rule.run_at);
            if (!Number.isNaN(parsedUtc.getTime())) {
                runAtLocal = `${parsedUtc.getFullYear()}-${AutomationUtils.zeroPad(parsedUtc.getMonth() + 1)}-${AutomationUtils.zeroPad(parsedUtc.getDate())}T${AutomationUtils.zeroPad(parsedUtc.getHours())}:${AutomationUtils.zeroPad(parsedUtc.getMinutes())}`;
            }
        }
        return {
            type: 'once',
            run_at_local: runAtLocal || getDefaultOneTimeLocalValue(),
            label: typeof rule.label === 'string' ? rule.label : '',
        };
    }

    const normalizedTimes = [];
    if (Array.isArray(rule.times)) {
        rule.times.forEach((time) => {
            const sanitized = AutomationUtils.sanitizeTimeValue(time);
            if (sanitized && !normalizedTimes.includes(sanitized)) {
                normalizedTimes.push(sanitized);
            }
        });
    }

    if (!normalizedTimes.length) {
        normalizedTimes.push(DEFAULT_SCHEDULE_TIME);
    }

    const normalizedDays = Array.isArray(rule.days)
        ? rule.days
              .map((day) => parseInt(day, 10))
              .filter((day) => Number.isInteger(day) && day >= 0 && day <= 6)
        : [];
    const uniqueDays = [...new Set(normalizedDays)];
    if (!uniqueDays.length) {
        uniqueDays.push(...DEFAULT_SCHEDULE_DAYS);
    }

    return {
        type: 'recurring',
        times: normalizedTimes,
        days: uniqueDays,
        label: typeof rule.label === 'string' ? rule.label : '',
    };
}

function normalizeScheduleRules(rules) {
    if (!Array.isArray(rules) || !rules.length) {
        return [];
    }
    return rules.map((rule) => normalizeScheduleRule(rule));
}

function getAutomationScheduleTimezoneForSubmit(mode) {
    const state = getAutomationState(mode);
    const hasRecurringRule = normalizeScheduleRules(state.scheduleRules).some((rule) => (
        rule.type !== 'once'
        && Array.isArray(rule.days)
        && rule.days.length > 0
        && Array.isArray(rule.times)
        && rule.times.length > 0
    ));
    if (!hasRecurringRule) {
        return null;
    }
    if (typeof state.scheduleTimezone === 'string' && state.scheduleTimezone.trim()) {
        return state.scheduleTimezone.trim();
    }
    return getPreferredAutomationTimezone();
}

function convertStoredRulesToEditorRules(rules, scheduleTimezone) {
    const normalizedTimezone = typeof scheduleTimezone === 'string' ? scheduleTimezone.trim() : '';
    if (normalizedTimezone) {
        return normalizeScheduleRules(rules);
    }
    return convertUtcRulesToLocal(rules);
}


function jsDayToInternal(jsDay) {
    return (jsDay + 6) % 7;
}

function getUtcReferenceDate() {
    const now = new Date();
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 12, 0, 0, 0));
}

function convertUtcTimeToLocal(dayIndex, timeStr) {
    const sanitized = AutomationUtils.sanitizeTimeValue(timeStr);
    if (!sanitized || typeof dayIndex !== 'number') return null;
    const [hourStr, minuteStr] = sanitized.split(':');
    const base = getUtcReferenceDate();
    const currentDay = jsDayToInternal(base.getUTCDay());
    const diffDays = dayIndex - currentDay;
    const utcDate = new Date(base);
    utcDate.setUTCDate(base.getUTCDate() + diffDays);
    utcDate.setUTCHours(parseInt(hourStr, 10), parseInt(minuteStr, 10), 0, 0);
    return {
        day: jsDayToInternal(utcDate.getDay()),
        time: `${AutomationUtils.zeroPad(utcDate.getHours())}:${AutomationUtils.zeroPad(utcDate.getMinutes())}`,
    };
}

function convertLocalRulesToUtc(rules) {
    const normalized = normalizeScheduleRules(rules);
    if (!normalized.length) return [];
    const result = [];

    normalized.forEach((rule) => {
        if (rule.type === 'once') {
            const localValue = AutomationUtils.sanitizeDateTimeLocalValue(rule.run_at_local);
            if (!localValue) return;
            const localDate = new Date(localValue);
            if (Number.isNaN(localDate.getTime())) return;
            result.push({
                type: 'once',
                run_at: localDate.toISOString(),
                label: rule.label || undefined,
            });
            return;
        }

        result.push({
            days: Array.isArray(rule.days) ? [...rule.days].sort((a, b) => a - b) : [],
            times: Array.isArray(rule.times) ? [...rule.times].sort() : [],
            label: rule.label || undefined,
        });
    });
    return result;
}

function convertUtcRulesToLocal(rules) {
    const normalized = normalizeScheduleRules(rules);
    if (!normalized.length) return [];
    const oneTimeRules = [];
    const labelDayMap = new Map();

    normalized.forEach((rule) => {
        if (rule.type === 'once' || typeof rule.run_at === 'string') {
            const parsedUtc = new Date(rule.run_at);
            if (Number.isNaN(parsedUtc.getTime())) return;
            oneTimeRules.push({
                type: 'once',
                run_at_local: `${parsedUtc.getFullYear()}-${AutomationUtils.zeroPad(parsedUtc.getMonth() + 1)}-${AutomationUtils.zeroPad(parsedUtc.getDate())}T${AutomationUtils.zeroPad(parsedUtc.getHours())}:${AutomationUtils.zeroPad(parsedUtc.getMinutes())}`,
                label: typeof rule.label === 'string' ? rule.label : '',
            });
            return;
        }

        const labelKey = rule.label || '';
        if (!labelDayMap.has(labelKey)) {
            labelDayMap.set(labelKey, new Map());
        }
        const dayMap = labelDayMap.get(labelKey);
        rule.days.forEach((day) => {
            rule.times.forEach((time) => {
                const converted = convertUtcTimeToLocal(day, time);
                if (!converted) return;
                if (!dayMap.has(converted.day)) {
                    dayMap.set(converted.day, new Set());
                }
                dayMap.get(converted.day).add(converted.time);
            });
        });
    });

    const result = [];
    labelDayMap.forEach((dayMap, labelKey) => {
        const signatureMap = new Map();
        dayMap.forEach((timesSet, day) => {
            const sortedTimes = Array.from(timesSet).sort();
            const signature = `${labelKey}__${sortedTimes.join('|')}`;
            if (!signatureMap.has(signature)) {
                signatureMap.set(signature, {
                    label: labelKey || undefined,
                    days: [],
                    times: sortedTimes,
                });
            }
            signatureMap.get(signature).days.push(day);
        });
        signatureMap.forEach((ruleObj) => {
            ruleObj.days = Array.from(new Set(ruleObj.days)).sort((a, b) => a - b);
            result.push(ruleObj);
        });
    });

    return [...oneTimeRules, ...result];
}

function getAutomationIconPickerRefs(mode) {
    const isEdit = mode === 'edit';
    const trigger = isEdit ? automationEditIconButton : automationIconButton;
    const dropdown = isEdit ? automationEditIconDropdown : automationIconDropdown;
    return {
        picker: trigger?.closest('.svg-select'),
        trigger,
        preview: trigger,
        dropdown,
        svgGrid: isEdit ? automationEditIconGrid : automationIconGrid,
        colorGrid: isEdit ? automationEditColorRow : automationColorRow,
        saveButton: isEdit ? automationEditIconSaveBtn : automationIconSaveBtn,
        cancelButton: isEdit ? automationEditIconCancelBtn : automationIconCancelBtn,
    };
}

const AutomationCreateIconPicker = workspaceAutomationIconUtils.createWorkspaceIconPicker({
    state: AutomationState.create,
    refs: () => getAutomationIconPickerRefs('create'),
    iconOptions: AUTOMATION_ICON_OPTIONS,
    colors: AUTOMATION_PICKER_COLORS,
    defaultIconId: AUTOMATION_DEFAULT_ICON_ID,
    defaultColor: AUTOMATION_ICON_COLORS[0],
    translate: automationT,
    variant: 'svg-select',
});

const AutomationEditIconPicker = workspaceAutomationIconUtils.createWorkspaceIconPicker({
    state: AutomationState.edit,
    refs: () => getAutomationIconPickerRefs('edit'),
    iconOptions: AUTOMATION_ICON_OPTIONS,
    colors: AUTOMATION_PICKER_COLORS,
    defaultIconId: AUTOMATION_DEFAULT_ICON_ID,
    defaultColor: AUTOMATION_ICON_COLORS[0],
    translate: automationT,
    variant: 'svg-select',
});

/**
 * Return the shared icon-picker controller for an automation form.
 *
 * @param {'create'|'edit'} mode Automation form mode.
 * @returns {object|undefined} Shared workspace icon-picker controller.
 */
function getAutomationIconPicker(mode) {
    return mode === 'edit' ? AutomationEditIconPicker : AutomationCreateIconPicker;
}

/**
 * Reset one automation picker from the API's icon and color fields.
 *
 * @param {'create'|'edit'} mode Automation form mode.
 * @param {string} iconValue Stored preset ID.
 * @param {string} colorValue Stored icon color.
 */
function resetAutomationIconPicker(
    mode,
    iconValue = AUTOMATION_DEFAULT_ICON_ID,
    colorValue = AUTOMATION_ICON_COLORS[0],
) {
    const picker = getAutomationIconPicker(mode);
    picker?.reset?.(iconValue, colorValue);
    picker?.render?.();
    picker?.updatePreview?.();
}

function resetAutomationCreateState() {
    resetAutomationIconPicker('create');
    AutomationState.create.selectedModelId = null;
    AutomationState.create.modelSearchQuery = '';
    AutomationState.create.selectedMcpServerIds = [];
    AutomationState.create.availableConnections = [];
    AutomationState.create.connectionsLoading = false;
    AutomationState.create.connectionsError = false;
    AutomationState.create.scheduleRules = [];
    AutomationState.create.scheduleTimezone = getPreferredAutomationTimezone();
    AutomationState.create.selectedSkillId = null;
    AutomationState.create.selectedNoteIds = [];
    AutomationState.create.selectedFileIds = [];
    AutomationState.create.fileMetadata = {};
    AutomationState.create.fileLibrarySearch = '';
    AutomationState.create.isFilesLoading = false;
    AutomationState.create.isActive = true;
    AutomationState.create.triggerType = 'schedule';
    AutomationState.create.webhookTrigger = null;
    AutomationState.create.webhookSecret = null;
    AutomationState.create.webhookPayloadMode = 'append';
    AutomationState.create.webhookIncludeHeaders = false;

    if (automationNameInput) automationNameInput.value = '';
    if (automationPromptInput) automationPromptInput.value = '';
    if (automationActiveToggle) automationActiveToggle.checked = true;
    clearAutomationValidationErrors('create');

    renderAutomationScheduleRules('create');
    renderAutomationModelSelect('create');
    renderAutomationConnectionsSelect('create');
    renderAutomationSkillSelect('create');
    renderAutomationNotesSelect('create');
    renderAutomationFilesSelected('create');
    renderAutomationFileLibrary('create');
}

function initAutomationIconPickers() {
    [AutomationCreateIconPicker, AutomationEditIconPicker].forEach((picker) => {
        picker?.bind?.();
        picker?.render?.();
        picker?.updatePreview?.();
    });
}

// Model Select Functions
/**
 * Return whether a shared chat-model entry can execute as an automation.
 *
 * The shared model cache also contains custom agents. Automation persistence
 * and background jobs currently require a base Models row, so agents must not
 * be offered as selectable values in this feature-specific picker.
 */
function isAutomationEligibleModel(model) {
    return !!model
        && model.model_kind !== 'agent';
}

async function loadAutomationModels() {
    try {
        const models = typeof window.getCachedUserModels === 'function'
            ? await window.getCachedUserModels()
            : await (async () => {
                const res = await window.authedFetch('/api/v1/llm/models/user', { method: 'GET' });
                if (!res.ok) return null;
                return res.json();
            })();
        if (Array.isArray(models)) {
            // Preserve user-managed base models while removing only custom
            // agents, matching the backend automation information response.
            automationsModelsCache = models.filter(isAutomationEligibleModel);
        }
    } catch (e) {
        console.error('Failed to load models for automations', e);
    }
}

function getFilteredAutomationModels(mode) {
    const state = getAutomationState(mode);
    const query = (state.modelSearchQuery || '').trim().toLowerCase();
    if (!query) {
        return automationsModelsCache;
    }
    return automationsModelsCache.filter((model) => {
        const name = (model.name || '').toLowerCase();
        const modelId = (model.model_id || '').toLowerCase();
        return name.includes(query) || modelId.includes(query);
    });
}

function resetAutomationModelSearch(mode) {
    const state = getAutomationState(mode);
    if (!state) return;
    const hadQuery = !!state.modelSearchQuery;
    state.modelSearchQuery = '';
    const searchInput = document.getElementById(`automationModelSelectSearch${mode}`);
    if (searchInput) {
        searchInput.value = '';
    }
    if (hadQuery) {
        renderAutomationModelOptions(mode);
    }
}

function closeAutomationModelDropdown(mode) {
    const trigger = document.getElementById(`automationModelSelectTrigger${mode}`);
    const dropdown = document.getElementById(`automationModelSelectDropdown${mode}`);
    trigger?.classList.remove('open');
    trigger?.setAttribute('aria-expanded', 'false');
    dropdown?.classList.remove('open');
    resetAutomationModelSearch(mode);
}

function renderAutomationModelOptions(mode) {
    const listEl = document.getElementById(`automationModelSelectList${mode}`);
    if (!listEl) return;

    listEl.innerHTML = `
        <div class="shared-model-select-loading">
            <div class="shared-model-select-spinner"></div>
            <span>${AutomationUtils.escapeHtml(automationT('automations_model_loading', 'Loading models...'))}</span>
        </div>
    `;

    const state = getAutomationState(mode);

    if (!automationsModelsCache.length) {
        listEl.innerHTML = `<div class="shared-model-select-empty">${AutomationUtils.escapeHtml(automationT('automations_model_empty', 'No models available'))}</div>`;
        return;
    }

    const filtered = getFilteredAutomationModels(mode);
    if (!filtered.length) {
        listEl.innerHTML = `<div class="shared-model-select-empty">${AutomationUtils.escapeHtml(automationT('automations_model_no_match', 'No models match your search'))}</div>`;
        return;
    }

    listEl.innerHTML = filtered.map((m) => `
        <button type="button" role="option" aria-selected="${m.model_id === state.selectedModelId ? 'true' : 'false'}"
            tabindex="-1" class="shared-model-select-item ${m.model_id === state.selectedModelId ? 'selected' : ''}"
            data-model-id="${AutomationUtils.escapeHtml(m.model_id || '')}">
            <span class="shared-model-select-item-icon">${resolveAutomationModelIcon(m.model_icon)}</span>
            <span class="shared-model-select-item-name">${AutomationUtils.escapeHtml(m.name)}</span>
            <span class="shared-model-select-item-check">
                ${Icons.check}
            </span>
        </button>
    `).join('');

    listEl.querySelectorAll('.shared-model-select-item').forEach((item) => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const modelId = item.dataset.modelId || null;
            const modelChanged = modelId !== state.selectedModelId;
            state.selectedModelId = modelId;
            if (modelChanged) {
                // Selections are tied to a model's MCP policy. Clearing them
                // immediately avoids submitting stale IDs if the eligibility
                // refresh fails after a model switch.
                state.selectedMcpServerIds = [];
                state.availableConnections = [];
            }
            closeAutomationModelDropdown(mode);
            renderAutomationModelSelect(mode);
            // Connector eligibility is model-specific. Refresh immediately and
            // prune selections that the newly chosen model cannot use.
            void loadAutomationConnections(mode, { pruneUnavailable: true });
        });
    });
}

function renderAutomationModelSelect(mode) {
    const container = mode === 'edit' ? automationEditModelSelect : automationModelSelect;
    if (!container) return;

    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const selectedModel = automationsModelsCache.find(m => m.model_id === state.selectedModelId);

    container.innerHTML = window.CreateEditFormRenderer.renderSingleSelect({
        kind: 'model',
        triggerId: `automationModelSelectTrigger${mode}`,
        dropdownId: `automationModelSelectDropdown${mode}`,
        iconHtml: selectedModel ? resolveAutomationModelIcon(selectedModel.model_icon) : Icons?.omlorix || '',
        label: selectedModel?.name || automationT('automations_model_select_placeholder', 'Select a model...'),
        placeholder: !selectedModel,
        caretHtml: Icons.chevron,
        search: {
            id: `automationModelSelectSearch${mode}`,
            placeholder: automationT('automations_model_search_placeholder', 'Search models...'),
            value: state.modelSearchQuery || '',
        },
        listId: `automationModelSelectList${mode}`,
    });

    window.CreateEditFormRenderer.bindSingleSelect({
        container,
        triggerId: `automationModelSelectTrigger${mode}`,
        dropdownId: `automationModelSelectDropdown${mode}`,
        searchId: `automationModelSelectSearch${mode}`,
        onOpen: ({ searchInput }) => {
            renderAutomationModelOptions(mode);
            requestAnimationFrame(() => searchInput?.focus());
        },
        onClose: () => {
            resetAutomationModelSearch(mode);
        },
        onSearch: (value) => {
            state.modelSearchQuery = value;
            renderAutomationModelOptions(mode);
        },
    });

    renderAutomationModelOptions(mode);

    // Close on outside click
    if (!automationsModelSelectOutsideHandlerBound) {
        document.addEventListener('click', (e) => {
            const createContainer = automationModelSelect;
            const editContainer = automationEditModelSelect;
            const target = e.target;
            const insideCreate = createContainer?.contains(target);
            const insideEdit = editContainer?.contains(target);
            if (!insideCreate) closeAutomationModelDropdown('create');
            if (!insideEdit) closeAutomationModelDropdown('edit');
        }, { capture: true });
        automationsModelSelectOutsideHandlerBound = true;
    }
}

function resolveAutomationModelIcon(iconValue) {
    const fallback = (typeof Icons === 'object' && Icons?.omlorix) ? Icons.omlorix : '';
    if (typeof iconValue !== 'string') return fallback;
    const trimmed = iconValue.trim();
    if (!trimmed) return fallback;
    if (trimmed.startsWith('<')) return fallback;
    if (window.IconPicker?.renderIconMarkup) {
        return window.IconPicker.renderIconMarkup(trimmed, {
            fallback,
            imageAlt: 'Automation model icon',
        });
    }
    const mapped = Icons?.[trimmed];
    if (typeof mapped === 'string' && mapped.trim()) return mapped;
    return fallback;
}

// Skills and Notes API Functions
async function loadAutomationSkills() {
    try {
        const res = await window.authedFetch('/api/v1/skills', { method: 'GET' });
        if (!res.ok) return;
        const skills = await res.json();
        if (Array.isArray(skills)) {
            automationsSkillsCache = skills;
        }
    } catch (e) {
        console.error('Failed to load skills for automations', e);
    }
}

async function loadAutomationNotes() {
    try {
        const params = new URLSearchParams({
            limit: String(AUTOMATIONS_PAGE_LIMIT),
            offset: '0',
        });
        const res = await window.authedFetch(`/api/v1/notes/?${params.toString()}`, { method: 'GET' });
        if (!res.ok) return;
        const notes = unwrapAutomationsPage(await res.json());
        if (Array.isArray(notes)) {
            automationsNotesCache = notes;
        }
    } catch (e) {
        console.error('Failed to load notes for automations', e);
    }
}

async function loadAutomationFiles() {
    try {
        const params = new URLSearchParams({
            limit: String(AUTOMATIONS_PAGE_LIMIT),
            offset: '0',
            sort_field: 'name',
            sort_direction: 'asc',
        });
        const res = await window.authedFetch(`/api/v1/files/workspace?${params.toString()}`, { method: 'GET' });
        if (!res.ok) return;
        const payload = await res.json();
        const files = Array.isArray(payload?.items) ? payload.items : (Array.isArray(payload) ? payload : []);
        if (Array.isArray(files)) {
            automationsFilesCache = files.map((file) => {
                const fileId = file.file_id || file.id;
                const normalized = {
                    file_id: fileId,
                    file_size: file.file_size,
                    file_type: file.file_type,
                    file_category: file.file_category,
                    file_name: file.meta?.original_filename || file.file_name || file.name,
                    created_at: file.created_at,
                    meta: file.meta || {},
                };
                upsertAutomationFileMeta({
                    file_id: fileId,
                    file_name: normalized.file_name,
                    file_size: normalized.file_size,
                    file_type: normalized.file_type,
                    file_category: normalized.file_category,
                });
                return normalized;
            });
        }
    } catch (e) {
        console.error('Failed to load files for automations', e);
    }
}

function handleAutomationSkillSelectDocumentClick(e) {
    ['create', 'edit'].forEach((mode) => {
        const container = mode === 'edit' ? automationEditSkillSelect : automationSkillSelect;
        if (!container || container.contains(e.target)) return;
        const trigger = container.querySelector(`#automationSkillSelectTrigger${mode}`);
        trigger?.classList.remove('open');
        trigger?.setAttribute('aria-expanded', 'false');
        container.querySelector(`#automationSkillSelectDropdown${mode}`)?.classList.remove('open');
    });
}

function ensureAutomationSkillSelectOutsideHandler() {
    if (automationSkillSelectOutsideHandlerBound) return;
    document.addEventListener('click', handleAutomationSkillSelectDocumentClick);
    automationSkillSelectOutsideHandlerBound = true;
}

function handleAutomationNotesSelectDocumentClick(e) {
    ['create', 'edit'].forEach((mode) => {
        const container = mode === 'edit' ? automationEditNotesSelect : automationNotesSelect;
        if (!container || container.contains(e.target)) return;
        container.querySelector(`#automationNotesAddTrigger${mode}`)?.classList.remove('open');
        container.querySelector(`#automationNotesDropdown${mode}`)?.classList.remove('open');
    });
}

function ensureAutomationNotesSelectOutsideHandler() {
    if (automationNotesSelectOutsideHandlerBound) return;
    document.addEventListener('click', handleAutomationNotesSelectDocumentClick);
    automationNotesSelectOutsideHandlerBound = true;
}

function handleAutomationConnectionsSelectDocumentClick(e) {
    ['create', 'edit'].forEach((mode) => {
        const container = mode === 'edit' ? automationEditConnectionsSelect : automationConnectionsSelect;
        if (!container || container.contains(e.target)) return;
        const trigger = container.querySelector(`#automationConnectionsAddTrigger${mode}`);
        trigger?.classList.remove('open');
        trigger?.setAttribute('aria-expanded', 'false');
        container.querySelector(`#automationConnectionsDropdown${mode}`)?.classList.remove('open');
    });
}

function ensureAutomationConnectionsSelectOutsideHandler() {
    if (automationConnectionsSelectOutsideHandlerBound) return;
    document.addEventListener('click', handleAutomationConnectionsSelectDocumentClick);
    automationConnectionsSelectOutsideHandlerBound = true;
}

function cleanupAutomationSelectOutsideHandlers() {
    if (automationSkillSelectOutsideHandlerBound) {
        document.removeEventListener('click', handleAutomationSkillSelectDocumentClick);
        automationSkillSelectOutsideHandlerBound = false;
    }
    if (automationNotesSelectOutsideHandlerBound) {
        document.removeEventListener('click', handleAutomationNotesSelectDocumentClick);
        automationNotesSelectOutsideHandlerBound = false;
    }
    if (automationConnectionsSelectOutsideHandlerBound) {
        document.removeEventListener('click', handleAutomationConnectionsSelectDocumentClick);
        automationConnectionsSelectOutsideHandlerBound = false;
    }
}

// Skill Select Functions
const automationSkillSelect = document.getElementById('automationSkillSelect');
const automationEditSkillSelect = document.getElementById('automationEditSkillSelect');

function renderAutomationSkillSelect(mode) {
    const container = mode === 'edit' ? automationEditSkillSelect : automationSkillSelect;
    if (!container) return;

    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const selectedSkill = automationsSkillsCache.find(s => s.id === state.selectedSkillId);
    const defaultIconData = getAutomationSkillIconData(null);
    const selectedIconData = selectedSkill ? getAutomationSkillIconData(selectedSkill.icon) : defaultIconData;

    const skillOptionsHtml = automationsSkillsCache.map((skill) => {
        const iconData = getAutomationSkillIconData(skill.icon);
        const isSelected = skill.id === state.selectedSkillId;
        return `
            <button type="button" role="option" aria-selected="${isSelected ? 'true' : 'false'}" tabindex="-1"
                class="shared-skill-select-item ${isSelected ? 'selected' : ''}"
                data-skill-id="${AutomationUtils.escapeHtml(skill.id)}">
                <span class="shared-skill-select-item-icon" style="background-color: ${iconData.color}">${renderAutomationSkillIconMarkup(iconData, 16)}</span>
                <span class="shared-skill-select-item-name">${AutomationUtils.escapeHtml(getAutomationSkillLabel(skill))}</span>
                <span class="shared-skill-select-item-check">
                    ${Icons.check}
                </span>
            </button>
        `;
    }).join('');

    container.innerHTML = window.CreateEditFormRenderer.renderSingleSelect({
        kind: 'skill',
        triggerId: `automationSkillSelectTrigger${mode}`,
        dropdownId: `automationSkillSelectDropdown${mode}`,
        iconHtml: renderAutomationSkillIconMarkup(selectedIconData, 16),
        iconStyle: `background-color: ${selectedIconData.color}`,
        label: selectedSkill
            ? getAutomationSkillLabel(selectedSkill)
            : automationT('automations_skill_none_selected', 'No skill selected (optional)'),
        placeholder: !selectedSkill,
        caretHtml: Icons.chevron,
        bodyHtml: `
            <button type="button" role="option" aria-selected="${!state.selectedSkillId ? 'true' : 'false'}"
                tabindex="-1" class="shared-skill-select-item ${!state.selectedSkillId ? 'selected' : ''}" data-skill-id="">
                <span class="shared-skill-select-item-icon" style="background-color: ${defaultIconData.color}">${renderAutomationSkillIconMarkup(defaultIconData, 16)}</span>
                <span class="shared-skill-select-item-name">${AutomationUtils.escapeHtml(automationT('automations_skill_none', 'No skill'))}</span>
                <span class="shared-skill-select-item-check">
                    ${Icons.check}
                </span>
            </button>
            ${skillOptionsHtml}
        `,
    });

    const selectBinding = window.CreateEditFormRenderer.bindSingleSelect({
        container,
        triggerId: `automationSkillSelectTrigger${mode}`,
        dropdownId: `automationSkillSelectDropdown${mode}`,
    });

    container.querySelectorAll('.shared-skill-select-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const skillId = item.dataset.skillId || null;
            state.selectedSkillId = skillId;
            selectBinding.setOpen(false);
            renderAutomationSkillSelect(mode);
        });
    });

    ensureAutomationSkillSelectOutsideHandler();
}

// Notes Select Functions (Multi-select with chips)
const automationNotesSelect = document.getElementById('automationNotesSelect');
const automationEditNotesSelect = document.getElementById('automationEditNotesSelect');

function getAutomationNoteLabel(note) {
    const title = typeof note?.title === 'string' ? note.title.trim() : '';
    if (title) return title;

    // List responses intentionally omit full content. Keep this fallback for
    // callers that already hold a full note response, then use the lightweight
    // snippet before treating the note as empty.
    const content = typeof note?.content === 'string' ? note.content : '';
    const contentLabel = content
        .split('\n')
        .map(line => line.replace(/^#+\s*/, '').trim())
        .find(Boolean);
    if (contentLabel) return contentLabel;

    const snippet = typeof note?.snippet === 'string' ? note.snippet.trim() : '';
    return snippet || automationT('automations_notes_empty_note', 'Empty note');
}

function renderAutomationNotesSelect(mode) {
    const container = mode === 'edit' ? automationEditNotesSelect : automationNotesSelect;
    if (!container) return;

    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const selectedNoteIds = state.selectedNoteIds || [];
    const selectedNotes = automationsNotesCache.filter(n => selectedNoteIds.includes(n.id));

    const noteIcon = Icons.notes_management;

    container.innerHTML = `
        <div class="automations-notes-selected-chips">
            ${selectedNotes.length === 0 ? `
                <span class="automations-notes-placeholder">${AutomationUtils.escapeHtml(automationT('automations_notes_none_selected', 'No notes selected (optional)'))}</span>
            ` : selectedNotes.map(note => `
                <span class="automations-notes-chip" data-note-id="${note.id}">
                    ${noteIcon}
                    <span class="automations-notes-chip-text">${AutomationUtils.escapeHtml(getAutomationNoteLabel(note))}</span>
                    <button type="button" class="automations-notes-chip-remove" data-note-id="${note.id}" aria-label="${AutomationUtils.escapeHtml(automationT('automations_notes_remove_aria', 'Remove note'))}">
                        ${Icons.close}
                    </button>
                </span>
            `).join('')}
        </div>
        <div class="automations-notes-add-section">
            <button type="button" class="automations-notes-add-trigger" id="automationNotesAddTrigger${mode}">
                ${Icons.plus}
                ${AutomationUtils.escapeHtml(automationT('automations_notes_add', 'Add note'))}
            </button>
            <div class="automations-notes-dropdown" id="automationNotesDropdown${mode}">
                ${automationsNotesCache.length === 0 ? `
                    <div class="automations-notes-dropdown-empty">${AutomationUtils.escapeHtml(automationT('automations_notes_empty', 'No notes available'))}</div>
                ` : automationsNotesCache.map(n => `
                    <div class="automations-notes-dropdown-item ${selectedNoteIds.includes(n.id) ? 'selected' : ''}" data-note-id="${n.id}">
                        <span class="automations-notes-dropdown-item-icon">${noteIcon}</span>
                        <span class="automations-notes-dropdown-item-text">${AutomationUtils.escapeHtml(getAutomationNoteLabel(n))}</span>
                        <span class="automations-notes-dropdown-item-check">
                            ${Icons.check}
                        </span>
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    // Remove chip handlers
    container.querySelectorAll('.automations-notes-chip-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const noteId = btn.dataset.noteId;
            state.selectedNoteIds = state.selectedNoteIds.filter(id => id !== noteId);
            renderAutomationNotesSelect(mode);
        });
    });

    // Add note trigger
    const addTrigger = container.querySelector(`#automationNotesAddTrigger${mode}`);
    const dropdown = container.querySelector(`#automationNotesDropdown${mode}`);

    addTrigger?.addEventListener('click', (e) => {
        e.stopPropagation();
        addTrigger.classList.toggle('open');
        dropdown?.classList.toggle('open');
    });

    // Dropdown item selection
    container.querySelectorAll('.automations-notes-dropdown-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const noteId = item.dataset.noteId;
            if (state.selectedNoteIds.includes(noteId)) {
                state.selectedNoteIds = state.selectedNoteIds.filter(id => id !== noteId);
            } else {
                state.selectedNoteIds = [...state.selectedNoteIds, noteId];
            }
            renderAutomationNotesSelect(mode);
        });
    });

    ensureAutomationNotesSelectOutsideHandler();
}

/** Render the same provider artwork used by connection cards and chat mentions. */
function resolveAutomationConnectionIcon(connection) {
    const iconsMap = typeof Icons !== 'undefined' && Icons ? Icons : (window.Icons || {});
    const fallback = typeof iconsMap.server === 'string' ? iconsMap.server : '';
    const providerIconKey = typeof iconsMap.getConnectionProviderIconKey === 'function'
        ? iconsMap.getConnectionProviderIconKey(connection?.provider)
        : '';
    const iconValue = providerIconKey || connection?.icon || '';
    if (typeof window.IconPicker?.renderIconMarkup === 'function') {
        return window.IconPicker.renderIconMarkup(iconValue, { fallback }) || fallback;
    }
    return providerIconKey && typeof iconsMap[providerIconKey] === 'string'
        ? iconsMap[providerIconKey]
        : fallback;
}

/** Load every connector eligible for the form's selected model. */
async function loadAutomationConnections(mode, { pruneUnavailable = false } = {}) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const modelId = String(state.selectedModelId || '').trim();
    if (!modelId) {
        state.availableConnections = [];
        state.connectionsLoading = false;
        state.connectionsError = false;
        if (pruneUnavailable) state.selectedMcpServerIds = [];
        renderAutomationConnectionsSelect(mode);
        return;
    }

    state.connectionsLoading = true;
    state.connectionsError = false;
    renderAutomationConnectionsSelect(mode);
    try {
        const params = new URLSearchParams({ model_id: modelId });
        const response = await window.authedFetch(`/api/v1/llm/mcp/connectors/mentions?${params.toString()}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        // Ignore a slow response for a model the user has already replaced.
        if (String(state.selectedModelId || '') !== modelId) return;
        state.availableConnections = Array.isArray(payload) ? payload : [];
        if (pruneUnavailable) {
            const eligibleIds = new Set(state.availableConnections.map((connection) => String(connection.id || '')));
            state.selectedMcpServerIds = state.selectedMcpServerIds.filter((serverId) => eligibleIds.has(serverId));
        }
    } catch (error) {
        if (String(state.selectedModelId || '') !== modelId) return;
        console.error('Failed to load connections for automation', error);
        // Keep the last successful metadata so existing selections remain
        // visible and removable while a transient refresh is retried.
        state.connectionsError = true;
    } finally {
        if (String(state.selectedModelId || '') === modelId) {
            state.connectionsLoading = false;
            renderAutomationConnectionsSelect(mode);
        }
    }
}

/** Render an accessible connection multi-select with removable provider chips. */
function renderAutomationConnectionsSelect(mode) {
    const container = mode === 'edit' ? automationEditConnectionsSelect : automationConnectionsSelect;
    if (!container) return;
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const selectedIds = Array.isArray(state.selectedMcpServerIds) ? state.selectedMcpServerIds : [];
    const connections = Array.isArray(state.availableConnections) ? state.availableConnections : [];
    const selectedConnections = connections.filter((connection) => selectedIds.includes(String(connection.id || '')));
    const hasModel = Boolean(state.selectedModelId);

    const placeholder = !hasModel
        ? automationT('automations_connections_select_model_first', 'Select a model to choose connections')
        : state.connectionsLoading
            ? automationT('automations_connections_loading', 'Loading connections...')
            : automationT('automations_connections_none_selected', 'No connections selected (optional)');
    const emptyLabel = state.connectionsLoading
        ? automationT('automations_connections_loading', 'Loading connections...')
        : state.connectionsError
            ? automationT('automations_connections_load_error', 'Connections could not be loaded. Try again.')
            : automationT('automations_connections_empty', 'No connections available for this model');
    const dropdownId = `automationConnectionsDropdown${mode}`;

    container.innerHTML = `
        <div class="automations-connections-selected-chips" aria-live="polite">
            ${selectedConnections.length === 0 ? `
                <span class="automations-connections-placeholder">${AutomationUtils.escapeHtml(placeholder)}</span>
            ` : selectedConnections.map((connection) => `
                <span class="automations-connection-chip" data-mcp-server-id="${AutomationUtils.escapeHtml(connection.id)}">
                    <span class="automations-connection-provider-icon" aria-hidden="true">${resolveAutomationConnectionIcon(connection)}</span>
                    <span class="automations-connection-chip-text">${AutomationUtils.escapeHtml(connection.name || '')}</span>
                    <button type="button" class="automations-connection-chip-remove" data-mcp-server-id="${AutomationUtils.escapeHtml(connection.id)}" aria-label="${AutomationUtils.escapeHtml(automationFormatT('automations_connections_remove_aria', 'Remove {name}', { name: connection.name || '' }))}">
                        ${Icons.close}
                    </button>
                </span>
            `).join('')}
        </div>
        <div class="automations-connections-add-section">
            <button type="button" class="automations-connections-add-trigger" id="automationConnectionsAddTrigger${mode}" aria-expanded="false" aria-haspopup="true" aria-controls="${dropdownId}" ${!hasModel || state.connectionsLoading ? 'disabled' : ''}>
                ${Icons.plus}
                ${AutomationUtils.escapeHtml(automationT('automations_connections_add', 'Add connection'))}
            </button>
            <div class="automations-connections-dropdown" id="${dropdownId}" role="group" aria-label="${AutomationUtils.escapeHtml(automationT('automations_create_connections_label', 'Connections'))}">
                ${connections.length === 0 ? `
                    <div class="automations-connections-dropdown-empty">${AutomationUtils.escapeHtml(emptyLabel)}</div>
                ` : connections.map((connection) => {
                    const serverId = String(connection.id || '');
                    const selected = selectedIds.includes(serverId);
                    return `
                        <button type="button" class="automations-connections-dropdown-item ${selected ? 'selected' : ''}" data-mcp-server-id="${AutomationUtils.escapeHtml(serverId)}" aria-pressed="${selected ? 'true' : 'false'}">
                            <span class="automations-connection-provider-icon" aria-hidden="true">${resolveAutomationConnectionIcon(connection)}</span>
                            <span class="automations-connections-dropdown-item-copy">
                                <span class="automations-connections-dropdown-item-name">${AutomationUtils.escapeHtml(connection.name || '')}</span>
                                <span class="automations-connections-dropdown-item-description">${AutomationUtils.escapeHtml(connection.description || automationT('automations_connections_item_description', 'Allow this connection for each automation run'))}</span>
                            </span>
                            <span class="automations-connections-dropdown-item-check" aria-hidden="true">${Icons.check}</span>
                        </button>
                    `;
                }).join('')}
            </div>
        </div>
    `;

    container.querySelectorAll('.automations-connection-chip-remove').forEach((button) => {
        button.addEventListener('click', () => {
            const serverId = button.dataset.mcpServerId;
            state.selectedMcpServerIds = selectedIds.filter((id) => id !== serverId);
            renderAutomationConnectionsSelect(mode);
        });
    });

    const addTrigger = container.querySelector(`#automationConnectionsAddTrigger${mode}`);
    const dropdown = container.querySelector(`#automationConnectionsDropdown${mode}`);
    addTrigger?.addEventListener('click', (event) => {
        event.stopPropagation();
        const willOpen = !addTrigger.classList.contains('open');
        addTrigger.classList.toggle('open', willOpen);
        addTrigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        dropdown?.classList.toggle('open', willOpen);
    });
    container.querySelectorAll('.automations-connections-dropdown-item').forEach((item) => {
        item.addEventListener('click', (event) => {
            event.stopPropagation();
            const serverId = item.dataset.mcpServerId;
            state.selectedMcpServerIds = selectedIds.includes(serverId)
                ? selectedIds.filter((id) => id !== serverId)
                : [...selectedIds, serverId];
            renderAutomationConnectionsSelect(mode);
        });
    });
    ensureAutomationConnectionsSelectOutsideHandler();
}

// Schedule Rules Functions
function getAutomationScheduleType(mode) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const firstRule = normalizeScheduleRules(state.scheduleRules || [])[0];
    return firstRule?.type === 'once' ? 'once' : 'recurring';
}

function setAutomationScheduleType(mode, type) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const normalizedType = type === 'once' ? 'once' : 'recurring';
    const currentType = getAutomationScheduleType(mode);
    if (currentType === normalizedType && Array.isArray(state.scheduleRules) && state.scheduleRules.length) {
        return;
    }
    state.scheduleRules = [createDefaultScheduleRule(normalizedType)];
    renderAutomationScheduleRules(mode, { preserveScroll: true });
}

function getAutomationScrollableAncestors(element) {
    const ancestors = [];
    let current = element?.parentElement;

    // The automation form may be scrolled by the window, by a workspace pane,
    // or by a future modal/container. Capturing every scrollable ancestor keeps
    // trigger re-renders stable no matter which container owns the scroll.
    while (current && current !== document.body && current !== document.documentElement) {
        const style = window.getComputedStyle(current);
        const canScrollY = /(auto|scroll|overlay)/.test(style.overflowY || style.overflow || '');
        if (canScrollY && current.scrollHeight > current.clientHeight) {
            ancestors.push(current);
        }
        current = current.parentElement;
    }

    return ancestors;
}

function captureAutomationScrollSnapshot(container) {
    const ancestors = getAutomationScrollableAncestors(container);
    return {
        windowX: window.scrollX,
        windowY: window.scrollY,
        ancestors: ancestors.map((element) => ({
            element,
            left: element.scrollLeft,
            top: element.scrollTop,
        })),
    };
}

function restoreAutomationScrollSnapshot(snapshot) {
    if (!snapshot) return;
    snapshot.ancestors.forEach(({ element, left, top }) => {
        element.scrollLeft = left;
        element.scrollTop = top;
    });
    window.scrollTo(snapshot.windowX, snapshot.windowY);
}

function focusAutomationElementWithoutScroll(element) {
    if (!element) return;
    try {
        element.focus({ preventScroll: true });
    } catch (_) {
        element.focus();
    }
}

function renderAutomationScheduleRules(mode, options = {}) {
    const container = mode === 'edit' ? automationEditScheduleRules : automationScheduleRules;
    if (!container) return;
    const scrollSnapshot = options.preserveScroll ? captureAutomationScrollSnapshot(container) : null;

    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const triggerType = state.triggerType === 'webhook' ? 'webhook' : 'schedule';
    const triggerTabScheduleId = `automationTriggerTab-${mode}-schedule`;
    const triggerTabWebhookId = `automationTriggerTab-${mode}-webhook`;
    const triggerPanelScheduleId = `automationTriggerPanel-${mode}-schedule`;
    const triggerPanelWebhookId = `automationTriggerPanel-${mode}-webhook`;
    const selectedTriggerTabId = triggerType === 'webhook' ? triggerTabWebhookId : triggerTabScheduleId;
    const selectedTriggerPanelId = triggerType === 'webhook' ? triggerPanelWebhookId : triggerPanelScheduleId;
    const normalizedRules = normalizeScheduleRules(state.scheduleRules || []);
    const scheduleType = normalizedRules[0]?.type === 'once' ? 'once' : 'recurring';
    if (mode === 'edit') {
        AutomationState.edit.scheduleRules = normalizedRules;
    } else {
        AutomationState.create.scheduleRules = normalizedRules;
    }

    // Inline icons used inside the trigger-type segmented control. Kept as
    // template strings so they inherit currentColor and stay theme-aware.
    const scheduleTabIcon = Icons.clock;
    const webhookTabIcon =  Icons.lightning;

    container.innerHTML = `
        <div class="automations-trigger-editor">
            <div class="automations-trigger-tabs" role="tablist" aria-label="${AutomationUtils.escapeHtml(automationT('automations_trigger_type_aria', 'Trigger type'))}">
                <button type="button" id="${triggerTabScheduleId}" class="automations-trigger-tab ${triggerType === 'schedule' ? 'active' : ''}" data-trigger-type="schedule" role="tab" aria-selected="${triggerType === 'schedule' ? 'true' : 'false'}" tabindex="${triggerType === 'schedule' ? '0' : '-1'}" aria-controls="${triggerPanelScheduleId}">${scheduleTabIcon}<span>${AutomationUtils.escapeHtml(automationT('automations_trigger_type_schedule', 'Schedule'))}</span></button>
                <button type="button" id="${triggerTabWebhookId}" class="automations-trigger-tab ${triggerType === 'webhook' ? 'active' : ''}" data-trigger-type="webhook" role="tab" aria-selected="${triggerType === 'webhook' ? 'true' : 'false'}" tabindex="${triggerType === 'webhook' ? '0' : '-1'}" aria-controls="${triggerPanelWebhookId}">${webhookTabIcon}<span>${AutomationUtils.escapeHtml(automationT('automations_trigger_type_webhook', 'Webhook'))}</span></button>
            </div>
            <div class="automations-trigger-panel" id="${selectedTriggerPanelId}" role="tabpanel" aria-labelledby="${selectedTriggerTabId}">
            ${triggerType === 'webhook' ? renderAutomationWebhookEditor(mode) : `
            <div class="automations-schedule-type-toggle" role="tablist" aria-label="${AutomationUtils.escapeHtml(automationT('automations_schedule_type_aria', 'Schedule type'))}">
                <button type="button" class="automations-schedule-type-btn ${scheduleType === 'recurring' ? 'active' : ''}" data-type="recurring" role="tab" aria-selected="${scheduleType === 'recurring' ? 'true' : 'false'}">${AutomationUtils.escapeHtml(automationT('automations_schedule_type_recurring', 'Recurring'))}</button>
                <button type="button" class="automations-schedule-type-btn ${scheduleType === 'once' ? 'active' : ''}" data-type="once" role="tab" aria-selected="${scheduleType === 'once' ? 'true' : 'false'}">${AutomationUtils.escapeHtml(automationT('automations_schedule_type_once', 'Run once'))}</button>
            </div>
            ${scheduleType === 'once' ? renderAutomationOneTimeScheduleEditor(normalizedRules[0] || createDefaultScheduleRule('once'), mode) : `
                <div class="automations-schedule-presets">
                    <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_schedule_presets_label', 'Quick presets'))}</span>
                    <div class="automations-schedule-presets-list">
                        <button type="button" class="automations-schedule-preset-btn" data-preset="morning">${AutomationUtils.escapeHtml(automationT('automations_schedule_preset_morning', 'Morning (7 AM)'))}</button>
                        <button type="button" class="automations-schedule-preset-btn" data-preset="business">${AutomationUtils.escapeHtml(automationT('automations_schedule_preset_business', 'Business hours'))}</button>
                        <button type="button" class="automations-schedule-preset-btn" data-preset="evening">${AutomationUtils.escapeHtml(automationT('automations_schedule_preset_evening', 'Evening (6 PM)'))}</button>
                    </div>
                </div>
                <div class="automations-schedule-rules-list">
                    ${normalizedRules.length === 0 ? `
                        <div class="automations-schedule-rules-empty">
                            ${AutomationUtils.escapeHtml(automationT('automations_schedule_rules_empty', 'No schedule rules defined. Add a time to tell the assistant when to run this automation.'))}
                        </div>
                    ` : normalizedRules.map((rule, index) => renderAutomationScheduleRuleItem(rule, index, mode)).join('')}
                </div>
                <button type="button" class="automations-schedule-add-btn" id="automationAddScheduleRule${mode}">
                    ${Icons.plus}
                    ${AutomationUtils.escapeHtml(automationT('automations_schedule_add_rule', 'Add schedule rule'))}
                </button>
            `}
            `}
            </div>
        </div>
    `;
    restoreAutomationScrollSnapshot(scrollSnapshot);
    requestAnimationFrame(() => restoreAutomationScrollSnapshot(scrollSnapshot));

    const setTriggerType = (nextType) => {
        const triggerScrollSnapshot = captureAutomationScrollSnapshot(container);
        clearAutomationFieldError(mode, 'schedule');
        state.triggerType = nextType === 'webhook' ? 'webhook' : 'schedule';
        renderAutomationScheduleRules(mode, { preserveScroll: true });
        requestAnimationFrame(() => {
            restoreAutomationScrollSnapshot(triggerScrollSnapshot);
            focusAutomationElementWithoutScroll(container.querySelector(`[data-trigger-type="${state.triggerType}"]`));
        });
    };

    container.querySelectorAll('[data-trigger-type]').forEach((btn) => {
        btn.addEventListener('click', () => {
            setTriggerType(btn.dataset.triggerType);
        });
        btn.addEventListener('keydown', (event) => {
            const key = event.key;
            if (key === 'ArrowLeft' || key === 'ArrowRight' || key === 'Home' || key === 'End') {
                event.preventDefault();
                const nextType = key === 'ArrowRight' || key === 'End' ? 'webhook' : 'schedule';
                setTriggerType(nextType);
            }
            if (key === 'Enter' || key === ' ') {
                event.preventDefault();
                setTriggerType(btn.dataset.triggerType);
            }
        });
    });

    if (triggerType === 'webhook') {
        bindAutomationWebhookEditor(mode, container);
        return;
    }

    container.querySelectorAll('.automations-schedule-type-btn').forEach((btn) => {
        if (btn.dataset.triggerType) return;
        btn.addEventListener('click', () => {
            const nextType = btn.dataset.type === 'once' ? 'once' : 'recurring';
            clearAutomationFieldError(mode, 'schedule');
            setAutomationScheduleType(mode, nextType);
        });
    });

    if (scheduleType === 'once') {
        const oneTimeInput = container.querySelector('.automations-once-datetime-input');
        const labelInput = container.querySelector('.automations-schedule-label-input');
        oneTimeInput?.addEventListener('change', (e) => {
            const value = AutomationUtils.sanitizeDateTimeLocalValue(e.target.value);
            if (!value) return;
            clearAutomationFieldError(mode, 'schedule');
            state.scheduleRules = [{
                ...createDefaultScheduleRule('once'),
                ...(state.scheduleRules?.[0] || {}),
                type: 'once',
                run_at_local: value,
            }];
        });
        labelInput?.addEventListener('input', (e) => {
            clearAutomationFieldError(mode, 'schedule');
            state.scheduleRules = [{
                ...createDefaultScheduleRule('once'),
                ...(state.scheduleRules?.[0] || {}),
                type: 'once',
                label: e.target.value || '',
            }];
        });
        return;
    }

    // Preset buttons
    container.querySelectorAll('.automations-schedule-preset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const preset = btn.dataset.preset;
            addAutomationSchedulePreset(mode, preset);
        });
    });

    // Add rule button
    container.querySelector(`#automationAddScheduleRule${mode}`)?.addEventListener('click', () => {
        addAutomationScheduleRule(mode);
    });

    // Day buttons and inputs
    container.querySelectorAll('.automations-schedule-rule-item').forEach((item, index) => {
        item.querySelectorAll('.automations-schedule-day-btn').forEach(dayBtn => {
            dayBtn.addEventListener('click', () => {
                const dayIndex = parseInt(dayBtn.dataset.day);
                toggleAutomationScheduleDay(mode, index, dayIndex);
            });
        });

        const timeInput = item.querySelector('.automations-schedule-time-input');
        const addTimeBtn = item.querySelector('.automations-schedule-time-add-btn');
        const labelInput = item.querySelector('.automations-schedule-label-input');

        addTimeBtn?.addEventListener('click', () => {
            if (!timeInput) return;
            const sanitized = AutomationUtils.sanitizeTimeValue(timeInput.value);
            if (!sanitized) {
                notifyError?.(automationT('automations_schedule_error_valid_time', 'Enter a valid time (HH:MM)'));
                return;
            }
            addAutomationScheduleTime(mode, index, sanitized);
            timeInput.value = '';
        });

        timeInput?.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                clearAutomationFieldError(mode, 'schedule');
                addTimeBtn?.click();
            }
        });

        item.querySelectorAll('.automations-schedule-time-chip button').forEach(btn => {
            btn.addEventListener('click', () => {
                const timeValue = btn.dataset.time;
                removeAutomationScheduleTime(mode, index, timeValue);
            });
        });

        labelInput?.addEventListener('input', (e) => updateAutomationScheduleField(mode, index, 'label', e.target.value));

        item.querySelector('.automations-schedule-remove-btn')?.addEventListener('click', () => {
            removeAutomationScheduleRule(mode, index);
        });
    });
}

function renderAutomationWebhookEditor(mode) {
    const state = getAutomationState(mode);
    const trigger = state.webhookTrigger;
    const hasTrigger = Boolean(trigger?.url);
    const isReserved = mode === 'create' && Boolean(trigger?.reservation_token);
    const isEnabled = Boolean(trigger?.is_enabled);

    // Reusable copy icon for the inline copy-to-clipboard buttons.
    const copyIcon = Icons.copy;

    // Secret is only available right after creation/rotation. Render its row
    // plus a one-time copy reminder only while we still hold the value.
    const secretBlock = state.webhookSecret ? `
        <div class="automations-webhook-row">
            <span class="automations-webhook-field-label">${AutomationUtils.escapeHtml(automationT('automations_webhook_secret_label', 'Secret'))}</span>
            <code class="automations-webhook-value">${AutomationUtils.escapeHtml(state.webhookSecret)}</code>
            <button type="button" class="automations-webhook-copy-btn" data-webhook-action="copy-secret" aria-label="${AutomationUtils.escapeHtml(automationT('automations_webhook_copy_secret', 'Copy secret'))}" data-tooltip="${AutomationUtils.escapeHtml(automationT('automations_webhook_copy_secret', 'Copy secret'))}">${copyIcon}</button>
        </div>
        <p class="automations-webhook-secret-note">
            ${Icons.warning}
            ${AutomationUtils.escapeHtml(automationT('automations_webhook_secret_once_note', "Copy this secret now \u2014 it won't be shown again."))}
        </p>
    ` : '';

    const curlExample = hasTrigger ? `curl -X POST "${trigger.url}" \\\n  -H "Authorization: Bearer ${state.webhookSecret || '<secret>'}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"message":"Hello from webhook"}'` : '';

    return `
        <div class="automations-webhook-editor">
            <p class="automations-once-hint">
                ${Icons.info}
                ${AutomationUtils.escapeHtml(automationT('automations_webhook_hint', 'Run this automation from external tools by sending an HTTPS POST request to its webhook URL.'))}
            </p>

            <div class="automations-webhook-section">
                <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_webhook_settings_label', 'Settings'))}</span>
                <div class="automations-webhook-setting">
                    <div class="automations-webhook-setting-text">
                        <span class="automations-webhook-setting-title">${AutomationUtils.escapeHtml(automationT('automations_webhook_payload_mode', 'Payload mode'))}</span>
                        <span class="automations-webhook-setting-desc">${AutomationUtils.escapeHtml(automationT('automations_webhook_payload_mode_desc', 'Choose how the request body is included in the prompt.'))}</span>
                    </div>
                    <select class="automations-webhook-select" data-webhook-field="payloadMode" aria-label="${AutomationUtils.escapeHtml(automationT('automations_webhook_payload_mode', 'Payload mode'))}">
                        <option value="append" ${state.webhookPayloadMode === 'append' ? 'selected' : ''}>${AutomationUtils.escapeHtml(automationT('automations_webhook_payload_append', 'Append payload to prompt'))}</option>
                        <option value="template" ${state.webhookPayloadMode === 'template' ? 'selected' : ''}>${AutomationUtils.escapeHtml(automationT('automations_webhook_payload_template', 'Use template variables'))}</option>
                        <option value="ignore" ${state.webhookPayloadMode === 'ignore' ? 'selected' : ''}>${AutomationUtils.escapeHtml(automationT('automations_webhook_payload_ignore', 'Ignore payload'))}</option>
                    </select>
                </div>
                <div class="automations-webhook-setting">
                    <label class="automations-webhook-setting-text" for="automationWebhookHeaders-${mode}">
                        <span class="automations-webhook-setting-title">${AutomationUtils.escapeHtml(automationT('automations_webhook_include_headers', 'Allow selected request headers in context'))}</span>
                        <span class="automations-webhook-setting-desc">${AutomationUtils.escapeHtml(automationT('automations_webhook_include_headers_desc', 'Pass a safe subset of request headers to the model.'))}</span>
                    </label>
                    <label class="toggle-switch">
                        <input type="checkbox" id="automationWebhookHeaders-${mode}" class="toggle-input" data-webhook-field="includeHeaders" ${state.webhookIncludeHeaders ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>

            ${hasTrigger ? `
                <div class="automations-webhook-section automations-webhook-endpoint">
                    <div class="automations-webhook-section-head">
                        <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_webhook_endpoint_label', 'Endpoint'))}</span>
                        <span class="automation-status-badge ${isEnabled ? 'active' : 'inactive'}">${AutomationUtils.escapeHtml(isReserved ? automationT('automations_webhook_status_reserved', 'Reserved') : (isEnabled ? automationT('automations_webhook_status_enabled', 'Enabled') : automationT('automations_webhook_status_disabled', 'Disabled')))}</span>
                    </div>
                    <div class="automations-webhook-row">
                        <span class="automations-webhook-field-label">${AutomationUtils.escapeHtml(automationT('automations_webhook_url_label', 'Webhook URL'))}</span>
                        <code class="automations-webhook-value">${AutomationUtils.escapeHtml(trigger.url)}</code>
                        <button type="button" class="automations-webhook-copy-btn" data-webhook-action="copy-url" aria-label="${AutomationUtils.escapeHtml(automationT('automations_webhook_copy_url', 'Copy URL'))}" data-tooltip="${AutomationUtils.escapeHtml(automationT('automations_webhook_copy_url', 'Copy URL'))}">${copyIcon}</button>
                    </div>
                    ${secretBlock}
                    ${isReserved ? `
                    <p class="automations-webhook-secret-note">
                        ${Icons.info}
                        ${AutomationUtils.escapeHtml(automationT('automations_webhook_reserved_note', 'This webhook becomes active when you create the automation.'))}
                    </p>
                    <div class="automations-webhook-actions">
                        <button type="button" class="automations-webhook-action-btn" data-webhook-action="reserve">
                            ${Icons.refresh}
                            ${AutomationUtils.escapeHtml(automationT('automations_webhook_regenerate', 'Generate new credentials'))}
                        </button>
                    </div>
                    ` : `<div class="automations-webhook-actions">
                        <button type="button" class="automations-webhook-action-btn" data-webhook-action="rotate">
                            ${Icons.refresh}
                            ${AutomationUtils.escapeHtml(automationT('automations_webhook_rotate_secret', 'Rotate secret'))}
                        </button>
                        <button type="button" class="automations-webhook-action-btn ${isEnabled ? 'danger' : ''}" data-webhook-action="toggle">
                            ${isEnabled
                                ? Icons.error
                                : Icons.check}
                            ${AutomationUtils.escapeHtml(isEnabled ? automationT('automations_webhook_disable', 'Disable webhook') : automationT('automations_webhook_enable', 'Enable webhook'))}
                        </button>
                        <button type="button" class="automations-webhook-action-btn" data-webhook-action="deliveries">
                            ${Icons.refresh}
                            ${AutomationUtils.escapeHtml(automationT('automations_webhook_refresh_deliveries', 'Refresh deliveries'))}
                        </button>
                    </div>`}
                </div>

                <div class="automations-webhook-section">
                    <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_webhook_example_label', 'Example request'))}</span>
                    <pre class="automations-webhook-curl"><code>${AutomationUtils.escapeHtml(curlExample)}</code></pre>
                </div>

                ${isReserved ? '' : `<div class="automations-webhook-section">
                    <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_webhook_deliveries_label', 'Recent deliveries'))}</span>
                    <div class="automations-webhook-deliveries" data-webhook-deliveries></div>
                </div>`}
            ` : `
                <div class="automations-schedule-rules-empty automations-webhook-empty">
                    ${Icons.lightning}
                    <span>${AutomationUtils.escapeHtml(automationT('automations_webhook_create_hint', 'Generate the final webhook URL and one-time secret before creating the automation.'))}</span>
                    ${mode === 'create' ? `
                        <button type="button" class="automations-webhook-action-btn" data-webhook-action="reserve">
                            ${Icons.plus}
                            ${AutomationUtils.escapeHtml(automationT('automations_webhook_generate', 'Generate webhook credentials'))}
                        </button>
                    ` : ''}
                </div>
            `}
        </div>
    `;
}

function bindAutomationWebhookEditor(mode, container) {
    const state = getAutomationState(mode);
    container.querySelector('[data-webhook-field="payloadMode"]')?.addEventListener('change', (event) => {
        state.webhookPayloadMode = event.target.value || 'append';
    });
    container.querySelector('[data-webhook-field="includeHeaders"]')?.addEventListener('change', (event) => {
        state.webhookIncludeHeaders = Boolean(event.target.checked);
    });
    container.querySelectorAll('[data-webhook-action]').forEach((button) => {
        button.addEventListener('click', async () => {
            const action = button.dataset.webhookAction;
            if (action === 'copy-url') {
                await copyAutomationWebhookValue(state.webhookTrigger?.url, 'automations_webhook_url_copied', 'Webhook URL copied');
            } else if (action === 'copy-secret') {
                await copyAutomationWebhookValue(state.webhookSecret, 'automations_webhook_secret_copied', 'Webhook secret copied');
            } else if (action === 'reserve' && mode === 'create') {
                await reserveAutomationWebhookCredentials(button);
            } else if (action === 'rotate') {
                await rotateAutomationWebhookSecret();
            } else if (action === 'toggle') {
                await updateAutomationWebhook({ is_enabled: !state.webhookTrigger?.is_enabled });
            } else if (action === 'deliveries') {
                await loadAutomationWebhookDeliveries();
            }
        });
    });
    if (mode === 'edit' && state.webhookTrigger?.url) {
        loadAutomationWebhookDeliveries();
    }
}

async function reserveAutomationWebhookCredentials(button) {
    // Fetch final credentials without persisting an automation or webhook row yet.
    if (button) button.disabled = true;
    try {
        const res = await window.authedFetch('/api/v1/automations/webhook/credentials', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || automationT('automations_webhook_create_failed', 'Failed to create webhook credentials'));
        }
        AutomationState.create.webhookTrigger = {
            id: data.trigger_id,
            url: data.url,
            reservation_token: data.reservation_token,
            expires_at: data.expires_at,
            is_enabled: false,
        };
        AutomationState.create.webhookSecret = data.secret || null;
        renderAutomationScheduleRules('create', { preserveScroll: true });
    } catch (error) {
        notifyError(error?.message || automationT('automations_webhook_create_failed', 'Failed to create webhook credentials'));
        if (button) button.disabled = false;
    }
}

async function copyAutomationWebhookValue(value, successKey, fallback) {
    if (!value) return;
    try {
        await navigator.clipboard.writeText(value);
        notifySuccess?.(automationT(successKey, fallback));
    } catch (_) {
        notifyError?.(automationT('automations_webhook_copy_failed', 'Failed to copy webhook value'));
    }
}

async function createAutomationWebhook(automationId, mode = 'create') {
    const state = getAutomationState(mode);
    const res = await window.authedFetch(`/api/v1/automations/${encodeURIComponent(automationId)}/webhook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            payload_mode: state.webhookPayloadMode || 'append',
            include_headers: Boolean(state.webhookIncludeHeaders),
            is_enabled: true,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.detail || automationT('automations_webhook_create_failed', 'Failed to create webhook trigger'));
    }
    return data.trigger || null;
}

async function updateAutomationWebhook(patch) {
    const automationId = activeAutomationContext?.id;
    if (!automationId) return;
    const state = AutomationState.edit;
    const res = await window.authedFetch(`/api/v1/automations/${encodeURIComponent(automationId)}/webhook`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            payload_mode: state.webhookPayloadMode || 'append',
            include_headers: Boolean(state.webhookIncludeHeaders),
            ...patch,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        notifyError(data.detail || automationT('automations_webhook_update_failed', 'Failed to update webhook trigger'));
        return false;
    }
    state.webhookTrigger = data.trigger || state.webhookTrigger;
    state.webhookSecret = data.trigger?.secret || state.webhookSecret;
    notifySuccess(automationT('automations_webhook_updated', 'Webhook trigger updated'));
    renderAutomationScheduleRules('edit', { preserveScroll: true });
    return true;
}

async function rotateAutomationWebhookSecret() {
    const automationId = activeAutomationContext?.id;
    if (!automationId) return;
    const res = await window.authedFetch(`/api/v1/automations/${encodeURIComponent(automationId)}/webhook/rotate`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        notifyError(data.detail || automationT('automations_webhook_rotate_failed', 'Failed to rotate webhook secret'));
        return;
    }
    AutomationState.edit.webhookTrigger = data.trigger || AutomationState.edit.webhookTrigger;
    AutomationState.edit.webhookSecret = data.trigger?.secret || null;
    notifySuccess(automationT('automations_webhook_secret_rotated', 'Webhook secret rotated. Copy the new secret now.'));
    renderAutomationScheduleRules('edit', { preserveScroll: true });
}

async function loadAutomationWebhookDeliveries() {
    const automationId = activeAutomationContext?.id;
    const container = automationEditScheduleRules?.querySelector('[data-webhook-deliveries]');
    if (!automationId || !container) return;
    const res = await window.authedFetch(`/api/v1/automations/${encodeURIComponent(automationId)}/webhook/deliveries?limit=10`, { method: 'GET' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        container.innerHTML = `<p class="automations-once-hint">${AutomationUtils.escapeHtml(automationT('automations_webhook_deliveries_failed', 'Failed to load recent deliveries'))}</p>`;
        return;
    }
    const deliveries = Array.isArray(data.deliveries) ? data.deliveries : [];
    if (!deliveries.length) {
        container.innerHTML = `<p class="automations-once-hint">${AutomationUtils.escapeHtml(automationT('automations_webhook_deliveries_empty', 'No webhook deliveries yet.'))}</p>`;
        return;
    }
    container.innerHTML = `
        <div class="automations-webhook-delivery-list">
            ${deliveries.map((delivery) => {
                const isOk = delivery.status === 'completed' || delivery.status === 'queued';
                return `
                <div class="automations-webhook-delivery">
                    <span class="automation-status-badge ${isOk ? 'active' : 'inactive'}">${AutomationUtils.escapeHtml(delivery.status || 'unknown')}</span>
                    <div class="automations-webhook-delivery-meta">
                        <span class="automations-webhook-delivery-time">${AutomationUtils.escapeHtml(new Date(delivery.created_at).toLocaleString())}</span>
                        ${delivery.chat_id ? `<span class="automations-webhook-delivery-note">${AutomationUtils.escapeHtml(automationT('automations_webhook_delivery_chat_created', 'Chat created'))}</span>` : ''}
                        ${delivery.error ? `<span class="automations-webhook-delivery-error">${AutomationUtils.escapeHtml(delivery.error)}</span>` : ''}
                    </div>
                </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderAutomationOneTimeScheduleEditor(rule, mode) {
    const normalizedRule = normalizeScheduleRule(rule);
    const runAtLocal = AutomationUtils.sanitizeDateTimeLocalValue(normalizedRule.run_at_local) || getDefaultOneTimeLocalValue();
    return `
        <div class="automations-schedule-rule-item automations-schedule-once-item" data-rule-index="0">
            <div class="automations-schedule-section">
                <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_schedule_run_at_local', 'Run at (local time)'))}</span>
                <input type="datetime-local" class="automations-schedule-time-input automations-once-datetime-input" value="${AutomationUtils.escapeHtml(runAtLocal)}">
            </div>
            <div class="automations-schedule-section">
                <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_schedule_rule_label_optional', 'Label'))}</span>
                <input type="text" class="automations-schedule-label-input" placeholder="${AutomationUtils.escapeHtml(automationT('automations_schedule_rule_label_placeholder', 'Rule label (optional)'))}" value="${AutomationUtils.escapeHtml(normalizedRule.label || '')}">
            </div>
            <p class="automations-once-hint">
                ${Icons.clock}
                ${AutomationUtils.escapeHtml(automationT('automations_schedule_once_hint', 'This automation will execute once at the selected date and time, then automatically pause.'))}
            </p>
        </div>
    `;
}

function renderAutomationScheduleRuleItem(rule, index, mode) {
    const normalizedRule = normalizeScheduleRule(rule);
    const times = normalizedRule.times;
    return `
        <div class="automations-schedule-rule-item" data-rule-index="${index}">
            <button type="button" class="automations-schedule-remove-btn" aria-label="${AutomationUtils.escapeHtml(automationT('automations_schedule_remove_rule_aria', 'Remove schedule rule'))}">
                ${Icons.close}
            </button>
            <div class="automations-schedule-section">
                <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_schedule_days_label', 'Repeat on'))}</span>
                <div class="automations-schedule-days">
                    ${DAY_LABELS.map((label, dayIndex) => `
                        <button type="button" class="automations-schedule-day-btn ${(normalizedRule.days || []).includes(dayIndex) ? 'active' : ''}" data-day="${dayIndex}" aria-pressed="${(normalizedRule.days || []).includes(dayIndex) ? 'true' : 'false'}">${AutomationUtils.escapeHtml(automationT(`automations_schedule_day_${dayIndex}`, label))}</button>
                    `).join('')}
                </div>
            </div>
            <div class="automations-schedule-section">
                <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_schedule_trigger_times', 'Trigger times'))}</span>
                ${times.length ? `
                    <div class="automations-schedule-times-list">
                        ${times.map((time) => `
                            <span class="automations-schedule-time-chip">
                                <span class="automations-schedule-time-chip-value">${AutomationUtils.escapeHtml(time)}</span>
                                <button type="button" class="automations-schedule-remove-time-btn" data-time="${AutomationUtils.escapeHtml(time)}" aria-label="${AutomationUtils.escapeHtml(automationFormatT('automations_schedule_remove_time_aria', 'Remove {time}', { time }))}">
                                   ${Icons.close}
                                </button>
                            </span>
                        `).join('')}
                    </div>
                ` : ''}
                <div class="automations-schedule-time-add">
                    <input type="time" class="automations-schedule-time-input" value="${AutomationUtils.escapeHtml(times[times.length - 1] || DEFAULT_SCHEDULE_TIME)}">
                    <button type="button" class="automations-schedule-time-add-btn">
                        ${Icons.plus}
                        ${AutomationUtils.escapeHtml(automationT('automations_schedule_add_time', 'Add time'))}
                    </button>
                </div>
            </div>
            <div class="automations-schedule-section">
                <span class="automations-field-label">${AutomationUtils.escapeHtml(automationT('automations_schedule_rule_label_optional', 'Label'))}</span>
                <input type="text" class="automations-schedule-label-input" placeholder="${AutomationUtils.escapeHtml(automationT('automations_schedule_rule_label_placeholder', 'Rule label (optional)'))}" value="${AutomationUtils.escapeHtml(normalizedRule.label || '')}">
            </div>
        </div>
    `;
}

function addAutomationSchedulePreset(mode, preset) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    let newRule = createDefaultScheduleRule();
    clearAutomationFieldError(mode, 'schedule');

    switch (preset) {
        case 'morning':
            newRule = { times: ['07:00'], days: [0, 1, 2, 3, 4], label: automationT('automations_schedule_label_morning', 'Morning briefing') };
            break;
        case 'business':
            newRule = { times: ['09:00'], days: [0, 1, 2, 3, 4], label: automationT('automations_schedule_label_business', 'Business hours') };
            break;
        case 'evening':
            newRule = { times: ['18:00'], days: [0, 1, 2, 3, 4], label: automationT('automations_schedule_label_evening', 'Evening summary') };
            break;
    }

    state.scheduleRules = [...state.scheduleRules, normalizeScheduleRule(newRule)];
    renderAutomationScheduleRules(mode, { preserveScroll: true });
}

function addAutomationScheduleRule(mode) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    clearAutomationFieldError(mode, 'schedule');
    state.scheduleRules = [...state.scheduleRules, createDefaultScheduleRule('recurring')];
    renderAutomationScheduleRules(mode, { preserveScroll: true });
}

function removeAutomationScheduleRule(mode, index) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    clearAutomationFieldError(mode, 'schedule');
    state.scheduleRules = state.scheduleRules.filter((_, i) => i !== index);
    renderAutomationScheduleRules(mode, { preserveScroll: true });
}

function toggleAutomationScheduleDay(mode, ruleIndex, dayIndex) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const rule = state.scheduleRules[ruleIndex];
    if (!rule) return;
    clearAutomationFieldError(mode, 'schedule');

    const days = rule.days || [];
    if (days.includes(dayIndex)) {
        rule.days = days.filter(d => d !== dayIndex);
    } else {
        rule.days = [...days, dayIndex].sort((a, b) => a - b);
    }
    renderAutomationScheduleRules(mode, { preserveScroll: true });
}

function updateAutomationScheduleField(mode, ruleIndex, field, value) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const rule = state.scheduleRules[ruleIndex];
    clearAutomationFieldError(mode, 'schedule');
    if (rule) {
        rule[field] = value;
    }
}

function addAutomationScheduleTime(mode, ruleIndex, timeValue) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const rule = state.scheduleRules[ruleIndex];
    if (!rule) return;
    clearAutomationFieldError(mode, 'schedule');
    const normalizedTime = AutomationUtils.sanitizeTimeValue(timeValue);
    if (!normalizedTime) return;
    if (!Array.isArray(rule.times)) {
        rule.times = [];
    }
    if (!rule.times.includes(normalizedTime)) {
        rule.times.push(normalizedTime);
        rule.times.sort();
        renderAutomationScheduleRules(mode, { preserveScroll: true });
    }
}

function removeAutomationScheduleTime(mode, ruleIndex, timeValue) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const rule = state.scheduleRules[ruleIndex];
    if (!rule || !Array.isArray(rule.times)) return;
    clearAutomationFieldError(mode, 'schedule');
    const filtered = rule.times.filter((time) => time !== timeValue);
    rule.times = filtered.length ? filtered : [DEFAULT_SCHEDULE_TIME];
    renderAutomationScheduleRules(mode, { preserveScroll: true });
}

// View Management
function showAutomationsStartContainer() {
    if (automationsContent) automationsContent.style.display = 'block';
    if (automationsContentCreateAutomation) automationsContentCreateAutomation.style.display = 'none';
    if (automationsContentEditAutomation) automationsContentEditAutomation.style.display = 'none';
    closeAllAutomationFileLibraries();
    activeAutomationContext = null;
    resetAutomationCreateState();
}

function showAutomationsCreateContainer() {
    if (automationsContent) automationsContent.style.display = 'none';
    if (automationsContentEditAutomation) automationsContentEditAutomation.style.display = 'none';
    if (automationsContentCreateAutomation) automationsContentCreateAutomation.style.display = 'block';
    closeAllAutomationFileLibraries();
    activeAutomationContext = null;
    resetAutomationCreateState();
    renderAutomationSkillSelect('create');
    renderAutomationNotesSelect('create');
    automationNameInput?.focus();
}

function showAutomationDeleteModal(automation) {
    if (!deleteAutomationOverlay) return;
    closeAllAutomationFileLibraries();
    activeAutomationContext = automation ? { mode: 'delete', id: automation.id, title: automation.title } : null;
    if (deleteAutomationTitle) {
        deleteAutomationTitle.textContent = automation?.title
            ? automationFormatT('automations_delete_title_named', 'Delete automation "{title}"', { title: automation.title })
            : automationT('automations_delete_title', 'Delete automation');
    }
    deleteAutomationOverlay.hidden = false;
}

function hideAutomationDeleteModal() {
    if (deleteAutomationOverlay) {
        deleteAutomationOverlay.hidden = true;
    }
    activeAutomationContext = null;
}

function showAutomationsDeleteContainer(automation) {
    showAutomationDeleteModal(automation);
}

function showAutomationsEditContainer(automation) {
    if (automationsContent) automationsContent.style.display = 'none';
    if (automationsContentCreateAutomation) automationsContentCreateAutomation.style.display = 'none';
    if (automationsContentEditAutomation) automationsContentEditAutomation.style.display = 'block';
    closeAllAutomationFileLibraries();

    const iconData = AutomationIconUtils.parse(automation.icon, automation.icon_color);

    activeAutomationContext = automation ? {
        id: automation.id,
        title: automation.title,
        prompt: automation.prompt,
        icon: automation.icon,
        icon_color: iconData.color,
        model_id: automation.model_id,
        schedule_rules: automation.schedule_rules || [],
        schedule_timezone: automation.schedule_timezone || null,
        webhook_trigger: automation.webhook_trigger || null,
        is_active: automation.is_active,
    } : null;

    if (automationEditNameInput) automationEditNameInput.value = automation?.title || '';
    if (automationEditPromptInput) automationEditPromptInput.value = automation?.prompt || '';
    if (automationEditActiveToggle) automationEditActiveToggle.checked = automation?.is_active ?? true;
    clearAutomationValidationErrors('edit');

    resetAutomationIconPicker('edit', automation?.icon || AUTOMATION_DEFAULT_ICON_ID, iconData.color);
    AutomationState.edit.selectedModelId = automation?.model_id || null;
    AutomationState.edit.selectedMcpServerIds = Array.isArray(automation?.mcp_server_ids) ? [...automation.mcp_server_ids] : [];
    AutomationState.edit.availableConnections = [];
    AutomationState.edit.connectionsLoading = false;
    AutomationState.edit.connectionsError = false;
    AutomationState.edit.modelSearchQuery = '';
    AutomationState.edit.scheduleTimezone = automation?.schedule_timezone || null;
    AutomationState.edit.scheduleRules = automation?.schedule_rules
        ? convertStoredRulesToEditorRules(automation.schedule_rules, AutomationState.edit.scheduleTimezone)
        : [];
    AutomationState.edit.selectedSkillId = automation?.skill_id || null;
    AutomationState.edit.selectedNoteIds = Array.isArray(automation?.note_ids) ? [...automation.note_ids] : [];
    AutomationState.edit.selectedFileIds = Array.isArray(automation?.file_ids) ? [...automation.file_ids] : [];
    AutomationState.edit.fileMetadata = {};
    AutomationState.edit.fileLibrarySearch = '';
    AutomationState.edit.isFilesLoading = false;
    AutomationState.edit.webhookTrigger = automation?.webhook_trigger || null;
    AutomationState.edit.webhookSecret = null;
    AutomationState.edit.webhookPayloadMode = automation?.webhook_trigger?.payload_mode || 'append';
    AutomationState.edit.webhookIncludeHeaders = Boolean(automation?.webhook_trigger?.include_headers);
    AutomationState.edit.triggerType = automation?.webhook_trigger ? 'webhook' : 'schedule';
    (AutomationState.edit.selectedFileIds || []).forEach((fileId) => {
        const meta = resolveAutomationFileMeta(fileId, 'edit');
        if (meta) {
            recordAutomationFileMeta('edit', meta);
        }
    });
    AutomationState.edit.isActive = automation?.is_active ?? true;

    renderAutomationModelSelect('edit');
    renderAutomationConnectionsSelect('edit');
    void loadAutomationConnections('edit', { pruneUnavailable: true });
    renderAutomationSkillSelect('edit');
    renderAutomationNotesSelect('edit');
    renderAutomationScheduleRules('edit');
    renderAutomationFilesSelected('edit');
    renderAutomationFileLibrary('edit');

    requestAnimationFrame(() => automationEditNameInput?.focus());
}

function isAutomationFormModeActive() {
    return Boolean(
        (automationsContentCreateAutomation && automationsContentCreateAutomation.style.display !== 'none') ||
        (automationsContentEditAutomation && automationsContentEditAutomation.style.display !== 'none')
    );
}

function hasAutomationTransientDropdown() {
    return Boolean(
        automationFileLibraryOpenMode ||
        document.querySelector('#automationsContainer .select-dropdown.open') ||
        document.querySelector('#automationsContainer .svg-select-dropdown.open') ||
        document.querySelector('#automationsContainer .shared-model-select-dropdown.open') ||
        document.querySelector('#automationsContainer .shared-skill-select-dropdown.open') ||
        document.querySelector('#automationsContainer .automations-notes-dropdown.open') ||
        document.querySelector('#automationsContainer .automations-connections-dropdown.open') ||
        document.querySelector('#automationsContainer .shared-file-library-dropdown.open')
    );
}

function closeAutomationTransientDropdowns() {
    closeAllAutomationDropdowns();
    closeAllAutomationFileLibraries();
    AutomationCreateIconPicker?.close?.();
    AutomationEditIconPicker?.close?.();

    ['create', 'edit'].forEach((mode) => {
        closeAutomationModelDropdown(mode);

        const skillContainer = mode === 'edit' ? automationEditSkillSelect : automationSkillSelect;
        skillContainer?.querySelector(`#automationSkillSelectTrigger${mode}`)?.classList.remove('open');
        skillContainer?.querySelector(`#automationSkillSelectDropdown${mode}`)?.classList.remove('open');

        const notesContainer = mode === 'edit' ? automationEditNotesSelect : automationNotesSelect;
        notesContainer?.querySelector(`#automationNotesAddTrigger${mode}`)?.classList.remove('open');
        notesContainer?.querySelector(`#automationNotesDropdown${mode}`)?.classList.remove('open');

        const connectionsContainer = mode === 'edit' ? automationEditConnectionsSelect : automationConnectionsSelect;
        const connectionsTrigger = connectionsContainer?.querySelector(`#automationConnectionsAddTrigger${mode}`);
        connectionsTrigger?.classList.remove('open');
        connectionsTrigger?.setAttribute('aria-expanded', 'false');
        connectionsContainer?.querySelector(`#automationConnectionsDropdown${mode}`)?.classList.remove('open');
    });
}

// Event Handlers
if (addAutomationBtn && automationsContent && automationsContentCreateAutomation) {
    addAutomationBtn.addEventListener('click', () => {
        showAutomationsCreateContainer();
    });
}

if (createAutomationCancelBtn) {
    createAutomationCancelBtn.addEventListener('click', () => showAutomationsStartContainer());
}

if (deleteAutomationCancelBtn) {
    deleteAutomationCancelBtn.addEventListener('click', () => hideAutomationDeleteModal());
}

if (deleteAutomationOverlay) {
    deleteAutomationOverlay.addEventListener('click', (event) => {
        if (event.target === deleteAutomationOverlay) {
            hideAutomationDeleteModal();
        }
    });
}

if (editAutomationCancelBtn) {
    editAutomationCancelBtn.addEventListener('click', () => showAutomationsStartContainer());
}

// Create Automation
if (confirmCreateAutomationBtn) {
    confirmCreateAutomationBtn.addEventListener('click', async () => {
        // Roll back any picker preview that was not committed with the
        // picker's own Save button before serializing the outer form.
        AutomationCreateIconPicker?.close?.();
        const requiredFields = validateAutomationRequiredFields('create');
        if (!requiredFields) return;
        if (!AutomationState.create.selectedModelId) {
            notifyError(automationT('automations_error_model_required', 'Please select a model'));
            return;
        }
        if (
            AutomationState.create.triggerType === 'webhook'
            && !AutomationState.create.webhookTrigger?.reservation_token
        ) {
            notifyError(automationT('automations_webhook_create_hint', 'Generate the webhook URL and secret before creating the automation.'));
            return;
        }

        if (AutomationState.create.triggerType !== 'webhook') {
            const createScheduleValidation = validateAutomationScheduleRulesForSubmit('create');
            if (!createScheduleValidation.ok) {
                showAutomationScheduleError(
                    'create',
                    createScheduleValidation.message || automationT('automations_schedule_error_invalid', 'Invalid schedule'),
                );
                return;
            }
        }
        clearAutomationFieldError('create', 'schedule');

        try {
            const scheduleRulesPayload = AutomationState.create.triggerType === 'webhook' ? [] : convertLocalRulesToUtc(AutomationState.create.scheduleRules);
            const res = await window.authedFetch('/api/v1/automations/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: requiredFields.title,
                    prompt: requiredFields.prompt,
                    model_id: AutomationState.create.selectedModelId,
                    icon: AutomationIconUtils.serialize('create'),
                    icon_color: AutomationIconUtils.selectedColor('create'),
                    schedule_rules: scheduleRulesPayload,
                    schedule_timezone: AutomationState.create.triggerType === 'webhook' ? null : getAutomationScheduleTimezoneForSubmit('create'),
                    skill_id: AutomationState.create.selectedSkillId || null,
                    note_ids: AutomationState.create.selectedNoteIds || [],
                    file_ids: AutomationState.create.selectedFileIds || [],
                    mcp_server_ids: AutomationState.create.selectedMcpServerIds || [],
                    is_active: automationActiveToggle?.checked ?? true,
                    // The backend creates this nested trigger in the same
                    // transaction, before the create form has an automation ID.
                    webhook_trigger: AutomationState.create.triggerType === 'webhook' ? {
                        payload_mode: AutomationState.create.webhookPayloadMode || 'append',
                        include_headers: Boolean(AutomationState.create.webhookIncludeHeaders),
                        is_enabled: true,
                        trigger_id: AutomationState.create.webhookTrigger?.id,
                        secret: AutomationState.create.webhookSecret,
                        reservation_token: AutomationState.create.webhookTrigger?.reservation_token,
                    } : null,
                }),
            });

            if (res.ok) {
                await refreshAutomations();
                showAutomationsStartContainer();
                notifySuccess(automationT('automations_success_created', 'Automation created successfully'));
            } else {
                const data = await res.json().catch(() => ({}));
                notifyError(data.detail || automationT('automations_error_create_failed', 'Failed to create automation'));
            }
        } catch (e) {
            notifyError(e?.message || automationT('automations_error_create', 'Error creating automation'));
        }
    });
}

// Delete Automation
if (confirmDeleteAutomationBtn) {
    confirmDeleteAutomationBtn.addEventListener('click', async () => {
        try {
            const id = activeAutomationContext?.id;
            if (!id) {
                notifyError(automationT('automations_error_id_missing', 'Automation id missing'));
                return;
            }
            confirmDeleteAutomationBtn.disabled = true;
            deleteAutomationCancelBtn && (deleteAutomationCancelBtn.disabled = true);

            const res = await window.authedFetch(`/api/v1/automations/delete?automation_id=${encodeURIComponent(id)}`, {
                method: 'DELETE',
            });

            if (res.ok) {
                await refreshAutomations();
                hideAutomationDeleteModal();
                showAutomationsStartContainer();
                notifySuccess(automationT('automations_success_deleted', 'Automation deleted successfully'));
            } else {
                hideAutomationDeleteModal();
                notifyError(automationT('automations_error_delete_failed', 'Failed to delete automation'));
            }
        } catch (error) {
            console.error('Failed to delete automation', error);
            notifyError(automationT('automations_error_delete_unexpected', 'Unexpected error while deleting automation'));
        } finally {
            confirmDeleteAutomationBtn.disabled = false;
            deleteAutomationCancelBtn && (deleteAutomationCancelBtn.disabled = false);
        }
    });
}

// Update Automation
if (saveAutomationChangesBtn) {
    saveAutomationChangesBtn.addEventListener('click', async () => {
        // The outer form must serialize only the picker's committed selection.
        AutomationEditIconPicker?.close?.();
        const requiredFields = validateAutomationRequiredFields('edit');
        if (!requiredFields) return;

        if (AutomationState.edit.triggerType !== 'webhook') {
            const editScheduleValidation = validateAutomationScheduleRulesForSubmit('edit');
            if (!editScheduleValidation.ok) {
                showAutomationScheduleError(
                    'edit',
                    editScheduleValidation.message || automationT('automations_schedule_error_invalid', 'Invalid schedule'),
                );
                return;
            }
        }
        clearAutomationFieldError('edit', 'schedule');

        try {
            const scheduleRulesPayload = AutomationState.edit.triggerType === 'webhook' ? [] : convertLocalRulesToUtc(AutomationState.edit.scheduleRules);
            const res = await window.authedFetch('/api/v1/automations/update', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    automation_id: activeAutomationContext.id,
                    title: requiredFields.title,
                    prompt: requiredFields.prompt,
                    model_id: AutomationState.edit.selectedModelId,
                    icon: AutomationIconUtils.serialize('edit'),
                    icon_color: AutomationIconUtils.selectedColor('edit'),
                    schedule_rules: scheduleRulesPayload,
                    schedule_timezone: AutomationState.edit.triggerType === 'webhook' ? null : getAutomationScheduleTimezoneForSubmit('edit'),
                    skill_id: AutomationState.edit.selectedSkillId || null,
                    note_ids: AutomationState.edit.selectedNoteIds || [],
                    file_ids: AutomationState.edit.selectedFileIds || [],
                    mcp_server_ids: AutomationState.edit.selectedMcpServerIds || [],
                    is_active: automationEditActiveToggle?.checked ?? true,
                }),
            });

            if (res.ok) {
                const data = await res.json().catch(() => ({}));
                if (AutomationState.edit.triggerType === 'webhook') {
                    if (AutomationState.edit.webhookTrigger?.id) {
                        const webhookUpdated = await updateAutomationWebhook({ is_enabled: AutomationState.edit.webhookTrigger.is_enabled !== false });
                        if (!webhookUpdated) return;
                    } else if (activeAutomationContext?.id) {
                        const trigger = await createAutomationWebhook(activeAutomationContext.id, 'edit');
                        AutomationState.edit.webhookTrigger = trigger;
                        AutomationState.edit.webhookSecret = trigger?.secret || null;
                        notifySuccess(automationT('automations_webhook_created_copy_secret', 'Automation created. Copy the webhook secret now; it will only be shown once.'));
                    }
                } else if (AutomationState.edit.webhookTrigger?.id) {
                    const webhookUpdated = await updateAutomationWebhook({ is_enabled: false });
                    if (!webhookUpdated) return;
                }
                await refreshAutomations();
                const preservedWebhookSecret = AutomationState.edit.webhookSecret;
                if (preservedWebhookSecret || AutomationState.edit.triggerType === 'webhook') {
                    showAutomationsEditContainer({ ...(data.automation || activeAutomationContext), webhook_trigger: AutomationState.edit.webhookTrigger });
                    AutomationState.edit.webhookSecret = preservedWebhookSecret;
                    renderAutomationScheduleRules('edit');
                } else {
                    showAutomationsStartContainer();
                }
                notifySuccess(automationT('automations_success_updated', 'Automation updated successfully'));
            } else {
                const data = await res.json().catch(() => ({}));
                notifyError(data.detail || automationT('automations_error_update_failed', 'Failed to update automation'));
            }
        } catch (err) {
            notifyError(err?.message || automationT('automations_error_update', 'Error updating automation'));
        }
    });
}

// Enter key handlers
automationNameInput?.addEventListener('input', () => clearAutomationFieldError('create', 'name'));
automationNameInput?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        automationPromptInput?.focus();
    }
});
automationPromptInput?.addEventListener('input', () => clearAutomationFieldError('create', 'prompt'));

automationEditNameInput?.addEventListener('input', () => clearAutomationFieldError('edit', 'name'));
automationEditNameInput?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        automationEditPromptInput?.focus();
    }
});
automationEditPromptInput?.addEventListener('input', () => clearAutomationFieldError('edit', 'prompt'));

// Close dropdowns handlers
function closeAllAutomationDropdowns() {
    document.querySelectorAll('#automationsContainer .select-dropdown.open').forEach((dd) => dd.classList.remove('open'));
    document.querySelectorAll('#automationsContainer .project-ellipsis[aria-expanded="true"]')
        .forEach((trigger) => trigger.setAttribute('aria-expanded', 'false'));
}

function validateAutomationScheduleRulesForSubmit(mode) {
    const state = mode === 'edit' ? AutomationState.edit : AutomationState.create;
    const normalized = normalizeScheduleRules(state.scheduleRules || []);
    if (!normalized.length) {
        return { ok: false, message: automationT('automations_schedule_error_configure', 'Please configure a schedule') };
    }

    const scheduleType = normalized[0]?.type === 'once' ? 'once' : 'recurring';
    if (scheduleType === 'once') {
        const firstRule = normalized[0];
        const localValue = AutomationUtils.sanitizeDateTimeLocalValue(firstRule?.run_at_local);
        if (!localValue) {
            return { ok: false, message: automationT('automations_schedule_error_valid_datetime', 'Please select a valid date and time') };
        }
        const runAt = new Date(localValue);
        if (Number.isNaN(runAt.getTime())) {
            return { ok: false, message: automationT('automations_schedule_error_valid_datetime', 'Please select a valid date and time') };
        }
        if (runAt.getTime() <= Date.now()) {
            return { ok: false, message: automationT('automations_schedule_error_future', 'One-time run must be in the future') };
        }
        return { ok: true };
    }

    const hasRecurringRule = normalized.some((rule) => rule.type !== 'once' && Array.isArray(rule.days) && rule.days.length && Array.isArray(rule.times) && rule.times.length);
    if (!hasRecurringRule) {
        return { ok: false, message: automationT('automations_schedule_error_recurring_rule', 'Please configure at least one recurring schedule rule') };
    }
    return { ok: true };
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('.automations-content-main-element')) {
        closeAllAutomationDropdowns();
    }
});

window.addEventListener('resize', closeAllAutomationDropdowns);
window.addEventListener('scroll', closeAllAutomationDropdowns, true);

// Automation Card Creation
function createAutomationCard(automation) {
    const iconData = AutomationIconUtils.parse(automation.icon, automation.icon_color);
    const iconMarkup = `<span class="project-icon">${AutomationIconUtils.render(iconData, { size: 24, strokeWidth: 0.5 })}</span>`;

    const playIcon = Icons.play;
    const pauseIcon = Icons.pause;
    const editIcon = Icons?.edit;
    const trashIcon = Icons?.trash || '';
    const statusLabel = automation.is_active ? automationT('automations_status_active', 'Active') : automationT('automations_status_paused', 'Paused');

    return window.EntityCardRenderer.createCard({
        className: `automations-content-main-element ${automation.is_active ? '' : 'inactive'}`,
        dataset: { automationId: automation.id },
        iconHtml: iconMarkup,
        title: automation.title,
        bottomExtraHtml: `
            <span class="automation-status-badge ${automation.is_active ? 'active' : 'inactive'}">
                ${automation.is_active ? Icons.recording : Icons.playing} ${AutomationUtils.escapeHtml(statusLabel)}
            </span>`,
        menuItems: [
            {
                action: 'edit',
                className: 'edit-btn',
                iconHtml: editIcon,
                label: automationT('automations_action_edit', 'Edit'),
                onSelect: () => showAutomationsEditContainer(automation),
            },
            {
                action: 'toggle',
                className: 'toggle-btn',
                iconHtml: automation.is_active ? pauseIcon : playIcon,
                label: automation.is_active
                    ? automationT('automations_action_pause', 'Pause')
                    : automationT('automations_action_activate', 'Activate'),
                onSelect: () => toggleAutomationActive(automation.id),
            },
            {
                action: 'delete',
                className: 'select-dropdown-button-red delete-btn',
                iconHtml: trashIcon,
                label: automationT('automations_action_delete', 'Delete'),
                onSelect: () => showAutomationsDeleteContainer(automation),
            },
        ],
        moreOptionsLabel: automationT('files_more_options', 'More options'),
        closeDropdowns: closeAllAutomationDropdowns,
        onClick: () => showAutomationsEditContainer(automation),
    });
}

async function toggleAutomationActive(automationId) {
    try {
        const res = await window.authedFetch(`/api/v1/automations/${automationId}/toggle`, { method: 'POST' });
        if (res.ok) {
            await refreshAutomations();
        } else {
            notifyError(automationT('automations_error_toggle_failed', 'Failed to toggle automation status'));
        }
    } catch (e) {
        notifyError(automationT('automations_error_toggle', 'Error toggling automation status'));
    }
}

function createAutomationsEmptyState() {
    const div = document.createElement('div');
    div.className = 'workspace-notifications-empty workspace-empty-grid';
    div.innerHTML = `
        <div class="workspace-notifications-empty-icon">
            ${Icons.clock}
        </div>
        <p class="workspace-notifications-empty-title">${AutomationUtils.escapeHtml(automationT('automations_empty_title', 'No automations yet'))}</p>
        <p class="workspace-notifications-empty-text">${AutomationUtils.escapeHtml(automationT('automations_empty_text', 'Automations let you automate prompts. Run once at a specific time or schedule recurring briefings and summaries.'))}</p>
    `;

    return div;
}

async function refreshAutomations() {
    try {
        const params = new URLSearchParams({
            limit: String(AUTOMATIONS_PAGE_LIMIT),
            offset: '0',
        });
        const res = await window.authedFetch(`/api/v1/automations/list?${params.toString()}`, { method: 'GET' });
        if (!res.ok) {
            notifyError(automationT('automations_error_fetch_failed', 'Failed to fetch automations'));
            return;
        }
        const data = await res.json();
        const automations = unwrapAutomationsPage(data, 'automations');

        if (automationsContainerMain) {
            automationsContainerMain.innerHTML = '';
            if (automations.length === 0) {
                automationsContainerMain.appendChild(createAutomationsEmptyState());
            } else {
                automations.forEach(t => automationsContainerMain.appendChild(createAutomationCard(t)));
            }
        }
    } catch (err) {
        console.error('Error fetching automations', err);
    }
}

async function initAutomations() {
    showAutomationsStartContainer();
    initAutomationIconPickers();
    initAutomationFilesUI();
    await Promise.all([loadAutomationModels(), loadAutomationSkills(), loadAutomationNotes(), loadAutomationFiles()]);
    renderAutomationModelSelect('create');
    renderAutomationConnectionsSelect('create');
    renderAutomationSkillSelect('create');
    renderAutomationNotesSelect('create');
    renderAutomationScheduleRules('create');
    renderAutomationFilesSelected('create');
    renderAutomationFileLibrary('create');
    await refreshAutomations();
}

// Export for global access
// Register escape handler for delete automation modal
if (typeof window !== 'undefined' && window.registerEscapeHandler) {
    window.registerEscapeHandler({
        id: 'delete-automation-modal',
        priority: 100,
        isActive: () => deleteAutomationOverlay && !deleteAutomationOverlay.hidden,
        close: () => hideAutomationDeleteModal(),
    });
    window.registerEscapeHandler({
        id: 'automations-transient-dropdowns',
        priority: 120,
        isActive: () => hasAutomationTransientDropdown(),
        close: () => closeAutomationTransientDropdowns(),
    });
    window.registerEscapeHandler({
        id: 'automations-form-mode',
        priority: 20,
        isActive: () => isAutomationFormModeActive(),
        close: () => showAutomationsStartContainer(),
    });
}

if (typeof window !== 'undefined') {
    window.initAutomations = initAutomations;
    window.refreshAutomations = refreshAutomations;
    window.showAutomationsStartContainer = showAutomationsStartContainer;
    window.showAutomationDeleteModal = showAutomationDeleteModal;
    window.hideAutomationDeleteModal = hideAutomationDeleteModal;
    window.cleanupAutomationSelectOutsideHandlers = cleanupAutomationSelectOutsideHandlers;
    if (window.__pendingAutomationsInit) {
        delete window.__pendingAutomationsInit;
        initAutomations();
    }
}
