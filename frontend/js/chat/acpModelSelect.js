(() => {
    'use strict';

    /**
     * ACP exposes three independent session settings. They intentionally use
     * the chat composer dropdown primitives instead of another selector
     * implementation. The data and persistence behavior stays ACP-specific;
     * the button, menu, focus, and close behavior are shared with Thinking.
     */
    const CONTROL_DEFINITIONS = {
        model: {
            containerId: 'chatBoxAcpModelContainer',
            triggerId: 'chatBoxAcpModelTrigger',
            valueId: 'chatBoxAcpModelValue',
            menuId: 'chatBoxAcpModelMenu',
            optionsKey: 'models',
            currentKey: 'current_model_id',
            requestKey: 'acp_model_id',
        },
        reasoning: {
            containerId: 'chatBoxAcpReasoningContainer',
            triggerId: 'chatBoxAcpReasoningTrigger',
            valueId: 'chatBoxAcpReasoningValue',
            menuId: 'chatBoxAcpReasoningMenu',
            optionsKey: 'reasoning_efforts',
            currentKey: 'current_reasoning_effort',
            requestKey: 'acp_reasoning_effort',
        },
        security: {
            containerId: 'chatBoxAcpSecurityContainer',
            triggerId: 'chatBoxAcpSecurityTrigger',
            valueId: 'chatBoxAcpSecurityValue',
            menuId: 'chatBoxAcpSecurityMenu',
            optionsKey: 'security_levels',
            currentKey: 'current_security_level',
            requestKey: 'acp_security_level',
        },
    };

    const state = {
        modelId: null,
        chatId: null,
        sessionId: null,
        controls: Object.fromEntries(
            Object.keys(CONTROL_DEFINITIONS).map((key) => [
                key,
                { options: [], selected: null },
            ]),
        ),
        requestVersion: 0,
        resolvedRequestVersion: 0,
        discoveryPending: true,
        syncTimer: null,
        persistenceInFlight: false,
        pendingPersistenceBody: null,
        reasoningEffortsByModel: {},
        dropdowns: {},
    };

    const CHATBOX_DROPDOWN_GROUP = 'chat-box-composer-dropdowns';
    const el = (id) => document.getElementById(id);
    const t = (key, fallback) => window.getTranslation?.(key, fallback) || fallback;

    /**
     * Return the small static surface shared by every ACP dropdown. Keeping
     * this markup in index.html makes the controls easy to inspect and keeps
     * them identical to the Thinking control in the composer.
     */
    function controlParts(controlName) {
        const definition = CONTROL_DEFINITIONS[controlName];
        if (!definition) return {};
        return {
            container: el(definition.containerId),
            trigger: el(definition.triggerId),
            value: el(definition.valueId),
            menu: el(definition.menuId),
        };
    }

    function selectedAcpConnection() {
        if (document.body?.classList?.contains('split-screen-active')) return null;
        const model = window.getSelectedModel?.();
        return (model?.provider || model?.provider_type) === 'acp' && model?.acp_terminal_available === true
            ? model
            : null;
    }

    function isChatSurfaceActive() {
        const chatContainer = el('chatContainer');
        return Boolean(
            chatContainer
            && !chatContainer.hidden
            && chatContainer.style.display !== 'none'
            && getComputedStyle(chatContainer).display !== 'none'
        );
    }

    function setControlsVisible(visible) {
        const controls = el('chatBoxAcpSessionControls');
        if (controls) controls.hidden = !visible;
        if (!visible) closeAllControls();
    }

    function setControlVisible(controlName, visible) {
        const { container } = controlParts(controlName);
        if (container) container.hidden = !visible;
        if (!visible) state.dropdowns[controlName]?.close({ reason: 'hidden' });
    }

    function closeAllControls(options = {}) {
        Object.values(state.dropdowns).forEach((dropdown) => dropdown?.close(options));
    }

    function normalizeOptions(values) {
        if (!Array.isArray(values)) return [];
        const seen = new Set();
        return values.flatMap((value) => {
            const id = String(value?.id || '').trim();
            if (!id || seen.has(id)) return [];
            seen.add(id);
            return [{
                id,
                name: String(value?.name || id),
                description: String(value?.description || '').trim() || null,
            }];
        });
    }

    function reasoningOptionName(option) {
        const id = String(option?.id || '').trim().toLowerCase();
        const translationKey = {
            minimal: 'llm.shared.settings.reasoning_effort.option.minimal',
            low: 'llm.shared.settings.reasoning_effort.option.low',
            medium: 'llm.shared.settings.reasoning_effort.option.medium',
            high: 'llm.shared.settings.reasoning_effort.option.high',
            xhigh: 'llm.shared.settings.reasoning_effort.option.xhigh',
            max: 'llm.shared.settings.reasoning_effort.option.max',
            ultra: 'acp_reasoning_effort_option_ultra',
        }[id];
        return translationKey ? t(translationKey, option.name || option.id) : option.name;
    }

    function optionDisplayName(controlName, option) {
        return controlName === 'reasoning' ? reasoningOptionName(option) : option.name;
    }

    function availableOptionsFor(controlName) {
        const control = state.controls[controlName];
        if (!control) return [];
        if (controlName !== 'reasoning') return control.options;

        const modelId = state.controls.model?.selected;
        const supported = state.reasoningEffortsByModel[modelId];
        return control.options.filter(
            (option) => !Array.isArray(supported) || supported.includes(option.id),
        );
    }

    function updateSelectedDescription(controlName) {
        const control = state.controls[controlName];
        const { trigger } = controlParts(controlName);
        if (!control || !trigger) return;
        const selected = control.options.find((option) => option.id === control.selected);
        const text = String(selected?.description || '').trim();
        if (text) {
            trigger.title = text;
        } else {
            trigger.removeAttribute('title');
        }
    }

    /**
     * Build the same menu-item shape used by the Thinking dropdown. The
     * option description remains available as the trigger tooltip, while the
     * menu stays intentionally compact and label-only.
     */
    function createOptionButton(controlName, option, selected) {
        const item = document.createElement('div');
        item.className = 'select-dropdown-item';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'select-dropdown-button';
        button.dataset.value = option.id;
        button.setAttribute('role', 'menuitemradio');
        button.setAttribute('aria-checked', selected ? 'true' : 'false');

        const name = document.createElement('span');
        name.textContent = optionDisplayName(controlName, option);
        button.append(name);

        if (selected) {
            const checkIcon = document.createElement('span');
            checkIcon.setAttribute('aria-hidden', 'true');
            checkIcon.innerHTML = window.Icons?.check || '✓';
            button.append(checkIcon);
        }

        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            handleSelectionChange(controlName, option.id);
            state.dropdowns[controlName]?.close({ reason: 'selection', restoreFocus: true });
        });
        item.append(button);
        return item;
    }

    /**
     * Render options directly into the shared composer dropdown. This avoids a
     * second selector widget and leaves sizing/placement to chatBox/chatBox.css.
     */
    function renderControl(controlName) {
        const control = state.controls[controlName];
        const { trigger, value, menu } = controlParts(controlName);
        if (!control || !trigger || !value || !menu) return;
        const availableOptions = availableOptionsFor(controlName);
        if (!availableOptions.length) {
            setControlVisible(controlName, false);
            return;
        }

        const selected = availableOptions.some((option) => option.id === control.selected)
            ? control.selected
            : availableOptions[0].id;
        control.selected = selected;
        value.removeAttribute('data-i18n');
        value.textContent = optionDisplayName(
            controlName,
            availableOptions.find((option) => option.id === selected),
        );
        trigger.disabled = false;
        menu.replaceChildren();
        for (const option of availableOptions) {
            menu.append(createOptionButton(controlName, option, option.id === selected));
        }

        setControlVisible(controlName, true);
        updateSelectedDescription(controlName);
    }

    function setSingleOption(controlName, key, fallback) {
        const { trigger, value, menu } = controlParts(controlName);
        if (!trigger || !value || !menu) return;
        state.dropdowns[controlName]?.close({ reason: 'state' });
        value.textContent = t(key, fallback);
        value.dataset.i18n = key;
        trigger.disabled = true;
        trigger.removeAttribute('title');
        menu.replaceChildren();
    }

    function reset({ hide = true } = {}) {
        state.requestVersion += 1;
        state.resolvedRequestVersion = 0;
        state.discoveryPending = true;
        state.modelId = null;
        state.chatId = null;
        state.sessionId = null;
        state.pendingPersistenceBody = null;
        state.reasoningEffortsByModel = {};
        closeAllControls();
        for (const controlName of Object.keys(CONTROL_DEFINITIONS)) {
            state.controls[controlName] = { options: [], selected: null };
            setSingleOption(controlName, 'acp_control_loading', 'Loading…');
            setControlVisible(controlName, false);
        }
        if (hide) setControlsVisible(false);
    }

    async function fetchChatDetail(chatId) {
        if (!chatId) return null;
        const response = await window.authedFetch(
            `/api/v1/chats/detail?chat_id=${encodeURIComponent(chatId)}`,
            { method: 'GET' },
        );
        return response.ok ? response.json() : null;
    }

    function sessionEntryFromDetail(detail, modelId) {
        const sessions = detail?.meta?.acp_sessions;
        const entry = sessions && typeof sessions === 'object' ? sessions[modelId] : null;
        return entry && typeof entry === 'object' ? entry : null;
    }

    function splitCombinedModel(value) {
        const normalized = String(value || '').trim();
        const match = normalized.match(/^(.*)\[([^\]]+)\]$/);
        return match ? { model: match[1].trim(), reasoning: match[2].trim() } : null;
    }

    function populateControls(payload, saved) {
        let savedModel = String(saved?.selected_model_id || '').trim() || null;
        let savedReasoning = String(saved?.selected_reasoning_effort || '').trim() || null;
        const combined = splitCombinedModel(savedModel);
        if (combined) {
            savedModel = combined.model;
            savedReasoning ||= combined.reasoning;
        }
        const savedValues = {
            model: savedModel,
            security: String(saved?.selected_security_level || '').trim() || null,
            reasoning: savedReasoning,
        };
        state.reasoningEffortsByModel = (
            payload?.reasoning_efforts_by_model
            && typeof payload.reasoning_efforts_by_model === 'object'
            && !Array.isArray(payload.reasoning_efforts_by_model)
        ) ? payload.reasoning_efforts_by_model : {};

        for (const [controlName, definition] of Object.entries(CONTROL_DEFINITIONS)) {
            const options = normalizeOptions(payload?.[definition.optionsKey]);
            const advertised = String(payload?.[definition.currentKey] || '').trim();
            const preferred = savedValues[controlName] || advertised || options[0]?.id || null;
            state.controls[controlName] = {
                options,
                selected: options.some((option) => option.id === preferred)
                    ? preferred
                    : options[0]?.id || null,
            };
            renderControl(controlName);
        }
        setControlsVisible(
            Object.values(state.controls).some((control) => control.options.length),
        );
    }

    async function sync({ force = false } = {}) {
        const connection = selectedAcpConnection();
        if (!connection || !isChatSurfaceActive()) {
            reset();
            return;
        }

        const modelId = String(connection.model_id || '').trim();
        const chatId = String(el('chatContainer')?.getAttribute('data-chat-id') || '').trim();
        const hasControls = Object.values(state.controls).some(
            (control) => control.options.length,
        );
        if (!force && state.modelId === modelId && state.chatId === chatId && hasControls) {
            setControlsVisible(true);
            return;
        }

        // Preserve an already discovered session while its first chat ID is
        // assigned during generation.
        if (window.isGenerating && state.modelId === modelId && hasControls) {
            state.chatId = chatId;
            setControlsVisible(true);
            return;
        }

        const requestVersion = ++state.requestVersion;
        state.resolvedRequestVersion = 0;
        state.discoveryPending = true;
        state.modelId = modelId;
        state.chatId = chatId;
        state.sessionId = null;
        state.reasoningEffortsByModel = {};
        setControlsVisible(true);
        for (const controlName of Object.keys(CONTROL_DEFINITIONS)) {
            state.controls[controlName] = { options: [], selected: null };
            setControlVisible(controlName, true);
            setSingleOption(controlName, 'acp_control_loading', 'Loading…');
        }

        try {
            const detail = await fetchChatDetail(chatId);
            if (requestVersion !== state.requestVersion) return;
            const saved = sessionEntryFromDetail(detail, modelId);
            const savedSessionId = String(saved?.session_id || '').trim() || null;
            const response = await window.authedFetch('/api/v1/remote-connections/acp/models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_id: modelId,
                    session_id: savedSessionId,
                }),
            });
            if (!response.ok) {
                throw new Error(`ACP session control discovery failed (${response.status})`);
            }
            const payload = await response.json();
            if (requestVersion !== state.requestVersion) return;
            state.sessionId = String(payload?.session_id || '').trim() || null;
            populateControls(payload, saved);
            state.resolvedRequestVersion = requestVersion;
            state.discoveryPending = false;
        } catch (error) {
            if (requestVersion !== state.requestVersion) return;
            state.sessionId = null;
            state.resolvedRequestVersion = 0;
            state.discoveryPending = true;
            for (const controlName of Object.keys(CONTROL_DEFINITIONS)) {
                state.controls[controlName] = { options: [], selected: null };
                setSingleOption(controlName, 'acp_controls_error', 'Controls unavailable');
                setControlVisible(controlName, controlName === 'model');
            }
            setControlsVisible(true);
            console.error('Failed to load ACP session controls', error);
        }
    }

    function scheduleSync(options = {}) {
        clearTimeout(state.syncTimer);
        state.syncTimer = setTimeout(() => {
            sync(options).catch((error) => {
                console.error('Failed to synchronize ACP session controls', error);
            });
        }, 80);
    }

    function selectionPersistenceBody() {
        if (
            state.discoveryPending
            || state.resolvedRequestVersion !== state.requestVersion
            || !state.chatId
            || !state.modelId
            || !state.sessionId
        ) {
            return null;
        }
        const body = {
            chat_id: state.chatId,
            model_id: state.modelId,
            session_id: state.sessionId,
        };
        for (const [controlName, definition] of Object.entries(CONTROL_DEFINITIONS)) {
            const selected = state.controls[controlName]?.selected;
            if (selected) body[definition.requestKey] = selected;
        }
        return body;
    }

    async function persistSelection() {
        const body = selectionPersistenceBody();
        if (!body) return;
        // Coalesce rapid control changes into the latest complete snapshot.
        // Only one write is active, so an older response can never finish
        // after and overwrite the newest UI state.
        state.pendingPersistenceBody = body;
        if (state.persistenceInFlight) return;
        state.persistenceInFlight = true;
        try {
            while (state.pendingPersistenceBody) {
                const nextBody = state.pendingPersistenceBody;
                state.pendingPersistenceBody = null;
                try {
                    const response = await window.authedFetch('/api/v1/remote-connections/acp/model-selection', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(nextBody),
                    });
                    if (!response.ok) {
                        throw new Error(`ACP session control persistence failed (${response.status})`);
                    }
                } catch (error) {
                    // Every send also carries the selections, so a transient
                    // persistence failure must not disable the composer.
                    console.error('Failed to persist ACP session controls', error);
                }
            }
        } finally {
            state.persistenceInFlight = false;
        }
    }

    function handleSelectionChange(controlName, value) {
        const normalized = String(value || '').trim();
        const control = state.controls[controlName];
        if (!normalized || !control.options.some((option) => option.id === normalized)) return;
        control.selected = normalized;
        renderControl(controlName);
        if (controlName === 'model') {
            // A combined-model ACP option can change available reasoning levels.
            renderControl('reasoning');
        }
        persistSelection();
    }

    function applyConfigUpdate(payload = {}) {
        if (!payload || typeof payload !== 'object') return;
        if (
            payload.reasoning_efforts_by_model
            && typeof payload.reasoning_efforts_by_model === 'object'
            && !Array.isArray(payload.reasoning_efforts_by_model)
        ) {
            state.reasoningEffortsByModel = payload.reasoning_efforts_by_model;
        }
        for (const [controlName, definition] of Object.entries(CONTROL_DEFINITIONS)) {
            if (Array.isArray(payload[definition.optionsKey])) {
                state.controls[controlName].options = normalizeOptions(
                    payload[definition.optionsKey],
                );
            }
            const current = String(payload[definition.currentKey] || '').trim();
            if (
                current
                && state.controls[controlName].options.some((option) => option.id === current)
            ) {
                state.controls[controlName].selected = current;
            }
            renderControl(controlName);
        }
        setControlsVisible(
            Object.values(state.controls).some((control) => control.options.length),
        );
    }

    function bindControl(controlName) {
        const { container, trigger, menu } = controlParts(controlName);
        if (!container || !trigger || !menu || typeof window.createDropdownController !== 'function') {
            return false;
        }

        // The shared controller supplies the exact same outside-click,
        // Escape, aria-expanded, and open-class behavior as Thinking.
        state.dropdowns[controlName] = window.createDropdownController({
            id: `chat-box-acp-${controlName}-dropdown`,
            group: CHATBOX_DROPDOWN_GROUP,
            trigger,
            dropdown: menu,
            root: container,
            escapePriority: 90,
        });
        return true;
    }

    function initialize() {
        let available = false;
        for (const controlName of Object.keys(CONTROL_DEFINITIONS)) {
            if (!bindControl(controlName)) continue;
            available = true;
            setSingleOption(controlName, 'acp_control_loading', 'Loading…');
        }
        if (!available) return;

        window.addEventListener('modelSelect:changed', () => {
            reset();
            scheduleSync({ force: true });
        });
        window.addEventListener('splitScreen:stateChanged', () => {
            reset();
            scheduleSync({ force: true });
        });
        document.addEventListener('i18n:updated', () => {
            for (const controlName of Object.keys(CONTROL_DEFINITIONS)) {
                renderControl(controlName);
            }
        });

        const chatContainer = el('chatContainer');
        if (chatContainer && typeof MutationObserver === 'function') {
            const observer = new MutationObserver((mutations) => {
                if (mutations.some((mutation) => (
                    mutation.attributeName === 'data-chat-id'
                    || mutation.attributeName === 'style'
                    || mutation.attributeName === 'hidden'
                ))) {
                    scheduleSync();
                }
            });
            observer.observe(chatContainer, {
                attributes: true,
                attributeFilter: ['data-chat-id', 'style', 'hidden'],
            });
        }
        scheduleSync();
    }

    function selectionStateIsCurrent() {
        const selectedConnection = selectedAcpConnection();
        return !(
            state.discoveryPending
            || state.resolvedRequestVersion !== state.requestVersion
            || String(selectedConnection?.model_id || '') !== state.modelId
        );
    }

    function currentSelection(controlName) {
        if (!selectionStateIsCurrent()) return null;
        return state.controls[controlName]?.selected || null;
    }

    window.getSelectedAcpModelId = () => currentSelection('model');
    window.getSelectedAcpSecurityLevel = () => currentSelection('security');
    window.getSelectedAcpReasoningEffort = () => currentSelection('reasoning');
    window.getSelectedAcpSessionId = () => (
        selectionStateIsCurrent() ? state.sessionId : null
    );
    window.syncAcpModelSelect = (options) => sync(options);
    window.applyAcpConfigUpdate = applyConfigUpdate;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
})();
