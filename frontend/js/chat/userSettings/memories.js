(() => {
    const state = {
        initialized: false,
        visible: false,
        isLoading: false,
        isSaving: false,
        settings: null,
    };

    const DOM = {
        get page() { return document.getElementById('memorySettingsPage'); },
        get enabledToggle() { return document.getElementById('userSettingsMemoryEnabledToggle'); },
        get includeInContextToggle() { return document.getElementById('userSettingsMemoryIncludeContextToggle'); },
        get autoCreateToggle() { return document.getElementById('userSettingsMemoryAutoCreateToggle'); },
        get manageWorkspaceButton() { return document.getElementById('memorySettingsManageWorkspaceButton'); },
    };

    const settingControls = [
        ['enabled', DOM.enabledToggle],
        ['include_in_context', DOM.includeInContextToggle],
        ['auto_create', DOM.autoCreateToggle],
    ];

    const t = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const setControlsDisabled = (disabled) => {
        [
            DOM.enabledToggle,
            DOM.includeInContextToggle,
            DOM.autoCreateToggle,
            DOM.manageWorkspaceButton,
        ].forEach((element) => {
            if (element) {
                element.disabled = disabled;
            }
        });
    };

    const render = () => {
        if (!state.settings) {
            return;
        }

        settingControls.forEach(([key, element]) => {
            if (element) element.checked = Boolean(state.settings[key]);
        });
    };

    const openWorkspaceMemories = () => {
        if (typeof window.closeUserSettings === 'function') {
            window.closeUserSettings({
                immediate: true,
                onClosed: () => {
                    if (typeof window.showWorkspaceContainer === 'function') {
                        window.showWorkspaceContainer({ tab: 'memories' });
                    }
                },
            });
            return;
        }

        if (typeof window.showWorkspaceContainer === 'function') {
            window.showWorkspaceContainer({ tab: 'memories' });
        }
    };

    const updateSetting = async (key, value) => {
        if (state.isSaving || !window.MemoriesAPI) {
            return;
        }

        const previousSettings = state.settings ? { ...state.settings } : null;
        state.isSaving = true;
        setControlsDisabled(true);

        try {
            state.settings = await window.MemoriesAPI.updateSettings({ [key]: value });
            render();
            if (typeof notifySuccess === 'function') {
                notifySuccess(t('workspace_memories_success_settings_updated', 'Memory settings updated'), 2000);
            }
        } catch (error) {
            if (previousSettings) {
                state.settings = previousSettings;
                render();
            }
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('workspace_memories_error_update_settings', 'Failed to update memory settings'));
            }
        } finally {
            state.isSaving = false;
            setControlsDisabled(state.isLoading);
        }
    };

    const bindEvents = () => {
        settingControls.forEach(([key, element]) => {
            element?.addEventListener('change', (event) => {
                updateSetting(key, Boolean(event.target.checked));
            });
        });
        DOM.manageWorkspaceButton?.addEventListener('click', openWorkspaceMemories);
    };

    const init = () => {
        if (state.initialized) {
            return;
        }
        state.initialized = true;
        bindEvents();
    };

    const load = async () => {
        init();
        if (!state.visible || !window.MemoriesAPI) {
            return;
        }

        state.isLoading = true;
        setControlsDisabled(true);

        try {
            state.settings = await window.MemoriesAPI.fetchSettings();
            render();
        } catch (error) {
            if (typeof notifyError === 'function') {
                notifyError(error.message || t('workspace_memories_error_load_settings', 'Failed to load memory settings'));
            }
        } finally {
            state.isLoading = false;
            setControlsDisabled(state.isSaving);
        }
    };

    const setVisibility = (visible) => {
        state.visible = Boolean(visible);
        if (DOM.page) {
            DOM.page.style.display = state.visible ? '' : 'none';
        }
    };

    if (typeof window !== 'undefined') {
        window.MemorySettingsPage = {
            init,
            load,
            setVisibility,
        };
    }
})();
