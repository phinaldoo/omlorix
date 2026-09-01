/**
* Sidebar Button Visibility Settings Manager
* Handles which sidebar buttons are shown/hidden
* Syncs settings with backend API
*/

const SIDEBAR_BUTTONS = {
    create_chat: {
        id: 'create_chat',
        label: 'sidebar_button_create_chat',
        default: true,
        featureFlag: null
    },
    search_chats: {
        id: 'search_chats',
        label: 'sidebar_button_search_chats',
        default: true,
        featureFlag: null
    },
    workspace: {
        id: 'workspace',
        label: 'sidebar_button_workspace',
        default: true,
        featureFlag: null
    },
    automations: {
        id: 'automations',
        label: 'sidebar_button_automations',
        default: true,
        featureFlag: 'enable_automations'
    },
    projects: {
        id: 'projects',
        label: 'sidebar_button_projects',
        default: true,
        featureFlag: 'enable_projects'
    }
};

let currentSidebarButtonVisibility = {};
let currentSidebarFeatureAvailability = {
    create_chat: true,
    search_chats: true,
    workspace: true,
    automations: false,
    projects: false
};
let eventListenersInitialized = false;
let sidebarSettingsLoaded = false;
let sidebarButtonsSaveInFlight = false;

function sidebarButtonsT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function saveSidebarVisibilityToStorage(visibility) {
    try {
        localStorage.setItem(SIDEBAR_VISIBILITY_STORAGE_KEY, JSON.stringify(visibility));
    } catch (error) {
        console.error('Error saving sidebar visibility to localStorage:', error);
    }
}

function coerceSidebarFeatureFlag(value) {
    if (value === true) return true;
    if (value === false || value === null || value === undefined) return false;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'y', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'n', 'off', ''].includes(normalized)) return false;
    }
    return false;
}

function resolveSidebarFeatureAvailability(data = null) {
    if (!data) {
        return { ...currentSidebarFeatureAvailability };
    }

    const setup = data || (typeof window !== 'undefined' ? window.chatSetup : null) || {};
    const fallbackSetup = typeof window !== 'undefined' ? window.chatSetup || {} : {};
    const resolveFlag = (key) => {
        if (Object.prototype.hasOwnProperty.call(setup, key)) {
            return coerceSidebarFeatureFlag(setup[key]);
        }
        const globalFeatureKey = key === 'enable_projects'
            ? 'enableProjectsFeature'
            : key === 'enable_automations'
                ? 'enableAutomationsFeature'
                : null;
        if (
            globalFeatureKey
            && typeof window !== 'undefined'
            && Object.prototype.hasOwnProperty.call(window, globalFeatureKey)
        ) {
            return coerceSidebarFeatureFlag(window[globalFeatureKey]);
        }
        return coerceSidebarFeatureFlag(fallbackSetup[key]);
    };

    return {
        create_chat: true,
        search_chats: true,
        workspace: true,
        automations: resolveFlag('enable_automations'),
        projects: resolveFlag('enable_projects')
    };
}

/**
 * Return the native multi-select that remains the form-value source of truth.
 */
function getSidebarButtonsSelect() {
    return document.getElementById('sidebar-buttons-multiselect');
}

/**
 * Upgrade the native multi-select with the same accessible dropdown used by
 * model settings. The native select remains in the DOM for form semantics and
 * straightforward state synchronization.
 */
function ensureSidebarButtonsMultiselect() {
    const select = getSidebarButtonsSelect();
    if (!select || select._multiSelect) {
        return select;
    }
    if (typeof initializeModelSettingsMultiSelect !== 'function') {
        return select;
    }

    const container = select.parentNode;
    const meta = initializeModelSettingsMultiSelect(select, {
        key: 'sidebar_button_visibility',
        placeholder: sidebarButtonsT(
            'sidebar_button_visibility_desc',
            'Choose which buttons to show in the sidebar.'
        ),
    });
    select._multiSelect = meta;
    meta.wrapper.classList.add('sidebar-buttons-multiselect');
    container?.appendChild(meta.wrapper);

    const trigger = meta.wrapper.querySelector('.model-settings-multiselect-trigger');
    trigger?.setAttribute('aria-labelledby', 'sidebar-button-visibility-title');
    trigger?.setAttribute('aria-describedby', 'sidebar-button-visibility-description');
    return select;
}

/**
 * Refresh labels copied into the custom dropdown after a runtime locale change.
 */
function refreshSidebarButtonsMultiselectTranslations() {
    const select = getSidebarButtonsSelect();
    if (!select) {
        return;
    }
    Object.entries(SIDEBAR_BUTTONS).forEach(([key, config]) => {
        const option = Array.from(select.options || []).find((candidate) => candidate.value === key);
        const fallback = option?.textContent || key;
        const label = sidebarButtonsT(config.label, fallback);
        if (option) {
            option.textContent = label;
        }
        const customLabel = select._multiSelect?.wrapper.querySelector(
            `.model-settings-multiselect-option[data-value="${key}"] .model-settings-multiselect-text`
        );
        if (customLabel) {
            customLabel.textContent = label;
        }
    });

    const wrapper = select._multiSelect?.wrapper;
    if (wrapper) {
        const actionButtons = wrapper.querySelectorAll('.model-settings-multiselect-action-btn');
        if (actionButtons[0]) {
            actionButtons[0].textContent = sidebarButtonsT('model_settings_select_all', 'Select All');
        }
        if (actionButtons[1]) {
            actionButtons[1].textContent = sidebarButtonsT('model_settings_unselect_all', 'Unselect All');
        }
        select._multiSelect.syncFromSelect?.();
        if (!Array.from(select.selectedOptions || []).length) {
            const valueLabel = wrapper.querySelector('.model-settings-multiselect-value');
            if (valueLabel) {
                valueLabel.textContent = sidebarButtonsT(
                    'sidebar_button_visibility_desc',
                    'Choose which buttons to show in the sidebar.'
                );
            }
        }
    }
}

function applySidebarSettingsRowAvailability(data = null) {
    const availability = resolveSidebarFeatureAvailability(data);
    currentSidebarFeatureAvailability = availability;
    const select = getSidebarButtonsSelect();

    Object.keys(SIDEBAR_BUTTONS).forEach(key => {
        const option = Array.from(select?.options || []).find((candidate) => candidate.value === key);
        const customOption = select?._multiSelect?.wrapper.querySelector(
            `.model-settings-multiselect-option[data-value="${key}"]`
        );
        const isAvailable = availability[key] !== false;

        // Feature policy wins over the user's display preference. Unavailable
        // destinations are removed from both the native and custom listboxes.
        if (option) {
            option.hidden = !isAvailable;
            option.disabled = !isAvailable;
        }
        if (customOption) {
            customOption.hidden = !isAvailable;
            customOption.disabled = !isAvailable;
            customOption.setAttribute('aria-disabled', isAvailable ? 'false' : 'true');
        }
    });
}

/**
 * Initialize sidebar button visibility settings
 */
async function initializeSidebarButtonSettings(initialData = null) {
    // Guard against redundant fetches
    if (sidebarSettingsLoaded) {
        if (initialData) {
            const chatSettings = initialData.chat || {};
            if (chatSettings.sidebar_button_visibility && typeof chatSettings.sidebar_button_visibility === 'object') {
                currentSidebarButtonVisibility = chatSettings.sidebar_button_visibility;
            }
            applySidebarSettingsRowAvailability(initialData);
            updateSidebarButtonUI();
        }
        return;
    }
    
    try {
        const data = initialData || (
            typeof window.getCachedUserSettingsInit === 'function'
                ? await window.getCachedUserSettingsInit()
                : await (async () => {
                    const response = await window.authedFetch('/api/v1/users/user-settings/init');
                    if (!response.ok) {
                        console.error('Failed to fetch user settings');
                        return null;
                    }
                    return response.json();
                })()
        );
        if (!data) return;
        const chatSettings = data.chat || {};
        currentSidebarButtonVisibility = chatSettings.sidebar_button_visibility || {};
        applySidebarSettingsRowAvailability(data);
        
        // Ensure all buttons have a value (default to true)
        Object.keys(SIDEBAR_BUTTONS).forEach(key => {
            if (currentSidebarButtonVisibility[key] === undefined) {
                currentSidebarButtonVisibility[key] = SIDEBAR_BUTTONS[key].default;
            }
        });
        
        // Save to localStorage
        saveSidebarVisibilityToStorage(currentSidebarButtonVisibility);
        
        sidebarSettingsLoaded = true;
        updateSidebarButtonUI();
    } catch (error) {
        console.error('Error initializing sidebar button settings:', error);
    }
}

/**
 * Update sidebar button UI based on current settings
 */
function updateSidebarButtonUI() {
    const select = getSidebarButtonsSelect();
    if (!select) {
        return;
    }
    Object.keys(SIDEBAR_BUTTONS).forEach(key => {
        const option = Array.from(select.options || []).find((candidate) => candidate.value === key);
        if (option) {
            option.selected = currentSidebarFeatureAvailability[key] !== false
                && currentSidebarButtonVisibility[key] !== false;
        }
    });
    ensureSidebarButtonsMultiselect();
    applySidebarSettingsRowAvailability();
    select._multiSelect?.syncFromSelect?.();
    refreshSidebarButtonsMultiselectTranslations();
}

/**
 * Disable the multi-select choices while a complete selection is being saved.
 */
function setSidebarButtonsSavingState(isSaving) {
    const select = getSidebarButtonsSelect();
    const wrapper = select?._multiSelect?.wrapper;
    const trigger = wrapper?.querySelector('.model-settings-multiselect-trigger');
    if (trigger) {
        trigger.setAttribute('aria-busy', isSaving ? 'true' : 'false');
    }
    wrapper?.querySelectorAll(
        '.model-settings-multiselect-option, .model-settings-multiselect-action-btn'
    ).forEach((button) => {
        button.disabled = isSaving || (
            button.classList.contains('model-settings-multiselect-option')
            && currentSidebarFeatureAvailability[button.dataset.value] === false
        );
    });
}

/**
 * Persist one complete multi-selection as the existing boolean-map API value.
 */
async function handleSidebarButtonSelectionChange() {
    if (sidebarButtonsSaveInFlight) {
        updateSidebarButtonUI();
        return;
    }
    const select = getSidebarButtonsSelect();
    if (!select) {
        return;
    }

    const previousVisibility = { ...currentSidebarButtonVisibility };
    const selectedKeys = new Set(
        Array.from(select.selectedOptions || [], (option) => option.value)
    );
    Object.keys(SIDEBAR_BUTTONS).forEach((key) => {
        if (currentSidebarFeatureAvailability[key] !== false) {
            currentSidebarButtonVisibility[key] = selectedKeys.has(key);
        }
    });

    // Save to localStorage immediately
    saveSidebarVisibilityToStorage(currentSidebarButtonVisibility);
    sidebarButtonsSaveInFlight = true;
    setSidebarButtonsSavingState(true);

    try {
        const response = await window.authedFetch('/api/v1/users/settings/sidebar-button-visibility', {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(currentSidebarButtonVisibility)
        });

        if (!response.ok) {
            throw new Error(`Sidebar button update failed (${response.status})`);
        }

        const result = await response.json();
        if (result.status !== 'success' || !result.sidebar_button_visibility) {
            throw new Error('Unexpected sidebar button update response');
        }
        currentSidebarButtonVisibility = { ...result.sidebar_button_visibility };
        saveSidebarVisibilityToStorage(currentSidebarButtonVisibility);
        updateSidebarButtonUI();
        if (typeof window.SharedDataCache?.clear === 'function') {
            window.SharedDataCache.clear('userSettingsInit');
        }
        // Apply the saved value immediately so the sidebar and settings control
        // always reflect the same persisted selection.
        if (typeof applySidebarButtonVisibility === 'function') {
            applySidebarButtonVisibility(currentSidebarButtonVisibility);
        }
    } catch (error) {
        console.error('Error updating sidebar button visibility:', error);
        handleUpdateError(previousVisibility);
    } finally {
        sidebarButtonsSaveInFlight = false;
        setSidebarButtonsSavingState(false);
    }
}

function handleUpdateError(previousVisibility) {
    console.error('Failed to update sidebar button visibility');
    if (typeof notifyError === 'function') {
        notifyError(sidebarButtonsT(
            'us_sidebar_buttons_update_failed',
            'Failed to update sidebar button visibility'
        ));
    }
    // Revert the change
    currentSidebarButtonVisibility = { ...previousVisibility };
    saveSidebarVisibilityToStorage(currentSidebarButtonVisibility);
    updateSidebarButtonUI();
}

/**
 * Initialize sidebar button settings event listeners
 */
function initializeSidebarButtonEventListeners() {
    // Guard against redundant initialization
    if (eventListenersInitialized) {
        return;
    }
    
    const select = ensureSidebarButtonsMultiselect();
    if (!select) {
        console.warn('Sidebar button multi-select not found');
        return;
    }
    select.addEventListener('change', handleSidebarButtonSelectionChange);
    document.addEventListener('i18n:updated', refreshSidebarButtonsMultiselectTranslations);
    
    eventListenersInitialized = true;
}

let settingsObserver = null;

// Single initialization function
async function initializeSidebarButtonSettingsOnce(initialData = null) {
    // Disconnect existing observer if any
    if (settingsObserver) {
        settingsObserver.disconnect();
    }
    await initializeSidebarButtonSettings(initialData);
    initializeSidebarButtonEventListeners();
}

// Expose functions to window object for external initialization
if (typeof window !== 'undefined') {
    window.initializeSidebarButtonSettings = initializeSidebarButtonSettings;
    window.initializeSidebarButtonEventListeners = initializeSidebarButtonEventListeners;
    window.initializeSidebarButtonSettingsOnce = initializeSidebarButtonSettingsOnce;
    window.applySidebarSettingsRowAvailability = applySidebarSettingsRowAvailability;
}
