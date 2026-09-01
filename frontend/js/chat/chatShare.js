(function () {
    'use strict';

    const overlay = document.getElementById('chatShareOverlay');
    const modal = overlay?.querySelector('.cs-modal');
    const closeBtn = document.getElementById('chatShareCloseBtn');
    const subtitleEl = document.getElementById('chatShareSubtitle');

    const linksSection = document.getElementById('chatShareLinksSection');
    const linkList = document.getElementById('chatShareLinkList');
    const emptySection = document.getElementById('chatShareEmptySection');

    const formSection = document.getElementById('chatShareForm');
    const formTitle = document.getElementById('chatShareFormTitle');
    const accessPublicInput = document.getElementById('chatShareAccessPublic');
    const accessAuthenticatedInput = document.getElementById('chatShareAccessAuthenticated');
    const accessInviteInput = document.getElementById('chatShareAccessInvite');
    const passwordField = document.getElementById('chatSharePasswordField');
    const passwordToggle = document.getElementById('chatSharePasswordToggle');
    const passwordContent = document.getElementById('chatSharePasswordContent');
    const passwordInput = document.getElementById('chatSharePasswordInput');
    const passwordHelper = document.getElementById('chatSharePasswordHelper');
    const passwordError = document.getElementById('chatSharePasswordError');
    const expiryToggle = document.getElementById('chatShareExpiryToggle');
    const expiryContent = document.getElementById('chatShareExpiryContent');
    const expiryInput = document.getElementById('chatShareExpiryInput');
    const expiryError = document.getElementById('chatShareExpiryError');
    const publicationField = document.getElementById('chatSharePublicationField');
    const publicationOptions = document.getElementById('chatSharePublicationOptions');
    const publicationError = document.getElementById('chatSharePublicationError');
    const inviteField = document.getElementById('chatShareInviteField');
    const inviteSearchInput = document.getElementById('chatShareInviteSearch');
    const inviteError = document.getElementById('chatShareInviteError');
    const inviteUserList = document.getElementById('chatShareInviteUserList');
    const inviteSelected = document.getElementById('chatShareInviteSelected');
    const inviteSelectedCount = document.getElementById('chatShareInviteSelectedCount');
    const inviteSelectedList = document.getElementById('chatShareInviteSelectedList');

    const noticeEl = document.getElementById('chatShareNotice');
    const primaryBtn = document.getElementById('chatSharePrimaryBtn');
    const secondaryBtn = document.getElementById('chatShareSecondaryBtn');

    const headerShareButton = document.getElementById('headerShareButton');
    const chatContainer = document.getElementById('chatContainer');

    if (!overlay || !headerShareButton || !chatContainer) {
        return;
    }

    const ACCESS_PUBLIC = 'public';
    const ACCESS_AUTHENTICATED = 'authenticated';
    const ACCESS_INVITED = 'invited';
    const ACCESS_INVITE = 'invite';

    /** @type {{ chatId: string|null, status: any, mode: 'list'|'create'|'edit', busy: boolean, publicUsers: any[], selectedUserIds: string[], usersLoaded: boolean, usersLoading: boolean, publicationOptions: any, publicationLoading: boolean, publicationLoadFailed: boolean }} */
    const state = {
        chatId: null,
        status: null,
        mode: 'list',
        busy: false,
        publicUsers: [],
        selectedUserIds: [],
        usersLoaded: false,
        usersLoading: false,
        publicationOptions: null,
        publicationLoading: false,
        publicationLoadFailed: false,
    };

    let lastFocused = null;
    let headerShareRefreshToken = 0;

    // ---------- Helpers ----------

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function setTranslatedText(element, key, fallback) {
        if (!element) return;
        element.setAttribute('data-i18n', key);
        element.textContent = t(key, fallback);
    }

    function notifyError(message) {
        if (typeof window.notifyError === 'function') {
            window.notifyError(message);
        } else {
            console.error(message);
        }
    }

    function notifySuccess(message) {
        if (typeof window.notifySuccess === 'function') {
            window.notifySuccess(message);
        }
    }

    function isChatSharingEnabled() {
        return Boolean(window.chatSetup?.enable_chat_sharing);
    }

    function setHeaderShareButtonVisible(visible) {
        headerShareButton.style.display = visible ? 'flex' : 'none';
    }

    async function syncHeaderShareVisibility() {
        const refreshToken = ++headerShareRefreshToken;
        const chatId = getCurrentChatId();
        if (!chatId) {
            if (refreshToken !== headerShareRefreshToken) return;
            setHeaderShareButtonVisible(false);
            return;
        }
        if (!window.chatSetup) {
            if (refreshToken !== headerShareRefreshToken) return;
            setHeaderShareButtonVisible(false);
            return;
        }
        if (isChatSharingEnabled()) {
            if (refreshToken !== headerShareRefreshToken) return;
            setHeaderShareButtonVisible(true);
            return;
        }
        try {
            const status = await apiJson(
                `/api/v1/chats/share/status?chat_id=${encodeURIComponent(chatId)}`,
                { method: 'GET' }
            );
            if (refreshToken !== headerShareRefreshToken) return;
            setHeaderShareButtonVisible(Boolean(status?.share_id));
        } catch (_) {
            if (refreshToken !== headerShareRefreshToken) return;
            setHeaderShareButtonVisible(false);
        }
    }

    function getCurrentChatId() {
        const id = chatContainer.getAttribute('data-chat-id');
        return id ? String(id).trim() : '';
    }

    function getActiveChatTitle() {
        const id = getCurrentChatId();
        if (!id) return '';
        // Look up the title on any rendered chat-list entry.
        const candidates = document.querySelectorAll(
            `[data-chat-id="${CSS.escape(id)}"]`
        );
        for (const el of candidates) {
            const titleNode = el.querySelector('.chat-list-title') || el.querySelector('.chat-title');
            const title = titleNode?.textContent?.trim();
            if (title) return title;
            const attr = el.getAttribute('data-chat-title')?.trim();
            if (attr) return attr;
        }
        return '';
    }

    function toIso(value) {
        if (!value) return null;
        const dt = new Date(value);
        return Number.isNaN(dt.getTime()) ? null : dt.toISOString();
    }

    function toLocalDateTimeValue(isoString) {
        if (!isoString) return '';
        const dt = new Date(isoString);
        if (Number.isNaN(dt.getTime())) return '';
        const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 16);
    }

    function formatExpiryHuman(isoString) {
        if (!isoString) return '';
        const dt = new Date(isoString);
        if (Number.isNaN(dt.getTime())) return '';
        try {
            return dt.toLocaleString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch (_) {
            return dt.toISOString();
        }
    }

    function isExpired(isoString) {
        if (!isoString) return false;
        const dt = new Date(isoString);
        if (Number.isNaN(dt.getTime())) return false;
        return dt.getTime() < Date.now();
    }

    function normalizeAccessMode(mode) {
        const normalized = String(mode || '').trim().toLowerCase();
        if (normalized === ACCESS_INVITED || normalized === ACCESS_INVITE) return ACCESS_INVITED;
        if (normalized === ACCESS_AUTHENTICATED) return ACCESS_AUTHENTICATED;
        return ACCESS_PUBLIC;
    }

    function getSelectedAccessMode() {
        if (accessInviteInput?.checked) return ACCESS_INVITE;
        return accessAuthenticatedInput?.checked ? ACCESS_AUTHENTICATED : ACCESS_PUBLIC;
    }

    function getLinkAccessModeForApi() {
        return getSelectedAccessMode() === ACCESS_AUTHENTICATED ? ACCESS_AUTHENTICATED : ACCESS_PUBLIC;
    }

    function isInviteSelected() {
        return getSelectedAccessMode() === ACCESS_INVITE;
    }

    async function apiJson(url, options = {}) {
        const response = await window.authedFetch(url, options);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok) {
            const message = payload?.detail || `Request failed (${response.status})`;
            throw new Error(message);
        }
        return payload;
    }

    function setBusy(busy) {
        state.busy = Boolean(busy);
        if (modal) {
            modal.classList.toggle('cs-busy', state.busy);
        }
        const controls = [
            primaryBtn,
            secondaryBtn,
            closeBtn,
            accessPublicInput,
            accessAuthenticatedInput,
            accessInviteInput,
            passwordToggle,
            passwordInput,
            expiryToggle,
            expiryInput,
            inviteSearchInput,
        ];
        controls.forEach((el) => {
            if (el) el.disabled = state.busy;
        });
        // Disable card actions
        if (linkList) {
            linkList.querySelectorAll('button').forEach((btn) => {
                btn.disabled = state.busy;
            });
        }
        if (inviteUserList) {
            inviteUserList.querySelectorAll('button').forEach((btn) => {
                btn.disabled = state.busy;
            });
        }
        if (publicationOptions) {
            publicationOptions.querySelectorAll('input').forEach((input) => {
                input.disabled = state.busy || state.publicationLoading;
            });
        }
    }

    function showNotice(text, type = 'info') {
        if (!noticeEl) return;
        if (!text) {
            noticeEl.hidden = true;
            noticeEl.textContent = '';
            noticeEl.className = 'cs-notice';
            return;
        }
        noticeEl.textContent = text;
        noticeEl.className = `cs-notice cs-notice-${type}`;
        noticeEl.hidden = false;
    }

    function showControlError(inputEl, errorEl, text) {
        if (!inputEl || !errorEl) return;
        if (window.FormValidation?.showInputError) {
            window.FormValidation.showInputError(inputEl, errorEl, text, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        errorEl.textContent = text || '';
        errorEl.hidden = false;
        inputEl.classList.add('cs-input-error');
        inputEl.setAttribute('aria-invalid', 'true');
    }

    function hideControlError(inputEl, errorEl) {
        if (!inputEl || !errorEl) return;
        if (window.FormValidation?.clearInputError) {
            window.FormValidation.clearInputError(inputEl, errorEl, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        errorEl.hidden = true;
        errorEl.textContent = '';
        inputEl.classList.remove('cs-input-error');
        inputEl.setAttribute('aria-invalid', 'false');
    }

    function showPasswordError(text) {
        showControlError(passwordInput, passwordError, text || t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
    }

    function hidePasswordError() {
        hideControlError(passwordInput, passwordError);
    }

    function showExpiryError(text) {
        showControlError(expiryInput, expiryError, text);
    }

    function hideExpiryError() {
        hideControlError(expiryInput, expiryError);
    }

    function showInviteError(text) {
        if (inviteUserList && !inviteUserList.hasAttribute('tabindex')) {
            inviteUserList.tabIndex = -1;
        }
        if (inviteError?.id && inviteUserList && !inviteUserList.hasAttribute('aria-describedby')) {
            inviteUserList.setAttribute('aria-describedby', inviteError.id);
        }
        showControlError(inviteUserList, inviteError, text);
    }

    function hideInviteError() {
        hideControlError(inviteUserList, inviteError);
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[c]));
    }

    function publicationPreviewText(preview) {
        if (!preview || typeof preview !== 'object') return '';
        const itemCount = Array.isArray(preview.items) ? preview.items.length : 0;
        if (preview.output_type === 'quiz') {
            const key = itemCount === 1
                ? 'chat_share_publication_quiz_preview_one'
                : 'chat_share_publication_quiz_preview_other';
            const fallback = itemCount === 1 ? '{count} question with answer' : '{count} questions with answers';
            return t(key, fallback).replace('{count}', String(itemCount));
        }
        if (preview.output_type === 'flashcards') {
            const key = itemCount === 1
                ? 'chat_share_publication_flashcards_preview_one'
                : 'chat_share_publication_flashcards_preview_other';
            const fallback = itemCount === 1 ? '{count} card with front and back' : '{count} cards with fronts and backs';
            return t(key, fallback).replace('{count}', String(itemCount));
        }
        if (preview.output_type === 'mcp_app') {
            const text = String(preview.text || '').trim();
            return text.length > 180 ? `${text.slice(0, 179).trim()}…` : text;
        }
        return '';
    }

    function relevantPublicationTurns() {
        const turns = Array.isArray(state.publicationOptions?.turns) ? state.publicationOptions.turns : [];
        return turns.filter((turn) => {
            const versions = Array.isArray(turn?.versions) ? turn.versions : [];
            return versions.length > 1
                || !versions.some((version) => version?.selected === true)
                || versions.some((version) => Array.isArray(version?.static_outputs) && version.static_outputs.length > 0);
        });
    }

    function renderPublicationOptions() {
        if (!publicationField || !publicationOptions) return;
        if (state.publicationLoading) {
            publicationField.hidden = false;
            publicationOptions.innerHTML = `<p class="cs-publication-loading">${escapeHtml(t('chat_share_publication_loading', 'Loading saved response versions…'))}</p>`;
            return;
        }
        const turns = relevantPublicationTurns();
        publicationField.hidden = turns.length === 0 && !state.publicationLoadFailed;
        if (state.publicationLoadFailed) {
            publicationField.hidden = false;
            publicationOptions.innerHTML = '';
            if (publicationError) {
                publicationError.textContent = t('chat_share_publication_load_failed', 'Saved response versions could not be loaded. Try again before publishing.');
                publicationError.hidden = false;
            }
            return;
        }
        if (publicationError) {
            publicationError.hidden = true;
            publicationError.textContent = '';
        }
        if (!turns.length) {
            publicationOptions.innerHTML = '';
            return;
        }

        publicationOptions.innerHTML = turns.map((turn, turnIndex) => {
            const versions = Array.isArray(turn.versions) ? turn.versions : [];
            const legend = String(turn.prompt_preview || t('chat_share_publication_turn_fallback', 'Assistant response'));
            return `<fieldset class="cs-publication-turn" data-publication-turn="${escapeHtml(turn.reference_id)}"><legend>${escapeHtml(legend)}</legend>${versions.map((version, versionIndex) => {
                const selected = version.selected === true;
                const outputs = Array.isArray(version.static_outputs) ? version.static_outputs : [];
                const radioId = `chatShareVersion-${turnIndex}-${versionIndex}`;
                return `<div class="cs-publication-version${selected ? ' is-selected' : ''}" data-publication-version="${escapeHtml(version.message_id)}"><input id="${radioId}" type="radio" name="chatShareVersion-${turnIndex}" value="${escapeHtml(version.message_id)}" data-reference-id="${escapeHtml(turn.reference_id)}" ${selected ? 'checked' : ''}><label class="cs-publication-version-copy" for="${radioId}"><span class="cs-publication-version-title">${escapeHtml(t('chat_share_publication_version_label', 'Saved answer {number}').replace('{number}', String(versionIndex + 1)))}</span><span class="cs-publication-version-preview">${escapeHtml(version.preview || '')}</span></label>${outputs.length ? `<div class="cs-publication-static-outputs" ${selected ? '' : 'hidden'}><span class="cs-publication-output-title">${escapeHtml(t('chat_share_publication_static_heading', 'Static tool outputs'))}</span>${outputs.map((output) => `<label class="cs-publication-output"><input type="checkbox" value="${escapeHtml(output.id)}" ${output.approved && selected ? 'checked' : ''}><span><span class="cs-publication-output-title">${escapeHtml(output.title || output.output_type)}</span><span class="cs-publication-output-preview">${escapeHtml(publicationPreviewText(output.preview))}</span></span></label>`).join('')}</div>` : ''}</div>`;
            }).join('')}</fieldset>`;
        }).join('');

        publicationOptions.querySelectorAll('input[type="radio"]').forEach((radio) => {
            radio.addEventListener('change', () => {
                const referenceId = String(radio.dataset.referenceId || '');
                const selectedMessageId = String(radio.value || '');
                const restoreFocus = document.activeElement === radio;
                const turn = state.publicationOptions.turns.find((candidate) => candidate.reference_id === referenceId);
                if (!turn) return;
                turn.versions.forEach((version) => {
                    version.selected = version.message_id === selectedMessageId;
                });
                renderPublicationOptions();
                if (restoreFocus) {
                    const replacement = Array.from(publicationOptions.querySelectorAll('input[type="radio"]'))
                        .find((candidate) => (
                            String(candidate.dataset.referenceId || '') === referenceId
                            && String(candidate.value || '') === selectedMessageId
                        ));
                    replacement?.focus();
                }
            });
        });
        publicationOptions.querySelectorAll('.cs-publication-output input[type="checkbox"]').forEach((checkbox) => {
            checkbox.addEventListener('change', () => {
                const outputId = String(checkbox.value || '');
                state.publicationOptions.turns.forEach((turn) => turn.versions.forEach((version) => {
                    (version.static_outputs || []).forEach((output) => {
                        if (output.id === outputId) output.approved = checkbox.checked;
                    });
                }));
            });
        });
    }

    function collectPublicationSelection() {
        const responseVersions = {};
        const approvedOutputIds = [];
        const turns = Array.isArray(state.publicationOptions?.turns) ? state.publicationOptions.turns : [];
        turns.forEach((turn) => {
            const selected = (turn.versions || []).find((version) => version.selected === true);
            if (!selected) return;
            responseVersions[turn.reference_id] = selected.message_id;
            (selected.static_outputs || []).forEach((output) => {
                if (output.approved === true) approvedOutputIds.push(output.id);
            });
        });
        return {
            response_versions: responseVersions,
            approved_output_ids: approvedOutputIds,
        };
    }

    async function loadPublicationOptions() {
        if (!state.chatId) return;
        state.publicationLoading = true;
        state.publicationLoadFailed = false;
        renderPublicationOptions();
        try {
            state.publicationOptions = await apiJson(
                `/api/v1/chats/share/publication/options?chat_id=${encodeURIComponent(state.chatId)}`,
                { method: 'GET' }
            );
        } catch (error) {
            state.publicationOptions = null;
            state.publicationLoadFailed = true;
            throw error;
        } finally {
            state.publicationLoading = false;
            renderPublicationOptions();
        }
    }

    function getUserInitials(user) {
        const source = String(user?.display_name || '').trim();
        if (!source) return '??';
        const parts = source.split(/\s+/).filter(Boolean);
        if (parts.length >= 2) {
            return `${parts[0][0] || ''}${parts[1][0] || ''}`.toUpperCase();
        }
        return source.slice(0, 2).toUpperCase();
    }

    function renderInviteUsers(users) {
        if (!inviteUserList) return;
        if (!users || users.length === 0) {
            inviteUserList.innerHTML = `<div class="cs-invite-state">${escapeHtml(t('chat_share_invite_no_users', 'No users available to invite.'))}</div>`;
            return;
        }

        inviteUserList.innerHTML = users.map((user) => {
            const isSelected = state.selectedUserIds.includes(user.id);
            return `
                <button type="button" class="cs-invite-user-item ${isSelected ? 'is-selected' : ''}" data-user-id="${escapeHtml(user.id)}">
                    <span class="cs-invite-avatar">${escapeHtml(getUserInitials(user))}</span>
                    <span class="cs-invite-user-info">
                        <span class="cs-invite-user-name">${escapeHtml(user.display_name || t('chat_share_unknown_user', 'Unknown user'))}</span>
                    </span>
                    <span class="cs-invite-check" aria-hidden="true">
                        ${Icons.check}
                    </span>
                </button>
            `;
        }).join('');

        inviteUserList.querySelectorAll('.cs-invite-user-item').forEach((item) => {
            item.addEventListener('click', () => toggleInviteUser(item.dataset.userId));
        });
    }

    function updateSelectedUsers() {
        if (!inviteSelected || !inviteSelectedCount || !inviteSelectedList) return;
        const selectedUsers = state.publicUsers.filter((user) => state.selectedUserIds.includes(user.id));
        inviteSelected.hidden = selectedUsers.length === 0;
        inviteSelectedCount.textContent = String(selectedUsers.length);
        inviteSelectedList.innerHTML = selectedUsers.map((user) => `
            <span class="cs-invite-selected-chip" data-user-id="${escapeHtml(user.id)}">
                <span>${escapeHtml(user.display_name || t('chat_share_unknown_user', 'Unknown user'))}</span>
                <button type="button" data-user-id="${escapeHtml(user.id)}" aria-label="${escapeHtml(t('chat_share_invite_remove_user_aria', 'Remove user'))}: ${escapeHtml(user.display_name || t('chat_share_unknown_user', 'user'))}">
                    ${Icons.close}
                </button>
            </span>
        `).join('');
        inviteSelectedList.querySelectorAll('button[data-user-id]').forEach((btn) => {
            btn.addEventListener('click', () => toggleInviteUser(btn.dataset.userId));
        });

        if (inviteUserList) {
            inviteUserList.querySelectorAll('.cs-invite-user-item').forEach((item) => {
                item.classList.toggle('is-selected', state.selectedUserIds.includes(item.dataset.userId));
            });
        }
    }

    function toggleInviteUser(userId) {
        const normalized = String(userId || '').trim();
        if (!normalized) return;
        const idx = state.selectedUserIds.indexOf(normalized);
        if (idx >= 0) {
            state.selectedUserIds.splice(idx, 1);
        } else {
            state.selectedUserIds.push(normalized);
        }
        if (state.selectedUserIds.length) hideInviteError();
        updateSelectedUsers();
    }

    function filterInviteUsers() {
        const term = String(inviteSearchInput?.value || '').trim().toLowerCase();
        const users = term
            ? state.publicUsers.filter((user) => {
                const haystack = [user.display_name].filter(Boolean).join(' ').toLowerCase();
                return haystack.includes(term);
            })
            : state.publicUsers;
        renderInviteUsers(users);
        updateSelectedUsers();
    }

    async function loadInviteUsers() {
        if (!inviteUserList || state.usersLoading || state.usersLoaded) return;
        state.usersLoading = true;
        inviteUserList.innerHTML = `<div class="cs-invite-state">${escapeHtml(t('chat_share_invite_loading_users', 'Loading users...'))}</div>`;
        try {
            const users = [];
            const seenUserIds = new Set();
            let offset = 0;
            const limit = 100;
            while (true) {
                const response = await window.authedFetch(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, { method: 'GET' });
                let page = null;
                try {
                    page = await response.json();
                } catch (_) {
                    page = null;
                }
                if (!response.ok) {
                    throw new Error(page?.detail || `Request failed (${response.status})`);
                }
                const pageUsers = Array.isArray(page) ? page : [];
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
            state.publicUsers = users;
            state.usersLoaded = true;
            filterInviteUsers();
        } catch (error) {
            inviteUserList.innerHTML = `<div class="cs-invite-state">${escapeHtml(t('chat_share_invite_load_failed', 'Failed to load users.'))}</div>`;
            notifyError(error?.message || t('chat_share_invite_load_failed', 'Failed to load users.'));
        } finally {
            state.usersLoading = false;
        }
    }

    // ---------- Render ----------

    function renderHeaderSubtitle() {
        if (!subtitleEl) return;
        const title = getActiveChatTitle();
        if (title) {
            subtitleEl.textContent = t('chat_share_modal_subtitle_sharing', 'Sharing "{title}"').replace('{title}', title);
        } else {
            subtitleEl.textContent = t('chat_share_modal_subtitle_default', 'Create a link to share this conversation.');
        }
    }

    function renderLinkCard(status) {
        const accessMode = normalizeAccessMode(status?.access_mode);
        const url = String(status?.share_url || '');
        const hasPassword = Boolean(status?.has_password);
        const expiresAt = status?.expires_at || null;
        const createdAt = status?.created_at || null;
        const expired = isExpired(expiresAt);

        const accessChip =
            accessMode === ACCESS_INVITED
                ? `<span class="cs-chip">
                       ${Icons.user_add}
                       ${escapeHtml(t('chat_share_chip_invited', 'Invited users'))}
                   </span>`
                : accessMode === ACCESS_AUTHENTICATED
                ? `<span class="cs-chip">
                       ${Icons.user}
                       ${escapeHtml(t('chat_share_chip_authenticated', 'Signed-in only'))}
                   </span>`
                : `<span class="cs-chip">
                       ${Icons.globe}
                       ${escapeHtml(t('chat_share_chip_public', 'Anyone with link'))}
                   </span>`;

        const passwordChip = hasPassword
            ? `<span class="cs-chip">
                   ${Icons.lock}
                   ${escapeHtml(t('chat_share_chip_password', 'Password'))}
               </span>`
            : '';

        let expiryChip = '';
        if (expiresAt) {
            const human = formatExpiryHuman(expiresAt);
            expiryChip = `<span class="cs-chip ${expired ? 'cs-chip-danger' : ''}">
                   ${Icons.clock}
                   ${escapeHtml(expired ? t('chat_share_chip_expired', 'Expired') : t('chat_share_chip_expires', 'Expires'))} ${escapeHtml(human)}
               </span>`;
        }

        const createdChip = createdAt
            ? `<span class="cs-chip cs-chip-muted">${escapeHtml(t('chat_share_chip_created', 'Created'))} ${escapeHtml(formatExpiryHuman(createdAt))}</span>`
            : '';

        return `
            <div class="cs-link-card ${expired ? 'cs-link-card-expired' : ''}">
                <div class="cs-link-url-row">
                    <input type="text" class="cs-link-url" value="${escapeHtml(url)}" readonly aria-label="${escapeHtml(t('chat_share_link_aria', 'Share link'))}">
                </div>
                <div class="cs-link-meta">
                    ${accessChip}
                    ${passwordChip}
                    ${expiryChip}
                    ${createdChip}
                </div>
                <div class="cs-link-actions">
                    <button type="button" class="om-button border cancel" data-action="copy" title="${escapeHtml(t('chat_share_copy_link', 'Copy link'))}">
                        ${Icons.copy}
                        ${escapeHtml(t('chat_share_copy', 'Copy'))}
                    </button>
                    <button type="button" class="om-button border cancel" data-action="open">
                        ${Icons.open_window}
                        ${escapeHtml(t('chat_share_open', 'Open'))}
                    </button>
                    <button type="button" class="om-button border cancel" data-action="edit">
                        ${Icons.create}
                        ${escapeHtml(t('chat_share_edit', 'Edit'))}
                    </button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete">
                        ${Icons.trash}
                        ${escapeHtml(t('chat_share_delete', 'Delete'))}
                    </button>
                </div>
            </div>
        `;
    }

    function bindLinkCardActions() {
        if (!linkList) return;
        linkList.querySelectorAll('.cs-link-card').forEach((card) => {
            card.querySelector('[data-action="copy"]')?.addEventListener('click', () => {
                const url = card.querySelector('.cs-link-url')?.value || '';
                copyToClipboard(url);
            });
            card.querySelector('[data-action="open"]')?.addEventListener('click', () => {
                const url = card.querySelector('.cs-link-url')?.value || '';
                if (url) window.open(url, '_blank', 'noopener,noreferrer');
            });
            card.querySelector('[data-action="edit"]')?.addEventListener('click', () => {
                enterEditMode();
            });
            card.querySelector('[data-action="delete"]')?.addEventListener('click', () => {
                confirmDelete();
            });
        });
    }

    function applyMode() {
        // Visibility per mode
        if (state.mode === 'list') {
            const hasShare = Boolean(state.status?.share_id);
            if (linksSection) linksSection.hidden = !hasShare;
            if (emptySection) emptySection.hidden = hasShare;
            if (formSection) formSection.hidden = true;

            if (hasShare && linkList) {
                linkList.innerHTML = renderLinkCard(state.status);
                bindLinkCardActions();
            } else if (linkList) {
                linkList.innerHTML = '';
            }

            if (primaryBtn) {
                setTranslatedText(
                    primaryBtn,
                    hasShare ? 'chat_share_new_link' : 'chat_share_create_link',
                    hasShare ? 'New link' : 'Create link'
                );
                primaryBtn.style.display = '';
            }
            if (secondaryBtn) {
                setTranslatedText(secondaryBtn, 'chat_share_done', 'Done');
                secondaryBtn.style.display = '';
            }
        } else {
            // create or edit
            if (linksSection) linksSection.hidden = true;
            if (emptySection) emptySection.hidden = true;
            if (formSection) formSection.hidden = false;

            if (formTitle) {
                const inviteSelected = isInviteSelected();
                setTranslatedText(
                    formTitle,
                    inviteSelected
                        ? 'chat_share_invite_users'
                        : (state.mode === 'edit' ? 'chat_share_edit_link' : 'chat_share_create_new_link'),
                    inviteSelected
                        ? 'Invite users'
                        : (state.mode === 'edit' ? 'Edit link' : 'Create new link')
                );
            }
            if (primaryBtn) {
                const inviteSelected = isInviteSelected();
                setTranslatedText(
                    primaryBtn,
                    inviteSelected
                        ? 'chat_share_send_invites'
                        : (state.mode === 'edit' ? 'chat_share_save_changes' : 'chat_share_create_link'),
                    inviteSelected
                        ? 'Send invites'
                        : (state.mode === 'edit' ? 'Save changes' : 'Create link')
                );
                primaryBtn.style.display = '';
            }
            if (secondaryBtn) {
                setTranslatedText(
                    secondaryBtn,
                    state.status?.share_id ? 'chat_share_cancel' : 'chat_share_done',
                    state.status?.share_id ? 'Cancel' : 'Done'
                );
                secondaryBtn.style.display = '';
            }
        }
        showNotice('');
        updateAccessModeFields();
        if (state.mode !== 'list') {
            renderPublicationOptions();
        }
    }

    function updateAccessModeFields() {
        const invite = isInviteSelected();
        if (passwordField) passwordField.hidden = invite;
        if (inviteField) inviteField.hidden = !invite;
        hideInviteError();
        if (invite) {
            if (passwordToggle) passwordToggle.checked = false;
            if (passwordContent) passwordContent.hidden = true;
            hidePasswordError();
            void loadInviteUsers();
        }
        if (primaryBtn && state.mode !== 'list') {
            setTranslatedText(
                primaryBtn,
                invite
                    ? 'chat_share_send_invites'
                    : (state.mode === 'edit' ? 'chat_share_save_changes' : 'chat_share_create_link'),
                invite
                    ? 'Send invites'
                    : (state.mode === 'edit' ? 'Save changes' : 'Create link')
            );
        }
        if (formTitle && state.mode !== 'list') {
            setTranslatedText(
                formTitle,
                invite
                    ? 'chat_share_invite_users'
                    : (state.mode === 'edit' ? 'chat_share_edit_link' : 'chat_share_create_new_link'),
                invite
                    ? 'Invite users'
                    : (state.mode === 'edit' ? 'Edit link' : 'Create new link')
            );
        }
    }

    function populateFormFromStatus() {
        const status = state.status || {};
        const accessMode = normalizeAccessMode(status.access_mode);
        if (accessPublicInput) accessPublicInput.checked = accessMode === ACCESS_PUBLIC;
        if (accessAuthenticatedInput) accessAuthenticatedInput.checked = accessMode === ACCESS_AUTHENTICATED;
        if (accessInviteInput) accessInviteInput.checked = accessMode === ACCESS_INVITED;

        const hasPassword = Boolean(status.has_password);
        if (passwordToggle) passwordToggle.checked = hasPassword;
        if (passwordContent) passwordContent.hidden = !hasPassword;
        if (passwordInput) {
            passwordInput.value = '';
            passwordInput.placeholder = hasPassword
                ? t('chat_share_password_new_placeholder', 'Enter a new password')
                : t('chat_share_password_placeholder', 'Enter a password');
        }
        if (passwordHelper) {
            passwordHelper.hidden = !hasPassword;
        }
        hidePasswordError();

        const hasExpiry = Boolean(status.expires_at);
        if (expiryToggle) expiryToggle.checked = hasExpiry;
        if (expiryContent) expiryContent.hidden = !hasExpiry;
        if (expiryInput) expiryInput.value = hasExpiry ? toLocalDateTimeValue(status.expires_at) : '';
        state.selectedUserIds = Array.isArray(status.invited_user_ids)
            ? status.invited_user_ids.map((userId) => String(userId || '').trim()).filter(Boolean)
            : [];
        if (inviteSearchInput) inviteSearchInput.value = '';
        updateSelectedUsers();
        updateAccessModeFields();
    }

    function resetFormForCreate() {
        state.status = state.status || {};
        if (accessPublicInput) accessPublicInput.checked = true;
        if (accessAuthenticatedInput) accessAuthenticatedInput.checked = false;
        if (accessInviteInput) accessInviteInput.checked = false;
        if (passwordToggle) passwordToggle.checked = false;
        if (passwordContent) passwordContent.hidden = true;
        if (passwordInput) {
            passwordInput.value = '';
            passwordInput.placeholder = t('chat_share_password_placeholder', 'Enter a password');
        }
        if (passwordHelper) passwordHelper.hidden = true;
        hidePasswordError();
        if (expiryToggle) expiryToggle.checked = false;
        if (expiryContent) expiryContent.hidden = true;
        if (expiryInput) expiryInput.value = '';
        state.selectedUserIds = [];
        if (inviteSearchInput) inviteSearchInput.value = '';
        updateSelectedUsers();
        updateAccessModeFields();
    }

    function enterEditMode() {
        state.mode = 'edit';
        populateFormFromStatus();
        applyMode();
    }

    function enterCreateMode() {
        state.mode = 'create';
        resetFormForCreate();
        applyMode();
    }

    function enterListMode() {
        state.mode = 'list';
        applyMode();
    }

    // ---------- API actions ----------

    async function loadShareStatus(chatId) {
        state.chatId = chatId;
        const status = await apiJson(
            `/api/v1/chats/share/status?chat_id=${encodeURIComponent(chatId)}`,
            { method: 'GET' }
        );
        state.status = status || {};
        return state.status;
    }

    async function copyToClipboard(value) {
        const text = String(value || '').trim();
        if (!text) return;
        try {
            await navigator.clipboard.writeText(text);
            notifySuccess(t('chat_share_link_copied_notice', 'Link copied'));
        } catch (_) {
            try {
                const tmp = document.createElement('textarea');
                tmp.value = text;
                document.body.appendChild(tmp);
                tmp.select();
                document.execCommand('copy');
                document.body.removeChild(tmp);
                notifySuccess(t('chat_share_link_copied_notice', 'Link copied'));
            } catch (e) {
                notifyError(t('chat_share_link_copy_failed', 'Failed to copy link'));
            }
        }
    }

    function validateForm() {
        hidePasswordError();
        hideExpiryError();
        hideInviteError();
        showNotice('');
        if (state.publicationLoading || state.publicationLoadFailed || !state.publicationOptions) {
            if (publicationError) {
                publicationError.textContent = t('chat_share_publication_required_error', 'Load the saved response review before publishing.');
                publicationError.hidden = false;
            }
            publicationField?.scrollIntoView?.({ block: 'nearest' });
            return false;
        }
        const incompleteTurn = (state.publicationOptions.turns || []).some(
            (turn) => !(turn.versions || []).some((version) => version.selected === true)
        );
        if (incompleteTurn) {
            if (publicationError) {
                publicationError.textContent = t('chat_share_publication_select_all_error', 'Choose one saved answer for every response before publishing.');
                publicationError.hidden = false;
            }
            publicationField?.scrollIntoView?.({ block: 'nearest' });
            return false;
        }
        if (isInviteSelected() && state.selectedUserIds.length === 0) {
            showInviteError(t('chat_share_invite_select_error', 'Select at least one user to invite.'));
            inviteUserList?.focus?.();
            return false;
        }
        if (!isInviteSelected() && passwordToggle?.checked) {
            const newPassword = String(passwordInput?.value || '').trim();
            const isCreating = state.mode === 'create';
            const wasSet = Boolean(state.status?.has_password);
            if (isCreating && !newPassword) {
                showPasswordError(t('chat_share_password_required_error', 'Please enter a password.'));
                passwordInput?.focus();
                return false;
            }
            if (!isCreating && !wasSet && !newPassword) {
                showPasswordError(t('chat_share_password_required_error', 'Please enter a password.'));
                passwordInput?.focus();
                return false;
            }
            if (newPassword && newPassword.length < 8) {
                showPasswordError(t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
                passwordInput?.focus();
                return false;
            }
        }
        if (expiryToggle?.checked) {
            const expiryValue = String(expiryInput?.value || '').trim();
            if (!expiryValue) {
                showExpiryError(t('chat_share_expiry_required_error', 'Please pick an expiration date and time.'));
                expiryInput?.focus();
                return false;
            }
            const expiryIso = toIso(expiryValue);
            if (!expiryIso) {
                showExpiryError(t('chat_share_expiry_invalid_error', 'Invalid expiration date and time.'));
                expiryInput?.focus();
                return false;
            }
            const expiryMs = new Date(expiryIso).getTime();
            if (expiryMs <= Date.now()) {
                showExpiryError(t('chat_share_expiry_future_error', 'Expiration must be in the future.'));
                expiryInput?.focus();
                return false;
            }
        }
        return true;
    }

    async function submitForm() {
        if (!state.chatId) return;
        if (!validateForm()) return;
        showNotice('');

        const isCreating = state.mode === 'create' || !state.status?.share_id;
        const accessMode = getSelectedAccessMode();

        if (accessMode === ACCESS_INVITE) {
            const payload = {
                chat_id: state.chatId,
                user_ids: state.selectedUserIds,
                publication: collectPublicationSelection(),
            };
            if (expiryToggle?.checked) {
                const iso = toIso(expiryInput?.value || '');
                if (iso) payload.expires_at = iso;
            }
            const result = await apiJson('/api/v1/chats/share/invite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            state.status = {
                ...state.status,
                share_id: result?.share_id || null,
                share_url: result?.share_url || null,
                created_at: result?.created_at || null,
                access_mode: result?.access_mode || ACCESS_INVITED,
                has_password: false,
                expires_at: result?.expires_at || null,
                invited_user_ids: result?.invited_user_ids || state.selectedUserIds.slice(),
            };
            state.selectedUserIds = [];
            updateSelectedUsers();
            notifySuccess(result?.message || t('chat_share_invites_sent', 'Invitations sent'));
            enterListMode();
            return;
        }

        if (isCreating) {
            // Build single payload for create
            const payload = {
                chat_id: state.chatId,
                access_mode: getLinkAccessModeForApi(),
                publication: collectPublicationSelection(),
            };
            if (passwordToggle?.checked) {
                const pw = String(passwordInput?.value || '').trim();
                if (pw) payload.password = pw;
            }
            if (expiryToggle?.checked) {
                const iso = toIso(expiryInput?.value || '');
                if (iso) payload.expires_at = iso;
            }
            const result = await apiJson('/api/v1/chats/share', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            state.status = result || {};
            notifySuccess(t('chat_share_created_notice', 'Share link created'));
            enterListMode();
            return;
        }

        // Editing existing share — apply only changes
        const prev = state.status || {};
        const prevAccess = normalizeAccessMode(prev.access_mode);
        const requestedAccess = accessMode === ACCESS_INVITE ? ACCESS_INVITED : accessMode;
        const ops = [];
        const nextPublication = collectPublicationSelection();
        const previousPublication = prev.publication || { response_versions: {}, approved_output_ids: [] };
        if (JSON.stringify(nextPublication) !== JSON.stringify(previousPublication)) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/publication', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId, publication: nextPublication }),
                })
            );
        }

        if (requestedAccess !== prevAccess && requestedAccess !== ACCESS_INVITED) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/access/change', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId, access_mode: requestedAccess }),
                })
            );
        }

        // Password
        const wasPwSet = Boolean(prev.has_password);
        const wantsPw = Boolean(passwordToggle?.checked);
        const newPw = String(passwordInput?.value || '').trim();

        if (wantsPw && !wasPwSet && newPw) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/password/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId, password: newPw }),
                })
            );
        } else if (wantsPw && wasPwSet && newPw) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/password/change', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId, password: newPw }),
                })
            );
        } else if (!wantsPw && wasPwSet) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/password/remove', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId }),
                })
            );
        }

        // Expiry
        const wasExpSet = Boolean(prev.expires_at);
        const wantsExp = Boolean(expiryToggle?.checked);
        const newExpIso = wantsExp ? toIso(expiryInput?.value || '') : null;
        const prevExpIso = prev.expires_at || null;

        if (wantsExp && !wasExpSet && newExpIso) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/expiry/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId, expires_at: newExpIso }),
                })
            );
        } else if (wantsExp && wasExpSet && newExpIso && newExpIso !== prevExpIso) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/expiry/change', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId, expires_at: newExpIso }),
                })
            );
        } else if (!wantsExp && wasExpSet) {
            ops.push(() =>
                apiJson('/api/v1/chats/share/expiry/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_id: state.chatId }),
                })
            );
        }

        if (!ops.length) {
            notifySuccess(t('chat_share_no_changes_notice', 'No changes to save'));
            enterListMode();
            return;
        }

        let lastResult = null;
        for (const op of ops) {
            lastResult = await op();
        }
        if (lastResult && typeof lastResult === 'object') {
            state.status = lastResult;
        } else {
            await loadShareStatus(state.chatId);
        }
        notifySuccess(t('chat_share_updated_notice', 'Share link updated'));
        enterListMode();
    }

    async function confirmDelete() {
        if (!state.chatId || !state.status?.share_id) return;
        const ok = await window.showDeleteConfirm({
            message: t('chat_share_delete_confirm', 'Delete this share link? This cannot be undone.'),
            confirmLabel: t('chat_share_delete', 'Delete'),
        });
        if (!ok) return;
        await runWithBusy(async () => {
            await apiJson('/api/v1/chats/share/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_id: state.chatId }),
            });
            state.status = {
                share_id: null,
                share_url: null,
                created_at: null,
                access_mode: ACCESS_PUBLIC,
                has_password: false,
                expires_at: null,
            };
            notifySuccess(t('chat_share_deleted_notice', 'Share link deleted'));
            enterListMode();
        });
    }

    async function runWithBusy(task) {
        if (state.busy) return;
        setBusy(true);
        try {
            await task();
        } catch (error) {
            const message = error?.message || 'Share request failed';
            notifyError(message);
            showNotice(message, 'error');
        } finally {
            setBusy(false);
        }
    }

    // ---------- Modal show / hide ----------

    function setOverlayHidden(hidden) {
        overlay.hidden = hidden;
        overlay.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    }

    async function openModalForChat(chatId) {
        const normalized = String(chatId || '').trim();
        if (!normalized) {
            notifyError(t('chat_share_select_chat_first', 'Select a chat first.'));
            return;
        }

        lastFocused = document.activeElement;
        setOverlayHidden(false);
        requestAnimationFrame(() => overlay.classList.add('cs-active'));

        renderHeaderSubtitle();
        // Show loading state in body
        if (linksSection) linksSection.hidden = true;
        if (emptySection) emptySection.hidden = true;
        if (formSection) formSection.hidden = true;
        showNotice('Loading…', 'info');

        await runWithBusy(async () => {
            await loadShareStatus(normalized);
            if (!isChatSharingEnabled() && !state.status?.share_id) {
                closeModal();
                notifyError(t('chat_share_disabled_error', 'Chat sharing is disabled for your account.'));
                return;
            }
            await loadPublicationOptions();
            // Default mode after load
            if (state.status?.share_id) {
                state.mode = 'list';
            } else {
                state.mode = 'create';
                resetFormForCreate();
            }
            applyMode();
            showNotice('');
        });

        // Focus management
        setTimeout(() => {
            const focusTarget =
                modal?.querySelector('input:not([disabled]), button:not([disabled])');
            focusTarget?.focus();
        }, 80);
    }

    function openModal() {
        return openModalForChat(getCurrentChatId());
    }

    function closeModal() {
        overlay.classList.remove('cs-active');
        setTimeout(() => {
            if (!overlay.classList.contains('cs-active')) {
                setOverlayHidden(true);
            }
        }, 180);
        if (lastFocused && typeof lastFocused.focus === 'function') {
            try { lastFocused.focus(); } catch (_) {}
        }
        lastFocused = null;
    }

    // ---------- Event wiring ----------

    headerShareButton.addEventListener('click', () => {
        void openModal();
    });

    closeBtn?.addEventListener('click', closeModal);

    overlay.addEventListener('click', (event) => {
        if (event.target === overlay) {
            closeModal();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !overlay.hidden) {
            event.stopPropagation();
            closeModal();
        }
    });

    [accessPublicInput, accessAuthenticatedInput, accessInviteInput].forEach((input) => {
        input?.addEventListener('change', updateAccessModeFields);
    });

    inviteSearchInput?.addEventListener('input', () => {
        filterInviteUsers();
        if (state.selectedUserIds.length) hideInviteError();
    });

    // Toggle password content
    passwordToggle?.addEventListener('change', () => {
        if (!passwordContent) return;
        passwordContent.hidden = !passwordToggle.checked;
        if (!passwordToggle.checked) {
            hidePasswordError();
        }
        if (passwordToggle.checked) {
            setTimeout(() => passwordInput?.focus(), 50);
        }
    });

    // Real-time password length validation
    passwordInput?.addEventListener('input', () => {
        if (!passwordToggle?.checked) return;
        const val = String(passwordInput.value || '').trim();
        if (val && val.length < 8) {
            showPasswordError(t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
        } else {
            hidePasswordError();
        }
    });

    expiryToggle?.addEventListener('change', () => {
        if (!expiryContent) return;
        expiryContent.hidden = !expiryToggle.checked;
        hideExpiryError();
        if (expiryToggle.checked) {
            // Default to 7 days from now if empty
            if (expiryInput && !expiryInput.value) {
                const future = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
                expiryInput.value = toLocalDateTimeValue(future.toISOString());
            }
            setTimeout(() => expiryInput?.focus(), 50);
        }
    });

    expiryInput?.addEventListener('input', hideExpiryError);
    expiryInput?.addEventListener('change', hideExpiryError);

    primaryBtn?.addEventListener('click', () => {
        if (state.mode === 'list') {
            enterCreateMode();
        } else {
            void runWithBusy(submitForm);
        }
    });

    secondaryBtn?.addEventListener('click', () => {
        if (state.mode === 'list' || !state.status?.share_id) {
            closeModal();
        } else {
            enterListMode();
        }
    });

    document.addEventListener('chatSetupReady', () => { void syncHeaderShareVisibility(); });
    if (typeof MutationObserver !== 'undefined') {
        const chatContextObserver = new MutationObserver(() => {
            void syncHeaderShareVisibility();
        });
        chatContextObserver.observe(chatContainer, {
            attributes: true,
            attributeFilter: ['data-chat-id', 'data-pending-chat', 'data-temp-chat'],
        });
    }
    void syncHeaderShareVisibility();

    if (typeof window !== 'undefined') {
        window.ChatShareModal = {
            open: openModal,
            openForChat: openModalForChat,
            close: closeModal,
            syncHeaderShareVisibility,
        };
    }
})();
