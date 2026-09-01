(() => {
    const LLM_ACCESS_FIELDS = [
        'first_name',
        'language',
        'country',
        'timezone',
        'location'
    ];

    let currentPermissions = {};
    let currentPreset = 'none';
    let isSaving = false;

    const llmAccessT = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const getModeFromPermissions = (permissions) => {
        if (!permissions || typeof permissions !== 'object') {
            return 'none';
        }
        const values = LLM_ACCESS_FIELDS.map(field => Boolean(permissions[field]));
        const allTrue = values.every(Boolean);
        const allFalse = values.every(value => value === false);

        if (allTrue) return 'all';
        if (allFalse) return 'none';
        return 'custom';
    };

    const getActiveMode = () => {
        if (['none', 'all', 'custom'].includes(currentPreset)) {
            return currentPreset;
        }
        return getModeFromPermissions(currentPermissions);
    };

    const normalizePresetValue = (value) => {
        if (!value && value !== 0) {
            return null;
        }
        if (typeof value === 'string') {
            return value.trim().toLowerCase();
        }
        if (value && typeof value === 'object' && typeof value.value === 'string') {
            return value.value.toLowerCase();
        }
        return null;
    };

    const createPermissionsObject = (mode, customValues = {}) => {
        const result = {};
        LLM_ACCESS_FIELDS.forEach(field => {
            if (mode === 'all') {
                result[field] = true;
            } else if (mode === 'none') {
                result[field] = false;
            } else {
                result[field] = Boolean(customValues[field]);
            }
        });
        return result;
    };

    const tryParsePermissionsObject = (value) => {
        if (!value) return null;

        if (typeof value === 'string') {
            const trimmed = value.trim();
            if (!trimmed) {
                return null;
            }
            const lowered = trimmed.toLowerCase();
            if (lowered === 'true' || lowered === 'all') {
                return createPermissionsObject('all');
            }
            if (lowered === 'false' || lowered === 'none') {
                return createPermissionsObject('none');
            }
            try {
                const parsed = JSON.parse(trimmed);
                if (parsed && typeof parsed === 'object') {
                    return tryParsePermissionsObject(parsed);
                }
            } catch (_) {
                return null;
            }
            return null;
        }

        if (typeof value === 'boolean') {
            return createPermissionsObject(value ? 'all' : 'none');
        }

        if (Array.isArray(value)) {
            return value.reduce((acc, field) => {
                if (typeof field === 'string' && LLM_ACCESS_FIELDS.includes(field)) {
                    acc[field] = true;
                }
                return acc;
            }, { ...createPermissionsObject('none') });
        }

        if (value && typeof value === 'object') {
            const normalized = {};
            LLM_ACCESS_FIELDS.forEach(field => {
                normalized[field] = Boolean(value[field]);
            });
            return normalized;
        }

        return null;
    };

    const normalizePermissionsValue = (value, presetHint = null) => {
        const parsed = tryParsePermissionsObject(value);
        if (parsed) {
            return parsed;
        }
        const normalizedPreset = normalizePresetValue(presetHint);
        if (normalizedPreset === 'all' || normalizedPreset === 'none') {
            return createPermissionsObject(normalizedPreset);
        }
        if (typeof value === 'boolean') {
            return createPermissionsObject(value ? 'all' : 'none');
        }
        if (value && typeof value === 'object') {
            const normalized = {};
            LLM_ACCESS_FIELDS.forEach(field => {
                normalized[field] = Boolean(value[field]);
            });
            return normalized;
        }
        return createPermissionsObject('none');
    };

    const setSavingState = (saving) => {
        const container = document.getElementById('llmAccessControls');
        isSaving = saving;
        if (container) {
            container.classList.toggle('is-saving', saving);
        }
    };

    const updateUI = () => {
        const container = document.getElementById('llmAccessControls');
        const fieldsContainer = document.getElementById('llmAccessFields');
        if (!container) return;

        const mode = getActiveMode();

        container.querySelectorAll('.llm-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });

        if (fieldsContainer) {
            fieldsContainer.style.display = mode === 'custom' ? 'grid' : 'none';
        }

        container.querySelectorAll('.llm-field-input').forEach(input => {
            const field = input.dataset.field;
            if (field && currentPermissions) {
                input.checked = Boolean(currentPermissions[field]);
            }
        });
    };

    const savePermissions = async (permissions, presetOverride = null) => {
        if (isSaving) return;

        const previousState = {
            permissions: { ...currentPermissions },
            preset: currentPreset,
        };

        currentPermissions = { ...permissions };
        currentPreset = presetOverride || getModeFromPermissions(currentPermissions);
        updateUI();
        setSavingState(true);

        try {
            const response = await window.authedFetch('/api/v1/users/settings/toogle', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ allow_llm_to_access_personal_information: currentPermissions })
            });

            if (!response.ok) {
                throw new Error(llmAccessT('user_settings_llm_access_save_failed', 'Failed to save LLM access permissions'));
            }
        } catch (error) {
            console.error('Failed to save LLM access permissions:', error);
            if (typeof notifyError === 'function') {
                notifyError(llmAccessT('user_settings_llm_access_save_failed', 'Failed to save LLM access permissions'));
            }
            currentPermissions = previousState.permissions;
            currentPreset = previousState.preset;
            updateUI();
        } finally {
            setSavingState(false);
        }
    };

    const savePreset = async (mode) => {
        if (isSaving) return;

        const previousState = {
            permissions: { ...currentPermissions },
            preset: currentPreset,
        };

        currentPermissions = createPermissionsObject(mode, currentPermissions);
        currentPreset = mode;
        updateUI();
        setSavingState(true);

        try {
            const response = await window.authedFetch('/api/v1/users/settings/toogle', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ allow_llm_to_access_personal_information_preset: mode })
            });

            if (!response.ok) {
                throw new Error(llmAccessT('user_settings_llm_preset_save_failed', 'Failed to save LLM preset'));
            }
        } catch (error) {
            console.error('Failed to save LLM preset:', error);
            if (typeof notifyError === 'function') {
                notifyError(llmAccessT('user_settings_llm_preset_save_failed', 'Failed to save LLM preset'));
            }
            currentPermissions = previousState.permissions;
            currentPreset = previousState.preset;
            updateUI();
        } finally {
            setSavingState(false);
        }
    };

    const handleModeClick = (event) => {
        const btn = event.target.closest('.llm-mode-btn');
        if (!btn || isSaving) return;

        const mode = btn.dataset.mode;
        if (!mode) return;

        if (mode === 'custom') {
            currentPreset = 'custom';
            updateUI();
            return;
        }

        savePreset(mode);
    };

    const handleFieldToggle = (event) => {
        const input = event.target;
        if (!input.classList.contains('llm-field-input') || isSaving) return;

        const field = input.dataset.field;
        if (!field) return;

        const newPermissions = { ...currentPermissions, [field]: input.checked };
        savePermissions(newPermissions, 'custom');
    };

    const initLLMAccessControls = (optionsOrPermissions, legacyPreset) => {
        let permissions;
        let preset = legacyPreset;

        if (
            optionsOrPermissions &&
            typeof optionsOrPermissions === 'object' &&
            (Object.prototype.hasOwnProperty.call(optionsOrPermissions, 'permissions') ||
             Object.prototype.hasOwnProperty.call(optionsOrPermissions, 'preset'))
        ) {
            permissions = optionsOrPermissions.permissions;
            preset = optionsOrPermissions.preset ?? legacyPreset;
        } else {
            permissions = optionsOrPermissions;
        }

        const normalizedPreset = normalizePresetValue(preset);
        currentPermissions = normalizePermissionsValue(permissions, normalizedPreset);
        currentPreset = normalizedPreset || getModeFromPermissions(currentPermissions);
        updateUI();

        const container = document.getElementById('llmAccessControls');
        if (!container || container.dataset.bound === 'true') return;

        container.addEventListener('click', handleModeClick);
        container.addEventListener('change', handleFieldToggle);
        container.dataset.bound = 'true';
    };

    window.initLLMAccessControls = initLLMAccessControls;
})();
