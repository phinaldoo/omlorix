(() => {
    'use strict';

    const formRenderer = window.CreateEditFormRenderer;
    if (!formRenderer) {
        throw new Error('CreateEditFormRenderer must load before agents.js');
    }

    const AgentsState = {
        initialized: false,
        loading: false,
        agents: [],
        baseModels: [],
        skills: [],
        publicUsers: [],
        publicUsersLoaded: false,
        publicUsersLoading: false,
        editorMode: 'create',
        editingAgentId: null,
        selectedBaseModelId: '',
        selectedSkillId: '',
        modelSearchQuery: '',
        selectedFileIds: [],
        selectedAssetIds: [],
        initialAssetIds: [],
        files: [],
        fileMetaMap: new Map(),
        fileLibrarySearch: '',
        filesLoading: false,
        fileLibraryOpen: false,
        currentAssets: [],
        modelSelectOutsideBound: false,
        skillSelectOutsideBound: false,
        fileLibraryOutsideBound: false,
        escapeHandlersBound: false,
        shareAgentId: null,
        acceptHandled: false,
    };

    const t = (key, fallback, vars) => {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const plural = (count, singularKey, singularFallback, pluralKey, pluralFallback, vars = {}) => (
        Number(count) === 1
            ? t(singularKey, singularFallback, { ...vars, count })
            : t(pluralKey, pluralFallback, { ...vars, count })
    );

    const notifySuccess = (message) => {
        if (typeof window.notifySuccess === 'function') {
            window.notifySuccess(message);
        }
    };

    const notifyError = (message) => {
        if (typeof window.notifyError === 'function') {
            window.notifyError(message);
        }
    };

    const agentsEnabled = () => typeof window === 'undefined' || window.enableAgentsFeature !== false;
    const agentSharingEnabled = () => typeof window === 'undefined' || window.allowAgentShareFeature !== false;
    const agentHasExistingShareState = (agent) => Boolean(
        agent?.clone_share_id ||
        agent?.live_share_id ||
        agent?.collaborate_share_id ||
        Number(agent?.live_subscriber_count || 0) > 0 ||
        Number(agent?.collaborate_subscriber_count || 0) > 0
    );
    const canManageAgentSharing = (agent) => agentSharingEnabled() || agentHasExistingShareState(agent);

    /**
     * Refresh model-based chat controls after the accessible agent inventory
     * changes. The model selector owns the authoritative refresh operation and
     * broadcasts its result to the chat-box mention menu.
     *
     * A refresh failure must not turn a successfully persisted agent mutation
     * into a misleading save/delete error. The controls can still recover on
     * the next full page load, so log the secondary failure for diagnostics.
     *
     * @returns {Promise<void>}
     */
    async function refreshAgentModelConsumers() {
        try {
            if (typeof window.refreshUserModelConsumers === 'function') {
                await window.refreshUserModelConsumers();
                return;
            }

            // Defensive fallback for unusual script-loading environments. The
            // regular application path uses refreshUserModelConsumers above.
            let models;
            if (typeof window.ModelSelectLoadModels === 'function') {
                models = await window.ModelSelectLoadModels({ forceRefresh: true });
            } else if (typeof window.getCachedUserModels === 'function') {
                models = await window.getCachedUserModels({ forceRefresh: true });
            }
            if (!Array.isArray(models)) {
                return;
            }
            window.dispatchEvent(new CustomEvent('userModels:refreshed', {
                detail: { models },
            }));
        } catch (error) {
            console.error('[agents] failed to refresh model consumers', error);
        }
    }

    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = String(value ?? '');
        return div.innerHTML;
    };

    const authedRequest = async (url, options = {}) => {
        const response = await window.authedFetch(url, options);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok) {
            throw new Error(payload?.detail || t('workspace_agents_request_failed', 'Request failed ({status})', { status: response.status }));
        }
        return payload;
    };

    const AgentsAPI = {
        async listAgents() {
            return authedRequest('/api/v1/agents', { method: 'GET' });
        },
        async getAgent(agentId) {
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}`, { method: 'GET' });
        },
        async createAgent(payload) {
            return authedRequest('/api/v1/agents', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        },
        async updateAgent(agentId, payload) {
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        },
        async deleteAgent(agentId) {
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
        },
        async listAssets(agentId) {
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}/assets`, { method: 'GET' });
        },
        async uploadAssets(agentId, files) {
            const formData = new FormData();
            files.forEach((file) => formData.append('files', file));
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}/assets`, {
                method: 'POST',
                body: formData,
            });
        },
        async attachFiles(agentId, fileIds) {
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}/assets/from-files`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_ids: fileIds }),
            });
        },
        async deleteAsset(agentId, assetId) {
            return authedRequest(`/api/v1/agents/${encodeURIComponent(agentId)}/assets/${encodeURIComponent(assetId)}`, {
                method: 'DELETE',
            });
        },
        async listFiles({ limit = 200, offset = 0, search = '', query = '' } = {}) {
            const pageLimit = Math.min(Math.max(Number(limit) || 200, 1), 200);
            const pageOffset = Math.max(Number(offset) || 0, 0);
            const params = new URLSearchParams({
                limit: String(pageLimit),
                offset: String(pageOffset),
                sort_field: 'name',
                sort_direction: 'asc',
            });
            const searchText = String(search || query || '').trim();
            if (searchText) {
                params.set('search', searchText);
            }
            const payload = await authedRequest(`/api/v1/files/workspace?${params.toString()}`, { method: 'GET' });
            const items = Array.isArray(payload?.items) ? payload.items : (Array.isArray(payload) ? payload : []);
            const responseLimit = Number(payload?.limit ?? pageLimit);
            const responseOffset = Number(payload?.offset ?? pageOffset);
            const total = Number(payload?.total ?? responseOffset + items.length);
            return {
                items,
                total: Number.isFinite(total) ? total : responseOffset + items.length,
                limit: Number.isFinite(responseLimit) ? responseLimit : pageLimit,
                offset: Number.isFinite(responseOffset) ? responseOffset : pageOffset,
                hasMore: typeof payload?.has_more === 'boolean'
                    ? payload.has_more
                    : responseOffset + items.length < total,
            };
        },
        async uploadFile(file) {
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
                throw new Error(payload?.detail || payload?.message || t('files_upload_failed', 'Upload failed'));
            }
            return payload;
        },
        async listBaseModels() {
            const payload = typeof window.getCachedUserModels === 'function'
                ? await window.getCachedUserModels()
                : await authedRequest('/api/v1/llm/models/user', { method: 'GET' });
            const models = Array.isArray(payload) ? payload : [];
            return models.filter((model) => model?.model_kind === 'base');
        },
        async listSkills() {
            const payload = await authedRequest('/api/v1/skills', { method: 'GET' });
            if (Array.isArray(payload)) return payload;
            if (Array.isArray(payload?.skills)) return payload.skills;
            return [];
        },
        async listUsers() {
            const users = [];
            const seenUserIds = new Set();
            let offset = 0;
            const limit = 100;
            while (true) {
                const response = await window.authedFetch(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, { method: 'GET' });
                let payload = null;
                try {
                    payload = await response.json();
                } catch (_) {
                    payload = null;
                }
                if (!response.ok) {
                    throw new Error(payload?.detail || t('workspace_agents_request_failed', 'Request failed ({status})', { status: response.status }));
                }
                const pageUsers = Array.isArray(payload) ? payload : (Array.isArray(payload?.users) ? payload.users : []);
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
        async shareAgent(agentId, shareType) {
            return authedRequest('/api/v1/agents/share', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_id: agentId, share_type: shareType }),
            });
        },
        async getShareStatus(agentId) {
            return authedRequest(`/api/v1/agents/share/status?agent_id=${encodeURIComponent(agentId)}`, {
                method: 'GET',
            });
        },
        async deleteShare(agentId, shareType) {
            return authedRequest('/api/v1/agents/share/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_id: agentId, share_type: shareType || null }),
            });
        },
        async inviteUsers(agentId, userIds, shareType) {
            return authedRequest('/api/v1/agents/invite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: agentId, user_ids: userIds, share_type: shareType }),
            });
        },
        async previewShare(shareId) {
            return authedRequest(`/api/v1/agents/shared/${encodeURIComponent(shareId)}`, { method: 'GET' });
        },
        async acceptShare(shareId) {
            return authedRequest(`/api/v1/agents/shared/${encodeURIComponent(shareId)}/accept`, {
                method: 'POST',
            });
        },
        async cloneShare(shareId) {
            return authedRequest(`/api/v1/agents/shared/${encodeURIComponent(shareId)}/clone`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
        },
        async unsubscribe(agentId) {
            return authedRequest(`/api/v1/agents/shared/${encodeURIComponent(agentId)}/unsubscribe`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
        },
    };

    function getSection() {
        return document.getElementById('workspaceSectionAgents');
    }

    function getBaseModelLabel(baseModelId) {
        const normalizedBaseModelId = String(baseModelId || '');
        const model = AgentsState.baseModels.find((item) => (
            String(item?.model_id || '') === normalizedBaseModelId
            || String(item?.id || '') === normalizedBaseModelId
        ));

        // A base-model UUID is an implementation detail and is not useful in
        // the card UI. If the referenced model is unavailable, show the
        // translated fallback instead of leaking the stored identifier.
        return model?.name || t('agents_base_model_unknown', 'Unknown model');
    }

    function getSkillLabel(skill) {
        return String(skill?.title || skill?.name || skill?.id || t('workspace_agents_untitled_skill', 'Untitled skill'));
    }

    const DEFAULT_SKILL_ICON_COLOR = '#E53935';
    const DEFAULT_SKILL_ICON_ID = 'tool';
    const DEFAULT_SKILL_ICON_BODY = (typeof featureIconBodies !== 'undefined' ? featureIconBodies : Icons.featureIconBodies).skillDefault;
    const DEFAULT_SKILL_ICON = Icons.wrapSvgBody(DEFAULT_SKILL_ICON_BODY, { width: '16', height: '16' });
    const skillIconUtils = window.WorkspaceIconUtils;
    const SKILL_ICON_OPTIONS = skillIconUtils.getWorkspaceIconOptions();

    function sanitizeSkillColor(color) {
        const value = String(color || '').trim();
        return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value) ? value : DEFAULT_SKILL_ICON_COLOR;
    }

    function getSkillIconData(iconValue) {
        const resolved = skillIconUtils.resolveWorkspaceStoredIcon(iconValue, {
            iconOptions: SKILL_ICON_OPTIONS,
            defaultIconId: DEFAULT_SKILL_ICON_ID,
            defaultColor: DEFAULT_SKILL_ICON_COLOR,
        });
        return {
            ...resolved,
            type: 'preset',
            iconId: resolved?.iconId || DEFAULT_SKILL_ICON_ID,
            svg: resolved?.svg || DEFAULT_SKILL_ICON,
            color: sanitizeSkillColor(resolved?.color),
        };
    }

    function renderSkillIcon(iconData, size = 16) {
        return skillIconUtils.renderWorkspaceIcon(iconData, {
            size,
            iconOptions: SKILL_ICON_OPTIONS,
            defaultIconId: DEFAULT_SKILL_ICON_ID,
        });
    }

    function resolveAgentModelIcon(iconValue) {
        const fallback = typeof Icons === 'object'
            ? (Icons?.omlorixModel || Icons?.omlorix || '')
            : '';
        if (window.IconPicker?.renderModelIconMarkup) {
            return window.IconPicker.renderModelIconMarkup(iconValue, {
                fallback,
                imageAlt: t('workspace_agents_base_model', 'Base model'),
            });
        }
        if (typeof resolveModelIcon === 'function') return resolveModelIcon(iconValue);
        return fallback;
    }

    function formatFileSize(bytes) {
        const value = Number(bytes);
        if (!Number.isFinite(value) || value <= 0) return '-';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = value;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }
        return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
    }

    function fileIconName(fileType) {
        if (typeof window.getFileIconForType === 'function') {
            const iconName = window.getFileIconForType(fileType);
            if (typeof iconName === 'string' && iconName.trim()) return iconName;
        }
        return 'txt.svg';
    }

    function fileExtensionLabel(filename) {
        if (typeof window.getFileExtensionLabel === 'function') {
            return window.getFileExtensionLabel(filename);
        }
        const parts = String(filename || '').split('.');
        return parts.length > 1 ? String(parts.pop()).toUpperCase() : 'FILE';
    }

    function normalizeFileRecord(file) {
        const fileId = file?.file_id || file?.id;
        if (!fileId) return null;
        return {
            file_id: String(fileId),
            file_name: file?.meta?.original_filename || file?.file_name || file?.name || String(fileId),
            file_size: file?.file_size ?? file?.size ?? 0,
            file_type: file?.file_type || file?.type || '',
            file_category: file?.file_category || file?.category || '',
            meta: file?.meta || {},
        };
    }

    function agentAssetDisplayName(asset) {
        return asset?.original_filename
            || asset?.meta?.original_filename
            || asset?.file_name
            || asset?.name
            || asset?.id
            || '';
    }

    function upsertFileMeta(file) {
        const normalized = normalizeFileRecord(file);
        if (!normalized) return null;
        AgentsState.fileMetaMap.set(normalized.file_id, normalized);
        return normalized;
    }

    function getFileMeta(fileId) {
        const id = String(fileId || '');
        if (AgentsState.fileMetaMap.has(id)) return AgentsState.fileMetaMap.get(id);
        const fromCache = AgentsState.files.find((file) => String(file.file_id || file.id) === id);
        return upsertFileMeta(fromCache) || { file_id: id, file_name: id, file_size: 0, file_type: '' };
    }

    function ensureLayout() {
        const section = getSection();
        if (!section || section.dataset.agentsLayoutReady === 'true') return;

        section.innerHTML = `
            <div class="projects-content" id="agentsLibraryView">
                <div class="projects-header workspace-skills-header">
                    <div>
                        <p class="projects-header-title">${escapeHtml(t('workspace_agents_title', 'Agents'))}</p>
                    </div>
                    <div class="workspace-skills-header-actions">
                        <button type="button" class="om-button border" id="createAgentBtn">
                            ${Icons.plus}
                            <span>${escapeHtml(t('workspace_agents_create', 'Create agent'))}</span>
                        </button>
                    </div>
                </div>
                <div class="prompt-library-loading" id="agentsLibraryLoading">
                    <p>${escapeHtml(t('workspace_agents_loading', 'Loading agents...'))}</p>
                </div>
                <div class="workspace-notifications-empty workspace-empty-grid" id="agentsLibraryEmpty" style="display:none;">
                    <div class="workspace-notifications-empty-icon">
                        ${Icons.user}
                    </div>
                    <p class="workspace-notifications-empty-title">${escapeHtml(t('workspace_agents_empty_title', 'No agents yet'))}</p>
                    <p class="workspace-notifications-empty-text">${escapeHtml(t('workspace_agents_empty', 'Create reusable custom agents on top of your accessible base models with instructions, skills, and reference files.'))}</p>
                </div>
                <div class="prompt-library-list" id="agentsLibraryList"></div>
            </div>

            ${formRenderer.renderPage({
                id: 'agentsEditorView',
                titleId: 'agentsEditorTitle',
                title: { key: 'workspace_agents_editor_create', fallback: 'Create agent' },
                formId: 'agentsEditorForm',
            })}

            ${formRenderer.renderPage({
                id: 'agentsShareView',
                title: { key: 'workspace_agents_share_title', fallback: 'Share agent' },
                headerActionsHtml: `
                    <div class="workspace-skills-header-actions">
                        <button type="button" class="om-button border" id="agentsShareBackBtn">${escapeHtml(t('workspace_agents_back', 'Back'))}</button>
                    </div>`,
                formId: 'agentsShareForm',
            })}
        `;

        section.dataset.agentsLayoutReady = 'true';
    }

    function showView(view) {
        ['agentsLibraryView', 'agentsEditorView', 'agentsShareView'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) {
                el.style.display = id === view ? '' : 'none';
            }
        });
    }

    function getFilteredAgents() {
        return AgentsState.agents;
    }

    function renderList() {
        const listEl = document.getElementById('agentsLibraryList');
        const emptyEl = document.getElementById('agentsLibraryEmpty');
        const loadingEl = document.getElementById('agentsLibraryLoading');
        if (!listEl) return;

        if (loadingEl) loadingEl.style.display = AgentsState.loading ? 'flex' : 'none';
        if (AgentsState.loading) return;

        const filtered = getFilteredAgents();
        listEl.innerHTML = '';

        if (!filtered.length) {
            if (emptyEl) emptyEl.style.display = 'flex';
            return;
        }

        if (emptyEl) emptyEl.style.display = 'none';
        const fragment = document.createDocumentFragment();
        filtered.forEach((agent) => {
            const card = document.createElement('article');
            card.className = 'prompt-library-card agent-library-card';
            const isMine = !agent?.is_shared;
            const shareBadge = isMine
                ? t('workspace_agents_badge_mine', 'Mine')
                : (agent?.share_type === 'collaborate'
                    ? t('workspace_agents_badge_collaborate', 'Shared • Collaborate')
                    : t('workspace_agents_badge_live', 'Shared • Live'));
            const ownerLine = !isMine && agent?.owner_name
                ? `<span class="prompt-library-owner">${escapeHtml(agent.owner_name)}</span>`
                : '';
            const baseModelLine = escapeHtml(getBaseModelLabel(agent?.base_model_id));

            card.innerHTML = `
                <div class="prompt-library-card-header agent-library-card-header">
                    <div class="agent-library-card-identity">
                        <div class="agent-library-card-icon" aria-hidden="true">
                            ${typeof resolveModelIcon === 'function' ? resolveModelIcon(agent?.model_icon || agent?.icon) : ''}
                        </div>
                        <h3 class="prompt-library-card-title">${escapeHtml(agent?.name || t('workspace_agents_untitled', 'Untitled agent'))}</h3>
                        <span class="agent-library-card-model">${baseModelLine}</span>
                        <div class="prompt-library-card-meta agent-library-card-meta">
                            <span class="prompt-library-badge ${isMine ? 'mine' : 'shared'}">${shareBadge}</span>
                            ${ownerLine}
                        </div>
                    </div>
                </div>
                <div class="prompt-library-card-actions">
                    ${(isMine || agent?.share_type === 'collaborate') ? `<button type="button" class="prompt-card-btn" data-action="edit">${escapeHtml(t('workspace_agents_action_edit', 'Edit'))}</button>` : ''}
                    ${isMine && canManageAgentSharing(agent) ? `<button type="button" class="prompt-card-btn" data-action="share">${escapeHtml(t('workspace_agents_action_share', 'Share'))}</button>` : ''}
                    <button type="button" class="prompt-card-btn danger" data-action="${isMine ? 'delete' : 'remove'}">${isMine ? escapeHtml(t('workspace_agents_delete', 'Delete')) : escapeHtml(t('workspace_agents_remove', 'Remove'))}</button>
                </div>
            `;

            card.querySelector('[data-action="edit"]')?.addEventListener('click', () => openEditor('edit', agent.id));
            card.querySelector('[data-action="share"]')?.addEventListener('click', () => openShareView(agent.id));
            card.querySelector('[data-action="delete"]')?.addEventListener('click', () => deleteAgent(agent));
            card.querySelector('[data-action="remove"]')?.addEventListener('click', () => removeSharedAgent(agent));
            fragment.appendChild(card);
        });
        listEl.appendChild(fragment);
    }

    function closeAgentModelDropdown() {
        const trigger = document.getElementById('agentBaseModelSelectTrigger');
        trigger?.classList.remove('open');
        trigger?.setAttribute('aria-expanded', 'false');
        document.getElementById('agentBaseModelSelectDropdown')?.classList.remove('open');
        AgentsState.modelSearchQuery = '';
        const searchInput = document.getElementById('agentBaseModelSelectSearch');
        if (searchInput) searchInput.value = '';
    }

    function closeAgentSkillDropdown() {
        const trigger = document.getElementById('agentSkillSelectTrigger');
        trigger?.classList.remove('open');
        trigger?.setAttribute('aria-expanded', 'false');
        document.getElementById('agentSkillSelectDropdown')?.classList.remove('open');
    }

    function isAgentEditorActive() {
        const editor = document.getElementById('agentsEditorView');
        return Boolean(editor && editor.style.display !== 'none');
    }

    function hasAgentTransientDropdown() {
        return Boolean(
            AgentsState.fileLibraryOpen ||
            document.getElementById('agentBaseModelSelectDropdown')?.classList.contains('open') ||
            document.getElementById('agentSkillSelectDropdown')?.classList.contains('open')
        );
    }

    function closeAgentTransientDropdowns() {
        closeAgentModelDropdown();
        closeAgentSkillDropdown();
        toggleAgentFileLibrary(false);
    }

    function registerEscapeHandlers() {
        if (
            AgentsState.escapeHandlersBound ||
            typeof window === 'undefined' ||
            typeof window.registerEscapeHandler !== 'function'
        ) {
            return;
        }

        window.registerEscapeHandler({
            id: 'workspace-agents-transient-dropdowns',
            priority: 120,
            isActive: () => hasAgentTransientDropdown(),
            close: () => closeAgentTransientDropdowns(),
        });

        window.registerEscapeHandler({
            id: 'workspace-agents-editor-mode',
            priority: 20,
            isActive: () => isAgentEditorActive(),
            close: () => showView('agentsLibraryView'),
        });

        AgentsState.escapeHandlersBound = true;
    }

    function renderAgentModelOptions() {
        const listEl = document.getElementById('agentBaseModelSelectList');
        if (!listEl) return;
        const query = String(AgentsState.modelSearchQuery || '').trim().toLowerCase();
        const filtered = query
            ? AgentsState.baseModels.filter((model) => (
                String(model?.name || '').toLowerCase().includes(query)
                || String(model?.model_id || '').toLowerCase().includes(query)
            ))
            : AgentsState.baseModels;

        if (!AgentsState.baseModels.length) {
            listEl.innerHTML = `<div class="shared-model-select-empty">${escapeHtml(t('workspace_agents_no_base_models', 'No base models available'))}</div>`;
            return;
        }
        if (!filtered.length) {
            listEl.innerHTML = `<div class="shared-model-select-empty">${escapeHtml(t('workspace_agents_no_model_matches', 'No models match your search'))}</div>`;
            return;
        }

        listEl.innerHTML = filtered.map((model) => {
            const modelId = String(model?.model_id || '');
            return `
                <button type="button" role="option" aria-selected="${modelId === AgentsState.selectedBaseModelId ? 'true' : 'false'}"
                    tabindex="-1" class="shared-model-select-item ${modelId === AgentsState.selectedBaseModelId ? 'selected' : ''}"
                    data-model-id="${escapeHtml(modelId)}">
                    <span class="shared-model-select-item-icon">${resolveAgentModelIcon(model?.model_icon)}</span>
                    <span class="shared-model-select-item-name">${escapeHtml(model?.name || modelId)}</span>
                    <span class="shared-model-select-item-check">
                        ${Icons.check}
                    </span>
                </button>
            `;
        }).join('');

        listEl.querySelectorAll('.shared-model-select-item').forEach((item) => {
            item.addEventListener('click', (event) => {
                event.stopPropagation();
                AgentsState.selectedBaseModelId = String(item.dataset.modelId || '');
                closeAgentModelDropdown();
                renderAgentModelSelect();
            });
        });
    }

    function renderAgentModelSelect() {
        const container = document.getElementById('agentBaseModelSelect');
        if (!container) return;
        const selectedModel = AgentsState.baseModels.find((model) => String(model?.model_id || '') === AgentsState.selectedBaseModelId);
        container.innerHTML = formRenderer.renderSingleSelect({
            kind: 'model',
            triggerId: 'agentBaseModelSelectTrigger',
            dropdownId: 'agentBaseModelSelectDropdown',
            iconHtml: selectedModel ? resolveAgentModelIcon(selectedModel.model_icon) : resolveAgentModelIcon(''),
            label: selectedModel?.name || t('model_select_title', 'Select Model'),
            placeholder: !selectedModel,
            caretHtml: Icons.chevron,
            search: {
                id: 'agentBaseModelSelectSearch',
                placeholder: t('model_select_search_placeholder', 'Search models...'),
                value: AgentsState.modelSearchQuery || '',
            },
            listId: 'agentBaseModelSelectList',
        });

        formRenderer.bindSingleSelect({
            container,
            triggerId: 'agentBaseModelSelectTrigger',
            dropdownId: 'agentBaseModelSelectDropdown',
            searchId: 'agentBaseModelSelectSearch',
            onOpen: ({ searchInput }) => {
                renderAgentModelOptions();
                requestAnimationFrame(() => searchInput?.focus());
            },
            onClose: () => {
                AgentsState.modelSearchQuery = '';
            },
            onSearch: (value) => {
                AgentsState.modelSearchQuery = value || '';
                renderAgentModelOptions();
            },
        });
        renderAgentModelOptions();

        if (!AgentsState.modelSelectOutsideBound) {
            document.addEventListener('click', (event) => {
                const currentContainer = document.getElementById('agentBaseModelSelect');
                if (currentContainer && !currentContainer.contains(event.target)) {
                    closeAgentModelDropdown();
                }
            }, { capture: true });
            AgentsState.modelSelectOutsideBound = true;
        }
    }

    function renderAgentSkillSelect() {
        const container = document.getElementById('agentSkillSelect');
        if (!container) return;
        const selectedSkill = AgentsState.skills.find((skill) => String(skill?.id || '') === AgentsState.selectedSkillId);
        const defaultIcon = getSkillIconData(null);
        const selectedIcon = selectedSkill ? getSkillIconData(selectedSkill.icon) : defaultIcon;
        const skillOptions = AgentsState.skills.map((skill) => {
            const skillId = String(skill?.id || '');
            const icon = getSkillIconData(skill?.icon);
            return `
                <button type="button" role="option" aria-selected="${skillId === AgentsState.selectedSkillId ? 'true' : 'false'}"
                    tabindex="-1" class="shared-skill-select-item ${skillId === AgentsState.selectedSkillId ? 'selected' : ''}"
                    data-skill-id="${escapeHtml(skillId)}">
                    <span class="shared-skill-select-item-icon" style="background-color: ${escapeHtml(icon.color)}">${renderSkillIcon(icon, 16)}</span>
                    <span class="shared-skill-select-item-name">${escapeHtml(getSkillLabel(skill))}</span>
                    <span class="shared-skill-select-item-check">
                        ${Icons.check}
                    </span>
                </button>
            `;
        }).join('');

        container.innerHTML = formRenderer.renderSingleSelect({
            kind: 'skill',
            triggerId: 'agentSkillSelectTrigger',
            dropdownId: 'agentSkillSelectDropdown',
            iconHtml: renderSkillIcon(selectedIcon, 16),
            iconStyle: `background-color: ${escapeHtml(selectedIcon.color)}`,
            label: selectedSkill ? getSkillLabel(selectedSkill) : t('agents_skill_none', 'No skill'),
            placeholder: !selectedSkill,
            caretHtml: Icons.chevron,
            bodyHtml: `
                <button type="button" role="option" aria-selected="${!AgentsState.selectedSkillId ? 'true' : 'false'}"
                    tabindex="-1" class="shared-skill-select-item ${!AgentsState.selectedSkillId ? 'selected' : ''}" data-skill-id="">
                    <span class="shared-skill-select-item-icon" style="background-color: ${escapeHtml(defaultIcon.color)}">${renderSkillIcon(defaultIcon, 16)}</span>
                    <span class="shared-skill-select-item-name">${escapeHtml(t('agents_skill_none', 'No skill'))}</span>
                    <span class="shared-skill-select-item-check">
                        ${Icons.check}
                    </span>
                </button>
                ${skillOptions}
            `,
        });

        const selectBinding = formRenderer.bindSingleSelect({
            container,
            triggerId: 'agentSkillSelectTrigger',
            dropdownId: 'agentSkillSelectDropdown',
        });
        container.querySelectorAll('.shared-skill-select-item').forEach((item) => {
            item.addEventListener('click', (event) => {
                event.stopPropagation();
                AgentsState.selectedSkillId = String(item.dataset.skillId || '');
                selectBinding.setOpen(false);
                renderAgentSkillSelect();
            });
        });

        if (!AgentsState.skillSelectOutsideBound) {
            document.addEventListener('click', (event) => {
                const currentContainer = document.getElementById('agentSkillSelect');
                if (!currentContainer || currentContainer.contains(event.target)) return;
                closeAgentSkillDropdown();
            }, { capture: true });
            AgentsState.skillSelectOutsideBound = true;
        }
    }

    function addAgentFileId(fileId) {
        const normalized = String(fileId || '').trim();
        if (!normalized) return;
        const unique = new Set(AgentsState.selectedFileIds);
        unique.add(normalized);
        AgentsState.selectedFileIds = Array.from(unique);
    }

    function removeAgentFileId(fileId) {
        const normalized = String(fileId || '').trim();
        AgentsState.selectedFileIds = AgentsState.selectedFileIds.filter((id) => id !== normalized);
    }

    function removeAgentAssetId(assetId) {
        const normalized = String(assetId || '').trim();
        AgentsState.selectedAssetIds = AgentsState.selectedAssetIds.filter((id) => id !== normalized);
    }

    function renderAgentFilesSelected() {
        const selectedEl = document.getElementById('agentFilesSelected');
        if (!selectedEl) return;
        const assetItems = (AgentsState.currentAssets || [])
            .filter((asset) => AgentsState.selectedAssetIds.includes(String(asset.id || '')))
            .map((asset) => ({
                kind: 'asset',
                id: String(asset.id),
                name: agentAssetDisplayName(asset),
                size: asset.file_size,
                type: asset.file_type,
            }));
        const fileItems = AgentsState.selectedFileIds.map((fileId) => {
            const meta = getFileMeta(fileId);
            return { kind: 'file', id: fileId, name: meta.file_name, size: meta.file_size, type: meta.file_type };
        });
        const items = [...assetItems, ...fileItems];

        if (!items.length) {
            selectedEl.innerHTML = `
                <div class="shared-files-placeholder">
                    <span>${escapeHtml(t('automations_files_empty_optional', 'No files attached (optional)'))}</span>
                </div>
            `;
            return;
        }

        selectedEl.innerHTML = `<div class="shared-file-chip-list">${items.map((item) => {
            const iconName = fileIconName(item.type);
            const extension = fileExtensionLabel(item.name || item.id);
            const safeName = escapeHtml(item.name || item.id);
            return `
                <div class="shared-file-chip" data-kind="${escapeHtml(item.kind)}" data-id="${escapeHtml(item.id)}">
                    <div class="shared-file-chip-icon">
                        <img src="/assets/file_svgs/${escapeHtml(iconName)}" alt="${escapeHtml(extension)}" width="24" height="24" loading="lazy">
                    </div>
                    <div class="shared-file-chip-body">
                        <p class="shared-file-chip-name" title="${safeName}">${safeName}</p>
                        <p class="shared-file-chip-meta">${escapeHtml(formatFileSize(item.size))}</p>
                    </div>
                    <button type="button" class="shared-file-chip-remove" data-kind="${escapeHtml(item.kind)}" data-id="${escapeHtml(item.id)}" aria-label="${escapeHtml(t('workspace_agents_remove', 'Remove'))}">
                        ${Icons.close}
                    </button>
                </div>
            `;
        }).join('')}</div>`;

        selectedEl.querySelectorAll('.shared-file-chip-remove').forEach((button) => {
            button.addEventListener('click', (event) => {
                event.stopPropagation();
                if (button.dataset.kind === 'asset') {
                    removeAgentAssetId(button.dataset.id);
                } else {
                    removeAgentFileId(button.dataset.id);
                }
                renderAgentFilesSelected();
                renderAgentFileLibrary();
            });
        });
    }

    function toggleAgentFileLibrary(open) {
        const button = document.getElementById('agentFileLibraryBtn');
        const dropdown = document.getElementById('agentFileLibraryDropdown');
        if (!button || !dropdown) return;
        const shouldOpen = typeof open === 'boolean' ? open : !AgentsState.fileLibraryOpen;
        AgentsState.fileLibraryOpen = shouldOpen;
        button.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        dropdown.classList.toggle('open', shouldOpen);
        if (shouldOpen) renderAgentFileLibrary();
    }

    function renderAgentFileLibrary() {
        const dropdown = document.getElementById('agentFileLibraryDropdown');
        if (!dropdown) return;
        const query = String(AgentsState.fileLibrarySearch || '').trim().toLowerCase();
        const selected = new Set(AgentsState.selectedFileIds);
        const filtered = query
            ? AgentsState.files.filter((file) => String(file.file_name || file.meta?.original_filename || file.id || '').toLowerCase().includes(query))
            : AgentsState.files;

        dropdown.innerHTML = `
            <div class="shared-file-library-panel ${AgentsState.filesLoading ? 'loading' : ''}">
                <div class="shared-file-library-header">
                    <input
                        type="text"
                        class="shared-file-library-search"
                        placeholder="${escapeHtml(t('files_search_placeholder', 'Search files'))}"
                        value="${escapeHtml(AgentsState.fileLibrarySearch || '')}"
                        aria-label="${escapeHtml(t('files_search_aria', 'Search files'))}"
                    >
                    <button type="button" class="shared-file-library-refresh" data-action="refresh" aria-label="${escapeHtml(t('workspace_connections_refresh', 'Refresh'))}">
                        ${Icons.refresh}
                    </button>
                </div>
                <div class="shared-file-library-content">
                    ${filtered.length ? `<div class="shared-file-library-list">${filtered.map((file) => {
                        const normalized = upsertFileMeta(file);
                        const fileId = normalized?.file_id || '';
                        const name = normalized?.file_name || fileId;
                        const iconName = fileIconName(normalized?.file_type);
                        const extension = fileExtensionLabel(name);
                        const isSelected = selected.has(fileId);
                        const escapedName = escapeHtml(name);
                        return `
                            <button type="button" class="shared-file-library-item ${isSelected ? 'selected' : ''}" data-file-id="${escapeHtml(fileId)}" aria-pressed="${isSelected ? 'true' : 'false'}" aria-label="${escapedName}">
                                <span class="shared-file-library-item-icon">
                                    <img src="/assets/file_svgs/${escapeHtml(iconName)}" alt="${escapeHtml(extension)}" width="24" height="24" loading="lazy">
                                </span>
                                <span class="shared-file-library-item-body">
                                    <span class="shared-file-library-item-name" title="${escapedName}">${escapedName}</span>
                                    <span class="shared-file-library-item-meta">${escapeHtml(formatFileSize(normalized?.file_size))}</span>
                                </span>
                                <span class="shared-file-library-item-check">
                                    ${Icons.check}
                                </span>
                            </button>
                        `;
                    }).join('')}</div>` : `
                        <div class="shared-file-library-empty">
                            ${escapeHtml(AgentsState.files.length ? t('chat_files_quickpick_empty_files', 'No files found') : t('files_empty_title', 'No files yet'))}
                        </div>
                    `}
                </div>
            </div>
        `;

        dropdown.querySelector('.shared-file-library-search')?.addEventListener('input', (event) => {
            AgentsState.fileLibrarySearch = event.target.value || '';
            renderAgentFileLibrary();
        });
        dropdown.querySelector('[data-action="refresh"]')?.addEventListener('click', async () => {
            await loadAgentFiles();
            renderAgentFileLibrary();
        });
        dropdown.querySelectorAll('.shared-file-library-item').forEach((item) => {
            item.addEventListener('click', () => {
                const fileId = String(item.dataset.fileId || '');
                if (!fileId) return;
                if (selected.has(fileId)) removeAgentFileId(fileId);
                else addAgentFileId(fileId);
                renderAgentFilesSelected();
                renderAgentFileLibrary();
            });
        });
    }

    async function loadAgentFiles() {
        AgentsState.filesLoading = true;
        renderAgentFileLibrary();
        try {
            const files = [];
            let offset = 0;
            let hasMore = true;
            while (hasMore) {
                const page = await AgentsAPI.listFiles({ limit: 200, offset });
                files.push(...page.items);
                const nextOffset = page.offset + page.items.length;
                hasMore = Boolean(page.hasMore) && page.items.length > 0 && nextOffset > offset;
                offset = nextOffset;
            }
            AgentsState.files = files.map(upsertFileMeta).filter(Boolean);
        } catch (error) {
            console.error('[agents] failed to load files', error);
            notifyError(error.message || t('workspace_agents_load_failed', 'Failed to load agents'));
        } finally {
            AgentsState.filesLoading = false;
            renderAgentFileLibrary();
        }
    }

    async function handleAgentFileUpload(fileList) {
        const files = Array.from(fileList || []);
        if (!files.length) return;
        AgentsState.filesLoading = true;
        renderAgentFileLibrary();
        try {
            for (const file of files) {
                try {
                    const result = await AgentsAPI.uploadFile(file);
                    const fileRecord = upsertFileMeta({
                        file_id: result.file_id,
                        file_name: file.name,
                        file_size: file.size,
                        file_type: file.type,
                        file_category: result.file_category,
                    });
                    if (fileRecord) addAgentFileId(fileRecord.file_id);
                    if (result.already_uploaded) {
                        notifySuccess(t('files_upload_already_uploaded', 'File already uploaded, reusing it'));
                    }
                } catch (error) {
                    notifyError(error.message || t('files_upload_failed_named', 'Failed to upload {filename}', { filename: file.name }));
                }
            }
            await loadAgentFiles();
            renderAgentFilesSelected();
        } finally {
            AgentsState.filesLoading = false;
            const input = document.getElementById('agentAssetInput');
            if (input) input.value = '';
            renderAgentFileLibrary();
        }
    }

    function bindAgentFilesUI() {
        const input = document.getElementById('agentAssetInput');
        document.getElementById('agentFileUploadBtn')?.addEventListener('click', (event) => {
            event.preventDefault();
            input?.click();
        });
        input?.addEventListener('change', (event) => handleAgentFileUpload(event.target?.files));
        document.getElementById('agentFileLibraryBtn')?.addEventListener('click', (event) => {
            event.preventDefault();
            toggleAgentFileLibrary();
        });

        if (!AgentsState.fileLibraryOutsideBound) {
            document.addEventListener('click', (event) => {
                if (!AgentsState.fileLibraryOpen) return;
                const dropdown = document.getElementById('agentFileLibraryDropdown');
                const button = document.getElementById('agentFileLibraryBtn');
                if (dropdown?.contains(event.target) || button?.contains(event.target)) return;
                toggleAgentFileLibrary(false);
            });
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && AgentsState.fileLibraryOpen) {
                    toggleAgentFileLibrary(false);
                }
            });
            AgentsState.fileLibraryOutsideBound = true;
        }
    }

    function renderEditor(agent = null) {
        const formEl = document.getElementById('agentsEditorForm');
        const titleEl = document.getElementById('agentsEditorTitle');
        if (!formEl || !titleEl) return;

        const selectedBaseModelId = String(agent?.base_model_id || AgentsState.baseModels[0]?.model_id || '');
        const selectedSkillId = String(agent?.skill_id || '');
        const assetRows = Array.isArray(agent?.assets) ? agent.assets : [];
        AgentsState.selectedBaseModelId = selectedBaseModelId;
        AgentsState.selectedSkillId = selectedSkillId;
        AgentsState.modelSearchQuery = '';
        AgentsState.currentAssets = assetRows;
        AgentsState.initialAssetIds = assetRows.map((asset) => String(asset.id || '')).filter(Boolean);
        AgentsState.selectedAssetIds = [...AgentsState.initialAssetIds];
        AgentsState.selectedFileIds = [];
        AgentsState.fileLibrarySearch = '';
        AgentsState.fileLibraryOpen = false;

        titleEl.textContent = AgentsState.editorMode === 'edit'
            ? t('workspace_agents_editor_edit', 'Edit agent')
            : t('workspace_agents_editor_create', 'Create agent');
        const saveLabel = AgentsState.editorMode === 'edit'
            ? t('workspace_agents_save', 'Save agent')
            : t('workspace_agents_create', 'Create agent');

        const description = formRenderer.renderDescription({
            className: 'projects-create-description',
            titleClass: 'projects-create-description-title',
            title: { key: 'workspace_agents_editor_tip_title', fallback: 'How it works' },
            textClass: 'projects-create-description-text',
            paragraphs: [{
                key: 'workspace_agents_editor_tip_text',
                fallback: 'Agent instructions and optional skill content are layered on top of the base model. Saved files are attached automatically to every request.',
            }],
        });
        const nameField = formRenderer.renderControlField({
            label: { key: 'workspace_agents_name', fallback: 'Agent name' },
            control: {
                id: 'agentNameInput',
                value: agent?.name || '',
                placeholder: t('workspace_agents_name_placeholder', 'Research strategist'),
            },
        });
        const iconField = formRenderer.renderField({
            label: {
                key: 'workspace_agents_icon',
                fallback: 'Icon',
                attributes: { for: 'agentIconInput' },
            },
            contentHtml: `
                ${formRenderer.renderControl({
                    id: 'agentIconInput',
                    type: 'hidden',
                    className: '',
                    value: agent?.model_icon || agent?.icon || 'sparkles',
                })}
                <div id="agentIconPicker"></div>
                ${formRenderer.renderFieldMessage({
                    key: 'workspace_agents_icon_hint',
                    fallback: 'Pick a preset icon, upload a small image, or paste custom SVG.',
                }, 'skills-input-hint')}`,
        });
        const baseModelField = formRenderer.renderField({
            labelHtml: `<label>${escapeHtml(t('workspace_agents_base_model', 'Base model'))}</label>`,
            contentHtml: '<div class="shared-model-select" id="agentBaseModelSelect"></div>',
        });
        const skillField = formRenderer.renderField({
            labelHtml: `<label>${escapeHtml(t('workspace_agents_skill', 'Skill (optional)'))}</label>`,
            contentHtml: '<div class="shared-skill-select" id="agentSkillSelect"></div>',
        });
        const instructionField = formRenderer.renderControlField({
            label: { key: 'workspace_agents_instruction', fallback: 'Instructions' },
            control: {
                tag: 'textarea',
                id: 'agentInstructionInput',
                value: agent?.instruction || '',
                placeholder: t('workspace_agents_instruction_placeholder', 'You are a careful research assistant. Always cite uncertainties and structure answers with a short summary first.'),
                attributes: { rows: 8 },
            },
        });
        const assetsField = formRenderer.renderField({
            labelHtml: `<label>${escapeHtml(t('workspace_agents_assets', 'Reference files and images'))}</label>`,
            contentHtml: `
                ${formRenderer.renderFilePicker({
                    selectedId: 'agentFilesSelected',
                    inputId: 'agentAssetInput',
                    uploadButtonId: 'agentFileUploadBtn',
                    libraryButtonId: 'agentFileLibraryBtn',
                    dropdownId: 'agentFileLibraryDropdown',
                    uploadLabel: { key: 'automations_files_upload', fallback: 'Upload files' },
                    libraryLabel: { key: 'automations_files_choose_library', fallback: 'Choose from library' },
                    uploadIconHtml: Icons.withSvgAttributes("upload", { "aria-hidden": "true" }),
                    libraryIconHtml: Icons.withSvgAttributes("list", { "aria-hidden": "true" }),
                })}
                ${formRenderer.renderFieldMessage({
                    key: 'workspace_agents_assets_hint',
                    fallback: 'These files are attached automatically whenever this agent is used.',
                }, 'skills-input-hint')}`,
        });
        const actions = formRenderer.renderActions({
            className: 'projects-create-buttons',
            buttons: [
                { id: 'agentsEditorBackBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                {
                    id: 'agentsSaveBtn',
                    className: 'om-button border submit',
                    key: AgentsState.editorMode === 'edit' ? 'workspace_agents_save' : 'workspace_agents_create',
                    fallback: saveLabel,
                },
            ],
        });
        formEl.innerHTML = `${description}${nameField}${iconField}${baseModelField}${skillField}${instructionField}${assetsField}${actions}`;

        document.getElementById('agentsEditorBackBtn')?.addEventListener('click', () => showView('agentsLibraryView'));
        document.getElementById('agentsSaveBtn')?.addEventListener('click', () => saveAgent());

        const iconInput = document.getElementById('agentIconInput');
        const iconPickerMount = document.getElementById('agentIconPicker');
        if (iconInput && iconPickerMount && window.IconPicker?.createIconPicker) {
            const picker = window.IconPicker.createIconPicker({
                value: iconInput.value || 'sparkles',
                presetType: 'model',
                onChange: (newValue) => {
                    iconInput.value = window.IconPicker?.sanitizeIconValue
                        ? window.IconPicker.sanitizeIconValue(newValue)
                        : String(newValue || '');
                },
            });
            iconPickerMount.innerHTML = '';
            iconPickerMount.appendChild(picker.container);
            iconInput.value = window.IconPicker?.sanitizeIconValue
                ? window.IconPicker.sanitizeIconValue(picker.getValue())
                : String(picker.getValue() || '');
        } else if (iconPickerMount) {
            iconPickerMount.innerHTML = `
                <input
                    type="text"
                    class="projects-create-input"
                    value="${escapeHtml(agent?.model_icon || agent?.icon || 'sparkles')}"
                    placeholder="sparkles"
                >
            `;
            const fallbackInput = iconPickerMount.querySelector('input');
            fallbackInput?.addEventListener('input', () => {
                if (iconInput) {
                    iconInput.value = String(fallbackInput.value || '').trim();
                }
            });
        }

        renderAgentModelSelect();
        renderAgentSkillSelect();
        bindAgentFilesUI();
        renderAgentFilesSelected();
        renderAgentFileLibrary();
        if (!AgentsState.files.length && !AgentsState.filesLoading) {
            loadAgentFiles();
        }
    }

    async function loadReferenceData() {
        const [baseModelsResult, skillsResult] = await Promise.allSettled([
            AgentsAPI.listBaseModels(),
            AgentsAPI.listSkills(),
        ]);
        if (baseModelsResult.status === 'fulfilled') {
            AgentsState.baseModels = baseModelsResult.value;
        } else {
            throw baseModelsResult.reason;
        }
        AgentsState.skills = skillsResult.status === 'fulfilled' ? skillsResult.value : [];
    }

    async function loadAgents() {
        AgentsState.loading = true;
        renderList();
        try {
            const payload = await AgentsAPI.listAgents();
            AgentsState.agents = Array.isArray(payload?.agents) ? payload.agents : [];
        } catch (error) {
            console.error('[agents] failed to load agents', error);
            notifyError(error.message || t('workspace_agents_load_failed', 'Failed to load agents'));
        } finally {
            AgentsState.loading = false;
            renderList();
        }
    }

    function updateAgentInState(agent) {
        const next = Array.isArray(AgentsState.agents) ? [...AgentsState.agents] : [];
        const index = next.findIndex((item) => String(item.id) === String(agent.id));
        if (index >= 0) next[index] = agent;
        else next.unshift(agent);
        AgentsState.agents = next;
    }

    async function openEditor(mode, agentId = null) {
        AgentsState.editorMode = mode;
        AgentsState.editingAgentId = agentId;
        if (!AgentsState.baseModels.length) {
            await loadReferenceData();
        }
        let agent = null;
        if (mode === 'edit' && agentId) {
            agent = await AgentsAPI.getAgent(agentId);
            updateAgentInState(agent);
        }
        renderEditor(agent);
        showView('agentsEditorView');
    }

    async function saveAgent() {
        const name = String(document.getElementById('agentNameInput')?.value || '').trim();
        const icon = String(document.getElementById('agentIconInput')?.value || '').trim();
        const baseModelId = String(AgentsState.selectedBaseModelId || '').trim();
        const skillIdRaw = String(AgentsState.selectedSkillId || '').trim();
        const instruction = String(document.getElementById('agentInstructionInput')?.value || '');

        if (!name || !icon || !baseModelId) {
            notifyError(t('workspace_agents_validation', 'Name, icon, and base model are required.'));
            return;
        }

        const payload = {
            name,
            icon,
            base_model_id: baseModelId,
            instruction,
            skill_id: skillIdRaw || null,
        };

        try {
            let agent;
            if (AgentsState.editorMode === 'edit' && AgentsState.editingAgentId) {
                agent = await AgentsAPI.updateAgent(AgentsState.editingAgentId, payload);
            } else {
                agent = await AgentsAPI.createAgent(payload);
            }

            const selectedAssets = new Set(AgentsState.selectedAssetIds || []);
            const assetsToDelete = (AgentsState.initialAssetIds || []).filter((assetId) => !selectedAssets.has(assetId));
            for (const assetId of assetsToDelete) {
                await AgentsAPI.deleteAsset(agent.id, assetId);
            }

            const selectedFileIds = (AgentsState.selectedFileIds || []).filter(Boolean);
            if (selectedFileIds.length) {
                await AgentsAPI.attachFiles(agent.id, selectedFileIds);
            }

            if (assetsToDelete.length || selectedFileIds.length) {
                agent = await AgentsAPI.getAgent(agent.id);
            }

            updateAgentInState(agent);
            notifySuccess(AgentsState.editorMode === 'edit'
                ? t('workspace_agents_saved', 'Agent updated')
                : t('workspace_agents_created_success', 'Agent created'));
            showView('agentsLibraryView');
            await Promise.all([
                loadAgents(),
                refreshAgentModelConsumers(),
            ]);
        } catch (error) {
            notifyError(error.message || t('workspace_agents_save_failed', 'Failed to save agent'));
        }
    }

    async function deleteAgent(agent) {
        if (!agent?.id) return;
        if (!await window.showDeleteConfirm({
            message: t('workspace_agents_confirm_delete', 'Delete this agent? This cannot be undone.'),
            confirmLabel: t('workspace_agents_delete', 'Delete'),
        })) return;
        try {
            await AgentsAPI.deleteAgent(agent.id);
            AgentsState.agents = AgentsState.agents.filter((item) => String(item.id) !== String(agent.id));
            renderList();
            notifySuccess(t('workspace_agents_deleted', 'Agent deleted'));
            await refreshAgentModelConsumers();
        } catch (error) {
            notifyError(error.message || t('workspace_agents_delete_failed', 'Failed to delete agent'));
        }
    }

    async function removeSharedAgent(agent) {
        if (!agent?.id) return;
        if (!await window.showDeleteConfirm({
            title: t('common_remove_confirm_title', 'Remove item?'),
            message: t('workspace_agents_confirm_remove', 'Remove this shared agent from your workspace?'),
            confirmLabel: t('workspace_agents_remove', 'Remove'),
        })) return;
        try {
            await AgentsAPI.unsubscribe(agent.id);
            AgentsState.agents = AgentsState.agents.filter((item) => String(item.id) !== String(agent.id));
            renderList();
            notifySuccess(t('workspace_agents_removed', 'Shared agent removed'));
            await refreshAgentModelConsumers();
        } catch (error) {
            notifyError(error.message || t('workspace_agents_remove_failed', 'Failed to remove shared agent'));
        }
    }

    function renderShareView(agent, shareStatus) {
        const formEl = document.getElementById('agentsShareForm');
        if (!formEl) return;

        const shareUrlByType = {
            clone: '/agents/clone',
            live: '/agents/live',
            collaborate: '/agents/collaborate',
        };

        const existingShares = [
            { type: 'clone', id: shareStatus?.clone_share_id },
            { type: 'live', id: shareStatus?.live_share_id, count: shareStatus?.live_subscriber_count || 0 },
            { type: 'collaborate', id: shareStatus?.collaborate_share_id, count: shareStatus?.collaborate_subscriber_count || 0 },
        ]
            .filter((share) => share.id)
            .map((share) => ({
                ...share,
                share_url: `${window.location.origin}${shareUrlByType[share.type]}/${share.id}`,
            }));

        const buildUserOptions = () => AgentsState.publicUsers
            .filter((user) => String(user?.id || '') !== String(window.chatSetup?.user_id || ''))
            .map((user) => {
                const label = user?.first_name && user?.last_name
                    ? `${user.first_name} ${user.last_name}`
                    : (user?.email || user?.id || t('workspace_agents_unknown_user', 'User'));
                return `<option value="${escapeHtml(String(user.id || ''))}">${escapeHtml(label)}</option>`;
            })
            .join('');
        const shareTypeLabels = {
            clone: t('workspace_agents_share_mode_clone', 'Clone'),
            live: t('workspace_agents_share_mode_live', 'Live'),
            collaborate: t('workspace_agents_share_mode_collaborate', 'Collaborate'),
        };
        const userOptions = buildUserOptions();

        formEl.innerHTML = `
            <div class="projects-create-description">
                <p class="projects-create-description-title">${escapeHtml(t('workspace_agents_share_modes', 'Sharing modes'))}</p>
                <p class="projects-create-description-text">${escapeHtml(t('workspace_agents_share_modes_text', 'Clone gives recipients their own copy. Live lets them use your agent with synced updates. Collaborate lets recipients edit the shared source.'))}</p>
            </div>
            <div class="notes-share-mode-toggle">
                <button type="button" class="notes-share-mode-btn active" data-mode="link" id="agentsShareModeLink">
                    ${Icons.share}
                    ${escapeHtml(t('workspace_agents_share_link_tab', 'Link'))}
                </button>
                <button type="button" class="notes-share-mode-btn" data-mode="invite" id="agentsShareModeInvite">
                    ${Icons.user_add}
                    ${escapeHtml(t('workspace_agents_share_invite_tab', 'Invite'))}
                </button>
            </div>

            <div class="notes-share-mode-content" id="agentsShareLinkMode">
                <div class="notes-share-type-section">
                    <label class="notes-share-label" for="agentsShareTypeSelect">${escapeHtml(t('workspace_agents_share_mode', 'Share mode'))}</label>
                    <div class="notes-share-type-select-wrapper">
                        <select id="agentsShareTypeSelect" class="notes-share-type-select">
                            <option value="clone">${escapeHtml(t('workspace_agents_share_mode_clone', 'Clone'))}</option>
                            <option value="live">${escapeHtml(t('workspace_agents_share_mode_live', 'Live'))}</option>
                            <option value="collaborate">${escapeHtml(t('workspace_agents_share_mode_collaborate', 'Collaborate'))}</option>
                        </select>
                       ${Icons.chevron}
                    </div>
                </div>
                <button type="button" class="om-button border submit" id="agentsCreateShareLinkBtn">${escapeHtml(t('workspace_agents_create_link', 'Create or refresh link'))}</button>
                <div class="notes-share-active-section">
                    <label class="notes-share-label">${escapeHtml(t('workspace_agents_existing_links', 'Existing share links'))}</label>
                    <div class="notes-share-active-list" id="agentsExistingShares">
                    ${existingShares.length ? existingShares.map((share) => `
                        <div class="notes-share-active-item" data-share-type="${escapeHtml(share.type)}">
                            <div class="notes-share-active-info">
                                <span class="notes-share-active-label">${escapeHtml(shareTypeLabels[share.type] || share.type)}</span>
                                <span class="notes-share-active-count">${escapeHtml(share.id)}${share.count ? escapeHtml(` · ${plural(share.count, 'workspace_agents_subscribers_one', '1 subscriber', 'workspace_agents_subscribers_other', '{count} subscribers', { count: share.count })}`) : ''}</span>
                            </div>
                            <div class="notes-share-active-actions">
                                <button type="button" class="om-button border cancel notes-share-active-copy" data-action="copy-share" data-share-url="${escapeHtml(share.share_url)}" title="${escapeHtml(t('workspace_agents_copy_link', 'Copy link'))}">
                                    ${Icons.copy}
                                </button>
                                <button type="button" class="om-button border danger-nofill notes-share-active-delete" data-action="delete-share" data-share-type="${escapeHtml(share.type)}" title="${escapeHtml(t('workspace_agents_delete_share', 'Delete share'))}">
                                    ${Icons.close}
                                </button>
                            </div>
                        </div>
                    `).join('') : `<p class="skills-input-hint">${escapeHtml(t('workspace_agents_no_shares', 'No share links created yet.'))}</p>`}
                    </div>
                </div>
            </div>

            <div class="notes-share-mode-content" id="agentsShareInviteMode" hidden>
                <div class="notes-share-type-section">
                    <label class="notes-share-label" for="agentsInviteTypeSelect">${escapeHtml(t('workspace_agents_share_mode', 'Share mode'))}</label>
                    <div class="notes-share-type-select-wrapper">
                        <select id="agentsInviteTypeSelect" class="notes-share-type-select">
                            <option value="clone">${escapeHtml(t('workspace_agents_share_mode_clone', 'Clone'))}</option>
                            <option value="live">${escapeHtml(t('workspace_agents_share_mode_live', 'Live'))}</option>
                            <option value="collaborate">${escapeHtml(t('workspace_agents_share_mode_collaborate', 'Collaborate'))}</option>
                        </select>
                        ${Icons.chevron}
                    </div>
                </div>
                <div class="notes-share-invite-section">
                    <label class="notes-share-label" for="agentsInviteUsersSelect">${escapeHtml(t('workspace_agents_invite_users', 'Invite users'))}</label>
                    <select id="agentsInviteUsersSelect" class="notes-share-type-select" multiple size="8">${userOptions}</select>
                    <p class="skills-input-hint">${escapeHtml(t('workspace_agents_invite_hint', 'Pick one or more users, then send invitations using the selected share mode.'))}</p>
                    <button type="button" class="om-button border submit" id="agentsSendInvitesBtn">${escapeHtml(t('workspace_agents_send_invites', 'Send invitations'))}</button>
                </div>
            </div>
        `;

        const setMode = async (mode) => {
            const isInvite = mode === 'invite';
            formEl.querySelector('#agentsShareModeLink')?.classList.toggle('active', !isInvite);
            formEl.querySelector('#agentsShareModeInvite')?.classList.toggle('active', isInvite);
            const linkMode = formEl.querySelector('#agentsShareLinkMode');
            const inviteMode = formEl.querySelector('#agentsShareInviteMode');
            if (linkMode) linkMode.hidden = isInvite;
            if (inviteMode) inviteMode.hidden = !isInvite;
            if (!isInvite || AgentsState.publicUsersLoaded || AgentsState.publicUsersLoading) return;
            const select = formEl.querySelector('#agentsInviteUsersSelect');
            AgentsState.publicUsersLoading = true;
            if (select) {
                select.innerHTML = `<option disabled>${escapeHtml(t('workspace_agents_loading_users', 'Loading users...'))}</option>`;
            }
            try {
                AgentsState.publicUsers = await AgentsAPI.listUsers();
                AgentsState.publicUsersLoaded = true;
                if (select) {
                    select.innerHTML = buildUserOptions() || `<option disabled>${escapeHtml(t('workspace_agents_no_invite_users', 'No users available'))}</option>`;
                }
            } catch (error) {
                AgentsState.publicUsersLoaded = false;
                if (select) {
                    select.innerHTML = `<option disabled>${escapeHtml(t('workspace_agents_load_users_failed', 'Failed to load users'))}</option>`;
                }
                notifyError(error.message || t('workspace_agents_load_users_failed', 'Failed to load users'));
            } finally {
                AgentsState.publicUsersLoading = false;
            }
        };
        formEl.querySelector('#agentsShareModeLink')?.addEventListener('click', () => setMode('link'));
        formEl.querySelector('#agentsShareModeInvite')?.addEventListener('click', () => setMode('invite'));

        formEl.querySelector('#agentsCreateShareLinkBtn')?.addEventListener('click', async () => {
            const shareType = String(document.getElementById('agentsShareTypeSelect')?.value || 'collaborate');
            try {
                const share = await AgentsAPI.shareAgent(agent.id, shareType);
                try {
                    await navigator.clipboard.writeText(String(share.share_url || ''));
                    notifySuccess(t('workspace_agents_link_ready', 'Share link copied to clipboard'));
                } catch (clipboardError) {
                    console.warn('[agents] clipboard write failed', clipboardError);
                    notifyError(t('workspace_agents_clipboard_failed', 'Could not copy link to clipboard - please copy it manually'));
                    if (share.share_url) {
                        if (typeof window.showCopyTextDialog === 'function') {
                            await window.showCopyTextDialog({
                                title: t('workspace_agents_copy_manual_title', 'Copy share link'),
                                message: t('workspace_agents_copy_manual', 'Copy this share link manually:'),
                                copyText: String(share.share_url),
                            });
                        }
                    }
                }
                await openShareView(agent.id);
            } catch (error) {
                notifyError(error.message || t('workspace_agents_link_failed', 'Failed to create share link'));
            }
        });

        formEl.querySelectorAll('[data-action="delete-share"]').forEach((button) => {
            button.addEventListener('click', async () => {
                const shareType = button.dataset.shareType;
                if (!await window.showDeleteConfirm({
                    message: t('workspace_agents_delete_share_confirm', 'Delete this share link?'),
                    confirmLabel: t('workspace_agents_delete_share', 'Delete share'),
                })) return;
                try {
                    await AgentsAPI.deleteShare(agent.id, shareType);
                    notifySuccess(t('workspace_agents_share_deleted', 'Share link deleted'));
                    await openShareView(agent.id);
                } catch (error) {
                    notifyError(error.message || t('workspace_agents_share_delete_failed', 'Failed to delete share link'));
                }
            });
        });

        formEl.querySelectorAll('[data-action="copy-share"]').forEach((button, index) => {
            button.addEventListener('click', async () => {
                const share = existingShares[index];
                if (!share) return;
                try {
                    const shareUrl = String(button.dataset.shareUrl || share.share_url || '');
                    if (!shareUrl) {
                        notifyError(t('workspace_agents_link_copy_failed', 'Failed to copy share link'));
                        return;
                    }
                    await navigator.clipboard.writeText(shareUrl);
                    notifySuccess(t('workspace_agents_link_copied', 'Share link copied'));
                } catch (error) {
                    notifyError(error.message || t('workspace_agents_link_copy_failed', 'Failed to copy share link'));
                }
            });
        });

        formEl.querySelector('#agentsSendInvitesBtn')?.addEventListener('click', async () => {
            const shareType = String(document.getElementById('agentsInviteTypeSelect')?.value || 'collaborate');
            const selectedUserIds = Array.from(document.getElementById('agentsInviteUsersSelect')?.selectedOptions || [])
                .map((option) => String(option.value || '').trim())
                .filter(Boolean);
            if (!selectedUserIds.length) {
                notifyError(t('workspace_agents_invite_select_users', 'Select at least one user first.'));
                return;
            }
            try {
                await AgentsAPI.inviteUsers(agent.id, selectedUserIds, shareType);
                notifySuccess(t('workspace_agents_invites_sent', 'Invitations sent'));
            } catch (error) {
                notifyError(error.message || t('workspace_agents_invites_failed', 'Failed to send invitations'));
            }
        });
    }

    async function openShareView(agentId) {
        const currentAgent = AgentsState.agents.find((agent) => agent.id === agentId);
        if (!canManageAgentSharing(currentAgent)) {
            notifyError(t('workspace_agents_share_disabled', 'Agent sharing is disabled for your account.'));
            return;
        }
        AgentsState.shareAgentId = agentId;
        const [agent, shareStatus] = await Promise.all([
            AgentsAPI.getAgent(agentId),
            AgentsAPI.getShareStatus(agentId),
        ]);
        updateAgentInState(agent);
        renderShareView(agent, shareStatus);
        showView('agentsShareView');
    }

    async function maybeHandleIncomingSharePath() {
        if (AgentsState.acceptHandled || typeof window === 'undefined') return;
        if (!agentsEnabled()) {
            return;
        }
        const path = window.location.pathname || '';
        const match = path.match(/^\/agents\/(clone|live|collaborate)\/([a-zA-Z0-9-]+)$/);
        if (!match) return;

        AgentsState.acceptHandled = true;
        const shareType = match[1];
        const shareId = match[2];

        try {
            const preview = await AgentsAPI.previewShare(shareId);
            const resolvedShareType = String(preview?.share_type || shareType).toLowerCase();
            const canCompleteShareAction = preview?.can_complete_share_action !== false
                && preview?.base_model_accessible !== false;
            const owner = preview?.owner_name ? `\n${t('workspace_agents_share_owner', 'Owner')}: ${preview.owner_name}` : '';
            const previewNotices = [];
            if (!canCompleteShareAction) {
                previewNotices.push(t(
                    'workspace_agents_share_base_model_unavailable',
                    'You cannot add this agent because you do not have access to its base model. Ask an administrator for access, then try again.',
                ));
            }
            if (resolvedShareType === 'clone' && preview?.clone_skill_will_be_omitted === true) {
                previewNotices.push(t(
                    'workspace_agents_share_clone_skill_omitted',
                    'This agent uses a skill you cannot access. Your cloned copy will be created without that skill.',
                ));
            }
            const notices = previewNotices.length ? `\n\n${previewNotices.join('\n\n')}` : '';
            if (typeof window.showWarningConfirm !== 'function') {
                notifyError(t('workspace_agents_share_accept_confirm_unavailable', 'Shared agent confirmation is unavailable. Please reload the page and try again.'));
                window.history.replaceState({}, '', '/workspace/agents');
                return;
            }
            const confirmed = await window.showWarningConfirm({
                title: t('workspace_agents_share_accept_title', 'Add shared agent?'),
                message: `${t('workspace_agents_share_accept_prompt', 'Add this shared agent to your workspace?')}\n\n${preview?.name || t('workspace_agents_share_unknown', 'Shared agent')}${owner}${notices}`,
                cancelLabel: !canCompleteShareAction ? t('common_close', 'Close') : t('common_cancel', 'Cancel'),
                confirmLabel: t('workspace_agents_share_accept_confirm', 'Add agent'),
                confirmDisabled: !canCompleteShareAction,
            });
            if (!confirmed || !canCompleteShareAction) {
                window.history.replaceState({}, '', '/workspace/agents');
                return;
            }

            if (resolvedShareType === 'clone') {
                await AgentsAPI.cloneShare(shareId);
            } else {
                await AgentsAPI.acceptShare(shareId);
            }

            notifySuccess(t('workspace_agents_share_added', 'Shared agent added to your workspace'));
            window.history.replaceState({}, '', '/workspace/agents');
            if (typeof window.showWorkspaceContainer === 'function') {
                window.showWorkspaceContainer({ tab: 'agents' });
            }
            await Promise.all([
                loadAgents(),
                refreshAgentModelConsumers(),
            ]);
        } catch (error) {
            console.error('[agents] failed to accept shared agent', error);
            notifyError(error.message || t('workspace_agents_share_accept_failed', 'Failed to open shared agent'));
            window.history.replaceState({}, '', '/workspace/agents');
            AgentsState.acceptHandled = false;
        }
    }

    function bindStaticEvents() {
        document.getElementById('createAgentBtn')?.addEventListener('click', () => openEditor('create'));
        document.getElementById('agentsShareBackBtn')?.addEventListener('click', () => showView('agentsLibraryView'));

    }

    const AgentsWorkspaceManager = {
        async init() {
            ensureLayout();
            registerEscapeHandlers();
            if (!agentsEnabled()) {
                return;
            }
            if (AgentsState.initialized) {
                await maybeHandleIncomingSharePath();
                return;
            }
            bindStaticEvents();
            AgentsState.initialized = true;
            // Load the base-model name lookup first so the initial card render
            // never falls back to an unresolved model identifier.
            try {
                await loadReferenceData();
            } catch (error) {
                console.error('[agents] failed to load reference data', error);
                AgentsState.baseModels = [];
            }
            await loadAgents();
            await maybeHandleIncomingSharePath();
        },
        loadAgents,
        show() {
            showView('agentsLibraryView');
            return this.init();
        },
        openCreate() {
            return openEditor('create');
        },
    };

    if (typeof window !== 'undefined') {
        window.AgentsWorkspaceManager = AgentsWorkspaceManager;
        if (/^\/agents\/(clone|live|collaborate)\//.test(window.location.pathname || '')) {
            Promise.resolve().then(() => AgentsWorkspaceManager.init()).catch((error) => {
                console.error('[agents] failed to initialize shared-agent handler', error);
            });
        }
    }
})();
