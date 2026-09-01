/**
 * Shared renderer for the main chat sidebar's middle action area.
 *
 * The rest of the app depends on stable element IDs such as
 * `sidebarWorkspace` and `sidebarProjects`, so this module renders those IDs
 * once and exposes small update methods for dynamic state.
 */
(function () {
    const DEFAULT_VISIBILITY = {
        create_chat: true,
        search_chats: true,
        workspace: true,
        automations: true,
        projects: true
    };

    const featureAvailability = {
        create_chat: true,
        search_chats: true,
        workspace: true,
        automations: false,
        projects: false
    };

    let userVisibility = { ...DEFAULT_VISIBILITY };

    const sidebarItems = [
        {
            key: 'create_chat',
            buttonId: 'sidebarCreateChat',
            textId: 'createChatText',
            labelKey: 'sidebar_create_chat',
            fallback: 'Create Chat',
            icon: () => Icons.create
        },
        {
            key: 'search_chats',
            buttonId: 'sidebarChatsSearch',
            labelKey: 'sidebar_search_chats',
            fallback: 'Search Chats',
            icon: () => Icons.magnifyingGlass
        },
        {
            key: 'workspace',
            buttonId: 'sidebarWorkspace',
            textId: 'WorkspaceText',
            labelKey: 'sidebar_workspace',
            fallback: 'Workspace',
            icon: () => Icons.workspace
        },
        {
            key: 'automations',
            containerId: 'sidebarAutomationsContainer',
            buttonId: 'sidebarAutomations',
            labelKey: 'sidebar_automations',
            fallback: 'Automations',
            icon: () => Icons.clock,
            usesDataHidden: true
        },
        {
            key: 'projects',
            containerId: 'sidebarProjects',
            labelKey: 'sidebar_projects',
            fallback: 'Projects',
            icon: () => Icons.folder,
            usesDataHidden: true
        }
    ];

    function getHost() {
        return document.getElementById('sidebarMid') || document.querySelector('.sidebar-container > .sidebar-mid');
    }

    function translate(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function createShortcutBadge(item) {
        if (!item.shortcutKey) {
            return null;
        }
        const badge = document.createElement('span');
        badge.className = 'sidebar-element-shorcut';
        badge.dataset.shortcutKey = item.shortcutKey;
        if (item.shortcutModifiers) {
            badge.dataset.shortcutModifiers = item.shortcutModifiers;
        }
        return badge;
    }

    function createSidebarItem(item) {
        const wrapper = document.createElement('div');
        wrapper.className = 'sidebar-element';
        wrapper.dataset.sidebarMidItem = item.key;
        if (item.containerId) {
            wrapper.id = item.containerId;
        }

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'sidebar-element-button';
        button.dataset.sidebarMidButton = item.key;
        if (item.buttonId) {
            button.id = item.buttonId;
        }
        if (item.shortcutAction) {
            button.dataset.shortcutAction = item.shortcutAction;
        }

        const iconHtml = typeof item.icon === 'function' ? item.icon() : '';
        if (iconHtml) {
            button.insertAdjacentHTML('beforeend', iconHtml);
        }

        const label = document.createElement('p');
        if (item.textId) {
            label.id = item.textId;
        }
        label.dataset.i18n = item.labelKey;
        label.textContent = translate(item.labelKey, item.fallback);

        button.appendChild(label);
        const shortcutBadge = createShortcutBadge(item);
        if (shortcutBadge) {
            button.appendChild(shortcutBadge);
        }
        wrapper.appendChild(button);
        return wrapper;
    }

    function createPinnedModelsSection() {
        const section = document.createElement('div');
        section.className = 'sidebar-quick-models';
        section.id = 'sidebarPinnedModels';
        section.hidden = true;

        const header = document.createElement('div');
        header.className = 'sidebar-quick-models-header';

        const title = document.createElement('p');
        title.dataset.i18n = 'sidebar_section_pinned_models';
        title.textContent = translate('sidebar_section_pinned_models', 'Pinned Models');
        header.appendChild(title);

        const list = document.createElement('div');
        list.className = 'sidebar-quick-models-list';
        list.id = 'sidebarPinnedModelsList';

        section.appendChild(header);
        section.appendChild(list);
        return section;
    }

    function isItemVisible(key) {
        return userVisibility[key] !== false && featureAvailability[key] !== false;
    }

    function updateRenderedVisibility() {
        sidebarItems.forEach((item) => {
            const wrapper = document.querySelector(`[data-sidebar-mid-item="${item.key}"]`);
            if (!wrapper) {
                return;
            }

            const isVisible = isItemVisible(item.key);
            if (item.usesDataHidden) {
                wrapper.setAttribute('data-sidebar-hidden', isVisible ? 'false' : 'true');
                wrapper.style.display = '';
                return;
            }

            wrapper.style.setProperty('display', isVisible ? '' : 'none', 'important');
        });
    }

    function updateRenderedTranslations() {
        sidebarItems.forEach((item) => {
            const label = document.querySelector(`[data-sidebar-mid-button="${item.key}"] [data-i18n="${item.labelKey}"]`);
            if (label) {
                label.textContent = translate(item.labelKey, item.fallback);
            }
        });

        const pinnedTitle = document.querySelector('#sidebarPinnedModels [data-i18n="sidebar_section_pinned_models"]');
        if (pinnedTitle) {
            pinnedTitle.textContent = translate('sidebar_section_pinned_models', 'Pinned Models');
        }
    }

    /**
     * Render the static shell exactly once before other chat modules bind
     * their existing event handlers to the generated IDs.
     */
    function render() {
        const host = getHost();
        if (!host || host.dataset.sidebarMidRendered === 'true') {
            return;
        }

        const fragment = document.createDocumentFragment();
        sidebarItems.forEach((item) => fragment.appendChild(createSidebarItem(item)));
        fragment.appendChild(createPinnedModelsSection());
        host.replaceChildren(fragment);
        host.dataset.sidebarMidRendered = 'true';
        updateRenderedVisibility();
        updateRenderedTranslations();
    }

    function setUserVisibility(visibility = {}) {
        userVisibility = { ...DEFAULT_VISIBILITY, ...visibility };
        updateRenderedVisibility();
    }

    function setFeatureAvailability(key, isAvailable) {
        if (!Object.prototype.hasOwnProperty.call(featureAvailability, key)) {
            return;
        }
        featureAvailability[key] = isAvailable === true;
        updateRenderedVisibility();
    }

    function setWorkspaceBadge(hasNew) {
        const button = document.getElementById('sidebarWorkspace');
        if (!button) {
            return;
        }

        let badge = button.querySelector('.notification-badge');
        if (hasNew) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'notification-badge';
                button.style.position = 'relative';
                button.appendChild(badge);
            }
            badge.style.display = '';
        } else if (badge) {
            badge.style.display = 'none';
        }
    }

    function renderPinnedModels(models = [], options = {}) {
        const section = document.getElementById('sidebarPinnedModels');
        const list = document.getElementById('sidebarPinnedModelsList');
        if (!section || !list) {
            return;
        }

        section.hidden = models.length === 0;
        list.innerHTML = '';

        models.forEach((model) => {
            const row = document.createElement('div');
            row.className = 'sidebar-quick-model';

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'sidebar-quick-model-button';
            button.dataset.modelId = model.model_id;

            const iconWrap = document.createElement('span');
            iconWrap.className = 'sidebar-quick-model-icon';
            if (typeof options.applyModelIcon === 'function') {
                options.applyModelIcon(iconWrap, model.model_icon);
            }

            const label = document.createElement('span');
            label.className = 'sidebar-quick-model-label';
            label.textContent = model.name || translate('model_select_unnamed_model', 'Unnamed model');

            button.appendChild(iconWrap);
            button.appendChild(label);
            button.addEventListener('click', async () => {
                if (typeof options.onSelect === 'function') {
                    await options.onSelect(model);
                }
            });

            const unpinButton = document.createElement('button');
            unpinButton.type = 'button';
            unpinButton.className = 'sidebar-quick-model-unpin';
            unpinButton.innerHTML = options.unpinIcon || Icons.unpin || '';
            unpinButton.setAttribute('aria-label', options.unpinLabel || translate('model_select_unpin_model', 'Unpin model'));
            unpinButton.setAttribute('title', options.unpinLabel || translate('model_select_unpin_model', 'Unpin model'));
            unpinButton.addEventListener('click', async (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (typeof options.onUnpin === 'function') {
                    await options.onUnpin(model);
                }
            });

            row.appendChild(button);
            row.appendChild(unpinButton);
            list.appendChild(row);
        });
    }

    window.ChatSidebarMid = {
        render,
        setUserVisibility,
        setFeatureAvailability,
        setWorkspaceBadge,
        renderPinnedModels
    };

    render();
    document.addEventListener('DOMContentLoaded', render);
    document.addEventListener('i18n:updated', updateRenderedTranslations);
})();
