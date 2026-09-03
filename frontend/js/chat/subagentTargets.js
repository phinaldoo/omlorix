(function initializeSubagentTargetsModule() {
    'use strict';

    const MAX_SELECTED_TARGETS = 20;
    const NON_CHAT_CAPABILITIES = new Set([
        'image_generation',
        'video_generation',
        'audio_generation',
        'music_generation',
        'transcription',
        'tts',
    ]);

    const state = {
        availability: 'unknown',
        automatic: true,
        selected: new Map(),
        targets: [],
        parentModelId: '',
        query: '',
        requestSequence: 0,
        open: false,
    };

    let wrapper = null;
    let trigger = null;
    let triggerLabel = null;
    let menu = null;
    let searchInput = null;
    let targetList = null;
    let emptyState = null;
    let retryButton = null;
    let automaticButton = null;

    function translate(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function formatTranslation(key, fallback, variables) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, variables);
        }
        return String(fallback || key).replace(/\{(\w+)\}/g, (_match, token) => (
            variables && Object.prototype.hasOwnProperty.call(variables, token)
                ? String(variables[token])
                : ''
        ));
    }

    function pluralTranslation(baseKey, count, oneFallback, otherFallback) {
        let category = Number(count) === 1 ? 'one' : 'other';
        try {
            category = new Intl.PluralRules(document.documentElement?.lang || 'en')
                .select(Math.abs(Number(count) || 0));
        } catch (_error) {
            // The one/other fallback above covers hosts without Intl support.
        }
        const categoryKey = `${baseKey}_${category}`;
        const missing = `__missing_translation_${categoryKey}__`;
        const localizedCategory = translate(categoryKey, missing);
        const fallback = category === 'one' ? oneFallback : otherFallback;
        const template = localizedCategory === missing
            ? translate(baseKey, fallback)
            : localizedCategory;
        return String(template).replace('{count}', String(count));
    }

    function targetKey(target) {
        const type = String(target?.type || '').trim().toLowerCase();
        const id = String(target?.id || '').trim();
        return type && id ? `${type}:${id}` : '';
    }

    function normalizeTarget(target) {
        const rawType = String(target?.type || target?.model_kind || '').trim().toLowerCase();
        const type = rawType === 'base' ? 'model' : rawType;
        const id = String(target?.id || target?.model_id || '').trim();
        if (!id || !['model', 'agent'].includes(type)) return null;
        return {
            type,
            id,
            name: String(target?.name || id),
            description: String(target?.description || ''),
            provider: String(target?.provider || ''),
            base_model_name: String(
                target?.base_model_name
                || (type === 'agent' ? target?.description : target?.name)
                || '',
            ),
            is_shared: Boolean(target?.is_shared),
        };
    }

    function isChatCapableTarget(target) {
        const capabilities = Array.isArray(target?.capabilities)
            ? target.capabilities.map((value) => String(value || '').trim().toLowerCase())
            : [];
        return !capabilities.some((capability) => NON_CHAT_CAPABILITIES.has(capability));
    }

    function modelSupportsSubagents(model) {
        const tools = Array.isArray(model?.model_select_tools)
            ? model.model_select_tools
            : (Array.isArray(model?.tools) ? model.tools : []);
        return tools.some((tool) => String(tool || '').trim().toLowerCase() === 'subagent');
    }

    function targetMatchesQuery(target) {
        const query = state.query.trim().toLowerCase();
        if (!query) return true;
        return [target.name, target.description, target.provider, target.base_model_name]
            .some((value) => String(value || '').toLowerCase().includes(query));
    }

    async function loadUserModels() {
        if (typeof window.getCachedUserModels === 'function') {
            return window.getCachedUserModels();
        }
        const response = await window.authedFetch('/api/v1/llm/models/user', { method: 'GET' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    }

    function getSelection() {
        if (state.automatic) return null;
        return Array.from(state.selected.values()).map((target) => ({
            type: target.type,
            id: target.id,
        }));
    }

    function getAvailability() {
        return state.availability;
    }

    function setSelection(targets) {
        state.selected.clear();
        if (!Array.isArray(targets)) {
            state.automatic = true;
        } else {
            targets.slice(0, MAX_SELECTED_TARGETS).forEach((target) => {
                const normalized = normalizeTarget(target);
                const key = targetKey(normalized);
                if (normalized && key) state.selected.set(key, normalized);
            });
            state.automatic = state.selected.size === 0;
        }
        render();
    }

    function updateVisibility() {
        if (!wrapper) return;
        const splitActive = Boolean(window.SplitScreenManager?.active);
        wrapper.hidden = !['enabled', 'error'].includes(state.availability) || splitActive;
        if (wrapper.hidden && state.open) closeMenu();
    }

    function updateTrigger() {
        if (!trigger || !triggerLabel) return;
        if (state.availability === 'error') {
            const unavailable = translate(
                'subagent_targets_button_unavailable',
                'Delegation targets unavailable',
            );
            triggerLabel.textContent = unavailable;
            trigger.setAttribute('aria-label', unavailable);
            return;
        }
        const selectedCount = state.selected.size;
        triggerLabel.textContent = state.automatic
            ? translate('subagent_targets_button_automatic', 'Delegation: Automatic')
            : pluralTranslation(
                'subagent_targets_button_selected',
                selectedCount,
                'Delegation: {count} selected',
                'Delegation: {count} selected',
            );
        trigger.setAttribute(
            'aria-label',
            state.automatic
                ? translate('subagent_targets_button_aria_automatic', 'Choose Subagent delegation targets. Any accessible target is currently allowed.')
                : pluralTranslation(
                    'subagent_targets_button_aria_selected',
                    selectedCount,
                    'Choose Subagent delegation targets. {count} target is selected.',
                    'Choose Subagent delegation targets. {count} targets are selected.',
                ),
        );
    }

    function updateStaticLabels() {
        if (!menu) return;
        const title = menu.querySelector('#subagentTargetsTitle');
        if (title) title.textContent = translate('subagent_targets_dialog_title', 'Delegation targets');
        const close = menu.querySelector('#subagentTargetsClose');
        close?.setAttribute('aria-label', translate('subagent_targets_close_aria', 'Close delegation targets'));
        const description = menu.querySelector('.subagent-targets-description');
        if (description) {
            description.textContent = translate(
                'subagent_targets_dialog_description',
                'Choose the exact models and saved Agents this response may delegate to.',
            );
        }
        const automaticName = automaticButton?.querySelector('.subagent-targets-item-name');
        const automaticDetail = automaticButton?.querySelector('.subagent-targets-item-detail');
        if (automaticName) automaticName.textContent = translate('subagent_targets_any_title', 'Any accessible target');
        if (automaticDetail) {
            automaticDetail.textContent = translate(
                'subagent_targets_any_description',
                'Let the parent discover and choose any target you can access.',
            );
        }
        if (searchInput) {
            searchInput.placeholder = translate('subagent_targets_search_placeholder', 'Search models and Agents…');
            searchInput.setAttribute('aria-label', translate('subagent_targets_search_aria', 'Search delegation targets'));
        }
        targetList?.setAttribute('aria-label', translate('subagent_targets_list_aria', 'Available delegation targets'));
        const done = menu.querySelector('.subagent-targets-done');
        if (done) done.textContent = translate('subagent_targets_done', 'Done');
    }

    function createTargetRow(target) {
        const normalized = normalizeTarget(target);
        if (!normalized) return null;
        const key = targetKey(normalized);

        const label = document.createElement('label');
        label.className = 'subagent-targets-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selected.has(key);
        checkbox.setAttribute(
            'aria-label',
            formatTranslation('subagent_targets_select_aria', 'Allow delegation to {name}', { name: normalized.name }),
        );
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) {
                if (state.selected.size >= MAX_SELECTED_TARGETS) {
                    checkbox.checked = false;
                    window.notifyError?.(formatTranslation(
                        'subagent_targets_limit_reached',
                        'Choose up to {count} delegation targets.',
                        { count: MAX_SELECTED_TARGETS },
                    ));
                    return;
                }
                state.selected.set(key, normalized);
                state.automatic = false;
            } else {
                state.selected.delete(key);
                if (!state.selected.size) state.automatic = true;
            }
            render();
        });

        const text = document.createElement('span');
        text.className = 'subagent-targets-item-text';
        const name = document.createElement('span');
        name.className = 'subagent-targets-item-name';
        name.textContent = normalized.name;

        const detail = document.createElement('span');
        detail.className = 'subagent-targets-item-detail';
        const kind = normalized.type === 'agent'
            ? translate('subagent_targets_agent_label', 'Saved Agent')
            : translate('subagent_targets_model_label', 'Model');
        const shared = normalized.is_shared
            ? translate('subagent_targets_shared_label', 'Shared')
            : '';
        detail.textContent = [kind, shared, normalized.base_model_name, normalized.provider]
            .filter(Boolean)
            .join(' · ');

        text.append(name, detail);
        label.append(checkbox, text);
        return label;
    }

    function renderTargetList() {
        if (!targetList) return;
        const loadFailed = state.availability === 'error';
        const visibleTargets = state.targets.filter(targetMatchesQuery);
        targetList.replaceChildren();
        visibleTargets.forEach((target) => {
            const row = createTargetRow(target);
            if (row) targetList.appendChild(row);
        });
        if (emptyState) {
            emptyState.hidden = !loadFailed && visibleTargets.length > 0;
            emptyState.textContent = loadFailed
                ? translate('subagent_targets_load_failed', 'Failed to load delegation targets.')
                : translate('subagent_targets_empty', 'No delegation targets found.');
        }
        if (retryButton) {
            retryButton.hidden = !loadFailed;
            retryButton.textContent = translate('subagent_targets_retry', 'Retry');
        }
        if (automaticButton) {
            automaticButton.hidden = loadFailed;
            automaticButton.setAttribute('aria-pressed', String(state.automatic));
            automaticButton.classList.toggle('active', state.automatic);
        }
        if (searchInput) searchInput.hidden = loadFailed;
        targetList.hidden = loadFailed;
    }

    function render() {
        updateVisibility();
        updateStaticLabels();
        updateTrigger();
        renderTargetList();
    }

    function closeMenu({ restoreFocus = false } = {}) {
        if (!menu || !trigger) return;
        state.open = false;
        menu.classList.remove('open');
        menu.setAttribute('aria-hidden', 'true');
        menu.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        if (restoreFocus) trigger.focus();
    }

    function openMenu() {
        if (!menu || !trigger || wrapper?.hidden) return;
        state.open = true;
        menu.hidden = false;
        window.prepareDropdownOpeningAnimation?.(trigger, menu);
        menu.classList.add('open');
        menu.setAttribute('aria-hidden', 'false');
        trigger.setAttribute('aria-expanded', 'true');
        window.requestAnimationFrame(() => {
            if (state.availability === 'error') retryButton?.focus();
            else searchInput?.focus();
        });
    }

    function toggleMenu() {
        if (state.open) closeMenu();
        else openMenu();
    }

    async function refresh(parentModelId = '') {
        const normalizedParentId = String(
            parentModelId
            || window.getSelectedModelId?.()
            || document.getElementById('modelSelect')?.getAttribute('data-model-id')
            || ''
        ).trim();
        state.parentModelId = normalizedParentId;
        state.targets = [];
        state.query = '';
        if (searchInput) searchInput.value = '';
        state.requestSequence += 1;
        state.availability = normalizedParentId ? 'loading' : 'unknown';
        render();
        if (!normalizedParentId || window.SplitScreenManager?.active) return;

        const requestId = ++state.requestSequence;
        try {
            const models = await loadUserModels();
            if (requestId !== state.requestSequence) return;
            if (!Array.isArray(models)) throw new Error('Unexpected models payload');
            const parent = models.find((model) => (
                String(model?.model_id || model?.id || '').trim() === normalizedParentId
            ));
            state.availability = parent && modelSupportsSubagents(parent) ? 'enabled' : 'disabled';
            state.targets = state.availability === 'enabled' ? models
                .filter(isChatCapableTarget)
                .map(normalizeTarget)
                .filter(Boolean)
                .sort((left, right) => (
                    left.name.localeCompare(right.name)
                    || left.type.localeCompare(right.type)
                    || left.id.localeCompare(right.id)
                )) : [];
            render();
        } catch (error) {
            if (requestId !== state.requestSequence) return;
            console.error('Failed to refresh Subagent target capability', error);
            state.availability = 'error';
            render();
        }
    }

    function buildUi() {
        const controls = document.querySelector('#chatBox .chat-box-bottom-div');
        if (!controls || document.getElementById('subagentTargetsControl')) return;

        wrapper = document.createElement('div');
        wrapper.id = 'subagentTargetsControl';
        wrapper.className = 'chat-box-dropdown subagent-targets-control';
        wrapper.hidden = true;

        trigger = document.createElement('button');
        trigger.id = 'subagentTargetsButton';
        trigger.type = 'button';
        trigger.className = 'om-button subagent-targets-trigger';
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-controls', 'subagentTargetsMenu');
        const icon = document.createElement('span');
        icon.className = 'subagent-targets-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = (typeof Icons !== 'undefined' && Icons?.model_tool_subagent) || '';
        triggerLabel = document.createElement('span');
        triggerLabel.className = 'subagent-targets-trigger-label';
        trigger.append(icon, triggerLabel);

        menu = document.createElement('div');
        menu.id = 'subagentTargetsMenu';
        menu.className = 'select-dropdown subagent-targets-menu';
        menu.setAttribute('role', 'dialog');
        menu.setAttribute('aria-modal', 'false');
        menu.setAttribute('aria-labelledby', 'subagentTargetsTitle');
        menu.setAttribute('aria-hidden', 'true');
        menu.hidden = true;

        const header = document.createElement('div');
        header.className = 'subagent-targets-header';
        const title = document.createElement('strong');
        title.id = 'subagentTargetsTitle';
        title.textContent = translate('subagent_targets_dialog_title', 'Delegation targets');
        const close = document.createElement('button');
        close.type = 'button';
        close.id = 'subagentTargetsClose';
        close.className = 'om-button';
        close.innerHTML = (typeof Icons !== 'undefined' && Icons?.close) || '<span aria-hidden="true">×</span>';
        close.setAttribute('aria-label', translate('subagent_targets_close_aria', 'Close delegation targets'));
        close.addEventListener('click', () => closeMenu({ restoreFocus: true }));
        header.append(title, close);

        const description = document.createElement('p');
        description.className = 'subagent-targets-description';
        description.textContent = translate(
            'subagent_targets_dialog_description',
            'Choose the exact models and saved Agents this response may delegate to.',
        );

        automaticButton = document.createElement('button');
        automaticButton.type = 'button';
        automaticButton.className = 'subagent-targets-automatic';
        automaticButton.setAttribute('aria-pressed', 'true');
        automaticButton.innerHTML = `<span class="subagent-targets-item-name"></span><span class="subagent-targets-item-detail"></span>`;
        automaticButton.querySelector('.subagent-targets-item-name').textContent = translate('subagent_targets_any_title', 'Any accessible target');
        automaticButton.querySelector('.subagent-targets-item-detail').textContent = translate(
            'subagent_targets_any_description',
            'Let the parent discover and choose any target you can access.',
        );
        automaticButton.addEventListener('click', () => setSelection(null));

        searchInput = document.createElement('input');
        searchInput.type = 'search';
        searchInput.className = 'subagent-targets-search';
        searchInput.placeholder = translate('subagent_targets_search_placeholder', 'Search models and Agents…');
        searchInput.setAttribute('aria-label', translate('subagent_targets_search_aria', 'Search delegation targets'));
        searchInput.addEventListener('input', () => {
            state.query = searchInput.value.trim();
            renderTargetList();
        });

        targetList = document.createElement('div');
        targetList.className = 'subagent-targets-list';
        targetList.setAttribute('role', 'group');
        targetList.setAttribute('aria-label', translate('subagent_targets_list_aria', 'Available delegation targets'));
        emptyState = document.createElement('p');
        emptyState.className = 'subagent-targets-empty';
        retryButton = document.createElement('button');
        retryButton.type = 'button';
        retryButton.className = 'subagent-targets-retry';
        retryButton.addEventListener('click', () => void refresh(state.parentModelId));

        const footer = document.createElement('div');
        footer.className = 'subagent-targets-footer';
        const done = document.createElement('button');
        done.type = 'button';
        done.className = 'om-button border submit subagent-targets-done';
        done.textContent = translate('subagent_targets_done', 'Done');
        done.addEventListener('click', () => closeMenu({ restoreFocus: true }));
        footer.appendChild(done);

        menu.append(header, description, automaticButton, searchInput, targetList, emptyState, retryButton, footer);
        wrapper.append(trigger, menu);
        const thinkingControl = document.getElementById('chatBoxThinkingContainer');
        if (thinkingControl?.parentElement === controls) thinkingControl.after(wrapper);
        else controls.appendChild(wrapper);

        trigger.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleMenu();
        });
        menu.addEventListener('click', (event) => event.stopPropagation());
        document.addEventListener('click', () => closeMenu());
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && state.open) {
                event.preventDefault();
                closeMenu({ restoreFocus: true });
            }
        });

        render();
    }

    function init() {
        buildUi();
        window.addEventListener('modelSelect:changed', (event) => {
            setSelection(null);
            void refresh(event?.detail?.modelId || '');
        });
        window.addEventListener('userModels:refreshed', () => void refresh(state.parentModelId));
        window.addEventListener('splitScreen:stateChanged', () => {
            updateVisibility();
            if (!window.SplitScreenManager?.active) void refresh();
        });
        document.addEventListener('i18n:updated', () => {
            buildUi();
            render();
        });
        void refresh();
    }

    window.SubagentTargets = {
        getSelection,
        getAvailability,
        setSelection,
        refresh,
        _normalizeTargetForTest: normalizeTarget,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
}());
