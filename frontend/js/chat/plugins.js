(function createAgentPluginsWorkspace() {
    'use strict';

    let initialized = false;
    let pendingFile = null;
    let previouslyFocused = null;

    function t(key, fallback) {
        return typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    }

    function icon(name) {
        return typeof Icons !== 'undefined' ? (Icons[name] || '') : '';
    }

    async function request(url, options = {}) {
        const fetcher = typeof window.authedFetch === 'function' ? window.authedFetch : window.fetch.bind(window);
        const response = await fetcher(url, { credentials: 'include', ...options });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            const detail = typeof data.detail === 'string' ? data.detail : '';
            throw new Error(detail || t('workspace_plugins_request_error', 'Plugin request failed.'));
        }
        if (response.status === 204) return null;
        return response;
    }

    function setBusy(busy, message = '') {
        const status = document.getElementById('pluginsStatus');
        const buttons = document.querySelectorAll('#workspaceSectionPlugins button');
        buttons.forEach((button) => { button.disabled = Boolean(busy); });
        if (status) status.textContent = message;
    }

    function safeExternalUrl(value) {
        try {
            const url = new URL(value);
            return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
        } catch (_) {
            return '';
        }
    }

    function warningText(code) {
        const warnings = {
            local_process_not_supported: ['workspace_plugins_warning_local_process', 'Local-process MCP servers cannot be installed in personal plugins.'],
            hooks_not_executed: ['workspace_plugins_warning_hooks', 'Hooks are preserved for portability but are not executed by Omlorix.'],
            registered_apps_require_mcp: ['workspace_plugins_warning_apps', 'Registered OpenAI app IDs are preserved but require an MCP-hosted app UI in Omlorix.'],
            no_installable_components: ['workspace_plugins_warning_empty', 'This bundle does not declare an installable skill or MCP server.'],
        };
        const entry = warnings[code];
        return entry ? t(entry[0], entry[1]) : String(code || '');
    }

    function actionButton(label, className, handler) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = className || 'projects-header-button';
        button.textContent = label;
        button.addEventListener('click', handler);
        return button;
    }

    function renderPlugin(plugin) {
        const card = document.createElement('article');
        card.className = 'plugins-card';
        card.dataset.enabled = String(Boolean(plugin.enabled));

        const heading = document.createElement('div');
        heading.className = 'plugins-card-heading';
        const identity = document.createElement('div');
        identity.className = 'plugins-card-identity';
        const cardIcon = document.createElement('span');
        cardIcon.className = 'plugins-card-icon';
        cardIcon.setAttribute('aria-hidden', 'true');
        cardIcon.innerHTML = icon('plugin');
        const titleWrap = document.createElement('div');
        titleWrap.className = 'plugins-card-title-wrap';
        const title = document.createElement('h2');
        title.className = 'plugins-card-title';
        title.textContent = plugin.name;
        const version = document.createElement('span');
        version.className = 'plugins-card-version';
        version.textContent = `v${plugin.version}`;
        titleWrap.append(title, version);
        const state = document.createElement('span');
        state.className = 'plugins-state';
        state.textContent = plugin.enabled
            ? t('workspace_plugins_enabled', 'Enabled')
            : t('workspace_plugins_disabled', 'Disabled');
        identity.append(cardIcon, titleWrap);
        heading.append(identity, state);

        const description = document.createElement('p');
        description.className = 'plugins-card-description';
        description.textContent = plugin.description || t('workspace_plugins_no_description', 'No description provided.');
        const components = document.createElement('ul');
        components.className = 'plugins-card-components';
        (plugin.components || []).forEach((component) => {
            const item = document.createElement('li');
            item.className = 'plugins-component-chip';
            const type = component.type === 'skill'
                ? t('workspace_plugins_skill', 'Skill')
                : t('workspace_plugins_server', 'MCP server');
            item.textContent = `${type}: ${component.name}`;
            components.appendChild(item);
        });

        const actions = document.createElement('div');
        actions.className = 'plugins-card-actions';
        const toggleLabel = plugin.enabled
            ? t('workspace_plugins_disable', 'Disable')
            : t('workspace_plugins_enable', 'Enable');
        actions.appendChild(actionButton(toggleLabel, 'projects-header-button submit', async () => {
            setBusy(true, t('workspace_plugins_updating', 'Updating plugin…'));
            try {
                await request(`/api/v1/plugins/${encodeURIComponent(plugin.id)}/enabled`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: !plugin.enabled }),
                });
                window.notifySuccess?.(!plugin.enabled
                    ? t('workspace_plugins_enabled_success', 'Plugin enabled.')
                    : t('workspace_plugins_disabled_success', 'Plugin disabled.'));
                await load();
            } catch (error) {
                window.notifyError?.(error.message);
            } finally {
                setBusy(false);
            }
        }));
        actions.appendChild(actionButton(t('workspace_plugins_export', 'Export'), 'projects-header-button', () => {
            window.location.assign(`/api/v1/plugins/${encodeURIComponent(plugin.id)}/export`);
        }));
        const homepage = safeExternalUrl(plugin.homepage);
        if (homepage) {
            const link = document.createElement('a');
            link.className = 'projects-header-button';
            link.href = homepage;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = t('workspace_plugins_homepage', 'Homepage');
            actions.appendChild(link);
        }
        actions.appendChild(actionButton(t('workspace_plugins_uninstall', 'Uninstall'), 'projects-header-button', async () => {
            const confirmed = await window.showDeleteConfirm?.({
                title: t('workspace_plugins_uninstall_title', 'Uninstall plugin?'),
                message: t('workspace_plugins_uninstall_desc', 'The plugin and all skills and MCP servers it installed will be removed.'),
                confirmLabel: t('workspace_plugins_uninstall', 'Uninstall'),
            });
            if (!confirmed) return;
            setBusy(true, t('workspace_plugins_uninstalling', 'Uninstalling plugin…'));
            try {
                await request(`/api/v1/plugins/${encodeURIComponent(plugin.id)}`, { method: 'DELETE' });
                window.notifySuccess?.(t('workspace_plugins_uninstalled_success', 'Plugin uninstalled.'));
                await load();
            } catch (error) {
                window.notifyError?.(error.message);
            } finally {
                setBusy(false);
            }
        }));

        card.append(heading, description, components, actions);
        return card;
    }

    async function load() {
        const grid = document.getElementById('pluginsGrid');
        const empty = document.getElementById('pluginsEmpty');
        if (!grid || !empty) return;
        setBusy(true, t('workspace_plugins_loading', 'Loading plugins…'));
        try {
            const response = await request('/api/v1/plugins');
            const plugins = await response.json();
            grid.replaceChildren(...plugins.map(renderPlugin));
            empty.hidden = plugins.length !== 0;
        } catch (error) {
            grid.replaceChildren();
            empty.hidden = true;
            window.notifyError?.(error.message);
        } finally {
            setBusy(false);
        }
    }

    function closeReview() {
        const overlay = document.getElementById('pluginReviewOverlay');
        if (!overlay) return;
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        pendingFile = null;
        previouslyFocused?.focus?.();
        previouslyFocused = null;
    }

    function openReview(preview, file) {
        pendingFile = file;
        previouslyFocused = document.activeElement;
        document.getElementById('pluginReviewTitle').textContent = preview.name;
        document.getElementById('pluginReviewDescription').textContent = preview.description;
        document.getElementById('pluginReviewVersion').textContent = preview.version;
        document.getElementById('pluginReviewAuthor').textContent = preview.author_name || t('workspace_plugins_unknown', 'Unknown');
        document.getElementById('pluginReviewSkills').textContent = preview.skill_names.length ? preview.skill_names.join(', ') : '0';
        document.getElementById('pluginReviewServers').textContent = preview.mcp_server_names.length ? preview.mcp_server_names.join(', ') : '0';
        const warnings = document.getElementById('pluginReviewWarnings');
        warnings.replaceChildren(...preview.warnings.map((warning) => {
            const item = document.createElement('li');
            item.textContent = warningText(warning);
            return item;
        }));
        warnings.hidden = preview.warnings.length === 0;
        const overlay = document.getElementById('pluginReviewOverlay');
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.querySelector('[role="dialog"]')?.focus();
    }

    async function previewSelectedFile(file) {
        if (!file) return;
        setBusy(true, t('workspace_plugins_reviewing', 'Reviewing plugin…'));
        try {
            const form = new FormData();
            form.append('file', file);
            const response = await request('/api/v1/plugins/preview', { method: 'POST', body: form });
            openReview(await response.json(), file);
        } catch (error) {
            window.notifyError?.(error.message);
        } finally {
            setBusy(false);
        }
    }

    async function installPending() {
        if (!pendingFile) return;
        const file = pendingFile;
        const button = document.getElementById('pluginReviewInstall');
        button.disabled = true;
        try {
            const form = new FormData();
            form.append('file', file);
            await request('/api/v1/plugins/install', { method: 'POST', body: form });
            closeReview();
            window.notifySuccess?.(t('workspace_plugins_installed_success', 'Plugin installed disabled.'));
            await load();
        } catch (error) {
            window.notifyError?.(error.message);
        } finally {
            button.disabled = false;
        }
    }

    function init() {
        if (initialized) return;
        const input = document.getElementById('pluginBundleInput');
        if (!input) return;
        initialized = true;
        const securityIcon = document.querySelector('.plugins-security-note-icon');
        const emptyIcon = document.querySelector('.plugins-empty-icon');
        if (securityIcon) securityIcon.innerHTML = icon('security');
        if (emptyIcon) emptyIcon.innerHTML = icon('plugin');
        document.querySelectorAll('#pluginInstallButton, #pluginEmptyInstallButton').forEach((button) => {
            button.addEventListener('click', () => input.click());
        });
        input.addEventListener('change', () => {
            const file = input.files?.[0];
            input.value = '';
            void previewSelectedFile(file);
        });
        document.getElementById('pluginReviewInstall')?.addEventListener('click', () => void installPending());
        document.querySelectorAll('#pluginReviewClose, #pluginReviewCancel').forEach((button) => button.addEventListener('click', closeReview));
        document.getElementById('pluginReviewOverlay')?.addEventListener('click', (event) => {
            if (event.target.id === 'pluginReviewOverlay') closeReview();
        });
        document.addEventListener('keydown', (event) => {
            const overlay = document.getElementById('pluginReviewOverlay');
            if (overlay?.hidden) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                closeReview();
                return;
            }
            if (event.key === 'Tab') {
                const focusable = Array.from(overlay.querySelectorAll(
                    'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
                )).filter((element) => !element.hidden);
                if (!focusable.length) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }
        });
    }

    window.AgentPluginsWorkspace = { show() { init(); return load(); }, load };
})();
